"""
Data migration (client review, 2026-11-09): moves every existing Product
that was tagged product_type=MATERIAL, category=PACKAGING onto the new
product_type=PACKAGING, and clears its now-redundant category (blank,
same as every other MATERIAL/PACKAGING row -- category is a POS-tab
concept these rows were never browsed by in the first place).

The selection is the exact same category == "PACKAGING" tag
import_raw_and_packaging_materials.py has always set for every row
imported from the client's own Packaging_Materials.xlsx (tape, boxes,
cups, lids, bags, containers, etc.) -- nothing here is guessed from a
product's name or unit of measure. Prints each product it moves to the
migration's own stdout, so running `migrate` on a real database shows
exactly what changed; apps.inventory.management.commands.
list_packaging_migration_candidates runs the identical, read-only query
beforehand for review, without touching any data.

Reversible: moving back targets every current product_type=PACKAGING
row and restores product_type=MATERIAL, category=PACKAGING on all of
them -- safe because PACKAGING didn't exist as a value before this
migration, so nothing else could have created a PACKAGING row for the
reversal to mistakenly sweep up.
"""

from django.db import migrations


def move_packaging_items_forward(apps, schema_editor):
    Product = apps.get_model("inventory", "Product")
    candidates = Product.objects.filter(product_type="MATERIAL", category="PACKAGING").order_by(
        "name"
    )

    moved_pks = list(candidates.values_list("pk", flat=True))
    for product in candidates:
        print(f"  Moving to Packaging Material: {product.name!r} (id={product.pk})")

    candidates.update(product_type="PACKAGING", category="")

    print(f"  {len(moved_pks)} product(s) moved from Raw Material to Packaging Material.")


def move_packaging_items_backward(apps, schema_editor):
    Product = apps.get_model("inventory", "Product")
    # No reliable pk list survives a fresh `migrate` process (the module-
    # level _MOVED_PKS above only helps within the same run) -- a
    # reversal instead targets every current PACKAGING row, which is
    # exactly the set this migration (and nothing else, since PACKAGING
    # didn't exist before it) could have created.
    reverted = Product.objects.filter(product_type="PACKAGING")
    count = reverted.count()
    reverted.update(product_type="MATERIAL", category="PACKAGING")
    print(f"  {count} product(s) moved back from Packaging Material to Raw Material.")


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0021_packaging_material_product_type"),
    ]

    operations = [
        migrations.RunPython(move_packaging_items_forward, move_packaging_items_backward),
    ]
