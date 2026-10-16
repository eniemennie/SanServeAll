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
        # The service now rejects an over-total discount outright (see
        # TestInvalidDiscountInput), so set the fields directly to keep
        # exercising the model's own safety cap -- still the second layer
        # of defense if items are removed after a valid discount was applied.
        draft.transaction_discount_category = "SD"
        draft.transaction_discount_type = "AMOUNT"
        draft.transaction_discount_amount = Decimal("9999.00")
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


class TestInvalidDiscountInput:
    """Negative tests: bad discount input must be rejected and leave the
    order untouched. Found by negative testing during Lab Activity 2 -- a
    percent over 100 (or a fixed amount over the order total) used to be
    accepted and silently turned the whole order into a free one, and
    NaN/Infinity crashed the server instead of being rejected."""

    @pytest.mark.parametrize(
        "discount_type, amount",
        [
            ("PERCENT", "-20"),  # negative
            ("PERCENT", "abc"),  # not a number
            ("PERCENT", None),  # blank
            ("PERCENT", "150"),  # over 100%
            ("PERCENT", "100.01"),  # just over 100%
            ("AMOUNT", "200"),  # more than the P125 order total
            ("PERCENT", "NaN"),  # crashed with an unhandled error
            ("PERCENT", "Infinity"),  # crashed with an unhandled error
        ],
    )
    def test_invalid_discount_is_rejected_and_order_unchanged(
        self, draft, item, discount_type, amount
    ):
        accepted = services.apply_transaction_discount(draft, "SD", discount_type, amount)
        draft.refresh_from_db()
        assert accepted is False
        assert draft.grand_total == Decimal("125.00")  # still full price

    @pytest.mark.parametrize(
        "discount_type, amount, expected_total",
        [
            ("PERCENT", "20", "100.00"),  # normal
            ("PERCENT", "100", "0.00"),  # exactly 100% is allowed (complimentary)
            ("AMOUNT", "125", "0.00"),  # exactly the order total is allowed
        ],
    )
    def test_valid_boundary_discounts_are_still_accepted(
        self, draft, item, discount_type, amount, expected_total
    ):
        accepted = services.apply_transaction_discount(draft, "SD", discount_type, amount)
        draft.refresh_from_db()
        assert accepted is True
        assert draft.grand_total == Decimal(expected_total)

    def test_over_100_percent_shows_an_error_and_does_not_apply(self, unlocked_client, draft, item):
        response = unlocked_client.post(
            reverse("pos:apply_transaction_discount"),
            {"category": "SD", "discount_type": "PERCENT", "amount": "150"},
        )
        follow = unlocked_client.get(response.url)
        draft.refresh_from_db()
        assert b"valid discount amount" in follow.content
        assert draft.grand_total == Decimal("125.00")


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


class TestSettingsModalMarkup:
    """Settings (Row 3 redesign) is almost entirely client-side JS/CSS
    (localStorage persistence, live dark theme and left-handed layout) --
    real verification for this batch is interactive browser testing, not
    server-rendered assertions. These just confirm the modal and its
    hooks are genuinely present in the page for that JS to attach to."""

    def test_settings_gear_icon_present(self, unlocked_client):
        response = unlocked_client.get(reverse("pos:ordering"))
        assert b'data-bs-target="#settingsModal"' in response.content

    def test_settings_modal_has_all_six_controls(self, unlocked_client):
        response = unlocked_client.get(reverse("pos:ordering"))
        for control_id in (
            "setting-view-mode",
            "setting-item-view",
            "setting-dark-theme",
            "setting-left-handed",
            "setting-show-order-type",
            "setting-print-receipt",
        ):
            assert control_id.encode() in response.content

    def test_layout_swap_hooks_present_on_both_columns(self, unlocked_client):
        response = unlocked_client.get(reverse("pos:ordering"))
        assert b"jc-catalog-col" in response.content
        assert b"jc-summary-col" in response.content
        assert b'id="pos-layout-row"' in response.content

    def test_dining_option_section_has_toggle_hook(self, unlocked_client):
        response = unlocked_client.get(reverse("pos:ordering"))
        assert b'id="dining-option-section"' in response.content
