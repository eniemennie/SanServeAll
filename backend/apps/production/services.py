"""
Production Module business logic (Week 8): recording a production run
converts raw materials into finished goods at the commissary -- both
sides happen atomically against the SAME Inventory model Week 5/6 already
built (branch=commissary), rather than a separate stock-tracking system.
"""

from datetime import timedelta

from django.db import transaction as db_transaction
from django.db.models import Sum
from django.utils import timezone

from apps.inventory.models import Inventory, InventoryTransaction, Product
from apps.production.models import IngredientUsage, ProductionRecord, StockTransferOrder


class ProductionError(Exception):
    """Raised when a production run can't be recorded as requested."""


class StockTransferError(Exception):
    """Raised when a stock transfer order can't be created or advanced as requested."""


def get_commissary_branch():
    """There is exactly one commissary (Phase 1 §1.1) -- this is the
    single lookup point for it, so nothing else needs to hardcode a
    branch name or code to find it."""
    from apps.accounts.models import Branch

    return Branch.objects.filter(is_commissary=True).first()


def get_total_output(days=30):
    """Total units produced across all completed runs in the window --
    the Dashboard Home Production Output KPI card (Fig. 3-19).

    Deliberately system-wide, not per-branch: ProductionRecord has no
    branch FK because production genuinely happens only at the single
    commissary and is distributed to branches afterward (Phase 1 §1.1) --
    there's no honest way to attribute one production run to one branch.
    This total is real; it just isn't branch-filterable the way Revenue
    or Orders are on the same dashboard.
    """
    since = timezone.now() - timedelta(days=days)
    total = ProductionRecord.objects.filter(
        status=ProductionRecord.Status.COMPLETED, created_at__gte=since
    ).aggregate(total=Sum("quantity_produced"))["total"]
    return total or 0


def record_production(commissary_staff, product_id, quantity_produced, ingredient_rows, **extra):
    """Records one production run (Fig. 3-24's Batch Management entry).

    `ingredient_rows` is a list of {"material_id": int, "quantity_used": int}
    dicts. Every material must have sufficient stock at the commissary --
    if any one doesn't, the ENTIRE run is rejected (nothing partially
    deducted), since a production run that used only some of its planned
    ingredients isn't a real, meaningful state to record.
    """
    commissary = get_commissary_branch()
    if commissary is None:
        raise ProductionError("No commissary branch is configured.")

    try:
        product = Product.objects.get(pk=product_id)
    except Product.DoesNotExist:
        raise ProductionError("Selected product does not exist.")

    if quantity_produced <= 0:
        raise ProductionError("Quantity produced must be greater than zero.")

    if not ingredient_rows:
        raise ProductionError("At least one ingredient/material is required.")

    # Validate every material and its available stock BEFORE touching any
    # data -- this is what guarantees the "reject the whole run, not a
    # partial one" behavior described above.
    resolved_rows = []
    for row in ingredient_rows:
        try:
            material = Product.objects.get(pk=row["material_id"], product_type="MATERIAL")
        except (Product.DoesNotExist, KeyError):
            raise ProductionError(f"Material with id {row.get('material_id')} not found.")

        quantity_used = int(row.get("quantity_used", 0))
        if quantity_used <= 0:
            raise ProductionError(f"Quantity used for {material.name} must be greater than zero.")

        inventory, _ = Inventory.objects.get_or_create(branch=commissary, product=material)
        if inventory.quantity_on_hand < quantity_used:
            raise ProductionError(
                f"Not enough {material.name} on hand at the commissary "
                f"(have {inventory.quantity_on_hand}, need {quantity_used})."
            )
        resolved_rows.append((material, quantity_used, inventory))

    with db_transaction.atomic():
        record = ProductionRecord.objects.create(
            commissary_staff=commissary_staff,
            product=product,
            quantity_produced=quantity_produced,
            supplier=extra.get("supplier", ""),
            quality=extra.get("quality", ProductionRecord.Quality.PASS),
            status=extra.get("status", ProductionRecord.Status.COMPLETED),
            notes=extra.get("notes", ""),
        )

        for material, quantity_used, inventory in resolved_rows:
            IngredientUsage.objects.create(
                production_record=record, material=material, quantity_used=quantity_used
            )
            inventory.quantity_on_hand -= quantity_used
            inventory.save()
            InventoryTransaction.objects.create(
                branch=commissary,
                product=material,
                movement_type=InventoryTransaction.MovementType.PRODUCTION_CONSUMPTION,
                quantity_change=-quantity_used,
            )

        finished_good_inventory, _ = Inventory.objects.get_or_create(
            branch=commissary, product=product
        )
        finished_good_inventory.quantity_on_hand += quantity_produced
        finished_good_inventory.save()
        InventoryTransaction.objects.create(
            branch=commissary,
            product=product,
            movement_type=InventoryTransaction.MovementType.PRODUCTION_OUTPUT,
            quantity_change=quantity_produced,
        )

    return record


# Forward-only lifecycle -- the only legal "next status" from each status.
# Enforced in advance_stock_transfer_status rather than left to callers,
# so skipping a step or going backward is a service-level error, not a
# silently-accepted write.
_NEXT_TRANSFER_STATUS = {
    StockTransferOrder.Status.PROCESSING: StockTransferOrder.Status.IN_TRANSIT,
    StockTransferOrder.Status.IN_TRANSIT: StockTransferOrder.Status.DELIVERED,
}


def create_stock_transfer_order(commissary_staff, destination_branch_id, product_id, quantity):
    """Dispatches finished goods from the commissary to a branch (Fig.
    3-24/3-27's "stock order" -- processing/in transit/delivered).

    Stock leaves the commissary's own Inventory immediately, the same
    moment the order is created (status=PROCESSING) -- once dispatched
    it is genuinely no longer on hand there, matching how record_production
    above deducts materials up front rather than waiting for some later
    confirmation step.
    """
    from apps.accounts.models import Branch

    commissary = get_commissary_branch()
    if commissary is None:
        raise StockTransferError("No commissary branch is configured.")

    try:
        destination = Branch.objects.get(pk=destination_branch_id)
    except Branch.DoesNotExist:
        raise StockTransferError("Destination branch does not exist.")

    if destination.pk == commissary.pk:
        raise StockTransferError("Destination branch cannot be the commissary itself.")
    if destination.is_commissary:
        raise StockTransferError("Destination branch cannot be another commissary.")

    try:
        product = Product.objects.get(pk=product_id)
    except Product.DoesNotExist:
        raise StockTransferError("Selected product does not exist.")

    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise StockTransferError("Enter a whole number for the quantity.")
    if quantity <= 0:
        raise StockTransferError("Quantity must be greater than zero.")

    with db_transaction.atomic():
        source_inventory, _ = Inventory.objects.get_or_create(branch=commissary, product=product)
        if source_inventory.quantity_on_hand < quantity:
            raise StockTransferError(
                f"Not enough {product.name} on hand at the commissary "
                f"(have {source_inventory.quantity_on_hand}, need {quantity})."
            )

        order = StockTransferOrder.objects.create(
            commissary_staff=commissary_staff,
            source_branch=commissary,
            destination_branch=destination,
            product=product,
            quantity=quantity,
            status=StockTransferOrder.Status.PROCESSING,
        )

        source_inventory.quantity_on_hand -= quantity
        source_inventory.save()
        InventoryTransaction.objects.create(
            branch=commissary,
            product=product,
            movement_type=InventoryTransaction.MovementType.TRANSFER_OUT,
            quantity_change=-quantity,
        )

    return order


def advance_stock_transfer_status(order, new_status):
    """Moves a StockTransferOrder forward one step
    (PROCESSING -> IN_TRANSIT -> DELIVERED only -- no skipping, no going
    backward). Reaching DELIVERED is the one moment the destination
    branch's own Inventory actually receives the stock; while IN_TRANSIT,
    the quantity is accounted for by this order alone, not counted as
    available at either branch.
    """
    allowed_next = _NEXT_TRANSFER_STATUS.get(order.status)
    if allowed_next is None:
        raise StockTransferError(
            f"Order is already {order.get_status_display()} -- "
            "no further status change is possible."
        )
    if new_status != allowed_next:
        raise StockTransferError(
            f"Cannot move from {order.get_status_display()} directly to "
            f"{StockTransferOrder.Status(new_status).label} -- the next step must be "
            f"{allowed_next.label}."
        )

    with db_transaction.atomic():
        order.status = new_status
        if new_status == StockTransferOrder.Status.DELIVERED:
            order.delivered_at = timezone.now()
            order.save()

            destination_inventory, _ = Inventory.objects.get_or_create(
                branch=order.destination_branch, product=order.product
            )
            destination_inventory.quantity_on_hand += order.quantity
            destination_inventory.save()
            InventoryTransaction.objects.create(
                branch=order.destination_branch,
                product=order.product,
                movement_type=InventoryTransaction.MovementType.TRANSFER_IN,
                quantity_change=order.quantity,
            )
        else:
            order.save()

    return order
