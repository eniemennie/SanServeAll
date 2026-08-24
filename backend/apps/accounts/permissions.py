"""
Role-based access control helpers (Row 16).

Two forms of the same check are provided since the codebase mixes
function-based views (accounts app) and class-based views (also accounts
app, and likely most future apps per the Phase 4 pattern) -- both need to
enforce "only these roles may reach this view" without duplicating the
check logic.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def _user_has_role(user, allowed_roles):
    return bool(user.role and user.role.name in allowed_roles)


def role_required(*allowed_roles):
    """Decorator for function-based views.

    Usage:
        @role_required(Role.OWNER_ADMIN)
        def some_admin_view(request):
            ...
    """

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            if not _user_has_role(request.user, allowed_roles):
                raise PermissionDenied("You do not have permission to access this page.")
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
    call site rather than hidden inside this mixin.
    """

    allowed_roles = ()

    def dispatch(self, request, *args, **kwargs):
        if not _user_has_role(request.user, self.allowed_roles):
            raise PermissionDenied("You do not have permission to access this page.")
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
