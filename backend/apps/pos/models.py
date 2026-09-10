"""
Point of Sale: sales transactions, line items, receipts.

A SalesTransaction starts life as a DRAFT -- built up item by item on the
Ordering Screen (Row 4) -- and is only marked COMPLETED once payment is
processed (Week 5). This lets the "current order in progress" live as a
real, queryable database row rather than fragile session state, and
survives a page refresh or accidental navigation away mid-order.
"""

from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import models


class DiscountCategory(models.TextChoices):
    """Per-item discount categories shown in the Product Customization
    modal (Row 3 redesign) -- matches the reference's exact dropdown
    list. Worth confirming the real PH discount-category codes (SC2,
    PWD2, CSC, CPWD, EMP, SD) with your adviser/BIR requirements, since
    these carry real legal/tax significance beyond just a UI label."""

    NO_DISCOUNT = "NO_DISCOUNT", "No Discount"
    SC2 = "SC2", "SC 2"
    PWD2 = "PWD2", "PWD 2"
    FAMILY = "FAMILY", "Family"
    CSC = "CSC", "CSC"
    CPWD = "CPWD", "CPWD"
    EMP = "EMP", "EMP"
    SPECIAL_DISCOUNT = "SPECIAL_DISCOUNT", "Special Discount"
    SD = "SD", "SD"


class DiscountType(models.TextChoices):
    AMOUNT = "AMOUNT", "Amount (\u20b1)"
    PERCENT = "PERCENT", "Percent (%)"


class AddOn(models.Model):
    """A priced extra for beverage customization (Row 3 redesign) --
    global across all beverage products (Extra Espresso Shot, Oat Milk,
    etc.), not per-product, matching the reference's consistent add-on
    list regardless of which drink was being customized."""

    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} (+\u20b1{self.price})"


class SalesTransaction(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        COMPLETED = "COMPLETED", "Completed"
        VOIDED = "VOIDED", "Voided"

    class PaymentMethod(models.TextChoices):
        CASH = "CASH", "Cash"
        GCASH = "GCASH", "GCash"
        CARD = "CARD", "Card"

    class DiningOption(models.TextChoices):
        DINE_IN = "DINE_IN", "Dine-in"
        TAKE_OUT = "TAKE_OUT", "Take-out"

    branch = models.ForeignKey("accounts.Branch", on_delete=models.PROTECT)
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    dining_option = models.CharField(
        max_length=16, choices=DiningOption.choices, default=DiningOption.DINE_IN
    )
    payment_method = models.CharField(max_length=32, choices=PaymentMethod.choices, blank=True)
    amount_tendered = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # A SEPARATE, additional discount applied to the whole order at once
    # (Row 3 redesign, "% Discount" button) -- layered on top of whatever
    # per-item discounts already exist (Batch 2's customization modal).
    # Per-item discounts already reduce each item's own unit_price, so
    # they're reflected in total_amount automatically; this is an
    # additional reduction beyond that.
    transaction_discount_category = models.CharField(max_length=32, blank=True)
    transaction_discount_type = models.CharField(max_length=16, blank=True)
    transaction_discount_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Transaction #{self.pk} ({self.status})"

    @property
    def total_amount(self):
        """The subtotal -- sum of item subtotals, each already reflecting
        its own per-item discount (Batch 2). Does NOT include the
        transaction-wide discount; see grand_total for that."""
        return sum((item.subtotal for item in self.items.all()), Decimal("0.00"))

    @property
    def item_discounts_total(self):
        """Sum of every line item's own per-item discount value (Batch 2)
        -- the "Discounts Applied" line in the Order Summary. Purely
        informational: those discounts are already baked into
        total_amount via each item's unit_price, this just surfaces the
        total for display."""
        total = Decimal("0.00")
        for item in self.items.all():
            value = item.customizations.get("discount_value") if item.customizations else None
            if value:
                try:
                    total += Decimal(str(value)) * item.quantity
                except (InvalidOperation, TypeError):
                    pass
        return total

    @property
    def transaction_discount_value(self):
        """The actual peso value of the transaction-wide discount (Row 3
        redesign, "% Discount"), capped so it can never exceed the
        subtotal it's being applied to."""
        if not self.transaction_discount_amount or not self.transaction_discount_category:
            return Decimal("0.00")

        subtotal = self.total_amount
        if self.transaction_discount_type == "PERCENT":
            value = subtotal * (self.transaction_discount_amount / Decimal("100"))
        else:
            value = self.transaction_discount_amount
        return min(value, subtotal)

    @property
    def grand_total(self):
        """The REAL final amount due -- subtotal minus the transaction-
        wide discount. This is what payment sufficiency, change, and
        receipts must all use, not total_amount alone."""
        return self.total_amount - self.transaction_discount_value

    def vat_breakdown(self, tax_rate_percent):
        """Splits grand_total into its VAT-exclusive amount and VAT
        portion, given grand_total is VAT-inclusive pricing (Row 3
        redesign) -- matches the reference's own displayed math exactly:
        VAT-exclusive = grand_total / (1 + rate), VAT = grand_total minus
        that. Takes the rate as a parameter rather than importing
        SystemConfiguration directly, keeping this model free of a
        cross-app import for one property."""
        grand_total = self.grand_total
        rate = Decimal(str(tax_rate_percent)) / Decimal("100")
        vat_exclusive = grand_total / (Decimal("1") + rate)
        vat_amount = grand_total - vat_exclusive
        return vat_exclusive.quantize(Decimal("0.01")), vat_amount.quantize(Decimal("0.01"))

    @property
    def change_due(self):
        if self.amount_tendered is None:
            return None
        return self.amount_tendered - self.grand_total


class SalesItem(models.Model):
    transaction = models.ForeignKey(
        SalesTransaction, on_delete=models.CASCADE, related_name="items"
    )
    # Nullable: a catalog product (Fig. 3-12) OR a custom, off-menu item
    # (Fig. 3-13, custom_name used instead) -- never both, enforced in
    # services.py rather than at the DB level, since a CHECK constraint
    # would complicate the common case for little real benefit here.
    product = models.ForeignKey(
        "inventory.Product", on_delete=models.PROTECT, null=True, blank=True
    )
    custom_name = models.CharField(max_length=150, blank=True)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)
    # Free-form order customization (Fig. 3-14): size, sugar level, add-ons,
    # discount note, etc. Kept as JSON rather than rigid columns since the
    # manuscript's own example ("Spanish Latte" with size/sugar/add-ons)
    # implies this varies per product rather than following one fixed shape.
    customizations = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return self.product.name if self.product else self.custom_name

    @property
    def subtotal(self):
        return self.unit_price * self.quantity
