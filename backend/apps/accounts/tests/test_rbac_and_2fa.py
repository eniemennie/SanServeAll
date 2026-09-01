"""
Tests for Week 3 batch 2 (Row 16, Row 17): RBAC middleware, role-based
access control, Admin Login, and 2FA (TOTP setup + verification).
"""

import pytest
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import Branch, Role, User
from apps.accounts.permissions import role_required
from apps.core.middleware import BranchScopingMiddleware

pytestmark = pytest.mark.django_db


def _valid_token(device):
    return f"{totp(device.bin_key):06d}"


@pytest.fixture
def branches():
    return {
        "batangas": Branch.objects.create(name="Batangas City", code="BATANGAS"),
        "alangilan": Branch.objects.create(
            name="Alangilan", code="ALANGILAN", is_kahero_branch=True
        ),
    }


@pytest.fixture
def roles():
    return {
        "owner_admin": Role.objects.create(name=Role.OWNER_ADMIN),
        "branch_staff": Role.objects.create(name=Role.BRANCH_STAFF),
    }


@pytest.fixture
def cashier(roles, branches):
    return User.objects.create_user(
        username="cashier1",
        password="testpass123",
        role=roles["branch_staff"],
        branch=branches["batangas"],
    )


@pytest.fixture
def admin(roles):
    return User.objects.create_user(
        username="owner1",
        email="owner@example.com",
        password="adminpass123",
        role=roles["owner_admin"],
    )


class TestBranchScopingMiddleware:
    """Unit-level tests hitting the middleware directly, plus integration
    tests through the full request/response cycle below."""

    def _run_middleware(self, user, session_data):
        rf = RequestFactory()
        request = rf.get("/")
        request.user = user
        request.session = session_data

        middleware = BranchScopingMiddleware(get_response=lambda r: HttpResponse())
        middleware(request)
        return request

    def test_branch_staff_with_matching_session_branch_is_accepted(self, cashier, branches):
        request = self._run_middleware(cashier, {"selected_branch_id": branches["batangas"].pk})
        assert request.selected_branch == branches["batangas"]
        assert "selected_branch_id" in request.session

    def test_branch_staff_with_mismatched_session_branch_is_rejected_and_cleared(
        self, cashier, branches
    ):
        """The core security guarantee: a session claiming a different
        branch than the user's actual assignment gets silently corrected,
        not trusted."""
        request = self._run_middleware(cashier, {"selected_branch_id": branches["alangilan"].pk})
        assert request.selected_branch is None
        assert "selected_branch_id" not in request.session

    def test_owner_admin_can_hold_any_active_branch(self, admin, branches):
        request = self._run_middleware(admin, {"selected_branch_id": branches["alangilan"].pk})
        assert request.selected_branch == branches["alangilan"]

    def test_no_session_branch_leaves_selected_branch_none(self, cashier):
        request = self._run_middleware(cashier, {})
        assert request.selected_branch is None


class TestRoleRequiredDecorator:
    def test_user_with_allowed_role_passes(self, admin, client):
        @role_required(Role.OWNER_ADMIN)
        def dummy_view(request):
            return HttpResponse("ok")

        rf = RequestFactory()
        request = rf.get("/")
        request.user = admin
        # RequestFactory bypasses OTPMiddleware entirely -- manually
        # simulate what it would set on a verified session, since this
        # test's actual purpose is confirming role_required's ROLE check
        # specifically, not re-testing OTP enforcement (covered
        # end-to-end via the real Client in TestAdminDashboardEnforcement
        # below, and across every OWNER_ADMIN-gated view in other apps).
        request.user.is_verified = lambda: True
        response = dummy_view(request)
        assert response.status_code == 200

    def test_user_without_allowed_role_is_denied(self, cashier):
        @role_required(Role.OWNER_ADMIN)
        def dummy_view(request):
            return HttpResponse("ok")

        rf = RequestFactory()
        request = rf.get("/")
        request.user = cashier
        with pytest.raises(PermissionDenied):
            dummy_view(request)


class TestAdminLogin:
    def test_valid_admin_credentials_log_in(self, client, admin):
        response = client.post(
            reverse("accounts:admin_login"),
            {"email": "owner@example.com", "password": "adminpass123"},
        )
        assert response.status_code == 302
        assert response.wsgi_request.user.is_authenticated

    def test_admin_with_no_2fa_device_goes_to_setup(self, client, admin):
        response = client.post(
            reverse("accounts:admin_login"),
            {"email": "owner@example.com", "password": "adminpass123"},
        )
        assert response.url == reverse("accounts:admin_2fa_setup")

    def test_admin_with_confirmed_2fa_device_goes_to_verify(self, client, admin):
        TOTPDevice.objects.create(user=admin, name="default", confirmed=True)
        response = client.post(
            reverse("accounts:admin_login"),
            {"email": "owner@example.com", "password": "adminpass123"},
        )
        assert response.url == reverse("accounts:admin_2fa_verify")

    def test_wrong_password_is_rejected(self, client, admin):
        response = client.post(
            reverse("accounts:admin_login"),
            {"email": "owner@example.com", "password": "wrongpassword"},
        )
        assert response.status_code == 200
        assert not response.wsgi_request.user.is_authenticated

    def test_non_admin_role_cannot_use_admin_login_even_with_correct_credentials(
        self, client, cashier
    ):
        """A BRANCH_STAFF account with perfectly valid credentials must
        still be refused here -- this is an admin-only entry point."""
        cashier.email = "cashier@example.com"
        cashier.save()
        response = client.post(
            reverse("accounts:admin_login"),
            {"email": "cashier@example.com", "password": "testpass123"},
        )
        assert response.status_code == 200
        assert not response.wsgi_request.user.is_authenticated


class TestTwoFactorSetup:
    def test_setup_page_shows_a_secret(self, client, admin):
        client.force_login(admin)
        response = client.get(reverse("accounts:admin_2fa_setup"))
        assert response.status_code == 200
        assert TOTPDevice.objects.filter(user=admin, confirmed=False).exists()

    def test_correct_token_confirms_device_and_proceeds(self, client, admin):
        client.force_login(admin)
        client.get(reverse("accounts:admin_2fa_setup"))  # creates unconfirmed device
        device = TOTPDevice.objects.get(user=admin, confirmed=False)

        response = client.post(reverse("accounts:admin_2fa_setup"), {"token": _valid_token(device)})
        # Row 12.4: successful confirmation now shows the one-time backup
        # codes page (200) before the admin proceeds, rather than
        # redirecting straight to branch selection.
        assert response.status_code == 200
        assert b"Save Your Backup Codes" in response.content

        device.refresh_from_db()
        assert device.confirmed is True

    def test_incorrect_token_does_not_confirm_device(self, client, admin):
        client.force_login(admin)
        client.get(reverse("accounts:admin_2fa_setup"))
        device = TOTPDevice.objects.get(user=admin, confirmed=False)

        response = client.post(reverse("accounts:admin_2fa_setup"), {"token": "000000"})
        assert response.status_code == 200

        device.refresh_from_db()
        assert device.confirmed is False


class TestTwoFactorVerify:
    def test_correct_token_verifies_and_proceeds(self, client, admin):
        device = TOTPDevice.objects.create(user=admin, name="default", confirmed=True)
        client.force_login(admin)

        response = client.post(
            reverse("accounts:admin_2fa_verify"), {"token": _valid_token(device)}
        )
        assert response.status_code == 302
        assert response.url == reverse("accounts:select_branch")

    def test_incorrect_token_is_rejected(self, client, admin):
        TOTPDevice.objects.create(user=admin, name="default", confirmed=True)
        client.force_login(admin)

        response = client.post(reverse("accounts:admin_2fa_verify"), {"token": "000000"})
        assert response.status_code == 200
        assert b"Incorrect code" in response.content


class TestAdminDashboardEnforcement:
    """End-to-end proof of the full chain this batch builds: role AND
    2FA are both required to reach the admin dashboard."""

    def test_non_admin_role_is_denied_even_when_logged_in(self, client, cashier):
        client.force_login(cashier)
        response = client.get(reverse("accounts:admin_dashboard"))
        assert response.status_code == 403

    def test_admin_without_2fa_verification_is_redirected_to_admin_login(self, client, admin):
        TOTPDevice.objects.create(user=admin, name="default", confirmed=True)
        client.force_login(admin)  # logged in, but no OTP verification this session
        response = client.get(reverse("accounts:admin_dashboard"))
        assert response.status_code == 302
        assert reverse("accounts:admin_login") in response.url

    def test_full_flow_admin_login_then_2fa_reaches_dashboard(self, client, admin):
        device = TOTPDevice.objects.create(user=admin, name="default", confirmed=True)
        client.force_login(admin)

        verify_response = client.post(
            reverse("accounts:admin_2fa_verify"), {"token": _valid_token(device)}
        )
        assert verify_response.status_code == 302

        dashboard_response = client.get(reverse("accounts:admin_dashboard"))
        assert dashboard_response.status_code == 200
        assert b"Admin Dashboard" in dashboard_response.content
