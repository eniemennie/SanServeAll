"""
Tests for import_branch_sales_history -- the real-client-data backfill
command that supersedes the earlier, never-merged import_client_data
command. Builds small synthetic workbooks (not the real client files,
which aren't test fixtures) that exercise the same column shapes and
edge cases documented in the command's own module docstring.
"""

from decimal import Decimal

import openpyxl
import pytest
from django.core.management import call_command

from apps.accounts.models import Branch
from apps.pos.models import SalesItem, SalesTransaction

pytestmark = pytest.mark.django_db


def _write_workbook(path, rows, has_store_sales):
    """rows: list of tuples. With has_store_sales=True, each row is
    (date, store, cafe_am, cafe_pm, cafe_total); without it (Alangilan's
    shape), each row is (date, cafe_am, cafe_pm, cafe_total)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales"
    if has_store_sales:
        ws.append(
            [
                "Date",
                "Year",
                "Month",
                "Day of Week",
                "Store Sales",
                "Cafe AM Sales",
                "Cafe PM Sales",
                "Cafe Total Sales",
                "Remarks",
            ]
        )
        for date, store, am, pm, total in rows:
            ws.append(
                [date, date.year, date.month, date.strftime("%A"), store, am, pm, total, None]
            )
    else:
        ws.append(
            [
                "Date",
                "Year",
                "Month",
                "Day of Week",
                "Cafe AM Sales",
                "Cafe PM Sales",
                "Cafe Total Sales",
                "Remarks",
            ]
        )
        for date, am, pm, total in rows:
            ws.append([date, date.year, date.month, date.strftime("%A"), am, pm, total, None])
    wb.save(path)


@pytest.fixture
def batangas_file(tmp_path):
    import datetime

    path = tmp_path / "Batangas_Sales_2024-2026.xlsx"
    _write_workbook(
        path,
        rows=[
            # Day 1: all three streams recorded -> 3 transactions.
            (datetime.date(2024, 1, 4), 9907, 5000, 6000, 11000),
            # Day 2: Store only, cafe entirely blank -> 1 transaction.
            (datetime.date(2024, 1, 5), 11832, None, None, None),
            # Day 3: AM/PM missing, only a Cafe Total given -> 1 "Cafe
            # Total Sales" transaction, not AM+PM, no double counting.
            (datetime.date(2024, 1, 6), None, None, None, 28001),
            # Day 4: everything blank -> zero transactions.
            (datetime.date(2024, 1, 7), None, None, None, None),
            # Day 5: an explicit recorded zero for Store Sales -- must be
            # imported as a real zero-amount sale, not skipped.
            (datetime.date(2024, 1, 8), 0, None, None, None),
        ],
        has_store_sales=True,
    )
    return str(path)


@pytest.fixture
def alangilan_file(tmp_path):
    import datetime

    path = tmp_path / "Alangilan_Sales_2024-2026.xlsx"
    _write_workbook(
        path,
        rows=[
            (datetime.date(2025, 9, 1), 14934.5, 14009.75, 28944.25),
        ],
        has_store_sales=False,
    )
    return str(path)


class TestParsingAndStreamRules:
    def test_all_three_streams_recorded_creates_three_transactions(self, batangas_file):
        call_command("import_branch_sales_history", batangas=batangas_file)

        branch = Branch.objects.get(code="BATANGAS")
        day1_items = SalesItem.objects.filter(
            transaction__branch=branch, transaction__completed_at__date="2024-01-04"
        )
        labels = sorted(i.custom_name for i in day1_items)
        assert labels == [
            "Cafe AM Sales (Imported)",
            "Cafe PM Sales (Imported)",
            "Store Sales (Imported)",
        ]

    def test_store_only_day_creates_exactly_one_transaction(self, batangas_file):
        call_command("import_branch_sales_history", batangas=batangas_file)

        branch = Branch.objects.get(code="BATANGAS")
        day2_items = SalesItem.objects.filter(
            transaction__branch=branch, transaction__completed_at__date="2024-01-05"
        )
        assert day2_items.count() == 1
        assert day2_items.first().custom_name == "Store Sales (Imported)"
        assert day2_items.first().unit_price == Decimal("11832")

    def test_missing_am_pm_falls_back_to_total_without_double_counting(self, batangas_file):
        call_command("import_branch_sales_history", batangas=batangas_file)

        branch = Branch.objects.get(code="BATANGAS")
        day3_items = SalesItem.objects.filter(
            transaction__branch=branch, transaction__completed_at__date="2024-01-06"
        )
        assert day3_items.count() == 1
        assert day3_items.first().custom_name == "Cafe Total Sales (Imported)"
        assert day3_items.first().unit_price == Decimal("28001")

    def test_fully_blank_day_creates_nothing(self, batangas_file):
        call_command("import_branch_sales_history", batangas=batangas_file)

        branch = Branch.objects.get(code="BATANGAS")
        assert not SalesTransaction.objects.filter(
            branch=branch, completed_at__date="2024-01-07"
        ).exists()

    def test_explicit_recorded_zero_is_imported_not_skipped(self, batangas_file):
        """The critical blank-vs-zero distinction: a genuine recorded 0
        must still produce a real transaction, never be confused with a
        day that simply wasn't recorded at all."""
        call_command("import_branch_sales_history", batangas=batangas_file)

        branch = Branch.objects.get(code="BATANGAS")
        day5_items = SalesItem.objects.filter(
            transaction__branch=branch, transaction__completed_at__date="2024-01-08"
        )
        assert day5_items.count() == 1
        assert day5_items.first().unit_price == Decimal("0")


class TestAlangilanWithoutStoreSales:
    def test_alangilan_shape_parses_without_a_store_sales_column(self, alangilan_file):
        call_command("import_branch_sales_history", alangilan=alangilan_file)

        branch = Branch.objects.get(code="ALANGILAN")
        assert branch.is_kahero_branch is True  # unchanged -- live data still goes via KaHero

        items = SalesItem.objects.filter(transaction__branch=branch)
        labels = sorted(i.custom_name for i in items)
        assert labels == ["Cafe AM Sales (Imported)", "Cafe PM Sales (Imported)"]


class TestIdempotencyAndDryRun:
    def test_running_twice_does_not_create_duplicates(self, batangas_file):
        call_command("import_branch_sales_history", batangas=batangas_file)
        first_count = SalesTransaction.objects.count()

        call_command("import_branch_sales_history", batangas=batangas_file)
        second_count = SalesTransaction.objects.count()

        assert first_count == second_count
        assert first_count > 0

    def test_dry_run_writes_nothing(self, batangas_file):
        call_command("import_branch_sales_history", batangas=batangas_file, dry_run=True)

        assert SalesTransaction.objects.count() == 0
        assert not Branch.objects.filter(code="BATANGAS").exists()

    def test_requires_at_least_one_branch_file(self):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            call_command("import_branch_sales_history")


class TestImportedTransactionsAreAlreadyCompleted:
    def test_transactions_are_completed_cash_sales_with_a_deactivated_cashier(self, batangas_file):
        """Historical backfill must never create a real, usable login --
        same pattern as the original (unmerged) import_client_data
        command for this exact reason."""
        call_command("import_branch_sales_history", batangas=batangas_file)

        tx = SalesTransaction.objects.filter(branch__code="BATANGAS").first()
        assert tx.status == SalesTransaction.Status.COMPLETED
        assert tx.payment_method == SalesTransaction.PaymentMethod.CASH
        assert tx.cashier.is_active is False
        assert not tx.cashier.has_usable_password()
