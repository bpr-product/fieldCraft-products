"""Unit tests for the Canonical Gold answer loader (load_answer.py).

All tests run without external connections (no Mongo, no PostgreSQL).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TS = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
_TENANT = "DEMO-T1"


def _base_row(**overrides) -> dict:
    row = {
        "tenantid": _TENANT,
        "source_env": "dev",
        "is_deleted": False,
        "deleted_at": None,
        "silver_lsn": 9,
        "silver_ts": datetime(2026, 6, 30, tzinfo=timezone.utc),
        "answer_id": 7001,
        "project_id": 9001,
        "trade_oid": "R.1",
        "question_template_version": 1,
        "schema_version": 1,
        "status": "submitted",
        "created_by": "system",
        "created_date": "2025-01-01 00:00:00+00",
        "updated_date": "2026-06-30 00:00:00+00",
        "invalid": "false",
        "answers_data": '{"Q1": "shingle"}',
    }
    row.update(overrides)
    return row


class TestAnswerShapeDoc:
    def test_basic_shape(self):
        from gold.src.canonical.load_answer import shape_doc
        doc = shape_doc(_base_row(), _TS, template_id=501)
        assert doc["tenantId"] == _TENANT
        assert doc["answerId"] == 7001
        assert doc["projectId"] == 9001
        assert doc["templateId"] == 501
        assert doc["answersData"] == {"Q1": "shingle"}
        assert doc["_grain"] == "canonical_answer"

    def test_row_without_answer_id_returns_none(self):
        from gold.src.canonical.load_answer import shape_doc
        assert shape_doc(_base_row(answer_id=None), _TS) is None

    def test_malformed_answers_data_returns_none_field(self):
        from gold.src.canonical.load_answer import shape_doc
        doc = shape_doc(_base_row(answers_data="not json"), _TS)
        assert doc["answersData"] is None

    def test_lineage_and_source_env(self):
        from gold.src.canonical.load_answer import shape_doc
        doc = shape_doc(_base_row(), _TS)
        assert doc["goldUpdatedAt"] == _TS
        assert doc["sourceEnv"] == "dev"

    def test_contract_validation_passes(self):
        from gold.src.canonical.load_answer import shape_doc, _contract_validator
        doc = shape_doc(_base_row(), _TS)
        assert _contract_validator.check(doc) is None


class TestBuildDocumentsGroupsTemplateLookupByEnv:
    def test_pairs_grouped_by_source_env(self):
        """Two rows sharing (trade_oid, version) but in different envs must
        resolve their templateId independently — mirrors
        load_question.py's env-scoped trade lookup fix."""
        from gold.src.canonical import load_answer

        rows = [
            _base_row(answer_id=1, source_env="dev", trade_oid="R.1", question_template_version=1),
            _base_row(answer_id=2, source_env="qa", trade_oid="R.1", question_template_version=1),
        ]
        seen_envs = []

        def _fake_fetch(pg_dsn, pairs, env=None):
            seen_envs.append(env)
            return {("R.1", 1): 100 if env == "dev" else 200}

        with patch.object(load_answer, "iter_silver_rows", return_value=rows), \
             patch.object(load_answer, "_fetch_template_ids", side_effect=_fake_fetch):
            docs = load_answer.build_documents("dsn", _TENANT, _TS)

        assert sorted(seen_envs) == ["dev", "qa"]
        by_env = {d["sourceEnv"]: d["templateId"] for d in docs}
        assert by_env["dev"] == 100
        assert by_env["qa"] == 200


def _mock_conn(fetchone_return):
    cur = MagicMock()
    cur.fetchone.return_value = fetchone_return
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cur
    return conn, cur


class TestGetMaxSilverTs:
    def test_delegates_to_shared_per_env_helper(self):
        from gold.src.canonical import load_answer
        with patch.object(load_answer, "get_max_silver_ts_per_env",
                          return_value=_TS) as mock_helper:
            result = load_answer._get_max_silver_ts("dsn", _TENANT)
        mock_helper.assert_called_once_with("dsn", "answers", "_s_tenantid", _TENANT)
        assert result == _TS


class TestRunAdvancesCursorViaSharedHelper:
    """Regression guard for the exact bug reviewer finding #2 flagged:
    `run()` used to track `max_silver_ts` inline as the plain max over every
    fetched row regardless of env — the overall-MAX bug this whole pass fixes
    — instead of calling its own (correctly per-env) `_get_max_silver_ts()`.
    Assert the source no longer contains that inline-tracking pattern and
    does call the per-env helper before writing the cursor."""

    def test_run_source_calls_get_max_silver_ts_not_inline_tracking(self):
        import inspect
        from gold.src.canonical.load_answer import run
        src = inspect.getsource(run)
        assert "_get_max_silver_ts(cfg.pg_dsn, cfg.tenantid)" in src
        assert "if ts and ts > max_silver_ts" not in src
