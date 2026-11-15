"""
Richer, clearly-labeled synthetic history for a small set of "hero"
(branch, product) pairs -- picked to visibly demonstrate trend and
weekly seasonality in the Demand Forecasting demo, not just clear the
bare 3-non-zero-day floor (that's a DIFFERENT, already-existing script:
scripts/backfill_thin_forecast_pairs.py -- run that too, separately,
for the other ~41 thin pairs; this script does not touch those).

Reuses the SAME safety-reviewed primitives as that script rather than
reimplementing them: _create_synthetic_sale() (stock pre-check, atomic
transaction.rollback-on-failure, inventory compensation -- the exact
logic that was hardened after the real 2026-11-06 production incident)
and the same dedicated bot identity (BOT_USERNAME =
"synthetic_backfill_bot"). Sharing that identity is intentional and
by-design (see that script's own docstring, safety property 2):
`SalesTransaction.objects.filter(cashier__username=BOT_USERNAME)` alone
isolates every synthetic row either script has ever created.

Safety properties (same as backfill_thin_forecast_pairs.py):
  1. INSERT-only. No existing row is ever updated or deleted.
  2. Every synthetic sale's cashier is the dedicated bot account.
  3. A synthetic sale is only ever placed on a day that pair's REAL
     daily sales series already shows as zero -- never layered on an
     existing real sale.
  4. Quantity is small and weekday/weekend-patterned (see HERO_PAIRS
     below), not an arbitrary volume.
  5. Dated within the last 84 days (12 full weeks) so it falls inside
     the (now 365-day) forecasting window, and within the real
     2024-2026 client data range -- never backdated outside it.
  6. Dry run by default (this script's own `run()` defaults to
     dry_run=True, same convention) -- prints exactly what it would
     create without writing anything until you explicitly approve a
     live run.
  7. Separate manifest file (hero_demo_manifest.json) and will get its
     own paired removal script once you've reviewed this one, same as
     backfill_thin_forecast_pairs.py / remove_synthetic_backfill.py.

Usage (dry run first -- writes nothing):
    python manage.py shell --settings=config.settings.production -c \
        "from scripts.backfill_hero_demo_patterns import run; run(dry_run=True)"

Then, only after reviewing the dry-run output:
    python manage.py shell --settings=config.settings.production -c \
        "from scripts.backfill_hero_demo_patterns import run; run(dry_run=False)"
"""

import json
import os
import random
import sys

import django

if not django.apps.apps.ready:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    django.setup()

from django.utils import timezone  # noqa: E402

from apps.accounts.models import Branch  # noqa: E402
from apps.inventory.models import Product  # noqa: E402
from apps.forecasting.ml.data_prep import build_daily_sales_series  # noqa: E402
from scripts.backfill_thin_forecast_pairs import (  # noqa: E402
    BOT_USERNAME,
    _create_synthetic_sale,
    ensure_bot_user,
)

random.seed(20261115)  # reproducible which days get a sale, same spirit as the other scripts

DAYS_BACK = 84  # 12 full weeks -- plenty to show a repeating weekly pattern
MANIFEST_PATH = os.path.join(os.path.dirname(__file__), "hero_demo_manifest.json")

# 2-3 products per branch, chosen to be ones you'll actually click
# through in the demo. Batangas/Lipa picks lean on products that
# already have SOME real sales (per the 2026-11-15 audit) so the
# synthetic days sit alongside real ones; Alangilan has no real
# multi-year history at all (its historical import failed), so its
# hero pairs are built entirely from this script plus whatever test
# imports already exist -- worth saying so plainly if asked.
HERO_PAIRS = [
    ("BATANGAS", "Cafe Latte"),
    ("BATANGAS", "Java Chip Frappe"),
    ("BATANGAS", "Carbonara"),
    ("LIPA", "Cafe Latte"),
    ("LIPA", "Americano"),
    ("LIPA", "Brownies"),
    ("ALANGILAN", "Americano"),
    ("ALANGILAN", "Spanish Latte"),
    ("ALANGILAN", "Classic Sans Rival (slice)"),
]


def _quantity_for(day_date):
    """Weekday/weekend pattern -- the actual signal Holt-Winters' weekly
    seasonal component is meant to pick up. Saturday/Sunday (5, 6) get a
    clear bump; Friday a smaller one; weekdays stay modest. Small random
    jitter so it doesn't look like a perfectly flat, obviously-fake
    repeat of the same number every week."""
    weekday = day_date.weekday()  # Mon=0 ... Sun=6
    if weekday in (5, 6):  # weekend
        return random.randint(4, 6)
    if weekday == 4:  # Friday
        return random.randint(2, 4)
    return random.randint(1, 3)


def _fill_probability_for(day_date):
    """Not every day gets a sale -- a real product doesn't sell literally
    every single day. Weekends sell almost always; weekdays sell most
    but not all of the time, so some real-looking zero days remain."""
    weekday = day_date.weekday()
    return 0.92 if weekday in (5, 6) else 0.7


def plan_hero_pair(branch, product):
    """Read-only: looks at the REAL daily series for this pair over the
    last DAYS_BACK days and returns the list of (date, quantity) that
    would be created -- only on days the real series shows as zero.

    Matches by calendar date (idx.date()), not by reconstructing a
    timestamp to look up directly. build_daily_sales_series anchors its
    index to UTC midnight (timezone.now() is UTC-aware; .replace(hour=0)
    zeroes the UTC hour, not the local one), while a naive
    timezone.make_aware(...) here would anchor to Asia/Manila midnight --
    an 8-hour offset that makes every key miss by a day and every
    lookup silently return "not found". Caught via debug_hero_lookup.py
    returning 0 candidates for every single hero pair on the first live
    dry run (2026-11-15) -- confirmed the series itself had hundreds of
    real zero days, so the lookup, not the data, was wrong."""
    today = timezone.localdate()
    series = build_daily_sales_series(branch, product, days_history=365)
    zero_dates = {idx.date() for idx, qty in series.items() if qty == 0}
    candidates = []
    for offset in range(DAYS_BACK, 0, -1):
        day = today - timezone.timedelta(days=offset)
        if day not in zero_dates:
            continue  # already has a real sale (or outside the series range) -- never touch it
        if random.random() > _fill_probability_for(day):
            continue  # deliberately left as a zero day, for realism
        candidates.append((day, _quantity_for(day)))
    return candidates


def run(dry_run=True):
    print("\n" + "=" * 70)
    print("Hero-pair demo pattern backfill" + (" (DRY RUN)" if dry_run else ""))
    print("=" * 70)

    plans = []
    for branch_code, product_name in HERO_PAIRS:
        try:
            branch = Branch.objects.get(code=branch_code)
            product = Product.objects.get(name=product_name, is_active=True)
        except (Branch.DoesNotExist, Product.DoesNotExist) as exc:
            print(f"  SKIP {product_name} @ {branch_code} -- not found in catalog: {exc}")
            continue
        candidates = plan_hero_pair(branch, product)
        plans.append({"branch": branch, "product": product, "days": candidates})
        print(
            f"  {product.name!r} @ {branch.name}: would add {len(candidates)} "
            f"synthetic sale day(s) out of {DAYS_BACK} days (only on real-zero days)."
        )

    total_planned = sum(len(p["days"]) for p in plans)
    print(f"\nTotal planned synthetic sale-days across all hero pairs: {total_planned}")

    if dry_run:
        print("\n" + "=" * 70)
        print("Dry run complete. No database changes were made.")
        print("Review the counts above, then call run(dry_run=False) to actually write these.")
        print("=" * 70)
        return

    bot_user = ensure_bot_user()
    pk_floor = 0
    from apps.pos.models import SalesTransaction

    last = SalesTransaction.objects.order_by("-pk").first()
    pk_floor = last.pk if last else 0

    created_rows = []
    for plan in plans:
        branch, product = plan["branch"], plan["product"]
        for day, qty in plan["days"]:
            result = _create_synthetic_sale(bot_user, branch, product, day, qty)
            if result is not None:
                created_rows.append(
                    {
                        "branch": branch.code,
                        "product": product.name,
                        "date": day.isoformat(),
                        "quantity": qty,
                        "transaction_pk": result.pk,
                    }
                )

    print(f"\nCreated {len(created_rows)} synthetic sale(s) across {len(plans)} hero pair(s).")

    with open(MANIFEST_PATH, "w") as f:
        json.dump(
            {
                "created_at": timezone.now().isoformat(),
                "bot_username": BOT_USERNAME,
                "pk_floor_before_this_run": pk_floor,
                "rows": created_rows,
            },
            f,
            indent=2,
        )
    print(f"Manifest written to {MANIFEST_PATH}")

    print("\n" + "=" * 70)
    print("Done. Re-run audit_forecast_coverage.py, then regenerate forecasts:")
    print(
        "  python manage.py shell --settings=config.settings.production -c "
        '"from apps.forecasting.services import run_forecast_for_all_products as f; f()"'
    )
    print("=" * 70)


if __name__ == "__main__":
    run()
