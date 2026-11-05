"""
Tests for StockTransferOrder: tracking finished goods moving from the
commissary to a branch (manuscript's "Supply distribution record updates
per branch" / "Stock order status tracking: processing, in transit,
delivered").
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

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

    def test_locks_the_source_inventory_row_before_checking_stock(
        self, commissary, commissary_staff, batangas, cake
    ):
        """Backend gap assessment item 26 (same class of risk as the POS
        double-submission race): the commissary's Inventory row must be
        locked with select_for_update() before the stock check, so two
        near-simultaneous submissions can't both read the same
        quantity_on_hand and both pass validation. In-memory sqlite can't
        reproduce the real concurrent race in a test (no true
        multi-connection row locking), so this asserts the locking call
        itself is actually made rather than skipped."""
        with patch(
            "apps.inventory.models.Inventory.objects.select_for_update",
            wraps=Inventory.objects.select_for_update,
        ) as mock_lock:
            services.create_stock_transfer_order(
                commissary_staff=commissary_staff,
                destination_branch_id=batangas.pk,
                product_id=cake.pk,
                quantity=10,
            )
        mock_lock.assert_called_once()

    def test_a_second_submission_correctly_sees_the_reduced_stock(
        self, commissary, commissary_staff, batangas, cake
    ):
        """Functional half of the double-submission fix: whether or not
        the two submissions are truly concurrent, the second one must
        always re-read the actual current stock (not a stale value from
        before the first submission's deduction) -- so resubmitting
        past what's really left on hand is rejected, not silently
        allowed to go negative."""
        services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=40,
        )
        # Only 10 of the original 50 remain -- a second, identical
        # resubmission for 40 more must now be rejected.
        with pytest.raises(services.StockTransferError, match="Not enough"):
            services.create_stock_transfer_order(
                commissary_staff=commissary_staff,
                destination_branch_id=batangas.pk,
                product_id=cake.pk,
                quantity=40,
            )
        commissary_inventory = Inventory.objects.get(branch=commissary, product=cake)
        assert commissary_inventory.quantity_on_hand == 10
        assert StockTransferOrder.objects.count() == 1

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

    def test_marking_delivered_records_who_performed_it(
        self, commissary, commissary_staff, batangas, cake
    ):
        """Task #166 (adviser dry-run, 2026-11-05): delivered_by must be
        the acting user, not the order's original commissary_staff
        (who merely created it -- a different person can later mark it
        Delivered)."""
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        role = Role.objects.create(name=Role.OWNER_ADMIN)
        deliverer = User.objects.create_user(username="owner1", password="x", role=role)

        services.advance_stock_transfer_status(order, StockTransferOrder.Status.IN_TRANSIT)
        services.advance_stock_transfer_status(
            order, StockTransferOrder.Status.DELIVERED, acted_by=deliverer
        )
        order.refresh_from_db()
        assert order.delivered_by == deliverer

    def test_marking_in_transit_does_not_set_delivered_by(
        self, commissary, commissary_staff, batangas, cake
    ):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        role = Role.objects.create(name=Role.OWNER_ADMIN)
        actor = User.objects.create_user(username="owner1", password="x", role=role)

        services.advance_stock_transfer_status(
            order, StockTransferOrder.Status.IN_TRANSIT, acted_by=actor
        )
        order.refresh_from_db()
        assert order.delivered_by is None

    def test_acted_by_defaults_to_none_for_backward_compatible_callers(
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
        assert order.delivered_by is None

    def test_an_unrecognized_status_value_raises_stock_transfer_error_not_valueerror(
        self, commissary, commissary_staff, batangas, cake
    ):
        """Backend gap assessment item 25: a garbage next_status value
        (e.g. a tampered form field) used to blow up with a bare,
        uncaught ValueError from StockTransferOrder.Status(new_status)
        inside the error-message formatting itself, before the service
        ever got a chance to report a clean, catchable error."""
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        with pytest.raises(
            services.StockTransferError, match="not a recognized stock transfer status"
        ):
            services.advance_stock_transfer_status(order, "BOGUS")
        order.refresh_from_db()
        assert order.status == StockTransferOrder.Status.PROCESSING

    def test_a_missing_status_value_also_raises_stock_transfer_error(
        self, commissary, commissary_staff, batangas, cake
    ):
        """Same bug, the other common trigger: request.POST.get(...)
        returns None when the field is simply missing from the form."""
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        with pytest.raises(
            services.StockTransferError, match="not a recognized stock transfer status"
        ):
            services.advance_stock_transfer_status(order, None)


class TestCancelStockTransferOrder:
    """Backend gap assessment item 24: StockTransferOrder cancellation/
    reversal path."""

    def test_cancelling_a_processing_order_returns_stock_to_the_commissary(
        self, commissary, commissary_staff, batangas, cake
    ):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        services.cancel_stock_transfer_order(order)
        order.refresh_from_db()

        assert order.status == StockTransferOrder.Status.CANCELLED
        assert order.cancelled_at is not None

        commissary_inventory = Inventory.objects.get(branch=commissary, product=cake)
        assert commissary_inventory.quantity_on_hand == 50  # fully restored

    def test_cancelling_logs_a_transfer_cancelled_transaction(
        self, commissary, commissary_staff, batangas, cake
    ):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        services.cancel_stock_transfer_order(order)

        txn = InventoryTransaction.objects.get(
            branch=commissary,
            product=cake,
            movement_type=InventoryTransaction.MovementType.TRANSFER_CANCELLED,
        )
        assert txn.quantity_change == 10

    def test_can_cancel_an_in_transit_order_too(self, commissary, commissary_staff, batangas, cake):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        services.advance_stock_transfer_status(order, StockTransferOrder.Status.IN_TRANSIT)

        services.cancel_stock_transfer_order(order)
        order.refresh_from_db()
        assert order.status == StockTransferOrder.Status.CANCELLED

        commissary_inventory = Inventory.objects.get(branch=commissary, product=cake)
        assert commissary_inventory.quantity_on_hand == 50

    def test_cannot_cancel_a_delivered_order(self, commissary, commissary_staff, batangas, cake):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        services.advance_stock_transfer_status(order, StockTransferOrder.Status.IN_TRANSIT)
        services.advance_stock_transfer_status(order, StockTransferOrder.Status.DELIVERED)

        with pytest.raises(services.StockTransferError, match="only a Processing or"):
            services.cancel_stock_transfer_order(order)

        order.refresh_from_db()
        assert order.status == StockTransferOrder.Status.DELIVERED
        # Destination's delivered stock is untouched by the failed cancel attempt.
        destination_inventory = Inventory.objects.get(branch=batangas, product=cake)
        assert destination_inventory.quantity_on_hand == 10

    def test_cannot_cancel_an_already_cancelled_order(
        self, commissary, commissary_staff, batangas, cake
    ):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        services.cancel_stock_transfer_order(order)

        with pytest.raises(services.StockTransferError, match="only a Processing or"):
            services.cancel_stock_transfer_order(order)

        commissary_inventory = Inventory.objects.get(branch=commissary, product=cake)
        assert commissary_inventory.quantity_on_hand == 50  # not double-credited

    def test_cancelling_records_who_performed_it(
        self, commissary, commissary_staff, batangas, cake
    ):
        """Task #166 (adviser dry-run, 2026-11-05): cancelled_by must be
        the acting user, not the order's original commissary_staff."""
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        role = Role.objects.create(name=Role.OWNER_ADMIN)
        canceller = User.objects.create_user(username="owner1", password="x", role=role)

        services.cancel_stock_transfer_order(order, cancelled_by=canceller)
        order.refresh_from_db()
        assert order.cancelled_by == canceller

    def test_cancelled_by_defaults_to_none_for_backward_compatible_callers(
        self, commissary, commissary_staff, batangas, cake
    ):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        services.cancel_stock_transfer_order(order)
        order.refresh_from_db()
        assert order.cancelled_by is None


class TestIsStaleInProcessing:
    """Task #166 (adviser dry-run, 2026-11-05): the Stock Transfers
    screen's visual flag for an order that's sat in PROCESSING for more
    than two days."""

    def test_false_when_recently_created(self, commissary, commissary_staff, batangas, cake):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        assert order.is_stale_in_processing is False

    def test_true_once_older_than_two_days(self, commissary, commissary_staff, batangas, cake):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        StockTransferOrder.objects.filter(pk=order.pk).update(
            created_at=timezone.now() - timedelta(days=3)
        )
        order.refresh_from_db()
        assert order.is_stale_in_processing is True

    def test_false_once_in_transit_even_if_old(self, commissary, commissary_staff, batangas, cake):
        """An order that's moved on isn't "stalled" anymore, however
        old it is -- the flag is specifically about PROCESSING."""
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        StockTransferOrder.objects.filter(pk=order.pk).update(
            created_at=timezone.now() - timedelta(days=3)
        )
        order.refresh_from_db()
        services.advance_stock_transfer_status(order, StockTransferOrder.Status.IN_TRANSIT)
        order.refresh_from_db()
        assert order.is_stale_in_processing is False


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

    def test_undelivered_order_shows_dash_not_a_blank_date(
        self, commissary_client, commissary_staff, commissary, batangas, cake
    ):
        """Task #140 (client review, 2026-11-04): Stock Transfers table
        layout cleanup -- an order that hasn't been delivered yet must
        still show an explicit placeholder in the Delivered column, not
        a blank cell someone could mistake for missing data."""
        services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=5,
        )
        response = commissary_client.get(reverse("production:stock_transfers"))
        assert "&mdash;" in response.content.decode()

    def test_delivered_order_shows_its_delivered_date(
        self, commissary_client, commissary_staff, commissary, batangas, cake
    ):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=5,
        )
        services.advance_stock_transfer_status(order, "IN_TRANSIT")
        services.advance_stock_transfer_status(order, "DELIVERED")

        response = commissary_client.get(reverse("production:stock_transfers"))
        order.refresh_from_db()
        content = response.content.decode()
        delivered_local = timezone.localtime(order.delivered_at)
        assert delivered_local.strftime("%b %d, %Y") in content
        assert delivered_local.strftime("%H:%M") in content

    def test_advance_view_records_the_logged_in_user_as_delivered_by(
        self, commissary_client, commissary_staff, commissary, batangas, cake
    ):
        """Task #166 (adviser dry-run, 2026-11-05): the view must pass
        request.user through to the service, and the page must display
        it, not just store it silently."""
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=5,
        )
        commissary_client.post(
            reverse("production:advance_stock_transfer", args=[order.pk]),
            {"next_status": "IN_TRANSIT"},
        )
        commissary_client.post(
            reverse("production:advance_stock_transfer", args=[order.pk]),
            {"next_status": "DELIVERED"},
        )
        order.refresh_from_db()
        assert order.delivered_by == commissary_staff

        list_response = commissary_client.get(reverse("production:stock_transfers"))
        content = list_response.content.decode()
        assert str(commissary_staff) in content

    def test_cancel_view_records_the_logged_in_user_as_cancelled_by(
        self, commissary_client, commissary_staff, commissary, batangas, cake
    ):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=5,
        )
        commissary_client.post(reverse("production:cancel_stock_transfer", args=[order.pk]))
        order.refresh_from_db()
        assert order.cancelled_by == commissary_staff

    def test_stale_processing_order_is_visually_flagged(
        self, commissary_client, commissary_staff, commissary, batangas, cake
    ):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=5,
        )
        StockTransferOrder.objects.filter(pk=order.pk).update(
            created_at=timezone.now() - timedelta(days=3)
        )

        response = commissary_client.get(reverse("production:stock_transfers"))
        assert "Stalled" in response.content.decode()

    def test_fresh_processing_order_is_not_flagged(
        self, commissary_client, commissary_staff, commissary, batangas, cake
    ):
        services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=5,
        )
        response = commissary_client.get(reverse("production:stock_transfers"))
        assert "Stalled" not in response.content.decode()

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

    def test_cancel_view_cancels_a_processing_order(
        self, commissary_client, commissary, commissary_staff, batangas, cake
    ):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        response = commissary_client.post(
            reverse("production:cancel_stock_transfer", args=[order.pk])
        )
        assert response.status_code == 302
        order.refresh_from_db()
        assert order.status == StockTransferOrder.Status.CANCELLED

        commissary_inventory = Inventory.objects.get(branch=commissary, product=cake)
        assert commissary_inventory.quantity_on_hand == 50

    def test_cancel_view_silently_ignores_a_delivered_order(
        self, commissary_client, commissary, commissary_staff, batangas, cake
    ):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        services.advance_stock_transfer_status(order, StockTransferOrder.Status.IN_TRANSIT)
        services.advance_stock_transfer_status(order, StockTransferOrder.Status.DELIVERED)

        response = commissary_client.post(
            reverse("production:cancel_stock_transfer", args=[order.pk])
        )
        assert response.status_code == 302
        order.refresh_from_db()
        assert order.status == StockTransferOrder.Status.DELIVERED

    def test_branch_staff_cannot_cancel(self, client, commissary, commissary_staff, batangas, cake):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        role = Role.objects.create(name=Role.BRANCH_STAFF)
        staff = User.objects.create_user(username="cashier2", password="testpass123", role=role)
        client.force_login(staff)

        response = client.post(reverse("production:cancel_stock_transfer", args=[order.pk]))
        assert response.status_code == 403
        order.refresh_from_db()
        assert order.status == StockTransferOrder.Status.PROCESSING

    def test_advance_view_does_not_500_on_a_bogus_status_value(
        self, commissary_client, commissary, commissary_staff, batangas, cake
    ):
        """Backend gap assessment item 25: a tampered/garbage next_status
        form value used to raise an uncaught ValueError past the view's
        own except services.StockTransferError. Now it's just another
        clean, no-op StockTransferError like any other illegal move."""
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        response = commissary_client.post(
            reverse("production:advance_stock_transfer", args=[order.pk]),
            {"next_status": "BOGUS"},
        )
        assert response.status_code == 302
        order.refresh_from_db()
        assert order.status == StockTransferOrder.Status.PROCESSING
