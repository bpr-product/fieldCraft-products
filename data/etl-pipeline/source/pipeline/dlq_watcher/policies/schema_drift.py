"""
SCHEMA_DRIFT policy — defers IO to SchemaDriftCorrector.

Triggered when Silver upsert fails with SQLSTATE 42703 (undefined_column) or
42P01 (undefined_table). The pure decision logic here is small: try to correct
on the first one or two attempts, then park if the corrector keeps failing.
"""
from __future__ import annotations

from dlq_watcher.policies.base import Action, Decision, Policy, RetryState

MAX_ATTEMPTS = 3


class SchemaDriftPolicy(Policy):
    policy_id = "schema_drift.v1"

    def evaluate(self, envelope: dict, state: RetryState, now_ms: int) -> Decision:
        if state.attempts >= MAX_ATTEMPTS:
            return Decision(
                action=Action.PARK,
                reason=f"schema_drift correction failed {state.attempts} times — parking",
                policy_id=self.policy_id,
            )
        return Decision(
            action=Action.CORRECT,
            reason=f"schema drift detected (attempt {state.attempts + 1}/{MAX_ATTEMPTS})",
            policy_id=self.policy_id,
        )
