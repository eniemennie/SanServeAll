"""
Tests for Row 3 redesign, Batch 3: dining option, whole-order discount
("% Discount"), VAT breakdown, the quick quantity stepper, and Clear All.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounts.models import Branch, CashierPIN, Role, User
from apps.inventory.models import Product
from apps.pos import services
from apps.pos.models import SalesTransaction

pytestmark = pytest.mark.django_db


@pytest.fixture
def branch():
    return Branch.objects.create(name="Lipa City", code="LIPA")


@pytest.fixture
def role():
    return Role.objects.create(name=Role.BRANCH_STAFF)


@pytest.fixture
def cashier(role, branch):
    user = User.objects.create_user(username="cashier1", role=role, branch=branch)
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


@pytest.fixture
def draft(cashier, branch):
    return services.get_or_create_draft_transaction(cashier, branch)


@pytest.fixture
def item(draft):
    product = Product.objects.create(name="Spanish Latte", price=Decimal("125.00"))
    return services.add_catalog_item(draft, product.pk, quantity=1)


class TestGrandTotalAndDiscountMath:
    """Reproduces the reference's own real numbers as the test oracle."""

    def test_grand_total_equals_subtotal_with_no_transaction_discount(self, draft, item):
        assert draft.grand_total == draft.total_amount

    def test_reference_example_spanish_latte_and_lasagna_no_transaction_discount(self, draft):
        latte = Product.objects.create(name="Spanish Latte", price=Decimal("80.00"))
        lasagna = Product.objects.create(name="Lasagna", price=Decimal("145.00"))
        services.add_catalog_item(draft, latte.pk, quantity=1)
        services.add_catalog_item(draft, lasagna.pk, quantity=1)
        assert draft.total_amount == Decimal("225.00")
        assert draft.grand_total == Decimal("225.00")

    def test_vat_breakdown_matches_the_reference_exactly(self, draft):
        latte = Product.objects.create(name="Spanish Latte", price=Decimal("80.00"))
        lasagna = Product.objects.create(name="Lasagna", price=Decimal("145.00"))
        services.add_catalog_item(draft, latte.pk, quantity=1)
        services.add_catalog_item(draft, lasagna.pk, quantity=1)

        vat_exclusive, vat_amount = draft.vat_breakdown(Decimal("12.00"))
        assert vat_exclusive == Decimal("200.89")
        assert vat_amount == Decimal("24.11")

    def test_transaction_discount_amount_reduces_grand_total(self, draft, item):
        services.apply_transaction_discount(draft, "SD", "AMOUNT", "20.00")
        assert draft.grand_total == Decimal("105.00")  # 125 - 20
        assert draft.total_amount == Decimal("125.00")  # subtotal unaffected

    def test_transaction_discount_percent_reduces_grand_total(self, draft, item):
        services.apply_transaction_discount(draft, "SC2", "PERCENT", "20")
        assert draft.grand_total == Decimal("100.00")  # 125 - 20%

    def test_transaction_discount_never_pushes_grand_total_negative(self, draft, item):
        services.apply_transaction_discount(draft, "SD", "AMOUNT", "9999.00")
        assert draft.grand_total == Decimal("0.00")

    def test_no_discount_category_means_no_transaction_discount(self, draft, item):
        services.apply_transaction_discount(draft, "NO_DISCOUNT", "", None)
        assert draft.grand_total == draft.total_amount

    def test_item_discounts_total_sums_across_items(self, draft):
        product = Product.objects.create(
            name="Spanish Latte", price=Decimal("125.00"), category=Product.Category.COFFEE
        )
        services.add_customized_item(
            draft, product.pk, discount_category="SD", discount_type="AMOUNT", discount_amount="45"
        )
        assert draft.item_discounts_total == Decimal("45.00")


class TestPaymentUsesGrandTotalNotSubtotal:
    """The real correctness concern: payment sufficiency and change must
    use the discounted grand_total, not the raw subtotal, or a cashier
    could be wrongly blocked from completing a valid payment, or a
    customer wrongly given the wrong change."""

    def test_payment_succeeds_with_amount_between_grand_total_and_subtotal(
        self, draft, item, cashier, branch
    ):
        """125 subtotal, SD20 transaction discount -> 105 grand total.
        Tendering 110 is enough against grand_total but NOT against the
        raw subtotal -- this must succeed."""
        services.apply_transaction_discount(draft, "SD", "AMOUNT", "20.00")
        services.complete_sale_payment(draft, "CASH", "110.00")
        draft.refresh_from_db()
        assert draft.status == SalesTransaction.Status.COMPLETED

    def test_change_due_is_based_on_grand_total(self, draft, item):
        services.apply_transaction_discount(draft, "SD", "AMOUNT", "20.00")
        draft.amount_tendered = Decimal("110.00")
        assert draft.change_due == Decimal("5.00")  # 110 - 105, not 110 - 125


class TestDiningOptionView:
    def test_set_dining_option_updates_the_draft(self, unlocked_client, draft):
        unlocked_client.post(reverse("pos:set_dining_option"), {"dining_option": "TAKE_OUT"})
        draft.refresh_from_db()
        assert draft.dining_option == "TAKE_OUT"

    def test_defaults_to_dine_in(self, draft):
        assert draft.dining_option == SalesTransaction.DiningOption.DINE_IN

    def test_invalid_dining_option_is_ignored(self, unlocked_client, draft):
        unlocked_client.post(
            reverse("pos:set_dining_option"), {"dining_option": "NOT_A_REAL_OPTION"}
        )
        draft.refresh_from_db()
        assert draft.dining_option == SalesTransaction.DiningOption.DINE_IN


class TestUpdateItemQuantityView:
    def test_stepper_updates_quantity(self, unlocked_client, item):
        unlocked_client.post(reverse("pos:update_item_quantity", args=[item.pk]), {"quantity": "3"})
        item.refresh_from_db()
        assert item.quantity == 3

    def test_cannot_set_quantity_below_one(self, unlocked_client, item):
        unlocked_client.post(reverse("pos:update_item_quantity", args=[item.pk]), {"quantity": "0"})
        item.refresh_from_db()
        assert item.quantity == 1  # unchanged, not zeroed


class TestClearDraftView:
    def test_clear_all_removes_every_item(self, unlocked_client, draft, item):
        assert draft.items.count() == 1
        unlocked_client.post(reverse("pos:clear_draft"))
        assert draft.items.count() == 0

    def test_clear_all_does_not_delete_the_transaction_itself(self, unlocked_client, draft, item):
        unlocked_client.post(reverse("pos:clear_draft"))
        draft.refresh_from_db()  # would raise DoesNotExist if the row were deleted
        assert draft.status == SalesTransaction.Status.DRAFT


class TestApplyTransactionDiscountView:
    def test_applies_a_valid_discount(self, unlocked_client, draft, item):
        unlocked_client.post(
            reverse("pos:apply_transaction_discount"),
            {"category": "SD", "discount_type": "AMOUNT", "amount": "20.00"},
        )
        draft.refresh_from_db()
        assert draft.grand_total == Decimal("105.00")

    def test_invalid_amount_shows_error(self, unlocked_client, draft, item):
        response = unlocked_client.post(
            reverse("pos:apply_transaction_discount"),
            {"category": "SD", "discount_type": "AMOUNT", "amount": "not-a-number"},
        )
        follow = unlocked_client.get(response.url)
        assert b"valid discount amount" in follow.content


class TestOrderSummaryDisplaysCorrectly:
    def test_shows_dining_option_buttons(self, unlocked_client):
        response = unlocked_client.get(reverse("pos:ordering"))
        assert b"Dine-in" in response.content
        assert b"Take-out" in response.content

    def test_shows_vat_breakdown_when_cart_has_items(self, unlocked_client, item):
        response = unlocked_client.get(reverse("pos:ordering"))
        assert b"Less VAT" in response.content
        assert b"VAT Exempt Sales" in response.content
        assert b"Grand Total" in response.content

    def test_no_vat_breakdown_shown_for_empty_cart(self, unlocked_client):
        response = unlocked_client.get(reverse("pos:ordering"))
        assert b"Less VAT" not in response.content
