"""Unit tests for the Canonical Gold project loader (load_project.py).

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
        "silver_lsn": 7,
        "project_silver_ts": datetime(2026, 6, 30, tzinfo=timezone.utc),
        "asset_silver_ts": datetime(2026, 6, 29, tzinfo=timezone.utc),
        "project_id": 9001,
        "project_uid": "puid-1",
        "order_no": "ORD-9",
        "customer_uid": "cust-1",
        "asset_uid": "asset-1",
        "status": "active",
        "trade": "Roofing",
        "trade_type": "Residential",
        "trade_oid": "R.1",
        "project_type": "install",
        "scheduled_type": "standard",
        "location": "123 Main St",
        "zipcode": "62701",
        "metro_code": "SPI",
        "jobsite_lat": "39.78",
        "jobsite_lng": "-89.65",
        "reference_project_id": None,
        "parent_project_id": None,
        "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "created_by": "system",
        "updated_at": datetime(2026, 6, 30, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


class TestProjectShapeDoc:
    def test_basic_shape(self):
        from gold.src.canonical.load_project import shape_doc
        doc = shape_doc(_base_row(), _TS)
        assert doc["tenantId"] == _TENANT
        assert doc["projectId"] == "9001"
        assert doc["orderNo"] == "ORD-9"
        assert doc["assetUid"] == "asset-1"
        assert doc["jobsiteLat"] == 39.78
        assert doc["jobsiteLng"] == -89.65
        assert doc["_grain"] == "canonical_project"

    def test_reference_and_parent_ids_cast_to_str(self):
        from gold.src.canonical.load_project import shape_doc
        row = _base_row(reference_project_id=8000, parent_project_id=7000)
        doc = shape_doc(row, _TS)
        assert doc["referenceProjectId"] == "8000"
        assert doc["parentProjectId"] == "7000"

    def test_null_reference_and_parent_ids_stay_none(self):
        from gold.src.canonical.load_project import shape_doc
        doc = shape_doc(_base_row(), _TS)
        assert doc["referenceProjectId"] is None
        assert doc["parentProjectId"] is None

    def test_lineage_and_source_env(self):
        from gold.src.canonical.load_project import shape_doc
        doc = shape_doc(_base_row(), _TS)
        assert doc["goldUpdatedAt"] == _TS
        assert doc["sourceEnv"] == "dev"

    def test_contract_validation_passes(self):
        from gold.src.canonical.load_project import shape_doc, _contract_validator
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
        from gold.src.canonical import load_project
        with patch.object(load_project, "get_max_silver_ts_per_env",
                          return_value=_TS) as mock_helper:
            result = load_project._get_max_silver_ts("dsn", _TENANT)
        mock_helper.assert_called_once_with("dsn", "project", "_s_tenantid", _TENANT)
        assert result == _TS
