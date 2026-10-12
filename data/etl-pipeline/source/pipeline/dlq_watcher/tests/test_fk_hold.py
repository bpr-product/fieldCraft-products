"""Tests for FkHoldPolicy + FkHoldCorrector + DEFERRED watcher flow."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.dlq.errors import ErrorType, build_envelope

from dlq_watcher.audit import InMemoryAudit
from dlq_watcher.correctors.base import CorrectionOutcome
from dlq_watcher.correctors.fk_hold import FkHoldCorrector, parse_fk_violation
from dlq_watcher.policies.base import Action, RetryState
from dlq_watcher.policies.fk_hold import MAX_ATTEMPTS, FkHoldPolicy
from dlq_watcher.state import InMemoryState
from dlq_watcher.watcher import WatcherConfig, process_envelope


# ── parse_fk_violation ───────────────────────────────────────────────────────

_PG_ERR = (
    'insert or update on table "task" violates foreign key constraint '
    '"task_project_id_fkey"\nDETAIL:  Key (project_id)=(42) is not present in '
    'table "project".'
)


def test_parse_extracts_column_value_parent():
    col, val, parent = parse_fk_violation(_PG_ERR)
    assert col == "project_id"
    assert val == "42"
    assert parent == "project"


def test_parse_handles_compound_key_value_pair():
    msg = 'Key (project_id, phase_id)=(42, 7) is not present in table "phase".'
    col, val, parent = parse_fk_violation(msg)
    assert col == "project_id, phase_id"
    assert val == "42, 7"
    assert parent == "phase"


def test_parse_returns_none_on_unrecognised_message():
    col, val, parent = parse_fk_violation("some unrelated error")
    assert (col, val, parent) == (None, None, None)


def test_parse_returns_none_on_empty():
    assert parse_fk_violation("") == (None, None, None)


# ── Policy ───────────────────────────────────────────────────────────────────

class TestFkHoldPolicy:
    policy = FkHoldPolicy()

    def test_first_attempt_corrects(self):
        d = self.policy.evaluate({}, RetryState(attempts=0), now_ms=1)
        assert d.action is Action.CORRECT

    def test_within_limit_keeps_correcting(self):
        d = self.policy.evaluate({}, RetryState(attempts=MAX_ATTEMPTS - 1), now_ms=1)
        assert d.action is Action.CORRECT

    def test_exhausted_parks(self):
        d = self.policy.evaluate({}, RetryState(attempts=MAX_ATTEMPTS), now_ms=1)
        assert d.action is Action.PARK
        assert "parent never landed" in d.reason


# ── Corrector with fake silver-pg ────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, parent_rows, executed_log):
        self._parent_rows = parent_rows
        self._log = executed_log

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params):
        self._log.append((sql, params))

    def fetchone(self):
        return self._parent_rows[0] if self._parent_rows else None


class _FakeConn:
    def __init__(self, parent_rows):
        self._parent_rows = parent_rows
        self.executed: list = []

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def cursor(self):
        return _FakeCursor(self._parent_rows, self.executed)


def _silver_factory(parent_present: bool):
    def factory():
        return _FakeConn([(1,)] if parent_present else [])
    return factory


def _env_with_fk_error():
    return {
        "entity":   "task",
        "tenantid": "tn1",
        "lsn":      99,
        "error_message": _PG_ERR,
    }


def test_corrector_applied_when_parent_now_exists():
    c = FkHoldCorrector(silver_pg_factory=_silver_factory(parent_present=True))
    result = c.correct(_env_with_fk_error())
    assert result.outcome is CorrectionOutcome.APPLIED
    assert "now present" in result.reason


def test_corrector_deferred_when_parent_still_missing():
    c = FkHoldCorrector(silver_pg_factory=_silver_factory(parent_present=False))
    result = c.correct(_env_with_fk_error())
    assert result.outcome is CorrectionOutcome.DEFERRED
    assert "still missing" in result.reason


def test_corrector_refuses_when_error_message_unparseable():
    c = FkHoldCorrector(silver_pg_factory=_silver_factory(parent_present=False))
    result = c.correct({"entity": "task", "tenantid": "tn1",
                        "error_message": "something_unrelated"})
    assert result.outcome is CorrectionOutcome.REFUSED


def test_corrector_refuses_unsafe_identifiers():
    """SQL-injection guard: bad parent_table identifier must raise."""
    c = FkHoldCorrector(silver_pg_factory=_silver_factory(parent_present=False))
    env = {
        "entity":   "task",
        "tenantid": "tn1",
        "error_message": 'Key (id)=(1) is not present in table "x; DROP TABLE silver.task; --".',
    }
    result = c.correct(env)
    # parse_fk_violation will pick up the malformed identifier; corrector raises
    # ValueError → outcome FAILED (caught inside correct()).
    assert result.outcome is CorrectionOutcome.FAILED


# ── End-to-end watcher with DEFERRED ─────────────────────────────────────────

def _cfg(mode="active"):
    return WatcherConfig(
        mode=mode,
        parking_topic_fn=lambda e: f"local.public.{e}.dlq.parking",
        source_topic_fn=lambda e: f"local.public.{e}",
        dlq_topic_fn=lambda e: f"local.public.{e}.dlq",
    )


def _recorder():
    sent: list[tuple[str, str]] = []
    def publish(t, p): sent.append((t, p))
    return publish, sent


def _env_fk_dlq():
    return build_envelope(
        error_type=ErrorType.FK_VIOLATION,
        error_message=_PG_ERR,
        entity="task", raw='{"id":1,"project_id":42,"tenantid":"tn1","op":"c"}',
        tenantid="tn1", lsn=99,
        sqlstate="23503", column_name="project_id",
        now_ms=1_000_000,
    )


def test_e2e_deferred_republishes_to_dlq_and_does_not_replay_to_source():
    state, audit = InMemoryState(), InMemoryAudit()
    publish, sent = _recorder()
    cor = FkHoldCorrector(silver_pg_factory=_silver_factory(parent_present=False))
    process_envelope(
        envelope_json=_env_fk_dlq(),
        policies={"FK_VIOLATION": FkHoldPolicy()},
        correctors={"FK_VIOLATION": cor},
        state=state, audit=audit, publish=publish, cfg=_cfg("active"),
        now_ms=2_000_000,
    )
    # Must NOT publish to source topic — parent missing
    assert not any(t == "local.public.task" for t, _ in sent)
    # Must republish to DLQ topic for later reconsideration
    dlq_publishes = [(t, p) for t, p in sent if t == "local.public.task.dlq"]
    assert len(dlq_publishes) == 1
    assert audit.records[-1].outcome == "deferred"


def test_e2e_applied_when_parent_lands_replays_original_to_source():
    state, audit = InMemoryState(), InMemoryAudit()
    publish, sent = _recorder()
    cor = FkHoldCorrector(silver_pg_factory=_silver_factory(parent_present=True))
    process_envelope(
        envelope_json=_env_fk_dlq(),
        policies={"FK_VIOLATION": FkHoldPolicy()},
        correctors={"FK_VIOLATION": cor},
        state=state, audit=audit, publish=publish, cfg=_cfg("active"),
        now_ms=2_000_000,
    )
    # Replayed to source topic
    src_publishes = [p for t, p in sent if t == "local.public.task"]
    assert len(src_publishes) == 1
    assert audit.records[-1].outcome == "replayed"
