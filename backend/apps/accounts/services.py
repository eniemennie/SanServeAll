"""
Business logic for accounts: PIN verification, branch-selection rules,
2FA backup codes (Row 12.4).

Keeps views.py thin (Phase 2 service-layer pattern) -- views handle HTTP
concerns only, this module holds the actual rules.
"""

import secrets
import string

from django.utils import timezone

from apps.accounts.models import Branch, CashierPIN, Role, TwoFactorBackupCode


def get_selectable_branches(user):
    """Return the branches a given user is allowed to operate in.

    OWNER_ADMIN is not scoped to a single branch (Phase 1 Section 2.1) and
    may select any active branch for dashboard/context purposes. All other
    roles are locked to their own assigned branch -- if the user already has
    a branch on their account, that is the only option, preventing the
    cross-branch data leakage the Branch Selection screen exists to guard
    against (Phase 2 design).
    """
    if user.role and user.role.name == Role.OWNER_ADMIN:
        return Branch.objects.filter(is_active=True).order_by("name")

    if user.branch_id:
        return Branch.objects.filter(pk=user.branch_id, is_active=True)

    return Branch.objects.none()


def user_can_select_branch(user, branch_id):
    """Confirms `branch_id` is one this user is actually allowed to pick,
    rather than trusting a posted value blindly."""
    return get_selectable_branches(user).filter(pk=branch_id).exists()


def user_requires_cashier_pin(user):
    """Only BRANCH_STAFF unlock POS actions via PIN -- Owner/Admin and
    Commissary Staff don't use the POS at all, so the PIN screen doesn't
    apply to them (Phase 1 Section 2: role responsibilities)."""
    return bool(user.role and user.role.name == Role.BRANCH_STAFF)


def verify_cashier_pin(user, raw_pin):
    """Checks a submitted PIN against the user's stored CashierPIN.

    Returns False (not an exception) for "no PIN set yet" and "wrong PIN"
    alike -- the caller shouldn't be able to distinguish those two cases
    from the response, which avoids leaking whether a PIN has been
    configured for a given account.
    """
    try:
        pin_record = user.cashier_pin
    except CashierPIN.DoesNotExist:
        return False

    if not pin_record.is_active:
        return False

    return pin_record.check_pin(raw_pin)


BACKUP_CODE_COUNT = 10
BACKUP_CODE_ALPHABET = string.ascii_uppercase + string.digits


def _generate_one_backup_code():
    """8 characters from an unambiguous alphabet (uppercase letters +
    digits) -- no 0/O or 1/I/L confusion, since these are meant to be
    handwritten or read off a printed sheet during an actual account-
    recovery situation, not typed from a password manager."""
    return "".join(secrets.choice(BACKUP_CODE_ALPHABET) for _ in range(8))


def generate_backup_codes(user):
    """Generates a fresh batch of 10 single-use recovery codes for a
    user, replacing any existing ones (Row 12.4) -- e.g. right after 2FA
    setup is first confirmed, or if an admin explicitly regenerates them
    because they suspect a code was compromised.

    Returns the PLAINTEXT codes -- the only moment they ever exist in
    that form. Only the hash is persisted; the caller must show these to
    the user immediately, since they cannot be retrieved again.
    """
    TwoFactorBackupCode.objects.filter(user=user).delete()

    plaintext_codes = []
    for _ in range(BACKUP_CODE_COUNT):
        raw_code = _generate_one_backup_code()
        backup_code = TwoFactorBackupCode(user=user)
        backup_code.set_code(raw_code)
        backup_code.save()
        plaintext_codes.append(raw_code)

    return plaintext_codes


def verify_and_consume_backup_code(user, raw_code):
    """Checks a submitted code against the user's unused backup codes;
    marks it used (single-use) if it matches. Returns True/False -- never
    reveals whether the code was simply wrong vs. already used, same
    non-leaking principle as verify_cashier_pin above."""
    raw_code = (raw_code or "").strip().upper()
    if not raw_code:
        return False

    for backup_code in TwoFactorBackupCode.objects.filter(user=user, used_at__isnull=True):
        if backup_code.check_code(raw_code):
            backup_code.used_at = timezone.now()
            backup_code.save()
            return True

    return False


def count_remaining_backup_codes(user):
    return TwoFactorBackupCode.objects.filter(user=user, used_at__isnull=True).count()
