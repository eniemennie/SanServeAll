"""
Inventory risk classification (Row 11.1): predefined threshold rules
based on current stock level, average consumption rate, and estimated
days remaining -- not a trained model (manuscript Chapter 3.6).

NOTE: an earlier version of this module also trained and ran a real
scikit-learn DecisionTreeClassifier on top of these same rule-derived
labels (fit and predicted on the same batch of rows), which technically
contradicted the manuscript's explicit "predefined thresholds...rather
than a trained classification model" claim. Removed to match what's
already documented and adviser-approved, rather than carrying code that
says something different from the paper. The classifier's predictions
were functionally near-identical to the rule anyway, since it was
trained on the rule's own output -- this removal has no real behavioral
impact, only removes the discrepancy.
"""

from datetime import timedelta

from django.utils import timezone

from apps.pos.models import SalesItem, SalesTransaction


def _get_risk_thresholds():
    """Reads the current risk thresholds from SystemConfiguration (Row
    12.2) -- Owner/Admin can tune these at runtime instead of them being
    fixed constants, as they were before Week 12. SystemConfiguration's
    own model field defaults (3/10) are the single source of truth for
    the fallback values -- not duplicated here."""
    from apps.system_config.models import SystemConfiguration

    config = SystemConfiguration.load()
    return config.high_risk_days_threshold, config.medium_risk_days_threshold


def compute_average_daily_demand(branch, product, days_history=30):
    """Average units of `product` consumed per day at `branch` over the
    recent window -- the demand-velocity input to risk scoring.

    Finished goods are sold via POS, so their demand comes from SalesItem.
    Raw materials are never sold directly -- they're consumed through
    Production (Week 8's IngredientUsage) -- so using SalesItem for them
    would always read zero demand regardless of real consumption, making
    every material look artificially "safe." Each product type reads
    from the data source that actually reflects how it leaves inventory.

    Packaging Material (client review, 2026-11-09, promoted from a
    MATERIAL row tagged category=PACKAGING to its own product_type) is
    never a SalesItem either -- a cup or lid is never its own line item
    -- so it would hit the same "always zero" problem as a raw material
    if it fell into the `else` branch below. Unlike a raw material
    though, it isn't only consumed through IngredientUsage: most of it
    is deducted per sold finished good via ProductPackaging (see
    apps.pos.services._deduct_inventory_for_sale), logged as its own
    PACKAGING_CONSUMPTION InventoryTransaction at the branch that sold
    it; some may also be used directly in a production batch (e.g.
    takeout boxes assembled at the commissary), logged as
    PRODUCTION_CONSUMPTION. Summing both, branch-scoped, is the honest
    "how much of this did this branch actually use" figure -- the same
    real audit trail driving find_ledger_discrepancies, not a guess.
    """
    from apps.inventory.models import InventoryTransaction, Product

    since = timezone.now() - timedelta(days=days_history)

    if product.product_type == Product.ProductType.MATERIAL:
        from apps.production.models import IngredientUsage

        # ProductionRecord has no branch field -- all production happens
        # at the single commissary by design (Week 8), so `branch` isn't
        # meaningful to filter by here; every material's consumption is
        # already implicitly commissary-scoped.
        total_used = IngredientUsage.objects.filter(
            production_record__created_at__gte=since,
            material=product,
        ).values_list("quantity_used", flat=True)
        total = sum(total_used)
    elif product.product_type == Product.ProductType.PACKAGING:
        consumption_types = [
            InventoryTransaction.MovementType.PACKAGING_CONSUMPTION,
            InventoryTransaction.MovementType.PRODUCTION_CONSUMPTION,
        ]
        # quantity_change is negative for a deduction -- negate the sum
        # to get a positive "amount consumed", same sign convention
        # compute_features expects from the MATERIAL/SalesItem branches.
        total_consumed = InventoryTransaction.objects.filter(
            branch=branch,
            product=product,
            movement_type__in=consumption_types,
            created_at__gte=since,
        ).values_list("quantity_change", flat=True)
        total = -sum(total_consumed)
    else:
        total_sold = SalesItem.objects.filter(
            transaction__branch=branch,
            transaction__status=SalesTransaction.Status.COMPLETED,
            transaction__completed_at__gte=since,
            product=product,
        ).values_list("quantity", flat=True)
        total = sum(total_sold)

    # Cast to float before dividing, not after: IngredientUsage.quantity_used
    # (the MATERIAL path above) is a Decimal field, so `total` is a Decimal
    # whenever there's any real material consumption -- SalesItem.quantity's
    # plain-int sum (the finished-good path) never exposed this, since
    # int/int division is already a float in Python 3. compute_features
    # below relies on this always being a float (its own comment says so)
    # and does `float(quantity_on_hand) / avg_daily_demand`, which raises
    # TypeError for float / Decimal -- never caught by tests since
    # IngredientUsage was empty (zero real consumption, never reaching the
    # `avg_daily_demand > 0` branch) until the 2026-11-03 historical import.
    return float(total) / days_history if days_history else 0.0


def compute_features(inventory):
    """Returns (quantity_on_hand, avg_daily_demand, days_of_stock_left)
    for one Inventory row. days_of_stock_left is None when demand is
    zero -- stock that nobody is buying never "runs out" in any
    meaningful sense, so reporting a finite number there would be
    misleading, not just imprecise."""
    avg_daily_demand = compute_average_daily_demand(inventory.branch, inventory.product)
    if avg_daily_demand > 0:
        # quantity_on_hand is a Decimal (client raw-data import: real
        # stock can be fractional); cast to float since avg_daily_demand
        # and days_of_stock_left are plain floats -- Python doesn't
        # support Decimal / float division directly.
        days_of_stock_left = float(inventory.quantity_on_hand) / avg_daily_demand
    else:
        days_of_stock_left = None
    return inventory.quantity_on_hand, avg_daily_demand, days_of_stock_left


def rule_based_label(quantity_on_hand, avg_daily_demand, days_of_stock_left):
    """The one and only classification rule (see module docstring for
    why there's no trained model backing this).

    Thresholds are read from SystemConfiguration (Row 12.2) on every call
    rather than cached, so an Owner/Admin's change takes effect on the
    very next scheduled run without a restart."""
    if quantity_on_hand <= 0:
        return "HIGH"
    if days_of_stock_left is None:
        return "LOW"  # no measurable demand -- stock isn't at risk of running out

    high_threshold, medium_threshold = _get_risk_thresholds()
    if days_of_stock_left < high_threshold:
        return "HIGH"
    if days_of_stock_left < medium_threshold:
        return "MEDIUM"
    return "LOW"


def classify_inventory_rows(inventory_queryset):
    """Classifies every Inventory row in the queryset using the rule
    directly (see module docstring) -- no trained model involved.

    Returns a list of dicts: {"inventory": ..., "risk_level": ...,
    "avg_daily_demand": ..., "days_of_stock_left": ...}.
    """
    rows = list(inventory_queryset)
    if not rows:
        return []

    results = []
    for inventory in rows:
        quantity_on_hand, avg_daily_demand, days_of_stock_left = compute_features(inventory)
        risk_level = rule_based_label(quantity_on_hand, avg_daily_demand, days_of_stock_left)
        results.append(
            {
                "inventory": inventory,
                "risk_level": risk_level,
                "quantity_on_hand": quantity_on_hand,
                "avg_daily_demand": avg_daily_demand,
                "days_of_stock_left": days_of_stock_left,
            }
        )
    return results
