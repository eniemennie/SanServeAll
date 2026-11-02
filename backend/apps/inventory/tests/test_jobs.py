"""
Tests for the low-stock alert scheduled-job entrypoint (backend gap
assessment item 20). apps.inventory.jobs stays thin by design (same
pattern as apps.forecasting.jobs), so these tests exist mainly to
confirm the actual email-sending wiring works end-to-end -- the
underlying detection logic itself is covered directly in
test_inventory_module.py's TestGetLowStockAlertsForAllBranches.
"""

import pytest

from apps.accounts.models import Branch
from apps.inventory.jobs import run_low_stock_alert_job
from apps.inventory.models import Inventory, Product
from apps.system_config.models import SystemConfiguration

pytestmark = pytest.mark.django_db


@pytest.fixture
def branch():
    return Branch.objects.create(name="Lipa City", code="LIPA")


class TestRunLowStockAlertJob:
    def test_sends_no_email_when_every_branch_is_adequately_stocked(self, branch, mailoutbox):
        healthy = Product.objects.create(name="Flour", price="45.00")
        Inventory.objects.create(
            branch=branch, product=healthy, quantity_on_hand=50, reorder_threshold=10
        )

        alerts = run_low_stock_alert_job()
        assert alerts == []
        assert len(mailoutbox) == 0

    def test_emails_the_configured_admin_alert_address_when_something_is_low(
        self, branch, mailoutbox
    ):
        config = SystemConfiguration.load()
        config.admin_alert_email = "owner@example.com"
        config.save()

        critical = Product.objects.create(name="Cashews", price="650.00")
        Inventory.objects.create(
            branch=branch, product=critical, quantity_on_hand=0, reorder_threshold=10
        )

        alerts = run_low_stock_alert_job()
        assert len(alerts) == 1
        assert len(mailoutbox) == 1
        assert "owner@example.com" in mailoutbox[0].to
        assert branch.name in mailoutbox[0].body

    def test_falls_back_to_mail_admins_when_no_alert_email_is_configured(
        self, branch, mailoutbox, settings
    ):
        settings.ADMINS = [("Owner", "fallback@example.com")]
        config = SystemConfiguration.load()
        config.admin_alert_email = ""
        config.save()

        critical = Product.objects.create(name="Cashews", price="650.00")
        Inventory.objects.create(
            branch=branch, product=critical, quantity_on_hand=0, reorder_threshold=10
        )

        run_low_stock_alert_job()
        assert len(mailoutbox) == 1
        assert "fallback@example.com" in mailoutbox[0].to
