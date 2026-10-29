"""
Views for the Production Module (Week 8): Production Recording and the
Batch Management Interface (Fig. 3-24).

Restricted to Commissary Staff and Owner/Admin -- branch cashiers have no
reason to record commissary production. Uses the shared role_required
decorator (Row 12.4) rather than a bespoke inline check, so an Owner/
Admin reaching these views is correctly required to have completed 2FA
-- Commissary Staff, who never enroll in 2FA at all, are unaffected,
since that enforcement is based on the actual logged-in user's role, not
merely whether Owner/Admin is among a view's allowed roles.
"""

from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.models import Branch, Role
from apps.accounts.permissions import role_required
from apps.inventory import services as inventory_services
from apps.inventory.models import Product
from apps.production import services
from apps.production.models import ProductionRecord, StockTransferOrder


@role_required(Role.COMMISSARY_STAFF, Role.OWNER_ADMIN)
def commissary_dashboard(request):
    """Commissary Staff's landing page after login/branch selection.

    Replaces the old generic `accounts.dashboard_placeholder` -- that view
    was an explicitly temporary stand-in (per its own docstring) for
    whichever role had no dedicated dashboard yet. Commissary Staff was
    the only role still landing there; this gives them a real one built
    from data this app already has, rather than a static welcome page.

    Wrapped in the same admin_base.html shell as the Owner/Admin screens
    for visual consistency -- that shell's sidebar/topbar are role-aware
    (see admin_base.html's own docstring), so a Commissary Staff user only
    ever sees the three links this role can actually reach (Dashboard,
    Record Production, Batch Logs); the Owner-only links (Settings,
    Reports, Forecasting, Product Availability, Resource Management) are
    not rendered at all for this role, rather than shown and then 403ing
    on click.
    """
    commissary = services.get_commissary_branch()
    recent_records = ProductionRecord.objects.select_related("product").order_by("-created_at")[:5]
    resource_status = (
        inventory_services.get_resource_status_summary(branch=commissary) if commissary else None
    )

    return render(
        request,
        "production/commissary_dashboard.html",
        {
            "active_nav": "dashboard",
            "total_output_30d": services.get_total_output(days=30),
            "recent_records": recent_records,
            "resource_status": resource_status,
            "commissary": commissary,
        },
    )


@role_required(Role.COMMISSARY_STAFF, Role.OWNER_ADMIN)
def record_production(request):
    """Production Recording -- creates one ProductionRecord plus its
    ingredient usages, atomically deducting materials and crediting the
    finished good's stock at the commissary."""
    error = None
    materials = Product.objects.filter(product_type=Product.ProductType.MATERIAL, is_active=True)
    finished_goods = Product.objects.filter(
        product_type=Product.ProductType.FINISHED_GOOD, is_active=True
    )

    if request.method == "POST":
        try:
            material_ids = request.POST.getlist("material_id")
            quantities_used = request.POST.getlist("quantity_used")
            ingredient_rows = [
                {"material_id": int(mid), "quantity_used": int(qty)}
                for mid, qty in zip(material_ids, quantities_used)
                if mid and qty
            ]

            services.record_production(
                commissary_staff=request.user,
                product_id=request.POST.get("product_id"),
                quantity_produced=int(request.POST.get("quantity_produced") or 0),
                ingredient_rows=ingredient_rows,
                supplier=request.POST.get("supplier", ""),
                quality=request.POST.get("quality", ProductionRecord.Quality.PASS),
                status=request.POST.get("status", ProductionRecord.Status.COMPLETED),
                notes=request.POST.get("notes", ""),
            )
            return redirect("production:batch_management")
        except (services.ProductionError, ValueError, TypeError) as exc:
            error = str(exc) or "Please check the values entered."

    return render(
        request,
        "production/production_entry.html",
        {
            "active_nav": "record",
            "error": error,
            "materials": materials,
            "finished_goods": finished_goods,
        },
    )


@role_required(Role.COMMISSARY_STAFF, Role.OWNER_ADMIN)
def batch_management(request):
    """Batch Management Interface (Fig. 3-24): list of production
    records with product, ingredient/material usage, supplier, quality,
    status -- plus edit/delete actions.

    NOTE: the reference design's table includes a per-row "Branch"
    column (Batangas/Alangilan/Lipa tags). ProductionRecord has no
    branch field at all -- production genuinely happens only at the
    single commissary (see get_total_output's own docstring from the
    Dashboard Home batch), so that column isn't rendered here rather
    than filled with a fabricated value.

    Wrapped in the same Admin Shell as the Owner/Admin screens (matching
    the reference design). Commissary Staff can also reach this view --
    admin_base.html's sidebar is role-aware, so a Commissary Staff user
    sees only their own three links here, not the Owner-only ones.
    """
    status_filter = request.GET.get("status", "")
    date_filter = request.GET.get("date", "")

    records = ProductionRecord.objects.select_related("product").prefetch_related(
        "ingredient_usages__material"
    )
    if status_filter:
        records = records.filter(status=status_filter)
    if date_filter == "today":
        records = records.filter(created_at__date=timezone.now().date())

    total = records.count()
    completed = records.filter(status=ProductionRecord.Status.COMPLETED).count()
    in_progress = records.filter(status=ProductionRecord.Status.IN_PROGRESS).count()
    pending = records.filter(status=ProductionRecord.Status.PENDING).count()
    total_quantity = sum(r.quantity_produced for r in records)
    quality_pass = records.filter(quality=ProductionRecord.Quality.PASS).count()
    quality_rate = round((quality_pass / total) * 100) if total else 100

    return render(
        request,
        "production/batch_management.html",
        {
            "active_nav": "batch",
            "records": records,
            "status_filter": status_filter,
            "date_filter": date_filter,
            "stats": {
                "total": total,
                "completed": completed,
                "in_progress": in_progress,
                "pending": pending,
                "total_quantity": total_quantity,
                "quality_rate": quality_rate,
            },
            "status_choices": ProductionRecord.Status.choices,
        },
    )


@role_required(Role.COMMISSARY_STAFF, Role.OWNER_ADMIN)
def delete_production_record(request, record_id):
    """Delete action (Fig. 3-24). Deliberately does NOT reverse the
    inventory changes the record originally made -- undoing a completed
    production run's stock effects is a distinct, more consequential
    operation than removing the record of it, and isn't assumed safe to
    do silently as a side effect of a delete click."""
    record = get_object_or_404(ProductionRecord, pk=record_id)
    if request.method == "POST":
        record.delete()
        return redirect("production:batch_management")

    return render(
        request, "production/confirm_delete.html", {"active_nav": "batch", "record": record}
    )


@role_required(Role.COMMISSARY_STAFF, Role.OWNER_ADMIN)
def stock_transfers(request):
    """Stock order status tracking (processing/in transit/delivered) --
    the manuscript's "Supply distribution record updates per branch".
    Lists existing StockTransferOrders and handles creating a new one;
    advancing an order's status is its own view (advance_stock_transfer)
    below, same split as record_production/batch_management above.
    """
    error = None
    finished_goods = Product.objects.filter(
        product_type=Product.ProductType.FINISHED_GOOD, is_active=True
    )
    destination_branches = Branch.objects.filter(is_commissary=False, is_active=True)

    if request.method == "POST":
        try:
            services.create_stock_transfer_order(
                commissary_staff=request.user,
                destination_branch_id=request.POST.get("destination_branch_id"),
                product_id=request.POST.get("product_id"),
                quantity=request.POST.get("quantity"),
            )
            return redirect("production:stock_transfers")
        except services.StockTransferError as exc:
            error = str(exc)

    orders = StockTransferOrder.objects.select_related(
        "product", "source_branch", "destination_branch"
    )

    return render(
        request,
        "production/stock_transfers.html",
        {
            "active_nav": "transfers",
            "error": error,
            "orders": orders,
            "finished_goods": finished_goods,
            "destination_branches": destination_branches,
            "status_choices": StockTransferOrder.Status.choices,
        },
    )


@role_required(Role.COMMISSARY_STAFF, Role.OWNER_ADMIN)
def advance_stock_transfer(request, order_id):
    """Advances one StockTransferOrder exactly one step forward
    (PROCESSING -> IN_TRANSIT -> DELIVERED). A thin wrapper over the
    service, matching quick_sale's POST-and-redirect-back pattern --
    no separate confirmation page for a single forward status step."""
    order = get_object_or_404(StockTransferOrder, pk=order_id)
    if request.method == "POST":
        next_status = request.POST.get("next_status")
        try:
            services.advance_stock_transfer_status(order, next_status)
        except services.StockTransferError:
            pass  # Stay on the page; the list view shows the order's real, unchanged status.

    return redirect("production:stock_transfers")
