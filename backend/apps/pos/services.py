"""
Sale-processing logic: draft-order construction, item add/remove/customize.

Payment finalization (marking a transaction COMPLETED) is Week 5 territory
-- everything here only ever creates/edits a DRAFT SalesTransaction.
"""

from decimal import Decimal, InvalidOperation

from apps.inventory.models import Product
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
