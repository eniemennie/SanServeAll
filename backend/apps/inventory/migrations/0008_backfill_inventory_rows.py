# Data migration -- part of the QA-documented fix for "Add Product"/
# "Branch-Specific Product Management" (SYSTEM_DOCUMENTATION QA report):
# POS and Inventory Monitoring now only show a product at a branch once
# a real Inventory row exists there (see inventory.services.create_product
# and apps/pos's catalog queries). Before that filter went live, every
# EXISTING product was implicitly visible everywhere regardless of
# Inventory rows. This backfill grandfathers in every product that was
# already in the catalog before this fix, at every active non-commissary
# branch, at its current quantity if a row already exists or 0 if not --
# so no product already in production use silently disappears from any
# branch's POS or Monitoring screen the moment this migration runs.
#
# Safe to run more than once (get_or_create is idempotent) and only ever
# adds rows -- never modifies or removes an existing Inventory row's
# quantity_on_hand.

from django.db import migrations


def backfill_inventory_rows(apps, schema_editor):
    Product = apps.get_model("inventory", "Product")
    Inventory = apps.get_model("inventory", "Inventory")
    Branch = apps.get_model("accounts", "Branch")

    branches = list(Branch.objects.filter(is_active=True, is_commissary=False))
    for product in Product.objects.all():
        for branch in branches:
            Inventory.objects.get_or_create(branch=branch, product=product)


def noop_reverse(apps, schema_editor):
    # Deliberately not removing the backfilled rows on reverse -- a zero-
    # quantity Inventory row is harmless, and we can't safely tell which
    # rows pre-existed vs. were created by this migration once reversed.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0007_alter_product_category"),
        ("accounts", "0004_cashierpin_failed_attempts_cashierpin_locked_until"),
    ]

    operations = [
        migrations.RunPython(backfill_inventory_rows, noop_reverse),
    ]
