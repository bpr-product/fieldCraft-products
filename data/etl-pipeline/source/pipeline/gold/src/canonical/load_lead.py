"""Silver → Gold loader: lead (Canonical Gold).

Reads silver.fieldcraft_customer_lead — the pre-conversion CRM lead record, sourced
from the `lead` table in the shared, cross-tenant Customer DB (see
docs/crm/eden_crm_gold_requirements.md, G2) — into one document per
(tenantId, leadUid). The table is namespaced fieldcraft_customer_ in Silver
(universal_silver_merge._namespaced_entity: every Customer DB table is), and
its _s_tenantid is the literal "fieldcraft_customer" for every row, so tenant
scoping uses the row's own business tenant_id column instead. Unlike
asset/customer it is already 1:1 on lead_uid, so this loader is a lean
passthrough, closer to load_tenant.py's shape than load_asset.py's GROUP BY.

customerId/projectId are numeric FKs in the source; they are stringified so
they match gold_canonical.project.projectId and the contract's string type.

Explicitly NOT synced (per G2): the raw `data` JSON payload (no defined
schema) and automation-engine bookkeeping columns (ase_job_id, retry_count,
last_error, locked_by, locked_at, next_attempt_at) — internal, never
user-facing.

leadUid is an optional scope filter (mirrors asset_uid/customer_uid on
load_asset.py/load_customer.py) so a real-time CDC event on
silver.fieldcraft_customer_lead (which carries lead_uid directly) can trigger a
single-lead recompute instead of rescanning every lead row for the tenant.

Upsert key: (tenantId, leadUid, sourceEnv)

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

COLLECTION = "lead"
FEATURE_NAME = "canonical"
_STEP_NAME = "load_lead"
_contract_validator = ContractValidator("canonical_lead")
TRANSIENT_ERRORS = (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError)
log = logging.getLogger("canonical.lead_loader")


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
    lead_uid: str | None = None

    @staticmethod
    def from_args_and_env(argv: list[str] | None = None) -> "LoaderConfig":
        p = argparse.ArgumentParser(description="Silver → Gold loader: lead")
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
        p.add_argument("--lead-uid",      default=os.environ.get("LEAD_UID") or None,
                       help="Scope to a single lead_uid — for a CDC-triggered single-lead "
                            "recompute instead of rescanning every lead row for the tenant.")
        ns = p.parse_args(argv)
        return LoaderConfig(
            mongo_uri=ns.mongo_uri or get_mongo_uri(),
            pg_dsn=ns.pg_dsn or get_postgres_dsn(),
            db_name=ns.db_name, tenantid=ns.tenant,
            batch_size=ns.batch_size, max_retries=ns.max_retries,
            retry_backoff_s=ns.retry_backoff, dry_run=ns.dry_run,
            max_docs=ns.max_docs, rebuild=ns.rebuild, lead_uid=ns.lead_uid,
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


# One row per (tenant, lead_uid) — the table is already 1:1 on lead_uid, no
# GROUP BY/dedup needed (unlike customer_asset). Tenant scope is the business
# tenant_id column: _s_tenantid is "fieldcraft_customer" for every row of this
# shared Customer DB table (see module docstring).
#
# lead_uid is an optional scope filter (mirrors asset_uid on load_asset.py):
# when provided, only that one lead's row is read, so a CDC event (which
# carries lead_uid directly) can trigger a single-lead recompute instead of
# rescanning every lead row for the tenant.
#
# The service-master join matches _s_env as well as id: Silver holds several
# source environments side by side and an id is only unique within one, so
# an id-only join would fan one lead out into one row per env that shares
# that id (same rule load_question.py applies to its trade join).
#
# serviceName resolves lead.service_id (the raw FK, used only in the JOIN
# condition below — not exposed as its own Gold field, and no separate Gold
# service_lookup collection exists) to a display name via LEFT JOIN on
# silver.external_service_service_master
# (docs/crm/eden_crm_gold_requirements.md G3a) — per explicit direction
# 2026-09-25: serviceName alone is sufficient, the raw FK is not surfaced in
# Gold. LEFT (not INNER) so a lead whose service_id doesn't resolve
# (deleted/renamed service, or the source table not yet populated) still
# returns a row instead of being silently dropped.
#
# No CDC trigger on the service_master table is needed for this join: the
# same reasoning as communication_log.projectId in load_communication_log.py
# — service_master is small, rarely-changing reference data, and a lead's own
# CDC event (which this collection already triggers on) is what drives most
# actual changes to serviceName visibility in practice. If service_master
# renames become a real freshness concern, re-run with --rebuild or wire
# silver.external_service_service_master as an additional
# trigger table in gold_user_permissions_stream.py.
_SILVER_QUERY = """
SELECT
    l.tenant_id                AS tenantid,
    l._s_env                   AS source_env,
    l.lead_uid                 AS lead_uid,
    sm.name                     AS service_name,
    l.external_deal_id          AS external_deal_id,
    l.external_contact_id       AS external_contact_id,
    l.first_name                AS first_name,
    l.last_name                 AS last_name,
    l.email                     AS email,
    l.phone                     AS phone,
    l.status                    AS status,
    l.sales_stage               AS sales_stage,
    l.sales_stage_entered_at    AS sales_stage_entered_at,
    l.customer_id                AS customer_id,
    l.converted_at               AS converted_at,
    l.project_uid                AS project_uid,
    l.project_id                 AS project_id,
    l.created_at                 AS created_at,
    l._s_is_deleted              AS is_deleted,
    l._s_lsn                     AS silver_lsn,
    l._s_silver_ts               AS lead_silver_ts
FROM silver.fieldcraft_customer_lead l
LEFT JOIN silver.external_service_service_master sm
    ON sm.id = l.service_id
   AND sm._s_env = l._s_env
WHERE l.tenant_id = %(tenantid)s
  AND l.lead_uid IS NOT NULL{lead_filter}
ORDER BY l.lead_uid
"""


def iter_silver_rows(
    pg_dsn: str, tenantid: str, max_docs: int = 0, lead_uid: str | None = None,
) -> Iterator[dict]:
    conn = psycopg2.connect(pg_dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            params = {"tenantid": tenantid}
            lead_filter = ""
            if lead_uid is not None:
                lead_filter = "\n  AND l.lead_uid = %(lead_uid)s"
                params["lead_uid"] = lead_uid
            cur.execute(_SILVER_QUERY.format(lead_filter=lead_filter), params)
            rows = cur if max_docs <= 0 else (r for _, r in zip(range(max_docs), cur))
            for row in rows:
                yield dict(row)
    finally:
        conn.close()


def build_documents(
    pg_dsn: str, tenantid: str, gold_ts: datetime,
    lead_uid: str | None = None, max_docs: int = 0,
) -> list[dict]:
    return [
        shape_doc(row, gold_ts)
        for row in iter_silver_rows(pg_dsn, tenantid, max_docs, lead_uid)
    ]


def _has_new_silver_rows(pg_dsn: str, tenantid: str, since: datetime) -> bool:
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM silver.fieldcraft_customer_lead WHERE tenant_id = %s AND _s_silver_ts > %s"
            ")",
            (tenantid, since),
        )
        return bool(cur.fetchone()[0])


def _get_max_silver_ts(pg_dsn: str, tenantid: str) -> datetime:
    return get_max_silver_ts_per_env(pg_dsn, "fieldcraft_customer_lead", "tenant_id", tenantid)


def shape_doc(row: dict, gold_ts: datetime) -> dict:
    watermarks = {"lead": parse_ts(row.get("lead_silver_ts"))}
    valid_wm = [ts for ts in watermarks.values() if ts is not None]
    lag_ms = max(0, int((gold_ts - min(valid_wm)).total_seconds() * 1000)) if valid_wm else 0

    doc = {
        "tenantId":            row["tenantid"],
        "leadUid":             row["lead_uid"],
        "serviceName":         row.get("service_name"),
        "externalDealId":      row.get("external_deal_id"),
        "externalContactId":   row.get("external_contact_id"),
        "firstName":           row.get("first_name"),
        "lastName":            row.get("last_name"),
        "email":               row.get("email"),
        "phone":               row.get("phone"),
        "status":              row.get("status"),
        "salesStage":          row.get("sales_stage"),
        "salesStageEnteredAt": parse_ts(row.get("sales_stage_entered_at")),
        "customerId":          None if row.get("customer_id") is None else str(row["customer_id"]),
        "convertedAt":         parse_ts(row.get("converted_at")),
        "projectUid":          row.get("project_uid"),
        "projectId":           None if row.get("project_id") is None else str(row["project_id"]),
        "createdAt":           parse_ts(row.get("created_at")),
        "_grain":            "canonical_lead",
        "_schemaVersion":    1,
        "_silverLsn":        int(row.get("silver_lsn") or 0),
        "isDeleted":         bool(row.get("is_deleted") or False),
        "dataAsOf":          gold_ts,
        "_silverWatermarks": watermarks,
        "lafieldcraft":             lag_ms,
    }
    doc.update(lineage_fields(FEATURE_NAME, gold_ts, iso=False,
                              source_updated_at=watermarks.get("lead"),
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
                             retryWrites=False, appname="canonical-lead")
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
                {"tenantId": d["tenantId"], "leadUid": d["leadUid"], "sourceEnv": d.get("sourceEnv"),
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
        for row in iter_silver_rows(cfg.pg_dsn, cfg.tenantid, cfg.max_docs, cfg.lead_uid):
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
