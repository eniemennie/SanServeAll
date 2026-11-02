"""
Tests for the create_commissary_staff_placeholder management command:
establishes a real, functional COMMISSARY_STAFF account with no
fictional name and no test account, ready for the client to attach
real staff details to later.
"""

import pytest
from django.core.management import call_command

from apps.accounts.models import Branch, Role, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def commissary():
    return Branch.objects.create(name="Commissary", code="COMMISSARY", is_commissary=True)


class TestCreateCommissaryStaffPlaceholder:
    def test_creates_one_commissary_staff_account(self, commissary):
        call_command("create_commissary_staff_placeholder")
        assert User.objects.filter(
            username="commissary_staff", role__name=Role.COMMISSARY_STAFF, branch=commissary
        ).exists()

    def test_created_account_has_no_name_set(self, commissary):
        call_command("create_commissary_staff_placeholder")
        user = User.objects.get(username="commissary_staff")
        assert user.first_name == ""
        assert user.last_name == ""
        assert user.get_full_name() == ""

    def test_created_account_has_a_usable_password(self, commissary):
        call_command("create_commissary_staff_placeholder")
        user = User.objects.get(username="commissary_staff")
        assert user.has_usable_password()
        assert user.check_password("CommissaryPending2026!")

    def test_no_commissary_branch_creates_nothing(self):
        call_command("create_commissary_staff_placeholder")
        assert User.objects.count() == 0

    def test_is_idempotent_when_run_twice(self, commissary):
        call_command("create_commissary_staff_placeholder")
        call_command("create_commissary_staff_placeholder")
        assert User.objects.filter(role__name=Role.COMMISSARY_STAFF).count() == 1

    def test_skips_when_an_active_commissary_staff_already_exists(self, commissary):
        role = Role.objects.create(name=Role.COMMISSARY_STAFF)
        User.objects.create_user(username="real_name_from_client", role=role, branch=commissary)
        call_command("create_commissary_staff_placeholder")
        assert User.objects.filter(role__name=Role.COMMISSARY_STAFF).count() == 1
        assert not User.objects.filter(username="commissary_staff").exists()

    def test_does_not_skip_when_only_an_inactive_account_exists(self, commissary):
        """Regression-style test mirroring the cashier placeholder bug
        (PR #90): an inactive leftover COMMISSARY_STAFF account must not
        be treated as "already staffed"."""
        role = Role.objects.create(name=Role.COMMISSARY_STAFF)
        User.objects.create_user(
            username="old_demo_commissary_staff",
            role=role,
            branch=commissary,
            is_active=False,
        )
        call_command("create_commissary_staff_placeholder")
        assert User.objects.filter(username="commissary_staff").exists()

    def test_dry_run_creates_nothing(self, commissary):
        call_command("create_commissary_staff_placeholder", "--dry-run")
        assert User.objects.count() == 0
