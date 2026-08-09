"""
Shared base models, permissions, and utilities used by every other app.
"""
from django.db import models


class TimestampedModel(models.Model):
    """Abstract base model adding created_at/updated_at to any model that
    inherits it. Used across every app so audit timestamps are consistent."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BranchScopedModel(TimestampedModel):
    """Abstract base model for anything that belongs to a specific branch
    (sales, inventory, production records, etc.). Concrete models inheriting
    this automatically get a `branch` FK and inherited timestamps.

    Use together with apps.core.middleware branch-scoping (Week 3) to ensure
    BRANCH_STAFF/COMMISSARY_STAFF users only ever see their own branch's rows.
    """

    branch = models.ForeignKey(
        "accounts.Branch",
        on_delete=models.PROTECT,
        related_name="%(class)ss",
    )

    class Meta:
        abstract = True
