"""
Tests for Week 6: Inventory Monitoring (Row 6.1), Product Inventory
Management (Row 6.2), Finished Goods & Materials filtering (Row 6.3), and
Low-Stock Alert Triggering (Row 6.4).
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounts.models import Branch, Role, User
from apps.inventory import services
from apps.inventory.models import Inventory, InventoryTransaction, Product
from conftest import verify_otp_for_client

pytestmark = pytest.mark.django_db


@pytest.fixture
def branch():
    return Branch.objects.create(name="Lipa City", code="LIPA")


@pytest.fixture
def other_branch():
    return Branch.objects.create(name="Batangas City", code="BATANGAS")


@pytest.fixture
def owner(branch):
    role = Role.objects.create(name=Role.OWNER_ADMIN)
    return User.objects.create_user(username="owner1", password="testpass123", role=role)


@pytest.fixture
def branch_staff(branch):
    role = Role.objects.create(name=Role.BRANCH_STAFF)
    return User.objects.create_user(
        username="staff1", password="testpass123", role=role, branch=branch
    )


@pytest.fixture
def owner_client(client, owner, branch):
    client.force_login(owner)
    verify_otp_for_client(client, owner)
    session = client.session
    session["selected_branch_id"] = branch.pk
    session.save()
    return client


@pytest.fixture
def staff_client(client, branch_staff, branch):
    client.force_login(branch_staff)
    session = client.session
    session["selected_branch_id"] = branch.pk
    session.save()
    return client


class TestInventoryModelStatus:
    def test_zero_threshold_never_flags_low_stock(self, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        inv = Inventory.objects.create(
            branch=branch, product=product, quantity_on_hand=1, reorder_threshold=0
        )
        assert inv.is_low_stock is False

    def test_stock_at_or_below_threshold_is_low_stock(self, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        inv = Inventory.objects.create(
            branch=branch, product=product, quantity_on_hand=5, reorder_threshold=5
        )
        assert inv.is_low_stock is True
        assert inv.status_label == "Low Stock"

    def test_zero_stock_is_out_of_stock_not_low_stock(self, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        inv = Inventory.objects.create(
            branch=branch, product=product, quantity_on_hand=0, reorder_threshold=5
        )
        assert inv.is_out_of_stock is True
        assert inv.is_low_stock is False  # out-of-stock is the more urgent, distinct status
        assert inv.status_label == "Out of Stock"

    def test_stock_above_threshold_is_available(self, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        inv = Inventory.objects.create(
            branch=branch, product=product, quantity_on_hand=20, reorder_threshold=5
        )
        assert inv.status_label == "Available"

    def test_negative_quantity_displays_as_zero(self, branch):
        """QA fix: a negative quantity_on_hand (the sale-deduction hook
        deliberately allows overselling to drive it below zero, as a
        signal for the AI risk module) must not be shown to a person as
        a raw negative number -- display_quantity clamps it to 0 while
        the real field stays negative for internal logic."""
        product = Product.objects.create(name="Widget", price="10.00")
        inv = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=-3)
        assert inv.quantity_on_hand == -3  # real value preserved
        assert inv.display_quantity == 0  # display clamps
        assert inv.is_out_of_stock is True  # status logic still uses the real value

    def test_positive_quantity_display_matches_real_value(self, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        inv = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=7)
        assert inv.display_quantity == 7

    def test_fractional_quantity_and_threshold_work_like_the_clients_real_data(self, branch):
        """Client raw-data import: Raw_Materials.xlsx tracks real
        materials to a fractional kg/liter (e.g. Ice: 0.3kg on hand,
        0.5kg reorder point) -- status logic must compare correctly on
        these Decimal values, not just whole numbers."""
        from decimal import Decimal

        material = Product.objects.create(
            name="Ice", price="6.00", product_type=Product.ProductType.MATERIAL
        )
        inv = Inventory.objects.create(
            branch=branch,
            product=material,
            quantity_on_hand=Decimal("0.3"),
            reorder_threshold=Decimal("0.5"),
        )
        assert inv.is_low_stock is True
        assert inv.is_out_of_stock is False
        assert inv.status_label == "Low Stock"


class TestGetBranchInventory:
    def test_only_returns_items_for_the_given_branch(self, branch, other_branch):
        product = Product.objects.create(name="Widget", price="10.00")
        Inventory.objects.create(branch=branch, product=product, quantity_on_hand=5)
        Inventory.objects.create(branch=other_branch, product=product, quantity_on_hand=99)

        items = services.get_branch_inventory(branch)
        assert len(items) == 1
        assert items[0].branch == branch

    def test_filters_by_product_type(self, branch):
        finished = Product.objects.create(
            name="Latte", price="125.00", product_type=Product.ProductType.FINISHED_GOOD
        )
        material = Product.objects.create(
            name="Flour", price="50.00", product_type=Product.ProductType.MATERIAL
        )
        Inventory.objects.create(branch=branch, product=finished, quantity_on_hand=10)
        Inventory.objects.create(branch=branch, product=material, quantity_on_hand=20)

        materials_only = services.get_branch_inventory(
            branch, product_type=Product.ProductType.MATERIAL
        )
        assert len(materials_only) == 1
        assert materials_only[0].product == material

    def test_low_stock_only_excludes_healthy_stock(self, branch):
        low = Product.objects.create(name="Low Item", price="10.00")
        healthy = Product.objects.create(name="Healthy Item", price="10.00")
        Inventory.objects.create(
            branch=branch, product=low, quantity_on_hand=2, reorder_threshold=5
        )
        Inventory.objects.create(
            branch=branch, product=healthy, quantity_on_hand=50, reorder_threshold=5
        )

        result = services.get_branch_inventory(branch, low_stock_only=True)
        assert len(result) == 1
        assert result[0].product == low

    def test_low_stock_only_includes_out_of_stock_items(self, branch):
        product = Product.objects.create(name="Empty", price="10.00")
        Inventory.objects.create(
            branch=branch, product=product, quantity_on_hand=0, reorder_threshold=5
        )

        result = services.get_branch_inventory(branch, low_stock_only=True)
        assert len(result) == 1

    def test_discontinued_product_is_excluded(self, branch):
        """Backend gap assessment: a discontinued product (is_active=
        False) with leftover stock was still showing up here as if it
        were a normal, orderable item."""
        discontinued = Product.objects.create(
            name="Discontinued Item", price="10.00", is_active=False
        )
        active = Product.objects.create(name="Active Item", price="10.00")
        Inventory.objects.create(branch=branch, product=discontinued, quantity_on_hand=25)
        Inventory.objects.create(branch=branch, product=active, quantity_on_hand=10)

        result = services.get_branch_inventory(branch)
        assert len(result) == 1
        assert result[0].product == active


class TestAdjustStock:
    def test_positive_adjustment_increases_stock(self, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        inv = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=10)

        services.adjust_stock(inv, 5)

        inv.refresh_from_db()
        assert inv.quantity_on_hand == 15

    def test_negative_adjustment_decreases_stock(self, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        inv = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=10)

        services.adjust_stock(inv, -3)

        inv.refresh_from_db()
        assert inv.quantity_on_hand == 7

    def test_adjustment_below_zero_is_rejected(self, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        inv = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=2)

        with pytest.raises(services.InventoryServiceError, match="below zero"):
            services.adjust_stock(inv, -5)

        inv.refresh_from_db()
        assert inv.quantity_on_hand == 2  # unchanged

    def test_zero_adjustment_is_rejected(self, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        inv = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=10)

        with pytest.raises(services.InventoryServiceError, match="cannot be zero"):
            services.adjust_stock(inv, 0)

    def test_adjustment_logs_a_manual_adjustment_transaction(self, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        inv = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=10)

        services.adjust_stock(inv, 5)

        movement = InventoryTransaction.objects.get(branch=branch, product=product)
        assert movement.movement_type == InventoryTransaction.MovementType.MANUAL_ADJUSTMENT
        assert movement.quantity_change == 5

    def test_adjustment_records_reason_and_acting_user(self, branch, owner):
        """Backend gap assessment: manual adjustments previously left no
        record of who made them or why."""
        product = Product.objects.create(name="Widget", price="10.00")
        inv = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=10)

        services.adjust_stock(inv, 5, reason="Corrected after physical count", user=owner)

        movement = InventoryTransaction.objects.get(branch=branch, product=product)
        assert movement.reason == "Corrected after physical count"
        assert movement.created_by == owner

    def test_adjustment_without_reason_or_user_still_works(self, branch):
        """Backward compatible: existing callers (seed scripts, Quick
        Sale's preset reason with no explicit user) that don't pass
        these keep working, just with a blank/null audit trail."""
        product = Product.objects.create(name="Widget", price="10.00")
        inv = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=10)

        services.adjust_stock(inv, 5)

        movement = InventoryTransaction.objects.get(branch=branch, product=product)
        assert movement.reason == ""
        assert movement.created_by is None

    def test_fractional_adjustment_is_accepted_for_raw_materials(self, branch):
        """Client raw-data import: quantity_on_hand is Decimal, not
        Integer -- a fractional kg/liter correction (e.g. a physical
        recount of a material) must work, not just whole-piece counts."""
        from decimal import Decimal

        material = Product.objects.create(
            name="White sugar", price="82.00", product_type=Product.ProductType.MATERIAL
        )
        inv = Inventory.objects.create(branch=branch, product=material, quantity_on_hand="10.5")

        services.adjust_stock(inv, "-0.8")

        inv.refresh_from_db()
        assert inv.quantity_on_hand == Decimal("9.7")
        movement = InventoryTransaction.objects.get(branch=branch, product=material)
        assert movement.quantity_change == Decimal("-0.8")

    def test_non_numeric_adjustment_is_rejected(self, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        inv = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=10)

        with pytest.raises(services.InventoryServiceError, match="Enter a number"):
            services.adjust_stock(inv, "not-a-number")


class TestGetLowStockAlertsForAllBranches:
    """Backend gap assessment item 20: the data behind proactive
    low-stock alerting. Reuses get_resource_status_summary's own
    detection, so this can never disagree with what the Dashboard/
    Monitoring screens show for the same branch."""

    def test_branch_with_nothing_low_is_excluded(self, branch):
        healthy = Product.objects.create(name="Flour", price="45.00")
        Inventory.objects.create(
            branch=branch, product=healthy, quantity_on_hand=50, reorder_threshold=10
        )

        alerts = services.get_low_stock_alerts_for_all_branches()
        assert alerts == []

    def test_branch_with_a_critical_item_is_included(self, branch, other_branch):
        healthy = Product.objects.create(name="Flour", price="45.00")
        Inventory.objects.create(
            branch=other_branch, product=healthy, quantity_on_hand=50, reorder_threshold=10
        )

        critical = Product.objects.create(name="Cashews", price="650.00")
        Inventory.objects.create(
            branch=branch, product=critical, quantity_on_hand=0, reorder_threshold=10
        )

        alerts = services.get_low_stock_alerts_for_all_branches()
        assert len(alerts) == 1
        assert alerts[0]["branch"] == branch
        assert alerts[0]["critical_count"] == 1

    def test_includes_the_commissary_branch_too(self):
        """Raw-material shortages at the commissary matter just as much
        as finished-good shortages at a native branch."""
        commissary = Branch.objects.create(name="Central Kitchen", code="CK", is_commissary=True)
        material = Product.objects.create(
            name="Milk",
            price="80.00",
            product_type=Product.ProductType.MATERIAL,
        )
        Inventory.objects.create(
            branch=commissary, product=material, quantity_on_hand=5, reorder_threshold=20
        )

        alerts = services.get_low_stock_alerts_for_all_branches()
        assert len(alerts) == 1
        assert alerts[0]["branch"] == commissary


class TestFindLedgerDiscrepancies:
    """Backend gap assessment item 29: a periodic check summing
    InventoryTransaction deltas and comparing the total to
    Inventory.quantity_on_hand, since the two are written together in
    every service function but nothing ties them back to each other
    afterward."""

    def test_an_inventory_row_built_up_entirely_through_adjust_stock_reconciles_cleanly(
        self, branch
    ):
        product = Product.objects.create(name="Widget", price="10.00")
        # Starts at 0 with no transaction -- a brand-new row with
        # nothing in the ledger yet is not itself a discrepancy, since
        # 0 == sum([]).
        inv = services.get_or_create_inventory_row(branch, product)
        services.adjust_stock(inv, 10)
        services.adjust_stock(inv, -3)

        assert services.find_ledger_discrepancies() == []

    def test_detects_a_balance_that_drifted_from_its_own_ledger(self, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        inv = services.get_or_create_inventory_row(branch, product)
        services.adjust_stock(inv, 10)  # ledger now sums to 10, balance is 10

        # Simulate the exact bug this check exists to catch: some future
        # code path updates quantity_on_hand directly without writing a
        # matching InventoryTransaction.
        inv.quantity_on_hand = 25
        inv.save()

        discrepancies = services.find_ledger_discrepancies()
        assert len(discrepancies) == 1
        entry = discrepancies[0]
        assert entry["inventory"] == inv
        assert entry["ledger_total"] == 10
        assert entry["recorded_balance"] == 25
        assert entry["difference"] == 15

    def test_multiple_branches_and_products_are_each_checked_independently(
        self, branch, other_branch
    ):
        product = Product.objects.create(name="Widget", price="10.00")
        clean_inv = services.get_or_create_inventory_row(branch, product)
        services.adjust_stock(clean_inv, 5)

        drifted_inv = services.get_or_create_inventory_row(other_branch, product)
        services.adjust_stock(drifted_inv, 5)
        drifted_inv.quantity_on_hand = 999
        drifted_inv.save()

        discrepancies = services.find_ledger_discrepancies()
        assert len(discrepancies) == 1
        assert discrepancies[0]["inventory"] == drifted_inv


class TestCreateProduct:
    def test_creates_a_product_with_given_fields(self):
        product = services.create_product("New Item", "99.00", Product.ProductType.FINISHED_GOOD)
        assert product.name == "New Item"

    def test_reorder_threshold_applies_to_each_provisioned_branchs_inventory_row(
        self, branch, other_branch
    ):
        """reorder_threshold now lives on Inventory, not Product (client
        raw-data import: real reorder points differ per branch), so it's
        applied to every Inventory row create_product provisions."""
        product = services.create_product(
            "New Item",
            "99.00",
            Product.ProductType.FINISHED_GOOD,
            reorder_threshold=10,
            branch_ids=[branch.pk, other_branch.pk],
        )
        assert Inventory.objects.get(branch=branch, product=product).reorder_threshold == 10
        assert Inventory.objects.get(branch=other_branch, product=product).reorder_threshold == 10

    def test_blank_name_is_rejected(self):
        with pytest.raises(services.InventoryServiceError, match="name is required"):
            services.create_product("   ", "10.00", Product.ProductType.FINISHED_GOOD)

    def test_no_branch_ids_provisions_nowhere(self):
        """Backward-compatible default: a product created without
        branch_ids gets no Inventory row anywhere, same as before the
        QA fix -- existing callers that don't pass it keep working."""
        product = services.create_product(
            "Unprovisioned", "10.00", Product.ProductType.FINISHED_GOOD
        )
        assert not Inventory.objects.filter(product=product).exists()

    def test_branch_ids_provisions_only_those_branches(self, branch, other_branch):
        product = services.create_product(
            "Branch Scoped Item",
            "10.00",
            Product.ProductType.FINISHED_GOOD,
            branch_ids=[branch.pk],
        )
        assert Inventory.objects.filter(branch=branch, product=product).exists()
        assert not Inventory.objects.filter(branch=other_branch, product=product).exists()
        inv = Inventory.objects.get(branch=branch, product=product)
        assert inv.quantity_on_hand == 0

    def test_defaults_to_piece_when_unit_of_measure_omitted(self):
        """Backend gap assessment item 28: existing callers that don't
        pass unit_of_measure at all must be unaffected."""
        product = services.create_product("New Item", "10.00", Product.ProductType.FINISHED_GOOD)
        assert product.unit_of_measure == Product.UnitOfMeasure.PIECE

    def test_accepts_a_valid_unit_of_measure(self):
        product = services.create_product(
            "Flour",
            "45.00",
            Product.ProductType.MATERIAL,
            unit_of_measure=Product.UnitOfMeasure.KILOGRAM,
        )
        assert product.unit_of_measure == Product.UnitOfMeasure.KILOGRAM

    def test_rejects_an_unrecognized_unit_of_measure(self):
        with pytest.raises(services.InventoryServiceError, match="not a recognized unit"):
            services.create_product(
                "Flour",
                "45.00",
                Product.ProductType.MATERIAL,
                unit_of_measure="BOGUS",
            )

    def test_rejects_a_duplicate_name_among_active_products(self):
        """Task #111: a real duplicate pair ("Seasalt Latte") was found
        live, with nothing stopping it. create_product's own pre-check
        should catch this before it ever reaches the DB constraint, with a
        clear, catchable error message."""
        services.create_product("Spanish Latte", "125.00", Product.ProductType.FINISHED_GOOD)
        with pytest.raises(services.InventoryServiceError, match="already exists"):
            services.create_product("Spanish Latte", "130.00", Product.ProductType.FINISHED_GOOD)

    def test_rejects_a_duplicate_name_case_insensitively(self):
        """kahero_integration's own import matching (_resolve_product)
        already treats names case-insensitively, so two active products
        differing only by case would be exactly the kind of pair that
        pipeline can't disambiguate -- must be rejected too."""
        services.create_product("Flour", "45.00", Product.ProductType.MATERIAL)
        with pytest.raises(services.InventoryServiceError, match="already exists"):
            services.create_product("flour", "45.00", Product.ProductType.MATERIAL)

    def test_allows_reusing_the_name_of_a_discontinued_product(self):
        """A discontinued product's name is free to reuse -- discontinuing
        something and later introducing a genuine replacement under the
        same name is a legitimate catalog change, not a duplicate. This is
        also what lets the constraint coexist with the real, already-
        resolved live data (ids 3/4, both literally "Seasalt Latte", one
        deactivated rather than deleted)."""
        old = services.create_product("Seasalt Latte", "112.00", Product.ProductType.FINISHED_GOOD)
        old.is_active = False
        old.save()

        new = services.create_product("Seasalt Latte", "120.00", Product.ProductType.FINISHED_GOOD)
        assert new.pk != old.pk

    def test_db_constraint_is_the_backstop_even_bypassing_the_service(self):
        """The service-level pre-check above isn't the only thing
        enforcing this -- the DB constraint itself (unique_active_product_name_ci)
        must reject a duplicate created directly through the model, the
        same way Django admin's own product creation form would hit it."""
        from django.db import IntegrityError, transaction

        Product.objects.create(name="Americano", price="150.00")
        with pytest.raises(IntegrityError), transaction.atomic():
            Product.objects.create(name="americano", price="150.00")


class TestInventoryMonitoringView:
    def test_branch_staff_can_view_monitoring_for_their_own_branch(self, staff_client, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        Inventory.objects.create(branch=branch, product=product, quantity_on_hand=5)

        response = staff_client.get(reverse("inventory:monitoring"))
        assert response.status_code == 200
        assert b"Widget" in response.content

    def test_owner_can_view_monitoring(self, owner_client, branch):
        response = owner_client.get(reverse("inventory:monitoring"))
        assert response.status_code == 200

    def test_no_selected_branch_redirects_to_branch_selection(self, client, branch_staff):
        client.force_login(branch_staff)  # no selected_branch_id in session
        response = client.get(reverse("inventory:monitoring"))
        assert response.status_code == 302
        assert response.url == reverse("accounts:select_branch")

    def test_negative_stock_displays_as_zero_not_a_raw_negative(self, staff_client, branch):
        """QA fix (SYSTEM_DOCUMENTATION: 'the system displays -1 instead
        of 0'): the Monitoring page must never render a raw negative
        quantity. Asserted against the rendered table cell specifically
        (">-1<") rather than scanning the whole page for "-1", since
        that substring can appear incidentally elsewhere (CSS, asset
        URLs) with no relation to inventory data."""
        product = Product.objects.create(name="Oversold Item", price="10.00")
        Inventory.objects.create(branch=branch, product=product, quantity_on_hand=-1)

        response = staff_client.get(reverse("inventory:monitoring"))
        assert b"Oversold Item" in response.content
        assert b">-1<" not in response.content
        assert response.context["items"][0].display_quantity == 0

    def test_low_stock_toggle_filters_results(self, staff_client, branch):
        low = Product.objects.create(name="Low Item", price="10.00")
        healthy = Product.objects.create(name="Healthy Item", price="10.00")
        Inventory.objects.create(
            branch=branch, product=low, quantity_on_hand=1, reorder_threshold=5
        )
        Inventory.objects.create(
            branch=branch, product=healthy, quantity_on_hand=99, reorder_threshold=5
        )

        response = staff_client.get(reverse("inventory:monitoring"), {"low_stock": "1"})
        assert b"Low Item" in response.content
        assert b"Healthy Item" not in response.content


class TestProductManagementView:
    def test_branch_staff_cannot_access_product_management(self, staff_client):
        response = staff_client.get(reverse("inventory:product_management"))
        assert response.status_code == 403

    def test_owner_can_view_product_management(self, owner_client):
        response = owner_client.get(reverse("inventory:product_management"))
        assert response.status_code == 200

    def test_stock_value_card_shows_two_decimal_places(self, owner_client, branch):
        """Task #137 (client review): currency/number formatting
        standardization. "Stock Value" used |floatformat:0 (a whole peso
        amount) while every other currency figure on this screen and
        across the Dashboard/Reports/Resource Management screens uses
        |floatformat:2."""
        product = Product.objects.create(name="Loose Tea", price="80.00")
        Inventory.objects.create(branch=branch, product=product, quantity_on_hand=Decimal("0.125"))

        content = owner_client.get(reverse("inventory:product_management")).content.decode()

        # Before the fix: |floatformat:0 rounded 10.00 down/up to a bare
        # "10" with no decimal point at all.
        assert "&#8369;10.00" in content

    def test_item_row_price_shows_two_decimal_places(self, owner_client, branch):
        """The per-row unit price was rendered with no filter at all
        (relying on DecimalField's own storage precision to happen to
        look right) rather than the same |floatformat:2 used everywhere
        else -- brought in line for consistency and so a future
        unquantized/annotated price value can't slip through unrounded."""
        product = Product.objects.create(name="Spanish Latte", price="95.50")
        Inventory.objects.create(branch=branch, product=product, quantity_on_hand=5)

        content = owner_client.get(reverse("inventory:product_management")).content.decode()

        assert "&#8369;95.50" in content

    def test_owner_can_add_a_product(self, owner_client, branch):
        response = owner_client.post(
            reverse("inventory:product_management"),
            {
                "name": "New Latte",
                "price": "130.00",
                "product_type": Product.ProductType.FINISHED_GOOD,
                "reorder_threshold": "10",
                "branches": [branch.pk],
            },
        )
        assert response.status_code == 302
        assert Product.objects.filter(name="New Latte").exists()

    def test_owner_can_set_the_unit_of_measure_when_adding_a_product(self, owner_client, branch):
        response = owner_client.post(
            reverse("inventory:product_management"),
            {
                "name": "Sugar",
                "price": "60.00",
                "product_type": Product.ProductType.MATERIAL,
                "reorder_threshold": "5",
                "unit_of_measure": Product.UnitOfMeasure.KILOGRAM,
                "branches": [branch.pk],
            },
        )
        assert response.status_code == 302
        product = Product.objects.get(name="Sugar")
        assert product.unit_of_measure == Product.UnitOfMeasure.KILOGRAM

    def test_adding_a_product_provisions_it_only_at_the_selected_branch(
        self, owner_client, branch, other_branch
    ):
        """QA fix: a product must be provisioned only where explicitly
        selected, not silently become available at every branch."""
        owner_client.post(
            reverse("inventory:product_management"),
            {
                "name": "Branch Exclusive Item",
                "price": "99.00",
                "product_type": Product.ProductType.FINISHED_GOOD,
                "branches": [branch.pk],
            },
        )
        product = Product.objects.get(name="Branch Exclusive Item")
        assert Inventory.objects.filter(branch=branch, product=product).exists()
        assert not Inventory.objects.filter(branch=other_branch, product=product).exists()

    def test_no_branch_selected_shows_error_and_does_not_create(self, owner_client):
        response = owner_client.post(
            reverse("inventory:product_management"),
            {
                "name": "No Branch Item",
                "price": "50.00",
                "product_type": Product.ProductType.FINISHED_GOOD,
            },
        )
        assert response.status_code == 200
        assert b"Select at least one branch" in response.content
        assert not Product.objects.filter(name="No Branch Item").exists()

    def test_blank_name_shows_error_and_does_not_create(self, owner_client):
        response = owner_client.post(
            reverse("inventory:product_management"),
            {"name": "", "price": "10.00", "product_type": Product.ProductType.FINISHED_GOOD},
        )
        assert response.status_code == 200
        assert not Product.objects.filter(price="10.00").exists()


class TestProductAvailabilityStockValuePrecision:
    """Task #137 (client review): currency/number formatting
    standardization. get_product_availability's stock_value sums
    Inventory.quantity_on_hand (3 decimal places -- materials can be
    fractional kg/L) times Product.price (2 decimal places), which can
    leave more than 2 decimal places in the running total -- the same
    unquantized-Decimal pattern as the live discount bugs fixed in PRs
    #169/#170. The template's |floatformat:2 always rounds the display
    correctly regardless, so this was never visibly wrong on screen, but
    it's quantized at the source now rather than left to rely on the
    template to paper over it."""

    def test_stock_value_is_quantized_to_two_decimal_places(self, branch):
        product = Product.objects.create(name="Loose Tea", price=Decimal("80.00"))
        Inventory.objects.create(branch=branch, product=product, quantity_on_hand=Decimal("0.125"))

        result = services.get_product_availability(branch=branch)

        # 0.125 * 80.00 = 10.000 unquantized -- would carry 3 decimal
        # places without the fix.
        assert result["stats"]["stock_value"] == Decimal("10.00")
        assert result["stats"]["stock_value"].as_tuple().exponent == -2

    def test_stock_value_with_no_inventory_is_zero_not_a_crash(self, branch):
        """sum() over an empty list defaults to int 0, which has no
        .quantize() method -- must use a Decimal("0.00") start value."""
        result = services.get_product_availability(branch=branch)
        assert result["stats"]["stock_value"] == Decimal("0.00")

    def test_stock_value_quantization_matches_item_level_display(self, branch):
        """Realistic multi-item mix: a fractional-quantity material plus
        a whole-unit finished good, matching the Product Availability
        screen's real combination of row types."""
        material = Product.objects.create(
            name="Fresh Milk",
            price=Decimal("95.50"),
            product_type=Product.ProductType.MATERIAL,
        )
        finished_good = Product.objects.create(name="Croissant", price=Decimal("65.00"))
        Inventory.objects.create(branch=branch, product=material, quantity_on_hand=Decimal("2.750"))
        Inventory.objects.create(
            branch=branch, product=finished_good, quantity_on_hand=Decimal("12")
        )

        result = services.get_product_availability(branch=branch)

        # 2.750 * 95.50 = 262.625, 12 * 65.00 = 780.00 -> 1042.625 raw,
        # 1042.63 after proper decimal rounding (round-half-up, matching
        # Decimal.quantize's default ROUND_HALF_EVEN... verified below).
        expected = (
            Decimal("2.750") * Decimal("95.50") + Decimal("12") * Decimal("65.00")
        ).quantize(Decimal("0.01"))
        assert result["stats"]["stock_value"] == expected
        assert result["stats"]["stock_value"].as_tuple().exponent == -2


class TestAdjustStockView:
    def test_branch_staff_cannot_adjust_stock(self, staff_client, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        inv = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=10)

        response = staff_client.get(reverse("inventory:adjust_stock", args=[inv.pk]))
        assert response.status_code == 403

    def test_owner_can_adjust_stock(self, owner_client, owner, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        inv = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=10)

        response = owner_client.post(
            reverse("inventory:adjust_stock", args=[inv.pk]),
            {"delta": "5", "reason": "Physical count correction"},
        )
        assert response.status_code == 302

        inv.refresh_from_db()
        assert inv.quantity_on_hand == 15

        movement = InventoryTransaction.objects.get(branch=branch, product=product)
        assert movement.reason == "Physical count correction"
        assert movement.created_by == owner

    def test_invalid_adjustment_shows_error_on_page(self, owner_client, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        inv = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=2)

        response = owner_client.post(
            reverse("inventory:adjust_stock", args=[inv.pk]),
            {"delta": "-10", "reason": "Testing"},
        )
        assert response.status_code == 200
        assert b"below zero" in response.content

    def test_blank_reason_is_rejected_and_does_not_adjust_stock(self, owner_client, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        inv = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=10)

        response = owner_client.post(
            reverse("inventory:adjust_stock", args=[inv.pk]), {"delta": "5", "reason": "   "}
        )
        assert response.status_code == 200
        assert b"reason is required" in response.content

        inv.refresh_from_db()
        assert inv.quantity_on_hand == 10
        assert not InventoryTransaction.objects.filter(branch=branch, product=product).exists()


class TestResourceStatusSummary:
    """Dashboard Home batch: Resource Status KPI card + Resource
    Monitoring list (Fig. 3-19). Confirms the honest 3-tier collapse
    (Critical/Low/Adequate) documented in get_resource_status_summary."""

    def test_collapses_to_three_real_tiers_with_correct_counts(self, branch):
        critical_product = Product.objects.create(name="Cashew Nuts", price="650.00")
        low_product = Product.objects.create(name="Butter", price="280.00")
        adequate_product = Product.objects.create(name="Flour", price="45.00")
        Inventory.objects.create(
            branch=branch, product=critical_product, quantity_on_hand=0, reorder_threshold=10
        )
        Inventory.objects.create(
            branch=branch, product=low_product, quantity_on_hand=5, reorder_threshold=10
        )
        Inventory.objects.create(
            branch=branch, product=adequate_product, quantity_on_hand=50, reorder_threshold=10
        )

        summary = services.get_resource_status_summary(branch=branch)

        assert summary["total_items"] == 3
        assert summary["critical_count"] == 1
        assert summary["low_count"] == 1
        assert summary["adequate_count"] == 1
        assert summary["sufficiency_pct"] == 33  # 1 of 3 adequate, rounded
        # Ordering: critical items surface first for the Resource
        # Monitoring list, matching the reference design's own ordering.
        assert summary["items"][0].product == critical_product

    def test_all_branches_aggregates_active_non_commissary_only(self, branch):
        commissary = Branch.objects.create(name="Central Kitchen", code="CK", is_commissary=True)
        product = Product.objects.create(name="Sugar", price="55.00")
        Inventory.objects.create(
            branch=branch, product=product, quantity_on_hand=50, reorder_threshold=10
        )
        Inventory.objects.create(
            branch=commissary, product=product, quantity_on_hand=200, reorder_threshold=10
        )

        summary = services.get_resource_status_summary(branch=None)

        assert summary["total_items"] == 1  # commissary excluded

    def test_empty_inventory_defaults_to_full_sufficiency_not_a_crash(self, branch):
        summary = services.get_resource_status_summary(branch=branch)
        assert summary["total_items"] == 0
        assert summary["sufficiency_pct"] == 100

    def test_discontinued_product_does_not_count_toward_sufficiency(self, branch):
        """A discontinued item shouldn't count toward or against the
        sufficiency percentage shown on the Dashboard KPI card."""
        discontinued = Product.objects.create(
            name="Discontinued Item", price="10.00", is_active=False
        )
        Inventory.objects.create(
            branch=branch, product=discontinued, quantity_on_hand=0, reorder_threshold=10
        )
        adequate = Product.objects.create(name="Flour", price="45.00")
        Inventory.objects.create(
            branch=branch, product=adequate, quantity_on_hand=50, reorder_threshold=10
        )

        summary = services.get_resource_status_summary(branch=branch)
        assert summary["total_items"] == 1
        assert summary["critical_count"] == 0
        assert summary["sufficiency_pct"] == 100


class TestProductAvailabilityEnhancements:
    """Product Availability batch: real cross-branch stock, stats,
    filtering, and the new Quick Sale action, replacing the previous
    bare catalog-only list."""

    def test_shell_context_present(self, owner_client):
        response = owner_client.get(reverse("inventory:product_management"))
        assert response.context["active_nav"] == "availability"

    def test_stats_reflect_real_inventory_across_all_branches(
        self, owner_client, branch, other_branch
    ):
        cashew = Product.objects.create(name="Cashew Nuts", price="650.00")
        flour = Product.objects.create(name="Flour", price="45.00")
        Inventory.objects.create(
            branch=branch, product=cashew, quantity_on_hand=0, reorder_threshold=10
        )
        Inventory.objects.create(
            branch=other_branch, product=flour, quantity_on_hand=50, reorder_threshold=10
        )

        response = owner_client.get(reverse("inventory:product_management"))
        stats = response.context["stats"]
        assert stats["total_products"] == 2
        assert stats["critical_count"] == 1
        assert stats["available_count"] == 1
        assert stats["total_stock"] == 50

    def test_discontinued_product_is_excluded_from_the_list_and_stats(self, owner_client, branch):
        discontinued = Product.objects.create(
            name="Discontinued Item", price="10.00", is_active=False
        )
        Inventory.objects.create(branch=branch, product=discontinued, quantity_on_hand=25)

        response = owner_client.get(reverse("inventory:product_management"))
        assert response.context["stats"]["total_products"] == 0
        assert len(response.context["items"]) == 0
        assert b"Discontinued Item" not in response.content

    def test_total_stock_stat_does_not_go_negative_from_an_oversold_item(
        self, owner_client, branch, other_branch
    ):
        """QA fix: the Total Stock stat card sums display_quantity, not
        the raw field -- an oversold item (negative quantity_on_hand)
        must not drag the displayed total below what the individually-
        displayed (already-clamped) rows add up to."""
        oversold = Product.objects.create(name="Oversold Item", price="10.00")
        healthy = Product.objects.create(name="Healthy Item", price="10.00")
        Inventory.objects.create(branch=branch, product=oversold, quantity_on_hand=-2)
        Inventory.objects.create(branch=other_branch, product=healthy, quantity_on_hand=5)

        response = owner_client.get(reverse("inventory:product_management"))
        stats = response.context["stats"]
        assert stats["total_stock"] == 5  # 0 (clamped) + 5, not -2 + 5

    def test_type_filter_narrows_to_materials_only(self, owner_client, branch):
        cake = Product.objects.create(
            name="Cake", price="140.00", product_type=Product.ProductType.FINISHED_GOOD
        )
        flour = Product.objects.create(
            name="Flour", price="45.00", product_type=Product.ProductType.MATERIAL
        )
        Inventory.objects.create(branch=branch, product=cake, quantity_on_hand=10)
        Inventory.objects.create(branch=branch, product=flour, quantity_on_hand=20)

        response = owner_client.get(reverse("inventory:product_management"), {"type": "MATERIAL"})
        product_names = {i.product.name for i in response.context["items"]}
        assert product_names == {"Flour"}

    def test_category_filter_narrows_to_packaging_materials(self, owner_client, branch):
        """Task #148 (client review, 2026-11-04): 'Packaging vs Food'
        filtering -- a packaging-tagged material shows up under the
        Packaging filter, and a plain (blank-category) material does
        not."""
        cling_wrap = Product.objects.create(
            name="Cling Wrap",
            price="40.00",
            product_type=Product.ProductType.MATERIAL,
            category=Product.Category.PACKAGING,
        )
        flour = Product.objects.create(
            name="Flour", price="45.00", product_type=Product.ProductType.MATERIAL
        )
        Inventory.objects.create(branch=branch, product=cling_wrap, quantity_on_hand=100)
        Inventory.objects.create(branch=branch, product=flour, quantity_on_hand=20)

        response = owner_client.get(
            reverse("inventory:product_management"),
            {"type": "MATERIAL", "category": "PACKAGING"},
        )
        product_names = {i.product.name for i in response.context["items"]}
        assert product_names == {"Cling Wrap"}

    def test_category_filter_narrows_to_raw_materials_food(self, owner_client, branch):
        """The 'Raw Materials (Food)' option is the complement of
        Packaging -- a MATERIAL row with no category tagged -- not a
        real stored category value (see
        services.RAW_MATERIAL_CATEGORY_FILTER)."""
        cling_wrap = Product.objects.create(
            name="Cling Wrap",
            price="40.00",
            product_type=Product.ProductType.MATERIAL,
            category=Product.Category.PACKAGING,
        )
        flour = Product.objects.create(
            name="Flour", price="45.00", product_type=Product.ProductType.MATERIAL
        )
        Inventory.objects.create(branch=branch, product=cling_wrap, quantity_on_hand=100)
        Inventory.objects.create(branch=branch, product=flour, quantity_on_hand=20)

        response = owner_client.get(
            reverse("inventory:product_management"),
            {"type": "MATERIAL", "category": "RAW_MATERIAL"},
        )
        product_names = {i.product.name for i in response.context["items"]}
        assert product_names == {"Flour"}

    def test_category_filter_narrows_finished_goods_by_category(self, owner_client, branch):
        latte = Product.objects.create(
            name="Spanish Latte", price="125.00", category=Product.Category.COFFEE
        )
        lasagna = Product.objects.create(
            name="Lasagna", price="150.00", category=Product.Category.PASTA
        )
        Inventory.objects.create(branch=branch, product=latte, quantity_on_hand=10)
        Inventory.objects.create(branch=branch, product=lasagna, quantity_on_hand=10)

        response = owner_client.get(reverse("inventory:product_management"), {"category": "COFFEE"})
        product_names = {i.product.name for i in response.context["items"]}
        assert product_names == {"Spanish Latte"}

    def test_category_choices_exclude_packaging_on_the_finished_products_tab(self, owner_client):
        response = owner_client.get(reverse("inventory:product_management"))
        values = [value for value, _ in response.context["category_choices"]]
        assert "PACKAGING" not in values
        assert "COFFEE" in values

    def test_category_choices_are_packaging_vs_raw_on_the_materials_tab(self, owner_client):
        response = owner_client.get(reverse("inventory:product_management"), {"type": "MATERIAL"})
        values = [value for value, _ in response.context["category_choices"]]
        assert values == ["PACKAGING", "RAW_MATERIAL"]

    def test_search_filters_by_product_name(self, owner_client, branch):
        latte = Product.objects.create(name="Spanish Latte", price="125.00")
        muffin = Product.objects.create(name="Blueberry Muffin", price="60.00")
        Inventory.objects.create(branch=branch, product=latte, quantity_on_hand=10)
        Inventory.objects.create(branch=branch, product=muffin, quantity_on_hand=10)

        response = owner_client.get(reverse("inventory:product_management"), {"search": "latte"})
        product_names = {i.product.name for i in response.context["items"]}
        assert product_names == {"Spanish Latte"}

    def test_quick_sale_reduces_real_stock_and_logs_a_transaction(self, owner_client, branch):
        product = Product.objects.create(name="Cashew Nuts", price="650.00")
        inventory = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=10)

        response = owner_client.post(
            reverse("inventory:quick_sale", args=[inventory.pk]), {"amount": "2"}
        )
        assert response.status_code == 302
        inventory.refresh_from_db()
        assert inventory.quantity_on_hand == 8
        assert InventoryTransaction.objects.filter(
            branch=inventory.branch, product=inventory.product
        ).exists()

    def test_quick_sale_cannot_go_below_zero(self, owner_client, branch):
        product = Product.objects.create(name="Cashew Nuts", price="650.00")
        inventory = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=1)

        owner_client.post(reverse("inventory:quick_sale", args=[inventory.pk]), {"amount": "5"})
        inventory.refresh_from_db()
        assert inventory.quantity_on_hand == 1  # unchanged -- adjust_stock rejected it

    def test_quick_sale_redirects_to_safe_internal_next_only(self, owner_client, branch):
        product = Product.objects.create(name="Cashew Nuts", price="650.00")
        inventory = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=10)

        malicious = owner_client.post(
            reverse("inventory:quick_sale", args=[inventory.pk]),
            {"amount": "1", "next": "https://evil.example.com/"},
        )
        assert malicious.url == reverse("inventory:product_management")

        safe = owner_client.post(
            reverse("inventory:quick_sale", args=[inventory.pk]),
            {"amount": "1", "next": "/inventory/products/?type=MATERIAL"},
        )
        assert safe.url == "/inventory/products/?type=MATERIAL"

    def test_branch_staff_cannot_quick_sale(self, staff_client, branch):
        product = Product.objects.create(name="Cashew Nuts", price="650.00")
        inventory = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=10)

        response = staff_client.post(
            reverse("inventory:quick_sale", args=[inventory.pk]), {"amount": "1"}
        )
        assert response.status_code == 403
