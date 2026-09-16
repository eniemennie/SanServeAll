"""
Scheduled-job entrypoints registered in config/scheduler.py (Row 10.3,
Row 11.1, Row 11.2). This module is intentionally thin -- logging and
failure-alerting live here; the actual logic lives in services.py so it
can be tested directly without going through APScheduler at all.
"""

import logging

from django.core.mail import mail_admins, send_mail

logger = logging.getLogger(__name__)


def _send_failure_alert(subject, message):
    """Sends a job-failure alert to SystemConfiguration.admin_alert_email
    (Row 12.2) when one is configured; falls back to Django's ADMINS
    setting (mail_admins) otherwise. Never lets an alerting failure
    itself crash the job."""
    try:
        from apps.system_config.models import SystemConfiguration

        config = SystemConfiguration.load()
        if config.admin_alert_email:
            send_mail(
                subject=subject,
                message=message,
                from_email=None,  # uses DEFAULT_FROM_EMAIL
                recipient_list=[config.admin_alert_email],
            )
        else:
            mail_admins(subject=subject, message=message)
    except Exception:
        # If email isn't configured at all (e.g. local dev), don't let
        # the alerting mechanism itself crash the job.
        logger.exception("Failed to send failure-alert email")


def run_forecast_job():
    """The actual scheduled entry point. Wraps run_forecast_for_all_products
    with logging and an admin email alert on failure (Phase 3 revision:
    a silently-broken scheduled job going unnoticed for weeks is a real
    production risk once this is a live business system)."""
    from apps.forecasting.services import run_forecast_for_all_products
    from apps.system_config.models import SystemConfiguration

    try:
        config = SystemConfiguration.load()
        results = run_forecast_for_all_products(steps=config.default_forecast_days)
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
        _send_failure_alert(
            "SanServeAll: Forecast job failed",
            "The scheduled ARIMA forecast job raised an unhandled exception. Check logs.",
        )
        raise


def run_risk_classification_job():
    """Scheduled entry point for Row 11.1. Same logging/alerting pattern
    as run_forecast_job."""
    from apps.forecasting.services import run_risk_classification_for_all_inventory

    try:
        scores = run_risk_classification_for_all_inventory()
        high_risk_count = sum(1 for s in scores if s.risk_level == "HIGH")
        logger.info(
            "Risk classification job completed: %s items scored, %s high-risk",
            len(scores),
            high_risk_count,
        )
        return scores
    except Exception:
        logger.exception("Risk classification job failed entirely")
        _send_failure_alert(
            "SanServeAll: Risk classification job failed",
            "The scheduled inventory risk classification job raised an "
            "unhandled exception. Check logs.",
        )
        raise


def run_insight_generation_job():
    """Scheduled entry point for Row 11.2. Runs after risk classification
    (see config/scheduler.py job ordering) so it summarizes fresh data."""
    from apps.forecasting.services import generate_insights_for_all_branches

    try:
        insights = generate_insights_for_all_branches()
        ai_generated_count = sum(1 for i in insights if i.generated_by_ai)
        logger.info(
            "Insight generation job completed: %s insights, %s from live AI call",
            len(insights),
            ai_generated_count,
        )
        return insights
    except Exception:
        logger.exception("Insight generation job failed entirely")
        _send_failure_alert(
            "SanServeAll: Insight generation job failed",
            "The scheduled AI insight generation job raised an unhandled " "exception. Check logs.",
        )
        raise
