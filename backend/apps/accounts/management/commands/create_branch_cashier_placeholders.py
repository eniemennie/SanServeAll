"""
Establishes the BRANCH_STAFF cashier account STRUCTURE for every branch
that actually needs one on the live "Select Cashier" (tap-to-select)
screen -- without inventing any fictional human name, and without
creating a "test" account.

Why this exists: as of this command's creation, staging has zero
BRANCH_STAFF users at all (TEST/test_cashier/lab_test_cashier were all
removed as part of the sample/test data cleanup), so the live Select
Cashier screen correctly shows "No cashiers are currently available."
The client has not yet confirmed the real names of the cashiers who will
staff each branch. Rather than inventing placeholder names (which would
violate the "no fictional names" instruction) or leaving the screen
broken until that information arrives, this command creates one real,
functional account per branch with:

  - username: an honest, branch-derived placeholder (e.g.
    "batangas_cashier") -- not a person's name, and not labeled "test"
    -- that doubles as a clear marker for the owner/admin that it still
    needs a real person's details attached.
  - first_name / last_name: left blank, on purpose. Both
    templates/accounts/select_cashier.html and
    templates/accounts/cashier_lock.html render
    `{{ cashier.get_full_name|default:cashier.username }}`, so a blank
    name falls back to showing the username honestly, rather than any
    fabricated display name.
  - an unusable password: these accounts are only ever meant to be used
    through the tap-to-select + PIN flow (CashierLockView), never through
    the username/password login form, so a normal password is not set.
  - a placeholder CashierPIN, deliberately set to PLACEHOLDER_PIN below.
    This is NOT a secret -- it is a known, documented value precisely
    because there is no real cashier yet to privately receive one. The
    owner/admin must change it (via the existing PIN-change flow) once a
    real person is assigned to the account.

Which branches get an account, and why:
  - Every Branch with is_active=True, is_commissary=False, and
    is_kahero_branch=False gets one. That condition is the same one the
    codebase already treats as the single source of truth for "which
    branches run the native real-time POS" (see Branch.is_kahero_branch's
    own docstring) -- so this naturally includes Batangas and Lipa and
    naturally excludes Alangilan (KaHero batch-import branch, no
    SanServeAll cashier login needed) and the commissary (staffed by
    COMMISSARY_STAFF, not BRANCH_STAFF), with no hardcoded branch list to
    keep in sync by hand if a branch is ever added, renamed, or retired.

Idempotent: re-running this command does not create duplicates or
overwrite an account that has already been given real details. A branch
is skipped (reported, not silently ignored) if it already has at least
one BRANCH_STAFF user assigned to it -- covering both "already handled by
this command" and "the client's real account already exists" -- so this
command only ever fills a genuine gap.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Branch, CashierPIN, Role

# Deliberately documented, not secret: there is no real cashier yet to
# privately receive a PIN. Change this via the app's own PIN-change flow
# once a real person is assigned to the account.
PLACEHOLDER_PIN = "0000"


class Command(BaseCommand):
    help = (
        "Creates one placeholder BRANCH_STAFF cashier account per native-POS "
        "branch (no fictional names, no test accounts), ready for the client "
        "to attach real staff details to later."
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

        branch_staff_role, _ = Role.objects.get_or_create(
            name=Role.BRANCH_STAFF, defaults={"description": "Branch Staff"}
        )

        branches = Branch.objects.filter(
            is_active=True, is_commissary=False, is_kahero_branch=False
        ).order_by("name")

        if not branches.exists():
            self.stdout.write(
                self.style.WARNING(
                    "No branches matched (active, non-commissary, non-KaHero). Nothing to do."
                )
            )
            return

        created, skipped = [], []

        with transaction.atomic():
            for branch in branches:
                already_staffed = User.objects.filter(
                    role=branch_staff_role, branch=branch
                ).exists()
                if already_staffed:
                    skipped.append(branch)
                    continue

                username = f"{branch.code.lower()}_cashier"

                if dry_run:
                    created.append((branch, username))
                    continue

                user = User.objects.create(
                    username=username,
                    first_name="",
                    last_name="",
                    role=branch_staff_role,
                    branch=branch,
                    is_active=True,
                )
                user.set_unusable_password()
                user.save()

                pin_record = CashierPIN(user=user, is_active=True)
                pin_record.set_pin(PLACEHOLDER_PIN)
                pin_record.save()

                created.append((branch, username))

            if dry_run:
                transaction.set_rollback(True)

        for branch, username in created:
            verb = "Would create" if dry_run else "Created"
            self.stdout.write(self.style.SUCCESS(f"{verb}: {username} -> {branch.name}"))

        for branch in skipped:
            self.stdout.write(
                self.style.WARNING(
                    f"Skipped {branch.name}: already has a BRANCH_STAFF account assigned."
                )
            )

        if created and not dry_run:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    f"Placeholder PIN for every new account is '{PLACEHOLDER_PIN}'. "
                    "These accounts have no real name attached yet and show only their "
                    "username on the Select Cashier screen. Once the client confirms real "
                    "staff details, update each account's first_name/last_name and change "
                    "its PIN -- do not leave the placeholder PIN in place."
                )
            )
