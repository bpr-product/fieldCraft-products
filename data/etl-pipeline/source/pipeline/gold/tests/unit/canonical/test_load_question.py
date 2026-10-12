"""Unit tests for the Canonical Gold question loader (load_question.py).

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

_TEMPLATE_DATA = {
    "SDM": {"Building": {"TemplateVersion": [{
        "ItemDef": [{"OID": "Q1", "Question": {"TranslatedText": {"text": "Roof type?"}},
                     "DataType": "text"}],
        "ItemGroupDef": [{"ItemRef": [{"ItemOID": "Q1", "Mandatory": "Yes"}]}],
    }]}}
}


def _base_row(**overrides) -> dict:
    row = {
        "tenantid": "fieldcraft_provider",
        "source_env": "dev",
        "is_deleted": False,
        "deleted_at": None,
        "silver_lsn": 12,
        "qt_silver_ts": datetime(2026, 6, 30, tzinfo=timezone.utc),
        "template_id": 501,
        "name": "Roofing v1",
        "trade_oid": "R.1",
        "trade_id": 42,
        "trade_name": "Roofing",
        "version": 1,
        "status": "active",
        "type": "install",
        "invalid": False,
        "created_by": "system",
        "created_date": "2025-01-01 00:00:00+00",
        "updated_date": "2026-06-30 00:00:00+00",
        "question_template_data": _TEMPLATE_DATA,
    }
    row.update(overrides)
    return row


class TestQuestionShapeDoc:
    def test_basic_shape(self):
        from gold.src.canonical.load_question import shape_doc
        doc = shape_doc(_base_row(), _TS, tenant_ids=["tn1", "tn2"])
        assert doc["tenantId"] == "fieldcraft_provider"
        assert doc["templateId"] == 501
        assert doc["tradeOid"] == "R.1"
        assert doc["tradeId"] == 42
        assert doc["_grain"] == "canonical_question"
        assert doc["itemCount"] == 1
        assert doc["items"][0]["itemOid"] == "Q1"
        assert doc["items"][0]["question"] == "Roof type?"
        assert doc["items"][0]["required"] is True

    def test_tenant_ids_sorted_and_deduped_by_caller(self):
        from gold.src.canonical.load_question import shape_doc
        doc = shape_doc(_base_row(), _TS, tenant_ids=["tn2", "tn1"])
        assert doc["tenantIds"] == ["tn1", "tn2"]

    def test_no_tenant_ids_is_empty_list_not_none(self):
        from gold.src.canonical.load_question import shape_doc
        doc = shape_doc(_base_row(), _TS, tenant_ids=None)
        assert doc["tenantIds"] == []

    def test_lineage_and_source_env(self):
        from gold.src.canonical.load_question import shape_doc
        doc = shape_doc(_base_row(), _TS, tenant_ids=[])
        assert doc["goldUpdatedAt"] == _TS
        assert doc["sourceEnv"] == "dev"

    def test_contract_validation_passes(self):
        from gold.src.canonical.load_question import shape_doc, _contract_validator
        doc = shape_doc(_base_row(), _TS, tenant_ids=[])
        assert _contract_validator.check(doc) is None


class TestFetchTenantIdsForTradesEnvScoping:
    """Regression guard for the bug where `_fetch_tenant_ids_for_trades()`
    accepted an `env` parameter that neither call site ever passed, making its
    `(%(env)s::text IS NULL OR _s_env = %(env)s)` filter a permanent no-op —
    tenantIds merged tenant ids across every environment instead of scoping
    per env."""

    def _mock_conn(self, rows):
        cur = MagicMock()
        cur.__iter__ = MagicMock(return_value=iter(rows))
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        conn = MagicMock()
        conn.cursor.return_value = cur
        return conn, cur

    def test_env_is_passed_to_the_query(self):
        from gold.src.canonical.load_question import _fetch_tenant_ids_for_trades
        conn, cur = self._mock_conn([])
        with patch("psycopg2.connect", return_value=conn):
            _fetch_tenant_ids_for_trades("dsn", [42], env="dev")
        called_params = cur.execute.call_args[0][1]
        assert called_params["env"] == "dev"

    def test_build_documents_groups_trade_lookup_by_source_env(self):
        """Two rows sharing a trade_id but in different envs must resolve
        their tenantIds independently — not in one ungrouped call."""
        from gold.src.canonical import load_question

        rows = [
            _base_row(template_id=1, trade_id=42, source_env="dev"),
            _base_row(template_id=2, trade_id=42, source_env="qa"),
        ]
        seen_envs = []

        def _fake_fetch(pg_dsn, trade_ids, env=None):
            seen_envs.append(env)
            return {42: [f"tenant-in-{env}"]}

        with patch.object(load_question, "iter_silver_rows", return_value=rows), \
             patch.object(load_question, "_fetch_tenant_ids_for_trades", side_effect=_fake_fetch):
            docs = load_question.build_documents("dsn", _TS)

        assert sorted(seen_envs) == ["dev", "qa"]
        by_env = {d["sourceEnv"]: d["tenantIds"] for d in docs}
        assert by_env["dev"] == ["tenant-in-dev"]
        assert by_env["qa"] == ["tenant-in-qa"]


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
    def test_delegates_to_shared_per_env_helper_with_template_tenant(self):
        from gold.src.canonical import load_question
        with patch.object(load_question, "get_max_silver_ts_per_env",
                          return_value=_TS) as mock_helper:
            result = load_question._get_max_silver_ts("dsn")
        mock_helper.assert_called_once_with(
            "dsn", "fieldcraft_provider_question_template", "_s_tenantid",
            load_question._TEMPLATE_TENANT,
        )
        assert result == _TS
