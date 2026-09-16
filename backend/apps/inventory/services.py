"""
Inventory Module business logic (Week 6): branch-scoped stock queries,
low-stock detection, and manual stock adjustment.
"""

from apps.inventory.models import Inventory, InventoryTransaction, Product


def get_branch_inventory(branch, product_type=None, low_stock_only=False):
    """Returns Inventory rows for a branch, optionally filtered by product
    type (Fig. 3-32 Finished Goods vs. Fig. 3-33 Materials share one
    underlying screen, distinguished by this filter) and/or low-stock
    status only (Row 6.4)."""
    queryset = Inventory.objects.select_related("product").filter(branch=branch)

    if product_type:
        queryset = queryset.filter(product__product_type=product_type)

    if low_stock_only:
        # Can't filter is_low_stock (a Python property) at the DB level
        # directly -- evaluated in Python instead. Branch-level inventory
        # counts are small enough that this isn't a real performance
        # concern; revisit with a DB-level annotation if that changes.
        queryset = [item for item in queryset if item.is_low_stock or item.is_out_of_stock]

    return queryset


def get_or_create_inventory_row(branch, product):
    """Ensures a branch has an Inventory row for a product even if no sale
    or adjustment has touched it yet, so it still shows up (at 0) on the
    monitoring screen rather than being invisible until first touched."""
    inventory, _ = Inventory.objects.get_or_create(branch=branch, product=product)
    return inventory


class InventoryServiceError(Exception):
    """Raised for any reason an inventory write (manual adjustment or
    product creation) can't be applied."""


def adjust_stock(inventory, delta, reason=""):
    """Manual Stock Adjustment (Fig. 3-34's action). `delta` is signed --
    positive to add stock (e.g. correcting a miscount), negative to
    remove it. Always logs a traceable InventoryTransaction, same as the
    Week 5 sale-deduction hook, so manual changes are equally auditable."""
    try:
        delta = int(delta)
    except (TypeError, ValueError):
        raise InventoryServiceError("Enter a whole number for the adjustment.")

    if delta == 0:
        raise InventoryServiceError("Adjustment cannot be zero.")

    new_quantity = inventory.quantity_on_hand + delta
    if new_quantity < 0:
        raise InventoryServiceError(
            f"This would take stock below zero (currently {inventory.quantity_on_hand})."
        )

    inventory.quantity_on_hand = new_quantity
    inventory.save()

    InventoryTransaction.objects.create(
        branch=inventory.branch,
        product=inventory.product,
        movement_type=InventoryTransaction.MovementType.MANUAL_ADJUSTMENT,
        quantity_change=delta,
    )
    return inventory


def create_product(name, price, product_type, reorder_threshold=0):
    """Product Inventory Management (Row 6.2): adds a new item to the
    catalog. Kept separate from POS's own catalog-reading logic -- this is
    the write side, restricted to Owner/Admin at the view layer."""
    name = (name or "").strip()
    if not name:
        raise InventoryServiceError("Product name is required.")

    return Product.objects.create(
        name=name,
        price=price,
        product_type=product_type,
        reorder_threshold=reorder_threshold or 0,
    )
