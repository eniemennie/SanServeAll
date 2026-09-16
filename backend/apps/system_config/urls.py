from django.urls import path

from apps.system_config import views

app_name = "system_config"

urlpatterns = [
    path("settings/", views.system_settings, name="system_settings"),
    path("configuration/", views.system_configuration, name="system_configuration"),
]
