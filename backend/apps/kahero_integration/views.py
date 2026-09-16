"""
Views for the KaHero batch-import pipeline: File Upload Screen (Row 7.1)
and Batch Processing Analytics Dashboard (Row 7.4).

Both restricted to Owner/Admin -- uploading historical sales data on
behalf of a branch is an administrative action, not a day-to-day staff
task (matches Table 3-2's Business Owner ownership of batch-related FRs).
"""

from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import Branch, Role
from apps.accounts.permissions import role_required
from apps.kahero_integration import services
from apps.kahero_integration.models import KaheroImportBatch


@role_required(Role.OWNER_ADMIN)
def upload_batch(request):
    """File Upload Screen (Row 7.1)."""
    error = None
    kahero_branch = Branch.objects.filter(is_kahero_branch=True).first()

    if request.method == "POST":
        uploaded_file = request.FILES.get("file")
        if uploaded_file is None:
            error = "Please choose a file to upload."
        elif kahero_branch is None:
            error = "No branch is configured for KaHero batch imports."
        else:
            batch = services.process_kahero_upload(kahero_branch, request.user, uploaded_file)
            return redirect("kahero:batch_detail", batch_id=batch.pk)

    return render(
        request, "kahero_integration/upload.html", {"error": error, "kahero_branch": kahero_branch}
    )


@role_required(Role.OWNER_ADMIN)
def batch_dashboard(request):
    """Batch Processing Analytics Dashboard (Row 7.4): every upload's
    status, row counts, and quality rate at a glance."""
    batches = KaheroImportBatch.objects.select_related("branch", "uploaded_by")

    total_batches = batches.count()
    status_counts = {
        label: batches.filter(status=value).count()
        for value, label in KaheroImportBatch.Status.choices
    }
    total_rows_received = sum(b.total_rows for b in batches)

    return render(
        request,
        "kahero_integration/dashboard.html",
        {
            "batches": batches,
            "total_batches": total_batches,
            "status_counts": status_counts,
            "total_rows_received": total_rows_received,
        },
    )


@role_required(Role.OWNER_ADMIN)
def batch_detail(request, batch_id):
    """Per-batch detail: exact result of one upload, including every
    row-level error, so staff can see precisely what happened rather than
    a single opaque success/fail flag."""
    batch = get_object_or_404(KaheroImportBatch, pk=batch_id)
    return render(request, "kahero_integration/batch_detail.html", {"batch": batch})
