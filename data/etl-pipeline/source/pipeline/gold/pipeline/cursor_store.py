"""Persistent cursor store for Gold pipeline incremental loads.

Wraps the gold_pipeline._job_state table in Silver PostgreSQL.
Provides read/write/mark_error/reset functions used by all loaders
to track their Silver watermark position.

Provisioning is **lazy / on demand**: the table is no longer created at
setup or at the start of every run. It is created the first time a real
(non-rebuild) batch incremental load actually reads or writes the cursor,
via ``_lazy_ensure`` (once per process per DSN). In a pure-realtime or
``--rebuild`` deployment — where streams use Flink checkpoints and rebuilds
read from EPOCH without touching the cursor — the table is never provisioned.

Usage pattern in a loader:
    from gold.pipeline.cursor_store import read_cursor, write_cursor, mark_error, EPOCH

    last_ts = read_cursor(pg_dsn, _STEP_NAME, cfg.tenantid)
    max_silver_ts = last_ts
    try:
        for row in iter_silver_rows(..., last_ts=last_ts):
            ts = row.get("silver_ts")
            if ts and ts > max_silver_ts:
                max_silver_ts = ts
            ...
        if stats.failed > 0:
            mark_error(pg_dsn, _STEP_NAME, cfg.tenantid, "write failures")
        elif max_silver_ts > last_ts:
            write_cursor(pg_dsn, _STEP_NAME, cfg.tenantid, max_silver_ts)
    except Exception as exc:
        mark_error(pg_dsn, _STEP_NAME, cfg.tenantid, exc)
        raise
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

from gold.pipeline.lineage import current_run_id

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

log = logging.getLogger("gold.pipeline.cursor_store")

# Track DSNs already provisioned in this process so lazy callers do not re-run
# the (idempotent) DDL on every cursor read/write.
_ensured_dsns: set[str] = set()


def _lazy_ensure(pg_dsn: str) -> None:
    """Provision the cursor table on first real use, once per process per DSN.

    Called by ``read_cursor`` / ``write_cursor`` / ``mark_error`` so the table
    materialises only when a genuine batch incremental load needs it — never at
    setup or for realtime/``--rebuild`` runs.
    """
    if pg_dsn in _ensured_dsns:
        return
    ensure_table(pg_dsn)
    _ensured_dsns.add(pg_dsn)


def ensure_table(pg_dsn: str) -> None:
    """Create gold_pipeline schema and _job_state table if not yet present.

    Idempotent — safe to call on every startup.
    """
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS gold_pipeline")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS gold_pipeline._job_state (
                step_name          TEXT        NOT NULL,
                tenant_id          TEXT        NOT NULL DEFAULT '',
                last_processed_ts  TIMESTAMPTZ,
                status             TEXT        NOT NULL DEFAULT 'idle',
                last_run_at        TIMESTAMPTZ,
                error_message      TEXT,
                rows_read          INT         NOT NULL DEFAULT 0,
                rows_written       INT         NOT NULL DEFAULT 0,
                duration_ms        INT         NOT NULL DEFAULT 0,
                run_id             TEXT,
                PRIMARY KEY (step_name, tenant_id)
            )
            """
        )
        # Add columns to existing tables (idempotent)
        for col, typedef in (
            ("rows_read",    "INT NOT NULL DEFAULT 0"),
            ("rows_written", "INT NOT NULL DEFAULT 0"),
            ("duration_ms",  "INT NOT NULL DEFAULT 0"),
            ("run_id",       "TEXT"),
        ):
            cur.execute(
                f"ALTER TABLE gold_pipeline._job_state "
                f"ADD COLUMN IF NOT EXISTS {col} {typedef}"
            )
        conn.commit()
    log.debug("gold_pipeline._job_state ensured")


def get_max_silver_ts_per_env(
    pg_dsn: str, table: str, tenant_col: str, tenant_value: str,
) -> datetime:
    """Return the MIN of each ``_s_env``'s own MAX(_s_silver_ts) for one tenant.

    NOT the overall MAX across all envs — a tenant can have rows in more than
    one ``_s_env`` (e.g. si_dev and datamart both cdc_enabled). Advancing a
    shared cursor to the fastest-moving env's watermark would mask a later,
    still-newer update in a slower-moving env whose own timestamp sits below
    that watermark. Extracted from the six canonical loaders, which
    previously each hand-copied this identical query against their own table.

    ``table`` and ``tenant_col`` are trusted, hardcoded literals passed by
    each loader (never user input) — interpolated directly since Postgres
    does not support parameterized identifiers.
    """
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT MIN(env_max) FROM ("
            f"  SELECT MAX(_s_silver_ts) AS env_max FROM silver.{table}"
            f"  WHERE {tenant_col} = %s GROUP BY _s_env"
            f") per_env",
            (tenant_value,),
        )
        ts = cur.fetchone()[0]
    if ts is None:
        return EPOCH
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def read_cursor(pg_dsn: str, step_name: str, tenant_id: str) -> datetime:
    """Return last_processed_ts for (step_name, tenant_id).

    Returns EPOCH when no row exists or ts is NULL — causing the loader
    to perform a full back-fill on its first run.
    """
    _lazy_ensure(pg_dsn)
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT last_processed_ts FROM gold_pipeline._job_state "
            "WHERE step_name = %s AND tenant_id = %s",
            (step_name, tenant_id),
        )
        row = cur.fetchone()
    if row is None or row[0] is None:
        return EPOCH
    ts = row[0]
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def write_cursor(
    pg_dsn: str,
    step_name: str,
    tenant_id: str,
    last_processed_ts: datetime,
    *,
    status: str = "idle",
    error_message: str | None = None,
    rows_read: int = 0,
    rows_written: int = 0,
    duration_ms: int = 0,
    run_id: str | None = None,
) -> None:
    """Upsert the watermark position for (step_name, tenant_id).

    ``run_id`` ties this watermark advance to the pipeline run that produced it
    (defaults to the active run from the environment) for end-to-end lineage.
    """
    now = datetime.now(timezone.utc)
    run_id = run_id if run_id is not None else current_run_id()
    _lazy_ensure(pg_dsn)
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gold_pipeline._job_state
                (step_name, tenant_id, last_processed_ts, status, last_run_at, error_message,
                 rows_read, rows_written, duration_ms, run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (step_name, tenant_id) DO UPDATE SET
                last_processed_ts = EXCLUDED.last_processed_ts,
                status            = EXCLUDED.status,
                last_run_at       = EXCLUDED.last_run_at,
                error_message     = EXCLUDED.error_message,
                rows_read         = EXCLUDED.rows_read,
                rows_written      = EXCLUDED.rows_written,
                duration_ms       = EXCLUDED.duration_ms,
                run_id            = EXCLUDED.run_id
            """,
            (step_name, tenant_id, last_processed_ts, status, now, error_message,
             rows_read, rows_written, duration_ms, run_id),
        )
        conn.commit()
    log.debug(
        "cursor advanced | step=%s tenant=%s ts=%s rows_read=%d rows_written=%d duration_ms=%d run_id=%s",
        step_name, tenant_id, last_processed_ts.isoformat(), rows_read, rows_written, duration_ms, run_id,
    )


def mark_error(
    pg_dsn: str,
    step_name: str,
    tenant_id: str,
    error: Exception | str,
    run_id: str | None = None,
) -> None:
    """Record a run failure WITHOUT advancing the cursor.

    The cursor is deliberately not advanced so the next run retries the
    same Silver rows rather than silently skipping them. ``run_id`` records
    which run hit the error (defaults to the active run from the environment).
    """
    now = datetime.now(timezone.utc)
    msg = str(error)[:2000]
    run_id = run_id if run_id is not None else current_run_id()
    _lazy_ensure(pg_dsn)
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gold_pipeline._job_state
                (step_name, tenant_id, status, last_run_at, error_message, run_id)
            VALUES (%s, %s, 'error', %s, %s, %s)
            ON CONFLICT (step_name, tenant_id) DO UPDATE SET
                status        = 'error',
                last_run_at   = EXCLUDED.last_run_at,
                error_message = EXCLUDED.error_message,
                run_id        = EXCLUDED.run_id
            """,
            (step_name, tenant_id, now, msg, run_id),
        )
        conn.commit()
    log.warning(
        "cursor error recorded | step=%s tenant=%s error=%s",
        step_name, tenant_id, msg,
    )


def reset_cursor(pg_dsn: str, step_name: str, tenant_id: str) -> None:
    """Reset cursor to NULL, triggering a full back-fill on next run.

    Use this when a collection is dropped and rebuilt.
    """
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gold_pipeline._job_state
                (step_name, tenant_id, last_processed_ts, status, last_run_at)
            VALUES (%s, %s, NULL, 'idle', NOW())
            ON CONFLICT (step_name, tenant_id) DO UPDATE SET
                last_processed_ts = NULL,
                status            = 'idle',
                last_run_at       = NOW()
            """,
            (step_name, tenant_id),
        )
        conn.commit()
    log.info(
        "cursor reset | step=%s tenant=%s → full backfill on next run",
        step_name, tenant_id,
    )
