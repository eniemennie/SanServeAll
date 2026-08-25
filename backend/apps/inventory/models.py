"""
Products, stock levels, stock movement, batch materials.

The Product model below is intentionally minimal -- POS (Week 4) needs a
sellable catalog before Inventory's own stock-tracking features exist
(Week 6, per the Phase 9 timeline). Week 6 extends this same model with
stock levels, reorder thresholds, and the full Inventory module rather
than replacing it, so POS's FK references here remain valid.

Inventory and InventoryTransaction below are ALSO intentionally minimal --
they exist now only to support the Week 5 real-time deduction hook (a
sale reduces stock on hand). Reorder thresholds, low-stock alerts, batch
tracking, and the actual Inventory Monitoring screens are Week 6 work.
"""

from django.db import models


class Product(models.Model):
    name = models.CharField(max_length=150)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Inventory(models.Model):
    """Current stock on hand for a product at a specific branch."""

    branch = models.ForeignKey("accounts.Branch", on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity_on_hand = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "product"], name="unique_inventory_per_branch_product"
            )
        ]
        verbose_name_plural = "Inventory"

    def __str__(self):
        return f"{self.product.name} @ {self.branch.name}: {self.quantity_on_hand}"


class InventoryTransaction(models.Model):
    """Logs every stock movement for traceability (Phase 1 ERD). Week 5
    only ever writes SALE_DEDUCTION rows; RESTOCK, batch-linked movements,
    etc. are added in Week 6/7 as those features land."""

    class MovementType(models.TextChoices):
        SALE_DEDUCTION = "SALE_DEDUCTION", "Sale Deduction"
        MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT", "Manual Adjustment"

    branch = models.ForeignKey("accounts.Branch", on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    movement_type = models.CharField(max_length=32, choices=MovementType.choices)
    # Negative for deductions, positive for additions -- signed so a
    # simple sum() over a product's transactions reconstructs its history.
    quantity_change = models.IntegerField()
    # String FK: avoids a hard import-order dependency between inventory
    # and pos, since pos.SalesItem already references inventory.Product.
    related_sales_item = models.ForeignKey(
        "pos.SalesItem", on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.movement_type} {self.quantity_change} x {self.product.name}"
