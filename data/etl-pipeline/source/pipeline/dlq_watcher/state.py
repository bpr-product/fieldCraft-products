"""
Watcher state — Redis-backed retry counters and circuit breaker.

Two distinct concerns share one module:

  1. RETRY STATE — per-event state keyed by idempotency_key.
     Hash with TTL 7 days. Fields: attempts, next_retry_at_ms, first_seen_ms,
     last_error, policy_id.

  2. CIRCUIT BREAKER — per-entity sliding window of DLQ ingress timestamps.
     Sorted set keyed by entity, score = epoch_ms. Trim entries older than
     CIRCUIT_BREAKER_WINDOW_MS on every check. Trip when count > threshold.

Both are accessed through the StateStore Protocol. Tests inject InMemoryState;
production uses RedisState.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from dlq_watcher.policies.base import RetryState

log = logging.getLogger(__name__)


RETRY_KEY_TTL_S          = 7 * 24 * 60 * 60
CIRCUIT_BREAKER_WINDOW_MS = 60_000    # 1 minute
CIRCUIT_BREAKER_LIMIT     = int(os.environ.get("DLQ_WATCHER_CB_LIMIT", "100"))


def _retry_key(idempotency_key: str) -> str:
    return f"dlq:retry:{idempotency_key}"


_NO_TENANT_PLACEHOLDER = "_notenant"


def _cb_key(tenant: str, entity: str) -> str:
    return f"dlq:cb:{tenant or _NO_TENANT_PLACEHOLDER}:{entity}"


def _cb_frozen_key(tenant: str, entity: str) -> str:
    return f"dlq:cb:frozen:{tenant or _NO_TENANT_PLACEHOLDER}:{entity}"


@dataclass(frozen=True)
class CircuitBreakerStatus:
    tripped:        bool
    rate_per_min:   int
    frozen_until_ms: int  # 0 when not frozen


@runtime_checkable
class StateStore(Protocol):
    def get_retry_state(self, idempotency_key: str) -> RetryState: ...
    def record_attempt(
        self,
        idempotency_key: str,
        *,
        next_retry_at_ms: int,
        last_error: str,
        policy_id: str,
        first_seen_ms: int,
    ) -> int: ...
    def clear_retry_state(self, idempotency_key: str) -> None: ...
    def circuit_breaker_check(self, entity: str, tenant: str, now_ms: int) -> CircuitBreakerStatus: ...
    def freeze_circuit_breaker(self, entity: str, tenant: str, until_ms: int) -> None: ...
    def unfreeze_circuit_breaker(self, entity: str, tenant: str) -> None: ...


# ── In-memory fake — used in tests ────────────────────────────────────────────

class InMemoryState:
    """Test fake. NOT thread-safe; tests are single-threaded."""

    def __init__(self) -> None:
        self._retry: dict[str, dict] = {}
        self._cb_events: dict[str, list[int]] = {}     # "tenant:entity" → [ts_ms,...]
        self._cb_frozen: dict[str, int] = {}           # "tenant:entity" → until_ms

    def get_retry_state(self, idempotency_key: str) -> RetryState:
        d = self._retry.get(idempotency_key)
        if not d:
            return RetryState()
        return RetryState(
            attempts=int(d.get("attempts", 0)),
            next_retry_at_ms=int(d.get("next_retry_at_ms", 0)),
            first_seen_ms=int(d.get("first_seen_ms", 0)),
            last_error=str(d.get("last_error", "")),
        )

    def record_attempt(self, idempotency_key, *, next_retry_at_ms,
                       last_error, policy_id, first_seen_ms):
        d = self._retry.setdefault(idempotency_key, {})
        d["attempts"]         = int(d.get("attempts", 0)) + 1
        d["next_retry_at_ms"] = next_retry_at_ms
        d["last_error"]       = last_error
        d["policy_id"]        = policy_id
        d["first_seen_ms"]    = d.get("first_seen_ms") or first_seen_ms
        return d["attempts"]

    def clear_retry_state(self, idempotency_key):
        self._retry.pop(idempotency_key, None)

    def circuit_breaker_check(self, entity, tenant, now_ms):
        key = _cb_key(tenant, entity)
        frozen_until = self._cb_frozen.get(key, 0)
        if frozen_until and now_ms < frozen_until:
            return CircuitBreakerStatus(tripped=True, rate_per_min=0, frozen_until_ms=frozen_until)
        # Record this check as an event, prune old entries
        events = self._cb_events.setdefault(key, [])
        events.append(now_ms)
        cutoff = now_ms - CIRCUIT_BREAKER_WINDOW_MS
        self._cb_events[key] = [t for t in events if t >= cutoff]
        rate = len(self._cb_events[key])
        tripped = rate > CIRCUIT_BREAKER_LIMIT
        return CircuitBreakerStatus(tripped=tripped, rate_per_min=rate, frozen_until_ms=0)

    def freeze_circuit_breaker(self, entity, tenant, until_ms):
        self._cb_frozen[_cb_key(tenant, entity)] = until_ms

    def unfreeze_circuit_breaker(self, entity, tenant):
        self._cb_frozen.pop(_cb_key(tenant, entity), None)


# ── Redis-backed implementation — used in production ─────────────────────────

class RedisState:
    """
    Production implementation. Lazy-imports redis so unit tests don't require
    the package to be installed.
    """

    def __init__(self, url: str | None = None) -> None:
        import redis  # noqa: F401 — lazy import; raises clearly if not installed
        url = url or os.environ.get("DLQ_WATCHER_REDIS_URL")
        if not url:
            raise RuntimeError(
                "DLQ_WATCHER_REDIS_URL must be set (e.g. redis://:<password>@host:6379/0)"
            )
        self._r = redis.Redis.from_url(url, decode_responses=True, socket_timeout=5)
        # Ping at construction so we fail fast on misconfiguration.
        self._r.ping()
        log.info("RedisState connected: %s", url.rsplit("@", 1)[-1])

    def get_retry_state(self, idempotency_key):
        d = self._r.hgetall(_retry_key(idempotency_key))
        if not d:
            return RetryState()
        return RetryState(
            attempts=int(d.get("attempts", 0)),
            next_retry_at_ms=int(d.get("next_retry_at_ms", 0)),
            first_seen_ms=int(d.get("first_seen_ms", 0)),
            last_error=str(d.get("last_error", "")),
        )

    def record_attempt(self, idempotency_key, *, next_retry_at_ms,
                       last_error, policy_id, first_seen_ms):
        key = _retry_key(idempotency_key)
        pipe = self._r.pipeline()
        pipe.hincrby(key, "attempts", 1)
        pipe.hset(key, mapping={
            "next_retry_at_ms": next_retry_at_ms,
            "last_error":       last_error[:500],
            "policy_id":        policy_id,
        })
        # first_seen_ms set only if absent
        pipe.hsetnx(key, "first_seen_ms", first_seen_ms)
        pipe.expire(key, RETRY_KEY_TTL_S)
        results = pipe.execute()
        return int(results[0])

    def clear_retry_state(self, idempotency_key):
        self._r.delete(_retry_key(idempotency_key))

    def circuit_breaker_check(self, entity, tenant, now_ms):
        frozen_until = int(self._r.get(_cb_frozen_key(tenant, entity)) or 0)
        if frozen_until and now_ms < frozen_until:
            return CircuitBreakerStatus(tripped=True, rate_per_min=0, frozen_until_ms=frozen_until)
        key = _cb_key(tenant, entity)
        cutoff = now_ms - CIRCUIT_BREAKER_WINDOW_MS
        # uuid4 hex suffix prevents zadd member collisions when two events
        # arrive in the same millisecond (which would silently undercount).
        member = f"{now_ms}-{uuid.uuid4().hex}"
        pipe = self._r.pipeline()
        pipe.zadd(key, {member: now_ms})
        pipe.zremrangebyscore(key, 0, cutoff)
        pipe.zcard(key)
        pipe.expire(key, 120)
        _, _, count, _ = pipe.execute()
        rate = int(count)
        return CircuitBreakerStatus(
            tripped=rate > CIRCUIT_BREAKER_LIMIT,
            rate_per_min=rate,
            frozen_until_ms=0,
        )

    def freeze_circuit_breaker(self, entity, tenant, until_ms):
        ttl_s = max(1, int((until_ms - int(time.time() * 1000)) / 1000) + 60)
        self._r.set(_cb_frozen_key(tenant, entity), until_ms, ex=ttl_s)

    def unfreeze_circuit_breaker(self, entity, tenant):
        self._r.delete(_cb_frozen_key(tenant, entity))
