"""Unit tests for the Canonical Gold asset loader (load_asset.py).

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
        "asset_uid": "asset-1",
        "asset_name": "123 Main St Roof",
        "asset_type": "roof",
        "asset_address": '{"line1": "123 Main St", "city": "Springfield"}',
        "billing_address": None,
        "customer_uid": "cust-1",
        "customer_name": "Jane Homeowner",
        "project_ids": ["9001", "9002"],
        "is_deleted": False,
        "silver_lsn": 3,
        "asset_silver_ts": datetime(2026, 6, 30, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


class TestAssetShapeDoc:
    def test_basic_shape(self):
        from gold.src.canonical.load_asset import shape_doc
        doc = shape_doc(_base_row(), _TS)
        assert doc["tenantId"] == _TENANT
        assert doc["assetUid"] == "asset-1"
        assert doc["name"] == "123 Main St Roof"
        assert doc["projectIds"] == ["9001", "9002"]
        assert doc["_grain"] == "canonical_asset"

    def test_address_json_string_is_parsed_to_object(self):
        from gold.src.canonical.load_asset import shape_doc
        doc = shape_doc(_base_row(), _TS)
        assert doc["address"] == {"line1": "123 Main St", "city": "Springfield"}

    def test_malformed_address_json_falls_back_to_raw_string(self):
        from gold.src.canonical.load_asset import shape_doc
        doc = shape_doc(_base_row(asset_address="not json"), _TS)
        assert doc["address"] == "not json"

    def test_lineage_and_source_env(self):
        from gold.src.canonical.load_asset import shape_doc
        doc = shape_doc(_base_row(), _TS)
        assert doc["goldUpdatedAt"] == _TS
        assert doc["sourceEnv"] == "dev"

    def test_contract_validation_passes(self):
        from gold.src.canonical.load_asset import shape_doc, _contract_validator
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
        from gold.src.canonical import load_asset
        with patch.object(load_asset, "get_max_silver_ts_per_env",
                          return_value=_TS) as mock_helper:
            result = load_asset._get_max_silver_ts("dsn", _TENANT)
        mock_helper.assert_called_once_with("dsn", "customer_asset", "_s_tenantid", _TENANT)
        assert result == _TS
