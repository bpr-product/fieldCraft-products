"""
Verifies the canonical DLQ envelope (shared.dlq.errors.build_envelope) is
field-compatible with the existing scripts/dlq_consumer.py replay tool.

The consumer is a CLI script, not a library — we cannot import it cleanly
because top-level execution sets up Kafka. Instead, we replicate the exact
field reads from the consumer's _inspect() and _replay() functions and
assert each one succeeds against an envelope built by the producer.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.dlq.errors import ErrorType, build_envelope


def _make_envelope():
    return build_envelope(
        error_type=ErrorType.PG_TRANSIENT,
        error_message="connection closed unexpectedly",
        entity="invoice",
        raw='{"id": 1, "tenantid": "tn1", "op": "u", "amount": 99.50}',
        tenantid="tn1",
        lsn=4242,
        sqlstate="08006",
        now_ms=1_700_000_000_000,
    )


def test_inspect_field_reads_succeed():
    """Mirrors scripts/dlq_consumer.py _inspect() lines 150–166."""
    raw = _make_envelope()
    env = json.loads(raw)

    # Line 150–151
    by_error_key = env.get("error_type", "unknown")
    assert by_error_key == "PG_TRANSIENT"

    # Line 163 — datetime.fromtimestamp on routed_at_ms / 1000 must not blow up
    ms = env.get("routed_at_ms", 0)
    assert isinstance(ms, int) and ms > 0

    # Lines 164–166
    assert env.get("error_type") == "PG_TRANSIENT"
    assert env.get("error_message", "").startswith("connection closed")
    assert env.get("raw_truncated", "").startswith('{"id": 1')


def test_replay_field_reads_succeed():
    """Mirrors scripts/dlq_consumer.py _replay() lines 200–219."""
    raw = _make_envelope()
    envelope = json.loads(raw)

    etype = envelope.get("error_type", "")
    assert etype == "PG_TRANSIENT"

    raw_payload = envelope.get("raw_truncated", "")
    assert raw_payload, "raw_truncated must be non-empty for replay to proceed"

    # Replay verifies raw_payload is still valid JSON before republishing.
    parsed = json.loads(raw_payload)
    assert parsed["id"] == 1
    assert parsed["tenantid"] == "tn1"


def test_replay_error_type_filter():
    """--error-type LATE_ARRIVAL must match envelope.error_type exactly."""
    raw_late = build_envelope(
        error_type=ErrorType.LATE_ARRIVAL,
        error_message="age_ms=900000",
        entity="task",
        raw='{"id":99}',
        tenantid="tn1",
        lsn=1,
    )
    raw_trans = _make_envelope()

    assert json.loads(raw_late)["error_type"] == "LATE_ARRIVAL"
    assert json.loads(raw_trans)["error_type"] == "PG_TRANSIENT"

    # A filter for LATE_ARRIVAL skips the transient envelope.
    filt = "LATE_ARRIVAL"
    assert json.loads(raw_late)["error_type"] == filt
    assert json.loads(raw_trans)["error_type"] != filt
