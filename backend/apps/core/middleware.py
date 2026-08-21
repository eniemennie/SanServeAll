"""
Branch-scoping middleware (Row 16 / Phase 2 design).

Runs on every request after AuthenticationMiddleware. Re-validates the
session's selected_branch_id against the user's actual assigned branch on
EVERY request, not just at selection time -- the view-level check in
BranchSelectionView (Week 3 batch 1) only guards the moment of selection;
this middleware is the defense-in-depth layer that catches a stale or
tampered session value on any later request (e.g., an admin reassigns a
cashier to a different branch mid-shift, or someone edits the session
cookie directly).

Attaches `request.selected_branch` (a Branch instance or None) so views
and templates can use it without re-querying the session/DB themselves.
"""

from apps.accounts.models import Branch, Role


class BranchScopingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.selected_branch = None

        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            branch_id = request.session.get("selected_branch_id")
            if branch_id:
                if self._session_branch_is_valid_for_user(user, branch_id):
                    request.selected_branch = Branch.objects.filter(pk=branch_id).first()
                else:
                    # Stale/tampered value -- force the user back through
                    # branch selection rather than silently trusting it.
                    request.session.pop("selected_branch_id", None)
                    request.session.pop("pos_unlocked", None)

        return self.get_response(request)

    @staticmethod
    def _session_branch_is_valid_for_user(user, branch_id):
        is_owner_admin = bool(user.role and user.role.name == Role.OWNER_ADMIN)
        if is_owner_admin:
            # Owner/Admin may hold any active branch as their dashboard
            # context -- validated against Branch existing/active, not
            # against a fixed assignment.
            return Branch.objects.filter(pk=branch_id, is_active=True).exists()

        # Every other role is locked to their own assigned branch. This is
        # the actual anti-leakage guarantee: even if session data were
        # tampered with to reference a different branch_id, this check
        # rejects it on every subsequent request, not just at selection.
        try:
            branch_id = int(branch_id)
        except (TypeError, ValueError):
            return False
        return user.branch_id == branch_id
