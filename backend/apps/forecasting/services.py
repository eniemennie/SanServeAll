"""
Orchestration only (Row 10 overview): pulls data via pos, calls the ml/
layer, stores results. Never called synchronously from pos/services.py --
this only ever runs from a scheduled job (Row 10.3), matching the Phase 2
decoupling rule that AI processing must not sit in the live POS request
path.
"""

import datetime

from django.db import models
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

    today = timezone.localdate()
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
    from apps.system_config.models import SystemConfiguration

    config = SystemConfiguration.load()

    insights = []
    latest_scores_by_branch = {}
    for score in get_latest_risk_scores().filter(
        risk_level__in=[InventoryRiskScore.RiskLevel.HIGH, InventoryRiskScore.RiskLevel.MEDIUM]
    ):
        latest_scores_by_branch.setdefault(score.branch_id, []).append(score)

    for branch_id, scores in latest_scores_by_branch.items():
        branch = Branch.objects.get(pk=branch_id)
        at_risk_items = ", ".join(f"{s.product.name} ({s.risk_level})" for s in scores[:5])

        message, generated_by_ai = generate_insight(
            "STOCKOUT_WARNING",
            {"branch_name": branch.name, "at_risk_items": at_risk_items},
            force_template=not config.ai_insights_enabled,
        )
        insight = AIInsight.objects.create(
            branch=branch,
            insight_type=AIInsight.InsightType.STOCKOUT_WARNING,
            message=message,
            generated_by_ai=generated_by_ai,
        )
        insights.append(insight)

    return insights


# ---------------------------------------------------------------------
# Row 11.3: AI-Powered Dashboards -- read-side queries only. Everything
# below reads data the scheduled jobs above already computed and saved;
# none of it re-runs ARIMA/the classifier/an AI API call on page load
# (Phase 2 decoupling rule).
# ---------------------------------------------------------------------


def get_latest_risk_scores(branch=None):
    """InventoryRiskScore is an append-only log -- every scheduled run
    adds new rows rather than updating old ones (same pattern as
    Forecast). This returns only the MOST RECENT score per (branch,
    product) pair, not every score ever computed -- without this, a
    dashboard would mix fresh and stale risk levels together forever."""
    from apps.forecasting.models import InventoryRiskScore

    queryset = InventoryRiskScore.objects.select_related("branch", "product")
    if branch is not None:
        queryset = queryset.filter(branch=branch)

    latest_ids = (
        queryset.values("branch_id", "product_id")
        .annotate(latest_id=models.Max("id"))
        .values_list("latest_id", flat=True)
    )
    return InventoryRiskScore.objects.filter(pk__in=latest_ids).select_related("branch", "product")


def calculate_recommended_reorder_quantity(avg_daily_demand, quantity_on_hand, target_days=14):
    """A simple, explainable restocking suggestion: order enough to cover
    `target_days` of average demand, accounting for what's already on
    hand. Deliberately not a sophisticated reorder-point optimization --
    that's a much bigger topic than this capstone's scope, and an honest
    simple formula beats a falsely sophisticated-looking one."""
    if avg_daily_demand <= 0:
        return 0
    needed = (avg_daily_demand * target_days) - quantity_on_hand
    return max(0, round(needed))


def get_critical_alerts(branch=None):
    """HIGH-risk items with a recommended reorder quantity attached --
    the core "automated restocking recommendations" (Fig. 3-21)."""
    alerts = []
    for score in get_latest_risk_scores(branch).filter(risk_level="HIGH"):
        alerts.append(
            {
                "score": score,
                "recommended_reorder_quantity": calculate_recommended_reorder_quantity(
                    score.avg_daily_demand, score.quantity_on_hand
                ),
            }
        )
    return alerts


def get_slow_moving_products(branch=None, limit=5):
    """Products with real stock on hand but very low sales velocity
    (Fig. 3-21). Items with zero avg_daily_demand AND zero stock are
    excluded -- there's nothing actionable about a product nobody
    stocks and nobody buys."""
    scores = (
        get_latest_risk_scores(branch)
        .filter(quantity_on_hand__gt=0)
        .order_by("avg_daily_demand")[: limit * 2]
    )
    return list(scores)[:limit]


def get_peak_hour(branch=None, days=30):
    """The hour of day (0-23) with the most completed transactions over
    the recent window -- a genuine, simple "peak hour" signal computed
    directly from real transaction timestamps, not a fabricated metric."""
    from datetime import timedelta

    since = timezone.now() - timedelta(days=days)
    transactions = SalesTransaction.objects.filter(
        status=SalesTransaction.Status.COMPLETED, completed_at__gte=since
    )
    if branch is not None:
        transactions = transactions.filter(branch=branch)

    hour_counts = {}
    for completed_at in transactions.values_list("completed_at", flat=True):
        local_hour = timezone.localtime(completed_at).hour
        hour_counts[local_hour] = hour_counts.get(local_hour, 0) + 1

    if not hour_counts:
        return None

    peak_hour = max(hour_counts, key=hour_counts.get)
    return {"hour": peak_hour, "transaction_count": hour_counts[peak_hour]}


def detect_unusual_patterns(branch=None, weeks_of_history=4):
    """Flags a product whose sales YESTERDAY deviated sharply (more than
    50% above or below) from that same weekday's average over the last
    few weeks. A simple, genuinely computable anomaly signal -- not a
    statistical model, just a real comparison against real history."""
    from datetime import timedelta

    yesterday = timezone.localdate() - timedelta(days=1)
    weekday = yesterday.weekday()

    comparison_start = yesterday - timedelta(weeks=weeks_of_history)

    def _units_sold_on(target_date, product_id=None):
        items = SalesItem.objects.filter(
            transaction__status=SalesTransaction.Status.COMPLETED,
            transaction__completed_at__date=target_date,
        )
        if branch is not None:
            items = items.filter(transaction__branch=branch)
        if product_id is not None:
            items = items.filter(product_id=product_id)
        return items.aggregate(total=models.Sum("quantity"))["total"] or 0

    product_ids = (
        SalesItem.objects.filter(transaction__status=SalesTransaction.Status.COMPLETED)
        .values_list("product_id", flat=True)
        .distinct()
    )

    anomalies = []
    for product_id in product_ids:
        if product_id is None:
            continue
        yesterday_units = _units_sold_on(yesterday, product_id)

        same_weekday_totals = []
        check_date = yesterday - timedelta(weeks=1)
        while check_date >= comparison_start:
            if check_date.weekday() == weekday:
                same_weekday_totals.append(_units_sold_on(check_date, product_id))
            check_date -= timedelta(days=1)

        if not same_weekday_totals or sum(same_weekday_totals) == 0:
            continue

        baseline = sum(same_weekday_totals) / len(same_weekday_totals)
        if baseline == 0:
            continue

        deviation_pct = ((yesterday_units - baseline) / baseline) * 100
        if abs(deviation_pct) > 50:
            product = Product.objects.get(pk=product_id)
            anomalies.append(
                {
                    "product": product,
                    "yesterday_units": yesterday_units,
                    "baseline_average": round(baseline, 1),
                    "deviation_pct": round(deviation_pct, 1),
                }
            )

    return anomalies


def get_latest_forecast_batch(branch=None):
    """Forecast is also an append-only log -- returns just the most
    recent generation run's rows, identified by generated_at falling
    within a short window of the single most recent timestamp (one
    scheduled job run creates all its rows within seconds of each
    other, at this data scale)."""
    from datetime import timedelta

    from apps.forecasting.models import Forecast

    queryset = Forecast.objects.select_related("branch", "product")
    if branch is not None:
        queryset = queryset.filter(branch=branch)

    latest = queryset.order_by("-generated_at").first()
    if latest is None:
        return Forecast.objects.none()

    window_start = latest.generated_at - timedelta(minutes=5)
    return queryset.filter(generated_at__gte=window_start)


def get_forecasting_dashboard_summary(branch=None):
    """Model accuracy, data points, predictions made, and an approximate
    confidence level (Fig. 3-25) -- all derived honestly from the latest
    forecast run, not invented. `avg_confidence_pct` is explicitly a
    rough heuristic (1 - MAE/demand), not a real statistical confidence
    interval -- documented as such rather than presented as more
    rigorous than it is."""
    batch = get_latest_forecast_batch(branch)
    rows = list(batch)

    if not rows:
        return {
            "predictions_made": 0,
            "products_forecasted": 0,
            "avg_mae": None,
            "avg_confidence_pct": None,
            "arima_count": 0,
            "naive_count": 0,
        }

    maes = [r.mae for r in rows if r.mae is not None]
    avg_mae = round(sum(maes) / len(maes), 2) if maes else None

    confidences = []
    for r in rows:
        if r.mae is not None and r.predicted_quantity > 0:
            confidence = max(0.0, min(100.0, 100 * (1 - r.mae / max(r.predicted_quantity, 1))))
            confidences.append(confidence)
    avg_confidence_pct = round(sum(confidences) / len(confidences), 1) if confidences else None

    return {
        "predictions_made": len(rows),
        "products_forecasted": len({r.product_id for r in rows}),
        "avg_mae": avg_mae,
        "avg_confidence_pct": avg_confidence_pct,
        "arima_count": sum(1 for r in rows if r.model_used.startswith("ARIMA")),
        "naive_count": sum(1 for r in rows if r.model_used == "NAIVE_AVERAGE"),
    }


def get_weekly_demand_pattern(branch=None):
    """Total predicted units per forecast date, across all products --
    feeds the 7-day forecast chart on the Forecasting Dashboard
    (Fig. 3-25)."""
    batch = get_latest_forecast_batch(branch)
    by_date = {}
    for row in batch:
        by_date.setdefault(row.forecast_date, 0.0)
        by_date[row.forecast_date] += row.predicted_quantity

    return [
        {"date": date.strftime("%b %d"), "predicted_total": round(total, 1)}
        for date, total in sorted(by_date.items())
    ]


def get_resource_management_dashboard_data(days=30):
    """Combines Week 9's resource consumption summary with a category
    breakdown (for a pie chart) and restocking recommendations scoped to
    raw materials specifically (Fig. 3-26).

    Deliberately NOT branch-filterable: raw materials only ever have
    Inventory rows at the commissary (Week 8's design), never at a
    customer-facing branch. A branch filter here would silently show
    zero results for every real branch, masking genuine restocking
    alerts -- confirmed and fixed after finding this during review.
    """
    from apps.analytics.services import get_resource_consumption_summary

    consumption = get_resource_consumption_summary(days=days)

    material_alerts = []
    for score in get_latest_risk_scores(branch=None).filter(
        risk_level__in=["HIGH", "MEDIUM"], product__product_type=Product.ProductType.MATERIAL
    ):
        material_alerts.append(
            {
                "score": score,
                "recommended_reorder_quantity": calculate_recommended_reorder_quantity(
                    score.avg_daily_demand, score.quantity_on_hand
                ),
            }
        )

    return {"consumption": consumption, "material_alerts": material_alerts}
