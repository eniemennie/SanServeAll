"""
Business logic for accounts: PIN verification, branch-selection rules.

Keeps views.py thin (Phase 2 service-layer pattern) -- views handle HTTP
concerns only, this module holds the actual rules.
"""

from apps.accounts.models import Branch, CashierPIN, Role


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
