"""
Tests for task #143: the Purchasing role and the Supplier model/screen.
"""

import pytest
from django.urls import reverse

from apps.accounts.models import Branch, Role, User
from apps.purchasing.models import Supplier
from conftest import verify_otp_for_client

pytestmark = pytest.mark.django_db


@pytest.fixture
def roles():
    return {
        "owner_admin": Role.objects.create(name=Role.OWNER_ADMIN),
        "branch_staff": Role.objects.create(name=Role.BRANCH_STAFF),
        "commissary_staff": Role.objects.create(name=Role.COMMISSARY_STAFF),
        "purchasing": Role.objects.create(name=Role.PURCHASING),
    }


@pytest.fixture
def owner(roles):
    return User.objects.create_user(
        username="owner1", password="testpass123", role=roles["owner_admin"]
    )


@pytest.fixture
def purchasing_staff(roles):
    return User.objects.create_user(
        username="purchasing1", password="testpass123", role=roles["purchasing"]
    )


@pytest.fixture
def branch_staff(roles):
    branch = Branch.objects.create(name="Batangas City", code="BATANGAS")
    return User.objects.create_user(
        username="branchstaff1",
        password="testpass123",
        role=roles["branch_staff"],
        branch=branch,
    )


@pytest.fixture
def commissary_staff(roles):
    branch = Branch.objects.create(name="Commissary", code="COMMISSARY", is_commissary=True)
    return User.objects.create_user(
        username="commissary1",
        password="testpass123",
        role=roles["commissary_staff"],
        branch=branch,
    )


class TestSupplierModel:
    def test_str_is_the_supplier_name(self):
        supplier = Supplier.objects.create(name="Acme Food Supply")
        assert str(supplier) == "Acme Food Supply"

    def test_is_active_defaults_to_true(self):
        supplier = Supplier.objects.create(name="Acme Food Supply")
        assert supplier.is_active is True

    def test_name_must_be_unique(self):
        Supplier.objects.create(name="Acme Food Supply")
        with pytest.raises(Exception):
            Supplier.objects.create(name="Acme Food Supply")


class TestSupplierManagementAccess:
    def test_owner_admin_can_view_the_page(self, client, owner):
        client.force_login(owner)
        verify_otp_for_client(client, owner)
        response = client.get(reverse("purchasing:supplier_management"))
        assert response.status_code == 200

    def test_purchasing_staff_can_view_the_page(self, client, purchasing_staff):
        client.force_login(purchasing_staff)
        response = client.get(reverse("purchasing:supplier_management"))
        assert response.status_code == 200

    def test_branch_staff_is_forbidden(self, client, branch_staff):
        client.force_login(branch_staff)
        response = client.get(reverse("purchasing:supplier_management"))
        assert response.status_code == 403

    def test_commissary_staff_is_forbidden(self, client, commissary_staff):
        client.force_login(commissary_staff)
        response = client.get(reverse("purchasing:supplier_management"))
        assert response.status_code == 403

    def test_anonymous_user_is_redirected_to_login(self, client):
        response = client.get(reverse("purchasing:supplier_management"))
        assert response.status_code == 302
        assert "login" in response.url


class TestSupplierManagementCreate:
    def test_purchasing_staff_can_add_a_supplier(self, client, purchasing_staff):
        client.force_login(purchasing_staff)
        response = client.post(
            reverse("purchasing:supplier_management"),
            {
                "name": "Acme Food Supply",
                "contact_person": "Juan Dela Cruz",
                "phone_number": "0917-555-1234",
                "email": "juan@acmefood.ph",
                "address": "123 Market St.",
                "notes": "Net 15 terms.",
            },
        )
        assert response.status_code == 200
        supplier = Supplier.objects.get(name="Acme Food Supply")
        assert supplier.contact_person == "Juan Dela Cruz"
        assert supplier.email == "juan@acmefood.ph"

    def test_blank_name_shows_an_error_and_creates_nothing(self, client, purchasing_staff):
        client.force_login(purchasing_staff)
        response = client.post(reverse("purchasing:supplier_management"), {"name": "  "})
        assert response.status_code == 200
        assert "required" in response.context["error"].lower()
        assert Supplier.objects.count() == 0

    def test_duplicate_name_is_rejected_case_insensitively(self, client, purchasing_staff):
        Supplier.objects.create(name="Acme Food Supply")
        client.force_login(purchasing_staff)
        response = client.post(
            reverse("purchasing:supplier_management"), {"name": "acme food supply"}
        )
        assert response.status_code == 200
        assert "already exists" in response.context["error"].lower()
        assert Supplier.objects.count() == 1


class TestSupplierToggleActive:
    def test_purchasing_staff_can_deactivate_a_supplier(self, client, purchasing_staff):
        supplier = Supplier.objects.create(name="Acme Food Supply")
        client.force_login(purchasing_staff)
        client.post(reverse("purchasing:supplier_toggle_active", args=[supplier.pk]))
        supplier.refresh_from_db()
        assert supplier.is_active is False

    def test_toggling_twice_reactivates_it(self, client, purchasing_staff):
        supplier = Supplier.objects.create(name="Acme Food Supply")
        client.force_login(purchasing_staff)
        url = reverse("purchasing:supplier_toggle_active", args=[supplier.pk])
        client.post(url)
        client.post(url)
        supplier.refresh_from_db()
        assert supplier.is_active is True

    def test_branch_staff_is_forbidden(self, client, branch_staff):
        supplier = Supplier.objects.create(name="Acme Food Supply")
        client.force_login(branch_staff)
        response = client.post(reverse("purchasing:supplier_toggle_active", args=[supplier.pk]))
        assert response.status_code == 403
