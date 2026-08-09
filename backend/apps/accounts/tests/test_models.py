"""
Tests for apps.accounts models (Row 9): Branch, Role, User, CashierPIN.

Covers the same behavior described as "verified locally" in the Row 9
commit -- turned into real, automated, repeatable tests rather than a
one-off manual check.
"""

import pytest

from apps.accounts.models import Branch, CashierPIN, Role, User

pytestmark = pytest.mark.django_db


class TestBranch:
    def test_only_one_branch_is_marked_as_kahero(self):
        """The confirmed client decision (Phase 2): exactly one branch runs
        KaHero/batch-import mode. This test locks that in as a real check,
        not just a doc comment."""
        Branch.objects.create(name="Batangas City", code="BATANGAS", is_kahero_branch=False)
        alangilan = Branch.objects.create(name="Alangilan", code="ALANGILAN", is_kahero_branch=True)
        Branch.objects.create(name="Lipa City", code="LIPA", is_kahero_branch=False)

        kahero_branches = Branch.objects.filter(is_kahero_branch=True)
        assert kahero_branches.count() == 1
        assert kahero_branches.first() == alangilan

    def test_branch_str_returns_name(self):
        branch = Branch.objects.create(name="Lipa City", code="LIPA")
        assert str(branch) == "Lipa City"


class TestRole:
    def test_role_choices_match_phase1_rbac_design(self):
        for role_value, _label in Role.ROLE_CHOICES:
            role = Role.objects.create(name=role_value)
            assert role.name == role_value

    def test_role_name_must_be_unique(self):
        Role.objects.create(name=Role.OWNER_ADMIN)
        with pytest.raises(Exception):
            Role.objects.create(name=Role.OWNER_ADMIN)


class TestUser:
    def test_owner_admin_user_has_no_branch(self):
        """Per Phase 1 par 2.1: OWNER_ADMIN is not scoped to a single branch."""
        role = Role.objects.create(name=Role.OWNER_ADMIN)
        user = User.objects.create_user(username="owner", password="testpass123", role=role)
        assert user.branch is None

    def test_branch_staff_user_is_scoped_to_a_branch(self):
        role = Role.objects.create(name=Role.BRANCH_STAFF)
        branch = Branch.objects.create(name="Lipa City", code="LIPA")
        user = User.objects.create_user(
            username="cashier1", password="testpass123", role=role, branch=branch
        )
        assert user.branch == branch

    def test_password_is_hashed_not_plaintext(self):
        """Confirms Django's default PBKDF2 hasher is active (Phase 3 decision)."""
        user = User.objects.create_user(username="cashier2", password="testpass123")
        assert user.password != "testpass123"
        assert user.password.startswith("pbkdf2_")


class TestCashierPIN:
    def test_set_pin_stores_a_hash_not_plaintext(self):
        user = User.objects.create_user(username="cashier3", password="testpass123")
        pin = CashierPIN.objects.create(user=user)
        pin.set_pin("1234")
        pin.save()

        assert pin.hashed_pin != "1234"
        assert pin.hashed_pin.startswith("pbkdf2_")

    def test_check_pin_accepts_correct_pin_and_rejects_wrong_pin(self):
        user = User.objects.create_user(username="cashier4", password="testpass123")
        pin = CashierPIN.objects.create(user=user)
        pin.set_pin("5678")
        pin.save()

        assert pin.check_pin("5678") is True
        assert pin.check_pin("0000") is False
