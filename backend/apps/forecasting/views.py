"""
Views for the AI-Powered Dashboards (Row 11.3): Decision Support
(Fig. 3-21), Forecasting (Fig. 3-25), and Resource Management
(Fig. 3-26). All three are read-only over data the scheduled jobs
(Weeks 10-11) already computed -- none of these views run Holt-Winters, the
risk classifier, or an AI API call on page load.

Owner/Admin only, matching Table 3-2's ownership of the AI/DSS FRs.
"""

import csv
import json
from datetime import date

from django.http import HttpResponse
from django.shortcuts import render

from apps.accounts.models import Branch, Role
from apps.accounts.permissions import role_required
from apps.forecasting import services
from apps.forecasting.models import AIInsight
from apps.inventory import services as inventory_services


@role_required(Role.OWNER_ADMIN)
def decision_support(request):
    """AI-Powered Decision Support Interface (Fig. 3-21)."""
    branch_id = request.GET.get("branch")
    branch = Branch.objects.filter(pk=branch_id).first() if branch_id else None

    return render(
        request,
        "forecasting/decision_support.html",
        {
            # UI/UX review (2026-11-05): this page now has its own sidebar
            # link (previously it had none at all -- unreachable except by
            # typing the URL directly), so it highlights its own item
            # rather than borrowing Demand Forecasting's.
            "active_nav": "decision_support",
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
            "active_nav": "forecast",
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
    """AI-Powered Resource Management Dashboard (Fig. 3-26).

    Material consumption/restocking data (the top KPI row, the pie chart,
    and the restocking table) is deliberately NOT filtered by the branch
    dropdown -- it's a materials-wide view across every branch that
    stocks materials (several real branches do; see
    services.get_resource_management_dashboard_data's docstring for the
    corrected history here). The branch dropdown in the shared Admin
    Shell topbar instead scopes a second, added section: real per-branch
    finished-goods stock health (reusing
    inventory.get_resource_status_summary from the Dashboard Home batch,
    rather than re-deriving the same thing twice)."""
    data = services.get_resource_management_dashboard_data()
    consumption = data["consumption"]

    branch_id = request.GET.get("branch")
    selected_branch = Branch.objects.filter(pk=branch_id).first() if branch_id else None

    # Pie-chart legend readability (pre-defense UI review): the real
    # material catalog has 37 distinct materials with usage in a given
    # 30-day window, which makes for an unreadable legend and a chart
    # that gets squeezed or cut off. Show the top N by usage and fold
    # the rest into a single "Other materials" slice -- the chart stays
    # legible without hiding any usage (the folded total is still real,
    # just not broken out by name).
    TOP_N_MATERIALS = 8
    by_material = consumption["by_material"]
    top_materials = by_material[:TOP_N_MATERIALS]
    other_materials = by_material[TOP_N_MATERIALS:]

    category_labels = [m["material__name"] for m in top_materials]
    # total_used comes from Sum("quantity_used") over IngredientUsage's
    # DecimalField -- the aggregate returns Decimal, which json.dumps()
    # can't serialize on its own (TypeError: Object of type Decimal is
    # not JSON serializable). Unit tests never caught this because their
    # fixtures left consumption empty; it only surfaces once there's real
    # material-usage data behind the chart, e.g. after the real
    # Production_Records/Ingredient_Usage import -- cast to float for the
    # Chart.js payload (display-only; every other use of this value keeps
    # the original Decimal precision).
    category_values = [float(m["total_used"]) for m in top_materials]

    if other_materials:
        category_labels.append(f"Other materials ({len(other_materials)})")
        category_values.append(float(sum(m["total_used"] for m in other_materials)))

    # Finished-Goods Stock Health list overcrowding (pre-defense UI review):
    # with real client data across every branch, this list runs to 175+
    # rows and made the page roughly 14,000px tall. Critical items are
    # already sorted first, then Low, then Adequate (see
    # get_resource_status_summary), so showing only the first N still
    # surfaces every Critical/Low item -- nothing that needs attention is
    # hidden, only the long, unremarkable Adequate tail.
    STOCK_HEALTH_DISPLAY_LIMIT = 20
    resource_status = inventory_services.get_resource_status_summary(branch=selected_branch)
    resource_status_items = resource_status["items"]
    resource_status_hidden_count = max(0, len(resource_status_items) - STOCK_HEALTH_DISPLAY_LIMIT)
    resource_status["items"] = resource_status_items[:STOCK_HEALTH_DISPLAY_LIMIT]

    return render(
        request,
        "forecasting/resource_management_dashboard.html",
        {
            "active_nav": "resource",
            "branches": Branch.objects.filter(is_active=True, is_commissary=False),
            "selected_branch": selected_branch,
            "consumption": consumption,
            "material_alerts": data["material_alerts"],
            "resource_status": resource_status,
            "resource_status_hidden_count": resource_status_hidden_count,
            "category_labels_json": json.dumps(category_labels),
            "category_values_json": json.dumps(category_values),
        },
    )


@role_required(Role.OWNER_ADMIN)
def export_resource_report(request):
    """CSV export of the Resource Management dashboard (Fig. 3-26) --
    materials consumption/restocking (commissary-wide) plus finished-goods
    stock health for whichever branch is selected in the topbar, mirroring
    exactly what's on screen rather than re-deriving a separate report."""
    data = services.get_resource_management_dashboard_data()
    consumption = data["consumption"]

    branch_id = request.GET.get("branch")
    selected_branch = Branch.objects.filter(pk=branch_id).first() if branch_id else None
    resource_status = inventory_services.get_resource_status_summary(branch=selected_branch)

    filename = f"resource_management_report_{date.today().isoformat()}.csv"
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)

    writer.writerow(["SanServeAll -- Resource Management Report"])
    writer.writerow(["Generated", date.today().isoformat()])
    branch_label = selected_branch.name if selected_branch else "All Branches"
    writer.writerow(["Branch (finished-goods scope)", branch_label])
    writer.writerow([])

    writer.writerow(["Materials Summary (commissary-wide, last 30 days)"])
    writer.writerow(["Total Cost", "Units Produced", "Cost per Unit"])
    writer.writerow(
        [
            consumption["total_cost"],
            consumption["total_produced"],
            consumption["cost_per_unit_produced"],
        ]
    )
    writer.writerow([])

    # Task #163: materials used is reported per unit of measurement, not
    # as one combined figure -- kg, L, and pc quantities are not the same
    # kind of thing and summing them together is meaningless.
    writer.writerow(["Total Units Used by Unit of Measurement"])
    writer.writerow(["Unit", "Total Used"])
    for row in consumption["units_used_by_unit"]:
        writer.writerow([row["unit_display"], row["total"]])
    writer.writerow([])

    writer.writerow(["Material Consumption by Category"])
    writer.writerow(["Material", "Unit", "Total Used"])
    for row in consumption["by_material"]:
        writer.writerow([row["material__name"], row["unit_display"], row["total_used"]])
    writer.writerow([])

    # Labeling accuracy fix (2026-11-05): matches the dashboard template --
    # this is a plain threshold formula (see
    # services.calculate_recommended_reorder_quantity's own docstring),
    # not AI/ML.
    writer.writerow(["Restocking Recommendations (Materials)"])
    writer.writerow(["Material", "Stock on Hand", "Risk Level", "Recommended Reorder Qty"])
    for alert in data["material_alerts"]:
        writer.writerow(
            [
                alert["score"].product.name,
                alert["score"].quantity_on_hand,
                alert["score"].risk_level,
                alert["recommended_reorder_quantity"],
            ]
        )
    writer.writerow([])

    writer.writerow(["Finished-Goods Stock Health"])
    writer.writerow(["Overall Sufficiency %", "Critical", "Low"])
    writer.writerow(
        [
            resource_status["sufficiency_pct"],
            resource_status["critical_count"],
            resource_status["low_count"],
        ]
    )
    writer.writerow([])
    writer.writerow(["Product", "Branch", "Quantity on Hand", "Status"])
    for item in resource_status["items"]:
        if item.is_out_of_stock:
            status = "Critical"
        elif item.is_low_stock:
            status = "Low"
        else:
            status = "Adequate"
        writer.writerow([item.product.name, item.branch.name, item.quantity_on_hand, status])

    return response
