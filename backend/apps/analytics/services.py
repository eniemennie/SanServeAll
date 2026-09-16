"""
Analytics Module business logic (Week 9): read-only aggregation queries
over data that already exists in pos, inventory, and production -- no new
models, this module only reports on what other modules already record.
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import F, Sum
from django.utils import timezone

from apps.inventory.services import get_branch_inventory
from apps.pos.models import SalesItem, SalesTransaction
from apps.production.models import IngredientUsage, ProductionRecord
from apps.system_config.models import BusinessSettings


def get_sales_summary(branch=None, days=30):
    """Overview numbers for the Sales/Analytics Dashboard (Fig. 3-19,
    3-28): total revenue, transaction count, units sold, average daily
    revenue over the given window.

    Revenue is summed from SalesItem (quantity x unit_price), NOT from
    SalesTransaction.amount_tendered -- the latter is what the customer
    physically handed over (e.g. Php500 tendered for a Php375 sale) and
    would overstate revenue by however much change was given back.
    """
    since = timezone.now() - timedelta(days=days)
    transactions = SalesTransaction.objects.filter(
        status=SalesTransaction.Status.COMPLETED, completed_at__gte=since
    )
    if branch is not None:
        transactions = transactions.filter(branch=branch)

    items = SalesItem.objects.filter(transaction__in=transactions)
    total_revenue = items.aggregate(total=Sum(F("quantity") * F("unit_price")))["total"] or Decimal(
        "0"
    )
    total_transactions = transactions.count()
    total_units_sold = items.aggregate(total=Sum("quantity"))["total"] or 0
    average_daily_revenue = (total_revenue / days) if days else Decimal("0")

    return {
        "total_revenue": total_revenue,
        "total_transactions": total_transactions,
        "total_units_sold": total_units_sold,
        "average_daily_revenue": average_daily_revenue,
        "days": days,
    }


def get_weekly_sales_trend(branch=None, weeks=8):
    """Revenue per week for the last N weeks -- feeds the Chart.js line
    chart on the Sales Analytics Dashboard (Fig. 3-28). Returned oldest
    week first, so it plots left-to-right chronologically. Same
    SalesItem-based revenue calculation as get_sales_summary, for the
    same reason (amount_tendered is not the sale total)."""
    now = timezone.now()
    trend = []
    for week_offset in range(weeks - 1, -1, -1):
        week_end = now - timedelta(weeks=week_offset)
        week_start = week_end - timedelta(weeks=1)

        transactions = SalesTransaction.objects.filter(
            status=SalesTransaction.Status.COMPLETED,
            completed_at__gte=week_start,
            completed_at__lt=week_end,
        )
        if branch is not None:
            transactions = transactions.filter(branch=branch)

        revenue = SalesItem.objects.filter(transaction__in=transactions).aggregate(
            total=Sum(F("quantity") * F("unit_price"))
        )["total"] or Decimal("0")
        trend.append({"week_label": week_start.strftime("%b %d"), "revenue": float(revenue)})

    return trend


def get_weekly_sales_trend_by_branch(weeks=8):
    """Same window/shape as get_weekly_sales_trend, but broken out per
    branch -- feeds the Dashboard Home Sales Analytics chart (Fig. 3-19),
    which plots Batangas/Lipa/Alangilan as three separate lines rather
    than one aggregate line. Only meaningful for "All Branches"; when a
    specific branch is selected, the Dashboard reuses the single-line
    get_weekly_sales_trend instead."""
    from apps.accounts.models import Branch

    now = timezone.now()
    branches = list(Branch.objects.filter(is_active=True, is_commissary=False))
    week_starts = []
    per_branch_revenue = {b.pk: [] for b in branches}

    for week_offset in range(weeks - 1, -1, -1):
        week_end = now - timedelta(weeks=week_offset)
        week_start = week_end - timedelta(weeks=1)
        week_starts.append(week_start.strftime("%b %d"))

        for b in branches:
            revenue = SalesItem.objects.filter(
                transaction__branch=b,
                transaction__status=SalesTransaction.Status.COMPLETED,
                transaction__completed_at__gte=week_start,
                transaction__completed_at__lt=week_end,
            ).aggregate(total=Sum(F("quantity") * F("unit_price")))["total"] or Decimal("0")
            per_branch_revenue[b.pk].append(float(revenue))

    return {
        "week_labels": week_starts,
        "series": [{"branch_name": b.name, "revenue": per_branch_revenue[b.pk]} for b in branches],
    }


def get_branch_performance(days=30):
    """Per-branch Revenue / Units Sold / Orders for the Dashboard Home
    Branch Performance cards (Fig. 3-19's three branch cards).

    NOTE: the reference design's third figure is labeled "Production",
    but ProductionRecord has no branch FK -- production genuinely is
    commissary-wide, not per-branch (the manuscript itself describes one
    centralized commissary distributing to all branches; a single
    production batch can't honestly be attributed to one branch). Rather
    than invent a number, Units Sold is used as the real,
    per-branch-derivable substitute for that third figure.
    """
    from apps.accounts.models import Branch

    since = timezone.now() - timedelta(days=days)
    results = []
    for branch in Branch.objects.filter(is_active=True, is_commissary=False):
        transactions = SalesTransaction.objects.filter(
            branch=branch, status=SalesTransaction.Status.COMPLETED, completed_at__gte=since
        )
        agg = SalesItem.objects.filter(transaction__in=transactions).aggregate(
            revenue=Sum(F("quantity") * F("unit_price")), units=Sum("quantity")
        )
        results.append(
            {
                "branch": branch,
                "revenue": agg["revenue"] or Decimal("0"),
                "units_sold": agg["units"] or 0,
                "orders": transactions.count(),
            }
        )
    return results


def get_sales_summary_with_toggles(
    branch=None,
    days=30,
    include_additional_sales=True,
    exclude_discounts=False,
    exclude_vat=False,
):
    """Dashboard Home's Total Revenue KPI card (Fig. 3-19), with the
    three real toggles the reference design shows underneath it:

    - include_additional_sales: SalesItem rows with product=None are
      off-menu/custom items (Fig. 3-13's "Add Custom Product"). Toggling
      this off excludes them, counting catalog products only.
    - exclude_discounts: when on, revenue is the pre-transaction-discount
      subtotal (item.subtotal sum) rather than the post-discount
      grand_total-equivalent figure. Per-item discounts (Batch 2's
      customization modal) are already baked into each item's unit_price
      and can't be honestly un-applied without the original list price,
      so this only reverses the transaction-wide discount
      (SalesTransaction.transaction_discount_*), matching the codebase's
      own distinction between the two discount types.
    - exclude_vat: strips the VAT portion out using the real configured
      tax_rate_percent (BusinessSettings, the same singleton the POS
      checkout screen uses), via the same VAT-inclusive-price formula as
      SalesTransaction.vat_breakdown.

    This is a separate function from get_sales_summary rather than added
    toggles on it, since get_sales_summary is already used (unmodified)
    by the existing analytics:sales_dashboard screen and its tests --
    changing its behavior would be a real regression risk for no benefit
    to that screen, which doesn't have these toggles.
    """
    since = timezone.now() - timedelta(days=days)
    transactions = SalesTransaction.objects.filter(
        status=SalesTransaction.Status.COMPLETED, completed_at__gte=since
    ).prefetch_related("items")
    if branch is not None:
        transactions = transactions.filter(branch=branch)

    tax_rate = BusinessSettings.load().tax_rate_percent
    vat_divisor = Decimal("1") + (tax_rate / Decimal("100"))

    total_revenue = Decimal("0.00")
    total_units = 0
    transaction_count = 0

    for txn in transactions:
        items = list(txn.items.all())
        if not include_additional_sales:
            items = [i for i in items if i.product_id is not None]
        if not items and not include_additional_sales:
            # Every item in this transaction was a custom/off-menu sale
            # and we're excluding those -- nothing left to count from it.
            continue

        transaction_count += 1
        subtotal = sum((i.subtotal for i in items), Decimal("0.00"))
        total_units += sum(i.quantity for i in items)

        if exclude_discounts:
            amount = subtotal
        else:
            if txn.transaction_discount_amount and txn.transaction_discount_category:
                if txn.transaction_discount_type == "PERCENT":
                    discount_value = subtotal * (txn.transaction_discount_amount / Decimal("100"))
                else:
                    discount_value = txn.transaction_discount_amount
                discount_value = min(discount_value, subtotal)
            else:
                discount_value = Decimal("0.00")
            amount = subtotal - discount_value

        if exclude_vat:
            amount = amount / vat_divisor

        total_revenue += amount

    average_daily_revenue = (total_revenue / days) if days else Decimal("0.00")

    return {
        "total_revenue": total_revenue.quantize(Decimal("0.01")),
        "total_transactions": transaction_count,
        "total_units_sold": total_units,
        "average_daily_revenue": average_daily_revenue.quantize(Decimal("0.01")),
        "days": days,
    }


def get_top_products(branch=None, days=30, limit=5):
    """Best-selling products by units sold over the window -- the "top
    products" panel on the Analytics Dashboard (Fig. 3-19)."""
    since = timezone.now() - timedelta(days=days)
    items = SalesItem.objects.filter(
        transaction__status=SalesTransaction.Status.COMPLETED,
        transaction__completed_at__gte=since,
    )
    if branch is not None:
        items = items.filter(transaction__branch=branch)

    return (
        items.values("product__id", "product__name")
        .annotate(
            units_sold=Sum("quantity"),
            revenue=Sum(F("quantity") * F("unit_price")),
        )
        .filter(product__isnull=False)
        .order_by("-units_sold")[:limit]
    )


def get_product_performance(days=30):
    """Per-product performance and growth rate (Fig. 3-29): units sold in
    the current window vs. the equally-sized window immediately before
    it. Products with no sales in either window are omitted -- a 0-to-0
    "growth rate" is meaningless noise, not a real signal."""
    now = timezone.now()
    current_start = now - timedelta(days=days)
    previous_start = current_start - timedelta(days=days)

    def _units_sold_in_range(start, end):
        return (
            SalesItem.objects.filter(
                transaction__status=SalesTransaction.Status.COMPLETED,
                transaction__completed_at__gte=start,
                transaction__completed_at__lt=end,
            )
            .values("product__id", "product__name")
            .annotate(units_sold=Sum("quantity"))
        )

    current = {row["product__id"]: row for row in _units_sold_in_range(current_start, now)}
    previous = {
        row["product__id"]: row for row in _units_sold_in_range(previous_start, current_start)
    }

    results = []
    for product_id in set(current) | set(previous):
        current_units = current.get(product_id, {}).get("units_sold", 0)
        previous_units = previous.get(product_id, {}).get("units_sold", 0)
        name = (current.get(product_id) or previous.get(product_id))["product__name"]

        if previous_units > 0:
            growth_rate = ((current_units - previous_units) / previous_units) * 100
        elif current_units > 0:
            growth_rate = None  # new product this period -- "infinite" growth isn't meaningful
        else:
            continue  # no sales in either window at all

        results.append(
            {
                "product_name": name,
                "current_units": current_units,
                "previous_units": previous_units,
                "growth_rate": growth_rate,
            }
        )

    return sorted(results, key=lambda r: r["current_units"], reverse=True)


def get_resource_consumption_summary(days=30):
    """Resource Consumption Analytics (Fig. 3-30): total materials used,
    total material cost, and cost broken down per finished good produced
    -- all derived from Production's own IngredientUsage records, not a
    separate tracking system."""
    since = timezone.now() - timedelta(days=days)
    usages = IngredientUsage.objects.filter(production_record__created_at__gte=since)

    total_units_used = usages.aggregate(total=Sum("quantity_used"))["total"] or 0
    total_cost = usages.aggregate(total=Sum(F("quantity_used") * F("material__price")))[
        "total"
    ] or Decimal("0")

    total_produced = (
        ProductionRecord.objects.filter(created_at__gte=since).aggregate(
            total=Sum("quantity_produced")
        )["total"]
        or 0
    )
    cost_per_unit_produced = (total_cost / total_produced) if total_produced else Decimal("0")

    by_material = (
        usages.values("material__name")
        .annotate(
            total_used=Sum("quantity_used"),
            total_material_cost=Sum(F("quantity_used") * F("material__price")),
        )
        .order_by("-total_used")
    )

    return {
        "total_units_used": total_units_used,
        "total_cost": total_cost,
        "total_produced": total_produced,
        "cost_per_unit_produced": cost_per_unit_produced,
        "by_material": list(by_material),
        "days": days,
    }


def get_operational_performance_summary(days=30):
    """Operational Performance (Fig. 3-31): batch completion rate and
    quality pass rate over the window. Average production TIME is
    deliberately not reported here -- ProductionRecord doesn't track a
    start/end duration distinct from created_at, so fabricating a number
    for it would be reporting something we don't actually measure."""
    since = timezone.now() - timedelta(days=days)
    records = ProductionRecord.objects.filter(created_at__gte=since)

    total_batches = records.count()
    completed_batches = records.filter(status=ProductionRecord.Status.COMPLETED).count()
    passed_batches = records.filter(quality=ProductionRecord.Quality.PASS).count()

    completion_rate = (completed_batches / total_batches * 100) if total_batches else None
    quality_pass_rate = (passed_batches / total_batches * 100) if total_batches else None

    return {
        "total_batches": total_batches,
        "completed_batches": completed_batches,
        "completion_rate": completion_rate,
        "passed_batches": passed_batches,
        "quality_pass_rate": quality_pass_rate,
        "days": days,
    }


def get_low_stock_alert_count(branch):
    """Reuses Inventory's own low-stock detection (Week 6) rather than
    re-implementing the same rule here -- the Analytics Dashboard's
    low-stock panel (Fig. 3-19) is a summary VIEW of Inventory's data,
    not a second source of truth for what counts as low stock."""
    items = get_branch_inventory(branch, low_stock_only=True)
    return len(items)
