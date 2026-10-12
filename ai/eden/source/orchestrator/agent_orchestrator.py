import difflib
import logging
import os
import re

from langgraph.graph import END, START, StateGraph

from source.agents.reporting_agent import BILLING_SIGNALS
from source.model.navigation_models import (
    NavigationOption,
    NavigationResponse,
    NavigationSuggestion,
    SessionContext,
)
from source.orchestrator.eden_state import AGENT_NODE_REGISTRY, EdenState
from source.prompts import TRIAGE_PAGE_CONTEXT_TEMPLATE
from source.resolvers.conversation_memory import ConversationMemory
from source.utils.audit_log import write_navigation_audit_log
from source.utils.llm_client import PRESIDIO_PLACEHOLDER_RE

logger = logging.getLogger(__name__)

# Below this, a "navigate" classification is treated as genuinely ambiguous
# rather than committed to silently — e.g. "take me to finance" plausibly
# means Finance, Invoices, or Job Ledger, and the model itself is unsure
# which. Confirmed high-precision hits (exact keyword tier, or an LLM call
# with a clear single winner) score 0.9-0.95 and never reach this check, so
# it only fires for the genuinely close calls.
ROUTE_DISAMBIGUATION_CONFIDENCE_THRESHOLD = 0.75

# Flat, tenant-wide routes that have a project-scoped sibling, for the
# exact-keyword direct-navigation tier only (a SEPARATE, smaller map from
# _attach_page_suggestion's own entity_target_route_id below -- that one
# also covers already-entity_required routes like job_ledger, which don't
# need this substitution since NavigationAgent._finalize already resolves
# their project context; this one only lists routes that are NOT
# entity_required, since those are the ones _finalize never looks at page
# context for at all).
#
# Real bug, fixed: "take me to the invoice" while sitting on a specific
# project's page matched invoice_list outright (its own keywords, "invoice"/
# "invoices", need no project context to be satisfied) and landed on the
# flat, all-projects /invoice list instead of that project's own invoice
# tab. project_invoice_view's keywords ALL require the literal word
# "project" ("project invoice", "invoice for this project"), so it never
# even enters the keyword race for this phrasing -- NavigationAgent.
# _keyword_match's own on-project-page scoring bonus only breaks ties
# between routes that BOTH already matched, it can't invent a match where
# the entity-required sibling's vocabulary doesn't overlap the utterance at
# all. Confirmed live, and the flat page's own frontend component also
# crashed on load when reached this way ("undefined is not iterable" in
# changeStatus/fetchRows) -- reason enough on its own to route around it
# whenever the real, project-scoped page is what the user actually wants.
_DIRECT_NAV_PROJECT_SCOPED_ALIAS = {
    "customers_projects": "project_information",
}
# Real gap, fixed by REMOVAL rather than redirect: invoice_list ("/invoice")
# used to live here too, aliased to project_invoice_view when on a project
# page, with two more constants (now deleted) handling "no project context"
# and "redirect target denied by permission." That whole redirect-away
# apparatus only covered the exact-keyword navigation tier -- confirmed
# live that the semantic-retrieval/LLM tier could still surface invoice_
# list as a candidate and route_id, reaching the SAME confirmed-broken
# frontend page ("undefined is not iterable" in changeStatus/fetchRows on
# load) through a path this redirect logic never touched. Removing
# invoice_list from route_registry.json entirely closes every tier at
# once, not just this one -- see navigation_agent.py's
# ENTITY_TAB_ALIAS_ROUTE_STATE comment for the full crash writeup.
# project_invoice_view (the working, entity_required="project" sibling)
# absorbed invoice_list's bare "invoice"/"billing"/"bill" keywords, so a
# no-project-context "show me invoices" now resolves through ITS existing
# ask-which-project chain instead of ever landing on the broken page.

# Same "low confidence -> ask, don't guess" discipline as the route-level
# threshold above, applied to eden_router's own top-level triage decision —
# specifically between insight/reporting, since an ambiguous money question
# (internal cost vs. the user's own bill) is the one place a silent wrong
# guess actually matters (navigation vs. either is low-stakes: worst case is
# a deflection, not exposing/hiding the wrong financial data). Lower than
# the route threshold because this is a coarse 3-way classification, not
# narrowing many candidates — the model is asked to reserve low scores for
# genuine ambiguity, not routine uncertainty.
TRIAGE_CLARIFY_CONFIDENCE_THRESHOLD = 0.5

# Multi-step reasoning trial — a second, independent LLM pass that
# double-checks a CONFIDENT primary classification when retrieval itself
# found two candidates close enough to compete (see
# AgentOrchestrator._maybe_verify_ambiguity). Off by default: this is a
# trial, easy to turn off/revert by leaving the env var unset — flip
# MULTI_STEP_VERIFICATION=true to enable it. Costs one extra LLM call
# (~1s), and only for the narrow slice of requests where retrieval's own
# scores signal a close call the primary classifier's confidence missed.
MULTI_STEP_VERIFICATION_ENABLED = (
    os.getenv("MULTI_STEP_VERIFICATION", "false").strip().lower() == "true"
)

# How close the top-2 retrieval scores must be, relative to the top score,
# before treating retrieval itself as flagging a genuine close call —
# independent of what confidence the LLM self-reported. Calibrated from the
# observed "where i can see the customer information" case: customer_table
# scored 0.301 vs customer_info_dashboard's 0.276, an 8% relative gap, yet
# the LLM returned confidence 0.95 for customer_table alone. 0.15 catches
# that with room to spare; most unambiguous utterances have a clear
# front-runner (>30% relative gap to the next candidate).
NEAR_TIE_MARGIN_RATIO = 0.15

# DEGRADED FALLBACK ONLY (no longer the primary router): the top-level
# "which agent" decision is now LLMClient.triage() on every request — a
# keyword list deciding between entire specialists was hardcoded routing
# wearing an AI label, and silently failed on any phrasing it wasn't
# written for. This list survives so obviously variance-shaped requests
# stay routable to Insight while the triage LLM call itself is failing
# (tunnel down, model crashed) — same pattern as Navigation's own
# fuzzy-keyword fallback when its LLM is unreachable.
VARIANCE_SIGNALS = [
    "variance", "over budget", "over estimate", "cost overrun",
    "why did cost", "why is cost", "actual vs estimate", "actual vs. estimate",
]

# NOT a general routing override -- deliberately narrower than VARIANCE_
# SIGNALS above and NOT a degraded fallback. Confirmed live, 100%
# reproducible across every test (not occasional LLM judgment noise): the
# triage LLM consistently classifies "what is the material cost"/"worker
# cost"/"tool cost"/"overhead cost" as agent='reporting' ("relates to the
# user's own billing and cost tracking"). That's not an ambiguous-phrasing
# judgment call the LLM is entitled to make either way -- reporting_agent.py
# structurally can NEVER answer these fields: materialCost/workerCost/
# toolCost/overheadCost are deliberately never included in the data its RAG
# call is given, for any user, by design (customer financial safety, see
# reporting_agent.py's own module docstring). Routing this phrasing to
# reporting isn't "the LLM picked a defensible interpretation," it's
# guaranteed to dead-end in a refusal reporting can never recover from,
# regardless of role. Applied as a correction to triage's OWN result
# (never overriding a "navigation"/other pick, and still passed through
# _route_insight_or_reporting below so the customer-safety role gate still
# applies exactly as it would for a correctly-triaged "insight" pick) --
# not a parallel/competing routing path. Kept in sync with insight_agent.
# COST_BREAKDOWN_SIGNALS' field vocabulary; a local copy, not an import,
# matching VARIANCE_SIGNALS' own convention of not coupling the orchestrator
# to an agent module's internals for a handful of words.
_INTERNAL_COST_BREAKDOWN_SIGNALS = [
    "material cost", "materials cost", "cost of material", "cost of materials",
    "worker cost", "labor cost", "labour cost", "cost of labor", "cost of labour",
    "tool cost", "tools cost", "cost of tools",
    "overhead cost", "cost of overhead",
]

# Real bug, fixed: UserRoleResolver.get_project_ids returning [] is
# documented (see its own docstring) to mean "no scoping data available,
# don't filter" -- the correct default for an INTERNAL role (a PM
# legitimately needs to see the whole tenant's projects while this Gold
# field is still being populated). Applying that SAME fail-open default to
# a CUSTOMER role is a real cross-customer data leak, not a hypothetical
# one -- verified live: a customer account with unpopulated project_ids
# saw every OTHER customer's project name/order number/status in
# disambiguation, not just their own. FIELDCRAFT Cloud's "customer" role is an
# individual homeowner/client, not tenant staff -- unscoped-by-default is
# never correct for them. This sentinel forces EntityResolver's
# `{"projectId": {"$in": allowed_project_ids}}` filter to match zero real
# projects (no project id will ever equal this string), so a customer with
# no real scoping data sees "no projects found" (a safe, generic deflection
# already handled by every caller) instead of the whole tenant.
_NO_CUSTOMER_PROJECTS_SENTINEL = ["__no_customer_projects__"]

# Greetings/pleasantries are things a small LLM classifies unreliably — and a
# wrong guess dumps the user into an ugly "I couldn't find that page". Handle
# them deterministically before the LLM.
#
# "What can I do here" / "where am I" style page-orientation questions used
# to have their own GUIDANCE_SIGNALS keyword list here, running as a
# pre-LLM tier -- removed per the same "let the LLM decide, not a keyword
# list" direction as ENTITY_STOP_WORDS: LLMClient.understand() already
# classifies these as intent="guidance" (see _dispatch_with_llm's
# `if intent == "guidance"` branch below), so the keyword list was a
# redundant second classifier duplicating a decision triage/understand
# already make, with the same whack-a-mole phrasing risk.
#
# Same reasoning removed DATA_QUESTION_SIGNALS (a keyword list for "what is
# the margin" / "how much" style questions) -- LLMClient.understand()
# already classifies these as intent="data_question" (see
# _dispatch_with_llm's `if intent == "data_question"` branch below).
GREETING_WORDS = {
    "hi", "hello", "hey", "yo", "hiya", "howdy", "greetings", "sup",
    "thanks", "thank", "thankyou", "cheers", "bye", "goodbye", "morning",
    "afternoon", "evening", "welcome",
}
GREETING_FILLER = {
    "there", "eden", "again", "everyone", "all", "buddy", "mate", "you",
    "good", "so", "much", "a", "lot", "please", "team",
    # "okay thanks" is gratitude, not a bare "okay" confirmation — these are
    # filler only, so they never make something a greeting by themselves and
    # a lone "ok" still reaches the affirmation tier.
    "ok", "okay",
}

# Bare confirmations after EDEN offered a page ("Did you mean Projects?").
# Same all-tokens-explained shape as the greeting check: "yes" alone is a
# confirmation, "yes take me to invoices" is not.
AFFIRMATION_WORDS = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "correct", "right",
    "exactly", "confirm",
}
AFFIRMATION_FILLER = {
    "please", "do", "it", "that", "one", "go", "ahead", "thanks", "thank",
    "you", "the",
}

# Bare rejections after EDEN navigated somewhere or offered options ("no",
# "not that one", "nope, wrong page"). Same all-tokens-explained shape as
# the greeting/affirmation tiers — "no, show me invoices instead" carries
# its own content and falls through to normal dispatch.
NEGATION_WORDS = {"no", "nope", "nah", "not"}
NEGATION_FILLER = {
    "that", "thats", "this", "one", "it", "the", "them", "those", "i",
    "dont", "didnt", "want", "wanted", "mean", "meant", "wrong", "page",
    "thanks", "thank", "you",
}

# Meta-recall questions about the CONVERSATION itself ("what was the project
# before this one") rather than a navigation command or a data question about
# a named entity. Requires both a before/previous marker AND a conversational
# BACK-REFERENCE phrase -- bare "project" was too generic (matched real data
# questions like "what was the project margin before this change order",
# routing a legitimate Insight/Reporting question into Navigation instead,
# where it has no way to resolve). "project" only counts paired with a
# recall-shaped phrase (previous/prior/earlier project, or "project we
# were/are"), never on its own.
_RECALL_MARKER_RE = re.compile(r"\b(before|previous|prior|earlier)\b", re.IGNORECASE)
_RECALL_CONTEXT_RE = re.compile(
    r"\b(previous project|prior project|earlier project|project we (were|are)|"
    r"talking|discuss(ed|ing)?|we were|were we|mentioned|switch(ed)?)\b",
    re.IGNORECASE,
)

# Typo-tolerant fallback for the "previous project"-shaped core of the two
# regexes above (same fuzzy-typo pattern as insight_agent.py/
# reporting_agent.py's _fuzzy_topic_word). Confirmed live: "take me to the
# rpevious projecct" (both words typo'd) matched neither exact regex at all
# and fell through to a generic "which project did you mean?" navigation
# prompt instead of resolving via conversation history -- the exact "recall
# reference silently dropped" failure mode this whole block exists to
# prevent, just triggered by a typo instead of a data-question-in-the-same-
# sentence. Marker words use the same 0.85 cutoff as insight/reporting's
# fuzzy topic check (no false positives found against common words); the
# single "project" target needs a tighter 0.9 -- "protect" ties "porject"
# (a real plausible typo) at an identical 0.857 ratio, so 0.85 there would
# false-positive on "protect" every time it appears alongside any marker
# word. 0.9 keeps the confirmed live case ("projecct", 0.933) clear of that
# collision.
_RECALL_MARKER_WORDS = ("before", "previous", "prior", "earlier")
_RECALL_PROJECT_WORDS = ("project",)

# _strip_route_fields_for_mobile rewrites a "navigate"-shaped message into
# informational phrasing before nulling the route -- every construction
# site across navigation_agent.py and this file phrases a navigate message
# as an ACTION ("Taking you to Invoices.", "Back to Invoices.") because on
# web that action really happens. On mobile it never does (there's no page
# to send the route to), so left as-is the message would claim something
# that didn't occur. Ordered list of (pattern, replacement) applied in
# sequence -- deliberately targets the small, closed set of action phrasings
# these construction sites actually use rather than a general "rewrite any
# sentence" attempt, same reasoning as the existing "Would you like to see
# the X page?" strip a few lines above this function.
#
# Real bug, fixed: confirmed live on fieldcraftmind (2026-09-03) -- these patterns
# were anchored to `^` (start of the WHOLE message), so a message where the
# action phrase is the second sentence (e.g. insight_agent.
# projects_disambiguation's over-cap redirect: "You have more projects than
# I can list here. Taking you to the Projects page...") never matched at
# all, and the false action claim survived untouched on mobile. Anchored to
# "start of message OR right after a '. '" instead, so it catches the
# phrase wherever it starts a sentence, not just the very first one.
_SENTENCE_START = r"(?:^|(?<=\. ))"
_MOBILE_NAVIGATE_PHRASING = [
    (re.compile(_SENTENCE_START + r"Taking you to (.+?) —"), r"\1 —"),
    (re.compile(_SENTENCE_START + r"Taking you to (.+?)\.\s*"), r"\1. "),
    (re.compile(_SENTENCE_START + r"Back to (.+?)\.\s*$"), r"\1."),
    (re.compile(r"— taking you to (.+?) instead\."), r"— \1 is available instead."),
    (re.compile(r"Taking you there instead\.\s*$"), r"It's available there instead."),
    # Real bug, fixed (PR review, Raja): navigation_agent.py's own
    # _navigate_message builds two more action-shaped phrasings this list
    # never covered -- "Found it — X is in the Y module." (show/find/where
    # utterances) and the DEFAULT fallback "Right this way — X." (used
    # whenever the utterance matches none of take/go/open/navigate/show/
    # find/where). The default branch is not an edge case -- it fires for
    # a large share of ordinary navigate phrasings -- so both leaked
    # through unrewritten on mobile, still implying a page opened.
    (
        re.compile(_SENTENCE_START + r"Found it — (.+?) is in the (.+?) module\.\s*"),
        r"\1 is in the \2 section. ",
    ),
    (re.compile(_SENTENCE_START + r"Right this way — (.+?)\.\s*"), r"\1 is available. "),
]


def _mobile_navigate_text(message: str) -> str:
    for pattern, replacement in _MOBILE_NAVIGATE_PHRASING:
        message = pattern.sub(replacement, message)
    return message.strip()


def _fuzzy_recall_signal(utterance_lower: str) -> bool:
    words = re.findall(r"[a-z]+", utterance_lower)
    has_marker = any(
        len(w) >= 6 and difflib.get_close_matches(w, _RECALL_MARKER_WORDS, n=1, cutoff=0.85)
        for w in words
    )
    has_project = any(
        len(w) >= 6 and difflib.get_close_matches(w, _RECALL_PROJECT_WORDS, n=1, cutoff=0.9)
        for w in words
    )
    return has_marker and has_project

# A recall reference and a DATA question can appear in the same sentence --
# "what is the previous project cost" is both "which project did I mean"
# (recall) and "what does it cost" (reporting/insight). The recall
# short-circuit below used to fire on the recall half alone and hand the whole
# turn to Navigation, which answered only which project it was and dropped the
# question. This is the "is there a data question in here too" test that keeps
# the short-circuit to genuinely PURE recall ("what was the previous project we
# discussed" -- none of these words). Deliberately the vocabulary Insight and
# Reporting actually answer, not a general question detector.
_DATA_QUESTION_RE = re.compile(
    r"\b(cost|costs|costing|price|pricing|budget|spend|spent|committed|margin|"
    r"variance|profit|status|schedule|scheduled|timeline|late|delay(ed|s)?|slip|"
    r"overdue|due|progress|complete|completed|completion|bill|billed|billing|"
    r"invoice[sd]?|owe[sd]?|owing|balance|paid|payment[s]?|total|amount)\b",
    re.IGNORECASE,
)

# "Take me back to the previous page" -- distinct from _RECALL_MARKER_RE
# above, which is about recalling a PROJECT mentioned earlier in the
# conversation. This is about re-navigating to the last PAGE. Deliberately
# narrow (requires "back" or "page", not the bare word "previous") to avoid
# false-matching an unrelated sentence that happens to contain "previous"
# ("show me the previous invoice") — see _resolve_go_back's docstring.
_GO_BACK_RE = re.compile(
    r"\b(go(ing)?\s+back|take\s+me\s+back|head(ing)?\s+back|"
    r"back\s+to\s+(the\s+)?(previous|last)\s+page|(previous|last)\s+page)\b",
    re.IGNORECASE,
)

# Conversation loop: how many unresolved turns (questions asked, options
# offered) a single pending goal survives before it's dropped — after this
# many failed clarifications, carrying the stale original request forward
# does more harm (polluting retrieval for what's probably a new attempt in
# fresh words) than good.
GOAL_MAX_ATTEMPTS = 3

# How confident LLMClient.classify_goal_continuation must be that a new
# message really IS the reply to EDEN's open question before that goal is
# allowed to capture the turn. Deliberately asymmetric (only consulted to
# CONTINUE, never to drop): continuing wrongly hijacks the new question,
# answers something the user didn't ask, and keeps doing it for up to
# GOAL_MAX_ATTEMPTS turns; dropping wrongly costs one clarifying re-ask.
# Set at the same 0.5 coin-flip line as TRIAGE_CLARIFY_CONFIDENCE_THRESHOLD
# -- below that the model is guessing, and a guess should not beat a fresh
# classification of what the user actually just typed.
GOAL_CONTINUATION_CONFIDENCE_THRESHOLD = 0.5

# An explicit navigation command is a FRESH instruction, never an option
# pick -- checked deterministically before the goal-continuation LLM call
# for the same reason the greeting/affirmation/negation tiers are: this is a
# closed, tiny vocabulary and a structural fact about the sentence, not the
# open-ended "does this answer my question" judgment the model owns. Needed
# because the model reliably (0.95 confidence, measured against Qwen3-8B)
# reads "take me to invoices" as picking an offered "Invoices" option --
# understandable, but wrong in consequence: the open goal's owning agent is
# Insight/Reporting (GOAL_KIND_AGENT never maps to navigation), so treating
# a navigation command as its reply strands the user in an agent that cannot
# navigate. Anchored at the start of the message so it only fires on a real
# imperative, never on a mention mid-sentence.
_FRESH_COMMAND_RE = re.compile(
    r"^\s*(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?"
    r"(?:show\s+me|take\s+me\s+to|take\s+me|bring\s+me\s+to|open|go\s+to|"
    r"navigate\s+to|where\s+is|where\s+can\s+i|i\s+want\s+to\s+see|"
    r"i\s+wanna\s+see|i\s+need\s+to\s+see|let\s+me\s+see)\b",
    re.IGNORECASE,
)

# Deterministic fallback for the same decision when that LLM call fails
# (failure-escalation: every LLM-primary decision needs one). A genuine
# reply to a clarification is short -- "yes", "the second one",
# "DEMO-Riverside"; a new request is a self-contained sentence. Anything
# longer than this many words is treated as a fresh request rather than an
# answer. Only reached when the model is unreachable/malformed.
GOAL_REPLY_MAX_WORDS_FALLBACK = 6

# Real bug, fixed: confirmed live -- selecting a project from a disambiguation
# list's clickable option cards re-sends that option's own label VERBATIM as
# the next chat utterance (e.g. "Madison-Roof Enhancement-334499 (Order
# #334499)"), the exact string EDEN itself wrote via
# InsightAgent/NavigationAgent's `f"{m.project_name} (Order #{m.order_no})"`
# label format. _resolve_insight_entity used to hand this whole noisy string
# to extract_project_name (a small local LLM) with no fallback tried once the
# call returned a (wrong) truthy name, so the click silently failed to
# resolve and _insight_node/_reporting_node's `response is None` branch
# re-ran the SAME "which project?" disambiguation -- from the user's side,
# clicking an option just re-shows the list. EDEN generated this exact
# string, so the trailing order number is ground truth, not something to
# re-extract: pull it directly and skip the LLM/regex chain entirely for
# this one, self-inflicted, 100%-reliable case.
_OFFERED_OPTION_ORDER_NO_RE = re.compile(r"\(Order #(\d+)\)\s*$")

# Sentinel _dispatch_with_llm returns as `route_id` for an out-of-scope
# response — never a real route id (registry ids are lowercase snake_case,
# never match this), so the caller can detect it without a new tuple
# element. See _dispatch_with_llm's out_of_scope branch for why this exists.
_OUT_OF_SCOPE_ROUTE_SENTINEL = "__out_of_scope__"

# Open "which project?" thread -> which agent owns it. eden_router routes a
# reply straight back to the asking agent, no triage needed — continuing an
# open conversation is continuity, not a fresh routing decision.
# insight_context_switch/reporting_context_switch (see
# AgentOrchestrator._maybe_confirm_project_context_switch) follow the exact
# same continuity mechanism — a yes/no reply to "you were asking about X,
# now you're on Y" routes back to whichever agent asked, same as any other
# open goal.
GOAL_KIND_AGENT = {
    "project_lookup": "insight",
    "reporting_lookup": "reporting",
    "insight_context_switch": "insight",
    "reporting_context_switch": "reporting",
    # Per explicit user direction: unlike *_context_switch above (the LLM
    # decides whether a NAMELESS follow-up is worth asking about), naming a
    # SPECIFIC project/order-number that differs from the page you're on is
    # ALWAYS worth confirming, no judgment call -- see
    # _maybe_confirm_explicit_project_switch. One kind per agent (not a
    # single shared kind) because each must resume in the SAME agent that
    # asked, exactly like the two lines above.
    "navigation_explicit_switch": "navigation",
    "insight_explicit_switch": "insight",
    "reporting_explicit_switch": "reporting",
    # Real bug, fixed: navigation's own multi-turn "which page did you mean?"
    # goal (opened by _update_goal_state for a disambiguation/fallback
    # response, see _run_navigation_cascade's final _finalize_turn call) used
    # to get NO "kind" at all, since navigation doesn't need cross-agent
    # continuity the way insight/reporting do. But that meant
    # _eden_router_node's own relatedness check above -- GOAL_KIND_AGENT.get(
    # kind) is None -> skip _is_goal_reply/clear_pending_goal entirely -- never
    # ran for it either. A brand-new, unrelated request typed while this goal
    # was open (e.g. "show me the materials" left open, then "take me to the
    # timelog") sailed straight past this guard and into
    # _dispatch_with_llm's own combined-text retrieval, which blindly
    # concatenates the stale original_utterance with the new one -- confirmed
    # live (fieldcraftmind, 2026-09-01). Tagging it "navigation_clarify" here routes
    # it through the exact same _is_goal_reply check every other goal kind
    # already gets.
    "navigation_clarify": "navigation",
}

# Sentinel for _resolve_insight_entity's optional pre-fetched `extraction`
# param -- None is itself a meaningful value there (the LLM call failed),
# so a plain `= None` default couldn't distinguish "not provided" from
# "provided and it was None".
_EXTRACTION_NOT_GIVEN = object()


class AgentOrchestrator:
    """LLM-first agentic dispatch. Deterministic tiers handle only what
    they can claim with near-certainty — greetings, feature spotlights,
    guidance/data-question templates, and exact keyword hits the route's
    own vocabulary fully explains. Everything else goes to the default
    path: semantic retrieval (RAG) narrows candidates, then the
    LangGraph+Agents SDK backend classifies with conversation history and
    verifies named entities via tools. Fuzzy keyword matching survives
    only as the degraded fallback when the LLM is unreachable.
    Agents execute — the orchestrator decides."""

    def __init__(self):
        from source.agents.insight_agent import InsightAgent
        from source.agents.navigation_agent import NavigationAgent
        from source.agents.reporting_agent import ReportingAgent
        self.registry = {
            "navigation": NavigationAgent(),
            "insight": InsightAgent(),
            "reporting": ReportingAgent(),
        }
        # Role-based response shaping (customer-safe vs. internal view) —
        # reads Gold's resolved roles, deliberately NOT behind
        # SKIP_RBAC_CHECK: even a pilot with permission checks bypassed
        # must never show internal financials to a customer role.
        from source.resolvers.user_role_resolver import UserRoleResolver
        self.role_resolver = UserRoleResolver()
        # Shared LLM brain — one client, one warm model.
        self.llm = self.registry["navigation"].llm_client
        # Reporting's billing-signal audit (see reporting_agent.py) reuses
        # this same shared client rather than constructing its own —
        # "one shared model, many prompts," not a second connection.
        self.registry["reporting"].llm_client = self.llm
        # Insight's variance_response composes its money sentence via this
        # same shared client too (see LLMClient.compose_variance_message).
        self.registry["insight"].llm_client = self.llm
        # Short-term memory for follow-up resolution on the LLM path only —
        # see ConversationMemory's docstring for why this doesn't touch the
        # deterministic keyword/feature paths.
        self.conversation_memory = ConversationMemory()
        # Conversational voice: rewrite template replies through the LLM so
        # EDEN sounds spoken rather than canned. Display text only — routing,
        # highlights, and options are decided before this runs and never
        # change. Costs one extra model call (~1s) per request; flip the env
        # var off to get instant template replies back.
        self.voice_enabled = (
            os.getenv("CONVERSATIONAL_VOICE", "true").strip().lower() == "true"
        )
        # Outer LangGraph: eden_router picks the agent (Navigation or
        # Insight) via AGENT_NODE_REGISTRY -- the real seam Document
        # search/Reporting register into next.
        self._graph = self._build_graph()

    def _scoped_project_ids(self, context: SessionContext) -> list[str]:
        """The single place allowed_project_ids is resolved for Insight/
        Reporting -- see _NO_CUSTOMER_PROJECTS_SENTINEL for why a customer's
        empty project_ids must fail CLOSED (zero visible projects). Real
        data population catching up (project_ids becoming non-empty for a
        customer) is unaffected -- that real list is always used in
        preference to the sentinel.

        Per explicit user direction: internal roles (admin/PM) are now
        scoped too, via the newly-populated project_health.assignees field
        (see EntityResolver.project_ids_for_assignee) -- a real behavior
        change from the previous "internal role always sees the whole
        tenant" default. Unlike the customer case, an internal account with
        NO assignee entries anywhere fails OPEN (unscoped, sees the whole
        tenant) rather than closed -- assignee data rolling out gradually
        across a tenant is a real, expected transition state, and locking
        out every admin whose projects haven't been retrofitted yet would
        be a severe, immediately-disruptive regression for a data-migration
        gap, not a security boundary worth enforcing that hard."""
        project_ids = self.role_resolver.get_project_ids(context.tenantId, context.userId)
        if project_ids:
            return project_ids
        if self.role_resolver.is_customer(context.tenantId, context.userId):
            # Real gap, partially closed: user_permissions.project_ids is
            # empty for effectively every customer in real data today (see
            # EntityResolver.list_projects' docstring) -- but project_health
            # independently carries the same customer identity on every
            # project it belongs to (project.customer.customerUuid,
            # confirmed live to match the customer's own JWT-derived
            # user_uid). Try that real, already-populated link before
            # falling back to the zero-projects sentinel, so a customer
            # whose project_ids simply hasn't been populated yet still sees
            # their own real project(s) instead of nothing.
            via_customer_uid = self.registry["reporting"].entity_resolver.project_ids_for_customer(
                context.tenantId, context.userId
            )
            if via_customer_uid:
                return via_customer_uid
            return _NO_CUSTOMER_PROJECTS_SENTINEL
        via_assignee = self.registry["reporting"].entity_resolver.project_ids_for_assignee(
            context.tenantId, context.userId
        )
        if via_assignee:
            return via_assignee
        return project_ids

    def _build_graph(self):
        graph = StateGraph(EdenState)
        graph.add_node("eden_router", self._eden_router_node)
        graph.add_node("navigation", self._navigation_node)
        graph.add_node("insight", self._insight_node)
        graph.add_node("reporting", self._reporting_node)
        graph.add_node("clarify", self._triage_clarify_node)
        graph.add_edge(START, "eden_router")
        graph.add_conditional_edges(
            "eden_router",
            lambda state: AGENT_NODE_REGISTRY[state["next_agent"]],
            {node_name: node_name for node_name in AGENT_NODE_REGISTRY.values()},
        )
        graph.add_edge("navigation", END)
        graph.add_edge("insight", END)
        graph.add_edge("reporting", END)
        graph.add_edge("clarify", END)
        return graph.compile()

    def _eden_router_node(self, state: EdenState) -> dict:
        context = state["context"]
        # Conversation continuity, not agent-guessing: if Insight or
        # Reporting already asked "which project?" and this is the reply,
        # keep going to whichever agent owns the open thread -- no LLM call
        # needed (a bare project name like "DEMO-Riverside" carries no
        # signal a classifier could use anyway).
        # ...but only when this message actually IS that reply. Continuity
        # used to be unconditional here: any open goal captured the next
        # GOAL_MAX_ATTEMPTS messages and routed them to its owning agent
        # with no check at all, so a brand-new question got swallowed as an
        # answer to a stale one and answered wrong (confirmed live: a goal
        # for "what are the leads we have" hijacked "i want to see the
        # payment details of customers"). That was the same hardcoded-
        # override failure mode the VARIANCE_SIGNALS removal fixed one
        # layer down, so it gets the same treatment -- the model reads the
        # message against the question (see _is_goal_reply).
        pending_goal = self._get_pending_goal(context)
        if pending_goal is not None:
            thread_agent = GOAL_KIND_AGENT.get(pending_goal.get("kind"))
            if thread_agent is not None:
                if self._is_goal_reply(context, pending_goal):
                    return {"next_agent": thread_agent}
                # A new request, not an answer. Drop the stale goal HERE, at
                # the single entry point, so every downstream consumer
                # (_run_navigation_cascade's pending_goal_before,
                # _dispatch_with_llm's combined-text retrieval, the agents'
                # intent_utterance) sees a clean slate and this turn is
                # classified on its own merits.
                self.conversation_memory.clear_pending_goal(
                    context.tenantId, context.userId
                )

        # Same continuity-over-classification reasoning as the pending-goal
        # check above: "what was the project before this one" is a question
        # about the conversation's OWN history, not a fresh request the
        # triage LLM should classify -- and only Navigation's recent-turns
        # walk (_resolve_history_recall) can actually answer it (Insight/
        # Reporting have no equivalent). Without this, triage classifies
        # this phrasing as a reporting/insight-shaped question (it mentions
        # "project") and routes it away from Navigation before that logic
        # ever runs -- confirmed live. Forcing navigation here, deterministically,
        # keeps the same low-false-positive marker+context regex Navigation's
        # own tier uses.
        #
        # Real bug, fixed: that reasoning only holds for a PURE recall
        # question. A COMPOUND one -- a recall reference plus a data question
        # in the same sentence -- was hijacked wholesale. Confirmed live
        # (2026-08-05 06:37:30): "what is the previous project cost " matched
        # both recall regexes, never reached triage at all, and Navigation
        # answered the half it owns -- naming the earlier project and opening
        # its page -- while "cost" was never read by anything and Reporting,
        # which owns cost, was never consulted. Reporting answers this fine
        # when it gets the chance (21s earlier, same session: "The estimated
        # cost of the project is $38,924.32").
        #
        # So: only short-circuit when there is no data question in the
        # sentence. When there IS one, resolve which project "previous" refers
        # to from the recent-turns buffer and bind it into the utterance
        # explicitly, then let triage classify what is actually being asked.
        # Binding by NAME (not by seeding last-discussed) is deliberate --
        # entity resolution ranks an explicit name above page context, so this
        # stays correct even while the user sits on a DIFFERENT project's page,
        # which is exactly the case that made the bug visible.
        utterance_lower_for_recall = context.utterance.lower()
        recall_override: SessionContext | None = None

        def _routed(agent: str) -> dict:
            """next_agent plus, when the utterance was rewritten above, the
            updated context so the specialist node sees the bound project.

            Real bug, fixed: this used to redirect any "navigation"
            resolution to Reporting on mobile ("there's no page to send
            them to"), unconditionally -- including greetings, guidance
            questions, and feature-lookup answers that Navigation handles
            without ever needing a page. Reporting has no small-talk/
            feature-lookup handling of its own, so those utterances fell
            all the way through respond() to status_response's no-project-
            context branch, which unconditionally lists every project via
            projects_disambiguation. Confirmed live: "hi" and "where's the
            upload button" on mobile both answered with a "Which project
            would you like to know about? Showing N of M..." listing
            instead of a real answer.

            Now every platform runs the SAME agent selection -- Navigation
            still resolves routes, entities, and disambiguation exactly as
            on web, so the reasoning stays identical across platforms.
            Mobile's "no page to send them to" constraint is handled one
            layer down instead: `_strip_route_fields_for_mobile` is the
            ONLY place platform branches the response shape (see its own
            docstring) -- Navigation's response construction itself has no
            platform branches of its own, and route-suppression is never
            achieved by swapping which specialist answers the question."""
            out: dict = {"next_agent": agent}
            if recall_override is not None:
                out["context"] = recall_override
            return out

        if (
            (_RECALL_MARKER_RE.search(utterance_lower_for_recall)
                and _RECALL_CONTEXT_RE.search(utterance_lower_for_recall))
            or _fuzzy_recall_signal(utterance_lower_for_recall)
        ):
            if not _DATA_QUESTION_RE.search(utterance_lower_for_recall):
                return _routed("navigation")
            previous_name = self._previous_distinct_project_name(context)
            if previous_name is None:
                # Nothing to bind -- fall back to the previous behaviour so
                # Navigation's own _resolve_history_recall still produces its
                # "I don't have an earlier project in our recent conversation"
                # message rather than Insight/Reporting asking "which project?"
                logger.info(
                    "compound recall: %r has a data question but no earlier "
                    "project in recent turns -- leaving it with navigation",
                    context.utterance,
                )
                return _routed("navigation")
            bound = f'{context.utterance.strip()} (referring to the project "{previous_name}")'
            recall_override = context.model_copy(update={"utterance": bound})
            context = recall_override
            logger.info(
                "compound recall: %r is recall + data question -- bound to "
                "earlier project %r and sent to triage",
                utterance_lower_for_recall, previous_name,
            )

        # Primary mechanism: a real LLM triage decision, on EVERY request
        # (explicit design decision -- the top-level "which specialist"
        # choice is an open-ended understanding problem, so no keyword list
        # decides it anymore; that was hardcoded routing wearing an AI
        # label, and it silently failed on any phrasing it wasn't written
        # for, e.g. "I think we're spending too much on the Oakwood job").
        triage = self.llm.triage(
            context.utterance, page_context=self._triage_page_context(context)
        )
        if triage is not None:
            agent = triage.get("agent")
            if agent in AGENT_NODE_REGISTRY:
                confidence = triage.get("confidence")
                logger.info(
                    "eden_router triage: agent=%r confidence=%r (%s)",
                    agent, confidence, triage.get("reasoning"),
                )
                if agent == "reporting" and any(
                    s in context.utterance.lower() for s in _INTERNAL_COST_BREAKDOWN_SIGNALS
                ):
                    logger.info(
                        "eden_router: correcting reporting -> insight for "
                        "internal cost-breakdown phrasing (reporting can "
                        "never answer this field)"
                    )
                    agent = "insight"
                if agent in ("insight", "reporting"):
                    agent = self._route_insight_or_reporting(agent, confidence, context)
                return _routed(agent)

        # Degraded fallback ONLY (triage call failed -- model unreachable
        # or malformed output): the old keyword check keeps obviously
        # variance-shaped requests routable while the LLM path is down,
        # same pattern as Navigation's own fuzzy-keyword fallback. No
        # confidence signal exists on this path, so no clarify branch here.
        utterance_lower = context.utterance.lower()
        if any(signal in utterance_lower for signal in VARIANCE_SIGNALS):
            agent = "reporting" if self.role_resolver.is_customer(context.tenantId, context.userId) else "insight"
            return _routed(agent)
        return _routed("navigation")

    def _previous_distinct_project_name(self, context: SessionContext) -> str | None:
        """The project discussed BEFORE the current one, from the recent-turns
        buffer -- what "the previous project" refers to.

        Same walk as _resolve_history_recall (state-and-memory Tier 2): newest
        first, collapsing consecutive turns about the same project so "show me
        X" then "what's its status" counts as one project, not a switch.
        distinct[0] is the current project, distinct[1] the one asked about.
        Returns None when fewer than two distinct projects exist, so callers
        can leave the turn with Navigation's own "no earlier project" message
        instead of guessing.

        Deliberately does NOT reuse _last_discussed_project: that remembers
        only the single latest project and would resolve "previous" to the
        CURRENT one -- answering confidently about the wrong project, which is
        worse than not answering."""
        try:
            recent = self.conversation_memory.get_recent_turns(
                context.tenantId, context.userId
            )
        except Exception as e:  # noqa: BLE001 -- memory is best-effort (Redis may be down)
            logger.warning("compound recall: recent-turns lookup failed: %s", e)
            return None
        distinct: list[str] = []
        for turn in reversed(recent or []):
            name = turn.get("project")
            if not name or (distinct and distinct[-1] == name):
                continue
            distinct.append(name)
            if len(distinct) == 2:
                return distinct[1]
        return None

    def _triage_page_context(self, context: SessionContext) -> str | None:
        """The page the user is looking at, formatted for triage as a
        tie-breaker (see LLMClient.triage's docstring for the live bug this
        closes). Registry-driven -- the label and page_feature names come
        from route_registry.json, never a hardcoded page list here.

        Returns None whenever the page can't be resolved (no currentRoute, a
        route not in the registry, or any unexpected error): triage is the
        single hop every request passes through, so this must degrade to the
        previous no-context behavior rather than raise, same rule as every
        other best-effort enrichment in this file."""
        if not context.currentRoute:
            return None
        try:
            nav = self.registry["navigation"]
            route = nav.route_by_pattern(context.currentRoute)
            if route is None:
                return None
            # route_by_pattern is a longest-prefix match, and `home`'s pattern
            # is "/" -- so ANY unrecognized path (a page not in the registry
            # yet, a typo'd currentRoute) prefix-matches Home and would inject
            # "Page: Home / Login button" as if that were the user's real
            # screen. Wrong page context is worse than none for a tie-breaker,
            # so only trust a root match when the user is genuinely at root.
            if route.pattern == "/" and context.currentRoute.rstrip("/") != "":
                return None
            features = [f.name for f in route.page_features if f.name]
            # A route with no page_features carries no tie-breaking signal
            # beyond its label -- still worth sending: the label alone
            # distinguishes "the customer list" from "one project's page".
            return TRIAGE_PAGE_CONTEXT_TEMPLATE.format(
                label=route.label_template,
                features=", ".join(features) if features else "(none listed)",
            )
        except Exception as e:  # noqa: BLE001 -- must never break routing
            logger.warning(
                "triage page context unavailable for currentRoute=%r: %s",
                context.currentRoute, e,
            )
            return None

    def _route_insight_or_reporting(self, agent: str, confidence: float | None, context: SessionContext) -> str:
        """Decides the final destination for a triage result of insight or
        reporting -- role gate FIRST, then (only for internal users, where
        insight-vs-reporting is a real fork) the confidence-gated clarify
        check. Order matters: a customer can never reach Insight regardless
        of confidence, so asking them "internal cost or your own bill" would
        offer a choice they don't actually have -- that ambiguity is only
        real for an internal user.

        proposal.md's role table: the customer role has NO Insight access --
        internal financials (committed spend, outstanding invoices) must
        never reach a customer. Decided HERE, as part of the router's own
        routing decision, not as a redirect buried inside Insight's node
        body -- the graph's conditional edge is the one place "which agent
        handles this" is ever decided, not partly here and partly inside an
        agent's code. Fail-closed -- unknown/missing roles land on the safe
        (reporting) side."""
        if self.role_resolver.is_customer(context.tenantId, context.userId):
            if agent == "insight":
                logger.info(
                    "eden_router role gate: downgrading insight -> reporting "
                    "(tenant=%s user=%s)", context.tenantId, context.userId,
                )
            return "reporting"

        # Internal user: insight vs. reporting is a genuine fork worth
        # asking about when the model itself wasn't confident -- mirrors
        # Navigation's own low-confidence -> disambiguate pattern
        # (ROUTE_DISAMBIGUATION_CONFIDENCE_THRESHOLD). "clarify" is a real
        # AGENT_NODE_REGISTRY entry (see eden_state.py), not a sentinel --
        # the caller just assigns whatever this returns straight to
        # next_agent, same as any other agent name.
        if confidence is not None and confidence < TRIAGE_CLARIFY_CONFIDENCE_THRESHOLD:
            logger.info(
                "eden_router: triage confidence %.2f below %.2f for agent=%r -- "
                "asking instead of guessing",
                confidence, TRIAGE_CLARIFY_CONFIDENCE_THRESHOLD, agent,
            )
            return "clarify"
        return agent

    def _navigation_node(self, state: EdenState) -> dict:
        return {"response": self._run_navigation_cascade(state["context"])}

    def _triage_clarify_node(self, state: EdenState) -> dict:
        """Low-confidence insight/reporting triage lands here instead of a
        silent guess -- a genuine graph node (not a special case), so
        AGENT_NODE_REGISTRY stays the single source of truth for every
        routing outcome.

        Reached ONLY for internal users: _route_insight_or_reporting sends a
        customer straight to reporting (they have no Insight access, so the
        fork isn't real for them) and only an internal user with a genuinely
        ambiguous cost question ever gets here. The question is therefore
        worded for that audience -- internal cost/budget (Insight) vs. the
        customer-facing billing view (Reporting). Real bug, fixed: it used
        to ask about "your own bill or what you owe," customer-facing
        language that never fit the internal user who is the only one ever
        shown this."""
        context = state["context"]
        response = NavigationResponse(
            type="disambiguation",
            question=(
                "Just to make sure I answer the right thing — do you mean this "
                "project's internal cost and budget, or its customer billing "
                "(invoices and payments)?"
            ),
        )
        return {"response": self._finalize_turn(context, response)}

    def _insight_node(self, state: EdenState) -> dict:
        # The customer/internal role gate is decided in eden_router (see
        # _apply_role_gate) -- by the time a request reaches this node, it's
        # already guaranteed to be from an internal role. This node no
        # longer makes that decision itself.
        context = state["context"]
        nav = self.registry["navigation"]
        # Snapshot before this turn -- same pre-tier pattern as the
        # navigation cascade, so a reply continuing an open "which project?"
        # goal is distinguished from an unrelated fresh question.
        pending_goal_before = self._get_pending_goal(context)

        if pending_goal_before is not None and pending_goal_before.get("kind") == "insight_explicit_switch":
            response = self._answer_explicit_project_switch(context, pending_goal_before, "insight")
            # Real bug, fixed: calling _finalize_turn with no resolved/
            # goal_kind here (unlike the normal flow a few lines below,
            # which always computes both after its own variance_response()
            # call) meant a "guidance" answer from this redispatch never
            # closed the OLD insight_explicit_switch goal -- it just sat
            # open in Redis -- and an unresolved fallback/disambiguation
            # from this SAME redispatch opened a brand-new, UNTAGGED goal
            # keyed on context.utterance, which at this point is the raw
            # reply to "did you mean X or Y" (e.g. literally "yes"), not a
            # real question. The next turn then had no goal_kind to route
            # on and fell through to out_of_scope. Confirmed live (nonprod,
            # tenant tn88351775, 2026-08-18) -- see known_issues.json's
            # goal_tracking_broken_on_project_switch_confirmation entry.
            resolved = response is not None and response.type == "guidance"
            goal_kind = "project_lookup" if (response is not None and response.type == "disambiguation") else None
            return {
                "response": self._finalize_turn(
                    context, response, pending_goal_before, False,
                    resolved=resolved, goal_kind=goal_kind,
                )
            }

        entity_name, entity_id, goal_continued, early = self._resolve_entity_with_context_switch(
            context, nav, pending_goal_before, "insight"
        )
        if early is not None:
            switch_response, extra_goal_fields = early
            return {
                "response": self._finalize_turn(
                    context, switch_response, pending_goal_before, False,
                    goal_kind="insight_context_switch", extra_goal_fields=extra_goal_fields,
                )
            }
        # Real bug, fixed: same class as the reporting node's own
        # intent_utterance fix a few lines above -- a reply continuing an
        # open "which project?" thread (context.utterance is just the
        # project name/selection) carries none of the ORIGINAL question's
        # signal words (e.g. "project cost"), so variance_response's
        # internal question-type checks (_is_total_cost_question etc.) saw
        # the bare reply and fell through to the generic variance view
        # instead of e.g. the total-cost answer. Confirmed live (fieldcraftmind,
        # 2026-08-31): "what is the project cost" -> "which project?" ->
        # project reply -> answered with generic committed/outstanding
        # variance data instead of estimatedCost. Use the goal's remembered
        # original utterance instead when continuing a thread.
        intent_utterance = (
            pending_goal_before["original_utterance"]
            if goal_continued and pending_goal_before.get("original_utterance")
            else context.utterance
        )
        allowed_project_ids = self._scoped_project_ids(context)
        response = self.registry["insight"].variance_response(
            context, entity_name, allowed_project_ids=allowed_project_ids, entity_id=entity_id,
            intent_utterance=intent_utterance,
        )
        # A real numeric answer is Insight's "guidance" type -- the only
        # outcome that should close the goal (see _update_goal_state's
        # resolved param; type alone can't distinguish this from a neutral
        # guidance turn elsewhere in the system).
        resolved = response is not None and response.type == "guidance"
        if resolved:
            # Cross-topic continuity: a later question to EITHER Insight or
            # Reporting that names no project and isn't on that project's
            # page falls back to this (see _resolve_insight_entity) --
            # otherwise switching topic ("is it delayed too?") without
            # repeating the project name re-asks "which project?" even
            # though the user just said it.
            #
            # Real bug, fixed: this used to only fire when `entity_name`
            # was set -- but the far more common flow (viewing a project's
            # page, asking a question with no name in it) resolves via
            # `entity_id` instead, leaving "last discussed project" stale
            # forever and starving _maybe_confirm_project_context_switch
            # of anything to compare against. response.project (now set by
            # every Insight/Reporting guidance answer) carries the
            # resolved name regardless of which path found it.
            #
            # Real bug, fixed: this used to prefer the raw spoken
            # `entity_name` (whatever casing the user typed, or the LLM
            # extraction returned) over the canonical resolved name --
            # `response.project.name` is always the actual Mongo
            # `projectName` value once a match resolved, strictly more
            # correct, so it should win whenever available. The mismatch
            # surfaced live: "buma p" (lowercase, as typed) got remembered
            # as "last discussed project" instead of "Buma P", so the
            # project-context-switch confirmation question quoted the
            # wrong casing back at the user.
            resolved_name = (response.project.name if response.project else None) or entity_name
            if resolved_name:
                self.conversation_memory.set_last_project(context.tenantId, context.userId, resolved_name)
            if response.project and response.project.id:
                self.conversation_memory.set_last_project_id(context.tenantId, context.userId, response.project.id)

            # Per explicit user direction: an explicit name/order-number
            # that resolved to a DIFFERENT project than the page always
            # gets confirmed, no judgment call -- see
            # _maybe_confirm_explicit_project_switch. Checked here, after
            # resolution, not before: the mismatch itself is what proves a
            # name was given (page-context fallback always matches the
            # page's own project).
            switch = self._maybe_confirm_explicit_project_switch(context, response, "insight")
            if switch is not None:
                switch_response, extra_goal_fields = switch
                return {
                    "response": self._finalize_turn(
                        context, switch_response, pending_goal_before, False,
                        goal_kind="insight_explicit_switch", extra_goal_fields=extra_goal_fields,
                    )
                }
        if resolved:
            self._attach_page_suggestion(
                response, "job_ledger", nav,
                tenant_id=context.tenantId, entity_name=entity_name, entity_id=entity_id,
                context=context,
            )
        # Only Insight's OWN disambiguation (list-projects / ambiguous-name)
        # tags the goal as project_lookup -- the generic nav deflection
        # below stays an untagged goal, same as any other unrelated fallback.
        goal_kind = "project_lookup" if (response is not None and response.type == "disambiguation") else None
        if response is None:
            # No-regression fallback: identical to today's pre-Insight
            # behavior for this same phrasing (nav's own data-question
            # deflection), rather than erroring or going silent.
            response = nav.data_question_response(context)
        return {
            "response": self._finalize_turn(
                context, response, pending_goal_before, goal_continued,
                resolved=resolved, goal_kind=goal_kind,
            )
        }

    def _reporting_node(self, state: EdenState) -> dict:
        # Mirrors _insight_node's shape exactly, minus the role gate
        # (Reporting's answer is customer-safe by query shape -- see
        # ReportingAgent's docstring -- so every role may use it).
        context = state["context"]
        nav = self.registry["navigation"]
        pending_goal_before = self._get_pending_goal(context)

        if pending_goal_before is not None and pending_goal_before.get("kind") == "reporting_explicit_switch":
            response = self._answer_explicit_project_switch(context, pending_goal_before, "reporting")
            # Real bug, fixed: same gap as _insight_node's mirror branch
            # above -- see that comment and
            # known_issues.json's goal_tracking_broken_on_project_switch_confirmation.
            resolved = response is not None and response.type == "guidance"
            goal_kind = "reporting_lookup" if (response is not None and response.type == "disambiguation") else None
            return {
                "response": self._finalize_turn(
                    context, response, pending_goal_before, False,
                    resolved=resolved, goal_kind=goal_kind,
                )
            }

        entity_name, entity_id, goal_continued, early = self._resolve_entity_with_context_switch(
            context, nav, pending_goal_before, "reporting"
        )
        if early is not None:
            switch_response, extra_goal_fields = early
            return {
                "response": self._finalize_turn(
                    context, switch_response, pending_goal_before, False,
                    goal_kind="reporting_context_switch", extra_goal_fields=extra_goal_fields,
                )
            }

        # Real bug, fixed: a reply continuing an open "which project?" thread
        # (context.utterance is just "DEMO-Riverside", the answer) carries
        # NONE of the ORIGINAL question's billing-vs-status signal -- using
        # it to decide billing vs. status silently answered a different
        # question than the one actually asked ("how much do I owe" got a
        # schedule-status answer instead, with no invoice suggestion,
        # because the bare project-name reply matches no BILLING_SIGNALS
        # word). Use the goal's remembered original utterance instead when
        # continuing a thread.
        #
        # Same bug, found again for the project-context-switch confirmation
        # (see _maybe_confirm_project_context_switch): a reply to "you were
        # asking about Buma P, now on Peter Parker's page -- continue with
        # Buma P, or check Peter Parker?" is answering WHICH PROJECT, not
        # asking a new question -- "yes", "no", and a re-typed project name
        # ("parker") all carry none of the original "why no invoice?"
        # signal either, for the exact same reason. goal_continued is
        # deliberately False for this goal kind (see
        # _resolve_entity_with_context_switch -- it's not a "which project?"
        # thread being extended), so it's checked separately here.
        answering_context_switch = (
            pending_goal_before is not None
            and pending_goal_before.get("kind") == "reporting_context_switch"
        )
        intent_utterance = (
            pending_goal_before["original_utterance"]
            if (goal_continued or answering_context_switch) and pending_goal_before.get("original_utterance")
            else context.utterance
        )

        allowed_project_ids = self._scoped_project_ids(context)
        response = self.registry["reporting"].respond(
            context, entity_name, allowed_project_ids=allowed_project_ids,
            intent_utterance=intent_utterance, entity_id=entity_id,
        )
        resolved = response is not None and response.type == "guidance"
        if resolved:
            # See _insight_node's matching comment: response.project (now
            # set by every Reporting guidance answer) carries the resolved
            # project name even when entity_name is None (page-context
            # resolution via entity_id) -- without this, "last discussed
            # project" never updated for that common flow. Canonical
            # response.project.name wins over the raw spoken entity_name
            # (same casing-mismatch fix as _insight_node).
            resolved_name = (response.project.name if response.project else None) or entity_name
            if resolved_name:
                self.conversation_memory.set_last_project(context.tenantId, context.userId, resolved_name)
            if response.project and response.project.id:
                self.conversation_memory.set_last_project_id(context.tenantId, context.userId, response.project.id)

            # Per explicit user direction: same "always confirm an explicit
            # different-project name" rule as _insight_node -- see
            # _maybe_confirm_explicit_project_switch.
            switch = self._maybe_confirm_explicit_project_switch(context, response, "reporting")
            if switch is not None:
                switch_response, extra_goal_fields = switch
                return {
                    "response": self._finalize_turn(
                        context, switch_response, pending_goal_before, False,
                        goal_kind="reporting_explicit_switch", extra_goal_fields=extra_goal_fields,
                    )
                }
        if resolved:
            # Same signal Reporting itself used to pick billing vs. status
            # (see ReportingAgent.respond) -- reused here ONLY to pick which
            # page to suggest, never to re-decide the answer that was
            # already given. Same intent_utterance fix applies here.
            is_billing = any(s in intent_utterance.lower() for s in BILLING_SIGNALS)
            # Billing -> this project's Invoice tab when the project is known
            # (falls back to the flat Invoices page otherwise -- see
            # _attach_page_suggestion). Status -> the Customers & Projects
            # list (customers_projects, flat) rather than nothing, so a status
            # answer also gets a follow-up.
            self._attach_page_suggestion(
                response, "invoice_list" if is_billing else "customers_projects", nav,
                tenant_id=context.tenantId, entity_name=entity_name, entity_id=entity_id,
                context=context,
            )
        goal_kind = "reporting_lookup" if (response is not None and response.type == "disambiguation") else None
        if response is None:
            response = nav.data_question_response(context)
        return {
            "response": self._finalize_turn(
                context, response, pending_goal_before, goal_continued,
                resolved=resolved, goal_kind=goal_kind,
            )
        }

    def _attach_page_suggestion(
        self, response: NavigationResponse, route_id: str | None, nav,
        tenant_id: str | None = None, entity_name: str | None = None,
        entity_id: str | None = None, context: SessionContext | None = None,
    ) -> None:
        """Front-desk follow-up: after a real answer, offer the relevant
        page both as a clickable suggestion chip AND as a natural-language
        offer in the message, so the conversation continues like a helper
        instead of dead-ending on the bare answer.

        Real bug, fixed: this used to always suggest the flat, all-projects
        invoice_list page for a billing answer, even when the answer was
        clearly about ONE specific project (e.g. "Peter Parker has an
        outstanding invoice of $14,800") -- landing the user on a page with
        none of the project context (header, tabs) the answer was just
        about. When `tenant_id` and an already-resolved `entity_name`/
        `entity_id` are given, this now tries the project-scoped route
        (aliased to project_information's own tab -- see
        NavigationAgent.ENTITY_TAB_ALIAS_ROUTE_STATE) via
        nav.resolve_project_route_option, which resolves from the project
        ALREADY identified by the caller rather than re-parsing the
        utterance. Falls back to the flat `route_id` (unscoped) whenever
        that isn't possible -- a generic-but-working suggestion beats none.

        `invoice_list` maps to the DIFFERENT id project_invoice_view (the
        flat page and its project-scoped counterpart are separate routes).
        `job_ledger` resolves to itself -- it's entity_required now (its own
        standalone page is unconditionally broken with no project id, see
        ENTITY_TAB_ALIAS_ROUTE_STATE's comment). If resolution fails here
        (no project known), the fallback nav.route_option("job_ledger")
        below still can't reach the broken flat page either -- being
        entity_required with no context, it safely redirects to the
        projects list instead (see NavigationAgent._route_option).

        No-op if route_id is None or the route can't be resolved -- a
        missing suggestion is a smaller failure than a broken one."""
        if route_id is None or response.suggestion is not None:
            return
        opt = None
        entity_target_route_id = {
            "invoice_list": "project_invoice_view",
            "job_ledger": "job_ledger",
            # Real bug, fixed: a status/general answer about ONE specific
            # project (e.g. "Peter Parker is a Roof Enhancement project...")
            # always suggested the flat, all-projects sold_production list
            # ("Would you like to see the Projects page?") even when the
            # project was already known -- landing the user on a page with
            # none of the context (header, tabs) the answer was just about,
            # and confirmed live to feel like a bug ("i said yes, it should
            # navigate inside the view page, not the Projects list").
            # Resolve to that project's own detail page instead, same
            # pattern as invoice_list/job_ledger above.
            "customers_projects": "project_information",
        }.get(route_id)
        if entity_target_route_id and tenant_id and (entity_name or entity_id):
            opt = nav.resolve_project_route_option(
                entity_target_route_id, tenant_id,
                entity_name=entity_name, entity_id=entity_id, context=context,
            )
        if opt is None:
            opt = nav.route_option(route_id)
        if opt is None:
            return
        # Real bug, fixed: this used to offer the follow-up unconditionally,
        # even when the user was ALREADY on the exact page (and exact
        # project) being suggested -- confirmed live: asking the start date
        # while already on Peter Parker's Project Information page got "...
        # Would you like to view the Peter Parker -- Project Information
        # page?" appended, which reads as broken when you're looking right
        # at it. Base-pattern match on the route (same idea as
        # NavigationAgent._is_already_on_page) plus a project-id match via
        # opt.routeState (see _entity_route_state -- always carries
        # "projectId" for an entity-scoped opt) covers both the flat-page
        # and project-scoped-page cases.
        if context is not None and context.currentRoute:
            route_base = opt.route.split("?")[0]
            same_page = context.currentRoute.startswith(route_base) or route_base.startswith(context.currentRoute)
            if same_page:
                opt_project_id = (opt.routeState or {}).get("projectId")
                same_project = (
                    opt_project_id is None
                    or (
                        isinstance(context.currentEntity, dict)
                        and context.currentEntity.get("type") == "project"
                        and str(context.currentEntity.get("id")) == str(opt_project_id)
                    )
                )
                if same_project:
                    return
        response.suggestion = NavigationSuggestion(
            label=opt.label, route=opt.route, routeState=opt.routeState
        )
        # The conversational "would you like to..." that makes the answer
        # feel like a continuing conversation rather than a dead end.
        # Appended to the message (single source of truth; reads naturally,
        # and when voice rewrite is on it gets phrased conversationally --
        # the chip stays the reliable clickable action even if voice drops
        # the sentence). No numbers here, so the fact-preservation guard in
        # rephrase() never trips on it.
        if response.message:
            response.message = (
                response.message.rstrip() + f" Would you like to see the {opt.label} page?"
            )

    def _resolve_insight_entity(
        self, context: SessionContext, nav, goal_continued: bool = False,
        extraction: dict | None = _EXTRACTION_NOT_GIVEN,
    ) -> tuple[str | None, str | None]:
        """Which project is this Insight/Reporting request about? Returns
        (entity_name, entity_id) -- at most one is non-None. entity_id is an
        exact projectId from PAGE CONTEXT (the user is viewing this
        project's page); the agents resolve it by exact id, which is exact
        and immune to the duplicate-name problem in real data. entity_name
        is a free-text reference resolved by name/order-number/fuzzy match.

        Resolution order:
        1. The LLM reads the sentence (extract_project_name) -- an explicit
           name/order-number in the MESSAGE wins over everything, since the
           user named something specific this turn.
        2. If the message names nothing and this is a reply continuing an
           open "which project?" question (goal_continued), the reply itself
           IS the answer -- use it as the name (a bare "maple" that the LLM
           judged too ambiguous to extract from is still the answer to a
           question we just asked).
        3. Otherwise fall to PAGE CONTEXT -- prefer the current page's
           project ID (exact) over its name.
        4. Finally, the last project discussed this conversation
           (cross-topic continuity).
        A FAILED extraction call (model unreachable) degrades to the old
        stop-word extraction, which itself falls back to page context/last
        project the same way.

        `extraction`: pass an already-fetched extract_project_name() result
        (e.g. from _resolve_entity_with_context_switch, which needs the same
        call for its own context-switch check) to avoid a second, redundant
        LLM round-trip for the same utterance. Omit to fetch it here as
        before."""
        offered_order_match = _OFFERED_OPTION_ORDER_NO_RE.search(context.utterance or "")
        if offered_order_match:
            return (offered_order_match.group(1), None)
        if extraction is _EXTRACTION_NOT_GIVEN:
            extraction = self.llm.extract_project_name(context.utterance)
        if extraction is not None:
            name = extraction.get("project_name")
            if name:
                return (name, None)
            # Data-grounded recovery: the LLM returned no name, but it may
            # have wrongly dropped one that's really there (reproducibly so
            # for "Peter Parker" -- model treats it as fictional, see
            # EntityResolver.find_named_project_in_text). If the utterance
            # LITERALLY contains a real project name from this user's own
            # scoped data, that's ground truth and outranks page/last-project
            # context, exactly as an LLM-extracted name (#1) would. Scoped to
            # the caller's own projects so it can never recover another
            # customer's project name.
            recovered = nav.entity_resolver.find_named_project_in_text(
                context.utterance, context.tenantId, self._scoped_project_ids(context)
            )
            if not recovered:
                # Real bug, fixed: confirmed live -- "what is the project
                # cost for 682053" (a bare order number, no "order"/"#"
                # prefix) got a null extraction despite the prompt
                # explicitly covering this case; the real project resolves
                # instantly by direct Mongo lookup, so this is a small-
                # model instruction-following miss, not a data gap. Same
                # ground-truth-recovery shape as the name check just above,
                # for the ORDER NUMBER half of extraction instead of the
                # name half.
                recovered = nav.entity_resolver.find_ordered_project_in_text(
                    context.utterance, context.tenantId, self._scoped_project_ids(context)
                )
            if recovered:
                return (recovered, None)
            if goal_continued:
                return (context.utterance.strip() or self._last_discussed_project(context), None)
            page_id = self._current_project_id(context)
            if page_id:
                return (None, page_id)
            page_name = nav.current_project_name(context)
            if page_name:
                return (page_name, None)
            return self._last_discussed_project_exact(context)
        if goal_continued:
            return (context.utterance.strip() or self._last_discussed_project(context), None)
        page_id = self._current_project_id(context)
        if page_id:
            return (None, page_id)
        page_name = nav.extract_entity_name(context)
        if page_name:
            return (page_name, None)
        return self._last_discussed_project_exact(context)

    def _current_project_id(self, context: SessionContext) -> str | None:
        """The projectId of the page the user is currently on, if any -- read
        from currentEntity (set by the frontend for project-scoped routes).
        Exact identifier, unlike the name, so it resolves cleanly even when
        many projects share a name."""
        entity = context.currentEntity
        if isinstance(entity, dict) and entity.get("type") == "project":
            pid = entity.get("id")
            return str(pid) if pid else None
        return None

    def _maybe_confirm_project_context_switch(
        self, context: SessionContext, nav, switch_kind: str, extraction: dict | None
    ) -> tuple[NavigationResponse, dict] | None:
        """Real gap, closed: navigating to a different project's page
        mid-conversation used to silently switch which project subsequent
        questions answered -- _resolve_insight_entity's page-context
        fallback picks up the NEW page with no signal that anything
        changed, so a follow-up like "what about the schedule?" quietly
        answers about a different project than the one the user thinks
        they're still discussing. Ask once instead of guessing.

        Returns None (proceed with normal resolution) whenever: this
        message names a project itself (that always wins outright, same
        priority order as _resolve_insight_entity -- nothing ambiguous to
        ask about), there's no page-context project to compare against,
        nothing was discussed yet this conversation, or the two already
        match. Only fires on a genuinely fresh question -- callers must
        only invoke this when no goal is already open (see
        _resolve_entity_with_context_switch).

        `extraction`: the caller's own already-fetched extract_project_name()
        result for this utterance -- reused here instead of calling the LLM
        a second time for the same sentence.

        Real bug, fixed: this used to fall back to the crude deterministic
        stop-word extractor (nav.extract_entity_name) whenever the LLM
        merely SAID there was no name, not just when the LLM call itself
        failed -- inconsistent with _resolve_insight_entity's own
        established rule (deterministic fallback is for a FAILED call
        only, never a second-guess of a working one) and a real whack-a-
        mole: any question word missing from ENTITY_STOP_WORDS ("how i
        paid" -> "how" survived stripping) got mistaken for a named
        project, silently skipping this whole check. Trust a successful
        LLM "no name" answer the same way _resolve_insight_entity already
        does."""
        if extraction is not None:
            named = extraction.get("project_name")
        else:
            named = nav.extract_entity_name(context, fallback_to_page_context=False)
        if named:
            logger.info(
                "project context switch: skipped -- utterance names %r explicitly", named,
            )
            return None

        page_id = self._current_project_id(context)
        if not page_id:
            logger.info(
                "project context switch: skipped -- no page project id in "
                "context.currentEntity (currentRoute=%r, currentEntity=%r)",
                context.currentRoute, context.currentEntity,
            )
            return None
        # Real bug, fixed: the frontend's currentEntity carries an id OR a
        # name, never both (deriveCurrentEntity prefers the id whenever
        # one is available, which is nearly always -- see
        # useSessionContext.ts) -- so nav.current_project_name(context)
        # (which only reads currentEntity.get("name")) returned None on
        # almost every real page visit, and this whole check silently
        # never fired. Resolve the page's project name from its id via
        # the entity resolver instead of depending on the frontend to
        # supply a name it structurally doesn't send.
        page_matches = nav.entity_resolver.resolve_by_id(page_id, context.tenantId)
        page_name = page_matches[0].project_name if page_matches else None
        if not page_name:
            logger.info(
                "project context switch: skipped -- page project id %r did not "
                "resolve to a project (tenant=%r)", page_id, context.tenantId,
            )
            return None

        last_name = self._last_discussed_project(context)
        if not last_name:
            logger.info(
                "project context switch: skipped -- no last-discussed project "
                "recorded (page project is %r)", page_name,
            )
            return None
        if last_name.strip().lower() == page_name.strip().lower():
            logger.info(
                "project context switch: skipped -- last discussed %r matches "
                "page project, no mismatch", page_name,
            )
            return None

        # Per explicit user direction: the LLM decides the WHOLE thing end
        # to end -- whether this mismatch is worth asking about at all, and
        # what to ask -- rather than a flat "always ask, fixed template"
        # rule. Everything above this point is still a plain data lookup
        # (which project was last discussed, which project owns this page,
        # do they match) -- not a judgment call, so it stays deterministic.
        # Only "is this worth interrupting the user about" is the model's
        # call now. Fails closed to NOT asking (silently proceed with the
        # page project) on any call failure -- same fail-open-to-the-
        # obvious-answer default as every other best-effort LLM call here.
        decision = self.llm.decide_project_context_switch(context.utterance, last_name, page_name)
        if not decision or not decision.get("ask"):
            logger.info(
                "project context switch: mismatch found (last=%r, page=%r) but "
                "LLM decided NOT to ask (decision=%r)", last_name, page_name, decision,
            )
            return None
        llm_question = decision.get("question")
        # PII-redaction leak guard: confirmed live -- guardrails' Presidio
        # input rail redacts last_name/page_name (real project names that
        # happen to contain a PERSON/LOCATION-shaped word, e.g. "Chennai" or
        # "steve") before the model ever sees them, and the model faithfully
        # echoes the placeholder back in its own generated question ("Are
        # you still asking about 'vijay cus, <LOCATION>-Roof
        # Enhancement-383724'..."). Same failure this client's rephrase()
        # already guards against (see PRESIDIO_PLACEHOLDER_RE), just via a
        # different call that doesn't go through rephrase() at all. Treat a
        # leaked placeholder the same as no question at all.
        if llm_question and PRESIDIO_PLACEHOLDER_RE.search(llm_question):
            logger.warning(
                "Project-context-switch question leaked a PII-redaction "
                "placeholder — using deterministic template instead: %.160r",
                llm_question,
            )
            llm_question = None
        question = llm_question or (
            f"You were asking about {last_name}, but you're now on "
            f"{page_name}'s page — continue with {last_name}, or "
            f"check {page_name} instead?"
        )

        logger.info(
            "project context switch: last discussed %r, now on %r's page — "
            "LLM decided to ask (%s)",
            last_name, page_name, decision.get("reasoning"),
        )
        response = NavigationResponse(type="disambiguation", question=question)
        extra_goal_fields = {
            "last_project_name": last_name,
            "page_project_id": page_id,
            "page_project_name": page_name,
        }
        return response, extra_goal_fields

    def _answer_project_context_switch(
        self, context: SessionContext, nav, goal: dict
    ) -> tuple[str | None, str | None]:
        """Resolves a reply to the "you were asking about X, now on Y"
        prompt (see _maybe_confirm_project_context_switch). Per explicit
        user direction, the LLM interprets the reply end to end ("last" /
        "page" / "other") rather than a bare yes/no keyword check --
        "stick with that one", "the one I'm on now", or a repeated project
        name all resolve correctly now, not just literal "yes"/"no". Falls
        back to resolving the CURRENT utterance fresh (same as an "other"
        choice) on any call failure -- it may itself name a different
        project entirely, and this never silently commits to the wrong
        one."""
        last_name = goal.get("last_project_name")
        page_name = goal.get("page_project_name")
        question = goal.get("last_question") or ""
        reply = self.llm.resolve_project_context_switch_reply(
            context.utterance, question, last_name, page_name
        )
        choice = (reply or {}).get("choice")
        if choice == "last":
            return last_name, None
        if choice == "page":
            return None, goal.get("page_project_id")
        return self._resolve_insight_entity(context, nav, goal_continued=False)

    def _maybe_confirm_explicit_project_switch(
        self, context: SessionContext, response: NavigationResponse,
        agent_kind: str, route_id: str | None = None,
    ) -> tuple[NavigationResponse, dict] | None:
        """Per explicit user direction: unlike
        _maybe_confirm_project_context_switch (a NAMELESS follow-up, where
        the LLM decides whether asking is even worth it), this fires
        whenever the CURRENT turn's response names a SPECIFIC project that
        differs from the one on the user's current page -- always, no
        judgment call. A mismatch here can only happen via an explicit
        name/order-number in the utterance: page-context fallback (the
        no-name path every agent already has) always resolves to the
        page's OWN project, never a different one -- so the mismatch alone
        proves a name was given, no separate check needed.

        `agent_kind`: which node produced `response`
        ("navigation"/"insight"/"reporting") -- determines how
        _answer_explicit_project_switch redoes the request against the
        page's project if the user picks "page" instead. `route_id`: the
        registry route id resolved (navigation only) -- needed to redo the
        SAME route against a different project id.

        Returns None (proceed with `response` unchanged) when there's no
        page project to compare against, or it already matches."""
        if response.project is None:
            return None
        page_id = self._current_project_id(context)
        if not page_id or page_id == response.project.id:
            return None
        nav = self.registry["navigation"]
        page_matches = nav.entity_resolver.resolve_by_id(page_id, context.tenantId)
        page_name = page_matches[0].project_name if page_matches else None
        if not page_name or page_name.strip().lower() == response.project.name.strip().lower():
            return None

        named_name = response.project.name
        question = (
            f"You're currently on {page_name}'s page — did you mean "
            f"{named_name}, or {page_name}?"
        )
        logger.info(
            "explicit project switch: message named %r, currently on %r's page "
            "(agent_kind=%s) — asking",
            named_name, page_name, agent_kind,
        )
        confirm_response = NavigationResponse(type="disambiguation", question=question)
        extra_goal_fields = {
            "agent_kind": agent_kind,
            "route_id": route_id,
            "named_project_id": response.project.id,
            "named_project_name": named_name,
            "page_project_id": page_id,
            "page_project_name": page_name,
            # Stashed so answering "named" needs no recomputation at all --
            # this IS the real, already-correct answer, just held back one
            # turn for confirmation.
            "named_response": response.model_dump(),
        }
        return confirm_response, extra_goal_fields

    def _answer_explicit_project_switch(
        self, context: SessionContext, goal: dict, agent_kind: str,
    ) -> NavigationResponse:
        """Resolves a reply to the "did you mean X or Y" prompt from
        _maybe_confirm_explicit_project_switch. Reuses the same LLM-driven
        reply interpretation as _answer_project_context_switch --
        "last"/"page"/"other" map onto "the named project"/"the page
        project"/neither here. "last" needs no recomputation: the FULL,
        already-computed response for the named project was stashed in the
        goal when the question was asked. "page" redoes the same request
        against the page's project id, dispatched differently per
        `agent_kind`. "other" falls through to a fresh dispatch of the
        current utterance -- same no-guessing principle as
        _answer_project_context_switch's own "other" case; it may itself
        name a third project entirely."""
        nav = self.registry["navigation"]
        named_name = goal.get("named_project_name")
        page_name = goal.get("page_project_name")
        question = goal.get("last_question") or ""
        reply = self.llm.resolve_project_context_switch_reply(
            context.utterance, question, named_name, page_name
        )
        choice = (reply or {}).get("choice")

        if choice == "last":
            return NavigationResponse.model_validate(goal["named_response"])

        if choice == "page":
            page_id = goal.get("page_project_id")
            if agent_kind == "navigation":
                route_id = goal.get("route_id")
                redone = (
                    nav.resolve_entity_route_for_project(route_id, context, page_id)
                    if route_id else None
                )
                if redone is not None:
                    return redone
                # No route captured, or it no longer resolves (rare) -- the
                # project's own info page is a safe, never-a-dead-end
                # fallback rather than repeating the same confirmation.
                fallback = nav.resolve_entity_route_for_project(
                    "project_information", context, page_id
                )
                return fallback if fallback is not None else nav.no_match(context)
            if agent_kind == "insight":
                allowed_project_ids = self._scoped_project_ids(context)
                response = self.registry["insight"].variance_response(
                    context, None, allowed_project_ids=allowed_project_ids, entity_id=page_id
                )
                return response if response is not None else nav.data_question_response(context)
            if agent_kind == "reporting":
                allowed_project_ids = self._scoped_project_ids(context)
                response = self.registry["reporting"].respond(
                    context, None, allowed_project_ids=allowed_project_ids,
                    intent_utterance=context.utterance, entity_id=page_id,
                )
                return response if response is not None else nav.data_question_response(context)
            return nav.no_match(context)

        # "other": resolve the CURRENT utterance fresh, same no-guessing
        # fallback as _answer_project_context_switch's own "other" case.
        if agent_kind == "navigation":
            # Real bug, caught before it shipped: _run_navigation_cascade
            # re-checks the pending goal at its own top -- calling it here
            # while THIS goal is still open would immediately re-enter this
            # exact answer-handling branch again, infinitely. Clear it
            # first; this utterance is being treated as a brand-new
            # request, not a continuation of the switch confirmation.
            self.conversation_memory.clear_pending_goal(context.tenantId, context.userId)
            return self._run_navigation_cascade(context)
        allowed_project_ids = self._scoped_project_ids(context)
        entity_name, entity_id = self._resolve_insight_entity(context, nav, goal_continued=False)
        if agent_kind == "insight":
            response = self.registry["insight"].variance_response(
                context, entity_name, allowed_project_ids=allowed_project_ids, entity_id=entity_id
            )
        else:
            response = self.registry["reporting"].respond(
                context, entity_name, allowed_project_ids=allowed_project_ids,
                intent_utterance=context.utterance, entity_id=entity_id,
            )
        return response if response is not None else nav.data_question_response(context)

    def _answer_page_topic_offer(
        self, context: SessionContext, goal: dict
    ) -> NavigationResponse:
        """Resolves a reply to EDEN's own proactive "You're on X. Would you
        like to know about the Y?" offer (see
        NavigationAgent._already_here_response / _pick_offerable_feature).
        A clear decline closes the loop politely. Anything else -- "yes", or
        the user naming the topic, or really anything at all -- is read as
        "yes, tell me": there's only one topic on the table (the one just
        offered), so no re-classification is needed. Dispatched straight to
        whichever Reporting answer function the topic's own keyword profile
        already mapped to (date/billing/status -- see NavigationAgent.
        _topic_kind), reusing that function's already-built answer+highlight
        logic end to end rather than re-deriving anything here. The project
        is resolved the same way every other page-aware follow-up in this
        file is: the page currently open, falling back to whatever was last
        discussed -- exactly right here, since the offer was made ABOUT the
        page the user is looking at."""
        nav = self.registry["navigation"]
        # A plain decline ("no", "nope", "not now") -- _is_negation is the
        # lightweight, side-effect-free check (just tokens vs. NEGATION_WORDS);
        # _resolve_negation is the heavier "offer alternative pages" handler
        # built for rejecting a NAVIGATE/disambiguation offer, wrong shape
        # for a plain data-topic decline.
        if self._is_negation(context.utterance.lower()):
            return NavigationResponse(
                type="guidance",
                message="No problem — let me know if you need anything else.",
            )
        reporting = self.registry["reporting"]
        allowed_project_ids = self._scoped_project_ids(context)
        entity_id = self._current_project_id(context)
        entity_name = None if entity_id else self._last_discussed_project(context)
        kind = goal.get("topic_kind", "status")
        if kind == "date":
            response = reporting.date_response(
                context, entity_name, allowed_project_ids=allowed_project_ids, entity_id=entity_id
            )
        elif kind == "billing":
            response = reporting.billing_response(
                context, entity_name, allowed_project_ids=allowed_project_ids, entity_id=entity_id
            )
        else:
            response = reporting.status_response(
                context, entity_name, allowed_project_ids=allowed_project_ids, entity_id=entity_id
            )
        return response if response is not None else nav.data_question_response(context)

    def _resolve_entity_with_context_switch(
        self, context: SessionContext, nav, pending_goal_before: dict | None, agent_kind: str
    ) -> tuple[str | None, str | None, bool, tuple[NavigationResponse, dict] | None]:
        """Shared by _insight_node/_reporting_node: wraps
        _resolve_insight_entity with the project-context-switch check.
        Returns (entity_name, entity_id, goal_continued, early_result) --
        when early_result is not None, the caller must return it directly
        (via _finalize_turn) instead of calling its agent's respond()."""
        switch_kind = f"{agent_kind}_context_switch"
        lookup_kind = "project_lookup" if agent_kind == "insight" else "reporting_lookup"

        if pending_goal_before is not None and pending_goal_before.get("kind") == switch_kind:
            entity_name, entity_id = self._answer_project_context_switch(context, nav, pending_goal_before)
            return entity_name, entity_id, False, None

        goal_continued = (
            pending_goal_before is not None and pending_goal_before.get("kind") == lookup_kind
        )
        # Only check for a context switch on a genuinely fresh question --
        # a reply continuing some OTHER open goal (e.g. "which project?")
        # is answering that question, not starting a new one to compare
        # against page context.
        if pending_goal_before is None:
            # Fetched once, reused by both the switch-check and the normal
            # resolution below (when no switch fires) -- avoids paying for
            # the same extract_project_name call twice on one utterance.
            extraction = self.llm.extract_project_name(context.utterance)
            switch = self._maybe_confirm_project_context_switch(context, nav, switch_kind, extraction)
            if switch is not None:
                return None, None, False, switch
            entity_name, entity_id = self._resolve_insight_entity(
                context, nav, goal_continued, extraction=extraction
            )
            return entity_name, entity_id, goal_continued, None

        entity_name, entity_id = self._resolve_insight_entity(context, nav, goal_continued)
        return entity_name, entity_id, goal_continued, None

    def _last_discussed_project(self, context: SessionContext) -> str | None:
        return self.conversation_memory.get_last_project(context.tenantId, context.userId)

    def _last_discussed_project_id(self, context: SessionContext) -> str | None:
        """Exact projectId companion to _last_discussed_project -- prefer
        this whenever available (see ConversationMemory.get_last_project_id's
        docstring for why the name alone is ambiguous in real data)."""
        return self.conversation_memory.get_last_project_id(context.tenantId, context.userId)

    def _last_discussed_project_exact(self, context: SessionContext) -> tuple[str | None, str | None]:
        """(entity_name, entity_id) pair for the final continuity fallback --
        prefers the exact remembered project ID over the ambiguous
        remembered name, e.g. after "272000" uniquely resolved a project by
        order number, a later question naming nothing should still resolve
        to THAT exact project, not re-trigger "which Peter Parker?" against
        every same-named project. Falls back to the name only when no ID
        was ever captured (e.g. Redis was down when it would have been
        set)."""
        last_id = self._last_discussed_project_id(context)
        if last_id:
            return (None, last_id)
        return (self._last_discussed_project(context), None)

    def dispatch(self, context: SessionContext) -> NavigationResponse:
        result = self._graph.invoke({"context": context})
        return result["response"]

    def _run_navigation_cascade(self, context: SessionContext) -> NavigationResponse:
        """The full 6-tier dispatch cascade (greeting -> affirmation ->
        negation -> exact-keyword -> feature-lookup -> LLM-default), plus
        the goal-state update, optional voice rewrite, audit log, and
        conversation-memory append that follow it. Extracted verbatim from
        dispatch() so it can run as a single LangGraph node body without
        changing any of its tuned behavior.

        Page-orientation "guidance" questions ("what can I do here") and
        data questions ("what's the margin on this job") no longer have
        their own pre-LLM keyword tiers -- they fall through to the
        LLM-default branch, where LLMClient.understand()'s own
        intent="guidance"/intent="data_question" classification (see
        _dispatch_with_llm) handles them end to end."""
        utterance_lower = context.utterance.lower()
        nav = self.registry["navigation"]
        # Set by the LLM branch when the model was unreachable — the voice
        # pass below must not fire another doomed LLM call on top of the
        # timeout(s) already paid (that stacking is exactly the 90s worst
        # case llm_client's max_retries=0 comment warns about).
        llm_unreachable = False
        # Set by tiers that manage the pending goal themselves (negation).
        goal_handled = False
        # Snapshot BEFORE any tier runs: several deterministic tiers
        # (exact-keyword, feature-lookup, data-question) are blind to
        # whether a clarification is already in progress — they only look
        # at the utterance/current page, never the goal. If one of them
        # fires while a goal is open and produces an unresolved response
        # (fallback/disambiguation), that is NOT the user answering the
        # pending question — it's an unrelated hit — so it must start a
        # FRESH goal, not silently overwrite the real one's last_question
        # with unrelated text (see _update_goal_state's goal_continued
        # param). Only the LLM refinement path (which was actually given
        # this goal to reason over) and an affirmation re-asking the same
        # clarification count as genuinely continuing it.
        pending_goal_before = self._get_pending_goal(context)
        goal_continued = False

        if pending_goal_before is not None and pending_goal_before.get("kind") == "navigation_explicit_switch":
            # Reply to "did you mean X or Y" (see
            # _maybe_confirm_explicit_project_switch) -- answer it directly,
            # bypassing every deterministic tier below (a bare "yes"/project
            # name carries no signal any of them could use anyway, same
            # reasoning as the goal kinds in GOAL_KIND_AGENT).
            response = self._answer_explicit_project_switch(context, pending_goal_before, "navigation")
            return self._finalize_turn(context, response, pending_goal_before, False)

        if pending_goal_before is not None and pending_goal_before.get("kind") == "page_topic_offer":
            # Reply to EDEN's own proactive "You're on X. Would you like to
            # know about the Y?" (see NavigationAgent._already_here_response)
            # -- bypasses every deterministic tier below for the same reason
            # as navigation_explicit_switch above: "yes"/a bare topic name
            # carries no retrievable signal of its own.
            response = self._answer_page_topic_offer(context, pending_goal_before)
            # resolved=True: this goal is always a one-shot reply (decline or
            # answer, never "still ambiguous, ask again") -- response.type is
            # "guidance" here (Reporting's real answer functions), which
            # _update_goal_state would otherwise treat as a neutral no-op and
            # leave the pending goal dangling for the NEXT, unrelated turn.
            return self._finalize_turn(context, response, pending_goal_before, False, resolved=True)

        # Set only by tiers that can resolve an entity-required route (exact-
        # keyword or LLM-driven navigate) -- the registry route id, so a
        # later explicit-project-switch confirmation (see
        # _maybe_confirm_explicit_project_switch) can redo the SAME route
        # against a different project id if the user picks "page" instead.
        route_id = None

        if self._is_greeting(utterance_lower):
            response = self._greeting_response(context.platform)
        elif (confirmed := self._resolve_affirmation(utterance_lower, context)) is not None:
            # "yes" / "sure, do it" right after EDEN offered a page ("Did you
            # mean Projects?") accepts that offer. Must be deterministic: the
            # word "yes" carries no retrievable signal, so the LLM path gets
            # unrelated candidates and (observed live) invents a route id the
            # registry doesn't have, dead-ending the confirmation in no_match.
            response = confirmed
            if pending_goal_before is not None and response.type == "disambiguation":
                goal_continued = True  # bare "yes" re-asking the same clarification
        elif (rejected := self._resolve_negation(utterance_lower, context)) is not None:
            # "no" / "not that one" right after EDEN navigated or offered a
            # page rejects it: mark the offer rejected in the pending goal
            # and immediately re-offer the next-best alternatives as
            # clickable cards. Deterministic for the same reason as the
            # affirmation tier — a bare "no" carries no retrievable signal.
            # This tier writes its own goal state (it knows the ORIGINAL
            # request; the generic updater would wrongly adopt "no not that
            # one" as the goal), so the dispatch-end update is skipped.
            response = rejected
            goal_handled = True
        elif (recalled := self._resolve_history_recall(utterance_lower, context)) is not None:
            # Meta-question about the conversation itself ("what was the
            # project before this one") -- deterministic for the same reason
            # as affirmation/negation: it references the turn HISTORY, not
            # the current utterance's own content, so retrieval/the LLM have
            # nothing in the message itself to go on and would invent an
            # answer. Must run before exact-keyword/feature-lookup since
            # "project"/"before" phrasing could otherwise false-match a
            # page's own keyword vocabulary.
            response = recalled
        elif (went_back := self._resolve_go_back(utterance_lower, context)) is not None:
            # "go back" / "take me back to the previous page" -- same
            # reasoning as history-recall just above: references turn
            # HISTORY, not the current utterance's content, so retrieval/
            # the LLM have nothing to go on (observed live: dead-ended in a
            # generic fallback goal instead of replaying the last page).
            # Must run before exact-keyword/feature-lookup for the same
            # reason recall does — "back"/"page" phrasing could otherwise
            # false-match a page's own keyword vocabulary.
            response = went_back
        elif (exact_route := nav.exact_keyword_route(utterance_lower, context)) is not None:
            # High-precision navigation tier: only claims utterances the
            # route's own vocabulary can FULLY explain (see
            # exact_keyword_route). Instant, free, and precise — but not
            # greedy: any unexplained content falls through. Running this
            # BEFORE feature lookup replaces the old fuzzy-score arbitration
            # between the two tiers ("feature wins only if strictly higher")
            # — clean navigation commands ("start a bid wizard", "upload
            # image", "show me the customers list") are fully explained and
            # claimed here, so they can no longer be stolen by a shorter
            # feature phrase, which is what that scoring rule existed to
            # prevent.
            # See _DIRECT_NAV_PROJECT_SCOPED_ALIAS's docstring: prefer the
            # project-scoped sibling, resolved against the exact page-context
            # project id (bypassing name extraction entirely, same as
            # _answer_explicit_project_switch), whenever one exists and the
            # user is actually on that kind of page right now.
            scoped_response = None
            scoped_route_id = _DIRECT_NAV_PROJECT_SCOPED_ALIAS.get(exact_route.id)
            if (
                scoped_route_id
                and isinstance(context.currentEntity, dict)
                and context.currentEntity.get("type") == "project"
                and context.currentEntity.get("id")
            ):
                scoped_response = nav.resolve_entity_route_for_project(
                    scoped_route_id, context, entity_id=str(context.currentEntity["id"]),
                )
            if scoped_response is not None:
                response = scoped_response
                route_id = scoped_route_id
            else:
                response = nav.finalize_keyword_route(
                    exact_route, context,
                    last_discussed_project=self._last_discussed_project(context),
                )
                route_id = exact_route.id
        elif (feature_response := nav.try_feature_lookup(context)) is not None:
            # Feature-spotlight tier: button/tab location questions
            # ("where's the edit vendor button") match a page_feature's
            # keyword phrase in full — also high precision, and it produces
            # highlight/locate data the LLM path can't. Must stay
            # deterministic: sending these to the LLM loses the spotlight
            # and (observed live) classifies the same phrase differently
            # depending on conversation history.
            response = feature_response
        else:
            # Default path (LLM-first architecture): semantic retrieval
            # narrows candidates, the LangGraph+Agents SDK backend
            # classifies with conversation history and verifies entities
            # via tools. Fuzzy keyword matching survives only as the
            # degraded fallback inside _dispatch_with_llm for when the LLM
            # is unreachable.
            response, llm_unreachable, route_id = self._dispatch_with_llm(context)
            if route_id == _OUT_OF_SCOPE_ROUTE_SENTINEL:
                # A closing acknowledgment, not an open question -- never
                # let it open/advance a pending goal (see the sentinel's
                # origin comment in _dispatch_with_llm for the bug this
                # closes). Also never treat it as continuing whatever goal
                # WAS open before this turn: an off-topic message doesn't
                # answer "which project did you mean," so the old goal must
                # stay untouched (still answerable by a later real reply),
                # not silently closed or overwritten either.
                route_id = None
                goal_handled = True
            elif pending_goal_before is not None:
                goal_continued = True  # the LLM was given this goal and reasoned over it

        # NavigationAgent._already_here_response just proactively offered a
        # page's data-bearing feature ("Would you like to know about the
        # balance?") -- open a matching goal manually (writes its own goal
        # state, same as the negation tier above) so the NEXT turn ("yes")
        # is answered by _answer_page_topic_offer instead of falling through
        # to an unrelated fresh dispatch. Bypasses _update_goal_state
        # entirely (via goal_handled) since that helper only auto-opens
        # goals for "disambiguation"/"fallback" types, not "guidance".
        if not goal_handled and response.topicOffer is not None:
            self.conversation_memory.set_pending_goal(
                context.tenantId, context.userId,
                {
                    "original_utterance": context.utterance,
                    "attempts": 0,
                    "rejected_routes": [],
                    "kind": "page_topic_offer",
                    "topic_kind": response.topicOffer.get("kind", "status"),
                },
            )
            goal_handled = True

        # Per explicit user direction: naming a project/order-number that
        # differs from the one on the user's current page is ALWAYS worth
        # confirming (no LLM judgment call, unlike the nameless-follow-up
        # case in _maybe_confirm_project_context_switch) -- see
        # _maybe_confirm_explicit_project_switch. Only checked when a real,
        # single project was actually resolved (response.project set);
        # skipped entirely for greeting/affirmation/negation/feature-lookup
        # responses, which never set `project`.
        if not goal_handled and response.project is not None:
            switch = self._maybe_confirm_explicit_project_switch(
                context, response, "navigation", route_id
            )
            if switch is not None:
                switch_response, extra_goal_fields = switch
                return self._finalize_turn(
                    context, switch_response, pending_goal_before, False,
                    goal_kind="navigation_explicit_switch", extra_goal_fields=extra_goal_fields,
                )

        return self._finalize_turn(
            context, response, pending_goal_before, goal_continued, goal_handled, llm_unreachable,
            goal_kind="navigation_clarify",
        )

    def _enforce_role_scope(
        self, context: SessionContext, response: NavigationResponse
    ) -> NavigationResponse:
        """Final, centralized, fail-closed role/permission guardrail --
        defense in depth. EVERY response passes through _finalize_turn, which
        calls this first, so no code path (present OR future) can ever put a
        page the user lacks permission for in front of them, even if an
        individual response-builder forgets to filter. This is not a
        replacement for the per-site filtering (that gives better UX -- a
        denied route can suggest an accessible counterpart); it's the
        backstop that makes a forgotten filter a non-event instead of a data
        leak. Twice this session a builder shipped unfiltered (disambiguation
        options, out-of-scope suggestion cards) -- this ensures the third
        one, whenever it's written, can't leak.

        Scrubs the three surfaces a response can carry a route:
          1. navigate target  -> forbidden becomes a plain deflection
          2. clickable options -> forbidden ones dropped; if that empties a
             disambiguation, it becomes a deflection (a question with no
             answerable option is worse than an honest "can't help there")
          3. suggestion chip   -> forbidden one dropped

        Every strip is logged at WARNING: a strip means an upstream builder
        leaked something it shouldn't have -- a real signal to fix that site,
        not something to swallow silently."""
        nav = self.registry["navigation"]

        if response.type == "navigate" and not nav.is_route_pattern_permitted(
            response.route, context, response.routeState
        ):
            logger.warning(
                "role-scope guardrail: blocked forbidden navigate route=%r "
                "tenant=%s user=%s -- deflecting (an upstream builder skipped "
                "its permission check)", response.route, context.tenantId, context.userId,
            )
            return NavigationResponse(
                type="guidance",
                message=(
                    "That page isn't available with your current access. "
                    "Let me know what you're trying to do and I'll point you "
                    "to something you can reach."
                ),
            )

        if response.options:
            allowed = [
                o for o in response.options
                if nav.is_route_pattern_permitted(o.route, context, o.routeState)
            ]
            if len(allowed) != len(response.options):
                logger.warning(
                    "role-scope guardrail: stripped %d/%d forbidden option(s) "
                    "tenant=%s user=%s", len(response.options) - len(allowed),
                    len(response.options), context.tenantId, context.userId,
                )
            if not allowed:
                # Every option was forbidden -- a disambiguation with nothing
                # the user can pick is a dead end; deflect honestly instead.
                return NavigationResponse(
                    type="guidance",
                    message=(
                        "I couldn't find a page you have access to for that. "
                        "Tell me a bit more about what you need and I'll help."
                    ),
                )
            response.options = allowed

        if response.suggestion and not nav.is_route_pattern_permitted(
            response.suggestion.route, context, response.suggestion.routeState
        ):
            logger.warning(
                "role-scope guardrail: stripped forbidden suggestion route=%r "
                "tenant=%s user=%s", response.suggestion.route,
                context.tenantId, context.userId,
            )
            # Real bug, fixed: confirmed live -- nulling response.suggestion
            # alone left the natural-language sentence _attach_page_suggestion
            # bakes into response.message ("...Would you like to see the
            # Invoices page?") dangling in the text, promising an action the
            # UI no longer offers (no chip, and the user still can't reach
            # that page if they say yes). Strip that exact appended clause
            # too -- it's a deterministic, known template (see
            # _attach_page_suggestion's own construction), not a guess.
            if response.message:
                response.message = re.sub(
                    r"\s*Would you like to see the .+ page\?\s*$",
                    "", response.message,
                ).rstrip()
            response.suggestion = None

        return response

    def _strip_route_fields_for_mobile(
        self, context: SessionContext, response: NavigationResponse
    ) -> NavigationResponse:
        """Mobile has no page to navigate to, so no route-shaped data should
        ever reach it. This is now the ONLY place platform branches the
        response shape -- every specialist runs identically on mobile and
        web (_eden_router_node picks the same agent either way; see
        _routed()'s docstring), so this single, shared choke point is what
        turns a "navigate"-shaped answer into a route-less, informationally-
        phrased one for mobile, instead of a router-level swap to a
        different specialist. Applies to EVERY navigate-shaped response
        regardless of which agent produced it -- Navigation's own route
        resolution, a goal-continuation path
        (_answer_explicit_project_switch, _answer_page_topic_offer), or
        this file's own recall/go-back responses. Same reasoning as
        _enforce_role_scope's three-surfaces coverage -- NavigationOption.route
        and NavigationSuggestion.route are both required (non-nullable)
        fields, so those two are dropped outright rather than nulled.

        Real gap, fixed: fields-only stripping left `type: "navigate"`
        standing with every route field null -- every OTHER "navigate"
        response in the contract carries a non-null route, so a mobile
        client that assumes that invariant has no defined behavior for this
        shape (could crash or silently no-op). `type` now becomes
        "guidance" here too, matching every other no-page-attached answer
        in this codebase (RAG rejections, margin refusals, etc.).

        Real gap, fixed (2026-09-03): the message text itself still claimed
        an action mobile never performs ("Taking you to Invoices.", "Back to
        Invoices.") even after the route was nulled -- confirmed live this
        read as though the app had navigated when nothing happened.
        _mobile_navigate_text rewrites the small, closed set of action
        phrasings these construction sites use into informational text
        BEFORE the type/route fields below are nulled."""
        if context.platform != "mobile":
            return response
        if response.type == "navigate" and response.message:
            response.message = _mobile_navigate_text(response.message)
        response.type = "guidance"
        response.route = None
        response.routeState = None
        response.icon = None
        if response.options:
            # Real bug, fixed: a specialist's question/message text (e.g.
            # insight_agent.projects_disambiguation's "Showing N of M --
            # if you don't see it here, tell me the order number.") is
            # written assuming its `options` list travels alongside it.
            # Nulling options outright left that promise dangling with no
            # list attached at all on mobile -- looked to the caller like
            # the backend returned nothing, even though it answered with a
            # real 200. NavigationOption.route is non-nullable so entries
            # can't be salvaged field-by-field the way `message`/`icon`
            # are; fold the plain labels into whichever text field is
            # already carrying the prompt so the information survives as
            # text instead of silently vanishing.
            names = [o.label for o in response.options if o.label]
            if names:
                listing = "\n".join(f"- {n}" for n in names)
                if response.question:
                    response.question = f"{response.question}\n{listing}"
                elif response.message:
                    response.message = f"{response.message}\n{listing}"
                else:
                    response.message = listing
                # PR review (Raja): the folded names are real data (project
                # names, order numbers) with no fact-preservation guard on
                # the name axis -- rephrase()'s only check (_number_tokens
                # in llm_client.py) verifies numbers survive, not names, the
                # exact gap NavigationResponse.skipVoiceRewrite's own
                # docstring calls out. Skip the rewrite entirely rather than
                # risk the voice-rewrite model reordering/dropping/renaming
                # entries the same way already logged elsewhere in this
                # file (e.g. presidio_redacts_project_names_in_entity_extraction).
                response.skipVoiceRewrite = True
            response.options = None
        if response.suggestion:
            # Real bug, fixed (caught live, not by inspection): nulling
            # response.suggestion alone leaves _attach_page_suggestion's own
            # appended sentence ("...Would you like to see the Invoices
            # page?") dangling in the text -- promising a chip that no
            # longer exists. Same exact template/fix as _enforce_role_scope
            # already uses for the identical leak.
            if response.message:
                response.message = re.sub(
                    r"\s*Would you like to see the .+ page\?\s*$",
                    "", response.message,
                ).rstrip()
            response.suggestion = None
        return response

    def _maybe_attach_notes_hint(
        self, context: SessionContext, response: NavigationResponse
    ) -> None:
        """Per explicit user direction: the first time a project comes up in
        a conversation, proactively point out the Notes panel for it -- but
        only once per project per session, not on every turn that mentions
        it (see ConversationMemory.has_shown_notes_hint/mark_notes_hint_shown).

        Fully data-driven, no hardcoded page/field list: looks up whatever
        route the user is currently on and asks the registry (via
        NavigationAgent.find_feature_by_name) whether THIS route happens to
        define a feature named something with "notes" in it -- works on any
        project-scoped page that already has one (Gantt, Project
        Information, Sales Estimate, ...) with nothing page-specific written
        here, and simply no-ops on a page that doesn't have one.

        Deliberately conservative about when it fires: only when a single
        project was actually resolved this turn (`response.project`) and the
        response doesn't already carry a highlight (never overwrite a more
        specific answer -- e.g. the start-date/cost field highlight from
        _resolve_affirmation's topic-feature match, or Insight's own
        topicOffer highlight -- with this lower-priority proactive tip)."""
        if response.project is None or response.highlight:
            return
        project_id = response.project.id
        if not project_id:
            return
        if self.conversation_memory.has_shown_notes_hint(
            context.tenantId, context.userId, project_id
        ):
            return
        nav = self.registry["navigation"]
        current_route = nav.route_by_pattern(context.currentRoute)
        if current_route is None:
            return
        feature = nav.find_feature_by_name(current_route, "notes")
        if feature is None:
            return
        tip = f"By the way, you can jot notes on this project here — {feature.hint}"
        response.insight = f"{response.insight} {tip}" if response.insight else tip
        response.highlight = feature.name
        response.highlightTargets = feature.locate or [feature.name]
        self.conversation_memory.mark_notes_hint_shown(
            context.tenantId, context.userId, project_id
        )

    def _finalize_turn(
        self,
        context: SessionContext,
        response: NavigationResponse,
        pending_goal_before: dict | None = None,
        goal_continued: bool = False,
        goal_handled: bool = False,
        llm_unreachable: bool = False,
        resolved: bool | None = None,
        goal_kind: str | None = None,
        extra_goal_fields: dict | None = None,
    ) -> NavigationResponse:
        """Shared tail for every agent's response: goal-state update, optional
        voice rewrite, audit log, conversation-memory append. Extracted
        verbatim from dispatch()'s original tail so any registered agent
        (not just Navigation) gets identical bookkeeping. Callers with no
        nav-specific tier state (e.g. Insight) can omit the goal/llm args —
        their response is then treated the same way an unrelated deterministic
        tier hit is: it may start a fresh goal if unresolved, same as today's
        established semantics for "blind" tiers (see _update_goal_state).

        `resolved`/`goal_kind` exist for agents whose "answered" response
        isn't `type == "navigate"` (e.g. Insight's `"guidance"` answers) — see
        _update_goal_state for how they're used. `extra_goal_fields` merges
        extra data into a freshly-opened goal (e.g.
        _maybe_confirm_project_context_switch's remembered project names/ids
        that a bare "yes"/"no" reply needs — see _answer_project_context_switch)."""
        # STRONG ROLE-SCOPE GUARDRAIL -- runs FIRST, before goal-state /
        # audit / memory, so a scrubbed response is what gets logged,
        # remembered, and (crucially) replayed by a later "yes". Every
        # response in the whole system funnels through _finalize_turn, so
        # this one gate makes it structurally impossible to surface a page
        # the user's permissions don't allow, regardless of which builder
        # produced the response or whether that builder remembered to filter.
        response = self._enforce_role_scope(context, response)
        # Conversation loop: a navigate closes the pending goal; an
        # unresolved turn (question asked / options offered) opens or
        # advances one (if goal_continued) or starts a fresh one (if not)
        # so the NEXT message is read as the user's answer.
        #
        # Real bug, fixed: confirmed live on mobile (2026-09-03) -- this used
        # to run AFTER _strip_route_fields_for_mobile, which unconditionally
        # rewrites response.type to "guidance" and nulls response.options
        # for EVERY mobile response. _update_goal_state's own logic keys off
        # exactly those two fields (response.type in
        # ("disambiguation", "fallback"), and response.options for the
        # "offered" list a later answer is resolved against) -- so on
        # mobile, a disambiguation could never open a pending goal at all,
        # and picking/re-sending one of the listed project labels had
        # nothing remembered to recognize it as the answer. It fell through
        # to a fresh, context-free triage instead and (confirmed live) got
        # misclassified as a navigation request to an unrelated page ("Sales
        # Estimates", "WIP Report") instead of answering about the project
        # actually named. Goal bookkeeping must see the ORIGINAL, unstripped
        # response -- the mobile transport rewrite is a presentation concern
        # for the OUTGOING response only, not something that should also
        # blind the conversation-loop's own state tracking.
        if not goal_handled:
            self._update_goal_state(
                context, response, pending_goal_before, goal_continued,
                resolved=resolved, goal_kind=goal_kind, extra_goal_fields=extra_goal_fields,
            )
        response = self._strip_route_fields_for_mobile(context, response)
        self._maybe_attach_notes_hint(context, response)

        if (
            self.voice_enabled and response.message and not llm_unreachable
            and not response.skipVoiceRewrite
        ):
            voiced = self.llm.rephrase(context.utterance, response.message)
            if isinstance(voiced, str) and voiced:
                response.message = voiced

        write_navigation_audit_log(
            tenant_id=context.tenantId,
            user_id=context.userId,
            utterance=context.utterance,
            resolved_route=response.route,
            confidence=response.confidence,
            status=response.type,
            platform=context.platform,
        )
        # For "navigate", route/label are on the response directly. For
        # "fallback"/"guidance" (feature lookups, disambiguation, no_match
        # suggestions), the actual page-of-interest lives in `suggestion` —
        # capturing only the top-level fields left every non-navigate turn
        # recorded as effectively empty ({"route": null, "label": null}),
        # which made follow-ups like "take me there again" unresolvable
        # regardless of how good the LLM's use of history was.
        remembered_route = response.route
        remembered_label = response.label
        remembered_route_state = response.routeState
        # `is_soft_suggestion` marks a "guidance" answer whose `.suggestion`
        # is an UNSOLICITED front-desk follow-up _attach_page_suggestion
        # tacks onto a resolved billing/status answer ("...Would you like to
        # see the Project Invoices page?") -- distinct from a "fallback"
        # response's suggestion, which the user was deliberately asked about
        # ("I couldn't find an exact match, did you mean Projects?"). Stored
        # for conversation history/analytics; no longer consulted by
        # _resolve_affirmation (see the comment there) -- a bare "yes" is
        # always unambiguous here because _attach_page_suggestion's tail is
        # always phrased as a direct yes/no question itself.
        is_soft_suggestion = response.type == "guidance" and response.route is None and response.suggestion is not None
        if remembered_route is None and response.suggestion is not None:
            remembered_route = response.suggestion.route
            remembered_label = response.suggestion.label
            remembered_route_state = response.suggestion.routeState
        # Real bug, fixed: response.project (the entity actually discussed —
        # e.g. "Peter Parker") is a different concept from `label` (the
        # PAGE, e.g. a status/date/billing answer's attached generic "see
        # the Projects page" suggestion) -- but was never captured for
        # conversation history at all, and confusingly `label` above already
        # gets overwritten by that generic page suggestion. Recorded as its
        # own field (see ConversationMemory.append_turn's matching comment)
        # so "Recent conversation" can tell the LLM who was actually being
        # discussed, distinct from what page was suggested. Without this, a
        # follow-up naming no project ("show me") had nothing but a
        # misleading page name to resolve against and re-asked "which
        # project?" despite last_project already being set correctly.
        remembered_project = response.project.name if response.project else None
        # Disambiguation turns have no single route/suggestion — remember the
        # offered options instead, so a follow-up ("yes", "the first one")
        # has referents to resolve against rather than an empty turn.
        remembered_options = None
        if response.options:
            remembered_options = [
                {"label": o.label, "route": o.route} for o in response.options
            ]

        self.conversation_memory.append_turn(
            tenant_id=context.tenantId,
            user_id=context.userId,
            utterance=context.utterance,
            response_type=response.type,
            route=remembered_route,
            label=remembered_label,
            options=remembered_options,
            project=remembered_project,
            response_payload=response.model_dump(),
            route_state=remembered_route_state,
            is_soft_suggestion=is_soft_suggestion,
        )
        return response

    def get_history(self, tenant_id: str, user_id: str, limit: int = 20) -> list[dict]:
        return self.conversation_memory.get_history(tenant_id, user_id, limit)

    def reset(self, tenant_id: str, user_id: str) -> None:
        """Clears server-side conversation state (pending goal, last
        project, recent turns) for this tenant/user -- see
        ConversationMemory.reset for why this exists."""
        self.conversation_memory.reset(tenant_id, user_id)

    def _dispatch_with_llm(
        self, context: SessionContext
    ) -> tuple[NavigationResponse, bool, str | None]:
        """Returns (response, llm_unreachable, route_id). The flag is True
        only when the model itself could not be reached and the response
        came from the degraded deterministic fallback — the caller uses it
        to skip the voice-rephrase pass, which would otherwise stack
        another timeout on an endpoint we just watched fail. `route_id` is
        the registry route id actually resolved (only meaningful for the
        "navigate" branches that can set an entity route's `.project`; None
        everywhere else) -- lets _run_navigation_cascade redo an
        entity-required resolution against a different project id later
        (see _maybe_confirm_explicit_project_switch)."""
        nav = self.registry["navigation"]

        # Vector pre-filter: narrow the full registry down to the routes
        # actually worth asking the LLM about. Sending everything (even
        # id+label only) either blew the model's context window or lost
        # enough signal to misclassify things like "take me to payroll" as a
        # real route — sending the top-K WITH full descriptions fixes both.
        # A low top score also means "nothing here fits," a more reliable
        # signal than trusting the model to self-report that from a
        # stripped-down list (see the "payroll"/"completed tasks" incidents).
        # Threshold comes from the agent itself (nav.candidate_threshold) —
        # semantic embeddings and TF-IDF char n-grams score on different
        # scales, so the agent knows which one applies, not this caller.
        #
        # Conversation loop: if a clarification is in progress, the current
        # message is (usually) an ANSWER to EDEN's question — a fragment
        # like "the dashboard one" retrieves nothing useful alone, so
        # retrieval runs over original request + reply combined, and pages
        # the user already rejected are dropped from the candidate set.
        pending_goal = self._get_pending_goal(context)
        retrieval_text = context.utterance
        if pending_goal:
            retrieval_text = f"{pending_goal['original_utterance']} {context.utterance}"
            logger.info(
                "conversation loop: clarification in progress (attempt %d) — "
                "retrieving against combined text %r, rejected=%r",
                pending_goal.get("attempts", 0), retrieval_text,
                pending_goal.get("rejected_routes") or [],
            )
        candidates = nav.top_candidates(retrieval_text)
        if pending_goal and pending_goal.get("rejected_routes"):
            rejected = set(pending_goal["rejected_routes"])
            candidates = [
                (r, s) for r, s in candidates if r.resolved_route() not in rejected
            ]
        recent_turns = self.conversation_memory.get_recent_turns(
            context.tenantId, context.userId
        )
        # Follow-ups referencing the previous turn ("take me to it", "no, the
        # other one") retrieve nothing useful from the utterance itself, so
        # the route history refers to is usually missing from the top-K — and
        # the LLM, instructed to resolve the referent, then has no valid id
        # to return and invents one (observed live: route_id='projects').
        # Injecting the history turns' routes gives it the real ids.
        if recent_turns:
            candidate_ids = {r.id for r, _ in candidates}
            injected: list[tuple] = []
            for turn in recent_turns:
                # A disambiguation turn's routes live in its options, not the
                # top-level route — inject those too, so "the first one" has
                # real ids to resolve against.
                patterns = [turn.get("route")] + [
                    o.get("route") for o in (turn.get("options") or [])
                ]
                for pattern in patterns:
                    hist_route = nav.route_by_pattern(pattern)
                    if hist_route is not None and hist_route.id not in candidate_ids:
                        injected.append((hist_route, 0.0))
                        candidate_ids.add(hist_route.id)
            # Real gap, fixed: the history injection above only remembers
            # the REMEMBERED ROUTE, which for a guidance answer (status/
            # date/billing/RAG) is a generic attached page suggestion (e.g.
            # "/customers-projects", the tenant-wide project list) rather than
            # the actual project discussed -- so a maximally vague
            # continuation ("show me" with no subject at all, right after
            # "what is the project status") retrieved unrelated noise and
            # never even had project_information as an option, no matter
            # how well the LLM's own prompt tells it to prefer a
            # project-scoped route (SYSTEM_PROMPT can't pick an id it was
            # never shown). If a project was established in Recent
            # conversation, always offer that project's own general detail
            # page as a candidate too -- the same safe default the prompt
            # asks the model to prefer for a subject-less continuation.
            if self._last_discussed_project(context) and "project_information" not in candidate_ids:
                project_info_route = next(
                    (r for r in nav.route_registry if r.id == "project_information"), None
                )
                if project_info_route is not None:
                    injected.append((project_info_route, 0.0))
                    candidate_ids.add(project_info_route.id)
            if injected:
                # Real bug, fixed: injected candidates used to be APPENDED
                # to the end of the list -- empirically confirmed live
                # (Qwen3-8B) that list POSITION, not just presence, decides
                # whether the model actually considers a candidate: the
                # identical candidate set, with project_information moved
                # from last to first, went from a reproducible 0/3 correct
                # answers to 3/3. Continuity-injected candidates (the exact
                # ones a follow-up utterance has the weakest retrieval
                # signal for, and therefore need the MOST help being
                # noticed) must lead the list, never trail it.
                candidates = injected + candidates
        # This gate used to also skip the LLM call outright whenever the top
        # retrieval score fell below candidate_threshold — meant to catch
        # "nothing here fits" cheaply, without paying for a round-trip the
        # model would likely waste. In practice it also caught genuine
        # requests that just didn't share vocabulary with any route's
        # indexed text (label + description only — page_features and
        # page_help aren't indexed): "where i should select the grade" on
        # the Materials page scored every route near zero (no route
        # description mentions "grade") and dead-ended in the generic
        # no-match message without the LLM ever seeing it. The LLM can
        # still reason from the top-K candidates' full descriptions even
        # when none of them scored well by character/vector overlap, so the
        # only thing still worth skipping the LLM for is having NO
        # candidates to show it at all (an empty registry state, not "low
        # score") — everything else now reaches the model and lets it (and
        # multi-step verification, if enabled) make the real call.
        if not candidates:
            logger.info("vector pre-filter: no candidates at all — skipping LLM call")
            return nav.no_match(context), False, None

        # Full decision trail, part 1: exactly what retrieval found and sent
        # to the model, and which backend produced it (tfidf char n-grams,
        # semantic sentence embeddings, or qdrant) — logged unconditionally
        # so "what were the top matches" and "are we using vectors" are
        # always answerable from the log, not just inferable from the .env.
        logger.info(
            "retrieval candidates (backend=%s, threshold=%.3f): %s",
            nav.retrieval_backend, nav.candidate_threshold,
            ", ".join(f"{r.id}={s:.3f}" for r, s in candidates),
        )

        # With a goal active, its clarification section already carries the
        # thread context, making the raw turn-by-turn history redundant —
        # drop it deliberately here (not a budget decision). Candidate
        # count is NOT pre-capped here anymore: LLMClient.understand()
        # measures the actual constructed prompt and trims adaptively to
        # fit the model's real context window (see its _fit_to_budget) —
        # a fixed upstream cap kept going stale as SYSTEM_PROMPT grew and
        # the retrieval backend changed, causing two separate live
        # context-overflow incidents.
        llm_history = None if pending_goal else recent_turns

        understanding = self.llm.understand(
            context.utterance,
            [r.model_dump() for r, _ in candidates],
            context.model_dump(),
            conversation_history=llm_history,
            pending_goal=pending_goal,
        )

        if understanding is None:
            # Degraded mode: the LLM is unreachable (tunnel down, model
            # crashed). Fuzzy keyword matching — demoted from the primary
            # dispatch — earns its keep here: a best-effort keyword answer
            # beats a blank no_match while the LLM path is offline.
            fallback_route, _ = nav.keyword_match_scored(context.utterance.lower(), context)
            if fallback_route is not None:
                logger.warning(
                    "LLM unavailable — degraded to fuzzy keyword fallback"
                )
                return nav.finalize_keyword_route(
                    fallback_route, context,
                    last_discussed_project=self._last_discussed_project(context),
                ), True, fallback_route.id
            logger.warning("LLM unavailable — falling back to deterministic no-match")
            return nav.no_match(context), True, None

        intent = understanding.get("intent")
        logger.info(
            "orchestrator triage intent=%r route_id=%r confidence=%r",
            intent, understanding.get("route_id"), understanding.get("confidence")
        )

        if intent == "data_question":
            # The prompt's contract says data_question is ONLY for computed
            # numbers, but the small model routinely tags plain view requests
            # ("show me the payments") with it anyway. When the model itself
            # picked the page where the answer lives, going there IS the
            # answer — navigate directly, keeping the model's own honest
            # insight ("costs live in the job ledger") as the caption, instead
            # of stopping at an "I can't crunch that number" bubble that just
            # offers the same page as a button. Only a data_question with no
            # route at all still gets the apologetic fallback.
            if understanding.get("route_id"):
                return nav.resolve_classified(
                    context, understanding,
                    last_discussed_project=self._last_discussed_project(context),
                ), False, understanding.get("route_id")
            return nav.data_question_response(context, understanding), False, None
        if intent == "guidance":
            return nav.guidance(context, understanding.get("insight")), False, None
        if intent == "navigate" and understanding.get("route_id"):
            disambiguation = self._maybe_disambiguate_route(understanding, candidates, nav, context)
            if disambiguation is not None:
                return disambiguation, False, None
            if MULTI_STEP_VERIFICATION_ENABLED:
                verified = self._maybe_verify_ambiguity(
                    context.utterance, understanding, candidates, nav, context
                )
                if verified is not None:
                    return verified, False, understanding.get("route_id")
            return nav.resolve_classified(
                context, understanding,
                last_discussed_project=self._last_discussed_project(context),
            ), False, understanding.get("route_id")
        if intent == "out_of_scope":
            # Real bug, fixed: this response is a CLOSING acknowledgment
            # ("I can't help with that, but here's what I can do"), not an
            # open question -- but its type is "fallback", the same type a
            # genuine unresolved navigation dead-end uses, and
            # _update_goal_state opens a pending goal for every "fallback"
            # response. That silently turned every off-topic/identity/
            # prompt-injection-probing message into a multi-turn "which
            # project did you mean?" clarification loop that then swallowed
            # the NEXT, totally unrelated message as if it were the answer
            # (observed live: "who are you" answered as a stale project
            # clarification). `route_id` is never real for this response
            # (out-of-scope never resolves a route) and is only ever
            # consumed downstream when `response.project` is set, which
            # out-of-scope responses never do -- safe to repurpose as the
            # sentinel the caller uses to skip goal-opening entirely via
            # `goal_handled`, rather than adding a new tuple element that
            # would require updating every other return in this method.
            return self._out_of_scope_response(understanding, candidates, nav, context), False, _OUT_OF_SCOPE_ROUTE_SENTINEL
        return nav.no_match(context), False, None

    def _maybe_disambiguate_route(
        self, understanding: dict, candidates: list, nav, context: SessionContext | None = None
    ) -> NavigationResponse | None:
        """When the LLM's own navigate confidence is low, that's the model
        telling us multiple pages are plausible ("take me to finance" ->
        Finance/Invoices/Job Ledger are all reasonable reads) — asking beats
        silently committing to a guess the user then has to notice is wrong
        and correct. High-precision hits (exact keyword tier, or an LLM call
        with a clear single winner) score 0.9+ and never reach here, so this
        only fires for genuinely close calls, not every navigate response.

        Alternates come from the same RAG candidates the LLM was already
        given — filtered to the retrieval threshold so a near-zero-score
        candidate (or an injected history-route stub) never becomes an
        option. Returns None (proceed as normal) when fewer than two real
        alternates survive that filter — there's nothing to ask about."""
        confidence = nav.clamp_confidence(understanding.get("confidence"))
        if confidence >= ROUTE_DISAMBIGUATION_CONFIDENCE_THRESHOLD:
            return None

        alt_ids = [understanding["route_id"]]
        for route, score in candidates:
            if score < nav.candidate_threshold:
                continue
            if route.id not in alt_ids:
                alt_ids.append(route.id)
            if len(alt_ids) >= 3:
                break

        response = self._disambiguation_response(alt_ids, nav, context)
        if response is not None:
            logger.info(
                "route disambiguation: confidence %.2f below threshold, offering %d options",
                confidence, len(response.options)
            )
        return response

    def _maybe_verify_ambiguity(
        self,
        utterance: str,
        understanding: dict,
        candidates: list,
        nav,
        context: SessionContext | None = None,
    ) -> NavigationResponse | None:
        """Multi-step reasoning trial (MULTI_STEP_VERIFICATION_ENABLED) — see
        module docstring constants. _maybe_disambiguate_route already catches
        the case where the LLM ITSELF reports low confidence. This catches
        the other case: retrieval found two candidates close enough to
        genuinely compete, but the LLM was confident anyway — small models
        are routinely overconfident (the "customer information" case: 0.301
        vs 0.276 in retrieval, yet confidence 0.95 from the primary call).
        Rather than trust that same self-reported number a second time, this
        spends one extra LLM call asking a differently-framed question with
        the retrieval evidence spelled out explicitly.

        Returns None (proceed with the primary answer unchanged) when
        retrieval itself has a clear front-runner, when the second pass
        disagrees that it's ambiguous, or on any call failure — this can
        only override a confident answer with a disambiguation prompt, it
        never downgrades or blocks a genuinely clear one.

        Real bug, fixed: this second-opinion call used to receive ONLY the
        bare utterance + candidates -- no conversation history, no current
        page. It had no way to know the primary's chosen route was the
        SAME one the previous turn already resolved (or that the user is
        literally already on that page), so a repeat/confirming follow-up
        ("take me to the customer page" right after "show customers"
        resolved to customer_table) got needlessly re-litigated into a
        disambiguation the conversation already answered. Checked FIRST,
        deterministically, before spending the extra LLM call: if the
        primary's chosen route matches the current page or the most
        recently resolved turn, that's conversational context settling the
        "ambiguity," not a case for a second opinion to re-open.
        """
        scored = [(r, s) for r, s in candidates if s >= nav.candidate_threshold]
        if len(scored) < 2:
            return None

        if context is not None and self._route_confirmed_by_context(
            understanding.get("route_id"), scored, nav, context
        ):
            logger.info(
                "multi-step verification: skipped — primary route already "
                "confirmed by current page or last-resolved turn"
            )
            return None

        top_score = scored[0][1]
        second_score = scored[1][1]
        if top_score <= 0:
            return None
        relative_gap = (top_score - second_score) / top_score
        if relative_gap >= NEAR_TIE_MARGIN_RATIO:
            # Full decision trail, part 2: log even the "nothing to see here"
            # case at debug level so the near-tie check's own verdict is
            # traceable, without adding INFO-level noise to every request
            # that reaches the LLM (most have a clear retrieval front-runner).
            logger.debug(
                "multi-step verification: retrieval gap %.1f%% >= %.0f%% threshold — "
                "clear front-runner, skipping second opinion",
                relative_gap * 100, NEAR_TIE_MARGIN_RATIO * 100,
            )
            return None

        primary_confidence = nav.clamp_confidence(understanding.get("confidence"))
        logger.info(
            "multi-step verification: near-tie detected (top=%.3f second=%.3f, gap=%.1f%%), "
            "primary confidence=%.2f — requesting second opinion",
            top_score, second_score, relative_gap * 100, primary_confidence,
        )

        verification = self.llm.verify_ambiguity(
            utterance,
            {
                "route_id": understanding.get("route_id"),
                "confidence": understanding.get("confidence"),
            },
            [
                {"id": r.id, "label": r.label_template, "description": r.description, "score": s}
                for r, s in scored[:3]
            ],
        )
        if not verification:
            logger.warning(
                "multi-step verification: second-opinion call failed — keeping primary answer"
            )
            return None
        if not verification.get("is_ambiguous"):
            logger.info(
                "multi-step verification: second pass agrees with primary — no override: %s",
                verification.get("reasoning"),
            )
            return None

        alt_ids = list(dict.fromkeys(verification.get("plausible_route_ids") or []))
        response = self._disambiguation_response(alt_ids, nav, context)
        if response is None:
            logger.warning(
                "multi-step verification: second pass said ambiguous but returned no "
                "usable route ids (%r) — keeping primary answer",
                verification.get("plausible_route_ids"),
            )
            return None

        logger.info(
            "multi-step verification: overriding confident answer (confidence %.2f) — "
            "retrieval near-tie (%.3f vs %.3f) confirmed ambiguous, offering %d options: %s",
            primary_confidence, top_score, second_score, len(response.options),
            verification.get("reasoning"),
        )
        return response

    def _route_confirmed_by_context(
        self, primary_route_id: str | None, scored: list, nav, context: SessionContext
    ) -> bool:
        """True when conversation context alone already settles which of the
        near-tied candidates is meant, independent of what the LLM said this
        turn:
        1. The primary's chosen route is the page the user is CURRENTLY on
           (context.currentRoute resolves to the same route), or
        2. It's the route the user's own last turn was just navigated to.

        Compared by LABEL, not by bare resolved route string -- routes that
        share a target shell page (customer_table and vendor_list both
        resolve to "/admin") would otherwise look identical by route alone.
        Label is the best signal actually available without the frontend
        reporting back which tab is selected (a separate, larger gap -- see
        docs/eden/AUTH_IMPLEMENTATION_PLAN.md-adjacent session notes)."""
        primary_route = next((r for r, _ in scored if r.id == primary_route_id), None)
        if primary_route is None:
            return False
        primary_label = primary_route.label_template

        current_route = nav.route_by_pattern(context.currentRoute)
        if current_route is not None and current_route.label_template == primary_label:
            return True

        recent_turns = self.conversation_memory.get_recent_turns(context.tenantId, context.userId)
        if recent_turns and recent_turns[-1].get("label") == primary_label:
            return True
        return False

    def _disambiguation_response(
        self, route_ids: list[str], nav, context: SessionContext | None = None
    ) -> NavigationResponse | None:
        """Shared by both the confidence-based and multi-step-verification
        disambiguation paths — builds the same NavigationResponse shape from
        a list of candidate route ids, capped to 3 clickable options (a
        user picking between more than that stops being a quick choice).
        Returns None when fewer than two of the ids resolve to a real
        route — there's nothing to ask about.

        `context` (optional) lets an entity-required candidate resolve to
        the project actually named in the request (see
        NavigationAgent._resolve_entity_option) instead of unconditionally
        collapsing to the bare projects list."""
        # Real bug, fixed: these options were built from raw retrieval
        # candidates with NO permission filter -- so a customer could be
        # offered a clickable option for a page they cannot access (observed
        # live: "Invoice Form" offered to a customer who lacks INV.REA).
        # Every option presented MUST be a page this role can actually
        # reach, exactly as the frontend only renders permitted pages. Skip
        # filtering only when context is absent (no identity to check
        # against) -- every real request path supplies it.
        if context is not None:
            route_ids = nav.filter_permitted_route_ids(route_ids, context)
        # Real bug, fixed: confirmed live -- Leads & Customers' and Admin >
        # Customers' "Invite Customer" buttons both open the exact same
        # CreateCustomer form/backend call, but picking either option from
        # a disambiguation used to land on a bare page with no spotlight at
        # all, even though the SAME utterance would have triggered a
        # highlighted, auto-clicked navigate had only one route matched
        # instead of two (try_feature_lookup_scored's single-match action
        # branch). Resolve each candidate's own matching page_feature
        # (fully data-driven, same find_topic_feature matching used
        # elsewhere) up front so it can be carried onto that option --
        # never on "where"/"how" phrasing, which is asking to be SHOWN
        # something, not have it clicked for them.
        route_feature: dict[str, "PageFeature"] = {}
        if (
            context is not None and context.utterance
            and not nav.is_informational_lookup(context.utterance)
        ):
            for rid in route_ids:
                route = nav.route_by_id(rid)
                if not route:
                    continue
                feature = nav.find_topic_feature(context.utterance, route)
                if feature:
                    route_feature[rid] = feature

        options = []
        for rid in route_ids:
            option = nav.route_option(rid, context)
            if not option:
                continue
            feature = route_feature.get(rid)
            if feature:
                option.highlightTargets = feature.locate or [feature.name]
                option.autoClick = True
            options.append(option)
        # Defensive backstop, not the primary fix: two registry routes
        # sharing a label/description (observed live: invoice_form vs.
        # invoice_form_add, since removed from route_registry.json once
        # confirmed as a dead duplicate frontend route — both rendered the
        # exact same component, and nothing in the app ever linked to the
        # "_add" one) would otherwise ask the user to pick between two
        # options they cannot actually distinguish, worse than not asking.
        # Kept here in case a future registry entry repeats the mistake.
        seen_labels: set[str] = set()
        deduped = []
        for o in options:
            if o.label in seen_labels:
                continue
            seen_labels.add(o.label)
            deduped.append(o)
        options = deduped[:3]
        if len(options) < 2:
            return None
        question = "A few pages match — which did you mean?"
        # Some of these candidate pages genuinely share the identical
        # underlying feature/component, not just a similar one -- confirmed
        # live: Leads & Customers' and Admin > Customers' "Invite Customer"
        # buttons both open the exact same CreateCustomer form and backend
        # call, just from two different starting pages. A bare "which did
        # you mean?" reads as if the two are different actions and picking
        # wrong loses something. Detect it generically (fully data-driven
        # off route_registry.json's existing page_features/keywords, same
        # matching find_topic_feature already does elsewhere -- no per-
        # feature special-casing) rather than hardcoding "invite customer":
        # if the utterance matches a same-named page_feature on 2+ of the
        # candidate routes, name it and say so honestly instead of the
        # generic question.
        shared_feature_names: dict[str, tuple[str, int]] = {}
        for feature in route_feature.values():
            key = feature.name.lower()
            display, count = shared_feature_names.get(key, (feature.name, 0))
            shared_feature_names[key] = (display, count + 1)
        shared_names = [display for display, count in shared_feature_names.values() if count >= 2]
        if shared_names:
            question = (
                f"{shared_names[0]} works the same either way — which "
                f"page would you like to go to?"
            )
        return NavigationResponse(
            type="disambiguation",
            question=question,
            options=options,
        )

    def _out_of_scope_response(
        self,
        understanding: dict,
        candidates: list | None = None,
        nav=None,
        context: SessionContext | None = None,
    ) -> NavigationResponse:
        # The LLM writes the acknowledgment (warm for greetings, courteous
        # decline otherwise); we always append what EDEN can actually do.
        # Conversation loop: instead of dead-ending, attach the best
        # above-threshold retrieval candidates as clickable cards — for a
        # navigation-shaped-but-vague request ("i need the money stuff"),
        # the insight is already a follow-up question per the prompt, and
        # the cards give the user something concrete to tap while answering.
        ack = (understanding.get("insight") or "").strip()
        redirect = ("I can help you get anywhere in FIELDCRAFT — try something like "
                    "'take me to invoices' or 'show me the schedule'.")
        options = None
        if candidates and nav is not None:
            # Permission-filter the offered cards, same as
            # _disambiguation_response -- never surface a page this role
            # can't reach as a clickable option.
            above_threshold = [
                r.id for r, s in candidates if s >= nav.candidate_threshold
            ]
            alt_ids = (
                above_threshold if context is None
                else nav.filter_permitted_route_ids(above_threshold, context)
            )[:3]
            built = [o for o in (nav.route_option(rid, context) for rid in alt_ids) if o]
            options = built or None
        return NavigationResponse(
            type="fallback",
            message=f"{ack} {redirect}".strip(),
            insight=ack or None,
            options=options,
        )

    @staticmethod
    def _tokens(utterance: str) -> list[str]:
        return [t for t in re.split(r"[^a-z']+", utterance) if t]

    def _is_greeting(self, utterance: str) -> bool:
        # Only a greeting if EVERY word is a greeting or filler — so "hey take
        # me to invoices" is still treated as navigation, not a wave hello.
        tokens = self._tokens(utterance)
        if not tokens or len(tokens) > 4:
            return False
        return all(t in GREETING_WORDS or t in GREETING_FILLER for t in tokens) \
            and any(t in GREETING_WORDS for t in tokens)

    def _is_affirmation(self, utterance: str) -> bool:
        tokens = self._tokens(utterance)
        if not tokens or len(tokens) > 4:
            return False
        return all(t in AFFIRMATION_WORDS or t in AFFIRMATION_FILLER for t in tokens) \
            and any(t in AFFIRMATION_WORDS for t in tokens)

    def _nothing_to_confirm_response(self) -> NavigationResponse:
        """Real bug, fixed: confirmed live -- a bare "yes" recognized as an
        affirmation-shaped utterance (see _is_affirmation) but with nothing
        remembered to confirm (no history at all, or the last turn was a
        permission-denied fallback with no route) used to just `return None`
        here, letting dispatch fall through to the generic LLM+retrieval
        navigate path. "Yes" carries no retrievable signal of its own, so
        that path built its retrieval candidates from noise (all well below
        candidate_threshold), yet the LLM still self-reported a confident
        navigate ("route_id='sold_production' confidence=0.9") -- which
        skips _maybe_disambiguate_route entirely (that gate only fires on
        LOW self-reported confidence, not on a low retrieval score backing
        it up) and silently sent the user somewhere that had nothing to do
        with what they'd just been told. An affirmation-shaped utterance
        with nothing to confirm should say so honestly instead of ever
        reaching that path."""
        return NavigationResponse(
            type="fallback",
            message=(
                "I don't have anything to confirm right now — what would "
                "you like to do?"
            ),
        )

    def _resolve_affirmation(
        self, utterance_lower: str, context: SessionContext
    ) -> NavigationResponse | None:
        """Navigate to the page the previous turn pointed at, if the utterance
        is a bare confirmation and that turn remembered a route (either a
        direct navigate or a suggestion like "Did you mean Projects?").
        Returns None only when the utterance isn't a confirmation at all,
        letting normal dispatch continue -- once it IS recognized as one,
        every exit is a real response (see _nothing_to_confirm_response),
        never a silent None that would let a contentless "yes" reach the
        LLM/retrieval path."""
        if not self._is_affirmation(utterance_lower):
            return None
        recent = self.conversation_memory.get_recent_turns(
            context.tenantId, context.userId
        )
        if not recent:
            return self._nothing_to_confirm_response()
        last = recent[-1]

        # "okay thanks" right after a successful navigation is an
        # acknowledgment, not a request to go there again — re-navigating
        # landed in a confusing "you're already on X". Confirmations only
        # make sense when the previous turn OFFERED something (a "did you
        # mean" suggestion or a disambiguation question).
        if last.get("type") == "navigate":
            return NavigationResponse(
                type="guidance",
                message=(
                    "Anytime! Just ask whenever you want to go somewhere "
                    "else in FIELDCRAFT."
                ),
            )

        # Previous bug, since reverted: a "guidance" turn's remembered route
        # can come from _attach_page_suggestion's follow-up tail ("...Would
        # you like to see the Project Invoices page?") tacked onto a
        # resolved billing/status answer. This used to be treated as
        # unconfirmable by bare filler, on the theory that "sure" might be
        # acknowledging the DATA ANSWER rather than the tacked-on offer. But
        # `_attach_page_suggestion` ALWAYS phrases the tail as an explicit
        # yes/no question ("Would you like to see the X page?") -- there is
        # no remaining ambiguity once the offer itself is a direct question,
        # so a bare "yes" right after it is unambiguously answering THAT
        # question. Confirmed live: a status answer ending "...Would you
        # like to see the Al Naber ... page?" -> "yes" -> got a non-committal
        # "Let me know if you'd like to see that page" instead of navigating,
        # which is worse than the original bug -- it now never lets a bare
        # "yes" confirm ANY tacked-on offer, even the unambiguous ones. Falls
        # through to the same route-confirmation logic below as a real
        # navigate/disambiguation suggestion.

        # After a disambiguation question, a bare "yes" doesn't say WHICH
        # option — re-ask with the same choices instead of guessing (or
        # worse, falling through to the LLM with no signal).
        options = last.get("options") or []
        if not last.get("route") and len(options) > 1:
            return NavigationResponse(
                type="disambiguation",
                question="Which one did you mean? Pick one below.",
                options=[
                    NavigationOption(label=o["label"], route=o["route"])
                    for o in options
                    if o.get("label") and o.get("route")
                ],
            )

        route_pattern = last.get("route")
        if not route_pattern:
            # Confirmed live: this is exactly the case that used to fall
            # through to the LLM/retrieval path with nothing to go on (see
            # _nothing_to_confirm_response) -- e.g. the previous turn was a
            # permission-denied fallback ("let me add a tool" for a role
            # without Tool access) with no route of its own, then a bare
            # "yes" got sent to the general navigate path anyway.
            return self._nothing_to_confirm_response()
        nav = self.registry["navigation"]

        # Real bug, fixed: a remembered suggestion that already carries a
        # resolved routeState (e.g. "Peter Parker — Project Invoices",
        # routeState.rowData="2079") was previously re-derived from scratch
        # via finalize_keyword_route -- which, for an entity-required route,
        # tries to extract the project from the CURRENT utterance ("yes"
        # itself), finds nothing, and answers "which project did you mean?"
        # instead of confirming the very project it just named. The project
        # was already resolved when the suggestion was built; replay that
        # routeState as-is rather than re-resolving it.
        remembered_state = last.get("routeState")
        if remembered_state:
            label = last.get("label") or route_pattern
            # Real bug, fixed: this replay used to construct the navigate
            # response directly, never re-running _check_permission -- a
            # page the user has no access to could be offered as a
            # suggestion (now fixed at the offer site too, see
            # NavigationAgent._has_permission) and then reached anyway by
            # confirming it with a bare "yes". Re-verify here as well, since
            # this bypasses the normal _finalize path entirely.
            denied = nav.verify_remembered_suggestion_permission(
                route_pattern, last.get("label"), context, remembered_state
            )
            if denied:
                return denied
            # Real gap, closed: confirming a suggestion that was made in
            # answer to a specific field question ("what's the start date")
            # used to just land on the bare page with no indication of where
            # that field actually is. Re-match the ORIGINAL question against
            # the target route's own page_features (same data-driven keyword
            # matching feature-lookup already uses elsewhere -- see
            # NavigationAgent.find_topic_feature) so arrival highlights the
            # specific field, not just the page. Works for whatever field
            # the registry already defines (start date, end date, address,
            # cost, ...) with no per-field code here -- purely a lookup
            # against existing route_registry.json data. No-op (plain
            # navigate, unchanged) when the original utterance doesn't match
            # any of this route's page_features.
            highlight_feature = None
            original_utterance = last.get("utterance")
            target_route = nav.route_by_pattern(route_pattern)
            if original_utterance and target_route:
                highlight_feature = nav.find_topic_feature(original_utterance, target_route)
            # Project-entity re-scoping (rbac-reviewer finding, 2026-07-29;
            # same fix applied to _resolve_history_recall) -- the permission
            # check above only confirms this PAGE TYPE is still allowed, not
            # that THIS SPECIFIC project is still in scope. See that method
            # for the full rationale.
            project_id = (remembered_state or {}).get("projectId")
            if project_id:
                allowed_project_ids = self._scoped_project_ids(context)
                if allowed_project_ids and project_id not in allowed_project_ids:
                    return NavigationResponse(
                        type="fallback",
                        message=(
                            "I can't confirm that anymore — try naming a "
                            "project directly, or ask me to list your "
                            "projects."
                        ),
                    )
            return NavigationResponse(
                type="navigate",
                route=route_pattern,
                label=label,
                message=f"Taking you to {label}.",
                confidence=0.95,
                routeState=remembered_state,
                insight=highlight_feature.hint if highlight_feature else None,
                highlight=highlight_feature.name if highlight_feature else None,
                highlightTargets=(highlight_feature.locate or [highlight_feature.name]) if highlight_feature else None,
            )

        route = nav.route_by_pattern(route_pattern)
        if route is None:
            return None
        return nav.finalize_keyword_route(
            route, context,
            last_discussed_project=self._last_discussed_project(context),
        )

    # ── conversation loop (pending navigation goal) ────────────────────────

    def _get_pending_goal(self, context: SessionContext) -> dict | None:
        """Defensive read: anything that isn't a well-formed goal dict
        (Redis corruption, a mocked memory in tests) is treated as no goal
        rather than crashing the request."""
        goal = self.conversation_memory.get_pending_goal(
            context.tenantId, context.userId
        )
        if isinstance(goal, dict) and goal.get("original_utterance"):
            return goal
        return None

    def _is_goal_reply(self, context: SessionContext, goal: dict) -> bool:
        """Is this message the ANSWER to the clarification EDEN just asked,
        or a brand-new request that happens to arrive while a goal is open?

        PRIMARY mechanism is the model (LLMClient.classify_goal_continuation
        / GOAL_CONTINUATION_SYSTEM_PROMPT), for the same reason triage()
        replaced VARIANCE_SIGNALS: whether a sentence answers a question is
        an open-ended understanding problem, and the alternative that was
        here before -- assume yes, always -- silently mis-answered every
        new question typed while a goal was open.

        The deterministic tiers that CAN decide this without the model run
        first and are trusted over it, since they're exact rather than
        probabilistic: a bare affirmation/negation ("yes", "no not that
        one") is unambiguously a reply, and Navigation's own
        _resolve_affirmation/_resolve_negation are built to resolve it.

        Fallback when the call fails (failure-escalation): the shape
        heuristic in GOAL_REPLY_MAX_WORDS_FALLBACK's comment -- short means
        reply, sentence-length means new request. Never raises."""
        utterance_lower = (context.utterance or "").lower()
        if self._is_affirmation(utterance_lower) or self._is_negation(utterance_lower):
            return True
        if _FRESH_COMMAND_RE.search(utterance_lower):
            logger.info(
                "conversation loop: %r is an explicit navigation command — "
                "treating as a new request, not a reply to goal %r",
                context.utterance, goal.get("original_utterance"),
            )
            return False

        question = goal.get("last_question") or ""
        original = goal.get("original_utterance") or ""
        offered = goal.get("offered")
        decision = None
        if self.llm is not None and question:
            decision = self.llm.classify_goal_continuation(
                context.utterance, question, original, offered=offered,
            )

        if decision is None:
            is_reply = len(self._tokens(utterance_lower)) <= GOAL_REPLY_MAX_WORDS_FALLBACK
            logger.info(
                "conversation loop: goal-continuation call unavailable — shape "
                "heuristic says %s for %r (goal %r)",
                "reply" if is_reply else "new request", context.utterance, original,
            )
            return is_reply

        is_reply = bool(decision.get("is_reply"))
        confidence = decision.get("confidence")
        # Confidence gates CONTINUING only (see the threshold's comment): a
        # low-confidence "it's a reply" is a guess, and a guess must not
        # beat classifying what the user actually just typed. A
        # low-confidence "it's a new request" already lands on the safer
        # side, so it needs no gate.
        if is_reply and confidence is not None \
                and confidence < GOAL_CONTINUATION_CONFIDENCE_THRESHOLD:
            logger.info(
                "conversation loop: goal-continuation said reply but only %.2f "
                "confident (< %.2f) — treating %r as a new request instead (goal %r)",
                confidence, GOAL_CONTINUATION_CONFIDENCE_THRESHOLD,
                context.utterance, original,
            )
            return False

        logger.info(
            "conversation loop: goal-continuation check — %r is %s (confidence=%s: %s), goal %r",
            context.utterance, "a reply" if is_reply else "a NEW request",
            confidence, decision.get("reasoning"), original,
        )
        return is_reply

    def _update_goal_state(
        self,
        context: SessionContext,
        response: NavigationResponse,
        existing_goal: dict | None,
        goal_continued: bool,
        resolved: bool | None = None,
        goal_kind: str | None = None,
        extra_goal_fields: dict | None = None,
    ) -> None:
        """Goal lifecycle, run once per dispatch: navigating anywhere means
        the requirement was met — close the goal. A turn that instead asked
        something (disambiguation) or fell back opens/advances a goal, so
        the user's NEXT message is interpreted as their answer. Guidance
        turns are neutral: a greeting or "where am I" mid-clarification
        neither opens nor abandons the loop. Capped at GOAL_MAX_ATTEMPTS
        unresolved turns — past that, carrying the stale original request
        forward pollutes retrieval for what is almost certainly a fresh
        attempt in new words.

        `existing_goal` / `goal_continued` (from dispatch()'s pre-tier
        snapshot): several deterministic tiers are blind to whether a
        clarification is already in progress. If one of THOSE fires while a
        goal is open and also produces an unresolved response, that is an
        unrelated hit, not the user answering the pending question — so it
        must start a FRESH goal (keyed on the current utterance) rather
        than silently overwriting the real goal's last_question/offered
        with unrelated text. Only the goal-aware LLM refinement path and an
        affirmation re-asking the same clarification set goal_continued —
        those are the only two callers who actually reasoned about this
        specific goal before producing their response.

        `resolved` lets a caller whose "answered" response isn't
        `type == "navigate"` (Insight's real answers are `"guidance"`) force
        the close — without it, a `"guidance"` response hits neither the
        close branch nor the keep-open branch and would silently linger
        until GOAL_MAX_ATTEMPTS force-drops it. `None` (the default)
        preserves the exact original `type == "navigate"` check, so
        Navigation's behavior is untouched. `goal_kind` tags a freshly-opened
        goal (e.g. `"project_lookup"`) so a later turn's routing/resolution
        knows which agent's conversation this is continuing.
        """
        if resolved is True or (resolved is None and response.type == "navigate"):
            self.conversation_memory.clear_pending_goal(
                context.tenantId, context.userId
            )
            return
        if response.type not in ("disambiguation", "fallback"):
            return
        # Real bug, fixed: a "fallback" response used to ALWAYS open a goal,
        # but that type covers two very different shapes -- a genuine
        # "did you mean X?" suggestion or option cards (something the user
        # can actually answer, e.g. a bare "yes" the affirmation tier
        # resolves against), and a plain decline with nothing to answer
        # (out_of_scope's acknowledgment, or no_match's generic "try asking
        # for schedule/invoices/purchase orders" when even the closest-route
        # guess came up empty). Opening a goal for the latter turned every
        # off-topic/identity/meta question ("who are you", "what is my
        # name") into a multi-turn "which page did you mean?" loop that then
        # swallowed the NEXT, unrelated message as if it were the answer
        # (observed live). Disambiguation always has a real question, so it
        # always opens one; a fallback only does when there's a concrete
        # suggestion or options to actually respond to.
        #
        # Real bug, fixed: that guard must NOT skip an already-open,
        # genuinely-continued goal (existing_goal is not None and
        # goal_continued) -- only skip OPENING a brand-new one. Without this
        # carve-out, a user with an open goal who replies with more gibberish
        # (also a bare no-suggestion/no-options fallback) left that goal
        # neither advanced nor dropped: GOAL_MAX_ATTEMPTS's 3-tries cap
        # silently never applied to this one reply shape, so the goal only
        # ever expired via Redis's TTL, not the documented attempts cap.
        if (
            response.type == "fallback"
            and not response.suggestion
            and not response.options
            and not (existing_goal is not None and goal_continued)
        ):
            return

        goal = existing_goal if (existing_goal is not None and goal_continued) else None
        if goal is None:
            goal = {
                "original_utterance": context.utterance,
                "attempts": 0,
                "rejected_routes": [],
            }
            if goal_kind:
                goal["kind"] = goal_kind
            if extra_goal_fields:
                goal.update(extra_goal_fields)
        attempts = goal.get("attempts", 0) + 1
        if attempts >= GOAL_MAX_ATTEMPTS:
            logger.info(
                "conversation loop: goal %r unresolved after %d attempts — dropping it",
                goal.get("original_utterance"), attempts,
            )
            self.conversation_memory.clear_pending_goal(
                context.tenantId, context.userId
            )
            return
        goal["attempts"] = attempts
        goal["last_question"] = response.question or response.message
        # The ordered option labels make ordinal answers resolvable — "the
        # second one" is meaningless to the model unless it can see what was
        # offered, in what order (observed live: it navigated to option 1).
        if response.options:
            goal["offered"] = [o.label for o in response.options]
        self.conversation_memory.set_pending_goal(
            context.tenantId, context.userId, goal
        )
        logger.info(
            "conversation loop: goal %r open (attempt %d/%d), awaiting the user's answer",
            goal["original_utterance"], attempts, GOAL_MAX_ATTEMPTS,
        )

    def _is_negation(self, utterance: str) -> bool:
        tokens = self._tokens(utterance)
        if not tokens or len(tokens) > 5:
            return False
        return all(t in NEGATION_WORDS or t in NEGATION_FILLER for t in tokens) \
            and any(t in NEGATION_WORDS for t in tokens)

    def _resolve_negation(
        self, utterance_lower: str, context: SessionContext
    ) -> NavigationResponse | None:
        """"No" / "not that one" right after EDEN navigated somewhere or
        offered a page rejects that offer: remember the rejection in the
        pending goal (so neither retrieval nor re-offers surface it again)
        and immediately come back with the next-best alternatives as
        clickable cards. Returns None when it isn't a bare rejection or
        there's nothing on the table to reject, letting dispatch continue."""
        if not self._is_negation(utterance_lower):
            return None
        recent = self.conversation_memory.get_recent_turns(
            context.tenantId, context.userId
        )
        if not recent:
            return None
        last = recent[-1]
        offered = [last.get("route")] + [
            o.get("route") for o in (last.get("options") or [])
        ]
        offered = [r for r in offered if r]
        if not offered:
            return None

        nav = self.registry["navigation"]
        goal = self._get_pending_goal(context)
        if goal is None:
            goal = {
                # The rejected offer was EDEN's answer to the LAST utterance —
                # that's the request still unmet, so it becomes the goal.
                "original_utterance": last.get("utterance") or context.utterance,
                "attempts": 0,
                "rejected_routes": [],
            }
        goal["rejected_routes"] = sorted(set(goal.get("rejected_routes") or []) | set(offered))
        logger.info(
            "conversation loop: user rejected %r for goal %r — offering alternatives",
            offered, goal["original_utterance"],
        )

        # Next-best alternatives: retrieval on the ORIGINAL request (the
        # rejection itself carries no signal), minus everything rejected.
        # Comparison is on resolved routes — the same string remembered in
        # the turn — so a rejected "Customers" (/admin) can't come back.
        candidates = nav.top_candidates(goal["original_utterance"])
        rejected = set(goal["rejected_routes"])
        alt_ids = [
            r.id for r, s in candidates
            if s >= nav.candidate_threshold and r.resolved_route() not in rejected
        ]
        response = self._disambiguation_response(alt_ids, nav, context)
        if response is not None:
            response.question = "Okay, not that one — is it one of these?"
        else:
            response = NavigationResponse(
                type="fallback",
                message=(
                    "Okay — tell me a bit more about what you're looking for, "
                    "and I'll find the right page."
                ),
            )

        # This tier owns its goal write (dispatch skips the generic updater
        # for it — the generic path can't know the original request): bump
        # attempts, keep the cap, record what was just asked.
        goal["attempts"] = goal.get("attempts", 0) + 1
        if goal["attempts"] >= GOAL_MAX_ATTEMPTS:
            logger.info(
                "conversation loop: goal %r unresolved after %d attempts — dropping it",
                goal["original_utterance"], goal["attempts"],
            )
            self.conversation_memory.clear_pending_goal(
                context.tenantId, context.userId
            )
        else:
            goal["last_question"] = response.question or response.message
            if response.options:
                goal["offered"] = [o.label for o in response.options]
            self.conversation_memory.set_pending_goal(
                context.tenantId, context.userId, goal
            )
        return response

    def _resolve_history_recall(
        self, utterance_lower: str, context: SessionContext
    ) -> NavigationResponse | None:
        """Answers meta-questions about the conversation itself ("what was
        the project before this one", "what were we discussing earlier") --
        _last_discussed_project only ever remembers the SINGLE latest
        project (overwritten on every switch), so it can't answer "before
        this one" on its own. This walks the raw recent-turns buffer
        (state-and-memory Tier 2, same data _resolve_affirmation/negation
        already read) instead, looking for the most recent DISTINCT project
        mentioned before the current one. Returns None (falls through to
        normal dispatch) when the utterance doesn't match this pattern at
        all, so it never shadows a real navigation/data request that happens
        to contain "earlier"/"before" as filler (e.g. "the invoice due
        earlier this month")."""
        if not (
            (_RECALL_MARKER_RE.search(utterance_lower) and _RECALL_CONTEXT_RE.search(utterance_lower))
            or _fuzzy_recall_signal(utterance_lower)
        ):
            return None

        recent = self.conversation_memory.get_recent_turns(
            context.tenantId, context.userId
        )
        # oldest -> newest; walk backwards collecting DISTINCT projects so
        # two consecutive turns about the same project ("show me X" then
        # "what's its status") count as one, not a switch.
        distinct: list[dict] = []
        for turn in reversed(recent):
            name = turn.get("project")
            if not name:
                continue
            if distinct and distinct[-1].get("project") == name:
                continue
            distinct.append(turn)
            if len(distinct) == 2:
                break

        if len(distinct) < 2:
            return NavigationResponse(
                type="fallback",
                message=(
                    "I don't have an earlier project in our recent "
                    "conversation — try naming it directly, or ask me to "
                    "list your projects."
                ),
            )

        # distinct[0] is the CURRENT project (matches _last_discussed_project);
        # distinct[1] is the one being asked about.
        previous_turn = distinct[1]
        previous_name = previous_turn.get("project")
        route_pattern = previous_turn.get("route")
        label = previous_turn.get("label") or previous_name
        remembered_state = previous_turn.get("routeState")

        if not route_pattern or not remembered_state:
            # No navigable routeState was ever captured for that turn (e.g.
            # it was a guidance answer, not a navigate) -- name it rather
            # than guess a route.
            return NavigationResponse(
                type="guidance",
                message=f"Before that, you were asking about {previous_name}.",
            )

        # Same fail-closed re-verification _resolve_affirmation uses before
        # replaying a remembered routeState -- permissions may have changed
        # since that turn was first shown.
        nav = self.registry["navigation"]
        denied = nav.verify_remembered_suggestion_permission(
            route_pattern, label, context, remembered_state
        )
        if denied:
            return denied

        # Project-entity re-scoping (rbac-reviewer finding, 2026-07-29):
        # the permission check above only confirms the role may still visit
        # this PAGE TYPE (e.g. PRO.REA) -- it says nothing about whether
        # THIS SPECIFIC project is still in scope for this user. A customer's
        # project assignment can change mid-session; without this, a stale
        # project id riding along in the replayed routeState would re-
        # navigate successfully as long as the route itself is generally
        # allowed. Empty allowed_project_ids means "unscoped" for internal
        # roles (fail-open by design, see _scoped_project_ids) -- only
        # enforced when the list is non-empty (real customer projects, or
        # the zero-projects sentinel, which matches nothing real).
        project_id = (remembered_state or {}).get("projectId")
        if project_id:
            allowed_project_ids = self._scoped_project_ids(context)
            if allowed_project_ids and project_id not in allowed_project_ids:
                return NavigationResponse(
                    type="fallback",
                    message=(
                        "I can't take you back to that project anymore — "
                        "try naming a project directly, or ask me to list "
                        "your projects."
                    ),
                )

        return NavigationResponse(
            type="navigate",
            route=route_pattern,
            label=label,
            message=f"Before that, you were looking at {label}.",
            confidence=0.9,
            routeState=remembered_state,
        )

    def _resolve_go_back(
        self, utterance_lower: str, context: SessionContext
    ) -> NavigationResponse | None:
        """"Go back" / "take me back to the previous page" replays the most
        recent turn's route from the raw recent-turns buffer (same data
        _resolve_history_recall/_resolve_affirmation/negation already read)
        -- preferring a turn whose route DIFFERS from context.currentRoute
        (a genuinely earlier page), but falling back to the most recent
        remembered route at all otherwise, rather than giving up (see the
        real-bug comment below -- an intervening guidance/data-answer turn
        with no route of its own shouldn't make "go back" a dead end just
        because the last real navigate happens to match where the page
        already is). Deterministic for the same reason as the other
        conversation-loop tiers: the phrase references turn HISTORY, not
        the current utterance's own content, so retrieval/the LLM have
        nothing to go on and (observed live) just dead-end in a confusing,
        self-contradicting fallback instead.

        Best-effort, not true browser history: EDEN only knows pages it
        itself navigated the user to, not manual clicks the user made
        outside the chat. Returns None when the phrase doesn't match this
        pattern, or NOTHING at all is remembered yet, letting dispatch
        continue to the generic fallback."""
        if not _GO_BACK_RE.search(utterance_lower):
            return None
        recent = self.conversation_memory.get_recent_turns(
            context.tenantId, context.userId
        )
        if not recent:
            return None

        # Real bug, fixed: confirmed live -- comparing bare route strings
        # alone can't tell two DIFFERENT pages apart when they share an
        # admin-shell URL (Materials, Vendors, Resources, Tools, Employees,
        # and Customers all resolve to the same "/admin", distinguished
        # only by routeState.selectedTab). Going Materials -> Vendors, then
        # saying "go back", used to see both turns' route == "/admin",
        # treat them as identical, skip both, and fall into the
        # nothing-genuinely-different fallback below -- which replayed the
        # MOST RECENT remembered turn (Vendors, the page already on),
        # producing a silent no-op that looks exactly like "go back" doing
        # nothing. Comparing the full (route, routeState) pair -- not just
        # route -- distinguishes tabs sharing one shell URL the same way
        # the bare string already correctly distinguishes unrelated pages.
        navigate_turns = [t for t in reversed(recent) if t.get("route")]
        if not navigate_turns:
            return None
        current_route = navigate_turns[0].get("route")
        current_state = navigate_turns[0].get("routeState")
        previous_turn = None
        for turn in navigate_turns[1:]:
            if turn.get("route") == current_route and turn.get("routeState") == current_state:
                continue
            previous_turn = turn
            break
        if previous_turn is None:
            # Every remembered turn is the exact same (route, routeState) --
            # an intervening guidance/data-answer turn doesn't move the
            # page, so the last real navigate is still "current", and
            # there's genuinely nothing else remembered to go back to. The
            # one page EDEN DOES remember is still a real, valid page --
            # re-navigating there is a harmless no-op at worst (already on
            # it) and correct at best (the frontend's own currentRoute can
            # be momentarily stale relative to what EDEN last sent).
            previous_turn = navigate_turns[0]

        route_pattern = previous_turn.get("route")
        label = previous_turn.get("label") or route_pattern
        remembered_state = previous_turn.get("routeState")

        # Same fail-closed re-verification _resolve_history_recall uses
        # before replaying a remembered routeState -- permissions may have
        # changed since that turn was first shown.
        nav = self.registry["navigation"]
        denied = nav.verify_remembered_suggestion_permission(
            route_pattern, label, context, remembered_state
        )
        if denied:
            return denied

        # Same project re-scoping check as _resolve_history_recall (rbac-
        # reviewer finding, 2026-07-29): the permission check above only
        # confirms the role may still visit this PAGE TYPE, not that THIS
        # SPECIFIC project is still in scope for this user.
        project_id = (remembered_state or {}).get("projectId")
        if project_id:
            allowed_project_ids = self._scoped_project_ids(context)
            if allowed_project_ids and project_id not in allowed_project_ids:
                return NavigationResponse(
                    type="fallback",
                    message=(
                        "I can't take you back to that page anymore — try "
                        "naming what you're looking for directly."
                    ),
                )

        return NavigationResponse(
            type="navigate",
            route=route_pattern,
            label=label,
            message=f"Back to {label}.",
            confidence=0.9,
            routeState=remembered_state,
        )

    def _greeting_response(self, platform: str | None = None) -> NavigationResponse:
        # Real bug, fixed: confirmed live on mobile (2026-09-03) -- the
        # example phrasing ("take me to invoices", "show me the schedule",
        # "open purchase orders") all suggest a PAGE NAVIGATION mobile can
        # never perform (see _strip_route_fields_for_mobile's own docstring
        # -- mobile has no page to send a route to). Suggesting them anyway
        # invites the exact request mobile can't fulfil. Mobile gets
        # data-question examples instead, matching what it can actually
        # answer (Insight/Reporting).
        if platform == "mobile":
            return NavigationResponse(
                type="guidance",
                message=(
                    "Hi! I'm EDEN. Ask me about a project — try \"what's the "
                    "status on order 12345\", \"what's the cost so far\", or "
                    "\"is anything overdue\"."
                ),
            )
        return NavigationResponse(
            type="guidance",
            message=(
                "Hi! I'm EDEN, your navigation assistant. I can take you anywhere "
                "in FIELDCRAFT — try \"take me to invoices\", \"show me the schedule\", or "
                "\"open purchase orders\"."
            ),
        )
