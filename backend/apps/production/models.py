"""
Commissary production tracking (Week 8): recording production output and
ingredient usage. Fig. 3-24's "Batch Management Interface" maps to
ProductionRecord here -- one record per production run, distinct from the
KaHero "batch" concept (Week 7), which is a file-upload batch, not a
production run. Same word, two different real-world things in the
manuscript; kept as separate models rather than forced into one.
"""

from django.conf import settings
from django.db import models


class ProductionRecord(models.Model):
    class Quality(models.TextChoices):
        PASS = "PASS", "Pass"
        FAIL = "FAIL", "Fail"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        COMPLETED = "COMPLETED", "Completed"

    commissary_staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    product = models.ForeignKey(
        "inventory.Product",
        on_delete=models.PROTECT,
        help_text="The finished good produced by this batch.",
    )
    quantity_produced = models.PositiveIntegerField()
    supplier = models.CharField(max_length=150, blank=True)
    quality = models.CharField(max_length=8, choices=Quality.choices, default=Quality.PASS)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Batch #{self.pk}: {self.quantity_produced} x {self.product.name}"


class StockTransferOrder(models.Model):
    """Tracks finished goods moving from the Commissary to a branch
    (manuscript's "Supply distribution record updates per branch" --
    Production/Commissary FR group -- and the "Stock order status
    tracking (processing, in transit, delivered)" bullet under FR-02).

    Deliberately a separate model from ProductionRecord: production is
    "commissary made X units of product Y", distribution is "commissary
    sent N of its already-on-hand units to branch Z" -- two different
    real-world events, and ProductionRecord's own docstring already notes
    there's no honest way to attribute a production run to a destination
    branch. Before this model existed, output produced at the commissary
    had no way to ever reach a branch's own Inventory at all.

    Status is forward-only (see services.advance_stock_transfer_status):
    PROCESSING -> IN_TRANSIT -> DELIVERED. Stock leaves the commissary's
    Inventory the moment an order is created (PROCESSING) -- once
    dispatched, it isn't available there anymore -- and only lands in the
    destination branch's Inventory once DELIVERED is confirmed. While
    IN_TRANSIT, the quantity is accounted for by this row alone, not
    double-counted as available at either branch.
    """

    class Status(models.TextChoices):
        PROCESSING = "PROCESSING", "Processing"
        IN_TRANSIT = "IN_TRANSIT", "In Transit"
        DELIVERED = "DELIVERED", "Delivered"

    commissary_staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="stock_transfer_orders",
        help_text="Who recorded/dispatched this order.",
    )
    source_branch = models.ForeignKey(
        "accounts.Branch",
        on_delete=models.PROTECT,
        related_name="stock_transfers_sent",
        help_text="Always the commissary -- kept explicit (not hardcoded) for auditability.",
    )
    destination_branch = models.ForeignKey(
        "accounts.Branch",
        on_delete=models.PROTECT,
        related_name="stock_transfers_received",
    )
    product = models.ForeignKey(
        "inventory.Product",
        on_delete=models.PROTECT,
        help_text="The finished good being shipped to the destination branch.",
    )
    quantity = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PROCESSING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    delivered_at = models.DateTimeField(
        null=True, blank=True, help_text="Set only when status actually reaches DELIVERED."
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"Transfer #{self.pk}: {self.quantity} x {self.product.name} "
            f"{self.source_branch.code} -> {self.destination_branch.code} ({self.status})"
        )


class IngredientUsage(models.Model):
    """One raw material consumed by a ProductionRecord. A single
    production run typically uses several materials (flour, sugar,
    butter, etc. -- Phase 1 §1.1), so this is a one-to-many child of
    ProductionRecord, not a single field on it."""

    production_record = models.ForeignKey(
        ProductionRecord, on_delete=models.CASCADE, related_name="ingredient_usages"
    )
    material = models.ForeignKey(
        "inventory.Product",
        on_delete=models.PROTECT,
        limit_choices_to={"product_type": "MATERIAL"},
    )
    quantity_used = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.quantity_used} x {self.material.name}"
