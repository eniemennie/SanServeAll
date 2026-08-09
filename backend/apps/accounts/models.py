"""
Users, Roles, Branches, RBAC, Cashier PIN, 2FA
(Row 9 of the Phase 9 WBS — Design Accounts Schema)
"""

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractUser
from django.db import models

from apps.core.models import TimestampedModel


class Branch(TimestampedModel):
    """A physical branch location of Jorge's Casa De Sans Rival.

    Exactly one branch should have is_kahero_branch=True at a time. This
    field — not a hardcoded name comparison anywhere else in the codebase —
    is the single source of truth for "which branch runs batch-import mode."
    Confirmed value (Phase 2/4 decision): Alangilan. See
    config/settings/base.py KAHERO_BRANCH for the matching env-driven config.
    """

    name = models.CharField(max_length=100, unique=True)
    code = models.SlugField(
        max_length=20,
        unique=True,
        help_text="Short unique code, e.g. 'BATANGAS', 'ALANGILAN', 'LIPA'.",
    )
    address = models.CharField(max_length=255, blank=True)
    is_kahero_branch = models.BooleanField(
        default=False,
        help_text=(
            "True only for the branch running the external KaHero POS "
            "(batch-import mode). All other branches run the native "
            "SanServeAll POS in real time."
        ),
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "branches"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Role(TimestampedModel):
    """A named role governing permissions/access level (RBAC)."""

    OWNER_ADMIN = "OWNER_ADMIN"
    BRANCH_STAFF = "BRANCH_STAFF"
    COMMISSARY_STAFF = "COMMISSARY_STAFF"

    ROLE_CHOICES = [
        (OWNER_ADMIN, "Owner / Admin"),
        (BRANCH_STAFF, "Branch Staff"),
        (COMMISSARY_STAFF, "Commissary Staff"),
    ]

    name = models.CharField(max_length=30, choices=ROLE_CHOICES, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.get_name_display()


class User(AbstractUser):
    """Central user/auth entity. Extends Django's AbstractUser rather than
    replacing it, so PBKDF2 hashing and the standard auth machinery keep
    working unchanged (Phase 3 decision) — no custom crypto code needed.
    """

    role = models.ForeignKey(
        Role,
        on_delete=models.PROTECT,
        related_name="users",
        null=True,  # null only until every user is assigned a role during onboarding
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="staff",
        null=True,
        blank=True,
        help_text="Null for OWNER_ADMIN users, who are not scoped to a single branch.",
    )
    employee_id = models.CharField(max_length=20, unique=True, null=True, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return self.get_full_name() or self.username


class CashierPIN(TimestampedModel):
    """A short numeric PIN layered on top of an already-authenticated
    BRANCH_STAFF session — not a replacement for the full Django login.
    Unlocks POS actions for that branch session only (Phase 2 design).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cashier_pin",
    )
    hashed_pin = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def set_pin(self, raw_pin: str) -> None:
        """Hashes and stores a new PIN using Django's own password hasher,
        so PINs get the same PBKDF2 protection as account passwords."""
        self.hashed_pin = make_password(raw_pin)

    def check_pin(self, raw_pin: str) -> bool:
        return check_password(raw_pin, self.hashed_pin)

    def __str__(self):
        return f"PIN for {self.user}"
