"""
READ-ONLY diagnostic for the 2026-11-06 partial backfill crash. Makes
NO database writes. Run this before touching anything, so the actual
cleanup can be based on real current state instead of a guess from the
traceback alone.

What happened: scripts/backfill_thin_forecast_pairs.py crashed partway
through its first synthetic sale (Java Chip Frappe @ Batangas City,
2026-08-14) because the compensating adjust_stock() call hit that
product's pre-existing (real, not caused by us) negative stock balance
and correctly refused to leave it still negative. The sale itself had
already been committed by that point (complete_sale_payment() doesn't
share adjust_stock()'s non-negative floor), and no manifest was
written (the crash happened before _write_manifest()).

Usage:
    python manage.py shell --settings=config.settings.production \
        -c "from scripts.diagnose_partial_backfill import run; run()"
"""

import os
import sys

import django

if not django.apps.apps.ready:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    django.setup()

from apps.accounts.models import User  # noqa: E402
from apps.inventory.models import Inventory, InventoryTransaction, Product  # noqa: E402
from apps.pos.models import SalesItem, SalesTransaction  # noqa: E402
from apps.accounts.models import Branch  # noqa: E402

BOT_USERNAME = "synthetic_backfill_bot"


def run():
    print("=" * 70)
    print("Diagnosing partial backfill crash (read-only)")
    print("=" * 70)

    bot_exists = User.objects.filter(username=BOT_USERNAME).exists()
    print(f"\nBot account '{BOT_USERNAME}' exists: {bot_exists}")

    if not bot_exists:
        print("No bot account found -- nothing from this script has been committed.")
        return

    bot_transactions = SalesTransaction.objects.filter(cashier__username=BOT_USERNAME).order_by(
        "pk"
    )
    print(f"\nSalesTransaction rows under the bot account: {bot_transactions.count()}")
    for txn in bot_transactions:
        items = SalesItem.objects.filter(transaction=txn)
        print(
            f"  Transaction #{txn.pk} -- branch={txn.branch.name} -- "
            f"status={txn.status} -- completed_at={txn.completed_at} -- "
            f"created_at={txn.created_at}"
        )
        for item in items:
            print(f"    item: {item.product.name} x {item.quantity}")

    print("\nCurrent Inventory level for the specific item involved in the crash:")
    try:
        branch = Branch.objects.get(name="Batangas City")
        product = Product.objects.get(name="Java Chip Frappe")
        inv = Inventory.objects.get(branch=branch, product=product)
        print(f"  Java Chip Frappe @ Batangas City -- quantity_on_hand={inv.quantity_on_hand}")

        print("\n  Recent InventoryTransaction log for this item (most recent first):")
        for tx in InventoryTransaction.objects.filter(branch=branch, product=product).order_by(
            "-pk"
        )[:8]:
            print(
                f"    #{tx.pk} {tx.movement_type} {tx.quantity_change} "
                f"created_at={tx.created_at} reason={tx.reason!r}"
            )
    except (Branch.DoesNotExist, Product.DoesNotExist, Inventory.DoesNotExist) as exc:
        print(f"  Could not look up that item directly: {exc}")

    print("\n" + "=" * 70)
    print("No changes were made. Share this output before any cleanup is written.")
    print("=" * 70)


if __name__ == "__main__":
    run()
