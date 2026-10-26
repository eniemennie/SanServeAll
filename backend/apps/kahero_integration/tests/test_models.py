"""
Tests for KaheroImportBatch's own model-level properties (quality_rate,
has_warnings) -- the silent-skip UI warning fix (Development Status
Assessment, KaHero item).
"""

import pytest

from apps.accounts.models import Branch, Role, User
from apps.kahero_integration.models import KaheroImportBatch

pytestmark = pytest.mark.django_db


@pytest.fixture
def branch():
    return Branch.objects.create(name="Alangilan", code="ALANGILAN", is_kahero_branch=True)


@pytest.fixture
def owner():
    role = Role.objects.create(name=Role.OWNER_ADMIN)
    return User.objects.create_user(username="owner1", password="testpass123", role=role)


def _make_batch(branch, owner, *, status, total_rows, success_count, error_count):
    return KaheroImportBatch.objects.create(
        branch=branch,
        uploaded_by=owner,
        original_filename="export.csv",
        status=status,
        total_rows=total_rows,
        success_count=success_count,
        error_count=error_count,
    )


def test_has_warnings_false_when_all_rows_succeed(branch, owner):
    batch = _make_batch(
        branch, owner, status=KaheroImportBatch.Status.COMPLETED,
        total_rows=2, success_count=2, error_count=0,
    )
    assert batch.has_warnings is False


def test_has_warnings_true_when_completed_with_skipped_rows(branch, owner):
    batch = _make_batch(
        branch, owner, status=KaheroImportBatch.Status.COMPLETED,
        total_rows=2, success_count=1, error_count=1,
    )
    assert batch.has_warnings is True


def test_has_warnings_false_when_batch_failed_outright(branch, owner):
    # A FAILED batch already shows a red "Failed" badge -- has_warnings
    # is specifically for a batch that LOOKS successful but isn't fully.
    batch = _make_batch(
        branch, owner, status=KaheroImportBatch.Status.FAILED,
        total_rows=1, success_count=0, error_count=1,
    )
    assert batch.has_warnings is False
