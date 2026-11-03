"""
One-off historical backfill for task #104: every branch's finished-goods
Inventory is currently zero, because neither historical-import command
(import_production_and_ingredient_usage, import_stock_transfer) ever
touched Inventory -- both were deliberately written that way, reasoning
that Raw_Materials.xlsx/Packaging_Materials.xlsx's "Quantity on Hand"
columns were already a CURRENT, post-consumption snapshot for *materials*.
But no equivalent current snapshot exists for *finished goods*, so that
same "don't touch Inventory" policy left finished-goods stock
permanently at zero everywhere -- a real gap, not a decision anyone
made on purpose.

This command closes that gap by replaying the two real event types that,
live, move finished-goods Inventory (apps.production.services), in the
same order they'd have happened historically:

  Phase 1 -- Production output: for every ProductionRecord with
  status=COMPLETED and quality=PASS (a FAIL run never became sellable
  stock -- see record_production), credit the commissary's Inventory for
  that product by quantity_produced. Mirrors record_production's own
  finished-goods crediting.

  Phase 2 -- Stock transfers: for every StockTransferOrder NOT CANCELLED
  (a cancelled order's creation-time deduction was already reversed back
  to the commissary in real life -- see cancel_stock_transfer_order --
  so it nets to zero and is skipped entirely here), deduct the
  commissary's Inventory for that product by quantity (mirrors
  create_stock_transfer_order's creation-time deduction, which happens
  regardless of the order's eventual status). Additionally, only for
  orders that reached DELIVERED, credit the destination branch's
  Inventory by quantity (mirrors advance_stock_transfer_status's
  delivery-time credit -- an order still PROCESSING/IN_TRANSIT has
  stock accounted for by the order alone, not sitting at either branch).

Every Inventory change this command makes is logged as its own
InventoryTransaction(movement_type=HISTORICAL_BACKFILL) row -- not
PRODUCTION_OUTPUT/TRANSFER_OUT/TRANSFER_IN -- so the audit trail never
misrepresents a reconstruction run long after the fact as a real-time
staff action, and so a second run can detect the first one and refuse to
double-apply (see idempotency below).

IMPORTANT CAVEAT (communicated to the project owner when this path was
chosen over the alternatives): this reconstructs gross production and
gross transfers, but there is no itemized historical sales data to net
back out -- Production_Records.xlsx/Stock_Transfer.xlsx predate the real
POS sales history, and import_branch_sales_history.py only has
*aggregate* daily revenue, not itemized per-product sales. So the
resulting numbers will OVERSTATE real current stock by however much was
actually sold out of that stock before today. This is a known,
accepted limitation of this remediation path, not a bug in this command.

Idempotency: refuses to run a second time if any
InventoryTransaction(movement_type=HISTORICAL_BACKFILL) row already
exists -- this command reconstructs the ENTIRE history every time it
runs, so running it twice would double-apply every movement. There is no
--force: if a prior run needs correcting, delete its HISTORICAL_BACKFILL
transactions and manually reverse their Inventory deltas first (or
restore from a pre-backfill database backup), rather than papering over
it with a flag that silently compounds the mistake.

Second, narrower guard, caught by testing this command against a local
dev database that (unlike the real target) already had live demo/test
traffic recorded through it: this command assumes NO real, live
production run or stock transfer has moved finished-goods Inventory yet
(true for the real target today -- task #104 exists precisely because
every finished-goods Inventory row is currently zero everywhere). If any
FINISHED_GOOD Inventory row is already nonzero, phase 1 would still sum
*every* COMPLETED+PASS ProductionRecord -- including the one(s) that
live event already credited -- and double-count it. So this command
also refuses to run if any FINISHED_GOOD Inventory row is already
nonzero, unless --ignore-existing-stock is passed (meant only for a
throwaway/dev database being used to test this command itself, never
for the real target without first understanding exactly which records
are already double-counted).

Consistency check: if replaying this history would ever drive a
commissary Inventory row negative (more was shipped out historically
than was ever produced, per this same historical data), that is a real
data inconsistency worth seeing, not something to silently clamp to
zero. By default this command reports every such product and makes NO
changes at all (atomic). Pass --allow-negative to proceed anyway and
clamp those rows to zero, logging a WARNING for each one it clamped.

Usage:
    python manage.py backfill_finished_goods_inventory --dry-run
    python manage.py backfill_finished_goods_inventory
    python manage.py backfill_finished_goods_inventory --allow-negative
"""

from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.inventory.models import Inventory, InventoryTransaction
from apps.production.models import ProductionRecord, StockTransferOrder
from apps.production.services import get_commissary_branch


class Command(BaseCommand):
    help = (
        "Backfills finished-goods Inventory from real historical ProductionRecord/"
        "StockTransferOrder data (task #104). See this file's module docstring for "
        "the full two-phase design and its known limitations."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute and report the resulting Inventory deltas without writing anything.",
        )
        parser.add_argument(
            "--allow-negative",
            action="store_true",
            help=(
                "If replaying history would drive a commissary Inventory row negative, "
                "clamp it to zero and log a warning instead of aborting."
            ),
        )
        parser.add_argument(
            "--ignore-existing-stock",
            action="store_true",
            help=(
                "Skip the pre-check that refuses to run while any FINISHED_GOOD "
                "Inventory row is already nonzero. Only for testing this command "
                "against a dev database that already has live test data in it -- "
                "see the module docstring."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        allow_negative = options["allow_negative"]
        ignore_existing_stock = options["ignore_existing_stock"]

        if InventoryTransaction.objects.filter(
            movement_type=InventoryTransaction.MovementType.HISTORICAL_BACKFILL
        ).exists():
            raise CommandError(
                "A historical backfill has already been run (HISTORICAL_BACKFILL "
                "InventoryTransaction rows already exist). Running this command again "
                "would double-apply every movement. See this command's module "
                "docstring for how to correct a prior run instead of re-running it."
            )

        if not ignore_existing_stock:
            existing_stock = Inventory.objects.filter(
                product__product_type="FINISHED_GOOD", quantity_on_hand__gt=0
            ).select_related("branch", "product")
            if existing_stock.exists():
                self.stdout.write(
                    self.style.ERROR(
                        f"{existing_stock.count()} FINISHED_GOOD Inventory row(s) are "
                        "already nonzero -- a real production run or stock transfer "
                        "has already moved Inventory, so replaying the full historical "
                        "data would double-count it. Nothing was written."
                    )
                )
                for row in existing_stock[:20]:
                    self.stdout.write(
                        f"  {row.product.name} @ {row.branch.name}: {row.quantity_on_hand}"
                    )
                raise CommandError(
                    "Aborting: see existing-stock report above. Pass "
                    "--ignore-existing-stock only if you understand exactly which "
                    "records this double-counts (see module docstring)."
                )

        commissary = get_commissary_branch()
        if commissary is None:
            raise CommandError("No commissary branch is configured.")

        # -- Phase 1: production output -----------------------------------
        # product_id -> [(production_record, quantity_produced), ...]
        production_rows = list(
            ProductionRecord.objects.filter(
                status=ProductionRecord.Status.COMPLETED,
                quality=ProductionRecord.Quality.PASS,
            ).select_related("product")
        )

        # -- Phase 2: stock transfers ---------------------------------------
        transfer_rows = list(
            StockTransferOrder.objects.exclude(
                status=StockTransferOrder.Status.CANCELLED
            ).select_related("product", "destination_branch")
        )

        # -- Compute net deltas in-memory first, so a consistency problem is
        # caught before anything is written (atomic by construction: nothing
        # is touched until every row has been checked).
        commissary_delta = defaultdict(lambda: 0)  # product_id -> Decimal-safe int delta
        branch_delta = defaultdict(lambda: 0)  # (branch_id, product_id) -> delta

        for record in production_rows:
            commissary_delta[record.product_id] += record.quantity_produced

        for order in transfer_rows:
            commissary_delta[order.product_id] -= order.quantity
            if order.status == StockTransferOrder.Status.DELIVERED:
                branch_delta[(order.destination_branch_id, order.product_id)] += order.quantity

        # -- Consistency check: would any commissary row go negative? -------
        negative_products = []
        for product_id, delta in commissary_delta.items():
            current = self._current_quantity(commissary.id, product_id)
            if current + delta < 0:
                negative_products.append((product_id, current, delta))

        if negative_products and not allow_negative:
            self.stdout.write(
                self.style.ERROR(
                    f"{len(negative_products)} product(s) would end up with negative "
                    "commissary stock -- more was shipped historically than was ever "
                    "produced, per this same historical data. Nothing was written. "
                    "Pass --allow-negative to clamp these to zero instead, or "
                    "investigate the underlying data first."
                )
            )
            for product_id, current, delta in negative_products:
                self.stdout.write(
                    f"  product_id={product_id}: current={current} delta={delta} "
                    f"would_be={current + delta}"
                )
            raise CommandError("Aborting: see negative-stock report above.")

        if dry_run:
            self._report(commissary, commissary_delta, branch_delta, negative_products)
            self.stdout.write(self.style.WARNING("--dry-run: nothing written."))
            return

        with transaction.atomic():
            self._apply_production_phase(commissary, production_rows)
            self._apply_transfer_phase(commissary, transfer_rows, allow_negative)

        self._report(commissary, commissary_delta, branch_delta, negative_products)
        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill complete: {len(production_rows)} production record(s) and "
                f"{len(transfer_rows)} non-cancelled transfer(s) replayed."
            )
        )

    # -- phase application ------------------------------------------------

    def _apply_production_phase(self, commissary, production_rows):
        for record in production_rows:
            inventory, _ = Inventory.objects.get_or_create(
                branch=commissary, product=record.product
            )
            inventory.quantity_on_hand += record.quantity_produced
            inventory.save()
            InventoryTransaction.objects.create(
                branch=commissary,
                product=record.product,
                movement_type=InventoryTransaction.MovementType.HISTORICAL_BACKFILL,
                quantity_change=record.quantity_produced,
            )

    def _apply_transfer_phase(self, commissary, transfer_rows, allow_negative):
        for order in transfer_rows:
            source_inventory, _ = Inventory.objects.get_or_create(
                branch=commissary, product=order.product
            )
            source_inventory.quantity_on_hand -= order.quantity
            if source_inventory.quantity_on_hand < 0:
                if not allow_negative:
                    # Already checked above; reaching this means the in-memory
                    # check and the actual writes disagreed -- a real bug, not
                    # a data problem, so fail loudly rather than save bad data.
                    raise CommandError(
                        f"Internal inconsistency: {order.product} would go negative "
                        "at the commissary during apply, but the pre-check didn't "
                        "catch it."
                    )
                self.stdout.write(
                    self.style.WARNING(
                        f"Clamping {order.product} to zero at the commissary "
                        f"(would have been {source_inventory.quantity_on_hand})."
                    )
                )
                source_inventory.quantity_on_hand = 0
            source_inventory.save()
            InventoryTransaction.objects.create(
                branch=commissary,
                product=order.product,
                movement_type=InventoryTransaction.MovementType.HISTORICAL_BACKFILL,
                quantity_change=-order.quantity,
            )

            if order.status == StockTransferOrder.Status.DELIVERED:
                dest_inventory, _ = Inventory.objects.get_or_create(
                    branch=order.destination_branch, product=order.product
                )
                dest_inventory.quantity_on_hand += order.quantity
                dest_inventory.save()
                InventoryTransaction.objects.create(
                    branch=order.destination_branch,
                    product=order.product,
                    movement_type=InventoryTransaction.MovementType.HISTORICAL_BACKFILL,
                    quantity_change=order.quantity,
                )

    # -- reporting ----------------------------------------------------------

    def _current_quantity(self, branch_id, product_id):
        inventory = Inventory.objects.filter(branch_id=branch_id, product_id=product_id).first()
        return inventory.quantity_on_hand if inventory else 0

    def _report(self, commissary, commissary_delta, branch_delta, negative_products):
        self.stdout.write(
            f"Commissary: {len(commissary_delta)} product(s) affected, net deltas computed."
        )
        self.stdout.write(
            f"Branches: {len(branch_delta)} (branch, product) pair(s) credited from "
            "DELIVERED transfers."
        )
        if negative_products:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(negative_products)} product(s) were clamped to zero at the "
                    "commissary -- see warnings above."
                )
            )
