"""Unit tests for the canonical real-time streaming job (gold_user_permissions_stream).

Focus: CanonicalCdcRouter's per-table trigger routing — including the
three-way customer_asset fan-out (project/customer/asset from one CDC event)
and the canonical.enabled_collections phase-in gate — and
CanonicalReaggregator's per-collection bounded-retry/debounce bookkeeping
across all five grains (user_permissions/tenant/project/customer/asset). The
KeyedProcessFunction is driven directly with fake Flink state/timer-service/
context, so no running Flink cluster is needed (mirrors test_eden_stream.py).
"""

from __future__ import annotations

import importlib
import json

import pytest

pytestmark = [pytest.mark.unit]

MOD = "gold.src.flink.jobs.gold_user_permissions_stream"


@pytest.fixture
def mod():
    return importlib.import_module(MOD)


# --- fake Flink primitives (identical to test_eden_stream.py) --------------

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
    r = mod.CanonicalReaggregator()
    r._pending = FakeState(list(pending))
    r._timer = FakeState(500)
    r._retry_timers = FakeState({})
    r._fail = {c: FakeState(0) for c in mod._FILTER_FIELDS}
    r._pg_dsn = "postgresql://test/fieldcraft_silver"
    ts = FakeTimerService()
    return r, FakeCtx(key, ts), ts


def _evt(table: str, **fields) -> str:
    return json.dumps({"op": "u", "source_table": f"public.{table}",
                        "_s_tenantid": "T", **fields})


# --- CanonicalCdcRouter ------------------------------------------------------

class TestCanonicalCdcRouter:
    def test_irrelevant_table_ignored(self, mod):
        assert list(mod.CanonicalCdcRouter().flat_map(_evt("unrelated", id=1))) == []

    def test_user_account_direct_key_routes_to_user_permissions(self, mod):
        out = list(mod.CanonicalCdcRouter().flat_map(_evt("user_account", id=7)))
        assert out == [{"tenantId": "T", "scope": "user:7", "collection": "user_permissions"}]

    def test_user_role_map_direct_key(self, mod):
        out = list(mod.CanonicalCdcRouter().flat_map(_evt("user_role_map", user_account_id=9)))
        assert out == [{"tenantId": "T", "scope": "user:9", "collection": "user_permissions"}]

    def test_resource_direct_key(self, mod):
        out = list(mod.CanonicalCdcRouter().flat_map(_evt("resource", user_id=3)))
        assert out == [{"tenantId": "T", "scope": "user:3", "collection": "user_permissions"}]

    @pytest.mark.parametrize("table", ["roles", "role_permission", "project_member"])
    def test_tenant_wide_rbac_tables_route_to_tenant_scope(self, mod, table):
        out = list(mod.CanonicalCdcRouter().flat_map(_evt(table, id=1)))
        assert out == [{"tenantId": "T", "scope": f"user:{mod._TENANT_SCOPE}",
                        "collection": "user_permissions"}]

    def test_tenant_table_routes_to_tenant_grain(self, mod):
        out = list(mod.CanonicalCdcRouter().flat_map(_evt("tenant", id=1)))
        assert out == [{"tenantId": "T", "scope": f"tenant:{mod._TENANT_SCOPE}",
                        "collection": "tenant"}]

    def test_project_table_direct_key_routes_to_project(self, mod):
        out = list(mod.CanonicalCdcRouter().flat_map(_evt("project", id=42)))
        assert out == [{"tenantId": "T", "scope": "project:42", "collection": "project"}]

    def test_project_table_missing_id_yields_nothing(self, mod):
        assert list(mod.CanonicalCdcRouter().flat_map(_evt("project"))) == []

    def test_customer_asset_fans_out_to_three_collections(self, mod):
        out = list(mod.CanonicalCdcRouter().flat_map(
            _evt("customer_asset", project_id=42, customer_uid="C1", asset_uid="A1")
        ))
        assert out == [
            {"tenantId": "T", "scope": "project:42", "collection": "project"},
            {"tenantId": "T", "scope": "customer:C1", "collection": "customer"},
            {"tenantId": "T", "scope": "asset:A1", "collection": "asset"},
        ]

    def test_customer_asset_partial_keys_only_yields_present_ones(self, mod):
        # project_id absent (e.g. a not-yet-linked asset row) -- only
        # customer/asset triggers should fire, not a bogus project trigger.
        out = list(mod.CanonicalCdcRouter().flat_map(
            _evt("customer_asset", customer_uid="C1", asset_uid="A1")
        ))
        assert out == [
            {"tenantId": "T", "scope": "customer:C1", "collection": "customer"},
            {"tenantId": "T", "scope": "asset:A1", "collection": "asset"},
        ]

    def test_fieldcraft_customer_customer_routes_to_customer_only(self, mod):
        out = list(mod.CanonicalCdcRouter().flat_map(
            _evt("fieldcraft_customer_customer", customer_uuid="C2")
        ))
        assert out == [{"tenantId": "T", "scope": "customer:C2", "collection": "customer"}]

    def test_missing_tenant_id_yields_nothing(self, mod):
        evt = json.dumps({"op": "u", "source_table": "public.tenant", "id": 1})
        assert list(mod.CanonicalCdcRouter().flat_map(evt)) == []

    def test_fieldcraft_customer_lead_takes_tenant_from_row_not_s_tenantid(self, mod):
        # Customer DB rows carry _s_tenantid="fieldcraft_customer"; the real tenant
        # is the row's own tenant_id column.
        evt = json.dumps({"op": "u", "source_table": "public.fieldcraft_customer_lead",
                          "_s_tenantid": "fieldcraft_customer", "tenant_id": "T-REAL",
                          "lead_uid": "L1"})
        out = list(mod.CanonicalCdcRouter().flat_map(evt))
        assert out == [{"tenantId": "T-REAL", "scope": "lead:L1", "collection": "lead"}]

    def test_fieldcraft_customer_lead_without_tenant_id_yields_nothing(self, mod):
        evt = json.dumps({"op": "u", "source_table": "public.fieldcraft_customer_lead",
                          "_s_tenantid": "fieldcraft_customer", "lead_uid": "L1"})
        assert list(mod.CanonicalCdcRouter().flat_map(evt)) == []

    def test_bare_lead_table_is_not_a_trigger(self, mod):
        # silver.lead never exists — the Customer DB table is namespaced.
        assert list(mod.CanonicalCdcRouter().flat_map(_evt("lead", lead_uid="L1"))) == []


def _reload_with_collection_gate(monkeypatch, *, disabled: set[str]):
    """Reload gold_user_permissions_stream with step_registry.is_collection_enabled
    forced to a known value, so _FILTER_FIELDS (computed once at import time)
    reflects a deterministic gate state rather than whatever gold_config.yml
    currently says. Mirrors test_eden_stream.py's identical helper.
    """
    import gold.pipeline.step_registry as step_registry

    def _gate(feature, collection):
        return collection not in disabled

    monkeypatch.setattr(step_registry, "is_collection_enabled", _gate)
    m = importlib.import_module(MOD)
    importlib.reload(m)
    return m


class TestCanonicalEnabledCollectionsGate:
    def test_disabled_collection_dropped_from_customer_asset_fanout(self, monkeypatch):
        # Regression guard for the streaming path honoring the same
        # canonical.enabled_collections allowlist step_registry.py's
        # build_steps() already applies to the batch/rebuild path -- a
        # partial rollout (e.g. only tenant/project enabled) must not have
        # the live stream keep computing/upserting customer/asset anyway.
        m = _reload_with_collection_gate(monkeypatch, disabled={"customer", "asset"})
        try:
            out = list(m.CanonicalCdcRouter().flat_map(
                _evt("customer_asset", project_id=42, customer_uid="C1", asset_uid="A1")
            ))
            assert out == [{"tenantId": "T", "scope": "project:42", "collection": "project"}]
            assert "customer" not in m._FILTER_FIELDS
            assert "asset" not in m._FILTER_FIELDS
        finally:
            monkeypatch.undo()
            importlib.reload(m)

    def test_all_enabled_by_default(self, monkeypatch):
        m = _reload_with_collection_gate(monkeypatch, disabled=set())
        try:
            assert set(m._FILTER_FIELDS) == {
                "user_permissions", "tenant", "project", "customer", "asset",
                "lead", "communication_log",
            }
        finally:
            monkeypatch.undo()
            importlib.reload(m)


# --- CanonicalReaggregator ----------------------------------------------------

class TestCanonicalReaggregatorRetry:
    def test_user_permissions_direct_user_success(self, mod, monkeypatch):
        monkeypatch.setattr(
            mod.load_user_permissions, "build_documents",
            lambda pg, tenant, ts, user_account_id=None: [
                {"tenantId": tenant, "user_uid": user_account_id}
            ],
        )
        r, ctx, ts = _make_reaggregator(mod, "T|user:7", ["user_permissions"])
        out = list(r.on_timer(0, ctx))
        assert len(out) == 1
        assert out[0]["_collection"] == "user_permissions"
        assert out[0]["user_uid"] == "7"
        assert r._pending.value() is None
        assert ts.registered == []

    def test_user_permissions_tenant_wide_scope_passes_none(self, mod, monkeypatch):
        seen = {}

        def fake_build(pg, tenant, ts, user_account_id=None):
            seen["user_account_id"] = user_account_id
            return [{"tenantId": tenant}]

        monkeypatch.setattr(mod.load_user_permissions, "build_documents", fake_build)
        r, ctx, ts = _make_reaggregator(mod, f"T|user:{mod._TENANT_SCOPE}", ["user_permissions"])
        list(r.on_timer(0, ctx))
        assert seen["user_account_id"] is None

    def test_tenant_success(self, mod, monkeypatch):
        monkeypatch.setattr(
            mod.load_tenant, "build_documents",
            lambda pg, tenant, ts: [{"tenantId": tenant, "name": "Acme"}],
        )
        r, ctx, ts = _make_reaggregator(mod, f"T|tenant:{mod._TENANT_SCOPE}", ["tenant"])
        out = list(r.on_timer(0, ctx))
        assert len(out) == 1 and out[0]["_collection"] == "tenant"
        assert r._pending.value() is None

    def test_project_success_passes_scope_as_project_id(self, mod, monkeypatch):
        seen = {}

        def fake_build(pg, tenant, ts, project_id=None):
            seen["project_id"] = project_id
            return [{"tenantId": tenant, "projectId": project_id}]

        monkeypatch.setattr(mod.load_project, "build_documents", fake_build)
        r, ctx, ts = _make_reaggregator(mod, "T|project:42", ["project"])
        out = list(r.on_timer(0, ctx))
        assert seen["project_id"] == "42"
        assert out[0]["_collection"] == "project"

    def test_customer_success_passes_scope_as_customer_uid(self, mod, monkeypatch):
        seen = {}

        def fake_build(pg, tenant, ts, customer_uid=None):
            seen["customer_uid"] = customer_uid
            return [{"tenantId": tenant, "customerUid": customer_uid}]

        monkeypatch.setattr(mod.load_customer, "build_documents", fake_build)
        r, ctx, ts = _make_reaggregator(mod, "T|customer:C1", ["customer"])
        out = list(r.on_timer(0, ctx))
        assert seen["customer_uid"] == "C1"
        assert out[0]["_collection"] == "customer"

    def test_asset_success_passes_scope_as_asset_uid(self, mod, monkeypatch):
        seen = {}

        def fake_build(pg, tenant, ts, asset_uid=None):
            seen["asset_uid"] = asset_uid
            return [{"tenantId": tenant, "assetUid": asset_uid}]

        monkeypatch.setattr(mod.load_asset, "build_documents", fake_build)
        r, ctx, ts = _make_reaggregator(mod, "T|asset:A1", ["asset"])
        out = list(r.on_timer(0, ctx))
        assert seen["asset_uid"] == "A1"
        assert out[0]["_collection"] == "asset"

    def test_transient_failure_keeps_pending_and_arms_retry(self, mod, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("silver blip")

        monkeypatch.setattr(mod.load_project, "build_documents", boom)
        r, ctx, ts = _make_reaggregator(mod, "T|project:42", ["project"])
        out = list(r.on_timer(0, ctx))
        assert out == []
        assert r._fail["project"].value() == 1
        assert r._pending.value() == ["project"]
        assert len(ts.registered) == 1

    def test_one_failing_collection_does_not_block_a_healthy_one(self, mod, monkeypatch):
        # Grain-prefixed keys mean this specific mix (customer + asset on the
        # same key) only happens if both were triggered off the same
        # customer_asset event before the key diverges by grain -- exercised
        # here purely to confirm per-collection isolation in the reaggregator,
        # independent of how the router produces its keys.
        monkeypatch.setattr(
            mod.load_asset, "build_documents",
            lambda pg, tenant, ts, asset_uid=None: [{"tenantId": tenant, "assetUid": asset_uid}],
        )

        def boom(*a, **k):
            raise RuntimeError("blip")

        monkeypatch.setattr(mod.load_customer, "build_documents", boom)
        r, ctx, ts = _make_reaggregator(mod, "T|asset:A1", ["customer", "asset"])
        out = list(r.on_timer(0, ctx))
        assert [d["_collection"] for d in out] == ["asset"]
        assert r._pending.value() == ["customer"]
