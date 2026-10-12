"""
PG_TRANSIENT policy — exponential backoff retry for transient PG failures.

Triggered by ErrorType.PG_TRANSIENT (SQLSTATE classes 08/40/53/57 and a few
specific codes — see shared/dlq/errors.py).

Behaviour:
  - First DLQ hit: REPLAY immediately.
  - Subsequent failures: WAIT then REPLAY with exponential backoff.
  - After MAX_ATTEMPTS attempts: PARK to <entity>.dlq.parking.

Backoff curve (per attempt N counted from 1):
  5s · 2^(N-1), capped at 5 minutes.
  Caps at 5 min so a multi-hour outage doesn't blow out the curve.
"""
from __future__ import annotations

from dlq_watcher.policies.base import Action, Decision, Policy, RetryState

MAX_ATTEMPTS    = 10
BASE_BACKOFF_MS = 5_000          # 5 seconds
MAX_BACKOFF_MS  = 5 * 60 * 1_000  # 5 minutes


class PgTransientPolicy(Policy):
    policy_id = "pg_transient.v1"

    def _backoff_ms(self, attempts: int) -> int:
        if attempts <= 0:
            return 0
        # 5s, 10s, 20s, 40s, 80s, 160s, then cap
        ms = BASE_BACKOFF_MS * (2 ** (attempts - 1))
        return min(ms, MAX_BACKOFF_MS)

    def evaluate(self, envelope: dict, state: RetryState, now_ms: int) -> Decision:
        attempts = state.attempts

        if attempts >= MAX_ATTEMPTS:
            return Decision(
                action=Action.PARK,
                reason=f"transient retries exhausted after {attempts} attempts",
                policy_id=self.policy_id,
            )

        # First time we've ever seen this event — replay right away.
        if attempts == 0:
            return Decision(
                action=Action.REPLAY,
                reason=f"first attempt (attempt {attempts + 1}/{MAX_ATTEMPTS})",
                policy_id=self.policy_id,
            )

        # Subsequent retries: respect the wait window if it hasn't elapsed.
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
