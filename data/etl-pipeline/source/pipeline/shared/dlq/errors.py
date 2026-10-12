"""
DLQ error taxonomy and envelope builder.

Every event that fails Bronze or Silver processing is wrapped in a structured
envelope before being written to a DLQ topic / table. The envelope shape is
the contract between producers (Bronze, Silver) and consumers (dlq_consumer,
the future dlq_watcher service).

Envelope shape (JSON):
    {
        "error_type":      <ErrorType enum value>,
        "error_message":   <human-readable detail>,
        "entity":          <entity name, e.g. "invoice">,
        "tenantid":        <tenant id, may be empty for Bronze rejections>,
        "lsn":             <CDC LSN if known, else null>,
        "sqlstate":        <PG SQLSTATE code if PG error, else null>,
        "column_name":     <PG column involved if known, else null>,
        "idempotency_key": "<tenantid>:<entity>:<lsn>"  or fallback hash,
        "attempts":        <int, defaults to 0 on first DLQ write>,
        "first_seen_ms":   <epoch ms of first DLQ write>,
        "routed_at_ms":    <epoch ms of THIS DLQ write>,
        "raw_full":        <full original payload, PII-redacted, for REPLAY/CORRECT>,
        "raw_truncated":   <first 500 chars of the redacted payload, for display/logging only>
    }

Note on raw_full vs raw_truncated: raw_truncated exists ONLY for human-readable
previews (dlq_consumer inspect output, dashboards) — it is NOT safe to
republish, since anything over 500 bytes is silently cut mid-record. REPLAY
and CORRECT must always source the payload to republish from raw_full.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from enum import Enum
from typing import Any

# Known-sensitive field names (worker PII per config/environments.yaml, plus
# other common secret/PII key names). Matched case-insensitively against
# JSON object keys. Values are replaced with "<redacted len=N>" so downstream
# consumers can see a field was populated without persisting the raw value —
# same convention as src/bpe/source/bpe/config_manager.py:redact_pii_oids.
_SENSITIVE_KEYS = {
    "ssn",
    "social_security_number",
    "socialsecuritynumber",
    "date_of_birth",
    "dateofbirth",
    "dob",
    "tax_id",
    "taxid",
    "ein",
    "bank_account",
    "bank_account_number",
    "routing_number",
    "credit_card",
    "credit_card_number",
    "cc_number",
    "password",
    "secret",
    "api_key",
    "apikey",
}

# Fallback regex redaction for free-text strings (error_message, or a raw
# payload that fails to parse as JSON) — catches "key": "value", key=value,
# and the PostgreSQL "Key (col)=(val)" DETAIL format.
_SENSITIVE_KV_RE = re.compile(
    r'(?i)\b(' + "|".join(sorted(_SENSITIVE_KEYS, key=len, reverse=True)) + r')\b'
    r'("?\s*[:=]\s*"?)([^"\',)\s]+)'
)
_SENSITIVE_DETAIL_RE = re.compile(
    r'(?i)\bKey\s*\(([^)]+)\)\s*=\s*\(([^)]*)\)'
)


def _redact_text(text: str) -> str:
    """Best-effort regex redaction of sensitive key/value pairs in free text."""
    if not text:
        return text

    def _kv_sub(m: "re.Match[str]") -> str:
        return f"{m.group(1)}{m.group(2)}<redacted len={len(m.group(3))}>"

    redacted = _SENSITIVE_KV_RE.sub(_kv_sub, text)

    def _detail_sub(m: "re.Match[str]") -> str:
        cols = [c.strip() for c in m.group(1).split(",")]
        if not any(c.lower() in _SENSITIVE_KEYS for c in cols):
            return m.group(0)
        vals = [v.strip() for v in m.group(2).split(",")]
        redacted_vals = ", ".join(f"<redacted len={len(v)}>" for v in vals)
        return f"Key ({m.group(1)})=({redacted_vals})"

    return _SENSITIVE_DETAIL_RE.sub(_detail_sub, redacted)


def _redact_obj(obj: Any) -> Any:
    """Recursively redact sensitive keys in a parsed JSON structure."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.strip().lower() in _SENSITIVE_KEYS and v is not None:
                out[k] = f"<redacted len={len(str(v))}>"
            else:
                out[k] = _redact_obj(v)
        return out
    if isinstance(obj, list):
        return [_redact_obj(v) for v in obj]
    return obj


def redact_pii(raw: str) -> str:
    """
    Field-level PII redaction of a raw CDC payload before it is persisted to
    the DLQ store.

    Structural (JSON) redaction is preferred — it walks the whole object
    graph and replaces known-sensitive field values with a length tag. When
    `raw` is not valid JSON (e.g. a malformed-payload DLQ entry), falls back
    to a best-effort regex scrub so we never write an obviously-sensitive
    value verbatim, even though field-name-precise redaction isn't possible
    without structure.
    """
    if not raw:
        return raw
    try:
        parsed = json.loads(raw)
    except Exception:
        return _redact_text(raw)
    return json.dumps(_redact_obj(parsed), default=str)


class ErrorType(str, Enum):
    """
    Enum of DLQ error categories. Values are stable strings — they are written
    to Kafka envelopes and matched by consumers, so do not rename without a
    migration plan.

    Values intentionally mirror PG SQLSTATE class semantics where applicable
    (22*** → TYPE_COERCION, 23502 → NOT_NULL_VIOLATION, 23503 → FK_VIOLATION).
    """

    # Envelope-level (Bronze + Silver structural validation)
    MALFORMED_JSON      = "MALFORMED_JSON"
    INVALID_OP          = "INVALID_OP"
    MISSING_TENANT_ID   = "MISSING_TENANT_ID"
    NO_ENTITY           = "NO_ENTITY"
    NO_PK               = "NO_PK"
    LATE_ARRIVAL        = "LATE_ARRIVAL"
    DELETE_ON_MISSING   = "DELETE_ON_MISSING"

    # Silver write-time (PostgreSQL upsert failures)
    PG_TRANSIENT        = "PG_TRANSIENT"        # connection drop, deadlock, lock_timeout
    TYPE_COERCION       = "TYPE_COERCION"       # SQLSTATE class 22 (data exception)
    FK_VIOLATION        = "FK_VIOLATION"        # SQLSTATE 23503
    NOT_NULL_VIOLATION  = "NOT_NULL_VIOLATION"  # SQLSTATE 23502
    UNIQUE_VIOLATION    = "UNIQUE_VIOLATION"    # SQLSTATE 23505
    SCHEMA_DRIFT        = "SCHEMA_DRIFT"        # column or table missing
    TENANT_MISMATCH     = "TENANT_MISMATCH"     # envelope tenantid != source_db (cross-tenant write risk)

    # Fallback
    UNKNOWN             = "UNKNOWN"


# SQLSTATE class 08 = connection exception; class 40 = transaction rollback (deadlock, serialization).
# Class 53 = insufficient resources (lock_timeout, statement_timeout).
# Class 57 = operator intervention (admin shutdown).
_TRANSIENT_CLASSES = {"08", "40", "53", "57"}
_TRANSIENT_CODES = {
    "55P03",  # lock_not_available
    "57014",  # query_canceled (statement_timeout)
}

_SQLSTATE_MAP = {
    "23502": ErrorType.NOT_NULL_VIOLATION,
    "23503": ErrorType.FK_VIOLATION,
    "23505": ErrorType.UNIQUE_VIOLATION,
    "42703": ErrorType.SCHEMA_DRIFT,  # undefined_column
    "42P01": ErrorType.SCHEMA_DRIFT,  # undefined_table
    # 42804 is "datatype_mismatch" (class 42 = syntax/access, but the *cause*
    # is a type mismatch in the SQL expression — e.g. bigint passed where the
    # column is TIMESTAMPTZ). Treat it as TYPE_COERCION so the future drift
    # auto-fix worker can pick these up.
    "42804": ErrorType.TYPE_COERCION,
}


def classify_sqlstate(sqlstate: str | None) -> ErrorType:
    """
    Map a PostgreSQL SQLSTATE code to an ErrorType.

    Falls through to ErrorType.UNKNOWN when the code does not match any known
    pattern. Callers should treat UNKNOWN as "park for human review" — never
    auto-retry an unclassified error.
    """
    if not sqlstate or len(sqlstate) < 2:
        return ErrorType.UNKNOWN

    if sqlstate in _SQLSTATE_MAP:
        return _SQLSTATE_MAP[sqlstate]

    cls = sqlstate[:2]
    if cls in _TRANSIENT_CLASSES or sqlstate in _TRANSIENT_CODES:
        return ErrorType.PG_TRANSIENT

    # Class 22 = data exception (numeric out of range, invalid datetime, etc.)
    if cls == "22":
        return ErrorType.TYPE_COERCION

    # Class 23 = integrity constraint violation, anything not mapped above
    if cls == "23":
        return ErrorType.UNKNOWN  # unmapped integrity violation — explicit park

    return ErrorType.UNKNOWN


def classify_pg_exception(exc: BaseException) -> tuple[ErrorType, str | None, str | None]:
    """
    Classify a psycopg2 exception into (ErrorType, sqlstate, column_name).

    Reads psycopg2's `.pgcode` (SQLSTATE) and `.diag.column_name` if present.
    Falls back to ErrorType.UNKNOWN for non-psycopg2 exceptions so callers do
    not need to special-case the import.
    """
    sqlstate: str | None = getattr(exc, "pgcode", None) or None
    column_name: str | None = None
    diag = getattr(exc, "diag", None)
    if diag is not None:
        column_name = getattr(diag, "column_name", None) or None

    # Network / driver errors with no SQLSTATE — treat as transient if the
    # exception type screams "connection broke."
    if sqlstate is None:
        name = type(exc).__name__.lower()
        if "operational" in name or "interface" in name or "connection" in name:
            return ErrorType.PG_TRANSIENT, None, None
        return ErrorType.UNKNOWN, None, column_name

    return classify_sqlstate(sqlstate), sqlstate, column_name


def _idempotency_key(tenantid: str, entity: str, lsn: Any, raw: str) -> str:
    """
    Stable per-event key. When LSN is present this is exact; without LSN we
    hash the raw payload so replays of the same event collapse to the same
    key (within hash-collision tolerance).
    """
    if lsn is not None and str(lsn).strip() not in ("", "None"):
        return f"{tenantid}:{entity}:{lsn}"
    digest = hashlib.blake2b(raw.encode("utf-8", errors="replace"), digest_size=8).hexdigest()
    return f"{tenantid}:{entity}:h:{digest}"


def build_envelope(
    *,
    error_type: ErrorType,
    error_message: str,
    entity: str,
    raw: str,
    tenantid: str = "",
    env: str = "",
    lsn: Any = None,
    sqlstate: str | None = None,
    column_name: str | None = None,
    attempts: int = 0,
    first_seen_ms: int | None = None,
    now_ms: int | None = None,
) -> str:
    """
    Build the canonical DLQ envelope as a JSON string.

    `now_ms` is overridable so tests are deterministic. `first_seen_ms`
    defaults to `now_ms` when None — used the first time an event hits
    the DLQ; preserve the original value on subsequent re-routes.
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    first_seen = first_seen_ms if first_seen_ms is not None else now

    # Redact known-sensitive fields BEFORE truncation/persistence — never
    # write raw PII (ssn, date_of_birth, etc.) to the durable DLQ store.
    redacted_full = redact_pii(raw)
    redacted_message = _redact_text(error_message)

    envelope: dict[str, Any] = {
        "error_type":      error_type.value,
        "error_message":   redacted_message[:500],
        "entity":          entity,
        "tenantid":        tenantid,
        "env":             env,
        "lsn":             lsn,
        "sqlstate":        sqlstate,
        "column_name":     column_name,
        "idempotency_key": _idempotency_key(tenantid, entity, lsn, raw),
        "attempts":        attempts,
        "first_seen_ms":   first_seen,
        "routed_at_ms":    now,
        # Full-fidelity, PII-redacted payload — REPLAY/CORRECT must republish
        # THIS field, never raw_truncated (finding: truncation at 500 chars
        # silently corrupts Silver on replay via partial ON CONFLICT upsert).
        "raw_full":        redacted_full,
        # Short preview ONLY — for dlq_consumer inspect output / dashboards.
        "raw_truncated":   redacted_full[:500],
    }
    return json.dumps(envelope, default=str)
