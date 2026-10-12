"""
universal_bronze_ingest.py  —  Flink Table API (SQL) approach.

Reads all {env}.* CDC topics and appends to bronze.raw_events using the native
Flink Iceberg connector via SQL INSERT INTO. Under Path A (Event Hubs
consolidation) internal CDC arrives on the single {env}.cdc.events hub; the
discovery pattern is broad ({env}.*) so it works for both the consolidated hub
and any external {env}.* topics.

Topics are discovered dynamically from Kafka at startup — no hardcoded list.
Only topics that actually exist are subscribed to, avoiding metadata failures
for topics with no data yet.
"""
import logging
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from confluent_kafka.admin import AdminClient
from pyflink.datastream import StreamExecutionEnvironment, CheckpointingMode
from pyflink.datastream.checkpoint_storage import FileSystemCheckpointStorage
from pyflink.table import StreamTableEnvironment

from shared.pipeline_config import CONSUMER_GROUP_BRONZE

log = logging.getLogger(__name__)

# Flink JARs — resolve from FLINK_LIB_DIR env var, defaulting to the actual
# container path (infra/docker/flink-python/Dockerfile bakes them into
# /opt/flink/lib/, not ~/.flink/lib/, which never existed in any deployed
# environment -- these JARs were silently skipped everywhere, every time,
# since FLINK_LIB_DIR was never set anywhere and the fallback path was wrong).
_FLINK_LIB = Path(os.environ.get("FLINK_LIB_DIR", "/opt/flink/lib"))
# Glob prefixes, not exact filenames -- the Dockerfile pins exact versions
# (e.g. iceberg-flink-runtime-1.19-1.6.0.jar) that a version bump would
# silently break again if matched by exact name.
_REQUIRED_JAR_GLOBS = [
    "iceberg-flink-runtime-*.jar",
    "flink-sql-connector-kafka-*.jar",
    "iceberg-azure-bundle-*.jar",  # ADLSFileIO + Azure SDK pre-shaded to avoid classloader conflicts with Flink's Jackson/Netty
]

BOOTSTRAP        = os.environ.get("ETL_KAFKA_BOOTSTRAP_SERVERS",   "localhost:9092")
ICEBERG_URI      = os.environ.get("ETL_ICEBERG_REST_URI",           "http://localhost:8181")
AZURE_ACCOUNT    = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME",     "")
AZURE_KEY        = os.environ.get("AZURE_STORAGE_ACCOUNT_KEY",      "")
AZURE_CONTAINER  = os.environ.get("AZURE_STORAGE_CONTAINER_BRONZE", "bronze")
ETL_ENV          = os.environ.get("ETL_ENV",                        "local")
# Kafka startup mode — default earliest-offset (full replay). Set to
# 'latest-offset' for a clean re-snapshot run when the hub still holds stale
# messages we want Bronze to skip.
BRONZE_SCAN_STARTUP_MODE = os.environ.get("BRONZE_SCAN_STARTUP_MODE", "earliest-offset")
CHECKPOINT_PATH  = os.environ.get("BRONZE_CHECKPOINT_PATH",         "file:///tmp/flink-checkpoints/bronze/")

# Topics to exclude: DLQ topics, pipeline control topics, schema-change topics
_EXCLUDE_TOPIC = re.compile(r'\.dlq$|\.pipeline\.|schema-changes\.')


def _discover_topics(wait_min: int = 3, poll_s: int = 10) -> list[str]:
    """
    Return all CDC topic names under this ETL_ENV.

    Matches any `{env}.*` topic. Under Path A consolidation that is the single
    `{env}.cdc.events` hub (all entities/tenants routed there; entity is read
    per-message from the `source_table` field), plus any external `{env}.*`
    topics. Excludes DLQ topics, pipeline control topics, and schema-change
    topics. Waits up to wait_min minutes for at least one topic to appear.
    """
    from shared.kafka_utils.auth import confluent_kafka_props
    admin    = AdminClient({"bootstrap.servers": BOOTSTRAP, **confluent_kafka_props()})
    # Any topic under this ETL_ENV that isn't internal
    pattern  = re.compile(rf"^{re.escape(ETL_ENV)}\..+$")
    deadline = time.time() + wait_min * 60

    while True:
        topics = [
            name for name in admin.list_topics(timeout=30).topics.keys()
            if pattern.match(name) and not _EXCLUDE_TOPIC.search(name)
        ]

        if topics:
            log.info(
                "Discovered %d Kafka topics under %s (provider + %d tenant DB(s))",
                len(topics), ETL_ENV,
                len({t.split(".")[1] for t in topics if t.count(".") >= 3}),
            )
            return sorted(topics)

        if time.time() >= deadline:
            raise RuntimeError(
                f"No {ETL_ENV}.* topics found after {wait_min} min — "
                "is Debezium running and has it finished snapshotting?"
            )
        log.info("No topics yet — waiting %ds for Debezium snapshot...", poll_s)
        time.sleep(poll_s)


def run() -> None:
    # ── Stream env + checkpointing ───────────────────────────────────────────
    env = StreamExecutionEnvironment.get_execution_environment()

    # Verify required JARs are present (they load onto the classpath at JVM
    # startup via the standard FLINK_HOME/lib mechanism -- no add_jars() call
    # needed for that, and none is made here: on PyFlink 1.19.1, calling
    # env.add_jars() before StreamTableEnvironment.create()/execute_sql()
    # corrupts the Table API's own jars/classpath config -- CREATE CATALOG
    # then fails with an unrelated-looking YAML parse error ("expected
    # '<document start>', but found ('<scalar>',)") because add_jars()'s
    # appended value collides with PyFlink's own pre-existing python.files
    # default. Reproduced in isolation: identical failure with just
    # add_jars()+execute_sql() and no Iceberg/Kafka involved at all; removing
    # the add_jars() call (this JAR is already on the classpath via lib/)
    # was the only combination that let CREATE CATALOG succeed.
    for jar_glob in _REQUIRED_JAR_GLOBS:
        matches = sorted(_FLINK_LIB.glob(jar_glob)) if _FLINK_LIB.is_dir() else []
        if matches:
            log.info("Found required JAR on classpath: %s", matches[-1].name)
        else:
            log.error("REQUIRED JAR NOT FOUND (matching %s in %s) -- Iceberg/Kafka "
                       "SQL connectors will be unavailable and table DDL/INSERT will "
                       "fail with 'Cannot find table' errors.", jar_glob, _FLINK_LIB)

    env.enable_checkpointing(60_000, CheckpointingMode.EXACTLY_ONCE)
    env.get_checkpoint_config().set_min_pause_between_checkpoints(30_000)
    env.get_checkpoint_config().set_tolerable_checkpoint_failure_number(3)
    env.get_checkpoint_config().set_checkpoint_storage(
        FileSystemCheckpointStorage(CHECKPOINT_PATH)
    )

    t_env = StreamTableEnvironment.create(env)

    # ── Iceberg catalog (REST → Azure Blob Storage) ──────────────────────────
    # ADLSFileIO is bundled inside iceberg-flink-runtime.jar and talks directly
    # to Azure without any Hadoop dependency. HadoopFileIO was removed because
    # it requires the full Hadoop ecosystem on the classpath.
    #
    # Dual-mode ADLS auth, selected by whether AZURE_STORAGE_ACCOUNT_KEY is set:
    #   - key present (local dev) → shared-key auth (unchanged behaviour)
    #   - key absent  (prod/AKS)  → ADLSFileIO falls back to the Azure SDK
    #     DefaultAzureCredential chain, which transparently picks up AKS
    #     Workload/Managed Identity (AZURE_CLIENT_ID / AZURE_TENANT_ID /
    #     AZURE_FEDERATED_TOKEN_FILE are injected into the pod). No account
    #     key lives on the pod — closes the hadoop-conf key-on-disk finding.
    if AZURE_KEY:
        # LOCAL DEV ONLY — shared-key injected as ADLSFileIO catalog properties.
        # ADLSFileIO does not read core-site.xml (that path is HadoopFileIO).
        # Flink logs the full DDL at INFO and the WebUI can expose it, so this
        # key is visible to anyone with Flink access on the local machine.
        # Prod path (AZURE_KEY absent) uses DefaultAzureCredential via VM
        # Managed Identity — no key present on the pod. Never set AZURE_KEY in
        # docker-compose.prod.yml or Key Vault; the else-branch covers prod.
        adls_auth_sql = (
            f",\n            'adls.auth.shared-key.account.name' = '{AZURE_ACCOUNT}'"
            f",\n            'adls.auth.shared-key.account.key'  = '{AZURE_KEY}'"
        )
        log.info("ADLS auth mode: shared-key (account=%s)", AZURE_ACCOUNT)
    else:
        # No key → DefaultAzureCredential (Workload/Managed Identity). The account
        # is derived from each table's abfss:// location, so no property is needed.
        adls_auth_sql = ""
        log.info("ADLS auth mode: DefaultAzureCredential (Workload/Managed Identity) — no account key on pod")

    catalog_sql = f"""
        CREATE CATALOG bronze_catalog WITH (
            'type'         = 'iceberg',
            'catalog-type' = 'rest',
            'uri'          = '{ICEBERG_URI}',
            'io-impl'      = 'org.apache.iceberg.azure.adlsv2.ADLSFileIO'{adls_auth_sql}
        )
    """
    try:
        t_env.execute_sql(catalog_sql)
    except Exception as exc:
        # AZURE_KEY (when set — local dev only) is embedded verbatim in
        # catalog_sql above. PyFlink/Flink's own exception messages on a
        # failed CREATE CATALOG echo the full submitted DDL back, so a naive
        # `log.error(..., exc)` here would leak the shared key into logs even
        # though we never explicitly log catalog_sql ourselves. Mask it before
        # it ever reaches a log line, matching how AZURE_KEY is already kept
        # out of the log.info() lines above.
        masked_msg = str(exc)
        if AZURE_KEY:
            masked_msg = masked_msg.replace(AZURE_KEY, "***")
        log.error("CREATE CATALOG bronze_catalog failed: %s", masked_msg)
        raise RuntimeError(f"CREATE CATALOG bronze_catalog failed: {masked_msg}") from None

    # ── One Kafka source table per entity, then UNION + INSERT ───────────────
    # Build a single Kafka source that reads raw JSON strings from all topics.
    # We use a single source table with value as STRING and extract fields via
    # JSON_VALUE in SQL — no Python beam worker involved at all.

    discovered = _discover_topics()
    if not discovered:
        raise RuntimeError(f"No {ETL_ENV}.* CDC topics found (expected {ETL_ENV}.cdc.events) — is Debezium running?")
    topics = ";".join(discovered)

    # Build extra Kafka SASL/SSL WITH options if Event Hubs auth is enabled.
    # Each property is added as 'properties.<key>' so it flows through to the
    # underlying Kafka consumer. No-op for local PLAINTEXT.
    from shared.kafka_utils.auth import flink_kafka_props
    kafka_props = dict(flink_kafka_props())
    # scan.startup.mode=group-offsets (the compose default) has no fallback of
    # its own: a partition with no prior committed offset throws
    # NoOffsetForPartitionException and crash-loops the whole job forever --
    # observed 2026-08-08 on a stale local.public.analytical_data-0 partition
    # left over from pre-consolidation topic naming. auto.offset.reset gives
    # every startup mode a safe default (earliest = no data loss) instead of
    # a permanent restart loop.
    kafka_props.setdefault("auto.offset.reset", "earliest")
    extra_props_sql = "".join(
        f",\n            'properties.{k}' = '{v}'"
        for k, v in kafka_props.items()
    )

    t_env.execute_sql(f"""
        CREATE TEMPORARY TABLE kafka_raw (
            raw_json  STRING,
            topic     STRING METADATA FROM 'topic',
            `offset`  BIGINT METADATA FROM 'offset',
            proc_time AS PROCTIME()
        ) WITH (
            'connector'                = 'kafka',
            'topic'                    = '{topics}',
            'properties.bootstrap.servers' = '{BOOTSTRAP}',
            'properties.group.id'      = '{CONSUMER_GROUP_BRONZE}',
            'scan.startup.mode'        = '{BRONZE_SCAN_STARTUP_MODE}',
            'format'                   = 'raw',
            'value.format'             = 'raw'{extra_props_sql}
        )
    """)

    # ── StatementSet: valid events → raw_events; invalid → raw_events_dlq ──────
    # Both sinks run in the same Flink job so exactly-once covers both tables.
    stmt = t_env.create_statement_set()

    # Sink 1: valid CDC events (op known, tenantid present)
    stmt.add_insert_sql("""
        INSERT INTO bronze_catalog.bronze.raw_events
        SELECT
            COALESCE(JSON_VALUE(raw_json, '$.source_type'), 'internal')  AS source_type,
            JSON_VALUE(raw_json, '$.op')                                  AS op,
            -- COALESCE(TRY_CAST(...), 0): a non-numeric lsn/ts_ms must become 0,
            -- never throw and never NULL. Plain CAST raises NumberFormatException
            -- which, with scan.startup.mode=earliest-offset, poison-pills the whole
            -- Bronze job into a permanent restart loop; bare TRY_CAST yields NULL
            -- which then violates the NOT NULL column. The raw payload is preserved.
            COALESCE(TRY_CAST(COALESCE(JSON_VALUE(raw_json, '$.lsn'),
                          JSON_VALUE(raw_json, '$.source_lsn'), '0') AS BIGINT), 0)  AS lsn,
            COALESCE(TRY_CAST(COALESCE(JSON_VALUE(raw_json, '$.ts_ms'),
                          JSON_VALUE(raw_json, '$.source_ts_ms'), '0') AS BIGINT), 0) AS ts_ms,
            -- tenantid: explicit field > source_db (DB name = tenant_id) > '_provider_'
            COALESCE(
                NULLIF(JSON_VALUE(raw_json, '$.tenantid'), ''),
                NULLIF(JSON_VALUE(raw_json, '$.source_db'),  ''),
                '_provider_'
            )                                                              AS tenantid,
            -- Path A: entity comes from the source_table field (Debezium add.fields)
            -- now that all tables are routed to one consolidated topic. Falls back
            -- to source_entity (external) then the topic's last segment (legacy
            -- per-entity topics) so the job works during migration.
            COALESCE(
                NULLIF(JSON_VALUE(raw_json, '$.source_table'), ''),
                NULLIF(JSON_VALUE(raw_json, '$.source_entity'), ''),
                REGEXP_EXTRACT(topic, '[^.]+$', 0)
            )                                                              AS entity,
            CONCAT(
                COALESCE(
                    NULLIF(JSON_VALUE(raw_json, '$.tenantid'), ''),
                    NULLIF(JSON_VALUE(raw_json, '$.source_db'),  ''),
                    '_provider_'
                ),
                ':',
                COALESCE(
                    JSON_VALUE(raw_json, '$.id'),
                    JSON_VALUE(raw_json, '$.log_id'),
                    JSON_VALUE(raw_json, '$.resource_schedule_id'),
                    JSON_VALUE(raw_json, '$.external_id'),
                    'unknown'
                )
            )                                                              AS record_id,
            COALESCE(JSON_VALUE(raw_json, '$.source_name'), 'debezium')   AS source_name,
            raw_json                                                       AS payload,
            CURRENT_TIMESTAMP                                              AS _ingested_at,
            -- Logical source env stamped by InsertField SMT; NULL for pre-multi-env events
            JSON_VALUE(raw_json, '$._s_env')                               AS _s_env
        FROM kafka_raw
        WHERE JSON_VALUE(raw_json, '$.op') IN ('c', 'u', 'd', 'r')
          -- Must stay the exact negation of Sink 2's MISSING_TENANT_ID predicate
          -- below. A valid-op event with no tenantid/source_db and
          -- source_type<>'external' is a legitimate _provider_-level event (see
          -- the tenantid COALESCE above) -- without this exclusion it satisfied
          -- BOTH sinks' WHERE clauses simultaneously and was written to
          -- raw_events (tenantid defaulted to '_provider_') AND independently
          -- to raw_events_dlq as MISSING_TENANT_ID, double-counting every
          -- genuine provider-level event and causing DLQ-replay tooling to
          -- reprocess events that already landed successfully.
          AND NOT (
              COALESCE(JSON_VALUE(raw_json, '$.tenantid'), '') = ''
              AND COALESCE(JSON_VALUE(raw_json, '$.source_db'), '') = ''
              AND COALESCE(JSON_VALUE(raw_json, '$.source_type'), '') <> 'external'
          )
    """)

    # Sink 2: DLQ — captures every event that fails Bronze validation.
    # rejection_reason values are aligned with shared.dlq.errors.ErrorType so
    # the same vocabulary is used across Bronze (Iceberg) and Silver (Kafka)
    # DLQs:
    #   MALFORMED_JSON     — op field is NULL (JSON_VALUE returned NULL = unparseable)
    #   INVALID_OP         — op present but not one of c/u/d/r
    #   MISSING_TENANT_ID  — valid op but tenantid absent or empty
    stmt.add_insert_sql("""
        INSERT INTO bronze_catalog.bronze.raw_events_dlq
        SELECT
            CASE
                WHEN JSON_VALUE(raw_json, '$.op') IS NULL
                    THEN 'MALFORMED_JSON'
                WHEN JSON_VALUE(raw_json, '$.op') NOT IN ('c', 'u', 'd', 'r')
                    THEN 'INVALID_OP'
                ELSE 'MISSING_TENANT_ID'
            END                                                            AS rejection_reason,
            JSON_VALUE(raw_json, '$.op')                                   AS rejected_op,
            topic                                                          AS topic,
            COALESCE(JSON_VALUE(raw_json, '$.tenantid'), '')               AS tenantid,
            raw_json                                                       AS payload,
            CURRENT_TIMESTAMP                                              AS _ingested_at,
            JSON_VALUE(raw_json, '$._s_env')                               AS _s_env
        FROM kafka_raw
        WHERE JSON_VALUE(raw_json, '$.op') IS NULL
           OR JSON_VALUE(raw_json, '$.op') NOT IN ('c', 'u', 'd', 'r')
           OR (
               COALESCE(JSON_VALUE(raw_json, '$.tenantid'), '') = ''
               AND COALESCE(JSON_VALUE(raw_json, '$.source_db'), '') = ''
               AND COALESCE(JSON_VALUE(raw_json, '$.source_type'), '') <> 'external'
           )
    """)

    stmt.execute()


if __name__ == "__main__":
    run()
