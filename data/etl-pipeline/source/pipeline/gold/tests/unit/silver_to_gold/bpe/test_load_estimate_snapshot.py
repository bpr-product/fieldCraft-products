"""Unit tests for gold.src.bpe.load_estimate_snapshot (Silver → Gold feature_store).

Pure unit tests — psycopg2 / pymongo / cursor_store mocked.
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from pymongo.errors import AutoReconnect, BulkWriteError, OperationFailure

from gold.tests.unit.silver_to_gold.conftest import (
    EPOCH,
    GOLD_TS,
    SILVER_TS,
    make_bulk_result,
    make_cfg,
    make_collection,
)

pytestmark = [pytest.mark.unit, pytest.mark.bpe]

MOD = "gold.src.bpe.load_estimate_snapshot"


@pytest.fixture
def mod():
    m = importlib.import_module(MOD)
    importlib.reload(m)
    m._cb = None
    m._shutdown = False
    return m


def _row(**over):
    base = {
        "tenantid": "DEMO-001",
        "silver_ts": SILVER_TS,
        "silver_lsn": 100,
        "project_id": 42,
        "trade_oid": "TR-1",
        "project_type": "NEW",
        "zipcode": "75001",
        "location": "TX",
        "q_template_version": 3,
        "area": 1500,
        "material_cost": 400.0,
        "worker_cost": 300.0,
        "tool_cost": 100.0,
        "overhead_cost": 50.0,
        "margin": 150.0,
        "total_cost": 1000.0,
        "bom_items": [{"id": 1, "total_cost": 800.0}],
        "actual_material_spend": 200.0,
        "actual_labor_spend": 100.0,
        "answers_data": [{"itemOid": "o1", "answer": "yes"}],
        "service_count": 0,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# LoaderConfig.from_args_and_env
# ---------------------------------------------------------------------------


class TestQueryIsEnvScoped:
    """Regression guard: every cross-table join in _SILVER_QUERY must be
    scoped by `_s_env = p._s_env` (matching the anchor project row's own
    env), not just `_s_tenantid`. Silver is partitioned by _s_env and a
    tenantid is only unique within one env — an unscoped join could pull a
    sibling table's row from a DIFFERENT env sharing that tenantid/project_id,
    the same cross-env collision class fixed elsewhere in this pipeline
    (see CLAUDE.md's sourceEnv cross-env scoping section)."""

    def test_query_selects_source_env(self, mod):
        assert "_s_env" in mod._SILVER_QUERY
        assert "AS source_env" in mod._SILVER_QUERY

    def test_every_join_scopes_by_p_s_env(self, mod):
        # Each subordinate table's correlated join/subquery must repeat
        # "_s_env = p._s_env" (or "x._s_env = p._s_env") alongside the
        # tenantid/project_id predicate — count should be >1 (one per joined
        # table, not just the top-level SELECT's own column).
        occurrences = mod._SILVER_QUERY.count("_s_env = p._s_env")
        assert occurrences > 1, (
            "expected multiple per-table _s_env = p._s_env join guards in "
            "_SILVER_QUERY, found only %d" % occurrences
        )


class TestFromArgs:
    def test_defaults(self, mod, monkeypatch):
        monkeypatch.delenv("TENANT_ID", raising=False)
        monkeypatch.setattr(mod, "get_mongo_uri", lambda: "mongodb://m")
        monkeypatch.setattr(mod, "get_postgres_dsn", lambda: "postgresql://p")
        cfg = mod.LoaderConfig.from_args_and_env(["--tenant", "T1"])
        assert cfg.tenantid == "T1"
        assert cfg.db_name == "gold_bpe"
        assert cfg.batch_size == 200
        assert cfg.dry_run is False
        assert cfg.mongo_uri == "mongodb://m"
        assert cfg.rebuild is False

    def test_rebuild_flag(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "get_mongo_uri", lambda: "mongodb://m")
        monkeypatch.setattr(mod, "get_postgres_dsn", lambda: "postgresql://p")
        cfg = mod.LoaderConfig.from_args_and_env(["--tenant", "T1", "--rebuild"])
        assert cfg.rebuild is True

    def test_flags_and_env(self, mod, monkeypatch):
        monkeypatch.setenv("TENANT_ID", "ENVT")
        cfg = mod.LoaderConfig.from_args_and_env(
            [
                "--dry-run",
                "--batch-size",
                "5",
                "--db",
                "gx",
                "--max-docs",
                "7",
                "--max-retries",
                "2",
                "--retry-backoff",
                "0.0",
                "--mongo-uri",
                "mongodb://x",
                "--pg-dsn",
                "postgresql://y",
            ]
        )
        assert cfg.tenantid == "ENVT"
        assert cfg.dry_run is True
        assert cfg.batch_size == 5
        assert cfg.db_name == "gx"
        assert cfg.max_docs == 7
        assert cfg.max_retries == 2


# _mask_uri is intentionally absent from load_estimate_snapshot: its startup
# banner (line ~464) logs db/tenant/dry_run but never the URI, so no masking is
# needed here. The canonical _mask_uri (used by load_estimate_summary, which
# does log the URI) is covered in test_helpers.py / silver_to_gold summary tests.


# ---------------------------------------------------------------------------
# coercion helpers
# ---------------------------------------------------------------------------


class TestCoercion:
    def test_float_none(self, mod):
        assert mod._float_or_none(None) is None

    def test_float_parses(self, mod):
        assert mod._float_or_none("1.5") == 1.5

    def test_float_bad(self, mod):
        assert mod._float_or_none("x") is None

    def test_int_none(self, mod):
        assert mod._int_or_none(None) is None

    def test_int_parses(self, mod):
        assert mod._int_or_none("4") == 4

    def test_int_bad(self, mod):
        assert mod._int_or_none("x") is None


# ---------------------------------------------------------------------------
# _build_features_dict
# ---------------------------------------------------------------------------


class TestBuildFeaturesDict:
    def test_empty_none(self, mod):
        assert mod._build_features_dict(None) == {}

    def test_empty_list(self, mod):
        assert mod._build_features_dict([]) == {}

    def test_json_string_list(self, mod):
        raw = json.dumps([{"itemOid": "o1", "answer": "yes"}])
        assert mod._build_features_dict(raw) == {"o1": "yes"}

    def test_bad_json_string(self, mod):
        assert mod._build_features_dict("{bad") == {}

    def test_list_of_dicts_itemOid_and_id_and_value(self, mod):
        data = [
            {"itemOid": "o1", "answer": "a"},
            {"id": "o2", "value": "v"},
            {"nope": "skip"},
            "notadict",
        ]
        out = mod._build_features_dict(data)
        assert out == {"o1": "a", "o2": "v"}

    def test_dict_passthrough(self, mod):
        d = {"k": 1}
        assert mod._build_features_dict(d) is d

    def test_non_list_non_dict_returns_empty(self, mod):
        assert mod._build_features_dict(42) == {}


# ---------------------------------------------------------------------------
# _build_data_quality
# ---------------------------------------------------------------------------


class TestBuildDataQuality:
    def test_no_bom_empty_list(self, mod):
        assert mod._build_data_quality(_row(bom_items=[])) == "estimate_fallback"

    def test_no_bom_empty_string(self, mod):
        assert mod._build_data_quality(_row(bom_items="[]")) == "estimate_fallback"

    def test_no_bom_none(self, mod):
        assert mod._build_data_quality(_row(bom_items=None)) == "estimate_fallback"

    def test_full_single_service(self, mod):
        assert mod._build_data_quality(_row(service_count=1)) == "full"

    def test_material_split_multi_service(self, mod):
        assert mod._build_data_quality(_row(service_count=3)) == "material_split"


# ---------------------------------------------------------------------------
# iter_silver_rows
# ---------------------------------------------------------------------------


class TestIterSilverRows:
    def test_yields_and_closes(self, mod, patch_pg):
        conn, cur = patch_pg(mod, rows=[_row(), _row(project_id=2)])
        out = list(mod.iter_silver_rows("dsn", "DEMO-001"))
        assert len(out) == 2 and all(isinstance(r, dict) for r in out)
        conn.close.assert_called_once()

    def test_max_docs_limits(self, mod, patch_pg):
        patch_pg(mod, rows=[_row(), _row(project_id=2), _row(project_id=3)])
        out = list(mod.iter_silver_rows("dsn", "DEMO-001", max_docs=2))
        assert len(out) == 2


# ---------------------------------------------------------------------------
# shape_doc
# ---------------------------------------------------------------------------


class TestShapeDoc:
    def test_full_doc(self, mod):
        doc = mod.shape_doc(_row(), GOLD_TS)
        # project_id is int to match the canonical Synthesized_Data.bpe schema
        assert doc["project_id"] == 42
        assert doc["tenantId"] == "DEMO-001"
        assert doc["service"] == "NEW"
        assert doc["area"] == 1500
        assert doc["n_services"] == 0
        assert doc["is_single_service"] is True
        assert doc["features"] == {"o1": "yes"}
        assert doc["total_cost"] == 1000.0
        assert doc["bom_items"] == [{"id": 1, "total_cost": 800.0}]
        assert doc["data_quality"] == "full"  # has bom, service_count 0 → <=1
        assert doc["_grain"] == "bpe_feature_store"
        assert doc["_schemaVersion"] == 1
        assert doc["lafieldcraft"] > 0
        assert doc["refreshed_at"] == GOLD_TS
        # source_flags is now the 6 provenance booleans (from the has_* query
        # columns); the minimal mock row sets none, so all resolve False.
        assert doc["source_flags"] == {
            "has_bom": False,
            "has_resource_estimate": False,
            "has_project_estimate": False,
            "has_answers": False,
            "has_tasks": False,
            "has_payment_expense": False,
        }

    def test_not_deleted_by_default(self, mod):
        doc = mod.shape_doc(_row(), GOLD_TS)
        assert doc["isDeleted"] is False
        assert doc["deletedAt"] is None

    def test_soft_deleted_project_is_tombstoned(self, mod):
        # A soft-deleted Silver project (op=u, _s_is_deleted=TRUE) must mark the
        # feature_store doc isDeleted=true rather than look live (the project query
        # already includes deleted rows).
        doc = mod.shape_doc(
            _row(is_deleted=True, deleted_at="2026-06-11 10:00:00+00"), GOLD_TS
        )
        assert doc["isDeleted"] is True
        assert doc["deletedAt"] == datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
        assert doc["project_id"] == 42  # still upserted by (tenantId, project_id)

    def test_bom_items_json_string_parsed(self, mod):
        doc = mod.shape_doc(_row(bom_items=json.dumps([{"id": 9}])), GOLD_TS)
        assert doc["bom_items"] == [{"id": 9}]

    def test_bom_items_bad_json_string(self, mod):
        doc = mod.shape_doc(_row(bom_items="{bad"), GOLD_TS)
        assert doc["bom_items"] == []

    def test_no_silver_ts_lag_zero(self, mod):
        doc = mod.shape_doc(_row(silver_ts=None), GOLD_TS)
        assert doc["lafieldcraft"] == 0

    def test_multi_service_not_single(self, mod):
        doc = mod.shape_doc(_row(service_count=2), GOLD_TS)
        assert doc["is_single_service"] is False
        assert doc["n_services"] == 2

    def test_missing_tenantid_empty(self, mod):
        doc = mod.shape_doc(_row(tenantid=None), GOLD_TS)
        assert doc["tenantId"] == ""


# ---------------------------------------------------------------------------
# bulk_write_with_retry
# ---------------------------------------------------------------------------


class TestBulkWrite:
    def test_success(self, mod):
        coll = make_collection(make_bulk_result(upserted=2, modified=1))
        stats = mod.WriteStats()
        mod.bulk_write_with_retry(coll, [object()], make_cfg(mod), stats)
        assert stats.upserted == 2 and stats.modified == 1 and stats.batches == 1

    def test_bulk_write_error_counts_failures(self, mod):
        coll = make_collection()
        coll.bulk_write.side_effect = BulkWriteError(
            {"writeErrors": [{"errmsg": "dup"}, {"errmsg": "dup2"}]}
        )
        stats = mod.WriteStats()
        mod.bulk_write_with_retry(coll, [object()], make_cfg(mod), stats)
        assert stats.failed == 2 and stats.batches == 1

    def test_bulk_write_error_no_details(self, mod):
        coll = make_collection()
        coll.bulk_write.side_effect = BulkWriteError(None)
        stats = mod.WriteStats()
        mod.bulk_write_with_retry(coll, [object()], make_cfg(mod), stats)
        assert stats.failed == 0 and stats.batches == 1

    def test_transient_then_success(self, mod):
        coll = make_collection()
        coll.bulk_write.side_effect = [AutoReconnect("boom"), make_bulk_result(upserted=1)]
        stats = mod.WriteStats()
        mod.bulk_write_with_retry(coll, [object()], make_cfg(mod, max_retries=3), stats)
        assert stats.upserted == 1

    def test_transient_exhausted_raises(self, mod):
        coll = make_collection()
        coll.bulk_write.side_effect = AutoReconnect("down")
        with pytest.raises(AutoReconnect):
            mod.bulk_write_with_retry(
                coll, [object()], make_cfg(mod, max_retries=2), mod.WriteStats()
            )

    def test_operation_failure_raises(self, mod):
        coll = make_collection()
        coll.bulk_write.side_effect = OperationFailure("bad")
        with pytest.raises(OperationFailure):
            mod.bulk_write_with_retry(coll, [object()], make_cfg(mod), mod.WriteStats())


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _wire_run(mod, monkeypatch, rows):
    monkeypatch.setattr(mod, "read_cursor", lambda *a, **k: EPOCH)
    monkeypatch.setattr(mod, "write_cursor", lambda *a, **k: None)
    monkeypatch.setattr(mod, "mark_error", lambda *a, **k: None)
    monkeypatch.setattr(mod, "iter_silver_rows", lambda *a, **k: iter(rows))
    # run() computes max_silver_ts via a real per-env Postgres query
    # (get_max_silver_ts_per_env) rather than tracking it inline over the
    # already-fetched rows -- must be mocked to keep this a pure unit test
    # (no live DB connection), matching the module docstring's contract.
    monkeypatch.setattr(mod, "get_max_silver_ts_per_env", lambda *a, **k: SILVER_TS)


class TestRun:
    def test_dry_run_does_not_advance_cursor(self, mod, monkeypatch):
        # dry-run must be side-effect-free: it must NOT advance the Silver cursor,
        # otherwise a subsequent live run would skip all rows.
        _wire_run(mod, monkeypatch, [_row(), _row(project_id=2)])
        wrote = []
        monkeypatch.setattr(mod, "write_cursor", lambda *a, **k: wrote.append(a))
        assert mod.run(make_cfg(mod, dry_run=True, batch_size=1)) == 0
        assert not wrote

    def test_wet_run_writes(self, mod, monkeypatch, patch_mongo):
        coll = patch_mongo(mod)
        _wire_run(mod, monkeypatch, [_row()])
        assert mod.run(make_cfg(mod, dry_run=False)) == 0
        coll.bulk_write.assert_called_once()

    def test_wet_run_bulk_failures_return_3(self, mod, monkeypatch, patch_mongo):
        coll = make_collection()
        coll.bulk_write.side_effect = BulkWriteError({"writeErrors": [{"e": 1}]})
        patch_mongo(mod, coll=coll)
        _wire_run(mod, monkeypatch, [_row()])
        marks = []
        monkeypatch.setattr(mod, "mark_error", lambda *a, **k: marks.append(a))
        assert mod.run(make_cfg(mod, dry_run=False)) == 3
        assert marks

    def test_transient_during_iter_returns_3(self, mod, monkeypatch):
        _wire_run(mod, monkeypatch, [])

        def boom(*a, **k):
            raise AutoReconnect("lost")

        monkeypatch.setattr(mod, "iter_silver_rows", boom)
        marks = []
        monkeypatch.setattr(mod, "mark_error", lambda *a, **k: marks.append(a))
        assert mod.run(make_cfg(mod, dry_run=True)) == 3
        assert marks

    def test_shutdown_breaks_loop(self, mod, monkeypatch):
        _wire_run(mod, monkeypatch, [_row(), _row(project_id=2)])
        mod._shutdown = True
        try:
            assert mod.run(make_cfg(mod, dry_run=True)) == 0
        finally:
            mod._shutdown = False


# ---------------------------------------------------------------------------
# main + signal handlers
# ---------------------------------------------------------------------------


class TestMainAndSignals:
    def test_main_success(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "_install_signal_handlers", lambda: None)
        monkeypatch.setattr(
            mod.LoaderConfig,
            "from_args_and_env",
            staticmethod(lambda argv=None: make_cfg(mod)),
        )
        monkeypatch.setattr(mod, "run", lambda cfg: 0)
        assert mod.main([]) == 0

    def test_main_config_error_returns_2(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "_install_signal_handlers", lambda: None)

        def raise_cfg(argv=None):
            raise RuntimeError("missing tenant")

        monkeypatch.setattr(
            mod.LoaderConfig, "from_args_and_env", staticmethod(raise_cfg)
        )
        assert mod.main([]) == 2

    def test_install_signal_handlers_and_handler(self, mod, monkeypatch):
        captured = {}

        def fake_signal(sig, handler):
            captured[sig] = handler

        monkeypatch.setattr(mod.signal, "signal", fake_signal)
        mod._install_signal_handlers()
        assert captured
        mod._shutdown = False
        list(captured.values())[0](2, None)
        assert mod._shutdown is True
        mod._shutdown = False
