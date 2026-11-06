"""
One-time, narrowly-scoped recovery for the partial backfill crash on
2026-11-06 (see diagnose_partial_backfill.py and the incident writeup
in the project conversation history for the full story).

What this undoes, precisely:
  - SalesTransaction #5988 (Java Chip Frappe @ Batangas City, backdated
    to 2026-08-14), and its one SalesItem -- the single synthetic sale
    that got committed before the crash.
  - InventoryTransaction #8818 -- that sale's own SALE_DEDUCTION log
    row, which was never compensated (the crash happened in the
    compensation step).
  - Restores Inventory.quantity_on_hand for (Batangas City, Java Chip
    Frappe) by +1.000 -- NOT via inventory_services.adjust_stock(),
    which is the exact function that refused to do this in the first
    place (it enforces a "result must never be negative" rule, and
    -2.000 + 1.000 = -1.000 is still negative). That rule protects
    against new, unjustified depletion; it isn't applicable here,
    because -1.000 is this item's own real, pre-existing balance from
    BEFORE our script ran (confirmed from the diagnostic: current
    -2.000, minus our own uncompensated -1.000 deduction, = -1.000
    pre-existing -- a genuine real-data characteristic unrelated to
    us, not something this script tries to "fix"). This script
    corrects a bug's side effect; it does not claim the resulting
    -1.000 is a healthy stock level.
  - Deletes the synthetic_backfill_bot account (nothing will reference
    it once the transaction above is gone).

Safety: every expected value below is asserted against actual current
state before any write happens. If even one doesn't match exactly
what diagnose_partial_backfill.py already confirmed, this script
prints what it found and makes NO changes, rather than guessing.

Defaults to dry_run=True (prints what it would do, writes nothing).

Usage:
    python manage.py shell --settings=config.settings.production \
        -c "from scripts.recover_partial_backfill_20261106 import run; run()"

    # only after reviewing the dry run:
    python manage.py shell --settings=config.settings.production \
        -c "from scripts.recover_partial_backfill_20261106 import run; run(dry_run=False)"
"""

import os
import sys
from decimal import Decimal

import django

if not django.apps.apps.ready:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    django.setup()

from django.db import transaction  # noqa: E402
from django.db.models import F  # noqa: E402

from apps.accounts.models import Branch, User  # noqa: E402
from apps.inventory.models import Inventory, InventoryTransaction, Product  # noqa: E402
from apps.pos.models import SalesItem, SalesTransaction  # noqa: E402

BOT_USERNAME = "synthetic_backfill_bot"
EXPECTED_TRANSACTION_PK = 5988
EXPECTED_INVENTORY_TX_PK = 8818
EXPECTED_CURRENT_QUANTITY = Decimal("-2.000")
RESTORE_AMOUNT = Decimal("1.000")
EXPECTED_RESTORED_QUANTITY = Decimal("-1.000")


def _check(label, condition):
    status = "OK" if condition else "MISMATCH"
    print(f"  [{status}] {label}")
    return condition


def run(dry_run=True):
    print("=" * 70)
    mode = "DRY RUN -- no writes will be made" if dry_run else "LIVE RUN"
    print(f"Recovering partial backfill crash ({mode})")
    print("=" * 70)

    print("\nVerifying current state matches exactly what was diagnosed...")
    all_ok = True

    bot = User.objects.filter(username=BOT_USERNAME).first()
    all_ok &= _check("bot account exists", bot is not None)
    if bot is None:
        print("\nStopping -- bot account not found. Nothing to recover, or already recovered.")
        return

    txn = SalesTransaction.objects.filter(pk=EXPECTED_TRANSACTION_PK, cashier=bot).first()
    all_ok &= _check(
        f"transaction #{EXPECTED_TRANSACTION_PK} exists under the bot", txn is not None
    )

    bot_txn_count = SalesTransaction.objects.filter(cashier=bot).count()
    all_ok &= _check(
        f"exactly 1 transaction under the bot (found {bot_txn_count})", bot_txn_count == 1
    )

    branch = Branch.objects.filter(name="Batangas City").first()
    product = Product.objects.filter(name="Java Chip Frappe").first()
    all_ok &= _check("branch 'Batangas City' found", branch is not None)
    all_ok &= _check("product 'Java Chip Frappe' found", product is not None)

    inv = None
    if branch is not None and product is not None:
        inv = Inventory.objects.filter(branch=branch, product=product).first()
        all_ok &= _check("inventory row exists", inv is not None)
        if inv is not None:
            all_ok &= _check(
                f"quantity_on_hand == {EXPECTED_CURRENT_QUANTITY} (found {inv.quantity_on_hand})",
                inv.quantity_on_hand == EXPECTED_CURRENT_QUANTITY,
            )

    inv_tx = InventoryTransaction.objects.filter(pk=EXPECTED_INVENTORY_TX_PK).first()
    all_ok &= _check(
        f"inventory transaction #{EXPECTED_INVENTORY_TX_PK} exists "
        f"(movement_type=SALE_DEDUCTION, quantity_change=-1.000)",
        inv_tx is not None
        and inv_tx.movement_type == InventoryTransaction.MovementType.SALE_DEDUCTION
        and inv_tx.quantity_change == Decimal("-1.000"),
    )

    # No activity after the crashed deduction -- confirms nothing has
    # drifted since diagnose_partial_backfill.py was run.
    later_tx_exists = (
        inv is not None
        and InventoryTransaction.objects.filter(
            branch=branch, product=product, pk__gt=EXPECTED_INVENTORY_TX_PK
        ).exists()
    )
    all_ok &= _check("no inventory activity on this item since the crash", not later_tx_exists)

    if not all_ok:
        print("\nOne or more checks did not match exactly. Stopping -- making NO changes.")
        print("Do not proceed by hand either; re-run diagnose_partial_backfill.py and")
        print("review what actually changed before touching anything.")
        return

    print("\nAll checks passed. Planned actions:")
    print(f"  1. Delete SalesTransaction #{EXPECTED_TRANSACTION_PK} (and its SalesItem row).")
    print(f"  2. Delete InventoryTransaction #{EXPECTED_INVENTORY_TX_PK} (the orphaned deduction).")
    print(
        f"  3. Restore Inventory.quantity_on_hand for Java Chip Frappe @ Batangas City: "
        f"{inv.quantity_on_hand} -> {EXPECTED_RESTORED_QUANTITY} (+{RESTORE_AMOUNT})."
    )
    print(f"  4. Delete the '{BOT_USERNAME}' account.")

    if dry_run:
        print("\nDry run complete. No database changes were made.")
        print("Review the above, then call run(dry_run=False) to actually apply it.")
        return

    with transaction.atomic():
        items_deleted, _ = SalesItem.objects.filter(transaction=txn).delete()
        txn.delete()
        print(
            f"\n  Deleted transaction #{EXPECTED_TRANSACTION_PK} and {items_deleted} item row(s)."
        )

        inv_tx.delete()
        print(f"  Deleted inventory transaction #{EXPECTED_INVENTORY_TX_PK}.")

        Inventory.objects.filter(pk=inv.pk).update(
            quantity_on_hand=F("quantity_on_hand") + RESTORE_AMOUNT
        )
        inv.refresh_from_db()
        print(f"  Restored quantity_on_hand -> {inv.quantity_on_hand}.")

        bot.delete()
        print(f"  Deleted the '{BOT_USERNAME}' account.")

    final_qty = Inventory.objects.get(branch=branch, product=product).quantity_on_hand
    print("\n" + "=" * 70)
    if final_qty == EXPECTED_RESTORED_QUANTITY:
        print(f"Recovery complete and verified: quantity_on_hand == {final_qty}, as expected.")
    else:
        print(
            f"!! Recovery ran, but final quantity_on_hand is {final_qty}, "
            f"not the expected {EXPECTED_RESTORED_QUANTITY}. Investigate immediately."
        )
    print("=" * 70)


if __name__ == "__main__":
    run()
