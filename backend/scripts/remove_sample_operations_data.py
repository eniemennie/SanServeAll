"""
Removes everything scripts/seed_sample_operations_data.py added or
changed, restoring the database to its exact pre-seed state -- so the
sample/demo data can be safely swapped out for the client's real data
without touching the database schema (no migration is involved on
either side; this only ever inserts/deletes/updates ordinary rows in
tables that already exist) and without risking any already-present
real data.

How it stays precise: it reads scripts/sample_data_manifest.json,
written by the seed script the moment it finishes, which records:
  - the exact pre-seed value of every Product.reorder_threshold and
    every Inventory.quantity_on_hand the seed script touched,
  - a primary-key "floor" for every model the seed script only ever
    ADDS rows to (SalesTransaction, ProductionRecord,
    InventoryTransaction, Forecast, InventoryRiskScore, AIInsight) --
    anything with a pk above that floor was created by the seed run
    and is deleted here,
  - the exact demo usernames the seed script created.

This is precise rather than a guess at which rows "look like" sample
data, which matters once real client data starts coexisting in the
same tables -- a heuristic (e.g. "delete anything from the last 90
days") would just as easily delete real transactions that happen to
fall in that same window.

IMPORTANT ASSUMPTION: run this BEFORE any real operational data (real
sales, real production runs, real staff accounts) is entered into the
system. The pk-floor approach can only distinguish "created by the
seed run" from "everything else" by position in each table's
insertion order -- it has no way to tell a real transaction from a
sample one if both were created after the seed run and before this
cleanup runs. Reference/catalog data (branches, roles, the product
catalog itself) is never touched by either script and is always safe
to keep, rename, or add to at any time, before or after this runs.

What this restores exactly, and what it deliberately does NOT touch:
  - Deletes every SalesTransaction (and its SalesItem line items, which
    cascade-delete with it), every ProductionRecord (and its
    IngredientUsage rows, same cascade), every InventoryTransaction
    log row, and every Forecast/InventoryRiskScore/AIInsight row the
    seed run created.
  - Deletes the demo User accounts (cashier_<branch>, commissary_staff)
    and their CashierPIN rows -- only safe once the above is gone,
    since SalesTransaction.cashier and ProductionRecord.commissary_staff
    are PROTECT foreign keys (Django would refuse the user deletion
    while either still referenced one, rather than silently cascading).
  - Restores Product.reorder_threshold and Inventory.quantity_on_hand
    on every row the seed script touched back to their recorded
    pre-seed values. An Inventory row that did NOT exist before the
    seed run is left in place (deleting it could break a page that now
    expects that branch/product pairing to exist) but its quantity is
    reset to 0 rather than deleted, so no sample stock lingers.
  - Does NOT touch the product catalog, branches, roles, or any other
    reference data from seed_demo_data.py -- none of that is sample
    data, and the real system needs it to keep existing exactly as is.
  - Does NOT change the database schema in any way -- no migration
    runs here, same as the seed script. Every change is an ordinary
    row insert/delete/update in tables that already exist, so none of
    this affects the system's existing functionality.

Usage (from the backend/ directory, with the venv active):
    python manage.py shell < scripts/remove_sample_operations_data.py

Or:
    python manage.py shell
    >>> from scripts.remove_sample_operations_data import run
    >>> run()

Safe to run more than once: with no manifest present, it reports that
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
from apps.inventory.models import Inventory, InventoryTransaction, Product  # noqa: E402
from apps.pos.models import SalesTransaction  # noqa: E402
from apps.production.models import ProductionRecord  # noqa: E402

MANIFEST_PATH = Path(__file__).resolve().parent / "sample_data_manifest.json"


def _restore_reorder_thresholds(reorder_snapshot):
    restored = 0
    for product in Product.objects.filter(pk__in=reorder_snapshot.keys()):
        original = reorder_snapshot[product.pk]
        if product.reorder_threshold != original:
            Product.objects.filter(pk=product.pk).update(reorder_threshold=original)
            restored += 1
    print(f"  Restored reorder_threshold to its pre-seed value on {restored} products.")


def _restore_inventory(inventory_snapshot):
    restored = 0
    zeroed = 0
    for inv in Inventory.objects.all():
        if inv.pk in inventory_snapshot:
            original = inventory_snapshot[inv.pk]
            if inv.quantity_on_hand != original:
                Inventory.objects.filter(pk=inv.pk).update(quantity_on_hand=original)
                restored += 1
        elif inv.quantity_on_hand != 0:
            # Didn't exist before the seed run -- left in place (a page
            # may now depend on this branch/product pairing existing)
            # but zeroed out so no sample stock lingers.
            Inventory.objects.filter(pk=inv.pk).update(quantity_on_hand=0)
            zeroed += 1
    print(f"  Restored quantity_on_hand to its pre-seed value on {restored} inventory rows.")
    print(f"  Zeroed out {zeroed} inventory rows that didn't exist before seeding.")


def run():
    print("=" * 70)
    print("Removing sample operational data")
    print("=" * 70)

    if not MANIFEST_PATH.exists():
        print(
            f"No manifest found at {MANIFEST_PATH} -- either sample data was "
            "never seeded, or it has already been removed. Nothing to do."
        )
        return

    manifest = json.loads(MANIFEST_PATH.read_text())
    pk_floor = manifest["pk_floor"]
    reorder_snapshot = {int(k): v for k, v in manifest["reorder_threshold_snapshot"].items()}
    inventory_snapshot = {int(k): v for k, v in manifest["inventory_snapshot"].items()}
    demo_usernames = manifest["demo_usernames"]

    with transaction.atomic():
        deleted_sales, _ = SalesTransaction.objects.filter(
            pk__gt=pk_floor["sales_transaction"]
        ).delete()
        print(f"  Deleted sales transactions (and their line items): {deleted_sales}")

        deleted_production, _ = ProductionRecord.objects.filter(
            pk__gt=pk_floor["production_record"]
        ).delete()
        print(f"  Deleted production records (and their ingredient usage): {deleted_production}")

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

        # Safe only now -- SalesTransaction.cashier and
        # ProductionRecord.commissary_staff are PROTECT foreign keys, so
        # this would have failed loudly instead of silently cascading
        # if either still referenced these accounts.
        deleted_users, _ = User.objects.filter(username__in=demo_usernames).delete()
        print(f"  Deleted demo accounts (and their PINs): {deleted_users}")

        _restore_reorder_thresholds(reorder_snapshot)
        _restore_inventory(inventory_snapshot)

    MANIFEST_PATH.unlink()
    print(f"\nRemoved manifest ({MANIFEST_PATH}).")
    print("\n" + "=" * 70)
    print("Sample operational data removed. The product catalog, branches,")
    print("and roles from seed_demo_data.py were left untouched -- they are")
    print("reference data, not sample data, and the real system needs them.")
    print("No database schema changes were made; this only touched rows in")
    print("tables that already existed.")
    print("=" * 70)


if __name__ == "__main__":
    run()
