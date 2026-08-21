from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.SanServeAllLoginView.as_view(), name="login"),
    path("logout/", views.SanServeAllLogoutView.as_view(), name="logout"),
    path("select-branch/", views.BranchSelectionView.as_view(), name="select_branch"),
    path("pin/", views.CashierPinView.as_view(), name="cashier_pin"),
    path("dashboard/", views.dashboard_placeholder, name="dashboard"),
    # Admin login + 2FA (Row 16, Row 17)
    path("admin/login/", views.AdminLoginView.as_view(), name="admin_login"),
    path("admin/2fa/setup/", views.TwoFactorSetupView.as_view(), name="admin_2fa_setup"),
    path("admin/2fa/verify/", views.TwoFactorVerifyView.as_view(), name="admin_2fa_verify"),
    path("admin/dashboard/", views.admin_dashboard_placeholder, name="admin_dashboard"),
]
