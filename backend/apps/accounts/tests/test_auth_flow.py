"""
Tests for the Week 3 auth flow (Row 14, Row 15): Login, Branch Selection,
Cashier PIN. Covers both the happy path and the branch-scoping rules that
exist specifically to prevent cross-branch data leakage (Phase 2 design).
"""

import pytest
from django.urls import reverse

from apps.accounts.models import Branch, CashierPIN, Role, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def branches():
    return {
        "batangas": Branch.objects.create(name="Batangas City", code="BATANGAS"),
        "alangilan": Branch.objects.create(
            name="Alangilan", code="ALANGILAN", is_kahero_branch=True
        ),
        "lipa": Branch.objects.create(name="Lipa City", code="LIPA"),
    }


@pytest.fixture
def roles():
    return {
        "owner_admin": Role.objects.create(name=Role.OWNER_ADMIN),
        "branch_staff": Role.objects.create(name=Role.BRANCH_STAFF),
        "commissary_staff": Role.objects.create(name=Role.COMMISSARY_STAFF),
    }


@pytest.fixture
def cashier(roles, branches):
    user = User.objects.create_user(
        username="cashier1",
        password="testpass123",
        role=roles["branch_staff"],
        branch=branches["lipa"],
    )
    pin = CashierPIN.objects.create(user=user)
    pin.set_pin("1234")
    pin.save()
    return user


@pytest.fixture
def owner(roles):
    return User.objects.create_user(
        username="owner1", password="testpass123", role=roles["owner_admin"]
    )


@pytest.fixture
def commissary_worker(roles, branches):
    return User.objects.create_user(
        username="commissary1",
        password="testpass123",
        role=roles["commissary_staff"],
        branch=branches["batangas"],
    )


class TestLogin:
    def test_valid_credentials_log_in_and_redirect_to_branch_selection(self, client, cashier):
        response = client.post(
            reverse("accounts:login"),
            {"username": "cashier1", "password": "testpass123"},
        )
        assert response.status_code == 302
        assert response.url == "/accounts/select-branch/"

    def test_invalid_credentials_do_not_log_in(self, client, cashier):
        response = client.post(
            reverse("accounts:login"),
            {"username": "cashier1", "password": "wrongpassword"},
        )
        assert response.status_code == 200  # re-renders form, no redirect
        assert not response.wsgi_request.user.is_authenticated

    def test_login_page_loads(self, client):
        response = client.get(reverse("accounts:login"))
        assert response.status_code == 200


class TestBranchSelection:
    def test_branch_staff_with_one_branch_skips_picker_and_goes_to_pin(self, client, cashier):
        client.force_login(cashier)
        response = client.get(reverse("accounts:select_branch"))
        assert response.status_code == 302
        assert response.url == reverse("accounts:cashier_pin")

    def test_branch_staff_session_has_correct_branch_after_auto_select(self, client, cashier):
        client.force_login(cashier)
        client.get(reverse("accounts:select_branch"))
        assert client.session["selected_branch_id"] == cashier.branch_id

    def test_commissary_staff_with_one_branch_skips_picker_goes_to_dashboard(
        self, client, commissary_worker
    ):
        client.force_login(commissary_worker)
        response = client.get(reverse("accounts:select_branch"))
        assert response.status_code == 302
        assert response.url == reverse("accounts:dashboard")

    def test_owner_admin_sees_all_active_branches_in_picker(self, client, owner, branches):
        client.force_login(owner)
        response = client.get(reverse("accounts:select_branch"))
        assert response.status_code == 200
        for branch in branches.values():
            assert branch.name.encode() in response.content

    def test_owner_admin_can_select_any_branch(self, client, owner, branches):
        client.force_login(owner)
        response = client.post(
            reverse("accounts:select_branch"), {"branch_id": branches["alangilan"].pk}
        )
        assert response.status_code == 302
        assert client.session["selected_branch_id"] == branches["alangilan"].pk

    def test_branch_staff_cannot_select_a_different_branch_than_their_own(
        self, client, cashier, branches
    ):
        """The core anti-leakage guarantee: even a crafted POST with a
        different branch_id must be rejected for a branch-scoped user."""
        client.force_login(cashier)
        other_branch = branches["batangas"]
        response = client.post(reverse("accounts:select_branch"), {"branch_id": other_branch.pk})
        assert response.status_code == 200  # re-rendered with error, not redirected
        assert b"valid branch" in response.content

    def test_anonymous_user_is_redirected_to_login(self, client):
        response = client.get(reverse("accounts:select_branch"))
        assert response.status_code == 302
        assert "/accounts/login/" in response.url


class TestCashierPin:
    def test_pin_screen_requires_branch_already_selected(self, client, cashier):
        client.force_login(cashier)
        response = client.get(reverse("accounts:cashier_pin"))
        assert response.status_code == 302
        assert response.url == reverse("accounts:select_branch")

    def test_correct_pin_unlocks_and_redirects_to_pos(self, client, cashier):
        client.force_login(cashier)
        session = client.session
        session["selected_branch_id"] = cashier.branch_id
        session.save()

        response = client.post(reverse("accounts:cashier_pin"), {"pin": "1234"})
        assert response.status_code == 302
        assert response.url == reverse("pos:ordering")
        assert client.session["pos_unlocked"] is True

    def test_incorrect_pin_does_not_unlock(self, client, cashier):
        client.force_login(cashier)
        session = client.session
        session["selected_branch_id"] = cashier.branch_id
        session.save()

        response = client.post(reverse("accounts:cashier_pin"), {"pin": "0000"})
        assert response.status_code == 200
        assert "pos_unlocked" not in client.session
        assert b"Incorrect PIN" in response.content


class TestLogout:
    def test_logout_clears_session_flags(self, client, cashier):
        client.force_login(cashier)
        session = client.session
        session["selected_branch_id"] = cashier.branch_id
        session["pos_unlocked"] = True
        session.save()

        client.post(reverse("accounts:logout"))
        assert "pos_unlocked" not in client.session
        assert "selected_branch_id" not in client.session
