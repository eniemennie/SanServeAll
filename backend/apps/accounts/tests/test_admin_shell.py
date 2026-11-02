"""
Tests for the Admin Shell (shared topbar + sidebar template every
Owner/Admin screen extends). Verifies the shell itself renders correctly
around the real admin_dashboard view (Dashboard Home) -- its own
access-control behavior (role + 2FA gating) is already covered by
test_rbac_and_2fa.py and isn't re-tested here.
"""

import pytest
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import Branch, Role, User
from apps.inventory.models import Inventory, Product
from conftest import verify_otp_for_client

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner_admin_role():
    return Role.objects.create(name=Role.OWNER_ADMIN)


@pytest.fixture
def branches():
    return [
        Branch.objects.create(name="Batangas City", code="BATANGAS"),
        Branch.objects.create(name="Lipa", code="LIPA"),
        Branch.objects.create(name="Alangilan", code="ALANGILAN", is_kahero_branch=True),
    ]


@pytest.fixture
def admin(owner_admin_role):
    return User.objects.create_user(
        username="owner1",
        email="owner@example.com",
        password="adminpass123",
        role=owner_admin_role,
    )


@pytest.fixture
def authed_client(client, admin):
    TOTPDevice.objects.create(user=admin, name="default", confirmed=True)
    client.force_login(admin)
    verify_otp_for_client(client, admin)
    return client


class TestAdminShell:
    def test_dashboard_renders_inside_shell(self, authed_client):
        response = authed_client.get(reverse("accounts:admin_dashboard"))
        assert response.status_code == 200
        content = response.content.decode()

        # Sidebar nav: all 7 screens present with real url-reversed hrefs.
        assert reverse("accounts:admin_dashboard") in content
        assert reverse("production:batch_management") in content
        assert reverse("forecasting:forecasting_dashboard") in content
        assert reverse("forecasting:resource_management_dashboard") in content
        # Was analytics:sales_dashboard -- the Reports & Export sidebar
        # item now points at the dedicated Reports hub instead (Reports
        # & Export batch), which itself links onward to sales_dashboard/
        # product_performance/resource_consumption as report tabs.
        assert reverse("analytics:reports_hub") in content
        assert reverse("inventory:product_management") in content
        assert reverse("system_config:system_settings") in content

        # Dashboard is the active nav item on this screen.
        assert "jc-nav-item active" in content

    def test_branch_dropdown_lists_active_non_commissary_branches(self, authed_client, branches):
        response = authed_client.get(reverse("accounts:admin_dashboard"))
        content = response.content.decode()

        assert "All Branches" in content
        assert "Batangas City" in content
        assert "Lipa" in content
        assert "Alangilan" in content

    def test_selecting_a_branch_marks_it_selected(self, authed_client, branches):
        alangilan = next(b for b in branches if b.code == "ALANGILAN")
        response = authed_client.get(reverse("accounts:admin_dashboard"), {"branch": alangilan.pk})
        content = response.content.decode()

        assert f'value="{alangilan.pk}" selected' in content

    def test_logout_link_present(self, authed_client):
        response = authed_client.get(reverse("accounts:admin_dashboard"))
        content = response.content.decode()
        assert reverse("accounts:logout") in content

    def test_sidebar_defaults_to_expanded_with_labels_visible(self, authed_client):
        """Adviser feedback (compliance check item 21): icon-only
        navigation needs labels. Every nav item already carries a real
        text label in the markup; this confirms the sidebar now defaults
        to the expanded state so that label is visible without
        requiring a click, rather than just existing in markup a
        collapsed sidebar hides by default."""
        response = authed_client.get(reverse("accounts:admin_dashboard"))
        content = response.content.decode()
        assert 'data-expanded="true"' in content


class TestNoTemplateSyntaxLeaksIntoPages:
    """Regression: Django's {# ... #} comment only works on a SINGLE line.
    A multi-line one in admin_base.html was not treated as a comment and
    rendered as visible text inside the page's flex layout, squeezing the
    sidebar and content to one side. Every admin screen extends
    admin_base.html, so checking each one catches it however it recurs."""

    @pytest.mark.parametrize(
        "url_name",
        [
            "accounts:admin_dashboard",
            "production:batch_management",
            "forecasting:forecasting_dashboard",
            "forecasting:resource_management_dashboard",
            "analytics:reports_hub",
            "inventory:product_management",
            "system_config:system_settings",
        ],
    )
    def test_no_unrendered_template_comments_or_tags(self, authed_client, url_name):
        content = authed_client.get(reverse(url_name)).content.decode()
        assert "{#" not in content
        assert "#}" not in content
        assert "{%" not in content


class TestBranchPerformanceOverviewVisibility:
    """Item 1: Branch Performance Overview should only show for the
    "All Branches" view -- once a specific branch is selected, every
    other section already shows that branch's own numbers, so the
    separate multi-branch comparison is redundant."""

    def test_shown_when_no_branch_selected(self, authed_client, branches):
        response = authed_client.get(reverse("accounts:admin_dashboard"))
        assert "Branch Performance Overview" in response.content.decode()

    def test_hidden_when_a_branch_is_selected(self, authed_client, branches):
        batangas = next(b for b in branches if b.code == "BATANGAS")
        response = authed_client.get(reverse("accounts:admin_dashboard"), {"branch": batangas.pk})
        assert "Branch Performance Overview" not in response.content.decode()


class TestResourceMonitoringShowsRawMaterials:
    """Item 2: Resource Monitoring should track raw materials, not
    finished/premade products, and should read from the commissary
    (materials are never branch-specific)."""

    @pytest.fixture
    def commissary(self):
        return Branch.objects.create(name="Commissary", code="COMMISSARY", is_commissary=True)

    def test_material_inventory_is_listed(self, authed_client, commissary):
        flour = Product.objects.create(
            name="Flour (kg)", price="55.00", product_type=Product.ProductType.MATERIAL
        )
        Inventory.objects.create(branch=commissary, product=flour, quantity_on_hand=20)
        response = authed_client.get(reverse("accounts:admin_dashboard"))
        content = response.content.decode()
        assert "Resource Monitoring" in content
        assert "Flour (kg)" in content

    def test_finished_products_are_not_listed_in_resource_monitoring(self, authed_client, branches):
        """A finished/sellable product's inventory at a real branch must
        not appear in the materials-only Resource Monitoring list."""
        batangas = next(b for b in branches if b.code == "BATANGAS")
        cake = Product.objects.create(name="Chocolate Cake", price="140.00")
        Inventory.objects.create(branch=batangas, product=cake, quantity_on_hand=5)

        response = authed_client.get(reverse("accounts:admin_dashboard"))
        content = response.content.decode()
        # The card renders (possibly with "no inventory tracked" copy
        # since no commissary/material exists in this test), but the
        # finished product name must not leak into it.
        assert "Chocolate Cake" not in content.split("Low Stock Orders Tracking")[0]

    def test_no_commissary_configured_shows_friendly_message_not_error(self, authed_client):
        response = authed_client.get(reverse("accounts:admin_dashboard"))
        assert response.status_code == 200
        assert "No commissary branch configured yet." in response.content.decode()


class TestTopProductsPercentages:
    """Item 3: the Top Products pie chart data must carry each product's
    percentage share of total units sold, and the shown slices
    (including a derived "Others" slice for whatever isn't in the
    top-N) must add up to ~100%."""

    def test_top_products_json_includes_percentages(self, authed_client, branches, monkeypatch):
        from apps.analytics import services as analytics_services

        monkeypatch.setattr(
            analytics_services,
            "get_top_products",
            lambda branch=None: [
                {"product__name": "Latte", "units_sold": 60},
                {"product__name": "Cake", "units_sold": 40},
            ],
        )
        monkeypatch.setattr(analytics_services, "get_total_units_sold", lambda branch=None: 100)

        response = authed_client.get(reverse("accounts:admin_dashboard"))
        content = response.content.decode()
        assert '"pct": 60' in content
        assert '"pct": 40' in content

    def test_others_slice_absorbs_the_remainder(self, authed_client, branches, monkeypatch):
        from apps.analytics import services as analytics_services

        monkeypatch.setattr(
            analytics_services,
            "get_top_products",
            lambda branch=None: [{"product__name": "Latte", "units_sold": 60}],
        )
        monkeypatch.setattr(analytics_services, "get_total_units_sold", lambda branch=None: 100)

        response = authed_client.get(reverse("accounts:admin_dashboard"))
        content = response.content.decode()
        assert '"name": "Others"' in content
        assert '"pct": 40' in content

    def test_zero_total_units_sold_does_not_crash(self, authed_client, branches, monkeypatch):
        from apps.analytics import services as analytics_services

        monkeypatch.setattr(analytics_services, "get_top_products", lambda branch=None: [])
        monkeypatch.setattr(analytics_services, "get_total_units_sold", lambda branch=None: 0)

        response = authed_client.get(reverse("accounts:admin_dashboard"))
        assert response.status_code == 200
        assert "var topProducts = []" in response.content.decode()


class TestLowStockOrdersTracking:
    """Item 4: a new section tracking raw materials at/below their
    minimum stock level, reusing the existing HIGH/MEDIUM material risk
    alerts rather than fabricating any order/PO status."""

    def test_section_present_and_empty_by_default(self, authed_client, branches):
        response = authed_client.get(reverse("accounts:admin_dashboard"))
        content = response.content.decode()
        assert "Low Stock Orders Tracking" in content
        assert "No raw materials are currently low on stock." in content

    def test_high_risk_material_appears_with_recommended_reorder_quantity(self, authed_client):
        from apps.forecasting.models import InventoryRiskScore

        commissary = Branch.objects.create(name="Commissary", code="COMMISSARY", is_commissary=True)
        flour = Product.objects.create(
            name="Flour (kg)", price="55.00", product_type=Product.ProductType.MATERIAL
        )
        InventoryRiskScore.objects.create(
            branch=commissary,
            product=flour,
            risk_level="HIGH",
            quantity_on_hand=0,
            avg_daily_demand=5.0,
            days_of_stock_left=0.0,
        )

        response = authed_client.get(reverse("accounts:admin_dashboard"))
        content = response.content.decode()
        assert "Flour (kg)" in content.split("Low Stock Orders Tracking")[1]
        assert "recommend ordering +" in content
        # No fabricated order/purchase-order status column exists.
        assert "Order #" not in content
        assert "PO-" not in content
