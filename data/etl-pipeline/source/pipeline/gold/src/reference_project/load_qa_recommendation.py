"""Load qa_answer documents from Silver PostgreSQL into Gold MongoDB.

Reads silver.answers (+ silver.bom per project) and upserts a raw passthrough
of each answer row into gold_qa_recommendation.qa_answer: the source snake_case
fields and values are kept as-is (answer_id/project_id as integers;
created_date/updated_date/invalid as their raw PG text; answers_data parsed
into a JSON object). BOM lines for the answer's project are fetched in bulk per
batch and embedded under the top-level ``bom`` field. The only added fields are
the platform plumbing the pipeline relies on — tenantId (partition + upsert
key), isDeleted/deletedAt (soft-delete tombstone), _silverLsn (LSN idempotency
guard), _grain/_schemaVersion (mandatory Gold meta), and the standard lineage
block (dataOrigin / sourceUpdatedAt / goldUpdatedAt / pipelineRunId) for
cross-collection traceability — same as every other Gold collection.

Soft-delete: Silver never physically deletes (a delete is an op=u that sets
_s_is_deleted=TRUE). The query intentionally INCLUDES soft-deleted rows so the
recompute marks the Gold doc isDeleted=true instead of orphaning it.

Upsert key : (tenantId, answer_id) — one document per Q&A answer.

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
    AutoReconnect,
    BulkWriteError,
    ConnectionFailure,
    NetworkTimeout,
    OperationFailure,
    ServerSelectionTimeoutError,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHARED_SRC = str(_REPO_ROOT / "shared")
_GOLD_SRC   = str(_REPO_ROOT / "gold" / "src")
_DATA_PIPELINE = str(_REPO_ROOT)
for _p in (_SHARED_SRC, _GOLD_SRC, _DATA_PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from secret_resolver import get_mongo_uri, get_postgres_dsn  # noqa: E402
from gold.src.monitoring.circuit_breaker import CircuitBreaker, CircuitOpenError  # noqa: E402
from gold.pipeline.cursor_store import (  # noqa: E402
    read_cursor, write_cursor, mark_error, get_max_silver_ts_per_env, EPOCH,
)
from gold.pipeline.lineage import lineage_fields, parse_ts  # noqa: E402

COLLECTION = "qa_answer"
FEATURE_NAME = "reference_project"
_DB_DEFAULT = "gold_qa_recommendation"
_STEP_NAME  = "load_qa_recommendation"
TRANSIENT_ERRORS = (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError)

log = logging.getLogger("qa_recommendation.answer_loader")

_cb: "CircuitBreaker | None" = None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


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
        p = argparse.ArgumentParser(
            description="Silver → Gold loader: qa_answer."
        )
        p.add_argument("--mongo-uri",    default=None)
        p.add_argument("--pg-dsn",       default=None)
        p.add_argument("--db",           dest="db_name",
                       default=os.environ.get("GOLD_QA_RECOMMENDATION_DB", _DB_DEFAULT))
        p.add_argument("--tenant",       required=not bool(os.environ.get("TENANT_ID")),
                       default=os.environ.get("TENANT_ID"))
        p.add_argument("--batch-size",   type=int,
                       default=int(os.environ.get("BATCH_SIZE", "200")))
        p.add_argument("--max-retries",  type=int,
                       default=int(os.environ.get("MAX_RETRIES", "5")))
        p.add_argument("--retry-backoff", type=float,
                       default=float(os.environ.get("RETRY_BACKOFF_S", "1.5")))
        p.add_argument("--dry-run",      action="store_true")
        p.add_argument("--max-docs",     type=int, default=int(os.environ.get("MAX_DOCS", "0")),
                       metavar="N", help="Cap Silver rows read (0=unlimited)")
        p.add_argument("--rebuild",      action="store_true",
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


# ---------------------------------------------------------------------------
# Silver query
# ---------------------------------------------------------------------------

# NOTE: soft-deleted rows (_s_is_deleted=TRUE) are INTENTIONALLY included so the
# recompute can tombstone the Gold doc (isDeleted=true) instead of leaving an
# orphan — Silver never physically deletes, and a soft-delete arrives as op=u.
_SILVER_QUERY = """
SELECT
    a._s_tenantid          AS tenantid,
    a._s_env               AS source_env,
    a._s_is_deleted        AS is_deleted,
    a._s_deleted_at        AS deleted_at,
    a._s_lsn               AS silver_lsn,
    a._s_silver_ts         AS silver_ts,
    a.id                   AS answer_id,
    a.project_id,
    a.question_template_version,
    a.schema_version,
    a.status,
    a.trade_oid,
    a.version,
    a.created_by,
    a.created_date         AS created_date,
    a.updated_date         AS updated_date,
    a.invalid::text        AS invalid,
    a.answers_data
FROM silver.answers a
WHERE a._s_tenantid  = %(tenantid)s
  AND a.invalid       = FALSE
  AND a._s_silver_ts  > %(last_ts)s
  AND (%(project_id)s::bigint IS NULL OR a.project_id = %(project_id)s::bigint)
ORDER BY a._s_silver_ts, a.id
"""

# BOM lines for a batch of projects — fetched separately so the answers query
# stays simple and the watermark stays on silver.answers._s_silver_ts.  BOM
# changes trigger a full project recompute via the streaming path (see
# _TRIGGER_TABLES in gold_qa_recommendation_stream.py).
_BOM_QUERY = """
SELECT
    b.id                   AS bom_id,
    b.project_id,
    b._s_env               AS bom_env,
    b.task_id,
    b.material_name,
    b.material_uid,
    b.cost_code::text      AS cost_code,
    b.quantity,
    b.unit,
    b.unit_cost,
    b.total_cost,
    b.over_head,
    b.profit_margin,
    b.tax,
    b.active,
    b.invalid              AS bom_invalid,
    b.version              AS bom_version,
    b.source_type_id,
    b.vendor_material_map_id,
    b.created_at           AS bom_created_at,
    b.updated_at           AS bom_updated_at,
    b._s_lsn               AS bom_lsn
FROM silver.bom b
WHERE b._s_tenantid   = %(tenantid)s
  AND b._s_is_deleted = FALSE
  AND b.project_id    = ANY(%(project_ids)s)
  AND (%(env)s::text IS NULL OR b._s_env = %(env)s)
ORDER BY b.project_id, b.id
"""


def iter_silver_rows(
    pg_dsn: str,
    tenantid: str,
    max_docs: int = 0,
    last_ts: datetime = EPOCH,
    project_id: int | None = None,
) -> Iterator[dict]:
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


def _fetch_bom_for_projects(
    pg_dsn: str, tenantid: str, project_ids: list[int], env: str | None = None
) -> dict[int, list[dict]]:
    """Return silver.bom lines grouped by project_id for a batch of projects.

    Returns an empty dict when project_ids is empty so callers need no guard.
    Only includes non-soft-deleted rows; the streaming path (bom in
    _TRIGGER_TABLES) handles re-embedding when BOM lines change.

    ``env`` scopes the lookup to a single ``_s_env`` (the answer row's own
    ``source_env``) so BOM rows sharing the same tenantid+project_id under a
    *different* Silver env (tenantid is only unique WITHIN one _s_env) can never
    bleed into this project's embedded ``bom`` block. ``None`` matches any env
    (used only when the caller cannot scope by env, e.g. legacy pre-multi-env
    rows with no source_env recorded).
    """
    if not project_ids:
        return {}
    by_project: dict[int, list[dict]] = {}
    conn = psycopg2.connect(pg_dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(
                _BOM_QUERY,
                {"tenantid": tenantid, "project_ids": project_ids, "env": env},
            )
            for row in cur:
                by_project.setdefault(row["project_id"], []).append(dict(row))
    finally:
        conn.close()
    return by_project


def _build_bom_block(lines: list[dict]) -> dict | None:
    """Build the embedded ``bom`` block from a list of silver.bom rows.

    Returns None (not an empty dict) when there are no lines so the field is
    absent from docs that have no BOM data rather than present as a stub.
    """
    if not lines:
        return None
    total = sum(r["total_cost"] for r in lines if r.get("total_cost") is not None)
    return {
        "line_count": len(lines),
        "total_cost": round(total, 6),
        "lines": [
            {
                "bom_id":              r["bom_id"],
                "task_id":             r.get("task_id"),
                "material_name":       r.get("material_name"),
                "material_uid":        r.get("material_uid"),
                "cost_code":           r.get("cost_code"),
                "quantity":            r.get("quantity"),
                "unit":                r.get("unit"),
                "unit_cost":           r.get("unit_cost"),
                "total_cost":          r.get("total_cost"),
                "over_head":           r.get("over_head"),
                "profit_margin":       r.get("profit_margin"),
                "tax":                 r.get("tax"),
                "active":              r.get("active"),
                "vendor_material_map_id": r.get("vendor_material_map_id"),
                "source_type_id":      r.get("source_type_id"),
                "bom_version":         r.get("bom_version"),
                "bom_created_at":      parse_ts(r.get("bom_created_at")),
                "bom_updated_at":      parse_ts(r.get("bom_updated_at")),
            }
            for r in lines
        ],
    }


def build_documents(pg_dsn: str, tenant: str, gold_ts: datetime,
                    project_id: int | None = None) -> list[dict]:
    """Return current qa_answer documents for a tenant (optionally one project).

    Reuses the same Silver query + shape_doc as the batch path but reads the
    CURRENT state (no watermark), so one recompute reflects the project's full
    present set of answers idempotently. BOM lines are fetched in bulk for all
    projects in the result set. Used by the real-time stream
    (gold_qa_recommendation_stream), project-scoped runs, and tenant rebuilds.
    Returns one document per Q&A answer (grain = qa_answer).
    """
    rows = list(iter_silver_rows(pg_dsn, tenant, max_docs=0, last_ts=EPOCH,
                                 project_id=project_id))
    # Group project_ids by source_env (answers._s_env) so each BOM lookup is
    # scoped to the SAME env as the answer rows it will be embedded into — a
    # tenantid (and in theory a project_id) is only unique WITHIN one _s_env,
    # not across envs, so a single ungrouped lookup could bleed BOM rows from
    # a different env into this project's embedded `bom` block. Keyed by
    # (source_env, project_id) rather than bare project_id for the same reason.
    pids_by_env: dict[str | None, set[int]] = {}
    for r in rows:
        pid = r.get("project_id")
        if pid:
            pids_by_env.setdefault(r.get("source_env"), set()).add(pid)
    bom_by_key: dict[tuple[str | None, int], list[dict]] = {}
    for env_value, pids in pids_by_env.items():
        per_env = _fetch_bom_for_projects(pg_dsn, tenant, list(pids), env=env_value)
        for pid, lines in per_env.items():
            bom_by_key[(env_value, pid)] = lines
    docs: list[dict] = []
    for row in rows:
        bom_lines = bom_by_key.get((row.get("source_env"), row.get("project_id")), [])
        doc = shape_doc(row, gold_ts, bom_lines=bom_lines)
        if doc is not None:
            docs.append(doc)
    return docs


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------


def _answers_data_object(raw: Any) -> Any:
    """Return answers_data as a JSON object (dict/list), or None.

    silver.answers.answers_data is TEXT holding JSON; store it parsed so the Gold
    field is an object, never a raw string. None/empty/unparseable → None.
    """
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def shape_doc(row: dict, gold_ts: datetime,
             bom_lines: list[dict] | None = None) -> dict | None:
    """Shape one qa_answer document.

    qa_answer is a raw passthrough of the source `silver.answers` row — original
    snake_case field names and values (answer_id/project_id as integers,
    answers_data unflattened, created_date/updated_date/invalid as their raw PG
    text). BOM lines for the row's project are embedded under ``bom`` when
    provided. The only additions are the platform fields the pipeline relies on:
      - tenantId    — Cosmos partition key AND the upsert filter key
      - _silverLsn  — LSN guard so an older CDC event can't clobber a newer one
      - _grain / _schemaVersion / _etlUpdatedAt — mandatory Gold meta

    Upsert key: (tenantId, answer_id).
    """
    if not row.get("answer_id"):
        return None

    doc = {
        # raw silver.answers business fields (passthrough — no rename/reshape)
        "answer_id":                 row["answer_id"],
        "project_id":                row.get("project_id"),
        "answers_data":              _answers_data_object(row.get("answers_data")),
        "created_by":                row.get("created_by"),
        "created_date":              parse_ts(row.get("created_date")),
        "updated_date":              parse_ts(row.get("updated_date")),
        "invalid":                   row.get("invalid"),
        "question_template_version": row.get("question_template_version"),
        "schema_version":            row.get("schema_version"),
        "status":                    row.get("status"),
        "trade_oid":                 row.get("trade_oid"),
        "version":                   row.get("version"),
        # BOM lines for this answer's project (None when project has no BOM rows)
        "bom":                       _build_bom_block(bom_lines or []),
        # platform plumbing (upsert key, tenant isolation, idempotency, meta)
        "tenantId":                  row["tenantid"],
        # soft-delete tombstone: a soft-deleted Silver row is marked here rather
        # than left as an orphan Gold doc (Silver never physically deletes).
        "isDeleted":                 bool(row.get("is_deleted") or False),
        "deletedAt":                 parse_ts(row.get("deleted_at")),
        "_silverLsn":                int(row.get("silver_lsn") or 0),
        "_grain":                    "qa_answer",
        "_schemaVersion":            3,
    }
    # Standard lineage block (goldUpdatedAt / dataOrigin / sourceUpdatedAt / …) —
    # same as every other Gold collection, for cross-collection traceability and
    # governance. Native datetimes, consistent with every other loader (2026-07-20
    # sweep — this was previously the only loader using iso=True).
    doc.update(lineage_fields(FEATURE_NAME, gold_ts,
                              source_updated_at=row.get("silver_ts"),
                              source_env=row.get("source_env")))
    return doc


# ---------------------------------------------------------------------------
# Bulk write with retry + circuit breaker
# ---------------------------------------------------------------------------


@dataclass
class WriteStats:
    upserted: int = 0
    modified: int = 0
    matched:  int = 0
    skipped:  int = 0
    failed:   int = 0
    batches:  int = 0


def bulk_write_with_retry(
    coll: Collection,
    ops: list[UpdateOne],
    cfg: LoaderConfig,
    stats: WriteStats,
) -> None:
    global _cb
    delay = cfg.retry_backoff_s
    for attempt in range(1, cfg.max_retries + 1):
        try:
            if _cb is None:
                _cb = CircuitBreaker(name=coll.full_name)
            res = _cb.call(lambda: coll.bulk_write(ops, ordered=False))
            stats.upserted += res.upserted_count or 0
            stats.modified += res.modified_count or 0
            stats.matched  += res.matched_count  or 0
            stats.batches  += 1
            return
        except BulkWriteError as exc:
            errors = exc.details.get("writeErrors", []) if exc.details else []
            stats.failed += len(errors)
            log.error(
                "bulk write partial failure: %d errors (first: %s)",
                len(errors), errors[0] if errors else "",
            )
            stats.batches += 1
            return
        except TRANSIENT_ERRORS as exc:
            if attempt == cfg.max_retries:
                log.error("bulk write failed after %d attempts: %s", attempt, exc)
                raise
            log.warning(
                "transient error attempt %d/%d: %s — retry in %.1fs",
                attempt, cfg.max_retries, exc, delay,
            )
            time.sleep(delay)
            delay *= 2
        except OperationFailure as exc:
            log.error("operation failure (non-retryable): %s", exc)
            raise


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

_shutdown = False


def _install_signal_handlers() -> None:
    def _handler(signum, _frame):
        global _shutdown
        log.warning("signal %s received — finishing current batch then stopping", signum)
        _shutdown = True

    signal.signal(signal.SIGINT, _handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handler)


def run(cfg: LoaderConfig) -> int:
    log.info(
        "loader start | db=%s.%s tenant=%s dry_run=%s mongo=%s",
        cfg.db_name, COLLECTION, cfg.tenantid, cfg.dry_run, _mask_uri(cfg.mongo_uri),
    )

    # Rebuild reads ALL current Silver (EPOCH) and must not advance the cursor.
    last_ts = EPOCH if cfg.rebuild else read_cursor(cfg.pg_dsn, _STEP_NAME, cfg.tenantid)
    if cfg.rebuild:
        log.info("rebuild mode | full read from EPOCH; cursor will NOT be advanced")
    gold_ts = datetime.now(timezone.utc)
    stats = WriteStats()

    client: MongoClient | None = None
    coll: Collection | None = None
    if not cfg.dry_run:
        client = MongoClient(
            cfg.mongo_uri,
            serverSelectionTimeoutMS=15_000,
            connectTimeoutMS=15_000,
            socketTimeoutMS=60_000,
            retryWrites=False,
            appname="qa-recommendation-loader",
        )
        coll = client[cfg.db_name][COLLECTION]

    pending: list[dict] = []
    seen = 0

    def flush() -> None:
        if not pending:
            return
        # Group by source_env before the BOM lookup — pending may span multiple
        # _s_env values for the same tenantid (tenantid is only unique WITHIN one
        # env), so a single ungrouped lookup could bleed another env's BOM rows
        # into this batch's embedded `bom` blocks. See build_documents() above.
        pids_by_env: dict[str | None, set[int]] = {}
        for row in pending:
            pid = row.get("project_id")
            if pid:
                pids_by_env.setdefault(row.get("source_env"), set()).add(pid)
        bom_by_key: dict[tuple[str | None, int], list[dict]] = {}
        for env_value, pids in pids_by_env.items():
            per_env = _fetch_bom_for_projects(cfg.pg_dsn, cfg.tenantid, list(pids), env=env_value)
            for pid, lines in per_env.items():
                bom_by_key[(env_value, pid)] = lines
        ops: list[UpdateOne] = []
        for row in pending:
            bom_lines = bom_by_key.get((row.get("source_env"), row.get("project_id")), [])
            doc = shape_doc(row, gold_ts, bom_lines=bom_lines)
            if doc is None:
                continue
            lsn = doc.get("_silverLsn", 0)
            ops.append(
                UpdateOne(
                    {
                        "tenantId": cfg.tenantid,
                        "answer_id": doc["answer_id"],
                        "sourceEnv": doc.get("sourceEnv"),
                        "$or": [
                            {"_silverLsn": {"$exists": False}},
                            {"_silverLsn": {"$lte": lsn}},
                        ],
                    },
                    {"$set": doc},
                    upsert=True,
                )
            )
        if ops and not cfg.dry_run and coll is not None:
            bulk_write_with_retry(coll, ops, cfg, stats)
        elif cfg.dry_run:
            stats.skipped += len(ops)
        pending.clear()

    try:
        for row in iter_silver_rows(cfg.pg_dsn, cfg.tenantid, cfg.max_docs, last_ts):
            if _shutdown:
                break
            seen += 1
            pending.append(row)
            if len(pending) >= cfg.batch_size:
                flush()
                log.info(
                    "progress | seen=%d upserted=%d modified=%d skipped=%d",
                    seen, stats.upserted, stats.modified, stats.skipped,
                )
        flush()

    except TRANSIENT_ERRORS as exc:
        mark_error(cfg.pg_dsn, _STEP_NAME, cfg.tenantid, exc)
        log.error("aborting after exhausted retries: %s", exc)
        return 3
    finally:
        if client is not None:
            client.close()

    # MIN of each _s_env's own MAX(_s_silver_ts) on silver.answers, not the
    # overall MAX — same cross-env-skip bug class already fixed for the six
    # canonical loaders and load_estimate_snapshot.py (BPE). A tenant can have
    # rows in more than one _s_env; a shared cursor advanced to the
    # fastest-moving env's watermark permanently skips a slower env's
    # still-unprocessed answers rows.
    max_silver_ts = get_max_silver_ts_per_env(cfg.pg_dsn, "answers", "_s_tenantid", cfg.tenantid)
    if cfg.dry_run:
        log.info("dry-run: cursor NOT advanced (would set last_processed_ts=%s)", max_silver_ts)
    elif cfg.rebuild:
        log.info("rebuild: cursor intentionally NOT advanced (incremental watermark preserved)")
    elif stats.failed > 0:
        mark_error(cfg.pg_dsn, _STEP_NAME, cfg.tenantid, f"{stats.failed} bulk write failures")
    elif max_silver_ts > last_ts:
        write_cursor(cfg.pg_dsn, _STEP_NAME, cfg.tenantid, max_silver_ts)

    total: int | None = None
    if not cfg.dry_run:
        verify_client = MongoClient(cfg.mongo_uri, appname="qa-recommendation-verify")
        try:
            total = verify_client[cfg.db_name][COLLECTION].count_documents(
                {"tenantId": cfg.tenantid}
            )
        finally:
            verify_client.close()

    log.info(
        "loader done | seen=%d upserted=%d modified=%d matched=%d "
        "skipped=%d failed=%d batches=%d tenant_total=%s",
        seen, stats.upserted, stats.modified, stats.matched,
        stats.skipped, stats.failed, stats.batches, total,
    )
    return 0 if stats.failed == 0 else 3


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _install_signal_handlers()
    try:
        cfg = LoaderConfig.from_args_and_env(argv)
    except RuntimeError as exc:
        log.error("config error: %s", exc)
        return 2
    return run(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
