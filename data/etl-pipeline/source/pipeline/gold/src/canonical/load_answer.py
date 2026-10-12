"""Silver → Gold loader: answer (Canonical Gold).

Reads silver.answers and upserts a lean, cross-feature passthrough of each
answer row into gold_canonical.answer: source snake_case-derived fields
(answerId/projectId as integers, answersData parsed to a JSON object,
createdDate/updatedDate/invalid as raw PG text) plus a precomputed
``templateId`` foreign key into this same database's `question` collection.

templateId is resolved by joining (trade_oid, question_template_version) —
the same key load_bid_context.py's per-project LATERAL join uses — against
silver.fieldcraft_provider_question_template. Precomputing it here means any
consumer (Eden, Q&A recommendation, a future feature) can look up the parsed
question set via gold_canonical.question.templateId without redoing that
trade_oid+version match itself.

Distinct from gold_qa_recommendation.qa_answer: that collection additionally
embeds BOM lines per project, a qa_recommendation-feature-specific
enrichment that does not belong in cross-feature canonical data. This
collection is deliberately leaner.

Soft-delete: Silver never physically deletes (a delete is an op=u that sets
_s_is_deleted=TRUE). The query intentionally INCLUDES soft-deleted rows so
the recompute marks the Gold doc isDeleted=true instead of orphaning it.

Upsert key: (tenantId, sourceEnv, answerId)

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
from gold.src.monitoring.circuit_breaker import CircuitBreaker  # noqa: E402
from gold.src.monitoring.contract_validator import ContractValidator  # noqa: E402
from gold.pipeline.cursor_store import (  # noqa: E402
    read_cursor, write_cursor, mark_error, get_max_silver_ts_per_env, EPOCH,
)
from gold.pipeline.lineage import lineage_fields, parse_ts, set_on_insert  # noqa: E402

COLLECTION = "answer"
FEATURE_NAME = "canonical"
_STEP_NAME = "load_answer"
_contract_validator = ContractValidator("canonical_answer")
TRANSIENT_ERRORS = (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError)
log = logging.getLogger("canonical.answer_loader")

_TEMPLATE_TENANT = os.environ.get("QUESTION_TEMPLATE_TENANT", "fieldcraft_provider")

_cb: "CircuitBreaker | None" = None


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
        p = argparse.ArgumentParser(description="Silver → Gold loader: answer")
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
                       help="Full rebuild: read ALL current Silver (ignore the "
                            "incremental cursor) and do NOT advance it.")
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


# Soft-deleted rows are INTENTIONALLY included — see module docstring.
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
    a.trade_oid,
    a.question_template_version,
    a.schema_version,
    a.status,
    a.created_by,
    a.created_date         AS created_date,
    a.updated_date         AS updated_date,
    a.invalid::text        AS invalid,
    a.answers_data
FROM silver.answers a
WHERE a._s_tenantid  = %(tenantid)s
  AND a._s_silver_ts > %(last_ts)s
ORDER BY a._s_silver_ts, a.id
"""

_TEMPLATE_ID_QUERY = """
SELECT id AS template_id, trade_oid, version
FROM silver.fieldcraft_provider_question_template
WHERE trade_oid = ANY(%(trade_oids)s)
  AND version   = ANY(%(versions)s)
  AND _s_tenantid = %(template_tenant)s
  AND (%(env)s::text IS NULL OR _s_env = %(env)s)
"""


def iter_silver_rows(
    pg_dsn: str, tenantid: str, max_docs: int = 0, last_ts: datetime = EPOCH,
) -> Iterator[dict]:
    conn = psycopg2.connect(pg_dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(_SILVER_QUERY, {"tenantid": tenantid, "last_ts": last_ts})
            rows = cur if max_docs <= 0 else (r for _, r in zip(range(max_docs), cur))
            for row in rows:
                yield dict(row)
    finally:
        conn.close()


def _fetch_template_ids(
    pg_dsn: str, pairs: list[tuple[str, int]], env: str | None = None
) -> dict[tuple[str, int], int]:
    """Return {(trade_oid, version): templateId} for a batch of answer rows.

    Scoped to ``env`` (the answer rows' own source_env) so a template row
    sharing a trade_oid+version under a *different* Silver env can never be
    mismatched in — mirrors the env-scoping load_qa_recommendation.py uses
    for its BOM lookup.
    """
    pairs = [p for p in pairs if p[0] is not None and p[1] is not None]
    if not pairs:
        return {}
    trade_oids = list({p[0] for p in pairs})
    versions = list({p[1] for p in pairs})
    result: dict[tuple[str, int], int] = {}
    conn = psycopg2.connect(pg_dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(
                _TEMPLATE_ID_QUERY,
                {"trade_oids": trade_oids, "versions": versions,
                 "template_tenant": _TEMPLATE_TENANT, "env": env},
            )
            for row in cur:
                result[(row["trade_oid"], row["version"])] = row["template_id"]
    finally:
        conn.close()
    return result


def _has_new_silver_rows(pg_dsn: str, tenantid: str, since: datetime) -> bool:
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM silver.answers WHERE _s_tenantid = %s AND _s_silver_ts > %s"
            ")",
            (tenantid, since),
        )
        return bool(cur.fetchone()[0])


def _get_max_silver_ts(pg_dsn: str, tenantid: str) -> datetime:
    return get_max_silver_ts_per_env(pg_dsn, "answers", "_s_tenantid", tenantid)


def _answers_data_object(raw: Any) -> Any:
    """Return answers_data as a JSON object (dict/list), or None.

    silver.answers.answers_data is TEXT holding JSON; store it parsed so the
    Gold field is an object, never a raw string.
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


def shape_doc(row: dict, gold_ts: datetime, template_id: int | None = None) -> dict | None:
    if not row.get("answer_id"):
        return None

    doc = {
        "tenantId":                  row["tenantid"],
        "answerId":                  row["answer_id"],
        "projectId":                 row.get("project_id"),
        "tradeOid":                  row.get("trade_oid"),
        "questionTemplateVersion":   row.get("question_template_version"),
        # precomputed FK into gold_canonical.question — see module docstring
        "templateId":                template_id,
        "answersData":               _answers_data_object(row.get("answers_data")),
        "status":                    row.get("status"),
        "schemaVersion":             row.get("schema_version"),
        "createdBy":                 row.get("created_by"),
        "createdDate":               parse_ts(row.get("created_date")),
        "updatedDate":               parse_ts(row.get("updated_date")),
        "invalid":                   row.get("invalid"),
        "isDeleted":                 bool(row.get("is_deleted") or False),
        "deletedAt":                 parse_ts(row.get("deleted_at")),
        "_silverLsn":                int(row.get("silver_lsn") or 0),
        "_grain":                    "canonical_answer",
        "_schemaVersion":            1,
    }
    doc.update(lineage_fields(FEATURE_NAME, gold_ts, iso=False,
                              source_updated_at=row.get("silver_ts"),
                              source_env=row.get("source_env")))
    return doc


def build_documents(pg_dsn: str, tenant: str, gold_ts: datetime) -> list[dict]:
    """Return current answer documents for a tenant (full recompute, no watermark)."""
    rows = list(iter_silver_rows(pg_dsn, tenant, max_docs=0, last_ts=EPOCH))
    pairs_by_env: dict[str | None, set[tuple[str, int]]] = {}
    for r in rows:
        if r.get("trade_oid") and r.get("question_template_version") is not None:
            pairs_by_env.setdefault(r.get("source_env"), set()).add(
                (r["trade_oid"], r["question_template_version"])
            )
    template_ids_by_key: dict[tuple[str | None, str, int], int] = {}
    for env_value, pairs in pairs_by_env.items():
        resolved = _fetch_template_ids(pg_dsn, list(pairs), env=env_value)
        for (trade_oid, version), tid in resolved.items():
            template_ids_by_key[(env_value, trade_oid, version)] = tid
    docs: list[dict] = []
    for row in rows:
        key = (row.get("source_env"), row.get("trade_oid"), row.get("question_template_version"))
        doc = shape_doc(row, gold_ts, template_id=template_ids_by_key.get(key))
        if doc is not None:
            docs.append(doc)
    return docs


@dataclass
class WriteStats:
    upserted: int = 0
    modified: int = 0
    matched:  int = 0
    skipped:  int = 0
    failed:   int = 0
    batches:  int = 0


def bulk_write_with_retry(
    coll: Collection, ops: list[UpdateOne], cfg: LoaderConfig, stats: WriteStats,
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
            log.error("bulk write partial failure: %d errors (first: %s)",
                      len(errors), errors[0] if errors else "")
            stats.batches += 1
            return
        except TRANSIENT_ERRORS as exc:
            if attempt == cfg.max_retries:
                log.error("bulk write failed after %d attempts: %s", attempt, exc)
                raise
            log.warning("transient error attempt %d/%d: %s — retry in %.1fs",
                        attempt, cfg.max_retries, exc, delay)
            time.sleep(delay)
            delay *= 2
        except OperationFailure as exc:
            log.error("operation failure (non-retryable): %s", exc)
            raise


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
    log.info("loader start | db=%s.%s tenant=%s dry_run=%s mongo=%s",
             cfg.db_name, COLLECTION, cfg.tenantid, cfg.dry_run, _mask_uri(cfg.mongo_uri))

    last_ts = EPOCH if cfg.rebuild else read_cursor(cfg.pg_dsn, _STEP_NAME, cfg.tenantid)
    if cfg.rebuild:
        log.info("rebuild mode | full read from EPOCH; cursor will NOT be advanced")
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
                             retryWrites=False, appname="canonical-answer")
        coll = client[cfg.db_name][COLLECTION]

    pending: list[dict] = []
    seen = 0

    def flush() -> None:
        if not pending:
            return
        # Group by source_env before the template lookup — pending may span
        # multiple _s_env values for the same tenantid. See _fetch_template_ids.
        pairs_by_env: dict[str | None, set[tuple[str, int]]] = {}
        for row in pending:
            if row.get("trade_oid") and row.get("question_template_version") is not None:
                pairs_by_env.setdefault(row.get("source_env"), set()).add(
                    (row["trade_oid"], row["question_template_version"])
                )
        template_ids_by_key: dict[tuple[str | None, str, int], int] = {}
        for env_value, pairs in pairs_by_env.items():
            resolved = _fetch_template_ids(cfg.pg_dsn, list(pairs), env=env_value)
            for (trade_oid, version), tid in resolved.items():
                template_ids_by_key[(env_value, trade_oid, version)] = tid

        ops: list[UpdateOne] = []
        for row in pending:
            key = (row.get("source_env"), row.get("trade_oid"), row.get("question_template_version"))
            doc = shape_doc(row, gold_ts, template_id=template_ids_by_key.get(key))
            if doc is None:
                continue
            _contract_validator.validate(doc, COLLECTION)
            lsn = doc.get("_silverLsn", 0)
            ops.append(
                UpdateOne(
                    {"tenantId": cfg.tenantid, "answerId": doc["answerId"],
                     "sourceEnv": doc.get("sourceEnv"),
                     "$or": [{"_silverLsn": {"$exists": False}},
                             {"_silverLsn": {"$lte": lsn}}]},
                    {"$set": doc, "$setOnInsert": set_on_insert(gold_ts, iso=False)},
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
                log.info("progress | seen=%d upserted=%d modified=%d skipped=%d",
                         seen, stats.upserted, stats.modified, stats.skipped)
        flush()
    except TRANSIENT_ERRORS as exc:
        mark_error(cfg.pg_dsn, _STEP_NAME, cfg.tenantid, exc)
        log.error("aborting after exhausted retries: %s", exc)
        return 3
    finally:
        if client is not None:
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

    log.info("loader done | seen=%d upserted=%d modified=%d matched=%d skipped=%d failed=%d batches=%d",
             seen, stats.upserted, stats.modified, stats.matched, stats.skipped, stats.failed, stats.batches)
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
