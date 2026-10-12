"""Cosmos DB for MongoDB sink for Gold Flink streaming jobs.

Writes Gold feature documents to Cosmos DB vCore via pymongo
UpdateOne upsert — identical idempotent semantics to the batch loaders.

Designed as a Flink RichSinkFunction so the MongoClient is opened/closed
with the task lifecycle (one client per task, not per record).

Key design:
- Upsert on (tenantId, <entity_key_field>) — safe to replay.
- Batched writes: accumulates up to MONGO_SINK_BATCH_SIZE records, then flushes
  via bulk_write. A background flush thread also flushes every
  MONGO_SINK_FLUSH_INTERVAL_MS so low-volume CDC streams (e.g. one recompute
  per project close) don't sit buffered until the batch fills — mirrors the
  Silver job's SILVER_FLUSH_INTERVAL_MS. Remaining records flushed on close().
- Transient errors retried up to MONGO_SINK_MAX_RETRIES with exponential backoff.
- OperationFailure (schema validation reject) logged as error — record
  is dropped so the stream continues.

Env vars:
  MONGO_URI or KEY_VAULT_URL + secret "gold-mongo-uri"
  MONGO_SINK_BATCH_SIZE          default 100
  MONGO_SINK_FLUSH_INTERVAL_MS   default 500   (time-based flush for low volume)
  MONGO_SINK_MAX_RETRIES         default 3
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SHARED_SRC = str(_REPO_ROOT / "shared")
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from typing import Any
if _SHARED_SRC not in sys.path:
    sys.path.insert(0, _SHARED_SRC)

try:
    from pyflink.datastream.functions import MapFunction
    _FLINK_AVAILABLE = True
except ImportError:
    _FLINK_AVAILABLE = False
    class MapFunction:  # type: ignore[no-redef]
        def open(self, ctx): pass
        def close(self): pass

from gold.pipeline.lineage import cleanup_stale_order_no_siblings  # noqa: E402

log = logging.getLogger("gold.flink.sinks.mongo")


def build_lsn_guard(base_filter: dict, lsn: "int | None") -> dict:
    """Return base_filter extended with an LSN guard if lsn is present.

    Only applies the write when the stored document is not newer (or absent),
    preventing a stale recompute from clobbering newer Gold data. Uses $lte
    (not strict $lt) so a --rebuild retry against unchanged Silver data (same
    LSN as the already-written doc) still matches the filter and updates in
    place instead of falling through to an upsert-insert that collides with
    the unique index on the base_filter keys (duplicate-key write errors).
    Both the streaming sink and batch loaders must use this helper so the
    guard semantics stay in sync.
    """
    if lsn is None:
        return base_filter
    return {
        **base_filter,
        "$or": [{"_silverLsn": {"$exists": False}}, {"_silverLsn": {"$lte": lsn}}],
    }


_BATCH_SIZE      = int(os.environ.get("MONGO_SINK_BATCH_SIZE", "100"))
# Time-based flush ceiling. Lowered 2000 -> 500 (2026-08-25) to cut the tail of
# the Silver->Gold latency budget, where it was one of the two largest remaining
# terms once the query-scoping fixes landed.
#
# This costs less than it looks like it should, because the flush is
# "batch_size OR interval, whichever first":
#   * Under load the buffer hits MONGO_SINK_BATCH_SIZE in well under 500ms and
#     flushes on size, exactly as before -- this interval is never reached, so
#     high-volume throughput and RU cost per document are unchanged.
#   * At low volume (the CDC-recompute case: one project, a handful of docs) the
#     buffer never fills, so the interval IS the latency. There the change is
#     the same small write happening 1.5s sooner, not more writes.
# The middle case -- a steady trickle that neither fills the batch nor idles --
# does pay more requests for the same documents, so watch Cosmos RU after this
# if a tenant sits in that band.
_FLUSH_INTERVAL_MS = int(os.environ.get("MONGO_SINK_FLUSH_INTERVAL_MS", "500"))
_MAX_RETRIES     = int(os.environ.get("MONGO_SINK_MAX_RETRIES", "3"))
_BASE_DELAY      = 0.2
# Liveness heartbeat cadence — the flush thread ticks every flush_interval even
# when idle, so we emit a heartbeat (throttled) to prove the stream is alive when
# no CDC has arrived for a long time. Detects a stalled/dead consumer that would
# otherwise look identical to a legitimately quiet source.
_HEARTBEAT_INTERVAL_S = int(os.environ.get("GOLD_HEARTBEAT_INTERVAL_S", "30"))


class MongoUpsertSink(MapFunction):
    """Flink RichSinkFunction — upserts feature dicts into Cosmos DB.

    Args:
        db_name:         target Cosmos DB database name
        collection_name: target collection name
        filter_fields:   list of field names that form the upsert filter key
                         e.g. ["tenantId", "projectId"]
        batch_size:      records to accumulate before flushing
    """

    def __init__(
        self,
        db_name: str,
        collection_name: str,
        filter_fields: list[str],
        batch_size: int = _BATCH_SIZE,
        flush_interval_ms: int = _FLUSH_INTERVAL_MS,
    ) -> None:
        self.db_name         = db_name
        self.collection_name = collection_name
        self.filter_fields   = filter_fields
        self.batch_size      = batch_size
        self.flush_interval_s = max(0.0, flush_interval_ms / 1000.0)
        self._client         = None
        self._coll           = None
        self._uri            = None          # set in open(); used for drop audit
        self._run_id         = None          # set in open(); stamped on every doc
        self._last_hb        = 0.0           # monotonic ts of last liveness heartbeat
        self._buffer: list[dict[str, Any]] = []
        # Runtime-only resources (lock/event/thread) are created in open() — NOT
        # here — because Flink cloudpickles this MapFunction to ship it to task
        # managers, and a threading.Lock is not picklable.
        self._lock: threading.Lock | None = None
        self._stop: threading.Event | None = None
        self._flusher: threading.Thread | None = None

    def open(self, runtime_context) -> None:
        from pymongo import MongoClient
        from secret_resolver import get_mongo_uri  # type: ignore[import]
        self._lock = threading.Lock()
        self._stop = threading.Event()
        uri = get_mongo_uri()
        self._uri = uri          # kept for the dead-letter-as-audit write on drop
        # Stream run id for auditability (created_at + pipelineRunId on every doc).
        # Stable per task instance; override via env for a coordinated deploy id.
        self._run_id = os.environ.get("GOLD_STREAM_RUN_ID") or (
            f"stream:{self.collection_name}:"
            f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        )
        # Environment stamp — injected into every doc so non-prod envs sharing a
        # Cosmos cluster (gold_bpe contains dev+qa+stg+sbx docs) can be filtered.
        # Set PIPELINE_ENV in the AKS ConfigMap per environment.
        self._env = os.environ.get("PIPELINE_ENV", "dev")
        self._client = MongoClient(
            uri,
            serverSelectionTimeoutMS=15_000,
            connectTimeoutMS=15_000,
            socketTimeoutMS=60_000,
            retryWrites=False,
            appname=f"gold-flink-{self.collection_name}",
        )
        self._coll = self._client[self.db_name][self.collection_name]
        # Time-based flush so a single buffered doc (low-volume CDC recompute)
        # reaches Cosmos within flush_interval_s instead of waiting for the batch
        # to fill. Daemon thread — exits with the task.
        if self.flush_interval_s > 0:
            self._flusher = threading.Thread(
                target=self._flush_loop, name=f"mongo-flush-{self.collection_name}",
                daemon=True,
            )
            self._flusher.start()
        log.info(
            "MongoUpsertSink opened | db=%s coll=%s filter=%s batch=%d flush_interval_s=%.1f",
            self.db_name, self.collection_name, self.filter_fields,
            self.batch_size, self.flush_interval_s,
        )

    def __call__(self, record: dict[str, Any]) -> dict[str, Any]:
        return self.map(record)

    def map(self, record: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        # Was "_etlUpdatedAt" until 2026-07-12 (consolidated into goldUpdatedAt —
        # see gold/pipeline/lineage.py's "Consolidation" note). Batch loaders stamp
        # goldUpdatedAt via lineage_fields(); this streaming path stamps it directly
        # since it doesn't go through that helper.
        record["goldUpdatedAt"] = now
        record["_env"] = self._env
        # Auditability for stream-written docs (batch path stamps these via the
        # loaders): createdAt is preserved on first insert ($setOnInsert in _write);
        # _pipelineRunId ties the doc to this stream run. setdefault so a doc that
        # already carries them (e.g. from a loader) is not overwritten.
        record.setdefault("createdAt", now)
        record.setdefault("_pipelineRunId", self._run_id)
        with self._lock:
            self._buffer.append(record)
            full = len(self._buffer) >= self.batch_size
        if full:
            # Data-path flush: a persistent failure here should fail the job so
            # Flink replays from checkpoint (upserts are idempotent).
            self._flush(fail_fast=True)
        return record

    def _flush_loop(self) -> None:
        """Background timer: flush any buffered docs every flush_interval_s, and
        emit a liveness heartbeat (throttled) so a stalled/idle stream is
        detectable even with zero CDC."""
        while not self._stop.wait(self.flush_interval_s):
            try:
                self._flush(fail_fast=False)
            except Exception as exc:   # noqa: BLE001 — keep the flusher alive
                log.error("MongoUpsertSink timer flush error | coll=%s err=%s",
                          self.collection_name, exc)
            self._maybe_heartbeat()

    def _maybe_heartbeat(self) -> None:
        """Emit a liveness heartbeat at most once per _HEARTBEAT_INTERVAL_S.
        Runs on every flush tick (data or not), so it proves the stream is alive
        during long quiet periods. Best-effort; never breaks the flusher."""
        import time as _t
        now = _t.monotonic()
        if now - self._last_hb < _HEARTBEAT_INTERVAL_S:
            return
        self._last_hb = now
        try:
            from gold.src.monitoring.metrics import record_heartbeat
            record_heartbeat(self.collection_name)
        except Exception:  # noqa: BLE001
            pass

    def _flush(self, fail_fast: bool = True) -> None:
        # Snapshot + clear under the lock; do the Mongo I/O outside it so map()
        # is never blocked on a network round trip.
        with self._lock:
            if not self._buffer:
                return
            docs = self._buffer
            self._buffer = []
        try:
            self._write(docs)
        except Exception:
            # Timer path must not lose docs on a transient/raised failure —
            # re-buffer for the next tick / close(). Data path re-raises so the
            # job fails and replays from checkpoint.
            if fail_fast:
                raise
            with self._lock:
                self._buffer[0:0] = docs
            raise

    def _write(self, docs: list[dict[str, Any]]) -> None:
        if not docs:
            return
        from pymongo import UpdateOne
        from pymongo.errors import BulkWriteError, OperationFailure, AutoReconnect, NetworkTimeout

        ops = []
        # Collected alongside ops (see cleanup_stale_order_no_siblings' module
        # comment) -- only for docs that both carry an orderNo and are keyed by
        # projectId, i.e. project_health / canonical.project, not every
        # collection this sink serves. A set so a batch touching the same
        # (tenant, orderNo, projectId) more than once only cleans up once.
        order_no_cleanup_keys: set[tuple[Any, Any, Any, Any]] = set()
        for doc in docs:
            # Every filter-key field must be present. A partial filter (missing key)
            # would match an unintended document and clobber another tenant/project,
            # so drop + log the doc rather than emit a degraded filter.
            missing = [k for k in self.filter_fields if k not in doc]
            if missing:
                log.error(
                    "MongoUpsertSink dropping doc missing filter field(s) %s | coll=%s",
                    missing, self.collection_name,
                )
                continue
            f = build_lsn_guard(
                {k: doc[k] for k in self.filter_fields},
                doc.get("_silverLsn"),
            )
            # createdAt uses $setOnInsert — preserved on first insert, never
            # overwritten on subsequent updates.
            set_doc = {k: v for k, v in doc.items() if k != "createdAt"}
            update: dict = {"$set": set_doc}
            if "createdAt" in doc:
                update["$setOnInsert"] = {"createdAt": doc["createdAt"]}
            ops.append(UpdateOne(f, update, upsert=True))
            if (
                "projectId" in self.filter_fields
                and "tenantId" in self.filter_fields
                and doc.get("orderNo")
            ):
                order_no_cleanup_keys.add(
                    (doc["tenantId"], doc["orderNo"], doc["projectId"], doc.get("sourceEnv"))
                )
        if not ops:
            return

        import time as _time
        t0 = _time.monotonic()
        delay = _BASE_DELAY
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                result = self._coll.bulk_write(ops, ordered=False)
                upserted = result.upserted_count or 0
                modified = result.modified_count or 0
                log.debug(
                    "MongoUpsertSink flush | coll=%s upserted=%d modified=%d",
                    self.collection_name, upserted, modified,
                )
                # Emit Prometheus metrics for streaming job visibility
                try:
                    from gold.src.monitoring.metrics import record_loader_run
                    tenant = (docs[0] if docs else {}).get("tenantId", "unknown")
                    record_loader_run(
                        f"flink.{self.collection_name}", tenant,
                        upserted=upserted, failed=0,
                        elapsed_s=_time.monotonic() - t0,
                    )
                except Exception:
                    pass
                # Only on a fully clean bulk_write -- see cleanup_stale_order_no_
                # siblings' module comment on why a partial-failure pass (the
                # BulkWriteError branch below) skips this and waits for the next
                # clean pass instead.
                for tenant_id, order_no, project_id, source_env in order_no_cleanup_keys:
                    deleted = cleanup_stale_order_no_siblings(
                        self._coll, tenant_id, order_no, project_id, source_env
                    )
                    if deleted:
                        log.info(
                            "MongoUpsertSink cleaned up %d stale sibling doc(s) | "
                            "coll=%s tenant=%s orderNo=%s sourceEnv=%s currentProjectId=%s",
                            deleted, self.collection_name, tenant_id, order_no, source_env, project_id,
                        )
                return
            except BulkWriteError as exc:
                errors = exc.details.get("writeErrors", []) if exc.details else []
                # Dup-key (11000) = the LSN guard rejecting a stale/duplicate
                # recompute that lost the race to a newer write. The newer data is
                # already in Cosmos, so this is benign — NOT a data-quality drop.
                real = [e for e in errors if e.get("code") != 11000]
                stale = len(errors) - len(real)
                if stale:
                    log.debug(
                        "MongoUpsertSink %d stale write(s) skipped by LSN guard | coll=%s",
                        stale, self.collection_name,
                    )
                if real:
                    log.error(
                        "MongoUpsertSink bulk write partial failure | coll=%s errors=%d first=%s",
                        self.collection_name, len(real), real[0],
                    )
                tenant = (docs[0] if docs else {}).get("tenantId", "unknown")
                details = exc.details or {}
                upserted = (details.get("nUpserted", 0) or 0) + (details.get("nModified", 0) or 0)
                try:
                    from gold.src.monitoring.metrics import record_loader_run
                    record_loader_run(
                        f"flink.{self.collection_name}", tenant,
                        upserted=upserted, failed=len(real),
                        elapsed_s=_time.monotonic() - t0,
                    )
                except Exception:
                    pass
                # Dead-letter-as-audit: record only GENUINE drops (not LSN-guard
                # skips) to gold_audit.gold_event so operators can see which
                # collection/tenant dropped docs and re-run `--rebuild`. Best-effort.
                if real:
                    try:
                        from gold.pipeline.audit import record_stream_event
                        first = docs[0] if docs else {}
                        pid = first.get("projectId", first.get("project_id"))
                        record_stream_event(
                            getattr(self, "_uri", None) or "",
                            tenant_id=tenant,
                            feature=self.collection_name,
                            event_type="sink_dropped",
                            project_id=str(pid) if pid is not None else None,
                            attempts=len(real),
                            error_message=str(real[0]),
                        )
                    except Exception:
                        pass
                # Dup-key skips and successful ops are done. Genuine write failures
                # must propagate so _flush(fail_fast=True) can re-raise and let
                # Flink fail the task and replay from checkpoint. Swallowing them
                # here would permanently drop those docs with no replay opportunity.
                if real:
                    raise
            except (AutoReconnect, NetworkTimeout) as exc:
                if attempt == _MAX_RETRIES:
                    log.error(
                        "MongoUpsertSink failed after %d attempts | coll=%s err=%s",
                        attempt, self.collection_name, exc,
                    )
                    try:
                        from gold.src.monitoring.metrics import record_loader_run
                        record_loader_run(
                            f"flink.{self.collection_name}", "unknown",
                            upserted=0, failed=len(ops),
                            elapsed_s=_time.monotonic() - t0,
                        )
                    except Exception:
                        pass
                    raise
                log.warning(
                    "MongoUpsertSink transient error attempt %d/%d | coll=%s — retry %.1fs",
                    attempt, _MAX_RETRIES, self.collection_name, delay,
                )
                time.sleep(delay)
                delay *= 2
            except OperationFailure as exc:
                log.error(
                    "MongoUpsertSink OperationFailure (non-retryable) | coll=%s err=%s",
                    self.collection_name, exc,
                )
                raise

    def close(self) -> None:
        # Stop the background flusher, then drain whatever is buffered.
        if self._stop is not None:
            self._stop.set()
        if self._flusher is not None:
            self._flusher.join(timeout=max(2.0, self.flush_interval_s + 1.0))
        try:
            self._flush(fail_fast=False)
        except Exception as exc:
            log.error("MongoUpsertSink close flush error: %s", exc)
        # On a failed final drain, _flush re-buffers the docs it could not write;
        # closing the client below abandons them. Surface the count so the loss is
        # visible (and recoverable via reconcile/--rebuild) rather than silent.
        dropped = len(self._buffer)
        if dropped:
            log.error(
                "MongoUpsertSink close DROPPING %d unflushed doc(s) | db=%s coll=%s "
                "— recover via Silver→Gold reconcile or run_pipeline --rebuild",
                dropped, self.db_name, self.collection_name,
            )
            # Every other failure branch in this file (BulkWriteError,
            # AutoReconnect exhaustion) records this metric so operators get
            # alerted — without it, data loss on a task-manager shutdown
            # mid-outage (exactly when this path is exercised) is invisible to
            # any dashboard/alert.
            try:
                from gold.src.monitoring.metrics import record_loader_run
                tenant = (self._buffer[0] if self._buffer else {}).get("tenantId", "unknown")
                record_loader_run(
                    f"flink.{self.collection_name}", tenant,
                    upserted=0, failed=dropped,
                    elapsed_s=0.0,
                )
            except Exception:
                pass
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
        log.info("MongoUpsertSink closed | db=%s coll=%s", self.db_name, self.collection_name)
