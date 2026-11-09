"""
Tests for task #143: the Purchasing role and the Supplier model/screen.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounts.models import Branch, Role, User
from apps.inventory.models import Product
from apps.purchasing.models import PurchaseOrder, PurchaseOrderItem, Supplier
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


class TestSupplierManagementUIUXReview:
    """Client review (2026-11-09), Suppliers page layout improvement: the
    New Supplier form now opens from a modal instead of a collapse
    panel, the table gained Materials Supplied/Phone/Email columns and
    View/Edit/Deactivate actions, and a search box sits alongside the
    existing client-side Active/Inactive filter. The underlying
    `suppliers` queryset and create/toggle logic are untouched (the
    queryset is now annotated with materials_supplied_count/
    purchase_order_count, but those are additive, read-only
    annotations) -- these tests cover the presentation only."""

    def test_new_supplier_modal_is_closed_by_default_when_suppliers_already_exist(
        self, client, purchasing_staff
    ):
        Supplier.objects.create(name="Acme Food Supply")
        client.force_login(purchasing_staff)
        response = client.get(reverse("purchasing:supplier_management"))
        content = response.content.decode()
        assert 'id="newSupplierModal"' in content
        assert 'class="modal fade show d-block" id="newSupplierModal"' not in content

    def test_new_supplier_modal_auto_opens_after_a_validation_error(self, client, purchasing_staff):
        client.force_login(purchasing_staff)
        response = client.post(reverse("purchasing:supplier_management"), {"name": "  "})
        content = response.content.decode()
        assert 'class="modal fade show d-block" id="newSupplierModal"' in content

    def test_empty_state_shows_an_icon_message_and_add_first_supplier_button(
        self, client, purchasing_staff
    ):
        client.force_login(purchasing_staff)
        response = client.get(reverse("purchasing:supplier_management"))
        content = response.content.decode()
        assert "No suppliers yet" in content
        assert "Add your first supplier" in content
        # The old bare message is gone, replaced by the richer empty state.
        assert "No suppliers yet.</td>" not in content

    def test_status_filter_buttons_search_box_and_row_markers_are_present(
        self, client, purchasing_staff
    ):
        active = Supplier.objects.create(name="Acme Food Supply")
        inactive = Supplier.objects.create(name="Old Vendor Co.", is_active=False)
        client.force_login(purchasing_staff)
        response = client.get(reverse("purchasing:supplier_management"))
        content = response.content.decode()
        assert 'data-filter="active"' in content
        assert 'data-filter="inactive"' in content
        assert 'data-status="active"' in content
        assert 'data-status="inactive"' in content
        assert 'id="jc-supplier-search"' in content
        assert active.name in content
        assert inactive.name in content

    def test_view_and_edit_actions_are_present_per_supplier(self, client, purchasing_staff):
        supplier = Supplier.objects.create(name="Acme Food Supply")
        client.force_login(purchasing_staff)
        response = client.get(reverse("purchasing:supplier_management"))
        content = response.content.decode()
        assert f'id="viewSupplierModal{supplier.pk}"' in content
        assert f'id="editSupplierModal{supplier.pk}"' in content
        assert f'data-bs-target="#viewSupplierModal{supplier.pk}"' in content
        assert f'data-bs-target="#editSupplierModal{supplier.pk}"' in content

    def test_materials_supplied_count_reflects_real_purchase_orders(
        self, client, purchasing_staff, owner
    ):
        branch = Branch.objects.create(name="Batangas City", code="BATANGAS")
        supplier = Supplier.objects.create(name="Acme Food Supply")
        flour = Product.objects.create(
            name="Flour (kg)", price="55.00", product_type=Product.ProductType.MATERIAL
        )
        sugar = Product.objects.create(
            name="Sugar (kg)", price="60.00", product_type=Product.ProductType.MATERIAL
        )
        order = PurchaseOrder.objects.create(supplier=supplier, branch=branch, created_by=owner)
        PurchaseOrderItem.objects.create(
            purchase_order=order, material=flour, ordered_quantity=Decimal("10")
        )
        PurchaseOrderItem.objects.create(
            purchase_order=order, material=sugar, ordered_quantity=Decimal("5")
        )

        client.force_login(purchasing_staff)
        response = client.get(reverse("purchasing:supplier_management"))
        viewed_supplier = next(s for s in response.context["suppliers"] if s.pk == supplier.pk)
        assert viewed_supplier.materials_supplied_count == 2
        content = response.content.decode()
        assert "Flour (kg)" in content
        assert "Sugar (kg)" in content

    def test_supplier_with_no_purchase_orders_shows_zero_materials_supplied(
        self, client, purchasing_staff
    ):
        supplier = Supplier.objects.create(name="Acme Food Supply")
        client.force_login(purchasing_staff)
        response = client.get(reverse("purchasing:supplier_management"))
        viewed_supplier = next(s for s in response.context["suppliers"] if s.pk == supplier.pk)
        assert viewed_supplier.materials_supplied_count == 0
        assert viewed_supplier.materials_supplied_names == []


class TestSupplierEditView:
    """Client review (2026-11-09), Suppliers page layout improvement: Edit
    reuses the same required-name/duplicate-name validation as New
    Supplier, scoped to exclude the supplier being edited itself."""

    def test_purchasing_staff_can_edit_a_supplier(self, client, purchasing_staff):
        supplier = Supplier.objects.create(name="Acme Food Supply", contact_person="Juan")
        client.force_login(purchasing_staff)
        response = client.post(
            reverse("purchasing:supplier_edit", args=[supplier.pk]),
            {
                "name": "Acme Food Supply Co.",
                "contact_person": "Maria Santos",
                "phone_number": "0917-555-9999",
                "email": "maria@acmefood.ph",
                "address": "456 New St.",
                "notes": "Net 30 terms now.",
            },
        )
        assert response.status_code == 302
        supplier.refresh_from_db()
        assert supplier.name == "Acme Food Supply Co."
        assert supplier.contact_person == "Maria Santos"
        assert supplier.email == "maria@acmefood.ph"

    def test_keeping_the_same_name_while_editing_other_fields_succeeds(
        self, client, purchasing_staff
    ):
        supplier = Supplier.objects.create(name="Acme Food Supply")
        client.force_login(purchasing_staff)
        response = client.post(
            reverse("purchasing:supplier_edit", args=[supplier.pk]),
            {"name": "Acme Food Supply", "contact_person": "Updated Contact"},
        )
        assert response.status_code == 302
        supplier.refresh_from_db()
        assert supplier.contact_person == "Updated Contact"

    def test_blank_name_shows_an_error_and_does_not_save(self, client, purchasing_staff):
        supplier = Supplier.objects.create(name="Acme Food Supply", contact_person="Juan")
        client.force_login(purchasing_staff)
        response = client.post(
            reverse("purchasing:supplier_edit", args=[supplier.pk]),
            {"name": "  ", "contact_person": "Someone Else"},
        )
        assert response.status_code == 200
        assert "required" in response.context["error"].lower()
        supplier.refresh_from_db()
        assert supplier.contact_person == "Juan"

    def test_duplicate_name_of_a_different_supplier_is_rejected(self, client, purchasing_staff):
        Supplier.objects.create(name="Acme Food Supply")
        other = Supplier.objects.create(name="Old Vendor Co.")
        client.force_login(purchasing_staff)
        response = client.post(
            reverse("purchasing:supplier_edit", args=[other.pk]),
            {"name": "acme food supply"},
        )
        assert response.status_code == 200
        assert "already exists" in response.context["error"].lower()
        other.refresh_from_db()
        assert other.name == "Old Vendor Co."

    def test_validation_error_reopens_the_edit_modal_with_submitted_values(
        self, client, purchasing_staff
    ):
        supplier = Supplier.objects.create(name="Acme Food Supply")
        client.force_login(purchasing_staff)
        response = client.post(
            reverse("purchasing:supplier_edit", args=[supplier.pk]),
            {"name": "  ", "contact_person": "Typed But Not Saved"},
        )
        content = response.content.decode()
        assert f'class="modal fade show d-block" id="editSupplierModal{supplier.pk}"' in content
        assert "Typed But Not Saved" in content

    def test_branch_staff_is_forbidden(self, client, branch_staff):
        supplier = Supplier.objects.create(name="Acme Food Supply")
        client.force_login(branch_staff)
        response = client.post(
            reverse("purchasing:supplier_edit", args=[supplier.pk]), {"name": "New Name"}
        )
        assert response.status_code == 403
        supplier.refresh_from_db()
        assert supplier.name == "Acme Food Supply"

    def test_get_request_redirects_without_changing_anything(self, client, purchasing_staff):
        supplier = Supplier.objects.create(name="Acme Food Supply")
        client.force_login(purchasing_staff)
        response = client.get(reverse("purchasing:supplier_edit", args=[supplier.pk]))
        assert response.status_code == 302
        supplier.refresh_from_db()
        assert supplier.name == "Acme Food Supply"
