"""Simple in-process circuit breaker for Gold batch loaders.

Prevents retry storms when Cosmos DB is unavailable.
State: CLOSED (normal) → OPEN (failing) → HALF_OPEN (testing recovery)

Usage:
    from gold.src.monitoring.circuit_breaker import CircuitBreaker, CircuitOpenError

    cb = CircuitBreaker(name="gold_bpe", failure_threshold=5, recovery_timeout_s=60)

    try:
        cb.call(lambda: coll.bulk_write(ops))
    except CircuitOpenError:
        log.warning("circuit open — skipping write, will retry after %ds", cb.recovery_timeout_s)

Env vars:
    GOLD_CB_FAILURE_THRESHOLD   default 5  — consecutive failures before opening
    GOLD_CB_RECOVERY_TIMEOUT_S  default 60 — seconds before half-open probe
"""

from __future__ import annotations

import logging
import os
import time
from enum import Enum
from threading import Lock
from typing import Callable, TypeVar

log = logging.getLogger("gold.circuit_breaker")

T = TypeVar("T")

_DEFAULT_THRESHOLD = int(os.environ.get("GOLD_CB_FAILURE_THRESHOLD", "5"))
_DEFAULT_RECOVERY  = int(os.environ.get("GOLD_CB_RECOVERY_TIMEOUT_S", "60"))


class CircuitState(Enum):
    CLOSED    = "closed"      # normal operation
    OPEN      = "open"        # failing — reject calls
    HALF_OPEN = "half_open"   # probe — allow one call


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the circuit is open."""


class CircuitBreaker:
    """Thread-safe circuit breaker for Gold Cosmos DB writes."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = _DEFAULT_THRESHOLD,
        recovery_timeout_s: int = _DEFAULT_RECOVERY,
    ) -> None:
        self.name               = name
        self.failure_threshold  = failure_threshold
        self.recovery_timeout_s = recovery_timeout_s
        self._state             = CircuitState.CLOSED
        self._failure_count     = 0
        self._last_failure_time = 0.0
        self._lock              = Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if time.monotonic() - self._last_failure_time >= self.recovery_timeout_s:
                    self._state = CircuitState.HALF_OPEN
                    log.info("circuit[%s] → HALF_OPEN (probing recovery)", self.name)
            return self._state

    def call(self, fn: Callable[[], T]) -> T:
        """Execute fn through the circuit breaker."""
        state = self.state
        if state == CircuitState.OPEN:
            raise CircuitOpenError(
                f"circuit[{self.name}] is OPEN — skipping call "
                f"(will probe in {self.recovery_timeout_s}s)"
            )
        try:
            result = fn()
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure(exc)
            raise

    def _on_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                log.info("circuit[%s] → CLOSED (recovery confirmed)", self.name)
            self._state         = CircuitState.CLOSED
            self._failure_count = 0

    def _on_failure(self, exc: Exception) -> None:
        with self._lock:
            self._failure_count    += 1
            self._last_failure_time = time.monotonic()
            if self._failure_count >= self.failure_threshold or self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                log.error(
                    "circuit[%s] → OPEN after %d failures (last: %s) "
                    "— writes suspended for %ds",
                    self.name, self._failure_count, exc, self.recovery_timeout_s,
                )

    def reset(self) -> None:
        """Manually reset to CLOSED — use after fixing root cause."""
        with self._lock:
            self._state         = CircuitState.CLOSED
            self._failure_count = 0
            log.info("circuit[%s] manually reset to CLOSED", self.name)
