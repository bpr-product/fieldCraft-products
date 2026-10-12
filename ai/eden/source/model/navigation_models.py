from typing import Literal
from pydantic import BaseModel, Field, field_validator


class NavigationUnderstanding(BaseModel):
    """The LLM triage contract — shared across every backend (raw OpenAI SDK,
    LangGraph, Agents SDK, combined) as a JSON Schema constraint on
    generation ("structured outputs"), not a shape hoped for after the fact.
    Passed as response_format / with_structured_output / output_type
    depending on the backend; the runtime rejects any token sequence that
    wouldn't validate against this model, so the old free-text parsing
    (STRICT_SUFFIX retries, <think>-tag stripping, brace-hunting in
    llm_client._extract_json) is no longer the primary path — see that
    module's docstring for where it still exists as a last-resort fallback.
    """
    intent: Literal["navigate", "data_question", "guidance", "out_of_scope"]
    route_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    extracted_entity: str | None = None
    insight: str | None = None


class AmbiguityVerification(BaseModel):
    """Multi-step reasoning trial (MULTI_STEP_VERIFICATION env flag, see
    agent_orchestrator.py's _maybe_verify_ambiguity) — a second, independent
    LLM pass that double-checks a confident primary classification when
    retrieval found two candidate routes close enough to genuinely compete.
    Small models are routinely overconfident (observed: "where i can see the
    customer information" scored customer_table 0.301 vs
    customer_info_dashboard 0.276 in retrieval, a near-tie, yet the primary
    classifier returned confidence 0.95) — this doesn't re-ask the same
    question, it asks a differently framed one with the retrieval evidence
    made explicit, rather than trusting the same self-reported number twice.
    """
    is_ambiguous: bool
    plausible_route_ids: list[str] = []
    reasoning: str | None = None


class AgentTriage(BaseModel):
    """eden_router's structured-output contract for the triage LLM call
    (LLMClient.triage) — the top-level "which specialist handles this
    request" decision. Runs on every request by explicit design: this
    decision is an open-ended understanding problem (unlike Navigation's
    internal greeting/exact-keyword tiers, which match closed, tiny
    vocabularies), so it belongs to the model, not a maintained keyword
    list. The old VARIANCE_SIGNALS keyword check survives only as the
    degraded fallback when this call fails.

    `confidence` mirrors Navigation's own low-confidence -> disambiguate
    pattern (see agent_orchestrator.ROUTE_DISAMBIGUATION_CONFIDENCE_THRESHOLD)
    applied at this top level: an ambiguous money question (internal cost
    vs. the user's own bill) should trigger a clarifying question instead of
    a silent guess, the same discipline already applied to page routing."""
    agent: Literal["navigation", "insight", "reporting"]
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reasoning: str | None = None


class ProjectNameExtraction(BaseModel):
    """Structured-output contract for LLMClient.extract_project_name — the
    "which project is this message about?" question, answered by reading
    the sentence rather than subtracting stop-words from it. project_name
    is None when the message names no specific project — a VALID answer,
    distinct from the call itself failing (the method returns None for
    that, and the caller degrades to the old stop-word extraction)."""
    project_name: str | None = None


class ProjectContextSwitchDecision(BaseModel):
    """Structured-output contract for LLMClient.decide_project_context_switch
    — end-to-end LLM judgment (per explicit user direction, replacing what
    was a flat deterministic string-compare) for whether a page-vs-last-
    discussed-project mismatch is worth interrupting the user about. Most
    such mismatches are NOT worth asking about (the user simply navigated
    on and the new page is obviously what they mean) — `ask` should be
    `True` only when the message itself carries a real signal that the
    user might still mean the previously-discussed project (e.g. "that
    project", "the one I just asked about"), not merely because the pages
    differ. When `ask` is True, `question` is the model's own natural
    one-sentence confirmation naming both projects."""
    ask: bool
    question: str | None = None
    reasoning: str | None = None


class ProjectContextSwitchReply(BaseModel):
    """Structured-output contract for
    LLMClient.resolve_project_context_switch_reply — interprets the user's
    reply to a project-context-switch confirmation. `choice` is "last" (the
    previously-discussed project), "page" (the project on their current
    page), or "other" (the reply names neither — e.g. a third project, or
    is unrelated — caller falls back to resolving the reply as a fresh
    question)."""
    choice: Literal["last", "page", "other"]
    reasoning: str | None = None


class RelatedRoutesSuggestion(BaseModel):
    """Structured-output contract for LLMClient.suggest_related_routes --
    replaces the old DATA_TOPIC_ROUTES keyword table (a fixed phrase->route
    lookup, e.g. "margin"/"profit"/"revenue" -> tenant_finance) with the
    model reading the actual question and picking, from the specific
    candidate routes it was shown, which ones plausibly hold the answer.
    Used for the "I couldn't compute that, but here's where you'd look"
    redirect (NavigationAgent.data_question_response) -- offered as
    clickable bubbles, so a wrong guess is low-stakes (the user just gets
    an extra button), unlike a navigate/entity decision.

    route_ids must be a subset of the candidate route ids it was given --
    never invented. Capped at 3 by the caller; return fewer (or none) if
    fewer genuinely fit rather than padding to 3."""
    route_ids: list[str] = []


class BillingIntentCheck(BaseModel):
    """Instrumentation only (LLMClient.classify_billing_intent) — never
    used to decide routing. ReportingAgent.BILLING_SIGNALS is a closed
    keyword list, structurally the same shape as the old top-level router
    that provably misrouted phrasing it wasn't written for. Rather than
    assume this smaller list is safe, a sample of requests are checked
    against this call and any disagreement is logged — real evidence
    before trusting or replacing the keyword list."""
    is_billing: bool
    reasoning: str | None = None


class DateIntentCheck(BaseModel):
    """PRIMARY decision (LLMClient.classify_date_intent) for ReportingAgent's
    date-vs-status fork -- replaces a keyword-list-only decision (DATE_SIGNALS
    survives only as the degraded fallback for when this call fails, same
    pattern as BILLING_SIGNALS/classify_billing_intent's relationship,
    except that call remains audit-only while this one is load-bearing per
    explicit user direction: a keyword list deciding an open-ended question
    can silently misroute phrasing it wasn't written for)."""
    is_date_question: bool
    reasoning: str | None = None


class GoalContinuationDecision(BaseModel):
    """PRIMARY decision (LLMClient.classify_goal_continuation) for
    eden_router's "is this message an ANSWER to the question EDEN just
    asked, or a brand-new request?" fork.

    Exists because the pending-goal branch used to be an unconditional
    override: ANY open goal captured the next GOAL_MAX_ATTEMPTS messages
    and routed them to the goal's owning agent with no LLM call at all --
    so an unrelated new question ("show me the customer dashboard") got
    swallowed as if it were the reply to a stale one ("what are the leads
    we have") and answered wrong. Confirmed live from the dev logs. That
    was the same "hardcoded routing wearing an AI label" failure mode the
    VARIANCE_SIGNALS removal already fixed one layer up, so it gets the
    same treatment: the model reads the actual message against the actual
    question, with a confidence field and a deterministic fallback.

    `confidence` is deliberately consulted only to CONTINUE a goal (see
    GOAL_CONTINUATION_CONFIDENCE_THRESHOLD): continuing wrongly cascades
    (it hijacks up to three turns and answers the wrong question), while
    dropping wrongly costs one clarifying re-ask. Asymmetric cost, so the
    tie-breaker favours dropping."""
    is_reply: bool
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reasoning: str | None = None


class SessionContext(BaseModel):
    utterance: str
    currentRoute: str
    currentEntity: dict | None = None
    timeOnPage: int = 0
    routeHistory: list[str] = []
    userId: str
    tenantId: str
    # Client-supplied, same trust level as currentRoute/currentEntity -- never
    # security-sensitive (unlike userId/tenantId, always overwritten from the
    # verified JWT). Only changes WHICH specialist answers and whether the
    # response carries route data, never what data a caller is allowed to see
    # -- misreporting this degrades that caller's own UX, nothing more, so it
    # doesn't need JWT-level verification. None/anything unrecognized behaves
    # exactly like "web" (today's behavior) -- see _eden_router_node.
    platform: str | None = None


class ProjectMatch(BaseModel):
    project_uid: str
    project_name: str
    status: str
    # Real Gold data has severe projectName duplication (one placeholder name
    # can cover hundreds of projects) -- order_no is the real, unambiguous
    # per-project identifier shown alongside the name so a listed project can
    # actually be told apart from another with the same name. None when the
    # source document has no orderNo (synthetic fixtures, older data).
    order_no: str | None = None


class PageFeature(BaseModel):
    """A specific, findable thing on a page (a button, filter, tab...) so EDEN
    can answer "where is X" for something the user can't locate, not just
    "what page has X" navigation."""
    name: str
    keywords: list[str]
    hint: str
    # Ordered text candidates the frontend uses to locate this element in the
    # live DOM (button label, tab text, input placeholder...) — first match
    # wins. Enables the visual spotlight, not just a text hint.
    locate: list[str] = []
    # Router state needed to actually LAND on this feature, when the
    # feature lives on a specific tab of a shared shell page rather than
    # the route's own bare pattern — e.g. admin_home's "QuickBooks tab"
    # feature needs {"selectedTab": "9"} to open Admin on the right tab,
    # the same way RouteEntry.target_route_state works for whole routes.
    # Without this, a cross-page suggestion for a tab-scoped feature could
    # only ever point at the bare page (whatever tab happens to already be
    # selected), not the specific tab the feature is actually on.
    route_state: dict | None = None
    # Ordered text candidates for a SECOND element to ring only after
    # autoClick has actually opened/clicked the first one -- for a feature
    # that's a closed dropdown/menu/dialog trigger rather than a direct
    # target, e.g. "Material Fields dropdown" (locate) reveals per-column
    # checkboxes only once opened. Used as the FALLBACK when nothing in
    # post_click_options matches (see below) -- ignored unless this
    # feature's response also sets autoClick=True, since ringing a second
    # element with no click never happened is meaningless.
    post_click_locate: list[str] = []
    # For a dropdown/menu whose revealed options are themselves a set of
    # named things a user could ask about (Material Fields' column
    # checkboxes: Selling Price, Vendor Name, Grade...) -- maps a keyword
    # phrase to the locate candidates for THAT specific option, so "where
    # can i add sell price" points into the opened menu at "Selling Price"
    # specifically, while "show me the vendor name field" (same dropdown,
    # different option) points at "Vendor Name" instead, rather than one
    # hardcoded option regardless of what was actually asked. Checked
    # against the SAME utterance already used to match this page_feature.
    post_click_options: dict[str, list[str]] = {}
    # Same "None means no permission required" convention as
    # RouteEntry.permission_required (single required code) -- for a
    # feature gated on exactly one real permission code the OWNING route
    # doesn't already require (e.g. a route open to every role that has one
    # button only some of those roles may use). Most features leave this
    # unset and are gated only by their route's own permission_required, as
    # before -- only set this when the real app gates the specific
    # button/element itself more narrowly than the page it lives on.
    permission_required: str | None = None
    # For a feature reachable via more than one real permission code (e.g.
    # "Cart & Drafts" -- CART.REA for the customer path, SALESCART.REA for
    # the internal sales/admin path, BID.REA at the page level) where ANY
    # one of them is sufficient. Checked as an OR; leave unset for the
    # single-code case above.
    permission_required_any: list[str] | None = None


class RouteEntry(BaseModel):
    id: str
    pattern: str
    icon: str = "📄"
    intent_keywords: list[str]
    # Only "project" is actually implemented (NavigationAgent._finalize,
    # _resolve_with_entity, resolve_entity_route_for_project, etc. all key
    # off this exact string). Any other truthy value still gets the
    # premature fuzzy-claim exemption in exact_keyword_route (a route with
    # entity_required set skips the "every token must be explained"
    # vocabulary check), but no resolver ever runs for it -- a spoken
    # entity name is silently discarded and the route falls back to
    # _finalize's generic ":"-in-pattern ancestor-redirect (no crash, but
    # no name resolution either). Route contracts must use "project" or
    # null/None -- don't introduce a new value without adding the matching
    # resolver first.
    entity_required: str | None
    # None means "no permission required" -- a handful of real pages (Home,
    # Welcome, chat UI, image upload, customer registration, and the
    # anonymous pages outside ProtectedRoute like /cancel, /plans,
    # /quickbooks/callback) have zero permission check in the real app (see
    # NavigationAgent._check_permission's matching note). /invoicepayment
    # was also one of these, but was removed from the registry entirely --
    # a public, emailed-link-only portal with no invoice token to supply,
    # EDEN can't navigate there meaningfully at all (same class of gap as
    # invoice_list, just link-only instead of crash-on-load).
    permission_required: str | None
    label_template: str
    description: str = ""
    page_help: str = ""
    page_features: list[PageFeature] = []
    module: str
    # For routes whose bare `pattern` renders a stripped-down page (no shell,
    # no Notes panel) while the "real" page is a shared shell reached via a
    # different path + router state — e.g. the material catalogue is a full
    # page at /materialmanagement, but the app's own navigation only ever
    # reaches it via /materialmart with state={selectedTab: "materials"}
    # (AB#32825; it was /admin tab "5" before), which is what renders Material
    # Mart's sub-tab bar around the same component. `pattern` is left untouched
    # (keyword-matching/guidance/page_help all key off it); target_route and
    # target_route_state are what the frontend actually navigates to when set
    # — see NavigationAgent._finalize's use of target_route().
    target_route: str | None = None
    target_route_state: dict | None = None

    def resolved_route(self) -> str:
        return self.target_route or self.pattern

    def resolved_route_state(self) -> dict | None:
        return self.target_route_state


class ProjectRef(BaseModel):
    id: str
    name: str
    status: str


class NavigationOption(BaseModel):
    label: str
    route: str
    icon: str | None = None
    description: str | None = None
    project: ProjectRef | None = None
    routeState: dict | None = None
    # Real bug, fixed: confirmed live -- a disambiguation between two pages
    # that both have the same matching page_feature (e.g. "Invite Customer"
    # on both Leads & Customers and Admin > Customers) used to land the
    # user on a bare page with no spotlight at all once they picked one,
    # even though the SAME utterance would have triggered a highlighted,
    # auto-clicked navigate had only one route matched. Mirrors
    # NavigationResponse's own highlightTargets/autoClick so the frontend's
    # PageSpotlight has something to point at after an option is chosen,
    # not just after a direct navigate.
    highlightTargets: list[str] | None = None
    autoClick: bool = False


class NavigationSuggestion(BaseModel):
    label: str
    route: str
    # See NavigationOption.routeState / PageFeature.route_state — without
    # this, feature-lookup's cross-page "that's on the X page" suggestion
    # (built in navigation_agent.try_feature_lookup_scored) could only ever
    # point at a route's bare pattern, never a specific tab within it.
    routeState: dict | None = None


class NavigationResponse(BaseModel):
    type: Literal["navigate", "disambiguation", "fallback", "guidance"]
    route: str | None = None
    label: str | None = None
    icon: str | None = None
    description: str | None = None
    module: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    message: str | None = None
    insight: str | None = None
    # Short label of a specific on-page element (button/filter/tab) the answer
    # points to — e.g. "Export Invoices button". Shown as the spotlight caption.
    highlight: str | None = None
    # Ordered text candidates the frontend uses to locate the element in the
    # live DOM and draw the visual spotlight over it. First visible match wins.
    highlightTargets: list[str] | None = None
    # True only for a genuine action request ("I need to add a new material")
    # where NavigationAgent.try_feature_lookup_scored decided to navigate
    # there directly rather than just describe it (see
    # _INFORMATIONAL_LOOKUP_RE) -- tells the frontend's PageSpotlight to
    # click the located element, not just ring it. Never set for a plain
    # data answer pointing at a value on the page, and normally not for a
    # "where is X" locational question either -- with ONE exception: when
    # the answer lives inside a container that must be opened to exist at
    # all (postClickHighlightTargets is set, e.g. Selling Price inside the
    # closed Material Fields dropdown), opening it IS the answer, so a
    # "where can i add sell price" question clicks too. Ringing a closed
    # dropdown and saying "it's in there" answers nothing.
    autoClick: bool = False
    # Ordered text candidates for a SECOND element to ring only once
    # autoClick has actually clicked the first one open -- e.g. clicking
    # "Material Fields" opens a dropdown; "Selling Price" (a checkbox
    # inside it) doesn't exist in the DOM until then, so it can't be the
    # PRIMARY highlightTargets. Ignored by the frontend unless autoClick
    # is also True. See PageFeature.post_click_locate for the registry side.
    postClickHighlightTargets: list[str] | None = None
    question: str | None = None
    options: list[NavigationOption] | None = None
    suggestion: NavigationSuggestion | None = None
    project: ProjectRef | None = None
    # Router state the frontend should pass to navigate(route, {state: ...})
    # alongside `route` — see RouteEntry.route_state.
    routeState: dict | None = None
    # Backend-internal only -- the frontend has no use for this and can
    # ignore it entirely (same as `pending_goal`, never rendered). Set by
    # NavigationAgent._already_here_response when it proactively offers a
    # page's data-bearing feature ("Would you like to know about the
    # balance?"); read by AgentOrchestrator._run_navigation_cascade right
    # after dispatch to open a matching pending goal, so a reply ("yes") is
    # resolved by re-dispatching straight to the right Reporting answer
    # function (date/billing/status) instead of falling through to a
    # generic "I don't understand." See _answer_page_topic_offer.
    topicOffer: dict | None = None
    # Backend-internal only. voice-rewrite's fact-preservation guard (see
    # _number_tokens in llm_client.py) only verifies NUMBERS survive the
    # rewrite unchanged -- there is no equivalent check for page/tab names,
    # so a role-permission-filtered list (e.g. NavigationAgent.
    # _project_information_help_text's tab list) could be silently altered
    # or "helpfully" restored to its unfiltered form by the rewrite model,
    # with nothing to catch it. Rather than build a second verification
    # pass, responses whose exact wording is permission-load-bearing set
    # this to skip the rewrite entirely -- correctness over polish for
    # content the user's role must never see beyond.
    skipVoiceRewrite: bool = False

    @field_validator("route")
    @classmethod
    def route_required_for_navigate(cls, v, info):
        if info.data.get("type") == "navigate" and not v:
            raise ValueError("route is required when type is navigate")
        return v


class HistoryTurn(BaseModel):
    utterance: str
    type: str
    route: str | None = None
    label: str | None = None
    response: dict = {}
    created_at: str | None = None


class HistoryResponse(BaseModel):
    turns: list[HistoryTurn]


class ResetRequest(BaseModel):
    tenantId: str
    userId: str


class ResetResponse(BaseModel):
    status: str = "ok"
