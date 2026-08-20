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

# Confirmed branch configuration (Phase 2 decision, resolved client
# contradiction): Alangilan runs KaHero POS / batch-import mode.
# Batangas City and Lipa City run the native SanServeAll POS, real-time.
BRANCHES = [
    {"name": "Batangas City", "code": "BATANGAS", "is_kahero_branch": False},
    {"name": "Alangilan", "code": "ALANGILAN", "is_kahero_branch": True},
    {"name": "Lipa City", "code": "LIPA", "is_kahero_branch": False},
]

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

    print("\nSeeding roles...")
    for data in ROLES:
        role, created = Role.objects.get_or_create(
            name=data["name"], defaults={"description": data["description"]}
        )
        status = "created" if created else "already exists"
        print(f"  [{status}] {role.get_name_display()}")

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
