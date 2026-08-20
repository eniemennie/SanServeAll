"""
Views for accounts: Login/Start Screen, Branch Selection, Cashier PIN
Authentication (Phase 1 Figs. 3-9, 3-10, 3-11).

Password authentication itself is handled by Django's built-in LoginView/
LogoutView (PBKDF2 hashing, session handling, CSRF -- Phase 3 decision to
not reinvent this). The custom views here layer the branch-scoping and
cashier-PIN flow on top of an already-authenticated session.
"""

from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.views import View

from apps.accounts.services import (
    get_selectable_branches,
    user_can_select_branch,
    user_requires_cashier_pin,
    verify_cashier_pin,
)


class SanServeAllLoginView(auth_views.LoginView):
    """Login/Start Screen (Fig. 3-9). Redirects to branch selection on
    success rather than a fixed URL, since where a user lands next depends
    on their role."""

    template_name = "accounts/login.html"

    def get_success_url(self):
        return "/accounts/select-branch/"


class SanServeAllLogoutView(auth_views.LogoutView):
    """Also clears the PIN-unlock flag, so a new login always starts from
    a locked POS state rather than inheriting a stale unlock."""

    def dispatch(self, request, *args, **kwargs):
        request.session.pop("pos_unlocked", None)
        request.session.pop("selected_branch_id", None)
        return super().dispatch(request, *args, **kwargs)


class BranchSelectionView(LoginRequiredMixin, View):
    """Branch Selection Screen (Fig. 3-10).

    Branch-scoped users (Branch Staff, Commissary Staff) have exactly one
    selectable branch and are routed straight through without being shown
    a picker -- the screen only meaningfully appears for Owner/Admin, who
    is choosing dashboard context rather than a work assignment.
    """

    template_name = "accounts/branch_selection.html"

    def get(self, request):
        branches = get_selectable_branches(request.user)

        if branches.count() == 1:
            return self._select_and_redirect(request, branches.first().pk)

        return render(request, self.template_name, {"branches": branches})

    def post(self, request):
        branch_id = request.POST.get("branch_id")
        if not branch_id or not user_can_select_branch(request.user, branch_id):
            branches = get_selectable_branches(request.user)
            return render(
                request,
                self.template_name,
                {"branches": branches, "error": "Please select a valid branch."},
            )
        return self._select_and_redirect(request, branch_id)

    def _select_and_redirect(self, request, branch_id):
        request.session["selected_branch_id"] = int(branch_id)
        if user_requires_cashier_pin(request.user):
            return redirect("accounts:cashier_pin")
        return redirect("accounts:dashboard")


class CashierPinView(LoginRequiredMixin, View):
    """Cashier PIN Authentication Screen (Fig. 3-11).

    A secondary, lightweight unlock on top of an already-logged-in session
    -- not a second login system. Sets a session flag scoped to the branch
    chosen on the previous screen; POS views (Week 4-5) will check this
    flag rather than re-prompting per action.
    """

    template_name = "accounts/pin_auth.html"

    def get(self, request):
        if "selected_branch_id" not in request.session:
            return redirect("accounts:select_branch")
        return render(request, self.template_name)

    def post(self, request):
        raw_pin = request.POST.get("pin", "")
        if verify_cashier_pin(request.user, raw_pin):
            request.session["pos_unlocked"] = True
            return redirect("accounts:dashboard")
        return render(
            request,
            self.template_name,
            {"error": "Incorrect PIN. Please try again."},
        )


@login_required
def dashboard_placeholder(request):
    """Temporary landing page post-login/PIN. Real role-specific dashboards
    (POS ordering, Owner/Admin analytics) land in later weeks -- this exists
    so the Week 3 flow has somewhere valid to redirect to and be tested
    end-to-end rather than dead-ending at a 404."""
    return render(request, "accounts/dashboard_placeholder.html")
