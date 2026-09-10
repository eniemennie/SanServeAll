"""
Tests for Row 3 redesign, Batch 2: Product Customization modal -- size,
sugar level, add-ons, and per-item discounts, computed in one step
rather than the original add-then-edit flow.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounts.models import Branch, CashierPIN, Role, User
from apps.inventory.models import Product
from apps.pos import services
from apps.pos.models import AddOn, SalesTransaction

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
def beverage(branch):
    """A product with size options and beverage category, matching the
    reference's Spanish Latte example."""
    return Product.objects.create(
        name="Spanish Latte",
        price=Decimal("95.00"),
        large_price=Decimal("105.00"),
        category=Product.Category.COFFEE,
    )


@pytest.fixture
def simple_product(branch):
    """A non-beverage product with no size options, matching the
    reference's "Lasagna" example (no size/sugar/add-ons shown)."""
    return Product.objects.create(
        name="Lasagna", price=Decimal("145.00"), category=Product.Category.PASTA
    )


@pytest.fixture
def draft(cashier, branch):
    return services.get_or_create_draft_transaction(cashier, branch)


class TestComputeCustomizedPrice:
    def test_small_size_uses_base_price(self, beverage):
        final_price, discount, before = services.compute_customized_price(
            beverage, "SMALL", [], "", None
        )
        assert final_price == Decimal("95.00")

    def test_large_size_uses_large_price(self, beverage):
        final_price, discount, before = services.compute_customized_price(
            beverage, "LARGE", [], "", None
        )
        assert final_price == Decimal("105.00")

    def test_large_size_ignored_for_products_with_no_size_options(self, simple_product):
        """A product with no large_price set must never accidentally
        charge a size upcharge just because 'LARGE' was submitted."""
        final_price, discount, before = services.compute_customized_price(
            simple_product, "LARGE", [], "", None
        )
        assert final_price == Decimal("145.00")

    def test_addons_add_to_the_price(self, beverage):
        oat_milk = AddOn.objects.create(name="Oat Milk", price=Decimal("30.00"))
        final_price, discount, before = services.compute_customized_price(
            beverage, "SMALL", [oat_milk.pk], "", None
        )
        assert final_price == Decimal("125.00")

    def test_multiple_addons_all_apply(self, beverage):
        oat_milk = AddOn.objects.create(name="Oat Milk", price=Decimal("30.00"))
        vanilla = AddOn.objects.create(name="Vanilla Syrup", price=Decimal("15.00"))
        final_price, discount, before = services.compute_customized_price(
            beverage, "SMALL", [oat_milk.pk, vanilla.pk], "", None
        )
        assert final_price == Decimal("140.00")

    def test_inactive_addons_are_excluded(self, beverage):
        inactive = AddOn.objects.create(
            name="Discontinued Syrup", price=Decimal("50.00"), is_active=False
        )
        final_price, discount, before = services.compute_customized_price(
            beverage, "SMALL", [inactive.pk], "", None
        )
        assert final_price == Decimal("95.00")

    def test_amount_discount_matches_the_reference_example(self, beverage):
        """Real numbers from the reference: Spanish Latte + Oat Milk =
        P125.00, Special Discount of P45 -> P80.00 after discount."""
        oat_milk = AddOn.objects.create(name="Oat Milk", price=Decimal("30.00"))
        final_price, discount_value, before = services.compute_customized_price(
            beverage, "SMALL", [oat_milk.pk], "AMOUNT", "45.00"
        )
        assert before == Decimal("125.00")
        assert discount_value == Decimal("45.00")
        assert final_price == Decimal("80.00")

    def test_percent_discount_computes_correctly(self, beverage):
        final_price, discount_value, before = services.compute_customized_price(
            beverage, "SMALL", [], "PERCENT", "20"
        )
        assert before == Decimal("95.00")
        assert discount_value == Decimal("19.00")
        assert final_price == Decimal("76.00")

    def test_discount_never_pushes_price_below_zero(self, beverage):
        final_price, discount_value, before = services.compute_customized_price(
            beverage, "SMALL", [], "AMOUNT", "9999.00"
        )
        assert final_price == Decimal("0.00")
        assert discount_value == before  # capped, not the full 9999

    def test_zero_discount_amount_applies_no_discount(self, beverage):
        final_price, discount_value, before = services.compute_customized_price(
            beverage, "SMALL", [], "AMOUNT", "0"
        )
        assert discount_value == Decimal("0.00")
        assert final_price == Decimal("95.00")


class TestAddCustomizedItemService:
    def test_creates_item_with_computed_price(self, draft, beverage):
        item = services.add_customized_item(draft, beverage.pk, size="LARGE")
        assert item.unit_price == Decimal("105.00")

    def test_stores_customizations_for_display(self, draft, beverage):
        oat_milk = AddOn.objects.create(name="Oat Milk", price=Decimal("30.00"))
        item = services.add_customized_item(
            draft, beverage.pk, size="SMALL", sugar_level="50%", addon_ids=[oat_milk.pk]
        )
        assert item.customizations["size"] == "Small"
        assert item.customizations["sugar_level"] == "50%"
        assert item.customizations["add_ons"] == ["Oat Milk"]

    def test_simple_product_gets_no_size_label(self, draft, simple_product):
        """A product with no size options should not show a false
        'Small' label just because that's the internal default."""
        item = services.add_customized_item(draft, simple_product.pk)
        assert item.customizations["size"] == ""

    def test_invalid_product_id_returns_none_not_a_crash(self, draft):
        assert services.add_customized_item(draft, 999999) is None

    def test_inactive_product_cannot_be_added(self, draft, beverage):
        beverage.is_active = False
        beverage.save()
        assert services.add_customized_item(draft, beverage.pk) is None

    def test_discount_category_stored_when_present(self, draft, beverage):
        item = services.add_customized_item(
            draft,
            beverage.pk,
            discount_category="SPECIAL_DISCOUNT",
            discount_type="AMOUNT",
            discount_amount="45.00",
        )
        assert item.customizations["discount_category"] == "Special Discount"
        assert item.unit_price == Decimal("50.00")  # 95 - 45

    def test_no_discount_category_is_not_stored(self, draft, beverage):
        item = services.add_customized_item(draft, beverage.pk, discount_category="NO_DISCOUNT")
        assert item.customizations["discount_category"] == ""


class TestAddCustomizedItemView:
    def test_adds_item_and_redirects_to_ordering(self, unlocked_client, beverage):
        response = unlocked_client.post(
            reverse("pos:add_customized_item"),
            {"product_id": beverage.pk, "size": "LARGE", "quantity": "1"},
        )
        assert response.status_code == 302
        assert response.url == reverse("pos:ordering")

    def test_item_appears_in_the_draft_with_correct_price(
        self, unlocked_client, beverage, cashier, branch
    ):
        unlocked_client.post(
            reverse("pos:add_customized_item"),
            {"product_id": beverage.pk, "size": "LARGE", "quantity": "2"},
        )
        draft_transaction = SalesTransaction.objects.get(
            cashier=cashier, branch=branch, status=SalesTransaction.Status.DRAFT
        )
        item = draft_transaction.items.first()
        assert item.unit_price == Decimal("105.00")
        assert item.quantity == 2
        assert item.subtotal == Decimal("210.00")

    def test_invalid_product_shows_error_message(self, unlocked_client):
        response = unlocked_client.post(reverse("pos:add_customized_item"), {"product_id": 999999})
        assert response.status_code == 302
        follow = unlocked_client.get(response.url)
        assert b"could not be added" in follow.content
