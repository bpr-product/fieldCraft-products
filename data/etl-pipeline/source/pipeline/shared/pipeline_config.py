"""
shared/pipeline_config.py — Single source of truth for all pipeline configuration.

Every script and Flink job imports from here. Nothing is hardcoded in individual files.
All values are driven by environment variables with documented defaults.
"""
import os
from pathlib import Path


# ── PostgreSQL schema captured by Debezium ────────────────────────────────────
# Must match the schema Debezium is configured to capture.
PG_SCHEMA: str = os.environ.get("ETL_PG_SCHEMA", "public")

# ── System/internal tables excluded from schema registration ─────────────────
# These tables exist in every PostgreSQL database (provider + tenant DBs) but
# carry no domain data and must not be registered in Schema Registry or Bronze.
SKIP_TABLES: frozenset[str] = frozenset({
    "flyway_schema_history",
    "debezium_heartbeat",
    "invoice_id",
})

# ── Internal Silver pipeline tables excluded from freshness / row-count checks ─
# These are pipeline control tables (DLQ, watermark), NOT source data tables.
# They have no _s_silver_ts column and are excluded only from health-check queries.
_default_internal = "_dlq,_dlq_corrections,_external_dlq,_cdc_watermark,_upsert_dlq,raw_events,raw_events_dlq,tenant_configuration"
INTERNAL_SILVER_TABLES: frozenset[str] = frozenset(
    t.strip()
    for t in os.environ.get("ETL_INTERNAL_SILVER_TABLES", _default_internal).split(",")
    if t.strip()
)

# ── Debezium connector naming ─────────────────────────────────────────────────
PLATFORM_CONNECTOR_MAIN:     str = os.environ.get("CONNECTOR_MAIN",     "fieldcraft-postgres-connector")
PLATFORM_CONNECTOR_REALTIME: str = os.environ.get("CONNECTOR_REALTIME", "fieldcraft-realtime-connector")
TENANT_CONNECTOR_PREFIX:     str = os.environ.get("CONNECTOR_TENANT_PREFIX", "fieldcraft-tenant-")

# ── Replication slot names (must match what Debezium created in PostgreSQL) ───
REPLICATION_SLOT_MAIN:     str = os.environ.get("PG_REPLICATION_SLOT_MAIN",     "fieldcraft_debezium_slot")
REPLICATION_SLOT_REALTIME: str = os.environ.get("PG_REPLICATION_SLOT_REALTIME", "fieldcraft_realtime_slot")
REPLICATION_SLOTS: list[str] = [REPLICATION_SLOT_MAIN, REPLICATION_SLOT_REALTIME]

# ── SLA / alerting thresholds ─────────────────────────────────────────────────
SLOT_LAG_WARN_BYTES:      int = int(os.environ.get("ALERT_SLOT_LAG_WARN_BYTES",  str(500  * 1024 * 1024)))
SLOT_LAG_CRIT_BYTES:      int = int(os.environ.get("ALERT_SLOT_LAG_CRIT_BYTES",  str(2048 * 1024 * 1024)))
SILVER_FRESHNESS_WARN_S:  int = int(os.environ.get("ALERT_SILVER_WARN_S",  "30"))
SILVER_FRESHNESS_CRIT_S:  int = int(os.environ.get("ALERT_SILVER_CRIT_S",  "300"))
DIGEST_INTERVAL_S:        int = int(os.environ.get("DIGEST_INTERVAL_S",    "1800"))

# ── Multi-environment config ──────────────────────────────────────────────────
# ETL_ENV = deployment tier: local | nonprod | prod
#   Selects the Event Hubs hub ({tier}.cdc.events), consumer groups,
#   Silver PostgreSQL server, and ADLS account.
#   NOT the same as the logical environment (_s_env per message).
# Default resolves relative to THIS file's location (source/pipeline/shared/
# -> source/pipeline/config/environments.yaml), not process cwd -- the
# Dockerfile's WORKDIR is /app (== src/datapipeline/), one level above
# source/pipeline/, so a bare "config/environments.yaml" silently fails to
# resolve there. ENVIRONMENTS_CONFIG_PATH env override still takes precedence.
ENVIRONMENTS_CONFIG_PATH: str = os.environ.get(
    "ENVIRONMENTS_CONFIG_PATH",
    str(Path(__file__).resolve().parent.parent / "config" / "environments.yaml"),
)

# ── Kafka consumer group names ────────────────────────────────────────────────
# Suffixed with ETL_ENV by default -- dev/nonprod/prod can share one physical
# Azure Event Hubs namespace (confirmed 2026-08-14), so an environment-agnostic
# group.id would make dev's and nonprod's consumers join the SAME consumer
# group and silently split each other's partitions/messages via Kafka's normal
# rebalance protocol -- no error, just each side quietly missing data. Topic
# names are already ETL_ENV-scoped (e.g. f"{ETL_ENV}.cdc.events"); group IDs
# previously were not, which defeated that isolation for anything using these.
_ETL_ENV_FOR_GROUPS: str = os.environ.get("ETL_ENV", "local")
CONSUMER_GROUP_BRONZE:    str = os.environ.get("CONSUMER_GROUP_BRONZE",   f"flink-bronze-sql-v1-{_ETL_ENV_FOR_GROUPS}")
CONSUMER_GROUP_SILVER:    str = os.environ.get("CONSUMER_GROUP_SILVER",   f"silver-universal-consumer-{_ETL_ENV_FOR_GROUPS}")
CONSUMER_GROUP_ONBOARDING:str = os.environ.get("CONSUMER_GROUP_ONBOARDING",f"tenant-onboarding-consumer-{_ETL_ENV_FOR_GROUPS}")
