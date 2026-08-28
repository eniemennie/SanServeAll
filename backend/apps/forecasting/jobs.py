"""
Scheduled-job entrypoints registered in config/scheduler.py (Row 10.3).
This module is intentionally thin -- logging and failure-alerting live
here; the actual forecasting logic lives in services.py so it can be
tested directly without going through APScheduler at all.
"""

import logging

from django.core.mail import mail_admins

logger = logging.getLogger(__name__)


def run_forecast_job():
    """The actual scheduled entry point. Wraps run_forecast_for_all_products
    with logging and an admin email alert on failure (Phase 3 revision:
    a silently-broken scheduled job going unnoticed for weeks is a real
    production risk once this is a live business system)."""
    from apps.forecasting.services import run_forecast_for_all_products

    try:
        results = run_forecast_for_all_products()
        logger.info(
            "Forecast job completed: %s succeeded, %s failed",
            results["succeeded"],
            len(results["failed"]),
        )
        if results["failed"]:
            logger.warning("Forecast job had failures: %s", results["failed"])
        return results
    except Exception:
        logger.exception("Forecast job failed entirely")
        try:
            mail_admins(
                subject="SanServeAll: Forecast job failed",
                message=(
                    "The scheduled ARIMA forecast job raised an unhandled " "exception. Check logs."
                ),
            )
        except Exception:
            # If email isn't configured (e.g. local dev), don't let the
            # alerting mechanism itself crash the job.
            logger.exception("Also failed to send failure-alert email")
        raise
