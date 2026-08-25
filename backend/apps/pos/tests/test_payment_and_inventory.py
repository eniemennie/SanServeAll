"""
Tests for Week 5: Payment Processing (Fig. 3-16), Transaction Receipt
(Fig. 3-17), and the real-time inventory deduction hook (Row 5.3).
"""

import pytest
from django.urls import reverse

from apps.accounts.models import Branch, CashierPIN, Role, User
from apps.inventory.models import Inventory, InventoryTransaction, Product
from apps.pos import services
from apps.pos.models import SalesTransaction

pytestmark = pytest.mark.django_db


@pytest.fixture
def branch():
    return Branch.objects.create(name="Lipa City", code="LIPA", is_kahero_branch=False)


@pytest.fixture
def kahero_branch():
    return Branch.objects.create(name="Alangilan", code="ALANGILAN", is_kahero_branch=True)


@pytest.fixture
def product():
    return Product.objects.create(name="Spanish Latte", price="125.00")


@pytest.fixture
def cashier(branch):
    role = Role.objects.create(name=Role.BRANCH_STAFF)
    user = User.objects.create_user(
        username="cashier1", password="testpass123", role=role, branch=branch
    )
    pin = CashierPIN.objects.create(user=user)
    pin.set_pin("1234")
    pin.save()
    return user


@pytest.fixture
def unlocked_client(client, cashier, branch):
    client.force_login(cashier)
    session = client.session
    session["selected_branch_id"] = branch.pk
    session["pos_unlocked"] = True
    session.save()
    return client


class TestCompleteSalePayment:
    """Service-layer tests for the core payment logic -- the most
    consequential function in the system so far, since it moves real money
    and real stock. Tested directly, not just through the view."""

    def test_cannot_pay_for_an_empty_order(self, cashier, branch):
        draft = services.get_or_create_draft_transaction(cashier, branch)
        with pytest.raises(services.PaymentError, match="empty order"):
            services.complete_sale_payment(draft, "CASH", "100.00")

    def test_invalid_payment_method_is_rejected(self, cashier, branch, product):
        draft = services.get_or_create_draft_transaction(cashier, branch)
        services.add_catalog_item(draft, product.pk, 1)
        with pytest.raises(services.PaymentError, match="payment method"):
            services.complete_sale_payment(draft, "BITCOIN", "1000.00")

    def test_insufficient_amount_tendered_is_rejected(self, cashier, branch, product):
        draft = services.get_or_create_draft_transaction(cashier, branch)
        services.add_catalog_item(draft, product.pk, 1)  # total = 125.00
        with pytest.raises(services.PaymentError, match="less than the total"):
            services.complete_sale_payment(draft, "CASH", "100.00")

    def test_valid_payment_completes_the_transaction(self, cashier, branch, product):
        draft = services.get_or_create_draft_transaction(cashier, branch)
        services.add_catalog_item(draft, product.pk, 2)  # total = 250.00

        result = services.complete_sale_payment(draft, "CASH", "300.00")

        assert result.status == SalesTransaction.Status.COMPLETED
        assert result.payment_method == "CASH"
        assert result.amount_tendered == pytest.approx(300.00)
        assert result.change_due == pytest.approx(50.00)
        assert result.completed_at is not None

    def test_native_branch_sale_deducts_inventory(self, cashier, branch, product):
        Inventory.objects.create(branch=branch, product=product, quantity_on_hand=10)
        draft = services.get_or_create_draft_transaction(cashier, branch)
        services.add_catalog_item(draft, product.pk, 3)

        services.complete_sale_payment(draft, "CASH", "500.00")

        inventory = Inventory.objects.get(branch=branch, product=product)
        assert inventory.quantity_on_hand == 7

    def test_native_branch_sale_logs_inventory_transaction(self, cashier, branch, product):
        draft = services.get_or_create_draft_transaction(cashier, branch)
        services.add_catalog_item(draft, product.pk, 2)

        services.complete_sale_payment(draft, "CASH", "500.00")

        movement = InventoryTransaction.objects.get(branch=branch, product=product)
        assert movement.movement_type == InventoryTransaction.MovementType.SALE_DEDUCTION
        assert movement.quantity_change == -2

    def test_kahero_branch_sale_does_not_deduct_inventory(self, kahero_branch, product):
        """The core architectural rule (Phase 2/3): Alangilan's inventory is
        reconciled through the batch-import pipeline, not real-time POS
        sales. A sale there must NOT trigger this hook."""
        role = Role.objects.create(name=Role.BRANCH_STAFF)
        cashier = User.objects.create_user(
            username="alangilan_cashier",
            password="testpass123",
            role=role,
            branch=kahero_branch,
        )
        Inventory.objects.create(branch=kahero_branch, product=product, quantity_on_hand=10)
        draft = services.get_or_create_draft_transaction(cashier, kahero_branch)
        services.add_catalog_item(draft, product.pk, 3)

        services.complete_sale_payment(draft, "CASH", "500.00")

        inventory = Inventory.objects.get(branch=kahero_branch, product=product)
        assert inventory.quantity_on_hand == 10  # unchanged
        assert not InventoryTransaction.objects.filter(branch=kahero_branch).exists()

    def test_custom_item_does_not_affect_inventory(self, cashier, branch):
        """Off-menu items (Fig. 3-13) aren't in the product catalog, so
        there's nothing to deduct stock from -- must not error either."""
        draft = services.get_or_create_draft_transaction(cashier, branch)
        services.add_custom_item(draft, "Extra Whipped Cream", "20.00")

        services.complete_sale_payment(draft, "CASH", "50.00")

        assert not InventoryTransaction.objects.exists()

    def test_payment_completing_leaves_a_fresh_draft_available(self, cashier, branch, product):
        """After payment, the next add-to-cart should start a NEW draft,
        not reuse the just-completed one."""
        draft = services.get_or_create_draft_transaction(cashier, branch)
        services.add_catalog_item(draft, product.pk, 1)
        services.complete_sale_payment(draft, "CASH", "200.00")

        new_draft = services.get_or_create_draft_transaction(cashier, branch)
        assert new_draft.pk != draft.pk
        assert new_draft.status == SalesTransaction.Status.DRAFT
        assert not new_draft.items.exists()


class TestPaymentView:
    def test_payment_page_loads_with_current_draft(self, unlocked_client, cashier, branch, product):
        draft = services.get_or_create_draft_transaction(cashier, branch)
        services.add_catalog_item(draft, product.pk, 1)

        response = unlocked_client.get(reverse("pos:payment"))
        assert response.status_code == 200
        assert b"Spanish Latte" in response.content

    def test_successful_payment_redirects_to_receipt(
        self, unlocked_client, cashier, branch, product
    ):
        draft = services.get_or_create_draft_transaction(cashier, branch)
        services.add_catalog_item(draft, product.pk, 1)

        response = unlocked_client.post(
            reverse("pos:payment"), {"payment_method": "CASH", "amount_tendered": "200.00"}
        )
        assert response.status_code == 302
        assert response.url == reverse("pos:receipt", kwargs={"transaction_id": draft.pk})

    def test_insufficient_payment_shows_error_and_stays_on_page(
        self, unlocked_client, cashier, branch, product
    ):
        draft = services.get_or_create_draft_transaction(cashier, branch)
        services.add_catalog_item(draft, product.pk, 1)

        response = unlocked_client.post(
            reverse("pos:payment"), {"payment_method": "CASH", "amount_tendered": "10.00"}
        )
        assert response.status_code == 200
        assert b"less than the total" in response.content

        draft.refresh_from_db()
        assert draft.status == SalesTransaction.Status.DRAFT  # not completed


class TestReceiptView:
    def test_cashier_can_view_their_own_completed_receipt(
        self, unlocked_client, cashier, branch, product
    ):
        draft = services.get_or_create_draft_transaction(cashier, branch)
        services.add_catalog_item(draft, product.pk, 1)
        services.complete_sale_payment(draft, "CASH", "200.00")

        response = unlocked_client.get(reverse("pos:receipt", kwargs={"transaction_id": draft.pk}))
        assert response.status_code == 200
        assert b"Spanish Latte" in response.content

    def test_cannot_view_another_cashiers_receipt(self, unlocked_client, cashier, branch, product):
        other_cashier = User.objects.create_user(
            username="other_cashier", password="testpass123", role=cashier.role, branch=branch
        )
        other_draft = services.get_or_create_draft_transaction(other_cashier, branch)
        services.add_catalog_item(other_draft, product.pk, 1)
        services.complete_sale_payment(other_draft, "CASH", "200.00")

        response = unlocked_client.get(
            reverse("pos:receipt", kwargs={"transaction_id": other_draft.pk})
        )
        assert response.status_code == 404

    def test_cannot_view_a_still_draft_transaction_as_a_receipt(
        self, unlocked_client, cashier, branch, product
    ):
        draft = services.get_or_create_draft_transaction(cashier, branch)
        services.add_catalog_item(draft, product.pk, 1)  # never paid

        response = unlocked_client.get(reverse("pos:receipt", kwargs={"transaction_id": draft.pk}))
        assert response.status_code == 404
