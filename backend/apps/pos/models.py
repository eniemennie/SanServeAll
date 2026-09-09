"""
Point of Sale: sales transactions, line items, receipts.

A SalesTransaction starts life as a DRAFT -- built up item by item on the
Ordering Screen (Row 4) -- and is only marked COMPLETED once payment is
processed (Week 5). This lets the "current order in progress" live as a
real, queryable database row rather than fragile session state, and
survives a page refresh or accidental navigation away mid-order.
"""

from decimal import Decimal

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

    branch = models.ForeignKey("accounts.Branch", on_delete=models.PROTECT)
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    payment_method = models.CharField(max_length=32, choices=PaymentMethod.choices, blank=True)
    amount_tendered = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Transaction #{self.pk} ({self.status})"

    @property
    def total_amount(self):
        return sum((item.subtotal for item in self.items.all()), Decimal("0.00"))

    @property
    def change_due(self):
        if self.amount_tendered is None:
            return None
        return self.amount_tendered - self.total_amount


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
