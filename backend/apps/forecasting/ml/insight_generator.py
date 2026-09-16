"""
Natural-language insight generation (Row 11.2): converts numeric
forecast/risk output into readable recommendations, per Table 3-2's
FR-04. Uses the raw HTTP API via `requests` rather than the anthropic/
openai SDK packages -- one fewer dependency, and this only needs a single
simple request/response, not the SDK's full feature surface.

Falls back to a plain, clearly-labeled template message when no API key
is configured or the API call fails -- the Decision Support System must
degrade gracefully rather than break the whole insight-generation step
just because a third-party API is unavailable (Phase 3 "Responsible Use
of AI": DSS output supports a human decision, it doesn't have to always
come from a live model call to be useful).
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-sonnet-4-6"
REQUEST_TIMEOUT_SECONDS = 15


def _build_prompt(insight_type, context):
    if insight_type == "STOCKOUT_WARNING":
        return (
            "You are a business analyst summarizing inventory risk for a small "
            "cafe's owner. Write ONE short, plain-language sentence (no bullet "
            "points, no markdown) warning about the following at-risk items. "
            "Be direct and actionable, not alarmist.\n\n"
            f"Branch: {context['branch_name']}\n"
            f"At-risk items: {context['at_risk_items']}"
        )
    if insight_type == "DEMAND_SUMMARY":
        return (
            "You are a business analyst summarizing a 7-day demand forecast "
            "for a small cafe's owner. Write ONE short, plain-language sentence "
            "(no bullet points, no markdown) highlighting the key trend.\n\n"
            f"Branch: {context['branch_name']}\n"
            f"Forecast summary: {context['forecast_summary']}"
        )
    raise ValueError(f"Unknown insight_type: {insight_type}")


def _call_claude_api(prompt):
    api_key = getattr(settings, "CLAUDE_API_KEY", None)
    if not api_key:
        return None

    try:
        response = requests.post(
            CLAUDE_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 150,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        return data["content"][0]["text"].strip()
    except Exception:
        logger.exception("Claude API call failed; falling back to template message.")
        return None


def _template_fallback(insight_type, context):
    """A plain, rule-based sentence used when the AI API isn't available.
    Deliberately un-fancy -- it exists so the feature still produces
    SOMETHING useful, not to imitate what the AI would have said."""
    if insight_type == "STOCKOUT_WARNING":
        return (
            f"{context['branch_name']}: the following items are at risk of "
            f"running out soon -- {context['at_risk_items']}. Consider "
            "restocking soon."
        )
    if insight_type == "DEMAND_SUMMARY":
        return f"{context['branch_name']} 7-day forecast: {context['forecast_summary']}."
    raise ValueError(f"Unknown insight_type: {insight_type}")


def generate_insight(insight_type, context, force_template=False):
    """Returns (message: str, generated_by_ai: bool). Never raises --
    any failure in the AI call path falls back to the template, since a
    broken third-party API should never take down the whole insight
    generation step.

    `force_template=True` (set via SystemConfiguration.ai_insights_enabled,
    Row 12.2) skips the API call entirely regardless of whether a key is
    configured -- a genuine kill switch for the AI provider, not just a
    "try it and fall back" path."""
    prompt = _build_prompt(insight_type, context)
    ai_message = None if force_template else _call_claude_api(prompt)

    if ai_message:
        return ai_message, True
    return _template_fallback(insight_type, context), False
