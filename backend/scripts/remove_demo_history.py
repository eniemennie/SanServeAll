"""
Removes everything scripts/generate_demo_history.py added -- restoring
the database to its exact pre-generation state. Entirely independent of
scripts/remove_synthetic_backfill.py and its own manifest; that script
undoes the forecast-coverage bot's synthetic sales, this one undoes only
this script's demo production/sales history.

How it stays precise: reads scripts/demo_history_manifest.json, written
by generate_demo_history.py the moment it finishes, which records a
primary-key floor for every model it only ever ADDS rows to
(ProductionRecord, IngredientUsage, SalesTransaction, SalesItem) and the
dedicated demo username every row was attributed to.

Two independent ways to find the rows to remove, cross-checked:
  1. Every ProductionRecord/SalesTransaction attributed to
     User.DEMO_DATA_GENERATOR_USERNAME.
  2. Every row of each tracked model with pk > that model's recorded
     floor.
Both should identify exactly the same rows -- if they ever disagree,
something unexpected happened between the generation run and this
cleanup (e.g. unrelated real activity against this exact account, which
should never happen since it's never used for anything else), so this
script reports the mismatch rather than silently picking one.

Does NOT touch Inventory or InventoryTransaction: generate_demo_history.py
never touches them in the first place (see that script's own docstring
for why -- its rows represent a period before real stock tracking
existed), so there is nothing to restore here.

IngredientUsage and SalesItem are deleted automatically via their
CASCADE foreign keys to ProductionRecord/SalesTransaction -- not tracked
or deleted independently.

Usage (from the backend/ directory, with the venv active):
    python manage.py shell --settings=config.settings.production \\
        -c "from scripts.remove_demo_history import run; run()"

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
from apps.pos.models import SalesTransaction  # noqa: E402
from apps.production.models import ProductionRecord  # noqa: E402

MANIFEST_PATH = Path(__file__).resolve().parent / "demo_history_manifest.json"


def run():
    print("=" * 70)
    print("Removing generated demo production/sales history")
    print("=" * 70)

    if not MANIFEST_PATH.exists():
        print(
            f"No manifest found at {MANIFEST_PATH} -- either generate_demo_history "
            "was never run, or it has already been removed. Nothing to do."
        )
        return

    manifest = json.loads(MANIFEST_PATH.read_text())
    pk_floor = manifest["pk_floor"]
    demo_username = manifest["demo_username"]

    production_by_account = set(
        ProductionRecord.objects.filter(commissary_staff__username=demo_username).values_list(
            "pk", flat=True
        )
    )
    production_by_floor = set(
        ProductionRecord.objects.filter(pk__gt=pk_floor["production_record"]).values_list(
            "pk", flat=True
        )
    )
    sales_by_account = set(
        SalesTransaction.objects.filter(cashier__username=demo_username).values_list(
            "pk", flat=True
        )
    )
    sales_by_floor = set(
        SalesTransaction.objects.filter(pk__gt=pk_floor["sales_transaction"]).values_list(
            "pk", flat=True
        )
    )

    mismatch = False
    if production_by_account != production_by_floor:
        mismatch = True
        print(
            "!! The account and pk-floor methods of identifying demo ProductionRecord "
            "rows disagree."
        )
        print(f"   By account only: {sorted(production_by_account - production_by_floor)}")
        print(f"   By pk floor only: {sorted(production_by_floor - production_by_account)}")
    if sales_by_account != sales_by_floor:
        mismatch = True
        print(
            "!! The account and pk-floor methods of identifying demo SalesTransaction "
            "rows disagree."
        )
        print(f"   By account only: {sorted(sales_by_account - sales_by_floor)}")
        print(f"   By pk floor only: {sorted(sales_by_floor - sales_by_account)}")
    if mismatch:
        print("   Stopping without deleting anything. Investigate before re-running this script.")
        return

    print(
        f"\nFound {len(production_by_account)} demo ProductionRecord row(s) and "
        f"{len(sales_by_account)} demo SalesTransaction row(s) to remove (both checks agree)."
    )

    with transaction.atomic():
        deleted_sales, _ = SalesTransaction.objects.filter(pk__in=sales_by_account).delete()
        print(f"  Deleted demo sales transactions (and their line items): {deleted_sales}")

        deleted_production, _ = ProductionRecord.objects.filter(
            pk__in=production_by_account
        ).delete()
        print(
            "  Deleted demo production records (and their ingredient usage rows): "
            f"{deleted_production}"
        )

        # Safe only now -- ProductionRecord.commissary_staff and
        # SalesTransaction.cashier are both PROTECT foreign keys, so this
        # would fail loudly instead of silently cascading if the demo
        # account still had any row referencing it.
        deleted_users, _ = User.objects.filter(username=demo_username).delete()
        print(f"  Deleted the demo-data generator account: {deleted_users}")

    MANIFEST_PATH.unlink()
    print(f"\nRemoved manifest ({MANIFEST_PATH}).")
    print("\n" + "=" * 70)
    print("Demo history removed. Real production/sales data and inventory levels")
    print("were never altered by either the generation or this removal -- only")
    print("rows this script's own manifest tracked were touched.")
    print("=" * 70)


if __name__ == "__main__":
    run()
