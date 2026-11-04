"""
Suppliers (task #143, client review, 2026-11-04): a new Purchasing role
manages relationships with the business's suppliers.

This is deliberately a NEW, standalone model rather than a migration of
the two pre-existing, separate, plain-text supplier fields already in the
codebase:
  - Product.supplier (apps.inventory.models) -- real catalog-level data
    imported from the client's Raw_Materials.xlsx / Packaging_Materials.xlsx
    (278 real rows, see import_raw_and_packaging_materials.py).
  - ProductionRecord.supplier (apps.production.models) -- a separate,
    per-batch free-text field.
Migrating either of those into a FK here would touch real, already-
populated client data and two other apps' models with no client ask for
that -- the client's request was for a new Suppliers concept for the new
Purchasing role to manage, not a rework of existing catalog/production
fields. Those two fields are left exactly as they are.

Confidentiality (client review, 2026-11-04): every field on a Supplier is
treated as confidential -- visible only to OWNER_ADMIN and PURCHASING
(apps.accounts.permissions.role_required/RoleRequiredMixin already gate
every view in apps.purchasing to just those two roles; Branch Staff and
Commissary Staff can't reach a Supplier record in any form, not even its
name).
"""

from django.db import models

from apps.core.models import TimestampedModel


class Supplier(TimestampedModel):
    """A supplier the business buys raw materials, packaging, or other
    goods from. Intentionally independent of Product/ProductionRecord's
    own free-text supplier fields -- see this module's docstring."""

    name = models.CharField(max_length=150, unique=True)
    contact_person = models.CharField(max_length=150, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    notes = models.TextField(
        blank=True, help_text="Payment terms, lead times, or any other internal note."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive suppliers are kept for history but hidden from new purchasing.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
