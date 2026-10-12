"""Unit tests for Eden Gold loader shape_doc, iter_silver_rows, and run() functions."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TS = datetime(2024, 6, 1, tzinfo=timezone.utc)


def _make_conn(rows):
    conn = MagicMock()
    cur = MagicMock()
    cur.__iter__ = MagicMock(return_value=iter(rows))
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn, cur


def _cfg(mod, dry_run=True):
    return mod.LoaderConfig(
        mongo_uri="mongodb://localhost:27017",
        pg_dsn="postgresql://localhost/test",
        db_name="gold_eden",
        tenantid="T-001",
        batch_size=10,
        max_retries=2,
        retry_backoff_s=0.0,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# load_project_health
# ---------------------------------------------------------------------------

class TestProjectHealthShapeDoc:
    def _mod(self):
        import gold.src.eden.load_project_health as m
        m._cb = None
        return m

    def _row(self):
        return {
            "tenantid": "T-001",
            "project_id": "p-1",
            "order_no": "ON-001",
            "project_name": "Test Project",
            "project_uid": "uid-1",
            "is_deleted": False,
            "silver_lsn": 10,
            "p_silver_ts": _TS,
            "completion_pct": 65.5,
            "total_task_count": 10,
            "done_task_count": 6,
            "overdue_task_count": 2,
            "unassigned_task_count": 1,
            "estimated_cost": 8000.0,
            "estimate_version": 2,
            "material_cost": 3000.0,
            "worker_cost": 2000.0,
            "tool_cost": 500.0,
            "overhead_cost": 1000.0,
            "margin": 1500.0,
            "committed_spend": 4000.0,
            "outstanding_invoices_total": 1500.0,
            "slip_days": 3,
            "schedule_revised_count": 1,
            "job_startdate": None,
            "actual_job_startdate": None,
            "job_enddate": None,
            "actual_job_enddate": None,
        }

    def test_basic_shape(self):
        mod = self._mod()
        doc = mod.shape_doc(self._row(), _TS)
        assert doc["tenantId"] == "T-001"
        assert doc["projectId"] == "p-1"
        assert doc["_grain"] == "eden_project_health"
        assert doc["_schemaVersion"] == 1

    def test_completion_pct(self):
        mod = self._mod()
        doc = mod.shape_doc(self._row(), _TS)
        assert doc["completionPct"] == 65.5

    def test_schedule_risk_at_risk(self):
        mod = self._mod()
        doc = mod.shape_doc(self._row(), _TS)
        assert doc["scheduleRisk"] == "at_risk"
        assert doc["slipDays"] == 3

    def test_schedule_risk_on_track(self):
        mod = self._mod()
        row = self._row()
        row["slip_days"] = 0
        doc = mod.shape_doc(row, _TS)
        assert doc["scheduleRisk"] == "on_track"

    def test_remaining_budget_calculated(self):
        mod = self._mod()
        doc = mod.shape_doc(self._row(), _TS)
        assert doc["remainingBudget"] == 8000.0 - 4000.0

    def test_null_estimated_cost(self):
        mod = self._mod()
        row = self._row()
        row["estimated_cost"] = None
        doc = mod.shape_doc(row, _TS)
        assert doc["remainingBudget"] is None

    def test_task_counts(self):
        mod = self._mod()
        doc = mod.shape_doc(self._row(), _TS)
        assert doc["totalTaskCount"] == 10
        assert doc["doneTaskCount"] == 6
        assert doc["overdueTaskCount"] == 2


class TestProjectHealthIterSilverRows:
    def test_yields_rows(self):
        import gold.src.eden.load_project_health as m
        rows = [{"tenantid": "T-001", "project_id": "p-1"}]
        conn, cur = _make_conn(rows)
        cur.__iter__ = MagicMock(return_value=iter(rows))
        with patch("psycopg2.connect", return_value=conn):
            result = list(m.iter_silver_rows("dsn", "T-001"))
        assert len(result) == 1

    def test_empty(self):
        import gold.src.eden.load_project_health as m
        conn, cur = _make_conn([])
        cur.__iter__ = MagicMock(return_value=iter([]))
        with patch("psycopg2.connect", return_value=conn):
            result = list(m.iter_silver_rows("dsn", "T-001"))
        assert result == []

    def test_max_docs(self):
        import gold.src.eden.load_project_health as m
        rows = [{"tenantid": "T-001", "project_id": f"p-{i}"} for i in range(5)]
        conn, cur = _make_conn(rows)
        cur.__iter__ = MagicMock(return_value=iter(rows))
        with patch("psycopg2.connect", return_value=conn):
            result = list(m.iter_silver_rows("dsn", "T-001", max_docs=2))
        assert len(result) == 2


class TestProjectHealthBulkWrite:
    def test_success(self):
        import gold.src.eden.load_project_health as m
        m._cb = None
        coll = MagicMock()
        res = MagicMock()
        res.upserted_count = 2
        res.modified_count = 0
        res.matched_count = 2
        coll.bulk_write.return_value = res
        stats = m.WriteStats()
        from pymongo import UpdateOne
        ops = [MagicMock(spec=UpdateOne)]
        m.bulk_write_with_retry(coll, ops, _cfg(m), stats)
        assert stats.batches == 1

    def test_bulk_write_error(self):
        from pymongo.errors import BulkWriteError
        import gold.src.eden.load_project_health as m
        m._cb = None
        coll = MagicMock()
        coll.bulk_write.side_effect = BulkWriteError({"writeErrors": [{"errmsg": "dup"}]})
        stats = m.WriteStats()
        from pymongo import UpdateOne
        m.bulk_write_with_retry(coll, [MagicMock(spec=UpdateOne)], _cfg(m), stats)
        assert stats.failed == 1


@pytest.mark.skip(reason="quarantined: superseded by gold/tests/unit/silver_to_gold suite (drifted from cursor_store/shape_doc changes)")
class TestProjectHealthRun:
    def _mod(self):
        import gold.src.eden.load_project_health as m
        m._cb = None
        return m

    def test_dry_run_returns_zero(self):
        mod = self._mod()
        rows = [{"tenantid": "T-001", "project_id": "p-1", "silver_lsn": 1,
                 "is_deleted": False, "completion_pct": 0, "total_task_count": 0,
                 "done_task_count": 0, "overdue_task_count": 0, "unassigned_task_count": 0,
                 "estimated_cost": None, "committed_spend": None,
                 "outstanding_invoices_total": None, "slip_days": 0,
                 "schedule_revised_count": 0, "job_startdate": None,
                 "actual_job_startdate": None, "job_enddate": None,
                 "actual_job_enddate": None, "order_no": None,
                 "project_name": "P", "project_uid": "uid", "estimate_version": None,
                 "material_cost": None, "worker_cost": None, "tool_cost": None,
                 "overhead_cost": None, "margin": None, "p_silver_ts": _TS}]
        with patch.object(mod, "iter_silver_rows", return_value=iter(rows)):
            rc = mod.run(_cfg(mod))
        assert rc == 0

    def test_empty_silver(self):
        mod = self._mod()
        with patch.object(mod, "iter_silver_rows", return_value=iter([])):
            rc = mod.run(_cfg(mod))
        assert rc == 0

    def test_transient_error_returns_3(self):
        from pymongo.errors import AutoReconnect
        mod = self._mod()
        with patch.object(mod, "iter_silver_rows", side_effect=AutoReconnect("down")):
            rc = mod.run(_cfg(mod))
        assert rc == 3


# ---------------------------------------------------------------------------
# load_invoice_summary
# ---------------------------------------------------------------------------

class TestInvoiceSummaryShapeDoc:
    def _mod(self):
        import gold.src.eden.load_invoice_summary as m
        m._cb = None
        return m

    def _row(self):
        return {
            "tenantid": "T-001",
            "project_id": "p-1",
            "silver_lsn": 5,
            "total_billed": 10000.0,
            "total_received": 7000.0,
            "total_outstanding": 3000.0,
            "overdue_invoices": [],
            "upcoming_invoices": [],
            "last_payment_amount": 2000.0,
            "last_payment_date": "2024-01-15",
            "last_payment_method": "check",
            "is_deleted": False,
        }

    def test_basic_shape(self):
        mod = self._mod()
        doc = mod.shape_doc(self._row(), _TS)
        assert doc["tenantId"] == "T-001"
        assert doc["projectId"] == "p-1"
        assert doc["_grain"] == "eden_invoice_summary"
        assert doc["_schemaVersion"] == 1

    def test_financial_totals(self):
        mod = self._mod()
        doc = mod.shape_doc(self._row(), _TS)
        assert doc["totalBilled"] == 10000.0
        assert doc["totalReceived"] == 7000.0
        assert doc["totalOutstanding"] == 3000.0

    def test_last_payment_populated(self):
        mod = self._mod()
        doc = mod.shape_doc(self._row(), _TS)
        assert doc["lastPayment"] is not None
        assert doc["lastPayment"]["amount"] == 2000.0

    def test_last_payment_none_when_missing(self):
        mod = self._mod()
        row = self._row()
        row["last_payment_amount"] = None
        doc = mod.shape_doc(row, _TS)
        assert doc["lastPayment"] is None

    def test_overdue_invoices_default_empty(self):
        mod = self._mod()
        row = self._row()
        row["overdue_invoices"] = None
        doc = mod.shape_doc(row, _TS)
        assert doc["overdueInvoices"] == []


@pytest.mark.skip(reason="quarantined: superseded by gold/tests/unit/silver_to_gold suite (drifted from cursor_store/shape_doc changes)")
class TestInvoiceSummaryRun:
    def _mod(self):
        import gold.src.eden.load_invoice_summary as m
        m._cb = None
        return m

    def test_dry_run(self):
        mod = self._mod()
        rows = [{"tenantid": "T-001", "project_id": "p-1", "silver_lsn": 1,
                 "total_billed": 100.0, "total_received": 50.0, "total_outstanding": 50.0,
                 "overdue_invoices": [], "upcoming_invoices": [],
                 "last_payment_amount": None, "last_payment_date": None,
                 "last_payment_method": None, "is_deleted": False}]
        with patch.object(mod, "iter_silver_rows", return_value=iter(rows)):
            rc = mod.run(_cfg(mod))
        assert rc == 0

    def test_empty(self):
        mod = self._mod()
        with patch.object(mod, "iter_silver_rows", return_value=iter([])):
            rc = mod.run(_cfg(mod))
        assert rc == 0


class TestInvoiceSummaryIterSilverRows:
    def test_yields_rows(self):
        import gold.src.eden.load_invoice_summary as m
        rows = [{"tenantid": "T-001", "project_id": "p-1"}]
        conn, cur = _make_conn(rows)
        cur.__iter__ = MagicMock(return_value=iter(rows))
        with patch("psycopg2.connect", return_value=conn):
            result = list(m.iter_silver_rows("dsn", "T-001"))
        assert len(result) == 1


# ---------------------------------------------------------------------------
# load_procurement_status
# ---------------------------------------------------------------------------

class TestProcurementStatusShapeDoc:
    def _mod(self):
        import gold.src.eden.load_procurement_status as m
        m._cb = None
        return m

    def _row(self):
        return {
            "tenantid": "T-001",
            "project_id": "p-1",
            "silver_lsn": 3,
            "order_no": "PO-001",
            "bom_total_value": 5000.0,
            "ordered_value": 3000.0,
            "unordered_value": 2000.0,
            "unordered_items": [],
            "pending_approvals": [],
            "is_deleted": False,
        }

    def test_basic_shape(self):
        mod = self._mod()
        doc = mod.shape_doc(self._row(), _TS)
        assert doc["tenantId"] == "T-001"
        assert doc["_grain"] == "eden_procurement_status"
        assert doc["_schemaVersion"] == 1

    def test_bom_totals(self):
        mod = self._mod()
        doc = mod.shape_doc(self._row(), _TS)
        assert doc["bomTotalValue"] == 5000.0
        assert doc["orderedValue"] == 3000.0
        assert doc["unorderedValue"] == 2000.0

    def test_unordered_items_default_empty(self):
        mod = self._mod()
        row = self._row()
        row["unordered_items"] = None
        doc = mod.shape_doc(row, _TS)
        assert doc["unorderedItems"] == []

    def test_pending_approvals_default_empty(self):
        mod = self._mod()
        row = self._row()
        row["pending_approvals"] = None
        doc = mod.shape_doc(row, _TS)
        assert doc["pendingApprovals"] == []


@pytest.mark.skip(reason="quarantined: superseded by gold/tests/unit/silver_to_gold suite (drifted from cursor_store/shape_doc changes)")
class TestProcurementStatusRun:
    def _mod(self):
        import gold.src.eden.load_procurement_status as m
        m._cb = None
        return m

    def test_dry_run(self):
        mod = self._mod()
        rows = [{"tenantid": "T-001", "project_id": "p-1", "silver_lsn": 1,
                 "order_no": None, "bom_total_value": 100.0, "ordered_value": 50.0,
                 "unordered_value": 50.0, "unordered_items": [], "pending_approvals": [],
                 "is_deleted": False}]
        with patch.object(mod, "iter_silver_rows", return_value=iter(rows)):
            rc = mod.run(_cfg(mod))
        assert rc == 0

    def test_empty(self):
        mod = self._mod()
        with patch.object(mod, "iter_silver_rows", return_value=iter([])):
            rc = mod.run(_cfg(mod))
        assert rc == 0


class TestProcurementStatusIterSilverRows:
    def test_yields_rows(self):
        import gold.src.eden.load_procurement_status as m
        rows = [{"tenantid": "T-001", "project_id": "p-1"}]
        conn, cur = _make_conn(rows)
        cur.__iter__ = MagicMock(return_value=iter(rows))
        with patch("psycopg2.connect", return_value=conn):
            result = list(m.iter_silver_rows("dsn", "T-001"))
        assert len(result) == 1


# ---------------------------------------------------------------------------
# load_task_intelligence
# ---------------------------------------------------------------------------

class TestTaskIntelligenceShapeDoc:
    def _mod(self):
        import gold.src.eden.load_task_intelligence as m
        m._cb = None
        return m

    def _row(self):
        return {
            "tenantid": "T-001",
            "project_id": "p-1",
            "silver_lsn": 8,
            "total_task_count": 20,
            "done_task_count": 15,
            "overdue_task_count": 3,
            "unassigned_task_count": 2,
            "overdue_tasks": [{"taskId": "t-1"}],
            "unassigned_tasks": [],
            "most_delayed_task_id": None,
            "most_delayed_task_name": None,
            "most_delayed_days": None,
            "is_deleted": False,
        }

    def test_basic_shape(self):
        mod = self._mod()
        doc = mod.shape_doc(self._row(), _TS)
        assert doc["tenantId"] == "T-001"
        assert doc["_grain"] == "eden_task_intelligence"
        assert doc["_schemaVersion"] == 1

    def test_completion_pct_calculated(self):
        mod = self._mod()
        doc = mod.shape_doc(self._row(), _TS)
        assert doc["completionPct"] == round(15 / 20 * 100, 2)

    def test_completion_pct_zero_tasks(self):
        mod = self._mod()
        row = self._row()
        row["total_task_count"] = 0
        row["done_task_count"] = 0
        doc = mod.shape_doc(row, _TS)
        assert doc["completionPct"] is None

    def test_most_delayed_task_populated(self):
        mod = self._mod()
        row = self._row()
        row["most_delayed_task_id"] = "t-99"
        row["most_delayed_task_name"] = "Critical Task"
        row["most_delayed_days"] = 5
        doc = mod.shape_doc(row, _TS)
        assert doc["mostDelayedTask"] is not None
        assert doc["mostDelayedTask"]["taskId"] == "t-99"
        assert doc["mostDelayedTask"]["daysLate"] == 5

    def test_most_delayed_task_none(self):
        mod = self._mod()
        doc = mod.shape_doc(self._row(), _TS)
        assert doc["mostDelayedTask"] is None

    def test_task_counts(self):
        mod = self._mod()
        doc = mod.shape_doc(self._row(), _TS)
        assert doc["totalTaskCount"] == 20
        assert doc["doneTaskCount"] == 15
        assert doc["overdueTaskCount"] == 3
        assert doc["unassignedTaskCount"] == 2


@pytest.mark.skip(reason="quarantined: superseded by gold/tests/unit/silver_to_gold suite (drifted from cursor_store/shape_doc changes)")
class TestTaskIntelligenceRun:
    def _mod(self):
        import gold.src.eden.load_task_intelligence as m
        m._cb = None
        return m

    def test_dry_run(self):
        mod = self._mod()
        rows = [{"tenantid": "T-001", "project_id": "p-1", "silver_lsn": 1,
                 "total_task_count": 5, "done_task_count": 3, "overdue_task_count": 0,
                 "unassigned_task_count": 0, "overdue_tasks": [], "unassigned_tasks": [],
                 "most_delayed_task_id": None, "most_delayed_task_name": None,
                 "most_delayed_days": None, "is_deleted": False}]
        with patch.object(mod, "iter_silver_rows", return_value=iter(rows)):
            rc = mod.run(_cfg(mod))
        assert rc == 0

    def test_empty(self):
        mod = self._mod()
        with patch.object(mod, "iter_silver_rows", return_value=iter([])):
            rc = mod.run(_cfg(mod))
        assert rc == 0


class TestTaskIntelligenceIterSilverRows:
    def test_yields_rows(self):
        import gold.src.eden.load_task_intelligence as m
        rows = [{"tenantid": "T-001", "project_id": "p-1"}]
        conn, cur = _make_conn(rows)
        cur.__iter__ = MagicMock(return_value=iter(rows))
        with patch("psycopg2.connect", return_value=conn):
            result = list(m.iter_silver_rows("dsn", "T-001"))
        assert len(result) == 1


# ---------------------------------------------------------------------------
# load_tenant_dashboard
# ---------------------------------------------------------------------------

class TestTenantDashboardShapeDoc:
    def _mod(self):
        import gold.src.eden.load_tenant_dashboard as m
        m._cb = None
        return m

    def _row(self):
        return {
            "tenantid": "T-001",
            "silver_lsn": 2,
            "project_count": 15,
            "at_risk_count": 3,
            "total_outstanding": 25000.0,
            "active_projects": [{"projectId": "p-1"}],
        }

    def test_basic_shape(self):
        mod = self._mod()
        doc = mod.shape_doc(self._row(), _TS)
        assert doc["tenantId"] == "T-001"
        assert doc["_grain"] == "eden_tenant_dashboard"
        assert doc["_schemaVersion"] == 1

    def test_project_counts(self):
        mod = self._mod()
        doc = mod.shape_doc(self._row(), _TS)
        assert doc["projectCount"] == 15
        assert doc["atRiskCount"] == 3

    def test_total_outstanding(self):
        mod = self._mod()
        doc = mod.shape_doc(self._row(), _TS)
        assert doc["totalOutstanding"] == 25000.0

    def test_active_projects_default_empty(self):
        mod = self._mod()
        row = self._row()
        row["active_projects"] = None
        doc = mod.shape_doc(row, _TS)
        assert doc["activeProjects"] == []

    def test_next_milestone_placeholder_none(self):
        mod = self._mod()
        doc = mod.shape_doc(self._row(), _TS)
        assert doc["nextMilestone"] is None


@pytest.mark.skip(reason="quarantined: superseded by gold/tests/unit/silver_to_gold suite (drifted from cursor_store/shape_doc changes)")
class TestTenantDashboardRun:
    def _mod(self):
        import gold.src.eden.load_tenant_dashboard as m
        return m

    def _row(self):
        return {"tenantid": "T-001", "silver_lsn": 1, "project_count": 5,
                "at_risk_count": 1, "total_outstanding": 1000.0,
                "active_projects": []}

    def test_dry_run(self):
        mod = self._mod()
        with patch.object(mod, "fetch_tenant_rows", return_value=[self._row()]):
            rc = mod.run(_cfg(mod))
        assert rc == 0

    def test_fetch_returns_none(self):
        mod = self._mod()
        with patch.object(mod, "fetch_tenant_rows", return_value=[]):
            rc = mod.run(_cfg(mod))
        assert rc == 0

    def test_transient_error_returns_3(self):
        from pymongo.errors import AutoReconnect
        mod = self._mod()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        with patch.object(mod, "fetch_tenant_rows", return_value=[self._row()]), \
             patch.object(mod, "_write_with_retry", side_effect=AutoReconnect("x")), \
             patch("gold.src.eden.load_tenant_dashboard.MongoClient", return_value=mock_client):
            rc = mod.run(_cfg(mod, dry_run=False))
        assert rc == 3


# ---------------------------------------------------------------------------
# load_bid_context
# ---------------------------------------------------------------------------

class TestBidContextShapeDoc:
    def _mod(self):
        import gold.src.eden.load_bid_context as m
        m._cb = None
        return m

    def _row(self):
        return {
            "tenantid": "T-001",
            "project_id": "p-1",
            "is_deleted": False,
            "silver_lsn": 7,
            "question_template_version": "v2",
            "answers_data": None,
            "answers_version": None,
            "total_questions": 3,
            "template_items": [],
        }

    def test_basic_shape(self):
        mod = self._mod()
        doc = mod.shape_doc(self._row(), _TS)
        assert doc["tenantId"] == "T-001"
        assert doc["projectId"] == "p-1"
        assert doc["_grain"] == "eden_bid_context"
        assert doc["_schemaVersion"] == 1

    def test_template_version(self):
        mod = self._mod()
        # totalQuestions derives from the parsed ODM/SDM template, so supply a
        # 3-item question_template_data blob (the empty default _row yields 0).
        row = self._row()
        row["question_template_data"] = {"SDM": {"Building": {"TemplateVersion": [{
            "ItemGroupDef": [{"ItemRef": [{"ItemOID": f"Q{i}", "Mandatory": "No"}
                                          for i in range(1, 4)]}],
            "ItemDef": [{"OID": f"Q{i}",
                         "Question": {"TranslatedText": {"text": f"q{i}"}},
                         "DataType": "text"} for i in range(1, 4)],
        }]}}}
        doc = mod.shape_doc(row, _TS)
        assert doc["questionTemplateVersion"] == "v2"
        assert doc["totalQuestions"] == 3

    def test_answered_count_empty(self):
        mod = self._mod()
        doc = mod.shape_doc(self._row(), _TS)
        assert doc["answeredCount"] == 0
        assert doc["answeredItems"] == []

    def test_null_answers_data(self):
        mod = self._mod()
        doc = mod.shape_doc(self._row(), _TS)
        assert doc["nextRequiredItem"] is None
        assert doc["visibleUnanswered"] == []


@pytest.mark.skip(reason="quarantined: superseded by gold/tests/unit/silver_to_gold suite (drifted from cursor_store/shape_doc changes)")
class TestBidContextRun:
    def _mod(self):
        import gold.src.eden.load_bid_context as m
        m._cb = None
        return m

    def test_dry_run(self):
        mod = self._mod()
        rows = [{"tenantid": "T-001", "project_id": "p-1",
                 "silver_lsn": 1, "is_deleted": False,
                 "question_template_version": "v2",
                 "answers_data": None, "answers_version": None,
                 "total_questions": 0, "template_items": []}]
        with patch.object(mod, "iter_silver_rows", return_value=iter(rows)):
            rc = mod.run(_cfg(mod))
        assert rc == 0

    def test_empty(self):
        mod = self._mod()
        with patch.object(mod, "iter_silver_rows", return_value=iter([])):
            rc = mod.run(_cfg(mod))
        assert rc == 0


class TestBidContextIterSilverRows:
    def test_yields_rows(self):
        import gold.src.eden.load_bid_context as m
        rows = [{"tenantid": "T-001", "project_id": "p-1"}]
        conn, cur = _make_conn(rows)
        cur.__iter__ = MagicMock(return_value=iter(rows))
        with patch("psycopg2.connect", return_value=conn):
            result = list(m.iter_silver_rows("dsn", "T-001"))
        assert len(result) == 1

    def test_empty(self):
        import gold.src.eden.load_bid_context as m
        conn, cur = _make_conn([])
        cur.__iter__ = MagicMock(return_value=iter([]))
        with patch("psycopg2.connect", return_value=conn):
            result = list(m.iter_silver_rows("dsn", "T-001"))
        assert result == []
