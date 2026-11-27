"""
Measures wall-clock response time and SQL query count for the three
pages most directly implicated by Dr. Rowell M. Hernandez's capstone
panel comment (SanServeAll_Revision_Matrix.pdf, 2026-11-27): Reports
Hub (Sales & Trend), Dashboard Home, and Decision Support -- "evaluating
the accuracy, efficiency, and responsiveness of the generated graphs
and dashboards."

Run this TWICE and compare the two printed reports:
  1. BEFORE scripts/generate_stress_test_data.py, against whatever real
     data already exists locally -- the baseline.
  2. AFTER it, against the full synthetic load -- the stress-tested
     number to cite.

This does NOT modify anything. It builds requests directly against the
view functions with Django's RequestFactory (not a live HTTP server),
counting every query each view issues via
django.test.utils.CaptureQueriesContext and timing wall-clock
execution. Decorator requirements (role_required, otp_required) are
satisfied by attaching a throwaway in-memory OWNER_ADMIN user to the
request rather than going through a real login -- this script never
touches CashierPIN, session auth, or OTP state, and never creates a
database row for its "user" (unsaved, bypasses otp_required's real
verification path entirely via a plain attribute, documented inline
below).

Usage (from the backend/ directory, with the venv active):
    python manage.py shell --settings=config.settings.development \\
        -c "from scripts.benchmark_report_performance import run; run()"
"""

import os
import sys
import time

import django

if not django.apps.apps.ready:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    django.setup()

from django.contrib.messages.storage.fallback import FallbackStorage  # noqa: E402
from django.contrib.sessions.backends.db import SessionStore  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from django.test.utils import CaptureQueriesContext  # noqa: E402
from django.db import connection  # noqa: E402

from apps.accounts.models import Role, User  # noqa: E402
from apps.analytics.views import reports_hub  # noqa: E402
from apps.forecasting.views import decision_support  # noqa: E402
from apps.accounts.views import admin_dashboard  # noqa: E402


def _owner_admin_user():
    """A real, saved OWNER_ADMIN user -- role_required checks
    request.user.role against the DB-backed Role model, so this must be
    a genuine row, not an in-memory stand-in. Reused across runs rather
    than created fresh each time (get_or_create), and never attributed
    any data -- purely a request-identity fixture for this benchmark."""
    role, _ = Role.objects.get_or_create(name=Role.OWNER_ADMIN)
    user, _ = User.objects.get_or_create(
        username="benchmark_viewer",
        defaults={
            "first_name": "Benchmark Viewer (scripts/benchmark_report_performance.py "
            "request fixture -- not a real staff account)",
            "role": role,
            "branch": None,
            "is_active": True,
        },
    )
    if user.role_id != role.pk:
        user.role = role
        user.save(update_fields=["role"])
    return user


def _prepare_request(factory, path, user):
    request = factory.get(path)
    request.user = user
    # otp_required calls request.user.is_verified() -- normally added by
    # django_otp's OTPMiddleware after a real device verification. This
    # benchmark never exercises real OTP/session auth, so it attaches a
    # plain always-true callable directly -- the same shortcut this
    # project's own tests use for otp_required-gated views, scoped to
    # this throwaway request object only, never persisted anywhere.
    request.user.is_verified = lambda: True
    request.session = SessionStore()
    request.session.create()
    messages_storage = FallbackStorage(request)
    request._messages = messages_storage
    return request


def _time_view(label, view_fn, request):
    with CaptureQueriesContext(connection) as ctx:
        start = time.perf_counter()
        response = view_fn(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
    status = getattr(response, "status_code", "?")
    print(f"{label}:")
    print(f"  HTTP status: {status}")
    print(f"  Wall-clock time: {elapsed_ms:.1f} ms")
    print(f"  SQL queries issued: {len(ctx.captured_queries)}")
    return elapsed_ms, len(ctx.captured_queries)


def run():
    print("=" * 70)
    print("SanServeAll report/dashboard performance benchmark")
    print("=" * 70)

    user = _owner_admin_user()
    factory = RequestFactory()

    results = {}
    results["Reports Hub (Sales & Trend, This Year)"] = _time_view(
        "Reports Hub (Sales & Trend, This Year)",
        reports_hub,
        _prepare_request(factory, "/analytics/reports/?period=this_year", user),
    )
    results["Dashboard Home"] = _time_view(
        "Dashboard Home",
        admin_dashboard,
        _prepare_request(factory, "/accounts/admin/dashboard/", user),
    )
    results["Decision Support"] = _time_view(
        "Decision Support",
        decision_support,
        _prepare_request(factory, "/forecasting/decision-support/", user),
    )

    print("\n" + "=" * 70)
    print("Summary (copy this into your defense notes):")
    print("=" * 70)
    for label, (elapsed_ms, query_count) in results.items():
        print(f"  {label}: {elapsed_ms:.1f} ms, {query_count} queries")
    print(
        "\nRun this again after scripts/generate_stress_test_data.py (or after "
        "scripts/remove_stress_test_data.py to restore the baseline) and compare "
        "the two printouts."
    )


if __name__ == "__main__":
    run()
