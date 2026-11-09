"""
One-off import of the client's real Bakery_Products.xlsx -- a price list
of bakery/pasalubong items (Silvanas, Ensaymada, Cheeserolls, Caramel
Bites, etc.), supplied to resolve the Finished Products tab's missing
pre-made product data (client review, 2026-11-09).

Unlike import_raw_and_packaging_materials.py, this file is a price list,
not a stock snapshot: one flat table of Product Name, Category,
Description, Size/Variant, Price (PHP), with no branch, quantity,
reorder threshold, or supplier column at all.

Several product names repeat with a different Size/Variant and price
(e.g. "Silvanas": Box of 10 @ P380, Box of 20 @ P760) -- Product.name
has a case-insensitive unique constraint among active products
(unique_active_product_name_ci), so these can't share one Product row.
Client review (2026-11-09) confirmed each (name, variant) pair should
become its own distinct, fully real catalog Product, with the variant
folded into the name (e.g. "Silvanas (Box of 10)") -- not squeezed into
Product.price/large_price's two-tier Small/Large shape, which doesn't
fit arbitrary box-size variants. A row with no Size/Variant at all
(e.g. "Banana Bread") keeps its bare name.

Every row here is tagged Product.Category.BAKERY ("Bakery & Pasalubong"),
the closest existing category -- the file's own finer groupings
(Silvanas / Soft & Fluffy / Bakery Corner) don't correspond to any
category this system tracks and aren't preserved anywhere (client
review, 2026-11-09). The file's "Description" column similarly has
nowhere to go -- Product has no description field -- and is
intentionally not imported.

No quantity/branch data exists in this file at all, so on its own this
command only creates/updates Product catalog rows -- without a real
Inventory row, a product doesn't appear on Product Availability or in
POS (see services.get_product_availability's own docstring). Client
review (2026-11-09): provision every imported product at 0
quantity_on_hand at every active, non-commissary branch (the same
Branch.objects.filter(is_active=True, is_commissary=False) set
stock_transfers' own destination_branches and get_product_availability
already use elsewhere -- Batangas City, Lipa City, and Alangilan, which
is a real retail branch like the other two, just synced via KaHero
rather than live POS), so each becomes real, visible,
zero-stock-until-counted catalog data -- never a fabricated quantity.

Idempotent, matching import_raw_and_packaging_materials.py's own
convention: Product fields are kept in sync via update_or_create keyed
by name (safe to re-run if the price list changes), and an Inventory
row's quantity_on_hand is only ever set on first creation (via
get_or_create's defaults=) -- re-running this must never reset real,
already-adjusted stock back to 0.

Usage:
    python manage.py import_bakery_products --file /path/to/Bakery_Products.xlsx --dry-run
    python manage.py import_bakery_products --file /path/to/Bakery_Products.xlsx
"""

from decimal import Decimal, InvalidOperation

import openpyxl
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import Branch
from apps.inventory.models import Inventory, Product

REQUIRED_COLUMNS = ["Product Name", "Category", "Description", "Size/Variant", "Price (₱)"]


class Command(BaseCommand):
    help = (
        "Imports the client's real Bakery_Products.xlsx price list as Finished Good "
        "Products, provisioned at 0 stock across every active, non-commissary branch."
    )

    def add_arguments(self, parser):
        parser.add_argument("--file", type=str, required=True, help="Path to Bakery_Products.xlsx")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and report counts without writing anything to the database.",
        )

    def handle(self, *args, **options):
        path = options["file"]
        dry_run = options["dry_run"]

        rows = self._parse_workbook(path)
        self.stdout.write(f"Bakery Products: parsed {len(rows)} rows from {path}")

        if dry_run:
            self.stdout.write(
                self.style.WARNING(f"--dry-run: nothing written ({len(rows)} rows parsed).")
            )
            return

        branches = list(Branch.objects.filter(is_active=True, is_commissary=False))
        if not branches:
            raise CommandError("No active, non-commissary branches found to provision stock at.")

        products_created = products_updated = 0
        inventory_created = inventory_skipped = 0

        with transaction.atomic():
            for row in rows:
                product, created = Product.objects.update_or_create(
                    name=row["name"],
                    defaults={
                        "price": row["price"],
                        "product_type": Product.ProductType.FINISHED_GOOD,
                        "category": Product.Category.BAKERY,
                        "is_active": True,
                    },
                )
                products_created += created
                products_updated += not created

                for branch in branches:
                    _, inv_created = Inventory.objects.get_or_create(
                        branch=branch, product=product, defaults={"quantity_on_hand": 0}
                    )
                    if inv_created:
                        inventory_created += 1
                    else:
                        inventory_skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Products: {products_created} created, {products_updated} updated. "
                f"Inventory: {inventory_created} created at 0 on hand, "
                f"{inventory_skipped} left untouched (already existed)."
            )
        )

    # -- parsing -----------------------------------------------------

    def _parse_workbook(self, path):
        try:
            wb = openpyxl.load_workbook(path, data_only=True)
        except FileNotFoundError as exc:
            raise CommandError(f"File not found: {path}") from exc

        ws = wb.active
        header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
        headers = [(str(h).strip() if h is not None else "") for h in header_row]
        missing = [c for c in REQUIRED_COLUMNS if c not in headers]
        if missing:
            raise CommandError(f"Missing expected column(s): {', '.join(missing)}")
        index = {name: headers.index(name) for name in REQUIRED_COLUMNS}

        rows = []
        seen_names = set()
        for raw in ws.iter_rows(min_row=2, values_only=True):
            if raw[index["Product Name"]] in (None, ""):
                continue  # trailing blank row

            base_name = str(raw[index["Product Name"]]).strip()
            variant = raw[index["Size/Variant"]]
            variant = str(variant).strip() if variant not in (None, "") else ""
            name = f"{base_name} ({variant})" if variant else base_name

            if name.lower() in seen_names:
                raise CommandError(
                    f"'{name}' appears more than once in this file with the same "
                    "name/variant combination -- each (Product Name, Size/Variant) pair "
                    "must resolve to a unique catalog name."
                )
            seen_names.add(name.lower())

            price_cell = raw[index["Price (₱)"]]
            try:
                price = Decimal(str(price_cell))
            except (InvalidOperation, TypeError) as exc:
                raise CommandError(f"'{name}' has a non-numeric price: {price_cell!r}.") from exc

            rows.append({"name": name, "price": price})
        return rows
