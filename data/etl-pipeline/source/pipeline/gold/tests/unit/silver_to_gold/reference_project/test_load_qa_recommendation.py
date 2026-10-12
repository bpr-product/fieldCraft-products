"""Unit tests for gold.src.reference_project.load_qa_recommendation (Silver → Gold).

Pure unit tests — all I/O (psycopg2, pymongo, cursor_store) is mocked via the
shared conftest fixtures. Mirrors the suite testing pattern established by
gold/tests/unit/silver_to_gold/eden/test_load_project_health.py.
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

pytestmark = [pytest.mark.unit, pytest.mark.reference_project]

MOD = "gold.src.reference_project.load_qa_recommendation"


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
        "is_deleted": False,
        "silver_lsn": 100,
        "silver_ts": SILVER_TS,
        "answer_id": 42,
        "answer_uuid": "AUID-1",
        "project_id": 7,
        "question_template_version": "v3",
        "schema_version": "1.0",
        "status": "SUBMITTED",
        "trade_oid": "T-OID",
        "version": 2,
        "created_by": "user1",
        "created_date": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_date": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "invalid": False,
        "answers_data": None,
        "project_uid": "PUID-1",
        "project_status": "ACTIVE",
        "trade": "ROOFING",
        "location": "TX",
        "order_no": "ORD-9",
    }
    base.update(over)
    return base


def _bom_line(**over):
    base = {
        "bom_id": 1,
        "project_id": 7,
        "task_id": 100,
        "material_name": "Owens Corning Shingles",
        "material_uid": "mat-uid-1",
        "cost_code": "MAT-001",
        "quantity": 10.0,
        "unit": "Each",
        "unit_cost": 25.50,
        "total_cost": 255.0,
        "over_head": 15,
        "profit_margin": 15,
        "tax": [{"percentage": "0", "name": "Tax", "value": ""}],
        "active": True,
        "bom_invalid": False,
        "bom_version": 1,
        "source_type_id": 6,
        "vendor_material_map_id": 22,
        "bom_created_at": "2026-01-01 09:00:00+00",
        "bom_updated_at": "2026-01-02 09:00:00+00",
        "bom_lsn": 50,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# LoaderConfig.from_args_and_env
# ---------------------------------------------------------------------------


class TestFromArgs:
    def test_defaults(self, mod, monkeypatch):
        monkeypatch.delenv("TENANT_ID", raising=False)
        monkeypatch.delenv("GOLD_QA_RECOMMENDATION_DB", raising=False)
        monkeypatch.delenv("BATCH_SIZE", raising=False)
        monkeypatch.setattr(mod, "get_mongo_uri", lambda: "mongodb://m")
        monkeypatch.setattr(mod, "get_postgres_dsn", lambda: "postgresql://p")
        cfg = mod.LoaderConfig.from_args_and_env(["--tenant", "T1"])
        assert cfg.tenantid == "T1"
        assert cfg.db_name == "gold_qa_recommendation"
        assert cfg.batch_size == 200
        assert cfg.dry_run is False
        assert cfg.max_docs == 0
        assert cfg.mongo_uri == "mongodb://m"
        assert cfg.pg_dsn == "postgresql://p"
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
                "--mongo-uri",
                "mongodb://x",
                "--pg-dsn",
                "postgresql://y",
                "--max-retries",
                "9",
                "--retry-backoff",
                "0.0",
            ]
        )
        assert cfg.tenantid == "ENVT"
        assert cfg.dry_run is True
        assert cfg.batch_size == 5
        assert cfg.db_name == "gx"
        assert cfg.max_docs == 7
        assert cfg.max_retries == 9


# ---------------------------------------------------------------------------
# _mask_uri
# ---------------------------------------------------------------------------


class TestMaskUri:
    def test_with_password_and_port(self, mod):
        masked = mod._mask_uri("postgresql://user:secret@host:5432/db")
        assert "secret" not in masked
        assert "***" in masked
        assert ":5432" in masked

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


# ---------------------------------------------------------------------------
# _answers_data_object
# ---------------------------------------------------------------------------


class TestAnswersDataObject:
    def test_none(self, mod):
        assert mod._answers_data_object(None) is None

    def test_empty_or_blank_string(self, mod):
        assert mod._answers_data_object("") is None
        assert mod._answers_data_object("   ") is None

    def test_json_object_string(self, mod):
        assert mod._answers_data_object('{"x": 1}') == {"x": 1}

    def test_json_array_string(self, mod):
        assert mod._answers_data_object("[1, 2, 3]") == [1, 2, 3]

    def test_already_object_passthrough(self, mod):
        assert mod._answers_data_object({"x": 1}) == {"x": 1}
        assert mod._answers_data_object([1, 2]) == [1, 2]

    def test_invalid_json_returns_none(self, mod):
        assert mod._answers_data_object("{not json") is None


# ---------------------------------------------------------------------------
# shape_doc
# ---------------------------------------------------------------------------


class TestShapeDoc:
    def test_missing_answer_id_returns_none(self, mod):
        assert mod.shape_doc(_row(answer_id=None), GOLD_TS) is None

    def test_raw_passthrough_fields(self, mod):
        doc = mod.shape_doc(_row(), GOLD_TS)
        # raw silver.answers fields kept verbatim (original snake_case names/values)
        assert doc["answer_id"] == 42
        assert doc["project_id"] == 7
        assert doc["status"] == "SUBMITTED"
        assert doc["trade_oid"] == "T-OID"
        assert doc["version"] == 2
        assert doc["question_template_version"] == "v3"
        assert doc["schema_version"] == "1.0"
        assert doc["created_by"] == "user1"
        # no enriched / camelCase fields leak through
        for stale in ("answerId", "answersData", "projectId", "lafieldcraft",
                      "_silverWatermarks", "projectStatus"):
            assert stale not in doc

    def test_platform_plumbing_present(self, mod):
        doc = mod.shape_doc(_row(), GOLD_TS)
        assert doc["tenantId"] == "DEMO-001"
        assert doc["_grain"] == "qa_answer"
        assert doc["_schemaVersion"] == 3
        assert doc["_silverLsn"] == 100
        # goldUpdatedAt is the sole gold-side write-time stamp (was _etlUpdatedAt
        # until 2026-07-12 — see gold/pipeline/lineage.py's "Consolidation" note).
        assert doc["goldUpdatedAt"] == GOLD_TS

    def test_lineage_block_present(self, mod):
        # qa_answer must carry the SAME lineage block as every other Gold
        # collection (governance/traceability consistency). featureName and
        # processingTimestamp were dropped 2026-07-12 (no functional reader /
        # pure duplicate of goldUpdatedAt) — no longer part of the block.
        doc = mod.shape_doc(_row(), GOLD_TS)
        for f in ("dataOrigin", "goldUpdatedAt", "sourceUpdatedAt"):
            assert f in doc, f"missing lineage field {f}"
        assert doc["sourceUpdatedAt"] == SILVER_TS   # = Silver _s_silver_ts

    def test_not_deleted_by_default(self, mod):
        doc = mod.shape_doc(_row(), GOLD_TS)
        assert doc["isDeleted"] is False
        assert doc["deletedAt"] is None

    def test_soft_deleted_row_is_tombstoned(self, mod):
        # A soft-deleted Silver row (op=u, _s_is_deleted=TRUE) must mark the Gold
        # doc isDeleted=true rather than be excluded (which would orphan it).
        doc = mod.shape_doc(
            _row(is_deleted=True, deleted_at="2026-06-11 10:00:00+00"), GOLD_TS
        )
        assert doc["isDeleted"] is True
        assert doc["deletedAt"] == datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
        # still a normal upsert by (tenantId, answer_id) — no orphan
        assert doc["answer_id"] == 42

    def test_project_id_none(self, mod):
        doc = mod.shape_doc(_row(project_id=None), GOLD_TS)
        assert doc["project_id"] is None

    def test_silver_lsn_zero_when_missing(self, mod):
        doc = mod.shape_doc(_row(silver_lsn=None), GOLD_TS)
        assert doc["_silverLsn"] == 0

    def test_answers_data_parsed_to_object(self, mod):
        doc = mod.shape_doc(_row(answers_data='{"a": 1, "b": [2, 3]}'), GOLD_TS)
        assert doc["answers_data"] == {"a": 1, "b": [2, 3]}

    def test_answers_data_none_stays_none(self, mod):
        assert mod.shape_doc(_row(answers_data=None), GOLD_TS)["answers_data"] is None

    def test_answers_data_invalid_json_becomes_none(self, mod):
        assert mod.shape_doc(_row(answers_data="{not json"), GOLD_TS)["answers_data"] is None

    def test_bom_none_when_no_lines(self, mod):
        doc = mod.shape_doc(_row(), GOLD_TS, bom_lines=[])
        assert doc["bom"] is None

    def test_bom_embedded_when_lines_provided(self, mod):
        lines = [_bom_line(), _bom_line(bom_id=2, total_cost=100.0)]
        doc = mod.shape_doc(_row(), GOLD_TS, bom_lines=lines)
        assert doc["bom"] is not None
        assert doc["bom"]["line_count"] == 2
        assert doc["bom"]["total_cost"] == round(255.0 + 100.0, 6)
        assert len(doc["bom"]["lines"]) == 2
        first = doc["bom"]["lines"][0]
        assert first["material_name"] == "Owens Corning Shingles"
        assert first["bom_id"] == 1

    def test_bom_line_fields_present(self, mod):
        doc = mod.shape_doc(_row(), GOLD_TS, bom_lines=[_bom_line()])
        line = doc["bom"]["lines"][0]
        for field in ("bom_id", "task_id", "material_name", "material_uid",
                      "cost_code", "quantity", "unit", "unit_cost", "total_cost",
                      "over_head", "profit_margin", "tax", "active",
                      "vendor_material_map_id", "source_type_id",
                      "bom_version", "bom_created_at", "bom_updated_at"):
            assert field in line, f"missing bom line field: {field}"

    def test_bom_none_when_bom_lines_not_passed(self, mod):
        doc = mod.shape_doc(_row(), GOLD_TS)
        assert doc["bom"] is None


# ---------------------------------------------------------------------------
# _build_bom_block
# ---------------------------------------------------------------------------


class TestBuildBomBlock:
    def test_returns_none_for_empty_list(self, mod):
        assert mod._build_bom_block([]) is None

    def test_line_count_and_total(self, mod):
        lines = [_bom_line(total_cost=100.0), _bom_line(bom_id=2, total_cost=50.0)]
        result = mod._build_bom_block(lines)
        assert result["line_count"] == 2
        assert result["total_cost"] == 150.0

    def test_null_total_cost_excluded_from_sum(self, mod):
        lines = [_bom_line(total_cost=200.0), _bom_line(bom_id=2, total_cost=None)]
        result = mod._build_bom_block(lines)
        assert result["total_cost"] == 200.0

    def test_lines_normalized_keys(self, mod):
        result = mod._build_bom_block([_bom_line()])
        line = result["lines"][0]
        # bom_lsn and project_id are internal — must NOT appear in the output
        assert "bom_lsn" not in line
        assert "project_id" not in line
        assert "bom_invalid" not in line
        assert line["bom_id"] == 1


# ---------------------------------------------------------------------------
# _fetch_bom_for_projects
# ---------------------------------------------------------------------------


class TestFetchBomForProjects:
    def test_empty_project_ids_returns_empty(self, mod):
        assert mod._fetch_bom_for_projects("dsn", "T1", []) == {}

    def test_groups_by_project_id(self, mod, patch_pg):
        rows = [_bom_line(project_id=7), _bom_line(bom_id=2, project_id=8)]
        patch_pg(mod, rows=rows)
        result = mod._fetch_bom_for_projects("dsn", "DEMO-001", [7, 8])
        assert set(result.keys()) == {7, 8}
        assert len(result[7]) == 1
        assert len(result[8]) == 1

    def test_multiple_lines_same_project(self, mod, patch_pg):
        rows = [_bom_line(project_id=7), _bom_line(bom_id=2, project_id=7)]
        patch_pg(mod, rows=rows)
        result = mod._fetch_bom_for_projects("dsn", "DEMO-001", [7])
        assert len(result[7]) == 2

    def test_env_param_passed_to_query(self, mod, patch_pg):
        # Regression guard: the caller's source_env must reach the query
        # params so a BOM row sharing the same tenantid+project_id under a
        # DIFFERENT Silver env (tenantid is only unique within one _s_env)
        # can never bleed into this project's embedded bom block.
        _, cur = patch_pg(mod, rows=[_bom_line(project_id=7)])
        mod._fetch_bom_for_projects("dsn", "DEMO-001", [7], env="si_dev")
        args, kwargs = cur.execute.call_args
        assert args[1]["env"] == "si_dev"

    def test_env_defaults_to_none_when_unscoped(self, mod, patch_pg):
        _, cur = patch_pg(mod, rows=[_bom_line(project_id=7)])
        mod._fetch_bom_for_projects("dsn", "DEMO-001", [7])
        args, kwargs = cur.execute.call_args
        assert args[1]["env"] is None


# ---------------------------------------------------------------------------
# iter_silver_rows
# ---------------------------------------------------------------------------


class TestIterSilverRows:
    def test_yields_dicts_and_closes(self, mod, patch_pg):
        conn, cur = patch_pg(mod, rows=[_row(), _row(answer_id=2)])
        out = list(mod.iter_silver_rows("dsn", "DEMO-001"))
        assert len(out) == 2 and all(isinstance(r, dict) for r in out)
        conn.close.assert_called_once()

    def test_max_docs_limits(self, mod, patch_pg):
        patch_pg(mod, rows=[_row(), _row(answer_id=2), _row(answer_id=3)])
        out = list(mod.iter_silver_rows("dsn", "DEMO-001", max_docs=2))
        assert len(out) == 2

    def test_query_params(self, mod, patch_pg):
        conn, cur = patch_pg(mod, rows=[_row()])
        list(mod.iter_silver_rows("dsn", "DEMO-001"))
        args, _ = cur.execute.call_args
        assert args[1]["tenantid"] == "DEMO-001"
        assert "last_ts" in args[1]


# ---------------------------------------------------------------------------
# bulk_write_with_retry
# ---------------------------------------------------------------------------


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

    def test_transient_then_success(self, mod):
        coll = make_collection()
        coll.bulk_write.side_effect = [
            AutoReconnect("boom"),
            make_bulk_result(upserted=1),
        ]
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


def _wire_run(mod, monkeypatch, rows, *, max_ts=GOLD_TS, bom_by_project=None):
    monkeypatch.setattr(mod, "read_cursor", lambda *a, **k: EPOCH)
    monkeypatch.setattr(mod, "write_cursor", lambda *a, **k: None)
    monkeypatch.setattr(mod, "mark_error", lambda *a, **k: None)
    monkeypatch.setattr(mod, "iter_silver_rows", lambda *a, **k: iter(rows))
    monkeypatch.setattr(mod, "_fetch_bom_for_projects",
                        lambda *a, **k: bom_by_project or {})
    # run() computes max_silver_ts via a real per-env Postgres query
    # (get_max_silver_ts_per_env) rather than tracking it inline over the
    # already-fetched rows -- must be mocked to keep this a pure unit test
    # (no live DB connection).
    monkeypatch.setattr(mod, "get_max_silver_ts_per_env", lambda *a, **k: max_ts)


class TestRun:
    def test_dry_run_success(self, mod, monkeypatch):
        # dry-run must be side-effect-free: it must NOT advance the Silver cursor,
        # otherwise a subsequent live run would skip all rows.
        _wire_run(mod, monkeypatch, [_row(silver_ts=GOLD_TS), _row(answer_id=2, silver_ts=GOLD_TS)])
        wrote = []
        monkeypatch.setattr(mod, "write_cursor", lambda *a, **k: wrote.append(a))
        assert mod.run(make_cfg(mod, dry_run=True, batch_size=1)) == 0
        assert not wrote  # dry-run does not advance the cursor

    def test_empty_rows(self, mod, monkeypatch):
        _wire_run(mod, monkeypatch, [])
        assert mod.run(make_cfg(mod, dry_run=True)) == 0

    def test_row_with_no_answer_id_skipped_in_flush(self, mod, monkeypatch):
        # answer_id None -> shape_doc returns None -> continue branch in flush()
        _wire_run(mod, monkeypatch, [_row(answer_id=None, silver_ts=GOLD_TS)])
        assert mod.run(make_cfg(mod, dry_run=True)) == 0

    def test_wet_run_writes_and_verifies(self, mod, monkeypatch, patch_mongo):
        coll = patch_mongo(mod)
        _wire_run(mod, monkeypatch, [_row()])
        assert mod.run(make_cfg(mod, dry_run=False)) == 0
        coll.bulk_write.assert_called_once()
        # verify path count_documents called on wet run
        coll.count_documents.assert_called_once()

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
        _wire_run(mod, monkeypatch, [_row(), _row(answer_id=2)])
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
        assert captured  # at least SIGINT registered
        mod._shutdown = False
        list(captured.values())[0](2, None)  # invoke handler
        assert mod._shutdown is True
        mod._shutdown = False
