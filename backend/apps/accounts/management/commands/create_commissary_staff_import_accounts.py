"""
Ensures the 3 real COMMISSARY_STAFF usernames the client's own historical
Production_Records.xlsx actually references -- commissary_staff,
commissary_staff2, commissary_staff3 -- exist as real, functional
accounts before that data is imported.

Why this exists, and how it differs from create_commissary_staff_placeholder:
That command's job is "is there at least one commissary staff account at
all" -- it intentionally stops as soon as ANY active COMMISSARY_STAFF
exists, which is exactly right for bootstrapping day-to-day operation but
wrong here. The client's Production_Records.xlsx records 356 real
production runs performed by 3 distinct staff identifiers
(commissary_staff: 224, commissary_staff2: 71, commissary_staff3: 61) --
these are the client's own usernames, not invented ones, and the importer
(apps/production, upcoming) will need a real User row for each one to
attribute every ProductionRecord.commissary_staff FK to. If only
commissary_staff existed, the other two usernames would have no account
to resolve against and the import would have to invent a fallback user or
fail outright.

Design, same principles as create_commissary_staff_placeholder and
create_branch_cashier_placeholders:
  - first_name / last_name left blank -- no fictional name is invented for
    any of the 3.
  - commissary Branch assignment, same as the existing commissary_staff
    account.
  - PLACEHOLDER_COMMISSARY_PASSWORD -- documented, not secret, same
    reasoning as the existing command: there is no real person yet to
    privately receive it for staff2/staff3 either.

Idempotent per exact username (get_or_create), not "any active commissary
staff" -- so running this after create_commissary_staff_placeholder has
already created "commissary_staff" leaves that account untouched and adds
only the 2 missing ones; running it again afterwards creates nothing.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Branch, Role
from apps.accounts.services import PLACEHOLDER_COMMISSARY_PASSWORD as PLACEHOLDER_PASSWORD

# The client's own usernames, exactly as they appear in
# Production_Records.xlsx's "Commissary Staff (username)" column.
REQUIRED_USERNAMES = ["commissary_staff", "commissary_staff2", "commissary_staff3"]


class Command(BaseCommand):
    help = (
        "Ensures commissary_staff, commissary_staff2, and commissary_staff3 all exist "
        "as real COMMISSARY_STAFF accounts, so the client's historical production data "
        "has a real user to attribute each run to."
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

        created, skipped = [], []

        with transaction.atomic():
            for username in REQUIRED_USERNAMES:
                if User.objects.filter(username=username).exists():
                    skipped.append(username)
                    continue

                if dry_run:
                    created.append(username)
                    continue

                user = User.objects.create(
                    username=username,
                    first_name="",
                    last_name="",
                    role=commissary_role,
                    branch=commissary_branch,
                    is_active=True,
                )
                user.set_password(PLACEHOLDER_PASSWORD)
                user.save()
                created.append(username)

            if dry_run:
                transaction.set_rollback(True)

        for username in created:
            verb = "Would create" if dry_run else "Created"
            self.stdout.write(self.style.SUCCESS(f"{verb}: {username} -> {commissary_branch.name}"))

        for username in skipped:
            self.stdout.write(self.style.WARNING(f"Skipped {username}: already exists."))

        if created and not dry_run:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    f"Password for every new account is '{PLACEHOLDER_PASSWORD}'. These "
                    "accounts have no real name attached yet. Once the client confirms real "
                    "staff details, update each account's first_name/last_name and change its "
                    "password -- do not leave the placeholder password in place."
                )
            )
