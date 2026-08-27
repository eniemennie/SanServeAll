"""
Seed script for SanServeAll local dev/demo (Row 11).

Populates the 3 real branches and 3 roles confirmed in Phase 1/2 planning.
Safe to run multiple times -- uses get_or_create so re-running never
duplicates records, only reports what already existed.

Usage (from the backend/ directory, with the venv active):
    python manage.py shell < scripts/seed_demo_data.py

Or import and call run() directly from a management shell:
    python manage.py shell
    >>> from scripts.seed_demo_data import run
    >>> run()
"""

import os
import sys
import django

# Allow running as a standalone script via `python scripts/seed_demo_data.py`
# in addition to `manage.py shell < ...` -- sets up Django if not already
# configured.
if not django.apps.apps.ready:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    django.setup()

from apps.accounts.models import Branch, Role  # noqa: E402
from apps.inventory.models import Product  # noqa: E402

# Confirmed branch configuration (Phase 2 decision, resolved client
# contradiction): Alangilan runs KaHero POS / batch-import mode.
# Batangas City and Lipa City run the native SanServeAll POS, real-time.
BRANCHES = [
    {"name": "Batangas City", "code": "BATANGAS", "is_kahero_branch": False},
    {"name": "Alangilan", "code": "ALANGILAN", "is_kahero_branch": True},
    {"name": "Lipa City", "code": "LIPA", "is_kahero_branch": False},
]

# The commissary (Phase 1 SS1.1) is modeled as a Branch row too (Week 8
# decision -- reuses all existing branch-scoping infrastructure) but is
# tracked separately from the 3 customer-facing branches above, since it
# has its own dedicated flag rather than a fourth is_kahero_branch-style
# entry in that list.
COMMISSARY = {"name": "Commissary", "code": "COMMISSARY", "is_commissary": True}

ROLES = [
    {
        "name": Role.OWNER_ADMIN,
        "description": (
            "Full access: analytics, forecasting, inventory oversight, "
            "user management, system config."
        ),
    },
    {
        "name": Role.BRANCH_STAFF,
        "description": "Branch-scoped POS operation, cashier PIN-authenticated, inventory updates.",
    },
    {
        "name": Role.COMMISSARY_STAFF,
        "description": "Production output, ingredient usage, supply distribution to branches.",
    },
]

# A minimal starter catalog so the POS Ordering Screen (Week 4) has real
# products to test against ahead of the full Inventory module (Week 6).
# Categories match the manuscript's own description of Jorge's Café menu
# (Phase 1 SS1.1: sweets, cakes, pastries, all-day meals, pasta, beverages).
PRODUCTS = [
    {"name": "Sans Rival Slice", "price": "150.00"},
    {"name": "Chocolate Cake Slice", "price": "140.00"},
    {"name": "Ensaymada", "price": "45.00"},
    {"name": "Spanish Latte", "price": "125.00"},
    {"name": "Cappuccino", "price": "115.00"},
    {"name": "Carbonara", "price": "185.00"},
    {"name": "Chicken Pesto Pasta", "price": "195.00"},
    {"name": "Sans Rival Breakfast Plate", "price": "220.00"},
]

# Starter raw materials (Week 8) so Production has something real to
# consume -- matches the manuscript's own ingredient examples (Phase 1
# SS1.1: flour, sugar, butter, eggs, milk, flavorings).
MATERIALS = [
    {"name": "Flour (kg)", "price": "55.00"},
    {"name": "Sugar (kg)", "price": "60.00"},
    {"name": "Butter (kg)", "price": "320.00"},
    {"name": "Eggs (tray)", "price": "210.00"},
    {"name": "Milk (liter)", "price": "95.00"},
]


def run():
    print("Seeding branches...")
    for data in BRANCHES:
        branch, created = Branch.objects.get_or_create(
            code=data["code"],
            defaults={"name": data["name"], "is_kahero_branch": data["is_kahero_branch"]},
        )
        status = "created" if created else "already exists"
        print(
            f"  [{status}] {branch.name} (code={branch.code}, "
            f"is_kahero_branch={branch.is_kahero_branch})"
        )

    print("\nSeeding commissary...")
    commissary, created = Branch.objects.get_or_create(
        code=COMMISSARY["code"], defaults={"name": COMMISSARY["name"], "is_commissary": True}
    )
    status = "created" if created else "already exists"
    print(f"  [{status}] {commissary.name} (code={commissary.code})")

    print("\nSeeding roles...")
    for data in ROLES:
        role, created = Role.objects.get_or_create(
            name=data["name"], defaults={"description": data["description"]}
        )
        status = "created" if created else "already exists"
        print(f"  [{status}] {role.get_name_display()}")

    print("\nSeeding starter product catalog...")
    for data in PRODUCTS:
        product, created = Product.objects.get_or_create(
            name=data["name"], defaults={"price": data["price"]}
        )
        status = "created" if created else "already exists"
        print(f"  [{status}] {product.name} (Php{product.price})")

    print("\nSeeding starter raw materials...")
    for data in MATERIALS:
        material, created = Product.objects.get_or_create(
            name=data["name"],
            defaults={"price": data["price"], "product_type": Product.ProductType.MATERIAL},
        )
        status = "created" if created else "already exists"
        print(f"  [{status}] {material.name} (Php{material.price})")

    kahero_count = Branch.objects.filter(is_kahero_branch=True).count()
    assert kahero_count == 1, (
        f"Expected exactly 1 KaHero branch, found {kahero_count}. "
        "Check for duplicate or misconfigured Branch rows before proceeding."
    )
    print(
        f"\nVerified: exactly 1 branch flagged is_kahero_branch=True "
        f"({Branch.objects.get(is_kahero_branch=True).name})."
    )
    print("Seed complete.")


if __name__ == "__main__":
    run()
