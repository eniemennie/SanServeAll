"""
Sale-processing logic: draft-order construction, item add/remove/customize,
and payment finalization (Week 5).
"""

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.inventory.models import Inventory, InventoryTransaction, Product
from apps.pos.models import SalesItem, SalesTransaction


def get_or_create_draft_transaction(user, branch):
    """One draft transaction per (cashier, branch) at a time. Reused across
    requests so the in-progress order survives navigation/refresh instead
    of living only in a session that could be lost."""
    draft, _ = SalesTransaction.objects.get_or_create(
        cashier=user,
        branch=branch,
        status=SalesTransaction.Status.DRAFT,
    )
    return draft


def add_catalog_item(draft, product_id, quantity=1):
    """Adds a product from the standard catalog to the draft order
    (Fig. 3-12). Returns None on an invalid/inactive product rather than
    raising, so the view can show a friendly error instead of a 500."""
    try:
        product = Product.objects.get(pk=product_id, is_active=True)
    except (Product.DoesNotExist, ValueError, TypeError):
        return None

    return SalesItem.objects.create(
        transaction=draft,
        product=product,
        unit_price=product.price,
        quantity=max(1, int(quantity) if str(quantity).isdigit() else 1),
    )


def add_custom_item(draft, name, price):
    """Adds an off-menu item (Fig. 3-13) -- a manually entered name and
    price for something not in the standard catalog."""
    name = (name or "").strip()
    if not name:
        return None
    try:
        price = Decimal(str(price))
    except (InvalidOperation, TypeError):
        return None
    if price < 0:
        return None

    return SalesItem.objects.create(
        transaction=draft,
        product=None,
        custom_name=name,
        unit_price=price,
        quantity=1,
    )


def update_item_customization(item, quantity=None, customizations=None):
    """Applies Order Customization changes (Fig. 3-14) to an existing line
    item -- size, sugar level, add-ons, quantity, etc. `customizations` is
    stored as-is (a plain dict) since its shape varies per product."""
    if quantity is not None:
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            quantity = None
        if quantity is not None and quantity > 0:
            item.quantity = quantity

    if customizations is not None:
        item.customizations = customizations

    item.save()
    return item


def remove_item(draft, item_id):
    """Removes a line item from the draft order. Scoped to `draft` so a
    cashier can't remove an item belonging to someone else's transaction
    by guessing an ID."""
    deleted, _ = SalesItem.objects.filter(pk=item_id, transaction=draft).delete()
    return deleted > 0


class PaymentError(Exception):
    """Raised for any reason a payment cannot be completed -- lets the
    view show a specific message rather than a generic failure."""


def complete_sale_payment(draft, payment_method, amount_tendered):
    """Finalizes a DRAFT transaction into COMPLETED, and triggers the
    real-time inventory deduction hook (Row 5.3) for native-POS branches.

    Deliberately atomic: the transaction status change and every inventory
    deduction happen together, or none of them do. A sale should never be
    marked paid while only half its stock got deducted.
    """
    if not draft.items.exists():
        raise PaymentError("Cannot process payment for an empty order.")

    if payment_method not in SalesTransaction.PaymentMethod.values:
        raise PaymentError("Please select a valid payment method.")

    try:
        amount_tendered = Decimal(str(amount_tendered))
    except (InvalidOperation, TypeError):
        raise PaymentError("Please enter a valid amount.")

    total = draft.total_amount
    if amount_tendered < total:
        raise PaymentError("Amount tendered is less than the total due.")

    with transaction.atomic():
        draft.payment_method = payment_method
        draft.amount_tendered = amount_tendered
        draft.status = SalesTransaction.Status.COMPLETED
        draft.completed_at = timezone.now()
        draft.save()

        # KaHero-branch (Alangilan) inventory is reconciled through the
        # batch-import pipeline, not real-time POS sales -- deliberately
        # skipped here, matching the Phase 2/3 architecture decision.
        if not draft.branch.is_kahero_branch:
            _deduct_inventory_for_sale(draft)

    return draft


def _deduct_inventory_for_sale(transaction_obj):
    """The real-time inventory deduction hook (Row 5.3). Only called for
    native-POS branches, and only from inside complete_sale_payment's
    atomic block -- never call this directly from a view."""
    for item in transaction_obj.items.select_related("product"):
        if item.product is None:
            # Custom/off-menu items (Fig. 3-13) aren't tracked in the
            # product catalog, so there's nothing to deduct stock from.
            continue

        inventory, _ = Inventory.objects.get_or_create(
            branch=transaction_obj.branch, product=item.product
        )
        # Deliberately not blocking the sale even if this drives stock
        # negative -- the sale has already been rung up and paid for by
        # the time this runs. A negative/low balance is a signal for the
        # AI risk-detection feature (Week 10-11) to flag, not a reason to
        # refuse a completed payment retroactively.
        inventory.quantity_on_hand -= item.quantity
        inventory.save()

        InventoryTransaction.objects.create(
            branch=transaction_obj.branch,
            product=item.product,
            movement_type=InventoryTransaction.MovementType.SALE_DEDUCTION,
            quantity_change=-item.quantity,
            related_sales_item=item,
        )
