"""
Alangilan-branch batch-import pipeline (Row 7).

KaheroImportBatch is the audit/staging record for each uploaded CSV/Excel
export -- one row per upload, tracking status, row counts, and per-row
errors so staff can see exactly what happened with a given file rather
than a silent success/failure.
"""

from django.conf import settings
from django.db import models


class KaheroImportBatch(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROCESSING = "PROCESSING", "Processing"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    branch = models.ForeignKey("accounts.Branch", on_delete=models.PROTECT)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    uploaded_file = models.FileField(upload_to="kahero_imports/%Y/%m/")
    original_filename = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)

    total_rows = models.PositiveIntegerField(default=0)
    success_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    # A list of {"row": <line number>, "message": <what went wrong>} dicts
    # -- kept as JSON rather than a separate table since these are only
    # ever read back as a simple list on the batch's own detail view, not
    # queried independently.
    error_log = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Batch #{self.pk} ({self.status}) - {self.original_filename}"

    @property
    def quality_rate(self):
        """Percentage of rows successfully ingested -- the "quality rate"
        metric for the Batch Processing Analytics Dashboard (Fig. 3-23,
        reinterpreted per Row 7.4 for file-upload batches rather than
        ingredient-receiving batches -- see PR description for the reasoning)."""
        if self.total_rows == 0:
            return None
        return round((self.success_count / self.total_rows) * 100, 1)
