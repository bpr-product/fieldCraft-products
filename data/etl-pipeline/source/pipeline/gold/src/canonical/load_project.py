"""Silver → Gold loader: project (Canonical Gold).

Reads silver.project directly — a LEAN project directory (identity, customer/
asset linkage, status, trade, location, dates). This is deliberately NOT a
duplicate of gold_eden.project_health, which stays the feature-owned source
for task/financial/schedule detail (completion %, cost estimate, committed
spend, schedule risk, etc.) — this collection exists so bpe/eden/qa_recommendation
can all resolve a project's basic identity from one shared place instead of
each re-deriving it from silver.project independently.

assetUid is resolved via silver.customer_asset (project has no asset_uid
column of its own) — one row per project verified live.

Upsert key: (tenantId, projectId, sourceEnv)

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
from gold.src.monitoring.circuit_breaker import CircuitBreaker  # noqa: E402
from gold.src.monitoring.contract_validator import ContractValidator  # noqa: E402
from gold.pipeline.cursor_store import (  # noqa: E402
    read_cursor, write_cursor, mark_error, get_max_silver_ts_per_env, EPOCH,
)
from gold.pipeline.lineage import (  # noqa: E402
    cleanup_stale_order_no_siblings, lineage_fields, parse_ts, set_on_insert,
)
from gold.pipeline.silver_schema import get_silver_columns, optional_select  # noqa: E402

COLLECTION = "project"
FEATURE_NAME = "canonical"
_STEP_NAME = "load_project"
_contract_validator = ContractValidator("canonical_project")
TRANSIENT_ERRORS = (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError)
log = logging.getLogger("canonical.project_loader")


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
        p = argparse.ArgumentParser(description="Silver → Gold loader: project")
        p.add_argument("--mongo-uri",     default=None)
        p.add_argument("--pg-dsn",        default=None)
        p.add_argument("--db",            dest="db_name",
                       default=os.environ.get("GOLD_CANONICAL_DB", "gold_canonical"))
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


def _build_silver_query(pg_dsn: str, project_id: str | None) -> str:
    # metro_code/jobsite_lat/jobsite_lng: added by ADO PR 7368 (NextGen ASE
    # schema); not yet present on silver.project in every env during the
    # migration window. optional_select() reads NULL for envs that don't have
    # them yet and switches to the real column automatically once
    # auto-schema-sync adds it — no code change needed either side.
    cols = get_silver_columns(pg_dsn, "project")
    optional = ",\n    ".join(
        optional_select("p", c, cols) for c in ("metro_code", "jobsite_lat", "jobsite_lng")
    )
    where_extra = "\n  AND p.id = %(project_id)s" if project_id is not None else ""
    return f"""
SELECT
    p._s_tenantid                          AS tenantid,
    p._s_env                               AS source_env,
    p._s_is_deleted                        AS is_deleted,
    p._s_deleted_at                        AS deleted_at,
    p._s_lsn                               AS silver_lsn,
    p._s_silver_ts                         AS project_silver_ts,
    p.id                                   AS project_id,
    p.project_uid,
    p.order_no,
    -- silver.project has no plain "name" column; the source system pre-formats
    -- description as "customer name, city-trade-orderNo" (e.g. "vijay cus,
    -- Chennai-Roof Enhancement-708869") — verified live on tn24047750
    -- 2026-07-30 — so it's exposed here as projectName, matching the same
    -- field gold_eden.project_health / gold_eden.bom_stock_gap already derive.
    p.description                          AS description,
    p.customer_uid,
    p.status,
    p.trade,
    p.trade_type,
    p.trade_oid,
    p.project_type,
    p.scheduled_type,
    p.location,
    p.zipcode,
    {optional},
    p.reference_project_id,
    p.parent_project_id,
    p.created_at,
    p.created_by,
    p.updated_at,
    asset.asset_uid,
    asset.asset_silver_ts

FROM silver.project p

LEFT JOIN LATERAL (
    SELECT ca.asset_uid, ca._s_silver_ts AS asset_silver_ts
    FROM silver.customer_asset ca
    WHERE ca._s_tenantid   = p._s_tenantid
      AND ca._s_env        = p._s_env
      AND ca.project_id    = p.id
      AND ca._s_is_deleted = FALSE
    LIMIT 1
) asset ON TRUE

WHERE p._s_tenantid  = %(tenantid)s
  AND p.invalid       = FALSE{where_extra}
ORDER BY p.id
"""


def iter_silver_rows(
    pg_dsn: str, tenantid: str, max_docs: int = 0, project_id: str | None = None
) -> Iterator[dict]:
    conn = psycopg2.connect(pg_dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            params = {"tenantid": tenantid}
            if project_id is not None:
                params["project_id"] = project_id
            cur.execute(_build_silver_query(pg_dsn, project_id), params)
            rows = cur if max_docs <= 0 else (r for _, r in zip(range(max_docs), cur))
            for row in rows:
                yield dict(row)
    finally:
        conn.close()


def build_documents(
    pg_dsn: str, tenantid: str, gold_ts: datetime,
    project_id: str | None = None, max_docs: int = 0,
) -> list[dict]:
    return [
        shape_doc(row, gold_ts)
        for row in iter_silver_rows(pg_dsn, tenantid, max_docs, project_id)
    ]


def _has_new_silver_rows(pg_dsn: str, tenantid: str, since: datetime) -> bool:
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM silver.project WHERE _s_tenantid = %s AND _s_silver_ts > %s"
            ")",
            (tenantid, since),
        )
        return bool(cur.fetchone()[0])


def _get_max_silver_ts(pg_dsn: str, tenantid: str) -> datetime:
    return get_max_silver_ts_per_env(pg_dsn, "project", "_s_tenantid", tenantid)


def _float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def shape_doc(row: dict, gold_ts: datetime) -> dict:
    watermarks = {
        "project":       parse_ts(row.get("project_silver_ts")),
        "customer_asset": parse_ts(row.get("asset_silver_ts")),
    }
    valid_wm = [ts for ts in watermarks.values() if ts is not None]
    lag_ms = int((gold_ts - min(valid_wm)).total_seconds() * 1000) if valid_wm else 0

    doc = {
        "tenantId":            row["tenantid"],
        "projectId":           str(row["project_id"]),
        "projectUid":          row.get("project_uid"),
        "orderNo":             row.get("order_no"),
        "projectName":         row.get("description") or row.get("order_no") or row.get("project_uid"),
        "customerUid":         row.get("customer_uid"),
        "assetUid":            row.get("asset_uid"),
        "status":              row.get("status"),
        "trade":               row.get("trade"),
        "tradeType":           row.get("trade_type"),
        "tradeOid":            row.get("trade_oid"),
        "projectType":         row.get("project_type"),
        "scheduledType":       row.get("scheduled_type"),
        "location":            row.get("location"),
        "zipcode":             row.get("zipcode"),
        "metroCode":           row.get("metro_code"),
        "jobsiteLat":          _float(row.get("jobsite_lat")),
        "jobsiteLng":          _float(row.get("jobsite_lng")),
        "referenceProjectId":  (str(row["reference_project_id"])
                                 if row.get("reference_project_id") is not None else None),
        "parentProjectId":     (str(row["parent_project_id"])
                                 if row.get("parent_project_id") is not None else None),
        "createdAt":           row.get("created_at"),
        "createdBy":           row.get("created_by"),
        "silverUpdatedAt":     row.get("updated_at"),
        "_grain":              "canonical_project",
        "_schemaVersion":      1,
        "_silverLsn":          int(row.get("silver_lsn") or 0),
        "isDeleted":           bool(row.get("is_deleted") or False),
        "deletedAt":           parse_ts(row.get("deleted_at")),
        "dataAsOf":            gold_ts,
        "_silverWatermarks":   watermarks,
        "lafieldcraft":               lag_ms,
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


def run(cfg: LoaderConfig) -> int:
    log.info("loader start | db=%s.%s tenant=%s dry_run=%s mongo=%s",
             cfg.db_name, COLLECTION, cfg.tenantid, cfg.dry_run, _mask_uri(cfg.mongo_uri))
    last_ts = read_cursor(cfg.pg_dsn, _STEP_NAME, cfg.tenantid)
    if cfg.rebuild:
        log.info("rebuild mode | full re-aggregate from current Silver; cursor will NOT be advanced")
    elif not _has_new_silver_rows(cfg.pg_dsn, cfg.tenantid, last_ts):
        log.info("no new Silver rows since %s — skipping", last_ts.isoformat())
        return 0
    gold_ts = datetime.now(timezone.utc)
    stats = WriteStats()
    client: MongoClient | None = None
    coll: Collection | None = None
    if not cfg.dry_run:
        client = MongoClient(cfg.mongo_uri, serverSelectionTimeoutMS=15_000,
                             connectTimeoutMS=15_000, socketTimeoutMS=60_000,
                             retryWrites=False, appname="canonical-project")
        coll = client[cfg.db_name][COLLECTION]

    pending: list[dict] = []
    seen = 0

    def flush() -> None:
        if not pending:
            return
        for d in pending:
            _contract_validator.validate(d, COLLECTION)
        ops = [
            UpdateOne(
                {"tenantId": d["tenantId"], "projectId": d["projectId"], "sourceEnv": d.get("sourceEnv"),
                 "$or": [{"_silverLsn": {"$exists": False}},
                         {"_silverLsn": {"$lte": d.get("_silverLsn", 0)}}]},
                {"$set": d, "$setOnInsert": set_on_insert(gold_ts, iso=False)},
                upsert=True,
            )
            for d in pending
        ]
        if ops and not cfg.dry_run and coll is not None:
            # See lineage.cleanup_stale_order_no_siblings' module comment: the
            # source system creates a NEW internal project id when a draft is
            # converted, leaving an abandoned duplicate doc under the old id.
            # Only clean up after confirming THIS batch wrote cleanly (failed
            # count unchanged) -- a partial failure means we can't be sure
            # every doc in `pending` actually landed, so cleanup waits for the
            # next clean pass rather than risk deleting a sibling based on an
            # unconfirmed write.
            _failed_before = stats.failed
            bulk_write_with_retry(coll, ops, cfg, stats)
            if stats.failed == _failed_before:
                for d in pending:
                    order_no = d.get("orderNo")
                    if not order_no:
                        continue
                    deleted = cleanup_stale_order_no_siblings(
                        coll, d["tenantId"], order_no, d["projectId"], d.get("sourceEnv")
                    )
                    if deleted:
                        log.info(
                            "cleaned up %d stale sibling doc(s) | orderNo=%s "
                            "tenant=%s sourceEnv=%s currentProjectId=%s",
                            deleted, order_no, d["tenantId"], d.get("sourceEnv"), d["projectId"],
                        )
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
