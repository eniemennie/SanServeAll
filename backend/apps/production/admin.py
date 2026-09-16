from django.contrib import admin

from apps.production.models import IngredientUsage, ProductionRecord


class IngredientUsageInline(admin.TabularInline):
    model = IngredientUsage
    extra = 0


@admin.register(ProductionRecord)
class ProductionRecordAdmin(admin.ModelAdmin):
    list_display = (
        "pk",
        "product",
        "quantity_produced",
        "quality",
        "status",
        "commissary_staff",
        "created_at",
    )
    list_filter = ("status", "quality")
    inlines = [IngredientUsageInline]
