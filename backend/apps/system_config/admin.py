from django.contrib import admin

from apps.system_config.models import BusinessSettings, SystemConfiguration


@admin.register(BusinessSettings)
class BusinessSettingsAdmin(admin.ModelAdmin):
    list_display = ("business_name", "tax_rate_percent", "currency_symbol", "updated_at")

    def has_add_permission(self, request):
        # Singleton -- never allow creating a second row through admin.
        return not BusinessSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SystemConfiguration)
class SystemConfigurationAdmin(admin.ModelAdmin):
    list_display = (
        "admin_alert_email",
        "ai_insights_enabled",
        "high_risk_days_threshold",
        "medium_risk_days_threshold",
        "default_forecast_days",
        "updated_at",
    )

    def has_add_permission(self, request):
        return not SystemConfiguration.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
