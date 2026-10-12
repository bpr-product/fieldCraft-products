"""OpenAI Agents SDK backend for the navigation LLM call.

Selected via LLM_CLIENT_BACKEND=agents_sdk (see LLMClient.__init__ in
llm_client.py -- the raw OpenAI SDK path stays the default and the instant
fallback). Same SYSTEM_PROMPT, same prompt payload
(llm_client.build_user_message) as the other backends. The Agent is given
output_type=NavigationUnderstanding, so the SDK enforces that JSON Schema on
the final answer directly -- no free-text parsing (llm_client._extract_json)
needed for this backend's own output.

What's different from the "openai" and "langgraph" backends: this one gives
the model a real tool, lookup_project, backed by the REAL, tenant-scoped
project directory (EntityResolver against gold_eden.project_health -- the
same resolution path Insight/Reporting use, with regex/normalized/semantic
tiers). The deterministic layer's entity extraction only regex-guesses a
name out of the utterance and never checks it against anything real. Here,
when the model suspects a project is mentioned, it can call the tool and get
back a verified id/name/status instead of guessing -- the concrete
capability an agent framework adds over a single-shot classification call.

Tenant scoping: the tool NEVER receives a tenant from the model. The tenant
id is injected per-request via the Agents SDK run context (NavToolContext,
passed to Runner.run_sync(context=...) and read back through
RunContextWrapper) -- so a lookup can only ever see the caller's own
tenant's projects, and the model has no way to widen that scope. Without a
tenant in context the tool refuses rather than querying unscoped.
"""

import logging
import os
from dataclasses import dataclass

from agents import (
    Agent,
    ModelSettings,
    OpenAIChatCompletionsModel,
    RunContextWrapper,
    Runner,
    function_tool,
)
from dotenv import load_dotenv
from openai import AsyncOpenAI

from source.model.navigation_models import NavigationUnderstanding

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class NavToolContext:
    """Per-request dependency injected into the agent run (never sent to the
    model). Carries the verified tenant id so lookup_project can scope its
    query -- see the module docstring on why the model must not supply this."""
    tenant_id: str | None = None


# Lazily-constructed shared EntityResolver -- honors ENTITY_RESOLUTION_BACKEND
# the same as everywhere else. Lazy so importing this module (e.g. at app
# start) doesn't eagerly spin up embeddings/Qdrant just to define the tool.
_entity_resolver = None


def _get_entity_resolver():
    global _entity_resolver
    if _entity_resolver is None:
        from source.resolvers.entity_resolver import EntityResolver

        _entity_resolver = EntityResolver()
    return _entity_resolver


# Appended to SYSTEM_PROMPT to form this backend's actual Agent
# instructions (see AgentsSDKLLMClient.__init__ below). Exposed at module
# level so llm_client._fit_to_budget can account for its size when
# computing the real system-prompt token cost for this backend — budgeting
# against bare SYSTEM_PROMPT alone (this text is several hundred tokens on
# top of it) is what caused the 400 "context length exceeded" errors seen
# in the combined/agents_sdk backends even after _fit_to_budget shipped.
EXTRA_INSTRUCTIONS = (
    "\n\nIf the user's message names or implies a specific project "
    "(a proper noun, a client/site name, e.g. \"the Henderson job\"), "
    "call lookup_project with that name first to verify it exists and "
    "get its real name/status before answering. Use the tool's "
    "verified project name (not its id) as extracted_entity.\n\n"
    "Do NOT call lookup_project for a generic entity-type word that "
    "names a CATEGORY of thing, not a specific project -- \"customer\", "
    "\"customers\", \"vendor\", \"employee\", \"material\", \"invoice\", "
    "\"schedule\" etc. are never project names on their own, even when "
    "the message also says \"details\"/\"information\"/\"list\". Worked "
    "example -- utterance \"show me the customer details\" (no proper "
    "noun anywhere): do NOT call lookup_project at all -- pick a route "
    "from the registry (a customer-related page) directly, the same as "
    "any other navigation request with no entity to resolve.\n\n"
    "Call lookup_project AT MOST ONCE per user message. If it returns "
    "\"No project matching\", do NOT retry with a rephrased or "
    "shortened version of the name -- that name is not a real project. "
    "Immediately proceed to a final answer using route_id/confidence/"
    "insight as normal (extracted_entity null), the same as if the tool "
    "had never been called.\n\n"
    "For vague references with no actual name (\"it\", \"that\", "
    "\"this one\", \"the thing we talked about\") and bare "
    "confirmations (\"yes\", \"no\", \"sure\", \"ok\", \"yeah\"): "
    "FIRST check the \"Recent conversation\" section below. If the "
    "most recent turn resolved to a specific route or named project, "
    "that is the referent -- resolve to that same route_id (or call "
    "lookup_project with that project's name if the prior turn named "
    "one) rather than asking again. This is the ONLY case where you "
    "may resolve a pronoun without it being named in the current "
    "message. Worked example -- recent conversation shows turn 1: "
    'user said "take me to the Henderson project" -> navigate '
    "(Henderson Residence Remodel), current utterance \"take me to "
    'it\": {"intent": "navigate", "route_id": "project_information", '
    '"confidence": 0.85, "extracted_entity": "Henderson Residence '
    'Remodel", "insight": "Back to Henderson Residence Remodel\'s '
    'project information."}\n\n'
    "Only when there is NO usable prior turn (empty history, or the "
    "prior turn also had no route/project) do you fall back to "
    "asking: route_id and extracted_entity null, confidence 0.2 or "
    "less, intent \"out_of_scope\". NEVER invent a route or claim "
    "the user is already on a page with no evidence for it either "
    "way. Worked example -- utterance \"take me to it\" with NO prior "
    "conversation: {\"intent\": \"out_of_scope\", \"route_id\": null, "
    '"confidence": 0.2, "extracted_entity": null, "insight": "Which '
    'project did you mean? Tell me the name or the page and I\'ll '
    'take you there."}\n\n'
    "WARNING -- a common mistake: do NOT put the tool's project id "
    "(e.g. \"2079\") into route_id. route_id is always a route "
    "registry id (e.g. \"project_information\"), never a project id. "
    "Worked example -- tool returns "
    '"Henderson Residence Remodel (id=2079, status=in_progress)" '
    "and the matching registry route is "
    '{"id": "project_information", ...}. Correct output: '
    '{"intent": "navigate", "route_id": "project_information", '
    '"confidence": 0.9, "extracted_entity": "Henderson Residence Remodel", '
    '"insight": "..."}. route_id is "project_information" -- the page -- '
    'NOT "2079", which is the project itself.'
)

# Rough token cost of the lookup_project tool's JSON schema, which the SDK
# sends alongside the instructions on every call — not text either
# _fit_to_budget or the plain char-count estimate ever sees otherwise.
TOOL_SCHEMA_TOKEN_ESTIMATE = 80


def _lookup_project_impl(tenant_id: str | None, name: str) -> str:
    """Plain, directly-testable body of the lookup_project tool. Kept
    separate from the @function_tool wrapper so it can be unit-tested without
    constructing an SDK RunContextWrapper."""
    if not tenant_id:
        # No tenant in context -- refuse rather than querying unscoped. Never
        # widen the scope on the model's behalf (see module docstring).
        return "Project lookup is unavailable right now."
    try:
        # Tenant-scoped resolution against real data, with the same
        # regex/normalized/semantic tiers Insight/Reporting rely on.
        matches = _get_entity_resolver().resolve_project(name, tenant_id)
    except Exception as e:
        logger.error(f"lookup_project failed for name={name!r}: {e}")
        return f"Project lookup failed for '{name}'."
    if not matches:
        return f"No project matching '{name}' found for this tenant."
    if len(matches) > 1:
        listed = "; ".join(
            f"{m.project_name} (id={m.project_uid}, status={m.status})"
            for m in matches[:3]
        )
        return (
            f"Multiple projects match '{name}': {listed}. "
            "The reference is ambiguous -- do not pick one; ask the user which."
        )
    m = matches[0]
    return f"{m.project_name} (id={m.project_uid}, status={m.status})"


@function_tool
def lookup_project(ctx: RunContextWrapper[NavToolContext], name: str) -> str:
    """Look up a construction project by name or order number within the
    current tenant and return its real id, name, and status. Use the
    returned NAME (never the id) as extracted_entity."""
    tenant_id = ctx.context.tenant_id if ctx and ctx.context else None
    return _lookup_project_impl(tenant_id, name)


def _run_sync_isolated(agent, message, context=None):
    """Run the agent synchronously, passing `context` (a NavToolContext) as
    the run's dependency object -- the SDK makes it available to tools via
    RunContextWrapper without ever exposing it to the model.

    Historically this dispatched Runner.run_sync onto a dedicated worker
    thread, because Runner.run_sync spins up its own event loop internally
    and raises if called from a thread that already has one running -- which
    navigation_api.py's route handlers used to be (async def, executing on
    FastAPI's event-loop thread).

    That's no longer the case: navigate()/history() in navigation_api.py are
    now plain `def` handlers, so FastAPI runs them in its worker threadpool
    (via anyio.to_thread) -- plain threads with no event loop of their own.
    Runner.run_sync is safe to call directly there, so the extra
    ThreadPoolExecutor indirection this function used to add is gone. Kept
    as a thin wrapper (rather than calling Runner.run_sync inline at each
    call site) purely so CombinedLLMClient can keep importing one name.
    """
    # Legitimate runs never need more than ~2 turns (an optional single
    # lookup_project call, then the final structured answer) -- confirmed
    # live that a misbehaving run (the model retrying lookup_project with
    # rephrased names instead of giving up on a miss) burns the SDK's
    # default of 10 full turns before failing, twice (this call plus
    # understand_message's one retry) for every affected request. A lower
    # cap fails the same bad run in a fraction of the time/cost without
    # constraining any real one-lookup-then-answer flow.
    return Runner.run_sync(agent, message, context=context, max_turns=4)


class AgentsSDKLLMClient:
    def __init__(self):
        client = AsyncOpenAI(
            base_url=os.getenv("LLM_BASE_URL", "http://localhost:9000/v1"),
            api_key=os.getenv("LLM_API_KEY", "not-required"),
            timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
            max_retries=0,
        )
        model = OpenAIChatCompletionsModel(
            model=os.getenv("LLM_MODEL_NAME", "mistral:7b"),
            openai_client=client,
        )
        from source.utils.llm_client import SYSTEM_PROMPT

        instructions = SYSTEM_PROMPT + EXTRA_INSTRUCTIONS
        self._agent = Agent(
            name="EDEN Navigation",
            instructions=instructions,
            model=model,
            tools=[lookup_project],
            # The SDK enforces this schema on the final answer -- the same
            # structured-output guarantee llm_client._call_llm and the
            # LangGraph backend get via response_format /
            # with_structured_output. Replaces the old strict-retry-on-
            # invalid-JSON dance (a second "strict" Agent with STRICT_SUFFIX
            # appended) since there's no free-text JSON left to get wrong.
            output_type=NavigationUnderstanding,
            model_settings=ModelSettings(
                # 0.0 = greedy decoding -- classification must be repeatable;
                # sampling at 0.1 produced different routes for identical
                # utterances across runs (see llm_client._call_llm).
                temperature=0.0,
                max_tokens=200,
                tool_choice="auto",
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            ),
        )

    @property
    def agent(self) -> Agent:
        """Shared with CombinedLLMClient so the agent, tools, and output
        schema live in exactly one place."""
        return self._agent

    def understand_message(
        self, context_block: str, user_message: str, tenant_id: str | None = None
    ) -> dict | None:
        # One retry on a hard call failure (timeout, connection blip, the
        # SDK's own output-schema validation raising) -- not a "reformat
        # your output" plea, since output_type already constrains what a
        # successful run can return. `tenant_id` scopes the lookup_project
        # tool (injected via run context, never seen by the model).
        # `context_block`/`user_message` are the split output of
        # llm_client.build_user_message -- see its docstring for why they
        # must stay separate (context_block carries EDEN-owned data, often
        # a real project name, and must never be sent as user-role turn
        # content). The Agents SDK's Runner.run_sync accepts a list of
        # role-tagged input items, not just a bare string, so this is
        # expressed as a "system" item ahead of the "user" item -- same
        # split as the other backends, just via that input-list shape
        # instead of a second SystemMessage/chat-completions role.
        result = self._run_once(context_block, user_message, tenant_id)
        if result is None:
            result = self._run_once(context_block, user_message, tenant_id)
        return result

    def _run_once(
        self, context_block: str, user_message: str, tenant_id: str | None = None
    ) -> dict | None:
        try:
            result = _run_sync_isolated(
                self._agent,
                [
                    {"role": "system", "content": context_block},
                    {"role": "user", "content": user_message},
                ],
                context=NavToolContext(tenant_id=tenant_id),
            )
        except Exception as e:
            logger.error(f"Agents SDK run failed: {e}")
            return None

        output = result.final_output
        if isinstance(output, NavigationUnderstanding):
            return output.model_dump()
        # Defensive fallback: some runtimes may not honor output_type and
        # hand back raw text instead -- tolerant-parse it rather than
        # discarding a usable answer.
        from source.utils.llm_client import LLMClient

        raw = (output or "").strip() if isinstance(output, str) else ""
        parsed = LLMClient._extract_json(raw) if raw else None
        if parsed is None:
            logger.warning("Agents SDK backend: unexpected output: %.120r", output)
        return parsed
