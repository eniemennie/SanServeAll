"""
Tests for Week 9: Sales/Analytics Dashboard (Row 9.1), Product
Performance (Row 9.2), Resource Consumption & Operational Performance
(Row 9.3).
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Branch, Role, User
from apps.analytics import services
from apps.inventory.models import Product
from apps.pos.models import SalesItem, SalesTransaction
from apps.production.models import IngredientUsage, ProductionRecord

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


@pytest.fixture
def owner(branch):
    role = Role.objects.create(name=Role.OWNER_ADMIN)
    return User.objects.create_user(username="owner1", password="testpass123", role=role)


@pytest.fixture
def owner_client(client, owner):
    client.force_login(owner)
    return client


@pytest.fixture
def latte():
    return Product.objects.create(name="Spanish Latte", price="125.00")


def _completed_sale(branch, cashier, product, quantity, unit_price, amount_tendered, when=None):
    """Creates a real COMPLETED sale with a deliberately different
    amount_tendered than the actual total -- this is the key setup for
    proving the revenue calculation ignores amount_tendered."""
    transaction = SalesTransaction.objects.create(
        branch=branch,
        cashier=cashier,
        status=SalesTransaction.Status.COMPLETED,
        payment_method="CASH",
        amount_tendered=amount_tendered,
        completed_at=when or timezone.now(),
    )
    SalesItem.objects.create(
        transaction=transaction, product=product, unit_price=unit_price, quantity=quantity
    )
    return transaction


class TestSalesSummary:
    def test_revenue_uses_sale_total_not_amount_tendered(self, branch, cashier, latte):
        """The critical correctness test: a Php375 sale paid with a
        Php500 bill must report Php375 revenue, not Php500."""
        _completed_sale(
            branch, cashier, latte, quantity=3, unit_price="125.00", amount_tendered="500.00"
        )

        summary = services.get_sales_summary(branch=branch)
        assert summary["total_revenue"] == pytest.approx(375.00)

    def test_draft_transactions_are_excluded(self, branch, cashier, latte):
        SalesTransaction.objects.create(
            branch=branch, cashier=cashier, status=SalesTransaction.Status.DRAFT
        )
        summary = services.get_sales_summary(branch=branch)
        assert summary["total_transactions"] == 0

    def test_old_sales_outside_the_window_are_excluded(self, branch, cashier, latte):
        old_date = timezone.now() - timedelta(days=60)
        _completed_sale(
            branch,
            cashier,
            latte,
            quantity=1,
            unit_price="125.00",
            amount_tendered="125.00",
            when=old_date,
        )
        summary = services.get_sales_summary(branch=branch, days=30)
        assert summary["total_revenue"] == 0

    def test_branch_filter_only_counts_that_branch(self, branch, cashier, latte):
        other_branch = Branch.objects.create(name="Batangas City", code="BATANGAS")
        other_cashier = User.objects.create_user(
            username="cashier2", password="testpass123", role=cashier.role, branch=other_branch
        )
        _completed_sale(branch, cashier, latte, 1, "125.00", "125.00")
        _completed_sale(other_branch, other_cashier, latte, 1, "125.00", "125.00")

        summary = services.get_sales_summary(branch=branch)
        assert summary["total_transactions"] == 1

    def test_units_sold_counts_quantity_not_transaction_count(self, branch, cashier, latte):
        _completed_sale(
            branch, cashier, latte, quantity=5, unit_price="125.00", amount_tendered="700.00"
        )
        summary = services.get_sales_summary(branch=branch)
        assert summary["total_units_sold"] == 5
        assert summary["total_transactions"] == 1


class TestWeeklySalesTrend:
    def test_returns_requested_number_of_weeks(self, branch, cashier, latte):
        trend = services.get_weekly_sales_trend(branch=branch, weeks=8)
        assert len(trend) == 8

    def test_trend_also_uses_sale_total_not_amount_tendered(self, branch, cashier, latte):
        _completed_sale(
            branch, cashier, latte, quantity=2, unit_price="125.00", amount_tendered="1000.00"
        )
        trend = services.get_weekly_sales_trend(branch=branch, weeks=1)
        assert trend[0]["revenue"] == pytest.approx(250.00)


class TestTopProducts:
    def test_orders_by_units_sold_descending(self, branch, cashier, latte):
        cake = Product.objects.create(name="Chocolate Cake", price="140.00")
        _completed_sale(
            branch, cashier, latte, quantity=2, unit_price="125.00", amount_tendered="250.00"
        )
        _completed_sale(
            branch, cashier, cake, quantity=10, unit_price="140.00", amount_tendered="1400.00"
        )

        top = list(services.get_top_products(branch=branch))
        assert top[0]["product__name"] == "Chocolate Cake"
        assert top[0]["units_sold"] == 10


class TestProductPerformance:
    def test_growth_rate_calculated_correctly(self, branch, cashier, latte):
        now = timezone.now()
        # Previous period: 10 units. Current period: 20 units. +100% growth.
        _completed_sale(
            branch, cashier, latte, 10, "125.00", "1250.00", when=now - timedelta(days=45)
        )
        _completed_sale(
            branch, cashier, latte, 20, "125.00", "2500.00", when=now - timedelta(days=5)
        )

        performance = services.get_product_performance(days=30)
        row = next(r for r in performance if r["product_name"] == "Spanish Latte")
        assert row["current_units"] == 20
        assert row["previous_units"] == 10
        assert row["growth_rate"] == pytest.approx(100.0)

    def test_new_product_this_period_has_no_growth_rate(self, branch, cashier, latte):
        _completed_sale(branch, cashier, latte, 5, "125.00", "625.00", when=timezone.now())
        performance = services.get_product_performance(days=30)
        row = next(r for r in performance if r["product_name"] == "Spanish Latte")
        assert row["previous_units"] == 0
        assert row["growth_rate"] is None

    def test_product_with_no_sales_in_either_window_is_omitted(self, branch, cashier):
        Product.objects.create(name="Unsold Item", price="99.00")
        performance = services.get_product_performance(days=30)
        names = [r["product_name"] for r in performance]
        assert "Unsold Item" not in names


class TestResourceConsumption:
    def test_totals_material_usage_and_cost(self, branch):
        Branch.objects.create(name="Commissary", code="COMMISSARY", is_commissary=True)
        role = Role.objects.create(name=Role.COMMISSARY_STAFF)
        staff = User.objects.create_user(username="commissary1", password="testpass123", role=role)
        flour = Product.objects.create(
            name="Flour (kg)", price="55.00", product_type=Product.ProductType.MATERIAL
        )
        cake = Product.objects.create(name="Cake", price="140.00")

        record = ProductionRecord.objects.create(
            commissary_staff=staff, product=cake, quantity_produced=10
        )
        IngredientUsage.objects.create(production_record=record, material=flour, quantity_used=5)

        summary = services.get_resource_consumption_summary(days=30)
        assert summary["total_units_used"] == 5
        assert summary["total_cost"] == pytest.approx(275.00)  # 5 * 55.00
        assert summary["total_produced"] == 10
        assert summary["cost_per_unit_produced"] == pytest.approx(27.50)  # 275 / 10


class TestOperationalPerformance:
    def test_completion_and_quality_rates(self):
        role = Role.objects.create(name=Role.COMMISSARY_STAFF)
        staff = User.objects.create_user(username="commissary1", password="testpass123", role=role)
        cake = Product.objects.create(name="Cake", price="140.00")

        ProductionRecord.objects.create(
            commissary_staff=staff,
            product=cake,
            quantity_produced=10,
            status=ProductionRecord.Status.COMPLETED,
            quality=ProductionRecord.Quality.PASS,
        )
        ProductionRecord.objects.create(
            commissary_staff=staff,
            product=cake,
            quantity_produced=5,
            status=ProductionRecord.Status.PENDING,
            quality=ProductionRecord.Quality.FAIL,
        )

        summary = services.get_operational_performance_summary(days=30)
        assert summary["total_batches"] == 2
        assert summary["completion_rate"] == pytest.approx(50.0)
        assert summary["quality_pass_rate"] == pytest.approx(50.0)

    def test_no_batches_returns_none_rates_not_zero(self):
        """None (not shown / "--") is more honest than 0% when there's
        simply no data yet -- 0% implies batches failed, not that none exist."""
        summary = services.get_operational_performance_summary(days=30)
        assert summary["total_batches"] == 0
        assert summary["completion_rate"] is None
        assert summary["quality_pass_rate"] is None


class TestSalesDashboardView:
    def test_owner_can_view_dashboard(self, owner_client):
        response = owner_client.get(reverse("analytics:sales_dashboard"))
        assert response.status_code == 200

    def test_non_owner_cannot_view_dashboard(self, client, cashier):
        client.force_login(cashier)
        response = client.get(reverse("analytics:sales_dashboard"))
        assert response.status_code == 403

    def test_branch_filter_shows_low_stock_count(self, owner_client, branch):
        response = owner_client.get(reverse("analytics:sales_dashboard"), {"branch": branch.pk})
        assert response.status_code == 200


class TestProductPerformanceView:
    def test_owner_can_view(self, owner_client):
        response = owner_client.get(reverse("analytics:product_performance"))
        assert response.status_code == 200

    def test_non_owner_cannot_view(self, client, cashier):
        client.force_login(cashier)
        response = client.get(reverse("analytics:product_performance"))
        assert response.status_code == 403


class TestResourceConsumptionView:
    def test_owner_can_view(self, owner_client):
        response = owner_client.get(reverse("analytics:resource_consumption"))
        assert response.status_code == 200

    def test_non_owner_cannot_view(self, client, cashier):
        client.force_login(cashier)
        response = client.get(reverse("analytics:resource_consumption"))
        assert response.status_code == 403
