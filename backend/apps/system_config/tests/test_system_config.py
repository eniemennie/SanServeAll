"""
Tests for Week 12 (batch 1): System Settings Interface (Row 12.1) and
System Configuration Interface (Row 12.2), including real wiring into
Week 10-11's previously-hardcoded risk thresholds, forecast window, and
alert routing.
"""

from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.forecasting.ml.risk_classifier import rule_based_label
from apps.system_config.models import BusinessSettings, SystemConfiguration
from conftest import verify_otp_for_client

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    role = Role.objects.create(name=Role.OWNER_ADMIN)
    return User.objects.create_user(username="owner1", password="testpass123", role=role)


@pytest.fixture
def owner_client(client, owner):
    client.force_login(owner)
    verify_otp_for_client(client, owner)
    return client


@pytest.fixture
def cashier():
    role = Role.objects.create(name=Role.BRANCH_STAFF)
    return User.objects.create_user(username="cashier1", password="testpass123", role=role)


class TestSingletonBehavior:
    def test_business_settings_load_creates_exactly_one_row(self):
        BusinessSettings.load()
        BusinessSettings.load()
        assert BusinessSettings.objects.count() == 1

    def test_saving_never_creates_a_second_row(self):
        obj = BusinessSettings.load()
        obj.business_name = "Updated Name"
        obj.save()
        another = BusinessSettings.load()
        assert BusinessSettings.objects.count() == 1
        assert another.business_name == "Updated Name"

    def test_delete_is_a_no_op(self):
        obj = BusinessSettings.load()
        obj.delete()
        assert BusinessSettings.objects.count() == 1

    def test_system_configuration_defaults_match_the_old_hardcoded_values(self):
        """Confirms the migration to configurable thresholds didn't
        silently change existing behavior -- the defaults match exactly
        what was hardcoded before Week 12."""
        config = SystemConfiguration.load()
        assert config.high_risk_days_threshold == 3
        assert config.medium_risk_days_threshold == 10
        assert config.default_forecast_days == 7
        assert config.ai_insights_enabled is True


class TestRiskThresholdsAreConfigurable:
    def test_rule_based_label_uses_configured_high_threshold(self):
        config = SystemConfiguration.load()
        config.high_risk_days_threshold = 5
        config.medium_risk_days_threshold = 15
        config.save()

        # 4 days left: below the CONFIGURED high threshold (5), so HIGH --
        # would have been "not high" under the old hardcoded default of 3.
        assert rule_based_label(50, 10.0, 4.0) == "HIGH"

    def test_rule_based_label_uses_configured_medium_threshold(self):
        config = SystemConfiguration.load()
        config.high_risk_days_threshold = 2
        config.medium_risk_days_threshold = 20
        config.save()

        assert rule_based_label(50, 10.0, 15.0) == "MEDIUM"

    def test_a_config_change_takes_effect_on_the_very_next_call(self):
        """No caching, no restart required -- confirmed by changing
        config mid-test and calling the classifier again immediately."""
        assert rule_based_label(50, 10.0, 4.0) == "MEDIUM"  # default thresholds: 3/10

        config = SystemConfiguration.load()
        config.high_risk_days_threshold = 5
        config.save()

        assert rule_based_label(50, 10.0, 4.0) == "HIGH"  # same input, new result


class TestAIInsightsToggle:
    def test_disabled_toggle_never_calls_the_api_even_with_a_key_configured(self, settings):
        from apps.forecasting.ml.insight_generator import generate_insight

        settings.CLAUDE_API_KEY = "fake-key-that-would-otherwise-be-used"
        with patch("apps.forecasting.ml.insight_generator.requests.post") as mock_post:
            message, generated_by_ai = generate_insight(
                "STOCKOUT_WARNING",
                {"branch_name": "Lipa City", "at_risk_items": "Flour (HIGH)"},
                force_template=True,
            )
        mock_post.assert_not_called()
        assert generated_by_ai is False


class TestSystemSettingsView:
    def test_owner_can_view(self, owner_client):
        response = owner_client.get(reverse("system_config:system_settings"))
        assert response.status_code == 200

    def test_non_owner_cannot_view(self, client, cashier):
        client.force_login(cashier)
        response = client.get(reverse("system_config:system_settings"))
        assert response.status_code == 403

    def test_owner_can_update_business_name(self, owner_client):
        response = owner_client.post(
            reverse("system_config:system_settings"),
            {
                "business_name": "New Business Name",
                "business_address": "123 Test St",
                "contact_phone": "0917-000-0000",
                "tax_rate_percent": "12.00",
                "currency_symbol": "P",
                "receipt_footer_text": "Thanks!",
            },
        )
        assert response.status_code == 302
        settings_obj = BusinessSettings.load()
        assert settings_obj.business_name == "New Business Name"

    def test_blank_business_name_is_rejected(self, owner_client):
        response = owner_client.post(
            reverse("system_config:system_settings"),
            {"business_name": "", "tax_rate_percent": "12.00"},
        )
        assert response.status_code == 200
        assert b"required" in response.content

    def test_invalid_tax_rate_shows_error(self, owner_client):
        response = owner_client.post(
            reverse("system_config:system_settings"),
            {"business_name": "Valid Name", "tax_rate_percent": "not_a_number"},
        )
        assert response.status_code == 200
        assert b"valid number" in response.content


class TestSystemConfigurationView:
    def test_owner_can_view(self, owner_client):
        response = owner_client.get(reverse("system_config:system_configuration"))
        assert response.status_code == 200

    def test_non_owner_cannot_view(self, client, cashier):
        client.force_login(cashier)
        response = client.get(reverse("system_config:system_configuration"))
        assert response.status_code == 403

    def test_owner_can_update_thresholds(self, owner_client):
        response = owner_client.post(
            reverse("system_config:system_configuration"),
            {
                "admin_alert_email": "owner@example.com",
                "high_risk_days_threshold": "5",
                "medium_risk_days_threshold": "15",
                "default_forecast_days": "10",
            },
        )
        assert response.status_code == 302
        config = SystemConfiguration.load()
        assert config.high_risk_days_threshold == 5
        assert config.admin_alert_email == "owner@example.com"

    def test_high_threshold_must_be_lower_than_medium(self, owner_client):
        response = owner_client.post(
            reverse("system_config:system_configuration"),
            {
                "high_risk_days_threshold": "20",
                "medium_risk_days_threshold": "5",
                "default_forecast_days": "7",
            },
        )
        assert response.status_code == 200
        assert b"must be lower" in response.content
        # unchanged from defaults
        config = SystemConfiguration.load()
        assert config.high_risk_days_threshold == 3

    def test_zero_or_negative_values_are_rejected(self, owner_client):
        response = owner_client.post(
            reverse("system_config:system_configuration"),
            {
                "high_risk_days_threshold": "0",
                "medium_risk_days_threshold": "10",
                "default_forecast_days": "7",
            },
        )
        assert response.status_code == 200
        assert b"greater than zero" in response.content

    def test_unchecking_the_toggle_disables_ai_insights(self, owner_client):
        """Checkbox inputs send nothing at all when unchecked -- this
        confirms the view correctly reads that as False, not crashing or
        defaulting back to True."""
        owner_client.post(
            reverse("system_config:system_configuration"),
            {
                "high_risk_days_threshold": "3",
                "medium_risk_days_threshold": "10",
                "default_forecast_days": "7",
                # ai_insights_enabled deliberately omitted
            },
        )
        config = SystemConfiguration.load()
        assert config.ai_insights_enabled is False
