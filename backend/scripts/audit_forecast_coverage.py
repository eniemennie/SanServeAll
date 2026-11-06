"""
READ-ONLY diagnostic: reports which (branch, product) pairs with real
sales history are currently under-served by the Holt-Winters pipeline
(i.e. falling back to NAIVE_AVERAGE because they don't clear the 14-day
/ 3-non-zero-day floor in apps.forecasting.ml.data_prep.has_sufficient_history),
and by how much they're short.

Makes NO database writes. Safe to run on production at any time, any
number of times -- it only reads Forecast rows (written by the last
run_forecast_for_all_products() call) and recomputes each pair's daily
sales series (also read-only) to quantify the shortfall.

This is step 1 of assessing a safe synthetic-data-backfill plan: before
generating anything, we need the actual list of under-served pairs and
how far each is from the threshold, on the real production data -- not
a guess.

Usage:
    python manage.py shell --settings=config.settings.production -c "from scripts.audit_forecast_coverage import run; run()"
"""

import os
import sys

import django

if not django.apps.apps.ready:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    django.setup()

from apps.forecasting.ml.data_prep import (
    build_daily_sales_series,
    has_sufficient_history,
)  # noqa: E402
from apps.forecasting.services import get_branch_product_pairs_with_sales_history  # noqa: E402
from apps.accounts.models import Branch  # noqa: E402
from apps.inventory.models import Product  # noqa: E402
from apps.forecasting.models import Forecast  # noqa: E402
from apps.pos.models import SalesTransaction  # noqa: E402


def run():
    print("=" * 70)
    print("Forecast coverage audit (read-only)")
    print("=" * 70)

    total_real_transactions = SalesTransaction.objects.filter(
        status=SalesTransaction.Status.COMPLETED
    ).count()
    print(f"\nReal completed transactions in the database right now: {total_real_transactions}")
    print("(This script does not touch any of them -- recorded here only so the")
    print(" number can be compared after any later step, as a tripwire.)")

    pairs = get_branch_product_pairs_with_sales_history()
    print(f"\n{len(pairs)} (branch, product) pairs have at least one real completed sale ever:\n")

    # Pull the latest model_used per pair directly (append-only log, so
    # take the most recent row per pair by pk).
    latest_forecast_rows = {}
    for row in Forecast.objects.order_by("branch_id", "product_id", "-generated_at", "-pk"):
        key = (row.branch_id, row.product_id)
        if key not in latest_forecast_rows:
            latest_forecast_rows[key] = row

    under_served = []
    adequate = []

    for branch_id, product_id in pairs:
        branch = Branch.objects.get(pk=branch_id)
        product = Product.objects.get(pk=product_id)
        series = build_daily_sales_series(branch, product)
        non_zero_days = int((series != 0).sum())
        total_days = len(series)
        sufficient = has_sufficient_history(series)

        forecast_row = latest_forecast_rows.get((branch_id, product_id))
        model_used = forecast_row.model_used if forecast_row else "(no forecast on record)"

        entry = {
            "branch": branch.name,
            "product": product.name,
            "branch_id": branch_id,
            "product_id": product_id,
            "non_zero_days": non_zero_days,
            "total_days": total_days,
            "sufficient": sufficient,
            "model_used": model_used,
        }

        if sufficient:
            adequate.append(entry)
        else:
            under_served.append(entry)

    print(f"Adequate history (real Holt-Winters eligible): {len(adequate)}")
    for e in adequate:
        print(
            f"  OK   {e['product']} @ {e['branch']} -- {e['non_zero_days']} non-zero / "
            f"{e['total_days']} days -- model_used={e['model_used']}"
        )

    print(f"\nUnder-served (falls back to NAIVE_AVERAGE): {len(under_served)}")
    for e in under_served:
        shortfall_days = max(0, 14 - e["total_days"])
        shortfall_nonzero = max(0, 3 - e["non_zero_days"])
        print(
            f"  NAIVE {e['product']} @ {e['branch']} -- {e['non_zero_days']} non-zero / "
            f"{e['total_days']} days -- needs +{shortfall_nonzero} non-zero day(s), "
            f"+{shortfall_days} day(s) of window -- model_used={e['model_used']}"
        )

    print("\n" + "=" * 70)
    print(
        f"Summary: {len(adequate)} adequate, {len(under_served)} under-served, "
        f"out of {len(pairs)} pairs with any real sales history."
    )
    print(f"Real completed transactions (unchanged by this script): {total_real_transactions}")
    print("=" * 70)


if __name__ == "__main__":
    run()
