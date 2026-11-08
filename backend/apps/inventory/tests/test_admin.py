"""
Tests for the Inventory admin hardening (Task tracker, 2026-11-08
reconciliation finding): `quantity_on_hand` must be read-only on the
admin change form, since every other write to it goes through a
logged InventoryTransaction and this was the one path that didn't.
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounts.models import Branch
from apps.inventory.models import Inventory, Product
from conftest import verify_otp_for_client

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def admin_client(client):
    admin = User.objects.create_superuser(
        username="admin_test", email="admin@example.com", password="testpass123"
    )
    client.force_login(admin)
    verify_otp_for_client(client, admin)
    return client


@pytest.fixture
def branch():
    return Branch.objects.create(name="Batangas City", code="BATANGAS")


@pytest.fixture
def material():
    return Product.objects.create(
        name="Ice", price="0.00", product_type=Product.ProductType.MATERIAL
    )


@pytest.fixture
def inventory_row(branch, material):
    return Inventory.objects.create(branch=branch, product=material, quantity_on_hand="10.000")


class TestInventoryAdminHardening:
    def test_inventory_changelist_loads(self, admin_client):
        response = admin_client.get(reverse("admin:inventory_inventory_changelist"))
        assert response.status_code == 200

    def test_quantity_on_hand_is_not_an_editable_input_on_the_change_form(
        self, admin_client, inventory_row
    ):
        url = reverse("admin:inventory_inventory_change", args=[inventory_row.pk])
        response = admin_client.get(url)
        assert response.status_code == 200
        assert b'name="quantity_on_hand"' not in response.content
        # Still visible, just not an editable widget -- the value itself
        # should still appear somewhere in the rendered read-only row.
        assert b"10.000" in response.content

    def test_reorder_threshold_remains_editable(self, admin_client, inventory_row):
        url = reverse("admin:inventory_inventory_change", args=[inventory_row.pk])
        response = admin_client.get(url)
        assert b'name="reorder_threshold"' in response.content

    def test_posting_a_different_quantity_through_the_admin_form_has_no_effect(
        self, admin_client, inventory_row, branch, material
    ):
        """The real regression test: even a maliciously/mistakenly
        crafted POST that includes quantity_on_hand must not move the
        stored value, since Django excludes readonly_fields from the
        bound ModelForm regardless of what the request body contains."""
        url = reverse("admin:inventory_inventory_change", args=[inventory_row.pk])
        response = admin_client.post(
            url,
            {
                "branch": branch.pk,
                "product": material.pk,
                "quantity_on_hand": "9999.000",
                "reorder_threshold": "5.000",
                "_save": "Save",
            },
        )
        assert response.status_code == 302  # successful admin save, redirects to changelist

        inventory_row.refresh_from_db()
        assert inventory_row.quantity_on_hand == 10
        assert inventory_row.reorder_threshold == 5
