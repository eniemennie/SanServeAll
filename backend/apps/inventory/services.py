"""
Inventory Module business logic (Week 6): branch-scoped stock queries,
low-stock detection, and manual stock adjustment.
"""

from decimal import Decimal, InvalidOperation

from apps.inventory.models import Inventory, InventoryTransaction, Product


def get_product_availability(branch=None, product_type=None, category=None, search=""):
    """Product Availability screen (Fig. 3-34), enhanced beyond the bare
    catalog list product_management previously showed: real per-branch
    stock across all branches (or one, via the shared shell's branch
    dropdown), plus the stat summary the reference design shows (Total
    Products/Available/Low Stock/Out of Stock/Total Stock/Stock Value).

    Deliberately reuses Inventory (not just Product) so stock numbers
    are real, not fabricated -- a Product with no Inventory row anywhere
    genuinely has no stock to report, and isn't included here.

    Excludes discontinued products (Product.is_active=False) -- backend
    gap assessment: a discontinued product with leftover stock was
    still showing up here as if it were a normal, orderable item.

    `category` filters by Product.category -- a finished-goods POS
    category like COFFEE. Client review (2026-11-09): Raw Materials and
    Packaging Materials are now their own product_type values with no
    category of their own, so `category` is only ever meaningful on the
    Finished Products tab; passing it alongside a MATERIAL/PACKAGING
    `product_type` simply matches nothing (every one of those rows has a
    blank category), same as passing a nonexistent category would.
    """
    from apps.accounts.models import Branch

    queryset = Inventory.objects.select_related("product", "branch").filter(product__is_active=True)
    if branch is not None:
        queryset = queryset.filter(branch=branch)
    else:
        queryset = queryset.filter(
            branch__in=Branch.objects.filter(is_active=True, is_commissary=False)
        )
    if product_type:
        queryset = queryset.filter(product__product_type=product_type)
    if category:
        queryset = queryset.filter(product__category=category)
    if search:
        queryset = queryset.filter(product__name__icontains=search)

    items = list(queryset.order_by("product__name"))

    total_products = len({i.product_id for i in items})
    critical_items = [i for i in items if i.is_out_of_stock]
    low_items = [i for i in items if not i.is_out_of_stock and i.is_low_stock]
    available_items = [i for i in items if not i.is_out_of_stock and not i.is_low_stock]
    # display_quantity, not the raw field -- these feed the stat cards a
    # person reads (Total Stock / Stock Value), same QA-fix reasoning as
    # Inventory.display_quantity: a negative quantity_on_hand is a real,
    # useful internal signal (oversold stock), but summing the raw value
    # here would make the displayed total drift below what the
    # individually-displayed (already-clamped) item rows add up to.
    total_stock = sum(i.display_quantity for i in items)
    # Task #137 (client review): quantity_on_hand carries 3 decimal
    # places (apps/inventory/models.py -- materials can be fractional
    # kg/L), so multiplying it by a 2-decimal-place price can leave more
    # than 2 decimal places in the running total (e.g. a 0.125 kg row at
    # P80.00 contributes Decimal('10.000'), not a clean 2dp amount).
    # Django's |floatformat template filter always rounds correctly
    # regardless, so this was never a visibly wrong number on screen --
    # but it's the same unquantized-Decimal pattern as the live
    # transaction/item discount bugs (PRs #169/#170), so it's quantized
    # here too rather than left to rely on the template to paper over it.
    stock_value = sum((i.display_quantity * i.product.price for i in items), Decimal("0.00"))
    stock_value = stock_value.quantize(Decimal("0.01"))

    return {
        "items": items,
        "stats": {
            "total_products": total_products,
            "available_count": len(available_items),
            "low_count": len(low_items),
            "critical_count": len(critical_items),
            "total_stock": total_stock,
            "stock_value": stock_value,
        },
    }


def group_items_by_product(items):
    """Client review (2026-11-09): the Product Availability table showed
    the same product name once per branch (e.g. "8x8x2 Blue Box" repeated
    for Batangas City, Lipa, Alangilan), which reads as crowded/duplicated
    once a category has several products across several branches. Groups
    the flat `items` list (as returned by get_product_availability, one
    Inventory row per product/branch) into one entry per Product, with its
    real per-branch Inventory rows nested underneath for the UI to expand.

    Purely a display reorganization -- doesn't merge, sum-and-save, delete,
    or otherwise touch any Inventory record. Each group's `branches` list
    holds the exact same Inventory objects `items` did, in the same order,
    so Quick Sale/Manual Adjustment (which already act on one Inventory pk
    at a time) are completely unaffected by grouping.

    Preserves `items`' own ordering (get_product_availability already
    orders by product name) -- a product's first appearance determines
    where its group sorts.
    """
    groups = {}
    for item in items:
        group = groups.setdefault(item.product_id, {"product": item.product, "branches": []})
        group["branches"].append(item)

    result = []
    for group in groups.values():
        branches = group["branches"]
        # Severity order matches status_label's own: Out of Stock is more
        # urgent than Low Stock, which is more urgent than Available -- a
        # collapsed group must never read as fine when one of its real
        # branches genuinely isn't, so it always shows its worst branch.
        if any(b.is_out_of_stock for b in branches):
            worst_status, worst_badge = "Out of Stock", "jc-badge-critical"
        elif any(b.is_low_stock for b in branches):
            worst_status, worst_badge = "Low Stock", "jc-badge-low"
        else:
            worst_status, worst_badge = "Available", "jc-badge-adequate"

        result.append(
            {
                "product": group["product"],
                "branches": branches,
                "branch_count": len(branches),
                "total_stock": sum(b.display_quantity for b in branches),
                "worst_status": worst_status,
                "worst_badge": worst_badge,
            }
        )
    return result


def get_branch_inventory(branch, product_type=None, low_stock_only=False):
    """Returns Inventory rows for a branch, optionally filtered by product
    type (Fig. 3-32 Finished Goods vs. Fig. 3-33 Materials share one
    underlying screen, distinguished by this filter) and/or low-stock
    status only (Row 6.4). Excludes discontinued products (Product.
    is_active=False), same exclusion as get_product_availability."""
    queryset = Inventory.objects.select_related("product").filter(
        branch=branch, product__is_active=True
    )

    if product_type:
        queryset = queryset.filter(product__product_type=product_type)

    if low_stock_only:
        # Can't filter is_low_stock (a Python property) at the DB level
        # directly -- evaluated in Python instead. Branch-level inventory
        # counts are small enough that this isn't a real performance
        # concern; revisit with a DB-level annotation if that changes.
        queryset = [item for item in queryset if item.is_low_stock or item.is_out_of_stock]

    return queryset


def get_resource_status_summary(branch=None, product_type=None, product_ids=None):
    """Stock-health snapshot for the Dashboard Home Resource Status KPI
    card (Fig. 3-19) and the Resource Monitoring list underneath it.

    NOTE: the reference design shows four tiers (Critical/Low/Medium/
    Adequate). Inventory only models three real states (is_out_of_stock,
    is_low_stock, and Available -- see Inventory.status_label, Fig.
    3-20/3-32's own three-state badge). Collapsed honestly here rather
    than inventing an unbacked "Medium" cutoff with no threshold behind
    it: Critical == is_out_of_stock, Low == is_low_stock, Adequate ==
    everything else.

    branch=None aggregates across every active, non-commissary branch
    (the Dashboard's "All Branches" state); a specific branch filters to
    just that one.

    product_type=None (the default) includes every product type, same as
    before this parameter existed -- the KPI card's "overall sufficiency"
    is meant to cover everything tracked. Pass a single ProductType value
    or an iterable of them (e.g. Product.MATERIAL_TYPES, to scope this to
    raw materials AND packaging materials -- Dashboard Home's Resource
    Monitoring list, Row N), typically combined with branch=<the
    commissary> since materials only ever have Inventory rows there.

    product_ids=None (the default) applies no further narrowing. Client
    review (2026-11-09): the Commissary Dashboard's "Materials Needing
    Attention" card was showing branch-only POS menu items (Americano,
    Carbonara, Lasagna, ...) as "out of stock" at the commissary -- the
    commissary never produces or stocks those, so an Inventory row for
    one there is a data artifact (e.g. a historical import that created
    a zeroed row per product per branch), not a real shortage. Pass an
    explicit iterable of Product ids (commissary_dashboard builds this
    from MATERIAL products plus FINISHED_GOOD products the commissary has
    actually produced, per ProductionRecord) to scope the summary to only
    what that branch genuinely tracks, without changing this function's
    default behavior for its other callers (Owner/Admin Dashboard Home,
    Resource Consumption, the low-stock alert job, ...), which still want
    every product type at a real sales branch.

    Excludes discontinued products (Product.is_active=False), same
    exclusion as get_product_availability -- a discontinued item
    shouldn't count toward or against the sufficiency percentage.
    """
    from apps.accounts.models import Branch

    queryset = Inventory.objects.select_related("product", "branch").filter(product__is_active=True)
    if branch is not None:
        queryset = queryset.filter(branch=branch)
    else:
        queryset = queryset.filter(
            branch__in=Branch.objects.filter(is_active=True, is_commissary=False)
        )
    if product_type is not None:
        if isinstance(product_type, (list, tuple, set, frozenset)):
            queryset = queryset.filter(product__product_type__in=product_type)
        else:
            queryset = queryset.filter(product__product_type=product_type)
    if product_ids is not None:
        queryset = queryset.filter(product_id__in=product_ids)

    items = list(queryset)
    critical_items = [i for i in items if i.is_out_of_stock]
    low_items = [i for i in items if not i.is_out_of_stock and i.is_low_stock]
    adequate_items = [i for i in items if not i.is_out_of_stock and not i.is_low_stock]
    total = len(items)
    sufficiency_pct = round((len(adequate_items) / total) * 100) if total else 100

    # Critical first, then Low, then Adequate -- matches the reference
    # design's own ordering for the Resource Monitoring list (Fig. 3-19).
    ordered_items = critical_items + low_items + adequate_items

    return {
        "total_items": total,
        "critical_count": len(critical_items),
        "critical_items": critical_items,
        "low_count": len(low_items),
        "low_items": low_items,
        "adequate_count": len(adequate_items),
        "items": ordered_items,
        "sufficiency_pct": sufficiency_pct,
    }


def get_or_create_inventory_row(branch, product, reorder_threshold=None):
    """Ensures a branch has an Inventory row for a product even if no sale
    or adjustment has touched it yet, so it still shows up (at 0) on the
    monitoring screen rather than being invisible until first touched.

    `reorder_threshold`, if given, only applies on first creation (via
    `defaults=`) -- a repeat call for a row that already exists must not
    silently overwrite whatever threshold is already configured there."""
    defaults = {}
    if reorder_threshold is not None:
        defaults["reorder_threshold"] = reorder_threshold
    inventory, _ = Inventory.objects.get_or_create(
        branch=branch, product=product, defaults=defaults
    )
    return inventory


class InventoryServiceError(Exception):
    """Raised for any reason an inventory write (manual adjustment or
    product creation) can't be applied."""


def adjust_stock(inventory, delta, reason="", user=None):
    """Manual Stock Adjustment (Fig. 3-34's action). `delta` is signed --
    positive to add stock (e.g. correcting a miscount), negative to
    remove it. Always logs a traceable InventoryTransaction, same as the
    Week 5 sale-deduction hook, so manual changes are equally auditable.

    `reason` and `user` are recorded on the transaction row (backend gap
    assessment: manual adjustments previously left no "who/why" trail).
    Both stay optional defaults here -- callers like the seed script and
    `quick_sale` (a one-click preset, not an operator-typed reason) don't
    always have a real reason or an acting user to pass -- but the
    Manual Stock Adjustment view itself requires a non-blank reason
    before ever calling this, since that is the one flow this finding is
    actually about.
    """
    try:
        # Decimal, not int: raw-material adjustments can be a fractional
        # kg/liter amount (client raw-data import). str() first so a
        # float delta (already-imprecise) doesn't carry binary-float
        # noise into the Decimal; Decimal(int) and Decimal("whole str")
        # both still work exactly for finished-goods piece counts.
        delta = Decimal(str(delta))
    except (TypeError, ValueError, InvalidOperation):
        raise InventoryServiceError("Enter a number for the adjustment.")

    if delta == 0:
        raise InventoryServiceError("Adjustment cannot be zero.")

    # Coerce the current value too: an Inventory instance built with a
    # string/float quantity_on_hand (e.g. straight from the client import
    # or a freshly-constructed test fixture) keeps that raw Python value
    # in memory until it's reloaded from the DB -- Django only converts
    # it to Decimal on the way in/out of the database, not on assignment.
    current_quantity = Decimal(str(inventory.quantity_on_hand))
    new_quantity = current_quantity + delta
    if new_quantity < 0:
        raise InventoryServiceError(
            f"This would take stock below zero (currently {inventory.quantity_on_hand})."
        )

    inventory.quantity_on_hand = new_quantity
    inventory.save()

    InventoryTransaction.objects.create(
        branch=inventory.branch,
        product=inventory.product,
        movement_type=InventoryTransaction.MovementType.MANUAL_ADJUSTMENT,
        quantity_change=delta,
        reason=reason,
        created_by=user,
    )
    return inventory


def create_product(
    name,
    price,
    product_type,
    reorder_threshold=0,
    branch_ids=None,
    unit_of_measure=None,
    category="",
):
    """Product Inventory Management (Row 6.2): adds a new item to the
    catalog. Kept separate from POS's own catalog-reading logic -- this is
    the write side, restricted to Owner/Admin at the view layer.

    `branch_ids`, if given, provisions an Inventory row (at 0 on hand) for
    exactly those branches -- the product only becomes visible on
    Monitoring and orderable in POS at a branch once it has a real
    Inventory row there (see apps/pos catalog queries). Left as an
    optional, defaulted parameter so existing callers (and the product's
    own creation) are unaffected if no branch is specified -- matches the
    pre-fix behavior of not auto-provisioning anywhere (QA-reported bug:
    a new product silently became orderable at every branch with no
    branch selection at all; this makes that selection explicit instead
    of removing it).

    `reorder_threshold` lives on Inventory, not Product (client raw-data
    import: real reorder points genuinely differ per branch for the same
    material), so it's applied to each Inventory row created here rather
    than to the Product itself -- with nothing to apply it to when
    branch_ids is empty, it's simply unused in that case, same as before.

    `unit_of_measure` (backend gap assessment item 28): optional, falls
    back to the model field's own default (PIECE) when omitted/blank --
    existing callers are unaffected. Validated against the real set of
    choices rather than silently accepted as-is, same reasoning as the
    status validation fix in production.services.advance_stock_transfer_status
    (item 25): an unrecognized value should be a clear, catchable error at
    creation time, not stored silently and discovered later.

    `category` is a POS category-tab concept (COFFEE, PASTA, ...) that
    only ever means anything for a FINISHED_GOOD -- optional, defaults to
    blank like the model field itself. Client review (2026-11-09): this
    used to also accept Product.Category.PACKAGING to tag a MATERIAL
    product Packaging (the Add Product form's old "Category" field,
    dev backlog follow-up to Task #148's Packaging Materials tab) --
    Packaging is now its own Product.ProductType.PACKAGING value, passed
    as `product_type` directly like any other type, so that tagging
    path and its validation no longer exist here.
    """
    from apps.accounts.models import Branch

    name = (name or "").strip()
    if not name:
        raise InventoryServiceError("Product name is required.")

    # Task #111: Product.name has a DB-level case-insensitive uniqueness
    # constraint among active products (unique_active_product_name_ci).
    # Checked here too, ahead of the actual insert, so a duplicate shows up
    # as this function's own clear InventoryServiceError (same as every
    # other validation failure in this function) rather than a raw
    # IntegrityError the view's except clause doesn't catch.
    if Product.objects.filter(name__iexact=name, is_active=True).exists():
        raise InventoryServiceError(f"A product named '{name}' already exists.")

    if unit_of_measure:
        valid_units = {choice.value for choice in Product.UnitOfMeasure}
        if unit_of_measure not in valid_units:
            raise InventoryServiceError(f"'{unit_of_measure}' is not a recognized unit of measure.")

    category = (category or "").strip()

    product = Product.objects.create(
        name=name,
        price=price,
        product_type=product_type,
        category=category,
        **({"unit_of_measure": unit_of_measure} if unit_of_measure else {}),
    )

    if branch_ids:
        for branch in Branch.objects.filter(pk__in=branch_ids):
            get_or_create_inventory_row(branch, product, reorder_threshold=reorder_threshold or 0)

    return product


def find_ledger_discrepancies():
    """Backend gap assessment item 29: Inventory.quantity_on_hand (the
    running balance) and InventoryTransaction (the paired audit ledger)
    are written together in every service function in this app, but
    nothing ever ties them back to each other afterward -- if a future
    code path ever updated one without the other, the drift would go
    undetected indefinitely.

    This sums every InventoryTransaction.quantity_change for each
    (branch, product) pair and compares that total to the Inventory
    row's own quantity_on_hand. A brand-new Inventory row with no
    transactions at all (e.g. one just provisioned via
    get_or_create_inventory_row/create_product's branch_ids) is expected
    to start at 0 either way, so it's naturally not a discrepancy.

    Returns a list of dicts -- empty when everything reconciles cleanly
    -- each with the Inventory row, the ledger's own total, the recorded
    balance, and the signed difference between them (recorded - ledger),
    so a positive difference means the balance is higher than the ledger
    accounts for and a negative one means it's lower.
    """
    from django.db.models import Sum

    discrepancies = []
    for inventory in Inventory.objects.select_related("branch", "product"):
        ledger_total = (
            InventoryTransaction.objects.filter(
                branch=inventory.branch, product=inventory.product
            ).aggregate(total=Sum("quantity_change"))["total"]
            or 0
        )
        if ledger_total != inventory.quantity_on_hand:
            discrepancies.append(
                {
                    "inventory": inventory,
                    "ledger_total": ledger_total,
                    "recorded_balance": inventory.quantity_on_hand,
                    "difference": inventory.quantity_on_hand - ledger_total,
                }
            )
    return discrepancies


def get_low_stock_alerts_for_all_branches():
    """Per-branch low-stock status, across every active branch
    (including the commissary, so raw-material shortages are caught
    too) -- the basis for proactive low-stock alerting (backend gap
    assessment item 20).

    Reuses get_resource_status_summary's own real detection logic
    rather than building a second, parallel "is this low" check, so this
    can never disagree with what the Dashboard/Monitoring screens
    already show for the same branch.

    Returns only the branches that actually have a critical or low-stock
    item right now, each with that branch's own summary dict merged in.
    """
    from apps.accounts.models import Branch

    alerts = []
    for branch in Branch.objects.filter(is_active=True).order_by("name"):
        summary = get_resource_status_summary(branch=branch)
        if summary["critical_count"] or summary["low_count"]:
            alerts.append({"branch": branch, **summary})
    return alerts
