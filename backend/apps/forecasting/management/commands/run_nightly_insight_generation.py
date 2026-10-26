"""
Standalone CLI entry point for the nightly AI insight generation job
(Row 11.2). See run_nightly_forecast.py's module docstring for why this
command exists -- same reasoning, same deployment story. Scheduled to
run after risk classification (see deployment notes for exact times) so
insights summarize fresh risk scores.
"""

from django.core.management.base import BaseCommand

from apps.forecasting.jobs import run_insight_generation_job


class Command(BaseCommand):
    help = "Runs the nightly AI insight generation job (see apps/forecasting/jobs.py)."

    def handle(self, *args, **options):
        insights = run_insight_generation_job()
        ai_generated_count = sum(1 for i in insights if i.generated_by_ai)
        self.stdout.write(
            self.style.SUCCESS(
                f"Insight generation job completed: {len(insights)} insights, "
                f"{ai_generated_count} from live AI call"
            )
        )
