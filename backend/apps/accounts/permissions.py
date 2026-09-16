"""
Role-based access control helpers (Row 16, hardened in Row 12.4).

Two forms of the same check are provided since the codebase mixes
function-based views (accounts app) and class-based views (also accounts
app, and likely most future apps per the Phase 4 pattern) -- both need to
enforce "only these roles may reach this view" without duplicating the
check logic.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


def _user_has_role(user, allowed_roles):
    return bool(user.role and user.role.name in allowed_roles)


def _otp_verified(user):
    return hasattr(user, "is_verified") and user.is_verified()


def _user_requires_otp(user):
    """Owner/Admin is the only role with a 2FA enrollment flow at all
    (Branch Staff and Commissary Staff never go through it). Checked
    against the ACTUAL authenticated user's role, not whether Owner/Admin
    merely appears among a view's allowed roles -- a view shared with
    Commissary Staff (e.g. Production) must not lock those users out just
    because Owner/Admin also happens to be allowed there."""
    from apps.accounts.models import Role

    return bool(user.role and user.role.name == Role.OWNER_ADMIN)


def role_required(*allowed_roles):
    """Decorator for function-based views.

    Usage:
        @role_required(Role.OWNER_ADMIN)
        def some_admin_view(request):
            ...

    Row 12.4 finding: role alone previously let an Owner/Admin who logged
    in via the plain username/password form (bypassing the admin-specific
    2FA-enforced login path) reach every Owner/Admin screen in the system
    -- 2FA was only ever actually enforced on one demonstration view. This
    now redirects to complete 2FA whenever Owner/Admin is required and the
    current session hasn't verified it, regardless of which login path
    was used to authenticate.
    """

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            if not _user_has_role(request.user, allowed_roles):
                raise PermissionDenied("You do not have permission to access this page.")
            if _user_requires_otp(request.user) and not _otp_verified(request.user):
                return redirect("accounts:admin_login")
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


class RoleRequiredMixin:
    """Mixin for class-based views. Set `allowed_roles` on the subclass.

    Usage:
        class SomeAdminView(RoleRequiredMixin, LoginRequiredMixin, View):
            allowed_roles = [Role.OWNER_ADMIN]
            ...

    Deliberately does NOT include LoginRequiredMixin itself -- combine
    explicitly so the MRO/redirect-to-login behavior stays obvious at the
    call site rather than hidden inside this mixin. Same Row 12.4 OTP
    enforcement as role_required above.
    """

    allowed_roles = ()

    def dispatch(self, request, *args, **kwargs):
        if not _user_has_role(request.user, self.allowed_roles):
            raise PermissionDenied("You do not have permission to access this page.")
        if _user_requires_otp(request.user) and not _otp_verified(request.user):
            return redirect("accounts:admin_login")
        return super().dispatch(request, *args, **kwargs)


def pos_unlock_required(view_func):
    """Gates POS views (Week 4+) behind the full Week 3 flow: logged in,
    a branch selected, and the Cashier PIN actually entered this session.

    Rather than a blanket 403, each failure state redirects to wherever the
    user actually needs to go next -- login, branch selection, or the PIN
    screen -- so a cashier who, say, refreshes mid-shift after a session
    timeout lands back in the right place instead of a dead end.
    """

    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        from django.shortcuts import redirect

        if "selected_branch_id" not in request.session:
            return redirect("accounts:select_branch")
        if not request.session.get("pos_unlocked"):
            return redirect("accounts:cashier_pin")
        return view_func(request, *args, **kwargs)

    return _wrapped
