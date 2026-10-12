"""Silver → Gold loader: procurement_status (Eden AI Assistant).

Reads silver.bom, silver.purchase_order, silver.purchase_order_items,
silver.vendor to produce one doc per project with BOM vs PO gap,
pending approvals, and unordered items.

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

COLLECTION = "procurement_status"
FEATURE_NAME = "eden"
_STEP_NAME = "load_procurement_status"
_contract_validator = ContractValidator("eden_procurement_status")
TRANSIENT_ERRORS = (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError,
                    CircuitOpenError)
log = logging.getLogger("eden.procurement_status_loader")


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
        p = argparse.ArgumentParser(description="Silver → Gold loader: procurement_status")
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


_SILVER_QUERY = """
SELECT
    p._s_tenantid                                                               AS tenantid,
    p._s_env                                                                     AS source_env,
    p._s_is_deleted                                                              AS is_deleted,
    p._s_deleted_at                                                              AS deleted_at,
    p._s_lsn                                                                     AS silver_lsn,
    p.id                                    AS project_id,

    -- BOM totals
    COALESCE(bom.total_value, 0)            AS bom_total_value,
    COALESCE(bom.ordered_value, 0)          AS ordered_value,
    COALESCE(bom.unordered_value, 0)        AS unordered_value,

    -- Unordered BOM items
    bom.unordered_items,

    -- Pending PO approvals
    po.pending_approvals,

    -- Order number for display
    p.order_no,

    -- Per-table Silver freshness watermarks
    p._s_silver_ts      AS project_silver_ts,
    bom.bom_silver_ts,
    po.po_silver_ts

FROM silver.project p

LEFT JOIN LATERAL (
    SELECT
        SUM(bl.total_cost)                                      AS total_value,
        SUM(bl.total_cost) FILTER (WHERE bl.is_ordered)         AS ordered_value,
        SUM(bl.total_cost) FILTER (WHERE NOT bl.is_ordered)     AS unordered_value,
        JSONB_AGG(
            JSONB_BUILD_OBJECT(
                'materialName', bl.material_name,
                'quantity',     bl.quantity,
                'unit',         bl.unit,
                'unitCost',     bl.unit_cost,
                'totalCost',    bl.total_cost,
                'vendorName',   bl.vendor_name
            )
            ORDER BY bl.total_cost DESC NULLS LAST
        ) FILTER (WHERE NOT bl.is_ordered)                      AS unordered_items,
        MAX(bl.bom_silver_ts)                                   AS bom_silver_ts
    FROM (
        -- silver.bom has no vendor_id and no direct PO-item link. A BOM line is
        -- "ordered" when its material (bom -> vendor_material_map.material_id)
        -- appears on an approved PO item for the same project. Vendor is resolved
        -- through vendor_material_map.vendor_id (no bom.vendor_id column exists).
        SELECT
            b.material_name, b.quantity, b.unit, b.unit_cost, b.total_cost,
            b._s_silver_ts AS bom_silver_ts,
            v.company_name AS vendor_name,
            EXISTS (
                SELECT 1
                FROM silver.purchase_order_items poi
                JOIN silver.purchase_order po
                  ON po.id           = poi.purchase_order
                 AND po.project_id   = b.project_id
                 AND po._s_tenantid  = b._s_tenantid
                 AND po._s_env       = b._s_env
                 AND po._s_is_deleted = FALSE
                WHERE poi.reference_type = 'Material'
                  AND poi.reference_id   = vmm.material_id
                  AND poi._s_tenantid    = b._s_tenantid
                  AND poi._s_env         = b._s_env
                  AND poi._s_is_deleted  = FALSE
            )                                                   AS is_ordered
        FROM silver.bom b
        LEFT JOIN silver.vendor_material_map vmm
               ON vmm.id = b.vendor_material_map_id
              AND vmm._s_tenantid = b._s_tenantid
              AND vmm._s_env = b._s_env
              AND vmm._s_is_deleted = FALSE
        LEFT JOIN silver.vendor v
               ON v.id = vmm.vendor_id
              AND v._s_tenantid = b._s_tenantid
              AND v._s_env = b._s_env
              AND v._s_is_deleted = FALSE
        WHERE b._s_tenantid  = p._s_tenantid
          AND b._s_env       = p._s_env
          AND b.project_id   = p.id
          AND b._s_is_deleted = FALSE
          AND b.invalid       = FALSE
    ) bl
) bom ON TRUE

LEFT JOIN LATERAL (
    SELECT
        JSONB_AGG(
            JSONB_BUILD_OBJECT(
                'poNumber',  po.id::text,
                'vendor',    v.company_name,
                'total',     po.total,
                'status',    po.status
            )
            ORDER BY po.created_at DESC NULLS LAST
        ) AS pending_approvals,
        MAX(po._s_silver_ts) AS po_silver_ts
    FROM silver.purchase_order po
    LEFT JOIN silver.vendor v
           ON v._s_tenantid = po._s_tenantid
          AND v._s_env = po._s_env
          AND v.id = po.vendor_id
          AND v._s_is_deleted = FALSE
    WHERE po._s_tenantid  = p._s_tenantid
      AND po._s_env       = p._s_env
      AND po.project_id   = p.id
      AND po.status       = 'PENDING_APPROVAL'
      AND po._s_is_deleted = FALSE
) po ON TRUE

WHERE p._s_tenantid  = %(tenantid)s
  AND p.invalid       = FALSE
ORDER BY p.id
"""


def _scoped_query(project_id: str | None) -> str:
    """Return the Silver query, optionally scoped to a single project.

    When project_id is provided, splice an ``AND p.id = %(project_id)s``
    predicate into the outer WHERE clause so the same tested SQL runs for
    one project — used by the real-time streaming job.
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


def _float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def shape_doc(row: dict, gold_ts: datetime) -> dict:
    watermarks = {
        "project":        _to_ts(row.get("project_silver_ts")),
        "bom":            _to_ts(row.get("bom_silver_ts")),
        "purchase_order": _to_ts(row.get("po_silver_ts")),
    }
    valid_wm = [ts for ts in watermarks.values() if ts is not None]
    lag_ms = int((gold_ts - min(valid_wm)).total_seconds() * 1000) if valid_wm else 0

    doc = {
        "tenantId":         row["tenantid"],
        "projectId":        str(row["project_id"]),
        "_grain":           "eden_procurement_status",
        "_schemaVersion":   1,
        "_silverLsn":       int(row.get("silver_lsn") or 0),
        "isDeleted":                bool(row.get("is_deleted") or False),
        "deletedAt":                parse_ts(row.get("deleted_at")),
        "dataAsOf":         gold_ts,
        "orderNo":          row.get("order_no"),
        "bomTotalValue":    _float(row.get("bom_total_value")),
        "orderedValue":     _float(row.get("ordered_value")),
        "unorderedValue":   _float(row.get("unordered_value")),
        "unorderedItems":   row.get("unordered_items") or [],
        "pendingApprovals": row.get("pending_approvals") or [],
        "_silverWatermarks": watermarks,
        "lafieldcraft":            lag_ms,
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
            stats.failed += len(exc.details.get("writeErrors", []) if exc.details else [])
            stats.batches += 1
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
                             retryWrites=False, appname="eden-procurement-status")
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
