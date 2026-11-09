"""
Read-only preview for the 0022_move_packaging_items_to_packaging_type
data migration (client review, 2026-11-09). Lists every Product the
migration will move from Raw Material to Packaging Material -- the
exact same query that migration itself runs -- without writing anything,
so it can be run and reviewed on the real database BEFORE `migrate`
actually applies the change.

Usage:
    python manage.py list_packaging_migration_candidates
"""

from django.core.management.base import BaseCommand

from apps.inventory.models import Product


class Command(BaseCommand):
    help = (
        "Lists every Product that the 0022_move_packaging_items_to_packaging_type "
        "migration will move from Raw Material to Packaging Material. Read-only -- "
        "run this before `migrate` to review the list first."
    )

    def handle(self, *args, **options):
        candidates = Product.objects.filter(
            product_type=Product.ProductType.MATERIAL, category="PACKAGING"
        ).order_by("name")

        if not candidates.exists():
            self.stdout.write("No Raw Material products are tagged Packaging -- nothing to move.")
            return

        self.stdout.write(
            f"{candidates.count()} product(s) will move from Raw Material to Packaging Material:"
        )
        for product in candidates:
            self.stdout.write(f"  - {product.name} (id={product.pk})")
