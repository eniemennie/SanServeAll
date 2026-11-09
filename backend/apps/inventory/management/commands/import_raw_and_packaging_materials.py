"""
One-off import of the client's real Raw_Materials.xlsx and
Packaging_Materials.xlsx -- the current (as-of-export) per-branch stock
snapshot for every raw material and packaging item the commissary and
branches actually track. Part of the client-dataset import work (task 3
of 5); the schema it depends on (Product.supplier, Inventory.
reorder_threshold/quantity_on_hand as Decimal, UnitOfMeasure.ROLL) landed
in two earlier PRs.

Each file is one flat table: Item No., Name, Supplier, Product Type
(the file's own "Product Type" column is always "MATERIAL" in the
client's raw data; this command derives the real Product.product_type
-- Raw Material vs Packaging Material -- from which file the row came
from instead, see below), Unit of Measure, Unit Cost, Quantity on Hand,
Reorder Threshold, Branch Code, Is Active. A material repeats once per branch
that stocks it (e.g. "White sugar" has 3 rows: COMMISSARY/LIPA/BATANGAS),
but its catalog-level facts -- Supplier, Unit Cost, Unit of Measure, Is
Active -- are identical across every one of its rows in the client's own
data (verified: zero mismatches across all 278 rows in both files), so
each becomes exactly one Product, with a separate Inventory row per
branch for its per-branch quantity and reorder threshold.

Decimal, not whole numbers: 175/206 raw-material rows have a fractional
Quantity on Hand and 164/206 a fractional Reorder Threshold (e.g. Ice:
0.3kg on hand) -- confirmed with the project owner to store these exactly
rather than round them (see Inventory.quantity_on_hand's own docstring).
Packaging_Materials.xlsx happens to be all whole numbers, but is imported
through the same Decimal-safe path for one consistent importer.

Idempotency:
  - Product fields (price=Unit Cost, supplier, unit_of_measure, is_active,
    product_type) are always kept in sync via update_or_create keyed by
    name -- safe to re-run, same convention as scripts/seed_menu_catalog.py,
    since these are catalog metadata the client's file is the source of
    truth for. `product_type` is Product.ProductType.PACKAGING for every
    row from Packaging_Materials.xlsx and Product.ProductType.MATERIAL for
    Raw_Materials.xlsx rows (task #148, client review, 2026-11-04 --
    "Packaging vs Food" filtering; client review, 2026-11-09 -- promoted
    from a category tag on a MATERIAL row to its own product_type). Both
    files' rows always get a blank `category` -- that field is a POS
    category-tab concept neither Raw Material nor Packaging Material rows
    are ever browsed by. Re-running this command against both files is
    the supported way to correct a product's type/category -- it only
    touches catalog metadata, never quantity_on_hand/reorder_threshold on
    an existing Inventory row (see below).
  - Inventory rows are get_or_create'd per (branch, product): a row's
    quantity_on_hand/reorder_threshold is set ONLY the first time that
    row is created. A second run (accidental re-import, or run again
    after go-live) must never silently reset real, already-adjusted
    stock back to this stale snapshot -- pass --force to explicitly
    overwrite an existing row's quantity/threshold back to the file's
    values (only meaningful before go-live, to correct a bad import).

Usage:
    python manage.py import_raw_and_packaging_materials \
        --raw-materials /path/to/Raw_Materials.xlsx \
        --packaging-materials /path/to/Packaging_Materials.xlsx \
        --dry-run
"""

from decimal import Decimal, InvalidOperation

import openpyxl
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import Branch
from apps.inventory.models import Inventory, Product

# The client's own unit strings -> Product.UnitOfMeasure. Every unit
# actually present in these two files maps cleanly; anything else is a
# genuine new gap, reported and skipped rather than guessed at.
UNIT_MAP = {
    "kg": Product.UnitOfMeasure.KILOGRAM,
    "g": Product.UnitOfMeasure.GRAM,
    "liter": Product.UnitOfMeasure.LITER,
    "ml": Product.UnitOfMeasure.MILLILITER,
    "piece": Product.UnitOfMeasure.PIECE,
    "pack": Product.UnitOfMeasure.PACK,
    "box": Product.UnitOfMeasure.BOX,
    "roll": Product.UnitOfMeasure.ROLL,
}

REQUIRED_COLUMNS = [
    "Name",
    "Supplier",
    "Product Type",
    "Unit of Measure",
    "Unit Cost",
    "Quantity on Hand",
    "Reorder Threshold",
    "Branch Code",
    "Is Active",
]


class Command(BaseCommand):
    help = (
        "Imports the client's real Raw_Materials.xlsx / Packaging_Materials.xlsx stock "
        "snapshot as Product + per-branch Inventory rows."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--raw-materials", type=str, default=None, help="Path to Raw_Materials.xlsx"
        )
        parser.add_argument(
            "--packaging-materials",
            type=str,
            default=None,
            help="Path to Packaging_Materials.xlsx",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and report counts without writing anything to the database.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Overwrite quantity_on_hand/reorder_threshold on an Inventory row that "
                "already exists, instead of leaving it untouched. Only meaningful before "
                "go-live, to correct a bad import -- never use this once real stock "
                "movement has happened against these rows."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force = options["force"]
        files = {
            "Raw Materials": options["raw_materials"],
            "Packaging Materials": options["packaging_materials"],
        }
        files = {label: path for label, path in files.items() if path}

        if not files:
            raise CommandError(
                "Pass at least one of --raw-materials, --packaging-materials with a file path."
            )

        all_rows = {}
        for label, path in files.items():
            rows = self._parse_workbook(path, label)
            all_rows[label] = rows
            self.stdout.write(f"{label}: parsed {len(rows)} rows from {path}")

        if dry_run:
            total = sum(len(rows) for rows in all_rows.values())
            self.stdout.write(
                self.style.WARNING(f"--dry-run: nothing written ({total} rows parsed).")
            )
            return

        products_created = products_updated = 0
        inventory_created = inventory_updated = inventory_skipped = 0

        with transaction.atomic():
            for label, rows in all_rows.items():
                # Task #148 (client review, 2026-11-04): the client's two
                # files ARE the one honest signal for "Packaging vs Food".
                # Client review (2026-11-09): that signal now sets
                # product_type directly (Packaging Material vs Raw
                # Material) instead of tagging category=PACKAGING on a
                # MATERIAL row -- category stays blank either way.
                product_type = (
                    Product.ProductType.PACKAGING
                    if label == "Packaging Materials"
                    else Product.ProductType.MATERIAL
                )
                for row in rows:
                    product, created = self._upsert_product(row, product_type)
                    products_created += created
                    products_updated += not created

                    branch = Branch.objects.filter(code=row["branch_code"]).first()
                    if branch is None:
                        raise CommandError(
                            f"{label}: unknown Branch Code '{row['branch_code']}' for "
                            f"'{row['name']}'. Expected an existing Branch.code."
                        )

                    inv, inv_created = Inventory.objects.get_or_create(
                        branch=branch,
                        product=product,
                        defaults={
                            "quantity_on_hand": row["quantity_on_hand"],
                            "reorder_threshold": row["reorder_threshold"],
                        },
                    )
                    if inv_created:
                        inventory_created += 1
                    elif force:
                        inv.quantity_on_hand = row["quantity_on_hand"]
                        inv.reorder_threshold = row["reorder_threshold"]
                        inv.save(update_fields=["quantity_on_hand", "reorder_threshold"])
                        inventory_updated += 1
                    else:
                        inventory_skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Products: {products_created} created, {products_updated} updated. "
                f"Inventory: {inventory_created} created, {inventory_updated} overwritten "
                f"(--force), {inventory_skipped} left untouched (already existed)."
            )
        )

    # -- parsing -----------------------------------------------------

    def _parse_workbook(self, path, label):
        try:
            wb = openpyxl.load_workbook(path, data_only=True)
        except FileNotFoundError as exc:
            raise CommandError(f"File not found: {path}") from exc

        ws = wb.active
        header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
        # Header cells carry trailing whitespace in the client's real
        # files (e.g. "Quantity on Hand ") -- stripped so column lookup
        # doesn't depend on reproducing that exactly.
        headers = [(str(h).strip() if h is not None else "") for h in header_row]
        missing = [c for c in REQUIRED_COLUMNS if c not in headers]
        if missing:
            raise CommandError(f"{label}: missing expected column(s): {', '.join(missing)}")
        index = {name: headers.index(name) for name in REQUIRED_COLUMNS}

        rows = []
        for raw in ws.iter_rows(min_row=2, values_only=True):
            if raw[index["Name"]] in (None, ""):
                continue  # trailing blank row

            name = str(raw[index["Name"]]).strip()
            unit_raw = str(raw[index["Unit of Measure"]]).strip().lower()
            unit = UNIT_MAP.get(unit_raw)
            if unit is None:
                raise CommandError(
                    f"{label}: '{name}' has unit of measure '{unit_raw}', which has no "
                    "mapping to Product.UnitOfMeasure. Add it to UNIT_MAP before importing."
                )

            try:
                unit_cost = Decimal(str(raw[index["Unit Cost"]]))
                quantity_on_hand = Decimal(str(raw[index["Quantity on Hand"]]))
                reorder_threshold = Decimal(str(raw[index["Reorder Threshold"]]))
            except (InvalidOperation, TypeError) as exc:
                raise CommandError(f"{label}: '{name}' has a non-numeric cost/quantity.") from exc

            rows.append(
                {
                    "name": name,
                    "supplier": (raw[index["Supplier"]] or "").strip(),
                    "unit": unit,
                    "unit_cost": unit_cost,
                    "quantity_on_hand": quantity_on_hand,
                    "reorder_threshold": reorder_threshold,
                    "branch_code": str(raw[index["Branch Code"]]).strip(),
                    "is_active": bool(raw[index["Is Active"]]),
                }
            )
        return rows

    # -- import ---------------------------------------------------------

    def _upsert_product(self, row, product_type):
        product, created = Product.objects.update_or_create(
            name=row["name"],
            defaults={
                "price": row["unit_cost"],
                "product_type": product_type,
                "unit_of_measure": row["unit"],
                "supplier": row["supplier"],
                "is_active": row["is_active"],
                "category": "",
            },
        )
        return product, created
