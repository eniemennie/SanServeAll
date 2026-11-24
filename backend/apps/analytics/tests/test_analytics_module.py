"""
Tests for Week 9: Sales/Analytics Dashboard (Row 9.1), Product
Performance (Row 9.2), Resource Consumption & Operational Performance
(Row 9.3).
"""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Branch, Role, User
from apps.analytics import services
from apps.analytics import views as analytics_views
from apps.inventory.models import Product
from conftest import verify_otp_for_client
from apps.pos.models import SalesItem, SalesTransaction
from apps.production.models import IngredientUsage, ProductionRecord

pytestmark = pytest.mark.django_db


@pytest.fixture
def branch():
    return Branch.objects.create(name="Lipa City", code="LIPA")


@pytest.fixture
def cashier(branch):
    role = Role.objects.create(name=Role.BRANCH_STAFF)
    return User.objects.create_user(
        username="cashier1", password="testpass123", role=role, branch=branch
    )


@pytest.fixture
def owner(branch):
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


def _completed_sale(branch, cashier, product, quantity, unit_price, amount_tendered, when=None):
    """Creates a real COMPLETED sale with a deliberately different
    amount_tendered than the actual total -- this is the key setup for
    proving the revenue calculation ignores amount_tendered."""
    transaction = SalesTransaction.objects.create(
        branch=branch,
        cashier=cashier,
        status=SalesTransaction.Status.COMPLETED,
        payment_method="CASH",
        amount_tendered=amount_tendered,
        completed_at=when or timezone.now(),
    )
    SalesItem.objects.create(
        transaction=transaction, product=product, unit_price=unit_price, quantity=quantity
    )
    return transaction


def _completed_sale_with_discount(
    branch,
    cashier,
    product,
    quantity,
    unit_price,
    discount_category,
    discount_type,
    discount_amount,
    when=None,
):
    """Creates a COMPLETED sale with a whole-order discount applied,
    amount_tendered set to the actual (post-discount) grand_total --
    the Activity 2 regression scenario (Oct 2026): a discounted sale
    whose dashboard revenue should reflect what was actually collected,
    not the pre-discount subtotal."""
    transaction = SalesTransaction.objects.create(
        branch=branch,
        cashier=cashier,
        status=SalesTransaction.Status.COMPLETED,
        payment_method="CASH",
        transaction_discount_category=discount_category,
        transaction_discount_type=discount_type,
        transaction_discount_amount=Decimal(discount_amount),
        completed_at=when or timezone.now(),
    )
    SalesItem.objects.create(
        transaction=transaction, product=product, unit_price=unit_price, quantity=quantity
    )
    transaction.refresh_from_db()
    transaction.amount_tendered = transaction.grand_total
    transaction.save()
    return transaction


class TestSalesSummary:
    def test_revenue_uses_sale_total_not_amount_tendered(self, branch, cashier, latte):
        """The critical correctness test: a Php375 sale paid with a
        Php500 bill must report Php375 revenue, not Php500."""
        _completed_sale(
            branch, cashier, latte, quantity=3, unit_price="125.00", amount_tendered="500.00"
        )

        summary = services.get_sales_summary(branch=branch)
        assert summary["total_revenue"] == pytest.approx(375.00)

    def test_draft_transactions_are_excluded(self, branch, cashier, latte):
        SalesTransaction.objects.create(
            branch=branch, cashier=cashier, status=SalesTransaction.Status.DRAFT
        )
        summary = services.get_sales_summary(branch=branch)
        assert summary["total_transactions"] == 0

    def test_old_sales_outside_the_branchs_own_window_are_excluded(self, branch, cashier, latte):
        """The window is anchored to the branch's own latest activity
        (see _trailing_window_anchor), not a sale being "old" in
        isolation -- so this needs a RECENT sale establishing that
        anchor, with a separate sale old enough to fall outside the
        30-day window measured back from it."""
        now = timezone.now()
        _completed_sale(
            branch,
            cashier,
            latte,
            quantity=1,
            unit_price="125.00",
            amount_tendered="125.00",
            when=now,
        )
        _completed_sale(
            branch,
            cashier,
            latte,
            quantity=1,
            unit_price="999.00",
            amount_tendered="999.00",
            when=now - timedelta(days=60),
        )
        summary = services.get_sales_summary(branch=branch, days=30)
        assert summary["total_revenue"] == pytest.approx(125.00)

    def test_a_branchs_only_sale_is_not_hidden_just_for_being_old(self, branch, cashier, latte):
        """Task #136 (client review, 2026-11-04): a branch whose only
        data comes from periodic batch uploads (not live POS) must still
        show its real most recent activity even when wall-clock "now"
        has drifted more than `days` past it -- it must never go blank
        just because nothing new has been uploaded recently. See
        _trailing_window_anchor's docstring for the full reasoning
        (this is the Alangilan branch's exact real-world scenario)."""
        old_date = timezone.now() - timedelta(days=60)
        _completed_sale(
            branch,
            cashier,
            latte,
            quantity=1,
            unit_price="125.00",
            amount_tendered="125.00",
            when=old_date,
        )
        summary = services.get_sales_summary(branch=branch, days=30)
        assert summary["total_revenue"] == pytest.approx(125.00)

    def test_branch_filter_only_counts_that_branch(self, branch, cashier, latte):
        other_branch = Branch.objects.create(name="Batangas City", code="BATANGAS")
        other_cashier = User.objects.create_user(
            username="cashier2", password="testpass123", role=cashier.role, branch=other_branch
        )
        _completed_sale(branch, cashier, latte, 1, "125.00", "125.00")
        _completed_sale(other_branch, other_cashier, latte, 1, "125.00", "125.00")

        summary = services.get_sales_summary(branch=branch)
        assert summary["total_transactions"] == 1

    def test_units_sold_counts_quantity_not_transaction_count(self, branch, cashier, latte):
        _completed_sale(
            branch, cashier, latte, quantity=5, unit_price="125.00", amount_tendered="700.00"
        )
        summary = services.get_sales_summary(branch=branch)
        assert summary["total_units_sold"] == 5
        assert summary["total_transactions"] == 1

    def test_revenue_reflects_transaction_wide_discount(self, branch, cashier, latte):
        """Activity 2 regression scenario (Oct 2026), now fixed: a
        Php500 subtotal with a 20% whole-order discount actually
        collects Php400 -- the dashboard must show Php400, not the
        pre-discount Php500 it showed before this fix."""
        _completed_sale_with_discount(
            branch,
            cashier,
            latte,
            quantity=4,
            unit_price="125.00",
            discount_category="SC2",
            discount_type="PERCENT",
            discount_amount="20",
        )

        summary = services.get_sales_summary(branch=branch)
        assert summary["total_revenue"] == pytest.approx(400.00)

    def test_sale_with_no_discount_still_reports_full_subtotal(self, branch, cashier, latte):
        """Guards against an over-correction -- a sale with no
        transaction-wide discount must still report its full subtotal,
        not accidentally zero it out."""
        _completed_sale(
            branch, cashier, latte, quantity=2, unit_price="125.00", amount_tendered="250.00"
        )
        summary = services.get_sales_summary(branch=branch)
        assert summary["total_revenue"] == pytest.approx(250.00)


class TestWeeklySalesTrend:
    def test_returns_requested_number_of_weeks(self, branch, cashier, latte):
        trend = services.get_weekly_sales_trend(branch=branch, weeks=8)
        assert len(trend) == 8

    def test_old_batch_only_data_still_appears_in_the_chart(self, branch, cashier, latte):
        """Task #136: a branch whose only data is older than the chart's
        window (same Alangilan scenario as get_branch_performance) must
        still show its real last weeks of activity on the Sales
        Analytics trend chart, anchored to its own latest transaction
        rather than going entirely blank."""
        old_date = timezone.now() - timedelta(days=60)
        _completed_sale(
            branch,
            cashier,
            latte,
            quantity=2,
            unit_price="125.00",
            amount_tendered="250.00",
            when=old_date,
        )
        trend = services.get_weekly_sales_trend(branch=branch, weeks=8)
        assert sum(week["revenue"] for week in trend) == pytest.approx(250.00)

    def test_trend_also_uses_sale_total_not_amount_tendered(self, branch, cashier, latte):
        _completed_sale(
            branch, cashier, latte, quantity=2, unit_price="125.00", amount_tendered="1000.00"
        )
        trend = services.get_weekly_sales_trend(branch=branch, weeks=1)
        assert trend[0]["revenue"] == pytest.approx(250.00)

    def test_trend_reflects_transaction_wide_discount(self, branch, cashier, latte):
        _completed_sale_with_discount(
            branch,
            cashier,
            latte,
            quantity=4,
            unit_price="125.00",
            discount_category="SC2",
            discount_type="PERCENT",
            discount_amount="20",
        )
        trend = services.get_weekly_sales_trend(branch=branch, weeks=1)
        assert trend[0]["revenue"] == pytest.approx(400.00)


class TestSalesTrend:
    """get_sales_trend: the Reports Hub's own date-range-aware trend
    chart data, distinct from the fixed-8-weeks get_weekly_sales_trend
    (still used by the separate Sales Analytics Dashboard)."""

    def test_short_range_buckets_by_day(self, branch, cashier, latte):
        _completed_sale(
            branch,
            cashier,
            latte,
            quantity=2,
            unit_price="125.00",
            amount_tendered="250.00",
            when=timezone.make_aware(datetime(2026, 11, 6, 10, 0)),
        )
        trend = services.get_sales_trend(
            branch=branch, date_from=date(2026, 11, 6), date_to=date(2026, 11, 7)
        )
        assert trend["granularity"] == "day"
        assert len(trend["points"]) == 2
        assert trend["points"][0]["revenue"] == pytest.approx(250.00)
        assert trend["points"][1]["revenue"] == pytest.approx(0.0)

    def test_medium_range_buckets_by_week(self, branch, cashier, latte):
        _completed_sale(
            branch,
            cashier,
            latte,
            quantity=1,
            unit_price="125.00",
            amount_tendered="125.00",
            when=timezone.make_aware(datetime(2026, 9, 1, 10, 0)),
        )
        trend = services.get_sales_trend(
            branch=branch, date_from=date(2026, 8, 1), date_to=date(2026, 10, 15)
        )
        assert trend["granularity"] == "week"
        assert sum(p["revenue"] for p in trend["points"]) == pytest.approx(125.00)
        # ~76 days / 7 => around 11 weekly points, nowhere near "hundreds"
        assert 9 <= len(trend["points"]) <= 13

    def test_long_range_buckets_by_month(self, branch, cashier, latte):
        _completed_sale(
            branch,
            cashier,
            latte,
            quantity=1,
            unit_price="125.00",
            amount_tendered="125.00",
            when=timezone.make_aware(datetime(2024, 6, 15, 10, 0)),
        )
        trend = services.get_sales_trend(
            branch=branch, date_from=date(2024, 1, 1), date_to=date(2025, 12, 31)
        )
        assert trend["granularity"] == "month"
        assert len(trend["points"]) == 24
        assert sum(p["revenue"] for p in trend["points"]) == pytest.approx(125.00)
        assert trend["points"][5]["revenue"] == pytest.approx(125.00)  # June 2024

    def test_zero_revenue_buckets_are_included_not_skipped(self, branch, cashier, latte):
        trend = services.get_sales_trend(
            branch=branch, date_from=date(2026, 11, 1), date_to=date(2026, 11, 5)
        )
        assert trend["granularity"] == "day"
        assert len(trend["points"]) == 5
        assert all(p["revenue"] == 0.0 for p in trend["points"])

    def test_discount_aware_revenue_same_as_other_reports(self, branch, cashier, latte):
        _completed_sale_with_discount(
            branch,
            cashier,
            latte,
            quantity=4,
            unit_price="125.00",
            discount_category="SC2",
            discount_type="PERCENT",
            discount_amount="20",
            when=timezone.make_aware(datetime(2026, 11, 6, 10, 0)),
        )
        trend = services.get_sales_trend(
            branch=branch, date_from=date(2026, 11, 6), date_to=date(2026, 11, 6)
        )
        assert trend["points"][0]["revenue"] == pytest.approx(400.00)

    def test_trailing_days_fallback_still_works_without_explicit_dates(
        self, branch, cashier, latte
    ):
        """No date_from/date_to given (legacy `days` link) -- same
        trailing-window shape get_sales_summary already uses."""
        _completed_sale(
            branch, cashier, latte, quantity=1, unit_price="125.00", amount_tendered="125.00"
        )
        trend = services.get_sales_trend(branch=branch, days=7)
        assert trend["granularity"] == "day"
        assert sum(p["revenue"] for p in trend["points"]) == pytest.approx(125.00)


class TestTopProducts:
    def test_orders_by_units_sold_descending(self, branch, cashier, latte):
        cake = Product.objects.create(name="Chocolate Cake", price="140.00")
        _completed_sale(
            branch, cashier, latte, quantity=2, unit_price="125.00", amount_tendered="250.00"
        )
        _completed_sale(
            branch, cashier, cake, quantity=10, unit_price="140.00", amount_tendered="1400.00"
        )

        top = list(services.get_top_products(branch=branch))
        assert top[0]["product__name"] == "Chocolate Cake"
        assert top[0]["units_sold"] == 10


class TestProductPerformance:
    def test_growth_rate_calculated_correctly(self, branch, cashier, latte):
        now = timezone.now()
        # Previous period: 10 units. Current period: 20 units. +100% growth.
        _completed_sale(
            branch, cashier, latte, 10, "125.00", "1250.00", when=now - timedelta(days=45)
        )
        _completed_sale(
            branch, cashier, latte, 20, "125.00", "2500.00", when=now - timedelta(days=5)
        )

        performance = services.get_product_performance(days=30)
        row = next(r for r in performance if r["product_name"] == "Spanish Latte")
        assert row["current_units"] == 20
        assert row["previous_units"] == 10
        assert row["growth_rate"] == pytest.approx(100.0)

    def test_new_product_this_period_has_no_growth_rate(self, branch, cashier, latte):
        _completed_sale(branch, cashier, latte, 5, "125.00", "625.00", when=timezone.now())
        performance = services.get_product_performance(days=30)
        row = next(r for r in performance if r["product_name"] == "Spanish Latte")
        assert row["previous_units"] == 0
        assert row["growth_rate"] is None

    def test_product_with_no_sales_in_either_window_is_omitted(self, branch, cashier):
        Product.objects.create(name="Unsold Item", price="99.00")
        performance = services.get_product_performance(days=30)
        names = [r["product_name"] for r in performance]
        assert "Unsold Item" not in names

    def test_imported_historical_rows_with_no_product_do_not_collapse_into_one_phantom_row(
        self, branch, cashier, latte
    ):
        """Task #142 (client review): import_branch_sales_history
        creates SalesItem rows with product=None and a custom_name like
        "Store Sales (Imported)" -- one per revenue stream per day, not
        per actual product. Before excluding product__isnull=True,
        Django's .values("product__id", ...) grouped every single one
        of those rows into one product_id=None bucket, so this
        screen's "units_sold" for that phantom row silently summed the
        *count of imported transactions* across every branch and every
        revenue stream combined -- not a real product's performance at
        all."""
        other_branch = Branch.objects.create(name="Batangas City", code="BATANGAS")
        for imported_branch, stream in [
            (branch, "Store Sales"),
            (branch, "Cafe AM Sales"),
            (other_branch, "Store Sales"),
        ]:
            tx = SalesTransaction.objects.create(
                branch=imported_branch,
                cashier=cashier,
                status=SalesTransaction.Status.COMPLETED,
                payment_method="CASH",
                amount_tendered="100.00",
                completed_at=timezone.now(),
            )
            SalesItem.objects.create(
                transaction=tx,
                product=None,
                custom_name=f"{stream} (Imported)",
                unit_price="100.00",
                quantity=1,
            )
        # A real catalog sale too, to confirm it's unaffected.
        _completed_sale(branch, cashier, latte, 3, "125.00", "375.00")

        performance = services.get_product_performance(days=30)

        assert None not in [r["product_name"] for r in performance]
        latte_row = next(r for r in performance if r["product_name"] == "Spanish Latte")
        assert latte_row["current_units"] == 3

    def test_branch_filter_excludes_other_branches(self, branch, cashier, latte):
        """Task #142: Reports Hub passes branch=selected_branch to
        get_sales_summary and get_weekly_sales_trend but was never
        wired through to this function -- so selecting a branch on
        that page silently left Product Performance showing every
        branch's sales regardless."""
        other_branch = Branch.objects.create(name="Batangas City", code="BATANGAS")
        other_cashier = User.objects.create_user(
            username="cashier2", password="testpass123", role=cashier.role, branch=other_branch
        )
        other_product = Product.objects.create(name="Batangas Special", price="110.00")
        _completed_sale(branch, cashier, latte, 5, "125.00", "625.00")
        _completed_sale(other_branch, other_cashier, other_product, 7, "110.00", "770.00")

        performance = services.get_product_performance(branch=branch, days=30)

        names = [r["product_name"] for r in performance]
        assert "Spanish Latte" in names
        assert "Batangas Special" not in names


class TestResourceConsumption:
    def test_totals_material_usage_and_cost(self, branch):
        Branch.objects.create(name="Commissary", code="COMMISSARY", is_commissary=True)
        role = Role.objects.create(name=Role.COMMISSARY_STAFF)
        staff = User.objects.create_user(username="commissary1", password="testpass123", role=role)
        flour = Product.objects.create(
            name="Flour (kg)", price="55.00", product_type=Product.ProductType.MATERIAL
        )
        cake = Product.objects.create(name="Cake", price="140.00")

        record = ProductionRecord.objects.create(
            commissary_staff=staff, product=cake, quantity_produced=10
        )
        IngredientUsage.objects.create(production_record=record, material=flour, quantity_used=5)

        summary = services.get_resource_consumption_summary(days=30)
        assert summary["units_used_by_unit"] == [
            {"unit": "PIECE", "unit_display": "pc", "total": 5}
        ]
        assert summary["total_cost"] == pytest.approx(275.00)  # 5 * 55.00
        assert summary["total_produced"] == 10
        assert summary["cost_per_unit_produced"] == pytest.approx(27.50)  # 275 / 10

    def test_units_used_are_broken_out_per_unit_not_summed_together(self, branch):
        """Task #163 (adviser dry-run, 2026-11-05): "Do not combine kg,
        L, and pc quantities into one total." Mixing a material measured
        in kg with one measured in pc into a single number produces a
        figure that doesn't represent any real quantity of anything."""
        role = Role.objects.create(name=Role.COMMISSARY_STAFF)
        staff = User.objects.create_user(username="commissary1", password="testpass123", role=role)
        flour = Product.objects.create(
            name="Flour",
            price="55.00",
            product_type=Product.ProductType.MATERIAL,
            unit_of_measure=Product.UnitOfMeasure.KILOGRAM,
        )
        cup_lids = Product.objects.create(
            name="Cup Lids",
            price="1.00",
            product_type=Product.ProductType.MATERIAL,
            unit_of_measure=Product.UnitOfMeasure.PIECE,
        )
        cake = Product.objects.create(name="Cake", price="140.00")
        record = ProductionRecord.objects.create(
            commissary_staff=staff, product=cake, quantity_produced=10
        )
        IngredientUsage.objects.create(production_record=record, material=flour, quantity_used=5)
        IngredientUsage.objects.create(
            production_record=record, material=cup_lids, quantity_used=300
        )

        summary = services.get_resource_consumption_summary(days=30)

        by_unit = {row["unit"]: row["total"] for row in summary["units_used_by_unit"]}
        assert by_unit == {"KILOGRAM": 5, "PIECE": 300}

        by_material = {row["material__name"]: row for row in summary["by_material"]}
        assert by_material["Flour"]["unit"] == "KILOGRAM"
        assert by_material["Flour"]["unit_display"] == "kg"
        assert by_material["Cup Lids"]["unit"] == "PIECE"
        assert by_material["Cup Lids"]["unit_display"] == "pc"


class TestOperationalPerformance:
    def test_completion_and_quality_rates(self):
        role = Role.objects.create(name=Role.COMMISSARY_STAFF)
        staff = User.objects.create_user(username="commissary1", password="testpass123", role=role)
        cake = Product.objects.create(name="Cake", price="140.00")

        ProductionRecord.objects.create(
            commissary_staff=staff,
            product=cake,
            quantity_produced=10,
            status=ProductionRecord.Status.COMPLETED,
            quality=ProductionRecord.Quality.PASS,
        )
        ProductionRecord.objects.create(
            commissary_staff=staff,
            product=cake,
            quantity_produced=5,
            status=ProductionRecord.Status.PENDING,
            quality=ProductionRecord.Quality.FAIL,
        )

        summary = services.get_operational_performance_summary(days=30)
        assert summary["total_batches"] == 2
        assert summary["completion_rate"] == pytest.approx(50.0)
        assert summary["quality_pass_rate"] == pytest.approx(50.0)

    def test_no_batches_returns_none_rates_not_zero(self):
        """None (not shown / "--") is more honest than 0% when there's
        simply no data yet -- 0% implies batches failed, not that none exist."""
        summary = services.get_operational_performance_summary(days=30)
        assert summary["total_batches"] == 0
        assert summary["completion_rate"] is None
        assert summary["quality_pass_rate"] is None


class TestSalesDashboardView:
    def test_owner_can_view_dashboard(self, owner_client):
        response = owner_client.get(reverse("analytics:sales_dashboard"))
        assert response.status_code == 200

    def test_non_owner_cannot_view_dashboard(self, client, cashier):
        client.force_login(cashier)
        response = client.get(reverse("analytics:sales_dashboard"))
        assert response.status_code == 403

    def test_branch_filter_shows_low_stock_count(self, owner_client, branch):
        response = owner_client.get(reverse("analytics:sales_dashboard"), {"branch": branch.pk})
        assert response.status_code == 200


class TestProductPerformanceView:
    def test_owner_can_view(self, owner_client):
        response = owner_client.get(reverse("analytics:product_performance"))
        assert response.status_code == 200

    def test_non_owner_cannot_view(self, client, cashier):
        client.force_login(cashier)
        response = client.get(reverse("analytics:product_performance"))
        assert response.status_code == 403


class TestResourceConsumptionView:
    def test_owner_can_view(self, owner_client):
        response = owner_client.get(reverse("analytics:resource_consumption"))
        assert response.status_code == 200

    def test_kilogram_quantity_shows_two_decimal_places(self, owner_client):
        """Task #137 (client review): currency/number formatting
        standardization. This page's materials figures rendered the raw
        Decimal with no formatting at all. quantity_used is a
        DecimalField(3dp), so an un-rounded total like 5.500 would show
        as "5.500" without the `qty` filter. For a continuous unit like
        kg, two decimal places is the standard (see task #163, which
        later specified pc-counted items should show as whole numbers
        instead -- see test_piece_quantity_shows_as_whole_number below)."""
        role = Role.objects.create(name=Role.COMMISSARY_STAFF)
        staff = User.objects.create_user(username="commissary1", password="testpass123", role=role)
        flour = Product.objects.create(
            name="Flour",
            price="55.00",
            product_type=Product.ProductType.MATERIAL,
            unit_of_measure=Product.UnitOfMeasure.KILOGRAM,
        )
        cake = Product.objects.create(name="Cake", price="140.00")
        record = ProductionRecord.objects.create(
            commissary_staff=staff, product=cake, quantity_produced=10
        )
        IngredientUsage.objects.create(
            production_record=record, material=flour, quantity_used="5.500"
        )

        content = owner_client.get(reverse("analytics:resource_consumption")).content.decode()

        assert "5.50" in content
        assert "5.500" not in content

    def test_piece_quantity_shows_as_whole_number(self, owner_client):
        """Task #163 (adviser dry-run, 2026-11-05): "Display piece-based
        quantities as whole numbers." A pc-counted material's total
        shouldn't render with a meaningless ".000" decimal tail."""
        role = Role.objects.create(name=Role.COMMISSARY_STAFF)
        staff = User.objects.create_user(username="commissary1", password="testpass123", role=role)
        cup_lids = Product.objects.create(
            name="Cup Lids",
            price="1.00",
            product_type=Product.ProductType.MATERIAL,
            unit_of_measure=Product.UnitOfMeasure.PIECE,
        )
        cake = Product.objects.create(name="Cake", price="140.00")
        record = ProductionRecord.objects.create(
            commissary_staff=staff, product=cake, quantity_produced=10
        )
        IngredientUsage.objects.create(
            production_record=record, material=cup_lids, quantity_used="300.000"
        )

        content = owner_client.get(reverse("analytics:resource_consumption")).content.decode()

        assert "300" in content
        assert "300.000" not in content
        assert "pc" in content

    def test_unit_column_shown_in_materials_table(self, owner_client):
        """Task #163: "Add a Unit column to the materials table.\" """
        role = Role.objects.create(name=Role.COMMISSARY_STAFF)
        staff = User.objects.create_user(username="commissary1", password="testpass123", role=role)
        flour = Product.objects.create(
            name="Flour",
            price="55.00",
            product_type=Product.ProductType.MATERIAL,
            unit_of_measure=Product.UnitOfMeasure.KILOGRAM,
        )
        cake = Product.objects.create(name="Cake", price="140.00")
        record = ProductionRecord.objects.create(
            commissary_staff=staff, product=cake, quantity_produced=10
        )
        IngredientUsage.objects.create(production_record=record, material=flour, quantity_used=5)

        response = owner_client.get(reverse("analytics:resource_consumption"))

        assert "<th>Unit</th>" in response.content.decode()

    def test_non_owner_cannot_view(self, client, cashier):
        client.force_login(cashier)
        response = client.get(reverse("analytics:resource_consumption"))
        assert response.status_code == 403


class TestBranchPerformance:
    """Dashboard Home batch: Branch Performance cards (Fig. 3-19)."""

    def test_revenue_and_units_are_per_branch_not_global(self, branch, cashier, latte):
        other_branch = Branch.objects.create(name="Batangas City", code="BATANGAS")
        other_cashier = User.objects.create_user(
            username="cashier2",
            password="testpass123",
            role=cashier.role,
            branch=other_branch,
        )

        _completed_sale(
            branch, cashier, latte, quantity=2, unit_price="125.00", amount_tendered="300.00"
        )
        _completed_sale(
            other_branch,
            other_cashier,
            latte,
            quantity=5,
            unit_price="125.00",
            amount_tendered="700.00",
        )

        rows = {r["branch"].pk: r for r in services.get_branch_performance()}

        assert rows[branch.pk]["revenue"] == pytest.approx(250.00)
        assert rows[branch.pk]["units_sold"] == 2
        assert rows[branch.pk]["orders"] == 1

        assert rows[other_branch.pk]["revenue"] == pytest.approx(625.00)
        assert rows[other_branch.pk]["units_sold"] == 5

    def test_branch_with_no_sales_still_appears_with_zeros(self, branch):
        rows = services.get_branch_performance()
        assert len(rows) == 1
        assert rows[0]["revenue"] == 0
        assert rows[0]["units_sold"] == 0
        assert rows[0]["orders"] == 0

    def test_a_sale_from_a_previous_month_is_not_counted(self, branch, cashier, latte):
        """Client review (2026-11-10): the Branch Performance Overview
        card is explicitly labeled "This month" (see admin_dashboard.html),
        but used to run on _trailing_window_anchor's own trailing-30-days-
        anchored-to-last-activity window instead of the real calendar
        month -- so a 60-day-old sale could still count as part of "this
        month"'s revenue, and a branch whose last real activity predated
        the current month entirely would show that stale period's totals
        mislabeled as this month's. (This superseded Task #136's old
        "show Alangilan's last real period rather than zeros" fallback for
        THIS specific card -- that fallback was right for a generic
        trailing-N-days KPI, but wrong for one that promises a specific
        calendar month: a branch with no sales yet this month genuinely
        has Php0 revenue this month.) Now uses _current_month_window, so
        a sale from a previous month must not count at all, even when
        it's the branch's only sale."""
        old_date = timezone.now() - timedelta(days=60)
        _completed_sale(
            branch,
            cashier,
            latte,
            quantity=1,
            unit_price="125.00",
            amount_tendered="125.00",
            when=old_date,
        )

        rows = {r["branch"].pk: r for r in services.get_branch_performance()}

        assert rows[branch.pk]["revenue"] == Decimal("0.00")
        assert rows[branch.pk]["units_sold"] == 0
        assert rows[branch.pk]["orders"] == 0

    def test_each_branchs_window_is_independent(self, branch, cashier, latte):
        """A sale outside the current month at one branch must not
        affect a sibling branch's own (real, this-month) numbers."""
        other_branch = Branch.objects.create(name="Batangas City", code="BATANGAS")
        other_cashier = User.objects.create_user(
            username="cashier2", password="testpass123", role=cashier.role, branch=other_branch
        )
        old_date = timezone.now() - timedelta(days=60)
        _completed_sale(
            branch,
            cashier,
            latte,
            quantity=1,
            unit_price="125.00",
            amount_tendered="125.00",
            when=old_date,
        )
        _completed_sale(
            other_branch,
            other_cashier,
            latte,
            quantity=3,
            unit_price="125.00",
            amount_tendered="375.00",
        )

        rows = {r["branch"].pk: r for r in services.get_branch_performance()}

        assert rows[branch.pk]["revenue"] == Decimal("0.00")
        assert rows[other_branch.pk]["revenue"] == pytest.approx(375.00)

    def test_revenue_reflects_transaction_wide_discount(self, branch, cashier, latte):
        _completed_sale_with_discount(
            branch,
            cashier,
            latte,
            quantity=4,
            unit_price="125.00",
            discount_category="SC2",
            discount_type="PERCENT",
            discount_amount="20",
        )
        rows = {r["branch"].pk: r for r in services.get_branch_performance()}
        assert rows[branch.pk]["revenue"] == pytest.approx(400.00)

    def test_toggles_actually_change_branch_performance_not_just_total_revenue(
        self, branch, cashier, latte
    ):
        """Client review (2026-11-10): before get_branch_performance
        accepted the same three toggles as get_sales_summary_with_
        toggles, it always used grand_total regardless of which toggles
        were active -- so flipping a toggle moved the Total Revenue KPI
        but left every Branch Performance card completely unchanged,
        guaranteeing a visible mismatch the moment an admin touched a
        toggle. Confirms each toggle now actually changes this card's
        own revenue figure."""
        txn = SalesTransaction.objects.create(
            branch=branch,
            cashier=cashier,
            status=SalesTransaction.Status.COMPLETED,
            completed_at=timezone.now(),
            transaction_discount_category="SENIOR",
            transaction_discount_type="PERCENT",
            transaction_discount_amount="20.00",
        )
        SalesItem.objects.create(transaction=txn, product=latte, unit_price="100.00", quantity=1)
        SalesItem.objects.create(
            transaction=txn, custom_name="Off-menu special", unit_price="50.00", quantity=1
        )

        default_rows = {r["branch"].pk: r for r in services.get_branch_performance()}
        no_additional_rows = {
            r["branch"].pk: r
            for r in services.get_branch_performance(include_additional_sales=False)
        }
        no_discount_rows = {
            r["branch"].pk: r for r in services.get_branch_performance(exclude_discounts=True)
        }

        # Default: (100 + 50) subtotal, 20% off = 120.00.
        assert default_rows[branch.pk]["revenue"] == pytest.approx(120.00)
        # Off-menu item excluded: 100 subtotal, 20% off = 80.00.
        assert no_additional_rows[branch.pk]["revenue"] == pytest.approx(80.00)
        # Discount reversed: 100 + 50 subtotal, no discount = 150.00.
        assert no_discount_rows[branch.pk]["revenue"] == pytest.approx(150.00)

    def test_total_revenue_matches_branch_performance_under_every_toggle(
        self, branch, cashier, latte
    ):
        """The headline guarantee from the client review (2026-11-10)
        audit: Total Revenue (the KPI card) must equal the sum of the
        Branch Performance cards' own revenue, for every toggle
        combination -- not just the defaults."""
        txn = SalesTransaction.objects.create(
            branch=branch,
            cashier=cashier,
            status=SalesTransaction.Status.COMPLETED,
            completed_at=timezone.now(),
            transaction_discount_category="SENIOR",
            transaction_discount_type="PERCENT",
            transaction_discount_amount="20.00",
        )
        SalesItem.objects.create(transaction=txn, product=latte, unit_price="100.00", quantity=1)
        SalesItem.objects.create(
            transaction=txn, custom_name="Off-menu special", unit_price="50.00", quantity=1
        )

        for kwargs in [
            {},
            {"include_additional_sales": False},
            {"exclude_discounts": True},
            {"exclude_vat": True},
            {"include_additional_sales": False, "exclude_discounts": True, "exclude_vat": True},
        ]:
            branch_rows = services.get_branch_performance(**kwargs)
            expected_total = sum((r["revenue"] for r in branch_rows), Decimal("0.00"))
            total_revenue = services.get_sales_summary_with_toggles(branch=None, **kwargs)[
                "total_revenue"
            ]
            assert total_revenue == expected_total, f"mismatch for toggles={kwargs}"


class TestSyntheticAndHistoricalImportRevenueHandling:
    """Client review (2026-11-10): "Synthetic Backfill Bot" and
    "historical_import_<branch>" were both showing up as ordinary
    cashiers. Confirmed those two need OPPOSITE handling: the bot's
    rows are forecast-only test data and must never count toward any
    revenue/units/order total (see services._exclude_synthetic_
    transactions); the historical_import rows are real, migrated sales
    and must count exactly like a normal sale."""

    @pytest.fixture
    def bot(self):
        role, _ = Role.objects.get_or_create(name=Role.OWNER_ADMIN)
        return User.objects.create_user(
            username=User.SYNTHETIC_BACKFILL_BOT_USERNAME, role=role, branch=None
        )

    @pytest.fixture
    def historical_importer(self, branch):
        role, _ = Role.objects.get_or_create(name=Role.BRANCH_STAFF)
        return User.objects.create_user(
            username="historical_import_lipa", role=role, branch=branch, is_active=False
        )

    def test_sales_summary_excludes_the_synthetic_bot(self, branch, cashier, bot, latte):
        _completed_sale(
            branch, cashier, latte, quantity=1, unit_price="125.00", amount_tendered="125.00"
        )
        _completed_sale(
            branch, bot, latte, quantity=1, unit_price="125.00", amount_tendered="125.00"
        )

        summary = services.get_sales_summary(branch=branch)
        assert summary["total_revenue"] == pytest.approx(125.00)
        assert summary["total_transactions"] == 1
        assert summary["total_units_sold"] == 1

    def test_sales_summary_includes_real_historical_import_rows(
        self, branch, cashier, historical_importer, latte
    ):
        _completed_sale(
            branch, cashier, latte, quantity=1, unit_price="125.00", amount_tendered="125.00"
        )
        _completed_sale(
            branch,
            historical_importer,
            latte,
            quantity=1,
            unit_price="125.00",
            amount_tendered="125.00",
        )

        summary = services.get_sales_summary(branch=branch)
        assert summary["total_revenue"] == pytest.approx(250.00)
        assert summary["total_transactions"] == 2

    def test_branch_performance_excludes_the_synthetic_bot(self, branch, cashier, bot, latte):
        _completed_sale(
            branch, cashier, latte, quantity=2, unit_price="125.00", amount_tendered="250.00"
        )
        _completed_sale(
            branch, bot, latte, quantity=10, unit_price="125.00", amount_tendered="1250.00"
        )

        rows = {r["branch"].pk: r for r in services.get_branch_performance()}
        assert rows[branch.pk]["revenue"] == pytest.approx(250.00)
        assert rows[branch.pk]["units_sold"] == 2
        assert rows[branch.pk]["orders"] == 1

    def test_branch_performance_includes_real_historical_import_rows(
        self, branch, historical_importer, latte
    ):
        _completed_sale(
            branch,
            historical_importer,
            latte,
            quantity=3,
            unit_price="125.00",
            amount_tendered="375.00",
        )

        rows = {r["branch"].pk: r for r in services.get_branch_performance()}
        assert rows[branch.pk]["revenue"] == pytest.approx(375.00)
        assert rows[branch.pk]["units_sold"] == 3

    def test_top_products_excludes_the_synthetic_bot(self, branch, cashier, bot, latte):
        _completed_sale(
            branch, cashier, latte, quantity=1, unit_price="125.00", amount_tendered="125.00"
        )
        _completed_sale(
            branch, bot, latte, quantity=50, unit_price="125.00", amount_tendered="6250.00"
        )

        top = {row["product__id"]: row for row in services.get_top_products(branch=branch)}
        assert top[latte.pk]["units_sold"] == 1

    def test_total_units_sold_excludes_the_synthetic_bot(self, branch, cashier, bot, latte):
        _completed_sale(
            branch, cashier, latte, quantity=1, unit_price="125.00", amount_tendered="125.00"
        )
        _completed_sale(
            branch, bot, latte, quantity=50, unit_price="125.00", amount_tendered="6250.00"
        )

        assert services.get_total_units_sold(branch=branch) == 1


class TestSalesSummaryWithToggles:
    """Dashboard Home batch: the three real Total Revenue toggles."""

    def test_default_toggles_match_get_sales_summary_reference_shape(self, branch, cashier, latte):
        _completed_sale(
            branch, cashier, latte, quantity=2, unit_price="125.00", amount_tendered="300.00"
        )
        result = services.get_sales_summary_with_toggles(branch=branch)
        assert set(result.keys()) == {
            "total_revenue",
            "total_transactions",
            "total_units_sold",
            "average_daily_revenue",
            "days",
        }
        assert result["total_revenue"] == pytest.approx(250.00)

    def test_include_additional_sales_off_excludes_custom_items(self, branch, cashier, latte):
        txn = SalesTransaction.objects.create(
            branch=branch,
            cashier=cashier,
            status=SalesTransaction.Status.COMPLETED,
            completed_at=timezone.now(),
        )
        SalesItem.objects.create(transaction=txn, product=latte, unit_price="125.00", quantity=1)
        SalesItem.objects.create(
            transaction=txn, custom_name="Off-menu special", unit_price="500.00", quantity=1
        )

        with_additional = services.get_sales_summary_with_toggles(
            branch=branch, include_additional_sales=True
        )
        without_additional = services.get_sales_summary_with_toggles(
            branch=branch, include_additional_sales=False
        )

        assert with_additional["total_revenue"] == pytest.approx(625.00)
        assert without_additional["total_revenue"] == pytest.approx(125.00)

    def test_exclude_discounts_reverses_transaction_wide_discount_only(
        self, branch, cashier, latte
    ):
        txn = SalesTransaction.objects.create(
            branch=branch,
            cashier=cashier,
            status=SalesTransaction.Status.COMPLETED,
            completed_at=timezone.now(),
            transaction_discount_category="SENIOR",
            transaction_discount_type="PERCENT",
            transaction_discount_amount="20.00",
        )
        SalesItem.objects.create(transaction=txn, product=latte, unit_price="100.00", quantity=1)

        discounted = services.get_sales_summary_with_toggles(branch=branch, exclude_discounts=False)
        undiscounted = services.get_sales_summary_with_toggles(
            branch=branch, exclude_discounts=True
        )

        assert discounted["total_revenue"] == pytest.approx(80.00)
        assert undiscounted["total_revenue"] == pytest.approx(100.00)

    def test_exclude_vat_strips_the_configured_tax_rate(self, branch, cashier, latte):
        from apps.system_config.models import BusinessSettings

        settings_obj = BusinessSettings.load()
        settings_obj.tax_rate_percent = "12.00"
        settings_obj.save()

        SalesTransaction.objects.create(
            branch=branch,
            cashier=cashier,
            status=SalesTransaction.Status.COMPLETED,
            completed_at=timezone.now(),
        ).items.create(product=latte, unit_price="112.00", quantity=1)

        inclusive = services.get_sales_summary_with_toggles(branch=branch, exclude_vat=False)
        exclusive = services.get_sales_summary_with_toggles(branch=branch, exclude_vat=True)

        assert inclusive["total_revenue"] == pytest.approx(112.00)
        assert exclusive["total_revenue"] == pytest.approx(100.00, abs=0.01)

    def test_percent_discount_is_quantized_like_grand_total(self, branch, cashier, latte):
        """Client review (2026-11-10): the old inline discount math here
        computed a PERCENT discount's value without quantize()ing it to
        2 decimal places the way SalesTransaction.transaction_discount_
        value (and so grand_total) already does -- a sub-centavo
        precision mismatch between this function and get_branch_
        performance's old grand_total-based figure. A 20% discount on
        Php457.60 is the exact real-site regression case
        (transaction_discount_value's own docstring): unquantized,
        Decimal division/multiplication leaves extra places
        (91.5200...), so this confirms the toggled result matches the
        model's own already-correct, quantized math exactly."""
        txn = SalesTransaction.objects.create(
            branch=branch,
            cashier=cashier,
            status=SalesTransaction.Status.COMPLETED,
            completed_at=timezone.now(),
            transaction_discount_category="SC2",
            transaction_discount_type="PERCENT",
            transaction_discount_amount="20.00",
        )
        SalesItem.objects.create(transaction=txn, product=latte, unit_price="457.60", quantity=1)
        txn.refresh_from_db()

        result = services.get_sales_summary_with_toggles(branch=branch)
        assert result["total_revenue"] == txn.grand_total

    def test_a_sale_from_a_previous_month_is_excluded_from_total_revenue(
        self, branch, cashier, latte
    ):
        """Mirrors TestBranchPerformance's own current-month regression
        test -- the Total Revenue KPI must honor the same real calendar-
        month window, not the old trailing-30-days-anchored-to-last-
        activity one."""
        old_date = timezone.now() - timedelta(days=60)
        _completed_sale(
            branch, cashier, latte, quantity=1, unit_price="125.00", amount_tendered="125.00"
        )
        _completed_sale(
            branch,
            cashier,
            latte,
            quantity=1,
            unit_price="999.00",
            amount_tendered="999.00",
            when=old_date,
        )

        result = services.get_sales_summary_with_toggles(branch=branch)
        assert result["total_revenue"] == pytest.approx(125.00)
        assert result["total_transactions"] == 1

    def test_all_branches_total_matches_sum_of_branch_performance_with_a_quiet_branch(
        self, branch, cashier, latte
    ):
        """Client review (2026-11-10): Total Revenue (branch=None) must
        equal the sum of the Branch Performance cards' own revenue, for
        every combination of the three toggles -- not just when every
        branch happens to have activity this month. Originally written
        for Task #161 (adviser dry-run, 2026-11-05)'s anchor-consistency
        fix; now get_sales_summary_with_toggles(branch=None) is built
        directly from get_branch_performance's own rows (see that
        function's own docstring), so this is close to a tautology for
        the default-toggles case, but still real coverage for the
        per-branch, non-default-toggle path below, which uses its own
        separate calculation for a single branch."""
        other_branch = Branch.objects.create(name="Batangas City", code="BATANGAS")
        other_cashier = User.objects.create_user(
            username="cashier2", password="testpass123", role=cashier.role, branch=other_branch
        )
        old_date = timezone.now() - timedelta(days=60)
        _completed_sale(
            branch,
            cashier,
            latte,
            quantity=1,
            unit_price="125.00",
            amount_tendered="125.00",
            when=old_date,
        )
        _completed_sale(
            other_branch,
            other_cashier,
            latte,
            quantity=3,
            unit_price="125.00",
            amount_tendered="375.00",
        )

        # Default toggles: Total Revenue (branch=None) must equal the sum
        # of the Branch Performance cards' own revenue -- the literal bug
        # reported ("Total Revenue excludes Alangilan").
        branch_rows = services.get_branch_performance()
        expected_default_total = sum(r["revenue"] for r in branch_rows)
        default_result = services.get_sales_summary_with_toggles(branch=None)
        assert float(default_result["total_revenue"]) == pytest.approx(
            float(expected_default_total)
        )

        # For every toggle combination, Total Revenue (branch=None) must
        # equal the sum of that SAME toggle combination applied per
        # branch -- the general "one consistent calculation" requirement,
        # now exercised across a batch-only branch plus a live branch.
        for kwargs in [
            {},
            {"include_additional_sales": False},
            {"exclude_discounts": True},
            {"exclude_vat": True},
            {"include_additional_sales": False, "exclude_discounts": True, "exclude_vat": True},
        ]:
            all_branches_total = services.get_sales_summary_with_toggles(branch=None, **kwargs)[
                "total_revenue"
            ]
            per_branch_sum = sum(
                (
                    services.get_sales_summary_with_toggles(branch=branch, **kwargs)[
                        "total_revenue"
                    ],
                    services.get_sales_summary_with_toggles(branch=other_branch, **kwargs)[
                        "total_revenue"
                    ],
                ),
                Decimal("0.00"),
            )
            assert float(all_branches_total) == pytest.approx(float(per_branch_sum)), (
                f"Total Revenue mismatch for toggles={kwargs}: "
                f"{all_branches_total} != {per_branch_sum}"
            )


class TestWeeklySalesTrendByBranch:
    def test_returns_one_series_per_active_branch(self, branch, cashier, latte):
        other_branch = Branch.objects.create(name="Batangas City", code="BATANGAS")
        _completed_sale(
            branch, cashier, latte, quantity=1, unit_price="125.00", amount_tendered="200.00"
        )

        result = services.get_weekly_sales_trend_by_branch()
        branch_names = {s["branch_name"] for s in result["series"]}

        assert branch_names == {branch.name, other_branch.name}
        assert len(result["week_labels"]) == 8
        for series in result["series"]:
            assert len(series["revenue"]) == 8

    def test_revenue_reflects_transaction_wide_discount(self, branch, cashier, latte):
        _completed_sale_with_discount(
            branch,
            cashier,
            latte,
            quantity=4,
            unit_price="125.00",
            discount_category="SC2",
            discount_type="PERCENT",
            discount_amount="20",
        )
        result = services.get_weekly_sales_trend_by_branch(weeks=1)
        series = next(s for s in result["series"] if s["branch_name"] == branch.name)
        assert series["revenue"][0] == pytest.approx(400.00)


class TestPresetPeriodRange:
    """Pure-function tests for the Reports Hub's named-preset date math
    (apps.analytics.views), independent of the view/DB -- pins down the
    exact calendar boundaries with a fixed `today` rather than relying
    on whatever day the suite happens to run."""

    def test_this_month_starts_on_the_1st_of_the_current_month(self):
        today = date(2026, 11, 17)
        start, end = analytics_views._preset_period_range("this_month", today=today)
        assert start == date(2026, 11, 1)
        assert end == today

    def test_last_3_months_spans_exactly_three_calendar_months(self):
        """Opened in November, "Last 3 Months" should read as
        September-October-November (3 months), not a rolling 90 days."""
        today = date(2026, 11, 17)
        start, end = analytics_views._preset_period_range("last_3_months", today=today)
        assert start == date(2026, 9, 1)
        assert end == today

    def test_last_6_months_spans_exactly_six_calendar_months(self):
        today = date(2026, 11, 17)
        start, end = analytics_views._preset_period_range("last_6_months", today=today)
        assert start == date(2026, 6, 1)
        assert end == today

    def test_this_year_starts_on_january_1st(self):
        today = date(2026, 11, 17)
        start, end = analytics_views._preset_period_range("this_year", today=today)
        assert start == date(2026, 1, 1)
        assert end == today

    def test_last_months_preset_crosses_a_year_boundary_correctly(self):
        """Opened in February, "Last 6 Months" must roll back into the
        previous calendar year (September) rather than wrapping to an
        invalid month number."""
        today = date(2027, 2, 10)
        start, end = analytics_views._preset_period_range("last_6_months", today=today)
        assert start == date(2026, 9, 1)
        assert end == today

    def test_unknown_period_is_rejected(self):
        with pytest.raises(ValueError):
            analytics_views._preset_period_range("not-a-real-period")


class TestReportsHubView:
    """Reports & Export batch: the new hub combining all four real
    report datasets (Sales & Trend, Product Performance, Resource
    Consumption, Operational Performance) into one request."""

    def test_owner_can_view(self, owner_client):
        response = owner_client.get(reverse("analytics:reports_hub"))
        assert response.status_code == 200

    def test_non_owner_cannot_view(self, client, cashier):
        client.force_login(cashier)
        response = client.get(reverse("analytics:reports_hub"))
        assert response.status_code == 403

    def test_shell_context_present(self, owner_client):
        response = owner_client.get(reverse("analytics:reports_hub"))
        assert response.context["active_nav"] == "reports"
        assert "branches" in response.context

    def test_all_four_real_datasets_present_in_context(self, owner_client, branch, cashier, latte):
        _completed_sale(
            branch, cashier, latte, quantity=2, unit_price="125.00", amount_tendered="300.00"
        )

        response = owner_client.get(reverse("analytics:reports_hub"))
        assert set(response.context["sales_summary"].keys()) >= {
            "total_revenue",
            "total_units_sold",
        }
        assert isinstance(response.context["product_performance"], list)
        assert "total_cost" in response.context["consumption"]

    def test_selected_branch_filters_product_performance(
        self, owner_client, branch, cashier, latte
    ):
        """Task #142: selecting a branch on the Reports Hub page must
        filter Product Performance the same way it already filters
        Sales Summary -- previously it was silently ignored here."""
        other_branch = Branch.objects.create(name="Batangas City", code="BATANGAS")
        other_cashier = User.objects.create_user(
            username="cashier2", password="testpass123", role=cashier.role, branch=other_branch
        )
        other_product = Product.objects.create(name="Batangas Special", price="110.00")
        _completed_sale(branch, cashier, latte, 5, "125.00", "625.00")
        _completed_sale(other_branch, other_cashier, other_product, 7, "110.00", "770.00")

        response = owner_client.get(reverse("analytics:reports_hub"), {"branch": branch.pk})

        names = [r["product_name"] for r in response.context["product_performance"]]
        assert "Spanish Latte" in names
        assert "Batangas Special" not in names
        assert "completion_rate" in response.context["operational"]

    def test_days_filter_is_honored(self, owner_client, branch, cashier, latte):
        response = owner_client.get(reverse("analytics:reports_hub"), {"days": 7})
        assert response.context["days"] == 7

    def test_product_performance_chart_has_whole_number_y_axis_and_units_sold_label(
        self, owner_client
    ):
        """Task #163 (adviser dry-run, 2026-11-05): the Product
        Performance chart's Y-axis previously had no configuration at
        all, so Chart.js's default "nice number" tick generator could
        pick fractional intervals (e.g. 1.75) for small datasets, and
        the axis carried no label explaining what it counted."""
        content = owner_client.get(reverse("analytics:reports_hub")).content.decode()

        assert '"text": "Units Sold"' in content or 'text: "Units Sold"' in content
        assert "precision: 0" in content

    def test_new_badge_has_tooltip_explaining_what_new_means(
        self, owner_client, branch, cashier, latte
    ):
        """Task #163: the "New" growth badge (shown when a product has
        no sales in the previous period, so no growth rate can be
        computed) needs a tooltip explaining why, not just the bare
        word "New" with no context."""
        _completed_sale(branch, cashier, latte, 3, "125.00", "375.00")

        content = owner_client.get(reverse("analytics:reports_hub")).content.decode()

        assert 'title="No sales in previous period.">New<' in content

    def test_branch_identified_on_product_performance_panel(
        self, owner_client, branch, cashier, latte
    ):
        """Task #163: "clearly identify which branch the report covers
        (label or column)" -- Product Performance can be branch-filtered
        (see test_selected_branch_filters_product_performance above) but
        previously showed no indication anywhere of which branch (or
        "all branches") the numbers on screen actually covered."""
        all_branches_content = owner_client.get(reverse("analytics:reports_hub")).content.decode()
        assert "All Branches" in all_branches_content

        scoped_content = owner_client.get(
            reverse("analytics:reports_hub"), {"branch": branch.pk}
        ).content.decode()
        assert f"Branch: <strong>{branch.name}</strong>" in scoped_content

    def test_no_fourth_url_invented_for_operational_performance(self):
        """Regression guard for the real routing decision this batch
        made: Operational Performance is a tab within reports_hub, not
        a URL of its own -- there's no analytics:operational_performance
        anywhere, by design."""
        from django.urls import NoReverseMatch

        with pytest.raises(NoReverseMatch):
            reverse("analytics:operational_performance")

    def test_custom_date_range_narrows_sales_summary(self, owner_client, branch, cashier, latte):
        """Task #146 (client review, 2026-11-04): an explicit date_from/
        date_to range should scope the Sales Summary to exactly that
        calendar range, independent of `days` and independent of
        "now" -- a sale outside the requested range must not count,
        and a sale inside it must."""
        in_range = _completed_sale(
            branch,
            cashier,
            latte,
            quantity=1,
            unit_price="125.00",
            amount_tendered="125.00",
            when=timezone.make_aware(datetime(2026, 11, 6, 10, 0)),
        )
        _completed_sale(
            branch,
            cashier,
            latte,
            quantity=5,
            unit_price="125.00",
            amount_tendered="625.00",
            when=timezone.make_aware(datetime(2026, 10, 1, 10, 0)),
        )

        response = owner_client.get(
            reverse("analytics:reports_hub"),
            {"date_from": "2026-11-06", "date_to": "2026-11-07"},
        )
        assert response.status_code == 200
        assert response.context["using_custom_range"] is True
        assert response.context["sales_summary"]["total_revenue"] == in_range.grand_total
        assert response.context["sales_summary"]["total_units_sold"] == 1

    def test_invalid_date_format_shows_error_and_falls_back_to_days(
        self, owner_client, branch, cashier, latte
    ):
        _completed_sale(
            branch, cashier, latte, quantity=1, unit_price="125.00", amount_tendered="125.00"
        )

        response = owner_client.get(reverse("analytics:reports_hub"), {"date_from": "not-a-date"})
        assert response.status_code == 200
        assert "YYYY-MM-DD" in response.context["date_error"]
        # An invalid range isn't a blank page -- it falls back to the
        # ordinary `days` preset rather than hiding real data.
        assert response.context["using_custom_range"] is False
        assert response.context["sales_summary"]["total_units_sold"] == 1

    def test_from_date_after_to_date_shows_error(self, owner_client):
        response = owner_client.get(
            reverse("analytics:reports_hub"),
            {"date_from": "2026-11-07", "date_to": "2026-11-01"},
        )
        assert response.status_code == 200
        assert "on or before" in response.context["date_error"]
        assert response.context["using_custom_range"] is False

    def test_no_custom_range_given_uses_days_preset_as_before(
        self, owner_client, branch, cashier, latte
    ):
        response = owner_client.get(reverse("analytics:reports_hub"), {"days": 7})
        assert response.context["using_custom_range"] is False
        assert response.context["days"] == 7

    def test_default_load_defaults_to_this_month_preset(self, owner_client):
        """Panel request (2026-11-17): the page's first load (no period,
        no dates, no days at all) retains a reasonable default --
        This Month -- rather than an unlabeled trailing 30 days."""
        response = owner_client.get(reverse("analytics:reports_hub"))
        today = timezone.localdate()
        assert response.context["period"] == "this_month"
        assert response.context["date_from"] == today.replace(day=1).isoformat()
        assert response.context["date_to"] == today.isoformat()

    def test_days_only_link_keeps_legacy_days_behavior_not_a_preset(self, owner_client):
        """An old bookmarked ?days=7 link (back-compat, no period) must
        not be mistaken for a named preset -- the days-based trailing
        window still applies exactly as before this change."""
        response = owner_client.get(reverse("analytics:reports_hub"), {"days": 7})
        assert response.context["period"] == "days"

    def test_each_named_preset_resolves_to_its_own_calendar_range(self, owner_client):
        today = timezone.localdate()
        cases = {
            "this_month": date(today.year, today.month, 1),
            "this_year": date(today.year, 1, 1),
        }
        for period, expected_start in cases.items():
            response = owner_client.get(reverse("analytics:reports_hub"), {"period": period})
            assert response.context["period"] == period
            assert response.context["date_from"] == expected_start.isoformat()
            assert response.context["date_to"] == today.isoformat()
            assert response.context["using_custom_range"] is True

    def test_last_3_and_6_months_presets_start_on_the_first_of_the_right_month(self, owner_client):
        today = timezone.localdate()
        three_months = analytics_views._first_of_month_offset(today, 2)
        six_months = analytics_views._first_of_month_offset(today, 5)

        resp3 = owner_client.get(reverse("analytics:reports_hub"), {"period": "last_3_months"})
        assert resp3.context["date_from"] == three_months.isoformat()

        resp6 = owner_client.get(reverse("analytics:reports_hub"), {"period": "last_6_months"})
        assert resp6.context["date_from"] == six_months.isoformat()

    def test_named_preset_overrides_stray_manual_dates_in_querystring(
        self, owner_client, branch, cashier, latte
    ):
        """Selecting a preset while a previous Custom Range's date_from/
        date_to are still sitting in the query string (e.g. a stale
        bookmark, or the dropdown firing before the date inputs are
        cleared) must win over those stray values, not silently honor
        them instead."""
        today = timezone.localdate()
        response = owner_client.get(
            reverse("analytics:reports_hub"),
            {"period": "this_month", "date_from": "2020-01-01", "date_to": "2020-01-02"},
        )
        assert response.context["date_from"] == today.replace(day=1).isoformat()
        assert response.context["date_to"] == today.isoformat()

    def test_custom_period_still_behaves_like_manual_date_range(
        self, owner_client, branch, cashier, latte
    ):
        """period=custom is just a label for today's existing manual
        date_from/date_to behavior -- explicitly exercising it here
        guards against the new period-resolution logic accidentally
        treating "custom" as a fifth preset."""
        in_range = _completed_sale(
            branch,
            cashier,
            latte,
            quantity=1,
            unit_price="125.00",
            amount_tendered="125.00",
            when=timezone.make_aware(datetime(2026, 11, 6, 10, 0)),
        )
        response = owner_client.get(
            reverse("analytics:reports_hub"),
            {"period": "custom", "date_from": "2026-11-06", "date_to": "2026-11-07"},
        )
        assert response.context["period"] == "custom"
        assert response.context["using_custom_range"] is True
        assert response.context["sales_summary"]["total_revenue"] == in_range.grand_total

    def test_period_with_no_sales_shows_empty_state_message(self, owner_client):
        content = owner_client.get(
            reverse("analytics:reports_hub"), {"period": "this_month"}
        ).content.decode()
        assert "No sales were recorded for this period." in content

    def test_imported_only_period_explains_why_the_other_three_tabs_are_empty(
        self, owner_client, branch, cashier
    ):
        """Investigation finding (client review, 2026-11-17): a period
        made up entirely of historical-import revenue (product=None
        SalesItem rows, no linked production/batch records) shows real
        revenue on Sales & Trend but legitimately nothing on Product
        Performance/Resource Consumption/Operational Performance --
        confirmed against production data, not a query bug (see
        get_product_performance's docstring on why product=None rows
        are excluded, and get_resource_consumption_summary/
        get_operational_performance_summary's complete independence
        from SalesTransaction). Each of those three tabs now explains
        this inline instead of just looking broken."""
        _completed_sale(
            branch,
            cashier,
            product=None,
            quantity=1,
            unit_price="100.00",
            amount_tendered="100.00",
            when=timezone.make_aware(datetime(2025, 6, 15, 10, 0)),
        )

        content = owner_client.get(
            reverse("analytics:reports_hub"),
            {"date_from": "2025-01-01", "date_to": "2025-12-31"},
        ).content.decode()

        assert "No product-level sales for this period." in content
        assert "No production recorded for this period." in content
        assert "No batches recorded for this period." in content

    def test_notes_about_imported_only_periods_do_not_show_when_data_exists(
        self, owner_client, branch, cashier, latte
    ):
        _completed_sale(
            branch,
            cashier,
            latte,
            quantity=1,
            unit_price="125.00",
            amount_tendered="125.00",
            when=timezone.make_aware(datetime(2025, 6, 15, 10, 0)),
        )
        commissary_role = Role.objects.create(name=Role.COMMISSARY_STAFF)
        commissary_staff = User.objects.create_user(
            username="commissary_notes_test", password="testpass123", role=commissary_role
        )
        record = ProductionRecord.objects.create(
            commissary_staff=commissary_staff,
            product=latte,
            quantity_produced=10,
            status=ProductionRecord.Status.COMPLETED,
            quality=ProductionRecord.Quality.PASS,
        )
        # created_at is auto_now_add -- set the real historical date via
        # an update(), same as any other field a factory can't pass
        # straight into create() for an auto_now_add column.
        ProductionRecord.objects.filter(pk=record.pk).update(
            created_at=timezone.make_aware(datetime(2025, 6, 15, 10, 0))
        )

        content = owner_client.get(
            reverse("analytics:reports_hub"),
            {"date_from": "2025-01-01", "date_to": "2025-12-31"},
        ).content.decode()

        assert "No product-level sales for this period." not in content
        assert "No production recorded for this period." not in content
        assert "No batches recorded for this period." not in content

    def test_trend_chart_follows_the_custom_range(self, owner_client, branch, cashier, latte):
        """Panel request (2026-11-17): the chart now moves with the
        same Period/From/To filter as the summary cards, instead of
        always showing its own fixed last-8-weeks window."""
        in_range = _completed_sale(
            branch,
            cashier,
            latte,
            quantity=1,
            unit_price="125.00",
            amount_tendered="125.00",
            when=timezone.make_aware(datetime(2026, 11, 6, 10, 0)),
        )
        response = owner_client.get(
            reverse("analytics:reports_hub"),
            {"branch": branch.pk, "date_from": "2026-11-06", "date_to": "2026-11-07"},
        )
        assert response.context["trend_granularity"] == "day"
        labels = json.loads(response.context["trend_labels_json"])
        revenues = json.loads(response.context["trend_revenue_json"])
        assert len(labels) == 2
        assert sum(revenues) == pytest.approx(float(in_range.grand_total))

    def test_trend_chart_granularity_matches_range_length(self, owner_client):
        """Same window the summary cards use (This Year, a long-enough
        range to require monthly, not weekly, bucketing)."""
        response = owner_client.get(reverse("analytics:reports_hub"), {"period": "last_6_months"})
        assert response.context["trend_granularity"] in ("day", "week", "month")

        long_range_response = owner_client.get(
            reverse("analytics:reports_hub"),
            {"date_from": "2024-01-01", "date_to": "2025-12-31"},
        )
        assert long_range_response.context["trend_granularity"] == "month"
        assert "grouped by month" in long_range_response.content.decode()

    def test_summary_cards_use_thousands_separators(self, owner_client, branch, cashier, latte):
        """Client review (2026-11-10): Total Units Sold (Sales tab) and
        Units Produced (Resource Consumption tab) previously rendered as
        plain, unformatted numbers -- apply the same intcomma formatting
        used everywhere else on this page (e.g. the peso filter)."""
        # One transaction with a large quantity is enough to cross the
        # four-digit threshold without creating hundreds of DB rows.
        _completed_sale(
            branch, cashier, latte, quantity=1234, unit_price="1.00", amount_tendered="1234.00"
        )

        content = owner_client.get(reverse("analytics:reports_hub")).content.decode()
        assert "1,234" in content

    def test_product_performance_table_has_search_and_show_all_toggle_past_ten_rows(
        self, owner_client, branch, cashier
    ):
        """Section 2, client review (2026-11-10): more than 10 products
        must get a search box and a "Show top 10 / Show all" toggle;
        every row still renders (data untouched), rows past the 10th are
        just initially hidden via report-row-extra d-none."""
        for i in range(15):
            product = Product.objects.create(name=f"Product {i:02d}", price="50.00")
            _completed_sale(branch, cashier, product, 1, "50.00", "50.00")

        response = owner_client.get(reverse("analytics:reports_hub"))
        assert len(response.context["product_performance"]) == 15
        content = response.content.decode()
        assert 'id="productSearch"' in content
        assert 'id="productShowAllToggle"' in content
        assert "Show all (15)" in content
        assert content.count('class="report-row report-row-extra d-none"') == 5
        # Every product name is still present in the rendered HTML, not
        # dropped -- the extra rows are hidden via CSS, not server-side.
        for i in range(15):
            assert f"Product {i:02d}" in content

    def test_product_performance_table_has_no_toggle_at_ten_rows_or_fewer(
        self, owner_client, branch, cashier, latte
    ):
        _completed_sale(branch, cashier, latte, 1, "125.00", "125.00")

        content = owner_client.get(reverse("analytics:reports_hub")).content.decode()
        assert 'id="productSearch"' not in content
        assert 'id="productShowAllToggle"' not in content

    def test_material_table_has_search_and_show_all_toggle_past_ten_rows(self, owner_client):
        Branch.objects.create(name="Commissary", code="COMMISSARY", is_commissary=True)
        role = Role.objects.create(name=Role.COMMISSARY_STAFF)
        staff = User.objects.create_user(username="commissary1", password="testpass123", role=role)
        cake = Product.objects.create(name="Cake", price="140.00")

        for i in range(12):
            material = Product.objects.create(
                name=f"Material {i:02d}", price="10.00", product_type=Product.ProductType.MATERIAL
            )
            record = ProductionRecord.objects.create(
                commissary_staff=staff, product=cake, quantity_produced=1
            )
            IngredientUsage.objects.create(
                production_record=record, material=material, quantity_used=1
            )

        response = owner_client.get(reverse("analytics:reports_hub"))
        assert len(response.context["consumption"]["by_material"]) == 12
        content = response.content.decode()
        assert 'id="materialSearch"' in content
        assert 'id="materialShowAllToggle"' in content
        assert "Show all (12)" in content
        assert content.count('class="report-row report-row-extra d-none"') == 2

    def test_data_coverage_note_shown_on_sales_tab(self, owner_client):
        """Investigation finding (client review, 2026-11-10): the
        Sales & Trend tab must clearly flag that the client's own
        historical sales records stop at 2026-09-30 (verified directly
        against Batangas/Lipa/Alangilan Sales_2024-2026.xlsx), so a flat
        or near-zero stretch after that date isn't mistaken for either a
        query bug or a confirmed total sales stop."""
        content = owner_client.get(reverse("analytics:reports_hub")).content.decode()
        assert "Data coverage note." in content
        assert "September 30, 2026" in content


class TestResolveReportWindowAndServicesWithExplicitDateRange:
    """Task #146 (client review, 2026-11-04): service-level coverage for
    the explicit date_from/date_to range, independent of the view."""

    def test_get_sales_summary_uses_exact_calendar_range(self, branch, cashier, latte):
        in_range = _completed_sale(
            branch,
            cashier,
            latte,
            quantity=1,
            unit_price="125.00",
            amount_tendered="125.00",
            when=timezone.make_aware(datetime(2026, 11, 6, 23, 59)),
        )
        _completed_sale(
            branch,
            cashier,
            latte,
            quantity=3,
            unit_price="125.00",
            amount_tendered="375.00",
            when=timezone.make_aware(datetime(2026, 11, 8, 0, 0)),
        )

        summary = services.get_sales_summary(date_from=date(2026, 11, 6), date_to=date(2026, 11, 7))
        assert summary["total_revenue"] == in_range.grand_total
        assert summary["total_units_sold"] == 1
        # average_daily_revenue divides by the 2 actual days in the
        # explicit range (Nov 6 + Nov 7), not the unrelated `days` default.
        assert summary["average_daily_revenue"] == pytest.approx(float(in_range.grand_total) / 2)

    def test_get_sales_summary_date_range_beats_days_when_both_given(self, branch, cashier, latte):
        """An explicit, valid date range always wins over `days` -- the
        two are mutually exclusive request shapes, not combined."""
        in_range = _completed_sale(
            branch,
            cashier,
            latte,
            quantity=1,
            unit_price="125.00",
            amount_tendered="125.00",
            when=timezone.make_aware(datetime(2026, 11, 6, 10, 0)),
        )
        summary = services.get_sales_summary(
            days=1, date_from=date(2026, 11, 6), date_to=date(2026, 11, 7)
        )
        assert summary["total_revenue"] == in_range.grand_total

    def test_get_product_performance_current_and_previous_windows_from_explicit_range(
        self, branch, cashier
    ):
        espresso = Product.objects.create(name="Espresso", price="90.00")
        current = _completed_sale(
            branch,
            cashier,
            espresso,
            quantity=4,
            unit_price="90.00",
            amount_tendered="360.00",
            when=timezone.make_aware(datetime(2026, 11, 6, 10, 0)),
        )
        # Same-length window immediately before the requested range --
        # must be picked up as "previous", not ignored.
        _completed_sale(
            branch,
            cashier,
            espresso,
            quantity=2,
            unit_price="90.00",
            amount_tendered="180.00",
            when=timezone.make_aware(datetime(2026, 11, 4, 10, 0)),
        )
        del current

        rows = services.get_product_performance(
            date_from=date(2026, 11, 6), date_to=date(2026, 11, 7)
        )
        espresso_row = next(r for r in rows if r["product_name"] == "Espresso")
        assert espresso_row["current_units"] == 4
        assert espresso_row["previous_units"] == 2

    def test_get_resource_consumption_summary_respects_explicit_range(self):
        role = Role.objects.create(name=Role.COMMISSARY_STAFF)
        staff = User.objects.create_user(username="commissary2", password="testpass123", role=role)
        flour = Product.objects.create(
            name="Flour (kg)", price="55.00", product_type=Product.ProductType.MATERIAL
        )
        cake = Product.objects.create(name="Cake", price="140.00")

        in_range_record = ProductionRecord.objects.create(
            commissary_staff=staff, product=cake, quantity_produced=10
        )
        ProductionRecord.objects.filter(pk=in_range_record.pk).update(
            created_at=timezone.make_aware(datetime(2026, 11, 6, 10, 0))
        )
        IngredientUsage.objects.create(
            production_record=in_range_record, material=flour, quantity_used=5
        )

        out_of_range_record = ProductionRecord.objects.create(
            commissary_staff=staff, product=cake, quantity_produced=99
        )
        ProductionRecord.objects.filter(pk=out_of_range_record.pk).update(
            created_at=timezone.make_aware(datetime(2026, 9, 1, 10, 0))
        )
        IngredientUsage.objects.create(
            production_record=out_of_range_record, material=flour, quantity_used=50
        )

        summary = services.get_resource_consumption_summary(
            date_from=date(2026, 11, 6), date_to=date(2026, 11, 7)
        )
        assert summary["units_used_by_unit"] == [
            {"unit": "PIECE", "unit_display": "pc", "total": 5}
        ]
        assert summary["total_produced"] == 10


class TestReportsHubExportView:
    """Excel download for the Reports Hub (client review, 2026-11-09:
    the manuscript referenced a "downloadable Excel report" that never
    existed in this codebase -- see reports_hub_export's own docstring).
    Verifies the response is a real, openable workbook whose numbers
    match what the corresponding service functions return for the same
    filters, not just that *a* response came back."""

    def test_owner_can_download(self, owner_client):
        response = owner_client.get(reverse("analytics:reports_hub_export"))
        assert response.status_code == 200
        assert response["Content-Type"] == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert "attachment" in response["Content-Disposition"]
        assert ".xlsx" in response["Content-Disposition"]

    def test_non_owner_cannot_download(self, client, cashier):
        client.force_login(cashier)
        response = client.get(reverse("analytics:reports_hub_export"))
        assert response.status_code == 403

    def test_workbook_has_one_sheet_per_reports_hub_tab(self, owner_client):
        import io

        import openpyxl

        response = owner_client.get(reverse("analytics:reports_hub_export"))
        workbook = openpyxl.load_workbook(io.BytesIO(response.content))

        assert workbook.sheetnames == [
            "Sales & Trend",
            "Product Performance",
            "Resource Consumption",
            "Operational Performance",
        ]

    def test_sales_sheet_matches_the_same_service_function_the_page_uses(
        self, owner_client, branch, cashier, latte
    ):
        import io

        import openpyxl

        _completed_sale(
            branch, cashier, latte, quantity=2, unit_price="125.00", amount_tendered="300.00"
        )

        response = owner_client.get(reverse("analytics:reports_hub_export"), {"branch": branch.pk})
        workbook = openpyxl.load_workbook(io.BytesIO(response.content))
        sheet = workbook["Sales & Trend"]
        rows = {row[0]: row[1] for row in sheet.iter_rows(min_row=2, max_row=5, values_only=True)}

        expected = services.get_sales_summary(branch=branch)
        assert rows["Total Revenue"] == float(expected["total_revenue"])
        assert rows["Total Units Sold"] == expected["total_units_sold"]

    def test_filename_reflects_branch_and_range(self, owner_client, branch):
        """The export's filename follows whatever range is actually in
        effect: with no query params at all, that's now the default
        This Month preset (panel request, 2026-11-17), not the old
        unlabeled Last 30 Days."""
        response = owner_client.get(reverse("analytics:reports_hub_export"), {"branch": branch.pk})
        disposition = response["Content-Disposition"]
        assert branch.name.replace(" ", "") in disposition
        assert "ThisMonth" in disposition

    def test_filename_reflects_legacy_days_link(self, owner_client, branch):
        response = owner_client.get(
            reverse("analytics:reports_hub_export"), {"branch": branch.pk, "days": 30}
        )
        disposition = response["Content-Disposition"]
        assert "Last30Days" in disposition

    def test_custom_date_range_is_honored_same_as_the_page(
        self, owner_client, branch, cashier, latte
    ):
        in_range = _completed_sale(
            branch,
            cashier,
            latte,
            quantity=1,
            unit_price="125.00",
            amount_tendered="125.00",
            when=timezone.make_aware(datetime(2026, 11, 6, 10, 0)),
        )
        _completed_sale(
            branch,
            cashier,
            latte,
            quantity=5,
            unit_price="125.00",
            amount_tendered="625.00",
            when=timezone.make_aware(datetime(2026, 10, 1, 10, 0)),
        )

        import io

        import openpyxl

        response = owner_client.get(
            reverse("analytics:reports_hub_export"),
            {"branch": branch.pk, "date_from": "2026-11-06", "date_to": "2026-11-07"},
        )
        workbook = openpyxl.load_workbook(io.BytesIO(response.content))
        sheet = workbook["Sales & Trend"]
        rows = {row[0]: row[1] for row in sheet.iter_rows(min_row=2, max_row=5, values_only=True)}

        assert rows["Total Units Sold"] == 1
        assert rows["Total Revenue"] == float(in_range.grand_total)

    def test_product_performance_sheet_marks_new_products_without_crashing(
        self, owner_client, branch, cashier, latte
    ):
        """growth_rate is None for a brand-new product (see
        get_product_performance) -- the sheet must render "New" for
        that row rather than erroring on round(None, 1)."""
        import io

        import openpyxl

        _completed_sale(
            branch, cashier, latte, quantity=3, unit_price="125.00", amount_tendered="375.00"
        )

        response = owner_client.get(reverse("analytics:reports_hub_export"), {"branch": branch.pk})
        workbook = openpyxl.load_workbook(io.BytesIO(response.content))
        sheet = workbook["Product Performance"]
        data_rows = list(sheet.iter_rows(min_row=2, values_only=True))

        assert ("Spanish Latte", 3, 0, "New", None) in data_rows


class TestDemoDataInclusionAndLabeling:
    """scripts/generate_demo_history.py (client review, 2026-11-18)
    attributes its generated 2020-2026 production/sales history to one
    dedicated User.DEMO_DATA_GENERATOR_USERNAME account. Unlike the
    synthetic forecast bot, this data IS meant to surface -- but only in
    Product Performance, Resource Consumption, and Operational
    Performance (never Sales & Trend, Sales History, the Dashboard, or
    get_sales_summary), and only with a per-row "includes_demo_data"
    flag so it's never silently indistinguishable from a real sale or a
    real batch."""

    @pytest.fixture
    def demo_user(self):
        role, _ = Role.objects.get_or_create(name=Role.OWNER_ADMIN)
        return User.objects.create_user(
            username=User.DEMO_DATA_GENERATOR_USERNAME, role=role, branch=None
        )

    def test_display_name_reads_demo_not_the_raw_username(self, demo_user):
        assert demo_user.is_demo_data_account is True
        assert demo_user.display_name == "Demo"

    def test_product_performance_includes_demo_units_and_flags_the_row(
        self, branch, cashier, demo_user, latte
    ):
        _completed_sale(
            branch, cashier, latte, quantity=2, unit_price="125.00", amount_tendered="250.00"
        )
        _completed_sale(
            branch, demo_user, latte, quantity=7, unit_price="125.00", amount_tendered="875.00"
        )

        rows = {
            r["product_name"]: r for r in services.get_product_performance(branch=branch, days=30)
        }
        assert rows["Spanish Latte"]["current_units"] == 9  # 2 real + 7 demo
        assert rows["Spanish Latte"]["includes_demo_data"] is True

    def test_product_performance_row_without_demo_data_is_not_flagged(self, branch, cashier, latte):
        _completed_sale(
            branch, cashier, latte, quantity=2, unit_price="125.00", amount_tendered="250.00"
        )
        rows = {
            r["product_name"]: r for r in services.get_product_performance(branch=branch, days=30)
        }
        assert rows["Spanish Latte"]["includes_demo_data"] is False

    def test_sales_summary_still_excludes_demo_sales(self, branch, cashier, demo_user, latte):
        """Demo data is deliberately visible in Product Performance, but
        it must never inflate a real revenue total."""
        _completed_sale(
            branch, cashier, latte, quantity=1, unit_price="125.00", amount_tendered="125.00"
        )
        _completed_sale(
            branch, demo_user, latte, quantity=50, unit_price="125.00", amount_tendered="6250.00"
        )

        summary = services.get_sales_summary(branch=branch)
        assert summary["total_revenue"] == pytest.approx(125.00)
        assert summary["total_transactions"] == 1

    def test_sales_trend_still_excludes_demo_sales(self, branch, cashier, demo_user, latte):
        _completed_sale(
            branch, cashier, latte, quantity=1, unit_price="125.00", amount_tendered="125.00"
        )
        _completed_sale(
            branch, demo_user, latte, quantity=50, unit_price="125.00", amount_tendered="6250.00"
        )

        trend = services.get_sales_trend(branch=branch, days=30)
        total_revenue = sum(p["revenue"] for p in trend["points"])
        assert total_revenue == pytest.approx(125.00)

    def test_resource_consumption_includes_demo_production_and_flags_it(self, branch, demo_user):
        role = Role.objects.create(name=Role.COMMISSARY_STAFF)
        staff = User.objects.create_user(username="commissary1", password="testpass123", role=role)
        flour = Product.objects.create(
            name="Flour (kg)", price="55.00", product_type=Product.ProductType.MATERIAL
        )
        cake = Product.objects.create(name="Cake", price="140.00")

        real_record = ProductionRecord.objects.create(
            commissary_staff=staff, product=cake, quantity_produced=10
        )
        IngredientUsage.objects.create(
            production_record=real_record, material=flour, quantity_used=5
        )

        demo_record = ProductionRecord.objects.create(
            commissary_staff=demo_user, product=cake, quantity_produced=20
        )
        IngredientUsage.objects.create(
            production_record=demo_record, material=flour, quantity_used=12
        )

        summary = services.get_resource_consumption_summary(days=30)
        assert summary["total_produced"] == 30  # 10 real + 20 demo
        assert summary["includes_demo_data"] is True

        by_material = {row["material__name"]: row for row in summary["by_material"]}
        assert by_material["Flour (kg)"]["total_used"] == 17  # 5 real + 12 demo
        assert by_material["Flour (kg)"]["includes_demo_data"] is True

    def test_resource_consumption_without_demo_data_is_not_flagged(self, branch):
        role = Role.objects.create(name=Role.COMMISSARY_STAFF)
        staff = User.objects.create_user(username="commissary1", password="testpass123", role=role)
        flour = Product.objects.create(
            name="Flour (kg)", price="55.00", product_type=Product.ProductType.MATERIAL
        )
        cake = Product.objects.create(name="Cake", price="140.00")
        record = ProductionRecord.objects.create(
            commissary_staff=staff, product=cake, quantity_produced=10
        )
        IngredientUsage.objects.create(production_record=record, material=flour, quantity_used=5)

        summary = services.get_resource_consumption_summary(days=30)
        assert summary["includes_demo_data"] is False
        assert all(not row["includes_demo_data"] for row in summary["by_material"])

    def test_operational_performance_includes_demo_batches_and_flags_it(self, branch, demo_user):
        cake = Product.objects.create(name="Cake", price="140.00")

        ProductionRecord.objects.create(
            commissary_staff=demo_user,
            product=cake,
            quantity_produced=10,
            status=ProductionRecord.Status.COMPLETED,
            quality=ProductionRecord.Quality.PASS,
        )

        summary = services.get_operational_performance_summary(days=30)
        assert summary["total_batches"] == 1
        assert summary["includes_demo_data"] is True

    def test_operational_performance_without_demo_batches_is_not_flagged(self, branch):
        role = Role.objects.create(name=Role.COMMISSARY_STAFF)
        staff = User.objects.create_user(username="commissary1", password="testpass123", role=role)
        cake = Product.objects.create(name="Cake", price="140.00")
        ProductionRecord.objects.create(
            commissary_staff=staff,
            product=cake,
            quantity_produced=10,
            status=ProductionRecord.Status.COMPLETED,
            quality=ProductionRecord.Quality.PASS,
        )

        summary = services.get_operational_performance_summary(days=30)
        assert summary["includes_demo_data"] is False


class TestReportsHubDemoDataLabeling:
    """View/template layer: the quiet "Demo" marker added to
    templates/analytics/reports_hub.html and the Excel export (client
    review, 2026-11-18) must appear exactly where demo data is present,
    and nowhere else."""

    @pytest.fixture
    def demo_user(self):
        role, _ = Role.objects.get_or_create(name=Role.OWNER_ADMIN)
        return User.objects.create_user(
            username=User.DEMO_DATA_GENERATOR_USERNAME, role=role, branch=None
        )

    def test_product_performance_tab_shows_demo_badge_when_demo_data_present(
        self, owner_client, branch, demo_user, latte
    ):
        _completed_sale(
            branch, demo_user, latte, quantity=5, unit_price="125.00", amount_tendered="625.00"
        )

        response = owner_client.get(reverse("analytics:reports_hub"), {"branch": branch.pk})
        assert response.status_code == 200
        assert b"jc-demo-marker" in response.content
        assert b"Spanish Latte" in response.content

    def test_reports_hub_has_no_demo_badge_without_any_demo_data(
        self, owner_client, branch, cashier, latte
    ):
        _completed_sale(
            branch, cashier, latte, quantity=5, unit_price="125.00", amount_tendered="625.00"
        )

        response = owner_client.get(reverse("analytics:reports_hub"), {"branch": branch.pk})
        assert response.status_code == 200
        assert b"jc-demo-marker" not in response.content

    def test_export_marks_demo_rows_in_product_performance_and_resource_consumption_sheets(
        self, owner_client, branch, demo_user
    ):
        flour = Product.objects.create(
            name="Flour (kg)", price="55.00", product_type=Product.ProductType.MATERIAL
        )
        cake = Product.objects.create(name="Cake", price="140.00")
        _completed_sale(
            branch, demo_user, cake, quantity=4, unit_price="140.00", amount_tendered="560.00"
        )
        demo_record = ProductionRecord.objects.create(
            commissary_staff=demo_user, product=cake, quantity_produced=10
        )
        IngredientUsage.objects.create(
            production_record=demo_record, material=flour, quantity_used=5
        )

        import io

        import openpyxl

        response = owner_client.get(reverse("analytics:reports_hub_export"), {"branch": branch.pk})
        workbook = openpyxl.load_workbook(io.BytesIO(response.content))

        product_rows = list(workbook["Product Performance"].iter_rows(min_row=2, values_only=True))
        assert any(row[0] == "Cake" and row[-1] == "Demo" for row in product_rows)

        material_rows = list(
            workbook["Resource Consumption"].iter_rows(min_row=2, values_only=True)
        )
        assert any(row and row[0] == "Flour (kg)" and row[-1] == "Demo" for row in material_rows)
