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

from apps.accounts.models import Role
from apps.accounts.permissions import role_required
from apps.inventory.models import Product
from apps.production import services
from apps.production.models import ProductionRecord


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
        {"error": error, "materials": materials, "finished_goods": finished_goods},
    )


@role_required(Role.COMMISSARY_STAFF, Role.OWNER_ADMIN)
def batch_management(request):
    """Batch Management Interface (Fig. 3-24): list of production
    records with branch (always commissary), ingredient/material,
    quantity, supplier, quality, status -- plus edit/delete actions."""
    records = ProductionRecord.objects.select_related("product").prefetch_related(
        "ingredient_usages__material"
    )
    return render(request, "production/batch_management.html", {"records": records})


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

    return render(request, "production/confirm_delete.html", {"record": record})
