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


def compute_customized_price(product, size, addon_ids, discount_type, discount_amount):
    """The real price math behind the Product Customization modal (Row 3
    redesign): base price (Small/Large) + add-ons, then a discount
    applied to that subtotal. Returns (final_price, discount_value,
    price_before_discount) so the view/template can show the same
    breakdown the reference does (Original Price / Discount / Price
    After Discount).

    Never lets a discount push the price below zero -- caps at the
    pre-discount subtotal regardless of what was entered."""
    from apps.pos.models import AddOn

    base_price = product.price
    if size == "LARGE" and product.has_size_options:
        base_price = product.large_price

    addon_total = Decimal("0.00")
    if addon_ids:
        addon_total = sum(
            (a.price for a in AddOn.objects.filter(pk__in=addon_ids, is_active=True)),
            Decimal("0.00"),
        )

    price_before_discount = base_price + addon_total

    discount_value = Decimal("0.00")
    if discount_amount:
        try:
            discount_amount = Decimal(str(discount_amount))
        except (InvalidOperation, TypeError):
            discount_amount = Decimal("0.00")

        if discount_amount > 0:
            if discount_type == "PERCENT":
                discount_value = price_before_discount * (discount_amount / Decimal("100"))
            else:
                discount_value = discount_amount
            # Never let a discount push the price negative
            discount_value = min(discount_value, price_before_discount)

    final_price = price_before_discount - discount_value
    return final_price, discount_value, price_before_discount


def add_customized_item(
    draft,
    product_id,
    quantity=1,
    size="",
    sugar_level="",
    addon_ids=None,
    discount_category="",
    discount_type="",
    discount_amount=None,
    note="",
):
    """Adds a beverage with full customization (Row 3 redesign, Fig.
    3-14) as a single step -- unlike the original add_catalog_item +
    separate customize_item flow, this computes the final per-unit price
    up front (size, add-ons, discount all applied) and stores every
    choice in `customizations` for receipt/order-summary display."""
    from apps.pos.models import AddOn

    try:
        product = Product.objects.get(pk=product_id, is_active=True)
    except (Product.DoesNotExist, ValueError, TypeError):
        return None

    quantity = max(1, int(quantity) if str(quantity).isdigit() else 1)
    addon_ids = addon_ids or []

    unit_price, discount_value, price_before_discount = compute_customized_price(
        product, size, addon_ids, discount_type, discount_amount
    )

    addon_names = list(
        AddOn.objects.filter(pk__in=addon_ids, is_active=True).values_list("name", flat=True)
    )

    from apps.pos.models import DiscountCategory

    discount_labels = dict(DiscountCategory.choices)
    customizations = {
        "size": (
            "Large"
            if size == "LARGE" and product.has_size_options
            else ("Small" if product.has_size_options else "")
        ),
        "sugar_level": sugar_level,
        "add_ons": addon_names,
        "discount_category": (
            discount_labels.get(discount_category, discount_category)
            if discount_category and discount_category != "NO_DISCOUNT"
            else ""
        ),
        "discount_value": str(discount_value) if discount_value else "",
        "price_before_discount": str(price_before_discount),
        "note": note,
    }

    return SalesItem.objects.create(
        transaction=draft,
        product=product,
        unit_price=unit_price,
        quantity=quantity,
        customizations=customizations,
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


def update_item_quantity(draft, item_id, quantity):
    """The quick +/- stepper directly in the Order Summary (Row 3
    redesign) -- distinct from the full Edit/customize flow, this only
    ever touches quantity. Never lets quantity drop below 1 -- use
    Remove for that instead, matching the reference's separate trash
    icon vs. the stepper."""
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        return False
    if quantity < 1:
        return False

    updated = SalesItem.objects.filter(pk=item_id, transaction=draft).update(quantity=quantity)
    return updated > 0


def clear_draft_items(draft):
    """'Clear All' (Row 3 redesign) -- empties the current draft order
    without discarding the transaction row itself, so the cashier can
    start over on the same draft rather than orphaning it."""
    draft.items.all().delete()


def set_dining_option(draft, dining_option):
    """Dine-in / Take-out toggle (Row 3 redesign)."""
    if dining_option not in SalesTransaction.DiningOption.values:
        return False
    draft.dining_option = dining_option
    draft.save()
    return True


def apply_transaction_discount(draft, category, discount_type, amount):
    """The '% Discount' button (Row 3 redesign) -- a discount applied to
    the WHOLE order at once, layered on top of (not instead of) any
    per-item discounts already applied via the customization modal."""
    if not category or category == "NO_DISCOUNT":
        draft.transaction_discount_category = ""
        draft.transaction_discount_type = ""
        draft.transaction_discount_amount = None
        draft.save()
        return True

    try:
        amount = Decimal(str(amount))
    except (InvalidOperation, TypeError):
        return False
    if amount < 0:
        return False

    draft.transaction_discount_category = category
    draft.transaction_discount_type = discount_type
    draft.transaction_discount_amount = amount
    draft.save()
    return True


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

    total = draft.grand_total
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
