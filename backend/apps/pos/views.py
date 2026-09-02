"""
Views for the POS module: Ordering Screen, Add Custom Product, Order
Customization, Payment Processing, Transaction Receipt (Phase 1 Figs.
3-12 through 3-17).

All views here are gated by @pos_unlock_required (Week 3's full login ->
branch selection -> cashier PIN flow) and operate only on the current
cashier's own DRAFT transaction for their selected branch.
"""

from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.permissions import pos_unlock_required
from apps.inventory.models import Product
from apps.pos import printing, services
from apps.pos.models import SalesItem, SalesTransaction


def _get_draft(request):
    return services.get_or_create_draft_transaction(request.user, request.selected_branch)


@pos_unlock_required
def pos_ordering(request):
    """POS Ordering Screen (Fig. 3-12): product catalog with search, and
    the current draft order's summary."""
    query = request.GET.get("q", "").strip()
    products = Product.objects.filter(is_active=True)
    if query:
        products = products.filter(name__icontains=query)

    draft = _get_draft(request)

    return render(
        request,
        "pos/pos_ordering.html",
        {"products": products, "query": query, "draft": draft},
    )


@pos_unlock_required
def add_catalog_item(request):
    if request.method == "POST":
        draft = _get_draft(request)
        services.add_catalog_item(
            draft, request.POST.get("product_id"), request.POST.get("quantity", 1)
        )
    return redirect("pos:ordering")


@pos_unlock_required
def add_custom_product(request):
    """Add Custom Product Interface (Fig. 3-13)."""
    error = None
    if request.method == "POST":
        draft = _get_draft(request)
        item = services.add_custom_item(draft, request.POST.get("name"), request.POST.get("price"))
        if item is not None:
            return redirect("pos:ordering")
        error = "Enter a valid name and a non-negative price."

    return render(request, "pos/add_custom_product.html", {"error": error})


@pos_unlock_required
def customize_item(request, item_id):
    """Order Customization Interface (Fig. 3-14) -- size, sugar level,
    add-ons, quantity for a specific line item already in the draft order."""
    draft = _get_draft(request)
    item = get_object_or_404(SalesItem, pk=item_id, transaction=draft)

    if request.method == "POST":
        customizations = {
            "size": request.POST.get("size", "").strip(),
            "sugar_level": request.POST.get("sugar_level", "").strip(),
            "add_ons": [a.strip() for a in request.POST.getlist("add_ons") if a.strip()],
            "notes": request.POST.get("notes", "").strip(),
        }
        services.update_item_customization(
            item, quantity=request.POST.get("quantity"), customizations=customizations
        )
        return redirect("pos:ordering")

    return render(request, "pos/order_customization.html", {"item": item})


@pos_unlock_required
def remove_item(request, item_id):
    if request.method == "POST":
        draft = _get_draft(request)
        services.remove_item(draft, item_id)
    return redirect("pos:ordering")


@pos_unlock_required
def payment(request):
    """POS Payment Processing Interface (Fig. 3-16): review order summary
    and total, select payment mode, enter amount tendered, confirm."""
    draft = _get_draft(request)
    error = None

    if request.method == "POST":
        try:
            services.complete_sale_payment(
                draft,
                payment_method=request.POST.get("payment_method"),
                amount_tendered=request.POST.get("amount_tendered"),
            )
            return redirect("pos:receipt", transaction_id=draft.pk)
        except services.PaymentError as exc:
            error = str(exc)

    return render(
        request,
        "pos/payment.html",
        {
            "draft": draft,
            "error": error,
            "payment_methods": SalesTransaction.PaymentMethod.choices,
        },
    )


@pos_unlock_required
def receipt(request, transaction_id):
    """Transaction Receipt Interface (Fig. 3-17): itemized digital
    receipt for a COMPLETED sale. Scoped to the current cashier/branch --
    not just any transaction ID -- so one cashier can't view another's
    receipts by guessing a URL."""
    completed_transaction = get_object_or_404(
        SalesTransaction,
        pk=transaction_id,
        cashier=request.user,
        branch=request.selected_branch,
        status=SalesTransaction.Status.COMPLETED,
    )
    rawbt_url = printing.build_rawbt_print_url(completed_transaction)
    return render(
        request,
        "pos/receipt.html",
        {"transaction": completed_transaction, "rawbt_url": rawbt_url},
    )
