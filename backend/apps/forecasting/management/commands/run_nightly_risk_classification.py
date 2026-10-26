"""
Standalone CLI entry point for the nightly inventory risk classification
job (Row 11.1). See run_nightly_forecast.py's module docstring for why
this command exists -- same reasoning, same deployment story.
"""

from django.core.management.base import BaseCommand

from apps.forecasting.jobs import run_risk_classification_job


class Command(BaseCommand):
    help = "Runs the nightly inventory risk classification job (see apps/forecasting/jobs.py)."

    def handle(self, *args, **options):
        scores = run_risk_classification_job()
        high_risk_count = sum(1 for s in scores if s.risk_level == "HIGH")
        self.stdout.write(
            self.style.SUCCESS(
                f"Risk classification job completed: {len(scores)} items scored, "
                f"{high_risk_count} high-risk"
            )
        )
