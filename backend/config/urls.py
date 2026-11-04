"""
Root URL configuration for SanServeAll.
Each domain app owns its own urls.py; this file only mounts them.
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django_otp.admin import OTPAdminSite

# Backend gap assessment: django_otp was already enforcing 2FA on the
# app's own custom Owner/Admin dashboard (via @otp_required in
# apps.accounts.views), but Django's built-in /admin/ site -- reachable
# by any is_staff account with just a password -- had no such check at
# all. Retrofitting the default AdminSite's class (rather than
# registering a second, separate OTPAdminSite instance) means every
# existing @admin.register() call across every app keeps working
# unchanged, while login to /admin/ itself now also requires a
# verified OTP device.
#
# Operational note: a staff/superuser account needs a confirmed TOTP
# device before it can use this. The self-service path is the existing
# one: log in through /accounts/admin/login/ (requires role=OWNER_ADMIN)
# and complete /accounts/admin/2fa-setup/ once -- after that, the same
# device verifies here too.
admin.site.__class__ = OTPAdminSite

urlpatterns = [
    path("", include("apps.core.urls")),
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("pos/", include("apps.pos.urls")),
    path("inventory/", include("apps.inventory.urls")),
    path("kahero/", include("apps.kahero_integration.urls")),
    path("production/", include("apps.production.urls")),
    path("analytics/", include("apps.analytics.urls")),
    path("forecasting/", include("apps.forecasting.urls")),
    path("system-config/", include("apps.system_config.urls")),
    path("purchasing/", include("apps.purchasing.urls")),
]

# django-debug-toolbar (development.py-only app) requires its own URLs to
# be registered, or any page it tries to render on top of raises
# NoReverseMatch ('djdt' is not a registered namespace). Only wired in when
# DEBUG is on and the app is actually installed, so this line is a no-op
# for staging/production settings, which never enable debug_toolbar.
if settings.DEBUG and "debug_toolbar" in settings.INSTALLED_APPS:
    import debug_toolbar

    urlpatterns += [path("__debug__/", include(debug_toolbar.urls))]
