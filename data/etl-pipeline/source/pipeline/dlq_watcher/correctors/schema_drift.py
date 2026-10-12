"""
SchemaDriftCorrector — applies missing ADD COLUMN statements to Silver PG.

What it does:
  1. Acquires a Redis lock per (tenantid, entity) so two watcher replicas can't
     race the same ALTER.
  2. Fetches source PG columns (the producer side) and Silver PG columns (the
     consumer side) for the entity in question.
  3. Calls pg_schema_inferrer.diff_schema() to identify missing columns.
  4. Builds ADD COLUMN IF NOT EXISTS statements via generate_alter_statements().
  5. Executes them against `silver-pg` in a single autocommit transaction.
  6. Sets a Redis cache-bust key so Silver's universal_silver_merge.py flushes
     its in-process schema cache on the next event for that entity.

What it does NOT do:
  - DROP COLUMN, ALTER TYPE, or column rename — those decisions stay manual.
    Detected mismatches are still reported in `actions_taken` for visibility.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator

from dlq_watcher.correctors.base import CorrectionOutcome, CorrectionResult

log = logging.getLogger(__name__)

DDL_LOCK_TTL_S       = int(os.environ.get("DLQ_WATCHER_DDL_LOCK_TTL_S", "60"))
SCHEMA_INVALIDATE_TTL_S = int(os.environ.get("DLQ_WATCHER_SCHEMA_INVALIDATE_TTL_S", "1800"))


def _ddl_lock_key(entity: str) -> str:
    return f"dlq:ddl-lock:{entity}"


def _schema_invalidate_key(entity: str) -> str:
    """Silver's _schema() consults this key — sees a value, drops cache."""
    return f"silver:schema:invalidate:{entity}"


class _DDLLockBusy(RuntimeError):
    """Raised when another watcher replica holds the lock."""


@contextmanager
def _redis_ddl_lock(redis_client, entity: str) -> Iterator[None]:
    """SETNX-based distributed lock with TTL safety net."""
    key = _ddl_lock_key(entity)
    acquired = redis_client.set(key, "locked", nx=True, ex=DDL_LOCK_TTL_S)
    if not acquired:
        raise _DDLLockBusy(f"DDL lock busy for entity={entity}")
    try:
        yield
    finally:
        try:
            redis_client.delete(key)
        except Exception as exc:
            log.warning("DDL lock release failed (TTL will reap): %s", exc)


class SchemaDriftCorrector:
    """
    Construct with two psycopg2 connection factories (so tests can inject
    fakes) plus the redis client. Connections are opened per call — the
    corrector runs rarely so the connect overhead is not a hot path.
    """
    corrector_id = "schema_drift.v1"

    def __init__(
        self,
        *,
        source_pg_factory,   # () → psycopg2.connection (source DB; reads info_schema)
        silver_pg_factory,   # () → psycopg2.connection (silver-pg; runs ALTER)
        redis_client,        # redis.Redis instance
        source_schema: str = "public",
    ) -> None:
        self._source_pg_factory = source_pg_factory
        self._silver_pg_factory = silver_pg_factory
        self._redis            = redis_client
        self._source_schema    = source_schema

    def correct(self, envelope: dict) -> CorrectionResult:
        entity   = envelope.get("entity") or ""
        tenantid = envelope.get("tenantid") or ""
        if not entity:
            return CorrectionResult(
                outcome=CorrectionOutcome.REFUSED,
                reason="schema_drift: envelope has no entity",
            )

        try:
            with _redis_ddl_lock(self._redis, entity):
                return self._do_correct(entity, tenantid)
        except _DDLLockBusy as exc:
            # Another replica is fixing the same drift — defer to next attempt.
            return CorrectionResult(
                outcome=CorrectionOutcome.SKIPPED,
                reason=str(exc),
                actions_taken=["ddl_lock_busy"],
            )
        except Exception as exc:
            log.exception("schema_drift corrector failed entity=%s", entity)
            return CorrectionResult(
                outcome=CorrectionOutcome.FAILED,
                reason=f"corrector raised: {exc}",
            )

    def _do_correct(self, entity: str, tenantid: str) -> CorrectionResult:
        # Lazy import so unit tests can construct this class without psycopg2.
        from shared.schema_utils.pg_schema_inferrer import (
            diff_schema,
            fetch_pg_columns,
            generate_alter_statements,
        )

        # 1. Inspect both sides of the boundary.
        # Strip any namespaced DB prefix (e.g. "fieldcraft_provider_manufacturer" → "manufacturer")
        # before querying the source PG, which stores the bare table name.
        _namespaced_dbs = os.environ.get(
            "SILVER_NAMESPACED_SOURCE_DBS", "fieldcraft_provider,fieldcraft_customer"
        ).split(",")
        source_table_name = entity
        for _db in _namespaced_dbs:
            if entity.startswith(_db + "_"):
                source_table_name = entity[len(_db) + 1:]
                break

        src_conn = self._source_pg_factory()
        try:
            source_cols = fetch_pg_columns(source_table_name, self._source_schema, pg_conn=src_conn)
        finally:
            src_conn.close()

        silver_conn = self._silver_pg_factory()
        try:
            silver_cols = fetch_pg_columns(entity, "silver", pg_conn=silver_conn)
        finally:
            silver_conn.close()

        diff = diff_schema(source_cols, silver_cols)
        missing = diff["missing_in_silver"]

        if not missing:
            # Drift was real at write time but is already fixed (race with manual
            # DDL, or a prior watcher run). Replay should now succeed.
            return CorrectionResult(
                outcome=CorrectionOutcome.SKIPPED,
                reason="silver schema already current (no missing columns)",
                actions_taken=["diff: nothing to add"],
            )

        # 2. Apply ADD COLUMN statements. Single autocommit DDL block.
        statements = generate_alter_statements(f"silver.{entity}", missing)
        actions: list[str] = []
        silver_conn2 = self._silver_pg_factory()
        try:
            silver_conn2.autocommit = True
            with silver_conn2.cursor() as cur:
                for stmt in statements:
                    cur.execute(stmt)
                    actions.append(stmt)
        finally:
            silver_conn2.close()

        # 3. Tell Silver to flush its in-process schema cache for this entity
        #    so the replay picks up the new column immediately rather than
        #    waiting for SCHEMA_CACHE_TTL_S (default 600s).
        self._redis.set(
            _schema_invalidate_key(entity),
            str(int(__import__("time").time() * 1000)),
            ex=SCHEMA_INVALIDATE_TTL_S,
        )
        actions.append(f"silver_cache_bust: {_schema_invalidate_key(entity)}")

        # 4. Surface unsafe diffs for operator review even though we did not
        #    auto-fix them — these go into actions_taken so the audit row keeps
        #    the visibility.
        if diff["type_mismatches"]:
            actions.append(f"unsafe (manual): type_mismatches={diff['type_mismatches']}")
        if diff["missing_in_source"]:
            actions.append(f"unsafe (manual): missing_in_source={diff['missing_in_source']}")

        return CorrectionResult(
            outcome=CorrectionOutcome.APPLIED,
            reason=f"added {len(missing)} column(s) to silver.{entity}",
            original_value={"missing_in_silver": [c["name"] for c in missing]},
            corrected_value={"added": [c["name"] for c in missing]},
            actions_taken=actions,
        )
