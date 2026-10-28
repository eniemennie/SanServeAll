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
def product(branch):
    """Provisioned at `branch` (an Inventory row, quantity 0) -- POS now
    only allows adding a product that's actually stocked at the
    cashier's branch (QA fix: branch-specific product availability).
    Tests that need a specific starting quantity update this row rather
    than creating a second one (branch+product is unique)."""
    product = Product.objects.create(name="Spanish Latte", price="125.00")
    Inventory.objects.create(branch=branch, product=product, quantity_on_hand=0)
    return product


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
        # `product` fixture already provisioned an Inventory row at
        # `branch` (quantity 0) -- set the real starting quantity on it
        # rather than creating a second row (branch+product is unique).
        Inventory.objects.filter(branch=branch, product=product).update(quantity_on_hand=10)
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

    def test_shows_all_four_payment_modes(self, unlocked_client, cashier, branch, product):
        """Payment method reorganization: Cash, GCash, Debit/Credit Card,
        and Other Digital Banks are all shown as equal, top-level
        buttons -- one click each, no sub-menu and no free-text field."""
        draft = services.get_or_create_draft_transaction(cashier, branch)
        services.add_catalog_item(draft, product.pk, 1)

        response = unlocked_client.get(reverse("pos:payment"))
        assert b"Cash" in response.content
        assert b"GCash" in response.content
        assert b"Debit/Credit Card" in response.content
        assert b"Other Digital Banks" in response.content

    def test_payment_methods_context_has_all_four_in_order(
        self, unlocked_client, cashier, branch, product
    ):
        draft = services.get_or_create_draft_transaction(cashier, branch)
        services.add_catalog_item(draft, product.pk, 1)

        response = unlocked_client.get(reverse("pos:payment"))
        values = [value for value, _ in response.context["payment_methods"]]
        assert values == ["CASH", "GCASH", "CARD", "OTHER_DIGITAL_BANKS"]

    def test_all_four_payment_methods_complete_successfully(
        self, unlocked_client, cashier, branch, product
    ):
        """End-to-end: every one of the four methods must be a valid,
        recordable payment method, not just a visible button."""
        for method in ["CASH", "GCASH", "CARD", "OTHER_DIGITAL_BANKS"]:
            draft = services.get_or_create_draft_transaction(cashier, branch)
            services.add_catalog_item(draft, product.pk, 1)
            draft.refresh_from_db()

            response = unlocked_client.post(
                reverse("pos:payment"),
                {"payment_method": method, "amount_tendered": str(draft.grand_total)},
            )
            assert response.status_code == 302

            draft.refresh_from_db()
            assert draft.status == SalesTransaction.Status.COMPLETED
            assert draft.payment_method == method

    def test_shows_the_quick_amount_buttons(self, unlocked_client, cashier, branch, product):
        draft = services.get_or_create_draft_transaction(cashier, branch)
        services.add_catalog_item(draft, product.pk, 1)

        response = unlocked_client.get(reverse("pos:payment"))
        for expected in (b'data-amount="1"', b'data-amount="500"', b'data-amount="1000"'):
            assert expected in response.content

    def test_shows_the_grand_total_not_just_subtotal(
        self, unlocked_client, cashier, branch, product
    ):
        """Row 3 redesign: confirms the payment page displays
        grand_total (post transaction-discount) as the actual amount
        due, not just the raw subtotal."""
        draft = services.get_or_create_draft_transaction(cashier, branch)
        services.add_catalog_item(draft, product.pk, 1)
        services.apply_transaction_discount(draft, "SD", "AMOUNT", "20.00")

        response = unlocked_client.get(reverse("pos:payment"))
        assert str(draft.grand_total).encode() in response.content

    def test_successful_payment_redirects_to_ordering_with_completion_flag(
        self, unlocked_client, cashier, branch, product
    ):
        """Row 3 redesign: successful payment now redirects back to the
        ordering screen (which auto-opens the Transaction Complete modal
        for this specific transaction), not to the standalone receipt
        page directly -- that page/URL still exists separately for
        later re-viewing or reprinting a past transaction."""
        draft = services.get_or_create_draft_transaction(cashier, branch)
        services.add_catalog_item(draft, product.pk, 1)

        response = unlocked_client.post(
            reverse("pos:payment"), {"payment_method": "CASH", "amount_tendered": "200.00"}
        )
        assert response.status_code == 302
        assert response.url == reverse("pos:ordering")

        follow = unlocked_client.get(response.url)
        assert b'id="transactionCompleteModal"' in follow.content
        assert b"Spanish Latte" in follow.content

    def test_completion_modal_does_not_reappear_on_a_page_refresh(
        self, unlocked_client, cashier, branch, product
    ):
        """The session flag is a genuine one-time signal -- a page
        refresh right after must not keep re-showing the modal for an
        old, already-acknowledged transaction."""
        draft = services.get_or_create_draft_transaction(cashier, branch)
        services.add_catalog_item(draft, product.pk, 1)
        unlocked_client.post(
            reverse("pos:payment"), {"payment_method": "CASH", "amount_tendered": "200.00"}
        )

        first_load = unlocked_client.get(reverse("pos:ordering"))
        assert b'id="transactionCompleteModal"' in first_load.content

        second_load = unlocked_client.get(reverse("pos:ordering"))
        assert b'id="transactionCompleteModal"' not in second_load.content

    def test_print_css_isolates_the_modal_without_a_fixed_position_bug(
        self, unlocked_client, cashier, branch, product
    ):
        """Regression test for the "3 identical copies" print/PDF bug:
        the old @media print rule only used `visibility: hidden` on the
        rest of the page, which doesn't collapse layout height, while
        the modal itself kept Bootstrap's `position: fixed` -- a
        fixed-position element is redrawn on every paginated print
        page, so a tall (invisible) POS page produced that many
        identical printed copies of the receipt. The fix hides every
        other direct child of <body> with `display: none` (actually
        removing them from layout) and drops the modal's fixed
        positioning during print. This only checks the rule is present
        in the rendered page -- Django's test client can't execute
        real browser pagination -- so it guards against someone
        reverting to the old `visibility`-only approach by accident."""
        draft = services.get_or_create_draft_transaction(cashier, branch)
        services.add_catalog_item(draft, product.pk, 1)
        unlocked_client.post(
            reverse("pos:payment"), {"payment_method": "CASH", "amount_tendered": "200.00"}
        )

        response = unlocked_client.get(reverse("pos:ordering"))

        # The fix: other direct children of <body> are actually removed
        # from layout (not just hidden), and the modal's fixed
        # positioning is dropped during print.
        assert b"body > *:not(#transactionCompleteModal) { display: none !important; }" in (
            response.content
        )
        assert b"position: static !important;" in response.content

        # The old buggy pattern must not come back: visibility alone
        # doesn't collapse layout height, which is what caused the
        # multi-page repeat in the first place.
        assert b"body * { visibility: hidden; }" not in response.content

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
