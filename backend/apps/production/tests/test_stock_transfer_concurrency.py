"""
Regression tests for a double-submission/race-condition gap found during
the pre-adviser-meeting verification pass on apps.production.services
(2026-11-04) -- the same risk class as backend gap assessment item 26
(create_stock_transfer_order's own select_for_update() fix), but that
fix was never applied to cancel_stock_transfer_order or
advance_stock_transfer_status.

Kept in its own file rather than added to test_stock_transfer_order.py
because that file is part of an open PR (#164, task #140) at the time
this was written -- adding tests here avoids touching a file already in
flight, with zero risk of a merge conflict.
"""

from unittest.mock import patch

import pytest

from apps.accounts.models import Branch, Role, User
from apps.inventory.models import Inventory, Product
from apps.production import services
from apps.production.models import StockTransferOrder

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
def cake(commissary):
    product = Product.objects.create(
        name="Chocolate Cake", price="140.00", product_type=Product.ProductType.FINISHED_GOOD
    )
    Inventory.objects.create(branch=commissary, product=product, quantity_on_hand=50)
    return product


class TestCancelStockTransferOrderLocking:
    def test_locks_the_order_row_before_checking_status(
        self, commissary, commissary_staff, batangas, cake
    ):
        """In-memory sqlite can't reproduce the real concurrent race in a
        test (no true multi-connection row locking) -- same limitation
        and same technique as the existing create_stock_transfer_order
        locking test -- so this asserts the locking call itself is
        actually made on the order, not skipped."""
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        with patch(
            "apps.production.models.StockTransferOrder.objects.select_for_update",
            wraps=StockTransferOrder.objects.select_for_update,
        ) as mock_lock:
            services.cancel_stock_transfer_order(order)
        mock_lock.assert_called_once()

    def test_locks_the_source_inventory_row_before_crediting_it_back(
        self, commissary, commissary_staff, batangas, cake
    ):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        with patch(
            "apps.inventory.models.Inventory.objects.select_for_update",
            wraps=Inventory.objects.select_for_update,
        ) as mock_lock:
            services.cancel_stock_transfer_order(order)
        mock_lock.assert_called_once()

    def test_two_cancel_calls_on_separately_fetched_copies_do_not_double_credit(
        self, commissary, commissary_staff, batangas, cake
    ):
        """The actual bug this closes: the ORIGINAL (unfixed) version
        checked/mutated the caller's own `order` object directly. Two
        separate requests for the same real-world click each fetch
        their OWN copy of the order (exactly what
        apps.production.views.cancel_stock_transfer's plain
        get_object_or_404 does) -- simulated here by fetching the order
        twice into two independent Python objects, mirroring two
        separate HTTP requests, rather than reusing one mutated object
        (which is what let the old code's own regression test pass
        without ever exercising the real race)."""
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        request_1_copy = StockTransferOrder.objects.get(pk=order.pk)
        request_2_copy = StockTransferOrder.objects.get(pk=order.pk)

        services.cancel_stock_transfer_order(request_1_copy)
        with pytest.raises(services.StockTransferError, match="only a Processing or"):
            services.cancel_stock_transfer_order(request_2_copy)

        commissary_inventory = Inventory.objects.get(branch=commissary, product=cake)
        assert commissary_inventory.quantity_on_hand == 50  # credited back exactly once


class TestAdvanceStockTransferStatusLocking:
    def test_locks_the_order_row_before_checking_status(
        self, commissary, commissary_staff, batangas, cake
    ):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        with patch(
            "apps.production.models.StockTransferOrder.objects.select_for_update",
            wraps=StockTransferOrder.objects.select_for_update,
        ) as mock_lock:
            services.advance_stock_transfer_status(order, StockTransferOrder.Status.IN_TRANSIT)
        mock_lock.assert_called_once()

    def test_locks_the_destination_inventory_row_when_marking_delivered(
        self, commissary, commissary_staff, batangas, cake
    ):
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        services.advance_stock_transfer_status(order, StockTransferOrder.Status.IN_TRANSIT)
        with patch(
            "apps.inventory.models.Inventory.objects.select_for_update",
            wraps=Inventory.objects.select_for_update,
        ) as mock_lock:
            services.advance_stock_transfer_status(order, StockTransferOrder.Status.DELIVERED)
        mock_lock.assert_called_once()

    def test_two_deliver_calls_on_separately_fetched_copies_do_not_double_credit(
        self, commissary, commissary_staff, batangas, cake
    ):
        """The actual bug this closes: marking DELIVERED twice via two
        separately-fetched copies of the same order (two near-
        simultaneous requests) must credit the destination branch's
        Inventory exactly once, not twice."""
        order = services.create_stock_transfer_order(
            commissary_staff=commissary_staff,
            destination_branch_id=batangas.pk,
            product_id=cake.pk,
            quantity=10,
        )
        order = services.advance_stock_transfer_status(order, StockTransferOrder.Status.IN_TRANSIT)

        request_1_copy = StockTransferOrder.objects.get(pk=order.pk)
        request_2_copy = StockTransferOrder.objects.get(pk=order.pk)

        services.advance_stock_transfer_status(request_1_copy, StockTransferOrder.Status.DELIVERED)
        with pytest.raises(services.StockTransferError, match="already Delivered"):
            services.advance_stock_transfer_status(
                request_2_copy, StockTransferOrder.Status.DELIVERED
            )

        destination_inventory = Inventory.objects.get(branch=batangas, product=cake)
        assert destination_inventory.quantity_on_hand == 10  # credited exactly once
