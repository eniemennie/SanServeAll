from django.contrib import admin

from apps.inventory.models import Inventory, InventoryTransaction, Product, ProductPackaging


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "product_type",
        "price",
        "supplier",
        "is_active",
        "updated_at",
    )
    list_filter = ("product_type", "is_active")
    search_fields = ("name",)


@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display = ("product", "branch", "quantity_on_hand", "reorder_threshold", "updated_at")
    list_filter = ("branch",)
    search_fields = ("product__name",)


@admin.register(InventoryTransaction)
class InventoryTransactionAdmin(admin.ModelAdmin):
    list_display = ("product", "branch", "movement_type", "quantity_change", "created_at")
    list_filter = ("branch", "movement_type")
    search_fields = ("product__name",)


@admin.register(ProductPackaging)
class ProductPackagingAdmin(admin.ModelAdmin):
    list_display = ("finished_good", "packaging_material", "quantity_per_unit")
    list_filter = ("finished_good",)
    search_fields = ("finished_good__name", "packaging_material__name")
