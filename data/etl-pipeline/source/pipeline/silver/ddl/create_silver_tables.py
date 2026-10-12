#!/usr/bin/env python3
"""
Create Silver entity tables dynamically from source tenant DBs.

- Connects to all active tenant DBs listed in fieldcraft_provider.public.tenant
- Takes the UNION of all tables across all tenants
- Reads column definitions and PKs from information_schema
- Creates silver.<table> in fieldcraft_silver for every source table
- Metadata columns added to every table: _s_tenantid, _s_is_deleted,
  _s_deleted_at, _s_lsn, _s_op, _s_bronze_ts, _s_silver_ts
- Primary key: (_s_tenantid, <source_pk_col(s)>)
- Safe to re-run: uses CREATE TABLE IF NOT EXISTS
"""
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# psycopg2 imported lazily — source-DB phase is skipped if it's unreachable

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ── Source DB connection (Azure PostgreSQL) ──────────────────────────────────
# __file__ is source/pipeline/silver/ddl/create_silver_tables.py -- climb 5
# levels (ddl -> silver -> pipeline -> source -> datapipeline) to reach
# src/datapipeline/.env.local, not source/pipeline/.env.local (3 levels,
# a nonexistent path -- .exists() silently failing left SILVER_PG_HOST/PORT
# unset and falling back to the localhost:5433 dev-container defaults below
# even when running against a real Azure-hosted Silver Postgres).
_ENV_FILE = Path(__file__).parent.parent.parent.parent.parent / ".env.local"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))

try:
    from shared.env_config.schema import source_creds as _source_creds
    _src = _source_creds()
    SRC_HOST     = _src.host
    SRC_PORT     = _src.port
    SRC_USER     = _src.user
    SRC_PASSWORD = _src.password
    SRC_SSL      = _src.ssl_mode
except Exception:
    SRC_HOST     = os.environ.get("POSTGRES_HOST")
    SRC_PORT     = int(os.environ.get("POSTGRES_PORT", 5432))
    SRC_USER     = os.environ.get("POSTGRES_USER")
    SRC_PASSWORD = os.environ.get("POSTGRES_PASSWORD")
    SRC_SSL      = os.environ.get("POSTGRES_SSL_MODE", "require")

# When true, enable PostgreSQL Row-Level Security on every silver.<entity>
# table and create a silver_reader role that microservices use. Writers
# (silver_writer) are granted BYPASSRLS so ingest is unaffected.
ENABLE_RLS   = os.environ.get("SILVER_ENABLE_RLS", "true").lower() == "true"

# ── Silver PG connection ─────────────────────────────────────────────────────
DST_HOST     = os.environ.get("SILVER_PG_HOST",     "localhost")
DST_PORT     = int(os.environ.get("SILVER_PG_PORT", "5433"))
DST_DB       = os.environ.get("SILVER_PG_DB", "")
DST_USER     = os.environ.get("SILVER_PG_USER",     "silver_writer")
DST_PASSWORD = os.environ.get("SILVER_PG_PASSWORD", "")
DST_SSL      = os.environ.get("SILVER_PG_SSL_MODE", "prefer")
# Admin credentials used only for bootstrap (CREATE DATABASE / CREATE USER).
# Falls back to DST_USER/DST_PASSWORD when not set (e.g. local PG where the
# same user has superuser rights).
DST_ADMIN_USER     = os.environ.get("SILVER_PG_ADMIN_USER",     DST_USER)
DST_ADMIN_PASSWORD = os.environ.get("SILVER_PG_ADMIN_PASSWORD", DST_PASSWORD)

# Source tables ending in _history are application audit/SCD tables and are
# intentionally excluded — Silver mirrors only current-state entities. Point-in-
# time history, if needed, replays from Bronze.
_SKIP_TABLES: frozenset[str] = frozenset()


def _is_history_table(name: str) -> bool:
    return name.endswith("_history")


def _fetch_partition_children(cur) -> set[str]:
    """
    Names of tables that are PARTITIONS of another table (e.g. the monthly
    children of a declaratively-partitioned communication_log). Postgres
    exposes each partition child as its own row in information_schema, with
    the same columns as the parent -- without this exclusion, every child
    gets union'd in as if it were a separate top-level entity, so one source
    table (partitioned into N children) explodes into N+1 Silver tables
    instead of the single mirrored table this pipeline intends to create.
    """
    cur.execute("""
        SELECT relname FROM pg_class WHERE relispartition
    """)
    return {r[0] for r in cur.fetchall()}

# PostgreSQL type mapping: source type → silver type
# We keep types as-is from source; TEXT is the safe fallback
_TYPE_MAP = {
    "integer":                    "INTEGER",
    "bigint":                     "BIGINT",
    "smallint":                   "SMALLINT",
    "numeric":                    "NUMERIC",
    "decimal":                    "NUMERIC",
    "real":                       "REAL",
    "double precision":           "DOUBLE PRECISION",
    "boolean":                    "BOOLEAN",
    "text":                       "TEXT",
    "character varying":          "TEXT",
    "character":                  "TEXT",
    "uuid":                       "TEXT",
    "json":                       "TEXT",
    "jsonb":                      "TEXT",
    "date":                       "DATE",
    "timestamp without time zone":"TIMESTAMPTZ",
    "timestamp with time zone":   "TIMESTAMPTZ",
    "time without time zone":     "TEXT",
    "time with time zone":        "TEXT",
    "bytea":                      "BYTEA",
    "inet":                       "TEXT",
    "cidr":                       "TEXT",
    "macaddr":                    "TEXT",
    "interval":                   "TEXT",
    "bit":                        "TEXT",
    "bit varying":                "TEXT",
    "xml":                        "TEXT",
    "money":                      "NUMERIC",
    "oid":                        "BIGINT",
}


_SRC_CONNECT_TIMEOUT = int(os.environ.get("SOURCE_PG_CONNECT_TIMEOUT_S", "15"))


def _src_conn(dbname: str):
    import psycopg2
    return psycopg2.connect(
        host=SRC_HOST, port=SRC_PORT, user=SRC_USER, password=SRC_PASSWORD,
        dbname=dbname, sslmode=SRC_SSL,
        connect_timeout=_SRC_CONNECT_TIMEOUT,
    )


_PROVIDER_DB = os.environ.get("POSTGRES_DB") or "fieldcraft_provider"


def get_active_tenants() -> list[str]:
    conn = _src_conn(_PROVIDER_DB)
    cur = conn.cursor()
    cur.execute("SELECT tenant_id FROM public.tenant WHERE status = 'Active' ORDER BY id")
    tenants = [r[0] for r in cur.fetchall()]
    conn.close()
    return tenants


# Namespaced (non-tenant) source DBs. Their tables are created as
# silver.{db}_{table} (e.g. silver.fieldcraft_provider_manufacturer,
# silver.fieldcraft_customer_asset) so they never collide with the bare silver.{table}
# names used for tenant DBs — and the full-DB-name prefix avoids clashing with a
# tenant table that happens to start with a short prefix word (e.g. a tenant's
# own `customer_asset`). Events still land with _s_tenantid = the source_db
# Debezium emits. MUST match SILVER_NAMESPACED_SOURCE_DBS in the Silver job's
# universal_silver_merge.py (same default).
NAMESPACED_SOURCE_DBS: tuple[str, ...] = tuple(
    db.strip()
    for db in os.environ.get("SILVER_NAMESPACED_SOURCE_DBS",
                             f"{_PROVIDER_DB},fieldcraft_customer").split(",")
    if db.strip()
)


def fetch_tenant_schema(tenant_id: str) -> tuple[dict[str, list[tuple[str, str]]], dict[str, list[str]]]:
    """
    Fetch all columns and PKs for all tables in one tenant DB using 2 queries total.
    Returns (table -> [(col, pg_type)], table -> [pk_cols])
    """
    conn = _src_conn(tenant_id)
    cur  = conn.cursor()

    partition_children = _fetch_partition_children(cur)

    # All columns for all public tables in one query
    cur.execute("""
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position
    """)
    all_cols: dict[str, list[tuple[str, str]]] = {}
    for table, col, dtype in cur.fetchall():
        if table in _SKIP_TABLES or _is_history_table(table) or table in partition_children:
            continue
        all_cols.setdefault(table, []).append((col, _TYPE_MAP.get(dtype, "TEXT")))

    # All PKs for all public tables in one query
    cur.execute("""
        SELECT tc.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema    = kcu.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema    = 'public'
        ORDER BY tc.table_name, kcu.ordinal_position
    """)
    all_pks: dict[str, list[str]] = {}
    for table, col in cur.fetchall():
        if table in _SKIP_TABLES or _is_history_table(table) or table in partition_children:
            continue
        all_pks.setdefault(table, []).append(col)

    conn.close()
    return all_cols, all_pks


def _fallback_pk(cols: list) -> list[str]:
    """
    When the source table has no formal PRIMARY KEY in information_schema,
    fall back to an ``id`` column if one exists. Without this, every event
    for such a table would be permanently routed to NO_PK DLQ.
    """
    for col, _typ in cols:
        if col == "id":
            return ["id"]
    return []


def _merge_table(union: dict, table: str, cols: list, pks: list, tid: str) -> None:
    if table not in union:
        union[table] = {
            "columns": dict(cols),
            "pks": pks or _fallback_pk(cols),
            "source_tenants": [tid],
        }
        return
    existing = union[table]["columns"]
    for col, typ in cols:
        if col not in existing:
            existing[col] = typ
    union[table]["source_tenants"].append(tid)
    if not union[table]["pks"]:
        union[table]["pks"] = pks or _fallback_pk(cols)


def build_union_schema(tenants: list[str]) -> dict[str, dict]:
    """
    For each table in the union across all tenants, collect:
      - columns: merged dict of col_name -> pg_type (union, first type wins)
      - pks: list of PK columns (from first tenant that has the table)
      - source_tenants: which tenants have this table
    """
    union: dict[str, dict] = {}
    for tid in tenants:
        cols_map, pks_map = fetch_tenant_schema(tid)
        print(f"  {tid}: {len(cols_map)} tables")
        for table, cols in cols_map.items():
            # "tenant" is provider-authoritative (see BASELINE_TABLE_EXCLUDES in
            # shared/env_config/schema.py) — tenant DBs each have their own
            # excluded public.tenant copy with a different schema (tenant_uuid,
            # mobile_no) than fieldcraft_provider.tenant's real one (tenant_id, tenant_uid,
            # phone_no). Unioning it here previously built silver.tenant from the
            # wrong source; the correct schema comes only from add_namespaced_tables()
            # below, which routes it to this same bare "tenant" key.
            if table == "tenant":
                continue
            _merge_table(union, table, cols, pks_map.get(table, []), tid)
    return union


def add_namespaced_tables(union: dict[str, dict], db: str) -> int:
    """Add silver.{db}_{table} entries for one non-tenant source DB into `union`.

    Each namespaced table is standalone (keyed by its prefixed name), so it never
    merges with a tenant's bare table of the same name. Returns the count added.
    Reuses the existing build_ddl / _sync_tables path, which creates silver.<key>.
    """
    cols_map, pks_map = fetch_tenant_schema(db)
    print(f"  {db}: {len(cols_map)} tables (namespaced as {db}_*)")
    for table, cols in cols_map.items():
        # "tenant" from the provider DB is the one exception to namespacing —
        # universal_silver_merge.py's _namespaced_entity() routes it to the bare
        # "tenant" table (not "fieldcraft_provider_tenant") since it's the single
        # source of truth for the tenant roster. DDL must match that routing or
        # the real provider schema (tenant_id, tenant_uid, phone_no) never
        # reaches the table tenant_discovery.py actually queries.
        key = "tenant" if (db == _PROVIDER_DB and table == "tenant") else f"{db}_{table}"
        if key not in union:
            union[key] = {
                "columns": dict(cols),
                "pks": pks_map.get(table, []) or _fallback_pk(cols),
                "source_tenants": [db],
            }
        else:
            existing = union[key]["columns"]
            for col, typ in cols:
                existing.setdefault(col, typ)
    return len(cols_map)


def build_ddl(table: str, schema: dict) -> str:
    cols = schema["columns"]
    pks  = schema["pks"]

    col_defs = [
        "_s_env       TEXT NOT NULL DEFAULT ''",
        "_s_tenantid  TEXT NOT NULL",
    ]

    # Business columns from source (skip tenantid — replaced by _s_tenantid)
    for col, typ in cols.items():
        if col == "tenantid":
            continue
        col_defs.append(f"{col}  {typ}")

    # Metadata columns
    col_defs += [
        "_s_is_deleted  BOOLEAN DEFAULT FALSE",
        "_s_deleted_at  TIMESTAMPTZ",
        "_s_bronze_ts   TIMESTAMPTZ",
        "_s_silver_ts   TIMESTAMPTZ DEFAULT NOW()",
        "_s_lsn         BIGINT",
        "_s_op          TEXT",
    ]

    cols_sql = ",\n    ".join(col_defs)

    if pks:
        pk_sql = ", ".join(pks)
        constraint = f"PRIMARY KEY (_s_env, _s_tenantid, {pk_sql})"
    else:
        # No PK found — _s_env + _s_tenantid together as a non-unique index
        constraint = "UNIQUE (_s_env, _s_tenantid)"

    return (
        f"CREATE TABLE IF NOT EXISTS silver.{table} (\n"
        f"    {cols_sql},\n"
        f"    {constraint}\n"
        f");"
    )



def _get_existing_silver_tables(cur) -> set[str]:
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'silver' AND table_type = 'BASE TABLE'
    """)
    return {r[0] for r in cur.fetchall()}


def _get_existing_columns(cur, table: str) -> set[str]:
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'silver' AND table_name = %s
    """, (table,))
    return {r[0] for r in cur.fetchall()}


def _add_missing_columns(cur, table: str, schema: dict, existing_cols: set[str]) -> int:
    added = 0
    for col, typ in schema["columns"].items():
        if col == "tenantid" or col in existing_cols:
            continue
        try:
            cur.execute(f"ALTER TABLE silver.{table} ADD COLUMN IF NOT EXISTS {col} {typ};")
            print(f"    + Added column: silver.{table}.{col} {typ}")
            added += 1
        except Exception as exc:
            print(f"    ! Could not add column {col} to silver.{table}: {exc}")
    return added


def _create_table(cur, table: str, schema: dict) -> bool:
    """Create a new silver table. Returns True if it has no PK.

    One physical table per entity, across every environment/tenant — _s_env
    (and _s_tenantid) are plain discriminator columns on that single table,
    not a partition key. Do NOT reintroduce PARTITION BY / per-env partition
    tables here: that was the earlier (now-reverted) design, and it multiplied
    every entity into 3 physical tables (<name>, <name>_staging, <name>_default)
    for no benefit — _s_env already lets callers filter/query per environment
    on the one table.
    """
    try:
        cur.execute(build_ddl(table, schema))
        pk_str = ",".join(schema["pks"]) if schema["pks"] else "NO PK"
        tenants_str = ",".join(schema["source_tenants"])
        print(f"  Created: silver.{table:<40} pk=({pk_str})  tenants=[{tenants_str}]")
    except Exception as exc:
        print(f"  ERROR creating silver.{table}: {exc}")
        return not schema["pks"]

    return not schema["pks"]


def _sync_new_table(cur, table: str, schema: dict, no_pk: list) -> int:
    if _create_table(cur, table, schema):
        no_pk.append(table)
    return 1


def _sync_existing_table(cur, table: str, schema: dict) -> int:
    existing_cols = _get_existing_columns(cur, table)
    n = _add_missing_columns(cur, table, schema, existing_cols)
    if n:
        print(f"  Altered: silver.{table} — {n} new column(s)")
        return 1
    return 0


def _sync_tables(cur, union: dict, existing_tables: set) -> tuple:
    created, altered, no_pk = 0, 0, []
    for table, schema in sorted(union.items()):
        if table not in existing_tables:
            created += _sync_new_table(cur, table, schema, no_pk)
        else:
            altered += _sync_existing_table(cur, table, schema)
    return created, altered, no_pk


# (table, index_name, index_ddl) — supports Gold's Eden loaders (load_project_health.py
# and siblings), which LATERAL-join these tables on (_s_tenantid, project_id) /
# task_attribute(task_id). Without them, load_project_health.py's query went
# effectively unbounded (8+ min observed on tn24047750's 286k-row task_attribute
# before these were added ad hoc on the live DB 2026-08-08). Codified here so a
# fresh environment or DB rebuild doesn't silently reintroduce the hang.
_GOLD_SUPPORT_INDEXES = [
    ("task", "idx_task_tenant_project",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_task_tenant_project ON silver.task (_s_tenantid, project_id)"),
    ("task_attribute", "idx_task_attribute_task_id",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_task_attribute_task_id ON silver.task_attribute (task_id)"),
    ("project_estimate", "idx_project_estimate_tenant_project",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_project_estimate_tenant_project ON silver.project_estimate (_s_tenantid, project_id)"),
    ("purchase_order", "idx_purchase_order_tenant_project",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_purchase_order_tenant_project ON silver.purchase_order (_s_tenantid, project_id)"),
    ("invoice", "idx_invoice_tenant_project",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_invoice_tenant_project ON silver.invoice (_s_tenantid, project_id)"),
    ("project_schedule", "idx_project_schedule_tenant_project",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_project_schedule_tenant_project ON silver.project_schedule (_s_tenantid, project_id)"),
    # load_project_health.py's project_estimate_schedule LATERAL join filters on
    # (_s_tenantid, _s_env, estimate_id, _s_is_deleted=false, invalid=false), but
    # this table's only index was its PK (_s_env, _s_tenantid, id) -- id being
    # this table's OWN row id, not estimate_id. Confirmed live 2026-08-18 via
    # EXPLAIN: without this index every lookup bitmap-scanned all of a tenant's
    # project_estimate_schedule rows (cost=438.80) instead of seeking straight to
    # the ~1-2 matching rows (cost=8.33 with this index -- ~52x cheaper), the same
    # "unbounded per-row LATERAL cost" class of bug the other 6 indexes above were
    # added for. Partial index matches the query's own filter predicate exactly,
    # keeping it small.
    ("project_estimate_schedule", "idx_project_estimate_schedule_estimate_lookup",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_project_estimate_schedule_estimate_lookup "
     "ON silver.project_estimate_schedule (_s_tenantid, _s_env, estimate_id) "
     "WHERE _s_is_deleted = FALSE AND invalid = FALSE"),
    # Same missing-index class as project_estimate_schedule above, found in the
    # same audit pass (2026-08-18): load_project_health.py's "freed resource"
    # lookup filters/joins silver.resource_schedule on project_id TWICE (once
    # directly, once via a nested "which other projects share this resource"
    # subquery) with no index beyond the PK (_s_env, _s_tenantid, id). This is
    # the pipeline's 2nd-largest table (292,910 rows on nonprod's staging env)
    # -- likely a larger real-world cost than project_estimate_schedule's fix,
    # just not caught by the one query we happened to EXPLAIN live.
    ("resource_schedule", "idx_resource_schedule_tenant_project",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_resource_schedule_tenant_project "
     "ON silver.resource_schedule (_s_tenantid, _s_env, project_id) "
     "WHERE _s_is_deleted = FALSE"),
    # Same load_project_health.py "reschedule event" lookup joins silver.
    # audit_log on resource_id (a UUID matching silver.project.project_uid, per
    # that query's own comment -- never audit_log.id) with no index beyond the
    # PK (_s_env, _s_tenantid, id).
    ("audit_log", "idx_audit_log_tenant_resource",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_audit_log_tenant_resource "
     "ON silver.audit_log (_s_tenantid, _s_env, resource_id)"),
    # The two indexes below complete the "freed resource" lookup that
    # idx_resource_schedule_tenant_project above only half-covers. Measured on
    # prod tn59695547 2026-08-24 (project 1183, 11 schedule versions):
    # fetch_schedule_history took 8.8-14.6 s and was 95% of the per-project
    # Silver->Gold cost, holding project_health ~5 min behind Silver.
    #
    # (1) The outer freed-resource scan filters resource_schedule on
    #     (status='completed', end_time range, resource_id IN (...)) and on
    #     project_id <> ps.project_id. That last one is an INEQUALITY, so
    #     idx_resource_schedule_tenant_project (which ends in project_id)
    #     cannot narrow past _s_tenantid/_s_env -- every candidate row was
    #     filtered by hand. This index leads with the equality columns and
    #     ends in end_time so the range is served by the index too.
    ("resource_schedule", "idx_resource_schedule_freed_lookup",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_resource_schedule_freed_lookup "
     "ON silver.resource_schedule (_s_tenantid, _s_env, resource_id, end_time) "
     "WHERE _s_is_deleted = FALSE AND status = 'completed'"),
    # (2) The bigger of the two, and invisible until (1) was in place: inside
    #     that lateral, a nested lateral resolves each candidate row's
    #     "latest schedule version" via ORDER BY version DESC LIMIT 1. With
    #     only idx_project_schedule_tenant_project (no version column) every
    #     one of those re-sorted -- 47,690 sorts for a single project,
    #     2,961,518 of the query's 3,148,560 buffer hits (94%). Adding version
    #     DESC to the index turns each into an ordered index seek.
    #
    # Combined effect, same project/query: 8.8-14.6 s -> 1.22-1.27 s
    # (~7-11x), buffer hits 3,148,560 -> 333,401 (9.4x). Applied live via
    # CREATE INDEX CONCURRENTLY the same day; codified here so a rebuild does
    # not silently reintroduce the regression -- the same reason the indexes
    # above this line exist.
    #
    # Residual cost is structural, not an index gap: the lateral still runs
    # ~47,700 times (candidate rows x schedule versions) to return one row per
    # version. Reducing that means restructuring the subquery in
    # load_project_health.py so ORDER BY end_time DESC LIMIT 1 happens BEFORE
    # the joins, not after.
    ("project_schedule", "idx_project_schedule_latest_version",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_project_schedule_latest_version "
     "ON silver.project_schedule (_s_tenantid, _s_env, project_id, version DESC) "
     "WHERE _s_is_deleted = FALSE AND invalid = FALSE"),
    # Same missing-index class as the 9 above, found in a later audit pass
    # (2026-08-24): load_project_health.py's estimate-history confidence/
    # evidence LATERALs (bom_agg/price_hist_agg) and load_estimate_price_
    # evidence.py's bom_agg -- both added 2026-08-01, after the 2026-08-08/18
    # fixes above -- LATERAL-join silver.bom filtered on
    # (_s_tenantid, _s_env, project_id, version) with no index beyond the PK
    # (_s_env, _s_tenantid, id). Every such lookup scanned the tenant's whole
    # BOM table instead of seeking to the one project/version's rows.
    ("bom", "idx_bom_tenant_project_version",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bom_tenant_project_version "
     "ON silver.bom (_s_tenantid, _s_env, project_id, version) "
     "WHERE _s_is_deleted = FALSE AND invalid = FALSE"),
    # Same two loaders' price-at-a-point-in-time lookups (mc_at/mc_prev/
    # "current cost") filter silver.material_cost on vendor_material_map_id
    # + an effective_startdate/effective_enddate window, ORDER BY id DESC
    # LIMIT 1 -- again nothing beyond the PK. Unlike bom (project-scoped),
    # material_cost has no project_id column at all -- it's tenant-wide
    # vendor pricing data -- so scoping the CALLING query per-project (see
    # load_project_health.py/load_estimate_price_evidence.py's run()) bounds
    # the bom side of these joins but does NOT bound material_cost's scan;
    # this index is the only fix for that half.
    ("material_cost", "idx_material_cost_vmm_effective",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_material_cost_vmm_effective "
     "ON silver.material_cost (vendor_material_map_id, effective_startdate DESC, id DESC) "
     "WHERE _s_is_deleted = FALSE"),
]


def _apply_gold_support_indexes(cur, existing_tables: set[str]) -> int:
    """Create indexes required by Gold's Silver-side query patterns.

    Guarded per-table on existence since not every deployment has every entity
    (e.g. a POC subset). Safe to re-run — CREATE INDEX CONCURRENTLY IF NOT
    EXISTS. CONCURRENTLY (not plain CREATE INDEX) matters here specifically:
    these tables are the ones Gold's realtime queries hit hardest
    (resource_schedule alone is 292,910 rows on nonprod), so if this DDL is
    ever re-run against an environment where they already hold production
    data — a redeploy, a DR restore, a new-tenant onboarding full rerun — a
    blocking CREATE INDEX takes a SHARE lock for the whole build and stalls
    every write to that table. That is the same lock/blocking-transaction
    class of incident this file's own indexes exist to fix (see the
    _GOLD_SUPPORT_INDEXES header comment). The caller's connection already
    runs autocommit=True (required for CONCURRENTLY — Postgres refuses it
    inside a transaction block).

    CONCURRENTLY's own failure mode is the reason for the indisvalid check
    below: an interrupted CONCURRENTLY build leaves the index in place but
    marked invalid, and a plain "IF NOT EXISTS" rerun sees the name already
    exists and silently treats that as success — the index is dead weight
    (unused by the planner) and the query it was meant to fix stays slow,
    with no signal anywhere that anything is wrong.
    """
    applied = 0
    for table, index_name, ddl in _GOLD_SUPPORT_INDEXES:
        if table not in existing_tables:
            continue
        try:
            cur.execute(ddl)
            cur.execute(
                "SELECT indisvalid FROM pg_index "
                "WHERE indexrelid = %s::regclass", (f"silver.{index_name}",),
            )
            row = cur.fetchone()
            if row is not None and row[0] is False:
                print(
                    f"  WARN: silver.{table}.{index_name} exists but is INVALID "
                    f"(an earlier CONCURRENTLY build likely failed/was interrupted) "
                    f"-- the planner will not use it; run "
                    f"'DROP INDEX CONCURRENTLY IF EXISTS silver.{index_name};' "
                    f"and re-run this script to rebuild it."
                )
            else:
                print(f"  Index ensured: silver.{table}.{index_name}")
            applied += 1
        except Exception as exc:
            print(f"  WARN: could not create index {index_name} on silver.{table}: {exc}")
    return applied


def _apply_row_level_security(cur, table_names: list[str]) -> None:
    """
    Enable RLS on every silver.<entity> table and create a silver_reader role.

    Tenant isolation contract:
      - silver_writer  has BYPASSRLS; ingest writes are unaffected.
      - silver_reader  is subject to RLS; microservices MUST run
            SET LOCAL app.tenant_id = '<tenantid>';
        at the start of every transaction. SELECTs return zero rows
        until that GUC is set.
      - Policies use _s_tenantid as the discriminator.
    """
    print("\nApplying Row-Level Security (SILVER_ENABLE_RLS=true)...")

    # 1. Reader role used by microservices. Created if missing; safe to re-run.
    # Note: silver_writer is the table owner and bypasses RLS by default (no FORCE
    # ROW LEVEL SECURITY), so ALTER ROLE BYPASSRLS is not needed and would fail on
    # Azure Database for PostgreSQL (requires superuser).
    reader_pwd = (os.environ.get("SILVER_READER_PWD")
                  or os.environ.get("SILVER_READER_PASSWORD"))
    if not reader_pwd:
        raise SystemExit(
            "SILVER_READER_PWD must be set when SILVER_ENABLE_RLS=true. "
            "Generate a strong password (e.g. `openssl rand -base64 32`) "
            "and store it in Azure Key Vault for production."
        )
    # Quote password safely for SQL — escape any embedded single quotes.
    safe_pwd = reader_pwd.replace("'", "''")
    cur.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'silver_reader') THEN
                CREATE ROLE silver_reader NOINHERIT LOGIN PASSWORD '{safe_pwd}';
            END IF;
        END
        $$;
    """)
    cur.execute("GRANT USAGE ON SCHEMA silver TO silver_reader;")
    cur.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA silver "
                "GRANT SELECT ON TABLES TO silver_reader;")

    # 3. Enable RLS + tenant policy on every entity table.
    applied = 0
    skipped = 0
    for table in sorted(table_names):
        # Skip internal pipeline tables (silver._dlq etc.) — they intentionally
        # span all tenants for operator visibility.
        if table.startswith("_"):
            continue
        try:
            cur.execute(f"GRANT SELECT ON silver.{table} TO silver_reader;")
            cur.execute(f"ALTER TABLE silver.{table} ENABLE ROW LEVEL SECURITY;")
            cur.execute(f"DROP POLICY IF EXISTS tenant_isolation ON silver.{table};")
            cur.execute(
                f"CREATE POLICY tenant_isolation ON silver.{table} "
                f"FOR SELECT TO silver_reader "
                f"USING (_s_tenantid = current_setting('app.tenant_id', true));"
            )
            applied += 1
        except Exception as exc:
            skipped += 1
            print(f"  WARN: skipped RLS on silver.{table}: {exc}")

    print(f"  RLS applied to {applied} table(s), skipped {skipped} (ownership). silver_reader role ready.")


def _ensure_database() -> None:
    """Connect to the postgres admin DB and create DST_DB + DST_USER if missing.

    Uses SILVER_PG_ADMIN_USER / SILVER_PG_ADMIN_PASSWORD (falls back to
    DST_USER / DST_PASSWORD). Safe to call on every run — all statements are
    guarded with IF NOT EXISTS or existence checks.
    """
    import psycopg2

    print(f"  Ensuring database '{DST_DB}' and role '{DST_USER}' exist...")
    try:
        admin_conn = psycopg2.connect(
            host=DST_HOST, port=DST_PORT, dbname="postgres",
            user=DST_ADMIN_USER, password=DST_ADMIN_PASSWORD, sslmode=DST_SSL,
        )
        admin_conn.autocommit = True
        cur = admin_conn.cursor()

        # Create role if it doesn't exist
        cur.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s;", (DST_USER,)
        )
        if not cur.fetchone():
            cur.execute(
                f"CREATE USER {DST_USER} WITH PASSWORD %s;", (DST_PASSWORD,)
            )
            print(f"  Created role: {DST_USER}")
        else:
            print(f"  Role already exists: {DST_USER}")

        # Create database if it doesn't exist
        cur.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s;", (DST_DB,)
        )
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{DST_DB}" OWNER {DST_USER};')
            print(f"  Created database: {DST_DB}")
        else:
            print(f"  Database already exists: {DST_DB}")

        # Ensure the role has full privileges on the database
        cur.execute(f'GRANT ALL PRIVILEGES ON DATABASE "{DST_DB}" TO {DST_USER};')

        cur.close()
        admin_conn.close()
    except Exception as exc:
        # Non-fatal: if admin creds aren't available the main connect will
        # surface the real error with a clear message.
        print(f"  WARN: could not bootstrap database/role: {exc}")
        print("        Continuing — database may already exist.")


def main() -> None:
    import psycopg2

    # Allow the multi-env loop below to temporarily redirect these globals so
    # get_active_tenants() / build_union_schema() hit each env's source PG host.
    global SRC_HOST, SRC_PORT, SRC_USER, SRC_PASSWORD, SRC_SSL

    # ── Phase 0: ensure database + role exist ────────────────────────────────
    _ensure_database()

    # ── Phase 1: infrastructure (always runs, no source DB needed) ───────────
    dst_conn = psycopg2.connect(
        host=DST_HOST, port=DST_PORT, dbname=DST_DB,
        user=DST_USER, password=DST_PASSWORD, sslmode=DST_SSL,
    )
    dst_conn.autocommit = True
    cur = dst_conn.cursor()

    cur.execute("CREATE SCHEMA IF NOT EXISTS silver AUTHORIZATION silver_writer;")

    # iceberg-rest's JdbcCatalog (docker-compose.{nonprod,prod}.yml's
    # CATALOG_URI, currentSchema=iceberg_meta) relies entirely on this schema
    # already existing -- its own bootstrap just runs an unqualified
    # `CREATE TABLE` for its internal catalog-tracking table, trusting
    # search_path to route it. A nonexistent schema doesn't fail at JDBC
    # connect time; Postgres only errors on the first unqualified CREATE
    # inside it ("no schema has been selected to create in"), which crash-
    # loops iceberg-rest with no indication the fix is a missing schema, not
    # a config or credentials problem. Confirmed live on the prod VM: this
    # schema was never created by any automated step, only by hand once
    # during local testing -- so every fresh environment hits this same
    # crash-loop until someone notices and creates it manually. Idempotent,
    # same pattern as the silver schema above.
    cur.execute("CREATE SCHEMA IF NOT EXISTS iceberg_meta AUTHORIZATION silver_writer;")

    # Drop any legacy public.silver_* tables from old DDL runs
    cur.execute("""
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public' AND tablename LIKE 'silver_%'
    """)
    for (t,) in cur.fetchall():
        cur.execute(f"DROP TABLE IF EXISTS public.{t} CASCADE;")
        print(f"  Dropped legacy: public.{t}")

    # Internal pipeline tables — always idempotent, no source DB needed
    cur.execute("""
        CREATE TABLE IF NOT EXISTS silver._dlq (
            id           BIGSERIAL PRIMARY KEY,
            entity       TEXT,
            error_type   TEXT,
            error_msg    TEXT,
            raw_payload  TEXT,
            captured_at  TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS silver._cdc_watermark (
            entity      TEXT PRIMARY KEY,
            max_lsn     BIGINT,
            updated_at  TIMESTAMPTZ DEFAULT NOW()
        );
    """)
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
    print("  Created: silver._dlq, silver._cdc_watermark, silver._external_dlq")

    # Per-tenant freshness function — consumed by postgres-exporter to emit
    # pipeline_silver_lag_seconds{entity, tenantid}. Dynamic SQL because Silver
    # entity tables are discovered at runtime, not declared up front.
    cur.execute("""
        CREATE OR REPLACE FUNCTION silver._tenant_freshness()
        RETURNS TABLE(entity TEXT, tenantid TEXT, lag_seconds DOUBLE PRECISION)
        LANGUAGE plpgsql AS $$
        DECLARE
            rec  RECORD;
            qry  TEXT;
        BEGIN
            FOR rec IN
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'silver'
                  AND table_type   = 'BASE TABLE'
                  AND table_name NOT LIKE '\\_%' ESCAPE '\\'
            LOOP
                qry := format(
                    'SELECT %L::text AS entity,
                            _s_tenantid::text AS tenantid,
                            EXTRACT(EPOCH FROM (NOW() - MAX(_s_silver_ts)))::double precision AS lag_seconds
                       FROM silver.%I
                      WHERE _s_silver_ts IS NOT NULL
                      GROUP BY _s_tenantid',
                    rec.table_name, rec.table_name
                );
                RETURN QUERY EXECUTE qry;
            END LOOP;
        END;
        $$;
    """)
    print("  Created: silver._tenant_freshness()")

    # ── Phase 2: entity tables (requires source PostgreSQL) ──────────────────
    if not SRC_HOST or not SRC_USER:
        print("\nWARN: POSTGRES_HOST / POSTGRES_USER not set — skipping entity table creation.")
        print("      Run this script from inside the kafka-connect container to create entity tables:")
        print("      docker exec <kafka-connect-id> python /app/silver/ddl/create_silver_tables.py")
        cur.close()
        dst_conn.close()
        print("\nSilver DDL complete: infrastructure tables only.")
        return

    # Load environments.yaml so we can union schemas across ALL enabled source
    # environments.  Each env may live on a separate PG host and carry tables that
    # are exclusive to it (e.g. si_dev has attribute_config, image_catalogue, etc.
    # not present on dev).  We temporarily redirect the module-level SRC_* globals
    # for each env so the existing get_active_tenants() / build_union_schema()
    # helpers transparently hit the right host.  Falls back to single-env mode when
    # environments.yaml is absent or unreadable.
    _ecfg = None
    _env_names: list[str] | None = None
    try:
        from shared.env_config.schema import load_environments
        from shared.pipeline_config import ENVIRONMENTS_CONFIG_PATH
        _ecfg = load_environments(
            os.environ.get("ENVIRONMENTS_CONFIG_PATH", ENVIRONMENTS_CONFIG_PATH)
        )
        _env_names = [n for n, _ in _ecfg.enabled_envs()]
    except Exception:
        pass

    union: dict = {}

    if _ecfg is not None:
        for env_name, env_spec in _ecfg.enabled_envs():
            print(f"\n  [{env_name}] Connecting to {env_spec.source.host} ...")
            _prev = (SRC_HOST, SRC_PORT, SRC_USER, SRC_PASSWORD, SRC_SSL)
            SRC_HOST     = env_spec.source.host
            SRC_PORT     = env_spec.source.port
            SRC_USER     = env_spec.source.user
            SRC_PASSWORD = env_spec.source.password
            SRC_SSL      = env_spec.source.ssl_mode or "require"
            try:
                env_tenants = get_active_tenants()
                print(f"  [{env_name}] {len(env_tenants)} active tenant(s): {env_tenants}")
                env_union = build_union_schema(env_tenants)
                print(f"  [{env_name}] {len(env_union)} table(s) from tenant DBs")
                for tbl, schema in env_union.items():
                    if tbl not in union:
                        union[tbl] = schema
                    else:
                        for col, typ in schema["columns"].items():
                            union[tbl]["columns"].setdefault(col, typ)
                # Namespaced (non-tenant) DBs — provider + customer — on this env's host.
                for ns_db in NAMESPACED_SOURCE_DBS:
                    try:
                        n = add_namespaced_tables(union, ns_db)
                        print(f"  [{env_name}] + {n} namespaced table(s) from {ns_db}")
                    except Exception as exc:
                        print(f"  [{env_name}] WARN: could not read namespaced DB {ns_db}: {exc}")
            except Exception as exc:
                print(f"  [{env_name}] WARN: could not read source PG: {exc}")
            finally:
                SRC_HOST, SRC_PORT, SRC_USER, SRC_PASSWORD, SRC_SSL = _prev
    else:
        # Single-env fallback: original behaviour when environments.yaml is absent.
        print("\nConnecting to source tenant DBs (single-env mode)...")
        try:
            tenants = get_active_tenants()
            print(f"Active tenants: {tenants}\n")
        except Exception as exc:
            print(f"\nWARN: Cannot reach source PostgreSQL ({SRC_HOST}): {exc}")
            print("      Silver infrastructure tables created. Entity tables skipped.")
            print("      To create entity tables, run from inside kafka-connect container:")
            print("      docker exec <kafka-connect-id> python /app/silver/ddl/create_silver_tables.py")
            cur.close()
            dst_conn.close()
            print("\nSilver DDL complete: infrastructure tables only.")
            return
        print(f"Building tenant union schema across {len(tenants)} tenant DB(s): {tenants}")
        union = build_union_schema(tenants)
        print(f"  Tenant union: {len(union)} bare tables")
        for ns_db in NAMESPACED_SOURCE_DBS:
            try:
                n = add_namespaced_tables(union, ns_db)
                print(f"  + {n} table(s) namespaced from {ns_db} → silver.{ns_db}_*")
            except Exception as exc:
                print(f"  WARN: could not read namespaced DB {ns_db}: {exc}")

    if not union:
        print("\nWARN: No source tables discovered — skipping entity table creation.")
        cur.close()
        dst_conn.close()
        print("\nSilver DDL complete: infrastructure tables only.")
        return

    print(f"\nUnion: {len(union)} tables total (tenant bare + namespaced)\n")
    if _env_names:
        print(f"  Environments feeding this union (discriminated by _s_env, "
              f"not by partition): {_env_names}")

    existing_tables = _get_existing_silver_tables(cur)
    created, altered, no_pk = _sync_tables(cur, union, existing_tables)

    # Create ext_ tables for external connector sources BEFORE RLS is applied
    # below, so silver.ext_* tables get the same RLS policy + silver_reader
    # grant as every other entity table on this same first run (previously
    # this ran after cur/dst_conn were closed, so newly-created ext_* tables
    # never got RLS/silver_reader on a fresh DB — see PR #7999 review).
    ext_config_dir = os.environ.get("EXTERNAL_CONFIG_DIR", "external/config/sources")
    if Path(ext_config_dir).exists():
        ext_conn = psycopg2.connect(
            host=DST_HOST, port=DST_PORT, dbname=DST_DB,
            user=DST_USER, password=DST_PASSWORD, sslmode=DST_SSL,
        )
        ext_conn.autocommit = True
        create_external_silver_tables(ext_conn, config_dir=ext_config_dir)
        ext_conn.close()

    all_tables_for_indexes = _get_existing_silver_tables(cur)
    _apply_gold_support_indexes(cur, all_tables_for_indexes)

    if ENABLE_RLS:
        all_tables = _get_existing_silver_tables(cur)
        _apply_row_level_security(cur, sorted(all_tables))

    cur.close()
    dst_conn.close()

    print(f"\nSilver DDL complete: {created} table(s) created, {altered} table(s) altered.")
    if no_pk:
        print(f"WARNING: {len(no_pk)} tables had no PK — upserts will use _s_tenantid only: {no_pk}")


def create_external_silver_tables(conn, config_dir: str = "external/config/sources") -> int:
    """Create silver.ext_{source}_{entity} tables for every non-example connector config.

    Uses a fixed schema: tenant + external_id primary key + standard Silver metadata
    + payload TEXT. Business fields are stored in payload JSON — no DDL change needed
    when external source schemas evolve.
    Safe to re-run: CREATE TABLE IF NOT EXISTS.
    """
    import yaml
    from pathlib import Path

    log = logging.getLogger(__name__)
    cur = conn.cursor()
    created = 0

    for p in sorted(Path(config_dir).glob("*.yaml")):
        if p.stem.startswith("example_"):
            continue
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  Skipping {p.name}: {exc}")
            continue

        source_name = (raw.get("source_name") or "").replace("-", "_").replace(".", "_")
        entity      = raw.get("source_entity") or ""
        if not source_name or not entity:
            continue

        table = f"ext_{source_name}_{entity}"
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS silver.{table} (
                _s_env           TEXT        NOT NULL DEFAULT '',
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
                PRIMARY KEY (_s_env, _s_tenantid, _s_external_id)
            )
        """)
        print(f"  Created (or verified): silver.{table}")
        created += 1

    conn.commit()
    cur.close()
    print(f"\nExternal Silver DDL complete: {created} table(s).")
    return created


if __name__ == "__main__":
    main()
