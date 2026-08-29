from django.urls import path

from apps.forecasting import views

app_name = "forecasting"

urlpatterns = [
    path("decision-support/", views.decision_support, name="decision_support"),
    path("forecast/", views.forecasting_dashboard, name="forecasting_dashboard"),
    path("resources/", views.resource_management_dashboard, name="resource_management_dashboard"),
]
