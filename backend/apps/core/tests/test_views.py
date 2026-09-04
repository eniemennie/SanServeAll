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

    def test_start_shift_links_to_cashier_login(self, client):
        response = client.get(reverse("core:landing"))
        assert reverse("accounts:login").encode() in response.content

    def test_sign_in_links_to_admin_login(self, client):
        response = client.get(reverse("core:landing"))
        assert reverse("accounts:admin_login").encode() in response.content

    def test_does_not_require_authentication(self, client):
        """The landing page is the public entry point -- it must be
        reachable by a completely anonymous visitor, unlike every other
        screen in the app. A 200 (not a redirect to a login page) is
        the actual proof of that."""
        response = client.get(reverse("core:landing"))
        assert response.status_code == 200
