"""
APScheduler bootstrap (Row 10.3 / Phase 3 revision): a persistent job
store means scheduled jobs survive a server restart -- an in-memory-only
scheduler would silently drop any pending job the moment the process
restarts, which is a real risk once this is a live production system.

Deliberately NOT using Celery+Redis (Phase 3 decision) -- at this scale
(a handful of daily jobs), that's infrastructure this project doesn't
need yet, and is documented as the upgrade path if job volume grows.
"""

import logging

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from django.conf import settings

logger = logging.getLogger(__name__)

_scheduler = None


def _build_sqlalchemy_url():
    """Points APScheduler's job store at the SAME database Django uses,
    via Django's own DATABASES config -- one database to operate and
    back up, not a second one just for job persistence."""
    db_config = settings.DATABASES["default"]
    engine = db_config["ENGINE"]

    if "sqlite3" in engine:
        return f"sqlite:///{db_config['NAME']}"
    if "mysql" in engine:
        return (
            f"mysql+mysqldb://{db_config['USER']}:{db_config['PASSWORD']}"
            f"@{db_config.get('HOST', 'localhost')}:{db_config.get('PORT', 3306)}"
            f"/{db_config['NAME']}"
        )
    raise ValueError(f"Unsupported database engine for scheduler job store: {engine}")


def start_scheduler():
    """Starts the background scheduler and registers all scheduled jobs.
    Safe to call more than once -- returns the existing scheduler instance
    if one is already running, rather than starting a second one."""
    global _scheduler

    if _scheduler is not None and _scheduler.running:
        return _scheduler

    jobstores = {"default": SQLAlchemyJobStore(url=_build_sqlalchemy_url())}
    _scheduler = BackgroundScheduler(jobstores=jobstores, timezone=str(settings.TIME_ZONE))

    from apps.forecasting.jobs import (
        run_forecast_job,
        run_insight_generation_job,
        run_risk_classification_job,
    )

    _scheduler.add_job(
        run_forecast_job,
        trigger="cron",
        hour=2,
        minute=0,
        id="nightly_forecast",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    _scheduler.add_job(
        run_risk_classification_job,
        trigger="cron",
        hour=2,
        minute=30,  # after the forecast job, so risk scoring can use fresh demand data
        id="nightly_risk_classification",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    _scheduler.add_job(
        run_insight_generation_job,
        trigger="cron",
        hour=3,
        minute=0,  # after risk classification, so insights summarize fresh risk scores
        id="nightly_insight_generation",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    _scheduler.start()
    logger.info("APScheduler started with persistent job store.")
    return _scheduler


def shutdown_scheduler():
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
