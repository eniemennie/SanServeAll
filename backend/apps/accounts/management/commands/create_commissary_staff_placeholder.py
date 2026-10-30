"""
Establishes a real, functional COMMISSARY_STAFF account -- without any
fictional human name and without being a "test" account -- the same
principle already applied to create_branch_cashier_placeholders for the
branch cashiers.

Why this exists: as of this command's creation, staging has zero
COMMISSARY_STAFF accounts at all. The one that existed, `commissary_staff`
(password "Jorge2026!"), was a demo/seed account created by
scripts/seed_sample_operations_data.py and intentionally removed during
the sample-data cleanup earlier in this project. Unlike the branch
cashiers (whose live screen literally reads "No cashiers are currently
available"), this gap is quieter -- Commissary Staff logs in via an
ordinary username/password form (CommissaryLoginView), so there's no
empty-state screen advertising the problem -- but it's the same real gap:
there is currently no way to log in as Commissary Staff at all, which
blocks Production Recording, Batch Management, and Stock Transfers
dispatch (all of which Commissary Staff -- not just Owner/Admin -- are
meant to use day to day).

Design, mirroring create_branch_cashier_placeholders:
  - username: "commissary_staff" -- the role's own name, not a person's
    name, and not labeled "test".
  - first_name / last_name: left blank, on purpose, same reasoning as the
    cashier placeholders -- nowhere in the UI should a fabricated name
    appear. (The old demo account set first_name="Commissary Staff",
    which this deliberately does NOT repeat.)
  - branch: the commissary Branch (Commissary Staff's natural scope).
  - a real, usable password -- unlike the cashier accounts, Commissary
    Staff authenticates via username+password (CommissaryLoginView), not
    a PIN, and that view has no brute-force lockout protection, so a
    trivial, guessable value like the cashier PIN's "0000" would be a
    real exposure here. PLACEHOLDER_PASSWORD below is still a
    documented, known value (not a secret) for the same reason the
    cashier PIN is: there is no real person yet to privately receive it.

Idempotent: skips (reports, not silently) if an ACTIVE COMMISSARY_STAFF
account already exists -- same is_active-scoped check as
create_branch_cashier_placeholders, and for the same reason: a branch
historical-import placeholder account should never be able to pattern-
match here, and neither should any other inactive leftover.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.accounts.models import Branch, Role

# Deliberately documented, not secret: there is no real commissary staff
# member yet to privately receive this. Change it via the admin or the
# account's own password-change flow once a real person is assigned.
PLACEHOLDER_PASSWORD = "CommissaryPending2026!"


class Command(BaseCommand):
    help = (
        "Creates one real, functional COMMISSARY_STAFF account (no fictional name, "
        "no test account) if none is currently active, ready for the client to "
        "attach real staff details to later."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be created/skipped without changing the database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        User = get_user_model()

        commissary_role, _ = Role.objects.get_or_create(
            name=Role.COMMISSARY_STAFF, defaults={"description": "Commissary Staff"}
        )

        commissary_branch = Branch.objects.filter(is_commissary=True, is_active=True).first()
        if commissary_branch is None:
            self.stdout.write(
                self.style.ERROR(
                    "No active commissary Branch (is_commissary=True) found. Nothing to do."
                )
            )
            return

        already_staffed = User.objects.filter(role=commissary_role, is_active=True).exists()
        if already_staffed:
            self.stdout.write(
                self.style.WARNING("Skipped: an active COMMISSARY_STAFF account already exists.")
            )
            return

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(f"Would create: commissary_staff -> {commissary_branch.name}")
            )
            return

        user = User.objects.create(
            username="commissary_staff",
            first_name="",
            last_name="",
            role=commissary_role,
            branch=commissary_branch,
            is_active=True,
        )
        user.set_password(PLACEHOLDER_PASSWORD)
        user.save()

        self.stdout.write(
            self.style.SUCCESS(f"Created: commissary_staff -> {commissary_branch.name}")
        )
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                f"Password for the new account is '{PLACEHOLDER_PASSWORD}'. This account has "
                "no real name attached yet. Once the client confirms real staff details, "
                "update the account's first_name/last_name and change its password -- do not "
                "leave the placeholder password in place."
            )
        )
