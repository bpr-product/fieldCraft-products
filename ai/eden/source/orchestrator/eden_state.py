from typing import TypedDict

from source.model.navigation_models import NavigationResponse, SessionContext

# Agent-name -> graph-node-name. Identity map today (agent names and node
# names happen to match) — the seam Document search/Reporting register into
# once they exist.
AGENT_NODE_REGISTRY: dict[str, str] = {
    "navigation": "navigation",
    "insight": "insight",
    "reporting": "reporting",
    # Not a specialist -- eden_router's own low-confidence "ask instead of
    # guess" branch (mirrors Navigation's route-disambiguation pattern).
    # Registered here so it's a real, graph-visible destination like every
    # other routing outcome, not a special case bypassing AGENT_NODE_REGISTRY.
    "clarify": "clarify",
}


class EdenState(TypedDict, total=False):
    """Per-invocation state threaded through the outer LangGraph. Deliberately
    minimal — cross-turn continuity (pending goal, recent turns) stays in
    ConversationMemory (Redis/Mongo), not here. Extend this shape as new
    agents (Insight, Document search, Reporting) attach to the graph."""

    context: SessionContext
    next_agent: str | None
    response: NavigationResponse | None
