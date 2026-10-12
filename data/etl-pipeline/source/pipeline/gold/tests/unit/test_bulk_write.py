"""Unit tests for bulk_write_with_retry across loaders using mocked pymongo."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _make_cfg(module):
    """Build a minimal LoaderConfig for the given loader module."""
    LoaderConfig = module.LoaderConfig
    fields = LoaderConfig.__dataclass_fields__
    kwargs = dict(
        mongo_uri="mongodb://localhost:27017",
        pg_dsn="postgresql://localhost/test",
        db_name="test_db",
        tenantid="T-001",
        batch_size=10,
        max_retries=3,
        retry_backoff_s=0.0,
        dry_run=True,
    )
    return LoaderConfig(**kwargs)


def _make_stats(module):
    return module.WriteStats()


def _fake_ops(n=2):
    from pymongo import UpdateOne
    return [MagicMock(spec=UpdateOne) for _ in range(n)]


# ---------------------------------------------------------------------------
# Eden loaders (circuit-breaker variant)
# ---------------------------------------------------------------------------

def _eden_mod(import_path: str):
    import importlib
    m = importlib.import_module(import_path)
    m._cb = None
    return m


class TestBulkWriteRetryProjectHealth:
    IMPORT = "gold.src.eden.load_project_health"

    def test_success(self):
        mod = _eden_mod(self.IMPORT)
        coll = MagicMock()
        coll.full_name = "gold_eden.project_health"
        res = MagicMock(); res.upserted_count = 2; res.modified_count = 0; res.matched_count = 2
        coll.bulk_write.return_value = res
        stats = _make_stats(mod)
        mod.bulk_write_with_retry(coll, _fake_ops(2), _make_cfg(mod), stats)
        assert stats.upserted == 2
        assert stats.batches == 1

    def test_bulk_write_error(self):
        from pymongo.errors import BulkWriteError
        mod = _eden_mod(self.IMPORT)
        coll = MagicMock(); coll.full_name = "test"
        coll.bulk_write.side_effect = BulkWriteError({"writeErrors": [{"e": "x"}]})
        stats = _make_stats(mod)
        mod.bulk_write_with_retry(coll, _fake_ops(), _make_cfg(mod), stats)
        assert stats.failed == 1

    def test_transient_raises(self):
        from pymongo.errors import AutoReconnect
        mod = _eden_mod(self.IMPORT)
        coll = MagicMock(); coll.full_name = "test"
        coll.bulk_write.side_effect = AutoReconnect("down")
        with pytest.raises(AutoReconnect):
            mod.bulk_write_with_retry(coll, _fake_ops(), _make_cfg(mod), _make_stats(mod))

    def test_operation_failure_raises(self):
        from pymongo.errors import OperationFailure
        mod = _eden_mod(self.IMPORT)
        coll = MagicMock(); coll.full_name = "test"
        coll.bulk_write.side_effect = OperationFailure("auth")
        with pytest.raises(OperationFailure):
            mod.bulk_write_with_retry(coll, _fake_ops(), _make_cfg(mod), _make_stats(mod))


class TestBulkWriteRetryInvoiceSummary:
    IMPORT = "gold.src.eden.load_invoice_summary"

    def test_success(self):
        mod = _eden_mod(self.IMPORT)
        coll = MagicMock(); coll.full_name = "test"
        res = MagicMock(); res.upserted_count = 1; res.modified_count = 0; res.matched_count = 1
        coll.bulk_write.return_value = res
        stats = _make_stats(mod)
        mod.bulk_write_with_retry(coll, _fake_ops(), _make_cfg(mod), stats)
        assert stats.batches == 1

    def test_transient_raises(self):
        from pymongo.errors import NetworkTimeout
        mod = _eden_mod(self.IMPORT)
        coll = MagicMock(); coll.full_name = "test"
        coll.bulk_write.side_effect = NetworkTimeout("x")
        with pytest.raises(NetworkTimeout):
            mod.bulk_write_with_retry(coll, _fake_ops(), _make_cfg(mod), _make_stats(mod))


class TestBulkWriteRetryTaskIntelligence:
    IMPORT = "gold.src.eden.load_task_intelligence"

    def test_success(self):
        mod = _eden_mod(self.IMPORT)
        coll = MagicMock(); coll.full_name = "test"
        res = MagicMock(); res.upserted_count = 3; res.modified_count = 0; res.matched_count = 3
        coll.bulk_write.return_value = res
        stats = _make_stats(mod)
        mod.bulk_write_with_retry(coll, _fake_ops(3), _make_cfg(mod), stats)
        assert stats.upserted == 3

    def test_bulk_error_increments_failed(self):
        from pymongo.errors import BulkWriteError
        mod = _eden_mod(self.IMPORT)
        coll = MagicMock(); coll.full_name = "test"
        coll.bulk_write.side_effect = BulkWriteError({"writeErrors": [{"e": "a"}, {"e": "b"}]})
        stats = _make_stats(mod)
        mod.bulk_write_with_retry(coll, _fake_ops(), _make_cfg(mod), stats)
        assert stats.failed == 2


class TestBulkWriteRetryProcurementStatus:
    IMPORT = "gold.src.eden.load_procurement_status"

    def test_success(self):
        mod = _eden_mod(self.IMPORT)
        coll = MagicMock(); coll.full_name = "test"
        res = MagicMock(); res.upserted_count = 1; res.modified_count = 1; res.matched_count = 1
        coll.bulk_write.return_value = res
        stats = _make_stats(mod)
        mod.bulk_write_with_retry(coll, _fake_ops(), _make_cfg(mod), stats)
        assert stats.modified == 1

    def test_operation_failure_raises(self):
        from pymongo.errors import OperationFailure
        mod = _eden_mod(self.IMPORT)
        coll = MagicMock(); coll.full_name = "test"
        coll.bulk_write.side_effect = OperationFailure("perm denied")
        with pytest.raises(OperationFailure):
            mod.bulk_write_with_retry(coll, _fake_ops(), _make_cfg(mod), _make_stats(mod))


class TestBulkWriteRetryBidContext:
    IMPORT = "gold.src.eden.load_bid_context"

    def test_success(self):
        mod = _eden_mod(self.IMPORT)
        coll = MagicMock(); coll.full_name = "test"
        res = MagicMock(); res.upserted_count = 0; res.modified_count = 2; res.matched_count = 2
        coll.bulk_write.return_value = res
        stats = _make_stats(mod)
        mod.bulk_write_with_retry(coll, _fake_ops(2), _make_cfg(mod), stats)
        assert stats.modified == 2

    def test_transient_raises(self):
        from pymongo.errors import ConnectionFailure
        mod = _eden_mod(self.IMPORT)
        coll = MagicMock(); coll.full_name = "test"
        coll.bulk_write.side_effect = ConnectionFailure("reset")
        with pytest.raises(ConnectionFailure):
            mod.bulk_write_with_retry(coll, _fake_ops(), _make_cfg(mod), _make_stats(mod))


# ---------------------------------------------------------------------------
# load_tenant_dashboard — _write_with_retry (single-doc variant, no _cb)
# ---------------------------------------------------------------------------

class TestWriteRetryTenantDashboard:
    def _mod(self):
        import gold.src.eden.load_tenant_dashboard as m
        return m

    def _cfg(self):
        return self._mod().LoaderConfig(
            mongo_uri="mongodb://localhost:27017",
            pg_dsn="postgresql://localhost/test",
            db_name="gold_eden",
            tenantid="T-001",
            batch_size=10,
            max_retries=3,
            retry_backoff_s=0.0,
            dry_run=False,
        )

    def test_success(self):
        mod = self._mod()
        coll = MagicMock()
        res = MagicMock(); res.upserted_count = 1; res.modified_count = 0
        coll.bulk_write.return_value = res
        stats = mod.WriteStats()
        doc = {"tenantId": "T-001", "_silverLsn": 1}
        mod._write_with_retry(coll, doc, self._cfg(), stats)
        assert stats.upserted == 1

    def test_bulk_write_error_increments_failed(self):
        from pymongo.errors import BulkWriteError
        mod = self._mod()
        coll = MagicMock()
        coll.bulk_write.side_effect = BulkWriteError({"writeErrors": [{"e": "dup"}]})
        stats = mod.WriteStats()
        doc = {"tenantId": "T-001", "_silverLsn": 1}
        mod._write_with_retry(coll, doc, self._cfg(), stats)
        assert stats.failed == 1

    def test_transient_raises(self):
        from pymongo.errors import AutoReconnect
        mod = self._mod()
        coll = MagicMock()
        coll.bulk_write.side_effect = AutoReconnect("down")
        stats = mod.WriteStats()
        doc = {"tenantId": "T-001", "_silverLsn": 1}
        with pytest.raises(AutoReconnect):
            mod._write_with_retry(coll, doc, self._cfg(), stats)

    def test_operation_failure_raises(self):
        from pymongo.errors import OperationFailure
        mod = self._mod()
        coll = MagicMock()
        coll.bulk_write.side_effect = OperationFailure("auth")
        stats = mod.WriteStats()
        doc = {"tenantId": "T-001", "_silverLsn": 1}
        with pytest.raises(OperationFailure):
            mod._write_with_retry(coll, doc, self._cfg(), stats)


# ---------------------------------------------------------------------------
# load_qa_recommendation — circuit-breaker variant
# ---------------------------------------------------------------------------

class TestBulkWriteRetryQaRecommendation:
    IMPORT = "gold.src.reference_project.load_qa_recommendation"

    def _mod(self):
        import importlib
        m = importlib.import_module(self.IMPORT)
        m._cb = None
        return m

    def test_success(self):
        mod = self._mod()
        coll = MagicMock()
        coll.full_name = "gold_qa_recommendation.qa_answer"
        res = MagicMock()
        res.upserted_count = 2
        res.modified_count = 0
        res.matched_count = 2
        coll.bulk_write.return_value = res
        stats = _make_stats(mod)
        mod.bulk_write_with_retry(coll, _fake_ops(2), _make_cfg(mod), stats)
        assert stats.upserted == 2
        assert stats.batches == 1

    def test_bulk_write_error_increments_failed(self):
        from pymongo.errors import BulkWriteError
        mod = self._mod()
        coll = MagicMock()
        coll.full_name = "gold_qa_recommendation.qa_answer"
        coll.bulk_write.side_effect = BulkWriteError({"writeErrors": [{"e": "dup"}]})
        stats = _make_stats(mod)
        mod.bulk_write_with_retry(coll, _fake_ops(), _make_cfg(mod), stats)
        assert stats.failed == 1

    def test_transient_raises_after_retries(self):
        from pymongo.errors import AutoReconnect
        mod = self._mod()
        coll = MagicMock()
        coll.full_name = "gold_qa_recommendation.qa_answer"
        coll.bulk_write.side_effect = AutoReconnect("down")
        with pytest.raises(AutoReconnect):
            mod.bulk_write_with_retry(coll, _fake_ops(), _make_cfg(mod), _make_stats(mod))

    def test_operation_failure_raises(self):
        from pymongo.errors import OperationFailure
        mod = self._mod()
        coll = MagicMock()
        coll.full_name = "gold_qa_recommendation.qa_answer"
        coll.bulk_write.side_effect = OperationFailure("auth")
        with pytest.raises(OperationFailure):
            mod.bulk_write_with_retry(coll, _fake_ops(), _make_cfg(mod), _make_stats(mod))


# ---------------------------------------------------------------------------
# load_estimate_snapshot — bulk_write_with_retry (no circuit breaker)
# ---------------------------------------------------------------------------

class TestBulkWriteRetryEstimateSnapshot:
    def _mod(self):
        import gold.src.bpe.load_estimate_snapshot as m
        return m

    def test_success(self):
        mod = self._mod()
        coll = MagicMock()
        res = MagicMock(); res.upserted_count = 1; res.modified_count = 0
        coll.bulk_write.return_value = res
        cfg = mod.LoaderConfig(
            mongo_uri="mongodb://localhost:27017", pg_dsn="postgresql://localhost/test",
            db_name="gold_bpe", tenantid="T-001", batch_size=10,
            max_retries=3, retry_backoff_s=0.0, dry_run=False,
        )
        stats = mod.WriteStats()
        mod.bulk_write_with_retry(coll, _fake_ops(), cfg, stats)
        assert stats.upserted == 1
        assert stats.batches == 1

    def test_bulk_write_error(self):
        from pymongo.errors import BulkWriteError
        mod = self._mod()
        coll = MagicMock()
        coll.bulk_write.side_effect = BulkWriteError({"writeErrors": [{"e": "dup"}]})
        cfg = mod.LoaderConfig(
            mongo_uri="mongodb://localhost:27017", pg_dsn="postgresql://localhost/test",
            db_name="gold_bpe", tenantid="T-001", batch_size=10,
            max_retries=3, retry_backoff_s=0.0, dry_run=False,
        )
        stats = mod.WriteStats()
        mod.bulk_write_with_retry(coll, _fake_ops(), cfg, stats)
        assert stats.failed == 1

    def test_transient_raises(self):
        from pymongo.errors import AutoReconnect
        mod = self._mod()
        coll = MagicMock()
        coll.bulk_write.side_effect = AutoReconnect("x")
        cfg = mod.LoaderConfig(
            mongo_uri="mongodb://localhost:27017", pg_dsn="postgresql://localhost/test",
            db_name="gold_bpe", tenantid="T-001", batch_size=10,
            max_retries=3, retry_backoff_s=0.0, dry_run=False,
        )
        with pytest.raises(AutoReconnect):
            mod.bulk_write_with_retry(coll, _fake_ops(), cfg, mod.WriteStats())

    def test_operation_failure_raises(self):
        from pymongo.errors import OperationFailure
        mod = self._mod()
        coll = MagicMock()
        coll.bulk_write.side_effect = OperationFailure("auth")
        cfg = mod.LoaderConfig(
            mongo_uri="mongodb://localhost:27017", pg_dsn="postgresql://localhost/test",
            db_name="gold_bpe", tenantid="T-001", batch_size=10,
            max_retries=3, retry_backoff_s=0.0, dry_run=False,
        )
        with pytest.raises(OperationFailure):
            mod.bulk_write_with_retry(coll, _fake_ops(), cfg, mod.WriteStats())


