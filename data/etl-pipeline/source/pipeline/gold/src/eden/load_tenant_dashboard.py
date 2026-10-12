"""Silver → Gold loader: tenant_dashboard (Eden AI Assistant).

Produces ONE document per (tenant, source_env) — not per project — a
cross-project summary used by Eden's project picker and dashboard queries.
A tenant confined to a single Silver source environment yields one document;
a tenant spanning multiple source environments (e.g. si_dev and datamart)
yields one document per environment.

Aggregates from silver.project, silver.task, silver.invoice,
silver.project_estimate, silver.project_schedule.

Upsert key: (tenantId, sourceEnv)
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
from gold.src.monitoring.contract_validator import ContractValidator  # noqa: E402
from gold.pipeline.cursor_store import (  # noqa: E402
    read_cursor, write_cursor, mark_error, get_max_silver_ts_per_env, EPOCH,
)
from gold.pipeline.lineage import lineage_fields, parse_ts, set_on_insert  # noqa: E402

COLLECTION = "tenant_dashboard"
FEATURE_NAME = "eden"
_STEP_NAME = "load_tenant_dashboard"
_contract_validator = ContractValidator("eden_tenant_dashboard")
TRANSIENT_ERRORS = (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError)
log = logging.getLogger("eden.tenant_dashboard_loader")


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
        p = argparse.ArgumentParser(description="Silver → Gold loader: tenant_dashboard")
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


# Single aggregated row per tenant
_SILVER_QUERY = """
SELECT
    p._s_tenantid                                                               AS tenantid,
    p._s_env                                                                     AS source_env,
    COUNT(DISTINCT p.id)                                                AS project_count,
    COUNT(DISTINCT p.id) FILTER (WHERE sched.slip_days > 0)            AS at_risk_count,
    COALESCE(SUM(inv_agg.outstanding), 0)                               AS total_outstanding,
    JSONB_AGG(
        JSONB_BUILD_OBJECT(
            'projectId',       p.id::text,
            'orderNo',         p.order_no,
            'status',          p.status,
            -- identifying fields for a project picker/overview — NOT the full
            -- project record (that's project_health.project); customer is raw
            -- JSON here, parsed into a display name in shape_doc().
            'customer',        p.customer,
            'trade',           p.trade,
            'location',        p.location,
            'completionPct',   task_agg.completion_pct,
            'overdueTaskCount',task_agg.overdue_task_count,
            'scheduleRisk',    CASE
                                   WHEN sched.slip_days IS NULL THEN 'unknown'
                                   WHEN sched.slip_days > 0     THEN 'at_risk'
                                   ELSE 'on_track'
                               END,
            'outstandingAmt',  inv_agg.outstanding
        )
        ORDER BY p.created_at DESC NULLS LAST
    )                                                                   AS active_projects,

    -- LSN high-water mark across all projects for this tenant
    MAX(p._s_lsn)                                                       AS silver_lsn_max,

    -- Per-table Silver freshness watermarks (tenant-level MAX)
    MAX(p._s_silver_ts)                                                 AS project_silver_ts,
    (SELECT MAX(_s_silver_ts) FROM silver.task
     WHERE _s_tenantid = p._s_tenantid AND _s_env = p._s_env AND _s_is_deleted = FALSE)      AS task_silver_ts,
    (SELECT MAX(_s_silver_ts) FROM silver.project_schedule
     WHERE _s_tenantid = p._s_tenantid AND _s_env = p._s_env AND _s_is_deleted = FALSE)      AS schedule_silver_ts,
    (SELECT MAX(_s_silver_ts) FROM silver.invoice
     WHERE _s_tenantid = p._s_tenantid AND _s_env = p._s_env AND _s_is_deleted = FALSE)      AS invoice_silver_ts,

    -- Next upcoming (not-done) milestone across ALL of this tenant's active
    -- projects (silver.task.is_milestone). Keyed by (tenantid, env) only —
    -- identical for every p-row in the GROUP BY, so (ARRAY_AGG(...))[1] just
    -- picks the one value (same technique used in load_bom_stock_gap.py).
    (ARRAY_AGG(tenant_ms.next_milestone))[1]                            AS next_milestone

FROM silver.project p

LEFT JOIN LATERAL (
    SELECT JSONB_BUILD_OBJECT(
        'taskId',    nm.id::text,
        'taskName',  nm.task_name,
        'projectId', nm.project_id::text,
        'endTime',   nm.end_time::text
    ) AS next_milestone
    FROM silver.task nm
    WHERE nm._s_tenantid   = p._s_tenantid
      AND nm._s_env        = p._s_env
      AND nm.is_milestone  = TRUE
      AND LOWER(nm.status) NOT IN ('completed','cancelled','skipped','delete')
      AND nm._s_is_deleted = FALSE
      AND nm.invalid       = FALSE
    ORDER BY nm.end_time ASC NULLS LAST
    LIMIT 1
) tenant_ms ON TRUE

-- silver.task.status is stored lowercase in practice ('scheduled','active',
-- 'completed','cancelled','skipped','delete') — verified live, never the
-- uppercase 'DONE'/'COMPLETED' this originally compared against (same bug
-- found and fixed in load_task_intelligence.py / load_project_health.py).
LEFT JOIN LATERAL (
    SELECT
        ROUND(
            COUNT(*) FILTER (WHERE LOWER(status) = 'completed') * 100.0
            / NULLIF(COUNT(*), 0), 2
        )                                                               AS completion_pct,
        COUNT(*) FILTER (
            WHERE end_time < NOW()
              AND LOWER(status) NOT IN ('completed','cancelled','skipped','delete')
        )                                                               AS overdue_task_count
    FROM silver.task
    WHERE _s_tenantid  = p._s_tenantid
      AND _s_env       = p._s_env
      AND project_id   = p.id
      AND _s_is_deleted = FALSE
      AND invalid       = FALSE
) task_agg ON TRUE

LEFT JOIN LATERAL (
    SELECT
        EXTRACT(DAY FROM actual_job_startdate - job_startdate)::int     AS slip_days
    FROM silver.project_schedule
    WHERE _s_tenantid  = p._s_tenantid
      AND _s_env       = p._s_env
      AND project_id   = p.id
      AND _s_is_deleted = FALSE
    ORDER BY version DESC NULLS LAST
    LIMIT 1
) sched ON TRUE

LEFT JOIN LATERAL (
    SELECT COALESCE(SUM(invoiced_amount) FILTER (WHERE status != 'PAID'), 0) AS outstanding
    FROM silver.invoice
    WHERE _s_tenantid  = p._s_tenantid
      AND _s_env       = p._s_env
      AND project_id   = p.id
      AND _s_is_deleted = FALSE
      AND invalid       = FALSE
) inv_agg ON TRUE

WHERE p._s_tenantid  = %(tenantid)s
  AND p.invalid       = FALSE
  AND p.status        NOT IN ('CLOSED', 'CANCELLED')
GROUP BY p._s_tenantid, p._s_env
"""


def fetch_tenant_rows(pg_dsn: str, tenantid: str) -> list[dict]:
    """Fetch the aggregated row(s) for the tenant.

    The Silver query groups by ``(_s_tenantid, _s_env)`` — one row per Silver
    source environment the tenant has projects in. A tenant confined to a
    single ``_s_env`` (the common case today) yields exactly one row. A
    tenant spanning multiple source environments (e.g. ``si_dev`` and
    ``datamart``) yields multiple rows, each of which becomes its own
    ``(tenantId, sourceEnv)``-keyed Gold document.
    """
    conn = psycopg2.connect(pg_dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(_SILVER_QUERY, {"tenantid": tenantid})
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def build_documents(
    pg_dsn: str,
    tenantid: str,
    gold_ts: datetime,
    project_id: str | None = None,
    max_docs: int = 0,
) -> list[dict]:
    """Build the shaped tenant_dashboard document(s) for a tenant.

    tenant_dashboard is a cross-project, tenant-grain aggregation — the
    ``project_id`` argument is accepted for a uniform signature with the
    per-project loaders but is intentionally ignored (a project-level change
    still re-aggregates the whole tenant dashboard).  Reused by the real-time
    Eden streaming job.

    Returns one document per ``(tenantid, source_env)`` row — a tenant
    spanning multiple Silver source environments yields multiple documents.
    """
    rows = fetch_tenant_rows(pg_dsn, tenantid)
    return [shape_doc(row, gold_ts) for row in rows]


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


def _float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _maybe_json(v):
    """Parse a free-text column that may hold JSON; see load_bom_stock_gap.py's
    identical helper for the full rationale."""
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


def _customer_display_name(customer) -> str | None:
    if not isinstance(customer, dict):
        return None
    parts = [customer.get("firstName"), customer.get("lastName")]
    name = " ".join(p.strip() for p in parts if p and str(p).strip())
    return name or None


def _shape_active_project(raw: dict) -> dict:
    """Consolidate one active_projects entry — parse its raw customer JSON
    into a display name rather than embedding the full customer object here
    (the full record already lives in project_health.project.customer;
    tenant_dashboard only needs an identifying name for the project picker)."""
    shaped = dict(raw)
    customer = _maybe_json(shaped.pop("customer", None))
    # Same fallback-chain pattern as load_project.py/load_project_health.py/
    # load_bom_stock_gap.py/load_estimate_price_evidence.py/
    # load_schedule_move_evidence.py, so projectName is never null when any
    # identifying data exists. This previously overwrote projectName with
    # ONLY _customer_display_name(customer), which is None whenever the
    # customer JSON is missing/malformed even though orderNo was already
    # sitting in `raw` unused. Last resort is projectId (p.id) rather than
    # project_uid -- this query's active_projects JSONB payload doesn't
    # carry project_uid at all, only projectId.
    shaped["projectName"] = (
        _customer_display_name(customer)
        or shaped.get("orderNo")
        or shaped.get("projectId")
    )
    return shaped


def _shape_next_milestone(raw: dict | None) -> dict | None:
    """Convert the JSONB-built nextMilestone's endTime (serialized as a string by
    Postgres's JSONB_BUILD_OBJECT — JSON has no native date type) back into a
    native datetime so it stores as a BSON date, not a string."""
    if raw is None:
        return None
    shaped = dict(raw)
    shaped["endTime"] = parse_ts(shaped.get("endTime"))
    return shaped


def shape_doc(row: dict, gold_ts: datetime) -> dict:
    watermarks = {
        "project":          _to_ts(row.get("project_silver_ts")),
        "task":             _to_ts(row.get("task_silver_ts")),
        "project_schedule": _to_ts(row.get("schedule_silver_ts")),
        "invoice":          _to_ts(row.get("invoice_silver_ts")),
    }
    valid_wm = [ts for ts in watermarks.values() if ts is not None]
    lag_ms = int((gold_ts - min(valid_wm)).total_seconds() * 1000) if valid_wm else 0

    doc = {
        "tenantId":        row["tenantid"],
        "_grain":          "eden_tenant_dashboard",
        "_schemaVersion":  1,
        "_silverLsnMax":   int(row.get("silver_lsn_max") or 0),
        "dataAsOf":        gold_ts,
        "projectCount":    int(row.get("project_count") or 0),
        "atRiskCount":     int(row.get("at_risk_count") or 0),
        "totalOutstanding":_float(row.get("total_outstanding")),
        "activeProjects":  [_shape_active_project(p) for p in (row.get("active_projects") or [])],
        "nextMilestone":   _shape_next_milestone(row.get("next_milestone")),  # silver.task.is_milestone, tenant-wide
        "_silverWatermarks": watermarks,
        "lafieldcraft":           lag_ms,
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


def _write_with_retry(coll: Collection, doc: dict,
                      cfg: LoaderConfig, stats: WriteStats,
                      gold_ts: datetime | None = None) -> None:
    if gold_ts is None:
        gold_ts = datetime.now(timezone.utc)
    # Guard key is _silverLsnMax, not _silverLsn — this collection is tenant-wide
    # (aggregated across projects) and only ever sets _silverLsnMax (see doc build
    # above). Using _silverLsn here was a no-op guard (fixed 2026-07-12): doc.get
    # always returned 0 and the $exists:False branch was always true, so the $or
    # always matched and stale writes were never actually rejected.
    op = UpdateOne({"tenantId": doc["tenantId"], "sourceEnv": doc.get("sourceEnv"), "$or": [{"_silverLsnMax": {"$exists": False}}, {"_silverLsnMax": {"$lte": doc.get("_silverLsnMax", 0)}}]}, {"$set": doc, "$setOnInsert": set_on_insert(gold_ts, iso=False)}, upsert=True)
    delay = cfg.retry_backoff_s
    for attempt in range(1, cfg.max_retries + 1):
        try:
            res = coll.bulk_write([op], ordered=False)
            stats.upserted += res.upserted_count or 0
            stats.modified += res.modified_count or 0
            return
        except BulkWriteError as exc:
            stats.failed += 1
            return
        except TRANSIENT_ERRORS as exc:
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
    gold_ts = datetime.now(timezone.utc)
    stats = WriteStats()

    last_ts = read_cursor(cfg.pg_dsn, _STEP_NAME, cfg.tenantid)
    if cfg.rebuild:
        log.info("rebuild mode | full re-aggregate from current Silver; cursor will NOT be advanced")
    elif not _has_new_silver_rows(cfg.pg_dsn, cfg.tenantid, last_ts):
        log.info("no new Silver rows since %s — skipping full re-aggregate", last_ts.isoformat())
        return 0

    rows = fetch_tenant_rows(cfg.pg_dsn, cfg.tenantid)
    if not rows:
        log.warning("no active projects found for tenant %s — skipping", cfg.tenantid)
        return 0

    docs = [shape_doc(row, gold_ts) for row in rows]

    if not cfg.dry_run:
        client = MongoClient(cfg.mongo_uri, serverSelectionTimeoutMS=15_000,
                             connectTimeoutMS=15_000, socketTimeoutMS=60_000,
                             retryWrites=False, appname="eden-tenant-dashboard")
        try:
            coll = client[cfg.db_name][COLLECTION]
            for doc in docs:
                _write_with_retry(coll, doc, cfg, stats, gold_ts)
        except TRANSIENT_ERRORS as exc:
            mark_error(cfg.pg_dsn, _STEP_NAME, cfg.tenantid, exc)
            log.error("aborting: %s", exc)
            return 3
        finally:
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

    log.info("loader done | tenant=%s upserted=%d failed=%d",
             cfg.tenantid, stats.upserted, stats.failed)
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
