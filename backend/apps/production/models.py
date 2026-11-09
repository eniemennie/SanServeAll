"""
Commissary production tracking (Week 8): recording production output and
ingredient usage. Fig. 3-24's "Batch Management Interface" maps to
ProductionRecord here -- one record per production run, distinct from the
KaHero "batch" concept (Week 7), which is a file-upload batch, not a
production run. Same word, two different real-world things in the
manuscript; kept as separate models rather than forced into one.
"""

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class ProductionRecord(models.Model):
    class Quality(models.TextChoices):
        PASS = "PASS", "Pass"
        FAIL = "FAIL", "Fail"
        # Client raw-data import (Production_Records.xlsx): 10/356 real
        # batches are still IN_PROGRESS/PENDING and genuinely have no
        # quality verdict yet -- storing those as PASS (implies it
        # passed inspection) or FAIL (implies it failed) would record a
        # verdict that was never actually given.
        PENDING = "PENDING", "Pending"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        COMPLETED = "COMPLETED", "Completed"

    # Client raw-data import (Production_Records.xlsx): the client's own
    # human-readable batch label (e.g. "BATCH-0001"). Nullable/blank for
    # any batch created the normal way through this app, which has never
    # needed its own label -- only imported history carries one, so a
    # future "check batch BATCH-0042" request from the client has
    # something to look up by.
    batch_ref = models.CharField(max_length=32, blank=True, null=True, unique=True, default=None)
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

    CANCELLED (backend gap assessment item 24) is the one exception to
    "forward-only": an order can be cancelled from PROCESSING or
    IN_TRANSIT (see services.cancel_stock_transfer_order), which returns
    the quantity to the commissary's Inventory -- reversing the deduction
    made at creation time. Once DELIVERED, an order is final and cannot
    be cancelled; a delivered shipment that needs correcting is a new,
    separate transfer or a manual inventory adjustment, not a reversal of
    this record.
    """

    class Status(models.TextChoices):
        PROCESSING = "PROCESSING", "Processing"
        IN_TRANSIT = "IN_TRANSIT", "In Transit"
        DELIVERED = "DELIVERED", "Delivered"
        CANCELLED = "CANCELLED", "Cancelled"

    # Client raw-data import (Stock_Transfer.xlsx): same reasoning as
    # ProductionRecord.batch_ref -- preserves the client's own
    # "TRANSFER-0001"-style label for imported history only.
    transfer_ref = models.CharField(max_length=32, blank=True, null=True, unique=True, default=None)
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
    cancelled_at = models.DateTimeField(
        null=True, blank=True, help_text="Set only when status actually reaches CANCELLED."
    )
    # Task #166 (adviser dry-run, 2026-11-05): who actually performed the
    # Deliver/Cancel action -- commissary_staff above only records who
    # CREATED the order, which isn't necessarily who later delivered or
    # cancelled it (a different commissary staff member or the owner/
    # admin -- both roles can act on an order, see role_required on the
    # advance_stock_transfer/cancel_stock_transfer views). PROTECT, not
    # CASCADE/SET_NULL: same reasoning as commissary_staff above --
    # this is an audit trail, and a deleted user account shouldn't
    # silently erase who performed a real stock movement. null=True
    # because every pre-existing row (and any order that hasn't reached
    # that status yet) genuinely has no such actor.
    delivered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stock_transfer_orders_delivered",
        help_text="Set only once status reaches DELIVERED.",
    )
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stock_transfer_orders_cancelled",
        help_text="Set only when status actually reaches CANCELLED.",
    )

    # Task #166 (adviser dry-run, 2026-11-05): how long an order can sit
    # in PROCESSING before the Stock Transfers screen visually flags it
    # as stale. Not configurable/per-branch -- the ticket just asked for
    # a flag at a fixed "more than two days" threshold, so this exists
    # as a named constant purely so the two days isn't a magic number
    # repeated between is_stale_in_processing and its own tests.
    STALE_IN_PROCESSING_AFTER = timedelta(days=2)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"Transfer #{self.pk}: {self.quantity} x {self.product.name} "
            f"{self.source_branch.code} -> {self.destination_branch.code} ({self.status})"
        )

    @property
    def is_stale_in_processing(self):
        """True once an order has sat in PROCESSING (created but not
        yet dispatched) for more than STALE_IN_PROCESSING_AFTER -- the
        Stock Transfers screen uses this to visually flag an order
        that's stalled before ever reaching In Transit. Deliberately
        keyed off `created_at`, not `updated_at`: an order that is
        STILL Processing has, by definition, never had a status change
        since creation, so the two are equal anyway -- created_at is
        used because it's the field whose meaning doesn't shift if
        that ever stops being true (e.g. a future edit to the order
        that isn't a status change)."""
        if self.status != self.Status.PROCESSING:
            return False
        return timezone.now() - self.created_at > self.STALE_IN_PROCESSING_AFTER


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
        # Client review (2026-11-09): widened from product_type=MATERIAL
        # alone -- this already accepted packaging rows before Packaging
        # became its own product_type (see ProductPackaging's own
        # docstring: a batch-assembled item like a takeout box can be
        # logged here directly), so it keeps accepting them now that
        # they're ProductType.PACKAGING rather than a MATERIAL row
        # tagged category=PACKAGING.
        limit_choices_to={"product_type__in": ["MATERIAL", "PACKAGING"]},
    )
    # Decimal, 3 places: client raw-data import (Ingredient_Usage.xlsx)
    # records real consumption down to a third decimal place for some
    # materials (e.g. Cream of tartar: 0.019kg) -- finer than the 2
    # places Inventory.quantity_on_hand uses, so this needs its own,
    # wider precision to store those exactly rather than rounding away
    # real small-quantity usage.
    quantity_used = models.DecimalField(max_digits=12, decimal_places=3)

    def __str__(self):
        unit = self.material.get_unit_of_measure_display()
        return f"{self.quantity_used} {unit} x {self.material.name}"
