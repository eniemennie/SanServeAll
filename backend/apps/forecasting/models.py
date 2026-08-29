"""
AI/DSS module: ARIMA forecasting (Row 10). Risk classification and
natural-language insights land in Week 11 (Row 11) -- this app only
holds the forecasting output for now.
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
