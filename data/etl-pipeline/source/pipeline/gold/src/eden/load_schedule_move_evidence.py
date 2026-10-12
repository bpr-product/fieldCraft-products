"""Silver → Gold loader: schedule_move_evidence (Eden Schedule agent — Q2).

Answers "why did this project's tentative start/end date move" with the best
available corroborating signals — never a proven cause, since no FK in this
schema encodes scheduling causation (e.g. a resource finishing early on
another project affecting this one). One document per (tenantId, sourceEnv,
projectId, version) of ``silver.project_schedule`` — grain
``eden_schedule_move_evidence``.

SOURCE MODEL (see EDEN_CustomerSupport_Reasoning_Constraints.md, Q2):
  * silver.project_schedule.actual_startdate/actual_enddate are 0% populated
    fleet-wide (checked across every tenant) — NOT used. This loader instead
    diffs successive `version` rows' startdate/enddate/job_startdate/
    job_enddate (window LAG) to detect that a date actually moved.
  * silver.project_schedule.reason was checked and ruled out as a causal
    code — it only ever holds two generic values (SAVE_TASKS, Auto
    Rescheduling Error) fleet-wide. Passed through read-only, never used to
    decide anything.
  * silver.resource_schedule (status='completed', filtered — 'scheduled'/
    'BOOKED' are full of far-future sentinel dates) is the evidence that a
    crew/resource actually finished early. Correlated by `project_id` and a
    time window bounded by the previous/current schedule version's
    `_s_silver_ts` (padded 1 day for clock skew).
  * silver.audit_log.resource_id is a UUID that matches silver.project.
    project_uid, NOT project.id — the join has to go through silver.project.
    `data` (jsonb) — data->'context' and data->>'taskUid' — is the richest
    available payload, though its structure isn't a clean contract.
  * silver.rule_solve_snapshot is the scheduling engine's (GoPAL) own record
    of when it ran a solve/reschedule computation. `attributes_snapshot`
    (jsonb) is deliberately NOT parsed or embedded here — its internal
    structure is opaque and unmapped; only the concrete, populated columns
    (scheduling_type/rules_hash/rule_count/fired_count/convergence_passes/
    captured_at) are surfaced. Carries an explicit `tenant_id` business
    column despite living in a per-tenant database (cross-cutting rule).
  * crew_expansion_proposal (0 rows / table absent in 2 tenants),
    rule_task_trace (0 rows), stage_transition (0 rows), and task_version
    (project-level, not task-level despite the name) were checked and ruled
    out — not built on here.

No single field in this document proves causation — `evidenceSummary`
surfaces what corroborating signals exist so Eden can present a confidence
level ("a recompute happened around this time") rather than a guess.

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

COLLECTION = "schedule_move_evidence"
FEATURE_NAME = "eden"
_STEP_NAME = "load_schedule_move_evidence"
_contract_validator = ContractValidator("eden_schedule_move_evidence")
TRANSIENT_ERRORS = (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError)
log = logging.getLogger("eden.schedule_move_evidence_loader")

# When a schedule version has no predecessor, look this far back for
# corroborating resource_schedule/audit_log/rule_solve_snapshot evidence.
_NO_PREV_WINDOW_DAYS = 30
# Padding on both ends of the correlation window for clock skew between
# Silver tables (each has its own _s_silver_ts ingestion time).
_WINDOW_PAD_DAYS = 1

_CAUSATION_CAVEAT = (
    "No table in this schema carries a foreign key proving scheduling "
    "causation (e.g. a resource finishing early on a DIFFERENT project "
    "freeing it up for this one). The fields below are corroborating "
    "signals for 'did something real happen around this time', not proof."
)


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
        p = argparse.ArgumentParser(description="Silver → Gold loader: schedule_move_evidence")
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


# Grain: one row per (project_id, version) of silver.project_schedule. LAG(...)
# OVER w gives the previous version's dates so shape_doc() can detect a real
# move (not just "the row changed"). The three evidence LATERALs correlate
# against the window between the previous version's _s_silver_ts and this
# version's _s_silver_ts (or _NO_PREV_WINDOW_DAYS before, for the first
# version), padded by _WINDOW_PAD_DAYS on each side.
_SILVER_QUERY = """
WITH schedules AS (
    SELECT
        ps.id                        AS schedule_id,
        ps._s_tenantid               AS tenantid,
        ps._s_env                    AS source_env,
        ps.project_id                AS project_id,
        ps.version                   AS version,
        ps.startdate                 AS startdate,
        ps.enddate                   AS enddate,
        ps.job_startdate             AS job_startdate,
        ps.job_enddate               AS job_enddate,
        ps.reason                    AS reason,
        ps._s_lsn                    AS silver_lsn,
        ps._s_silver_ts              AS schedule_silver_ts,
        LAG(ps.startdate)      OVER w AS prev_startdate,
        LAG(ps.enddate)        OVER w AS prev_enddate,
        LAG(ps.job_startdate)  OVER w AS prev_job_startdate,
        LAG(ps.job_enddate)    OVER w AS prev_job_enddate,
        LAG(ps.version)        OVER w AS prev_version,
        LAG(ps._s_silver_ts)   OVER w AS prev_schedule_silver_ts
    FROM silver.project_schedule ps
    WHERE ps._s_tenantid   = %(tenantid)s
      AND ps._s_is_deleted = FALSE
    WINDOW w AS (PARTITION BY ps.project_id, ps._s_env ORDER BY ps.version)
)
SELECT
    sc.*,
    p.project_uid                    AS project_uid,
    p.description                    AS description,
    p.order_no                       AS order_no,
    p.customer                       AS customer_raw,
    p._s_silver_ts                   AS project_silver_ts,
    COALESCE(rs_agg.completions, '[]'::json)  AS resource_completions,
    rs_agg.rs_silver_ts                       AS rs_silver_ts,
    COALESCE(al_agg.events, '[]'::json)       AS audit_events,
    al_agg.al_silver_ts                       AS al_silver_ts,
    COALESCE(rss_agg.snapshots, '[]'::json)   AS rule_solve_snapshots,
    rss_agg.rss_silver_ts                     AS rss_silver_ts
FROM schedules sc
JOIN silver.project p
       ON p.id            = sc.project_id
      AND p._s_tenantid   = sc.tenantid
      AND p._s_env        = sc.source_env
      AND p._s_is_deleted = FALSE

-- Evidence 1: resources that actually finished a completed assignment on
-- THIS project within the correlation window (early-finish signal).
LEFT JOIN LATERAL (
    SELECT
        JSON_AGG(JSON_BUILD_OBJECT(
            'resourceId', rs.resource_id,
            'startTime',  rs.start_time::text,
            'endTime',    rs.end_time::text,
            'duration',   rs.duration
        ) ORDER BY rs.end_time)                AS completions,
        MAX(rs._s_silver_ts)                   AS rs_silver_ts
    FROM silver.resource_schedule rs
    WHERE rs._s_tenantid   = sc.tenantid
      AND rs._s_env        = sc.source_env
      AND rs.project_id    = sc.project_id
      AND rs._s_is_deleted = FALSE
      AND rs.status        = 'completed'
      AND rs.end_time BETWEEN
            COALESCE(sc.prev_schedule_silver_ts,
                     sc.schedule_silver_ts - (INTERVAL '1 day' * %(no_prev_window_days)s))
            - (INTERVAL '1 day' * %(window_pad_days)s)
        AND sc.schedule_silver_ts + (INTERVAL '1 day' * %(window_pad_days)s)
) rs_agg ON TRUE

-- Evidence 2: reschedule-related audit trail entries for this project
-- (audit_log.resource_id matches project.project_uid, never project.id).
LEFT JOIN LATERAL (
    SELECT
        JSON_AGG(JSON_BUILD_OBJECT(
            'action',       al.action,
            'resourceType', al.resource_type,
            'createdAt',    al.created_at::text,
            'context',      al.data -> 'context',
            'taskUid',      al.data ->> 'taskUid'
        ) ORDER BY al.created_at)              AS events,
        MAX(al._s_silver_ts)                   AS al_silver_ts
    FROM silver.audit_log al
    WHERE al._s_tenantid   = sc.tenantid
      AND al._s_env        = sc.source_env
      AND al.resource_id   = p.project_uid
      AND al.action ILIKE '%%RESCHEDUL%%'
      AND al.created_at BETWEEN
            COALESCE(sc.prev_schedule_silver_ts,
                     sc.schedule_silver_ts - (INTERVAL '1 day' * %(no_prev_window_days)s))
            - (INTERVAL '1 day' * %(window_pad_days)s)
        AND sc.schedule_silver_ts + (INTERVAL '1 day' * %(window_pad_days)s)
) al_agg ON TRUE

-- Evidence 3: the scheduling engine's own record of a solve/recompute run
-- for this project. attributes_snapshot is intentionally NOT selected —
-- its structure is opaque/unmapped (see module docstring).
LEFT JOIN LATERAL (
    SELECT
        JSON_AGG(JSON_BUILD_OBJECT(
            'schedulingType',    rss.scheduling_type,
            'rulesHash',         rss.rules_hash,
            'ruleCount',         rss.rule_count,
            'firedCount',        rss.fired_count,
            'convergencePasses', rss.convergence_passes,
            'capturedAt',        rss.captured_at::text
        ) ORDER BY rss.captured_at)             AS snapshots,
        MAX(rss._s_silver_ts)                   AS rss_silver_ts
    FROM silver.rule_solve_snapshot rss
    WHERE rss._s_tenantid = sc.tenantid
      AND rss._s_env      = sc.source_env
      -- explicit business tenant_id column carried despite per-tenant DB
      -- (cross-cutting rule — see EDEN_CustomerSupport_Reasoning_Constraints.md)
      AND rss.tenant_id    = sc.tenantid
      AND rss.project_id   = sc.project_id
      AND rss.captured_at BETWEEN
            COALESCE(sc.prev_schedule_silver_ts,
                     sc.schedule_silver_ts - (INTERVAL '1 day' * %(no_prev_window_days)s))
            - (INTERVAL '1 day' * %(window_pad_days)s)
        AND sc.schedule_silver_ts + (INTERVAL '1 day' * %(window_pad_days)s)
) rss_agg ON TRUE

ORDER BY sc.project_id, sc.version
"""


def _scoped_query(project_id: str | None) -> str:
    if project_id is None:
        return _SILVER_QUERY
    return _SILVER_QUERY.replace(
        "ORDER BY sc.project_id, sc.version",
        "WHERE sc.project_id = %(project_id)s\nORDER BY sc.project_id, sc.version",
        1,
    )


def _query_params(tenantid: str, project_id: str | None = None) -> dict:
    params = {
        "tenantid": tenantid,
        "no_prev_window_days": _NO_PREV_WINDOW_DAYS,
        "window_pad_days": _WINDOW_PAD_DAYS,
    }
    if project_id is not None:
        params["project_id"] = project_id
    return params


def iter_silver_rows(
    pg_dsn: str, tenantid: str, max_docs: int = 0, project_id: str | None = None
) -> Iterator[dict]:
    conn = psycopg2.connect(pg_dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(_scoped_query(project_id), _query_params(tenantid, project_id))
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
    SELECT 1 FROM silver.project_schedule    WHERE _s_tenantid = %(t)s AND _s_silver_ts > %(s)s
    UNION ALL
    SELECT 1 FROM silver.resource_schedule    WHERE _s_tenantid = %(t)s AND _s_silver_ts > %(s)s
    UNION ALL
    SELECT 1 FROM silver.audit_log           WHERE _s_tenantid = %(t)s AND _s_silver_ts > %(s)s
    UNION ALL
    SELECT 1 FROM silver.rule_solve_snapshot WHERE _s_tenantid = %(t)s AND _s_silver_ts > %(s)s
)
"""

# MIN of each _s_env's own MAX (combined across all four tables for that
# env), not the overall MAX — see load_bom_stock_gap.py / canonical loaders
# for why a shared cursor advanced to the fastest-moving env's watermark
# would mask a slower env's still-unprocessed update indefinitely.
_MAX_TS_SQL = """
SELECT MIN(env_max) FROM (
    SELECT _s_env, MAX(ts) AS env_max FROM (
        SELECT _s_env, _s_silver_ts AS ts FROM silver.project_schedule    WHERE _s_tenantid = %(t)s
        UNION ALL
        SELECT _s_env, _s_silver_ts AS ts FROM silver.resource_schedule    WHERE _s_tenantid = %(t)s
        UNION ALL
        SELECT _s_env, _s_silver_ts AS ts FROM silver.audit_log           WHERE _s_tenantid = %(t)s
        UNION ALL
        SELECT _s_env, _s_silver_ts AS ts FROM silver.rule_solve_snapshot WHERE _s_tenantid = %(t)s
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


def _date_diff_days(curr, prev) -> int | None:
    """Whole-day difference curr - prev, for date/datetime-like values."""
    if curr is None or prev is None:
        return None
    try:
        delta = curr - prev
        return delta.days if hasattr(delta, "days") else None
    except TypeError:
        return None


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


def _shape_completion(raw: dict) -> dict:
    return {
        "resourceId": (str(raw["resourceId"]) if raw.get("resourceId") is not None else None),
        "startTime":  parse_ts(raw.get("startTime")),
        "endTime":    parse_ts(raw.get("endTime")),
        "duration":   raw.get("duration"),
    }


def _shape_audit_event(raw: dict) -> dict:
    return {
        "action":       raw.get("action"),
        "resourceType": raw.get("resourceType"),
        "createdAt":    parse_ts(raw.get("createdAt")),
        "context":      raw.get("context"),
        "taskUid":      (str(raw["taskUid"]) if raw.get("taskUid") is not None else None),
    }


def _shape_rule_solve(raw: dict) -> dict:
    return {
        "schedulingType":    raw.get("schedulingType"),
        "rulesHash":         raw.get("rulesHash"),
        "ruleCount":         raw.get("ruleCount"),
        "firedCount":        raw.get("firedCount"),
        "convergencePasses": raw.get("convergencePasses"),
        "capturedAt":        parse_ts(raw.get("capturedAt")),
    }


def shape_doc(row: dict, gold_ts: datetime) -> dict:
    watermarks = {
        "project_schedule":    _to_ts(row.get("schedule_silver_ts")),
        "project":             _to_ts(row.get("project_silver_ts")),
        "resource_schedule":   _to_ts(row.get("rs_silver_ts")),
        "audit_log":           _to_ts(row.get("al_silver_ts")),
        "rule_solve_snapshot": _to_ts(row.get("rss_silver_ts")),
    }
    valid_wm = [ts for ts in watermarks.values() if ts is not None]
    lag_ms = int((gold_ts - min(valid_wm)).total_seconds() * 1000) if valid_wm else 0

    startdate, enddate = row.get("startdate"), row.get("enddate")
    job_startdate, job_enddate = row.get("job_startdate"), row.get("job_enddate")
    prev_startdate, prev_enddate = row.get("prev_startdate"), row.get("prev_enddate")
    prev_job_startdate, prev_job_enddate = row.get("prev_job_startdate"), row.get("prev_job_enddate")

    has_prev = row.get("prev_version") is not None
    moved = {
        "startDate":    has_prev and startdate != prev_startdate,
        "endDate":      has_prev and enddate != prev_enddate,
        "jobStartDate": has_prev and job_startdate != prev_job_startdate,
        "jobEndDate":   has_prev and job_enddate != prev_job_enddate,
    }
    any_moved = any(moved.values())

    days_moved = {
        "startDate":    _date_diff_days(startdate, prev_startdate),
        "endDate":      _date_diff_days(enddate, prev_enddate),
        "jobStartDate": _date_diff_days(job_startdate, prev_job_startdate),
        "jobEndDate":   _date_diff_days(job_enddate, prev_job_enddate),
    }

    completions = [_shape_completion(c) for c in (row.get("resource_completions") or [])]
    audit_events = [_shape_audit_event(a) for a in (row.get("audit_events") or [])]
    rule_solves = [_shape_rule_solve(r) for r in (row.get("rule_solve_snapshots") or [])]

    # Heuristic confidence, not proof — see _CAUSATION_CAVEAT. "corroborated"
    # only means at least one independent signal exists in the correlation
    # window, not that it caused the move.
    evidence_count = sum([bool(completions), bool(audit_events), bool(rule_solves)])
    confidence = (
        "unconfirmed" if not any_moved else
        "corroborated" if evidence_count >= 1 else
        "no_corroborating_signal"
    )

    customer = _maybe_json(row.get("customer_raw"))
    customer = customer if isinstance(customer, dict) else None

    doc = {
        "tenantId":        row["tenantid"],
        "projectId":       str(row["project_id"]),
        "projectUid":      row.get("project_uid"),
        "projectName":     (row.get("description")
                              or _customer_display_name(customer)
                              or row.get("order_no")),
        "orderNo":         row.get("order_no"),
        "version":         row.get("version"),
        "prevVersion":     row.get("prev_version"),
        "startDate":       parse_ts(startdate),
        "endDate":         parse_ts(enddate),
        "jobStartDate":    parse_ts(job_startdate),
        "jobEndDate":      parse_ts(job_enddate),
        "prevStartDate":   parse_ts(prev_startdate),
        "prevEndDate":     parse_ts(prev_enddate),
        "prevJobStartDate": parse_ts(prev_job_startdate),
        "prevJobEndDate":  parse_ts(prev_job_enddate),
        # Passed through read-only — checked and ruled out as a causal code
        # (only ever SAVE_TASKS / "Auto Rescheduling Error" fleet-wide).
        "reason":          row.get("reason"),
        "moved":           moved,
        "anyMoved":        any_moved,
        "daysMoved":       days_moved,
        "resourceCompletions": completions,
        "auditEvents":         audit_events,
        "ruleSolveSnapshots":  rule_solves,
        "evidenceSummary": {
            "hasResourceCompletionEvidence": bool(completions),
            "hasAuditLogEvidence":           bool(audit_events),
            "hasRuleSolveEvidence":          bool(rule_solves),
            "confidence":                    confidence,
            "caveat":                        _CAUSATION_CAVEAT,
        },
        "_grain":              "eden_schedule_move_evidence",
        "_schemaVersion":      1,
        "_silverLsn":          int(row.get("silver_lsn") or 0),
        "isDeleted":           False,
        "dataAsOf":            gold_ts,
        "_silverWatermarks":   watermarks,
        "lafieldcraft":               lag_ms,
    }
    doc.update(lineage_fields(FEATURE_NAME, gold_ts, iso=False,
                              source_updated_at=watermarks.get("project_schedule"),
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
                             retryWrites=False, appname="eden-schedule-move-evidence")
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
