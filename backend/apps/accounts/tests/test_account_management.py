"""
Tests for the in-app Account Management screen (backend gap assessment:
"build an in-app account-management view for Owner/Admin -- create/edit
user, assign branch/role, reset PIN"). Covers both the service-layer
rules (apps.accounts.services) and the two views built on top of them.
"""

import pytest
from django.urls import reverse
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts import services
from apps.accounts.models import Branch, CashierPIN, Role, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def branch():
    return Branch.objects.create(name="Batangas City", code="BATANGAS")


@pytest.fixture
def roles():
    return {
        "owner_admin": Role.objects.create(name=Role.OWNER_ADMIN),
        "branch_staff": Role.objects.create(name=Role.BRANCH_STAFF),
        "commissary_staff": Role.objects.create(name=Role.COMMISSARY_STAFF),
    }


@pytest.fixture
def owner(roles):
    return User.objects.create_user(
        username="owner1", password="testpass123", role=roles["owner_admin"]
    )


@pytest.fixture
def owner_client(client, owner):
    """An Owner/Admin whose session is both logged in AND OTP-verified --
    account_management/account_edit require both, same as admin_dashboard."""
    device = TOTPDevice.objects.create(user=owner, name="default", confirmed=True)
    client.force_login(owner)
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()
    return client


@pytest.fixture
def cashier(roles, branch):
    user = User.objects.create_user(
        username="cashier1", password="x", role=roles["branch_staff"], branch=branch
    )
    pin = CashierPIN.objects.create(user=user)
    pin.set_pin("1234")
    pin.save()
    return user


class TestCreateStaffAccountService:
    def test_branch_staff_gets_unusable_password_and_a_generated_pin(self, roles, branch):
        user, pin = services.create_staff_account(
            username="newcashier", role_name=Role.BRANCH_STAFF, branch_id=branch.pk
        )
        assert not user.has_usable_password()
        assert pin is not None and len(pin) == 4 and pin.isdigit()
        assert user.cashier_pin.check_pin(pin)

    def test_owner_admin_requires_a_password_and_no_branch(self, roles):
        with pytest.raises(services.AccountManagementError, match="password is required"):
            services.create_staff_account(username="newowner", role_name=Role.OWNER_ADMIN)

        user, pin = services.create_staff_account(
            username="newowner", role_name=Role.OWNER_ADMIN, password="s3cret123"
        )
        assert pin is None
        assert user.branch is None
        assert user.check_password("s3cret123")

    def test_commissary_staff_requires_a_branch(self, roles):
        with pytest.raises(services.AccountManagementError, match="branch"):
            services.create_staff_account(
                username="newcommissary", role_name=Role.COMMISSARY_STAFF, password="s3cret123"
            )

    def test_duplicate_username_is_rejected(self, roles, branch):
        services.create_staff_account(
            username="dupe", role_name=Role.BRANCH_STAFF, branch_id=branch.pk
        )
        with pytest.raises(services.AccountManagementError, match="already taken"):
            services.create_staff_account(
                username="dupe", role_name=Role.BRANCH_STAFF, branch_id=branch.pk
            )

    def test_blank_username_is_rejected(self, roles):
        with pytest.raises(services.AccountManagementError, match="Username is required"):
            services.create_staff_account(username="   ", role_name=Role.OWNER_ADMIN, password="x")


class TestUpdateStaffAccountService:
    def test_updates_branch_and_active_status(self, cashier, branch):
        other_branch = Branch.objects.create(name="Lipa City", code="LIPA")
        services.update_staff_account(cashier, branch_id=other_branch.pk, is_active=False)
        cashier.refresh_from_db()
        assert cashier.branch == other_branch
        assert cashier.is_active is False

    def test_does_not_change_role(self, cashier):
        original_role = cashier.role
        services.update_staff_account(cashier, branch_id=cashier.branch_id, is_active=True)
        cashier.refresh_from_db()
        assert cashier.role == original_role


class TestResetCashierPinService:
    def test_generates_a_new_pin_and_clears_lockout(self, cashier):
        pin_record = cashier.cashier_pin
        pin_record.failed_attempts = 4
        pin_record.save()

        new_pin = services.reset_cashier_pin(cashier)

        pin_record.refresh_from_db()
        assert pin_record.check_pin(new_pin)
        assert not pin_record.check_pin("1234")  # old PIN no longer works
        assert pin_record.failed_attempts == 0
        assert pin_record.locked_until is None

    def test_rejects_a_non_branch_staff_account(self, owner):
        with pytest.raises(services.AccountManagementError, match="Only Branch Staff"):
            services.reset_cashier_pin(owner)


class TestAccountManagementView:
    def test_owner_can_view_the_page(self, owner_client):
        response = owner_client.get(reverse("accounts:account_management"))
        assert response.status_code == 200

    def test_non_owner_is_denied(self, client, cashier):
        client.force_login(cashier)
        response = client.get(reverse("accounts:account_management"))
        assert response.status_code == 403

    def test_owner_without_otp_verification_is_redirected(self, client, owner):
        client.force_login(owner)  # logged in, but no OTP verification this session
        response = client.get(reverse("accounts:account_management"))
        assert response.status_code == 302
        assert reverse("accounts:admin_login") in response.url

    def test_creating_a_branch_staff_account_shows_the_generated_pin_once(
        self, owner_client, branch
    ):
        response = owner_client.post(
            reverse("accounts:account_management"),
            {"username": "freshcashier", "role": Role.BRANCH_STAFF, "branch": branch.pk},
        )
        assert response.status_code == 200
        assert User.objects.filter(username="freshcashier").exists()
        assert b"Their PIN is" in response.content

    def test_invalid_submission_shows_error_and_does_not_create(self, owner_client):
        response = owner_client.post(
            reverse("accounts:account_management"),
            {"username": "nopass", "role": Role.OWNER_ADMIN},  # no password
        )
        assert response.status_code == 200
        assert b"password is required" in response.content
        assert not User.objects.filter(username="nopass").exists()


class TestAccountEditView:
    def test_owner_can_update_branch_and_active_status(self, owner_client, cashier, branch):
        other_branch = Branch.objects.create(name="Lipa City", code="LIPA")
        response = owner_client.post(
            reverse("accounts:account_edit", args=[cashier.pk]),
            {
                "action": "update",
                "branch": other_branch.pk,
                "first_name": "Jamie",
                "last_name": "Cruz",
            },
            # is_active checkbox omitted -> deactivate
        )
        assert response.status_code == 302

        cashier.refresh_from_db()
        assert cashier.branch == other_branch
        assert cashier.first_name == "Jamie"
        assert cashier.is_active is False

    def test_reset_pin_action_shows_new_pin_without_redirecting(self, owner_client, cashier):
        response = owner_client.post(
            reverse("accounts:account_edit", args=[cashier.pk]), {"action": "reset_pin"}
        )
        assert response.status_code == 200
        assert b"New PIN for cashier1" in response.content

        cashier.cashier_pin.refresh_from_db()
        assert not cashier.cashier_pin.check_pin("1234")

    def test_non_owner_cannot_edit_accounts(self, client, cashier, roles):
        other_cashier = User.objects.create_user(
            username="cashier2", password="x", role=roles["branch_staff"]
        )
        client.force_login(cashier)
        response = client.post(
            reverse("accounts:account_edit", args=[other_cashier.pk]), {"action": "reset_pin"}
        )
        assert response.status_code == 403
