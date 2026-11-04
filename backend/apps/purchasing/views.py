from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import Role
from apps.accounts.permissions import role_required
from apps.purchasing.models import Supplier


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
        {
            "active_nav": "suppliers",
            "suppliers": Supplier.objects.all(),
            "error": error,
            "created_name": created_name,
        },
    )


@role_required(Role.OWNER_ADMIN, Role.PURCHASING)
def supplier_toggle_active(request, supplier_id):
    """Deactivate/reactivate a supplier rather than deleting it -- keeps
    its history (and any future FK from a purchase order) intact."""
    supplier = get_object_or_404(Supplier, pk=supplier_id)
    if request.method == "POST":
        supplier.is_active = not supplier.is_active
        supplier.save(update_fields=["is_active"])
    return redirect("purchasing:supplier_management")
