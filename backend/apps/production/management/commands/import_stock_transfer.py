"""
One-off historical import of the client's real Stock_Transfer.xlsx (508
rows): commissary -> branch finished-goods shipments, recorded as
StockTransferOrder rows. Part of the client-dataset import work (the last
of the 5 import tasks); depends on the same catalog/accounts work as
import_production_and_ingredient_usage (the 6 size-variant Products, the
commissary_staff/commissary_staff2/commissary_staff3 accounts), since
Stock_Transfer.xlsx's 43 product names are the exact same universe (100%
overlap, verified via set difference) -- see
apps.production.client_import_mapping, shared with that command.

IMPORTANT -- this import does NOT touch Inventory or create
InventoryTransaction rows, unlike apps.production.services.
create_stock_transfer_order/advance_stock_transfer_status (the live path a
real transfer normally goes through, which deducts from the commissary's
Inventory at creation and credits the destination branch's Inventory on
delivery). Raw_Materials.xlsx/Packaging_Materials.xlsx's Quantity on Hand
is already a CURRENT snapshot of finished-goods-adjacent stock, already
net of every historical transfer in this file. Running the live service
functions against that same current snapshot would double-count: these
508 transfers' stock movements were already reflected before the
snapshot was taken. So StockTransferOrder rows are created directly, as a
historical log/audit trail only -- the same reasoning
import_production_and_ingredient_usage uses for ProductionRecord/
IngredientUsage, and import_branch_sales_history uses for its own
historical SalesTransaction backfill.

Source columns: Transfer Ref, Commissary Staff (username), Source Branch
Code (always COMMISSARY), Destination Branch Code (BATANGAS/LIPA),
Product Name (cosmetic-variant names resolved via
apps.production.client_import_mapping, same as Production_Records.xlsx),
Quantity, Status (DELIVERED/IN_TRANSIT/PROCESSING -- no CANCELLED rows are
present in the source file, but an unrecognized Status is still a hard
error, not silently ignored), Created Date, Delivered Date (present only
for DELIVERED rows -- 0 mismatches verified against Status in the source
file; a mismatch found in the data is a hard error here, not silently
accepted, since that would mean the two columns disagree about whether
the transfer actually arrived).

"Butter Toast and Broas (Jar)" (7 rows, including 2 ODD quantities -- 13
and 15): same combined-shipment situation as Production_Records.xlsx's
combined batches, resolved the same way (confirmed with the project
owner for Production_Records; applied uniformly here since it's the same
client file convention) -- split via split_evenly() (exact, no unit lost
to floor division) into two child StockTransferOrder rows, transfer_ref
suffixed "-BT"/"-BR" (the single source transfer_ref can't be reused
twice -- it's unique).

Idempotent per transfer_ref (or per suffixed child ref for the split
rows): skips a transfer_ref that already exists, so a re-run after a
partial import or an accidental second run never creates duplicates.

Usage:
    python manage.py import_stock_transfer \
        --stock-transfer /path/to/Stock_Transfer.xlsx \
        --dry-run
"""

import datetime

import openpyxl
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Branch
from apps.inventory.models import Product
from apps.production.client_import_mapping import (
    COMBINED_PRODUCTS,
    resolve_product_name,
    split_evenly,
)
from apps.production.models import StockTransferOrder

# No time-of-day is recorded for "Created Date" in the source file -- a
# fixed, arbitrary time (commissary's nominal start of day) is used
# purely so imported rows sort in a sensible order; has no bearing on
# correctness. "Delivered Date" (when present) uses the same convention.
TRANSFER_TIME_OF_DAY = datetime.time(8, 0)


class Command(BaseCommand):
    help = (
        "Imports the client's real Stock_Transfer.xlsx as historical "
        "StockTransferOrder rows (no live Inventory deduction/crediting)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--stock-transfer", type=str, required=True, help="Path to Stock_Transfer.xlsx"
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and report counts without writing anything to the database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        path = options["stock_transfer"]

        rows = self._parse(path)
        self.stdout.write(f"Stock Transfer: parsed {len(rows)} rows")

        if dry_run:
            self.stdout.write(self.style.WARNING("--dry-run: nothing written."))
            return

        created = skipped = 0
        with transaction.atomic():
            for row in rows:
                children = self._create_transfer_orders(row)
                if children is None:
                    skipped += 1
                    continue
                created += len(children)

        self.stdout.write(
            self.style.SUCCESS(
                f"StockTransferOrder: {created} created, {skipped} transfer_ref(s) "
                "already existed."
            )
        )

    # -- parsing -----------------------------------------------------------

    def _parse(self, path):
        wb = self._load_workbook(path)
        ws = wb.active
        header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
        headers = [(str(h).strip() if h is not None else "") for h in header_row]
        required = [
            "Transfer Ref",
            "Commissary Staff (username)",
            "Source Branch Code",
            "Destination Branch Code",
            "Product Name",
            "Quantity",
            "Status",
            "Created Date",
            "Delivered Date",
        ]
        missing = [c for c in required if c not in headers]
        if missing:
            raise CommandError(f"Stock Transfer: missing column(s): {', '.join(missing)}")
        index = {name: headers.index(name) for name in required}

        rows = []
        for raw in ws.iter_rows(min_row=2, values_only=True):
            if raw[index["Transfer Ref"]] in (None, ""):
                continue

            transfer_ref = str(raw[index["Transfer Ref"]]).strip()

            created_date_raw = raw[index["Created Date"]]
            try:
                created_date = datetime.date.fromisoformat(str(created_date_raw)[:10])
            except ValueError as exc:
                raise CommandError(
                    f"Transfer {transfer_ref!r}: unparseable Created Date " f"{created_date_raw!r}."
                ) from exc

            status_raw = str(raw[index["Status"]]).strip()
            try:
                status = StockTransferOrder.Status(status_raw)
            except ValueError as exc:
                raise CommandError(
                    f"Transfer {transfer_ref!r}: unrecognized Status {status_raw!r}."
                ) from exc

            delivered_date_raw = raw[index["Delivered Date"]]
            has_delivered_date = delivered_date_raw not in (None, "")
            if status == StockTransferOrder.Status.DELIVERED and not has_delivered_date:
                raise CommandError(
                    f"Transfer {transfer_ref!r}: Status is DELIVERED but Delivered Date "
                    "is blank."
                )
            if status != StockTransferOrder.Status.DELIVERED and has_delivered_date:
                raise CommandError(
                    f"Transfer {transfer_ref!r}: Delivered Date is set but Status is "
                    f"{status_raw!r}, not DELIVERED."
                )

            delivered_date = None
            if has_delivered_date:
                try:
                    delivered_date = datetime.date.fromisoformat(str(delivered_date_raw)[:10])
                except ValueError as exc:
                    raise CommandError(
                        f"Transfer {transfer_ref!r}: unparseable Delivered Date "
                        f"{delivered_date_raw!r}."
                    ) from exc

            rows.append(
                {
                    "transfer_ref": transfer_ref,
                    "staff_username": str(raw[index["Commissary Staff (username)"]]).strip(),
                    "source_branch_code": str(raw[index["Source Branch Code"]]).strip(),
                    "destination_branch_code": str(raw[index["Destination Branch Code"]]).strip(),
                    "product_name": str(raw[index["Product Name"]]).strip(),
                    "quantity": int(raw[index["Quantity"]]),
                    "status": status,
                    "created_date": created_date,
                    "delivered_date": delivered_date,
                }
            )
        return rows

    # -- name/user/branch/product resolution --------------------------------

    def _resolve_product(self, name):
        real_name = resolve_product_name(name)
        product = Product.objects.filter(name=real_name).first()
        if product is None:
            raise CommandError(
                f"'{name}' (mapped to '{real_name}') has no matching catalog Product. "
                "Add it to apps.production.client_import_mapping or the catalog before "
                "importing."
            )
        return product

    def _resolve_staff(self, username):
        User = get_user_model()
        user = User.objects.filter(username=username).first()
        if user is None:
            raise CommandError(
                f"Commissary staff username '{username}' has no matching User. Run "
                "create_commissary_staff_import_accounts before importing."
            )
        return user

    def _resolve_branch(self, code, transfer_ref):
        branch = Branch.objects.filter(code=code).first()
        if branch is None:
            raise CommandError(f"Transfer {transfer_ref!r}: no Branch with code {code!r}.")
        return branch

    # -- import --------------------------------------------------------------

    def _create_transfer_orders(self, row):
        """Returns the list of StockTransferOrder rows created for this
        source row (more than one only for a combined-shipment row like
        "Butter Toast and Broas (Jar)"), or None if this transfer_ref was
        already imported."""
        combo = COMBINED_PRODUCTS.get(row["product_name"])
        plan = (
            [(f"{row['transfer_ref']}-{suffix}", name) for suffix, name in combo]
            if combo
            else [(row["transfer_ref"], row["product_name"])]
        )
        # Idempotency check must use the actual ref(s) that get created --
        # for a combined shipment that's the suffixed child refs
        # ("TRANSFER-0257-BT"/"-BR"), never the bare source transfer_ref
        # itself (no row is ever created under that exact ref). Checking
        # the first child's ref is enough: all of a shipment's children
        # are created together, in the same call, so they exist or don't
        # as a unit.
        if StockTransferOrder.objects.filter(transfer_ref=plan[0][0]).exists():
            return None

        staff = self._resolve_staff(row["staff_username"])
        source_branch = self._resolve_branch(row["source_branch_code"], row["transfer_ref"])
        destination_branch = self._resolve_branch(
            row["destination_branch_code"], row["transfer_ref"]
        )

        created_naive = datetime.datetime.combine(row["created_date"], TRANSFER_TIME_OF_DAY)
        created_at = timezone.make_aware(created_naive)
        delivered_at = None
        if row["delivered_date"] is not None:
            delivered_naive = datetime.datetime.combine(row["delivered_date"], TRANSFER_TIME_OF_DAY)
            delivered_at = timezone.make_aware(delivered_naive)

        # Split evenly (exactly -- any remainder goes to the earliest
        # child, see split_evenly) across every child -- there's no
        # finer real breakdown of a combined shipment's quantity per
        # product (see module docstring).
        quantities = split_evenly(row["quantity"], len(plan))

        created = []
        for (transfer_ref, product_name), quantity_each in zip(plan, quantities):
            product = self._resolve_product(product_name)
            order = StockTransferOrder.objects.create(
                transfer_ref=transfer_ref,
                commissary_staff=staff,
                source_branch=source_branch,
                destination_branch=destination_branch,
                product=product,
                quantity=quantity_each,
                status=row["status"],
                delivered_at=delivered_at,
            )
            # created_at is auto_now_add -- backdated after the fact so
            # the record sorts/reports under its real historical date.
            StockTransferOrder.objects.filter(pk=order.pk).update(created_at=created_at)
            created.append(order)
        return created

    # -- shared --------------------------------------------------------------

    def _load_workbook(self, path):
        try:
            return openpyxl.load_workbook(path, data_only=True)
        except FileNotFoundError as exc:
            raise CommandError(f"File not found: {path}") from exc
