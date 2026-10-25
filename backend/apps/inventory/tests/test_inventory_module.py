"""
Tests for Week 6: Inventory Monitoring (Row 6.1), Product Inventory
Management (Row 6.2), Finished Goods & Materials filtering (Row 6.3), and
Low-Stock Alert Triggering (Row 6.4).
"""

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
        product = Product.objects.create(name="Widget", price="10.00", reorder_threshold=0)
        inv = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=1)
        assert inv.is_low_stock is False

    def test_stock_at_or_below_threshold_is_low_stock(self, branch):
        product = Product.objects.create(name="Widget", price="10.00", reorder_threshold=5)
        inv = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=5)
        assert inv.is_low_stock is True
        assert inv.status_label == "Low Stock"

    def test_zero_stock_is_out_of_stock_not_low_stock(self, branch):
        product = Product.objects.create(name="Widget", price="10.00", reorder_threshold=5)
        inv = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=0)
        assert inv.is_out_of_stock is True
        assert inv.is_low_stock is False  # out-of-stock is the more urgent, distinct status
        assert inv.status_label == "Out of Stock"

    def test_stock_above_threshold_is_available(self, branch):
        product = Product.objects.create(name="Widget", price="10.00", reorder_threshold=5)
        inv = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=20)
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
        low = Product.objects.create(name="Low Item", price="10.00", reorder_threshold=5)
        healthy = Product.objects.create(name="Healthy Item", price="10.00", reorder_threshold=5)
        Inventory.objects.create(branch=branch, product=low, quantity_on_hand=2)
        Inventory.objects.create(branch=branch, product=healthy, quantity_on_hand=50)

        result = services.get_branch_inventory(branch, low_stock_only=True)
        assert len(result) == 1
        assert result[0].product == low

    def test_low_stock_only_includes_out_of_stock_items(self, branch):
        product = Product.objects.create(name="Empty", price="10.00", reorder_threshold=5)
        Inventory.objects.create(branch=branch, product=product, quantity_on_hand=0)

        result = services.get_branch_inventory(branch, low_stock_only=True)
        assert len(result) == 1


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


class TestCreateProduct:
    def test_creates_a_product_with_given_fields(self):
        product = services.create_product(
            "New Item", "99.00", Product.ProductType.FINISHED_GOOD, reorder_threshold=10
        )
        assert product.name == "New Item"
        assert product.reorder_threshold == 10

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
        low = Product.objects.create(name="Low Item", price="10.00", reorder_threshold=5)
        healthy = Product.objects.create(name="Healthy Item", price="10.00", reorder_threshold=5)
        Inventory.objects.create(branch=branch, product=low, quantity_on_hand=1)
        Inventory.objects.create(branch=branch, product=healthy, quantity_on_hand=99)

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


class TestAdjustStockView:
    def test_branch_staff_cannot_adjust_stock(self, staff_client, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        inv = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=10)

        response = staff_client.get(reverse("inventory:adjust_stock", args=[inv.pk]))
        assert response.status_code == 403

    def test_owner_can_adjust_stock(self, owner_client, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        inv = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=10)

        response = owner_client.post(
            reverse("inventory:adjust_stock", args=[inv.pk]), {"delta": "5"}
        )
        assert response.status_code == 302

        inv.refresh_from_db()
        assert inv.quantity_on_hand == 15

    def test_invalid_adjustment_shows_error_on_page(self, owner_client, branch):
        product = Product.objects.create(name="Widget", price="10.00")
        inv = Inventory.objects.create(branch=branch, product=product, quantity_on_hand=2)

        response = owner_client.post(
            reverse("inventory:adjust_stock", args=[inv.pk]), {"delta": "-10"}
        )
        assert response.status_code == 200
        assert b"below zero" in response.content


class TestResourceStatusSummary:
    """Dashboard Home batch: Resource Status KPI card + Resource
    Monitoring list (Fig. 3-19). Confirms the honest 3-tier collapse
    (Critical/Low/Adequate) documented in get_resource_status_summary."""

    def test_collapses_to_three_real_tiers_with_correct_counts(self, branch):
        critical_product = Product.objects.create(
            name="Cashew Nuts", price="650.00", reorder_threshold=10
        )
        low_product = Product.objects.create(name="Butter", price="280.00", reorder_threshold=10)
        adequate_product = Product.objects.create(name="Flour", price="45.00", reorder_threshold=10)
        Inventory.objects.create(branch=branch, product=critical_product, quantity_on_hand=0)
        Inventory.objects.create(branch=branch, product=low_product, quantity_on_hand=5)
        Inventory.objects.create(branch=branch, product=adequate_product, quantity_on_hand=50)

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
        product = Product.objects.create(name="Sugar", price="55.00", reorder_threshold=10)
        Inventory.objects.create(branch=branch, product=product, quantity_on_hand=50)
        Inventory.objects.create(branch=commissary, product=product, quantity_on_hand=200)

        summary = services.get_resource_status_summary(branch=None)

        assert summary["total_items"] == 1  # commissary excluded

    def test_empty_inventory_defaults_to_full_sufficiency_not_a_crash(self, branch):
        summary = services.get_resource_status_summary(branch=branch)
        assert summary["total_items"] == 0
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
        cashew = Product.objects.create(name="Cashew Nuts", price="650.00", reorder_threshold=10)
        flour = Product.objects.create(name="Flour", price="45.00", reorder_threshold=10)
        Inventory.objects.create(branch=branch, product=cashew, quantity_on_hand=0)
        Inventory.objects.create(branch=other_branch, product=flour, quantity_on_hand=50)

        response = owner_client.get(reverse("inventory:product_management"))
        stats = response.context["stats"]
        assert stats["total_products"] == 2
        assert stats["critical_count"] == 1
        assert stats["available_count"] == 1
        assert stats["total_stock"] == 50

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
