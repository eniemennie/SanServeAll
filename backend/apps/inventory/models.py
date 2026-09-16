"""
Products, stock levels, stock movement, batch materials.

Week 6 extends Product and Inventory with the fields the Inventory Module
actually needs (reorder threshold, product type distinguishing finished
goods from raw materials) -- rather than replacing what Week 4/5 already
built, so POS's and the deduction hook's existing FK references and
behavior stay valid unchanged.
"""

from django.db import models


class Product(models.Model):
    class ProductType(models.TextChoices):
        FINISHED_GOOD = "FINISHED_GOOD", "Finished Good"
        MATERIAL = "MATERIAL", "Raw Material"

    class Category(models.TextChoices):
        COFFEE = "COFFEE", "Coffee"
        COFFEE_FRAPPE = "COFFEE_FRAPPE", "Coffee Frappe"
        NON_COFFEE_FRAPPE = "NON_COFFEE_FRAPPE", "Non-Coffee Frappe"
        SEA_SALT_SERIES = "SEA_SALT_SERIES", "Sea Salt Series"
        SPECIALTY = "SPECIALTY", "Specialty"
        BREAKFAST = "BREAKFAST", "Breakfast"
        PASTA = "PASTA", "Pasta"
        DESSERTS = "DESSERTS", "Desserts"
        CAKES = "CAKES", "Cakes"

    name = models.CharField(max_length=150)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    # Set only for products with a Large size option (Row 3 redesign,
    # Product Customization modal) -- `price` above is always the Small/
    # base price. Left null for products with no size variants at all
    # (pasta, cakes, etc. -- confirmed by the reference showing "Lasagna"
    # added to cart with no size noted, unlike "Spanish Latte (Small)").
    large_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # MATERIAL rows (flour, sugar, cups, etc. -- Phase 1 SS1.1) are not sold
    # directly through POS; `price` still applies as their unit cost for
    # inventory valuation, just not as a sellable catalog price.
    product_type = models.CharField(
        max_length=32, choices=ProductType.choices, default=ProductType.FINISHED_GOOD
    )
    # Blank for MATERIAL rows -- categories are a sellable-catalog concept
    # (Row 3 redesign, POS category tabs), materials aren't browsed there.
    category = models.CharField(max_length=32, choices=Category.choices, blank=True)
    is_best_seller = models.BooleanField(default=False)
    # Below this quantity_on_hand, Inventory.is_low_stock flags the item
    # (Row 6.4). Zero means "don't flag" rather than "always flag" -- a
    # product with no threshold configured yet shouldn't spam alerts.
    reorder_threshold = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def has_size_options(self):
        return self.large_price is not None

    # Categories that get the full customization modal (size, sugar
    # level, add-ons -- Row 3 redesign). Everything else (pasta,
    # breakfast, desserts, cakes) gets a simpler quantity + discount
    # flow, matching the reference showing "Lasagna" added with none of
    # those options shown at all.
    BEVERAGE_CATEGORIES = {
        Category.COFFEE,
        Category.COFFEE_FRAPPE,
        Category.NON_COFFEE_FRAPPE,
        Category.SEA_SALT_SERIES,
        Category.SPECIALTY,
    }

    @property
    def is_beverage(self):
        return self.category in self.BEVERAGE_CATEGORIES


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

    @property
    def is_out_of_stock(self):
        return self.quantity_on_hand <= 0

    @property
    def is_low_stock(self):
        """True when stock is at or below the product's configured reorder
        threshold, but still above zero (out-of-stock is its own, more
        urgent status -- Fig. 3-20/3-32 distinguish the two)."""
        if self.product.reorder_threshold <= 0:
            return False
        return 0 < self.quantity_on_hand <= self.product.reorder_threshold

    @property
    def status_label(self):
        if self.is_out_of_stock:
            return "Out of Stock"
        if self.is_low_stock:
            return "Low Stock"
        return "Available"


class InventoryTransaction(models.Model):
    """Logs every stock movement for traceability (Phase 1 ERD). Week 5
    only ever writes SALE_DEDUCTION rows; RESTOCK, batch-linked movements,
    etc. are added in Week 6/7 as those features land."""

    class MovementType(models.TextChoices):
        SALE_DEDUCTION = "SALE_DEDUCTION", "Sale Deduction"
        MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT", "Manual Adjustment"
        # Week 8: distinct from MANUAL_ADJUSTMENT so the audit trail can
        # actually distinguish "an admin corrected a miscount" from "the
        # commissary consumed/produced stock via a production run" --
        # both are automatic, neither is a manual override.
        PRODUCTION_CONSUMPTION = "PRODUCTION_CONSUMPTION", "Production Consumption"
        PRODUCTION_OUTPUT = "PRODUCTION_OUTPUT", "Production Output"

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
