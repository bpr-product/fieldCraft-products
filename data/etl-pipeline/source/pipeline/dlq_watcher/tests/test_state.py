"""Tests for in-memory StateStore — same semantics expected from RedisState."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dlq_watcher.state import (
    CIRCUIT_BREAKER_LIMIT,
    CIRCUIT_BREAKER_WINDOW_MS,
    CircuitBreakerStatus,
    InMemoryState,
    StateStore,
)


@pytest.fixture
def state() -> StateStore:
    return InMemoryState()


# ── Retry state ──────────────────────────────────────────────────────────────

def test_get_retry_state_returns_defaults_when_unknown(state):
    s = state.get_retry_state("missing-key")
    assert s.attempts == 0
    assert s.next_retry_at_ms == 0
    assert s.first_seen_ms == 0
    assert s.last_error == ""


def test_record_attempt_increments_attempts(state):
    state.record_attempt("k", next_retry_at_ms=2000,
                         last_error="boom", policy_id="p", first_seen_ms=1000)
    state.record_attempt("k", next_retry_at_ms=3000,
                         last_error="boom2", policy_id="p", first_seen_ms=1000)
    assert state.get_retry_state("k").attempts == 2


def test_record_attempt_preserves_first_seen(state):
    state.record_attempt("k", next_retry_at_ms=2000,
                         last_error="x", policy_id="p", first_seen_ms=1000)
    state.record_attempt("k", next_retry_at_ms=200000,
                         last_error="y", policy_id="p", first_seen_ms=99999)
    # first_seen_ms must remain the original value
    assert state.get_retry_state("k").first_seen_ms == 1000


def test_clear_retry_state_resets(state):
    state.record_attempt("k", next_retry_at_ms=2,
                         last_error="x", policy_id="p", first_seen_ms=1)
    state.clear_retry_state("k")
    assert state.get_retry_state("k").attempts == 0


# ── Circuit breaker ──────────────────────────────────────────────────────────

def test_cb_does_not_trip_below_limit(state):
    for i in range(10):
        cb = state.circuit_breaker_check("invoice", tenant="tn-a", now_ms=1_000 + i)
    assert cb.tripped is False
    assert cb.rate_per_min == 10


def test_cb_trips_above_limit(state):
    for i in range(CIRCUIT_BREAKER_LIMIT + 5):
        cb = state.circuit_breaker_check("invoice", tenant="tn-a", now_ms=1_000 + i)
    assert cb.tripped is True
    assert cb.rate_per_min > CIRCUIT_BREAKER_LIMIT


def test_cb_window_slides(state):
    # Burst at t=1000
    for _ in range(CIRCUIT_BREAKER_LIMIT + 5):
        state.circuit_breaker_check("invoice", tenant="tn-a", now_ms=1_000)
    # Far later — old events should be evicted
    cb = state.circuit_breaker_check(
        "invoice", tenant="tn-a", now_ms=1_000 + CIRCUIT_BREAKER_WINDOW_MS + 5_000
    )
    assert cb.tripped is False
    assert cb.rate_per_min == 1  # only the new check itself


def test_cb_per_entity_isolated(state):
    for _ in range(CIRCUIT_BREAKER_LIMIT + 5):
        state.circuit_breaker_check("invoice", tenant="tn-a", now_ms=1_000)
    cb_task = state.circuit_breaker_check("task", tenant="tn-a", now_ms=1_000)
    assert cb_task.tripped is False
    assert cb_task.rate_per_min == 1


def test_cb_per_tenant_isolated(state):
    # Finding 20: one noisy tenant must not freeze the breaker for other tenants
    # of the same entity.
    for _ in range(CIRCUIT_BREAKER_LIMIT + 5):
        state.circuit_breaker_check("invoice", tenant="tn-noisy", now_ms=1_000)
    cb_other = state.circuit_breaker_check("invoice", tenant="tn-quiet", now_ms=1_000)
    assert cb_other.tripped is False
    assert cb_other.rate_per_min == 1


def test_freeze_circuit_breaker(state):
    state.freeze_circuit_breaker("invoice", tenant="tn-a", until_ms=10_000)
    cb = state.circuit_breaker_check("invoice", tenant="tn-a", now_ms=5_000)
    assert cb.tripped is True
    assert cb.frozen_until_ms == 10_000


def test_unfreeze_circuit_breaker(state):
    state.freeze_circuit_breaker("invoice", tenant="tn-a", until_ms=10_000)
    state.unfreeze_circuit_breaker("invoice", tenant="tn-a")
    cb = state.circuit_breaker_check("invoice", tenant="tn-a", now_ms=5_000)
    assert cb.tripped is False


def test_freeze_expires_naturally(state):
    state.freeze_circuit_breaker("invoice", tenant="tn-a", until_ms=10_000)
    cb = state.circuit_breaker_check("invoice", tenant="tn-a", now_ms=11_000)
    # After the freeze window has elapsed, the check should fall through
    # to the sliding-window logic (rate=1 because this is the first event).
    assert cb.frozen_until_ms == 0
    assert cb.tripped is False
