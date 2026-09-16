"""
Production Module business logic (Week 8): recording a production run
converts raw materials into finished goods at the commissary -- both
sides happen atomically against the SAME Inventory model Week 5/6 already
built (branch=commissary), rather than a separate stock-tracking system.
"""

from django.db import transaction as db_transaction

from apps.inventory.models import Inventory, InventoryTransaction, Product
from apps.production.models import IngredientUsage, ProductionRecord


class ProductionError(Exception):
    """Raised when a production run can't be recorded as requested."""


def get_commissary_branch():
    """There is exactly one commissary (Phase 1 §1.1) -- this is the
    single lookup point for it, so nothing else needs to hardcode a
    branch name or code to find it."""
    from apps.accounts.models import Branch

    return Branch.objects.filter(is_commissary=True).first()


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
