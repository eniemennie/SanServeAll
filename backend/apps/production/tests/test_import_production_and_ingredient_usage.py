"""
Tests for import_production_and_ingredient_usage -- the real-client-data
import for Production_Records.xlsx / Ingredient_Usage.xlsx. Builds small
synthetic workbooks (not the real client files, which aren't test
fixtures) that exercise the same column shapes and edge cases documented
in the command's own module docstring: Decimal quantities, the
PENDING-quality batches, the combined Butter Toast/Broas 50/50 split
(for both output and ingredient usage), and the no-live-deduction rule.
"""

from decimal import Decimal

import openpyxl
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import Branch, Role, User
from apps.inventory.models import Inventory, Product
from apps.production.models import IngredientUsage, ProductionRecord

pytestmark = pytest.mark.django_db

PRODUCTION_HEADERS = [
    "Batch Ref",
    "Commissary Staff (username)",
    "Product Name (reference)",
    "Quantity Produced",
    "Supplier",
    "Quality",
    "Status",
    "Production Date",
    "Notes",
    "Data Source",
]
USAGE_HEADERS = ["Batch Ref (FK)", "Material Name", "Quantity Used", "Unit of Measure "]


def _write_production_workbook(path, rows):
    """rows: list of (batch_ref, staff, product_name, qty, quality, status, date, notes)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(PRODUCTION_HEADERS)
    for batch_ref, staff, product_name, qty, quality, status, date, notes in rows:
        ws.append(
            [batch_ref, staff, product_name, qty, None, quality, status, date, notes, "ESTIMATED"]
        )
    wb.save(path)


def _write_usage_workbook(path, rows):
    """rows: list of (batch_ref, material_name, qty_used, unit)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(USAGE_HEADERS)
    for row in rows:
        ws.append(list(row))
    wb.save(path)


@pytest.fixture
def commissary():
    return Branch.objects.create(name="Commissary", code="COMMISSARY", is_commissary=True)


@pytest.fixture
def staff(commissary):
    role, _ = Role.objects.get_or_create(name=Role.COMMISSARY_STAFF)
    return User.objects.create_user(username="commissary_staff", role=role, branch=commissary)


@pytest.fixture
def product():
    return Product.objects.create(
        name="Classic Tiramisu", price="190.00", product_type=Product.ProductType.FINISHED_GOOD
    )


@pytest.fixture
def cake_8in_round():
    """Matches PRODUCT_NAME_MAP's "Almond (8' Round)" -> "Almond (8in Round)"."""
    return Product.objects.create(
        name="Almond (8in Round)", price="860.00", product_type=Product.ProductType.FINISHED_GOOD
    )


@pytest.fixture
def butter_toast():
    return Product.objects.create(
        name="Butter Toast (Jar)", price="140.00", product_type=Product.ProductType.FINISHED_GOOD
    )


@pytest.fixture
def broas():
    return Product.objects.create(
        name="Broas (Jar)", price="140.00", product_type=Product.ProductType.FINISHED_GOOD
    )


@pytest.fixture
def flour():
    return Product.objects.create(
        name="Flour", price="55.00", product_type=Product.ProductType.MATERIAL
    )


class TestImportProductionAndIngredientUsage:
    def test_requires_at_least_one_file(self):
        with pytest.raises(CommandError, match="Pass at least one"):
            call_command("import_production_and_ingredient_usage")

    def test_usage_alone_requires_production_records(self, tmp_path):
        path = tmp_path / "usage.xlsx"
        _write_usage_workbook(path, [("BATCH-0001", "Flour", 1, "kg")])
        with pytest.raises(CommandError, match="requires --production-records"):
            call_command("import_production_and_ingredient_usage", ingredient_usage=str(path))

    def test_dry_run_writes_nothing(self, staff, product, tmp_path):
        path = tmp_path / "prod.xlsx"
        _write_production_workbook(
            path,
            [
                (
                    "BATCH-0001",
                    "commissary_staff",
                    "Classic Tiramisu",
                    18,
                    "PASS",
                    "COMPLETED",
                    "2026-05-06",
                    "",
                )
            ],
        )
        call_command(
            "import_production_and_ingredient_usage", production_records=str(path), dry_run=True
        )
        assert ProductionRecord.objects.count() == 0

    def test_creates_a_production_record_with_backdated_date(self, staff, product, tmp_path):
        path = tmp_path / "prod.xlsx"
        _write_production_workbook(
            path,
            [
                (
                    "BATCH-0001",
                    "commissary_staff",
                    "Classic Tiramisu",
                    18,
                    "PASS",
                    "COMPLETED",
                    "2026-05-06",
                    "Weekend stock.",
                )
            ],
        )
        call_command("import_production_and_ingredient_usage", production_records=str(path))

        record = ProductionRecord.objects.get(batch_ref="BATCH-0001")
        assert record.product == product
        assert record.commissary_staff == staff
        assert record.quantity_produced == 18
        assert record.quality == ProductionRecord.Quality.PASS
        assert record.status == ProductionRecord.Status.COMPLETED
        assert record.notes == "Weekend stock."
        assert record.created_at.date().isoformat() == "2026-05-06"

    def test_does_not_touch_inventory_or_create_transactions(
        self, staff, product, commissary, tmp_path
    ):
        """Core correctness rule: Raw_Materials.xlsx's snapshot is
        already net of this historical consumption -- replaying it
        through the live deduction path would double-count."""
        from apps.inventory.models import InventoryTransaction

        path = tmp_path / "prod.xlsx"
        _write_production_workbook(
            path,
            [
                (
                    "BATCH-0001",
                    "commissary_staff",
                    "Classic Tiramisu",
                    18,
                    "PASS",
                    "COMPLETED",
                    "2026-05-06",
                    "",
                )
            ],
        )
        call_command("import_production_and_ingredient_usage", production_records=str(path))

        assert Inventory.objects.count() == 0
        assert InventoryTransaction.objects.count() == 0

    def test_pending_quality_is_imported_honestly(self, staff, product, tmp_path):
        path = tmp_path / "prod.xlsx"
        _write_production_workbook(
            path,
            [
                (
                    "BATCH-0001",
                    "commissary_staff",
                    "Classic Tiramisu",
                    10,
                    "PENDING",
                    "IN_PROGRESS",
                    "2026-06-01",
                    "",
                )
            ],
        )
        call_command("import_production_and_ingredient_usage", production_records=str(path))
        record = ProductionRecord.objects.get(batch_ref="BATCH-0001")
        assert record.quality == ProductionRecord.Quality.PENDING
        assert record.status == ProductionRecord.Status.IN_PROGRESS

    def test_cosmetic_name_is_mapped_to_the_real_catalog_product(
        self, staff, cake_8in_round, tmp_path
    ):
        path = tmp_path / "prod.xlsx"
        _write_production_workbook(
            path,
            [
                (
                    "BATCH-0001",
                    "commissary_staff",
                    "Almond (8' Round)",
                    6,
                    "PASS",
                    "COMPLETED",
                    "2026-05-04",
                    "",
                )
            ],
        )
        call_command("import_production_and_ingredient_usage", production_records=str(path))
        record = ProductionRecord.objects.get(batch_ref="BATCH-0001")
        assert record.product == cake_8in_round

    def test_unmapped_product_name_raises_a_clear_error(self, staff, tmp_path):
        path = tmp_path / "prod.xlsx"
        _write_production_workbook(
            path,
            [
                (
                    "BATCH-0001",
                    "commissary_staff",
                    "Mystery Cake",
                    6,
                    "PASS",
                    "COMPLETED",
                    "2026-05-04",
                    "",
                )
            ],
        )
        with pytest.raises(CommandError, match="Mystery Cake"):
            call_command("import_production_and_ingredient_usage", production_records=str(path))

    def test_unknown_staff_username_raises_a_clear_error(self, product, tmp_path):
        path = tmp_path / "prod.xlsx"
        _write_production_workbook(
            path,
            [
                (
                    "BATCH-0001",
                    "nonexistent_staff",
                    "Classic Tiramisu",
                    6,
                    "PASS",
                    "COMPLETED",
                    "2026-05-04",
                    "",
                )
            ],
        )
        with pytest.raises(CommandError, match="nonexistent_staff"):
            call_command("import_production_and_ingredient_usage", production_records=str(path))

    def test_rerun_skips_an_existing_batch_ref(self, staff, product, tmp_path):
        path = tmp_path / "prod.xlsx"
        _write_production_workbook(
            path,
            [
                (
                    "BATCH-0001",
                    "commissary_staff",
                    "Classic Tiramisu",
                    18,
                    "PASS",
                    "COMPLETED",
                    "2026-05-06",
                    "",
                )
            ],
        )
        call_command("import_production_and_ingredient_usage", production_records=str(path))
        call_command("import_production_and_ingredient_usage", production_records=str(path))
        assert ProductionRecord.objects.filter(batch_ref="BATCH-0001").count() == 1

    def test_combined_batch_splits_into_two_records_with_half_quantity(
        self, staff, butter_toast, broas, tmp_path
    ):
        path = tmp_path / "prod.xlsx"
        _write_production_workbook(
            path,
            [
                (
                    "BATCH-0181",
                    "commissary_staff",
                    "Butter Toast and Broas (Jar)",
                    30,
                    "PASS",
                    "COMPLETED",
                    "2026-07-01",
                    "",
                )
            ],
        )
        call_command("import_production_and_ingredient_usage", production_records=str(path))

        bt = ProductionRecord.objects.get(batch_ref="BATCH-0181-BT")
        br = ProductionRecord.objects.get(batch_ref="BATCH-0181-BR")
        assert bt.product == butter_toast
        assert br.product == broas
        assert bt.quantity_produced == 15
        assert br.quantity_produced == 15
        assert not ProductionRecord.objects.filter(batch_ref="BATCH-0181").exists()

    def test_ingredient_usage_is_attached_to_the_matching_batch(
        self, staff, product, flour, tmp_path
    ):
        prod_path = tmp_path / "prod.xlsx"
        usage_path = tmp_path / "usage.xlsx"
        _write_production_workbook(
            prod_path,
            [
                (
                    "BATCH-0001",
                    "commissary_staff",
                    "Classic Tiramisu",
                    18,
                    "PASS",
                    "COMPLETED",
                    "2026-05-06",
                    "",
                )
            ],
        )
        _write_usage_workbook(usage_path, [("BATCH-0001", "Flour", 2.5, "kg")])

        call_command(
            "import_production_and_ingredient_usage",
            production_records=str(prod_path),
            ingredient_usage=str(usage_path),
        )

        record = ProductionRecord.objects.get(batch_ref="BATCH-0001")
        usage = IngredientUsage.objects.get(production_record=record)
        assert usage.material == flour
        assert usage.quantity_used == Decimal("2.5")

    def test_fractional_quantity_used_is_stored_exactly(self, staff, product, flour, tmp_path):
        prod_path = tmp_path / "prod.xlsx"
        usage_path = tmp_path / "usage.xlsx"
        _write_production_workbook(
            prod_path,
            [
                (
                    "BATCH-0001",
                    "commissary_staff",
                    "Classic Tiramisu",
                    18,
                    "PASS",
                    "COMPLETED",
                    "2026-05-06",
                    "",
                )
            ],
        )
        _write_usage_workbook(usage_path, [("BATCH-0001", "Flour", 0.019, "kg")])

        call_command(
            "import_production_and_ingredient_usage",
            production_records=str(prod_path),
            ingredient_usage=str(usage_path),
        )
        usage = IngredientUsage.objects.get(production_record__batch_ref="BATCH-0001")
        assert usage.quantity_used == Decimal("0.019")

    def test_combined_batch_usage_is_split_evenly_across_both_children(
        self, staff, butter_toast, broas, flour, tmp_path
    ):
        prod_path = tmp_path / "prod.xlsx"
        usage_path = tmp_path / "usage.xlsx"
        _write_production_workbook(
            prod_path,
            [
                (
                    "BATCH-0181",
                    "commissary_staff",
                    "Butter Toast and Broas (Jar)",
                    30,
                    "PASS",
                    "COMPLETED",
                    "2026-07-01",
                    "",
                )
            ],
        )
        _write_usage_workbook(usage_path, [("BATCH-0181", "Flour", 4.0, "kg")])

        call_command(
            "import_production_and_ingredient_usage",
            production_records=str(prod_path),
            ingredient_usage=str(usage_path),
        )

        bt_usage = IngredientUsage.objects.get(production_record__batch_ref="BATCH-0181-BT")
        br_usage = IngredientUsage.objects.get(production_record__batch_ref="BATCH-0181-BR")
        assert bt_usage.quantity_used == Decimal("2.0")
        assert br_usage.quantity_used == Decimal("2.0")

    def test_unmapped_material_name_raises_a_clear_error(self, staff, product, tmp_path):
        prod_path = tmp_path / "prod.xlsx"
        usage_path = tmp_path / "usage.xlsx"
        _write_production_workbook(
            prod_path,
            [
                (
                    "BATCH-0001",
                    "commissary_staff",
                    "Classic Tiramisu",
                    18,
                    "PASS",
                    "COMPLETED",
                    "2026-05-06",
                    "",
                )
            ],
        )
        _write_usage_workbook(usage_path, [("BATCH-0001", "Mystery Material", 1, "kg")])

        with pytest.raises(CommandError, match="Mystery Material"):
            call_command(
                "import_production_and_ingredient_usage",
                production_records=str(prod_path),
                ingredient_usage=str(usage_path),
            )

    def test_missing_production_file_raises_a_clear_error(self):
        with pytest.raises(CommandError, match="File not found"):
            call_command(
                "import_production_and_ingredient_usage",
                production_records="/nonexistent/path.xlsx",
            )

    def test_missing_required_column_raises_a_clear_error(self, tmp_path):
        path = tmp_path / "prod.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Batch Ref"])
        ws.append(["BATCH-0001"])
        wb.save(path)

        with pytest.raises(CommandError, match="missing column"):
            call_command("import_production_and_ingredient_usage", production_records=str(path))
