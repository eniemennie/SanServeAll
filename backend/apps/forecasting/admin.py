from django.contrib import admin

from apps.forecasting.models import AIInsight, Forecast, InventoryRiskScore


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


@admin.register(InventoryRiskScore)
class InventoryRiskScoreAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "branch",
        "risk_level",
        "quantity_on_hand",
        "avg_daily_demand",
        "days_of_stock_left",
        "computed_at",
    )
    list_filter = ("branch", "risk_level")
    search_fields = ("product__name",)


@admin.register(AIInsight)
class AIInsightAdmin(admin.ModelAdmin):
    list_display = ("branch", "insight_type", "generated_by_ai", "generated_at")
    list_filter = ("branch", "insight_type", "generated_by_ai")
