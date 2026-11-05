"""
Tests for Week 9: Sales/Analytics Dashboard (Row 9.1), Product
Performance (Row 9.2), Resource Consumption & Operational Performance
(Row 9.3).
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Branch, Role, User
from apps.analytics import services
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

    def test_branch_with_only_old_batch_data_still_shows_its_real_numbers(
        self, branch, cashier, latte
    ):
        """Task #136 (client review, 2026-11-04): Alangilan's real
        scenario -- a branch updated only via periodic batch upload, not
        live POS, whose last upload is now more than 30 days in the
        past. It must show that branch's own real last period of
        activity on the Branch Performance Overview card, not zeros, and
        must not affect a sibling branch that DOES have recent activity
        (each branch's window is anchored independently -- see
        _trailing_window_anchor)."""
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

        assert rows[branch.pk]["revenue"] == pytest.approx(125.00)
        assert rows[branch.pk]["units_sold"] == 1
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

    def test_all_branches_total_matches_sum_of_branch_performance_with_batch_only_branch(
        self, branch, cashier, latte
    ):
        """Task #161 (adviser dry-run, 2026-11-05): Alangilan's real
        scenario -- a batch-only branch whose last upload is more than
        `days` in the past -- was showing up on the Branch Performance
        cards (which anchor per-branch, see
        test_branch_with_only_old_batch_data_still_shows_its_real_numbers)
        but NOT in the Total Revenue KPI, because
        get_sales_summary_with_toggles(branch=None) anchored its single
        window to timezone.now() for every branch combined, instead of
        anchoring each branch independently the same way
        get_branch_performance does. Total Revenue must equal the sum of
        every branch's own Branch Performance revenue, for every
        combination of the three toggles -- not just when every branch
        happens to have recent activity."""
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

    def test_trend_chart_ignores_the_custom_range_by_design(
        self, owner_client, branch, cashier, latte
    ):
        """Documented scope boundary: get_weekly_sales_trend deliberately
        does not take date_from/date_to, so the chart data shouldn't
        change based on the custom-range querystring."""
        without_range = owner_client.get(reverse("analytics:reports_hub"))
        with_range = owner_client.get(
            reverse("analytics:reports_hub"),
            {"date_from": "2026-11-06", "date_to": "2026-11-07"},
        )
        assert without_range.context["trend_labels_json"] == with_range.context["trend_labels_json"]


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
