"""
Pure NumPy additive Holt-Winters (triple exponential smoothing).

Why this exists instead of statsmodels.tsa.holtwinters.ExponentialSmoothing:
statsmodels' implementation is correct and was the original choice (see
forecast_model.py's module docstring for the ARIMA -> Holt-Winters switch),
but it pulls in statsmodels AND scipy as a transitive dependency purely for
its parameter-fitting optimizer. On PythonAnywhere's free tier (512MB total
disk quota), scipy + scipy.libs + statsmodels alone cost ~168MB -- more
than a third of the entire account quota -- for a single function call.

This module reimplements the same algorithm (additive trend, additive
seasonal Holt-Winters, matching forecast_model.py's TREND="add",
SEASONAL="add" choice and its reasoning about zero-sales days) using only
numpy, which the project already depends on directly. The only real
difference from the statsmodels version is HOW the smoothing parameters
(alpha, beta, gamma) are chosen: statsmodels uses a continuous gradient-based
optimizer (via scipy.optimize); this module uses a coarse grid search
minimizing the same in-sample sum-of-squared-errors objective. That's a
real simplification -- flagged here rather than silently matched -- but a
reasonable one for daily retail demand at this scale, and it keeps the
college capstone's own forecasting formula first-party and auditable rather
than hidden inside a third-party optimizer.
"""

import numpy as np

# Coarse grid over the valid [0, 1) range for each smoothing parameter.
# 5 values per parameter x 3 parameters = 125 combinations to fit per
# forecast call -- cheap even on modest hardware, and plenty fine-grained
# for daily demand data that isn't being hand-tuned per product anyway
# (same "reasonable general-purpose default" spirit as the statsmodels
# version this replaces).
_PARAM_GRID = np.arange(0.1, 1.0, 0.2)


def _initial_components(y, m):
    """Classic Holt-Winters initialization from the first two full
    seasonal cycles (Hyndman & Athanasopoulos' standard textbook method):
    initial level is the first season's average, initial trend is the
    per-period change between the first two seasons' averages, and
    initial seasonal indices are the first-two-seasons' values after
    removing that level/trend, averaged by position within the season
    and centered to sum to zero (required for an additive seasonal
    component -- otherwise the seasonal indices would bias the level).
    """
    two_seasons = 2 * m
    season_avgs = []
    for i in range(2):
        start = i * m
        end = start + m
        season_avgs.append(np.mean(y[start:end]))
    level0 = float(season_avgs[0])
    trend0 = float((season_avgs[1] - season_avgs[0]) / m)

    detrended = y[:two_seasons] - (level0 + trend0 * np.arange(two_seasons))
    seasonal0 = np.array([np.mean(detrended[i::m]) for i in range(m)])
    seasonal0 = seasonal0 - np.mean(seasonal0)
    return level0, trend0, seasonal0


def _fit_with_params(y, alpha, beta, gamma, m):
    """Runs the additive Holt-Winters recurrence once for a given
    (alpha, beta, gamma), returning the in-sample sum-of-squared one-step
    -ahead errors (the fitting objective) plus the final level/trend/
    seasonal state needed to forecast forward from the end of the series.
    """
    level, trend, seasonal = _initial_components(y, m)
    seasonal = list(seasonal)

    sse = 0.0
    n = len(y)
    for t in range(n):
        s_idx = t % m
        s_lag = seasonal[s_idx]

        # One-step-ahead prediction using the *previous* state -- this is
        # the actual fitting objective: how well would this parameter
        # choice have predicted each real observation in sequence.
        yhat = level + trend + s_lag
        sse += (y[t] - yhat) ** 2

        level_new = alpha * (y[t] - s_lag) + (1 - alpha) * (level + trend)
        trend_new = beta * (level_new - level) + (1 - beta) * trend
        seasonal[s_idx] = gamma * (y[t] - level_new) + (1 - gamma) * s_lag

        level, trend = level_new, trend_new

    return sse, level, trend, seasonal


def fit(y, m=7):
    """Fits additive Holt-Winters to `y` (a 1D numpy array of at least
    2*m observations -- callers are expected to have already checked
    this via has_sufficient_history/_has_enough_for_seasonal_fit, same as
    the statsmodels version this replaces). Picks (alpha, beta, gamma) by
    grid search over the SSE objective, then returns the fitted state
    needed to forecast forward.
    """
    y = np.asarray(y, dtype=float)
    best = None
    for alpha in _PARAM_GRID:
        for beta in _PARAM_GRID:
            for gamma in _PARAM_GRID:
                sse, level, trend, seasonal = _fit_with_params(y, alpha, beta, gamma, m)
                if best is None or sse < best[0]:
                    best = (sse, level, trend, seasonal)

    _, level, trend, seasonal = best
    return {"level": level, "trend": trend, "seasonal": seasonal, "m": m}


def forecast(fit_result, steps):
    """Projects `steps` periods forward from a fitted state: level grows
    linearly by `trend` each step (the additive-trend assumption), with
    the appropriate seasonal index for how far into the next cycle each
    step falls added on top.
    """
    level = fit_result["level"]
    trend = fit_result["trend"]
    seasonal = fit_result["seasonal"]
    m = fit_result["m"]

    return [level + h * trend + seasonal[(h - 1) % m] for h in range(1, steps + 1)]
