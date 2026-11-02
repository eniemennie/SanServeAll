"""
Tests for import_raw_and_packaging_materials -- the real-client-data
import for Raw_Materials.xlsx / Packaging_Materials.xlsx. Builds small
synthetic workbooks (not the real client files, which aren't test
fixtures) that exercise the same column shapes and edge cases documented
in the command's own module docstring: fractional Decimal quantities,
per-material catalog fields shared across branch rows, and the
get_or_create-not-overwrite idempotency rule.
"""

from decimal import Decimal

import openpyxl
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import Branch
from apps.inventory.models import Inventory, Product

pytestmark = pytest.mark.django_db

HEADERS = [
    "Item No.",
    "Name",
    "Supplier",
    "Product Type",
    "Unit of Measure",
    "Unit Cost",
    "Quantity on Hand ",
    "Reorder Threshold ",
    "Branch Code",
    "Is Active",
]


def _write_workbook(path, rows):
    """rows: list of tuples (name, supplier, unit, unit_cost, qty, reorder,
    branch_code, is_active)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADERS)
    for i, row in enumerate(rows, start=1):
        name, supplier, unit, unit_cost, qty, reorder, branch_code, is_active = row
        ws.append(
            [i, name, supplier, "MATERIAL", unit, unit_cost, qty, reorder, branch_code, is_active]
        )
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


class TestImportRawAndPackagingMaterials:
    def test_requires_at_least_one_file(self):
        with pytest.raises(CommandError, match="Pass at least one"):
            call_command("import_raw_and_packaging_materials")

    def test_dry_run_writes_nothing(self, branches, tmp_path):
        path = tmp_path / "raw.xlsx"
        _write_workbook(
            path, [("White sugar", "Juan Store", "kg", 82.0, 113.9, 37.3, "COMMISSARY", True)]
        )
        call_command("import_raw_and_packaging_materials", raw_materials=str(path), dry_run=True)
        assert Product.objects.count() == 0
        assert Inventory.objects.count() == 0

    def test_creates_one_product_and_one_inventory_row_per_branch(self, branches, tmp_path):
        """A material repeating across branches (the client's own data
        shape) becomes exactly one Product and one Inventory row per
        branch it appears at, not a duplicate Product per row."""
        path = tmp_path / "raw.xlsx"
        _write_workbook(
            path,
            [
                ("White sugar", "Juan Store", "kg", 82.0, 113.9, 37.3, "COMMISSARY", True),
                ("White sugar", "Juan Store", "kg", 82.0, 40.0, 10.0, "LIPA", True),
                ("White sugar", "Juan Store", "kg", 82.0, 25.0, 5.0, "BATANGAS", True),
            ],
        )
        call_command("import_raw_and_packaging_materials", raw_materials=str(path))

        assert Product.objects.filter(name="White sugar").count() == 1
        product = Product.objects.get(name="White sugar")
        assert product.product_type == Product.ProductType.MATERIAL
        assert product.unit_of_measure == Product.UnitOfMeasure.KILOGRAM
        assert product.supplier == "Juan Store"
        assert product.price == Decimal("82.00")
        assert Inventory.objects.filter(product=product).count() == 3

    def test_fractional_quantities_are_stored_exactly(self, branches, tmp_path):
        """Core decision this command exists to honor: 0.3kg must stay
        0.3, never round to 0 (false stockout) or 1 (hides a near-empty
        supply)."""
        path = tmp_path / "raw.xlsx"
        _write_workbook(path, [("Ice", "Pantoja", "kg", 6.0, 0.3, 0.5, "COMMISSARY", True)])
        call_command("import_raw_and_packaging_materials", raw_materials=str(path))

        inv = Inventory.objects.get(product__name="Ice")
        assert inv.quantity_on_hand == Decimal("0.3")
        assert inv.reorder_threshold == Decimal("0.5")
        assert inv.is_low_stock is True

    def test_unmapped_unit_of_measure_raises_a_clear_error(self, branches, tmp_path):
        path = tmp_path / "raw.xlsx"
        _write_workbook(
            path, [("Mystery Item", "Unknown Co", "fortnight", 1.0, 1, 1, "COMMISSARY", True)]
        )
        with pytest.raises(CommandError, match="fortnight"):
            call_command("import_raw_and_packaging_materials", raw_materials=str(path))

    def test_unknown_branch_code_raises_a_clear_error(self, branches, tmp_path):
        path = tmp_path / "raw.xlsx"
        _write_workbook(
            path, [("White sugar", "Juan Store", "kg", 82.0, 10, 2, "NONEXISTENT", True)]
        )
        with pytest.raises(CommandError, match="NONEXISTENT"):
            call_command("import_raw_and_packaging_materials", raw_materials=str(path))

    def test_rerun_does_not_overwrite_existing_inventory_by_default(self, branches, tmp_path):
        """A real stock movement may have happened against this row
        since the first import -- a second accidental run must not
        silently reset it back to the stale snapshot."""
        path = tmp_path / "raw.xlsx"
        _write_workbook(
            path, [("White sugar", "Juan Store", "kg", 82.0, 113.9, 37.3, "COMMISSARY", True)]
        )
        call_command("import_raw_and_packaging_materials", raw_materials=str(path))

        inv = Inventory.objects.get(product__name="White sugar")
        inv.quantity_on_hand = Decimal("5.0")  # simulates real stock movement since import
        inv.save()

        call_command("import_raw_and_packaging_materials", raw_materials=str(path))
        inv.refresh_from_db()
        assert inv.quantity_on_hand == Decimal("5.0")  # untouched

    def test_force_overwrites_existing_inventory(self, branches, tmp_path):
        path = tmp_path / "raw.xlsx"
        _write_workbook(
            path, [("White sugar", "Juan Store", "kg", 82.0, 113.9, 37.3, "COMMISSARY", True)]
        )
        call_command("import_raw_and_packaging_materials", raw_materials=str(path))

        inv = Inventory.objects.get(product__name="White sugar")
        inv.quantity_on_hand = Decimal("5.0")
        inv.save()

        call_command("import_raw_and_packaging_materials", raw_materials=str(path), force=True)
        inv.refresh_from_db()
        assert inv.quantity_on_hand == Decimal("113.90")

    def test_product_catalog_fields_stay_in_sync_on_rerun(self, branches, tmp_path):
        """Unlike Inventory quantities, Product catalog fields (price,
        supplier, unit, is_active) are always kept in sync -- the
        client's file is the source of truth for these regardless of
        --force, same convention as seed_menu_catalog.py."""
        path = tmp_path / "raw.xlsx"
        _write_workbook(
            path, [("White sugar", "Juan Store", "kg", 82.0, 113.9, 37.3, "COMMISSARY", True)]
        )
        call_command("import_raw_and_packaging_materials", raw_materials=str(path))

        _write_workbook(
            path,
            [("White sugar", "New Supplier Co", "kg", 90.0, 113.9, 37.3, "COMMISSARY", True)],
        )
        call_command("import_raw_and_packaging_materials", raw_materials=str(path))

        product = Product.objects.get(name="White sugar")
        assert product.supplier == "New Supplier Co"
        assert product.price == Decimal("90.00")

    def test_inactive_material_is_imported_as_inactive(self, branches, tmp_path):
        path = tmp_path / "raw.xlsx"
        _write_workbook(
            path, [("Taco mix", "Restaurant Depot", "kg", 380.0, 1.5, 0.8, "COMMISSARY", False)]
        )
        call_command("import_raw_and_packaging_materials", raw_materials=str(path))
        assert Product.objects.get(name="Taco mix").is_active is False

    def test_both_files_can_be_imported_together(self, branches, tmp_path):
        raw_path = tmp_path / "raw.xlsx"
        pack_path = tmp_path / "packaging.xlsx"
        _write_workbook(
            raw_path, [("White sugar", "Juan Store", "kg", 82.0, 113.9, 37.3, "COMMISSARY", True)]
        )
        _write_workbook(
            pack_path,
            [
                (
                    "Packaging hot (blue cup+lid+ecobag)",
                    "Dale Prints / Shopee",
                    "piece",
                    9.5,
                    388,
                    129,
                    "COMMISSARY",
                    True,
                )
            ],
        )
        call_command(
            "import_raw_and_packaging_materials",
            raw_materials=str(raw_path),
            packaging_materials=str(pack_path),
        )
        assert Product.objects.filter(name="White sugar").exists()
        assert Product.objects.filter(name__icontains="Packaging hot").exists()
        pack_product = Product.objects.get(name__icontains="Packaging hot")
        assert pack_product.unit_of_measure == Product.UnitOfMeasure.PIECE

    def test_missing_file_raises_a_clear_error(self, branches):
        with pytest.raises(CommandError, match="File not found"):
            call_command(
                "import_raw_and_packaging_materials", raw_materials="/nonexistent/path.xlsx"
            )

    def test_missing_required_column_raises_a_clear_error(self, branches, tmp_path):
        path = tmp_path / "raw.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Name", "Supplier"])  # missing everything else
        ws.append(["White sugar", "Juan Store"])
        wb.save(path)

        with pytest.raises(CommandError, match="missing expected column"):
            call_command("import_raw_and_packaging_materials", raw_materials=str(path))
