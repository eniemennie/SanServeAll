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
        assert set(result.keys()) == {"level", "trend", "seasonal", "m", "n"}
        assert result["m"] == 7
        assert result["n"] == len(y)
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

    def test_continues_the_seasonal_cycle_from_where_the_data_actually_ended(self):
        """Regression test for the phase-alignment bug found during the
        pre-adviser-meeting verification pass (2026-11-04): every prior
        test here used np.tile(pattern, k) -- always an exact multiple
        of m=7 -- which hid a bug where forecast() unconditionally
        restarted the weekly cycle at phase 0 on the first forecast
        step, no matter which day of the week the fitted series actually
        ended on. Production always fits a fixed 90-day window
        (build_daily_sales_series), and 90 % 7 == 6, not 0, so this was
        live on every real forecast, not just a theoretical edge case.

        Uses a 90-day series (production's real window length) built
        from a clean, no-trend weekly pattern with one standout day, so
        the correct forecast is unambiguous: it must continue rotating
        the same pattern from the day immediately after the last
        observed one, not jump back to the pattern's first entry.
        """
        pattern = [1.0, 1.0, 2.0, 2.0, 2.0, 2.0, 9.0]  # one standout day (index 6)
        y = np.tile(pattern, 13)[:90]  # 90 days, same as build_daily_sales_series
        fit_result = holt_winters.fit(y, m=7)
        forecast = holt_winters.forecast(fit_result, steps=7)

        # len(y) == 90, so the first forecast step continues at phase
        # 90 % 7 == 6 -- i.e. the standout day should land FIRST in the
        # forecast, not last (which is what the old, buggy formula would
        # have produced for this exact series).
        assert forecast[0] == max(forecast)
        assert forecast[0] > forecast[1]

    def test_phase_alignment_formula_reduces_to_the_original_when_n_is_a_multiple_of_m(self):
        """When len(y) IS an exact multiple of m (every pre-existing
        test's case), the fixed formula must produce exactly the same
        result as before -- confirming the fix is additive, not a
        behavior change for the cases that were already passing."""
        y = np.tile([1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 2.0], 4)  # 28 days, 28 % 7 == 0
        fit_result = holt_winters.fit(y, m=7)
        forecast = holt_winters.forecast(fit_result, steps=7)
        # With n % m == 0, step h=1 must align to seasonal[0], same as
        # the original (unfixed) formula would have given.
        assert forecast[0] == pytest.approx(
            fit_result["level"] + 1 * fit_result["trend"] + fit_result["seasonal"][0]
        )
