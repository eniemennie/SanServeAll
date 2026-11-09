from collections import defaultdict

from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import Branch, Role
from apps.accounts.permissions import role_required
from apps.inventory.models import Product
from apps.purchasing.models import PurchaseOrder, PurchaseOrderItem, Supplier
from apps.purchasing.services import (
    PurchasingError,
    cancel_purchase_order,
    create_purchase_order,
    mark_purchase_order_ordered,
    receive_purchase_order,
)

SUPPLIER_FORM_FIELDS = ["name", "contact_person", "phone_number", "email", "address", "notes"]


def _build_supplier_list_context(
    error=None, created_name=None, edit_error_supplier_id=None, edit_form_values=None
):
    """Shared context builder for supplier_management and supplier_edit
    (client review, 2026-11-09, Suppliers page layout improvement) --
    both views render the same template (list + modals), so this keeps
    the suppliers queryset/annotations in one place rather than
    duplicated between them.

    `materials_supplied_count` (client review, 2026-11-09): "Materials
    Supplied" on the table and in the View modal is deliberately NOT
    Product.supplier (a separate, pre-existing plain-text catalog field
    unrelated to this Supplier model -- see apps.purchasing.models'
    own module docstring) -- it's a real, honestly-derived count of
    distinct materials this supplier has actually been ordered for via
    a PurchaseOrder here, so it only ever reflects real purchasing
    history, never an invented or unrelated number.
    """
    suppliers = Supplier.objects.annotate(
        materials_supplied_count=Count("purchase_orders__items__material", distinct=True),
        purchase_order_count=Count("purchase_orders", distinct=True),
    )

    materials_by_supplier = defaultdict(set)
    for item in PurchaseOrderItem.objects.filter(
        purchase_order__supplier__in=suppliers
    ).select_related("material", "purchase_order"):
        materials_by_supplier[item.purchase_order.supplier_id].add(item.material.name)
    for supplier in suppliers:
        supplier.materials_supplied_names = sorted(materials_by_supplier.get(supplier.id, []))

    return {
        "active_nav": "suppliers",
        "suppliers": suppliers,
        "error": error,
        "created_name": created_name,
        "edit_error_supplier_id": edit_error_supplier_id,
        "edit_form_values": edit_form_values or {},
    }


@role_required(Role.OWNER_ADMIN, Role.PURCHASING)
def supplier_management(request):
    """List + create suppliers (task #143). Mirrors
    apps.accounts.views.account_management's list-and-create-on-one-page
    pattern (plain `error`/`created_name` context vars for feedback,
    same as that view's `error`/`created_username`, rather than the
    django.contrib.messages framework, which admin_base.html doesn't
    render anywhere in this codebase).

    Both OWNER_ADMIN and PURCHASING may reach this -- it's the only
    screen PURCHASING has at all (see BranchSelectionView's redirect),
    and OWNER_ADMIN retains full visibility/control over every screen in
    the system, consistent with every other role_required call
    elsewhere in the codebase."""
    error = None
    created_name = None

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        if not name:
            error = "Supplier name is required."
        elif Supplier.objects.filter(name__iexact=name).exists():
            error = f"A supplier named '{name}' already exists."
        else:
            Supplier.objects.create(
                name=name,
                contact_person=(request.POST.get("contact_person") or "").strip(),
                phone_number=(request.POST.get("phone_number") or "").strip(),
                email=(request.POST.get("email") or "").strip(),
                address=(request.POST.get("address") or "").strip(),
                notes=(request.POST.get("notes") or "").strip(),
            )
            created_name = name

    return render(
        request,
        "purchasing/supplier_management.html",
        _build_supplier_list_context(error=error, created_name=created_name),
    )


@role_required(Role.OWNER_ADMIN, Role.PURCHASING)
def supplier_edit(request, supplier_id):
    """Edits an existing supplier's details (client review, 2026-11-09,
    Suppliers page layout improvement) -- same fields and validation as
    creating one, opened from a per-row Edit modal instead of a
    separate page, consistent with this screen's own list-and-edit-
    on-one-page pattern. On a validation error, re-renders the same
    list with that supplier's Edit modal reopened and the submitted
    (not yet saved) values preserved, rather than silently discarding
    what was typed."""
    supplier = get_object_or_404(Supplier, pk=supplier_id)

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        submitted = {
            field: (request.POST.get(field) or "").strip() for field in SUPPLIER_FORM_FIELDS
        }

        if not name:
            error = "Supplier name is required."
        elif Supplier.objects.filter(name__iexact=name).exclude(pk=supplier.pk).exists():
            error = f"A supplier named '{name}' already exists."
        else:
            supplier.name = name
            supplier.contact_person = submitted["contact_person"]
            supplier.phone_number = submitted["phone_number"]
            supplier.email = submitted["email"]
            supplier.address = submitted["address"]
            supplier.notes = submitted["notes"]
            supplier.save()
            return redirect("purchasing:supplier_management")

        return render(
            request,
            "purchasing/supplier_management.html",
            _build_supplier_list_context(
                error=error, edit_error_supplier_id=supplier.pk, edit_form_values=submitted
            ),
        )

    return redirect("purchasing:supplier_management")


@role_required(Role.OWNER_ADMIN, Role.PURCHASING)
def supplier_toggle_active(request, supplier_id):
    """Deactivate/reactivate a supplier rather than deleting it -- keeps
    its history (and any future FK from a purchase order) intact."""
    supplier = get_object_or_404(Supplier, pk=supplier_id)
    if request.method == "POST":
        supplier.is_active = not supplier.is_active
        supplier.save(update_fields=["is_active"])
    return redirect("purchasing:supplier_management")


@role_required(Role.OWNER_ADMIN, Role.PURCHASING)
def purchase_order_list(request):
    """List existing purchase orders and create a new one (Consolidated
    Task Tracker "Purchasing/Receiving records model"). Mirrors
    supplier_management's own list-and-create-on-one-page pattern.

    New-order items use the same parallel-list form convention as
    apps.production.views' IngredientUsage rows (material_id[] /
    quantity_used[]) -- one row per MATERIAL in the catalog, blank rows
    skipped, here with an added unit_cost[] column."""
    error = None
    created_order = None

    materials = Product.objects.filter(
        product_type=Product.ProductType.MATERIAL, is_active=True
    ).order_by("name")

    if request.method == "POST":
        material_ids = request.POST.getlist("material_id")
        ordered_quantities = request.POST.getlist("ordered_quantity")
        unit_costs = request.POST.getlist("unit_cost")
        items = [
            {"material_id": mid, "ordered_quantity": qty, "unit_cost": cost}
            for mid, qty, cost in zip(material_ids, ordered_quantities, unit_costs)
            if (qty or "").strip()
        ]

        try:
            created_order = create_purchase_order(
                created_by=request.user,
                supplier_id=request.POST.get("supplier_id"),
                branch_id=request.POST.get("branch_id"),
                items=items,
                notes=(request.POST.get("notes") or "").strip(),
            )
        except PurchasingError as exc:
            error = str(exc)

    return render(
        request,
        "purchasing/purchase_order_list.html",
        {
            "active_nav": "purchase_orders",
            "orders": PurchaseOrder.objects.select_related("supplier", "branch").all(),
            "suppliers": Supplier.objects.filter(is_active=True),
            "branches": Branch.objects.all(),
            "materials": materials,
            "error": error,
            "created_order": created_order,
        },
    )


@role_required(Role.OWNER_ADMIN, Role.PURCHASING)
def purchase_order_detail(request, order_id):
    """View one PurchaseOrder, its line items, and the action available
    for its current status (mark Ordered, receive, or cancel)."""
    order = get_object_or_404(
        PurchaseOrder.objects.select_related("supplier", "branch"), pk=order_id
    )
    return render(
        request,
        "purchasing/purchase_order_detail.html",
        {
            "active_nav": "purchase_orders",
            "order": order,
            "items": order.items.select_related("material"),
        },
    )


@role_required(Role.OWNER_ADMIN, Role.PURCHASING)
def purchase_order_mark_ordered(request, order_id):
    order = get_object_or_404(PurchaseOrder, pk=order_id)
    if request.method == "POST":
        try:
            mark_purchase_order_ordered(order, acted_by=request.user)
        except PurchasingError:
            pass
    return redirect("purchasing:purchase_order_detail", order_id=order.pk)


@role_required(Role.OWNER_ADMIN, Role.PURCHASING)
def purchase_order_receive(request, order_id):
    """GET shows a receiving form pre-filled with each line's ordered
    quantity (editable, to record a short or over delivery -- Option B
    of the 2026-11-07 scoped options). POST actually receives it."""
    order = get_object_or_404(
        PurchaseOrder.objects.select_related("supplier", "branch"), pk=order_id
    )
    error = None

    if request.method == "POST":
        received_quantities = {
            item_id: request.POST.get(f"received_quantity_{item_id}", "")
            for item_id in request.POST.getlist("item_id")
        }
        try:
            receive_purchase_order(
                order, received_quantities=received_quantities, received_by=request.user
            )
            return redirect("purchasing:purchase_order_detail", order_id=order.pk)
        except PurchasingError as exc:
            error = str(exc)

    return render(
        request,
        "purchasing/purchase_order_receive.html",
        {
            "active_nav": "purchase_orders",
            "order": order,
            "items": order.items.select_related("material"),
            "error": error,
        },
    )


@role_required(Role.OWNER_ADMIN, Role.PURCHASING)
def purchase_order_cancel(request, order_id):
    order = get_object_or_404(PurchaseOrder, pk=order_id)
    if request.method == "POST":
        try:
            cancel_purchase_order(order, cancelled_by=request.user)
        except PurchasingError:
            pass
    return redirect("purchasing:purchase_order_detail", order_id=order.pk)
