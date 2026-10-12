"""Gold data freshness SLA monitor.

After each tenant pipeline run, checks how stale Gold documents are
compared to Silver. Emits Prometheus metrics and logs warnings when
lag exceeds the SLA threshold defined in gold_config.yml.

Freshness lag = Gold goldUpdatedAt - Silver max(_s_silver_ts)

SLA thresholds (from gold_config.yml):
  batch collections : 900s (15 min)
  AI Assistant      : 2s
  Invoice Aging     : 60s
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

log = logging.getLogger("gold.monitoring.freshness")

# Map Gold collection name → Silver table name
_COLLECTION_TO_SILVER_TABLE: dict[str, str] = {
    "feature_store":            "project",
    "project_health":           "project",
    "invoice_summary":          "invoice",
    "task_intelligence":        "task",
    "procurement_status":       "bom",
    "workforce_status":         "resource_schedule",
    "bom_stock_gap":            "bom",
    "weather_schedule_cross":   "task",
    "bid_context":              "answers",
    "tenant_dashboard":         "project",
}

# Map loader step name → Gold collection name + db env var
_STEP_TO_COLLECTION: dict[str, tuple[str, str, str]] = {
    "load_estimate_snapshot":        ("GOLD_BPE_DB_NAME",         "gold_bpe",              "feature_store"),
    "load_project_health":           ("GOLD_EDEN_DB",  "gold_eden",  "project_health"),
    "load_invoice_summary":          ("GOLD_EDEN_DB",  "gold_eden",  "invoice_summary"),
    "load_task_intelligence":        ("GOLD_EDEN_DB",  "gold_eden",  "task_intelligence"),
    "load_procurement_status":       ("GOLD_EDEN_DB",  "gold_eden",  "procurement_status"),
    "load_workforce_status":         ("GOLD_EDEN_DB",  "gold_eden",  "workforce_status"),
    "load_bom_stock_gap":            ("GOLD_EDEN_DB",  "gold_eden",  "bom_stock_gap"),
    "load_weather_schedule_cross":   ("GOLD_EDEN_DB",  "gold_eden",  "weather_schedule_cross"),
    "load_bid_context":              ("GOLD_EDEN_DB",  "gold_eden",  "bid_context"),
    "load_tenant_dashboard":         ("GOLD_EDEN_DB",  "gold_eden",  "tenant_dashboard"),
}


class FreshnessResult(NamedTuple):
    tenant: str
    collection: str
    silver_ts: datetime | None
    gold_ts: datetime | None
    lag_seconds: float | None
    sla_seconds: int
    is_stale: bool


def _get_silver_watermark(pg_dsn: str, tenant: str, table: str) -> datetime | None:
    """Get the Silver watermark for a tenant + table.

    MIN of each ``_s_env``'s own MAX(_s_silver_ts), not the overall MAX — same
    fix the canonical loaders' cursors apply (see
    ``gold.pipeline.cursor_store.get_max_silver_ts_per_env``). Without this, a
    fast-moving env's fresh watermark masks a slow-moving env's genuine
    staleness for the same tenant, since an overall MAX only ever reflects the
    fastest env.
    """
    try:
        from gold.pipeline.cursor_store import get_max_silver_ts_per_env
        ts = get_max_silver_ts_per_env(pg_dsn, table, "_s_tenantid", tenant)
        return ts if ts > datetime(1970, 1, 1, tzinfo=timezone.utc) else None
    except Exception as exc:
        log.debug("silver watermark query failed for %s.%s: %s", tenant, table, exc)
        return None


def _get_gold_watermark(mongo_uri: str, db_name: str,
                         collection: str, tenant: str) -> datetime | None:
    """Get the Gold watermark for a tenant + collection.

    MIN of each ``sourceEnv``'s own MAX(goldUpdatedAt), not the overall MAX —
    mirrors ``_get_silver_watermark``'s per-env MIN-of-MAX fix. A single
    latest-doc lookup (the previous behavior) reflects whichever env wrote
    most recently; if a tenant has an idle/slow env, that env's real Gold
    staleness would never surface because a fast env's fresh write keeps
    re-topping the overall latest.

    Was ``_etlUpdatedAt`` until 2026-07-12, when that field was consolidated into
    ``goldUpdatedAt`` (see gold/pipeline/lineage.py's "Consolidation" note) — both
    always carried the identical value, so this is a rename, not a behavior change.
    """
    try:
        from pymongo import MongoClient
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=3_000,
                             appname="gold-freshness")
        try:
            coll = client[db_name][collection]
            per_env = coll.aggregate([
                {"$match": {"tenantId": tenant}},
                {"$group": {"_id": "$sourceEnv", "maxUpdated": {"$max": "$goldUpdatedAt"}}},
            ])
            vals: list[datetime] = []
            for row in per_env:
                val = row.get("maxUpdated")
                if val is None:
                    continue
                if isinstance(val, str):
                    val = datetime.fromisoformat(val)
                if isinstance(val, datetime):
                    vals.append(val)
            return min(vals) if vals else None
        finally:
            client.close()
    except Exception as exc:
        log.debug("gold watermark query failed for %s.%s: %s", collection, tenant, exc)
    return None


def _get_sla(step_name: str) -> int:
    """Get freshness SLA in seconds from gold_config.yml."""
    try:
        from gold.pipeline.step_registry import get_profile
        profile = get_profile(step_name)
        if profile:
            return int(profile.get("freshness_sla", 900))
    except Exception:
        pass
    return 900  # default 15 min


def check_freshness(
    step_name: str,
    tenant: str,
    pg_dsn: str,
    mongo_uri: str,
) -> FreshnessResult | None:
    """Check freshness for one step + tenant. Returns None if step not tracked."""
    if step_name not in _STEP_TO_COLLECTION:
        return None

    env_var, db_default, collection = _STEP_TO_COLLECTION[step_name]
    db_name    = os.environ.get(env_var, db_default)
    silver_tbl = _COLLECTION_TO_SILVER_TABLE.get(collection, "project")
    sla        = _get_sla(step_name)
    now        = datetime.now(timezone.utc)

    silver_ts = _get_silver_watermark(pg_dsn, tenant, silver_tbl)
    gold_ts   = _get_gold_watermark(mongo_uri, db_name, collection, tenant)

    # Compute lag: how far Gold is behind Silver.
    # Positive = Silver has newer data than Gold (Gold is stale).
    lag_seconds: float | None = None
    if gold_ts and silver_ts:
        # Normalise to UTC
        if gold_ts.tzinfo is None:
            gold_ts = gold_ts.replace(tzinfo=timezone.utc)
        if silver_ts.tzinfo is None:
            silver_ts = silver_ts.replace(tzinfo=timezone.utc)
        lag_seconds = (silver_ts - gold_ts).total_seconds()

    is_stale = lag_seconds is not None and lag_seconds > sla

    result = FreshnessResult(
        tenant=tenant,
        collection=collection,
        silver_ts=silver_ts,
        gold_ts=gold_ts,
        lag_seconds=lag_seconds,
        sla_seconds=sla,
        is_stale=is_stale,
    )

    # Log
    if lag_seconds is None:
        log.debug("freshness unknown | %s.%s tenant=%s — no timestamps found",
                  db_name, collection, tenant)
    elif is_stale:
        log.warning(
            "STALE | %s.%s tenant=%s lag=%.0fs sla=%ds "
            "— Gold is %.1f min behind Silver",
            db_name, collection, tenant, lag_seconds, sla, lag_seconds / 60,
        )
    else:
        log.debug("fresh | %s.%s tenant=%s silver_ahead=%.0fs sla=%ds",
                  db_name, collection, tenant, max(lag_seconds, 0), sla)

    # Emit Prometheus metric
    _emit_metric(tenant, collection, lag_seconds, is_stale)

    return result


# Prometheus gauges are created ONCE at import (a metric may only be registered
# in the default CollectorRegistry a single time). Creating them per-call raised
# a duplicate-registration error that was swallowed by the bare except below, so
# every freshness metric after the first tenant was silently dropped. Holding the
# Gauge objects at module scope and only calling .labels(...).set(...) per-call
# fixes multi-tenant emission.
_lag_gauge = None
_stale_gauge = None


def _freshness_gauges():
    """Lazily build the two freshness gauges once; cache them at module scope.

    Returns (lag_gauge, stale_gauge), or (None, None) if prometheus_client is
    unavailable. Lazy (not import-time) so importing this module never fails and
    so the metrics register in whatever default registry the process set up.
    """
    global _lag_gauge, _stale_gauge
    if _lag_gauge is not None:
        return _lag_gauge, _stale_gauge
    try:
        from prometheus_client import Gauge
        _lag_gauge = Gauge(
            "gold_freshness_lag_seconds",
            "Seconds since Gold collection was last updated for this tenant",
            ["tenant", "collection"],
        )
        _stale_gauge = Gauge(
            "gold_freshness_is_stale",
            "1 if Gold collection is stale (lag > SLA), 0 otherwise",
            ["tenant", "collection"],
        )
    except Exception:
        _lag_gauge, _stale_gauge = None, None
    return _lag_gauge, _stale_gauge


def _emit_metric(
    tenant: str,
    collection: str,
    lag_seconds: float | None,
    is_stale: bool,
) -> None:
    """Emit Prometheus gauge — non-fatal if prometheus_client not installed."""
    try:
        lag_gauge, stale_gauge = _freshness_gauges()
        if lag_gauge is None:
            return
        if lag_seconds is not None:
            lag_gauge.labels(tenant=tenant, collection=collection).set(lag_seconds)
        stale_gauge.labels(tenant=tenant, collection=collection).set(1 if is_stale else 0)
    except Exception as exc:
        log.debug("freshness metric emit failed (non-fatal): %s", exc)


def check_all_freshness(
    tenant: str,
    pg_dsn: str,
    mongo_uri: str,
    failed_steps: list[str],
) -> list[FreshnessResult]:
    """Check freshness for all tracked collections for a tenant.

    Skips steps that already failed (no point checking freshness if load failed).
    Returns list of FreshnessResult — one per collection checked.
    """
    results = []
    stale_count = 0

    for step_name in _STEP_TO_COLLECTION:
        if step_name in failed_steps:
            log.debug("freshness skipped — step failed: %s", step_name)
            continue

        result = check_freshness(step_name, tenant, pg_dsn, mongo_uri)
        if result:
            results.append(result)
            if result.is_stale:
                stale_count += 1

    if stale_count > 0:
        log.warning(
            "freshness summary | tenant=%s stale=%d/%d collections",
            tenant, stale_count, len(results),
        )
    else:
        log.info(
            "freshness summary | tenant=%s all %d collections fresh ✓",
            tenant, len(results),
        )

    return results
