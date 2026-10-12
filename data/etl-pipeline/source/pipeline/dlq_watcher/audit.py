"""
Audit writer — appends one row per watcher decision to silver._dlq_corrections.

Same Protocol pattern as state.py: AuditSink Protocol with a Postgres
implementation for prod and an InMemoryAudit for tests.

Every watcher decision is auditable. In shadow mode, outcome='shadow'.
In active mode, outcome moves through 'replayed' → 'succeeded' or 're_dlq'.
PARK decisions write outcome='parked'. WAIT decisions are NOT audited (they
are not actions yet) — only terminal-for-this-tick outcomes.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditRecord:
    entity:           str
    tenantid:         str
    lsn:              int | None
    idempotency_key:  str
    error_type:       str
    sqlstate:         str | None
    column_name:      str | None
    policy_id:        str
    outcome:          str    # shadow | replayed | succeeded | re_dlq | parked | failed
    attempts:         int
    error_message:    str
    first_seen_ms:    int
    original_value:   dict | None = None
    corrected_value:  dict | None = None


class AuditSink(Protocol):
    def write(self, rec: AuditRecord) -> None: ...
    def close(self) -> None: ...


class InMemoryAudit:
    """Test fake — collects records in a list."""
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def write(self, rec: AuditRecord) -> None:
        self.records.append(rec)

    def close(self) -> None:
        pass


class PostgresAudit:
    """Production sink — writes to silver._dlq_corrections."""

    _INSERT = """
        INSERT INTO silver._dlq_corrections (
            entity, tenantid, lsn, idempotency_key,
            error_type, sqlstate, column_name,
            policy_id, outcome, attempts, error_message,
            first_seen_ms, original_value, corrected_value
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
    """

    def __init__(self) -> None:
        import psycopg2
        self._conn = psycopg2.connect(
            host=os.environ.get("SILVER_PG_HOST",     "localhost"),
            port=int(os.environ.get("SILVER_PG_PORT", "5432")),
            dbname=os.environ.get("SILVER_PG_DB", ""),
            user=os.environ.get("SILVER_PG_USER",     "silver_writer"),
            password=os.environ.get("SILVER_WRITER_PWD") or os.environ.get("SILVER_PG_PASSWORD", ""),
            sslmode=os.environ.get("SILVER_PG_SSL_MODE", "disable"),
            connect_timeout=10,
        )
        self._conn.autocommit = True

    def write(self, rec: AuditRecord) -> None:
        with self._conn.cursor() as cur:
            cur.execute(self._INSERT, (
                rec.entity, rec.tenantid, rec.lsn, rec.idempotency_key,
                rec.error_type, rec.sqlstate, rec.column_name,
                rec.policy_id, rec.outcome, rec.attempts, rec.error_message[:500],
                rec.first_seen_ms,
                json.dumps(rec.original_value) if rec.original_value is not None else None,
                json.dumps(rec.corrected_value) if rec.corrected_value is not None else None,
            ))

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception as exc:
            log.warning("PostgresAudit close failed: %s", exc)
