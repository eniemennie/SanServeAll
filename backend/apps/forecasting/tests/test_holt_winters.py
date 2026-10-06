"""
Direct unit tests for apps.forecasting.ml.holt_winters -- the hand-written
pure-numpy additive Holt-Winters implementation that replaced
statsmodels.tsa.holtwinters.ExponentialSmoothing (see forecast_model.py and
holt_winters.py's own module docstrings for why). These tests exercise the
module on its own, independent of forecast_model.py's naive-fallback
wrapping, since it's first-party math now and deserves direct coverage
rather than only being checked indirectly.
"""

import numpy as np
import pytest

from apps.forecasting.ml import holt_winters


class TestFit:
    def test_returns_expected_keys(self):
        y = np.tile([1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 2.0], 4)  # 4 clean weekly cycles
        result = holt_winters.fit(y, m=7)
        assert set(result.keys()) == {"level", "trend", "seasonal", "m"}
        assert result["m"] == 7
        assert len(result["seasonal"]) == 7

    def test_handles_a_perfectly_flat_series_without_crashing(self):
        # Every smoothing parameter and every seasonal index should
        # collapse to the same constant for a dead-flat series -- this is
        # the degenerate case most likely to divide by zero or blow up a
        # hand-written implementation.
        y = np.full(28, 5.0)
        result = holt_winters.fit(y, m=7)
        assert np.isfinite(result["level"])
        assert np.isfinite(result["trend"])
        assert all(np.isfinite(s) for s in result["seasonal"])

    def test_handles_a_series_with_real_zero_days(self):
        # The whole reason Holt-Winters is additive, not multiplicative,
        # in this project: genuine zero-sales days must not break fitting.
        y = np.tile([0.0, 0.0, 3.0, 0.0, 5.0, 0.0, 2.0], 4)
        result = holt_winters.fit(y, m=7)
        assert np.isfinite(result["level"])
        assert np.isfinite(result["trend"])


class TestForecast:
    def test_returns_requested_number_of_steps(self):
        y = np.tile([1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 2.0], 4)
        fit_result = holt_winters.fit(y, m=7)
        forecast = holt_winters.forecast(fit_result, steps=10)
        assert len(forecast) == 10

    def test_continues_a_strong_upward_trend(self):
        # A clearly increasing series (trend dominates any seasonal
        # noise) should forecast further increases, not flatten out or
        # reverse -- the core thing an additive trend component is for.
        rng = np.random.default_rng(7)
        days = np.arange(35)
        values = 5 + 2 * days + rng.normal(0, 0.5, 35)  # strong, steady growth
        fit_result = holt_winters.fit(values, m=7)
        forecast = holt_winters.forecast(fit_result, steps=7)
        assert forecast[-1] > forecast[0]

    def test_recovers_a_clean_weekly_seasonal_pattern(self):
        # A repeating 7-day pattern with no trend and no noise: the
        # forecast's own 7-day shape should resemble the input's shape
        # (high values stay relatively high, low values stay relatively
        # low), not flatten into a single repeated number.
        pattern = [1.0, 1.0, 2.0, 2.0, 2.0, 8.0, 9.0]  # weekend spike
        y = np.tile(pattern, 6)
        fit_result = holt_winters.fit(y, m=7)
        forecast = holt_winters.forecast(fit_result, steps=7)
        # The two "weekend" days should still forecast noticeably higher
        # than the weekday days, same relative shape as the input.
        weekday_avg = np.mean(forecast[:5])
        weekend_avg = np.mean(forecast[5:])
        assert weekend_avg > weekday_avg

    @pytest.mark.parametrize("steps", [1, 7, 14])
    def test_forecast_length_matches_steps_for_various_horizons(self, steps):
        y = np.tile([1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 2.0], 4)
        fit_result = holt_winters.fit(y, m=7)
        forecast = holt_winters.forecast(fit_result, steps=steps)
        assert len(forecast) == steps
