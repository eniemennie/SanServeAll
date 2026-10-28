"""
One-off historical backfill for the client's real per-branch daily sales
workbooks (Alangilan_Sales_2024-2026.xlsx, Batangas_Sales_2024-2026.xlsx,
Lipa_Sales_2024-2026.xlsx) -- supersedes the earlier, never-merged
import_client_data command, which was written for a different (and, it
turns out, incorrect) assumption about the data's shape: a single
combined workbook with one daily total per branch plus a separate "Raw
Mats" sheet. The real files are one "Sales" sheet per branch, with up to
three distinct revenue figures per day (Store Sales, Cafe AM Sales, Cafe
PM Sales) and a "Remarks" column that explicitly distinguishes "not
recorded" from a genuine recorded zero -- neither of which the old
command had any concept of. Raw materials data is a separate file to be
handled later; this command is sales-only.

Design decisions (confirmed with the project owner before building this):

1. Each revenue stream (Store Sales, Cafe AM Sales, Cafe PM Sales) that
   has an actual recorded value for a given day becomes its OWN
   SalesTransaction -- not summed into one daily total -- so Analytics
   can still distinguish retail/pasalubong (Store) revenue from café
   dine-in/take-out (Cafe) revenue. A day with all three recorded
   produces 3 transactions.

2. A blank cell means "not recorded" and is skipped entirely -- it is
   NEVER treated as zero. An explicit recorded zero (flagged in the
   source via a "Remarks" note such as "Cafe sales 0 in source") IS
   imported, as a genuine zero-amount completed sale.

3. Cafe AM/PM vs. Cafe Total: some rows have AM and PM individually
   recorded; others only have a single Total (flagged via a "Remarks"
   note like "Cafe AM/PM not recorded; total as entered in source").
   Importing AM+PM AND Total for the same day would double-count, so the
   rule is: import AM and PM separately when BOTH are present; otherwise,
   if Total is present, import that alone as a single "Cafe Total Sales"
   transaction. This is an implementation-level assumption, not something
   the project owner was asked about directly -- flagged here for the
   same reason past assumptions in this codebase have been flagged in
   comments rather than silently guessed at.

4. Alangilan is the codebase's designated KaHero (batch-import) branch
   for LIVE data, but this particular historical file is daily
   aggregates, not KaHero's required per-transaction format
   (transaction_date/product_name/quantity/unit_price -- see
   kahero_integration/parsers.py). So this one-time historical backfill
   is handled the same way as Batangas/Lipa, bypassing the KaHero
   pipeline entirely -- confirmed with the project owner. Alangilan's
   day-to-day LIVE sales still go through KaHero as before; only this
   historical backfill is different.

Each branch's file is independently optional on the command line, and
idempotent per (branch, date, revenue-stream label) -- safe to re-run,
and safe to run with only one or two of the three files at a time.

Usage:
    python manage.py import_branch_sales_history \
        --batangas /path/to/Batangas_Sales_2024-2026.xlsx \
        --lipa /path/to/Lipa_Sales_2024-2026.xlsx \
        --alangilan /path/to/Alangilan_Sales_2024-2026.xlsx \
        --dry-run
"""

import datetime
from decimal import Decimal

import openpyxl
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Branch, Role, User
from apps.pos.models import SalesItem, SalesTransaction

# (branch code, branch display name, is_kahero_branch, has a "Store Sales"
# column in its workbook) -- matches scripts/seed_demo_data.py's existing
# branch naming exactly, so this command reuses those same Branch rows
# rather than creating differently-named duplicates.
BRANCH_CONFIG = {
    "batangas": {"code": "BATANGAS", "name": "Batangas City", "is_kahero_branch": False},
    "lipa": {"code": "LIPA", "name": "Lipa City", "is_kahero_branch": False},
    "alangilan": {"code": "ALANGILAN", "name": "Alangilan", "is_kahero_branch": True},
}

# Fixed, distinct time-of-day per revenue stream -- purely so backdated
# transactions for the same day sort in a sensible order (Store opens
# before the café's AM shift, which precedes its PM shift); has no
# bearing on correctness, since dedup below keys off the stream label
# itself, not the time.
STREAM_TIME_OF_DAY = {
    "Store Sales": datetime.time(9, 0),
    "Cafe AM Sales": datetime.time(10, 0),
    "Cafe PM Sales": datetime.time(15, 0),
    "Cafe Total Sales": datetime.time(12, 0),
}


class Command(BaseCommand):
    help = (
        "Import the client's real per-branch daily sales workbooks "
        "(Alangilan/Batangas/Lipa Sales 2024-2026.xlsx) as backdated, "
        "completed historical SalesTransaction rows."
    )

    def add_arguments(self, parser):
        parser.add_argument("--batangas", type=str, default=None, help="Path to Batangas's .xlsx")
        parser.add_argument("--lipa", type=str, default=None, help="Path to Lipa's .xlsx")
        parser.add_argument("--alangilan", type=str, default=None, help="Path to Alangilan's .xlsx")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and report counts without writing anything to the database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        branch_files = {
            "batangas": options["batangas"],
            "lipa": options["lipa"],
            "alangilan": options["alangilan"],
        }
        branch_files = {k: v for k, v in branch_files.items() if v}

        if not branch_files:
            raise CommandError(
                "Pass at least one of --batangas, --lipa, --alangilan with a file path."
            )

        all_rows = {}
        for key, path in branch_files.items():
            config = BRANCH_CONFIG[key]
            try:
                wb = openpyxl.load_workbook(path, data_only=True)
            except FileNotFoundError as exc:
                raise CommandError(f"File not found: {path}") from exc
            rows = self._parse_sheet(wb["Sales"], has_store_sales=key != "alangilan")
            all_rows[key] = rows
            self.stdout.write(
                f"{config['name']}: parsed {len(rows)} importable (branch, date, stream) rows "
                f"from {path}"
            )

        if dry_run:
            self.stdout.write(self.style.WARNING("--dry-run: nothing written."))
            return

        total_created, total_skipped = 0, 0
        with transaction.atomic():
            for key, rows in all_rows.items():
                config = BRANCH_CONFIG[key]
                branch = self._ensure_branch(config)
                cashier = self._ensure_import_user(branch, key)
                created, skipped = self._import_rows(rows, branch, cashier)
                total_created += created
                total_skipped += skipped
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{config['name']}: {created} transactions created, "
                        f"{skipped} already existed."
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(f"Total: {total_created} created, {total_skipped} skipped.")
        )

    # -- parsing -----------------------------------------------------

    def _parse_sheet(self, ws, has_store_sales):
        """Reads the daily (left-hand) table only -- the right-hand
        monthly aggregate table is a redundant summary view in the source
        file, not imported separately. Returns a flat list of
        {"date", "stream", "amount"} dicts, one per non-blank revenue
        figure, already applying rule #3's AM/PM-vs-Total precedence."""
        rows = []
        for raw in ws.iter_rows(min_row=2, values_only=True):
            if has_store_sales:
                date, _year, _month, _dow, store, cafe_am, cafe_pm, cafe_total = raw[:8]
            else:
                date, _year, _month, _dow, cafe_am, cafe_pm, cafe_total = raw[:7]
                store = None

            if not isinstance(date, datetime.datetime):
                continue
            date = date.date()

            if store is not None:
                rows.append({"date": date, "stream": "Store Sales", "amount": store})

            if cafe_am is not None and cafe_pm is not None:
                rows.append({"date": date, "stream": "Cafe AM Sales", "amount": cafe_am})
                rows.append({"date": date, "stream": "Cafe PM Sales", "amount": cafe_pm})
            elif cafe_total is not None:
                # Either AM, PM, or both are missing for this day -- the
                # only reliable figure left is the total as entered in
                # the source (rule #3). Importing AM/PM individually here
                # would either fabricate a number that was never recorded
                # or double-count against this total.
                rows.append({"date": date, "stream": "Cafe Total Sales", "amount": cafe_total})
            # else: no cafe data recorded for this day at all -- skip,
            # per rule #2 (blank means "not recorded", never zero).

        return rows

    # -- setup ---------------------------------------------------------

    def _ensure_branch(self, config):
        branch, _ = Branch.objects.get_or_create(
            code=config["code"],
            defaults={"name": config["name"], "is_kahero_branch": config["is_kahero_branch"]},
        )
        return branch

    def _ensure_import_user(self, branch, key):
        role, _ = Role.objects.get_or_create(name=Role.BRANCH_STAFF)
        username = f"historical_import_{key}"
        user, _ = User.objects.get_or_create(
            username=username,
            defaults={
                "role": role,
                "branch": branch,
                "password": make_password(None),  # unusable password -- not a real login
                "is_active": False,
            },
        )
        return user

    # -- import ---------------------------------------------------------

    def _import_rows(self, rows, branch, cashier):
        created, skipped = 0, 0
        for row in rows:
            # Idempotency keys off the actual stream label, not a
            # time-of-day encoding trick -- so re-running with the same
            # file never creates duplicates, and a day with multiple
            # streams is tracked independently per stream.
            already = SalesTransaction.objects.filter(
                branch=branch,
                cashier=cashier,
                status=SalesTransaction.Status.COMPLETED,
                completed_at__date=row["date"],
                items__custom_name=f"{row['stream']} (Imported)",
            ).exists()
            if already:
                skipped += 1
                continue

            time_of_day = STREAM_TIME_OF_DAY[row["stream"]]
            naive_dt = datetime.datetime.combine(row["date"], time_of_day)
            completed_at = timezone.make_aware(naive_dt)
            amount = Decimal(str(row["amount"]))

            tx = SalesTransaction.objects.create(
                branch=branch,
                cashier=cashier,
                status=SalesTransaction.Status.COMPLETED,
                payment_method=SalesTransaction.PaymentMethod.CASH,
                amount_tendered=amount,
                completed_at=completed_at,
            )
            SalesItem.objects.create(
                transaction=tx,
                product=None,
                custom_name=f"{row['stream']} (Imported)",
                unit_price=amount,
                quantity=1,
            )
            # created_at is auto_now_add -- backdate it after the fact so
            # the transaction sorts/reports under its real historical date.
            SalesTransaction.objects.filter(pk=tx.pk).update(created_at=completed_at)
            created += 1
        return created, skipped
