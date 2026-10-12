#!/usr/bin/env python3
"""
Create Bronze Iceberg tables dynamically.

1. bronze.raw_events — unified append-only table for ALL CDC events.
   Schema: fixed 10-column envelope (source_type, op, lsn, ts_ms, tenantid,
   entity, record_id, source_name, payload, _ingested_at).

2. bronze.<entity> — one table per source entity, discovered dynamically
   from active tenant DBs in fieldcraft_provider.public.tenant.
   Business fields derived from information_schema (not hardcoded).
   Common CDC envelope fields prepended to every table.

Safe to re-run: checks both catalog metadata AND backing ADLS blobs.
If a catalog entry exists but its ADLS blobs have been deleted (e.g. after
a manual storage wipe), the table is automatically dropped from the catalog
and recreated — preventing the BlobNotFound error on the next Flink job start.
"""
import logging
import os
import sys
from pathlib import Path

# psycopg2 is only needed when BRONZE_CREATE_PER_ENTITY_TABLES=true — imported lazily below

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.iceberg_utils.catalog_factory import get_catalog
from shared.pipeline_config import PG_SCHEMA
from pyiceberg.schema import Schema
from pyiceberg.types import (
    NestedField, StringType, LongType, IntegerType,
    TimestampType, BooleanType, FloatType, DoubleType, DateType,
)
from pyiceberg.partitioning import PartitionSpec, PartitionField
from pyiceberg.transforms import IdentityTransform
from pyiceberg.exceptions import TableAlreadyExistsError, NoSuchTableError
from azure.core.exceptions import ResourceNotFoundError

# Silence after all imports so pyiceberg/azure loggers are registered
for _noisy in ("azure", "azure.core.pipeline.policies.http_logging_policy", "pyiceberg", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# ── Load .env.local ───────────────────────────────────────────────────────────
# __file__ is source/pipeline/bronze/ddl/create_bronze_tables.py -- climb 5
# levels to reach src/datapipeline/.env.local (3 levels only reaches
# source/pipeline/.env.local, which doesn't exist -- .exists() silently
# no-ops, leaving env vars unset).
_ENV_FILE = Path(__file__).parent.parent.parent.parent.parent / ".env.local"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))

SRC_HOST     = os.environ.get("POSTGRES_HOST")
SRC_PORT     = int(os.environ.get("POSTGRES_PORT", 5432))
SRC_USER     = os.environ.get("POSTGRES_USER")
SRC_PASSWORD = os.environ.get("POSTGRES_PASSWORD")
SRC_SSL      = os.environ.get("POSTGRES_SSL_MODE", "require")

# No table filtering — every source table gets a Bronze schema
_SKIP_TABLES: frozenset[str] = frozenset()

# Per-entity Bronze tables are schema-only (universal_bronze_ingest.py writes only
# to bronze.raw_events). Set this to "true" to create them for downstream tools
# (e.g. dbt, Synapse serverless) that need explicit schema definitions.
_CREATE_PER_ENTITY_TABLES = os.environ.get("BRONZE_CREATE_PER_ENTITY_TABLES", "false").lower() == "true"

# ── PostgreSQL type → Iceberg type mapping ────────────────────────────────────
def _pg_to_iceberg(pg_type: str) -> object:
    t = pg_type.lower()
    if t in ("integer", "int", "int4", "smallint", "int2"):
        return IntegerType()
    if t in ("bigint", "int8", "bigserial"):
        return LongType()
    if t in ("real", "float4"):
        return FloatType()
    if t in ("double precision", "float8"):
        return DoubleType()
    if t == "boolean":
        return BooleanType()
    if t == "date":
        return DateType()
    if t in ("timestamp without time zone", "timestamp with time zone",
             "timestamp", "timestamptz"):
        return TimestampType()
    # text, varchar, uuid, json, jsonb, numeric, inet, etc. → STRING
    return StringType()


# ── Common CDC envelope fields (prepended to every Bronze table) ──────────────
# Field IDs 1-100 reserved for envelope; business fields start at 200.
COMMON_FIELDS = [
    NestedField(1,   "op",           StringType(),    required=True),
    NestedField(2,   "lsn",          LongType(),      required=True),
    NestedField(3,   "ts_ms",        LongType(),      required=True),
    NestedField(4,   "tenantid",     StringType(),    required=True),
    NestedField(5,   "table_name",   StringType(),    required=False),
    NestedField(100, "_ingested_at", TimestampType(), required=False),
]
_COMMON_NAMES = {f.name for f in COMMON_FIELDS}

# ── Unified raw_events schema ─────────────────────────────────────────────────
RAW_EVENTS_SCHEMA = Schema(
    NestedField(1,  "source_type",  StringType(),    required=True),
    NestedField(2,  "op",           StringType(),    required=True),
    NestedField(3,  "lsn",          LongType(),      required=True),
    NestedField(4,  "ts_ms",        LongType(),      required=True),
    NestedField(5,  "tenantid",     StringType(),    required=True),
    NestedField(6,  "entity",       StringType(),    required=True),
    NestedField(7,  "record_id",    StringType(),    required=True),
    NestedField(8,  "source_name",  StringType(),    required=False),
    NestedField(9,  "payload",      StringType(),    required=False),
    NestedField(10, "_ingested_at", TimestampType(), required=False),
    # Logical source environment stamped by InsertField SMT on the Debezium connector.
    # Nullable for backward compat with rows written before multi-env rollout.
    NestedField(11, "_s_env",       StringType(),    required=False),
)


# ── DLQ schema — captures events that fail Bronze validation ─────────────────
# Stores: invalid op codes, missing tenantid, malformed JSON.
# No tenantid partition since tenantid may be absent on malformed events.
RAW_EVENTS_DLQ_SCHEMA = Schema(
    NestedField(1, "rejection_reason", StringType(),    required=True),
    NestedField(2, "rejected_op",      StringType(),    required=False),
    NestedField(3, "topic",            StringType(),    required=True),
    NestedField(4, "tenantid",         StringType(),    required=False),
    NestedField(5, "payload",          StringType(),    required=False),
    NestedField(6, "_ingested_at",     TimestampType(), required=False),
    NestedField(7, "_s_env",           StringType(),    required=False),
)


def _bronze_props() -> dict:
    return {
        "format-version":                            "2",
        "write.target-file-size-bytes":              "134217728",
        "write.metadata.delete-after-commit.enabled":"true",
        "write.metadata.previous-versions-max":      "100",
    }


# ── Dynamic entity discovery from source DBs ─────────────────────────────────

def _get_active_tenants() -> list[str]:
    import psycopg2
    conn = psycopg2.connect(
        host=SRC_HOST, port=SRC_PORT,
        user=SRC_USER, password=SRC_PASSWORD,
        dbname=os.environ.get("PG_DBNAME", ""), sslmode=SRC_SSL,
    )
    cur = conn.cursor()
    cur.execute(
        f"SELECT tenant_id FROM {PG_SCHEMA}.tenant WHERE status='Active' ORDER BY id"
    )
    tenants = [r[0] for r in cur.fetchall()]
    conn.close()
    return tenants


def _discover_entities() -> dict[str, list[NestedField]]:
    """
    Connect to each active tenant DB and collect the union of all tables +
    their column definitions. Returns {entity: [NestedField, ...]} where
    fields start at ID 200 (after the common envelope fields).
    """
    tenants = _get_active_tenants()
    print(f"  Discovering entities from {len(tenants)} active tenant DB(s)...")

    # entity -> {col_name -> pg_type}  (union across all tenants)
    entity_cols: dict[str, dict[str, str]] = {}

    for tid in tenants:
        try:
            conn = psycopg2.connect(
                host=SRC_HOST, port=SRC_PORT,
                user=SRC_USER, password=SRC_PASSWORD,
                dbname=tid, sslmode=SRC_SSL,
            )
            cur = conn.cursor()
            cur.execute("""
                SELECT table_name, column_name, data_type, ordinal_position
                FROM information_schema.columns
                WHERE table_schema = %s
                ORDER BY table_name, ordinal_position
            """, (PG_SCHEMA,))
            for table, col, dtype, ordinal in cur.fetchall():
                if table in _SKIP_TABLES:
                    continue
                if col in _COMMON_NAMES:
                    continue
                entity_cols.setdefault(table, {})
                # First tenant wins; store (type, ordinal) so field IDs are
                # derived from ordinal_position and stay stable across re-runs.
                entity_cols[table].setdefault(col, (dtype, ordinal))
            conn.close()
        except Exception as e:
            print(f"  WARNING: could not connect to tenant DB '{tid}': {e}")

    # Build NestedField lists.
    # Field ID = 200 + ordinal_position (1-based from information_schema) so the
    # same column always gets the same Iceberg field ID regardless of discovery
    # order, making schema evolution safe across job restarts.
    result: dict[str, list[NestedField]] = {}
    for entity, cols in entity_cols.items():
        fields = []
        for col, (dtype, ordinal) in sorted(cols.items(), key=lambda kv: kv[1][1]):
            field_id = 200 + ordinal
            fields.append(NestedField(field_id, col, _pg_to_iceberg(dtype), required=False))
        result[entity] = fields

    print(f"  Discovered {len(result)} entities across all tenant DBs.")
    return result


# ── Storage health probe ──────────────────────────────────────────────────────

def _table_storage_ok_once(cat, identifier: str) -> bool:
    """
    Single storage probe. Return True if the catalog entry has live backing
    blobs on ADLS (or the table is empty and has none to probe).

    Flink raises BlobNotFound when a table exists in the Iceberg REST catalog
    (metadata stored in PG) but its ADLS parquet/manifest files were deleted
    (e.g. after a manual blob-container wipe). Probing plan_files() touches the
    manifest list on ADLS, which catches this before the Flink job starts.

    Only ResourceNotFoundError (Azure's "blob/container not found" signal) is
    treated as confirmation that the blobs are actually gone. Any other
    exception (expired token, network blip, throttling, etc.) is NOT proof of
    deletion — it must never be conflated with "blobs were deleted", or a
    transient error would trigger a destructive drop_table() + recreate-empty.
    Those are logged and re-raised so the caller does not treat them as "stale".
    """
    try:
        tbl = cat.load_table(identifier)
        if tbl.current_snapshot() is None:
            return True  # Empty table — no blobs to probe
        list(tbl.scan(limit=1).plan_files())
        return True
    except ResourceNotFoundError as exc:
        logging.warning(
            "Storage probe found missing blobs for %s: %s", identifier, exc,
        )
        return False
    except Exception as exc:
        logging.warning(
            "Storage probe for %s raised a non-'not-found' error (%s) — treating as "
            "transient, NOT as confirmation of deletion; skipping drop/recreate.",
            identifier, exc,
        )
        raise


def _table_storage_ok(cat, identifier: str) -> bool:
    """
    Return True if the catalog entry has live backing blobs on ADLS.

    Requires TWO consecutive confirmed not-found probes before reporting the
    table as stale, so a single flaky/transient not-found doesn't trigger a
    destructive drop_table() + recreate-empty. Any exception other than a
    confirmed not-found (including on the confirmation probe) means we cannot
    determine whether the blobs are actually gone — that is NEVER treated as
    "deleted"; we log it and skip the drop (return True / "leave alone") rather
    than risk destroying a table over a transient error.
    """
    try:
        first_missing = not _table_storage_ok_once(cat, identifier)
    except Exception:
        logging.warning(
            "Storage probe for %s could not be completed — skipping drop/recreate "
            "this run (leaving existing catalog entry as-is).",
            identifier,
        )
        return True
    if not first_missing:
        return True

    # First probe says missing — confirm with a second probe before declaring stale.
    try:
        second_missing = not _table_storage_ok_once(cat, identifier)
    except Exception:
        logging.warning(
            "Storage probe confirmation for %s could not be completed — skipping "
            "drop/recreate this run (leaving existing catalog entry as-is).",
            identifier,
        )
        return True
    if not second_missing:
        return True

    logging.warning(
        "Storage probe confirmed missing blobs for %s on two consecutive probes — "
        "will drop stale catalog entry and recreate",
        identifier,
    )
    return False


def _drop_and_log(cat, identifier: str) -> None:
    try:
        cat.drop_table(identifier)
        logging.info("Dropped stale catalog entry: %s", identifier)
    except NoSuchTableError:
        pass


# ── Table creation helpers ────────────────────────────────────────────────────

def _create_raw_events(cat) -> None:
    spec = PartitionSpec(
        PartitionField(source_id=5, field_id=1000, transform=IdentityTransform(), name="tenantid"),
        PartitionField(source_id=11, field_id=1001, transform=IdentityTransform(), name="_s_env"),
    )
    try:
        cat.create_table(
            identifier="bronze.raw_events",
            schema=RAW_EVENTS_SCHEMA,
            partition_spec=spec,
            properties=_bronze_props(),
        )
        print("  Created: bronze.raw_events")
    except TableAlreadyExistsError:
        # Table exists — ensure _s_env partition field is present (added in multi-env rollout).
        # Pre-multi-env tables were only partitioned by tenantid; this migration adds _s_env.
        try:
            tbl = cat.load_table("bronze.raw_events")
            existing_part_names = {pf.name for pf in tbl.spec().fields}
            if "_s_env" not in existing_part_names:
                with tbl.update_spec() as upd:
                    upd.add_field("_s_env", IdentityTransform())
                print("  Updated: bronze.raw_events (added _s_env partition field)")
            else:
                print("  Exists:  bronze.raw_events")
        except Exception as exc:
            logging.warning("Could not update partition spec for bronze.raw_events: %s", exc)
            print("  Exists:  bronze.raw_events")


def _create_raw_events_dlq(cat) -> None:
    try:
        cat.create_table(
            identifier="bronze.raw_events_dlq",
            schema=RAW_EVENTS_DLQ_SCHEMA,
            partition_spec=PartitionSpec(),  # unpartitioned — tenantid may be absent
            properties=_bronze_props(),
        )
        print("  Created: bronze.raw_events_dlq")
    except TableAlreadyExistsError:
        print("  Exists:  bronze.raw_events_dlq")


def _create_entity_table(cat, entity: str, fields: list[NestedField]) -> None:
    table_id = f"bronze.{entity}"
    schema   = Schema(*(COMMON_FIELDS + fields))
    spec     = PartitionSpec(
        PartitionField(source_id=4, field_id=1000, transform=IdentityTransform(), name="tenantid"),
    )
    try:
        cat.create_table(
            identifier=table_id,
            schema=schema,
            partition_spec=spec,
            properties=_bronze_props(),
        )
        print(f"  Created: {table_id}  ({len(fields)} business fields)")
    except TableAlreadyExistsError:
        print(f"  Exists:  {table_id}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    cat = get_catalog("bronze")

    try:
        cat.create_namespace("bronze")
    except Exception:
        pass  # already exists

    # Pre-fetch existing tables in one round trip — avoids one Azure call per table
    try:
        existing = {f"{ns}.{tbl}" for ns, tbl in cat.list_tables("bronze")}
    except Exception:
        existing = set()

    # 1. Unified raw_events table (valid events — always)
    if "bronze.raw_events" in existing:
        if _table_storage_ok(cat, "bronze.raw_events"):
            print("  Exists:  bronze.raw_events")
        else:
            print("  Stale:   bronze.raw_events (catalog entry exists but ADLS blobs missing — recreating)")
            _drop_and_log(cat, "bronze.raw_events")
            _create_raw_events(cat)
    else:
        _create_raw_events(cat)

    # 2. DLQ table (invalid/malformed events — always alongside raw_events)
    if "bronze.raw_events_dlq" in existing:
        if _table_storage_ok(cat, "bronze.raw_events_dlq"):
            print("  Exists:  bronze.raw_events_dlq")
        else:
            print("  Stale:   bronze.raw_events_dlq (catalog entry exists but ADLS blobs missing — recreating)")
            _drop_and_log(cat, "bronze.raw_events_dlq")
            _create_raw_events_dlq(cat)
    else:
        _create_raw_events_dlq(cat)

    # 3. Per-entity tables — only when explicitly requested.
    # universal_bronze_ingest.py writes exclusively to bronze.raw_events.
    # Enable with: BRONZE_CREATE_PER_ENTITY_TABLES=true (e.g. for dbt / Synapse schema discovery).
    if _CREATE_PER_ENTITY_TABLES:
        entities = _discover_entities()
        created = skipped = 0
        for entity, fields in sorted(entities.items()):
            table_id = f"bronze.{entity}"
            if table_id in existing:
                if _table_storage_ok(cat, table_id):
                    skipped += 1
                else:
                    print(f"  Stale:   {table_id} (ADLS blobs missing — recreating)")
                    _drop_and_log(cat, table_id)
                    _create_entity_table(cat, entity, fields)
                    created += 1
            else:
                _create_entity_table(cat, entity, fields)
                created += 1
        print(f"\nBronze DDL complete: bronze.raw_events + {len(entities)} entity tables "
              f"({created} created, {skipped} already existed).")
    else:
        print("\nBronze DDL complete: bronze.raw_events only. "
              "Set BRONZE_CREATE_PER_ENTITY_TABLES=true to also create per-entity schema tables.")


if __name__ == "__main__":
    main()
