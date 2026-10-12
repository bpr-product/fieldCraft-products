"""Unit tests for NavigationAgent — keyword routing, LLM-classified routing,
entity resolution, RBAC enforcement, already-on-page detection, data-question
redirects, and on-page feature lookup ("where is X" for something visible)."""
from unittest.mock import MagicMock


def test_navigate_keyword_only(nav_agent, ctx):
    # Real bug, fixed (PR review, Raja): this used to test "invoices" ->
    # the flat, non-entity /invoice route (invoice_list), which was later
    # removed entirely (confirmed-broken frontend page -- see
    # ENTITY_TAB_ALIAS_ROUTE_STATE's invoice_list comment). This test went
    # stale at that point and nobody updated it -- "invoices" now resolves
    # to project_invoice_view (entity_required="project"), a genuinely
    # different response shape (fallback asking which project, not a bare
    # navigate) covered by its own tests below
    # (test_invoice_keyword_with_no_project_asks_which_project /
    # test_invoice_keyword_resolves_via_page_context). Swapped to another
    # genuinely flat, non-entity keyword so this test keeps testing what it
    # was actually meant to: a bare keyword resolving straight to navigate.
    response = nav_agent.try_keyword_navigation(ctx("stock levels"))
    assert response is not None
    assert response.type == "navigate"
    assert response.route == "/materialmart"


def test_navigate_explicit_phrase(nav_agent, ctx):
    response = nav_agent.try_keyword_navigation(ctx("take me to the purchase orders"))
    assert response.type == "navigate"
    assert response.route == "/purchaseOrder"


def test_customers_navigates_through_admin_shell_not_bare_page(nav_agent, ctx):
    # Regression: customer_table used to have no target_route, so EDEN sent
    # users to the bare /customertable page — missing the Admin tab bar and
    # Notes panel a human sees by clicking Admin Management > Customers.
    # Verified against the frontend's Admin.tsx tabs array: Customers is
    # selectedTab "3".
    response = nav_agent.try_keyword_navigation(ctx("show me the customers list"))
    assert response.type == "navigate"
    assert response.route == "/admin"
    assert response.routeState == {"selectedTab": "3"}


def test_employees_navigates_through_admin_shell(nav_agent, ctx):
    response = nav_agent.try_keyword_navigation(ctx("show me the employee list"))
    assert response.type == "navigate"
    assert response.route == "/admin"
    assert response.routeState == {"selectedTab": "1"}


def test_keyword_match_ignores_substring_false_positives(nav_agent, ctx):
    # "reports" must not match the "po" keyword (Purchase Orders) as a substring.
    response = nav_agent.try_keyword_navigation(ctx("show me reports"))
    assert response is None


def test_keyword_match_word_boundary_are_vs_ar(nav_agent, ctx):
    # "are" must not match the "ar" keyword (Invoices/accounts-receivable).
    response = nav_agent.try_keyword_navigation(ctx("where are my tools"))
    assert response is not None
    # tool_management's target_route is /admin (+selectedTab state) — the
    # bare /toolManagement page has no tab bar or Notes panel, see
    # RouteEntry.target_route.
    assert response.route == "/admin"
    assert response.routeState == {"selectedTab": "4"}


def test_keyword_match_handles_plurals(nav_agent, ctx):
    # A non-entity route: "schedules" (plural of the gantt_charts keyword
    # "schedule") would otherwise ask "which project?" since gantt_charts
    # requires an entity — that's correct behavior, tested separately below.
    response = nav_agent.try_keyword_navigation(ctx("show me the timesheets"))
    assert response is not None
    assert response.route == "/timesheets"


def test_keyword_match_plural_on_entity_route_asks_for_project(nav_agent, ctx):
    response = nav_agent.try_keyword_navigation(ctx("show me the schedules"))
    assert response is not None
    assert response.type == "fallback"
    assert "which project" in response.message.lower()


def test_no_keyword_match_returns_none(nav_agent, ctx):
    response = nav_agent.try_keyword_navigation(ctx("who installed the roof shingles"))
    assert response is None


def test_navigate_infers_context_from_llm_entity(nav_agent, ctx):
    nav_agent.entity_resolver.resolve_project.return_value = [
        __import__("source.model.navigation_models", fromlist=["ProjectMatch"])
        .ProjectMatch(project_uid="uid-1", project_name="Henderson Roofing", status="active")
    ]
    understanding = {
        "intent": "navigate",
        "route_id": "gantt_charts",
        "confidence": 0.9,
        "extracted_entity": "henderson",
        "insight": "Shows the schedule.",
    }
    response = nav_agent.resolve_classified(ctx("show schedule", currentRoute="/soldproduction"), understanding)
    assert response.type == "navigate"
    # gantt_charts has no :projectId URL segment — project context travels in
    # the `project` field (frontend passes it via router state), not the URL.
    assert response.project is not None
    assert response.project.id == "uid-1"
    nav_agent.entity_resolver.resolve_project.assert_called_once()


def test_llm_entity_matching_current_page_project_collapses_to_exact_id(
    nav_agent, ctx, project_match
):
    # Real bug, fixed: confirmed live -- "yes" (no signal of its own) after a
    # suggestion got stripped by the role-scope guardrail falls through to a
    # fresh LLM dispatch, which extracts the project by NAME from
    # conversation history (e.g. "Peter Parker") since "yes" itself names
    # nothing. Real Gold data has severe name duplication (3+ projects named
    # "Peter Parker"), so resolving by name alone re-asks "which project?"
    # even though the user is SITTING on that exact project's own page right
    # now (currentEntity carries its exact id). When the LLM's extracted
    # name matches the CURRENT page's own project (resolved by exact id, not
    # the frontend's rarely-sent name field), the exact id must win instead
    # of re-triggering an unrelated name collision.
    nav_agent.entity_resolver.resolve_by_id.return_value = [
        project_match("Peter Parker", "uid-272000"),
    ]
    nav_agent.entity_resolver.resolve_project.return_value = [
        project_match("Peter Parker", "uid-1"),
        project_match("Peter Parker", "uid-2"),
        project_match("Peter Parker", "uid-3"),
    ]
    understanding = {
        "intent": "navigate",
        "route_id": "project_information",
        "confidence": 0.9,
        "extracted_entity": "Peter Parker",
    }
    response = nav_agent.resolve_classified(
        ctx("yes", currentEntity={"type": "project", "id": "uid-272000"}),
        understanding,
    )
    assert response.type == "navigate"
    assert response.project is not None
    assert response.project.id == "uid-272000"
    nav_agent.entity_resolver.resolve_project.assert_not_called()


def test_llm_entity_naming_a_different_project_still_disambiguates(
    nav_agent, ctx, project_match
):
    # The collapse-to-page-id fix must NOT fire when the extracted name
    # genuinely differs from the page's own project -- that's a real switch
    # request, not a name collision to resolve away.
    nav_agent.entity_resolver.resolve_by_id.return_value = [
        project_match("Henderson Roofing", "uid-henderson"),
    ]
    nav_agent.entity_resolver.resolve_project.return_value = [
        project_match("Peter Parker", "uid-1"),
        project_match("Peter Parker", "uid-2"),
    ]
    understanding = {
        "intent": "navigate",
        "route_id": "project_information",
        "confidence": 0.9,
        "extracted_entity": "Peter Parker",
    }
    response = nav_agent.resolve_classified(
        ctx("what about Peter Parker", currentEntity={"type": "project", "id": "uid-henderson"}),
        understanding,
    )
    assert response.type == "disambiguation"
    nav_agent.entity_resolver.resolve_project.assert_called_once()


def test_llm_entity_matching_page_id_collapses_to_that_project(
    nav_agent, ctx, project_match, monkeypatch
):
    # Real bug, fixed: confirmed live -- "show me the invoice" while sitting
    # on a project's own page extracted_entity came back as "1239", the raw
    # page-context projectId, not a name. The sibling name-match safety net
    # (test_llm_entity_matching_current_page_project_collapses_to_exact_id,
    # above) never fires here -- "1239" trivially never equals the page's
    # real project name -- so entity_name stayed "1239", got searched as a
    # NAME, found nothing, and produced "I couldn't find a project matching
    # '1239'" while sitting on that exact project's page the whole time. The
    # id-match check must fire BEFORE the name-match one, using the page id
    # directly, not a name lookup.
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = {"INV.REA"}
    nav_agent.entity_resolver.resolve_by_id.return_value = [
        project_match("TESTShanel TESTSchuppe, Lake Damienfurt-Roof Enhancement-268302", "1239"),
    ]
    understanding = {
        "intent": "navigate",
        "route_id": "project_invoice_view",
        "confidence": 0.95,
        "extracted_entity": "1239",
    }
    response = nav_agent.resolve_classified(
        ctx("show me the invoice", currentEntity={"type": "project", "id": "1239"}),
        understanding,
    )
    assert response.type == "navigate"
    assert response.project is not None
    assert response.project.id == "1239"
    nav_agent.entity_resolver.resolve_project.assert_not_called()


def test_llm_entity_redacted_to_presidio_placeholder_falls_back_to_utterance(
    nav_agent, ctx, project_match, monkeypatch
):
    # Real bug, fixed: confirmed live -- "take me to ron kasper" (6 real
    # projects named "Ron Kasper, Cedarburg-..." exist for the tenant) came
    # back with extracted_entity="<PERSON>", not "ron kasper". understand()'s
    # user-role prompt passes through guardrails' Presidio rail before the
    # model ever sees it -- every project in this domain IS named after a
    # customer, so Presidio has no way to tell a full name in chat is a
    # legitimate project reference and redacts it like any other PERSON
    # entity; the model just echoes the placeholder back as its own
    # "extraction". Trusting that placeholder as a literal search term found
    # zero projects (or, with no other signal, produced the generic "Which
    # project did you mean?" message) despite real matches existing. Falling
    # back to the deterministic, LLM-free stop-word stripper on EDEN's own
    # un-redacted context.utterance (never sent anywhere, so it can't be
    # redacted) recovers the real name.
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = {"PRO.REA"}
    nav_agent.entity_resolver.resolve_project.return_value = [
        project_match("Ron Kasper, Cedarburg-Roof Enhancement-885119", "uid-1"),
        project_match("Ron Kasper, Cedarburg-Roof Enhancement-843223", "uid-2"),
    ]
    understanding = {
        "intent": "navigate",
        "route_id": "project_information",
        "confidence": 0.8,
        "extracted_entity": "<PERSON>",
    }
    response = nav_agent.resolve_classified(
        ctx("take me to ron kasper"), understanding,
    )
    assert response.type == "disambiguation"
    nav_agent.entity_resolver.resolve_project.assert_called_once_with(
        "ron kasper", "test-tenant"
    )


def test_disambiguation_two_projects(nav_agent, ctx, project_match):
    nav_agent.entity_resolver.resolve_project.return_value = [
        project_match("Henderson Roofing", "uid-1"),
        project_match("Henderson Retail", "uid-2"),
    ]
    understanding = {"intent": "navigate", "route_id": "gantt_charts", "confidence": 0.9,
                      "extracted_entity": "henderson"}
    response = nav_agent.resolve_classified(ctx("henderson schedule"), understanding)
    assert response.type == "disambiguation"
    assert len(response.options) == 2


def test_entity_no_match_returns_fallback(nav_agent, ctx):
    nav_agent.entity_resolver.resolve_project.return_value = []
    understanding = {"intent": "navigate", "route_id": "gantt_charts", "confidence": 0.9,
                      "extracted_entity": "xyznonexistent"}
    response = nav_agent.resolve_classified(ctx("xyznonexistent schedule"), understanding)
    assert response.type == "fallback"
    assert "xyznonexistent" in response.message


def test_entity_required_with_no_entity_name_asks_which_project(nav_agent, ctx):
    response = nav_agent.try_keyword_navigation(ctx("schedule"))
    assert response.type == "fallback"
    assert "which project" in response.message.lower()


# ── which-project fallback example must be topic-relevant, not a fixed
# generic "schedule" example ────────────────────────────────────────────
# Real bug, fixed (Issue 28731): confirmed live -- "i want upload delivery
# slip" (delivery_slip is entity_required="project", so asking which
# project IS correct and necessary here) got prompted with "Try: 'take me
# to Henderson schedule'" -- a hardcoded example completely unrelated to
# delivery slips, used for EVERY project-scoped route regardless of what
# was actually asked. Looked like EDEN had silently switched to an
# unrelated schedule request instead of continuing the delivery-slip one.

def test_delivery_slip_which_project_example_is_topic_relevant_not_schedule(nav_agent, ctx):
    response = nav_agent.try_keyword_navigation(ctx("delivery slip"))
    assert response.type == "fallback"
    assert "which project" in response.message.lower()
    assert "delivery slip" in response.message.lower()
    assert "schedule" not in response.message.lower()


def test_job_ledger_which_project_example_is_also_topic_relevant(nav_agent, ctx):
    # Same fix, a second project-scoped route -- confirms this isn't a
    # delivery_slip-only special case.
    response = nav_agent.try_keyword_navigation(ctx("job ledger"))
    assert response.type == "fallback"
    assert "job ledger" in response.message.lower()
    assert "schedule" not in response.message.lower()


def test_project_bom_example_strips_redundant_project_prefix(nav_agent, ctx):
    # "Henderson's Project Materials (BOM)" reads awkwardly (redundant
    # "Project"); the example clause strips it to "Henderson's Materials
    # (BOM)". The route's real label_template ("Project Materials (BOM)")
    # is untouched elsewhere -- only this one example clause is trimmed.
    response = nav_agent.try_keyword_navigation(ctx("bill of materials"))
    assert response.type == "fallback"
    assert "henderson's materials (bom)" in response.message.lower()
    assert "henderson's project materials" not in response.message.lower()


def test_fallback_not_in_registry(nav_agent, ctx):
    understanding = {"intent": "navigate", "route_id": "payroll_nonexistent", "confidence": 0.5}
    response = nav_agent.resolve_classified(ctx("take me to payroll"), understanding)
    assert response.type == "fallback"


def test_fallback_permission_denied(nav_agent, ctx, monkeypatch):
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = {"DASH.REA"}
    response = nav_agent.try_keyword_navigation(ctx("take me to invoices"))
    assert response.type == "fallback"
    assert "don't have access" in response.message


def test_permission_denied_with_no_related_page_has_no_suggestion(nav_agent, ctx, monkeypatch):
    # Real bug, fixed: confirmed live -- a permission denial for a route
    # sharing NO vocabulary with anything the role can reach used to fall
    # back to suggesting accessible[0], the FIRST registry route with
    # permission_required=None -- pure registry-order noise ("You don't have
    # access to Timesheets" followed by an unrelated "Create Asset" offer).
    # Attaching ANY suggestion here also made _update_goal_state open a
    # whole new multi-turn goal instead of returning the plain denial as a
    # single closed answer. With no genuinely related page (vocabulary
    # overlap > 1), suggestion must stay unset.
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = set()
    route = next(r for r in nav_agent.route_registry if r.id == "timesheets")
    response = nav_agent._check_permission(route, ctx("show me the timesheets"))
    assert response is not None
    assert response.type == "fallback"
    assert "don't have access to timesheets" in response.message.lower()
    assert response.suggestion is None


def test_permission_denied_create_bid_does_not_suggest_create_asset(nav_agent, ctx, monkeypatch):
    # AB#30621: create_asset carries permission_required=None (AB#28704 --
    # there's no real permission to gate it on), so it always passed the
    # accessible-candidate filter, and its keywords ("create asset", "add a
    # new asset") share only the generic verbs "create"/"new"/"add" with
    # create_bid's own keywords -- enough to clear the overlap > 1
    # threshold on filler alone. Confirmed live: a Labor Resource denied
    # Create Bid was told "you can see your own Create Asset", even though
    # Labor Resources have no Create Asset access either, and assets belong
    # to Customers, not Labor Resources. create_asset is never a genuine
    # standalone destination anyway -- direct navigation to it always
    # redirects to create_bid (test_create_asset_redirects_to_create_bid
    # below) -- so it must never be offered as a denial-fallback either.
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = set()
    route = next(r for r in nav_agent.route_registry if r.id == "create_bid")
    response = nav_agent._check_permission(route, ctx("create bid"))
    assert response is not None
    assert response.type == "fallback"
    assert response.suggestion is None or response.suggestion.label != "Create Asset"
    assert "create asset" not in response.message.lower()


def test_book_leave_is_not_a_resource_management_feature(nav_agent, ctx, monkeypatch):
    # Real gap, fixed (2026-09-15): AB#28708/AB#30636 had modeled "book
    # leave" as a genuine, reachable feature on resource_management (its
    # own "Book Leave button" page_feature, plus "book leave" in the
    # route's own intent_keywords) -- confirmed live to be dead code: the
    # frontend's ResourceList.tsx dialog only ever calls
    # setDatePickerOpen(false) (the close handler); nothing in that
    # component ever calls setDatePickerOpen(true), so the dialog has no
    # reachable trigger on the real page at all. Removed both the
    # page_feature and the intent_keywords entries -- resource_management
    # must never again resolve "book leave" to anything, for any
    # permission set, since there is nothing real there to send anyone to.
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    for permissions in ({"RES.REA"}, {"TIM.REA"}, {"RES.REA", "TIM.REA"}, set()):
        nav_agent.role_resolver.get_permissions.return_value = permissions
        route = next(r for r in nav_agent.route_registry if r.id == "resource_management")
        assert not any(
            "book leave" in kw or "leave" in kw for kw in route.intent_keywords
        ), f"resource_management.intent_keywords still references leave-booking: {route.intent_keywords}"
        assert not any(
            f.name == "Book Leave button" or f.name == "Book now button"
            for f in route.page_features
        ), f"resource_management still exposes a leave-booking page_feature under permissions={permissions}"


def test_book_leave_resolves_only_to_timesheets_regardless_of_resource_permission(
    nav_agent, ctx, monkeypatch
):
    # The only real, reachable "Book Leave" destination is Timesheets' own
    # page_feature (gated on TIM.REA, the user's own leave-booking
    # permission) -- holding RES.REA (resource_management's permission)
    # neither helps nor is required, since resource_management no longer
    # has any competing feature to prefer it over. A user with RES.REA but
    # not TIM.REA correctly gets nothing from this tier (falls through to
    # the LLM/other tiers instead of a stale direct match), rather than
    # being sent to a dialog that was never reachable in the real app.
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = {"TIM.REA"}
    response = nav_agent.try_feature_lookup(ctx("book leave"))
    assert response is not None
    assert response.type == "navigate"
    assert response.route == "/timesheets"
    assert response.highlight == "Book Leave button"
    assert response.highlightTargets == ["Book Leave"]

    nav_agent.role_resolver.get_permissions.return_value = {"RES.REA"}
    response = nav_agent.try_feature_lookup(ctx("book leave"))
    assert response is None


def test_workforce_denial_without_leave_permission_gets_plain_denial(nav_agent, ctx, monkeypatch):
    # Guards the phrase check itself: an utterance that reaches
    # resource_management for a non-leave reason ("show me the crew") must
    # not be redirected to Timesheets just because the user happens to hold
    # timesheets.READ -- the fallback only fires when the DENIED route's own
    # page_feature (Book Leave button) actually matches what was asked.
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = {"TIM.REA"}
    route = next(r for r in nav_agent.route_registry if r.id == "resource_management")
    response = nav_agent._check_permission(route, ctx("show me the crew"))
    assert response is not None
    assert response.type == "fallback"
    assert response.route is None


def test_denied_route_feature_name_collision_does_not_redirect(nav_agent, ctx, monkeypatch):
    # RBAC review of AB#30636's fix caught this: the fallback originally
    # matched on page_feature NAME alone, but the registry reuses generic
    # names ("Save button", "Go Back button", "Update (row)") across many
    # UNRELATED routes with different permissions -- e.g. po_details and
    # project_invoice_view each have their own, unrelated "Save button".
    # A user denied po_details (lacks PO.REA) saying "save purchase order"
    # must NOT be redirected to project_invoice_view just because the user
    # happens to hold INV.REA and that route also has something named
    # "Save button" -- project_invoice_view's own Save button keywords
    # ("save invoice changes", "save edits", "update invoice") don't
    # describe saving a purchase order at all. The fix requires the
    # alternate route's feature to ALSO independently match the utterance
    # via its own keywords, not just share a label.
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = {"INV.REA"}
    route = next(r for r in nav_agent.route_registry if r.id == "po_details")
    response = nav_agent._check_permission(route, ctx("save purchase order"))
    assert response is not None
    assert response.type == "fallback"
    assert response.route is None


def test_permission_granted_allows_navigation(nav_agent, ctx, monkeypatch, project_match):
    # Real bug, fixed (PR review, Raja): "take me to invoices" now resolves
    # to project_invoice_view (entity_required="project", since invoice_list
    # was removed and this route absorbed its bare keywords) -- unlike the
    # old flat /invoice page, granting INV.REA alone is no longer enough to
    # get a "navigate" response with NO project context at all (see
    # test_invoice_keyword_with_no_project_asks_which_project below for that
    # case). Page context (currentEntity) lets it resolve deterministically.
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = {"INV.REA"}
    nav_agent.entity_resolver.resolve_by_id.return_value = [
        project_match("Henderson Roofing", "uid-1"),
    ]
    response = nav_agent.try_keyword_navigation(
        ctx("take me to invoices", currentEntity={"type": "project", "id": "uid-1"})
    )
    assert response.type == "navigate"


def test_invoice_keyword_with_no_project_asks_which_project(nav_agent, ctx, monkeypatch):
    # Real bug, fixed (PR review, Raja): confirmed live -- project_invoice_
    # view's intent_keywords never actually got invoice_list's bare
    # invoice/billing keywords added, despite a comment nearby claiming
    # they were "absorbed." A no-project-context "take me to invoices"
    # scored 0 on the deterministic keyword tier and fell through to the
    # semantic-retrieval/LLM tier instead -- an undisclosed regression from
    # before invoice_list's removal. Now that the keywords are genuinely
    # present, the exact-keyword tier claims it immediately (no LLM call)
    # and -- correctly, since this route IS project-scoped -- asks which
    # project rather than guessing.
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = {"INV.REA"}
    response = nav_agent.try_keyword_navigation(ctx("take me to invoices"))
    assert response.type == "fallback"
    assert "which project" in response.message.lower()


def test_invoice_keyword_resolves_via_page_context(nav_agent, ctx, monkeypatch, project_match):
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = {"INV.REA"}
    nav_agent.entity_resolver.resolve_by_id.return_value = [
        project_match("Henderson Roofing", "uid-1"),
    ]
    response = nav_agent.try_keyword_navigation(
        ctx("show me invoices", currentEntity={"type": "project", "id": "uid-1"})
    )
    assert response.type == "navigate"
    # project_invoice_view is in ENTITY_TAB_ALIAS_ROUTE_STATE -- its own
    # single-item page needs an id EDEN never has for a general request, so
    # it resolves to project_information's tab that lists every invoice,
    # not its own raw pattern.
    assert response.route == "/soldproduction/projectinformation"
    assert response.routeState["tabName"] == "simple-tab-7"
    assert response.routeState["selectedTab"] == 7


# ── is_route_pattern_permitted: tab-alias blind spot ────────────────────────
# Real bug, fixed: ENTITY_TAB_ALIAS_ROUTE_STATE collapses 10 distinct,
# differently-permissioned tab routes (invoice, BOM, PO, job ledger, delivery
# slip, bill, payments, documents, WIP report, vendor bill) onto
# project_information's own bare URL, told apart only by
# routeState.selectedTab. A permission check keyed on the URL string ALONE
# (used by the role-scope guardrail and the remembered-suggestion replay)
# could therefore only ever recover project_information's own PRO.REA, never
# the tab's real permission -- meaning an account with PRO.REA but NOT
# INV.REA would pass a check that was silently checking the wrong thing.

def test_is_route_pattern_permitted_checks_real_tab_permission_not_project_information(
    nav_agent, ctx, monkeypatch
):
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    # Has PRO.REA (project_information's own permission) but NOT INV.REA
    # (project_invoice_view's real permission, aliased under tab 7).
    nav_agent.role_resolver.get_permissions.return_value = {"PRO.REA"}
    allowed = nav_agent.is_route_pattern_permitted(
        "/soldproduction/projectinformation",
        ctx("yes"),
        route_state={"selectedTab": 7, "rowData": "2079"},
    )
    assert allowed is False


def test_is_route_pattern_permitted_allows_tab_when_real_permission_held(
    nav_agent, ctx, monkeypatch
):
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = {"PRO.REA", "INV.REA"}
    allowed = nav_agent.is_route_pattern_permitted(
        "/soldproduction/projectinformation",
        ctx("yes"),
        route_state={"selectedTab": 7, "rowData": "2079"},
    )
    assert allowed is True


def test_is_route_pattern_permitted_bare_project_information_unaffected(
    nav_agent, ctx, monkeypatch
):
    # No selectedTab at all -- a genuine project_information visit must
    # still be governed by PRO.REA, not accidentally reinterpreted as a tab.
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = {"PRO.REA"}
    allowed = nav_agent.is_route_pattern_permitted(
        "/soldproduction/projectinformation",
        ctx("yes"),
        route_state={"rowData": "2079"},
    )
    assert allowed is True


def test_verify_remembered_suggestion_permission_checks_real_tab_permission(
    nav_agent, ctx, monkeypatch
):
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = {"PRO.REA"}
    denied = nav_agent.verify_remembered_suggestion_permission(
        "/soldproduction/projectinformation",
        "Peter Parker — Project Invoices",
        ctx("yes"),
        route_state={"selectedTab": 7, "rowData": "2079"},
    )
    assert denied is not None
    assert denied.type == "fallback"


# ── resolve_project_route_option permission check ──────────────────────────
# Real bug, fixed: this builder (used by AgentOrchestrator._attach_page_
# suggestion for the "would you like to see the Invoices page?" follow-up
# after a billing answer) never checked permission at all -- confirmed live,
# a customer (INV.PAY only) asking a billing question got offered and then
# navigated to project_invoice_view (needs INV.REA), a real cross-role leak.

def test_resolve_project_route_option_denies_without_permission(nav_agent, ctx, monkeypatch, project_match):
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = {"INV.PAY"}  # no INV.REA
    nav_agent.entity_resolver.resolve_by_id.return_value = [project_match("Peter Parker")]
    opt = nav_agent.resolve_project_route_option(
        "project_invoice_view", "tenant-1", entity_id="uid-1", context=ctx("what do I owe"),
    )
    assert opt is None


def test_resolve_project_route_option_allows_with_permission(nav_agent, ctx, monkeypatch, project_match):
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = {"INV.REA"}
    nav_agent.entity_resolver.resolve_by_id.return_value = [project_match("Peter Parker")]
    opt = nav_agent.resolve_project_route_option(
        "project_invoice_view", "tenant-1", entity_id="uid-1", context=ctx("what do I owe"),
    )
    assert opt is not None


def test_resolve_project_route_option_skips_check_without_context(nav_agent, ctx, project_match):
    # No context given -- permissive default, unchanged from before this fix
    # (every real caller passes context; this only covers a caller that
    # genuinely has no session, which shouldn't itself become a new denial).
    nav_agent.entity_resolver.resolve_by_id.return_value = [project_match("Peter Parker")]
    opt = nav_agent.resolve_project_route_option(
        "project_invoice_view", "tenant-1", entity_id="uid-1",
    )
    assert opt is not None


# ── _project_information_help_text tab list is registry-derived, not a hardcoded copy ──
# Real bug, fixed: _PROJECT_TAB_PERMISSION used to hardcode each tab's
# permission code as a literal string, duplicating route_registry.json's own
# permission_required field on the route that actually owns each tab -- a
# second, unguarded copy that wouldn't follow if a route's real permission
# changed. Now derived from the registry at use time via _PROJECT_TAB_ROUTE_ID.

def test_project_information_help_text_hides_tabs_without_permission(nav_agent, ctx, monkeypatch):
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = {"BOM.REA"}  # only BOM
    text = nav_agent._project_information_help_text(ctx("what can I do here"))
    assert "BOM" in text
    assert "Invoice" not in text
    assert "Job Ledger" not in text
    assert "Tasks" in text  # always listed, no permission_required in the registry


def test_project_information_help_text_shows_all_permitted_tabs(nav_agent, ctx, monkeypatch):
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = {"BOM.REA", "INV.REA", "JBL.REA"}
    text = nav_agent._project_information_help_text(ctx("what can I do here"))
    assert "BOM" in text
    assert "Invoice" in text
    assert "Job Ledger" in text
    assert "PO" not in text  # PO.REA not granted


def test_project_information_help_text_unrestricted_in_dev_bypass(nav_agent, ctx, monkeypatch):
    monkeypatch.setenv("SKIP_RBAC_CHECK", "true")
    text = nav_agent._project_information_help_text(ctx("what can I do here"))
    # None = unrestricted -- every tab should be listed regardless of role_resolver
    for tab in ("BOM", "PO", "Invoice", "Job Ledger", "Documents"):
        assert tab in text


def test_already_on_page(nav_agent, ctx):
    # Real bug, fixed (PR review, Raja): "invoices" used to resolve to the
    # flat /invoice route (invoice_list), since removed entirely -- it now
    # resolves to project_invoice_view (entity_required="project"), whose
    # OWN raw pattern (not its ENTITY_TAB_ALIAS_ROUTE_STATE-aliased
    # destination) is what _is_already_on_page compares currentRoute
    # against.
    response = nav_agent.try_keyword_navigation(
        ctx("show invoices", currentRoute="/soldproduction/projectinformation/invoiceview")
    )
    assert response.type == "guidance"
    # project_invoice_view has an offerable page_feature, so
    # _already_here_response takes its discovery-question branch ("You're
    # on X. Would you like to know about...") rather than the flat
    # "You're already on X" fallback -- see that function's docstring.
    assert response.message.startswith("You're on Project Invoices.")


def test_already_on_entity_page_matches_by_base_pattern(nav_agent, ctx):
    response = nav_agent.try_keyword_navigation(
        ctx("show schedule", currentRoute="/ganttCharts/uid-1")
    )
    assert response.type == "guidance"


def test_entity_page_project_switch_resolves_new_project(nav_agent, ctx, project_match):
    # Naming a DIFFERENT project while on an entity page must resolve it,
    # not stop at "you're already on Gantt Charts".
    nav_agent.entity_resolver.resolve_project.return_value = [
        project_match("Buma Warehouse", "uid-9")
    ]
    response = nav_agent.try_keyword_navigation(
        ctx(
            "show buma schedule",
            currentRoute="/ganttCharts",
            currentEntity={"type": "project", "name": "Henderson Roofing"},
        )
    )
    assert response.type == "navigate"
    assert response.project.id == "uid-9"


def test_entity_resolution_carries_project_in_route_state(nav_agent, ctx, project_match):
    # Entity routes have no :projectId URL segment — the resolved project
    # must travel in routeState (and the project field) for the frontend.
    nav_agent.entity_resolver.resolve_project.return_value = [
        project_match("Henderson Roofing", "uid-1")
    ]
    response = nav_agent.try_keyword_navigation(ctx("henderson schedule"))
    assert response.type == "navigate"
    assert response.routeState["projectId"] == "uid-1"
    assert response.routeState["projectName"] == "Henderson Roofing"


def test_exact_keyword_tier_tolerates_punctuation(nav_agent, ctx):
    # "open invoices!" must be fully explained by the route's vocabulary —
    # a punctuation-glued token must not demote it to the LLM path.
    # Real bug, fixed (PR review, Raja): "/invoice" was invoice_list's raw
    # pattern, since removed entirely -- "invoices" now resolves to
    # project_invoice_view (entity_required="project"), which returns the
    # route object itself here (exact_keyword_route's entity_required
    # branch), not a resolved/aliased path.
    route = nav_agent.exact_keyword_route("open invoices!")
    assert route is not None
    assert route.id == "project_invoice_view"


def test_exact_keyword_tier_yields_to_upload_bill_global_feature(nav_agent, ctx):
    # Real bug, fixed: "bill" is in ENTITY_STOP_WORDS so it never gets
    # mistaken for a project name (billing-question vocabulary), but that
    # list was also being reused, via _word_matches, to decide what counts
    # as inert filler in exact_keyword_route's own vocab check. That let it
    # wave "bill" through as meaningless filler for "bill upload" while
    # document_management's "uploads" keyword explained the other word —
    # so document_management claimed the whole utterance outright, and the
    # global "Upload Bill" Topbar action (the actually-correct target, one
    # tier later in try_feature_lookup) never got a chance to run. "bill" is
    # also real vocabulary for the Upload Bill global feature, so it must
    # NOT count as explained filler here — exact_keyword_route should decline
    # to claim this utterance at all, leaving it for the feature-lookup tier.
    route = nav_agent.exact_keyword_route("bill upload")
    assert route is None

    response = nav_agent.try_feature_lookup(ctx("bill upload"))
    assert response is not None
    assert response.type == "guidance"
    assert response.highlight == "Upload Bill"


def test_data_question_offers_navigation_bubbles(nav_agent, ctx):
    response = nav_agent.data_question_response(ctx("what is the margin on this job"))
    assert response.type == "fallback"
    assert response.options
    assert any(o.route == "/tenantfinance" for o in response.options)


def test_data_question_no_topic_match_has_no_options(nav_agent, ctx):
    response = nav_agent.data_question_response(ctx("what is the meaning of life"))
    assert response.type == "fallback"
    assert not response.options


def test_data_topic_fallback_prioritizes_invoice_over_generic_cost_words(nav_agent, ctx):
    # Real bug, fixed: a plain invoice question ("what is the cost on this
    # invoice") shares generic money words ("cost") with the catch-all
    # job_ledger/tenant_finance row -- without "invoice"/"invoices" as their
    # own trigger words, this could suggest job_ledger instead of the
    # actually-relevant invoice_list page. Only reachable when the LLM path
    # fails (llm_client.suggest_related_routes returns None here).
    # Real bug, fixed (PR review, Raja): invoice_list (a flat, non-entity
    # route) was removed and its bare invoice/billing keywords genuinely
    # transferred to project_invoice_view (entity_required="project") --
    # this table's own row already pointed there, so the fallback picks it
    # up correctly. Building a clickable option for an entity-required
    # route with no project resolved falls back to the generic Projects
    # list (route_option's own existing behavior for exactly this shape,
    # unrelated to this fix) rather than invoice_list's old bare page.
    response = nav_agent.data_question_response(ctx("what is the cost on this invoice"))
    assert response.type == "fallback"
    assert response.options
    assert response.options[0].route == "/customers-projects"


def test_no_match_suggests_closest_route_on_typo(nav_agent, ctx):
    response = nav_agent.no_match(ctx("open invocies"))
    assert response.type == "fallback"
    assert response.suggestion is not None


def test_guidance_describes_current_page(nav_agent, ctx):
    # Real bug, fixed (PR review, Raja): currentRoute="/invoice" was
    # invoice_list's raw pattern, since removed entirely -- no route in the
    # registry matches that pattern anymore, so this silently fell through
    # to describing the root "/" (Home) route instead of an invoice page.
    response = nav_agent.guidance(
        ctx("what is this page", currentRoute="/soldproduction/projectinformation/invoiceview")
    )
    assert response.type == "guidance"
    assert "Invoices" in response.message


def test_guidance_off_registry_route_has_generic_message(nav_agent, ctx):
    response = nav_agent.guidance(ctx("what is this page", currentRoute="/some/unknown/route"))
    assert response.type == "guidance"


def test_guidance_on_project_information_lists_only_permitted_tabs(nav_agent, ctx, monkeypatch):
    # Real bug, fixed: page_help used to be a static string listing every
    # tab (Tasks, BOM, PO, Invoice, Documents) regardless of role -- a
    # customer with no BOM.REA/PO.REA/INV.REA got told about tabs they
    # can't open at all. Now built dynamically from the account's real
    # permissions.
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = {"PRO.REA", "DOC.REA"}
    response = nav_agent.guidance(
        ctx("what can I do here", currentRoute="/soldproduction/projectinformation")
    )
    assert response.type == "guidance"
    assert "Tasks" in response.message
    assert "Documents" in response.message
    assert "BOM" not in response.message
    assert "PO" not in response.message
    assert "Invoice" not in response.message


def test_guidance_on_project_information_admin_sees_all_tabs(nav_agent, ctx, monkeypatch):
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = {
        "PRO.REA", "BOM.REA", "PO.REA", "INV.REA", "DOC.REA",
        "DELSLP.REA", "BIL.REA", "PAY.REA", "JBL.REA", "WIPREP.REA", "VENBIL.REA",
    }
    response = nav_agent.guidance(
        ctx("what can I do here", currentRoute="/soldproduction/projectinformation")
    )
    assert "BOM" in response.message
    assert "PO" in response.message


# ── on-page feature lookup ──────────────────────────────────────────────────

def test_feature_lookup_on_current_page_highlights_it(nav_agent, ctx):
    # Real bug, fixed (PR review, Raja): "Export Invoices button" was a
    # page_feature of invoice_list (a CSV export on that flat AR table),
    # since removed along with the whole route -- no page in the registry
    # has that feature anymore, so this always fell through to a fallback
    # response with nothing to highlight. Switched to a real feature on
    # project_invoice_view (the route that now owns bare invoice/billing
    # navigation), currentRoute set to its actual pattern.
    response = nav_agent.try_feature_lookup(
        ctx("where is the send invoice button", currentRoute="/soldproduction/projectinformation/invoiceview")
    )
    assert response is not None
    assert response.type == "guidance"
    assert response.highlight == "Send Invoice button"


def test_feature_lookup_word_order_independent(nav_agent, ctx):
    # "filter by status" must match the "status filter" keyword despite order.
    response = nav_agent.try_feature_lookup(ctx("how do i filter by status", currentRoute="/vendorlist"))
    assert response is not None
    assert response.highlight == "status filter"


def test_feature_lookup_on_different_page_redirects_with_suggestion(nav_agent, ctx):
    # Real bug, fixed (PR review, Raja): same invoice_list removal as
    # above -- "export invoices"/"Export Invoices button" no longer exist
    # anywhere. Switched to a real feature on project_invoice_view.
    response = nav_agent.try_feature_lookup(ctx("where can i send an invoice", currentRoute="/dashboard"))
    assert response is not None
    assert response.type == "fallback"
    assert response.suggestion.route == "/soldproduction/projectinformation/invoiceview"


def test_feature_lookup_no_match_returns_none(nav_agent, ctx):
    response = nav_agent.try_feature_lookup(ctx("take me to invoices", currentRoute="/dashboard"))
    assert response is None


def test_feature_lookup_ties_across_pages_asks_instead_of_guessing(nav_agent, ctx):
    # Real bug, fixed: confirmed live -- "let me add a customer" matches an
    # identically-scored "add customer" keyword on BOTH Leads & Customers'
    # and Admin > Customers' own "Invite Customer" page feature. The
    # cross-page sweep used to keep only the single best (`score >
    # best_score`, strict), so a genuine tie silently fell to whichever
    # route happened to come first in route_registry.json's list order --
    # landing on Leads & Customers every time by accident of list
    # position, never asking. Both routes' own permissions granted here so
    # neither gets filtered out of the tie.
    nav_agent.role_resolver.get_permissions.return_value = {"VEN.REA"}
    response = nav_agent.try_feature_lookup(ctx("let me add a vendor", currentRoute="/dashboard"))
    assert response is not None
    assert response.type == "disambiguation"
    assert "add new vendor" in response.question.lower()
    assert "works the same" in response.question.lower()
    assert {o.label for o in response.options} == {"Vendors", "Vendor Management"}
    # Real bug, fixed: confirmed live -- picking either option here used to
    # land on a bare page with no spotlight and no auto-click, even though
    # the same utterance would have triggered a highlighted, auto-clicked
    # navigate had only one route matched instead of two.
    for option in response.options:
        assert option.autoClick is True
        assert option.highlightTargets


def test_feature_lookup_action_request_on_different_page_navigates_there(nav_agent, ctx):
    # Real bug, fixed: confirmed live -- "i need to add a new material"
    # (an action request, not a locational question) used to get the same
    # "that's on the Materials page, here's the button" description +
    # suggestion as a genuine "where is X" question, never an actual
    # navigate. Only a "where"/"how" lead-in should keep the
    # describe-and-suggest behavior (see
    # test_feature_lookup_on_different_page_redirects_with_suggestion);
    # everything else that matched a feature's keywords is a request to DO
    # the thing.
    response = nav_agent.try_feature_lookup(
        ctx("i need to add a new material", currentRoute="/dashboard")
    )
    assert response is not None
    assert response.type == "navigate"
    assert response.route == "/materialmart"
    assert response.routeState == {"selectedTab": "materials"}
    # Per explicit user direction: an action request ends with the button
    # already clicked (frontend PageSpotlight), not just circled -- the
    # backend signals this via autoClick.
    assert response.autoClick is True
    assert response.highlight == "Add New Material button"


def test_feature_lookup_where_phrasing_still_only_suggests(nav_agent, ctx):
    # The other side of the same fix: a genuine locational question about
    # the SAME feature must still describe-and-suggest, not force a
    # navigate the user didn't ask for.
    response = nav_agent.try_feature_lookup(
        ctx("where do i add material", currentRoute="/dashboard")
    )
    assert response is not None
    assert response.type == "fallback"
    assert response.suggestion is not None
    assert response.suggestion.route == "/materialmart"
    # A locational question must never auto-click -- only an action
    # request does (see the sibling action-request test above).
    assert response.autoClick is False


# ── "edit/delete bid" navigates to the real project when Gold has it ───────
# EDEN never edits or deletes anything itself -- it only navigates the user
# to where THEY can. "Cart & Drafts" is the right answer for a bid still
# sitting in the cart as a pre-booking draft (no Gold record at all); once
# it's a real, booked project, the right answer is that project's own
# Project Information page, not a generic Cart pointer.

def test_edit_bid_naming_a_gold_project_navigates_there_not_to_cart(nav_agent, ctx, project_match):
    nav_agent.entity_resolver.resolve_project.return_value = [
        project_match("Henderson Roofing", "uid-1"),
    ]
    response = nav_agent.try_feature_lookup(ctx("edit henderson bid", currentRoute="/dashboard"))
    assert response is not None
    assert response.type == "navigate"
    assert response.route == "/soldproduction/projectinformation"
    assert response.project.id == "uid-1"


def test_delete_bid_naming_a_gold_project_navigates_there(nav_agent, ctx, project_match):
    nav_agent.entity_resolver.resolve_project.return_value = [
        project_match("Henderson Roofing", "uid-1"),
    ]
    response = nav_agent.try_feature_lookup(ctx("delete henderson bid", currentRoute="/dashboard"))
    assert response is not None
    assert response.type == "navigate"
    assert response.route == "/soldproduction/projectinformation"


def test_edit_bid_with_no_matching_gold_project_falls_back_to_cart(nav_agent, ctx):
    # Nothing in Gold under this name -- it's still a pre-booking draft,
    # only reachable via the Cart, so the generic Cart & Drafts pointer is
    # the honest (and only correct) answer.
    nav_agent.entity_resolver.resolve_project.return_value = []
    response = nav_agent.try_feature_lookup(ctx("edit xyznonexistent bid", currentRoute="/dashboard"))
    assert response is not None
    assert response.type == "guidance"
    assert response.highlight == "Cart & Drafts"


def test_edit_bid_multiple_gold_matches_disambiguates(nav_agent, ctx, project_match):
    nav_agent.entity_resolver.resolve_project.return_value = [
        project_match("Henderson Roofing", "uid-1"),
        project_match("Henderson Retail", "uid-2"),
    ]
    response = nav_agent.try_feature_lookup(ctx("edit henderson bid", currentRoute="/dashboard"))
    assert response is not None
    assert response.type == "disambiguation"
    assert len(response.options) == 2


def test_feature_sweep_resolves_permissions_once_not_per_route(nav_agent, ctx, monkeypatch):
    """Regression for the dominant latency bug: the cross-page feature sweep
    called _has_permission per route, and each call re-queried
    gold_canonical.user_permissions -- ~0.23s per Cosmos round-trip x ~70
    routes = ~16s of a ~20s request, on EVERY request, since this tier runs
    before the LLM path. Measured live before/after: 16.06s -> 0.24s.

    Asserts the CALL COUNT, not wall-clock, so it stays meaningful in CI (no
    real Mongo) and pins the real invariant: one permission resolution per
    sweep, however many routes the registry grows to."""
    # The autouse rbac_bypassed fixture would skip the lookup entirely.
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver = MagicMock()
    nav_agent.role_resolver.get_permissions.return_value = ["PRO.REA"]
    # Matches no page_feature, so the sweep runs over every route instead of
    # short-circuiting on an early hit.
    nav_agent.try_feature_lookup(ctx("zzzqqq nothing matches this at all"))
    assert nav_agent.role_resolver.get_permissions.call_count == 1, (
        "permissions must be resolved once per sweep, not once per route"
    )


def test_filter_permitted_route_ids_matches_the_singular_check(nav_agent, ctx, monkeypatch):
    """The batch filter exists only to collapse Mongo round-trips -- it must
    agree with has_route_id_permission exactly, never relax the gate."""
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver = MagicMock()
    nav_agent.role_resolver.get_permissions.return_value = ["DASH.REA"]
    c = ctx("anything")
    ids = ["dashboard", "invoice_list", "definitely_not_a_real_route_id"]
    batch = nav_agent.filter_permitted_route_ids(ids, c)
    singular = [i for i in ids if nav_agent.has_route_id_permission(i, c)]
    assert batch == singular
    assert "definitely_not_a_real_route_id" not in batch
    # And it really is one lookup for the whole list, not one per id.
    nav_agent.role_resolver.get_permissions.reset_mock()
    nav_agent.filter_permitted_route_ids(ids, c)
    assert nav_agent.role_resolver.get_permissions.call_count == 1


# ── _ground_insight: LLM-classified navigation must not assert an ungrounded
# capability ─────────────────────────────────────────────────────────────
# Real bug, fixed: confirmed live -- resolve_classified() passed the LLM's
# raw NavigationUnderstanding.insight straight into the user-facing
# navigate message with zero verification against the route's own
# page_help/page_features. Two independent live incidents, both reproduced
# below against the REAL route_registry.json entries (nav_agent fixture),
# not a fabricated stub: "you can deactivate a customer account from the
# Customer Admin page" (admin_customer's real page_features are profile
# photo / billing address / property link / save-update button -- nothing
# about deactivation) and an equivalent claim that leave can be booked from
# Timesheets (whose real page_help/description are strictly about time
# logs/hours/attendance).

def test_llm_navigation_insight_asserting_ungrounded_capability_is_dropped(nav_agent, ctx):
    # AB#28717 superseded this exact scenario: _ground_insight() still
    # exists and still drops fabricated capability claims (see the
    # leave-booking case right below, which exercises that mechanism
    # generically) -- but "deactivate a customer" specifically now short-
    # circuits BEFORE reaching route resolution at all, since there is no
    # real destination for it anywhere in the app (checked the actual
    # frontend: Customer.tsx forces status="Active" on every save, no UI
    # control sets it otherwise). Suppressing the lie while still landing
    # on a page that can't do what was asked (the old assertion here) was
    # only half the fix -- an honest apology, not a redirect, is correct.
    understanding = {
        "intent": "navigate",
        "route_id": "admin_customer",
        "confidence": 0.9,
        "extracted_entity": None,
        "insight": "You can deactivate a customer account from the Customer Admin page.",
    }
    response = nav_agent.resolve_classified(ctx("I want to deactivate customer"), understanding)
    assert response.type == "fallback"
    assert response.route is None
    assert "sorry" in response.message.lower()


def test_llm_navigation_insight_asserting_ungrounded_leave_booking_is_dropped(nav_agent, ctx):
    understanding = {
        "intent": "navigate",
        "route_id": "timesheets",
        "confidence": 0.9,
        "extracted_entity": None,
        "insight": "You can book leave from the Timesheets page.",
    }
    response = nav_agent.resolve_classified(ctx("i need to book leave"), understanding)
    assert response.type == "navigate"
    assert response.route == "/timesheets"
    assert "leave" not in response.message.lower()


def test_llm_navigation_insight_grounded_in_real_page_features_survives(nav_agent, ctx):
    # A claim that IS grounded in admin_customer's real page_features
    # (billing address fields genuinely exist there) must pass through
    # unchanged -- this guard must not reject every capability claim, only
    # ungrounded ones.
    understanding = {
        "intent": "navigate",
        "route_id": "admin_customer",
        "confidence": 0.9,
        "extracted_entity": None,
        "insight": "Here you can update the billing address fields for this customer.",
    }
    response = nav_agent.resolve_classified(ctx("update customer billing address"), understanding)
    assert response.type == "navigate"
    assert "billing address" in response.message.lower()


def test_llm_navigation_insight_with_no_claim_shape_is_never_touched(nav_agent, ctx):
    # A plain descriptive insight (no "you can"/"lets you"/etc. claim shape
    # at all) must pass straight through -- this guard only inspects text
    # that actually asserts a capability.
    understanding = {
        "intent": "navigate",
        "route_id": "admin_customer",
        "confidence": 0.9,
        "extracted_entity": None,
        "insight": "The customer account management page.",
    }
    response = nav_agent.resolve_classified(ctx("customer admin"), understanding)
    assert response.type == "navigate"
    assert "the customer account management page" in response.message.lower()


# ── AB#28704: create_asset has no real permission to gate on, ever redirect
# to Create Bid instead of claiming a permission denial that was never true ──

def test_create_asset_redirects_to_create_bid_with_permission(nav_agent, ctx, monkeypatch):
    # Confirmed against real tenant RBAC data: AST.REA (the old gate) does
    # not exist for any role, and nothing else maps to "register company
    # equipment" either -- so create_asset itself is never denied. But the
    # real destination, create_bid, DOES have a real permission (PRO.CRE);
    # a user who holds it must still be redirected there, not denied.
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = {"PRO.CRE"}
    response = nav_agent.try_keyword_navigation(ctx("create asset"))
    assert response.type == "navigate"
    assert response.route == "/createbid"
    assert "don't have access" not in response.message.lower()
    assert "create bid" in response.message.lower()


def test_create_asset_redirects_to_create_bid_ignoring_defunct_permissions(nav_agent, ctx, monkeypatch):
    # The redirect itself isn't a per-user decision about CREATE ASSET --
    # it fires regardless of the old, nonexistent AST.REA/ASS.CRE codes,
    # because the standalone /asset page was never how this is done in the
    # real app. PRO.CRE (create_bid's own, real permission) still gates
    # whether the redirect's DESTINATION is reachable -- see the sibling
    # test above and the denial test below.
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = {"AST.REA", "ASS.CRE", "PRO.CRE"}
    response = nav_agent.try_keyword_navigation(ctx("register equipment"))
    assert response.type == "navigate"
    assert response.route == "/createbid"


def test_create_asset_denies_when_create_bid_not_permitted(nav_agent, ctx, monkeypatch):
    # Real gap, found live (2026-09-15): this redirect used to build the
    # create_bid navigate UNCONDITIONALLY, never checking PRO.CRE -- an
    # "upstream builder skipped its permission check", confirmed live via
    # eden-service-dev's logs: a labour-role user with no PRO.CRE asked
    # about creating an asset, got redirected straight to /createbid
    # anyway, and only the ORCHESTRATOR's separate, later-stage role-scope
    # guardrail caught it -- replacing this route's own accurate message
    # with a generic "That page isn't available with your current access"
    # deflection that no longer names Create Bid at all. Checking
    # create_bid's own permission HERE means this exact function returns
    # the same accurate, specific denial asking for Create Bid directly
    # would give -- not a vague catch-all from a downstream net.
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = set()
    response = nav_agent.try_keyword_navigation(ctx("create asset"))
    assert response.type == "fallback"
    assert "create bid" in response.message.lower()
    assert response.route is None


# ── AB#28708 RETRACTED (2026-09-15): "book leave" must NOT reach
# resource_management at all -- see test_book_leave_is_not_a_resource_
# management_feature and test_book_leave_resolves_only_to_timesheets_
# regardless_of_resource_permission near the top of this file for the
# corrected behavior. The dialog AB#28708 pointed at was confirmed dead
# code in the real frontend (ResourceList.tsx never calls
# setDatePickerOpen(true) anywhere) -- resource_management's "book leave"
# registry entries were never a real, reachable feature. ─────────────────

def test_book_leave_does_not_navigate_to_resource_management(nav_agent, ctx, monkeypatch):
    monkeypatch.setenv("SKIP_RBAC_CHECK", "false")
    nav_agent.role_resolver.get_permissions.return_value = {"RES.REA"}
    response = nav_agent.try_keyword_navigation(ctx("book leave"))
    assert response is None or response.route != "/admin"


# ── AB#28717: "deactivate a customer" has no real destination anywhere in
# the app -- checked the actual frontend (Customer.tsx forces status=
# "Active" on every save, customer_table's row actions are only View
# Asset/Edit customer) -- must apologize honestly, never navigate ─────────

def test_deactivate_customer_apologizes_instead_of_navigating(nav_agent, ctx):
    understanding = {
        "intent": "navigate",
        "route_id": "admin_customer",
        "confidence": 0.9,
        "extracted_entity": None,
        "insight": "You can deactivate a customer account from the Customer Admin page.",
    }
    response = nav_agent.resolve_classified(ctx("how do I deactivate a customer"), understanding)
    assert response.type == "fallback"
    assert response.route is None
    assert "sorry" in response.message.lower()
    assert "not able" in response.message.lower()


def test_deactivate_customer_apologizes_regardless_of_llm_route_choice(nav_agent, ctx):
    # Same ask, different LLM-classified route_id -- the apology must fire
    # off the utterance itself, not off which route the LLM happened to pick.
    understanding = {
        "intent": "navigate",
        "route_id": "customer_table",
        "confidence": 0.8,
        "extracted_entity": None,
        "insight": None,
    }
    response = nav_agent.resolve_classified(ctx("deactivate this customer"), understanding)
    assert response.type == "fallback"
    assert "sorry" in response.message.lower()


# --- QA Issue AB#30013 (corrected): "sell price" is a Materials/Inventory
# concept, not Create Estimate ------------------------------------------
# Real bug, fixed: confirmed live -- "where can i add sell price?" first
# got misdiagnosed as needing project context on /create-estimate (that
# route's own entity_required gap is real and separately fixed below, but
# it isn't the correct destination for THIS question). Per direct
# confirmation against the real app: Selling Price is a toggleable column
# on Admin Management > Materials (MaterialList.tsx's "Material Fields"
# dropdown, hidden by default -- defaultVisibleColumns doesn't include
# it), and separately an editable field on Inventory's per-row Edit
# action (EditMaterialDialog_v2.tsx). Neither is project-scoped. Removed
# the wrong "sell price" keywords from create_estimate and added them to
# material_management's own "Material Fields dropdown" page_feature
# instead (plus enhanced inventory's existing "Edit (row icon)" feature
# for the editing half of the same concept).

# Real bug, fixed: confirmed live -- "show me the material selling price"
# navigated to Materials with NO highlight and NO dropdown opened ("it just
# navigated"). The earlier version of this fix put "sell price"/"selling
# price" in material_management's ROUTE-LEVEL intent_keywords, which let
# exact_keyword_route -- the orchestrator's FIRST dispatch tier (see
# agent_orchestrator.py's elif chain) -- fully explain the sentence from the
# route's own vocabulary and claim it, returning a bare navigate. The tier
# that actually produces the highlight/autoClick (try_feature_lookup) never
# ran. Same bug class as the inventory/"Stock Bills" one below; both are
# fixed the same way -- feature-specific phrasing belongs ONLY on the
# page_feature, never duplicated up onto the route.
#
# These tests assert through the REAL dispatch order for that reason. The
# previous versions called try_keyword_navigation (a cruder tier the
# orchestrator doesn't reach first) and so passed happily while the live
# behavior was broken -- exactly the blind spot that let this ship twice.

def test_sell_price_question_is_not_claimed_by_bare_route_navigation(nav_agent, ctx):
    assert nav_agent.exact_keyword_route(
        "show me the material selling price", ctx("show me the material selling price")
    ) is None


def test_sell_price_question_navigates_to_materials_with_dropdown_opened(nav_agent, ctx):
    # AB#32825: the catalogue moved from Admin's Materials tab to Material
    # Mart's Materials sub-tab, so material_management's target_route is
    # /materialmart (+selectedTab "materials") -- not a bare
    # /materialmanagement page, and no longer /admin tab 5.
    response = nav_agent.try_feature_lookup(ctx("show me the material selling price"))
    assert response is not None
    assert response.route == "/materialmart"
    assert response.routeState == {"selectedTab": "materials"}
    assert response.route != "/create-estimate"
    assert "material fields" in (response.highlight or "").lower()
    assert response.autoClick is True
    assert response.postClickHighlightTargets == ["Selling Price"]


def test_where_phrasing_still_opens_the_dropdown_it_asks_about(nav_agent, ctx):
    # The one exception to the "never click a where-question" rule: Selling
    # Price is a hidden-by-default column that doesn't exist in the DOM until
    # the dropdown is opened, so ringing the closed dropdown answers nothing.
    response = nav_agent.try_feature_lookup(ctx("where can i add sell price"))
    assert response is not None
    assert response.autoClick is True
    assert response.postClickHighlightTargets == ["Selling Price"]


def test_sell_price_feature_lookup_highlights_material_fields_dropdown(nav_agent, ctx):
    response = nav_agent.try_feature_lookup(ctx("show selling price"))
    assert response is not None
    assert "material fields" in (response.highlight or "").lower()
    # Real bug, fixed: opening the dropdown isn't enough on its own --
    # "Selling Price" is one of many checkboxes inside it, invisible until
    # the click happens, so it must ride as a SECOND (post-click) target
    # rather than the primary highlightTargets (which point at the
    # closed dropdown control itself).
    assert response.autoClick is True
    assert response.postClickHighlightTargets == ["Selling Price"]


def test_material_fields_dropdown_post_click_target_follows_what_was_asked(
    nav_agent, ctx
):
    # Same dropdown, different column -- the post-click ring must follow
    # whatever the user actually asked about, not always land on Selling
    # Price regardless of phrasing.
    response = nav_agent.try_feature_lookup(ctx("turn on vendor name column"))
    assert response is not None
    assert "material fields" in (response.highlight or "").lower()
    assert response.postClickHighlightTargets == ["Vendor Name"]


# create_estimate's OWN entity_required gap is real and separate from the
# "sell price" misrouting above -- GradeSelectionPage.tsx does have an
# explicit `if (!projectId) setError('No project selected...')` guard, so
# any route_id="create_estimate" navigation genuinely needs a project.

def test_create_estimate_with_named_project_attaches_it(nav_agent, ctx, project_match):
    nav_agent.entity_resolver.resolve_project.return_value = [
        project_match("Henderson Roofing", "uid-1")
    ]
    understanding = {"intent": "navigate", "route_id": "create_estimate", "confidence": 0.9,
                      "extracted_entity": "henderson"}
    response = nav_agent.resolve_classified(ctx("start an estimate for henderson"), understanding)
    assert response.type == "navigate"
    assert response.route == "/create-estimate"
    assert response.routeState["projectId"] == "uid-1"


def test_create_estimate_with_no_entity_name_asks_which_project(nav_agent, ctx):
    response = nav_agent.try_keyword_navigation(ctx("start an estimate"))
    assert response is not None
    assert response.type == "fallback"
    assert response.route is None


# --- QA Issue AB#30005: "set up trade"/"add new trade" landed on the
# wrong routes ------------------------------------------------------------
# Real bug, fixed: confirmed live -- both phrasings matched nothing in
# question_builder's own intent_keywords, so retrieval fell back to
# unrelated routes (admin_home's generic description; create_bid's
# unrelated "Trade Selection" wizard STEP, a pure word collision with
# wanting to create a new trade TYPE). The real "Trade SetUp" button
# (confirmed in TradeQuestions.tsx) lives on question_builder and was
# never represented in the registry at all. Added keywords + a dedicated
# page_feature so both phrasings resolve there directly.

# Asserted through the real dispatch order (exact_keyword_route first, then
# try_feature_lookup) -- these phrasings carry a specific on-page answer, so
# being claimed as a BARE route navigation is the failure mode, not the goal.
# See the sell-price block above for why that distinction matters.

def test_trade_setup_question_is_not_claimed_by_bare_route_navigation(nav_agent, ctx):
    assert nav_agent.exact_keyword_route(
        "where can i set up trade", ctx("where can i set up trade")
    ) is None


def test_trade_setup_question_points_at_the_trade_setup_button(nav_agent, ctx):
    # Trade management lives under Settings > Trade Configuration
    # (/tenantconfiguration), NOT /questionBuilder -- see the route-correction
    # note on test_trade_create_update_delete_all_resolve_to_trade_setup.
    response = nav_agent.try_feature_lookup(ctx("set up trade"))
    assert response is not None
    assert response.route == "/tenantconfiguration"
    assert response.highlight == "Trade Configuration tab"
    assert response.postClickHighlightTargets == ["Trade SetUp"]


def test_add_new_trade_question_matches_question_builder_not_create_bid(nav_agent, ctx):
    # "how" phrasing now opens the Trade SetUp dialog rather than only
    # pointing at the button: the lists a user actually needs ("List of
    # Trades" / "Selected Trades") do not exist in the DOM until it opens,
    # the same container-must-be-opened case as Selling Price inside the
    # Material Fields dropdown.
    response = nav_agent.try_feature_lookup(ctx("how can i add new trade"))
    assert response is not None
    assert response.route == "/tenantconfiguration"
    assert response.route != "/createbid"
    assert response.highlight == "Trade Configuration tab"
    assert response.postClickHighlightTargets == ["Trade SetUp"]


# Real bug, fixed: confirmed live -- "list the bills in inventory" DID land
# on /inventory (the route-level keyword match), but the sub-tab stayed on
# Stock overview instead of switching to Stock Bills. Root cause was one
# tier earlier than the highlight/autoClick logic itself: exact_keyword_route
# (the orchestrator's FIRST dispatch tier -- see agent_orchestrator.py's
# elif chain) fully "explained" the utterance using inventory's own bare
# vocab ("inventory") plus "bill"/"bills" waved through as ENTITY_STOP_WORDS
# filler (that list exists to stop billing words from being misread as a
# PROJECT NAME, not to certify them meaningless here -- the exact same
# collision class already fixed for global_features, just never extended to
# a route's OWN page_features). That claimed the utterance and returned a
# bare, unhighlighted /inventory navigate BEFORE try_feature_lookup (the
# next tier, where the specific "Stock Bills tab" page_feature and its
# autoClick live) ever got a chance to run.
def test_exact_keyword_route_defers_bills_phrasing_to_feature_lookup(nav_agent, ctx):
    route = nav_agent.exact_keyword_route(
        "list the bills in inventory", ctx("list the bills in inventory")
    )
    assert route is None


def test_inventory_bills_question_from_another_page_autoclicks_stock_bills_tab(
    nav_agent, ctx
):
    response = nav_agent.try_feature_lookup(
        ctx("list the bills in inventory", currentRoute="/dashboard")
    )
    assert response is not None
    assert response.route == "/materialmart"
    assert "stock bills" in (response.highlight or "").lower()
    assert response.autoClick is True


# --- QA Issue AB#30010: "inventory bills" answered with the deprecated,
# project-scoped Vendor/Subcontractor Bill tab -----------------------------
# Real bug, fixed: confirmed live -- the response was a near-verbatim quote
# of project_vendor_bill's page-feature hint, a route about a SINGLE
# project's vendor bills, unrelated to tenant-wide inventory billing. The
# real destination (InventoryTabs_v2.tsx's "Stock Bills" sub-tab) was never
# represented in the registry at all -- added as a page_feature on the
# existing inventory route (no new route needed).

def test_inventory_bills_question_matches_stock_bills_not_vendor_bill(nav_agent, ctx):
    # The reported answer was a near-verbatim quote of project_vendor_bill's
    # page-feature hint -- try_feature_lookup (not try_keyword_navigation,
    # which already resolved /inventory correctly via the pre-existing
    # "inventory" keyword) is the mechanism that picks WHICH feature/hint
    # gets quoted, so that's what must be exercised here.
    response = nav_agent.try_feature_lookup(ctx("list all bills in inventory"))
    assert response is not None
    assert "stock bills" in (response.highlight or "").lower()
    assert "vendor" not in response.message.lower()
    assert "subcontractor" not in response.message.lower()


def test_inventory_bills_question_autoclicks_when_already_on_inventory_page(
    nav_agent, ctx
):
    # Real bug, fixed: confirmed live -- asking this same question while
    # ALREADY on /inventory hit try_feature_lookup_scored's current-page
    # branch, not the cross-page sweep the test above exercises. That
    # branch used to never set autoClick regardless of phrasing, so the
    # frontend's PageSpotlight only rang the "Stock Bills" tab instead of
    # actually clicking it -- Inventory's sub-tab (Stock overview/Stock
    # POs/Stock Bills/Stock Delivery Slips) is client-side React state, not
    # a route, so ringing it with no click left the user on whatever
    # sub-tab was already showing. "list"/"show" phrasing is an action
    # request, not a "where is X" lookup, so this must autoClick.
    response = nav_agent.try_feature_lookup(
        ctx("show me the stock bills", currentRoute="/materialmart")
    )
    assert response is not None
    assert "stock bills" in (response.highlight or "").lower()
    assert response.autoClick is True


def test_inventory_bills_where_question_on_inventory_page_does_not_autoclick(
    nav_agent, ctx
):
    # Same current-page branch, but "where" phrasing is a locational
    # question, not an action -- must stay click-free like every other
    # informational lookup.
    response = nav_agent.try_feature_lookup(
        ctx("where are the stock bills", currentRoute="/materialmart")
    )
    assert response is not None
    assert "stock bills" in (response.highlight or "").lower()
    assert response.autoClick is False


# The Trade and Grade filters are the same shape as Material Fields (a MUI
# Select whose options only exist once opened), so they get the same
# treatment: open the dropdown, then ring the option actually asked about.
# They were missed the first time round because their options render as a
# bare <MenuItem>{value}</MenuItem> -- no ListItemText/Typography wrapper --
# which the frontend's element search couldn't see at all.

def test_trade_filter_opens_and_rings_the_named_trade(nav_agent, ctx):
    response = nav_agent.try_feature_lookup(
        ctx("filter by roof enhancement trade", currentRoute="/materialmart")
    )
    assert response is not None
    assert response.highlight == "Trade filter"
    assert response.autoClick is True
    assert response.postClickHighlightTargets == ["Roof Enhancement"]


def test_grade_filter_opens_and_rings_the_named_grade(nav_agent, ctx):
    response = nav_agent.try_feature_lookup(
        ctx("show me grade a", currentRoute="/materialmart")
    )
    assert response is not None
    assert response.highlight == "Grade filter"
    assert response.autoClick is True
    assert response.postClickHighlightTargets == ["a"]


def test_materials_trade_filter_only_claims_material_scoped_phrasing(nav_agent, ctx):
    # Per explicit user direction: "material trade" belongs to the Materials
    # Trade filter, but a bare mention of "trade" must NOT be dragged there.
    # An earlier pass added generic keywords ("the trade", "show trade",
    # "view trade") to widen matching, and they hijacked every trade question
    # in the app -- including AB#30005's own repro step. Trade is a word that
    # appears across Question Builder, Create Bid, Tenant Configuration and
    # Tools; only material-scoped phrasing may claim the Materials filter.
    material_scoped = nav_agent.try_feature_lookup(ctx("material trade"))
    assert material_scoped is not None
    assert material_scoped.highlight == "Trade filter"

    for generic in ["show me the trade", "the trade", "view trade", "select the trade"]:
        response = nav_agent.try_feature_lookup(ctx(generic))
        highlight = response.highlight if response else None
        assert highlight != "Trade filter", generic
        assert highlight != "Grade filter", generic


def test_dropdown_filters_open_even_without_a_named_option(nav_agent, ctx):
    response = nav_agent.try_feature_lookup(
        ctx("filter by grade", currentRoute="/materialmart")
    )
    assert response is not None
    assert response.autoClick is True
    assert response.postClickHighlightTargets is None


def test_material_scoped_trade_and_grade_phrasing_opens_the_filters(nav_agent, ctx):
    # The Materials filters answer to material-scoped phrasing, and open the
    # dropdown when they do. Generic "trade"/"grade" deliberately does NOT
    # land here -- see test_materials_trade_filter_only_claims_material_scoped_phrasing.
    for utterance, expected in [
        ("material trade", "Trade filter"),
        ("material grade", "Grade filter"),
        ("filter materials by trade", "Trade filter"),
    ]:
        response = nav_agent.try_feature_lookup(ctx(utterance, currentRoute="/admin"))
        assert response is not None, utterance
        assert response.highlight == expected, utterance
        assert response.autoClick is True, utterance


def test_trade_filter_keywords_do_not_steal_trade_setup(nav_agent, ctx):
    # The Materials Trade filter shares the word "trade" with
    # question_builder's Trade SetUp button; adding natural phrasings to the
    # filter must not capture requests to CREATE a trade.
    for utterance in ["set up trade", "add new trade", "add a trade"]:
        response = nav_agent.try_feature_lookup(ctx(utterance, currentRoute="/materialmart"))
        assert response is not None, utterance
        assert response.highlight == "Trade Configuration tab", utterance


# Real bug, fixed: confirmed live (nav_audit utterance='shoe me the purchase
# order' resolved_route=None status=fallback). A typo for "show" left "shoe"
# unexplained, so purchase_orders -- the tenant-wide list, and the right
# answer -- rejected itself, and exact_keyword_route's "an unexplained
# leftover IS a project name" rule handed the turn to the project-scoped
# po_details, which had no project to resolve and dead-ended. The LLM/RAG
# path never ran, because this deterministic tier answered first.

def test_typo_for_a_filler_word_defers_to_the_llm_not_a_project_route(nav_agent, ctx):
    for utterance in [
        "shoe me the purchase order",
        "sho me the purchase order",
        "shwo me the purchase order",
    ]:
        route = nav_agent.exact_keyword_route(utterance, ctx(utterance))
        # None = fall through to try_feature_lookup then the LLM/RAG path,
        # which reads typos semantically. Landing on ANY project-scoped
        # route here is the dead-end this guards against.
        assert route is None or not route.entity_required, utterance


def test_correctly_spelled_purchase_order_still_routes_deterministically(nav_agent, ctx):
    route = nav_agent.exact_keyword_route(
        "show me the purchase order", ctx("show me the purchase order")
    )
    assert route is not None
    assert route.id == "purchase_orders"
    assert route.entity_required is None


def test_a_real_name_is_not_mistaken_for_a_typo(nav_agent, ctx):
    # The typo check must not swallow genuine entity names -- that's what
    # keeps the named-project handoff working.
    vocab = {"material", "materials", "list", "management"}
    for name in ["peter", "parker", "buma", "henderson", "riverside"]:
        assert nav_agent._is_probable_typo_of_filler(name, vocab) is False, name


def test_two_letter_words_are_not_stemmed_into_single_letters(nav_agent, ctx):
    # Real bug, fixed: _word_matches stripped a trailing "s" with no length
    # floor, so "is" -> "i" and "as" -> "a". Those then matched the PRONOUN
    # "i" and the ARTICLE "a" as standalone tokens, meaning any keyword
    # phrase containing "is" matched almost any sentence with "i" in it.
    # Live effect: the Materials "where is trade" keyword matched "where can
    # I set up trade" and out-scored question_builder's "set up trade"
    # (314 vs 312), re-breaking AB#30005's own repro step.
    assert nav_agent._word_matches("is", ["can", "i", "do", "this"]) is False
    assert nav_agent._word_matches("as", ["add", "a", "trade"]) is False
    # Real plurals must still stem normally.
    assert nav_agent._word_matches("bills", ["bill", "list"]) is True
    assert nav_agent._word_matches("po", ["pos"]) is True


def test_ab30005_repro_steps_resolve_to_trade_setup(nav_agent, ctx):
    # The two literal queries from AB#30005's Steps to Reproduce.
    for utterance in ["Where can I set up trade?", "How can I add new trade?"]:
        response = nav_agent.try_feature_lookup(ctx(utterance))
        assert response is not None, utterance
        assert response.highlight == "Trade Configuration tab", utterance


# AB#30005 Expected Result: EDEN must give the correct path for CREATING,
# UPDATING and DELETING trades. Verified against the live frontend: all three
# happen in the one Trade SetUp dialog (TradeSetUp.tsx), whose transfer list
# moves trades between "List of Trades" and "Selected Trades" -- handleAdd-
# TenantTrade reports "Trade Added Successfully" / "Trade Removed Successfully"
# from that same Save. There is no separate edit-a-trade screen: TradeForm.tsx
# exists but is never imported, and deleteTrade() has no UI caller.

def test_trade_create_update_delete_all_resolve_to_trade_setup(nav_agent, ctx):
    for utterance in [
        "where can i set up trade",
        "how can i add new trade",
        "how can i delete a trade",
        "remove a trade",
        "how to update a trade",
        "edit trade",
        "manage trades",
        "trade management",
    ]:
        response = nav_agent.try_feature_lookup(ctx(utterance))
        assert response is not None, utterance
        assert response.highlight == "Trade Configuration tab", utterance
        assert response.autoClick is True, utterance


def test_trade_post_click_points_at_the_trade_setup_button(nav_agent, ctx):
    # Settings opens on whichever tab localStorage last held, so the tab has
    # to be clicked before the Trade SetUp button exists in the DOM -- hence
    # tab first, button as the post-click target. The two transfer lists
    # ("List of Trades" / "Selected Trades") live one level deeper again, and
    # are described in the hint rather than rung.
    for utterance in ["how can i add new trade", "how can i delete a trade"]:
        response = nav_agent.try_feature_lookup(ctx(utterance))
        assert response.postClickHighlightTargets == ["Trade SetUp"], utterance


def test_weak_global_feature_does_not_beat_a_stronger_page_feature(nav_agent, ctx):
    # Real bug, fixed: the global branch returned before the cross-page sweep
    # ran, so the Notifications bell matching the single word "updates"
    # (score 107) beat question_builder's "Trade SetUp button" (score 314) for
    # "how to update a trade" -- the exact "suggests unrelated workflows"
    # failure AB#30005 reports. Global still wins a tie, never a weaker match.
    response = nav_agent.try_feature_lookup(ctx("how to update a trade"))
    assert response is not None
    assert response.highlight == "Trade Configuration tab"
    # ...but a genuine global request still reaches the global feature.
    still_global = nav_agent.try_feature_lookup(ctx("any updates"))
    assert still_global is not None
    assert still_global.highlight == "Notifications"


# Real bug, fixed: confirmed against the frontend -- trade management was
# pointed at /questionBuilder, which is wrong. Routes.tsx maps
# /questionBuilder to questionBuilder/components/Questions, which has no
# Trade SetUp button at all. TradeQuestions.tsx (the file that HAS it) is
# imported only by Tenantconfiguration.tsx, where it renders as the "Trade
# Configuration" tab. The real path is the profile menu's Settings entry ->
# /tenantconfiguration -> Trade Configuration tab -> Trade SetUp button.

def test_trade_management_never_routes_to_question_builder(nav_agent, ctx):
    for utterance in [
        "where can i set up trade",
        "how can i add new trade",
        "how can i delete a trade",
        "manage trades",
        "trade management",
    ]:
        response = nav_agent.try_feature_lookup(ctx(utterance))
        assert response is not None, utterance
        assert response.route == "/tenantconfiguration", utterance
        assert response.route != "/questionBuilder", utterance


def test_question_builder_keeps_its_own_real_features(nav_agent, ctx):
    # /questionBuilder genuinely does own these (Questions.tsx renders a
    # "Trade Questions" tab and a "Select Your Trade" dropdown) -- moving
    # trade MANAGEMENT away must not strip them.
    for utterance, expected in [
        ("trade questions", "Trade Questions tab"),
        ("select your trade", "Select Your Trade dropdown"),
        ("add question template", "Add Question Template button"),
    ]:
        response = nav_agent.try_feature_lookup(ctx(utterance))
        assert response is not None, utterance
        assert response.route == "/questionBuilder", utterance
        assert response.highlight == expected, utterance


def test_settings_lists_the_real_configuration_tabs(nav_agent, ctx):
    response = nav_agent.try_feature_lookup(ctx("what configurations we have"))
    assert response is not None
    assert response.route == "/tenantconfiguration"
    hint = (response.insight or "").lower()
    for tab in ["general configuration", "trade configuration", "finance settings",
                "mail configuration", "time & payroll", "clock-in settings"]:
        assert tab in hint, tab


def test_general_configuration_lands_in_the_settings_shell(nav_agent, ctx):
    # Real bug, fixed: confirmed against the frontend -- /Generalconfiguration
    # IS declared in Routes.tsx, so it renders rather than 404s, but nothing in
    # the app ever navigates there. Generalconfiguration.tsx is imported by
    # exactly one other place: Tenantconfiguration.tsx, where it is tab 0. The
    # bare URL renders the form OUTSIDE the Settings tab shell, so the user
    # lands with no tab bar and no way to reach the other configurations.
    route = nav_agent.route_by_id("general_configuration")
    assert route.resolved_route() == "/tenantconfiguration"

    for utterance in ["where are the working days checkboxes", "company address"]:
        response = nav_agent.try_feature_lookup(ctx(utterance))
        assert response is not None, utterance
        target = response.route or (
            response.suggestion.route if response.suggestion else None
        )
        assert target == "/tenantconfiguration", utterance


# Real bug, fixed: confirmed live -- "get me the clock in settings" navigated
# to /tenantconfiguration but left the user on the Asset tab. Settings restores
# whichever tab localStorage last held (TAB_STORAGE_KEY) and ignores router
# state entirely, so a bare route navigate lands on an arbitrary tab. Five of
# the nine tabs had no page_feature at all, so nothing could click them.

def test_every_settings_tab_clicks_its_own_tab(nav_agent, ctx):
    tabs = [
        ("general configuration", "General Configuration tab"),
        ("asset questions", "Asset tab"),
        ("trade configuration", "Trade Configuration tab"),
        ("subcontractor configuration", "Subcontractor Configuration tab"),
        ("finance settings", "Finance Settings tab"),
        ("mail configuration", "Mail Configuration tab"),
        ("process workflow metrics", "Process Workflow Metrics tab"),
        ("time and payroll", "Time & Payroll tab"),
        ("get me the clock in settings", "Clock-In Settings tab"),
    ]
    for utterance, expected in tabs:
        response = nav_agent.try_feature_lookup(ctx(utterance))
        assert response is not None, utterance
        assert response.highlight == expected, utterance
        # Must CLICK the tab, not merely ring it -- otherwise the user is left
        # on whatever tab localStorage happened to hold.
        assert response.autoClick is True, utterance
        target = response.route or (
            response.suggestion.route if response.suggestion else None
        )
        assert target == "/tenantconfiguration", utterance


# Real bug, fixed: confirmed live -- "show me the delivery slips" and "stock
# delivery slips" claimed delivery_slip (a single project's own document,
# entity_required="project") outright on its bare "delivery slip" keyword,
# asking "which project did you mean?" even though the request was plainly
# about Inventory's tenant-wide Stock Delivery Slips sub-tab. Same
# structural class as the earlier purchase_orders/po_details collision:
# exact_keyword_route's entity_required bypass claimed the utterance with
# zero comparison against a more specific page_feature match elsewhere.

def test_stock_delivery_slips_does_not_ask_which_project(nav_agent, ctx):
    for utterance in [
        "show me the delivery slips",
        "stock delivery slips",
        "delivery slips for inventory",
    ]:
        route = nav_agent.exact_keyword_route(utterance, ctx(utterance))
        assert route is None or route.id != "delivery_slip", utterance
        response = nav_agent.try_feature_lookup(ctx(utterance))
        assert response is not None, utterance
        assert response.highlight == "Stock Delivery Slips tab", utterance
        assert response.route == "/materialmart", utterance
        assert response.autoClick is True, utterance


def test_named_project_delivery_slip_still_asks_which_project_when_ambiguous(nav_agent, ctx):
    # The fix must not break delivery_slip's own legitimate fuzzy claim --
    # a request that actually names something project-shaped still resolves
    # there deterministically, same as before.
    route = nav_agent.exact_keyword_route(
        "delivery slip for henderson", ctx("delivery slip for henderson")
    )
    assert route is not None
    assert route.id == "delivery_slip"


def test_automation_plans_requires_the_real_permission_not_config_manage(nav_agent, ctx):
    # Real bug, fixed: confirmed live -- "show me the automation plans" said
    # "you don't have access" for a user without CON.MAN, but Automation
    # Plans is gated by automation.READ ("AUT.REA") in the actual frontend
    # (Topbar.tsx's automation-plans tab: isUserHasPermission(automation.READ),
    # PagePermission.tsx), not tenant configuration management. Per the
    # frontend's own seed-migration comment, the 'sales' role gets READ +
    # ENROLL (AUT.REA/AUT.FOR) but never CON.MAN -- the wrong requirement
    # would have denied every sales user, always.
    nav_agent.role_resolver.get_permissions.return_value = ["AUT.REA", "AUT.FOR"]
    response = nav_agent.try_keyword_navigation(ctx("show me the automation plans"))
    assert response is not None
    assert response.type == "navigate"
    assert response.route == "/automationplans"


def test_no_post_click_feature_is_preempted_by_its_own_route_keywords(nav_agent, ctx):
    # Real bug, fixed THREE times this session (material_management's
    # "Material Fields dropdown", tenant_configuration's "Trade Configuration
    # tab", inventory's "Stock Bills tab") and documented in CLAUDE.md:
    # duplicating a page_feature's own keyword phrase onto its route's
    # top-level intent_keywords lets exact_keyword_route -- the FIRST
    # dispatch tier -- fully explain the utterance and return a bare
    # navigate, before try_feature_lookup (which owns the highlight and the
    # post-click reveal) ever runs. Automated review (PR !8420) suggested a
    # broad "does this exact phrase appear in both places" check; dry-run
    # against the live registry found 25 false positives across
    # long-standing, harmless entries (e.g. sales_estimate's "new bid" also
    # naming its own "Create Bid button") where the duplicated phrase
    # already lands on the fully correct, fully visible destination with
    # nothing hidden behind a click -- landing via the bare route is
    # genuinely fine there. The real harm is specific to features whose
    # answer requires a FURTHER client-side reveal after arriving
    # (autoClick opening a dropdown/tab, `post_click_locate`/
    # `post_click_options` pointing at something not yet in the DOM) --
    # skipping straight to a bare navigate for THOSE loses real information
    # the user asked for. Scoped to exactly that set: for every
    # post-click-bearing feature's own keyword phrase, used alone as the
    # utterance, the phrase must not be fully claimed by its own route
    # before try_feature_lookup gets a chance to run.
    violations = []
    for route in nav_agent.route_registry:
        for feature in route.page_features:
            if not (feature.post_click_locate or feature.post_click_options):
                continue
            for kw in feature.keywords:
                claimed = nav_agent.exact_keyword_route(kw, ctx(kw))
                if claimed is not None and claimed.id == route.id:
                    violations.append(
                        f"{route.id}: keyword {kw!r} (feature {feature.name!r}, "
                        f"which needs a post-click reveal) is fully claimed by "
                        f"the route's own intent_keywords before try_feature_lookup "
                        f"ever runs -- move this phrase off the route's top-level "
                        f"intent_keywords, it belongs on the page_feature only."
                    )
    assert not violations, "\n".join(violations)


# --- Sales/CRM tab consolidation (#32710) ---------------------------------
# tenant-web's ENABLE_SALES_CRM_CONSOLIDATION hides Sales/Estimate,
# Sold/Production, Leads & Customers and Pipeline in favour of Customers &
# Projects and Performance (and redirects /salesestimate + /soldproduction to
# /customers-projects); Document Management's tab opens /documents once
# documentsExplorer_v2_enable is on. EDEN must never send anyone to the hidden
# pages, and the old wording must reach the visible tab.
CRM_PERMS = {"CUS.REA", "PRO.REA", "TARREP.REA", "CART.REA", "CON.MAN", "AUT.REA"}


def test_consolidated_crm_tabs_are_never_a_navigation_target(nav_agent):
    retired = {"/salesestimate", "/soldproduction", "/leadscustomers", "/pipeline",
               "/documentmanagement"}
    assert not [r.id for r in nav_agent.route_registry if r.resolved_route() in retired]


def test_consolidated_crm_old_wording_reaches_the_visible_tab(nav_agent, ctx):
    nav_agent.role_resolver.get_permissions.return_value = CRM_PERMS
    cases = {
        "show my projects": "/customers-projects",
        "go to sales estimate": "/customers-projects",
        "open the pipeline": "/performance",
        "show the sales pipeline": "/performance",
        "let me create a bid": "/createbid",
        "go to document management": "/documents",
    }
    for utterance, expected in cases.items():
        route = nav_agent.exact_keyword_route(utterance, ctx(utterance))
        assert route is not None, utterance
        assert route.resolved_route() == expected, utterance


def test_consolidated_crm_features_highlight_the_specific_control(nav_agent, ctx):
    # One page_feature per stage tab: the frontend spotlight highlights the
    # FIRST locate label it finds, so a single all-tabs feature always lit "Lead".
    nav_agent.role_resolver.get_permissions.return_value = CRM_PERMS
    cases = {
        "what do we have in sold and production": ("/customers-projects", ["Sold & in production"]),
        "show the estimate tab": ("/customers-projects", ["Estimate"]),
        "show completed projects": ("/customers-projects", ["Complete"]),
        "show turned down projects": ("/customers-projects", ["Turned Down"]),
        "let me create a customer": ("/customers-projects", ["Create Customer"]),
        "upload a document": ("/documents", ["Upload"]),
        "take me to leads": ("/customers-projects", ["Lead"]),
        "show lost leads": ("/customers-projects", ["Turned Down"]),
        "show the email templates": ("/automationplans", ["Email templates"]),
        "create a new plan": ("/automationplans", ["New plan"]),
        "show me the stage map": ("/performance", ["Stage map"]),
        "switch to performance view": ("/performance", ["Performance"]),
    }
    for utterance, (route, targets) in cases.items():
        response = nav_agent.try_feature_lookup(ctx(utterance))
        assert response is not None, utterance
        assert response.route == route, utterance
        assert response.highlightTargets == targets, utterance


def test_create_customer_never_autoclicks_the_admin_save_button(nav_agent, ctx):
    nav_agent.role_resolver.get_permissions.return_value = CRM_PERMS
    response = nav_agent.try_feature_lookup(ctx("let me create a customer"))
    assert response is not None
    assert response.route != "/admin/customer"


def test_customer_portal_fallback_lands_on_customers_and_projects(nav_agent):
    # /leadscustomers/:id has no resolver for :id; with /leadscustomers retired
    # the URL-prefix ancestor would otherwise be "/" (home).
    portal = nav_agent.route_by_id("customer_portal")
    assert nav_agent._nearest_static_ancestor(portal).pattern == "/customers-projects"


# AB#32825 -- Inventory became Material Mart (/materialmart) and the material
# catalogue moved from Admin's Materials tab (selectedTab "5") to Material
# Mart's Materials sub-tab (selectedTab "materials"). /inventory and
# /materialmanagement still exist in the app, but only as a redirect / a bare
# page, so EDEN must navigate to the Material Mart paths itself.
def test_materials_request_opens_material_mart_materials_subtab(nav_agent, ctx):
    route = nav_agent.exact_keyword_route("go to materials", ctx("go to materials"))
    assert route is not None and route.id == "material_management"
    assert route.resolved_route() == "/materialmart"
    assert route.resolved_route_state() == {"selectedTab": "materials"}
    assert route.permission_required == "CON.MAN"


def test_material_mart_name_reaches_material_mart(nav_agent, ctx):
    route = nav_agent.exact_keyword_route("open material mart", ctx("open material mart"))
    assert route is not None and route.id == "inventory"
    assert route.resolved_route() == "/materialmart"
    assert route.label_template == "Material Mart"


def test_material_mart_child_pages_live_under_materialmart(nav_agent):
    patterns = {r.id: r.pattern for r in nav_agent.route_registry}
    assert patterns["inventory_audit"] == "/materialmart/audit/:sku/:materialUid"
    assert patterns["inventory_report"] == "/materialmart/report"
    assert not any(p.startswith("/inventory") for p in patterns.values())


def test_add_material_from_another_page_opens_materials_subtab(nav_agent, ctx):
    response = nav_agent.try_feature_lookup(ctx("add new material", currentRoute="/dashboard"))
    assert response is not None
    assert response.route == "/materialmart"
    assert response.routeState == {"selectedTab": "materials"}
    assert response.highlight == "Add New Material button"


def test_admin_no_longer_offers_materials_or_inventory_tabs(nav_agent):
    admin = next(r for r in nav_agent.route_registry if r.id == "admin_home")
    tabs = {f.name for f in admin.page_features}
    assert "Materials tab" not in tabs
    assert "Inventory tab" not in tabs
    assert all((f.route_state or {}).get("selectedTab") not in ("5", "8") for f in admin.page_features)


def test_subcontractor_request_opens_admin_subcontractors_tab_not_vendors(nav_agent, ctx):
    # Real bug, fixed (found live 2026-10-09): vendor_list carried "subcontractors"/"subs"
    # as route keywords, so "take me to the subcontractor" opened Admin > Vendors (tab 2).
    # Subcontractors is its own Admin tab (7, behind the "subcontractor" flag, on by
    # default); the Vendors page lists no subcontractors.
    utterance = "take me to the subcontractor"
    route = nav_agent.exact_keyword_route(utterance, ctx(utterance))
    assert route is None or route.id != "vendor_list"
    response = nav_agent.try_feature_lookup(ctx(utterance, currentRoute="/dashboard"))
    assert response is not None
    assert response.route == "/admin"
    assert response.routeState == {"selectedTab": "7"}
    assert response.highlight == "Subcontractors tab"
