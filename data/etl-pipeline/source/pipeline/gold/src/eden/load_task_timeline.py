"""Silver → Gold loader: task_timeline (Eden AI Assistant — task versioned history).

Reads silver.task and appends one Gold document per retained task *version*
row (not one document per task, and not one per project). silver.task is
bi-temporal SCD-2: the source keeps every version_start_date/version_end_date
window as its own physical row, and one live project has been observed with
194k task rows — far too high-cardinality to embed as a `*History[]` array on
task_intelligence the way estimateHistory/scheduleRevisions/priceHistory are
embedded elsewhere (see docs/gold-layer/historical_data_strategy.md, "Cost /
performance risks: embedding kills point-read economics"). This collection is
the dedicated `*_timeline` alternative the strategy doc calls for instead.

Design consequence: each version row, once written here, is immutable (its
business fields never change after the fact — only silver.task's `invalid`
flag flips from FALSE to TRUE when a newer version supersedes it, which this
loader still needs to pick up as an update to the SAME already-written
document). So this loader is a plain incremental append/update keyed on
silver's own change-tracking column (`_s_silver_ts`), not a full per-tenant
re-aggregate like the other Eden loaders — there is nothing to "collapse", no
no-op filtering, and no per-project rebuild cost. Batch-only by design: no
streaming job feeds this collection (see step_registry.py's task_timeline
wiring) — recomputing on every Silver CDC event would amplify write volume far
beyond what the other, much lower-cardinality streaming Eden collections
tolerate.

Upsert key: (tenantId, taskUid, version, sourceEnv)
Exit codes: 0 success · 2 config error · 3 load failure
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse, urlunparse

import psycopg2
import psycopg2.extras
from pymongo import MongoClient, UpdateOne
from pymongo.collection import Collection
from pymongo.errors import (
    AutoReconnect, BulkWriteError, ConnectionFailure,
    NetworkTimeout, OperationFailure, ServerSelectionTimeoutError,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHARED_SRC = str(_REPO_ROOT / "shared")
_DATA_PIPELINE = str(_REPO_ROOT)
for _p in (_SHARED_SRC, _DATA_PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from secret_resolver import get_mongo_uri, get_postgres_dsn  # noqa: E402
from gold.src.monitoring.circuit_breaker import CircuitBreaker, CircuitOpenError  # noqa: E402
from gold.src.monitoring.contract_validator import ContractValidator  # noqa: E402
from gold.pipeline.cursor_store import (  # noqa: E402
    read_cursor, write_cursor, mark_error, get_max_silver_ts_per_env, EPOCH,
)
from gold.pipeline.lineage import lineage_fields, parse_ts, set_on_insert  # noqa: E402
from gold.src.flink.sinks.mongo_sink import build_lsn_guard  # noqa: E402

COLLECTION = "task_timeline"
FEATURE_NAME = "eden"
_STEP_NAME = "load_task_timeline"
_contract_validator = ContractValidator("eden_task_timeline")
TRANSIENT_ERRORS = (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError,
                    CircuitOpenError)
log = logging.getLogger("eden.task_timeline_loader")


@dataclass(frozen=True)
class LoaderConfig:
    mongo_uri: str
    pg_dsn: str
    db_name: str
    tenantid: str
    batch_size: int
    max_retries: int
    retry_backoff_s: float
    dry_run: bool
    max_docs: int = 0
    rebuild: bool = False

    @staticmethod
    def from_args_and_env(argv: list[str] | None = None) -> "LoaderConfig":
        p = argparse.ArgumentParser(description="Silver → Gold loader: task_timeline")
        p.add_argument("--mongo-uri",     default=None)
        p.add_argument("--pg-dsn",        default=None)
        p.add_argument("--db",            dest="db_name",
                       default=os.environ.get("GOLD_EDEN_DB", "gold_eden"))
        p.add_argument("--tenant", required=not bool(os.environ.get("TENANT_ID")),
                       default=os.environ.get("TENANT_ID"))
        p.add_argument("--batch-size",    type=int, default=int(os.environ.get("BATCH_SIZE", "200")))
        p.add_argument("--max-retries",   type=int, default=int(os.environ.get("MAX_RETRIES", "5")))
        p.add_argument("--retry-backoff", type=float, default=float(os.environ.get("RETRY_BACKOFF_S", "1.5")))
        p.add_argument("--dry-run",       action="store_true")
        p.add_argument("--max-docs",      type=int, default=int(os.environ.get("MAX_DOCS", "0")),
                       help="Throttle for the initial backfill — a full --rebuild of a "
                            "194k-row project is not a casual operation (see module docstring).")
        p.add_argument("--rebuild",       action="store_true",
                       help="Ignore the incremental cursor and re-read every version row "
                            "currently in Silver (one-time backfill). Does NOT advance the "
                            "cursor. Use --max-docs to throttle on large tenants.")
        ns = p.parse_args(argv)
        return LoaderConfig(
            mongo_uri=ns.mongo_uri or get_mongo_uri(),
            pg_dsn=ns.pg_dsn or get_postgres_dsn(),
            db_name=ns.db_name, tenantid=ns.tenant,
            batch_size=ns.batch_size, max_retries=ns.max_retries,
            retry_backoff_s=ns.retry_backoff, dry_run=ns.dry_run,
            max_docs=ns.max_docs, rebuild=ns.rebuild,
        )


def _mask_uri(uri: str) -> str:
    try:
        u = urlparse(uri)
        if u.password:
            netloc = f"{u.username}:***@{u.hostname}"
            if u.port:
                netloc += f":{u.port}"
            return urlunparse(u._replace(netloc=netloc))
        return uri
    except Exception:
        return re.sub(r"://[^@]+@", "://***@", uri)


# One row per retained task version — NOT filtered to invalid=FALSE (unlike
# every other Eden loader's silver.task query): a version being superseded
# (invalid flips TRUE) is itself a historical fact this collection must keep.
# Also not filtered to _s_is_deleted=FALSE — a task's soft-delete is a real
# timeline event, not something to hide from the history.
# Ordered by _s_silver_ts so incremental runs process strictly in the order
# Silver received them (relevant for the LSN guard on re-delivery).
_SILVER_QUERY = """
SELECT
    t._s_tenantid       AS tenantid,
    t._s_env            AS source_env,
    t._s_is_deleted      AS is_deleted,
    t._s_lsn             AS silver_lsn,
    t._s_silver_ts       AS recorded_at,
    t.id                 AS task_row_id,
    t.task_uid           AS task_uid,
    t.project_id         AS project_id,
    t.task_name          AS task_name,
    t.status             AS status,
    t.invalid            AS is_superseded,
    CAST(t.version AS INT) AS version,
    t.version_start_date AS version_start_date,
    t.version_end_date   AS version_end_date,
    t.start_time         AS start_time,
    t.end_time           AS end_time,
    t.assignees          AS assignees_raw,
    t.reason             AS reason,
    t.created_at         AS task_created_at,
    t.updated_at         AS task_updated_at
FROM silver.task t
WHERE t._s_tenantid  = %(tenantid)s
  AND t._s_silver_ts > %(since)s
ORDER BY t._s_silver_ts ASC, t.id ASC
"""


def iter_silver_rows(
    pg_dsn: str, tenantid: str, since: datetime, max_docs: int = 0
) -> Iterator[dict]:
    conn = psycopg2.connect(pg_dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(_SILVER_QUERY, {"tenantid": tenantid, "since": since})
            rows = cur if max_docs <= 0 else (r for _, r in zip(range(max_docs), cur))
            for row in rows:
                yield dict(row)
    finally:
        conn.close()


def _has_new_silver_rows(pg_dsn: str, tenantid: str, since: datetime) -> bool:
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM silver.task"
            "  WHERE _s_tenantid = %s AND _s_silver_ts > %s"
            ")",
            (tenantid, since),
        )
        return bool(cur.fetchone()[0])


def _get_max_silver_ts(pg_dsn: str, tenantid: str, floor: datetime) -> datetime:
    """Per-env MIN-of-MAX _s_silver_ts seen for this tenant, bounded below by
    ``floor``.

    MIN of each _s_env's own MAX, not the overall MAX — see the canonical
    loaders' get_max_silver_ts_per_env for why: a shared cursor advanced to
    the fastest-moving env's watermark would mask a later update in a
    slower-moving env still below that watermark.

    ``floor`` (the cursor value used for the just-completed read) guards
    against a table that has since gone quiet returning a stale/older max —
    matches the other loaders' "only advance forward" cursor discipline.
    """
    ts = get_max_silver_ts_per_env(pg_dsn, "task", "_s_tenantid", tenantid)
    return max(ts, floor)


def _to_ts(v) -> "datetime | None":
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo is not None else v.replace(tzinfo=timezone.utc)
    return None


def _maybe_json(v):
    """Parse a free-text column that may hold JSON (silver.task.assignees is
    TEXT holding ``[{"resourceUid","role","name"}, ...]``). Returns the parsed
    value when valid JSON, the original string when it isn't (never silently
    dropped), or None for empty/null input."""
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    if not isinstance(v, str):
        return v
    s = v.strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError, ValueError):
        return s


def shape_doc(row: dict, gold_ts: datetime) -> dict:
    task_uid = row.get("task_uid") or f"row-{row['task_row_id']}"
    doc = {
        "tenantId":         row["tenantid"],
        "taskUid":          task_uid,
        "version":          row.get("version") if row.get("version") is not None else 0,
        "taskId":           str(row["task_row_id"]),
        "projectId":        (str(row["project_id"]) if row.get("project_id") is not None else None),
        "taskName":         row.get("task_name"),
        "_grain":           "eden_task_timeline",
        "_schemaVersion":   1,
        "_silverLsn":       int(row.get("silver_lsn") or 0),
        "isDeleted":        bool(row.get("is_deleted") or False),
        "isSuperseded":     bool(row.get("is_superseded") or False),
        "dataAsOf":         gold_ts,
        "status":           row.get("status"),
        "versionStartDate": parse_ts(row.get("version_start_date")),
        "versionEndDate":   parse_ts(row.get("version_end_date")),
        "startTime":        parse_ts(row.get("start_time")),
        "endTime":          parse_ts(row.get("end_time")),
        "assignees":        _maybe_json(row.get("assignees_raw")) or [],
        "reason":           row.get("reason"),
        "taskCreatedAt":    parse_ts(row.get("task_created_at")),
        "taskUpdatedAt":    parse_ts(row.get("task_updated_at")),
    }
    doc.update(lineage_fields(FEATURE_NAME, gold_ts, iso=False,
                              source_updated_at=_to_ts(row.get("recorded_at")),
                              source_env=row.get("source_env")))
    return doc


def build_documents(
    pg_dsn: str, tenantid: str, gold_ts: datetime, since: datetime, max_docs: int = 0,
) -> list[dict]:
    """Build shaped Gold documents for every task version row newer than ``since``."""
    return [
        shape_doc(row, gold_ts)
        for row in iter_silver_rows(pg_dsn, tenantid, since, max_docs)
    ]


@dataclass
class WriteStats:
    upserted: int = 0
    modified: int = 0
    failed:   int = 0
    batches:  int = 0


_cb: CircuitBreaker | None = None


def bulk_write_with_retry(coll: Collection, ops: list[UpdateOne],
                          cfg: LoaderConfig, stats: WriteStats) -> None:
    delay = cfg.retry_backoff_s
    for attempt in range(1, cfg.max_retries + 1):
        try:
            global _cb
            if _cb is None:
                _cb = CircuitBreaker(name=coll.full_name)
            res = _cb.call(lambda: coll.bulk_write(ops, ordered=False))
            stats.upserted += res.upserted_count or 0
            stats.modified += res.modified_count or 0
            stats.batches  += 1
            return
        except BulkWriteError as exc:
            errors = exc.details.get("writeErrors", []) if exc.details else []
            stats.failed += len(errors)
            stats.batches += 1
            return
        except TRANSIENT_ERRORS as exc:
            if attempt == cfg.max_retries:
                raise
            log.warning("transient error attempt %d/%d: %s — retry %.1fs",
                        attempt, cfg.max_retries, exc, delay)
            time.sleep(delay)
            delay *= 2
        except OperationFailure as exc:
            log.error("operation failure: %s", exc)
            raise


_shutdown = False


def _install_signal_handlers() -> None:
    def _handler(signum, _frame):
        global _shutdown
        _shutdown = True
    signal.signal(signal.SIGINT, _handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handler)


def _upsert_op(d: dict, gold_ts: datetime) -> UpdateOne:
    base_filter = {
        "tenantId": d["tenantId"], "taskUid": d["taskUid"], "version": d["version"],
        "sourceEnv": d.get("sourceEnv"),
    }
    return UpdateOne(
        build_lsn_guard(base_filter, d.get("_silverLsn")),
        {"$set": d, "$setOnInsert": set_on_insert(gold_ts, iso=False)},
        upsert=True,
    )


def run(cfg: LoaderConfig) -> int:
    log.info("loader start | db=%s.%s tenant=%s dry_run=%s rebuild=%s",
             cfg.db_name, COLLECTION, cfg.tenantid, cfg.dry_run, cfg.rebuild)
    last_ts = EPOCH if cfg.rebuild else read_cursor(cfg.pg_dsn, _STEP_NAME, cfg.tenantid)
    if cfg.rebuild:
        log.warning("rebuild mode | re-reading every task version row currently in Silver — "
                    "not a casual operation on a high-cardinality tenant; cursor will NOT be advanced")
    elif not _has_new_silver_rows(cfg.pg_dsn, cfg.tenantid, last_ts):
        log.info("no new task version rows since %s — skipping", last_ts.isoformat())
        return 0
    gold_ts = datetime.now(timezone.utc)
    stats = WriteStats()
    client: MongoClient | None = None
    coll: Collection | None = None
    if not cfg.dry_run:
        client = MongoClient(cfg.mongo_uri, serverSelectionTimeoutMS=15_000,
                             connectTimeoutMS=15_000, socketTimeoutMS=60_000,
                             retryWrites=False, appname="eden-task-timeline")
        coll = client[cfg.db_name][COLLECTION]

    pending: list[dict] = []
    seen = 0

    def flush() -> None:
        if not pending:
            return
        for d in pending:
            _contract_validator.validate(d, COLLECTION)
        ops = [_upsert_op(d, gold_ts) for d in pending]
        if ops and not cfg.dry_run and coll is not None:
            bulk_write_with_retry(coll, ops, cfg, stats)
        pending.clear()

    try:
        for row in iter_silver_rows(cfg.pg_dsn, cfg.tenantid, last_ts, cfg.max_docs):
            if _shutdown:
                break
            seen += 1
            pending.append(shape_doc(row, gold_ts))
            if len(pending) >= cfg.batch_size:
                flush()
                log.info("progress | seen=%d upserted=%d", seen, stats.upserted)
        flush()
    except TRANSIENT_ERRORS as exc:
        mark_error(cfg.pg_dsn, _STEP_NAME, cfg.tenantid, exc)
        log.error("aborting: %s", exc)
        return 3
    finally:
        if client:
            client.close()

    if cfg.dry_run:
        log.info("dry-run: cursor NOT advanced")
    elif cfg.rebuild:
        log.info("rebuild: cursor intentionally NOT advanced")
    elif stats.failed > 0:
        mark_error(cfg.pg_dsn, _STEP_NAME, cfg.tenantid, f"{stats.failed} bulk write failures")
    else:
        max_ts = _get_max_silver_ts(cfg.pg_dsn, cfg.tenantid, last_ts)
        if max_ts > last_ts:
            write_cursor(cfg.pg_dsn, _STEP_NAME, cfg.tenantid, max_ts)

    log.info("loader done | seen=%d upserted=%d failed=%d", seen, stats.upserted, stats.failed)
    return 0 if stats.failed == 0 else 3


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    _install_signal_handlers()
    try:
        cfg = LoaderConfig.from_args_and_env(argv)
    except RuntimeError as exc:
        log.error("config error: %s", exc)
        return 2
    return run(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
