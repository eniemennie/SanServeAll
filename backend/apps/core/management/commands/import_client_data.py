"""
One-off seed command for the client-supplied "Sales 2024-2026 and Raw
Mats.xlsx" workbook.

Imports two things, each idempotent (safe to re-run):

1. Raw Mats sheet -> Product rows (product_type=MATERIAL) + an Inventory
   row per target branch, quantity_on_hand defaulted to 0 since the
   client's sheet has no stock counts, only item names and suppliers.
   Supplier name is kept in Product's name as a comment-free source of
   truth isn't modeled yet, so it's only used for console output, not
   stored (no Supplier field exists on Product/Inventory today).

2. Sales sheet -> backdated, COMPLETED SalesTransaction rows (one per
   branch per day that has a non-blank amount), each with a single
   SalesItem (no `product` FK -- a synthetic custom_name line item,
   since the sheet only has daily totals, not a per-item breakdown).
   This seeds realistic historical volume for the Analytics dashboard
   and Performance testing, NOT real itemized sales history.

Despite the file's name, only Jan 4 - May 31, 2024 actually has
non-blank daily amounts for either branch; every other row (rest of
2024, all of 2025/2026) is a blank template placeholder and is skipped.

Usage:
    python manage.py import_client_data /path/to/Sales-2024-2026-and-Raw-Mats.xlsx
    python manage.py import_client_data /path/to/file.xlsx --dry-run
"""

import datetime
from decimal import Decimal

import openpyxl
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Branch, Role, User
from apps.inventory.models import Inventory, Product
from apps.pos.models import SalesItem, SalesTransaction

BRANCH_CODES = {
    "Batangas": "BATANGAS",
    "Lipa": "LIPA",
}


class Command(BaseCommand):
    help = "Import the client's Raw Mats + daily Sales workbook as seed data."

    def add_arguments(self, parser):
        parser.add_argument("xlsx_path", type=str)
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and report counts without writing anything to the database.",
        )

    def handle(self, *args, **options):
        path = options["xlsx_path"]
        dry_run = options["dry_run"]

        try:
            wb = openpyxl.load_workbook(path, data_only=True)
        except FileNotFoundError as exc:
            raise CommandError(f"File not found: {path}") from exc

        materials = self._parse_raw_mats(wb["Raw Mats"])
        sales_rows = self._parse_sales(wb["Sales"])

        self.stdout.write(f"Parsed {len(materials)} raw material rows.")
        self.stdout.write(f"Parsed {len(sales_rows)} usable daily sales rows (non-blank amounts).")

        if dry_run:
            self.stdout.write(self.style.WARNING("--dry-run: nothing written."))
            return

        with transaction.atomic():
            branches = self._ensure_branches()
            import_users = self._ensure_import_users(branches)
            mat_created, mat_existing = self._import_materials(materials, branches)
            tx_created, tx_skipped = self._import_sales(sales_rows, branches, import_users)

        self.stdout.write(
            self.style.SUCCESS(f"Materials: {mat_created} created, {mat_existing} already existed.")
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Sales transactions: {tx_created} created, "
                f"{tx_skipped} skipped (already imported)."
            )
        )

    # -- parsing -----------------------------------------------------

    def _parse_raw_mats(self, ws):
        rows = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            name, supplier = (row + (None, None))[:2]
            if not name:
                continue
            rows.append({"name": str(name).strip(), "supplier": (supplier or "").strip()})
        return rows

    def _parse_sales(self, ws):
        rows = []
        for row in ws.iter_rows(min_row=4, values_only=True):
            row = row + (None,) * 7
            bdate, bamt = row[0], row[2]
            ldate, lamt = row[4], row[6]
            if isinstance(bdate, datetime.datetime) and bamt not in (None, ""):
                rows.append(
                    {"date": bdate.date(), "branch": "Batangas", "amount": Decimal(str(bamt))}
                )
            if isinstance(ldate, datetime.datetime) and lamt not in (None, ""):
                rows.append({"date": ldate.date(), "branch": "Lipa", "amount": Decimal(str(lamt))})
        return rows

    # -- setup ---------------------------------------------------------

    def _ensure_branches(self):
        branches = {}
        for label, code in BRANCH_CODES.items():
            branch, _ = Branch.objects.get_or_create(
                code=code, defaults={"name": f"{label} City" if label != "Lipa" else "Lipa"}
            )
            branches[label] = branch
        return branches

    def _ensure_import_users(self, branches):
        role, _ = Role.objects.get_or_create(name=Role.BRANCH_STAFF)
        users = {}
        for label, branch in branches.items():
            username = f"historical_import_{label.lower()}"
            user, _ = User.objects.get_or_create(
                username=username,
                defaults={
                    "role": role,
                    "branch": branch,
                    "password": make_password(None),  # unusable password -- not a real login
                    "is_active": False,
                },
            )
            users[label] = user
        return users

    # -- import ---------------------------------------------------------

    def _import_materials(self, materials, branches):
        created, existing = 0, 0
        for row in materials:
            product, was_created = Product.objects.get_or_create(
                name=row["name"],
                product_type=Product.ProductType.MATERIAL,
                defaults={"price": Decimal("0.00")},
            )
            if was_created:
                created += 1
            else:
                existing += 1
            for branch in branches.values():
                Inventory.objects.get_or_create(
                    branch=branch, product=product, defaults={"quantity_on_hand": 0}
                )
        return created, existing

    def _import_sales(self, sales_rows, branches, import_users):
        created, skipped = 0, 0
        for row in sales_rows:
            branch = branches[row["branch"]]
            cashier = import_users[row["branch"]]
            naive_dt = datetime.datetime.combine(row["date"], datetime.time(12, 0))
            completed_at = timezone.make_aware(naive_dt)

            already = SalesTransaction.objects.filter(
                branch=branch,
                cashier=cashier,
                completed_at=completed_at,
                status=SalesTransaction.Status.COMPLETED,
            ).exists()
            if already:
                skipped += 1
                continue

            tx = SalesTransaction.objects.create(
                branch=branch,
                cashier=cashier,
                status=SalesTransaction.Status.COMPLETED,
                payment_method=SalesTransaction.PaymentMethod.CASH,
                amount_tendered=row["amount"],
                completed_at=completed_at,
            )
            SalesItem.objects.create(
                transaction=tx,
                product=None,
                custom_name="Historical Daily Sales (Imported)",
                unit_price=row["amount"],
                quantity=1,
            )
            # created_at is auto_now_add -- backdate it after the fact so
            # the transaction sorts/reports under its real historical date.
            SalesTransaction.objects.filter(pk=tx.pk).update(created_at=completed_at)
            created += 1
        return created, skipped
