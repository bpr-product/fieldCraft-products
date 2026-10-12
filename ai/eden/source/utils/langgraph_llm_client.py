"""LangGraph backend for the navigation LLM call.

Selected via LLM_CLIENT_BACKEND=langgraph (see LLMClient.__init__ in
llm_client.py -- the raw OpenAI SDK path stays the default and the instant
fallback). Same SYSTEM_PROMPT, same prompt payload (built by
llm_client.build_user_message, shared by construction) as the other
backends -- just expressed as a small LangGraph state graph:

    START -> classify -> (retry once on hard failure) -> END

Structured outputs (with_structured_output(NavigationUnderstanding))
constrain generation to that JSON Schema at the token level, so the
classify->parse->strict-retry graph this backend used to need is gone: a
runtime that honors the schema cannot return a value that fails to parse,
so there's nothing left for a "parse" node to catch. The one retry that
remains is a plain resiliency net for a transient call failure (timeout,
connection blip), same as every other backend here -- not a "reformat your
output" plea.

One node is deliberately minimal: this call is a single-shot
classification, not an iterative tool-use loop, so no tools are registered
and there is no open-ended agent looping. The graph exists as the
extension seam for future agents (Finance/Schedule handoffs attach here as
new nodes/edges), not because today's call needs orchestration.
"""

import logging
import os
from typing import TypedDict

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from source.model.navigation_models import NavigationUnderstanding

load_dotenv()

logger = logging.getLogger(__name__)


class _NavState(TypedDict):
    context_block: str
    user_message: str
    attempts: int
    result: dict | None


class LangGraphLLMClient:
    def __init__(self):
        llm = ChatOpenAI(
            model=os.getenv("LLM_MODEL_NAME", "mistral:7b"),
            base_url=os.getenv("LLM_BASE_URL", "http://localhost:9000/v1"),
            api_key=os.getenv("LLM_API_KEY", "not-required"),
            # 0.0 = greedy decoding -- classification must be repeatable;
            # sampling at 0.1 produced different routes for identical
            # utterances across runs (see llm_client._call_llm).
            temperature=0.0,
            max_tokens=200,
            timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
            # One transparent retry is ours (the graph's retry edge below);
            # hidden SDK retries stack timeouts -- same reasoning as the raw
            # SDK path in llm_client.py.
            max_retries=0,
            # MUST match llm_client._call_llm exactly -- especially
            # enable_thinking: SmolLM3-3B's default reasoning mode burns the
            # whole max_tokens budget on an internal <think> trace before any
            # JSON appears (verified against vLLM: "hello" costs 249 prompt
            # tokens with it on, 69 with it off). If this pass-through ever
            # silently stops working, that bug returns -- verify with real
            # usage-token numbers after any langchain-openai upgrade.
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        # method="json_schema": the runtime enforces NavigationUnderstanding's
        # schema on generation (guided decoding), rather than langchain
        # simulating structure via a tool call ("function_calling") -- the
        # closer match to what response_format={"type": "json_object"} did
        # before, just schema-constrained instead of merely "valid JSON".
        self._structured_llm = llm.with_structured_output(
            NavigationUnderstanding, method="json_schema"
        )

        graph = StateGraph(_NavState)
        graph.add_node("classify", self._classify)
        graph.add_edge(START, "classify")
        graph.add_conditional_edges(
            "classify", self._should_retry, {"retry": "classify", "done": END}
        )
        self._graph = graph.compile()

    def understand_message(
        self, context_block: str, user_message: str, tenant_id: str | None = None
    ) -> dict | None:
        """Run the prompt through the graph. Same return contract as
        LLMClient.understand: parsed dict, or None on hard failure so the
        orchestrator falls back deterministically. `tenant_id` is accepted
        for signature parity with the combined/agents_sdk backends but
        ignored here -- this backend registers no tools, so there is nothing
        to tenant-scope. `context_block`/`user_message` are the split output
        of llm_client.build_user_message -- see its docstring for why they
        must stay separate (context_block carries EDEN-owned data, often a
        real project name, and must be a system message, never user-role)."""
        try:
            final = self._graph.invoke({
                "context_block": context_block,
                "user_message": user_message,
                "attempts": 0,
                "result": None,
            })
            return final.get("result")
        except Exception as e:
            logger.error(f"LangGraph LLM call failed: {e}")
            return None

    # -- graph nodes ---------------------------------------------------------

    def _classify(self, state: _NavState) -> dict:
        from source.utils.llm_client import SYSTEM_PROMPT

        try:
            parsed = self._structured_llm.invoke([
                SystemMessage(content=SYSTEM_PROMPT),
                SystemMessage(content=state["context_block"]),
                HumanMessage(content=state["user_message"]),
            ])
            result = parsed.model_dump() if parsed is not None else None
        except Exception as e:
            logger.error(f"LangGraph classify node failed: {e}")
            result = None
        return {"result": result, "attempts": state["attempts"] + 1}

    def _should_retry(self, state: _NavState) -> str:
        # One retry on a hard call failure (timeout, connection blip) -- not
        # a reformat attempt, since a schema-constrained runtime can't return
        # something that fails to parse in the first place.
        if state.get("result") is None and state["attempts"] < 2:
            return "retry"
        return "done"
