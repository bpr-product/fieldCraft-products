"""Unit tests for audit.record_stream_event — streaming dead-letter-as-audit."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import gold.pipeline.audit as audit  # noqa: E402

pytestmark = pytest.mark.audit


class _FakeColl:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(doc)


class _FakeDB:
    def __init__(self):
        self.gold_event = _FakeColl()


def test_record_stream_event_inserts_gold_event(monkeypatch):
    fake = _FakeDB()
    monkeypatch.setattr(audit, "_db", lambda uri: fake)

    audit.record_stream_event(
        "mongodb://x",
        tenant_id="tn1",
        feature="bpe",
        event_type="recompute_quarantined",
        project_id="1001",
        attempts=3,
        error_message="boom",
    )

    assert len(fake.gold_event.docs) == 1
    doc = fake.gold_event.docs[0]
    assert doc["eventType"] == "recompute_quarantined"
    assert doc["tenantId"] == "tn1"
    assert doc["feature"] == "bpe"
    assert doc["runId"] == "stream:bpe"          # marks streaming origin
    assert doc["stepName"] == "stream"
    assert doc["status"] == "failed"
    assert doc["metadata"]["projectId"] == "1001"
    assert doc["metadata"]["attempts"] == 3
    assert doc["metadata"]["origin"] == "stream"
    assert doc["errorMessage"] == "boom"


def test_record_stream_event_never_raises(monkeypatch):
    def boom(uri):
        raise RuntimeError("mongo down")

    monkeypatch.setattr(audit, "_db", boom)
    # must swallow — audit is non-fatal
    audit.record_stream_event(
        "mongodb://x", tenant_id="tn1", feature="qa_recommendation",
        event_type="recompute_quarantined",
    )


def test_error_message_truncated(monkeypatch):
    fake = _FakeDB()
    monkeypatch.setattr(audit, "_db", lambda uri: fake)
    audit.record_stream_event(
        "mongodb://x", tenant_id="t", feature="bpe",
        event_type="sink_dropped", error_message="x" * 5000,
    )
    assert len(fake.gold_event.docs[0]["errorMessage"]) == 2000
