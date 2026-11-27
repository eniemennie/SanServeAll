"""
Generates a large volume of synthetic SalesTransaction/SalesItem rows,
LOCAL DEV DATABASE ONLY, purely to load-test Reports Hub, Dashboard
Home, and Decision Support's data-handling capacity, accuracy,
efficiency, and responsiveness at a realistic order of magnitude.

Capstone panel revision (2026-11-27), Dr. Rowell M. Hernandez
(SanServeAll_Revision_Matrix.pdf): "The dataset should be expanded and
properly organized to provide sufficient records for testing the
system's data-handling capacity, and evaluating the accuracy,
efficiency, and responsiveness of the generated graphs and dashboards."

THIS IS NOT scripts/generate_demo_history.py. That script fills a real
historical gap with real, PERMANENT, quietly-labeled rows meant to be
seen on the LIVE PRODUCTION Reports Hub. This script is the opposite in
every respect:
  - Local dev database ONLY -- see _assert_local_dev_settings() below,
    which refuses to run against staging/production settings. The
    client's real production database must never receive this load-test
    volume: it is disposable test fixture data, not a real business
    record, and mixing the two would make it impossible to honestly
    separate "real" from "synthetic" ever again.
  - Counted in EVERY report/dashboard total -- Sales & Trend, Sales
    History, the Dashboard, Decision Support, all of it. Unlike
    DEMO_DATA_GENERATOR_USERNAME, there is no _exclude_*-style filtering
    anywhere for STRESS_TEST_GENERATOR_USERNAME (see
    apps.accounts.models.User's comment on that constant). Excluding it
    would defeat the purpose: the whole point is to measure those exact
    totals/charts/queries under realistic volume.
  - Meant to be temporary. Run scripts/remove_stress_test_data.py the
    moment benchmarking is done -- this is a load-test fixture, never a
    citable "real dataset," and must never be presented as one.

WHAT THIS CREATES, attributed to one dedicated identity
(apps.accounts.models.User.STRESS_TEST_GENERATOR_USERNAME):
  - SalesTransaction + SalesItem rows (exactly one item per transaction,
    a deliberate simplification -- see "Honest limitations" below),
    spread across every active Branch and a random active FINISHED_GOOD
    Product, at random business-hour timestamps across the requested
    date range.

WHAT THIS NEVER TOUCHES:
  - Inventory / InventoryTransaction, ProductionRecord, IngredientUsage.
    Same reasoning generate_demo_history.py already documents: going
    through apps.pos.services would deduct real local stock as a side
    effect of writing load-test fixture rows, and there is no "real"
    historical balance for a synthetic row to deduct from in the first
    place.
  - Any other model.

HONEST LIMITATIONS, stated here rather than only in conversation:
  - Exactly one SalesItem per SalesTransaction (a real sale often has
    2-3). This keeps bulk_create's two-pass (transactions, then their
    items) simple and fast at six-figure volume; it understates
    per-transaction item-level load slightly but has no effect on
    transaction-count-driven totals (revenue, units sold, transaction
    counts) that Reports Hub/Dashboard/Decision Support primarily
    report on.
  - Flat random distribution across branches/products/timestamps -- no
    attempt to model real seasonal demand, weekday/weekend skew, or
    per-branch popularity differences. This is a load test (can the
    system handle N rows efficiently and correctly), not a realism
    simulation.
  - transaction_discount_* fields and amount_tendered are left blank --
    irrelevant to the report totals/charts being benchmarked, and
    skipping them keeps row construction simple at this volume.

Removable: writes stress_test_data_manifest.json (a pk floor per model,
plus the dedicated username), so scripts/remove_stress_test_data.py can
undo precisely this run later -- independent of every other script's
own manifest in this directory.

Usage (from the backend/ directory, with the venv active -- LOCAL DEV
SETTINGS ONLY, this script refuses any other settings module):
    Dry run FIRST (default -- prints exactly what would be created,
    writes nothing):
        python manage.py shell --settings=config.settings.development \\
            -c "from scripts.generate_stress_test_data import run; run()"

    Only after reviewing that output, the real run (~270,000 rows by
    default -- takes a few minutes):
        python manage.py shell --settings=config.settings.development \\
            -c "from scripts.generate_stress_test_data import run; run(dry_run=False)"

    Smaller/custom run:
        python manage.py shell --settings=config.settings.development \\
            -c "from scripts.generate_stress_test_data import run; \\
                run(dry_run=False, total_transactions=50000)"
"""

import json
import os
import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import django

if not django.apps.apps.ready:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    django.setup()

from django.conf import settings  # noqa: E402
from django.db import transaction  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.accounts.models import Branch, Role, User  # noqa: E402
from apps.inventory.models import Product  # noqa: E402
from apps.pos.models import SalesItem, SalesTransaction  # noqa: E402

STRESS_USERNAME = User.STRESS_TEST_GENERATOR_USERNAME
MANIFEST_PATH = Path(__file__).resolve().parent / "stress_test_data_manifest.json"

DEFAULT_TOTAL_TRANSACTIONS = 270_000
DEFAULT_DAYS_BACK = 3 * 365  # roughly the real dataset's own ~3-year span
BATCH_SIZE = 5_000


LOCAL_SAFE_SETTINGS_MODULES = {
    "config.settings.development",
    # The automated test settings module is also local/disposable (its
    # own SQLite file, never staging/production) -- allowed so this
    # script's own test suite (scripts/tests/test_generate_stress_test_data.py)
    # can exercise the real run() path, including this guard itself,
    # without monkeypatching it out.
    "config.settings.test",
}


def _assert_local_dev_settings():
    """Hard safety guard: this script must never run against staging or
    production. Checked two independent ways, since either alone could
    be wrong in an unusual environment:
      1. DJANGO_SETTINGS_MODULE must be one of LOCAL_SAFE_SETTINGS_MODULES.
      2. The configured database file path must not contain "staging" or
         "production" (catches the case of a differently-named settings
         module still pointed at a non-dev database).
    Refuses loudly rather than silently proceeding if either check
    fails."""
    settings_module = os.environ.get("DJANGO_SETTINGS_MODULE", "")
    if settings_module not in LOCAL_SAFE_SETTINGS_MODULES:
        raise RuntimeError(
            f"Refusing to run: DJANGO_SETTINGS_MODULE is {settings_module!r}, "
            f"not one of {sorted(LOCAL_SAFE_SETTINGS_MODULES)}. This script "
            "generates a large synthetic load-test volume and must only ever "
            "run against a local/disposable database. Re-run with "
            "--settings=config.settings.development."
        )
    db_name = str(settings.DATABASES["default"].get("NAME", "")).lower()
    if "staging" in db_name or "production" in db_name:
        raise RuntimeError(
            f"Refusing to run: the configured database ({db_name!r}) looks "
            "like a staging/production database, not a local dev one. "
            "Stress-test data must never be written there."
        )


def ensure_stress_test_user():
    """A single dedicated identity every row this script creates is
    attributed to -- not scoped to one branch, never logs in (unusable
    password). See the STRESS_TEST_GENERATOR_USERNAME comment in
    apps.accounts.models.User."""
    role, _ = Role.objects.get_or_create(name=Role.OWNER_ADMIN)
    user, created = User.objects.get_or_create(
        username=STRESS_USERNAME,
        defaults={
            "first_name": "Stress Test Generator (not a real staff account -- "
            "local load-test fixture, see scripts/generate_stress_test_data.py)",
            "role": role,
            "branch": None,
            "is_active": True,
        },
    )
    if created:
        user.set_unusable_password()
        user.save()
        print(f"  Created dedicated stress-test identity: {STRESS_USERNAME}")
    else:
        print(f"  Using existing stress-test identity: {STRESS_USERNAME}")
    return user


def _eligible_products():
    products = list(
        Product.objects.filter(
            is_active=True, product_type=Product.ProductType.FINISHED_GOOD
        ).exclude(price__isnull=True)
    )
    if not products:
        raise RuntimeError(
            "No active FINISHED_GOOD products with a price found. Seed the "
            "catalog first (see scripts/seed_menu_catalog.py / "
            "scripts/seed_demo_data.py)."
        )
    return products


def _eligible_branches():
    """Active, customer-facing branches only. The commissary
    (Branch.is_commissary=True) is the production facility, not a POS
    sales location -- it never processes a real sale, so it must never
    be picked as a synthetic transaction's branch either."""
    branches = list(Branch.objects.filter(is_active=True, is_commissary=False))
    if not branches:
        raise RuntimeError(
            "No active, non-commissary branches found. Seed branches first "
            "(see scripts/seed_demo_data.py)."
        )
    return branches


def _random_timestamp(start_date: date, end_date: date):
    span_days = (end_date - start_date).days
    offset = random.randint(0, max(span_days, 0))
    day = start_date + timedelta(days=offset)
    hour = random.randint(7, 20)  # business hours
    minute = random.randint(0, 59)
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _pk_floor():
    """Highest existing pk per tracked model, recorded BEFORE generation
    -- everything this run creates has pk > this floor. Same convention
    as scripts/generate_demo_history.py's own manifest."""
    return {
        "sales_transaction": (
            SalesTransaction.objects.order_by("-pk").values_list("pk", flat=True).first() or 0
        ),
        "sales_item": (SalesItem.objects.order_by("-pk").values_list("pk", flat=True).first() or 0),
    }


def generate(total_transactions, start_date, end_date, stress_user, branches, products, dry_run):
    """Builds and bulk_creates SalesTransaction + matching SalesItem rows
    in BATCH_SIZE-sized chunks. Returns the number of transactions
    actually created (equal to total_transactions unless dry_run)."""
    if dry_run:
        print(f"[DRY RUN] Would create {total_transactions} SalesTransaction row(s)")
        print(f"[DRY RUN] Would create {total_transactions} SalesItem row(s) (1 per transaction)")
        print(f"[DRY RUN] Date range: {start_date} to {end_date}")
        print(f"[DRY RUN] Branches: {[b.name for b in branches]}")
        print(f"[DRY RUN] Eligible products: {len(products)}")
        return 0

    created_total = 0
    remaining = total_transactions
    while remaining > 0:
        batch_n = min(BATCH_SIZE, remaining)
        txn_objs = []
        for _ in range(batch_n):
            when = _random_timestamp(start_date, end_date)
            txn_objs.append(
                SalesTransaction(
                    branch=random.choice(branches),
                    cashier=stress_user,
                    status=SalesTransaction.Status.COMPLETED,
                    dining_option=random.choice(list(SalesTransaction.DiningOption.values)),
                    payment_method=random.choice(list(SalesTransaction.PaymentMethod.values)),
                    created_at=when,
                    updated_at=when,
                    completed_at=when,
                )
            )
        with transaction.atomic():
            created_txns = SalesTransaction.objects.bulk_create(txn_objs)
            if created_txns[0].pk is None:
                raise RuntimeError(
                    "bulk_create did not return populated primary keys on this "
                    "database backend -- aborting rather than silently creating "
                    "SalesItem rows with no transaction to attach to."
                )
            item_objs = []
            for txn in created_txns:
                product = random.choice(products)
                quantity = random.randint(1, 3)
                when = txn.completed_at
                item_objs.append(
                    SalesItem(
                        transaction=txn,
                        product=product,
                        unit_price=product.price,
                        quantity=quantity,
                        created_at=when,
                        updated_at=when,
                    )
                )
            SalesItem.objects.bulk_create(item_objs)
        created_total += batch_n
        remaining -= batch_n
        print(f"  ...{created_total}/{total_transactions} transactions created")
    return created_total


def _write_manifest(pk_floor, total_created):
    manifest = {
        "generated_at": timezone.now().isoformat(),
        "stress_test_username": STRESS_USERNAME,
        "pk_floor": pk_floor,
        "transactions_created": total_created,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    print(f"\nWrote removal manifest to {MANIFEST_PATH}.")
    print("Run scripts/remove_stress_test_data.py to undo precisely this run later.")


def run(dry_run=True, total_transactions=DEFAULT_TOTAL_TRANSACTIONS, days_back=DEFAULT_DAYS_BACK):
    print("=" * 70)
    mode = "DRY RUN -- no writes will be made" if dry_run else "LIVE RUN"
    print(f"Generating stress-test sales data ({mode})")
    print("=" * 70)

    _assert_local_dev_settings()

    end_date = timezone.localdate()
    start_date = end_date - timedelta(days=days_back)

    branches = _eligible_branches()
    products = _eligible_products()

    if dry_run:
        generate(total_transactions, start_date, end_date, None, branches, products, dry_run=True)
        print("\n" + "=" * 70)
        print("Dry run complete. No database changes were made.")
        print("Review the above, then call run(dry_run=False) to actually create these rows.")
        print("=" * 70)
        return

    if MANIFEST_PATH.exists():
        raise RuntimeError(
            f"{MANIFEST_PATH} already exists -- a previous stress-test run hasn't "
            "been removed yet. Run scripts/remove_stress_test_data.py first, then "
            "re-run this script."
        )

    real_sales_before = (
        SalesTransaction.objects.filter(status=SalesTransaction.Status.COMPLETED)
        .exclude(cashier__username=STRESS_USERNAME)
        .count()
    )

    pk_floor = _pk_floor()
    stress_user = ensure_stress_test_user()

    print(f"\nGenerating {total_transactions} synthetic sales transactions...")
    print(f"Date range: {start_date} to {end_date} ({days_back} days)")
    total_created = generate(
        total_transactions, start_date, end_date, stress_user, branches, products, dry_run=False
    )
    print(f"\n  Created {total_created} SalesTransaction + SalesItem row(s).")

    real_sales_after = (
        SalesTransaction.objects.filter(status=SalesTransaction.Status.COMPLETED)
        .exclude(cashier__username=STRESS_USERNAME)
        .count()
    )
    print("\nValidation (confirm no real data was touched):")
    print(f"  Real completed SalesTransaction count before: {real_sales_before}")
    print(f"  Real completed SalesTransaction count after:  {real_sales_after}")
    if real_sales_after != real_sales_before:
        print(
            "  !! MISMATCH -- a real count changed. Stop and investigate before "
            "running anything else; do not run remove_stress_test_data.py blindly."
        )
    else:
        print("  OK -- unchanged, exactly as expected.")

    _write_manifest(pk_floor, total_created)

    print("\n" + "=" * 70)
    print("Done. Reports Hub, Dashboard Home, and Decision Support now reflect")
    print(f"{total_created} additional synthetic transactions for benchmarking.")
    print("Run scripts/benchmark_report_performance.py to measure response times,")
    print("then scripts/remove_stress_test_data.py to clean this up afterward.")
    print("=" * 70)


if __name__ == "__main__":
    run()
