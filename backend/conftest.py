"""
Shared pytest fixtures and helpers.
"""

from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice


def verify_otp_for_client(client, user):
    """Marks a test client's session as having completed 2FA for `user`,
    without going through the real login->2FA-verify HTTP flow.

    Since Row 12.4, role_required(Role.OWNER_ADMIN) also enforces OTP
    verification for actual Owner/Admin users -- any test that does
    `client.force_login(owner)` and then expects to reach an Owner/Admin-
    gated view must also call this, or it will be redirected to
    accounts:admin_login instead of reaching the view under test.

    Creates a confirmed TOTPDevice for the user if one doesn't already
    exist, then sets the exact session key OTPMiddleware reads
    (DEVICE_ID_SESSION_KEY) to that device's persistent_id.
    """
    device, _ = TOTPDevice.objects.get_or_create(
        user=user, name="default", defaults={"confirmed": True}
    )
    if not device.confirmed:
        device.confirmed = True
        device.save()

    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()
    return device
