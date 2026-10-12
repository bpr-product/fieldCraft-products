import json
import logging
import os
import re
import time

from openai import OpenAI
from dotenv import load_dotenv

from source.model.navigation_models import (
    AgentTriage,
    AmbiguityVerification,
    BillingIntentCheck,
    DateIntentCheck,
    GoalContinuationDecision,
    NavigationUnderstanding,
    ProjectContextSwitchDecision,
    ProjectContextSwitchReply,
    ProjectNameExtraction,
    RelatedRoutesSuggestion,
)
from source.prompts import (
    BILLING_COMPOSE_SYSTEM_PROMPT,
    BILLING_INTENT_SYSTEM_PROMPT,
    DATE_INTENT_SYSTEM_PROMPT,
    EXTRACT_PROJECT_SYSTEM_PROMPT,
    GOAL_CONTINUATION_SYSTEM_PROMPT,
    PROJECT_DATA_ANSWER_SYSTEM_PROMPT,
    PROJECT_CONTEXT_SWITCH_REPLY_SYSTEM_PROMPT,
    PROJECT_CONTEXT_SWITCH_SYSTEM_PROMPT,
    RELATED_ROUTES_SYSTEM_PROMPT,
    STRICT_SUFFIX,
    SYSTEM_PROMPT,
    TRIAGE_SYSTEM_PROMPT,
    VARIANCE_COMPOSE_SYSTEM_PROMPT,
    VERIFY_AMBIGUITY_SYSTEM_PROMPT,
    VOICE_SYSTEM_PROMPT,
)

load_dotenv()

# Real bug, fixed (PR review, 2026-08-12): every call site below this point
# had max_tokens=2048 -- the model's ENTIRE real context window (see
# MODEL_CONTEXT_WINDOW_TOKENS's docstring), which on a model with only a
# 2048-token window leaves ZERO room for any prompt at all and reproduces
# the live 400 ("maximum context length is 2048 tokens") this file's own
# budget logic exists to prevent. Originally bumped to 2048 (from an
# original small budget of 60-200) on 2026-07-21 to accommodate NVIDIA's
# Nemotron Nano -- a mandatory-reasoning model that spent 1000+ tokens on
# invisible reasoning before any real content, returning empty/malformed
# output at the original small budgets. That reasoning-mode risk is now
# handled the correct way instead -- every call site disables thinking
# mode via extra_body={"chat_template_kwargs": {"enable_thinking": False}}
# (two call sites, decide_project_context_switch and
# resolve_project_context_switch_reply, were missing this and had it added
# alongside this fix) -- so max_tokens is back down to 300, generous
# headroom for the small classification JSON / short prose these calls
# actually produce, not a defense against invisible reasoning tokens.

logger = logging.getLogger(__name__)

# Context budget: SmolLM3-3B's real limit, not a guess. Observed live,
# TWICE, as a hard 400 from the model server: "maximum context length is
# 2048 tokens... prompt contains at least 1849 input tokens". Both times
# were caused by a FIXED candidate-count cap in agent_orchestrator.py that
# was calibrated against the prompt's size at the time and then went stale
# — once when SYSTEM_PROMPT grew (the page-scoped-ambiguity guidance added
# real bulk: SYSTEM_PROMPT alone is now ~1090 estimated tokens, well over
# half the whole budget on its own), and once when the active retrieval
# backend changed (semantic vs tfidf surfaces different, sometimes longer,
# candidate descriptions). A fixed cap can't self-correct for either kind
# of drift. _fit_to_budget below measures the ACTUAL constructed prompt
# and trims adaptively instead, so it stays correct as SYSTEM_PROMPT or the
# retrieval backend keep changing.
# Bumped from 2048 (the old SmolLM3-3B limit) to Qwen3-8B's real 32768-token
# window after the model swap -- this constant had gone stale, silently
# trimming candidates/history down to the old, smaller floor on every
# request even though the live model could hold far more context.
# Overridable via env var so the next model swap doesn't require a code
# change to avoid the same drift.
MODEL_CONTEXT_WINDOW_TOKENS = int(os.getenv("MODEL_CONTEXT_WINDOW_TOKENS", "32768"))
# The model's rated window is NOT the real ceiling -- every EDEN call to the
# self-hosted model passes through the guardrails gateway first, which
# enforces its own, independently-configured Layer-1 input cap
# (LLM_MAX_INPUT_TOKENS; 1024 from-template, 6000 in dev) and
# rejects anything over it before the request ever reaches the model --
# no error surfaces here, it just comes back as an empty/blocked
# completion (see guardrails' app/main.py chat_completions). Found live:
# budgeting only against MODEL_CONTEXT_WINDOW_TOKENS (32768) let
# _fit_to_budget build prompts far larger than guardrails would actually
# accept, so every multi-candidate understand() call silently failed at
# the gateway, 100% of the time, with no vLLM call ever logged. Mirror
# guardrails' own default here so the two stay in sync without a code
# change; set this env var to match if guardrails' cap is ever retuned.
# Default is deliberately CONSERVATIVE (matches guardrails/.env.template's
# 1024, the value a from-template deploy actually gets), because the two
# directions of drift are not symmetric: EDEN guessing LOWER than the
# gateway only wastes some context, while EDEN guessing HIGHER makes it
# build prompts the gateway silently drops -- the exact failure this
# constant was introduced to fix. Environments that raised the gateway cap
# raise this in their own .env to match (dev: 6000).
GUARDRAILS_MAX_INPUT_TOKENS = int(os.getenv("GUARDRAILS_MAX_INPUT_TOKENS", "1024"))
# Must be >= the largest max_tokens any budgeted call actually requests
# (see _call_llm/_call_llm_legacy below, 300) -- this is a single constant
# shared across every backend's budget calc, not per-call, so it has to
# cover the biggest real request, not the smallest.
RESERVED_OUTPUT_TOKENS = 300
# Chars-per-token for English/JSON text is commonly ~4; used only to
# ESTIMATE fit before sending (no real tokenizer available here) —
# deliberately biased low so we trim a bit more than strictly necessary
# rather than risk hitting the model server's hard 400 again.
CHARS_PER_TOKEN_ESTIMATE = 3.5
MIN_CANDIDATES_FLOOR = 2


def _estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN_ESTIMATE)

# Matches dollar amounts ($64,600), percentages (31%), and plain numbers (12,
# 21) -- the exact tokens a rewrite could silently drop or alter while
# rephrasing a sentence built from real Mongo data (committedSpend,
# scheduleRisk deltas, slipDays, etc.).
_NUMBER_TOKEN_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")

# Presidio's redaction placeholders -- when LLM_BASE_URL points at
# guardrails-gateway instead of raw vLLM, the gateway's PII input rail can
# redact a real project/customer name in the draft (e.g. "Ethan Miller" ->
# "<PERSON>") before the model ever sees it. Public (no leading underscore):
# reused by agent_orchestrator.py, which hits the exact same leak via a
# different LLM call (decide_project_context_switch) that doesn't go
# through rephrase() at all -- see its own leak guard.
#
# This alternation is a hand-duplicated MIRROR of guardrails'
# DEFAULT_PRESIDIO_ENTITIES (src/guardrails/app/config.py) -- EDEN can't
# import that module (separate deployable service, separate Docker build
# context). If PRESIDIO_ENTITIES is ever widened there (e.g. ORGANIZATION,
# NRP) for a legitimate reason, this regex silently stops catching the new
# placeholder type, and extract_project_name would return a corrupted
# <ORGANIZATION>-style string as a real project name again. Drift between
# the two is checked by src/eden/scripts/check_presidio_entities_sync.py, run
# from a full repo checkout (not either service's own container) -- update BOTH
# together, and re-run that script, whenever either changes.
PRESIDIO_PLACEHOLDER_RE = re.compile(
    r"<(PERSON|EMAIL_ADDRESS|PHONE_NUMBER|US_SSN|CREDIT_CARD|IP_ADDRESS|LOCATION)>"
)

# rephrase()'s causal-fabrication guard -- see that function's comment.
# Confirmed live: a plain status draft with no stated cause came back from
# the rewrite with "...was updated due to changes in scope and resource
# allocation," a specific claim invented wholesale. Deliberately a small,
# closed connector vocabulary ("due to", "because of", "caused by", "as a
# result of", "attributed to", "the reason is/was") -- not a general
# hedge-word scan, so an unrelated sentence sharing a word doesn't trip it.
_CAUSAL_PHRASE_RE = re.compile(
    r"\b(due to|because of|caused by|as a result of|owing to|attributed to|"
    r"the reason (?:is|was))\b",
    re.IGNORECASE,
)
# Broader companion, checked against the DRAFT ONLY: a real cause-bearing
# draft from build_estimate_change_message/build_schedule_change_message
# never uses the connector phrases above -- it says "It appears related
# to:", "Reason on file:", "recorded as", "I can confirm this wasn't...
# the movement is in", "the change is a price update on". A rewrite may
# naturally reword any of those as "because of X" -- a genuine, fact-
# preserving paraphrase, not an invention -- so checking the draft against
# the NARROW connector set alone would reject good rewrites of exactly the
# Q1/Q2 answers this session added. Only a draft matching NEITHER pattern
# has truly asserted no cause at all.
_DRAFT_STATES_CAUSE_RE = re.compile(
    _CAUSAL_PHRASE_RE.pattern + r"|reason on file|appears related to|recorded as|"
    r"confirmed this wasn|movement is in|price update on",
    re.IGNORECASE,
)

# rephrase()'s access/permission-denial-fabrication guard. Confirmed live: a
# draft that stated no access/permission problem at all (a plain "I don't
# have that specific detail available for this project" data-availability
# message) came back from the rewrite as "I don't have access to view other
# users' projects" -- an invented RBAC-denial reason with no basis in the
# draft. This is the SAME "instruction alone isn't verification" gap the
# causal-claim guard above closes, just for a different fabrication shape:
# it wasn't caught by the causal-claim guard (no due-to/because-of connector
# word), the number guard (no figures involved), or the PII-leak guard (no
# placeholder) -- and it only happened to trip the new-question guard
# because this particular fabrication also ended in a question. A rewrite
# that fabricated the identical access-denial claim as a flat statement
# (no trailing "?") would have shipped to the user undetected. Legitimate
# access-denial drafts DO exist (navigation_agent.py's "You don't have
# access to {route}", reporting_agent.py/insight_agent.py's "isn't
# available at your access level") and use this exact vocabulary, so the
# same regex checked against the draft distinguishes a real denial being
# paraphrased from one invented out of nothing.
_ACCESS_DENIAL_PHRASE_RE = re.compile(
    r"\b(?:don'?t|do not|doesn'?t|does not|can'?t|cannot|not) (?:have |get )?"
    r"(?:access|permission)\b|\baccess level\b|\bnot authorized\b",
    re.IGNORECASE,
)

# Page-name preservation guard: matches _attach_page_suggestion's own
# deterministic closing sentence (agent_orchestrator.py), built from a page
# ALREADY permission-checked for this user (see _enforce_role_scope). The
# "invented question" guard in rephrase() only catches a rewrite that adds a
# question where the draft had none -- it doesn't catch one that keeps the
# question shape but SWAPS which page it names for a different (possibly
# unpermitted, possibly nonexistent) one. Confirmed live: draft offered one
# page, rewrite named "Job Ledger" instead -- the exact failure the voice
# prompt's own worked example already warns against (prompts/voice.py),
# proof the prompt alone doesn't reliably prevent it.
_SUGGESTED_PAGE_RE = re.compile(r"[Ww]ould you like to see the (.+?) page\?")


def _number_tokens(text: str) -> set[str]:
    # rstrip trailing ",." -- the regex's thousands-separator group greedily
    # swallows a sentence-punctuation comma/period right after a number
    # ("$64,600, compared to..."), which would otherwise make the same
    # number compare as different tokens depending on what follows it.
    #
    # lstrip leading "$" too -- real bug, fixed: compose_billing_message/
    # compose_variance_message build their `required` set from
    # f"{v:,.0f}" (never a "$" prefix), but this function previously kept
    # a leading "$" whenever the generated text had one (nearly always,
    # for money). "$14,800" != "14,800" as plain strings, so the subset
    # check failed on almost every correctly-composed sentence -- observed
    # live: a perfectly accurate composed sentence ("...is $14,800 with
    # outstanding invoices totaling $14,800.") logged as "dropped/altered
    # a real figure" and silently discarded in favor of the deterministic
    # template, every time, for every non-zero figure. Stripping "$" from
    # BOTH sides (required is already "$"-free) makes the comparison
    # currency-symbol-agnostic instead of currency-symbol-sensitive.
    # rephrase()'s draft-vs-text comparison is unaffected by this change
    # (both sides run through this same function, so they were already
    # comparing consistently either way).
    #
    # Real bug, fixed: the "$" fix above left the EXACT same hole open for
    # "%", which _NUMBER_TOKEN_RE deliberately captures. Source data carries a
    # bare number (project_health.completionPct = 16.67) while any natural
    # sentence about it writes "16.67%", so "16.67%" != "16.67" and the
    # fact-check rejected its own correct answer. Confirmed live: a fully
    # accurate reporting answer ("...completion percentage of 16.67%, with 9
    # tasks completed out of 54 total tasks" -- every figure matching
    # completionPct=16.67 / doneTaskCount=9 / totalTaskCount=54) was discarded
    # as "contains figures not present in source data", with
    # answer_numbers={'16.67%'} the sole offender. completionPct is the
    # most-quoted figure in Reporting, so this silently threw away most good
    # percentage answers in favour of the deterministic template. Strip "%"
    # from the right the same way "$" is stripped from the left --
    # unit-symbol-agnostic, not unit-symbol-sensitive. Order matters: "%"
    # comes before the ",." strip so "16.67%," (percentage at a clause end)
    # loses both.
    return {
        match.lstrip("$").rstrip(",.").rstrip("%").rstrip(",.")
        for match in _NUMBER_TOKEN_RE.findall(text)
    }


def build_verify_ambiguity_message(
    utterance: str, chosen: dict, candidates: list[dict]
) -> str:
    """Prompt payload for the multi-step ambiguity-verification call. Module-level
    like build_user_message, for the same reason: one place defines the exact
    payload shape."""
    lines = [
        f'{i}. id={c["id"]} label="{c["label"]}" description="{c.get("description", "")}" '
        f'retrieval_score={c["score"]:.3f}'
        for i, c in enumerate(candidates, 1)
    ]
    return f"""Chosen by the first classifier: {chosen.get("route_id")} (confidence {chosen.get("confidence")})

Candidate pages (from retrieval, closest match first):
{chr(10).join(lines)}

User's message: "{utterance}"

Decide and return JSON only."""


def build_user_message(
    utterance: str,
    route_registry: list[dict],
    session_context: dict,
    conversation_history: list[dict] | None = None,
    pending_goal: dict | None = None,
) -> tuple[str, str]:
    """Build the exact prompt payload for the navigation-understanding call.

    Module-level (not a method) so every LLM backend — the raw OpenAI SDK
    path, the LangGraph adapter, and the Agents SDK/combined backends —
    sends the identical payload by construction, not by each backend
    re-implementing the format and drifting apart. The token-budget
    constraints documented on LLMClient.understand apply to whatever is
    passed in here.

    Returns `(context_block, user_content)`, NOT a single string — real bug,
    fixed: this used to return one combined string sent as a single
    user-role message. `context_block` (route registry, session context,
    conversation history, pending-goal state) is EDEN-owned data that
    frequently contains a real project name (`session_context["currentEntity"]`,
    conversation-history `[project: ...]` brackets — see navigation_agent.py),
    and a FIELDCRAFT project name is frequently a person name (e.g. "Dan Cabush,
    Racine-Roof Enhancement-646248"). Guardrails' Presidio input rail
    redacts user-role content only, never system-role (confirmed in
    guardrails/app/pii.py) — sending this as user-role silently corrupted it
    to `<PERSON>` before the model ever saw it, whenever LLM_BASE_URL points
    at the guardrails gateway. Same bug class already fixed for six other
    call sites in this file (see PRESIDIO_PLACEHOLDER_RE's callers); this
    was the seventh, and the highest-traffic one — every call site must now
    send `context_block` as a SEPARATE system-role message and keep only
    `user_content` (the literal new utterance) in the user role.
    """
    slim_registry = [
        {
            "id": r["id"],
            "label": r["label_template"],
            "description": r.get("description", ""),
        }
        for r in route_registry
    ]
    registry_str = json.dumps(slim_registry)
    context_str = json.dumps({
        "currentRoute": session_context.get("currentRoute"),
        "currentEntity": session_context.get("currentEntity"),
        "timeOnPage": session_context.get("timeOnPage"),
        # Last few pages visited — cheap extra signal for "go back" /
        # "the page I was on before" phrasings. Capped to keep the token
        # budget flat (see the context-window notes on understand()).
        "recentRoutes": (session_context.get("routeHistory") or [])[-3:],
        # Per explicit user direction (2026-09-03): the model previously had
        # no way to know it was talking to mobile at all -- see
        # SYSTEM_PROMPT's own "Mobile platform" paragraph for what this
        # changes. Omitted (None) for web, same as every other unset field
        # here, so an old prompt/eval fixture with no "platform" key still
        # reads the same as web always has.
        "platform": session_context.get("platform"),
    })

    history_section = ""
    if conversation_history:
        lines = []
        for i, turn in enumerate(conversation_history, 1):
            outcome = turn["type"]
            if turn.get("label"):
                outcome += f" ({turn['label']})"
            # Real bug, fixed: `label` is the PAGE (e.g. a status/date/
            # billing answer's attached generic "see the Projects page"
            # suggestion) -- a different concept from the PROJECT actually
            # being discussed (e.g. "Peter Parker"), which used to have no
            # representation here at all. Surfaced as its own bracket so a
            # pronoun follow-up ("show me", "for this") a few turns later
            # can still be resolved to the right project even when the
            # in-between turns' `label` only ever names a suggested page.
            if turn.get("project"):
                outcome += f" [project: {turn['project']}]"
            lines.append(f'{i}. user said "{turn["utterance"]}" -> {outcome}')
        history_section = "Recent conversation (most recent last):\n" + "\n".join(lines) + "\n\n"

    # Conversation-loop state: when the previous turn ended with EDEN asking
    # a question instead of navigating, the current message is (usually) the
    # user's ANSWER to that question — the model must resolve the ORIGINAL
    # request using the new detail, not treat a fragment like "the dashboard
    # one" as a standalone request. The escape hatch matters equally: a user
    # can abandon a clarification mid-flow ("actually take me to invoices"),
    # and that must win over the stale goal.
    goal_section = ""
    if pending_goal and pending_goal.get("original_utterance"):
        asked = pending_goal.get("last_question") or ""
        # Numbered, in the order shown to the user — without this, an
        # ordinal answer ("the second one") is unresolvable and the model
        # guesses (observed live: it picked option 1).
        offered_line = ""
        offered = pending_goal.get("offered") or []
        if offered:
            numbered = ", ".join(f"{i}. {label}" for i, label in enumerate(offered, 1))
            offered_line = (
                f"EDEN offered these options, in this order: {numbered}. "
                'Answers like "the first one" / "the second one" refer to this list.\n'
            )
        goal_section = (
            "Clarification in progress:\n"
            f'The user originally asked: "{pending_goal["original_utterance"]}"\n'
            f'EDEN replied: "{asked}"\n'
            f"{offered_line}"
            "Treat the current message as their answer, and resolve the ORIGINAL "
            "request using this new detail. Only if the current message is clearly "
            "a brand-new, self-contained request should you ignore the original "
            "one and handle the new request instead.\n\n"
        )

    context_block = f"""Route registry:
{registry_str}

{history_section}{goal_section}Session context:
{context_str}"""
    user_content = f'User utterance: "{utterance}"\n\nUnderstand the message and return JSON only.'
    return context_block, user_content


def warm_up() -> None:
    """Load the model into memory so the first real user message is fast.

    Local runtimes (Ollama/vLLM) unload idle models; a cold first call can
    take 15s+. Called from a background thread at app startup — never raises.
    Module-level with its own bare client on purpose: constructing a full
    LLMClient here would also build whatever LLM_CLIENT_BACKEND is configured
    (LangGraph graph, Agents SDK agent) just to send a 1-token ping.
    """
    try:
        client = OpenAI(
            base_url=os.getenv("LLM_BASE_URL", "http://localhost:9000/v1"),
            api_key=os.getenv("LLM_API_KEY", "not-required"),
            timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
            max_retries=0,
        )
        client.chat.completions.create(
            model=os.getenv("LLM_MODEL_NAME", "mistral:7b"),
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
        logger.info("LLM warm-up complete — model is resident")
    except Exception as e:
        logger.warning(f"LLM warm-up failed (will retry lazily on first use): {e}")


class LLMClient:
    def __init__(self):
        self.client = OpenAI(
            base_url=os.getenv("LLM_BASE_URL", "http://localhost:9000/v1"),
            api_key=os.getenv("LLM_API_KEY", "not-required"),
            timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
            # One transparent retry is ours (strict re-prompt below); hidden
            # SDK retries stack timeouts into a 90s worst case mid-demo.
            max_retries=0,
        )
        self.model = os.getenv("LLM_MODEL_NAME", "mistral:7b")
        # Voice-pass circuit breaker: after a rephrase failure, skip further
        # rephrase attempts for this long. The voice pass is cosmetic — when
        # the model is down it must not keep adding a full LLM timeout to
        # every request on top of whatever the routing path already paid.
        self._voice_down_until = 0.0

        # Backend switch: "openai" (default, the raw-SDK path below) or
        # "langgraph" (the graph adapter in langgraph_llm_client.py). Flip the
        # env var and restart to switch — the instant fallback if the
        # LangGraph path misbehaves, no code revert needed. Import is lazy so
        # the default path never even loads langchain/langgraph, and an
        # import/init failure degrades to the raw SDK instead of taking the
        # service down.
        self.backend = os.getenv("LLM_CLIENT_BACKEND", "openai").strip().lower()
        self._langgraph = None
        self._agents_sdk = None
        self._combined = None
        if self.backend == "combined":
            try:
                from source.utils.combined_llm_client import CombinedLLMClient
                self._combined = CombinedLLMClient()
                logger.info("LLM backend: combined (langgraph + agents_sdk)")
            except Exception as e:
                logger.error(
                    f"Combined backend init failed — falling back to openai: {e}"
                )
                self.backend = "openai"
        elif self.backend == "langgraph":
            try:
                from source.utils.langgraph_llm_client import LangGraphLLMClient
                self._langgraph = LangGraphLLMClient()
                logger.info("LLM backend: langgraph")
            except Exception as e:
                logger.error(
                    f"LangGraph backend init failed — falling back to openai: {e}"
                )
                self.backend = "openai"
        elif self.backend == "agents_sdk":
            try:
                from source.utils.agents_sdk_llm_client import AgentsSDKLLMClient
                self._agents_sdk = AgentsSDKLLMClient()
                logger.info("LLM backend: agents_sdk")
            except Exception as e:
                logger.error(
                    f"Agents SDK backend init failed — falling back to openai: {e}"
                )
                self.backend = "openai"

    def understand(
        self,
        utterance: str,
        route_registry: list[dict],
        session_context: dict,
        conversation_history: list[dict] | None = None,
        pending_goal: dict | None = None,
    ) -> dict | None:
        """One LLM call that triages intent, picks a route, extracts the entity,
        and writes a one-sentence insight. Returns None on hard failure so the
        caller can fall back deterministically.

        `route_registry` is expected to already be pre-filtered to a small
        candidate set (see AgentOrchestrator._dispatch_with_llm's vector
        pre-filter, char n-gram TF-IDF) — sending the full 50+ route registry
        here blew SmolLM3-3B's 2048-token context window outright (measured:
        ~1849 input tokens before generation even starts), and an earlier
        id+label-only workaround fit but lost enough signal to misclassify
        things like "take me to payroll" as a real route instead of
        correctly falling back. Full descriptions for a handful of
        pre-filtered candidates fixes both problems at once. Keyword lists
        are omitted — the deterministic keyword path already ran before the
        LLM is consulted.

        `conversation_history`: last few turns from ConversationMemory (see its
        docstring — this is the only place EDEN's otherwise-stateless design
        gets any multi-turn context, and only for the ~10% of requests that
        reach the LLM at all). Kept to a few short lines given the context
        budget; omitted entirely from the prompt when empty rather than
        sending an empty section.
        """
        route_registry, conversation_history = self._fit_to_budget(
            utterance, route_registry, session_context, conversation_history, pending_goal,
        )
        context_block, user_message = build_user_message(
            utterance, route_registry, session_context, conversation_history,
            pending_goal=pending_goal,
        )

        # Tenant id for the agent backends' tenant-scoped lookup_project tool.
        # Pulled from session_context (SessionContext.model_dump carries it)
        # and injected into the agent run context -- NEVER placed in the
        # prompt/model-visible text (build_user_message deliberately omits
        # it), so the model can't widen or spoof the scope.
        tenant_id = session_context.get("tenantId")

        if self._combined is not None:
            return self._combined.understand_message(context_block, user_message, tenant_id)
        if self._langgraph is not None:
            return self._langgraph.understand_message(context_block, user_message, tenant_id)
        if self._agents_sdk is not None:
            return self._agents_sdk.understand_message(context_block, user_message, tenant_id)

        result = self._call_llm(context_block, user_message)
        if result is None:
            # One retry only — this is a plain resiliency net for a transient
            # failure (timeout, connection blip), not a "reformat your
            # output" plea. Structured outputs (response_format=
            # NavigationUnderstanding below) constrain generation at the
            # token level, so the old failure mode this retry used to chase
            # — the model returning prose instead of JSON — can't happen on
            # a runtime that honors the schema.
            result = self._call_llm(context_block, user_message)
        return result

    def _fit_to_budget(
        self,
        utterance: str,
        route_registry: list[dict],
        session_context: dict,
        conversation_history: list[dict] | None,
        pending_goal: dict | None,
    ) -> tuple[list[dict], list[dict] | None]:
        """Adaptively trim candidates/history until the ACTUAL constructed
        prompt (system + user message, measured for real via
        build_user_message — not guessed) fits under whichever is smaller:
        the model's own context window, or the guardrails gateway's
        independently-configured input cap (see GUARDRAILS_MAX_INPUT_TOKENS's
        docstring — budgeting against the model alone let prompts pass this
        check while still getting rejected at the gateway). Order of what
        gets cut, cheapest-to-lose
        first: conversation_history (the goal section, if any, already
        carries the thread context, so raw turn text is the most
        dispensable), then candidates one at a time down to a floor of 2
        (fewer than that defeats the point of giving the model real
        choices). Always returns something — even over budget at the
        floor, that's still the best-effort prompt, and the LLM call's own
        error handling covers an actual 400 from here.
        """
        budget = min(MODEL_CONTEXT_WINDOW_TOKENS, GUARDRAILS_MAX_INPUT_TOKENS) - RESERVED_OUTPUT_TOKENS
        system_tokens = _estimate_tokens(SYSTEM_PROMPT)
        # combined/agents_sdk send a bigger instructions string than plain
        # SYSTEM_PROMPT (tool-use + pronoun-resolution rules, see
        # agents_sdk_llm_client.EXTRA_INSTRUCTIONS) plus the lookup_project
        # tool's JSON schema on every call. Budgeting against bare
        # SYSTEM_PROMPT alone undercounts this backend's real prompt size —
        # exactly what caused the 400 context-length errors to keep
        # recurring even after this method shipped.
        if self.backend in ("combined", "agents_sdk"):
            from source.utils.agents_sdk_llm_client import (
                EXTRA_INSTRUCTIONS,
                TOOL_SCHEMA_TOKEN_ESTIMATE,
            )
            system_tokens += _estimate_tokens(EXTRA_INSTRUCTIONS) + TOOL_SCHEMA_TOKEN_ESTIMATE

        def total_tokens(candidates: list[dict], history: list[dict] | None) -> int:
            context_block, user_content = build_user_message(
                utterance, candidates, session_context, history, pending_goal=pending_goal
            )
            return system_tokens + _estimate_tokens(context_block) + _estimate_tokens(user_content)

        total = total_tokens(route_registry, conversation_history)
        if total <= budget:
            return route_registry, conversation_history

        if conversation_history:
            total = total_tokens(route_registry, None)
            if total <= budget:
                logger.warning(
                    "Context budget: dropped conversation history to fit (~%d tokens over budget without it)",
                    max(0, total - budget),
                )
                return route_registry, None

        candidates = list(route_registry)
        while len(candidates) > MIN_CANDIDATES_FLOOR:
            candidates = candidates[:-1]
            total = total_tokens(candidates, None)
            if total <= budget:
                logger.warning(
                    "Context budget: trimmed candidates to %d (no history) to fit (~%d tokens)",
                    len(candidates), total,
                )
                return candidates, None

        logger.warning(
            "Context budget: still ~%d tokens with only %d candidates and no history — "
            "sending anyway, this is the smallest prompt we can build",
            total, len(candidates),
        )
        return candidates, None

    def verify_ambiguity(
        self, utterance: str, chosen: dict, candidates: list[dict]
    ) -> dict | None:
        """Multi-step reasoning trial's second LLM pass — see
        VERIFY_AMBIGUITY_SYSTEM_PROMPT and agent_orchestrator._maybe_verify_ambiguity
        for when this is called and why. Always uses the raw SDK path
        regardless of LLM_CLIENT_BACKEND, same reasoning as rephrase(): this
        is one cheap, single-shot structured call, not a multi-turn agent
        run, so it doesn't need whichever graph/tools backend is configured
        for the primary classification. Returns None on any failure so the
        caller keeps the primary classifier's original answer — this check
        can only add a second opinion, never block on one.
        """
        user_message = build_verify_ambiguity_message(utterance, chosen, candidates)
        try:
            response = self.client.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": VERIFY_AMBIGUITY_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.0,
                max_tokens=300,
                response_format=AmbiguityVerification,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            message = response.choices[0].message
            if message.refusal or message.parsed is None:
                return None
            return message.parsed.model_dump()
        except Exception as e:
            logger.warning(
                f"Ambiguity verification call failed — keeping primary classification: {e}"
            )
            return None

    def triage(self, utterance: str, page_context: str | None = None) -> dict | None:
        """Top-level agent routing (eden_router): which specialist —
        navigation or insight — should handle this request. Runs on EVERY
        request by explicit design decision; the old VARIANCE_SIGNALS
        keyword check was hardcoded routing wearing an AI label and
        survives only as the caller's degraded fallback when this call
        fails. Always uses the raw SDK path regardless of
        LLM_CLIENT_BACKEND, same reasoning as verify_ambiguity/rephrase:
        one cheap, single-shot structured call needs no graph or tools.
        Returns None on any failure so the caller falls back to keyword
        routing rather than erroring the request.

        Real bug, fixed: this used to classify the bare utterance with no
        idea which page the user was looking at, so a message that only
        makes sense against the page in front of them got read as a
        project question -- confirmed live: "who all are active" on the
        customer list (whose own page_features include an "active
        customers" status filter) triaged to reporting at 0.9 as "a
        specific project status", which then had no project, asked for
        one, and fell through to an irrelevant answer. `page_context` (an
        already-formatted TRIAGE_PAGE_CONTEXT_TEMPLATE block, built by the
        caller) is appended as a SECOND SYSTEM message, never as user
        content: the guardrails PII rail redacts user-role text only, and
        a page label containing a person-shaped word would return as
        "<PERSON>", corrupting the signal. None (unresolvable page) sends
        the byte-identical prompt this method used before."""
        messages: list[dict] = [{"role": "system", "content": TRIAGE_SYSTEM_PROMPT}]
        if page_context:
            messages.append({"role": "system", "content": page_context})
        messages.append({"role": "user", "content": utterance})
        try:
            response = self.client.chat.completions.parse(
                model=self.model,
                messages=messages,
                temperature=0.0,
                max_tokens=300,
                response_format=AgentTriage,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            message = response.choices[0].message
            if message.refusal or message.parsed is None:
                return None
            return message.parsed.model_dump()
        except Exception as e:
            logger.warning(f"Triage call failed — falling back to keyword routing: {e}")
            return None

    def decide_project_context_switch(
        self, utterance: str, last_project: str, page_project: str
    ) -> dict | None:
        """End-to-end LLM judgment (per explicit user direction, replacing
        a flat deterministic string-compare) for whether a page-vs-last-
        discussed-project mismatch is worth interrupting the user about.
        See ProjectContextSwitchDecision's docstring for the contract.
        Returns None on call failure — caller degrades to NOT asking
        (silently uses the page project), the same fail-open default as
        every other best-effort LLM call in this client."""
        try:
            response = self.client.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": PROJECT_CONTEXT_SWITCH_SYSTEM_PROMPT},
                    # Project names go in the SYSTEM role for the same reason as
                    # rephrase() above: the gateway redacts user-role content
                    # only, and these names come from EDEN's own Mongo reads,
                    # not from the user. Observed live -- both names arriving as
                    # <PERSON> made the model echo the placeholder into the
                    # question it generated ("Are you still asking about 'vijay
                    # cus, <LOCATION>-Roof Enhancement-383724'..."), which the
                    # caller's own leak guard then had to discard in favour of
                    # the deterministic template.
                    {"role": "system", "content": (
                        f'Last discussed project: "{last_project}"\n'
                        f'Project on the current page: "{page_project}"'
                    )},
                    {"role": "user", "content": f'User\'s new message: "{utterance}"'},
                ],
                temperature=0.0,
                max_tokens=300,
                response_format=ProjectContextSwitchDecision,
                # Real gap, fixed (PR review): every sibling classification
                # call in this file disables thinking mode -- this one and
                # resolve_project_context_switch_reply below were missed.
                # Without it, a reasoning-mode model can burn the entire
                # max_tokens budget on an invisible reasoning trace before
                # any real content, returning empty/malformed output --
                # confirmed live elsewhere in this file, see the module
                # header comment. Only safe to lower max_tokens to 300 (from
                # 2048) with this also in place.
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            parsed = response.choices[0].message.parsed
            return parsed.model_dump() if parsed else None
        except Exception as e:
            logger.warning(f"Project-context-switch decision call failed: {e}")
            return None

    def resolve_project_context_switch_reply(
        self, reply: str, question: str, last_project: str, page_project: str
    ) -> dict | None:
        """Interprets the user's reply to a project-context-switch
        confirmation -- "last", "page", or "other" (see
        ProjectContextSwitchReply's docstring). Returns None on call
        failure -- caller degrades to treating the reply as "other"
        (resolve it as a fresh question), the safest default since it
        never silently commits to the wrong project."""
        try:
            response = self.client.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": PROJECT_CONTEXT_SWITCH_REPLY_SYSTEM_PROMPT},
                    # Same reason as decide_project_context_switch above (its
                    # sibling): question/last_project/page_project are all
                    # EDEN-owned (the question EDEN itself generated, project
                    # names from EDEN's own Mongo reads), not user input -- the
                    # gateway redacts user-role content only. This exact call
                    # was missed when that fix was made elsewhere; only `reply`
                    # (genuinely the user's own message) belongs in user role.
                    {"role": "system", "content": (
                        f'You asked: "{question}"\n'
                        f'Option A (last discussed): "{last_project}"\n'
                        f'Option B (current page): "{page_project}"'
                    )},
                    {"role": "user", "content": f'User\'s reply: "{reply}"'},
                ],
                temperature=0.0,
                max_tokens=300,
                response_format=ProjectContextSwitchReply,
                # Same gap and fix as decide_project_context_switch above.
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            parsed = response.choices[0].message.parsed
            return parsed.model_dump() if parsed else None
        except Exception as e:
            logger.warning(f"Project-context-switch reply-resolution call failed: {e}")
            return None

    def extract_project_name(self, utterance: str) -> dict | None:
        """Which project, if any, does this message name? Primary mechanism
        for Insight's entity resolution — reads the sentence instead of
        subtracting stop-words from it (see EXTRACT_PROJECT_SYSTEM_PROMPT
        for the failure mode that killed the subtraction approach). Same
        cheap raw-SDK single-shot pattern as triage/verify_ambiguity.

        Return contract distinguishes two very different "nothings":
        - {"project_name": None} — the call SUCCEEDED and the message names
          no project (caller should fall back to page context, then list
          projects).
        - None — the call itself FAILED (model unreachable); caller degrades
          to the old stop-word extraction."""
        try:
            response = self.client.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": EXTRACT_PROJECT_SYSTEM_PROMPT},
                    {"role": "user", "content": utterance},
                ],
                temperature=0.0,
                max_tokens=300,
                response_format=ProjectNameExtraction,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            message = response.choices[0].message
            if message.refusal or message.parsed is None:
                return None
            parsed = message.parsed.model_dump()
            # Real bug, fixed: confirmed live -- "take me to al naber
            # project" extracted project_name="<PERSON>" (the utterance
            # itself is USER-role content, and the gateway's Presidio rail
            # redacts real customer/project names there, unlike the 5
            # EDEN-owned-data call sites already fixed for this). Unlike
            # those, the utterance here can't just move to a system message
            # -- it's genuinely the user's own input, not EDEN-owned data,
            # and the whole point of this call is to read it. Treating a
            # placeholder extraction as a call FAILURE (not a successful
            # "<PERSON>" name) makes the caller fall back to
            # _extract_entity_name's deterministic stop-word stripper
            # instead, which reads the real, unredacted utterance text
            # directly and correctly finds "al naber" -- rather than
            # passing "I couldn't find a project matching '<PERSON>'"
            # straight through to the user.
            project_name = parsed.get("project_name")
            if project_name and PRESIDIO_PLACEHOLDER_RE.search(project_name):
                logger.warning(
                    f"Project-name extraction returned a PII placeholder "
                    f"({project_name!r}) — falling back to stop-word extraction"
                )
                return None
            return parsed
        except Exception as e:
            logger.warning(
                f"Project-name extraction call failed — falling back to stop-word extraction: {e}"
            )
            return None

    def suggest_related_routes(
        self, utterance: str, candidates: list[dict]
    ) -> dict | None:
        """Which of these candidate pages plausibly answers this data
        question? Primary mechanism for NavigationAgent.data_question_response's
        "here's where you'd look" bubble suggestions — replaces the
        DATA_TOPIC_ROUTES keyword table (see RELATED_ROUTES_SYSTEM_PROMPT for
        why). `candidates` is the same [{"id", "label", "description", ...}]
        shape used for the main understand() call (see NavigationAgent.
        top_candidates).

        Return contract mirrors extract_project_name: {"route_ids": []} is a
        SUCCESSFUL call that found nothing relevant (caller should not add
        any keyword-fallback ids on top of a real "nothing fits" answer);
        None means the call itself FAILED and the caller degrades to
        DATA_TOPIC_ROUTES."""
        if not candidates:
            return {"route_ids": []}
        try:
            catalog = "\n".join(
                f"- id={c.get('id')} label={c.get('label')!r} "
                f"description={c.get('description')!r}"
                for c in candidates
            )
            response = self.client.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": RELATED_ROUTES_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Question: {utterance}\n\nCandidates:\n{catalog}"},
                ],
                temperature=0.0,
                max_tokens=300,
                response_format=RelatedRoutesSuggestion,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            message = response.choices[0].message
            if message.refusal or message.parsed is None:
                return None
            candidate_ids = {c.get("id") for c in candidates}
            route_ids = [rid for rid in message.parsed.route_ids if rid in candidate_ids][:3]
            return {"route_ids": route_ids}
        except Exception as e:
            logger.warning(
                f"Related-routes suggestion call failed — falling back to DATA_TOPIC_ROUTES: {e}"
            )
            return None

    def classify_billing_intent(self, utterance: str) -> dict | None:
        """Instrumentation-only check for ReportingAgent's BILLING_SIGNALS
        keyword list (see BILLING_INTENT_SYSTEM_PROMPT) -- called on a
        sampled fraction of requests, purely to log disagreement with the
        keyword decision. Never used to route or answer. Returns None on
        any failure; the caller simply skips logging for that sample."""
        try:
            response = self.client.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": BILLING_INTENT_SYSTEM_PROMPT},
                    {"role": "user", "content": utterance},
                ],
                temperature=0.0,
                max_tokens=300,
                response_format=BillingIntentCheck,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            message = response.choices[0].message
            if message.refusal or message.parsed is None:
                return None
            return message.parsed.model_dump()
        except Exception as e:
            logger.warning(f"Billing-intent audit call failed — skipping this sample: {e}")
            return None

    def classify_date_intent(self, utterance: str) -> dict | None:
        """PRIMARY decision for ReportingAgent's date-vs-status fork (see
        DATE_INTENT_SYSTEM_PROMPT / DateIntentCheck) -- unlike
        classify_billing_intent, this is load-bearing, not audit-only: per
        explicit user direction, a keyword list alone can't reliably
        generalize to phrasing it wasn't written for. Returns None on any
        failure; caller degrades to the DATE_SIGNALS keyword list."""
        try:
            response = self.client.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": DATE_INTENT_SYSTEM_PROMPT},
                    {"role": "user", "content": utterance},
                ],
                temperature=0.0,
                max_tokens=300,
                response_format=DateIntentCheck,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            message = response.choices[0].message
            if message.refusal or message.parsed is None:
                return None
            return message.parsed.model_dump()
        except Exception as e:
            logger.warning(f"Date-intent classification call failed — falling back to keyword check: {e}")
            return None

    def classify_goal_continuation(
        self, utterance: str, question: str, original_utterance: str,
        offered: list[str] | None = None,
    ) -> dict | None:
        """PRIMARY decision for eden_router's "is this message a reply to the
        clarification EDEN just asked, or a new request?" fork (see
        GOAL_CONTINUATION_SYSTEM_PROMPT / GoalContinuationDecision).
        Load-bearing: before this existed, ANY open goal unconditionally
        captured the next few messages, so an unrelated question got
        answered as if it were the reply. Returns None on any failure;
        caller degrades to a deterministic shape heuristic (see
        AgentOrchestrator._looks_like_goal_reply)."""
        offered_block = ""
        if offered:
            offered_block = "\nOptions you offered: " + "; ".join(offered)
        try:
            response = self.client.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": GOAL_CONTINUATION_SYSTEM_PROMPT},
                    {"role": "user", "content": (
                        f'Their original request: "{original_utterance}"\n'
                        f'You then asked: "{question}"{offered_block}\n'
                        f'Their new message: "{utterance}"'
                    )},
                ],
                temperature=0.0,
                max_tokens=300,
                response_format=GoalContinuationDecision,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            message = response.choices[0].message
            if message.refusal or message.parsed is None:
                return None
            return message.parsed.model_dump()
        except Exception as e:
            logger.warning(
                f"Goal-continuation classification call failed — "
                f"falling back to shape heuristic: {e}"
            )
            return None

    def answer_from_project_data(self, utterance: str, data: dict) -> str | None:
        """Real RAG/agentic replacement for ReportingAgent's per-question
        hardcoded branches -- see PROJECT_DATA_ANSWER_SYSTEM_PROMPT's
        docstring for the full reasoning. `data` is the FULL
        permission-scoped project_health/invoice_summary facts (already
        stripped of any field this caller's role must never see -- this
        method has no opinion on that, it only answers from whatever it's
        given). Returns free text, not structured output: the answer shape
        varies by question, unlike every other LLMClient call here.

        Does NOT verify the answer against `data` -- that is the caller's
        responsibility (ReportingAgent.answer_from_data), same separation
        as rephrase()/compose_billing_message() never verifying themselves.

        Return contract distinguishes two very different "nothings", same
        shape as extract_project_name's: `None` means the call itself
        failed or produced something unusable (exception, malformed/JSON-
        shaped output, a leaked PII placeholder) -- caller falls back to
        the old deterministic branches. `""` (empty string) means the call
        SUCCEEDED but the model had nothing to say -- caller should treat
        this as an honest "I don't have that," not defer to the fallback
        chain, which would answer a DIFFERENT question instead.

        Real bug, fixed: `data` used to ride in the USER message -- the
        exact same mistake already fixed in rephrase()/
        decide_project_context_switch()/compose_billing_message()/
        compose_variance_message() (see rephrase()'s comment for the full
        history), just missed here as a fifth call site. The gateway
        redacts user-role content only; a real project name IS a person
        name ("Al Naber, Burlington-Roof Enhancement-543655"), so Presidio
        replaced it with <PERSON> and -- unlike rephrase(), which has
        PRESIDIO_PLACEHOLDER_RE as a leak guard -- nothing here checked for
        one, so the literal placeholder shipped straight to the user
        (confirmed live: 'The project ... is titled "<PERSON>, Burlington-
        Roof Enhancement-543655"...'). `data` is EDEN's own Mongo read, not
        user input, so it belongs in the trusted system role; only the
        user's own utterance stays user-role, where redaction is correct."""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": PROJECT_DATA_ANSWER_SYSTEM_PROMPT},
                    {"role": "system", "content": f"Data: {json.dumps(data, default=str)}"},
                    {"role": "user", "content": f"Question: {utterance}"},
                ],
                temperature=0.0,
                max_tokens=300,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            text = (response.choices[0].message.content or "").strip().strip('"').strip()
            # Real bug, fixed: confirmed live -- "what is the material used
            # for this project" (materialCost correctly excluded from data)
            # got the model to produce a genuinely EMPTY response instead of
            # the honest "I don't have that" sentence the prompt explicitly
            # asks for. That empty text used to collapse into the exact same
            # `return None` as a true call failure, so the caller
            # (ReportingAgent.answer_from_data) couldn't tell "the model
            # tried and had nothing to say" apart from "the call itself
            # failed" -- both fell through the ENTIRE deterministic chain
            # (billing/date/status_response) to an unrelated status blurb
            # that reads as though the material question was answered.
            # A malformed, JSON-shaped response is a different failure mode
            # (a real parsing/formatting failure, not "nothing to say") and
            # still returns None so the caller defers to the old chain.
            if text.startswith("{"):
                return None
            if not text:
                return ""
            # PII-redaction leak guard, same pattern as rephrase()'s: no
            # "draft" to diff against here (this generates from scratch,
            # it doesn't rewrite an existing sentence), so ANY placeholder
            # in the output is illegitimate -- the customer-safe data this
            # answers from should never legitimately contain the literal
            # string "<PERSON>" etc.
            if PRESIDIO_PLACEHOLDER_RE.search(text):
                logger.warning(
                    "Project-data answer leaked a PII-redaction placeholder "
                    "-- discarding: %.160r", text,
                )
                return None
            return text
        except Exception as e:
            logger.warning(f"Project-data answer call failed: {e}")
            return None

    def compose_billing_message(
        self, project_name: str, total_billed: float, total_received: float,
        total_outstanding: float,
    ) -> str | None:
        """Per explicit user direction: the LLM composes
        ReportingAgent.billing_response's message end to end (including how
        to phrase a genuinely zero figure), replacing the deterministic
        if/elif template that used to be the only option (that template
        still exists as the fallback here). Verified against the real
        figures afterward — every NON-ZERO amount must survive verbatim;
        zero amounts are deliberately exempt from that check, since the
        whole point is letting the model describe them in words instead of
        "$0". Returns None on any failure or a verification mismatch —
        caller keeps its own deterministic template in that case, same
        fail-safe pattern as rephrase()."""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": BILLING_COMPOSE_SYSTEM_PROMPT},
                    # SYSTEM role, not user: the gateway redacts user-role
                    # content only, and `project_name` comes from EDEN's own
                    # Mongo read -- real FIELDCRAFT project names ARE person names
                    # ("Dan Cabush, Racine-Roof Enhancement-646248"), so as
                    # user content Presidio would hand the model <PERSON> and
                    # the composed sentence would name <PERSON> back at the
                    # customer. Unlike rephrase()/the context-switch question
                    # there is NO placeholder guard on this path -- the
                    # fact-check below only verifies the FIGURES survive, so a
                    # redacted name would ship straight to the user. Same fix
                    # as rephrase(); see its comment for the full history.
                    {"role": "system", "content": (
                        f"Project: {project_name}\n"
                        f"Billed: ${total_billed:,.0f}\n"
                        f"Received: ${total_received:,.0f}\n"
                        f"Outstanding: ${total_outstanding:,.0f}"
                    )},
                    {"role": "user", "content": "Compose the reply."},
                ],
                temperature=0.3,
                max_tokens=300,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            text = (response.choices[0].message.content or "").strip().strip('"').strip()
            if not text or len(text) > 400 or text.startswith("{"):
                return None
            required = {f"{v:,.0f}" for v in (total_billed, total_received, total_outstanding) if v}
            if not required.issubset(_number_tokens(text)):
                logger.warning(
                    "Billing message composition dropped/altered a real figure "
                    "— keeping template: %r", text,
                )
                return None
            return text
        except Exception as e:
            logger.warning(f"Billing message composition call failed: {e}")
            return None

    def compose_variance_message(
        self, project_name: str, committed_spend: float, outstanding_invoices: float,
    ) -> str | None:
        """Same pattern as compose_billing_message, for
        InsightAgent.variance_response's committed-spend /
        outstanding-invoices figures. The schedule-risk sentence is
        appended separately by the caller (deterministic, unaffected) —
        this call composes only the money sentence."""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": VARIANCE_COMPOSE_SYSTEM_PROMPT},
                    # SYSTEM role for the same reason as
                    # compose_billing_message above -- EDEN-side Mongo data, no
                    # placeholder guard on this path, and a redacted project
                    # name would be composed into the answer verbatim.
                    {"role": "system", "content": (
                        f"Project: {project_name}\n"
                        f"Committed spend: ${committed_spend:,.0f}\n"
                        f"Outstanding invoices: ${outstanding_invoices:,.0f}"
                    )},
                    {"role": "user", "content": "Compose the reply."},
                ],
                temperature=0.3,
                max_tokens=300,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            text = (response.choices[0].message.content or "").strip().strip('"').strip()
            if not text or len(text) > 300 or text.startswith("{"):
                return None
            required = {f"{v:,.0f}" for v in (committed_spend, outstanding_invoices) if v}
            if not required.issubset(_number_tokens(text)):
                logger.warning(
                    "Variance message composition dropped/altered a real figure "
                    "— keeping template: %r", text,
                )
                return None
            return text
        except Exception as e:
            logger.warning(f"Variance message composition call failed: {e}")
            return None

    def rephrase(self, utterance: str, draft: str) -> str | None:
        """Conversational voice pass: rewrite a template reply so it sounds
        naturally spoken, preserving every fact. Returns None on any failure
        or suspicious output so the caller keeps the reliable template —
        routing is never affected, this touches display text only. Always
        uses the raw SDK path regardless of LLM_CLIENT_BACKEND: a one-line
        rewrite needs no graph, tools, or retry policy."""
        if time.monotonic() < self._voice_down_until:
            return None
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": VOICE_SYSTEM_PROMPT},
                    # Real bug, fixed: the draft used to ride in the USER
                    # message, and the guardrails gateway redacts user-role
                    # content only ("Redact only user-role content; system
                    # prompts are trusted and left intact" -- guardrails
                    # app/pii.py). Real FIELDCRAFT project names ARE person names
                    # ("Dan Cabush, Racine-Roof Enhancement-646248"), so
                    # Presidio replaced them with <PERSON> before the model saw
                    # the draft, the model faithfully echoed the placeholder,
                    # and PRESIDIO_PLACEHOLDER_RE below then correctly threw
                    # the whole rewrite away. Net effect, observed on every
                    # project-named response live: the conversational voice was
                    # silently dead and every such reply shipped as the raw
                    # template. The draft is EDEN's own generated text built
                    # from its own Mongo reads -- not user input -- so it
                    # belongs in the trusted system role. Only the user's
                    # actual utterance stays user-role, where redaction is
                    # correct and where a leak can't corrupt the facts (the
                    # placeholder guard still runs, for anything that slips
                    # through from the utterance itself).
                    {
                        "role": "system",
                        "content": f"Draft reply to rewrite: {draft}",
                    },
                    {
                        "role": "user",
                        "content": (
                            f'User said: "{utterance}"\n'
                            "Rewritten reply:"
                        ),
                    },
                ],
                # Mild sampling on purpose — the whole point is that the same
                # canned reply doesn't come out word-for-word identical every
                # time. Facts are anchored by the draft, not the temperature.
                temperature=0.4,
                max_tokens=300,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            text = (response.choices[0].message.content or "").strip().strip('"').strip()
            # A rewrite that balloons past the draft's ballpark, goes empty,
            # or starts emitting JSON means the small model wandered off —
            # keep the template instead. Same for a literal fill-in-the-blank
            # placeholder like "[insert page name or button location]" —
            # observed live: rephrasing a draft that named no specific page
            # ("I can't answer data questions yet... I can still navigate you
            # anywhere in FIELDCRAFT"), the model wrote what looks like a TEMPLATE
            # for an answer instead of a finished one, bracket placeholder
            # and all, which is worse than the plain original — a user must
            # never see raw templating syntax.
            has_placeholder = bool(re.search(r"[\[<]\s*(insert|fill|your|page name|button|xxx|tbd)", text, re.IGNORECASE))
            if not text or len(text) > max(400, 2 * len(draft)) or text.startswith("{") or has_placeholder:
                if has_placeholder:
                    logger.warning("Voice rephrase produced a template placeholder — keeping original: %.120r", text)
                return None
            # Fact-preservation guard: every number in the draft (a real
            # dollar figure, percentage, or day count sourced directly from
            # Mongo) must survive the rewrite verbatim. The prompt instructs
            # this, but instruction isn't verification -- a rewrite that
            # silently turns $64,600 into $64,000 would otherwise reach the
            # frontend with nothing catching it.
            draft_numbers = _number_tokens(draft)
            if draft_numbers and not draft_numbers.issubset(_number_tokens(text)):
                logger.warning(
                    "Voice rephrase dropped/altered a number from the draft — "
                    "keeping original: draft_numbers=%s rewritten=%.160r",
                    draft_numbers, text,
                )
                return None
            # PII-redaction leak guard: when LLM_BASE_URL points at guardrails
            # (not raw vLLM), the gateway's Presidio input rail redacts the
            # draft's own text before the model ever sees it -- a real project/
            # customer name that happens to match a PERSON/LOCATION/etc. entity
            # (e.g. "Ethan Miller") comes back as a literal "<PERSON>" in the
            # rewrite. Confirmed live: the model faithfully echoes whatever
            # placeholder it was handed. Same principle as the number guard
            # above -- only reject placeholders NEWLY introduced by the round
            # trip, not ones already present in the draft.
            leaked = set(PRESIDIO_PLACEHOLDER_RE.findall(text)) - set(PRESIDIO_PLACEHOLDER_RE.findall(draft))
            if leaked:
                logger.warning(
                    "Voice rephrase leaked a PII-redaction placeholder not in the "
                    "original draft — keeping original: leaked=%s rewritten=%.160r",
                    leaked, text,
                )
                return None
            # Real bug, fixed: confirmed live -- despite the prompt
            # instruction against it, this rewrite still sometimes invents
            # its own closing offer/question ("Would you like to see the X
            # page?") when the draft has none, including offering pages that
            # don't exist in the registry at all. A user confirming that
            # invented offer with "yes" has nothing real to confirm and
            # dead-ends. Same "instruction alone isn't verification"
            # principle as the number guard above: if the draft didn't end
            # with a question, the rewrite must not originate one either.
            if not draft.rstrip().endswith("?") and text.rstrip().endswith("?"):
                logger.warning(
                    "Voice rephrase invented a new question/offer not in the "
                    "draft — keeping original: draft=%.160r rewritten=%.160r",
                    draft, text,
                )
                return None
            # Page-name preservation guard: a real, already-permission-checked
            # page offer in the draft must name that SAME page in the
            # rewrite -- not a different, possibly unpermitted or
            # nonexistent one. See _SUGGESTED_PAGE_RE above.
            draft_page_match = _SUGGESTED_PAGE_RE.search(draft)
            if draft_page_match:
                draft_page = draft_page_match.group(1).strip()
                if draft_page not in text:
                    logger.warning(
                        "Voice rephrase swapped or dropped the offered page "
                        "name — keeping original: draft_page=%r rewritten=%.160r",
                        draft_page, text,
                    )
                    return None
            # Real bug, fixed: confirmed live -- rephrasing a plain STATUS
            # draft (completion %, task counts, on-track/no-delay) that
            # stated no cause at all, the model invented one anyway: "...was
            # updated due to changes in scope and resource allocation." Not a
            # dropped/altered NUMBER (all four numbers survived intact, so
            # the guard above didn't catch it) and not a new closing
            # QUESTION (the sentence isn't phrased as one) -- a fabricated
            # CAUSAL CLAIM is a third, previously uncovered shape of
            # invention this rewrite pass can produce, on ANY draft, not
            # just RAG/Q1/Q2 answers (this is the last, general-purpose
            # safety net every rephrased response passes through). Same
            # "instruction alone isn't verification" principle as the two
            # guards above: if the draft asserted no cause, the rewrite must
            # not originate one either.
            causal_in_text = _CAUSAL_PHRASE_RE.search(text)
            if causal_in_text and not _DRAFT_STATES_CAUSE_RE.search(draft):
                logger.warning(
                    "Voice rephrase invented a causal claim not in the draft "
                    "— keeping original: claim=%r draft=%.160r rewritten=%.160r",
                    causal_in_text.group(0), draft, text,
                )
                return None
            # Real bug, fixed: confirmed live -- a draft with no access/
            # permission problem at all was rewritten into a fabricated
            # RBAC-denial claim ("I don't have access to view other users'
            # projects"). Same "instruction alone isn't verification"
            # principle as the causal-claim guard above, for a fabrication
            # shape that guard doesn't cover: if the draft never raised an
            # access/permission issue, the rewrite must not originate one.
            access_denial_in_text = _ACCESS_DENIAL_PHRASE_RE.search(text)
            if access_denial_in_text and not _ACCESS_DENIAL_PHRASE_RE.search(draft):
                logger.warning(
                    "Voice rephrase invented an access/permission-denial claim "
                    "not in the draft — keeping original: claim=%r draft=%.160r "
                    "rewritten=%.160r",
                    access_denial_in_text.group(0), draft, text,
                )
                return None
            return text
        except Exception as e:
            self._voice_down_until = time.monotonic() + 60.0
            logger.warning(
                f"Voice rephrase failed — keeping template replies for 60s: {e}"
            )
            return None

    def _call_llm(self, context_block: str, user_message: str) -> dict | None:
        """Structured-output call: response_format=NavigationUnderstanding
        constrains generation to that JSON Schema at the token level, so the
        runtime cannot return prose, an unparsable fragment, or a field
        outside the declared enum/range — the class of bug STRICT_SUFFIX and
        _extract_json's brace-hunting used to paper over after the fact.

        Falls back to _call_llm_legacy (the old free-text + tolerant-parse
        path) if the structured call itself fails — e.g. a runtime that
        doesn't honor json_schema response formatting. Same
        try-then-degrade discipline as every other backend swap in this
        file (agents_sdk/langgraph/combined each fall back to "openai" on
        init failure); this is the same idea one call at a time instead of
        for the process lifetime, since a runtime's json_schema support
        doesn't change request to request.

        `context_block` (route registry/session context/history/pending-goal
        -- EDEN-owned, may contain a real project name) is its OWN system
        message, separate from SYSTEM_PROMPT and never placed in the
        user-role turn -- see build_user_message's docstring for why.
        """
        try:
            response = self.client.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "system", "content": context_block},
                    {"role": "user", "content": user_message}
                ],
                # 0.0 = greedy decoding. At 0.1 the same utterance classified
                # differently across runs (observed live: "recent payment
                # details" → Invoices, then Customer Finance, then a project
                # question — three runs, three answers). Routing is
                # classification; sampling variance is pure downside here.
                temperature=0.0,
                # Was 2048 -- the model's ENTIRE real context window (see
                # MODEL_CONTEXT_WINDOW_TOKENS's docstring/env comment),
                # leaving zero room for any prompt at all. thinking is
                # disabled below, so this call's actual output is just the
                # small NavigationUnderstanding JSON -- 300 is generous
                # headroom for that, not a real cap on anything this call
                # produces.
                max_tokens=300,
                response_format=NavigationUnderstanding,
                # SmolLM3-3B is a reasoning model — thinking mode is on by
                # default and burns the entire max_tokens budget on an
                # internal <think> trace before ever emitting the JSON answer
                # (verified: "hello" alone costs 249 prompt tokens and 86
                # completion tokens of pure reasoning with this on). Routing
                # is pure classification with no analytical work worth
                # showing — disabling this also cuts prompt overhead from the
                # reasoning-enabled chat template (249 -> 69 tokens on the
                # same "hello" test).
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            message = response.choices[0].message
            if message.refusal:
                logger.warning("LLM refused to answer: %s", message.refusal)
                return None
            if message.parsed is not None:
                return message.parsed.model_dump()
            # Some runtimes accept response_format=json_schema but still
            # populate .content instead of .parsed — tolerant-parse that
            # content rather than treating it as a hard failure.
            return self._extract_json(message.content or "")
        except Exception as e:
            logger.warning(
                f"Structured-output call failed ({e}) — falling back to json_object mode"
            )
            return self._call_llm_legacy(context_block, user_message)

    def _call_llm_legacy(self, context_block: str, user_message: str, strict: bool = False) -> dict | None:
        """Pre-structured-output path: free-text generation + tolerant
        parsing (_extract_json) + a strict re-prompt on parse failure. Kept
        as the fallback for a runtime that doesn't support json_schema
        response formatting — this backend is the last-resort default, so
        it has to keep working even there."""
        system = SYSTEM_PROMPT
        if strict:
            system += STRICT_SUFFIX

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "system", "content": context_block},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.0,
                # Same fix and reasoning as _call_llm's max_tokens above --
                # this is that same call's fallback path, not a separate
                # concern.
                max_tokens=300,
                # Force JSON-object output where the runtime honors it (Ollama
                # and vLLM both do) — sharply cuts the "prose around JSON" case.
                response_format={"type": "json_object"},
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            content = response.choices[0].message.content.strip()
            parsed = self._extract_json(content)
            if parsed is None:
                logger.warning("LLM returned invalid JSON: %.120r", content)
                if not strict:
                    return self._call_llm_legacy(context_block, user_message, strict=True)
            return parsed
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return None

    @staticmethod
    def _extract_json(content: str) -> dict | None:
        """Parse the model's reply into a dict, tolerating reasoning traces and
        stray prose. Tries a clean parse, then the outermost {...} substring."""
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        # Strip ```json ... ``` fences if the model added them.
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content).strip()
        try:
            result = json.loads(content)
            return result if isinstance(result, dict) else None
        except json.JSONDecodeError:
            pass
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end > start:
            try:
                result = json.loads(content[start:end + 1])
                return result if isinstance(result, dict) else None
            except json.JSONDecodeError:
                return None
        return None
