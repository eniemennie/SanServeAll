"""
Backend gap assessment item 29: a periodic consistency check summing
InventoryTransaction deltas and comparing the total to each Inventory
row's own quantity_on_hand -- the two are written together in every
service function in this app, but nothing had ever tied them back to
each other afterward, so drift from a future bug would go undetected
indefinitely.

Run manually (`python manage.py reconcile_inventory`) or wired into a
periodic Scheduled Task, same reasoning as the nightly forecast/risk/
insight commands in apps/forecasting/management/commands -- those run
through APScheduler too, but a standalone command also works from an
external cron with no running web worker, and gives a real process
exit code a monitoring tool can actually alert on.
"""

from django.core.management.base import BaseCommand

from apps.inventory.services import find_ledger_discrepancies


class Command(BaseCommand):
    help = (
        "Checks that every Inventory.quantity_on_hand matches the sum of its own "
        "InventoryTransaction ledger entries, and reports any that don't."
    )

    def handle(self, *args, **options):
        discrepancies = find_ledger_discrepancies()

        if not discrepancies:
            self.stdout.write(
                self.style.SUCCESS("Inventory reconciliation: no discrepancies found.")
            )
            return

        self.stdout.write(
            self.style.ERROR(
                f"Inventory reconciliation: {len(discrepancies)} discrepancy(ies) found:"
            )
        )
        for entry in discrepancies:
            inventory = entry["inventory"]
            self.stdout.write(
                self.style.ERROR(
                    f"  - {inventory.product.name} @ {inventory.branch.name}: "
                    f"recorded balance is {entry['recorded_balance']}, ledger sums to "
                    f"{entry['ledger_total']} (difference: {entry['difference']:+d})"
                )
            )

        raise SystemExit(1)
