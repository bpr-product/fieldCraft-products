"""Silver → Gold loader: communication_log (Canonical Gold).

Reads silver.communication_log — CRM communication metadata (email/SMS send
events), sourced from the communication_log table which lives in EACH
TENANT'S OWN database (not the shared Customer DB lead/customer live in),
immutable/append-only, monthly-partitioned — see
docs/crm/eden_crm_gold_requirements.md, G5.

Deliberately excludes all PII columns (body_snapshot, body_tokens,
to_address, from_address) — confirmed NULL on every row written today (no
application code path populates them) and gated on DPO sign-off regardless.
These are ALSO excluded at the Bronze CDC hop (see
fieldcraft-tenant-connector.template.json's column.exclude.list) so this loader's
own exclusion is redundant defense-in-depth, not the only gate.

PK column: communicationLogId is the communication_log.id value, typed as a
string. The source column may be a UUID or an auto-increment integer depending
on tenant/env; we stringify unconditionally (same pattern as load_lead's
customerId/projectId) so the contract's string type always holds regardless.

projectId: communication_log.project_id is a plain FK to silver.project.id
(confirmed 2026-09-25) — but the Gold field is deliberately the resolved
ORDER NUMBER (silver.project.order_no), not that raw FK value, per explicit
direction 2026-09-25. Resolved via a LEFT JOIN on silver.project (see
_SILVER_QUERY). Nullable: a communication can be logged before a project
exists (e.g. a pre-conversion lead's welcome email), matching customerUuid's
own nullability for the same reason.

communicationLogId is an optional scope filter (mirrors leadUid/assetUid on
load_lead.py/load_asset.py) so a real-time CDC event on
silver.communication_log can trigger a single-row recompute instead of
rescanning the whole tenant's (potentially large, monthly-partitioned) table.

Since rows are immutable/append-only (no UPDATE), _s_is_deleted is not
expected to ever be true here, but the same soft-delete filter is applied
for consistency with every other canonical loader — see this repo's
Silver-exclusion discipline in CLAUDE.md's Gotchas section.

Upsert key: (tenantId, communicationLogId, sourceEnv)

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

COLLECTION = "communication_log"
FEATURE_NAME = "canonical"
_STEP_NAME = "load_communication_log"
_contract_validator = ContractValidator("canonical_communication_log")
TRANSIENT_ERRORS = (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError)
log = logging.getLogger("canonical.communication_log_loader")


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
    communication_log_id: str | None = None

    @staticmethod
    def from_args_and_env(argv: list[str] | None = None) -> "LoaderConfig":
        p = argparse.ArgumentParser(description="Silver → Gold loader: communication_log")
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
        p.add_argument("--communication-log-id", default=os.environ.get("COMMUNICATION_LOG_ID") or None,
                       help="Scope to a single communicationLogId — for a CDC-triggered "
                            "single-row recompute instead of rescanning the whole tenant's "
                            "(potentially large, monthly-partitioned) table.")
        ns = p.parse_args(argv)
        return LoaderConfig(
            mongo_uri=ns.mongo_uri or get_mongo_uri(),
            pg_dsn=ns.pg_dsn or get_postgres_dsn(),
            db_name=ns.db_name, tenantid=ns.tenant,
            batch_size=ns.batch_size, max_retries=ns.max_retries,
            retry_backoff_s=ns.retry_backoff, dry_run=ns.dry_run,
            max_docs=ns.max_docs, rebuild=ns.rebuild,
            communication_log_id=ns.communication_log_id,
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


# One row per (tenant, communication_log_id) — silver.communication_log is
# already 1:1 on its own id (append-only, no dedup needed).
#
# communication_log_id is an optional scope filter (mirrors lead_uid on
# load_lead.py): when provided, only that one row is read, so a CDC event on
# silver.communication_log (which carries id directly) can trigger a
# single-row recompute instead of rescanning the whole tenant's table.
#
# body_snapshot/body_tokens/to_address/from_address are intentionally NOT
# selected — see module docstring.
#
# projectId is deliberately the PROJECT'S ORDER NUMBER (silver.project.
# order_no), not communication_log.project_id's raw value (an internal FK to
# silver.project.id) — per explicit direction 2026-09-25. The LEFT JOIN
# resolves that FK to the order number; LEFT (not INNER) so a communication
# logged before a project exists (project_id NULL) still returns a row
# instead of being silently dropped from the whole collection. The join
# matches _s_env too: a project id is only unique within one source env, so
# without it one log row fans out into one row per env sharing that id.
#
# No CDC trigger on silver.project is needed for this join: order_no is
# assigned once at project creation and treated as a stable identity key
# elsewhere in this pipeline (see lineage.cleanup_stale_order_no_siblings),
# not a value that changes independently later — so a communication_log row's
# own CDC event (which this collection already triggers on) is sufficient to
# keep projectId correct without also wiring silver.project as a trigger
# table in gold_user_permissions_stream.py.
_SILVER_QUERY = """
SELECT
    c._s_tenantid                AS tenantid,
    c._s_env                     AS source_env,
    c.id                          AS communication_log_id,
    c.channel                     AS channel,
    c.direction                   AS direction,
    c.status                      AS status,
    c.subject                     AS subject,
    c.occurred_at                 AS occurred_at,
    c.actor_type                  AS actor_type,
    c.actor_ref                   AS actor_ref,
    c.customer_uuid                AS customer_uuid,
    p.order_no                     AS project_id,
    c.provider_message_ref         AS provider_message_ref,
    c._s_is_deleted                AS is_deleted,
    c._s_lsn                       AS silver_lsn,
    c._s_silver_ts                 AS communication_log_silver_ts
FROM silver.communication_log c
LEFT JOIN silver.project p
    ON p.id = c.project_id
   AND p._s_tenantid = c._s_tenantid
   AND p._s_env = c._s_env
WHERE c._s_tenantid = %(tenantid)s
  AND c.id IS NOT NULL{row_filter}
ORDER BY c.id
"""


def iter_silver_rows(
    pg_dsn: str, tenantid: str, max_docs: int = 0, communication_log_id: str | None = None,
) -> Iterator[dict]:
    conn = psycopg2.connect(pg_dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            params = {"tenantid": tenantid}
            row_filter = ""
            if communication_log_id is not None:
                row_filter = "\n  AND c.id = %(communication_log_id)s"
                params["communication_log_id"] = communication_log_id
            cur.execute(_SILVER_QUERY.format(row_filter=row_filter), params)
            rows = cur if max_docs <= 0 else (r for _, r in zip(range(max_docs), cur))
            for row in rows:
                yield dict(row)
    finally:
        conn.close()


def build_documents(
    pg_dsn: str, tenantid: str, gold_ts: datetime,
    communication_log_id: str | None = None, max_docs: int = 0,
) -> list[dict]:
    return [
        shape_doc(row, gold_ts)
        for row in iter_silver_rows(pg_dsn, tenantid, max_docs, communication_log_id)
    ]


def _has_new_silver_rows(pg_dsn: str, tenantid: str, since: datetime) -> bool:
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM silver.communication_log WHERE _s_tenantid = %s AND _s_silver_ts > %s"
            ")",
            (tenantid, since),
        )
        return bool(cur.fetchone()[0])


def _get_max_silver_ts(pg_dsn: str, tenantid: str) -> datetime:
    return get_max_silver_ts_per_env(pg_dsn, "communication_log", "_s_tenantid", tenantid)


def shape_doc(row: dict, gold_ts: datetime) -> dict:
    watermarks = {"communication_log": parse_ts(row.get("communication_log_silver_ts"))}
    valid_wm = [ts for ts in watermarks.values() if ts is not None]
    lag_ms = max(0, int((gold_ts - min(valid_wm)).total_seconds() * 1000)) if valid_wm else 0

    doc = {
        "tenantId":            row["tenantid"],
        "communicationLogId":  str(row["communication_log_id"]) if row.get("communication_log_id") is not None else None,
        "channel":             row.get("channel"),
        "direction":           row.get("direction"),
        "status":              row.get("status"),
        "subject":             row.get("subject"),
        "occurredAt":          parse_ts(row.get("occurred_at")),
        "actorType":           row.get("actor_type"),
        "actorRef":            row.get("actor_ref"),
        "customerUuid":        row.get("customer_uuid"),
        "projectId":           row.get("project_id"),
        "providerMessageRef":  row.get("provider_message_ref"),
        "_grain":            "canonical_communication_log",
        "_schemaVersion":    1,
        "_silverLsn":        int(row.get("silver_lsn") or 0),
        "isDeleted":         bool(row.get("is_deleted") or False),
        "dataAsOf":          gold_ts,
        "_silverWatermarks": watermarks,
        "lafieldcraft":             lag_ms,
    }
    doc.update(lineage_fields(FEATURE_NAME, gold_ts, iso=False,
                              source_updated_at=watermarks.get("communication_log"),
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
                             retryWrites=False, appname="canonical-communication-log")
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
                {"tenantId": d["tenantId"], "communicationLogId": d["communicationLogId"],
                 "sourceEnv": d.get("sourceEnv"),
                 "$or": [{"_silverLsn": {"$exists": False}},
                         {"_silverLsn": {"$lte": d.get("_silverLsn", 0)}}]},
                {"$set": d, "$setOnInsert": set_on_insert(gold_ts, iso=False)},
                upsert=True,
            )
            for d in pending
        ]
        if ops and not cfg.dry_run and coll is not None:
            bulk_write_with_retry(coll, ops, cfg, stats)
        pending.clear()

    try:
        for row in iter_silver_rows(cfg.pg_dsn, cfg.tenantid, cfg.max_docs, cfg.communication_log_id):
            if _shutdown:
                break
            seen += 1
            pending.append(shape_doc(row, gold_ts))
            if len(pending) >= cfg.batch_size:
                flush()
        flush()
    except (TRANSIENT_ERRORS + (OperationFailure, psycopg2.Error)) as exc:
        log.error("aborting: %s", exc)
        try:
            mark_error(cfg.pg_dsn, _STEP_NAME, cfg.tenantid, exc)
        except psycopg2.Error as mark_exc:
            log.error("could not record failure — Postgres itself unreachable: %s", mark_exc)
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
