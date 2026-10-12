"""Claude-backed NL interpreter: free-form prompt -> Gold dashboard intent.

Turns an arbitrary business prompt into one of the known, pre-built Gold
dashboard intents (plus an optional tenant id), using the Anthropic Python SDK
(Claude Opus 4.8) with structured outputs.

Safety boundary: the model ONLY classifies which pre-built, parameterised Gold
query to run and extracts a tenant id — it NEVER generates Mongo queries or
free-form data access. Every actual read still goes through the deterministic,
tenant-scoped builders in dashboard_app.py. This keeps the NL surface flexible
while data access stays auditable and injection-safe.

Credentials: resolved from the environment by the SDK (ANTHROPIC_API_KEY). Never
hardcode a key (FIELDCRAFT InfoSec / SOC 2).

Degrades cleanly: raises RuntimeError if the SDK isn't installed or no key is
configured, so the caller falls back to the keyword router.
"""
from __future__ import annotations

import json
import os

# The intents the dashboard can serve, mirrored from dashboard_app.INTENTS.
# Kept as a plain list so the schema enum and the prompt catalog stay in sync.
INTENT_KEYS = [
    "executive", "ar", "workforce", "project_health", "procurement",
    "tasks", "bid", "gap_kpi", "gap_aging", "gap_trend", "gap_customer", "none",
]

_CATALOG = """\
You route a user's business prompt to ONE Gold-layer dashboard intent for a
construction/field-service SaaS. The Gold layer (Cosmos DB) has these collections;
pick the intent whose data actually answers the prompt:

SERVED today:
- executive        -> tenant_dashboard: cross-project counts, at-risk count, total outstanding
- ar               -> invoice_summary: billed / received / outstanding, overdue invoices (finance)
- workforce        -> workforce_status: headcount, W2/1099 mix, hours logged, utilization
- project_health   -> project_health: completion %, cost breakdown, schedule risk, slip days
- procurement      -> procurement_status: BOM vs PO gap, unordered value, pending approvals
- tasks            -> task_intelligence: overdue / unassigned tasks
- bid              -> bid_context: Q&A / questionnaire completeness per project

NOT served (pick the matching gap intent so the dashboard honestly reports the gap):
- gap_kpi      -> KPI scorecard: gold_kpi collection is UNBUILT
- gap_aging    -> AR aging buckets (current/30/60/90+): gold_invoice_aging is UNBUILT
- gap_trend    -> any trend / over-time / period-over-period: Gold stores last-state only, NO history
- gap_customer -> customer-centric views: no customer entity exists in the source

- none -> the prompt doesn't map to any of the above.

Also extract a tenant id if the user names one (e.g. "for tenant tn78010693"); else null.
Prefer a SERVED intent when the prompt is answerable; only choose a gap intent when the
request is specifically about the unavailable capability.
"""

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "intent": {"type": "string", "enum": INTENT_KEYS},
        "tenant": {"type": ["string", "null"]},
        "rationale": {"type": "string"},
    },
    "required": ["intent", "tenant", "rationale"],
}

MODEL = os.environ.get("DASH_NL_MODEL", "claude-opus-4-8")


def available() -> bool:
    """True if the SDK is importable and an API key is configured."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def interpret(prompt: str) -> dict:
    """Classify a free-form prompt into {intent, tenant, rationale}.

    Raises RuntimeError if the SDK/key is unavailable; the caller falls back to
    the keyword router on any exception.
    """
    if not available():
        raise RuntimeError("Anthropic SDK or API key not available")
    client = _get_client()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=512,
        system=_CATALOG,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("model refused to classify prompt")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    data = json.loads(text)
    if data.get("intent") not in INTENT_KEYS:
        data["intent"] = "none"
    return data
