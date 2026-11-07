"""
Tests for PurchaseOrder/PurchaseOrderItem (Consolidated Task Tracker
"Purchasing/Receiving records model" -- 2026-11-07 scoped options,
Option B): the model, the service layer (create/mark-ordered/receive/
cancel), and the views' role gating.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounts.models import Branch, Role, User
from apps.inventory.models import Inventory, InventoryTransaction, Product
from apps.purchasing.models import PurchaseOrder, PurchaseOrderItem, Supplier
from apps.purchasing.services import (
    PurchasingError,
    cancel_purchase_order,
    create_purchase_order,
    mark_purchase_order_ordered,
    receive_purchase_order,
)
from conftest import verify_otp_for_client

pytestmark = pytest.mark.django_db


@pytest.fixture
def roles():
    return {
        "owner_admin": Role.objects.create(name=Role.OWNER_ADMIN),
        "branch_staff": Role.objects.create(name=Role.BRANCH_STAFF),
        "purchasing": Role.objects.create(name=Role.PURCHASING),
    }


@pytest.fixture
def owner(roles):
    return User.objects.create_user(
        username="owner1", password="testpass123", role=roles["owner_admin"]
    )


@pytest.fixture
def purchasing_staff(roles):
    return User.objects.create_user(
        username="purchasing1", password="testpass123", role=roles["purchasing"]
    )


@pytest.fixture
def branch_staff(roles):
    branch = Branch.objects.create(name="Batangas City", code="BATANGAS")
    return User.objects.create_user(
        username="branchstaff1",
        password="testpass123",
        role=roles["branch_staff"],
        branch=branch,
    )


@pytest.fixture
def branch():
    return Branch.objects.create(name="Lipa City", code="LIPA")


@pytest.fixture
def supplier():
    return Supplier.objects.create(name="Acme Food Supply")


@pytest.fixture
def flour():
    return Product.objects.create(
        name="Flour", price="45.00", product_type=Product.ProductType.MATERIAL
    )


class TestPurchaseOrderModel:
    def test_str_shows_pk_and_supplier(self, supplier, branch, owner):
        order = PurchaseOrder.objects.create(supplier=supplier, branch=branch, created_by=owner)
        assert str(order) == f"PO #{order.pk} — Acme Food Supply"

    def test_defaults_to_draft(self, supplier, branch, owner):
        order = PurchaseOrder.objects.create(supplier=supplier, branch=branch, created_by=owner)
        assert order.status == PurchaseOrder.Status.DRAFT

    def test_total_cost_sums_each_line(self, supplier, branch, owner, flour):
        sugar = Product.objects.create(
            name="Sugar", price="30.00", product_type=Product.ProductType.MATERIAL
        )
        order = PurchaseOrder.objects.create(supplier=supplier, branch=branch, created_by=owner)
        PurchaseOrderItem.objects.create(
            purchase_order=order, material=flour, ordered_quantity=10, unit_cost=Decimal("50.00")
        )
        PurchaseOrderItem.objects.create(
            purchase_order=order, material=sugar, ordered_quantity=5, unit_cost=Decimal("30.00")
        )
        assert order.total_cost == Decimal("650.00")

    def test_received_variance_is_none_before_receiving(self, supplier, branch, owner, flour):
        order = PurchaseOrder.objects.create(supplier=supplier, branch=branch, created_by=owner)
        item = PurchaseOrderItem.objects.create(
            purchase_order=order, material=flour, ordered_quantity=10, unit_cost=Decimal("50.00")
        )
        assert item.received_variance is None

    def test_received_variance_reflects_a_short_delivery(self, supplier, branch, owner, flour):
        order = PurchaseOrder.objects.create(supplier=supplier, branch=branch, created_by=owner)
        item = PurchaseOrderItem.objects.create(
            purchase_order=order,
            material=flour,
            ordered_quantity=10,
            received_quantity=8,
            unit_cost=Decimal("50.00"),
        )
        assert item.received_variance == Decimal("-2")


class TestCreatePurchaseOrder:
    def test_creates_a_draft_order_with_its_items(self, supplier, branch, owner, flour):
        order = create_purchase_order(
            created_by=owner,
            supplier_id=supplier.pk,
            branch_id=branch.pk,
            items=[{"material_id": flour.pk, "ordered_quantity": "10", "unit_cost": "50.00"}],
        )
        assert order.status == PurchaseOrder.Status.DRAFT
        assert order.items.count() == 1
        item = order.items.first()
        assert item.ordered_quantity == Decimal("10")
        assert item.unit_cost == Decimal("50.00")

    def test_rejects_an_order_with_no_items(self, supplier, branch, owner):
        with pytest.raises(PurchasingError, match="at least one item"):
            create_purchase_order(
                created_by=owner, supplier_id=supplier.pk, branch_id=branch.pk, items=[]
            )

    def test_rejects_a_nonexistent_supplier(self, branch, owner, flour):
        with pytest.raises(PurchasingError, match="supplier"):
            create_purchase_order(
                created_by=owner,
                supplier_id=999999,
                branch_id=branch.pk,
                items=[{"material_id": flour.pk, "ordered_quantity": "10"}],
            )

    def test_rejects_a_zero_quantity_item(self, supplier, branch, owner, flour):
        with pytest.raises(PurchasingError, match="greater than zero"):
            create_purchase_order(
                created_by=owner,
                supplier_id=supplier.pk,
                branch_id=branch.pk,
                items=[{"material_id": flour.pk, "ordered_quantity": "0"}],
            )

    def test_does_not_touch_inventory(self, supplier, branch, owner, flour):
        create_purchase_order(
            created_by=owner,
            supplier_id=supplier.pk,
            branch_id=branch.pk,
            items=[{"material_id": flour.pk, "ordered_quantity": "10"}],
        )
        assert not Inventory.objects.filter(branch=branch, product=flour).exists()
        assert InventoryTransaction.objects.count() == 0


class TestMarkPurchaseOrderOrdered:
    def test_moves_draft_to_ordered(self, supplier, branch, owner, flour):
        order = create_purchase_order(
            created_by=owner,
            supplier_id=supplier.pk,
            branch_id=branch.pk,
            items=[{"material_id": flour.pk, "ordered_quantity": "10"}],
        )
        mark_purchase_order_ordered(order, acted_by=owner)
        order.refresh_from_db()
        assert order.status == PurchaseOrder.Status.ORDERED
        assert order.ordered_at is not None

    def test_rejects_marking_an_already_ordered_order(self, supplier, branch, owner, flour):
        order = create_purchase_order(
            created_by=owner,
            supplier_id=supplier.pk,
            branch_id=branch.pk,
            items=[{"material_id": flour.pk, "ordered_quantity": "10"}],
        )
        mark_purchase_order_ordered(order)
        with pytest.raises(PurchasingError, match="only a Draft order"):
            mark_purchase_order_ordered(order)


class TestReceivePurchaseOrder:
    def test_defaults_received_quantity_to_ordered_and_restocks_inventory(
        self, supplier, branch, owner, flour
    ):
        order = create_purchase_order(
            created_by=owner,
            supplier_id=supplier.pk,
            branch_id=branch.pk,
            items=[{"material_id": flour.pk, "ordered_quantity": "10"}],
        )
        mark_purchase_order_ordered(order)
        receive_purchase_order(order, received_by=owner)

        order.refresh_from_db()
        assert order.status == PurchaseOrder.Status.RECEIVED
        assert order.received_at is not None
        item = order.items.first()
        assert item.received_quantity == Decimal("10")

        inventory = Inventory.objects.get(branch=branch, product=flour)
        assert inventory.quantity_on_hand == Decimal("10")
        transaction = InventoryTransaction.objects.get(branch=branch, product=flour)
        assert transaction.movement_type == InventoryTransaction.MovementType.RESTOCK
        assert transaction.quantity_change == Decimal("10")

    def test_records_a_short_delivery_and_only_restocks_what_actually_arrived(
        self, supplier, branch, owner, flour
    ):
        order = create_purchase_order(
            created_by=owner,
            supplier_id=supplier.pk,
            branch_id=branch.pk,
            items=[{"material_id": flour.pk, "ordered_quantity": "10"}],
        )
        mark_purchase_order_ordered(order)
        item = order.items.first()
        receive_purchase_order(order, received_quantities={str(item.pk): "8"}, received_by=owner)

        item.refresh_from_db()
        assert item.received_quantity == Decimal("8")
        assert item.received_variance == Decimal("-2")
        inventory = Inventory.objects.get(branch=branch, product=flour)
        assert inventory.quantity_on_hand == Decimal("8")

    def test_rejects_receiving_a_draft_order(self, supplier, branch, owner, flour):
        order = create_purchase_order(
            created_by=owner,
            supplier_id=supplier.pk,
            branch_id=branch.pk,
            items=[{"material_id": flour.pk, "ordered_quantity": "10"}],
        )
        with pytest.raises(PurchasingError, match="only an Ordered order"):
            receive_purchase_order(order)

    def test_adds_to_existing_stock_rather_than_overwriting_it(
        self, supplier, branch, owner, flour
    ):
        Inventory.objects.create(branch=branch, product=flour, quantity_on_hand=5)
        order = create_purchase_order(
            created_by=owner,
            supplier_id=supplier.pk,
            branch_id=branch.pk,
            items=[{"material_id": flour.pk, "ordered_quantity": "10"}],
        )
        mark_purchase_order_ordered(order)
        receive_purchase_order(order)

        inventory = Inventory.objects.get(branch=branch, product=flour)
        assert inventory.quantity_on_hand == Decimal("15")


class TestCancelPurchaseOrder:
    def test_cancels_a_draft_order(self, supplier, branch, owner, flour):
        order = create_purchase_order(
            created_by=owner,
            supplier_id=supplier.pk,
            branch_id=branch.pk,
            items=[{"material_id": flour.pk, "ordered_quantity": "10"}],
        )
        cancel_purchase_order(order, cancelled_by=owner)
        order.refresh_from_db()
        assert order.status == PurchaseOrder.Status.CANCELLED
        assert order.cancelled_by == owner

    def test_cancels_an_ordered_order_with_no_inventory_effect(
        self, supplier, branch, owner, flour
    ):
        order = create_purchase_order(
            created_by=owner,
            supplier_id=supplier.pk,
            branch_id=branch.pk,
            items=[{"material_id": flour.pk, "ordered_quantity": "10"}],
        )
        mark_purchase_order_ordered(order)
        cancel_purchase_order(order)
        order.refresh_from_db()
        assert order.status == PurchaseOrder.Status.CANCELLED
        assert InventoryTransaction.objects.count() == 0

    def test_rejects_cancelling_a_received_order(self, supplier, branch, owner, flour):
        order = create_purchase_order(
            created_by=owner,
            supplier_id=supplier.pk,
            branch_id=branch.pk,
            items=[{"material_id": flour.pk, "ordered_quantity": "10"}],
        )
        mark_purchase_order_ordered(order)
        receive_purchase_order(order)
        with pytest.raises(PurchasingError, match="Draft or Ordered"):
            cancel_purchase_order(order)


class TestPurchaseOrderViewsAccess:
    def test_owner_admin_can_view_the_list(self, client, owner):
        client.force_login(owner)
        verify_otp_for_client(client, owner)
        response = client.get(reverse("purchasing:purchase_order_list"))
        assert response.status_code == 200

    def test_purchasing_staff_can_view_the_list(self, client, purchasing_staff):
        client.force_login(purchasing_staff)
        response = client.get(reverse("purchasing:purchase_order_list"))
        assert response.status_code == 200

    def test_branch_staff_is_forbidden(self, client, branch_staff):
        client.force_login(branch_staff)
        response = client.get(reverse("purchasing:purchase_order_list"))
        assert response.status_code == 403

    def test_purchasing_staff_can_create_an_order_through_the_view(
        self, client, purchasing_staff, supplier, branch, flour
    ):
        client.force_login(purchasing_staff)
        response = client.post(
            reverse("purchasing:purchase_order_list"),
            {
                "supplier_id": supplier.pk,
                "branch_id": branch.pk,
                "material_id": [flour.pk],
                "ordered_quantity": ["10"],
                "unit_cost": ["50.00"],
                "notes": "",
            },
        )
        assert response.status_code == 200
        assert PurchaseOrder.objects.count() == 1

    def test_full_lifecycle_through_the_views(
        self, client, purchasing_staff, supplier, branch, flour
    ):
        order = create_purchase_order(
            created_by=purchasing_staff,
            supplier_id=supplier.pk,
            branch_id=branch.pk,
            items=[{"material_id": flour.pk, "ordered_quantity": "10"}],
        )
        client.force_login(purchasing_staff)

        detail_url = reverse("purchasing:purchase_order_detail", args=[order.pk])
        assert client.get(detail_url).status_code == 200

        client.post(reverse("purchasing:purchase_order_mark_ordered", args=[order.pk]))
        order.refresh_from_db()
        assert order.status == PurchaseOrder.Status.ORDERED

        item = order.items.first()
        client.post(
            reverse("purchasing:purchase_order_receive", args=[order.pk]),
            {"item_id": [item.pk], f"received_quantity_{item.pk}": "10"},
        )
        order.refresh_from_db()
        assert order.status == PurchaseOrder.Status.RECEIVED
        assert Inventory.objects.get(branch=branch, product=flour).quantity_on_hand == Decimal("10")

    def test_branch_staff_cannot_cancel_an_order(self, client, branch_staff, order_for_cancel):
        client.force_login(branch_staff)
        response = client.post(
            reverse("purchasing:purchase_order_cancel", args=[order_for_cancel.pk])
        )
        assert response.status_code == 403


@pytest.fixture
def order_for_cancel(supplier, branch, purchasing_staff, flour):
    return create_purchase_order(
        created_by=purchasing_staff,
        supplier_id=supplier.pk,
        branch_id=branch.pk,
        items=[{"material_id": flour.pk, "ordered_quantity": "10"}],
    )
