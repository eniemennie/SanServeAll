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

# A richer starter catalog matching the UI reference's menu (Row 3
# redesign) -- category assignments are my own reasonable best-effort
# read of the menu, since the reference screenshots only show the "All"
# tab view, not which tab each item belongs to. Worth double-checking
# against the real menu and adjusting via Django admin if any are wrong.
PRODUCTS = [
    # Coffee
    {"name": "Americano", "price": "75.00", "category": "COFFEE"},
    {"name": "Cafe Latte", "price": "85.00", "category": "COFFEE"},
    {"name": "Spanish Latte", "price": "95.00", "category": "COFFEE", "is_best_seller": True},
    {"name": "Mocha Latte", "price": "95.00", "category": "COFFEE"},
    {"name": "Cappuccino", "price": "115.00", "category": "COFFEE"},
    {"name": "Caramel Macchiato", "price": "105.00", "category": "COFFEE"},
    {"name": "Salted Caramel Macchiato", "price": "110.00", "category": "COFFEE"},
    # Sea Salt Series
    {"name": "Sea Salt Latte", "price": "105.00", "category": "SEA_SALT_SERIES"},
    {
        "name": "Spanish Sea Salt Latte",
        "price": "115.00",
        "category": "SEA_SALT_SERIES",
        "is_best_seller": True,
    },
    {"name": "Matcha Sea Salt Latte", "price": "115.00", "category": "SEA_SALT_SERIES"},
    {"name": "Biscoff Sea Salt Latte", "price": "120.00", "category": "SEA_SALT_SERIES"},
    {"name": "Biscoff Latte", "price": "115.00", "category": "SEA_SALT_SERIES"},
    # Coffee Frappe
    {"name": "Caramel Macchiato Frappe", "price": "115.00", "category": "COFFEE_FRAPPE"},
    {
        "name": "Java Chip Frappe",
        "price": "115.00",
        "category": "COFFEE_FRAPPE",
        "is_best_seller": True,
    },
    {"name": "Mocha Frappe", "price": "110.00", "category": "COFFEE_FRAPPE"},
    {"name": "Cookies & Cream Frappe", "price": "115.00", "category": "COFFEE_FRAPPE"},
    {"name": "Chocolate Frappe", "price": "105.00", "category": "COFFEE_FRAPPE"},
    # Non-Coffee Frappe
    {"name": "Strawberry Cream Frappe", "price": "110.00", "category": "NON_COFFEE_FRAPPE"},
    {"name": "Matcha Frappe", "price": "115.00", "category": "NON_COFFEE_FRAPPE"},
    {"name": "Double Dutch Frappe", "price": "110.00", "category": "NON_COFFEE_FRAPPE"},
    # Specialty
    {"name": "Matcha Latte", "price": "105.00", "category": "SPECIALTY"},
    # Breakfast
    {"name": "Sans Rival Breakfast Plate", "price": "220.00", "category": "BREAKFAST"},
    # Pasta
    {"name": "Carbonara", "price": "185.00", "category": "PASTA"},
    {"name": "Chicken Pesto Pasta", "price": "195.00", "category": "PASTA"},
    {"name": "Lasagna", "price": "145.00", "category": "PASTA"},
    # Desserts
    {"name": "Ensaymada", "price": "45.00", "category": "DESSERTS"},
    # Cakes
    {"name": "Sans Rival Slice", "price": "150.00", "category": "CAKES"},
    {"name": "Chocolate Cake Slice", "price": "140.00", "category": "CAKES"},
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
            name=data["name"],
            defaults={
                "price": data["price"],
                "category": data.get("category", ""),
                "is_best_seller": data.get("is_best_seller", False),
            },
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
