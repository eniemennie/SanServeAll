"""
Products, stock levels, stock movement, batch materials.

Week 6 extends Product and Inventory with the fields the Inventory Module
actually needs (reorder threshold, product type distinguishing finished
goods from raw materials) -- rather than replacing what Week 4/5 already
built, so POS's and the deduction hook's existing FK references and
behavior stay valid unchanged.
"""

from django.core.validators import MinValueValidator
from django.db import models


class Product(models.Model):
    class ProductType(models.TextChoices):
        FINISHED_GOOD = "FINISHED_GOOD", "Finished Good"
        MATERIAL = "MATERIAL", "Raw Material"

    class UnitOfMeasure(models.TextChoices):
        """Backend gap assessment item 28: quantities everywhere else in
        this project (quantity_on_hand, quantity_used, etc.) are plain
        integers with no unit attached -- 10 could mean 10 pieces, kg, or
        liters depending on the item. Purely descriptive for now (no
        conversion math exists or is needed yet); it exists so a person
        reading a quantity knows what it actually counts, and so any
        future recipe/BOM math has a real unit to validate against
        instead of silently assuming everything is in the same unit."""

        PIECE = "PIECE", "pc"
        KILOGRAM = "KILOGRAM", "kg"
        GRAM = "GRAM", "g"
        LITER = "LITER", "L"
        MILLILITER = "MILLILITER", "mL"
        PACK = "PACK", "pack"
        BOX = "BOX", "box"
        # Client raw-data import (Packaging_Materials.xlsx): rolls of
        # receipt/register tape, cling wrap, etc. -- distinct from PACK
        # (a bundle of discrete pieces), not a value any existing unit
        # could stand in for without losing information.
        ROLL = "ROLL", "roll"

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
        BAKERY = "BAKERY", "Bakery & Pasalubong"

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
    unit_of_measure = models.CharField(
        max_length=16, choices=UnitOfMeasure.choices, default=UnitOfMeasure.PIECE
    )
    # Client raw-data import (Raw_Materials.xlsx / Packaging_Materials.xlsx):
    # every material carries a real supplier name (e.g. "Juan Store",
    # "Restaurant Depot"), consistent across every branch's row for that
    # same material -- a genuine catalog-level fact with nowhere to live
    # until now. Blank for finished goods and any material whose supplier
    # isn't tracked yet.
    supplier = models.CharField(max_length=150, blank=True)
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
    # Decimal, not Integer: the client's real Raw_Materials.xlsx tracks
    # 175/206 raw-material rows to a fractional kg/liter (e.g. Ice: 0.3kg
    # on hand) -- rounding to a whole unit would misstate real stock
    # (0.3kg rounding to 0 is a false stockout; 0.9kg rounding to 1 hides
    # a near-empty supply), so this holds the client's own numbers
    # exactly rather than lying about them. Finished-goods counts (whole
    # pieces) are unaffected -- every whole number is still an exact
    # Decimal value.
    quantity_on_hand = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Backend gap assessment (client raw-data import): moved here from
    # Product.reorder_threshold -- the client's own Raw_Materials.xlsx /
    # Packaging_Materials.xlsx data showed real materials genuinely
    # needing a different reorder point per branch (e.g. a branch that
    # burns through coffee beans faster), which one global per-Product
    # value could never represent. Zero means "don't flag" rather than
    # "always flag" -- a row with no threshold configured yet shouldn't
    # spam alerts, same convention the old Product field used. Decimal
    # for the same reason as quantity_on_hand above -- 164/206 raw
    # materials have a fractional reorder point in the client's data.
    reorder_threshold = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
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
    def display_quantity(self):
        """QA fix: `quantity_on_hand` can genuinely go negative (the
        real-time sale-deduction hook deliberately allows overselling to
        drive it below zero -- see pos.services._deduct_inventory_for_sale
        -- so the AI risk-classification module has a real signal for
        "urgently oversold", not just "empty"). That's useful data, not a
        bug, but showing a raw "-1" to a cashier or owner reads as a
        glitch rather than a meaningful status. Every UI that shows this
        number to a person uses this instead of the raw field; anything
        computing real stock health, deductions, or feeding the AI
        model keeps using quantity_on_hand directly, unchanged."""
        return max(self.quantity_on_hand, 0)

    @property
    def is_low_stock(self):
        """True when stock is at or below this branch's own configured
        reorder threshold, but still above zero (out-of-stock is its own,
        more urgent status -- Fig. 3-20/3-32 distinguish the two)."""
        if self.reorder_threshold <= 0:
            return False
        return 0 < self.quantity_on_hand <= self.reorder_threshold

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
        # Week N: stock distribution from the commissary to a branch
        # (production.StockTransferOrder) -- OUT logged against the
        # commissary the moment an order is dispatched (PROCESSING), IN
        # logged against the destination branch only once DELIVERED is
        # confirmed.
        TRANSFER_OUT = "TRANSFER_OUT", "Transfer Out"
        TRANSFER_IN = "TRANSFER_IN", "Transfer In"
        # Backend gap assessment item 24: a cancelled StockTransferOrder
        # (PROCESSING or IN_TRANSIT only -- DELIVERED is final) returns
        # the quantity to the commissary's own Inventory. Logged
        # separately from TRANSFER_OUT/IN so the audit trail shows a
        # cancellation happened, not an ordinary second transfer.
        TRANSFER_CANCELLED = "TRANSFER_CANCELLED", "Transfer Cancelled"

    branch = models.ForeignKey("accounts.Branch", on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    movement_type = models.CharField(max_length=32, choices=MovementType.choices)
    # Negative for deductions, positive for additions -- signed so a
    # simple sum() over a product's transactions reconstructs its history.
    # Decimal, matching Inventory.quantity_on_hand above, for the same
    # reason: a raw-material adjustment can be a fractional kg/liter
    # amount in the client's real data.
    quantity_change = models.DecimalField(max_digits=12, decimal_places=2)
    # String FK: avoids a hard import-order dependency between inventory
    # and pos, since pos.SalesItem already references inventory.Product.
    related_sales_item = models.ForeignKey(
        "pos.SalesItem", on_delete=models.SET_NULL, null=True, blank=True
    )
    # Audit trail for MANUAL_ADJUSTMENT rows (backend gap assessment:
    # "who/why" fields were missing, so a stock correction left no record
    # of who made it or why). Left null/blank for every other movement
    # type, whose "why" is already implied by movement_type itself (a
    # sale, a production run, a transfer) -- only a manual override needs
    # a human-entered reason. SET_NULL rather than PROTECT: the audit row
    # itself must survive a staff account later being deleted.
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_adjustments",
    )
    reason = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.movement_type} {self.quantity_change} x {self.product.name}"
