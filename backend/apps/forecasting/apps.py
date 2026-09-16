import os
import sys

from django.apps import AppConfig
from django.conf import settings


class ForecastingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.forecasting"

    def ready(self):
        # Primary gate: config/settings/test.py sets this False, so
        # pytest never starts the scheduler regardless of anything below
        # -- this is what actually protects the test suite.
        if not getattr(settings, "APSCHEDULER_AUTOSTART", False):
            return

        # Secondary gate: under development/staging/production settings,
        # AppConfig.ready() also fires for one-off management commands
        # (migrate, shell, makemigrations) -- only start for `runserver`
        # itself, not those. sys.argv is empty/irrelevant under a real
        # WSGI production server, so this check is a no-op there.
        if len(sys.argv) > 1 and sys.argv[1] != "runserver":
            return

        # Under `runserver`, Django's autoreloader spawns a child worker
        # process with RUN_MAIN=true; without this check the reloader's
        # own watcher process would start a duplicate scheduler too.
        if (
            len(sys.argv) > 1
            and sys.argv[1] == "runserver"
            and os.environ.get("RUN_MAIN") != "true"
        ):
            return

        from config.scheduler import start_scheduler

        start_scheduler()
