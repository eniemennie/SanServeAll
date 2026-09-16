"""
Orchestrates the KaHero batch-import pipeline (Row 7.3): validate file ->
stage rows -> ingest into pos models -> mark KaheroImportBatch
complete/failed.

Critical rule: rows ingested here become COMPLETED SalesTransaction/
SalesItem rows directly -- they do NOT go through pos.services.
complete_sale_payment(), because that function's real-time inventory
deduction hook must never run for KaHero-branch sales (Phase 2/3
architecture decision, already enforced once in pos/services.py). This
pipeline creates already-completed historical sales; there is nothing to
deduct in real time for data that describes the past.
"""

from decimal import Decimal, InvalidOperation
from datetime import datetime

from django.db import transaction as db_transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.inventory.models import Product
from apps.kahero_integration.models import KaheroImportBatch
from apps.kahero_integration.parsers import ParseError, parse_kahero_file
from apps.pos.models import SalesItem, SalesTransaction


def _resolve_product(product_name):
    """Looks up a product by name, case-insensitive. Returns None if no
    match -- callers treat this as a row-level error rather than silently
    auto-creating a product from what might be a typo in the export."""
    return Product.objects.filter(name__iexact=product_name.strip()).first()


def _parse_transaction_date(raw_value):
    """KaHero exports may give a real datetime (from Excel) or a string
    (from CSV) -- normalize both to a timezone-aware datetime, since
    Django's USE_TZ=True setting expects one. Falls back to "now" only as
    a last resort, since a missing/unparseable date shouldn't block
    ingestion of an otherwise-valid row entirely."""
    if hasattr(raw_value, "to_pydatetime"):  # pandas Timestamp
        naive = raw_value.to_pydatetime()
    else:
        naive = parse_datetime(str(raw_value))
        if naive is None:
            # parse_datetime only handles full datetimes; a date-only
            # string like "2026-08-01" needs parse_date + combining with
            # a time component before it's usable here.
            parsed_date = parse_date(str(raw_value))
            if parsed_date is None:
                return timezone.now()
            naive = datetime.combine(parsed_date, datetime.min.time())

    if timezone.is_aware(naive):
        return naive
    return timezone.make_aware(naive)


def process_kahero_upload(branch, uploaded_by, uploaded_file):
    """Entry point for Row 7.1's upload view. Creates the audit batch
    record first (so even a file that fails to parse at all still leaves
    a record of the attempt), then parses and ingests."""
    batch = KaheroImportBatch.objects.create(
        branch=branch,
        uploaded_by=uploaded_by,
        uploaded_file=uploaded_file,
        original_filename=uploaded_file.name,
        status=KaheroImportBatch.Status.PROCESSING,
    )

    try:
        uploaded_file.seek(0)  # the batch's FileField save above already
        # consumed this stream once to write it to disk -- must rewind
        # before pandas can read it too.
        valid_rows, parse_errors = parse_kahero_file(uploaded_file)
    except ParseError as exc:
        batch.status = KaheroImportBatch.Status.FAILED
        batch.error_log = [{"row": None, "message": str(exc)}]
        batch.completed_at = timezone.now()
        batch.save()
        return batch

    ingested_count, ingestion_errors = _ingest_rows(batch, valid_rows)

    all_errors = [{"row": None, "message": msg} for msg in parse_errors] + ingestion_errors
    batch.total_rows = len(valid_rows) + len(parse_errors)
    batch.success_count = ingested_count
    batch.error_count = batch.total_rows - ingested_count
    batch.error_log = all_errors
    batch.status = (
        KaheroImportBatch.Status.COMPLETED
        if ingested_count > 0
        else KaheroImportBatch.Status.FAILED
    )
    batch.completed_at = timezone.now()
    batch.save()

    return batch


def _ingest_rows(batch, rows):
    """Creates a COMPLETED SalesTransaction+SalesItem per valid row.
    Each row is its own atomic unit -- one bad row (e.g. an unmatched
    product) doesn't roll back the rows around it."""
    ingested_count = 0
    errors = []

    for row in rows:
        product = _resolve_product(row["product_name"])
        if product is None:
            errors.append(
                {
                    "row": None,
                    "message": (
                        f"Product '{row['product_name']}' not found in catalog -- " "row skipped."
                    ),
                }
            )
            continue

        try:
            unit_price = Decimal(str(row["unit_price"]))
        except InvalidOperation:
            errors.append({"row": None, "message": f"Invalid price for '{row['product_name']}'."})
            continue

        with db_transaction.atomic():
            sales_transaction = SalesTransaction.objects.create(
                branch=batch.branch,
                cashier=batch.uploaded_by,
                status=SalesTransaction.Status.COMPLETED,
                payment_method=SalesTransaction.PaymentMethod.CASH,
                amount_tendered=unit_price * row["quantity"],
                completed_at=_parse_transaction_date(row["transaction_date"]),
            )
            SalesItem.objects.create(
                transaction=sales_transaction,
                product=product,
                unit_price=unit_price,
                quantity=row["quantity"],
            )
        ingested_count += 1

    return ingested_count, errors
