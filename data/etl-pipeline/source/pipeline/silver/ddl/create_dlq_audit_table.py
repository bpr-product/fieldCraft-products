#!/usr/bin/env python3
"""
Create the DLQ audit table — silver._dlq_corrections.

Every auto-correction performed by the future dlq_watcher writes a row here:
which event, what error_type triggered correction, which policy was applied,
the before→after value diff, and the outcome (replayed, parked, failed).

The table is SQL-queryable indefinitely — it is the audit trail consumers
(compliance, debug, Grafana panels) read from. Volatile retry state lives in
Redis; this table is for durable history.

Safe to re-run: uses CREATE SCHEMA IF NOT EXISTS + CREATE TABLE IF NOT EXISTS.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Reuse the .env loader / connection params from the sibling Silver DDL script.
# __file__ is source/pipeline/silver/ddl/create_dlq_audit_table.py -- climb 5
# levels to reach src/datapipeline/.env.local (see create_silver_tables.py's
# identical fix for why 3 levels silently resolves to a nonexistent path).
_ENV_FILE = Path(__file__).parent.parent.parent.parent.parent / ".env.local"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))

DST_HOST     = os.environ.get("SILVER_PG_HOST",     "localhost")
DST_PORT     = int(os.environ.get("SILVER_PG_PORT", "5433"))
DST_DB       = os.environ.get("SILVER_PG_DB", "")
DST_USER     = os.environ.get("SILVER_PG_USER",     "silver_writer")
DST_PASSWORD = os.environ.get("SILVER_PG_PASSWORD", "")
DST_SSL      = os.environ.get("SILVER_PG_SSL_MODE", "prefer")


DDL = """
CREATE SCHEMA IF NOT EXISTS silver;

CREATE TABLE IF NOT EXISTS silver._dlq_corrections (
    id                BIGSERIAL    PRIMARY KEY,
    -- Identity of the event being corrected
    entity            TEXT         NOT NULL,
    tenantid          TEXT         NOT NULL DEFAULT '',
    lsn               BIGINT,
    idempotency_key   TEXT         NOT NULL,
    -- Classification at the time of correction
    error_type        TEXT         NOT NULL,
    sqlstate          TEXT,
    column_name       TEXT,
    -- Policy that ran and what it did
    policy_id         TEXT         NOT NULL,
    original_value    JSONB,
    corrected_value   JSONB,
    -- Outcome — see dlq_watcher policy contract
    --   shadow:    watcher computed action but did NOT replay (shadow mode)
    --   replayed:  watcher republished to source topic; Silver upsert pending
    --   succeeded: watcher confirmed Silver upsert landed
    --   re_dlq:    replay re-entered DLQ — retry exhausted or new error
    --   parked:    watcher gave up; event sits in *.dlq.parking
    --   failed:    watcher itself errored applying the correction
    outcome           TEXT         NOT NULL,
    attempts          INTEGER      NOT NULL DEFAULT 1,
    error_message     TEXT,
    -- Timing
    first_seen_ms     BIGINT       NOT NULL,
    corrected_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS dlq_corrections_idem_idx
    ON silver._dlq_corrections (idempotency_key);

CREATE INDEX IF NOT EXISTS dlq_corrections_entity_outcome_idx
    ON silver._dlq_corrections (entity, outcome, corrected_at DESC);

CREATE INDEX IF NOT EXISTS dlq_corrections_error_type_idx
    ON silver._dlq_corrections (error_type, corrected_at DESC);

COMMENT ON TABLE silver._dlq_corrections IS
    'Append-only audit log of automatic DLQ corrections performed by dlq_watcher. '
    'Every row proves what was changed, by which policy, with what outcome. '
    'Never delete rows — required for compliance review of auto-mutated business data.';
"""


def main() -> None:
    import psycopg2
    conn = psycopg2.connect(
        host=DST_HOST, port=DST_PORT, dbname=DST_DB,
        user=DST_USER, password=DST_PASSWORD, sslmode=DST_SSL,
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(DDL)
    cur.close()
    conn.close()
    print("DLQ audit table ready: silver._dlq_corrections")


if __name__ == "__main__":
    main()
