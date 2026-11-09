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
            # Client review (2026-11-09): the Add Product form's single
            # "Category" dropdown reuses the same `product_type` POST
            # field name the old "Type" dropdown used, and the same
            # empty-string-means-Finished-Products convention the page's
            # own tabs already use (see PRODUCT_AVAILABILITY_TABS) -- an
            # explicitly empty value is "Finished Products", not "no
            # value was submitted", so `.get(..., default)` alone
            # wouldn't apply the default here (a <select> always submits
            # some value). `or` handles both that and a genuinely missing
            # field the same way, and existing callers that already post
            # a real value (FINISHED_GOOD/MATERIAL/PACKAGING) are
            # unaffected either way.
            submitted_product_type = request.POST.get("product_type") or (
                Product.ProductType.FINISHED_GOOD
            )
            services.create_product(
                name=request.POST.get("name"),
                price=request.POST.get("price") or 0,
                product_type=submitted_product_type,
                reorder_threshold=request.POST.get("reorder_threshold") or 0,
                branch_ids=branch_ids,
                unit_of_measure=request.POST.get("unit_of_measure"),
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

    # Bug fix (client review, 2026-11-09): the Finished Products tab's
    # own link is bare "?type=" (empty string) -- `product_type or None`
    # turned that into product_type=None, which applies NO type filter
    # at all, so Raw Materials and Packaging rows were silently mixed
    # into what's supposed to be a Finished-Goods-only view (confirmed
    # live on production: a "blue box" Raw Material row shown right next
    # to "Almond (7in Square)", a real Finished Good). An empty/missing
    # `type` query param means "the Finished Products tab", not "every
    # type at once" -- there's no tab or control anywhere on this page
    # that asks for an unfiltered mix of both types -- so it now
    # resolves to an explicit FINISHED_GOOD filter, matching the Raw
    # Materials/Packaging tabs' own always-explicit `type=`.
    effective_product_type = product_type or Product.ProductType.FINISHED_GOOD

    availability = services.get_product_availability(
        branch=selected_branch,
        product_type=effective_product_type,
        category=category or None,
        search=search,
    )

    # Client review (2026-11-09): Packaging Materials is now its own
    # Product.ProductType value (promoted from a category tag on a
    # MATERIAL row -- see Product.ProductType's own docstring), so each
    # of the three tabs here is simply its own product_type, with no
    # category-based special-casing needed to tell Packaging apart from
    # Raw Materials any more.
    if effective_product_type == Product.ProductType.PACKAGING:
        active_tab = "packaging"
        selected_tab_value = Product.ProductType.PACKAGING
    elif effective_product_type == Product.ProductType.MATERIAL:
        active_tab = "materials"
        selected_tab_value = Product.ProductType.MATERIAL
    else:
        # Client review (2026-11-09): any value that isn't one of the
        # other two tabs -- including a missing/empty `type`, or an
        # unrecognized one from a hand-edited query string -- falls
        # back to the Finished Products tab (see effective_product_type
        # above), so its own tab value ("") is what should read as
        # selected here too, not the raw (possibly garbage) `type`
        # param -- this is also what the Add Product form's Category
        # dropdown defaults to below.
        active_tab = "finished"
        selected_tab_value = ""

    # Task #148 (client review, 2026-11-04): category options depend on
    # which type tab is active -- a finished-goods category (Coffee,
    # Pasta, ...) means nothing on the Raw Materials or Packaging
    # Materials tabs, which don't have categories of their own at all
    # (Product.Category is a POS-tab concept, finished-goods-only).
    category_choices = [] if active_tab != "finished" else list(Product.Category.choices)

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
            "selected_tab_value": selected_tab_value,
            "category_choices": category_choices,
            "search": search,
            "items": availability["items"],
            # Client review (2026-11-09): collapsible product grouping --
            # the table renders from this, `items` stays in context
            # unchanged (existing tests/filters read it directly) and
            # un-grouped as the source of truth for stats above.
            "product_groups": services.group_items_by_product(availability["items"]),
            "stats": availability["stats"],
            "quick_sale_amounts": [1, 2, 5],
            "error": error,
            # Client review (2026-11-09): the page's own three tabs and
            # the Add Product form's "Category" dropdown are now built
            # from this exact same list (see its own docstring in
            # services.py), so they can't drift apart the way the old
            # separate "Type" + "Category" dropdowns did.
            "tab_choices": services.PRODUCT_AVAILABILITY_TABS,
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
