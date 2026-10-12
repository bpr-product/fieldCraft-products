"""Unit tests for gold.src.eden.load_invoice_summary (Silver → Gold)."""

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

MOD = "gold.src.eden.load_invoice_summary"


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
        "project_id": 42,
        "silver_lsn": 100,
        "project_silver_ts": SILVER_TS,
        "total_billed": 1000.0,
        "total_outstanding": 300.0,
        "overdue_invoices": [{"invoiceNo": "I-1"}],
        "upcoming_invoices": [{"invoiceNo": "I-2"}],
        "invoice_silver_ts": SILVER_TS,
        "total_received": 700.0,
        "last_payment_amount": 250.0,
        "last_payment_date": datetime(2026, 5, 1, tzinfo=timezone.utc),
        "last_payment_method": "ACH",
        "payment_silver_ts": SILVER_TS,
    }
    base.update(over)
    return base


class TestFromArgs:
    def test_defaults(self, mod, monkeypatch):
        monkeypatch.delenv("TENANT_ID", raising=False)
        cfg = mod.LoaderConfig.from_args_and_env(["--tenant", "T1"])
        assert cfg.tenantid == "T1"
        assert cfg.db_name == "gold_eden"
        assert cfg.batch_size == 200
        assert cfg.dry_run is False

    def test_flags_and_env(self, mod, monkeypatch):
        monkeypatch.setenv("TENANT_ID", "ENVT")
        cfg = mod.LoaderConfig.from_args_and_env(
            ["--dry-run", "--batch-size", "5", "--db", "gx", "--max-docs", "7"]
        )
        assert cfg.tenantid == "ENVT"
        assert cfg.dry_run is True
        assert cfg.batch_size == 5
        assert cfg.db_name == "gx"
        assert cfg.max_docs == 7


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


class TestScopedQuery:
    def test_none_returns_base(self, mod):
        assert mod._scoped_query(None) == mod._SILVER_QUERY

    def test_scoped_adds_predicate(self, mod):
        q = mod._scoped_query("P9")
        assert "AND p.id = %(project_id)s" in q
        assert q != mod._SILVER_QUERY


class TestCoercion:
    def test_to_ts_none(self, mod):
        assert mod._to_ts(None) is None

    def test_to_ts_naive_gets_utc(self, mod):
        out = mod._to_ts(datetime(2026, 1, 1))
        assert out.tzinfo is timezone.utc

    def test_to_ts_aware_passthrough(self, mod):
        aware = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert mod._to_ts(aware) is aware

    def test_to_ts_non_datetime(self, mod):
        assert mod._to_ts("2026-01-01") is None

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
        assert doc["projectId"] == "42"
        assert doc["_grain"] == "eden_invoice_summary"
        assert doc["_schemaVersion"] == 1
        assert doc["totalBilled"] == 1000.0
        assert doc["lastPayment"]["amount"] == 250.0
        assert doc["lastPayment"]["payMethod"] == "ACH"
        assert doc["lastPayment"]["receivedDate"] is not None
        assert doc["lafieldcraft"] > 0

    def test_last_payment_none(self, mod):
        doc = mod.shape_doc(_row(last_payment_amount=None), GOLD_TS)
        assert doc["lastPayment"] is None

    def test_last_payment_no_date(self, mod):
        doc = mod.shape_doc(_row(last_payment_date=None), GOLD_TS)
        assert doc["lastPayment"]["receivedDate"] is None

    def test_empty_invoice_lists_default(self, mod):
        doc = mod.shape_doc(_row(overdue_invoices=None, upcoming_invoices=None), GOLD_TS)
        assert doc["overdueInvoices"] == [] and doc["upcomingInvoices"] == []

    def test_no_watermarks_lag_zero(self, mod):
        doc = mod.shape_doc(
            _row(project_silver_ts=None, invoice_silver_ts=None, payment_silver_ts=None),
            GOLD_TS,
        )
        assert doc["lafieldcraft"] == 0

    def test_null_silver_lsn(self, mod):
        doc = mod.shape_doc(_row(silver_lsn=None), GOLD_TS)
        assert doc["_silverLsn"] == 0


class TestIterSilverRows:
    def test_yields_dicts_and_closes(self, mod, patch_pg):
        conn, cur = patch_pg(mod, rows=[_row(), _row(project_id=2)])
        out = list(mod.iter_silver_rows("dsn", "DEMO-001"))
        assert len(out) == 2 and all(isinstance(r, dict) for r in out)
        conn.close.assert_called_once()

    def test_max_docs_limits(self, mod, patch_pg):
        patch_pg(mod, rows=[_row(), _row(project_id=2), _row(project_id=3)])
        out = list(mod.iter_silver_rows("dsn", "DEMO-001", max_docs=2))
        assert len(out) == 2

    def test_project_id_param(self, mod, patch_pg):
        conn, cur = patch_pg(mod, rows=[_row()])
        list(mod.iter_silver_rows("dsn", "DEMO-001", project_id="P9"))
        args, _ = cur.execute.call_args
        assert "AND p.id = %(project_id)s" in args[0]
        assert args[1]["project_id"] == "P9"

    def test_build_documents(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "iter_silver_rows", lambda *a, **k: iter([_row()]))
        docs = mod.build_documents("dsn", "DEMO-001", GOLD_TS)
        assert len(docs) == 1 and docs[0]["projectId"] == "42"


class TestSilverTsHelpers:
    def test_has_new_rows_true(self, mod, patch_pg):
        patch_pg(mod, fetchone=(True,))
        assert mod._has_new_silver_rows("dsn", "DEMO-001", EPOCH) is True

    def test_has_new_rows_false(self, mod, patch_pg):
        patch_pg(mod, fetchone=(False,))
        assert mod._has_new_silver_rows("dsn", "DEMO-001", EPOCH) is False

    def test_max_ts_naive_gets_utc(self, mod, patch_pg):
        patch_pg(mod, fetchone=(datetime(2026, 5, 1),))
        out = mod._get_max_silver_ts("dsn", "DEMO-001")
        assert out.tzinfo is timezone.utc

    def test_max_ts_none_is_epoch(self, mod, patch_pg):
        patch_pg(mod, fetchone=(None,))
        assert mod._get_max_silver_ts("dsn", "DEMO-001") == EPOCH

    def test_max_ts_aware_passthrough(self, mod, patch_pg):
        aware = datetime(2026, 5, 1, tzinfo=timezone.utc)
        patch_pg(mod, fetchone=(aware,))
        assert mod._get_max_silver_ts("dsn", "DEMO-001") == aware


class TestBulkWrite:
    def test_success(self, mod):
        coll = make_collection(make_bulk_result(upserted=2, modified=1, matched=1))
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
        coll.bulk_write.side_effect = BulkWriteError({})
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


def _wire_run(mod, monkeypatch, rows, *, has_new=True, max_ts=GOLD_TS):
    monkeypatch.setattr(mod, "read_cursor", lambda *a, **k: EPOCH)
    monkeypatch.setattr(mod, "write_cursor", lambda *a, **k: None)
    monkeypatch.setattr(mod, "mark_error", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_has_new_silver_rows", lambda *a, **k: has_new)
    monkeypatch.setattr(mod, "_get_max_silver_ts", lambda *a, **k: max_ts)
    monkeypatch.setattr(mod, "iter_silver_rows", lambda *a, **k: iter(rows))
    monkeypatch.setattr(
        mod, "_contract_validator",
        __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock(),
    )


class TestRun:
    def test_no_new_rows_returns_zero(self, mod, monkeypatch):
        _wire_run(mod, monkeypatch, [], has_new=False)
        assert mod.run(make_cfg(mod, dry_run=True)) == 0

    def test_dry_run_success(self, mod, monkeypatch):
        _wire_run(mod, monkeypatch, [_row(), _row(project_id=2)])
        wrote = []
        monkeypatch.setattr(mod, "write_cursor", lambda *a, **k: wrote.append(a))
        assert mod.run(make_cfg(mod, dry_run=True, batch_size=1)) == 0
        assert not wrote  # dry-run must NOT advance the cursor

    def test_wet_run_writes(self, mod, monkeypatch, patch_mongo):
        coll = patch_mongo(mod)
        _wire_run(mod, monkeypatch, [_row()])
        assert mod.run(make_cfg(mod, dry_run=False)) == 0
        coll.bulk_write.assert_called_once()

    def test_transient_during_iter_returns_3(self, mod, monkeypatch):
        _wire_run(mod, monkeypatch, [])

        def boom(*a, **k):
            raise AutoReconnect("lost")

        monkeypatch.setattr(mod, "iter_silver_rows", boom)
        marks = []
        monkeypatch.setattr(mod, "mark_error", lambda *a, **k: marks.append(a))
        assert mod.run(make_cfg(mod, dry_run=True)) == 3
        assert marks

    def test_bulk_failures_return_3(self, mod, monkeypatch, patch_mongo):
        coll = make_collection()
        coll.bulk_write.side_effect = BulkWriteError({"writeErrors": [{"e": 1}]})
        patch_mongo(mod, coll=coll)
        _wire_run(mod, monkeypatch, [_row()])
        marks = []
        monkeypatch.setattr(mod, "mark_error", lambda *a, **k: marks.append(a))
        assert mod.run(make_cfg(mod, dry_run=False)) == 3
        assert marks

    def test_shutdown_breaks_loop(self, mod, monkeypatch):
        _wire_run(mod, monkeypatch, [_row(), _row(project_id=2)])
        mod._shutdown = True
        try:
            assert mod.run(make_cfg(mod, dry_run=True)) == 0
        finally:
            mod._shutdown = False


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

        monkeypatch.setattr(
            mod.LoaderConfig, "from_args_and_env", staticmethod(raise_cfg)
        )
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
