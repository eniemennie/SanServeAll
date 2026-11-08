"""
Consolidated Task Tracker item "Pre-existing negative/zero real stock
blocking branch/product pairs from forecast backfill" (masterlist,
2026-11-07/08) -- owner-approved follow-up to
scripts/diagnose_negative_stock_forecast_pairs.py's findings.

That diagnostic found that most of the under-served, backfill-blocked
pairs share one simple, consistent story: their ENTIRE logged
InventoryTransaction history is SALE_DEDUCTION only, with no
HISTORICAL_BACKFILL opening balance from task #104's reconstruction
(apps/production/management/commands/backfill_finished_goods_inventory.py)
and no restock/production/transfer of any kind. These SKU/branch pairs
simply never received a starting inventory number -- task #104's
reconstruction only covers pairs with real ProductionRecord/
StockTransferOrder history, and these have none -- so real sales
against an implicit zero balance pushed them negative. That is a real
gap in the data, not a sign anything is broken.

This command closes that specific gap, and ONLY that gap: for every
(branch, product) pair that is (a) under-served per
apps.forecasting.ml.data_prep.has_sufficient_history, (b) currently
below the forecast backfill script's own stock floor
(scripts/backfill_thin_forecast_pairs.py's _has_sufficient_stock, which
needs real stock >= the 1-2 unit synthetic sale it wants to place), (c)
has never had a HISTORICAL_BACKFILL transaction of its own, and (d) its
ledger reconciles exactly with its own ending balance (via
apps.inventory.services.find_ledger_discrepancies) -- it credits a
single HISTORICAL_BACKFILL InventoryTransaction bringing that pair's
real Inventory.quantity_on_hand up to --target-balance (default 10,
deliberately modest -- enough to clear the forecast backfill script's
sufficiency check and stop understating real demand, not an attempt to
fabricate a precise historical count the way task #104 reconstructs one
from real production/transfer records).

Condition (d) is the one that matters most: it is what keeps this
command from ever touching the OTHER kind of pair the same diagnostic
found -- one whose ledger total does not match its own recorded
balance at all (e.g. Americano @ Batangas City, Almond 7in Square @
Batangas City as of 2026-11-08). That is a real, unexplained
data-integrity problem (see the "Reconcile Inventory.quantity_on_hand
vs ledger" tracker row), and crediting more stock on top of a number
that is already wrong would paper over it rather than fix it -- that
stays a separate, explicit owner/adviser decision, never something this
command does automatically.

HONEST LIMITATION, same caveat task #104's own command states up
front: there is no real historical record (no production run, no
transfer, no prior sale pattern beyond what already pushed the pair
negative) to reconstruct an actual number from for these specific
pairs -- --target-balance is a judgment call, not a fact recovered from
data. It is deliberately far below the ~400-unit flat default seen on
this project's broad demo-seeded products, so it does not compound the
same "fabricated-looking round number" pattern already flagged
elsewhere in this project's data-integrity findings.

Safety properties, matching this project's established backfill
conventions:
  - Dry-run by default; nothing is written unless --apply is passed
    (same convention as scripts/backfill_thin_forecast_pairs.py, the
    project's own post-incident-hardened precedent for new backfill
    work -- see that script's module docstring, safety property 8).
  - Idempotent: a pair already credited by a prior run of this exact
    command (or by any other HISTORICAL_BACKFILL transaction, e.g. the
    Almond 7in Square manual fix) is skipped, not re-credited.
  - Every write happens inside one atomic transaction per pair, and the
    whole run is wrapped in an outer atomic block too, so a failure
    partway through never leaves some pairs credited and others not.

Usage:
    python manage.py backfill_missing_opening_balance              # dry run (default)
    python manage.py backfill_missing_opening_balance --apply
    python manage.py backfill_missing_opening_balance --apply --target-balance 15
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Branch
from apps.forecasting.ml.data_prep import build_daily_sales_series, has_sufficient_history
from apps.forecasting.services import get_branch_product_pairs_with_sales_history
from apps.inventory.models import Inventory, InventoryTransaction, Product
from apps.inventory.services import find_ledger_discrepancies

# Mirrors scripts/backfill_thin_forecast_pairs.py's own candidate sale size
# (1-2 units) -- a pair needs at least this much real stock for that script
# to ever attempt a synthetic sale against it.
_MINIMUM_SAFE_QUANTITY = Decimal("1")


class Command(BaseCommand):
    help = (
        "Gives a modest HISTORICAL_BACKFILL opening balance to under-served "
        "forecast pairs whose negative/zero real stock is fully explained by "
        "never having received one (see this file's module docstring). Dry-run "
        "by default -- pass --apply to write."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually write the HISTORICAL_BACKFILL transactions. Without this, "
            "only reports what would be done.",
        )
        parser.add_argument(
            "--target-balance",
            type=Decimal,
            default=Decimal("10"),
            help="Quantity to bring each qualifying pair's real stock up to. Default 10 "
            "-- see module docstring for why this is a judgment call, not a fact.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        target_balance = options["target_balance"]
        if target_balance <= 0:
            raise ValueError("--target-balance must be a positive quantity.")

        discrepant_pairs = {
            (entry["inventory"].branch_id, entry["inventory"].product_id)
            for entry in find_ledger_discrepancies()
        }
        already_backfilled_pairs = set(
            InventoryTransaction.objects.filter(
                movement_type=InventoryTransaction.MovementType.HISTORICAL_BACKFILL
            ).values_list("branch_id", "product_id")
        )

        candidates = []
        for branch_id, product_id in get_branch_product_pairs_with_sales_history():
            branch = Branch.objects.get(pk=branch_id)
            product = Product.objects.get(pk=product_id)
            series = build_daily_sales_series(branch, product)
            if has_sufficient_history(series):
                continue  # not under-served; nothing to clear for this pair

            inv = Inventory.objects.filter(branch=branch, product=product).first()
            current_qty = inv.quantity_on_hand if inv is not None else Decimal("0")
            if current_qty >= _MINIMUM_SAFE_QUANTITY:
                continue  # already clear of the forecast backfill script's own floor

            if (branch_id, product_id) in discrepant_pairs:
                continue  # ledger doesn't reconcile -- separate owner/adviser decision
            if (branch_id, product_id) in already_backfilled_pairs:
                continue  # already given an opening balance once; never re-credit

            candidates.append((branch, product, current_qty))

        if not candidates:
            self.stdout.write(
                self.style.SUCCESS(
                    "No qualifying pairs found -- nothing is both under-served, below "
                    "the stock floor, never backfilled, and ledger-clean right now."
                )
            )
            return

        self.stdout.write(f"{len(candidates)} qualifying pair(s) found:\n")
        for branch, product, current_qty in candidates:
            delta = target_balance - current_qty
            self.stdout.write(
                f"  {product.name} @ {branch.name}: {current_qty} -> {target_balance} "
                f"(credit {delta:+})"
            )

        if not apply_changes:
            self.stdout.write(
                self.style.WARNING("\nDry run: nothing written. Pass --apply to write.")
            )
            return

        with transaction.atomic():
            for branch, product, current_qty in candidates:
                delta = target_balance - current_qty
                with transaction.atomic():
                    inventory = Inventory.objects.select_for_update().get(
                        branch=branch, product=product
                    )
                    inventory.quantity_on_hand = target_balance
                    inventory.save()
                    InventoryTransaction.objects.create(
                        branch=branch,
                        product=product,
                        movement_type=InventoryTransaction.MovementType.HISTORICAL_BACKFILL,
                        quantity_change=delta,
                    )

        self.stdout.write(
            self.style.SUCCESS(f"\nBackfilled {len(candidates)} pair(s) to {target_balance} each.")
        )
