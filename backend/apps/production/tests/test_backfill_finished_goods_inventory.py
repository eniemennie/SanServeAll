"""
Tests for backfill_finished_goods_inventory (task #104): the one-off
reconstruction of finished-goods Inventory from real historical
ProductionRecord/StockTransferOrder data, since neither historical
import command ever touched Inventory itself.

Covers: Phase 1 (production crediting), Phase 2 (transfer deduction +
DELIVERED-only branch crediting, CANCELLED skipped entirely), the
HISTORICAL_BACKFILL audit trail, both guards (already-ran,
pre-existing-stock), the negative-stock consistency check, and
--dry-run making no changes.
"""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import Branch, Role, User
from apps.inventory.models import Inventory, InventoryTransaction, Product
from apps.production.models import ProductionRecord, StockTransferOrder

pytestmark = pytest.mark.django_db


@pytest.fixture
def commissary():
    return Branch.objects.create(name="Commissary", code="COMMISSARY", is_commissary=True)


@pytest.fixture
def batangas():
    return Branch.objects.create(name="Batangas", code="BATANGAS")


@pytest.fixture
def lipa():
    return Branch.objects.create(name="Lipa", code="LIPA")


@pytest.fixture
def staff(commissary):
    role, _ = Role.objects.get_or_create(name=Role.COMMISSARY_STAFF)
    return User.objects.create_user(username="commissary_staff", role=role, branch=commissary)


@pytest.fixture
def cake():
    return Product.objects.create(
        name="Classic Tiramisu", price="190.00", product_type=Product.ProductType.FINISHED_GOOD
    )


def _production(staff, product, quantity, status="COMPLETED", quality="PASS"):
    return ProductionRecord.objects.create(
        commissary_staff=staff,
        product=product,
        quantity_produced=quantity,
        status=status,
        quality=quality,
    )


def _transfer(staff, commissary, destination, product, quantity, status):
    return StockTransferOrder.objects.create(
        commissary_staff=staff,
        source_branch=commissary,
        destination_branch=destination,
        product=product,
        quantity=quantity,
        status=status,
    )


class TestBackfillFinishedGoodsInventory:
    def test_phase_1_credits_commissary_from_completed_pass_production_only(
        self, staff, commissary, cake
    ):
        _production(staff, cake, 10, status="COMPLETED", quality="PASS")
        # Never credited: not COMPLETED, or COMPLETED but FAIL.
        _production(staff, cake, 99, status="PENDING", quality="PENDING")
        _production(staff, cake, 50, status="COMPLETED", quality="FAIL")

        call_command("backfill_finished_goods_inventory")

        inv = Inventory.objects.get(branch=commissary, product=cake)
        assert inv.quantity_on_hand == 10
        assert (
            InventoryTransaction.objects.filter(
                movement_type=InventoryTransaction.MovementType.HISTORICAL_BACKFILL,
                branch=commissary,
                product=cake,
            ).count()
            == 1
        )

    def test_phase_2_deducts_commissary_regardless_of_status_credits_branch_only_if_delivered(
        self, staff, commissary, batangas, lipa, cake
    ):
        _production(staff, cake, 100, status="COMPLETED", quality="PASS")
        _transfer(staff, commissary, batangas, cake, 10, StockTransferOrder.Status.DELIVERED)
        _transfer(staff, commissary, lipa, cake, 5, StockTransferOrder.Status.IN_TRANSIT)
        _transfer(staff, commissary, lipa, cake, 3, StockTransferOrder.Status.PROCESSING)

        call_command("backfill_finished_goods_inventory")

        # Commissary: 100 produced - 10 - 5 - 3 = 82 (all three transfers
        # deduct at creation time, regardless of how far they got).
        assert Inventory.objects.get(branch=commissary, product=cake).quantity_on_hand == 82
        # Batangas: only the DELIVERED transfer actually landed there.
        assert Inventory.objects.get(branch=batangas, product=cake).quantity_on_hand == 10
        # Lipa: neither IN_TRANSIT nor PROCESSING has landed yet.
        assert not Inventory.objects.filter(branch=lipa, product=cake).exists()

    def test_cancelled_transfer_is_skipped_entirely_net_zero(
        self, staff, commissary, batangas, cake
    ):
        _production(staff, cake, 50, status="COMPLETED", quality="PASS")
        _transfer(staff, commissary, batangas, cake, 7, StockTransferOrder.Status.CANCELLED)

        call_command("backfill_finished_goods_inventory")

        # A cancelled order's creation-time deduction was already reversed
        # back to the commissary in real life (cancel_stock_transfer_order),
        # so replaying it here must have zero net effect.
        assert Inventory.objects.get(branch=commissary, product=cake).quantity_on_hand == 50
        assert not Inventory.objects.filter(branch=batangas, product=cake).exists()
        assert not InventoryTransaction.objects.filter(product=cake, branch=batangas).exists()

    def test_dry_run_computes_but_writes_nothing(self, staff, commissary, cake):
        _production(staff, cake, 10, status="COMPLETED", quality="PASS")

        call_command("backfill_finished_goods_inventory", "--dry-run")

        assert not Inventory.objects.filter(product=cake).exists()
        assert not InventoryTransaction.objects.filter(
            movement_type=InventoryTransaction.MovementType.HISTORICAL_BACKFILL
        ).exists()

    def test_refuses_to_run_twice(self, staff, commissary, cake):
        _production(staff, cake, 10, status="COMPLETED", quality="PASS")
        call_command("backfill_finished_goods_inventory")

        with pytest.raises(CommandError, match="already been run"):
            call_command("backfill_finished_goods_inventory")

        # Confirm it's a real refusal, not a partial second application.
        assert Inventory.objects.get(branch=commissary, product=cake).quantity_on_hand == 10

    def test_refuses_to_run_when_finished_good_inventory_already_nonzero(
        self, staff, commissary, cake
    ):
        # Simulates a real live production/transfer event having already
        # moved Inventory before the backfill runs -- replaying the full
        # history on top of that would double-count it.
        Inventory.objects.create(branch=commissary, product=cake, quantity_on_hand=3)
        _production(staff, cake, 10, status="COMPLETED", quality="PASS")

        with pytest.raises(CommandError, match="existing-stock report"):
            call_command("backfill_finished_goods_inventory")

        # Nothing was written -- still exactly the pre-existing value.
        assert Inventory.objects.get(branch=commissary, product=cake).quantity_on_hand == 3
        assert not InventoryTransaction.objects.filter(
            movement_type=InventoryTransaction.MovementType.HISTORICAL_BACKFILL
        ).exists()

    def test_ignore_existing_stock_flag_bypasses_that_guard(self, staff, commissary, cake):
        Inventory.objects.create(branch=commissary, product=cake, quantity_on_hand=3)
        _production(staff, cake, 10, status="COMPLETED", quality="PASS")

        call_command("backfill_finished_goods_inventory", "--ignore-existing-stock")

        # Confirms the flag really does skip the guard (the resulting
        # number intentionally double-counts here, matching the
        # documented caveat for this flag).
        assert Inventory.objects.get(branch=commissary, product=cake).quantity_on_hand == 13

    def test_negative_resulting_stock_aborts_with_no_writes_by_default(
        self, staff, commissary, batangas, cake
    ):
        # More shipped than ever produced, per this same historical data.
        _production(staff, cake, 5, status="COMPLETED", quality="PASS")
        _transfer(staff, commissary, batangas, cake, 20, StockTransferOrder.Status.DELIVERED)

        with pytest.raises(CommandError, match="negative-stock report"):
            call_command("backfill_finished_goods_inventory")

        assert not Inventory.objects.filter(product=cake).exists()

    def test_allow_negative_clamps_commissary_to_zero_but_still_credits_destination(
        self, staff, commissary, batangas, cake
    ):
        _production(staff, cake, 5, status="COMPLETED", quality="PASS")
        _transfer(staff, commissary, batangas, cake, 20, StockTransferOrder.Status.DELIVERED)

        call_command("backfill_finished_goods_inventory", "--allow-negative")

        assert Inventory.objects.get(branch=commissary, product=cake).quantity_on_hand == 0
        # The destination still received the full delivered quantity --
        # clamping is a commissary-side data-inconsistency flag, not a
        # reason to understate what a branch actually received.
        assert Inventory.objects.get(branch=batangas, product=cake).quantity_on_hand == 20
