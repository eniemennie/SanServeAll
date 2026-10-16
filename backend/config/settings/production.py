import os
from pathlib import Path

from .base import *  # noqa

DEBUG = False
ALLOWED_HOSTS = os.environ.get("PRODUCTION_ALLOWED_HOSTS", "").split(",")

# SQLite, not MySQL -- a deployment-driven change, not a design change.
# PythonAnywhere stopped including a free MySQL/Postgres database for
# accounts created from 2026-01-15 onward (confirmed directly against
# this account's own Databases page); the only way to get a real managed
# database back is a paid plan. The alternative -- an external free
# database (e.g. Aiven) paired with a free app host (e.g. Render) -- was
# considered and rejected: PythonAnywhere's free-tier network sandboxing
# only proxies HTTP(S), so it can't reach an external MySQL server on
# port 3306 at all, and moving the app itself to a host that could still
# leaves two independently-sleeping free services instead of one, with a
# database that must be woken manually (not automatically, unlike a
# sleeping web app) -- a real risk of the system being down with no
# warning on the one day it matters most. SQLite needs no second service
# to keep alive and never sleeps.
#
# This is a documented capstone team budget constraint, same spirit as
# the ARIMA -> Holt-Winters and Claude -> Gemini swaps -- see the
# manuscript's deployment-constraints note for the full reasoning.
#
# The database file lives OUTSIDE the git-managed project directory
# (SQLITE_DB_PATH, defaulting to ~/data/sanserveall_production.sqlite3)
# so a `git pull` or any repo-level operation on the server can never
# touch real data -- unlike backend/db.sqlite3 (gitignored, but still
# inside the repo folder), which is fine for local dev/test but not
# something worth risking in a deployment script's path.
_default_sqlite_path = Path.home() / "data" / "sanserveall_production.sqlite3"
_sqlite_path = Path(os.environ.get("SQLITE_DB_PATH", str(_default_sqlite_path)))
# SQLite won't create its own parent directory -- without this, the very
# first request after a fresh deploy fails with "unable to open database
# file" rather than just creating it, which is a confusing first error
# to hit on a brand-new server.
_sqlite_path.parent.mkdir(parents=True, exist_ok=True)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(_sqlite_path),
        "OPTIONS": {
            # WAL mode lets reads proceed while a write is in progress
            # (the scheduler's nightly jobs vs. normal page views)
            # instead of blocking each other -- SQLite's default
            # rollback-journal mode locks the whole database for any
            # write.
            "init_command": "PRAGMA journal_mode=WAL;",
            # Django's sqlite3 backend defaults to a 5-second busy
            # timeout -- bumped here since this now serves real
            # concurrent traffic (POS, the scheduler, dashboard views),
            # not just a single local dev server.
            "timeout": 20,
        },
    }
}

MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")  # noqa: F405

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000

# PythonAnywhere (and virtually every PaaS) terminates HTTPS at their edge
# and forwards requests internally over plain HTTP -- without this,
# Django sees every request as insecure and SECURE_SSL_REDIRECT above
# causes an infinite redirect loop (secure request comes in -> proxy
# forwards as HTTP -> Django redirects to HTTPS -> proxy terminates and
# forwards as HTTP again -> repeat). This tells Django to trust the
# X-Forwarded-Proto header the proxy sets instead of only trusting its
# own (always-HTTP-internally) connection.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# CSRF protection needs to know which real, HTTPS origins are allowed to
# submit forms here -- without this, every POST (login, POS checkout,
# Add Product, Settings saves) gets a confusing CSRF 403 once DEBUG=False,
# since Django won't implicitly trust an HTTPS origin it wasn't told
# about. Built from the same ALLOWED_HOSTS env var above rather than a
# second variable to keep in sync.
CSRF_TRUSTED_ORIGINS = [f"https://{host}" for host in ALLOWED_HOSTS if host]
