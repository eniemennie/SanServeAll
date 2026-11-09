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
    path(
        "suppliers/<int:supplier_id>/edit/",
        views.supplier_edit,
        name="supplier_edit",
    ),
    path("orders/", views.purchase_order_list, name="purchase_order_list"),
    path("orders/<int:order_id>/", views.purchase_order_detail, name="purchase_order_detail"),
    path(
        "orders/<int:order_id>/mark-ordered/",
        views.purchase_order_mark_ordered,
        name="purchase_order_mark_ordered",
    ),
    path(
        "orders/<int:order_id>/receive/",
        views.purchase_order_receive,
        name="purchase_order_receive",
    ),
    path(
        "orders/<int:order_id>/cancel/",
        views.purchase_order_cancel,
        name="purchase_order_cancel",
    ),
]
