"""
Inventory risk classification (Row 11.1). See the module docstring on
InventoryRiskScore for why labels are rule-bootstrapped rather than drawn
from real historical outcomes -- a real scikit-learn DecisionTreeClassifier
still does the actual classification, this just explains where its
training labels come from on a brand-new system with no track record yet.
"""

from datetime import timedelta

import numpy as np
from django.utils import timezone
from sklearn.tree import DecisionTreeClassifier

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
    """
    from apps.inventory.models import Product

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
    else:
        total_sold = SalesItem.objects.filter(
            transaction__branch=branch,
            transaction__status=SalesTransaction.Status.COMPLETED,
            transaction__completed_at__gte=since,
            product=product,
        ).values_list("quantity", flat=True)
        total = sum(total_sold)

    return total / days_history if days_history else 0.0


def compute_features(inventory):
    """Returns (quantity_on_hand, avg_daily_demand, days_of_stock_left)
    for one Inventory row. days_of_stock_left is None when demand is
    zero -- stock that nobody is buying never "runs out" in any
    meaningful sense, so reporting a finite number there would be
    misleading, not just imprecise."""
    avg_daily_demand = compute_average_daily_demand(inventory.branch, inventory.product)
    if avg_daily_demand > 0:
        days_of_stock_left = inventory.quantity_on_hand / avg_daily_demand
    else:
        days_of_stock_left = None
    return inventory.quantity_on_hand, avg_daily_demand, days_of_stock_left


def rule_based_label(quantity_on_hand, avg_daily_demand, days_of_stock_left):
    """The bootstrap business rule used to generate training labels (see
    module docstring). Also used directly as the final label whenever a
    fitted classifier isn't available (e.g. only one inventory row exists
    -- scikit-learn's DecisionTreeClassifier needs at least 2 classes
    represented to fit at all).

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


def _days_left_as_feature(days_of_stock_left):
    """scikit-learn needs a numeric value, not None -- a large finite
    number stands in for "effectively never runs out" without special-
    casing None throughout the feature matrix."""
    return days_of_stock_left if days_of_stock_left is not None else 9999.0


def classify_inventory_rows(inventory_queryset):
    """Classifies every Inventory row in the queryset.

    Returns a list of dicts: {"inventory": ..., "risk_level": ...,
    "avg_daily_demand": ..., "days_of_stock_left": ...}.

    Trains a fresh DecisionTreeClassifier on the SAME batch of rows being
    classified (features + rule-bootstrapped labels), then predicts on
    those rows -- appropriate at this data scale (a handful of branches x
    products), where a separate held-out training set isn't meaningfully
    different from just using the rule directly, but a real classifier is
    still what makes the actual prediction.
    """
    rows = list(inventory_queryset)
    if not rows:
        return []

    features = []
    rule_labels = []
    raw_features = []  # (quantity_on_hand, avg_daily_demand, days_of_stock_left) for output

    for inventory in rows:
        quantity_on_hand, avg_daily_demand, days_of_stock_left = compute_features(inventory)
        raw_features.append((quantity_on_hand, avg_daily_demand, days_of_stock_left))
        features.append(
            [quantity_on_hand, avg_daily_demand, _days_left_as_feature(days_of_stock_left)]
        )
        rule_labels.append(rule_based_label(quantity_on_hand, avg_daily_demand, days_of_stock_left))

    X = np.array(features)
    unique_labels = set(rule_labels)

    if len(unique_labels) < 2:
        # A DecisionTreeClassifier can't meaningfully fit on a single
        # class -- every row already has the same rule-based label, so
        # there's nothing for a classifier to distinguish anyway.
        predicted_labels = rule_labels
    else:
        classifier = DecisionTreeClassifier(max_depth=4, random_state=42)
        classifier.fit(X, rule_labels)
        predicted_labels = list(classifier.predict(X))

    results = []
    for inventory, (quantity_on_hand, avg_daily_demand, days_of_stock_left), risk_level in zip(
        rows, raw_features, predicted_labels
    ):
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
