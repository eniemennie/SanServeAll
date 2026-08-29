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

# Business-rule thresholds for the bootstrap labels. Chosen to be
# conservative (flagging risk a bit earlier rather than later) -- a false
# "medium risk" flag costs a manager a few seconds double-checking a
# shelf; a missed real stockout costs a lost sale.
HIGH_RISK_DAYS_THRESHOLD = 3
MEDIUM_RISK_DAYS_THRESHOLD = 10


def compute_average_daily_demand(branch, product, days_history=30):
    """Average units of `product` sold per day at `branch` over the
    recent window -- the demand-velocity input to risk scoring. Simpler
    than forecasting's own ARIMA-based series (no need for a full time
    series here, just one summary number)."""
    since = timezone.now() - timedelta(days=days_history)
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
    represented to fit at all)."""
    if quantity_on_hand <= 0:
        return "HIGH"
    if days_of_stock_left is None:
        return "LOW"  # no measurable demand -- stock isn't at risk of running out
    if days_of_stock_left < HIGH_RISK_DAYS_THRESHOLD:
        return "HIGH"
    if days_of_stock_left < MEDIUM_RISK_DAYS_THRESHOLD:
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
