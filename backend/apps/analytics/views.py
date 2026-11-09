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

import openpyxl
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render

from apps.accounts.models import Branch, Role
from apps.accounts.permissions import role_required
from apps.analytics import services


def _resolve_reports_hub_filters(request):
    """Shared branch/days/date-range parsing for reports_hub and its
    Excel export sibling (reports_hub_export) -- factored out so the
    export always resolves the exact same filters the on-screen view
    just used, rather than risking the two interpreting a query string
    differently were this logic duplicated."""
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

    return {
        "days": days,
        "selected_branch": selected_branch,
        "date_from_raw": date_from_raw,
        "date_to_raw": date_to_raw,
        "date_error": date_error,
        "effective_from": effective_from,
        "effective_to": effective_to,
    }


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

    Previously named "Reports," not "Reports & Export" -- no CSV/PDF
    download existed anywhere in this codebase (adviser compliance
    review flagged this; rather than build a real export feature under
    an earlier time constraint, the label was corrected to match what
    the screen actually did). Client review (2026-11-09, Appendix D/
    Fig. 4-4 screenshot-readiness assessment) surfaced that the
    manuscript still referenced a "downloadable Excel report" -- see
    reports_hub_export below, which is the real feature that label was
    waiting on. This view itself still covers viewing/reporting only;
    the export is a separate view/URL.

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
    f = _resolve_reports_hub_filters(request)
    selected_branch = f["selected_branch"]
    days = f["days"]
    effective_from = f["effective_from"]
    effective_to = f["effective_to"]

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
            "date_from": f["date_from_raw"],
            "date_to": f["date_to_raw"],
            "date_error": f["date_error"],
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
def reports_hub_export(request):
    """Excel download for the Reports Hub (client review, 2026-11-09:
    Appendix D/Fig. 4-4 screenshot-readiness assessment surfaced that
    the manuscript references a "downloadable Excel report" that never
    actually existed in this codebase -- reports_hub's own docstring
    already documented that an adviser compliance review caught the
    same gap earlier and the screen was relabeled "Reports," not
    "Reports & Export," rather than build this under a prior time
    constraint. This view is the real feature that label was waiting
    on.

    Resolves filters via the exact same helper reports_hub uses
    (_resolve_reports_hub_filters) and calls the exact same service
    functions with the exact same arguments, so the downloaded workbook
    always matches what's currently on screen for the same branch/
    days/date-range query string -- never a second, divergent
    calculation of the same numbers.

    One workbook, one sheet per Reports Hub tab (Sales & Trend, Product
    Performance, Resource Consumption, Operational Performance) -- the
    hub combines all four into one page, so the download mirrors that
    rather than forcing four separate files.
    """
    f = _resolve_reports_hub_filters(request)
    selected_branch = f["selected_branch"]
    days = f["days"]
    effective_from = f["effective_from"]
    effective_to = f["effective_to"]

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

    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)

    sheet = workbook.create_sheet("Sales & Trend")
    sheet.append(["Metric", "Value"])
    sheet.append(["Total Revenue", float(sales_summary["total_revenue"])])
    sheet.append(["Average Daily Revenue", float(sales_summary["average_daily_revenue"])])
    sheet.append(["Total Transactions", sales_summary["total_transactions"]])
    sheet.append(["Total Units Sold", sales_summary["total_units_sold"]])
    sheet.append([])
    sheet.append(["Week", "Revenue"])
    for row in trend:
        sheet.append([row["week_label"], row["revenue"]])

    sheet = workbook.create_sheet("Product Performance")
    sheet.append(["Product", "This Period", "Previous Period", "Growth Rate (%)"])
    for row in product_performance_rows:
        sheet.append(
            [
                row["product_name"],
                row["current_units"],
                row["previous_units"],
                round(row["growth_rate"], 1) if row["growth_rate"] is not None else "New",
            ]
        )

    sheet = workbook.create_sheet("Resource Consumption")
    sheet.append(["Material", "Unit", "Total Used", "Total Cost"])
    for material in consumption["by_material"]:
        sheet.append(
            [
                material["material__name"],
                material["unit_display"],
                float(material["total_used"]),
                float(material["total_material_cost"]),
            ]
        )
    sheet.append([])
    sheet.append(["Total Material Cost", float(consumption["total_cost"])])
    sheet.append(["Units Produced", consumption["total_produced"]])
    sheet.append(["Cost per Unit Produced", float(consumption["cost_per_unit_produced"])])

    sheet = workbook.create_sheet("Operational Performance")
    sheet.append(["Metric", "Value"])
    sheet.append(["Total Batches", operational["total_batches"]])
    sheet.append(["Completed Batches", operational["completed_batches"]])
    sheet.append(
        [
            "Completion Rate (%)",
            (
                round(operational["completion_rate"], 1)
                if operational["completion_rate"] is not None
                else "N/A"
            ),
        ]
    )
    sheet.append(["Passed Batches", operational["passed_batches"]])
    sheet.append(
        [
            "Quality Pass Rate (%)",
            (
                round(operational["quality_pass_rate"], 1)
                if operational["quality_pass_rate"] is not None
                else "N/A"
            ),
        ]
    )

    branch_label = selected_branch.name if selected_branch else "AllBranches"
    if effective_from and effective_to:
        range_label = f"{f['date_from_raw']}_to_{f['date_to_raw']}"
    else:
        range_label = f"Last{days}Days"
    filename = f"SanServeAll_Report_{branch_label}_{range_label}.xlsx".replace(" ", "")

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    workbook.save(response)
    return response


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
