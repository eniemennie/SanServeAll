"""
Tests for the Sales History view (backend gap assessment: "add a
filterable sales-history view (branch/cashier/date)").
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Branch, Role, User
from apps.analytics import services
from apps.inventory.models import Product
from apps.pos.models import SalesItem, SalesTransaction
from conftest import verify_otp_for_client

pytestmark = pytest.mark.django_db


@pytest.fixture
def branches():
    return {
        "batangas": Branch.objects.create(name="Batangas City", code="BATANGAS"),
        "lipa": Branch.objects.create(name="Lipa City", code="LIPA"),
    }


@pytest.fixture
def cashiers(branches):
    role = Role.objects.create(name=Role.BRANCH_STAFF)
    return {
        "alex": User.objects.create_user(
            username="alex", password="x", role=role, branch=branches["batangas"]
        ),
        "bea": User.objects.create_user(
            username="bea", password="x", role=role, branch=branches["lipa"]
        ),
    }


@pytest.fixture
def owner():
    role = Role.objects.create(name=Role.OWNER_ADMIN)
    return User.objects.create_user(username="owner1", password="testpass123", role=role)


@pytest.fixture
def owner_client(client, owner):
    client.force_login(owner)
    verify_otp_for_client(client, owner)
    return client


@pytest.fixture
def latte():
    return Product.objects.create(name="Spanish Latte", price="125.00")


def _sale(branch, cashier, product, status=SalesTransaction.Status.COMPLETED, when=None):
    transaction = SalesTransaction.objects.create(
        branch=branch,
        cashier=cashier,
        status=status,
        payment_method="CASH",
        completed_at=when or timezone.now(),
    )
    SalesItem.objects.create(
        transaction=transaction, product=product, unit_price="125.00", quantity=1
    )
    return transaction


class TestGetSalesHistoryService:
    def test_only_completed_transactions_are_included(self, branches, cashiers, latte):
        completed = _sale(branches["batangas"], cashiers["alex"], latte)
        _sale(branches["batangas"], cashiers["alex"], latte, status=SalesTransaction.Status.DRAFT)
        _sale(branches["batangas"], cashiers["alex"], latte, status=SalesTransaction.Status.VOIDED)

        results = list(services.get_sales_history())
        assert results == [completed]

    def test_filters_by_branch(self, branches, cashiers, latte):
        in_batangas = _sale(branches["batangas"], cashiers["alex"], latte)
        _sale(branches["lipa"], cashiers["bea"], latte)

        results = list(services.get_sales_history(branch=branches["batangas"]))
        assert results == [in_batangas]

    def test_filters_by_cashier(self, branches, cashiers, latte):
        alex_sale = _sale(branches["batangas"], cashiers["alex"], latte)
        _sale(branches["lipa"], cashiers["bea"], latte)

        results = list(services.get_sales_history(cashier_id=cashiers["alex"].pk))
        assert results == [alex_sale]

    def test_filters_by_date_range_inclusive(self, branches, cashiers, latte):
        today = timezone.now()
        yesterday_sale = _sale(
            branches["batangas"], cashiers["alex"], latte, when=today - timedelta(days=1)
        )
        today_sale = _sale(branches["batangas"], cashiers["alex"], latte, when=today)
        _sale(branches["batangas"], cashiers["alex"], latte, when=today - timedelta(days=5))

        results = services.get_sales_history(
            date_from=(today - timedelta(days=1)).date(), date_to=today.date()
        )
        assert set(results) == {yesterday_sale, today_sale}


class TestGetCashiersWithSalesHistoryService:
    def test_only_includes_cashiers_who_have_a_completed_sale(self, branches, cashiers, latte):
        _sale(branches["batangas"], cashiers["alex"], latte)
        # bea has never completed a sale
        names = list(services.get_cashiers_with_sales_history().values_list("username", flat=True))
        assert names == ["alex"]

    def test_includes_a_now_inactive_cashier_who_has_a_past_sale(self, branches, cashiers, latte):
        _sale(branches["batangas"], cashiers["alex"], latte)
        cashiers["alex"].is_active = False
        cashiers["alex"].save()

        names = list(services.get_cashiers_with_sales_history().values_list("username", flat=True))
        assert names == ["alex"]


class TestSalesHistoryView:
    def test_owner_can_view_the_page(self, owner_client, branches, cashiers, latte):
        _sale(branches["batangas"], cashiers["alex"], latte)
        response = owner_client.get(reverse("analytics:sales_history"))
        assert response.status_code == 200
        assert b"Batangas City" in response.content

    def test_non_owner_is_denied(self, client, cashiers):
        client.force_login(cashiers["alex"])
        response = client.get(reverse("analytics:sales_history"))
        assert response.status_code == 403

    def test_branch_filter_narrows_results(self, owner_client, branches, cashiers, latte):
        in_lipa = _sale(branches["lipa"], cashiers["bea"], latte)
        _sale(branches["batangas"], cashiers["alex"], latte)

        response = owner_client.get(
            reverse("analytics:sales_history"), {"branch": branches["lipa"].pk}
        )
        assert response.status_code == 200
        assert list(response.context["transactions"]) == [in_lipa]

    def test_invalid_date_shows_error_and_no_results(self, owner_client, branches, cashiers, latte):
        _sale(branches["batangas"], cashiers["alex"], latte)
        response = owner_client.get(reverse("analytics:sales_history"), {"date_from": "not-a-date"})
        assert response.status_code == 200
        assert b"YYYY-MM-DD" in response.content
        assert list(response.context["transactions"]) == []

    def test_from_date_after_to_date_shows_error(self, owner_client):
        response = owner_client.get(
            reverse("analytics:sales_history"),
            {"date_from": "2026-02-01", "date_to": "2026-01-01"},
        )
        assert response.status_code == 200
        assert b"on or before" in response.content
