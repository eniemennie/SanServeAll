from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import Branch, CashierPIN, Role, User


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_kahero_branch", "is_active")
    list_filter = ("is_kahero_branch", "is_active")
    search_fields = ("name", "code")


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name",)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "role", "branch", "is_staff")
    list_filter = ("role", "branch", "is_staff")
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("SanServeAll", {"fields": ("role", "branch", "employee_id", "phone_number")}),
    )


@admin.register(CashierPIN)
class CashierPINAdmin(admin.ModelAdmin):
    list_display = ("user", "is_active", "last_used_at")
    readonly_fields = ("last_used_at",)
