"""
Tests for scripts/generate_stress_test_data.py and
scripts/remove_stress_test_data.py -- the local-only, throwaway
load-test fixture generator for benchmarking Reports Hub/Dashboard
Home/Decision Support at realistic volume (capstone panel revision,
2026-11-27, Dr. Rowell M. Hernandez). Runs at small volume (tens of
rows, not the real ~270,000) purely to verify correctness quickly;
actual load-testing volume is exercised manually, not in this suite.
"""

import json

import pytest
from django.utils import timezone

from apps.accounts.models import Branch, Role, User
from apps.inventory.models import Product
from apps.pos.models import SalesItem, SalesTransaction
from scripts import generate_stress_test_data, remove_stress_test_data

pytestmark = pytest.mark.django_db


@pytest.fixture
def catalog():
    branch1 = Branch.objects.create(name="Lipa City", code="LIPA")
    branch2 = Branch.objects.create(name="Batangas City", code="BATANGAS")
    # Deliberately also seeds an active commissary -- proves
    # _eligible_branches() excludes it rather than just happening to
    # never see one (the bug this fixture was added to catch: the
    # commissary is the production facility, never a POS sales
    # location, and must never be picked as a synthetic transaction's
    # branch).
    Branch.objects.create(name="Commissary", code="COMMISSARY", is_commissary=True)
    cake = Product.objects.create(
        name="Chocolate Cake", price="140.00", product_type=Product.ProductType.FINISHED_GOOD
    )
    latte = Product.objects.create(
        name="Spanish Latte", price="120.00", product_type=Product.ProductType.FINISHED_GOOD
    )
    return {"branches": [branch1, branch2], "products": [cake, latte]}


@pytest.fixture(autouse=True)
def _clean_manifest(tmp_path, monkeypatch):
    """Point MANIFEST_PATH at a throwaway file per test, rather than the
    real scripts/stress_test_data_manifest.json, so running this suite
    never interferes with a real local run someone has in progress."""
    manifest_path = tmp_path / "stress_test_data_manifest.json"
    monkeypatch.setattr(generate_stress_test_data, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(remove_stress_test_data, "MANIFEST_PATH", manifest_path)
    yield


class TestAssertLocalDevSettings:
    def test_refuses_when_settings_module_is_not_local(self, monkeypatch):
        monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.production")
        with pytest.raises(RuntimeError, match="Refusing to run"):
            generate_stress_test_data._assert_local_dev_settings()

    def test_refuses_when_database_name_looks_like_staging_or_prod(self, monkeypatch):
        monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.test")
        monkeypatch.setitem(
            generate_stress_test_data.settings.DATABASES["default"],
            "NAME",
            "/srv/production.sqlite3",
        )
        with pytest.raises(RuntimeError, match="Refusing to run"):
            generate_stress_test_data._assert_local_dev_settings()

    def test_allows_the_real_test_settings_module(self, monkeypatch):
        monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.test")
        generate_stress_test_data._assert_local_dev_settings()  # does not raise


class TestEligibleBranches:
    def test_excludes_the_commissary(self, catalog):
        branches = generate_stress_test_data._eligible_branches()
        assert all(not b.is_commissary for b in branches)
        assert {b.pk for b in branches} == {b.pk for b in catalog["branches"]}


class TestGenerate:
    def test_dry_run_creates_nothing(self, catalog):
        before_txn = SalesTransaction.objects.count()
        before_item = SalesItem.objects.count()
        generate_stress_test_data.run(dry_run=True, total_transactions=20)
        assert SalesTransaction.objects.count() == before_txn
        assert SalesItem.objects.count() == before_item
        assert not generate_stress_test_data.MANIFEST_PATH.exists()

    def test_live_run_creates_expected_row_counts(self, catalog, monkeypatch):
        monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.test")
        generate_stress_test_data.run(dry_run=False, total_transactions=30, days_back=60)

        stress_user = User.objects.get(username=generate_stress_test_data.STRESS_USERNAME)
        assert SalesTransaction.objects.filter(cashier=stress_user).count() == 30
        assert SalesItem.objects.filter(transaction__cashier=stress_user).count() == 30
        for txn in SalesTransaction.objects.filter(cashier=stress_user):
            assert txn.status == SalesTransaction.Status.COMPLETED
            assert txn.branch in catalog["branches"]
            assert txn.completed_at is not None
            assert txn.completed_at.date() >= timezone.localdate() - timezone.timedelta(days=60)

    def test_live_run_never_touches_real_sales_transactions(self, catalog):
        real_branch = catalog["branches"][0]
        real_cashier = User.objects.create_user(
            username="real_cashier",
            password="x",
            role=Role.objects.get_or_create(name=Role.BRANCH_STAFF)[0],
            branch=real_branch,
        )
        real_txn = SalesTransaction.objects.create(
            branch=real_branch,
            cashier=real_cashier,
            status=SalesTransaction.Status.COMPLETED,
            payment_method="CASH",
        )
        SalesItem.objects.create(
            transaction=real_txn, product=catalog["products"][0], unit_price="140.00", quantity=1
        )
        real_txn.refresh_from_db()

        generate_stress_test_data.run(dry_run=False, total_transactions=15, days_back=30)

        real_txn.refresh_from_db()
        assert real_txn.cashier_id == real_cashier.pk
        assert real_txn.status == SalesTransaction.Status.COMPLETED
        assert (
            SalesTransaction.objects.filter(status=SalesTransaction.Status.COMPLETED)
            .exclude(cashier__username=generate_stress_test_data.STRESS_USERNAME)
            .count()
            == 1
        )

    def test_refuses_a_second_run_before_cleanup(self, catalog):
        generate_stress_test_data.run(dry_run=False, total_transactions=10, days_back=30)
        with pytest.raises(RuntimeError, match="hasn't.*been removed"):
            generate_stress_test_data.run(dry_run=False, total_transactions=10, days_back=30)

    def test_manifest_records_pk_floor_and_count(self, catalog):
        generate_stress_test_data.run(dry_run=False, total_transactions=12, days_back=30)
        manifest = json.loads(generate_stress_test_data.MANIFEST_PATH.read_text())
        assert manifest["transactions_created"] == 12
        assert manifest["stress_test_username"] == generate_stress_test_data.STRESS_USERNAME
        assert "sales_transaction" in manifest["pk_floor"]


class TestRemove:
    def test_round_trip_removes_exactly_what_was_created_and_nothing_else(self, catalog):
        real_branch = catalog["branches"][0]
        real_cashier = User.objects.create_user(
            username="real_cashier2",
            password="x",
            role=Role.objects.get_or_create(name=Role.BRANCH_STAFF)[0],
            branch=real_branch,
        )
        real_txn = SalesTransaction.objects.create(
            branch=real_branch,
            cashier=real_cashier,
            status=SalesTransaction.Status.COMPLETED,
            payment_method="CASH",
        )
        SalesItem.objects.create(
            transaction=real_txn, product=catalog["products"][0], unit_price="140.00", quantity=1
        )

        generate_stress_test_data.run(dry_run=False, total_transactions=25, days_back=30)
        assert SalesTransaction.objects.count() == 26  # 25 synthetic + 1 real

        remove_stress_test_data.run()

        assert not generate_stress_test_data.MANIFEST_PATH.exists()
        assert SalesTransaction.objects.count() == 1
        assert SalesTransaction.objects.first().pk == real_txn.pk
        assert not User.objects.filter(username=generate_stress_test_data.STRESS_USERNAME).exists()

    def test_safe_to_call_with_no_manifest(self):
        remove_stress_test_data.run()  # does not raise

    def test_removes_in_batches_smaller_than_the_total_row_count(self, catalog, monkeypatch):
        """Regression test (2026-11-27): a single queryset-wide .delete()
        call on all stress-test SalesTransaction rows at once can raise
        sqlite3.OperationalError: too many SQL variables at real
        stress-test volume (~270,000 rows) -- confirmed live -- because
        InventoryTransaction.related_sales_item is a nullable SET_NULL FK
        to SalesItem, and Django ORs every internal per-batch "set to
        NULL" update together into one combined statement whose
        bind-variable count scales with the TOTAL row count being
        deleted, not any one batch. remove_stress_test_data.run() now
        chunks the delete call itself (DELETE_BATCH_SIZE) rather than
        relying on Django's own internal batching alone. This test forces
        a small batch size with a row count that doesn't divide evenly
        by it, so the loop must run several iterations plus a partial
        final one, and confirms the end result is still exactly correct:
        every stress-test row gone, every real row untouched."""
        monkeypatch.setattr(remove_stress_test_data, "DELETE_BATCH_SIZE", 7)

        real_branch = catalog["branches"][0]
        real_cashier = User.objects.create_user(
            username="real_cashier3",
            password="x",
            role=Role.objects.get_or_create(name=Role.BRANCH_STAFF)[0],
            branch=real_branch,
        )
        real_txn = SalesTransaction.objects.create(
            branch=real_branch,
            cashier=real_cashier,
            status=SalesTransaction.Status.COMPLETED,
            payment_method="CASH",
        )
        SalesItem.objects.create(
            transaction=real_txn, product=catalog["products"][0], unit_price="140.00", quantity=1
        )

        generate_stress_test_data.run(dry_run=False, total_transactions=23, days_back=30)
        assert SalesTransaction.objects.count() == 24  # 23 synthetic + 1 real

        remove_stress_test_data.run()

        assert not generate_stress_test_data.MANIFEST_PATH.exists()
        assert SalesTransaction.objects.count() == 1
        assert SalesTransaction.objects.first().pk == real_txn.pk
        assert not User.objects.filter(username=generate_stress_test_data.STRESS_USERNAME).exists()

    def test_reports_mismatch_without_deleting_if_counts_disagree(self, catalog, capsys):
        generate_stress_test_data.run(dry_run=False, total_transactions=10, days_back=30)
        # Simulate drift: a real transaction somehow attributed to the
        # stress-test account after generation (should never happen in
        # practice -- this proves the cross-check catches it if it did).
        stress_user = User.objects.get(username=generate_stress_test_data.STRESS_USERNAME)
        manifest = json.loads(generate_stress_test_data.MANIFEST_PATH.read_text())
        manifest["pk_floor"]["sales_transaction"] = (
            SalesTransaction.objects.filter(cashier=stress_user).order_by("pk").first().pk
        )
        generate_stress_test_data.MANIFEST_PATH.write_text(json.dumps(manifest))

        remove_stress_test_data.run()

        captured = capsys.readouterr()
        assert "disagree" in captured.out
        # Nothing was deleted -- manifest still present, rows untouched.
        assert generate_stress_test_data.MANIFEST_PATH.exists()
        assert SalesTransaction.objects.filter(cashier=stress_user).count() == 10
