"""
Service layer for apps.purchasing.PurchaseOrder/PurchaseOrderItem
(Consolidated Task Tracker "Purchasing/Receiving records model" --
2026-11-07 scoped options, Option B).

Deliberately follows the same shape as apps.production.services' own
StockTransferOrder functions: a forward-only status lifecycle enforced
here (not left to callers), every mutating step wrapped in
db_transaction.atomic() with select_for_update() on the row(s) being
changed, so two near-simultaneous requests for the same order (a
double-click, a network retry) can't both pass a status check against
their own stale copy and double-apply an inventory change -- the same
risk class as production.services' backend gap assessment items 24-26.
"""

from decimal import Decimal, InvalidOperation

from django.db import transaction as db_transaction
from django.utils import timezone

from apps.inventory.models import Inventory, InventoryTransaction, Product
from apps.purchasing.models import PurchaseOrder, PurchaseOrderItem, Supplier

# Client review (2026-11-09): the New Purchase Order form's Items section
# was one flat, ungrouped list of 100+ materials -- grouped here by the
# same real Product.product_type a material already carries (no new
# field, no guessing from the name), so Raw Materials and Packaging
# Materials can each collapse into their own section. Anything that
# isn't one of those two (not expected given purchase_order_list's own
# materials queryset, which is already scoped to Product.MATERIAL_TYPES
# -- but kept as an explicit, labeled "Others" bucket rather than
# silently dropped, in case that queryset is ever widened) falls into
# "Others".
MATERIAL_GROUP_LABELS = (
    (Product.ProductType.MATERIAL, "Raw Materials"),
    (Product.ProductType.PACKAGING, "Packaging Materials"),
)
OTHER_MATERIAL_GROUP_LABEL = "Others"


def group_materials_by_type(materials):
    """Groups an iterable of Product rows (as purchase_order_list's own
    `materials` queryset provides) into the sections the New Purchase
    Order form's Items list renders as collapsible groups.

    Returns a list of dicts, each `{"key": <a stable string for this
    group's DOM id>, "label": <display name>, "materials": [...]}`, in
    the fixed order Raw Materials -> Packaging Materials -> Others.
    Preserves each group's own relative ordering of `materials`
    (whatever order the caller's queryset already used, e.g. .order_by
    ("name")) -- this is a pure grouping/display reorganization, nothing
    here changes which materials exist or their order within a group.
    A group with no materials at all is left out entirely, rather than
    rendered as an empty, pointless collapsible section.
    """
    by_type = {}
    for material in materials:
        by_type.setdefault(material.product_type, []).append(material)

    groups = []
    for product_type, label in MATERIAL_GROUP_LABELS:
        items = by_type.pop(product_type, [])
        if items:
            groups.append({"key": product_type, "label": label, "materials": items})

    leftovers = [material for items in by_type.values() for material in items]
    if leftovers:
        groups.append({"key": "OTHER", "label": OTHER_MATERIAL_GROUP_LABEL, "materials": leftovers})

    return groups


class PurchasingError(Exception):
    """Raised for any invalid purchasing operation (bad input, or an
    illegal status transition) -- callers (views) catch this and show
    the message to the user, the same convention as
    apps.production.services.StockTransferError."""


def create_purchase_order(created_by, supplier_id, branch_id, items, notes=""):
    """Creates a new PurchaseOrder in DRAFT status with its line items.

    `items` is an iterable of dicts, each with "material_id",
    "ordered_quantity", and optionally "unit_cost" -- matching the
    parallel-list form-field convention apps.production.views already
    uses for IngredientUsage rows (material_id[]/quantity_used[]).
    Rows with no usable ordered_quantity should already be filtered out
    by the caller (same "leave blank to skip" convention as that form).

    Nothing is deducted from any Inventory here -- a PO doesn't remove
    stock from anyone; see receive_purchase_order for the one point
    stock actually moves.
    """
    from apps.accounts.models import Branch

    try:
        supplier = Supplier.objects.get(pk=supplier_id)
    except (Supplier.DoesNotExist, ValueError, TypeError):
        raise PurchasingError("Selected supplier does not exist.")

    try:
        branch = Branch.objects.get(pk=branch_id)
    except (Branch.DoesNotExist, ValueError, TypeError):
        raise PurchasingError("Selected branch does not exist.")

    items = list(items)
    if not items:
        raise PurchasingError("Add at least one item with a quantity before creating the order.")

    cleaned_items = []
    for raw in items:
        try:
            # Client review (2026-11-09): widened from
            # Product.ProductType.MATERIAL alone -- a supplier can be
            # ordered from for packaging materials too, now their own
            # ProductType.PACKAGING instead of a MATERIAL row tagged
            # category=PACKAGING.
            material = Product.objects.get(
                pk=raw["material_id"], product_type__in=Product.MATERIAL_TYPES
            )
        except (Product.DoesNotExist, ValueError, TypeError, KeyError):
            raise PurchasingError("One of the selected materials does not exist.")

        try:
            ordered_quantity = Decimal(str(raw.get("ordered_quantity", "")))
        except (InvalidOperation, TypeError, ValueError):
            raise PurchasingError(f"Enter a valid quantity for {material.name}.")
        if ordered_quantity <= 0:
            raise PurchasingError(f"Quantity for {material.name} must be greater than zero.")

        unit_cost_raw = raw.get("unit_cost") or "0"
        try:
            unit_cost = Decimal(str(unit_cost_raw))
        except (InvalidOperation, TypeError, ValueError):
            raise PurchasingError(f"Enter a valid unit cost for {material.name}.")
        if unit_cost < 0:
            raise PurchasingError(f"Unit cost for {material.name} cannot be negative.")

        cleaned_items.append((material, ordered_quantity, unit_cost))

    with db_transaction.atomic():
        order = PurchaseOrder.objects.create(
            supplier=supplier,
            branch=branch,
            created_by=created_by,
            notes=notes,
            status=PurchaseOrder.Status.DRAFT,
        )
        PurchaseOrderItem.objects.bulk_create(
            [
                PurchaseOrderItem(
                    purchase_order=order,
                    material=material,
                    ordered_quantity=ordered_quantity,
                    unit_cost=unit_cost,
                )
                for material, ordered_quantity, unit_cost in cleaned_items
            ]
        )

    return order


def mark_purchase_order_ordered(order, acted_by=None):
    """DRAFT -> ORDERED. No inventory effect -- placing an order with a
    supplier doesn't change anyone's stock on hand yet."""
    with db_transaction.atomic():
        locked = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
        if locked.status != PurchaseOrder.Status.DRAFT:
            raise PurchasingError(
                f"Order is {locked.get_status_display()} -- only a Draft order can be "
                "marked Ordered."
            )
        locked.status = PurchaseOrder.Status.ORDERED
        locked.ordered_at = timezone.now()
        locked.save()

    return locked


def receive_purchase_order(order, received_quantities=None, received_by=None):
    """ORDERED -> RECEIVED: the one point stock actually arrives.

    `received_quantities` is an optional mapping of
    {str(PurchaseOrderItem.pk): raw quantity string/Decimal}. A line
    missing from the mapping (or blank) defaults to that line's own
    ordered_quantity -- matching the common case where a delivery
    arrives exactly as ordered, while still letting a receiving clerk
    override any individual line to record a short or over delivery
    (Option B of the 2026-11-07 scoped options).

    Each line's actual received quantity is added to the order's own
    branch's Inventory and logged as a RESTOCK InventoryTransaction --
    the same "inventory only changes at the one confirming step" shape
    as StockTransferOrder reaching DELIVERED.
    """
    received_quantities = received_quantities or {}

    with db_transaction.atomic():
        locked = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
        if locked.status != PurchaseOrder.Status.ORDERED:
            raise PurchasingError(
                f"Order is {locked.get_status_display()} -- only an Ordered order can be "
                "received."
            )

        items = list(locked.items.select_related("material"))
        if not items:
            raise PurchasingError("This order has no items to receive.")

        for item in items:
            raw = received_quantities.get(str(item.pk))
            if raw is None or str(raw).strip() == "":
                quantity = item.ordered_quantity
            else:
                try:
                    quantity = Decimal(str(raw))
                except (InvalidOperation, TypeError, ValueError):
                    raise PurchasingError(
                        f"Enter a valid received quantity for {item.material.name}."
                    )
            if quantity < 0:
                raise PurchasingError(
                    f"Received quantity for {item.material.name} cannot be negative."
                )

            item.received_quantity = quantity
            item.save(update_fields=["received_quantity"])

            inventory, _ = Inventory.objects.get_or_create(
                branch=locked.branch, product=item.material
            )
            inventory = Inventory.objects.select_for_update().get(pk=inventory.pk)
            inventory.quantity_on_hand += quantity
            inventory.save()
            InventoryTransaction.objects.create(
                branch=locked.branch,
                product=item.material,
                movement_type=InventoryTransaction.MovementType.RESTOCK,
                quantity_change=quantity,
            )

        locked.status = PurchaseOrder.Status.RECEIVED
        locked.received_at = timezone.now()
        locked.received_by = received_by
        locked.save()

    return locked


def cancel_purchase_order(order, cancelled_by=None):
    """Cancels a PurchaseOrder from DRAFT or ORDERED. No inventory
    reversal is needed -- unlike StockTransferOrder (which deducts
    stock up front, at creation), a PO never touches Inventory until
    RECEIVED, so there's nothing to undo before that point. Once
    RECEIVED, an order is final and cannot be cancelled -- the stock is
    already real, on-hand inventory; correcting that is a separate
    manual adjustment, not a cancellation of this record."""
    with db_transaction.atomic():
        locked = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
        if locked.status not in (PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.ORDERED):
            raise PurchasingError(
                f"Order is {locked.get_status_display()} -- only a Draft or Ordered order "
                "can be cancelled."
            )
        locked.status = PurchaseOrder.Status.CANCELLED
        locked.cancelled_at = timezone.now()
        locked.cancelled_by = cancelled_by
        locked.save()

    return locked
