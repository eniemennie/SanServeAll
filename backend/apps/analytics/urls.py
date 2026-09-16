from django.urls import path

from apps.analytics import views

app_name = "analytics"

urlpatterns = [
    path("sales/", views.sales_dashboard, name="sales_dashboard"),
    path("products/", views.product_performance, name="product_performance"),
    path("resources/", views.resource_consumption, name="resource_consumption"),
]
