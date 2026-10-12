"""
FkHoldCorrector — defers a child event until its parent row lands in Silver.

PostgreSQL's foreign_key_violation error message follows a well-defined shape:

    insert or update on table "<child>" violates foreign key constraint "<fk>"
    DETAIL:  Key (<col>)=(<value>) is not present in table "<parent>".

We parse `(<col>)=(<value>)` and `table "<parent>"` from error_message. Then
we probe `silver.<parent>` for `<col> = <value>` AND `_s_tenantid = <tenant>`.

Outcomes:
  APPLIED  — parent exists now; replay the child.
  DEFERRED — parent missing; watcher republishes to DLQ for later reconsideration.
  REFUSED  — error_message did not parse (envelope shape unexpected); park.
  FAILED   — silver-pg query raised; park (defensive — should never happen).
"""
from __future__ import annotations

import logging
import re

from dlq_watcher.correctors.base import CorrectionOutcome, CorrectionResult

log = logging.getLogger(__name__)


# Example error_message captured from psycopg2 23503:
#   'insert or update on table "task" violates foreign key constraint "task_project_id_fkey"
#    DETAIL:  Key (project_id)=(42) is not present in table "project".'
#
# Capture column name + value + parent table.
_KEY_VALUE_RE = re.compile(r"Key\s*\(([^)]+)\)\s*=\s*\(([^)]*)\)", re.IGNORECASE)
_PARENT_TABLE_RE = re.compile(r'not present in table\s+"([^"]+)"', re.IGNORECASE)


def parse_fk_violation(error_message: str) -> tuple[str | None, str | None, str | None]:
    """
    Returns (column_name, value, parent_table). Any element may be None if the
    error message doesn't conform to the expected PG shape.
    """
    if not error_message:
        return None, None, None
    col = val = parent = None
    m = _KEY_VALUE_RE.search(error_message)
    if m:
        col = m.group(1).strip()
        val = m.group(2).strip()
    m = _PARENT_TABLE_RE.search(error_message)
    if m:
        parent = m.group(1).strip()
    return col, val, parent


class FkHoldCorrector:
    """
    Construct with a silver-pg connection factory. Connections are opened per
    probe so we never keep a long-lived connection in the watcher.
    """
    corrector_id = "fk_hold.v1"

    def __init__(self, *, silver_pg_factory) -> None:
        self._silver_pg_factory = silver_pg_factory

    def correct(self, envelope: dict) -> CorrectionResult:
        err_msg  = envelope.get("error_message", "") or ""
        tenantid = envelope.get("tenantid") or ""
        col, val, parent = parse_fk_violation(err_msg)

        if not (col and val and parent):
            return CorrectionResult(
                outcome=CorrectionOutcome.REFUSED,
                reason="fk_hold could not parse PG error_message",
                original_value={"error_message": err_msg[:300]},
            )

        if not tenantid:
            # Never fall back to an unscoped, cross-tenant parent-existence probe:
            # that could satisfy this FK hold using a different tenant's row with
            # the same PK value. Park for human review instead.
            return CorrectionResult(
                outcome=CorrectionOutcome.REFUSED,
                reason="fk_hold refuses unscoped cross-tenant probe: envelope has no tenantid",
                original_value={"missing_parent": {"table": parent, "key": {col: val}}},
            )

        try:
            exists = self._parent_exists(parent, col, val, tenantid)
        except Exception as exc:
            log.exception("fk_hold parent-probe failed parent=%s col=%s", parent, col)
            return CorrectionResult(
                outcome=CorrectionOutcome.FAILED,
                reason=f"silver probe raised: {exc}",
            )

        if exists:
            return CorrectionResult(
                outcome=CorrectionOutcome.APPLIED,
                reason=f"parent silver.{parent}.{col}={val} now present",
                original_value={"missing_parent": {"table": parent, "key": {col: val}}},
                corrected_value={"parent_landed": True},
                actions_taken=[f"probed silver.{parent} ({col}={val}) — found"],
            )

        return CorrectionResult(
            outcome=CorrectionOutcome.DEFERRED,
            reason=f"parent silver.{parent}.{col}={val} still missing",
            original_value={"awaiting_parent": {"table": parent, "key": {col: val}}},
            actions_taken=[f"probed silver.{parent} ({col}={val}) — missing"],
        )

    def _parent_exists(self, parent_table: str, column: str, value: str,
                       tenantid: str) -> bool:
        # Whitelist the parent table and column to keep this SQL injection-safe.
        # Both come from a parsed PG error message — values are quoted by us,
        # identifiers are validated against a strict regex.
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", parent_table):
            raise ValueError(f"unsafe parent_table identifier: {parent_table!r}")
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", column):
            raise ValueError(f"unsafe column identifier: {column!r}")

        conn = self._silver_pg_factory()
        try:
            with conn.cursor() as cur:
                if tenantid:
                    cur.execute(
                        f"SELECT 1 FROM silver.{parent_table} "
                        f"WHERE {column}::text = %s AND _s_tenantid = %s "
                        f"AND (_s_is_deleted IS NULL OR _s_is_deleted = FALSE) LIMIT 1",
                        (value, tenantid),
                    )
                else:
                    cur.execute(
                        f"SELECT 1 FROM silver.{parent_table} "
                        f"WHERE {column}::text = %s "
                        f"AND (_s_is_deleted IS NULL OR _s_is_deleted = FALSE) LIMIT 1",
                        (value,),
                    )
                return cur.fetchone() is not None
        finally:
            conn.close()
