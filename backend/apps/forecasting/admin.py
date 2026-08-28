from django.contrib import admin

from apps.forecasting.models import Forecast


@admin.register(Forecast)
class ForecastAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "branch",
        "forecast_date",
        "predicted_quantity",
        "model_used",
        "mae",
        "generated_at",
    )
    list_filter = ("branch", "model_used")
    search_fields = ("product__name",)
