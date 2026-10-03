import os
from .base import *  # noqa

DEBUG = False
ALLOWED_HOSTS = os.environ.get("PRODUCTION_ALLOWED_HOSTS", "").split(",")

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
