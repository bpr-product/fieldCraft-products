"""
universal_silver_merge.py — single Flink pipeline for all Silver CDC merges.

One Kafka source reads all entity topics. A single flatMap routes each event
to the correct Silver PostgreSQL table by entity name extracted from the topic.
Runs in one task slot — no per-entity parallelism overhead.

Topic rediscovery: a background thread polls Kafka every TOPIC_POLL_INTERVAL_S
seconds. When it finds topics not present in the current run it sets a shared
Event, which causes the Flink job to exit cleanly. The outer restart loop in
main() then relaunches the job with the expanded topic list.
"""

import json
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from shared.kafka_utils.topic_naming import dlq_topic as _dlq_topic
from shared.dlq.errors import ErrorType, build_envelope, classify_pg_exception
from pyflink.common import WatermarkStrategy, Types
from pyflink.common.serialization import SimpleStringSchema
from pyflink.datastream import StreamExecutionEnvironment, CheckpointingMode
from pyflink.datastream.checkpoint_storage import FileSystemCheckpointStorage
from pyflink.datastream.connectors.kafka import (
    KafkaSource,
    KafkaOffsetsInitializer,
    KafkaOffsetResetStrategy,
)
from pyflink.datastream.functions import KeyedProcessFunction, MapFunction, RuntimeContext
from pyflink.datastream.state import ListStateDescriptor

log = logging.getLogger(__name__)


TOPIC_POLL_INTERVAL_S = int(os.environ.get("SILVER_TOPIC_POLL_INTERVAL_S", "300"))
# Schema is re-fetched from information_schema after this many seconds idle per entity.
SCHEMA_CACHE_TTL_S = int(os.environ.get("SILVER_SCHEMA_CACHE_TTL_S", "600"))
# Events older than this threshold (wall-clock) are routed to DLQ. 0 = disabled.
LATE_EVENT_THRESHOLD_MS = int(os.environ.get("SILVER_LATE_EVENT_THRESHOLD_MS", str(5 * 60 * 1000)))
# When true, abort the job at startup if any discovered Kafka entity has no
# matching Silver PostgreSQL table. When false, log a warning and continue.
# Default false in production: strict=true aborts the entire Silver job when any
# entity has no Silver table, taking down all entities and all tenants. Route
# missing-table events to DLQ instead and let the rest of the job continue.
# Set to true locally to catch DDL gaps fast during development.
SCHEMA_DRIFT_STRICT = os.environ.get("SILVER_SCHEMA_DRIFT_STRICT", "false").lower() == "true"
# Timeout in seconds to block on each DLQ produce ack. Higher = slower DLQ writes
# but stronger guarantee that a bad event isn't silently dropped.
DLQ_PRODUCE_TIMEOUT_S = int(os.environ.get("SILVER_DLQ_PRODUCE_TIMEOUT_S", "10"))
# Transient PG errors (connection drop, deadlock, lock_timeout) are retried with
# exponential backoff before being DLQ'd. Caps the total wait per event so a
# permanently-broken PG doesn't stall the FlatMap indefinitely.
PG_RETRY_MAX_ATTEMPTS = int(os.environ.get("SILVER_PG_RETRY_MAX_ATTEMPTS", "3"))
PG_RETRY_BASE_DELAY_MS = int(os.environ.get("SILVER_PG_RETRY_BASE_DELAY_MS", "200"))
# Batch size for execute_values upserts. 100 records per round trip vs 1 achieves
# ~100x throughput improvement on high-latency Azure PG connections, allowing the
# first Flink checkpoint to complete before the 30-minute timeout for large
# entities (task: 130k events, audit_log: 94k events).
WRITE_BATCH_SIZE = int(os.environ.get("SILVER_WRITE_BATCH_SIZE", "100"))
# Max time a buffered event waits before being flushed, even if the batch isn't
# full. Without this, low-volume (tenant, entity) subtasks would hold events in
# the in-memory buffer until 100 accumulate — breaking the Silver freshness SLA
# and risking loss on restart (buffer is not checkpointed state).
FLUSH_INTERVAL_MS = int(os.environ.get("SILVER_FLUSH_INTERVAL_MS", "2000"))
# Per-row CDC logging is a hot-path killer during the initial snapshot: it formats
# and writes one INFO line PER ROW (millions of rows), starving the Flink task
# thread so it can't call Kafka poll() often enough — the Event Hubs connection
# goes idle, the broker drops it, in-flight fetches are cancelled, and throughput
# collapses to ~0. Default OFF; the batch upsert itself already happened. Set
# SILVER_LOG_EVERY_ROW=true only for low-volume debugging.
LOG_EVERY_ROW = os.environ.get("SILVER_LOG_EVERY_ROW", "false").lower() == "true"

BOOTSTRAP = os.environ.get("ETL_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
ETL_ENV = os.environ.get("ETL_ENV", "local")
CHECKPOINT_PATH = os.environ.get("SILVER_CHECKPOINT_PATH", "file:///tmp/flink-checkpoints/silver/")
PG_HOST = os.environ.get("SILVER_PG_HOST", "localhost")
PG_PORT = int(os.environ.get("SILVER_PG_PORT", "5433"))
PG_DB = os.environ.get("SILVER_PG_DB", "")
PG_USER = os.environ.get("SILVER_PG_USER", "silver_writer")
PG_PASSWORD = os.environ.get("SILVER_PG_PASSWORD", "")
PG_SSL_MODE = os.environ.get("SILVER_PG_SSL_MODE", "prefer")

# Parallelism for the Silver FlatMap (SilverRoutingProcess, the Postgres-
# writing operator) AND the job's default parallelism -- env.set_parallelism()
# applies job-wide, not just to this operator, so this value governs every
# operator that doesn't have its own explicit .set_parallelism() override.
# Each parallel subtask of the write-side operator opens its own psycopg2
# connection — keep this <= the silver-pg connection pool size
# (PGBOUNCER_DEFAULT_POOL_SIZE in docker-compose.local.yml). Deliberately NOT
# the knob for Kafka-source read parallelism -- see SOURCE_PARALLELISM below,
# a genuinely separate setting scoped only to the Kafka source operator, so
# raising Kafka-side concurrency doesn't also multiply Postgres connection
# count or every other operator in this job's graph.
SILVER_PARALLELISM = int(os.environ.get("SILVER_PARALLELISM", "2"))

# Parallelism for JUST the Kafka source operator (env.from_source(...) below)
# -- lets Silver's consumer use more of a topic's partitions in parallel
# without touching SILVER_PARALLELISM (the write-side/job-default setting
# above). Bounded above by the topic's actual partition count regardless of
# this value (Kafka never runs more parallel consumer subtasks than there
# are partitions to read from).
SOURCE_PARALLELISM = int(os.environ.get("SILVER_SOURCE_PARALLELISM", "2"))

# Source-database namespacing. Tables from these databases are written to
# prefixed Silver tables (silver.{source_db}_{table}) so they never collide
# with the bare silver.{table} names used for tenant DBs. The prefix is the
# full source DB name (e.g. fieldcraft_provider, fieldcraft_customer) — using the full name
# avoids clashing with a tenant table that happens to start with a short
# prefix word (e.g. a tenant's own `customer_asset` table). Any source DB NOT
# listed here (i.e. tenant DBs) keeps its bare table name, unified across
# tenants by _s_tenantid. Configurable via SILVER_NAMESPACED_SOURCE_DBS.
NAMESPACED_SOURCE_DBS = frozenset(
    db.strip()
    for db in os.environ.get("SILVER_NAMESPACED_SOURCE_DBS", "fieldcraft_provider,fieldcraft_customer").split(",")
    if db.strip()
)


def _namespaced_entity(source_db: str, table: str) -> str:
    """Resolve a raw source table name to its Silver entity (table) name.

    Tables from a namespaced source DB get a {source_db}_ prefix; everything
    else (tenant DBs) keeps the bare table name. The returned value is used as
    the routing key, schema-cache key, and the silver.<name> table — so it must
    exactly match the table that create_silver_tables.py builds.

    Exception: fieldcraft_provider.tenant is the sole source of truth for the tenant
    registry (see CLAUDE.md "Tenant registry — single source of truth") and is
    deliberately NOT namespaced — it must land in bare silver.tenant, which is
    what gold/pipeline/tenant_discovery.py reads. Namespacing it would silently
    route provider tenant data to silver.fieldcraft_provider_tenant instead, leaving
    silver.tenant permanently unpopulated by live CDC (found 2026-07-03).
    """
    if table == "tenant" and source_db == "fieldcraft_provider":
        return table
    if source_db and source_db in NAMESPACED_SOURCE_DBS:
        return f"{source_db}_{table}"
    return table


_VALID_OPS = frozenset({"c", "u", "d", "r", "t"})


class FatalConfigError(RuntimeError):
    """Configuration-level error that must not be auto-restarted by main()."""


def _list_entities() -> list[str]:
    """Return internal entity names = Silver base tables (excluding _internal and ext_ tables).

    Path A (Event Hubs consolidation): entities are no longer derivable from Kafka
    topic names — all internal CDC is routed to one consolidated `{env}.cdc.events`
    hub. The source of truth for 'which entities exist' is the Silver schema in
    PostgreSQL. Per-event routing still reads entity from the message `source_table`
    field (see `_route_key` / `_silver_table_name`); this list is only used for
    startup logging, the schema-drift check, and the new-entity poll watcher.
    """
    import psycopg2

    conn = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASSWORD,
        sslmode=PG_SSL_MODE,
        connect_timeout=5,
        keepalives=1,
        keepalives_idle=15,
        keepalives_interval=5,
        keepalives_count=3,
    )
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='silver' AND table_type='BASE TABLE'"
        )
        names = [r[0] for r in cur.fetchall()]
        cur.close()
    finally:
        conn.close()
    return sorted(n for n in names if not n.startswith("_") and not n.startswith("ext_"))


def _discover_entities(wait_min: int = 3, poll_s: int = 10) -> list[str]:
    deadline = time.time() + wait_min * 60
    while True:
        try:
            entities = _list_entities()
        except Exception as exc:
            entities = []
            log.warning("Silver entity discovery (PG) failed: %s", exc)
        if entities:
            log.info("Discovered %d Silver entities: %s", len(entities), entities)
            return entities
        if time.time() >= deadline:
            raise RuntimeError(
                f"No silver.* entity tables found after {wait_min} min — " "run create_silver_tables.py first"
            )
        log.info("No Silver entity tables yet — waiting %ds...", poll_s)
        time.sleep(poll_s)


def _start_topic_watcher(known: set[str], new_topics_event: threading.Event) -> list[threading.Thread]:
    """
    Starts two background threads that both set new_topics_event when new
    Kafka topics appear, triggering a clean Silver job restart:

    1. onboarding-listener — subscribes to local.pipeline.tenant_onboarded.
       Reacts within seconds when tenant_onboarding_job.py signals a new tenant.
       This is the primary real-time path.

    2. topic-poll-watcher — polls Kafka metadata every TOPIC_POLL_INTERVAL_S.
       Safety net for topics that appear outside the onboarding pipeline
       (manual connector deploy, migration, etc.).
    """
    from shared.kafka_utils.auth import confluent_kafka_props

    auth_props = confluent_kafka_props()
    onboarded_topic = f"{ETL_ENV}.pipeline.tenant_onboarded"

    def _onboarding_listener():
        from confluent_kafka import Consumer, KafkaError

        consumer = Consumer(
            {
                "bootstrap.servers": BOOTSTRAP,
                # Suffixed with ETL_ENV -- dev/nonprod/prod can share one
                # physical Event Hubs namespace; an env-agnostic group.id
                # would join dev's and nonprod's onboarding listeners into the
                # same consumer group and silently split tenant-onboarded
                # signals between them.
                "group.id": f"silver-onboarding-listener-{ETL_ENV}",
                "auto.offset.reset": "latest",
                "enable.auto.commit": "true",
                **auth_props,
            }
        )
        consumer.subscribe([onboarded_topic])
        log.info("Onboarding listener subscribed to %s", onboarded_topic)
        try:
            while not new_topics_event.is_set():
                msg = consumer.poll(timeout=2.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() != KafkaError._PARTITION_EOF:
                        log.warning("Onboarding listener error: %s", msg.error())
                    continue
                try:
                    event = json.loads(msg.value().decode("utf-8"))
                    tenant_id = event.get("tenant_id", "unknown")
                    log.info("Onboarding signal received for tenant %s — signalling job restart", tenant_id)
                except Exception:
                    log.info("Onboarding signal received — signalling job restart")
                new_topics_event.set()
        finally:
            consumer.close()

    def _poll_watcher():
        # Path A: new tenants/entities no longer create Kafka topics (all CDC is
        # one consolidated hub). New *entity types* show up as new Silver tables,
        # so the watcher polls PG for new entity tables and restarts to pick them up.
        while not new_topics_event.is_set():
            time.sleep(TOPIC_POLL_INTERVAL_S)
            if new_topics_event.is_set():
                break
            try:
                current = set(_list_entities())
                added = current - known
                if added:
                    log.info("Poll watcher: new Silver entity tables %s — signalling job restart", sorted(added))
                    new_topics_event.set()
            except Exception as exc:
                log.warning("Entity poll watcher failed: %s", exc)

    threads = [
        threading.Thread(target=_onboarding_listener, name="onboarding-listener", daemon=True),
        threading.Thread(target=_poll_watcher, name="topic-poll-watcher", daemon=True),
    ]
    for t in threads:
        t.start()
    return threads


class _DlqRoutingSink(MapFunction):
    """
    Routes DLQ events to per-entity Kafka topics using a confluent_kafka producer.

    Input string format: 'DLQ|{kafka_topic}|{json_payload}'

    Correctness contract (revised after the per-event flush version was found
    to block Silver throughput beyond what is operationally viable):

      - Producer is idempotent (enable.idempotence=true, acks=all) so retries
        cannot produce duplicates within a session.
      - produce() is asynchronous — buffered in the producer's internal queue
        with linger.ms batching. poll(0) on every map() invocation services
        delivery callbacks without blocking.
      - The on-delivery callback records any failed acks in _delivery_errors.
        The next map() invocation checks the queue and RAISES on accumulated
        errors, which fails the Flink task → no checkpoint → no offset commit
        → event is re-read on restart.
      - close() does a bounded flush(timeout=DLQ_PRODUCE_TIMEOUT_S) to surface
        any in-flight delivery failures during graceful task shutdown.

    Known small gap: if a DLQ produce is sitting in the producer's buffer
    when the task dies suddenly (e.g., kill -9), and the checkpoint has
    already advanced past the source event, that DLQ message can be lost.
    The window is at most `linger.ms` (20 ms) — far better than the
    pre-Phase-1 silent-drop-on-failure, but not as tight as a native Flink
    KafkaSink with EXACTLY_ONCE delivery guarantee.

    Implemented as MapFunction (not SinkFunction) because PyFlink's
    SinkFunction is JavaFunctionWrapper — it wraps Java sinks, not Python.
    The map result is returned only to satisfy Flink's operator chain;
    nothing consumes it (a downstream print() acts as the no-op terminal sink).
    """

    def __init__(self, bootstrap_servers: str, produce_timeout_s: int) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._produce_timeout_s = produce_timeout_s
        self._producer = None
        self._delivery_errors: list[str] = []

    def open(self, _runtime_context: RuntimeContext) -> None:  # noqa: ARG002
        from confluent_kafka import Producer
        from shared.kafka_utils.auth import confluent_kafka_props, is_event_hubs

        # Event Hubs Standard tier doesn't support Kafka idempotent producer.
        # Auto-detect and disable; SCRAM/SSL still works for at-least-once.
        idempotence = "false" if is_event_hubs() else "true"
        config = {
            "bootstrap.servers": self._bootstrap_servers,
            "enable.idempotence": idempotence,
            "acks": "all",
            "retries": "10",
            "max.in.flight.requests.per.connection": "5",
            "linger.ms": "20",
            "batch.size": "16384",
            **confluent_kafka_props(),
        }
        self._producer = Producer(config)
        log.info(
            "DLQ routing map opened → %s (idempotence=%s, close-flush timeout=%ds)",
            self._bootstrap_servers,
            idempotence,
            self._produce_timeout_s,
        )

    def _on_delivery(self, err, _msg) -> None:
        if err is not None:
            self._delivery_errors.append(str(err))

    def map(self, value: str) -> str:
        # Surface any prior delivery failures BEFORE we accept the next event.
        # This is the tight failure-detection loop: at most one batch of lag
        # before a real failure manifests as a thrown exception that fails
        # the task.
        if self._delivery_errors:
            errs = "; ".join(self._delivery_errors[:3])
            self._delivery_errors.clear()
            raise RuntimeError(f"DLQ produce failed (prior batch): {errs}")

        try:
            _, topic, payload = value.split("|", 2)
        except ValueError:
            # Malformed envelope from upstream FlatMap — fail loudly.
            raise RuntimeError(f"Malformed DLQ envelope (expected 'DLQ|topic|payload'): {value[:300]!r}")

        self._producer.produce(topic, value=payload.encode("utf-8"), on_delivery=self._on_delivery)
        # Non-blocking poll services delivery callbacks for messages that
        # have already been acked. No flush() here — that would serialise
        # every event and kill throughput.
        self._producer.poll(0)
        return value

    def close(self) -> None:
        if self._producer:
            # Final flush on graceful shutdown — surface any unrecoverable
            # failure as the last word from this task.
            remaining = self._producer.flush(timeout=self._produce_timeout_s)
            if remaining > 0 or self._delivery_errors:
                log.error(
                    "DLQ producer close: %s undelivered, %s errors. "
                    "Some DLQ events may be lost on this task instance.",
                    remaining,
                    len(self._delivery_errors),
                )


class SilverRoutingProcess(KeyedProcessFunction):
    """
    Receives raw JSON strings from the Kafka source (keyed by tenant|entity).
    Normalises CDC envelope, validates, then upserts into silver.<entity>.
    One psycopg2 connection per task — shared across all entities in the slot.
    Emits DLQ envelopes as 'DLQ|{entity_dlq_topic}|{json}' for _DlqRoutingSink.

    Valid events are buffered and flushed via execute_values in batches for
    throughput. The buffer is flushed when it reaches WRITE_BATCH_SIZE, on a
    processing-time timer every FLUSH_INTERVAL_MS (so low-volume entities don't
    starve), on a validation-failure yield, and on close().

    Schema cache has a TTL (SCHEMA_CACHE_TTL_S) so that column additions made
    via ALTER TABLE while the job is running are picked up automatically on the
    next event for that entity after the TTL expires.
    """

    def __init__(self, pg_host, pg_port, pg_db, pg_user, pg_password, pg_sslmode, etl_env):
        self._pg_host = pg_host
        self._pg_port = pg_port
        self._pg_db = pg_db
        self._pg_user = pg_user
        self._pg_password = pg_password
        self._pg_sslmode = pg_sslmode
        self._etl_env = etl_env
        self._conn = None
        self._cur = None
        # {entity: {pk_cols, biz_cols, _cached_at, _cache_invalidated_at}}
        self._schema_cache: dict = {}
        # Lazy Redis client for the dlq_watcher cache-bust signal. None means
        # Redis isn't configured — cache-bust is best-effort, not required.
        self._redis = None
        self._redis_attempted = False
        # Per-key pending state (ListState) — checkpoint-safe. Keyed by
        # f"{tenantid}|{entity}". Per-key in-memory dicts track count and timer.
        self._pending_state = None  # ListState[str], initialized in open()
        self._pending_count: dict = {}   # key_str → int (items in ListState for that key)
        self._timer_set: dict = {}       # key_str → bool

    def open(self, ctx: RuntimeContext):
        self._pending_state = ctx.get_list_state(
            ListStateDescriptor("pending_events", Types.STRING())
        )
        self._connect()
        log.info("SilverRoutingProcess opened — connected to %s:%s/%s", self._pg_host, self._pg_port, self._pg_db)

    def _connect(self):
        import psycopg2

        # connect_timeout only bounds the initial TCP handshake — it does NOT
        # bound query execution. Observed 2026-08-07: the socket to pgbouncer
        # went silently dead (no TCP RST/FIN) while idle, and the next
        # _ensure_conn() health-check query then blocked forever with no
        # exception, freezing this operator (and the whole Flink checkpoint
        # barrier behind it) for over an hour. TCP keepalives let the OS
        # detect and kill a dead socket instead of waiting on a read that
        # will never arrive.
        #
        # statement_timeout is set via a SET command below, not the
        # `options` connect() kwarg — pgbouncer's startup-packet handling
        # rejects it ("FATAL: unsupported startup parameter in options:
        # statement_timeout"; only extra_float_digits/search_path are in its
        # IGNORE_STARTUP_PARAMETERS allow-list), which made every connection
        # attempt fail outright and crash-loop the Python worker.
        self._conn = psycopg2.connect(
            host=self._pg_host,
            port=self._pg_port,
            dbname=self._pg_db,
            user=self._pg_user,
            password=self._pg_password,
            sslmode=self._pg_sslmode,
            connect_timeout=5,
            keepalives=1,
            keepalives_idle=15,
            keepalives_interval=5,
            keepalives_count=3,
        )
        self._conn.autocommit = True
        self._cur = self._conn.cursor()
        self._cur.execute("SET statement_timeout = 10000")

    def _ensure_conn(self):
        try:
            self._cur.execute("SELECT 1")
        except Exception:
            self._connect()

    def _maybe_redis(self):
        """Lazy, best-effort Redis connect for the cache-bust signal."""
        if self._redis_attempted:
            return self._redis
        self._redis_attempted = True
        url = os.environ.get("DLQ_WATCHER_REDIS_URL")
        if not url:
            return None
        try:
            import redis

            self._redis = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2)
            self._redis.ping()
        except Exception as exc:
            log.info("Silver: Redis cache-bust unavailable (%s) — using TTL only", exc)
            self._redis = None
        return self._redis

    def _cache_busted(self, entity: str, entry: dict) -> bool:
        """
        True when the dlq_watcher has signalled a schema change for this entity
        since we cached it. Best-effort — any error returns False so cache TTL
        remains the safety net.
        """
        r = self._maybe_redis()
        if r is None:
            return False
        try:
            ts = r.get(f"silver:schema:invalidate:{entity}")
        except Exception:
            return False
        if not ts:
            return False
        try:
            bust_ms_seen = entry.get("_cache_invalidated_at_ms", 0)
            return int(ts) > int(bust_ms_seen)
        except (TypeError, ValueError):
            return False

    def _schema(self, entity: str) -> dict:
        entry = self._schema_cache.get(entity)
        # Cache-bust hook for dlq_watcher: if the watcher just ran ALTER TABLE
        # on this entity, it sets silver:schema:invalidate:<entity> in Redis.
        # When present and newer than our last observation, drop the cache
        # and re-fetch from information_schema.
        if entry and self._cache_busted(entity, entry):
            entry = None
        if entry and time.monotonic() - entry.get("_cached_at", 0) < SCHEMA_CACHE_TTL_S:
            return entry

        # Cache miss or TTL expired — re-fetch from information_schema
        self._ensure_conn()
        # Fetch column name AND data_type so we can coerce JSON values at write
        # time (e.g. Debezium emits epoch-millis bigints for some date fields,
        # but Silver DDL maps them to TIMESTAMPTZ — runtime cast bridges the gap).
        self._cur.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema='silver' AND table_name=%s
            ORDER BY ordinal_position
        """,
            (entity,),
        )
        col_rows = self._cur.fetchall()
        all_cols = [r[0] for r in col_rows]
        col_types = {r[0]: r[1] for r in col_rows}
        # Columns the DB enforces NOT NULL (excluding Silver metadata cols which
        # Silver itself always populates — only check business columns).
        not_null_cols = frozenset(r[0] for r in col_rows if r[2] == "NO" and not r[0].startswith("_s_"))

        self._cur.execute(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
            WHERE tc.constraint_type='PRIMARY KEY' AND tc.table_schema='silver' AND tc.table_name=%s
            ORDER BY kcu.ordinal_position
        """,
            (entity,),
        )
        pk_cols = [r[0] for r in self._cur.fetchall() if r[0] not in ("_s_tenantid", "_s_env")]

        meta = frozenset(
            {
                "_s_env",
                "_s_tenantid",
                "_s_is_deleted",
                "_s_deleted_at",
                "_s_bronze_ts",
                "_s_silver_ts",
                "_s_lsn",
                "_s_op",
            }
        )
        biz_cols = [c for c in all_cols if c not in meta]

        # Snapshot the current cache-bust marker so we only re-invalidate on a
        # strictly newer signal from dlq_watcher.
        invalidate_seen = 0
        r = self._maybe_redis()
        if r is not None:
            try:
                v = r.get(f"silver:schema:invalidate:{entity}")
                invalidate_seen = int(v) if v else 0
            except Exception:
                invalidate_seen = 0

        schema_info = {
            "pk_cols": pk_cols,
            "biz_cols": biz_cols,
            "col_types": col_types,
            "not_null_cols": not_null_cols,
            "_cached_at": time.monotonic(),
            "_cache_invalidated_at_ms": invalidate_seen,
        }
        self._schema_cache[entity] = schema_info
        log.info("Schema cached for silver.%s: pk=%s biz_cols=%d", entity, pk_cols, len(biz_cols))
        return schema_info

    @staticmethod
    def _normalise(event: dict) -> dict:
        source_db = event.get("source_db", "")
        if not event.get("tenantid"):
            event["tenantid"] = (
                event.get("tenant_id")
                or event.get("tenantId")
                or source_db  # DB name = tenant_id for per-tenant connectors
                or ""
            )
        elif (
            source_db
            and source_db not in NAMESPACED_SOURCE_DBS
            and event.get("source_type") != "external"
            and event["tenantid"] != source_db
        ):
            # envelope tenantid was already set but disagrees with the actual
            # source DB — flag for DLQ routing before any write attempt.
            event["__tenant_mismatch"] = f"tenantid={event['tenantid']!r} source_db={source_db!r}"
        # Handle per-tenant connectors which use un-aliased field names
        if "lsn" not in event:
            src_lsn = event.get("source_lsn")
            # Safe cast: 'not-a-number' or any non-numeric string must become None,
            # never raise. Bare int() throws ValueError and poison-pills the subtask.
            try:
                event["lsn"] = int(src_lsn) if src_lsn is not None else None
            except (ValueError, TypeError):
                event["lsn"] = None
        if "ts_ms" not in event:
            raw_ts = event.get("source_ts_ms") or 0
            try:
                event["ts_ms"] = int(raw_ts) if raw_ts else 0
            except (ValueError, TypeError):
                event["ts_ms"] = 0
        # Logical source env: stamped by InsertField SMT on the connector; falls back
        # to the deployment tier (ETL_ENV) for pre-multi-env rows and local single-env.
        event["_s_env"] = event.get("_s_env") or ETL_ENV
        return event

    # ── validation helpers ────────────────────────────────────────────────────

    @staticmethod
    def _check_op(op: str) -> tuple[ErrorType, str] | None:
        if op not in _VALID_OPS:
            return ErrorType.INVALID_OP, f"invalid_op={op!r}"
        return None

    @staticmethod
    def _check_tenantid(tenantid: str) -> tuple[ErrorType, str] | None:
        if not tenantid or not tenantid.strip() or len(tenantid) > 255:
            return ErrorType.MISSING_TENANT_ID, f"invalid_tenantid={tenantid!r}"
        return None

    @staticmethod
    def _check_tenant_mismatch(event: dict) -> tuple[ErrorType, str] | None:
        detail = event.get("__tenant_mismatch")
        if detail:
            return ErrorType.TENANT_MISMATCH, f"cross-tenant write blocked: {detail}"
        return None

    @staticmethod
    def _check_late(ts_ms) -> tuple[ErrorType, str] | None:
        if not LATE_EVENT_THRESHOLD_MS or not ts_ms:
            return None
        try:
            age_ms = int(time.time() * 1000) - int(ts_ms)
            if age_ms > LATE_EVENT_THRESHOLD_MS:
                return ErrorType.LATE_ARRIVAL, f"late_event age_ms={age_ms}"
        except (TypeError, ValueError):
            pass
        return None

    @staticmethod
    def _silver_table_name(event: dict, entity: str) -> str:
        """Return silver.<entity> for internal CDC, silver.ext_<source>_<entity> for external.
        When source == entity (single-level path), avoids duplication: ext_eagleview not ext_eagleview_eagleview."""
        if event.get("source_type") == "external":
            source = (event.get("source_name") or "unknown").replace("-", "_").replace(".", "_")
            if source == entity:
                return f"silver.ext_{source}"
            return f"silver.ext_{source}_{entity}"
        return f"silver.{entity}"

    def _write(self, entity: str, entity_dlq: str, event: dict, tenantid: str, op: str, raw: str):
        """
        Persist one validated event to Silver PostgreSQL.
        Yields a DLQ envelope on any write-level error, otherwise yields nothing.
        PG exceptions are classified via SQLSTATE — see shared.dlq.errors.
        Schema-fetch and connection-acquisition failures are also DLQ'd so a
        transient PG hiccup never kills the Flink task.
        """
        table = self._silver_table_name(event, entity)
        try:
            schema = self._schema(entity)
            if not schema["pk_cols"]:
                log.warning("No PK for silver.%s — routing to DLQ", entity)
                yield f"DLQ|{entity_dlq}|" + build_envelope(
                    error_type=ErrorType.NO_PK,
                    error_message=f"no primary key defined for silver.{entity}",
                    entity=entity,
                    raw=raw,
                    tenantid=tenantid,
                    lsn=event.get("lsn"),
                )
                return

            # Retry loop for transient PG errors only. Non-transient errors
            # (schema drift, NOT NULL, type coercion, etc.) fall through to
            # the DLQ branch immediately — retrying them would just waste time.
            last_exc: BaseException | None = None
            last_etype = ErrorType.UNKNOWN
            last_sqlstate: str | None = None
            last_column: str | None = None
            row_found = True
            for attempt in range(1, PG_RETRY_MAX_ATTEMPTS + 1):
                try:
                    self._ensure_conn()
                    if op == "d":
                        row_found = self._soft_delete(table, event, tenantid, schema)
                    else:
                        self._upsert(table, event, tenantid, op, schema)
                    last_exc = None
                    break
                except Exception as exc:
                    last_etype, last_sqlstate, last_column = classify_pg_exception(exc)
                    last_exc = exc
                    if last_etype != ErrorType.PG_TRANSIENT or attempt == PG_RETRY_MAX_ATTEMPTS:
                        break
                    delay_s = (PG_RETRY_BASE_DELAY_MS * (2 ** (attempt - 1))) / 1000.0
                    log.warning(
                        "Transient PG error entity=%s tenantid=%s attempt=%d/%d sqlstate=%s — retrying in %.2fs: %s",
                        entity,
                        tenantid,
                        attempt,
                        PG_RETRY_MAX_ATTEMPTS,
                        last_sqlstate,
                        delay_s,
                        exc,
                    )
                    time.sleep(delay_s)

            if last_exc is not None:
                log.error(
                    "Write failed entity=%s tenantid=%s op=%s sqlstate=%s column=%s error_type=%s after %d attempt(s): %s",
                    entity,
                    tenantid,
                    op,
                    last_sqlstate,
                    last_column,
                    last_etype.value,
                    PG_RETRY_MAX_ATTEMPTS if last_etype == ErrorType.PG_TRANSIENT else 1,
                    last_exc,
                )
                yield f"DLQ|{entity_dlq}|" + build_envelope(
                    error_type=last_etype,
                    error_message=str(last_exc),
                    entity=entity,
                    raw=raw,
                    tenantid=tenantid,
                    lsn=event.get("lsn"),
                    sqlstate=last_sqlstate,
                    column_name=last_column,
                )
                return

            if op == "d" and not row_found:
                yield f"DLQ|{entity_dlq}|" + build_envelope(
                    error_type=ErrorType.DELETE_ON_MISSING,
                    error_message="delete on row not present in Silver",
                    entity=entity,
                    raw=raw,
                    tenantid=tenantid,
                    lsn=event.get("lsn"),
                )
                return
        except Exception as exc:
            # Catches anything that escapes the retry loop — typically a non-PG
            # exception (e.g. schema fetch failure not raised by psycopg2).
            etype, sqlstate, column_name = classify_pg_exception(exc)
            log.error(
                "Write failed (unhandled) entity=%s tenantid=%s op=%s sqlstate=%s column=%s error_type=%s: %s",
                entity,
                tenantid,
                op,
                sqlstate,
                column_name,
                etype.value,
                exc,
            )
            yield f"DLQ|{entity_dlq}|" + build_envelope(
                error_type=etype,
                error_message=str(exc),
                entity=entity,
                raw=raw,
                tenantid=tenantid,
                lsn=event.get("lsn"),
                sqlstate=sqlstate,
                column_name=column_name,
            )

    # ── main routing ──────────────────────────────────────────────────────────

    def process_element(self, value: str, ctx: "KeyedProcessFunction.Context"):
        raw = value
        try:
            event = json.loads(raw)
        except Exception as exc:
            # Malformed/no-entity events go to the unified {env}.dlq hub (not a
            # separate {env}.silver.dlq) — keeps the DLQ family within the Event
            # Hubs 10-hub cap. The error_type still distinguishes them.
            yield f"DLQ|{_dlq_topic()}|" + build_envelope(
                error_type=ErrorType.MALFORMED_JSON,
                error_message=str(exc),
                entity="",
                raw=raw,
            )
            return

        raw_table = event.get("source_table") or event.get("table") or event.get("source_entity", "")
        if not raw_table:
            yield f"DLQ|{_dlq_topic()}|" + build_envelope(
                error_type=ErrorType.NO_ENTITY,
                error_message="event has no source_table, table, or source_entity field",
                entity="",
                raw=raw,
                tenantid=event.get("tenantid", "") or "",
            )
            return

        # Resolve the Silver table name. Internal CDC from a namespaced source DB
        # (fieldcraft_provider/fieldcraft_customer) is prefixed: silver.{source_db}_{table};
        # tenant DBs keep the bare name. External events keep their raw entity —
        # _silver_table_name() maps those to silver.ext_<source>_<entity>.
        if event.get("source_type") == "external":
            entity = raw_table
        else:
            entity = _namespaced_entity(event.get("source_db") or event.get("db") or "", raw_table)

        entity_dlq = _dlq_topic(entity)
        # Any unexpected error from _normalise / validation must be DLQ'd immediately.
        # Valid events are buffered and flushed in batches via execute_values for
        # throughput — one round trip per WRITE_BATCH_SIZE records instead of one
        # per record, which is essential on high-latency Azure PG connections.
        try:
            event = self._normalise(event)
            tenantid = event.get("tenantid", "")
            op = event.get("op", "")

            err = (
                self._check_op(op)
                or self._check_tenantid(tenantid)
                or self._check_tenant_mismatch(event)
                or self._check_late(event.get("ts_ms"))
            )
            if err:
                # Validation failures are never buffered — they yield immediately.
                # Flush the buffer first so DLQ order stays consistent with write order.
                yield from self._flush_pending()
                etype, msg = err
                yield f"DLQ|{entity_dlq}|" + build_envelope(
                    error_type=etype,
                    error_message=msg,
                    entity=entity,
                    raw=raw,
                    tenantid=tenantid,
                    lsn=event.get("lsn"),
                )
                return

            env = event.get("_s_env") or ETL_ENV
            key_str = f"{env}|{tenantid.strip()}|{entity}"
            self._pending_state.add(json.dumps(
                [entity, entity_dlq, event, tenantid.strip(), op, raw]
            ))
            self._pending_count[key_str] = self._pending_count.get(key_str, 0) + 1
            if self._pending_count.get(key_str, 0) >= WRITE_BATCH_SIZE:
                yield from self._flush_pending()
            elif not self._timer_set.get(key_str):
                # Arm a single flush timer so low-volume buffers don't starve.
                fire_at = ctx.timer_service().current_processing_time() + FLUSH_INTERVAL_MS
                ctx.timer_service().register_processing_time_timer(fire_at)
                self._timer_set[key_str] = True
        except Exception as exc:
            log.exception("Unhandled process_element error entity=%s: %s", entity, exc)
            yield f"DLQ|{entity_dlq}|" + build_envelope(
                error_type=ErrorType.UNKNOWN,
                error_message=f"unhandled_process_element_error: {exc}",
                entity=entity,
                raw=raw,
                tenantid=str(event.get("tenantid", "") or ""),
                lsn=event.get("lsn"),
            )

    def on_timer(self, timestamp, ctx: "KeyedProcessFunction.OnTimerContext"):
        """Periodic flush so buffered low-volume events don't wait for a full batch."""
        yield from self._flush_pending()
        # Re-arm only if items arrived during the flush for any key.
        if any(v > 0 for v in self._pending_count.values()):
            current_key = ctx.get_current_key()
            key_str = str(current_key) if current_key is not None else ""
            if not self._timer_set.get(key_str):
                fire_at = ctx.timer_service().current_processing_time() + FLUSH_INTERVAL_MS
                ctx.timer_service().register_processing_time_timer(fire_at)
                self._timer_set[key_str] = True

    @staticmethod
    def _coerce_value(value, pg_type: str):
        """
        Coerce a raw JSON value to the Python type psycopg2 expects for the
        given PostgreSQL column type. Today this only bridges the most common
        Debezium quirk — epoch-millis bigints being emitted for columns the
        Silver DDL inferrer mapped to TIMESTAMPTZ — but it's the right place
        to extend for future type mismatches.
        """
        import datetime as _dt

        if value is None:
            return None
        if pg_type in ("timestamp with time zone", "timestamp without time zone") and isinstance(value, (int, float)):
            try:
                # Heuristic: if magnitude looks like epoch-millis (>1e11 ≈ year
                # 1973), treat as milliseconds; if smaller it's likely seconds.
                divisor = 1000.0 if abs(value) > 1e11 else 1.0
                return _dt.datetime.fromtimestamp(value / divisor, tz=_dt.timezone.utc)
            except (ValueError, OSError, OverflowError):
                return value
        if pg_type == "date" and isinstance(value, (int, float)):
            try:
                divisor = 1000.0 if abs(value) > 1e11 else 1.0
                return _dt.datetime.fromtimestamp(value / divisor, tz=_dt.timezone.utc).date()
            except (ValueError, OSError, OverflowError):
                return value
        return value

    def _flush_pending(self):
        """
        Flush all buffered events to Silver PostgreSQL in batches.
        Yields DLQ envelope strings for any events that fail at the batch level.
        Called on size threshold, on the flush timer, and on validation-failure yields.
        """
        # Drain ListState and reset tracking dicts.
        items_raw = list(self._pending_state.get() or [])
        if not items_raw:
            return
        self._pending_state.clear()
        self._pending_count.clear()
        self._timer_set.clear()
        pending = [json.loads(s) for s in items_raw]

        # Group by (entity, entity_dlq, op) so one execute_values call covers
        # all records for the same table + operation type.
        from collections import defaultdict

        groups: dict = defaultdict(list)
        for entity, entity_dlq, event, tenantid, op, raw in pending:
            groups[(entity, entity_dlq, op)].append((event, tenantid, raw))

        for (entity, entity_dlq, op), items in groups.items():
            first_event = items[0][0] if items else {}
            table = self._silver_table_name(first_event, entity)
            try:
                schema = self._schema(entity)
            except Exception as exc:
                etype, sqlstate, col = classify_pg_exception(exc)
                for event, tenantid, raw in items:
                    yield f"DLQ|{entity_dlq}|" + build_envelope(
                        error_type=etype,
                        error_message=str(exc),
                        entity=entity,
                        raw=raw,
                        tenantid=tenantid,
                        lsn=event.get("lsn"),
                        sqlstate=sqlstate,
                        column_name=col,
                    )
                continue

            if not schema["pk_cols"]:
                for event, tenantid, raw in items:
                    yield f"DLQ|{entity_dlq}|" + build_envelope(
                        error_type=ErrorType.NO_PK,
                        error_message=f"no primary key defined for silver.{entity}",
                        entity=entity,
                        raw=raw,
                        tenantid=tenantid,
                        lsn=event.get("lsn"),
                    )
                continue

            if op == "t":
                # Debezium TRUNCATE event — soft-delete all rows for this env+tenant+entity.
                # Actual table truncation would break audit trails; we mark rows deleted instead.
                # Items in this group can span multiple (env, tenantid) pairs (grouping is only
                # by (entity, entity_dlq, op)), so process each (env, tenantid) subgroup with its
                # own UPDATE — reading only items[0] here previously applied the truncate for one
                # tenant and silently dropped every other tenant's TRUNCATE event in the batch,
                # with no DLQ (see PR #7999 review).
                subgroups: dict = defaultdict(list)
                for event, tenantid_i, raw in items:
                    env_i = event.get("_s_env") or ETL_ENV
                    subgroups[(env_i, tenantid_i)].append((event, raw))

                for (env, tenantid), sub_items in subgroups.items():
                    incoming_lsn = max(
                        (e.get("lsn") for e, _ in sub_items if e.get("lsn") is not None),
                        default=None,
                    )
                    set_lsn_clause = ", _s_lsn=%s" if incoming_lsn is not None else ""
                    set_lsn_params: list = [incoming_lsn] if incoming_lsn is not None else []
                    lsn_guard = ""
                    lsn_guard_params: list = []
                    if incoming_lsn is not None:
                        lsn_guard = " AND (_s_lsn IS NULL OR _s_lsn < %s)"
                        lsn_guard_params = [incoming_lsn]
                    try:
                        self._ensure_conn()
                        self._cur.execute(
                            f"UPDATE {table} SET _s_is_deleted=TRUE, _s_deleted_at=NOW(), "
                            f"_s_silver_ts=NOW(), _s_op='d'{set_lsn_clause} "
                            f"WHERE _s_env=%s AND _s_tenantid=%s AND _s_is_deleted=FALSE{lsn_guard}",
                            set_lsn_params + [env, tenantid] + lsn_guard_params,
                        )
                        log.info("TRUNCATE soft-delete: %d rows in %s env=%s tenant=%s",
                                 self._cur.rowcount, table, env, tenantid)
                        # Connection is autocommit — no manual commit needed.
                    except Exception as exc:
                        etype, sqlstate, col = classify_pg_exception(exc)
                        for event, raw in sub_items:
                            yield f"DLQ|{entity_dlq}|" + build_envelope(
                                error_type=etype,
                                error_message=str(exc),
                                entity=entity,
                                raw=raw,
                                tenantid=tenantid,
                                lsn=event.get("lsn"),
                                sqlstate=sqlstate,
                            )
                continue

            if op == "d":
                for event, tenantid, raw in items:
                    try:
                        self._ensure_conn()
                        row_found = self._soft_delete(table, event, tenantid, schema)
                        if row_found:
                            pk_cols_d = schema.get("pk_cols", [])
                            pk_val = "  ".join(f"{c}={event.get(c, '?')}" for c in pk_cols_d) if pk_cols_d else "?"
                            source_db = event.get("source_db") or event.get("db") or "unknown"
                            log.info(
                                "CDC %-8s  source_db=%-20s → silver_db=%-14s  "
                                "table=%-36s  tenant=%-20s  source_id=%-8s  silver_id=%-8s  lsn=%s  *** SOFT DELETED ***",
                                "DELETE",
                                source_db,
                                PG_DB,
                                table,
                                tenantid,
                                pk_val,
                                pk_val,
                                event.get("lsn"),
                            )
                        else:
                            yield f"DLQ|{entity_dlq}|" + build_envelope(
                                error_type=ErrorType.DELETE_ON_MISSING,
                                error_message="delete on row not present in Silver",
                                entity=entity,
                                raw=raw,
                                tenantid=tenantid,
                                lsn=event.get("lsn"),
                            )
                    except Exception as exc:
                        etype, sqlstate, col = classify_pg_exception(exc)
                        yield f"DLQ|{entity_dlq}|" + build_envelope(
                            error_type=etype,
                            error_message=str(exc),
                            entity=entity,
                            raw=raw,
                            tenantid=tenantid,
                            lsn=event.get("lsn"),
                            sqlstate=sqlstate,
                            column_name=col,
                        )
            else:
                yield from self._upsert_batch(table, entity, entity_dlq, items, op, schema)

    def _upsert_batch(self, table: str, entity: str, entity_dlq: str, items: list, op: str, schema: dict):
        """
        Batch-upsert N records in a single execute_values round trip.
        Falls back to per-record upserts if the batch fails, minimising DLQ routing.
        Each fallback failure is individually DLQ'd.
        """
        from psycopg2.extras import execute_values
        import datetime as _dt

        biz_cols = schema["biz_cols"]
        pk_cols = schema["pk_cols"]
        col_types = schema.get("col_types", {})
        not_null_cols = schema.get("not_null_cols", frozenset())

        # Deduplicate within the batch: keep only the highest-LSN event per
        # (tenantid, pk) so a rapid double-update doesn't produce two rows with
        # the same conflict key and trigger "ON CONFLICT DO UPDATE cannot affect
        # row a second time".
        dedup: dict = {}
        for event, tenantid, raw in items:
            env = event.get("_s_env") or ETL_ENV
            pk_key = tuple([env, tenantid] + [event.get(c) for c in pk_cols])
            existing_lsn = dedup[pk_key][0].get("lsn") if pk_key in dedup else None
            cur_lsn = event.get("lsn")
            if existing_lsn is None or (cur_lsn is not None and cur_lsn >= existing_lsn):
                dedup[pk_key] = (event, tenantid, raw)
        deduped_items = list(dedup.values())

        # Pre-write NOT NULL check — reject before touching the DB so we avoid
        # a failed round-trip per bad record under bulk loads (e.g. re-snapshot
        # after a source schema migration added a NOT NULL constraint).
        # Only applied to INSERT/snapshot (op=c/r): Debezium UPDATE payloads carry
        # only changed columns, so an absent column is not the same as an explicit
        # NULL — checking absent columns on updates produces false positives.
        clean_items = []
        for event, tenantid, raw in deduped_items:
            null_violations = (
                [c for c in not_null_cols if c in biz_cols and event.get(c) is None] if op in ("c", "r") else []
            )
            if null_violations:
                yield f"DLQ|{entity_dlq}|" + build_envelope(
                    error_type=ErrorType.NOT_NULL_VIOLATION,
                    error_message=f"pre-write null check failed: {null_violations}",
                    entity=entity,
                    raw=raw,
                    tenantid=tenantid,
                    lsn=event.get("lsn"),
                    column_name=null_violations[0],
                )
            else:
                clean_items.append((event, tenantid, raw))
        deduped_items = clean_items
        if not deduped_items:
            return

        rows: list[tuple] = []
        for event, tenantid, raw in deduped_items:
            env = event.get("_s_env") or ETL_ENV
            biz_vals = [self._coerce_value(event.get(c), col_types.get(c, "")) for c in biz_cols]
            ts_ms = event.get("ts_ms")
            try:
                bronze_ts = _dt.datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=_dt.timezone.utc) if ts_ms else None
            except (TypeError, ValueError, OSError):
                bronze_ts = None
            rows.append(tuple([env, tenantid] + biz_vals + [False, bronze_ts, event.get("lsn"), op]))

        ins_cols = ["_s_env", "_s_tenantid"] + biz_cols + ["_s_is_deleted", "_s_bronze_ts", "_s_lsn", "_s_op", "_s_silver_ts"]
        col_list = ", ".join(ins_cols)
        n_param = len(ins_cols) - 1  # _s_silver_ts is NOW()
        template = "(" + ", ".join(["%s"] * n_param) + ", NOW())"
        pk_conflict = "_s_env, _s_tenantid" + (", " + ", ".join(pk_cols) if pk_cols else "")
        upd_cols = biz_cols + ["_s_is_deleted", "_s_bronze_ts", "_s_lsn", "_s_op"]
        upd_set = ", ".join(f"{c}=EXCLUDED.{c}" for c in upd_cols)
        lsn_guard = (
            f"WHERE EXCLUDED._s_lsn IS NULL OR {table}._s_lsn IS NULL " f"   OR {table}._s_lsn < EXCLUDED._s_lsn"
        )
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES %s "
            f"ON CONFLICT ({pk_conflict}) DO UPDATE SET {upd_set}, _s_silver_ts=NOW() {lsn_guard}"
        )

        try:
            self._ensure_conn()
            execute_values(self._cur, sql, rows, template=template, page_size=len(rows))
            # Per-row logging is OFF by default (LOG_EVERY_ROW) — see constant note.
            # The upsert above already persisted the whole batch in one round-trip;
            # the loop below only emits human-readable trace lines, so skipping it
            # when disabled removes the snapshot-time throughput bottleneck entirely.
            if LOG_EVERY_ROW:
                op_label = {"c": "INSERT", "u": "UPDATE", "r": "SNAPSHOT"}.get(op, op.upper())
                for event, tenantid, _ in deduped_items:
                    # Always derive PK from the actual schema pk_cols — never assume 'id'.
                    # For composite PKs show all columns as "col1=v1 col2=v2".
                    if pk_cols:
                        pk_val = "  ".join(f"{c}={event.get(c, '?')}" for c in pk_cols)
                    else:
                        pk_val = "?"
                    # Build a compact summary of key business fields present in this event.
                    # Show name/title/description for human context, status/state for lifecycle,
                    # version for optimistic-lock tracking, and flag new records explicitly.
                    _KEY_FIELDS = [
                        ("name", lambda v: f"name={v!r}"),
                        ("title", lambda v: f"title={v!r}"),
                        ("status", lambda v: f"status={v}"),
                        ("state", lambda v: f"state={v}"),
                        ("version", lambda v: f"version={v}"),
                        ("phase", lambda v: f"phase={v}"),
                        ("action", lambda v: f"action={v}"),
                        ("op_type", lambda v: f"op_type={v}"),
                        ("is_deleted", lambda v: f"is_deleted={v}"),
                    ]
                    extras = [
                        fmt(event[f])
                        for f, fmt in _KEY_FIELDS
                        if f in event and event[f] is not None and str(event[f]).strip() != ""
                    ]
                    source_db = event.get("source_db") or event.get("db") or "unknown"
                    new_flag = "  *** NEW RECORD ***" if op == "c" else ""
                    extra_str = ("  [" + "  ".join(extras) + "]") if extras else ""
                    log.info(
                        "CDC %-8s  source_db=%-20s → silver_db=%-14s  "
                        "table=%-36s  tenant=%-20s  source_id=%-8s  silver_id=%-8s  lsn=%s%s%s",
                        op_label,
                        source_db,
                        PG_DB,
                        table,
                        tenantid,
                        pk_val,
                        pk_val,
                        event.get("lsn"),
                        extra_str,
                        new_flag,
                    )
            return
        except Exception as batch_exc:
            log.warning(
                "Batch upsert failed for %s (%d rows): %s — falling back to per-record", table, len(rows), batch_exc
            )

        # Per-record fallback: minimises data loss when one bad row fails the batch
        for (event, tenantid, raw), _row in zip(deduped_items, rows):
            last_exc: BaseException | None = None
            last_etype = ErrorType.PG_TRANSIENT
            last_sqlstate: str | None = None
            last_column: str | None = None
            for attempt in range(1, PG_RETRY_MAX_ATTEMPTS + 1):
                try:
                    self._ensure_conn()
                    self._upsert(table, event, tenantid, op, schema)
                    last_exc = None
                    break
                except Exception as exc:
                    last_etype, last_sqlstate, last_column = classify_pg_exception(exc)
                    last_exc = exc
                    if last_etype != ErrorType.PG_TRANSIENT or attempt == PG_RETRY_MAX_ATTEMPTS:
                        break
                    time.sleep((PG_RETRY_BASE_DELAY_MS * (2 ** (attempt - 1))) / 1000.0)
            if last_exc is not None:
                yield f"DLQ|{entity_dlq}|" + build_envelope(
                    error_type=last_etype,
                    error_message=str(last_exc),
                    entity=entity,
                    raw=raw,
                    tenantid=tenantid,
                    lsn=event.get("lsn"),
                    sqlstate=last_sqlstate,
                    column_name=last_column,
                )

    def _upsert(self, table, rec, tenantid, op, schema):
        import datetime as _dt

        biz_cols = schema["biz_cols"]
        pk_cols = schema["pk_cols"]
        col_types = schema.get("col_types", {})
        present = [c for c in biz_cols if c in rec]
        # Apply per-column type coercion so e.g. bigint epoch-millis values
        # don't fail the upsert against a TIMESTAMPTZ column with SQLSTATE 42804.
        biz_vals = [self._coerce_value(rec[c], col_types.get(c, "")) for c in present]
        ts_ms = rec.get("ts_ms")
        try:
            bronze_ts = _dt.datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=_dt.timezone.utc) if ts_ms else None
        except (TypeError, ValueError, OSError):
            bronze_ts = None

        env = rec.get("_s_env") or ETL_ENV
        ins_cols = ["_s_env", "_s_tenantid"] + present + ["_s_is_deleted", "_s_bronze_ts", "_s_lsn", "_s_op", "_s_silver_ts"]
        n_params = len(ins_cols) - 1
        ph = ", ".join(["%s"] * n_params) + ", NOW()"
        upd_set = ", ".join(f"{c}=EXCLUDED.{c}" for c in present + ["_s_is_deleted", "_s_bronze_ts", "_s_lsn", "_s_op"])
        conflict = "_s_env, _s_tenantid" + (", " + ", ".join(pk_cols) if pk_cols else "")

        # LSN guard: only overwrite if incoming LSN is strictly newer than stored LSN.
        # Prevents out-of-order Flink replay from overwriting a more-recent Silver row.
        self._cur.execute(
            f"INSERT INTO {table} ({', '.join(ins_cols)}) VALUES ({ph}) "
            f"ON CONFLICT ({conflict}) DO UPDATE SET {upd_set}, _s_silver_ts=NOW() "
            f"WHERE EXCLUDED._s_lsn IS NULL OR {table}._s_lsn IS NULL "
            f"   OR {table}._s_lsn < EXCLUDED._s_lsn",
            [env, tenantid] + biz_vals + [False, bronze_ts, rec.get("lsn"), op],
        )
        log.debug("UPSERT %s tenant=%s op=%s rowcount=%d", table, tenantid, op, self._cur.rowcount)

    def _soft_delete(self, table, rec, tenantid, schema) -> bool:
        """
        Soft-delete a Silver row. Returns True if a row was found and updated,
        False if the row did not exist (caller routes to DLQ).

        LSN guard: when the incoming event has an LSN, the UPDATE only fires if
        the stored _s_lsn is older (or NULL), preventing a stale DELETE from
        overwriting a newer UPDATE that arrived out of order.
        """
        pk_cols = schema["pk_cols"]
        if pk_cols:
            where = " AND ".join(f"{c}=%s" for c in pk_cols)
            vals = [rec.get(c) for c in pk_cols]
        else:
            where = "_s_tenantid=%s"
            vals = [tenantid]

        incoming_lsn = rec.get("lsn")
        if incoming_lsn is not None:
            lsn_guard = " AND (_s_lsn IS NULL OR _s_lsn < %s)"
            lsn_params = [incoming_lsn]
        else:
            lsn_guard = ""
            lsn_params = []

        env = rec.get("_s_env") or ETL_ENV
        self._cur.execute(
            f"UPDATE {table} SET _s_is_deleted=TRUE, _s_deleted_at=NOW(), "
            f"_s_silver_ts=NOW(), _s_op='d', _s_lsn=%s "
            f"WHERE _s_env=%s AND _s_tenantid=%s AND {where}{lsn_guard}",
            [incoming_lsn, env, tenantid] + vals + lsn_params,
        )
        return self._cur.rowcount > 0

    def close(self):
        # ListState is checkpointed by Flink — do not call _flush_pending() here.
        # State APIs are undefined outside process_element/on_timer context in PyFlink;
        # calling them in close() raises. Buffered events survive via checkpoint on restart.
        if self._cur:
            self._cur.close()
        if self._conn:
            self._conn.close()


def _check_schema_drift(entities: list[str]) -> None:
    """
    At startup, compare Kafka entity topics vs Silver PostgreSQL tables.

    Behaviour is controlled by SILVER_SCHEMA_DRIFT_STRICT (default true):
      - strict=true  → raise RuntimeError on any missing Silver table. The job
                       fails fast so an operator runs `create_silver_tables.py`
                       instead of silently DLQ'ing every event for the entity.
      - strict=false → log a warning and continue (legacy soft-fail behaviour,
                       useful for partial-rollout scenarios).

    The connectivity-check exception is always non-fatal — a transient DB
    blip at startup should not block the job. Only a *successful* check that
    reveals missing tables triggers the strict abort.
    """
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=PG_HOST,
            port=PG_PORT,
            dbname=PG_DB,
            user=PG_USER,
            password=PG_PASSWORD,
            sslmode=PG_SSL_MODE,
            connect_timeout=5,
            keepalives=1,
            keepalives_idle=15,
            keepalives_interval=5,
            keepalives_count=3,
        )
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='silver' AND table_type='BASE TABLE'"
        )
        silver_tables = {r[0] for r in cur.fetchall()}
        cur.close()
        conn.close()
    except Exception as exc:
        log.warning("Schema drift check could not run (non-fatal): %s", exc)
        return

    missing = [e for e in entities if e not in silver_tables and not e.startswith("ext_")]
    if not missing:
        log.info("Schema drift check passed — all %d entities have Silver tables.", len(entities))
        return

    msg = (
        f"Schema drift: {len(missing)} entity topic(s) have no Silver table. "
        f"Run silver/ddl/create_silver_tables.py to create them. Missing: {missing}"
    )
    if SCHEMA_DRIFT_STRICT:
        log.error(msg + " — aborting (SILVER_SCHEMA_DRIFT_STRICT=true).")
        raise FatalConfigError(msg)
    log.warning(msg + " — continuing (SILVER_SCHEMA_DRIFT_STRICT=false); events will route to DLQ.")


def _route_key(value: str) -> str:
    """
    key_by selector: extract (tenantid, entity) so events for the same
    tenant/entity always go to the same parallel subtask and remain ordered.
    Malformed events get a constant fallback key — they'll be DLQ'd anyway
    inside process_element, but key_by needs a valid string here.
    """
    try:
        e = json.loads(value)
        raw_table = e.get("source_table") or e.get("table") or e.get("source_entity") or "_unknown"
        # Mirror the entity resolution in process_element so events for the same
        # resolved Silver table land on the same subtask (and stay ordered).
        if e.get("source_type") == "external":
            ent = raw_table
        else:
            ent = _namespaced_entity(e.get("source_db") or e.get("db") or "", raw_table)
        tid = e.get("tenantid") or e.get("tenant_id") or e.get("tenantId") or e.get("source_db") or "_unknown"
        env = e.get("_s_env") or ETL_ENV
        return f"{env}|{tid}|{ent}"
    except Exception:
        return "_malformed"


def run(entities: list[str], new_topics_event: threading.Event) -> None:
    _check_schema_drift(entities)
    env = StreamExecutionEnvironment.get_execution_environment()
    env.set_parallelism(SILVER_PARALLELISM)
    # AT_LEAST_ONCE is safe because Silver uses idempotent ON CONFLICT upserts.
    # It allows checkpoint barriers to pass through without waiting for all
    # in-flight DB writes to complete, preventing the 9+ minute checkpoint
    # stalls that caused all checkpoints to expire and offsets to never commit.
    env.enable_checkpointing(60_000, CheckpointingMode.AT_LEAST_ONCE)
    env.get_checkpoint_config().set_checkpoint_timeout(1_800_000)  # 30 min — covers initial backlog
    env.get_checkpoint_config().set_min_pause_between_checkpoints(30_000)
    env.get_checkpoint_config().set_tolerable_checkpoint_failure_number(10)
    env.get_checkpoint_config().set_checkpoint_storage(FileSystemCheckpointStorage(CHECKPOINT_PATH))

    # Path A: all internal CDC is routed to one consolidated hub.
    # Silver consumes {env}.cdc.events only; entity is read per-message from the
    # source_table field (see _route_key). External data is written directly to
    # silver.ext_* by SilverDirectWriter and is NOT consumed here.
    topic_pattern = rf"{re.escape(ETL_ENV)}\.cdc\.events$"
    from shared.kafka_utils.auth import flink_kafka_props
    from shared.pipeline_config import CONSUMER_GROUP_SILVER

    builder = (
        KafkaSource.builder()
        .set_bootstrap_servers(BOOTSTRAP)
        .set_topic_pattern(topic_pattern)
        # Suffixed with ETL_ENV -- dev/nonprod/prod can share one physical
        # Event Hubs namespace; an env-agnostic group.id would join dev's and
        # nonprod's Silver consumers into the same consumer group and
        # silently split CDC events between them via Kafka's rebalance
        # protocol, even though topic_pattern above is already env-scoped.
        # Imported from shared.pipeline_config (not re-literaled here) so an
        # operator's CONSUMER_GROUP_SILVER override (e.g. to force a
        # clean-slate replay, or split off a diagnostic consumer during an
        # incident) actually takes effect on this job -- see universal_bronze_
        # ingest.py's CONSUMER_GROUP_BRONZE for the same pattern.
        .set_group_id(CONSUMER_GROUP_SILVER)
        # Default: resume from committed offsets (earliest on first run). Set
        # SILVER_SCAN_STARTUP_MODE=latest for a clean re-snapshot run so Silver
        # skips stale messages still retained on the hub.
        .set_starting_offsets(
            KafkaOffsetsInitializer.latest()
            if os.environ.get("SILVER_SCAN_STARTUP_MODE", "").lower() == "latest"
            else KafkaOffsetsInitializer.committed_offsets(KafkaOffsetResetStrategy.EARLIEST)
        )
        .set_property("partition.discovery.interval.ms", os.environ.get("SILVER_PARTITION_DISCOVERY_MS", "-1"))
        .set_value_only_deserializer(SimpleStringSchema())
    )
    # Inject SASL/SSL properties iff configured for Event Hubs (no-op locally).
    for _k, _v in flink_kafka_props().items():
        builder = builder.set_property(_k, _v)
    src = builder.build()
    combined = env.from_source(
        src, WatermarkStrategy.no_watermarks(), "kafka-all-entities"
    ).set_parallelism(SOURCE_PARALLELISM)

    # key_by on (tenantid, entity) so parallelism > 1 preserves per-tenant
    # per-entity ordering. Same key → same subtask → ordered writes.
    keyed = combined.key_by(_route_key, key_type=Types.STRING())

    result = keyed.process(
        SilverRoutingProcess(PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASSWORD, PG_SSL_MODE, ETL_ENV),
        output_type=Types.STRING(),
    )

    # Route each DLQ event to its entity-specific Kafka topic.
    # MapFunction (side-effect writer) + print() provides the required terminal sink.
    # The printed output shows DLQ records in local dev — useful for debugging.
    result.filter(lambda v: v.startswith("DLQ|")).map(
        _DlqRoutingSink(BOOTSTRAP, DLQ_PRODUCE_TIMEOUT_S), output_type=Types.STRING()
    ).print()

    log.info("Silver merge pipeline ready for %d entities — executing.", len(entities))

    exc_holder: list[Exception] = []

    def _execute():
        try:
            env.execute("universal-silver-merge")
        except Exception as exc:
            exc_holder.append(exc)

    exec_thread = threading.Thread(target=_execute, name="flink-execute", daemon=True)
    exec_thread.start()

    while exec_thread.is_alive():
        if new_topics_event.is_set():
            log.info("New topics event set — stopping current Flink execution for restart.")
            try:
                env.close()
            except Exception:
                pass
            exec_thread.join(timeout=30)
            return
        exec_thread.join(timeout=5)

    if exc_holder:
        raise exc_holder[0]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")

    RESTART_BACKOFF_S = int(os.environ.get("SILVER_RESTART_BACKOFF_S", "10"))

    while True:
        entities = _discover_entities()
        known = set(entities)
        new_topics_event = threading.Event()

        _start_topic_watcher(known, new_topics_event)
        log.info("Starting Silver merge for %d entities: %s", len(entities), entities)

        try:
            run(entities, new_topics_event)
        except FatalConfigError as exc:
            log.error("FATAL config error — not restarting: %s", exc)
            sys.exit(2)
        except Exception as exc:
            log.error("Silver merge job exited with error: %s — restarting in %ds", exc, RESTART_BACKOFF_S)
            time.sleep(RESTART_BACKOFF_S)
            continue

        if new_topics_event.is_set():
            log.info("Restarting to pick up new topics (backoff=%ds)...", RESTART_BACKOFF_S)
            time.sleep(RESTART_BACKOFF_S)
        else:
            log.info("Flink job completed cleanly — exiting.")
            break
