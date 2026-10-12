"""
Orchestrator tests — exercise process_envelope() end-to-end with fakes.
No Kafka, no Redis, no PG.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.dlq.errors import ErrorType, build_envelope

from dlq_watcher.audit import InMemoryAudit
from dlq_watcher.policies.base import Action
from dlq_watcher.policies.late_arrival import LateArrivalPolicy
from dlq_watcher.policies.transient import PgTransientPolicy
from dlq_watcher.state import InMemoryState
from dlq_watcher.watcher import WatcherConfig, process_envelope


def _publish_recorder():
    sent = []

    def publish(topic: str, payload: str) -> None:
        sent.append((topic, payload))

    return publish, sent


def _make_env(error_type: ErrorType, *, entity="invoice", tenantid="tn1",
              lsn=42, raw='{"id":1,"tenantid":"tn1","op":"u"}'):
    return build_envelope(
        error_type=error_type,
        error_message="test",
        entity=entity, raw=raw, tenantid=tenantid, lsn=lsn,
        now_ms=1_000_000,
    )


def _cfg(mode="active"):
    return WatcherConfig(
        mode=mode,
        parking_topic_fn=lambda e: f"local.public.{e}.dlq.parking",
        source_topic_fn=lambda e: f"local.public.{e}",
    )


# ── Active mode ──────────────────────────────────────────────────────────────

def test_active_mode_replays_pg_transient_first_attempt():
    state = InMemoryState()
    audit = InMemoryAudit()
    publish, sent = _publish_recorder()
    cfg = _cfg(mode="active")

    decision = process_envelope(
        envelope_json=_make_env(ErrorType.PG_TRANSIENT),
        policies={"PG_TRANSIENT": PgTransientPolicy()},
        state=state, audit=audit, publish=publish, cfg=cfg,
        now_ms=2_000_000,
    )

    assert decision.action is Action.REPLAY
    assert len(sent) == 1
    assert sent[0][0] == "local.public.invoice"
    assert json.loads(sent[0][1])["id"] == 1
    assert len(audit.records) == 1
    assert audit.records[0].outcome == "replayed"
    assert audit.records[0].attempts == 1
    # Retry state recorded the next_retry_at_ms so a subsequent failure waits.
    rs = state.get_retry_state(json.loads(_make_env(ErrorType.PG_TRANSIENT))["idempotency_key"])
    assert rs.attempts == 1
    assert rs.next_retry_at_ms > 2_000_000


def test_active_mode_park_after_exhausted_transient_retries():
    state = InMemoryState()
    audit = InMemoryAudit()
    publish, sent = _publish_recorder()
    cfg = _cfg(mode="active")
    env = _make_env(ErrorType.PG_TRANSIENT)
    key = json.loads(env)["idempotency_key"]

    # Pre-populate state to be at the exhaustion threshold.
    for i in range(10):
        state.record_attempt(key, next_retry_at_ms=0,
                             last_error="x", policy_id="p", first_seen_ms=0)

    decision = process_envelope(
        envelope_json=env,
        policies={"PG_TRANSIENT": PgTransientPolicy()},
        state=state, audit=audit, publish=publish, cfg=cfg,
        now_ms=999_999_999,
    )
    assert decision.action is Action.PARK
    # PARK is audit-only now — recorded in the audit table, no parking-hub publish.
    assert sent == []
    assert audit.records[0].outcome == "parked"


def test_active_mode_wait_does_not_publish():
    state = InMemoryState()
    audit = InMemoryAudit()
    publish, sent = _publish_recorder()
    cfg = _cfg(mode="active")
    env = _make_env(ErrorType.PG_TRANSIENT)
    key = json.loads(env)["idempotency_key"]

    # Set up state so the next decision is WAIT.
    state.record_attempt(key, next_retry_at_ms=9_000_000,
                         last_error="x", policy_id="p", first_seen_ms=1)

    decision = process_envelope(
        envelope_json=env,
        policies={"PG_TRANSIENT": PgTransientPolicy()},
        state=state, audit=audit, publish=publish, cfg=cfg,
        now_ms=2_000_000,
    )
    assert decision.action is Action.WAIT
    assert sent == []
    # WAIT decisions are NOT audited (not terminal).
    assert audit.records == []


# ── Shadow mode ──────────────────────────────────────────────────────────────

def test_shadow_mode_does_not_publish_but_audits_with_shadow_outcome():
    state = InMemoryState()
    audit = InMemoryAudit()
    publish, sent = _publish_recorder()
    cfg = _cfg(mode="shadow")

    decision = process_envelope(
        envelope_json=_make_env(ErrorType.PG_TRANSIENT),
        policies={"PG_TRANSIENT": PgTransientPolicy()},
        state=state, audit=audit, publish=publish, cfg=cfg,
        now_ms=2_000_000,
    )
    assert decision.action is Action.REPLAY
    assert sent == []  # shadow mode: no Kafka publish
    assert audit.records[0].outcome == "shadow"


def test_shadow_mode_park_is_audit_only():
    """Parking is audit-only in both modes — recorded in silver._dlq_corrections,
    never published to a parking hub."""
    state = InMemoryState()
    audit = InMemoryAudit()
    publish, sent = _publish_recorder()
    cfg = _cfg(mode="shadow")
    env = _make_env(ErrorType.PG_TRANSIENT)
    key = json.loads(env)["idempotency_key"]
    for i in range(10):
        state.record_attempt(key, next_retry_at_ms=0,
                             last_error="x", policy_id="p", first_seen_ms=0)

    decision = process_envelope(
        envelope_json=env,
        policies={"PG_TRANSIENT": PgTransientPolicy()},
        state=state, audit=audit, publish=publish, cfg=cfg,
        now_ms=999_999_999,
    )
    assert decision.action is Action.PARK
    assert sent == []
    assert audit.records[-1].outcome == "parked"


# ── No-policy / unknown error_type ───────────────────────────────────────────

def test_unknown_error_type_routes_to_parking():
    state = InMemoryState()
    audit = InMemoryAudit()
    publish, sent = _publish_recorder()
    cfg = _cfg(mode="active")

    decision = process_envelope(
        envelope_json=_make_env(ErrorType.UNKNOWN),
        policies={"PG_TRANSIENT": PgTransientPolicy()},  # no UNKNOWN handler
        state=state, audit=audit, publish=publish, cfg=cfg,
        now_ms=1,
    )
    assert decision.action is Action.PARK
    assert sent == []  # audit-only park, no parking-hub publish
    assert audit.records[0].policy_id == "no_policy"


# ── Circuit breaker ──────────────────────────────────────────────────────────

def test_circuit_breaker_trips_and_skips_replay():
    from dlq_watcher.state import CIRCUIT_BREAKER_LIMIT

    state = InMemoryState()
    audit = InMemoryAudit()
    publish, sent = _publish_recorder()
    cfg = _cfg(mode="active")
    policies = {"PG_TRANSIENT": PgTransientPolicy()}

    # Flood with enough envelopes for the same entity to trip the breaker.
    for i in range(CIRCUIT_BREAKER_LIMIT + 5):
        process_envelope(
            envelope_json=_make_env(ErrorType.PG_TRANSIENT, lsn=i),
            policies=policies, state=state, audit=audit, publish=publish, cfg=cfg,
            now_ms=1_000_000 + i,
        )

    # Most recent decision should be PARK from CB, not REPLAY.
    last = audit.records[-1]
    assert last.policy_id == "circuit_breaker"
    assert last.outcome == "parked"


def test_circuit_breaker_is_per_entity():
    from dlq_watcher.state import CIRCUIT_BREAKER_LIMIT

    state = InMemoryState()
    audit = InMemoryAudit()
    publish, sent = _publish_recorder()
    cfg = _cfg(mode="active")
    policies = {"PG_TRANSIENT": PgTransientPolicy()}

    # Trip CB for invoice
    for i in range(CIRCUIT_BREAKER_LIMIT + 5):
        process_envelope(
            envelope_json=_make_env(ErrorType.PG_TRANSIENT, entity="invoice", lsn=i),
            policies=policies, state=state, audit=audit, publish=publish, cfg=cfg,
            now_ms=1_000_000 + i,
        )
    # task entity remains healthy
    process_envelope(
        envelope_json=_make_env(ErrorType.PG_TRANSIENT, entity="task", lsn=999),
        policies=policies, state=state, audit=audit, publish=publish, cfg=cfg,
        now_ms=2_000_000,
    )
    assert any(r.entity == "task" and r.outcome == "replayed" for r in audit.records)


# ── Unparseable envelope ─────────────────────────────────────────────────────

def test_unparseable_envelope_skips_silently():
    state = InMemoryState()
    audit = InMemoryAudit()
    publish, sent = _publish_recorder()
    cfg = _cfg(mode="active")
    result = process_envelope(
        envelope_json="not-json{",
        policies={}, state=state, audit=audit, publish=publish, cfg=cfg,
        now_ms=1,
    )
    assert result is None
    assert audit.records == []
    assert sent == []


# ── LATE_ARRIVAL works through same code path ────────────────────────────────

def test_late_arrival_replays_first_attempt():
    state = InMemoryState()
    audit = InMemoryAudit()
    publish, sent = _publish_recorder()
    cfg = _cfg(mode="active")
    decision = process_envelope(
        envelope_json=_make_env(ErrorType.LATE_ARRIVAL),
        policies={"LATE_ARRIVAL": LateArrivalPolicy()},
        state=state, audit=audit, publish=publish, cfg=cfg,
        now_ms=2_000_000,
    )
    assert decision.action is Action.REPLAY
    assert len(sent) == 1
