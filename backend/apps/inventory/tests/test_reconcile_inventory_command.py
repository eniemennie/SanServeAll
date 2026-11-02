"""
Tests for the reconcile_inventory management command (backend gap
assessment item 29): a periodic consistency check summing
InventoryTransaction deltas and comparing the total to each Inventory
row's own quantity_on_hand.
"""

import io

import pytest
from django.core.management import call_command

from apps.accounts.models import Branch
from apps.inventory import services
from apps.inventory.models import Product

pytestmark = pytest.mark.django_db


@pytest.fixture
def branch():
    return Branch.objects.create(name="Lipa City", code="LIPA")


class TestReconcileInventoryCommand:
    def test_reports_success_and_exits_cleanly_when_everything_reconciles(self, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        inv = services.get_or_create_inventory_row(branch, product)
        services.adjust_stock(inv, 10)

        out = io.StringIO()
        call_command("reconcile_inventory", stdout=out)
        assert "no discrepancies" in out.getvalue().lower()

    def test_reports_each_discrepancy_and_exits_with_a_nonzero_status(self, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        inv = services.get_or_create_inventory_row(branch, product)
        services.adjust_stock(inv, 10)
        inv.quantity_on_hand = 999
        inv.save()

        out = io.StringIO()
        with pytest.raises(SystemExit) as exc_info:
            call_command("reconcile_inventory", stdout=out)

        assert exc_info.value.code == 1
        output = out.getvalue()
        assert "1 discrepancy" in output
        assert "Widget" in output
        assert branch.name in output
        # quantity_on_hand is Decimal (client raw-data import), not int --
        # a regression guard for the ":+d" int-only format spec that used
        # to crash this command outright on any discrepancy.
        assert "+989.00" in output

    def test_formats_a_negative_difference_without_crashing(self, branch):
        """Same Decimal-formatting regression guard as the test above,
        for the other sign."""
        product = Product.objects.create(name="Widget", price="10.00")
        inv = services.get_or_create_inventory_row(branch, product)
        services.adjust_stock(inv, 10)
        inv.quantity_on_hand = 1
        inv.save()

        out = io.StringIO()
        with pytest.raises(SystemExit):
            call_command("reconcile_inventory", stdout=out)

        assert "-9.00" in out.getvalue()
