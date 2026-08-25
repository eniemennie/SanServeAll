"""
Views for the Inventory Module (Week 6): Inventory Monitoring
(Fig. 3-20/3-27/3-32/3-33 combined into one branch/type-filtered screen),
Product Inventory Management (Fig. 3-34), and Manual Stock Adjustment.

Monitoring is viewable by any authenticated user with a branch selected
(branch staff need visibility into their own stock, not just Owner/Admin).
Managing the product catalog and adjusting stock are Owner/Admin-only --
gated with @role_required, matching Table 3-2's Business Owner ownership
of FR-02 (Multi-Branch Inventory Synchronization).
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import Role
from apps.accounts.permissions import role_required
from apps.inventory import services
from apps.inventory.models import Inventory, Product


def _require_selected_branch(request):
    """Monitoring views need a branch context but not a full POS PIN
    unlock -- this is a lighter-weight check than @pos_unlock_required."""
    return request.selected_branch is not None


@login_required
def inventory_monitoring(request):
    """Inventory Monitoring Interface (Fig. 3-20), combined with Finished
    Goods (Fig. 3-32) and Materials Tracking (Fig. 3-33) via the
    product_type filter, and Stock and Resource Management (Fig. 3-27)."""
    if not _require_selected_branch(request):
        return redirect("accounts:select_branch")

    product_type = request.GET.get("type", "")
    low_stock_only = request.GET.get("low_stock") == "1"

    items = services.get_branch_inventory(
        request.selected_branch,
        product_type=product_type or None,
        low_stock_only=low_stock_only,
    )

    return render(
        request,
        "inventory/monitoring.html",
        {
            "items": items,
            "selected_type": product_type,
            "low_stock_only": low_stock_only,
            "product_types": Product.ProductType.choices,
        },
    )


@role_required(Role.OWNER_ADMIN)
def product_management(request):
    """Product Inventory Management Interface (Fig. 3-34): catalog list
    plus the add-product form."""
    error = None
    if request.method == "POST":
        try:
            services.create_product(
                name=request.POST.get("name"),
                price=request.POST.get("price") or 0,
                product_type=request.POST.get("product_type", Product.ProductType.FINISHED_GOOD),
                reorder_threshold=request.POST.get("reorder_threshold") or 0,
            )
            return redirect("inventory:product_management")
        except (services.InventoryServiceError, ValueError, TypeError) as exc:
            error = str(exc) or "Please check the values entered."

    products = Product.objects.all()
    return render(
        request,
        "inventory/product_management.html",
        {"products": products, "error": error, "product_types": Product.ProductType.choices},
    )


@role_required(Role.OWNER_ADMIN)
def adjust_stock(request, inventory_id):
    """Manual Stock Adjustment action (Fig. 3-34)."""
    inventory = get_object_or_404(Inventory, pk=inventory_id)
    error = None

    if request.method == "POST":
        try:
            services.adjust_stock(inventory, request.POST.get("delta"))
            return redirect("inventory:monitoring")
        except services.InventoryServiceError as exc:
            error = str(exc)

    return render(request, "inventory/adjust_stock.html", {"inventory": inventory, "error": error})
