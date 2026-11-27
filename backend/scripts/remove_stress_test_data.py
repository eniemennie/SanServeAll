"""
Removes everything scripts/generate_stress_test_data.py added --
restoring the local dev database to its exact pre-generation state.
Entirely independent of scripts/remove_demo_history.py and its own
manifest; that script undoes a different, permanent, production-bound
generator, this one undoes only the local-only stress-test load.

How it stays precise: reads scripts/stress_test_data_manifest.json,
written by generate_stress_test_data.py the moment it finishes, which
records a primary-key floor for every model it only ever ADDS rows to
(SalesTransaction, SalesItem) and the dedicated stress-test username
every row was attributed to.

Two independent ways to identify the rows to remove, cross-checked by
COUNT rather than by collecting every individual pk into memory (this
run can be a quarter-million rows -- a Python set of that many pks, or
a single SQL `IN (...)` clause that large, is exactly the kind of thing
this script exists to avoid doing):
  1. COUNT of SalesTransaction attributed to
     User.STRESS_TEST_GENERATOR_USERNAME.
  2. COUNT of SalesTransaction with pk > the recorded floor.
Both should be the same number -- if they disagree, something
unexpected happened between the generation run and this cleanup (e.g.
unrelated activity against this exact account, which should never
happen since it is never used for anything else), so this script
reports the mismatch rather than silently picking one.

Does NOT touch Inventory, InventoryTransaction, ProductionRecord, or
IngredientUsage: generate_stress_test_data.py never touches them in the
first place.

SalesItem rows are deleted automatically via their CASCADE foreign key
to SalesTransaction -- not tracked or deleted independently.

Deletion itself filters on BOTH conditions at once
(pk__gt=floor AND cashier__username=username) in a single DB-side
query, so it scales to six-figure row counts without ever materializing
a pk list in Python.

Usage (from the backend/ directory, with the venv active):
    python manage.py shell --settings=config.settings.development \\
        -c "from scripts.remove_stress_test_data import run; run()"

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

MANIFEST_PATH = Path(__file__).resolve().parent / "stress_test_data_manifest.json"


def run():
    print("=" * 70)
    print("Removing generated stress-test sales data")
    print("=" * 70)

    if not MANIFEST_PATH.exists():
        print(
            f"No manifest found at {MANIFEST_PATH} -- either generate_stress_test_data "
            "was never run, or it has already been removed. Nothing to do."
        )
        return

    manifest = json.loads(MANIFEST_PATH.read_text())
    pk_floor = manifest["pk_floor"]["sales_transaction"]
    stress_username = manifest["stress_test_username"]

    count_by_account = SalesTransaction.objects.filter(cashier__username=stress_username).count()
    count_by_floor = SalesTransaction.objects.filter(pk__gt=pk_floor).count()

    if count_by_account != count_by_floor:
        print(
            "!! The account and pk-floor methods of identifying stress-test "
            "SalesTransaction rows disagree."
        )
        print(f"   By account: {count_by_account} row(s)")
        print(f"   By pk floor: {count_by_floor} row(s)")
        print("   Stopping without deleting anything. Investigate before re-running this script.")
        return

    print(
        f"\nFound {count_by_account} stress-test SalesTransaction row(s) to remove "
        "(both checks agree)."
    )

    with transaction.atomic():
        deleted_sales, _ = SalesTransaction.objects.filter(
            pk__gt=pk_floor, cashier__username=stress_username
        ).delete()
        print(f"  Deleted stress-test sales transactions (and their line items): {deleted_sales}")

        # Safe only now -- SalesTransaction.cashier is a PROTECT foreign
        # key, so this would fail loudly instead of silently cascading
        # if the stress-test account still had any row referencing it.
        deleted_users, _ = User.objects.filter(username=stress_username).delete()
        print(f"  Deleted the stress-test generator account: {deleted_users}")

    MANIFEST_PATH.unlink()
    print(f"\nRemoved manifest ({MANIFEST_PATH}).")
    print("\n" + "=" * 70)
    print("Stress-test data removed. Real sales data was never altered by either")
    print("the generation or this removal -- only rows this script's own manifest")
    print("tracked were touched.")
    print("=" * 70)


if __name__ == "__main__":
    run()
