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

from apps.accounts.models import Branch, Role
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
    """Product Inventory Management Interface (Fig. 3-34): real
    per-branch stock (via get_product_availability), Finished
    Products/Raw Materials toggle, search, stat cards, and the
    catalog-add form. Enhanced from a bare Product-only list (no stock
    numbers, no branch concept at all) to match the reference design's
    actual content, which shows real Inventory rows, not just catalog
    entries.
    """
    error = None
    if request.method == "POST":
        try:
            branch_ids = request.POST.getlist("branches")
            if not branch_ids:
                raise services.InventoryServiceError(
                    "Select at least one branch to add this product to."
                )
            services.create_product(
                name=request.POST.get("name"),
                price=request.POST.get("price") or 0,
                product_type=request.POST.get("product_type", Product.ProductType.FINISHED_GOOD),
                reorder_threshold=request.POST.get("reorder_threshold") or 0,
                branch_ids=branch_ids,
                unit_of_measure=request.POST.get("unit_of_measure"),
                category=request.POST.get("new_product_category"),
            )
            return redirect("inventory:product_management")
        except (services.InventoryServiceError, ValueError, TypeError) as exc:
            error = str(exc) or "Please check the values entered."

    branch_id = request.GET.get("branch")
    selected_branch = None
    if branch_id:
        try:
            selected_branch = Branch.objects.filter(pk=branch_id).first()
        except (ValueError, TypeError):
            # Bug fix (2026-11-08, capstone QA pass): a malformed or
            # hand-edited branch= value (non-numeric pk) used to raise an
            # uncaught ValueError here and surface as a raw Django 500.
            # Treat it the same as no branch selected rather than crashing.
            selected_branch = None
    product_type = request.GET.get("type", "")
    category = request.GET.get("category", "")
    search = request.GET.get("search", "")

    availability = services.get_product_availability(
        branch=selected_branch,
        product_type=product_type or None,
        category=category or None,
        search=search,
    )

    # Client review (2026-11-08): Packaging materials (tape, boxes, cups,
    # etc.) now get their own dedicated tab, split out of the general
    # Raw Materials tab rather than requiring the category dropdown --
    # the client separately maintains and uploads packaging data (its
    # own spreadsheet) distinct from food raw materials, so it should
    # read as its own section here too, not a filter one has to know to
    # apply.
    is_packaging_tab = (
        product_type == Product.ProductType.MATERIAL and category == Product.Category.PACKAGING
    )
    if is_packaging_tab:
        active_tab = "packaging"
    elif product_type == Product.ProductType.MATERIAL:
        active_tab = "materials"
    else:
        active_tab = "finished"

    # Task #148 (client review, 2026-11-04): category options depend on
    # which type tab is active -- a finished-goods category (Coffee,
    # Pasta, ...) means nothing on the Raw Materials tab, and the
    # Packaging/Raw Materials split means nothing on the Finished
    # Products tab. Materials' own "Raw Materials (Food)" option maps to
    # services.RAW_MATERIAL_CATEGORY_FILTER, not a real stored category
    # (see that constant's docstring). The dedicated Packaging tab shows
    # no category dropdown at all -- it's already scoped to the one
    # category, so there's nothing left to filter by.
    if is_packaging_tab:
        category_choices = []
    elif product_type == Product.ProductType.MATERIAL:
        category_choices = [
            (Product.Category.PACKAGING, Product.Category.PACKAGING.label),
            (services.RAW_MATERIAL_CATEGORY_FILTER, "Raw Materials (Food)"),
        ]
    else:
        category_choices = [
            (value, label)
            for value, label in Product.Category.choices
            if value != Product.Category.PACKAGING
        ]

    return render(
        request,
        "inventory/product_management.html",
        {
            "active_nav": "availability",
            "branches": Branch.objects.filter(is_active=True, is_commissary=False),
            "selected_branch": selected_branch,
            "selected_type": product_type,
            "selected_category": category,
            "active_tab": active_tab,
            "category_choices": category_choices,
            "raw_material_category_filter": services.RAW_MATERIAL_CATEGORY_FILTER,
            "search": search,
            "items": availability["items"],
            "stats": availability["stats"],
            "quick_sale_amounts": [1, 2, 5],
            "error": error,
            "product_types": Product.ProductType.choices,
            "unit_choices": Product.UnitOfMeasure.choices,
        },
    )


@role_required(Role.OWNER_ADMIN)
def quick_sale(request, inventory_id):
    """Quick Sale (Fig. 3-34's -1/-2/-5 buttons): a thin wrapper over the
    same real adjust_stock service the Manual Adjustment page uses, just
    invoked inline with a preset negative delta instead of a separate
    confirmation form. Still produces a real, auditable
    InventoryTransaction -- not a display-only decrement.
    """
    inventory = get_object_or_404(Inventory, pk=inventory_id)
    if request.method == "POST":
        try:
            delta = -abs(int(request.POST.get("amount", 1)))
            services.adjust_stock(inventory, delta, reason="Quick Sale", user=request.user)
        except (services.InventoryServiceError, ValueError, TypeError):
            pass  # Same behavior as running out mid-quick-sale: no-op, stay on the page.

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER", "")
    if not next_url.startswith("/"):
        # Empty, missing, or (defensively) an external URL in a spoofed
        # Referer header -- fall back to a known-safe internal page
        # rather than trusting it, since HTTP_REFERER is caller-supplied.
        return redirect("inventory:product_management")
    return redirect(next_url)


@role_required(Role.OWNER_ADMIN)
def adjust_stock(request, inventory_id):
    """Manual Stock Adjustment action (Fig. 3-34). Requires a non-blank
    reason -- backend gap assessment: this was the one flow with no
    audit trail of who made a correction or why, since a miscount
    "fix" looks identical to any other adjustment in the ledger without
    one."""
    inventory = get_object_or_404(Inventory, pk=inventory_id)
    error = None

    if request.method == "POST":
        reason = (request.POST.get("reason") or "").strip()
        if not reason:
            error = "A reason is required for a manual stock adjustment."
        else:
            try:
                services.adjust_stock(
                    inventory, request.POST.get("delta"), reason=reason, user=request.user
                )
                return redirect("inventory:monitoring")
            except services.InventoryServiceError as exc:
                error = str(exc)

    return render(request, "inventory/adjust_stock.html", {"inventory": inventory, "error": error})
