"""
Views for accounts: Login/Start Screen, Branch Selection, Cashier PIN
Authentication (Phase 1 Figs. 3-9, 3-10, 3-11), Admin Login and 2FA
(Fig. 3-18, Row 16/17).

Password authentication itself is handled by Django's built-in LoginView/
LogoutView (PBKDF2 hashing, session handling, CSRF -- Phase 3 decision to
not reinvent this). The custom views here layer the branch-scoping,
cashier-PIN, and 2FA flow on top of an already-authenticated session.
"""

import base64
import io

import qrcode
from django.contrib.auth import authenticate
from django.contrib.auth import login as auth_login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views import View
from django_otp import login as otp_login
from django_otp.decorators import otp_required
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import Role, User
from apps.accounts.permissions import role_required
from apps.accounts.services import (
    generate_backup_codes,
    get_selectable_branches,
    user_can_select_branch,
    user_requires_cashier_pin,
    verify_and_consume_backup_code,
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
        if request.user.role and request.user.role.name == Role.OWNER_ADMIN:
            return redirect("accounts:admin_dashboard")
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
            return redirect("pos:ordering")
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


def _base32_secret(device):
    """Returns the TOTP device's secret key formatted for manual entry into
    an authenticator app (Google Authenticator, Authy, etc.) -- most apps
    expect base32, while django-otp stores the raw key as bytes/hex."""
    return base64.b32encode(device.bin_key).decode("utf-8")


@login_required
def two_factor_qr_code(request):
    """Renders the current unconfirmed TOTP device's provisioning URI
    (Row 12.4) as a PNG QR code -- scanning this is far less error-prone
    than manually typing a 32-character base32 secret, though the manual
    key is still shown as a fallback for devices that can't scan."""
    device = TOTPDevice.objects.filter(user=request.user, confirmed=False).first()
    if device is None:
        return HttpResponse(status=404)

    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(device.config_url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return HttpResponse(buffer.getvalue(), content_type="image/png")


class AdminLoginView(View):
    """Admin Login Interface (Fig. 3-18).

    Deliberately separate from the staff Login/Start Screen: authenticates
    by email rather than username, and only succeeds for accounts with the
    OWNER_ADMIN role -- entering correct credentials for a non-admin
    account here is treated the same as a wrong password, rather than
    leaking which accounts exist or what role they hold.
    """

    template_name = "accounts/admin_login.html"

    def get(self, request):
        return render(request, self.template_name)

    def post(self, request):
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")

        candidate = User.objects.filter(email__iexact=email).first()
        user = None
        if candidate is not None:
            authed = authenticate(request, username=candidate.username, password=password)
            if authed is not None and authed.role and authed.role.name == Role.OWNER_ADMIN:
                user = authed

        if user is None:
            return render(
                request,
                self.template_name,
                {"error": "Invalid email or password."},
            )

        auth_login(request, user)

        if TOTPDevice.objects.filter(user=user, confirmed=True).exists():
            return redirect("accounts:admin_2fa_verify")
        return redirect("accounts:admin_2fa_setup")


class TwoFactorSetupView(LoginRequiredMixin, View):
    """2FA enrollment (Row 17, finalized Row 12.4). Shown the first time
    an Owner/Admin logs in with no confirmed authenticator device yet.
    Generates (or reuses) an UNCONFIRMED device, shows a scannable QR
    code (plus the manual-entry secret as a fallback), and only marks it
    confirmed once the admin proves they actually captured it correctly
    by submitting a currently-valid code back -- at which point a batch
    of one-time backup codes is generated and shown exactly once."""

    template_name = "accounts/two_factor_setup.html"

    def get(self, request):
        device, _ = TOTPDevice.objects.get_or_create(
            user=request.user, confirmed=False, defaults={"name": "default"}
        )
        return render(
            request,
            self.template_name,
            {"secret": _base32_secret(device), "config_url": device.config_url},
        )

    def post(self, request):
        device = TOTPDevice.objects.filter(user=request.user, confirmed=False).first()
        token = request.POST.get("token", "")

        if device is not None and device.verify_token(token):
            device.confirmed = True
            device.save()
            otp_login(request, device)
            backup_codes = generate_backup_codes(request.user)
            return render(
                request, "accounts/two_factor_backup_codes.html", {"backup_codes": backup_codes}
            )

        error_context = {"error": "Incorrect code. Please try again."}
        if device is not None:
            error_context.update(
                {"secret": _base32_secret(device), "config_url": device.config_url}
            )
        return render(request, self.template_name, error_context)


class TwoFactorVerifyView(LoginRequiredMixin, View):
    """2FA verification for an Owner/Admin who already has a confirmed
    device -- the normal repeat-login path, as opposed to first-time
    setup. Accepts either a live TOTP token or a one-time backup code
    (Row 12.4), so a lost authenticator device doesn't lock an Owner/
    Admin out of their own account."""

    template_name = "accounts/two_factor_verify.html"

    def get(self, request):
        return render(request, self.template_name)

    def post(self, request):
        token = request.POST.get("token", "")
        for device in TOTPDevice.objects.filter(user=request.user, confirmed=True):
            if device.verify_token(token):
                otp_login(request, device)
                return redirect("accounts:select_branch")

        if verify_and_consume_backup_code(request.user, token):
            device = TOTPDevice.objects.filter(user=request.user, confirmed=True).first()
            if device is not None:
                otp_login(request, device)
            return redirect("accounts:select_branch")

        return render(
            request,
            self.template_name,
            {"error": "Incorrect code. Please try again."},
        )


@otp_required(login_url="accounts:admin_login")
def regenerate_backup_codes(request):
    """Lets an already-2FA-verified Owner/Admin invalidate their old
    backup codes and generate a fresh set (Row 12.4) -- e.g. after using
    several, or if they suspect an old code sheet was compromised.
    Requires an ALREADY verified session (not just a role check) since
    generating a fresh set of recovery credentials is itself a sensitive
    action."""
    if request.method == "POST":
        backup_codes = generate_backup_codes(request.user)
        return render(
            request, "accounts/two_factor_backup_codes.html", {"backup_codes": backup_codes}
        )
    return render(request, "accounts/two_factor_regenerate_confirm.html")


@role_required(Role.OWNER_ADMIN)
@otp_required(login_url="accounts:admin_login")
def admin_dashboard_placeholder(request):
    """Temporary Owner/Admin landing page. Protected by BOTH role (only
    OWNER_ADMIN) and OTP verification (must have completed 2FA this
    session) -- demonstrates the full enforcement chain this batch builds:
    Admin Login -> 2FA -> Branch Selection -> here."""
    return render(request, "accounts/admin_dashboard_placeholder.html")
