"""
Step 3 of the panel's request ("supplement the existing client dataset
with synthetic sales data so we can properly test the forecasting
model"): targeted synthetic-data backfill for the specific pairs
scripts/audit_mae_coverage.py reports as "on HoltWinters but no MAE
yet" -- pairs that already clear the 3-non-zero-day floor for a real
forecast, but whose TRAINING portion (the 365-day window minus the
last HOLDOUT_DAYS days, which apps.forecasting.ml.forecast_model.
_compute_holdout_mae holds out as a test set) falls just short of that
same 3-non-zero-day floor, so no real accuracy (MAE) score can be
computed for them.

This is deliberately NOT scripts/backfill_thin_forecast_pairs.py (fixes
a DIFFERENT shortfall -- total non-zero days, anywhere in the window --
for a DIFFERENT, non-overlapping set of pairs: those still on
NAIVE_AVERAGE). A pair already on real HoltWinters never appears here.
Reuses that script's own safety-reviewed primitives rather than
reimplementing them (same dedicated bot identity, same atomic
sale+compensation, same stock pre-check) -- see its module docstring
for the full rationale behind each one. Only this script's own
candidate-day selection differs, and it differs in exactly one way:

    THE KEY DIFFERENCE: candidate zero-days are restricted to the
    TRAINING portion of the window (series.index[:-HOLDOUT_DAYS]) only.
    backfill_thin_forecast_pairs.py picks from any zero-day before
    today, including the last HOLDOUT_DAYS days -- fine for its own
    goal (any non-zero day anywhere raises that pair's total above 3),
    but wrong for this one: a day added inside the held-out window
    would still leave the training portion short, and the MAE would
    stay None.

Safety properties (identical to backfill_thin_forecast_pairs.py's own
8, not restated in full here):
  1. INSERT-only. 2. Dedicated bot account (BOT_USERNAME, shared with
  that script -- same identity isolates every synthetic row either
  script has ever created). 3. Only on a day the real series is
  currently zero. 4. Small quantity (1-2 units). 5. Exactly as many
  days as each pair is short, never more. 6. Inventory is compensated
  back to its pre-sale value inside the same atomic block. 7. Own
  manifest (mae_coverage_manifest.json) for later removal. 8. Real
  stock checked sufficient before attempting a synthetic sale.

HONEST LIMITATION: every one of the 16 pairs the audit found needs
only 1-2 more non-zero days in training. That clears the bare 3-day
floor so _compute_holdout_mae can run at all -- it does not make the
resulting MAE a strong, low-variance estimate. Same limitation
backfill_thin_forecast_pairs.py already states about its own 3-5
non-zero days: this exercises the evaluation pipeline across more
pairs, not a claim those MAE scores are highly precise.

Usage (dry run FIRST -- writes nothing):
    python manage.py shell --settings=config.settings.production \
        -c "from scripts.backfill_mae_coverage_pairs import run; run()"

Only after reviewing that output:
    python manage.py shell --settings=config.settings.production \
        -c "from scripts.backfill_mae_coverage_pairs import run; run(dry_run=False)"
"""

import json
import os
import random
import sys
from pathlib import Path

import django

if not django.apps.apps.ready:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    django.setup()

from django.utils import timezone  # noqa: E402

from apps.accounts.models import Branch  # noqa: E402
from apps.forecasting.ml.data_prep import (
    build_daily_sales_series,
    has_sufficient_history,
)  # noqa: E402
from apps.forecasting.ml.forecast_model import _has_enough_for_seasonal_fit  # noqa: E402
from apps.forecasting.models import AIInsight, Forecast, InventoryRiskScore  # noqa: E402
from apps.forecasting.services import get_branch_product_pairs_with_sales_history  # noqa: E402
from apps.inventory.models import InventoryTransaction, Product  # noqa: E402
from apps.pos.models import SalesItem, SalesTransaction  # noqa: E402
from scripts.backfill_thin_forecast_pairs import (  # noqa: E402
    BOT_USERNAME,
    _create_synthetic_sale,
    _has_sufficient_stock,
    _max_pk,
    ensure_bot_user,
)

MANIFEST_PATH = Path(__file__).resolve().parent / "mae_coverage_manifest.json"

MINIMUM_NON_ZERO_DAYS = 3
HOLDOUT_DAYS = 7  # must match forecast_model._compute_holdout_mae's own default

# Fixed seed -- reproducible which days/quantities get chosen, same
# spirit as the other backfill scripts' own random.seed() calls.
random.seed(20261117)


def find_mae_shortfall_pairs():
    """Same rule as scripts/audit_mae_coverage.py's "fixable" bucket --
    reuses the real forecasting-pipeline functions directly so this can
    never check a different standard than what generate_forecast()/
    _compute_holdout_mae() actually apply."""
    shortfall_pairs = []
    for branch_id, product_id in get_branch_product_pairs_with_sales_history():
        branch = Branch.objects.get(pk=branch_id)
        product = Product.objects.get(pk=product_id)
        series = build_daily_sales_series(branch, product)

        on_holt_winters = has_sufficient_history(series) and _has_enough_for_seasonal_fit(series)
        if not on_holt_winters:
            continue  # still on NAIVE_AVERAGE -- that's backfill_thin_forecast_pairs.py's job

        train = series.iloc[:-HOLDOUT_DAYS]
        train_non_zero = int((train != 0).sum())
        shortfall = max(0, MINIMUM_NON_ZERO_DAYS - train_non_zero)
        if shortfall == 0:
            continue  # already gets a real MAE

        training_zero_days = [idx.date() for idx, qty in train.items() if qty == 0]
        shortfall_pairs.append(
            {
                "branch": branch,
                "product": product,
                "shortfall": shortfall,
                "zero_days": training_zero_days,
                "train_non_zero": train_non_zero,
            }
        )
    return shortfall_pairs


def backfill(shortfall_pairs, bot_user, dry_run):
    created_count = 0
    details = []
    incomplete_pairs = []

    for entry in shortfall_pairs:
        branch, product, shortfall = entry["branch"], entry["product"], entry["shortfall"]
        chosen_days = random.sample(entry["zero_days"], k=min(shortfall, len(entry["zero_days"])))

        print(f"\n  {product.name} @ {branch.name}: needs +{shortfall} non-zero training day(s)")
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
    print(
        "No dedicated removal script exists for this manifest yet (same as "
        "backfill_hero_demo_patterns.py) -- it follows the exact same {pk_floor, "
        "bot_username, synthetic_sales} shape as synthetic_backfill_manifest.json, "
        "so remove_synthetic_backfill.py's own logic can be pointed at it if needed."
    )


def run(dry_run=True):
    print("=" * 70)
    mode = "DRY RUN -- no writes will be made" if dry_run else "LIVE RUN"
    print(f"Backfilling holdout-MAE-shortfall pairs ({mode})")
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

    print("\nFinding holdout-MAE-shortfall pairs (same rule as audit_mae_coverage.py)...")
    shortfall_pairs = find_mae_shortfall_pairs()
    if not shortfall_pairs:
        print("  None found -- nothing to backfill. (If this is unexpected, run")
        print("  audit_mae_coverage.py first to see current coverage.)")
        return

    for e in shortfall_pairs:
        print(
            f"  {e['product'].name} @ {e['branch'].name} -- {e['train_non_zero']} non-zero "
            f"training day(s), needs +{e['shortfall']}"
        )

    if dry_run:
        print("\nDRY RUN -- previewing what would be created (no bot account created, no writes):")
        _, _, incomplete_pairs = backfill(shortfall_pairs, bot_user=None, dry_run=True)
        if incomplete_pairs:
            print(
                f"\n  {len(incomplete_pairs)} pair(s) would NOT reach their full shortfall "
                "even in a live run (insufficient real stock):"
            )
            for p in incomplete_pairs:
                print(
                    f"    {p['product']} @ {p['branch']} -- would get "
                    f"{p['succeeded']}/{p['shortfall']} day(s); would stay without a real MAE."
                )
        print("\n" + "=" * 70)
        print("Dry run complete. No database changes were made.")
        print("Review the above, then call run(dry_run=False) to actually create these rows.")
        print("=" * 70)
        return

    bot_user = ensure_bot_user()

    print(
        "\nCreating synthetic sales (only on training-window days each pair had zero real sales)..."
    )
    created_count, details, incomplete_pairs = backfill(shortfall_pairs, bot_user, dry_run=False)
    print(f"\n  Created {created_count} synthetic sale(s) across {len(shortfall_pairs)} pair(s).")

    if incomplete_pairs:
        print(
            f"\n  {len(incomplete_pairs)} pair(s) did NOT reach their full shortfall "
            "(commonly: insufficient real stock):"
        )
        for p in incomplete_pairs:
            print(
                f"    {p['product']} @ {p['branch']} -- got {p['succeeded']}/{p['shortfall']} "
                "day(s); still without a real MAE after this run."
            )

    real_transactions_after = (
        SalesTransaction.objects.filter(status=SalesTransaction.Status.COMPLETED)
        .exclude(cashier__username=BOT_USERNAME)
        .count()
    )
    print("\nValidation (confirm no real data was touched):")
    print(f"  Real (non-synthetic) completed transactions before: {real_transactions_before}")
    print(f"  Real (non-synthetic) completed transactions after:  {real_transactions_after}")
    if real_transactions_after != real_transactions_before:
        print(
            "  !! MISMATCH -- the real transaction count changed. Stop and investigate "
            "before running anything else."
        )
    else:
        print("  OK -- unchanged, exactly as expected.")

    _write_manifest(pk_floor, BOT_USERNAME, details)

    print("\n" + "=" * 70)
    print("Done. Regenerate forecasts, then re-run audit_mae_coverage.py to confirm")
    print("these pairs now show a real MAE score:")
    print(
        "  python manage.py shell --settings=config.settings.production -c "
        '"from apps.forecasting.services import run_forecast_for_all_products as f; f()"'
    )
    print("=" * 70)


if __name__ == "__main__":
    run()
