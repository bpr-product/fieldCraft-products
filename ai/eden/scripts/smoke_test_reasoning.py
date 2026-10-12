"""
Live end-to-end smoke test: runs the REAL InsightAgent/ReportingAgent code
(not mocks) against the real gold_eden_synthesised database, to prove the
Day 1/2/3 implementation actually works over the wire, not just in unit tests.

Set MONGODB_URI to the synthesis cluster and MONGODB_DB=gold_eden_synthesised
before running. MONGODB_PERMISSIONS_DB defaults to gold_user_permissions --
point it at eden_synthesis (which holds user_permissions) if testing role
gating against that dataset instead of a mocked role lookup.

Usage:
    MONGODB_URI="mongodb://..." MONGODB_DB=gold_eden_synthesised \
    MONGODB_PERMISSIONS_DB=eden_synthesis \
    python3 scripts/smoke_test_reasoning.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from source.agents.insight_agent import InsightAgent
from source.agents.reporting_agent import ReportingAgent
from source.model.navigation_models import SessionContext

TENANT = "tn59395620"  # matches gold_eden_synthesised's synthetic projects

SCENARIOS = [
    ("9500001", "why did the estimate change", "Q1 high confidence"),
    ("9500002", "why did the estimate change", "Q1 no confidence (BOM doesn't reconcile)"),
    ("9500003", "why did the start date move", "Q2 low confidence (crew freed early)"),
    ("9500004", "why did the start date move", "Q2 no confidence"),
    ("9500005", "why did the estimate change", "Golden path Q1"),
    ("9500005", "why did the start date move", "Golden path Q2"),
]

MARGIN_USERS = ["syn-admin-user", "syn-superadmin-user", "syn-sales-user", "syn-customer-user"]


def ctx(utterance: str, user_id: str = "syn-admin-user") -> SessionContext:
    return SessionContext(
        utterance=utterance, currentRoute="/dashboard", currentEntity=None,
        timeOnPage=0, userId=user_id, tenantId=TENANT,
    )


def main():
    insight = InsightAgent()
    reporting = ReportingAgent()

    print("=" * 70)
    print("Q1 / Q2 -- via InsightAgent (internal, figures included)")
    print("=" * 70)
    for project_id, utterance, label in SCENARIOS:
        result = insight.variance_response(ctx(utterance), None, entity_id=project_id)
        print(f"\n[{label}] project={project_id}")
        print(f"  Q: {utterance}")
        print(f"  A: {result.message if result else '(None -- check project exists / field names)'}")

    print("\n" + "=" * 70)
    print("Q1 / Q2 -- via ReportingAgent (customer-safe, no dollar figures)")
    print("=" * 70)
    for project_id, utterance, label in SCENARIOS:
        result = reporting.respond(ctx(utterance), None, entity_id=project_id)
        print(f"\n[{label}] project={project_id}")
        print(f"  Q: {utterance}")
        print(f"  A: {result.message if result else '(None)'}")

    print("\n" + "=" * 70)
    print("Q3 -- margin RBAC gate, across roles (project 9500005)")
    print("=" * 70)
    for user_id in MARGIN_USERS:
        result = insight.variance_response(
            ctx("what is the profit margin", user_id=user_id), None, entity_id="9500005"
        )
        print(f"\n[{user_id}]")
        print(f"  A: {result.message if result else '(None)'}")


if __name__ == "__main__":
    main()
