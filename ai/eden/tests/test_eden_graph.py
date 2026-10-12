"""Shape + parity tests for the outer LangGraph built in
AgentOrchestrator._build_graph. The graph isn't wired into dispatch() yet
(see agent_orchestrator.dispatch) -- these tests exercise it directly."""
from unittest.mock import MagicMock, patch

import pytest

from source.orchestrator.agent_orchestrator import AgentOrchestrator


@pytest.fixture
def orchestrator(monkeypatch):
    monkeypatch.setenv("SKIP_RBAC_CHECK", "true")
    orch = AgentOrchestrator()
    orch.registry["navigation"].entity_resolver = MagicMock()
    orch.registry["navigation"].entity_resolver.resolve_project.return_value = []
    orch.registry["navigation"].entity_resolver.resolve_by_id.return_value = []
    # Explicit None (not a truthy auto-Mock) -- the data-grounded name
    # recovery in _resolve_insight_entity calls this; a bare Mock return
    # would look like "utterance contains a real project name" and hijack
    # every fallback-path test. None = "no real project name in the text",
    # the correct default for these tests' synthetic utterances.
    orch.registry["navigation"].entity_resolver.find_named_project_in_text.return_value = None
    # Same "unconfigured MagicMock is truthy" gap as find_named_project_in_text
    # above, for the order-number half of the same recovery step.
    orch.registry["navigation"].entity_resolver.find_ordered_project_in_text.return_value = None
    orch.llm = MagicMock()
    orch.registry["navigation"].llm_client = orch.llm
    # eden_router calls triage() on every request; default everything to
    # navigation, and let Insight-specific tests override per-test.
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
    # Real Gold data has project_ids empty for effectively every user today
    # -- [] is "no scoping data available" (unscoped), the common default.
    orch.role_resolver.get_project_ids.return_value = []
    return orch


def test_graph_has_expected_nodes(orchestrator):
    nodes = set(orchestrator._graph.get_graph().nodes)
    assert "eden_router" in nodes
    assert "navigation" in nodes
    assert "insight" in nodes
    assert "reporting" in nodes
    assert "clarify" in nodes


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_graph_invoke_matches_direct_cascade_call(mock_audit, orchestrator, ctx):
    context = ctx("hello there")
    direct = orchestrator._run_navigation_cascade(context)

    context2 = ctx("hello there")
    graphed = orchestrator._graph.invoke({"context": context2})["response"]

    assert graphed.type == direct.type
    assert graphed.message == direct.message


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_graph_invoke_matches_direct_cascade_call_on_keyword_route(mock_audit, orchestrator, ctx):
    # Real bug, fixed: "take me to invoices" used to resolve via the bare
    # keyword tier straight to navigate/invoice_list -- that route was
    # removed entirely (its frontend page crashed on load) and billing/
    # invoice keywords now resolve to project_invoice_view, entity_required
    # -- with no project named or in page context here, that's a "which
    # project?" fallback, not a bare navigate. Swapped to another genuinely
    # flat, non-entity keyword so this test keeps testing what it was
    # actually meant to: the graph and the direct cascade call agreeing.
    context = ctx("take me to the purchase orders")
    direct = orchestrator._run_navigation_cascade(context)

    context2 = ctx("take me to the purchase orders")
    graphed = orchestrator._graph.invoke({"context": context2})["response"]

    assert graphed.type == direct.type == "navigate"
    assert graphed.route == direct.route == "/purchaseOrder"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_variance_question_routes_to_insight_with_resolvable_project(mock_audit, orchestrator, ctx):
    from source.model.navigation_models import NavigationResponse

    orchestrator.llm.triage.return_value = {"agent": "insight"}
    nav = orchestrator.registry["navigation"]
    insight = orchestrator.registry["insight"]
    with patch.object(nav, "extract_entity_name", return_value="Henderson Roofing"), \
         patch.object(
             insight, "variance_response",
             return_value=NavigationResponse(
                 type="guidance",
                 message="Henderson Roofing: actual cost $142,000 vs. estimate $100,000 (+42.0%).",
             ),
         ) as mock_variance:
        response = orchestrator._graph.invoke(
            {"context": ctx("why is cost over budget on henderson")}
        )["response"]

    mock_variance.assert_called_once()
    assert response.type == "guidance"
    assert "Henderson Roofing" in response.message


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_variance_question_with_unresolvable_project_falls_back_to_deflection(
    mock_audit, orchestrator, ctx
):
    # entity_name resolves to something, but Insight genuinely can't answer
    # (e.g. name not found at all) -- still falls back to nav's deflection,
    # same as before. variance_response IS called now even with no entity
    # name (that's the new "list projects" case, covered separately) -- this
    # test covers the "still can't help" case collapsing to None.
    orchestrator.llm.triage.return_value = {"agent": "insight"}
    nav = orchestrator.registry["navigation"]
    insight = orchestrator.registry["insight"]
    with patch.object(nav, "extract_entity_name", return_value="nonexistent"), \
         patch.object(insight, "variance_response", return_value=None) as mock_variance:
        response = orchestrator._graph.invoke(
            {"context": ctx("why is cost over budget on nonexistent")}
        )["response"]

    mock_variance.assert_called_once()
    assert response.type == "fallback"
    assert "couldn't" in response.message.lower()


def _memory_mock(recent_turns=None, pending_goal=None, last_project=None, last_project_id=None):
    mem = MagicMock()
    mem.get_recent_turns.return_value = recent_turns or []
    mem.get_pending_goal.return_value = pending_goal
    mem.get_last_project.return_value = last_project
    # Explicit None, not a MagicMock auto-attribute -- a bare MagicMock()
    # return value is truthy, which would make _last_discussed_project_exact
    # (correctly) prefer the "id" over the name every time, defeating tests
    # that specifically exercise the name-only fallback.
    mem.get_last_project_id.return_value = last_project_id
    return mem


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_variance_question_no_project_named_lists_projects_and_tags_goal(
    mock_audit, orchestrator, ctx
):
    from source.model.navigation_models import NavigationOption, NavigationResponse, ProjectRef

    orchestrator.conversation_memory = _memory_mock()
    orchestrator.llm.triage.return_value = {"agent": "insight"}
    nav = orchestrator.registry["navigation"]
    insight = orchestrator.registry["insight"]
    listing = NavigationResponse(
        type="disambiguation",
        question="Which project would you like to know about?",
        options=[
            NavigationOption(
                label="Henderson Roofing", route="/soldproduction/projectinformation",
                project=ProjectRef(id="uid-1", name="Henderson Roofing", status="active"),
            ),
        ],
    )
    with patch.object(nav, "extract_entity_name", return_value=None), \
         patch.object(insight, "variance_response", return_value=listing) as mock_variance:
        response = orchestrator.dispatch(ctx("why is cost over budget"))

    mock_variance.assert_called_once_with(
        mock_variance.call_args.args[0], None, allowed_project_ids=[], entity_id=None,
        intent_utterance="why is cost over budget",
    )
    assert response.type == "disambiguation"
    goal = orchestrator.conversation_memory.set_pending_goal.call_args.args[2]
    assert goal["kind"] == "project_lookup"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_project_lookup_reply_routes_to_insight_and_resolves(mock_audit, orchestrator, ctx):
    from source.model.navigation_models import NavigationResponse

    # A pending "which project?" goal is open -- the reply ("Henderson
    # Roofing") carries no variance keywords, but must still route to
    # insight (eden_router checks the pending goal's kind).
    goal = {
        "original_utterance": "why is cost over budget",
        "attempts": 1,
        "rejected_routes": [],
        "kind": "project_lookup",
    }
    orchestrator.conversation_memory = _memory_mock(pending_goal=goal)
    nav = orchestrator.registry["navigation"]
    insight = orchestrator.registry["insight"]
    answer = NavigationResponse(
        type="guidance",
        message="Henderson Roofing: actual cost $142,000 vs. estimate $100,000 (+42.0%).",
    )
    with patch.object(nav, "extract_entity_name", return_value="Henderson Roofing"), \
         patch.object(insight, "variance_response", return_value=answer):
        response = orchestrator.dispatch(ctx("Henderson Roofing"))

    assert response.type == "guidance"
    assert "Henderson Roofing" in response.message
    orchestrator.conversation_memory.clear_pending_goal.assert_called_once()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_non_keyword_phrasing_reaches_insight_via_triage(mock_audit, orchestrator, ctx):
    from source.model.navigation_models import NavigationResponse

    # THE case the old keyword-only router silently failed on: no
    # VARIANCE_SIGNALS phrase anywhere in the utterance, but the triage LLM
    # understands it's a project cost concern. Regression test proving the
    # router is now decision-by-understanding, not string matching.
    orchestrator.llm.triage.return_value = {
        "agent": "insight", "reasoning": "project spending concern",
    }
    nav = orchestrator.registry["navigation"]
    insight = orchestrator.registry["insight"]
    answer = NavigationResponse(type="guidance", message="Oakwood: $22,100 committed.")
    with patch.object(nav, "extract_entity_name", return_value="Oakwood"), \
         patch.object(insight, "variance_response", return_value=answer) as mock_variance:
        response = orchestrator.dispatch(
            ctx("I think we're spending too much on the Oakwood job")
        )

    mock_variance.assert_called_once()
    assert response.type == "guidance"
    assert "Oakwood" in response.message


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_triage_failure_falls_back_to_keyword_routing(mock_audit, orchestrator, ctx):
    from source.model.navigation_models import NavigationResponse

    # Degraded mode: triage LLM call fails (returns None). Obviously
    # variance-keyword-shaped requests must still reach Insight via the
    # demoted VARIANCE_SIGNALS fallback -- same resilience contract as
    # Navigation's own fuzzy-keyword fallback when its LLM is down.
    orchestrator.llm.triage.return_value = None
    nav = orchestrator.registry["navigation"]
    insight = orchestrator.registry["insight"]
    answer = NavigationResponse(type="guidance", message="Henderson: $62,000 committed.")
    with patch.object(nav, "extract_entity_name", return_value="Henderson"), \
         patch.object(insight, "variance_response", return_value=answer) as mock_variance:
        response = orchestrator.dispatch(ctx("why is cost over budget on henderson"))

    mock_variance.assert_called_once()
    assert response.type == "guidance"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_triage_failure_defaults_to_navigation_for_non_variance(mock_audit, orchestrator, ctx):
    # Degraded mode, non-variance phrasing: falls through to navigation,
    # exactly the pre-triage behavior. Real bug, fixed: this used to assert
    # invoice_list's old no-project-context redirect -- that route was
    # removed entirely, and "invoices" now resolves to project_invoice_view
    # (entity_required), a different response shape. Swapped to another
    # genuinely flat, non-entity keyword -- this test only cares that
    # degraded triage still reaches Navigation and resolves deterministically
    # at all, not the exact route.
    orchestrator.llm.triage.return_value = None
    response = orchestrator.dispatch(ctx("take me to the purchase orders"))
    assert response.type == "navigate"
    assert response.route == "/purchaseOrder"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_llm_extraction_supplies_entity_name_to_insight(mock_audit, orchestrator, ctx):
    from source.model.navigation_models import NavigationResponse

    # Primary path: the LLM reads the sentence and finds the name -- the
    # stop-word extractor must NOT be consulted. This is the phrasing the
    # subtraction approach mangled ("think spending too much oakwood job").
    orchestrator.llm.triage.return_value = {"agent": "insight"}
    orchestrator.llm.extract_project_name.return_value = {"project_name": "Oakwood"}
    nav = orchestrator.registry["navigation"]
    insight = orchestrator.registry["insight"]
    answer = NavigationResponse(type="guidance", message="Oakwood: $22,100 committed.")
    with patch.object(nav, "extract_entity_name") as mock_stopword, \
         patch.object(insight, "variance_response", return_value=answer) as mock_variance:
        response = orchestrator.dispatch(
            ctx("I think we are spending too much on the Oakwood job")
        )

    mock_stopword.assert_not_called()
    mock_variance.assert_called_once()
    assert mock_variance.call_args.args[1] == "Oakwood"
    assert response.type == "guidance"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_status_question_routes_to_reporting(mock_audit, orchestrator, ctx):
    from source.model.navigation_models import NavigationResponse

    orchestrator.llm.triage.return_value = {"agent": "reporting"}
    orchestrator.llm.extract_project_name.return_value = {"project_name": "Henderson"}
    reporting = orchestrator.registry["reporting"]
    answer = NavigationResponse(
        type="guidance", message="Henderson: 68% complete. Schedule: on track."
    )
    with patch.object(reporting, "status_response", return_value=answer) as mock_status:
        response = orchestrator.dispatch(ctx("how is the Henderson project going"))

    mock_status.assert_called_once()
    assert mock_status.call_args.args[1] == "Henderson"
    assert response.type == "guidance"
    assert "$" not in response.message


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_customer_money_question_downgraded_to_reporting(mock_audit, orchestrator, ctx):
    from source.model.navigation_models import NavigationResponse

    # THE customer-safety case: triage says insight (it's a money question),
    # but the user holds a customer role -- proposal.md's role table says
    # customers have NO Insight access, so they get the customer-safe
    # Reporting summary instead. Insight's variance_response must never run.
    orchestrator.llm.triage.return_value = {"agent": "insight"}
    orchestrator.llm.extract_project_name.return_value = {"project_name": "Henderson"}
    orchestrator.role_resolver.is_customer.return_value = True
    insight = orchestrator.registry["insight"]
    reporting = orchestrator.registry["reporting"]
    safe = NavigationResponse(
        type="guidance", message="Henderson: 68% complete. Schedule: on track."
    )
    with patch.object(insight, "variance_response") as mock_variance, \
         patch.object(reporting, "status_response", return_value=safe) as mock_status:
        response = orchestrator.dispatch(ctx("why is cost over budget on Henderson"))

    mock_variance.assert_not_called()
    mock_status.assert_called_once()
    assert "$" not in response.message


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_customer_billing_question_answered_directly(mock_audit, orchestrator, ctx):
    from source.model.navigation_models import NavigationResponse

    # A customer's OWN billing question -- different from the internal-cost
    # case above. Triage should classify this as reporting (not insight),
    # and Reporting answers with the customer's own bill, not a downgrade
    # message. Insight is never even consulted.
    orchestrator.llm.triage.return_value = {"agent": "reporting"}
    orchestrator.llm.extract_project_name.return_value = {"project_name": "Henderson"}
    orchestrator.role_resolver.is_customer.return_value = True
    insight = orchestrator.registry["insight"]
    reporting = orchestrator.registry["reporting"]
    bill = NavigationResponse(
        type="guidance",
        message="Henderson: billed $50,000, received $42,000, outstanding $8,000.",
    )
    with patch.object(insight, "variance_response") as mock_variance, \
         patch.object(reporting, "billing_response", return_value=bill) as mock_billing:
        response = orchestrator.dispatch(ctx("how much do I owe on Henderson"))

    mock_variance.assert_not_called()
    mock_billing.assert_called_once()
    assert "$8,000" in response.message


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_insight_threads_users_project_ids_through_to_variance_response(
    mock_audit, orchestrator, ctx
):
    # Real gap, fixed: EntityResolver never read a user's scoped project_ids
    # at all. The orchestrator must fetch it and pass it through.
    from source.model.navigation_models import NavigationResponse

    orchestrator.llm.triage.return_value = {"agent": "insight"}
    orchestrator.llm.extract_project_name.return_value = {"project_name": "Henderson"}
    orchestrator.role_resolver.get_project_ids.return_value = ["9000001", "9000003"]
    insight = orchestrator.registry["insight"]
    answer = NavigationResponse(type="guidance", message="Henderson: $62,000 committed.")
    with patch.object(insight, "variance_response", return_value=answer) as mock_variance:
        orchestrator.dispatch(ctx("why is cost over budget on Henderson"))

    assert mock_variance.call_args.kwargs["allowed_project_ids"] == ["9000001", "9000003"]


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_reporting_threads_users_project_ids_through_to_respond(mock_audit, orchestrator, ctx):
    from source.model.navigation_models import NavigationResponse

    orchestrator.llm.triage.return_value = {"agent": "reporting"}
    orchestrator.llm.extract_project_name.return_value = {"project_name": "Henderson"}
    orchestrator.role_resolver.get_project_ids.return_value = ["9000001"]
    reporting = orchestrator.registry["reporting"]
    answer = NavigationResponse(type="guidance", message="Henderson: 68% complete.")
    with patch.object(reporting, "respond", return_value=answer) as mock_respond:
        orchestrator.dispatch(ctx("how is the project going"))

    assert mock_respond.call_args.kwargs["allowed_project_ids"] == ["9000001"]


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_low_confidence_insight_triage_asks_instead_of_guessing(mock_audit, orchestrator, ctx):
    # An internal user, ambiguous money question, low triage confidence --
    # mirrors Navigation's own low-confidence disambiguation pattern, applied
    # at the top level. Neither Insight nor Reporting should ever be reached.
    orchestrator.llm.triage.return_value = {
        "agent": "insight", "confidence": 0.4, "reasoning": "could be either",
    }
    orchestrator.role_resolver.is_customer.return_value = False
    insight = orchestrator.registry["insight"]
    reporting = orchestrator.registry["reporting"]
    with patch.object(insight, "variance_response") as mock_variance, \
         patch.object(reporting, "respond") as mock_respond:
        response = orchestrator.dispatch(ctx("what's going on with the money on this project"))

    mock_variance.assert_not_called()
    mock_respond.assert_not_called()
    assert response.type == "disambiguation"
    assert "internal cost" in response.question.lower()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_high_confidence_insight_triage_does_not_clarify(mock_audit, orchestrator, ctx):
    from source.model.navigation_models import NavigationResponse

    orchestrator.llm.triage.return_value = {"agent": "insight", "confidence": 0.95}
    orchestrator.llm.extract_project_name.return_value = {"project_name": "Henderson"}
    orchestrator.role_resolver.is_customer.return_value = False
    insight = orchestrator.registry["insight"]
    answer = NavigationResponse(type="guidance", message="Henderson: $62,000 committed.")
    with patch.object(insight, "variance_response", return_value=answer) as mock_variance:
        response = orchestrator.dispatch(ctx("why is cost over budget on Henderson"))

    mock_variance.assert_called_once()
    assert response.type == "guidance"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_resolved_insight_answer_suggests_job_ledger(mock_audit, orchestrator, ctx, project_match):
    # Real gap, fixed: a successful Insight answer used to be number-only --
    # no "want to see the page too?" suggestion. Now it should point at the
    # RESOLVED project's own Job Ledger tab -- job_ledger's own standalone
    # page (/jobLedger, rendered with zero props) is unconditionally broken
    # with no project id (JobLedgerList.tsx requires one to fetch anything
    # at all -- verified live, "data.map is not a function"), so it's
    # entity_required now and aliased to project_information's Job Ledger
    # tab (see NavigationAgent.ENTITY_TAB_ALIAS_ROUTE_STATE).
    from source.model.navigation_models import NavigationResponse

    orchestrator.llm.triage.return_value = {"agent": "insight", "confidence": 0.95}
    orchestrator.llm.extract_project_name.return_value = {"project_name": "Henderson"}
    orchestrator.role_resolver.is_customer.return_value = False
    orchestrator.registry["navigation"].entity_resolver.resolve_project.return_value = [
        project_match("Henderson")
    ]
    insight = orchestrator.registry["insight"]
    answer = NavigationResponse(type="guidance", message="Henderson: $62,000 committed.")
    with patch.object(insight, "variance_response", return_value=answer):
        response = orchestrator.dispatch(ctx("why is cost over budget on Henderson"))

    assert response.suggestion is not None
    assert response.suggestion.route == "/soldproduction/projectinformation"
    assert response.suggestion.routeState["tabName"] == "simple-tab-9"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_resolved_billing_answer_suggests_invoice_list(mock_audit, orchestrator, ctx, project_match):
    # Real bug, fixed: invoice_list ("/invoice") was removed entirely (its
    # frontend page crashed on load -- see route_registry.json's own
    # comment). Billing follow-ups now point at the RESOLVED project's own
    # Invoices tab instead -- project_invoice_view, aliased to
    # project_information's tab 7 (see ENTITY_TAB_ALIAS_ROUTE_STATE) --
    # same pattern as test_resolved_insight_answer_suggests_job_ledger.
    from source.model.navigation_models import NavigationResponse

    orchestrator.llm.triage.return_value = {"agent": "reporting", "confidence": 0.95}
    orchestrator.llm.extract_project_name.return_value = {"project_name": "Henderson"}
    orchestrator.registry["navigation"].entity_resolver.resolve_project.return_value = [
        project_match("Henderson")
    ]
    reporting = orchestrator.registry["reporting"]
    bill = NavigationResponse(type="guidance", message="Henderson: billed $50,000.")
    with patch.object(reporting, "billing_response", return_value=bill):
        response = orchestrator.dispatch(ctx("how much do I owe on Henderson"))

    assert response.suggestion is not None
    assert response.suggestion.route == "/soldproduction/projectinformation"
    assert response.suggestion.routeState["tabName"] == "simple-tab-7"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_resolved_status_answer_suggests_projects_list(mock_audit, orchestrator, ctx):
    # Front-desk follow-up: a status answer now offers the Projects list
    # (flat, safe) rather than nothing. The project's OWN page is NOT
    # deep-linked -- ProjectInformation_v2 reads its project from
    # state.rowData, which EDEN can't populate, so that would break. The
    # projects-list redirect is the honest non-broken next step.
    from source.model.navigation_models import NavigationResponse

    orchestrator.llm.triage.return_value = {"agent": "reporting", "confidence": 0.95}
    orchestrator.llm.extract_project_name.return_value = {"project_name": "Henderson"}
    reporting = orchestrator.registry["reporting"]
    status = NavigationResponse(type="guidance", message="Henderson: 68% complete.")
    with patch.object(reporting, "status_response", return_value=status):
        response = orchestrator.dispatch(ctx("how is the project going"))

    assert response.suggestion is not None
    assert response.suggestion.route == "/customers-projects"
    # The conversational offer is appended to the message too.
    assert "would you like to see" in response.message.lower()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_navigation_triage_never_clarifies_regardless_of_confidence(mock_audit, orchestrator, ctx):
    # The clarify gate is scoped to insight/reporting only -- a low-
    # confidence navigation classification should proceed normally (worst
    # case is a deflection, not exposing/hiding financial data).
    orchestrator.llm.triage.return_value = {"agent": "navigation", "confidence": 0.1}
    response = orchestrator.dispatch(ctx("hello there"))
    assert response.type == "guidance"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_customer_low_confidence_insight_skips_clarify_goes_straight_to_reporting(
    mock_audit, orchestrator, ctx
):
    # Role gate must be checked BEFORE the clarify gate: a customer can
    # never reach Insight regardless of confidence, so they must never be
    # asked "internal cost or your own bill" -- that choice doesn't exist
    # for them. Low confidence + customer should go straight to Reporting,
    # not to the clarify question.
    from source.model.navigation_models import NavigationResponse

    orchestrator.llm.triage.return_value = {"agent": "insight", "confidence": 0.2}
    orchestrator.llm.extract_project_name.return_value = {"project_name": "Henderson"}
    orchestrator.role_resolver.is_customer.return_value = True
    reporting = orchestrator.registry["reporting"]
    safe = NavigationResponse(type="guidance", message="Henderson: 68% complete.")
    with patch.object(reporting, "status_response", return_value=safe) as mock_status:
        response = orchestrator.dispatch(ctx("what's going on with the money on this project"))

    mock_status.assert_called_once()
    assert response.type == "guidance"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_reporting_lookup_reply_routes_back_to_reporting(mock_audit, orchestrator, ctx):
    from source.model.navigation_models import NavigationResponse

    # Reporting asked "which project?" -- the bare-name reply must route
    # back to reporting (goal kind reporting_lookup), not to navigation or
    # insight, and the answer closes the goal.
    goal = {
        "original_utterance": "how is the project going",
        "attempts": 1,
        "rejected_routes": [],
        "kind": "reporting_lookup",
    }
    orchestrator.conversation_memory = _memory_mock(pending_goal=goal)
    orchestrator.llm.extract_project_name.return_value = {"project_name": "Henderson Roofing"}
    reporting = orchestrator.registry["reporting"]
    answer = NavigationResponse(
        type="guidance", message="Henderson Roofing: 68% complete. Schedule: on track."
    )
    with patch.object(reporting, "status_response", return_value=answer):
        response = orchestrator.dispatch(ctx("Henderson Roofing"))

    assert response.type == "guidance"
    orchestrator.llm.triage.assert_not_called()
    orchestrator.conversation_memory.clear_pending_goal.assert_called_once()


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_bare_single_word_reply_to_open_goal_is_used_as_entity_name(mock_audit, orchestrator, ctx):
    # Real bug, fixed: a bare one-word reply ("maple") to an open "which
    # project?" question has no conversation context of its own, so the LLM
    # extraction call reasonably returns null (too ambiguous alone to call a
    # name). But this turn IS the answer -- it must be used directly as the
    # entity name, not re-run through generic extraction that doesn't know
    # a question was just asked.
    from source.model.navigation_models import NavigationResponse

    goal = {
        "original_utterance": "i want to know the demo project cost",
        "attempts": 1,
        "rejected_routes": [],
        "kind": "project_lookup",
    }
    orchestrator.conversation_memory = _memory_mock(pending_goal=goal)
    orchestrator.llm.extract_project_name.return_value = {"project_name": None}
    insight = orchestrator.registry["insight"]
    answer = NavigationResponse(type="guidance", message="Maple: $36,960 committed.")
    with patch.object(insight, "variance_response", return_value=answer) as mock_variance:
        response = orchestrator.dispatch(ctx("maple"))

    assert mock_variance.call_args.args[1] == "maple"
    assert response.type == "guidance"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_reporting_lookup_reply_preserves_original_billing_intent(mock_audit, orchestrator, ctx, project_match):
    # Real bug, fixed: the bare project-name reply ("DEMO-Hillcrest Window
    # Upgrade") carries none of the ORIGINAL question's billing-vs-status
    # signal ("how much do I owe" / "invoice amount") -- ReportingAgent.respond
    # must be called with the goal's remembered ORIGINAL utterance as
    # intent_utterance, not the bare reply, or it silently answers a status
    # question instead of the billing one actually asked.
    #
    # Separately, invoice_list ("/invoice") was removed entirely -- the
    # billing suggestion now points at the RESOLVED project's own Invoices
    # tab (project_invoice_view, aliased to project_information's tab 7),
    # which needs a resolvable project match to build at all.
    from source.model.navigation_models import NavigationResponse

    goal = {
        "original_utterance": "i want to know invoice amount",
        "attempts": 1,
        "rejected_routes": [],
        "kind": "reporting_lookup",
    }
    orchestrator.conversation_memory = _memory_mock(pending_goal=goal)
    orchestrator.llm.extract_project_name.return_value = {"project_name": "DEMO-Hillcrest Window Upgrade"}
    orchestrator.registry["navigation"].entity_resolver.resolve_project.return_value = [
        project_match("DEMO-Hillcrest Window Upgrade")
    ]
    reporting = orchestrator.registry["reporting"]
    answer = NavigationResponse(
        type="guidance", message="DEMO-Hillcrest Window Upgrade: billed $4,463."
    )
    with patch.object(reporting, "respond", return_value=answer) as mock_respond:
        response = orchestrator.dispatch(ctx("DEMO-Hillcrest Window Upgrade"))

    assert mock_respond.call_args.kwargs["intent_utterance"] == "i want to know invoice amount"
    assert response.type == "guidance"
    # The billing suggestion decision must also use the original intent, not
    # the bare reply -- "DEMO-Hillcrest Window Upgrade" alone matches no
    # BILLING_SIGNALS word.
    assert response.suggestion is not None
    assert response.suggestion.route == "/soldproduction/projectinformation"
    assert response.suggestion.routeState["tabName"] == "simple-tab-7"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_llm_extraction_null_falls_back_to_current_page_project(mock_audit, orchestrator, ctx):
    from source.model.navigation_models import NavigationResponse

    # Extraction SUCCEEDED and says the message names no project -- but the
    # user is ON a project's page, so that page's project is the referent.
    # (The utterance text can't carry page context, so this fallback lives
    # in the orchestrator, not the prompt.)
    orchestrator.llm.triage.return_value = {"agent": "insight"}
    orchestrator.llm.extract_project_name.return_value = {"project_name": None}
    nav = orchestrator.registry["navigation"]
    insight = orchestrator.registry["insight"]
    answer = NavigationResponse(type="guidance", message="Henderson: $62,000 committed.")
    with patch.object(nav, "current_project_name", return_value="Henderson"), \
         patch.object(insight, "variance_response", return_value=answer) as mock_variance:
        response = orchestrator.dispatch(ctx("how is cost looking"))

    assert mock_variance.call_args.args[1] == "Henderson"
    assert response.type == "guidance"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_page_context_id_resolves_by_exact_id_not_name(mock_audit, orchestrator, ctx):
    # The user is on a project's page (currentEntity carries the exact
    # projectId). A nameless question ("how is cost looking") must resolve
    # by that EXACT id -- passed as entity_id, entity_name None -- so it's
    # immune to the duplicate-name problem (many projects sharing one name).
    from source.model.navigation_models import NavigationResponse

    orchestrator.llm.triage.return_value = {"agent": "insight"}
    orchestrator.llm.extract_project_name.return_value = {"project_name": None}
    insight = orchestrator.registry["insight"]
    answer = NavigationResponse(type="guidance", message="Project 9000001: $62,000 committed.")
    context = ctx(
        "how is cost looking",
        currentRoute="/soldproduction/projectinformation",
        currentEntity={"type": "project", "id": "9000001"},
    )
    with patch.object(insight, "variance_response", return_value=answer) as mock_variance:
        response = orchestrator.dispatch(context)

    # entity_name positional arg is None; entity_id kwarg carries the id.
    assert mock_variance.call_args.args[1] is None
    assert mock_variance.call_args.kwargs["entity_id"] == "9000001"
    assert response.type == "guidance"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_explicit_name_in_message_beats_page_context_id(mock_audit, orchestrator, ctx):
    # If the user NAMES a project in the message, that wins over the page
    # they happen to be on -- "why is Oakwood over budget" while viewing
    # Riverside's page resolves Oakwood, by name, not the page's id.
    from source.model.navigation_models import NavigationResponse

    orchestrator.llm.triage.return_value = {"agent": "insight"}
    orchestrator.llm.extract_project_name.return_value = {"project_name": "Oakwood"}
    insight = orchestrator.registry["insight"]
    answer = NavigationResponse(type="guidance", message="Oakwood: $22,100 committed.")
    context = ctx(
        "why is Oakwood over budget",
        currentRoute="/soldproduction/projectinformation",
        currentEntity={"type": "project", "id": "9000001"},
    )
    with patch.object(insight, "variance_response", return_value=answer) as mock_variance:
        orchestrator.dispatch(context)

    assert mock_variance.call_args.args[1] == "Oakwood"
    assert mock_variance.call_args.kwargs["entity_id"] is None


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_no_name_and_no_page_context_falls_back_to_last_discussed_project(
    mock_audit, orchestrator, ctx
):
    from source.model.navigation_models import NavigationResponse

    # Neither the sentence nor the current page names a project, but the
    # conversation already resolved one earlier ("cross-topic continuity") --
    # a follow-up like "is it delayed too?" should still resolve to that
    # project instead of re-asking "which project?".
    orchestrator.llm.triage.return_value = {"agent": "insight"}
    orchestrator.llm.extract_project_name.return_value = {"project_name": None}
    orchestrator.conversation_memory = _memory_mock(last_project="Henderson Roofing")
    nav = orchestrator.registry["navigation"]
    insight = orchestrator.registry["insight"]
    answer = NavigationResponse(type="guidance", message="Henderson Roofing: at risk, 12 days behind.")
    with patch.object(nav, "current_project_name", return_value=None), \
         patch.object(insight, "variance_response", return_value=answer) as mock_variance:
        response = orchestrator.dispatch(ctx("is it delayed too"))

    assert mock_variance.call_args.args[1] == "Henderson Roofing"
    assert response.type == "guidance"


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_resolved_insight_answer_remembers_project_for_later_followups(
    mock_audit, orchestrator, ctx
):
    from source.model.navigation_models import NavigationResponse

    orchestrator.llm.triage.return_value = {"agent": "insight"}
    orchestrator.llm.extract_project_name.return_value = {"project_name": "Oakwood"}
    orchestrator.conversation_memory = _memory_mock()
    insight = orchestrator.registry["insight"]
    answer = NavigationResponse(type="guidance", message="Oakwood: $22,100 committed.")
    with patch.object(insight, "variance_response", return_value=answer):
        orchestrator.dispatch(ctx("why is cost over budget on Oakwood"))

    orchestrator.conversation_memory.set_last_project.assert_called_once_with(
        "test-tenant", "test-user", "Oakwood"
    )


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_unresolved_insight_turn_does_not_remember_a_project(
    mock_audit, orchestrator, ctx
):
    # A disambiguation/fallback turn isn't a real resolution -- must not
    # poison "last discussed project" with something that was never
    # confirmed to be a real, single project match.
    orchestrator.llm.triage.return_value = {"agent": "insight"}
    orchestrator.llm.extract_project_name.return_value = {"project_name": None}
    orchestrator.conversation_memory = _memory_mock()
    orchestrator.dispatch(ctx("how's cost looking"))

    orchestrator.conversation_memory.set_last_project.assert_not_called()


# ── Triage page context (eden_router) ────────────────────────────────────────
# Regression cover for the live misroute these exist to prevent: "who all are
# active" on the customer list triaged to reporting@0.9 as "a specific project
# status", had no project to work with, asked for one, and returned an
# irrelevant answer -- because triage classified the bare utterance with no
# idea which page was on screen. See LLMClient.triage / _triage_page_context.

def test_triage_page_context_names_page_and_its_features(orchestrator, ctx):
    # Registry-driven, not a hardcoded page list: the customer list's own
    # "Status filter chips" feature is the signal that makes "active" read as
    # a filter value rather than a project's status.
    block = orchestrator._triage_page_context(ctx("who all are active", currentRoute="/customertable"))

    assert block is not None
    assert "Customers" in block
    assert "Status filter chips" in block


def test_triage_page_context_none_without_a_current_route(orchestrator, ctx):
    # No page => no tie-breaker, and triage must receive the byte-identical
    # prompt it used before this feature existed.
    assert orchestrator._triage_page_context(ctx("who all are active", currentRoute="")) is None


def test_triage_page_context_rejects_root_prefix_match_on_unknown_page(orchestrator, ctx):
    # route_by_pattern is a longest-prefix match and `home`'s pattern is "/",
    # so ANY unregistered path prefix-matches Home and would inject
    # "Page: Home / Login button" as though that were the user's real screen.
    # Wrong page context is worse than none for a tie-breaker.
    assert orchestrator._triage_page_context(ctx("who all are active", currentRoute="/not-a-real-page")) is None
    # ...but a genuine root visit still gets its context.
    assert orchestrator._triage_page_context(ctx("hello", currentRoute="/")) is not None


def test_triage_page_context_survives_a_broken_registry(orchestrator, ctx):
    # Triage is the one hop EVERY request passes through -- this enrichment
    # must degrade to None, never raise, or a registry problem takes down all
    # routing (failure-escalation: fail soft at request time).
    orchestrator.registry["navigation"].route_by_pattern = MagicMock(
        side_effect=RuntimeError("registry unavailable")
    )
    assert orchestrator._triage_page_context(ctx("who all are active", currentRoute="/customertable")) is None


@patch("source.orchestrator.agent_orchestrator.write_navigation_audit_log")
def test_eden_router_passes_page_context_to_triage(mock_audit, orchestrator, ctx):
    orchestrator.dispatch(ctx("who all are active", currentRoute="/customertable"))

    _, kwargs = orchestrator.llm.triage.call_args
    assert "Status filter chips" in kwargs["page_context"]


def test_triage_sends_page_context_as_a_second_system_message(monkeypatch):
    # NOT user content: the guardrails PII rail redacts user-role text only,
    # so a page label containing a person-shaped word would come back as
    # "<PERSON>" and corrupt the very signal being added.
    from source.utils import llm_client as llm_client_module

    client = llm_client_module.LLMClient()
    captured = {}

    def fake_parse(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop after capturing the request")

    monkeypatch.setattr(client.client.chat.completions, "parse", fake_parse)

    client.triage("who all are active", page_context="PAGE CONTEXT BLOCK")
    roles = [m["role"] for m in captured["messages"]]
    assert roles == ["system", "system", "user"]
    assert captured["messages"][1]["content"] == "PAGE CONTEXT BLOCK"
    assert captured["messages"][-1]["content"] == "who all are active"

    captured.clear()
    client.triage("who all are active")
    assert [m["role"] for m in captured["messages"]] == ["system", "user"]


# ── Compound recall: recall reference + data question in one sentence ────────
# Regression cover for the live hijack (2026-08-05 06:37:30): "what is the
# previous project cost " matched both recall regexes, never reached triage,
# and Navigation answered only WHICH project it was -- "cost" was never read
# and Reporting was never consulted.

def _memory_with_two_projects():
    mem = _memory_mock() if "_memory_mock" in globals() else MagicMock()
    mem.get_pending_goal.return_value = None
    mem.get_recent_turns.return_value = [
        {"project": "Dan Cabush, Racine-Roof Enhancement-646248"},   # earlier
        {"project": "Al Naber, Burlington-Roof Enhancement-543655"},  # current
        {"project": "Al Naber, Burlington-Roof Enhancement-543655"},  # same, collapses
    ]
    return mem


def test_compound_recall_goes_to_the_data_specialist(orchestrator, ctx):
    orchestrator.conversation_memory = _memory_with_two_projects()
    orchestrator.llm.triage.return_value = {"agent": "reporting", "confidence": 0.9}

    result = orchestrator._eden_router_node(
        {"context": ctx("what is the previous project cost ", currentRoute="/soldproduction/projectinformation")}
    )
    assert result["next_agent"] == "reporting"


def test_compound_recall_binds_the_earlier_project_not_the_current_one(orchestrator, ctx):
    # The bug's sharpest edge: the user is ON the current project's page, so
    # page context would resolve the CURRENT project. Binding by name wins
    # because entity resolution ranks an explicit name above page context.
    orchestrator.conversation_memory = _memory_with_two_projects()
    orchestrator.llm.triage.return_value = {"agent": "reporting", "confidence": 0.9}

    result = orchestrator._eden_router_node({"context": ctx(
        "what is the previous project cost ",
        currentRoute="/soldproduction/projectinformation",
        currentEntity={"type": "project", "id": "1203"},
    )})

    assert "Racine-Roof Enhancement-646248" in result["context"].utterance
    assert "Burlington" not in result["context"].utterance
    # Triage must classify the BOUND text, not the bare utterance.
    assert "Racine-Roof Enhancement-646248" in orchestrator.llm.triage.call_args[0][0]


def test_pure_recall_still_short_circuits_to_navigation(orchestrator, ctx):
    # No data-question word -- only Navigation's recent-turns walk can answer
    # "which project was it", so this path must not change.
    orchestrator.conversation_memory = _memory_with_two_projects()

    result = orchestrator._eden_router_node(
        {"context": ctx("what was the previous project we discussed")}
    )
    assert result["next_agent"] == "navigation"
    assert "context" not in result          # utterance untouched
    orchestrator.llm.triage.assert_not_called()


def test_compound_recall_with_no_earlier_project_stays_with_navigation(orchestrator, ctx):
    # Only one distinct project in history -- nothing to bind. Leave it with
    # Navigation so its own "I don't have an earlier project in our recent
    # conversation" message fires, rather than Reporting asking "which project?"
    orchestrator.conversation_memory = _memory_with_two_projects()
    orchestrator.conversation_memory.get_recent_turns.return_value = [{"project": "Only One"}]

    result = orchestrator._eden_router_node({"context": ctx("what is the previous project cost")})
    assert result["next_agent"] == "navigation"
    assert "context" not in result


def test_a_data_question_merely_containing_earlier_is_not_treated_as_recall(orchestrator, ctx):
    # "earlier" as filler must not trip the recall pair at all -- the context
    # regex needs a real recall phrase, so this goes through normal triage.
    orchestrator.conversation_memory = _memory_with_two_projects()
    orchestrator.llm.triage.return_value = {"agent": "reporting", "confidence": 0.9}

    result = orchestrator._eden_router_node(
        {"context": ctx("the invoice due earlier this month")}
    )
    assert result["next_agent"] == "reporting"
    assert "context" not in result


def test_typo_d_recall_still_short_circuits_to_navigation(orchestrator, ctx):
    # Real bug, fixed: confirmed live -- "take me to the rpevious projecct"
    # (both words typo'd) matched neither exact recall regex at all and
    # fell through to a generic "which project did you mean?" navigation
    # prompt instead of resolving via conversation history. Same pure-recall
    # shape as test_pure_recall_still_short_circuits_to_navigation, just
    # with the live typos.
    orchestrator.conversation_memory = _memory_with_two_projects()

    result = orchestrator._eden_router_node(
        {"context": ctx("take me to the rpevious projecct")}
    )
    assert result["next_agent"] == "navigation"
    assert "context" not in result
    orchestrator.llm.triage.assert_not_called()


def test_fuzzy_recall_does_not_false_positive_on_protect(orchestrator, ctx):
    # "protect" ties a real plausible typo of "project" ("porject") at an
    # identical difflib ratio (0.857) -- confirmed while tuning the fix
    # above. A marker word ("previously") plus "protect" in the same
    # sentence must NOT be treated as project recall.
    orchestrator.conversation_memory = _memory_with_two_projects()
    orchestrator.llm.triage.return_value = {"agent": "navigation", "confidence": 0.9}

    result = orchestrator._eden_router_node(
        {"context": ctx("take me to previously protect the data")}
    )
    assert result["next_agent"] == "navigation"
    orchestrator.llm.triage.assert_called_once()


# ── Soft suggestions require more than bare filler to confirm ───────────────
# _attach_page_suggestion tacks a follow-up "Would you like to see the
# Project Invoices page?" onto a resolved billing/status answer. This used
# to be treated as unconfirmable by bare filler ("sure" might be
# acknowledging the DATA ANSWER, not the offer) -- but the tail is ALWAYS
# phrased as a direct yes/no question, so there's no remaining ambiguity to
# guard against once the offer itself asks a question. Reverted after
# confirming live it made things worse: a status answer ending "...Would you
# like to see the Al Naber ... page?" -> "yes" got a non-committal brush-off
# instead of navigating -- worse than the original bug, since it now never
# let a bare "yes" confirm ANY tacked-on offer, even the unambiguous ones.

def test_soft_suggestion_confirms_on_bare_filler(orchestrator, ctx):
    # Real bug, fixed: invoice_list ("/invoice") was removed entirely (its
    # frontend page crashed on load), so a remembered "/invoice" no longer
    # resolves to any registry route and the replay silently fell back to
    # "/". Swapped to a route that still exists -- this test is only about
    # the bare-filler confirmation mechanism, not invoices specifically.
    orchestrator.conversation_memory = MagicMock()
    orchestrator.conversation_memory.get_recent_turns.return_value = [{
        "utterance": "what is the cost", "type": "guidance",
        "route": "/purchaseOrder", "label": "Purchase Orders",
        "isSoftSuggestion": True,
    }]
    result = orchestrator._resolve_affirmation("sure", ctx("sure"))
    assert result is not None
    assert result.type == "navigate"
    assert result.route == "/purchaseOrder"


def test_genuine_fallback_offer_still_confirms_on_bare_yes(orchestrator, ctx):
    # No isSoftSuggestion key -- the shape a real "did you mean X?" fallback
    # suggestion produces. Must be unaffected by the soft-suggestion guard.
    # invoice_list ("/invoice") was removed entirely -- swapped to a route
    # that still exists, same reasoning as test_soft_suggestion_confirms_on_
    # bare_filler above.
    orchestrator.conversation_memory = MagicMock()
    orchestrator.conversation_memory.get_recent_turns.return_value = [{
        "utterance": "take me to purchase orders", "type": "fallback",
        "route": "/purchaseOrder", "label": "Purchase Orders",
    }]
    result = orchestrator._resolve_affirmation("yes", ctx("yes"))
    assert result is not None
    assert result.type == "navigate"
    assert result.route == "/purchaseOrder"


def test_soft_suggestion_flag_set_only_for_guidance_with_suggestion_only(orchestrator, ctx):
    # is_soft_suggestion computed in _finalize_turn: guidance + no direct
    # route + a suggestion object -- never for navigate/fallback/disambiguation.
    from source.model.navigation_models import NavigationResponse, NavigationSuggestion

    guidance_with_suggestion = NavigationResponse(
        type="guidance", message="x",
        suggestion=NavigationSuggestion(label="Invoices", route="/invoice"),
    )
    navigate_response = NavigationResponse(type="navigate", route="/leadscustomers")

    orchestrator.conversation_memory = MagicMock()
    orchestrator.conversation_memory.get_pending_goal.return_value = None
    orchestrator._finalize_turn(ctx("x"), guidance_with_suggestion, None, False)
    kwargs = orchestrator.conversation_memory.append_turn.call_args.kwargs
    assert kwargs["is_soft_suggestion"] is True

    orchestrator.conversation_memory.append_turn.reset_mock()
    orchestrator._finalize_turn(ctx("x"), navigate_response, None, False)
    kwargs = orchestrator.conversation_memory.append_turn.call_args.kwargs
    assert kwargs["is_soft_suggestion"] is False
