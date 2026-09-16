"""
Tests for Week 12 (batch 2): 2FA Enrollment Finalization (Row 12.4) --
QR code generation, backup/recovery codes, and the OTP enforcement fix
(role_required now actually requires 2FA for Owner/Admin users, not just
the one demonstration view it was originally wired to).
"""

from unittest.mock import patch

import pytest
from django.urls import reverse
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import Role, TwoFactorBackupCode, User
from apps.accounts.services import (
    count_remaining_backup_codes,
    generate_backup_codes,
    verify_and_consume_backup_code,
)
from conftest import verify_otp_for_client

pytestmark = pytest.mark.django_db


def _valid_token(device):
    return str(totp(device.bin_key)).zfill(6)


@pytest.fixture
def admin():
    role = Role.objects.create(name=Role.OWNER_ADMIN)
    return User.objects.create_user(
        username="owner1", email="owner@example.com", password="testpass123", role=role
    )


@pytest.fixture
def cashier():
    role = Role.objects.create(name=Role.BRANCH_STAFF)
    return User.objects.create_user(username="cashier1", password="testpass123", role=role)


class TestQRCodeView:
    def test_returns_a_png_image_for_the_unconfirmed_device(self, client, admin):
        client.force_login(admin)
        TOTPDevice.objects.create(user=admin, name="default", confirmed=False)

        response = client.get(reverse("accounts:admin_2fa_qr_code"))
        assert response.status_code == 200
        assert response["Content-Type"] == "image/png"
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"  # real PNG file signature

    def test_no_unconfirmed_device_returns_404(self, client, admin):
        client.force_login(admin)
        response = client.get(reverse("accounts:admin_2fa_qr_code"))
        assert response.status_code == 404

    def test_requires_login(self, client):
        response = client.get(reverse("accounts:admin_2fa_qr_code"))
        assert response.status_code == 302  # redirected to login


class TestBackupCodeGeneration:
    def test_generates_exactly_ten_codes(self, admin):
        codes = generate_backup_codes(admin)
        assert len(codes) == 10
        assert TwoFactorBackupCode.objects.filter(user=admin).count() == 10

    def test_codes_are_unique(self, admin):
        codes = generate_backup_codes(admin)
        assert len(set(codes)) == 10

    def test_regenerating_invalidates_old_codes(self, admin):
        first_batch = generate_backup_codes(admin)
        generate_backup_codes(admin)

        assert TwoFactorBackupCode.objects.filter(user=admin).count() == 10  # not 20
        # None of the old codes should still verify
        assert not any(verify_and_consume_backup_code(admin, code) for code in first_batch)

    def test_plaintext_codes_are_never_stored(self, admin):
        codes = generate_backup_codes(admin)
        for stored in TwoFactorBackupCode.objects.filter(user=admin):
            assert stored.code_hash not in codes
            assert len(stored.code_hash) > 20  # a real hash, not the raw 8-char code


class TestBackupCodeVerification:
    def test_valid_unused_code_verifies_successfully(self, admin):
        codes = generate_backup_codes(admin)
        assert verify_and_consume_backup_code(admin, codes[0]) is True

    def test_code_can_only_be_used_once(self, admin):
        codes = generate_backup_codes(admin)
        assert verify_and_consume_backup_code(admin, codes[0]) is True
        assert verify_and_consume_backup_code(admin, codes[0]) is False  # already used

    def test_wrong_code_is_rejected(self, admin):
        generate_backup_codes(admin)
        assert verify_and_consume_backup_code(admin, "WRONGCOD") is False

    def test_is_case_insensitive(self, admin):
        codes = generate_backup_codes(admin)
        assert verify_and_consume_backup_code(admin, codes[0].lower()) is True

    def test_count_remaining_decreases_as_codes_are_used(self, admin):
        codes = generate_backup_codes(admin)
        assert count_remaining_backup_codes(admin) == 10
        verify_and_consume_backup_code(admin, codes[0])
        assert count_remaining_backup_codes(admin) == 9


class TestSetupGeneratesBackupCodesOnConfirmation:
    def test_confirming_setup_shows_backup_codes_page(self, client, admin):
        client.force_login(admin)
        device = TOTPDevice.objects.create(user=admin, name="default", confirmed=False)

        response = client.post(reverse("accounts:admin_2fa_setup"), {"token": _valid_token(device)})
        assert response.status_code == 200
        assert b"Save Your Backup Codes" in response.content
        assert TwoFactorBackupCode.objects.filter(user=admin).count() == 10


class TestVerifyAcceptsBackupCode:
    def test_valid_backup_code_logs_in_successfully(self, client, admin):
        TOTPDevice.objects.create(user=admin, name="default", confirmed=True)
        codes = generate_backup_codes(admin)
        client.force_login(admin)

        response = client.post(reverse("accounts:admin_2fa_verify"), {"token": codes[0]})
        assert response.status_code == 302
        assert response.url == reverse("accounts:select_branch")

    def test_used_backup_code_cannot_be_reused(self, client, admin):
        TOTPDevice.objects.create(user=admin, name="default", confirmed=True)
        codes = generate_backup_codes(admin)
        client.force_login(admin)

        client.post(reverse("accounts:admin_2fa_verify"), {"token": codes[0]})
        response = client.post(reverse("accounts:admin_2fa_verify"), {"token": codes[0]})
        assert response.status_code == 200  # rejected, stayed on the verify page
        assert b"Incorrect code" in response.content


class TestRegenerateBackupCodesView:
    def test_requires_otp_verification_not_just_login(self, client, admin):
        client.force_login(admin)  # logged in, but no OTP verification this session
        response = client.get(reverse("accounts:admin_2fa_regenerate_backup_codes"))
        assert response.status_code == 302
        assert response.url.startswith(reverse("accounts:admin_login"))

    def test_verified_admin_can_view_confirmation_page(self, client, admin):
        client.force_login(admin)
        verify_otp_for_client(client, admin)
        response = client.get(reverse("accounts:admin_2fa_regenerate_backup_codes"))
        assert response.status_code == 200

    def test_posting_regenerates_and_shows_new_codes(self, client, admin):
        client.force_login(admin)
        verify_otp_for_client(client, admin)
        old_codes = generate_backup_codes(admin)

        response = client.post(reverse("accounts:admin_2fa_regenerate_backup_codes"))
        assert response.status_code == 200
        assert b"Save Your Backup Codes" in response.content
        assert not any(verify_and_consume_backup_code(admin, code) for code in old_codes)


class TestOTPEnforcementAcrossOwnerAdminViews:
    """The core Row 12.4 security fix: role_required(Role.OWNER_ADMIN)
    now also requires OTP verification, closing the gap where logging in
    via the plain username/password page bypassed 2FA for the entire
    real Owner/Admin surface."""

    def test_role_required_view_redirects_unverified_owner_to_admin_login(self, client, admin):
        """Logs in via the REGULAR (non-admin, non-2FA) login path, then
        tries to reach a role_required(OWNER_ADMIN) view directly."""
        client.force_login(admin)  # simulates the plain login bypass
        response = client.get(reverse("system_config:system_settings"))
        assert response.status_code == 302
        assert response.url == reverse("accounts:admin_login")

    def test_role_required_view_allows_a_verified_owner_through(self, client, admin):
        client.force_login(admin)
        verify_otp_for_client(client, admin)
        response = client.get(reverse("system_config:system_settings"))
        assert response.status_code == 200

    def test_shared_role_view_does_not_lock_out_non_admin_roles(self, client):
        """Production's views allow both Commissary Staff and Owner/Admin.
        A Commissary Staff user -- who never enrolls in 2FA at all --
        must still be able to reach it without hitting an OTP wall."""
        role = Role.objects.create(name=Role.COMMISSARY_STAFF)
        staff = User.objects.create_user(username="commissary1", password="testpass123", role=role)
        client.force_login(staff)
        response = client.get(reverse("production:batch_management"))
        assert response.status_code == 200

    def test_non_owner_admin_role_is_still_denied_by_permission_not_otp(self, client, cashier):
        """A user with the wrong role entirely gets PermissionDenied (403),
        not redirected to admin_login -- the role check still fires first."""
        client.force_login(cashier)
        response = client.get(reverse("system_config:system_settings"))
        assert response.status_code == 403


class TestInsightGeneratorStillWorksWithMockedAPI:
    """Sanity check that this batch's changes to permissions.py didn't
    accidentally affect unrelated modules -- included here since it's a
    quick, cheap confirmation alongside the larger permissions change."""

    def test_mocked_api_path_still_reachable(self, settings):
        from apps.forecasting.ml.insight_generator import generate_insight

        settings.CLAUDE_API_KEY = "fake-key"
        with patch("apps.forecasting.ml.insight_generator.requests.post") as mock_post:
            mock_post.return_value.json.return_value = {"content": [{"text": "ok"}]}
            mock_post.return_value.raise_for_status.return_value = None
            message, generated_by_ai = generate_insight(
                "DEMAND_SUMMARY", {"branch_name": "Lipa City", "forecast_summary": "steady"}
            )
        assert generated_by_ai is True
        assert message == "ok"
