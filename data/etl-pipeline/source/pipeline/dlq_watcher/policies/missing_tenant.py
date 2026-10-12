"""
MISSING_TENANT_ID policy — defers IO to MissingTenantCorrector.

Triggered when the Silver normalizer cannot find a tenant id in the payload.
Two attempts: the first triggers correction (search nested keys, patch, replay).
If that fails (no derivable tenant), park — auto-guessing the tenant for an
ambiguous event would violate tenant isolation.
"""
from __future__ import annotations

from dlq_watcher.policies.base import Action, Decision, Policy, RetryState

MAX_ATTEMPTS = 2


class MissingTenantPolicy(Policy):
    policy_id = "missing_tenant.v1"

    def evaluate(self, envelope: dict, state: RetryState, now_ms: int) -> Decision:
        if state.attempts >= MAX_ATTEMPTS:
            return Decision(
                action=Action.PARK,
                reason=f"missing_tenant correction failed {state.attempts} times — parking",
                policy_id=self.policy_id,
            )
        return Decision(
            action=Action.CORRECT,
            reason=f"missing tenant id (attempt {state.attempts + 1}/{MAX_ATTEMPTS})",
            policy_id=self.policy_id,
        )
