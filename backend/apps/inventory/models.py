"""
Products, stock levels, stock movement, batch materials.

Week 6 extends Product and Inventory with the fields the Inventory Module
actually needs (reorder threshold, product type distinguishing finished
goods from raw materials) -- rather than replacing what Week 4/5 already
built, so POS's and the deduction hook's existing FK references and
behavior stay valid unchanged.
"""

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Lower


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
        # Task #148 (client review, 2026-11-04): the only Category value
        # that applies to a MATERIAL row, not a sellable FINISHED_GOOD --
        # every choice above this line is a POS category-tab concept
        # (Row 3 redesign) and stays finished-goods-only. A raw/food
        # material (flour, sugar, ice, ...) is deliberately left with a
        # BLANK category rather than inventing a "Raw Material" value for
        # it: the only real distinction the client asked for is
        # "Packaging vs Food", and food materials already read clearly as
        # "not Packaging" without a second label. See
        # import_raw_and_packaging_materials.py, which tags this from
        # Packaging_Materials.xlsx specifically -- it is never guessed
        # from unit_of_measure (packaging items aren't reliably ROLL; many
        # are PIECE/PACK/BOX, the same units raw materials also use).
        PACKAGING = "PACKAGING", "Packaging"

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
        constraints = [
            # Task #111 (backend gap assessment): nothing previously stopped
            # two products sharing a name -- found live as a real duplicate
            # pair ("Seasalt Latte", ids 3/4), resolved by deactivating the
            # orphaned one rather than deleting it. Scoped to is_active=True
            # (a partial unique index, same idiom as
            # accounts.Branch.is_kahero_branch's own constraint) rather than
            # a blanket constraint, for two reasons: (1) it must coexist
            # with exactly that already-resolved live pair -- a blanket
            # constraint would fail to even apply as a migration against
            # that real data, since both rows carry the literal identical
            # name regardless of is_active; (2) discontinuing a product and
            # later introducing a genuine replacement under the same name
            # is a legitimate catalog change, not a duplicate. Case-
            # insensitive (via Lower()) because kahero_integration's own
            # import matching (_resolve_product) already treats "Flour" and
            # "flour" as the same product -- two active rows differing only
            # by case would be exactly the kind of ambiguous pair that
            # pipeline can't disambiguate.
            models.UniqueConstraint(
                Lower("name"),
                condition=models.Q(is_active=True),
                name="unique_active_product_name_ci",
            ),
        ]

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


class ProductPackaging(models.Model):
    """Packaging consumed per unit of a sold finished good (Consolidated
    Task Tracker "Packaging material consumption tracking" -- 2026-11-07
    scoped options, Option A). A lightweight packaging bill-of-materials:
    e.g. one Spanish Latte sold consumes 1 cup + 1 lid.

    Deliberately not apps.production.IngredientUsage -- that model ties
    consumption to a ProductionRecord (a commissary batch); this ties
    consumption to a *sale* of a finished good (POS), which is how
    cups/lids/straws are actually used up, one sold drink at a time,
    not per production batch. (Packaging that genuinely is consumed per
    batch, e.g. takeout boxes assembled at the commissary, can already
    be logged as an ordinary IngredientUsage row today -- its `material`
    FK only requires product_type=MATERIAL, which every packaging
    Product already is; see import_raw_and_packaging_materials.py.
    That's a separate, already-working path, not this one.)

    No mapping exists until someone (Owner/Admin or Purchasing) adds one
    here -- deliberately opt-in rather than guessed, since not every
    finished good uses packaging the same way (e.g. a dine-in order may
    use none at all)."""

    finished_good = models.ForeignKey(
        "inventory.Product",
        on_delete=models.CASCADE,
        related_name="packaging_requirements",
        limit_choices_to={"product_type": "FINISHED_GOOD"},
    )
    packaging_material = models.ForeignKey(
        "inventory.Product",
        on_delete=models.PROTECT,
        related_name="used_as_packaging_for",
        limit_choices_to={"category": "PACKAGING"},
    )
    quantity_per_unit = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("1"),
        validators=[MinValueValidator(Decimal("0.001"))],
        help_text="How much of this packaging material one unit sold of the finished "
        "good consumes, e.g. 1 cup + 1 lid per drink.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["finished_good", "packaging_material"],
                name="unique_packaging_per_finished_good",
            )
        ]
        verbose_name_plural = "Product Packaging"

    def __str__(self):
        return (
            f"{self.quantity_per_unit} x {self.packaging_material.name} "
            f"per {self.finished_good.name}"
        )


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
    # 3 places, not 2: widened for Ingredient_Usage.xlsx, which records
    # some material consumption to a third decimal place (e.g. Cream of
    # tartar: 0.019kg) -- a production run's deduction from this field
    # must never be rounded at the boundary.
    quantity_on_hand = models.DecimalField(max_digits=12, decimal_places=3, default=0)
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
        max_digits=12, decimal_places=3, default=0, validators=[MinValueValidator(0)]
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
        # Task #104: the historical client import (Production_Records.xlsx,
        # Stock_Transfer.xlsx) never touched Inventory at all (see those
        # import commands' own docstrings), which left every branch's
        # finished-goods Inventory at zero. backfill_finished_goods_inventory
        # replays that history through this same Inventory/InventoryTransaction
        # model, reconstructing real production-output and stock-transfer
        # movements -- but it is a one-off reconstruction run long after the
        # fact, not a live event a commissary/branch staffer actually recorded
        # in real time. Logged under its own type (rather than reusing
        # PRODUCTION_OUTPUT/TRANSFER_OUT/TRANSFER_IN) so: (1) the audit trail
        # never misrepresents this as a real-time staff action, and (2) this
        # command can detect its own prior run and refuse to double-apply.
        HISTORICAL_BACKFILL = "HISTORICAL_BACKFILL", "Historical Backfill"
        # Consolidated Task Tracker "Purchasing/Receiving records model"
        # (2026-11-07 scoped options, Option B): this module's docstring
        # above literally names RESTOCK as planned since Week 5 -- it's
        # only being added now because nothing in the codebase could
        # ever receive a delivery before apps.purchasing.PurchaseOrder
        # existed. Logged when a PurchaseOrder is marked RECEIVED (see
        # apps.purchasing.services.receive_purchase_order), using the
        # order's actual received_quantity per line, not the quantity
        # originally ordered -- distinct from PRODUCTION_OUTPUT (the
        # commissary made it) and MANUAL_ADJUSTMENT (a staff correction
        # with no external source): this is specifically stock arriving
        # from an outside supplier.
        RESTOCK = "RESTOCK", "Restock (Purchase Order Received)"
        # Consolidated Task Tracker "Packaging material consumption
        # tracking" (2026-11-07 scoped options, Option A): logged
        # alongside SALE_DEDUCTION whenever a sold item has a configured
        # ProductPackaging row -- see apps.pos.services.
        # _deduct_inventory_for_sale, which now deducts the mapped
        # packaging (cups, lids, etc.) right after the finished good
        # itself, for the same SalesItem. Distinct from SALE_DEDUCTION
        # so the audit trail can tell "the item sold" apart from "the
        # packaging consumed to sell it" at a glance.
        PACKAGING_CONSUMPTION = "PACKAGING_CONSUMPTION", "Packaging Consumption"

    branch = models.ForeignKey("accounts.Branch", on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    movement_type = models.CharField(max_length=32, choices=MovementType.choices)
    # Negative for deductions, positive for additions -- signed so a
    # simple sum() over a product's transactions reconstructs its history.
    # Decimal, matching Inventory.quantity_on_hand above, for the same
    # reason: a raw-material adjustment can be a fractional kg/liter
    # amount in the client's real data.
    quantity_change = models.DecimalField(max_digits=12, decimal_places=3)
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
