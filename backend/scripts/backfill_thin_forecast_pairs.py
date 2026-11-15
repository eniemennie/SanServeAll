"""
Targeted synthetic-data backfill for the specific (branch, product) pairs
that scripts/audit_forecast_coverage.py reports as under-served -- i.e.
pairs that already have real sales, but fewer than the 3 non-zero days
apps.forecasting.ml.data_prep.has_sufficient_history requires, so they
fall back to NAIVE_AVERAGE instead of a real Holt-Winters forecast.

This is deliberately NOT scripts/seed_sample_operations_data.py, and
does not touch that script's logic, its SKIP_THRESHOLD guard, or its
manifest. That script fills an empty database with ~90 days of broad
demo history; this one adds a handful of extra sales days to specific
pairs that already have real history, on top of a database that
already has real production data in it. Different job, different
blast radius, so it stays a separate script with its own manifest
(synthetic_backfill_manifest.json) and its own removal script
(remove_synthetic_backfill.py).

Safety properties (see the Nov 6 2026 conversation that authorized
this -- "I want to ensure that we can generate additional supplemental
data without duplicating or modifying our existing real transactions"):

1. INSERT-only against SalesTransaction/SalesItem. No existing row is
   ever updated or deleted by this script.
2. Every synthetic transaction's cashier is a single, dedicated bot
   account (BOT_USERNAME) created here -- never one of the real staff
   accounts, and never seed_sample_operations_data.py's own demo
   cashier accounts (cashier_batangas/cashier_lipa), which are reserved
   for that other script. `SalesTransaction.objects.filter(cashier__
   username=BOT_USERNAME)` alone is enough to isolate every row this
   script ever created, independent of the pk-floor bookkeeping below.
3. A synthetic sale is only ever placed on a day that pair's real daily
   sales series shows as zero -- never layered on top of, or near, an
   already-recorded real sale for that exact (branch, product) pair.
4. Quantity per synthetic sale is kept small (1-2 units) -- matching
   the scale of the handful of real sales already on record for these
   thin pairs, not an arbitrary volume that would skew the pair's
   average demand.
5. Exactly as many days as each specific pair is short, never more --
   this tops a pair up to the minimum the forecasting code itself
   requires (has_sufficient_history's own 3-non-zero-day floor), it
   does not build out a full history.
6. complete_sale_payment() unconditionally deducts real Inventory stock
   as a side effect (by design -- see that function's own comments);
   immediately after each synthetic sale, this script restores the
   exact quantity via inventory_services.adjust_stock(), so real
   Inventory.quantity_on_hand ends this script numerically unchanged,
   with a second, clearly-labeled ("Synthetic backfill compensation")
   InventoryTransaction log row alongside the sale's own SALE_DEDUCTION
   row -- an auditable correction, not a silent one. Inventory was not
   explicitly one of the six safety requirements this was built
   against, but leaving it altered as a side effect of a forecasting-
   only backfill would be its own kind of misrepresentation, so it's
   covered anyway.
7. Removable: writes synthetic_backfill_manifest.json (pk floors +
   BOT_USERNAME) the same way seed_sample_operations_data.py does, so
   remove_synthetic_backfill.py can undo precisely this run later --
   entirely independent of that other script's own manifest.
8. (Added 2026-11-06, after a production incident) Real stock is
   checked as sufficient BEFORE attempting a synthetic sale, and the
   sale + its compensation run inside one atomic transaction. The
   first live run crashed on a product whose real stock was already
   negative before this script touched anything:
   complete_sale_payment()'s deduction has no floor (by design), but
   the compensating adjust_stock() call does refuse to leave a result
   negative, and non-atomic execution let the deduction commit before
   its own compensation failed. A pair whose real stock can't safely
   support a synthetic sale is now skipped outright (reported at the
   end as "incomplete"), rather than attempted and left half-done. See
   scripts/recover_partial_backfill_20261106.py for how that one
   partial write was undone.

HONEST LIMITATION, stated here rather than just in conversation:
clearing the 3-non-zero-day floor makes generate_forecast() ATTEMPT a
real Holt-Winters fit instead of falling back to NAIVE_AVERAGE. It does
not make that fit accurate -- 3-5 non-zero days out of a 90-day window
is still extremely sparse, low-confidence data. This exercises the
pipeline across more pairs; it is not a claim that those forecasts are
now reliable.

Usage (from the backend/ directory, with the venv active):
    Dry run FIRST (default -- prints exactly what would be created,
    writes nothing):
        python manage.py shell --settings=config.settings.production \\
            -c "from scripts.backfill_thin_forecast_pairs import run; run()"

    Only after reviewing that output, the real run:
        python manage.py shell --settings=config.settings.production \\
            -c "from scripts.backfill_thin_forecast_pairs import run; run(dry_run=False)"
"""

import json
import os
import random
import sys
from decimal import Decimal
from pathlib import Path

import django

if not django.apps.apps.ready:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    django.setup()

from django.db import transaction  # noqa: E402
from django.db.models import Max  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.accounts.models import Branch, Role, User  # noqa: E402
from apps.forecasting.ml.data_prep import (
    build_daily_sales_series,
    has_sufficient_history,
)  # noqa: E402
from apps.forecasting.models import AIInsight, Forecast, InventoryRiskScore  # noqa: E402
from apps.forecasting.services import get_branch_product_pairs_with_sales_history  # noqa: E402
from apps.inventory import services as inventory_services  # noqa: E402
from apps.inventory.models import Inventory, InventoryTransaction, Product  # noqa: E402
from apps.pos import services as pos_services  # noqa: E402
from apps.pos.models import SalesItem, SalesTransaction  # noqa: E402

MANIFEST_PATH = Path(__file__).resolve().parent / "synthetic_backfill_manifest.json"

BOT_USERNAME = "synthetic_backfill_bot"

# Mirrors apps.forecasting.ml.data_prep.has_sufficient_history and
# build_daily_sales_series's own defaults. These are plain constants, not
# imported from data_prep's own default, so they MUST be kept in sync by
# hand whenever that default changes -- updated 90 -> 365 alongside the
# 2026-11-14 window-widening change; if data_prep's default ever changes
# again without updating this, this script would silently judge
# "under-served" against a different window than production forecasts
# actually use.
MINIMUM_NON_ZERO_DAYS = 3
DAYS_HISTORY = 365

# Fixed seed -- reproducible which days/quantities get chosen, same
# spirit as seed_sample_operations_data.py's own random.seed().
random.seed(20261106)


def _max_pk(model):
    return model.objects.aggregate(m=Max("pk"))["m"] or 0


def ensure_bot_user():
    """A single dedicated identity for every synthetic sale this script
    ever creates -- not scoped to one branch (branch=None, same
    convention User.branch already documents for OWNER_ADMIN), since
    this bot needs to place sales at whichever branch each under-served
    pair belongs to. Never logs in -- unusable password."""
    # OWNER_ADMIN already exists in any database that's run seed_demo_data.py
    # (required reference data); get_or_create here only guards a database
    # this script somehow runs against before that.
    role, _ = Role.objects.get_or_create(name=Role.OWNER_ADMIN)
    user, created = User.objects.get_or_create(
        username=BOT_USERNAME,
        defaults={
            "first_name": "Synthetic Backfill Bot (forecast-only, not a real staff account)",
            "role": role,
            "branch": None,
            "is_active": True,
        },
    )
    if created:
        user.set_unusable_password()
        user.save()
        print(f"  Created dedicated synthetic-sales identity: {BOT_USERNAME}")
    else:
        print(f"  Using existing synthetic-sales identity: {BOT_USERNAME}")
    return user


def find_under_served_pairs():
    """Same pair list and threshold as audit_forecast_coverage.py --
    reuses the real forecasting-pipeline functions directly rather than
    reimplementing the rule, so this can never check a different
    standard than what generate_forecast() actually applies."""
    under_served = []
    for branch_id, product_id in get_branch_product_pairs_with_sales_history():
        branch = Branch.objects.get(pk=branch_id)
        product = Product.objects.get(pk=product_id)
        series = build_daily_sales_series(branch, product, days_history=DAYS_HISTORY)
        if has_sufficient_history(series, minimum_days=14):
            continue

        non_zero_days = int((series != 0).sum())
        shortfall = max(0, MINIMUM_NON_ZERO_DAYS - non_zero_days)
        if shortfall == 0:
            # short on window length, not non-zero days -- nothing this
            # script can safely add
            continue

        zero_days = [idx.date() for idx, qty in series.items() if qty == 0]
        under_served.append(
            {
                "branch": branch,
                "product": product,
                "shortfall": shortfall,
                "zero_days": zero_days,
                "non_zero_days": non_zero_days,
            }
        )
    return under_served


class _SkipSale(Exception):
    """Raised inside the atomic block below to abandon this one
    candidate sale cleanly -- caught outside the `with`, never inside
    it, so Django's atomic() always sees the exception propagate and
    rolls back the whole block rather than leaving a partial write."""


def _has_sufficient_stock(branch, product, quantity):
    """Shared by both the dry-run preview and the live path, so a
    dry run actually previews what the live run will do -- including
    skips -- rather than unconditionally claiming every candidate day
    would be created. (Found right after the atomicity fix: the dry
    run never called this check at all, so it kept showing a
    known-negative-stock pair as "would create" when the live run
    would actually skip it.)

    Read-only deliberately -- a plain filter().first(), not
    get_or_create(): this is called from the dry-run path too, which
    must never write anything, not even an Inventory row that happens
    to be missing. A missing row reads as 0 (healthy default; the
    real create-if-missing still happens, correctly, in
    _create_synthetic_sale's own live-path lookup)."""
    inv = Inventory.objects.filter(branch=branch, product=product).first()
    current_qty = inv.quantity_on_hand if inv is not None else Decimal("0")
    return current_qty >= quantity, current_qty


def _create_synthetic_sale(bot_user, branch, product, day_date, quantity):
    """One synthetic sale, through the real service layer -- same
    deduction/logging side effects a real sale would trigger, same as
    seed_sample_operations_data.py's own approach, then immediately
    compensated (see module docstring, safety property 6).

    Two defenses added after the 2026-11-06 production incident, where
    a product's real stock was already negative before this script
    ran: complete_sale_payment()'s own deduction has no floor (by
    design), but the compensating adjust_stock() call does refuse to
    leave a result negative -- so a sale deducting from already-thin
    or negative real stock could succeed while its own compensation
    then failed, and non-atomic execution let that partial state
    commit. Fixed here by (1) checking real stock is sufficient BEFORE
    attempting anything, so a thin/negative-stock item is skipped
    outright rather than deducted past zero, and (2) wrapping the
    whole sale+compensation sequence in one atomic block, so any
    unexpected failure anywhere in it rolls back everything instead of
    leaving a half-done write."""
    sufficient, current_qty = _has_sufficient_stock(branch, product, quantity)
    if not sufficient:
        print(
            f"    skipping {day_date} -- real stock for {product.name} @ {branch.name} "
            f"is {current_qty}, not enough to safely deduct {quantity} unit(s) "
            "and compensate without going negative. Not attempting this sale."
        )
        return None

    inv, _ = Inventory.objects.get_or_create(branch=branch, product=product)
    try:
        with transaction.atomic():
            draft = pos_services.get_or_create_draft_transaction(bot_user, branch)
            pos_services.add_catalog_item(draft, product.pk, quantity=quantity)
            if not draft.items.exists():
                raise _SkipSale(f"could not add {product.name} to a draft order")

            pos_services.set_dining_option(draft, SalesTransaction.DiningOption.TAKE_OUT)
            total = draft.grand_total
            completed = pos_services.complete_sale_payment(
                draft,
                payment_method=SalesTransaction.PaymentMethod.CASH,
                amount_tendered=total,
            )

            hour = random.randint(8, 18)
            minute = random.randint(0, 59)
            ts = timezone.make_aware(
                timezone.datetime(day_date.year, day_date.month, day_date.day, hour, minute)
            )
            SalesTransaction.objects.filter(pk=completed.pk).update(created_at=ts, completed_at=ts)
            SalesItem.objects.filter(transaction_id=completed.pk).update(
                created_at=ts, updated_at=ts
            )

            # Undo complete_sale_payment's unconditional stock deduction
            # (safety property 6) -- real Inventory.quantity_on_hand must
            # not end this script altered, only the forecasting-relevant
            # sales rows should be. The pre-check above guarantees this
            # can never fail on a negative-result refusal, since we only
            # got here because quantity_on_hand was already >= quantity.
            #
            # MUST re-fetch here, not reuse the `inv` object from the
            # pre-check above: adjust_stock() writes back
            # inventory.quantity_on_hand from whatever is already in
            # memory on the object it's given, not a fresh read. The
            # pre-check's `inv` was fetched BEFORE complete_sale_payment()
            # deducted stock (via its own separate fetch), so passing
            # that same object here would make adjust_stock() compute
            # "pre-deduction value + quantity" and overwrite the real,
            # already-deducted DB value with it -- silently discarding
            # the deduction instead of compensating it. Caught locally
            # by testing this exact fix against a clean, round-number
            # baseline before it ever reached production.
            inv.refresh_from_db()
            inventory_services.adjust_stock(
                inv,
                quantity,
                reason="Synthetic backfill compensation -- restores stock a synthetic "
                "sale deducted (sample data, forecast-coverage backfill)",
            )
            InventoryTransaction.objects.filter(
                pk=InventoryTransaction.objects.latest("pk").pk
            ).update(created_at=ts)

            return completed
    except (pos_services.PaymentError, inventory_services.InventoryServiceError, _SkipSale) as exc:
        print(
            f"    could not complete synthetic sale for {product.name} on {day_date}: "
            f"{exc} -- skipped (rolled back, nothing committed)."
        )
        return None


def backfill(under_served_pairs, bot_user, dry_run):
    created_count = 0
    details = []
    incomplete_pairs = []
    today = timezone.localdate()

    for entry in under_served_pairs:
        branch, product, shortfall = entry["branch"], entry["product"], entry["shortfall"]
        candidate_days = [d for d in entry["zero_days"] if d < today]
        chosen_days = random.sample(candidate_days, k=min(shortfall, len(candidate_days)))

        print(f"\n  {product.name} @ {branch.name}: needs +{shortfall} non-zero day(s)")
        succeeded_for_pair = 0
        for day_date in sorted(chosen_days):
            quantity = random.randint(1, 2)
            if dry_run:
                sufficient, current_qty = _has_sufficient_stock(branch, product, quantity)
                if sufficient:
                    print(f"    [DRY RUN] would create: {day_date} -- {quantity} unit(s)")
                    succeeded_for_pair += 1
                else:
                    print(
                        f"    [DRY RUN] would SKIP {day_date} -- real stock is "
                        f"{current_qty}, not enough to safely deduct {quantity} unit(s)."
                    )
                continue
            txn = _create_synthetic_sale(bot_user, branch, product, day_date, quantity)
            if txn is not None:
                created_count += 1
                succeeded_for_pair += 1
                details.append(
                    {
                        "branch": branch.name,
                        "product": product.name,
                        "date": day_date.isoformat(),
                        "quantity": quantity,
                        "transaction_pk": txn.pk,
                    }
                )
                print(f"    {day_date} -- {quantity} unit(s) -- transaction #{txn.pk}")

        if succeeded_for_pair < shortfall:
            incomplete_pairs.append(
                {
                    "branch": branch.name,
                    "product": product.name,
                    "shortfall": shortfall,
                    "succeeded": succeeded_for_pair,
                }
            )

    return created_count, details, incomplete_pairs


def _write_manifest(pk_floor, bot_username, details):
    manifest = {
        "backfilled_at": timezone.now().isoformat(),
        "bot_username": bot_username,
        "pk_floor": pk_floor,
        "synthetic_sales": details,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    print(f"\nWrote removal manifest to {MANIFEST_PATH}.")
    print("Run scripts/remove_synthetic_backfill.py to undo precisely this run later.")


def run(dry_run=True):
    print("=" * 70)
    mode = "DRY RUN -- no writes will be made" if dry_run else "LIVE RUN"
    print(f"Backfilling thin forecast-coverage pairs ({mode})")
    print("=" * 70)

    real_transactions_before = (
        SalesTransaction.objects.filter(status=SalesTransaction.Status.COMPLETED)
        .exclude(cashier__username=BOT_USERNAME)
        .count()
    )
    print(f"\nReal (non-synthetic) completed transactions right now: {real_transactions_before}")

    pk_floor = {
        "sales_transaction": _max_pk(SalesTransaction),
        "sales_item": _max_pk(SalesItem),
        "inventory_transaction": _max_pk(InventoryTransaction),
        "forecast": _max_pk(Forecast),
        "inventory_risk_score": _max_pk(InventoryRiskScore),
        "ai_insight": _max_pk(AIInsight),
    }

    print("\nFinding under-served pairs (same rule as audit_forecast_coverage.py)...")
    under_served = find_under_served_pairs()
    if not under_served:
        print("  None found -- nothing to backfill. (If this is unexpected, run")
        print("  audit_forecast_coverage.py first to see current coverage.)")
        return

    for e in under_served:
        print(
            f"  {e['product'].name} @ {e['branch'].name} -- {e['non_zero_days']} non-zero day(s), "
            f"needs +{e['shortfall']}"
        )

    if dry_run:
        print("\nDRY RUN -- previewing what would be created (no bot account created, no writes):")
        _, _, incomplete_pairs = backfill(under_served, bot_user=None, dry_run=True)
        if incomplete_pairs:
            print(
                f"\n  {len(incomplete_pairs)} pair(s) would NOT reach their full shortfall "
                "even in a live run (insufficient real stock -- see the 'would SKIP' "
                "messages above):"
            )
            for p in incomplete_pairs:
                print(
                    f"    {p['product']} @ {p['branch']} -- would get "
                    f"{p['succeeded']}/{p['shortfall']} day(s); would stay under-served."
                )
        print("\n" + "=" * 70)
        print("Dry run complete. No database changes were made.")
        print("Review the above, then call run(dry_run=False) to actually create these rows.")
        print("=" * 70)
        return

    bot_user = ensure_bot_user()

    print("\nCreating synthetic sales (only on days each pair had zero real sales)...")
    created_count, details, incomplete_pairs = backfill(under_served, bot_user, dry_run=False)
    print(f"\n  Created {created_count} synthetic sale(s) across {len(under_served)} pair(s).")

    if incomplete_pairs:
        print(
            f"\n  {len(incomplete_pairs)} pair(s) did NOT reach their full shortfall "
            "(commonly: insufficient real stock to safely deduct from -- see the "
            "'skipping' messages above for why):"
        )
        for p in incomplete_pairs:
            print(
                f"    {p['product']} @ {p['branch']} -- got {p['succeeded']}/{p['shortfall']} "
                "day(s); still under-served after this run."
            )

    real_transactions_after = (
        SalesTransaction.objects.filter(status=SalesTransaction.Status.COMPLETED)
        .exclude(cashier__username=BOT_USERNAME)
        .count()
    )
    print("\nValidation (requirement 6 -- confirm no real data was touched):")
    print(f"  Real (non-synthetic) completed transactions before: {real_transactions_before}")
    print(f"  Real (non-synthetic) completed transactions after:  {real_transactions_after}")
    if real_transactions_after != real_transactions_before:
        print(
            "  !! MISMATCH -- the real transaction count changed. Stop and investigate "
            "before running anything else; do not run remove_synthetic_backfill.py blindly."
        )
    else:
        print("  OK -- unchanged, exactly as expected.")

    _write_manifest(pk_floor, BOT_USERNAME, details)

    print("\n" + "=" * 70)
    print("Done. Re-run audit_forecast_coverage.py to confirm these pairs now pass")
    print("the sufficiency floor, then re-run forecasting to regenerate forecasts:")
    print(
        "  python manage.py shell --settings=config.settings.production -c "
        '"from apps.forecasting.services import run_forecast_for_all_products as f; f()"'
    )
    print("=" * 70)


if __name__ == "__main__":
    run()
