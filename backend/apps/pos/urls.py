from django.urls import path

from apps.pos import views

app_name = "pos"

urlpatterns = [
    path("", views.pos_ordering, name="ordering"),
    path("add-catalog-item/", views.add_catalog_item, name="add_catalog_item"),
    path("add-custom-product/", views.add_custom_product, name="add_custom_product"),
    path("item/<int:item_id>/customize/", views.customize_item, name="customize_item"),
    path("item/<int:item_id>/remove/", views.remove_item, name="remove_item"),
]
