"""
Root URL configuration for SanServeAll.
Each domain app owns its own urls.py; this file only mounts them.
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("pos/", include("apps.pos.urls")),
    path("inventory/", include("apps.inventory.urls")),
    path("kahero/", include("apps.kahero_integration.urls")),
    path("production/", include("apps.production.urls")),
    path("analytics/", include("apps.analytics.urls")),
    path("forecasting/", include("apps.forecasting.urls")),
    path("system-config/", include("apps.system_config.urls")),
]

# django-debug-toolbar (development.py-only app) requires its own URLs to
# be registered, or any page it tries to render on top of raises
# NoReverseMatch ('djdt' is not a registered namespace). Only wired in when
# DEBUG is on and the app is actually installed, so this line is a no-op
# for staging/production settings, which never enable debug_toolbar.
if settings.DEBUG and "debug_toolbar" in settings.INSTALLED_APPS:
    import debug_toolbar

    urlpatterns += [path("__debug__/", include(debug_toolbar.urls))]
