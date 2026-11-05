"""Tests for the `peso`/`comma2`/`qty` template filters (QA fix,
adviser dry-run 2026-11-05 -- Issue #2: Currency and Number Formatting).

Covers the filters directly (unit-level) and, via a real rendered page,
the two concrete symptoms the dry-run reported: a large peso amount
missing its thousands separator, and a piece-based quantity showing
three decimal places instead of a whole number.
"""

from decimal import Decimal

import pytest
from django.template import Context, Template
from django.utils import timezone

from apps.accounts.models import Branch, Role, User
from apps.core.templatetags.sanserveall_filters import dadd, peso, qty
from apps.inventory.models import Inventory, Product
from apps.pos.models import SalesItem, SalesTransaction
from conftest import verify_otp_for_client

pytestmark = pytest.mark.django_db


class TestPesoFilter:
    def test_adds_thousands_separator_and_two_decimals(self):
        assert peso(Decimal("1210142.87")) == "1,210,142.87"

    def test_small_amount_still_gets_two_decimals(self):
        assert peso(Decimal("415")) == "415.00"

    def test_rounds_to_two_decimal_places(self):
        assert peso(Decimal("1210142.876")) == "1,210,142.88"

    def test_non_numeric_input_is_returned_unchanged_rather_than_raising(self):
        assert peso("N/A") == "N/A"


class TestQtyFilter:
    def test_piece_unit_renders_as_a_whole_number(self):
        assert qty(Decimal("54.000"), "PIECE") == "54"

    def test_large_piece_count_still_gets_a_thousands_separator(self):
        assert qty(Decimal("1200.000"), "PACK") == "1,200"

    def test_kilogram_unit_keeps_two_decimal_places(self):
        assert qty(Decimal("2.500"), "KILOGRAM") == "2.50"

    def test_liter_unit_keeps_two_decimal_places(self):
        assert qty(Decimal("0.000"), "LITER") == "0.00"

    def test_unit_lookup_is_case_insensitive(self):
        assert qty(Decimal("3.000"), "piece") == "3"


class TestDaddFilter:
    """`dadd` exists because Django's built-in `add` filter tries
    `int(value) + int(arg)` first, which silently truncates two
    Decimals with cents to whole pesos before summing -- found while
    wiring up the POS discount-total line (pos_ordering.html), which
    chains `|dadd:...|peso`."""

    def test_adds_two_decimals_without_losing_cents(self):
        assert dadd(Decimal("1000000.00"), Decimal("210142.87")) == Decimal("1210142.87")

    def test_djangos_builtin_add_filter_would_have_truncated_this(self):
        """Documents the bug `dadd` avoids -- not asserting on our own
        code, but pinning Django's actual `add` behavior so a future
        Django upgrade that changes it doesn't silently reopen the
        truncation."""
        from django.template.defaultfilters import add as django_add

        assert django_add(Decimal("1000000.00"), Decimal("210142.87")) == 1210142

    def test_dadd_then_peso_on_two_decimals(self):
        template = Template("{% load sanserveall_filters %}{{ a|dadd:b|peso }}")
        rendered = template.render(Context({"a": Decimal("1000000.00"), "b": Decimal("210142.87")}))
        assert rendered == "1,210,142.87"


class TestDashboardRevenueFormatting:
    """Reproduces the dry-run's literal example: a branch revenue large
    enough to need a thousands separator must render as
    "1,210,142.87", not "1210142.87"."""

    def test_large_branch_revenue_renders_with_thousands_separator(self, client):
        role = Role.objects.create(name=Role.OWNER_ADMIN)
        owner = User.objects.create_user(username="owner1", password="testpass123", role=role)
        client.force_login(owner)
        verify_otp_for_client(client, owner)

        branch = Branch.objects.create(name="Alangilan", code="ALANGILAN")
        cashier_role = Role.objects.create(name=Role.BRANCH_STAFF)
        cashier = User.objects.create_user(
            username="cashier1", password="testpass123", role=cashier_role, branch=branch
        )
        product = Product.objects.create(name="Bulk Order", price="1210142.87")

        txn = SalesTransaction.objects.create(
            branch=branch,
            cashier=cashier,
            status=SalesTransaction.Status.COMPLETED,
            completed_at=timezone.now(),
        )
        SalesItem.objects.create(
            transaction=txn, product=product, unit_price="1210142.87", quantity=1
        )

        response = client.get("/accounts/admin/dashboard/")
        assert response.status_code == 200
        content = response.content.decode()
        # The Branch Performance card's Revenue line must show the
        # thousands separator. (The page's Chart.js data block also
        # contains the raw, comma-free "1210142.87" as JSON for the
        # chart -- that's correct JS data, not a display string, so
        # this check is scoped to the Revenue line itself rather than
        # asserting the raw form is absent from the whole page.)
        assert "1,210,142.87" in content
        revenue_idx = content.find("<span>Revenue</span>")
        revenue_line = content[revenue_idx : revenue_idx + 120]  # noqa: E203
        assert "1210142.87" not in revenue_line


class TestProductAvailabilityQuantityFormatting:
    """Reproduces the dry-run's literal example: a piece-based item with
    54 units in stock must render as "54 pc", not "54.000 pc"."""

    def test_piece_based_stock_shows_a_whole_number(self, client):
        role = Role.objects.create(name=Role.OWNER_ADMIN)
        owner = User.objects.create_user(username="owner1", password="testpass123", role=role)
        client.force_login(owner)
        verify_otp_for_client(client, owner)

        branch = Branch.objects.create(name="Lipa City", code="LIPA")
        product = Product.objects.create(
            name="Paper Cup", price="5.00", unit_of_measure=Product.UnitOfMeasure.PIECE
        )
        Inventory.objects.create(branch=branch, product=product, quantity_on_hand=54)

        response = client.get("/inventory/products/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "54 pc" in content
        assert "54.000 pc" not in content
