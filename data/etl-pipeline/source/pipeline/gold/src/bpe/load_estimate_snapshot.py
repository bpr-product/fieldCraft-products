"""Silver → Gold loader: feature_store (BPE engineered features).

Reads silver.project joined with estimates, BOM, and answers
and upserts per-project engineered features into gold_bpe.feature_store.

Collection: feature_store (Final approved schema)
Upsert key: (tenantId, project_id) — one document per project, tenant-scoped
so projects sharing an id across tenants do not collide.

Exit codes:
  0  success
  2  configuration / connection error
  3  load failure after retries exhausted
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
from typing import Any, Iterator
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
from gold.pipeline.cursor_store import (  # noqa: E402
    read_cursor, write_cursor, mark_error, get_max_silver_ts_per_env, EPOCH,
)
from gold.pipeline.lineage import lineage_fields, parse_ts, set_on_insert  # noqa: E402

COLLECTION = "feature_store"
FEATURE_NAME = "bpe"
_STEP_NAME = "load_estimate_snapshot"
TRANSIENT_ERRORS = (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError)
log = logging.getLogger("bpe.feature_store_loader")


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
        p = argparse.ArgumentParser(description="Gold BPE feature_store loader")
        p.add_argument("--mongo-uri",     default=None)
        p.add_argument("--pg-dsn",        default=None)
        p.add_argument("--db",            dest="db_name",
                       default=os.environ.get("GOLD_BPE_DB_NAME", "gold_bpe"))
        p.add_argument("--tenant", required=not bool(os.environ.get("TENANT_ID")),
                       default=os.environ.get("TENANT_ID"))
        p.add_argument("--batch-size",    type=int, default=int(os.environ.get("BATCH_SIZE", "200")))
        p.add_argument("--max-retries",   type=int, default=int(os.environ.get("MAX_RETRIES", "5")))
        p.add_argument("--retry-backoff", type=float, default=float(os.environ.get("RETRY_BACKOFF_S", "1.5")))
        p.add_argument("--dry-run",       action="store_true")
        p.add_argument("--max-docs",      type=int, default=int(os.environ.get("MAX_DOCS", "0")))
        p.add_argument("--rebuild",       action="store_true",
                       help="Full rebuild: read ALL current Silver (ignore the "
                            "incremental cursor) and do NOT advance it. Use to "
                            "recover a gap for a realtime feature.")
        ns = p.parse_args(argv)
        return LoaderConfig(
            mongo_uri=ns.mongo_uri or get_mongo_uri(),
            pg_dsn=ns.pg_dsn or get_postgres_dsn(),
            db_name=ns.db_name,
            tenantid=ns.tenant,
            batch_size=ns.batch_size,
            max_retries=ns.max_retries,
            retry_backoff_s=ns.retry_backoff,
            dry_run=ns.dry_run,
            max_docs=ns.max_docs,
            rebuild=ns.rebuild,
        )

# ---------------------------------------------------------------------------
# Silver query — engineered features per project
# ---------------------------------------------------------------------------

_SILVER_QUERY = """
SELECT
    p._s_tenantid                                                               AS tenantid,
    p._s_env                                                                    AS source_env,
    p._s_silver_ts                                                              AS silver_ts,
    p._s_lsn                                                                    AS silver_lsn,
    p._s_is_deleted                                                             AS is_deleted,
    p._s_deleted_at                                                             AS deleted_at,
    p.id                                                                        AS project_id,
    p.trade_oid,
    p.project_type,
    p.zipcode,
    p.location,
    p.schema_version                                                            AS q_template_version,

    -- sq_ft / area from customer_asset JSON
    CASE
        WHEN p.customer_asset IS NOT NULL
             AND p.customer_asset::text LIKE '%%area%%'
        THEN (p.customer_asset::jsonb ->> 'area')::numeric
        ELSE NULL
    END                                                                         AS area,

    -- cost columns from latest project_estimate
    e.material_cost,
    e.worker_cost,
    e.tool_cost,
    e.overhead_cost,
    e.margin,
    e.total_cost,

    -- BOM items as JSON array
    COALESCE(bom_agg.bom_items, '[]'::json)                                     AS bom_items,

    -- actual spend
    COALESCE(pay_agg.actual_material_spend, 0)                                  AS actual_material_spend,
    COALESCE(pay_agg.actual_labor_spend, 0)                                     AS actual_labor_spend,

    -- answered OIDs as JSON array for features dict
    -- silver.answers.answers_data is TEXT; cast so COALESCE types match
    -- (_build_features_dict parses the JSON string downstream)
    COALESCE(ans_agg.answers_data::text, '[]')                                  AS answers_data,

    -- asset attributes from customer_asset JSON (same source as area above)
    asset.doc ->> 'stories'                                                     AS stories,
    asset.doc ->> 'assetType'                                                   AS asset_type,
    asset.doc ->> 'city'                                                        AS city,
    asset.doc ->> 'state'                                                       AS state,

    -- source provenance flags (one EXISTS per contributing Silver table)
    EXISTS(SELECT 1 FROM silver.bom x
           WHERE x._s_tenantid = p._s_tenantid AND x._s_env = p._s_env AND x.project_id = p.id
             AND x._s_is_deleted = FALSE AND x.invalid = FALSE)                 AS has_bom,
    EXISTS(SELECT 1 FROM silver.resource_estimate x
           WHERE x._s_tenantid = p._s_tenantid AND x._s_env = p._s_env AND x.project_id = p.id
             AND x._s_is_deleted = FALSE AND x.invalid = FALSE)                 AS has_resource_estimate,
    EXISTS(SELECT 1 FROM silver.project_estimate x
           WHERE x._s_tenantid = p._s_tenantid AND x._s_env = p._s_env AND x.project_id = p.id
             AND x._s_is_deleted = FALSE AND x.invalid = FALSE)                 AS has_project_estimate,
    EXISTS(SELECT 1 FROM silver.answers x
           WHERE x._s_tenantid = p._s_tenantid AND x._s_env = p._s_env AND x.project_id = p.id
             AND x._s_is_deleted = FALSE AND x.invalid = FALSE)                 AS has_answers,
    EXISTS(SELECT 1 FROM silver.task x
           WHERE x._s_tenantid = p._s_tenantid AND x._s_env = p._s_env AND x.project_id = p.id
             AND x._s_is_deleted = FALSE AND x.invalid = FALSE)                 AS has_tasks,
    -- payment_expense has no `invalid` column; guard on soft-delete only
    EXISTS(SELECT 1 FROM silver.payment_expense x
           WHERE x._s_tenantid = p._s_tenantid AND x._s_env = p._s_env AND x.project_id = p.id
             AND x._s_is_deleted = FALSE)                                       AS has_payment_expense,

    -- service count
    COALESCE(svc_count.service_count, 0)                                        AS service_count

FROM silver.project p

LEFT JOIN LATERAL (
    SELECT material_cost, worker_cost, tool_cost, overhead_cost, margin, total_cost
    FROM silver.project_estimate
    WHERE _s_tenantid  = p._s_tenantid
      AND _s_env        = p._s_env
      AND project_id   = p.id
      AND _s_is_deleted = FALSE
      AND invalid       = FALSE
    ORDER BY updated_at DESC NULLS LAST
    LIMIT 1
) e ON TRUE

LEFT JOIN LATERAL (
    SELECT json_agg(json_build_object(
        'materialName', b.material_name,
        'quantity',     b.quantity,
        'unit',         b.unit,
        'unitCost',     b.unit_cost,
        'lineTotal',    b.total_cost
    )) AS bom_items
    FROM silver.bom b
    WHERE b._s_tenantid  = p._s_tenantid
      AND b._s_env        = p._s_env
      AND b.project_id   = p.id
      AND b._s_is_deleted = FALSE
      AND b.invalid       = FALSE
) bom_agg ON TRUE

LEFT JOIN LATERAL (
    SELECT
        COALESCE(SUM(CASE
            WHEN LOWER(reference_type) ILIKE '%%purchase%%'
              OR LOWER(reference_type) ILIKE '%%order%%'
              OR LOWER(reference_type) = 'po'
            THEN amount ELSE 0 END), 0) AS actual_material_spend,
        COALESCE(SUM(CASE
            WHEN LOWER(reference_type) ILIKE '%%salary%%'
              OR LOWER(reference_type) ILIKE '%%labour%%'
              OR LOWER(reference_type) ILIKE '%%labor%%'
            THEN amount ELSE 0 END), 0) AS actual_labor_spend
    FROM silver.payment_expense
    WHERE _s_tenantid  = p._s_tenantid
      AND _s_env        = p._s_env
      AND project_id   = p.id
      AND _s_is_deleted = FALSE
      AND invalid       = FALSE
) pay_agg ON TRUE

LEFT JOIN LATERAL (
    SELECT answers_data
    FROM silver.answers
    WHERE _s_tenantid  = p._s_tenantid
      AND _s_env        = p._s_env
      AND project_id   = p.id
      AND _s_is_deleted = FALSE
      AND invalid       = FALSE
    ORDER BY version DESC NULLS LAST
    LIMIT 1
) ans_agg ON TRUE

LEFT JOIN LATERAL (
    SELECT 0 AS service_count
) svc_count ON TRUE

-- Parse customer_asset once (object JSON only) for asset attribute extraction
LEFT JOIN LATERAL (
    SELECT CASE
        WHEN p.customer_asset IS NOT NULL AND left(btrim(p.customer_asset), 1) = '{'
        THEN p.customer_asset::jsonb
    END AS doc
) asset ON TRUE

WHERE p._s_tenantid  = %(tenantid)s
  AND p.invalid       = FALSE
  AND p._s_silver_ts  > %(last_ts)s
  AND (%(project_id)s::bigint IS NULL OR p.id = %(project_id)s::bigint)
ORDER BY p._s_silver_ts, p.id
"""


def _float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _build_features_dict(answers_data: Any) -> dict:
    """Convert answers_data JSON array into {oid: value} dict for feature_store."""
    if not answers_data:
        return {}
    if isinstance(answers_data, str):
        try:
            answers_data = json.loads(answers_data)
        except (json.JSONDecodeError, TypeError):
            return {}
    if isinstance(answers_data, list):
        return {
            a.get("itemOid") or a.get("id", ""): a.get("answer") or a.get("value")
            for a in answers_data
            if isinstance(a, dict) and (a.get("itemOid") or a.get("id"))
        }
    if isinstance(answers_data, dict):
        return answers_data
    return {}


def _build_data_quality(row: dict) -> str:
    bom_items = row.get("bom_items")
    has_bom = bool(bom_items and bom_items != [] and bom_items != "[]")
    service_count = int(row.get("service_count") or 0)
    if not has_bom:
        return "estimate_fallback"
    if service_count <= 1:
        return "full"
    return "material_split"


def iter_silver_rows(pg_dsn: str, tenantid: str, max_docs: int = 0,
                     last_ts: datetime = EPOCH,
                     project_id: int | None = None) -> Iterator[dict]:
    conn = psycopg2.connect(pg_dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(
                _SILVER_QUERY,
                {"tenantid": tenantid, "last_ts": last_ts, "project_id": project_id},
            )
            rows = cur if max_docs <= 0 else (r for _, r in zip(range(max_docs), cur))
            for row in rows:
                yield dict(row)
    finally:
        conn.close()


def build_documents(pg_dsn: str, tenant: str, gold_ts: datetime,
                    project_id: int | None = None) -> list[dict]:
    """Return current feature_store documents for a tenant (optionally one project).

    Reuses the same Silver query + shape_doc as the batch path, but reads the
    CURRENT state (no watermark) so one recompute reflects the project's full
    present state idempotently. Used by:
      • the real-time stream (gold_bpe_stream) — scoped to the changed project,
      • project-scoped runs (project_id set),
      • tenant-wide rebuilds (project_id=None).
    """
    docs: list[dict] = []
    for row in iter_silver_rows(pg_dsn, tenant, max_docs=0, last_ts=EPOCH,
                                project_id=project_id):
        doc = shape_doc(row, gold_ts)
        if doc is not None:
            docs.append(doc)
    return docs


def shape_doc(row: dict, gold_ts: datetime) -> dict:
    """Build a feature_store document matching the Final approved schema."""
    bom_raw = row.get("bom_items")
    if isinstance(bom_raw, str):
        try:
            bom_raw = json.loads(bom_raw)
        except (json.JSONDecodeError, TypeError):
            bom_raw = []

    silver_lsn = int(row.get("silver_lsn") or 0)
    silver_ts  = row.get("silver_ts") or gold_ts
    lag_ms     = int((gold_ts - silver_ts).total_seconds() * 1000) if silver_ts else 0

    doc = {
        "tenantId":              row.get("tenantid") or "",
        # int to match the canonical Synthesized_Data.bpe schema (not str).
        "project_id":            _int_or_none(row["project_id"]),
        "service":               row.get("project_type"),
        "trade_oid":             row.get("trade_oid"),
        "area":                  _int_or_none(row.get("area")),
        "stories":               _int_or_none(row.get("stories")),
        "asset_type":            row.get("asset_type"),
        "zipcode":               row.get("zipcode"),
        "city":                  row.get("city"),
        "state":                 row.get("state"),
        # services / service_count require Q&A job_type resolution (ML-owned
        # feature logic — see other team's source/bpe/data_pipeline.py). Left
        # empty pending that mapping rather than fabricated; tracked for follow-up.
        "services":              [],
        "n_services":            int(row.get("service_count") or 0),
        "is_single_service":     int(row.get("service_count") or 0) <= 1,
        "q_template_version":    _int_or_none(row.get("q_template_version")),
        "features":              _build_features_dict(row.get("answers_data")),
        "material_cost":         _float_or_none(row.get("material_cost")),
        "worker_cost":           _float_or_none(row.get("worker_cost")),
        "tool_cost":             _float_or_none(row.get("tool_cost")),
        "overhead_cost":         _float_or_none(row.get("overhead_cost")),
        "margin":                _float_or_none(row.get("margin")),
        "total_cost":            _float_or_none(row.get("total_cost")),
        "bom_items":             bom_raw or [],
        "actual_material_spend": _float_or_none(row.get("actual_material_spend")),
        "actual_labor_spend":    _float_or_none(row.get("actual_labor_spend")),
        "data_quality":          _build_data_quality(row),
        "source_flags":          {
            "has_bom":               bool(row.get("has_bom")),
            "has_resource_estimate": bool(row.get("has_resource_estimate")),
            "has_project_estimate":  bool(row.get("has_project_estimate")),
            "has_answers":           bool(row.get("has_answers")),
            "has_tasks":             bool(row.get("has_tasks")),
            "has_payment_expense":   bool(row.get("has_payment_expense")),
        },
        "refreshed_at":          gold_ts,
        # soft-delete tombstone: a soft-deleted Silver project is marked here rather
        # than left as a live-looking Gold doc (the project query includes deleted
        # rows; Silver never physically deletes).
        "isDeleted":             bool(row.get("is_deleted") or False),
        "deletedAt":             parse_ts(row.get("deleted_at")),
        # Platform audit / lineage fields
        "_grain":                "bpe_feature_store",
        "_schemaVersion":        1,
        "_silverLsn":            silver_lsn,
        "_silverWatermarks":     {"project": silver_ts},
        "lafieldcraft":                 lag_ms,
    }
    doc.update(lineage_fields(FEATURE_NAME, gold_ts, iso=False, source_updated_at=silver_ts,
                              source_env=row.get("source_env")))
    return doc


@dataclass
class WriteStats:
    upserted: int = 0
    modified: int = 0
    failed:   int = 0
    batches:  int = 0


def bulk_write_with_retry(coll: Collection, ops: list[UpdateOne],
                          cfg: LoaderConfig, stats: WriteStats) -> None:
    delay = cfg.retry_backoff_s
    for attempt in range(1, cfg.max_retries + 1):
        try:
            res = coll.bulk_write(ops, ordered=False)
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
        log.warning("signal %s — finishing current batch then stopping", signum)
        _shutdown = True
    signal.signal(signal.SIGINT, _handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handler)


def run(cfg: LoaderConfig) -> int:
    log.info("loader start | db=%s.%s tenant=%s dry_run=%s",
             cfg.db_name, COLLECTION, cfg.tenantid, cfg.dry_run)
    gold_ts = datetime.now(timezone.utc)
    stats   = WriteStats()

    # Rebuild reads ALL current Silver (EPOCH) and must not advance the
    # incremental cursor; a normal run reads from the stored watermark.
    last_ts = EPOCH if cfg.rebuild else read_cursor(cfg.pg_dsn, _STEP_NAME, cfg.tenantid)
    if cfg.rebuild:
        log.info("rebuild mode | full read from EPOCH; cursor will NOT be advanced")

    client: MongoClient | None = None
    coll: Collection | None = None
    if not cfg.dry_run:
        client = MongoClient(
            cfg.mongo_uri,
            serverSelectionTimeoutMS=15_000,
            connectTimeoutMS=15_000,
            socketTimeoutMS=60_000,
            retryWrites=False,
            appname="bpe-feature-store-loader",
        )
        coll = client[cfg.db_name][COLLECTION]

    pending: list[dict] = []
    seen = 0

    def flush() -> None:
        if not pending:
            return
        ops: list[UpdateOne] = []
        for row in pending:
            doc = shape_doc(row, gold_ts)
            ops.append(
                UpdateOne(
                    # Composite key: project_id is only unique within a tenant, so
                    # keying on project_id alone lets two tenants clobber each other.
                    # sourceEnv further scopes the key since tenantid is only unique
                    # within one _s_env (a tenantid could theoretically exist under
                    # two different source environments).
                    {"tenantId": doc["tenantId"], "project_id": doc["project_id"],
                     "sourceEnv": doc.get("sourceEnv")},
                    {"$set": doc, "$setOnInsert": set_on_insert(gold_ts, iso=False)},
                    upsert=True,
                )
            )
        if ops and not cfg.dry_run and coll is not None:
            bulk_write_with_retry(coll, ops, cfg, stats)
        pending.clear()

    try:
        for row in iter_silver_rows(cfg.pg_dsn, cfg.tenantid, cfg.max_docs, last_ts):
            if _shutdown:
                break
            seen += 1
            pending.append(row)
            if len(pending) >= cfg.batch_size:
                flush()
                log.info("progress | seen=%d upserted=%d modified=%d",
                         seen, stats.upserted, stats.modified)
        flush()

    except TRANSIENT_ERRORS as exc:
        mark_error(cfg.pg_dsn, _STEP_NAME, cfg.tenantid, exc)
        log.error("aborting: %s", exc)
        return 3
    finally:
        if client is not None:
            client.close()

    # MIN of each _s_env's own MAX(_s_silver_ts) on silver.project, not the
    # overall MAX — a tenant can have rows in more than one _s_env (e.g.
    # si_dev and datamart both cdc_enabled); a shared cursor advanced to the
    # fastest-moving env's watermark would permanently skip a slower env's
    # still-unprocessed rows. Same pattern as the six canonical loaders'
    # get_max_silver_ts_per_env usage.
    max_silver_ts = get_max_silver_ts_per_env(cfg.pg_dsn, "project", "_s_tenantid", cfg.tenantid)
    if cfg.dry_run:
        log.info("dry-run: cursor NOT advanced (would set last_processed_ts=%s)", max_silver_ts)
    elif cfg.rebuild:
        log.info("rebuild: cursor intentionally NOT advanced (incremental watermark preserved)")
    elif stats.failed > 0:
        mark_error(cfg.pg_dsn, _STEP_NAME, cfg.tenantid, f"{stats.failed} bulk write failures")
    elif max_silver_ts > last_ts:
        write_cursor(cfg.pg_dsn, _STEP_NAME, cfg.tenantid, max_silver_ts)

    log.info("loader done | seen=%d upserted=%d modified=%d failed=%d batches=%d",
             seen, stats.upserted, stats.modified, stats.failed, stats.batches)
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
