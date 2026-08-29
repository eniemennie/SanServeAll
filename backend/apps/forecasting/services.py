"""
Orchestration only (Row 10 overview): pulls data via pos, calls the ml/
layer, stores results. Never called synchronously from pos/services.py --
this only ever runs from a scheduled job (Row 10.3), matching the Phase 2
decoupling rule that AI processing must not sit in the live POS request
path.
"""

import datetime

from django.utils import timezone

from apps.forecasting.ml.arima_model import generate_forecast
from apps.forecasting.ml.data_prep import build_daily_sales_series
from apps.forecasting.models import Forecast
from apps.inventory.models import Product
from apps.pos.models import SalesItem, SalesTransaction


def run_forecast_for_product(branch, product, steps=7):
    """Generates and saves a forecast for one (branch, product) pair.
    Returns the list of created Forecast rows."""
    series = build_daily_sales_series(branch, product)
    result = generate_forecast(series, steps=steps)

    today = timezone.now().date()
    forecasts = []
    for day_offset, predicted_quantity in enumerate(result["predicted_values"], start=1):
        forecasts.append(
            Forecast(
                branch=branch,
                product=product,
                forecast_date=today + datetime.timedelta(days=day_offset),
                predicted_quantity=predicted_quantity,
                model_used=result["model_used"],
                mae=result["mae"],
            )
        )
    Forecast.objects.bulk_create(forecasts)
    return forecasts


def get_branch_product_pairs_with_sales_history():
    """Only forecast (branch, product) pairs that have ever had at least
    one completed sale -- generating a forecast for a product nobody has
    ever bought at a given branch produces nothing but a flat-zero
    naive-average row, which isn't useful output worth storing."""
    pairs = (
        SalesItem.objects.filter(
            transaction__status=SalesTransaction.Status.COMPLETED, product__isnull=False
        )
        .values_list("transaction__branch_id", "product_id")
        .distinct()
    )
    return list(pairs)


def run_forecast_for_all_products(steps=7):
    """Entry point for the scheduled job (Row 10.3). Runs every (branch,
    product) pair with real sales history; one pair's failure doesn't
    stop the rest from being processed."""
    from apps.accounts.models import Branch

    results = {"succeeded": 0, "failed": []}

    for branch_id, product_id in get_branch_product_pairs_with_sales_history():
        try:
            branch = Branch.objects.get(pk=branch_id)
            product = Product.objects.get(pk=product_id)
            run_forecast_for_product(branch, product, steps=steps)
            results["succeeded"] += 1
        except Exception as exc:
            results["failed"].append(
                {"branch_id": branch_id, "product_id": product_id, "error": str(exc)}
            )

    return results


def run_risk_classification_for_all_inventory():
    """Entry point for the risk-classification scheduled job (Row 11.1).
    Classifies every Inventory row across every branch and saves the
    results -- the dashboard (Week 11.3) reads these saved rows rather
    than re-running the classifier on every page load."""
    from apps.forecasting.ml.risk_classifier import classify_inventory_rows
    from apps.forecasting.models import InventoryRiskScore
    from apps.inventory.models import Inventory

    results = classify_inventory_rows(Inventory.objects.select_related("branch", "product").all())

    scores = [
        InventoryRiskScore(
            branch=r["inventory"].branch,
            product=r["inventory"].product,
            risk_level=r["risk_level"],
            quantity_on_hand=r["quantity_on_hand"],
            avg_daily_demand=r["avg_daily_demand"],
            days_of_stock_left=r["days_of_stock_left"],
        )
        for r in results
    ]
    InventoryRiskScore.objects.bulk_create(scores)
    return scores


def generate_insights_for_all_branches():
    """Entry point for the insight-generation scheduled job (Row 11.2).
    For each branch with at least one high/medium-risk item, generates a
    stockout-warning insight; runs after risk classification so it has
    fresh data to summarize, not a stale prior run's results."""
    from apps.accounts.models import Branch
    from apps.forecasting.ml.insight_generator import generate_insight
    from apps.forecasting.models import AIInsight, InventoryRiskScore

    insights = []
    latest_scores_by_branch = {}
    for score in InventoryRiskScore.objects.filter(
        risk_level__in=[InventoryRiskScore.RiskLevel.HIGH, InventoryRiskScore.RiskLevel.MEDIUM]
    ).select_related("branch", "product"):
        latest_scores_by_branch.setdefault(score.branch_id, []).append(score)

    for branch_id, scores in latest_scores_by_branch.items():
        branch = Branch.objects.get(pk=branch_id)
        at_risk_items = ", ".join(f"{s.product.name} ({s.risk_level})" for s in scores[:5])

        message, generated_by_ai = generate_insight(
            "STOCKOUT_WARNING", {"branch_name": branch.name, "at_risk_items": at_risk_items}
        )
        insight = AIInsight.objects.create(
            branch=branch,
            insight_type=AIInsight.InsightType.STOCKOUT_WARNING,
            message=message,
            generated_by_ai=generated_by_ai,
        )
        insights.append(insight)

    return insights
