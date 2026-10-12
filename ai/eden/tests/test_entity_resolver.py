"""Unit tests for EntityResolver — project name matching against Mongo, with
the collection fully mocked so tests never touch a live database. Mock docs
use the real gold_eden.project_health field names (projectId/projectName),
not ProjectMatch's own internal field names (project_uid/project_name)."""
import os
from unittest.mock import MagicMock, patch

# Imported (not just referenced via patch's dotted string) so
# patch("source.utils.qdrant_client.QdrantVectorStore", ...) below can
# resolve the module -- patch's import machinery requires it already be in
# sys.modules, and entity_resolver.py only imports it lazily inside a method.
import source.utils.qdrant_client  # noqa: F401
from source.resolvers.entity_resolver import EntityResolver


def _make_cursor(docs: list[dict]):
    """A cursor mock usable both ways real code exercises it: iterated
    directly after .sort() (resolve_project's regex tier, list_projects),
    and chained through .limit(n) first (_resolve_by_order_no's exact
    lookup, which takes the first of an already-sorted result)."""
    cursor = MagicMock()
    cursor.__iter__.return_value = iter(docs)
    cursor.sort.return_value = cursor
    cursor.limit.side_effect = lambda n: iter(list(docs)[:n])
    return cursor


def _mock_collection(docs: list[dict], order_no_docs: list[dict] | None = None):
    """docs: what a projectName regex query (or an unfiltered list_projects-
    style query) should return. order_no_docs: what an exact orderNo query
    should return -- defaults to none, i.e. the order-number tier always
    misses and falls through to the name tier, matching every test here that
    isn't specifically about order-number matching (see the dedicated
    order-number test group below for the populated case).

    _resolve_by_order_no and resolve_project's name search both now go
    through collection.find(...) (neither uses find_one any more -- see
    entity_resolver.py's 2026-08-26 fix for the duplicate-orderNo-doc class
    of bug), so the two query shapes are distinguished by the presence of an
    "orderNo" key in the filter dict, the only reliable signal available
    from outside."""
    order_no_docs = order_no_docs or []
    collection = MagicMock()

    def find_side_effect(query, _projection):
        if "orderNo" in query:
            return _make_cursor(order_no_docs)
        return _make_cursor(docs)

    collection.find.side_effect = find_side_effect
    return collection


def test_exact_match_returns_one_result():
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    with patch("source.resolvers.entity_resolver.get_collection", return_value=_mock_collection(docs)):
        results = EntityResolver().resolve_project("Henderson Roofing", "tenant-1")
    assert len(results) == 1
    assert results[0].project_uid == "uid-1"


def test_partial_match_returns_result():
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    with patch("source.resolvers.entity_resolver.get_collection", return_value=_mock_collection(docs)):
        results = EntityResolver().resolve_project("henderson", "tenant-1")
    assert len(results) == 1


def test_no_match_returns_empty_list():
    with patch("source.resolvers.entity_resolver.get_collection", return_value=_mock_collection([])):
        results = EntityResolver().resolve_project("xyznonexistent", "tenant-1")
    assert results == []


def test_multiple_matches_returns_list():
    docs = [
        {"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"},
        {"projectId": "uid-2", "projectName": "Henderson Retail", "status": "active"},
    ]
    with patch("source.resolvers.entity_resolver.get_collection", return_value=_mock_collection(docs)):
        results = EntityResolver().resolve_project("henderson", "tenant-1")
    assert len(results) == 2


def test_name_search_returns_more_than_three_duplicate_names():
    # Real bug, fixed: confirmed live -- this query used to cap at 3 with NO
    # sort at all. Real Gold data has severe projectName duplication (a
    # dozen+ projects sharing one name), so a specific one (e.g. a project
    # matching a particular order number) could simply never appear among
    # an arbitrary, unsorted first 3 -- unreachable by name at all. Proves
    # the cap now matches NAME_MATCH_LIMIT (10), not the old 3. Each doc has
    # its OWN distinct orderNo, so dedup-by-orderNo (see
    # _dedupe_latest_per_order_no) doesn't collapse any of them.
    docs = [
        {"projectId": f"uid-{i}", "projectName": "Peter Parker", "status": "active", "orderNo": str(100000 + i)}
        for i in range(7)
    ]
    with patch("source.resolvers.entity_resolver.get_collection", return_value=_mock_collection(docs)):
        results = EntityResolver().resolve_project("Peter Parker", "tenant-1")
    assert len(results) == 7


def test_duplicate_order_no_siblings_deduped_to_the_most_recently_updated():
    # Real bug, fixed 2026-08-26: the source system creates a NEW internal
    # project id (not an update to the existing row) when a draft project is
    # converted/submitted -- confirmed live on real projects (order 977660:
    # ids 1232 draft -> 1233 current). Two docs can share one orderNo; only
    # the one Gold most recently touched (goldUpdatedAt) is the live one.
    docs = [
        {"projectId": "1232", "projectName": "Leeson Buss", "status": "draft",
         "orderNo": "482999", "goldUpdatedAt": "2026-08-26T10:22:20Z"},
        {"projectId": "1233", "projectName": "Leeson Buss", "status": "awaitingEstimation",
         "orderNo": "482999", "goldUpdatedAt": "2026-08-26T10:49:32Z"},
    ]
    with patch("source.resolvers.entity_resolver.get_collection", return_value=_mock_collection(docs)):
        results = EntityResolver().resolve_project("Leeson Buss", "tenant-1")
    assert len(results) == 1
    assert results[0].project_uid == "1233"


def test_multi_word_reply_falls_back_to_individual_word_match():
    """Real bug, fixed: a goal-continuation reply like "peter invoice
    details" (answering "which project?") still has "invoice" left over
    after filler-stripping ("details" is filler, "invoice" isn't project-
    name filler either) -- the whole phrase matched no project even though
    "Peter Parker" was the obviously-intended answer. Unlike the other
    tests here, this needs a mock that actually applies the regex filter
    (the shared _mock_collection always returns every doc unconditionally,
    which would make the full-phrase query trivially "match")."""
    docs = [{"projectId": "uid-1", "projectName": "Peter Parker", "status": "active"}]

    def fake_find(query, _projection):
        if "orderNo" in query:
            return _make_cursor([])
        name_filter = query.get("projectName")
        if isinstance(name_filter, dict) and "$regex" in name_filter:
            pattern = name_filter["$regex"]
            matched = [d for d in docs if pattern.search(d["projectName"])]
        else:
            matched = docs  # list_projects' unfiltered query, used by the normalized-match fallback
        return _make_cursor(matched)

    collection = MagicMock()
    collection.find.side_effect = fake_find
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        results = EntityResolver().resolve_project("peter invoice details", "tenant-1")
    assert len(results) == 1
    assert results[0].project_uid == "uid-1"


def test_single_word_no_match_does_not_recurse_into_word_split():
    # A single word that matches nothing must NOT trigger the word-split
    # fallback (it would just retry the same single word forever) --
    # guarded by `len(stripped.split()) > 1` in resolve_project.
    with patch("source.resolvers.entity_resolver.get_collection", return_value=_mock_collection([])):
        results = EntityResolver().resolve_project("nonexistent", "tenant-1")
    assert results == []


def test_empty_name_returns_empty_list_without_querying():
    with patch("source.resolvers.entity_resolver.get_collection") as mock_get:
        results = EntityResolver().resolve_project("", "tenant-1")
    assert results == []
    mock_get.assert_not_called()


def test_mongo_failure_returns_empty_list_not_raises():
    with patch("source.resolvers.entity_resolver.get_collection", side_effect=Exception("timeout")):
        results = EntityResolver().resolve_project("henderson", "tenant-1")
    assert results == []


# ── exact projectId lookup (resolve_by_id, page context) ────────────────────
# resolve_by_id is unaffected by the orderNo-dedup fix (an exact projectId is
# always unambiguous -- it names one specific Silver row, never a shared
# orderNo), so it still goes through find_one exactly as before.

def test_resolve_by_id_returns_single_exact_match():
    collection = MagicMock()
    collection.find_one.return_value = {
        "projectId": "9000001", "projectName": "DEMO-Riverside Roof Replacement",
        "status": "active",
    }
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        results = EntityResolver().resolve_by_id("9000001", "tenant-1")
    assert len(results) == 1
    assert results[0].project_uid == "9000001"
    call_filter = collection.find_one.call_args.args[0]
    # isDeleted is excluded so a project removed in the source app doesn't
    # keep surfacing via EDEN just because Gold retains the tombstoned doc.
    assert call_filter == {
        "tenantId": "tenant-1", "projectId": "9000001", "isDeleted": {"$ne": True},
    }


def test_resolve_by_id_empty_on_no_match():
    collection = MagicMock()
    collection.find_one.return_value = None
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        results = EntityResolver().resolve_by_id("nope", "tenant-1")
    assert results == []


def test_resolve_by_id_empty_on_blank_id_without_querying():
    with patch("source.resolvers.entity_resolver.get_collection") as mock_get:
        results = EntityResolver().resolve_by_id("  ", "tenant-1")
    assert results == []
    mock_get.assert_not_called()


def test_resolve_by_id_scoped_out_returns_empty():
    # allowed_project_ids that excludes this id -> no match, even though the
    # project exists (a user can't see a project outside their scope even by
    # being on its page). Short-circuits WITHOUT querying at all, since the
    # id provably isn't in scope -- no point hitting Mongo.
    collection = MagicMock()
    collection.find_one.return_value = None
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        results = EntityResolver().resolve_by_id(
            "9000001", "tenant-1", allowed_project_ids=["9000002"]
        )
    assert results == []
    collection.find_one.assert_not_called()


def test_resolve_by_id_in_scope_matches():
    collection = MagicMock()
    collection.find_one.return_value = {
        "projectId": "9000001", "projectName": "DEMO-Riverside", "status": "active",
    }
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        results = EntityResolver().resolve_by_id(
            "9000001", "tenant-1", allowed_project_ids=["9000001", "9000002"]
        )
    assert len(results) == 1
    call_filter = collection.find_one.call_args.args[0]
    assert call_filter["projectId"] == {"$in": ["9000001"]}


# ── order-number matching ────────────────────────────────────────────────────
# Real gap, fixed: project_health.orderNo was never checked anywhere -- only
# projectName. An order-number reference ("order 900001") resolved to
# nothing. Checked FIRST, as an exact match (never fuzzy), before the
# name-based chain.
#
# _resolve_by_order_no goes through collection.find(...).sort("goldUpdatedAt",
# -1).limit(1) rather than find_one (2026-08-26 fix) -- a plain find_one had
# no way to prefer the live doc over an abandoned duplicate sharing the same
# orderNo (see test_duplicate_order_no_siblings_deduped_to_the_most_recently_
# updated above). These tests mock that chain instead of find_one.

def _mock_order_no_collection(doc: dict | None):
    collection = MagicMock()
    collection.find.return_value = _make_cursor([doc] if doc else [])
    return collection


def test_bare_order_number_resolves_via_sorted_find():
    collection = _mock_order_no_collection({
        "projectId": "9000001", "projectName": "DEMO-Riverside Roof Replacement",
        "status": "active", "orderNo": "900001",
    })
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        results = EntityResolver().resolve_project("900001", "tenant-1")
    assert len(results) == 1
    assert results[0].project_name == "DEMO-Riverside Roof Replacement"
    call_filter = collection.find.call_args.args[0]
    assert call_filter == {
        "tenantId": "tenant-1", "orderNo": "900001", "isDeleted": {"$ne": True},
    }
    collection.find.return_value.sort.assert_called_once_with("goldUpdatedAt", -1)
    collection.find.return_value.limit.assert_called_once_with(1)


def test_order_number_with_order_prefix_is_stripped():
    collection = _mock_order_no_collection({
        "projectId": "9000001", "projectName": "DEMO-Riverside Roof Replacement",
        "status": "active", "orderNo": "900001",
    })
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        results = EntityResolver().resolve_project("order 900001", "tenant-1")
    assert len(results) == 1
    call_filter = collection.find.call_args.args[0]
    assert call_filter["orderNo"] == "900001"


def test_order_number_with_hash_prefix_is_stripped():
    collection = _mock_order_no_collection({
        "projectId": "9000001", "projectName": "DEMO-Riverside Roof Replacement",
        "status": "active", "orderNo": "900001",
    })
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        results = EntityResolver().resolve_project("order #900001", "tenant-1")
    assert len(results) == 1
    call_filter = collection.find.call_args.args[0]
    assert call_filter["orderNo"] == "900001"


def test_order_number_scoped_by_allowed_project_ids():
    collection = _mock_order_no_collection(None)
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        EntityResolver().resolve_project("900001", "tenant-1", allowed_project_ids=["uid-1"])
    call_filter = collection.find.call_args.args[0]
    assert call_filter["projectId"] == {"$in": ["uid-1"]}


def test_no_order_number_match_falls_through_to_name_query():
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    collection = _mock_collection(docs)  # order_no_docs defaults to none -> always misses
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        results = EntityResolver().resolve_project("henderson", "tenant-1")
    assert len(results) == 1
    assert results[0].project_name == "Henderson Roofing"


def test_order_number_lookup_failure_falls_through_to_name_query():
    # A raise from the order-number find() must not break the whole lookup --
    # fall through to the name-based path rather than propagating the error.
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]

    def find_side_effect(query, _projection):
        if "orderNo" in query:
            raise Exception("timeout")
        return _make_cursor(docs)

    collection = MagicMock()
    collection.find.side_effect = find_side_effect
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        results = EntityResolver().resolve_project("henderson", "tenant-1")
    assert len(results) == 1


def test_query_filters_by_tenant_id():
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    collection = _mock_collection(docs)
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        EntityResolver().resolve_project("henderson", "tenant-42")
    # Last call is the name-search query (order-number tier already missed
    # and ran first) -- both carry tenantId regardless.
    call_filter = collection.find.call_args[0][0]
    assert call_filter["tenantId"] == "tenant-42"


def test_missing_status_defaults_to_unknown():
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing"}]
    with patch("source.resolvers.entity_resolver.get_collection", return_value=_mock_collection(docs)):
        results = EntityResolver().resolve_project("henderson", "tenant-1")
    assert results[0].status == "unknown"


def test_null_status_defaults_to_unknown():
    # project_health.status is confirmed always null in real live data --
    # `.get("status") or "unknown"` must treat None the same as missing.
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": None}]
    with patch("source.resolvers.entity_resolver.get_collection", return_value=_mock_collection(docs)):
        results = EntityResolver().resolve_project("henderson", "tenant-1")
    assert results[0].status == "unknown"


def test_list_projects_returns_all_for_tenant_unfiltered_by_name():
    docs = [
        {"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"},
        {"projectId": "uid-2", "projectName": "Buma Retail", "status": "active"},
    ]
    collection = MagicMock()
    collection.find.return_value = _make_cursor(docs)
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        results = EntityResolver().list_projects("tenant-1")
    assert len(results) == 2
    call_filter = collection.find.call_args[0][0]
    assert call_filter == {"tenantId": "tenant-1", "isDeleted": {"$ne": True}}


def test_list_projects_dedupes_duplicate_order_no_siblings():
    # Same real bug as test_duplicate_order_no_siblings_deduped_... above,
    # exercised through list_projects instead of resolve_project -- the
    # no-name-given "which project?" listing must not show one real project
    # twice just because it has an abandoned draft-id sibling doc.
    docs = [
        {"projectId": "1232", "projectName": "Leeson Buss", "status": "draft",
         "orderNo": "482999", "goldUpdatedAt": "2026-08-26T10:22:20Z"},
        {"projectId": "1233", "projectName": "Leeson Buss", "status": "awaitingEstimation",
         "orderNo": "482999", "goldUpdatedAt": "2026-08-26T10:49:32Z"},
    ]
    collection = MagicMock()
    collection.find.return_value = _make_cursor(docs)
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        results = EntityResolver().list_projects("tenant-1", limit=5)
    assert len(results) == 1
    assert results[0].project_uid == "1233"


# ── project_ids scoping (allowed_project_ids) ────────────────────────────────
# Real gap, fixed: EntityResolver never read user_permissions.project_ids at
# all, even where it's populated. An empty/None list still means "no scoping
# data available" (unscoped) -- see UserRoleResolver.get_project_ids for why
# a genuinely empty list can't safely mean "show nothing" given real data.

def test_list_projects_none_allowed_ids_is_unscoped():
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    collection = MagicMock()
    collection.find.return_value = _make_cursor(docs)
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        EntityResolver().list_projects("tenant-1", allowed_project_ids=None)
    call_filter = collection.find.call_args[0][0]
    assert "projectId" not in call_filter


def test_list_projects_empty_allowed_ids_is_unscoped():
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    collection = MagicMock()
    collection.find.return_value = _make_cursor(docs)
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        EntityResolver().list_projects("tenant-1", allowed_project_ids=[])
    call_filter = collection.find.call_args[0][0]
    assert "projectId" not in call_filter


def test_list_projects_nonempty_allowed_ids_filters_query():
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    collection = MagicMock()
    collection.find.return_value = _make_cursor(docs)
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        EntityResolver().list_projects("tenant-1", allowed_project_ids=["uid-1", "uid-2"])
    call_filter = collection.find.call_args[0][0]
    assert call_filter["projectId"] == {"$in": ["uid-1", "uid-2"]}


def test_resolve_project_nonempty_allowed_ids_filters_query():
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    collection = _mock_collection(docs)
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        EntityResolver().resolve_project("henderson", "tenant-1", allowed_project_ids=["uid-1"])
    call_filter = collection.find.call_args[0][0]
    assert call_filter["projectId"] == {"$in": ["uid-1"]}


def test_resolve_project_scoping_excludes_out_of_scope_match():
    # The mocked collection itself just returns whatever docs it's given --
    # this proves the FILTER is actually sent to Mongo, which is what would
    # make Mongo itself exclude the out-of-scope project in a real query.
    docs = []  # a real Mongo query with this $in filter would return nothing
    collection = _mock_collection(docs)
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        results = EntityResolver().resolve_project(
            "henderson", "tenant-1", allowed_project_ids=["uid-2"]
        )
    assert results == []
    call_filter = collection.find.call_args[0][0]
    assert call_filter["projectId"] == {"$in": ["uid-2"]}


def test_list_projects_mongo_failure_returns_empty_list_not_raises():
    with patch("source.resolvers.entity_resolver.get_collection", side_effect=Exception("timeout")):
        results = EntityResolver().list_projects("tenant-1")
    assert results == []


# ── semantic fallback (ENTITY_RESOLUTION_BACKEND=semantic) ──────────────────
# The embeddings client is mocked throughout -- these tests verify the
# fallback wiring (regex-miss -> semantic -> ranked/thresholded results),
# not FastEmbed/ONNX itself.

def _resolver_with_semantic_backend(embeddings_client):
    with patch("source.utils.embeddings_client.EmbeddingsClient", return_value=embeddings_client), \
         patch.dict("os.environ", {"ENTITY_RESOLUTION_BACKEND": "semantic"}):
        return EntityResolver()


def _mock_embeddings_client(is_reachable=True):
    client = MagicMock()
    client.is_reachable.return_value = is_reachable
    return client


def test_regex_hit_never_calls_semantic_fallback():
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    embeddings_client = _mock_embeddings_client()
    resolver = _resolver_with_semantic_backend(embeddings_client)
    with patch("source.resolvers.entity_resolver.get_collection", return_value=_mock_collection(docs)):
        results = resolver.resolve_project("henderson", "tenant-1")
    assert len(results) == 1
    embeddings_client.embed.assert_not_called()


# ── normalized (separator/filler-insensitive) fallback ──────────────────────
# Real gap found via live testing: "the oak wood job" failed to resolve at
# all against "DEMO-Oakwood Bathroom Renovation" -- regex needs the exact
# substring "oak wood" (with the space); semantic scored it below threshold.
# This tier runs UNCONDITIONALLY (no embeddings, no feature flag) between
# regex and semantic.

def _find_side_effect_regex_miss_then_list(all_projects):
    """Simulates three distinct query shapes hitting the same
    collection.find(): _resolve_by_order_no (always misses here -- not what
    these tests are about), resolve_project's regex tier (misses, by
    construction of the test cases below), and list_projects' unfiltered
    query (used by the normalized/semantic/qdrant fallback tiers, returns
    the full candidate pool)."""
    def find_side_effect(query, _projection):
        if "orderNo" in query:
            return _make_cursor([])
        if "$regex" in str(query.get("projectName", "")):
            return _make_cursor([])
        return _make_cursor(all_projects)
    return find_side_effect


def test_whitespace_only_miss_resolves_with_regex_backend():
    # Forces "regex" explicitly -- this repo's .env may have
    # ENTITY_RESOLUTION_BACKEND=qdrant set for live runs, but this test
    # proves the normalized tier doesn't depend on any embeddings backend
    # being active at all.
    all_projects = [
        {"projectId": "uid-1", "projectName": "DEMO-Oakwood Bathroom Renovation", "status": "active"},
    ]
    collection = MagicMock()
    collection.find.side_effect = _find_side_effect_regex_miss_then_list(all_projects)
    with patch.dict(os.environ, {"ENTITY_RESOLUTION_BACKEND": "regex"}):
        resolver = EntityResolver()
        assert resolver.resolution_backend == "regex"
        with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
            results = resolver.resolve_project("oak wood", "tenant-1")
    assert len(results) == 1
    assert results[0].project_name == "DEMO-Oakwood Bathroom Renovation"


def test_leading_the_and_trailing_job_filler_are_stripped_before_normalizing():
    all_projects = [
        {"projectId": "uid-1", "projectName": "DEMO-Oakwood Bathroom Renovation", "status": "active"},
    ]
    collection = MagicMock()
    collection.find.side_effect = _find_side_effect_regex_miss_then_list(all_projects)
    with patch.dict(os.environ, {"ENTITY_RESOLUTION_BACKEND": "regex"}):
        resolver = EntityResolver()
        with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
            results = resolver.resolve_project("the oak wood job", "tenant-1")
    assert len(results) == 1
    assert results[0].project_name == "DEMO-Oakwood Bathroom Renovation"


def test_normalized_tier_runs_before_semantic_and_skips_embeddings_call():
    # "river side job" is a whitespace-only miss the normalized tier now
    # catches -- proving it runs BEFORE semantic (the embeddings client is
    # never even touched).
    all_projects = [
        {"projectId": "uid-1", "projectName": "Riverside Ave Retrofit", "status": "active"},
    ]
    embeddings_client = _mock_embeddings_client()
    resolver = _resolver_with_semantic_backend(embeddings_client)
    collection = MagicMock()
    collection.find.side_effect = _find_side_effect_regex_miss_then_list(all_projects)
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        results = resolver.resolve_project("river side job", "tenant-1")
    assert len(results) == 1
    assert results[0].project_name == "Riverside Ave Retrofit"
    embeddings_client.embed.assert_not_called()


def test_no_normalized_match_falls_through_empty_on_regex_backend():
    all_projects = [{"projectId": "uid-1", "projectName": "Oakwood Terrace", "status": "active"}]
    collection = MagicMock()
    collection.find.side_effect = _find_side_effect_regex_miss_then_list(all_projects)
    with patch.dict(os.environ, {"ENTITY_RESOLUTION_BACKEND": "regex"}):
        resolver = EntityResolver()
        with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
            results = resolver.resolve_project("completely unrelated phrase", "tenant-1")
    assert results == []


def test_normalized_match_rejects_query_matching_almost_every_project():
    # Real bug, fixed: "demo project" strips to "demo" after filler removal
    # -- and every project in a synthetic/demo tenant shares a "DEMO-"
    # prefix, so it matched ALL of them. That's not a real disambiguation
    # signal, it's a shared prefix accident -- must be rejected, not
    # presented as if the user asked something meaningfully ambiguous.
    all_projects = [
        {"projectId": f"uid-{i}", "projectName": f"DEMO-Project {i}", "status": "active"}
        for i in range(6)
    ]
    collection = MagicMock()
    collection.find.side_effect = _find_side_effect_regex_miss_then_list(all_projects)
    with patch.dict(os.environ, {"ENTITY_RESOLUTION_BACKEND": "regex"}):
        resolver = EntityResolver()
        with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
            results = resolver.resolve_project("demo project", "tenant-1")
    assert results == []


def test_normalized_match_accepts_a_genuinely_small_ambiguous_set():
    # A real, meaningful ambiguity (query matches a small handful, not
    # nearly everything) must still work -- this isn't a blanket ban on
    # multi-match results, only on ones too generic to mean anything.
    all_projects = [
        {"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"},
        {"projectId": "uid-2", "projectName": "Henderson Retail", "status": "active"},
    ]
    collection = MagicMock()
    collection.find.side_effect = _find_side_effect_regex_miss_then_list(all_projects)
    with patch.dict(os.environ, {"ENTITY_RESOLUTION_BACKEND": "regex"}):
        resolver = EntityResolver()
        with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
            results = resolver.resolve_project("henderson job", "tenant-1")
    assert len(results) == 2


def test_regex_miss_falls_back_to_semantic_match():
    all_projects = [
        {"projectId": "uid-1", "projectName": "Riverside Ave Retrofit", "status": "active"},
        {"projectId": "uid-2", "projectName": "Oakwood Terrace", "status": "active"},
    ]
    embeddings_client = _mock_embeddings_client()
    # "riverside road" (not "river side job"): a genuine synonym-style miss
    # (different word, "road" vs "ave") that ONLY semantic similarity can
    # catch -- the separator-insensitive normalized tier (see
    # _normalize_for_match) now catches whitespace-only misses like "river
    # side job" BEFORE this fallback ever runs, so this test picks a case
    # that tier can't resolve, to keep exercising semantic specifically.
    # embed() is called twice: once for the query name, once for the batch
    # of candidate project names -- keyed by input so each call gets the
    # right vectors regardless of call order.
    vectors_by_text = {
        "riverside road": [1.0, 0.0],
        "Riverside Ave Retrofit": [0.9, 0.1],
        "Oakwood Terrace": [0.1, 0.9],
    }
    embeddings_client.embed.side_effect = lambda texts: [vectors_by_text[t] for t in texts]
    resolver = _resolver_with_semantic_backend(embeddings_client)

    # First call (order-no) -> empty; second (regex) -> empty; third
    # (list_projects for the semantic pool) -> full candidate list.
    collection = MagicMock()
    collection.find.side_effect = _find_side_effect_regex_miss_then_list(all_projects)
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        results = resolver.resolve_project("riverside road", "tenant-1")

    assert len(results) == 1
    assert results[0].project_name == "Riverside Ave Retrofit"


def test_regex_miss_with_no_semantic_match_above_threshold_returns_empty():
    all_projects = [{"projectId": "uid-1", "projectName": "Oakwood Terrace", "status": "active"}]
    embeddings_client = _mock_embeddings_client()
    vectors_by_text = {
        "completely unrelated phrase": [1.0, 0.0],
        "Oakwood Terrace": [0.0, 1.0],  # orthogonal -> score 0
    }
    embeddings_client.embed.side_effect = lambda texts: [vectors_by_text[t] for t in texts]
    resolver = _resolver_with_semantic_backend(embeddings_client)

    collection = MagicMock()
    collection.find.side_effect = _find_side_effect_regex_miss_then_list(all_projects)
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        results = resolver.resolve_project("completely unrelated phrase", "tenant-1")
    assert results == []


def test_semantic_backend_disabled_by_default_no_embeddings_client():
    # ENTITY_RESOLUTION_BACKEND is enabled in this repo's .env for live runs;
    # this test isolates the *code's own* default (unset env) rather than
    # relying on whatever .env happens to say.
    with patch.dict("os.environ", {}, clear=False):
        os.environ.pop("ENTITY_RESOLUTION_BACKEND", None)
        resolver = EntityResolver()
    assert resolver.resolution_backend == "regex"
    assert resolver._embeddings_client is None


def test_semantic_init_failure_falls_back_to_regex():
    with patch("source.utils.embeddings_client.EmbeddingsClient", side_effect=Exception("model load failed")), \
         patch.dict("os.environ", {"ENTITY_RESOLUTION_BACKEND": "semantic"}):
        resolver = EntityResolver()
    assert resolver.resolution_backend == "regex"
    assert resolver._embeddings_client is None


# ── qdrant fallback (ENTITY_RESOLUTION_BACKEND=qdrant) ──────────────────────
# Both the embeddings client and the vector store are mocked -- these tests
# verify the index-then-search wiring, not a live Qdrant instance.

def _resolver_with_qdrant_backend(embeddings_client, vector_store):
    with patch("source.utils.embeddings_client.EmbeddingsClient", return_value=embeddings_client), \
         patch("source.utils.qdrant_client.QdrantVectorStore", return_value=vector_store), \
         patch.dict("os.environ", {"ENTITY_RESOLUTION_BACKEND": "qdrant"}):
        return EntityResolver()


def _mock_vector_store(is_reachable=True):
    store = MagicMock()
    store.is_reachable.return_value = is_reachable
    return store


def test_qdrant_backend_indexes_candidates_then_searches():
    all_projects = [
        {"projectId": "uid-1", "projectName": "Riverside Ave Retrofit", "status": "active"},
        {"projectId": "uid-2", "projectName": "Oakwood Terrace", "status": "active"},
    ]
    embeddings_client = _mock_embeddings_client()
    # "riverside road" (not "river side job") -- see the identical note in
    # test_regex_miss_falls_back_to_semantic_match: this needs to be a case
    # the separator-insensitive normalized tier can't catch, so it still
    # reaches qdrant specifically.
    vectors_by_text = {
        "riverside road": [1.0, 0.0],
        "Riverside Ave Retrofit": [0.9, 0.1],
        "Oakwood Terrace": [0.1, 0.9],
    }
    embeddings_client.embed.side_effect = lambda texts: [vectors_by_text[t] for t in texts]
    vector_store = _mock_vector_store()
    vector_store.search_projects.return_value = [("uid-1", "Riverside Ave Retrofit", 0.97)]
    resolver = _resolver_with_qdrant_backend(embeddings_client, vector_store)
    assert resolver.resolution_backend == "qdrant"

    collection = MagicMock()
    collection.find.side_effect = _find_side_effect_regex_miss_then_list(all_projects)
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        results = resolver.resolve_project("riverside road", "tenant-1")

    assert len(results) == 1
    assert results[0].project_name == "Riverside Ave Retrofit"
    vector_store.index_projects.assert_called_once()
    index_args = vector_store.index_projects.call_args.args
    assert index_args[0] == "tenant-1"
    assert index_args[1] == ["uid-1", "uid-2"]
    assert index_args[2] == ["Riverside Ave Retrofit", "Oakwood Terrace"]
    vector_store.search_projects.assert_called_once_with("tenant-1", [1.0, 0.0], k=3)


def test_qdrant_backend_below_threshold_hit_is_dropped():
    all_projects = [{"projectId": "uid-1", "projectName": "Oakwood Terrace", "status": "active"}]
    embeddings_client = _mock_embeddings_client()
    embeddings_client.embed.side_effect = lambda texts: [[0.0, 0.0] for _ in texts]
    vector_store = _mock_vector_store()
    vector_store.search_projects.return_value = [("uid-1", "Oakwood Terrace", 0.2)]
    resolver = _resolver_with_qdrant_backend(embeddings_client, vector_store)

    collection = MagicMock()
    collection.find.side_effect = _find_side_effect_regex_miss_then_list(all_projects)
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        results = resolver.resolve_project("nothing similar", "tenant-1")
    assert results == []


def test_qdrant_init_failure_falls_back_to_regex():
    with patch("source.utils.embeddings_client.EmbeddingsClient", return_value=_mock_embeddings_client()), \
         patch("source.utils.qdrant_client.QdrantVectorStore", side_effect=Exception("qdrant unreachable")), \
         patch.dict("os.environ", {"ENTITY_RESOLUTION_BACKEND": "qdrant"}):
        resolver = EntityResolver()
    assert resolver.resolution_backend == "regex"
    assert resolver._embeddings_client is None
    assert resolver._vector_store is None


def test_qdrant_not_reachable_falls_back_to_regex():
    with patch("source.utils.embeddings_client.EmbeddingsClient", return_value=_mock_embeddings_client()), \
         patch("source.utils.qdrant_client.QdrantVectorStore", return_value=_mock_vector_store(is_reachable=False)), \
         patch.dict("os.environ", {"ENTITY_RESOLUTION_BACKEND": "qdrant"}):
        resolver = EntityResolver()
    assert resolver.resolution_backend == "regex"
    assert resolver._vector_store is None



def test_find_ordered_project_in_text_recovers_bare_order_number():
    # Real bug, fixed: confirmed live -- "what is the project cost for
    # 682053" (a bare order number, no "order"/"#" prefix) got a null
    # extraction from the LLM despite EXTRACT_PROJECT_SYSTEM_PROMPT
    # explicitly covering this case, even though the real project resolves
    # instantly by a direct query against project_health.orderNo -- a
    # small-model instruction-following miss, not a data gap. Same
    # ground-truth-recovery shape as find_named_project_in_text, for the
    # ORDER NUMBER half of extraction instead of the name half.
    collection = MagicMock()
    collection.distinct.return_value = ["682053"]
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        result = EntityResolver().find_ordered_project_in_text(
            "what is the project cost for 682053", "tenant-1"
        )
    assert result == "682053"
    # The candidate digit run was actually checked against real data, not
    # just echoed back -- confirms the query used the extracted number.
    query = collection.distinct.call_args.args[1]
    assert query["orderNo"]["$in"] == ["682053"]


def test_find_ordered_project_in_text_no_digits_returns_none():
    result = EntityResolver().find_ordered_project_in_text(
        "why is cost over budget", "tenant-1"
    )
    assert result is None


def test_find_ordered_project_in_text_ambiguous_multiple_matches_returns_none():
    # Two different real order numbers both appear in the message -- same
    # "don't guess" rule find_named_project_in_text applies to multiple
    # name matches.
    collection = MagicMock()
    collection.distinct.return_value = ["682053", "297655"]
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        result = EntityResolver().find_ordered_project_in_text(
            "compare cost for 682053 and 297655", "tenant-1"
        )
    assert result is None


def test_find_ordered_project_in_text_no_real_match_returns_none():
    # A short/incidental number in the message (e.g. "in 3 days") that
    # isn't any real project's order number must not be treated as one.
    collection = MagicMock()
    collection.distinct.return_value = []
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        result = EntityResolver().find_ordered_project_in_text(
            "will this be done in 2026 days", "tenant-1"
        )
    assert result is None


# --- isDeleted exclusion --------------------------------------------------
# Real gap, fixed: Gold's project_health deliberately does NOT drop a
# project removed in the source app -- the loader carries isDeleted/
# deletedAt through as data instead of filtering at the pipeline layer
# (see load_project_health.py), so consumers are expected to filter. None
# of EntityResolver's Mongo queries did, so a project deleted in the real
# app kept surfacing via EDEN (listed, resolved by name/order number,
# answered about) even though the application itself no longer shows it.
# Every query-building method now excludes isDeleted=true; these tests
# cover the methods not already exercised by a call_filter assertion above.

def test_find_ordered_project_in_text_excludes_deleted():
    collection = MagicMock()
    collection.distinct.return_value = ["682053"]
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        EntityResolver().find_ordered_project_in_text("order 682053", "tenant-1")
    call_filter = collection.distinct.call_args.args[1]
    assert call_filter["isDeleted"] == {"$ne": True}


def test_find_named_project_in_text_excludes_deleted():
    collection = MagicMock()
    collection.distinct.return_value = ["Peter Parker"]
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        EntityResolver().find_named_project_in_text("about peter parker's project", "tenant-1")
    call_filter = collection.distinct.call_args.args[1]
    assert call_filter["isDeleted"] == {"$ne": True}


def test_count_projects_excludes_deleted():
    collection = MagicMock()
    collection.count_documents.return_value = 3
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        EntityResolver().count_projects("tenant-1")
    call_filter = collection.count_documents.call_args.args[0]
    assert call_filter["isDeleted"] == {"$ne": True}


def test_project_ids_for_customer_excludes_deleted():
    collection = MagicMock()
    collection.find.return_value = []
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        EntityResolver().project_ids_for_customer("tenant-1", "customer-uid-1")
    call_filter = collection.find.call_args.args[0]
    assert call_filter["isDeleted"] == {"$ne": True}


def test_project_ids_for_assignee_excludes_deleted():
    collection = MagicMock()
    collection.find.return_value = []
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        EntityResolver().project_ids_for_assignee("tenant-1", "user-1")
    call_filter = collection.find.call_args.args[0]
    assert call_filter["isDeleted"] == {"$ne": True}


def test_resolve_project_name_regex_excludes_deleted():
    docs = [{"projectId": "uid-1", "projectName": "Henderson Roofing", "status": "active"}]
    collection = _mock_collection(docs)
    with patch("source.resolvers.entity_resolver.get_collection", return_value=collection):
        EntityResolver().resolve_project("henderson", "tenant-1")
    call_filter = collection.find.call_args[0][0]
    assert call_filter["isDeleted"] == {"$ne": True}
