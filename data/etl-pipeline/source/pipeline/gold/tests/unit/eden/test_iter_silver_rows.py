"""Unit tests for iter_silver_rows / fetch_tenant_rows in Eden and AI Assistant loaders."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _make_conn(rows):
    cur = MagicMock()
    cur.__iter__ = MagicMock(return_value=iter(rows))
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn, cur


# ── Eden loaders ──────────────────────────────────────────────────────────────

@pytest.mark.eden
class TestIterSilverRowsProjectHealth:
    def test_yields_rows(self):
        from gold.src.eden.load_project_health import iter_silver_rows
        rows = [{"tenantid": "T-001", "project_id": "p-1", "silver_lsn": 5,
                 "completion_pct": 75.0}]
        conn, cur = _make_conn(rows)
        cur.__iter__ = MagicMock(return_value=iter(rows))
        with patch("psycopg2.connect", return_value=conn):
            result = list(iter_silver_rows("dsn", "T-001"))
        assert result[0]["completion_pct"] == 75.0

    def test_max_docs(self):
        from gold.src.eden.load_project_health import iter_silver_rows
        rows = [{"tenantid": "T-001", "project_id": f"p-{i}"} for i in range(5)]
        conn, cur = _make_conn(rows)
        cur.__iter__ = MagicMock(return_value=iter(rows))
        with patch("psycopg2.connect", return_value=conn):
            assert len(list(iter_silver_rows("dsn", "T-001", max_docs=2))) == 2

    def test_connection_closed(self):
        from gold.src.eden.load_project_health import iter_silver_rows
        conn, cur = _make_conn([])
        cur.__iter__ = MagicMock(return_value=iter([]))
        with patch("psycopg2.connect", return_value=conn):
            list(iter_silver_rows("dsn", "T-001"))
        conn.close.assert_called_once()


@pytest.mark.eden
class TestIterSilverRowsInvoiceSummary:
    def test_yields_rows(self):
        from gold.src.eden.load_invoice_summary import iter_silver_rows
        rows = [{"tenantid": "T-001", "project_id": "p-2", "total_invoiced": 1000.0}]
        conn, cur = _make_conn(rows)
        cur.__iter__ = MagicMock(return_value=iter(rows))
        with patch("psycopg2.connect", return_value=conn):
            assert list(iter_silver_rows("dsn", "T-001"))[0]["total_invoiced"] == 1000.0

    def test_empty(self):
        from gold.src.eden.load_invoice_summary import iter_silver_rows
        conn, cur = _make_conn([])
        cur.__iter__ = MagicMock(return_value=iter([]))
        with patch("psycopg2.connect", return_value=conn):
            assert list(iter_silver_rows("dsn", "T-001")) == []

    def test_max_docs(self):
        from gold.src.eden.load_invoice_summary import iter_silver_rows
        rows = [{"tenantid": "T-001", "project_id": f"p-{i}"} for i in range(4)]
        conn, cur = _make_conn(rows)
        cur.__iter__ = MagicMock(return_value=iter(rows))
        with patch("psycopg2.connect", return_value=conn):
            assert len(list(iter_silver_rows("dsn", "T-001", max_docs=2))) == 2


@pytest.mark.eden
class TestIterSilverRowsTaskIntelligence:
    def test_yields_rows(self):
        from gold.src.eden.load_task_intelligence import iter_silver_rows
        rows = [{"tenantid": "T-001", "project_id": "p-3", "total_tasks": 10}]
        conn, cur = _make_conn(rows)
        cur.__iter__ = MagicMock(return_value=iter(rows))
        with patch("psycopg2.connect", return_value=conn):
            assert list(iter_silver_rows("dsn", "T-001"))[0]["total_tasks"] == 10

    def test_connection_closed(self):
        from gold.src.eden.load_task_intelligence import iter_silver_rows
        conn, cur = _make_conn([])
        cur.__iter__ = MagicMock(return_value=iter([]))
        with patch("psycopg2.connect", return_value=conn):
            list(iter_silver_rows("dsn", "T-001"))
        conn.close.assert_called_once()


@pytest.mark.eden
class TestIterSilverRowsProcurementStatus:
    def test_yields_rows(self):
        from gold.src.eden.load_procurement_status import iter_silver_rows
        rows = [{"tenantid": "T-001", "project_id": "p-4", "total_bom_items": 3}]
        conn, cur = _make_conn(rows)
        cur.__iter__ = MagicMock(return_value=iter(rows))
        with patch("psycopg2.connect", return_value=conn):
            assert list(iter_silver_rows("dsn", "T-001"))[0]["total_bom_items"] == 3

    def test_empty(self):
        from gold.src.eden.load_procurement_status import iter_silver_rows
        conn, cur = _make_conn([])
        cur.__iter__ = MagicMock(return_value=iter([]))
        with patch("psycopg2.connect", return_value=conn):
            assert list(iter_silver_rows("dsn", "T-001")) == []


@pytest.mark.eden
class TestIterSilverRowsBidContext:
    def test_yields_rows(self):
        from gold.src.eden.load_bid_context import iter_silver_rows
        rows = [{"tenantid": "T-001", "project_id": "p-5", "total_questions": 5}]
        conn, cur = _make_conn(rows)
        cur.__iter__ = MagicMock(return_value=iter(rows))
        with patch("psycopg2.connect", return_value=conn):
            assert list(iter_silver_rows("dsn", "T-001"))[0]["total_questions"] == 5

    def test_max_docs(self):
        from gold.src.eden.load_bid_context import iter_silver_rows
        rows = [{"tenantid": "T-001", "project_id": f"p-{i}"} for i in range(3)]
        conn, cur = _make_conn(rows)
        cur.__iter__ = MagicMock(return_value=iter(rows))
        with patch("psycopg2.connect", return_value=conn):
            assert len(list(iter_silver_rows("dsn", "T-001", max_docs=1))) == 1


@pytest.mark.eden
class TestFetchTenantRows:
    def _make_fetchall_conn(self, rows):
        cur = MagicMock()
        cur.fetchall.return_value = rows
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        conn = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return conn, cur

    def test_returns_list_when_rows_found(self):
        from gold.src.eden.load_tenant_dashboard import fetch_tenant_rows
        row = {"tenantid": "T-001", "project_count": 5}
        conn, _ = self._make_fetchall_conn([row])
        with patch("psycopg2.connect", return_value=conn):
            assert fetch_tenant_rows("dsn", "T-001") == [dict(row)]

    def test_returns_empty_list_when_no_rows(self):
        from gold.src.eden.load_tenant_dashboard import fetch_tenant_rows
        conn, _ = self._make_fetchall_conn([])
        with patch("psycopg2.connect", return_value=conn):
            assert fetch_tenant_rows("dsn", "T-001") == []

    def test_connection_closed(self):
        from gold.src.eden.load_tenant_dashboard import fetch_tenant_rows
        conn, _ = self._make_fetchall_conn([])
        with patch("psycopg2.connect", return_value=conn):
            fetch_tenant_rows("dsn", "T-001")
        conn.close.assert_called_once()
