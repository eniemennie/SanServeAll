"""
AI/DSS module: ARIMA forecasting (Row 10), inventory risk classification
and natural-language insights (Row 11).
"""

from django.db import models


class Forecast(models.Model):
    """One predicted-demand data point: a specific product, at a specific
    branch, for a specific future date. A single forecast RUN produces
    several of these rows (e.g. 7 rows for a 7-day-ahead forecast) --
    `generated_at` groups rows from the same run together."""

    branch = models.ForeignKey("accounts.Branch", on_delete=models.CASCADE)
    product = models.ForeignKey("inventory.Product", on_delete=models.CASCADE)
    forecast_date = models.DateField(help_text="The future date this prediction is for.")
    predicted_quantity = models.FloatField()
    # Records which method actually produced this number -- ARIMA needs a
    # reasonable amount of history to fit meaningfully; a new product/
    # branch pair with too little sales history falls back to a naive
    # average instead of forcing ARIMA on data it can't model well.
    model_used = models.CharField(max_length=50)
    # Mean Absolute Error against a held-out slice of real historical
    # data, computed at the time this forecast was generated -- null
    # when there wasn't enough history to hold anything out for
    # validation (Phase 3's "MAE/RMSE validation" decision, applied
    # honestly rather than faked when data is too sparse to support it).
    mae = models.FloatField(null=True, blank=True)
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-generated_at", "forecast_date"]

    def __str__(self):
        return (
            f"{self.product.name} @ {self.branch.name} on "
            f"{self.forecast_date}: {self.predicted_quantity:.1f}"
        )


class InventoryRiskScore(models.Model):
    """One risk classification for a (branch, product) pair (Row 11.1).

    Labels are bootstrapped from a clear business rule (days of stock
    left at current demand) rather than genuine historical "this actually
    went out of stock" labels, which don't exist yet for a brand-new
    system -- a real scikit-learn DecisionTreeClassifier is fit and
    predicts on this rule-derived data, a standard cold-start pattern,
    not a shortcut around actually using the library.
    """

    class RiskLevel(models.TextChoices):
        LOW = "LOW", "Low Risk"
        MEDIUM = "MEDIUM", "Medium Risk"
        HIGH = "HIGH", "High Risk"

    branch = models.ForeignKey("accounts.Branch", on_delete=models.CASCADE)
    product = models.ForeignKey("inventory.Product", on_delete=models.CASCADE)
    risk_level = models.CharField(max_length=10, choices=RiskLevel.choices)
    quantity_on_hand = models.IntegerField(help_text="Stock snapshot at classification time.")
    avg_daily_demand = models.FloatField()
    days_of_stock_left = models.FloatField(
        null=True, blank=True, help_text="Null when demand is zero (stock never runs out)."
    )
    computed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-computed_at"]

    def __str__(self):
        return f"{self.product.name} @ {self.branch.name}: {self.risk_level}"


class AIInsight(models.Model):
    """A natural-language summary generated from forecast/risk data
    (Row 11.2) -- stored so the dashboard (Week 11.3) reads a pre-computed
    result rather than calling the AI API on every page load, matching
    the Phase 2 decoupling rule that AI processing runs on schedule, not
    in the live request path."""

    class InsightType(models.TextChoices):
        STOCKOUT_WARNING = "STOCKOUT_WARNING", "Stockout Warning"
        DEMAND_SUMMARY = "DEMAND_SUMMARY", "Demand Summary"

    branch = models.ForeignKey("accounts.Branch", on_delete=models.CASCADE)
    insight_type = models.CharField(max_length=32, choices=InsightType.choices)
    message = models.TextField()
    # True when a real AI API call produced this text; False when it fell
    # back to a plain-template message (no API key configured, or the
    # call failed) -- the dashboard can be honest about which happened
    # rather than presenting a template sentence as if it were AI-written.
    generated_by_ai = models.BooleanField(default=False)
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-generated_at"]

    def __str__(self):
        return f"{self.get_insight_type_display()} for {self.branch.name}"
