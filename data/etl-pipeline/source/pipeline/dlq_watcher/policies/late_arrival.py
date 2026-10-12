"""
LATE_ARRIVAL policy — slow backoff for events that arrived past the late-event
threshold. The Silver job (universal_silver_merge.py) treats wall-clock age vs
SILVER_LATE_EVENT_THRESHOLD_MS as the trigger.

Triggered by ErrorType.LATE_ARRIVAL.

Behaviour:
  - First DLQ hit: REPLAY immediately. The threshold may have been set too low,
    or the Silver consumer simply lagged behind the producer.
  - Subsequent failures: WAIT with a slower curve than transient — late events
    are not urgent, but we still want to drain them before parking.
  - After MAX_ATTEMPTS attempts: PARK.

Backoff curve (per attempt N counted from 1):
  60s · 4^(N-1), capped at 1 hour.
  → 1 min, 4 min, 16 min, 60 min, 60 min...
"""
from __future__ import annotations

from dlq_watcher.policies.base import Action, Decision, Policy, RetryState

MAX_ATTEMPTS    = 5
BASE_BACKOFF_MS = 60_000           # 1 minute
MAX_BACKOFF_MS  = 60 * 60 * 1_000  # 1 hour


class LateArrivalPolicy(Policy):
    policy_id = "late_arrival.v1"

    def _backoff_ms(self, attempts: int) -> int:
        if attempts <= 0:
            return 0
        ms = BASE_BACKOFF_MS * (4 ** (attempts - 1))
        return min(ms, MAX_BACKOFF_MS)

    def evaluate(self, envelope: dict, state: RetryState, now_ms: int) -> Decision:
        attempts = state.attempts

        if attempts >= MAX_ATTEMPTS:
            return Decision(
                action=Action.PARK,
                reason=f"late_arrival retries exhausted after {attempts} attempts",
                policy_id=self.policy_id,
            )

        if attempts == 0:
            return Decision(
                action=Action.REPLAY,
                reason=f"first attempt (attempt {attempts + 1}/{MAX_ATTEMPTS})",
                policy_id=self.policy_id,
            )

        if now_ms < state.next_retry_at_ms:
            return Decision(
                action=Action.WAIT,
                reason=f"backoff (attempt {attempts + 1}/{MAX_ATTEMPTS})",
                policy_id=self.policy_id,
                delay_ms=state.next_retry_at_ms - now_ms,
            )

        return Decision(
            action=Action.REPLAY,
            reason=f"backoff elapsed (attempt {attempts + 1}/{MAX_ATTEMPTS})",
            policy_id=self.policy_id,
        )
