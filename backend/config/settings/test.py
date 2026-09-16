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

# MD5 is intentionally weak/fast -- fine for tests, since test data never
# needs real security, but genuinely matters for suite speed. PBKDF2's
# real strength (Django's default, ~600k iterations) is deliberately
# slow, and this project's test suite creates many users (accounts) and
# now also many backup codes (Row 12.4, 10 PBKDF2 hashes per call) --
# a well-established Django testing convention, not a security compromise
# for production (staging/production settings never import from here).
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
