"""
Tests for the create_purchasing_staff_placeholder management command:
establishes a real, functional PURCHASING account with no fictional
name and no test account, ready for the client to attach real staff
details to later. Mirrors
test_create_commissary_staff_placeholder.py, minus the branch-related
cases -- PURCHASING is explicitly not branch-scoped.
"""

import pytest
from django.core.management import call_command

from apps.accounts.models import Role, User

pytestmark = pytest.mark.django_db


class TestCreatePurchasingStaffPlaceholder:
    def test_creates_one_purchasing_account(self):
        call_command("create_purchasing_staff_placeholder")
        assert User.objects.filter(
            username="purchasing_staff", role__name=Role.PURCHASING, branch__isnull=True
        ).exists()

    def test_created_account_has_no_name_set(self):
        call_command("create_purchasing_staff_placeholder")
        user = User.objects.get(username="purchasing_staff")
        assert user.first_name == ""
        assert user.last_name == ""
        assert user.get_full_name() == ""

    def test_created_account_has_a_usable_password(self):
        call_command("create_purchasing_staff_placeholder")
        user = User.objects.get(username="purchasing_staff")
        assert user.has_usable_password()
        assert user.check_password("PurchasingPending2026!")

    def test_is_idempotent_when_run_twice(self):
        call_command("create_purchasing_staff_placeholder")
        call_command("create_purchasing_staff_placeholder")
        assert User.objects.filter(role__name=Role.PURCHASING).count() == 1

    def test_skips_when_an_active_purchasing_account_already_exists(self):
        role = Role.objects.create(name=Role.PURCHASING)
        User.objects.create_user(username="real_name_from_client", role=role)
        call_command("create_purchasing_staff_placeholder")
        assert User.objects.filter(role__name=Role.PURCHASING).count() == 1
        assert not User.objects.filter(username="purchasing_staff").exists()

    def test_does_not_skip_when_only_an_inactive_account_exists(self):
        """Regression-style test mirroring the cashier/commissary
        placeholder bug (PR #90): an inactive leftover PURCHASING
        account must not be treated as "already staffed"."""
        role = Role.objects.create(name=Role.PURCHASING)
        User.objects.create_user(username="old_demo_purchasing_staff", role=role, is_active=False)
        call_command("create_purchasing_staff_placeholder")
        assert User.objects.filter(username="purchasing_staff").exists()

    def test_dry_run_creates_nothing(self):
        call_command("create_purchasing_staff_placeholder", "--dry-run")
        assert User.objects.count() == 0
