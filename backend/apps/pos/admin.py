from django.contrib import admin

from apps.pos.models import SalesItem, SalesTransaction


class SalesItemInline(admin.TabularInline):
    model = SalesItem
    extra = 0
    readonly_fields = ("subtotal",)


@admin.register(SalesTransaction)
class SalesTransactionAdmin(admin.ModelAdmin):
    list_display = ("pk", "branch", "cashier", "status", "created_at", "total_amount")
    list_filter = ("status", "branch")
    inlines = [SalesItemInline]
