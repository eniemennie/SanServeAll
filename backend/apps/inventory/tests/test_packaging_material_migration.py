"""
Tests for the Packaging Material product_type promotion (client review,
2026-11-09): the 0022_move_packaging_items_to_packaging_type data
migration, and its read-only preview,
list_packaging_migration_candidates, which runs the exact same query
before `migrate` actually moves anything so the client can review the
list first.

The migration's forward/backward functions are RunPython callables that
only ever call `apps.get_model(...)` -- they don't rely on any
migration-specific model shape -- so they're exercised directly here
against the real, current Product model rather than through Django's
migration executor, matching how apps.inventory.tests already tests its
other management commands (see test_reconcile_inventory_command.py).
"""

import importlib
import io

import pytest
from django.apps import apps as real_apps
from django.core.management import call_command

from apps.inventory.models import Product

pytestmark = pytest.mark.django_db

# The migration module's filename starts with a digit, so it isn't a
# valid dotted-import target (`import x.0022_foo` can't parse) --
# importlib.import_module takes a plain string and isn't subject to that
# restriction.
_migration_module = importlib.import_module(
    "apps.inventory.migrations.0022_move_packaging_items_to_packaging_type"
)
move_packaging_items_forward = _migration_module.move_packaging_items_forward
move_packaging_items_backward = _migration_module.move_packaging_items_backward


class TestMovePackagingItemsDataMigration:
    def test_forward_moves_only_material_rows_tagged_packaging(self):
        """A MATERIAL row tagged category=PACKAGING (the old "Packaging
        vs Food" signal) is moved onto the new PACKAGING product_type,
        and its now-redundant category is cleared. A plain food
        MATERIAL (blank category) and a FINISHED_GOOD are both left
        untouched -- the query is deliberately narrow."""
        cling_wrap = Product.objects.create(
            name="Cling Wrap",
            price="40.00",
            product_type=Product.ProductType.MATERIAL,
            category="PACKAGING",
        )
        flour = Product.objects.create(
            name="Flour", price="45.00", product_type=Product.ProductType.MATERIAL
        )
        latte = Product.objects.create(name="Spanish Latte", price="125.00")

        move_packaging_items_forward(real_apps, None)

        cling_wrap.refresh_from_db()
        flour.refresh_from_db()
        latte.refresh_from_db()
        assert cling_wrap.product_type == Product.ProductType.PACKAGING
        assert cling_wrap.category == ""
        assert flour.product_type == Product.ProductType.MATERIAL
        assert latte.product_type == Product.ProductType.FINISHED_GOOD

    def test_forward_with_nothing_to_move_changes_nothing(self):
        flour = Product.objects.create(
            name="Flour", price="45.00", product_type=Product.ProductType.MATERIAL
        )
        move_packaging_items_forward(real_apps, None)
        flour.refresh_from_db()
        assert flour.product_type == Product.ProductType.MATERIAL

    def test_backward_reverts_packaging_rows_to_tagged_material(self):
        """The reverse migration (for `migrate app <previous>`) undoes
        the forward move: every PACKAGING row goes back to
        product_type=MATERIAL, category=PACKAGING."""
        cling_wrap = Product.objects.create(
            name="Cling Wrap", price="40.00", product_type=Product.ProductType.PACKAGING
        )
        flour = Product.objects.create(
            name="Flour", price="45.00", product_type=Product.ProductType.MATERIAL
        )

        move_packaging_items_backward(real_apps, None)

        cling_wrap.refresh_from_db()
        flour.refresh_from_db()
        assert cling_wrap.product_type == Product.ProductType.MATERIAL
        assert cling_wrap.category == "PACKAGING"
        assert flour.product_type == Product.ProductType.MATERIAL
        assert flour.category == ""

    def test_forward_then_backward_round_trips(self):
        cling_wrap = Product.objects.create(
            name="Cling Wrap",
            price="40.00",
            product_type=Product.ProductType.MATERIAL,
            category="PACKAGING",
        )

        move_packaging_items_forward(real_apps, None)
        cling_wrap.refresh_from_db()
        assert cling_wrap.product_type == Product.ProductType.PACKAGING

        move_packaging_items_backward(real_apps, None)
        cling_wrap.refresh_from_db()
        assert cling_wrap.product_type == Product.ProductType.MATERIAL
        assert cling_wrap.category == "PACKAGING"


class TestListPackagingMigrationCandidatesCommand:
    def test_lists_each_candidate_by_name(self):
        Product.objects.create(
            name="Cling Wrap",
            price="40.00",
            product_type=Product.ProductType.MATERIAL,
            category="PACKAGING",
        )
        Product.objects.create(
            name="16oz Cup",
            price="3.00",
            product_type=Product.ProductType.MATERIAL,
            category="PACKAGING",
        )
        Product.objects.create(
            name="Flour", price="45.00", product_type=Product.ProductType.MATERIAL
        )

        out = io.StringIO()
        call_command("list_packaging_migration_candidates", stdout=out)
        output = out.getvalue()

        assert "2 product(s) will move" in output
        assert "Cling Wrap" in output
        assert "16oz Cup" in output
        assert "Flour" not in output

    def test_reports_nothing_to_move_when_no_candidates_exist(self):
        Product.objects.create(
            name="Flour", price="45.00", product_type=Product.ProductType.MATERIAL
        )

        out = io.StringIO()
        call_command("list_packaging_migration_candidates", stdout=out)
        assert "nothing to move" in out.getvalue()

    def test_does_not_modify_any_data(self):
        """Read-only means read-only -- running the preview command must
        never itself move a candidate, only report it."""
        cling_wrap = Product.objects.create(
            name="Cling Wrap",
            price="40.00",
            product_type=Product.ProductType.MATERIAL,
            category="PACKAGING",
        )

        call_command("list_packaging_migration_candidates", stdout=io.StringIO())

        cling_wrap.refresh_from_db()
        assert cling_wrap.product_type == Product.ProductType.MATERIAL
        assert cling_wrap.category == "PACKAGING"
