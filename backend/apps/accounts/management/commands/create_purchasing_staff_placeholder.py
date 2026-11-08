"""
Establishes a real, functional PURCHASING account -- without any
fictional human name and without being a "test" account -- the same
principle already applied to create_commissary_staff_placeholder for
Commissary Staff and create_branch_cashier_placeholders for the branch
cashiers.

Why this exists: Role.PURCHASING (Task #143) and the full login/routing
path for it (PurchasingLoginView -> BranchSelectionView's Purchasing
special-case -> purchasing:supplier_management) were both built and
merged, but there was never an actual account to log in with -- no
seed command, no demo account, nothing. Without this, the only way to
reach the Purchasing screens at all is to create an account by hand
through Account Management first, which is extra friction right before
a defense/demo when what's needed is "log in and show it works."

Design, mirroring create_commissary_staff_placeholder:
  - username: "purchasing_staff" -- the role's own name, not a person's
    name, and not labeled "test".
  - first_name / last_name: left blank, on purpose, same reasoning as
    the commissary/cashier placeholders -- nowhere in the UI should a
    fabricated name appear.
  - branch: none. Unlike Commissary Staff, PURCHASING is explicitly not
    branch-scoped (Task #143, same convention as OWNER_ADMIN) -- there
    is no branch to assign here, and User.branch stays null.
  - a real, usable password -- PurchasingLoginView authenticates by
    username+password with no brute-force lockout exemption, so a
    trivial, guessable value would be a real exposure. The password
    below is still a documented, known value (not a secret) for the
    same reason the commissary placeholder's is: there is no real
    person yet to privately receive it.

Idempotent: skips (reports, not silently) if an ACTIVE PURCHASING
account already exists -- same is_active-scoped check as the other two
placeholder commands, so an inactive leftover never blocks this.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.accounts.models import Role
from apps.accounts.services import PLACEHOLDER_PURCHASING_PASSWORD as PLACEHOLDER_PASSWORD

# Deliberately documented, not secret: there is no real purchasing staff
# member yet to privately receive this. Change it via the admin or the
# account's own password-change flow once a real person is assigned.
# (Canonical value lives in apps.accounts.services, shared with the
# find_accounts_with_placeholder_credentials() deploy-gate check --
# Phase 1 item 7.)


class Command(BaseCommand):
    help = (
        "Creates one real, functional PURCHASING account (no fictional name, no test "
        "account) if none is currently active, ready for the client to attach real "
        "staff details to later."
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

        purchasing_role, _ = Role.objects.get_or_create(
            name=Role.PURCHASING, defaults={"description": "Purchasing"}
        )

        already_staffed = User.objects.filter(role=purchasing_role, is_active=True).exists()
        if already_staffed:
            self.stdout.write(
                self.style.WARNING("Skipped: an active PURCHASING account already exists.")
            )
            return

        if dry_run:
            self.stdout.write(self.style.SUCCESS("Would create: purchasing_staff"))
            return

        user = User.objects.create(
            username="purchasing_staff",
            first_name="",
            last_name="",
            role=purchasing_role,
            branch=None,
            is_active=True,
        )
        user.set_password(PLACEHOLDER_PASSWORD)
        user.save()

        self.stdout.write(self.style.SUCCESS("Created: purchasing_staff"))
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                f"Password for the new account is '{PLACEHOLDER_PASSWORD}'. This account has "
                "no real name attached yet. Once the client confirms real staff details, "
                "update the account's first_name/last_name and change its password -- do not "
                "leave the placeholder password in place."
            )
        )
