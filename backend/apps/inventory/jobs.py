"""
Scheduled-job entrypoints registered in config/scheduler.py. Mirrors
apps.forecasting.jobs's own pattern: this module stays thin (logging and
email-sending only), the real logic lives in services.py so it can be
tested directly without going through APScheduler at all.
"""

import logging

from django.core.mail import mail_admins, send_mail

logger = logging.getLogger(__name__)


def _send_alert_email(subject, message):
    """Sends to SystemConfiguration.admin_alert_email (Row 12.2) when one
    is configured; falls back to Django's ADMINS setting (mail_admins)
    otherwise. Same fallback pattern as
    apps.forecasting.jobs._send_failure_alert. Never lets an alerting
    failure itself crash the job."""
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
        logger.exception("Failed to send low-stock alert email")


def run_low_stock_alert_job():
    """Scheduled entry point (backend gap assessment item 20): low-stock
    detection was Fully Implemented but only ever evaluated on page
    load -- nothing proactively alerted anyone between visits to the
    Monitoring/Dashboard screens. Runs the same real per-branch detection
    (get_low_stock_alerts_for_all_branches, built on the same
    get_resource_status_summary the dashboard itself uses) and emails a
    summary when any branch has a critical or low-stock item.

    Sends nothing, and logs that fact, when every branch is adequately
    stocked -- an alert that fires on every run regardless of real
    status would quickly get ignored like a smoke detector that's
    always beeping.
    """
    from apps.inventory.services import get_low_stock_alerts_for_all_branches

    try:
        alerts = get_low_stock_alerts_for_all_branches()
        if not alerts:
            logger.info("Low-stock alert job: every branch is adequately stocked.")
            return alerts

        lines = [f"{len(alerts)} branch(es) need attention:", ""]
        for entry in alerts:
            lines.append(
                f"- {entry['branch'].name}: {entry['critical_count']} out of stock, "
                f"{entry['low_count']} low stock"
            )
        _send_alert_email("SanServeAll: Low stock alert", "\n".join(lines))
        logger.info("Low-stock alert job: sent alert for %s branch(es).", len(alerts))
        return alerts
    except Exception:
        logger.exception("Low-stock alert job failed entirely")
        _send_alert_email(
            "SanServeAll: Low-stock alert job failed",
            "The scheduled low-stock alert job raised an unhandled exception. Check logs.",
        )
        raise
