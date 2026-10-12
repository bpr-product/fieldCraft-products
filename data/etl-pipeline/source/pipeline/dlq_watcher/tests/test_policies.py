"""Pure-logic tests for PG_TRANSIENT and LATE_ARRIVAL policies."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dlq_watcher.policies.base import Action, RetryState
from dlq_watcher.policies.transient import PgTransientPolicy
from dlq_watcher.policies.late_arrival import LateArrivalPolicy


def _env(error_type: str, **overrides) -> dict:
    base = {
        "error_type": error_type,
        "entity": "invoice",
        "tenantid": "tn1",
        "lsn": 100,
        "raw_truncated": '{"id":1}',
        "routed_at_ms": 1_000,
        "first_seen_ms": 1_000,
    }
    base.update(overrides)
    return base


# ── PG_TRANSIENT ─────────────────────────────────────────────────────────────

class TestPgTransientPolicy:
    policy = PgTransientPolicy()

    def test_first_attempt_replays_immediately(self):
        d = self.policy.evaluate(_env("PG_TRANSIENT"), RetryState(attempts=0), now_ms=10_000)
        assert d.action is Action.REPLAY
        assert d.delay_ms == 0
        assert "attempt 1" in d.reason

    def test_second_attempt_waits_with_backoff(self):
        d = self.policy.evaluate(_env("PG_TRANSIENT"), RetryState(attempts=1, next_retry_at_ms=20_000), now_ms=10_000)
        assert d.action is Action.WAIT
        assert d.delay_ms == 10_000  # wait 10s more

    def test_when_backoff_elapsed_replays(self):
        d = self.policy.evaluate(_env("PG_TRANSIENT"), RetryState(attempts=1, next_retry_at_ms=10_000), now_ms=15_000)
        assert d.action is Action.REPLAY

    def test_exhausted_attempts_parks(self):
        d = self.policy.evaluate(_env("PG_TRANSIENT"), RetryState(attempts=10), now_ms=999_999)
        assert d.action is Action.PARK
        assert "exhausted" in d.reason.lower()

    @pytest.mark.parametrize("attempts,expected_ms", [
        (1, 5_000),      # 5s
        (2, 10_000),     # 10s
        (3, 20_000),     # 20s
        (4, 40_000),     # 40s
        (5, 80_000),     # 80s
        (6, 160_000),    # 160s
        (7, 300_000),    # capped at 5 min
        (8, 300_000),
        (9, 300_000),
    ])
    def test_backoff_curve_caps_at_5_min(self, attempts, expected_ms):
        # Probe the backoff via a "just-replayed" record: prior next_retry has elapsed,
        # so we expect REPLAY now and the next WAIT after this attempt to use expected_ms.
        # We test the curve directly via the helper.
        assert self.policy._backoff_ms(attempts) == expected_ms

    def test_policy_id_is_stable(self):
        assert self.policy.policy_id == "pg_transient.v1"


# ── LATE_ARRIVAL ─────────────────────────────────────────────────────────────

class TestLateArrivalPolicy:
    policy = LateArrivalPolicy()

    def test_first_attempt_replays_immediately(self):
        d = self.policy.evaluate(_env("LATE_ARRIVAL"), RetryState(attempts=0), now_ms=10_000)
        assert d.action is Action.REPLAY
        assert d.delay_ms == 0

    def test_exhausted_after_5_attempts_parks(self):
        d = self.policy.evaluate(_env("LATE_ARRIVAL"), RetryState(attempts=5), now_ms=999_999)
        assert d.action is Action.PARK

    def test_wait_when_backoff_not_elapsed(self):
        d = self.policy.evaluate(_env("LATE_ARRIVAL"), RetryState(attempts=2, next_retry_at_ms=1_000_000), now_ms=500_000)
        assert d.action is Action.WAIT
        assert d.delay_ms == 500_000

    @pytest.mark.parametrize("attempts,expected_ms", [
        (1,    60_000),       # 1 min
        (2,   240_000),       # 4 min
        (3,   960_000),       # 16 min
        (4, 3_600_000),       # capped at 1 hour
        (5, 3_600_000),
    ])
    def test_backoff_curve_caps_at_1_hour(self, attempts, expected_ms):
        assert self.policy._backoff_ms(attempts) == expected_ms

    def test_policy_id_is_stable(self):
        assert self.policy.policy_id == "late_arrival.v1"
