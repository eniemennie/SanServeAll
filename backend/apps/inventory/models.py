"""
Products, stock levels, stock movement, batch materials.

The Product model below is intentionally minimal -- POS (Week 4) needs a
sellable catalog before Inventory's own stock-tracking features exist
(Week 6, per the Phase 9 timeline). Week 6 extends this same model with
stock levels, reorder thresholds, and the full Inventory module rather
than replacing it, so POS's FK references here remain valid.
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
