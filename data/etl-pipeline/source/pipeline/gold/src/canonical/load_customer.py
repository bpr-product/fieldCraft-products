"""Silver → Gold loader: customer (Canonical Gold).

Reads silver.customer_asset — a real, per-project row carrying customer
identity fields denormalized inline (customer_uid, customer_name, email,
mobile_no, account_no) — deduped by customer_uid into one document per
customer. LEFT JOINs silver.fieldcraft_customer_customer (the proper normalized
customer master table, keyed by customer_uuid) for richer fields
(firstname/lastname/gender/birthdate/status) WHEN populated.

Data gap (verified live, 2026-07-11): silver.fieldcraft_customer_customer currently
has 0 rows in this environment — the real customer master table exists in the
schema but has never been fed. Until it is, every "customer" document here is
reconstructed purely from the per-project customer_asset snapshot; richer
fields from fieldcraft_customer_customer will simply be null. The join is written so
those fields populate automatically, with no code change, once that table is
ingested.

One customer can legitimately have many assets and many projects (verified
live: up to 46 distinct assets and 834 distinct projects for one customer_uid
in this dataset) — projectIds/assetUids are arrays, not singular fields.

Upsert key: (tenantId, customerUid, sourceEnv)

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

COLLECTION = "customer"
FEATURE_NAME = "canonical"
_STEP_NAME = "load_customer"
_contract_validator = ContractValidator("canonical_customer")
TRANSIENT_ERRORS = (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError)
log = logging.getLogger("canonical.customer_loader")


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
        p = argparse.ArgumentParser(description="Silver → Gold loader: customer")
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


# One row per (tenant, customer_uid) — deduped across every customer_asset row
# (one customer can have many assets and many projects).
#
# customer_uid is an optional scope filter (added alongside real-time CDC
# streaming — see gold_user_permissions_stream.py's customer/asset routing):
# when provided, only that one customer's rows are aggregated, so a CDC event
# on customer_asset/fieldcraft_customer_customer (both of which carry customer_uid
# directly) can trigger a single-customer recompute instead of rescanning
# every customer_asset row for the tenant.
_SILVER_QUERY = """
SELECT
    ca._s_tenantid                                          AS tenantid,
    ca._s_env                                                AS source_env,
    ca.customer_uid                                          AS customer_uid,
    MAX(ca.customer_name)                                    AS customer_name,
    MAX(ca.email)                                            AS email,
    MAX(ca.mobile_no)                                        AS mobile_no,
    MAX(ca.account_no)                                       AS account_no,
    ARRAY_AGG(DISTINCT ca.project_id ORDER BY ca.project_id) AS project_ids,
    ARRAY_AGG(DISTINCT ca.asset_uid)                         AS asset_uids,
    BOOL_AND(ca._s_is_deleted)                                AS is_deleted,
    MAX(ca._s_lsn)                                            AS silver_lsn,
    MAX(ca._s_silver_ts)                                      AS customer_asset_silver_ts,

    -- Enrichment from the real customer master table (fieldcraft_customer_customer),
    -- when populated — see module docstring for the current-emptiness gap.
    -- Identical for every ca-row sharing this customer_uid, so (ARRAY_AGG)[1]
    -- just picks the one value (same technique as load_bom_stock_gap.py).
    (ARRAY_AGG(gc.firstname))[1]                              AS firstname,
    (ARRAY_AGG(gc.middlename))[1]                             AS middlename,
    (ARRAY_AGG(gc.lastname))[1]                               AS lastname,
    (ARRAY_AGG(gc.gender))[1]                                 AS gender,
    (ARRAY_AGG(gc.birthdate))[1]                              AS birthdate,
    (ARRAY_AGG(gc.status))[1]                                 AS fieldcraft_customer_status,
    (ARRAY_AGG(gc.account_no))[1]                             AS fieldcraft_customer_account_no,
    (ARRAY_AGG(gc.alternate_mobile))[1]                       AS alternate_mobile,
    (ARRAY_AGG(gc._s_silver_ts))[1]                           AS fieldcraft_customer_silver_ts

FROM silver.customer_asset ca
LEFT JOIN silver.fieldcraft_customer_customer gc
       ON gc.customer_uuid = ca.customer_uid
      AND gc._s_env         = ca._s_env
      AND gc._s_is_deleted  = FALSE
WHERE ca._s_tenantid  = %(tenantid)s
  AND ca._s_is_deleted = FALSE
  AND ca.customer_uid IS NOT NULL{customer_filter}
GROUP BY ca._s_tenantid, ca._s_env, ca.customer_uid
ORDER BY ca.customer_uid
"""


def iter_silver_rows(
    pg_dsn: str, tenantid: str, max_docs: int = 0, customer_uid: str | None = None,
) -> Iterator[dict]:
    conn = psycopg2.connect(pg_dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            params = {"tenantid": tenantid}
            customer_filter = ""
            if customer_uid is not None:
                customer_filter = "\n  AND ca.customer_uid = %(customer_uid)s"
                params["customer_uid"] = customer_uid
            cur.execute(_SILVER_QUERY.format(customer_filter=customer_filter), params)
            rows = cur if max_docs <= 0 else (r for _, r in zip(range(max_docs), cur))
            for row in rows:
                yield dict(row)
    finally:
        conn.close()


def build_documents(
    pg_dsn: str, tenantid: str, gold_ts: datetime,
    customer_uid: str | None = None, max_docs: int = 0,
) -> list[dict]:
    return [
        shape_doc(row, gold_ts)
        for row in iter_silver_rows(pg_dsn, tenantid, max_docs, customer_uid)
    ]


def _has_new_silver_rows(pg_dsn: str, tenantid: str, since: datetime) -> bool:
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM silver.customer_asset WHERE _s_tenantid = %s AND _s_silver_ts > %s"
            ")",
            (tenantid, since),
        )
        return bool(cur.fetchone()[0])


def _get_max_silver_ts(pg_dsn: str, tenantid: str) -> datetime:
    return get_max_silver_ts_per_env(pg_dsn, "customer_asset", "_s_tenantid", tenantid)


def shape_doc(row: dict, gold_ts: datetime) -> dict:
    watermarks = {
        "customer_asset":      parse_ts(row.get("customer_asset_silver_ts")),
        "fieldcraft_customer_customer": parse_ts(row.get("fieldcraft_customer_silver_ts")),
    }
    valid_wm = [ts for ts in watermarks.values() if ts is not None]
    lag_ms = int((gold_ts - min(valid_wm)).total_seconds() * 1000) if valid_wm else 0

    project_ids = [str(p) for p in (row.get("project_ids") or []) if p is not None]
    asset_uids = [a for a in (row.get("asset_uids") or []) if a is not None]

    # Prefer the real customer master's name split when present; fall back to
    # the denormalized customer_name string from customer_asset.
    display_name = None
    if row.get("firstname") or row.get("lastname"):
        parts = [row.get("firstname"), row.get("middlename"), row.get("lastname")]
        display_name = " ".join(p.strip() for p in parts if p and str(p).strip()) or None
    display_name = display_name or row.get("customer_name")

    doc = {
        "tenantId":          row["tenantid"],
        "customerUid":       row["customer_uid"],
        "name":              display_name,
        "firstName":         row.get("firstname"),
        "middleName":        row.get("middlename"),
        "lastName":          row.get("lastname"),
        "gender":            row.get("gender"),
        "birthDate":         parse_ts(row.get("birthdate")),
        "email":             row.get("email"),
        "mobileNo":          row.get("mobile_no"),
        "alternateMobile":   row.get("alternate_mobile"),
        "accountNo":         row.get("account_no") or row.get("fieldcraft_customer_account_no"),
        "status":            row.get("fieldcraft_customer_status"),
        "projectIds":        project_ids,
        "assetUids":         asset_uids,
        "hasCustomerMasterRecord": bool(row.get("fieldcraft_customer_silver_ts") is not None),
        "_grain":            "canonical_customer",
        "_schemaVersion":    1,
        "_silverLsn":        int(row.get("silver_lsn") or 0),
        "isDeleted":         bool(row.get("is_deleted") or False),
        "dataAsOf":          gold_ts,
        "_silverWatermarks": watermarks,
        "lafieldcraft":             lag_ms,
    }
    doc.update(lineage_fields(FEATURE_NAME, gold_ts, iso=False,
                              source_updated_at=watermarks.get("customer_asset"),
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
                             retryWrites=False, appname="canonical-customer")
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
                {"tenantId": d["tenantId"], "customerUid": d["customerUid"], "sourceEnv": d.get("sourceEnv"),
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
