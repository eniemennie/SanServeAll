"""
Tests for StockTransferOrder: tracking finished goods moving from the
commissary to a branch (manuscript's "Supply distribution record updates
per branch" / "Stock order status tracking: processing, in transit,
delivered").
"""

import pytest
from django.urls import reverse

from apps.accounts.models import Branch, Role, User
from apps.inventory.models import Inventory, InventoryTransaction, Product
from apps.production import services
from apps.production.models import StockTransferOrder
from conftest import verify_otp_for_client

pytestmark = pytest.mark.django_db


@pytest.fixture
def commissary():
    return Branch.objects.create(name="Commissary", code="COMMISSARY", is_commissary=True)


@pytest.fixture
def batangas():
    return Branch.objects.create(name="Batangas City", code="BATANGAS")


@pytest.fixture
def commissary_staff():
    role = Role.objects.create(name=Role.COMMISSARY_STAFF)
    return User.objects.create_user(username="commissary1", password="testpass123", role=role)


@pytest.fixture
def commissary_client(client, commissary_staff):
    client.force_login(commissary_staff)
    return client


@pytest.fixture
def cake(commissary):
    product = Product.objects.create(
        name="Chocolate Cake", price="140.00", product_type=Product.ProductType.FINISHED_GOOD
    )
    Inventory.objects.create(branch=commissary, product=product, quantity_on_hand=50)
    return product


class TestCreateStockTransferOrder:
    def test_valid_order_is_created_as_processing(
        self, commissary, commissary_staff, batangas, cake
    ):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        assert order.status == StockTransferOrder.Status.PROCESSING
        assert order.source_branch == commissary
        assert order.destination_branch == batangas
        assert order.quantity == 10
        assert order.delivered_at is None

    def test_deducts_commissary_stock_immediately(
        self, commissary, commissary_staff, batangas, cake
    ):
        services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        commissary_inventory = Inventory.objects.get(branch=commissary, product=cake)
        assert commissary_inventory.quantity_on_hand == 40  # 50 - 10

    def test_does_not_credit_destination_until_delivered(
        self, commissary, commissary_staff, batangas, cake
    ):
        services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        assert not Inventory.objects.filter(branch=batangas, product=cake).exists()

    def test_logs_transfer_out_transaction(self, commissary, commissary_staff, batangas, cake):
        services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        txn = InventoryTransaction.objects.get(
            branch=commissary,
            product=cake,
            movement_type=InventoryTransaction.MovementType.TRANSFER_OUT,
        )
        assert txn.quantity_change == -10

    def test_insufficient_stock_is_rejected(self, commissary, commissary_staff, batangas, cake):
        with pytest.raises(services.StockTransferError, match="Not enough"):
            services.create_stock_transfer_order(
                commissary_staff=commissary_staff,
                destination_branch_id=batangas.pk,
                product_id=cake.pk,
                quantity=999,
            )
        commissary_inventory = Inventory.objects.get(branch=commissary, product=cake)
        assert commissary_inventory.quantity_on_hand == 50  # untouched

    def test_destination_cannot_be_the_commissary_itself(self, commissary, commissary_staff, cake):
        with pytest.raises(services.StockTransferError, match="cannot be the commissary"):
            services.create_stock_transfer_order(
                commissary_staff=commissary_staff,
                destination_branch_id=commissary.pk,
                product_id=cake.pk,
                quantity=5,
            )

    def test_destination_cannot_be_another_commissary(self, commissary, commissary_staff, cake):
        other_commissary = Branch.objects.create(
            name="Second Commissary", code="COMMISSARY2", is_commissary=True
        )
        with pytest.raises(services.StockTransferError, match="cannot be another commissary"):
            services.create_stock_transfer_order(
                commissary_staff=commissary_staff,
                destination_branch_id=other_commissary.pk,
                product_id=cake.pk,
                quantity=5,
            )

    def test_zero_quantity_is_rejected(self, commissary, commissary_staff, batangas, cake):
        with pytest.raises(services.StockTransferError, match="greater than zero"):
            services.create_stock_transfer_order(
                commissary_staff=commissary_staff,
                destination_branch_id=batangas.pk,
                product_id=cake.pk,
                quantity=0,
            )

    def test_no_commissary_configured_raises_clear_error(self, batangas):
        role = Role.objects.create(name=Role.COMMISSARY_STAFF)
        staff = User.objects.create_user(
            username="isolated_staff", password="testpass123", role=role
        )
        product = Product.objects.create(name="Isolated Cake", price="100.00")

        with pytest.raises(services.StockTransferError, match="No commissary branch"):
            services.create_stock_transfer_order(
                commissary_staff=staff,
                destination_branch_id=batangas.pk,
                product_id=product.pk,
                quantity=5,
            )


class TestAdvanceStockTransferStatus:
    def test_processing_advances_to_in_transit(self, commissary, commissary_staff, batangas, cake):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        services.advance_stock_transfer_status(order, StockTransferOrder.Status.IN_TRANSIT)
        order.refresh_from_db()
        assert order.status == StockTransferOrder.Status.IN_TRANSIT
        assert order.delivered_at is None

    def test_in_transit_advances_to_delivered_and_credits_destination(
        self, commissary, commissary_staff, batangas, cake
    ):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        services.advance_stock_transfer_status(order, StockTransferOrder.Status.IN_TRANSIT)
        services.advance_stock_transfer_status(order, StockTransferOrder.Status.DELIVERED)
        order.refresh_from_db()

        assert order.status == StockTransferOrder.Status.DELIVERED
        assert order.delivered_at is not None

        destination_inventory = Inventory.objects.get(branch=batangas, product=cake)
        assert destination_inventory.quantity_on_hand == 10

        txn = InventoryTransaction.objects.get(
            branch=batangas,
            product=cake,
            movement_type=InventoryTransaction.MovementType.TRANSFER_IN,
        )
        assert txn.quantity_change == 10

    def test_cannot_skip_in_transit(self, commissary, commissary_staff, batangas, cake):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        with pytest.raises(services.StockTransferError, match="next step must be"):
            services.advance_stock_transfer_status(order, StockTransferOrder.Status.DELIVERED)
        order.refresh_from_db()
        assert order.status == StockTransferOrder.Status.PROCESSING
        assert not Inventory.objects.filter(branch=batangas, product=cake).exists()

    def test_cannot_go_backward(self, commissary, commissary_staff, batangas, cake):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        services.advance_stock_transfer_status(order, StockTransferOrder.Status.IN_TRANSIT)
        with pytest.raises(services.StockTransferError, match="next step must be"):
            services.advance_stock_transfer_status(order, StockTransferOrder.Status.PROCESSING)

    def test_cannot_advance_past_delivered(self, commissary, commissary_staff, batangas, cake):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        services.advance_stock_transfer_status(order, StockTransferOrder.Status.IN_TRANSIT)
        services.advance_stock_transfer_status(order, StockTransferOrder.Status.DELIVERED)
        with pytest.raises(services.StockTransferError, match="no further status change"):
            services.advance_stock_transfer_status(order, StockTransferOrder.Status.DELIVERED)


class TestStockTransfersView:
    def test_commissary_staff_can_view_the_page(self, commissary_client):
        response = commissary_client.get(reverse("production:stock_transfers"))
        assert response.status_code == 200

    def test_branch_staff_cannot_access_the_page(self, client):
        role = Role.objects.create(name=Role.BRANCH_STAFF)
        staff = User.objects.create_user(username="cashier1", password="testpass123", role=role)
        client.force_login(staff)
        response = client.get(reverse("production:stock_transfers"))
        assert response.status_code == 403

    def test_owner_can_view_the_page(self, client):
        role = Role.objects.create(name=Role.OWNER_ADMIN)
        owner = User.objects.create_user(username="owner1", password="testpass123", role=role)
        client.force_login(owner)
        verify_otp_for_client(client, owner)
        response = client.get(reverse("production:stock_transfers"))
        assert response.status_code == 200

    def test_valid_submission_creates_an_order_and_redirects(
        self, commissary_client, commissary, batangas, cake
    ):
        response = commissary_client.post(
            reverse("production:stock_transfers"),
            {"product_id": cake.pk, "destination_branch_id": batangas.pk, "quantity": "10"},
        )
        assert response.status_code == 302
        assert StockTransferOrder.objects.count() == 1

    def test_invalid_submission_shows_error_and_creates_nothing(
        self, commissary_client, commissary, batangas, cake
    ):
        response = commissary_client.post(
            reverse("production:stock_transfers"),
            {"product_id": cake.pk, "destination_branch_id": batangas.pk, "quantity": "999"},
        )
        assert response.status_code == 200
        assert b"Not enough" in response.content
        assert StockTransferOrder.objects.count() == 0

    def test_advance_view_moves_the_order_forward(
        self, commissary_client, commissary, commissary_staff, batangas, cake
    ):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        response = commissary_client.post(
            reverse("production:advance_stock_transfer", args=[order.pk]),
            {"next_status": "IN_TRANSIT"},
        )
        assert response.status_code == 302
        order.refresh_from_db()
        assert order.status == StockTransferOrder.Status.IN_TRANSIT

    def test_advance_view_silently_ignores_an_illegal_jump(
        self, commissary_client, commissary, commissary_staff, batangas, cake
    ):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        response = commissary_client.post(
            reverse("production:advance_stock_transfer", args=[order.pk]),
            {"next_status": "DELIVERED"},
        )
        assert response.status_code == 302
        order.refresh_from_db()
        assert order.status == StockTransferOrder.Status.PROCESSING
