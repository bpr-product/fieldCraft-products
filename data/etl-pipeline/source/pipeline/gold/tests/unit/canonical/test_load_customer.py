"""Unit tests for the Canonical Gold customer loader (load_customer.py).

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
        "customer_uid": "cust-1",
        "customer_name": "Jane Homeowner",
        "email": "jane@example.com",
        "mobile_no": "555-0101",
        "account_no": "ACC-1",
        "project_ids": ["9001", "9002"],
        "asset_uids": ["asset-1"],
        "is_deleted": False,
        "silver_lsn": 5,
        "customer_asset_silver_ts": datetime(2026, 6, 30, tzinfo=timezone.utc),
        "firstname": None,
        "middlename": None,
        "lastname": None,
        "gender": None,
        "birthdate": None,
        "fieldcraft_customer_status": None,
        "fieldcraft_customer_account_no": None,
        "alternate_mobile": None,
        "fieldcraft_customer_silver_ts": None,
    }
    row.update(overrides)
    return row


class TestCustomerShapeDoc:
    def test_basic_shape_falls_back_to_customer_asset_name(self):
        from gold.src.canonical.load_customer import shape_doc
        doc = shape_doc(_base_row(), _TS)
        assert doc["tenantId"] == _TENANT
        assert doc["customerUid"] == "cust-1"
        assert doc["name"] == "Jane Homeowner"
        assert doc["projectIds"] == ["9001", "9002"]
        assert doc["assetUids"] == ["asset-1"]
        assert doc["hasCustomerMasterRecord"] is False
        assert doc["_grain"] == "canonical_customer"

    def test_prefers_customer_master_name_split_when_present(self):
        from gold.src.canonical.load_customer import shape_doc
        row = _base_row(firstname="Jane", middlename=None, lastname="Homeowner",
                        fieldcraft_customer_silver_ts=datetime(2026, 6, 29, tzinfo=timezone.utc))
        doc = shape_doc(row, _TS)
        assert doc["name"] == "Jane Homeowner"
        assert doc["firstName"] == "Jane"
        assert doc["lastName"] == "Homeowner"
        assert doc["hasCustomerMasterRecord"] is True

    def test_lineage_and_source_env(self):
        from gold.src.canonical.load_customer import shape_doc
        doc = shape_doc(_base_row(), _TS)
        assert doc["goldUpdatedAt"] == _TS
        assert doc["sourceEnv"] == "dev"

    def test_contract_validation_passes(self):
        from gold.src.canonical.load_customer import shape_doc, _contract_validator
        doc = shape_doc(_base_row(), _TS)
        assert _contract_validator.check(doc) is None


class TestSilverQueryJoinIsEnvScoped:
    def test_fieldcraft_customer_customer_join_scopes_by_s_env(self):
        """Regression guard: the fieldcraft_customer_customer join previously had no
        _s_env predicate, so a customer_uuid reused across environments (seeded/
        synthetic data reused across dev/qa/staging) could pull PII from the
        wrong environment's customer record into this environment's canonical
        document. Assert the fixed join text is present in the query."""
        from gold.src.canonical.load_customer import _SILVER_QUERY
        assert "gc._s_env" in _SILVER_QUERY
        assert "= ca._s_env" in _SILVER_QUERY


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
        from gold.src.canonical import load_customer
        with patch.object(load_customer, "get_max_silver_ts_per_env",
                          return_value=_TS) as mock_helper:
            result = load_customer._get_max_silver_ts("dsn", _TENANT)
        mock_helper.assert_called_once_with("dsn", "customer_asset", "_s_tenantid", _TENANT)
        assert result == _TS
