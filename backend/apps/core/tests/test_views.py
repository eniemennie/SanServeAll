"""
Tests for the public landing page (UI-matching Step 2).
"""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


class TestLandingPage:
    def test_landing_page_loads(self, client):
        response = client.get(reverse("core:landing"))
        assert response.status_code == 200

    def test_root_url_serves_the_landing_page(self, client):
        response = client.get("/")
        assert response.status_code == 200

    def test_shows_the_business_name_and_tagline(self, client):
        response = client.get(reverse("core:landing"))
        assert b"Jorge" in response.content
        assert b"artisanal coffee" in response.content

    def test_renders_the_real_logo_image_not_placeholder_text(self, client):
        """Confirms the actual logo.png asset is used -- not the earlier
        styled-text approximation this replaced."""
        response = client.get(reverse("core:landing"))
        assert b"images/logo.png" in response.content
        assert b'alt="Jorge' in response.content

    def test_cashier_button_links_to_cashier_selection(self, client):
        """The Cashier role button leads to the tap-to-select cashier flow
        (Row 3 redesign), not the plain username/password login -- this
        button replaced the old "Start Shift" label (Row 19), same target."""
        response = client.get(reverse("core:landing"))
        assert reverse("accounts:select_cashier").encode() in response.content

    def test_admin_owner_button_links_to_admin_login(self, client):
        """Replaced the old "Sign In" label (Row 19), same target."""
        response = client.get(reverse("core:landing"))
        assert reverse("accounts:admin_login").encode() in response.content

    def test_commissary_button_links_to_commissary_login(self, client):
        """Commissary Staff gets its own branded login screen (styled like
        Admin/Owner's but username/password-based) rather than the bare
        generic accounts:login screen -- BranchSelectionView's existing
        role-based routing (not this button) is still what sends a
        Commissary Staff user on to production:dashboard after that."""
        response = client.get(reverse("core:landing"))
        assert reverse("accounts:commissary_login").encode() in response.content

    def test_purchasing_button_links_to_purchasing_login(self, client):
        """Purchasing Staff (Task #143) gets its own branded login screen,
        the same way Commissary Staff does -- see
        PurchasingLoginView's docstring for why the landing page needed
        this button at all."""
        response = client.get(reverse("core:landing"))
        assert reverse("accounts:purchasing_login").encode() in response.content

    def test_shows_all_four_role_labels(self, client):
        response = client.get(reverse("core:landing"))
        assert b"Cashier" in response.content
        assert b"Admin" in response.content
        assert b"Commissary" in response.content
        assert b"Purchasing" in response.content

    def test_does_not_require_authentication(self, client):
        """The landing page is the public entry point -- it must be
        reachable by a completely anonymous visitor, unlike every other
        screen in the app. A 200 (not a redirect to a login page) is
        the actual proof of that."""
        response = client.get(reverse("core:landing"))
        assert response.status_code == 200


class TestRoleButtonLayout:
    """Regression test for the 2026-11-08 role-button layout task: the
    four role buttons used a flex-wrap row (flex: 1 1 160px, max-width
    220px) that fit only 3 per line inside the hero's 640px container,
    leaving the 4th button alone on its own row, narrower than its
    siblings -- "one button awkwardly positioned on a separate row".
    Reworked to a 2x2 CSS grid, which is a complete shape for exactly
    4 items at every non-mobile width (never a partial row), collapsing
    to one column on phones where that's the natural layout. This only
    checks the fix's CSS is present -- Django's test client can't
    execute real browser layout -- so it guards against someone
    reverting to the old flex-wrap approach by accident."""

    def test_role_grid_uses_a_2x2_css_grid_not_flex_wrap(self, client):
        response = client.get(reverse("core:landing"))
        content = response.content.decode()

        assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in content
        assert "flex-wrap: wrap;" not in content

    def test_role_grid_collapses_to_one_column_on_phones(self, client):
        response = client.get(reverse("core:landing"))
        content = response.content.decode()

        assert "@media (max-width: 575.98px)" in content
        mobile_block = content.split("@media (max-width: 575.98px)", 1)[1]
        assert ".jc-role-grid" in mobile_block
        assert "grid-template-columns: 1fr;" in mobile_block

    def test_each_role_gets_a_distinct_brand_color(self, client):
        """Req. 3 ("distinguish each account type"): four different
        classes, not an alternating 2-color pattern repeated twice."""
        response = client.get(reverse("core:landing"))
        content = response.content.decode()

        assert 'class="jc-role-btn jc-role-btn-orange"' in content
        assert 'class="jc-role-btn jc-role-btn-navy"' in content
        assert 'class="jc-role-btn jc-role-btn-sage"' in content
        assert 'class="jc-role-btn jc-role-btn-teal"' in content
