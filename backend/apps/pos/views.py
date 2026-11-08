"""
Views for the POS module: Ordering Screen, Add Custom Product, Order
Customization, Payment Processing, Transaction Receipt (Phase 1 Figs.
3-12 through 3-17).

All views here are gated by @pos_unlock_required (Week 3's full login ->
branch selection -> cashier PIN flow) and operate only on the current
cashier's own DRAFT transaction for their selected branch.
"""

import base64

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.permissions import pos_unlock_required
from apps.inventory.models import Product
from apps.pos import printing, services
from apps.pos.models import AddOn, DiscountCategory, DiscountType, SalesTransaction


def _get_draft(request):
    return services.get_or_create_draft_transaction(request.user, request.selected_branch)


@pos_unlock_required
def pos_ordering(request):
    """POS Ordering Screen (Fig. 3-12): product catalog with search and
    category filtering (Row 3 redesign), and the current draft order's
    summary."""
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()

    # Branch-scoped: a product only shows up in a branch's POS catalog
    # once it has a real Inventory row there (see
    # inventory.services.create_product's branch_ids provisioning) --
    # fixes the QA-reported bug where a new product became orderable at
    # every branch regardless of which one it was meant for.
    products = Product.objects.filter(
        is_active=True,
        product_type=Product.ProductType.FINISHED_GOOD,
        inventory__branch=request.selected_branch,
    )
    if query:
        products = products.filter(name__icontains=query)
    if category:
        products = products.filter(category=category)

    draft = _get_draft(request)
    from apps.system_config.models import BusinessSettings

    business_settings = BusinessSettings.load()
    vat_exclusive, vat_amount = draft.vat_breakdown(business_settings.tax_rate_percent)

    # Row 3 redesign: a one-time flag left by a just-completed payment
    # (see the payment view) -- popped immediately so a page refresh
    # never re-shows the modal for an old transaction.
    completed_transaction = None
    completed_rawbt_url = None
    completed_discount_total = None
    completed_vat_exclusive = None
    completed_vat_amount = None
    completed_transaction_id = request.session.pop("just_completed_transaction_id", None)
    if completed_transaction_id:
        completed_transaction = SalesTransaction.objects.filter(
            pk=completed_transaction_id,
            cashier=request.user,
            status=SalesTransaction.Status.COMPLETED,
        ).first()
        if completed_transaction:
            completed_rawbt_url = printing.build_rawbt_print_url(completed_transaction)
            # Task tracker, 2026-11-08: the Transaction Complete modal
            # showed Subtotal and Total with no line explaining the gap
            # between them, so an SC/PWD discount silently vanished from
            # view right after payment (it was still applied -- grand_total
            # already reflects it -- just never displayed), even though
            # the standalone receipt.html page already shows this
            # breakdown (added for task #48's BIR requirement). Same
            # computation as the receipt view, reused here rather than
            # duplicated logic.
            completed_vat_exclusive, completed_vat_amount = completed_transaction.vat_breakdown(
                business_settings.tax_rate_percent
            )
            completed_discount_total = (
                completed_transaction.item_discounts_total
                + completed_transaction.transaction_discount_value
            )

    return render(
        request,
        "pos/pos_ordering.html",
        {
            "products": products,
            "query": query,
            "selected_category": category,
            "categories": Product.Category.choices,
            "draft": draft,
            "addons": AddOn.objects.filter(is_active=True),
            "discount_categories": DiscountCategory.choices,
            "discount_types": DiscountType.choices,
            "vat_exclusive": vat_exclusive,
            "vat_amount": vat_amount,
            "tax_rate_percent": business_settings.tax_rate_percent,
            "currency_symbol": business_settings.currency_symbol,
            "dining_options": SalesTransaction.DiningOption.choices,
            "business_settings": business_settings,
            "completed_transaction": completed_transaction,
            "completed_rawbt_url": completed_rawbt_url,
            "completed_discount_total": completed_discount_total,
            "completed_vat_exclusive": completed_vat_exclusive,
            "completed_vat_amount": completed_vat_amount,
        },
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
def add_customized_item(request):
    """Product Customization modal's submit target (Fig. 3-14, Row 3
    redesign) -- adds the item with size/sugar/add-ons/discount already
    applied in one step, rather than add-then-edit."""
    if request.method == "POST":
        draft = _get_draft(request)
        item = services.add_customized_item(
            draft,
            product_id=request.POST.get("product_id"),
            quantity=request.POST.get("quantity", 1),
            size=request.POST.get("size", ""),
            sugar_level=request.POST.get("sugar_level", ""),
            addon_ids=request.POST.getlist("addon_ids"),
            discount_category=request.POST.get("discount_category", ""),
            discount_type=request.POST.get("discount_type", ""),
            discount_amount=request.POST.get("discount_amount") or None,
            note=request.POST.get("note", ""),
        )
        if item is None:
            messages.error(request, "That product could not be added. Please try again.")
    return redirect("pos:ordering")


@pos_unlock_required
def add_custom_product(request):
    """Add Custom Product (Fig. 3-13) -- now a modal on the ordering
    screen (Row 3 redesign) rather than a separate page, so an invalid
    submission redirects back to the ordering screen with a flash
    message instead of rendering a standalone error page."""
    if request.method == "POST":
        draft = _get_draft(request)
        item = services.add_custom_item(draft, request.POST.get("name"), request.POST.get("price"))
        if item is not None:
            return redirect("pos:ordering")
        messages.error(request, "Enter a valid name and a non-negative price.")
    return redirect("pos:ordering")


@pos_unlock_required
def remove_item(request, item_id):
    if request.method == "POST":
        draft = _get_draft(request)
        services.remove_item(draft, item_id)
    return redirect("pos:ordering")


@pos_unlock_required
def update_item_quantity(request, item_id):
    """The quick +/- stepper in the Order Summary (Row 3 redesign)."""
    if request.method == "POST":
        draft = _get_draft(request)
        services.update_item_quantity(draft, item_id, request.POST.get("quantity"))
    return redirect("pos:ordering")


@pos_unlock_required
def clear_draft(request):
    """'Clear All' (Row 3 redesign)."""
    if request.method == "POST":
        draft = _get_draft(request)
        services.clear_draft_items(draft)
    return redirect("pos:ordering")


@pos_unlock_required
def set_dining_option(request):
    """Dine-in / Take-out toggle (Row 3 redesign)."""
    if request.method == "POST":
        draft = _get_draft(request)
        services.set_dining_option(draft, request.POST.get("dining_option", ""))
    return redirect("pos:ordering")


@pos_unlock_required
def apply_transaction_discount(request):
    """'% Discount' button (Row 3 redesign) -- a whole-order discount,
    layered on top of any per-item discounts already applied."""
    if request.method == "POST":
        draft = _get_draft(request)
        success = services.apply_transaction_discount(
            draft,
            category=request.POST.get("category", ""),
            discount_type=request.POST.get("discount_type", ""),
            amount=request.POST.get("amount") or None,
        )
        if not success:
            messages.error(request, "Please enter a valid discount amount.")
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
            # Row 3 redesign: the reference shows a Transaction Complete
            # modal over the (now-cleared) ordering screen, not a
            # separate page navigation. A one-time session flag tells
            # the ordering view to auto-open it for this specific
            # transaction on the very next request -- the standalone
            # pos:receipt page/URL still exists separately, for later
            # re-viewing or reprinting a past transaction by its own URL.
            request.session["just_completed_transaction_id"] = draft.pk
            return redirect("pos:ordering")
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

    from apps.system_config.models import BusinessSettings

    business_settings = BusinessSettings.load()
    # Found during task #48's demo dry-run: the receipt itself never
    # showed a discount line or a VAT breakdown at all, even though the
    # pre-payment Order Summary (pos_ordering.html) already computes and
    # displays both -- a real gap for an SC/PWD sale, where BIR requires
    # the official receipt to show the VAT-exempt sales breakdown, not
    # just a lower total with no explanation.
    vat_exclusive, vat_amount = completed_transaction.vat_breakdown(
        business_settings.tax_rate_percent
    )
    discount_total = (
        completed_transaction.item_discounts_total
        + completed_transaction.transaction_discount_value
    )
    return render(
        request,
        "pos/receipt.html",
        {
            "transaction": completed_transaction,
            "rawbt_url": rawbt_url,
            "business_settings": business_settings,
            "tax_rate_percent": business_settings.tax_rate_percent,
            "vat_exclusive": vat_exclusive,
            "vat_amount": vat_amount,
            "discount_total": discount_total,
        },
    )


@pos_unlock_required
def receipt_escpos_payload(request, transaction_id):
    """JSON endpoint for the direct-to-laptop Web Bluetooth print path
    (static/js/escpos_bluetooth_print.js) -- PT-210 printer integration,
    "add it alongside RawBT" (task tracker, 2026-11-08). Returns the same
    ESC/POS byte stream build_rawbt_print_url already base64-encodes into
    an intent URL for RawBT, just as plain JSON instead: the browser
    decodes it client-side and writes it straight to the printer's BLE
    characteristic, no RawBT/Android app in between. Same
    ownership/branch scoping as the receipt view -- this is the same
    receipt data in a different shape, not a new access path."""
    completed_transaction = get_object_or_404(
        SalesTransaction,
        pk=transaction_id,
        cashier=request.user,
        branch=request.selected_branch,
        status=SalesTransaction.Status.COMPLETED,
    )
    escpos_bytes = printing.build_escpos_receipt(completed_transaction)
    return JsonResponse({"escpos_base64": base64.b64encode(escpos_bytes).decode("ascii")})
