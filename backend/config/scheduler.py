"""
APScheduler bootstrap (Phase 3 revision: persistent, MySQL-backed job store
+ failure-alert emails, since scheduled jobs are production-critical).

Registered jobs (implemented in Week 10-11, see Phase 9):
  - apps.forecasting.jobs.run_forecast_job
  - apps.forecasting.jobs.run_risk_classification_job
  - apps.kahero_integration.jobs.run_scheduled_import_job

This file only wires the scheduler; it does not contain business logic.
"""
# TODO (Week 10): implement once apps/forecasting/jobs.py exists
# from apscheduler.schedulers.background import BackgroundScheduler
# from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
def start_scheduler():
    raise NotImplementedError("Scheduler wiring lands in Week 10 (Phase 9).")
