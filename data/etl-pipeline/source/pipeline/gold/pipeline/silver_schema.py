"""Runtime column-existence probe for Silver tables.

During a schema migration window (e.g. ADO PR 7368 — NextGen ASE schema
merged to dev's Postgres but not yet migrated to staging/prod), a Gold
loader's SELECT can reference columns that exist in one env's source but not
another's. Since Silver is one shared table set across all `_s_env`
partitions, a genuinely-missing column breaks the query for every env, not
just the one that lacks it.

Rather than hand-comment fields out of a loader and remember to uncomment
them once the column lands, loaders call `get_silver_columns()` (cached,
mirrors the TTL pattern `SILVER_SCHEMA_CACHE_TTL_S` already uses in the
Silver merge job) and build their SELECT list with `optional_col()`: the real
column if it exists, `NULL AS <col>` otherwise. The moment auto-schema-sync
adds the column, the next loader run picks it up automatically — no code
change, no re-enable step.

`table_exists()` is the same idea one level up, for schema changes bigger
than an added column — e.g. a table being split/renamed (task_additional_info
-> task_attribute + task_payload) — where a loader needs to switch which join
it runs, not just which columns it reads.
"""

from __future__ import annotations

import time

import psycopg2

CACHE_TTL_S = 300  # matches SILVER_SCHEMA_CACHE_TTL_S's default

_cache: dict[str, tuple[float, frozenset[str]]] = {}
_table_cache: dict[str, tuple[float, bool]] = {}


def table_exists(pg_dsn: str, table: str, schema: str = "silver") -> bool:
    """Whether silver.<table> exists at all yet — for schema changes bigger
    than an added column (e.g. a table split/rename, like ADO PR 7368's
    task_additional_info -> task_attribute + task_payload). Cached the same
    way as get_silver_columns().
    """
    key = f"{schema}.{table}"
    now = time.monotonic()
    cached = _table_cache.get(key)
    if cached is not None and now - cached[0] < CACHE_TTL_S:
        return cached[1]

    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = %s",
            (schema, table),
        )
        exists = cur.fetchone() is not None

    _table_cache[key] = (now, exists)
    return exists


def get_silver_columns(pg_dsn: str, table: str, schema: str = "silver") -> frozenset[str]:
    """Return the set of column names that currently exist on silver.<table>.

    Cached per (schema, table) for CACHE_TTL_S seconds so this doesn't add a
    round trip to every row fetched — only to the first query of a batch
    every few minutes.
    """
    key = f"{schema}.{table}"
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and now - cached[0] < CACHE_TTL_S:
        return cached[1]

    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s",
            (schema, table),
        )
        cols = frozenset(row[0] for row in cur.fetchall())

    _cache[key] = (now, cols)
    return cols


def optional_select(table_alias: str, column: str, existing: frozenset[str]) -> str:
    """SELECT-list fragment for a column that may not exist on Silver yet.

    Returns "<alias>.<column>" if the column exists, else "NULL AS <column>"
    so the result column is named "<column>" either way — callers keep using
    row.get("<column>") without caring which case fired. Only valid in a
    SELECT list (the "AS" alias has no meaning as a function argument —
    see optional_value() for use inside e.g. jsonb_build_object()).
    """
    if column in existing:
        return f"{table_alias}.{column}"
    return f"NULL AS {column}"


def optional_value(table_alias: str, column: str, existing: frozenset[str]) -> str:
    """Bare value expression for a column that may not exist on Silver yet.

    Returns "<alias>.<column>" if the column exists, else the literal NULL —
    no alias, since this is meant to be spliced in as a function argument
    (e.g. jsonb_build_object('key', optional_value(...))), where "AS" is not
    valid syntax.
    """
    if column in existing:
        return f"{table_alias}.{column}"
    return "NULL"
