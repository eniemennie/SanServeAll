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
from datetime import datetime

from django.core.paginator import Paginator
from django.shortcuts import render

from apps.accounts.models import Branch, Role
from apps.accounts.permissions import role_required
from apps.analytics import services


@role_required(Role.OWNER_ADMIN)
def reports_hub(request):
    """Reports hub. The reference design shows four tabs (Sales & Trend
    Report / Product Performance / Resource Consumption / Operational
    Performance), but only three real URLs exist -- Resource Consumption
    and Operational Performance are already one combined view
    (resource_consumption, Fig. 3-30/3-31 together). This is a NEW view
    that assembles all four real datasets in a single request, with the
    tabs switched client-side (see extra_js below) rather than inventing
    a fourth URL that doesn't correspond to anything.

    Named "Reports," not "Reports & Export" -- no CSV/PDF download
    exists anywhere in this codebase (adviser compliance review flagged
    this; rather than build a real export feature under today's time
    constraints, the label was corrected to match what the screen
    actually does). This view covers viewing/reporting only.

    Task #146 (client review, 2026-11-04): "Users should be able to
    select a specific date range... rather than being limited to preset
    reporting periods." A real date_from/date_to range (same
    YYYY-MM-DD parsing/validation pattern already proven on
    analytics:sales_history) now takes priority over the preset `days`
    dropdown whenever both dates are given and valid. All of Sales
    Summary, Product Performance, Resource Consumption, and Operational
    Performance respect it. The weekly trend CHART is a deliberate
    exception -- it's a fixed "last 8 weeks" line chart by design, and
    bucketing an arbitrary short range (the client's own example,
    "November 6-7") into meaningful weekly points isn't a sensible
    reshape of that visual; the template labels it accordingly rather
    than silently implying it also follows the custom range.
    """
    days = int(request.GET.get("days", 30))

    branch_id = request.GET.get("branch")
    selected_branch = Branch.objects.filter(pk=branch_id).first() if branch_id else None

    date_from_raw = request.GET.get("date_from") or ""
    date_to_raw = request.GET.get("date_to") or ""
    date_error = None
    date_from = date_to = None
    try:
        if date_from_raw:
            date_from = datetime.strptime(date_from_raw, "%Y-%m-%d").date()
        if date_to_raw:
            date_to = datetime.strptime(date_to_raw, "%Y-%m-%d").date()
    except ValueError:
        date_error = "Enter dates as YYYY-MM-DD."

    if date_from and date_to and date_from > date_to:
        date_error = "'From' date must be on or before 'To' date."

    # An incomplete or invalid custom range falls back to the preset
    # `days` window rather than silently showing nothing -- only a
    # genuine error (bad format, or From after To) blocks the query.
    effective_from = date_from if not date_error else None
    effective_to = date_to if not date_error else None

    sales_summary = services.get_sales_summary(
        branch=selected_branch, days=days, date_from=effective_from, date_to=effective_to
    )
    trend = services.get_weekly_sales_trend(branch=selected_branch)
    product_performance_rows = services.get_product_performance(
        branch=selected_branch, days=days, date_from=effective_from, date_to=effective_to
    )
    consumption = services.get_resource_consumption_summary(
        days=days, date_from=effective_from, date_to=effective_to
    )
    operational = services.get_operational_performance_summary(
        days=days, date_from=effective_from, date_to=effective_to
    )

    return render(
        request,
        "analytics/reports_hub.html",
        {
            "active_nav": "reports",
            "branches": Branch.objects.filter(is_active=True, is_commissary=False),
            "selected_branch": selected_branch,
            "days": days,
            "date_from": date_from_raw,
            "date_to": date_to_raw,
            "date_error": date_error,
            "using_custom_range": bool(effective_from and effective_to),
            "sales_summary": sales_summary,
            "product_performance": product_performance_rows,
            "consumption": consumption,
            "operational": operational,
            "trend_labels_json": json.dumps([t["week_label"] for t in trend]),
            "trend_revenue_json": json.dumps([float(t["revenue"]) for t in trend]),
            "product_names_json": json.dumps([r["product_name"] for r in product_performance_rows]),
            "product_units_json": json.dumps(
                [r["current_units"] for r in product_performance_rows]
            ),
        },
    )


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
def sales_history(request):
    """Sales History (backend gap assessment: "add a filterable
    sales-history view (branch/cashier/date)"). A transaction-level list
    for looking up a specific sale, distinct from every other view in
    this module, which only ever shows aggregated totals.
    """
    branch_id = request.GET.get("branch")
    selected_branch = Branch.objects.filter(pk=branch_id).first() if branch_id else None

    cashier_id = request.GET.get("cashier") or None

    date_from_raw = request.GET.get("date_from") or ""
    date_to_raw = request.GET.get("date_to") or ""
    date_error = None
    date_from = date_to = None
    try:
        if date_from_raw:
            date_from = datetime.strptime(date_from_raw, "%Y-%m-%d").date()
        if date_to_raw:
            date_to = datetime.strptime(date_to_raw, "%Y-%m-%d").date()
    except ValueError:
        date_error = "Enter dates as YYYY-MM-DD."

    if date_from and date_to and date_from > date_to:
        date_error = "'From' date must be on or before 'To' date."

    transactions = []
    page_obj = None
    if not date_error:
        results = services.get_sales_history(
            branch=selected_branch,
            cashier_id=cashier_id,
            date_from=date_from,
            date_to=date_to,
        )
        page_obj = Paginator(results, 25).get_page(request.GET.get("page"))
        transactions = page_obj.object_list

    pagination_qs = request.GET.copy()
    pagination_qs.pop("page", None)

    return render(
        request,
        "analytics/sales_history.html",
        {
            "active_nav": "reports",
            "branches": Branch.objects.filter(is_active=True, is_commissary=False),
            "selected_branch": selected_branch,
            "cashiers": services.get_cashiers_with_sales_history(),
            "selected_cashier_id": int(cashier_id) if cashier_id else None,
            "date_from": date_from_raw,
            "date_to": date_to_raw,
            "date_error": date_error,
            "transactions": transactions,
            "page_obj": page_obj,
            "pagination_qs": pagination_qs.urlencode(),
        },
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
