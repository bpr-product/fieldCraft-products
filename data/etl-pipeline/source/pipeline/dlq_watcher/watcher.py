"""
Watcher orchestrator — the testable core of dlq_watcher.

`process_envelope` is a pure-orchestration function:
  envelope JSON → policy lookup → state update → audit write → publish callback.

It does NOT consume from Kafka. The main loop (main.py) wires Kafka to this
function. Keeping the orchestrator IO-free makes the policy-routing logic
unit-testable without a broker.

Watcher modes:
  shadow — REPLAY/CORRECT decisions are logged to audit (outcome='shadow') but
           the publish callback is NOT invoked and correctors are NOT run.
  active — REPLAY decisions invoke the publish callback; CORRECT invokes the
           corrector first, then replays on success.
PARK / WAIT behave identically in both modes.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Callable

from dlq_watcher.audit import AuditRecord, AuditSink
from dlq_watcher.correctors.base import CorrectionOutcome, Corrector
from dlq_watcher.policies.base import Action, Decision, Policy, RetryState
from dlq_watcher.state import StateStore

log = logging.getLogger(__name__)


# Callable signature for publishing back to Kafka.
PublishFn = Callable[[str, str], None]


@dataclass
class WatcherConfig:
    mode:               str           # "shadow" | "active"
    source_topic_fn:    Callable[[str], str]
    dlq_topic_fn:       Callable[[str], str] | None = None  # for DEFERRED republish
    cb_freeze_ms:       int = 5 * 60 * 1_000
    # Deprecated/unused: parking is now AUDIT-ONLY — parked events are recorded in
    # silver._dlq_corrections (which carries the full envelope), not republished to
    # a separate parking hub. This keeps the DLQ family within the Event Hubs
    # 10-hub cap. Field retained as optional so existing callers don't break.
    parking_topic_fn:   Callable[[str], str] | None = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _audit_record(env: dict, *, policy_id: str, outcome: str, attempts: int,
                  error_message: str, now_ms: int,
                  original_value: dict | None = None,
                  corrected_value: dict | None = None) -> AuditRecord:
    return AuditRecord(
        entity=env.get("entity") or "",
        tenantid=env.get("tenantid") or "",
        lsn=env.get("lsn"),
        idempotency_key=env.get("idempotency_key") or "",
        error_type=env.get("error_type") or "",
        sqlstate=env.get("sqlstate"),
        column_name=env.get("column_name"),
        policy_id=policy_id,
        outcome=outcome,
        attempts=attempts,
        error_message=error_message,
        first_seen_ms=int(env.get("first_seen_ms") or now_ms),
        original_value=original_value,
        corrected_value=corrected_value,
    )


def _handle_circuit_breaker(env: dict, state: StateStore, audit: AuditSink,
                            cfg: WatcherConfig, now_ms: int) -> Decision | None:
    """Return a PARK decision if the CB tripped; None if the CB allows continuation."""
    entity = env.get("entity") or ""
    # Scoped per-tenant so one noisy/misbehaving tenant tripping the breaker for
    # this entity doesn't PARK every other tenant's envelopes for the same entity.
    tenant = env.get("tenantid") or "_notenant"
    cb = state.circuit_breaker_check(entity, tenant, now_ms)
    if not cb.tripped:
        return None

    just_tripped = cb.frozen_until_ms == 0
    if just_tripped:
        state.freeze_circuit_breaker(entity, tenant, now_ms + cfg.cb_freeze_ms)
        log.error(
            "Circuit breaker TRIPPED entity=%s tenant=%s rate=%s/min — freezing for %ss",
            entity, tenant, cb.rate_per_min, cfg.cb_freeze_ms // 1000,
        )
        # Only the trip event audits — subsequent events during the same
        # freeze window are suppressed to avoid log/audit spam.
        audit.write(_audit_record(
            env, policy_id="circuit_breaker", outcome="parked",
            attempts=int(env.get("attempts") or 0),
            error_message=f"circuit breaker frozen (rate={cb.rate_per_min}/min)",
            now_ms=now_ms,
        ))
    return Decision(action=Action.PARK, reason="circuit breaker",
                    policy_id="circuit_breaker")


def _handle_no_policy(env: dict, envelope_json: str, audit: AuditSink,
                      publish: PublishFn, cfg: WatcherConfig, now_ms: int) -> Decision:
    """No policy registered for this error_type — park for human review."""
    entity     = env.get("entity") or ""
    error_type = env.get("error_type") or ""
    audit.write(_audit_record(
        env, policy_id="no_policy", outcome="parked",
        attempts=int(env.get("attempts") or 0),
        error_message=f"no policy registered for {error_type}",
        now_ms=now_ms,
    ))
    # Parked = audit-only (recorded above); no parking-hub republish.
    return Decision(action=Action.PARK, reason="no policy", policy_id="no_policy")


def _safe_replay_payload(env: dict) -> str | None:
    """
    Return the payload that is safe to republish to a source/CDC topic, or
    None when only a truncated preview is available.

    `raw_full` (added alongside `raw_truncated` in shared.dlq.errors) is the
    complete, PII-redacted original payload and is the ONLY field REPLAY/
    CORRECT may republish. `raw_truncated` is a 500-char display-only
    preview — envelopes written before `raw_full` existed, or any envelope
    that otherwise lacks it, must never be replayed as if the preview were
    the whole payload (see finding: silent partial-upsert corruption on
    Silver replay of a truncated payload).
    """
    raw_full = env.get("raw_full")
    if raw_full:
        return raw_full
    return None


def _handle_wait(env: dict, decision: Decision, state: RetryState) -> Decision:
    log.info(
        "WAIT entity=%s key=%s attempts=%s delay_ms=%s",
        env.get("entity"), env.get("idempotency_key"),
        state.attempts, decision.delay_ms,
    )
    return decision


def _handle_park(env: dict, envelope_json: str, decision: Decision,
                 state: StateStore, audit: AuditSink, publish: PublishFn,
                 cfg: WatcherConfig, now_ms: int) -> Decision:
    idem_key = env.get("idempotency_key") or ""
    entity   = env.get("entity") or ""
    attempts = state.record_attempt(
        idem_key, next_retry_at_ms=0,
        last_error=decision.reason, policy_id=decision.policy_id,
        first_seen_ms=int(env.get("first_seen_ms") or now_ms),
    )
    audit.write(_audit_record(
        env, policy_id=decision.policy_id, outcome="parked",
        attempts=attempts, error_message=decision.reason, now_ms=now_ms,
    ))
    # Parked = audit-only; no parking-hub republish.
    return decision


def _handle_replay(env: dict, decision: Decision, policy: Policy,
                   state: StateStore, audit: AuditSink, publish: PublishFn,
                   cfg: WatcherConfig, now_ms: int) -> Decision:
    idem_key    = env.get("idempotency_key") or ""
    entity      = env.get("entity") or ""
    # raw_full is the complete, untruncated (PII-redacted) payload — the only
    # field safe to republish. raw_truncated is a 500-char preview and MUST
    # NOT be republished: replaying a truncated payload silently corrupts the
    # Silver upsert (ON CONFLICT DO UPDATE overwrites a complete row with a
    # partial one, and the LSN guard doesn't catch it since the LSN is fine).
    raw_payload = _safe_replay_payload(env)

    retry_state = state.get_retry_state(idem_key)
    # Pre-compute the WAIT-after-this-replay so the next failure knows the wait.
    next_retry_at = now_ms + _next_backoff_ms(policy, retry_state.attempts + 1)

    attempts = state.record_attempt(
        idem_key, next_retry_at_ms=next_retry_at,
        last_error=decision.reason, policy_id=decision.policy_id,
        first_seen_ms=int(env.get("first_seen_ms") or now_ms),
    )

    if raw_payload is None:
        # Full payload unavailable — never republish a known-truncated
        # payload as if it were complete. Force PARK for human review.
        audit.write(_audit_record(
            env, policy_id=decision.policy_id, outcome="parked",
            attempts=attempts,
            error_message="raw_full unavailable — refusing to replay a "
                           "truncated payload (forced PARK)",
            now_ms=now_ms,
        ))
        log.error(
            "REPLAY entity=%s key=%s — raw_full missing/truncated-only, "
            "forcing PARK instead of replay", entity, idem_key,
        )
        return Decision(action=Action.PARK, reason="raw_full unavailable for replay",
                        policy_id=decision.policy_id)

    outcome = "shadow" if cfg.mode == "shadow" else "replayed"
    audit.write(_audit_record(
        env, policy_id=decision.policy_id, outcome=outcome,
        attempts=attempts, error_message=decision.reason, now_ms=now_ms,
    ))

    if cfg.mode == "active" and raw_payload:
        publish(cfg.source_topic_fn(entity), raw_payload)
        log.info("REPLAYED entity=%s key=%s attempts=%s", entity, idem_key, attempts)
    elif cfg.mode == "shadow":
        log.info(
            "SHADOW entity=%s key=%s attempts=%s — would replay (no publish)",
            entity, idem_key, attempts,
        )
    return decision


def _handle_correct(env: dict, envelope_json: str, decision: Decision,
                    corrector: Corrector | None, state: StateStore,
                    audit: AuditSink, publish: PublishFn,
                    cfg: WatcherConfig, now_ms: int) -> Decision:
    """
    Run the corrector for this error_type (active mode only — shadow audits
    intent without invoking IO). On APPLIED/SKIPPED, replay the original
    payload to the source topic. On REFUSED/FAILED, park for human review.
    """
    idem_key   = env.get("idempotency_key") or ""
    entity     = env.get("entity") or ""
    first_seen = int(env.get("first_seen_ms") or now_ms)
    # raw_payload is read AFTER the corrector runs — correctors may mutate
    # env["raw_full"] (e.g. MissingTenantCorrector patches tenantid). Only
    # raw_full is ever republished — see _safe_replay_payload.

    if corrector is None:
        attempts = state.record_attempt(
            idem_key, next_retry_at_ms=0, last_error="no corrector registered",
            policy_id=decision.policy_id, first_seen_ms=first_seen,
        )
        audit.write(_audit_record(
            env, policy_id=decision.policy_id, outcome="parked", attempts=attempts,
            error_message="no corrector registered for CORRECT action",
            now_ms=now_ms,
        ))
        # Parked = audit-only; no parking-hub republish.
        return Decision(action=Action.PARK, reason="no corrector",
                        policy_id=decision.policy_id)

    if cfg.mode == "shadow":
        # Audit intent only — never invoke the corrector's IO in shadow mode.
        attempts = state.record_attempt(
            idem_key, next_retry_at_ms=0, last_error=decision.reason,
            policy_id=decision.policy_id, first_seen_ms=first_seen,
        )
        audit.write(_audit_record(
            env, policy_id=decision.policy_id, outcome="shadow",
            attempts=attempts, error_message=f"would invoke {corrector.corrector_id}",
            now_ms=now_ms,
        ))
        log.info(
            "SHADOW CORRECT entity=%s key=%s — would run %s",
            entity, idem_key, corrector.corrector_id,
        )
        return decision

    # Active mode — actually run the corrector. Read raw_payload AFTER so
    # correctors that patch the payload (e.g. MissingTenantCorrector) have
    # their mutation honoured at replay time.
    result = corrector.correct(env)
    raw_payload = _safe_replay_payload(env)
    next_retry_at = now_ms + 5_000  # short follow-up window if replay fails again
    attempts = state.record_attempt(
        idem_key, next_retry_at_ms=next_retry_at,
        last_error=result.reason, policy_id=decision.policy_id,
        first_seen_ms=first_seen,
    )

    if result.outcome in (CorrectionOutcome.APPLIED, CorrectionOutcome.SKIPPED):
        if raw_payload is None:
            # Corrector says APPLIED/SKIPPED, but there is no full-fidelity
            # payload to republish — never fall back to raw_truncated
            # (would silently corrupt Silver on replay). Force PARK.
            audit.write(_audit_record(
                env, policy_id=decision.policy_id, outcome="parked",
                attempts=attempts,
                error_message=f"{corrector.corrector_id}: {result.reason}; "
                               "raw_full unavailable — refusing to replay a "
                               "truncated payload (forced PARK)",
                original_value=result.original_value,
                corrected_value=result.corrected_value,
                now_ms=now_ms,
            ))
            log.error(
                "CORRECT entity=%s key=%s — raw_full missing/truncated-only "
                "after corrector, forcing PARK instead of replay",
                entity, idem_key,
            )
            return Decision(action=Action.PARK, reason="raw_full unavailable for replay",
                            policy_id=decision.policy_id)

        audit.write(_audit_record(
            env, policy_id=decision.policy_id, outcome="replayed",
            attempts=attempts,
            error_message=f"{corrector.corrector_id}: {result.reason}",
            original_value=result.original_value,
            corrected_value=result.corrected_value,
            now_ms=now_ms,
        ))
        if raw_payload:
            publish(cfg.source_topic_fn(entity), raw_payload)
        log.info(
            "CORRECTED entity=%s key=%s outcome=%s — replayed",
            entity, idem_key, result.outcome.value,
        )
        return decision

    if result.outcome is CorrectionOutcome.DEFERRED:
        # The fix can't apply yet (e.g. FK violation waiting for parent row).
        # Republish the envelope back to the DLQ topic so it gets reconsidered
        # on the next watcher pass. Attempt counter increments via the
        # state.record_attempt call above — eventually exhausts and parks.
        audit.write(_audit_record(
            env, policy_id=decision.policy_id, outcome="deferred",
            attempts=attempts,
            error_message=f"{corrector.corrector_id}: {result.reason}",
            original_value=result.original_value,
            corrected_value=result.corrected_value,
            now_ms=now_ms,
        ))
        if cfg.dlq_topic_fn is not None:
            publish(cfg.dlq_topic_fn(entity), envelope_json)
        log.info(
            "DEFERRED entity=%s key=%s reason=%s — republished to DLQ",
            entity, idem_key, result.reason,
        )
        return decision

    # REFUSED or FAILED — park.
    audit.write(_audit_record(
        env, policy_id=decision.policy_id, outcome="parked",
        attempts=attempts,
        error_message=f"{corrector.corrector_id} {result.outcome.value}: {result.reason}",
        original_value=result.original_value,
        corrected_value=result.corrected_value,
        now_ms=now_ms,
    ))
    # Parked = audit-only; no parking-hub republish.
    log.error(
        "CORRECTOR %s entity=%s key=%s reason=%s — parked",
        result.outcome.value, entity, idem_key, result.reason,
    )
    return Decision(action=Action.PARK, reason=result.reason,
                    policy_id=decision.policy_id)


# ── Main entry point ────────────────────────────────────────────────────────

def process_envelope(
    *,
    envelope_json: str,
    policies: dict[str, Policy],
    state: StateStore,
    audit: AuditSink,
    publish: PublishFn,
    cfg: WatcherConfig,
    correctors: dict[str, Corrector] | None = None,
    now_ms: int | None = None,
) -> Decision | None:
    """
    Process a single DLQ envelope. Returns the Decision (or None when the
    envelope is unparseable — that's a SKIP, logged only).
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    correctors = correctors or {}

    try:
        env = json.loads(envelope_json)
    except Exception:
        log.exception("Unparseable DLQ envelope (SKIP)")
        return None

    error_type = env.get("error_type")
    idem_key   = env.get("idempotency_key") or ""
    if not error_type or not idem_key:
        log.error("Envelope missing error_type or idempotency_key (SKIP): %s", env)
        return None

    cb_decision = _handle_circuit_breaker(env, state, audit, cfg, now)
    if cb_decision is not None:
        return cb_decision

    policy = policies.get(error_type)
    if policy is None:
        return _handle_no_policy(env, envelope_json, audit, publish, cfg, now)

    retry_state = state.get_retry_state(idem_key)
    decision    = policy.evaluate(env, retry_state, now)

    if decision.action is Action.WAIT:
        return _handle_wait(env, decision, retry_state)

    if decision.action is Action.PARK:
        return _handle_park(env, envelope_json, decision, state, audit, publish, cfg, now)

    if decision.action is Action.CORRECT:
        corrector = correctors.get(error_type)
        return _handle_correct(env, envelope_json, decision, corrector,
                               state, audit, publish, cfg, now)

    if decision.action is Action.REPLAY:
        return _handle_replay(env, decision, policy, state, audit, publish, cfg, now)

    # SKIP — log and audit as failed (we couldn't process the envelope).
    audit.write(_audit_record(
        env, policy_id=decision.policy_id, outcome="failed",
        attempts=int(env.get("attempts") or 0),
        error_message=decision.reason, now_ms=now,
    ))
    return decision


def _next_backoff_ms(policy: Policy, attempts: int) -> int:
    """Read the policy's _backoff_ms curve if available, else 0."""
    fn = getattr(policy, "_backoff_ms", None)
    if callable(fn):
        try:
            return int(fn(attempts))
        except Exception:
            log.exception("policy._backoff_ms raised — using 0")
    return 0
