from django.urls import path

from apps.purchasing import views

app_name = "purchasing"

urlpatterns = [
    path("suppliers/", views.supplier_management, name="supplier_management"),
    path(
        "suppliers/<int:supplier_id>/toggle-active/",
        views.supplier_toggle_active,
        name="supplier_toggle_active",
    ),
]
