"""
SilverDirectWriter — writes external CDC envelopes directly to silver.ext_* tables.

External data bypasses the Flink Silver CDC merge job because:
- ext_ tables have a fixed schema (payload TEXT column, no typed business columns)
- No schema cache TTL or drift detection needed

Implements the same invariants as the Flink Silver job:
- LSN guard: ON CONFLICT ... WHERE _s_lsn < EXCLUDED._s_lsn (prevents out-of-order overwrites)
- Soft-delete: DELETE ops set _s_is_deleted=TRUE, _s_deleted_at=NOW() (rows never physically removed)
- Composite PK: (_s_tenantid, _s_external_id) — tenant isolation matches all other Silver tables
"""
import json
import logging
import os
import re
import time
from typing import Any

import psycopg2
from psycopg2.extras import execute_values

log = logging.getLogger(__name__)

PG_HOST     = os.environ.get("SILVER_PG_HOST",     "localhost")
PG_PORT     = int(os.environ.get("SILVER_PG_PORT", "5432"))
PG_DB       = os.environ.get("SILVER_PG_DB",       "")
PG_USER     = os.environ.get("SILVER_PG_USER",     "silver_writer")
PG_PASSWORD = os.environ.get("SILVER_PG_PASSWORD", "")
PG_SSL      = os.environ.get("SILVER_PG_SSL_MODE", "require")

# source_name/entity ultimately trace back to attacker-controlled blob paths
# (via filename_stem in event_grid_handler.py) and get interpolated directly
# into DDL/DML below with no quoting — validate once, early, against a strict
# allowlist so every downstream f-string use is already safe.
_IDENTIFIER_RE = re.compile(r"^[a-z0-9_]+$")


def _validate_identifier_part(value: str, label: str) -> str:
    normalized = value.lower()
    if not _IDENTIFIER_RE.match(normalized):
        raise ValueError(
            f"Invalid {label} {value!r} — must match ^[a-z0-9_]+$ (case-insensitive)"
        )
    return normalized


def _table_name(source_name: str, entity: str) -> str:
    src = _validate_identifier_part(source_name.replace("-", "_").replace(".", "_"), "source_name")
    ent = _validate_identifier_part(entity, "entity")
    if src == ent:
        return f"silver.ext_{src}"
    return f"silver.ext_{src}_{ent}"


def _ensure_table(cur, table: str) -> None:
    """Create the ext_ table if it doesn't exist yet."""
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {table} (
            _s_external_id   TEXT        NOT NULL,
            _s_tenantid      TEXT        NOT NULL DEFAULT '',
            _s_source_name   TEXT,
            _s_op            TEXT,
            _s_lsn           BIGINT,
            _s_is_deleted    BOOLEAN     DEFAULT FALSE,
            _s_deleted_at    TIMESTAMPTZ,
            _s_bronze_ts     TIMESTAMPTZ,
            _s_silver_ts     TIMESTAMPTZ DEFAULT NOW(),
            payload          TEXT,
            PRIMARY KEY (_s_tenantid, _s_external_id)
        )
    """)


def _ensure_dlq_table(cur) -> None:
    """Create silver._external_dlq if it doesn't exist."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS silver._external_dlq (
            id            BIGSERIAL    PRIMARY KEY,
            source_name   TEXT         NOT NULL,
            source_entity TEXT         NOT NULL,
            error_type    TEXT         NOT NULL,
            error_message TEXT,
            raw_record    TEXT,
            created_at    TIMESTAMPTZ  DEFAULT NOW()
        )
    """)


class SilverDirectWriter:
    def __init__(self, config) -> None:
        self._config = config
        self._conn   = None

    def _get_conn(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(
                host=PG_HOST, port=PG_PORT, dbname=PG_DB,
                user=PG_USER, password=PG_PASSWORD, sslmode=PG_SSL,
            )
        return self._conn

    def write(self, envelopes: list[dict[str, Any]]) -> None:
        if not envelopes:
            return

        table = _table_name(self._config.source_name, self._config.source_entity)
        now   = time.time()

        # Build rows and deduplicate by external_id within the batch.
        # If id_field is absent, a row-index fallback guarantees uniqueness.
        # Dedup keeps the last occurrence (highest row index) per id.
        seen: dict[str, tuple] = {}
        for i, env in enumerate(envelopes):
            ext_id = (
                env.get("external_id")
                or f"{self._config.source_name}:{self._config.source_entity}:{i}:{int(now*1000)}"
            )
            seen[ext_id] = (
                ext_id,
                env.get("tenantid") or "",  # empty string = platform-level (no tenant)
                env.get("source_name") or self._config.source_name,
                env.get("op", "r"),
                (lambda r: int(r) if r is not None else int(now * 1000))(env.get("lsn")),
                env.get("payload") or json.dumps(
                    {k: v for k, v in env.items()
                     if k not in ("external_id", "tenantid", "source_name", "op", "lsn",
                                  "ts_ms", "source_entity", "_ingested_at", "source_type")},
                    default=str,
                ),
            )
        rows = list(seen.values())

        # Split: deletes need soft-delete; upserts need LSN guard.
        upsert_rows = [r for r in rows if r[3] != "d"]
        delete_rows = [r for r in rows if r[3] == "d"]

        conn = self._get_conn()
        with conn:
            cur = conn.cursor()
            _ensure_table(cur, table)
            if upsert_rows:
                execute_values(cur, f"""
                    INSERT INTO {table}
                        (_s_external_id, _s_tenantid, _s_source_name, _s_op, _s_lsn, payload)
                    VALUES %s
                    ON CONFLICT (_s_tenantid, _s_external_id) DO UPDATE SET
                        _s_source_name = EXCLUDED._s_source_name,
                        _s_op          = EXCLUDED._s_op,
                        _s_lsn         = EXCLUDED._s_lsn,
                        _s_silver_ts   = NOW(),
                        payload        = EXCLUDED.payload
                    WHERE {table}._s_lsn IS NULL OR EXCLUDED._s_lsn IS NULL
                       OR {table}._s_lsn < EXCLUDED._s_lsn
                """, upsert_rows)
                log.info("SilverDirectWriter: upserted %d rows into %s", len(upsert_rows), table)
            if delete_rows:
                for row in delete_rows:
                    ext_id, _, _, _, lsn_val, _ = row
                    tenantid_val = row[1]
                    cur.execute(
                        f"UPDATE {table} "
                        f"SET _s_is_deleted=TRUE, _s_deleted_at=NOW(), _s_op='d', "
                        f"    _s_lsn=%s, _s_silver_ts=NOW() "
                        f"WHERE _s_tenantid=%s AND _s_external_id=%s "
                        f"  AND (_s_lsn IS NULL OR %s IS NULL OR _s_lsn < %s)",
                        (lsn_val, tenantid_val, ext_id, lsn_val, lsn_val),
                    )
                log.info("SilverDirectWriter: soft-deleted %d rows in %s", len(delete_rows), table)

    def write_dlq(self, records: list[dict]) -> None:
        """Write validation-rejected records to silver._external_dlq."""
        if not records:
            return
        rows = [
            (
                rec.get("source_name", self._config.source_name),
                rec.get("source_entity", self._config.source_entity),
                rec.get("error_type", "UNKNOWN"),
                rec.get("error_message", ""),
                rec.get("raw_truncated", ""),
            )
            for rec in records
        ]
        conn = self._get_conn()
        with conn:
            cur = conn.cursor()
            _ensure_dlq_table(cur)
            execute_values(cur, """
                INSERT INTO silver._external_dlq
                    (source_name, source_entity, error_type, error_message, raw_record)
                VALUES %s
            """, rows)
            log.info(
                "DLQ: wrote %d rejected records from %s/%s",
                len(rows), self._config.source_name, self._config.source_entity,
            )

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.close()
