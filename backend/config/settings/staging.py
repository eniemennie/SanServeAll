import os
from .base import *  # noqa

DEBUG = False
ALLOWED_HOSTS = os.environ.get("STAGING_ALLOWED_HOSTS", "").split(",")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.environ["DB_NAME"],
        "USER": os.environ["DB_USER"],
        "PASSWORD": os.environ["DB_PASSWORD"],
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "3306"),
    }
}

MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")  # noqa: F405

# Same reasoning as production.py's CSRF_TRUSTED_ORIGINS -- without this,
# every POST form submission gets a CSRF 403 once DEBUG=False and the
# site is reached over HTTPS, which it will be on any real host.
CSRF_TRUSTED_ORIGINS = [f"https://{host}" for host in ALLOWED_HOSTS if host]
