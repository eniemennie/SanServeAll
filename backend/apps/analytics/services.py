"""
Analytics Module business logic (Week 9): read-only aggregation queries
over data that already exists in pos, inventory, and production -- no new
models, this module only reports on what other modules already record.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import F, Max, Sum
from django.utils import timezone

from apps.accounts.models import User
from apps.inventory.models import Product
from apps.inventory.services import get_branch_inventory
from apps.pos.models import SalesItem, SalesTransaction
from apps.production.models import IngredientUsage, ProductionRecord
from apps.system_config.models import BusinessSettings


def _exclude_synthetic_transactions(qs, cashier_lookup="cashier__username"):
    """Client review (2026-11-10): scripts/backfill_thin_forecast_pairs.py
    creates real, COMPLETED SalesTransaction rows (through the normal POS
    service layer) attributed to a single dedicated bot account purely so
    the forecasting pipeline has enough non-zero days to fit against --
    see that script's own module docstring. Nothing about `status=
    COMPLETED` distinguishes those from a genuine sale, so every report
    aggregate below must explicitly exclude them, or synthetic test data
    silently inflates real revenue/units/order totals.

    Deliberately does NOT also exclude apps.core's
    "historical_import_<branch>" accounts -- those ARE real, migrated
    historical sales and belong in every total exactly as a normal sale
    would (see User.is_historical_import_account's own docstring).

    `cashier_lookup` lets a caller starting from SalesItem reach through
    to its transaction's cashier (pass "transaction__cashier__username").
    """
    return qs.exclude(**{cashier_lookup: User.SYNTHETIC_BACKFILL_BOT_USERNAME})


def _trailing_window_anchor(branch=None):
    """The end-of-window anchor for every trailing-N-days/weeks query
    below. Defaults to "now" -- unchanged behavior for a branch with an
    ongoing live POS stream (Batangas/Lipa), since its latest
    transaction is always close to now anyway.

    For a branch that only gets new data via periodic batch upload, not
    live POS (Alangilan, via KaHero), anchoring to "now" means that once
    more than `days`/`weeks` have passed since the last upload, every
    trailing window lands entirely after that branch's last real
    transaction -- the dashboard then honestly has nothing to show, even
    though real, correctly-attributed data exists for that branch.

    Anchoring instead to whichever is EARLIER -- now, or that branch's
    own latest completed transaction -- keeps the window covering that
    branch's actual most recent period of real data. That's what
    "display actual results" means for a branch that isn't continuously
    updated, without fabricating anything: it's still a real `days`-day
    (or `weeks`-week) slice of real transactions, just not one ending
    today.

    Scoped to a single branch only (branch=None keeps the previous,
    now-anchored behavior) -- there's no single sensible anchor across
    branches with different update cadences, and the "All Branches"
    views aggregate branches with live data anyway.

    Nudged 1 second past the latest transaction itself (when that's the
    chosen anchor) rather than sitting exactly on it: every caller below
    filters with a strict upper bound (completed_at__lt=week_end, or an
    exclusive-window pattern via gte on the start side), matching the
    old behavior where "now" was always safely after any existing
    completed_at. Anchoring exactly on the latest transaction's own
    timestamp would make that transaction fail its own window's
    strict-less-than check and get excluded -- the opposite of this
    fix's purpose.
    """
    if branch is None:
        return timezone.now()
    real_transactions = _exclude_synthetic_transactions(
        SalesTransaction.objects.filter(branch=branch, status=SalesTransaction.Status.COMPLETED)
    )
    latest = real_transactions.aggregate(latest=Max("completed_at"))["latest"]
    if latest is None:
        return timezone.now()
    return min(timezone.now(), latest + timedelta(seconds=1))


def _resolve_report_window(days=30, date_from=None, date_to=None, branch=None):
    """Resolves the (since, until) datetime bounds for a report-style
    query, as an EXCLUSIVE upper bound on `until` (matching every
    caller's own completed_at__lt / created_at__lt convention).

    Task #146 (client review, 2026-11-04): "Users should be able to
    select a specific date range... rather than being limited to preset
    reporting periods." When BOTH date_from and date_to (plain python
    date objects) are given, the window is exactly that explicit
    calendar range -- the whole of date_from's day through the whole of
    date_to's day, inclusive -- regardless of "now" or any branch's own
    latest activity. This is a genuinely different shape of query than
    "trailing N days": an admin can ask for a short, arbitrary, past
    range (the client's own example: "November 6-7") that has nothing
    to do with today.

    Otherwise falls back to the existing trailing-N-days-from-anchor
    behavior (see _trailing_window_anchor), unchanged from before this
    function existed -- every caller that doesn't pass date_from/
    date_to behaves exactly as it did previously.
    """
    if date_from and date_to:
        since = timezone.make_aware(datetime.combine(date_from, datetime.min.time()))
        until = timezone.make_aware(datetime.combine(date_to, datetime.min.time())) + timedelta(
            days=1
        )
        return since, until
    anchor = _trailing_window_anchor(branch)
    return anchor - timedelta(days=days), anchor


def get_sales_summary(branch=None, days=30, date_from=None, date_to=None):
    """Overview numbers for the Sales/Analytics Dashboard (Fig. 3-19,
    3-28): total revenue, transaction count, units sold, average daily
    revenue over the given window.

    Revenue is each transaction's grand_total (SalesTransaction's own
    property: item subtotals minus that transaction's whole-order
    discount, if any) -- NOT a raw SUM(quantity * unit_price) over
    SalesItem, which is mathematically identical to the PRE-discount
    subtotal and overstates revenue by the full discount amount on any
    discounted sale (Activity 2 regression testing, Oct 2026: confirmed
    via a Php500 subtotal / 20% discount / Php400 actually-collected
    sale showing as Php500 on this dashboard). Also NOT
    SalesTransaction.amount_tendered -- that's what the customer
    physically handed over (e.g. Php500 tendered for a Php375 sale) and
    would overstate revenue by however much change was given back.

    Iterates transactions in Python (via grand_total/items.all(), both
    prefetched) rather than a single SQL aggregate, since grand_total's
    discount math isn't expressible as a plain SUM() -- a percent
    discount is computed against each transaction's own subtotal, not a
    column that can be summed first and discounted after.

    Accepts an explicit date_from/date_to range (task #146) as an
    alternative to `days` -- see _resolve_report_window. When given,
    `average_daily_revenue` divides by the actual number of days in
    that range rather than the unrelated `days` parameter.
    """
    since, until = _resolve_report_window(days, date_from, date_to, branch)
    transactions = SalesTransaction.objects.filter(
        status=SalesTransaction.Status.COMPLETED, completed_at__gte=since, completed_at__lt=until
    )
    transactions = _exclude_synthetic_transactions(transactions)
    if branch is not None:
        transactions = transactions.filter(branch=branch)
    transactions = list(transactions.prefetch_related("items"))

    total_revenue = sum((txn.grand_total for txn in transactions), Decimal("0.00"))
    total_transactions = len(transactions)
    total_units_sold = sum((item.quantity for txn in transactions for item in txn.items.all()), 0)
    window_days = (date_to - date_from).days + 1 if (date_from and date_to) else days
    average_daily_revenue = (total_revenue / window_days) if window_days else Decimal("0")

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
    discount-aware grand_total revenue calculation as get_sales_summary,
    for the same reason (a raw SalesItem SUM() is the pre-discount
    subtotal, not what was actually collected).

    Anchored via _trailing_window_anchor rather than straight
    timezone.now() -- see that helper's docstring. Without this, a
    batch-only branch (Alangilan) whose last upload is more than `weeks`
    old would show a chart of entirely-empty weeks even though it has
    real recent (for that branch) weekly data to plot."""
    now = _trailing_window_anchor(branch)
    trend = []
    for week_offset in range(weeks - 1, -1, -1):
        week_end = now - timedelta(weeks=week_offset)
        week_start = week_end - timedelta(weeks=1)

        transactions = SalesTransaction.objects.filter(
            status=SalesTransaction.Status.COMPLETED,
            completed_at__gte=week_start,
            completed_at__lt=week_end,
        )
        transactions = _exclude_synthetic_transactions(transactions)
        if branch is not None:
            transactions = transactions.filter(branch=branch)
        transactions = transactions.prefetch_related("items")

        revenue = sum((txn.grand_total for txn in transactions), Decimal("0.00"))
        trend.append({"week_label": week_start.strftime("%b %d"), "revenue": float(revenue)})

    return trend


def get_weekly_sales_trend_by_branch(weeks=8):
    """Same window/shape as get_weekly_sales_trend, but broken out per
    branch -- feeds the Dashboard Home Sales Analytics chart (Fig. 3-19),
    which plots Batangas/Lipa/Alangilan as three separate lines rather
    than one aggregate line. Only meaningful for "All Branches"; when a
    specific branch is selected, the Dashboard reuses the single-line
    get_weekly_sales_trend instead. Same discount-aware grand_total
    revenue calculation as get_weekly_sales_trend -- see that
    function's docstring and get_sales_summary's for why a raw
    SalesItem SUM() is wrong here."""
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
            branch_transactions = SalesTransaction.objects.filter(
                branch=b,
                status=SalesTransaction.Status.COMPLETED,
                completed_at__gte=week_start,
                completed_at__lt=week_end,
            )
            branch_transactions = _exclude_synthetic_transactions(
                branch_transactions
            ).prefetch_related("items")
            revenue = sum((txn.grand_total for txn in branch_transactions), Decimal("0.00"))
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

    Revenue uses each transaction's discount-aware grand_total, same as
    get_sales_summary -- see that function's docstring for why a raw
    SalesItem SUM() overstates revenue on discounted sales.
    """
    from apps.accounts.models import Branch

    results = []
    for branch in Branch.objects.filter(is_active=True, is_commissary=False):
        # Anchored per-branch (see _trailing_window_anchor) rather than
        # once outside the loop -- a batch-only branch (Alangilan) needs
        # its own window anchored to its own latest data, not to
        # whichever branch happens to have live activity right now.
        since = _trailing_window_anchor(branch) - timedelta(days=days)
        transactions = SalesTransaction.objects.filter(
            branch=branch, status=SalesTransaction.Status.COMPLETED, completed_at__gte=since
        )
        transactions = list(_exclude_synthetic_transactions(transactions).prefetch_related("items"))
        revenue = sum((txn.grand_total for txn in transactions), Decimal("0.00"))
        units_sold = sum((item.quantity for txn in transactions for item in txn.items.all()), 0)
        results.append(
            {
                "branch": branch,
                "revenue": revenue,
                "units_sold": units_sold,
                "orders": len(transactions),
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

    Task #161 (adviser dry-run, 2026-11-05): when branch is None ("All
    Branches"), the window used to be anchored once to timezone.now()
    for every branch's transactions combined. A batch-only branch (e.g.
    Alangilan, updated via periodic upload rather than live POS -- see
    _trailing_window_anchor) whose last upload is more than `days` in
    the past then had ALL of its transactions fall outside that single
    now-anchored window and silently contributed nothing to Total
    Revenue -- while the Branch Performance cards (get_branch_
    performance) anchor each branch independently to its OWN latest
    activity and correctly include it. The result was Total Revenue !=
    sum of the Branch Performance cards' own revenue figures whenever
    any branch's last activity was more than `days` old.

    Fixed by anchoring per branch, exactly like get_branch_performance:
    when branch is None, the query is run once per active branch (each
    with its own anchor) and the results are combined, rather than once
    globally. A specific branch still resolves to a single query with
    that branch's own anchor, unchanged from before.
    """
    if branch is None:
        from apps.accounts.models import Branch

        branches = list(Branch.objects.filter(is_active=True, is_commissary=False))
        combined = {
            "total_revenue": Decimal("0.00"),
            "total_transactions": 0,
            "total_units_sold": 0,
            "average_daily_revenue": Decimal("0.00"),
            "days": days,
        }
        for b in branches:
            per_branch = get_sales_summary_with_toggles(
                branch=b,
                days=days,
                include_additional_sales=include_additional_sales,
                exclude_discounts=exclude_discounts,
                exclude_vat=exclude_vat,
            )
            combined["total_revenue"] += per_branch["total_revenue"]
            combined["total_transactions"] += per_branch["total_transactions"]
            combined["total_units_sold"] += per_branch["total_units_sold"]
            combined["average_daily_revenue"] += per_branch["average_daily_revenue"]
        return combined

    since = _trailing_window_anchor(branch) - timedelta(days=days)
    transactions = SalesTransaction.objects.filter(
        status=SalesTransaction.Status.COMPLETED,
        completed_at__gte=since,
        branch=branch,
    )
    transactions = _exclude_synthetic_transactions(transactions).prefetch_related("items")

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
    products" panel on the Analytics Dashboard (Fig. 3-19).

    NOTE (same discount issue tracked on the dashboard's revenue totals,
    not fixed here): this panel's per-product `revenue` is still a raw
    quantity x unit_price SUM(), i.e. pre-transaction-discount. Unlike
    get_sales_summary's single total, a whole-order discount here can't
    be honestly allocated back down to individual line items (which
    share of a flat/percent order discount belongs to which product?),
    so this is left as-is rather than introducing an arbitrary
    allocation rule. Ranking by units_sold (what this panel is actually
    for) is unaffected either way; only the displayed revenue figure per
    product should be read as pre-discount."""
    since = _trailing_window_anchor(branch) - timedelta(days=days)
    items = SalesItem.objects.filter(
        transaction__status=SalesTransaction.Status.COMPLETED,
        transaction__completed_at__gte=since,
    )
    items = _exclude_synthetic_transactions(items, cashier_lookup="transaction__cashier__username")
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


def get_total_units_sold(branch=None, days=30):
    """Total units sold across every product over the window -- the same
    filter criteria as get_top_products (completed transactions, same
    window/branch), used as the denominator for that panel's per-product
    percentage share (Dashboard Home's Top Products pie chart). Kept as
    its own small query rather than summing get_top_products' own
    (limited, ranked) results, since the percentages need to reflect
    every product sold, not just the top N shown."""
    since = _trailing_window_anchor(branch) - timedelta(days=days)
    items = SalesItem.objects.filter(
        transaction__status=SalesTransaction.Status.COMPLETED,
        transaction__completed_at__gte=since,
    )
    items = _exclude_synthetic_transactions(items, cashier_lookup="transaction__cashier__username")
    if branch is not None:
        items = items.filter(transaction__branch=branch)

    return items.aggregate(total=Sum("quantity"))["total"] or 0


def get_product_performance(branch=None, days=30, date_from=None, date_to=None):
    """Per-product performance and growth rate (Fig. 3-29): units sold in
    the current window vs. the equally-sized window immediately before
    it. Products with no sales in either window are omitted -- a 0-to-0
    "growth rate" is meaningless noise, not a real signal.

    Accepts an explicit date_from/date_to range (task #146) as an
    alternative to `days` -- the "current" window becomes exactly that
    range, and "previous" is the immediately-preceding window of the
    same length, same as the days-based behavior.

    Task #142 (client review): two bugs found while auditing the
    Reports module for branch misaggregation.

    1. product__isnull=True rows are now excluded. The real client
       historical-sales backfill (import_branch_sales_history) creates
       SalesItem rows with product=None and a custom_name like
       "Store Sales (Imported)" -- one per revenue stream per day, not
       one per actual product. Before this filter, Django's
       .values("product__id", ...) grouped every single one of those
       rows together under one product_id=None bucket, producing a
       single phantom row with product_name=None whose "units_sold"
       silently summed the *count of imported transactions* across
       ALL THREE BRANCHES and ALL FOUR revenue streams (Store/Cafe AM/
       Cafe PM/Cafe Total) combined -- a textbook misaggregation.
       Reproduced directly: three transactions across two branches and
       two streams collapsed into one {"product_name": None,
       "current_units": 3, ...} row. A None product was never a real
       product to report "performance" on in the first place.

    2. Added branch filtering, matching get_sales_summary's/
       get_weekly_sales_trend's own branch=None pattern. The Reports
       Hub view passes `branch=selected_branch` to those two but never
       to this one, so selecting a branch on that page silently left
       Product Performance showing every branch's data regardless --
       not reachable through the current in-app navigation (nothing
       yet links to reports_hub with ?branch=, confirmed via grep), but
       live, URL-reachable, and inconsistent with its sibling
       functions on the exact same page."""
    current_start, now = _resolve_report_window(days, date_from, date_to, branch)
    previous_start = current_start - (now - current_start)

    def _units_sold_in_range(start, end):
        items = SalesItem.objects.filter(
            product__isnull=False,
            transaction__status=SalesTransaction.Status.COMPLETED,
            transaction__completed_at__gte=start,
            transaction__completed_at__lt=end,
        )
        items = _exclude_synthetic_transactions(
            items, cashier_lookup="transaction__cashier__username"
        )
        if branch is not None:
            items = items.filter(transaction__branch=branch)
        return items.values("product__id", "product__name").annotate(units_sold=Sum("quantity"))

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


def get_resource_consumption_summary(days=30, date_from=None, date_to=None):
    """Resource Consumption Analytics (Fig. 3-30): total materials used,
    total material cost, and cost broken down per finished good produced
    -- all derived from Production's own IngredientUsage records, not a
    separate tracking system.

    Accepts an explicit date_from/date_to range (task #146) as an
    alternative to `days` -- see _resolve_report_window.

    Task #163 (adviser dry-run, 2026-11-05): `total_units_used` used to
    be a single Sum("quantity_used") across every material, regardless
    of unit -- adding 5kg of flour, 2L of milk, and 300 pieces of cup
    lids into one "307" figure, which is meaningless (not a real
    quantity of anything). Replaced with `units_used_by_unit`, a list
    of {unit, unit_display, total} broken out per unit_of_measure, so
    nothing with a different unit is ever summed together. For the
    same reason, `by_material` now also carries each material's own
    unit (`unit`/`unit_display`), both for display (the ticket's "Add a
    Unit column") and so a future consumer of this data can't
    accidentally sum across units either.
    """
    since, until = _resolve_report_window(days, date_from, date_to)
    usages = IngredientUsage.objects.filter(
        production_record__created_at__gte=since, production_record__created_at__lt=until
    )

    unit_display_map = dict(Product.UnitOfMeasure.choices)

    units_used_by_unit = [
        {
            "unit": row["material__unit_of_measure"],
            "unit_display": unit_display_map.get(
                row["material__unit_of_measure"], row["material__unit_of_measure"]
            ),
            "total": row["total"],
        }
        for row in (
            usages.values("material__unit_of_measure")
            .annotate(total=Sum("quantity_used"))
            .order_by("material__unit_of_measure")
        )
    ]

    total_cost = usages.aggregate(total=Sum(F("quantity_used") * F("material__price")))[
        "total"
    ] or Decimal("0")

    total_produced = (
        ProductionRecord.objects.filter(created_at__gte=since, created_at__lt=until).aggregate(
            total=Sum("quantity_produced")
        )["total"]
        or 0
    )
    cost_per_unit_produced = (total_cost / total_produced) if total_produced else Decimal("0")

    by_material = [
        {
            "material__name": row["material__name"],
            "unit": row["material__unit_of_measure"],
            "unit_display": unit_display_map.get(
                row["material__unit_of_measure"], row["material__unit_of_measure"]
            ),
            "total_used": row["total_used"],
            "total_material_cost": row["total_material_cost"],
        }
        for row in (
            usages.values("material__name", "material__unit_of_measure")
            .annotate(
                total_used=Sum("quantity_used"),
                total_material_cost=Sum(F("quantity_used") * F("material__price")),
            )
            .order_by("-total_used")
        )
    ]

    return {
        "units_used_by_unit": units_used_by_unit,
        "total_cost": total_cost,
        "total_produced": total_produced,
        "cost_per_unit_produced": cost_per_unit_produced,
        "by_material": by_material,
        "days": days,
    }


def get_operational_performance_summary(days=30, date_from=None, date_to=None):
    """Operational Performance (Fig. 3-31): batch completion rate and
    quality pass rate over the window. Average production TIME is
    deliberately not reported here -- ProductionRecord doesn't track a
    start/end duration distinct from created_at, so fabricating a number
    for it would be reporting something we don't actually measure.

    Accepts an explicit date_from/date_to range (task #146) as an
    alternative to `days` -- see _resolve_report_window."""
    since, until = _resolve_report_window(days, date_from, date_to)
    records = ProductionRecord.objects.filter(created_at__gte=since, created_at__lt=until)

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


def get_sales_history(branch=None, cashier_id=None, date_from=None, date_to=None):
    """Transaction-level Sales History (backend gap assessment: "add a
    filterable sales-history view (branch/cashier/date)"). Unlike the
    other functions in this module, which aggregate across many
    transactions, this returns the individual completed transactions
    themselves -- for looking up or auditing a specific sale, not for
    dashboard totals.

    Only COMPLETED transactions -- a DRAFT (abandoned mid-order) or
    VOIDED sale was never a real, collected sale and has no place in a
    sales history (same exclusion get_sales_summary already applies).

    Client review (2026-11-10): also excludes the synthetic forecast-
    backfill bot's own rows unconditionally -- see
    _exclude_synthetic_transactions. Those were never a real sale either,
    and Sales History is exactly the kind of per-transaction audit view
    where presenting one as an ordinary completed sale would be actively
    misleading, not just an inflated total. The real, migrated
    "historical_import_<branch>" rows are NOT excluded -- see
    get_cashiers_with_sales_history for why those stay.

    `date_from`/`date_to` are inclusive calendar dates (not datetimes) in
    the business's own local time (settings.TIME_ZONE), matching how an
    Owner/Admin actually thinks about "show me Tuesday's sales" rather
    than requiring them to reason about UTC boundaries.
    """
    qs = (
        SalesTransaction.objects.filter(status=SalesTransaction.Status.COMPLETED)
        .select_related("branch", "cashier")
        .prefetch_related("items")
    )
    qs = _exclude_synthetic_transactions(qs)

    if branch is not None:
        qs = qs.filter(branch=branch)
    if cashier_id:
        qs = qs.filter(cashier_id=cashier_id)
    if date_from:
        qs = qs.filter(completed_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(completed_at__date__lte=date_to)

    return qs.order_by("-completed_at")


def get_cashiers_with_sales_history():
    """Cashiers to offer in the Sales History filter dropdown -- everyone
    who has at least one COMPLETED, non-synthetic transaction, including
    an account since deactivated (a past sale by a now-former cashier is
    still part of the history and still needs to be findable), rather
    than every currently-active Branch Staff account regardless of
    whether they've ever actually made a sale.

    Client review (2026-11-10): the synthetic forecast-backfill bot
    (User.SYNTHETIC_BACKFILL_BOT_USERNAME) is excluded outright -- it was
    showing up here as an ordinary, filterable "cashier" right alongside
    real staff, which misrepresents forecast-only test data as a
    legitimate sales channel. Its own rows are also excluded from
    get_sales_history's results, so there would be nothing to show even
    if it were offered here.

    apps.core's "historical_import_<branch>" accounts are deliberately
    NOT excluded -- their attributed sales are real, migrated history
    (see User.is_historical_import_account), and an Owner/Admin may
    genuinely want to filter Sales History down to just that branch's
    imported record. They're still clearly distinguished from a real
    staff member's name via User.display_name, rather than showing the
    raw "historical_import_<branch>" username (used in
    analytics/sales_history.html instead of get_full_name/username)."""
    from django.contrib.auth import get_user_model

    cashier_ids = (
        _exclude_synthetic_transactions(
            SalesTransaction.objects.filter(status=SalesTransaction.Status.COMPLETED)
        )
        .values_list("cashier_id", flat=True)
        .distinct()
    )
    return get_user_model().objects.filter(pk__in=cashier_ids).order_by("username")
