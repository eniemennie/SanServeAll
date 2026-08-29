"""
Views for the AI-Powered Dashboards (Row 11.3): Decision Support
(Fig. 3-21), Forecasting (Fig. 3-25), and Resource Management
(Fig. 3-26). All three are read-only over data the scheduled jobs
(Weeks 10-11) already computed -- none of these views run ARIMA, the
risk classifier, or an AI API call on page load.

Owner/Admin only, matching Table 3-2's ownership of the AI/DSS FRs.
"""

import json

from django.shortcuts import render

from apps.accounts.models import Branch, Role
from apps.accounts.permissions import role_required
from apps.forecasting import services
from apps.forecasting.models import AIInsight


@role_required(Role.OWNER_ADMIN)
def decision_support(request):
    """AI-Powered Decision Support Interface (Fig. 3-21)."""
    branch_id = request.GET.get("branch")
    branch = Branch.objects.filter(pk=branch_id).first() if branch_id else None

    return render(
        request,
        "forecasting/decision_support.html",
        {
            "critical_alerts": services.get_critical_alerts(branch),
            "slow_moving_products": services.get_slow_moving_products(branch),
            "peak_hour": services.get_peak_hour(branch),
            "anomalies": services.detect_unusual_patterns(branch),
            "insights": (
                AIInsight.objects.filter(branch=branch) if branch else AIInsight.objects.all()[:10]
            ),
            "branches": Branch.objects.filter(is_active=True, is_commissary=False),
            "selected_branch": branch,
        },
    )


@role_required(Role.OWNER_ADMIN)
def forecasting_dashboard(request):
    """AI-Powered Forecasting Dashboard (Fig. 3-25)."""
    branch_id = request.GET.get("branch")
    branch = Branch.objects.filter(pk=branch_id).first() if branch_id else None

    summary = services.get_forecasting_dashboard_summary(branch)
    weekly_pattern = services.get_weekly_demand_pattern(branch)
    latest_batch = services.get_latest_forecast_batch(branch)

    return render(
        request,
        "forecasting/forecasting_dashboard.html",
        {
            "summary": summary,
            "pattern_labels_json": json.dumps([p["date"] for p in weekly_pattern]),
            "pattern_values_json": json.dumps([p["predicted_total"] for p in weekly_pattern]),
            "forecasts": latest_batch.order_by("product__name", "forecast_date"),
            "branches": Branch.objects.filter(is_active=True, is_commissary=False),
            "selected_branch": branch,
        },
    )


@role_required(Role.OWNER_ADMIN)
def resource_management_dashboard(request):
    """AI-Powered Resource Management Dashboard (Fig. 3-26). No branch
    filter -- raw materials only exist at the commissary (Week 8), so a
    per-branch view would always show empty results for every real
    branch. See services.get_resource_management_dashboard_data."""
    data = services.get_resource_management_dashboard_data()
    consumption = data["consumption"]

    category_labels = [m["material__name"] for m in consumption["by_material"]]
    category_values = [m["total_used"] for m in consumption["by_material"]]

    return render(
        request,
        "forecasting/resource_management_dashboard.html",
        {
            "consumption": consumption,
            "material_alerts": data["material_alerts"],
            "category_labels_json": json.dumps(category_labels),
            "category_values_json": json.dumps(category_values),
        },
    )
