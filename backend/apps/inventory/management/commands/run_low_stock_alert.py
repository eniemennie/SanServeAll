"""
Standalone CLI entry point for the daily low-stock alert job (see
apps.inventory.jobs.run_low_stock_alert_job). See
apps/forecasting/management/commands/run_nightly_forecast.py's module
docstring for why this command exists -- same reasoning, same
deployment story: config/scheduler.py's in-process APScheduler only
runs while the web worker is alive, which PythonAnywhere's free tier
does not guarantee at a fixed time of day, so this lets PythonAnywhere's
own "Scheduled Tasks" feature call `python manage.py run_low_stock_alert`
directly instead.

This command was missing even though the job itself (daily_low_stock_alert,
registered at 06:00 local time in config/scheduler.py) has existed since
the low-stock alerting feature shipped -- the other three nightly/daily
jobs (forecast, risk classification, insight generation) each already had
their own standalone command, but this one did not. That gap matters here
specifically: deployment/production/notes.md's migration plan is to stand
up PythonAnywhere Scheduled Tasks for the jobs that have standalone
commands, then set APSCHEDULER_AUTOSTART=false so the unreliable in-process
scheduler stops running. Followed literally with this command missing, that
would have silently turned off the low-stock alert job entirely -- it
would have had no way to run at all once the in-process scheduler was
disabled.

See deployment/production/notes.md for the exact Scheduled Task setup
(times are in UTC there, not local time).
"""

from django.core.management.base import BaseCommand

from apps.inventory.jobs import run_low_stock_alert_job


class Command(BaseCommand):
    help = "Runs the daily low-stock alert job (see apps/inventory/jobs.py)."

    def handle(self, *args, **options):
        alerts = run_low_stock_alert_job()
        if not alerts:
            self.stdout.write(
                self.style.SUCCESS(
                    "Low-stock alert job completed: every branch adequately stocked."
                )
            )
            return
        self.stdout.write(
            self.style.SUCCESS(f"Low-stock alert job completed: {len(alerts)} branch(es) alerted.")
        )
