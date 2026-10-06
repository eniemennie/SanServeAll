"""
Tests for scripts/seed_menu_catalog.py.

Imports the seed script's `run()` function directly, same convention as
test_seed_demo_data.py, so it participates in the normal pytest-django
transaction/rollback per test.
"""

import pytest

from apps.inventory.models import Product
from apps.pos.models import AddOn

pytestmark = pytest.mark.django_db


def _run_seed():
    from scripts.seed_menu_catalog import run

    run()


class TestSeedMenuCatalog:
    def test_all_day_breakfast_is_deliberately_excluded(self):
        """The owner explicitly asked for this section to be left out --
        a real regression guard, not just an initial check, so it can't
        silently come back if the script is extended later."""
        _run_seed()
        assert not Product.objects.filter(category=Product.Category.BREAKFAST).exists()
        for name in ["Tapa", "Tocino", "Longanisa", "Hungarian"]:
            assert not Product.objects.filter(name__icontains=name).exists()

    def test_coffee_loaded_as_separate_hot_and_iced_products(self):
        """Hot/Iced isn't a size, so it's two real products, not one
        product with a mismatched 'Large' price standing in for Iced."""
        _run_seed()
        hot = Product.objects.get(name="Americano (Hot)")
        iced = Product.objects.get(name="Americano (Iced)")
        assert hot.price == 80 and iced.price == 90
        assert hot.pk != iced.pk
        assert hot.large_price is None  # not misused to mean "Iced"

    def test_cakes_loaded_as_four_separate_sized_products_per_flavor(self):
        _run_seed()
        sizes = ["Mini", "Solo", "7in Square", "8in Round"]
        for size in sizes:
            assert Product.objects.filter(
                name=f"Classic Cashew ({size})", category=Product.Category.CAKES
            ).exists()
        assert Product.objects.get(name="Classic Cashew (8in Round)").price == 840

    def test_best_seller_flags_match_the_real_menu_badges(self):
        _run_seed()
        assert Product.objects.get(name="Classic Cashew (Mini)").is_best_seller is True
        assert Product.objects.get(name="Almond (Mini)").is_best_seller is False
        assert Product.objects.get(name="Lasagna").is_best_seller is True
        assert Product.objects.get(name="Chicken Pesto").is_best_seller is False

    def test_bakery_category_used_for_items_with_no_other_fit(self):
        _run_seed()
        assert Product.objects.get(name="Mixed Nuts (200g)").category == Product.Category.BAKERY
        assert (
            Product.objects.get(name="Classic Ensaymada (Piece)").category
            == Product.Category.BAKERY
        )

    def test_two_tier_items_use_the_real_price_and_large_price_fields(self):
        """Items that genuinely only have 2 real tiers (Small/Big,
        Tub/Family) use the model's existing 2-tier support correctly,
        unlike Hot/Iced and the 4-tier cakes."""
        _run_seed()
        bites = Product.objects.get(name="Original Caramel Bites")
        assert bites.price == 105 and bites.large_price == 200
        assert bites.has_size_options is True

    def test_addons_include_both_condense_volumes(self):
        _run_seed()
        assert AddOn.objects.get(name="Extra Condense (25ml)").price == 15
        assert AddOn.objects.get(name="Extra Condense (40ml)").price == 25

    def test_script_is_safe_to_run_more_than_once(self):
        _run_seed()
        count_after_first_run = Product.objects.count()
        _run_seed()
        assert Product.objects.count() == count_after_first_run

    def test_expected_total_product_count(self):
        """Pinned to a real number so an accidental duplicate or a
        silently-dropped item changes this test, not just a vague
        'some products exist' check."""
        _run_seed()
        # 7 Hot + 7 Iced (coffee) + 4 Sea Salt + 5 Coffee Frappe +
        # 6 Non-Coffee Frappe + 7 Specialty + 4 Pasta + 5 Desserts +
        # 24 Cakes (6 flavors x 4 sizes) + 2 Silvanas + 5 Soft & Fluffy +
        # 9 Bakery Corner = 85
        assert Product.objects.count() == 85
        assert (
            AddOn.objects.filter(
                name__in=[
                    "Extra Espresso Shot",
                    "Oat Milk",
                    "Extra Pump of Sauce",
                    "Extra Pump of Syrup",
                    "Extra Condense (25ml)",
                    "Extra Condense (40ml)",
                ]
            ).count()
            == 6
        )
