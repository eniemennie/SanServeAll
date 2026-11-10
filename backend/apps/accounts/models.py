"""
Users, Roles, Branches, RBAC, Cashier PIN, 2FA
(Row 9 of the Phase 9 WBS — Design Accounts Schema)
"""

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

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
    is_commissary = models.BooleanField(
        default=False,
        help_text=(
            "True only for the centralized commissary (Phase 1 §1.1). "
            "Modeled as a Branch row so Production (Week 8) can reuse "
            "existing branch-scoping, selection, and Inventory "
            "infrastructure rather than a parallel system."
        ),
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "branches"
        ordering = ["name"]
        constraints = [
            # Enforces the "exactly one branch should have this True"
            # invariant documented above at the database level -- without
            # this, nothing stopped two branches from both being flagged
            # True (or the flag drifting to zero), and
            # kahero_integration's `.filter(is_kahero_branch=True).first()`
            # would silently pick an arbitrary one rather than erroring.
            # A partial unique index (only rows where the flag is True)
            # rather than a plain unique=True, since False is allowed on
            # every other branch.
            models.UniqueConstraint(
                fields=["is_kahero_branch"],
                condition=models.Q(is_kahero_branch=True),
                name="unique_kahero_branch",
            ),
        ]

    def __str__(self):
        return self.name


class Role(TimestampedModel):
    """A named role governing permissions/access level (RBAC)."""

    OWNER_ADMIN = "OWNER_ADMIN"
    BRANCH_STAFF = "BRANCH_STAFF"
    COMMISSARY_STAFF = "COMMISSARY_STAFF"
    # Task #143 (client review, 2026-11-04): a new office role for managing
    # supplier relationships (apps.purchasing.Supplier). Not scoped to a
    # single branch -- same convention as OWNER_ADMIN (User.branch stays
    # null) -- since suppliers are a company-wide, commissary-side concern,
    # not a per-branch one. Does not require 2FA (see
    # apps.accounts.permissions._user_requires_otp, which checks only
    # OWNER_ADMIN by name, not by "is this an office role").
    PURCHASING = "PURCHASING"

    ROLE_CHOICES = [
        (OWNER_ADMIN, "Owner / Admin"),
        (BRANCH_STAFF, "Branch Staff"),
        (COMMISSARY_STAFF, "Commissary Staff"),
        (PURCHASING, "Purchasing"),
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

    # Client review (2026-11-10): two kinds of non-staff "cashier" accounts
    # exist purely as a required FK target for SalesTransaction.cashier,
    # neither of which a report should ever treat as an ordinary staff
    # member -- but they need OPPOSITE handling, so they're classified
    # separately rather than lumped into one "is_fake" flag:
    #
    # - SYNTHETIC_BACKFILL_BOT_USERNAME: the one dedicated identity
    #   scripts/backfill_thin_forecast_pairs.py attributes its
    #   forecast-coverage-only synthetic sales to (see that script's own
    #   module docstring). These are NOT real sales and must never count
    #   toward revenue, units sold, or any other report total, and this
    #   account must never appear as an ordinary, selectable cashier.
    #
    # - HISTORICAL_IMPORT_USERNAME_PREFIX: the one inactive placeholder
    #   account apps.core's import_branch_sales_history command creates
    #   per branch, purely as the required cashier FK target for real
    #   historical sales migrated from the client's own pre-system daily
    #   sales workbooks (see that command's own module docstring). The
    #   sales attributed to these accounts ARE real and belong in every
    #   revenue total -- only the raw, login-style username needs a
    #   friendlier display label (see display_name below).
    SYNTHETIC_BACKFILL_BOT_USERNAME = "synthetic_backfill_bot"
    HISTORICAL_IMPORT_USERNAME_PREFIX = "historical_import_"

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
    # Brute-force lockout for the password-based login doors (Admin,
    # Commissary) -- mirrors CashierPIN's own failed_attempts/locked_until
    # fields below, same threshold/duration policy, applied here because
    # neither of those doors had any attempt limit at all (unlike the
    # PIN-based Cashier flow, which already tracks this per-CashierPIN).
    failed_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    def is_locked(self) -> bool:
        return bool(self.locked_until and self.locked_until > timezone.now())

    @property
    def is_synthetic_backfill_account(self) -> bool:
        """True only for the single dedicated bot account that attributes
        forecast-coverage-only synthetic sales to itself -- never a real
        staff member. See the SYNTHETIC_BACKFILL_BOT_USERNAME comment
        above for what created it and why."""
        return self.username == self.SYNTHETIC_BACKFILL_BOT_USERNAME

    @property
    def is_historical_import_account(self) -> bool:
        """True for the inactive per-branch placeholder account that
        real, migrated historical sales are attributed to -- not a real,
        selectable staff cashier, but its attributed sales ARE real. See
        the HISTORICAL_IMPORT_USERNAME_PREFIX comment above."""
        return self.username.startswith(self.HISTORICAL_IMPORT_USERNAME_PREFIX)

    @property
    def display_name(self) -> str:
        """A report should never show a bare login-style username as if
        it were a cashier's name. Real users: unchanged (full name, or
        the username as a last resort). The historical-import placeholder:
        a plain "Imported" label -- whichever report is showing it
        already has a Branch column saying which branch. The synthetic
        bot: a short, clearly-marked label, kept here as a fallback for
        the rare case this account is ever shown directly rather than
        filtered out of a report entirely (see apps.analytics.services
        for where it's filtered out) -- never silently presented as an
        ordinary cashier's name."""
        if self.is_historical_import_account:
            return "Imported"
        if self.is_synthetic_backfill_account:
            return "Synthetic (forecast only)"
        return self.get_full_name() or self.username

    def __str__(self):
        return self.get_full_name() or self.username


class CashierPIN(TimestampedModel):
    """A cashier's PIN -- now the SOLE credential for the tap-to-select
    entry flow (Row 3 redesign), not a secondary factor on top of a
    password login as originally built. That change means a 4-digit PIN
    (10,000 possibilities) is genuinely brute-forceable if left
    unprotected, unlike its original role as a second factor on an
    already-password-authenticated session -- so this model now also
    tracks failed attempts and a temporary lockout.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cashier_pin",
    )
    hashed_pin = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    failed_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    def set_pin(self, raw_pin: str) -> None:
        """Hashes and stores a new PIN using Django's own password hasher,
        so PINs get the same PBKDF2 protection as account passwords."""
        self.hashed_pin = make_password(raw_pin)

    def check_pin(self, raw_pin: str) -> bool:
        return check_password(raw_pin, self.hashed_pin)

    def is_locked(self) -> bool:
        return bool(self.locked_until and self.locked_until > timezone.now())

    def __str__(self):
        return f"PIN for {self.user}"


class TwoFactorBackupCode(models.Model):
    """One single-use recovery code (Row 12.4), generated in a batch of
    10 the moment 2FA setup is confirmed. Shown to the Owner/Admin in
    plaintext exactly once at generation time -- only the hash is ever
    stored, same PBKDF2 protection as passwords and cashier PINs -- so an
    admin who loses their authenticator device isn't permanently locked
    out of their own account.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="backup_codes"
    )
    code_hash = models.CharField(max_length=128)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def set_code(self, raw_code: str) -> None:
        self.code_hash = make_password(raw_code)

    def check_code(self, raw_code: str) -> bool:
        return check_password(raw_code, self.code_hash)

    def __str__(self):
        status = "used" if self.used_at else "unused"
        return f"Backup code for {self.user} ({status})"
