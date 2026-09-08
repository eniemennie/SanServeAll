"""
Tests for the Row 3 redesign: tap-to-select cashier entry (no username/
password), PIN as the sole credential, with brute-force lockout.
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Branch, CashierPIN, Role, User
from apps.accounts.services import (
    PIN_LOCKOUT_THRESHOLD,
    get_selectable_cashiers,
    verify_cashier_pin_with_lockout,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def branch():
    return Branch.objects.create(name="Lipa City", code="LIPA")


@pytest.fixture
def cashier(branch):
    role = Role.objects.create(name=Role.BRANCH_STAFF)
    user = User.objects.create_user(
        username="cashier1", first_name="Mae", last_name="Manalo", role=role, branch=branch
    )
    pin = CashierPIN.objects.create(user=user)
    pin.set_pin("1234")
    pin.save()
    return user


class TestGetSelectableCashiers:
    def test_returns_active_branch_staff_with_a_branch(self, cashier):
        cashiers = get_selectable_cashiers()
        assert cashier in cashiers

    def test_excludes_owner_admin(self, branch):
        role = Role.objects.create(name=Role.OWNER_ADMIN)
        User.objects.create_user(username="owner1", role=role)
        assert get_selectable_cashiers().count() == 0

    def test_excludes_commissary_staff(self):
        role = Role.objects.create(name=Role.COMMISSARY_STAFF)
        User.objects.create_user(username="commissary1", role=role)
        assert get_selectable_cashiers().count() == 0

    def test_excludes_inactive_users(self, cashier):
        cashier.is_active = False
        cashier.save()
        assert cashier not in get_selectable_cashiers()

    def test_excludes_branch_staff_with_no_branch_assigned(self):
        role = Role.objects.create(name=Role.BRANCH_STAFF)
        User.objects.create_user(username="unassigned", role=role)
        assert get_selectable_cashiers().count() == 0


class TestVerifyCashierPinWithLockout:
    def test_correct_pin_succeeds(self, cashier):
        success, error = verify_cashier_pin_with_lockout(cashier, "1234")
        assert success is True
        assert error is None

    def test_wrong_pin_fails_with_generic_message(self, cashier):
        success, error = verify_cashier_pin_with_lockout(cashier, "9999")
        assert success is False
        assert error == "Incorrect PIN. Please try again."

    def test_no_pin_configured_fails_generically_not_with_a_crash(self, branch):
        role = Role.objects.create(name=Role.BRANCH_STAFF)
        user = User.objects.create_user(username="nopin", role=role, branch=branch)
        success, error = verify_cashier_pin_with_lockout(user, "1234")
        assert success is False
        assert error == "Incorrect PIN. Please try again."

    def test_correct_pin_resets_failed_attempt_counter(self, cashier):
        verify_cashier_pin_with_lockout(cashier, "0000")
        verify_cashier_pin_with_lockout(cashier, "0000")
        verify_cashier_pin_with_lockout(cashier, "1234")  # correct
        cashier.cashier_pin.refresh_from_db()
        assert cashier.cashier_pin.failed_attempts == 0

    def test_locks_out_after_threshold_failed_attempts(self, cashier):
        for _ in range(PIN_LOCKOUT_THRESHOLD):
            verify_cashier_pin_with_lockout(cashier, "0000")

        # Even the CORRECT pin is now rejected while locked out
        success, error = verify_cashier_pin_with_lockout(cashier, "1234")
        assert success is False
        assert "Too many incorrect attempts" in error

    def test_lockout_expires_after_the_duration(self, cashier):
        for _ in range(PIN_LOCKOUT_THRESHOLD):
            verify_cashier_pin_with_lockout(cashier, "0000")

        # Simulate the lockout window having already passed
        cashier.cashier_pin.refresh_from_db()
        cashier.cashier_pin.locked_until = timezone.now() - timedelta(seconds=1)
        cashier.cashier_pin.save()

        success, error = verify_cashier_pin_with_lockout(cashier, "1234")
        assert success is True


class TestSelectCashierView:
    def test_page_loads_without_any_login(self, client):
        """The entire point of this screen -- reachable by a completely
        anonymous visitor at a shared terminal."""
        response = client.get(reverse("accounts:select_cashier"))
        assert response.status_code == 200

    def test_shows_the_cashier_and_their_branch(self, client, cashier):
        response = client.get(reverse("accounts:select_cashier"))
        assert b"Mae" in response.content
        assert b"Lipa City" in response.content

    def test_links_to_that_cashiers_lock_screen(self, client, cashier):
        response = client.get(reverse("accounts:select_cashier"))
        expected_url = reverse("accounts:cashier_lock", args=[cashier.pk])
        assert expected_url.encode() in response.content


class TestCashierLockView:
    def test_get_shows_the_named_cashier(self, client, cashier):
        response = client.get(reverse("accounts:cashier_lock", args=[cashier.pk]))
        assert response.status_code == 200
        assert b"Mae" in response.content

    def test_invalid_user_id_returns_404_not_a_crash(self, client):
        response = client.get(reverse("accounts:cashier_lock", args=[999999]))
        assert response.status_code == 404

    def test_owner_admin_id_is_not_reachable_via_this_url(self, client, branch):
        """Confirms the lookup is scoped to get_selectable_cashiers(), not
        any arbitrary user ID -- an Owner/Admin account must not be
        reachable through the cashier PIN flow at all."""
        role = Role.objects.create(name=Role.OWNER_ADMIN)
        owner = User.objects.create_user(username="owner1", role=role)
        response = client.get(reverse("accounts:cashier_lock", args=[owner.pk]))
        assert response.status_code == 404

    def test_correct_pin_logs_in_and_redirects_to_pos(self, client, cashier):
        response = client.post(reverse("accounts:cashier_lock", args=[cashier.pk]), {"pin": "1234"})
        assert response.status_code == 302
        assert response.url == reverse("pos:ordering")

    def test_correct_pin_actually_authenticates_the_session(self, client, cashier):
        """Not just a redirect -- the user must be genuinely logged in,
        since PIN is now the sole credential, not a secondary unlock on
        an already-authenticated session."""
        client.post(reverse("accounts:cashier_lock", args=[cashier.pk]), {"pin": "1234"})
        response = client.get(reverse("pos:ordering"))
        assert response.status_code == 200  # reachable without a further login step

    def test_correct_pin_sets_the_cashiers_own_branch(self, client, cashier, branch):
        client.post(reverse("accounts:cashier_lock", args=[cashier.pk]), {"pin": "1234"})
        session = client.session
        assert session["selected_branch_id"] == branch.pk

    def test_wrong_pin_does_not_log_in(self, client, cashier):
        response = client.post(reverse("accounts:cashier_lock", args=[cashier.pk]), {"pin": "9999"})
        assert response.status_code == 200  # stayed on the page, not redirected
        assert b"Incorrect PIN" in response.content

        # Confirm no session was actually established
        response = client.get(reverse("pos:ordering"))
        assert response.status_code == 302  # redirected to login, not authenticated

    def test_lockout_is_shown_and_blocks_login_even_with_the_right_pin(self, client, cashier):
        for _ in range(PIN_LOCKOUT_THRESHOLD):
            client.post(reverse("accounts:cashier_lock", args=[cashier.pk]), {"pin": "0000"})

        response = client.post(reverse("accounts:cashier_lock", args=[cashier.pk]), {"pin": "1234"})
        assert response.status_code == 200
        assert b"Too many incorrect attempts" in response.content
