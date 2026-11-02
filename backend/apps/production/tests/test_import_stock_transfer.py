"""
Tests for import_stock_transfer -- the real-client-data import for
Stock_Transfer.xlsx. Builds small synthetic workbooks (not the real
client file, which isn't a test fixture) that exercise the same column
shapes and edge cases documented in the command's own module docstring:
the combined Butter Toast/Broas split (including an ODD quantity, unlike
Production_Records' all-even occurrences), DELIVERED/Delivered-Date
consistency, idempotency, and the no-live-Inventory-effect rule.
"""

import openpyxl
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.accounts.models import Branch, Role, User
from apps.inventory.models import Inventory, Product
from apps.production.models import StockTransferOrder

pytestmark = pytest.mark.django_db

HEADERS = [
    "Transfer Ref",
    "Commissary Staff (username)",
    "Source Branch Code",
    "Destination Branch Code",
    "Product Name ",
    "Quantity",
    "Status",
    "Created Date",
    "Delivered Date",
]


def _write_workbook(path, rows):
    """rows: list of (transfer_ref, staff, source_code, dest_code,
    product_name, qty, status, created_date, delivered_date)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADERS)
    for row in rows:
        ws.append(list(row))
    wb.save(path)


@pytest.fixture
def commissary():
    return Branch.objects.create(name="Commissary", code="COMMISSARY", is_commissary=True)


@pytest.fixture
def batangas():
    return Branch.objects.create(name="Batangas", code="BATANGAS")


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


class TestImportStockTransfer:
    def test_dry_run_writes_nothing(self, staff, batangas, product, tmp_path):
        path = tmp_path / "transfer.xlsx"
        _write_workbook(
            path,
            [
                (
                    "TRANSFER-0001",
                    "commissary_staff",
                    "COMMISSARY",
                    "BATANGAS",
                    "Classic Tiramisu",
                    10,
                    "DELIVERED",
                    "2026-07-01",
                    "2026-07-02",
                )
            ],
        )
        call_command("import_stock_transfer", stock_transfer=str(path), dry_run=True)
        assert StockTransferOrder.objects.count() == 0

    def test_creates_order_with_cosmetic_name_resolved(
        self, staff, batangas, cake_8in_round, tmp_path
    ):
        path = tmp_path / "transfer.xlsx"
        _write_workbook(
            path,
            [
                (
                    "TRANSFER-0002",
                    "commissary_staff",
                    "COMMISSARY",
                    "BATANGAS",
                    "Almond (8' Round)",
                    4,
                    "IN_TRANSIT",
                    "2026-07-03",
                    "",
                )
            ],
        )
        call_command("import_stock_transfer", stock_transfer=str(path))

        order = StockTransferOrder.objects.get(transfer_ref="TRANSFER-0002")
        assert order.product == cake_8in_round
        assert order.quantity == 4
        assert order.status == StockTransferOrder.Status.IN_TRANSIT
        assert order.commissary_staff == staff
        assert order.source_branch.code == "COMMISSARY"
        assert order.destination_branch.code == "BATANGAS"
        assert order.delivered_at is None
        assert order.created_at.date() == timezone.datetime(2026, 7, 3).date()

    def test_delivered_status_sets_delivered_at_from_delivered_date(
        self, staff, batangas, product, tmp_path
    ):
        path = tmp_path / "transfer.xlsx"
        _write_workbook(
            path,
            [
                (
                    "TRANSFER-0003",
                    "commissary_staff",
                    "COMMISSARY",
                    "BATANGAS",
                    "Classic Tiramisu",
                    5,
                    "DELIVERED",
                    "2026-07-01",
                    "2026-07-04",
                )
            ],
        )
        call_command("import_stock_transfer", stock_transfer=str(path))

        order = StockTransferOrder.objects.get(transfer_ref="TRANSFER-0003")
        assert order.status == StockTransferOrder.Status.DELIVERED
        assert order.delivered_at is not None
        assert order.delivered_at.date() == timezone.datetime(2026, 7, 4).date()

    def test_delivered_status_without_delivered_date_is_a_hard_error(
        self, staff, batangas, product, tmp_path
    ):
        path = tmp_path / "transfer.xlsx"
        _write_workbook(
            path,
            [
                (
                    "TRANSFER-0004",
                    "commissary_staff",
                    "COMMISSARY",
                    "BATANGAS",
                    "Classic Tiramisu",
                    5,
                    "DELIVERED",
                    "2026-07-01",
                    "",
                )
            ],
        )
        with pytest.raises(CommandError, match="Delivered Date is blank"):
            call_command("import_stock_transfer", stock_transfer=str(path))

    def test_delivered_date_without_delivered_status_is_a_hard_error(
        self, staff, batangas, product, tmp_path
    ):
        path = tmp_path / "transfer.xlsx"
        _write_workbook(
            path,
            [
                (
                    "TRANSFER-0005",
                    "commissary_staff",
                    "COMMISSARY",
                    "BATANGAS",
                    "Classic Tiramisu",
                    5,
                    "PROCESSING",
                    "2026-07-01",
                    "2026-07-02",
                )
            ],
        )
        with pytest.raises(CommandError, match="not DELIVERED"):
            call_command("import_stock_transfer", stock_transfer=str(path))

    def test_combined_shipment_splits_into_two_orders_with_half_quantity(
        self, staff, batangas, butter_toast, broas, tmp_path
    ):
        path = tmp_path / "transfer.xlsx"
        _write_workbook(
            path,
            [
                (
                    "TRANSFER-0257",
                    "commissary_staff",
                    "COMMISSARY",
                    "BATANGAS",
                    "Butter Toast and Broas (Jar)",
                    12,
                    "DELIVERED",
                    "2026-07-01",
                    "2026-07-02",
                )
            ],
        )
        call_command("import_stock_transfer", stock_transfer=str(path))

        bt = StockTransferOrder.objects.get(transfer_ref="TRANSFER-0257-BT")
        br = StockTransferOrder.objects.get(transfer_ref="TRANSFER-0257-BR")
        assert bt.product == butter_toast
        assert br.product == broas
        assert bt.quantity == 6
        assert br.quantity == 6

    def test_combined_shipment_with_odd_quantity_splits_without_losing_a_unit(
        self, staff, batangas, butter_toast, broas, tmp_path
    ):
        """Real client data: TRANSFER-0324 (qty 13) and TRANSFER-0394
        (qty 15) are genuinely odd -- unlike Production_Records.xlsx's
        all-even combined-batch occurrences. Plain `quantity // 2` would
        silently drop a unit here (13 // 2 = 6 + 6 = 12)."""
        path = tmp_path / "transfer.xlsx"
        _write_workbook(
            path,
            [
                (
                    "TRANSFER-0324",
                    "commissary_staff",
                    "COMMISSARY",
                    "BATANGAS",
                    "Butter Toast and Broas (Jar)",
                    13,
                    "IN_TRANSIT",
                    "2026-07-05",
                    "",
                )
            ],
        )
        call_command("import_stock_transfer", stock_transfer=str(path))

        bt = StockTransferOrder.objects.get(transfer_ref="TRANSFER-0324-BT")
        br = StockTransferOrder.objects.get(transfer_ref="TRANSFER-0324-BR")
        assert bt.quantity == 7
        assert br.quantity == 6
        assert bt.quantity + br.quantity == 13

    def test_rerun_is_idempotent_per_transfer_ref(self, staff, batangas, product, tmp_path):
        path = tmp_path / "transfer.xlsx"
        _write_workbook(
            path,
            [
                (
                    "TRANSFER-0006",
                    "commissary_staff",
                    "COMMISSARY",
                    "BATANGAS",
                    "Classic Tiramisu",
                    7,
                    "PROCESSING",
                    "2026-07-01",
                    "",
                )
            ],
        )
        call_command("import_stock_transfer", stock_transfer=str(path))
        call_command("import_stock_transfer", stock_transfer=str(path))
        assert StockTransferOrder.objects.filter(transfer_ref="TRANSFER-0006").count() == 1

    def test_rerun_is_idempotent_for_combined_shipment_children(
        self, staff, batangas, butter_toast, broas, tmp_path
    ):
        path = tmp_path / "transfer.xlsx"
        _write_workbook(
            path,
            [
                (
                    "TRANSFER-0257",
                    "commissary_staff",
                    "COMMISSARY",
                    "BATANGAS",
                    "Butter Toast and Broas (Jar)",
                    12,
                    "DELIVERED",
                    "2026-07-01",
                    "2026-07-02",
                )
            ],
        )
        call_command("import_stock_transfer", stock_transfer=str(path))
        call_command("import_stock_transfer", stock_transfer=str(path))
        assert StockTransferOrder.objects.filter(transfer_ref="TRANSFER-0257-BT").count() == 1
        assert StockTransferOrder.objects.filter(transfer_ref="TRANSFER-0257-BR").count() == 1

    def test_does_not_touch_inventory(self, staff, batangas, product, tmp_path):
        """Core invariant: this is a historical log, not a live transfer --
        it must never create or affect Inventory/InventoryTransaction
        rows, since the current Raw/Packaging snapshot already accounts
        for everything this file records."""
        Inventory.objects.create(branch=staff.branch, product=product, quantity_on_hand=50)
        Inventory.objects.create(branch=batangas, product=product, quantity_on_hand=0)
        path = tmp_path / "transfer.xlsx"
        _write_workbook(
            path,
            [
                (
                    "TRANSFER-0007",
                    "commissary_staff",
                    "COMMISSARY",
                    "BATANGAS",
                    "Classic Tiramisu",
                    10,
                    "DELIVERED",
                    "2026-07-01",
                    "2026-07-02",
                )
            ],
        )
        call_command("import_stock_transfer", stock_transfer=str(path))

        assert Inventory.objects.get(branch=staff.branch, product=product).quantity_on_hand == 50
        assert Inventory.objects.get(branch=batangas, product=product).quantity_on_hand == 0

    def test_unresolvable_product_name_is_a_hard_error(self, staff, batangas, tmp_path):
        path = tmp_path / "transfer.xlsx"
        _write_workbook(
            path,
            [
                (
                    "TRANSFER-0008",
                    "commissary_staff",
                    "COMMISSARY",
                    "BATANGAS",
                    "Nonexistent Product",
                    1,
                    "PROCESSING",
                    "2026-07-01",
                    "",
                )
            ],
        )
        with pytest.raises(CommandError, match="no matching catalog Product"):
            call_command("import_stock_transfer", stock_transfer=str(path))

    def test_unknown_branch_code_is_a_hard_error(self, staff, product, tmp_path):
        path = tmp_path / "transfer.xlsx"
        _write_workbook(
            path,
            [
                (
                    "TRANSFER-0009",
                    "commissary_staff",
                    "COMMISSARY",
                    "NONEXISTENT",
                    "Classic Tiramisu",
                    1,
                    "PROCESSING",
                    "2026-07-01",
                    "",
                )
            ],
        )
        with pytest.raises(CommandError, match="no Branch with code"):
            call_command("import_stock_transfer", stock_transfer=str(path))

    def test_unknown_staff_username_is_a_hard_error(self, commissary, batangas, product, tmp_path):
        path = tmp_path / "transfer.xlsx"
        _write_workbook(
            path,
            [
                (
                    "TRANSFER-0010",
                    "nonexistent_staff",
                    "COMMISSARY",
                    "BATANGAS",
                    "Classic Tiramisu",
                    1,
                    "PROCESSING",
                    "2026-07-01",
                    "",
                )
            ],
        )
        with pytest.raises(CommandError, match="no matching User"):
            call_command("import_stock_transfer", stock_transfer=str(path))

    def test_unrecognized_status_is_a_hard_error(self, staff, batangas, product, tmp_path):
        path = tmp_path / "transfer.xlsx"
        _write_workbook(
            path,
            [
                (
                    "TRANSFER-0011",
                    "commissary_staff",
                    "COMMISSARY",
                    "BATANGAS",
                    "Classic Tiramisu",
                    1,
                    "BOGUS_STATUS",
                    "2026-07-01",
                    "",
                )
            ],
        )
        with pytest.raises(CommandError, match="unrecognized Status"):
            call_command("import_stock_transfer", stock_transfer=str(path))

    def test_missing_required_column_is_a_hard_error(self, tmp_path):
        path = tmp_path / "transfer.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Transfer Ref", "Quantity"])
        ws.append(["TRANSFER-0012", 1])
        wb.save(path)
        with pytest.raises(CommandError, match="missing column"):
            call_command("import_stock_transfer", stock_transfer=str(path))

    def test_file_not_found_is_a_hard_error(self, tmp_path):
        with pytest.raises(CommandError, match="File not found"):
            call_command("import_stock_transfer", stock_transfer=str(tmp_path / "missing.xlsx"))
