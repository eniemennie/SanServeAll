"""
Tests for scripts/generate_demo_history.py and scripts/remove_demo_history.py
(client review, 2026-11-18): the generator that fills the Reports Hub's
Product Performance/Resource Consumption/Operational Performance tabs
with demo data for the 2020-01-01 to 2026-05-01 gap before real
production tracking began, and its exact-undo removal script.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Branch, Role, User
from apps.analytics import services as analytics_services
from apps.inventory.models import Inventory, Product
from apps.pos.models import SalesItem, SalesTransaction
from apps.production import services as production_services
from apps.production.models import IngredientUsage, ProductionRecord
from conftest import verify_otp_for_client

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded_real_data():
    """10 weeks of real ProductionRecord + SalesTransaction history for
    one product, across two branches -- enough real recent data for
    generate_demo_history.py to compute a real weekly average from."""
    role_commissary, _ = Role.objects.get_or_create(name=Role.COMMISSARY_STAFF)
    role_branch, _ = Role.objects.get_or_create(name=Role.BRANCH_STAFF)
    commissary = Branch.objects.create(name="Commissary", code="COMMISSARY", is_commissary=True)
    branch1 = Branch.objects.create(name="Lipa City", code="LIPA")
    branch2 = Branch.objects.create(name="Batangas City", code="BATANGAS")
    staff = User.objects.create_user(username="commissary1", password="x", role=role_commissary)
    cashier1 = User.objects.create_user(
        username="cashier1", password="x", role=role_branch, branch=branch1
    )
    # branch2 deliberately gets no real sales/production of its own --
    # proves a branch with no real history of this product simply gets
    # no demo data for it either, rather than inheriting branch1's.
    User.objects.create_user(username="cashier2", password="x", role=role_branch, branch=branch2)

    flour = Product.objects.create(
        name="Flour (kg)", price="55.00", product_type=Product.ProductType.MATERIAL
    )
    Inventory.objects.create(branch=commissary, product=flour, quantity_on_hand=10000)
    cake = Product.objects.create(
        name="Chocolate Cake", price="140.00", product_type=Product.ProductType.FINISHED_GOOD
    )

    now = timezone.now()
    for i in range(10):
        rec = production_services.record_production(
            commissary_staff=staff,
            product_id=cake.pk,
            quantity_produced=20,
            ingredient_rows=[{"material_id": flour.pk, "quantity_used": 5}],
        )
        ProductionRecord.objects.filter(pk=rec.pk).update(created_at=now - timedelta(days=7 * i))

        txn = SalesTransaction.objects.create(
            branch=branch1,
            cashier=cashier1,
            status=SalesTransaction.Status.COMPLETED,
            payment_method="CASH",
        )
        SalesItem.objects.create(transaction=txn, product=cake, unit_price="140.00", quantity=3)
        txn.refresh_from_db()
        txn.amount_tendered = txn.grand_total
        txn.save()
        ts = now - timedelta(days=7 * i)
        SalesTransaction.objects.filter(pk=txn.pk).update(created_at=ts, completed_at=ts)

    return branch1, branch2, cake


class TestGenerateDemoHistory:
    def test_generates_rows_scaled_from_real_averages_without_touching_real_data(
        self, seeded_real_data, tmp_path, monkeypatch
    ):
        import scripts.generate_demo_history as gen

        monkeypatch.setattr(gen, "MANIFEST_PATH", tmp_path / "manifest.json")

        gen.run(dry_run=True)  # must not write anything
        assert not ProductionRecord.objects.filter(
            commissary_staff__username=User.DEMO_DATA_GENERATOR_USERNAME
        ).exists()

        gen.run(dry_run=False)

        demo = User.objects.get(username=User.DEMO_DATA_GENERATOR_USERNAME)
        production = ProductionRecord.objects.filter(commissary_staff=demo)
        sales = SalesTransaction.objects.filter(cashier=demo)
        usage = IngredientUsage.objects.filter(production_record__commissary_staff=demo)

        assert production.count() > 0
        assert sales.count() > 0
        assert usage.count() > 0

        earliest = production.order_by("created_at").first()
        latest = production.order_by("-created_at").first()
        assert earliest.created_at.date() >= gen.DEMO_RANGE_START
        assert latest.created_at.date() <= gen.DEMO_RANGE_END

        # Real rows (10 production, 10 sales from the fixture) untouched.
        assert ProductionRecord.objects.exclude(commissary_staff=demo).count() == 10
        assert SalesTransaction.objects.exclude(cashier=demo).count() == 10

        # Re-running doesn't duplicate the dedicated account.
        gen.run(dry_run=False)
        assert User.objects.filter(username=User.DEMO_DATA_GENERATOR_USERNAME).count() == 1

    def test_finds_same_product_once_not_once_per_real_batch(self, seeded_real_data):
        """Regression: ProductionRecord's default Meta.ordering made an
        earlier version's DB-level .distinct() silently fail to dedupe
        by product_id (a well-known Django gotcha), listing the same
        product once per real ProductionRecord instead of once."""
        import scripts.generate_demo_history as gen

        eligible = gen.find_eligible_products(weeks_tracked=9)
        assert len(eligible) == 1
        assert eligible[0]["product"].name == "Chocolate Cake"


class TestDemoHistorySurfacesOnlyWhereIntended:
    def test_surfaces_in_the_three_reports_hub_tabs_for_an_old_range(
        self, seeded_real_data, tmp_path, monkeypatch
    ):
        import scripts.generate_demo_history as gen

        monkeypatch.setattr(gen, "MANIFEST_PATH", tmp_path / "manifest.json")
        branch1, _branch2, _cake = seeded_real_data
        gen.run(dry_run=False)

        old_from, old_to = date(2021, 1, 1), date(2021, 3, 1)

        rows = {
            r["product_name"]: r
            for r in analytics_services.get_product_performance(
                branch=branch1, date_from=old_from, date_to=old_to
            )
        }
        assert "Chocolate Cake" in rows
        assert rows["Chocolate Cake"]["current_units"] > 0
        assert rows["Chocolate Cake"]["includes_demo_data"] is True

        consumption = analytics_services.get_resource_consumption_summary(
            date_from=old_from, date_to=old_to
        )
        assert consumption["total_produced"] > 0
        assert consumption["includes_demo_data"] is True

        operational = analytics_services.get_operational_performance_summary(
            date_from=old_from, date_to=old_to
        )
        assert operational["total_batches"] > 0
        assert operational["includes_demo_data"] is True

    def test_never_surfaces_in_sales_trend_history_dashboard_or_batch_logs(
        self, seeded_real_data, tmp_path, monkeypatch, client
    ):
        import scripts.generate_demo_history as gen

        monkeypatch.setattr(gen, "MANIFEST_PATH", tmp_path / "manifest.json")
        branch1, _branch2, _cake = seeded_real_data
        gen.run(dry_run=False)

        old_from, old_to = date(2021, 1, 1), date(2021, 3, 1)

        summary = analytics_services.get_sales_summary(
            branch=branch1, date_from=old_from, date_to=old_to
        )
        assert summary["total_revenue"] == 0
        assert summary["total_transactions"] == 0

        history = analytics_services.get_sales_history(
            branch=branch1, date_from=old_from, date_to=old_to
        )
        assert len(history) == 0

        assert production_services.get_total_output(days=99999) == 200  # only the 10 real batches

        role, _ = Role.objects.get_or_create(name=Role.OWNER_ADMIN)
        owner = User.objects.create_user(username="owner1", password="x", role=role)
        client.force_login(owner)
        verify_otp_for_client(client, owner)
        response = client.get(reverse("production:batch_management"))
        assert response.context["stats"]["total"] == 10

    def test_reports_hub_page_shows_the_demo_badge_for_an_old_range(
        self, seeded_real_data, tmp_path, monkeypatch, client
    ):
        import scripts.generate_demo_history as gen

        monkeypatch.setattr(gen, "MANIFEST_PATH", tmp_path / "manifest.json")
        branch1, _branch2, _cake = seeded_real_data
        gen.run(dry_run=False)

        role, _ = Role.objects.get_or_create(name=Role.OWNER_ADMIN)
        owner = User.objects.create_user(username="owner2", password="x", role=role)
        client.force_login(owner)
        verify_otp_for_client(client, owner)

        response = client.get(
            reverse("analytics:reports_hub"),
            {"branch": branch1.pk, "date_from": "2021-01-01", "date_to": "2021-03-01"},
        )
        assert response.status_code == 200
        assert b"Demo</span>" in response.content


class TestRemoveDemoHistory:
    def test_removal_undoes_generation_exactly(self, seeded_real_data, tmp_path, monkeypatch):
        import scripts.generate_demo_history as gen
        import scripts.remove_demo_history as remove_gen

        manifest_path = tmp_path / "manifest.json"
        monkeypatch.setattr(gen, "MANIFEST_PATH", manifest_path)
        monkeypatch.setattr(remove_gen, "MANIFEST_PATH", manifest_path)

        real_production_count = ProductionRecord.objects.count()
        real_sales_count = SalesTransaction.objects.count()

        gen.run(dry_run=False)
        assert ProductionRecord.objects.count() > real_production_count
        assert SalesTransaction.objects.count() > real_sales_count
        assert User.objects.filter(username=User.DEMO_DATA_GENERATOR_USERNAME).exists()

        remove_gen.run()

        assert ProductionRecord.objects.count() == real_production_count
        assert SalesTransaction.objects.count() == real_sales_count
        assert not User.objects.filter(username=User.DEMO_DATA_GENERATOR_USERNAME).exists()
        assert not manifest_path.exists()

    def test_no_manifest_is_a_no_op(self, tmp_path, monkeypatch):
        import scripts.remove_demo_history as remove_gen

        monkeypatch.setattr(remove_gen, "MANIFEST_PATH", tmp_path / "missing.json")
        remove_gen.run()  # must not raise
