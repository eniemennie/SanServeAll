"""
READ-ONLY audit: distinguishes generated/sample/demo data from real
client/business data currently stored in a SanServeAll database.

Makes NO changes whatsoever -- every query below is a read. Safe to run
against any environment, including the live staging database, as many
times as needed.

Why this exists: before any cleanup, we need facts about what's actually
in the target database, not assumptions. This script enumerates what IS
there and sorts it into three buckets per the existing conventions already
built into this codebase:

  1. Known-sample data -- rows `seed_sample_operations_data.py` would have
     created, identified precisely via its own manifest file
     (scripts/sample_data_manifest.json), not by guessing from values. If
     that manifest is present, `scripts/remove_sample_operations_data.py`
     is the existing, already-tested, precise tool for removing exactly
     this bucket -- nothing here invents a new removal method.
  2. Known-reference/real data -- branches, roles, and the real menu
     catalog from seed_demo_data.py / seed_menu_catalog.py. Always
     preserved, never a cleanup candidate.
  3. Unrecognized -- anything that doesn't match the signatures of (1) or
     (2). Per instruction, this script never deletes or recommends
     deleting these; it only lists them for human review.

Usage (from the backend/ directory, with the venv active, against
whichever settings module points at the database you want to inspect):
    python manage.py shell --settings=config.settings.<env> -c "
import sys; sys.path.insert(0, '.')
from scripts.audit_sample_vs_client_data import run
run()
"

(Piping the file in via `shell < scripts/audit_sample_vs_client_data.py`
does not reliably trigger this script's own __main__ guard under the
Django version this project uses -- the -c form above is the one that's
been verified to work.)

Prints a structured, plain-text report to stdout. Nothing is written to
the database or filesystem.
"""

import json
import os
import sys

import django

if not django.apps.apps.ready:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    django.setup()

from django.conf import settings  # noqa: E402

from apps.accounts.models import Branch, User  # noqa: E402
from apps.forecasting.models import AIInsight, Forecast, InventoryRiskScore  # noqa: E402
from apps.inventory.models import Inventory, InventoryTransaction, Product  # noqa: E402
from apps.kahero_integration.models import KaheroImportBatch  # noqa: E402
from apps.pos.models import SalesItem, SalesTransaction  # noqa: E402
from apps.production.models import ProductionRecord  # noqa: E402

MANIFEST_PATH = os.path.join(os.path.dirname(__file__), "sample_data_manifest.json")

# Known-reference signatures, from the codebase's own seed scripts --
# never cleanup candidates.
KNOWN_BRANCH_CODES = {"BATANGAS", "ALANGILAN", "LIPA", "COMMISSARY"}
KNOWN_DEMO_USERNAMES = {"cashier_batangas", "cashier_lipa", "commissary_staff"}

# Signature of rows created by this session's import_branch_sales_history
# command (PR #86) -- custom_name values of the form "<Stream> (Imported)".
IMPORT_STREAM_SUFFIX = " (Imported)"

# Signature of the old, now-superseded PR #57 script, in case leftover
# rows from it exist anywhere (confirmed present in local dev; unknown on
# staging).
OLD_PR57_CUSTOM_NAME = "Historical Daily Sales (Imported)"

MANIFEST_MODELS = {
    "sales_transaction": SalesTransaction,
    "production_record": ProductionRecord,
    "inventory_transaction": InventoryTransaction,
    "forecast": Forecast,
    "inventory_risk_score": InventoryRiskScore,
    "ai_insight": AIInsight,
}


def line(char="-", n=72):
    print(char * n)


def section(title):
    print()
    line("=")
    print(title)
    line("=")


def run():
    db = settings.DATABASES["default"]
    print(f"Database engine: {db['ENGINE']}")
    print(f"Database name:   {db['NAME']}")

    # ------------------------------------------------------------------
    section("1. sample_data_manifest.json (seed_sample_operations_data.py)")
    # ------------------------------------------------------------------
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH) as f:
            manifest = json.load(f)
        print(f"FOUND at {MANIFEST_PATH}")
        print(
            "This means seed_sample_operations_data.py was run against THIS "
            "database at some point, and scripts/remove_sample_operations_data.py "
            "can be used to remove exactly what it created."
        )
        pk_floor = manifest.get("pk_floor", {})
        demo_usernames = manifest.get("demo_usernames", [])
        print(f"\nRecorded pk_floor per model: {pk_floor}")
        print(f"Recorded demo usernames: {demo_usernames}")

        print("\nPer-model counts (total vs. above-floor == sample-script candidates):")
        for key, model in MANIFEST_MODELS.items():
            floor = pk_floor.get(key)
            total = model.objects.count()
            if floor is None:
                print(f"  {model.__name__:20s} total={total:6d}  (no floor recorded for '{key}')")
                continue
            above = model.objects.filter(pk__gt=floor).count()
            below_or_eq = total - above
            print(
                f"  {model.__name__:20s} total={total:6d}  "
                f"pk<= {floor} (protected)={below_or_eq:6d}  "
                f"pk> {floor} (sample candidates)={above:6d}"
            )
    else:
        print(f"NOT FOUND at {MANIFEST_PATH}")
        print(
            "This means either:\n"
            "  (a) seed_sample_operations_data.py was never run against this "
            "database, OR\n"
            "  (b) it WAS run, and remove_sample_operations_data.py has "
            "already been used since (it deletes the manifest when it "
            "finishes).\n"
            "The per-model totals below are the only signal available in "
            "this case -- they do not by themselves prove sample data is "
            "present or absent."
        )
        print("\nRaw totals (no floor to compare against):")
        for key, model in MANIFEST_MODELS.items():
            print(f"  {model.__name__:20s} total={model.objects.count():6d}")

    # ------------------------------------------------------------------
    section("2. Branches")
    # ------------------------------------------------------------------
    for b in Branch.objects.all().order_by("name"):
        flag = "" if b.code in KNOWN_BRANCH_CODES else "  <-- UNRECOGNIZED CODE, review"
        print(
            f"  id={b.id:3d} code={b.code:12s} name={b.name:20s} "
            f"is_kahero={b.is_kahero_branch} is_commissary={b.is_commissary} "
            f"is_active={b.is_active}{flag}"
        )
    unknown_branches = Branch.objects.exclude(code__in=KNOWN_BRANCH_CODES)
    if unknown_branches.exists():
        print(
            f"\n  {unknown_branches.count()} branch(es) above don't match the "
            "known reference set (BATANGAS/ALANGILAN/LIPA/COMMISSARY) -- flagged "
            "for your review, not a deletion candidate."
        )

    # ------------------------------------------------------------------
    section("3. User accounts")
    # ------------------------------------------------------------------
    print(f"Total users: {User.objects.count()}\n")
    for u in User.objects.all().order_by("date_joined"):
        is_known_demo = u.username in KNOWN_DEMO_USERNAMES
        tag = "KNOWN DEMO (from seed_sample_operations_data.py)" if is_known_demo else ""
        print(
            f"  username={u.username:22s} active={u.is_active!s:5s} "
            f"role={(u.role.name if u.role else '-'):18s} "
            f"branch={(u.branch.code if u.branch else '-'):10s} "
            f"usable_password={u.has_usable_password()!s:5s} "
            f"joined={u.date_joined.date()} last_login={u.last_login} {tag}"
        )
    unknown_users = User.objects.exclude(username__in=KNOWN_DEMO_USERNAMES).exclude(
        is_superuser=True
    )
    print(
        f"\n  {unknown_users.count()} non-superuser account(s) above are not one of "
        "the known seed-script demo usernames. Most are presumably real staff "
        "accounts -- listed above for you to confirm, nothing here assumes "
        "they're sample data."
    )

    # ------------------------------------------------------------------
    section("4. Products (catalog)")
    # ------------------------------------------------------------------
    print(f"Total products: {Product.objects.count()}")
    print(
        "Catalog rows are configuration, not business-activity data -- "
        "listed only for completeness, not treated as a cleanup candidate "
        "by this script. Review manually against the real menu if needed."
    )

    # ------------------------------------------------------------------
    section("5. Real client sales-history import signature (import_branch_sales_history)")
    # ------------------------------------------------------------------
    imported_items = SalesItem.objects.filter(custom_name__iendswith=IMPORT_STREAM_SUFFIX).exclude(
        custom_name=OLD_PR57_CUSTOM_NAME
    )
    by_stream = imported_items.values("custom_name").order_by("custom_name").distinct()
    print(
        f"SalesItem rows matching the real-import pattern ('<Stream> (Imported)'): "
        f"{imported_items.count()}"
    )
    if imported_items.exists():
        for row in by_stream:
            n = imported_items.filter(custom_name=row["custom_name"]).count()
            print(f"  {row['custom_name']:40s} count={n}")
        print(
            "\nIf this count is 0, the real client sales-history import "
            "(PR #86) has NOT yet been run against this database."
        )
    else:
        print("None found -- the real client sales-history import has NOT been run here yet.")

    # ------------------------------------------------------------------
    section("6. Leftover old PR #57 script data (superseded, closed)")
    # ------------------------------------------------------------------
    old_items = SalesItem.objects.filter(custom_name=OLD_PR57_CUSTOM_NAME)
    print(f"SalesItem rows with custom_name='{OLD_PR57_CUSTOM_NAME}': {old_items.count()}")
    if old_items.exists():
        print(
            "These came from testing the OLD, now-closed/superseded PR #57 "
            "script -- confirmed present in local dev. If present here too, "
            "they are a real generated-data cleanup candidate (not real "
            "client data), but flagged here for your review rather than "
            "auto-removed."
        )

    # ------------------------------------------------------------------
    section("7. KaHero import batches")
    # ------------------------------------------------------------------
    print(f"Total KaheroImportBatch rows: {KaheroImportBatch.objects.count()}")

    # ------------------------------------------------------------------
    section("8. Inventory snapshot")
    # ------------------------------------------------------------------
    print(f"Total Inventory rows: {Inventory.objects.count()}")

    print()
    line("=")
    print("End of audit. No data was modified.")
    line("=")


if __name__ == "__main__":
    run()
