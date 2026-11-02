"""
Tests for Week 3 batch 2 (Row 16, Row 17): RBAC middleware, role-based
access control, Admin Login, and 2FA (TOTP setup + verification).
"""

import pytest
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse
from django.views import View
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import Branch, Role, User
from apps.accounts.permissions import RoleRequiredMixin, role_required
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


class _DummyMixinView(RoleRequiredMixin, View):
    """Minimal class-based view for exercising RoleRequiredMixin directly
    (backend gap assessment: the mixin was added in anticipation of
    future class-based views per the Phase 4 pattern, but no current CBV
    uses it, and unlike its function-based sibling role_required -- see
    TestRoleRequiredDecorator above -- it had no test of its own at all).
    """

    allowed_roles = [Role.OWNER_ADMIN]

    def get(self, request, *args, **kwargs):
        return HttpResponse("ok")


class TestRoleRequiredMixin:
    def test_user_with_allowed_role_passes(self, admin):
        rf = RequestFactory()
        request = rf.get("/")
        request.user = admin
        # Same reasoning as TestRoleRequiredDecorator's equivalent test:
        # RequestFactory bypasses OTPMiddleware, so the verified-session
        # state is simulated directly -- this test is about the mixin's
        # ROLE check, not re-proving OTP enforcement.
        request.user.is_verified = lambda: True

        response = _DummyMixinView.as_view()(request)
        assert response.status_code == 200

    def test_user_without_allowed_role_is_denied(self, cashier):
        rf = RequestFactory()
        request = rf.get("/")
        request.user = cashier

        with pytest.raises(PermissionDenied):
            _DummyMixinView.as_view()(request)

    def test_owner_admin_without_otp_verification_is_redirected_to_admin_login(self, admin):
        """Same Row 12.4 guarantee role_required enforces: holding the
        allowed role alone isn't enough for Owner/Admin -- the current
        session must have completed 2FA too, or the mixin redirects to
        complete it rather than letting the view run."""
        rf = RequestFactory()
        request = rf.get("/")
        request.user = admin
        request.user.is_verified = lambda: False

        response = _DummyMixinView.as_view()(request)
        assert response.status_code == 302
        assert response.url == reverse("accounts:admin_login")

    def test_non_owner_admin_role_skips_otp_check_entirely(self, cashier):
        """A role other than Owner/Admin never goes through 2FA at all
        (see _user_requires_otp's own docstring) -- granting it access
        here must not get blocked on an OTP check that doesn't apply."""

        class _BranchStaffView(RoleRequiredMixin, View):
            allowed_roles = [Role.BRANCH_STAFF]

            def get(self, request, *args, **kwargs):
                return HttpResponse("ok")

        rf = RequestFactory()
        request = rf.get("/")
        request.user = cashier

        response = _BranchStaffView.as_view()(request)
        assert response.status_code == 200


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

    def test_locks_out_after_threshold_wrong_passwords_even_the_correct_one(self, client, admin):
        """Previously unlimited -- any number of wrong passwords could be
        tried against the admin login with no lockout at all, unlike the
        Cashier PIN flow which already had one."""
        from apps.accounts.services import LOGIN_LOCKOUT_THRESHOLD

        for _ in range(LOGIN_LOCKOUT_THRESHOLD):
            client.post(
                reverse("accounts:admin_login"),
                {"email": "owner@example.com", "password": "wrongpassword"},
            )

        response = client.post(
            reverse("accounts:admin_login"),
            {"email": "owner@example.com", "password": "adminpass123"},
        )
        assert response.status_code == 200
        assert not response.wsgi_request.user.is_authenticated
        assert b"Too many incorrect attempts" in response.content

    def test_a_correct_password_on_the_wrong_role_does_not_count_toward_lockout(
        self, client, cashier
    ):
        """A Branch Staff account's own correct password, tried on the
        admin-only door, is refused -- but shouldn't move that account
        toward locking its REAL login path, since the attacker didn't
        fail to guess that account's password."""
        cashier.email = "cashier@example.com"
        cashier.save()

        for _ in range(10):
            client.post(
                reverse("accounts:admin_login"),
                {"email": "cashier@example.com", "password": "testpass123"},
            )

        cashier.refresh_from_db()
        assert cashier.failed_attempts == 0
        assert cashier.locked_until is None


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
        assert response.url == reverse("accounts:admin_dashboard")

    def test_incorrect_token_is_rejected(self, client, admin):
        TOTPDevice.objects.create(user=admin, name="default", confirmed=True)
        client.force_login(admin)

        response = client.post(reverse("accounts:admin_2fa_verify"), {"token": "000000"})
        assert response.status_code == 200
        assert b"Incorrect code" in response.content

    def test_verify_page_uses_the_shared_auth_page_styling(self, client, admin):
        """Verify screen was redesigned (Row 12) to match Admin Login's
        jc-auth-* styling rather than the old generic Bootstrap card, so
        the two screens in one login flow don't visually disagree."""
        TOTPDevice.objects.create(user=admin, name="default", confirmed=True)
        client.force_login(admin)

        response = client.get(reverse("accounts:admin_2fa_verify"))
        assert response.status_code == 200
        assert b"jc-auth-card" in response.content
        assert b"Enter Authenticator Code" in response.content


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
        # Owner/Admin goes straight to the dashboard after 2FA -- no
        # Branch Selection step, since that role isn't scoped to one
        # branch and the dashboard's own branch filter is a separate,
        # on-page control, not a login gate.
        assert verify_response.url == reverse("accounts:admin_dashboard")

        dashboard_response = client.get(reverse("accounts:admin_dashboard"))
        assert dashboard_response.status_code == 200
        # Was "Admin Dashboard" (the old placeholder's own heading) --
        # updated to the real Dashboard Home content's defining heading
        # now that the placeholder has been replaced (Dashboard Home
        # batch). Still verifies the same thing: login -> 2FA -> the
        # real dashboard actually renders, not just a 200 with any body.
        assert b"Branch Performance Overview" in dashboard_response.content
