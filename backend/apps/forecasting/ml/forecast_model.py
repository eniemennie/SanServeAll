"""
Holt-Winters Exponential Smoothing time-series forecasting (Row 10.2).
Replaces the earlier ARIMA(1,1,1) model -- see the module docstring below
for why. Wraps statsmodels so the rest of the app deals with a simple
"give me N days of predictions" interface, not the fitting/forecasting
API directly. No new dependency: statsmodels already ships
ExponentialSmoothing alongside ARIMA.

Why Holt-Winters over ARIMA: the cafe's own daily sales genuinely show
weekly seasonality (weekend spikes -- see the Weekly Demand Pattern
chart on the Demand Forecasting screen, which plots exactly this).
ARIMA(1,1,1) never explicitly modeled that weekly cycle; Holt-Winters'
seasonal component does, directly.

Additive (not multiplicative) trend and seasonal components: real sales
data includes genuine zero-sales days, and multiplicative decomposition
divides by the seasonal/trend level, which is undefined (or requires
special-casing) at zero. Additive has no such restriction.
"""

import warnings

import numpy as np
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from apps.forecasting.ml.data_prep import has_sufficient_history

# Weekly seasonality (7-day cycle), matching the cafe's own observed
# demand pattern. Additive trend + seasonal, no damping -- a reasonable
# general-purpose default for daily retail sales without hand-tuning per
# product, same spirit as the ARIMA(1,1,1) default it replaces.
SEASONAL_PERIODS = 7
TREND = "add"
SEASONAL = "add"

# Holt-Winters' seasonal component needs at least 2 full seasonal cycles
# to estimate reliably -- below this, statsmodels either refuses to fit
# or produces an unreliable seasonal estimate. has_sufficient_history's
# own 14-day floor already happens to satisfy this (2 x 7), which is
# convenient but not a coincidence worth relying on if SEASONAL_PERIODS
# ever changes -- this constant exists so the two stay connected on
# purpose, not by accident.
MINIMUM_SEASONAL_CYCLES = 2


def _naive_forecast(series, steps):
    """Fallback when there isn't enough history to fit Holt-Winters
    meaningfully -- the average of the last 14 days (or however much
    history exists), repeated forward. Simple, honest, and clearly
    labeled as such rather than dressing up a low-confidence guess as a
    real model result."""
    recent_window = series.tail(min(14, len(series)))
    average = float(recent_window.mean()) if len(recent_window) else 0.0
    return [round(average, 2)] * steps


def _has_enough_for_seasonal_fit(series):
    """Holt-Winters with a 7-day seasonal period needs at least
    MINIMUM_SEASONAL_CYCLES x SEASONAL_PERIODS real data points to
    estimate the seasonal component at all -- fitting on less either
    raises inside statsmodels or silently produces a degenerate
    seasonal estimate. Checked explicitly here rather than letting
    statsmodels fail unpredictably."""
    return len(series) >= MINIMUM_SEASONAL_CYCLES * SEASONAL_PERIODS


def _fit_holtwinters(series):
    with warnings.catch_warnings():
        # statsmodels emits routine convergence warnings on short or
        # unusual series that don't indicate a real problem -- silenced
        # here rather than left to alarm whoever reads the logs.
        warnings.simplefilter("ignore")
        model = ExponentialSmoothing(
            series,
            trend=TREND,
            seasonal=SEASONAL,
            seasonal_periods=SEASONAL_PERIODS,
            initialization_method="estimated",
        )
        return model.fit()


def _compute_holdout_mae(series, holdout_days=7):
    """Fits on all but the last `holdout_days`, forecasts that many steps
    ahead, and compares against what actually happened -- a genuine
    accuracy check against real held-out data, not a number invented
    after the fact.

    Returns None when there isn't enough history to hold anything out
    without starving the fit -- an honest "we don't know yet" rather
    than a fabricated score.
    """
    if len(series) < holdout_days * 3:
        return None

    train = series.iloc[:-holdout_days]
    actual_holdout = series.iloc[-holdout_days:]

    if not has_sufficient_history(train) or not _has_enough_for_seasonal_fit(train):
        return None

    try:
        fitted = _fit_holtwinters(train)
        predicted = fitted.forecast(steps=holdout_days)
        mae = float(np.mean(np.abs(predicted.values - actual_holdout.values)))
        return round(mae, 2)
    except Exception:
        # A holdout-validation failure shouldn't block the real forecast
        # from being generated -- it just means we report no MAE for it.
        return None


def generate_forecast(series, steps=7):
    """Produces `steps` days of forecasted demand from a daily sales
    series (see data_prep.build_daily_sales_series).

    Returns a dict: {"predicted_values": [...], "model_used": str,
    "mae": float | None}. Falls back to a naive average when there isn't
    enough history for Holt-Winters to fit meaningfully (either the
    general data-sufficiency floor, or specifically not enough for the
    seasonal component), rather than forcing a sophisticated-looking
    model onto data that can't support one.
    """
    if not has_sufficient_history(series) or not _has_enough_for_seasonal_fit(series):
        return {
            "predicted_values": _naive_forecast(series, steps),
            "model_used": "NAIVE_AVERAGE",
            "mae": None,
        }

    mae = _compute_holdout_mae(series)

    try:
        fitted = _fit_holtwinters(series)
        forecast_result = fitted.forecast(steps=steps)
        predicted_values = [round(float(v), 2) for v in forecast_result.values]
        # Holt-Winters can occasionally predict negative demand on a
        # noisy/short series, which is meaningless for physical unit
        # sales -- clamped to zero rather than reported as-is.
        predicted_values = [max(0.0, v) for v in predicted_values]
        model_used = f"HoltWinters(seasonal={SEASONAL_PERIODS})"
    except Exception:
        # A genuine fitting failure (e.g. a pathological series
        # Holt-Winters can't converge on) falls back to the same naive
        # method rather than propagating an exception into the
        # scheduled job.
        predicted_values = _naive_forecast(series, steps)
        model_used = "NAIVE_AVERAGE"
        mae = None

    return {"predicted_values": predicted_values, "model_used": model_used, "mae": mae}
