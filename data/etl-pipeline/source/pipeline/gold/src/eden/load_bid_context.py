"""Silver → Gold loader: bid_context (Eden AI Assistant).

Reads silver.answers and silver.fieldcraft_provider_question_template (SDM) to produce
one document per project with:
  - Current form state (answered items)
  - Visible but unanswered items (derived from ConditionDef evaluation)
  - Next required item for Guide mode
  - Question template version

Upsert key: (tenantId, projectId)
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
from gold.src.canonical.odm_parser import parse_template as _parse_template  # noqa: E402

COLLECTION = "bid_context"
FEATURE_NAME = "eden"
_STEP_NAME = "load_bid_context"
_contract_validator = ContractValidator("eden_bid_context")
TRANSIENT_ERRORS = (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError,
                    CircuitOpenError)
log = logging.getLogger("eden.bid_context_loader")


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
        p = argparse.ArgumentParser(description="Silver → Gold loader: bid_context")
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


# question_template is GLOBAL reference data, all rows owned by the provider
# tenant (verified live 2026-06-15: 47 rows under 'fieldcraft_provider', none under any
# customer tenant). Override via env if the provider tenant id differs.
#
# Source table: silver.fieldcraft_provider_question_template, NOT bare silver.question_template.
# question_template is sourced from the fieldcraft_provider connector, so per
# universal_silver_merge.py::_namespaced_entity() it is namespaced under
# SILVER_NAMESPACED_SOURCE_DBS (fieldcraft_provider is in that list and question_template
# has no exemption like `tenant` does) — live CDC only ever populates the
# fieldcraft_provider_ prefixed table. Verified live 2026-07-16: bare silver.question_template
# has 0 rows; silver.fieldcraft_provider_question_template has 449. This loader was
# silently reading the empty table (same routing-gap pattern already fixed for
# silver.tenant on 2026-07-03), so bid_context.templateItems/answeredItems/
# visibleUnanswered have been empty for every project regardless of actual
# Q&A data. Fixed 2026-07-16.
_TEMPLATE_TENANT = os.environ.get("QUESTION_TEMPLATE_TENANT", "fieldcraft_provider")

_SILVER_QUERY = """
SELECT
    p._s_tenantid                                                               AS tenantid,
    p._s_env                                                                     AS source_env,
    p._s_is_deleted                                                              AS is_deleted,
    p._s_deleted_at                                                              AS deleted_at,
    p._s_lsn                                                                     AS silver_lsn,
    p._s_silver_ts                                                               AS project_silver_ts,
    p.id                                                AS project_id,

    -- Latest answers set for this project
    a.question_template_version,
    a.answers_data,
    a.version                                           AS answers_version,
    a.status,
    a.answers_silver_ts,

    -- Question template — the raw ODM/SDM JSON blob; parsed in shape_doc.
    -- (silver.fieldcraft_provider_question_template is one row per template with the
    --  whole structure in question_template_data — there are no per-item columns.)
    qt.question_template_data,
    qt.qt_silver_ts

FROM silver.project p

LEFT JOIN LATERAL (
    SELECT question_template_version, answers_data, version, status, trade_oid,
           _s_silver_ts AS answers_silver_ts
    FROM silver.answers
    WHERE _s_tenantid  = p._s_tenantid
      AND _s_env       = p._s_env
      AND project_id   = p.id
      AND _s_is_deleted = FALSE
      AND invalid       = FALSE
    ORDER BY version DESC NULLS LAST
    LIMIT 1
) a ON TRUE

LEFT JOIN LATERAL (
    SELECT
        qt.question_template_data,
        qt._s_silver_ts AS qt_silver_ts
    FROM silver.fieldcraft_provider_question_template qt
    WHERE qt._s_tenantid       = %(template_tenant)s
      AND qt._s_env            = p._s_env
      AND qt.version           = a.question_template_version
      AND (a.trade_oid IS NULL OR qt.trade_oid = a.trade_oid)
      AND qt._s_is_deleted     = FALSE
      AND qt.invalid           = FALSE
    -- Deterministic pick: exact trade match first (NULL=NULL counts as a match),
    -- then a global (trade_oid IS NULL) template, then highest version. Without
    -- the explicit global tiebreak, a null answers.trade_oid made every row's
    -- (qt.trade_oid = a.trade_oid) NULL, so a trade-specific template could be
    -- chosen arbitrarily instead of the generic one.
    ORDER BY (qt.trade_oid IS NOT DISTINCT FROM a.trade_oid) DESC,
             (qt.trade_oid IS NULL) DESC,
             qt.version DESC
    LIMIT 1
) qt ON TRUE

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
            params = {"tenantid": tenantid, "template_tenant": _TEMPLATE_TENANT}
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


def _parse_answers(answers_data) -> dict[str, dict]:
    """Parse answers_data into a dict keyed by ItemOID → {"value": ...}.

    Current source shape is ODM nested:
        {EventData:[{FormData:[{ItemGroupData:[{ItemData:[{ItemOID,Value},...]}]}]}]}
    Legacy flat list/dict shapes are still handled defensively. Only items with a
    non-empty Value count as answered.
    """
    if not answers_data:
        return {}
    if isinstance(answers_data, str):
        try:
            answers_data = json.loads(answers_data)
        except json.JSONDecodeError:
            return {}

    out: dict[str, dict] = {}

    # ODM nested structure
    if isinstance(answers_data, dict) and "EventData" in answers_data:
        for ev in answers_data.get("EventData") or []:
            for fd in (ev.get("FormData") or []) if isinstance(ev, dict) else []:
                for igd in (fd.get("ItemGroupData") or []) if isinstance(fd, dict) else []:
                    for item in (igd.get("ItemData") or []) if isinstance(igd, dict) else []:
                        if not isinstance(item, dict):
                            continue
                        oid = item.get("ItemOID")
                        val = item.get("Value")
                        if oid and val is not None and str(val) != "":
                            out[oid] = {"value": val}
        return out

    # Legacy flat list of {itemOid/id, value}
    if isinstance(answers_data, list):
        return {a.get("itemOid", a.get("id", "")): a for a in answers_data if isinstance(a, dict)}
    # Legacy flat dict keyed by itemOid. Normalise scalar values to {"value": v}
    # so downstream answer.get("value") never hits a str/list (AttributeError).
    if isinstance(answers_data, dict):
        return {
            k: (v if isinstance(v, dict) else {"value": v})
            for k, v in answers_data.items()
        }
    return {}


def shape_doc(row: dict, gold_ts: datetime) -> dict:
    answers_map = _parse_answers(row.get("answers_data"))
    template_items = _parse_template(row.get("question_template_data"))
    total_questions = len(template_items)

    answered_items = []
    visible_unanswered = []

    for item in template_items:
        item_oid = item.get("itemOid", "")
        if item_oid in answers_map:
            answer = answers_map[item_oid]
            answered_items.append({
                "itemOid":  item_oid,
                "question": item.get("question"),
                "answer":   answer.get("value") or answer.get("values"),
            })
        else:
            visible_unanswered.append({
                "itemOid":  item_oid,
                "question": item.get("question"),
                "type":     item.get("type"),
                "required": item.get("required", False),
            })

    # Next required item = first visible unanswered that is required
    next_required = next(
        (i for i in visible_unanswered if i.get("required")),
        visible_unanswered[0] if visible_unanswered else None,
    )

    # question_template_version is an int in silver.answers; the Gold contract +
    # Cosmos validator expect a string|null. Cast (None stays None).
    qtv = row.get("question_template_version")
    qtv_str = str(qtv) if qtv is not None else None

    watermarks = {
        "project":           _to_ts(row.get("project_silver_ts")),
        "answers":           _to_ts(row.get("answers_silver_ts")),
        "question_template": _to_ts(row.get("qt_silver_ts")),
    }
    valid_wm = [ts for ts in watermarks.values() if ts is not None]
    lag_ms = int((gold_ts - min(valid_wm)).total_seconds() * 1000) if valid_wm else 0

    doc = {
        "tenantId":                row["tenantid"],
        "projectId":               str(row["project_id"]),
        "_grain":                  "eden_bid_context",
        "_schemaVersion":          1,
        "_silverLsn":              int(row.get("silver_lsn") or 0),
        "isDeleted":                bool(row.get("is_deleted") or False),
        "deletedAt":                parse_ts(row.get("deleted_at")),
        "dataAsOf":                gold_ts,
        "template_id":             qtv_str,
        "questionTemplateVersion": qtv_str,
        "status":                  row.get("status"),
        "totalQuestions":          total_questions,
        "answeredCount":           len(answered_items),
        "answeredItems":           answered_items,
        "visibleUnanswered":       visible_unanswered,
        "nextRequiredItem":        next_required,
        "_silverWatermarks":       watermarks,
        "lafieldcraft":                   lag_ms,
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
                             retryWrites=False, appname="eden-bid-context")
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
