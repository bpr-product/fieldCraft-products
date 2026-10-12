import difflib
import logging
import os
import random
import re
from datetime import datetime

from source.agents.insight_agent import (
    _prefer_non_degenerate_sibling,
    _project_list_limit,
    apply_reasoning_continuity,
    build_estimate_change_message,
    build_schedule_change_message,
    estimate_changed_per_summary,
    projects_disambiguation,
    resolve_margin_response,
    schedule_changed_per_summary,
)
from source.model.navigation_models import NavigationResponse, ProjectRef, SessionContext
from source.resolvers.conversation_memory import ConversationMemory
from source.resolvers.entity_resolver import (
    PROJECT_HEALTH_COLLECTION,
    EntityResolver,
)
from source.resolvers.user_role_resolver import UserRoleResolver
from source.utils.audit_log import write_reasoning_audit_log
from source.utils.llm_client import _number_tokens
from source.utils.mongo_client import get_collection

logger = logging.getLogger(__name__)

# Fields explicitly EXCLUDED from the customer-safe RAG context (see
# answer_from_data) even though they live in project_health -- the same
# customer-safety guarantee status_response's query shape already enforces
# ("NO money fields in this projection"), applied here too. Internal cost
# detail belongs to Insight, never to a document handed to an LLM answering
# a customer-facing question.
#
# Real bug, fixed: estimatedCost used to be on this list, but that's the
# customer's OWN quoted/agreed amount -- confirmed live, the real app's own
# customer-facing dashboard already shows it directly (the "Estimate"
# column on a customer's own projects table, e.g. $14,510.07 for order
# 272000 -- exactly project_health.estimatedCost for that project).
# Redacting it was MORE restrictive than the actual application already is
# for this same role. What must stay hidden is what it's actually COSTING
# the contractor to do the job -- committedSpend/materialCost/workerCost/
# toolCost/overheadCost/margin -- since committedSpend vs. estimatedCost
# reveals profit margin, which a customer should never see. remainingBudget
# (estimatedCost - committedSpend) stays redacted for the same reason: with
# estimatedCost now visible, exposing remainingBudget too would let a
# customer back-calculate committedSpend algebraically.
_PROJECT_HEALTH_INTERNAL_ONLY_FIELDS = {
    "materialCost", "workerCost", "toolCost",
    "overheadCost", "margin", "committedSpend", "remainingBudget",
    "estimateHistory", "estimateSummary", "estimateVersion",
}

# Real bug, fixed: invoice_summary was handed to the RAG model with ZERO
# redaction, unlike project_health -- found live via automation test:
# asking "what is the material cost" got answered with a real dollar
# figure ($10,348.00) pulled from invoice_summary.expenses, an internal
# FIELDCRAFT-to-vendor/employee payment ledger (payee.userId, chartOfAccountId,
# reference IDs -- confirmed live, none of it the customer's business,
# same class of internal-cost detail _PROJECT_HEALTH_INTERNAL_ONLY_FIELDS
# already protects on the project_health side). invoices/totalBilled/
# totalOutstanding/totalReceived/payments etc. are the customer's OWN
# billing relationship and stay visible -- only the internal expense
# ledger is excluded here.
_INVOICE_SUMMARY_INTERNAL_ONLY_FIELDS = {"expenses"}

# Domain -> which fields of the REDACTED data actually passed to the RAG
# model (see answer_from_data's `data` dict) carry a legitimate cause/reason
# string for that domain. Used by _grounded_reason_domains / the RAG
# verification's domain-grounding check below.
#
# Real bug, fixed: confirmed live -- asked "why the estimation changed?" (a
# natural paraphrase _is_estimate_change_question's keyword gate misses, so
# it fell through to this RAG path instead of the dedicated, bomReconciles-
# aware _estimate_change_response), the model answered "due to the reason
# 'SAVE_TASKS' in the second revision" -- a specific, confident, WRONG claim.
# "SAVE_TASKS" is scheduleRevisions[].reason, not anything estimate-related;
# estimateHistory/estimateSummary are ALREADY excluded from this data
# entirely (_PROJECT_HEALTH_INTERNAL_ONLY_FIELDS, above) for cost-redaction
# reasons, so the model had ZERO real estimate-domain data to ground an
# estimate-cause claim in and improvised using the only reason-shaped text
# available. The existing verification (_number_tokens) only checks numeric
# figures -- structurally blind to a non-numeric cross-domain misattribution
# like this.
_REASON_DOMAIN_FIELDS = {
    "schedule": (
        ("scheduleRevisions", ("reason", "comment")),
        ("scheduleSummary", ("lastReason",)),
    ),
    "status": (
        ("statusTimeline", ("changeReason",)),
    ),
}


def _grounded_reason_strings(data: dict, domain: str) -> set[str]:
    """Every real, present reason/cause string for `domain` within the
    ACTUAL (already redacted) data dict the RAG model was given -- not the
    raw Mongo document, so this reflects exactly what the model could have
    legitimately grounded an answer in."""
    project = (data or {}).get("project") or {}
    strings: set[str] = set()
    for container_key, fields in _REASON_DOMAIN_FIELDS.get(domain, ()):
        container = project.get(container_key)
        entries = container if isinstance(container, list) else [container] if isinstance(container, dict) else []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for field in fields:
                value = entry.get(field)
                if isinstance(value, str) and value.strip():
                    strings.add(value.strip())
    return strings


def _question_reason_domain(utterance_lower: str) -> str | None:
    """Which domain's OWN reason data a question is entitled to cite as a
    cause -- 'estimate' when asking about an estimate/price, 'schedule' for
    a start-date/schedule question. Note: estimateHistory/estimateSummary
    are excluded from RAG's data entirely, so this domain's grounded-string
    set from _grounded_reason_strings is always empty -- any cause cited
    for it is necessarily cross-domain or invented.

    Deliberately BROADER than _is_estimate_change_question/_is_schedule_
    change_question in one respect -- topic word alone, no "why" phrase
    requirement -- since those two exist to decide ROUTING to the
    dedicated builders in respond() and are intentionally narrow; reusing
    them here would make this check blind to exactly the phrasings that
    fall through to this RAG path in the first place (a statement like
    "the estimate went up because X" carries a cause worth checking even
    with no "why" in it at all). Shares _mentions_estimate_topic/
    _mentions_schedule_topic's topic detection (stem match + fuzzy typo
    fallback) with the routing functions, so a misspelling like
    "estiamtion" is caught here too, not just in routing.

    Returns None when the utterance doesn't clearly belong to either topic,
    so the check below only applies where a wrong domain is actually
    identifiable, never as a guess."""
    if _mentions_estimate_topic(utterance_lower):
        return "estimate"
    if _mentions_schedule_topic(utterance_lower):
        return "schedule"
    return None


def _find_ungrounded_cross_domain_reason(
    answer: str, data: dict, question_domain: str | None
) -> str | None:
    """Returns the specific cross-domain reason string the answer cited, if
    any -- i.e. the answer states a cause that only exists in a DIFFERENT
    domain's data than the one the question asked about. None means the
    answer passes (no check applies, or nothing ungrounded was found).

    Deliberately narrow: only flags a cause that is traceable to a WRONG
    domain's real data, not any cause-shaped text in general -- the RAG
    path's existing per-figure verification already exists for the "did the
    model invent something not in the data at all" case; this closes the
    different gap where a real string was grounded, just in the wrong
    field."""
    if question_domain is None:
        return None
    answer_lower = answer.lower()
    own_domain_reasons = _grounded_reason_strings(data, question_domain)
    for other_domain in _REASON_DOMAIN_FIELDS:
        if other_domain == question_domain:
            continue
        for reason in _grounded_reason_strings(data, other_domain):
            if reason.lower() in answer_lower and reason not in own_domain_reasons:
                return reason
    return None


# Cost-concept phrase -> the project_health field it names. Every one of
# these fields is in _PROJECT_HEALTH_INTERNAL_ONLY_FIELDS -- i.e. NONE of
# them are ever actually present in the `data` dict answer_from_data hands
# the model (they're stripped before the call, for cost-redaction reasons
# unrelated to this check). If the ANSWER discusses one of these concepts
# anyway, whatever number it attached to it cannot be grounded in anything
# real, regardless of what that number happens to be -- see
# _answer_discusses_excluded_field.
_INTERNAL_FIELD_TOPIC_PHRASES = {
    "material cost": "materialCost",
    "materials cost": "materialCost",
    # Real bug, fixed: confirmed live -- "is it related to materials
    # price?" was affirmed with a fabricated "yes" (plus a fabricated
    # attribution to a person pulled from an unrelated field) even though
    # materialCost is excluded from the model's context entirely and
    # evidence.priceChangeDetected is explicitly false. "price" phrasing
    # slipped through because this dict only had "cost"-suffixed variants.
    "material price": "materialCost",
    "materials price": "materialCost",
    "labor cost": "workerCost",
    "worker cost": "workerCost",
    "tool cost": "toolCost",
    "tools cost": "toolCost",
    "overhead cost": "overheadCost",
    "profit margin": "margin",
    "committed spend": "committedSpend",
    "remaining budget": "remainingBudget",
}


def _answer_discusses_excluded_field(answer_lower: str, data: dict) -> str | None:
    """Returns the field name if the answer discusses an internal-only cost
    concept that was never sent to the model at all. Confirmed live: asked
    "what is the tool cost and material cost" (both fields excluded), the
    answer stated "$194.62" for BOTH -- neither matches the real value
    (materialCost=150.75, toolCost=0.0 for that project); "194.62" merely
    happens to appear, coincidentally, attached to a wholly unrelated field
    elsewhere in the document, which is enough to pass the numeric
    verification (that check only asks "does this digit-string appear
    ANYWHERE," never "is it attached to the field being asked about").
    This closes the gap the numeric check structurally cannot: if the
    field itself isn't even a key in `data`, no number stated for it can
    be grounded, so the answer is rejected regardless of the actual digits.
    None means no excluded-field concept was mentioned."""
    project = (data or {}).get("project") or {}
    for phrase, field in _INTERNAL_FIELD_TOPIC_PHRASES.items():
        if phrase in answer_lower and field not in project:
            return field
    return None


# Confirmed live despite PROJECT_DATA_ANSWER_SYSTEM_PROMPT already
# explicitly instructing against it ("Never say the word 'JSON' or refer
# to 'the data' as a technical object"): "The JSON does not provide
# specific information about changes to material or tool cost
# estimations." -- the model describing its own input format, an internal
# implementation detail that must never reach the user. Same "instruction
# alone isn't a guarantee" discipline as the invented-follow-up-offer
# strip above.
_SELF_REFERENTIAL_RE = re.compile(
    r"\bthe json\b|\bthis json\b|\bthe (data|dataset) (provided|given|does not|doesn't)\b|"
    r"\baccording to the (data|json)\b|\bin the (given|provided) data\b",
    re.IGNORECASE,
)


def _format_date(value) -> str:
    """Mongo may return jobStartDate/etc as a real datetime or a string in
    whatever shape the loader wrote (observed live: "2028-03-13
    09:30:00+00:00", space-separated, not strict ISO "T"-separated) --
    normalize either to "Month DD, YYYY" (prose date format per org
    convention) rather than surfacing a raw timestamp to the user. Every
    ISO-like variant starts with a fixed-width YYYY-MM-DD date, so parsing
    just that prefix is robust regardless of what follows it."""
    strftime_day = "%-d" if os.name != "nt" else "%#d"
    if isinstance(value, datetime):
        return value.strftime(f"%B {strftime_day}, %Y")
    text = str(value)
    try:
        parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        return parsed.strftime(f"%B {strftime_day}, %Y")
    except ValueError:
        return text


def _parse_date(value) -> "datetime | None":
    """Shared date parse used by _format_date and _date_highlight_variants --
    a real datetime passes through, an ISO-like string is parsed off its
    fixed-width YYYY-MM-DD prefix, anything else is None. Deliberately
    date-only (drops time-of-day) since it only ever feeds display
    formatting -- do NOT reuse this for a chronological comparison
    (two same-day timestamps would incorrectly collapse to equal); use
    _parse_datetime_full for that instead."""
    if isinstance(value, datetime):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _parse_datetime_full(value) -> "datetime | None":
    """Full date+time parse for chronological comparisons (e.g. the
    end-before-start sanity check in date_response) -- unlike _parse_date,
    this keeps time-of-day, so two timestamps on the same calendar day
    still compare correctly (the real reported defect, project_id=1129/
    tenant tn88351775, is same-day: jobStartDate 12:00:00 vs jobEndDate
    08:25:00). Handles a real datetime, or a string of the observed live
    shape ("2028-03-13 09:30:00+00:00", space-separated) -- the timezone
    suffix is ignored, same fixed-width-prefix approach as _parse_date,
    since the source data is a single-offset dump, not a real multi-
    timezone signal worth parsing precisely."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value)
    for length, fmt in ((19, "%Y-%m-%d %H:%M:%S"), (19, "%Y-%m-%dT%H:%M:%S"), (10, "%Y-%m-%d")):
        try:
            return datetime.strptime(text[:length], fmt)
        except ValueError:
            continue
    return None


def _date_highlight_variants(value) -> list[str]:
    """Every on-screen spelling of a date the frontend actually renders, so
    PageSpotlight can locate the pill. Real bug, fixed: the highlight sent
    ONLY the prose form ("March 13, 2028"), but the Project Information pill
    renders dayjs "MMM D YYYY" ("Mar 13 2028") and the Customer Invoices
    page renders "MMM DD, YYYY" ("Mar 13, 2028") -- exact-substring search
    (PageSpotlight.findElement) matched none of them, so a correct date
    highlight silently found nothing on the page every time. The spoken
    answer still uses _format_date's prose form; these are ADDITIONAL
    locate candidates only. Falls back to just the prose form for an
    unparseable value."""
    dt = _parse_date(value)
    if dt is None:
        return [_format_date(value)]
    d = "%-d" if os.name != "nt" else "%#d"
    variants = [
        dt.strftime(f"%B {d}, %Y"),   # March 13, 2028  (prose, org convention)
        dt.strftime(f"%b {d}, %Y"),   # Mar 13, 2028    (Customer Invoices page)
        dt.strftime(f"%b {d} %Y"),    # Mar 13 2028     (Project Information pill)
        dt.strftime("%b %d, %Y"),     # Mar 13, 2028    (zero-padded day)
        dt.strftime("%b %d %Y"),      # Mar 13 2028     (zero-padded day)
    ]
    seen: set[str] = set()
    return [v for v in variants if not (v in seen or seen.add(v))]

INVOICE_SUMMARY_COLLECTION = "invoice_summary"
TASK_INTELLIGENCE_COLLECTION = "task_intelligence"

# Raw project_health.status values (see fieldcraft-tenant-web's
# src/utils/ProjectStatus.ts, the frontend's canonical status enum) that
# come BEFORE a project is even sold/contracted -- no invoice can exist yet
# at these stages by construction, not by inference. A project here still
# gets an invoice_summary doc with all-zero totals (likely an ETL
# placeholder), and reporting billing_response's plain "$0 billed" wording
# read as "you owe nothing" instead of "no invoice yet" -- observed live
# for a project in awaitingEstimation ("Task & estimate preparation").
# Deliberately NOT extended into Contract Approval/PO Generation/Project
# Active -- whether an invoice (e.g. a deposit) can exist that early is a
# real business-workflow question, not something to guess at here.
PRE_CONTRACT_STATUSES = frozenset({
    "draft", "bidPending", "bidReview",
    "awaitingEstimation", "estimationApproval", "awaitingReestimation",
})

# Display text for PRE_CONTRACT_STATUSES only -- mirrors
# ProjectStatus.ts's getStatusDisplayName for just these keys, not a full
# duplicate of that map (the other statuses never reach this branch).
PRE_CONTRACT_STATUS_LABELS = {
    "draft": "Draft",
    "bidPending": "Bid in Cart",
    "bidReview": "Q&A Review Pending",
    "awaitingEstimation": "Task & estimate preparation",
    "estimationApproval": "Estimation Approval",
    "awaitingReestimation": "Re-estimation Requested",
}

# Instrumentation only -- never affects routing. BILLING_SIGNALS (below) is
# a closed keyword list, structurally the same shape as the old top-level
# router that provably misrouted phrasing it wasn't written for. Rather
# than assume this smaller list is safe because it's smaller, a sample of
# requests are checked against a real LLM call and disagreements are
# logged -- real evidence before trusting or replacing the keyword list.
BILLING_SIGNAL_AUDIT_SAMPLE_RATE = float(os.getenv("BILLING_SIGNAL_AUDIT_SAMPLE_RATE", "0.1"))

# Billing questions ("how much do I owe", "have I paid", "what's my
# balance") are a DIFFERENT kind of money question from Insight's internal
# cost questions -- this is the customer's OWN bill, not FIELDCRAFT's internal
# vendor/labor cost structure. Safe for every role, including customers:
# nobody needs permission to ask what they themselves owe. Closed,
# billing-specific vocabulary (same reasoning as Navigation's own
# deterministic word lists) -- not a re-introduction of hardcoded routing
# for the OPEN-ENDED "which specialist" decision; this only distinguishes
# two things Reporting itself already owns.
BILLING_SIGNALS = [
    "owe", "invoice", "bill", "balance", "paid", "payment",
    "outstanding", "overdue",
    # "cost" was a real gap: a customer asking "what's the cost of this
    # project" almost always means their own bill (they never see internal
    # cost breakdowns through this agent anyway -- that's Insight, and
    # they're never routed there), but the word wasn't in this list, so it
    # silently fell through to the money-free status view instead. Safe to
    # include: billing_response already falls through to status_response
    # when there's no invoice doc, so a mis-hit here still degrades
    # gracefully rather than answering wrong.
    "cost",
]

# BILLING_SIGNALS with "cost" removed -- used only by answer_from_data's
# empty-answer fallthrough allowlist below, never for the is_billing check
# above (that one needs "cost", see its own comment). "cost" is what makes
# "what is the material cost" (the excluded-field bug's own repro phrasing)
# match BILLING_SIGNALS -- letting it into the fallthrough allowlist would
# let an excluded-field cost question fall through to a misleading generic
# answer instead of the honest apology it must keep. Genuine billing
# vocabulary (invoice/bill/paid/owe/balance/outstanding/overdue) carries no
# such risk.
_BILLING_FALLTHROUGH_SIGNALS = [s for s in BILLING_SIGNALS if s != "cost"]

# Real gap, fixed: "when does this project start/end" used to fall into the
# generic status_response summary (completion %, task counts, schedule risk)
# regardless of what was actually asked -- that summary contains no date at
# all, so the question went unanswered. project_health.jobStartDate/
# actualJobStartDate/jobEndDate/actualJobEndDate are populated fields that
# were simply never read (confirmed against gold_query_reference.md's
# field-level schema).
#
# DEGRADED FALLBACK ONLY -- per explicit user direction, this keyword list is
# NOT the primary decision (see _is_date_question / LLMClient.classify_date_intent
# / DATE_INTENT_SYSTEM_PROMPT). A closed keyword list deciding an open-ended
# "is this about dates" question can silently misroute phrasing it wasn't
# written for -- same class of problem BILLING_SIGNALS has, except here the
# LLM call is load-bearing, not audit-only. This list only matters when the
# LLM call itself fails (model unreachable) or isn't wired (agent-level tests
# constructing ReportingAgent() directly).
DATE_SIGNALS = [
    "start date", "end date", "kickoff", "target date", "completion date",
    "when does", "when will", "when did", "how long", "due date",
]

# --- New reasoning requirement: estimate-change / schedule-move / margin ---
# See EDEN_CustomerSupport_Reasoning_Constraints.md. Checked BEFORE the
# existing rag_response/billing/date/status chain in respond() -- margin
# in particular must never reach the generic RAG path at all (it's already
# excluded from that context via _PROJECT_HEALTH_INTERNAL_ONLY_FIELDS, but
# this gives a deliberate, tested refusal message instead of relying on
# RAG's number-verification safety net to happen to reject a hallucinated
# figure). Same closed-keyword-vocabulary reasoning as BILLING_SIGNALS/
# DATE_SIGNALS -- these only distinguish views Reporting itself already owns.
MARGIN_SIGNALS = ["profit margin", "margin", "how profitable"]

# Real gap, fixed: answer_from_data's empty-answer guard (see that function's
# comment) needs to tell "a status question the RAG model just happened to
# answer with nothing" apart from "a genuinely excluded-field question
# (material/labor/tool/overhead cost, margin, ...) the model correctly had
# nothing to say about" -- falling through to status_response is safe and
# correct for the former (same customer-safe fields, live-verified against
# real Gold data) but would answer a DIFFERENT question for the latter.
# Deliberately a small, closed vocabulary -- same reasoning as BILLING_
# SIGNALS/DATE_SIGNALS above -- only distinguishing a view Reporting itself
# already owns (status_response), not a re-introduction of top-level agent
# routing.
STATUS_SIGNALS = [
    "status", "progress", "how is it going", "how's it going",
    "where do things stand", "how far along", "on track", "on schedule",
]


# Same widening as insight_agent.py's matching constants -- kept as a
# separate copy in this file (not a shared import) per the existing
# duplication between the two agents' routing, but must stay IN SYNC or
# Insight and Reporting silently diverge on which phrasings reach their
# respective Q1/Q2 builders. See insight_agent.py's _has_why_change_signal/
# ESTIMATE_TOPIC_SIGNALS comments for the live case this closes ("why the
# estimation changed?" missed the old fixed "why did/is/has/does" list and
# the exact-substring "estimate" topic check).
_WHY_CHANGE_RE = re.compile(r"\bwhy\b")
_CHANGE_STEM_RE = re.compile(r"\b(chang|mov|differ|shift)\w*\b")


def _has_why_change_signal(utterance_lower: str) -> bool:
    return bool(_WHY_CHANGE_RE.search(utterance_lower) and _CHANGE_STEM_RE.search(utterance_lower))


# Word-boundary regex, not a bare substring: found while testing this widening
# that "estimat" as a plain substring ALSO matches "estimator"/"estimators" --
# a genuine job title in this domain (construction bid estimating), not an
# edge case. Requiring a real inflection suffix (e/es/ed/ing/ion) catches
# every real form (estimate/estimates/estimated/estimating/estimation,
# schedule/schedules/scheduled/scheduling) while excluding the person-noun
# forms, which end in -or/-ors instead. See insight_agent.py's matching
# comment for the fuller history.
_ESTIMATE_STEM_RE = re.compile(r"\bestimat(?:e|es|ed|ing|ion)\b")
_SCHEDULE_STEM_RE = re.compile(r"\bschedul(?:e|es|ed|ing)\b")

ESTIMATE_TOPIC_SIGNALS = ["material cost", "material price", "price change", "cost change"]
SCHEDULE_TOPIC_SIGNALS = ["start date", "tentative date", "date move", "date moved"]

# Same fuzzy-typo widening as insight_agent.py's matching helpers -- kept in
# sync for the same reason as _has_why_change_signal above. Confirmed live:
# "why the estiamtion changed for 102380" (transposed letters) missed the
# exact-substring/stem check here AND _question_reason_domain below (this
# file's RAG-verification topic check), fell through to a generic status
# answer with no reasoning_audit entry at all.
_ESTIMATE_REFERENCE_WORDS = ("estimate", "estimates", "estimation", "estimating")
_SCHEDULE_REFERENCE_WORDS = ("schedule", "scheduling", "scheduled")


def _fuzzy_topic_word(utterance_lower: str, reference_words: tuple[str, ...]) -> bool:
    for word in re.findall(r"[a-z]+", utterance_lower):
        if len(word) < 6:
            continue
        if difflib.get_close_matches(word, reference_words, n=1, cutoff=0.85):
            return True
    return False


def _mentions_estimate_topic(utterance_lower: str) -> bool:
    return bool(_ESTIMATE_STEM_RE.search(utterance_lower)) or any(
        t in utterance_lower for t in ESTIMATE_TOPIC_SIGNALS
    ) or _fuzzy_topic_word(utterance_lower, _ESTIMATE_REFERENCE_WORDS)


def _mentions_schedule_topic(utterance_lower: str) -> bool:
    return bool(_SCHEDULE_STEM_RE.search(utterance_lower)) or any(
        t in utterance_lower for t in SCHEDULE_TOPIC_SIGNALS
    ) or _fuzzy_topic_word(utterance_lower, _SCHEDULE_REFERENCE_WORDS)


def _is_margin_question(utterance_lower: str) -> bool:
    return any(s in utterance_lower for s in MARGIN_SIGNALS)


def _is_estimate_change_question(utterance_lower: str) -> bool:
    return _has_why_change_signal(utterance_lower) and _mentions_estimate_topic(utterance_lower)


def _is_schedule_change_question(utterance_lower: str) -> bool:
    return _has_why_change_signal(utterance_lower) and _mentions_schedule_topic(utterance_lower)


def _data_answer_alternative_offer(can_view_estimate: bool, can_view_invoices: bool) -> str:
    """Real gap, fixed: see ReportingAgent.answer_from_data's comment at the
    call site -- offers only what this specific caller's own EST.REA/
    INV.REA permissions (or customer status) actually allow them to see,
    instead of a hardcoded "estimate or billing information" offer."""
    if can_view_estimate and can_view_invoices:
        return "I can help with your project's overall estimate or billing information instead."
    if can_view_estimate:
        return "I can help with your project's overall estimate instead."
    if can_view_invoices:
        return "I can help with your project's billing information instead."
    return "I can help with your project's schedule or status instead."


class ReportingAgent:
    """Role-appropriate status summaries (proposal.md agent #5): completion,
    task counts, schedule position — the "where do things stand" view every
    role including customers may see.

    Customer-safe BY QUERY SHAPE, not by prompt or display filtering: the
    Mongo projection below contains no money fields at all
    (committedSpend/outstandingInvoicesTotal/margin never leave the
    database for this agent), so internal financials structurally cannot
    reach a customer through this path — proposal.md's rule that role
    filtering "must happen at the query level" taken literally. Root-cause
    "why" answers stay Insight's job (internal roles only)."""

    def __init__(self, llm_client=None):
        self.entity_resolver = EntityResolver()
        # Optional: wired by AgentOrchestrator to the same shared LLM client
        # every other agent uses (see agent_orchestrator.__init__) -- used
        # ONLY for the billing-signal audit below, never for answering.
        # None-safe: agent-level tests constructing ReportingAgent() directly
        # simply get no audit logging, not a crash.
        self.llm_client = llm_client
        # Own instance, same pattern as InsightAgent's -- used only by the
        # Q3 margin gate (UserRoleResolver.is_margin_visible).
        self.role_resolver = UserRoleResolver()
        # Own instance too -- lets Q1/Q2 honestly reference their OWN prior
        # answer (see insight_agent.apply_reasoning_continuity).
        self.conversation_memory = ConversationMemory()

    def respond(
        self, context: SessionContext, entity_name: str | None,
        allowed_project_ids: list[str] | None = None,
        intent_utterance: str | None = None,
        entity_id: str | None = None,
    ) -> NavigationResponse | None:
        """Single entry point. Reporting's own "which of my two views"
        decision -- billing (the customer's own bill) vs. status (progress/
        schedule) -- checked against a small, closed keyword vocabulary,
        same reasoning as Navigation's internal deterministic tiers. This
        is NOT a re-introduction of hardcoded top-level agent routing:
        eden_router already made the open-ended "which specialist" decision
        via the triage LLM call; this only distinguishes two things
        Reporting itself already owns.

        `allowed_project_ids`: see EntityResolver.list_projects -- threaded
        straight through to both sub-views.

        `intent_utterance`: real bug, fixed -- when this is a reply
        continuing an open "which project?" thread, `context.utterance` is
        just the project name/answer (e.g. "DEMO-Riverside"), which carries
        NONE of the original question's billing-vs-status signal. Checking
        BILLING_SIGNALS against that bare reply silently answered a
        completely different question than the one asked ("how much do I
        owe" got a schedule-status answer instead). The orchestrator passes
        the ORIGINAL utterance here when continuing a goal; defaults to
        context.utterance for a fresh, unresolved single-turn question."""
        utterance = intent_utterance or context.utterance

        # New reasoning requirement (estimate-change / schedule-move /
        # margin) -- checked BEFORE the RAG path. Margin is already
        # excluded from RAG's context (_PROJECT_HEALTH_INTERNAL_ONLY_FIELDS)
        # but this gives a deliberate, tested refusal instead of relying on
        # RAG's number-verification safety net to happen to reject a
        # hallucinated figure. Same "which of OUR OWN views" reasoning as
        # the billing/date split below -- these only decide between things
        # Reporting itself already owns, not the top-level agent routing.
        utterance_lower = utterance.lower()
        if _is_margin_question(utterance_lower):
            response = self._margin_response(context, entity_name, allowed_project_ids, entity_id)
            if response is not None:
                return response
        elif _is_estimate_change_question(utterance_lower):
            response = self._estimate_change_response(context, entity_name, allowed_project_ids, entity_id)
            if response is not None:
                return response
        elif _is_schedule_change_question(utterance_lower):
            response = self._schedule_change_response(context, entity_name, allowed_project_ids, entity_id)
            if response is not None:
                return response
        # Real bug, fixed (PR review): a bare cost question ("what is the
        # project cost", "what's the budget") used to be intercepted HERE
        # via _total_cost_response, before the RAG path below ever ran --
        # routing it through resolve_margin_response's is_margin_visible
        # gate, same as margin. But estimatedCost is explicitly NOT in
        # _PROJECT_HEALTH_INTERNAL_ONLY_FIELDS above -- Reporting's own RAG
        # path (answer_from_data) already surfaces it to ANY caller,
        # customer included (see test_answer_from_data_strips_invented_
        # followup_offer: "what is the project cost" -> real $14,510.07 for
        # a non-admin). Intercepting the exact same phrasing here refused a
        # figure the customer's own dashboard already shows them. Removed
        # entirely -- let it fall through to the RAG path unchanged. This
        # gate is only correct for Insight (fully internal, gated off from
        # customers at the router level; admin-vs-internal-role granularity
        # there is a real, separate distinction), not for Reporting.

        # PRIMARY path: real RAG/agentic answer over the full customer-safe
        # project_health + invoice_summary data, replacing the fixed
        # billing/date/status branches below for ANY phrasing they weren't
        # written for ("latest invoice," "which was paid most recently,"
        # etc.) -- see answer_from_data's docstring for the retrieval +
        # verification design. Falls through to the original branch chain
        # on any failure (LLM unreachable, verification rejected the
        # answer, or no project resolved yet) -- same fail-open-to-
        # deterministic pattern as every other LLM-primary call here.
        rag_response = self.answer_from_data(context, entity_name, allowed_project_ids, entity_id, utterance)
        if rag_response is not None:
            return rag_response

        utterance_lower = utterance.lower()
        is_billing = any(signal in utterance_lower for signal in BILLING_SIGNALS)
        self._maybe_audit_billing_signal(utterance, is_billing)
        if is_billing:
            response = self.billing_response(context, entity_name, allowed_project_ids, entity_id)
            if response is not None:
                return response
            # Billing data unavailable for this project -- fall through to
            # the status view rather than a bare "can't answer" failure.

        is_date_question = self._is_date_question(utterance, utterance_lower)
        if is_date_question:
            response = self.date_response(context, entity_name, allowed_project_ids, entity_id)
            if response is not None:
                return response
            # No schedule row for this project -- fall through to the
            # generic status view rather than a bare "can't answer" failure,
            # same pattern as the billing fallback above.
        return self.status_response(context, entity_name, allowed_project_ids, entity_id)

    def _is_date_question(self, utterance: str, utterance_lower: str) -> bool:
        """PRIMARY decision via LLMClient.classify_date_intent (see
        DATE_INTENT_SYSTEM_PROMPT) -- per explicit user direction, DATE_SIGNALS
        alone can't reliably generalize to phrasing it wasn't written for.
        Falls back to the keyword list only when the LLM call itself fails
        (unreachable/malformed output) or isn't wired (agent-level tests
        constructing ReportingAgent() directly), same fail-open-to-deterministic
        pattern as every other LLM-primary call in this codebase."""
        if self.llm_client is not None:
            result = self.llm_client.classify_date_intent(utterance)
            if result is not None:
                return bool(result.get("is_date_question"))
        return any(signal in utterance_lower for signal in DATE_SIGNALS)

    def _maybe_audit_billing_signal(self, utterance: str, keyword_said_billing: bool) -> None:
        """Instrumentation only -- see BILLING_SIGNAL_AUDIT_SAMPLE_RATE.
        Never raises, never changes what respond() returns."""
        if self.llm_client is None or random.random() >= BILLING_SIGNAL_AUDIT_SAMPLE_RATE:
            return
        try:
            llm_check = self.llm_client.classify_billing_intent(utterance)
        except Exception as e:
            logger.warning(f"Billing-signal audit call failed: {e}")
            return
        if llm_check is None:
            return
        llm_said_billing = bool(llm_check.get("is_billing"))
        if llm_said_billing != keyword_said_billing:
            logger.warning(
                "BILLING_SIGNALS disagreement: keyword=%s llm=%s utterance=%r (%s)",
                keyword_said_billing, llm_said_billing, utterance,
                llm_check.get("reasoning"),
            )

    def billing_response(
        self, context: SessionContext, entity_name: str | None,
        allowed_project_ids: list[str] | None = None,
        entity_id: str | None = None,
    ) -> NavigationResponse | None:
        """The customer's OWN bill -- what they've been billed, received
        credit for, and currently owe. Different from Insight's internal
        cost questions (what FIELDCRAFT is paying vendors/labor): this is the
        customer's own financial relationship, not the company's internal
        cost structure, so it's safe for every role including customers.
        Same outcome contract as status_response (list/disambiguate/None/
        answer). `entity_id`: exact projectId from page context, takes
        precedence over entity_name (see variance_response)."""
        if not entity_id and not entity_name:
            # Per explicit user direction (reversing an earlier aggregate-
            # dashboard shortcut here): ANY project-related question naming
            # no project -- billing included -- must ask "which project?"
            # with an actual pick-list, consistently with status_response/
            # date_response, never silently aggregate across every project
            # instead of asking.
            projects = self.entity_resolver.list_projects(
                context.tenantId, limit=_project_list_limit(context.platform),
                allowed_project_ids=allowed_project_ids,
            )
            return projects_disambiguation(projects)

        if entity_id:
            matches = self.entity_resolver.resolve_by_id(
                entity_id, context.tenantId, allowed_project_ids=allowed_project_ids
            )
        else:
            matches = self.entity_resolver.resolve_project(
                entity_name, context.tenantId, allowed_project_ids=allowed_project_ids
            )
        if len(matches) == 0:
            return None
        if len(matches) > 1:
            limit = _project_list_limit(context.platform)
            return projects_disambiguation(matches if limit == 0 else matches[:limit])

        match = matches[0]

        # AB#30855: this agent's customer-safe-by-query-shape design (see
        # class docstring) is correct for an actual customer -- it's their
        # own bill, always visible. But any INTERNAL (non-customer) caller
        # can also reach this method (Insight/Reporting is picked by triage
        # confidence, not exclusively by role -- see rbac-data-scoping), and
        # until now nothing here checked whether that internal caller
        # actually holds INV.REA. Confirmed live: Labor holds neither
        # INV.REA nor INV.PAY in the real role_permission seed, yet EDEN
        # disclosed totalBilled/totalReceived/totalOutstanding to a Labor
        # user with no check at all. Gate ONLY the internal path -- a
        # customer is never asked for INV.REA, matching how the real app's
        # own customer portal shows this data unconditionally to its owner.
        if not self.role_resolver.is_customer(context.tenantId, context.userId):
            permissions = self.role_resolver.get_permissions(context.tenantId, context.userId)
            if "INV.REA" not in permissions:
                return NavigationResponse(
                    type="guidance",
                    message=(
                        "I'm not able to share invoice/billing information for "
                        f"{match.project_name} -- that isn't available at your "
                        "access level."
                    ),
                    project=ProjectRef(id=match.project_uid, name=match.project_name, status=match.status),
                )

        try:
            collection = get_collection(INVOICE_SUMMARY_COLLECTION)
            doc = collection.find_one(
                {
                    "tenantId": context.tenantId, "projectId": match.project_uid,
                    "isDeleted": {"$ne": True},
                },
                {
                    "totalBilled": 1, "totalReceived": 1,
                    "totalOutstanding": 1, "_id": 0,
                },
            )
            if not doc:
                return None

            total_billed = float(doc["totalBilled"])
            total_received = float(doc["totalReceived"])
            total_outstanding = float(doc["totalOutstanding"])
        except Exception as e:
            logger.error(
                f"Reporting billing query failed for projectId="
                f"{match.project_uid!r}: {e}"
            )
            return None

        # Traceability: which project (by id) and the exact raw field
        # values pulled from Mongo, so the final chat answer can be
        # manually cross-checked against Gold directly -- e.g.
        # `db.invoice_summary.find({"projectId": "..."})` should show
        # these same numbers verbatim.
        logger.info(
            "Reporting billing data: project=%r projectId=%s -- "
            "totalBilled=%s totalReceived=%s totalOutstanding=%s",
            match.project_name, match.project_uid,
            total_billed, total_received, total_outstanding,
        )

        # A pre-contract project (see PRE_CONTRACT_STATUSES) reporting all
        # zero totals hasn't "settled its bill" -- no invoice exists yet to
        # bill against. Saying so plainly beats "$0 billed, $0 outstanding",
        # which reads as a real, paid-off balance rather than "nothing to
        # see here yet" (observed live for a project in awaitingEstimation).
        if (
            total_billed == 0 and total_received == 0 and total_outstanding == 0
            and match.status in PRE_CONTRACT_STATUSES
        ):
            status_label = PRE_CONTRACT_STATUS_LABELS.get(match.status, match.status)
            message = (
                f"{match.project_name} doesn't have an invoice yet — it's "
                f"still in {status_label}. I'll let you know once billing "
                f"data is available."
            )
            return NavigationResponse(
                type="guidance", message=message,
                project=ProjectRef(id=match.project_uid, name=match.project_name, status=match.status),
            )

        # Per explicit user direction: the LLM composes this message end to
        # end (including how to phrase a genuinely zero figure) via
        # LLMClient.compose_billing_message, tried first here. The if/elif
        # chain below is the deterministic FALLBACK -- used when
        # self.llm_client isn't wired (agent-level tests constructing
        # ReportingAgent() directly) or the composition call fails/fails
        # its own real-figure verification. Real gap this fallback (and
        # the LLM composer) both fix: a flat "billed $X, received $Y,
        # outstanding $Z" template reads oddly whenever one of the three
        # is genuinely zero -- "received $0" sounds like a data problem,
        # not "nothing yet"; "outstanding $0" on a real bill reads as
        # unremarkable when it actually means "fully paid."
        message = None
        if self.llm_client is not None:
            message = self.llm_client.compose_billing_message(
                match.project_name, total_billed, total_received, total_outstanding
            )
        if message is None:
            if total_billed == 0:
                # Not caught by the PRE_CONTRACT_STATUSES branch above (an
                # unlisted/unexpected status still reporting zero activity)
                # -- same honest "nothing yet" message, without guessing at
                # a specific project-status label this time.
                message = f"{match.project_name} hasn't been billed anything yet."
            elif total_outstanding == 0:
                message = (
                    f"{match.project_name} has been billed ${total_billed:,.0f} "
                    f"and it's fully paid — nothing outstanding."
                )
            elif total_received == 0:
                message = (
                    f"{match.project_name} has been billed ${total_billed:,.0f} "
                    f"with nothing received yet — the full ${total_outstanding:,.0f} "
                    f"is outstanding."
                )
            else:
                message = (
                    f"{match.project_name}: billed ${total_billed:,.0f}, "
                    f"received ${total_received:,.0f}, "
                    f"outstanding ${total_outstanding:,.0f}."
                )
        # Real bug, fixed: without `project` set here, an answer resolved
        # via PAGE CONTEXT (entity_id, not a spoken name -- the common case
        # of viewing a project's page and just asking) gave the
        # orchestrator no way to know which project this was about, so
        # AgentOrchestrator._reporting_node's set_last_project(...) call
        # (gated on `entity_name`) never fired -- leaving "last discussed
        # project" stale and starving the project-context-switch
        # confirmation of anything to compare against.
        return NavigationResponse(
            type="guidance", message=message,
            project=ProjectRef(id=match.project_uid, name=match.project_name, status=match.status),
        )

    def answer_from_data(
        self, context: SessionContext, entity_name: str | None,
        allowed_project_ids: list[str] | None, entity_id: str | None,
        utterance: str,
    ) -> NavigationResponse | None:
        """Real RAG/agentic answer over the FULL customer-safe
        project_health + invoice_summary data -- replaces the fixed
        billing_response/date_response/status_response branches for any
        question phrasing they weren't specifically written for ("latest
        invoice," "which was paid most recently," etc.). See
        PROJECT_DATA_ANSWER_SYSTEM_PROMPT's docstring for the full design.

        Two-part safety guarantee, both required:
        1. The retrieved data itself never contains internal-only fields
           (_PROJECT_HEALTH_INTERNAL_ONLY_FIELDS) -- role-safety is a data
           preparation step, never left to the model to avoid on its own.
        2. Every number/date-looking token in the model's answer is
           verified to already appear in the source data (_number_tokens,
           the same verification compose_billing_message/rephrase already
           use for a single field, generalized to arbitrary retrieved
           JSON). An answer that fails this check is discarded --
           returns None, same as any other failure here -- never shown to
           the user.

        Returns None (falls through to the old deterministic branches) when:
        no project is resolved yet (caller's disambiguation still needed),
        no LLM client is wired, the model call fails, or verification
        rejects the answer. Never a bare crash -- this is strictly additive
        on top of the proven fallback chain, not a replacement for it."""
        if not entity_id and not entity_name:
            return None  # let the existing chain's disambiguation handle this
        if self.llm_client is None:
            return None
        utterance_lower = utterance.lower()

        if entity_id:
            matches = self.entity_resolver.resolve_by_id(
                entity_id, context.tenantId, allowed_project_ids=allowed_project_ids
            )
        else:
            matches = self.entity_resolver.resolve_project(
                entity_name, context.tenantId, allowed_project_ids=allowed_project_ids
            )
        if len(matches) != 1:
            return None  # no match or ambiguous -- old chain's disambiguation handles both

        match = matches[0]
        try:
            health_doc = get_collection(PROJECT_HEALTH_COLLECTION).find_one(
                {
                    "tenantId": context.tenantId, "projectId": match.project_uid,
                    "isDeleted": {"$ne": True},
                }, {"_id": 0}
            ) or {}
            invoice_doc = get_collection(INVOICE_SUMMARY_COLLECTION).find_one(
                {
                    "tenantId": context.tenantId, "projectId": match.project_uid,
                    "isDeleted": {"$ne": True},
                }, {"_id": 0}
            ) or {}
        except Exception as e:
            logger.error(f"RAG data fetch failed for projectId={match.project_uid!r}: {e}")
            return None

        safe_health = {
            k: v for k, v in health_doc.items()
            if k not in _PROJECT_HEALTH_INTERNAL_ONLY_FIELDS
        }
        safe_invoice = {
            k: v for k, v in invoice_doc.items()
            if k not in _INVOICE_SUMMARY_INTERNAL_ONLY_FIELDS
        }

        # AB#30855: estimatedCost was deliberately UN-redacted above (see
        # _PROJECT_HEALTH_INTERNAL_ONLY_FIELDS's comment) because it's the
        # CUSTOMER's own quoted amount, visible on their own dashboard --
        # that reasoning only holds for an actual customer. The same is
        # true of the whole billing block: safe for a customer (their own
        # bill) by this method's own design, but not vetted for every
        # OTHER role that can reach this RAG path via triage (see
        # rbac-data-scoping). Confirmed live: Labor holds neither EST.REA
        # nor INV.REA in the real role_permission seed. Redact both for any
        # non-customer caller who doesn't hold the matching real permission
        # -- same codes billing_response/resolve_margin_response already
        # use, not invented here.
        is_customer_caller = self.role_resolver.is_customer(context.tenantId, context.userId)
        if not is_customer_caller:
            permissions = self.role_resolver.get_permissions(context.tenantId, context.userId)
            if "EST.REA" not in permissions:
                safe_health.pop("estimatedCost", None)
            if "INV.REA" not in permissions:
                safe_invoice = {}
        else:
            permissions = []

        # Real gap, fixed: every rejection/fallback branch below used to
        # hardcode "your project's overall estimate or billing information
        # instead" as the "here's what I CAN help with" offer, regardless of
        # whether this specific caller could see either -- the same
        # anti-pattern insight_agent.py's _available_alternative_offer
        # already fixed for the margin/total-cost denials, just never
        # applied to this RAG path. Confirmed live: a Labor user (no
        # EST.REA, no INV.REA) asking "what the estimate is over budget"
        # got back the exact hardcoded offer text above -- pointing at two
        # things they also can't see. A customer always sees their own
        # estimate/billing (see the redaction block above), so both are
        # unconditionally true for a customer caller.
        can_view_estimate = is_customer_caller or "EST.REA" in permissions
        can_view_invoices = is_customer_caller or "INV.REA" in permissions

        data = {"project": safe_health, "billing": safe_invoice}

        try:
            answer = self.llm_client.answer_from_project_data(utterance, data)
            # Real bug, fixed: confirmed live -- "what is the material used
            # for this project" (materialCost correctly excluded from
            # `data`) got the model to produce a genuinely empty response.
            # answer_from_project_data now distinguishes this ("" -- the
            # call succeeded, the model had nothing to say) from a true
            # call failure (None -- exception/malformed output), same
            # contract shape as extract_project_name's. Treating an empty
            # answer the same as a hard failure sent it through respond()'s
            # ENTIRE fallback chain (billing/date/status_response) to an
            # unrelated status blurb that read as though the material
            # question was answered -- the same "falls through to a
            # misleading answer" bug already fixed for the excluded-field/
            # cross-domain-reason/self-referential-leak/numeric-fabrication
            # rejections below, just one level earlier (before the model
            # even produced text to check, rather than after).
            #
            # Real bug, fixed (sibling to the one above): that fix was
            # unconditional -- it blocked EVERY empty answer from falling
            # through, not just the excluded-field case it was written for.
            # Confirmed live: "what is the project status" / "what is the
            # status of project X" also produced an empty LLM answer (no
            # clear reason why this specific phrasing stalls the model), and
            # the hard apology above shipped instead of ever trying status_
            # response -- even though status_response sits right below in
            # this same fallback chain, reads the SAME customer-safe fields
            # (completionPct/statusTimeline/scheduleRisk), was live-verified
            # against real Gold data for two different projects, and would
            # have answered correctly both times.
            #
            # Only let it fall through (return None) when the utterance
            # itself is clearly a STATUS_SIGNALS question -- an allowlist,
            # not a blocklist. A blocklist keyed off _INTERNAL_FIELD_TOPIC_
            # PHRASES was tried first and rejected: "what is the material
            # USED for this project" (the ORIGINAL bug's own repro phrasing,
            # still covered by TestEmptyRagAnswerRefusalDoesNotFallThroughTo
            # Status) doesn't literally contain any of those cost-phrase
            # strings either, so a blocklist let it fall through too and
            # broke that exact regression test. An allowlist is the safer
            # direction: everything not explicitly recognized as a status
            # question keeps the hard apology, same as before this fix.
            #
            # Sibling gap, same class, found live: "what is the invoiced
            # amount" and "what is the start date" ALSO produced an empty
            # LLM answer and shipped the hard apology, even though
            # billing_response/date_response sit right below in this same
            # fallback chain and read real, populated fields --
            # project_health.outstandingInvoicesTotal confirmed =2449.0 for
            # the exact project the invoice case reproduced against, via
            # InsightAgent's own variance_response query moments earlier in
            # the same session; jobStartDate/jobEndDate are date_response's
            # own documented fields (see DATE_SIGNALS' comment above).
            # _BILLING_FALLTHROUGH_SIGNALS is BILLING_SIGNALS with "cost"
            # deliberately removed -- "cost" is what makes "what is the
            # material cost" (the ORIGINAL excluded-field bug's repro
            # phrasing) match BILLING_SIGNALS, and this allowlist must
            # never let an excluded-field cost question fall through to a
            # misleading generic answer; only genuine billing vocabulary
            # (invoice/bill/paid/owe/balance/outstanding/overdue) is safe
            # to add here. DATE_SIGNALS carries no such risk -- none of its
            # words overlap any excluded-field phrasing.
            if answer == "" and any(
                s in utterance_lower
                for s in STATUS_SIGNALS + _BILLING_FALLTHROUGH_SIGNALS + DATE_SIGNALS
            ):
                return None
            if answer == "":
                return NavigationResponse(
                    type="guidance",
                    message=(
                        "I don't have that specific detail available for "
                        "this project. "
                        + _data_answer_alternative_offer(can_view_estimate, can_view_invoices)
                    ),
                    project=ProjectRef(id=match.project_uid, name=match.project_name, status=match.status),
                )
            if not answer or not isinstance(answer, str):
                return None

            # Verification: every number/percentage/dollar-figure the
            # model's answer contains must already exist somewhere in the
            # source data -- an answer inventing or miscounting a figure is
            # discarded, not shown. This is the guardrail that makes "let
            # the model read the data and answer freely" safe rather than
            # a hallucination risk.
            #
            # Real bug, fixed: _number_tokens keeps thousands-separator
            # commas as-is (by design, for compose_billing_message's
            # comma-formatted-both-sides comparison) -- but here the model
            # naturally writes "$8,000" while raw Mongo data stringifies as
            # bare "8000" (no comma), so a correct answer was being
            # rejected as "fabricated." Stripping commas on BOTH sides
            # before comparing only makes matching MORE lenient (a superset
            # of what matched before), never less -- safe to do locally
            # without changing _number_tokens' shared behavior elsewhere.
            source_numbers = {n.replace(",", "") for n in _number_tokens(str(data))}
            # Real bug, fixed: Mongo stores some figures at 3+ decimal
            # places (e.g. totalOutstanding: 14800.271), but the natural,
            # correct way to say a dollar figure out loud is 2 decimals
            # ("$14,800.27"). That's a different token string than the raw
            # source value, so a factually-correct, rounded answer was
            # being rejected as "fabricated." Adding the 2-decimal and
            # whole-number roundings of every source figure only makes
            # matching MORE lenient (a superset of exact matches), never
            # less -- it never accepts a figure that doesn't trace back to
            # a real source value.
            for token in list(source_numbers):
                try:
                    value = float(token)
                except ValueError:
                    continue
                if "." in token:
                    source_numbers.add(f"{value:.2f}")
                    source_numbers.add(f"{value:.0f}")
            answer_numbers = {n.replace(",", "") for n in _number_tokens(answer)}
            if not answer_numbers.issubset(source_numbers):
                logger.warning(
                    "RAG answer rejected -- contains figures not present in source data: "
                    "answer_numbers=%s source_numbers=%s answer=%r",
                    answer_numbers - source_numbers, source_numbers, answer,
                )
                # Real bug, fixed (PR review, Raja): same fallthrough-to-a-
                # misleading-answer bug 2727a8f fixed for the sibling
                # cross-domain-reason and self-referential-leak rejections --
                # this one, the numeric-fabrication guard, was missed in that
                # pass. A fabricated figure must not silently discard the
                # turn and let it land on an unrelated deterministic answer
                # that reads as though the original question was answered.
                return NavigationResponse(
                    type="guidance",
                    message=(
                        "I don't have a verified figure for that on file -- "
                        "I can loop in your project manager for the exact "
                        "number."
                    ),
                    project=ProjectRef(id=match.project_uid, name=match.project_name, status=match.status),
                )

            # Domain grounding: a cause the model states must belong to the
            # domain the question actually asked about, not merely appear
            # SOMEWHERE in the document -- see _find_ungrounded_cross_domain_
            # reason's docstring for the live case this closes (an estimate-
            # change question answered with a schedule reason, "SAVE_TASKS").
            # The numeric check above only verifies figures; a reason/cause
            # string passes it trivially since it isn't numeric at all.
            question_domain = _question_reason_domain(utterance_lower)
            cross_domain_reason = _find_ungrounded_cross_domain_reason(answer, data, question_domain)
            if cross_domain_reason is not None:
                logger.warning(
                    "RAG answer rejected -- cited a %r reason for a %r question: "
                    "reason=%r answer=%r",
                    "schedule" if cross_domain_reason in _grounded_reason_strings(data, "schedule") else "other",
                    question_domain, cross_domain_reason, answer,
                )
                # Real bug, fixed (PR review, Raja): this rejection returned
                # None like the excluded-field one used to before db4066f --
                # same "falls through to an unrelated deterministic answer"
                # bug, just on a different rejection path. A real, grounded
                # reason DOES exist in the data here (just for the wrong
                # domain), so this deliberately doesn't claim "no reason on
                # file" -- it reuses insight_agent.py's own "not confirmed"
                # phrasing for whichever domain was actually asked about
                # (estimate/schedule have an established phrase each; "status"
                # -- the third real _REASON_DOMAIN_FIELDS key -- falls back to
                # a generic one), for tonal consistency across both reasoning
                # paths rather than inventing new wording here.
                _DOMAIN_UPDATE_NOUN = {"estimate": "estimate", "schedule": "schedule"}
                updated_noun = _DOMAIN_UPDATE_NOUN.get(question_domain, "project")
                return NavigationResponse(
                    type="guidance",
                    message=(
                        f"Your {updated_noun} was updated, but I don't have a "
                        f"confirmed reason on file for that specific change "
                        f"-- I can loop in your project manager for detail."
                    ),
                    project=ProjectRef(id=match.project_uid, name=match.project_name, status=match.status),
                )

            # Excluded-field grounding: confirmed live -- asked "what is the
            # tool cost and material cost" (both fields are in
            # _PROJECT_HEALTH_INTERNAL_ONLY_FIELDS and so were NEVER SENT to
            # the model at all), the answer confidently stated "$194.62" for
            # BOTH -- neither matches either real value (materialCost=150.75,
            # toolCost=0.0). The number-verification check above only asks
            # "does this digit-string appear ANYWHERE in the data" -- it
            # passed here purely because "194.62" happens to appear,
            # coincidentally, attached to a completely unrelated field
            # elsewhere in the document. This catches the shape the numeric
            # check structurally can't: the answer discusses a cost CONCEPT
            # that isn't even a key in the data the model was given, meaning
            # whatever number it stated for it cannot be grounded, no matter
            # what that number happens to be.
            excluded_field = _answer_discusses_excluded_field(answer.lower(), data)
            if excluded_field is not None:
                logger.warning(
                    "RAG answer rejected -- discusses excluded field %r never "
                    "sent to the model: answer=%r", excluded_field, answer,
                )
                # Real bug, fixed: returning None here (like every other
                # rejection above) sends this back through respond()'s
                # fallback chain -- is_billing/_is_date_question/
                # status_response -- which happily answers a DIFFERENT
                # question instead. Confirmed live: "what is the material
                # cost" fell through to a full billing breakdown ("The
                # project has been billed $19,657...") -- not wrong, just
                # not what was asked, and worse, it reads as though the
                # question WAS answered. Unlike the other rejections above
                # (a numeric fabrication, a cross-domain reason, a self-
                # reference), this one specifically means "the user asked
                # about something this role can never see" -- the SAME
                # category Q3's margin gate already has a direct,
                # honest refusal for (see resolve_margin_response). Return
                # that refusal directly instead of falling through, same
                # tone as the margin refusal for consistency.
                return NavigationResponse(
                    type="guidance",
                    message=(
                        "I'm not able to share that specific cost breakdown "
                        "-- that level of detail isn't available at your "
                        "access level. "
                        + _data_answer_alternative_offer(can_view_estimate, can_view_invoices)
                    ),
                    project=ProjectRef(id=match.project_uid, name=match.project_name, status=match.status),
                )
        except Exception as e:
            # Never let a malformed model response or verification error
            # crash the request -- degrade to the old deterministic chain,
            # same as every other LLM call site in this codebase.
            logger.warning(f"RAG answer/verification failed unexpectedly: {e}")
            return None

        # Real bug, fixed: confirmed live -- despite the prompt instruction
        # against it, the model still sometimes ends its own answer with an
        # invented follow-up offer ("Would you like to see the cost
        # breakdown page?", a page that doesn't even exist in the registry).
        # That sentence has NO backing suggestion (the real one, if any, is
        # attached separately and safely by AgentOrchestrator.
        # _attach_page_suggestion, permission-checked), so confirming it
        # with "yes" has nothing to resolve and dead-ends. The number-
        # verification guard above only checks numeric tokens -- it can't
        # catch this kind of invented text. Same lesson as everywhere else
        # in this codebase: an instruction alone isn't a guarantee, strip
        # deterministically instead of trusting the model to comply.
        stripped_answer = re.sub(
            r"\s*(Would|Do) you (like|want) (to|me to)?\s*(see|check|view|go to|visit)\b[^.?!]*\?\s*$",
            "", answer, flags=re.IGNORECASE,
        ).rstrip()
        # Safety: only apply the strip if real content remains -- if the
        # model's ENTIRE answer was just the invented offer (no fact stated
        # before it, an edge case, not the normal shape), stripping would
        # leave nothing to show the user. Showing the unstripped answer is
        # still better than an empty response.
        if stripped_answer:
            answer = stripped_answer

        # Real bug, fixed: confirmed live -- despite the prompt already
        # explicitly saying "Never say the word 'JSON' or refer to 'the
        # data' as a technical object" (PROJECT_DATA_ANSWER_SYSTEM_PROMPT),
        # the model answered "The JSON does not provide specific
        # information about changes to material or tool cost estimations."
        # -- describing its own input format to the user, an internal
        # implementation detail (that this answer is generated by feeding
        # it a JSON blob) that should never surface. The CONTENT of that
        # particular answer was accurate (those fields really are
        # excluded), so this is a wording leak, not a factual one -- but
        # the same "instruction alone isn't a guarantee" rule as the
        # follow-up-offer strip above applies: reject rather than trust
        # compliance. No safe way to strip just the offending clause
        # without risking a broken sentence, so the whole answer is
        # discarded in favor of the deterministic fallback chain.
        if _SELF_REFERENTIAL_RE.search(answer):
            logger.warning(
                "RAG answer rejected -- describes its own input format: "
                "answer=%r", answer,
            )
            # Real bug, fixed (PR review, Raja): same "falls through to an
            # unrelated deterministic answer" gap db4066f closed for the
            # excluded-field rejection, just on this rejection path instead.
            # The underlying content here is usually a legitimate "that
            # detail isn't in what I have" answer, just phrased as a leak
            # ("the JSON doesn't provide...") -- reusing the excluded-field
            # refusal's closing sentence keeps the tone consistent.
            return NavigationResponse(
                type="guidance",
                message=(
                    "I don't have that specific detail available right now. "
                    + _data_answer_alternative_offer(can_view_estimate, can_view_invoices)
                ),
                project=ProjectRef(id=match.project_uid, name=match.project_name, status=match.status),
            )

        logger.info(
            "RAG answer for project=%r projectId=%s: %r",
            match.project_name, match.project_uid, answer,
        )

        # Per explicit user direction: when the user is already ON this
        # project's own page, highlight where the answer lives on screen,
        # not just answer in chat -- same pattern as date_response's
        # on-page highlight. Only highlights a value that is BOTH a real
        # invoiceNo from the already-verified-safe source data AND actually
        # present in the model's (already fabrication-checked) answer text
        # -- never a guessed selector, and never something the answer
        # didn't actually say.
        highlight_targets = None
        insight = None
        on_this_project_page = (
            isinstance(context.currentEntity, dict)
            and context.currentEntity.get("type") == "project"
            and str(context.currentEntity.get("id")) == str(match.project_uid)
        )
        # Real gap, fixed: the invoice number / amount highlight used to fire
        # ONLY when on_this_project_page -- but the Customer Invoices page
        # (/customerInvoices) isn't project-scoped, so the frontend sends no
        # currentEntity for it (see useSessionContext.deriveCurrentEntity),
        # leaving the gate permanently shut there even though that page is
        # exactly where invoice numbers and amounts render. currentRoute IS
        # always sent, so also allow the invoice-value highlights when the
        # user is on an invoice/billing-type route. Safe to be liberal:
        # PageSpotlight searches only the live DOM, excludes EDEN's own chat
        # panel, and degrades to a small dismissable toast when the text
        # isn't found -- a highlight that finds nothing costs nothing.
        current_route = (context.currentRoute or "").lower()
        on_invoice_page = "invoice" in current_route
        if on_this_project_page or on_invoice_page:
            for inv in (safe_invoice.get("invoices") or []):
                invoice_no = inv.get("invoiceNo")
                if invoice_no and invoice_no in answer:
                    highlight_targets = [invoice_no]
                    insight = "You'll find this in the Invoice No column."
                    break
            # Invoice / billing AMOUNTS -- confirmed live the user asks "what's
            # the invoice amount" and nothing highlights. The page renders
            # amounts as toLocaleString currency ("$14,800.27"); highlight the
            # dollar figure(s) the answer actually states, each of which the
            # number-verification pass above already proved traces back to
            # real source data. Formatted three ways the page might render
            # them (with/without the $, comma-grouped) so PageSpotlight can
            # match whichever the component used.
            if highlight_targets is None:
                money = re.findall(r"\$\s?[\d,]+(?:\.\d{1,2})?", answer)
                if money:
                    variants: list[str] = []
                    for m in money:
                        bare = m.lstrip("$").strip()
                        for v in (m.strip(), f"${bare}", bare):
                            if v not in variants:
                                variants.append(v)
                    highlight_targets = variants
                    insight = "The amount is shown on this page."
        if highlight_targets is None and on_this_project_page:
            # Schedule dates -- this RAG path answers date questions FIRST
            # (respond() tries it before date_response), so date_response's
            # own on-page highlight never runs for most real date questions.
            # Only highlight a date VALUE that's both real source data and
            # literally present in the (already-verified) answer text -- sent
            # in every on-screen spelling (see _date_highlight_variants).
            for field in ("actualJobStartDate", "jobStartDate", "actualJobEndDate", "jobEndDate"):
                raw = safe_health.get(field)
                if not raw:
                    continue
                if _format_date(raw) in answer:
                    highlight_targets = _date_highlight_variants(raw)
                    insight = "The date pills near the top of this page show exactly that."
                    break
            # Site address -- confirmed real on-screen label ("Site") and
            # value (see Screenshot 2026-07-21 170959.png), same safe-match.
            if highlight_targets is None:
                street = (
                    safe_health.get("project", {})
                    .get("customerAsset", {})
                    .get("address")
                )
                if street and street in answer:
                    highlight_targets = [street]
                    insight = "The Site field near the top of this page shows the full address."

        return NavigationResponse(
            type="guidance", message=answer,
            project=ProjectRef(id=match.project_uid, name=match.project_name, status=match.status),
            insight=insight,
            highlightTargets=highlight_targets,
        )

    def date_response(
        self, context: SessionContext, entity_name: str | None,
        allowed_project_ids: list[str] | None = None,
        entity_id: str | None = None,
    ) -> NavigationResponse | None:
        """"When does this start/end" -- project_health.jobStartDate/
        actualJobStartDate/jobEndDate/actualJobEndDate, populated fields
        that respond()'s generic status_response never read (see
        DATE_SIGNALS). Same outcome contract as status_response/
        billing_response: list/disambiguate/None/answer. Returns None (not
        a bare failure) when the project has no schedule row at all --
        caller falls back to the generic status view, same pattern as
        billing_response's own fallback."""
        if not entity_id and not entity_name:
            projects = self.entity_resolver.list_projects(
                context.tenantId, limit=_project_list_limit(context.platform),
                allowed_project_ids=allowed_project_ids,
            )
            return projects_disambiguation(projects)

        if entity_id:
            matches = self.entity_resolver.resolve_by_id(
                entity_id, context.tenantId, allowed_project_ids=allowed_project_ids
            )
        else:
            matches = self.entity_resolver.resolve_project(
                entity_name, context.tenantId, allowed_project_ids=allowed_project_ids
            )
        if len(matches) == 0:
            return None
        if len(matches) > 1:
            limit = _project_list_limit(context.platform)
            return projects_disambiguation(matches if limit == 0 else matches[:limit])

        match = matches[0]
        try:
            collection = get_collection(PROJECT_HEALTH_COLLECTION)
            doc = collection.find_one(
                {
                    "tenantId": context.tenantId, "projectId": match.project_uid,
                    "isDeleted": {"$ne": True},
                },
                {
                    "jobStartDate": 1, "actualJobStartDate": 1,
                    "jobEndDate": 1, "actualJobEndDate": 1, "_id": 0,
                },
            )
            if not doc:
                return None
            job_start = doc.get("jobStartDate")
            actual_start = doc.get("actualJobStartDate")
            job_end = doc.get("jobEndDate")
            actual_end = doc.get("actualJobEndDate")
        except Exception as e:
            logger.error(
                f"Reporting date query failed for projectId="
                f"{match.project_uid!r}: {e}"
            )
            return None

        # No schedule row at all -- distinct from "dates are null on a real
        # schedule row" (still answerable, just partially). Let the caller
        # fall back to the generic status view instead of a bare "unknown."
        if job_start is None and actual_start is None and job_end is None and actual_end is None:
            return None

        logger.info(
            "Reporting date data: project=%r projectId=%s -- "
            "jobStartDate=%s actualJobStartDate=%s jobEndDate=%s actualJobEndDate=%s",
            match.project_name, match.project_uid,
            job_start, actual_start, job_end, actual_end,
        )

        parts = []
        if actual_start:
            parts.append(f"started {_format_date(actual_start)}")
        elif job_start:
            parts.append(f"planned to start {_format_date(job_start)}")
        if actual_end:
            parts.append(f"finished {_format_date(actual_end)}")
        elif job_end:
            parts.append(f"targeted to finish {_format_date(job_end)}")

        summary = ", ".join(parts) if parts else "no schedule dates recorded yet"
        message = f"{match.project_name}: {summary}."

        # Sanity check on the SAME two values just used to build the summary
        # above (actual takes precedence over planned, matching the parts
        # logic). An end date genuinely equal to the start is valid (a
        # zero-duration record) -- only flag end BEFORE start, an internally
        # inconsistent source record. Confirmed live: project_id=1129,
        # tenant tn88351775 has had jobEndDate before jobStartDate on the
        # same day since the record was created -- Silver/Gold faithfully
        # replicated a source Postgres defect, not a pipeline bug, so this
        # can recur for any project. Presenting it as a confident answer
        # would put an unflagged wrong answer in front of a customer; the
        # fix is a caveat in the response text itself, not a log line.
        effective_start = _parse_datetime_full(actual_start or job_start) if (actual_start or job_start) else None
        effective_end = _parse_datetime_full(actual_end or job_end) if (actual_end or job_end) else None
        if effective_start is not None and effective_end is not None and effective_end < effective_start:
            message += (
                " Heads up — the end date on this record is before the start date, "
                "which looks like a data issue, so treat this as unverified rather than confirmed."
            )

        # Per explicit user direction: when the user is already ON this
        # exact project's own page (not just asking about it from
        # elsewhere), highlight where the answer actually lives on screen
        # instead of only answering in chat -- confirmed live, the date
        # pills render the bare formatted date with no separate text label
        # (see Screenshot 2026-07-21 181251.png), so the date VALUE itself
        # is the real, matchable on-screen text, not a guessed field name.
        on_this_project_page = (
            isinstance(context.currentEntity, dict)
            and context.currentEntity.get("type") == "project"
            and str(context.currentEntity.get("id")) == str(match.project_uid)
        )
        highlight_targets = None
        insight = None
        if on_this_project_page:
            highlight_targets = [
                variant
                for d in (actual_start or job_start, actual_end or job_end) if d
                for variant in _date_highlight_variants(d)
            ] or None
            if highlight_targets:
                insight = "The date pills near the top of this page show exactly that."

        return NavigationResponse(
            type="guidance", message=message,
            project=ProjectRef(id=match.project_uid, name=match.project_name, status=match.status),
            insight=insight,
            highlightTargets=highlight_targets,
        )

    def status_response(
        self, context: SessionContext, entity_name: str | None,
        allowed_project_ids: list[str] | None = None,
        entity_id: str | None = None,
    ) -> NavigationResponse | None:
        """Same outcome contract as InsightAgent.variance_response:
        - No name/id given -> list the tenant's projects and ask which one.
        - Ambiguous name (>1 match) -> disambiguate among those matches.
        - No match / missing data / Mongo failure -> None (caller falls back
          to the generic deflection).
        - Exactly one match -> the customer-safe status summary.
        `entity_id`: exact projectId from page context, takes precedence
        over entity_name (see variance_response)."""
        if not entity_id and not entity_name:
            projects = self.entity_resolver.list_projects(
                context.tenantId, limit=_project_list_limit(context.platform),
                allowed_project_ids=allowed_project_ids,
            )
            return projects_disambiguation(projects)

        if entity_id:
            matches = self.entity_resolver.resolve_by_id(
                entity_id, context.tenantId, allowed_project_ids=allowed_project_ids
            )
        else:
            matches = self.entity_resolver.resolve_project(
                entity_name, context.tenantId, allowed_project_ids=allowed_project_ids
            )
        if len(matches) == 0:
            return None
        if len(matches) > 1:
            limit = _project_list_limit(context.platform)
            return projects_disambiguation(matches if limit == 0 else matches[:limit])

        match = matches[0]
        try:
            collection = get_collection(PROJECT_HEALTH_COLLECTION)
            # NO money fields in this projection -- that absence is the
            # customer-safety guarantee, do not "just add" committedSpend
            # here. Internal financial detail belongs to Insight.
            doc = collection.find_one(
                {
                    "tenantId": context.tenantId, "projectId": match.project_uid,
                    "isDeleted": {"$ne": True},
                },
                {
                    "orderNo": 1, "completionPct": 1, "totalTaskCount": 1, "doneTaskCount": 1,
                    "overdueTaskCount": 1, "scheduleRisk": 1, "slipDays": 1, "statusTimeline": 1,
                    "_id": 0,
                },
            )
            if not doc:
                return None

            completion = doc.get("completionPct")
            completion = float(completion) if completion is not None else None
            total_tasks = doc.get("totalTaskCount")
            total_tasks = int(total_tasks) if total_tasks is not None else None
            done_tasks = int(doc.get("doneTaskCount") or 0)
            overdue_tasks = int(doc.get("overdueTaskCount") or 0)
            schedule_risk = doc.get("scheduleRisk") or "unknown"
            slip_days = doc.get("slipDays")
            slip_days = int(slip_days) if slip_days is not None else None
            # statusTimeline is stored oldest-first (see gold_query_reference.md
            # §1) -- the LAST element is the current/most recent stage, never
            # the first. Real gap, fixed: this field existed and was populated
            # but nothing read it, so "what's the current status" had no
            # stage-name answer at all, only completion%/schedule-risk.
            status_timeline = doc.get("statusTimeline") or []
            current_stage = status_timeline[-1] if status_timeline else None
        except Exception as e:
            logger.error(
                f"Reporting status query failed for projectId="
                f"{match.project_uid!r}: {e}"
            )
            return None

        # Traceability: which project (by id AND order number) and the
        # exact raw field values pulled from Mongo, so the final chat
        # answer can be manually cross-checked against Gold directly.
        logger.info(
            "Reporting status data: project=%r projectId=%s orderNo=%s -- "
            "completionPct=%s totalTaskCount=%s doneTaskCount=%s "
            "overdueTaskCount=%s scheduleRisk=%r slipDays=%s currentStage=%r",
            match.project_name, match.project_uid, doc.get("orderNo"),
            completion, total_tasks, done_tasks, overdue_tasks, schedule_risk, slip_days,
            current_stage,
        )

        # completionPct is only ~8% populated in real data -- build the
        # summary from whichever facts exist rather than assuming all do.
        parts = []
        if current_stage and current_stage.get("newState"):
            parts.append(f"currently in {current_stage['newState']}")
        if completion is not None:
            parts.append(f"{completion:.0f}% complete")
        if total_tasks:
            task_bit = f"{done_tasks} of {total_tasks} tasks done"
            if overdue_tasks:
                task_bit += f", {overdue_tasks} overdue"
            parts.append(task_bit)
        summary = ", ".join(parts) if parts else "no task progress recorded yet"

        message = f"{match.project_name}: {summary}. "
        if schedule_risk == "at_risk" and slip_days:
            message += f"Schedule: at risk, {slip_days} days behind."
        elif schedule_risk == "unknown":
            message += "Schedule: not yet available."
        else:
            message += "Schedule: on track."

        if overdue_tasks:
            most_delayed = self._most_delayed_task(context.tenantId, match.project_uid)
            if most_delayed:
                message += f" Most overdue: \"{most_delayed['taskName']}\" ({most_delayed['daysLate']} days late)."

        # See billing_response's matching comment -- `project` lets a
        # page-context-resolved answer (no spoken name) still update "last
        # discussed project" in AgentOrchestrator._reporting_node.
        return NavigationResponse(
            type="guidance", message=message,
            project=ProjectRef(id=match.project_uid, name=match.project_name, status=match.status),
        )

    def _resolve_single_match(
        self, context: SessionContext, entity_name: str | None,
        allowed_project_ids: list[str] | None, entity_id: str | None,
    ):
        """Shared resolution for the three new reasoning-requirement
        methods below -- same "no name/id at all -> disambiguate,
        ambiguous -> disambiguate, no match -> None, exactly one -> that
        match" contract as billing_response/status_response, factored out
        since all three need it identically and none of them differ in how
        the project itself is found."""
        if not entity_id and not entity_name:
            projects = self.entity_resolver.list_projects(
                context.tenantId, limit=_project_list_limit(context.platform),
                allowed_project_ids=allowed_project_ids,
            )
            return None, projects_disambiguation(projects)
        if entity_id:
            matches = self.entity_resolver.resolve_by_id(
                entity_id, context.tenantId, allowed_project_ids=allowed_project_ids
            )
        else:
            matches = self.entity_resolver.resolve_project(
                entity_name, context.tenantId, allowed_project_ids=allowed_project_ids
            )
        if len(matches) == 0:
            return None, None
        if len(matches) > 1:
            limit = _project_list_limit(context.platform)
            return None, projects_disambiguation(matches if limit == 0 else matches[:limit])
        return matches[0], None

    def _margin_response(
        self, context: SessionContext, entity_name: str | None,
        allowed_project_ids: list[str] | None, entity_id: str | None,
    ) -> NavigationResponse | None:
        """Q3 -- gated on the real EST.MOD permission (constraints #30-#35,
        is_margin_visible: held by admin/sales/superAdmin, confirmed live).
        This is the path a customer reaches (Reporting is customer-safe by
        construction, per this class's own docstring) -- customer never
        holds EST.MOD, so the refusal here is the load-bearing enforcement
        for that role. Thin wrapper only -- see resolve_margin_response, the
        single shared function BOTH agents delegate to; this method must
        never query `margin` or call is_margin_visible itself."""
        match, early = self._resolve_single_match(context, entity_name, allowed_project_ids, entity_id)
        if early is not None:
            return early
        if match is None:
            return None
        return resolve_margin_response(context, match, self.role_resolver)

    def _estimate_change_response(
        self, context: SessionContext, entity_name: str | None,
        allowed_project_ids: list[str] | None, entity_id: str | None,
    ) -> NavigationResponse | None:
        """Q1 -- customer/salesperson path, so include_figures=False: cause
        only, no dollar figures. Per explicit user direction, materialCost
        stays exactly as internal-only as _PROJECT_HEALTH_INTERNAL_ONLY_
        FIELDS already treats it -- this method never reads or surfaces
        the delta/snapshot dollar values, only estimateHistory's reason
        text (constraints #1-#11)."""
        match, early = self._resolve_single_match(context, entity_name, allowed_project_ids, entity_id)
        if early is not None:
            return early
        if match is None:
            return None
        try:
            collection = get_collection(PROJECT_HEALTH_COLLECTION)
            doc = collection.find_one(
                {
                    "tenantId": context.tenantId, "projectId": match.project_uid,
                    "isDeleted": {"$ne": True},
                },
                {"estimateHistory": 1, "estimateSummary": 1, "_id": 0},
            )
        except Exception as e:
            logger.error(f"Reporting estimate-change query failed for projectId={match.project_uid!r}: {e}")
            return None
        if not doc:
            return None
        history = doc.get("estimateHistory") or []
        summary = doc.get("estimateSummary") or {}
        current = next((e for e in history if e.get("isCurrent")), None)
        # See estimate_changed_per_summary: a single retained entry is not
        # proof nothing changed, and claiming so contradicted the record.
        if current is None or (len(history) < 2 and not estimate_changed_per_summary(history, summary)):
            message = f"{match.project_name}'s estimate has not changed since it was first prepared."
        else:
            current = _prefer_non_degenerate_sibling(history, current)
            message = build_estimate_change_message(
                match.project_name, current, include_figures=False, summary=summary,
            )
            confidence = current.get("confidence")
            message = apply_reasoning_continuity(
                self.conversation_memory, context.tenantId, context.userId,
                match.project_uid, "estimate_change", message, confidence,
            )
            # Backlog #26159: capture every Q1 attempt so an unresolved one
            # can be surfaced to a human -- see write_reasoning_audit_log's
            # docstring. Same capture as InsightAgent's equivalent method.
            write_reasoning_audit_log(
                context.tenantId, context.userId, "reporting", match.project_uid,
                "estimate_change", confidence,
                "resolved" if confidence in ("high", "low") else "unresolved",
            )
        return NavigationResponse(
            type="guidance", message=message,
            project=ProjectRef(id=match.project_uid, name=match.project_name, status=match.status),
        )

    def _schedule_change_response(
        self, context: SessionContext, entity_name: str | None,
        allowed_project_ids: list[str] | None, entity_id: str | None,
    ) -> NavigationResponse | None:
        """Q2 -- same for every role (dates aren't cost-sensitive). See
        build_schedule_change_message -- correlational reasoning
        (constraints #12-#29) always stays hedged at confidence "low"."""
        match, early = self._resolve_single_match(context, entity_name, allowed_project_ids, entity_id)
        if early is not None:
            return early
        if match is None:
            return None
        try:
            collection = get_collection(PROJECT_HEALTH_COLLECTION)
            doc = collection.find_one(
                {
                    "tenantId": context.tenantId, "projectId": match.project_uid,
                    "isDeleted": {"$ne": True},
                },
                {"scheduleRevisions": 1, "scheduleSummary": 1, "_id": 0},
            )
        except Exception as e:
            logger.error(f"Reporting schedule-change query failed for projectId={match.project_uid!r}: {e}")
            return None
        if not doc:
            return None
        revisions = doc.get("scheduleRevisions") or []
        summary = doc.get("scheduleSummary") or {}
        current = next((e for e in revisions if e.get("isCurrent")), None)
        if current is None or (len(revisions) < 2 and not schedule_changed_per_summary(revisions, summary)):
            message = f"{match.project_name}'s schedule has not changed since it was first set."
        else:
            message = build_schedule_change_message(current, summary=summary)
            confidence = current.get("confidence")
            message = apply_reasoning_continuity(
                self.conversation_memory, context.tenantId, context.userId,
                match.project_uid, "schedule_move", message, confidence,
            )
            # Backlog #26159: same capture as InsightAgent's equivalent.
            write_reasoning_audit_log(
                context.tenantId, context.userId, "reporting", match.project_uid,
                "schedule_move", confidence,
                "resolved" if confidence in ("high", "low") else "unresolved",
            )
        return NavigationResponse(
            type="guidance", message=message,
            project=ProjectRef(id=match.project_uid, name=match.project_name, status=match.status),
        )

    def _most_delayed_task(self, tenant_id: str, project_id: str) -> dict | None:
        """Customer-safe enrichment on top of the overdue COUNT already in
        project_health: names the single most-delayed task, from
        task_intelligence.mostDelayedTask. Deliberately projects ONLY that
        one pre-computed sub-object -- never task_intelligence.overdueTasks/
        unassignedTasks, whose entries carry an `assignees` array (internal
        workforce names). mostDelayedTask itself has no assignee field at
        all ({taskId, taskName, daysLate} only, per datafinal.md 3.2.3), so
        this is customer-safe by query shape, same discipline as
        billing_response/status_response's money-field exclusion -- not
        because assignee names are masked after the fact, but because they
        are never read out of Mongo on this path in the first place.

        Returns None (silently omitted from the message) on no document,
        no mostDelayedTask, or any Mongo failure -- this is a nice-to-have
        enrichment, never something status_response's core answer should
        fail over."""
        try:
            collection = get_collection(TASK_INTELLIGENCE_COLLECTION)
            doc = collection.find_one(
                {
                    "tenantId": tenant_id, "projectId": project_id,
                    "isDeleted": {"$ne": True},
                },
                {"mostDelayedTask": 1, "_id": 0},
            )
            most_delayed = (doc or {}).get("mostDelayedTask")
            if not most_delayed or not most_delayed.get("taskName"):
                return None
            return most_delayed
        except Exception as e:
            logger.warning(f"task_intelligence lookup failed for projectId={project_id!r}: {e}")
            return None
