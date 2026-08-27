"""
Views for the Production Module (Week 8): Production Recording and the
Batch Management Interface (Fig. 3-24).

Restricted to Commissary Staff and Owner/Admin -- branch cashiers have no
reason to record commissary production.
"""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import Role
from apps.inventory.models import Product
from apps.production import services
from apps.production.models import ProductionRecord


def _can_manage_production(user):
    return bool(user.role and user.role.name in (Role.COMMISSARY_STAFF, Role.OWNER_ADMIN))


@login_required
def record_production(request):
    """Production Recording -- creates one ProductionRecord plus its
    ingredient usages, atomically deducting materials and crediting the
    finished good's stock at the commissary."""
    if not _can_manage_production(request.user):
        raise PermissionDenied("Only commissary staff or Owner/Admin can record production.")

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
        {"error": error, "materials": materials, "finished_goods": finished_goods},
    )


@login_required
def batch_management(request):
    """Batch Management Interface (Fig. 3-24): list of production
    records with branch (always commissary), ingredient/material,
    quantity, supplier, quality, status -- plus edit/delete actions."""
    if not _can_manage_production(request.user):

        raise PermissionDenied("Only commissary staff or Owner/Admin can view this page.")

    records = ProductionRecord.objects.select_related("product").prefetch_related(
        "ingredient_usages__material"
    )
    return render(request, "production/batch_management.html", {"records": records})


@login_required
def delete_production_record(request, record_id):
    """Delete action (Fig. 3-24). Deliberately does NOT reverse the
    inventory changes the record originally made -- undoing a completed
    production run's stock effects is a distinct, more consequential
    operation than removing the record of it, and isn't assumed safe to
    do silently as a side effect of a delete click."""
    if not _can_manage_production(request.user):

        raise PermissionDenied("Only commissary staff or Owner/Admin can delete records.")

    record = get_object_or_404(ProductionRecord, pk=record_id)
    if request.method == "POST":
        record.delete()
        return redirect("production:batch_management")

    return render(request, "production/confirm_delete.html", {"record": record})
