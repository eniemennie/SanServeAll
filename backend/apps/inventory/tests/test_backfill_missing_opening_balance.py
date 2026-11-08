"""
Tests for the backfill_missing_opening_balance management command --
Consolidated Task Tracker follow-up to
scripts/diagnose_negative_stock_forecast_pairs.py's 2026-11-08 findings
(see that command's module docstring for the full reasoning).
"""

import io
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.accounts.models import Branch, Role, User
from apps.inventory import services
from apps.inventory.models import InventoryTransaction, Product
from apps.pos.models import SalesItem, SalesTransaction

pytestmark = pytest.mark.django_db


@pytest.fixture
def branch():
    return Branch.objects.create(name="Lipa City", code="LIPA")


@pytest.fixture
def cashier(branch):
    role = Role.objects.create(name=Role.BRANCH_STAFF)
    return User.objects.create_user(
        username="cashier1", password="testpass123", role=role, branch=branch
    )


def _oversell(inv, quantity):
    """Drives real stock negative the way an actual POS sale does
    (complete_sale_payment's deduction has no floor, by design -- see
    scripts/backfill_thin_forecast_pairs.py's module docstring) --
    adjust_stock() refuses to go below zero, so it can't simulate this.
    Logs a matching SALE_DEDUCTION so the ledger stays reconciled."""
    inv.quantity_on_hand = Decimal(str(inv.quantity_on_hand)) - quantity
    inv.save()
    InventoryTransaction.objects.create(
        branch=inv.branch,
        product=inv.product,
        movement_type=InventoryTransaction.MovementType.SALE_DEDUCTION,
        quantity_change=-quantity,
    )


def _give_one_sale_day(branch, product, cashier):
    """Registers exactly one non-zero sales day for (branch, product) --
    enough for get_branch_product_pairs_with_sales_history to pick up the
    pair, and far below has_sufficient_history's 3-non-zero-day floor, so
    the pair reads as under-served."""
    transaction = SalesTransaction.objects.create(
        branch=branch,
        cashier=cashier,
        status=SalesTransaction.Status.COMPLETED,
        payment_method="CASH",
        amount_tendered=Decimal("100.00"),
        completed_at=timezone.now(),
    )
    SalesItem.objects.create(
        transaction=transaction, product=product, unit_price=Decimal("50.00"), quantity=1
    )


class TestBackfillMissingOpeningBalanceCommand:
    def test_dry_run_by_default_writes_nothing(self, branch, cashier):
        product = Product.objects.create(name="Java Chip Frappe", price="150.00")
        _give_one_sale_day(branch, product, cashier)
        inv = services.get_or_create_inventory_row(branch, product)
        _oversell(inv, Decimal("1"))

        out = io.StringIO()
        call_command("backfill_missing_opening_balance", stdout=out)

        inv.refresh_from_db()
        assert inv.quantity_on_hand == Decimal("-1")
        assert not InventoryTransaction.objects.filter(
            movement_type=InventoryTransaction.MovementType.HISTORICAL_BACKFILL
        ).exists()
        assert "Dry run: nothing written" in out.getvalue()
        assert "Java Chip Frappe" in out.getvalue()

    def test_apply_credits_a_qualifying_pair_up_to_the_target_balance(self, branch, cashier):
        product = Product.objects.create(name="Java Chip Frappe", price="150.00")
        _give_one_sale_day(branch, product, cashier)
        inv = services.get_or_create_inventory_row(branch, product)
        _oversell(inv, Decimal("1"))

        out = io.StringIO()
        call_command("backfill_missing_opening_balance", "--apply", stdout=out)

        inv.refresh_from_db()
        assert inv.quantity_on_hand == Decimal("10")  # default target balance
        backfill_txn = InventoryTransaction.objects.get(
            branch=branch,
            product=product,
            movement_type=InventoryTransaction.MovementType.HISTORICAL_BACKFILL,
        )
        assert backfill_txn.quantity_change == Decimal("11")  # -1 -> 10

    def test_apply_respects_a_custom_target_balance(self, branch, cashier):
        product = Product.objects.create(name="Java Chip Frappe", price="150.00")
        _give_one_sale_day(branch, product, cashier)
        inv = services.get_or_create_inventory_row(branch, product)
        _oversell(inv, Decimal("2"))

        call_command("backfill_missing_opening_balance", "--apply", "--target-balance", "25")

        inv.refresh_from_db()
        assert inv.quantity_on_hand == Decimal("25")

    def test_skips_a_pair_whose_real_stock_is_already_sufficient(self, branch, cashier):
        product = Product.objects.create(name="Americano (Hot)", price="100.00")
        _give_one_sale_day(branch, product, cashier)
        inv = services.get_or_create_inventory_row(branch, product)
        services.adjust_stock(inv, 398, reason="already stocked")

        call_command("backfill_missing_opening_balance", "--apply")

        inv.refresh_from_db()
        assert inv.quantity_on_hand == Decimal("398")  # untouched

    def test_skips_a_pair_that_already_has_a_historical_backfill(self, branch, cashier):
        """Idempotency: a pair already given an opening balance once
        (whether by this command's own earlier run, or by a one-off
        manual fix like the real Almond 7in Square case) is never
        re-credited."""
        product = Product.objects.create(name="Almond (7in Square)", price="450.00")
        _give_one_sale_day(branch, product, cashier)
        inv = services.get_or_create_inventory_row(branch, product)
        inv.quantity_on_hand = Decimal("0")
        inv.save()
        InventoryTransaction.objects.create(
            branch=branch,
            product=product,
            movement_type=InventoryTransaction.MovementType.HISTORICAL_BACKFILL,
            quantity_change=Decimal("30"),
        )
        InventoryTransaction.objects.create(
            branch=branch,
            product=product,
            movement_type=InventoryTransaction.MovementType.SALE_DEDUCTION,
            quantity_change=Decimal("-30"),
        )

        out = io.StringIO()
        call_command("backfill_missing_opening_balance", "--apply", stdout=out)

        inv.refresh_from_db()
        assert inv.quantity_on_hand == Decimal("0")  # untouched
        assert "No qualifying pairs" in out.getvalue()

    def test_skips_a_pair_whose_ledger_does_not_reconcile(self, branch, cashier):
        """The Americano @ Batangas City / Almond 7in Square @ Batangas
        City case: real quantity_on_hand doesn't match the sum of its
        own logged transactions at all. Crediting more stock on top of
        an already-wrong number would paper over a real data-integrity
        problem, so this command must never touch it."""
        product = Product.objects.create(name="Americano", price="100.00")
        _give_one_sale_day(branch, product, cashier)
        inv = services.get_or_create_inventory_row(branch, product)
        _oversell(inv, Decimal("3"))
        # Silently desync the recorded balance from the ledger -- mirrors
        # the real, unexplained drift found in production, not something
        # any service function here would do on its own.
        inv.quantity_on_hand = Decimal("-5")
        inv.save()

        out = io.StringIO()
        call_command("backfill_missing_opening_balance", "--apply", stdout=out)

        inv.refresh_from_db()
        assert inv.quantity_on_hand == Decimal("-5")  # untouched
        assert "No qualifying pairs" in out.getvalue()

    def test_skips_a_pair_with_enough_sales_history_to_not_be_under_served(self, branch, cashier):
        """A pair with 3+ non-zero sales days doesn't need this at all --
        has_sufficient_history already considers it forecastable."""
        product = Product.objects.create(name="Cafe Latte", price="120.00")
        # 3 DISTINCT days, not 3 same-day sales -- build_daily_sales_series
        # sums per calendar day, so same-day sales would still read as one
        # non-zero day and stay under-served.
        for days_ago in (1, 2, 3):
            transaction = SalesTransaction.objects.create(
                branch=branch,
                cashier=cashier,
                status=SalesTransaction.Status.COMPLETED,
                payment_method="CASH",
                amount_tendered=Decimal("100.00"),
                completed_at=timezone.now() - timezone.timedelta(days=days_ago),
            )
            SalesItem.objects.create(
                transaction=transaction, product=product, unit_price=Decimal("50.00"), quantity=1
            )
        inv = services.get_or_create_inventory_row(branch, product)
        _oversell(inv, Decimal("1"))

        call_command("backfill_missing_opening_balance", "--apply")

        inv.refresh_from_db()
        assert inv.quantity_on_hand == Decimal("-1")  # untouched -- not under-served

    def test_running_twice_does_not_double_credit(self, branch, cashier):
        product = Product.objects.create(name="Java Chip Frappe", price="150.00")
        _give_one_sale_day(branch, product, cashier)
        inv = services.get_or_create_inventory_row(branch, product)
        _oversell(inv, Decimal("1"))

        call_command("backfill_missing_opening_balance", "--apply")
        call_command("backfill_missing_opening_balance", "--apply")

        inv.refresh_from_db()
        assert inv.quantity_on_hand == Decimal("10")
        assert (
            InventoryTransaction.objects.filter(
                branch=branch,
                product=product,
                movement_type=InventoryTransaction.MovementType.HISTORICAL_BACKFILL,
            ).count()
            == 1
        )

    def test_reports_success_message_when_nothing_qualifies(self, branch):
        out = io.StringIO()
        call_command("backfill_missing_opening_balance", stdout=out)
        assert "No qualifying pairs" in out.getvalue()
