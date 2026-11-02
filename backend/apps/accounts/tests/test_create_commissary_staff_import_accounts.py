"""
Tests for the create_commissary_staff_import_accounts management command:
ensures the 3 real commissary-staff usernames the client's own
Production_Records.xlsx references (commissary_staff, commissary_staff2,
commissary_staff3) all exist, without disturbing an already-real account
and without the "stop at the first active account" behavior of
create_commissary_staff_placeholder (which would be wrong here).
"""

import pytest
from django.core.management import call_command

from apps.accounts.models import Branch, Role, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def commissary():
    return Branch.objects.create(name="Commissary", code="COMMISSARY", is_commissary=True)


class TestCreateCommissaryStaffImportAccounts:
    def test_creates_all_three_required_usernames(self, commissary):
        call_command("create_commissary_staff_import_accounts")
        for username in ["commissary_staff", "commissary_staff2", "commissary_staff3"]:
            assert User.objects.filter(
                username=username, role__name=Role.COMMISSARY_STAFF, branch=commissary
            ).exists()

    def test_created_accounts_have_no_name_set(self, commissary):
        call_command("create_commissary_staff_import_accounts")
        user = User.objects.get(username="commissary_staff2")
        assert user.first_name == ""
        assert user.last_name == ""

    def test_created_accounts_have_a_usable_password(self, commissary):
        call_command("create_commissary_staff_import_accounts")
        user = User.objects.get(username="commissary_staff3")
        assert user.has_usable_password()
        assert user.check_password("CommissaryPending2026!")

    def test_leaves_an_existing_real_commissary_staff_untouched(self, commissary):
        role, _ = Role.objects.get_or_create(name=Role.COMMISSARY_STAFF)
        existing = User.objects.create_user(
            username="commissary_staff",
            first_name="Real",
            last_name="Person",
            role=role,
            branch=commissary,
        )
        call_command("create_commissary_staff_import_accounts")
        existing.refresh_from_db()
        assert existing.first_name == "Real"
        assert existing.last_name == "Person"
        assert User.objects.filter(role=role).count() == 3

    def test_is_idempotent_when_run_twice(self, commissary):
        call_command("create_commissary_staff_import_accounts")
        call_command("create_commissary_staff_import_accounts")
        assert User.objects.filter(role__name=Role.COMMISSARY_STAFF).count() == 3

    def test_no_commissary_branch_creates_nothing(self):
        call_command("create_commissary_staff_import_accounts")
        assert User.objects.count() == 0

    def test_dry_run_creates_nothing(self, commissary):
        call_command("create_commissary_staff_import_accounts", "--dry-run")
        assert User.objects.count() == 0
