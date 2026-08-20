from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.SanServeAllLoginView.as_view(), name="login"),
    path("logout/", views.SanServeAllLogoutView.as_view(), name="logout"),
    path("select-branch/", views.BranchSelectionView.as_view(), name="select_branch"),
    path("pin/", views.CashierPinView.as_view(), name="cashier_pin"),
    path("dashboard/", views.dashboard_placeholder, name="dashboard"),
]
