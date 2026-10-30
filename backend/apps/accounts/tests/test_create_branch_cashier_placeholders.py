"""
Tests for the create_branch_cashier_placeholders management command:
establishes a placeholder BRANCH_STAFF account per native-POS branch,
with no fictional name and no test account, ready for the client to
attach real staff details to later.
"""

import pytest
from django.core.management import call_command

from apps.accounts.models import Branch, CashierPIN, Role, User
from apps.accounts.services import get_selectable_cashiers

pytestmark = pytest.mark.django_db


@pytest.fixture
def batangas():
    return Branch.objects.create(name="Batangas City", code="BATANGAS")


@pytest.fixture
def lipa():
    return Branch.objects.create(name="Lipa City", code="LIPA")


@pytest.fixture
def alangilan():
    return Branch.objects.create(name="Alangilan", code="ALANGILAN", is_kahero_branch=True)


@pytest.fixture
def commissary():
    return Branch.objects.create(name="Commissary", code="COMMISSARY", is_commissary=True)


class TestCreateBranchCashierPlaceholders:
    def test_creates_one_account_per_eligible_branch(self, batangas, lipa, alangilan, commissary):
        call_command("create_branch_cashier_placeholders")

        assert User.objects.filter(branch=batangas, username="batangas_cashier").exists()
        assert User.objects.filter(branch=lipa, username="lipa_cashier").exists()

    def test_skips_kahero_and_commissary_branches(self, alangilan, commissary):
        call_command("create_branch_cashier_placeholders")
        assert User.objects.count() == 0

    def test_created_accounts_have_no_name_set(self, batangas):
        call_command("create_branch_cashier_placeholders")
        user = User.objects.get(branch=batangas)
        assert user.first_name == ""
        assert user.last_name == ""
        assert user.get_full_name() == ""

    def test_created_accounts_are_visible_on_select_cashier_screen(self, batangas):
        call_command("create_branch_cashier_placeholders")
        user = User.objects.get(branch=batangas)
        assert user in get_selectable_cashiers()

    def test_created_accounts_have_an_active_pin(self, batangas):
        call_command("create_branch_cashier_placeholders")
        user = User.objects.get(branch=batangas)
        pin_record = CashierPIN.objects.get(user=user)
        assert pin_record.is_active
        assert pin_record.check_pin("0000")

    def test_created_accounts_have_unusable_password(self, batangas):
        call_command("create_branch_cashier_placeholders")
        user = User.objects.get(branch=batangas)
        assert not user.has_usable_password()

    def test_is_idempotent_when_run_twice(self, batangas, lipa):
        call_command("create_branch_cashier_placeholders")
        call_command("create_branch_cashier_placeholders")
        assert User.objects.filter(role__name=Role.BRANCH_STAFF).count() == 2

    def test_skips_branch_that_already_has_a_branch_staff_user(self, batangas):
        role = Role.objects.create(name=Role.BRANCH_STAFF)
        User.objects.create_user(username="real_name_from_client", role=role, branch=batangas)
        call_command("create_branch_cashier_placeholders")
        assert User.objects.filter(branch=batangas).count() == 1
        assert not User.objects.filter(username="batangas_cashier").exists()

    def test_dry_run_creates_nothing(self, batangas, lipa):
        call_command("create_branch_cashier_placeholders", "--dry-run")
        assert User.objects.count() == 0

    def test_does_not_skip_a_branch_with_only_an_inactive_historical_import_account(self, batangas):
        """Regression test: the sales-history import command creates one
        INACTIVE "historical_import_<branch>" BRANCH_STAFF user per branch,
        purely as the required FK target for imported SalesTransactions --
        never meant to be a real, selectable cashier. A branch with only
        that leftover account must still get a real placeholder account,
        not be wrongly treated as "already staffed" (caught via a real
        staging dry-run before this ever created a wrong state there)."""
        role = Role.objects.create(name=Role.BRANCH_STAFF)
        User.objects.create_user(
            username="historical_import_batangas",
            role=role,
            branch=batangas,
            is_active=False,
        )

        call_command("create_branch_cashier_placeholders")

        assert User.objects.filter(branch=batangas, username="batangas_cashier").exists()
