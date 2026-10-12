"""
Data pipeline control-plane API — health, pipeline status, and DLQ circuit-breaker
inspection over the Bronze/Silver/External data pipeline (source.pipeline.*).

The pipeline itself is Flink/Kafka batch+streaming jobs, not request/response —
this router is the same "thin FastAPI control plane over background workers"
shape as every other service's public/protected router split (see bpe_router /
bpe_public_router in src/bpe/source/bpe/router.py). Protected routes stack the
same three guards as every other protected router in this repo.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException

from source.security.api_key_guard import verify_api_key
from source.security.bearer_token_guard import verify_bearer_token
from source.security.fieldcraft_token_guard import require_fieldcraft_claims

logger = logging.getLogger(__name__)

ETL_SECURITY_DEPS = [
    Depends(verify_api_key),
    Depends(verify_bearer_token),
    Depends(require_fieldcraft_claims),
]

# Public — no auth, matches every other service's health convention.
etl_public_router = APIRouter(tags=["health"])

# Protected — pipeline status / DLQ inspection.
etl_router = APIRouter(prefix="/api/v1/etl", tags=["etl"], dependencies=ETL_SECURITY_DEPS)


@etl_public_router.get("/health")
def health() -> dict:
    return {"status": "healthy", "service": "etl"}


@etl_router.get("/status")
def status() -> dict:
    """Static pipeline configuration summary — env, thresholds, connector names.

    Does not probe Kafka/Postgres/Redis directly (that's cdc_monitor.py /
    pipeline_health_check.py, run as scheduled jobs, not request-time work).
    """
    from shared import pipeline_config as cfg

    return {
        "pg_schema": cfg.PG_SCHEMA,
        "replication_slots": cfg.REPLICATION_SLOTS,
        "silver_freshness_warn_s": cfg.SILVER_FRESHNESS_WARN_S,
        "silver_freshness_crit_s": cfg.SILVER_FRESHNESS_CRIT_S,
        "slot_lag_warn_bytes": cfg.SLOT_LAG_WARN_BYTES,
        "slot_lag_crit_bytes": cfg.SLOT_LAG_CRIT_BYTES,
    }


@etl_router.get("/dlq/circuit-breaker/{entity}")
def circuit_breaker_status(entity: str, tenant: str | None = None) -> dict:
    """Query the dlq_watcher circuit-breaker state for one entity.

    `tenant` is optional — the circuit breaker is keyed per (tenant, entity);
    omitting it checks the shared "no tenant" bucket that `state.py` uses for
    untenanted DLQ events (see `_NO_TENANT_PLACEHOLDER`).

    Fails soft with 503 (not a crash) when Redis is unreachable/unconfigured —
    matches this repo's "audit/monitoring never takes down the request path"
    convention (see BPE's audit_mongo_writer, guardrails' AuditLogger).
    """
    from dlq_watcher.state import RedisState

    try:
        state = RedisState()
        cb = state.circuit_breaker_check(entity, tenant or "", now_ms=int(time.time() * 1000))
    except Exception as e:
        logger.warning("dlq_watcher RedisState unavailable: %s", e)
        raise HTTPException(status_code=503, detail="dlq_watcher state store unavailable") from e

    return {
        "entity": entity,
        "tenant": tenant,
        "tripped": cb.tripped,
        "rate_per_min": cb.rate_per_min,
        "frozen_until_ms": cb.frozen_until_ms,
    }
