"""
One-off historical import of the client's real Production_Records.xlsx
(356 batches) and Ingredient_Usage.xlsx (2,781 material-consumption rows,
FK'd to Production_Records by Batch Ref). Part of the client-dataset
import work (task 4 of 5); depends on the schema/catalog/materials work
in three earlier PRs (Product.supplier, Inventory as Decimal,
IngredientUsage.quantity_used as Decimal, ProductionRecord.Quality.
PENDING, the commissary_staff/commissary_staff2/commissary_staff3
accounts, and the 6 new per-size-variant Products).

Production_Records.xlsx's own "Data Source" column labels every row
"ESTIMATED sample data ... not actual records" -- confirmed with the
project owner to import it anyway, as real ProductionRecord/
IngredientUsage history (not as removable sample/demo data).

IMPORTANT -- this import does NOT touch Inventory or create
InventoryTransaction rows, unlike apps.production.services.
record_production (the live path a real batch normally goes through).
Raw_Materials.xlsx/Packaging_Materials.xlsx's Quantity on Hand is
already a CURRENT snapshot -- i.e. already net of everything these 356
historical batches consumed. Running record_production's live deduction
logic against that same current snapshot would double-count: materials
these batches used were already subtracted before the snapshot was
taken. So ProductionRecord/IngredientUsage rows are created directly,
as a historical log/audit trail only -- same reasoning
import_branch_sales_history uses for bypassing pos.services for its own
historical SalesTransaction backfill.

Product name mapping: the client's "Product Name (reference)" column
uses a few cosmetic variants of the real catalog names (e.g. "8' Round"
for the cakes' "8in Round", "200 g" for the catalog's "200g") --
apps.production.client_import_mapping (shared with import_stock_transfer,
which draws from this exact same 43-name universe) translates every one
of them to its real Product. Two categories of real (non-cosmetic) gaps this
uncovered, both resolved with the project owner before building this:

  - Size variants the sales-facing catalog collapses into one 2-tier
    Product (Original/Salted Caramel Bites Small/Big, Tea Cookies Big,
    Creamy Leche Flan Family Flan) -- resolved by adding those as their
    own Products (scripts/seed_menu_catalog.py's
    seed_production_size_variants), not by splitting or replacing the
    2-tier ones POS still uses for its own size-selection UX.
  - "Butter Toast and Broas (Jar)" (4 batches): one combined batch
    yielding two separate finished goods with only one recorded total
    quantity -- resolved (confirmed with the project owner) by splitting
    the quantity 50/50 into two child ProductionRecord rows, batch_ref
    suffixed "-BT"/"-BR" (the single source batch_ref can't be reused
    twice -- it's unique). The same 4 batches' Ingredient_Usage rows are
    split 50/50 the same way, for consistency with the output split --
    there's no finer real breakdown of which ingredients went to which
    jar, so this is the same assumption applied uniformly, not a new one.

Idempotent per batch_ref (or per suffixed child ref for the split
batches): skips a batch_ref that already exists, so a re-run after a
partial import or an accidental second run never creates duplicates.

Usage:
    python manage.py import_production_and_ingredient_usage \
        --production-records /path/to/Production_Records.xlsx \
        --ingredient-usage /path/to/Ingredient_Usage.xlsx \
        --dry-run
"""

import datetime
from decimal import Decimal, InvalidOperation

import openpyxl
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.inventory.models import Product
from apps.production.client_import_mapping import (
    COMBINED_PRODUCTS,
    resolve_product_name,
    split_evenly,
)
from apps.production.models import IngredientUsage, ProductionRecord

# No time-of-day is recorded in the source file -- a fixed, arbitrary
# time (commissary's nominal start of day) is used purely so imported
# batches sort in a sensible order; has no bearing on correctness.
PRODUCTION_TIME_OF_DAY = datetime.time(8, 0)


class Command(BaseCommand):
    help = (
        "Imports the client's real Production_Records.xlsx / Ingredient_Usage.xlsx as "
        "historical ProductionRecord + IngredientUsage rows (no live Inventory deduction)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--production-records", type=str, default=None, help="Path to Production_Records.xlsx"
        )
        parser.add_argument(
            "--ingredient-usage", type=str, default=None, help="Path to Ingredient_Usage.xlsx"
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and report counts without writing anything to the database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        production_path = options["production_records"]
        usage_path = options["ingredient_usage"]

        if not production_path and not usage_path:
            raise CommandError(
                "Pass at least one of --production-records, --ingredient-usage with a file path."
            )
        if usage_path and not production_path:
            raise CommandError(
                "--ingredient-usage requires --production-records in the same run (it's "
                "keyed by Batch Ref, which only exists once production rows are created)."
            )

        production_rows = self._parse_production(production_path) if production_path else []
        usage_rows = self._parse_usage(usage_path) if usage_path else []

        if production_path:
            self.stdout.write(f"Production Records: parsed {len(production_rows)} rows")
        if usage_path:
            self.stdout.write(f"Ingredient Usage: parsed {len(usage_rows)} rows")

        if dry_run:
            self.stdout.write(self.style.WARNING("--dry-run: nothing written."))
            return

        records_created = records_skipped = 0
        usage_created = 0

        with transaction.atomic():
            # batch_ref -> list of ProductionRecord created for it (more
            # than one only for the combined Butter Toast/Broas batches).
            created_by_batch_ref = {}

            for row in production_rows:
                children = self._create_production_records(row)
                if children is None:
                    records_skipped += 1
                    continue
                created_by_batch_ref[row["batch_ref"]] = children
                records_created += len(children)

            for batch_ref, rows in self._group_usage_by_batch(usage_rows).items():
                targets = created_by_batch_ref.get(batch_ref)
                if not targets:
                    # Either this batch_ref's ProductionRecord already
                    # existed from an earlier run (skipped above, so its
                    # IngredientUsage rows already exist too), or it was
                    # not part of this --production-records file at all.
                    continue
                usage_created += self._create_ingredient_usage(targets, rows)

        self.stdout.write(
            self.style.SUCCESS(
                f"ProductionRecord: {records_created} created, {records_skipped} batch_ref(s) "
                f"already existed. IngredientUsage: {usage_created} created."
            )
        )

    # -- parsing: Production_Records.xlsx --------------------------------

    def _parse_production(self, path):
        wb = self._load_workbook(path)
        ws = wb.active
        header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
        headers = [(str(h).strip() if h is not None else "") for h in header_row]
        required = [
            "Batch Ref",
            "Commissary Staff (username)",
            "Product Name (reference)",
            "Quantity Produced",
            "Quality",
            "Status",
            "Production Date",
            "Notes",
        ]
        missing = [c for c in required if c not in headers]
        if missing:
            raise CommandError(f"Production Records: missing column(s): {', '.join(missing)}")
        index = {name: headers.index(name) for name in required}
        supplier_idx = headers.index("Supplier") if "Supplier" in headers else None

        rows = []
        for raw in ws.iter_rows(min_row=2, values_only=True):
            if raw[index["Batch Ref"]] in (None, ""):
                continue

            production_date_raw = raw[index["Production Date"]]
            try:
                production_date = datetime.date.fromisoformat(str(production_date_raw)[:10])
            except ValueError as exc:
                raise CommandError(
                    f"Batch {raw[index['Batch Ref']]!r}: unparseable Production Date "
                    f"{production_date_raw!r}."
                ) from exc

            supplier = ""
            if supplier_idx is not None:
                raw_supplier = raw[supplier_idx]
                supplier = "" if raw_supplier in (None, "") else str(raw_supplier).strip()

            rows.append(
                {
                    "batch_ref": str(raw[index["Batch Ref"]]).strip(),
                    "staff_username": str(raw[index["Commissary Staff (username)"]]).strip(),
                    "product_name": str(raw[index["Product Name (reference)"]]).strip(),
                    "quantity_produced": int(raw[index["Quantity Produced"]]),
                    "supplier": supplier,
                    "quality": str(raw[index["Quality"]]).strip(),
                    "status": str(raw[index["Status"]]).strip(),
                    "production_date": production_date,
                    "notes": str(raw[index["Notes"]] or "").strip(),
                }
            )
        return rows

    # -- parsing: Ingredient_Usage.xlsx -----------------------------------

    def _parse_usage(self, path):
        wb = self._load_workbook(path)
        ws = wb.active
        header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
        headers = [(str(h).strip() if h is not None else "") for h in header_row]
        required = ["Batch Ref (FK)", "Material Name", "Quantity Used"]
        missing = [c for c in required if c not in headers]
        if missing:
            raise CommandError(f"Ingredient Usage: missing column(s): {', '.join(missing)}")
        index = {name: headers.index(name) for name in required}

        rows = []
        for raw in ws.iter_rows(min_row=2, values_only=True):
            if raw[index["Batch Ref (FK)"]] in (None, ""):
                continue
            try:
                quantity_used = Decimal(str(raw[index["Quantity Used"]]))
            except (InvalidOperation, TypeError) as exc:
                raise CommandError(
                    f"Batch {raw[index['Batch Ref (FK)']]!r}: non-numeric Quantity Used "
                    f"{raw[index['Quantity Used']]!r}."
                ) from exc
            rows.append(
                {
                    "batch_ref": str(raw[index["Batch Ref (FK)"]]).strip(),
                    "material_name": str(raw[index["Material Name"]]).strip(),
                    "quantity_used": quantity_used,
                }
            )
        return rows

    def _group_usage_by_batch(self, usage_rows):
        grouped = {}
        for row in usage_rows:
            grouped.setdefault(row["batch_ref"], []).append(row)
        return grouped

    # -- name/user/product resolution -------------------------------------

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

    # -- import: ProductionRecord ------------------------------------------

    def _create_production_records(self, row):
        """Returns the list of ProductionRecord rows created for this
        source row (more than one only for a combined batch), or None if
        this batch_ref was already imported."""
        combo = COMBINED_PRODUCTS.get(row["product_name"])
        plan = (
            [(f"{row['batch_ref']}-{suffix}", name) for suffix, name in combo]
            if combo
            else [(row["batch_ref"], row["product_name"])]
        )
        # Idempotency check must use the actual ref(s) that get created --
        # for a combined batch that's the suffixed child refs
        # ("BATCH-0181-BT"/"-BR"), never the bare source batch_ref itself
        # (no row is ever created under that exact ref). Checking the
        # first child's ref is enough: all of a batch's children are
        # created together, in the same call, so they exist or don't as
        # a unit.
        if ProductionRecord.objects.filter(batch_ref=plan[0][0]).exists():
            return None

        staff = self._resolve_staff(row["staff_username"])
        naive_dt = datetime.datetime.combine(row["production_date"], PRODUCTION_TIME_OF_DAY)
        created_at = timezone.make_aware(naive_dt)
        quality = self._map_quality(row["quality"], row["batch_ref"])
        status = self._map_status(row["status"], row["batch_ref"])

        # Split evenly (exactly -- any remainder goes to the earliest
        # child, see split_evenly) across every child -- there's no
        # finer real breakdown of a combined batch's output per product
        # (see module docstring).
        quantities = split_evenly(row["quantity_produced"], len(plan))

        created = []
        for (batch_ref, product_name), quantity_each in zip(plan, quantities):
            product = self._resolve_product(product_name)
            record = ProductionRecord.objects.create(
                batch_ref=batch_ref,
                commissary_staff=staff,
                product=product,
                quantity_produced=quantity_each,
                supplier=row["supplier"],
                quality=quality,
                status=status,
                notes=row["notes"],
            )
            # created_at is auto_now_add -- backdated after the fact so
            # the record sorts/reports under its real historical date.
            ProductionRecord.objects.filter(pk=record.pk).update(created_at=created_at)
            created.append(record)
        return created

    def _map_quality(self, raw_quality, batch_ref):
        try:
            return ProductionRecord.Quality(raw_quality)
        except ValueError:
            raise CommandError(f"Batch {batch_ref!r}: unrecognized Quality '{raw_quality}'.")

    def _map_status(self, raw_status, batch_ref):
        try:
            return ProductionRecord.Status(raw_status)
        except ValueError:
            raise CommandError(f"Batch {batch_ref!r}: unrecognized Status '{raw_status}'.")

    # -- import: IngredientUsage --------------------------------------------

    def _create_ingredient_usage(self, targets, usage_rows):
        """`targets` is the list of ProductionRecord(s) this batch_ref's
        usage rows apply to -- more than one only for a combined batch,
        in which case each material's full recorded usage is split
        evenly across every child record, the same convention as the
        output-quantity split (see module docstring)."""
        created = 0
        for usage_row in usage_rows:
            # Client review (2026-11-09): widened to Product.MATERIAL_TYPES
            # -- a historical usage row can legitimately name a packaging
            # item (now ProductType.PACKAGING instead of a MATERIAL row
            # tagged category=PACKAGING).
            material = Product.objects.filter(
                name=usage_row["material_name"], product_type__in=Product.MATERIAL_TYPES
            ).first()
            if material is None:
                raise CommandError(
                    f"Material '{usage_row['material_name']}' has no matching Raw Material "
                    "or Packaging Material Product. Run import_raw_and_packaging_materials "
                    "before importing ingredient usage."
                )

            quantity_each = usage_row["quantity_used"] / len(targets)
            for record in targets:
                IngredientUsage.objects.create(
                    production_record=record, material=material, quantity_used=quantity_each
                )
                created += 1
        return created

    # -- shared --------------------------------------------------------------

    def _load_workbook(self, path):
        try:
            return openpyxl.load_workbook(path, data_only=True)
        except FileNotFoundError as exc:
            raise CommandError(f"File not found: {path}") from exc
