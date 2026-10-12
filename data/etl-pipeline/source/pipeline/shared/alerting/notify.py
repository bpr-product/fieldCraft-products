"""
notify.py

Instant alerting for the FIELDCRAFT pipeline.

Severity levels:
  CRITICAL — wake someone up (Teams @channel + optional PagerDuty)
  WARNING  — visible in Teams but no page
  INFO     — digest-only (not sent immediately)

Configuration (environment variables):
  ALERT_TEAMS_WEBHOOK_URL   — MicroFieldCrafts Incoming Webhook URL
  ALERT_PAGERDUTY_KEY       — PagerDuty Events API v2 integration key (optional)
  ALERT_ENV                 — environment label shown in messages (default: local)
  ALERT_ENABLED             — set to "false" to suppress all outbound calls (testing)
"""
import json
import logging
import os
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import requests

log = logging.getLogger(__name__)

TEAMS_WEBHOOK   = os.environ.get("ALERT_TEAMS_WEBHOOK_URL", "")
PAGERDUTY_KEY   = os.environ.get("ALERT_PAGERDUTY_KEY",     "")
ALERT_ENV       = os.environ.get("ALERT_ENV",               os.environ.get("ETL_ENV", "local"))
ALERT_ENABLED   = os.environ.get("ALERT_ENABLED",           "true").lower() != "false"
# Set ALERT_SSL_VERIFY=false in .env.local only — never in production
_SSL_VERIFY     = os.environ.get("ALERT_SSL_VERIFY", "true").lower() != "false"

_PAGERDUTY_URL  = "https://events.pagerduty.com/v2/enqueue"


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING  = "WARNING"
    INFO     = "INFO"


# Colour coding for Teams adaptive cards
_SEVERITY_COLOR = {
    Severity.CRITICAL: "attention",   # red
    Severity.WARNING:  "warning",     # yellow
    Severity.INFO:     "good",        # green
}

_SEVERITY_EMOJI = {
    Severity.CRITICAL: "🚨",
    Severity.WARNING:  "⚠️",
    Severity.INFO:     "ℹ️",
}


def _teams_payload(severity: Severity, title: str, body: str, details: dict) -> dict:
    """Build a Teams Workflows-compatible Adaptive Card payload.

    Uses the Power Automate Workflows webhook format (required after Microsoft
    retired O365 Connectors in Aug 2024). The old {"text": "..."} MessageCard
    format shows 'Card - access it on https://go.skype.com/cards.unsupported'
    in modern Teams clients.
    """
    ts    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    emoji = _SEVERITY_EMOJI[severity]
    color = _SEVERITY_COLOR[severity]

    facts = []
    if details:
        for k, v in details.items():
            facts.append({"title": str(k), "value": str(v)})
    facts.append({"title": "Environment", "value": ALERT_ENV})
    facts.append({"title": "Time", "value": ts})

    card_body: list = [
        {
            "type": "TextBlock",
            "text": f"{emoji} {title}",
            "weight": "Bolder",
            "size": "Medium",
            "color": color,
            "wrap": True,
        }
    ]
    if body:
        card_body.append({
            "type": "TextBlock",
            "text": body,
            "wrap": True,
        })
    if facts:
        card_body.append({
            "type": "FactSet",
            "facts": facts,
        })

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": card_body,
                },
            }
        ],
    }


def _send_teams(severity: Severity, title: str, body: str, details: dict) -> None:
    if not TEAMS_WEBHOOK:
        log.debug("ALERT_TEAMS_WEBHOOK_URL not set — skipping Teams notification")
        return
    payload = _teams_payload(severity, title, body, details)
    try:
        r = requests.post(TEAMS_WEBHOOK, json=payload, timeout=10, verify=_SSL_VERIFY)
        r.raise_for_status()
        log.info("Teams alert sent: [%s] %s", severity.value, title)
    except Exception as exc:
        log.error("Teams alert FAILED: %s", exc)


def _send_pagerduty(title: str, body: str, details: dict, dedup_key: str) -> None:
    if not PAGERDUTY_KEY:
        return
    payload = {
        "routing_key":  PAGERDUTY_KEY,
        "event_action": "trigger",
        "dedup_key":    dedup_key,
        "payload": {
            "summary":   title,
            "source":    f"fieldcraft-pipeline-{ALERT_ENV}",
            "severity":  "critical",
            "custom_details": details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "links": [{"href": body, "text": "Details"}] if body.startswith("http") else [],
    }
    try:
        r = requests.post(_PAGERDUTY_URL, json=payload, timeout=10)
        r.raise_for_status()
        log.info("PagerDuty alert triggered: %s", dedup_key)
    except Exception as exc:
        log.error("PagerDuty alert FAILED: %s", exc)


def alert(
    severity: Severity,
    title: str,
    body: str = "",
    details: Optional[dict] = None,
    dedup_key: Optional[str] = None,
) -> None:
    """
    Send an instant alert to Teams (and PagerDuty if CRITICAL).

    Parameters
    ----------
    severity  : Severity.CRITICAL | WARNING | INFO
    title     : short headline shown in Teams card title
    body      : longer description — shown in card body
    details   : key-value facts shown in the card FactSet
    dedup_key : PagerDuty dedup key — prevents duplicate pages for same incident
    """
    if not ALERT_ENABLED:
        log.info("[ALERT SUPPRESSED] [%s] %s", severity.value, title)
        return

    details   = details or {}
    dedup_key = dedup_key or f"{ALERT_ENV}-{title[:60]}-{int(time.time() // 300)}"

    log.warning("[%s] %s — %s", severity.value, title, body)

    if severity in (Severity.CRITICAL, Severity.WARNING):
        _send_teams(severity, title, body, details)

    if severity == Severity.CRITICAL:
        _send_pagerduty(title, body, details, dedup_key)


# ── Convenience wrappers ──────────────────────────────────────────────────────

def critical(title: str, body: str = "", details: Optional[dict] = None,
             dedup_key: Optional[str] = None) -> None:
    alert(Severity.CRITICAL, title, body, details, dedup_key)


def warning(title: str, body: str = "", details: Optional[dict] = None,
            dedup_key: Optional[str] = None) -> None:
    alert(Severity.WARNING, title, body, details, dedup_key)


def info(title: str, body: str = "", details: Optional[dict] = None) -> None:
    alert(Severity.INFO, title, body, details)


# ── Pipeline-specific alert helpers ──────────────────────────────────────────

def _fmt_lag(lag_s: float) -> str:
    if lag_s >= 3600:
        return f"{lag_s / 3600:.1f} hours"
    if lag_s >= 60:
        return f"{lag_s / 60:.0f} minutes"
    return f"{lag_s:.0f} seconds"


def alert_silver_stale_grouped(stale_tables: list[dict]) -> None:
    count     = len(stale_tables)
    worst     = sorted(stale_tables, key=lambda x: x["lag_s"], reverse=True)[:5]
    max_lag   = _fmt_lag(worst[0]["lag_s"]) if worst else "unknown"
    worst_str = "\n".join(
        f"  • {t['entity']} — {_fmt_lag(t['lag_s'])} ago  ({t['row_count']:,} rows)"
        for t in worst
    )
    critical(
        title=f"Silver data is stale — {count} tables not updated in {max_lag}",
        body=(
            f"**WHAT HAPPENED**\n"
            f"{count} Silver tables have not received new data in {max_lag}.\n\n"
            f"**ROOT CAUSE** — most likely one of:\n"
            f"  1. Flink Silver job crashed or stopped\n"
            f"  2. Debezium connectors down (no CDC events reaching Kafka)\n"
            f"  3. Pipeline was manually stopped\n\n"
            f"**IMPACT**\n"
            f"APIs, ML models, and dashboards are reading outdated data.\n\n"
            f"**MOST AFFECTED TABLES**\n"
            f"{worst_str}\n\n"
            f"**HOW TO FIX**\n"
            f"  Step 1 — Check Flink: http://localhost:8081\n"
            f"  Step 2 — Restart pipeline: bash scripts/start_pipeline.sh\n"
            f"  Step 3 — If Flink running but stale: bash scripts/deploy_connectors.sh"
        ),
        details={
            "tables_affected": str(count),
            "worst_lag":       max_lag,
            "fix":             "bash scripts/start_pipeline.sh",
        },
        dedup_key=f"silver-stale-grouped-{ALERT_ENV}",
    )


def alert_dlq_grouped(dlq_tables: list[dict]) -> None:
    total    = sum(d["depth"] for d in dlq_tables)
    affected = "\n".join(
        f"  • {d['entity']} — {d['depth']} events quarantined"
        for d in dlq_tables[:5]
    )
    warning(
        title=f"DLQ Alert — {total} events quarantined across {len(dlq_tables)} entities",
        body=(
            f"**WHAT HAPPENED**\n"
            f"{total} CDC events could not be written to Silver and were sent to the DLQ.\n\n"
            f"**ROOT CAUSE** — events land in DLQ when:\n"
            f"  1. Payload is missing required fields (tenantid or id)\n"
            f"  2. JSON is malformed or cannot be parsed\n"
            f"  3. Unknown CDC operation type received\n"
            f"  4. Schema mismatch between source and Silver table\n\n"
            f"**IMPACT**\n"
            f"These {total} records are NOT in Silver — APIs and dashboards are missing this data.\n\n"
            f"**AFFECTED ENTITIES**\n"
            f"{affected}\n\n"
            f"**HOW TO FIX**\n"
            f"  Step 1 — Inspect DLQ: python scripts/dlq_consumer.py\n"
            f"  Step 2 — Fix the root cause (schema or payload issue)\n"
            f"  Step 3 — Replay events back to the source topic"
        ),
        details={
            "total_quarantined": str(total),
            "entities_affected": str(len(dlq_tables)),
            "fix":               "python scripts/dlq_consumer.py",
        },
        dedup_key=f"dlq-nonempty-grouped-{ALERT_ENV}",
    )


def alert_connectors_down_grouped(failed: list[dict]) -> None:
    names = "\n".join(f"  • {c['name']} (state: {c.get('state', 'UNKNOWN')})" for c in failed)
    critical(
        title=f"{len(failed)} Debezium connector(s) failed — CDC has stopped flowing",
        body=(
            f"**WHAT HAPPENED**\n"
            f"{len(failed)} Debezium connector task(s) are in FAILED state.\n"
            f"No new CDC events are being read from PostgreSQL.\n\n"
            f"**ROOT CAUSE** — common causes:\n"
            f"  1. Source PostgreSQL unreachable from Docker (network/firewall)\n"
            f"  2. Replication slot dropped on the source DB\n"
            f"  3. Database credentials changed or expired\n"
            f"  4. Tenant database does not exist or was renamed\n\n"
            f"**IMPACT**\n"
            f"No new inserts, updates, or deletes will reach Silver until this is fixed.\n\n"
            f"**FAILED CONNECTORS**\n"
            f"{names}\n\n"
            f"**HOW TO FIX**\n"
            f"  Step 1 — Check error: curl http://localhost:8083/connectors/NAME/status\n"
            f"  Step 2 — Redeploy: bash scripts/deploy_connectors.sh\n"
            f"  Step 3 — Verify DB is reachable from Docker"
        ),
        details={
            "failed_count": str(len(failed)),
            "fix":          "bash scripts/deploy_connectors.sh",
        },
        dedup_key=f"connectors-down-grouped-{ALERT_ENV}",
    )


def alert_flink_no_jobs() -> None:
    critical(
        title="Pipeline stopped — no Flink jobs are running",
        body=(
            f"**WHAT HAPPENED**\n"
            f"All Flink streaming jobs have stopped.\n"
            f"Bronze ingest and Silver CDC merge are both down.\n\n"
            f"**ROOT CAUSE** — common causes:\n"
            f"  1. Pipeline was manually stopped (docker compose down)\n"
            f"  2. Flink TaskManager ran out of memory and crashed\n"
            f"  3. Job failed due to a code exception (check Flink UI)\n"
            f"  4. Checkpoint directory full or inaccessible\n\n"
            f"**IMPACT**\n"
            f"No CDC events are being processed. Bronze and Silver are not updating.\n"
            f"All downstream consumers are reading stale data.\n\n"
            f"**HOW TO FIX**\n"
            f"  Step 1 — Check Flink UI: http://localhost:8081\n"
            f"  Step 2 — Restart: bash scripts/start_pipeline.sh\n"
            f"  Step 3 — If OOM: increase TaskManager memory in docker-compose.local.yml"
        ),
        details={
            "status": "Bronze stopped | Silver stopped",
            "fix":    "bash scripts/start_pipeline.sh",
        },
        dedup_key=f"flink-no-running-jobs-{ALERT_ENV}",
    )


def alert_wal_slot_lag(slot: str, lag_mb: float) -> None:
    severity = Severity.CRITICAL if lag_mb > 2048 else Severity.WARNING
    alert(
        severity=severity,
        title=f"WAL slot '{slot}' is {lag_mb:.0f} MB behind — PostgreSQL disk fill risk",
        body=(
            f"WHAT HAPPENED\n"
            f"PostgreSQL replication slot '{slot}' is {lag_mb:.0f} MB behind the current WAL position.\n\n"
            f"ROOT CAUSE\n"
            f"Debezium has been disconnected and the replication slot is keeping old WAL segments "
            f"on disk. PostgreSQL cannot reclaim this disk space until Debezium catches up.\n\n"
            f"IMPACT\n"
            f"{'CRITICAL: ' if lag_mb > 2048 else 'WARNING: '}"
            f"If the lag continues to grow, the PostgreSQL data disk will fill up and the "
            f"source database will stop accepting writes — taking down the live application.\n\n"
            f"HOW TO FIX\n"
            f"  Step 1 — Restart connector: bash scripts/deploy_connectors.sh\n"
            f"  Step 2 — Monitor lag: SELECT slot_name, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) FROM pg_replication_slots;\n"
            f"  Step 3 — If connector cannot recover, drop and recreate the slot"
        ),
        details={
            "slot":           slot,
            "lag_mb":         f"{lag_mb:.0f}",
            "critical_at_mb": "2048",
            "fix":            "bash scripts/deploy_connectors.sh",
        },
        dedup_key=f"wal-slot-lag-{slot}-{ALERT_ENV}",
    )


def notify_pipeline_started(flink_jobs: int = 0, connectors: int = 0) -> None:
    info(
        title=f"Pipeline started — {ALERT_ENV}",
        body=(
            f"**FIELDCRAFT Data Pipeline is now running.**\n\n"
            f"**WHAT IS RUNNING**\n"
            f"  • Flink jobs submitted: {flink_jobs}\n"
            f"  • Debezium connectors deployed: {connectors}\n"
            f"  • Bronze ingest: active\n"
            f"  • Silver CDC merge: active\n"
            f"  • Teams monitor: checking every 60 seconds\n\n"
            f"**WHAT TO EXPECT**\n"
            f"  • Debezium will snapshot source PostgreSQL (may take a few minutes)\n"
            f"  • Bronze Iceberg tables will start filling with raw CDC events\n"
            f"  • Silver tables will be upserted as events flow through\n\n"
            f"Next health digest will be sent in 30 minutes."
        ),
    )


def alert_circuit_breaker_tripped(entity: str, rate_per_min: int, limit: int,
                                  freeze_minutes: int) -> None:
    """
    Fired by dlq_watcher when DLQ ingress for an entity exceeds the rate limit.
    PagerDuty page because auto-replay has been suspended — a human needs to
    investigate before the freeze window expires.
    """
    critical(
        title=f"DLQ circuit breaker TRIPPED — {entity} ({rate_per_min}/min > {limit}/min)",
        body=(
            f"**WHAT HAPPENED**\n"
            f"Entity `{entity}` is producing DLQ events faster than the watcher will auto-replay.\n"
            f"Auto-replay is FROZEN for {freeze_minutes} minutes to prevent a replay storm.\n\n"
            f"**ROOT CAUSE** — almost always one of:\n"
            f"  1. A producer bug is generating malformed events at high volume\n"
            f"  2. Silver PG schema drift just deployed and ALL events are failing the upsert\n"
            f"  3. A flaky FK relationship just collapsed and child events are piling up\n\n"
            f"**IMPACT**\n"
            f"  • Events for `{entity}` are NOT being auto-replayed during the freeze\n"
            f"  • Other entities continue to recover normally\n"
            f"  • Existing DLQ events are NOT lost — they sit in `local.public.{entity}.dlq`\n\n"
            f"**HOW TO INVESTIGATE**\n"
            f"  Step 1 — inspect recent DLQ:  python scripts/dlq_consumer.py --entity {entity} --inspect\n"
            f"  Step 2 — check audit table:   SELECT error_type, COUNT(*) FROM silver._dlq_corrections "
            f"WHERE entity='{entity}' AND corrected_at > NOW() - INTERVAL '15 min' GROUP BY 1;\n"
            f"  Step 3 — once root cause is fixed, manually unfreeze via Redis:\n"
            f"           DEL dlq:cb:frozen:{entity}"
        ),
        details={
            "entity":         entity,
            "rate_per_min":   str(rate_per_min),
            "limit_per_min":  str(limit),
            "freeze_minutes": str(freeze_minutes),
        },
        dedup_key=f"dlq-cb-{entity}-{ALERT_ENV}",
    )


def alert_dlq_by_error_type(entity: str, error_type: str, count: int) -> None:
    """
    Periodic digest of DLQ traffic broken down by error_type. Warning-only —
    informational; the circuit breaker is the load-shedding mechanism.
    """
    warning(
        title=f"DLQ {error_type} surge on {entity} — {count} events",
        body=(
            f"**WHAT HAPPENED**\n"
            f"Entity `{entity}` has accumulated {count} events of type `{error_type}` "
            f"in the recent watcher window.\n\n"
            f"**WHAT THE WATCHER IS DOING**\n"
            f"  • Auto-replaying mechanically (PG_TRANSIENT, LATE_ARRIVAL) per policy\n"
            f"  • Anything not auto-handled is parked at `local.public.{entity}.dlq.parking`\n\n"
            f"**HOW TO REVIEW**\n"
            f"  SELECT * FROM silver._dlq_corrections\n"
            f"  WHERE entity='{entity}' AND error_type='{error_type}'\n"
            f"  ORDER BY corrected_at DESC LIMIT 50;"
        ),
        details={
            "entity":     entity,
            "error_type": error_type,
            "count":      str(count),
        },
        dedup_key=f"dlq-etype-{entity}-{error_type}-{ALERT_ENV}",
    )


def notify_pipeline_stopped() -> None:
    warning(
        title=f"Pipeline stopped — {ALERT_ENV}",
        body=(
            f"**FIELDCRAFT Data Pipeline has been stopped.**\n\n"
            f"**WHAT STOPPED**\n"
            f"  • All Docker containers removed\n"
            f"  • Flink jobs no longer running\n"
            f"  • Debezium connectors offline\n"
            f"  • Bronze and Silver are no longer updating\n\n"
            f"**DATA STATE**\n"
            f"  • Bronze Iceberg data is preserved (MinIO volume)\n"
            f"  • Silver PostgreSQL data is preserved (silver-pg volume)\n"
            f"  • Kafka offsets are lost (will re-snapshot on next start)\n\n"
            f"To restart: bash scripts/start_pipeline.sh"
        ),
    )


# Keep old per-entity signatures as thin wrappers so nothing else breaks
def alert_silver_stale(entity: str, lag_seconds: float) -> None:
    alert_silver_stale_grouped([{"entity": entity, "lag_s": lag_seconds, "row_count": 0}])


def alert_dlq_nonempty(entity: str, depth: int, topic: str) -> None:
    alert_dlq_grouped([{"entity": entity, "depth": depth, "topic": topic}])


def alert_connector_down(connector: str, state: str) -> None:
    alert_connectors_down_grouped([{"name": connector, "state": state}])
