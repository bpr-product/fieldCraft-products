import difflib
import logging
import re

from source.model.navigation_models import (
    NavigationOption,
    NavigationResponse,
    ProjectRef,
    SessionContext,
)
from source.resolvers.entity_resolver import (
    PROJECT_HEALTH_COLLECTION,
    EntityResolver,
)
from source.resolvers.conversation_memory import ConversationMemory
from source.resolvers.user_role_resolver import UserRoleResolver
from source.utils.audit_log import write_reasoning_audit_log
from source.utils.mongo_client import get_collection

logger = logging.getLogger(__name__)

# Where a picked project's option card points if a frontend renders it as a
# clickable navigate action. The primary interaction this feature targets is
# the user typing the project's name as their next message (resolved via
# EntityResolver, same as any fresh query) -- clicking is a reasonable
# fallback, not the main path.
PROJECT_INFORMATION_ROUTE = "/soldproduction/projectinformation"

# Real gap, fixed (2026-09-03): projects_disambiguation used to redirect to
# the real Projects list page (route_registry.json id "sold_production")
# above a 30-project cap instead of listing -- reversed per explicit user
# direction once OptionCardList (NavigationMessage.tsx) grew an internal
# scroll that handles any option count. No cap/redirect route needed here
# anymore; see projects_disambiguation's own docstring.

# --- New reasoning requirement: estimate-change / schedule-move / margin ---
# See EDEN_CustomerSupport_Reasoning_Constraints.md for the full Postgres
# investigation this is built on (which tables/fields back each answer, and
# which paths were checked and found unusable). All three below are closed
# keyword vocabularies deciding a question Insight/Reporting ALREADY own
# (same reasoning as Reporting's BILLING_SIGNALS/DATE_SIGNALS) -- not a
# re-introduction of hardcoded top-level agent routing.

# Q3 (profit margin) -- kept separate from the general variance-question
# path because this one has a hard role gate (UserRoleResolver.is_margin_visible)
# that must never be bypassed by generic cost-variance phrasing.
MARGIN_SIGNALS = ["profit margin", "margin", "how profitable"]

# A bare "what does this project cost" question -- distinct from the
# default variance question ("why is cost over budget", which answers with
# committedSpend/remainingBudget, not estimatedCost) and from a margin
# question above. Deliberately excludes "why" -- every one of the
# intentional "why is cost over/under budget" variance questions this
# agent already answers correctly contains "why" and the word "budget";
# without this exclusion, "why is cost over budget" would be hijacked by
# this check too, since "budget" is one of these signals.
TOTAL_COST_SIGNALS = [
    "total cost", "total project cost", "total estimate", "what is the budget",
    "what's the budget", "project cost", "cost of the project", "how much does this cost",
    "how much will this cost", "estimated cost",
    # Real gap, found live: "what is the estimated amount" (a real, common
    # phrasing of the exact same question "estimated cost" already
    # covered) fell through every check above and hit the generic
    # variance_response instead -- which answers with committedSpend/
    # outstandingInvoicesTotal, the SAME figure as the project's invoiced
    # amount, not the estimate. That made "estimated amount" and "invoiced
    # amount" return an identical number for an unrelated reason (both
    # landing on the same fallback view), reading as though the estimate
    # equals what's been invoiced.
    "estimated amount", "estimate amount",
    # Real gap, found live (AB#gold-canonical / EDEN Labor-role RBAC pass):
    # "what is the project estimate" -- a common, natural phrasing of this
    # exact same question -- matched none of the signals above and fell
    # through to the generic variance_response, which discloses
    # remainingBudget (estimatedCost - committedSpend, the SAME protected
    # figure EST.REA gates here) with NO permission check at all. Confirmed
    # live: a Labor-role user asking this got "$618 remains under the
    # original estimate" despite lacking EST.REA. "project estimate" is
    # deliberately narrower than a bare "estimate" (which would over-match
    # things like "why did the estimate change" -- already handled by the
    # separate estimate-change branch) and reads naturally as this same
    # bare-total-cost question, not a change/reason question.
    "project estimate", "the estimate for this project", "estimate for the project",
]


def _is_total_cost_question(utterance_lower: str) -> bool:
    if "why" in utterance_lower:
        return False
    return any(s in utterance_lower for s in TOTAL_COST_SIGNALS)

# Q1/Q2 share the same causal-question shape (a "why" carrying a change/
# move/difference verb) paired with a topic word -- both conditions
# required, so a plain "why is cost over budget" (the existing
# variance_response question, asking about a CURRENT value, not a change)
# doesn't get mis-caught by the estimate-change branch.
#
# Real bug, fixed: the original _WHY_SIGNALS fixed list (["why did", "why
# is", "why has", "why does"]) required one of those four EXACT phrases
# immediately after "why" -- confirmed live, "why the estimation changed?"
# and "why the estimate changed" both miss it (the phrase is "why the",
# not "why did/is/has/does"), and fall through to the general RAG path
# (see reporting_agent.py's _question_reason_domain, added first to catch
# exactly this fallthrough at the verification layer). This regex
# generalizes the SAME shape without an ever-growing fixed-phrase list:
# "why" anywhere in the utterance, paired with a change/move/difference
# verb stem anywhere in it -- order-agnostic, catches "why did X change",
# "why the X changed", "why is X different", "why did X's date move".
_WHY_CHANGE_RE = re.compile(r"\bwhy\b")
_CHANGE_STEM_RE = re.compile(r"\b(chang|mov|differ|shift)\w*\b")


def _has_why_change_signal(utterance_lower: str) -> bool:
    return bool(_WHY_CHANGE_RE.search(utterance_lower) and _CHANGE_STEM_RE.search(utterance_lower))


# Real bug, fixed: "estimate" as an exact substring doesn't match
# "estimation"/"estimating" -- confirmed live, "why the estimation
# changed?" missed this topic check even though it's unambiguously about
# the estimate.
#
# Real bug #2, found while testing the fix above: a bare "estimat"
# substring (no word boundary) ALSO matches "estimator"/"estimators" --
# a genuine job title in this domain (construction bid estimating), not
# an edge case. "why did the estimator leave" would have wrongly counted
# as an estimate-topic question. Same collision risk for "schedul" against
# "scheduler"/"schedulers". Word-boundary regexes requiring a real
# inflection suffix (e/es/ed/ing/ion) catch every real form
# (estimate/estimates/estimated/estimating/estimation,
# schedule/schedules/scheduled/scheduling) while excluding the person-noun
# forms, which end in -or/-ors instead.
_ESTIMATE_STEM_RE = re.compile(r"\bestimat(?:e|es|ed|ing|ion)\b")
_SCHEDULE_STEM_RE = re.compile(r"\bschedul(?:e|es|ed|ing)\b")

ESTIMATE_TOPIC_SIGNALS = ["material cost", "material price", "price change", "cost change"]
SCHEDULE_TOPIC_SIGNALS = ["start date", "tentative date", "date move", "date moved"]

# Real bug, fixed: confirmed live -- "why the estiamtion changed for
# 102380" (transposed letters) doesn't contain the "estimat" stem at all,
# so it missed BOTH this topic check and reporting_agent.py's
# _question_reason_domain (the RAG verification's own topic check), fell
# through to a generic status answer, and separately let the voice
# rephrase fabricate a cause unchecked (see llm_client.rephrase's
# _CAUSAL_PHRASE_RE guard). A single-word misspelling is common enough in
# real typed input that the exact-substring/stem check alone isn't
# sufficient -- this adds a fuzzy fallback for exactly that failure mode,
# not a general spell-checker: only single TOKENS of the utterance are
# compared (never multi-word phrases), against a small closed reference
# list per topic. Cutoff tuned empirically, not guessed: 0.8 also matched
# "estimator"/"estimators"/"estimable" (ratios 0.78-0.84) -- a real risk in
# this domain, where "estimator" is a genuine job title, not just an
# unlikely edge case. 0.85 still catches every real single-letter-
# transposition typo tested ("estiamtion"->estimation, "esitmate"->
# estimate, "scedule"->schedule) while excluding all three false
# positives. Words under 6 chars are skipped entirely regardless of
# cutoff, to keep the false-positive risk on short common words near zero.
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


_ESTIMATE_BUCKET_LABELS = {
    "materialCost": "material cost",
    "workerCost": "labor cost",
    "toolCost": "tool cost",
    "overheadCost": "overhead cost",
}

# A bare "what is the material cost"/"what is the worker cost" question --
# distinct from _is_estimate_change_question (a "why did X change" shape,
# which already owns "material cost" via ESTIMATE_TOPIC_SIGNALS) and from
# _is_total_cost_question (the aggregate estimatedCost figure, a different
# field). Real gap, fixed: confirmed live this had NO path in either agent
# -- every phrasing not caught by the four checks above fell through to
# the generic variance_response, silently answering with committedSpend
# instead of the specific figure asked for. Same failure class
# _total_cost_response's own docstring already describes for bare "total
# cost" phrasing, just never extended to these four per-category fields.
# Deliberately excludes "why", same reasoning as _is_total_cost_question,
# so "why did the material cost change" stays owned by the change-
# explanation path above, not hijacked here.
COST_BREAKDOWN_SIGNALS = {
    "materialCost": ["material cost", "materials cost", "cost of material", "cost of materials"],
    "workerCost": ["worker cost", "labor cost", "labour cost", "cost of labor", "cost of labour"],
    "toolCost": ["tool cost", "tools cost", "cost of tools"],
    "overheadCost": ["overhead cost", "cost of overhead"],
}


def _cost_breakdown_category(utterance_lower: str) -> str | None:
    if "why" in utterance_lower:
        return None
    for field, signals in COST_BREAKDOWN_SIGNALS.items():
        if any(s in utterance_lower for s in signals):
            return field
    return None


def _changed_buckets_note(delta: dict) -> str | None:
    """Constraint #11's "report the figure but assert no cause" principle,
    generalized to whichever cost bucket(s) actually moved -- material,
    labor, tool, overhead -- using `delta`, which the ETL already computes
    independently of `confidence` (verified live: real gold_eden project
    7890 has a fully-populated delta with confidence entirely absent).
    Returns None when delta has no bucket-level entries to report."""
    changed = [
        (label, delta[key]) for key, label in _ESTIMATE_BUCKET_LABELS.items()
        if delta.get(key)
    ]
    if not changed:
        return None
    parts = [f"{label} changed by ${abs(v):,.0f}" for label, v in changed]
    return "Specifically: " + ", ".join(parts) + "."


def _bom_price_change_message(evidence: dict | None, include_figures: bool) -> str | None:
    """The fully deterministic Q1 case per
    EDEN_CustomerSupport_Reasoning_Constraints.md's constraint #4/#7:
    project_estimate.material_cost changed because a SPECIFIC material's
    price moved, traced via material_cost's effective-dated history. The
    gold layer already computes this per-entry as `evidence.
    priceChangeDetected` + `materialName` (+ prior/new unit cost for
    internal roles) -- this was sitting unread. Checked live across every
    estimateHistory entry in this tenant's real gold_eden data (98
    entries): priceChangeDetected is never True here, so this path is
    unexercised by live traffic today, but the ETL contract supports it and
    another tenant (or this one later) may populate it. Returns None when
    evidence doesn't support this deterministic a claim -- callers fall
    back to the bomReconciles-level fact or the generic wording."""
    evidence = evidence or {}
    if not evidence.get("priceChangeDetected"):
        return None
    material = evidence.get("materialName")
    if not material:
        return None
    if include_figures and evidence.get("priorUnitCost") is not None and evidence.get("newUnitCost") is not None:
        return (
            f"Confirmed: the change is a price update on {material} -- unit "
            f"cost moved from ${evidence['priorUnitCost']:,.2f} to "
            f"${evidence['newUnitCost']:,.2f}."
        )
    return f"Confirmed: the change is a price update on {material}."


def _join_labels(labels: list[str]) -> str:
    """'material cost' / 'material cost and labor cost' / 'A, B and C' --
    same joining rule used for bucket_phrase below, factored out so both
    stay consistent."""
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + f" and {labels[-1]}"


def _moved_bucket_names(delta: dict) -> list[str]:
    """Which cost buckets show a real, non-zero movement between versions --
    NAMES only, no dollar figures, so this is customer-safe (contrast with
    _changed_buckets_note, which includes $ amounts and is internal-only).

    Deliberately does NOT require evidence.bomReconciles, unlike
    _bom_confirmed_not_material_message. The delta is a plain before/after
    subtraction the ETL computes independently for EACH bucket -- whether
    overhead moved is a fact regardless of whether material cost happens to
    reconcile against the bill of materials. bomReconciles only backs the
    stronger claim "material is CONFIRMED unchanged," never the weaker,
    always-available fact "here's what the record shows moved.\""""
    return [
        label for key, label in _ESTIMATE_BUCKET_LABELS.items()
        if abs(delta.get(key) or 0) > 0.005
    ]


def _bom_confirmed_not_material_message(evidence: dict | None, delta: dict) -> str | None:
    """The realistic ceiling for Q1 in THIS tenant's data (priceChangeDetected
    never fires -- see _bom_price_change_message): `evidence.bomReconciles`
    is the gold layer's own computed check that project_estimate.material_
    cost == SUM(bom.total_cost) (constraint #4). True means material cost is
    VERIFIED STABLE -- not "unattributed", but ruled out as the cause, a
    fact rather than a hedge. Combined with the bucket-level `delta` the ETL
    already computes (independent of confidence -- see _changed_buckets_
    note), checked across every 2+-version project in this tenant: when
    exactly one bucket moved, it was overhead 19 times out of 19 non-material
    cases, material/labor/tool never moved alone. So when bomReconciles is
    True and a bucket moved, naming that bucket is an observed measurement,
    not a guess -- the ROOT CAUSE of why that bucket moved is still
    unconfirmed and stays hedged by the caller.

    Guards against the contradictory case (found live: 2 of 18 low-confidence
    entries have bomReconciles=True alongside a small non-zero materialCost
    delta) by refusing to claim "not material" when material demonstrably
    moved -- returns None so the caller falls back to the generic wording
    rather than asserting something the same record contradicts."""
    evidence = evidence or {}
    if not evidence.get("bomReconciles"):
        return None
    delta = delta or {}
    if abs(delta.get("materialCost") or 0) > 0.005:
        return None  # bomReconciles=True but material moved -- contradictory, don't claim it didn't
    moved = [
        label for key, label in _ESTIMATE_BUCKET_LABELS.items()
        if key != "materialCost" and abs(delta.get(key) or 0) > 0.005
    ]
    if not moved:
        return None
    bucket_phrase = _join_labels(moved)
    # Only claim "unchanged" for a bucket the record actually shows at ~0 --
    # bomReconciles proves material specifically; a bucket simply ABSENT
    # from delta is unknown, not confirmed unchanged, so it's never folded
    # into this list on an assumption.
    also_unchanged = [
        label for key, label in _ESTIMATE_BUCKET_LABELS.items()
        if key != "materialCost" and key in delta
        and abs(delta.get(key) or 0) <= 0.005 and label not in moved
    ]
    unchanged_phrase = _join_labels(["material cost", *also_unchanged])
    return (
        f"I can confirm this wasn't {unchanged_phrase} -- those match the "
        f"project's records, unchanged. The movement is in {bucket_phrase}."
    )


def _prefer_non_degenerate_sibling(history: list[dict], current: dict) -> dict:
    """Defensive fallback for a real ETL data-quality bug (flagged
    separately to the data team, not silently assumed to be the general
    case): verified live against real gold_eden project 7890, the same
    `version` number was written TWICE, and the entry flagged `isCurrent`
    had its delta computed against itself (all zero) instead of against
    the prior version -- the sibling entry for the SAME version carries
    the real bucket-level delta. If `current`'s delta is entirely empty/
    zero and a same-version sibling has a non-zero delta, use the
    sibling's delta/snapshot for the figure-report ONLY -- confidence and
    reason always come from `current` unchanged, since duplicating those
    from a sibling would risk asserting a cause current doesn't actually
    carry.

    Second fallback tier, same reasoning applied one step further: verified
    live on gold_eden order 102380 (project 1013) -- the CURRENT entry (v2)
    has an empty delta because it's financially identical to v1 (a re-save,
    not a new change); the actual last recorded movement is v1's delta
    (v0->v1). Same-version matching doesn't find this (v1 != v2), so without
    this tier the bucket-level fact (overhead moved) was unreachable for any
    project whose current version happens to be a flat re-save of the one
    that actually changed -- which is common (the ETL numbers every save,
    not just financially meaningful ones; see estimateSummary.
    rawVersionCount vs meaningfulRevisionCount). Walks history NEWEST-first
    for the most recent entry (any version) with an informative delta.
    Confidence/reason still always come from `current` unchanged."""
    delta = current.get("delta") or {}
    if any(delta.get(k) for k in _ESTIMATE_BUCKET_LABELS):
        return current  # current's own delta is already informative
    sibling = next(
        (
            e for e in history
            if e is not current and e.get("version") == current.get("version")
            and any((e.get("delta") or {}).get(k) for k in _ESTIMATE_BUCKET_LABELS)
        ),
        None,
    )
    if sibling is None:
        sibling = next(
            (
                e for e in reversed(history)
                if e is not current
                and any((e.get("delta") or {}).get(k) for k in _ESTIMATE_BUCKET_LABELS)
            ),
            None,
        )
    if sibling is None:
        return current
    merged = dict(current)
    merged["delta"] = sibling.get("delta")
    merged["snapshot"] = current.get("snapshot") or sibling.get("snapshot")
    return merged


def apply_reasoning_continuity(
    conversation_memory: ConversationMemory,
    tenant_id: str, user_id: str, project_id: str, question_type: str,
    raw_message: str, confidence,
) -> str:
    """Lets EDEN honestly reference its OWN prior Q1/Q2 answer for the
    SAME project and SAME question_type -- never across question types
    (see ConversationMemory.get_last_reasoning_answer's docstring for why
    that boundary matters: asserting a connection between, say, an
    estimate-change reason and a schedule-move reason would be exactly the
    kind of unproven-causation claim the confidence-gating in this file
    already exists to prevent).

    Stores the RAW (undecorated) message every call, so the prefix never
    compounds across turns -- comparison is always against the plain text
    that was actually shown last time, not a previously-decorated string."""
    prior = conversation_memory.get_last_reasoning_answer(tenant_id, user_id, project_id, question_type)
    conversation_memory.set_last_reasoning_answer(
        tenant_id, user_id, project_id, question_type, raw_message, confidence
    )
    if prior is None:
        return raw_message
    if prior.get("message") == raw_message:
        return f"As I mentioned earlier: {raw_message}"
    return f"This is different from what I told you before -- {raw_message}"


def estimate_changed_per_summary(history: list, summary: dict | None) -> bool:
    """Did the estimate actually change, when `history` is too thin to diff?

    Real bug, fixed: callers used `len(history) < 2` as "never changed" and
    stated that flatly. Confirmed live on gold_eden project 1203 (order
    543655): history holds ONE entry, but it is `version: 2` with
    `confidence: "low"` and `inferredReason: "Cost change (no specific
    material attributable)"` -- the ETL recorded that a change happened and
    simply didn't retain v0/v1. EDEN answered "estimate has not changed since
    it was first prepared", contradicting the record it had just read, with no
    hedge at all (violating constraint #36's explicit-confidence rule).

    estimateSummary carries the ETL's own verdict, so consult it rather than
    inferring from array length."""
    summary = summary or {}
    if (summary.get("meaningfulRevisionCount") or 0) > 0:
        return True
    if summary.get("totalCostDelta"):
        return True
    if (summary.get("currentVersion") or 0) > (summary.get("baselineVersion") or 0):
        return True
    current = next((e for e in history if e.get("isCurrent")), None) or {}
    if (current.get("version") or 0) > 0:
        return True
    return bool(current.get("inferredReason") or current.get("reason"))


def schedule_changed_per_summary(revisions: list, summary: dict | None) -> bool:
    """Q2's counterpart to estimate_changed_per_summary -- same `len < 2`
    flaw, same fix. scheduleSummary states the shift directly
    (originalJobStartDate vs currentJobStartDate, totalEndShiftDays), so a
    single retained revision no longer reads as "never rescheduled"."""
    summary = summary or {}
    if (summary.get("meaningfulRevisionCount") or 0) > 0:
        return True
    if summary.get("totalEndShiftDays"):
        return True
    original, current_start = summary.get("originalJobStartDate"), summary.get("currentJobStartDate")
    if original and current_start and str(original)[:10] != str(current_start)[:10]:
        return True
    current = next((e for e in revisions if e.get("isCurrent")), None) or {}
    return (current.get("version") or 0) > 0 or bool(current.get("reason"))


def build_estimate_change_message(
    project_name: str, current_entry: dict, include_figures: bool,
    summary: dict | None = None,
) -> str:
    """Q1 answer from an estimateHistory "current" entry (constraints
    #1-#11). `include_figures`: internal roles (Insight) get the dollar
    delta and bucket breakdown, in EVERY confidence tier ("high"/"low"/
    "none") -- the delta is a verified fact independent of how much we
    trust the CAUSE, so confidence must never suppress it for Insight.
    customer/salesperson roles (Reporting) get the cause only, NEVER a
    dollar figure, in every tier -- per explicit user direction
    distinguishing "cost to the contractor" (materialCost) from the
    customer's own quoted total. This deliberately overrides the
    constraints doc's original "no confidence" sample response (which
    showed a total-cost figure) for the Reporting/customer path -- that
    sample predates the later, more specific figures-vs-no-figures
    decision, which takes precedence for that role.

    Confidence is never upgraded past what's on the record (constraint
    #36) -- "none"/missing never asserts a CAUSE, but per constraint #11
    it still reports WHAT changed when the underlying data (delta) makes
    that possible, rather than a maximally uninformative non-answer.

    `confidence` is entirely evidence.bomReconciles-derived (does the
    MATERIAL-cost figure reconcile against the bill of materials) -- a
    real, human-entered `reason` is a separate signal it must never gate.
    A real reason is reported (hedged) whenever present, regardless of
    confidence; the bucket-movement fact (which bucket moved between
    versions) is likewise reported whenever the delta shows one, whether
    or not bomReconciles has confirmed material specifically -- only the
    stronger "material is CONFIRMED ruled out" CLAIM needs that gate."""
    confidence = current_entry.get("confidence")
    reason = current_entry.get("reason")
    has_real_reason = bool(reason)  # human-entered, distinct from the inferred fallback below
    delta = current_entry.get("delta") or {}
    snapshot = current_entry.get("snapshot") or {}
    evidence = current_entry.get("evidence") or {}
    summary = summary or {}

    # Reason fallback chain, weakest claim last. `reason` is a recorded,
    # human-entered cause and is the only one that may be stated plainly.
    # `inferredReason` is the ETL's own guess ("Cost change (no specific
    # material attributable)") and estimateSummary.lastReason is the most
    # recent change's reason, which may not belong to THIS entry -- both were
    # previously unread, leaving EDEN saying "I don't have a confirmed reason
    # on file" while a reason sat in the record. Neither may be asserted as
    # fact, so both force the hedged wording below (constraint #36: never
    # upgrade confidence past what the record supports).
    inferred = current_entry.get("inferredReason") or summary.get("lastReason")
    if not reason and inferred and confidence == "high":
        confidence = "low"  # an inferred cause is never deterministic
    reason = reason or inferred

    revisions = summary.get("meaningfulRevisionCount") or 0
    revision_note = (
        f" This is across {revisions} recorded revisions." if revisions > 1 else ""
    )

    # Evidence-based enrichment -- ONLY when there's no real human-entered
    # reason to report. A human `reason` ("Customer Requested To Change") is
    # a direct business-level explanation and stays as-is; these are
    # mechanism-level facts the ETL derives from the BOM, useful precisely
    # when nothing more human is on file (the common case in real data:
    # 71 of 98 estimateHistory entries checked live had no `reason` at all).
    #
    # Tier 1 -- deterministic (constraint #4/#7): evidence.priceChangeDetected
    # names the specific material and its price move. Never occurs in this
    # tenant's live data (checked all 98 entries) but the ETL contract
    # supports it, so it's handled rather than assumed impossible.
    #
    # Tier 2 -- the REAL ceiling for this tenant: evidence.bomReconciles=True
    # means project_estimate.material_cost is VERIFIED to match SUM(bom.
    # total_cost) -- material is confirmed stable, not just "unattributed".
    # Checked live across every 2+-version project in this tenant (24 of
    # them): when exactly one cost bucket moved, it was overhead 19 times out
    # of 19 non-material cases, material/labor/tool never moved alone. So
    # naming the bucket that moved is a measured fact here, not a guess --
    # only the ROOT CAUSE of that bucket's movement stays unconfirmed, which
    # both helpers' wording reflects ("I can't confirm the specific driver").
    if not has_real_reason:
        evidence_message = _bom_price_change_message(evidence, include_figures)
        if evidence_message is None:
            evidence_message = _bom_confirmed_not_material_message(evidence, delta)
        if evidence_message is None and not include_figures:
            # Tier 3, found live: bomReconciles=False means material ISN'T
            # confirmed unchanged, so _bom_confirmed_not_material_message
            # correctly declines to say so -- but it was ALSO discarding the
            # bucket-movement fact itself, which doesn't depend on material
            # reconciling at all (_moved_bucket_names, above). Whichever
            # bucket moved is real, computed data either way; only the
            # "material is ruled out" CLAIM needed bomReconciles.
            #
            # CUSTOMER PATH ONLY (not include_figures): Insight has its own,
            # richer figure-based fallback further down this function (the
            # "No confirmed CAUSE" section, `if include_figures: ...`) --
            # this tier existing must NOT short-circuit before that runs, or
            # Insight loses real dollar figures it already had (found while
            # testing: real gold_eden project 7890, materialCost delta
            # $2,000, used to correctly reach that figure-based tier; this
            # tier intercepting it first regressed Insight to a bucket-name-
            # only message with no figures at all).
            moved = _moved_bucket_names(delta)
            if moved:
                evidence_message = (
                    f"Your estimate was updated. The recorded change is in "
                    f"{_join_labels(moved)}, though I can't confirm the exact cause."
                )
        if evidence_message is not None:
            tail = " I can loop in your project manager for further detail." if confidence != "high" else ""
            return f"{evidence_message}{tail}{revision_note}"

    # estimateSummary.totalCostDelta is the whole-history movement; the entry's
    # own delta only covers the last hop. Use the entry's when present, fall
    # back to the summary's so a thin history still yields a figure.
    if not delta.get("totalCost") and summary.get("totalCostDelta"):
        delta = {**delta, "totalCost": summary["totalCostDelta"]}
    # NOTE deliberately NOT surfacing summary["marginDelta"] here. Margin is
    # gated on the real EST.MOD permission (Q3, resolve_margin_response/
    # is_margin_visible -- held by admin/sales/superAdmin, confirmed live),
    # and include_figures=True means "internal role", which is broader than
    # that (projectManager also reaches Insight without EST.MOD). Leaking a
    # margin delta through Q1 would route around that gate.

    if confidence == "high" and reason:
        if include_figures:
            bucket_note = _changed_buckets_note(delta)
            prefix = f"{bucket_note} " if bucket_note else ""
            return f"{prefix}{reason}."
        return f"Your estimate was updated. Reason on file: {reason}."
    # Real bug, fixed: this used to require confidence == "low" specifically
    # -- confirmed live on 17 of 24 multi-version projects in this tenant,
    # a REAL, human-entered reason ("Customer Requested To Change") sat on
    # the record while confidence == "none", and the answer was "I don't
    # have a confirmed reason on file," discarding it entirely. `confidence`
    # here is entirely evidence.bomReconciles-derived -- whether the
    # MATERIAL-cost figure reconciles against the bill of materials. A
    # human typing a real reason into the record is a completely separate
    # signal; bomReconciles says nothing about whether THAT text is
    # trustworthy, so it must not gate whether it's reported at all. Widened
    # from "== low" to "!= high" (the only other values are "low"/"none"/
    # missing -- confirmed live, "high" never occurs in this tenant's data)
    # so a real reason is always reported, hedged, regardless of the
    # material-reconciliation check.
    if reason and confidence != "high":
        if include_figures:
            bucket_note = _changed_buckets_note(delta)
            tail = f" {bucket_note}" if bucket_note else ""
            return (
                f"Your estimate was updated. It appears related to: {reason}.{tail} "
                f"I can loop in your project manager to confirm."
            )
        # Customer-safe: bucket NAMES only, no dollar figures -- same
        # exclusion applied to Insight's own reason via _changed_buckets_note
        # not being called on this branch below.
        moved = _moved_bucket_names(delta)
        bucket_phrase = f" This shows up as a change in {_join_labels(moved)}." if moved else ""
        return (
            f"Your estimate was updated. It appears related to: {reason}.{bucket_phrase} "
            f"I can loop in your project manager to confirm."
        )

    # No confirmed CAUSE -- but constraint #11 says still report the figure
    # when it's available, rather than a bare non-answer. Insight only:
    # Reporting/customer stays figure-free in every tier (see docstring).
    if include_figures:
        total_cost = snapshot.get("totalCost")
        total_delta = delta.get("totalCost")
        if total_cost is not None and total_delta:
            prior_total = total_cost - total_delta
            bucket_note = _changed_buckets_note(delta)
            tail = f" {bucket_note}" if bucket_note else ""
            return (
                f"Your estimate was updated from ${prior_total:,.0f} to "
                f"${total_cost:,.0f}.{tail}{revision_note} I can't confirm the "
                f"specific driver of that change from the project records -- I "
                f"can loop in your project manager for detail."
            )
        # No snapshot to anchor against, but the summary still knows the
        # movement -- report direction and size rather than nothing.
        if total_delta:
            direction = "up" if total_delta > 0 else "down"
            return (
                f"Your estimate moved {direction} by ${abs(total_delta):,.0f}."
                f"{revision_note} I can't confirm the specific driver from the "
                f"project records -- I can loop in your project manager for detail."
            )
    return (
        f"Your estimate was updated, but I don't have a confirmed reason on "
        f"file -- I can loop in your project manager for detail.{revision_note}"
    )


def _format_schedule_date(value) -> str | None:
    """Minimal local date formatter -- mirrors reporting_agent.py's
    _format_date/_parse_date logic (a real datetime, or an ISO-like string
    whose first 10 chars are YYYY-MM-DD) without importing from
    reporting_agent (which imports FROM this module -- would be circular)."""
    import datetime as _dt
    import os as _os

    if value is None:
        return None
    strftime_day = "%-d" if _os.name != "nt" else "%#d"
    if isinstance(value, _dt.datetime):
        return value.strftime(f"%B {strftime_day}, %Y")
    try:
        parsed = _dt.datetime.strptime(str(value)[:10], "%Y-%m-%d")
        return parsed.strftime(f"%B {strftime_day}, %Y")
    except ValueError:
        return str(value)


def build_schedule_change_message(current_entry: dict, summary: dict | None = None) -> str:
    """Q2 answer from a scheduleRevisions "current" entry (constraints
    #12-#29). Dates aren't cost-sensitive, so this is the same for every
    role -- unlike Q1, there's no figures-vs-no-figures split. Confidence
    "low" is always hedged (constraint #36) -- causation between two
    projects is never provable from this data, only correlation.

    No-confidence branch still reports the actual new date (constraint
    #11's "report the figure, assert no cause" principle applied to
    schedule data) -- matches the constraints doc's own no-confidence
    sample response ("...moved to July 29..."), which the earlier
    implementation omitted.

    Regression guard (found live against real gold_eden project 7890):
    when `delta` explicitly shows zero shift (jobStartShiftDays == 0 and
    jobEndShiftDays in (0, None)), the date must NOT be reported as
    "moved" -- the ETL's own scheduleSummary.meaningfulRevisionCount was
    0 for that project, meaning nothing actually changed between
    versions. Only an explicit zero counts as "confirmed no shift";
    missing/None delta fields mean "unknown", not "confirmed unchanged",
    and fall through to the existing report-the-date behavior below."""
    confidence = current_entry.get("confidence")
    reason = current_entry.get("reason")
    snapshot = current_entry.get("snapshot") or {}
    delta = current_entry.get("delta") or {}
    summary = summary or {}

    # scheduleSummary fields, all previously unread. `lastReason` is the big
    # one: EDEN answered "I don't have a confirmed reason for the change on
    # file" while the record said "Recurring Auto Rescheduling" (confirmed live
    # on order 102380). It describes the MOST RECENT revision, which may not be
    # the entry in hand, so it can never be stated as this change's cause --
    # only attributed as the latest recorded reason.
    summary_reason = summary.get("lastReason")
    original_start = _format_schedule_date(summary.get("originalJobStartDate"))
    current_start = _format_schedule_date(
        summary.get("currentJobStartDate") or snapshot.get("jobStartDate")
    )
    end_shift_days = summary.get("totalEndShiftDays")
    revisions = summary.get("meaningfulRevisionCount") or 0

    # "From X to Y" beats a bare "moved to Y" -- the customer's question is
    # about the move, and both endpoints are on the record.
    if original_start and current_start and original_start != current_start:
        span = f"Your project's start date moved from {original_start} to {current_start}"
    elif current_start:
        span = f"Your project's start date is set for {current_start}"
    else:
        span = "Your project's schedule was updated"
    end_note = (
        f", and the finish date shifted by {end_shift_days} day"
        f"{'s' if abs(end_shift_days) != 1 else ''}"
        if end_shift_days else ""
    )
    revision_note = f" There have been {revisions} recorded revisions." if revisions > 1 else ""

    if confidence in ("high", "low") and reason:
        text = (current_entry.get("comment") or reason).rstrip(". ")
        hedge = " I can confirm the exact reason with your project manager." if confidence == "low" else ""
        return f"Your schedule was updated. {text}.{hedge}"

    start_shift = delta.get("jobStartShiftDays")
    end_shift = delta.get("jobEndShiftDays")
    # Only an EXPLICIT zero on both the entry AND the summary counts as
    # "confirmed unchanged" -- the summary is what caught project 1203, where
    # the entry looked static but the summary knew about a shift.
    if start_shift == 0 and end_shift in (0, None) and not schedule_changed_per_summary([], summary):
        new_start = _format_schedule_date(snapshot.get("jobStartDate"))
        if new_start:
            return (
                f"Your project's schedule was reviewed, but the start date "
                f"hasn't actually changed -- it's still set for {new_start}."
            )
        return "Your project's schedule was reviewed, but I don't see a confirmed date shift on file."

    if summary_reason:
        return (
            f"{span}{end_note}. The most recent schedule change on file was "
            f"recorded as \"{summary_reason}\" -- I can confirm whether that "
            f"applies to this move with your project manager.{revision_note}"
        )
    if original_start and current_start and original_start != current_start:
        return (
            f"{span}{end_note}. I don't have a confirmed reason for the change "
            f"on file -- I can check with your project manager.{revision_note}"
        )
    new_start = _format_schedule_date(snapshot.get("jobStartDate"))
    if new_start:
        return (
            f"Your project's start date moved to {new_start}. I don't have "
            f"a confirmed reason for the change on file -- I can check with "
            f"your project manager.{revision_note}"
        )
    return (
        "Your schedule was updated, but I don't have a confirmed reason on "
        "file -- I can check with your project manager."
    )


def resolve_margin_response(
    context: SessionContext, match, role_resolver: UserRoleResolver,
    question_type: str = "margin",
) -> NavigationResponse | None:
    """THE ONLY place `project_health.margin`/`estimatedCost` may be read
    and turned into a user-facing response, from EITHER agent. Senior
    review finding (2026-08-01): before this consolidation, InsightAgent
    and ReportingAgent each independently queried Mongo and independently
    called the role gate -- two copies of the same gate,
    with nothing stopping a THIRD future response path from querying
    `margin` directly and forgetting the check. Collapsing both call
    sites into this single function means there is exactly one place the
    query + gate can be wrong, and `tests/test_margin_gate_single_source.py`
    mechanically fails the suite if a second unguarded `"margin"` Mongo
    read site is ever added to either agent file -- the enforcement is a
    test invariant, not just a docstring convention, given Python has no
    type-level way to forbid it outright.

    `question_type`: "margin" (default) -- "what is the profit margin" --
    or "total_cost" -- a bare "what is the total project cost"/"what's the
    budget" question, which wants the SAME gated estimatedCost figure but
    phrased without stating margin at all (a real gap: before this, a bare
    cost question had nowhere in either agent that read estimatedCost, so
    it silently fell through to a variance/billing view that answers a
    DIFFERENT number instead). Same single query either way -- this
    parameter only changes which sentence build_margin_message composes
    from the SAME already-fetched doc, never adds a second read site.

    2026-09-10: "margin" and "total_cost" use TWO DIFFERENT real
    permissions, not one shared gate -- see the branch below. Confirmed live
    against the real source `role_permission` table (not just Gold) that
    `EST.REA` is held by admin/superAdmin/sales/purchaseOfficer, refused
    for foreman/labour/shopSupervisor/contractorLabour/General Employee,
    and (separately, via a different code path) already visible to
    customer -- i.e. it draws exactly the line a bare cost question should.
    Margin specifically CANNOT use EST.REA the same way -- sales/customer/
    purchaseOfficer all hold it too, which would leak margin to roles it
    must stay refused for. It uses `EST.MOD` instead (also confirmed live,
    all 3 tenants): held by admin/sales/superAdmin only -- matching how the
    real fieldcraft-server product itself gates the margin columns in its own
    Estimate Preview table (see is_margin_visible's own docstring). Neither
    branch hardcodes a role name or a user id -- both read the caller's
    actual resolved permissions.

    Both InsightAgent._margin_response/_total_cost_response and
    ReportingAgent._margin_response/_total_cost_response are thin wrappers:
    resolve the project match, call this, return the result. Neither
    computes the gate or queries Mongo itself."""
    try:
        collection = get_collection(PROJECT_HEALTH_COLLECTION)
        doc = collection.find_one(
            {"tenantId": context.tenantId, "projectId": match.project_uid, "isDeleted": {"$ne": True}},
            {"estimatedCost": 1, "margin": 1, "_id": 0},
        )
    except Exception as e:
        logger.error(f"Margin query failed for projectId={match.project_uid!r}: {e}")
        return None
    if not doc:
        return None
    # Fetched once, reused for the figure gate below (total_cost branch)
    # AND for build_margin_message's dynamic "here's what I CAN help with"
    # fallback offer on either branch's denial -- see
    # _available_alternative_offer's own docstring for why this must be
    # real permissions, not a hardcoded suggestion.
    permissions = role_resolver.get_permissions(context.tenantId, context.userId)
    can_view_invoices = "INV.REA" in permissions
    can_view_cost_breakdown = "BPE.CST" in permissions
    if question_type == "total_cost":
        # Permission-based, not role-based -- EST.REA is a real permission
        # code (confirmed live at the actual source role_permission table,
        # not invented here), read from this specific user's own Gold
        # permissions. No role name, no user id, anywhere in this branch.
        can_view_figure = "EST.REA" in permissions
    else:
        # is_margin_visible, NOT is_admin -- deliberately a separate gate,
        # see its docstring in user_role_resolver.py for why (is_admin also
        # gates the EDEN_ADMIN_ONLY pilot restriction, which must stay
        # admin-only). Permission-based too (EST.MOD), just a DIFFERENT
        # permission than total_cost's EST.REA -- see this function's own
        # docstring above for why margin can't use EST.REA.
        can_view_figure = role_resolver.is_margin_visible(context.tenantId, context.userId)
    message = build_margin_message(
        match.project_name, doc.get("estimatedCost"), doc.get("margin"), can_view_figure,
        question_type=question_type,
        can_view_invoices=can_view_invoices, can_view_cost_breakdown=can_view_cost_breakdown,
    )
    return NavigationResponse(
        type="guidance", message=message,
        project=ProjectRef(id=match.project_uid, name=match.project_name, status=match.status),
    )


def _available_alternative_offer(can_view_invoices: bool, can_view_cost_breakdown: bool) -> str:
    """Real gap, fixed: the two denial messages in build_margin_message used
    to hardcode a specific "I can help with X instead" offer regardless of
    whether the caller could actually see X either. Confirmed live: a Labor
    user (no INV.REA) denied the total-cost figure was pointed at "your
    project's billing/invoice information" -- equally inaccessible to them
    -- and (no BPE.CST) denied margin was pointed at "the estimate
    breakdown" -- same problem, different permission. Offers only what this
    user's own permissions actually allow, falling back to something every
    internal role can see regardless (committedSpend/scheduleRisk are
    ungated for internal staff by this agent's own design -- see the
    AB#30855 comment on the default variance_response)."""
    if can_view_invoices:
        return "I can help with your project's billing/invoice information instead."
    if can_view_cost_breakdown:
        return "I can walk you through the estimate breakdown if that's helpful."
    return "I can help with your project's schedule or status instead."


def build_margin_message(
    project_name: str, estimated_cost, margin, can_view_figure: bool, question_type: str = "margin",
    can_view_invoices: bool = False, can_view_cost_breakdown: bool = False,
) -> str:
    """Q3 -- TWO different real permissions feed `can_view_figure` depending
    on `question_type` (see resolve_margin_response, the only caller):
    "total_cost" checks `EST.REA` (read from this user's actual Gold-layer
    permissions); "margin" (default) checks `EST.MOD`
    (UserRoleResolver.is_margin_visible) -- because `EST.REA` is too broad
    for margin specifically (customer/purchaseOfficer hold it too, but not
    EST.MOD; constraints #30-#35). Whichever gate produced it,
    `can_view_figure` is just a bool by the time it reaches this function.

    `question_type="total_cost"` composes a cost-only sentence (no margin
    mentioned) for a bare "what is the total project cost" question --
    same figure source, different phrasing, since stating margin in the
    answer to a question that never asked about margin reads as though the
    question was misheard."""
    if question_type == "total_cost":
        if not can_view_figure:
            return (
                "I'm not able to share the total project cost -- that "
                "isn't available at your access level. "
                + _available_alternative_offer(can_view_invoices, can_view_cost_breakdown)
            )
        if not estimated_cost:
            return f"I don't have a total estimate on file for {project_name} yet."
        return (
            f"The total estimated cost for {project_name} is "
            f"${estimated_cost:,.0f}, based on the latest estimate version."
        )
    if not can_view_figure:
        return (
            "I'm not able to share margin or profitability details -- "
            "that isn't available at your access level. "
            + _available_alternative_offer(can_view_invoices, can_view_cost_breakdown)
        )
    if margin is None or not estimated_cost:
        return f"I don't have margin data available for {project_name} yet."
    pct = (margin / estimated_cost * 100) if estimated_cost else None
    pct_text = f" (about {pct:.1f}%)" if pct is not None else ""
    return (
        f"The profit margin on {project_name} is ${margin:,.0f} on a "
        f"${estimated_cost:,.0f} total estimate{pct_text}, based on the "
        f"latest estimate version."
    )


def resolve_cost_breakdown_response(
    context: SessionContext, match, role_resolver: UserRoleResolver, category: str,
) -> NavigationResponse | None:
    """A bare "what is the material/worker/tool/overhead cost" question --
    confirmed live via direct Mongo query that project_health stores these
    four fields as top-level, fully-populated values (same collection
    resolve_margin_response already reads; verified against a real project:
    materialCost=66.89, workerCost=505.01, toolCost=0, overheadCost=257.35).

    Gated on BPE.CST specifically, NOT is_margin_visible like
    resolve_margin_response above -- unlike margin/estimatedCost (where
    role_permission has no field-level distinction and customer/sales both
    hold the same EST.REA as admin, per build_margin_message's docstring),
    BPE.CST is a REAL, differentiated permission: confirmed live against
    gold_canonical.user_permissions, every customer role in a real tenant
    lacks it while sales/admin/labour+sales combinations hold it. Reusing
    is_margin_visible here would incorrectly deny a sales-only user who
    genuinely holds BPE.CST."""
    try:
        collection = get_collection(PROJECT_HEALTH_COLLECTION)
        doc = collection.find_one(
            {"tenantId": context.tenantId, "projectId": match.project_uid, "isDeleted": {"$ne": True}},
            {"materialCost": 1, "workerCost": 1, "toolCost": 1, "overheadCost": 1, "_id": 0},
        )
    except Exception as e:
        logger.error(f"Cost breakdown query failed for projectId={match.project_uid!r}: {e}")
        return None
    if not doc:
        return None
    label = _ESTIMATE_BUCKET_LABELS.get(category, category)
    permissions = role_resolver.get_permissions(context.tenantId, context.userId)
    if "BPE.CST" not in permissions:
        return NavigationResponse(
            type="guidance",
            message=(
                f"I'm not able to share the {label} breakdown -- that "
                "isn't available at your access level. I can help with "
                "your project's overall status instead."
            ),
            project=ProjectRef(id=match.project_uid, name=match.project_name, status=match.status),
        )
    value = doc.get(category)
    if value is None:
        return NavigationResponse(
            type="guidance",
            message=f"I don't have {label} data on file for {match.project_name} yet.",
            project=ProjectRef(id=match.project_uid, name=match.project_name, status=match.status),
        )
    return NavigationResponse(
        type="guidance",
        message=(
            f"The {label} for {match.project_name} is ${value:,.2f}, "
            f"based on the latest estimate version."
        ),
        project=ProjectRef(id=match.project_uid, name=match.project_name, status=match.status),
    )


class InsightAgent:
    """Project-level status reporter (proposal.md build-order step 2).

    Deliberately narrower than proposal.md's full design: category and
    date_range variance need gold_change_order_impact, which doesn't exist
    yet. estimatedCost/margin (a true "actual vs. estimate" comparison) are
    only ~7% populated in real data -- confirmed live -- so this reports
    committedSpend/outstandingInvoicesTotal/scheduleRisk/slipDays instead,
    the four fields confirmed 100% populated in gold_eden.project_health.
    No reason store, no interviewer/synthesizer, no "at risk" judgment
    beyond the data's own scheduleRisk value -- just the honest numbers."""

    def __init__(self, llm_client=None):
        self.entity_resolver = EntityResolver()
        # Optional: wired by AgentOrchestrator to the same shared LLM client
        # every other agent uses (see agent_orchestrator.__init__) -- used
        # to compose variance_response's money sentence end to end (see
        # LLMClient.compose_variance_message). None-safe: agent-level tests
        # constructing InsightAgent() directly simply get the deterministic
        # fallback template, not a crash.
        self.llm_client = llm_client
        # Own instance, same pattern as EntityResolver above -- used only by
        # the Q3 margin gate (UserRoleResolver.is_margin_visible). Field-level
        # redaction is decided per-agent (see _PROJECT_HEALTH_INTERNAL_ONLY_
        # FIELDS in reporting_agent.py for the same principle), not threaded
        # down from the orchestrator's own role_resolver instance.
        self.role_resolver = UserRoleResolver()
        # Own instance too, same reasoning -- lets Q1/Q2 honestly reference
        # their OWN prior answer (see apply_reasoning_continuity) without
        # needing anything threaded down from the orchestrator's own
        # conversation_memory (which handles project continuity/goal-state,
        # a different concern).
        self.conversation_memory = ConversationMemory()

    def variance_response(
        self, context: SessionContext, entity_name: str | None,
        allowed_project_ids: list[str] | None = None,
        entity_id: str | None = None,
        intent_utterance: str | None = None,
    ) -> NavigationResponse | None:
        """Three outcomes:
        - No name/id given at all -> list the tenant's projects and ask which
          one (a real disambiguation, not the generic deflection).
        - Name given, ambiguous (>1 match) -> disambiguate among those specific
          matches.
        - Name given, no match at all -> None (a wrong name isn't "list
          everything"; caller falls back to NavigationAgent.data_question_response,
          same no-regression deflection as before).
        - Name/id given, exactly one match -> the real numeric answer, or None on
          missing/malformed data / Mongo unreachable (same deflection fallback).

        `allowed_project_ids`: this user's Gold-scoped project_ids, if any
        (see EntityResolver.list_projects for the "empty means unscoped, not
        zero projects" reasoning) -- threaded straight through to enforce
        project-level scoping once real data populates it.

        `entity_id`: an exact projectId (from page context -- the user is
        viewing this project). When present it takes precedence over
        entity_name and resolves by exact id (see EntityResolver.resolve_by_id)
        -- exact and immune to the duplicate-name problem.

        `intent_utterance`: the ORIGINAL question's text, when this call is
        continuing an open "which project?" goal -- `context.utterance` at
        that point is just the reply (a project name/selection), which
        carries none of the original question's signal words. Same fix as
        ReportingAgent.respond's own `intent_utterance` param (see
        AgentOrchestrator._insight_node); defaults to `context.utterance`
        for a fresh, non-continuation question."""
        utterance_lower = (intent_utterance if intent_utterance is not None else context.utterance or "").lower()

        # Real gap, fixed: margin/total_cost permission (EST.MOD/EST.REA) is
        # a USER-level gate, not a per-project one -- it never depends on
        # which project gets picked. But the old flow always resolved (or,
        # with no project named, asked "which project?") FIRST, deferring
        # the permission check until after a match was found deep inside
        # _margin_response/_total_cost_response below. Confirmed live: a
        # Labor user (no EST.REA) asking "what is the project estimate"
        # with no project named got a full "which project?" round-trip
        # before ever being told they don't have access -- needless UX for
        # a question this agent can already tell is cost-typed from the
        # phrasing alone, before any project is involved. Not a data leak
        # (the project list itself carries no cost figures -- see
        # EntityResolver.list_projects/_scoped_project_ids gating), just an
        # unnecessary detour. Checking here short-circuits BEFORE entity
        # resolution, for both the "no project named" and "project named"
        # cases alike, since the gate doesn't care which project.
        if _is_margin_question(utterance_lower) or _is_total_cost_question(utterance_lower):
            # Fetched once, reused for whichever gate applies below AND for
            # the denial's dynamic "here's what I CAN help with" offer --
            # see _available_alternative_offer's docstring for why this
            # can't be a hardcoded suggestion (Labor lacks INV.REA/BPE.CST
            # just as much as EST.REA/EST.MOD).
            permissions = self.role_resolver.get_permissions(context.tenantId, context.userId)
            can_view_invoices = "INV.REA" in permissions
            can_view_cost_breakdown = "BPE.CST" in permissions
            if _is_margin_question(utterance_lower):
                if not self.role_resolver.is_margin_visible(context.tenantId, context.userId):
                    return NavigationResponse(
                        type="guidance",
                        message=build_margin_message(
                            "", None, None, False,
                            can_view_invoices=can_view_invoices,
                            can_view_cost_breakdown=can_view_cost_breakdown,
                        ),
                    )
            elif "EST.REA" not in permissions:
                return NavigationResponse(
                    type="guidance",
                    message=build_margin_message(
                        "", None, None, False, question_type="total_cost",
                        can_view_invoices=can_view_invoices,
                        can_view_cost_breakdown=can_view_cost_breakdown,
                    ),
                )

        if not entity_id and not entity_name:
            projects = self.entity_resolver.list_projects(
                context.tenantId, limit=_project_list_limit(context.platform),
                allowed_project_ids=allowed_project_ids,
            )
            return self._projects_disambiguation(projects)

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
            capped = matches if limit == 0 else matches[:limit]
            return self._projects_disambiguation(capped)

        match = matches[0]

        # New reasoning requirement (estimate-change / schedule-move /
        # margin) -- checked before the default cost-variance query below,
        # same "closed keyword vocabulary decides which of OUR OWN views"
        # pattern as Reporting's billing-vs-status split. entity_id/
        # entity_name resolution above is shared; only the query + message
        # differ per question type. (utterance_lower already computed above,
        # ahead of the early margin/total_cost permission short-circuit.)
        if _is_margin_question(utterance_lower):
            return self._margin_response(context, match)
        if _is_estimate_change_question(utterance_lower):
            return self._estimate_change_response(context, match)
        if _is_schedule_change_question(utterance_lower):
            return self._schedule_change_response(context, match)
        if _is_total_cost_question(utterance_lower):
            return self._total_cost_response(context, match)
        cost_breakdown_category = _cost_breakdown_category(utterance_lower)
        if cost_breakdown_category is not None:
            return self._cost_breakdown_response(context, match, cost_breakdown_category)

        try:
            collection = get_collection(PROJECT_HEALTH_COLLECTION)
            doc = collection.find_one(
                {"tenantId": context.tenantId, "projectId": match.project_uid, "isDeleted": {"$ne": True}},
                {
                    "orderNo": 1, "committedSpend": 1, "outstandingInvoicesTotal": 1,
                    "remainingBudget": 1, "scheduleRisk": 1, "slipDays": 1, "_id": 0,
                },
            )
            if not doc:
                return None

            # committedSpend/outstandingInvoicesTotal are confirmed 100%
            # non-null in real data, but coerce defensively anyway -- field
            # types aren't documented, treat anything malformed as "can't
            # confidently answer" rather than crashing the request.
            committed_spend = float(doc["committedSpend"])
            outstanding_invoices = float(doc["outstandingInvoicesTotal"])
            # AB#30855: outstandingInvoicesTotal is an invoice figure, same
            # class of data resolve_margin_response already gates on a real
            # permission code (EST.REA/EST.MOD) -- this default variance
            # path had no equivalent check at all. INV.REA is the same code
            # already live on project_invoice_view/project_invoice_preview
            # in route_registry.json, not invented here. Confirmed live:
            # Labor holds neither.
            _permissions = self.role_resolver.get_permissions(
                context.tenantId, context.userId
            )
            can_view_invoices = "INV.REA" in _permissions
            # Real gap, fixed: committedSpend was left ungated on the
            # (unverified) assumption that it's "a different, internal cost
            # concern" every internal role can see. Checked directly against
            # the real application: the equivalent UI (Job Ledger's "Total
            # Job Costs"/"Actual Cost") is gated on JBL.REA
            # (PagePermission.tsx: ProjectInfoJobLedger: ["JBL.REA"]).
            # Confirmed live: Labor holds neither JBL.REA nor INV.REA. A
            # genuine $0 is still real, honest data -- but showing it
            # unconditionally to a role the real app never shows Job Ledger
            # to at all is the same class of leak as the invoice/estimate
            # fixes above, just one field later.
            can_view_committed_spend = "JBL.REA" in _permissions
            # Sibling gap to AB#30855, found live in the same session:
            # remainingBudget = estimatedCost - committedSpend -- the SAME
            # protected estimate figure resolve_margin_response's
            # total_cost branch already gates on EST.REA. This default
            # variance path (reached by "why is cost over budget" and any
            # other phrasing that doesn't match TOTAL_COST_SIGNALS) composed
            # a sentence naming the dollar amount over/under "the original
            # estimate" with no permission check at all. Confirmed live: a
            # Labor user (no EST.REA) asking "what is the project estimate"
            # got "$618 remains under the original estimate." Gate ONLY the
            # budget-status sentence -- committedSpend/scheduleRisk stay
            # ungated, same as the invoice fix above.
            can_view_estimate = "EST.REA" in _permissions
            # remainingBudget = estimatedCost - committedSpend, pre-computed
            # by Gold -- genuinely null (not just falsy) whenever no
            # estimate exists yet (_dataCompleteness.estimate=false;
            # confirmed live: Buma P has no estimate and remainingBudget is
            # null, Peter Parker has one and it's -290.20 -- already $290
            # over its original estimate). This is the one field that turns
            # "here's the committed spend" into an actual over/under-budget
            # verdict, which is what "why is cost over budget" (the
            # question that routes here) is actually asking -- see
            # _budget_status_sentence below.
            remaining_budget = doc.get("remainingBudget")
            remaining_budget = float(remaining_budget) if remaining_budget is not None else None
            schedule_risk = doc.get("scheduleRisk") or "unknown"
            slip_days = doc.get("slipDays")
            slip_days = int(slip_days) if slip_days is not None else None
        except Exception as e:
            logger.error(
                f"Insight variance query failed for projectId="
                f"{match.project_uid!r}: {e}"
            )
            return None

        # Traceability: which project (by id AND order number, not just
        # name) and the exact raw field values pulled from Mongo, so the
        # final chat answer can be manually cross-checked against Gold
        # directly -- e.g. `db.project_health.find({"projectId": "..."})`
        # should show these same numbers verbatim.
        logger.info(
            "Insight variance data: project=%r projectId=%s orderNo=%s -- "
            "committedSpend=%s outstandingInvoicesTotal=%s remainingBudget=%s "
            "scheduleRisk=%r slipDays=%s",
            match.project_name, match.project_uid, doc.get("orderNo"),
            committed_spend, outstanding_invoices, remaining_budget, schedule_risk, slip_days,
        )

        # Per explicit user direction: the LLM composes this money sentence
        # end to end (including how to phrase a genuinely zero figure) via
        # LLMClient.compose_variance_message, tried first here. The
        # if/elif chain below is the deterministic FALLBACK -- used when
        # self.llm_client isn't wired (agent-level tests constructing
        # InsightAgent() directly) or the composition call fails/fails its
        # own real-figure verification. Real gap this fallback (and the
        # LLM composer) both fix: a flat "$X committed, $Y outstanding
        # invoices" template reads oddly whenever one of the two is
        # genuinely zero -- "$0 committed" sounds like missing data, not
        # "nothing spent so far"; "$0 outstanding invoices" alongside real
        # committed spend actually means "fully settled internally."
        message = None
        if self.llm_client is not None and can_view_invoices and can_view_committed_spend:
            message = self.llm_client.compose_variance_message(
                match.project_name, committed_spend, outstanding_invoices
            )
        if message is not None:
            message = message.rstrip() + " "
        elif not can_view_committed_spend and not can_view_invoices:
            message = (
                f"{match.project_name}: job cost and invoice details "
                "aren't available at your access level. "
            )
        elif not can_view_committed_spend:
            message = (
                f"{match.project_name}: job cost details aren't available "
                "at your access level. "
                + (
                    "No outstanding invoices. " if outstanding_invoices == 0
                    else f"${outstanding_invoices:,.0f} outstanding invoices. "
                )
            )
        elif not can_view_invoices:
            message = (
                f"{match.project_name}: ${committed_spend:,.0f} committed. "
                "Invoice details aren't available at your access level. "
            )
        else:
            if committed_spend == 0:
                message = f"{match.project_name} has no committed spend yet. "
            elif outstanding_invoices == 0:
                message = (
                    f"{match.project_name}: ${committed_spend:,.0f} committed, "
                    f"no outstanding invoices. "
                )
            else:
                message = (
                    f"{match.project_name}: ${committed_spend:,.0f} committed, "
                    f"${outstanding_invoices:,.0f} outstanding invoices. "
                )
        if remaining_budget is not None and can_view_estimate:
            message += self._budget_status_sentence(remaining_budget) + " "
        if schedule_risk == "at_risk" and slip_days:
            message += f"Schedule: at risk, {slip_days} days behind."
        elif schedule_risk == "unknown":
            message += "Schedule: not yet available."
        else:
            message += "Schedule: on track."

        # Real bug, fixed: without `project` set here, a request resolved
        # via PAGE CONTEXT (entity_id, not a spoken name -- by far the more
        # common flow: viewing a project's page and just asking) gave the
        # orchestrator no way to know WHICH project this answer was about,
        # so AgentOrchestrator._insight_node's set_last_project(...) call
        # (gated on `entity_name`) never fired for it -- silently leaving
        # "last discussed project" stale/unset even after a real, resolved
        # answer, and starving the project-context-switch confirmation
        # (see _maybe_confirm_project_context_switch) of anything to
        # compare against.
        return NavigationResponse(
            type="guidance", message=message,
            project=ProjectRef(id=match.project_uid, name=match.project_name, status=match.status),
        )

    @staticmethod
    def _budget_status_sentence(remaining_budget: float) -> str:
        """Turns project_health's pre-computed remainingBudget (estimatedCost
        - committedSpend) into an explicit over/under-budget verdict --
        deterministic, not LLM-composed, since the three cases (negative/
        positive/exactly zero) are exhaustive and unambiguous, unlike the
        genuinely open-ended zero-value phrasing compose_variance_message
        handles. Kept as a separate sentence/method (not folded into the
        LLM composer's prompt) so this addition doesn't touch that
        already-verified contract at all."""
        if remaining_budget < 0:
            return f"This is ${abs(remaining_budget):,.0f} over the original estimate."
        if remaining_budget > 0:
            return f"${remaining_budget:,.0f} remains under the original estimate."
        return "Exactly on budget against the original estimate."

    def _projects_disambiguation(self, matches: list) -> NavigationResponse | None:
        return projects_disambiguation(matches)

    def _margin_response(self, context: SessionContext, match) -> NavigationResponse | None:
        """Q3 -- gated on the real EST.MOD permission (see
        UserRoleResolver.is_margin_visible, constraints #30-#35): held by
        admin/sales/superAdmin, confirmed live. Reachable via Insight
        because a non-"customer" role (e.g. purchaseOfficer, foreman) is
        not routed away at Layer 1 the way a customer role is -- some of
        those reaching Insight hold EST.MOD, some don't. Thin wrapper
        only -- see resolve_margin_response, the single shared function
        BOTH agents delegate to; this method must never query `margin` or
        call is_margin_visible itself."""
        return resolve_margin_response(context, match, self.role_resolver)

    def _total_cost_response(self, context: SessionContext, match) -> NavigationResponse | None:
        """A bare "what is the total project cost"/"what's the budget"
        question -- gated on EST.REA (a DIFFERENT permission than
        _margin_response's EST.MOD -- see resolve_margin_response's
        docstring for why) and same estimatedCost source as
        _margin_response, just phrased without stating margin (see
        build_margin_message's question_type="total_cost" branch). Real
        gap this closes: before this, neither agent had any path that
        stated estimatedCost for this phrasing -- it fell through to the
        default variance view instead, which answers with committedSpend
        (a different, sometimes coincidentally-equal number), silently
        substituting the wrong figure for what was actually asked. Thin
        wrapper only, same discipline as _margin_response."""
        return resolve_margin_response(context, match, self.role_resolver, question_type="total_cost")

    def _cost_breakdown_response(
        self, context: SessionContext, match, category: str
    ) -> NavigationResponse | None:
        """"What is the material/worker/tool/overhead cost" -- thin
        wrapper only, same discipline as _margin_response/
        _total_cost_response above: resolve_cost_breakdown_response owns
        the query and the BPE.CST gate, this method never touches Mongo
        or role_resolver.get_permissions itself."""
        return resolve_cost_breakdown_response(context, match, self.role_resolver, category)

    def _estimate_change_response(self, context: SessionContext, match) -> NavigationResponse | None:
        """Q1 -- internal role, so include_figures=True (real dollar delta,
        not just the cause) -- see build_estimate_change_message. Confirmed
        via EDEN_CustomerSupport_Reasoning_Constraints.md constraints #1-#11
        that project_estimate.material_cost == SUM(bom.total_cost)
        reconciles; the gold-layer estimateHistory[].confidence/evidence
        fields are how that reconciliation surfaces here without Insight
        querying Postgres directly."""
        try:
            collection = get_collection(PROJECT_HEALTH_COLLECTION)
            doc = collection.find_one(
                {"tenantId": context.tenantId, "projectId": match.project_uid, "isDeleted": {"$ne": True}},
                {"estimateHistory": 1, "estimateSummary": 1, "_id": 0},
            )
        except Exception as e:
            logger.error(f"Insight estimate-change query failed for projectId={match.project_uid!r}: {e}")
            return None
        if not doc:
            return None
        history = doc.get("estimateHistory") or []
        summary = doc.get("estimateSummary") or {}
        current = next((e for e in history if e.get("isCurrent")), None)
        # len(history) < 2 alone is not "never changed" -- see
        # estimate_changed_per_summary for the live case it got wrong.
        if current is None or (len(history) < 2 and not estimate_changed_per_summary(history, summary)):
            message = f"{match.project_name}'s estimate has not changed since it was first prepared."
        else:
            current = _prefer_non_degenerate_sibling(history, current)
            message = build_estimate_change_message(
                match.project_name, current, include_figures=True, summary=summary,
            )
            confidence = current.get("confidence")
            # "Do I already know I told this user this before?" -- honest
            # self-reference only (same project, same question_type), never
            # a fabricated cross-topic link to Q2. See
            # apply_reasoning_continuity's docstring.
            message = apply_reasoning_continuity(
                self.conversation_memory, context.tenantId, context.userId,
                match.project_uid, "estimate_change", message, confidence,
            )
            # Backlog #26159: capture every Q1 attempt so an unresolved one
            # (no confirmed cause) can be surfaced to a human -- see
            # write_reasoning_audit_log's docstring.
            write_reasoning_audit_log(
                context.tenantId, context.userId, "insight", match.project_uid,
                "estimate_change", confidence,
                "resolved" if confidence in ("high", "low") else "unresolved",
            )
        return NavigationResponse(
            type="guidance", message=message,
            project=ProjectRef(id=match.project_uid, name=match.project_name, status=match.status),
        )

    def _schedule_change_response(self, context: SessionContext, match) -> NavigationResponse | None:
        """Q2 -- see build_schedule_change_message. This reasoning is
        correlational, not deterministic (constraints #12-#29: no FK proves
        a freed-up resource caused this project's schedule to move) -- the
        wording stays hedged whenever confidence is "low", regardless of
        role, since the underlying data can't support a stronger claim."""
        try:
            collection = get_collection(PROJECT_HEALTH_COLLECTION)
            doc = collection.find_one(
                {"tenantId": context.tenantId, "projectId": match.project_uid, "isDeleted": {"$ne": True}},
                {"scheduleRevisions": 1, "scheduleSummary": 1, "_id": 0},
            )
        except Exception as e:
            logger.error(f"Insight schedule-change query failed for projectId={match.project_uid!r}: {e}")
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
            # Backlog #26159: same capture as _estimate_change_response.
            # Q2's confidence ceiling is "low" (never "high"), so "low" is
            # still counted as resolved here -- it's a hedged but genuine
            # answer, not a non-answer.
            write_reasoning_audit_log(
                context.tenantId, context.userId, "insight", match.project_uid,
                "schedule_move", confidence,
                "resolved" if confidence in ("high", "low") else "unresolved",
            )
        return NavigationResponse(
            type="guidance", message=message,
            project=ProjectRef(id=match.project_uid, name=match.project_name, status=match.status),
        )


def _project_list_limit(platform: str | None) -> int:
    """Mongo's .limit(0) means "no limit". Per explicit user direction
    (2026-09-03), web's card list now also handles any count via
    OptionCardList's internal scroll (see projects_disambiguation) instead
    of redirecting to the Projects page above the old cap -- so both
    platforms fetch every project up front, not just mobile. `platform` is
    unused now but kept on the signature so call sites don't need to change
    again if a platform-specific fetch limit ever comes back."""
    return 0


def projects_disambiguation(matches: list) -> NavigationResponse | None:
    """Builds a disambiguation response listing candidate projects. Shared
    by Insight and Reporting (both need the same "which project?" ask).
    Returns None when there's nothing to offer (empty tenant/no matches)
    -- caller falls back to the generic deflection, same as any other
    can't-confidently-answer case.

    Per explicit user direction (reversing an earlier ">4 -> redirect to
    Projects page" behavior): ANY project-related question that names no
    project must ask "which project?" with an actual pick-list, consistently,
    never silently aggregate. Real customers can have a dozen+ real projects
    sharing one name (see EntityResolver.project_ids_for_customer) -- the
    order number appended to each label (below) is what makes them
    distinguishable.

    Real gap, fixed (2026-09-03): a total beyond the old 30-project cap used
    to redirect to the real Projects list page on web instead of listing --
    reversed per explicit user direction once OptionCardList
    (NavigationMessage.tsx) grew an internal vertical scroll that already
    handles any option count without the chat panel growing unbounded (capped
    to ~5 cards' worth of visible height). So the full (uncapped) set of
    matches is always shown as clickable cards, on every platform -- no more
    partial "showing X of Y" list and no redirect; order number remains an
    explicit escape hatch either way (_resolve_by_order_no is an exact match
    against the full dataset, never capped by this limit)."""
    if not matches:
        return None
    question = "Which project would you like to know about?"
    options = [
        NavigationOption(
            # Real Gold data has severe projectName duplication (one
            # placeholder name can cover hundreds of projects) -- append the
            # order number so two same-named projects are actually
            # distinguishable in the list, not just visually identical.
            label=f"{m.project_name} (Order #{m.order_no})" if m.order_no else m.project_name,
            route=PROJECT_INFORMATION_ROUTE,
            project=ProjectRef(id=m.project_uid, name=m.project_name, status=m.status),
            # Real bug, fixed: `project` alone lands in state.project on the
            # frontend (see Chat.tsx's handleNavigate), but
            # ProjectInformation_v2.tsx reads state.rowData -- verified
            # live, the real View button passes the bare project id under
            # that exact key (onProjectInformationPage(project.id, ...)).
            # Without this, picking a project here landed on a blank page.
            routeState={"rowData": m.project_uid, "projectId": m.project_uid, "projectName": m.project_name},
        )
        for m in matches
    ]
    return NavigationResponse(
        type="disambiguation",
        question=question,
        options=options,
    )
