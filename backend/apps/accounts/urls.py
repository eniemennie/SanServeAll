from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.SanServeAllLoginView.as_view(), name="login"),
    path("logout/", views.SanServeAllLogoutView.as_view(), name="logout"),
    # Password reset (backend gap assessment): Owner/Admin and Commissary
    # Staff only -- the only two roles with a real password at all (Branch
    # Staff is PIN-only, see CashierPIN). Django's own PasswordResetForm
    # already excludes inactive accounts and accounts with an unusable
    # password, so a Branch Staff account can't reach this by email lookup
    # even though the URL itself isn't role-gated (it can't be -- the
    # requester isn't logged in yet).
    path(
        "password-reset/",
        auth_views.PasswordResetView.as_view(
            template_name="accounts/password_reset_form.html",
            email_template_name="accounts/password_reset_email.html",
            subject_template_name="accounts/password_reset_subject.txt",
            success_url=reverse_lazy("accounts:password_reset_done"),
        ),
        name="password_reset",
    ),
    path(
        "password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(template_name="accounts/password_reset_done.html"),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_reset_confirm.html",
            success_url=reverse_lazy("accounts:password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "reset/done/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="accounts/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),
    path("select-cashier/", views.SelectCashierView.as_view(), name="select_cashier"),
    path("cashier-lock/<int:user_id>/", views.CashierLockView.as_view(), name="cashier_lock"),
    path("select-branch/", views.BranchSelectionView.as_view(), name="select_branch"),
    path("pin/", views.CashierPinView.as_view(), name="cashier_pin"),
    # Commissary Staff login (landing page role selector, Row 19)
    path("commissary/login/", views.CommissaryLoginView.as_view(), name="commissary_login"),
    # Purchasing Staff login (landing page role selector, Row 19; Task #143)
    path("purchasing/login/", views.PurchasingLoginView.as_view(), name="purchasing_login"),
    # Admin login + 2FA (Row 16, Row 17)
    path("admin/login/", views.AdminLoginView.as_view(), name="admin_login"),
    path("admin/2fa/setup/", views.TwoFactorSetupView.as_view(), name="admin_2fa_setup"),
    path("admin/2fa/qr-code/", views.two_factor_qr_code, name="admin_2fa_qr_code"),
    path("admin/2fa/verify/", views.TwoFactorVerifyView.as_view(), name="admin_2fa_verify"),
    path(
        "admin/2fa/backup-codes/regenerate/",
        views.regenerate_backup_codes,
        name="admin_2fa_regenerate_backup_codes",
    ),
    path("admin/dashboard/", views.admin_dashboard, name="admin_dashboard"),
    # Account Management (backend gap assessment): create/edit a staff
    # account, assign branch, reset a cashier's PIN.
    path("manage/", views.account_management, name="account_management"),
    path("manage/<int:user_id>/", views.account_edit, name="account_edit"),
]
