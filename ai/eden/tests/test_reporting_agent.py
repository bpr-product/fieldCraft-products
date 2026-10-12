"""Unit tests for ReportingAgent — customer-safe status summaries, with both
the entity-resolver's and this agent's Mongo collections fully mocked. The
load-bearing assertion throughout: NO money fields ever appear in the query
projection or the message — that absence is the customer-safety guarantee."""
from unittest.mock import MagicMock, patch

from source.agents.reporting_agent import ReportingAgent
from source.model.navigation_models import NavigationResponse


def _make_project_cursor(docs: list[dict]):
    """entity_resolver.py's resolve_project (name tier) chains
    .find(...).sort(...) then iterates directly; list_projects chains
    .find(...).sort(...).limit(n) -- .sort() must return the same
    chainable cursor either way, or whichever call happens second silently
    yields an unrelated, unconfigured MagicMock instead of `docs`. n=0
    (Mongo's own "no limit" convention, see list_projects' own fix for the
    Python-slice-vs-Mongo-limit gap) returns every doc, matching real
    .limit(0) semantics."""
    cursor = MagicMock()
    cursor.__iter__.return_value = iter(docs)
    cursor.sort.return_value = cursor
    cursor.limit.side_effect = lambda n: iter(docs if not n else docs[:n])
    return cursor


def _mock_project_matches(docs: list[dict]):
    """Real gap, fixed: resolve_project checks orderNo FIRST via
    _resolve_by_order_no's own separate .find(...) call -- an
    unconditional single cursor here (regardless of query) would let an
    order-number lookup for a plain name like "henderson" spuriously
    "match" via this same mock, short-circuiting past the name-search tier
    these tests actually exercise. Distinguish by the presence of an
    "orderNo" key in the filter dict, mirroring test_entity_resolver.py's
    own _mock_collection."""
    collection = MagicMock()

    def find_side_effect(query, _projection):
        if "orderNo" in query:
            return _make_project_cursor([])
        return _make_project_cursor(docs)

    collection.find.side_effect = find_side_effect
    collection.count_documents.return_value = len(docs)
    return collection


def _mock_health_doc(doc: dict | None):
    collection = MagicMock()
    collection.find_one.return_value = doc
    return collection


def _mock_get_collection_by_name(docs_by_name: dict[str, dict | None]):
    """answer_from_data queries TWO different collections (project_health,
    invoice_summary) by name -- this fakes get_collection(name) to hand
    back the right canned doc for whichever name it's called with."""
    def _get(name, db_name=None):
        collection = MagicMock()
        collection.find_one.return_value = docs_by_name.get(name)
        return collection
    return _get


# ── answer_from_data: real RAG/agentic path ────────────────────────────────
# Replaces status_response/billing_response/date_response for ANY phrasing
# they weren't specifically written for. Two-part safety guarantee tested
# here: (1) internal-only project_health fields never reach the model's
# context, (2) an answer containing a figure not present in the source data
# is discarded rather than shown.


def test_answer_from_data_returns_none_without_llm_client(ctx):
    agent = ReportingAgent()  # llm_client=None by default
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ):
        result = agent.answer_from_data(ctx("what is the latest invoice"), "henderson", None, None, "what is the latest invoice")
    assert result is None


def test_answer_from_data_returns_none_without_project_named(ctx):
    llm = MagicMock()
    agent = ReportingAgent(llm_client=llm)
    result = agent.answer_from_data(ctx("what is the project status"), None, None, None, "what is the project status")
    assert result is None
    llm.answer_from_project_data.assert_not_called()


def test_answer_from_data_returns_verified_answer(ctx):
    llm = MagicMock()
    llm.answer_from_project_data.return_value = "You have 2 invoices, and $8,000 is still outstanding."
    agent = ReportingAgent(llm_client=llm)
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {"completionPct": 50.0, "committedSpend": 99999}  # internal-only field, must be stripped
    invoice_doc = {"totalOutstanding": 8000, "invoices": [{"invoiceNo": "INV-1"}, {"invoiceNo": "INV-2"}]}
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        side_effect=_mock_get_collection_by_name({
            "project_health": health_doc, "invoice_summary": invoice_doc,
        }),
    ):
        result = agent.answer_from_data(
            ctx("how many invoices and what's still owed"), "henderson", None, None,
            "how many invoices and what's still owed",
        )
    assert result is not None
    assert result.type == "guidance"
    assert "8,000" in result.message
    # The data actually sent to the model must never include committedSpend.
    sent_data = llm.answer_from_project_data.call_args.args[1]
    assert "committedSpend" not in sent_data["project"]


def test_answer_from_data_strips_invented_followup_offer(ctx):
    # Real bug, fixed: confirmed live -- despite the prompt instruction
    # against it, the model still sometimes appends its own invented
    # follow-up offer ("Would you like to see the cost breakdown page?", a
    # page that doesn't exist in the registry at all). That sentence has NO
    # backing suggestion, so confirming it with "yes" dead-ends. Number
    # verification alone can't catch this (it's not a numeric fabrication).
    llm = MagicMock()
    llm.answer_from_project_data.return_value = (
        "The project cost is $14,510.07. "
        "Would you like to see the cost breakdown page?"
    )
    agent = ReportingAgent(llm_client=llm)
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {"estimatedCost": 14510.07}
    invoice_doc = {}
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        side_effect=_mock_get_collection_by_name({
            "project_health": health_doc, "invoice_summary": invoice_doc,
        }),
    ):
        result = agent.answer_from_data(
            ctx("what is the project cost"), "henderson", None, None,
            "what is the project cost",
        )
    assert result is not None
    assert "$14,510.07" in result.message
    assert "cost breakdown page" not in result.message
    assert "?" not in result.message


def test_answer_from_data_rejects_answer_with_fabricated_figure(ctx):
    llm = MagicMock()
    # $50,000 does not appear anywhere in the real source data below.
    llm.answer_from_project_data.return_value = "You still owe $50,000 on this project."
    agent = ReportingAgent(llm_client=llm)
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {"completionPct": 50.0}
    invoice_doc = {"totalOutstanding": 8000}
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        side_effect=_mock_get_collection_by_name({
            "project_health": health_doc, "invoice_summary": invoice_doc,
        }),
    ):
        result = agent.answer_from_data(
            ctx("what's still owed"), "henderson", None, None, "what's still owed",
        )
    # Fix (PR review, Raja): a fabricated-figure rejection is no longer a
    # bare None -- it's a direct, honest guidance refusal, same fix shape
    # as the cross-domain-reason/self-referential-leak siblings (2727a8f).
    # Must NOT fall through to respond()'s billing/date/status chain.
    assert isinstance(result, NavigationResponse)
    assert result.type == "guidance"
    assert "don't have a verified figure" in result.message
    assert result.project.id == "uid-1"


def test_answer_from_data_survives_malformed_llm_response(ctx):
    llm = MagicMock()
    llm.answer_from_project_data.return_value = MagicMock()  # not a string -- must not crash
    agent = ReportingAgent(llm_client=llm)
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        side_effect=_mock_get_collection_by_name({"project_health": {}, "invoice_summary": {}}),
    ):
        result = agent.answer_from_data(ctx("status"), "henderson", None, None, "status")
    assert result is None


def test_answer_from_data_empty_answer_offer_matches_labour_permissions(ctx):
    # Real gap, fixed: confirmed live -- a Labor user (no EST.REA, no
    # INV.REA) asking "what the estimnate is over budget" (a genuinely
    # empty model answer, not in STATUS_SIGNALS/_BILLING_FALLTHROUGH_SIGNALS/
    # DATE_SIGNALS) got back "I can help with your project's overall
    # estimate or billing information instead" -- both things this exact
    # user also cannot see. Must offer only what this caller's own
    # permissions allow.
    llm = MagicMock()
    llm.answer_from_project_data.return_value = ""
    agent = ReportingAgent(llm_client=llm)
    agent.role_resolver = MagicMock()
    agent.role_resolver.is_customer.return_value = False
    agent.role_resolver.get_permissions.return_value = []  # Labor: neither EST.REA nor INV.REA
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {"estimatedCost": 14510.07}
    invoice_doc = {"totalOutstanding": 8000}
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        side_effect=_mock_get_collection_by_name({
            "project_health": health_doc, "invoice_summary": invoice_doc,
        }),
    ):
        result = agent.answer_from_data(
            ctx("what the estimnate is over budget"), "henderson", None, None,
            "what the estimnate is over budget",
        )
    assert result is not None
    assert "estimate or billing" not in result.message
    assert "schedule or status instead" in result.message


def test_answer_from_data_empty_answer_offer_grants_permitted_alternative(ctx):
    llm = MagicMock()
    llm.answer_from_project_data.return_value = ""
    agent = ReportingAgent(llm_client=llm)
    agent.role_resolver = MagicMock()
    agent.role_resolver.is_customer.return_value = False
    agent.role_resolver.get_permissions.return_value = ["INV.REA"]  # holds billing, not estimate
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {"estimatedCost": 14510.07}
    invoice_doc = {"totalOutstanding": 8000}
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        side_effect=_mock_get_collection_by_name({
            "project_health": health_doc, "invoice_summary": invoice_doc,
        }),
    ):
        result = agent.answer_from_data(
            ctx("what the estimnate is over budget"), "henderson", None, None,
            "what the estimnate is over budget",
        )
    assert result is not None
    assert "billing information instead" in result.message
    assert "overall estimate" not in result.message


def test_no_entity_name_lists_available_projects(ctx):
    agent = ReportingAgent()
    docs = [
        {"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"},
        {"projectId": "uid-2", "projectName": "Buma Retail", "status": "active"},
    ]
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ):
        result = agent.status_response(ctx("how is the project going"), None)
    assert result is not None
    assert result.type == "disambiguation"
    assert [o.label for o in result.options] == ["Henderson Roofing", "Buma Retail"]


def test_returns_none_when_no_project_match(ctx):
    agent = ReportingAgent()
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches([]),
    ):
        result = agent.status_response(ctx("status of nonexistent"), "nonexistent")
    assert result is None


def test_status_summary_contains_no_money(ctx):
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "completionPct": 68.0, "totalTaskCount": 10, "doneTaskCount": 7,
        "overdueTaskCount": 2, "scheduleRisk": "on_track", "slipDays": 0,
    }
    health_collection = _mock_health_doc(health_doc)
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        return_value=health_collection,
    ):
        result = agent.status_response(ctx("status of henderson"), "henderson")
    assert result is not None
    assert result.type == "guidance"
    assert "68% complete" in result.message
    assert "7 of 10 tasks done" in result.message
    assert "2 overdue" in result.message
    assert "on track" in result.message.lower()
    # The customer-safety guarantee, asserted both ways:
    assert "$" not in result.message
    projection = health_collection.find_one.call_args.args[1]
    assert "committedSpend" not in projection
    assert "outstandingInvoicesTotal" not in projection


def test_status_uses_last_status_timeline_entry_as_current_stage(ctx):
    # statusTimeline is stored OLDEST-first (see gold_query_reference.md) --
    # the current stage is the LAST element, never the first. Real bug this
    # closes: "what's the current status" had no stage-name answer at all
    # before this, only completion%/schedule-risk.
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "completionPct": 68.0, "totalTaskCount": 10, "doneTaskCount": 7,
        "overdueTaskCount": 2, "scheduleRisk": "on_track", "slipDays": 0,
        "statusTimeline": [
            {"changedAt": "2026-07-01T00:00:00Z", "changedBy": None,
             "changeReason": "Changed to next state", "newState": "awaitingEstimation"},
            {"changedAt": "2026-07-14T07:03:14Z", "changedBy": "Ashley",
             "changeReason": "Changed to next state", "newState": "Contract Upload"},
        ],
    }
    health_collection = _mock_health_doc(health_doc)
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        return_value=health_collection,
    ):
        result = agent.status_response(ctx("status of henderson"), "henderson")
    assert "currently in Contract Upload" in result.message
    assert "awaitingEstimation" not in result.message


def test_at_risk_schedule_reported(ctx):
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "completionPct": 40.0, "totalTaskCount": 10, "doneTaskCount": 4,
        "overdueTaskCount": 0, "scheduleRisk": "at_risk", "slipDays": 12,
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ):
        result = agent.status_response(ctx("status of henderson"), "henderson")
    assert result is not None
    assert "at risk" in result.message.lower()
    assert "12 days behind" in result.message


def test_respond_dispatches_status_for_non_billing_question(ctx):
    agent = ReportingAgent()
    with patch.object(agent, "status_response", return_value="status") as mock_status, \
         patch.object(agent, "billing_response") as mock_billing:
        result = agent.respond(ctx("how is the project going"), "henderson")
    mock_status.assert_called_once()
    mock_billing.assert_not_called()
    assert result == "status"


def test_respond_dispatches_billing_for_billing_question(ctx):
    agent = ReportingAgent()
    with patch.object(agent, "billing_response", return_value="billing") as mock_billing, \
         patch.object(agent, "status_response") as mock_status:
        result = agent.respond(ctx("how much do I owe on this project"), "henderson")
    mock_billing.assert_called_once()
    mock_status.assert_not_called()
    assert result == "billing"


def test_intent_utterance_overrides_bare_reply_for_billing_decision(ctx):
    # Real bug, fixed: a bare project-name reply continuing a "which
    # project?" thread carries none of the original question's billing
    # signal. intent_utterance lets the caller supply the ORIGINAL question
    # instead of context.utterance (the reply) for the billing-vs-status
    # decision.
    agent = ReportingAgent()
    with patch.object(agent, "billing_response", return_value="billing") as mock_billing, \
         patch.object(agent, "status_response") as mock_status:
        result = agent.respond(
            ctx("DEMO-Hillcrest Window Upgrade"), "DEMO-Hillcrest Window Upgrade",
            intent_utterance="i want to know invoice amount",
        )
    mock_billing.assert_called_once()
    mock_status.assert_not_called()
    assert result == "billing"


def test_intent_utterance_defaults_to_context_utterance_when_not_given(ctx):
    agent = ReportingAgent()
    with patch.object(agent, "billing_response") as mock_billing, \
         patch.object(agent, "status_response", return_value="status") as mock_status:
        result = agent.respond(ctx("how is the project going"), "henderson")
    mock_billing.assert_not_called()
    mock_status.assert_called_once()
    assert result == "status"


def test_cost_question_triggers_billing_view(ctx):
    # Real gap, fixed: "cost" wasn't in BILLING_SIGNALS, so a customer
    # asking "what's the cost of this project" silently got the money-free
    # status view instead of their own bill.
    agent = ReportingAgent()
    with patch.object(agent, "billing_response", return_value="billing") as mock_billing, \
         patch.object(agent, "status_response") as mock_status:
        result = agent.respond(ctx("what's the cost of this project"), "henderson")
    mock_billing.assert_called_once()
    mock_status.assert_not_called()
    assert result == "billing"


def test_respond_falls_back_to_status_when_billing_data_unavailable(ctx):
    agent = ReportingAgent()
    with patch.object(agent, "billing_response", return_value=None), \
         patch.object(agent, "status_response", return_value="status") as mock_status:
        result = agent.respond(ctx("what's my balance"), "henderson")
    mock_status.assert_called_once()
    assert result == "status"


def test_billing_response_reports_own_bill_no_internal_cost_fields(ctx):
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    invoice_doc = {"totalBilled": 50000, "totalReceived": 42000, "totalOutstanding": 8000}
    invoice_collection = _mock_health_doc(invoice_doc)
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        return_value=invoice_collection,
    ):
        result = agent.billing_response(ctx("how much do I owe"), "henderson")
    assert result is not None
    assert "$50,000" in result.message
    assert "$42,000" in result.message
    assert "$8,000" in result.message
    projection = invoice_collection.find_one.call_args.args[1]
    assert "committedSpend" not in projection


def test_billing_response_returns_none_when_no_invoice_doc(ctx):
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        return_value=_mock_health_doc(None),
    ):
        result = agent.billing_response(ctx("how much do I owe"), "henderson")
    assert result is None


def test_respond_routes_date_question_to_date_response(ctx):
    agent = ReportingAgent()
    with patch.object(agent, "date_response", return_value="dates") as mock_date, \
         patch.object(agent, "status_response") as mock_status:
        result = agent.respond(ctx("when does henderson start"), "henderson")
    mock_date.assert_called_once()
    mock_status.assert_not_called()
    assert result == "dates"


def test_respond_falls_back_to_status_when_no_schedule_row(ctx):
    agent = ReportingAgent()
    with patch.object(agent, "date_response", return_value=None), \
         patch.object(agent, "status_response", return_value="status") as mock_status:
        result = agent.respond(ctx("when does henderson start"), "henderson")
    mock_status.assert_called_once()
    assert result == "status"


def test_llm_date_classification_is_primary_even_without_keyword_match(ctx):
    # Real requirement: a paraphrase DATE_SIGNALS was never written for
    # ("how long until this wraps up") must still route to date_response
    # when the LLM classifies it as a date question -- the keyword list is
    # only a fallback, not the decision.
    llm = MagicMock()
    llm.classify_date_intent.return_value = {"is_date_question": True, "reasoning": "asks about timeline"}
    agent = ReportingAgent(llm_client=llm)
    with patch.object(agent, "answer_from_data", return_value=None), \
         patch.object(agent, "date_response", return_value="dates") as mock_date, \
         patch.object(agent, "status_response") as mock_status:
        result = agent.respond(ctx("how long until this wraps up"), "henderson")
    llm.classify_date_intent.assert_called_once()
    mock_date.assert_called_once()
    mock_status.assert_not_called()
    assert result == "dates"


def test_llm_date_classification_overrides_a_keyword_false_positive(ctx):
    # The LLM saying "not a date question" wins even if a DATE_SIGNALS
    # phrase happens to appear in the utterance -- LLM is primary, not just
    # an additive check.
    llm = MagicMock()
    llm.classify_date_intent.return_value = {"is_date_question": False, "reasoning": "asking about budget, not dates"}
    agent = ReportingAgent(llm_client=llm)
    with patch.object(agent, "answer_from_data", return_value=None), \
         patch.object(agent, "date_response") as mock_date, \
         patch.object(agent, "status_response", return_value="status") as mock_status:
        result = agent.respond(ctx("what's the target cost for this kickoff meeting"), "henderson")
    mock_date.assert_not_called()
    mock_status.assert_called_once()
    assert result == "status"


def test_date_classification_falls_back_to_keywords_when_llm_call_fails(ctx):
    llm = MagicMock()
    llm.classify_date_intent.return_value = None
    agent = ReportingAgent(llm_client=llm)
    with patch.object(agent, "answer_from_data", return_value=None), \
         patch.object(agent, "date_response", return_value="dates") as mock_date:
        result = agent.respond(ctx("when does henderson start"), "henderson")
    llm.classify_date_intent.assert_called_once()
    mock_date.assert_called_once()
    assert result == "dates"


def test_date_response_reports_start_and_end_dates(ctx):
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "jobStartDate": "2028-03-13 09:30:00+00:00", "actualJobStartDate": None,
        "jobEndDate": "2028-03-15 13:20:00+00:00", "actualJobEndDate": None,
    }
    health_collection = _mock_health_doc(health_doc)
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        return_value=health_collection,
    ):
        result = agent.date_response(ctx("when does henderson start"), "henderson")
    assert result is not None
    assert result.type == "guidance"
    assert "March 13, 2028" in result.message
    assert "March 15, 2028" in result.message
    assert "planned to start" in result.message
    assert "targeted to finish" in result.message


def test_date_response_prefers_actual_over_planned_dates(ctx):
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "jobStartDate": "2028-03-13 09:30:00+00:00", "actualJobStartDate": "2028-03-14 08:00:00+00:00",
        "jobEndDate": "2028-03-15 13:20:00+00:00", "actualJobEndDate": "2028-03-16 17:00:00+00:00",
    }
    health_collection = _mock_health_doc(health_doc)
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        return_value=health_collection,
    ):
        result = agent.date_response(ctx("when does henderson start"), "henderson")
    assert "started March 14, 2028" in result.message
    assert "finished March 16, 2028" in result.message


def test_date_response_returns_none_when_no_schedule_data(ctx):
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "jobStartDate": None, "actualJobStartDate": None,
        "jobEndDate": None, "actualJobEndDate": None,
    }
    health_collection = _mock_health_doc(health_doc)
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        return_value=health_collection,
    ):
        result = agent.date_response(ctx("when does henderson start"), "henderson")
    assert result is None


def test_date_response_flags_end_before_start_as_inconsistent(ctx):
    # Real, reproduced source-data defect: project_id=1129/tenant tn88351775
    # has had jobEndDate before jobStartDate on the same day since the
    # record was created -- confirmed via direct query that Silver/Gold
    # faithfully replicated a source Postgres defect, not a pipeline bug.
    # date_response must not present this as a confident answer.
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "jobStartDate": "2028-03-13 12:00:00+00:00", "actualJobStartDate": None,
        "jobEndDate": "2028-03-13 08:25:00+00:00", "actualJobEndDate": None,
    }
    health_collection = _mock_health_doc(health_doc)
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        return_value=health_collection,
    ):
        result = agent.date_response(ctx("when does henderson start"), "henderson")
    assert result is not None
    assert "before the start date" in result.message.lower()
    assert "unverified" in result.message.lower()


def test_date_response_does_not_flag_zero_duration_as_inconsistent(ctx):
    # end == start (to the second) is a VALID zero-duration record --
    # only end < start is a real defect. Must not false-positive here.
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "jobStartDate": "2028-03-13 09:00:00+00:00", "actualJobStartDate": None,
        "jobEndDate": "2028-03-13 09:00:00+00:00", "actualJobEndDate": None,
    }
    health_collection = _mock_health_doc(health_doc)
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        return_value=health_collection,
    ):
        result = agent.date_response(ctx("when does henderson start"), "henderson")
    assert result is not None
    assert "before the start date" not in result.message.lower()


def test_missing_completion_pct_still_summarizes(ctx):
    # completionPct is only ~8% populated in real data -- a doc without it
    # must still produce a useful summary, not crash or go blank.
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "completionPct": None, "totalTaskCount": 5, "doneTaskCount": 1,
        "overdueTaskCount": 0, "scheduleRisk": "unknown", "slipDays": 0,
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ):
        result = agent.status_response(ctx("status of henderson"), "henderson")
    assert result is not None
    assert "1 of 5 tasks done" in result.message
    assert "not yet available" in result.message.lower()


# ── BILLING_SIGNALS disagreement audit (instrumentation only) ───────────────
# BILLING_SIGNALS is a closed keyword list, the same shape as the old
# top-level router that provably misrouted unseen phrasing -- these tests
# verify the audit wiring (sampled LLM check, logged disagreement), never
# that it changes what respond() actually returns.

def test_no_llm_client_skips_audit_silently(ctx):
    # Agent-level tests construct ReportingAgent() with no llm_client --
    # must not crash, must not attempt any call.
    agent = ReportingAgent()
    assert agent.llm_client is None
    with patch.object(agent, "status_response", return_value="status"):
        result = agent.respond(ctx("how is the project going"), "henderson")
    assert result == "status"


def test_audit_skipped_outside_sample_rate(ctx):
    agent = ReportingAgent(llm_client=MagicMock())
    with patch("source.agents.reporting_agent.random.random", return_value=0.99), \
         patch.object(agent, "answer_from_data", return_value=None), \
         patch.object(agent, "status_response", return_value="status"):
        agent.respond(ctx("how is the project going"), "henderson")
    agent.llm_client.classify_billing_intent.assert_not_called()


def test_audit_runs_within_sample_rate_and_logs_disagreement(ctx, caplog):
    llm_client = MagicMock()
    llm_client.classify_billing_intent.return_value = {
        "is_billing": True, "reasoning": "sounds like a billing question",
    }
    agent = ReportingAgent(llm_client=llm_client)
    with patch("source.agents.reporting_agent.random.random", return_value=0.0), \
         patch.object(agent, "answer_from_data", return_value=None), \
         patch.object(agent, "status_response", return_value="status"), \
         caplog.at_level("WARNING"):
        # "how is the project going" -> keyword says NOT billing; LLM (mocked
        # above) says billing -> a real disagreement, must be logged.
        agent.respond(ctx("how is the project going"), "henderson")
    llm_client.classify_billing_intent.assert_called_once_with("how is the project going")
    assert any("BILLING_SIGNALS disagreement" in r.message for r in caplog.records)


def test_audit_no_log_when_llm_agrees(ctx, caplog):
    llm_client = MagicMock()
    llm_client.classify_billing_intent.return_value = {
        "is_billing": False, "reasoning": "asks about progress",
    }
    agent = ReportingAgent(llm_client=llm_client)
    with patch("source.agents.reporting_agent.random.random", return_value=0.0), \
         patch.object(agent, "answer_from_data", return_value=None), \
         patch.object(agent, "status_response", return_value="status"), \
         caplog.at_level("WARNING"):
        agent.respond(ctx("how is the project going"), "henderson")
    assert not any("BILLING_SIGNALS disagreement" in r.message for r in caplog.records)


def test_audit_failure_does_not_break_respond(ctx):
    llm_client = MagicMock()
    llm_client.classify_billing_intent.side_effect = Exception("model down")
    agent = ReportingAgent(llm_client=llm_client)
    with patch("source.agents.reporting_agent.random.random", return_value=0.0), \
         patch.object(agent, "answer_from_data", return_value=None), \
         patch.object(agent, "status_response", return_value="status"):
        result = agent.respond(ctx("how is the project going"), "henderson")
    assert result == "status"


# --- New reasoning requirement: estimate-change / schedule-move / margin ---
# See EDEN_CustomerSupport_Reasoning_Constraints.md. This is the customer/
# salesperson-facing path -- the load-bearing assertion for Q1 specifically:
# NO dollar figures ever appear in the message (materialCost stays exactly
# as internal-only as this file's own _PROJECT_HEALTH_INTERNAL_ONLY_FIELDS
# already treats it), unlike Insight's version of the same question.

def _mock_role_collection(roles: list[str] | None, permissions: list[str] | None = None):
    """`permissions` defaults to None so existing tests that never set it
    keep the exact same behavior (missing key == empty for get_permissions,
    same as before this param existed)."""
    collection = MagicMock()
    if roles is None:
        collection.find_one.return_value = None
    else:
        doc = {"roles": roles}
        if permissions is not None:
            doc["permissions"] = permissions
        collection.find_one.return_value = doc
    return collection


def test_estimate_change_customer_gets_cause_only_no_dollar_figures(ctx):
    """Per explicit user direction: customer/salesperson get the cause,
    never the materialCost delta -- contrast with Insight's
    test_estimate_change_high_confidence_includes_figures."""
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "estimateHistory": [
            {"version": 1, "isCurrent": False, "confidence": None, "reason": None},
            {
                "version": 2, "isCurrent": True, "confidence": "high",
                "reason": "Material unit price increased - receipt captured for OC TruDef Duration Lifetime",
                "delta": {"materialCost": 500},
            },
        ]
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ):
        result = agent.respond(ctx("why did the estimate change on henderson"), "henderson")
    assert result is not None
    assert result.type == "guidance"
    assert "receipt captured" in result.message
    assert "$" not in result.message
    assert "500" not in result.message


def test_estimate_change_no_confidence_gives_honest_fallback(ctx):
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "estimateHistory": [
            {"version": 1, "isCurrent": False, "confidence": None, "reason": None},
            {"version": 2, "isCurrent": True, "confidence": "none", "reason": None},
        ]
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ):
        result = agent.respond(ctx("why did the estimate change on henderson"), "henderson")
    assert result is not None
    assert "don't have a confirmed reason" in result.message


def test_schedule_change_low_confidence_is_hedged_for_customer(ctx):
    """Constraint #36: hedged wording applies regardless of role -- dates
    aren't cost-sensitive, but causation still isn't provable."""
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "scheduleRevisions": [
            {"version": 1, "isCurrent": False, "confidence": None, "reason": None, "comment": None},
            {
                "version": 2, "isCurrent": True, "confidence": "low",
                "reason": "Crew became available earlier than planned",
                "comment": "Start moved earlier by 2 day(s) -- crew finished a prior job ahead of schedule",
            },
        ]
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ):
        result = agent.respond(ctx("why did my start date move on henderson"), "henderson")
    assert result is not None
    assert "crew finished a prior job" in result.message
    assert "confirm the exact reason" in result.message


def test_estimate_change_no_confidence_customer_still_gets_no_figures_even_with_delta(ctx):
    """Overrides the constraints doc's original no-confidence sample
    (which showed a dollar figure) for the customer path specifically --
    the later, more specific "cause only, no dollar figures" decision
    wins for Reporting even when delta data is available, unlike Insight's
    equivalent test which DOES show the figure.

    Real bug, fixed: this used to assert the flat "I don't have a
    confirmed reason on file" text -- discarding the bucket-movement fact
    (materialCost moved $2,000 here) even though that's real, computed
    data independent of bomReconciles. Reporting now gets the bucket NAME
    (still no dollar figure, still customer-safe) instead of nothing."""
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "estimateHistory": [
            {
                "version": 4, "isCurrent": False, "confidence": None, "reason": None,
                "snapshot": {"totalCost": 2905.63, "materialCost": 2000},
            },
            {
                "version": 6, "isCurrent": True, "confidence": None, "reason": None,
                "snapshot": {"totalCost": 5085.63, "materialCost": 4000},
                "delta": {"totalCost": 2180, "materialCost": 2000},
            },
        ]
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ):
        result = agent.respond(ctx("why did the estimate change on henderson"), "henderson")
    assert result is not None
    assert "$" not in result.message
    assert "material cost" in result.message
    assert "can't confirm the exact cause" in result.message


def test_margin_customer_is_refused(ctx):
    """Constraint #32/#33: this IS the path a real customer/salesperson
    reaches (Reporting is customer-safe by construction) -- the load-
    bearing enforcement point for the demo's actual test case. Patches
    insight_agent.get_collection, not reporting_agent's: since the
    Priority-1 consolidation, resolve_margin_response (in insight_agent.py)
    is the ONLY place that queries margin, for either agent."""
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {"estimatedCost": 46000, "margin": 5000}
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ), patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_role_collection(["customer"]),
    ):
        result = agent.respond(ctx("what is the profit margin on henderson"), "henderson")
    assert result is not None
    assert "not able to share margin" in result.message
    assert "5,000" not in result.message
    assert "46,000" not in result.message


def test_margin_admin_via_reporting_sees_figure(ctx):
    """Defensive: if triage ever routes an admin's margin question through
    Reporting instead of Insight, the answer must still be correct -- now
    structurally guaranteed rather than merely defensive, since both
    agents delegate to the exact same resolve_margin_response function."""
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {"estimatedCost": 46000, "margin": 5000}
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ), patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_role_collection(["admin"], permissions=["EST.MOD"]),
    ):
        result = agent.respond(ctx("what is the profit margin on henderson"), "henderson")
    assert result is not None
    assert "$5,000" in result.message


def test_margin_question_with_no_project_disambiguates(ctx):
    """Consistent with billing_response/status_response: a project-related
    question naming no project asks "which project?" rather than
    silently aggregating or erroring."""
    agent = ReportingAgent()
    docs = [
        {"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"},
        {"projectId": "uid-2", "projectName": "Buma Retail", "status": "active"},
    ]
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ):
        result = agent.respond(ctx("what is the profit margin"), None)
    assert result is not None
    assert result.type == "disambiguation"


# ── RAG domain grounding: a cited cause must belong to the right field ──────
# answer_from_data's only pre-existing check (_number_tokens) verifies FIGURES
# -- structurally blind to a non-numeric cross-domain misattribution. Confirmed
# live: "why the estimation changed?" (at the time, a phrasing
# _is_estimate_change_question's fixed "why did/is/has/does" + exact-substring
# "estimate" gate missed, so it fell through to this RAG path instead of the
# dedicated _estimate_change_response) got answered "due to the reason
# 'SAVE_TASKS' in the second revision" -- SAVE_TASKS is scheduleRevisions[].
# reason, and estimateHistory/estimateSummary are excluded from this data
# entirely (_PROJECT_HEALTH_INTERNAL_ONLY_FIELDS), so the model had zero real
# estimate-domain data and improvised using the only reason-shaped text it
# had. _is_estimate_change_question's gate was since widened (see
# TestWidenedChangeQuestionGate below) to also catch this phrasing -- both
# layers now agree, defense in depth rather than either alone.

from source.agents.reporting_agent import (  # noqa: E402
    _find_ungrounded_cross_domain_reason,
    _is_estimate_change_question,
    _is_schedule_change_question,
    _mentions_estimate_topic,
    _question_reason_domain,
)


class TestRagDomainGrounding:
    def test_question_reason_domain_recognizes_both_reasoning_topics(self):
        assert _question_reason_domain("why the estimation changed?") == "estimate"
        assert _question_reason_domain("why did the estimate price change?") == "estimate"
        assert _question_reason_domain("why did the tentative start date move?") == "schedule"
        assert _question_reason_domain("what is the cost") is None

    def test_schedule_reason_cited_for_an_estimate_question_is_rejected(self):
        # The exact live shape: SAVE_TASKS only exists under scheduleRevisions.
        data = {"project": {"scheduleRevisions": [{"reason": "SAVE_TASKS", "isCurrent": False}]}}
        answer = "The estimation changed due to the reason \"SAVE_TASKS\" in the second revision."
        assert _find_ungrounded_cross_domain_reason(answer, data, "estimate") == "SAVE_TASKS"

    def test_a_reason_grounded_in_its_own_domain_is_not_rejected(self):
        data = {"project": {"scheduleRevisions": [{"reason": "Recurring Auto Rescheduling", "isCurrent": True}]}}
        answer = "Your schedule moved because of Recurring Auto Rescheduling."
        assert _find_ungrounded_cross_domain_reason(answer, data, "schedule") is None

    def test_no_detected_domain_skips_the_check_entirely(self):
        data = {"project": {"scheduleRevisions": [{"reason": "SAVE_TASKS", "isCurrent": False}]}}
        assert _find_ungrounded_cross_domain_reason("SAVE_TASKS mentioned here", data, None) is None


def test_answer_from_data_rejects_the_live_estimate_schedule_confusion(ctx):
    llm = MagicMock()
    llm.answer_from_project_data.return_value = (
        "The estimation changed due to the reason \"SAVE_TASKS\" in the second revision."
    )
    agent = ReportingAgent(llm_client=llm)
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {"scheduleRevisions": [{"reason": "SAVE_TASKS", "isCurrent": False}]}
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        side_effect=_mock_get_collection_by_name({
            "project_health": health_doc, "invoice_summary": {},
        }),
    ):
        result = agent.answer_from_data(
            ctx("why the estimation changed?"), "henderson", None, None,
            "why the estimation changed?",
        )
    # Fix (PR review, Raja): a cross-domain-reason rejection is no longer a
    # bare None -- it's a direct, honest guidance refusal, matching the
    # excluded-field rejection's own db4066f fix. Must NOT fall through to
    # respond()'s billing/date/status chain, which would answer a different
    # question than the one asked.
    assert isinstance(result, NavigationResponse)
    assert result.type == "guidance"
    assert "confirmed reason on file" in result.message
    assert "estimate" in result.message.lower()
    assert result.project.id == "uid-1"


def test_answer_from_data_cross_domain_refusal_uses_schedule_phrasing_for_a_schedule_question(ctx):
    # Same rejection path, opposite direction -- a schedule question citing a
    # real STATUS-domain reason instead of a schedule one. Confirms the
    # refusal's wording picks "schedule", not a hardcoded "estimate".
    llm = MagicMock()
    llm.answer_from_project_data.return_value = 'Your schedule moved because of "Contract Approved".'
    agent = ReportingAgent(llm_client=llm)
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {"statusTimeline": [{"changeReason": "Contract Approved", "newState": "Contract Approval"}]}
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        side_effect=_mock_get_collection_by_name({
            "project_health": health_doc, "invoice_summary": {},
        }),
    ):
        result = agent.answer_from_data(
            ctx("why did the schedule change?"), "henderson", None, None,
            "why did the schedule change?",
        )
    assert isinstance(result, NavigationResponse)
    assert result.type == "guidance"
    assert "confirmed reason on file" in result.message
    assert "schedule" in result.message.lower()


def test_answer_from_data_rejects_a_fabricated_figure_with_an_honest_refusal(ctx):
    # Fix (PR review, Raja): 2727a8f fixed the cross-domain-reason and
    # self-referential-leak siblings of this exact "return None -> falls
    # through to an unrelated deterministic answer" bug, but missed this
    # third one -- the numeric-fabrication guard. A figure the model
    # invented (not present anywhere in source data) must return an honest
    # refusal directly, not None.
    llm = MagicMock()
    llm.answer_from_project_data.return_value = "The total outstanding balance is $99,999."
    agent = ReportingAgent(llm_client=llm)
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    invoice_doc = {"totalOutstanding": 14800.27}
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        side_effect=_mock_get_collection_by_name({
            "project_health": {}, "invoice_summary": invoice_doc,
        }),
    ):
        result = agent.answer_from_data(
            ctx("what is the outstanding balance"), "henderson", None, None,
            "what is the outstanding balance",
        )
    assert isinstance(result, NavigationResponse)
    assert result.type == "guidance"
    assert "don't have a verified figure" in result.message
    assert result.project.id == "uid-1"


class TestWidenedChangeQuestionGate:
    # _is_estimate_change_question/_is_schedule_change_question used to
    # require one of a FIXED four phrases ("why did/is/has/does") plus, for
    # estimate, the EXACT substring "estimate". Confirmed live: "why the
    # estimation changed?" matched neither requirement and fell through to
    # the general RAG path (see TestRagDomainGrounding above for what that
    # cost). Widened to a "why" + change/move/difference-stem pairing
    # (order-agnostic, no fixed phrase list) plus a stem match on "estimat"/
    # "schedul" that covers every real inflection.

    def test_natural_paraphrases_now_reach_the_dedicated_builder(self):
        assert _is_estimate_change_question("why the estimation changed?")
        assert _is_estimate_change_question("why the estimate changed")
        assert _is_estimate_change_question("why is the estimate different")
        assert _is_schedule_change_question("why has the schedule shifted")

    def test_original_fixed_phrases_still_match(self):
        assert _is_estimate_change_question("why did the estimate price change?")
        assert _is_schedule_change_question("why did the tentative start date move?")

    def test_a_current_value_question_is_not_mistaken_for_a_change_question(self):
        # "why is cost over budget" asks about the CURRENT state, not a
        # change -- must stay with the existing variance_response path, not
        # get diverted into Q1's change-reasoning builder.
        assert not _is_estimate_change_question("why is cost over budget")

    def test_topic_alone_without_why_does_not_match(self):
        assert not _is_estimate_change_question("what is the estimate")

    def test_why_alone_without_a_relevant_topic_does_not_match(self):
        assert not _is_estimate_change_question("why did you email me")
        assert not _is_schedule_change_question("why did you email me")


class TestFuzzyTypoTolerance:
    # Confirmed live: "why the estiamtion changed for 102380" (transposed
    # letters) missed the exact-substring/stem check entirely, fell through
    # to a generic status answer with no reasoning_audit entry, and
    # separately let the voice rephrase fabricate an unchecked cause.

    def test_the_live_typo_now_reaches_the_dedicated_builder(self):
        assert _is_estimate_change_question("why the estiamtion changed for 102380")
        assert _question_reason_domain("why the estiamtion changed for 102380") == "estimate"

    def test_schedule_typo_also_caught(self):
        assert _is_schedule_change_question("why did the scedule move")

    def test_short_words_are_not_fuzzy_matched(self):
        # len(word) < 6 guard -- must not treat an unrelated short word as a
        # near-match to "estimate"/"schedule" and false-positive. Calls the
        # topic function directly -- _is_estimate_change_question would pass
        # this for the wrong reason (no change-verb in "end" either).
        assert not _mentions_estimate_topic("why did it end")

    def test_estimator_the_job_title_is_not_mistaken_for_estimate(self):
        # Real risk in this domain: "estimator" is a genuine job title, not
        # just an unlikely edge case, and fuzzy-matched at cutoff 0.8
        # (ratio 0.824) before the cutoff was raised to 0.85. Calls the
        # topic function directly for the same reason as the test above.
        assert not _mentions_estimate_topic("why did the estimator leave")
        assert not _mentions_estimate_topic("the estimators reviewed it")


# ── Three live bugs from one log window ─────────────────────────────────────
from source.agents.reporting_agent import (  # noqa: E402
    _answer_discusses_excluded_field,
    _SELF_REFERENTIAL_RE,
)


class TestRagPiiPlaceholderRejected:
    # Fix 1: answer_from_project_data used to put `data` in the USER role,
    # the same mistake already fixed in 4 other calls this session -- the
    # gateway's Presidio rail redacts user-role content only, so a real
    # project name (which IS a person name) came back as <PERSON> with no
    # guard to catch it. Confirmed live: 'The project ... is titled
    # "<PERSON>, Burlington-Roof Enhancement-543655"...' shipped verbatim.

    def test_leaked_placeholder_is_rejected(self, ctx):
        llm = MagicMock()
        llm.answer_from_project_data.return_value = (
            'The project with order number 543655 is titled "<PERSON>, '
            'Burlington-Roof Enhancement-543655" and is in the "Payment" status.'
        )
        agent = ReportingAgent(llm_client=llm)
        docs = [{"projectId": "uid-1", "projectName": "Al Naber", "status": "active"}]
        with patch(
            "source.resolvers.entity_resolver.get_collection",
            return_value=_mock_project_matches(docs),
        ), patch(
            "source.agents.reporting_agent.get_collection",
            side_effect=_mock_get_collection_by_name({"project_health": {}, "invoice_summary": {}}),
        ):
            result = agent.answer_from_data(ctx("tell me about it"), "al naber", None, None, "tell me about it")
        # Caught by the numeric-fabrication guard (the order number "543655"
        # isn't in this test's empty source data) -- same honest-refusal fix
        # as the other rejection paths, not a bare None.
        assert isinstance(result, NavigationResponse)
        assert result.type == "guidance"
        assert "don't have a verified figure" in result.message

    def test_data_rides_in_the_system_role_not_user(self, monkeypatch):
        from source.utils import llm_client as m
        client = m.LLMClient()
        captured = {}
        monkeypatch.setattr(
            client.client.chat.completions, "create",
            lambda **kw: (captured.update(kw) or (_ for _ in ()).throw(RuntimeError("stop"))),
        )
        try:
            client.answer_from_project_data("what is the status", {"project": {"projectName": "Al Naber"}})
        except RuntimeError:
            pass
        roles = [msg["role"] for msg in captured["messages"]]
        assert roles == ["system", "system", "user"]
        assert "Al Naber" in captured["messages"][1]["content"]
        assert "Al Naber" not in captured["messages"][-1]["content"]


class TestRagExcludedFieldRejected:
    # Fix 2: materialCost/toolCost are in _PROJECT_HEALTH_INTERNAL_ONLY_FIELDS
    # -- never sent to the model at all. Confirmed live: asked about both,
    # the model stated "$194.62" for BOTH; neither matched the real values,
    # but the number happened to appear elsewhere in the document
    # (unrelated field), which is all the numeric check verifies.

    def test_fabricated_figure_for_an_excluded_field_is_rejected(self):
        data = {"project": {"completionPct": 20.0}, "billing": {}}
        answer = "The tool cost is $194.62 and the material cost is $194.62."
        assert _answer_discusses_excluded_field(answer.lower(), data) in ("toolCost", "materialCost")

    def test_a_present_field_is_not_flagged(self):
        # completionPct is customer-safe and genuinely present -- must not
        # be treated as an excluded-field discussion.
        data = {"project": {"completionPct": 20.0}, "billing": {}}
        assert _answer_discusses_excluded_field("the project is 20% complete", data) is None

    def test_full_answer_from_data_rejects_the_live_case(self, ctx):
        llm = MagicMock()
        llm.answer_from_project_data.return_value = (
            "The tool cost is $194.62 and the material cost is $194.62."
        )
        agent = ReportingAgent(llm_client=llm)
        docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
        # completionPct present (customer-safe) and, matching the live case,
        # coincidentally holds the SAME figure the model fabricated for the
        # excluded fields -- so the numeric-fabrication check (which only
        # asks "does this digit-string appear anywhere in the data") passes
        # it through, and it's the excluded-field check specifically that
        # must catch it. materialCost/toolCost are absent from this dict
        # entirely (excluded before it's even built, same as production).
        health_doc = {"completionPct": 194.62}
        with patch(
            "source.resolvers.entity_resolver.get_collection",
            return_value=_mock_project_matches(docs),
        ), patch(
            "source.agents.reporting_agent.get_collection",
            side_effect=_mock_get_collection_by_name({"project_health": health_doc, "invoice_summary": {}}),
        ):
            result = agent.answer_from_data(
                ctx("what is the tool cost and material cost"), "henderson", None, None,
                "what is the tool cost and material cost",
            )
        # Fix 4 (this commit): an excluded-field rejection is no longer a
        # bare None -- it's a direct, honest guidance refusal, so it must
        # come back as a NavigationResponse, not fall through to None (and
        # from there to respond()'s billing/date/status fallback chain).
        assert isinstance(result, NavigationResponse)
        assert result.type == "guidance"
        assert "not able to share" in result.message
        assert result.project.id == "uid-1"
        assert result.project.name == "Henderson Roofing"

    def test_material_price_phrasing_is_also_caught(self):
        # Real bug, fixed: confirmed live -- "is it related to materials
        # price?" was affirmed with a fabricated "yes" (plus a fabricated
        # attribution to a person pulled from an unrelated field,
        # project.createdBy) even though materialCost is excluded from the
        # model's context entirely and evidence.priceChangeDetected is
        # explicitly false in the real data. _INTERNAL_FIELD_TOPIC_PHRASES
        # only had "cost"-suffixed variants, so "price" phrasing slipped
        # through unrejected.
        data = {"project": {"completionPct": 16.67, "createdBy": "Jon Demet"}, "billing": {}}
        answer = 'The change was made by "Jon," and it is related to materials price.'
        assert _answer_discusses_excluded_field(answer.lower(), data) == "materialCost"


class TestExcludedFieldRefusalDoesNotFallThroughToBilling:
    # Fix 4: returning None from the excluded-field check (fix 2) sent the
    # question back through respond()'s billing/date/status fallback chain,
    # which happily answered a DIFFERENT question. Confirmed live: "what is
    # the material cost" produced "The project has been billed $19,657..."
    # -- topically unrelated, and worse, reads as though the question WAS
    # answered. respond() must now return the honest refusal directly.

    def test_material_cost_question_gets_a_refusal_not_a_billing_answer(self, ctx):
        llm = MagicMock()
        llm.answer_from_project_data.return_value = "The material cost is $194.62."
        agent = ReportingAgent(llm_client=llm)
        docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
        # completionPct coincidentally equals the fabricated figure so the
        # generic numeric-fabrication check passes it through -- same
        # reasoning as TestRagExcludedFieldRejected above -- leaving the
        # excluded-field check as the one that must catch it.
        health_doc = {"completionPct": 194.62}
        invoice_doc = {"totalBilled": 19657.0}
        with patch(
            "source.resolvers.entity_resolver.get_collection",
            return_value=_mock_project_matches(docs),
        ), patch(
            "source.agents.reporting_agent.get_collection",
            side_effect=_mock_get_collection_by_name(
                {"project_health": health_doc, "invoice_summary": invoice_doc}
            ),
        ):
            result = agent.respond(
                ctx("what is the material cost"), "henderson", None,
                "what is the material cost",
            )
        assert result is not None
        assert result.type == "guidance"
        assert "not able to share" in result.message
        assert "19,657" not in result.message
        assert "billed" not in result.message.lower()


class TestEmptyRagAnswerRefusalDoesNotFallThroughToStatus:
    # Real bug, fixed: confirmed live -- "what is the material used for this
    # project" (materialCost correctly excluded from the RAG data) got the
    # model to produce a genuinely EMPTY response instead of the honest "I
    # don't have that" sentence the prompt asks for. answer_from_project_data
    # used to collapse that empty text into the exact same `None` as a true
    # call failure, so respond() couldn't tell "the model tried and had
    # nothing to say" apart from "the call itself failed" -- both fell
    # through the ENTIRE billing/date/status_response chain to an unrelated
    # status blurb that read as though the material question was answered.

    def test_empty_answer_gets_a_refusal_not_a_status_answer(self, ctx):
        llm = MagicMock()
        llm.answer_from_project_data.return_value = ""
        agent = ReportingAgent(llm_client=llm)
        docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
        health_doc = {
            "completionPct": 70.18, "totalTaskCount": 57, "doneTaskCount": 40,
            "overdueTaskCount": 0, "scheduleRisk": "on_track", "slipDays": 0,
        }
        with patch(
            "source.resolvers.entity_resolver.get_collection",
            return_value=_mock_project_matches(docs),
        ), patch(
            "source.agents.reporting_agent.get_collection",
            side_effect=_mock_get_collection_by_name(
                {"project_health": health_doc, "invoice_summary": {}}
            ),
        ):
            result = agent.respond(
                ctx("what is the material used for this project"), "henderson", None,
                "what is the material used for this project",
            )
        assert result is not None
        assert result.type == "guidance"
        assert "don't have that" in result.message
        assert "70%" not in result.message
        assert "tasks done" not in result.message

    def test_none_answer_still_falls_through_to_status(self, ctx):
        # A true call failure (exception/malformed output) must still defer
        # to the old deterministic chain -- only an EMPTY string (call
        # succeeded, nothing to say) gets the direct refusal above.
        llm = MagicMock()
        llm.answer_from_project_data.return_value = None
        agent = ReportingAgent(llm_client=llm)
        docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
        health_doc = {
            "completionPct": 70.18, "totalTaskCount": 57, "doneTaskCount": 40,
            "overdueTaskCount": 0, "scheduleRisk": "on_track", "slipDays": 0,
        }
        with patch(
            "source.resolvers.entity_resolver.get_collection",
            return_value=_mock_project_matches(docs),
        ), patch(
            "source.agents.reporting_agent.get_collection",
            side_effect=_mock_get_collection_by_name(
                {"project_health": health_doc, "invoice_summary": {}}
            ),
        ):
            result = agent.respond(
                ctx("what is the material used for this project"), "henderson", None,
                "what is the material used for this project",
            )
        assert result is not None
        assert "70%" in result.message


class TestEmptyRagAnswerForStatusQuestionFallsThrough:
    # Real bug, fixed: confirmed live -- "what is the project status" and
    # "what is the status of project X" also produced an empty LLM answer
    # (same shape as the material-used case above), but unlike that case
    # status_response CAN answer correctly from the same customer-safe
    # fields (completionPct/statusTimeline/scheduleRisk) -- live-verified
    # against real Gold data for two different projects. The unconditional
    # empty-answer refusal above was blocking that correct answer from ever
    # running. STATUS_SIGNALS is deliberately an allowlist, not a blocklist
    # keyed off the excluded-field phrases -- see reporting_agent.py's
    # comment for why a blocklist was tried first and rejected (it broke
    # TestEmptyRagAnswerRefusalDoesNotFallThroughToStatus above).

    def test_empty_answer_for_a_status_question_falls_through(self, ctx):
        llm = MagicMock()
        llm.answer_from_project_data.return_value = ""
        agent = ReportingAgent(llm_client=llm)
        docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
        health_doc = {
            "completionPct": 70.18, "totalTaskCount": 57, "doneTaskCount": 40,
            "overdueTaskCount": 0, "scheduleRisk": "on_track", "slipDays": 0,
        }
        with patch(
            "source.resolvers.entity_resolver.get_collection",
            return_value=_mock_project_matches(docs),
        ), patch(
            "source.agents.reporting_agent.get_collection",
            side_effect=_mock_get_collection_by_name(
                {"project_health": health_doc, "invoice_summary": {}}
            ),
        ):
            result = agent.respond(
                ctx("what is the project status"), "henderson", None,
                "what is the project status",
            )
        assert result is not None
        assert "70%" in result.message
        assert "don't have that" not in result.message

    def test_empty_answer_for_material_used_still_gets_the_refusal(self, ctx):
        # Same repro phrasing as TestEmptyRagAnswerRefusalDoesNotFallThrough
        # ToStatus -- re-asserted here to pin the allowlist boundary: a
        # status-shaped utterance falls through, this one still must not.
        llm = MagicMock()
        llm.answer_from_project_data.return_value = ""
        agent = ReportingAgent(llm_client=llm)
        docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
        health_doc = {
            "completionPct": 70.18, "totalTaskCount": 57, "doneTaskCount": 40,
            "overdueTaskCount": 0, "scheduleRisk": "on_track", "slipDays": 0,
        }
        with patch(
            "source.resolvers.entity_resolver.get_collection",
            return_value=_mock_project_matches(docs),
        ), patch(
            "source.agents.reporting_agent.get_collection",
            side_effect=_mock_get_collection_by_name(
                {"project_health": health_doc, "invoice_summary": {}}
            ),
        ):
            result = agent.respond(
                ctx("what is the material used for this project"), "henderson", None,
                "what is the material used for this project",
            )
        assert result is not None
        assert "don't have that" in result.message
        assert "70%" not in result.message


class TestEmptyRagAnswerForBillingAndDateQuestionFallsThrough:
    # Real bug, fixed: confirmed live -- "what is the invoiced amount" and
    # "what is the start date" produced the SAME empty-LLM-answer shape as
    # the material-used/status cases above, but billing_response/
    # date_response sit right below in this same fallback chain and read
    # real, populated fields (project_health.outstandingInvoicesTotal
    # confirmed =2449.0 live; jobStartDate/jobEndDate are date_response's
    # own documented fields) -- the unconditional refusal was blocking
    # both from ever running, same as the status case STATUS_SIGNALS
    # already fixed. _BILLING_FALLTHROUGH_SIGNALS deliberately excludes
    # "cost" so this must NOT reopen the material-used regression above.

    def test_empty_answer_for_invoiced_amount_falls_through(self, ctx, project_match):
        llm = MagicMock()
        llm.answer_from_project_data.return_value = ""
        # Force the deterministic fallback template, not an uncontrolled
        # MagicMock message -- compose_billing_message is a real LLM call
        # this test isn't exercising.
        llm.compose_billing_message.return_value = None
        agent = ReportingAgent(llm_client=llm)
        health_doc = {
            "completionPct": 70.18, "totalTaskCount": 57, "doneTaskCount": 40,
            "overdueTaskCount": 0, "scheduleRisk": "on_track", "slipDays": 0,
        }
        invoice_doc = {"totalBilled": 2449.0, "totalReceived": 0.0, "totalOutstanding": 2449.0}
        with patch.object(
            agent.entity_resolver, "resolve_project",
            return_value=[project_match("Henderson Roofing")],
        ), patch(
            "source.agents.reporting_agent.get_collection",
            side_effect=_mock_get_collection_by_name(
                {"project_health": health_doc, "invoice_summary": invoice_doc}
            ),
        ):
            result = agent.respond(
                ctx("what is the invoiced amount"), "henderson", None,
                "what is the invoiced amount",
            )
        assert result is not None
        assert "don't have that" not in result.message
        assert "2,449" in result.message

    def test_empty_answer_for_start_date_falls_through(self, ctx, project_match):
        llm = MagicMock()
        llm.answer_from_project_data.return_value = ""
        agent = ReportingAgent(llm_client=llm)
        health_doc = {
            "completionPct": 70.18, "totalTaskCount": 57, "doneTaskCount": 40,
            "overdueTaskCount": 0, "scheduleRisk": "on_track", "slipDays": 0,
            "jobStartDate": "2026-10-23T00:00:00.000Z",
        }
        with patch.object(
            agent.entity_resolver, "resolve_project",
            return_value=[project_match("Henderson Roofing")],
        ), patch(
            "source.agents.reporting_agent.get_collection",
            side_effect=_mock_get_collection_by_name(
                {"project_health": health_doc, "invoice_summary": {}}
            ),
        ):
            result = agent.respond(
                ctx("what is the start date"), "henderson", None,
                "what is the start date",
            )
        assert result is not None
        assert "don't have that" not in result.message

    def test_empty_answer_for_material_used_still_gets_the_refusal_not_billing(self, ctx):
        # Boundary pin: "cost" is deliberately excluded from
        # _BILLING_FALLTHROUGH_SIGNALS -- this must keep failing the same
        # way as TestEmptyRagAnswerRefusalDoesNotFallThroughToStatus's own
        # test, not get accidentally fixed by the billing signals above.
        llm = MagicMock()
        llm.answer_from_project_data.return_value = ""
        agent = ReportingAgent(llm_client=llm)
        docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
        health_doc = {
            "completionPct": 70.18, "totalTaskCount": 57, "doneTaskCount": 40,
            "overdueTaskCount": 0, "scheduleRisk": "on_track", "slipDays": 0,
        }
        with patch(
            "source.resolvers.entity_resolver.get_collection",
            return_value=_mock_project_matches(docs),
        ), patch(
            "source.agents.reporting_agent.get_collection",
            side_effect=_mock_get_collection_by_name(
                {"project_health": health_doc, "invoice_summary": {}}
            ),
        ):
            result = agent.respond(
                ctx("what is the material cost for this project"), "henderson", None,
                "what is the material cost for this project",
            )
        assert result is not None
        assert "don't have that" in result.message


class TestRagSelfReferentialLeakRejected:
    # Fix 3: confirmed live despite the prompt already explicitly forbidding
    # it -- the model still said "The JSON does not provide specific
    # information about changes to material or tool cost estimations,"
    # describing its own input format to the user.

    def test_json_self_reference_is_caught(self):
        assert _SELF_REFERENTIAL_RE.search(
            "The JSON does not provide specific information about changes "
            "to material or tool cost estimations."
        )

    def test_data_provided_self_reference_is_caught(self):
        assert _SELF_REFERENTIAL_RE.search("The data provided does not include that.")

    def test_a_clean_natural_answer_is_not_flagged(self):
        assert not _SELF_REFERENTIAL_RE.search("There is no upcoming invoice on record.")
        assert not _SELF_REFERENTIAL_RE.search("The project is 20% complete.")

    def test_full_answer_from_data_returns_a_refusal_not_a_billing_answer(self, ctx):
        # Fix (PR review, Raja): same "falls through to an unrelated
        # deterministic answer" gap db4066f closed for the excluded-field
        # rejection -- this rejection path had the same bug. Must return the
        # honest refusal directly, not None (which respond() would otherwise
        # send through its billing/date/status fallback chain).
        #
        # Deliberately avoids any "material cost"/"tool cost"-shaped phrase
        # in the answer -- that would trip the EARLIER excluded-field check
        # instead (it runs first in answer_from_data), never reaching this
        # self-referential rejection at all.
        llm = MagicMock()
        llm.answer_from_project_data.return_value = "The data provided does not include that."
        agent = ReportingAgent(llm_client=llm)
        docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
        health_doc = {"completionPct": 20.0}
        with patch(
            "source.resolvers.entity_resolver.get_collection",
            return_value=_mock_project_matches(docs),
        ), patch(
            "source.agents.reporting_agent.get_collection",
            side_effect=_mock_get_collection_by_name(
                {"project_health": health_doc, "invoice_summary": {}}
            ),
        ):
            result = agent.answer_from_data(
                ctx("what is the vendor bill status"), "henderson", None, None,
                "what is the vendor bill status",
            )
        assert isinstance(result, NavigationResponse)
        assert result.type == "guidance"
        assert "don't have that specific detail" in result.message
        assert result.project.id == "uid-1"


# --- isDeleted exclusion (PR review, Raja) --------------------------------
# Real gap, fixed: the isDeleted filters added to billing_response,
# answer_from_data (project_health + invoice_summary), date_response,
# status_response, _estimate_change_response, _schedule_change_response,
# and _most_delayed_task (8 sites total) had zero asserting test coverage
# -- a future edit could silently drop the filter from any of these sites
# (surfacing a deleted project's billing/status/date/task figures) with
# nothing in the suite catching it.

def test_billing_response_excludes_deleted(ctx):
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    invoice_collection = _mock_health_doc(
        {"totalBilled": 50000, "totalReceived": 42000, "totalOutstanding": 8000}
    )
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        return_value=invoice_collection,
    ):
        agent.billing_response(ctx("how much do I owe"), "henderson")
    call_filter = invoice_collection.find_one.call_args.args[0]
    assert call_filter["isDeleted"] == {"$ne": True}


def test_answer_from_data_excludes_deleted_on_both_collections(ctx):
    llm = MagicMock()
    llm.answer_from_project_data.return_value = "You still owe $8,000 on this project."
    agent = ReportingAgent(llm_client=llm)
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    seen_filters: dict[str, dict] = {}

    def _get(name, db_name=None):
        collection = MagicMock()
        collection.find_one.side_effect = lambda query, _proj=None, n=name: (
            seen_filters.__setitem__(n, query) or {"totalOutstanding": 8000}
        )
        return collection

    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection", side_effect=_get,
    ):
        agent.answer_from_data(
            ctx("what's still owed"), "henderson", None, None, "what's still owed",
        )
    assert seen_filters["project_health"]["isDeleted"] == {"$ne": True}
    assert seen_filters["invoice_summary"]["isDeleted"] == {"$ne": True}


def test_date_response_excludes_deleted(ctx):
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_collection = _mock_health_doc({
        "jobStartDate": "2028-03-13 09:30:00+00:00", "actualJobStartDate": None,
        "jobEndDate": "2028-03-15 13:20:00+00:00", "actualJobEndDate": None,
    })
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        return_value=health_collection,
    ):
        agent.date_response(ctx("when does henderson start"), "henderson")
    call_filter = health_collection.find_one.call_args.args[0]
    assert call_filter["isDeleted"] == {"$ne": True}


def test_status_response_excludes_deleted(ctx):
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_collection = _mock_health_doc({
        "completionPct": 68.0, "totalTaskCount": 10, "doneTaskCount": 7,
        "overdueTaskCount": 2, "scheduleRisk": "on_track", "slipDays": 0,
    })
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        return_value=health_collection,
    ):
        agent.status_response(ctx("status of henderson"), "henderson")
    call_filter = health_collection.find_one.call_args.args[0]
    assert call_filter["isDeleted"] == {"$ne": True}


def test_estimate_change_lookup_excludes_deleted(ctx):
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_collection = _mock_health_doc({
        "estimateHistory": [
            {"version": 1, "isCurrent": True, "confidence": "high",
             "reason": "Material price increase", "delta": {"materialCost": 500}},
        ]
    })
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        return_value=health_collection,
    ):
        agent.respond(ctx("why did the estimate change on henderson"), "henderson")
    call_filter = health_collection.find_one.call_args.args[0]
    assert call_filter["isDeleted"] == {"$ne": True}


def test_schedule_change_lookup_excludes_deleted(ctx):
    agent = ReportingAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_collection = _mock_health_doc({
        "scheduleRevisions": [
            {"version": 1, "isCurrent": True, "confidence": "low",
             "reason": "Crew became available earlier", "comment": "Start moved earlier"},
        ]
    })
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.reporting_agent.get_collection",
        return_value=health_collection,
    ):
        agent.respond(ctx("why did the start date move on henderson"), "henderson")
    call_filter = health_collection.find_one.call_args.args[0]
    assert call_filter["isDeleted"] == {"$ne": True}


def test_most_delayed_task_excludes_deleted(ctx):
    task_collection = _mock_health_doc(
        {"mostDelayedTask": {"taskName": "Install shingles", "daysLate": 3}}
    )
    with patch(
        "source.agents.reporting_agent.get_collection",
        return_value=task_collection,
    ):
        ReportingAgent()._most_delayed_task("tenant-1", "uid-1")
    call_filter = task_collection.find_one.call_args.args[0]
    assert call_filter["isDeleted"] == {"$ne": True}
