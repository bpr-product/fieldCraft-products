"""Unit tests for gold.src.eden.load_tenant_dashboard (Silver → Gold).

tenant_dashboard is a tenant-grain loader: it uses fetch_tenant_rows +
_write_with_retry instead of iter_silver_rows + bulk_write_with_retry.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timezone

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

pytestmark = [pytest.mark.unit, pytest.mark.eden]

MOD = "gold.src.eden.load_tenant_dashboard"


@pytest.fixture
def mod():
    m = importlib.import_module(MOD)
    importlib.reload(m)
    m._shutdown = False
    return m


def _row(**over):
    base = {
        "tenantid": "DEMO-001",
        "project_count": 3,
        "at_risk_count": 1,
        "total_outstanding": 5000.0,
        "active_projects": [{"projectId": "1"}],
        "silver_lsn_max": 999,
        "project_silver_ts": SILVER_TS,
        "task_silver_ts": SILVER_TS,
        "schedule_silver_ts": SILVER_TS,
        "invoice_silver_ts": SILVER_TS,
    }
    base.update(over)
    return base


class TestFromArgs:
    def test_defaults(self, mod, monkeypatch):
        monkeypatch.delenv("TENANT_ID", raising=False)
        cfg = mod.LoaderConfig.from_args_and_env(["--tenant", "T1"])
        assert cfg.tenantid == "T1" and cfg.db_name == "gold_eden"
        assert cfg.batch_size == 200 and cfg.dry_run is False

    def test_flags_and_env(self, mod, monkeypatch):
        monkeypatch.setenv("TENANT_ID", "ENVT")
        cfg = mod.LoaderConfig.from_args_and_env(
            ["--dry-run", "--batch-size", "5", "--db", "gx", "--max-docs", "7"]
        )
        assert cfg.tenantid == "ENVT" and cfg.dry_run is True
        assert cfg.batch_size == 5 and cfg.db_name == "gx" and cfg.max_docs == 7


class TestMaskUri:
    def test_with_password_and_port(self, mod):
        masked = mod._mask_uri("postgresql://user:secret@host:5432/db")
        assert "secret" not in masked and "***" in masked and ":5432" in masked

    def test_with_password_no_port(self, mod):
        masked = mod._mask_uri("postgresql://user:secret@host/db")
        assert "secret" not in masked and "***" in masked

    def test_no_password_returned_asis(self, mod):
        assert mod._mask_uri("mongodb://host:27017") == "mongodb://host:27017"

    def test_exception_path_falls_back_to_regex(self, mod, monkeypatch):
        def boom(_):
            raise ValueError("bad uri")

        monkeypatch.setattr(mod, "urlparse", boom)
        masked = mod._mask_uri("postgresql://user:secret@host/db")
        assert "secret" not in masked and "***" in masked


class TestCoercion:
    def test_to_ts_none(self, mod):
        assert mod._to_ts(None) is None

    def test_to_ts_naive_gets_utc(self, mod):
        assert mod._to_ts(datetime(2026, 1, 1)).tzinfo is timezone.utc

    def test_to_ts_aware_passthrough(self, mod):
        aware = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert mod._to_ts(aware) is aware

    def test_to_ts_non_datetime(self, mod):
        assert mod._to_ts("x") is None

    def test_float_none(self, mod):
        assert mod._float(None) is None

    def test_float_parses(self, mod):
        assert mod._float("1.5") == 1.5 and mod._float(2) == 2.0

    def test_float_bad(self, mod):
        assert mod._float("nope") is None


class TestShapeDoc:
    def test_full_row(self, mod):
        doc = mod.shape_doc(_row(), GOLD_TS)
        assert doc["tenantId"] == "DEMO-001"
        assert doc["_grain"] == "eden_tenant_dashboard"
        assert doc["projectCount"] == 3
        assert doc["atRiskCount"] == 1
        assert doc["totalOutstanding"] == 5000.0
        assert doc["_silverLsnMax"] == 999
        # _shape_active_project pops the raw "customer" JSON and adds a parsed
        # "projectName" via a fallback chain: customer display name -> orderNo
        # -> projectId. The fixture row has no customer field and no orderNo,
        # so it falls all the way through to projectId ("1").
        assert doc["activeProjects"] == [{"projectId": "1", "projectName": "1"}]
        assert doc["lafieldcraft"] > 0

    def test_empty_active_projects(self, mod):
        doc = mod.shape_doc(_row(active_projects=None), GOLD_TS)
        assert doc["activeProjects"] == []

    def test_null_counts_and_lsn(self, mod):
        doc = mod.shape_doc(
            _row(project_count=None, at_risk_count=None, silver_lsn_max=None,
                 total_outstanding=None),
            GOLD_TS,
        )
        assert doc["projectCount"] == 0 and doc["atRiskCount"] == 0
        assert doc["_silverLsnMax"] == 0 and doc["totalOutstanding"] is None

    def test_no_watermarks_lag_zero(self, mod):
        doc = mod.shape_doc(
            _row(project_silver_ts=None, task_silver_ts=None,
                 schedule_silver_ts=None, invoice_silver_ts=None),
            GOLD_TS,
        )
        assert doc["lafieldcraft"] == 0


class TestFetchTenantRows:
    def test_returns_list_of_dicts(self, mod, patch_pg):
        conn, cur = patch_pg(mod)
        cur.fetchall = lambda: [_row()]
        out = mod.fetch_tenant_rows("dsn", "DEMO-001")
        assert isinstance(out, list) and len(out) == 1
        assert isinstance(out[0], dict) and out[0]["tenantid"] == "DEMO-001"
        conn.close.assert_called_once()

    def test_returns_empty_list_when_no_rows(self, mod, patch_pg):
        conn, cur = patch_pg(mod)
        cur.fetchall = lambda: []
        assert mod.fetch_tenant_rows("dsn", "DEMO-001") == []

    def test_build_documents_row(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "fetch_tenant_rows", lambda *a, **k: [_row()])
        docs = mod.build_documents("dsn", "DEMO-001", GOLD_TS)
        assert len(docs) == 1 and docs[0]["tenantId"] == "DEMO-001"

    def test_build_documents_no_row(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "fetch_tenant_rows", lambda *a, **k: [])
        assert mod.build_documents("dsn", "DEMO-001", GOLD_TS) == []

    def test_build_documents_multi_env_returns_multiple_docs(self, mod, monkeypatch):
        rows = [
            _row(source_env="si_dev"),
            _row(source_env="datamart"),
        ]
        monkeypatch.setattr(mod, "fetch_tenant_rows", lambda *a, **k: rows)
        docs = mod.build_documents("dsn", "DEMO-001", GOLD_TS)
        assert len(docs) == 2
        assert {d["sourceEnv"] for d in docs} == {"si_dev", "datamart"}
        for row, doc in zip(rows, docs):
            assert doc["sourceEnv"] == row["source_env"]


class TestSilverTsHelpers:
    def test_has_new_rows_true(self, mod, patch_pg):
        patch_pg(mod, fetchone=(True,))
        assert mod._has_new_silver_rows("dsn", "DEMO-001", EPOCH) is True

    def test_has_new_rows_false(self, mod, patch_pg):
        patch_pg(mod, fetchone=(False,))
        assert mod._has_new_silver_rows("dsn", "DEMO-001", EPOCH) is False

    def test_max_ts_naive_gets_utc(self, mod, patch_pg):
        patch_pg(mod, fetchone=(datetime(2026, 5, 1),))
        assert mod._get_max_silver_ts("dsn", "DEMO-001").tzinfo is timezone.utc

    def test_max_ts_none_is_epoch(self, mod, patch_pg):
        patch_pg(mod, fetchone=(None,))
        assert mod._get_max_silver_ts("dsn", "DEMO-001") == EPOCH

    def test_max_ts_aware_passthrough(self, mod, patch_pg):
        aware = datetime(2026, 5, 1, tzinfo=timezone.utc)
        patch_pg(mod, fetchone=(aware,))
        assert mod._get_max_silver_ts("dsn", "DEMO-001") == aware


class TestWriteWithRetry:
    def _doc(self):
        return {"tenantId": "DEMO-001", "_silverLsn": 5}

    def test_success(self, mod):
        coll = make_collection(make_bulk_result(upserted=1, modified=0))
        stats = mod.WriteStats()
        mod._write_with_retry(coll, self._doc(), make_cfg(mod), stats)
        assert stats.upserted == 1

    def test_bulk_write_error_counts_one(self, mod):
        coll = make_collection()
        coll.bulk_write.side_effect = BulkWriteError({"writeErrors": [{"e": 1}]})
        stats = mod.WriteStats()
        mod._write_with_retry(coll, self._doc(), make_cfg(mod), stats)
        assert stats.failed == 1

    def test_transient_then_success(self, mod):
        coll = make_collection()
        coll.bulk_write.side_effect = [AutoReconnect("x"), make_bulk_result(upserted=1)]
        stats = mod.WriteStats()
        mod._write_with_retry(coll, self._doc(), make_cfg(mod, max_retries=3), stats)
        assert stats.upserted == 1

    def test_transient_exhausted_raises(self, mod):
        coll = make_collection()
        coll.bulk_write.side_effect = AutoReconnect("down")
        with pytest.raises(AutoReconnect):
            mod._write_with_retry(
                coll, self._doc(), make_cfg(mod, max_retries=2), mod.WriteStats()
            )

    def test_operation_failure_raises(self, mod):
        coll = make_collection()
        coll.bulk_write.side_effect = OperationFailure("bad")
        with pytest.raises(OperationFailure):
            mod._write_with_retry(coll, self._doc(), make_cfg(mod), mod.WriteStats())


def _wire_run(mod, monkeypatch, row, *, has_new=True, max_ts=GOLD_TS):
    monkeypatch.setattr(mod, "read_cursor", lambda *a, **k: EPOCH)
    monkeypatch.setattr(mod, "write_cursor", lambda *a, **k: None)
    monkeypatch.setattr(mod, "mark_error", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_has_new_silver_rows", lambda *a, **k: has_new)
    monkeypatch.setattr(mod, "_get_max_silver_ts", lambda *a, **k: max_ts)
    monkeypatch.setattr(mod, "fetch_tenant_rows", lambda *a, **k: [row] if row else [])
    monkeypatch.setattr(
        mod, "_contract_validator",
        __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock(),
    )


class TestRun:
    def test_no_new_rows_returns_zero(self, mod, monkeypatch):
        _wire_run(mod, monkeypatch, _row(), has_new=False)
        assert mod.run(make_cfg(mod, dry_run=True)) == 0

    def test_no_active_projects_returns_zero(self, mod, monkeypatch):
        _wire_run(mod, monkeypatch, None)
        assert mod.run(make_cfg(mod, dry_run=True)) == 0

    def test_dry_run_success(self, mod, monkeypatch):
        _wire_run(mod, monkeypatch, _row())
        wrote = []
        monkeypatch.setattr(mod, "write_cursor", lambda *a, **k: wrote.append(a))
        assert mod.run(make_cfg(mod, dry_run=True)) == 0
        assert not wrote  # dry-run must NOT advance the cursor

    def test_wet_run_writes(self, mod, monkeypatch, patch_mongo):
        coll = patch_mongo(mod)
        _wire_run(mod, monkeypatch, _row())
        assert mod.run(make_cfg(mod, dry_run=False)) == 0
        coll.bulk_write.assert_called_once()

    def test_wet_run_multi_env_writes_one_doc_per_row(self, mod, monkeypatch, patch_mongo):
        coll = patch_mongo(mod)
        monkeypatch.setattr(mod, "read_cursor", lambda *a, **k: EPOCH)
        monkeypatch.setattr(mod, "write_cursor", lambda *a, **k: None)
        monkeypatch.setattr(mod, "mark_error", lambda *a, **k: None)
        monkeypatch.setattr(mod, "_has_new_silver_rows", lambda *a, **k: True)
        monkeypatch.setattr(mod, "_get_max_silver_ts", lambda *a, **k: GOLD_TS)
        rows = [_row(source_env="si_dev"), _row(source_env="datamart")]
        monkeypatch.setattr(mod, "fetch_tenant_rows", lambda *a, **k: rows)
        monkeypatch.setattr(
            mod, "_contract_validator",
            __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock(),
        )
        assert mod.run(make_cfg(mod, dry_run=False)) == 0
        assert coll.bulk_write.call_count == 2

    def test_transient_during_write_returns_3(self, mod, monkeypatch, patch_mongo):
        coll = make_collection()
        coll.bulk_write.side_effect = AutoReconnect("lost")
        patch_mongo(mod, coll=coll)
        _wire_run(mod, monkeypatch, _row())
        marks = []
        monkeypatch.setattr(mod, "mark_error", lambda *a, **k: marks.append(a))
        assert mod.run(make_cfg(mod, dry_run=False, max_retries=1)) == 3
        assert marks

    def test_bulk_failures_return_3(self, mod, monkeypatch, patch_mongo):
        coll = make_collection()
        coll.bulk_write.side_effect = BulkWriteError({"writeErrors": [{"e": 1}]})
        patch_mongo(mod, coll=coll)
        _wire_run(mod, monkeypatch, _row())
        marks = []
        monkeypatch.setattr(mod, "mark_error", lambda *a, **k: marks.append(a))
        assert mod.run(make_cfg(mod, dry_run=False)) == 3
        assert marks


class TestMainAndSignals:
    def test_main_success(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "_install_signal_handlers", lambda: None)
        monkeypatch.setattr(
            mod.LoaderConfig, "from_args_and_env", staticmethod(lambda argv=None: make_cfg(mod))
        )
        monkeypatch.setattr(mod, "run", lambda cfg: 0)
        assert mod.main([]) == 0

    def test_main_config_error_returns_2(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "_install_signal_handlers", lambda: None)

        def raise_cfg(argv=None):
            raise RuntimeError("missing tenant")

        monkeypatch.setattr(mod.LoaderConfig, "from_args_and_env", staticmethod(raise_cfg))
        assert mod.main([]) == 2

    def test_install_signal_handlers_and_handler(self, mod, monkeypatch):
        captured = {}
        monkeypatch.setattr(mod.signal, "signal", lambda sig, h: captured.__setitem__(sig, h))
        mod._install_signal_handlers()
        assert captured
        mod._shutdown = False
        list(captured.values())[0](2, None)
        assert mod._shutdown is True
        mod._shutdown = False
