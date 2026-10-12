"""Gold audit writer — records pipeline execution history to MongoDB.

Three collections in the ``gold_audit`` database:

  pipeline_run  — one document per cron / manual run_pipeline.py invocation
  gold_event    — one document per step event within a run (step_complete,
                  step_failed, dq_warning, dq_failure, freshness_stale, ...)
  job_state     — one document per (feature, tenantId) — current state only,
                  upserted on every step completion

Retention (TTL indexes created by setup_gold_audit):
  pipeline_run  — 90 days via endedAt TTL index
  gold_event    — 90 days via occurredAt TTL index
  job_state     — no TTL, current state only

All public functions are non-fatal: write failures are logged at WARNING
level and swallowed so pipeline execution is never blocked by audit writes.

Relationship to existing stores:
  gold_pipeline._job_state (Silver PG)  — processing cursor for correctness;
                                           never touched by this module
  gold_event.pipeline_runs (Cosmos DB)  — RETIRED 2026-06-11. It duplicated this
                                           module's gold_event collection with no
                                           in-repo consumer; event_writer.py and
                                           its record_step double-write were removed.
                                           gold_audit is now the single audit
                                           system of record.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("gold.pipeline.audit")

_AUDIT_DB = os.environ.get("GOLD_AUDIT_DB_NAME", "gold_audit")
_TIMEOUT_MS = 3_000
_TTL_SECONDS = 7_776_000  # 90 days


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _db(mongo_uri: str):
    from pymongo import MongoClient
    return MongoClient(mongo_uri, serverSelectionTimeoutMS=_TIMEOUT_MS,
                       appname="gold-audit")[_AUDIT_DB]


def _triggered_by() -> str:
    return "manual" if sys.stdin.isatty() else "cron"


# ── Setup (idempotent) ────────────────────────────────────────────────────────

def setup_gold_audit(mongo_uri: str) -> int:
    """Provision the live gold_audit collection (``gold_event``) + indexes.

    Returns 0 on success, 1 on error.

    Only ``gold_event`` is provisioned — it is written continuously by the
    realtime streams (quarantine / sink_dropped via ``record_stream_event``).
    The batch-only ``pipeline_run`` and ``job_state`` collections are **not**
    pre-created: in a pure-realtime / no-batch deployment they would stay empty.
    If a ``run_pipeline --rebuild`` is ever executed, ``open_run`` / ``close_run``
    / ``upsert_job_state`` create them on first write (Mongo auto-creates on
    insert); their monitoring indexes can be added then via ``--only gold_audit``
    after the collections exist. This keeps the schema free of empty stale tables.
    """
    try:
        db = _db(mongo_uri)

        # gold_event indexes
        db.gold_event.create_index([("runId", 1)])
        db.gold_event.create_index([("tenantId", 1), ("feature", 1), ("occurredAt", -1)])
        db.gold_event.create_index([("eventType", 1), ("occurredAt", -1)])
        db.gold_event.create_index(
            [("feature", 1), ("stepName", 1), ("status", 1)],
            name="feature_step_status",
        )
        # TTL: 90d rolling window
        db.gold_event.create_index([("occurredAt", 1)],
                                   expireAfterSeconds=_TTL_SECONDS,
                                   name="ttl_occurred_at")

        # pipeline_run / job_state are intentionally NOT provisioned here
        # (batch-only; created lazily on first rebuild write). See docstring.

        log.info("gold_audit database ready (%s) — gold_event provisioned", _AUDIT_DB)
        return 0
    except Exception as exc:
        log.error("setup_gold_audit failed: %s", exc)
        return 1


# ── Run lifecycle ─────────────────────────────────────────────────────────────

def open_run(
    mongo_uri: str,
    *,
    feature: Optional[str],
    dry_run: bool,
    triggered_by: Optional[str] = None,
    pipeline_version: Optional[str] = None,
) -> Optional[str]:
    """Insert a pipeline_run document with status='running'.

    Returns run_id (str UUID) to pass through the pipeline, or None on error.
    """
    run_id = str(uuid.uuid4())
    try:
        doc = {
            "runId":           run_id,
            "startedAt":       _now(),
            "endedAt":         None,
            "status":          "running",
            "feature":         feature,
            "vmRole":          os.environ.get("VM_ROLE", "all"),
            "dryRun":          dry_run,
            "tenantsTotal":    None,
            "tenantsPassed":   None,
            "tenantsFailed":   None,
            "elapsedMs":       None,
            "triggeredBy":     triggered_by or _triggered_by(),
            "pipelineVersion": pipeline_version or os.environ.get("GIT_SHA", "unknown"),
            "errorSummary":    None,
        }
        _db(mongo_uri).pipeline_run.insert_one(doc)
        log.debug("audit run opened: %s", run_id)
        return run_id
    except Exception as exc:
        log.warning("audit.open_run failed (non-fatal): %s", exc)
        return None


def close_run(
    mongo_uri: str,
    run_id: Optional[str],
    *,
    tenants_total: int,
    tenants_passed: int,
    tenants_failed: int,
    elapsed_ms: int,
    failed_summary: list[dict],
) -> None:
    """Update pipeline_run document to completed / partial / failed."""
    if not run_id:
        return
    if tenants_passed == 0 and tenants_failed > 0:
        status = "failed"
    elif tenants_failed > 0:
        status = "partial"
    else:
        status = "completed"
    try:
        _db(mongo_uri).pipeline_run.update_one(
            {"runId": run_id},
            {"$set": {
                "endedAt":       _now(),
                "status":        status,
                "tenantsTotal":  tenants_total,
                "tenantsPassed": tenants_passed,
                "tenantsFailed": tenants_failed,
                "elapsedMs":     elapsed_ms,
                "errorSummary":  failed_summary[:50] if failed_summary else None,
            }},
        )
        log.debug("audit run closed: %s status=%s", run_id, status)
    except Exception as exc:
        log.warning("audit.close_run failed (non-fatal): %s", exc)


# ── Step events ───────────────────────────────────────────────────────────────

def record_event(
    mongo_uri: str,
    run_id: Optional[str],
    *,
    tenant_id: str,
    feature: str,
    step_name: str,
    event_type: str,
    status: Optional[str] = None,
    duration_ms: Optional[int] = None,
    records_read: Optional[int] = None,
    records_written: Optional[int] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    metadata: Optional[dict] = None,
    dry_run: bool = False,
) -> None:
    """Append one gold_event document. Never raises.

    event_type values:
      step_complete   — loader step finished successfully
      step_failed     — loader step returned non-zero rc or raised
      step_skipped    — skipped due to failed dependency or timeout
      dq_warning      — post-load DQ gate found a non-critical issue
      dq_failure      — post-load DQ gate found a critical issue
      freshness_stale — Gold collection lag exceeds SLA threshold
    """
    if not run_id:
        return
    try:
        doc = {
            "runId":          run_id,
            "occurredAt":     _now(),
            "tenantId":       tenant_id,
            "feature":        feature,
            "stepName":       step_name,
            "eventType":      event_type,
            "status":         status,
            "durationMs":     duration_ms,
            "recordsRead":    records_read,
            "recordsWritten": records_written,
            "errorCode":      error_code,
            "errorMessage":   error_message[:2000] if error_message else None,
            "metadata":       metadata,
            "dryRun":         dry_run,
        }
        _db(mongo_uri).gold_event.insert_one(doc)
    except Exception as exc:
        log.warning("audit.record_event failed (non-fatal): %s", exc)


def record_stream_event(
    mongo_uri: str,
    *,
    tenant_id: str,
    feature: str,
    event_type: str,
    project_id: Optional[str] = None,
    attempts: Optional[int] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """Append one gold_event for a STREAMING-origin failure (no pipeline run).

    The Flink streams have no pipeline run_id, so the regular record_event
    (which requires run_id) skips them. This is the streaming complement: it
    records a quarantined recompute or a dropped sink write into the SAME
    gold_audit.gold_event collection (90-day TTL, existing indexes) so operators
    can query which (tenant, project, feature) is stuck and target a
    `run_pipeline.py --rebuild`. Dead-letter-as-audit — no DLQ topic/hub needed.
    Never raises.

    event_type values (streaming):
      recompute_quarantined — recompute failed past the retry cap; trigger skipped
      sink_dropped          — sink dropped doc(s) after a non-retryable write error
    """
    try:
        doc = {
            "runId":        f"stream:{feature}",   # marks streaming origin
            "occurredAt":   _now(),
            "tenantId":     tenant_id,
            "feature":      feature,
            "stepName":     "stream",
            "eventType":    event_type,
            "status":       "failed",
            "errorCode":    error_code,
            "errorMessage": error_message[:2000] if error_message else None,
            "metadata":     {"projectId": project_id, "attempts": attempts, "origin": "stream"},
            "dryRun":       False,
        }
        _db(mongo_uri).gold_event.insert_one(doc)
    except Exception as exc:   # noqa: BLE001 — audit is non-fatal
        log.warning("audit.record_stream_event failed (non-fatal): %s", exc)


# ── Job state ─────────────────────────────────────────────────────────────────

def upsert_job_state(
    mongo_uri: str,
    run_id: Optional[str],
    *,
    feature: str,
    tenant_id: str,
    status: str,
    watermark: Optional[datetime] = None,
    error: Optional[str] = None,
) -> None:
    """UPSERT gold_audit.job_state for (feature, tenantId).

    status values: 'idle' (success), 'running', 'error', 'disabled'
    Never raises.
    """
    now = _now()
    key = {"feature": feature, "tenantId": tenant_id}
    try:
        if status == "error":
            update = {
                "$set": {
                    "status":     status,
                    "lastRunId":  run_id,
                    "lastRunAt":  now,
                    "lastError":  error[:500] if error else None,
                    "updatedAt":  now,
                },
                "$inc": {"consecutiveFailures": 1},
                "$setOnInsert": {**key, "lastSuccessAt": None, "lastWatermark": None},
            }
        else:
            set_doc = {
                "status":              status,
                "lastRunId":           run_id,
                "lastRunAt":           now,
                "lastSuccessAt":       now,
                "consecutiveFailures": 0,
                "lastError":           None,
                "updatedAt":           now,
            }
            if watermark is not None:
                set_doc["lastWatermark"] = watermark
            update = {
                "$set":         set_doc,
                "$setOnInsert": key,
            }
        _db(mongo_uri).job_state.update_one(key, update, upsert=True)
    except Exception as exc:
        log.warning("audit.upsert_job_state failed (non-fatal): %s", exc)
