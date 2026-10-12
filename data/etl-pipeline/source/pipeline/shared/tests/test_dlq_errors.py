"""Unit tests for shared.dlq.errors — pure logic, no IO."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.dlq.errors import (
    ErrorType,
    build_envelope,
    classify_pg_exception,
    classify_sqlstate,
)


# ── classify_sqlstate ────────────────────────────────────────────────────────

@pytest.mark.parametrize("sqlstate,expected", [
    ("23502", ErrorType.NOT_NULL_VIOLATION),
    ("23503", ErrorType.FK_VIOLATION),
    ("23505", ErrorType.UNIQUE_VIOLATION),
    ("42703", ErrorType.SCHEMA_DRIFT),
    ("42P01", ErrorType.SCHEMA_DRIFT),
    ("08006", ErrorType.PG_TRANSIENT),  # connection_failure
    ("08001", ErrorType.PG_TRANSIENT),  # sqlclient_unable_to_establish_sqlconnection
    ("40001", ErrorType.PG_TRANSIENT),  # serialization_failure
    ("40P01", ErrorType.PG_TRANSIENT),  # deadlock_detected
    ("53300", ErrorType.PG_TRANSIENT),  # too_many_connections
    ("57014", ErrorType.PG_TRANSIENT),  # query_canceled
    ("55P03", ErrorType.PG_TRANSIENT),  # lock_not_available
    ("22003", ErrorType.TYPE_COERCION),  # numeric_value_out_of_range
    ("22007", ErrorType.TYPE_COERCION),  # invalid_datetime_format
    ("22P02", ErrorType.TYPE_COERCION),  # invalid_text_representation
])
def test_classify_sqlstate_known(sqlstate, expected):
    assert classify_sqlstate(sqlstate) is expected


@pytest.mark.parametrize("sqlstate", [None, "", "X", "23999", "99999"])
def test_classify_sqlstate_unknown(sqlstate):
    # 23999 is class 23 (integrity) but not 23502/3/5 — explicit UNKNOWN park
    assert classify_sqlstate(sqlstate) is ErrorType.UNKNOWN


# ── classify_pg_exception ────────────────────────────────────────────────────

class _FakeDiag:
    def __init__(self, column_name=None):
        self.column_name = column_name


class _FakePgError(Exception):
    """Mimics psycopg2.Error interface (pgcode + diag)."""
    def __init__(self, pgcode=None, column_name=None, message=""):
        super().__init__(message)
        self.pgcode = pgcode
        self.diag = _FakeDiag(column_name=column_name)


def test_classify_pg_exception_with_sqlstate_and_column():
    exc = _FakePgError(pgcode="22003", column_name="amount", message="overflow")
    etype, sqlstate, col = classify_pg_exception(exc)
    assert etype is ErrorType.TYPE_COERCION
    assert sqlstate == "22003"
    assert col == "amount"


def test_classify_pg_exception_fk_violation():
    exc = _FakePgError(pgcode="23503", column_name="project_id")
    etype, sqlstate, col = classify_pg_exception(exc)
    assert etype is ErrorType.FK_VIOLATION
    assert sqlstate == "23503"
    assert col == "project_id"


def test_classify_pg_exception_no_sqlstate_treats_operational_as_transient():
    class OperationalError(Exception):
        pass
    etype, sqlstate, col = classify_pg_exception(OperationalError("connection closed"))
    assert etype is ErrorType.PG_TRANSIENT
    assert sqlstate is None
    assert col is None


def test_classify_pg_exception_unknown_python_exception():
    etype, sqlstate, col = classify_pg_exception(ValueError("nope"))
    assert etype is ErrorType.UNKNOWN
    assert sqlstate is None


# ── build_envelope ───────────────────────────────────────────────────────────

def _parse(env_str):
    return json.loads(env_str)


def test_envelope_minimum_required_fields():
    env = _parse(build_envelope(
        error_type=ErrorType.LATE_ARRIVAL,
        error_message="age_ms=12345",
        entity="invoice",
        raw='{"id":1}',
        tenantid="tn1",
        lsn=42,
        now_ms=1_700_000_000_000,
    ))
    expected_fields = {
        "error_type", "error_message", "entity", "tenantid", "env",
        "lsn", "sqlstate", "column_name", "idempotency_key",
        "attempts", "first_seen_ms", "routed_at_ms", "raw_full", "raw_truncated",
    }
    assert set(env.keys()) == expected_fields
    assert env["error_type"] == "LATE_ARRIVAL"
    assert env["entity"] == "invoice"
    assert env["tenantid"] == "tn1"
    assert env["lsn"] == 42
    assert env["routed_at_ms"] == 1_700_000_000_000
    assert env["first_seen_ms"] == 1_700_000_000_000  # defaults to now_ms
    assert env["attempts"] == 0
    # Redaction round-trips the payload through json.loads/json.dumps (to walk
    # and redact known-sensitive keys), so byte-for-byte whitespace isn't
    # preserved — compare parsed content instead.
    assert json.loads(env["raw_truncated"]) == {"id": 1}
    assert json.loads(env["raw_full"]) == {"id": 1}


def test_envelope_idempotency_key_with_lsn():
    env = _parse(build_envelope(
        error_type=ErrorType.PG_TRANSIENT,
        error_message="conn dropped",
        entity="task",
        raw='{"x":1}',
        tenantid="tn1",
        lsn=99,
    ))
    assert env["idempotency_key"] == "tn1:task:99"


def test_envelope_idempotency_key_without_lsn_uses_payload_hash():
    raw = '{"x":1}'
    a = _parse(build_envelope(
        error_type=ErrorType.MALFORMED_JSON,
        error_message="x",
        entity="task",
        raw=raw,
        tenantid="tn1",
    ))
    b = _parse(build_envelope(
        error_type=ErrorType.MALFORMED_JSON,
        error_message="x",
        entity="task",
        raw=raw,
        tenantid="tn1",
    ))
    assert a["idempotency_key"] == b["idempotency_key"]
    assert a["idempotency_key"].startswith("tn1:task:h:")

    # Different raw → different key
    c = _parse(build_envelope(
        error_type=ErrorType.MALFORMED_JSON,
        error_message="x",
        entity="task",
        raw='{"x":2}',
        tenantid="tn1",
    ))
    assert a["idempotency_key"] != c["idempotency_key"]


def test_envelope_truncates_raw_to_500_chars():
    big = "a" * 1000
    env = _parse(build_envelope(
        error_type=ErrorType.MALFORMED_JSON,
        error_message="big",
        entity="x",
        raw=big,
    ))
    assert len(env["raw_truncated"]) == 500


def test_envelope_raw_full_preserves_full_payload_over_500_chars():
    # Finding P0: raw_truncated must NEVER be the field REPLAY/CORRECT use to
    # republish — raw_full must carry the complete, untruncated payload.
    big_payload = json.dumps({"id": 1, "note": "x" * 600})
    assert len(big_payload) > 500
    env = _parse(build_envelope(
        error_type=ErrorType.MALFORMED_JSON,
        error_message="big",
        entity="x",
        raw=big_payload,
    ))
    assert env["raw_full"] == big_payload
    assert len(env["raw_truncated"]) == 500
    assert len(env["raw_full"]) > 500


def test_envelope_truncates_error_message_to_500_chars():
    big = "e" * 1000
    env = _parse(build_envelope(
        error_type=ErrorType.UNKNOWN,
        error_message=big,
        entity="x",
        raw="r",
    ))
    assert len(env["error_message"]) == 500


def test_envelope_redacts_sensitive_fields_in_raw_full():
    # Finding P1: worker PII (ssn, date_of_birth) must never be persisted
    # unredacted to the durable DLQ store.
    raw = json.dumps({
        "id": 1,
        "ssn": "123-45-6789",
        "date_of_birth": "1980-01-01",
        "name": "not sensitive",
    })
    env = _parse(build_envelope(
        error_type=ErrorType.NOT_NULL_VIOLATION,
        error_message="ok",
        entity="worker",
        raw=raw,
    ))
    full = json.loads(env["raw_full"])
    assert full["ssn"].startswith("<redacted len=")
    assert full["date_of_birth"].startswith("<redacted len=")
    assert full["name"] == "not sensitive"
    assert "123-45-6789" not in env["raw_full"]
    assert "1980-01-01" not in env["raw_full"]


def test_envelope_redacts_sensitive_fields_in_error_message():
    env = _parse(build_envelope(
        error_type=ErrorType.UNIQUE_VIOLATION,
        error_message="duplicate key value violates unique constraint "
                       '"worker_ssn_key" DETAIL:  Key (ssn)=(123-45-6789) already exists.',
        entity="worker",
        raw='{"id":1}',
    ))
    assert "123-45-6789" not in env["error_message"]
    assert "<redacted len=" in env["error_message"]


def test_envelope_preserves_first_seen_ms_on_retry():
    # On the first DLQ write, first_seen_ms = now_ms.
    # On a subsequent re-route by the watcher, first_seen_ms must be preserved.
    env = _parse(build_envelope(
        error_type=ErrorType.PG_TRANSIENT,
        error_message="retry",
        entity="x",
        raw="r",
        attempts=3,
        first_seen_ms=1_000,
        now_ms=5_000,
    ))
    assert env["first_seen_ms"] == 1_000
    assert env["routed_at_ms"] == 5_000
    assert env["attempts"] == 3


def test_envelope_is_valid_json_string():
    s = build_envelope(
        error_type=ErrorType.NO_PK,
        error_message="no pk",
        entity="x",
        raw="r",
    )
    assert isinstance(s, str)
    # Round-trip
    assert json.loads(s)["error_type"] == "NO_PK"


def test_error_type_values_are_stable_strings():
    # Producers and consumers depend on these literal strings.
    # If you must change one, that's a migration, not a refactor.
    assert ErrorType.PG_TRANSIENT.value == "PG_TRANSIENT"
    assert ErrorType.LATE_ARRIVAL.value == "LATE_ARRIVAL"
    assert ErrorType.SCHEMA_DRIFT.value == "SCHEMA_DRIFT"
    assert ErrorType.UNKNOWN.value == "UNKNOWN"
