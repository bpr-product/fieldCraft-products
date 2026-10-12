"""Combined backend for the navigation LLM call -- LangGraph orchestrates,
OpenAI Agents SDK is the model+tools runtime inside the graph's node.

Selected via LLM_CLIENT_BACKEND=combined. Structure:

    START -> classify (runs the Agents SDK agent -- calls lookup_project as needed)
          -> END

This is the "LangGraph + Agents SDK" architecture: LangGraph is the
graph/orchestration layer and the node's model call is a real Agents SDK
Agent+Runner with a tenant-scoped tool available to it -- not a bare chat
completion. Reuses AgentsSDKLLMClient wholesale (agent, tool, output schema,
AND its own hard-failure retry) rather than reimplementing run+parse logic,
so there is exactly one place lookup_project, the agent's instructions, and
the output schema are defined.

Resiliency (retry on a transient call failure) lives in exactly ONE place --
AgentsSDKLLMClient.understand_message. An earlier version ALSO retried at
this graph level, which meant a single failure could trigger up to 4 agent
runs (2 graph attempts x 2 internal). That double-up is removed: this graph
is a straight-through single node today, and stays a seam for future
Finance/Schedule handoffs (added here as new nodes/edges) -- not a redundant
retry wrapper.
"""

import logging
from typing import TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

load_dotenv()

logger = logging.getLogger(__name__)


class _NavState(TypedDict):
    context_block: str
    user_message: str
    tenant_id: str | None
    result: dict | None


class CombinedLLMClient:
    def __init__(self):
        from source.utils.agents_sdk_llm_client import AgentsSDKLLMClient

        self._sdk_client = AgentsSDKLLMClient()

        graph = StateGraph(_NavState)
        graph.add_node("classify", self._classify)
        graph.add_edge(START, "classify")
        graph.add_edge("classify", END)
        self._graph = graph.compile()

    def understand_message(
        self, context_block: str, user_message: str, tenant_id: str | None = None
    ) -> dict | None:
        # `context_block`/`user_message` are the split output of
        # llm_client.build_user_message -- see its docstring for why they
        # must stay separate (context_block carries EDEN-owned data, often
        # a real project name, and must never be sent as user-role turn
        # content). Passed straight through to the wrapped Agents SDK
        # client, which is where they're actually placed on the model call.
        try:
            final = self._graph.invoke({
                "context_block": context_block,
                "user_message": user_message,
                "tenant_id": tenant_id,
                "result": None,
            })
            return final.get("result")
        except Exception as e:
            logger.error(f"Combined LangGraph+Agents SDK call failed: {e}")
            return None

    # -- graph nodes ---------------------------------------------------------

    def _classify(self, state: _NavState) -> dict:
        try:
            result = self._sdk_client.understand_message(
                state["context_block"], state["user_message"], state.get("tenant_id")
            )
        except Exception as e:
            logger.error(f"Combined backend classify node failed: {e}")
            result = None
        return {"result": result}
