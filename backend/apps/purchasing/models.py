"""
Suppliers (task #143, client review, 2026-11-04): a new Purchasing role
manages relationships with the business's suppliers.

This is deliberately a NEW, standalone model rather than a migration of
the two pre-existing, separate, plain-text supplier fields already in the
codebase:
  - Product.supplier (apps.inventory.models) -- real catalog-level data
    imported from the client's Raw_Materials.xlsx / Packaging_Materials.xlsx
    (278 real rows, see import_raw_and_packaging_materials.py).
  - ProductionRecord.supplier (apps.production.models) -- a separate,
    per-batch free-text field.
Migrating either of those into a FK here would touch real, already-
populated client data and two other apps' models with no client ask for
that -- the client's request was for a new Suppliers concept for the new
Purchasing role to manage, not a rework of existing catalog/production
fields. Those two fields are left exactly as they are.

Confidentiality (client review, 2026-11-04): every field on a Supplier is
treated as confidential -- visible only to OWNER_ADMIN and PURCHASING
(apps.accounts.permissions.role_required/RoleRequiredMixin already gate
every view in apps.purchasing to just those two roles; Branch Staff and
Commissary Staff can't reach a Supplier record in any form, not even its
name).
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TimestampedModel


class Supplier(TimestampedModel):
    """A supplier the business buys raw materials, packaging, or other
    goods from. Intentionally independent of Product/ProductionRecord's
    own free-text supplier fields -- see this module's docstring."""

    name = models.CharField(max_length=150, unique=True)
    contact_person = models.CharField(max_length=150, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    notes = models.TextField(
        blank=True, help_text="Payment terms, lead times, or any other internal note."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive suppliers are kept for history but hidden from new purchasing.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PurchaseOrder(TimestampedModel):
    """A purchase order placed with a Supplier for one or more materials,
    tracked through to receiving (Consolidated Task Tracker: "Purchasing/
    Receiving records model" -- 2026-11-07 scoped options, Option B).

    Deliberately mirrors apps.production.StockTransferOrder's own
    forward-only lifecycle and its "inventory only actually changes at
    the one confirming step" shape (there: DELIVERED; here: RECEIVED) --
    the same real-world event (stock arriving somewhere), just from a
    different source (a supplier, not the commissary). Unlike
    StockTransferOrder, nothing is deducted from anyone at creation
    time -- a PO doesn't remove stock from anyone, it only ever adds
    stock once goods are actually received (see
    apps.purchasing.services.receive_purchase_order).

    received_quantity lives on PurchaseOrderItem, not here, because a
    real delivery can differ per line (one material short, another
    exactly as ordered) -- see that model's own docstring.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ORDERED = "ORDERED", "Ordered"
        RECEIVED = "RECEIVED", "Received"
        CANCELLED = "CANCELLED", "Cancelled"

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_orders")
    branch = models.ForeignKey(
        "accounts.Branch",
        on_delete=models.PROTECT,
        related_name="purchase_orders",
        help_text="Which branch/commissary this order restocks.",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="purchase_orders_created",
    )
    ordered_at = models.DateTimeField(
        null=True, blank=True, help_text="Set only once status reaches Ordered."
    )
    received_at = models.DateTimeField(
        null=True, blank=True, help_text="Set only once status reaches Received."
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="purchase_orders_received",
    )
    cancelled_at = models.DateTimeField(
        null=True, blank=True, help_text="Set only once status reaches Cancelled."
    )
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="purchase_orders_cancelled",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"PO #{self.pk} — {self.supplier.name}"

    @property
    def total_cost(self):
        """Sum of each line's ordered_quantity x unit_cost -- the order's
        cost as placed, regardless of what's actually been received yet
        (receiving can under/over-deliver a line without changing what
        was originally agreed/ordered)."""
        return sum(
            (item.ordered_quantity * item.unit_cost for item in self.items.all()), Decimal("0")
        )


class PurchaseOrderItem(models.Model):
    """One material line on a PurchaseOrder.

    received_quantity is kept separate from ordered_quantity (Option B
    of the 2026-11-07 scoped options) so a real short or over delivery
    is visible rather than silently assumed to match what was ordered --
    it stays null until the order is actually marked Received, at which
    point apps.purchasing.services.receive_purchase_order fills it in
    (defaulting to ordered_quantity when the receiving staff doesn't
    override it)."""

    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="items"
    )
    material = models.ForeignKey(
        "inventory.Product",
        on_delete=models.PROTECT,
        limit_choices_to={"product_type": "MATERIAL"},
    )
    ordered_quantity = models.DecimalField(
        max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))]
    )
    received_quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Set only once the order reaches Received; defaults to the ordered "
        "quantity but can be overridden to record a short or over delivery.",
    )
    unit_cost = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0"), validators=[MinValueValidator(0)]
    )

    def __str__(self):
        return f"{self.ordered_quantity} x {self.material.name}"

    @property
    def received_variance(self):
        """received_quantity - ordered_quantity: positive for an
        over-delivery, negative for a short delivery, None until
        actually received. A plain difference rather than a percentage
        -- materials here are tracked to 0.001 precision, where a raw
        amount is more legible to a receiving clerk checking a delivery
        against the PO than a percentage would be."""
        if self.received_quantity is None:
            return None
        return self.received_quantity - self.ordered_quantity
