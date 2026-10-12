"""Unit tests for the Canonical Gold tenant loader (load_tenant.py).

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
        "silver_lsn": 10,
        "tenant_silver_ts": datetime(2026, 6, 30, tzinfo=timezone.utc),
        "tenant_uuid": "uuid-1",
        "name": "SYN Roofing",
        "status": "Active",
        "email": "syn@example.com",
        "address": "123 Main St",
        "city": "Springfield",
        "state": "IL",
        "country": "US",
        "zipcode": "62701",
        "description": None,
        "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "created_by": "system",
        "updated_at": datetime(2026, 6, 30, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


class TestTenantShapeDoc:
    def test_basic_shape(self):
        from gold.src.canonical.load_tenant import shape_doc
        doc = shape_doc(_base_row(), _TS)
        assert doc["tenantId"] == _TENANT
        assert doc["tenantUuid"] == "uuid-1"
        assert doc["name"] == "SYN Roofing"
        assert doc["status"] == "Active"
        assert doc["_grain"] == "canonical_tenant"
        assert doc["_schemaVersion"] == 1
        assert doc["isDeleted"] is False

    def test_lineage_fields_present(self):
        from gold.src.canonical.load_tenant import shape_doc
        doc = shape_doc(_base_row(), _TS)
        assert doc["goldUpdatedAt"] == _TS
        assert doc["dataOrigin"] == "SOURCE"
        assert doc["sourceEnv"] == "dev"

    def test_watermark_and_lag_computed(self):
        from gold.src.canonical.load_tenant import shape_doc
        doc = shape_doc(_base_row(), _TS)
        assert doc["_silverWatermarks"]["tenant"] == _base_row()["tenant_silver_ts"]
        assert doc["lafieldcraft"] >= 0

    def test_deleted_row_is_deleted_true(self):
        from gold.src.canonical.load_tenant import shape_doc
        doc = shape_doc(_base_row(is_deleted=True), _TS)
        assert doc["isDeleted"] is True

    def test_contract_validation_passes(self):
        from gold.src.canonical.load_tenant import shape_doc, _contract_validator
        doc = shape_doc(_base_row(), _TS)
        assert _contract_validator.check(doc) is None


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
        """Regression guard for the bug class this loader was fixed for: a
        hand-copied overall-MAX query (no per-env grouping) would silently
        mask a slower env's still-unprocessed update. Verifying delegation to
        the shared helper (rather than re-testing its SQL here) prevents this
        loader from ever hand-rolling that query again."""
        from gold.src.canonical import load_tenant
        with patch.object(load_tenant, "get_max_silver_ts_per_env",
                          return_value=_TS) as mock_helper:
            result = load_tenant._get_max_silver_ts("dsn", _TENANT)
        mock_helper.assert_called_once_with("dsn", "tenant", "_s_tenantid", _TENANT)
        assert result == _TS

    def test_no_rows_returns_epoch(self):
        from gold.src.canonical.load_tenant import _get_max_silver_ts
        from gold.pipeline.cursor_store import EPOCH
        conn, _ = _mock_conn((None,))
        with patch("psycopg2.connect", return_value=conn):
            assert _get_max_silver_ts("dsn", _TENANT) == EPOCH
