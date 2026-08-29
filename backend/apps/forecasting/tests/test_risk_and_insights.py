"""
Tests for Week 11 (batch 1): Inventory Risk Classifier (Row 11.1) and
Natural-Language Insight Generator (Row 11.2). The AI dashboards
themselves (Row 11.3) are a separate, later batch.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from apps.accounts.models import Branch, Role, User
from apps.forecasting import services
from apps.forecasting.ml.insight_generator import generate_insight
from apps.forecasting.ml.risk_classifier import (
    classify_inventory_rows,
    compute_average_daily_demand,
    rule_based_label,
)
from apps.forecasting.models import AIInsight, InventoryRiskScore
from apps.inventory.models import Inventory, Product
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


class TestRuleBasedLabel:
    def test_zero_stock_is_always_high_risk(self):
        assert rule_based_label(0, 5.0, 0.0) == "HIGH"

    def test_no_demand_is_low_risk_regardless_of_stock(self):
        assert rule_based_label(2, 0.0, None) == "LOW"

    def test_few_days_left_is_high_risk(self):
        assert rule_based_label(5, 2.0, 2.5) == "HIGH"

    def test_medium_days_left_is_medium_risk(self):
        assert rule_based_label(50, 5.0, 8.0) == "MEDIUM"

    def test_many_days_left_is_low_risk(self):
        assert rule_based_label(500, 5.0, 100.0) == "LOW"


class TestComputeAverageDailyDemand:
    def test_no_sales_is_zero_demand(self, branch, latte):
        assert compute_average_daily_demand(branch, latte, days_history=30) == 0.0

    def test_sales_are_averaged_over_the_window(self, branch, cashier, latte):
        _completed_sale(branch, cashier, latte, quantity=30, days_ago=1)
        avg = compute_average_daily_demand(branch, latte, days_history=30)
        assert avg == pytest.approx(1.0)  # 30 units / 30 days

    def test_material_demand_comes_from_production_consumption_not_sales(self, branch):
        """The critical fix: materials are never sold via POS -- reading
        SalesItem for a material would always return zero regardless of
        real consumption, making it impossible to ever flag a material as
        at-risk. Demand must come from IngredientUsage instead."""
        from apps.accounts.models import Branch, Role, User
        from apps.production.models import IngredientUsage, ProductionRecord

        commissary = Branch.objects.create(name="Commissary", code="COMMISSARY", is_commissary=True)
        role = Role.objects.create(name=Role.COMMISSARY_STAFF)
        staff = User.objects.create_user(username="commissary1", password="testpass123", role=role)
        flour = Product.objects.create(
            name="Flour (kg)", price="55.00", product_type=Product.ProductType.MATERIAL
        )
        cake = Product.objects.create(name="Cake", price="140.00")

        record = ProductionRecord.objects.create(
            commissary_staff=staff, product=cake, quantity_produced=10
        )
        IngredientUsage.objects.create(production_record=record, material=flour, quantity_used=30)

        avg = compute_average_daily_demand(commissary, flour, days_history=30)
        assert avg == pytest.approx(1.0)  # 30 units used / 30 days

    def test_material_with_zero_production_usage_is_zero_demand(self, branch):
        material = Product.objects.create(
            name="Unused Material", price="10.00", product_type=Product.ProductType.MATERIAL
        )
        avg = compute_average_daily_demand(branch, material, days_history=30)
        assert avg == 0.0


class TestClassifyInventoryRows:
    def test_empty_queryset_returns_empty_list(self):
        assert classify_inventory_rows(Inventory.objects.none()) == []

    def test_single_row_uses_rule_directly_no_crash(self, branch, latte):
        """A DecisionTreeClassifier can't fit on a single class -- this
        must fall back to the rule directly rather than raising."""
        inv = Inventory.objects.create(branch=branch, product=latte, quantity_on_hand=0)
        results = classify_inventory_rows(Inventory.objects.filter(pk=inv.pk))
        assert len(results) == 1
        assert results[0]["risk_level"] == "HIGH"  # zero stock -- rule says HIGH

    def test_out_of_stock_item_is_classified_high_risk(self, branch, cashier, latte):
        _completed_sale(branch, cashier, latte, quantity=5, days_ago=1)
        cake = Product.objects.create(name="Cake", price="140.00")
        _completed_sale(branch, cashier, cake, quantity=5, days_ago=1)

        Inventory.objects.create(branch=branch, product=latte, quantity_on_hand=0)
        healthy_inv = Inventory.objects.create(branch=branch, product=cake, quantity_on_hand=500)

        results = classify_inventory_rows(Inventory.objects.filter(branch=branch))
        by_product = {r["inventory"].product.name: r for r in results}
        assert by_product["Spanish Latte"]["risk_level"] == "HIGH"
        assert by_product["Cake"]["risk_level"] == "LOW"
        assert healthy_inv.quantity_on_hand == 500  # sanity: not mutated

    def test_days_of_stock_left_is_none_when_no_demand(self, branch, latte):
        Inventory.objects.create(branch=branch, product=latte, quantity_on_hand=50)
        results = classify_inventory_rows(Inventory.objects.filter(branch=branch))
        assert results[0]["days_of_stock_left"] is None


class TestRunRiskClassificationForAllInventory:
    def test_saves_a_score_per_inventory_row(self, branch, latte):
        Inventory.objects.create(branch=branch, product=latte, quantity_on_hand=10)
        scores = services.run_risk_classification_for_all_inventory()
        assert len(scores) == 1
        assert InventoryRiskScore.objects.count() == 1

    def test_no_inventory_produces_no_scores(self):
        scores = services.run_risk_classification_for_all_inventory()
        assert scores == []


class TestInsightGenerator:
    def test_no_api_key_falls_back_to_template(self, settings):
        settings.CLAUDE_API_KEY = ""
        message, generated_by_ai = generate_insight(
            "STOCKOUT_WARNING",
            {"branch_name": "Lipa City", "at_risk_items": "Spanish Latte (HIGH)"},
        )
        assert generated_by_ai is False
        assert "Lipa City" in message
        assert "Spanish Latte" in message

    def test_successful_api_call_returns_ai_generated_true(self, settings):
        settings.CLAUDE_API_KEY = "fake-test-key"
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "content": [{"text": "Lipa City is low on Spanish Latte -- restock soon."}]
        }
        mock_response.raise_for_status.return_value = None

        with patch(
            "apps.forecasting.ml.insight_generator.requests.post", return_value=mock_response
        ):
            message, generated_by_ai = generate_insight(
                "STOCKOUT_WARNING",
                {"branch_name": "Lipa City", "at_risk_items": "Spanish Latte (HIGH)"},
            )

        assert generated_by_ai is True
        assert message == "Lipa City is low on Spanish Latte -- restock soon."

    def test_api_failure_falls_back_to_template_not_an_exception(self, settings):
        settings.CLAUDE_API_KEY = "fake-test-key"
        with patch(
            "apps.forecasting.ml.insight_generator.requests.post",
            side_effect=ConnectionError("network unreachable"),
        ):
            message, generated_by_ai = generate_insight(
                "DEMAND_SUMMARY",
                {"branch_name": "Lipa City", "forecast_summary": "steady demand expected"},
            )

        assert generated_by_ai is False
        assert "Lipa City" in message

    def test_unknown_insight_type_raises_value_error(self):
        with pytest.raises(ValueError):
            generate_insight("NOT_A_REAL_TYPE", {})


class TestGenerateInsightsForAllBranches:
    def test_only_generates_for_branches_with_at_risk_items(self, branch, latte, settings):
        settings.CLAUDE_API_KEY = ""
        healthy_branch = Branch.objects.create(name="Batangas City", code="BATANGAS")

        InventoryRiskScore.objects.create(
            branch=branch,
            product=latte,
            risk_level="HIGH",
            quantity_on_hand=0,
            avg_daily_demand=5.0,
            days_of_stock_left=0.0,
        )
        InventoryRiskScore.objects.create(
            branch=healthy_branch,
            product=latte,
            risk_level="LOW",
            quantity_on_hand=500,
            avg_daily_demand=5.0,
            days_of_stock_left=100.0,
        )

        insights = services.generate_insights_for_all_branches()
        branch_ids = [i.branch_id for i in insights]
        assert branch.pk in branch_ids
        assert healthy_branch.pk not in branch_ids

    def test_creates_a_stockout_warning_insight(self, branch, latte, settings):
        settings.CLAUDE_API_KEY = ""
        InventoryRiskScore.objects.create(
            branch=branch,
            product=latte,
            risk_level="HIGH",
            quantity_on_hand=0,
            avg_daily_demand=5.0,
            days_of_stock_left=0.0,
        )
        services.generate_insights_for_all_branches()
        insight = AIInsight.objects.get(branch=branch)
        assert insight.insight_type == AIInsight.InsightType.STOCKOUT_WARNING
        assert insight.generated_by_ai is False
