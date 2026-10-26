"""
Standalone CLI entry point for the nightly Holt-Winters forecast job
(Row 10.3's APScheduler job, now also reachable without a running web
worker).

Why this exists: the in-process APScheduler in config/scheduler.py only
runs while the Django web process itself is alive, which PythonAnywhere
does not guarantee at a fixed time of night (free-tier WSGI workers can
idle out and restart on the next request). This command lets
PythonAnywhere's own "Scheduled Tasks" feature -- a real, independent
cron -- call `python manage.py run_nightly_forecast` directly, so the
job runs on schedule regardless of web-worker uptime.

See deployment/production/notes.md and deployment/staging/notes.md for
the exact Scheduled Task setup (times are in UTC there, not local time).
"""

from django.core.management.base import BaseCommand

from apps.forecasting.jobs import run_forecast_job


class Command(BaseCommand):
    help = "Runs the nightly Holt-Winters forecast job (see apps/forecasting/jobs.py)."

    def handle(self, *args, **options):
        results = run_forecast_job()
        self.stdout.write(
            self.style.SUCCESS(
                f"Forecast job completed: {results['succeeded']} succeeded, "
                f"{len(results['failed'])} failed"
            )
        )
