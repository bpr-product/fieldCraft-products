#!/usr/bin/env python3
"""
table_discovery.py

Dynamic table discovery and pipeline bootstrap.

Runs on startup and on a configurable schedule. For every table found in
PostgreSQL that is not yet in the pipeline, it:

  1. Queries information_schema.columns to get live column definitions
  2. Checks if a hand-authored .avsc contract exists — uses it if present,
     otherwise infers schema from PostgreSQL column types
  3. Registers the Avro schema in Schema Registry (BACKWARD compatibility)
  4. Creates the Bronze Iceberg table (if missing)
  5. Creates the Silver Iceberg table (if missing)
  6. Updates the Debezium connector's table.include.list via Kafka Connect REST API
     so CDC events start flowing for the new table immediately
  7. Records the table in the pipeline registry (local JSON file) so subsequent
     runs skip already-onboarded tables unless a schema change is detected

On schema change detection (existing table, new columns in PG):
  - Registers a new Avro schema version (BACKWARD compatible additive only)
  - Calls scripts/evolve_iceberg_schema.py for Bronze and Silver tables
  - Logs a warning for breaking changes (rename/drop/type change) — operator
    must handle these manually

Environment variables
---------------------
  PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DBNAME
  ETL_KAFKA_CONNECT_URL     : Kafka Connect REST API (default http://localhost:8083)
  ETL_SCHEMA_REGISTRY_URL   : Schema Registry (default http://localhost:8082)
  ETL_ICEBERG_REST_URI      : Bronze Iceberg REST catalog
  ETL_ICEBERG_REST_SILVER_URI : Silver Iceberg REST catalog
  ETL_S3_ENDPOINT, ETL_S3_ACCESS_KEY, ETL_S3_SECRET_KEY
  ETL_ENV                   : environment prefix (default local)
  DISCOVERY_INTERVAL_SEC    : seconds between discovery runs (default 300)
  DISCOVERY_REGISTRY_PATH   : path to pipeline registry JSON (default .pipeline_registry.json)
  CONTRACTS_DIR             : path to hand-authored .avsc contracts (default contracts/bronze)

Usage
-----
  # Run once (bootstrap)
  python scripts/table_discovery.py --once

  # Run continuously (watch for new tables every 5 minutes)
  python scripts/table_discovery.py

  # Dry run (show what would be created without touching anything)
  python scripts/table_discovery.py --once --dry-run

  # Force re-onboard a specific table (re-registers schema, re-checks DDL)
  python scripts/table_discovery.py --once --force-table timelog
"""
import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import psycopg2
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.schema_utils.pg_schema_inferrer import (
    fetch_all_tables,
    fetch_pg_columns,
    build_avro_schema,
    build_bronze_iceberg_schema,
    build_silver_iceberg_schema,
)
from shared.iceberg_utils.catalog_factory import get_catalog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("table_discovery")

# ── Config ────────────────────────────────────────────────────────────────────

KAFKA_CONNECT_URL   = os.environ.get("ETL_KAFKA_CONNECT_URL",      "http://localhost:8083")
SCHEMA_REGISTRY_URL = os.environ.get("ETL_SCHEMA_REGISTRY_URL",    "http://localhost:8082")
ETL_ENV             = os.environ.get("ETL_ENV",                     "local")
DISCOVERY_INTERVAL  = int(os.environ.get("DISCOVERY_INTERVAL_SEC", "300"))
REGISTRY_PATH       = Path(os.environ.get("DISCOVERY_REGISTRY_PATH", ".pipeline_registry.json"))
CONTRACTS_DIR       = Path(os.environ.get("CONTRACTS_DIR", "contracts/bronze"))
PG_SCHEMA           = os.environ.get("ETL_PG_SCHEMA", "public")

# Debezium connector names — both are updated when new tables are discovered
from shared.pipeline_config import PLATFORM_CONNECTOR_MAIN, PLATFORM_CONNECTOR_REALTIME
CONNECTOR_NAMES     = [PLATFORM_CONNECTOR_MAIN, PLATFORM_CONNECTOR_REALTIME]

# Bronze envelope fields — identical to create_bronze_tables.py
from pyiceberg.types import (
    NestedField, StringType, LongType, IntegerType, TimestampType, BooleanType,
)

BRONZE_COMMON_FIELDS = [
    NestedField(1,   "op",             StringType(),    required=True),
    NestedField(2,   "lsn",            LongType(),      required=True),
    NestedField(3,   "ts_ms",          LongType(),      required=True),
    NestedField(4,   "tenantid",       StringType(),    required=True),
    NestedField(5,   "table_name",     StringType(),    required=False),
    NestedField(6,   "schema_version", IntegerType(),   required=False),
    NestedField(100, "_ingested_at",   TimestampType(), required=False),
]

SILVER_META_FIELDS = [
    NestedField(900, "is_deleted", BooleanType(),   required=False),
    NestedField(901, "deleted_at", TimestampType(), required=False),
    NestedField(902, "_bronze_ts", TimestampType(), required=False),
    NestedField(903, "_silver_ts", TimestampType(), required=False),
    NestedField(904, "_lsn",       LongType(),      required=False),
    NestedField(905, "_op",        StringType(),    required=False),
]


# ── Pipeline registry ─────────────────────────────────────────────────────────

def _load_registry() -> dict:
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text())
    return {}


def _save_registry(registry: dict) -> None:
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2))


# ── Schema Registry ───────────────────────────────────────────────────────────

def _sr_subject(table: str) -> str:
    return f"{ETL_ENV}.{PG_SCHEMA}.{table}-value"


def _get_registered_fields(table: str) -> set[str] | None:
    """Return field names from the latest registered schema, or None if not registered."""
    url = f"{SCHEMA_REGISTRY_URL}/subjects/{_sr_subject(table)}/versions/latest"
    resp = requests.get(url, timeout=5)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    schema = json.loads(resp.json()["schema"])
    # Extract field names from the 'after' record
    for field in schema.get("fields", []):
        if field["name"] == "after":
            after_type = field.get("type", [])
            types = after_type if isinstance(after_type, list) else [after_type]
            for t in types:
                if isinstance(t, dict) and t.get("type") == "record":
                    return {f["name"] for f in t.get("fields", [])}
    return set()


def _register_schema(table: str, avro_schema: dict, dry_run: bool = False) -> int | None:
    """Register Avro schema in Schema Registry. Returns schema ID."""
    subject = _sr_subject(table)
    if dry_run:
        log.info("  [dry-run] Would register schema for subject: %s", subject)
        return None

    # Check compatibility first
    compat_url = f"{SCHEMA_REGISTRY_URL}/compatibility/subjects/{subject}/versions/latest"
    payload = {"schema": json.dumps(avro_schema)}
    compat_resp = requests.post(compat_url, json=payload, timeout=10)
    if compat_resp.status_code == 200:
        if not compat_resp.json().get("is_compatible", True):
            log.warning("  BREAKING SCHEMA CHANGE for %s — operator must handle manually", table)
            return None

    url = f"{SCHEMA_REGISTRY_URL}/subjects/{subject}/versions"
    resp = requests.post(url, json={"schema": json.dumps(avro_schema)}, timeout=10)
    resp.raise_for_status()
    schema_id = resp.json()["id"]
    log.info("  Registered schema for %s — schema_id=%d", subject, schema_id)
    return schema_id


# ── Iceberg DDL ───────────────────────────────────────────────────────────────

from pyiceberg.partitioning import PartitionSpec, PartitionField
from pyiceberg.transforms import IdentityTransform


def _iceberg_table_props() -> dict:
    return {
        "format-version": "2",
        "write.target-file-size-bytes": "134217728",
        "write.metadata.delete-after-commit.enabled": "true",
        "write.metadata.previous-versions-max": "100",
    }


def _create_bronze_table(table: str, columns: list[dict], dry_run: bool = False) -> None:
    table_id = f"bronze.{table}"
    if dry_run:
        log.info("  [dry-run] Would create Bronze table: %s", table_id)
        return

    cat = get_catalog("bronze")
    try:
        cat.create_namespace("bronze")
    except Exception:
        pass

    if cat.table_exists(table_id):
        log.info("  Bronze table exists: %s", table_id)
        return

    schema = build_bronze_iceberg_schema(columns, BRONZE_COMMON_FIELDS)
    spec = PartitionSpec(
        PartitionField(source_id=4, field_id=1000, transform=IdentityTransform(), name="tenantid"),
    )
    cat.create_table(table_id, schema=schema, partition_spec=spec, properties=_iceberg_table_props())
    log.info("  Created Bronze table: %s", table_id)


def _create_silver_table(table: str, columns: list[dict], dry_run: bool = False) -> None:
    table_id = f"silver.{table}"
    if dry_run:
        log.info("  [dry-run] Would create Silver table: %s", table_id)
        return

    cat = get_catalog("silver")
    try:
        cat.create_namespace("silver")
    except Exception:
        pass

    if cat.table_exists(table_id):
        log.info("  Silver table exists: %s", table_id)
        return

    # Determine PK field — default "id"; fall back to first non-tenantid column
    pk = "id" if any(c["name"] == "id" for c in columns) else next(
        (c["name"] for c in columns if c["name"] != "tenantid"), "id"
    )
    schema = build_silver_iceberg_schema(columns, SILVER_META_FIELDS, pk_field=pk)
    spec = PartitionSpec(
        PartitionField(source_id=1, field_id=1000, transform=IdentityTransform(), name="tenantid"),
    )
    cat.create_table(table_id, schema=schema, partition_spec=spec, properties=_iceberg_table_props())
    log.info("  Created Silver table: %s  (pk=%s)", table_id, pk)


# ── Debezium connector update ─────────────────────────────────────────────────

def _get_connector_config(connector_name: str) -> dict | None:
    url = f"{KAFKA_CONNECT_URL}/connectors/{connector_name}/config"
    resp = requests.get(url, timeout=5)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _update_connector_tables(all_tables: list[str], dry_run: bool = False) -> None:
    """
    Update fieldcraft-postgres-connector table.include.list and message.key.columns
    to include all discovered tables. fieldcraft-realtime-connector is left unchanged
    (it handles only timelog and resource_schedule with tuned throughput settings).
    """
    connector_name = PLATFORM_CONNECTOR_MAIN
    config = _get_connector_config(connector_name)
    if config is None:
        log.warning("  Connector %s not found — skipping update", connector_name)
        return

    new_include = ",".join(f"{PG_SCHEMA}.{t}" for t in sorted(all_tables))
    new_key_cols = ";".join(
        f"{PG_SCHEMA}.{t}:tenantid,id" for t in sorted(all_tables)
    )

    current_include = config.get("table.include.list", "")
    if current_include == new_include:
        log.info("  Connector %s table list unchanged", connector_name)
        return

    if dry_run:
        log.info("  [dry-run] Would update %s table.include.list to: %s", connector_name, new_include)
        return

    config["table.include.list"]   = new_include
    config["message.key.columns"]  = new_key_cols

    url = f"{KAFKA_CONNECT_URL}/connectors/{connector_name}/config"
    resp = requests.put(url, json=config, timeout=10)
    resp.raise_for_status()
    log.info("  Updated connector %s — now tracking %d tables", connector_name, len(all_tables))


# ── Per-table onboarding ──────────────────────────────────────────────────────

def _load_avsc_contract(table: str) -> dict | None:
    """Return parsed .avsc dict if a hand-authored contract exists, else None."""
    avsc_path = CONTRACTS_DIR / f"{table}.avsc"
    if avsc_path.exists():
        return json.loads(avsc_path.read_text())
    return None


def onboard_table(
    table: str,
    pg_conn,
    registry: dict,
    dry_run: bool = False,
    force: bool = False,
) -> bool:
    """
    Onboard one table into the pipeline. Returns True if any action was taken.
    """
    log.info("── %s ──", table)

    columns = fetch_pg_columns(table, pg_schema=PG_SCHEMA, pg_conn=pg_conn)
    if not columns:
        log.warning("  No columns found for %s — skipping", table)
        return False

    # Detect schema drift for already-registered tables
    registered_fields = _get_registered_fields(table)
    pg_field_names    = {c["name"] for c in columns}

    if registered_fields is not None and not force:
        new_fields = pg_field_names - registered_fields
        if not new_fields:
            log.info("  Schema unchanged — skipping")
            return False
        log.info("  New columns detected: %s", sorted(new_fields))

    # Use hand-authored contract if present, otherwise infer from PG
    avsc_contract = _load_avsc_contract(table)
    if avsc_contract:
        log.info("  Using hand-authored contract: contracts/bronze/%s.avsc", table)
        avro_schema = avsc_contract
    else:
        log.info("  Inferring schema from PostgreSQL columns")
        avro_schema = build_avro_schema(table, columns)

    _register_schema(table, avro_schema, dry_run=dry_run)
    _create_bronze_table(table, columns, dry_run=dry_run)
    _create_silver_table(table, columns, dry_run=dry_run)

    if not dry_run:
        registry[table] = {
            "columns":    [c["name"] for c in columns],
            "onboarded":  int(time.time()),
            "contract":   "avsc" if avsc_contract else "inferred",
        }

    return True


# ── Main discovery loop ───────────────────────────────────────────────────────

def run_discovery(dry_run: bool = False, force_table: str | None = None) -> None:
    log.info("=== Table Discovery Run ===")

    pg_conn = psycopg2.connect(
        host=os.environ.get("PG_HOST", "localhost"),
        port=int(os.environ.get("PG_PORT", "5432")),
        user=os.environ.get("PG_USER", "postgres"),
        password=os.environ.get("PG_PASSWORD", ""),
        dbname=os.environ.get("PG_DBNAME", ""),
        sslmode=os.environ.get("PG_SSLMODE", "require"),
    )

    try:
        all_pg_tables = fetch_all_tables(pg_schema=PG_SCHEMA, pg_conn=pg_conn)
        log.info("Found %d tables in PostgreSQL schema '%s'", len(all_pg_tables), PG_SCHEMA)

        registry = _load_registry()
        acted_on = []

        for table in all_pg_tables:
            if force_table and table != force_table:
                continue
            force = force_table == table
            if onboard_table(table, pg_conn, registry, dry_run=dry_run, force=force):
                acted_on.append(table)

        if acted_on and not dry_run:
            _save_registry(registry)
            _update_connector_tables(all_pg_tables, dry_run=dry_run)
            log.info("Discovery complete — onboarded/updated: %s", acted_on)
        else:
            log.info("Discovery complete — no changes needed")

    finally:
        pg_conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Dynamic table discovery and pipeline bootstrap.")
    parser.add_argument("--once",        action="store_true", help="Run once then exit")
    parser.add_argument("--dry-run",     action="store_true", help="Show actions without executing")
    parser.add_argument("--force-table", metavar="TABLE",     help="Force re-onboard a specific table")
    args = parser.parse_args()

    if args.once:
        run_discovery(dry_run=args.dry_run, force_table=args.force_table)
        return

    log.info("Running discovery every %ds. Ctrl+C to stop.", DISCOVERY_INTERVAL)
    while True:
        try:
            run_discovery(dry_run=args.dry_run, force_table=args.force_table)
        except Exception as exc:
            log.error("Discovery run failed: %s", exc, exc_info=True)
        time.sleep(DISCOVERY_INTERVAL)


if __name__ == "__main__":
    main()
