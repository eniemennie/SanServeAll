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


def get_resource_status_summary(branch=None):
    """Stock-health snapshot for the Dashboard Home Resource Status KPI
    card (Fig. 3-19) and the Resource Monitoring list underneath it.

    NOTE: the reference design shows four tiers (Critical/Low/Medium/
    Adequate). Inventory only models three real states (is_out_of_stock,
    is_low_stock, and Available -- see Inventory.status_label, Fig.
    3-20/3-32's own three-state badge). Collapsed honestly here rather
    than inventing an unbacked "Medium" cutoff with no threshold behind
    it: Critical == is_out_of_stock, Low == is_low_stock, Adequate ==
    everything else.

    branch=None aggregates across every active, non-commissary branch
    (the Dashboard's "All Branches" state); a specific branch filters to
    just that one.
    """
    from apps.accounts.models import Branch

    queryset = Inventory.objects.select_related("product", "branch")
    if branch is not None:
        queryset = queryset.filter(branch=branch)
    else:
        queryset = queryset.filter(
            branch__in=Branch.objects.filter(is_active=True, is_commissary=False)
        )

    items = list(queryset)
    critical_items = [i for i in items if i.is_out_of_stock]
    low_items = [i for i in items if not i.is_out_of_stock and i.is_low_stock]
    adequate_items = [i for i in items if not i.is_out_of_stock and not i.is_low_stock]
    total = len(items)
    sufficiency_pct = round((len(adequate_items) / total) * 100) if total else 100

    # Critical first, then Low, then Adequate -- matches the reference
    # design's own ordering for the Resource Monitoring list (Fig. 3-19).
    ordered_items = critical_items + low_items + adequate_items

    return {
        "total_items": total,
        "critical_count": len(critical_items),
        "critical_items": critical_items,
        "low_count": len(low_items),
        "low_items": low_items,
        "adequate_count": len(adequate_items),
        "items": ordered_items,
        "sufficiency_pct": sufficiency_pct,
    }


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
