"""Tests for MissingTenantPolicy + MissingTenantCorrector."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.dlq.errors import ErrorType, build_envelope

from dlq_watcher.audit import InMemoryAudit
from dlq_watcher.correctors.base import CorrectionOutcome
from dlq_watcher.correctors.missing_tenant import (
    MissingTenantCorrector,
    find_tenant_in_payload,
)
from dlq_watcher.policies.base import Action, RetryState
from dlq_watcher.policies.missing_tenant import MAX_ATTEMPTS, MissingTenantPolicy
from dlq_watcher.state import InMemoryState
from dlq_watcher.watcher import WatcherConfig, process_envelope


# ── find_tenant_in_payload ───────────────────────────────────────────────────

@pytest.mark.parametrize("payload,expected_value,expected_source", [
    ({"tenantid": "tn1"},                       "tn1",  "tenantid"),
    ({"tenant_id": "tn2"},                      "tn2",  "tenant_id"),
    ({"tenantId": "tn3"},                       "tn3",  "tenantId"),
    ({"source_db": "tn4"},                      "tn4",  "source_db"),
    ({"source": {"db": "tn5"}},                 "tn5",  "source.db"),
    ({"source": {"tenant_id": "tn6"}},          "tn6",  "source.tenant_id"),
    ({"source": {"name": "tn7"}},               "tn7",  "source.name"),
    ({"payload": {"source": {"db": "tn8"}}},    "tn8",  "payload.source.db"),
    # Whitespace-only values are not tenants
    ({"tenantid": "  ", "source": {"db": "tn9"}}, "tn9", "source.db"),
])
def test_find_tenant_locations(payload, expected_value, expected_source):
    v, src = find_tenant_in_payload(payload)
    assert v == expected_value
    assert src == expected_source


def test_find_tenant_returns_none_when_absent():
    payload = {"id": 1, "name": "x", "source": {"table": "invoice"}}
    v, src = find_tenant_in_payload(payload)
    assert v is None
    assert src is None


def test_find_tenant_flat_keys_win_over_nested():
    payload = {"tenantid": "flat-wins", "source": {"db": "nested-loses"}}
    v, src = find_tenant_in_payload(payload)
    assert v == "flat-wins"


# ── MissingTenantPolicy ──────────────────────────────────────────────────────

class TestMissingTenantPolicy:
    policy = MissingTenantPolicy()

    def test_first_attempt_corrects(self):
        d = self.policy.evaluate({}, RetryState(attempts=0), now_ms=1)
        assert d.action is Action.CORRECT

    def test_exhausted_attempts_parks(self):
        d = self.policy.evaluate({}, RetryState(attempts=MAX_ATTEMPTS), now_ms=1)
        assert d.action is Action.PARK


# ── MissingTenantCorrector ──────────────────────────────────────────────────

def test_corrector_refuses_when_no_raw():
    result = MissingTenantCorrector().correct({"entity": "invoice"})
    assert result.outcome is CorrectionOutcome.REFUSED
    assert "raw_full" in result.reason


def test_corrector_refuses_when_raw_is_not_json():
    result = MissingTenantCorrector().correct({
        "entity": "invoice",
        "raw_full": "not-json{",
    })
    assert result.outcome is CorrectionOutcome.REFUSED


def test_corrector_refuses_when_payload_has_no_tenant_anywhere():
    raw = json.dumps({"id": 1, "name": "x"})
    result = MissingTenantCorrector().correct({
        "entity": "invoice",
        "raw_full": raw,
    })
    assert result.outcome is CorrectionOutcome.REFUSED
    assert "no tenant" in result.reason.lower()


def test_corrector_applies_from_source_db():
    env = {
        "entity": "invoice",
        "raw_full": json.dumps({"id": 1, "source_db": "tn1234", "op": "u"}),
    }
    result = MissingTenantCorrector().correct(env)
    assert result.outcome is CorrectionOutcome.APPLIED
    assert result.corrected_value == {"tenantid": "tn1234"}
    # Envelope was mutated — patched raw_full has tenantid injected
    patched = json.loads(env["raw_full"])
    assert patched["tenantid"] == "tn1234"
    assert env["tenantid"] == "tn1234"


def test_corrector_applies_from_nested_source_db():
    env = {
        "entity": "invoice",
        "raw_full": json.dumps({"id": 1, "source": {"db": "tn9999"}, "op": "c"}),
    }
    result = MissingTenantCorrector().correct(env)
    assert result.outcome is CorrectionOutcome.APPLIED
    assert json.loads(env["raw_full"])["tenantid"] == "tn9999"


# ── End-to-end through the watcher ──────────────────────────────────────────

class _Recorder:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []
    def __call__(self, t, p): self.sent.append((t, p))


def _cfg(mode="active"):
    return WatcherConfig(
        mode=mode,
        parking_topic_fn=lambda e: f"local.public.{e}.dlq.parking",
        source_topic_fn=lambda e: f"local.public.{e}",
    )


def _env_missing_tenant(raw):
    return build_envelope(
        error_type=ErrorType.MISSING_TENANT_ID,
        error_message="invalid_tenantid=''",
        entity="invoice", raw=raw, tenantid="", lsn=10,
        now_ms=1_000_000,
    )


def test_e2e_active_recovers_and_replays_corrected_payload():
    state, audit, recorder = InMemoryState(), InMemoryAudit(), _Recorder()
    raw = json.dumps({"id": 7, "source_db": "tn42", "op": "u"})

    process_envelope(
        envelope_json=_env_missing_tenant(raw),
        policies={"MISSING_TENANT_ID": MissingTenantPolicy()},
        correctors={"MISSING_TENANT_ID": MissingTenantCorrector()},
        state=state, audit=audit, publish=recorder, cfg=_cfg("active"),
        now_ms=2_000_000,
    )

    # Replayed to source topic
    src_publishes = [p for t, p in recorder.sent if t == "local.public.invoice"]
    assert len(src_publishes) == 1
    # The replayed payload now has tenantid patched in
    replayed = json.loads(src_publishes[0])
    assert replayed["tenantid"] == "tn42"
    # Audit row reflects replayed outcome with the diff
    last = audit.records[-1]
    assert last.outcome == "replayed"
    assert last.corrected_value == {"tenantid": "tn42"}


def test_e2e_active_refuses_when_no_tenant_anywhere_parks():
    state, audit, recorder = InMemoryState(), InMemoryAudit(), _Recorder()
    raw = json.dumps({"id": 7, "op": "u"})  # no source_db, no source.*

    process_envelope(
        envelope_json=_env_missing_tenant(raw),
        policies={"MISSING_TENANT_ID": MissingTenantPolicy()},
        correctors={"MISSING_TENANT_ID": MissingTenantCorrector()},
        state=state, audit=audit, publish=recorder, cfg=_cfg("active"),
        now_ms=2_000_000,
    )

    # No replay; parked = audit-only (no publish to any hub).
    assert recorder.sent == []
    assert audit.records[-1].outcome == "parked"


def test_e2e_shadow_does_not_mutate_or_publish():
    state, audit, recorder = InMemoryState(), InMemoryAudit(), _Recorder()
    raw = json.dumps({"id": 7, "source_db": "tn42", "op": "u"})

    process_envelope(
        envelope_json=_env_missing_tenant(raw),
        policies={"MISSING_TENANT_ID": MissingTenantPolicy()},
        correctors={"MISSING_TENANT_ID": MissingTenantCorrector()},
        state=state, audit=audit, publish=recorder, cfg=_cfg("shadow"),
        now_ms=2_000_000,
    )
    assert recorder.sent == []
    assert audit.records[-1].outcome == "shadow"
