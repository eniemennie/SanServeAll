from django.urls import path

from apps.inventory import views

app_name = "inventory"

urlpatterns = [
    path("", views.inventory_monitoring, name="monitoring"),
    path("products/", views.product_management, name="product_management"),
    path("<int:inventory_id>/adjust/", views.adjust_stock, name="adjust_stock"),
]
