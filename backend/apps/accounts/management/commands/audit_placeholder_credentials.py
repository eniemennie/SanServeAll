"""
Backend gap assessment item 7 (Phase 1, critical): the "0000" cashier PIN
and the commissary seed password (apps.accounts.services.
PLACEHOLDER_CASHIER_PIN / PLACEHOLDER_COMMISSARY_PASSWORD) are a known,
intentional gap while no real staff member exists yet to receive a real
credential privately -- create_branch_cashier_placeholders and
create_commissary_staff_placeholder both print a one-time warning about
this at creation time. That warning is easy to miss weeks later, which is
exactly when it matters: right before a real deployment, once real staff
should already have replaced it.

This command is the enforcement half of item 7 -- a deploy-gate check
intended to run (manually, or as a CI/deploy step) before shipping: it
exits 1 and lists every account still using its documented placeholder
value, or exits 0 with nothing to report once every placeholder has
actually been rotated.
"""

from django.core.management.base import BaseCommand

from apps.accounts.services import find_accounts_with_placeholder_credentials


class Command(BaseCommand):
    help = (
        "Fails (exit 1) if any active account still uses a documented placeholder "
        "credential (the seed cashier PIN or commissary password). Run before deploying."
    )

    def handle(self, *args, **options):
        findings = find_accounts_with_placeholder_credentials()

        if not findings:
            self.stdout.write(
                self.style.SUCCESS("No placeholder credentials found on any active account.")
            )
            return

        self.stdout.write(
            self.style.ERROR(
                f"{len(findings)} active account(s) still use a documented placeholder "
                "credential:"
            )
        )
        for finding in findings:
            self.stdout.write(self.style.ERROR(f"  - {finding['message']}"))
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "Rotate each one (the PIN-change flow, or a password change) before "
                "deploying any further."
            )
        )
        raise SystemExit(1)
