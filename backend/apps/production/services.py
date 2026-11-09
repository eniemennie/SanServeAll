"""
Production Module business logic (Week 8): recording a production run
converts raw materials into finished goods at the commissary -- both
sides happen atomically against the SAME Inventory model Week 5/6 already
built (branch=commissary), rather than a separate stock-tracking system.
"""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

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


def get_commissary_relevant_product_ids():
    """Which products the commissary actually produces or uses -- the
    Commissary Dashboard's "Materials Needing Attention" card (client
    review, 2026-11-09) needs this to stop showing branch-only POS menu
    items (Americano, Carbonara, Lasagna, ...) as if they were something
    the commissary stocks. Without this filter, get_resource_status_summary
    returns every Inventory row at the commissary branch, including rows
    for products the commissary never produces -- an artifact of every
    product getting an Inventory row per branch (including the commissary)
    somewhere upstream, not a real shortage.

    Two groups count as "relevant":
      - Every MATERIAL product (raw food materials + packaging) -- these
        are the production inputs; per get_resource_status_summary's own
        docstring, materials only ever have real Inventory rows at the
        commissary in the first place.
      - FINISHED_GOOD products the commissary has actually produced at
        least once (i.e. appear in ProductionRecord) -- its own baked-
        goods output waiting to be distributed to branches. Deliberately
        derived from real production history rather than a hardcoded
        category list (Cakes/Desserts/Bakery today could change), so this
        stays correct as the catalog evolves.
    """
    material_ids = Product.objects.filter(product_type=Product.ProductType.MATERIAL).values_list(
        "pk", flat=True
    )
    produced_finished_good_ids = (
        ProductionRecord.objects.filter(product__product_type=Product.ProductType.FINISHED_GOOD)
        .values_list("product_id", flat=True)
        .distinct()
    )
    return list(material_ids) + list(produced_finished_good_ids)


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

    `ingredient_rows` is a list of {"material_id": int, "quantity_used": Decimal}
    dicts -- quantity_used accepts a Decimal, or anything Decimal() can parse
    (an int, or a numeric string such as "0.05"), matching
    IngredientUsage.quantity_used's own DecimalField(decimal_places=3): raw
    materials are commonly measured in fractional kg/L, so a whole-number-only
    type here would make it impossible to log a perfectly normal usage amount.
    Every material must have sufficient stock at the commissary --
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

        try:
            quantity_used = Decimal(str(row.get("quantity_used", 0)))
        except InvalidOperation:
            raise ProductionError(f"Invalid quantity for {material.name}.")
        if quantity_used <= 0:
            raise ProductionError(f"Quantity used for {material.name} must be greater than zero.")

        inventory, _ = Inventory.objects.get_or_create(branch=commissary, product=material)
        if inventory.quantity_on_hand < quantity_used:
            raise ProductionError(
                f"Not enough {material.name} on hand at the commissary "
                f"(have {inventory.quantity_on_hand}, need {quantity_used})."
            )
        resolved_rows.append((material, quantity_used, inventory))

    status = extra.get("status", ProductionRecord.Status.COMPLETED)
    quality = extra.get("quality", ProductionRecord.Quality.PASS)

    with db_transaction.atomic():
        record = ProductionRecord.objects.create(
            commissary_staff=commissary_staff,
            product=product,
            quantity_produced=quantity_produced,
            supplier=extra.get("supplier", ""),
            quality=quality,
            status=status,
            notes=extra.get("notes", ""),
        )

        # Backend gap assessment item 3 (Phase 1, critical): inventory
        # should only move once a run has actually finished. A run still
        # PENDING/IN_PROGRESS hasn't happened yet -- recording one lets
        # staff log the plan without pretending materials were already
        # consumed, so neither side of the ledger moves until COMPLETED.
        if status != ProductionRecord.Status.COMPLETED:
            return record

        for material, quantity_used, inventory in resolved_rows:
            IngredientUsage.objects.create(
                production_record=record, material=material, quantity_used=quantity_used
            )
            # Materials are consumed by the attempt itself, regardless of
            # whether the output later passes QC -- a failed batch still
            # used up real flour and sugar, so this deduction happens
            # either way.
            inventory.quantity_on_hand -= quantity_used
            inventory.save()
            InventoryTransaction.objects.create(
                branch=commissary,
                product=material,
                movement_type=InventoryTransaction.MovementType.PRODUCTION_CONSUMPTION,
                quantity_change=-quantity_used,
            )

        # Only QUALITY.PASS output is fit to sell -- crediting a failed
        # batch to the finished-good shelf would let a branch's POS sell
        # it. A FAIL run still shows up in the batch log and still
        # consumed its materials above; it just never becomes sellable
        # stock.
        if quality != ProductionRecord.Quality.PASS:
            return record

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

    Backend gap assessment item 26 (same class of risk as the POS
    double-submission race): locks the commissary's Inventory row with
    select_for_update() before checking/deducting stock, inside the
    atomic block. Without this, two near-simultaneous submissions (a
    double-click, or a network retry) could both read the same
    quantity_on_hand, both pass the "enough stock" check, and both
    deduct -- creating two orders and debiting stock twice against a
    quantity that only really covered one. With the lock, the second
    request's read blocks until the first commits, then sees the
    already-reduced quantity and is correctly rejected if there isn't
    enough left.
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
        source_inventory = Inventory.objects.select_for_update().get(pk=source_inventory.pk)
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


def cancel_stock_transfer_order(order, cancelled_by=None):
    """Cancels a StockTransferOrder (backend gap assessment item 24),
    returning its quantity to the commissary's own Inventory -- a
    reversal of the deduction create_stock_transfer_order made up front.

    `cancelled_by` (task #166, adviser dry-run 2026-11-05) records WHO
    performed the cancellation -- optional and defaults to None so
    existing callers (and the management command / backfill paths that
    cancel orders without a real acting user) keep working unchanged;
    the view below always passes request.user.

    Only legal from PROCESSING or IN_TRANSIT: in both states the stock
    already left the commissary and hasn't yet reached the destination
    branch, so there's nothing at the destination to undo. Once
    DELIVERED, the stock is already real, on-hand inventory at the
    destination branch -- reversing that is a distinct business event
    (a return/correction), not a cancellation of this transfer, so it's
    deliberately not allowed here. No separate approval step is required
    (same scope decision as the rest of this stock-transfer workflow).

    Verification pass finding (2026-11-04), same risk class as backend
    gap assessment item 26 (create_stock_transfer_order's own
    double-submission fix, above): the original version checked and
    mutated the caller's already-loaded `order` object directly, with no
    row lock. Two near-simultaneous cancel requests for the same order
    (double-click, or a network retry) each fetch their OWN separate
    `order` instance via the view's plain get_object_or_404 -- both could
    read status=PROCESSING, both pass this function's status check, and
    both add `quantity` back to the commissary's Inventory, crediting it
    twice for one real cancellation. Fixed the same way as item 26: the
    order itself is re-fetched with select_for_update() inside the
    atomic block and re-checked there, so the second request blocks
    until the first commits, then sees CANCELLED and is correctly
    rejected -- the caller's passed-in `order` is only used for its pk.
    """
    with db_transaction.atomic():
        locked_order = StockTransferOrder.objects.select_for_update().get(pk=order.pk)
        if locked_order.status not in (
            StockTransferOrder.Status.PROCESSING,
            StockTransferOrder.Status.IN_TRANSIT,
        ):
            raise StockTransferError(
                f"Order is {locked_order.get_status_display()} -- only a Processing or "
                "In Transit order can be cancelled."
            )

        locked_order.status = StockTransferOrder.Status.CANCELLED
        locked_order.cancelled_at = timezone.now()
        locked_order.cancelled_by = cancelled_by
        locked_order.save()

        source_inventory, _ = Inventory.objects.get_or_create(
            branch=locked_order.source_branch, product=locked_order.product
        )
        source_inventory = Inventory.objects.select_for_update().get(pk=source_inventory.pk)
        source_inventory.quantity_on_hand += locked_order.quantity
        source_inventory.save()
        InventoryTransaction.objects.create(
            branch=locked_order.source_branch,
            product=locked_order.product,
            movement_type=InventoryTransaction.MovementType.TRANSFER_CANCELLED,
            quantity_change=locked_order.quantity,
        )

    return locked_order


def advance_stock_transfer_status(order, new_status, acted_by=None):
    """Moves a StockTransferOrder forward one step
    (PROCESSING -> IN_TRANSIT -> DELIVERED only -- no skipping, no going
    backward). Reaching DELIVERED is the one moment the destination
    branch's own Inventory actually receives the stock; while IN_TRANSIT,
    the quantity is accounted for by this order alone, not counted as
    available at either branch.

    `acted_by` (task #166, adviser dry-run 2026-11-05) records WHO
    advanced the order, but is only ever stored as `delivered_by` --
    there's no "who marked it In Transit" field, since the ticket only
    asked to track who delivered or cancelled an order, not every
    intermediate step. Optional and defaults to None so existing
    callers keep working unchanged; the view below always passes
    request.user.

    Backend gap assessment item 25: `new_status` is validated against the
    real set of status values before it's used anywhere, including in an
    error message -- `StockTransferOrder.Status(new_status)` raises a
    bare ValueError for anything that isn't a recognized value (garbage
    input, or simply a missing/None POST field), which previously
    surfaced as an uncaught 500 instead of the StockTransferError the
    view already knows how to handle gracefully.

    Verification pass finding (2026-11-04), same risk class as backend
    gap assessment item 26: the original version checked and mutated
    the caller's already-loaded `order` object directly, with no row
    lock -- two near-simultaneous "mark Delivered" requests for the same
    order could each pass the status check against their own stale copy
    and each credit the destination branch's Inventory once, doubling
    real stock that only actually arrived once. The order is now
    re-fetched with select_for_update() inside the atomic block and
    every check re-run against that locked row, same fix shape as
    cancel_stock_transfer_order above -- the caller's passed-in `order`
    is only used for its pk.
    """
    valid_statuses = {choice.value for choice in StockTransferOrder.Status}
    if new_status not in valid_statuses:
        raise StockTransferError(f"'{new_status}' is not a recognized stock transfer status.")

    with db_transaction.atomic():
        locked_order = StockTransferOrder.objects.select_for_update().get(pk=order.pk)

        allowed_next = _NEXT_TRANSFER_STATUS.get(locked_order.status)
        if allowed_next is None:
            raise StockTransferError(
                f"Order is already {locked_order.get_status_display()} -- "
                "no further status change is possible."
            )
        if new_status != allowed_next:
            raise StockTransferError(
                f"Cannot move from {locked_order.get_status_display()} directly to "
                f"{StockTransferOrder.Status(new_status).label} -- the next step must be "
                f"{allowed_next.label}."
            )

        locked_order.status = new_status
        if new_status == StockTransferOrder.Status.DELIVERED:
            locked_order.delivered_at = timezone.now()
            locked_order.delivered_by = acted_by
            locked_order.save()

            destination_inventory, _ = Inventory.objects.get_or_create(
                branch=locked_order.destination_branch, product=locked_order.product
            )
            destination_inventory = Inventory.objects.select_for_update().get(
                pk=destination_inventory.pk
            )
            destination_inventory.quantity_on_hand += locked_order.quantity
            destination_inventory.save()
            InventoryTransaction.objects.create(
                branch=locked_order.destination_branch,
                product=locked_order.product,
                movement_type=InventoryTransaction.MovementType.TRANSFER_IN,
                quantity_change=locked_order.quantity,
            )
        else:
            locked_order.save()

    return locked_order
