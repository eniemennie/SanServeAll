"""
Tests for import_bakery_products -- the real-client-data import for
Bakery_Products.xlsx. Builds small synthetic workbooks (not the real
client file, which isn't a test fixture) that exercise the same shape
and edge cases documented in the command's own module docstring:
multiple price variants per product name folded into distinct catalog
names, BAKERY category tagging, 0-stock provisioning across every
active non-commissary branch, and the get_or_create-not-overwrite
idempotency rule for Inventory rows.
"""

from decimal import Decimal

import openpyxl
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import Branch
from apps.inventory.models import Inventory, Product

pytestmark = pytest.mark.django_db

HEADERS = ["Product Name", "Category", "Description", "Size/Variant", "Price (₱)"]


def _write_workbook(path, rows):
    """rows: list of tuples (name, category, description, variant, price)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADERS)
    for row in rows:
        ws.append(list(row))
    wb.save(path)


@pytest.fixture
def branches():
    return {
        "COMMISSARY": Branch.objects.create(
            name="Commissary", code="COMMISSARY", is_commissary=True
        ),
        "LIPA": Branch.objects.create(name="Lipa City", code="LIPA"),
        "BATANGAS": Branch.objects.create(name="Batangas City", code="BATANGAS"),
    }


class TestImportBakeryProducts:
    def test_dry_run_writes_nothing(self, branches, tmp_path):
        path = tmp_path / "bakery.xlsx"
        _write_workbook(path, [("Banana Bread", "Bakery Corner", None, None, 60)])
        call_command("import_bakery_products", file=str(path), dry_run=True)
        assert Product.objects.count() == 0
        assert Inventory.objects.count() == 0

    def test_product_with_no_variant_keeps_its_bare_name(self, branches, tmp_path):
        path = tmp_path / "bakery.xlsx"
        _write_workbook(path, [("Banana Bread", "Bakery Corner", None, None, 60)])
        call_command("import_bakery_products", file=str(path))

        product = Product.objects.get(name="Banana Bread")
        assert product.price == Decimal("60")
        assert product.product_type == Product.ProductType.FINISHED_GOOD
        assert product.category == Product.Category.BAKERY

    def test_multiple_variants_become_distinct_products(self, branches, tmp_path):
        """Task (client review, 2026-11-09): "Silvanas" appears twice in
        the real file with different sizes/prices -- Product.name's
        unique constraint means these can't share one row, so each
        (name, variant) pair becomes its own fully real catalog Product
        with the variant folded into the name."""
        path = tmp_path / "bakery.xlsx"
        _write_workbook(
            path,
            [
                ("Silvanas", "Silvanas", "Assorted", "Box of 10", 380),
                ("Silvanas", "Silvanas", "Assorted", "Box of 20", 760),
            ],
        )
        call_command("import_bakery_products", file=str(path))

        box_of_10 = Product.objects.get(name="Silvanas (Box of 10)")
        box_of_20 = Product.objects.get(name="Silvanas (Box of 20)")
        assert box_of_10.price == Decimal("380")
        assert box_of_20.price == Decimal("760")
        assert Product.objects.filter(name__istartswith="Silvanas").count() == 2

    def test_duplicate_name_variant_combination_is_rejected(self, branches, tmp_path):
        path = tmp_path / "bakery.xlsx"
        _write_workbook(
            path,
            [
                ("Silvanas", "Silvanas", "Assorted", "Box of 10", 380),
                ("Silvanas", "Silvanas", "Assorted", "Box of 10", 999),
            ],
        )
        with pytest.raises(CommandError, match="appears more than once"):
            call_command("import_bakery_products", file=str(path))

    def test_provisions_zero_stock_at_every_active_non_commissary_branch(self, branches, tmp_path):
        """No quantity/branch data exists in the real file at all -- each
        imported product is provisioned at 0 on hand at every active,
        non-commissary branch (never the commissary itself, and never a
        fabricated non-zero quantity)."""
        path = tmp_path / "bakery.xlsx"
        _write_workbook(path, [("Banana Bread", "Bakery Corner", None, None, 60)])
        call_command("import_bakery_products", file=str(path))

        product = Product.objects.get(name="Banana Bread")
        inventory_branches = set(
            Inventory.objects.filter(product=product).values_list("branch__code", flat=True)
        )
        assert inventory_branches == {"LIPA", "BATANGAS"}
        assert all(inv.quantity_on_hand == 0 for inv in Inventory.objects.filter(product=product))

    def test_rerunning_does_not_reset_real_stock_back_to_zero(self, branches, tmp_path):
        """Idempotency: a second import run must never silently reset
        real, already-adjusted stock back to 0 -- same rule as
        import_raw_and_packaging_materials.py's own Inventory handling."""
        path = tmp_path / "bakery.xlsx"
        _write_workbook(path, [("Banana Bread", "Bakery Corner", None, None, 60)])
        call_command("import_bakery_products", file=str(path))

        product = Product.objects.get(name="Banana Bread")
        inventory = Inventory.objects.get(product=product, branch=branches["LIPA"])
        inventory.quantity_on_hand = 25
        inventory.save(update_fields=["quantity_on_hand"])

        call_command("import_bakery_products", file=str(path))

        inventory.refresh_from_db()
        assert inventory.quantity_on_hand == 25

    def test_rerunning_updates_a_changed_price(self, branches, tmp_path):
        path = tmp_path / "bakery.xlsx"
        _write_workbook(path, [("Banana Bread", "Bakery Corner", None, None, 60)])
        call_command("import_bakery_products", file=str(path))

        _write_workbook(path, [("Banana Bread", "Bakery Corner", None, None, 65)])
        call_command("import_bakery_products", file=str(path))

        product = Product.objects.get(name="Banana Bread")
        assert product.price == Decimal("65")
        assert Product.objects.filter(name="Banana Bread").count() == 1

    def test_missing_file_raises_clear_error(self, branches):
        with pytest.raises(CommandError, match="File not found"):
            call_command("import_bakery_products", file="/no/such/file.xlsx")

    def test_missing_required_column_is_rejected(self, branches, tmp_path):
        path = tmp_path / "bakery.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Product Name", "Price (₱)"])
        ws.append(["Banana Bread", 60])
        wb.save(path)

        with pytest.raises(CommandError, match="Missing expected column"):
            call_command("import_bakery_products", file=str(path))

    def test_non_numeric_price_raises_clear_error(self, branches, tmp_path):
        path = tmp_path / "bakery.xlsx"
        _write_workbook(path, [("Banana Bread", "Bakery Corner", None, None, "N/A")])
        with pytest.raises(CommandError, match="non-numeric price"):
            call_command("import_bakery_products", file=str(path))

    def test_no_active_branches_raises_clear_error(self, tmp_path):
        """No `branches` fixture here -- the DB genuinely has no active,
        non-commissary branches to provision stock at."""
        path = tmp_path / "bakery.xlsx"
        _write_workbook(path, [("Banana Bread", "Bakery Corner", None, None, 60)])
        with pytest.raises(CommandError, match="No active, non-commissary branches"):
            call_command("import_bakery_products", file=str(path))
