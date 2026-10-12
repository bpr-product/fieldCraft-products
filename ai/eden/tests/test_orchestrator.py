"""Unit tests for AgentOrchestrator — deterministic backstops (greeting,
feature lookup) that must resolve without ever calling the LLM, plus the
LLM-triage branch when nothing deterministic fits (this now includes
page-orientation "guidance" and "data_question" questions -- see
test_guidance_question_is_llm_classified and
test_data_question_is_llm_classified_and_offers_bubbles)."""
from unittest.mock import MagicMock, patch

import pytest

import source.orchestrator.agent_orchestrator as agent_orchestrator_module
from source.orchestrator.agent_orchestrator import AgentOrchestrator


@pytest.fixture
def orchestrator(monkeypatch):
    monkeypatch.setenv("SKIP_RBAC_CHECK", "true")
    orch = AgentOrchestrator()
    orch.registry["navigation"].entity_resolver = MagicMock()
    orch.registry["navigation"].entity_resolver.resolve_project.return_value = []
    orch.registry["navigation"].entity_resolver.resolve_by_id.return_value = []
    # See test_eden_graph's fixture: None = "no real project name in the
    # utterance", so the data-grounded recovery never hijacks fallback tests.
    orch.registry["navigation"].entity_resolver.find_named_project_in_text.return_value = None
    # Same "unconfigured MagicMock is truthy" gap as find_named_project_in_text
    # above, for the order-number half of the same recovery step.
    orch.registry["navigation"].entity_resolver.find_ordered_project_in_text.return_value = None
    orch.llm = MagicMock()
    orch.registry["navigation"].llm_client = orch.llm
    # eden_router calls triage() on every request (the primary routing
    # mechanism); these tests exercise Navigation's internal tiers, so the
    # stub sends everything there. Tests that need Insight routing override
    # this per-test.
    orch.llm.triage.return_value = {"agent": "navigation"}
    # Insight's LLM entity extraction: None = "call failed", which degrades
    # to nav.extract_entity_name -- the path existing tests already stub.
    orch.llm.extract_project_name.return_value = None
    # Same "unconfigured MagicMock is truthy" gap as extract_project_name --
    # ReportingAgent/InsightAgent now try LLM message composition first
    # (see LLMClient.compose_billing_message/compose_variance_message);
    # None here means "composition unavailable," degrading to the
    # deterministic template these tests actually exercise.
    orch.llm.compose_billing_message.return_value = None
    orch.llm.compose_variance_message.return_value = None
    # Same gap again -- data_question_response now tries
    # LLMClient.suggest_related_routes first (replaces the DATA_TOPIC_ROUTES
    # keyword table); None means "call failed," degrading to that
    # deterministic table, which is what these tests actually exercise.
    orch.llm.suggest_related_routes.return_value = None
    # Role shaping: default to an internal user so Insight tests exercise
    # Insight itself; customer-view tests flip this per-test.
    orch.role_resolver = MagicMock()
    orch.role_resolver.is_customer.return_value = False
    orch.role_resolver.get_project_ids.return_value = []
    return orch


def test_resolve_insight_entity_recovers_bare_order_number_llm_missed(orchestrator, ctx):
    # Real bug, fixed: confirmed live -- "what is the project cost for
    # 682053" (a bare order number, no "order"/"#" prefix) got a null
    # extraction from the LLM despite the prompt explicitly covering this
    # case, even though the real project resolves instantly by a direct
    # Mongo lookup -- a small-model instruction-following miss, not a data
    # gap. find_ordered_project_in_text is the ground-truth recovery,
    # checked after find_named_project_in_text (names) comes back empty.
    nav = orchestrator.registry["navigation"]
    nav.entity_resolver.find_named_project_in_text.return_value = None
    nav.entity_resolver.find_ordered_project_in_text.return_value = "682053"
    entity_name, entity_id = orchestrator._resolve_insight_entity(
        ctx("what is the project cost for 682053"), nav,
        extraction={"project_name": None},
    )
    assert entity_name == "682053"
    assert entity_id is None
    nav.entity_resolver.find_ordered_project_in_text.assert_called_once()


def test_resolve_insight_entity_skips_order_recovery_when_name_already_found(orchestrator, ctx):
    # The order-number recovery must never run once name recovery already
    # found something -- an LLM name-extraction miss recovered by
    # find_named_project_in_text should win outright, same priority a
    # successful LLM extraction would have had.
    nav = orchestrator.registry["navigation"]
    nav.entity_resolver.find_named_project_in_text.return_value = "Henderson Roofing"
    nav.entity_resolver.find_ordered_project_in_text.return_value = None
    entity_name, entity_id = orchestrator._resolve_insight_entity(
        ctx("why is henderson roofing over budget"), nav,
        extraction={"project_name": None},
    )
    assert entity_name == "Henderson Roofing"
    nav.entity_resolver.find_ordered_project_in_text.assert_not_called()


def test_resolve_insight_entity_resolves_clicked_option_card_by_order_number(orchestrator, ctx):
    # Real bug, fixed: confirmed live in prod -- clicking a project option
    # card re-sends that card's own label verbatim, e.g. "sonny hayes,
    # Madison-Roof Enhancement-334499 (Order #334499)" (EDEN's own
    # f"{project_name} (Order #{order_no})" format from InsightAgent/
    # NavigationAgent's disambiguation-list builder). Handing that whole
    # noisy string to extract_project_name got a wrong/no name back with no
    # further recovery tried, so _insight_node's `response is None` branch
    # re-ran the SAME "which project?" disambiguation -- from the user's
    # side, clicking an option just re-listed the projects again. The
    # trailing "(Order #NNNN)" is a string EDEN itself generated, so it must
    # resolve directly, without ever reaching the LLM.
    nav = orchestrator.registry["navigation"]
    entity_name, entity_id = orchestrator._resolve_insight_entity(
        ctx("sonny hayes, Madison-Roof Enhancement-334499 (Order #334499)"),
        nav, goal_continued=True,
    )
    assert entity_name == "334499"
    assert entity_id is None
    orchestrator.llm.extract_project_name.assert_not_called()
    nav.entity_resolver.find_named_project_in_text.assert_not_called()
    nav.entity_resolver.find_ordered_project_in_text.assert_not_called()


def test_resolve_insight_entity_option_card_regex_ignores_non_trailing_order_mentions(orchestrator, ctx):
    # The pattern is anchored at end-of-string on purpose: an order number
    # mentioned mid-sentence ("is order #334499 related to the Madison job?")
    # is a genuinely free-text question, not an echoed option label, and must
    # still go through normal extraction.
    nav = orchestrator.registry["navigation"]
    orchestrator.llm.extract_project_name.return_value = {"project_name": None}
    nav.entity_resolver.find_named_project_in_text.return_value = None
    nav.entity_resolver.find_ordered_project_in_text.return_value = "334499"
    entity_name, entity_id = orchestrator._resolve_insight_entity(
        ctx("is order #334499 related to the Madison job?"), nav, goal_continued=True,
    )
    assert entity_name == "334499"
    orchestrator.llm.extract_project_name.assert_called_once()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_greeting_never_calls_llm(mock_audit, orchestrator, ctx):
    response = orchestrator.dispatch(ctx("hello there"))
    assert response.type == "guidance"
    orchestrator.llm.understand.assert_not_called()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_thanks_is_treated_as_greeting(mock_audit, orchestrator, ctx):
    response = orchestrator.dispatch(ctx("thanks so much"))
    assert response.type == "guidance"
    orchestrator.llm.understand.assert_not_called()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_audit_log_records_platform_from_context(mock_audit, orchestrator, ctx):
    # Real gap, fixed: platform wasn't recorded anywhere durable (not the
    # nav_audit log line, not the agent_audit Mongo doc), so a live mobile
    # vs. web request could never be told apart after the fact -- see
    # eden_rbac_scoping_lookslike_bug_20260903 memory / CLAUDE.md gotcha.
    orchestrator.dispatch(ctx("hey take me to projects", platform="mobile"))
    assert mock_audit.call_args.kwargs["platform"] == "mobile"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_audit_log_records_platform_none_for_web(mock_audit, orchestrator, ctx):
    orchestrator.dispatch(ctx("hey take me to projects"))
    assert mock_audit.call_args.kwargs["platform"] is None


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_greeting_word_combined_with_navigation_still_navigates(mock_audit, orchestrator, ctx):
    # "hey take me to projects" must navigate, not just wave hello.
    response = orchestrator.dispatch(ctx("hey take me to projects"))
    assert response.type == "navigate"
    assert response.route == "/customers-projects"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_guidance_question_is_llm_classified(mock_audit, orchestrator, ctx):
    # Page-orientation questions ("what can I do here") no longer have a
    # deterministic GUIDANCE_SIGNALS tier -- LLMClient.understand()'s own
    # intent="guidance" classification (see _dispatch_with_llm) handles them,
    # same as data_question/navigate/out_of_scope.
    orchestrator.llm.understand.return_value = {
        "intent": "guidance", "route_id": None, "confidence": 0.9,
        "insight": "Invoices live here.",
    }
    response = orchestrator.dispatch(ctx("what can i do here", currentRoute="/soldproduction/projectinformation/invoiceview"))
    assert response.type == "guidance"
    orchestrator.llm.understand.assert_called_once()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_feature_question_never_calls_llm(mock_audit, orchestrator, ctx):
    response = orchestrator.dispatch(
        ctx("where is the export timesheet button", currentRoute="/timesheets")
    )
    assert response.highlight == "Export Timelog button"
    orchestrator.llm.understand.assert_not_called()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_data_question_is_llm_classified_and_offers_bubbles(mock_audit, orchestrator, ctx):
    # DATA_QUESTION_SIGNALS keyword tier removed -- LLMClient.understand()'s
    # own intent="data_question" classification (see _dispatch_with_llm)
    # handles this, same as guidance/navigate/out_of_scope.
    orchestrator.llm.understand.return_value = {
        "intent": "data_question", "route_id": None, "confidence": 0.8,
        "insight": "I can't compute that yet.",
    }
    response = orchestrator.dispatch(ctx("what is the margin on this job"))
    assert response.type == "fallback"
    assert response.options
    orchestrator.llm.understand.assert_called_once()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_keyword_navigation_never_calls_llm(mock_audit, orchestrator, ctx):
    response = orchestrator.dispatch(ctx("take me to projects"))
    assert response.type == "navigate"
    orchestrator.llm.understand.assert_not_called()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_fuzzy_utterance_engages_llm(mock_audit, orchestrator, ctx):
    orchestrator.llm.understand.return_value = {
        "intent": "navigate", "route_id": "vendor_list", "confidence": 0.85,
        "extracted_entity": None, "insight": "Approved vendors.",
    }
    response = orchestrator.dispatch(ctx("who installed the roof shingles"))
    orchestrator.llm.understand.assert_called_once()
    assert response.type == "navigate"
    # vendor_list renders inside the shared Admin tab shell (target_route=
    # /admin, selectedTab "2") rather than its own bare pattern — same as
    # Materials/Tools/Resources/Inventory and (as of this fix) Customers/
    # Employees, so EDEN's navigation matches what a user reaches by
    # clicking through the Admin tab bar manually.
    assert response.route == "/admin"
    assert response.routeState == {"selectedTab": "2"}


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_low_confidence_navigate_offers_disambiguation(mock_audit, orchestrator, ctx):
    # "who installed the roof shingles" has no exact keyword match, so it
    # reaches the LLM tier. A low confidence here means the model itself is
    # unsure among the RAG candidates — ask, don't silently commit. Retrieval
    # is mocked so the test doesn't depend on the live embeddings service
    # actually surfacing two close candidates for this phrase.
    nav = orchestrator.registry["navigation"]
    vendor_list = nav.route_by_pattern("/vendorlist")
    subcontractor = nav.route_by_pattern("/vendormanagement")
    with patch.object(nav, "top_candidates", return_value=[
        (vendor_list, 0.5), (subcontractor, 0.45),
    ]):
        orchestrator.llm.understand.return_value = {
            "intent": "navigate", "route_id": "vendor_list", "confidence": 0.55,
            "extracted_entity": None, "insight": "Approved vendors.",
        }
        response = orchestrator.dispatch(ctx("who installed the roof shingles"))
    assert response.type == "disambiguation"
    assert response.question
    assert len(response.options) >= 2
    # vendor_list's option now points through /admin (selectedTab "2"), same
    # target_route resolution as the "navigate" path — see
    # test_fuzzy_utterance_engages_llm.
    assert any(o.route == "/admin" and o.routeState == {"selectedTab": "2"} for o in response.options)


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_mobile_disambiguation_still_opens_a_pending_goal(mock_audit, orchestrator, ctx):
    # Real bug, fixed: confirmed live on mobile (2026-09-03) --
    # _strip_route_fields_for_mobile used to run BEFORE _update_goal_state,
    # which unconditionally rewrites response.type to "guidance" and nulls
    # response.options for every mobile response. _update_goal_state's own
    # logic keys off exactly those two fields, so on mobile a
    # disambiguation could never open a pending goal at all -- the next
    # message (the user picking one of the listed options) had nothing
    # remembered to recognize it as the answer, and fell through to a
    # fresh, context-free triage that (confirmed live) got misclassified
    # as a navigation request to an unrelated page. Goal bookkeeping must
    # see the ORIGINAL, unstripped response, independent of the mobile
    # transport rewrite applied to the OUTGOING response.
    orchestrator.conversation_memory = MagicMock()
    orchestrator.conversation_memory.get_recent_turns.return_value = []
    orchestrator.conversation_memory.get_pending_goal.return_value = None
    nav = orchestrator.registry["navigation"]
    vendor_list = nav.route_by_pattern("/vendorlist")
    subcontractor = nav.route_by_pattern("/vendormanagement")
    with patch.object(nav, "top_candidates", return_value=[
        (vendor_list, 0.5), (subcontractor, 0.45),
    ]):
        orchestrator.llm.understand.return_value = {
            "intent": "navigate", "route_id": "vendor_list", "confidence": 0.55,
            "extracted_entity": None, "insight": "Approved vendors.",
        }
        response = orchestrator.dispatch(ctx("who installed the roof shingles", platform="mobile"))
    # The OUTGOING response is still correctly mobile-shaped...
    assert response.type == "guidance"
    assert response.options is None
    # ...but the pending goal was opened from the real, pre-strip shape,
    # with the real offered labels -- not skipped, and not empty.
    orchestrator.conversation_memory.set_pending_goal.assert_called_once()
    goal = orchestrator.conversation_memory.set_pending_goal.call_args.args[2]
    assert len(goal.get("offered") or []) >= 2


def test_disambiguation_names_a_feature_shared_by_both_candidates(orchestrator, ctx):
    # Real gap, closed: confirmed live -- Leads & Customers' and Admin >
    # Customers' "Invite Customer" buttons both open the exact same
    # CreateCustomer form/backend call (confirmed against the frontend
    # source), so the generic "which did you mean?" reads as if the two
    # options are different actions and picking wrong loses something.
    # When the utterance matches a same-named page_feature on both
    # candidates (fully data-driven off route_registry.json, same
    # find_topic_feature matching used elsewhere -- not hardcoded to
    # "invite customer" specifically), the question should say so
    # honestly instead of the generic one. Tests _disambiguation_response
    # directly rather than through dispatch() -- "invite a customer" has
    # a strong exact-keyword match that resolves deterministically before
    # ever reaching the LLM/retrieval tier this method is normally called
    # from, so exercising it through dispatch would test the wrong thing.
    nav = orchestrator.registry["navigation"]
    response = orchestrator._disambiguation_response(
        ["vendor_list", "vendor_management"], nav,
        ctx("i want to add a vendor"),
    )
    assert response is not None
    assert response.type == "disambiguation"
    assert "add new vendor" in response.question.lower()
    assert "works the same" in response.question.lower()
    assert len(response.options) == 2
    assert {o.label for o in response.options} == {"Vendors", "Vendor Management"}
    # Real bug, fixed: confirmed live -- picking either option here used to
    # land on a bare page with no spotlight and no auto-click, even though
    # the same utterance would have triggered a highlighted, auto-clicked
    # navigate had only one route matched instead of two.
    for option in response.options:
        assert option.autoClick is True
        assert option.highlightTargets


def test_disambiguation_never_autoclicks_for_where_phrasing(orchestrator, ctx):
    # "Where" phrasing is asking to be SHOWN something, not have it clicked
    # for them -- same rule try_feature_lookup_scored's own tie branch
    # already applies (see is_informational_lookup).
    nav = orchestrator.registry["navigation"]
    response = orchestrator._disambiguation_response(
        ["vendor_list", "vendor_management"], nav,
        ctx("where can i add a vendor"),
    )
    assert response is not None
    for option in response.options:
        assert option.autoClick is False
        assert option.highlightTargets is None


def test_disambiguation_uses_generic_question_when_no_feature_is_shared(orchestrator, ctx):
    # Sanity check for the same code path: two candidates that DON'T share
    # a matching page_feature for this utterance must keep the plain
    # "which did you mean?" question, not falsely claim a shared feature.
    nav = orchestrator.registry["navigation"]
    response = orchestrator._disambiguation_response(
        ["vendor_list", "vendor_management"], nav,
        ctx("who installed the roof shingles"),
    )
    assert response is not None
    assert response.type == "disambiguation"
    assert response.question == "A few pages match — which did you mean?"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_high_confidence_navigate_skips_disambiguation(mock_audit, orchestrator, ctx):
    orchestrator.llm.understand.return_value = {
        "intent": "navigate", "route_id": "vendor_list", "confidence": 0.9,
        "extracted_entity": None, "insight": "Approved vendors.",
    }
    response = orchestrator.dispatch(ctx("who installed the roof shingles"))
    assert response.type == "navigate"
    assert response.route == "/admin"
    assert response.routeState == {"selectedTab": "2"}


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_llm_unavailable_falls_back_to_no_match(mock_audit, orchestrator, ctx):
    orchestrator.llm.understand.return_value = None
    response = orchestrator.dispatch(ctx("who installed the roof shingles"))
    assert response.type == "fallback"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_llm_out_of_scope_gets_polite_redirect(mock_audit, orchestrator, ctx):
    orchestrator.llm.understand.return_value = {
        "intent": "out_of_scope", "route_id": None, "confidence": 0.9,
        "insight": "I can only help with FIELDCRAFT navigation.",
    }
    # Avoid "what's the"/"how much" phrasing — those trip the deterministic
    # data-question guard before the LLM is ever consulted (tested separately).
    response = orchestrator.dispatch(ctx("tell me a joke"))
    assert response.type == "fallback"
    assert "navigation" in response.insight.lower() or "FIELDCRAFT" in response.message


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_llm_data_question_with_route_navigates_there(mock_audit, orchestrator, ctx):
    # The model tagged it data_question but also picked the page where the
    # answer lives — going there IS the answer (the model's honest insight
    # rides along as the caption), not an "I can't crunch that" bubble that
    # just offers the same page as a button.
    #
    # Uses customer_info_dashboard (flat, no entity required) rather than
    # job_ledger -- job_ledger is entity_required now (its own standalone
    # page is unconditionally broken with no project id; see
    # NavigationAgent.ENTITY_TAB_ALIAS_ROUTE_STATE), so it no longer
    # navigates directly without a resolved project, which isn't what this
    # test is exercising (the generic "data_question + route_id navigates"
    # mechanic, not job_ledger specifically). invoice_list (the previous
    # flat stand-in here) was removed entirely -- its own frontend page
    # crashed on load regardless of which tier navigated there.
    # Real bug, fixed (PR review, Raja): "how's the billing looking" used to
    # be a safe generic-navigate example utterance, but project_invoice_view
    # now genuinely owns the bare word "billing" in its intent_keywords (the
    # fix for the invoice/billing keyword-tier regression this same review
    # flagged) -- it intercepted this utterance on the deterministic tier
    # BEFORE the mocked understand() call below was ever reached, so this
    # test was actually exercising the wrong code path. Swapped to a phrase
    # with zero keyword-tier collisions so the mocked LLM path is genuinely
    # what's under test here.
    orchestrator.llm.understand.return_value = {
        "intent": "data_question", "route_id": "question_builder", "confidence": 0.8,
        "insight": "Here's where that lives.",
    }
    response = orchestrator.dispatch(ctx("give me an update on this"))
    assert response.type == "navigate"
    assert response.route == "/questionBuilder"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_llm_data_question_without_route_offers_fallback(mock_audit, orchestrator, ctx):
    orchestrator.llm.understand.return_value = {
        "intent": "data_question", "route_id": None, "confidence": 0.8,
        "insight": "I can't compute that yet.",
    }
    response = orchestrator.dispatch(ctx("how's the job costing looking"))
    assert response.type == "fallback"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_audit_log_called_for_every_dispatch(mock_audit, orchestrator, ctx):
    orchestrator.dispatch(ctx("take me to invoices"))
    mock_audit.assert_called_once()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_affirmation_after_navigate_acknowledges_not_renavigates(mock_audit, orchestrator, ctx):
    # "okay" right after a successful navigation is an acknowledgment —
    # re-navigating just produced a confusing "you're already on X".
    orchestrator.conversation_memory = MagicMock()
    orchestrator.conversation_memory.get_recent_turns.return_value = [
        {"utterance": "take me to invoices", "type": "navigate",
         "route": "/invoice", "label": "Invoices"},
    ]
    response = orchestrator.dispatch(ctx("okay"))
    assert response.type == "guidance"
    orchestrator.llm.understand.assert_not_called()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_affirmation_after_suggestion_navigates_there(mock_audit, orchestrator, ctx):
    # "yes" after "Did you mean Projects?" accepts the offer.
    orchestrator.conversation_memory = MagicMock()
    orchestrator.conversation_memory.get_recent_turns.return_value = [
        {"utterance": "show projcts", "type": "fallback",
         "route": "/customers-projects", "label": "Customers & Projects"},
    ]
    response = orchestrator.dispatch(ctx("yes"))
    assert response.type == "navigate"
    assert response.route == "/customers-projects"
    orchestrator.llm.understand.assert_not_called()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_affirmation_after_disambiguation_reoffers_options(mock_audit, orchestrator, ctx):
    # A bare "yes" after "which did you mean?" names no option — re-ask with
    # the same choices instead of guessing or falling through to the LLM.
    orchestrator.conversation_memory = MagicMock()
    orchestrator.conversation_memory.get_recent_turns.return_value = [
        {"utterance": "take me to finance", "type": "disambiguation",
         "route": None, "label": None,
         "options": [{"label": "Invoices", "route": "/invoice"},
                     {"label": "Job Ledger", "route": "/jobLedger"}]},
    ]
    response = orchestrator.dispatch(ctx("yes"))
    assert response.type == "disambiguation"
    assert len(response.options) == 2
    orchestrator.llm.understand.assert_not_called()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_affirmation_with_no_history_gets_honest_fallback_not_llm_guess(mock_audit, orchestrator, ctx):
    # Real bug, fixed: confirmed live -- a bare "yes" with nothing remembered
    # at all used to fall through to the generic LLM/retrieval navigate path,
    # which built candidates from noise (the word "yes" itself) yet still
    # returned a confidently-navigated response to an unrelated page. An
    # affirmation-shaped utterance with nothing to confirm must say so
    # honestly instead, and never reach the LLM.
    orchestrator.conversation_memory = MagicMock()
    orchestrator.conversation_memory.get_recent_turns.return_value = []
    response = orchestrator.dispatch(ctx("yes"))
    assert response.type == "fallback"
    orchestrator.llm.understand.assert_not_called()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_affirmation_after_routeless_fallback_gets_honest_fallback_not_llm_guess(mock_audit, orchestrator, ctx):
    # Real bug, fixed: confirmed live on fieldcraftmind (2026-09-02) -- "let me add
    # a tool" for a user without Tool permission correctly got blocked (a
    # fallback turn with no route). The user then said "yes", which used to
    # fall through to the LLM/retrieval path with nothing to go on, and the
    # LLM confidently navigated them to an unrelated page ("Projects")
    # anyway -- the model's own self-reported confidence (0.9) skipped the
    # low-confidence disambiguation gate even though nothing genuinely
    # matched (top retrieval score 0.163, well below threshold). A bare
    # "yes" after a routeless fallback has nothing to confirm and must say
    # so, not guess.
    orchestrator.conversation_memory = MagicMock()
    orchestrator.conversation_memory.get_recent_turns.return_value = [
        {"utterance": "let me add a tool", "type": "fallback",
         "route": None, "label": None},
    ]
    response = orchestrator.dispatch(ctx("yes"))
    assert response.type == "fallback"
    orchestrator.llm.understand.assert_not_called()


# Real gap, fixed by REMOVAL: invoice_list ("/invoice") used to be tested
# here across 4 scenarios (project-scoped alias, no-context redirect,
# permission-denied fallback, last-discussed-project fallback) -- all
# specific to a redirect-away-from-the-crash mechanism that only covered
# the exact-keyword tier and left the retrieval/LLM tier still able to
# reach the confirmed-broken frontend page. invoice_list was removed from
# route_registry.json entirely instead (closing every tier at once), which
# retired that whole redirect mechanism along with it -- there is no
# longer a deterministic "flat invoice route" scenario left to test; a
# bare "take me to the invoice" with no other signal now falls through to
# the ordinary retrieval/LLM path like any other unmatched phrase, same as
# every other removed/never-added route.


# ── Multi-step reasoning trial (MULTI_STEP_VERIFICATION) ───────────────────
#
# Reproduces the real, observed case: "where i can see the customer
# information" retrieved customer_table (0.301) and customer_info_dashboard
# (0.276) — an 8% relative gap, well inside NEAR_TIE_MARGIN_RATIO (0.15) —
# yet the primary LLM call returned confidence 0.95 for customer_table
# alone, so the confidence-based disambiguation check never fired.

@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_multi_step_verification_disabled_is_inert(mock_audit, orchestrator, ctx, monkeypatch):
    # Explicitly force the flag off for this test rather than asserting on
    # whatever a developer's local .env currently has MULTI_STEP_VERIFICATION
    # set to — a real near-tie must produce zero second-opinion calls when
    # the trial is disabled, regardless of ambient env state.
    monkeypatch.setattr(agent_orchestrator_module, "MULTI_STEP_VERIFICATION_ENABLED", False)
    # Empty, mocked memory -- deterministic regardless of real Redis state
    # (this fixture's tenant/user id is shared across the whole test module,
    # and _route_confirmed_by_context now reads recent_turns).
    orchestrator.conversation_memory = _memory_mock()

    nav = orchestrator.registry["navigation"]
    customer_table = nav.route_by_pattern("/customertable")
    customer_info = nav.route_by_pattern("/CustomerInfoDashboard")
    with patch.object(nav, "top_candidates", return_value=[
        (customer_table, 0.301), (customer_info, 0.276),
    ]):
        orchestrator.llm.understand.return_value = {
            "intent": "navigate", "route_id": "customer_table", "confidence": 0.95,
            "extracted_entity": None, "insight": "Here are the customers.",
        }
        response = orchestrator.dispatch(ctx("where i can see the customer information"))
    assert response.type == "navigate"
    orchestrator.llm.verify_ambiguity.assert_not_called()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_multi_step_verification_overrides_confident_near_tie(mock_audit, orchestrator, ctx, monkeypatch):
    monkeypatch.setattr(agent_orchestrator_module, "MULTI_STEP_VERIFICATION_ENABLED", True)
    # Empty, mocked memory -- see comment in test_multi_step_verification_
    # disabled_is_inert above; this test specifically needs NO prior turn to
    # already "confirm" customer_table, or _route_confirmed_by_context would
    # correctly skip verification before it ever gets a chance to fire.
    orchestrator.conversation_memory = _memory_mock()
    nav = orchestrator.registry["navigation"]
    customer_table = nav.route_by_pattern("/customertable")
    customer_info = nav.route_by_pattern("/CustomerInfoDashboard")
    with patch.object(nav, "top_candidates", return_value=[
        (customer_table, 0.301), (customer_info, 0.276),
    ]):
        orchestrator.llm.understand.return_value = {
            "intent": "navigate", "route_id": "customer_table", "confidence": 0.95,
            "extracted_entity": None, "insight": "Here are the customers.",
        }
        orchestrator.llm.verify_ambiguity.return_value = {
            "is_ambiguous": True,
            "plausible_route_ids": ["customer_table", "customer_info_dashboard"],
            "reasoning": "Both pages could answer 'customer information'.",
        }
        response = orchestrator.dispatch(ctx("where i can see the customer information"))
    orchestrator.llm.verify_ambiguity.assert_called_once()
    assert response.type == "disambiguation"
    assert len(response.options) == 2


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_multi_step_verification_skips_when_not_near_tied(mock_audit, orchestrator, ctx, monkeypatch):
    monkeypatch.setattr(agent_orchestrator_module, "MULTI_STEP_VERIFICATION_ENABLED", True)
    orchestrator.conversation_memory = _memory_mock()
    nav = orchestrator.registry["navigation"]
    # question_builder stands in for the removed invoice_list/
    # invoice_payment here -- flat/no-entity/no-permission and genuinely
    # generic (no external-link dependency), so this test's own mechanic
    # (near-tie detection, not invoice-specific) is unaffected.
    #
    # Real bug, fixed (PR review, Raja): the utterance itself must not
    # collide with the deterministic keyword tier -- "show me the invoices"
    # now genuinely resolves via project_invoice_view's own intent_keywords
    # (the fix for the invoice/billing keyword-tier regression this same
    # review flagged), which would intercept it before dispatch() ever
    # reaches the mocked top_candidates/understand() below.
    dashboard = nav.route_by_pattern("/questionBuilder")
    vendor_list = nav.route_by_pattern("/vendorlist")
    with patch.object(nav, "top_candidates", return_value=[
        (dashboard, 0.9), (vendor_list, 0.2),  # clear front-runner, not a near-tie
    ]):
        orchestrator.llm.understand.return_value = {
            "intent": "navigate", "route_id": "question_builder", "confidence": 0.95,
            "extracted_entity": None, "insight": "Here's where that lives.",
        }
        response = orchestrator.dispatch(ctx("give me an update on this"))
    orchestrator.llm.verify_ambiguity.assert_not_called()
    assert response.type == "navigate"


# ── Conversation loop (pending navigation goal) ────────────────────────────


def _memory_mock(recent_turns=None, pending_goal=None, last_project=None):
    mem = MagicMock()
    mem.get_recent_turns.return_value = recent_turns or []
    mem.get_pending_goal.return_value = pending_goal
    mem.get_last_project.return_value = last_project
    return mem


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_negation_after_navigate_offers_alternatives(mock_audit, orchestrator, ctx):
    # "no not that one" after being taken to Customers must reject it and
    # come back with the next-best pages as clickable cards — deterministic,
    # no LLM call.
    orchestrator.conversation_memory = _memory_mock(recent_turns=[
        {"utterance": "where i can see the customer information",
         "type": "navigate", "route": "/admin", "label": "Customers"},
    ])
    nav = orchestrator.registry["navigation"]
    customer_table = nav.route_by_pattern("/customertable")   # resolves to /admin
    customer_info = nav.route_by_pattern("/CustomerInfoDashboard")
    leads_customers = nav.route_by_pattern("/leadscustomers")
    with patch.object(nav, "top_candidates", return_value=[
        (customer_table, 0.30), (customer_info, 0.28), (leads_customers, 0.25),
    ]):
        response = orchestrator.dispatch(ctx("no not that one"))
    assert response.type == "disambiguation"
    # The rejected page (Customers -> /admin) must not be re-offered.
    assert all(o.route != "/admin" for o in response.options)
    assert len(response.options) == 2
    orchestrator.llm.understand.assert_not_called()
    # The rejection was recorded against the original request.
    goal = orchestrator.conversation_memory.set_pending_goal.call_args.args[2]
    assert goal["original_utterance"] == "where i can see the customer information"
    assert "/admin" in goal["rejected_routes"]


def _hijack_goal():
    """A real pending goal captured from the dev logs — Reporting couldn't
    answer "what are the leads we have" and offered page bubbles."""
    return {
        "original_utterance": "what are the leads we have",
        "last_question": "I couldn't pull that up directly. But I can take you to where you'll find it:",
        "attempts": 1,
        "rejected_routes": [],
        "kind": "reporting_lookup",
        "offered": ["Customer Info Dashboard", "Invoices", "Projects"],
    }


def test_stale_goal_does_not_hijack_an_unrelated_new_question(orchestrator, ctx):
    # THE bug this whole mechanism exists to fix, reproduced from live dev
    # logs: a pending goal for "what are the leads we have" swallowed the
    # unrelated "i want to see the payment details of customers" and
    # answered neither. eden_router used to hand ANY message to the goal's
    # owning agent with no check at all.
    orchestrator.conversation_memory = _memory_mock(pending_goal=_hijack_goal())
    orchestrator.llm.classify_goal_continuation.return_value = {
        "is_reply": False, "confidence": 0.9, "reasoning": "different topic",
    }
    decision = orchestrator._eden_router_node(
        {"context": ctx("i want to see the payment ddetails of custoemrs")}
    )
    # Routed by fresh triage (the fixture stubs triage -> navigation), NOT
    # blindly to reporting because that's who owned the stale goal.
    assert decision["next_agent"] == "navigation"
    # And the stale goal is dropped so no downstream consumer still sees it.
    orchestrator.conversation_memory.clear_pending_goal.assert_called_once()


def test_genuine_reply_still_continues_its_goal(orchestrator, ctx):
    # The other side of the same coin: picking an offered option must still
    # go back to the agent that asked, with the goal intact.
    orchestrator.conversation_memory = _memory_mock(pending_goal=_hijack_goal())
    orchestrator.llm.classify_goal_continuation.return_value = {
        "is_reply": True, "confidence": 0.9, "reasoning": "picks an option",
    }
    decision = orchestrator._eden_router_node({"context": ctx("Invoices")})
    assert decision["next_agent"] == "reporting"
    orchestrator.conversation_memory.clear_pending_goal.assert_not_called()


def _navigation_clarify_goal():
    """Navigation's own multi-turn "which page did you mean?" goal (opened
    by _update_goal_state via _run_navigation_cascade's final _finalize_turn
    call, goal_kind="navigation_clarify")."""
    return {
        "original_utterance": "show me the materials",
        "last_question": "Which page did you mean?",
        "attempts": 1,
        "rejected_routes": [],
        "kind": "navigation_clarify",
        "offered": ["Material Management", "Timesheets"],
    }


def test_navigation_clarify_kind_is_registered_in_goal_kind_agent():
    # Real bug, fixed: this goal kind used to have no entry at all in
    # GOAL_KIND_AGENT, so _eden_router_node's relatedness check
    # (GOAL_KIND_AGENT.get(kind) is None -> skip _is_goal_reply entirely)
    # silently never ran for it.
    assert agent_orchestrator_module.GOAL_KIND_AGENT["navigation_clarify"] == "navigation"


def test_stale_navigation_clarify_goal_does_not_hijack_an_unrelated_new_question(orchestrator, ctx):
    # THE bug reproduced live (fieldcraftmind, 2026-09-01): a pending navigation
    # goal for "show me the materials" swallowed the unrelated, unambiguous
    # "take me to the timelog" into _dispatch_with_llm's combined-text
    # retrieval instead of being read as its own fresh request. Now that
    # this goal kind is tagged and registered, the same relatedness check
    # every other goal kind gets must apply here too.
    orchestrator.conversation_memory = _memory_mock(pending_goal=_navigation_clarify_goal())
    orchestrator.llm.classify_goal_continuation.return_value = {
        "is_reply": False, "confidence": 0.9, "reasoning": "different topic",
    }
    decision = orchestrator._eden_router_node(
        {"context": ctx("take me to the timelog")}
    )
    assert decision["next_agent"] == "navigation"
    orchestrator.conversation_memory.clear_pending_goal.assert_called_once()


def test_genuine_reply_still_continues_navigation_clarify_goal(orchestrator, ctx):
    orchestrator.conversation_memory = _memory_mock(pending_goal=_navigation_clarify_goal())
    orchestrator.llm.classify_goal_continuation.return_value = {
        "is_reply": True, "confidence": 0.9, "reasoning": "picks an option",
    }
    decision = orchestrator._eden_router_node({"context": ctx("Timesheets")})
    assert decision["next_agent"] == "navigation"
    orchestrator.conversation_memory.clear_pending_goal.assert_not_called()


def test_navigation_command_is_never_read_as_an_option_pick(orchestrator, ctx):
    # Deterministic pre-check, ahead of the LLM: measured live, Qwen3-8B is
    # 0.95-confident that "take me to invoices" PICKS an offered "Invoices"
    # option. Understandable, but the goal's owner is Reporting, which
    # cannot navigate -- so that reading strands the user. The LLM stub here
    # returns the wrong answer on purpose to prove the pre-check wins.
    orchestrator.conversation_memory = _memory_mock(pending_goal=_hijack_goal())
    orchestrator.llm.classify_goal_continuation.return_value = {
        "is_reply": True, "confidence": 0.95, "reasoning": "matches an offered option",
    }
    assert orchestrator._is_goal_reply(ctx("take me to invoices"), _hijack_goal()) is False
    assert orchestrator._is_goal_reply(ctx("show me the customer dashboard"), _hijack_goal()) is False
    # ...but a bare option name is still a pick, and the LLM still decides that.
    assert orchestrator._is_goal_reply(ctx("Invoices"), _hijack_goal()) is True


def _explicit_switch_goal():
    """A real pending goal shape from _maybe_confirm_explicit_project_switch
    -- "did you mean Testdelores, or Riverside?" still awaiting a reply."""
    return {
        "kind": "reporting_explicit_switch",
        "original_utterance": "what is the invoice amount",
        "attempts": 1,
        "rejected_routes": [],
        "last_question": "You're currently on Riverside's page — did you mean Testdelores, or Riverside?",
        "agent_kind": "reporting",
        "route_id": None,
        "named_project_id": "proj-testdelores",
        "named_project_name": "Testdelores",
        "page_project_id": "proj-riverside",
        "page_project_name": "Riverside",
        "named_response": {
            "type": "guidance", "message": "Testdelores: $1,200 outstanding.",
        },
    }


def test_ambiguous_reply_to_explicit_switch_confirmation_tags_the_new_goal(orchestrator, ctx):
    # Reproduced live (nonprod, tenant tn88351775, 2026-08-18): replying
    # "yes" to "did you mean X, or Y?" is genuinely ambiguous -- the LLM's
    # choice-resolution correctly reports "other" rather than guessing. The
    # bug was downstream: when the resulting fresh redispatch itself can't
    # resolve a project (no page context, no last-discussed project) and
    # falls to a "which project?" disambiguation, _reporting_node used to
    # call _finalize_turn with no resolved/goal_kind at all -- so the new
    # goal opened UNTAGGED, keyed on the raw reply ("yes") as
    # original_utterance, with no "kind" for the next turn to route on.
    orchestrator.conversation_memory = _memory_mock(pending_goal=_explicit_switch_goal())
    orchestrator.llm.resolve_project_context_switch_reply.return_value = {"choice": "other"}
    # No page context, no real project name in "yes", no last-discussed
    # project recorded -- entity resolution must come up empty so the
    # redispatch below is forced into the unresolved disambiguation branch.
    orchestrator.llm.extract_project_name.return_value = None
    orchestrator.conversation_memory.get_last_project.return_value = None
    orchestrator.conversation_memory.get_last_project_id.return_value = None
    disambiguation = MagicMock()
    disambiguation.type = "disambiguation"
    disambiguation.question = "Which project would you like to know about?"
    disambiguation.options = None
    disambiguation.suggestion = None
    disambiguation.route = None
    disambiguation.label = None
    disambiguation.confidence = None
    disambiguation.routeState = None
    disambiguation.project = None
    disambiguation.message = None
    disambiguation.skipVoiceRewrite = False
    orchestrator.registry["reporting"] = MagicMock()
    orchestrator.registry["reporting"].respond.return_value = disambiguation

    orchestrator._reporting_node({"context": ctx("yes")})

    orchestrator.conversation_memory.clear_pending_goal.assert_not_called()
    new_goal = orchestrator.conversation_memory.set_pending_goal.call_args.args[2]
    # The fix: the fresh goal is tagged so the NEXT turn (e.g. naming the
    # project again) routes straight back to reporting instead of falling
    # through unrouted to out_of_scope.
    assert new_goal["kind"] == "reporting_lookup"


def test_low_confidence_reply_is_treated_as_a_new_request(orchestrator, ctx):
    # Asymmetric cost (see GOAL_CONTINUATION_CONFIDENCE_THRESHOLD): a
    # low-confidence "it's a reply" is a guess, and continuing wrongly
    # cascades for up to GOAL_MAX_ATTEMPTS turns, while dropping wrongly
    # costs one re-ask. Below the threshold, prefer dropping.
    orchestrator.conversation_memory = _memory_mock(pending_goal=_hijack_goal())
    orchestrator.llm.classify_goal_continuation.return_value = {
        "is_reply": True, "confidence": 0.3, "reasoning": "could be either",
    }
    assert orchestrator._is_goal_reply(ctx("the leads status"), _hijack_goal()) is False


def test_goal_continuation_falls_back_to_shape_when_llm_fails(orchestrator, ctx):
    # failure-escalation: the LLM-primary decision needs a deterministic
    # fallback. Short == reply, sentence-length == new request.
    orchestrator.conversation_memory = _memory_mock(pending_goal=_hijack_goal())
    orchestrator.llm.classify_goal_continuation.return_value = None
    assert orchestrator._is_goal_reply(ctx("Invoices"), _hijack_goal()) is True
    assert orchestrator._is_goal_reply(
        ctx("what is the total outstanding balance across all my active projects"),
        _hijack_goal(),
    ) is False


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_go_back_replays_last_route_deterministically(mock_audit, orchestrator, ctx):
    # Regression: "take me back to the previous page" used to fall through
    # every deterministic tier (nothing in the phrase itself is retrievable)
    # and dead-end in a generic fallback goal instead of replaying the last
    # page EDEN actually navigated the user to -- confirmed live.
    orchestrator.conversation_memory = _memory_mock(recent_turns=[
        {"utterance": "show me the customer lsit", "type": "navigate",
         "route": "/admin", "label": "Customers"},
    ])
    # ctx() defaults currentRoute to "/dashboard", distinct from the
    # remembered "/admin" -- so it's a real "go back", not a no-op.
    response = orchestrator.dispatch(ctx("take me back to the previous page"))
    assert response.type == "navigate"
    assert response.route == "/admin"
    assert response.label == "Customers"
    # triage() still runs (top-level agent choice, every request); only
    # the heavier per-route understand() call is skipped -- deterministic
    # for the same reason negation/recall are.
    orchestrator.llm.understand.assert_not_called()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_go_back_skips_the_current_page_itself(mock_audit, orchestrator, ctx):
    # If the most recent remembered turn IS the page the user is currently
    # on (per context.currentRoute), that's not "the previous page" -- walk
    # further back to the one before it.
    orchestrator.conversation_memory = _memory_mock(recent_turns=[
        {"utterance": "show me the customer lsit", "type": "navigate",
         "route": "/admin", "label": "Customers"},
        {"utterance": "open the dashboard", "type": "navigate",
         "route": "/dashboard", "label": "Dashboard"},
    ])
    response = orchestrator.dispatch(
        ctx("go back", currentRoute="/dashboard")
    )
    assert response.type == "navigate"
    assert response.route == "/admin"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_go_back_distinguishes_admin_tabs_sharing_one_shell_route(mock_audit, orchestrator, ctx):
    # Real bug, fixed: confirmed live -- Tools, Vendors, Resources,
    # Employees, and Customers all resolve to the same "/admin" shell URL,
    # distinguished only by routeState.selectedTab. Comparing bare route
    # strings alone made "go back" after Tools -> Vendors see both turns'
    # route == "/admin", treat them as identical, and fall back to
    # replaying the MOST RECENT one (Vendors -- the page already on)
    # instead of the genuinely earlier Tools tab. A silent no-op that
    # looked exactly like "go back" doing nothing. (Originally Materials,
    # tab 5 -- AB#32825 moved it to Material Mart, so it no longer exists.)
    orchestrator.conversation_memory = _memory_mock(recent_turns=[
        {"utterance": "show me tools", "type": "navigate",
         "route": "/admin", "label": "Tools", "routeState": {"selectedTab": "4"}},
        {"utterance": "show me vendors", "type": "navigate",
         "route": "/admin", "label": "Vendors", "routeState": {"selectedTab": "2"}},
    ])
    response = orchestrator.dispatch(
        ctx("go back to the previous page", currentRoute="/admin")
    )
    assert response.type == "navigate"
    assert response.route == "/admin"
    assert response.label == "Tools"
    assert response.routeState == {"selectedTab": "4"}


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_go_back_falls_back_to_only_remembered_page_even_if_current(mock_audit, orchestrator, ctx):
    # Real bug, fixed: confirmed live -- every remembered turn's route
    # matched context.currentRoute (an intervening guidance turn with no
    # route of its own doesn't move the page, so the last real navigate is
    # still "current"), so the old code returned None here and fell all the
    # way through to the LLM path, which produced a confusing,
    # self-contradicting fallback ("I couldn't find that page... would you
    # like to see 'schedule'/'invoices'/'purchase orders'?") instead of
    # navigating anywhere for "take me to the previous page." The one page
    # EDEN remembers is still a real, valid page to go back to.
    orchestrator.conversation_memory = _memory_mock(recent_turns=[
        {"utterance": "let me create a project", "type": "navigate",
         "route": "/createbid", "label": "Create Bid"},
        {"utterance": "what do i have here", "type": "guidance",
         "route": None, "label": None},
    ])
    response = orchestrator.dispatch(
        ctx("take me to the previous page", currentRoute="/createbid")
    )
    assert response.type == "navigate"
    assert response.route == "/createbid"
    assert response.label == "Create Bid"
    orchestrator.llm.understand.assert_not_called()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_refinement_answer_resolves_original_request(mock_audit, orchestrator, ctx, monkeypatch):
    # With a clarification pending, "its the second one" is an ANSWER: the
    # LLM must see the goal context, and retrieval must run on the combined
    # original + reply text.
    monkeypatch.setattr(agent_orchestrator_module, "MULTI_STEP_VERIFICATION_ENABLED", False)
    goal = {
        "original_utterance": "where i can see the customer information",
        "last_question": "A few pages match — which did you mean?",
        "attempts": 1,
        "rejected_routes": [],
    }
    orchestrator.conversation_memory = _memory_mock(
        recent_turns=[{"utterance": "where i can see the customer information",
                       "type": "disambiguation", "route": None, "label": None}],
        pending_goal=goal,
    )
    nav = orchestrator.registry["navigation"]
    customer_info = nav.route_by_pattern("/CustomerInfoDashboard")
    customer_table = nav.route_by_pattern("/customertable")
    with patch.object(nav, "top_candidates", return_value=[
        (customer_info, 0.5), (customer_table, 0.3),
    ]) as mock_retrieval:
        orchestrator.llm.understand.return_value = {
            "intent": "navigate", "route_id": "customer_info_dashboard",
            "confidence": 0.9, "extracted_entity": None,
            "insight": "The customer overview dashboard.",
        }
        response = orchestrator.dispatch(ctx("its the second one"))
    # Retrieval saw the original request, not just the bare fragment.
    retrieval_arg = mock_retrieval.call_args.args[0]
    assert "customer information" in retrieval_arg
    assert "second one" in retrieval_arg
    # The LLM was given the goal context.
    assert orchestrator.llm.understand.call_args.kwargs["pending_goal"] == goal
    # Resolved — and the goal is closed.
    assert response.type == "navigate"
    assert response.route == "/CustomerInfoDashboard"
    orchestrator.conversation_memory.clear_pending_goal.assert_called_once()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_bare_no_match_fallback_does_not_open_a_new_goal(mock_audit, orchestrator, ctx):
    # Real bug, fixed: a bare no-match fallback (no concrete suggestion or
    # options to answer -- just "I couldn't find that, try asking for X/Y/Z")
    # used to ALWAYS open a goal, which turned every off-topic/identity/meta
    # question ("who are you") into an unwanted multi-turn "which page did
    # you mean?" loop that then swallowed the NEXT, unrelated message as if
    # it were the answer (observed live). With no pending goal already open,
    # this reply shape must NOT start one -- see _update_goal_state's own
    # comment on this exact distinction.
    orchestrator.conversation_memory = _memory_mock()
    response = orchestrator.dispatch(ctx("zzzqqq xyzabc"))
    assert response.type == "fallback"
    assert not response.suggestion and not response.options
    orchestrator.conversation_memory.set_pending_goal.assert_not_called()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_unrelated_tier_collision_starts_fresh_goal_not_corrupt_old_one(
    mock_audit, orchestrator, ctx
):
    # Regression: a goal is open for "customer information" (2 options
    # offered). The next message accidentally matches an UNRELATED
    # deterministic tier (feature lookup, cross-page) instead of answering
    # the pending question. That response must NOT be merged into the old
    # goal (overwriting its last_question with unrelated text while
    # `offered` silently survives) — it must replace it with a fresh goal
    # keyed on the new utterance instead.
    old_goal = {
        "original_utterance": "where i can see the customer information",
        "attempts": 1,
        "rejected_routes": [],
        "last_question": "A few pages match — which did you mean?",
        "offered": ["Customers", "Customer Info Dashboard"],
    }
    orchestrator.conversation_memory = _memory_mock(pending_goal=old_goal)

    # "where's the edit vendor button" is a real feature-lookup cross-page
    # hit (see test_feature_question_never_calls_llm) — an unrelated
    # deterministic tier firing while the customer-information goal is open.
    response = orchestrator.dispatch(ctx("where's the edit vendor button", currentRoute="/dashboard"))
    assert response.type == "fallback"
    orchestrator.llm.understand.assert_not_called()

    new_goal = orchestrator.conversation_memory.set_pending_goal.call_args.args[2]
    # Replaced, not merged: the stale "customer information" thread is gone.
    assert new_goal["original_utterance"] == "where's the edit vendor button"
    assert new_goal["attempts"] == 1
    assert new_goal.get("offered") != old_goal["offered"]


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_goal_dropped_after_max_attempts(mock_audit, orchestrator, ctx):
    # Three unresolved turns in a row: stop carrying the stale request.
    orchestrator.conversation_memory = _memory_mock(pending_goal={
        "original_utterance": "zzzqqq xyzabc", "attempts": 2, "rejected_routes": [],
    })
    response = orchestrator.dispatch(ctx("qqqzzz zyxcba"))
    assert response.type == "fallback"
    orchestrator.conversation_memory.clear_pending_goal.assert_called()
    orchestrator.conversation_memory.set_pending_goal.assert_not_called()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_bare_no_match_still_advances_an_already_open_goal(mock_audit, orchestrator, ctx):
    # Real bug, fixed: the "don't open a NEW goal for a bare no-match" guard
    # (see _update_goal_state's comment) used to also block advancing an
    # EXISTING, already-continued goal when the follow-up was itself a bare
    # no-suggestion/no-options fallback -- so GOAL_MAX_ATTEMPTS's 3-tries cap
    # silently never applied to this one reply shape; the goal just sat
    # untouched until Redis's TTL expired it instead. Below the cap (attempts
    # 0 -> 1), it must still advance and stay open, not be silently ignored.
    orchestrator.conversation_memory = _memory_mock(pending_goal={
        "original_utterance": "zzzqqq xyzabc", "attempts": 0, "rejected_routes": [],
    })
    response = orchestrator.dispatch(ctx("qqqzzz zyxcba"))
    assert response.type == "fallback"
    orchestrator.conversation_memory.clear_pending_goal.assert_not_called()
    goal = orchestrator.conversation_memory.set_pending_goal.call_args.args[2]
    assert goal["original_utterance"] == "zzzqqq xyzabc"
    assert goal["attempts"] == 1


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_navigate_closes_goal(mock_audit, orchestrator, ctx):
    orchestrator.conversation_memory = _memory_mock(pending_goal={
        "original_utterance": "customer stuff", "attempts": 1, "rejected_routes": [],
    })
    response = orchestrator.dispatch(ctx("take me to projects"))
    assert response.type == "navigate"
    orchestrator.conversation_memory.clear_pending_goal.assert_called_once()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_out_of_scope_offers_candidate_cards(mock_audit, orchestrator, ctx, monkeypatch):
    # A vague, navigation-shaped request classified out_of_scope should ask
    # its follow-up question WITH the best candidates as clickable cards,
    # instead of dead-ending in plain text.
    monkeypatch.setattr(agent_orchestrator_module, "MULTI_STEP_VERIFICATION_ENABLED", False)
    orchestrator.conversation_memory = _memory_mock()
    nav = orchestrator.registry["navigation"]
    tenant_finance = nav.route_by_pattern("/tenantfinance")
    job_ledger = nav.route_by_pattern("/jobLedger")
    with patch.object(nav, "top_candidates", return_value=[
        (tenant_finance, 0.4), (job_ledger, 0.35),
    ]):
        orchestrator.llm.understand.return_value = {
            "intent": "out_of_scope", "route_id": None, "confidence": 0.3,
            "extracted_entity": None,
            "insight": "Which money details do you need — company finances or job costs?",
        }
        response = orchestrator.dispatch(ctx("i need the money stuff"))
    assert response.type == "fallback"
    assert response.options is not None
    assert 1 <= len(response.options) <= 3


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_multi_step_verification_keeps_original_when_second_pass_disagrees(
    mock_audit, orchestrator, ctx, monkeypatch
):
    monkeypatch.setattr(agent_orchestrator_module, "MULTI_STEP_VERIFICATION_ENABLED", True)
    orchestrator.conversation_memory = _memory_mock()
    nav = orchestrator.registry["navigation"]
    customer_table = nav.route_by_pattern("/customertable")
    customer_info = nav.route_by_pattern("/CustomerInfoDashboard")
    with patch.object(nav, "top_candidates", return_value=[
        (customer_table, 0.301), (customer_info, 0.276),
    ]):
        orchestrator.llm.understand.return_value = {
            "intent": "navigate", "route_id": "customer_table", "confidence": 0.95,
            "extracted_entity": None, "insight": "Here are the customers.",
        }
        orchestrator.llm.verify_ambiguity.return_value = {
            "is_ambiguous": False, "plausible_route_ids": [], "reasoning": "Clearly the list.",
        }
        response = orchestrator.dispatch(ctx("where i can see the customer information"))
    orchestrator.llm.verify_ambiguity.assert_called_once()
    assert response.type == "navigate"
    assert response.route == "/admin"  # customer_table's registry-fixed target_route
    assert response.routeState == {"selectedTab": "3"}


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_multi_step_verification_skips_when_last_turn_already_resolved_it(
    mock_audit, orchestrator, ctx, monkeypatch
):
    # Real bug, fixed: "take me to the customer page" right after a prior
    # turn already navigated to customer_table must NOT be re-litigated into
    # a disambiguation -- conversation context (the last resolved turn)
    # already answers which of the near-tied candidates was meant, and the
    # second-opinion LLM call (which sees no history at all) must not even
    # be asked.
    monkeypatch.setattr(agent_orchestrator_module, "MULTI_STEP_VERIFICATION_ENABLED", True)
    nav = orchestrator.registry["navigation"]
    customer_table = nav.route_by_pattern("/customertable")
    customer_info = nav.route_by_pattern("/CustomerInfoDashboard")
    orchestrator.conversation_memory = _memory_mock(recent_turns=[
        {"utterance": "show customers", "type": "navigate", "route": "/admin",
         "label": customer_table.label_template},
    ])
    with patch.object(nav, "top_candidates", return_value=[
        (customer_table, 0.380), (customer_info, 0.375),
    ]):
        orchestrator.llm.understand.return_value = {
            "intent": "navigate", "route_id": "customer_table", "confidence": 1.0,
            "extracted_entity": None, "insight": "Here are the customers.",
        }
        response = orchestrator.dispatch(ctx("take me to the customer page"))
    orchestrator.llm.verify_ambiguity.assert_not_called()
    assert response.type == "navigate"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_multi_step_verification_skips_when_already_on_that_page(
    mock_audit, orchestrator, ctx, monkeypatch
):
    # Same short-circuit, other trigger: the user is already ON the page the
    # primary answer picked (context.currentRoute resolves to it directly) --
    # asking "is this ambiguous?" a second time is pointless when they're
    # looking right at it.
    monkeypatch.setattr(agent_orchestrator_module, "MULTI_STEP_VERIFICATION_ENABLED", True)
    nav = orchestrator.registry["navigation"]
    # question_builder stands in for the removed invoice_list/
    # invoice_payment here -- flat/no-entity/no-permission and genuinely
    # generic (no external-link dependency), so this test's own mechanic
    # (already-on-page short-circuit, not invoice-specific) is unaffected.
    #
    # Real bug, fixed (PR review, Raja): "show me the invoices again" now
    # genuinely collides with project_invoice_view's own intent_keywords
    # (the fix for the invoice/billing keyword-tier regression this same
    # review flagged), which would intercept it on the deterministic tier
    # before dispatch() ever reaches the mocked top_candidates/understand()
    # below.
    dashboard = nav.route_by_pattern("/questionBuilder")
    customer_invoices = nav.route_by_pattern("/customerInvoices")
    orchestrator.conversation_memory = _memory_mock()
    with patch.object(nav, "top_candidates", return_value=[
        (dashboard, 0.380), (customer_invoices, 0.375),
    ]):
        orchestrator.llm.understand.return_value = {
            "intent": "navigate", "route_id": "question_builder", "confidence": 0.9,
            "extracted_entity": None, "insight": "Here's where that lives.",
        }
        response = orchestrator.dispatch(
            ctx("give me an update again", currentRoute="/questionBuilder")
        )
    orchestrator.llm.verify_ambiguity.assert_not_called()
    # Already-on-this-page resolution legitimately answers with "guidance"
    # ("you're already here"), not "navigate" -- the point of this test is
    # that it's NOT re-litigated into a disambiguation between
    # question_builder and customer_invoices, which is what the bug would
    # have produced.
    assert response.type in ("navigate", "guidance")


# ── Customer project-scoping fail-closed fix ───────────────────────────────
# Real bug, fixed: UserRoleResolver.get_project_ids() returning [] means "no
# scoping data available, don't filter" for an INTERNAL role (correct --
# a PM needs to see the whole tenant). Applying that same fail-open default
# to a CUSTOMER role is a cross-customer data leak, not hypothetical --
# verified live against a real account: a customer with unpopulated
# project_ids saw every OTHER customer's project in disambiguation.


def test_scoped_project_ids_fails_closed_for_customer_with_no_scoping_data(orchestrator, ctx):
    orchestrator.role_resolver.is_customer.return_value = True
    orchestrator.role_resolver.get_project_ids.return_value = []
    scoped = orchestrator._scoped_project_ids(ctx("what is the project status"))
    assert scoped == agent_orchestrator_module._NO_CUSTOMER_PROJECTS_SENTINEL


def test_scoped_project_ids_stays_unscoped_for_internal_role_with_no_scoping_data(orchestrator, ctx):
    # No assignee entries anywhere for this account -- fails OPEN (unlike
    # the customer sentinel), since assignee data rolling out gradually is
    # a real, expected transition state, not a security boundary.
    orchestrator.role_resolver.is_customer.return_value = False
    orchestrator.role_resolver.get_project_ids.return_value = []
    orchestrator.registry["reporting"].entity_resolver = MagicMock()
    orchestrator.registry["reporting"].entity_resolver.project_ids_for_assignee.return_value = []
    scoped = orchestrator._scoped_project_ids(ctx("what is the project status"))
    assert scoped == []


def test_scoped_project_ids_scopes_internal_role_via_assignee_data(orchestrator, ctx):
    # Per explicit user direction: once project_health.assignees is
    # populated for an internal account, it's scoped to those real
    # assignments instead of seeing the whole tenant.
    orchestrator.role_resolver.is_customer.return_value = False
    orchestrator.role_resolver.get_project_ids.return_value = []
    orchestrator.registry["reporting"].entity_resolver = MagicMock()
    orchestrator.registry["reporting"].entity_resolver.project_ids_for_assignee.return_value = ["proj-1", "proj-2"]
    scoped = orchestrator._scoped_project_ids(ctx("what is the project status"))
    assert scoped == ["proj-1", "proj-2"]


def test_scoped_project_ids_prefers_real_data_over_sentinel_even_for_customer(orchestrator, ctx):
    # Once Gold actually populates project_ids for a customer, that real
    # list must be used -- the sentinel only applies while it's genuinely empty.
    orchestrator.role_resolver.is_customer.return_value = True
    orchestrator.role_resolver.get_project_ids.return_value = ["proj-42"]
    scoped = orchestrator._scoped_project_ids(ctx("what is the project status"))
    assert scoped == ["proj-42"]


def test_reset_delegates_to_conversation_memory(orchestrator):
    orchestrator.conversation_memory = MagicMock()
    orchestrator.reset("tenant-1", "user-1")
    orchestrator.conversation_memory.reset.assert_called_once_with("tenant-1", "user-1")


# ── Centralized role-scope guardrail (_enforce_role_scope) ─────────────────
# The final, fail-closed backstop: every response funnels through
# _finalize_turn -> _enforce_role_scope, so a page the user's permissions
# don't allow can NEVER be surfaced regardless of which builder produced the
# response or whether it remembered to filter. Twice this session a builder
# shipped unfiltered (disambiguation options, out-of-scope cards) -- these
# lock in that the backstop catches the next one.

def _rbac_orchestrator(orchestrator, monkeypatch, permissions: set[str]):
    """Turn RBAC ON (the fixture defaults it off) and pin the navigation
    agent's permission set to exactly `permissions`."""
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav = orchestrator.registry["navigation"]
    nav.role_resolver = MagicMock()
    nav.role_resolver.get_permissions.return_value = permissions
    return orchestrator


def test_guardrail_blocks_forbidden_navigate(orchestrator, monkeypatch, ctx):
    from source.model.navigation_models import NavigationResponse
    _rbac_orchestrator(orchestrator, monkeypatch, {"PRO.REA"})
    # /invoice = invoice_list, requires INV.REA (not held) -> must not navigate.
    resp = NavigationResponse(type="navigate", route="/soldproduction/projectinformation/invoiceview", label="Invoices", confidence=0.95)
    scrubbed = orchestrator._enforce_role_scope(ctx("open invoices"), resp)
    assert scrubbed.type == "guidance"
    assert scrubbed.route is None


def test_guardrail_allows_permitted_navigate(orchestrator, monkeypatch, ctx):
    from source.model.navigation_models import NavigationResponse
    _rbac_orchestrator(orchestrator, monkeypatch, {"PRO.REA"})
    # /soldproduction = sold_production, requires PRO.REA (held) -> unchanged.
    resp = NavigationResponse(type="navigate", route="/soldproduction", label="Projects", confidence=0.95)
    scrubbed = orchestrator._enforce_role_scope(ctx("open projects"), resp)
    assert scrubbed.type == "navigate"
    assert scrubbed.route == "/soldproduction"


def test_guardrail_strips_forbidden_options_keeps_allowed(orchestrator, monkeypatch, ctx):
    from source.model.navigation_models import NavigationOption, NavigationResponse
    _rbac_orchestrator(orchestrator, monkeypatch, {"PRO.REA"})
    resp = NavigationResponse(
        type="disambiguation",
        question="Which one?",
        options=[
            NavigationOption(label="Projects", route="/soldproduction"),   # PRO.REA -> kept
            NavigationOption(label="Invoices", route="/soldproduction/projectinformation/invoiceview"),          # INV.REA -> dropped
            NavigationOption(label="Materials", route="/materialmanagement"),  # MAT.REA -> dropped
        ],
    )
    scrubbed = orchestrator._enforce_role_scope(ctx("show me things"), resp)
    assert scrubbed.type == "disambiguation"
    assert [o.route for o in scrubbed.options] == ["/soldproduction"]


def test_guardrail_deflects_when_all_options_forbidden(orchestrator, monkeypatch, ctx):
    from source.model.navigation_models import NavigationOption, NavigationResponse
    _rbac_orchestrator(orchestrator, monkeypatch, {"PRO.REA"})
    resp = NavigationResponse(
        type="disambiguation",
        question="Which one?",
        options=[
            NavigationOption(label="Invoices", route="/soldproduction/projectinformation/invoiceview"),
            NavigationOption(label="Materials", route="/materialmanagement"),
        ],
    )
    scrubbed = orchestrator._enforce_role_scope(ctx("show me things"), resp)
    assert scrubbed.type == "guidance"  # a question with no answerable option -> deflect
    assert not scrubbed.options


def test_guardrail_strips_forbidden_suggestion(orchestrator, monkeypatch, ctx):
    from source.model.navigation_models import NavigationResponse, NavigationSuggestion
    _rbac_orchestrator(orchestrator, monkeypatch, {"PRO.REA"})
    resp = NavigationResponse(
        type="guidance",
        message="Here's the status. Would you like to see the Invoices page?",
        suggestion=NavigationSuggestion(label="Invoices", route="/soldproduction/projectinformation/invoiceview"),
    )
    scrubbed = orchestrator._enforce_role_scope(ctx("status"), resp)
    assert scrubbed.suggestion is None


def test_guardrail_is_noop_when_rbac_skipped(orchestrator, ctx):
    # The fixture defaults SKIP_RBAC_CHECK=true -> the guardrail must pass
    # everything through untouched (local-dev behavior).
    from source.model.navigation_models import NavigationResponse
    resp = NavigationResponse(type="navigate", route="/soldproduction/projectinformation/invoiceview", label="Invoices", confidence=0.95)
    scrubbed = orchestrator._enforce_role_scope(ctx("open invoices"), resp)
    assert scrubbed.type == "navigate"
    assert scrubbed.route == "/soldproduction/projectinformation/invoiceview"


# ── Mobile support: same agent selection as web, route data stripped later ──

def test_mobile_platform_routes_to_the_same_agent_as_web(orchestrator, ctx):
    # Real bug, fixed (2026-09-03): the router used to unconditionally
    # redirect any "navigation" resolution to "reporting" on mobile, on the
    # reasoning that mobile has no page to send a route to. That swapped
    # the ENTIRE specialist for the turn, not just the route data -- so
    # anything Navigation handles without needing a page (greetings,
    # feature-lookup answers, guidance) also got redirected to Reporting,
    # which has no small-talk/feature-lookup handling of its own and fell
    # through to a "which project?" listing. Agent selection is now
    # platform-independent -- mobile's "no page" constraint is handled one
    # layer down, in response construction (see
    # test_strip_route_fields_for_mobile_rewrites_navigate_message_phrasing),
    # never by swapping which specialist answers.
    orchestrator.llm.triage.return_value = {
        "agent": "navigation", "confidence": 0.95, "reasoning": "navigation intent",
    }
    mobile_decision = orchestrator._eden_router_node(
        {"context": ctx("take me to invoices", platform="mobile")}
    )
    web_decision = orchestrator._eden_router_node(
        {"context": ctx("take me to invoices")}
    )
    assert mobile_decision["next_agent"] == web_decision["next_agent"] == "navigation"


def test_mobile_platform_greeting_routes_to_navigation(orchestrator, ctx):
    orchestrator.llm.triage.return_value = {
        "agent": "navigation", "confidence": 0.95, "reasoning": "greeted the assistant",
    }
    decision = orchestrator._eden_router_node(
        {"context": ctx("hi", platform="mobile")}
    )
    assert decision["next_agent"] == "navigation"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_mobile_greeting_does_not_suggest_navigation_examples(mock_audit, orchestrator, ctx):
    # Real bug, fixed: confirmed live on mobile (2026-09-03) -- the web
    # greeting's example phrasing ("take me to invoices", "show me the
    # schedule") suggests a page navigation mobile can never perform (see
    # _strip_route_fields_for_mobile's own docstring -- there is no page on
    # mobile). Mobile gets data-question examples instead.
    response = orchestrator.dispatch(ctx("hi", platform="mobile"))
    assert response.type == "guidance"
    lowered = response.message.lower()
    assert "take me to" not in lowered
    assert "show me the schedule" not in lowered


def test_strip_route_fields_for_mobile_rewrites_navigate_message_phrasing(orchestrator, ctx):
    # Real gap, fixed: nulling the route left the message text still
    # CLAIMING an action mobile never performs ("Taking you to Invoices.") --
    # confirmed live this read as though the app had navigated when nothing
    # happened. The action framing must be rewritten to informational text
    # in the same pass that nulls the route.
    from source.model.navigation_models import NavigationResponse
    resp = NavigationResponse(
        type="navigate", route="/soldproduction/projectinformation/invoiceview",
        label="Invoices", message="Taking you to Invoices. View and manage invoices here.",
        confidence=0.95,
    )
    stripped = orchestrator._strip_route_fields_for_mobile(ctx("x", platform="mobile"), resp)
    assert stripped.type == "guidance"
    assert stripped.route is None
    assert stripped.message == "Invoices. View and manage invoices here."


def test_strip_route_fields_for_mobile_rewrites_back_to_phrasing(orchestrator, ctx):
    from source.model.navigation_models import NavigationResponse
    resp = NavigationResponse(
        type="navigate", route="/soldproduction/projectinformation/invoiceview",
        label="Invoices", message="Back to Invoices.", confidence=0.9,
    )
    stripped = orchestrator._strip_route_fields_for_mobile(ctx("x", platform="mobile"), resp)
    assert stripped.message == "Invoices."


def test_strip_route_fields_for_mobile_rewrites_phrasing_mid_message(orchestrator, ctx):
    # Real bug, fixed: confirmed live on fieldcraftmind (2026-09-03) -- the phrasing
    # patterns were anchored to `^` (start of the WHOLE message), so a
    # message where the action phrase is the SECOND sentence (exactly
    # insight_agent.projects_disambiguation's over-cap redirect) never
    # matched at all, and "Taking you to the Projects page" survived
    # untouched on mobile -- confirmed live, this still read as though the
    # app had navigated.
    from source.model.navigation_models import NavigationResponse
    resp = NavigationResponse(
        type="navigate", route="/soldproduction",
        label="Projects",
        message=(
            "You have more projects than I can list here. Taking you to "
            "the Projects page so you can pick the one you mean "
            "(or tell me its order number)."
        ),
    )
    stripped = orchestrator._strip_route_fields_for_mobile(ctx("x", platform="mobile"), resp)
    assert "taking you to" not in stripped.message.lower()
    assert "you have more projects than i can list here" in stripped.message.lower()


def test_strip_route_fields_for_mobile_rewrites_found_it_phrasing(orchestrator, ctx):
    # Real bug, fixed (PR review, Raja): navigation_agent.py's
    # _navigate_message builds "Found it — X is in the Y module." for
    # show/find/where utterances -- not covered by any existing pattern,
    # so it leaked through unrewritten on mobile, still implying a page
    # had opened.
    from source.model.navigation_models import NavigationResponse
    resp = NavigationResponse(
        type="navigate", route="/timesheets",
        label="Timesheets",
        message="Found it — Timesheets is in the Time module.",
        confidence=0.9,
    )
    stripped = orchestrator._strip_route_fields_for_mobile(ctx("x", platform="mobile"), resp)
    assert "found it" not in stripped.message.lower()
    assert stripped.message == "Timesheets is in the Time section."


def test_strip_route_fields_for_mobile_rewrites_default_navigate_phrasing(orchestrator, ctx):
    # Real bug, fixed (PR review, Raja): "Right this way — X." is
    # _navigate_message's DEFAULT fallback, used whenever the utterance
    # matches none of take/go/open/navigate/show/find/where -- not an
    # edge case, but none of the existing patterns covered it either.
    from source.model.navigation_models import NavigationResponse
    resp = NavigationResponse(
        type="navigate", route="/soldproduction/projectinformation/invoiceview",
        label="Invoices", message="Right this way — Invoices.", confidence=0.85,
    )
    stripped = orchestrator._strip_route_fields_for_mobile(ctx("x", platform="mobile"), resp)
    assert "right this way" not in stripped.message.lower()
    assert stripped.message == "Invoices is available."


def test_web_platform_still_routes_to_navigation_unchanged(orchestrator, ctx):
    # Same triage result, no platform set (the default -- every existing web
    # request) -- must be completely unaffected by the mobile redirect.
    orchestrator.llm.triage.return_value = {
        "agent": "navigation", "confidence": 0.95, "reasoning": "navigation intent",
    }
    decision = orchestrator._eden_router_node(
        {"context": ctx("take me to invoices")}
    )
    assert decision["next_agent"] == "navigation"


# ── Internal cost-breakdown phrasing must reach Insight, never Reporting --
# confirmed live, 100% reproducible: the triage LLM consistently
# misclassifies "material cost"/"worker cost" as reporting, which
# structurally can never answer it (the field is excluded from its data by
# design). Not a general routing override -- narrower than VARIANCE_SIGNALS,
# and only ever corrects a "reporting" pick, never competes with triage on
# anything else. ───────────────────────────────────────────────────────────

def test_material_cost_triaged_reporting_is_corrected_to_insight_for_internal_user(orchestrator, ctx):
    orchestrator.llm.triage.return_value = {
        "agent": "reporting", "confidence": 0.95,
        "reasoning": "relates to the user's own billing and cost tracking",
    }
    orchestrator.role_resolver.is_customer.return_value = False
    decision = orchestrator._eden_router_node(
        {"context": ctx("what is the material cost for 711043")}
    )
    assert decision["next_agent"] == "insight"


def test_worker_cost_triaged_reporting_is_corrected_to_insight_for_internal_user(orchestrator, ctx):
    orchestrator.llm.triage.return_value = {
        "agent": "reporting", "confidence": 0.95,
        "reasoning": "relates to billing and cost tracking",
    }
    orchestrator.role_resolver.is_customer.return_value = False
    decision = orchestrator._eden_router_node(
        {"context": ctx("what is the worker cost on henderson")}
    )
    assert decision["next_agent"] == "insight"


def test_material_cost_correction_still_respects_customer_role_gate(orchestrator, ctx):
    # The correction feeds back into _route_insight_or_reporting, not around
    # it -- a customer must still be downgraded to reporting regardless
    # (they'll get an honest "not available at your access level" refusal
    # from Insight's own BPE.CST gate if this somehow reached it, but the
    # role gate is the first, fail-closed line of defense and must never be
    # bypassed by this correction).
    orchestrator.llm.triage.return_value = {
        "agent": "reporting", "confidence": 0.95,
        "reasoning": "relates to billing and cost tracking",
    }
    orchestrator.role_resolver.is_customer.return_value = True
    decision = orchestrator._eden_router_node(
        {"context": ctx("what is the material cost for 711043")}
    )
    assert decision["next_agent"] == "reporting"


def test_unrelated_reporting_phrasing_is_never_touched_by_the_correction(orchestrator, ctx):
    # Boundary pin: this correction must only fire for the narrow internal-
    # cost vocabulary -- a genuine billing question triaged to reporting
    # must pass through completely unaffected.
    orchestrator.llm.triage.return_value = {
        "agent": "reporting", "confidence": 0.95,
        "reasoning": "billing-related query",
    }
    orchestrator.role_resolver.is_customer.return_value = False
    decision = orchestrator._eden_router_node(
        {"context": ctx("what is the invoiced amount")}
    )
    assert decision["next_agent"] == "reporting"


def test_finalize_turn_strips_route_fields_for_mobile(orchestrator, ctx):
    # Defense-in-depth backstop: even if a navigate-shaped response reaches
    # _finalize_turn for a mobile caller (e.g. via a goal-continuation path
    # the router-level redirect doesn't cover), no route-shaped data must
    # survive to the response the client actually receives.
    from source.model.navigation_models import NavigationOption, NavigationResponse, NavigationSuggestion
    resp = NavigationResponse(
        type="navigate", route="/soldproduction/projectinformation/invoiceview", routeState={"selectedTab": 7}, icon="\U0001f4b0",
        label="Invoices", confidence=0.95,
        options=[NavigationOption(label="Invoices", route="/soldproduction/projectinformation/invoiceview")],
        suggestion=NavigationSuggestion(label="Invoices", route="/soldproduction/projectinformation/invoiceview"),
    )
    stripped = orchestrator._strip_route_fields_for_mobile(ctx("x", platform="mobile"), resp)
    assert stripped.route is None
    assert stripped.routeState is None
    assert stripped.icon is None
    assert stripped.options is None
    assert stripped.suggestion is None
    # Real bug, fixed: leaving type="navigate" with every route field null
    # is a shape no OTHER "navigate" response in the contract produces --
    # a mobile client that assumes a non-null route on that type has no
    # defined behavior for it. Matches every other no-page-attached answer.
    assert stripped.type == "guidance"


def test_finalize_turn_is_noop_for_web(orchestrator, ctx):
    from source.model.navigation_models import NavigationResponse
    resp = NavigationResponse(type="navigate", route="/soldproduction/projectinformation/invoiceview", label="Invoices", confidence=0.95)
    unchanged = orchestrator._strip_route_fields_for_mobile(ctx("x"), resp)
    assert unchanged.route == "/soldproduction/projectinformation/invoiceview"


def test_mobile_disambiguation_preserves_option_labels_as_text(orchestrator, ctx):
    # Real bug, fixed: projects_disambiguation() builds "Showing N of M --
    # if you don't see it here, tell me the order number." ALONGSIDE a real
    # `options` list from the same `matches`. Stripping `options` outright
    # for mobile left that "Showing N of M" text dangling with nothing to
    # show -- a disambiguation turn that looked like the backend answered
    # with nothing, even though it returned a real 200 with real data.
    from source.model.navigation_models import NavigationOption, NavigationResponse
    resp = NavigationResponse(
        type="disambiguation",
        question=(
            "Which project would you like to know about? Showing 2 of 5 -- "
            "if you don't see it here, tell me the order number."
        ),
        options=[
            NavigationOption(label="Alpha (Order #1)", route="/soldproduction/projectinformation"),
            NavigationOption(label="Beta (Order #2)", route="/soldproduction/projectinformation"),
        ],
    )
    stripped = orchestrator._strip_route_fields_for_mobile(ctx("x", platform="mobile"), resp)
    assert stripped.type == "guidance"
    assert stripped.options is None
    # The promised list must survive as plain text on `question` instead of
    # vanishing -- the caller still needs the actual project names/order
    # numbers to answer the "which one" prompt.
    assert "Alpha (Order #1)" in stripped.question
    assert "Beta (Order #2)" in stripped.question
    assert "Showing 2 of 5" in stripped.question
    # PR review (Raja): the folded names have no fact-preservation guard on
    # the name axis in the voice-rewrite step (_number_tokens only checks
    # numbers survive, not names) -- must skip that rewrite entirely rather
    # than risk it silently reordering/renaming the folded entries.
    assert stripped.skipVoiceRewrite is True


def test_mobile_fallback_with_options_folds_into_message_and_skips_rewrite(orchestrator, ctx):
    # PR review (Raja): the sibling `elif response.message` / `else` fold
    # branch was previously untested and, before the fix above, also left
    # a name-corruption gap open on a genuinely reachable path --
    # navigation_agent.py's fallback-with-options response sets `message`
    # (page names as options), not `question`, and can reach this stripper
    # via the same mobile goal-continuation backstop this function's own
    # docstring describes.
    from source.model.navigation_models import NavigationOption, NavigationResponse
    resp = NavigationResponse(
        type="fallback",
        message="I couldn't pull that up directly. Here are some places that might help:",
        options=[
            NavigationOption(label="Projects", route="/soldproduction"),
            NavigationOption(label="Invoices", route="/soldproduction/projectinformation/invoiceview"),
        ],
    )
    stripped = orchestrator._strip_route_fields_for_mobile(ctx("x", platform="mobile"), resp)
    assert stripped.type == "guidance"
    assert stripped.options is None
    assert stripped.question is None
    assert "Projects" in stripped.message
    assert "Invoices" in stripped.message
    assert stripped.skipVoiceRewrite is True
