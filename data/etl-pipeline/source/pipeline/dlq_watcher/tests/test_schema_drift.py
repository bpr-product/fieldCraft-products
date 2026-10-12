"""Tests for SchemaDriftPolicy + SchemaDriftCorrector + CORRECT watcher flow."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.dlq.errors import ErrorType, build_envelope

from dlq_watcher.audit import InMemoryAudit
from dlq_watcher.correctors.base import CorrectionOutcome, CorrectionResult
from dlq_watcher.correctors.schema_drift import SchemaDriftCorrector
from dlq_watcher.policies.base import Action, RetryState
from dlq_watcher.policies.schema_drift import MAX_ATTEMPTS, SchemaDriftPolicy
from dlq_watcher.state import InMemoryState
from dlq_watcher.watcher import WatcherConfig, process_envelope


# ── Policy ───────────────────────────────────────────────────────────────────

class TestSchemaDriftPolicy:
    policy = SchemaDriftPolicy()

    def test_first_attempt_corrects(self):
        d = self.policy.evaluate({}, RetryState(attempts=0), now_ms=1)
        assert d.action is Action.CORRECT

    def test_within_max_attempts_keeps_correcting(self):
        d = self.policy.evaluate({}, RetryState(attempts=MAX_ATTEMPTS - 1), now_ms=1)
        assert d.action is Action.CORRECT

    def test_exhausted_attempts_park(self):
        d = self.policy.evaluate({}, RetryState(attempts=MAX_ATTEMPTS), now_ms=1)
        assert d.action is Action.PARK
        assert "failed" in d.reason.lower()

    def test_policy_id_is_stable(self):
        assert self.policy.policy_id == "schema_drift.v1"


# ── Corrector fakes ──────────────────────────────────────────────────────────

class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, tuple[str, int]] = {}     # key → (value, expiry-counter)
        self._locks_taken: list[str] = []

    def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.store:
            return False
        self.store[key] = (str(value), ex or 0)
        if "ddl-lock" in key:
            self._locks_taken.append(key)
        return True

    def delete(self, key):
        self.store.pop(key, None)


class _FakeCursor:
    def __init__(self, executed_log, fetch_rows):
        self._executed = executed_log
        self._fetch = fetch_rows
        self._idx = 0

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params=None):
        self._executed.append((sql, params))

    def fetchall(self):
        # Returns the next pre-queued row set
        rows = self._fetch[self._idx] if self._idx < len(self._fetch) else []
        self._idx += 1
        return rows


class _FakeConn:
    """psycopg2-like context-manager connection."""
    def __init__(self, fetch_rows):
        self.executed: list[tuple] = []
        self._fetch_rows = fetch_rows
        self.autocommit = False

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def cursor(self):
        return _FakeCursor(self.executed, self._fetch_rows)


def _src_factory(rows):
    """Returns a callable that yields a fresh fake conn each call."""
    def factory():
        return _FakeConn([rows])
    return factory


def _silver_factory(rows, alter_log):
    """Silver factory: first call returns info_schema query, later calls run ALTERs."""
    call_count = {"n": 0}

    def factory():
        n = call_count["n"]
        call_count["n"] += 1
        if n == 0:
            return _FakeConn([rows])   # info_schema fetch
        # ALTER pass — empty fetch, but record executed statements
        conn = _FakeConn([[]])
        conn.executed = alter_log
        return conn
    return factory


def _make_env(error_type=ErrorType.SCHEMA_DRIFT, entity="invoice", tenantid="tn1",
              raw='{"id":1,"tenantid":"tn1","op":"u","note":"hello"}'):
    return build_envelope(
        error_type=error_type,
        error_message="undefined_column note",
        entity=entity, raw=raw, tenantid=tenantid, lsn=10,
        sqlstate="42703", column_name="note",
        now_ms=1_000_000,
    )


# ── Corrector ────────────────────────────────────────────────────────────────

def test_corrector_refuses_when_entity_empty():
    redis = _FakeRedis()
    c = SchemaDriftCorrector(
        source_pg_factory=_src_factory([]),
        silver_pg_factory=_src_factory([]),
        redis_client=redis,
    )
    result = c.correct({"entity": ""})
    assert result.outcome is CorrectionOutcome.REFUSED


def test_corrector_ddl_lock_busy_returns_skipped():
    redis = _FakeRedis()
    redis.store["dlq:ddl-lock:invoice"] = ("locked", 60)  # pre-existing lock
    c = SchemaDriftCorrector(
        source_pg_factory=_src_factory([]),
        silver_pg_factory=_src_factory([]),
        redis_client=redis,
    )
    result = c.correct({"entity": "invoice"})
    assert result.outcome is CorrectionOutcome.SKIPPED
    assert "busy" in result.reason.lower()


def test_corrector_skipped_when_no_diff():
    """Silver already has all source columns — corrector reports SKIPPED."""
    # Both sides return the same row shape from information_schema query.
    rows = [("id", "integer", "NO"), ("name", "text", "YES")]
    redis = _FakeRedis()
    c = SchemaDriftCorrector(
        source_pg_factory=_src_factory(rows),
        silver_pg_factory=_src_factory(rows),
        redis_client=redis,
    )
    result = c.correct({"entity": "invoice", "tenantid": "tn1"})
    assert result.outcome is CorrectionOutcome.SKIPPED


def test_corrector_applies_when_columns_missing():
    src_rows    = [("id", "integer", "NO"), ("name", "text", "YES"), ("note", "text", "YES")]
    silver_rows = [("id", "integer", "NO"), ("name", "text", "YES")]
    alter_log: list[tuple] = []
    redis = _FakeRedis()
    c = SchemaDriftCorrector(
        source_pg_factory=_src_factory(src_rows),
        silver_pg_factory=_silver_factory(silver_rows, alter_log),
        redis_client=redis,
    )
    result = c.correct({"entity": "invoice", "tenantid": "tn1"})
    assert result.outcome is CorrectionOutcome.APPLIED
    # Exactly one ALTER (for `note`) executed
    altered_sql = [sql for sql, _ in alter_log if "ALTER TABLE" in sql]
    assert len(altered_sql) == 1
    assert "ADD COLUMN IF NOT EXISTS note" in altered_sql[0]
    # Cache-bust signal set
    assert "silver:schema:invalidate:invoice" in redis.store


# ── Watcher CORRECT flow ─────────────────────────────────────────────────────

class _StubCorrector:
    corrector_id = "schema_drift.v1"
    def __init__(self, outcome):
        self._outcome = outcome
        self.called = 0
    def correct(self, envelope):
        self.called += 1
        return CorrectionResult(outcome=self._outcome, reason="stub",
                                original_value={"a": 1}, corrected_value={"b": 2},
                                actions_taken=["stub"])


def _cfg(mode="active"):
    return WatcherConfig(
        mode=mode,
        parking_topic_fn=lambda e: f"local.public.{e}.dlq.parking",
        source_topic_fn=lambda e: f"local.public.{e}",
    )


def _recorder():
    sent: list[tuple[str, str]] = []
    def publish(t, p): sent.append((t, p))
    return publish, sent


def test_active_correct_applied_then_replays():
    state = InMemoryState()
    audit = InMemoryAudit()
    publish, sent = _recorder()
    cor = _StubCorrector(CorrectionOutcome.APPLIED)
    process_envelope(
        envelope_json=_make_env(),
        policies={"SCHEMA_DRIFT": SchemaDriftPolicy()},
        state=state, audit=audit, publish=publish, cfg=_cfg("active"),
        correctors={"SCHEMA_DRIFT": cor},
        now_ms=2_000_000,
    )
    assert cor.called == 1
    # The corrected event is replayed to the source topic
    assert any(t == "local.public.invoice" for t, _ in sent)
    rec = audit.records[-1]
    assert rec.outcome == "replayed"
    assert rec.policy_id == "schema_drift.v1"
    assert rec.original_value == {"a": 1}
    assert rec.corrected_value == {"b": 2}


def test_active_correct_failed_parks():
    state = InMemoryState()
    audit = InMemoryAudit()
    publish, sent = _recorder()
    cor = _StubCorrector(CorrectionOutcome.FAILED)
    process_envelope(
        envelope_json=_make_env(),
        policies={"SCHEMA_DRIFT": SchemaDriftPolicy()},
        state=state, audit=audit, publish=publish, cfg=_cfg("active"),
        correctors={"SCHEMA_DRIFT": cor},
        now_ms=2_000_000,
    )
    assert audit.records[-1].outcome == "parked"
    assert sent == []  # parked = audit-only, no parking-hub publish


def test_shadow_correct_does_not_invoke_corrector():
    state = InMemoryState()
    audit = InMemoryAudit()
    publish, sent = _recorder()
    cor = _StubCorrector(CorrectionOutcome.APPLIED)
    process_envelope(
        envelope_json=_make_env(),
        policies={"SCHEMA_DRIFT": SchemaDriftPolicy()},
        state=state, audit=audit, publish=publish, cfg=_cfg("shadow"),
        correctors={"SCHEMA_DRIFT": cor},
        now_ms=2_000_000,
    )
    assert cor.called == 0
    assert sent == []
    assert audit.records[-1].outcome == "shadow"


def test_active_correct_with_no_corrector_registered_parks():
    state = InMemoryState()
    audit = InMemoryAudit()
    publish, sent = _recorder()
    process_envelope(
        envelope_json=_make_env(),
        policies={"SCHEMA_DRIFT": SchemaDriftPolicy()},
        state=state, audit=audit, publish=publish, cfg=_cfg("active"),
        correctors={},  # no corrector for SCHEMA_DRIFT
        now_ms=2_000_000,
    )
    assert audit.records[-1].outcome == "parked"
    assert "no corrector" in audit.records[-1].error_message.lower()


def test_exhausted_schema_drift_parks_without_calling_corrector():
    state = InMemoryState()
    audit = InMemoryAudit()
    publish, sent = _recorder()
    cor = _StubCorrector(CorrectionOutcome.APPLIED)
    env = _make_env()
    key = json.loads(env)["idempotency_key"]
    for _ in range(MAX_ATTEMPTS):
        state.record_attempt(key, next_retry_at_ms=0, last_error="x",
                             policy_id="schema_drift.v1", first_seen_ms=1)
    process_envelope(
        envelope_json=env,
        policies={"SCHEMA_DRIFT": SchemaDriftPolicy()},
        state=state, audit=audit, publish=publish, cfg=_cfg("active"),
        correctors={"SCHEMA_DRIFT": cor},
        now_ms=2_000_000,
    )
    assert cor.called == 0
    assert audit.records[-1].outcome == "parked"
