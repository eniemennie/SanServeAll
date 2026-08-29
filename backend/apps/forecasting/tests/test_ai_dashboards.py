"""
Tests for Week 11 (batch 2): AI-Powered Decision Support (Row 11.3a),
Forecasting Dashboard (Row 11.3b), Resource Management Dashboard
(Row 11.3c). Focused heavily on the "latest per branch/product" logic,
since Forecast and InventoryRiskScore are append-only logs that a naive
query would otherwise mix stale and fresh rows together on.
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Branch, Role, User
from apps.forecasting import services
from apps.forecasting.models import Forecast, InventoryRiskScore
from apps.inventory.models import Product
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


@pytest.fixture
def owner():
    role = Role.objects.create(name=Role.OWNER_ADMIN)
    return User.objects.create_user(username="owner1", password="testpass123", role=role)


@pytest.fixture
def owner_client(client, owner):
    client.force_login(owner)
    return client


@pytest.fixture
def latte():
    return Product.objects.create(name="Spanish Latte", price="125.00")


def _completed_sale(branch, cashier, product, quantity, days_ago=0, hour=None):
    when = timezone.now() - timedelta(days=days_ago)
    if hour is not None:
        # get_peak_hour() reports LOCAL wall-clock hour (a business owner
        # thinks in local time, not UTC) -- so the requested hour must be
        # set on the LOCAL representation, not on the UTC-based `when`
        # directly, or it's off by the local UTC offset (Asia/Manila is
        # UTC+8, so setting UTC hour=9 directly produces local hour=17).
        when = timezone.localtime(when).replace(hour=hour, minute=0, second=0, microsecond=0)
    transaction = SalesTransaction.objects.create(
        branch=branch,
        cashier=cashier,
        status=SalesTransaction.Status.COMPLETED,
        payment_method="CASH",
        amount_tendered="1000.00",
        completed_at=when,
    )
    SalesItem.objects.create(
        transaction=transaction, product=product, unit_price=product.price, quantity=quantity
    )
    return transaction


class TestGetLatestRiskScores:
    def test_only_returns_the_most_recent_score_per_pair(self, branch, latte):
        """The critical correctness property: an old stale score must
        not appear alongside a newer one for the same product/branch."""
        InventoryRiskScore.objects.create(
            branch=branch,
            product=latte,
            risk_level="LOW",
            quantity_on_hand=500,
            avg_daily_demand=1.0,
            days_of_stock_left=500.0,
        )
        newest = InventoryRiskScore.objects.create(
            branch=branch,
            product=latte,
            risk_level="HIGH",
            quantity_on_hand=1,
            avg_daily_demand=5.0,
            days_of_stock_left=0.2,
        )

        latest = list(services.get_latest_risk_scores(branch))
        assert len(latest) == 1
        assert latest[0].pk == newest.pk
        assert latest[0].risk_level == "HIGH"

    def test_branch_filter_scopes_correctly(self, branch, latte):
        other_branch = Branch.objects.create(name="Batangas City", code="BATANGAS")
        InventoryRiskScore.objects.create(
            branch=branch,
            product=latte,
            risk_level="LOW",
            quantity_on_hand=100,
            avg_daily_demand=1.0,
            days_of_stock_left=100.0,
        )
        InventoryRiskScore.objects.create(
            branch=other_branch,
            product=latte,
            risk_level="HIGH",
            quantity_on_hand=1,
            avg_daily_demand=5.0,
            days_of_stock_left=0.2,
        )

        latest = list(services.get_latest_risk_scores(branch))
        assert len(latest) == 1
        assert latest[0].branch == branch


class TestCalculateRecommendedReorderQuantity:
    def test_zero_demand_recommends_nothing(self):
        assert services.calculate_recommended_reorder_quantity(0, 5) == 0

    def test_recommends_enough_to_cover_target_days_minus_current_stock(self):
        # avg_daily_demand=10, target_days=14 -> need 140, have 20 -> recommend 120
        assert services.calculate_recommended_reorder_quantity(10, 20, target_days=14) == 120

    def test_never_recommends_a_negative_quantity(self):
        # Already has more than enough stock
        assert services.calculate_recommended_reorder_quantity(1, 1000) == 0


class TestGetCriticalAlerts:
    def test_only_includes_high_risk_items(self, branch, latte):
        InventoryRiskScore.objects.create(
            branch=branch,
            product=latte,
            risk_level="MEDIUM",
            quantity_on_hand=10,
            avg_daily_demand=2.0,
            days_of_stock_left=5.0,
        )
        alerts = services.get_critical_alerts(branch)
        assert alerts == []

    def test_includes_a_reorder_recommendation(self, branch, latte):
        InventoryRiskScore.objects.create(
            branch=branch,
            product=latte,
            risk_level="HIGH",
            quantity_on_hand=1,
            avg_daily_demand=10.0,
            days_of_stock_left=0.1,
        )
        alerts = services.get_critical_alerts(branch)
        assert len(alerts) == 1
        assert alerts[0]["recommended_reorder_quantity"] > 0


class TestGetSlowMovingProducts:
    def test_excludes_out_of_stock_items(self, branch, latte):
        InventoryRiskScore.objects.create(
            branch=branch,
            product=latte,
            risk_level="HIGH",
            quantity_on_hand=0,
            avg_daily_demand=0.0,
            days_of_stock_left=None,
        )
        slow_movers = services.get_slow_moving_products(branch)
        assert slow_movers == []

    def test_orders_by_lowest_demand_first(self, branch):
        fast = Product.objects.create(name="Fast Seller", price="100.00")
        slow = Product.objects.create(name="Slow Seller", price="100.00")
        InventoryRiskScore.objects.create(
            branch=branch,
            product=fast,
            risk_level="LOW",
            quantity_on_hand=50,
            avg_daily_demand=20.0,
            days_of_stock_left=2.5,
        )
        InventoryRiskScore.objects.create(
            branch=branch,
            product=slow,
            risk_level="LOW",
            quantity_on_hand=50,
            avg_daily_demand=1.0,
            days_of_stock_left=50.0,
        )

        results = services.get_slow_moving_products(branch)
        assert results[0].product.name == "Slow Seller"


class TestGetPeakHour:
    def test_no_transactions_returns_none(self, branch):
        assert services.get_peak_hour(branch) is None

    def test_identifies_the_hour_with_most_transactions(self, branch, cashier, latte):
        _completed_sale(branch, cashier, latte, 1, hour=9)
        _completed_sale(branch, cashier, latte, 1, hour=9)
        _completed_sale(branch, cashier, latte, 1, hour=14)

        result = services.get_peak_hour(branch)
        assert result["hour"] == 9
        assert result["transaction_count"] == 2


class TestDetectUnusualPatterns:
    def test_no_history_produces_no_anomalies(self, branch):
        assert services.detect_unusual_patterns(branch) == []

    def test_a_real_deviation_is_flagged(self, branch, cashier, latte):
        # Same weekday, 1-4 weeks ago: consistently ~5 units
        for weeks_ago in range(1, 5):
            _completed_sale(branch, cashier, latte, quantity=5, days_ago=1 + (weeks_ago * 7))
        # Yesterday: a big spike to 50 units (way more than 50% above baseline)
        _completed_sale(branch, cashier, latte, quantity=50, days_ago=1)

        anomalies = services.detect_unusual_patterns(branch)
        assert len(anomalies) == 1
        assert anomalies[0]["product"] == latte
        assert anomalies[0]["yesterday_units"] == 50

    def test_normal_variation_is_not_flagged(self, branch, cashier, latte):
        for weeks_ago in range(1, 5):
            _completed_sale(branch, cashier, latte, quantity=5, days_ago=1 + (weeks_ago * 7))
        _completed_sale(branch, cashier, latte, quantity=6, days_ago=1)  # within 50%

        anomalies = services.detect_unusual_patterns(branch)
        assert anomalies == []


class TestGetLatestForecastBatch:
    def test_only_returns_the_most_recent_run(self, branch, latte):
        old_forecast = Forecast.objects.create(
            branch=branch,
            product=latte,
            forecast_date=timezone.now().date(),
            predicted_quantity=5.0,
            model_used="NAIVE_AVERAGE",
        )
        old_forecast.generated_at = timezone.now() - timedelta(days=1)
        old_forecast.save()

        Forecast.objects.create(
            branch=branch,
            product=latte,
            forecast_date=timezone.now().date(),
            predicted_quantity=8.0,
            model_used="ARIMA(1, 1, 1)",
        )

        batch = list(services.get_latest_forecast_batch(branch))
        assert len(batch) == 1
        assert batch[0].predicted_quantity == 8.0


class TestGetForecastingDashboardSummary:
    def test_no_forecasts_returns_zeroed_summary(self, branch):
        summary = services.get_forecasting_dashboard_summary(branch)
        assert summary["predictions_made"] == 0
        assert summary["avg_mae"] is None

    def test_counts_predictions_and_model_types(self, branch, latte):
        cake = Product.objects.create(name="Cake", price="140.00")
        Forecast.objects.create(
            branch=branch,
            product=latte,
            forecast_date=timezone.now().date(),
            predicted_quantity=8.0,
            model_used="ARIMA(1, 1, 1)",
            mae=1.5,
        )
        Forecast.objects.create(
            branch=branch,
            product=cake,
            forecast_date=timezone.now().date(),
            predicted_quantity=3.0,
            model_used="NAIVE_AVERAGE",
        )

        summary = services.get_forecasting_dashboard_summary(branch)
        assert summary["predictions_made"] == 2
        assert summary["products_forecasted"] == 2
        assert summary["arima_count"] == 1
        assert summary["naive_count"] == 1
        assert summary["avg_mae"] == 1.5


class TestGetWeeklyDemandPattern:
    def test_sums_predicted_quantity_per_date(self, branch, latte):
        cake = Product.objects.create(name="Cake", price="140.00")
        target_date = timezone.now().date() + timedelta(days=1)
        Forecast.objects.create(
            branch=branch,
            product=latte,
            forecast_date=target_date,
            predicted_quantity=5.0,
            model_used="ARIMA(1, 1, 1)",
        )
        Forecast.objects.create(
            branch=branch,
            product=cake,
            forecast_date=target_date,
            predicted_quantity=3.0,
            model_used="ARIMA(1, 1, 1)",
        )

        pattern = services.get_weekly_demand_pattern(branch)
        assert len(pattern) == 1
        assert pattern[0]["predicted_total"] == 8.0


class TestDecisionSupportView:
    def test_owner_can_view(self, owner_client):
        response = owner_client.get(reverse("forecasting:decision_support"))
        assert response.status_code == 200

    def test_non_owner_cannot_view(self, client, cashier):
        client.force_login(cashier)
        response = client.get(reverse("forecasting:decision_support"))
        assert response.status_code == 403

    def test_critical_alert_appears_on_page(self, owner_client, branch, latte):
        InventoryRiskScore.objects.create(
            branch=branch,
            product=latte,
            risk_level="HIGH",
            quantity_on_hand=0,
            avg_daily_demand=5.0,
            days_of_stock_left=0.0,
        )
        response = owner_client.get(reverse("forecasting:decision_support"), {"branch": branch.pk})
        assert b"Spanish Latte" in response.content


class TestForecastingDashboardView:
    def test_owner_can_view(self, owner_client):
        response = owner_client.get(reverse("forecasting:forecasting_dashboard"))
        assert response.status_code == 200

    def test_non_owner_cannot_view(self, client, cashier):
        client.force_login(cashier)
        response = client.get(reverse("forecasting:forecasting_dashboard"))
        assert response.status_code == 403

    def test_chart_js_is_loaded(self, owner_client):
        response = owner_client.get(reverse("forecasting:forecasting_dashboard"))
        assert b"chart.umd.min.js" in response.content


class TestResourceManagementDashboardView:
    def test_owner_can_view(self, owner_client):
        response = owner_client.get(reverse("forecasting:resource_management_dashboard"))
        assert response.status_code == 200

    def test_non_owner_cannot_view(self, client, cashier):
        client.force_login(cashier)
        response = client.get(reverse("forecasting:resource_management_dashboard"))
        assert response.status_code == 403

    def test_material_alerts_show_commissary_data_not_filtered_by_customer_branch(
        self, owner_client, branch
    ):
        """Regression test: raw materials only ever have Inventory rows
        at the commissary, never at a customer-facing branch like
        `branch`. This dashboard must show commissary material alerts
        regardless -- a branch-scoped query here previously (incorrectly)
        showed zero alerts, silently masking real restocking needs."""
        commissary = Branch.objects.create(name="Commissary", code="COMMISSARY", is_commissary=True)
        flour = Product.objects.create(
            name="Flour (kg)", price="55.00", product_type=Product.ProductType.MATERIAL
        )
        InventoryRiskScore.objects.create(
            branch=commissary,
            product=flour,
            risk_level="HIGH",
            quantity_on_hand=2,
            avg_daily_demand=5.0,
            days_of_stock_left=0.4,
        )

        response = owner_client.get(reverse("forecasting:resource_management_dashboard"))
        assert b"Flour (kg)" in response.content
