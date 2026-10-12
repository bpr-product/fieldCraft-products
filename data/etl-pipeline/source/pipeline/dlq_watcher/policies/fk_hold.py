"""
FK_VIOLATION policy — defers IO to FkHoldCorrector.

Triggered when Silver upsert fails with SQLSTATE 23503 (foreign_key_violation).
The corrector decides whether the parent row exists. The policy only manages
how many times we try before giving up.

Two independent park conditions — whichever fires first:
  - MAX_ATTEMPTS exceeded (attempt-based)
  - FK_HOLD_TIMEOUT_MS elapsed since first_seen_ms (wall-clock based)

The wall-clock guard prevents events from sitting in the hold queue
indefinitely when the parent row was deleted before it ever reached Silver.
"""
from __future__ import annotations

import os

from dlq_watcher.policies.base import Action, Decision, Policy, RetryState

MAX_ATTEMPTS       = 30   # ~30 redeliveries × backoff → ample window for parent to land
FK_HOLD_TIMEOUT_MS = int(os.environ.get("FK_HOLD_TIMEOUT_MS", str(30 * 60 * 1000)))  # default 30 min


class FkHoldPolicy(Policy):
    policy_id = "fk_hold.v1"

    def evaluate(self, envelope: dict, state: RetryState, now_ms: int) -> Decision:
        if state.attempts >= MAX_ATTEMPTS:
            return Decision(
                action=Action.PARK,
                reason=f"fk_hold exhausted after {state.attempts} attempts — parent never landed",
                policy_id=self.policy_id,
            )

        if state.first_seen_ms and (now_ms - state.first_seen_ms) >= FK_HOLD_TIMEOUT_MS:
            elapsed_min = (now_ms - state.first_seen_ms) // 60_000
            return Decision(
                action=Action.PARK,
                reason=(
                    f"fk_hold timeout after {elapsed_min}m "
                    f"(FK_HOLD_TIMEOUT_MS={FK_HOLD_TIMEOUT_MS}) — parent never landed"
                ),
                policy_id=self.policy_id,
            )

        return Decision(
            action=Action.CORRECT,
            reason=f"fk violation — probing parent (attempt {state.attempts + 1}/{MAX_ATTEMPTS})",
            policy_id=self.policy_id,
        )
