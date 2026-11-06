"""
Removes everything scripts/backfill_thin_forecast_pairs.py added --
restoring the database to its exact pre-backfill state. Entirely
independent of scripts/remove_sample_operations_data.py and its
manifest; that script undoes the broad 90-day demo-history seed, this
one undoes only the targeted handful of synthetic sales added to
specific under-served pairs.

How it stays precise: reads scripts/synthetic_backfill_manifest.json,
written by the backfill script the moment it finishes, which records a
primary-key floor for every model the backfill script only ever ADDS
rows to (SalesTransaction, SalesItem, InventoryTransaction, Forecast,
InventoryRiskScore, AIInsight) and the dedicated bot username every
synthetic sale was placed under.

Two independent ways to find the rows to remove, cross-checked:
  1. Every SalesTransaction with cashier__username == the manifest's
     bot_username.
  2. Every row of each tracked model with pk > that model's recorded
     floor.
Both should identify exactly the same SalesTransaction rows -- if they
ever disagree, something unexpected happened between the backfill run
and this cleanup (e.g. unrelated real activity against this exact bot
account, which should never happen since it's never used for anything
else), so this script reports the mismatch rather than silently
picking one.

Does NOT touch Inventory.quantity_on_hand or reorder_threshold: the
backfill script already compensates every synthetic sale's stock
deduction immediately (see that script's own docstring, safety
property 6), so real inventory levels were never left altered in the
first place -- there is nothing to restore here, unlike
remove_sample_operations_data.py which does restore inventory snapshots
for the broader demo seed.

Usage (from the backend/ directory, with the venv active):
    python manage.py shell --settings=config.settings.production \\
        -c "from scripts.remove_synthetic_backfill import run; run()"

Safe to run more than once: with no manifest present, it reports
there's nothing to do and makes no changes.
"""

import json
import os
import sys
from pathlib import Path

import django

if not django.apps.apps.ready:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    django.setup()

from django.db import transaction  # noqa: E402

from apps.accounts.models import User  # noqa: E402
from apps.forecasting.models import AIInsight, Forecast, InventoryRiskScore  # noqa: E402
from apps.inventory.models import InventoryTransaction  # noqa: E402
from apps.pos.models import SalesTransaction  # noqa: E402

MANIFEST_PATH = Path(__file__).resolve().parent / "synthetic_backfill_manifest.json"


def run():
    print("=" * 70)
    print("Removing synthetic forecast-coverage backfill")
    print("=" * 70)

    if not MANIFEST_PATH.exists():
        print(
            f"No manifest found at {MANIFEST_PATH} -- either the backfill was "
            "never run, or it has already been removed. Nothing to do."
        )
        return

    manifest = json.loads(MANIFEST_PATH.read_text())
    pk_floor = manifest["pk_floor"]
    bot_username = manifest["bot_username"]

    by_bot = set(
        SalesTransaction.objects.filter(cashier__username=bot_username).values_list("pk", flat=True)
    )
    by_floor = set(
        SalesTransaction.objects.filter(pk__gt=pk_floor["sales_transaction"]).values_list(
            "pk", flat=True
        )
    )
    if by_bot != by_floor:
        only_bot = by_bot - by_floor
        only_floor = by_floor - by_bot
        print(
            "!! The bot-account and pk-floor methods of identifying synthetic "
            "transactions disagree -- stopping without deleting anything."
        )
        print(f"   By bot account only: {sorted(only_bot)}")
        print(f"   By pk floor only: {sorted(only_floor)}")
        print("   Investigate before re-running this script.")
        return

    print(f"\nFound {len(by_bot)} synthetic sales transaction(s) to remove (both checks agree).")

    with transaction.atomic():
        deleted_sales, _ = SalesTransaction.objects.filter(pk__in=by_bot).delete()
        print(f"  Deleted synthetic sales transactions (and their line items): {deleted_sales}")

        deleted_inv_tx, _ = InventoryTransaction.objects.filter(
            pk__gt=pk_floor["inventory_transaction"]
        ).delete()
        print(f"  Deleted inventory transaction log rows: {deleted_inv_tx}")

        deleted_forecast, _ = Forecast.objects.filter(pk__gt=pk_floor["forecast"]).delete()
        print(f"  Deleted forecasts: {deleted_forecast}")

        deleted_risk, _ = InventoryRiskScore.objects.filter(
            pk__gt=pk_floor["inventory_risk_score"]
        ).delete()
        print(f"  Deleted inventory risk scores: {deleted_risk}")

        deleted_insight, _ = AIInsight.objects.filter(pk__gt=pk_floor["ai_insight"]).delete()
        print(f"  Deleted AI insights: {deleted_insight}")

        # Safe only now -- SalesTransaction.cashier is a PROTECT foreign
        # key, so this would fail loudly instead of silently cascading
        # if the bot account still had any transaction referencing it.
        deleted_users, _ = User.objects.filter(username=bot_username).delete()
        print(f"  Deleted the synthetic-sales bot account: {deleted_users}")

    MANIFEST_PATH.unlink()
    print(f"\nRemoved manifest ({MANIFEST_PATH}).")
    print("\n" + "=" * 70)
    print("Synthetic backfill removed. Real sales data and inventory levels")
    print("were never altered by either the backfill or this removal -- only")
    print("rows this script's own manifest tracked were touched.")
    print("=" * 70)


if __name__ == "__main__":
    run()
