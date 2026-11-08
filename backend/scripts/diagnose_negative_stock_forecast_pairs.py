"""
READ-ONLY diagnostic for the Consolidated Task Tracker item "Pre-existing
negative/zero real stock blocking 8 branch/product pairs from forecast
backfill" (masterlist, 2026-11-07/08).

Context: scripts/backfill_thin_forecast_pairs.py refuses to create a
synthetic sale for a (branch, product) pair when that pair's real
Inventory.quantity_on_hand is already too thin to safely cover even one
more unit sold (see that script's _has_sufficient_stock) -- this is a
deliberate safety property (added 2026-11-06 after a production
incident), not a bug in the backfill script. Of the 10 under-served
pairs scripts/audit_forecast_coverage.py reports, 2 had enough real
stock to backfill; this script exists to show, for the other 8, *why*
their real stock is already at/near zero -- not to fix anything.

Makes NO database writes. Safe to run on production at any time, any
number of times -- every query here is read-only.

For each under-served pair, reports:
  - Current real Inventory.quantity_on_hand right now.
  - Whether a HISTORICAL_BACKFILL InventoryTransaction exists for this
    exact (branch, product) -- i.e. whether task #104's one-off
    finished-goods-inventory reconstruction (see
    apps.production.management.commands.backfill_finished_goods_inventory)
    ever gave this pair a starting balance at all. If not, this pair's
    Inventory genuinely started at the Django default of 0 and has
    never had anything but real-time POS/production/transfer activity
    move it -- a materially different story from "a reconstructed
    opening balance turned out too low."
  - A sum of every InventoryTransaction this pair has ever logged,
    grouped by movement_type, so the trajectory from that opening
    balance (or zero) to today's number is visible at a glance, rather
    than just the single ending number.

This does NOT reach a verdict on whether the negative/thin stock itself
is a data problem worth correcting (that's the actual owner/adviser
decision this task tracker item is waiting on) -- it only shows the
mechanics behind today's number, which is the missing piece needed to
make that call with real information instead of a guess.

Usage (on the real target, exactly like audit_forecast_coverage.py):
    python manage.py shell --settings=config.settings.production \
        -c "from scripts.diagnose_negative_stock_forecast_pairs import run; run()"
"""

import os
import sys
from collections import defaultdict
from decimal import Decimal

import django

if not django.apps.apps.ready:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    django.setup()

from apps.accounts.models import Branch  # noqa: E402
from apps.forecasting.ml.data_prep import (  # noqa: E402
    build_daily_sales_series,
    has_sufficient_history,
)
from apps.forecasting.services import get_branch_product_pairs_with_sales_history  # noqa: E402
from apps.inventory.models import Inventory, InventoryTransaction, Product  # noqa: E402

# Matches scripts/backfill_thin_forecast_pairs.py's own _has_sufficient_stock
# check: a candidate synthetic sale is for 1 or 2 units, so "blocked" in
# practice means real stock can't even cover 1 more unit sold.
_MINIMUM_SAFE_QUANTITY = Decimal("1")


def run():
    print("=" * 70)
    print("Negative/zero real-stock diagnostic for under-served forecast pairs")
    print("(read-only -- makes no changes)")
    print("=" * 70)

    pairs = get_branch_product_pairs_with_sales_history()
    under_served = []
    for branch_id, product_id in pairs:
        branch = Branch.objects.get(pk=branch_id)
        product = Product.objects.get(pk=product_id)
        series = build_daily_sales_series(branch, product)
        if not has_sufficient_history(series):
            under_served.append((branch, product))

    print(f"\n{len(under_served)} under-served pair(s) found.\n")

    blocked, clear = [], []

    for branch, product in under_served:
        inv = Inventory.objects.filter(branch=branch, product=product).first()
        current_qty = inv.quantity_on_hand if inv is not None else Decimal("0")
        is_blocked = current_qty < _MINIMUM_SAFE_QUANTITY

        transactions = InventoryTransaction.objects.filter(branch=branch, product=product).order_by(
            "created_at"
        )

        totals_by_type = defaultdict(lambda: Decimal("0"))
        counts_by_type = defaultdict(int)
        for txn in transactions:
            totals_by_type[txn.movement_type] += txn.quantity_change
            counts_by_type[txn.movement_type] += 1

        has_historical_backfill = (
            InventoryTransaction.MovementType.HISTORICAL_BACKFILL in totals_by_type
        )

        entry = {
            "branch": branch.name,
            "product": product.name,
            "current_qty": current_qty,
            "is_blocked": is_blocked,
            "has_historical_backfill": has_historical_backfill,
            "totals_by_type": dict(totals_by_type),
            "counts_by_type": dict(counts_by_type),
            "transaction_count": transactions.count(),
        }
        (blocked if is_blocked else clear).append(entry)

    print(f"Blocked (real stock too thin to safely backfill): {len(blocked)}")
    for e in blocked:
        print(f"\n  {e['product']} @ {e['branch']}")
        print(f"    Current real quantity_on_hand: {e['current_qty']}")
        if e["transaction_count"] == 0:
            print(
                "    No InventoryTransaction rows at all for this pair -- stock has sat "
                "at the Django-default 0 since the row was created, untouched by any "
                "logged event. Likely never produced/transferred/reconstructed for this "
                "branch, only ever sold from (if at all)."
            )
        else:
            if e["has_historical_backfill"]:
                backfill_note = "YES"
            else:
                backfill_note = "NO -- this pair was never covered by that reconstruction"
            print(f"    Starting balance from task #104's historical backfill: {backfill_note}")
            print("    Full movement history, by type:")
            for movement_type, total in sorted(e["totals_by_type"].items()):
                count = e["counts_by_type"][movement_type]
                sign = "+" if total >= 0 else ""
                print(f"      {movement_type:<24} {sign}{total:>10}  ({count} transaction(s))")

    print(f"\nNot blocked (real stock currently sufficient): {len(clear)}")
    for e in clear:
        print(f"  {e['product']} @ {e['branch']} -- quantity_on_hand={e['current_qty']}")

    print("\n" + "=" * 70)
    print(
        f"Summary: {len(blocked)} blocked, {len(clear)} clear, out of "
        f"{len(under_served)} under-served pairs."
    )
    print("This script draws no conclusion about whether the blocked pairs' thin/negative")
    print("stock is itself a problem worth correcting -- that's the owner/adviser call this")
    print("task tracker item is waiting on. It only shows the mechanics behind today's number.")
    print("=" * 70)


if __name__ == "__main__":
    run()
