"""Unit tests for the Gold streaming recompute guard (R5).

Covers bounded retry, quarantine after the cap, success-resets-streak, and the
never-raises contract. Flink ValueState / TimerService are faked.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_SHARED = _ROOT.parent / "shared"           # for secret_resolver
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

import gold.src.flink.recompute_guard as guard  # noqa: E402
import gold.pipeline.audit as audit_mod          # noqa: E402


class FakeState:
    def __init__(self, v: int = 0):
        self._v = v
        self.clear_calls = 0

    def value(self):
        return self._v

    def update(self, v):
        self._v = v

    def clear(self):
        self._v = 0
        self.clear_calls += 1


class FakeTimer:
    def __init__(self):
        self.registered: list[int] = []
        self._now = 1000

    def current_processing_time(self):
        return self._now

    def register_processing_time_timer(self, ts):
        self.registered.append(ts)


@pytest.fixture(autouse=True)
def _no_real_metrics(monkeypatch):
    """Capture metric calls + isolate audit/Mongo so tests never touch real infra."""
    rec = MagicMock()
    monkeypatch.setattr("gold.src.monitoring.metrics.record_loader_run", rec)
    monkeypatch.setattr(guard, "MAX_RETRIES", 3)
    # Quarantine writes a gold_audit event via record_stream_event + get_mongo_uri;
    # stub both so the unit test stays offline (the guard call is best-effort anyway).
    monkeypatch.setattr(audit_mod, "record_stream_event", MagicMock())
    monkeypatch.setattr("secret_resolver.get_mongo_uri", lambda: "mongodb://stub", raising=False)
    return rec


def _run(build, state, timer, log=None):
    return guard.recompute_with_retry(
        build,
        fail_state=state,
        timer_service=timer,
        feature="bpe",
        tenant="tn1",
        project_id="1001",
        log=log or MagicMock(),
    )


def test_success_returns_docs_and_resets_streak():
    state = FakeState(v=2)
    docs, retry_ts = _run(lambda: [{"a": 1}], state, FakeTimer())
    assert docs == [{"a": 1}]
    assert retry_ts is None, "success must not schedule a retry"
    assert state.value() == 0 and state.clear_calls == 1


def test_success_no_clear_when_streak_zero():
    state = FakeState(v=0)
    docs, retry_ts = _run(lambda: [{"a": 1}], state, FakeTimer())
    assert docs == [{"a": 1}]
    assert retry_ts is None
    assert state.clear_calls == 0  # nothing to reset


def test_failure_under_cap_schedules_retry_no_raise(_no_real_metrics):
    state, timer = FakeState(0), FakeTimer()

    def boom():
        raise RuntimeError("pg down")

    docs, retry_ts = _run(boom, state, timer)
    assert docs == []                      # nothing emitted
    assert state.value() == 1              # streak incremented
    assert len(timer.registered) == 1      # retry scheduled
    assert timer.registered[0] == 1000 + guard.RETRY_BACKOFF_MS  # backoff^0
    # The returned timestamp must match the timer actually registered: callers
    # persist it in Flink state so process_element can cancel the retry when a
    # new CDC event arrives. If these diverged, stale timers would accumulate
    # and fire as phantom on_timer calls long after the issue cleared.
    assert retry_ts == timer.registered[0]
    _no_real_metrics.assert_called()       # failure metric emitted


def test_backoff_doubles_per_attempt(_no_real_metrics):
    state, timer = FakeState(1), FakeTimer()  # this will be attempt #2
    _run(lambda: (_ for _ in ()).throw(RuntimeError("x")), state, timer)
    assert state.value() == 2
    assert timer.registered[0] == 1000 + guard.RETRY_BACKOFF_MS * 2  # backoff^1


def test_quarantine_at_cap_clears_state_no_retry(_no_real_metrics):
    state, timer = FakeState(2), FakeTimer()  # next failure is attempt #3 == cap

    docs, retry_ts = _run(lambda: (_ for _ in ()).throw(RuntimeError("poison")), state, timer)
    assert docs == []
    assert retry_ts is None, "quarantine must not schedule a retry"
    assert state.clear_calls == 1          # quarantined → streak cleared
    assert timer.registered == []          # NOT retried again
    # quarantine metric emitted (distinct label)
    labels = [c.args[0] for c in _no_real_metrics.call_args_list]
    assert any("quarantine" in l for l in labels)


def test_quarantine_writes_audit_event(_no_real_metrics):
    # On quarantine, a gold_audit.gold_event (recompute_quarantined) is recorded so
    # operators can query the stuck (tenant, project) and target --rebuild.
    state, timer = FakeState(2), FakeTimer()
    _run(lambda: (_ for _ in ()).throw(RuntimeError("poison")), state, timer)
    audit_mod.record_stream_event.assert_called_once()
    kw = audit_mod.record_stream_event.call_args.kwargs
    assert kw["event_type"] == "recompute_quarantined"
    assert kw["tenant_id"] == "tn1"
    assert kw["project_id"] == "1001"
    assert kw["feature"] == "bpe"


def test_no_audit_event_on_retry(_no_real_metrics):
    # A retry (under the cap) must NOT write an audit event — only quarantine does.
    state, timer = FakeState(0), FakeTimer()
    _run(lambda: (_ for _ in ()).throw(RuntimeError("transient")), state, timer)
    audit_mod.record_stream_event.assert_not_called()


def test_never_raises_even_if_metric_fails(monkeypatch):
    # metric import/call blowing up must not propagate
    def explode(*a, **k):
        raise RuntimeError("metrics broken")

    monkeypatch.setattr("gold.src.monitoring.metrics.record_loader_run", explode)
    state, timer = FakeState(0), FakeTimer()
    # should not raise
    docs, retry_ts = guard.recompute_with_retry(
        lambda: (_ for _ in ()).throw(ValueError("bad data")),
        fail_state=state, timer_service=timer,
        feature="qa_recommendation", tenant="tn1", project_id="9", log=MagicMock(),
    )
    assert docs == []
    # A broken metrics backend must not stop the retry being scheduled either —
    # observability failing should degrade reporting, never the recovery path.
    assert retry_ts is not None
