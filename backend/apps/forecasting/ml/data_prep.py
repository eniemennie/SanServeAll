"""
Data preparation for ARIMA forecasting (Row 10.1). Pure pandas/numpy work
here -- no Django ORM writes, no model-fitting -- this module's only job
is turning raw sales rows into a clean, regularly-spaced time series that
statsmodels can actually work with.
"""

import pandas as pd
from django.utils import timezone

from apps.pos.models import SalesItem, SalesTransaction


def build_daily_sales_series(branch, product, days_history=90):
    """Returns a pandas Series indexed by date (daily frequency), values
    are total units of `product` sold at `branch` that day.

    The window covers complete days only -- up through YESTERDAY, not
    including today, since today isn't a finished day yet and would
    understate demand if treated as a normal data point. All boundaries
    are normalized to midnight and kept timezone-aware throughout, so the
    DB filter and the reindex target line up exactly -- a mismatch here
    would silently zero out every real data point on reindex.

    Days with ZERO sales are filled in explicitly -- ARIMA needs a
    regular, gap-free time index. A day with no sales is a real data
    point (demand was zero that day), not a missing one.
    """
    today_midnight = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = today_midnight  # exclusive upper bound
    start_date = end_date - pd.Timedelta(days=days_history)
    full_date_index = pd.date_range(start=start_date, end=end_date - pd.Timedelta(days=1), freq="D")

    items = SalesItem.objects.filter(
        transaction__branch=branch,
        transaction__status=SalesTransaction.Status.COMPLETED,
        transaction__completed_at__gte=start_date,
        transaction__completed_at__lt=end_date,
        product=product,
    ).values_list("transaction__completed_at", "quantity")

    if not items:
        return pd.Series(0.0, index=full_date_index)

    df = pd.DataFrame(items, columns=["completed_at", "quantity"])
    df["date"] = pd.to_datetime(df["completed_at"]).dt.normalize()
    daily_totals = df.groupby("date")["quantity"].sum()

    series = daily_totals.reindex(full_date_index, fill_value=0.0).astype(float)
    return series


def has_sufficient_history(series, minimum_days=14):
    """ARIMA can technically run on very short series, but the result is
    not meaningful -- this is the single place that decision is made, so
    arima_model.py doesn't need its own opinion about what "enough data"
    means."""
    non_zero_days = int((series != 0).sum())
    return len(series) >= minimum_days and non_zero_days >= 3
