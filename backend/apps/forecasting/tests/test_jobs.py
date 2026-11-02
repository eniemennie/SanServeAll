"""
Tests for apps.forecasting.jobs's scheduled-job entrypoints.

run_forecast_job in particular closes out backend gap assessment item
23 ("Default Forecast Days" setting saved but never read): this test
locks in that SystemConfiguration.default_forecast_days actually
reaches run_forecast_for_all_products as its `steps` argument, so a
future refactor can't silently re-introduce the hardcoded default
without a test failing.
"""

from unittest.mock import patch

import pytest

from apps.forecasting.jobs import run_forecast_job
from apps.system_config.models import SystemConfiguration

pytestmark = pytest.mark.django_db


class TestRunForecastJob:
    def test_uses_the_configured_default_forecast_days_as_the_step_count(self):
        config = SystemConfiguration.load()
        config.default_forecast_days = 14
        config.save()

        with patch("apps.forecasting.services.run_forecast_for_all_products") as mock_run:
            mock_run.return_value = {"succeeded": 0, "failed": []}
            run_forecast_job()

        mock_run.assert_called_once_with(steps=14)

    def test_falls_back_to_the_field_default_when_unconfigured(self):
        config = SystemConfiguration.load()
        assert config.default_forecast_days == 7  # the model field's own default

        with patch("apps.forecasting.services.run_forecast_for_all_products") as mock_run:
            mock_run.return_value = {"succeeded": 0, "failed": []}
            run_forecast_job()

        mock_run.assert_called_once_with(steps=7)
