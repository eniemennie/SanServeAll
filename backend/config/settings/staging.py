import os
from pathlib import Path

from .base import *  # noqa

DEBUG = False
ALLOWED_HOSTS = os.environ.get("STAGING_ALLOWED_HOSTS", "").split(",")

# SQLite, not MySQL -- same deployment-driven reasoning as production.py
# (see that file's module-level comment for the full explanation). A
# separate file from production's so staging and production never
# accidentally share one database file if they're ever run on the same
# machine.
_default_sqlite_path = Path.home() / "data" / "sanserveall_staging.sqlite3"
_sqlite_path = Path(os.environ.get("SQLITE_DB_PATH", str(_default_sqlite_path)))
_sqlite_path.parent.mkdir(parents=True, exist_ok=True)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(_sqlite_path),
        "OPTIONS": {
            "init_command": "PRAGMA journal_mode=WAL;",
            "timeout": 20,
        },
    }
}

MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")  # noqa: F405

# Same reasoning as production.py's CSRF_TRUSTED_ORIGINS -- without this,
# every POST form submission gets a CSRF 403 once DEBUG=False and the
# site is reached over HTTPS, which it will be on any real host.
CSRF_TRUSTED_ORIGINS = [f"https://{host}" for host in ALLOWED_HOSTS if host]
