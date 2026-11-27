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

Deletion happens in DELETE_BATCH_SIZE-row chunks, NOT as one single
queryset-wide .delete() call, even though that looks like it should be
safe for a simple two-condition filter (pk__gt=floor AND cashier_id=
stress_user_id). The real reason: apps/inventory/models.py's
InventoryTransaction.related_sales_item is a nullable FK to
pos.SalesItem with on_delete=SET_NULL. Every SalesTransaction this
deletes cascades to delete its SalesItem (CASCADE), and for every
batch of SalesItem rows Django's own deletion collector internally
discovers along the way, it queues up a separate "set
related_sales_item to NULL" update -- then, when the single
queryset-wide .delete() actually executes, Django ORs every one of
those per-internal-batch update clauses together into ONE combined SQL
UPDATE statement before running it (see django/db/models/deletion.py,
Collector.delete(), the `field_updates` / `combined_updates` handling).
That one statement's bind-variable count scales with the TOTAL number
of rows being deleted, not any one batch's size -- Django's own
internal chunking (GET_ITERATOR_CHUNK_SIZE, bulk_batch_size) does
nothing to prevent this, because it happens one level up, combining
the batches back together. Confirmed live: deleting all 270,000
stress-test rows in one .delete() call raised
"sqlite3.OperationalError: too many SQL variables" (2026-11-27, panel
defense dry run). Chunking the delete call ITSELF, not just relying on
Django's internal batching, is what actually keeps each combined
UPDATE's variable count bounded -- which is why this loops over small
slices instead.

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

# See the "too many SQL variables" explanation in the module docstring
# above -- this bounds the bind-variable count of the combined SET-NULL
# update Django builds across however many rows get deleted, by bounding
# how many rows a single .delete() call is ever asked to cover at once.
DELETE_BATCH_SIZE = 1_000


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

    # Resolved once, up front, so the per-batch deletion loop below can
    # filter on the plain indexed cashier_id column rather than
    # re-joining to accounts_user by username on every iteration.
    stress_user = User.objects.filter(username=stress_username).first()
    stress_user_id = stress_user.pk if stress_user is not None else None

    count_by_account = (
        SalesTransaction.objects.filter(cashier_id=stress_user_id).count()
        if stress_user_id is not None
        else 0
    )
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
        deleted_sales = 0
        while True:
            # DELETE_BATCH_SIZE at a time, by pk, rather than one
            # queryset-wide .delete() -- see the module docstring and
            # DELETE_BATCH_SIZE's own comment for why that one single
            # call can crash SQLite at real stress-test volume.
            batch_pks = list(
                SalesTransaction.objects.filter(pk__gt=pk_floor, cashier_id=stress_user_id)
                .order_by("pk")
                .values_list("pk", flat=True)[:DELETE_BATCH_SIZE]
            )
            if not batch_pks:
                break
            batch_deleted, _ = SalesTransaction.objects.filter(pk__in=batch_pks).delete()
            deleted_sales += batch_deleted
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
