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
