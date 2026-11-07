"""
Tests for ProductPackaging (Consolidated Task Tracker "Packaging material
consumption tracking" -- 2026-11-07 scoped options, Option A): the
packaging bill-of-materials model itself. The actual deduction-on-sale
behavior it drives is tested where that hook lives, in
apps.pos.tests.test_payment_and_inventory.
"""

import pytest
from django.db import IntegrityError

from apps.inventory.models import Product, ProductPackaging

pytestmark = pytest.mark.django_db


@pytest.fixture
def latte():
    return Product.objects.create(
        name="Spanish Latte", price="125.00", product_type=Product.ProductType.FINISHED_GOOD
    )


@pytest.fixture
def cup():
    return Product.objects.create(
        name="16oz Cup",
        price="3.00",
        product_type=Product.ProductType.MATERIAL,
        category=Product.Category.PACKAGING,
    )


class TestProductPackagingModel:
    def test_str_shows_quantity_and_both_products(self, latte, cup):
        mapping = ProductPackaging.objects.create(
            finished_good=latte, packaging_material=cup, quantity_per_unit="1"
        )
        assert "16oz Cup" in str(mapping)
        assert "Spanish Latte" in str(mapping)

    def test_quantity_per_unit_defaults_to_one(self, latte, cup):
        mapping = ProductPackaging.objects.create(finished_good=latte, packaging_material=cup)
        assert mapping.quantity_per_unit == 1

    def test_same_pair_cannot_be_mapped_twice(self, latte, cup):
        ProductPackaging.objects.create(finished_good=latte, packaging_material=cup)
        with pytest.raises(IntegrityError):
            ProductPackaging.objects.create(finished_good=latte, packaging_material=cup)

    def test_one_finished_good_can_require_several_packaging_materials(self, latte, cup):
        lid = Product.objects.create(
            name="Cup Lid",
            price="1.00",
            product_type=Product.ProductType.MATERIAL,
            category=Product.Category.PACKAGING,
        )
        ProductPackaging.objects.create(finished_good=latte, packaging_material=cup)
        ProductPackaging.objects.create(finished_good=latte, packaging_material=lid)

        assert latte.packaging_requirements.count() == 2
