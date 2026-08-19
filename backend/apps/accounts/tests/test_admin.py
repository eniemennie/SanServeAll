"""
Tests confirming the Django admin (Row 12) actually works end-to-end for
accounts models -- not just that admin.py registers them.
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounts.models import Branch

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def admin_client(client):
    admin = User.objects.create_superuser(
        username="admin_test", email="admin@example.com", password="testpass123"
    )
    client.force_login(admin)
    return client


class TestAccountsAdminPagesRender:
    def test_admin_index_loads(self, admin_client):
        response = admin_client.get(reverse("admin:index"))
        assert response.status_code == 200

    def test_branch_changelist_loads(self, admin_client):
        response = admin_client.get(reverse("admin:accounts_branch_changelist"))
        assert response.status_code == 200

    def test_role_changelist_loads(self, admin_client):
        response = admin_client.get(reverse("admin:accounts_role_changelist"))
        assert response.status_code == 200

    def test_user_changelist_loads(self, admin_client):
        response = admin_client.get(reverse("admin:accounts_user_changelist"))
        assert response.status_code == 200

    def test_cashierpin_changelist_loads(self, admin_client):
        response = admin_client.get(reverse("admin:accounts_cashierpin_changelist"))
        assert response.status_code == 200

    def test_seeded_branch_data_appears_in_admin_list(self, admin_client):
        Branch.objects.create(name="Alangilan", code="ALANGILAN", is_kahero_branch=True)
        response = admin_client.get(reverse("admin:accounts_branch_changelist"))
        assert b"Alangilan" in response.content
