"""Settings used only by the test suite — never touches dev/staging/prod data
or fires real scheduled jobs."""
from .development import *  # noqa

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
APSCHEDULER_AUTOSTART = False
