"""Silver → Gold loader: question (Canonical Gold).

Reads silver.fieldcraft_provider_question_template — GLOBAL, provider-owned Q&A
template reference data (verified live 2026-07-16: 449 rows, all under
_s_tenantid='fieldcraft_provider'). NOT the bare silver.question_template table,
which is empty — question_template is sourced from the fieldcraft_provider connector
and, per universal_silver_merge.py::_namespaced_entity(), gets namespaced
under SILVER_NAMESPACED_SOURCE_DBS with no exemption (unlike `tenant`), so
live CDC only ever populates silver.fieldcraft_provider_question_template.

One document per template version (grain = the source row's own `id`), joined to:
  - silver.fieldcraft_provider_trade (trade_oid → trade id/name)
  - silver.fieldcraft_provider_tenant_trade_map (trade id → subscribed tenant_ids)

`tenantIds` on each document lists tenants with an EXPLICIT active row in
fieldcraft_provider_tenant_trade_map for the template's trade. This is a subscription
list, not a usage list: verified live 2026-07-16 that a tenant can answer a
template whose trade has no tenant_trade_map row at all (e.g. trade P.52 /
tenant tn59395620), so a template with an empty tenantIds array does not mean
"no tenant uses this" — only "no tenant has an explicit mapping". Consumers
needing actual usage should query gold_canonical.answer.templateId instead.

This is global reference data, not per-tenant, so unlike the other canonical
loaders (tenant/customer/project/asset) this loader ignores --tenant for its
Silver query and always reads the full current set of provider templates.
--tenant is still accepted (required by the shared CLI/step_registry
contract) but only used as a passthrough for logging; the cursor is tracked
under the constant pseudo-tenant key _CURSOR_KEY so every tenant iteration in
a pipeline run converges on one shared watermark instead of recomputing this
small, rarely-changing table once per real tenant.

Upsert key: (tenantId="fieldcraft_provider", sourceEnv, templateId)

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
from gold.src.monitoring.circuit_breaker import CircuitBreaker  # noqa: E402
from gold.src.monitoring.contract_validator import ContractValidator  # noqa: E402
from gold.pipeline.cursor_store import (  # noqa: E402
    read_cursor, write_cursor, mark_error, get_max_silver_ts_per_env, EPOCH,
)
from gold.pipeline.lineage import lineage_fields, parse_ts, set_on_insert  # noqa: E402
from gold.src.canonical.odm_parser import parse_template  # noqa: E402

COLLECTION = "question"
FEATURE_NAME = "canonical"
_STEP_NAME = "load_question"
_contract_validator = ContractValidator("canonical_question")
TRANSIENT_ERRORS = (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError)
log = logging.getLogger("canonical.question_loader")

# question_template is GLOBAL reference data owned by the provider tenant.
# Override via env if the provider tenant id differs (matches
# load_bid_context.py's QUESTION_TEMPLATE_TENANT convention).
_TEMPLATE_TENANT = os.environ.get("QUESTION_TEMPLATE_TENANT", "fieldcraft_provider")

# Constant cursor key so every real-tenant invocation of this loader (the
# per-tenant pipeline loop still calls it once per tenant) shares one
# watermark instead of each tenant re-scanning the same global table.
_CURSOR_KEY = _TEMPLATE_TENANT


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
        p = argparse.ArgumentParser(description="Silver → Gold loader: question")
        p.add_argument("--mongo-uri",     default=None)
        p.add_argument("--pg-dsn",        default=None)
        p.add_argument("--db",            dest="db_name",
                       default=os.environ.get("GOLD_CANONICAL_DB", "gold_canonical"))
        # Accepted for CLI/step_registry compatibility only — this loader's
        # Silver query is global (see module docstring) and ignores this value.
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


# One row per template version. trade lookup is a LEFT JOIN scoped to the same
# _s_env as the template row (a trade_oid is only unique WITHIN one env).
_SILVER_QUERY = """
SELECT
    qt.id                     AS template_id,
    qt._s_tenantid            AS tenantid,
    qt._s_env                 AS source_env,
    qt._s_is_deleted          AS is_deleted,
    qt._s_deleted_at          AS deleted_at,
    qt._s_lsn                 AS silver_lsn,
    qt._s_silver_ts           AS qt_silver_ts,
    qt.name,
    qt.trade_oid,
    qt.version,
    qt.status,
    qt.type,
    qt.invalid,
    qt.created_by,
    qt.created_date           AS created_date,
    qt.updated_date           AS updated_date,
    qt.question_template_data,
    t.id                      AS trade_id,
    t.name                    AS trade_name
FROM silver.fieldcraft_provider_question_template qt
LEFT JOIN silver.fieldcraft_provider_trade t
  ON t.trade_oid = qt.trade_oid AND t._s_env = qt._s_env
WHERE qt._s_tenantid = %(template_tenant)s
ORDER BY qt.id
"""

_TENANT_TRADE_MAP_QUERY = """
SELECT trade_id, tenant_id
FROM silver.fieldcraft_provider_tenant_trade_map
WHERE trade_id = ANY(%(trade_ids)s)
  AND status = 'active'
  AND _s_is_deleted = FALSE
  AND (%(env)s::text IS NULL OR _s_env = %(env)s)
"""


def iter_silver_rows(pg_dsn: str, max_docs: int = 0) -> Iterator[dict]:
    conn = psycopg2.connect(pg_dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(_SILVER_QUERY, {"template_tenant": _TEMPLATE_TENANT})
            rows = cur if max_docs <= 0 else (r for _, r in zip(range(max_docs), cur))
            for row in rows:
                yield dict(row)
    finally:
        conn.close()


def _fetch_tenant_ids_for_trades(
    pg_dsn: str, trade_ids: list[int], env: str | None = None
) -> dict[int, list[str]]:
    """Return active tenant_ids per trade_id from fieldcraft_provider_tenant_trade_map.

    See module docstring — this is a subscription list, not a usage list.
    """
    if not trade_ids:
        return {}
    by_trade: dict[int, list[str]] = {}
    conn = psycopg2.connect(pg_dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(_TENANT_TRADE_MAP_QUERY, {"trade_ids": trade_ids, "env": env})
            for row in cur:
                by_trade.setdefault(row["trade_id"], []).append(row["tenant_id"])
    finally:
        conn.close()
    return by_trade


def _has_new_silver_rows(pg_dsn: str, since: datetime) -> bool:
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM silver.fieldcraft_provider_question_template"
            "  WHERE _s_tenantid = %s AND _s_silver_ts > %s"
            ")",
            (_TEMPLATE_TENANT, since),
        )
        return bool(cur.fetchone()[0])


def _get_max_silver_ts(pg_dsn: str) -> datetime:
    return get_max_silver_ts_per_env(
        pg_dsn, "fieldcraft_provider_question_template", "_s_tenantid", _TEMPLATE_TENANT
    )


def shape_doc(row: dict, gold_ts: datetime, tenant_ids: list[str] | None = None) -> dict:
    items = parse_template(row.get("question_template_data"))
    watermarks = {"question_template": parse_ts(row.get("qt_silver_ts"))}
    valid_wm = [ts for ts in watermarks.values() if ts is not None]
    lag_ms = int((gold_ts - min(valid_wm)).total_seconds() * 1000) if valid_wm else 0

    doc = {
        "tenantId":       row["tenantid"],
        "templateId":     row["template_id"],
        "name":           row.get("name"),
        "tradeOid":       row.get("trade_oid"),
        "tradeId":        row.get("trade_id"),
        "tradeName":      row.get("trade_name"),
        "version":        row.get("version"),
        "status":         row.get("status"),
        "type":           row.get("type"),
        "invalid":        bool(row.get("invalid") or False),
        "items":          items,
        "itemCount":      len(items),
        # Subscription list, not usage list — see module docstring.
        "tenantIds":      sorted(tenant_ids or []),
        "createdBy":      row.get("created_by"),
        "createdDate":    parse_ts(row.get("created_date")),
        "updatedDate":    parse_ts(row.get("updated_date")),
        "_grain":         "canonical_question",
        "_schemaVersion": 1,
        "_silverLsn":     int(row.get("silver_lsn") or 0),
        "isDeleted":      bool(row.get("is_deleted") or False),
        "deletedAt":      parse_ts(row.get("deleted_at")),
        "dataAsOf":       gold_ts,
        "_silverWatermarks": watermarks,
        "lafieldcraft":          lag_ms,
    }
    doc.update(lineage_fields(FEATURE_NAME, gold_ts, iso=False,
                              source_updated_at=watermarks.get("question_template"),
                              source_env=row.get("source_env")))
    return doc


def build_documents(pg_dsn: str, gold_ts: datetime, max_docs: int = 0) -> list[dict]:
    """Build shaped Gold documents for the full current template set."""
    rows = list(iter_silver_rows(pg_dsn, max_docs))
    # Group by source_env before the tenant-trade-map lookup — a trade_id is
    # only unique within one _s_env, so resolving across all rows in one
    # ungrouped call (the previous bug) let tenant ids from one env bleed into
    # another env's document. Mirrors load_answer.py's pairs_by_env pattern.
    trade_ids_by_env: dict[str | None, set[int]] = {}
    for r in rows:
        if r.get("trade_id") is not None:
            trade_ids_by_env.setdefault(r.get("source_env"), set()).add(r["trade_id"])
    tenant_ids_by_key: dict[tuple[str | None, int], list[str]] = {}
    for env_value, trade_ids in trade_ids_by_env.items():
        resolved = _fetch_tenant_ids_for_trades(pg_dsn, sorted(trade_ids), env=env_value)
        for trade_id, tenant_ids in resolved.items():
            tenant_ids_by_key[(env_value, trade_id)] = tenant_ids
    return [
        shape_doc(row, gold_ts,
                  tenant_ids_by_key.get((row.get("source_env"), row.get("trade_id")), []))
        for row in rows
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


def run(cfg: LoaderConfig) -> int:
    log.info("loader start | db=%s.%s tenant=%s (ignored — global reference data) dry_run=%s mongo=%s",
             cfg.db_name, COLLECTION, cfg.tenantid, cfg.dry_run, _mask_uri(cfg.mongo_uri))
    last_ts = read_cursor(cfg.pg_dsn, _STEP_NAME, _CURSOR_KEY)
    if cfg.rebuild:
        log.info("rebuild mode | full re-aggregate from current Silver; cursor will NOT be advanced")
    elif not _has_new_silver_rows(cfg.pg_dsn, last_ts):
        log.info("no new Silver rows since %s — skipping", last_ts.isoformat())
        return 0
    gold_ts = datetime.now(timezone.utc)
    stats = WriteStats()
    client: MongoClient | None = None
    coll: Collection | None = None
    if not cfg.dry_run:
        client = MongoClient(cfg.mongo_uri, serverSelectionTimeoutMS=15_000,
                             connectTimeoutMS=15_000, socketTimeoutMS=60_000,
                             retryWrites=False, appname="canonical-question")
        coll = client[cfg.db_name][COLLECTION]

    pending: list[dict] = []
    pending_trade_ids_by_env: dict[str | None, set[int]] = {}
    seen = 0

    def flush() -> None:
        nonlocal pending_trade_ids_by_env
        if not pending:
            return
        # Grouped by source_env — see build_documents()'s comment for why an
        # ungrouped lookup here bled tenant ids across environments.
        tenant_ids_by_key: dict[tuple[str | None, int], list[str]] = {}
        for env_value, trade_ids in pending_trade_ids_by_env.items():
            resolved = _fetch_tenant_ids_for_trades(cfg.pg_dsn, sorted(trade_ids), env=env_value)
            for trade_id, tenant_ids in resolved.items():
                tenant_ids_by_key[(env_value, trade_id)] = tenant_ids
        docs = [
            shape_doc(row, gold_ts,
                      tenant_ids_by_key.get((row.get("source_env"), row.get("trade_id")), []))
            for row in pending
        ]
        for d in docs:
            _contract_validator.validate(d, COLLECTION)
        ops = [
            UpdateOne(
                {"tenantId": d["tenantId"], "sourceEnv": d.get("sourceEnv"),
                 "templateId": d["templateId"],
                 "$or": [{"_silverLsn": {"$exists": False}},
                         {"_silverLsn": {"$lte": d.get("_silverLsn", 0)}}]},
                {"$set": d, "$setOnInsert": set_on_insert(gold_ts, iso=False)},
                upsert=True,
            )
            for d in docs
        ]
        if ops and not cfg.dry_run and coll is not None:
            bulk_write_with_retry(coll, ops, cfg, stats)
        pending.clear()
        pending_trade_ids_by_env = {}

    try:
        for row in iter_silver_rows(cfg.pg_dsn, cfg.max_docs):
            if _shutdown:
                break
            seen += 1
            pending.append(row)
            if row.get("trade_id") is not None:
                pending_trade_ids_by_env.setdefault(row.get("source_env"), set()).add(row["trade_id"])
            if len(pending) >= cfg.batch_size:
                flush()
        flush()
    except TRANSIENT_ERRORS as exc:
        mark_error(cfg.pg_dsn, _STEP_NAME, _CURSOR_KEY, exc)
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
        mark_error(cfg.pg_dsn, _STEP_NAME, _CURSOR_KEY, f"{stats.failed} bulk write failures")
    else:
        max_ts = _get_max_silver_ts(cfg.pg_dsn)
        if max_ts > last_ts:
            write_cursor(cfg.pg_dsn, _STEP_NAME, _CURSOR_KEY, max_ts)

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
