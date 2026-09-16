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

    def test_owner_can_add_a_product(self, owner_client):
        response = owner_client.post(
            reverse("inventory:product_management"),
            {
                "name": "New Latte",
                "price": "130.00",
                "product_type": Product.ProductType.FINISHED_GOOD,
                "reorder_threshold": "10",
            },
        )
        assert response.status_code == 302
        assert Product.objects.filter(name="New Latte").exists()

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
