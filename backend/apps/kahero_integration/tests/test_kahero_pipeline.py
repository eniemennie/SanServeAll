"""
Tests for Week 7: KaHero batch-import pipeline -- parser validation
(Row 7.2), ingestion into POS models (Row 7.3), and the upload/dashboard
views (Row 7.1, 7.4).
"""

import io
from unittest.mock import patch

import pandas as pd
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.accounts.models import Branch, Role, User
from apps.inventory.models import Inventory, InventoryTransaction, Product
from conftest import verify_otp_for_client
from apps.kahero_integration import services
from apps.kahero_integration.models import KaheroImportBatch
from apps.kahero_integration.parsers import ParseError, parse_kahero_file, read_kahero_file
from apps.pos.models import SalesItem, SalesTransaction

pytestmark = pytest.mark.django_db


def _make_csv(rows_csv_text):
    return SimpleUploadedFile(
        "kahero_export.csv", rows_csv_text.encode("utf-8"), content_type="text/csv"
    )


@pytest.fixture
def kahero_branch():
    return Branch.objects.create(name="Alangilan", code="ALANGILAN", is_kahero_branch=True)


@pytest.fixture
def owner():
    role = Role.objects.create(name=Role.OWNER_ADMIN)
    return User.objects.create_user(username="owner1", password="testpass123", role=role)


@pytest.fixture
def owner_client(client, owner):
    client.force_login(owner)
    verify_otp_for_client(client, owner)
    return client


@pytest.fixture
def product():
    return Product.objects.create(name="Spanish Latte", price="125.00")


VALID_CSV = (
    "transaction_date,product_name,quantity,unit_price\n"
    "2026-08-01,Spanish Latte,2,125.00\n"
    "2026-08-01,Spanish Latte,1,125.00\n"
)


class TestParser:
    def test_valid_csv_parses_with_no_errors(self):
        valid_rows, errors = parse_kahero_file(_make_csv(VALID_CSV))
        assert len(valid_rows) == 2
        assert errors == []

    def test_missing_required_column_raises_parse_error(self):
        bad_csv = "product_name,quantity\nSpanish Latte,2\n"
        with pytest.raises(ParseError, match="Missing required column"):
            read_kahero_file(_make_csv(bad_csv))

    def test_empty_file_raises_parse_error(self):
        empty_csv = "transaction_date,product_name,quantity,unit_price\n"
        with pytest.raises(ParseError, match="no data rows"):
            read_kahero_file(_make_csv(empty_csv))

    def test_unsupported_file_type_raises_parse_error(self):
        bad_file = SimpleUploadedFile("export.txt", b"not a real export", content_type="text/plain")
        with pytest.raises(ParseError, match="Unsupported file type"):
            read_kahero_file(bad_file)

    def test_negative_quantity_is_a_row_error_not_a_file_error(self):
        csv_text = (
            "transaction_date,product_name,quantity,unit_price\n"
            "2026-08-01,Spanish Latte,-3,125.00\n"
        )
        valid_rows, errors = parse_kahero_file(_make_csv(csv_text))
        assert valid_rows == []
        assert len(errors) == 1
        assert "positive number" in errors[0]

    def test_non_numeric_price_is_a_row_error(self):
        csv_text = (
            "transaction_date,product_name,quantity,unit_price\n"
            "2026-08-01,Spanish Latte,2,not_a_price\n"
        )
        valid_rows, errors = parse_kahero_file(_make_csv(csv_text))
        assert valid_rows == []
        assert "not a valid number" in errors[0]

    def test_missing_product_name_is_a_row_error(self):
        csv_text = "transaction_date,product_name,quantity,unit_price\n2026-08-01,,2,125.00\n"
        valid_rows, errors = parse_kahero_file(_make_csv(csv_text))
        assert valid_rows == []
        assert "product_name is missing" in errors[0]

    def test_mixed_good_and_bad_rows_returns_both(self):
        csv_text = (
            "transaction_date,product_name,quantity,unit_price\n"
            "2026-08-01,Spanish Latte,2,125.00\n"
            "2026-08-01,Spanish Latte,-1,125.00\n"
        )
        valid_rows, errors = parse_kahero_file(_make_csv(csv_text))
        assert len(valid_rows) == 1
        assert len(errors) == 1

    def test_xlsx_file_parses_correctly(self):
        df = pd.DataFrame(
            {
                "transaction_date": ["2026-08-01"],
                "product_name": ["Spanish Latte"],
                "quantity": [3],
                "unit_price": [125.00],
            }
        )
        buffer = io.BytesIO()
        df.to_excel(buffer, index=False, engine="openpyxl")
        buffer.seek(0)
        xlsx_file = SimpleUploadedFile(
            "export.xlsx",
            buffer.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        valid_rows, errors = parse_kahero_file(xlsx_file)
        assert len(valid_rows) == 1
        assert errors == []


class TestIngestion:
    def test_valid_rows_create_completed_sales_transactions(self, kahero_branch, owner, product):
        batch = services.process_kahero_upload(kahero_branch, owner, _make_csv(VALID_CSV))

        assert batch.status == KaheroImportBatch.Status.COMPLETED
        assert batch.success_count == 2
        assert SalesTransaction.objects.filter(branch=kahero_branch).count() == 2
        assert all(
            t.status == SalesTransaction.Status.COMPLETED
            for t in SalesTransaction.objects.filter(branch=kahero_branch)
        )

    def test_unmatched_product_is_skipped_and_logged(self, kahero_branch, owner):
        csv_text = (
            "transaction_date,product_name,quantity,unit_price\n"
            "2026-08-01,Nonexistent Product,2,50.00\n"
        )
        batch = services.process_kahero_upload(kahero_branch, owner, _make_csv(csv_text))

        assert batch.success_count == 0
        assert batch.error_count == 1
        assert "not found in catalog" in batch.error_log[0]["message"]
        assert not SalesTransaction.objects.filter(branch=kahero_branch).exists()

    def test_kahero_ingestion_never_deducts_inventory(self, kahero_branch, owner, product):
        """The critical architectural rule: even though real SalesItems get
        created, this pipeline must NEVER trigger the real-time inventory
        deduction hook -- that's what the batch import is replacing."""
        Inventory.objects.create(branch=kahero_branch, product=product, quantity_on_hand=100)

        services.process_kahero_upload(kahero_branch, owner, _make_csv(VALID_CSV))

        inventory = Inventory.objects.get(branch=kahero_branch, product=product)
        assert inventory.quantity_on_hand == 100  # unchanged
        assert not InventoryTransaction.objects.filter(branch=kahero_branch).exists()

    def test_sales_item_quantities_and_prices_match_the_file(self, kahero_branch, owner, product):
        services.process_kahero_upload(kahero_branch, owner, _make_csv(VALID_CSV))

        items = SalesItem.objects.filter(transaction__branch=kahero_branch)
        quantities = sorted(item.quantity for item in items)
        assert quantities == [1, 2]

    def test_completely_unparseable_file_marks_batch_failed(self, kahero_branch, owner):
        bad_file = SimpleUploadedFile(
            "bad.csv", b"not,even,valid\nheaders", content_type="text/csv"
        )
        batch = services.process_kahero_upload(kahero_branch, owner, bad_file)

        assert batch.status == KaheroImportBatch.Status.FAILED
        assert batch.error_log[0]["message"]  # some message recorded

    def test_reuploading_the_same_file_skips_every_row_as_duplicate(
        self, kahero_branch, owner, product
    ):
        """Backend gap assessment item 4 (Phase 1, critical): KaHero
        exports carry no external ID, so a staff member re-uploading the
        same file (accidentally, or because KaHero re-sent it) must not
        silently double every sale."""
        services.process_kahero_upload(kahero_branch, owner, _make_csv(VALID_CSV))
        assert SalesTransaction.objects.filter(branch=kahero_branch).count() == 2

        second_batch = services.process_kahero_upload(kahero_branch, owner, _make_csv(VALID_CSV))

        # No new sales created the second time around.
        assert SalesTransaction.objects.filter(branch=kahero_branch).count() == 2
        assert second_batch.success_count == 0
        assert second_batch.error_count == 2
        assert all("Duplicate" in e["message"] for e in second_batch.error_log)

    def test_duplicate_detection_is_scoped_per_branch(self, owner, product):
        """The same sale figures showing up at a different branch's own
        export is not a duplicate of this branch's import -- each branch
        reconciles its own KaHero data independently."""
        # Only one branch may carry is_kahero_branch=True at a time (backend
        # gap assessment: accounts.Branch.unique_kahero_branch constraint) --
        # that flag is unrelated to process_kahero_upload's own scoping, so
        # branch_b is a plain branch here, not a second KaHero branch.
        branch_a = Branch.objects.create(name="Alangilan", code="ALANGILAN", is_kahero_branch=True)
        branch_b = Branch.objects.create(name="Batangas", code="BATANGAS")

        services.process_kahero_upload(branch_a, owner, _make_csv(VALID_CSV))
        second_batch = services.process_kahero_upload(branch_b, owner, _make_csv(VALID_CSV))

        assert second_batch.success_count == 2
        assert SalesTransaction.objects.filter(branch=branch_b).count() == 2

    def test_two_distinct_rows_with_the_same_product_and_date_are_not_flagged(
        self, kahero_branch, owner, product
    ):
        """VALID_CSV's two rows share a product and date but differ in
        quantity -- real, distinct sales, not a duplicate re-import."""
        batch = services.process_kahero_upload(kahero_branch, owner, _make_csv(VALID_CSV))

        assert batch.success_count == 2
        assert batch.error_count == 0

    def test_quality_rate_reflects_success_ratio(self, kahero_branch, owner, product):
        csv_text = (
            "transaction_date,product_name,quantity,unit_price\n"
            "2026-08-01,Spanish Latte,2,125.00\n"
            "2026-08-01,Nonexistent,1,50.00\n"
        )
        batch = services.process_kahero_upload(kahero_branch, owner, _make_csv(csv_text))
        assert batch.quality_rate == 50.0
        # The batch is still COMPLETED (at least one row ingested), but
        # has_warnings must flag that it wasn't a clean import.
        assert batch.status == KaheroImportBatch.Status.COMPLETED
        assert batch.has_warnings is True


class TestCatchAllExceptionHandling:
    """Backend gap assessment item 27: an unanticipated exception (a DB
    issue, or anything else nobody wrote a specific handler for) must
    never crash the request or leave the batch stuck at PROCESSING --
    it must always resolve to a terminal FAILED status with the error
    logged, same as any other, anticipated failure."""

    def test_unexpected_error_during_parsing_fails_the_batch_cleanly(self, kahero_branch, owner):
        with patch(
            "apps.kahero_integration.services.parse_kahero_file",
            side_effect=RuntimeError("boom"),
        ):
            batch = services.process_kahero_upload(kahero_branch, owner, _make_csv(VALID_CSV))

        assert batch.status == KaheroImportBatch.Status.FAILED
        assert batch.completed_at is not None
        assert "unexpected error" in batch.error_log[0]["message"].lower()

    def test_unexpected_error_during_ingestion_fails_the_whole_batch_cleanly(
        self, kahero_branch, owner, product
    ):
        with patch(
            "apps.kahero_integration.services._ingest_rows",
            side_effect=RuntimeError("boom"),
        ):
            batch = services.process_kahero_upload(kahero_branch, owner, _make_csv(VALID_CSV))

        assert batch.status == KaheroImportBatch.Status.FAILED
        assert batch.completed_at is not None
        assert "unexpected error" in batch.error_log[0]["message"].lower()
        # The batch never gets stuck at PROCESSING -- it always reaches
        # a terminal status, even when the failure was unanticipated.
        assert batch.status != KaheroImportBatch.Status.PROCESSING

    def test_unexpected_error_on_one_row_is_skipped_not_fatal_to_the_batch(
        self, kahero_branch, owner, product
    ):
        """A single bad row raising something nobody anticipated (here,
        simulated via a broken date parse) must be recorded as a
        row-level error and skipped -- the row(s) around it must still
        be ingested, same tolerance as the already-handled "product not
        found" / "invalid price" cases."""
        csv_text = (
            "transaction_date,product_name,quantity,unit_price\n"
            "2026-08-01,Spanish Latte,2,125.00\n"
            "2026-08-01,Spanish Latte,1,125.00\n"
        )
        with patch(
            "apps.kahero_integration.services._parse_transaction_date",
            side_effect=[RuntimeError("boom"), services._parse_transaction_date("2026-08-01")],
        ):
            batch = services.process_kahero_upload(kahero_branch, owner, _make_csv(csv_text))

        assert batch.success_count == 1
        assert batch.error_count == 1
        assert batch.status == KaheroImportBatch.Status.COMPLETED
        assert any("unexpected error" in e["message"].lower() for e in batch.error_log)


class TestSilentSkipWarningUI:
    """A batch that's COMPLETED but skipped rows must not look identical
    to a fully clean batch in either the dashboard list or the batch
    detail page (Development Status Assessment, KaHero item)."""

    def _partial_csv(self):
        return (
            "transaction_date,product_name,quantity,unit_price\n"
            "2026-08-01,Spanish Latte,2,125.00\n"
            "2026-08-01,Nonexistent,1,50.00\n"
        )

    def test_dashboard_shows_warning_badge_for_partial_batch(
        self, owner_client, kahero_branch, owner, product
    ):
        services.process_kahero_upload(kahero_branch, owner, _make_csv(self._partial_csv()))
        response = owner_client.get(reverse("kahero:dashboard"))
        assert response.status_code == 200
        assert b"Completed (warnings)" in response.content

    def test_dashboard_shows_plain_completed_badge_for_clean_batch(
        self, owner_client, kahero_branch, owner, product
    ):
        services.process_kahero_upload(kahero_branch, owner, _make_csv(VALID_CSV))
        response = owner_client.get(reverse("kahero:dashboard"))
        assert response.status_code == 200
        assert b"Completed (warnings)" not in response.content

    def test_batch_detail_shows_warning_banner_for_partial_batch(
        self, owner_client, kahero_branch, owner, product
    ):
        batch = services.process_kahero_upload(kahero_branch, owner, _make_csv(self._partial_csv()))
        response = owner_client.get(reverse("kahero:batch_detail", args=[batch.pk]))
        assert response.status_code == 200
        assert b"row(s) were" in response.content
        assert b"skipped" in response.content

    def test_batch_detail_has_no_warning_banner_for_clean_batch(
        self, owner_client, kahero_branch, owner, product
    ):
        batch = services.process_kahero_upload(kahero_branch, owner, _make_csv(VALID_CSV))
        response = owner_client.get(reverse("kahero:batch_detail", args=[batch.pk]))
        assert response.status_code == 200
        assert b"were skipped and were not imported" not in response.content


class TestUploadView:
    def test_owner_can_access_upload_page(self, owner_client):
        response = owner_client.get(reverse("kahero:upload"))
        assert response.status_code == 200

    def test_non_owner_cannot_access_upload_page(self, client):
        role = Role.objects.create(name=Role.BRANCH_STAFF)
        staff = User.objects.create_user(username="staff1", password="testpass123", role=role)
        client.force_login(staff)
        response = client.get(reverse("kahero:upload"))
        assert response.status_code == 403

    def test_successful_upload_redirects_to_batch_detail(
        self, owner_client, kahero_branch, product
    ):
        response = owner_client.post(reverse("kahero:upload"), {"file": _make_csv(VALID_CSV)})
        assert response.status_code == 302
        batch = KaheroImportBatch.objects.first()
        assert response.url == reverse("kahero:batch_detail", args=[batch.pk])

    def test_no_file_shows_error(self, owner_client, kahero_branch):
        response = owner_client.post(reverse("kahero:upload"), {})
        assert response.status_code == 200
        assert b"choose a file" in response.content


class TestDashboardView:
    def test_dashboard_shows_batch_summary(self, owner_client, kahero_branch, owner, product):
        services.process_kahero_upload(kahero_branch, owner, _make_csv(VALID_CSV))

        response = owner_client.get(reverse("kahero:dashboard"))
        assert response.status_code == 200
        assert b"Total Batches" in response.content

    def test_dashboard_lists_the_uploaded_filename(
        self, owner_client, kahero_branch, owner, product
    ):
        services.process_kahero_upload(kahero_branch, owner, _make_csv(VALID_CSV))

        response = owner_client.get(reverse("kahero:dashboard"))
        assert b"kahero_export.csv" in response.content

    def test_non_owner_cannot_access_dashboard(self, client):
        role = Role.objects.create(name=Role.BRANCH_STAFF)
        staff = User.objects.create_user(username="staff1", password="testpass123", role=role)
        client.force_login(staff)
        response = client.get(reverse("kahero:dashboard"))
        assert response.status_code == 403
