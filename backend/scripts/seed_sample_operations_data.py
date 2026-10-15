"""
Seeds realistic SAMPLE OPERATIONAL DATA -- historical sales, production
runs, and current inventory levels -- on top of the reference/catalog
data `seed_demo_data.py` already provides.

Why this script exists: `seed_demo_data.py` only creates reference rows
(branches, roles, the product catalog, materials, add-ons) -- it has no
orders, no production history, no stock levels. With nothing but that,
every dashboard, report, and the forecasting/AI-insight features show
either all-zero or "Insufficient Data", which makes it impossible to
actually evaluate whether those features work the way the manuscript's
research objectives describe. This script fills that gap with ~90 days
of plausible day-by-day history, run through the REAL service-layer
functions (`pos.services.complete_sale_payment`,
`production.services.record_production`) so every downstream side
effect -- inventory deduction, InventoryTransaction logging, material
consumption -- happens exactly the way a real sale or production run
would trigger it. Timestamps are backdated afterward via a plain
queryset `.update()` (which bypasses `auto_now_add`/`auto_now`, unlike
`.save()`), not invented at the row level, so the data a real session
run would produce and the data this script produces are structurally
identical -- only the dates differ.

Deliberately scoped to the two native-POS branches (Batangas City,
Lipa City) for sales history -- Alangilan is modeled as KaHero-
batch-import (Phase 2 decision), so simulating live POS sales there
would misrepresent how that branch actually operates.

Production and sales are simulated in ONE combined day-by-day loop
(`seed_operational_history`), not two independent passes, because
there is no automated commissary-to-branch transfer feature in the
app (confirmed: `InventoryTransaction.MovementType` has no transfer
type, and `record_production()` only ever credits the commissary's
own inventory -- branch replenishment is a manual, off-system step in
the real product, reconciled through `inventory.services.adjust_stock`
/ "Manual Stock Adjustment"). Running sales and production as two
separate passes (production second, after sales already drew branch
stock all the way down) produced wildly negative branch inventory
for best-sellers in an early version of this script -- there was
nothing simulating a delivery arriving. The combined loop instead
produces, then periodically delivers from the commissary to each
branch (capped by what the commissary actually has on hand, logged as
a paired MANUAL_ADJUSTMENT on each side -- the same function and
movement type a real manual restock would use), then sells -- so a
branch's stock can still legitimately run low or hit zero (that's
the point of the risk-classification demo), but doesn't free-fall
hundreds of units negative with nothing to explain it.

Idempotency: safe to run once. Re-running SKIPS historical generation
entirely (and says so) if the two native branches already have more
than SKIP_THRESHOLD completed transactions *within the forecasting
lookback window itself* (the trailing DAYS_OF_HISTORY days from today)
-- deliberately NOT an all-time count, because a database can easily
already contain old, unrelated historical-import data (e.g. a prior
one-off batch load from 2024) that is real and shouldn't be touched,
but is also completely outside the window
`apps.forecasting.ml.data_prep.build_daily_sales_series` actually
looks at. An all-time count would see that old data, wrongly conclude
"already seeded," and skip -- leaving the forecasting window empty.
Checking recency instead means this script still does its job (populate
the window the forecasting/reporting features actually read) regardless
of what unrelated older data already exists. This is a guard against
duplicating 90 days of *recent* history on every re-run, not a true
idempotent diff. Reference-data seeding (users, reorder thresholds,
starting inventory) still safely uses get_or_create/update_or_create
and is fine to re-run on its own.

Usage (from the backend/ directory, with the venv active):
    python manage.py shell < scripts/seed_sample_operations_data.py

Or:
    python manage.py shell
    >>> from scripts.seed_sample_operations_data import run
    >>> run()

Run `scripts/seed_demo_data.py` FIRST if this is a brand new database --
this script assumes the branches/roles/product catalog already exist
and will raise a clear error if they don't.
"""

import os
import random
import sys
from datetime import timedelta
from decimal import Decimal

import django

if not django.apps.apps.ready:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    django.setup()

from django.utils import timezone  # noqa: E402

from apps.accounts.models import Branch, CashierPIN, Role, User  # noqa: E402
from apps.inventory import services as inventory_services  # noqa: E402
from apps.inventory.models import Inventory, InventoryTransaction, Product  # noqa: E402
from apps.pos import services as pos_services  # noqa: E402
from apps.pos.models import SalesItem, SalesTransaction  # noqa: E402
from apps.production import services as production_services  # noqa: E402
from apps.production.models import ProductionRecord  # noqa: E402

# Fixed seed -- the generated history is reproducible run to run (useful
# when comparing "before/after" screenshots for the manuscript), not a
# different random dataset every time.
random.seed(20261015)

DAYS_OF_HISTORY = 90
SKIP_THRESHOLD = 200  # see idempotency note above

# Demo credentials -- printed at the end, not hidden. Change/rotate
# these before a real public demo if that ever matters; for an
# adviser evaluation they just need to be known and consistent.
CASHIER_PASSWORD = "Jorge2026!"
COMMISSARY_PASSWORD = "Jorge2026!"

# Reorder thresholds -- seed_demo_data.py never sets these (defaults to
# 0, meaning "never flag low stock"), so without this, the Resource
# Management / risk-classification features have nothing to ever flag,
# no matter how low stock actually runs. Only applied to rows that are
# still at the default 0, so a threshold set by hand later is never
# silently overwritten by re-running this script.
FINISHED_GOOD_THRESHOLD = 30
MATERIAL_THRESHOLD = 80

# Weekly commissary -> branch delivery simulation (see module docstring
# for why this exists). A branch's stock for a product below TRIGGER
# gets topped back up toward TARGET, capped by what the commissary
# actually has on hand that week -- never fabricated from nothing.
REPLENISH_TRIGGER = 120
REPLENISH_TARGET = 350
REPLENISH_WEEKDAY = 0  # Monday


def _native_branches():
    return list(Branch.objects.filter(is_kahero_branch=False, is_commissary=False))


def _days_ago(days):
    """Timezone-aware midnight N days before today -- used to scope the
    idempotency checks to the forecasting-relevant recent window rather
    than all-time history (see module docstring)."""
    d = timezone.localdate() - timedelta(days=days)
    return timezone.make_aware(timezone.datetime(d.year, d.month, d.day))


def ensure_demo_users():
    """One cashier per native branch, plus a commissary staff account --
    get_or_create, so re-running never duplicates or reassigns anyone."""
    print("Ensuring demo staff accounts exist...")
    branch_role = Role.objects.get(name=Role.BRANCH_STAFF)
    commissary_role = Role.objects.get(name=Role.COMMISSARY_STAFF)
    commissary_branch = production_services.get_commissary_branch()
    if commissary_branch is None:
        raise RuntimeError("No commissary branch found -- run scripts/seed_demo_data.py first.")

    cashiers = {}
    for branch in _native_branches():
        username = f"cashier_{branch.code.lower()}"
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "first_name": f"{branch.name} Cashier",
                "role": branch_role,
                "branch": branch,
                "is_active": True,
            },
        )
        if created:
            user.set_password(CASHIER_PASSWORD)
            user.save()
        pin, _ = CashierPIN.objects.get_or_create(user=user)
        pin.set_pin("1234")
        pin.is_active = True
        pin.failed_attempts = 0
        pin.locked_until = None
        pin.save()
        status = "created" if created else "already exists"
        print(f"  [{status}] {username} (PIN 1234, branch={branch.name})")
        cashiers[branch.pk] = user

    commissary_user, created = User.objects.get_or_create(
        username="commissary_staff",
        defaults={
            "first_name": "Commissary Staff",
            "role": commissary_role,
            "branch": commissary_branch,
            "is_active": True,
        },
    )
    if created:
        commissary_user.set_password(COMMISSARY_PASSWORD)
        commissary_user.save()
    status = "created" if created else "already exists"
    print(f"  [{status}] commissary_staff (password {COMMISSARY_PASSWORD})")

    return cashiers, commissary_user, commissary_branch


def ensure_reorder_thresholds():
    print("\nSetting reorder thresholds where unset...")
    updated_fg = Product.objects.filter(
        product_type=Product.ProductType.FINISHED_GOOD, reorder_threshold=0
    ).update(reorder_threshold=FINISHED_GOOD_THRESHOLD)
    updated_mat = Product.objects.filter(
        product_type=Product.ProductType.MATERIAL, reorder_threshold=0
    ).update(reorder_threshold=MATERIAL_THRESHOLD)
    print(f"  Finished goods set to threshold {FINISHED_GOOD_THRESHOLD}: {updated_fg} products")
    print(f"  Materials set to threshold {MATERIAL_THRESHOLD}: {updated_mat} products")


def ensure_starting_inventory(cashiers, commissary_branch):
    """Generous starting stock so 90 days of simulated sales/production
    draw it down to a believable (not permanently-negative) level,
    rather than starting from zero. The final snapshot pass at the end
    of run() deliberately pushes a few specific items below threshold
    for a realistic mixed adequate/low/critical picture."""
    print("\nSeeding starting inventory levels (only where none exists yet)...")
    finished_goods = Product.objects.filter(
        product_type=Product.ProductType.FINISHED_GOOD, is_active=True
    )
    materials = Product.objects.filter(product_type=Product.ProductType.MATERIAL, is_active=True)

    created_count = 0
    for branch in _native_branches():
        for product in finished_goods:
            _, created = Inventory.objects.get_or_create(
                branch=branch, product=product, defaults={"quantity_on_hand": 400}
            )
            created_count += int(created)

    for product in finished_goods:
        _, created = Inventory.objects.get_or_create(
            branch=commissary_branch, product=product, defaults={"quantity_on_hand": 200}
        )
        created_count += int(created)
    for product in materials:
        _, created = Inventory.objects.get_or_create(
            branch=commissary_branch, product=product, defaults={"quantity_on_hand": 5000}
        )
        created_count += int(created)
    print(f"  Created {created_count} new inventory rows (existing rows left untouched).")


def _simulate_day_of_sales(day_date, branch, cashier, catalog, best_sellers):
    """One day's worth of completed sales transactions for one branch,
    with a Fri/Sat/Sun volume bump and best-sellers weighted to sell
    more often -- not a uniform random draw, so the resulting trend/
    seasonality is actually visible to Holt-Winters and to a human
    looking at the Sales Analytics chart."""
    is_weekend = day_date.weekday() >= 4  # Fri=4, Sat=5, Sun=6
    order_count = random.randint(14, 24) if is_weekend else random.randint(7, 14)

    for _ in range(order_count):
        draft = pos_services.get_or_create_draft_transaction(cashier, branch)
        item_count = random.randint(1, 4)
        chosen = random.choices(
            population=catalog,
            weights=[3 if p in best_sellers else 1 for p in catalog],
            k=item_count,
        )
        for product in chosen:
            pos_services.add_catalog_item(draft, product.pk, quantity=random.randint(1, 3))

        if not draft.items.exists():
            continue

        pos_services.set_dining_option(
            draft,
            random.choice(
                [SalesTransaction.DiningOption.DINE_IN, SalesTransaction.DiningOption.TAKE_OUT]
            ),
        )

        # ~15% of orders carry a transaction-wide discount -- enough to
        # exercise the revenue-minus-discount dashboard math without
        # making every single order discounted (unrealistic).
        if random.random() < 0.15:
            pos_services.apply_transaction_discount(draft, "SC2", "PERCENT", Decimal("20.00"))

        total = draft.grand_total
        tendered = (total + Decimal(random.choice([0, 20, 50, 100]))).quantize(Decimal("1"))
        try:
            completed = pos_services.complete_sale_payment(
                draft,
                payment_method=random.choice(
                    [
                        SalesTransaction.PaymentMethod.CASH,
                        SalesTransaction.PaymentMethod.CASH,
                        SalesTransaction.PaymentMethod.GCASH,
                        SalesTransaction.PaymentMethod.CARD,
                    ]
                ),
                amount_tendered=tendered,
            )
        except pos_services.PaymentError:
            continue

        # Backdate -- completed_at/created_at are set to "now" by the
        # service above; this queryset .update() (not .save()) rewrites
        # them to the simulated historical moment without re-triggering
        # auto_now/auto_now_add or any signal.
        hour = random.randint(7, 19)
        minute = random.randint(0, 59)
        ts = timezone.make_aware(
            timezone.datetime(day_date.year, day_date.month, day_date.day, hour, minute)
        )
        SalesTransaction.objects.filter(pk=completed.pk).update(created_at=ts, completed_at=ts)
        SalesItem.objects.filter(transaction_id=completed.pk).update(created_at=ts, updated_at=ts)


def _run_production_for_day(day_date, commissary_user, finished_goods, materials):
    """One day's production runs at the commissary. Returns the count of
    records actually created (some may be skipped for lack of material
    stock, especially in the first couple of simulated weeks before
    production has built up a buffer)."""
    created = 0
    for product in random.sample(finished_goods, k=min(len(finished_goods), random.randint(2, 5))):
        quantity = random.randint(10, 60)
        ingredient_rows = [
            {"material_id": m.pk, "quantity_used": random.randint(1, 10)}
            for m in random.sample(materials, k=min(3, len(materials)))
        ]
        status = random.choices(
            [ProductionRecord.Status.COMPLETED, ProductionRecord.Status.IN_PROGRESS],
            weights=[9, 1],
        )[0]
        quality = random.choices(
            [ProductionRecord.Quality.PASS, ProductionRecord.Quality.FAIL], weights=[19, 1]
        )[0]
        try:
            record = production_services.record_production(
                commissary_staff=commissary_user,
                product_id=product.pk,
                quantity_produced=quantity,
                ingredient_rows=ingredient_rows,
                status=status,
                quality=quality,
            )
        except production_services.ProductionError:
            continue  # insufficient material stock that day -- skip, don't fabricate

        hour = random.randint(6, 14)
        ts = timezone.make_aware(
            timezone.datetime(day_date.year, day_date.month, day_date.day, hour, 0)
        )
        ProductionRecord.objects.filter(pk=record.pk).update(created_at=ts, updated_at=ts)
        created += 1
    return created


def _run_weekly_replenishment(day_date, native_branches, commissary_branch, finished_goods):
    """Simulates a Monday delivery run: tops up any branch/product below
    REPLENISH_TRIGGER toward REPLENISH_TARGET, capped by what the
    commissary actually has on hand -- the same adjust_stock() function
    and MANUAL_ADJUSTMENT movement type a real manual restock would use,
    applied as a paired debit/credit so nothing is created from nothing."""
    ts = timezone.make_aware(timezone.datetime(day_date.year, day_date.month, day_date.day, 6, 0))
    for branch in native_branches:
        for product in finished_goods:
            branch_inv, _ = Inventory.objects.get_or_create(branch=branch, product=product)
            if branch_inv.quantity_on_hand >= REPLENISH_TRIGGER:
                continue

            commissary_inv, _ = Inventory.objects.get_or_create(
                branch=commissary_branch, product=product
            )
            wanted = REPLENISH_TARGET - branch_inv.quantity_on_hand
            delivery = max(0, min(wanted, commissary_inv.quantity_on_hand))
            if delivery <= 0:
                continue  # commissary has nothing spare to send this week

            inventory_services.adjust_stock(
                commissary_inv, -delivery, reason=f"Delivery to {branch.name} (sample data)"
            )
            # Captured by primary key immediately after each call (the
            # script is single-threaded, so this is always the row that
            # call just created) rather than by a created-date filter,
            # which could also match unrelated same-day rows for the
            # same branch/product from an earlier run today.
            InventoryTransaction.objects.filter(
                pk=InventoryTransaction.objects.latest("pk").pk
            ).update(created_at=ts)

            inventory_services.adjust_stock(
                branch_inv, delivery, reason="Delivery from Commissary (sample data)"
            )
            InventoryTransaction.objects.filter(
                pk=InventoryTransaction.objects.latest("pk").pk
            ).update(created_at=ts)


def seed_operational_history(cashiers, commissary_user, commissary_branch):
    """The core ~90-day simulation: production, periodic branch
    replenishment, then sales, one day at a time, in that order, so
    each day's deliveries can only draw on stock production has
    actually built up by then (see module docstring)."""
    print(f"\nSeeding {DAYS_OF_HISTORY} days of production + replenishment + sales history...")
    native_branches = _native_branches()

    # One recency-scoped guard covers the whole coupled process -- see
    # the module docstring's Idempotency section for why this checks the
    # trailing window, not an all-time count.
    existing = SalesTransaction.objects.filter(
        branch__in=native_branches,
        status=SalesTransaction.Status.COMPLETED,
        completed_at__gte=_days_ago(DAYS_OF_HISTORY),
    ).count()
    if existing > SKIP_THRESHOLD:
        print(
            f"  Skipping -- {existing} completed transactions already exist "
            f"within the last {DAYS_OF_HISTORY} days (> {SKIP_THRESHOLD}). "
            "Re-run would duplicate recent history, not refresh it."
        )
        return

    finished_goods = list(
        Product.objects.filter(product_type=Product.ProductType.FINISHED_GOOD, is_active=True)
    )
    materials = list(
        Product.objects.filter(product_type=Product.ProductType.MATERIAL, is_active=True)
    )
    best_sellers = {p for p in finished_goods if p.is_best_seller}
    today = timezone.localdate()
    production_count = 0

    for offset in range(DAYS_OF_HISTORY, 0, -1):
        day_date = today - timedelta(days=offset)

        production_count += _run_production_for_day(
            day_date, commissary_user, finished_goods, materials
        )

        if day_date.weekday() == REPLENISH_WEEKDAY:
            _run_weekly_replenishment(day_date, native_branches, commissary_branch, finished_goods)

        for branch in native_branches:
            _simulate_day_of_sales(
                day_date, branch, cashiers[branch.pk], finished_goods, best_sellers
            )

        if offset % 15 == 0:
            print(f"  ...{DAYS_OF_HISTORY - offset}/{DAYS_OF_HISTORY} days done")

    print(f"  History seeding complete ({production_count} production records).")


def apply_demo_stock_snapshot(commissary_branch):
    """Deliberately pushes a handful of specific items below their
    reorder threshold so Resource Management / risk classification has
    a real mixed adequate/low/critical picture to show, rather than
    leaving that to chance after 90 days of simulated draws."""
    print("\nApplying a realistic low/critical stock snapshot for the demo...")
    critical_materials = Product.objects.filter(
        product_type=Product.ProductType.MATERIAL, is_active=True
    ).order_by("name")[:2]
    for material in critical_materials:
        Inventory.objects.filter(branch=commissary_branch, product=material).update(
            quantity_on_hand=max(0, material.reorder_threshold // 3)
        )
        print(f"  {material.name} @ Commissary -> critical/low stock")

    low_finished_goods = Product.objects.filter(
        product_type=Product.ProductType.FINISHED_GOOD, is_active=True
    ).order_by("name")[:2]
    for branch in _native_branches()[:1]:
        for product in low_finished_goods:
            Inventory.objects.filter(branch=branch, product=product).update(
                quantity_on_hand=max(0, product.reorder_threshold // 2)
            )
            print(f"  {product.name} @ {branch.name} -> low stock")


def generate_forecasts_and_insights():
    print("\nRunning forecasting, risk classification, and AI insight generation...")
    from apps.forecasting import services as forecasting_services

    # run_forecast_for_all_products() returns a result dict
    # ({"succeeded": int, "failed": [...]}), NOT a list of forecasts --
    # len() on it would silently report 2 (its key count) no matter how
    # many pairs actually succeeded, so its fields are read explicitly.
    forecast_results = forecasting_services.run_forecast_for_all_products()
    succeeded = forecast_results.get("succeeded", 0)
    failed = forecast_results.get("failed", [])
    print(f"  Forecasts generated for {succeeded} branch/product pairs.")
    if failed:
        print(f"  {len(failed)} pairs could not be forecast (commonly: insufficient history):")
        for f in failed[:5]:
            print(f"    branch={f['branch_id']} product={f['product_id']}: {f['error']}")
        if len(failed) > 5:
            print(f"    ...and {len(failed) - 5} more.")

    risk_scores = forecasting_services.run_risk_classification_for_all_inventory()
    print(f"  Risk scores generated: {len(risk_scores) if risk_scores else 0}.")
    try:
        insights = forecasting_services.generate_insights_for_all_branches()
        print(f"  AI insights generated: {len(insights) if insights else 0}.")
    except Exception as exc:  # noqa: BLE001 -- Gemini API may be unavailable/unconfigured
        print(f"  AI insight generation skipped ({exc.__class__.__name__}: {exc}).")


def run():
    print("=" * 70)
    print("Seeding sample operational data for evaluation")
    print("=" * 70)

    if not Branch.objects.filter(is_commissary=True).exists():
        raise RuntimeError(
            "No branches found -- run `scripts/seed_demo_data.py` first to "
            "create branches, roles, and the product catalog."
        )

    cashiers, commissary_user, commissary_branch = ensure_demo_users()
    ensure_reorder_thresholds()
    ensure_starting_inventory(cashiers, commissary_branch)
    seed_operational_history(cashiers, commissary_user, commissary_branch)
    apply_demo_stock_snapshot(commissary_branch)
    generate_forecasts_and_insights()

    print("\n" + "=" * 70)
    print("Done. Demo accounts (unchanged if they already existed):")
    for branch in _native_branches():
        print(f"  Cashier @ {branch.name}: username=cashier_{branch.code.lower()}  PIN=1234")
    print(f"  Commissary Staff: username=commissary_staff  password={COMMISSARY_PASSWORD}")
    print("Log in as Owner/Admin to view Dashboard Home, Reports, and Forecasting now populated.")
    print("=" * 70)


if __name__ == "__main__":
    run()
