from django.contrib import admin

from apps.purchasing.models import Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "contact_person", "phone_number", "email", "is_active")
    search_fields = ("name", "contact_person", "email")
    list_filter = ("is_active",)
