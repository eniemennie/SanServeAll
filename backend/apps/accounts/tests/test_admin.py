"""
Tests confirming the Django admin (Row 12) actually works end-to-end for
accounts models -- not just that admin.py registers them.
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import Branch

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def admin_client(client):
    """A superuser whose session is also OTP-verified -- the backend gap
    assessment's fix means /admin/ now requires both, same as the app's
    own custom Owner/Admin dashboard already did. force_login() only
    covers the first factor, so a confirmed device is created and its
    persistent_id is written to the session the same way
    django_otp.login() does on a real login, rather than going through
    the OTPAdminAuthenticationForm end-to-end in every test that just
    wants an already-authorized admin client."""
    admin = User.objects.create_superuser(
        username="admin_test", email="admin@example.com", password="testpass123"
    )
    device = TOTPDevice.objects.create(user=admin, name="default", confirmed=True)
    client.force_login(admin)
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()
    return client


@pytest.fixture
def unverified_admin_client(client):
    """A real superuser, logged in, but with NO OTP verification on the
    session -- the exact gap this fix closes."""
    admin = User.objects.create_superuser(
        username="admin_no_otp", email="admin_no_otp@example.com", password="testpass123"
    )
    client.force_login(admin)
    return client


class TestAccountsAdminPagesRender:
    def test_admin_index_loads(self, admin_client):
        response = admin_client.get(reverse("admin:index"))
        assert response.status_code == 200

    def test_branch_changelist_loads(self, admin_client):
        response = admin_client.get(reverse("admin:accounts_branch_changelist"))
        assert response.status_code == 200

    def test_role_changelist_loads(self, admin_client):
        response = admin_client.get(reverse("admin:accounts_role_changelist"))
        assert response.status_code == 200

    def test_user_changelist_loads(self, admin_client):
        response = admin_client.get(reverse("admin:accounts_user_changelist"))
        assert response.status_code == 200

    def test_cashierpin_changelist_loads(self, admin_client):
        response = admin_client.get(reverse("admin:accounts_cashierpin_changelist"))
        assert response.status_code == 200

    def test_seeded_branch_data_appears_in_admin_list(self, admin_client):
        Branch.objects.create(name="Alangilan", code="ALANGILAN", is_kahero_branch=True)
        response = admin_client.get(reverse("admin:accounts_branch_changelist"))
        assert b"Alangilan" in response.content


class TestAdminSiteRequiresTwoFactor:
    """Backend gap assessment: Django's built-in /admin/ site previously
    had no 2FA check at all -- any is_staff account could reach it with
    just a password, even though the app's own custom Owner/Admin
    dashboard already required OTP verification. This confirms /admin/
    now enforces the same thing."""

    def test_superuser_without_otp_verification_cannot_reach_admin_index(
        self, unverified_admin_client
    ):
        response = unverified_admin_client.get(reverse("admin:index"))
        assert response.status_code == 302
        assert "/admin/login/" in response.url

    def test_superuser_with_otp_verified_session_reaches_admin_index(self, admin_client):
        response = admin_client.get(reverse("admin:index"))
        assert response.status_code == 200
