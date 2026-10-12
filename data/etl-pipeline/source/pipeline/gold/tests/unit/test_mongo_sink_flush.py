"""Unit tests for the streaming MongoUpsertSink flush behaviour.

Covers the time-based flush (added so low-volume CDC streams don't sit buffered
until the batch fills), the existing size-based flush, and close() draining.
Uses a fake collection — no Mongo or Flink runtime required.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from gold.src.flink.sinks.mongo_sink import MongoUpsertSink  # noqa: E402


class FakeColl:
    """Records the size of each bulk_write call."""

    def __init__(self):
        self.batches: list[int] = []

    def bulk_write(self, ops, ordered=False):
        self.batches.append(len(ops))

        class _R:
            upserted_count = len(ops)
            modified_count = 0

        return _R()


def _make_sink(batch_size: int, flush_interval_ms: int, coll=None) -> MongoUpsertSink:
    """Construct a sink and start its flusher without a real MongoClient.

    Mimics open() (which creates the lock/event/thread on the task manager) but
    skips the MongoClient — these runtime resources are intentionally NOT created
    in __init__ so the sink stays picklable (see test_sink_is_picklable).
    """
    s = MongoUpsertSink(
        "gold_bpe", "feature_store", ["tenantId", "project_id"],
        batch_size=batch_size, flush_interval_ms=flush_interval_ms,
    )
    s._coll = coll or FakeColl()
    s._lock = threading.Lock()
    s._stop = threading.Event()
    # open() also sets these two, and map() reads BOTH on every record
    # (record["_env"] and setdefault("_pipelineRunId", ...)). Kept in sync with
    # open() deliberately: this helper exists to mimic it, so anything open()
    # starts setting has to be mirrored here or every test through it breaks
    # with an AttributeError that looks unrelated to the change that caused it.
    s._env = "dev"
    s._run_id = "stream:test:19700101T000000Z"
    if s.flush_interval_s > 0:
        s._flusher = threading.Thread(target=s._flush_loop, daemon=True)
        s._flusher.start()
    return s


class TestMongoUpsertSinkFlush:
    def test_single_doc_flushed_by_timer(self):
        """A lone buffered doc reaches the collection via the timer, not the batch."""
        s = _make_sink(batch_size=100, flush_interval_ms=150)
        try:
            s.map({"tenantId": "tn1", "project_id": 1175})
            assert s._coll.batches == [], "should not flush before batch fills"
            # Poll for the background timer flush instead of a fixed sleep, so the
            # test can't flake under load.
            deadline = time.time() + 3.0
            while not s._coll.batches and time.time() < deadline:
                time.sleep(0.02)
            assert s._coll.batches == [1], "timer should flush the single doc"
        finally:
            s.close()

    def test_size_based_flush_still_works(self):
        """Filling the batch flushes immediately, independent of the timer."""
        s = _make_sink(batch_size=3, flush_interval_ms=60_000)
        try:
            for i in range(3):
                s.map({"tenantId": "tn1", "project_id": i})
            assert s._coll.batches == [3]
        finally:
            s.close()

    def test_close_drains_partial_buffer(self):
        s = _make_sink(batch_size=100, flush_interval_ms=60_000)
        s.map({"tenantId": "tn1", "project_id": 99})
        s.close()
        assert s._coll.batches == [1]

    def test_gold_updated_at_stamped(self):
        # Was "_etlUpdatedAt" until 2026-07-12 (consolidated into goldUpdatedAt
        # — see gold/pipeline/lineage.py's "Consolidation" note).
        s = _make_sink(batch_size=1, flush_interval_ms=0)
        rec = s.map({"tenantId": "tn1", "project_id": 7})
        assert "goldUpdatedAt" in rec
        s.close()

    def test_audit_fields_stamped_on_stream_docs(self):
        # Auditability: stream-written docs get createdAt + _pipelineRunId centrally.
        s = _make_sink(batch_size=1, flush_interval_ms=0)
        s._run_id = "run-xyz"
        rec = s.map({"tenantId": "tn1", "project_id": 7})
        assert "goldUpdatedAt" in rec and "createdAt" in rec
        assert rec["_pipelineRunId"] == "run-xyz"
        s.close()

    def test_existing_audit_fields_not_overwritten(self):
        # A doc that already carries createdAt / _pipelineRunId (e.g. from a loader)
        # keeps its own values (setdefault).
        s = _make_sink(batch_size=1, flush_interval_ms=0)
        s._run_id = "run-xyz"
        rec = s.map({"tenantId": "tn1", "project_id": 7,
                     "createdAt": "orig", "_pipelineRunId": "loader-run"})
        assert rec["createdAt"] == "orig"
        assert rec["_pipelineRunId"] == "loader-run"
        s.close()

    def test_timer_failure_rebuffers_docs(self):
        """A raised write in the timer path must not lose docs (re-buffered)."""
        from pymongo.errors import AutoReconnect

        coll = FakeColl()
        calls = {"n": 0}

        def flaky_bulk_write(ops, ordered=False):
            calls["n"] += 1
            if calls["n"] == 1:
                raise AutoReconnect("down")
            return FakeColl().bulk_write(ops)

        coll.bulk_write = flaky_bulk_write
        s = _make_sink(batch_size=100, flush_interval_ms=120, coll=coll)
        try:
            s.map({"tenantId": "tn1", "project_id": 1})
            # First tick raises after retries; doc is re-buffered, later ticks retry.
            time.sleep(1.2)
            assert calls["n"] >= 2, "doc should be retried, not dropped"
        finally:
            s.close()

    def test_no_flusher_thread_when_interval_zero(self):
        s = _make_sink(batch_size=10, flush_interval_ms=0)
        assert s._flusher is None
        s.close()

    def test_bulk_write_error_records_sink_dropped_audit(self, monkeypatch):
        """A genuine BulkWriteError must audit sink_dropped AND propagate.

        The audit half is unchanged. The propagation half is the newer contract
        (mongo_sink._write): a real write failure re-raises so _flush(fail_fast=
        True) fails the Flink task and the batch is replayed from checkpoint.
        Swallowing it here would permanently drop those documents with no replay
        opportunity, which is why this test asserts the raise rather than a
        quiet return. Dup-key (11000) is the benign case and is covered
        separately by test_dup_key_error_is_benign_not_audited.
        """
        from pymongo.errors import BulkWriteError

        rec = MagicMock()
        monkeypatch.setattr("gold.pipeline.audit.record_stream_event", rec)
        monkeypatch.setattr("gold.src.monitoring.metrics.record_loader_run",
                            MagicMock(), raising=False)

        class BoomColl:
            def bulk_write(self, ops, ordered=False):
                raise BulkWriteError({"writeErrors": [{"errmsg": "dup key"}]})

        s = _make_sink(batch_size=1, flush_interval_ms=0, coll=BoomColl())
        s._uri = "mongodb://stub"
        # batch=1 → map() flushes inline → _write raises → propagates out of map()
        with pytest.raises(BulkWriteError):
            s.map({"tenantId": "tn1", "project_id": 1175})
        # fail_fast=True does not re-buffer, so close() has nothing left to drain.
        s.close()

        rec.assert_called_once()
        kw = rec.call_args.kwargs
        assert kw["event_type"] == "sink_dropped"
        assert kw["tenant_id"] == "tn1"
        assert kw["feature"] == "feature_store"
        assert kw["project_id"] == "1175"

    def test_lsn_guard_added_to_filter(self):
        """A doc carrying _silverLsn gets the $or/$lte guard in its upsert filter so
        a stale recompute can't clobber newer Gold data, while an equal-LSN retry
        (e.g. a --rebuild re-run against unchanged Silver) still matches and updates
        in place instead of hitting a duplicate-key upsert-insert (mirrors batch loaders)."""
        captured: dict = {}

        class CaptureColl:
            def bulk_write(self, ops, ordered=False):
                captured["ops"] = ops

                class _R:
                    upserted_count = len(ops)
                    modified_count = 0

                return _R()

        s = _make_sink(batch_size=1, flush_interval_ms=0, coll=CaptureColl())
        s.map({"tenantId": "tn1", "project_id": 7, "_silverLsn": 42})
        s.close()
        flt = captured["ops"][0]._filter
        assert flt["tenantId"] == "tn1" and flt["project_id"] == 7
        assert flt["$or"] == [
            {"_silverLsn": {"$exists": False}},
            {"_silverLsn": {"$lte": 42}},
        ]

    def test_doc_missing_filter_field_is_skipped(self):
        """A doc missing a filter-key field is dropped, not written with a partial
        filter that would match (and clobber) an unintended document."""
        coll = FakeColl()
        s = _make_sink(batch_size=2, flush_interval_ms=0, coll=coll)
        s.map({"tenantId": "tn1"})                       # missing project_id → skipped
        s.map({"tenantId": "tn1", "project_id": 5})       # complete → written
        s.close()
        assert coll.batches == [1], "only the complete doc should reach the collection"

    def test_dup_key_error_is_benign_not_audited(self, monkeypatch):
        """A dup-key (11000) BulkWriteError is the LSN guard rejecting a stale write
        — benign, so no sink_dropped audit is emitted."""
        from pymongo.errors import BulkWriteError

        rec = MagicMock()
        monkeypatch.setattr("gold.pipeline.audit.record_stream_event", rec)
        monkeypatch.setattr("gold.src.monitoring.metrics.record_loader_run",
                            MagicMock(), raising=False)

        class DupColl:
            def bulk_write(self, ops, ordered=False):
                raise BulkWriteError({"writeErrors": [{"code": 11000, "errmsg": "dup"}],
                                      "nUpserted": 0, "nModified": 0})

        s = _make_sink(batch_size=1, flush_interval_ms=0, coll=DupColl())
        s._uri = "mongodb://stub"
        s.map({"tenantId": "tn1", "project_id": 1, "_silverLsn": 1})
        s.close()
        rec.assert_not_called()

    def test_heartbeat_throttled(self, monkeypatch):
        # Liveness heartbeat fires on a flush tick, then is throttled until the
        # interval elapses — proves the stream is alive during quiet periods.
        import gold.src.flink.sinks.mongo_sink as ms
        rec = MagicMock()
        monkeypatch.setattr("gold.src.monitoring.metrics.record_heartbeat", rec)
        s = _make_sink(batch_size=10, flush_interval_ms=0)   # no flusher thread
        s._maybe_heartbeat()
        assert rec.call_count == 1
        rec.assert_called_with("feature_store")
        s._maybe_heartbeat()                                  # within interval → skip
        assert rec.call_count == 1
        s._last_hb -= (ms._HEARTBEAT_INTERVAL_S + 1)          # force interval elapsed
        s._maybe_heartbeat()
        assert rec.call_count == 2
        s.close()

    def test_record_heartbeat_writes_overwrite_file(self, monkeypatch, tmp_path):
        # Heartbeat goes to its OWN file (overwritten, not appended → no growth).
        import gold.src.monitoring.metrics as metrics
        monkeypatch.setattr(metrics, "_METRICS_FILE", tmp_path / "gold_pipeline_metrics.prom")
        metrics.record_heartbeat("feature_store")
        hb = tmp_path / "gold_heartbeat_feature_store.prom"
        assert hb.exists()
        body = hb.read_text(encoding="utf-8")
        assert 'gold_stream_heartbeat_timestamp_seconds{component="feature_store"}' in body
        metrics.record_heartbeat("feature_store")             # second write overwrites
        assert body.count("gold_stream_heartbeat_timestamp_seconds{") == 1  # not appended

    def test_sink_is_picklable(self):
        """Flink cloudpickles the MapFunction to ship it to task managers, so a
        freshly constructed sink must serialize — the lock/event/thread must NOT
        be created until open(). Regression for 'cannot pickle _thread.lock'."""
        try:
            import cloudpickle as pickler
        except ImportError:
            import pickle as pickler
        s = MongoUpsertSink(
            "gold_bpe", "feature_store", ["tenantId", "project_id"],
            batch_size=100, flush_interval_ms=2000,
        )
        restored = pickler.loads(pickler.dumps(s))
        assert restored.batch_size == 100
        assert restored.flush_interval_s == 2.0
        assert restored._lock is None and restored._flusher is None
