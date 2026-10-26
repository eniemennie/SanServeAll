from django.contrib import admin

from apps.kahero_integration.models import KaheroImportBatch


@admin.register(KaheroImportBatch)
class KaheroImportBatchAdmin(admin.ModelAdmin):
    list_display = (
        "pk",
        "branch",
        "original_filename",
        "status",
        "total_rows",
        "success_count",
        "error_count",
        "uploaded_by",
        "created_at",
    )
    list_filter = ("status", "branch")
    readonly_fields = (
        "total_rows",
        "success_count",
        "error_count",
        "error_log",
        "created_at",
        "completed_at",
    )
