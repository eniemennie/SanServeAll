"""
System Settings (Row 12.1) and System Configuration (Row 12.2). Both are
singleton models -- exactly one row ever exists, since these represent
global business/system settings, not per-branch or per-user records.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models


class SingletonModel(models.Model):
    """Enforces exactly one row (pk=1) for any subclass. A well-known
    pattern for site-wide settings -- implemented directly here rather
    than adding a third-party package for something this small."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # the singleton row is never deleted through the ORM

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class BusinessSettings(SingletonModel):
    """System Settings Interface (Row 12.1): business-facing info that
    appears on receipts and system-wide displays."""

    business_name = models.CharField(max_length=150, default="Jorge's Casa De Sans Rival")
    business_address = models.CharField(max_length=255, blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    tax_rate_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("12.00"))
    currency_symbol = models.CharField(max_length=5, default="\u20b1")  # Philippine peso sign
    receipt_footer_text = models.CharField(
        max_length=255, blank=True, default="Thank you for your purchase!"
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.business_name


class SystemConfiguration(SingletonModel):
    """System Configuration Interface (Row 12.2): behavior that was
    previously hardcoded in Weeks 10-11 (risk thresholds, forecast
    window, admin alert routing, AI on/off) -- now genuinely
    configurable by Owner/Admin at runtime, not just a cosmetic form."""

    admin_alert_email = models.EmailField(
        blank=True,
        help_text="Where scheduled-job failure alerts are sent. Falls back to "
        "Django's ADMINS setting if left blank.",
    )
    ai_insights_enabled = models.BooleanField(
        default=True,
        help_text="When off, insight generation always uses the template "
        "fallback -- no live AI API calls are made at all, regardless of "
        "whether an API key is configured.",
    )
    high_risk_days_threshold = models.PositiveIntegerField(default=3)
    medium_risk_days_threshold = models.PositiveIntegerField(default=10)
    default_forecast_days = models.PositiveIntegerField(default=7)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.high_risk_days_threshold >= self.medium_risk_days_threshold:
            raise ValidationError(
                "High-risk threshold must be lower than the medium-risk "
                "threshold -- otherwise nothing could ever be classified "
                "as medium risk."
            )

    def __str__(self):
        return "System Configuration"
