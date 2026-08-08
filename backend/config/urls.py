"""
Root URL configuration for SanServeAll.
Each domain app owns its own urls.py; this file only mounts them.
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    # path("api/v1/accounts/", include("apps.accounts.urls")),
    # path("api/v1/pos/", include("apps.pos.urls")),
    # path("api/v1/inventory/", include("apps.inventory.urls")),
    # path("api/v1/production/", include("apps.production.urls")),
    # path("api/v1/kahero/", include("apps.kahero_integration.urls")),
    # path("api/v1/analytics/", include("apps.analytics.urls")),
    # path("api/v1/forecasting/", include("apps.forecasting.urls")),
    # TODO: uncomment each line as that app's urls.py is implemented (see Phase 9 timeline)
]
