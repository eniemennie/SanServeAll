"""
Tests for the run_low_stock_alert management command -- the standalone CLI
entry point that lets PythonAnywhere Scheduled Tasks call the low-stock
alert job directly (see run_low_stock_alert.py's module docstring). The
underlying job itself is already covered in test_jobs.py; these tests only
confirm the command wrapper is wired to it correctly and reports sensibly.
"""

import io

import pytest
from django.core.management import call_command

from apps.accounts.models import Branch
from apps.inventory.models import Inventory, Product
from apps.system_config.models import SystemConfiguration

pytestmark = pytest.mark.django_db


@pytest.fixture
def branch():
    return Branch.objects.create(name="Lipa City", code="LIPA")


class TestRunLowStockAlertCommand:
    def test_reports_everything_adequately_stocked(self, branch):
        healthy = Product.objects.create(name="Flour", price="45.00")
        Inventory.objects.create(
            branch=branch, product=healthy, quantity_on_hand=50, reorder_threshold=10
        )

        out = io.StringIO()
        call_command("run_low_stock_alert", stdout=out)
        assert "adequately stocked" in out.getvalue().lower()

    def test_reports_how_many_branches_were_alerted(self, branch, mailoutbox):
        config = SystemConfiguration.load()
        config.admin_alert_email = "owner@example.com"
        config.save()

        critical = Product.objects.create(name="Cashews", price="650.00")
        Inventory.objects.create(
            branch=branch, product=critical, quantity_on_hand=0, reorder_threshold=10
        )

        out = io.StringIO()
        call_command("run_low_stock_alert", stdout=out)
        assert "1 branch" in out.getvalue().lower()
        assert len(mailoutbox) == 1
