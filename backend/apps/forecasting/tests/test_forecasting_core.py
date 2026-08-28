"""
Tests for Week 10: Data Prep Pipeline (Row 10.1), ARIMA Model (Row 10.2),
and Scheduler Job Registration (Row 10.3).
"""

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
from django.utils import timezone

from apps.accounts.models import Branch, Role, User
from apps.forecasting import services
from apps.forecasting.ml.arima_model import generate_forecast
from apps.forecasting.ml.data_prep import build_daily_sales_series, has_sufficient_history
from apps.forecasting.models import Forecast
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
def latte():
    return Product.objects.create(name="Spanish Latte", price="125.00")


def _completed_sale(branch, cashier, product, quantity, days_ago):
    when = timezone.now() - timedelta(days=days_ago)
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


class TestDataPrep:
    def test_no_sales_returns_all_zero_series(self, branch, latte):
        series = build_daily_sales_series(branch, latte, days_history=30)
        assert (series == 0).all()
        assert len(series) == 30

    def test_days_with_sales_reflect_correct_quantity(self, branch, cashier, latte):
        _completed_sale(branch, cashier, latte, quantity=5, days_ago=1)
        series = build_daily_sales_series(branch, latte, days_history=30)
        assert series.iloc[-1] == 5  # yesterday is the last complete day

    def test_days_without_sales_are_zero_not_missing(self, branch, cashier, latte):
        """The critical correctness property: a day with zero sales is a
        real data point (demand was zero), not a gap in the series."""
        _completed_sale(branch, cashier, latte, quantity=10, days_ago=20)
        series = build_daily_sales_series(branch, latte, days_history=30)
        assert series.isna().sum() == 0  # no NaN/missing values anywhere
        assert (series == 0).sum() == 29  # every day except the one sale

    def test_multiple_sales_same_day_are_summed(self, branch, cashier, latte):
        when = timezone.now() - timedelta(days=1)
        for _ in range(3):
            t = SalesTransaction.objects.create(
                branch=branch,
                cashier=cashier,
                status=SalesTransaction.Status.COMPLETED,
                payment_method="CASH",
                amount_tendered="500.00",
                completed_at=when,
            )
            SalesItem.objects.create(transaction=t, product=latte, unit_price="125.00", quantity=2)

        series = build_daily_sales_series(branch, latte, days_history=30)
        assert series.iloc[-1] == 6  # 3 transactions x 2 units each

    def test_today_is_excluded_as_an_incomplete_day(self, branch, cashier, latte):
        """Today isn't a finished day yet -- a sale happening right now
        shouldn't appear in the historical series at all."""
        _completed_sale(branch, cashier, latte, quantity=99, days_ago=0)
        series = build_daily_sales_series(branch, latte, days_history=30)
        assert (series == 0).all()

    def test_draft_transactions_are_excluded(self, branch, cashier, latte):
        SalesTransaction.objects.create(
            branch=branch, cashier=cashier, status=SalesTransaction.Status.DRAFT
        )
        series = build_daily_sales_series(branch, latte, days_history=30)
        assert (series == 0).all()


class TestHasSufficientHistory:
    def test_short_series_is_insufficient(self):
        series = pd.Series([1.0] * 5)
        assert has_sufficient_history(series) is False

    def test_all_zero_series_is_insufficient_even_if_long(self):
        series = pd.Series([0.0] * 60)
        assert has_sufficient_history(series) is False

    def test_long_series_with_real_activity_is_sufficient(self):
        series = pd.Series([1.0, 0.0, 2.0, 0.0] * 20)
        assert has_sufficient_history(series) is True


class TestArimaModel:
    def test_insufficient_history_falls_back_to_naive_average(self):
        series = pd.Series([0.0] * 30)  # all zero -- insufficient
        result = generate_forecast(series, steps=7)
        assert result["model_used"] == "NAIVE_AVERAGE"
        assert len(result["predicted_values"]) == 7
        assert result["mae"] is None

    def test_sufficient_history_uses_arima(self):
        # A synthetic but realistic-looking daily demand series
        rng = np.random.default_rng(42)
        values = 10 + 3 * np.sin(np.linspace(0, 10, 90)) + rng.normal(0, 1, 90)
        series = pd.Series(np.clip(values, 0, None))

        result = generate_forecast(series, steps=7)
        assert result["model_used"].startswith("ARIMA")
        assert len(result["predicted_values"]) == 7

    def test_predicted_values_are_never_negative(self):
        # A series that could plausibly cause ARIMA to predict a dip below zero
        series = pd.Series([0.0, 0.0, 1.0, 0.0, 0.0] * 20)
        result = generate_forecast(series, steps=7)
        assert all(v >= 0 for v in result["predicted_values"])

    def test_naive_forecast_uses_recent_average(self):
        # Genuinely insufficient: fewer than the minimum length AND fewer
        # than 3 non-zero days, so has_sufficient_history is definitely False.
        series = pd.Series([0.0] * 8 + [10.0])
        result = generate_forecast(series, steps=3)
        assert result["model_used"] == "NAIVE_AVERAGE"
        # All 3 predicted values should be identical (naive = flat average)
        assert len(set(result["predicted_values"])) == 1


class TestRunForecastForProduct:
    def test_creates_the_requested_number_of_forecast_rows(self, branch, cashier, latte):
        for i in range(20):
            _completed_sale(branch, cashier, latte, quantity=5, days_ago=i)

        forecasts = services.run_forecast_for_product(branch, latte, steps=7)
        assert len(forecasts) == 7
        assert Forecast.objects.filter(branch=branch, product=latte).count() == 7

    def test_forecast_dates_are_in_the_future_and_sequential(self, branch, cashier, latte):
        for i in range(20):
            _completed_sale(branch, cashier, latte, quantity=5, days_ago=i)

        forecasts = services.run_forecast_for_product(branch, latte, steps=7)
        today = timezone.now().date()
        dates = sorted(f.forecast_date for f in forecasts)
        assert dates[0] == today + timedelta(days=1)
        assert dates[-1] == today + timedelta(days=7)

    def test_no_history_still_produces_a_naive_forecast_not_an_error(self, branch, latte):
        """Even a brand-new product with zero sales history should
        produce SOME output (a naive flat forecast), not raise."""
        forecasts = services.run_forecast_for_product(branch, latte, steps=7)
        assert len(forecasts) == 7
        assert all(f.model_used == "NAIVE_AVERAGE" for f in forecasts)


class TestRunForecastForAllProducts:
    def test_only_processes_products_with_real_sales_history(self, branch, cashier, latte):
        Product.objects.create(name="Never Sold Item", price="99.00")
        _completed_sale(branch, cashier, latte, quantity=5, days_ago=1)

        pairs = services.get_branch_product_pairs_with_sales_history()
        product_ids = [p[1] for p in pairs]
        assert latte.pk in product_ids
        never_sold = Product.objects.get(name="Never Sold Item")
        assert never_sold.pk not in product_ids

    def test_run_for_all_creates_forecasts_for_every_pair_with_history(
        self, branch, cashier, latte
    ):
        cake = Product.objects.create(name="Cake", price="140.00")
        _completed_sale(branch, cashier, latte, quantity=5, days_ago=1)
        _completed_sale(branch, cashier, cake, quantity=3, days_ago=1)

        results = services.run_forecast_for_all_products(steps=7)
        assert results["succeeded"] == 2
        assert results["failed"] == []
        assert Forecast.objects.filter(product=latte).count() == 7
        assert Forecast.objects.filter(product=cake).count() == 7

    def test_one_pair_failing_does_not_block_the_others(self, branch, cashier, latte, monkeypatch):
        cake = Product.objects.create(name="Cake", price="140.00")
        _completed_sale(branch, cashier, latte, quantity=5, days_ago=1)
        _completed_sale(branch, cashier, cake, quantity=3, days_ago=1)

        original = services.run_forecast_for_product

        def _fail_for_latte(branch_arg, product_arg, steps=7):
            if product_arg.pk == latte.pk:
                raise RuntimeError("simulated failure")
            return original(branch_arg, product_arg, steps=steps)

        monkeypatch.setattr(services, "run_forecast_for_product", _fail_for_latte)

        results = services.run_forecast_for_all_products(steps=7)
        assert results["succeeded"] == 1
        assert len(results["failed"]) == 1
        assert Forecast.objects.filter(product=cake).count() == 7
        assert Forecast.objects.filter(product=latte).count() == 0
