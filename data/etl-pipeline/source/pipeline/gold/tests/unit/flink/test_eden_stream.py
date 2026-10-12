"""Unit tests for the Eden real-time streaming job (gold_eden_stream).

Focus: the R2 bounded-retry parity in EdenReaggregator.on_timer — success
clears the collection from pending, a transient failure keeps it pending and
arms a retry timer (rather than dropping it to self-heal on the next CDC
change). The KeyedProcessFunction is driven directly with fake Flink state /
timer-service / context, so no running Flink cluster is needed.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timezone

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.eden]

MOD = "gold.src.flink.jobs.gold_eden_stream"


@pytest.fixture
def mod():
    return importlib.import_module(MOD)


# --- fake Flink primitives --------------------------------------------------

class FakeState:
    def __init__(self, v=None):
        self._v = v

    def value(self):
        return self._v

    def update(self, v):
        self._v = v

    def clear(self):
        self._v = None


class FakeTimerService:
    def __init__(self, now=1000):
        self._now = now
        self.registered: list[int] = []

    def current_processing_time(self):
        return self._now

    def register_processing_time_timer(self, ts):
        self.registered.append(ts)

    def delete_processing_time_timer(self, ts):
        pass


class FakeCtx:
    def __init__(self, key, ts):
        self._key = key
        self._ts = ts

    def get_current_key(self):
        return self._key

    def timer_service(self):
        return self._ts


def _make_reaggregator(mod, key, pending):
    """Build an EdenReaggregator with hand-wired state, bypassing open().

    Must mirror EVERY ValueState that open() registers — the operator reads them
    unguarded, so a state added there without being added here fails with an
    AttributeError inside on_timer that looks unrelated to the change that
    caused it. Keep this in step with EdenReaggregator.open().
    """
    r = mod.EdenReaggregator()
    r._pending = FakeState(list(pending))
    r._timer = FakeState(500)
    r._fail = {c: FakeState(0) for c in mod._FILTER_FIELDS}
    # Retry-timer bookkeeping: process_element cancels these so recovered keys
    # don't fire phantom on_timer calls.
    r._retry_timers = FakeState({})
    # Debounce-window instrumentation: first_seen also bounds coalescing
    # (EDEN_DEBOUNCE_MAX_MS), so it is load-bearing, not just observability.
    r._first_seen = FakeState(0)
    r._trigger_count = FakeState(0)
    # Selective fan-out: content_hash is the committed per-collection read-set
    # hash, pending_hash stages the value promoted only after a clean build.
    r._content_hash = {c: FakeState(None) for c in mod._FILTER_FIELDS}
    r._pending_hash = FakeState({})
    r._pg_dsn = "postgresql://test/fieldcraft_silver"
    ts = FakeTimerService()
    return r, FakeCtx(key, ts), ts


# --- tests ------------------------------------------------------------------

class TestEdenReaggregatorRetry:
    def test_success_emits_tagged_doc_and_clears_pending(self, mod, monkeypatch):
        monkeypatch.setattr(
            mod.load_project_health, "build_documents",
            lambda *a, **k: [{"tenantId": "T", "projectId": "42", "completionPct": 50}],
        )
        r, ctx, ts = _make_reaggregator(mod, "T|42", ["project_health"])
        out = list(r.on_timer(0, ctx))
        assert len(out) == 1
        assert out[0]["_collection"] == "project_health"
        assert out[0]["projectId"] == "42"
        # succeeded → dropped from pending, no retry timer, fail-state stays 0
        assert r._pending.value() is None
        assert ts.registered == []
        assert (r._fail["project_health"].value() or 0) == 0

    def test_transient_failure_keeps_pending_and_arms_retry(self, mod, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("silver blip")

        monkeypatch.setattr(mod.load_project_health, "build_documents", boom)
        r, ctx, ts = _make_reaggregator(mod, "T|42", ["project_health"])
        out = list(r.on_timer(0, ctx))
        assert out == []                                  # nothing emitted
        assert r._fail["project_health"].value() == 1     # attempt counted
        assert r._pending.value() == ["project_health"]   # stays pending
        assert len(ts.registered) == 1                    # retry timer armed

    def test_one_failing_collection_does_not_block_a_healthy_one(self, mod, monkeypatch):
        monkeypatch.setattr(
            mod.load_task_intelligence, "build_documents",
            lambda *a, **k: [{"tenantId": "T", "projectId": "42"}],
        )

        def boom(*a, **k):
            raise RuntimeError("blip")

        monkeypatch.setattr(mod.load_project_health, "build_documents", boom)
        r, ctx, ts = _make_reaggregator(mod, "T|42", ["project_health", "task_intelligence"])
        out = list(r.on_timer(0, ctx))
        # healthy collection still emitted; failing one retained for retry
        assert [d["_collection"] for d in out] == ["task_intelligence"]
        assert r._pending.value() == ["project_health"]

    def test_tenant_dashboard_path(self, monkeypatch):
        """Tenant-grain recompute, with the collection gate forced ON.

        tenant_dashboard is NOT in gold_config.yml's eden.enabled_collections, so
        with the real gate it is absent from _FILTER_FIELDS — and on_timer indexes
        self._fail[collection] unguarded, so driving it directly raised KeyError.
        That is not a code bug: the module-level assert
        (_ALL_ROUTED_COLLECTIONS <= _FILTER_FIELDS) guarantees a disabled
        collection can never be routed, so the state this test was constructing
        by hand is unreachable in production.

        Reloading with the gate ON — the same helper the router tests use —
        exercises the real path instead of a state that cannot occur.
        """
        mod = _reload_with_collection_gate(monkeypatch, tenant_dashboard_enabled=True)
        try:
            monkeypatch.setattr(
                mod.load_tenant_dashboard, "build_documents",
                lambda *a, **k: [{"tenantId": "T", "projectCount": 3}],
            )
            r, ctx, ts = _make_reaggregator(
                mod, f"T|{mod._TENANT_SCOPE}", ["tenant_dashboard"])
            out = list(r.on_timer(0, ctx))
            assert len(out) == 1 and out[0]["_collection"] == "tenant_dashboard"
            assert r._pending.value() is None
        finally:
            monkeypatch.undo()
            import importlib
            importlib.reload(mod)


def _reload_with_collection_gate(monkeypatch, *, tenant_dashboard_enabled: bool):
    """Reload gold_eden_stream with step_registry.is_collection_enabled forced
    to a known value, so _PROJECT_BUILDERS/_FILTER_FIELDS/_TABLE_TO_COLLECTIONS
    (computed once at import time — see gold_eden_stream.py's module-level
    dict comprehensions) reflect a deterministic gate state rather than
    whatever gold_config.yml currently says.
    """
    import importlib
    import gold.pipeline.step_registry as step_registry

    def _gate(feature, collection):
        if collection == "tenant_dashboard":
            return tenant_dashboard_enabled
        return True

    monkeypatch.setattr(step_registry, "is_collection_enabled", _gate)
    mod = importlib.import_module(MOD)
    importlib.reload(mod)
    return mod


class TestEdenCdcRouter:
    def test_routes_project_change_to_enabled_collections(self, mod):
        import json
        evt = json.dumps({
            "op": "u", "source_table": "public.project",
            "id": 42, "_s_tenantid": "T",
        })
        out = list(mod.EdenCdcRouter().flat_map(evt))
        proj = [o for o in out if o["projectId"] == "42"]
        assert len(proj) == 1 and "project_health" in proj[0]["collections"]

    def test_tenant_dashboard_routed_when_enabled(self, monkeypatch):
        # Regression guard for the Phase 1 enabled_collections gate: force
        # tenant_dashboard ENABLED and assert the tenant-grain trigger fires.
        # (Previously this branched on is_collection_enabled() itself, so it
        # passed regardless of whether the gating logic was correct.)
        import json
        mod = _reload_with_collection_gate(monkeypatch, tenant_dashboard_enabled=True)
        try:
            evt = json.dumps({
                "op": "u", "source_table": "public.project",
                "id": 42, "_s_tenantid": "T",
            })
            out = list(mod.EdenCdcRouter().flat_map(evt))
            tenant = [o for o in out if o["projectId"] == mod._TENANT_SCOPE]
            assert len(tenant) == 1 and tenant[0]["collections"] == ["tenant_dashboard"]
        finally:
            # restore the real gate and reload so later tests in this module
            # (and the module-scoped `mod` fixture) see the real config again.
            monkeypatch.undo()
            import importlib
            importlib.reload(mod)

    def test_tenant_dashboard_not_routed_when_disabled(self, monkeypatch):
        # Mirror of the above with the gate forced OFF.
        import json
        mod = _reload_with_collection_gate(monkeypatch, tenant_dashboard_enabled=False)
        try:
            evt = json.dumps({
                "op": "u", "source_table": "public.project",
                "id": 42, "_s_tenantid": "T",
            })
            out = list(mod.EdenCdcRouter().flat_map(evt))
            tenant = [o for o in out if o["projectId"] == mod._TENANT_SCOPE]
            assert tenant == []
        finally:
            monkeypatch.undo()
            import importlib
            importlib.reload(mod)

    def test_irrelevant_table_ignored(self, mod):
        import json
        evt = json.dumps({"op": "u", "source_table": "public.unrelated",
                          "id": 1, "_s_tenantid": "T"})
        assert list(mod.EdenCdcRouter().flat_map(evt)) == []


class TestEdenCdcRouterAutocommit:
    """Regression guard for the PR's stated root-cause fix: EdenCdcRouter._cursor()
    setting connection.autocommit = True (gold_eden_stream.py, in _cursor()).

    Without it, psycopg2's default (autocommit=False) leaves a bare SELECT's
    transaction open until the next commit/rollback -- and _lookup_project_ids
    only ever closes the CURSOR, never commits -- so the connection sat idle in
    a transaction between CDC events. That pins Silver's xmin horizon, blocking
    autovacuum, which is what actually degraded query performance in the
    incident this PR traces to (Task #28476). A regression that reverted
    autocommit to the psycopg2 default would pass every OTHER test in this
    file unnoticed, since none of them reach the Postgres lookup path --
    every existing TestEdenCdcRouter case drives flat_map() with
    source_table="public.project", which never calls _cursor() at all.
    """

    def test_cursor_sets_autocommit_true(self, mod, monkeypatch):
        from unittest.mock import MagicMock
        conn = MagicMock()
        conn.closed = False
        monkeypatch.setattr(mod.psycopg2, "connect", lambda dsn: conn)

        router = mod.EdenCdcRouter()
        router._pg_dsn = "postgresql://test/fieldcraft_silver"
        router._conn = None

        router._cursor()

        assert conn.autocommit is True, (
            "EdenCdcRouter._cursor() must set autocommit=True on every new "
            "connection -- without it a bare SELECT leaves the connection "
            "idle-in-transaction, pinning Silver's xmin horizon (Task #28476)."
        )

    def test_lookup_triggering_table_reaches_cursor_via_flat_map(self, mod, monkeypatch):
        """End-to-end: a real _LOOKUP_TABLES CDC event, driven through the
        public flat_map() entry point exactly as Kafka would deliver it, must
        actually reach _cursor() -- proving the autocommit fix is on the path
        real traffic takes, not just reachable if called directly."""
        import json
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchall.return_value = [("1206",)]
        conn = MagicMock()
        conn.closed = False
        conn.cursor.return_value = cur
        monkeypatch.setattr(mod.psycopg2, "connect", lambda dsn: conn)

        evt = json.dumps({
            "op": "u", "source_table": "public.resource_schedule",
            "resource_id": "r-1", "project_id": "1206", "_s_tenantid": "T",
            "_s_env": "prod",
        })
        router = mod.EdenCdcRouter()
        # flat_map() alone doesn't call open() -- Flink calls it once per task
        # lifecycle, before any element arrives. Mirror that here rather than
        # skip it, since _cursor()/_lookup_project_ids both assume it already
        # ran (real usage never calls flat_map() without a prior open()).
        router._pg_dsn = "postgresql://test/fieldcraft_silver"
        router._conn = None
        list(router.flat_map(evt))

        assert conn.cursor.called, (
            "a resource_schedule CDC event (a _LOOKUP_TABLES member routing to "
            "project_health) must reach _cursor() via flat_map() -- if this "
            "stops firing, the autocommit fix silently stops applying to real "
            "traffic even though test_cursor_sets_autocommit_true still passes."
        )
        assert conn.autocommit is True
