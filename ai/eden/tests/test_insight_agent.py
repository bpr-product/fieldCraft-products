"""Unit tests for InsightAgent — project-level status reporter, with both
the entity-resolver's and this agent's Mongo collections fully mocked so
tests never touch a live database. Mock docs use the real gold_eden
field names (projectId/projectName/committedSpend/etc.)."""
from unittest.mock import MagicMock, patch

from source.agents.insight_agent import InsightAgent


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


def test_no_entity_name_lists_available_projects(ctx):
    agent = InsightAgent()
    docs = [
        {"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"},
        {"projectId": "uid-2", "projectName": "Buma Retail", "status": "active"},
    ]
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ):
        result = agent.variance_response(ctx("why is cost over budget"), None)
    assert result is not None
    assert result.type == "disambiguation"
    assert result.question
    assert [o.label for o in result.options] == ["Henderson Roofing", "Buma Retail"]
    assert result.options[0].project.id == "uid-1"


def test_cost_question_with_no_project_denies_immediately_without_permission(ctx):
    """Real gap, fixed: margin/total_cost permission is user-level, not
    per-project -- it never depended on which project got picked. The old
    flow always asked "which project?" first when none was named, deferring
    the EST.REA/EST.MOD check until deep inside _margin_response/
    _total_cost_response. A Labor user (no EST.REA) asking "what is the
    project estimate" with no project named should be denied immediately,
    never see a project list, and never hit the project-lookup collection
    at all (asserted via the entity_resolver mock never being invoked)."""
    agent = InsightAgent()
    with patch(
        "source.resolvers.entity_resolver.get_collection",
    ) as mock_entity_get_collection, patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_role_collection(["labour"], permissions=[]),
    ):
        result = agent.variance_response(ctx("what is the project estimate"), None)
    assert result is not None
    assert result.type == "guidance"
    assert "not able to share the total project cost" in result.message
    mock_entity_get_collection.assert_not_called()


def test_margin_question_with_no_project_denies_immediately_without_permission(ctx):
    """Same early short-circuit, margin variant (EST.MOD)."""
    agent = InsightAgent()
    with patch(
        "source.resolvers.entity_resolver.get_collection",
    ) as mock_entity_get_collection, patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_role_collection(["labour"], permissions=[]),
    ):
        result = agent.variance_response(ctx("what is the profit margin"), None)
    assert result is not None
    assert result.type == "guidance"
    assert "not able to share margin or profitability details" in result.message
    mock_entity_get_collection.assert_not_called()


def test_no_entity_name_and_no_projects_returns_none(ctx):
    agent = InsightAgent()
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches([]),
    ):
        result = agent.variance_response(ctx("why is cost over budget"), None)
    assert result is None


def test_returns_none_when_no_project_match(ctx):
    agent = InsightAgent()
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches([]),
    ):
        result = agent.variance_response(
            ctx("why is cost over budget on nonexistent"), "nonexistent"
        )
    assert result is None


def test_no_entity_name_lists_all_projects_up_to_cap_without_partial_message(ctx):
    """Every project is shown as a card -- no "showing X of Y" truncation
    message, and no redirect."""
    agent = InsightAgent()
    docs = [
        {"projectId": f"uid-{i}", "projectName": f"Project {i}", "status": "active"}
        for i in range(20)
    ]
    collection = MagicMock()
    cursor = MagicMock()
    # list_projects chains .find().sort().limit() -- .sort() must return the
    # same cursor mock, or .limit() lands on an unrelated auto-generated mock
    # and silently yields an empty list instead of docs.
    cursor.sort.return_value = cursor
    cursor.limit.return_value = docs
    collection.find.return_value = cursor
    collection.count_documents.return_value = len(docs)
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        result = agent.variance_response(ctx("why is cost over budget"), None)
    assert result is not None
    assert result.type == "disambiguation"
    assert len(result.options) == 20
    assert "Showing" not in (result.question or "")


def test_no_entity_name_beyond_old_cap_lists_every_project_on_web(ctx):
    # Real gap, fixed (2026-09-03): the old ">30 -> redirect to Projects
    # page" web behavior is reversed per explicit user direction now that
    # OptionCardList (NavigationMessage.tsx) grew an internal scroll that
    # handles any option count -- web now lists everything as clickable
    # cards, same as mobile already did, instead of redirecting.
    agent = InsightAgent()
    true_total = 45
    fetched_docs = [
        {"projectId": f"uid-{i}", "projectName": f"Project {i}", "status": "active"}
        for i in range(true_total)
    ]
    collection = MagicMock()
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = fetched_docs
    collection.find.return_value = cursor
    collection.count_documents.return_value = true_total
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        result = agent.variance_response(ctx("why is cost over budget"), None)
    assert result is not None
    assert result.type == "disambiguation"
    assert len(result.options) == true_total
    # Confirms the fetch itself was requested unbounded (Mongo's own
    # .limit(0) convention), not just that this particular mock happened
    # to return everything regardless of the limit argument.
    cursor.limit.assert_called_with(0)


def test_no_entity_name_beyond_old_cap_on_mobile_lists_every_project_instead(ctx):
    # Real bug, fixed: confirmed live on mobile (2026-09-03) -- mobile has
    # no Projects list page to redirect to at all (see
    # _strip_route_fields_for_mobile's own docstring). Now that web behaves
    # the same way (see test above), this just confirms mobile is unaffected
    # by that change.
    agent = InsightAgent()
    true_total = 45
    fetched_docs = [
        {"projectId": f"uid-{i}", "projectName": f"Project {i}", "status": "active"}
        for i in range(true_total)
    ]
    collection = MagicMock()
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = fetched_docs
    collection.find.return_value = cursor
    collection.count_documents.return_value = true_total
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        result = agent.variance_response(
            ctx("why is cost over budget", platform="mobile"), None
        )
    assert result is not None
    assert result.type == "disambiguation"
    assert len(result.options) == true_total
    cursor.limit.assert_called_with(0)
    cursor.limit.assert_called_with(0)


def test_ambiguous_project_name_disambiguates_among_matches(ctx):
    agent = InsightAgent()
    docs = [
        {"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"},
        {"projectId": "uid-2", "projectName": "Henderson Retail", "status": "active"},
    ]
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ):
        result = agent.variance_response(
            ctx("why is cost over budget on henderson"), "henderson"
        )
    assert result is not None
    assert result.type == "disambiguation"
    assert [o.label for o in result.options] == ["Henderson Roofing", "Henderson Retail"]


def test_returns_none_when_no_health_doc(ctx):
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(None),
    ):
        result = agent.variance_response(
            ctx("why is cost over budget on henderson"), "henderson"
        )
    assert result is None


def test_returns_none_on_malformed_data(ctx):
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {"committedSpend": "not-a-number", "outstandingInvoicesTotal": 0}
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ):
        result = agent.variance_response(
            ctx("why is cost over budget on henderson"), "henderson"
        )
    assert result is None


def test_reports_committed_spend_and_outstanding_invoices(ctx):
    # AB#30855: outstandingInvoicesTotal is now gated on INV.REA (same as
    # margin/total_cost's own permission gates) -- mock a role that holds
    # it so this test still exercises the happy-path figure disclosure,
    # not the new denial path (see test_outstanding_invoices_refused_
    # without_inv_rea below for that).
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "committedSpend": 142000, "outstandingInvoicesTotal": 5000,
        "scheduleRisk": "on_track", "slipDays": 0,
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ), patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_role_and_permissions_collection(["admin"], ["INV.REA", "JBL.REA"]),
    ):
        result = agent.variance_response(
            ctx("why is cost over budget on henderson"), "henderson"
        )
    assert result is not None
    assert result.type == "guidance"
    assert "Henderson Roofing" in result.message
    assert "$142,000" in result.message
    assert "$5,000" in result.message
    assert "on track" in result.message.lower()


def test_outstanding_invoices_refused_without_inv_rea(ctx):
    # AB#30855, real bug fixed: a "why is cost over budget" question used
    # to get outstandingInvoicesTotal with zero permission check. Isolates
    # the INV.REA gate specifically by granting JBL.REA too (committedSpend
    # is now its OWN gate -- see test_committed_spend_refused_without_
    # jbl_rea and test_labour_denied_both_committed_spend_and_invoices for
    # that one and the real, combined-denial Labor case).
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "committedSpend": 142000, "outstandingInvoicesTotal": 5000,
        "scheduleRisk": "on_track", "slipDays": 0,
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ), patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_role_and_permissions_collection(["sales"], ["JBL.REA"]),
    ):
        result = agent.variance_response(
            ctx("why is cost over budget on henderson"), "henderson"
        )
    assert result is not None
    assert "$142,000" in result.message
    assert "$5,000" not in result.message
    assert "aren't available at your access level" in result.message.lower()


def test_committed_spend_refused_without_jbl_rea(ctx):
    """Real gap, fixed: committedSpend was shown unconditionally, on the
    unverified assumption it's "a different, ungated internal concern."
    Checked directly against the real application: the equivalent UI (Job
    Ledger's "Total Job Costs") is gated on JBL.REA. Isolates that gate by
    granting INV.REA too, so only the job-cost figure is refused."""
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "committedSpend": 142000, "outstandingInvoicesTotal": 5000,
        "scheduleRisk": "on_track", "slipDays": 0,
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ), patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_role_and_permissions_collection(["sales"], ["INV.REA"]),
    ):
        result = agent.variance_response(
            ctx("why is cost over budget on henderson"), "henderson"
        )
    assert result is not None
    assert "142,000" not in result.message
    assert "$5,000" in result.message
    assert "job cost details aren't available at your access level" in result.message


def test_labour_denied_both_committed_spend_and_invoices(ctx):
    """The real, live case: Labor holds neither JBL.REA nor INV.REA in the
    real role_permission seed -- confirmed live on fieldcraftmind, 2026-09-18.
    Both figures must be refused with one combined message; schedule stays
    visible."""
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "committedSpend": 142000, "outstandingInvoicesTotal": 5000,
        "scheduleRisk": "on_track", "slipDays": 0,
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ), patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_role_and_permissions_collection(["labour"], ["TAS.REA"]),
    ):
        result = agent.variance_response(
            ctx("why is cost over budget on henderson"), "henderson"
        )
    assert result is not None
    assert "142,000" not in result.message
    assert "5,000" not in result.message
    assert "job cost and invoice details aren't available at your access level" in result.message
    assert "on track" in result.message.lower()


def test_reports_at_risk_schedule_with_slip_days(ctx):
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "committedSpend": 76000, "outstandingInvoicesTotal": 15200,
        "scheduleRisk": "at_risk", "slipDays": 12,
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ):
        result = agent.variance_response(
            ctx("why is cost over budget on henderson"), "henderson"
        )
    assert result is not None
    assert "at risk" in result.message.lower()
    assert "12 days behind" in result.message


def test_reports_unknown_schedule_status(ctx):
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "committedSpend": 0, "outstandingInvoicesTotal": 0,
        "scheduleRisk": "unknown", "slipDays": 0,
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ):
        result = agent.variance_response(
            ctx("why is cost over budget on henderson"), "henderson"
        )
    assert result is not None
    assert "not yet available" in result.message.lower()


# --- New reasoning requirement: estimate-change / schedule-move / margin ---
# See EDEN_CustomerSupport_Reasoning_Constraints.md for the constraint
# numbers referenced below.

def _mock_role_collection(roles: list[str] | None, permissions: list[str] | None = None):
    """`permissions` defaults to None (not []) so existing margin-only tests
    that never set it keep the exact same behavior as before this param
    existed -- .find_one() still returns a doc with no "permissions" key,
    and get_permissions() treats a missing key as empty, same result either
    way. Only tests exercising the EST.REA-gated total_cost path need to
    pass real permissions."""
    collection = MagicMock()
    if roles is None:
        collection.find_one.return_value = None
    else:
        doc = {"roles": roles}
        if permissions is not None:
            doc["permissions"] = permissions
        collection.find_one.return_value = doc
    return collection


def test_estimate_change_high_confidence_includes_figures(ctx):
    """Constraint #4/#11: a reconciling, high-confidence estimateHistory
    entry gets a plain causal answer. Insight is internal, so
    include_figures=True -- the dollar delta is shown."""
    agent = InsightAgent()
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
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ):
        result = agent.variance_response(
            ctx("why did the estimate change on henderson"), "henderson"
        )
    assert result is not None
    assert result.type == "guidance"
    assert "$500" in result.message
    assert "receipt captured" in result.message


def test_estimate_change_low_confidence_includes_figures(ctx):
    """Regression for the figures-parity gap found live against real
    gold_eden project 7890: at confidence="low", Insight (include_figures=
    True) must show the bucket-level delta same as the "high" branch --
    the delta is a verified fact independent of how much the CAUSE is
    trusted. Before this fix, "low" ignored delta/include_figures entirely
    and returned identical text to Reporting/customer."""
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "estimateHistory": [
            {"version": 4, "isCurrent": False, "confidence": "none", "reason": None},
            {
                "version": 6, "isCurrent": True, "confidence": "low",
                "reason": "Customer Requested To Change",
                "delta": {"materialCost": 2000.0, "overheadCost": 100.0, "totalCost": 2180.0},
            },
        ]
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ):
        result = agent.variance_response(
            ctx("why did the estimate change on henderson"), "henderson"
        )
    assert result is not None
    assert "$2,000" in result.message
    assert "$100" in result.message
    assert "Customer Requested To Change" in result.message


def test_estimate_change_no_confidence_gives_honest_fallback(ctx):
    """Constraint #11 guard: no confidence/reason on file -> never assert
    a cause."""
    agent = InsightAgent()
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
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ):
        result = agent.variance_response(
            ctx("why did the estimate change on henderson"), "henderson"
        )
    assert result is not None
    assert "don't have a confirmed reason" in result.message
    assert "$" not in result.message


def test_estimate_unchanged_when_only_one_version(ctx):
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    # version 0 = the baseline estimate (estimateSummary.baselineVersion is 0
    # in real gold data), i.e. genuinely never revised. A lone entry at
    # version > 0 means the ETL dropped its predecessors and IS a change --
    # see estimate_changed_per_summary and the version-2 test below.
    health_doc = {"estimateHistory": [{"version": 0, "isCurrent": True, "confidence": None, "reason": None}]}
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ):
        result = agent.variance_response(
            ctx("why did the estimate change on henderson"), "henderson"
        )
    assert result is not None
    assert "has not changed" in result.message


def test_schedule_change_low_confidence_is_hedged(ctx):
    """Constraint #36: correlational reasoning must never assert causation
    as fact."""
    agent = InsightAgent()
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
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ):
        result = agent.variance_response(
            ctx("why did the start date move on henderson"), "henderson"
        )
    assert result is not None
    assert "crew finished a prior job" in result.message
    assert "confirm the exact reason" in result.message


def test_margin_admin_role_sees_figure(ctx):
    """Constraint #32: admin holds EST.MOD (confirmed live), the permission
    is_margin_visible checks."""
    agent = InsightAgent()
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
        result = agent.variance_response(
            ctx("what is the profit margin on henderson"), "henderson"
        )
    assert result is not None
    assert "$5,000" in result.message
    assert "$46,000" in result.message


def test_margin_superadmin_role_sees_figure(ctx):
    """2026-09-09 fix (OOM_AND_CDC_FAILURE_RCA_2026-09-09.md /
    PROD_FIXES_2026-09-09.md at repo root): a real superAdmin account
    (jon@example.test) was incorrectly refused margin visibility.
    UserRoleResolver.is_margin_visible (NOT is_admin -- see its own
    docstring for why they're deliberately separate) now checks the real
    EST.MOD permission (rewritten 2026-09-10 from a role-name check --
    superAdmin holds EST.MOD, confirmed live). is_admin() itself is
    intentionally unchanged -- see test_user_role_resolver.py's
    test_superadmin_is_not_admin, which must keep passing."""
    agent = InsightAgent()
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
        return_value=_mock_role_collection(["superAdmin"], permissions=["EST.MOD"]),
    ):
        result = agent.variance_response(
            ctx("what is the profit margin on henderson"), "henderson"
        )
    assert result is not None
    assert "$5,000" in result.message
    assert "$46,000" in result.message


def test_margin_sales_role_now_sees_figure(ctx):
    """2026-09-10 fix: sales genuinely holds EST.MOD in the real product
    (confirmed live against the actual source role_permission table, all 3
    tenants) -- must see margin. This REVERSES the previous behavior
    (sales used to be refused, identically to customer) once is_margin_
    visible was rewritten from a role-name check to a real permission
    check. sales is used here specifically because it's NOT "customer", so
    it is NOT routed away from Insight at Layer 1 -- this method is the
    actual enforcement point for it."""
    agent = InsightAgent()
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
        return_value=_mock_role_collection(["sales"], permissions=["EST.MOD"]),
    ):
        result = agent.variance_response(
            ctx("what is the profit margin on henderson"), "henderson"
        )
    assert result is not None
    assert "$5,000" in result.message


def test_margin_purchaseofficer_role_is_refused(ctx):
    """Constraint #32/#33: purchaseOfficer holds EST.REA but NOT EST.MOD
    (confirmed live, all 3 tenants) -- correctly refused. purchaseOfficer
    is not "customer", so it is NOT routed away from Insight at Layer 1,
    meaning this method is the actual enforcement point."""
    agent = InsightAgent()
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
        return_value=_mock_role_collection(["purchaseOfficer"], permissions=["EST.REA"]),
    ):
        result = agent.variance_response(
            ctx("what is the profit margin on henderson"), "henderson"
        )
    assert result is not None
    assert "not able to share margin" in result.message
    assert "5,000" not in result.message
    assert "46,000" not in result.message


def test_estimate_change_no_confidence_but_delta_present_reports_figure_and_bucket(ctx):
    """Constraint #11 applied to the "none" tier: real gold_eden project
    7890 has delta populated (materialCost=2000) with confidence entirely
    absent -- the ETL already computes bucket-level deltas independently
    of confidence. Insight must use that, not the flat non-answer."""
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "estimateHistory": [
            {
                "version": 4, "isCurrent": False, "confidence": None, "reason": None,
                "snapshot": {"totalCost": 2905.63, "materialCost": 2000, "workerCost": 665.71, "toolCost": 0, "overheadCost": 133.29},
            },
            {
                "version": 6, "isCurrent": True, "confidence": None, "reason": None,
                "snapshot": {"totalCost": 5085.63, "materialCost": 4000, "workerCost": 665.71, "toolCost": 0, "overheadCost": 233.29},
                "delta": {"totalCost": 2180, "materialCost": 2000, "workerCost": 0, "toolCost": 0, "overheadCost": 100},
            },
        ]
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ):
        result = agent.variance_response(
            ctx("why did the estimate change on henderson"), "henderson"
        )
    assert result is not None
    assert "$2,906" in result.message or "$2,905" in result.message  # prior total
    assert "$5,086" in result.message  # new total
    assert "material cost changed by $2,000" in result.message
    assert "can't confirm the specific driver" in result.message


def test_estimate_change_no_confidence_no_delta_stays_generic(ctx):
    """No delta at all (not just no confidence) -> the maximally-honest
    generic fallback, unchanged from before."""
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "estimateHistory": [
            {"version": 1, "isCurrent": False, "confidence": None, "reason": None},
            {"version": 2, "isCurrent": True, "confidence": None, "reason": None},
        ]
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ):
        result = agent.variance_response(
            ctx("why did the estimate change on henderson"), "henderson"
        )
    assert result is not None
    assert "$" not in result.message


def test_schedule_change_no_confidence_reports_new_date(ctx):
    """Constraint #11 applied to Q2's "none" tier -- matches the
    constraints doc's own sample ("...moved to July 29...")."""
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "scheduleRevisions": [
            {"version": 1, "isCurrent": False, "confidence": None, "reason": None, "comment": None},
            {
                "version": 2, "isCurrent": True, "confidence": None, "reason": None, "comment": None,
                "snapshot": {"jobStartDate": "2026-07-29"},
            },
        ]
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ):
        result = agent.variance_response(
            ctx("why did the start date move on henderson"), "henderson"
        )
    assert result is not None
    assert "July 29, 2026" in result.message
    assert "don't have a confirmed reason" in result.message


def test_schedule_change_zero_delta_does_not_claim_a_move(ctx):
    """Regression for a false-positive found live against real gold_eden
    project 7890: delta explicitly showed jobStartShiftDays=0 (confirmed
    by scheduleSummary.meaningfulRevisionCount=0 on the real doc -- ETL's
    own judgment that nothing changed), but the old code still said "your
    start date moved to <date>" because it only ever read snapshot, never
    delta. Must now say nothing moved instead of asserting a false move."""
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "scheduleRevisions": [
            {"version": 1, "isCurrent": False, "confidence": None, "reason": None, "comment": None},
            {
                "version": 2, "isCurrent": True, "confidence": None, "reason": None, "comment": None,
                "snapshot": {"jobStartDate": "2027-03-31"},
                "delta": {"jobStartShiftDays": 0, "jobEndShiftDays": None},
            },
        ]
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ):
        result = agent.variance_response(
            ctx("why did the start date move on henderson"), "henderson"
        )
    assert result is not None
    assert "moved to" not in result.message
    assert "hasn't actually changed" in result.message
    assert "March 31, 2027" in result.message


def test_estimate_change_high_confidence_logs_resolved_audit(ctx):
    """Backlog #26159: a real causal answer must be logged as "resolved",
    not "unresolved" -- only the no-confidence fallback is an escalation
    candidate."""
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "estimateHistory": [
            {"version": 1, "isCurrent": False, "confidence": None, "reason": None},
            {"version": 2, "isCurrent": True, "confidence": "high", "reason": "Receipt captured"},
        ]
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ), patch(
        "source.agents.insight_agent.write_reasoning_audit_log"
    ) as mock_audit:
        agent.variance_response(ctx("why did the estimate change on henderson"), "henderson")
    mock_audit.assert_called_once()
    args = mock_audit.call_args.args
    assert args[3] == "uid-1"          # project_uid
    assert args[4] == "estimate_change"
    assert args[5] == "high"           # confidence
    assert args[6] == "resolved"       # status


def test_estimate_change_no_confidence_logs_unresolved_audit(ctx):
    """The exact escalation trigger: a "none"/missing-confidence answer is
    logged as "unresolved" so a human can follow up."""
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "estimateHistory": [
            {"version": 1, "isCurrent": False, "confidence": None, "reason": None},
            {"version": 2, "isCurrent": True, "confidence": None, "reason": None},
        ]
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ), patch(
        "source.agents.insight_agent.write_reasoning_audit_log"
    ) as mock_audit:
        agent.variance_response(ctx("why did the estimate change on henderson"), "henderson")
    mock_audit.assert_called_once()
    assert mock_audit.call_args.args[6] == "unresolved"


def test_schedule_change_low_confidence_logs_resolved_audit(ctx):
    """Q2's ceiling is "low", never "high" -- "low" still counts as
    resolved (a hedged but genuine answer), not unresolved."""
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "scheduleRevisions": [
            {"version": 1, "isCurrent": False, "confidence": None, "reason": None, "comment": None},
            {
                "version": 2, "isCurrent": True, "confidence": "low",
                "reason": "Crew became available earlier than planned", "comment": None,
            },
        ]
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ), patch(
        "source.agents.insight_agent.write_reasoning_audit_log"
    ) as mock_audit:
        agent.variance_response(ctx("why did the start date move on henderson"), "henderson")
    mock_audit.assert_called_once()
    args = mock_audit.call_args.args
    assert args[4] == "schedule_move"
    assert args[5] == "low"
    assert args[6] == "resolved"


def test_no_history_does_not_write_audit(ctx):
    """No history to explain isn't an unresolved query -- there was
    nothing to answer in the first place, so no audit row should be
    written at all."""
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    # version 0 = the baseline estimate (estimateSummary.baselineVersion is 0
    # in real gold data), i.e. genuinely never revised. A lone entry at
    # version > 0 means the ETL dropped its predecessors and IS a change --
    # see estimate_changed_per_summary and the version-2 test below.
    health_doc = {"estimateHistory": [{"version": 0, "isCurrent": True, "confidence": None, "reason": None}]}
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ), patch(
        "source.agents.insight_agent.write_reasoning_audit_log"
    ) as mock_audit:
        agent.variance_response(ctx("why did the estimate change on henderson"), "henderson")
    mock_audit.assert_not_called()


def test_second_identical_answer_gets_as_i_mentioned_prefix(ctx, monkeypatch):
    """Honest self-reference: asking the SAME question about the SAME
    project twice, with nothing changed, should reference the prior
    answer -- using a real (patched-per-test) fake Redis so the two calls
    in this test actually share state, unlike the autouse fixture that
    blocks Redis for every OTHER test's isolation."""
    store = {}

    class _FakeRedis:
        def get(self, key):
            return store.get(key)

        def setex(self, key, ttl, value):
            store[key] = value

    monkeypatch.setattr(
        "source.resolvers.conversation_memory.get_redis_client",
        lambda: _FakeRedis(),
    )
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "estimateHistory": [
            {"version": 1, "isCurrent": False, "confidence": None, "reason": None},
            {"version": 2, "isCurrent": True, "confidence": "high", "reason": "Receipt captured"},
        ]
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ):
        first = agent.variance_response(ctx("why did the estimate change on henderson"), "henderson")
        second = agent.variance_response(ctx("why did the estimate change on henderson"), "henderson")
    assert "As I mentioned earlier" not in first.message
    assert "As I mentioned earlier" in second.message
    assert "Receipt captured" in second.message


def test_changed_answer_flags_the_difference(ctx, monkeypatch):
    """If the underlying data genuinely changed between two asks, EDEN
    says so honestly instead of silently repeating or silently updating
    with no acknowledgment."""
    store = {}

    class _FakeRedis:
        def get(self, key):
            return store.get(key)

        def setex(self, key, ttl, value):
            store[key] = value

    monkeypatch.setattr(
        "source.resolvers.conversation_memory.get_redis_client",
        lambda: _FakeRedis(),
    )
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    first_doc = {
        "estimateHistory": [
            {"version": 1, "isCurrent": False, "confidence": None, "reason": None},
            {"version": 2, "isCurrent": True, "confidence": "high", "reason": "Receipt captured"},
        ]
    }
    second_doc = {
        "estimateHistory": [
            {"version": 1, "isCurrent": False, "confidence": None, "reason": None},
            {"version": 2, "isCurrent": False, "confidence": "high", "reason": "Receipt captured"},
            {"version": 3, "isCurrent": True, "confidence": "high", "reason": "Second receipt captured"},
        ]
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(first_doc),
    ):
        first = agent.variance_response(ctx("why did the estimate change on henderson"), "henderson")
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(second_doc),
    ):
        second = agent.variance_response(ctx("why did the estimate change on henderson"), "henderson")
    assert "As I mentioned earlier" not in first.message
    assert "This is different from what I told you before" in second.message
    assert "Second receipt captured" in second.message


def test_margin_lookup_failure_fails_closed_to_refused(ctx):
    """UserRoleResolver.is_admin fails closed (constraint discipline
    mirrors is_customer's fail-closed direction) -- a role-lookup error
    must never default to showing margin."""
    agent = InsightAgent()
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
        side_effect=Exception("timeout"),
    ):
        result = agent.variance_response(
            ctx("what is the profit margin on henderson"), "henderson"
        )
    assert result is not None
    assert "not able to share margin" in result.message


# ── Q1/Q2 enrichment from estimateSummary / scheduleSummary ─────────────────
# Five fields were present in gold_eden and read by nothing: lastReason,
# totalCostDelta, originalJobStartDate, totalEndShiftDays, and (via the entry)
# inferredReason. EDEN answered "I don't have a confirmed reason on file" while
# the record held one -- confirmed live on order 102380.

from source.agents.insight_agent import (  # noqa: E402
    build_estimate_change_message,
    build_schedule_change_message,
    estimate_changed_per_summary,
    schedule_changed_per_summary,
)


class TestChangedPerSummary:
    def test_lone_entry_at_version_zero_is_genuinely_unchanged(self):
        assert not estimate_changed_per_summary(
            [{"version": 0, "isCurrent": True}], {"currentVersion": 0, "baselineVersion": 0}
        )

    def test_lone_entry_at_version_two_counts_as_changed(self):
        # gold_eden project 1203 (order 543655): one retained entry, version 2,
        # inferredReason "Cost change...". EDEN used to answer "has not changed
        # since it was first prepared" -- contradicting its own source record.
        assert estimate_changed_per_summary(
            [{"version": 2, "isCurrent": True, "inferredReason": "Cost change"}], {}
        )

    def test_summary_delta_alone_counts_as_changed(self):
        assert estimate_changed_per_summary([], {"totalCostDelta": 760.36})

    def test_schedule_date_shift_in_summary_counts_as_changed(self):
        assert schedule_changed_per_summary([], {
            "originalJobStartDate": "2026-04-10 00:00:00",
            "currentJobStartDate": "2026-04-13 00:00:00",
        })

    def test_schedule_unchanged_when_summary_shows_no_shift(self):
        assert not schedule_changed_per_summary([], {
            "originalJobStartDate": "2026-04-10 00:00:00",
            "currentJobStartDate": "2026-04-10 00:00:00",
            "meaningfulRevisionCount": 0, "totalEndShiftDays": 0,
        })


class TestSummaryEnrichedMessages:
    # Real scheduleSummary from gold_eden order 102380.
    SCHED = {
        "originalJobStartDate": "2026-04-10 00:00:00",
        "currentJobStartDate": "2026-04-13 00:00:00",
        "totalEndShiftDays": 6,
        "meaningfulRevisionCount": 14,
        "lastReason": "Recurring Auto Rescheduling",
    }

    def test_schedule_message_reports_the_span_and_the_recorded_reason(self):
        msg = build_schedule_change_message(
            {"confidence": None, "reason": None, "snapshot": {}, "delta": {}}, summary=self.SCHED
        )
        assert "April 10, 2026" in msg and "April 13, 2026" in msg
        assert "6 day" in msg
        assert "Recurring Auto Rescheduling" in msg
        assert "14 recorded revisions" in msg

    def test_schedule_message_never_asserts_the_summary_reason_as_the_cause(self):
        # lastReason describes the MOST RECENT revision, which may not be this
        # entry's -- constraint #36 forbids stating it as fact.
        msg = build_schedule_change_message(
            {"confidence": None, "reason": None, "snapshot": {}, "delta": {}}, summary=self.SCHED
        )
        assert "project manager" in msg          # hedge present
        assert "because" not in msg.lower()      # no causal claim

    def test_estimate_message_uses_inferred_reason_but_hedged(self):
        msg = build_estimate_change_message(
            "SYN-Project",
            {"confidence": "low", "reason": None,
             "inferredReason": "Cost change (no specific material attributable)",
             "delta": {}, "snapshot": {}},
            include_figures=False,
        )
        assert "Cost change" in msg
        assert "appears related to" in msg       # hedged, not asserted

    def test_estimate_message_falls_back_to_summary_total_delta(self):
        msg = build_estimate_change_message(
            "SYN-Project",
            {"confidence": None, "reason": None, "delta": {}, "snapshot": {}},
            include_figures=True,
            summary={"totalCostDelta": 760.36, "meaningfulRevisionCount": 2},
        )
        assert "760" in msg

    def test_margin_delta_is_never_surfaced_through_q1(self):
        # Margin is admin-gated (Q3). include_figures=True only means
        # "internal", which is broader than admin -- surfacing marginDelta here
        # would route around that gate.
        msg = build_estimate_change_message(
            "SYN-Project",
            {"confidence": None, "reason": None, "delta": {}, "snapshot": {}},
            include_figures=True,
            summary={"totalCostDelta": 760.36, "marginDelta": 1450.15},
        )
        assert "1,450" not in msg and "1450" not in msg


# ── evidence.bomReconciles wired into Q1 ─────────────────────────────────────
# gold_eden's estimateHistory carries an `evidence` object (bomReconciles,
# priceChangeDetected, materialName, priorUnitCost/newUnitCost, priceHistory)
# that nothing read. Confirmed live on order 102380: material_cost verifiably
# matches SUM(bom.total_cost) (bomReconciles=True) and the bucket delta shows
# overhead moved -- EDEN said "no specific material attributable" instead of
# the stronger, still-true "confirmed not material, movement is in overhead".

from source.agents.insight_agent import (  # noqa: E402
    _bom_confirmed_not_material_message,
    _bom_price_change_message,
    _prefer_non_degenerate_sibling,
)


class TestBomEvidence:
    def test_confirmed_not_material_names_the_bucket_that_moved(self):
        # Real shape from order 102380: bomReconciles True, overhead moved,
        # material/labor/tool present in delta at ~0.
        msg = _bom_confirmed_not_material_message(
            {"bomReconciles": True},
            {"materialCost": 0, "workerCost": 0, "toolCost": 0, "overheadCost": -439.78},
        )
        assert "overhead cost" in msg
        assert "material cost" in msg  # named as one of the ruled-out buckets

    def test_returns_none_when_bom_does_not_reconcile(self):
        # The majority case in real data (80 of 98 entries, tenant-wide): the
        # ETL can't even trust the material baseline, so nothing may be
        # claimed as ruled out. Must fall back to the generic wording.
        assert _bom_confirmed_not_material_message(
            {"bomReconciles": False}, {"overheadCost": -439.78}
        ) is None

    def test_returns_none_when_no_bucket_actually_moved(self):
        assert _bom_confirmed_not_material_message({"bomReconciles": True}, {}) is None

    def test_never_claims_not_material_when_material_itself_moved(self):
        # Real shape from orders 421578/745786: bomReconciles=True but
        # materialCost has a small non-zero delta -- asserting "confirmed not
        # material" would directly contradict the same record's own delta.
        assert _bom_confirmed_not_material_message(
            {"bomReconciles": True},
            {"materialCost": 65.42, "overheadCost": 29.44},
        ) is None

    def test_only_claims_unchanged_for_buckets_actually_at_zero(self):
        # toolCost is ABSENT from delta (unknown), not proven zero -- must not
        # be folded into "confirmed unchanged" on an assumption.
        msg = _bom_confirmed_not_material_message(
            {"bomReconciles": True},
            {"materialCost": 0, "workerCost": 0, "overheadCost": -439.78},
        )
        assert "labor cost" in msg  # workerCost was present at 0 -- provable
        assert "tool cost" not in msg  # absent from delta -- not provable

    def test_price_change_message_names_the_material(self):
        msg = _bom_price_change_message(
            {"priceChangeDetected": True, "materialName": "OCDL-B"}, include_figures=False
        )
        assert "OCDL-B" in msg
        assert "$" not in msg  # customer/cause-only path: no unit prices

    def test_price_change_message_includes_unit_costs_for_internal(self):
        msg = _bom_price_change_message(
            {"priceChangeDetected": True, "materialName": "OCDL-B",
             "priorUnitCost": 500.0, "newUnitCost": 4000.0},
            include_figures=True,
        )
        assert "500" in msg and "4,000" in msg

    def test_price_change_message_none_when_not_detected(self):
        assert _bom_price_change_message({"bomReconciles": True}, include_figures=True) is None


class TestEstimateChangeMessageEvidenceIntegration:
    # Real shape: order 102380's CURRENT entry (v2) is a flat re-save
    # (identical to v1), so its OWN delta is empty; the actual bucket
    # movement lives on v1. _prefer_non_degenerate_sibling must reach back
    # past a same-version match to find it.
    HISTORY = [
        {"version": 0, "isCurrent": False, "confidence": "low",
         "evidence": {"bomReconciles": True}, "delta": None, "snapshot": {}},
        {"version": 1, "isCurrent": False, "confidence": "low",
         "evidence": {"bomReconciles": True},
         "delta": {"totalCost": 760.36, "overheadCost": -439.78, "margin": 1450.15},
         "snapshot": {"totalCost": 4651.80}},
        {"version": 2, "isCurrent": True, "confidence": "low", "reason": None,
         "evidence": {"bomReconciles": True}, "delta": {}, "snapshot": {"totalCost": 4651.80}},
    ]

    def test_reaches_back_past_a_flat_resave_for_the_real_delta(self):
        current = next(e for e in self.HISTORY if e["isCurrent"])
        resolved = _prefer_non_degenerate_sibling(self.HISTORY, current)
        assert resolved["delta"]["overheadCost"] == -439.78

    def test_end_to_end_message_names_overhead_not_the_generic_phrase(self):
        current = next(e for e in self.HISTORY if e["isCurrent"])
        resolved = _prefer_non_degenerate_sibling(self.HISTORY, current)
        msg = build_estimate_change_message("Project", resolved, include_figures=False)
        assert "overhead cost" in msg
        assert "no specific material attributable" not in msg

    def test_a_real_human_reason_still_takes_priority_over_evidence(self):
        # If the ETL ever DOES record a real reason alongside bomReconciles,
        # the human explanation must win -- it's more authoritative than a
        # mechanism-level bucket fact.
        entry = {
            "confidence": "low", "reason": "Customer Requested To Change",
            "evidence": {"bomReconciles": True},
            "delta": {"overheadCost": -1636.55}, "snapshot": {},
        }
        msg = build_estimate_change_message("Project", entry, include_figures=False)
        assert "Customer Requested To Change" in msg
        assert "confirmed this wasn't" not in msg


# ── Widened change-question gate (mirrors reporting_agent.py's copy) ────────
# _is_estimate_change_question/_is_schedule_change_question used to require a
# FIXED phrase ("why did/is/has/does") plus, for estimate, the EXACT
# substring "estimate". "why the estimation changed?" matched neither and
# fell through past this dedicated builder into the general RAG path (see
# reporting_agent.py's TestRagDomainGrounding for what that let through
# live). Widened to a "why" + change/move/difference-stem pairing (order-
# agnostic) plus a stem match covering every real inflection.

from source.agents.insight_agent import (  # noqa: E402
    _is_estimate_change_question,
    _is_schedule_change_question,
)


class TestWidenedChangeQuestionGate:
    def test_natural_paraphrases_now_reach_the_dedicated_builder(self):
        assert _is_estimate_change_question("why the estimation changed?")
        assert _is_estimate_change_question("why the estimate changed")
        assert _is_schedule_change_question("why has the schedule shifted")

    def test_original_fixed_phrases_still_match(self):
        assert _is_estimate_change_question("why did the estimate price change?")
        assert _is_schedule_change_question("why did the tentative start date move?")

    def test_a_current_value_question_is_not_mistaken_for_a_change_question(self):
        assert not _is_estimate_change_question("why is cost over budget")

    def test_topic_alone_without_why_does_not_match(self):
        assert not _is_estimate_change_question("what is the estimate")


# ── Real reason must never be gated by confidence ───────────────────────────
# confidence is entirely evidence.bomReconciles-derived (does material cost
# reconcile against the bill of materials) -- a real, human-entered reason is
# a SEPARATE signal it must never suppress. Confirmed live: 17 of 24 multi-
# version projects in tn88351775 have confidence="none" alongside a real
# reason ("Customer Requested To Change") on file, and the answer discarded
# it entirely ("I don't have a confirmed reason on file").

from source.agents.insight_agent import _moved_bucket_names  # noqa: E402


class TestRealReasonNeverGatedByConfidence:
    ENTRY_NONE_CONFIDENCE_REAL_REASON = {
        "confidence": "none", "reason": "Customer Requested To Change",
        "delta": {"overheadCost": -1256.59}, "snapshot": {},
    }

    def test_reporting_reports_the_real_reason_and_the_bucket(self):
        msg = build_estimate_change_message(
            "SYN-Project", self.ENTRY_NONE_CONFIDENCE_REAL_REASON, include_figures=False
        )
        assert "Customer Requested To Change" in msg
        assert "overhead cost" in msg
        assert "$" not in msg

    def test_insight_reports_the_real_reason_and_the_dollar_figure(self):
        msg = build_estimate_change_message(
            "SYN-Project", self.ENTRY_NONE_CONFIDENCE_REAL_REASON, include_figures=True
        )
        assert "Customer Requested To Change" in msg
        assert "1,257" in msg or "1256" in msg

    def test_missing_confidence_is_treated_the_same_as_none(self):
        entry = {"reason": "Customer Requested To Change", "delta": {}, "snapshot": {}}
        msg = build_estimate_change_message("SYN-Project", entry, include_figures=False)
        assert "Customer Requested To Change" in msg

    def test_high_confidence_still_takes_the_unhedged_branch(self):
        # Must not regress: a real "high" confidence entry (never occurs in
        # this tenant's live data, but the ETL contract supports it) still
        # gets the plain, unhedged reason -- not the hedged "It appears
        # related to" wording the widened branch produces for low/none.
        entry = {"confidence": "high", "reason": "Weather delay", "delta": {}, "snapshot": {}}
        msg = build_estimate_change_message("SYN-Project", entry, include_figures=False)
        assert msg == "Your estimate was updated. Reason on file: Weather delay."


class TestBucketMovementIndependentOfBomReconciles:
    def test_moved_bucket_names_ignores_bomreconciles_entirely(self):
        # _moved_bucket_names takes only delta -- no evidence/bomReconciles
        # parameter at all, unlike _bom_confirmed_not_material_message.
        assert _moved_bucket_names({"overheadCost": -1256.59}) == ["overhead cost"]
        assert _moved_bucket_names({"materialCost": 2000.0}) == ["material cost"]
        assert _moved_bucket_names({}) == []

    def test_no_real_reason_bomreconciles_false_still_names_the_bucket(self):
        # Tier 3: bomReconciles=False means _bom_confirmed_not_material_message
        # correctly declines the "confirmed not material" CLAIM, but the
        # bucket-movement FACT is independent of that and must still surface
        # -- for the customer (no-figures) path only, see the next test.
        entry = {
            "confidence": "none", "reason": None,
            "evidence": {"bomReconciles": False},
            "delta": {"overheadCost": -500.0}, "snapshot": {},
        }
        msg = build_estimate_change_message("SYN-Project", entry, include_figures=False)
        assert "overhead cost" in msg
        assert "can't confirm the exact cause" in msg

    def test_insight_still_gets_its_own_richer_figure_fallback_not_tier_3(self):
        # Real bug, fixed while testing: tier 3 must NOT fire for
        # include_figures=True, or Insight regresses from its own existing
        # figure-based "no confirmed cause" fallback (dollar totals) down to
        # a bucket-name-only message with no figures at all. Verified against
        # the real gold_eden project 7890 shape (materialCost delta $2,000).
        entry = {
            "version": 6, "isCurrent": True, "confidence": None, "reason": None,
            "snapshot": {"totalCost": 5085.63, "materialCost": 4000},
            "delta": {"totalCost": 2180, "materialCost": 2000},
        }
        msg = build_estimate_change_message("SYN-Project", entry, include_figures=True)
        assert "$2,906" in msg and "$5,086" in msg  # the richer before/after figure path
        assert "material cost changed by $2,000" in msg
        assert "can't confirm the exact cause" not in msg  # tier 3's own phrasing, must not fire


# ── Bare "what is the material/worker cost" -- real gap, fixed. Confirmed
# live: this phrasing had no path in InsightAgent at all and silently fell
# through to the generic variance_response (committedSpend), answering a
# different question than what was asked. ─────────────────────────────────

def _mock_role_and_permissions_collection(roles: list[str], permissions: list[str]):
    collection = MagicMock()
    collection.find_one.return_value = {"roles": roles, "permissions": permissions}
    return collection


def test_material_cost_question_with_bpe_cst_returns_the_figure(ctx):
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {"materialCost": 66.89, "workerCost": 505.01, "toolCost": 0, "overheadCost": 257.35}
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ), patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_role_and_permissions_collection(["sales"], ["BPE.CST"]),
    ):
        result = agent.variance_response(
            ctx("what is the material cost on henderson"), "henderson"
        )
    assert result is not None
    assert "material cost" in result.message.lower()
    assert "$66.89" in result.message


def test_worker_cost_question_without_bpe_cst_is_refused(ctx):
    # Deliberately a role WITHOUT BPE.CST -- unlike margin/estimatedCost
    # (gated on is_admin, since role_permission has no field-level
    # distinction there), BPE.CST genuinely differs by role: confirmed
    # live, every customer role lacks it while sales/admin hold it.
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {"materialCost": 66.89, "workerCost": 505.01, "toolCost": 0, "overheadCost": 257.35}
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ), patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_role_and_permissions_collection(["labour"], ["TAS.REA"]),
    ):
        result = agent.variance_response(
            ctx("what is the worker cost on henderson"), "henderson"
        )
    assert result is not None
    assert "not able to share the labor cost" in result.message.lower()
    assert "505" not in result.message


def test_why_did_material_cost_change_still_reaches_estimate_change_path(ctx):
    # Regression guard: _cost_breakdown_category must exclude "why" so this
    # phrasing stays owned by _is_estimate_change_question (the causal-
    # explanation path), never hijacked by the new bare-lookup handler.
    from source.agents.insight_agent import _cost_breakdown_category, _is_estimate_change_question
    utterance = "why did the material cost change on henderson"
    assert _cost_breakdown_category(utterance) is None
    assert _is_estimate_change_question(utterance) is True


def test_intent_utterance_overrides_bare_reply_for_total_cost(ctx):
    # Real bug, fixed: continuing an open "which project?" goal after
    # "what is the project cost" calls variance_response with
    # context.utterance == the reply naming the project (e.g. "Henderson
    # Roofing"), which carries none of TOTAL_COST_SIGNALS' words -- without
    # intent_utterance carrying the ORIGINAL question forward, this fell
    # through to the generic committedSpend/outstandingInvoices view instead
    # of the estimatedCost figure actually asked for. Confirmed live
    # (fieldcraftmind, 2026-08-31).
    agent = InsightAgent()
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
        return_value=_mock_role_collection(["admin"], permissions=["EST.REA"]),
    ):
        result = agent.variance_response(
            ctx("Henderson Roofing"), "Henderson Roofing",
            intent_utterance="what is the project cost",
        )
    assert result is not None
    assert "$46,000" in result.message


def test_no_intent_utterance_falls_back_to_context_utterance(ctx):
    # A fresh, non-continuation question (no goal being continued) passes
    # no intent_utterance -- must behave exactly as before this fix,
    # detecting the question type from context.utterance itself.
    agent = InsightAgent()
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
        return_value=_mock_role_collection(["admin"], permissions=["EST.REA"]),
    ):
        result = agent.variance_response(
            ctx("what is the project cost on henderson"), "henderson"
        )
    assert result is not None
    assert "$46,000" in result.message


def test_total_cost_permission_based_not_role_based(ctx):
    """2026-09-10 fix: total project cost is gated on the real EST.REA
    permission (confirmed live against the actual source role_permission
    table across all 3 prod tenants: held by sales/admin/purchaseOfficer/
    superAdmin, refused for foreman/labour/shopSupervisor/contractorLabour/
    General Employee) -- NOT the role-name check margin still uses. sales
    previously would have been refused here (same gate as margin); now
    correctly sees the figure because sales genuinely holds EST.REA."""
    agent = InsightAgent()
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
        return_value=_mock_role_collection(["sales"], permissions=["EST.REA"]),
    ):
        result = agent.variance_response(
            ctx("what is the total project cost on henderson"), "henderson"
        )
    assert result is not None
    assert "$46,000" in result.message


def test_total_cost_refused_without_est_rea_permission(ctx):
    """A role holding no EST.REA (e.g. foreman, per real source data) is
    refused -- even though it would previously have been irrelevant here
    (the old gate only ever checked role name, never a real permission)."""
    agent = InsightAgent()
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
        return_value=_mock_role_collection(["foreman"], permissions=[]),
    ):
        result = agent.variance_response(
            ctx("what is the total project cost on henderson"), "henderson"
        )
    assert result is not None
    assert "not able to share the total project cost" in result.message
    assert "46,000" not in result.message


def test_total_cost_denial_offers_no_fallback_the_user_also_lacks(ctx):
    """Real gap, fixed: the total_cost denial used to hardcode "I can help
    with your project's billing/invoice information instead" regardless of
    whether the caller could see that either. Labor holds neither EST.REA,
    INV.REA, nor BPE.CST -- confirmed live -- so the denial must fall back
    to something ungated for internal staff (schedule/status), not offer
    billing/invoice or an estimate breakdown they also can't see."""
    agent = InsightAgent()
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
        return_value=_mock_role_collection(["labour"], permissions=[]),
    ):
        result = agent.variance_response(
            ctx("what is the total project cost on henderson"), "henderson"
        )
    assert result is not None
    assert "billing/invoice" not in result.message
    assert "estimate breakdown" not in result.message
    assert "schedule or status" in result.message


def test_total_cost_denial_offers_invoice_fallback_when_user_holds_it(ctx):
    """A role that DOES hold INV.REA (but still lacks EST.REA) gets pointed
    at billing/invoice info, since that's a real, reachable alternative for
    them -- the dynamic offer, not a hardcoded one."""
    agent = InsightAgent()
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
        return_value=_mock_role_collection(["customer"], permissions=["INV.REA"]),
    ):
        result = agent.variance_response(
            ctx("what is the total project cost on henderson"), "henderson"
        )
    assert result is not None
    assert "billing/invoice" in result.message


def test_margin_denial_offers_no_fallback_the_user_also_lacks(ctx):
    """Same fix, margin variant: Labor lacks EST.MOD, INV.REA, and BPE.CST,
    so the margin denial must not offer the estimate breakdown (BPE.CST)
    it used to hardcode."""
    agent = InsightAgent()
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
        return_value=_mock_role_collection(["labour"], permissions=[]),
    ):
        result = agent.variance_response(
            ctx("what is the profit margin on henderson"), "henderson"
        )
    assert result is not None
    assert "estimate breakdown" not in result.message
    assert "billing/invoice" not in result.message
    assert "schedule or status" in result.message


def test_project_estimate_phrasing_routes_to_gated_total_cost(ctx):
    """Real gap, fixed: "what is the project estimate" -- a natural phrasing
    of the same bare-total-cost question -- used to match none of
    TOTAL_COST_SIGNALS and fall through to the ungated default variance
    path. Confirmed it now routes to the EST.REA-gated total_cost branch,
    same as "what is the total project cost"."""
    agent = InsightAgent()
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
        return_value=_mock_role_collection(["labour"], permissions=[]),
    ):
        result = agent.variance_response(
            ctx("what is the project estimate on henderson"), "henderson"
        )
    assert result is not None
    assert "not able to share the total project cost" in result.message
    assert "46,000" not in result.message


def test_default_variance_budget_status_refused_without_est_rea(ctx):
    """Sibling gap to AB#30855, found live: the default variance_response
    (reached by "why is cost over budget" and any phrasing that doesn't
    match TOTAL_COST_SIGNALS) composed a budget-status sentence naming a
    dollar amount over/under "the original estimate" -- derived from
    remainingBudget = estimatedCost - committedSpend, the SAME protected
    figure EST.REA already gates elsewhere -- with no permission check at
    all. Isolates that gate by granting INV.REA/JBL.REA too, so
    committedSpend/schedule stay visible and only the budget-status
    sentence is refused."""
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "committedSpend": 5000, "outstandingInvoicesTotal": 0,
        "remainingBudget": 618.61, "scheduleRisk": "on_track", "slipDays": 0,
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ), patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_role_and_permissions_collection(["labour"], ["INV.REA", "JBL.REA"]),
    ):
        result = agent.variance_response(
            ctx("why is cost over budget on henderson"), "henderson"
        )
    assert result is not None
    assert "$5,000" in result.message
    assert "619" not in result.message
    assert "estimate" not in result.message.lower()


def test_default_variance_budget_status_shown_with_est_rea(ctx):
    """Happy path for the same gate: a role holding EST.REA still sees the
    budget-status sentence exactly as before this fix."""
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "committedSpend": 5000, "outstandingInvoicesTotal": 0,
        "remainingBudget": 618.61, "scheduleRisk": "on_track", "slipDays": 0,
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ), patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_role_and_permissions_collection(["admin"], ["INV.REA", "EST.REA"]),
    ):
        result = agent.variance_response(
            ctx("why is cost over budget on henderson"), "henderson"
        )
    assert result is not None
    assert "619" in result.message
    assert "remains under the original estimate" in result.message


def test_margin_est_rea_alone_is_not_sufficient(ctx):
    """Regression guard: margin must NOT accept EST.REA as a substitute for
    EST.MOD -- a user holding only EST.REA (e.g. sales without EST.MOD, or
    customer/purchaseOfficer who hold EST.REA in real data too) must stay
    refused for MARGIN specifically, unlike total_cost (which DOES accept
    EST.REA). If this ever starts passing with the refusal message replaced
    by a real figure, EST.REA has been wrongly wired into the margin branch."""
    agent = InsightAgent()
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
        return_value=_mock_role_collection(["sales"], permissions=["EST.REA"]),
    ):
        result = agent.variance_response(
            ctx("what is the profit margin on henderson"), "henderson"
        )
    assert result is not None
    assert "not able to share margin" in result.message
    assert "5,000" not in result.message


def test_estimated_amount_returns_estimatedCost_not_committedSpend(ctx):
    # Real gap, fixed: confirmed live -- "what is the estimated amount"
    # (a real, common phrasing of the already-covered "estimated cost")
    # fell through every check and hit the generic variance_response
    # instead, answering with committedSpend/remainingBudget -- the SAME
    # figure as the project's INVOICED amount, not the estimate. That made
    # "estimated amount" and "invoiced amount" return an identical number
    # for an unrelated reason.
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_doc = {
        "estimatedCost": 972.23, "margin": 142.97,
        "committedSpend": 486.11, "outstandingInvoicesTotal": 486.11,
        "remainingBudget": 486.11, "scheduleRisk": "on_track", "slipDays": 0,
    }
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=_mock_health_doc(health_doc),
    ), patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_role_collection(["admin"], permissions=["EST.REA"]),
    ):
        result = agent.variance_response(
            ctx("what is the estimated amount on henderson"), "henderson"
        )
    assert result is not None
    assert "$972" in result.message
    assert "486" not in result.message


# --- isDeleted exclusion (PR review, Raja) --------------------------------
# Real gap, fixed: the isDeleted PROJECT_HEALTH_COLLECTION.find_one filter
# added to resolve_margin_response, resolve_cost_breakdown_response,
# variance_response, _estimate_change_response, and _schedule_change_response
# had zero asserting test coverage -- a future edit could silently drop the
# filter from any of these 5 sites (surfacing a deleted project's margin/
# cost/variance/reasoning figures) with nothing in the suite catching it.

def test_margin_lookup_excludes_deleted(ctx):
    # Real-permissions role mock required (EST.MOD) since the margin
    # permission gate now short-circuits BEFORE the Mongo lookup this test
    # is actually exercising (see the early gate added to variance_response)
    # -- a role with no explicit permissions would now be denied before
    # find_one is ever called, leaving nothing for call_args to assert on.
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_collection = _mock_health_doc({"estimatedCost": 46000, "margin": 5000})
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=health_collection,
    ), patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_role_collection(["admin"], permissions=["EST.MOD"]),
    ):
        agent.variance_response(ctx("what is the profit margin on henderson"), "henderson")
    call_filter = health_collection.find_one.call_args.args[0]
    assert call_filter["isDeleted"] == {"$ne": True}


def test_cost_breakdown_lookup_excludes_deleted(ctx):
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_collection = _mock_health_doc(
        {"materialCost": 66.89, "workerCost": 505.01, "toolCost": 0, "overheadCost": 257.35}
    )
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=health_collection,
    ), patch(
        "source.resolvers.user_role_resolver.get_collection",
        return_value=_mock_role_and_permissions_collection(["sales"], ["BPE.CST"]),
    ):
        agent.variance_response(ctx("what is the material cost on henderson"), "henderson")
    call_filter = health_collection.find_one.call_args.args[0]
    assert call_filter["isDeleted"] == {"$ne": True}


def test_variance_lookup_excludes_deleted(ctx):
    agent = InsightAgent()
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    health_collection = _mock_health_doc({
        "committedSpend": 142000, "outstandingInvoicesTotal": 5000,
        "scheduleRisk": "on_track", "slipDays": 0,
    })
    with patch(
        "source.resolvers.entity_resolver.get_collection",
        return_value=_mock_project_matches(docs),
    ), patch(
        "source.agents.insight_agent.get_collection",
        return_value=health_collection,
    ):
        agent.variance_response(ctx("why is cost over budget on henderson"), "henderson")
    call_filter = health_collection.find_one.call_args.args[0]
    assert call_filter["isDeleted"] == {"$ne": True}


def test_estimate_change_lookup_excludes_deleted(ctx):
    agent = InsightAgent()
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
        "source.agents.insight_agent.get_collection",
        return_value=health_collection,
    ):
        agent.variance_response(ctx("why did the estimate change on henderson"), "henderson")
    call_filter = health_collection.find_one.call_args.args[0]
    assert call_filter["isDeleted"] == {"$ne": True}


def test_schedule_change_lookup_excludes_deleted(ctx):
    agent = InsightAgent()
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
        "source.agents.insight_agent.get_collection",
        return_value=health_collection,
    ):
        agent.variance_response(ctx("why did the start date move on henderson"), "henderson")
    call_filter = health_collection.find_one.call_args.args[0]
    assert call_filter["isDeleted"] == {"$ne": True}
