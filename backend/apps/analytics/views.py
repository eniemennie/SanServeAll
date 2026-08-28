"""
Views for the Analytics Module (Week 9): Sales/Analytics Dashboard
(Fig. 3-19, 3-28), Product Performance Monitoring (Fig. 3-29), and
Resource Consumption / Operational Performance (Fig. 3-30, 3-31).

Restricted to Owner/Admin only -- these are cross-branch business
reports, matching Table 3-2's Business Owner ownership of the analytics
FRs. Unlike Inventory Monitoring (viewable by branch staff), there is no
staff-facing version of these screens in this batch.
"""

import json

from django.shortcuts import render

from apps.accounts.models import Branch, Role
from apps.accounts.permissions import role_required
from apps.analytics import services


@role_required(Role.OWNER_ADMIN)
def sales_dashboard(request):
    """Analytics Dashboard + Sales Analytics combined (Fig. 3-19, 3-28).
    Optional branch filter via ?branch=<id>, matching the Branch Filter
    Dropdown pattern (Fig. 3-22) used elsewhere."""
    branch_id = request.GET.get("branch")
    branch = Branch.objects.filter(pk=branch_id).first() if branch_id else None

    summary = services.get_sales_summary(branch=branch)
    trend = services.get_weekly_sales_trend(branch=branch)
    top_products = services.get_top_products(branch=branch)
    low_stock_count = services.get_low_stock_alert_count(branch) if branch else None

    return render(
        request,
        "analytics/sales_dashboard.html",
        {
            "summary": summary,
            "trend_labels_json": json.dumps([t["week_label"] for t in trend]),
            "trend_revenue_json": json.dumps([t["revenue"] for t in trend]),
            "top_products": top_products,
            "low_stock_count": low_stock_count,
            "branches": Branch.objects.filter(is_active=True, is_commissary=False),
            "selected_branch": branch,
        },
    )


@role_required(Role.OWNER_ADMIN)
def product_performance(request):
    """Product Performance Monitoring (Fig. 3-29): units sold this period
    vs. the prior period, with a growth rate, filterable by window length
    (defaults to the manuscript's own "last 30 days")."""
    days = int(request.GET.get("days", 30))
    performance = services.get_product_performance(days=days)

    return render(
        request,
        "analytics/product_performance.html",
        {"performance": performance, "days": days},
    )


@role_required(Role.OWNER_ADMIN)
def resource_consumption(request):
    """Resource Consumption Analytics (Fig. 3-30) combined with
    Operational Performance (Fig. 3-31) -- both are commissary/production
    metrics over the same time window, so shown on one screen."""
    days = int(request.GET.get("days", 30))
    consumption = services.get_resource_consumption_summary(days=days)
    operational = services.get_operational_performance_summary(days=days)

    return render(
        request,
        "analytics/resource_consumption.html",
        {"consumption": consumption, "operational": operational, "days": days},
    )
