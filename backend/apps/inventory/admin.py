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
    # `quantity_on_hand` is read-only here (Task tracker, 2026-11-08
    # reconciliation finding): this was a plain ModelAdmin with no
    # readonly_fields, so the stock-on-hand field could be hand-edited
    # straight from the admin change form with no InventoryTransaction
    # ever logged for it -- the one unprotected path into a field every
    # other write to it (POS sales, production, transfers, purchasing
    # receipts, and the dedicated Manual Stock Adjustment flow at
    # adjust_stock()) goes through db_transaction.atomic() + a logged
    # transaction for. find_ledger_discrepancies()/reconcile_inventory
    # turned up a handful of rows where the recorded balance and the
    # transaction ledger had drifted apart with real, non-zero ledger
    # activity on both sides -- exactly the shape an admin-form edit
    # like this would produce. Left list_display unchanged (still shown,
    # just not editable); still correct here because it isn't the
    # importer's seeded-but-never-logged *opening* balances, and there's
    # already an audited UI for adjusting stock -- Manual Stock
    # Adjustment (Fig. 3-34) -- so nothing is lost by closing this door.
    readonly_fields = ("quantity_on_hand",)
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
