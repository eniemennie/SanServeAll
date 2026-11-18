"""
Generates demo production/material-consumption/per-product-sales history
for 2020-01-01 through 2026-05-01 -- the gap before real batch/production
tracking began in this system (2026-05-04) -- so the Reports Hub's
Product Performance, Resource Consumption, and Operational Performance
tabs aren't empty for a pre-tracking date range.

Client review (2026-11-18), after several rounds of negotiation on scope
(see conversation history -- multiple earlier designs, including a
"Demo Mode" toggle and a separate "Demo Data" tab, were proposed and then
explicitly rejected). FINAL, locked-in design: real, permanent database
rows, visible directly on those three Reports Hub tabs by default for
old date ranges -- no separate tab, no toggle, no banner -- but each
carrying a small, quiet inline "Demo" label (see apps.analytics.services'
_exclude_demo_data_transactions and templates/analytics/reports_hub.html).

WHAT THIS CREATES, all attributed to one dedicated identity
(apps.accounts.models.User.DEMO_DATA_GENERATOR_USERNAME):
  - ProductionRecord + IngredientUsage rows -- one weekly batch per
    eligible finished-good product, materials scaled to that product's
    own real recipe ratios.
  - SalesTransaction + SalesItem rows -- one weekly sale per (eligible
    product, active branch) pair, scaled to that pair's own real recent
    weekly average.

WHAT THIS NEVER TOUCHES:
  - Inventory / InventoryTransaction. Same reasoning
    import_production_and_ingredient_usage.py and
    import_branch_sales_history.py already use for their own historical
    backfills (see those commands' own docstrings): these rows represent
    a period before real stock tracking existed, so there is no honest
    "historical inventory balance" to deduct from or credit to. Going
    through apps.production.services.record_production or
    apps.pos.services would alter TODAY's real stock levels as a side
    effect of writing history -- a far bigger and unrelated change than
    "generate some report-filler rows." ProductionRecord and
    SalesTransaction/SalesItem rows are created directly instead, same
    as those two commands.
  - Sales & Trend, Sales History, the Dashboard, or any revenue/units
    total -- see apps.analytics.services._exclude_demo_data_transactions,
    applied everywhere except get_product_performance,
    get_resource_consumption_summary, and
    get_operational_performance_summary, which deliberately include this
    script's rows.
  - Batch Logs (apps.production.views.batch_management) or the
    Dashboard's Production Output KPI (apps.production.services.
    get_total_output) -- both explicitly exclude this account (client
    review, 2026-11-18): Batch Logs has no date filter and computes its
    own stat cards from every ProductionRecord by default, unlike the
    three Reports Hub tabs above.

ELIGIBILITY AND VOLUME -- derived from REAL data at run time, never
hardcoded, so this keeps working correctly if the real catalog or the
tracking-start date ever changes:
  - "Real tracking start" is NOT hardcoded as 2026-05-04 -- it's the
    earliest real (non-demo) ProductionRecord.created_at actually on
    file, computed fresh every run.
  - Eligible (for production + materials) products: active FINISHED_GOOD
    products with at least one real (non-bot, non-demo) COMPLETED
    ProductionRecord already on file. A product this system has never
    actually seen produced has no real recipe or volume to scale from --
    generating one for it would be inventing a number with nothing real
    behind it, so it's skipped (reported at the end).
  - Weekly production quantity per eligible product = that product's
    real total quantity_produced (COMPLETED, non-demo) divided by the
    number of full weeks since real tracking start.
  - Material ratios per eligible product = that product's real total
    quantity_used per material (non-demo) divided by its real total
    quantity_produced -- i.e. this product's own real recipe, scaled to
    whatever quantity a given demo batch produces.
  - Weekly sold quantity per (product, branch) pair = that pair's real
    total COMPLETED units sold (excluding the forecast bot, this script's
    own demo rows, and the historical-sales import's product=None rows)
    divided by the same real-tracking-start week count. This is computed
    independently of the production-eligibility list above: a product
    can have real sales history without a matching real production
    record (and still gets demo Product Performance data), since that
    tab is sales-driven, not production-driven.
  - A fractional weekly average (e.g. 0.3 units/week) is handled as a
    probability, not rounded up to "every week" or down to "never" (see
    _probabilistic_quantity) -- the generated series has roughly the
    same sparsity as the real one it's scaled from.

HONEST LIMITATIONS, stated here rather than only in conversation:
  - This is a stylized estimate scaled from each product's own real,
    recent weekly average -- not a simulation of this business's actual
    2020-2026 history, and not reconciled against the real daily
    aggregate revenue apps.core.import_branch_sales_history already
    imported for 2024-2026 from the client's own pre-system workbooks.
    That import's rows have no product attached at all (one aggregate
    revenue figure per branch per day -- see that command's own
    docstring), so there is nothing concrete for this script's
    per-product rows to reconcile against; the two simply coexist as
    separate, differently-sourced figures over the same stretch of
    calendar time.
  - Every generated batch is Status.COMPLETED; Quality is sampled
    against each product's own real PASS rate (falling back to 95%
    PASS for a product with no real PASS/FAIL variation to sample from)
    rather than defaulting to 100% PASS outright.
  - Flat weekly cadence across the whole 2020-2026-05 range -- no
    attempt to model seasonal demand, year-over-year growth, or
    holidays.

Removable: writes demo_history_manifest.json (pk floors + the generated
row counts), so scripts/remove_demo_history.py can undo precisely this
run later -- independent of every other script's own manifest in this
directory.

Usage (from the backend/ directory, with the venv active):
    Dry run FIRST (default -- prints exactly what would be created,
    writes nothing):
        python manage.py shell --settings=config.settings.production \\
            -c "from scripts.generate_demo_history import run; run()"

    Only after reviewing that output, the real run:
        python manage.py shell --settings=config.settings.production \\
            -c "from scripts.generate_demo_history import run; run(dry_run=False)"
"""

import json
import os
import random
import sys
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import django

if not django.apps.apps.ready:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    django.setup()

from django.db import transaction  # noqa: E402
from django.db.models import Max, Sum  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.accounts.models import Branch, Role, User  # noqa: E402
from apps.inventory.models import Product  # noqa: E402
from apps.pos.models import SalesItem, SalesTransaction  # noqa: E402
from apps.production.models import IngredientUsage, ProductionRecord  # noqa: E402

MANIFEST_PATH = Path(__file__).resolve().parent / "demo_history_manifest.json"

DEMO_USERNAME = User.DEMO_DATA_GENERATOR_USERNAME

DEMO_RANGE_START = date(2020, 1, 1)
DEMO_RANGE_END = date(2026, 5, 1)

# Fixed seed -- reproducible which weeks/quantities get chosen, same
# convention scripts/backfill_thin_forecast_pairs.py already uses.
random.seed(20261118)


def _max_pk(model):
    return model.objects.aggregate(m=Max("pk"))["m"] or 0


def ensure_demo_user():
    """A single dedicated identity every row this script creates is
    attributed to -- not scoped to one branch (branch=None, same
    convention User.branch already documents for OWNER_ADMIN), since
    demo sales get attributed across whichever branches have real sales
    history. Never logs in -- unusable password."""
    role, _ = Role.objects.get_or_create(name=Role.OWNER_ADMIN)
    user, created = User.objects.get_or_create(
        username=DEMO_USERNAME,
        defaults={
            "first_name": "Demo Data Generator (not a real staff account -- see "
            "scripts/generate_demo_history.py)",
            "role": role,
            "branch": None,
            "is_active": True,
        },
    )
    if created:
        user.set_unusable_password()
        user.save()
        print(f"  Created dedicated demo-data identity: {DEMO_USERNAME}")
    else:
        print(f"  Using existing demo-data identity: {DEMO_USERNAME}")
    return user


def _real_tracking_start():
    """Earliest real (non-demo) ProductionRecord.created_at on file --
    deliberately NOT hardcoded as 2026-05-04, so this script stays
    correct if that assumption ever changes (see module docstring)."""
    earliest = (
        ProductionRecord.objects.exclude(commissary_staff__username=DEMO_USERNAME)
        .order_by("created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    return timezone.localtime(earliest).date() if earliest else None


def find_eligible_products(weeks_tracked):
    """Active FINISHED_GOOD products with at least one real (non-bot,
    non-demo) COMPLETED ProductionRecord -- each one's real weekly
    production average, real pass rate, and real per-material recipe
    ratios, all computed from that same real history (see module
    docstring)."""
    real_records = ProductionRecord.objects.filter(
        status=ProductionRecord.Status.COMPLETED
    ).exclude(commissary_staff__username=DEMO_USERNAME)

    eligible = []
    # Collected in Python, not via a DB-level .distinct(): ProductionRecord's
    # Meta.ordering = ["-created_at"] makes Django fold created_at into the
    # implicit ORDER BY on a .values_list(...).distinct() query, so SQL
    # DISTINCT silently stops deduping by product_id alone (a well-known
    # Django gotcha) -- caught via this script's own smoke test, which
    # otherwise listed the same product once per real ProductionRecord.
    product_ids = set(real_records.values_list("product_id", flat=True))
    for product_id in product_ids:
        product = Product.objects.filter(pk=product_id, is_active=True).first()
        if product is None:
            continue

        product_records = real_records.filter(product=product)
        total_produced = product_records.aggregate(t=Sum("quantity_produced"))["t"] or 0
        if total_produced <= 0:
            continue

        pass_count = product_records.filter(quality=ProductionRecord.Quality.PASS).count()
        total_count = product_records.count()
        pass_rate = (pass_count / total_count) if total_count else 0.95

        usage_rows = (
            IngredientUsage.objects.filter(production_record__in=product_records)
            .values("material_id")
            .annotate(total_used=Sum("quantity_used"))
        )
        material_ratios = {
            row["material_id"]: row["total_used"] / Decimal(total_produced) for row in usage_rows
        }

        eligible.append(
            {
                "product": product,
                "weekly_avg_produced": Decimal(total_produced) / Decimal(weeks_tracked),
                "pass_rate": pass_rate,
                "material_ratios": material_ratios,
            }
        )
    return eligible


def find_real_weekly_sales_by_branch(weeks_tracked):
    """Real (non-bot, non-demo) COMPLETED SalesItem rows with a real
    product attached (excludes the historical-sales import's own
    product=None rows -- see get_product_performance's own docstring for
    why those were never a real product to report on), grouped by
    (product, branch), each pair's real recent weekly average units
    sold."""
    items = SalesItem.objects.filter(
        product__isnull=False, transaction__status=SalesTransaction.Status.COMPLETED
    ).exclude(
        transaction__cashier__username__in=[
            User.SYNTHETIC_BACKFILL_BOT_USERNAME,
            DEMO_USERNAME,
        ]
    )
    grouped = items.values("product_id", "transaction__branch_id").annotate(
        total_units=Sum("quantity")
    )
    return {
        (row["product_id"], row["transaction__branch_id"]): (
            Decimal(row["total_units"]) / Decimal(weeks_tracked)
        )
        for row in grouped
    }


def _probabilistic_quantity(weekly_avg):
    """A fractional weekly average (e.g. 0.3 units/week) is handled as a
    probability of getting exactly one occurrence that week, not rounded
    up to "every week" or down to "never" -- see module docstring. An
    average of 1 or more is jittered +/-40% around itself instead,
    rounded, minimum 1."""
    if weekly_avg <= 0:
        return 0
    if weekly_avg < 1:
        return 1 if random.random() < float(weekly_avg) else 0
    jittered = float(weekly_avg) * random.uniform(0.6, 1.4)
    return max(1, round(jittered))


def _create_demo_production(demo_user, product, quantity, pass_rate, material_ratios, when):
    quality = (
        ProductionRecord.Quality.PASS
        if random.random() < pass_rate
        else ProductionRecord.Quality.FAIL
    )
    record = ProductionRecord.objects.create(
        commissary_staff=demo_user,
        product=product,
        quantity_produced=quantity,
        quality=quality,
        status=ProductionRecord.Status.COMPLETED,
        notes="Demo data -- generated to fill history before real production tracking "
        "began (see scripts/generate_demo_history.py). Not a real batch.",
    )
    # created_at is auto_now_add -- backdated after the fact so the
    # record sorts/reports under its generated historical date, same
    # pattern as every other historical-backfill script in this repo.
    ProductionRecord.objects.filter(pk=record.pk).update(created_at=when)

    for material_id, ratio in material_ratios.items():
        quantity_used = (ratio * quantity).quantize(Decimal("0.001"))
        if quantity_used <= 0:
            continue
        IngredientUsage.objects.create(
            production_record=record, material_id=material_id, quantity_used=quantity_used
        )
    return record


def _create_demo_sale(demo_user, branch, product, quantity, when):
    txn = SalesTransaction.objects.create(
        branch=branch,
        cashier=demo_user,
        status=SalesTransaction.Status.COMPLETED,
        dining_option=SalesTransaction.DiningOption.TAKE_OUT,
        payment_method=SalesTransaction.PaymentMethod.CASH,
    )
    SalesItem.objects.create(
        transaction=txn, product=product, unit_price=product.price, quantity=quantity
    )
    txn.refresh_from_db()
    txn.amount_tendered = txn.grand_total
    txn.save(update_fields=["amount_tendered"])
    # created_at/completed_at are auto_now_add / set at completion time
    # respectively -- both backdated after the fact, same pattern as
    # every other historical-backfill script in this repo.
    SalesTransaction.objects.filter(pk=txn.pk).update(created_at=when, completed_at=when)
    return txn


def _weekly_anchors(start_date, end_date):
    """Evenly spaced weekly anchor dates from start_date through
    end_date inclusive -- a flat cadence (see module docstring's "Honest
    limitations"), not aligned to any particular day of the week."""
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=7)


def _week_timestamp(week_start):
    hour = random.randint(8, 17)
    minute = random.randint(0, 59)
    return timezone.make_aware(datetime.combine(week_start, time(hour, minute)))


def generate(eligible, sales_by_pair, weeks, demo_user, dry_run):
    """Shared by both the dry-run preview and the live path (same
    convention as backfill_thin_forecast_pairs.py's own backfill()), so
    a dry run previews exactly the counts the live run will actually
    create."""
    production_created = 0
    sales_created = 0
    production_pks = []
    sales_pks = []

    for week_start in weeks:
        when = _week_timestamp(week_start)

        for entry in eligible:
            quantity = _probabilistic_quantity(entry["weekly_avg_produced"])
            if quantity <= 0:
                continue
            if dry_run:
                production_created += 1
                continue
            record = _create_demo_production(
                demo_user,
                entry["product"],
                quantity,
                entry["pass_rate"],
                entry["material_ratios"],
                when,
            )
            production_created += 1
            production_pks.append(record.pk)

        for (product_id, branch_id), weekly_avg in sales_by_pair.items():
            quantity = _probabilistic_quantity(weekly_avg)
            if quantity <= 0:
                continue
            if dry_run:
                sales_created += 1
                continue
            txn = _create_demo_sale(
                demo_user,
                Branch.objects.get(pk=branch_id),
                Product.objects.get(pk=product_id),
                quantity,
                when,
            )
            sales_created += 1
            sales_pks.append(txn.pk)

    return production_created, sales_created, production_pks, sales_pks


def _write_manifest(pk_floor, production_pks, sales_pks):
    manifest = {
        "generated_at": timezone.now().isoformat(),
        "demo_username": DEMO_USERNAME,
        "pk_floor": pk_floor,
        "production_record_pks": production_pks,
        "sales_transaction_pks": sales_pks,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    print(f"\nWrote removal manifest to {MANIFEST_PATH}.")
    print("Run scripts/remove_demo_history.py to undo precisely this run later.")


def run(dry_run=True):
    print("=" * 70)
    mode = "DRY RUN -- no writes will be made" if dry_run else "LIVE RUN"
    print(f"Generating demo history ({DEMO_RANGE_START} to {DEMO_RANGE_END}) ({mode})")
    print("=" * 70)

    today = timezone.localdate()
    real_start = _real_tracking_start()
    if real_start is None:
        print("\nNo real (non-demo) ProductionRecord exists yet -- nothing to scale demo")
        print("volume from. Run this only after real production has some history on file.")
        return

    weeks_tracked = max(1, (today - real_start).days // 7)
    print(f"\nReal tracking start (earliest real ProductionRecord): {real_start}")
    print(f"Weeks of real history to average over: {weeks_tracked}")

    eligible = find_eligible_products(weeks_tracked)
    sales_by_pair = find_real_weekly_sales_by_branch(weeks_tracked)
    branches_with_sales = {branch_id for _, branch_id in sales_by_pair}

    if not eligible and not sales_by_pair:
        print("\nNo real production or sales history to scale from -- nothing to generate.")
        return

    print(f"\nEligible for production + materials ({len(eligible)} product(s)):")
    for e in eligible:
        print(
            f"  {e['product'].name}: ~{e['weekly_avg_produced']:.2f} unit(s)/week, "
            f"{e['pass_rate'] * 100:.0f}% real pass rate, "
            f"{len(e['material_ratios'])} material(s) in its recipe"
        )
    if not eligible:
        print("  (none -- no FINISHED_GOOD product has any real COMPLETED ProductionRecord)")

    print(
        f"\nEligible for sales ({len(sales_by_pair)} product/branch pair(s) across "
        f"{len(branches_with_sales)} branch(es)):"
    )
    if not sales_by_pair:
        print("  (none -- no real COMPLETED sale has a real product attached)")

    weeks = list(_weekly_anchors(DEMO_RANGE_START, DEMO_RANGE_END))
    print(f"\n{len(weeks)} week(s) from {DEMO_RANGE_START} to {DEMO_RANGE_END}.")

    pk_floor = {
        "production_record": _max_pk(ProductionRecord),
        "ingredient_usage": _max_pk(IngredientUsage),
        "sales_transaction": _max_pk(SalesTransaction),
        "sales_item": _max_pk(SalesItem),
    }

    if dry_run:
        production_created, sales_created, _, _ = generate(
            eligible, sales_by_pair, weeks, demo_user=None, dry_run=True
        )
        print(f"\n[DRY RUN] Would create ~{production_created} ProductionRecord row(s)")
        print(f"[DRY RUN] Would create ~{sales_created} SalesTransaction row(s)")
        print("\n" + "=" * 70)
        print("Dry run complete. No database changes were made.")
        print("Review the above, then call run(dry_run=False) to actually create these rows.")
        print("=" * 70)
        return

    real_production_before = ProductionRecord.objects.exclude(
        commissary_staff__username=DEMO_USERNAME
    ).count()
    real_sales_before = (
        SalesTransaction.objects.filter(status=SalesTransaction.Status.COMPLETED)
        .exclude(cashier__username=DEMO_USERNAME)
        .count()
    )

    demo_user = ensure_demo_user()

    print("\nGenerating demo production + sales history...")
    with transaction.atomic():
        production_created, sales_created, production_pks, sales_pks = generate(
            eligible, sales_by_pair, weeks, demo_user, dry_run=False
        )
    print(
        f"\n  Created {production_created} ProductionRecord row(s) and "
        f"{sales_created} SalesTransaction row(s)."
    )

    real_production_after = ProductionRecord.objects.exclude(
        commissary_staff__username=DEMO_USERNAME
    ).count()
    real_sales_after = (
        SalesTransaction.objects.filter(status=SalesTransaction.Status.COMPLETED)
        .exclude(cashier__username=DEMO_USERNAME)
        .count()
    )
    print("\nValidation (confirm no real data was touched):")
    print(f"  Real ProductionRecord count before: {real_production_before}")
    print(f"  Real ProductionRecord count after:  {real_production_after}")
    print(f"  Real completed SalesTransaction count before: {real_sales_before}")
    print(f"  Real completed SalesTransaction count after:  {real_sales_after}")
    if real_production_after != real_production_before or real_sales_after != real_sales_before:
        print(
            "  !! MISMATCH -- a real count changed. Stop and investigate before running "
            "anything else; do not run remove_demo_history.py blindly."
        )
    else:
        print("  OK -- unchanged, exactly as expected.")

    _write_manifest(pk_floor, production_pks, sales_pks)

    print("\n" + "=" * 70)
    print("Done. The Reports Hub's Product Performance, Resource Consumption, and")
    print("Operational Performance tabs should now show data for old date ranges,")
    print('each row that includes demo data carrying a quiet "Demo" label.')
    print("=" * 70)


if __name__ == "__main__":
    run()
