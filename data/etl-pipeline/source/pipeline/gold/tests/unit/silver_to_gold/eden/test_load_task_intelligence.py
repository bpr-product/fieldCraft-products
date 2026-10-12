"""Unit tests for gold.src.eden.load_task_intelligence (Silver → Gold)."""

from __future__ import annotations

import importlib
import re
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

MOD = "gold.src.eden.load_task_intelligence"


@pytest.fixture
def mod():
    m = importlib.import_module(MOD)
    importlib.reload(m)
    m._cb = None
    m._shutdown = False
    return m


@pytest.fixture(autouse=True)
def _default_silver_columns(mod, monkeypatch):
    """Default get_silver_columns()/table_exists() to "everything present" so
    tests that don't care about the ADO PR 7368 schema-probe path
    (TestIterSilverRows, TestRun, etc.) don't hit a real Postgres connection
    via _scoped_query(). TestScopedQuery overrides these per-test where the
    probe result matters.
    """
    monkeypatch.setattr(mod, "get_silver_columns",
                         lambda pg_dsn, table: frozenset({"notes", "created_by"}))
    monkeypatch.setattr(mod, "table_exists", lambda pg_dsn, table: True)


class TestQueryIsEnvScoped:
    """Regression guard: every cross-table join in _SILVER_QUERY must be
    scoped by `<alias>._s_env = <anchor alias>._s_env`, not just tenantid.
    Silver is partitioned by _s_env and a tenantid is only unique within one
    env — an unscoped join could pull a sibling table's row from a DIFFERENT
    env sharing that tenantid/project_id (see CLAUDE.md's sourceEnv cross-env
    scoping section for the failure class this guards against)."""

    _ENV_JOIN_RE = re.compile(r"\b\w*\._s_env\s*=\s*\w*\._s_env\b")

    def test_query_selects_source_env(self, mod):
        assert "_s_env" in mod._SILVER_QUERY
        assert "AS source_env" in mod._SILVER_QUERY

    def test_every_join_scopes_by_matching_s_env(self, mod):
        matches = self._ENV_JOIN_RE.findall(mod._SILVER_QUERY)
        assert len(matches) > 1, (
            "expected multiple per-table <alias>._s_env = <alias>._s_env "
            "join guards in _SILVER_QUERY, found only %d: %r" % (len(matches), matches)
        )


def _row(**over):
    base = {
        "tenantid": "DEMO-001",
        "project_id": 42,
        "silver_lsn": 100,
        "project_silver_ts": SILVER_TS,
        "total_task_count": 10,
        "done_task_count": 5,
        "overdue_task_count": 2,
        "unassigned_task_count": 1,
        "overdue_tasks": [{"taskId": "1"}],
        "unassigned_tasks": [{"taskId": "2"}],
        "most_delayed_task_name": "Frame",
        "most_delayed_days": 7,
        "most_delayed_task_id": "T-9",
        "task_silver_ts": SILVER_TS,
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


class TestScopedQuery:
    def test_none_returns_unscoped(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "get_silver_columns", lambda pg_dsn, table: frozenset())
        q = mod._scoped_query("dsn", None)
        assert "AND p.id = %(project_id)s" not in q

    def test_scoped_adds_predicate(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "get_silver_columns", lambda pg_dsn, table: frozenset())
        unscoped = mod._scoped_query("dsn", None)
        q = mod._scoped_query("dsn", "P9")
        assert "AND p.id = %(project_id)s" in q and q != unscoped

    def test_created_by_null_when_column_absent(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "get_silver_columns", lambda pg_dsn, table: frozenset())
        q = mod._scoped_query("dsn", None)
        assert "__CREATED_BY_COL__" not in q
        assert "NULL                                  AS created_by," in q

    def test_created_by_uses_real_column_when_present(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "get_silver_columns", lambda pg_dsn, table: frozenset({"created_by"}))
        q = mod._scoped_query("dsn", None)
        assert "at.created_by                                  AS created_by," in q

    def test_invalid_column_absent_drops_filter_and_selects_null(self, mod, monkeypatch):
        # Referencing a genuinely absent column unconditionally would be a
        # hard SQL error, not a graceful null — both the SELECT and the WHERE
        # filter must degrade.
        monkeypatch.setattr(mod, "get_silver_columns", lambda pg_dsn, table: frozenset())
        q = mod._scoped_query("dsn", None)
        assert "__INVALID_COL__" not in q and "__INVALID_FILTER__" not in q
        assert "NULL                                      AS invalid," in q
        assert "at.invalid" not in q

    def test_invalid_column_present_keeps_filter_and_selects_real_column(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "get_silver_columns", lambda pg_dsn, table: frozenset({"invalid"}))
        q = mod._scoped_query("dsn", None)
        assert "at.invalid                                      AS invalid," in q
        # NULL treated as "not superseded", not excluded — live-verified on
        # tn24047750: invalid exists but is NULL on every row (36795/36795),
        # never TRUE/FALSE, so a plain `= FALSE` filter silently matched zero
        # tasks for that tenant despite tasks being present in Silver.
        assert "AND (at.invalid IS NULL OR at.invalid = FALSE)" in q

    def test_tae_join_inert_when_tables_absent(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "table_exists", lambda pg_dsn, table: False)
        q = mod._scoped_query("dsn", None)
        assert "__TAE_JOIN__" not in q and "__TP_JOIN__" not in q
        assert "FROM silver.task_attribute ta" not in q
        assert "FROM silver.task_payload tp" not in q
        assert "NULL::text  AS status" in q
        assert "NULL::text AS payload" in q

    def test_tae_join_real_when_tables_present(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "table_exists", lambda pg_dsn, table: True)
        q = mod._scoped_query("dsn", None)
        assert "FROM silver.task_attribute ta" in q
        assert "JOIN silver.attributes attrs" in q
        assert "FROM silver.task_payload tp" in q

    def test_tae_join_inert_when_only_one_table_present(self, mod, monkeypatch):
        # task_attribute present but attributes catalog missing — attribute_id
        # can't be resolved by key, so the join must stay inert either way.
        monkeypatch.setattr(
            mod, "table_exists",
            lambda pg_dsn, table: table == "task_attribute",
        )
        q = mod._scoped_query("dsn", None)
        assert "FROM silver.task_attribute ta" not in q
        assert "NULL::text  AS status" in q
        assert "at.created_by                                  AS created_by," in q


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


class TestShapeDoc:
    def test_full_row(self, mod):
        doc = mod.shape_doc(_row(), GOLD_TS)
        assert doc["_grain"] == "eden_task_intelligence"
        assert doc["projectId"] == "42"
        assert doc["completionPct"] == 50.0  # 5/10*100
        assert doc["mostDelayedTask"]["taskId"] == "T-9"
        assert doc["mostDelayedTask"]["daysLate"] == 7
        assert doc["lafieldcraft"] > 0

    def test_most_delayed_none(self, mod):
        doc = mod.shape_doc(_row(most_delayed_task_id=None), GOLD_TS)
        assert doc["mostDelayedTask"] is None

    def test_most_delayed_zero_days(self, mod):
        doc = mod.shape_doc(_row(most_delayed_days=0), GOLD_TS)
        assert doc["mostDelayedTask"]["daysLate"] == 0

    def test_completion_pct_total_zero(self, mod):
        doc = mod.shape_doc(_row(total_task_count=0, done_task_count=0), GOLD_TS)
        assert doc["completionPct"] is None

    def test_empty_task_lists_default(self, mod):
        doc = mod.shape_doc(_row(overdue_tasks=None, unassigned_tasks=None), GOLD_TS)
        assert doc["overdueTasks"] == [] and doc["unassignedTasks"] == []

    def test_no_watermarks_lag_zero(self, mod):
        doc = mod.shape_doc(_row(project_silver_ts=None, task_silver_ts=None), GOLD_TS)
        assert doc["lafieldcraft"] == 0

    def test_null_counts_default_zero(self, mod):
        doc = mod.shape_doc(
            _row(overdue_task_count=None, unassigned_task_count=None, silver_lsn=None),
            GOLD_TS,
        )
        assert doc["overdueTaskCount"] == 0 and doc["unassignedTaskCount"] == 0
        assert doc["_silverLsn"] == 0


class TestIterSilverRows:
    def test_yields_dicts_and_closes(self, mod, patch_pg):
        conn, cur = patch_pg(mod, rows=[_row(), _row(project_id=2)])
        out = list(mod.iter_silver_rows("dsn", "DEMO-001"))
        assert len(out) == 2
        conn.close.assert_called_once()

    def test_max_docs_limits(self, mod, patch_pg):
        patch_pg(mod, rows=[_row(), _row(project_id=2), _row(project_id=3)])
        assert len(list(mod.iter_silver_rows("dsn", "DEMO-001", max_docs=2))) == 2

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
        assert mod._get_max_silver_ts("dsn", "DEMO-001").tzinfo is timezone.utc

    def test_max_ts_none_is_epoch(self, mod, patch_pg):
        patch_pg(mod, fetchone=(None,))
        assert mod._get_max_silver_ts("dsn", "DEMO-001") == EPOCH

    def test_max_ts_aware_passthrough(self, mod, patch_pg):
        aware = datetime(2026, 5, 1, tzinfo=timezone.utc)
        patch_pg(mod, fetchone=(aware,))
        assert mod._get_max_silver_ts("dsn", "DEMO-001") == aware


class TestBulkWrite:
    def test_success(self, mod):
        coll = make_collection(make_bulk_result(upserted=2, modified=1))
        stats = mod.WriteStats()
        mod.bulk_write_with_retry(coll, [object()], make_cfg(mod), stats)
        assert stats.upserted == 2 and stats.modified == 1 and stats.batches == 1

    def test_bulk_write_error_counts_failures(self, mod):
        coll = make_collection()
        coll.bulk_write.side_effect = BulkWriteError(
            {"writeErrors": [{"e": 1}, {"e": 2}]}
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
        coll.bulk_write.side_effect = [AutoReconnect("x"), make_bulk_result(upserted=1)]
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
