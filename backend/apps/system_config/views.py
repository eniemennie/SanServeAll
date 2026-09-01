"""
Views for System Settings (Row 12.1) and System Configuration (Row 12.2).
Owner/Admin only -- these are global, business-wide settings, matching
the ownership pattern used for every other admin-only screen.
"""

from decimal import Decimal, InvalidOperation

from django.shortcuts import redirect, render

from apps.accounts.models import Role
from apps.accounts.permissions import role_required
from apps.system_config.models import BusinessSettings, SystemConfiguration


@role_required(Role.OWNER_ADMIN)
def system_settings(request):
    """System Settings Interface (Row 12.1): business info that appears
    on receipts and system-wide displays."""
    settings_obj = BusinessSettings.load()
    error = None

    if request.method == "POST":
        try:
            settings_obj.business_name = request.POST.get("business_name", "").strip()
            settings_obj.business_address = request.POST.get("business_address", "").strip()
            settings_obj.contact_phone = request.POST.get("contact_phone", "").strip()
            settings_obj.tax_rate_percent = Decimal(request.POST.get("tax_rate_percent") or "0")
            settings_obj.currency_symbol = request.POST.get("currency_symbol", "\u20b1").strip()
            settings_obj.receipt_footer_text = request.POST.get("receipt_footer_text", "").strip()

            if not settings_obj.business_name:
                error = "Business name is required."
            else:
                settings_obj.save()
                return redirect("system_config:system_settings")
        except InvalidOperation:
            error = "Tax rate must be a valid number."

    return render(
        request, "system_config/system_settings.html", {"settings": settings_obj, "error": error}
    )


@role_required(Role.OWNER_ADMIN)
def system_configuration(request):
    """System Configuration Interface (Row 12.2): behavior that was
    previously hardcoded in Weeks 10-11 (risk thresholds, forecast
    window, alert routing, AI on/off)."""
    config = SystemConfiguration.load()
    error = None

    if request.method == "POST":
        try:
            high_threshold = int(request.POST.get("high_risk_days_threshold") or 0)
            medium_threshold = int(request.POST.get("medium_risk_days_threshold") or 0)
            forecast_days = int(request.POST.get("default_forecast_days") or 0)

            if high_threshold <= 0 or medium_threshold <= 0 or forecast_days <= 0:
                error = "All threshold and day values must be greater than zero."
            elif high_threshold >= medium_threshold:
                error = (
                    "High-risk threshold must be lower than the medium-risk "
                    "threshold -- otherwise nothing could ever be classified "
                    "as medium risk."
                )
            else:
                config.admin_alert_email = request.POST.get("admin_alert_email", "").strip()
                config.ai_insights_enabled = request.POST.get("ai_insights_enabled") == "on"
                config.high_risk_days_threshold = high_threshold
                config.medium_risk_days_threshold = medium_threshold
                config.default_forecast_days = forecast_days
                config.save()
                return redirect("system_config:system_configuration")
        except (TypeError, ValueError):
            error = "Please enter valid whole numbers for the threshold fields."

    return render(
        request, "system_config/system_configuration.html", {"config": config, "error": error}
    )
