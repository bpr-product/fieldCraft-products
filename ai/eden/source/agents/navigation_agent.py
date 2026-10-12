import difflib
import json
import logging
import os
import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from source.model.navigation_models import (
    NavigationOption,
    NavigationResponse,
    NavigationSuggestion,
    PageFeature,
    ProjectMatch,
    ProjectRef,
    RouteEntry,
    SessionContext,
)
from source.resolvers.entity_resolver import EntityResolver
from source.resolvers.user_role_resolver import UserRoleResolver
from source.utils.llm_client import LLMClient, PRESIDIO_PLACEHOLDER_RE

logger = logging.getLogger(__name__)

REGISTRY_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "contracts", "route_registry.json"
)

# Vector pre-filtering: narrows the registry down to the routes actually worth
# asking the LLM about (see NavigationAgent.top_candidates). A neural-embedding
# version (sentence-transformers) was drafted 2026-07-08 but reverted — it pulls
# in torch, which fails to load on this dev machine (WinError 1455: paging file
# too small). This uses character n-gram TF-IDF instead: no deep-learning
# framework, works on this machine as-is, and handles morphological variation
# ("bills" ~ "billing") reasonably well without a stemmer. Lower semantic
# ceiling than neural embeddings (won't catch a true zero-vocabulary-overlap
# paraphrase), but solves both real problems it needs to: narrowing candidates
# under the model's tiny context window, and catching "nothing matches" via a
# low-similarity threshold instead of trusting the LLM to self-report that.
CANDIDATE_TOP_K = 8
CANDIDATE_SIMILARITY_THRESHOLD = 0.08

# Real bug, fixed: "this"/"that"/"it" live in ENTITY_STOP_WORDS below (never
# part of a project NAME), which made exact_keyword_route treat them as inert
# filler -- "I want to see the invoice for this" fully "explained" every
# token (ENTITY_STOP_WORDS + invoice_list's own vocab) and confidently
# claimed the tenant-wide /invoice list via the fast path, even though "this"
# was actually referring to a specific project (Peter Parker) discussed one
# turn earlier. Unlike a spoken name, a referential pronoun can't be resolved
# by keyword matching at all -- only the LLM understand() path (which
# receives "Recent conversation") can. See exact_keyword_route's use of this
# set for the demotion this enables.
REFERENTIAL_PRONOUNS = {"this", "that", "it"}

# Words that are never part of a project/customer name: navigation verbs,
# route-describing nouns, and conversational filler. Shared by
# _extract_entity_name (what name did the user say?) and
# names_unmatched_entity (did the keyword fast-path just swallow a name it
# can't use?).
ENTITY_STOP_WORDS = {
    "take", "me", "to", "the", "show", "open", "go", "schedule",
    "schedules", "gantt", "timeline", "tasks", "plan", "project",
    "projects", "for", "of", "my", "a", "an", "please", "see",
    "view", "where", "is", "get", "navigate", "find", "what",
    "about", "this", "that", "page", "pages", "list", "in", "on",
    # words that are part of route-describing phrases (e.g.
    # project_information's own intent keywords "project details"/
    # "project info"/"project overview") rather than ever being part of
    # an actual project name — without these, "where can i find the
    # project details" left "details" as the only surviving token and
    # the resolver tried (and failed) to find a project literally named
    # "details" instead of recognizing no project was named.
    "details", "detail", "info", "information", "overview",
    # Same gap, found live for delivery_slip's own intent keywords
    # ("delivery slip", "delivery note", "material delivery", "delivery
    # receipt"): asking "show me delivery slips" with no project named
    # left "delivery slips" as the only surviving tokens, which the
    # resolver then tried (and failed) to match as a literal project name
    # instead of recognizing no project was named and asking which one.
    "delivery", "deliveries", "slip", "slips", "note", "notes",
    "material", "materials", "receipt", "receipts",
    # conversational fillers — never part of a project name
    "okay", "ok", "yes", "no", "hey", "hi", "hello", "can",
    "you", "i", "we", "want", "need", "help", "tell", "give",
    "sure", "thanks", "thank", "now", "then", "just", "lets",
    "let", "us", "and", "so", "also", "well", "know", "check",
    "are", "recent",
    # variance-question words (Insight phrasings: "why is cost over budget
    # on Riverside") — without these, "riverside" alone never survives as
    # the extracted name; the whole question ("why cost over budget
    # riverside") gets treated as the name and matches nothing. These never
    # collide with a real project name in practice, same reasoning as the
    # navigation-verb fillers above.
    "why", "did", "cost", "costs", "over", "budget", "estimate",
    "estimates", "variance", "overrun", "went", "up", "down",
    "actual", "vs",
    # Same gap again, found live for Reporting's own billing vocabulary
    # (mirrors BILLING_SIGNALS in reporting_agent.py): "what is the
    # invoiced amount" with no project named left "invoiced amount" as the
    # only surviving tokens, which extract_entity_name(...,
    # fallback_to_page_context=False) then reported as an explicitly-named
    # project -- silently skipping
    # AgentOrchestrator._maybe_confirm_project_context_switch's "ask before
    # switching" check (it believed the message named something, so there
    # was nothing ambiguous to ask about) and falling straight through to
    # page-context resolution instead.
    "invoice", "invoiced", "invoices", "bill", "billed", "bills",
    "owe", "owes", "owed", "balance", "outstanding", "overdue",
    "paid", "payment", "payments", "received", "amount", "due", "total",
    # Same gap, pre-empted for every OTHER project-scoped tab route added
    # this same session (project_bom, po_details, project_payments,
    # project_documents, project_wip_report, project_vendor_bill) --
    # rather than wait to hit each one live one at a time ("purchase orde"
    # -> po_details's own "purchase"/"order" vocabulary got treated as an
    # attempted project name, same failure mode as delivery/invoice above),
    # their distinctive keywords are added here up front.
    "purchase", "order", "orders", "po", "summary",
    "bom", "takeoff",
    "vendor", "history", "made", "subcontractor",
    "documents", "attachments",
    "wip", "report", "work", "progress",
}

# Several registry routes point at a single-ITEM detail page (one invoice,
# one delivery slip) that needs an id EDEN never has for a general "show me
# this project's X" request -- verified live against each page's own
# useLocation().state read:
#   - project_invoice_view / project_invoice_preview -> InvoiceFormView.tsx /
#     InvoicePreview.tsx both call getInvoiceByProjectIdAndInvoiceId(...),
#     needing an invoiceNo only reachable today by clicking one already-
#     listed invoice row (ProjectList.tsx's per-invoice "Pay Now" button).
#   - delivery_slip -> DeliveryslipDetails.tsx calls
#     getPOAndSlip(projectId, purchaseId, receiptId, ...), needing a
#     purchaseId/receiptId only reachable by clicking one already-listed
#     slip row (DeliverySlip.tsx's own navigate() call).
# Each is aliased at resolution time to project_information's own tab that
# actually lists every item for the project (or the honest empty state) --
# the same page a user reaches by clicking View -> that tab manually.
# Verified against ProjectInformation_v2.tsx's getVisibleTabs (tab keys) and
# CustomTabPanel (a panel is visible only when data === xyz).
ENTITY_TAB_ALIAS_ROUTE_STATE = {
    "project_invoice_view": {"tabName": "simple-tab-7", "selectedTab": 7, "projectView": True},
    "project_invoice_preview": {"tabName": "simple-tab-7", "selectedTab": 7, "projectView": True},
    "delivery_slip": {"tabName": "simple-tab-4", "selectedTab": 4, "projectView": True},
    # job_ledger's OWN standalone route (/jobLedger, rendered with zero
    # props per Routes.tsx) is unconditionally broken -- JobLedgerList.tsx
    # requires props.projectId to fetch anything at all
    # (getJobLedgerList's API call is literally
    # /v2/jobLedgers/getJobLedger/projectId/{projectId} -- there is no
    # tenant-wide variant), so it crashes (`data.map is not a function`)
    # the moment it's reached without one. Verified live. Aliased to the
    # same tab inside project_information, same reasoning as the invoice/
    # delivery-slip entries above.
    "job_ledger": {"tabName": "simple-tab-9", "selectedTab": 9, "projectView": True},
    # project_bom has no dedicated standalone frontend route at all --
    # BOM only exists as project_information's own BOM tab (BOMList.tsx,
    # reads props.projectId the same as every other tab component here).
    # Its `pattern` in route_registry.json is a synthetic id only, never
    # literally navigated to.
    "project_bom": {"tabName": "simple-tab-2", "selectedTab": 2, "projectView": True},
    # po_details is GetPODetails.tsx (/podetails) -- a SINGLE purchase
    # order's detail view needing both projectId AND purchaseId, only
    # reachable in the real app by clicking one already-listed PO row.
    # A general "show me the PO" request has no purchaseId to give it, so
    # it's aliased to project_information's own PO tab (POList.tsx, same
    # props.projectId convention as every other tab component here) --
    # verified live: this exact mismatch is why a PO page reached through
    # EDEN looked like it had "missing components" (it was rendering the
    # wrong -- and broken without a purchaseId -- page).
    "po_details": {"tabName": "simple-tab-3", "selectedTab": 3, "projectView": True},
    # bill_form is BillForm.tsx (/bill) -- needs a WHOLE bill row object
    # (location.state.row) EDEN can never construct, not just an id. The
    # frontend's own comment on this exact page confirms the failure mode:
    # "location.state is null when the page is opened directly (EDEN chat
    # navigation, deep link, refresh) ... the page crashes on mount and
    # renders with sections missing." Aliased to project_information's own
    # bill-listing tab, which lists every bill for the project instead.
    #
    # Real bug, fixed: this was selectedTab=5 -- confirmed against
    # ProjectInformation.tsx's actual <Tabs> render order (frontend repo,
    # src/web/fieldcraft-tenant-web/src/pages/ProjectInformation.tsx ~line
    # 1246) that tab id 5 doesn't exist at all in the projectView=true
    # branch this page renders in -- MUI's CustomTabPanel silently shows
    # nothing for a `value` with no matching panel. There is no separate
    # standalone "Bill" tab anymore; it's been consolidated into "Bills &
    # Payables" (id 14, a11yProps-assigned once and never renumbered, NOT
    # a positional index) -- the same tab project_vendor_bill below already
    # correctly points to. bill_form's mapping was never updated when that
    # consolidation happened.
    "bill_form": {"tabName": "simple-tab-14", "selectedTab": 14, "projectView": True},
    # invoice_list (/invoice) USED to be recorded here as NOT aliasable --
    # a standalone, tenant-wide LIST page with no project/item id involved
    # at all, so there was no "which project's tab" to redirect to, unlike
    # bill_form/job_ledger above. Confirmed live (three separate times: via
    # direct "take me to the invoice," via "back to the previous page"
    # replaying a remembered route, and via the semantic-retrieval/LLM
    # navigation tier surfacing it as a candidate) that the page itself
    # (changeStatus/fetchRows in bundle.js) throws "undefined is not
    # iterable" on mount, because it assumes some status-filter array is
    # always seeded via location.state -- true only when reached by
    # clicking the real in-app sidebar link, never true for EDEN's direct
    # navigate(). A per-tier redirect-away fix (tried first) only covered
    # the exact-keyword tier and left the retrieval/LLM tier still able to
    # reach the crash -- removing invoice_list from route_registry.json
    # entirely closes every tier at once.
    #
    # Real bug, fixed (PR review, Raja): this comment originally claimed
    # project_invoice_view (entity_required="project") "absorbed" invoice_
    # list's bare invoice/billing keywords -- confirmed FALSE at the time:
    # project_invoice_view's own intent_keywords in route_registry.json
    # still only had its 3 original project-qualified phrases ("project
    # invoice," "invoice for this project," "project billing"), so a bare
    # "take me to invoices" / "show me invoices" with no project context
    # scored 0 on the deterministic keyword tier and fell all the way
    # through to the semantic-retrieval/LLM tier instead -- an undisclosed
    # regression this comment papered over rather than describing. Fixed by
    # actually adding invoice_list's bare invoice/billing-domain keywords
    # ("invoice," "invoices," "billing," "accounts receivable," "ar,"
    # "unpaid," "outstanding invoices," "payment request," "client
    # invoice," "raise invoice," "send invoice") to project_invoice_view's
    # intent_keywords -- now a no-project-context "show me invoices"
    # resolves deterministically through THAT route's existing ask-which-
    # project chain, as this comment always claimed. Deliberately did NOT
    # carry over invoice_list's old bare "bill"/"bills" keywords -- those
    # now belong to bill_form/project_vendor_bill, which didn't exist as
    # separate project-scoped routes when invoice_list was one combined
    # page; adding them here would misroute a bare "bills" query to the
    # Invoice tab instead of the Bill tab.
    # project_payments/_documents/_wip_report/_vendor_bill have no
    # dedicated standalone frontend route at all -- each only exists as
    # project_information's own tab (ReceiveBillPaymentList/DocumentList/
    # WipReport/VendorBill, all reading props.projectId the same as every
    # other tab component here). Their `pattern`s in route_registry.json
    # are synthetic ids only, never literally navigated to.
    "project_payments": {"tabName": "simple-tab-8", "selectedTab": 8, "projectView": True},
    "project_documents": {"tabName": "simple-tab-10", "selectedTab": 10, "projectView": True},
    "project_wip_report": {"tabName": "simple-tab-11", "selectedTab": 11, "projectView": True},
    "project_vendor_bill": {"tabName": "simple-tab-14", "selectedTab": 14, "projectView": True},
}

# Reverse lookup: selectedTab -> the route id whose OWN permission actually
# governs that tab. Real gap, fixed: every one of the 10 aliased ids above
# resolves (via _entity_route) to project_information's bare URL, so a
# permission check keyed on the URL string ALONE (is_route_pattern_permitted,
# verify_remembered_suggestion_permission) can only ever recover
# project_information's own PRO.REA -- structurally blind to which tab is
# actually being requested, even though each tab has a genuinely different
# real permission (INV.REA, BOM.REA, PO.REA, JBL.REA, etc.). Two ids share
# tab 7 (project_invoice_view/project_invoice_preview); both require the
# identical INV.REA, so last-one-wins here is harmless for permission
# purposes specifically.
_TAB_TO_ROUTE_ID = {v["selectedTab"]: k for k, v in ENTITY_TAB_ALIAS_ROUTE_STATE.items()}

# Semantic retrieval (RETRIEVAL_BACKEND=semantic): real sentence embeddings,
# computed in-process via FastEmbed (all-MiniLM-L6-v2 on ONNX Runtime — see
# embeddings_client.py's docstring). Cosine similarity on true sentence
# embeddings sits in a much higher range than TF-IDF's character-overlap
# scores for related short phrases, so it needs its own threshold rather
# than reusing CANDIDATE_SIMILARITY_THRESHOLD.
CANDIDATE_SIMILARITY_THRESHOLD_SEMANTIC = 0.3

# DEGRADED FALLBACK ONLY: maps data-question topics to the page(s) where that
# data actually lives, so a question EDEN can't answer yet ("what's the
# margin on Henderson?") becomes a helpful "here's where you'll find it" with
# navigation bubbles. Ordered by specificity — the first matching row wins
# its route ids. Per explicit user direction, LLMClient.suggest_related_routes
# is now the PRIMARY mechanism (see data_question_response) — it reads the
# actual question against the real candidate descriptions instead of a fixed
# phrase table. This table survives only for when that call fails (model
# unreachable), same pattern as VARIANCE_SIGNALS/ENTITY_STOP_WORDS.
DATA_TOPIC_ROUTES: list[tuple[tuple[str, ...], list[str]]] = [
    (("job cost", "job costs", "cost code", "variance", "actual vs", "expenses by job"),
     ["job_ledger"]),
    (("margin", "profit", "p&l", "p and l", "revenue", "income", "financials",
      "financial", "accounting", "earnings"), ["tenant_finance", "job_ledger"]),
    # invoice_list ("/invoice") was removed from route_registry.json --
    # confirmed-broken frontend page, crashes on EDEN's direct navigate()
    # regardless of which tier reached it (see the comment on
    # ENTITY_TAB_ALIAS_ROUTE_STATE's invoice_list entry above for the full
    # writeup, including the PR-review-confirmed regression window and its
    # fix). project_invoice_view now genuinely carries the bare invoice/
    # billing keywords in route_registry.json and has its own working
    # ask-which-project chain -- this DATA_TOPIC_ROUTES table is only the
    # degraded LLM-unreachable fallback, not the primary path for these
    # words anymore.
    (("invoice", "invoices", "unpaid", "receivable", "outstanding", "overdue",
      "invoice amount", "invoice total", "billed", "billing"),
     ["project_invoice_view", "customer_info_dashboard"]),
    (("customer balance", "customer account", "customer payment", "amount due",
      "account balance", "balance"), ["customer_invoices", "customer_info_dashboard"]),
    (("payment record", "past payments", "payments made", "payment log"),
     ["customer_info_dashboard"]),
    (("how many customers", "customer count", "number of customers",
      "customers we have", "our customers", "customer list"), ["customer_table"]),
    (("how many vendors", "vendor count", "number of vendors",
      "vendors we have", "our vendors"), ["vendor_list"]),
    (("how many employees", "employee count", "number of employees",
      "employees we have", "our employees", "staff count"), ["employee_list"]),
    (("stock", "inventory", "on hand", "on-hand", "available stock", "quantity",
      "quantities"), ["inventory"]),
    # estimate_view ("/estimateview") was removed from route_registry.json --
    # confirmed-broken frontend page, crashes on EDEN's direct navigate()
    # (same class as general_contract_template/change_order_contract/
    # bill_form: a single-item page reading location.state unconditionally).
    # sales_estimate absorbed its bare estimate/quote keywords, and once the
    # Sales/CRM tab consolidation retired /salesestimate, customers_projects
    # (whose Estimate stage tab lists the same projects) took them over.
    (("estimate", "bid amount", "quote", "quoted"), ["customers_projects"]),
    (("deadline", "due date", "when is", "when will", "milestone", "finish",
      "completion", "timeline"), ["gantt_charts"]),
    (("cost", "costs", "price", "pricing", "total", "how much", "spend", "spent"),
     ["job_ledger", "tenant_finance"]),
]

# _ground_insight()'s claim-detector -- a free-text insight only needs
# grounding-checked when it actually ASSERTS a capability ("you can X",
# "lets you X"), not when it's a plain description sentence with no such
# claim shape at all. Deliberately a small, closed connector vocabulary,
# same reasoning as reporting_agent.py's _CAUSAL_PHRASE_RE.
_INSIGHT_CLAIM_RE = re.compile(
    r"\byou can\b|\ballows? you to\b|\blets? you\b|\bable to\b|"
    r"\bwhere you can\b|\bhere you can\b|\byou'?re able to\b",
    re.IGNORECASE,
)

# AB#28717: "deactivate a customer" has no real destination -- checked the
# actual frontend (Customer.tsx, the /admin/customer edit form): its
# `status` field is hardcoded to "Active" both on initial state AND
# unconditionally right before every save, with no dropdown/toggle exposed
# anywhere in the UI to set it to anything else. customer_table's row
# actions are only "View Asset" and "Edit customer" -- no deactivate
# control there either. This isn't a missing route or a fabricated
# permission (like create_asset/AB#28704) -- deactivating a customer
# genuinely is not a self-service action anywhere in the app today, for
# any role. Checked ahead of routing (not a _ground_insight-style claim
# check) because the LLM can classify this onto more than one route
# (admin_customer, customer_table) depending on phrasing, and every one of
# them would be equally wrong to navigate to for this specific ask.
_CUSTOMER_DEACTIVATION_RE = re.compile(
    r"\bdeactivat\w*\b.{0,20}\bcustomer\b|\bcustomer\b.{0,20}\bdeactivat\w*\b",
    re.IGNORECASE,
)


def _grounded_page_text(route: "RouteEntry") -> str:
    """Every real, registered fact about what this page can do -- the ONLY
    text a free-text insight claim is allowed to be grounded in. Mirrors
    reporting_agent.py's _grounded_reason_strings: the actual (already
    scoped) source of truth, not the raw LLM output."""
    parts = [route.description, route.page_help]
    for feature in route.page_features:
        parts.append(feature.name)
        parts.append(feature.hint)
        parts.extend(feature.keywords)
        parts.extend(feature.locate)
    return " ".join(p for p in parts if p).lower()


def _ground_insight(insight: str | None, route: "RouteEntry") -> str | None:
    """Reject a free-text LLM insight that asserts a specific capability
    this route's own registered data never mentions -- returns the insight
    unchanged if it's safe (no claim shape, or the claim is grounded), None
    otherwise (caller falls back to route.description, the safe default).

    Real bug, fixed: confirmed live -- resolve_classified() passes the LLM's
    raw NavigationUnderstanding.insight straight into the user-facing
    navigate message with zero verification, the same "instruction alone
    isn't a guarantee" gap reporting_agent.py's RAG guards already closed
    for cost/billing answers, just never applied to navigation. Two
    independent live incidents: "you can deactivate a customer account from
    the Customer Admin page" (admin_customer's page_help/page_features
    -- profile photo, billing address, property link, save/update button --
    say NOTHING about deactivation) and a claim that leave can be booked
    from Timesheets (whose own page_help/keywords are strictly about time
    logs/hours/attendance, nothing about leave). Both are confident,
    specific, and entirely unsupported by the route's own registered data.

    Deliberately narrow: only flags insight text that actually CLAIMS a
    capability (see _INSIGHT_CLAIM_RE) -- a plain descriptive sentence with
    no such claim shape is never touched by this check, so a normal insight
    like "Administer customer accounts" (route.description itself) always
    passes through unchanged."""
    if not insight:
        return insight
    match = _INSIGHT_CLAIM_RE.search(insight)
    if match is None:
        return insight
    claim_segment = insight[match.end():]
    claim_segment = re.split(r"[.!?]", claim_segment, maxsplit=1)[0]
    # Same "only meaningful words" convention as reporting_agent.py's
    # _fuzzy_topic_word (len < 6 skipped) -- short words (the, from, page,
    # here, your...) are too generic to prove or disprove grounding either
    # way; only substantive words carry a real claim.
    claim_words = [w for w in re.findall(r"[a-zA-Z]+", claim_segment) if len(w) >= 6]
    if not claim_words:
        return insight
    grounded_text = _grounded_page_text(route)
    # ALL significant claim words must be grounded, not just one -- an
    # earlier version of this check used any(), which let the exact live
    # 28717 incident through: "deactivate a customer account" trivially
    # overlaps grounded_text on "customer"/"account" alone (route.
    # description literally says "Administer customer accounts"), even
    # though the actual load-bearing word -- "deactivate" -- appears
    # nowhere. any() proves nothing when a claim reuses the page's own
    # generic domain vocabulary; all() forces the SPECIFIC action word to
    # be grounded too.
    if all(w.lower() in grounded_text for w in claim_words):
        return insight
    logger.warning(
        "Navigation insight rejected -- claims a capability route %r's own "
        "page_help/page_features never mention: insight=%r",
        route.id, insight,
    )
    return None


def _example_label(route: "RouteEntry") -> str:
    """route.label_template, trimmed for use inside "Try: 'X's <label>'" --
    real bug, fixed: confirmed live -- _resolve_with_entity's "which project
    did you mean" fallback used ONE hardcoded example ("Henderson
    schedule") for every project-scoped route regardless of what was
    actually asked, so a delivery-slip request got prompted with an
    unrelated schedule example (looked like EDEN had switched topics
    entirely). Every project-scoped route's own label_template is already
    available here and makes the example topic-relevant.

    Most of those labels start with a redundant "Project " prefix
    (route_registry.json's own naming, e.g. "Project Payments", "Project
    Purchase Orders") that reads awkwardly stacked after "Henderson's" --
    "Henderson's Project Payments" vs. the cleaner "Henderson's Payments".
    Stripped here for the example text only; the full label_template is
    still used everywhere else (navigate confirmations, disambiguation
    labels) where "Project Payments" alone, without a possessive prefix, is
    the clearer form.

    project_information is the one deliberate exception: stripping its
    "Project " prefix leaves just "Information", too vague to serve as a
    useful example ("Henderson's Information") -- kept whole instead."""
    label = route.label_template
    if route.id != "project_information" and label.startswith("Project "):
        return label[len("Project "):]
    return label


# The global_features entry (route_registry.json) that "edit bid"/"delete
# bid"/"remove bid"/"cancel bid" keyword-match onto by default. Kept as a
# name, not an id -- PageFeature has no id field, `name` is how every other
# matcher in this file already identifies a specific feature.
_CART_DRAFTS_FEATURE_NAME = "Cart & Drafts"

# Real bug, fixed: try_feature_lookup_scored's cross-page branch used to
# treat EVERY match the same way regardless of phrasing -- a pure locational
# question ("where's the edit vendor button", "where can i send an invoice")
# and a genuine action request ("I need to add a new material", "add a new
# material") both got the identical "that's on the X page, here's what
# you'll find" description + clickable suggestion, never an actual navigate.
# Confirmed live: asking to DO something ("i need to add a new material")
# got told about the button instead of being taken to it. A "where"/"how"
# lead-in is someone asking to locate something, not asking to be taken
# there right now -- keep the describe-and-suggest behavior for those
# (test_feature_lookup_on_different_page_redirects_with_suggestion's "where
# can i send an invoice" depends on this staying a fallback, not a navigate).
# Everything else that matched a feature's keywords is treated as the user
# asking to DO the thing -- navigate there with the highlight already
# attached, instead of making them ask twice or click a suggestion.
_INFORMATIONAL_LOOKUP_RE = re.compile(r"^\s*(where|how)\b", re.IGNORECASE)


class NavigationAgent:
    """Executes navigation the orchestrator has triaged: matches routes,
    enforces RBAC, resolves project entities, and builds the response."""

    def __init__(self):
        self.entity_resolver = EntityResolver()
        # Real bug, fixed 2026-07-20: page-level permission was being checked
        # against a Postgres-backed resolver (rbac_resolver.py) -- a
        # completely separate system from the Mongo gold_canonical.user_permissions
        # this service's auth layer already resolves roles/permissions from
        # (see source/utils/auth.py). Every permission decision for navigation
        # must come from that same Mongo source of truth, not a second,
        # independent query path that can drift from it.
        self.role_resolver = UserRoleResolver()
        self.llm_client = LLMClient()
        registry_data = self._load_registry_file()
        self.route_registry: list[RouteEntry] = [
            RouteEntry(**entry) for entry in registry_data["routes"]
        ]
        # Features that live outside any single route — e.g. the cart icons
        # and the GoPAL/Completed-Task-Feed sidebar, both rendered globally
        # in MainLayout.tsx on every page, not scoped to one route's
        # page_features. Checked first in try_feature_lookup, before the
        # current-page/cross-page route search, since "which page is this
        # on" doesn't apply to something available everywhere.
        self.global_features: list[PageFeature] = [
            PageFeature(**f) for f in registry_data.get("global_features", [])
        ]
        # Computed once at startup (same lifecycle as loading the registry
        # JSON) — fitting + transforming ~55 short strings is well under a
        # second. No persistence and no vector DB: recomputing on every
        # restart is cheap enough that caching it would be pure complexity
        # for nothing at this scale (see top_candidates for the query side).
        self._route_vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5))
        self._route_vectors = self._route_vectorizer.fit_transform(
            [f"{r.label_template}. {r.description}" for r in self.route_registry]
        )

        # Semantic retrieval, feature-flagged: default stays "tfidf" until
        # verified against the regression suite — same discipline used for
        # the LLM backend swaps in llm_client.py. Falls back to tfidf on any
        # init failure (service unreachable, etc.) rather than blocking
        # startup.
        self.retrieval_backend = os.getenv("RETRIEVAL_BACKEND", "tfidf").strip().lower()
        self._embeddings_client = None
        self._route_embeddings = None
        self._vector_store = None
        if self.retrieval_backend == "semantic":
            try:
                from source.utils.embeddings_client import EmbeddingsClient

                client = EmbeddingsClient()
                if not client.is_reachable():
                    raise RuntimeError("embeddings service not reachable")
                texts = [f"{r.label_template}. {r.description}" for r in self.route_registry]
                self._route_embeddings = client.embed(texts)
                self._embeddings_client = client
                logger.info("Retrieval backend: semantic (all-MiniLM-L6-v2)")
            except Exception:
                logger.exception("Semantic retrieval init failed — falling back to tfidf")
                self.retrieval_backend = "tfidf"
        elif self.retrieval_backend == "qdrant":
            # Same all-MiniLM-L6-v2 embeddings as "semantic", but persisted in
            # Qdrant (eden-qdrant, docker-compose.local.yml) instead of an
            # in-process array — the vectors survive process restarts and
            # this is the seam for indexing something bigger than the route
            # registry later (projects/documents for Finance/Schedule
            # agents), where re-embedding everything at every start stops
            # being free. At today's ~55 routes the in-memory "semantic"
            # backend is still faster (no network round-trip); this exists
            # for when that stops being true.
            try:
                from source.utils.embeddings_client import EmbeddingsClient
                from source.utils.qdrant_client import QdrantVectorStore

                embeddings_client = EmbeddingsClient()
                if not embeddings_client.is_reachable():
                    raise RuntimeError("embeddings service not reachable")
                store = QdrantVectorStore()
                if not store.is_reachable():
                    raise RuntimeError("qdrant not reachable")
                texts = [f"{r.label_template}. {r.description}" for r in self.route_registry]
                vectors = embeddings_client.embed(texts)
                store.index_routes([r.id for r in self.route_registry], vectors)
                self._embeddings_client = embeddings_client
                self._vector_store = store
                logger.info("Retrieval backend: qdrant (all-MiniLM-L6-v2, persisted)")
            except Exception:
                logger.exception("Qdrant retrieval init failed — falling back to tfidf")
                self.retrieval_backend = "tfidf"

    def _load_registry_file(self) -> dict:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    @property
    def candidate_threshold(self) -> float:
        """Similarity threshold matching whichever retrieval backend is
        active — semantic embeddings and TF-IDF char n-grams sit on
        different score scales, so the caller (agent_orchestrator's vector
        pre-filter gate) asks the agent rather than hardcoding one."""
        if self.retrieval_backend in ("semantic", "qdrant"):
            return CANDIDATE_SIMILARITY_THRESHOLD_SEMANTIC
        return CANDIDATE_SIMILARITY_THRESHOLD

    def top_candidates(
        self, utterance: str, k: int = CANDIDATE_TOP_K
    ) -> list[tuple[RouteEntry, float]]:
        """Return the k routes most similar to the utterance, each paired
        with its score. Semantic backend (real sentence embeddings) catches
        true paraphrases with zero vocabulary overlap; TF-IDF (default,
        always available) catches morphological/typo variation via shared
        character n-grams. Recomputed fresh per call — 55ish routes is
        negligible; no index needed at this scale, this linear scan IS the
        fast path here."""
        if self.retrieval_backend == "qdrant" and self._vector_store is not None:
            try:
                query_vec = self._embeddings_client.embed([utterance])[0]
                hits = self._vector_store.search(query_vec, k)
                by_id = {r.id: r for r in self.route_registry}
                return [(by_id[rid], score) for rid, score in hits if rid in by_id]
            except Exception:
                logger.exception("Qdrant retrieval call failed, using tfidf for this request")

        if self.retrieval_backend == "semantic" and self._embeddings_client is not None:
            try:
                query_vec = self._embeddings_client.embed([utterance])
                scores = cosine_similarity(query_vec, self._route_embeddings)[0]
                top_idx = scores.argsort()[::-1][:k]
                return [(self.route_registry[i], float(scores[i])) for i in top_idx]
            except Exception:
                logger.exception("Semantic retrieval call failed, using tfidf for this request")

        query_vec = self._route_vectorizer.transform([utterance])
        scores = cosine_similarity(query_vec, self._route_vectors)[0]
        top_idx = scores.argsort()[::-1][:k]
        return [(self.route_registry[i], float(scores[i])) for i in top_idx]

    # ── entry points (called by the orchestrator) ──────────────────────────

    def try_keyword_navigation(
        self, context: SessionContext
    ) -> NavigationResponse | None:
        """Deterministic fast path. Returns None when no keyword matches,
        signalling the orchestrator to engage the LLM."""
        route, _ = self._keyword_match(context.utterance.lower(), context)
        if not route:
            return None
        return self._finalize(route, context, confidence=0.95)

    def exact_keyword_route(
        self, utterance: str, context: SessionContext | None = None
    ) -> RouteEntry | None:
        """High-precision deterministic tier (LLM-first architecture): the
        fast path only claims an utterance it can FULLY explain — every
        token must be either conversational filler or part of the matched
        route's own keyword vocabulary. Anything with unexplained content
        ("payment details of peter parker" — who is peter parker?) falls
        through to the RAG->LLM path, whose entity extraction and lookup
        tools can actually use the parts a substring match would silently
        drop. This replaces the old fuzzy-claims-everything behavior that
        needed a keyword-list patch every time a new phrasing collided.

        Routes with entity_required keep the fuzzy claim: their fast path
        (_finalize) already extracts and resolves the spoken name
        deterministically — that tier isn't broken, so it isn't demoted.

        `context`: lets _keyword_match prefer a project-scoped route over a
        same/lower-scoring tenant-wide one when the user is currently on a
        project's page (see _keyword_match's own comment) -- real gap,
        fixed: asking "material" while looking at a project landed on the
        tenant-wide Material Management admin catalog instead of that
        project's own BOM tab, because both share the word "material" and
        the flat admin route happened to score no lower."""
        route, score = self._keyword_match(utterance, context)
        if route is None:
            return None
        if route.entity_required:
            # Real bug, fixed: confirmed live -- "show me the delivery slips"
            # / "stock delivery slips" claimed delivery_slip (project-scoped)
            # outright on its bare "delivery slip" keyword, asking "which
            # project?" even when the utterance was plainly about Inventory's
            # tenant-wide Stock Delivery Slips sub-tab, not any one project's
            # single delivery slip document. entity_required's fuzzy claim
            # exists so project-scoped routes aren't demoted by the "fully
            # explained" vocab check above -- it was never meant to also
            # outrank a MORE SPECIFIC page_feature match elsewhere with no
            # comparison at all. Only defer when the feature match strictly
            # beats this route's own score (a tie keeps today's existing,
            # tested behavior for every other entity-required route unless a
            # feature genuinely scores higher for this exact utterance).
            if context is not None:
                feature_response, feature_score = self.try_feature_lookup_scored(context)
                if feature_response is not None and feature_score > score:
                    return None
            return route

        # Real bug, fixed: a referential pronoun ("this"/"that"/"it") only
        # LOOKS like inert filler to this deterministic tier -- it's actually
        # standing in for a specific project the user may have just been
        # discussing (e.g. asked about "Peter Parker", then said "I want to
        # see the invoice for this"). If a project-scoped sibling route
        # shares this route's own keyword vocabulary (project_invoice_view
        # vs. invoice_list both keyed on "invoice"), demote to the LLM
        # understand() path instead of confidently claiming the tenant-wide
        # page -- only that path receives "Recent conversation" and can
        # actually resolve what the pronoun refers to.
        utterance_tokens = self._tokenize(utterance)
        if any(t in REFERENTIAL_PRONOUNS for t in utterance_tokens):
            route_words: set[str] = set()
            for kw in route.intent_keywords:
                route_words.update(kw.split())
            for sibling in self.route_registry:
                if sibling.id == route.id or not sibling.entity_required:
                    continue
                sibling_words: set[str] = set()
                for kw in sibling.intent_keywords:
                    sibling_words.update(kw.split())
                if route_words & sibling_words:
                    return None

        vocab: set[str] = set()
        for kw in route.intent_keywords:
            vocab.update(kw.split())
        vocab.update(route.label_template.lower().split())
        # Real bug, fixed: ENTITY_STOP_WORDS exists to stop billing/PO/
        # delivery-domain words ("bill", "invoice", "vendor", "receipt",
        # "task", "history"...) from being misread as a PROJECT NAME -- it
        # was never meant to certify them as meaningless FILLER for this
        # unrelated vocab check too, but _word_matches doesn't know the
        # difference. That let "bill upload"/"upload a bill" get claimed by
        # document_management: "uploads" is genuinely in its vocab, and
        # "bill" -- the one word that actually matters -- got waved through
        # as inert stopword filler instead of being left unexplained, so
        # the global "Upload Bill" Topbar action (try_feature_lookup, the
        # very next tier) never got a chance to win.
        #
        # A stopword only counts as explained here if it isn't itself
        # load-bearing vocabulary for a global feature whose OWN keyword
        # phrase fully matches this utterance -- not just any word pulled
        # from any global-feature keyword. A naive whole-phrase word-set
        # (first attempt at this fix) incidentally contained filler like
        # "a" (from "upload a bill") and "view" (from "view cart"), which
        # then falsely disqualified "a"/"view" as filler in ANY unrelated
        # utterance ("upload a document" got wrongly kicked out too, since
        # "a" no longer counted as explained). Requiring the WHOLE phrase to
        # match first (same all-words-present rule _match_feature/
        # _keyword_match already use) keeps this scoped to utterances that
        # actually are the global action, not anything sharing one filler
        # word with one of its phrasings.
        global_match_words: set[str] = set()
        for feat in self.global_features:
            for kw in feat.keywords:
                kw_words = kw.split()
                if all(self._word_matches(w, utterance_tokens) for w in kw_words):
                    global_match_words.update(kw_words)
        # Real bug, fixed: confirmed live -- the SAME collision as
        # global_features above, but for this route's OWN page_features.
        # "list the bills in inventory" is fully explained by inventory's
        # bare vocab ("inventory") plus "bill"/"bills" waved through as
        # ENTITY_STOP_WORDS filler (it's there to stop billing words from
        # being misread as a PROJECT NAME, not to certify them meaningless
        # here) -- so this tier claimed the utterance and returned a bare
        # /inventory navigate, before try_feature_lookup (the very next
        # tier) ever got a chance to match the route's own, far more
        # specific "Stock Bills tab" page_feature and switch to that
        # sub-tab instead of leaving the user on whichever one was already
        # showing. Same all-words-present carve-out as global_match_words.
        page_match_words: set[str] = set()
        for feat in route.page_features:
            for kw in feat.keywords:
                kw_words = kw.split()
                if all(self._word_matches(w, utterance_tokens) for w in kw_words):
                    page_match_words.update(kw_words)
        # _tokenize, not .split(): "open invoices!" must not leave a
        # punctuation-glued token the vocabulary can't explain — that demoted
        # clean commands to the LLM path for a stray "!" or "?".
        for w in self._tokenize(utterance):
            stopword_hit = self._word_matches(w, list(ENTITY_STOP_WORDS))
            vocab_hit = self._word_matches(w, list(vocab))
            explained_by_stopword_only = stopword_hit and not vocab_hit
            if explained_by_stopword_only and (
                self._word_matches(w, list(global_match_words))
                or self._word_matches(w, list(page_match_words))
            ):
                stopword_hit = False
            if not (stopword_hit or vocab_hit):
                # Real bug, fixed: "show me the material list for Peter
                # Parker" tied on raw keyword score between project_bom
                # (entity_required) and material_management (tenant-wide,
                # same "material list" keyword) with no page context to
                # break the tie via the bonus above -- material_management
                # won by registry order, then correctly rejected itself
                # here (can't explain "peter"/"parker"), but instead of
                # falling through to the LLM path, control fell to
                # FEATURE lookup next, which has no such vocabulary check
                # and misfired onto an unrelated global "Materials tab"
                # admin feature (observed live). The unexplained leftover
                # IS the named project this non-entity route can't use --
                # retry excluding it; if the next-best candidate is
                # entity-required, its own fuzzy claim (this function's
                # very next branch, unreached this time) can actually use
                # that name, so defer to it instead of abandoning to
                # feature-lookup's blind spot.
                #
                # Real bug, fixed: confirmed live -- that "unexplained
                # leftover IS a project name" assumption is wrong for a
                # TYPO, and the cost is a hard dead-end rather than a
                # graceful miss. "shoe me the purchase order" (a slip for
                # "show") left "shoe" unexplained, so purchase_orders (the
                # tenant-wide list, entity_required=None, the right answer)
                # rejected itself and this branch handed the turn to
                # po_details -- project-scoped -- which then had no project
                # to resolve and dead-ended in a routeless fallback. The
                # user reasonably asked why the LLM/RAG didn't just
                # understand the typo: it never ran, because this
                # deterministic tier answered first.
                # "shoe"/"show" scores 0.75, under _word_matches' strict
                # 0.82 bar (correctly -- that bar guards positive keyword
                # matching), so a looser, separate check is used purely to
                # decide whether the leftover is name-shaped at all. When
                # it looks like a misspelt filler word, fall through to the
                # LLM/RAG path, which handles typos semantically. A false
                # "typo" verdict on a real name is harmless -- the LLM path
                # extracts and resolves names perfectly well; the reverse
                # error is the one that dead-ends.
                if not self._is_probable_typo_of_filler(w, vocab):
                    alt_route, _alt_score = self._keyword_match(utterance, context, exclude_id=route.id)
                    if alt_route is not None and alt_route.entity_required:
                        return alt_route
                return None  # unexplained token — the LLM path decides
        return route

    def keyword_match_scored(
        self, utterance: str, context: SessionContext | None = None
    ) -> tuple[RouteEntry | None, int]:
        """Public accessor so the orchestrator can compare this score against
        try_feature_lookup_scored's without re-running the search itself."""
        return self._keyword_match(utterance.lower(), context)

    def route_by_pattern(self, pattern: str | None) -> RouteEntry | None:
        """Public accessor for the orchestrator — resolves a remembered route
        pattern from conversation history back to its registry entry (used by
        affirmation follow-ups and history-candidate injection)."""
        return self._route_by_pattern(pattern)

    def route_by_id(self, route_id: str | None) -> RouteEntry | None:
        """Public accessor for the orchestrator — resolves a route id (e.g.
        a disambiguation candidate) back to its registry entry, so a caller
        can inspect its page_features (see _disambiguation_response's
        shared-feature check) without reaching into the private lookup."""
        return self._route_by_id(route_id)

    def route_option(
        self, route_id: str | None, context: SessionContext | None = None
    ) -> NavigationOption | None:
        """Public accessor for the orchestrator — builds a clickable
        NavigationOption card for a route id (used by route-level
        disambiguation when the LLM's navigate confidence is low).

        `context` is optional and, when given, lets an entity-required
        candidate (e.g. project_invoice_view) resolve to the actual named
        project ("go to buma invoice page" -> Buma's invoice view) instead
        of unconditionally collapsing to the bare Projects list — see
        _resolve_entity_option."""
        return self._route_option(route_id, context)

    def resolve_project_route_option(
        self, route_id: str, tenant_id: str,
        entity_name: str | None = None, entity_id: str | None = None,
        context: SessionContext | None = None,
    ) -> NavigationOption | None:
        """Public accessor for the orchestrator — builds a project-scoped
        NavigationOption from a project ALREADY resolved by the caller
        (Reporting/Insight's own answer), rather than re-parsing the
        utterance (see _resolve_entity_option, which does the latter for
        the disambiguation path). Real bug this fixes: Reporting's own
        "would you like to see the Invoices page?" follow-up (see
        AgentOrchestrator._attach_page_suggestion) pointed at the flat,
        all-projects invoice_list page even when a specific project had
        just been discussed and answered about — landing the user on a
        page with none of the project context (header, tabs) they were
        just looking at. Returns None (caller falls back to its flat
        default) whenever the route isn't project-entity-required, no
        name/id was given, or it resolves to zero/multiple projects.

        Real bug, fixed: this NEVER checked permission at all -- confirmed
        live, a customer (INV.PAY only, no INV.REA) asking a billing
        question got offered THIS route's aliased project_invoice_view tab
        (needs INV.REA) as the follow-up suggestion and landed on it after
        confirming, a real cross-role data leak. The AgentOrchestrator's
        universal _enforce_role_scope backstop would strip an unpermitted
        suggestion at the very end regardless, but that's defense in depth,
        not a reason to leave the SOURCE of the leak unpermissioned --
        denied here means the caller falls back to its flat, generically-
        permissioned default instead, same "no-op on denial" contract as
        every other branch in this function. `context` is optional (a
        missing context skips the check, same permissive default as before
        this fix, for any caller that genuinely has no session to check
        against) -- every real caller has one and must pass it."""
        route = self._route_by_id(route_id)
        if route is None or route.entity_required != "project":
            return None
        if entity_id:
            matches = self.entity_resolver.resolve_by_id(entity_id, tenant_id)
        elif entity_name:
            matches = self.entity_resolver.resolve_project(entity_name, tenant_id)
        else:
            return None
        if len(matches) != 1:
            return None
        if context is not None and not self._has_permission(route, context):
            return None
        match = matches[0]
        return NavigationOption(
            label=f"{match.project_name} — {route.label_template}",
            route=self._entity_route(route, match),
            icon=route.icon,
            description=route.description,
            routeState=self._entity_route_state(route, match),
        )

    def resolve_entity_route_for_project(
        self, route_id: str, context: SessionContext, entity_id: str,
    ) -> NavigationResponse | None:
        """Public accessor for the orchestrator — redoes an entity-required
        route's resolution against an EXACT project id, bypassing name
        extraction entirely. Used by
        AgentOrchestrator._answer_explicit_project_switch when the user
        picks "the project on my current page" over an explicitly named one
        that differed from it (see
        AgentOrchestrator._maybe_confirm_explicit_project_switch) -- same
        route, different project, no re-parsing of the (now-stale) original
        utterance. Returns None when route_id isn't a real, entity-required
        route (caller falls back to a safe default)."""
        route = self._route_by_id(route_id)
        if route is None or not route.entity_required:
            return None
        return self._resolve_with_entity(route, context, None, entity_id=entity_id)

    def clamp_confidence(self, value) -> float:
        """Public accessor for the orchestrator — same clamping the agent
        uses internally, so both sides agree on what a valid confidence
        looks like."""
        return self._clamp_confidence(value)

    def finalize_keyword_route(
        self, route: RouteEntry, context: SessionContext,
        last_discussed_project: str | None = None,
    ) -> NavigationResponse:
        """Build the response for a route the orchestrator already selected
        via keyword_match_scored — same confidence try_keyword_navigation
        has always used for a keyword hit."""
        return self._finalize(
            route, context, confidence=0.95,
            last_discussed_project=last_discussed_project,
        )

    def resolve_classified(
        self, context: SessionContext, understanding: dict,
        last_discussed_project: str | None = None,
    ) -> NavigationResponse:
        """Execute a navigation the orchestrator's LLM already classified."""
        if _CUSTOMER_DEACTIVATION_RE.search(context.utterance):
            return NavigationResponse(
                type="fallback",
                message=(
                    "Sorry, I'm not able to do that — deactivating a "
                    "customer isn't something i can perform."
                ),
            )
        route_id = understanding.get("route_id")
        route = next(
            (r for r in self.route_registry if r.id == route_id), None
        )
        if not route:
            return self.no_match(context)
        return self._finalize(
            route,
            context,
            confidence=self._clamp_confidence(understanding.get("confidence")),
            insight=understanding.get("insight"),
            llm_entity=understanding.get("extracted_entity"),
            from_llm=True,
            last_discussed_project=last_discussed_project,
        )

    def try_feature_lookup(
        self, context: SessionContext
    ) -> NavigationResponse | None:
        """"Where is X" about something ON a page, not which page to open.

        If the user is currently on the page that has it: point directly at it
        (highlight). If it lives on a DIFFERENT page: offer a bubble there,
        naming the feature so they know what they're going to find.
        Returns None if no page_feature keyword matches anything — the caller
        falls through to normal route navigation/LLM handling.
        """
        response, _ = self.try_feature_lookup_scored(context)
        return response

    def try_feature_lookup_scored(
        self, context: SessionContext
    ) -> tuple[NavigationResponse | None, int]:
        """Same as try_feature_lookup, but also returns the winning match's
        score so the orchestrator can compare it against a keyword-matched
        route's score instead of always letting a feature match win outright
        — that flat priority was exactly what caused "start a bid wizard" to
        land on a Sales Estimate button instead of navigating to /createbid,
        and "show me the customers list" to land on an Admin tab suggestion
        instead of /customertable, even though each had a clean, more
        specific keyword match available.

        Global features, the current page, and every other page are all
        candidates now, compared by score, rather than global features
        winning outright the moment any keyword matches at all — that flat
        priority is exactly what caused "where can i see uploaded bills" (on
        the Project Information page) to land on the global "Upload Bill"
        Topbar action instead of that page's own, far more specific "Vendor
        / Subcontractor Bill list" feature. Ties favor the current page over
        a global feature, and a global feature over a cross-page suggestion,
        since both are more directly useful than "that's on another page."
        """
        utterance = context.utterance.lower()
        current_route = self._route_by_pattern(context.currentRoute)

        def resolve_post_click(match: "PageFeature") -> list[str] | None:
            """Which specific option (if any) inside the menu/dropdown that
            `match.locate` opens should get the SECOND ring, based on what
            was actually asked -- not one hardcoded option regardless of
            phrasing. Falls back to match.post_click_locate (or nothing) if
            no specific option's own keywords match this utterance."""
            for option_kw, option_locate in match.post_click_options.items():
                option_words = option_kw.split()
                if all(self._word_matches(w, self._tokenize(utterance)) for w in option_words):
                    return option_locate
            return match.post_click_locate or None

        global_match, global_score = self._match_feature(utterance, self.global_features)
        # AB#30853: a global feature gated on its own permission_required/
        # permission_required_any (e.g. Cart & Drafts) must never win this
        # match for a caller who doesn't hold it -- global_features have no
        # owning route, so nothing else in this method's permission story
        # (route.permission_required) would ever catch this. Dropping the
        # match here, same as the route sweep's `if not permission_check
        # (route): continue` below, lets every downstream branch (the Cart
        # special-case, the tie-break against cross-page matches) behave as
        # though this feature simply didn't match -- consistent with how a
        # denied route is silently excluded from a suggestion list, not
        # surfaced as an explicit denial.
        if global_match is not None and not self._feature_permission_ok(global_match, context):
            global_match, global_score = None, 0

        current_match, current_score = (None, 0)
        if current_route:
            current_match, current_score = self._match_feature(
                utterance, current_route.page_features
            )

        if current_match and current_score >= global_score:
            # Real bug, fixed: confirmed live -- an action-phrased request for
            # something already on the CURRENT page ("list the bills in
            # inventory" while sitting on /inventory) used to only ring the
            # element, never click it -- the same utterance asked from a
            # DIFFERENT page (the cross-page branch below) already auto-
            # clicked. That inconsistency is exactly why a same-page ask like
            # switching Inventory's client-side sub-tab (Stock overview/Stock
            # POs/Stock Bills/Stock Delivery Slips -- not separate routes,
            # just tab state) looked like it "did nothing": the tab element
            # was highlighted but never actually clicked into. "Where"/"how"
            # phrasing stays click-free -- it's asking to be shown, not acted
            # on (see _INFORMATIONAL_LOOKUP_RE).
            post_click = resolve_post_click(current_match)
            # A target that only exists once its dropdown/menu is opened has
            # to be opened even for a "where is it" question -- see the
            # cross-page branch below for the full reasoning.
            informational = bool(_INFORMATIONAL_LOOKUP_RE.search(utterance))
            return NavigationResponse(
                type="guidance",
                message=f"Here it is — {current_match.hint}",
                insight=current_match.hint,
                highlight=current_match.name,
                highlightTargets=current_match.locate or [current_match.name],
                autoClick=(not informational) or bool(post_click),
                postClickHighlightTargets=post_click,
            ), current_score

        if global_match and global_match.name == _CART_DRAFTS_FEATURE_NAME:
            # "edit/delete/remove/cancel bid" naming a project that's
            # already been booked into a real Gold project isn't a Cart
            # draft anymore -- EDEN never edits or deletes anything itself,
            # it only navigates the user to where THEY can do it (see
            # _bid_edit_navigate_response). If the named project resolves in
            # Gold, send them straight to Project Information instead of the
            # generic Cart pointer; if it doesn't resolve, the bid is still
            # a pre-booking draft Gold has no record of, and the Cart
            # pointer below is the honest, only-correct answer.
            bid_response = self._bid_edit_navigate_response(context)
            if bid_response is not None:
                return bid_response, global_score

        def global_response() -> tuple[NavigationResponse, int]:
            # Same autoClick fix as the current-page branch above -- a global
            # feature (e.g. a Topbar action) asked for with action phrasing
            # should end up clicked, not just circled.
            post_click = resolve_post_click(global_match)
            informational = bool(_INFORMATIONAL_LOOKUP_RE.search(utterance))
            return NavigationResponse(
                type="guidance",
                message=f"Here it is — {global_match.hint}",
                insight=global_match.hint,
                highlight=global_match.name,
                highlightTargets=global_match.locate or [global_match.name],
                autoClick=(not informational) or bool(post_click),
                postClickHighlightTargets=post_click,
            ), global_score

        # Search every other route, keeping the best-scoring cross-page
        # match(es). Real bug, fixed: confirmed live -- this sweep used to be
        # unreachable whenever ANY global feature matched, because the global
        # branch returned outright above. That is the same flat-priority bug
        # this function's docstring says was fixed for the current page, just
        # never extended to the cross-page tier: "how to update a trade"
        # matched the global Notifications bell on the single word "updates"
        # (score 107) and beat question_builder's "Trade SetUp button" (score
        # 314), which is exactly the "suggests unrelated workflows" failure
        # AB#30005 was raised about. Global is now compared by score like
        # everything else, still winning ties (see the docstring's ordering
        # rule) but no longer winning outright on a far weaker match.
        best_route, best_match, best_score = None, None, 0
        # Real bug, fixed: confirmed live -- "let me add a customer" matches
        # an identically-scored "add customer" keyword on BOTH Leads &
        # Customers' and Admin > Customers' own "Invite Customer" page
        # features. This sweep used to keep only the single best (`score >
        # best_score`, strict), so a genuine tie silently fell to whichever
        # route happened to come first in route_registry.json's list order
        # -- landing on the same page every time by accident of list
        # position, not understanding, and never giving the user a chance
        # to say which one they meant. Track every route that ties the top
        # score, not just the first.
        tied_routes: list[tuple["RouteEntry", "PageFeature"]] = []
        # Resolve this user's permissions ONCE for the whole sweep. Calling
        # _has_permission per route re-queried Mongo every iteration: ~0.23s
        # per Cosmos round-trip x ~70 routes = ~16s of the ~20s a request
        # took, on EVERY request, since this tier runs before the LLM path
        # (measured live). Request-scoped by construction -- fetched fresh on
        # each call, so unlike a TTL cache it cannot serve a stale permission
        # set after a revocation.
        permission_check = self._permission_checker(context)
        for route in self.route_registry:
            if route is current_route:
                continue
            # Never suggest a page the user has no permission for -- a
            # cross-page feature suggestion is still an offer to navigate
            # there (see module-level fix note on _has_permission).
            if not permission_check(route):
                continue
            match, score = self._match_feature(utterance, route.page_features)
            if not match:
                continue
            if score > best_score:
                best_route, best_match, best_score = route, match, score
                tied_routes = [(route, match)]
            elif score == best_score:
                tied_routes.append((route, match))

        # Global features are more directly useful than "that's on another
        # page", so they still win a TIE -- but only a tie. A weaker global
        # match must not beat a stronger, more specific cross-page one.
        if global_match and global_score >= best_score:
            return global_response()

        # Only ask for a genuine ACTION request ("let me add a customer") --
        # "where"/"how" phrasing stays a plain informational suggestion even
        # when it ties (e.g. project_invoice_view/project_invoice_preview
        # both have a "Send Invoice button" -- functionally two views of the
        # same document, not a real fork worth interrupting a "where is X"
        # question over; the action case below is the one where landing on
        # the wrong one of two GENUINELY different pages actually matters).
        if len(tied_routes) > 1 and not _INFORMATIONAL_LOOKUP_RE.search(utterance):
            options = [
                NavigationOption(
                    label=route.label_template,
                    route=route.resolved_route(),
                    icon=route.icon,
                    description=route.description,
                    routeState=match.route_state or route.resolved_route_state(),
                    # Real bug, fixed: confirmed live -- picking either
                    # option here used to land on a bare page with no
                    # spotlight, even though the SAME utterance would have
                    # triggered a highlighted, auto-clicked navigate had
                    # only one route matched (see the single-match action
                    # branch below). Already gated to action-only by this
                    # branch's own `not _INFORMATIONAL_LOOKUP_RE` check, so
                    # autoClick is always correct here.
                    highlightTargets=match.locate or [match.name],
                    autoClick=True,
                )
                for route, match in tied_routes[:3]
            ]
            # Same honesty check as AgentOrchestrator._disambiguation_response
            # (kept independent rather than shared -- this tier runs before
            # that method's LLM/retrieval path even engages): if every tied
            # route matched the SAME named feature, say the action is
            # identical either way instead of implying they're different.
            tied_names = {m.name.lower() for _, m in tied_routes}
            question = (
                f"{tied_routes[0][1].name} works the same either way — "
                f"which page would you like to go to?"
                if len(tied_names) == 1
                else "A few pages match — which did you mean?"
            )
            return NavigationResponse(
                type="disambiguation",
                question=question,
                options=options,
            ), best_score

        if best_match:
            route_state = best_match.route_state or best_route.resolved_route_state()
            post_click = resolve_post_click(best_match)
            # Real bug, fixed: confirmed live -- "where can i add sell price"
            # took this informational path and rang the CLOSED Material
            # Fields dropdown. That doesn't answer the question: Selling
            # Price is a hidden-by-default column that doesn't exist in the
            # DOM until the dropdown is opened, so there is physically
            # nothing to point at. A feature whose real answer only appears
            # after opening it (post_click targets) is the one exception to
            # the "never click a where-question" rule below -- opening it IS
            # the answer.
            if _INFORMATIONAL_LOOKUP_RE.search(utterance) and not post_click:
                # "Where"/"how" phrasing is asking to LOCATE something, not
                # asking to be taken there right now -- describe it and offer
                # a suggestion to click, same as before.
                return NavigationResponse(
                    type="fallback",
                    message=(
                        f"That's on the {best_route.label_template} page, not here. "
                        f"{best_match.hint}"
                    ),
                    insight=best_match.hint,
                    highlight=best_match.name,
                    highlightTargets=best_match.locate or [best_match.name],
                    suggestion=NavigationSuggestion(
                        label=best_route.label_template,
                        route=best_route.resolved_route(),
                        # The FEATURE's own route_state (e.g. QuickBooks tab's
                        # {"selectedTab": "9"}) takes priority over the route's
                        # own resolved_route_state() — a tab-scoped feature needs
                        # ITS specific tab, not whatever the bare route defaults
                        # to. Without this, "open quickbooks" from another page
                        # suggested plain /admin with no tab selected at all.
                        routeState=route_state,
                    ),
                ), best_score
            # Anything else that matched is a request to DO the thing --
            # navigate there directly with the highlight already attached,
            # instead of describing it and waiting for a second ask/click.
            # Real bug, fixed: joining with " — " read awkwardly whenever
            # hint ITSELF already contains its own em-dash (most do, e.g.
            # "The Add New Material button — adds an editable row to the
            # table.") -- "Taking you to Materials — The Add New Material
            # button — adds an editable row to the table." Two full
            # sentences (period, not a second dash) reads naturally either
            # way.
            return NavigationResponse(
                type="navigate",
                route=best_route.resolved_route(),
                label=best_route.label_template,
                icon=best_route.icon,
                description=best_route.description,
                module=best_route.module,
                message=f"Taking you to {best_route.label_template}. {best_match.hint}",
                insight=best_match.hint,
                highlight=best_match.name,
                highlightTargets=best_match.locate or [best_match.name],
                routeState=route_state,
                # Per explicit user direction: an action request ("I need to
                # add a new material") should end with the button already
                # clicked, not just circled -- the frontend's PageSpotlight
                # does the actual click once it locates the live element.
                autoClick=True,
                postClickHighlightTargets=post_click,
            ), best_score
        return None, 0

    def find_feature_by_name(self, route: RouteEntry, name_contains: str) -> "PageFeature | None":
        """Looks up a route's own page_features by a substring of the
        feature's `name` (case-insensitive) -- e.g. "notes" matches this
        route's "Notes panel toggle"/"notes toggle"/"Show/Hide Notes toggle"
        entry, whichever name convention that particular page happens to
        use. Purely a registry lookup, same data-driven spirit as
        find_topic_feature -- no per-page special-casing, works on any
        route that happens to define a feature by that name and returns
        None on any that doesn't."""
        needle = name_contains.lower()
        return next(
            (f for f in route.page_features if needle in f.name.lower()), None
        )

    def find_topic_feature(self, utterance: str, route: RouteEntry) -> "PageFeature | None":
        """Public wrapper around _match_feature for a single route's own
        page_features -- lets a caller that already knows WHICH route it's
        confirming (e.g. AgentOrchestrator._resolve_affirmation replaying a
        remembered page suggestion) find the specific on-page field the
        ORIGINAL question was about, so it can highlight that field on
        arrival instead of just landing on the bare page. Fully data-driven
        off route_registry.json's existing page_features/keywords -- same
        matching this file already uses for feature-lookup, no per-field
        (start date/end date/cost/address, or anything else) special-casing
        needed; whatever page_feature's keywords the utterance actually
        matches wins, automatically covering every field already defined in
        the registry and any added later."""
        feature, _score = self._match_feature(utterance, route.page_features)
        return feature

    def is_informational_lookup(self, utterance: str) -> bool:
        """Public accessor for AgentOrchestrator._disambiguation_response --
        true for "where"/"how" phrasing (see _INFORMATIONAL_LOOKUP_RE),
        which must never get autoClick on a disambiguation option (a
        locational question is asking to be shown something, not asking
        for it to be clicked for them) -- same rule
        try_feature_lookup_scored's own tie branch already applies."""
        return bool(_INFORMATIONAL_LOOKUP_RE.search(utterance))

    def _match_feature(
        self, utterance: str, features: list[PageFeature]
    ) -> tuple[PageFeature | None, int]:
        # Word-set, typo-tolerant match — same rules as route keywords: "filter
        # by status" matches "status filter" regardless of order, and a typo
        # like "staus" still lands on the right feature. Scoring matches
        # _keyword_match's formula exactly (word count * 100 + keyword length)
        # so feature-match and keyword-match scores are directly comparable —
        # they used to be on different scales (plain word count here vs the
        # weighted formula there), which is part of why feature-lookup always
        # "won" regardless of how specific the competing keyword match was.
        tokens = self._tokenize(utterance)
        best: PageFeature | None = None
        best_score = 0
        for feature in features:
            for kw in feature.keywords:
                kw_words = kw.split()
                # Real bug, fixed: a single-word keyword ("previous", "next",
                # "back") is common enough that it can appear incidentally
                # anywhere in a long, unrelated sentence -- "ignore all
                # PREVIOUS instructions..." matched a page's literal
                # "Previous button" feature this way. A multi-word keyword
                # phrase is specific enough to trust appearing anywhere; a
                # single word only means what it looks like it means when
                # it's most of what was actually said.
                if len(kw_words) == 1 and len(tokens) > 5:
                    continue
                if all(self._word_matches(w, tokens) for w in kw_words):
                    score = len(kw_words) * 100 + len(kw)
                    if score > best_score:
                        best, best_score = feature, score
        return best, best_score

    def _route_by_pattern(self, current_route: str | None) -> RouteEntry | None:
        if not current_route:
            return None
        # Pick the longest (most specific) matching pattern, not the first
        # one in registry order — otherwise a short parent route like
        # "/inventory" or "/soldproduction" incorrectly claims every nested
        # page under it (e.g. "/soldproduction/projectinformation") whenever
        # it happens to appear earlier in route_registry.json.
        matches = [
            r for r in self.route_registry
            if current_route.startswith(r.pattern.split("/:")[0])
        ]
        if not matches:
            return None
        return max(matches, key=lambda r: len(r.pattern.split("/:")[0]))

    def data_question_response(
        self, context: SessionContext, understanding: dict | None = None
    ) -> NavigationResponse:
        """Fallback for a data question EDEN couldn't actually answer this
        turn -- either Insight/Reporting couldn't resolve a project from the
        message (0 or ambiguous matches it couldn't otherwise handle), or
        eden_router sent this to Navigation instead of a data agent. Turn it
        into a helpful redirect: honest message + bubble buttons to the
        page(s) where the answer lives. NOTE: the message used to claim "the
        Finance and Schedule agents are coming soon" -- stale copy from
        before Insight/Reporting existed, and actively misleading once they
        did (they exist and may have just tried and failed to resolve the
        project named)."""
        route_ids: list[str] = []
        if understanding and understanding.get("route_id"):
            route_ids.append(understanding["route_id"])

        # Per explicit user direction: LLMClient.suggest_related_routes reads
        # the actual question against real candidate descriptions instead of
        # the fixed DATA_TOPIC_ROUTES phrase table, which is now the
        # fallback-only path (LLM unreachable) -- see that constant's
        # comment. Every caller of data_question_response gets this,
        # including the two paths that previously never called the LLM at
        # all (Insight/Reporting's "couldn't resolve a project" redirect).
        suggested = None
        if self.llm_client is not None:
            candidates = self.top_candidates(context.utterance)
            suggested = self.llm_client.suggest_related_routes(
                context.utterance, [r.model_dump() for r, _ in candidates]
            )
        if suggested is not None:
            route_ids += suggested.get("route_ids") or []
        else:
            route_ids += self._data_topic_routes(context.utterance.lower())

        options: list[NavigationOption] = []
        seen_routes: set[str] = set()
        for rid in route_ids:
            opt = self._route_option(rid)
            if opt and opt.route not in seen_routes:
                seen_routes.add(opt.route)
                options.append(opt)
            if len(options) >= 3:
                break

        insight = (understanding or {}).get("insight")
        if options:
            return NavigationResponse(
                type="fallback",
                message=(
                    "I couldn't pull that up directly — I may not have matched the "
                    "right project. But I can take you straight to where you'll "
                    "find it:"
                ),
                insight=insight,
                options=options,
            )
        return NavigationResponse(
            type="fallback",
            message=(
                "I couldn't find a project matching that — try naming the exact "
                "project or its order number, or ask me to list your projects. I "
                "can still navigate you anywhere in FIELDCRAFT."
            ),
            insight=insight,
        )

    def no_match(self, context: SessionContext) -> NavigationResponse:
        suggestion = self._closest_route_suggestion(context.utterance.lower(), context)
        if suggestion:
            return NavigationResponse(
                type="fallback",
                message=f"I couldn't find that page. Did you mean {suggestion.label}?",
                suggestion=suggestion
            )
        return NavigationResponse(
            type="fallback",
            message="I couldn't find that page. Try asking for 'schedule', 'invoices', or 'purchase orders'."
        )

    # Project Information's own tabs, each gated by a DIFFERENT permission
    # than the shell page itself (PRO.REA just gets you onto the page --
    # each tab inside it has its own real-app gate). "Tasks" carries no
    # permission_required anywhere in the registry -- always listed.
    #
    # Real bug, fixed: confirmed live -- route.page_help is a static JSON
    # string ("switch between tabs like Tasks, BOM, PO, Invoice, and
    # Documents...") that lists every tab regardless of role. A customer
    # (no BOM.REA/PO.REA) asking "what can I do here" got told about tabs
    # they can't open at all -- a hardcoded response, not one shaped by
    # their actual permissions. Used by _project_information_help_text to
    # build the tab clause dynamically instead.
    # Real bug, fixed: this used to hardcode each tab's permission code as a
    # literal string here, duplicating contracts/route_registry.json's own
    # permission_required field on the route that actually owns each tab
    # (confirmed live: project_bom/po_details/delivery_slip/bill_form/
    # project_invoice_view/project_payments/job_ledger/project_documents/
    # project_wip_report/project_vendor_bill each already carry the exact
    # same code) -- a second, unguarded copy that wouldn't follow if any of
    # those routes' permission ever changed. Now maps each tab label to the
    # ROUTE ID that owns it (stable UI naming, not permission data) and
    # reads permission_required from the registry itself at use time in
    # _project_information_help_text -- the registry stays the only source
    # of truth for the actual permission code.
    _PROJECT_TAB_ROUTE_ID = {
        "BOM": "project_bom",
        "PO": "po_details",
        "Delivery Slips": "delivery_slip",
        "Bill": "bill_form",
        "Invoice": "project_invoice_view",
        "Payments": "project_payments",
        "Job Ledger": "job_ledger",
        "Documents": "project_documents",
        "WIP Report": "project_wip_report",
        "Vendor / Subcontractor Bill": "project_vendor_bill",
    }

    def _project_information_help_text(self, context: SessionContext) -> str:
        if os.getenv("SKIP_RBAC_CHECK", "false").lower() == "true":
            permissions = None  # None = unrestricted, matches _check_permission's dev bypass
        else:
            permissions = self.role_resolver.get_permissions(context.tenantId, context.userId)
        by_id = {r.id: r for r in self.route_registry}

        def _tab_visible(route_id: str) -> bool:
            route = by_id.get(route_id)
            required = route.permission_required if route else None
            return permissions is None or not required or required in permissions

        tabs = ["Tasks"] + [
            name for name, route_id in self._PROJECT_TAB_ROUTE_ID.items()
            if _tab_visible(route_id)
        ]
        tab_list = tabs[0] if len(tabs) == 1 else ", ".join(tabs[:-1]) + f", and {tabs[-1]}"
        return (
            f"Here you can review this project's full details and overview, switch "
            f"between tabs like {tab_list}, toggle the Gantt/Task List view, create or "
            f"view the estimate, and manage hold status."
        )

    def guidance(
        self, context: SessionContext, insight: str | None = None
    ) -> NavigationResponse:
        route = self._route_by_pattern(context.currentRoute)
        if route:
            # skipVoiceRewrite=True for project_information: this tab list is
            # permission-filtered (see _project_information_help_text) and
            # voice-rewrite's fact-preservation guard only verifies NUMBERS
            # survive unchanged, not page/tab names -- nothing would catch
            # the rewrite model "helpfully" restoring a filtered-out tab
            # from its own general knowledge of what such pages look like.
            is_project_info = route.id == "project_information"
            if is_project_info:
                help_text = f" {self._project_information_help_text(context)}"
            else:
                help_text = f" {route.page_help}" if route.page_help else ""
            return NavigationResponse(
                type="guidance",
                message=f"You are on the {route.label_template} page, part of the "
                        f"{route.module} module.{help_text}",
                insight=insight or route.description,
                skipVoiceRewrite=is_project_info,
            )
        return NavigationResponse(
            type="guidance",
            message="You can ask me to navigate to schedules, invoices, or purchase orders.",
            insight=insight
        )

    # ── shared navigation pipeline ─────────────────────────────────────────

    def _finalize(
        self,
        route: RouteEntry,
        context: SessionContext,
        confidence: float,
        insight: str | None = None,
        llm_entity: str | None = None,
        from_llm: bool = False,
        last_discussed_project: str | None = None,
    ) -> NavigationResponse:
        insight_text = _ground_insight(insight, route) or route.description
        already_here = bool(context.currentRoute) and self._is_already_on_page(
            route, context.currentRoute
        )

        if route.entity_required == "project":
            # LLM path: trust the model's extraction — never fall back to
            # crude word-stripping, which turns "what about the schedules"
            # into a fake project name. Page-context fallback is handled
            # explicitly below (via the exact id) rather than folded in
            # here, so it isn't lost to _current_project_name's name-only
            # read (see _resolve_with_entity's docstring for why that
            # matters).
            if from_llm:
                entity_name = llm_entity
                # Real bug, fixed: confirmed live -- "take me to ron kasper"
                # (6 real projects named "Ron Kasper, Cedarburg-..." exist
                # for this tenant) came back with extracted_entity="<PERSON>",
                # not "ron kasper". understand()'s user-role prompt passes
                # through guardrails' Presidio rail before the model ever
                # sees it -- Presidio has no way to know a full name inside
                # chat is a legitimate project reference (every project in
                # this domain IS named after a customer), so it redacts it
                # the same as any other PERSON entity, and the model just
                # echoes the placeholder back as its own "extraction". This
                # is the same PRESIDIO_PLACEHOLDER_RE bug class already fixed
                # twice for OUTPUT-side leaks (rephrase(),
                # decide_project_context_switch) -- see
                # eden_pii_redaction_name_corruption in memory -- but this is
                # an EXTRACTION-side failure: there's no "known real name" to
                # fall back to from the model's own corrupted output, so
                # instead fall back to the deterministic, LLM-free stop-word
                # stripper on EDEN's own un-redacted context.utterance (never
                # sent anywhere, so it can't be redacted) -- the same
                # last-resort extractor the from_llm=False branch below
                # already uses when the LLM call fails outright.
                if entity_name and PRESIDIO_PLACEHOLDER_RE.search(entity_name):
                    entity_name = self._extract_entity_name(
                        context, fallback_to_page_context=False
                    )
            else:
                # Real change, per explicit user direction: the fast
                # exact-keyword tier used to extract the project name via
                # the deterministic ENTITY_STOP_WORDS stripper alone,
                # never calling the LLM at all -- the source of every
                # "route's own vocabulary word wasn't in the stop-word
                # list" bug tonight (delivery/invoice/purchase/wip all hit
                # this exact path). Now calls the real extraction model
                # here too, same as the from_llm=True path already does,
                # accepting the extra ~1s round-trip. The deterministic
                # stripper survives ONLY as the true last-resort fallback
                # for when the LLM call itself fails outright (returns
                # None) -- there is no model to defer to in that case.
                extraction = self.llm_client.extract_project_name(context.utterance)
                if extraction is not None:
                    entity_name = extraction.get("project_name")
                else:
                    entity_name = self._extract_entity_name(context, fallback_to_page_context=False)

            # Real bug, fixed: no name spoken used to fall back to
            # _current_project_name(context) alone -- which reads
            # currentEntity.get("name"), a field the frontend almost never
            # sends (deriveCurrentEntity sends an id OR a name, preferring
            # the id whenever one is available). That silently discarded
            # page context on nearly every entity-required request that
            # named no project ("show me the material list" while on Peter
            # Parker's page asked "which project?" instead of using the
            # page you're plainly looking at). Try the exact id first,
            # mirroring how Insight/Reporting already prioritize it.
            entity_id = None
            if not entity_name:
                entity_id = self._current_project_id(context)
                if not entity_id:
                    entity_name = self._current_project_name(context)
            else:
                # Real bug, fixed: confirmed live -- a signal-free reply
                # ("yes") to a suggestion the guardrail correctly stripped
                # (permission denied) falls through to a fresh LLM dispatch,
                # which -- having only conversation HISTORY to go on, no new
                # signal from "yes" itself -- extracts the project by NAME
                # from that history (e.g. "Peter Parker"). Real Gold data
                # has severe name duplication, so resolving that name alone
                # finds 3+ projects and re-asks "which project?" even though
                # the user is SITTING on that exact project's own page right
                # now, with an unambiguous id available. When the extracted
                # name matches the CURRENT page's own project name, prefer
                # the exact id over the ambiguous name -- "the one you're
                # looking at" is a far safer read than a name collision that
                # doesn't actually apply to what's being discussed. Does
                # NOT fire when the name differs from the page's project --
                # that's a genuine switch request, left untouched.
                #
                # Real bug, fixed: page_name via _current_project_name(context)
                # (currentEntity.get("name")) is almost always None -- the
                # frontend sends an id OR a name, preferring the id (see
                # deriveCurrentEntity), which is exactly the common case here
                # (project_information's page state is a bare projectId).
                # Resolving the page's own project NAME by its exact id
                # (resolve_by_id) instead of trusting a field the frontend
                # essentially never populates is what actually makes this
                # match fire in the real, common case.
                page_id = self._current_project_id(context)
                if page_id:
                    # Real bug, fixed (confirmed live): this safety net only
                    # ever compared the extraction against the page's real
                    # project NAME -- but the model's own extracted_entity
                    # sometimes echoes the page's raw numeric id instead of a
                    # name (e.g. "1239"), presumably because that id is what
                    # currentEntity/page context actually carries. Confirmed
                    # live: "show me the invoice" while on that project's own
                    # page extracted entity_name="1239" (project_invoice_
                    # view's own id), which trivially never equals the real
                    # project name, so the name-match branch below never
                    # fired -- entity_name stayed "1239", got searched as a
                    # NAME, found nothing, and produced "I couldn't find a
                    # project matching '1239'" while sitting on that exact
                    # project's page the whole time. Checked first (a plain
                    # string-equality against page_id is cheaper than the
                    # name lookup below, and an id match is unambiguous).
                    if entity_name.strip() == str(page_id).strip():
                        entity_id = page_id
                        entity_name = None
                if page_id and entity_name:
                    page_matches = self.entity_resolver.resolve_by_id(page_id, context.tenantId)
                    page_name = page_matches[0].project_name if len(page_matches) == 1 else None
                    if page_name and page_name.strip().lower() == entity_name.strip().lower():
                        entity_id = page_id
                        entity_name = None
            # Real bug, fixed: page context (currentEntity) is the ONLY
            # fallback checked above -- but a project established a turn
            # earlier via a REPORTING/INSIGHT answer (e.g. "what's the
            # status of Peter Parker?") never touches currentEntity at all,
            # that's frontend page state, not conversation state. A
            # follow-up naming no project and not on that project's page
            # ("show me the bom" right after discussing Peter Parker) fell
            # straight to "which project did you mean?", discarding context
            # Insight/Reporting already track via _last_discussed_project.
            # Checked LAST, after page context, matching Insight/Reporting's
            # own priority order (explicit name > page > last discussed).
            if not entity_name and not entity_id and last_discussed_project:
                entity_name = last_discussed_project

            # "Already on this page" only holds when no DIFFERENT project was
            # named/identified — entity pages are one route for every
            # project, so "show the Buma schedule" while on Henderson's
            # Gantt must resolve Buma, not stop at "you're already on Gantt
            # Charts".
            current_id = self._current_project_id(context)
            current_name = self._current_project_name(context)
            same_project = (
                (not entity_name and not entity_id)
                or (entity_id is not None and current_id is not None and entity_id == current_id)
                or (
                    entity_name is not None and current_name is not None
                    and entity_name.strip().lower() == current_name.strip().lower()
                )
            )
            if already_here and same_project:
                return self._already_here_response(route, insight_text)
            denied = self._check_permission(route, context)
            if denied:
                return denied
            return self._resolve_with_entity(route, context, entity_name, insight_text, entity_id=entity_id)

        if already_here:
            return self._already_here_response(route, insight_text)

        denied = self._check_permission(route, context)
        if denied:
            return denied

        if "/:" in route.pattern:
            # A route pattern with an unresolved :param segment (e.g.
            # inventory_audit's /inventory/audit/:sku/:materialUid) has no
            # resolver to fill it in here — returning it verbatim would send
            # the user to a literally broken URL. Redirect to the nearest
            # ancestor route instead, same idea as _route_option's handling
            # of unresolvable project routes.
            parent = self._nearest_static_ancestor(route)
            if parent:
                return NavigationResponse(
                    type="navigate",
                    route=parent.resolved_route(),
                    label=parent.label_template,
                    icon=parent.icon,
                    description=parent.description,
                    module=parent.module,
                    message=(
                        f"{route.label_template} needs a specific item selected first — "
                        f"taking you to {parent.label_template} instead."
                    ),
                    insight=insight_text,
                    confidence=confidence,
                    routeState=parent.resolved_route_state(),
                )
            return NavigationResponse(
                type="fallback",
                message=f"I can't open {route.label_template} directly — it needs a specific item selected first."
            )

        return NavigationResponse(
            type="navigate",
            route=route.resolved_route(),
            label=route.label_template,
            icon=route.icon,
            description=route.description,
            module=route.module,
            message=f"{self._navigate_message(route, context)} {insight_text}",
            insight=insight_text,
            confidence=confidence,
            routeState=route.resolved_route_state(),
        )

    # Words that signal a page_feature is a real DATA point (a number, a
    # date, a balance) rather than a bare UI control (a button/tab/toggle) --
    # used by _pick_offerable_feature to decide what's worth proactively
    # offering vs. staying silent. Deliberately generic/registry-driven, not
    # a per-page list -- works for any route's page_features without
    # hardcoding page names.
    _OFFERABLE_FEATURE_KEYWORDS = {
        "balance", "outstanding", "paid", "unpaid", "total", "owe",
        "budget", "cost", "date", "kickoff", "target", "amount", "due",
    }
    _QUESTION_STARTERS = ("what", "how", "why", "when", "where", "who")
    # Categorizes a picked feature so the orchestrator's follow-up (see
    # AgentOrchestrator._answer_page_topic_offer) knows which existing
    # Reporting answer function to call once the user says "yes" -- date
    # and billing are distinct data shapes with their own already-built
    # answer+highlight functions; anything else defaults to the general
    # status answer.
    _DATE_TOPIC_WORDS = {"date", "kickoff", "target", "schedule", "start", "end", "due"}
    _BILLING_TOPIC_WORDS = {"balance", "paid", "unpaid", "total", "owe", "outstanding", "amount"}

    def _topic_kind(self, feature: "PageFeature") -> str:
        kws = " ".join(feature.keywords).lower()
        if any(w in kws for w in self._DATE_TOPIC_WORDS):
            return "date"
        if any(w in kws for w in self._BILLING_TOPIC_WORDS):
            return "billing"
        return "status"

    def _pick_offerable_feature(self, route: RouteEntry) -> "PageFeature | None":
        """Picks the single page_feature most likely to answer a REAL data
        question, scored by how many of its own keywords hit the data-ish
        vocabulary above. Returns None when nothing scores (e.g. a page
        whose only features are buttons) -- caller falls back to the old
        generic message rather than offering to "tell you about" a button."""
        best, best_score = None, 0
        for feature in route.page_features:
            score = sum(
                1 for kw in feature.keywords
                if any(word in kw.lower() for word in self._OFFERABLE_FEATURE_KEYWORDS)
            )
            if score > best_score:
                best, best_score = feature, score
        return best

    def _feature_topic_phrase(self, feature: "PageFeature") -> str:
        """A natural, sayable topic phrase for a page_feature -- its
        internal `name` (e.g. "Payment summary tiles") is a UI label, not
        something a person would ask about, so this picks the shortest of
        its own keywords that isn't itself phrased as a question ("what do
        i owe" -> skip, "balance" -> use), falling back to the feature name
        only when every keyword is question-shaped."""
        candidates = [kw for kw in feature.keywords if not kw.lower().startswith(self._QUESTION_STARTERS)]
        pool = candidates or feature.keywords
        return min(pool, key=len) if pool else feature.name.lower()

    def _already_here_response(
        self, route: RouteEntry, insight_text: str
    ) -> NavigationResponse:
        """Real gap, fixed: this used to always give a flat "You're already
        on X. {page_help}" -- confirmed live to feel like a dead end even
        when the page has a specific, askable data point (a schedule, a
        balance) right in front of the user. Proactively surface the
        highest-signal data-bearing page_feature as a discovery question
        instead, highlighting its on-screen location immediately -- no data
        is fetched here (that's the SAME answer-and-highlight flow already
        built end to end in Reporting/Insight once the user actually asks,
        e.g. by naming the topic or the exact field). Falls back to the old
        generic message when nothing on this page scores as data-bearing
        (e.g. a page of pure UI buttons/toggles) -- an unhelpful
        button-shaped question would be worse than the honest generic
        help text."""
        feature = self._pick_offerable_feature(route)
        if feature:
            topic = self._feature_topic_phrase(feature)
            return NavigationResponse(
                type="guidance",
                message=f"You're on {route.label_template}. Would you like to know about the {topic}?",
                insight=feature.hint,
                highlight=feature.name,
                highlightTargets=feature.locate or [feature.name],
                topicOffer={"route_id": route.id, "topic": topic, "kind": self._topic_kind(feature)},
            )
        help_text = route.page_help or "Need help finding something specific here?"
        return NavigationResponse(
            type="guidance",
            message=f"You're already on {route.label_template}. {help_text}",
            insight=insight_text,
        )

    def _check_permission(
        self, route: RouteEntry, context: SessionContext
    ) -> NavigationResponse | None:
        """Returns the denial response, or None when access is allowed.

        Real gap, fixed: `permission_required: null` used to still deny
        access (`None not in permissions` is True), even though several real
        pages (Home, Welcome, chat, image upload, customer registration, and
        the anonymous pages outside ProtectedRoute like /cancel, /plans,
        /quickbooks/callback) have NO permission check at all in the real
        app -- confirmed by grepping every one of those page components for
        isUserHasPermission and finding zero hits. Every route in the
        registry used to carry some non-null code as a placeholder (often
        the wrong one, e.g. TAS.REA) simply because null wasn't supported --
        now null means exactly what the real app does: always allowed."""
        if route.id == "create_asset":
            # AB#28704: create_asset used to gate on permission_required=
            # "AST.REA" -- confirmed against the real tenant RBAC data
            # (role_permission table) that this permission does not exist
            # for ANY role, admin included, and nothing else in that table
            # maps to "register company equipment" either (the similarly-
            # named ASS.* family is a different feature -- a customer's own
            # property/equipment record -- held only by the customer role).
            # There is no real permission to gate this on. Rather than
            # claim a permission denial that was never true, redirect to
            # Create Bid, where asset creation actually happens in the real
            # app, with an honest explanation instead of "you don't have
            # access". Always fires regardless of role -- this isn't a
            # per-user decision, the standalone page isn't how this is done.
            create_bid = self._route_by_id("create_bid")
            if create_bid is not None:
                # Real gap, found live (2026-09-15): this redirect used to
                # build the create_bid navigate UNCONDITIONALLY, never
                # checking whether the user actually holds create_bid's own
                # permission (PRO.CRE) -- an "upstream builder skipped its
                # permission check", caught only by the orchestrator's
                # separate, later-stage role-scope guardrail
                # (_finalize_turn), which then had to fall back to a generic
                # "That page isn't available with your current access"
                # deflection instead of an accurate denial, because by the
                # time it intervenes it no longer has this route's own label/
                # message to work with. Checking create_bid's own permission
                # HERE means a user who can't create bids either gets the
                # same accurate, specific denial (via this same function,
                # recursively) that asking for Create Bid directly would
                # give them -- not a vague catch-all from a downstream net.
                create_bid_denial = self._check_permission(create_bid, context)
                if create_bid_denial is not None:
                    return create_bid_denial
                return NavigationResponse(
                    type="navigate",
                    route=create_bid.resolved_route(),
                    label=create_bid.label_template,
                    icon=create_bid.icon,
                    description=create_bid.description,
                    module=create_bid.module,
                    message=(
                        "Create Asset isn't its own page — you can create "
                        "assets for customers from Create Bid. Taking you "
                        "there instead."
                    ),
                    routeState=create_bid.resolved_route_state(),
                )
        if route.permission_required is None:
            return None
        if os.getenv("SKIP_RBAC_CHECK", "false").lower() == "true":
            return None
        permissions = self.role_resolver.get_permissions(
            context.tenantId, context.userId
        )
        if route.permission_required not in permissions:
            # AB#30636: a denied route can still have a genuine, narrower
            # counterpart the user's ACTUAL permissions (fetched above from
            # gold_canonical, not assumed) do allow -- e.g. "book leave"
            # matches resource_management's own "Book Leave button"
            # page_feature (AB#28708 put it there so a user WITH
            # resource.READ reaches the Resources page's leave calendar
            # directly), but RES.REA gates the whole crew/workforce
            # management page, not leave-booking specifically. Confirmed
            # live: an employee who holds timesheets.READ (their own leave-
            # booking permission) but not resource.READ got told they had no
            # leave-booking access at all, when Timesheets' OWN, identically-
            # named "Book Leave button" page_feature was already the exact
            # right destination for them -- its hint says so verbatim:
            # "shown without resource.READ permission".
            #
            # Nothing here names a specific route, permission code, or
            # phrase list -- route_registry.json is the single source of
            # truth for all three (per this file's own module docstring), so
            # this looks for whatever it says instead of a hardcoded pair:
            # does the DENIED route have a page_feature this utterance
            # actually matches, and does some OTHER route expose a
            # same-named feature this user's real permissions (via the same
            # _permission_checker every other multi-route sweep in this
            # class uses) already allow? If so, that route/feature IS the
            # honest answer, not a permission denial -- any route pair the
            # registry models this way (same feature name, different gating
            # routes) gets this for free, not just Resources/Timesheets.
            #
            # RBAC review caught a real gap in a first draft of this fix:
            # matching on feature NAME alone is too loose -- the registry
            # reuses generic feature names ("Save button", "Update (row)",
            # "Go Back button") across many UNRELATED routes with different
            # permissions (e.g. po_details's and project_invoice_view's own,
            # unrelated "Save button" features). Requiring the ALT route's
            # feature to ALSO independently match the utterance via its own
            # keywords (not just share a label) rejects that incidental
            # collision -- "save purchase order" doesn't satisfy
            # project_invoice_view's own "save invoice"/"save edits"
            # keywords -- while still accepting the genuine Book-Leave case,
            # where "book leave" is itself a keyword on BOTH routes' features.
            matched_feature, _ = self._match_feature(
                context.utterance.lower(), route.page_features
            )
            if matched_feature is not None:
                permission_check = self._permission_checker(context)
                for alt_route in self.route_registry:
                    if alt_route.id == route.id or not permission_check(alt_route):
                        continue
                    alt_feature, _ = self._match_feature(
                        context.utterance.lower(), alt_route.page_features
                    )
                    if alt_feature is not None and alt_feature.name == matched_feature.name:
                        # Real gap, found live (2026-09-15): this branch only
                        # ever fires because the utterance matched an ACTION
                        # (a page_feature, e.g. "book leave") -- the user
                        # never asked for `route` (Resources) by name; it's
                        # purely the internal page EDEN's own keyword match
                        # happened to resolve that action to first. Naming
                        # it ("You don't have access to Resources...") leaks
                        # that internal routing detail and reads as denying
                        # something the user never asked for. Since this
                        # fallback only exists to answer the ACTION, phrase
                        # the message around the action's real destination
                        # only -- never the incidentally-denied route.
                        return NavigationResponse(
                            type="navigate",
                            route=alt_route.resolved_route(),
                            label=alt_route.label_template,
                            icon=alt_route.icon,
                            description=alt_route.description,
                            module=alt_route.module,
                            message=(
                                f"You can do that from {alt_route.label_template}. "
                                "Taking you there."
                            ),
                            routeState=alt_feature.route_state or alt_route.resolved_route_state(),
                            highlight=alt_feature.name,
                            highlightTargets=alt_feature.locate or [alt_feature.name],
                        )
            return self._permission_denied_response(route, permissions)
        return None

    def _has_permission(self, route: RouteEntry, context: SessionContext) -> bool:
        """Same check as _check_permission, as a plain bool -- for filtering
        a LIST of candidate routes (suggestions, disambiguation options)
        rather than gating one route the user asked to navigate to.

        Real bug, fixed 2026-07-20: suggestion-building paths
        (_closest_route_suggestion, the cross-page feature-lookup suggestion
        in try_feature_lookup_scored) never checked permission at all --  a
        page the user has no access to could be offered as a "did you mean
        X?" card, and confirming it with a bare "yes" navigated there
        directly (see AgentOrchestrator._resolve_affirmation's
        remembered-routeState replay), bypassing _check_permission entirely.
        Every route offered as a suggestion must pass this first.

        Same `permission_required is None` -> always-allowed rule as
        _check_permission (see its docstring) -- kept consistent so a
        no-permission-needed route (e.g. Home) is never filtered out of a
        suggestion/disambiguation list by this check alone."""
        if route.permission_required is None:
            return True
        if os.getenv("SKIP_RBAC_CHECK", "false").lower() == "true":
            return True
        permissions = self.role_resolver.get_permissions(
            context.tenantId, context.userId
        )
        return route.permission_required in permissions

    def _feature_permission_ok(self, feature: "PageFeature", context: SessionContext) -> bool:
        """Same permission convention as _has_permission, extended to a
        single PageFeature -- for a button/element gated more narrowly than
        the page it lives on (its own route.permission_required, if any, is
        checked separately and earlier -- reaching the page at all already
        implies that). Real gap, fixed: PageFeature had no permission field
        of any kind, so a route-level-only gate structurally could not stop
        EDEN from describing an inaccessible feature (Cart & Drafts offered
        to a Labor-role user who holds none of CART.REA/SALESCART.REA/
        BID.REA -- AB#30853). `permission_required_any` is an OR of codes,
        for a feature reachable via more than one real permission path;
        `permission_required` is the single-code case. Both absent -> no
        extra gate, same always-allowed default as route-level checks."""
        if feature.permission_required is None and not feature.permission_required_any:
            return True
        if os.getenv("SKIP_RBAC_CHECK", "false").lower() == "true":
            return True
        permissions = set(
            self.role_resolver.get_permissions(context.tenantId, context.userId)
        )
        if feature.permission_required_any:
            return any(p in permissions for p in feature.permission_required_any)
        return feature.permission_required in permissions

    def _permission_checker(self, context: SessionContext):
        """Returns a `(route) -> bool` predicate with this user's permissions
        already resolved, for callers that must check MANY routes in one pass
        (the cross-page feature sweep, suggestion/option filtering).

        Exists purely to collapse N Mongo round-trips into one: _has_permission
        re-reads gold_canonical.user_permissions on every call, which is
        ~0.23s against Cosmos -- fine once, ruinous inside a 70-route loop.
        Applies exactly the same rules as _has_permission (no
        permission_required -> allowed; SKIP_RBAC_CHECK honoured) so the two
        can never disagree; the only difference is WHEN the lookup happens.
        Deliberately not a TTL cache: the snapshot lives only as long as the
        predicate, so a revoked permission is picked up on the next request
        rather than lingering."""
        if os.getenv("SKIP_RBAC_CHECK", "false").lower() == "true":
            return lambda route: True
        permissions = set(
            self.role_resolver.get_permissions(context.tenantId, context.userId)
        )
        return lambda route: (
            route.permission_required is None
            or route.permission_required in permissions
        )

    def has_route_id_permission(self, route_id: str, context: SessionContext) -> bool:
        """Public permission check by route id, for callers that hold ids
        rather than RouteEntry objects (AgentOrchestrator's option-building
        paths -- disambiguation and out-of-scope suggestion cards). An
        unknown id returns False (never offer something we can't resolve).
        Same SKIP_RBAC_CHECK/get_permissions semantics as _has_permission --
        this is that check, keyed by id. Every route offered to the user as
        a clickable option MUST pass this, exactly as the frontend only
        renders pages the user's permissions allow."""
        route = next((r for r in self.route_registry if r.id == route_id), None)
        if route is None:
            return False
        return self._has_permission(route, context)

    def filter_permitted_route_ids(
        self, route_ids: list[str], context: SessionContext
    ) -> list[str]:
        """Batch form of has_route_id_permission, order preserved. Same
        one-lookup-per-sweep reason as _permission_checker: callers filtering
        a LIST of candidate option cards would otherwise pay a Mongo
        round-trip per id. Unknown ids are dropped, exactly as the singular
        method returns False for them."""
        permission_check = self._permission_checker(context)
        by_id = {r.id: r for r in self.route_registry}
        return [
            rid for rid in route_ids
            if rid in by_id and permission_check(by_id[rid])
        ]

    def _tab_aliased_route(
        self, route_pattern: str | None, route_state: dict | None
    ) -> RouteEntry | None:
        """If (route_pattern, route_state) is one of ENTITY_TAB_ALIAS_ROUTE_STATE's
        10 aliased tab routes -- recognized by the pattern being EXACTLY
        project_information's own resolved URL (never a prefix match; a
        genuine project_information navigation must not be misread as some
        tab) and route_state carrying that tab's selectedTab -- returns the
        ORIGINAL route entry whose OWN permission actually governs it (e.g.
        project_invoice_view/INV.REA for tab 7), not project_information's
        blanket PRO.REA. Returns None for anything else (including a
        genuine, bare project_information visit), so callers fall back to
        their normal resolution unchanged."""
        if not route_state or not route_pattern:
            return None
        info_route = self._route_by_id("project_information")
        if info_route is None or route_pattern != info_route.resolved_route():
            return None
        selected_tab = route_state.get("selectedTab")
        if selected_tab is None:
            return None
        aliased_id = _TAB_TO_ROUTE_ID.get(selected_tab)
        return self._route_by_id(aliased_id) if aliased_id else None

    def is_route_pattern_permitted(
        self, route_pattern: str | None, context: SessionContext,
        route_state: dict | None = None,
    ) -> bool:
        """Fail-closed permission check keyed by route PATTERN -- what a
        NavigationResponse actually carries (response.route, options[].route,
        suggestion.route). Used by the orchestrator's final role-scope
        guardrail (_enforce_role_scope) to scrub EVERY outgoing response, so
        no code path can surface a page the user can't reach even if its own
        builder forgot to filter.

        `route_state`: real gap, fixed -- a bare pattern string can't tell
        an aliased tab route apart from project_information itself (see
        _tab_aliased_route); passing the accompanying routeState lets this
        recover the tab's own real permission instead of silently falling
        back to project_information's PRO.REA for all ten of them.

        None/empty pattern -> True: there's nothing to gate (a pure guidance
        answer has no route). A pattern that resolves to no registry route ->
        False: never surface something we can't verify. Otherwise the same
        permission check every other gate uses (_has_permission, which honors
        permission_required=None as always-allowed and SKIP_RBAC_CHECK for
        local dev)."""
        if not route_pattern:
            return True
        route = self._tab_aliased_route(route_pattern, route_state) or self._route_by_pattern(route_pattern)
        if route is None:
            return False
        return self._has_permission(route, context)

    def verify_remembered_suggestion_permission(
        self, route_pattern: str, label: str | None, context: SessionContext,
        route_state: dict | None = None,
    ) -> NavigationResponse | None:
        """Re-verifies permission before AgentOrchestrator._resolve_affirmation
        replays a remembered suggestion's routeState directly, bypassing the
        normal _finalize/_check_permission path entirely (see that method's
        docstring). That replayed suggestion may have been built before
        _has_permission's fix, or the account's permissions may simply have
        changed since. Fails CLOSED (treats an unrecoverable route as
        denied) rather than silently allowing the replay -- this is a
        security-relevant gate, not a UX nicety, and a rare unresolvable
        case must never default to "allow".

        `route_state`: real gap, fixed -- a remembered tab-aliased
        suggestion's pattern always equals project_information's own URL
        (see ENTITY_TAB_ALIAS_ROUTE_STATE), so re-resolving by pattern alone
        always re-verified project_information's PRO.REA, never the tab's
        real permission (e.g. a replayed "Peter Parker — Project Invoices"
        suggestion was only ever checked against PRO.REA, not INV.REA).
        Checked FIRST, falling back to the existing label-based shell-page
        disambiguation (_route_for_remembered_suggestion) unchanged when the
        tab-alias condition doesn't apply."""
        route = self._tab_aliased_route(route_pattern, route_state) or \
            self._route_for_remembered_suggestion(route_pattern, label)
        if route is None:
            return self._permission_denied_response_unknown_route()
        return self._check_permission(route, context)

    def _route_for_remembered_suggestion(
        self, route_pattern: str, label: str | None
    ) -> RouteEntry | None:
        """Recovers the registry entry a remembered suggestion actually
        referred to. Routes that share a target shell page (e.g.
        customer_table and vendor_list both resolve to "/admin") are
        disambiguated by label; entity-scoped routes never collide on
        resolved_route() alone, so their composite remembered label (e.g.
        "Peter Parker — Project Invoices", which does NOT equal the route's
        own label_template) never needs to be matched at all."""
        candidates = [r for r in self.route_registry if r.resolved_route() == route_pattern]
        if len(candidates) == 1:
            return candidates[0]
        if label:
            for r in candidates:
                if r.label_template == label:
                    return r
        return None

    def _permission_denied_response_unknown_route(self) -> NavigationResponse:
        return NavigationResponse(
            type="fallback",
            message="I couldn't verify access to that page — please ask again.",
        )

    def _nearest_static_ancestor(self, route: RouteEntry) -> RouteEntry | None:
        # An explicit target_route wins over the URL-prefix guess: once a
        # parent page is retired (e.g. /leadscustomers after the Sales/CRM
        # tab consolidation), the prefix search would fall through to "/".
        if route.target_route:
            target = next(
                (r for r in self.route_registry if r.pattern == route.target_route), None
            )
            if target is not None:
                return target
        static_prefix = route.pattern.split("/:")[0]
        candidates = [
            r for r in self.route_registry
            if r.id != route.id
            and "/:" not in r.pattern
            and static_prefix.startswith(r.pattern)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: len(r.pattern))

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"[a-z0-9']+", text.lower())

    def _word_matches(self, target: str, utterance_tokens: list[str]) -> bool:
        """Does `target` appear in the utterance — exact, plural-tolerant, or
        (for words of 4+ letters) a typo-tolerant fuzzy match? Short words
        (<=3 letters, e.g. "po", "ar") never fuzzy-match — that's exactly what
        let "reports" match "po" and "are" match "ar" before word-boundary
        checks were added, so fuzzy tolerance must not reopen that hole.

        Fuzzy match also requires the two words be within 2 characters of the
        same length. Real typos (transpositions, single substitutions/drops)
        almost never change length by more than that ("shedule"/"schedule"
        diff=1, "invocies"/"invoices" diff=0) — but a compound word built on
        a shorter one can still score a high SequenceMatcher ratio purely from
        character overlap despite being a different word: "subcontractor" vs
        "contractor" scores 0.87 (diff=3), which incorrectly matched
        "subcontractors tab" to the unrelated "Contractors tab" feature on
        the Expenses page before this guard was added.

        Real bug, fixed: confirmed live -- "invited" (a status/lifecycle
        label like the "Invited" filter chip on the Customers/Employees
        tables) and "invite" (the action verb, "Invite Customer") are a
        DIFFERENT false-positive shape the length-diff guard alone doesn't
        catch: diff is only 1, and the ratio (0.92) clears the threshold
        easily, since one is simply the other plus a tense suffix, not a
        typo. That let a request naming the action ("invite a customer")
        incorrectly also match a feature whose keyword only ever meant the
        status ("invited customers"), corrupting the shared-feature check
        in AgentOrchestrator._disambiguation_response (find_topic_feature
        picked "Status filter chips" over "Invite Customer button" purely
        because "invited customers" is one character longer, per
        _match_feature's own length-based tie-break). A verb-tense suffix
        ("d"/"ed") added to the shorter word is a grammar variant, not a
        typo -- reject it before the ratio check ever runs.
        """
        stem = self._stem(target)
        for token in utterance_tokens:
            token_stem = self._stem(token)
            if token_stem == stem:
                return True
            if (
                len(stem) >= 4 and len(token_stem) >= 4
                and abs(len(stem) - len(token_stem)) <= 2
                and not self._is_tense_variant(stem, token_stem)
                and difflib.SequenceMatcher(None, token_stem, stem).ratio() >= 0.82
            ):
                return True
        return False

    def _is_probable_typo_of_filler(self, word: str, vocab: set[str]) -> bool:
        """Does `word` look like a misspelt filler/vocabulary word rather
        than a name? Deliberately looser than _word_matches' 0.82 bar, and
        used for a different decision: not "does this match a keyword"
        (where a false positive routes somewhere wrong), but "is this
        name-shaped enough to hand the turn to a project-scoped route"
        (where a false positive dead-ends the turn -- see the caller).
        Erring toward "typo" just defers to the LLM/RAG path, which reads
        both typos and real names better than this tier can.

        Length must be within 1 (a typo rarely changes length more). The
        3-character floor is deliberately lower than _word_matches' 4 --
        that guard exists because a short word fuzzy-matching a KEYWORD
        sends the turn to the wrong page ("reports" matching "po"), which
        is a real error; here the only consequence is preferring the
        LLM/RAG path, so common short slips ("sho" for "show") are worth
        catching. Below 3 characters everything looks like everything.
        """
        if len(word) < 3:
            return False
        for candidate in (*ENTITY_STOP_WORDS, *vocab):
            if len(candidate) < 3 or abs(len(candidate) - len(word)) > 1:
                continue
            if difflib.SequenceMatcher(None, word, candidate).ratio() >= 0.70:
                return True
        return False

    @staticmethod
    def _stem(word: str) -> str:
        """Strip a plural "s", but never down to a 1-character stem.

        Real bug, fixed: confirmed live -- the naive `word[:-1] if
        endswith("s")` turned the 2-letter words "is"/"as"/"us"/"vs" into
        "i"/"a"/"u"/"v", which then matched the PRONOUN "i" and the ARTICLE
        "a" as standalone tokens. That made any keyword phrase containing
        "is" match almost any sentence with "i" in it: the Materials "where
        is trade" keyword matched "where can I set up trade" (score 314)
        and out-ranked question_builder's correct "set up trade" (312),
        re-breaking AB#30005's own repro step. Only strip when a real stem
        (2+ characters) survives -- "pos"->"po" and "bills"->"bill" still
        work, "is"/"as" now stay themselves.
        """
        return word[:-1] if word.endswith("s") and len(word) > 2 else word

    @staticmethod
    def _is_tense_variant(a: str, b: str) -> bool:
        """True when the longer of the two words is exactly the shorter one
        plus a trailing "d" or "ed" ("invite"/"invited", "book"/"booked") --
        a verb-tense change, not a typo, so _word_matches' fuzzy tolerance
        must not treat them as the same word."""
        shorter, longer = sorted((a, b), key=len)
        return longer in (shorter + "d", shorter + "ed")

    def _keyword_match(
        self, utterance: str, context: SessionContext | None = None,
        exclude_id: str | None = None,
    ) -> tuple[RouteEntry | None, int]:
        # Word-set, typo-tolerant match: every word in the keyword phrase must
        # appear somewhere in the utterance (order-independent, each word
        # allowing plural/typo slack) — "shedule" or "take me to the schedule"
        # both hit "schedule" the same way "filter by status" hits "status filter".
        tokens = self._tokenize(utterance)
        # Real gap, fixed: a project-scoped route (entity_required) sharing
        # vocabulary with a tenant-wide one (project_bom's "material" vs.
        # material_management's "materials") used to be picked purely on
        # keyword-specificity score, with no awareness that the user is
        # sitting on a project's page right now -- asking "material" there
        # landed on the admin-wide Material Management catalog instead of
        # that project's own BOM tab. Currently viewing ANY project-scoped
        # page (context.currentEntity carries a project) is a strong signal
        # that a same-vocabulary request means THIS project, so give an
        # entity_required candidate a flat bonus large enough to win any
        # tie/near-tie against a tenant-wide route it shares words with —
        # never enough to beat a genuinely more specific tenant-wide phrase
        # ("material stock levels" for inventory, len-based score already
        # far higher), just enough to break the "same word, different
        # scope" ambiguity in favor of where the user already is.
        on_project_page = bool(
            context is not None
            and isinstance(context.currentEntity, dict)
            and context.currentEntity.get("type") == "project"
        )
        best_score = 0
        best_route = None
        for route in self.route_registry:
            if exclude_id is not None and route.id == exclude_id:
                continue
            for kw in route.intent_keywords:
                kw_words = kw.split()
                if all(self._word_matches(w, tokens) for w in kw_words):
                    # More specific (longer) keywords outrank shorter ones, so
                    # "project schedule" wins over "projects" for the same utterance.
                    score = len(kw_words) * 100 + len(kw)
                    if on_project_page and route.entity_required:
                        score += 50
                    if score > best_score:
                        best_score = score
                        best_route = route
        return best_route, best_score

    def _bid_edit_navigate_response(
        self, context: SessionContext
    ) -> NavigationResponse | None:
        """"edit bid"/"delete bid"/"remove bid"/"cancel bid" naming a project
        that's already been booked into a real Gold project isn't a Cart
        draft anymore -- it's just that project, editable/deletable the same
        way any other project is, from Project Information. EDEN never
        performs the edit/delete itself here or anywhere else; this only
        decides WHERE to send the user so they can do it themselves.

        Deliberately does its own resolve() check first (matching the same
        entity_id-then-name priority _resolve_with_entity/_resolve_entity_option
        already use) instead of calling _resolve_with_entity directly and
        trusting it to fail -- _resolve_with_entity's own "no match" message
        talks about the requested route ("I couldn't find a project
        matching...'"), which is wrong here: a genuine miss doesn't mean
        "try the full project name," it means the bid is still a pre-booking
        Cart draft with no Gold record at all, and the caller's existing
        Cart & Drafts guidance is the honest answer for that case. Returns
        None so the caller falls through to it.

        No LLM call -- this fires from the deterministic keyword-lookup tier
        (try_feature_lookup_scored), before the LLM path even runs, so entity
        extraction here uses the same stopword-stripping fallback
        _resolve_entity_option falls back to on LLM miss, not the LLM
        extractor itself."""
        entity_id = self._current_project_id(context)
        entity_name = None
        if not entity_id:
            entity_name = self._extract_entity_name(context, fallback_to_page_context=True)
            if not entity_name:
                return None
        if entity_id:
            matches = self.entity_resolver.resolve_by_id(entity_id, context.tenantId)
        else:
            matches = self.entity_resolver.resolve_project(entity_name, context.tenantId)
        if not matches:
            return None
        route = self._route_by_id("project_information")
        if route is None:
            return None
        return self._resolve_with_entity(
            route,
            context,
            entity_name=entity_name,
            entity_id=entity_id,
            insight=(
                "This is Project Information — you can make that change "
                "here yourself; EDEN only navigates, it doesn't edit or "
                "delete anything on its own."
            ),
        )

    def _resolve_with_entity(
        self,
        route: RouteEntry,
        context: SessionContext,
        entity_name: str | None = None,
        insight: str | None = None,
        entity_id: str | None = None,
    ) -> NavigationResponse:
        """`entity_id`: real bug, fixed -- this used to only ever resolve by
        NAME, even when the user was sitting on a project's page and named
        nothing ("show me the material list" while on Peter Parker's page).
        Navigation's own entity resolution had no equivalent of
        AgentOrchestrator._resolve_insight_entity's page-context-ID
        priority -- it only ever checked _current_project_name(context),
        which reads currentEntity.get("name"); the frontend's own
        deriveCurrentEntity sends an id OR a name, never both, and prefers
        the id whenever one is available (i.e. almost always) -- so that
        name check was satisfied almost never, silently discarding page
        context and asking "which project?" on nearly every entity-required
        navigation request that named no project. `entity_id` (an exact
        projectId from page context, resolved via resolve_by_id -- exact
        and immune to the duplicate-name problem) is now checked FIRST,
        mirroring how Insight/Reporting already prioritize it."""
        if entity_id:
            matches = self.entity_resolver.resolve_by_id(entity_id, context.tenantId)
        elif entity_name:
            matches = self.entity_resolver.resolve_project(
                entity_name, context.tenantId
            )
        else:
            return NavigationResponse(
                type="fallback",
                message=(
                    f"Which project did you mean? Try: "
                    f"'Henderson's {_example_label(route)}'."
                ),
            )

        if not matches:
            return NavigationResponse(
                type="fallback",
                message=f"I couldn't find a project matching '{entity_name}'. Try the full project name."
            )

        if len(matches) > 1:
            # Real bug, fixed: label was bare m.project_name -- confirmed
            # live, real Gold data has severe projectName duplication (one
            # placeholder name covers many projects), so this rendered as
            # 3+ IDENTICAL, unclickable-looking "Peter Parker" cards with no
            # way to tell them apart (description fell back to a raw status
            # string, which can itself be "unknown"/"Unknown" for every one
            # of them too). Same fix already applied in insight_agent.py's
            # projects_disambiguation for the exact same real-data shape --
            # this is a SEPARATE code path (entity-required route resolution
            # here vs. Insight/Reporting's own project lookup there) that
            # never got it. order_no is the real, unambiguous identifier.
            options = [
                NavigationOption(
                    label=f"{m.project_name} (Order #{m.order_no})" if m.order_no else m.project_name,
                    route=self._entity_route(route, m),
                    icon=route.icon,
                    description=m.status.replace("_", " ").title(),
                    project=ProjectRef(
                        id=m.project_uid, name=m.project_name, status=m.status
                    ),
                    routeState=self._entity_route_state(route, m),
                )
                for m in matches
            ]
            return NavigationResponse(
                type="disambiguation",
                question="Which project did you mean?",
                options=options
            )

        match = matches[0]
        label = f"{match.project_name} — {route.label_template}"

        return NavigationResponse(
            type="navigate",
            route=self._entity_route(route, match),
            label=label,
            icon=route.icon,
            description=route.description,
            module=route.module,
            message=f"Taking you to {label}.",
            insight=insight or route.description,
            confidence=0.91,
            project=ProjectRef(
                id=match.project_uid, name=match.project_name, status=match.status
            ),
            routeState=self._entity_route_state(route, match),
        )

    def _entity_route(self, route: RouteEntry, match: ProjectMatch) -> str:
        if route.id in ENTITY_TAB_ALIAS_ROUTE_STATE:
            # See ENTITY_TAB_ALIAS_ROUTE_STATE's module comment — this route
            # is a single-item detail page needing an id EDEN never has for
            # a general request; the honest match is project_information's
            # own tab listing every item for the project instead.
            info_route = self._route_by_id("project_information")
            if info_route is not None:
                return info_route.resolved_route()
        if ":projectId" in route.pattern:
            return route.pattern.replace(":projectId", match.project_uid)
        return route.resolved_route()

    def _entity_route_state(self, route: RouteEntry, match: ProjectMatch) -> dict:
        """None of today's entity routes carry :projectId in their URL — the
        frontend opens them with the project in router state (see
        test_navigate_infers_context_from_llm_entity). Merge the resolved
        project into the route's own state so `routeState` alone is enough to
        deep-link, matching how non-entity routes already travel.

        `rowData`: despite the name, ProjectInformation_v2.tsx's own state
        key is just the bare project id (verified live: the real View button
        calls onProjectInformationPage(project.id, ...), and every child --
        getProjectInformation(rowData, ...), <InvoiceList project={rowData}>
        -- uses it as a plain id, never a full row object). Setting it here
        is what makes a project_information (or aliased tab) deep link
        actually resolve a project instead of rendering blank."""
        state = dict(route.resolved_route_state() or {})
        alias_state = ENTITY_TAB_ALIAS_ROUTE_STATE.get(route.id)
        if alias_state is not None:
            state.update(alias_state)
        state.setdefault("rowData", match.project_uid)
        state.setdefault("projectId", match.project_uid)
        state.setdefault("projectName", match.project_name)
        return state

    def _navigate_message(self, route: RouteEntry, context: SessionContext) -> str:
        utterance = context.utterance.lower()
        if any(w in utterance for w in ("take me", "go to", "open", "navigate")):
            return f"Taking you to {route.label_template} —"
        if any(w in utterance for w in ("show", "find", "where")):
            return f"Found it — {route.label_template} is in the {route.module} module."
        return f"Right this way — {route.label_template}."

    def extract_entity_name(
        self, context: SessionContext, fallback_to_page_context: bool = True
    ) -> str | None:
        """Public wrapper so other agents (e.g. Insight) can reuse this
        stop-word-aware extractor instead of duplicating ENTITY_STOP_WORDS.
        NOTE: for Insight this is now the DEGRADED fallback only — the
        primary path is LLMClient.extract_project_name (reads the sentence
        instead of subtracting words from it).

        `fallback_to_page_context=False` returns None instead of the page's
        project when the utterance itself names nothing — used by the
        project-context-switch check (see
        AgentOrchestrator._maybe_confirm_project_context_switch), which
        needs to know whether THIS message named a project at all, as
        distinct from silently inheriting the page."""
        return self._extract_entity_name(context, fallback_to_page_context)

    def current_project_name(self, context: SessionContext) -> str | None:
        """Public wrapper: the project of the page the user is currently on,
        if any. Used by Insight when the LLM extraction says the MESSAGE
        names no project — page context is something the utterance text
        alone can never carry."""
        return self._current_project_name(context)

    def _extract_entity_name(
        self, context: SessionContext, fallback_to_page_context: bool = True
    ) -> str | None:
        # A name spoken in the utterance always outranks the page the user
        # happens to be on — "take me to Henderson schedule" from the Buma
        # project page must resolve Henderson, not Buma.
        utterance_lower = context.utterance.lower()
        # Typo-tolerant filtering — a mistyped filler word ("shedule") must
        # still be recognized as the filler it is, not mistaken for a project
        # name just because it doesn't exactly match the dictionary spelling.
        # Surrounding punctuation is stripped per word (so "henderson?" is
        # still recognized) while inner characters like hyphens are kept —
        # they can be part of a real project name.
        words = []
        for w in utterance_lower.split():
            core = w.strip("'\".,!?;:()[]")
            if core and not self._word_matches(core, list(ENTITY_STOP_WORDS)):
                words.append(core)
        if words:
            return " ".join(words).strip()

        if not fallback_to_page_context:
            return None
        return self._current_project_name(context)

    def _current_project_name(self, context: SessionContext) -> str | None:
        if context.currentEntity and context.currentEntity.get("type") == "project":
            return context.currentEntity.get("name")
        return None

    def _current_project_id(self, context: SessionContext) -> str | None:
        """The projectId of the page the user is currently on, if any --
        mirrors AgentOrchestrator._current_project_id. Real gap this
        closes: the frontend's deriveCurrentEntity sends an id OR a name,
        never both, and prefers the id whenever one is available (i.e.
        almost always) -- _current_project_name alone was therefore almost
        never populated, and _resolve_with_entity had no other way to use
        page context at all."""
        if isinstance(context.currentEntity, dict) and context.currentEntity.get("type") == "project":
            pid = context.currentEntity.get("id")
            return str(pid) if pid else None
        return None

    def _is_already_on_page(self, route: RouteEntry, current_route: str) -> bool:
        if route.entity_required:
            base = route.pattern.split("/:")[0]
            return current_route.startswith(base)
        return current_route == route.pattern

    def _route_by_id(self, route_id: str | None) -> RouteEntry | None:
        if not route_id:
            return None
        return next((r for r in self.route_registry if r.id == route_id), None)

    def _route_option(
        self, route_id: str | None, context: SessionContext | None = None
    ) -> NavigationOption | None:
        route = self._route_by_id(route_id)
        if not route:
            return None
        # Entity routes can't be deep-linked without a project id. If the
        # request itself named a project (e.g. "go to buma invoice page"),
        # resolve it and deep-link straight there — that name is the whole
        # reason this route is even a plausible candidate, and silently
        # dropping it turned a specific request into a generic "Projects"
        # card indistinguishable from having asked for nothing at all.
        # Only a single, unambiguous match is trusted here; anything else
        # falls back to the old safe default of the projects list, where
        # the user can pick a project first.
        if route.entity_required:
            resolved = self._resolve_entity_option(route, context)
            if resolved is not None:
                return resolved
            route = self._route_by_id("customers_projects") or route
        return NavigationOption(
            label=route.label_template,
            route=route.resolved_route(),
            icon=route.icon,
            description=route.description,
            routeState=route.resolved_route_state(),
        )

    def _resolve_entity_option(
        self, route: RouteEntry, context: SessionContext | None
    ) -> NavigationOption | None:
        """Best-effort project resolution for an entity-required route being
        offered as a disambiguation OPTION (not the primary navigate path —
        see _resolve_with_entity for that). Returns None (caller falls back
        to the projects list) whenever there's no context, no named/current
        project, or the name resolves to zero or more-than-one project —
        this only fires for the clean, unambiguous case."""
        if context is None or route.entity_required != "project":
            return None
        # Real bug, fixed: this used to fall back to
        # _current_project_name(context) alone when the utterance named
        # nothing -- a field the frontend almost never sends (it prefers
        # sending an id instead). Try the exact page-context id first, same
        # priority as _resolve_with_entity now uses.
        #
        # Per explicit user direction: extraction here now calls the real
        # LLM (extract_project_name) instead of the deterministic
        # ENTITY_STOP_WORDS stripper, same reasoning as _finalize's
        # entity_required branch -- the stripper survives only as the
        # fallback for when the LLM call itself fails outright.
        extraction = self.llm_client.extract_project_name(context.utterance)
        if extraction is not None:
            entity_name = extraction.get("project_name")
        else:
            entity_name = self._extract_entity_name(context, fallback_to_page_context=False)
        entity_id = None
        if not entity_name:
            entity_id = self._current_project_id(context)
            if not entity_id:
                entity_name = self._current_project_name(context)
        if not entity_name and not entity_id:
            return None
        if entity_id:
            matches = self.entity_resolver.resolve_by_id(entity_id, context.tenantId)
        else:
            matches = self.entity_resolver.resolve_project(entity_name, context.tenantId)
        if len(matches) != 1:
            return None
        match = matches[0]
        return NavigationOption(
            label=f"{match.project_name} — {route.label_template}",
            route=self._entity_route(route, match),
            icon=route.icon,
            description=route.description,
            project=ProjectRef(id=match.project_uid, name=match.project_name, status=match.status),
            routeState=self._entity_route_state(route, match),
        )

    def _data_topic_routes(self, utterance: str) -> list[str]:
        hits: list[str] = []
        for signals, route_ids in DATA_TOPIC_ROUTES:
            if any(s in utterance for s in signals):
                hits += route_ids
        return hits

    def _clamp_confidence(self, value) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.75

    def _closest_route_suggestion(
        self, utterance: str, context: SessionContext
    ) -> NavigationSuggestion | None:
        # Real bug, fixed: short abbreviation keywords ("ar", "po", "bom")
        # are one or two edits away from ordinary English words at this
        # length, so difflib's typo-tolerant fuzzy match treated them as
        # near-identical -- "who ARE you" (a plain conversational question,
        # not a typo of anything) fuzzy-matched "ar" (Accounts Receivable)
        # at ratio 0.8, past the 0.75 cutoff, and suggested Invoices. Typo
        # tolerance only makes sense for real words long enough that a typo
        # and a coincidence are distinguishable; below this length, only an
        # EXACT match should count.
        MIN_FUZZY_KEYWORD_LEN = 4
        keyword_to_route = {
            kw: route
            for route in self.route_registry
            for kw in route.intent_keywords
        }
        exact_keywords = {kw for kw in keyword_to_route if len(kw) < MIN_FUZZY_KEYWORD_LEN}
        fuzzy_keywords = {kw for kw in keyword_to_route if len(kw) >= MIN_FUZZY_KEYWORD_LEN}
        # One permission resolution for the whole word sweep, same reason as
        # the cross-page feature sweep (see _permission_checker): this loop
        # can check up to 4 routes per word, and _has_permission re-queries
        # Mongo every time.
        permission_check = self._permission_checker(context)
        for word in utterance.split():
            if word in exact_keywords:
                route = keyword_to_route[word]
                if permission_check(route):
                    return NavigationSuggestion(
                        label=route.label_template,
                        route=route.resolved_route(),
                        routeState=route.resolved_route_state(),
                    )
                continue
            # n=3, not 1: the closest keyword match may belong to a route the
            # user has no permission for -- try the next-closest candidates
            # rather than either suggesting an inaccessible page or giving up
            # on this word entirely (see _has_permission's fix note).
            close = difflib.get_close_matches(
                word, fuzzy_keywords, n=3, cutoff=0.75
            )
            for match in close:
                route = keyword_to_route[match]
                if not permission_check(route):
                    continue
                return NavigationSuggestion(
                    label=route.label_template,
                    route=route.resolved_route(),
                    routeState=route.resolved_route_state(),
                )
        return None

    def _permission_denied_response(
        self, route: RouteEntry, permissions: set[str]
    ) -> NavigationResponse:
        accessible = [
            r for r in self.route_registry
            if (r.permission_required is None or r.permission_required in permissions)
            and r.id != route.id
            # AB#30621: create_asset carries permission_required=None (AB#28704
            # -- there's no real permission to gate it on) so it always passed
            # the accessible-candidate filter above, and its keywords ("create
            # asset", "add a new asset") share nothing but the generic verbs
            # "create"/"new"/"add" with create_bid's own keywords -- enough to
            # clear the overlap > 1 threshold below on filler alone. Confirmed
            # live: a Labor Resource denied Create Bid was told "you can see
            # your own Create Asset", even though Labor Resources have no
            # Create Asset access either, and assets belong to Customers, not
            # Labor Resources. create_asset is never a genuine standalone
            # destination anyway -- direct navigation to it always redirects
            # to create_bid (the AB#28704 special case in _check_permission
            # above) -- so it can never be a legitimate "you CAN reach this"
            # alternative here either. Exclude it outright rather than raise
            # the overlap threshold, which would also suppress real matches
            # like invoice_list <-> customer_invoices.
            and r.id != "create_asset"
        ]
        # Real gap, fixed: this used to always suggest accessible[0] (the
        # FIRST accessible route in registry order -- observed live: a
        # customer denied "Invoices" got offered "Projects", an unrelated
        # page). But denied routes very often have an accessible COUNTERPART
        # serving the same need for this role -- the admin-wide invoice_list
        # (INV.REA) vs. the customer's own customer_invoices (INV.PAY) are
        # the same concept gated by different permissions. Pick the
        # accessible route whose own vocabulary best overlaps the DENIED
        # route's (same subject), so "show me my invoices" -> denied
        # invoice_list -> offered the customer's OWN Customer Invoices page,
        # not a random accessible one. This is the same "route by what this
        # role can actually reach" principle the frontend applies when it
        # shows a customer only their own pages.
        denied_tokens = self._route_vocab_tokens(route)
        best_alt = None
        best_overlap = 0
        for cand in accessible:
            overlap = len(denied_tokens & self._route_vocab_tokens(cand))
            if overlap > best_overlap:
                best_overlap = overlap
                best_alt = cand

        suggestion = None
        message = (
            f"You don't have access to {route.label_template} yet. "
            "Ask your admin to grant you access if you need it."
        )
        if best_alt is not None and best_overlap > 1:
            # A genuinely related page this role CAN reach -- lead with it.
            # Threshold is > 1, not > 0: a single shared token is too weak a
            # signal (real gap, found live -- "Create Bid"'s own keyword "add
            # bid to cart" and "Image Upload"'s "add picture" share only the
            # generic verb "add", which made EDEN call Image Upload a
            # "genuinely related" substitute for Create Bid). Requiring 2+
            # shared tokens still passes real matches (invoice_list <->
            # customer_invoices share "invoice"+"payment") while rejecting
            # incidental verb overlap.
            suggestion = NavigationSuggestion(
                label=best_alt.label_template,
                route=best_alt.resolved_route(),
                routeState=best_alt.resolved_route_state(),
            )
            message = (
                f"You don't have access to {route.label_template}, but you can see "
                f"your own {best_alt.label_template}. Would you like to go there?"
            )
        # Real bug, fixed: an `elif accessible:` branch used to fall back to
        # suggesting accessible[0] -- the FIRST route in the entire registry
        # with permission_required is None -- whenever no genuinely related
        # page cleared the overlap bar above. That's not "somewhere related,"
        # it's registry-order noise: confirmed live, a user denied Timesheets
        # (zero vocabulary overlap with anything) got offered "Create Asset"
        # -- unrelated in every sense, purely because it happened to be the
        # first null-permission route in file order. Worse, attaching ANY
        # suggestion here makes _update_goal_state treat this as "a concrete
        # suggestion to respond to" and open a whole new multi-turn
        # clarification goal instead of returning the plain denial as a
        # single, closed answer -- the message text already reads as closed
        # ("Ask your admin..."), not a question, so leaving suggestion unset
        # when nothing is genuinely related keeps the response honest AND
        # correctly closed.
        return NavigationResponse(
            type="fallback", message=message, suggestion=suggestion
        )

    def _route_vocab_tokens(self, route: RouteEntry) -> set[str]:
        """The set of meaningful word tokens describing a route -- its label
        plus every intent keyword -- used to measure how related two routes
        are (see _permission_denied_response). Stop-words stripped so the
        overlap reflects real subject words ('invoice', 'payment') not
        filler ('the', 'my', 'for')."""
        tokens: set[str] = set()
        for phrase in [route.label_template, *route.intent_keywords]:
            for w in self._tokenize(phrase):
                if w not in ENTITY_STOP_WORDS and len(w) > 2:
                    tokens.add(w)
        return tokens
