"""
pg_schema_inferrer.py

Infers Avro + Iceberg schemas directly from live PostgreSQL column definitions.

No .avsc file needed. When a new table appears in PostgreSQL, this module:
  1. Queries information_schema.columns for that table
  2. Maps PostgreSQL types → Avro types → Iceberg types
  3. Returns a ready-to-register Avro schema dict and a pyiceberg Schema

This is the dynamic replacement for hand-authored contracts/bronze/*.avsc files.
For tables that already have a hand-authored .avsc, that contract still takes
precedence (checked by the caller).

PostgreSQL → Avro → Iceberg type mapping
-----------------------------------------
  integer, serial         → int      → IntegerType
  bigint, bigserial       → long     → LongType
  numeric, decimal        → string   → StringType  (decimal.handling.mode=string)
  real, float4            → float    → FloatType
  double precision, float8→ double   → DoubleType
  boolean                 → boolean  → BooleanType
  text, varchar, char     → string   → StringType
  uuid                    → string   → StringType
  json, jsonb             → string   → StringType
  date                    → string   → StringType  (ISO date string)
  timestamp[tz]           → long (timestamp-millis logicalType) → TimestampType
  bytea                   → bytes    → StringType  (base64 string)
  ARRAY, composite, etc.  → string   → StringType  (JSON-serialised)
"""
import os
from typing import Any

from pyiceberg.schema import Schema
from pyiceberg.types import (
    BooleanType, DoubleType, FloatType, IntegerType,
    LongType, NestedField, StringType, TimestampType,
)

# Envelope fields added by Debezium — never inferred from PG columns
_ENVELOPE_FIELDS = frozenset({
    "op", "lsn", "ts_ms", "tenantid", "schema_version",
    "table_name", "_ingested_at",
})

# Canonical PG type names — extracted to avoid string duplication across
# the three type maps below.
_PG_DOUBLE_PRECISION = "double precision"
_PG_TIMESTAMP_NO_TZ  = "timestamp without time zone"
_PG_TIMESTAMP_TZ     = "timestamp with time zone"

# Bronze envelope field IDs (reserved 1–100)
_BRONZE_COMMON_START = 1
# Business field IDs start at 200 (same as existing DDL scripts)
_BUSINESS_FIELD_ID_START = 200
# Silver business field IDs start at 2 (tenantid=1, business=2+, meta=900+)
_SILVER_BUSINESS_FIELD_ID_START = 2


def _pg_type_to_avro(pg_type: str) -> dict[str, Any]:
    """Map a PostgreSQL data type string to an Avro field type definition."""
    t = pg_type.lower().split("(")[0].strip()  # strip precision: varchar(255) → varchar

    if t in ("integer", "int", "int4", "serial", "int2", "smallint", "smallserial"):
        return {"type": ["null", "int"], "default": None}
    if t in ("bigint", "int8", "bigserial", "oid"):
        return {"type": ["null", "long"], "default": None}
    if t in ("real", "float4"):
        return {"type": ["null", "float"], "default": None}
    if t in (_PG_DOUBLE_PRECISION, "float8", "float"):
        return {"type": ["null", "double"], "default": None}
    if t in ("boolean", "bool"):
        return {"type": ["null", "boolean"], "default": None}
    if t in ("timestamp", _PG_TIMESTAMP_NO_TZ, _PG_TIMESTAMP_TZ, "timestamptz"):
        return {"type": ["null", {"type": "long", "logicalType": "timestamp-millis"}], "default": None}
    # Everything else: text, varchar, char, uuid, json, jsonb, numeric, decimal,
    # date, bytea, arrays, composites → string
    return {"type": ["null", "string"], "default": None}


def _pg_type_to_iceberg(pg_type: str):
    """Map a PostgreSQL data type string to a pyiceberg type."""
    t = pg_type.lower().split("(")[0].strip()

    if t in ("integer", "int", "int4", "serial", "int2", "smallint", "smallserial"):
        return IntegerType()
    if t in ("bigint", "int8", "bigserial", "oid"):
        return LongType()
    if t in ("real", "float4"):
        return FloatType()
    if t in (_PG_DOUBLE_PRECISION, "float8", "float"):
        return DoubleType()
    if t in ("boolean", "bool"):
        return BooleanType()
    if t in ("timestamp", _PG_TIMESTAMP_NO_TZ, _PG_TIMESTAMP_TZ, "timestamptz"):
        return TimestampType()
    return StringType()


def fetch_pg_columns(
    table_name: str,
    pg_schema: str = "public",
    pg_conn=None,
) -> list[dict]:
    """
    Query information_schema.columns for a table and return column metadata.

    Returns list of dicts: [{"name": str, "data_type": str, "is_nullable": bool}]

    pg_conn: a psycopg2 connection. If None, creates one from env vars:
        PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DBNAME
    """
    import psycopg2

    own_conn = pg_conn is None
    if own_conn:
        pg_conn = psycopg2.connect(
            host=os.environ.get("PG_HOST", "localhost"),
            port=int(os.environ.get("PG_PORT", "5432")),
            user=os.environ.get("PG_USER", "postgres"),
            password=os.environ.get("PG_PASSWORD", ""),
            dbname=os.environ.get("PG_DBNAME", ""),
        )

    try:
        with pg_conn.cursor() as cur:
            cur.execute("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
            """, (pg_schema, table_name))
            return [
                {
                    "name":        row[0],
                    "data_type":   row[1],
                    "is_nullable": row[2] == "YES",
                }
                for row in cur.fetchall()
                if row[0] not in _ENVELOPE_FIELDS
            ]
    finally:
        if own_conn:
            pg_conn.close()


def fetch_all_tables(pg_schema: str = "public", pg_conn=None) -> list[str]:
    """
    Return all table names in the given PostgreSQL schema.
    Excludes system tables and Debezium internal tables.
    """
    import psycopg2

    own_conn = pg_conn is None
    if own_conn:
        pg_conn = psycopg2.connect(
            host=os.environ.get("PG_HOST", "localhost"),
            port=int(os.environ.get("PG_PORT", "5432")),
            user=os.environ.get("PG_USER", "postgres"),
            password=os.environ.get("PG_PASSWORD", ""),
            dbname=os.environ.get("PG_DBNAME", ""),
        )

    try:
        with pg_conn.cursor() as cur:
            cur.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s
                  AND table_type = 'BASE TABLE'
                  AND table_name NOT LIKE 'debezium_%'
                  AND table_name NOT LIKE 'pg_%'
                ORDER BY table_name
            """, (pg_schema,))
            return [row[0] for row in cur.fetchall()]
    finally:
        if own_conn:
            pg_conn.close()


def build_avro_schema(
    table_name: str,
    columns: list[dict],
    namespace: str = "com.fieldcraft.cdc",
) -> dict:
    """
    Build a full Debezium-style Avro schema dict from PG column metadata.

    The schema wraps business fields in an 'after' record — matching the
    structure that existing Debezium .avsc contracts use.
    """
    after_fields = [
        {
            "name":    col["name"],
            "default": None,
            **_pg_type_to_avro(col["data_type"]),
        }
        for col in columns
    ]

    return {
        "type":      "record",
        "name":      table_name,
        "namespace": namespace,
        "fields": [
            {"name": "op",             "type": "string"},
            {"name": "lsn",            "type": ["null", "long"],   "default": None},
            {"name": "ts_ms",          "type": ["null", "long"],   "default": None},
            {"name": "tenantid",       "type": "string"},
            {"name": "schema_version", "type": ["null", "int"],    "default": None},
            {
                "name": "after",
                "type": [
                    "null",
                    {
                        "type":      "record",
                        "name":      f"{table_name}_after",
                        "namespace": namespace,
                        "fields":    after_fields,
                    }
                ],
                "default": None,
            },
        ],
    }


def build_bronze_iceberg_schema(
    columns: list[dict],
    common_fields: list[NestedField],
) -> Schema:
    """
    Build a Bronze Iceberg Schema from PG column metadata.

    common_fields: the standard Bronze envelope fields (op, lsn, ts_ms,
                   tenantid, table_name, schema_version, _ingested_at).
    """
    common_names = {f.name for f in common_fields}
    fid = _BUSINESS_FIELD_ID_START

    business_fields = []
    for col in columns:
        if col["name"] in common_names:
            continue
        iceberg_type = _pg_type_to_iceberg(col["data_type"])
        business_fields.append(
            NestedField(fid, col["name"], iceberg_type, required=not col["is_nullable"])
        )
        fid += 1

    return Schema(*(common_fields + business_fields))


def build_silver_iceberg_schema(
    columns: list[dict],
    silver_meta_fields: list[NestedField],
    pk_field: str = "id",
) -> Schema:
    """
    Build a Silver Iceberg Schema from PG column metadata.

    tenantid is always field ID 1. Business fields start at ID 2.
    silver_meta_fields (is_deleted, deleted_at, _bronze_ts, etc.) use IDs 900+.
    """
    tenantid_field = NestedField(1, "tenantid", StringType(), required=True)
    fid = _SILVER_BUSINESS_FIELD_ID_START

    business_fields = []
    for col in columns:
        if col["name"] == "tenantid":
            continue
        iceberg_type = _pg_type_to_iceberg(col["data_type"])
        required = (col["name"] == pk_field) or not col["is_nullable"]
        business_fields.append(
            NestedField(fid, col["name"], iceberg_type, required=required)
        )
        fid += 1

    return Schema(*([tenantid_field] + business_fields + silver_meta_fields))


# ── Schema diff + ALTER TABLE generation (used by dlq_watcher) ────────────────

# Maps source PG types → the canonical Silver PG type expression used by
# silver/ddl/create_silver_tables.py. Keep in sync with that file's _TYPE_MAP.
_SILVER_TYPE_MAP = {
    "integer":                    "INTEGER",
    "bigint":                     "BIGINT",
    "smallint":                   "SMALLINT",
    "numeric":                    "NUMERIC",
    "decimal":                    "NUMERIC",
    "real":                       "REAL",
    _PG_DOUBLE_PRECISION:         "DOUBLE PRECISION",
    "boolean":                    "BOOLEAN",
    "text":                       "TEXT",
    "character varying":          "TEXT",
    "character":                  "TEXT",
    "uuid":                       "TEXT",
    "json":                       "TEXT",
    "jsonb":                      "TEXT",
    "date":                       "DATE",
    _PG_TIMESTAMP_NO_TZ:          "TIMESTAMPTZ",
    _PG_TIMESTAMP_TZ:             "TIMESTAMPTZ",
    "bytea":                      "BYTEA",
}


def _silver_type_for(pg_type: str) -> str:
    """Map a source PG type to the Silver column type. Fallback: TEXT."""
    return _SILVER_TYPE_MAP.get(pg_type.lower().strip(), "TEXT")


def diff_schema(
    source_cols: list[dict],
    silver_cols: list[dict],
) -> dict[str, list]:
    """
    Compare source PG vs Silver PG column lists for one entity.

    Returns:
        {
          "missing_in_silver":   [ {name, data_type, is_nullable}, ... ],
          "missing_in_source":   [ str, ... ],          # columns present in Silver but not source
          "type_mismatches":     [ {name, source_type, silver_type}, ... ],
        }

    Only `missing_in_silver` is auto-correctable. The other two are reported
    for operator visibility but NEVER auto-fixed.
    """
    src_by_name    = {c["name"]: c for c in source_cols}
    silver_by_name = {c["name"]: c for c in silver_cols}

    missing_in_silver = [
        c for name, c in src_by_name.items()
        if name not in silver_by_name and name not in _ENVELOPE_FIELDS
    ]
    missing_in_source = [
        name for name in silver_by_name
        if name not in src_by_name and not name.startswith("_s_")
    ]

    type_mismatches = []
    for name, src in src_by_name.items():
        if name in silver_by_name:
            expected = _silver_type_for(src["data_type"])
            actual_raw = silver_by_name[name]["data_type"].lower()
            # Silver stores "timestamp with time zone" for TIMESTAMPTZ — compare loosely.
            if expected == "TIMESTAMPTZ" and "timestamp" in actual_raw:
                continue
            if expected.lower() != actual_raw:
                type_mismatches.append({
                    "name":        name,
                    "source_type": src["data_type"],
                    "silver_type": silver_by_name[name]["data_type"],
                })

    return {
        "missing_in_silver": missing_in_silver,
        "missing_in_source": missing_in_source,
        "type_mismatches":   type_mismatches,
    }


def generate_alter_statements(
    silver_table: str,
    missing_in_silver: list[dict],
) -> list[str]:
    """
    Build ALTER TABLE ... ADD COLUMN statements for the auto-correctable diff
    subset.

    Always emits `ADD COLUMN ... IF NOT EXISTS` so a concurrent watcher run
    that races to apply the same diff is a no-op rather than an error.

    Does NOT emit DROP COLUMN or ALTER TYPE — those decisions stay manual.
    """
    statements = []
    for col in missing_in_silver:
        name      = col["name"]
        pg_type   = col["data_type"]
        silver_ty = _silver_type_for(pg_type)
        statements.append(
            f"ALTER TABLE {silver_table} ADD COLUMN IF NOT EXISTS "
            f"{name} {silver_ty}"
        )
    return statements
