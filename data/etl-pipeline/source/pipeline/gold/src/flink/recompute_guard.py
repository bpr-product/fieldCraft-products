"""Bounded-retry + quarantine guard for Gold streaming recompute (R5).

A CDC-triggered recompute can fail transiently (Silver/PG blip, Cosmos hiccup)
or permanently (poison data / code bug). The original behavior was to log the
error and drop the trigger silently — no metric, no retry. Two problems:

  * Realtime features skip the batch loaders, so a *transient* failure silently
    lost that project's update until some unrelated future change re-triggered it.
  * A *poison* trigger vanished with no signal — invisible data staleness.

This guard fixes both without changing the recompute-from-current-Silver model:

  * Retries up to GOLD_RECOMPUTE_MAX_RETRIES with exponential backoff by
    re-registering the key's processing-time timer. on_timer is idempotent
    (re-reads current Silver), so a retry is just another fire.
  * Emits a failure metric on every failure and a distinct quarantine metric
    when retries are exhausted — drops become observable, alertable.
  * Never raises. One poison (tenant, project) key is contained; it cannot
    crashloop the whole job and take down every other tenant on it.

Tunables (env):
  GOLD_RECOMPUTE_MAX_RETRIES       default 3   (attempts before quarantine)
  GOLD_RECOMPUTE_RETRY_BACKOFF_MS  default 2000 (base; doubles per attempt)
"""

from __future__ import annotations

import os
from typing import Any, Callable

MAX_RETRIES = int(os.environ.get("GOLD_RECOMPUTE_MAX_RETRIES", "3"))
RETRY_BACKOFF_MS = int(os.environ.get("GOLD_RECOMPUTE_RETRY_BACKOFF_MS", "2000"))


def record_recompute_failure(feature: str, tenant: str, *, quarantined: bool = False) -> None:
    """Best-effort failure counter — never let metrics break the stream.

    Emits gold_loader_failed_total{loader="flink.recompute.<feature>[.quarantine]"}.
    Use quarantined=True for the terminal (retries-exhausted) signal so alerting
    can distinguish a transient blip from a stuck poison key.
    """
    label = f"{feature}.quarantine" if quarantined else feature
    try:
        from gold.src.monitoring.metrics import record_loader_run
        record_loader_run(
            f"flink.recompute.{label}", tenant or "unknown",
            upserted=0, failed=1, elapsed_s=0.0,
        )
    except Exception:  # noqa: BLE001
        pass


def _record_quarantine_audit(feature, tenant, project_id, attempts, exc) -> None:
    """Record a quarantined recompute to gold_audit.gold_event (dead-letter-as-audit).

    Reuses the existing audit DB — no DLQ topic/hub. Best-effort; never raises.
    Only fires on quarantine (rare), so opening a short-lived client is fine.
    """
    try:
        from gold.pipeline.audit import record_stream_event
        from secret_resolver import get_mongo_uri  # type: ignore[import]
        record_stream_event(
            get_mongo_uri(),
            tenant_id=tenant,
            feature=feature,
            event_type="recompute_quarantined",
            project_id=project_id,
            attempts=attempts,
            error_message=str(exc),
        )
    except Exception:  # noqa: BLE001 — audit is non-fatal
        pass


def recompute_with_retry(
    build_callable: Callable[[], list],
    *,
    fail_state: Any,        # Flink ValueState[int]
    timer_service: Any,     # Flink TimerService
    feature: str,
    tenant: str,
    project_id: str,
    log: Any,
) -> tuple[list, "int | None"]:
    """Run build_callable(); manage retry/quarantine on failure.

    Returns (docs, retry_timer_ts):
      - docs: recomputed documents on success; empty list on failure/quarantine.
      - retry_timer_ts: the processing-time timestamp of the retry timer that was
        registered, or None (success, quarantine, or no retry scheduled). Callers
        must store this in Flink state so process_element can cancel it when the
        next CDC event arrives — without this, retry timers accumulate and fire
        as phantom on_timer calls long after the issue is resolved.

    Never raises. One poison (tenant, project) key is contained; it cannot
    crashloop the whole job and take down every other tenant on it.
    """
    try:
        docs = build_callable()
    except Exception as exc:  # noqa: BLE001 — contain the failure to this key
        attempts = (fail_state.value() or 0) + 1
        record_recompute_failure(feature, tenant)
        if attempts < MAX_RETRIES:
            fail_state.update(attempts)
            backoff = RETRY_BACKOFF_MS * (2 ** (attempts - 1))
            retry_ts = timer_service.current_processing_time() + backoff
            timer_service.register_processing_time_timer(retry_ts)
            log.warning(
                "recompute retry %d/%d | feature=%s tenant=%s project=%s err=%s",
                attempts, MAX_RETRIES, feature, tenant, project_id, exc,
            )
            return [], retry_ts
        else:
            record_recompute_failure(feature, tenant, quarantined=True)
            _record_quarantine_audit(feature, tenant, project_id, attempts, exc)
            log.error(
                "recompute QUARANTINED after %d attempts (trigger skipped, not "
                "retried) | feature=%s tenant=%s project=%s err=%s",
                attempts, feature, tenant, project_id, exc,
            )
            fail_state.clear()
            return [], None
    # success — clear any prior failure streak
    if (fail_state.value() or 0) != 0:
        fail_state.clear()
    return docs, None
