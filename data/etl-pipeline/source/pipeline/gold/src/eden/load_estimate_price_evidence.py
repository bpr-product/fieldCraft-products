"""Silver → Gold loader: estimate_price_evidence (Eden Finance agent — Q1).

Answers "why did this project's estimate price change" with a deterministic
reconciliation instead of a guess. One document per (tenantId, sourceEnv,
projectId, version) of ``silver.project_estimate`` — grain
``eden_estimate_price_evidence``.

SOURCE MODEL (see EDEN_CustomerSupport_Reasoning_Constraints.md, Q1):
  * silver.project_estimate is the estimate itself, versioned per project.
    The five cost buckets (material_cost/worker_cost/tool_cost/overhead_cost/
    total_cost) plus margin live here, one row per (project_id, version).
  * silver.bom is the proof mechanism: SUM(bom.total_cost) for the same
    (project_id, version), filtered `invalid = FALSE` so a superseded BOM
    line is never double-counted, is compared against
    project_estimate.material_cost — a match means a deterministic,
    provable cause instead of a guess.
  * silver.vendor_material_map is pure join glue from a bom line out to
    silver.material_cost (the real price-history table, independent of any
    one project's BOM). effective_startdate/effective_enddate let us pick
    the price that was actually in force at the estimate's created_at,
    not whatever the current price happens to be today.
  * receipts.project_id / purchase_order.project_id were ruled out as the
    "obvious" cause (0/3 and 1/8 rows populated in the tenant checked) —
    not built on here.

Upsert key: (tenantId, sourceEnv, projectId, version)
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
from gold.pipeline.cursor_store import read_cursor, write_cursor, mark_error, EPOCH  # noqa: E402
from gold.pipeline.lineage import lineage_fields, set_on_insert, parse_ts  # noqa: E402

COLLECTION = "estimate_price_evidence"
FEATURE_NAME = "eden"
_STEP_NAME = "load_estimate_price_evidence"
_contract_validator = ContractValidator("eden_estimate_price_evidence")
TRANSIENT_ERRORS = (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError)
log = logging.getLogger("eden.estimate_price_evidence_loader")

# Reconciliation is "matched" when the BOM sum and the estimate's
# material_cost bucket agree within this tolerance (float rounding /
# currency-cents drift, not a real discrepancy).
_RECONCILIATION_TOLERANCE = 0.01


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
        p = argparse.ArgumentParser(description="Silver → Gold loader: estimate_price_evidence")
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


# Grain: one row per (project_id, version) of silver.project_estimate.
# LAG(...) OVER w gives the previous version's cost buckets in the same
# query, so shape_doc() can compute per-bucket deltas without a second
# round trip. bom_agg reconciles SUM(bom.total_cost) for the SAME version
# against project_estimate.material_cost, and for each BOM line resolves
# the vendor price that was actually in force at the estimate's
# created_at (priceAtEstimate) plus the immediately-preceding price
# (priceBefore) — the two numbers a "did the vendor's price change" answer
# needs.
_SILVER_QUERY = """
WITH estimates AS (
    SELECT
        pe.id                          AS estimate_id,
        pe._s_tenantid                 AS tenantid,
        pe._s_env                      AS source_env,
        pe.project_id                  AS project_id,
        pe.version                     AS version,
        pe.material_cost               AS material_cost,
        pe.worker_cost                 AS worker_cost,
        pe.tool_cost                   AS tool_cost,
        pe.overhead_cost               AS overhead_cost,
        pe.margin                      AS margin,
        pe.total_cost                  AS total_cost,
        pe.created_at                  AS created_at,
        pe._s_lsn                      AS silver_lsn,
        pe._s_silver_ts                AS estimate_silver_ts,
        LAG(pe.material_cost)  OVER w  AS prev_material_cost,
        LAG(pe.worker_cost)    OVER w  AS prev_worker_cost,
        LAG(pe.tool_cost)      OVER w  AS prev_tool_cost,
        LAG(pe.overhead_cost)  OVER w  AS prev_overhead_cost,
        LAG(pe.margin)         OVER w  AS prev_margin,
        LAG(pe.total_cost)     OVER w  AS prev_total_cost,
        LAG(pe.version)        OVER w  AS prev_version,
        LAG(pe.created_at)     OVER w  AS prev_created_at
    FROM silver.project_estimate pe
    WHERE pe._s_tenantid   = %(tenantid)s
      AND pe.invalid        = FALSE
      AND pe._s_is_deleted  = FALSE
    WINDOW w AS (PARTITION BY pe.project_id, pe._s_env ORDER BY pe.version)
)
SELECT
    est.*,
    p.description                       AS description,
    p.order_no                          AS order_no,
    p.customer                          AS customer_raw,
    p._s_silver_ts                      AS project_silver_ts,
    COALESCE(bom_agg.bom_total, 0)       AS bom_total_cost,
    bom_agg.bom_silver_ts               AS bom_silver_ts,
    COALESCE(bom_agg.bom_lines, '[]'::json) AS bom_lines
FROM estimates est
JOIN silver.project p
       ON p.id            = est.project_id
      AND p._s_tenantid   = est.tenantid
      AND p._s_env        = est.source_env
      AND p._s_is_deleted = FALSE
LEFT JOIN LATERAL (
    SELECT
        -- Recomputed as quantity x price-at-est.created_at (time-consistent),
        -- not trusted from b.total_cost — see load_project_health.py's
        -- identical fix for why (b.total_cost is frozen at last-write time,
        -- not point-in-time correct when a price is bumped without a
        -- matching bom.version bump). Falls back to b.total_cost when no
        -- material_cost row is resolvable.
        SUM(COALESCE(b.quantity * mc_at.cost, b.total_cost)) AS bom_total,
        MAX(b._s_silver_ts)             AS bom_silver_ts,
        JSON_AGG(JSON_BUILD_OBJECT(
            'materialName',       b.material_name,
            'quantity',           b.quantity,
            'unit',               b.unit,
            'unitCost',           b.unit_cost,
            'lineTotal',          b.total_cost,
            'vendorMaterialMapId', b.vendor_material_map_id,
            'priceAtEstimate',    mc_at.cost,
            'priceEffectiveFrom', mc_at.effective_startdate::text,
            'priceEffectiveTo',   mc_at.effective_enddate::text,
            'priceBefore',        mc_prev.cost
        ) ORDER BY b.id)                AS bom_lines
    FROM silver.bom b
    LEFT JOIN LATERAL (
        SELECT mc.cost, mc.effective_startdate, mc.effective_enddate
        FROM silver.material_cost mc
        WHERE mc.vendor_material_map_id = b.vendor_material_map_id
          AND mc._s_tenantid   = b._s_tenantid
          AND mc._s_env        = b._s_env
          AND mc._s_is_deleted = FALSE
          AND mc.effective_startdate <= est.created_at
          AND (mc.effective_enddate IS NULL OR mc.effective_enddate >= est.created_at)
        -- Ordered by id DESC ALONE (not effective_startdate) — see
        -- load_project_health.py's identical fix for why startdate-first
        -- ordering picks the wrong row on overlapping windows (verified
        -- live, tenant tn22473627).
        ORDER BY mc.id DESC
        LIMIT 1
    ) mc_at ON TRUE
    LEFT JOIN LATERAL (
        SELECT mc2.cost
        FROM silver.material_cost mc2
        WHERE mc2.vendor_material_map_id = b.vendor_material_map_id
          AND mc2._s_tenantid   = b._s_tenantid
          AND mc2._s_env        = b._s_env
          AND mc2._s_is_deleted = FALSE
          AND mc2.effective_enddate < est.created_at
        ORDER BY mc2.id DESC
        LIMIT 1
    ) mc_prev ON TRUE
    WHERE b._s_tenantid   = est.tenantid
      AND b._s_env        = est.source_env
      AND b.project_id    = est.project_id
      -- Resolve the EFFECTIVE bom version rather than requiring an exact
      -- match on est.version — see load_project_health.py's identical fix
      -- (verified live, tenant tn22473627 project order_no 597339: bom.version
      -- doesn't always advance in lockstep with project_estimate.version).
      AND b.version = (
          SELECT MAX(v.version)
          FROM silver.bom v
          WHERE v._s_tenantid  = est.tenantid
            AND v._s_env       = est.source_env
            AND v.project_id   = est.project_id
            AND v.version     <= est.version
            AND v._s_is_deleted = FALSE
      )
      AND b._s_is_deleted  = FALSE
      AND b.invalid        = FALSE
) bom_agg ON TRUE
ORDER BY est.project_id, est.version
"""


def _scoped_query(project_id: str | None) -> str:
    if project_id is None:
        return _SILVER_QUERY
    return _SILVER_QUERY.replace(
        "ORDER BY est.project_id, est.version",
        "WHERE est.project_id = %(project_id)s\nORDER BY est.project_id, est.version",
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
    """Build shaped Gold documents for a tenant (or a single project)."""
    return [
        shape_doc(row, gold_ts)
        for row in iter_silver_rows(pg_dsn, tenantid, max_docs, project_id)
    ]


_NEWNESS_SQL = """
SELECT EXISTS (
    SELECT 1 FROM silver.project_estimate WHERE _s_tenantid = %(t)s AND _s_silver_ts > %(s)s
    UNION ALL
    SELECT 1 FROM silver.bom              WHERE _s_tenantid = %(t)s AND _s_silver_ts > %(s)s
)
"""

# MIN of each _s_env's own MAX (combined across both tables for that env),
# not the overall MAX — a shared cursor advanced to the fastest-moving
# env's watermark would mask a slower env's still-unprocessed update
# indefinitely (see load_bom_stock_gap.py / canonical loaders for the same
# pattern and the 2026-07-23 sourceEnv fix this mirrors).
_MAX_TS_SQL = """
SELECT MIN(env_max) FROM (
    SELECT _s_env, MAX(ts) AS env_max FROM (
        SELECT _s_env, _s_silver_ts AS ts FROM silver.project_estimate WHERE _s_tenantid = %(t)s
        UNION ALL
        SELECT _s_env, _s_silver_ts AS ts FROM silver.bom              WHERE _s_tenantid = %(t)s
    ) rows
    GROUP BY _s_env
) per_env
"""


def _has_new_silver_rows(pg_dsn: str, tenantid: str, since: datetime) -> bool:
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(_NEWNESS_SQL, {"t": tenantid, "s": since})
        return bool(cur.fetchone()[0])


def _list_project_ids(pg_dsn: str, tenantid: str) -> list[str]:
    """Every non-deleted project id for the tenant — used to run the (heavy)
    per-project _SILVER_QUERY once per project instead of once, unscoped, for
    the whole tenant. See run()'s docstring-style comment for why: bom_agg's
    LATERAL joins into silver.bom/silver.material_cost have no supporting
    index on either table, so an unscoped run pays that unindexed per-row
    cost across EVERY project in the tenant inside one query. This lookup
    itself is cheap (silver.project, small/indexed relative to bom/
    material_cost) — same pattern gold_eden_stream.py's EdenCdcRouter already
    uses for its own project-id lookups.
    """
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM silver.project WHERE _s_tenantid = %(t)s AND _s_is_deleted = FALSE",
            {"t": tenantid},
        )
        return [str(r[0]) for r in cur.fetchall()]


def _get_max_silver_ts(pg_dsn: str, tenantid: str) -> datetime:
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(_MAX_TS_SQL, {"t": tenantid})
        ts = cur.fetchone()[0]
    if ts is None:
        return EPOCH
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


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


def _delta(curr, prev) -> float | None:
    c, p = _float(curr), _float(prev)
    if c is None or p is None:
        return None
    return round(c - p, 2) + 0.0


def _maybe_json(v):
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


def _customer_display_name(customer: dict | None) -> str | None:
    if not customer:
        return None
    parts = [customer.get("firstName"), customer.get("lastName")]
    name = " ".join(p.strip() for p in parts if p and str(p).strip())
    return name or None


def _shape_bom_line(raw: dict) -> dict:
    price_at = _float(raw.get("priceAtEstimate"))
    price_before = _float(raw.get("priceBefore"))
    return {
        "materialName":        raw.get("materialName"),
        "quantity":            _float(raw.get("quantity")),
        "unit":                raw.get("unit"),
        "unitCost":            _float(raw.get("unitCost")),
        "lineTotal":           _float(raw.get("lineTotal")),
        "vendorMaterialMapId": (str(raw["vendorMaterialMapId"])
                                 if raw.get("vendorMaterialMapId") is not None else None),
        "priceAtEstimate":     price_at,
        "priceEffectiveFrom":  parse_ts(raw.get("priceEffectiveFrom")),
        "priceEffectiveTo":    parse_ts(raw.get("priceEffectiveTo")),
        "priceBefore":         price_before,
        # The concrete "did the vendor's price change" signal Q1 needs —
        # true only when both prices are known and they actually differ
        # (not just "no prior price on record").
        "vendorPriceChanged":  (price_at is not None and price_before is not None
                                 and round(price_at, 2) != round(price_before, 2)),
    }


def shape_doc(row: dict, gold_ts: datetime) -> dict:
    watermarks = {
        "project_estimate": _to_ts(row.get("estimate_silver_ts")),
        "bom":              _to_ts(row.get("bom_silver_ts")),
        "project":          _to_ts(row.get("project_silver_ts")),
    }
    valid_wm = [ts for ts in watermarks.values() if ts is not None]
    lag_ms = int((gold_ts - min(valid_wm)).total_seconds() * 1000) if valid_wm else 0

    material_cost = _float(row.get("material_cost"))
    bom_total = _float(row.get("bom_total_cost")) or 0.0
    reconciliation_delta = (
        round(material_cost - bom_total, 2) if material_cost is not None else None
    )
    reconciled = (
        reconciliation_delta is not None
        and abs(reconciliation_delta) <= _RECONCILIATION_TOLERANCE
    )

    bom_lines = [_shape_bom_line(b) for b in (row.get("bom_lines") or [])]
    price_changes = [b for b in bom_lines if b["vendorPriceChanged"]]

    customer = _maybe_json(row.get("customer_raw"))
    customer = customer if isinstance(customer, dict) else None

    cost_deltas = {
        "materialCost": _delta(row.get("material_cost"), row.get("prev_material_cost")),
        "workerCost":   _delta(row.get("worker_cost"),   row.get("prev_worker_cost")),
        "toolCost":     _delta(row.get("tool_cost"),     row.get("prev_tool_cost")),
        "overheadCost": _delta(row.get("overhead_cost"), row.get("prev_overhead_cost")),
        "margin":       _delta(row.get("margin"),        row.get("prev_margin")),
        "totalCost":    _delta(row.get("total_cost"),    row.get("prev_total_cost")),
    }
    # Which bucket actually moved — what a "why did the price change" answer
    # should lead with, rather than making the reader scan every field.
    changed_buckets = [k for k, v in cost_deltas.items() if v not in (None, 0.0)]

    doc = {
        "tenantId":            row["tenantid"],
        "projectId":           str(row["project_id"]),
        "projectName":         (row.get("description")
                                  or _customer_display_name(customer)
                                  or row.get("order_no")),
        "orderNo":             row.get("order_no"),
        "version":             row.get("version"),
        "prevVersion":         row.get("prev_version"),
        "createdAt":           parse_ts(row.get("created_at")),
        "prevCreatedAt":       parse_ts(row.get("prev_created_at")),
        "materialCost":        material_cost,
        "workerCost":          _float(row.get("worker_cost")),
        "toolCost":            _float(row.get("tool_cost")),
        "overheadCost":        _float(row.get("overhead_cost")),
        "margin":              _float(row.get("margin")),
        "totalCost":           _float(row.get("total_cost")),
        "costDeltas":          cost_deltas,
        "changedBuckets":      changed_buckets,
        "bomReconciliation": {
            "bomTotalCost":    bom_total,
            "estimateMaterialCost": material_cost,
            "delta":           reconciliation_delta,
            "reconciled":      reconciled,
            "toleranceUsed":   _RECONCILIATION_TOLERANCE,
        },
        "bomLines":            bom_lines,
        "materialPriceChanges": price_changes,
        "_grain":              "eden_estimate_price_evidence",
        "_schemaVersion":      1,
        "_silverLsn":          int(row.get("silver_lsn") or 0),
        "isDeleted":           False,
        "dataAsOf":            gold_ts,
        "_silverWatermarks":   watermarks,
        "lafieldcraft":               lag_ms,
    }
    doc.update(lineage_fields(FEATURE_NAME, gold_ts, iso=False,
                              source_updated_at=watermarks.get("project_estimate"),
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


def _upsert_op(d: dict, gold_ts: datetime) -> UpdateOne:
    return UpdateOne(
        {"tenantId": d["tenantId"], "projectId": d["projectId"],
         "version": d["version"], "sourceEnv": d.get("sourceEnv"),
         "$or": [{"_silverLsn": {"$exists": False}},
                 {"_silverLsn": {"$lte": d.get("_silverLsn", 0)}}]},
        {"$set": d, "$setOnInsert": set_on_insert(gold_ts, iso=False)},
        upsert=True,
    )


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
                             retryWrites=False, appname="eden-estimate-price-evidence")
        coll = client[cfg.db_name][COLLECTION]

    pending: list[dict] = []
    seen = 0

    def flush() -> None:
        if not pending:
            return
        ops = [_upsert_op(d, gold_ts) for d in pending]
        for d in pending:
            _contract_validator.validate(d, COLLECTION)
        if ops and not cfg.dry_run and coll is not None:
            bulk_write_with_retry(coll, ops, cfg, stats)
        pending.clear()

    try:
        # Scoped per project (see _list_project_ids docstring) rather than one
        # unscoped iter_silver_rows(cfg.pg_dsn, cfg.tenantid, cfg.max_docs)
        # call for the whole tenant — bom_agg's LATERAL joins into silver.bom/
        # silver.material_cost have no supporting index, so running this
        # unscoped paid that unindexed cost once per (project, version) across
        # EVERY project in the tenant, inside a single query. cfg.max_docs is
        # a GLOBAL cap across all projects (not per-project), so it's checked
        # here in the outer loop rather than passed into iter_silver_rows.
        for project_id in _list_project_ids(cfg.pg_dsn, cfg.tenantid):
            if _shutdown or (cfg.max_docs > 0 and seen >= cfg.max_docs):
                break
            for row in iter_silver_rows(cfg.pg_dsn, cfg.tenantid, project_id=project_id):
                if _shutdown or (cfg.max_docs > 0 and seen >= cfg.max_docs):
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
