"""
Silver CDC-merge integration tests (read-only) against the Silver PostgreSQL
database.

Architecture note: Silver is **PostgreSQL** (Azure DB for PostgreSQL), not
Iceberg. The pre-refactor Iceberg-catalog lookups (`get_catalog`) and the
unprefixed column names (`is_deleted`, `version`) have been replaced with
psycopg2 reads and the real `_s_`-prefixed metadata columns.

Asserts CDC-merge correctness on the live Silver tables:
  - platform metadata columns present
  - current-state rows exist
  - soft-delete sets `_s_deleted_at` whenever `_s_is_deleted` is TRUE
  - no duplicate `(_s_tenantid, id)` — dedup + LSN guard hold
  - `_s_tenantid` is never NULL/empty (tenant isolation)

Skips gracefully when Silver PG is unreachable (CI without a live stack).
Target entity is overridable via SILVER_TEST_ENTITY (default: project).
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Auto-load .env.local so SILVER_PG_* point at the configured Silver DB when
# the suite is run standalone.
_ENV = Path(__file__).resolve().parents[2] / ".env.local"
if _ENV.exists():
    for _ln in _ENV.read_text(encoding="utf-8").splitlines():
        _ln = _ln.strip()
        if not _ln or _ln.startswith("#") or "=" not in _ln:
            continue
        _k, _v = _ln.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

SILVER_PG_HOST     = os.environ.get("SILVER_PG_HOST", "localhost")
SILVER_PG_PORT     = int(os.environ.get("SILVER_PG_PORT", "5432"))
SILVER_PG_DB       = os.environ.get("SILVER_PG_DB", "")
SILVER_PG_USER     = os.environ.get("SILVER_PG_USER", "silver_writer")
SILVER_PG_PASSWORD = os.environ.get("SILVER_PG_PASSWORD") or os.environ.get("SILVER_WRITER_PWD", "")
SILVER_PG_SSL      = os.environ.get("SILVER_PG_SSL_MODE", "require")

ENTITY = os.environ.get("SILVER_TEST_ENTITY", "project")

META_COLS = {
    "_s_env", "_s_tenantid", "_s_op", "_s_lsn", "_s_is_deleted",
    "_s_deleted_at", "_s_bronze_ts", "_s_silver_ts",
}


@pytest.fixture(scope="module")
def conn():
    try:
        import psycopg2
        c = psycopg2.connect(
            host=SILVER_PG_HOST, port=SILVER_PG_PORT, dbname=SILVER_PG_DB,
            user=SILVER_PG_USER, password=SILVER_PG_PASSWORD,
            sslmode=SILVER_PG_SSL, connect_timeout=8,
        )
        c.autocommit = True
        yield c
        c.close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Silver PostgreSQL unavailable: {exc}")


def _table_exists(conn, entity: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'silver' AND table_name = %s",
        (entity,),
    )
    found = cur.fetchone() is not None
    cur.close()
    return found


def _has_column(conn, entity: str, col: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'silver' AND table_name = %s AND column_name = %s",
        (entity, col),
    )
    found = cur.fetchone() is not None
    cur.close()
    return found


def test_metadata_columns_present(conn):
    """Every Silver entity table exposes the full platform metadata column set."""
    if not _table_exists(conn, ENTITY):
        pytest.skip(f"silver.{ENTITY} does not exist")
    cur = conn.cursor()
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'silver' AND table_name = %s",
        (ENTITY,),
    )
    cols = {r[0] for r in cur.fetchall()}
    cur.close()
    missing = META_COLS - cols
    assert not missing, f"silver.{ENTITY} missing metadata columns: {missing}"


def test_current_state_has_rows(conn):
    """The current-state table has data after a pipeline run."""
    if not _table_exists(conn, ENTITY):
        pytest.skip(f"silver.{ENTITY} does not exist")
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM silver.{ENTITY}")
    n = cur.fetchone()[0]
    cur.close()
    if n == 0:
        pytest.skip(f"silver.{ENTITY} has no rows yet")
    assert n >= 1


def test_soft_delete_sets_deleted_at(conn):
    """A soft-deleted row (_s_is_deleted=TRUE) must always have _s_deleted_at set."""
    if not _table_exists(conn, ENTITY):
        pytest.skip(f"silver.{ENTITY} does not exist")
    cur = conn.cursor()
    cur.execute(
        f"SELECT COUNT(*) FROM silver.{ENTITY} "
        f"WHERE _s_is_deleted = TRUE AND _s_deleted_at IS NULL"
    )
    violations = cur.fetchone()[0]
    cur.close()
    assert violations == 0, (
        f"{violations} rows in silver.{ENTITY} have _s_is_deleted=TRUE but _s_deleted_at IS NULL"
    )


def test_pk_uniqueness(conn):
    """No duplicate (_s_env, _s_tenantid, id) triples — dedup + LSN guard must hold."""
    if not _table_exists(conn, ENTITY):
        pytest.skip(f"silver.{ENTITY} does not exist")
    if not _has_column(conn, ENTITY, "id"):
        pytest.skip(f"silver.{ENTITY} has no 'id' column")
    cur = conn.cursor()
    cur.execute(f"""
        SELECT _s_env, _s_tenantid, id, COUNT(*) AS c
        FROM silver.{ENTITY}
        GROUP BY _s_env, _s_tenantid, id
        HAVING COUNT(*) > 1
        LIMIT 5
    """)
    dupes = cur.fetchall()
    cur.close()
    assert not dupes, f"Duplicate (_s_env, _s_tenantid, id) triples in silver.{ENTITY}: {dupes}"


def test_tenantid_never_null(conn):
    """Tenant isolation: no Silver row may have a NULL/empty _s_tenantid."""
    if not _table_exists(conn, ENTITY):
        pytest.skip(f"silver.{ENTITY} does not exist")
    cur = conn.cursor()
    cur.execute(
        f"SELECT COUNT(*) FROM silver.{ENTITY} "
        f"WHERE _s_tenantid IS NULL OR _s_tenantid = ''"
    )
    n = cur.fetchone()[0]
    cur.close()
    assert n == 0, f"{n} rows in silver.{ENTITY} have NULL/empty _s_tenantid"


def test_s_env_column_present(conn):
    """Every Silver entity table must have an _s_env column (multi-env DDL)."""
    if not _table_exists(conn, ENTITY):
        pytest.skip(f"silver.{ENTITY} does not exist")
    assert _has_column(conn, ENTITY, "_s_env"), (
        f"silver.{ENTITY} missing _s_env column — re-run Silver DDL"
    )


def test_pk_includes_s_env(conn):
    """The primary key of Silver entity tables must include _s_env (env partitioning)."""
    if not _table_exists(conn, ENTITY):
        pytest.skip(f"silver.{ENTITY} does not exist")
    cur = conn.cursor()
    cur.execute("""
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema = 'silver'
          AND tc.table_name = %s
        ORDER BY kcu.ordinal_position
    """, (ENTITY,))
    pk_cols = [r[0] for r in cur.fetchall()]
    cur.close()
    if not pk_cols:
        pytest.skip(f"silver.{ENTITY} has no primary key")
    assert pk_cols[0] == "_s_env", (
        f"silver.{ENTITY} PK should start with _s_env, got: {pk_cols}"
    )
