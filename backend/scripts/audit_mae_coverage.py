"""
READ-ONLY diagnostic, step 2 of assessing the panel's request to
"supplement the existing client dataset with synthetic sales data so we
can properly test the forecasting model": for every (branch, product)
pair that already gets a real Holt-Winters forecast but NO holdout MAE
score (apps.forecasting.ml.forecast_model._compute_holdout_mae returns
None for it), shows exactly why -- and by how much.

Makes NO database writes. Safe to run on production at any time, any
number of times. Does NOT trust the stored Forecast.model_used/mae
columns (those are an append-only log of the last scheduled run and can
go stale between runs -- see the Alangilan hero-pair stale-audit
finding earlier in this project); every number here is recomputed live
from the real SalesItem rows via build_daily_sales_series, exactly the
inputs apps.forecasting.ml.forecast_model.generate_forecast itself uses.

Why a pair can be on real HoltWinters but still have no MAE: the window
is always a fixed 365 days (build_daily_sales_series zero-fills it), so
"not enough *days*" is never the real gate -- it's the number of
NON-ZERO days. generate_forecast needs >=3 non-zero days anywhere in
the 365-day window to use HoltWinters at all. _compute_holdout_mae
additionally splits off the last 7 days as a held-out test set and
needs the remaining training portion ALONE to still have >=3 non-zero
days. A pair whose non-zero days are barely at/above 3, with one or
more of them inside that last 7-day window, clears the first bar but
not the second.

Usage:
    python manage.py shell --settings=config.settings.production \
        -c "from scripts.audit_mae_coverage import run; run()"
"""

import os
import sys

import django

if not django.apps.apps.ready:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    django.setup()

from apps.accounts.models import Branch  # noqa: E402
from apps.forecasting.ml.data_prep import (
    build_daily_sales_series,
    has_sufficient_history,
)  # noqa: E402
from apps.forecasting.ml.forecast_model import _has_enough_for_seasonal_fit  # noqa: E402
from apps.forecasting.services import get_branch_product_pairs_with_sales_history  # noqa: E402
from apps.inventory.models import Product  # noqa: E402
from apps.pos.models import SalesTransaction  # noqa: E402

HOLDOUT_DAYS = 7
MINIMUM_NON_ZERO_DAYS = 3


def run():
    print("=" * 70)
    print("Holdout-MAE coverage audit (read-only)")
    print("=" * 70)

    total_real_transactions = SalesTransaction.objects.filter(
        status=SalesTransaction.Status.COMPLETED
    ).count()
    print(f"\nReal completed transactions right now: {total_real_transactions}")
    print(
        "(Unchanged by this script -- recorded as a tripwire, same as audit_forecast_coverage.py.)"
    )

    pairs = get_branch_product_pairs_with_sales_history()
    print(f"\n{len(pairs)} (branch, product) pairs have at least one real completed sale ever.\n")

    has_mae = []
    no_mae_fixable = []
    no_mae_not_hw = []

    for branch_id, product_id in pairs:
        branch = Branch.objects.get(pk=branch_id)
        product = Product.objects.get(pk=product_id)
        series = build_daily_sales_series(branch, product)

        full_non_zero = int((series != 0).sum())
        on_holt_winters = has_sufficient_history(series) and _has_enough_for_seasonal_fit(series)

        if not on_holt_winters:
            no_mae_not_hw.append(
                {"branch": branch.name, "product": product.name, "full_non_zero": full_non_zero}
            )
            continue

        train = series.iloc[:-HOLDOUT_DAYS]
        holdout = series.iloc[-HOLDOUT_DAYS:]
        train_non_zero = int((train != 0).sum())
        holdout_non_zero = int((holdout != 0).sum())

        entry = {
            "branch": branch.name,
            "product": product.name,
            "branch_id": branch_id,
            "product_id": product_id,
            "full_non_zero": full_non_zero,
            "train_non_zero": train_non_zero,
            "holdout_non_zero": holdout_non_zero,
            "shortfall": max(0, MINIMUM_NON_ZERO_DAYS - train_non_zero),
        }

        if train_non_zero >= MINIMUM_NON_ZERO_DAYS:
            has_mae.append(entry)
        else:
            no_mae_fixable.append(entry)

    print(f"Already get a real holdout MAE: {len(has_mae)}")

    print(
        f"\nOn HoltWinters but NO MAE yet -- fixable by adding non-zero days to training data: "
        f"{len(no_mae_fixable)}"
    )
    for e in sorted(no_mae_fixable, key=lambda x: x["shortfall"]):
        print(
            f"  {e['product']} @ {e['branch']} -- {e['full_non_zero']} non-zero day(s) total "
            f"({e['train_non_zero']} in training, {e['holdout_non_zero']} in the held-out last "
            f"{HOLDOUT_DAYS} days) -- needs +{e['shortfall']} more non-zero day(s) in training "
            f"(must land outside the last {HOLDOUT_DAYS} days, and on a day the real series is "
            f"currently zero)"
        )

    print(f"\nStill on NAIVE_AVERAGE (not touched by this plan): {len(no_mae_not_hw)}")
    for e in no_mae_not_hw:
        print(f"  {e['product']} @ {e['branch']} -- {e['full_non_zero']} non-zero day(s) total")

    print("\n" + "=" * 70)
    print(
        f"Summary: {len(has_mae)} with real MAE, {len(no_mae_fixable)} fixable, "
        f"{len(no_mae_not_hw)} still NAIVE, out of {len(pairs)} pairs."
    )
    print(f"Real completed transactions (unchanged by this script): {total_real_transactions}")
    print("=" * 70)


if __name__ == "__main__":
    run()
