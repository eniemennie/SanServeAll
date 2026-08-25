from django.contrib import admin

from apps.inventory.models import Inventory, InventoryTransaction, Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "price", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display = ("product", "branch", "quantity_on_hand", "updated_at")
    list_filter = ("branch",)
    search_fields = ("product__name",)


@admin.register(InventoryTransaction)
class InventoryTransactionAdmin(admin.ModelAdmin):
    list_display = ("product", "branch", "movement_type", "quantity_change", "created_at")
    list_filter = ("branch", "movement_type")
    search_fields = ("product__name",)
