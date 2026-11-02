"""
Tests for backend gap assessment item 7 (Phase 1, critical): the
audit_placeholder_credentials deploy-gate command and the service
function it's built on, find_accounts_with_placeholder_credentials().
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from apps.accounts.models import Branch, CashierPIN, Role
from apps.accounts.services import (
    PLACEHOLDER_CASHIER_PIN,
    PLACEHOLDER_COMMISSARY_PASSWORD,
    find_accounts_with_placeholder_credentials,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def branch():
    return Branch.objects.create(name="Batangas City", code="BATANGAS")


@pytest.fixture
def commissary_branch():
    return Branch.objects.create(name="Commissary", code="COMMISSARY", is_commissary=True)


@pytest.fixture
def branch_staff_role():
    return Role.objects.create(name=Role.BRANCH_STAFF)


@pytest.fixture
def commissary_staff_role():
    return Role.objects.create(name=Role.COMMISSARY_STAFF)


class TestFindAccountsWithPlaceholderCredentials:
    def test_flags_a_cashier_still_on_the_placeholder_pin(self, branch, branch_staff_role):
        User = get_user_model()
        cashier = User.objects.create(
            username="batangas_cashier", role=branch_staff_role, branch=branch, is_active=True
        )
        pin_record = CashierPIN(user=cashier, is_active=True)
        pin_record.set_pin(PLACEHOLDER_CASHIER_PIN)
        pin_record.save()

        findings = find_accounts_with_placeholder_credentials()
        assert len(findings) == 1
        assert findings[0]["credential_type"] == "Cashier PIN"
        assert findings[0]["user"] == cashier

    def test_a_rotated_pin_is_not_flagged(self, branch, branch_staff_role):
        User = get_user_model()
        cashier = User.objects.create(
            username="batangas_cashier", role=branch_staff_role, branch=branch, is_active=True
        )
        pin_record = CashierPIN(user=cashier, is_active=True)
        pin_record.set_pin("7183")  # a real, rotated PIN
        pin_record.save()

        assert find_accounts_with_placeholder_credentials() == []

    def test_an_inactive_cashier_pin_is_not_flagged(self, branch, branch_staff_role):
        User = get_user_model()
        cashier = User.objects.create(
            username="batangas_cashier", role=branch_staff_role, branch=branch, is_active=True
        )
        pin_record = CashierPIN(user=cashier, is_active=False)
        pin_record.set_pin(PLACEHOLDER_CASHIER_PIN)
        pin_record.save()

        assert find_accounts_with_placeholder_credentials() == []

    def test_flags_a_commissary_account_still_on_the_seed_password(
        self, commissary_branch, commissary_staff_role
    ):
        User = get_user_model()
        staff = User.objects.create(
            username="commissary_staff",
            role=commissary_staff_role,
            branch=commissary_branch,
            is_active=True,
        )
        staff.set_password(PLACEHOLDER_COMMISSARY_PASSWORD)
        staff.save()

        findings = find_accounts_with_placeholder_credentials()
        assert len(findings) == 1
        assert findings[0]["credential_type"] == "Commissary password"
        assert findings[0]["user"] == staff

    def test_a_rotated_commissary_password_is_not_flagged(
        self, commissary_branch, commissary_staff_role
    ):
        User = get_user_model()
        staff = User.objects.create(
            username="commissary_staff",
            role=commissary_staff_role,
            branch=commissary_branch,
            is_active=True,
        )
        staff.set_password("a-real-rotated-password")
        staff.save()

        assert find_accounts_with_placeholder_credentials() == []

    def test_no_accounts_at_all_returns_empty(self):
        assert find_accounts_with_placeholder_credentials() == []


class TestAuditPlaceholderCredentialsCommand:
    def test_exits_nonzero_when_placeholders_remain(self, branch, branch_staff_role):
        User = get_user_model()
        cashier = User.objects.create(
            username="batangas_cashier", role=branch_staff_role, branch=branch, is_active=True
        )
        pin_record = CashierPIN(user=cashier, is_active=True)
        pin_record.set_pin(PLACEHOLDER_CASHIER_PIN)
        pin_record.save()

        with pytest.raises(SystemExit) as exc_info:
            call_command("audit_placeholder_credentials")
        assert exc_info.value.code == 1

    def test_exits_cleanly_when_nothing_needs_rotating(self, branch, branch_staff_role):
        User = get_user_model()
        cashier = User.objects.create(
            username="batangas_cashier", role=branch_staff_role, branch=branch, is_active=True
        )
        pin_record = CashierPIN(user=cashier, is_active=True)
        pin_record.set_pin("7183")
        pin_record.save()

        call_command("audit_placeholder_credentials")  # does not raise
