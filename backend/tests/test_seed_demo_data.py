"""
Tests for scripts/seed_demo_data.py (Row 11).

Imports the seed script's `run()` function directly rather than shelling
out, so it participates in the normal pytest-django transaction/rollback
per test.
"""

import pytest
from django.conf import settings

from apps.accounts.models import Branch, Role
from apps.inventory.models import Product

pytestmark = pytest.mark.django_db


def _run_seed():
    from scripts.seed_demo_data import run

    run()


class TestSeedDemoData:
    def test_creates_three_customer_facing_branches_plus_commissary(self):
        _run_seed()
        assert Branch.objects.count() == 4
        assert set(Branch.objects.values_list("code", flat=True)) == {
            "BATANGAS",
            "ALANGILAN",
            "LIPA",
            "COMMISSARY",
        }

    def test_exactly_one_commissary_branch(self):
        _run_seed()
        commissary_branches = Branch.objects.filter(is_commissary=True)
        assert commissary_branches.count() == 1
        assert commissary_branches.first().code == "COMMISSARY"

    def test_exactly_one_branch_is_kahero_and_matches_settings(self):
        _run_seed()
        kahero_branches = Branch.objects.filter(is_kahero_branch=True)
        assert kahero_branches.count() == 1
        assert kahero_branches.first().name == settings.KAHERO_BRANCH

    def test_creates_three_roles(self):
        _run_seed()
        assert Role.objects.count() == 3
        role_names = set(Role.objects.values_list("name", flat=True))
        assert role_names == {Role.OWNER_ADMIN, Role.BRANCH_STAFF, Role.COMMISSARY_STAFF}

    def test_running_twice_does_not_create_duplicates(self):
        _run_seed()
        _run_seed()
        assert Branch.objects.count() == 4
        assert Role.objects.count() == 3

    def test_running_twice_keeps_kahero_flag_correct(self):
        _run_seed()
        _run_seed()
        assert Branch.objects.filter(is_kahero_branch=True).count() == 1

    def test_creates_starter_finished_goods_catalog(self):
        _run_seed()
        finished_goods = Product.objects.filter(product_type=Product.ProductType.FINISHED_GOOD)
        assert finished_goods.count() == 8
        assert finished_goods.filter(is_active=True).count() == 8

    def test_creates_starter_raw_materials(self):
        _run_seed()
        materials = Product.objects.filter(product_type=Product.ProductType.MATERIAL)
        assert materials.count() == 5

    def test_running_twice_does_not_duplicate_products(self):
        _run_seed()
        _run_seed()
        assert Product.objects.count() == 13  # 8 finished goods + 5 materials
