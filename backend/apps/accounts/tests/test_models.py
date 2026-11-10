"""
Tests for apps.accounts models (Row 9): Branch, Role, User, CashierPIN.

Covers the same behavior described as "verified locally" in the Row 9
commit -- turned into real, automated, repeatable tests rather than a
one-off manual check.
"""

import pytest
from django.db import IntegrityError, transaction
from django.test import override_settings

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

    def test_a_second_kahero_branch_is_rejected_at_the_database_level(self):
        """The test above only checked that nobody had *tried* to create a
        second KaHero branch -- nothing previously stopped it from
        happening. This confirms the invariant is now actually enforced
        by the schema (a partial unique constraint), not just by
        convention: creating a second is_kahero_branch=True row must
        raise, not silently succeed."""
        Branch.objects.create(name="Alangilan", code="ALANGILAN", is_kahero_branch=True)

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Branch.objects.create(name="Batangas City", code="BATANGAS", is_kahero_branch=True)

        # Confirms the failed insert didn't partially land -- still
        # exactly one KaHero branch, and it's still the original one.
        assert Branch.objects.filter(is_kahero_branch=True).count() == 1
        assert Branch.objects.filter(code="BATANGAS").exists() is False

    def test_multiple_non_kahero_branches_are_unaffected_by_the_constraint(self):
        """Confirms the partial index only restricts True rows -- as many
        branches as needed can still have is_kahero_branch=False."""
        Branch.objects.create(name="Batangas City", code="BATANGAS", is_kahero_branch=False)
        Branch.objects.create(name="Lipa City", code="LIPA", is_kahero_branch=False)
        assert Branch.objects.filter(is_kahero_branch=False).count() == 2


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
        """Confirms Django's default PBKDF2 hasher is active (Phase 3
        decision). Explicitly restores the real hasher for this one test
        -- config/settings/test.py swaps in a fast, weak hasher suite-wide
        (Row 12.4) purely for test speed, which would otherwise mask
        exactly the thing this test exists to verify."""
        with override_settings(
            PASSWORD_HASHERS=["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
        ):
            user = User.objects.create_user(username="cashier2", password="testpass123")
            assert user.password != "testpass123"
            assert user.password.startswith("pbkdf2_")

    def test_is_synthetic_backfill_account_true_only_for_the_bot_username(self):
        """Client review (2026-11-10): confirms the classification used
        by apps.analytics.services to exclude the forecast-backfill
        bot's rows from every report total."""
        role = Role.objects.create(name=Role.OWNER_ADMIN)
        bot = User.objects.create_user(username="synthetic_backfill_bot", role=role)
        other = User.objects.create_user(username="cashier1", role=role)
        assert bot.is_synthetic_backfill_account is True
        assert other.is_synthetic_backfill_account is False
        assert bot.is_historical_import_account is False

    def test_is_historical_import_account_true_for_the_per_branch_prefix(self):
        role = Role.objects.create(name=Role.BRANCH_STAFF)
        importer = User.objects.create_user(username="historical_import_lipa", role=role)
        other = User.objects.create_user(username="cashier1", role=role)
        assert importer.is_historical_import_account is True
        assert other.is_historical_import_account is False
        assert importer.is_synthetic_backfill_account is False

    def test_display_name_for_a_real_user_falls_back_to_username(self):
        role = Role.objects.create(name=Role.BRANCH_STAFF)
        no_name = User.objects.create_user(username="cashier1", role=role)
        named = User.objects.create_user(
            username="cashier2", role=role, first_name="Alex", last_name="Cruz"
        )
        assert no_name.display_name == "cashier1"
        assert named.display_name == "Alex Cruz"

    def test_display_name_for_the_historical_import_account_is_imported(self):
        role = Role.objects.create(name=Role.BRANCH_STAFF)
        importer = User.objects.create_user(username="historical_import_lipa", role=role)
        assert importer.display_name == "Imported"

    def test_display_name_for_the_synthetic_bot_is_a_short_marked_label(self):
        """Never the bot's own real first_name (a long, scary string --
        see scripts/backfill_thin_forecast_pairs.py's ensure_bot_user)
        and never a bare username presented as an ordinary cashier."""
        role = Role.objects.create(name=Role.OWNER_ADMIN)
        bot = User.objects.create_user(
            username="synthetic_backfill_bot",
            role=role,
            first_name="Synthetic Backfill Bot (forecast-only, not a real staff account)",
        )
        assert bot.display_name == "Synthetic (forecast only)"


class TestCashierPIN:
    def test_set_pin_stores_a_hash_not_plaintext(self):
        """Same restore-the-real-hasher rationale as
        test_password_is_hashed_not_plaintext above."""
        with override_settings(
            PASSWORD_HASHERS=["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
        ):
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
