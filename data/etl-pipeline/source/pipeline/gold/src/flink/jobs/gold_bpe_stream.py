"""Gold — BPE feature_store real-time streaming job (CDC-triggered recompute).

Converts gold_bpe.feature_store from batch (cron every 15 min) to real time.
Reads all CDC events from local.silver.cdc.events (the dedicated Silver CDC hub —
NOT local.cdc.events which is shared with Bronze/Silver) and, on any change to a Silver table that feeds the feature_store
document, re-runs the existing, tested loader SQL for the affected
(tenantId, project_id) and upserts the result into gold_bpe.feature_store.

Why CDC-triggered recompute (not native Flink joins):
  feature_store is a multi-table join/aggregation against Silver PG (project +
  project_estimate + bom + payment_expense + answers, plus EXISTS checks on
  resource_estimate / task for source_flags, plus customer_asset JSON on the
  project row). Re-expressing that as in-stream stateful joins would be a large,
  fragile rewrite. Instead we reuse load_estimate_snapshot.build_documents(
  pg_dsn, tenant, gold_ts, project_id=...) — the same SQL the batch path runs —
  scoped to one project and triggered by CDC. Join correctness is preserved;
  freshness drops from 15 min to seconds.

  NOTE: this job is the SOLE writer of gold_bpe.feature_store when bpe.type is
  "realtime" in gold_config.yml (the batch loaders are skipped in
  step_registry.build_steps).

Architecture:
  Kafka (local.silver.cdc.events) ── BpeCdcRouter ──→ key_by(tenantId|projectId)
                              (source_table → relevant?)        │
                                                                ↓
                                          BpeReaggregator (KeyedProcessFunction)
                                          debounce timer per key, then recompute
                                          feature_store for that project
                                                                │
                                                                ↓
                                          MongoUpsertSink(feature_store,
                                                          key=(tenantId, project_id))

Trigger tables (a change to any → recompute that project's feature_store):
  project              → recompute (project carries its own id + customer_asset JSON)
  project_estimate     → cost columns
  bom                  → bom_items + has_bom
  payment_expense      → actual spend + has_payment_expense
  answers              → features dict + has_answers
  resource_estimate    → has_resource_estimate flag
  task                 → has_tasks flag

Debounce: a burst of related CDC events for one project collapses into a single
recompute after BPE_DEBOUNCE_MS of quiet (default 1000 ms).

Checkpoint: EXACTLY_ONCE, 60s interval, 3 tolerable failures. Delivery to Cosmos
is at-least-once; each recompute reads current Silver state and upserts by
(tenantId, project_id), so replay is idempotent.

Env vars:
  KAFKA_BOOTSTRAP_SERVERS   default localhost:9092
  GOLD_BPE_KAFKA_GROUP_ID   default gold-bpe-v1 (per-job; avoids shared-.env group collision)
  KAFKA_CDC_TOPIC           default local.silver.cdc.events
  GOLD_BPE_DB_NAME          default gold_bpe
  BPE_DEBOUNCE_MS           default 1000
  FLINK_PARALLELISM         default 2
  LOG_LEVEL                 default INFO

Exit codes:
  0  job completed (or terminated by signal)
  1  startup / configuration error
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SHARED_SRC = str(_REPO_ROOT / "shared")
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if _SHARED_SRC not in sys.path:
    sys.path.insert(0, _SHARED_SRC)

from pyflink.common import WatermarkStrategy, Duration, Types
from pyflink.common.serialization import SimpleStringSchema
from pyflink.datastream import StreamExecutionEnvironment, CheckpointingMode
from pyflink.common.restart_strategy import RestartStrategies
from pyflink.datastream.connectors.kafka import KafkaSource
from pyflink.datastream.functions import (
    FlatMapFunction,
    KeyedProcessFunction,
    RuntimeContext,
)
from pyflink.datastream.state import ValueStateDescriptor

from kafka_utils.auth import flink_kafka_props          # type: ignore[import]
from secret_resolver import get_postgres_dsn            # type: ignore[import]
from gold.src.flink.sinks.mongo_sink import MongoUpsertSink
from gold.src.flink.recompute_guard import recompute_with_retry
from gold.src.flink.kafka_offsets import make_offsets_initializer
from gold.src.flink.cdc_parser import parse_cdc as _parse_cdc

# Reuse the batch loader's tested SQL + shape_doc, scoped to one project.
from gold.src.bpe import load_estimate_snapshot
from gold.pipeline.step_registry import is_feature_enabled

log = logging.getLogger("gold.flink.bpe")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KAFKA_BOOTSTRAP = (os.environ.get("KAFKA_BOOTSTRAP_SERVERS")
                    or os.environ.get("ETL_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"))
_ETL_ENV = os.environ.get("ETL_ENV", "local")
# Per-job env key (NOT the shared KAFKA_GROUP_ID): all three Gold stream services
# load the same /opt/fieldcraft/gold/.env, so a single KAFKA_GROUP_ID would put bpe/eden/qa
# in one consumer group and silently split the CDC stream between them. Also
# suffixed with ETL_ENV -- dev/nonprod/prod can share one physical Event Hubs
# namespace, so without this an env-agnostic group.id would ADDITIONALLY join
# dev's and nonprod's gold-bpe consumers into the same group across
# environments, on top of the intra-environment job collision this was
# already guarding against.
KAFKA_GROUP_ID  = os.environ.get("GOLD_BPE_KAFKA_GROUP_ID", f"gold-bpe-v1-{_ETL_ENV}")
# Derived from ETL_ENV, not hardcoded -- must match the Silver-source
# Debezium connector's actual output topic (see fieldcraft-silver-cdc-connector
# .template.json's transforms.route.replacement:
# "${ETL_ENV}.silver.cdc.events"). A hardcoded "local.silver.cdc.events"
# default only happened to work in dev (where ETL_ENV=="local"); any other
# ETL_ENV value would silently point this job at a topic nothing ever
# produces to, and if dev/nonprod ever share ETL_ENV="local" on the same
# Event Hubs namespace, they'd collide on this exact topic.
KAFKA_CDC_TOPIC = os.environ.get("KAFKA_CDC_TOPIC", f"{_ETL_ENV}.silver.cdc.events")
GOLD_BPE_DB     = os.environ.get("GOLD_BPE_DB_NAME", "gold_bpe")
DEBOUNCE_MS     = int(os.environ.get("BPE_DEBOUNCE_MS", "1000"))

COLLECTION = "feature_store"
# feature_store is keyed (tenantId, project_id) — see load_estimate_snapshot flush.
_FILTER_FIELDS = ["tenantId", "project_id", "sourceEnv", "_env"]

# Silver source tables whose changes require recomputing a project's feature_store.
# `project` carries the project key in its own `id` (and the customer_asset JSON);
# every other table references the project via `project_id`.
_TRIGGER_TABLES = frozenset({
    "project",
    "project_estimate",
    "bom",
    "payment_expense",
    "answers",
    "resource_estimate",
    "task",
})
_PROJECT_ID_IS_OWN_ID = "project"


# ---------------------------------------------------------------------------
# BpeCdcRouter — map each relevant CDC event to a (tenantId, projectId) trigger
# ---------------------------------------------------------------------------

class BpeCdcRouter(FlatMapFunction):
    """Emit a {tenantId, projectId} trigger for each feature_store-relevant CDC event."""

    def flat_map(self, raw: str) -> Iterator[dict[str, Any]]:
        record = _parse_cdc(raw)
        if not record:
            return
        table = record.get("_source_table")
        if table not in _TRIGGER_TABLES:
            return
        tenant_id = record.get("_tenantid")
        if not tenant_id:
            return
        # Resolve the project key: own id for `project`, else project_id.
        project_id = record.get("id") if table == _PROJECT_ID_IS_OWN_ID else record.get("project_id")
        if project_id is None:
            return
        yield {"tenantId": str(tenant_id), "projectId": str(project_id)}


# ---------------------------------------------------------------------------
# BpeReaggregator — debounced recompute per (tenantId, projectId)
# ---------------------------------------------------------------------------

class BpeReaggregator(KeyedProcessFunction):
    """Debounce CDC triggers per key, then recompute feature_store for the project.

    A burst of related events for one project collapses into one recompute after
    DEBOUNCE_MS of quiet, which then reads current Silver state and emits the
    project's feature_store document(s).
    """

    def open(self, ctx: RuntimeContext) -> None:
        self._timer = ctx.get_state(ValueStateDescriptor("timer_ts", Types.LONG()))
        self._retry_timer = ctx.get_state(ValueStateDescriptor("retry_timer_ts", Types.LONG()))
        self._fail = ctx.get_state(ValueStateDescriptor("recompute_fail_count", Types.INT()))
        self._pg_dsn = get_postgres_dsn()

    def process_element(
        self, trigger: dict[str, Any], ctx: "KeyedProcessFunction.Context"
    ) -> Iterator[dict[str, Any]]:
        # Cancel any pending retry timer so it doesn't fire as a phantom
        # on_timer after the issue that caused it has already been resolved.
        retry_ts = self._retry_timer.value()
        if retry_ts:
            ctx.timer_service().delete_processing_time_timer(retry_ts)
            self._retry_timer.clear()
        existing = self._timer.value()
        fire_at = ctx.timer_service().current_processing_time() + DEBOUNCE_MS
        if existing:
            ctx.timer_service().delete_processing_time_timer(existing)
        ctx.timer_service().register_processing_time_timer(fire_at)
        self._timer.update(fire_at)
        return iter(())

    def on_timer(
        self, timestamp: int, ctx: "KeyedProcessFunction.OnTimerContext"
    ) -> Iterator[dict[str, Any]]:
        self._timer.clear()
        key = ctx.get_current_key()               # "tenantId|projectId"
        tenant_id, _, project_id = key.partition("|")
        gold_ts = datetime.now(timezone.utc)
        docs, retry_ts = recompute_with_retry(
            lambda: load_estimate_snapshot.build_documents(
                self._pg_dsn, tenant_id, gold_ts, project_id=int(project_id)
            ),
            fail_state=self._fail,
            timer_service=ctx.timer_service(),
            feature="bpe",
            tenant=tenant_id,
            project_id=project_id,
            log=log,
        )
        if retry_ts is not None:
            self._retry_timer.update(retry_ts)
        else:
            self._retry_timer.clear()
        for doc in docs:
            yield doc


# ---------------------------------------------------------------------------
# Job entry point
# ---------------------------------------------------------------------------

def build_job(env: StreamExecutionEnvironment) -> None:
    """Wire up the BPE feature_store real-time recompute pipeline."""
    kafka_props = flink_kafka_props()

    builder = (
        KafkaSource.builder()
        .set_bootstrap_servers(KAFKA_BOOTSTRAP)
        .set_topics(KAFKA_CDC_TOPIC)
        .set_group_id(KAFKA_GROUP_ID)
        .set_starting_offsets(make_offsets_initializer())
        .set_value_only_deserializer(SimpleStringSchema())
    )
    for k, v in kafka_props.items():
        builder = builder.set_property(k, v)
    cdc_source = builder.build()

    watermark = WatermarkStrategy.for_bounded_out_of_orderness(Duration.of_seconds(10))
    cdc_stream = env.from_source(cdc_source, watermark, KAFKA_CDC_TOPIC)

    (
        cdc_stream
        .flat_map(BpeCdcRouter())
        .key_by(lambda r: f"{r['tenantId']}|{r['projectId']}")
        .process(BpeReaggregator())
        .map(
            MongoUpsertSink(
                db_name=GOLD_BPE_DB,
                collection_name=COLLECTION,
                filter_fields=_FILTER_FIELDS,
            )
        )
        .name(f"Cosmos: {COLLECTION}")
    )


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not is_feature_enabled("bpe"):
        log.info("bpe disabled in gold_config.yml — exiting without starting the stream")
        return 0

    env = StreamExecutionEnvironment.get_execution_environment()

    _kafka_jar = _REPO_ROOT / "flink-jars" / "flink-sql-connector-kafka.jar"
    if _kafka_jar.exists():
        env.add_jars(f"file:///{_kafka_jar.as_posix()}")

    env.set_parallelism(int(os.environ.get("FLINK_PARALLELISM", "2")))
    # Never stop: auto-restart on any failure with a backoff (vs Flink's implicit
    # 1s hammer). Effectively-infinite attempts; the R5 recompute guard already
    # keeps poison triggers from failing the job, so restarts are for genuine
    # infra faults (PG/Cosmos/EH blips) and recover from checkpoint.
    env.set_restart_strategy(RestartStrategies.fixed_delay_restart(
        int(os.environ.get("GOLD_RESTART_ATTEMPTS", "2147483647")),
        int(os.environ.get("GOLD_RESTART_DELAY_MS", "10000")),
    ))
    env.enable_checkpointing(
        interval=60_000,   # 60s
        mode=CheckpointingMode.EXACTLY_ONCE,
    )
    env.get_checkpoint_config().set_tolerable_checkpoint_failure_number(3)

    build_job(env)

    log.info("Submitting Gold BPE real-time streaming job...")
    env.execute("gold-bpe-stream")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
