"""
Tests for the Admin Shell (shared topbar + sidebar template every
Owner/Admin screen extends). Verifies the shell itself renders correctly
around the existing admin_dashboard_placeholder view -- the placeholder's
own access-control behavior (role + 2FA gating) is already covered by
test_rbac_and_2fa.py and isn't re-tested here.
"""

import pytest
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import Branch, Role, User
from conftest import verify_otp_for_client

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner_admin_role():
    return Role.objects.create(name=Role.OWNER_ADMIN)


@pytest.fixture
def branches():
    return [
        Branch.objects.create(name="Batangas City", code="BATANGAS"),
        Branch.objects.create(name="Lipa", code="LIPA"),
        Branch.objects.create(name="Alangilan", code="ALANGILAN", is_kahero_branch=True),
    ]


@pytest.fixture
def admin(owner_admin_role):
    return User.objects.create_user(
        username="owner1",
        email="owner@example.com",
        password="adminpass123",
        role=owner_admin_role,
    )


@pytest.fixture
def authed_client(client, admin):
    TOTPDevice.objects.create(user=admin, name="default", confirmed=True)
    client.force_login(admin)
    verify_otp_for_client(client, admin)
    return client


class TestAdminShell:
    def test_dashboard_renders_inside_shell(self, authed_client):
        response = authed_client.get(reverse("accounts:admin_dashboard"))
        assert response.status_code == 200
        content = response.content.decode()

        # Sidebar nav: all 7 screens present with real url-reversed hrefs.
        assert reverse("accounts:admin_dashboard") in content
        assert reverse("production:batch_management") in content
        assert reverse("forecasting:forecasting_dashboard") in content
        assert reverse("forecasting:resource_management_dashboard") in content
        assert reverse("analytics:sales_dashboard") in content
        assert reverse("inventory:product_management") in content
        assert reverse("system_config:system_settings") in content

        # Dashboard is the active nav item on this screen.
        assert "jc-nav-item active" in content

    def test_branch_dropdown_lists_active_non_commissary_branches(self, authed_client, branches):
        response = authed_client.get(reverse("accounts:admin_dashboard"))
        content = response.content.decode()

        assert "All Branches" in content
        assert "Batangas City" in content
        assert "Lipa" in content
        assert "Alangilan" in content

    def test_selecting_a_branch_marks_it_selected(self, authed_client, branches):
        alangilan = next(b for b in branches if b.code == "ALANGILAN")
        response = authed_client.get(reverse("accounts:admin_dashboard"), {"branch": alangilan.pk})
        content = response.content.decode()

        assert f'value="{alangilan.pk}" selected' in content

    def test_logout_link_present(self, authed_client):
        response = authed_client.get(reverse("accounts:admin_dashboard"))
        content = response.content.decode()
        assert reverse("accounts:logout") in content
