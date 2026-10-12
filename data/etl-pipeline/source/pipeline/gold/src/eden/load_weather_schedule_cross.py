"""Silver → Gold loader: weather_schedule_cross (Eden Schedule agent).

Crosses silver.site_weather (forecast) against silver.task (schedule) to
pre-identify which scheduled tasks will be weather-blocked. One document per
(tenantId, projectId, taskId, scheduledDate) — grain ``eden_weather_schedule_cross``.

Source: silver.task -> silver.project (project_uid/name) + silver.site_weather
(weather_data JSON, keyed by project_id + day). A task contributes a document
for each forecast day that matches its ``start_time::date``.

Block thresholds are pipeline-configured (no hardcoded values in shape logic):
  WEATHER_PRECIP_THRESHOLD_MM   default 10.0
  WEATHER_WIND_THRESHOLD_KMH    default 40.0

Upsert key: (tenantId, projectId, taskId, scheduledDate)
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
from gold.pipeline.lineage import lineage_fields, parse_ts, set_on_insert  # noqa: E402

COLLECTION = "weather_schedule_cross"
FEATURE_NAME = "eden"
_STEP_NAME = "load_weather_schedule_cross"
_contract_validator = ContractValidator("eden_weather_schedule_cross")
TRANSIENT_ERRORS = (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError)
log = logging.getLogger("eden.weather_schedule_cross_loader")

# Block thresholds — pipeline-configurable, never hardcoded in shape logic.
PRECIP_THRESHOLD_MM = float(os.environ.get("WEATHER_PRECIP_THRESHOLD_MM", "10.0"))
WIND_THRESHOLD_KMH = float(os.environ.get("WEATHER_WIND_THRESHOLD_KMH", "40.0"))

# Best-effort key candidates for the Azure Maps weather_data JSON blob. The exact
# payload shape is unconfirmed (no live data at build time), so we probe a small
# set of common key names and coerce defensively.
_CONDITION_KEYS = ("condition", "conditions", "summary", "phrase", "description", "weather")
_PRECIP_KEYS = ("precipitationMm", "precipitation_mm", "precipitation", "precip", "precip_mm",
                "totalPrecipitation", "rain", "rainfall")
_WIND_KEYS = ("windSpeedKmh", "wind_speed_kmh", "windSpeed", "wind_speed", "wind", "windKmh")


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
        p = argparse.ArgumentParser(description="Silver → Gold loader: weather_schedule_cross")
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


# One row per scheduled task. The forecast for the task's start_time date is
# attached when available (LEFT JOIN) — a task with no forecast row still emits a
# document (weather null → blocked=false) rather than silently disappearing.
_SILVER_QUERY = """
SELECT
    t._s_tenantid                       AS tenantid,
    t._s_env                            AS source_env,
    t.project_id                        AS project_id,
    p.customer                          AS project_name,
    t.id                                AS task_id,
    t.task_uid                          AS task_uid,
    t.task_name                         AS task_name,
    t.start_time::date                  AS scheduled_date,
    sw.weather_data                     AS weather_data,
    t._s_is_deleted                     AS is_deleted,
    t._s_deleted_at                      AS deleted_at,
    GREATEST(t._s_lsn, COALESCE(sw._s_lsn, 0)) AS silver_lsn,
    t._s_silver_ts                      AS task_silver_ts,
    sw._s_silver_ts                     AS weather_silver_ts,
    p._s_silver_ts                      AS project_silver_ts

FROM silver.task t
JOIN silver.project p
       ON p.id          = t.project_id
      AND p._s_tenantid = t._s_tenantid
      AND p._s_env      = t._s_env
      AND p._s_is_deleted = FALSE
LEFT JOIN silver.site_weather sw
       ON sw.project_id   = t.project_id
      AND sw._s_tenantid  = t._s_tenantid
      AND sw._s_env       = t._s_env
      AND sw._s_is_deleted = FALSE
      AND sw.day::date     = t.start_time::date

WHERE t._s_tenantid   = %(tenantid)s
  AND t.invalid        = FALSE
  AND t._s_is_deleted  = FALSE
  AND t.start_time IS NOT NULL
ORDER BY t.project_id, t.id, sw.day
"""


def _scoped_query(project_id: str | None) -> str:
    if project_id is None:
        return _SILVER_QUERY
    return _SILVER_QUERY.replace(
        "WHERE t._s_tenantid   = %(tenantid)s",
        "WHERE t._s_tenantid   = %(tenantid)s\n  AND t.project_id = %(project_id)s",
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
    """Build shaped Gold documents for a tenant (or a single project).

    Reused by the real-time Eden streaming job (project_id set).
    """
    return [
        shape_doc(row, gold_ts)
        for row in iter_silver_rows(pg_dsn, tenantid, max_docs, project_id)
    ]


_NEWNESS_SQL = """
SELECT EXISTS (
    SELECT 1 FROM silver.task         WHERE _s_tenantid = %(t)s AND _s_silver_ts > %(s)s
    UNION ALL
    SELECT 1 FROM silver.site_weather WHERE _s_tenantid = %(t)s AND _s_silver_ts > %(s)s
)
"""

# MIN of each _s_env's own MAX (across BOTH tables combined for that env), not
# the overall MAX — see the canonical loaders' get_max_silver_ts_per_env for
# why: a shared cursor advanced to the fastest-moving env's watermark would
# mask a later update in a slower-moving env still below that watermark.
_MAX_TS_SQL = """
SELECT MIN(env_max) FROM (
    SELECT _s_env, MAX(ts) AS env_max FROM (
        SELECT _s_env, _s_silver_ts AS ts FROM silver.task         WHERE _s_tenantid = %(t)s
        UNION ALL
        SELECT _s_env, _s_silver_ts AS ts FROM silver.site_weather WHERE _s_tenantid = %(t)s
    ) rows
    GROUP BY _s_env
) per_env
"""


def _has_new_silver_rows(pg_dsn: str, tenantid: str, since: datetime) -> bool:
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(_NEWNESS_SQL, {"t": tenantid, "s": since})
        return bool(cur.fetchone()[0])


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


def _first_key(d: dict, keys: tuple[str, ...]):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _parse_weather(raw) -> dict:
    """Parse the weather_data TEXT blob into condition/precip/wind (best-effort).

    The Azure Maps payload shape is unconfirmed at build time; probe a small set
    of common key names. Returns a dict with keys condition/precip/wind (any may
    be None).
    """
    if not raw:
        return {"condition": None, "precip": None, "wind": None}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return {"condition": None, "precip": None, "wind": None}
    # Some providers wrap the forecast in a list or a "forecast"/"current" node.
    if isinstance(data, list):
        data = data[0] if data and isinstance(data[0], dict) else {}
    if isinstance(data, dict):
        for nest in ("current", "forecast", "today", "daily"):
            inner = data.get(nest)
            if isinstance(inner, list) and inner and isinstance(inner[0], dict):
                data = inner[0]
                break
            if isinstance(inner, dict):
                data = inner
                break
    if not isinstance(data, dict):
        return {"condition": None, "precip": None, "wind": None}
    cond = _first_key(data, _CONDITION_KEYS)
    return {
        "condition": str(cond) if cond is not None else None,
        "precip": _float(_first_key(data, _PRECIP_KEYS)),
        "wind": _float(_first_key(data, _WIND_KEYS)),
    }


def shape_doc(row: dict, gold_ts: datetime) -> dict:
    watermarks = {
        "project":      _to_ts(row.get("project_silver_ts")),
        "task":         _to_ts(row.get("task_silver_ts")),
        "site_weather": _to_ts(row.get("weather_silver_ts")),
    }
    valid_wm = [ts for ts in watermarks.values() if ts is not None]
    lag_ms = int((gold_ts - min(valid_wm)).total_seconds() * 1000) if valid_wm else 0

    w = _parse_weather(row.get("weather_data"))
    precip, wind = w["precip"], w["wind"]
    reasons = []
    if precip is not None and precip > PRECIP_THRESHOLD_MM:
        reasons.append("Heavy rain forecast")
    if wind is not None and wind > WIND_THRESHOLD_KMH:
        reasons.append("High winds")
    blocked = bool(reasons)

    doc = {
        "tenantId":         row["tenantid"],
        "projectId":        str(row["project_id"]),
        "taskId":           str(row["task_id"]),
        "taskUid":          row.get("task_uid"),
        "taskName":         row.get("task_name"),
        "scheduledDate":    parse_ts(row["scheduled_date"]),
        "_grain":           "eden_weather_schedule_cross",
        "_schemaVersion":   1,
        "_silverLsn":       int(row.get("silver_lsn") or 0),
        "isDeleted":        bool(row.get("is_deleted") or False),
        "deletedAt":        parse_ts(row.get("deleted_at")),
        "dataAsOf":         gold_ts,
        "weatherCondition": w["condition"],
        "precipitationMm":  precip,
        "windSpeedKmh":     wind,
        "blocked":          blocked,
        "blockReason":      " / ".join(reasons) if reasons else None,
        "_silverWatermarks": watermarks,
        "lafieldcraft":            lag_ms,
    }
    doc.update(lineage_fields(FEATURE_NAME, gold_ts, iso=False,
                              source_updated_at=watermarks.get("task"),
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
         "taskId": d["taskId"], "scheduledDate": d["scheduledDate"],
         "sourceEnv": d.get("sourceEnv"),
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
                             retryWrites=False, appname="eden-weather-schedule-cross")
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
