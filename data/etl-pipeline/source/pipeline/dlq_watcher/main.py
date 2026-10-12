"""
dlq_watcher entrypoint — wires Kafka to process_envelope().

Subscribes to all `<env>.*.dlq` Kafka topics via pattern. For each message,
delegates to dlq_watcher.watcher.process_envelope with a real RedisState +
PostgresAudit + confluent_kafka.Producer + registered correctors.

Modes:
  DLQ_WATCHER_MODE=shadow (default) — log + audit only, no replay / no DDL
  DLQ_WATCHER_MODE=active           — runs correctors and replays
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.alerting.notify import alert_circuit_breaker_tripped

from dlq_watcher.audit import PostgresAudit
from dlq_watcher.correctors.fk_hold import FkHoldCorrector
from dlq_watcher.correctors.missing_tenant import MissingTenantCorrector
from dlq_watcher.correctors.schema_drift import SchemaDriftCorrector
from dlq_watcher.policies.fk_hold import FkHoldPolicy
from dlq_watcher.policies.late_arrival import LateArrivalPolicy
from dlq_watcher.policies.missing_tenant import MissingTenantPolicy
from dlq_watcher.policies.schema_drift import SchemaDriftPolicy
from dlq_watcher.policies.transient import PgTransientPolicy
from dlq_watcher.state import CIRCUIT_BREAKER_LIMIT, RedisState
from dlq_watcher.watcher import WatcherConfig, process_envelope

log = logging.getLogger("dlq_watcher")

BOOTSTRAP        = os.environ.get("ETL_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
ETL_ENV          = os.environ.get("ETL_ENV", "local")
PG_SCHEMA        = os.environ.get("ETL_PG_SCHEMA", "public")
WATCHER_MODE     = os.environ.get("DLQ_WATCHER_MODE", "shadow").lower()
POLL_TIMEOUT_S   = float(os.environ.get("DLQ_WATCHER_POLL_TIMEOUT_S", "2.0"))
CB_FREEZE_MS     = int(os.environ.get("DLQ_WATCHER_CB_FREEZE_MS", str(5 * 60 * 1_000)))
# Suffixed with ETL_ENV -- dev/nonprod/prod can share one physical Event Hubs
# namespace, so an environment-agnostic group.id would join dev's and
# nonprod's dlq_watcher into the SAME consumer group and silently split each
# other's DLQ messages via Kafka's rebalance protocol. See pipeline_config.py's
# CONSUMER_GROUP_* constants for the same fix applied to Bronze/Silver/onboarding.
CONSUMER_GROUP   = os.environ.get("DLQ_WATCHER_CONSUMER_GROUP", f"dlq-watcher-v1-{ETL_ENV}")

# Path A (Event Hubs consolidation): Silver writes entity-level DLQ events to the
# unified `{env}.dlq` hub, plus `{env}.silver.dlq` for malformed/no-entity events.
# The optional `(.+\.)?` group matches BOTH `{env}.dlq` and `{env}.<x>.dlq` while
# excluding `.dlq.parking` (terminal, must not be re-consumed).
DLQ_TOPIC_PATTERN = rf"^{re.escape(ETL_ENV)}\.(.+\.)?dlq$"
_PARKING_SUFFIX   = re.compile(r"\.dlq\.parking$")

POLICIES = {
    "PG_TRANSIENT":      PgTransientPolicy(),
    "LATE_ARRIVAL":      LateArrivalPolicy(),
    "SCHEMA_DRIFT":      SchemaDriftPolicy(),
    "MISSING_TENANT_ID": MissingTenantPolicy(),
    "FK_VIOLATION":      FkHoldPolicy(),
}


# Path A topology: internal CDC is consolidated onto a single `{env}.cdc.events`
# hub and the DLQ onto a single `{env}.dlq` hub (Event Hubs Standard 10-hub cap).
# The entity is carried inside each envelope/payload (`source_table`), so the
# watcher does NOT use per-entity topic names — they are no longer produced or
# consumed and replaying to them would be a silent no-op.

# Parking is audit-only — parked events are recorded in silver._dlq_corrections,
# not republished to a parking hub (avoids an 11th Event Hub against the 10-cap).


def _source_topic(entity: str) -> str:
    # Replay target = the consolidated hub the Silver job actually consumes.
    # The replayed payload carries `source_table`, so Silver routes it per-entity.
    return f"{ETL_ENV}.cdc.events"


def _dlq_topic(entity: str) -> str:
    # DEFERRED republish target = the unified DLQ hub (so it is re-consumed).
    return f"{ETL_ENV}.dlq"


def _source_pg_factory():
    import psycopg2
    return psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ["POSTGRES_USER"],
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        sslmode=os.environ.get("POSTGRES_SSL_MODE", "require"),
        dbname=os.environ.get("POSTGRES_DB", ""),
    )


def _silver_pg_factory():
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("SILVER_PG_HOST", "localhost"),
        port=int(os.environ.get("SILVER_PG_PORT", "5432")),
        user=os.environ.get("SILVER_PG_USER", "silver_writer"),
        password=os.environ.get("SILVER_WRITER_PWD") or os.environ.get("SILVER_PG_PASSWORD", ""),
        sslmode=os.environ.get("SILVER_PG_SSL_MODE", "disable"),
        dbname=os.environ.get("SILVER_PG_DB", ""),
        connect_timeout=10,
    )


def _build_correctors(state: RedisState) -> dict:
    """Build the corrector registry."""
    # _redis is private but stable — we need the bare client for SET NX.
    redis_client = state._r  # noqa: SLF001
    return {
        "SCHEMA_DRIFT": SchemaDriftCorrector(
            source_pg_factory=_source_pg_factory,
            silver_pg_factory=_silver_pg_factory,
            redis_client=redis_client,
            source_schema=PG_SCHEMA,
        ),
        "MISSING_TENANT_ID": MissingTenantCorrector(),
        "FK_VIOLATION":      FkHoldCorrector(silver_pg_factory=_silver_pg_factory),
    }


def _maybe_page_circuit_breaker(decision, envelope_json, paged_entities: set) -> None:
    """Page PagerDuty once per entity per freeze window."""
    if decision is None or decision.policy_id != "circuit_breaker":
        return
    try:
        entity = json.loads(envelope_json).get("entity") or ""
    except Exception:
        log.exception("Failed to extract entity from CB-tripped envelope")
        return
    if not entity or entity in paged_entities:
        return
    paged_entities.add(entity)
    alert_circuit_breaker_tripped(
        entity=entity,
        rate_per_min=CIRCUIT_BREAKER_LIMIT + 1,
        limit=CIRCUIT_BREAKER_LIMIT,
        freeze_minutes=CB_FREEZE_MS // 60_000,
    )


def _make_publish(producer):
    def publish(topic: str, payload: str) -> None:
        producer.produce(topic, value=payload.encode("utf-8"))
        producer.poll(0)
    return publish


def _consume_one(consumer, producer):
    """Returns (topic, envelope_json, msg) or None when nothing to process."""
    from confluent_kafka import KafkaError
    msg = consumer.poll(POLL_TIMEOUT_S)
    if msg is None:
        producer.poll(0)
        return None
    if msg.error():
        if msg.error().code() != KafkaError._PARTITION_EOF:
            log.warning("Kafka error: %s", msg.error())
        return None
    topic = msg.topic() or ""
    if _PARKING_SUFFIX.search(topic):
        # Parking topic — commit immediately, do not re-process.
        consumer.commit(message=msg, asynchronous=True)
        return None
    try:
        envelope_json = msg.value().decode("utf-8")
    except Exception:
        log.exception("Decode failed topic=%s", topic)
        consumer.commit(message=msg, asynchronous=True)
        return None
    return topic, envelope_json, msg


def _run_loop(consumer, producer, state, audit, correctors, cfg) -> None:
    from dlq_watcher.policies.base import Action
    publish = _make_publish(producer)
    paged_entities: set[str] = set()

    while True:
        item = _consume_one(consumer, producer)
        if item is None:
            continue
        _topic, envelope_json, msg = item

        decision = process_envelope(
            envelope_json=envelope_json,
            policies=POLICIES,
            state=state, audit=audit, publish=publish, cfg=cfg,
            correctors=correctors,
        )
        _maybe_page_circuit_breaker(decision, envelope_json, paged_entities)
        # Only commit offset when we are done with this message. WAIT decisions
        # expect re-delivery on restart — committing their offset would lose them.
        if decision is None or decision.action is not Action.WAIT:
            consumer.commit(message=msg, asynchronous=True)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  dlq_watcher  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log.info("Starting dlq_watcher mode=%s bootstrap=%s pattern=%s",
             WATCHER_MODE, BOOTSTRAP, DLQ_TOPIC_PATTERN)

    if WATCHER_MODE not in ("shadow", "active"):
        log.error("Invalid DLQ_WATCHER_MODE=%r (expected 'shadow' or 'active'). Exiting.", WATCHER_MODE)
        sys.exit(2)

    from confluent_kafka import Consumer, Producer
    # SASL/SSL props so both clients authenticate to Azure Event Hubs (no-op for
    # local PLAINTEXT). Without these, Event Hubs closes the connection during the
    # API-version query and the watcher can never consume or replay.
    from shared.kafka_utils.auth import confluent_kafka_props

    state      = RedisState()
    audit      = PostgresAudit()
    correctors = _build_correctors(state)

    producer = Producer({
        "bootstrap.servers":  BOOTSTRAP,
        # Idempotent producer is NOT supported by Azure Event Hubs (Standard) —
        # leaving it on fails with "Failed to acquire idempotence PID". Replays
        # are already idempotent at the Silver layer (LSN guard), so plain
        # acks=all delivery is sufficient.
        "enable.idempotence": "false",
        "acks":               "all",
        "linger.ms":          "20",
        **confluent_kafka_props(),
    })
    consumer = Consumer({
        "bootstrap.servers":  BOOTSTRAP,
        "group.id":           CONSUMER_GROUP,
        "auto.offset.reset":  "earliest",
        "enable.auto.commit": "false",
        "session.timeout.ms": "30000",
        **confluent_kafka_props(),
    })
    consumer.subscribe(
        [DLQ_TOPIC_PATTERN],
        on_assign=lambda c, parts: log.info("Assigned: %s", parts),
    )

    cfg = WatcherConfig(
        mode=WATCHER_MODE,
        source_topic_fn=_source_topic,
        dlq_topic_fn=_dlq_topic,
        cb_freeze_ms=CB_FREEZE_MS,
    )

    try:
        _run_loop(consumer, producer, state, audit, correctors, cfg)
    except KeyboardInterrupt:
        log.info("Shutting down.")
    finally:
        try:
            producer.flush(timeout=10)
        except Exception:
            log.exception("Producer flush failed during shutdown")
        consumer.close()
        audit.close()


if __name__ == "__main__":
    main()
