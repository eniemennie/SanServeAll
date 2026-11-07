from django.contrib import admin

from apps.purchasing.models import PurchaseOrder, PurchaseOrderItem, Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "contact_person", "phone_number", "email", "is_active")
    search_fields = ("name", "contact_person", "email")
    list_filter = ("is_active",)


class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 0


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "supplier", "branch", "status", "created_by", "created_at")
    list_filter = ("status", "branch")
    search_fields = ("supplier__name",)
    inlines = [PurchaseOrderItemInline]
