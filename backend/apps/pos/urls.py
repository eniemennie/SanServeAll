from django.urls import path

from apps.pos import views

app_name = "pos"

urlpatterns = [
    path("", views.pos_ordering, name="ordering"),
    path("add-catalog-item/", views.add_catalog_item, name="add_catalog_item"),
    path("add-customized-item/", views.add_customized_item, name="add_customized_item"),
    path("add-custom-product/", views.add_custom_product, name="add_custom_product"),
    path("item/<int:item_id>/customize/", views.customize_item, name="customize_item"),
    path("item/<int:item_id>/remove/", views.remove_item, name="remove_item"),
    path(
        "item/<int:item_id>/update-quantity/",
        views.update_item_quantity,
        name="update_item_quantity",
    ),
    path("clear-draft/", views.clear_draft, name="clear_draft"),
    path("set-dining-option/", views.set_dining_option, name="set_dining_option"),
    path(
        "apply-transaction-discount/",
        views.apply_transaction_discount,
        name="apply_transaction_discount",
    ),
    path("payment/", views.payment, name="payment"),
    path("receipt/<int:transaction_id>/", views.receipt, name="receipt"),
]
