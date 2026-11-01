"""
Tests for the password-reset flow (backend gap assessment: "add
password-reset flow (Owner/Admin, Commissary)"). Built on Django's own
PasswordResetView/Confirm/Done/Complete rather than reinventing token
generation -- these tests mostly confirm OUR wiring (templates, URL
names, the console email backend, and that Branch Staff's PIN-only
accounts correctly can't use this), not Django's own, already-tested
token logic.
"""

import re

import pytest
from django.core import mail
from django.urls import reverse

from apps.accounts.models import Branch, CashierPIN, Role, User

pytestmark = pytest.mark.django_db


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
        username="owner1",
        email="owner@example.com",
        password="oldpass123",
        role=roles["owner_admin"],
    )


@pytest.fixture
def commissary_worker(roles):
    return User.objects.create_user(
        username="commissary1",
        email="commissary@example.com",
        password="oldpass123",
        role=roles["commissary_staff"],
    )


@pytest.fixture
def cashier(roles):
    branch = Branch.objects.create(name="Batangas City", code="BATANGAS")
    user = User.objects.create_user(
        username="cashier1", role=roles["branch_staff"], branch=branch
    )
    user.set_unusable_password()
    user.save()
    pin = CashierPIN.objects.create(user=user)
    pin.set_pin("1234")
    pin.save()
    return user


def _extract_reset_url(email_body):
    match = re.search(r"(/accounts/reset/\S+)", email_body)
    assert match, f"No reset link found in email body:\n{email_body}"
    return match.group(1)


class TestPasswordResetRequest:
    def test_request_page_loads(self, client):
        response = client.get(reverse("accounts:password_reset"))
        assert response.status_code == 200

    def test_known_owner_admin_email_gets_a_reset_email(self, client, owner, mailoutbox):
        response = client.post(reverse("accounts:password_reset"), {"email": owner.email})
        assert response.status_code == 302
        assert response.url == reverse("accounts:password_reset_done")
        assert len(mailoutbox) == 1
        assert owner.email in mailoutbox[0].to
        assert "/accounts/reset/" in mailoutbox[0].body

    def test_known_commissary_email_gets_a_reset_email(self, client, commissary_worker, mailoutbox):
        response = client.post(
            reverse("accounts:password_reset"), {"email": commissary_worker.email}
        )
        assert response.status_code == 302
        assert len(mailoutbox) == 1

    def test_branch_staff_unusable_password_account_gets_no_email(
        self, client, cashier, mailoutbox
    ):
        """The whole point of this exclusion: a PIN-only account has no
        password to reset, so Django's own PasswordResetForm.get_users()
        skips it -- same non-leaking redirect either way, confirmed by
        the next test."""
        cashier.email = "cashier@example.com"
        cashier.save()
        client.post(reverse("accounts:password_reset"), {"email": cashier.email})
        assert len(mailoutbox) == 0

    def test_unknown_email_still_redirects_to_done_no_leak(self, client, mailoutbox):
        """Same response whether or not the email exists -- otherwise the
        response itself would reveal which emails are registered."""
        response = client.post(
            reverse("accounts:password_reset"), {"email": "nobody@example.com"}
        )
        assert response.status_code == 302
        assert response.url == reverse("accounts:password_reset_done")
        assert len(mailoutbox) == 0


class TestPasswordResetConfirm:
    def test_valid_link_lets_an_owner_set_a_new_password(self, client, owner, mailoutbox):
        client.post(reverse("accounts:password_reset"), {"email": owner.email})
        reset_path = _extract_reset_url(mailoutbox[0].body)

        # Django's PasswordResetConfirmView redirects the raw token link
        # to a session-backed "set-password" URL on first GET -- follow
        # that redirect before posting the new password, same as a real
        # browser would.
        get_response = client.get(reset_path, follow=True)
        assert get_response.status_code == 200

        post_response = client.post(
            get_response.request["PATH_INFO"],
            {"new_password1": "brandNewPass456", "new_password2": "brandNewPass456"},
        )
        assert post_response.status_code == 302
        assert post_response.url == reverse("accounts:password_reset_complete")

        owner.refresh_from_db()
        assert owner.check_password("brandNewPass456")
        assert not owner.check_password("oldpass123")

    def test_invalid_link_shows_expired_message(self, client, owner):
        response = client.get(
            reverse(
                "accounts:password_reset_confirm",
                kwargs={"uidb64": "bad", "token": "bad-token"},
            ),
            follow=True,
        )
        assert response.status_code == 200
        assert b"invalid or has already been used" in response.content

    def test_mismatched_passwords_are_rejected(self, client, owner, mailoutbox):
        client.post(reverse("accounts:password_reset"), {"email": owner.email})
        reset_path = _extract_reset_url(mailoutbox[0].body)
        get_response = client.get(reset_path, follow=True)

        post_response = client.post(
            get_response.request["PATH_INFO"],
            {"new_password1": "brandNewPass456", "new_password2": "somethingElse789"},
        )
        assert post_response.status_code == 200  # re-rendered with errors

        owner.refresh_from_db()
        assert owner.check_password("oldpass123")  # unchanged
