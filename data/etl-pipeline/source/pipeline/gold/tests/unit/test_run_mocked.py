"""Unit tests for run() in Gold loaders using fully mocked infrastructure."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TS = datetime(2024, 6, 1, tzinfo=timezone.utc)


def _cfg(mod, *, dry_run: bool = True, batch_size: int = 10):
    fields = mod.LoaderConfig.__dataclass_fields__
    kwargs = dict(
        mongo_uri="mongodb://localhost:27017",
        pg_dsn="postgresql://localhost/test",
        db_name="test_db",
        tenantid="T-001",
        batch_size=batch_size,
        max_retries=3,
        retry_backoff_s=0.0,
        dry_run=dry_run,
    )
    if "max_docs" in fields:
        kwargs["max_docs"] = 0
    return mod.LoaderConfig(**kwargs)


def _mock_client():
    client = MagicMock()
    coll = MagicMock()
    res = MagicMock()
    res.upserted_count = 1
    res.modified_count = 0
    res.matched_count = 0
    coll.bulk_write.return_value = res
    coll.full_name = "test_db.test_coll"
    client.__getitem__ = MagicMock(return_value={
        "test_coll": coll,
    })
    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=coll)
    client.__getitem__ = MagicMock(return_value=db)
    return client, coll


_CONTRACT_OFF = {"GOLD_CONTRACT_VALIDATION": "off"}


# ---------------------------------------------------------------------------
# Eden: load_project_health
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="quarantined: superseded by gold/tests/unit/silver_to_gold suite (drifted from cursor_store/shape_doc changes)")
@pytest.mark.eden
class TestRunProjectHealth:
    def _mod(self):
        import gold.src.eden.load_project_health as m
        m._cb = None
        m._shutdown = False
        return m

    def _row(self):
        return {
            "tenantid": "T-001", "project_id": "p-1", "silver_lsn": 5,
            "is_deleted": False, "order_no": None, "project_name": "Test",
            "status": "ACTIVE", "total_task_count": 10, "done_task_count": 5,
            "overdue_task_count": 1, "unassigned_task_count": 2,
            "completion_pct": 50.0, "estimated_cost": 1000.0,
            "estimate_version": 1, "material_cost": 200.0, "worker_cost": 500.0,
            "tool_cost": 100.0, "overhead_cost": 100.0, "margin": 100.0,
            "committed_pos": 300.0, "committed_invoices": 100.0,
            "committed_spend": 400.0, "outstanding_invoices_total": 50.0,
            "slip_days": 0, "schedule_revised_count": 0,
            "job_startdate": None, "actual_job_startdate": None,
            "job_enddate": None, "actual_job_enddate": None,
            "p_silver_ts": None,
        }

    def test_dry_run_succeeds(self):
        mod = self._mod()
        with patch.object(mod, "iter_silver_rows", return_value=iter([self._row()])), \
             patch.dict(os.environ, _CONTRACT_OFF):
            rc = mod.run(_cfg(mod))
        assert rc == 0

    def test_dry_run_empty(self):
        mod = self._mod()
        with patch.object(mod, "iter_silver_rows", return_value=iter([])), \
             patch.dict(os.environ, _CONTRACT_OFF):
            rc = mod.run(_cfg(mod))
        assert rc == 0

    def test_wet_run_succeeds(self):
        mod = self._mod()
        client, coll = _mock_client()
        with patch.object(mod, "iter_silver_rows", return_value=iter([self._row()])), \
             patch.object(mod, "bulk_write_with_retry"), \
             patch(f"gold.src.eden.load_project_health.MongoClient", return_value=client), \
             patch.dict(os.environ, _CONTRACT_OFF):
            rc = mod.run(_cfg(mod, dry_run=False))
        assert rc == 0

    def test_transient_error_returns_3(self):
        from pymongo.errors import AutoReconnect
        mod = self._mod()
        with patch.object(mod, "iter_silver_rows", side_effect=AutoReconnect("down")), \
             patch.dict(os.environ, _CONTRACT_OFF):
            rc = mod.run(_cfg(mod))
        assert rc == 3

    def test_batch_flush(self):
        mod = self._mod()
        rows = [self._row() for _ in range(5)]
        with patch.object(mod, "iter_silver_rows", return_value=iter(rows)), \
             patch.dict(os.environ, _CONTRACT_OFF):
            rc = mod.run(_cfg(mod, batch_size=2))
        assert rc == 0


# ---------------------------------------------------------------------------
# Eden: load_invoice_summary
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="quarantined: superseded by gold/tests/unit/silver_to_gold suite (drifted from cursor_store/shape_doc changes)")
@pytest.mark.eden
class TestRunInvoiceSummary:
    def _mod(self):
        import gold.src.eden.load_invoice_summary as m
        m._cb = None
        m._shutdown = False
        return m

    def _row(self):
        return {
            "tenantid": "T-001", "project_id": "p-2", "silver_lsn": 3,
            "is_deleted": False,
            "total_invoiced": 2000.0, "total_paid": 1000.0,
            "outstanding_balance": 1000.0, "invoice_count": 3,
            "overdue_invoices": None, "upcoming_invoices": None,
            "total_received": 1000.0, "last_payment_amount": None,
            "last_payment_date": None, "last_payment_method": None,
        }

    def test_dry_run_succeeds(self):
        mod = self._mod()
        with patch.object(mod, "iter_silver_rows", return_value=iter([self._row()])), \
             patch.dict(os.environ, _CONTRACT_OFF):
            rc = mod.run(_cfg(mod))
        assert rc == 0

    def test_dry_run_empty(self):
        mod = self._mod()
        with patch.object(mod, "iter_silver_rows", return_value=iter([])), \
             patch.dict(os.environ, _CONTRACT_OFF):
            rc = mod.run(_cfg(mod))
        assert rc == 0

    def test_transient_returns_3(self):
        from pymongo.errors import NetworkTimeout
        mod = self._mod()
        with patch.object(mod, "iter_silver_rows", side_effect=NetworkTimeout("t")), \
             patch.dict(os.environ, _CONTRACT_OFF):
            rc = mod.run(_cfg(mod))
        assert rc == 3


# ---------------------------------------------------------------------------
# Eden: load_task_intelligence
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="quarantined: superseded by gold/tests/unit/silver_to_gold suite (drifted from cursor_store/shape_doc changes)")
@pytest.mark.eden
class TestRunTaskIntelligence:
    def _mod(self):
        import gold.src.eden.load_task_intelligence as m
        m._cb = None
        m._shutdown = False
        return m

    def _row(self):
        return {
            "tenantid": "T-001", "project_id": "p-3", "silver_lsn": 2,
            "is_deleted": False,
            "total_tasks": 8, "done_tasks": 4, "overdue_count": 1,
            "unassigned_count": 1, "overdue_tasks": None, "unassigned_tasks": None,
            "most_delayed_task": None, "completion_pct": 50.0,
            "on_track_count": 5, "at_risk_count": 1, "blocked_count": 0,
        }

    def test_dry_run_succeeds(self):
        mod = self._mod()
        with patch.object(mod, "iter_silver_rows", return_value=iter([self._row()])), \
             patch.dict(os.environ, _CONTRACT_OFF):
            rc = mod.run(_cfg(mod))
        assert rc == 0

    def test_wet_run(self):
        mod = self._mod()
        client, _ = _mock_client()
        with patch.object(mod, "iter_silver_rows", return_value=iter([self._row()])), \
             patch.object(mod, "bulk_write_with_retry"), \
             patch(f"gold.src.eden.load_task_intelligence.MongoClient", return_value=client), \
             patch.dict(os.environ, _CONTRACT_OFF):
            rc = mod.run(_cfg(mod, dry_run=False))
        assert rc == 0


# ---------------------------------------------------------------------------
# Eden: load_procurement_status
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="quarantined: superseded by gold/tests/unit/silver_to_gold suite (drifted from cursor_store/shape_doc changes)")
@pytest.mark.eden
class TestRunProcurementStatus:
    def _mod(self):
        import gold.src.eden.load_procurement_status as m
        m._cb = None
        m._shutdown = False
        return m

    def _row(self):
        return {
            "tenantid": "T-001", "project_id": "p-4", "silver_lsn": 1,
            "is_deleted": False,
            "total_bom_items": 5, "ordered_items": 3, "received_items": 2,
            "pending_items": 2, "total_bom_cost": 500.0, "committed_po_amount": 300.0,
            "pending_po_amount": 100.0, "po_count": 2, "overdue_po_items": None,
        }

    def test_dry_run_succeeds(self):
        mod = self._mod()
        with patch.object(mod, "iter_silver_rows", return_value=iter([self._row()])), \
             patch.dict(os.environ, _CONTRACT_OFF):
            rc = mod.run(_cfg(mod))
        assert rc == 0

    def test_dry_run_empty(self):
        mod = self._mod()
        with patch.object(mod, "iter_silver_rows", return_value=iter([])), \
             patch.dict(os.environ, _CONTRACT_OFF):
            rc = mod.run(_cfg(mod))
        assert rc == 0


# ---------------------------------------------------------------------------
# Eden: load_bid_context
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="quarantined: superseded by gold/tests/unit/silver_to_gold suite (drifted from cursor_store/shape_doc changes)")
@pytest.mark.eden
class TestRunBidContext:
    def _mod(self):
        import gold.src.eden.load_bid_context as m
        m._cb = None
        m._shutdown = False
        return m

    def _row(self):
        return {
            "tenantid": "T-001", "project_id": "p-5", "silver_lsn": 9,
            "is_deleted": False, "question_template_version": "v1",
            "answers_data": None, "answers_version": None,
            "total_questions": 5, "template_items": [],
        }

    def test_dry_run_succeeds(self):
        mod = self._mod()
        with patch.object(mod, "iter_silver_rows", return_value=iter([self._row()])), \
             patch.dict(os.environ, _CONTRACT_OFF):
            rc = mod.run(_cfg(mod))
        assert rc == 0

    def test_wet_run(self):
        mod = self._mod()
        client, _ = _mock_client()
        with patch.object(mod, "iter_silver_rows", return_value=iter([self._row()])), \
             patch.object(mod, "bulk_write_with_retry"), \
             patch(f"gold.src.eden.load_bid_context.MongoClient", return_value=client), \
             patch.dict(os.environ, _CONTRACT_OFF):
            rc = mod.run(_cfg(mod, dry_run=False))
        assert rc == 0


# ---------------------------------------------------------------------------
# BPE: load_estimate_snapshot
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="quarantined: superseded by gold/tests/unit/silver_to_gold suite (drifted from cursor_store/shape_doc changes)")
@pytest.mark.bpe
class TestRunEstimateSnapshot:
    def _mod(self):
        import gold.src.bpe.load_estimate_snapshot as m
        m._shutdown = False
        return m

    def _cfg(self, dry_run=True):
        mod = self._mod()
        return mod.LoaderConfig(
            mongo_uri="mongodb://localhost:27017",
            pg_dsn="postgresql://localhost/test",
            db_name="gold_bpe",
            tenantid="T-001",
            batch_size=10,
            max_retries=3,
            retry_backoff_s=0.0,
            dry_run=dry_run,
        )

    def _summary(self):
        return {
            "tenantId": "T-001",
            "projectId": "p-snap-1",
            "costRange": {"estimateLow": 1000, "estimateMid": 1500, "estimateHigh": 2000, "confidence": 0.8},
            "qaContext": {"answeredOids": ["oid1"], "answeredCount": 1},
            "mlMeta": {"modelVersion": "v1"},
            "project": {"sqFt": 2500},
            "services": ["roofing"],
        }

    def test_dry_run(self):
        mod = self._mod()
        fake_client = MagicMock()
        db = MagicMock()
        snap_coll = MagicMock()
        snap_coll.find.return_value = iter([])
        db.__getitem__ = MagicMock(return_value=snap_coll)
        fake_client.__getitem__ = MagicMock(return_value=db)
        fake_client.close = MagicMock()

        with patch.object(mod, "iter_estimate_summaries",
                          return_value=iter([self._summary()])), \
             patch("gold.src.bpe.load_estimate_snapshot.MongoClient",
                   return_value=fake_client):
            rc = mod.run(self._cfg(dry_run=True))
        assert rc == 0

    def test_wet_run_no_existing(self):
        mod = self._mod()
        fake_client = MagicMock()
        db = MagicMock()
        snap_coll = MagicMock()
        snap_coll.find.return_value = iter([])
        res = MagicMock(); res.upserted_count = 1; res.modified_count = 0
        snap_coll.bulk_write.return_value = res
        db.__getitem__ = MagicMock(return_value=snap_coll)
        fake_client.__getitem__ = MagicMock(return_value=db)
        fake_client.close = MagicMock()

        with patch.object(mod, "iter_estimate_summaries",
                          return_value=iter([self._summary()])), \
             patch("gold.src.bpe.load_estimate_snapshot.MongoClient",
                   return_value=fake_client):
            rc = mod.run(self._cfg(dry_run=False))
        assert rc == 0

    def test_empty_summaries(self):
        mod = self._mod()
        fake_client = MagicMock()
        db = MagicMock()
        snap_coll = MagicMock()
        snap_coll.find.return_value = iter([])
        db.__getitem__ = MagicMock(return_value=snap_coll)
        fake_client.__getitem__ = MagicMock(return_value=db)
        fake_client.close = MagicMock()

        with patch.object(mod, "iter_estimate_summaries", return_value=iter([])), \
             patch("gold.src.bpe.load_estimate_snapshot.MongoClient",
                   return_value=fake_client):
            rc = mod.run(self._cfg(dry_run=True))
        assert rc == 0

    def test_transient_error_returns_3(self):
        from pymongo.errors import AutoReconnect
        mod = self._mod()
        fake_client = MagicMock()
        db = MagicMock()
        snap_coll = MagicMock()
        snap_coll.find.return_value = iter([])
        db.__getitem__ = MagicMock(return_value=snap_coll)
        fake_client.__getitem__ = MagicMock(return_value=db)
        fake_client.close = MagicMock()

        with patch.object(mod, "iter_estimate_summaries",
                          side_effect=AutoReconnect("down")), \
             patch("gold.src.bpe.load_estimate_snapshot.MongoClient",
                   return_value=fake_client):
            rc = mod.run(self._cfg(dry_run=True))
        assert rc == 3




