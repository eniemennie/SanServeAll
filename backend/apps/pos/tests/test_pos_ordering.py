"""
Tests for Week 4 (Row 4.1-4.3): POS Ordering Screen, Add Custom Product,
Order Customization.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounts.models import Branch, CashierPIN, Role, User
from apps.inventory.models import Product
from apps.pos.models import SalesItem, SalesTransaction

pytestmark = pytest.mark.django_db


@pytest.fixture
def branch():
    return Branch.objects.create(name="Lipa City", code="LIPA")


@pytest.fixture
def role():
    return Role.objects.create(name=Role.BRANCH_STAFF)


@pytest.fixture
def cashier(role, branch):
    user = User.objects.create_user(
        username="cashier1", password="testpass123", role=role, branch=branch
    )
    pin = CashierPIN.objects.create(user=user)
    pin.set_pin("1234")
    pin.save()
    return user


@pytest.fixture
def unlocked_client(client, cashier, branch):
    """A client that's already completed the full login -> branch ->
    PIN flow -- POS views require all three, per pos_unlock_required."""
    client.force_login(cashier)
    session = client.session
    session["selected_branch_id"] = branch.pk
    session["pos_unlocked"] = True
    session.save()
    return client


@pytest.fixture
def product(branch):
    return Product.objects.create(name="Spanish Latte", price=Decimal("120.00"))


class TestPosUnlockRequired:
    def test_anonymous_user_redirected_to_login(self, client):
        response = client.get(reverse("pos:ordering"))
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_logged_in_without_branch_selected_redirects_to_branch_selection(self, client, cashier):
        client.force_login(cashier)
        response = client.get(reverse("pos:ordering"))
        assert response.status_code == 302
        assert response.url == reverse("accounts:select_branch")

    def test_branch_selected_without_pin_redirects_to_pin_screen(self, client, cashier, branch):
        client.force_login(cashier)
        session = client.session
        session["selected_branch_id"] = branch.pk
        session.save()
        response = client.get(reverse("pos:ordering"))
        assert response.status_code == 302
        assert response.url == reverse("accounts:cashier_pin")

    def test_fully_unlocked_reaches_ordering_screen(self, unlocked_client):
        response = unlocked_client.get(reverse("pos:ordering"))
        assert response.status_code == 200


class TestPosOrdering:
    def test_active_products_are_listed(self, unlocked_client, product):
        response = unlocked_client.get(reverse("pos:ordering"))
        assert b"Spanish Latte" in response.content

    def test_inactive_products_are_not_listed(self, unlocked_client, product):
        product.is_active = False
        product.save()
        response = unlocked_client.get(reverse("pos:ordering"))
        assert b"Spanish Latte" not in response.content

    def test_search_filters_products(self, unlocked_client, product):
        Product.objects.create(name="Iced Tea", price=Decimal("50.00"))
        response = unlocked_client.get(reverse("pos:ordering"), {"q": "Latte"})
        assert b"Spanish Latte" in response.content
        assert b"Iced Tea" not in response.content

    def test_adding_catalog_item_creates_a_draft_transaction_and_item(
        self, unlocked_client, product, cashier, branch
    ):
        unlocked_client.post(
            reverse("pos:add_catalog_item"), {"product_id": product.pk, "quantity": 2}
        )
        draft = SalesTransaction.objects.get(
            cashier=cashier, branch=branch, status=SalesTransaction.Status.DRAFT
        )
        item = draft.items.get(product=product)
        assert item.quantity == 2
        assert item.unit_price == product.price

    def test_repeated_visits_reuse_the_same_draft_transaction(
        self, unlocked_client, product, cashier, branch
    ):
        unlocked_client.post(reverse("pos:add_catalog_item"), {"product_id": product.pk})
        unlocked_client.get(reverse("pos:ordering"))
        unlocked_client.post(reverse("pos:add_catalog_item"), {"product_id": product.pk})

        drafts = SalesTransaction.objects.filter(
            cashier=cashier, branch=branch, status=SalesTransaction.Status.DRAFT
        )
        assert drafts.count() == 1
        assert drafts.first().items.count() == 2

    def test_page_links_to_payment_once_an_item_is_added(self, unlocked_client, product):
        """Regression test: the ordering screen previously showed a
        Total but had no actual link anywhere to reach Payment -- a
        cashier had no way to check out through the real UI at all.
        Every prior test/walkthrough reached pos:payment directly via
        reverse() or a hand-built URL, never by clicking through the
        page itself, which is exactly how this went undetected."""
        unlocked_client.post(reverse("pos:add_catalog_item"), {"product_id": product.pk})
        response = unlocked_client.get(reverse("pos:ordering"))
        assert reverse("pos:payment").encode() in response.content
        assert b"Proceed to Payment" in response.content

    def test_payment_link_is_disabled_when_cart_is_empty(self, unlocked_client):
        response = unlocked_client.get(reverse("pos:ordering"))
        assert b"disabled" in response.content
        assert b"Add an item to continue" in response.content


class TestAddCustomProduct:
    def test_valid_custom_item_is_added_to_draft(self, unlocked_client, cashier, branch):
        response = unlocked_client.post(
            reverse("pos:add_custom_product"), {"name": "Special Cake Slice", "price": "85.50"}
        )
        assert response.status_code == 302
        draft = SalesTransaction.objects.get(cashier=cashier, branch=branch)
        item = draft.items.get(custom_name="Special Cake Slice")
        assert item.product is None
        assert item.unit_price == Decimal("85.50")

    def test_blank_name_is_rejected(self, unlocked_client):
        response = unlocked_client.post(
            reverse("pos:add_custom_product"), {"name": "  ", "price": "10.00"}
        )
        assert response.status_code == 200
        assert b"valid name" in response.content

    def test_negative_price_is_rejected(self, unlocked_client):
        response = unlocked_client.post(
            reverse("pos:add_custom_product"), {"name": "Item", "price": "-5.00"}
        )
        assert response.status_code == 200
        assert b"valid name" in response.content


class TestOrderCustomization:
    def test_customize_updates_quantity_and_options(
        self, unlocked_client, product, cashier, branch
    ):
        unlocked_client.post(reverse("pos:add_catalog_item"), {"product_id": product.pk})
        item = SalesItem.objects.get(product=product)

        response = unlocked_client.post(
            reverse("pos:customize_item", args=[item.pk]),
            {"quantity": 3, "size": "Large", "sugar_level": "50%", "add_ons": ["Pearls"]},
        )
        assert response.status_code == 302

        item.refresh_from_db()
        assert item.quantity == 3
        assert item.customizations["size"] == "Large"
        assert item.customizations["sugar_level"] == "50%"
        assert item.customizations["add_ons"] == ["Pearls"]

    def test_cannot_customize_another_cashiers_item(self, unlocked_client, product, branch, role):
        other_cashier = User.objects.create_user(
            username="other_cashier", password="testpass123", role=role, branch=branch
        )
        other_draft = SalesTransaction.objects.create(
            cashier=other_cashier, branch=branch, status=SalesTransaction.Status.DRAFT
        )
        other_item = SalesItem.objects.create(
            transaction=other_draft, product=product, unit_price=product.price, quantity=1
        )

        response = unlocked_client.post(
            reverse("pos:customize_item", args=[other_item.pk]), {"quantity": 99}
        )
        assert response.status_code == 404


class TestRemoveItem:
    def test_removing_an_item_deletes_it_from_the_draft(
        self, unlocked_client, product, cashier, branch
    ):
        unlocked_client.post(reverse("pos:add_catalog_item"), {"product_id": product.pk})
        item = SalesItem.objects.get(product=product)

        unlocked_client.post(reverse("pos:remove_item", args=[item.pk]))
        assert not SalesItem.objects.filter(pk=item.pk).exists()
