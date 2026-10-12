"""Silver → Gold loader: workforce_status (Eden AI Assistant).

Produces one document per project summarising the labour assigned to it: the
distinct resources scheduled, employment mix (W2 vs 1099 contractor),
assignment status counts, scheduled duration, actual hours logged, and a
simple utilisation ratio.

Data model (verified against live Silver, 2026-06-15):
  silver.resource_schedule  — the assignment backbone (resource_id, task_id,
                              project_id, start/end, duration, status). Scoped
                              to a project via project_id.
  silver.resource           — the labour/person master (resource_uid, name,
                              type = W2 | 1099, unit, skills, status).
  silver.timelog            — actual hours (time_spent, status), linked to a
                              schedule via resource_schedule_id (no project_id
                              of its own, so hours are reached through the
                              schedule join).

  NOTE: there is NO silver.worker / crew / equipment / task_assignment table —
  those source tables are not materialised in Silver. "workforce" here means
  scheduled resources, not equipment.

  All joins are tenant-scoped (``_s_tenantid`` equality) because Silver source
  PKs repeat across tenants (composite PK); a join without the tenant predicate
  fans out across tenants.

Like the other Eden per-project loaders, the incremental newness check and
cursor track ``silver.project`` only — the streaming job (triggered by
resource_schedule / task CDC) owns real-time freshness; the batch path is
backfill / ``--rebuild``.

Upsert key: (tenantId, projectId)

Exit codes: 0 success · 2 config error · 3 load failure
"""

from __future__ import annotations

import argparse
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

COLLECTION = "workforce_status"
FEATURE_NAME = "eden"
_STEP_NAME = "load_workforce_status"
_contract_validator = ContractValidator("eden_workforce_status")
TRANSIENT_ERRORS = (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError,
                    CircuitOpenError)
log = logging.getLogger("eden.workforce_status_loader")


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
        p = argparse.ArgumentParser(description="Silver → Gold loader: workforce_status")
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
        p.add_argument("--max-docs",      type=int, default=int(os.environ.get("MAX_DOCS", "0")))
        p.add_argument("--rebuild",       action="store_true",
                       help="Force full re-aggregate from current Silver (ignore the "
                            "newness check) and do NOT advance the cursor.")
        ns = p.parse_args(argv)
        return LoaderConfig(
            mongo_uri=ns.mongo_uri or get_mongo_uri(),
            pg_dsn=ns.pg_dsn or get_postgres_dsn(),
            db_name=ns.db_name, tenantid=ns.tenant,
            batch_size=ns.batch_size, max_retries=ns.max_retries,
            retry_backoff_s=ns.retry_backoff, dry_run=ns.dry_run,
            max_docs=ns.max_docs,
            rebuild=ns.rebuild,
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


# One row per project — workforce aggregated from resource_schedule (assignments),
# resource (the person), and timelog (actual hours). All joins tenant-scoped.
_SILVER_QUERY = """
SELECT
    p._s_tenantid                                          AS tenantid,
    p._s_env                                                AS source_env,
    p._s_is_deleted                                        AS is_deleted,
    p._s_deleted_at                                        AS deleted_at,
    p._s_lsn                                               AS silver_lsn,
    p._s_silver_ts                                         AS project_silver_ts,
    p.id                                                   AS project_id,

    COALESCE(wf.headcount, 0)                              AS headcount,
    COALESCE(wf.scheduled_assignments, 0)                 AS scheduled_assignments,
    COALESCE(wf.active_assignments, 0)                     AS active_assignments,
    COALESCE(wf.completed_assignments, 0)                  AS completed_assignments,
    wf.scheduled_duration_total,
    wf.next_scheduled_start,
    wf.rs_silver_ts,
    wf.assigned_resources,

    COALESCE(hrs.hours_logged, 0)                          AS hours_logged,
    COALESCE(hrs.active_timelog_count, 0)                  AS active_timelog_count,
    hrs.timelog_silver_ts

FROM silver.project p

-- NOTE: silver.resource and silver.resource_schedule have no `invalid` column
-- (unlike silver.project) — only `_s_is_deleted` filters them. See freshness.py,
-- which omits `invalid = FALSE` from the resource_schedule watermark for the same
-- reason. `invalid = FALSE` here would raise UndefinedColumn and crash the loader.
LEFT JOIN LATERAL (
    SELECT
        COUNT(DISTINCT rs.resource_id)                                          AS headcount,
        COUNT(*) FILTER (WHERE rs.status = 'scheduled')                         AS scheduled_assignments,
        COUNT(*) FILTER (WHERE rs.status = 'active')                            AS active_assignments,
        COUNT(*) FILTER (WHERE rs.status = 'completed')                         AS completed_assignments,
        SUM(rs.duration)                                                        AS scheduled_duration_total,
        MIN(rs.start_time) FILTER (
            WHERE rs.start_time > NOW() AND rs.status = 'scheduled'
        )                                                                       AS next_scheduled_start,
        MAX(rs._s_silver_ts)                                                    AS rs_silver_ts,

        -- distinct resources scheduled on this project (capped detail list)
        (
            SELECT JSONB_AGG(
                JSONB_BUILD_OBJECT(
                    'resourceId',     ar.id::text,
                    'resourceUid',    ar.resource_uid,
                    'name',           ar.name,
                    'employmentType', ar.rtype,
                    'unit',           ar.unit,
                    'skills',         ar.skills,
                    'status',         ar.status
                ) ORDER BY ar.name NULLS LAST
            )
            FROM (
                SELECT DISTINCT
                    r.id, r.resource_uid, r.name, r.type AS rtype,
                    r.unit, r.skills, r.status
                FROM silver.resource_schedule rs2
                JOIN silver.resource r
                  ON r.id = rs2.resource_id
                 AND r._s_tenantid = rs2._s_tenantid
                 AND r._s_env = rs2._s_env
                WHERE rs2._s_tenantid  = p._s_tenantid
                  AND rs2._s_env       = p._s_env
                  AND rs2.project_id   = p.id
                  AND rs2._s_is_deleted = FALSE
                  AND r._s_is_deleted   = FALSE
                LIMIT 100
            ) ar
        )                                                                       AS assigned_resources
    FROM silver.resource_schedule rs
    WHERE rs._s_tenantid  = p._s_tenantid
      AND rs._s_env       = p._s_env
      AND rs.project_id   = p.id
      AND rs._s_is_deleted = FALSE
) wf ON TRUE

LEFT JOIN LATERAL (
    -- actual hours reached through the schedule join (timelog has no project_id)
    SELECT
        SUM(tl.time_spent)                                  AS hours_logged,
        COUNT(*) FILTER (WHERE tl.status = 'active')        AS active_timelog_count,
        MAX(tl._s_silver_ts)                                AS timelog_silver_ts
    FROM silver.timelog tl
    JOIN silver.resource_schedule rs3
      ON rs3.id = tl.resource_schedule_id
     AND rs3._s_tenantid = tl._s_tenantid
     AND rs3._s_env = tl._s_env
    WHERE rs3._s_tenantid  = p._s_tenantid
      AND rs3._s_env       = p._s_env
      AND rs3.project_id   = p.id
      AND rs3._s_is_deleted = FALSE
      AND tl._s_is_deleted  = FALSE
) hrs ON TRUE

WHERE p._s_tenantid  = %(tenantid)s
  AND p.invalid       = FALSE
ORDER BY p.id
"""


def _scoped_query(project_id: str | None) -> str:
    """Return the Silver query, optionally scoped to a single project.

    When project_id is provided, splice an ``AND p.id = %(project_id)s``
    predicate into the outer WHERE clause so the same tested SQL runs for one
    project — used by the real-time streaming job.
    """
    if project_id is None:
        return _SILVER_QUERY
    return _SILVER_QUERY.replace(
        "WHERE p._s_tenantid  = %(tenantid)s",
        "WHERE p._s_tenantid  = %(tenantid)s\n  AND p.id = %(project_id)s",
        1,
    )


def iter_silver_rows(
    pg_dsn: str, tenantid: str, max_docs: int = 0, project_id: str | None = None
) -> Iterator[dict]:
    conn = psycopg2.connect(pg_dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            params = {"tenantid": tenantid}
            if project_id is not None:
                params["project_id"] = project_id
            cur.execute(_scoped_query(project_id), params)
            rows = cur if max_docs <= 0 else (r for _, r in zip(range(max_docs), cur))
            for row in rows:
                yield dict(row)
    finally:
        conn.close()


def build_documents(
    pg_dsn: str,
    tenantid: str,
    gold_ts: datetime,
    project_id: str | None = None,
    max_docs: int = 0,
) -> list[dict]:
    """Build shaped Gold documents for a tenant (or a single project).

    Reused by the real-time Eden streaming job (project_id set).
    """
    return [
        shape_doc(row, gold_ts)
        for row in iter_silver_rows(pg_dsn, tenantid, max_docs, project_id)
    ]


def _has_new_silver_rows(pg_dsn: str, tenantid: str, since: datetime) -> bool:
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM silver.project"
            "  WHERE _s_tenantid = %s AND _s_silver_ts > %s"
            ")",
            (tenantid, since),
        )
        return bool(cur.fetchone()[0])


def _get_max_silver_ts(pg_dsn: str, tenantid: str) -> datetime:
    # MIN of each _s_env's own MAX, not the overall MAX — see the canonical
    # loaders' get_max_silver_ts_per_env for why: a shared cursor advanced to
    # the fastest-moving env's watermark would mask a later update in a
    # slower-moving env still below that watermark.
    return get_max_silver_ts_per_env(pg_dsn, "project", "_s_tenantid", tenantid)


def _to_ts(v) -> "datetime | None":
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo is not None else v.replace(tzinfo=timezone.utc)
    return None


def _iso(v) -> "datetime | None":
    """Despite the name (kept to minimize call-site churn), returns a native
    datetime, not a string — every Gold date field must be a native BSON date."""
    return _to_ts(v)


def _float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def shape_doc(row: dict, gold_ts: datetime) -> dict:
    assigned = row.get("assigned_resources") or []
    # Employment mix from the (uncapped in practice — 28 resources system-wide,
    # 100-row cap) distinct resource list.
    w2_count = sum(1 for a in assigned if a.get("employmentType") == "W2")
    contractor_count = sum(1 for a in assigned if a.get("employmentType") == "1099")

    scheduled_dur = _float(row.get("scheduled_duration_total")) or 0.0
    # Floored at 0 — silver.timelog.time_spent can sum slightly negative for a
    # project (correction/adjustment entries), which the Gold schema validator
    # (minimum: 0) rejects outright. Treat a negative sum as no net hours logged.
    hours_logged = max(_float(row.get("hours_logged")) or 0.0, 0.0)
    # NOTE: resource_schedule.duration and timelog.time_spent unit alignment is
    # unconfirmed — utilisation is approximate. Null when nothing scheduled.
    utilization_pct = (
        round(hours_logged / scheduled_dur * 100, 2) if scheduled_dur > 0 else None
    )

    watermarks = {
        "project":           _to_ts(row.get("project_silver_ts")),
        "resource_schedule": _to_ts(row.get("rs_silver_ts")),
        "timelog":           _to_ts(row.get("timelog_silver_ts")),
    }
    valid_wm = [ts for ts in watermarks.values() if ts is not None]
    lag_ms = int((gold_ts - min(valid_wm)).total_seconds() * 1000) if valid_wm else 0

    doc = {
        "tenantId":               row["tenantid"],
        "projectId":              str(row["project_id"]),
        "_grain":                 "eden_workforce_status",
        "_schemaVersion":         1,
        "_silverLsn":             int(row.get("silver_lsn") or 0),
        "isDeleted":              bool(row.get("is_deleted") or False),
        "deletedAt":              parse_ts(row.get("deleted_at")),
        "dataAsOf":               gold_ts,
        "headcount":              int(row.get("headcount") or 0),
        "w2Count":                w2_count,
        "contractorCount":        contractor_count,
        "scheduledAssignments":   int(row.get("scheduled_assignments") or 0),
        "activeAssignments":      int(row.get("active_assignments") or 0),
        "completedAssignments":   int(row.get("completed_assignments") or 0),
        "scheduledDurationTotal": scheduled_dur,
        "hoursLogged":            hours_logged,
        "utilizationPct":         utilization_pct,
        "activeTimelogCount":     int(row.get("active_timelog_count") or 0),
        "nextScheduledStart":     _iso(row.get("next_scheduled_start")),
        "assignedResources":      assigned,
        "_silverWatermarks":      watermarks,
        "lafieldcraft":                  lag_ms,
    }
    doc.update(lineage_fields(FEATURE_NAME, gold_ts, iso=False,
                              source_updated_at=watermarks.get("project"),
                              source_env=row.get("source_env")))
    return doc


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
        except TRANSIENT_ERRORS:
            if attempt == cfg.max_retries:
                raise
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


def run(cfg: LoaderConfig) -> int:
    log.info("loader start | db=%s.%s tenant=%s dry_run=%s",
             cfg.db_name, COLLECTION, cfg.tenantid, cfg.dry_run)
    last_ts = read_cursor(cfg.pg_dsn, _STEP_NAME, cfg.tenantid)
    if cfg.rebuild:
        log.info("rebuild mode | full re-aggregate from current Silver; cursor will NOT be advanced")
    elif not _has_new_silver_rows(cfg.pg_dsn, cfg.tenantid, last_ts):
        log.info("no new Silver rows since %s — skipping full re-aggregate", last_ts.isoformat())
        return 0
    gold_ts = datetime.now(timezone.utc)
    stats = WriteStats()
    client: MongoClient | None = None
    coll: Collection | None = None
    if not cfg.dry_run:
        client = MongoClient(cfg.mongo_uri, serverSelectionTimeoutMS=15_000,
                             connectTimeoutMS=15_000, socketTimeoutMS=60_000,
                             retryWrites=False, appname="eden-workforce-status")
        coll = client[cfg.db_name][COLLECTION]

    pending: list[dict] = []
    seen = 0

    def flush() -> None:
        if not pending:
            return
        for d in pending:
            _contract_validator.validate(d, COLLECTION)
        ops = [UpdateOne({"tenantId": d["tenantId"], "projectId": d["projectId"], "sourceEnv": d.get("sourceEnv"), "$or": [{"_silverLsn": {"$exists": False}}, {"_silverLsn": {"$lte": d.get("_silverLsn", 0)}}]},
                         {"$set": d, "$setOnInsert": set_on_insert(gold_ts, iso=False)}, upsert=True) for d in pending]
        if ops and not cfg.dry_run and coll is not None:
            bulk_write_with_retry(coll, ops, cfg, stats)
        pending.clear()

    try:
        for row in iter_silver_rows(cfg.pg_dsn, cfg.tenantid, cfg.max_docs):
            if _shutdown:
                break
            seen += 1
            pending.append(shape_doc(row, gold_ts))
            if len(pending) >= cfg.batch_size:
                flush()
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
        log.info("rebuild: cursor intentionally NOT advanced (incremental watermark preserved)")
    elif stats.failed > 0:
        mark_error(cfg.pg_dsn, _STEP_NAME, cfg.tenantid, f"{stats.failed} bulk write failures")
    else:
        max_ts = _get_max_silver_ts(cfg.pg_dsn, cfg.tenantid)
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
