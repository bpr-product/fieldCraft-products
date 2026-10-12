"""Gold — qa_recommendation (qa_answer) real-time streaming job.

Converts gold_qa_recommendation.qa_answer from batch (cron every 15 min) to real
time. Reads CDC from local.silver.cdc.events (the dedicated Silver CDC hub —
NOT local.cdc.events which is shared with Bronze/Silver) and, on a change to a Silver table feeding qa_answer, re-runs the
existing loader SQL for the affected (tenantId, project_id) and upserts the
resulting documents into gold_qa_recommendation.qa_answer.

Grain note: qa_answer is keyed (tenantId, answer_id) — one document per Q&A
answer. A recompute is scoped to a project (all of that project's answers) and
emits one document per answer; each upserts by (tenantId, answer_id). Like the
batch path and the Eden/BPE streams, recompute-and-upsert reflects current Silver
state but does not physically delete answers removed from Silver (soft-delete is
carried via isDeleted on the row).

Why CDC-triggered recompute (not native Flink joins): qa_answer is a 3-table
join (answers + project + bom) with an EventData JSONB transform. We reuse
load_qa_recommendation.build_documents(pg_dsn, tenant, gold_ts, project_id=...) —
the same SQL the batch path runs — scoped to one project and triggered by CDC.

This job is the SOLE writer of qa_answer when qa_recommendation.type is
"realtime" in gold_config.yml (the batch loader is skipped in
step_registry.build_steps).

Trigger tables (a change to any → recompute that project's answers):
  answers   → the Q&A rows themselves (project key = project_id)
  project   → join enrichment (project_uid/trade/location/...) (key = own id)
  bom       → bill of materials lines (project key = project_id)

Checkpoint: EXACTLY_ONCE, 60s interval, 3 tolerable failures. Delivery is
at-least-once; each recompute reads current Silver and upserts by
(tenantId, answer_id), so replay is idempotent.

Env vars:
  KAFKA_BOOTSTRAP_SERVERS   default localhost:9092
  GOLD_QA_RECOMMENDATION_KAFKA_GROUP_ID  default gold-qa-recommendation-v1 (per-job; avoids shared-.env group collision)
  KAFKA_CDC_TOPIC           default local.silver.cdc.events
  GOLD_QA_RECOMMENDATION_DB default gold_qa_recommendation
  QA_RECO_DEBOUNCE_MS       default 1000
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
from gold.src.reference_project import load_qa_recommendation
from gold.pipeline.step_registry import is_feature_enabled

log = logging.getLogger("gold.flink.qa_recommendation")

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
# dev's and nonprod's gold-qa-recommendation consumers into the same group
# across environments, on top of the intra-environment job collision this
# was already guarding against.
KAFKA_GROUP_ID  = os.environ.get("GOLD_QA_RECOMMENDATION_KAFKA_GROUP_ID", f"gold-qa-recommendation-v1-{_ETL_ENV}")
# Derived from ETL_ENV, not hardcoded -- must match the Silver-source
# Debezium connector's actual output topic (see fieldcraft-silver-cdc-connector
# .template.json's transforms.route.replacement:
# "${ETL_ENV}.silver.cdc.events"). A hardcoded "local.silver.cdc.events"
# default only happened to work in dev (where ETL_ENV=="local"); any other
# ETL_ENV value would silently point this job at a topic nothing ever
# produces to, and if dev/nonprod ever share ETL_ENV="local" on the same
# Event Hubs namespace, they'd collide on this exact topic.
KAFKA_CDC_TOPIC = os.environ.get("KAFKA_CDC_TOPIC", f"{_ETL_ENV}.silver.cdc.events")
GOLD_DB         = os.environ.get("GOLD_QA_RECOMMENDATION_DB", "gold_qa_recommendation")
DEBOUNCE_MS     = int(os.environ.get("QA_RECO_DEBOUNCE_MS", "1000"))

COLLECTION = "qa_answer"
# qa_answer is keyed (tenantId, answer_id) — one document per Q&A answer.
# sourceEnv (Silver _s_env — which env's answers table this row came from) is
# folded into the upsert filter because a tenantid is only unique WITHIN one
# _s_env; "_env" is a separate, unrelated field (the streaming sink's
# PIPELINE_ENV deployment-tier stamp) and is kept as-is.
_FILTER_FIELDS = ["tenantId", "answer_id", "sourceEnv", "_env"]

# Silver source tables whose changes require recomputing a project's answers.
# `project` carries the project key in its own `id`; `answers` references it via
# project_id; `bom` (bill of materials) references it via project_id.
_TRIGGER_TABLES = frozenset({"answers", "project", "bom"})
_PROJECT_ID_IS_OWN_ID = "project"


# ---------------------------------------------------------------------------
# QaCdcRouter — map each relevant CDC event to a (tenantId, projectId) trigger
# ---------------------------------------------------------------------------

class QaCdcRouter(FlatMapFunction):
    """Emit a {tenantId, projectId} trigger for each qa_answer-relevant CDC event."""

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
        project_id = record.get("id") if table == _PROJECT_ID_IS_OWN_ID else record.get("project_id")
        if project_id is None:
            return
        yield {"tenantId": str(tenant_id), "projectId": str(project_id)}


# ---------------------------------------------------------------------------
# QaReaggregator — debounced recompute per (tenantId, projectId)
# ---------------------------------------------------------------------------

class QaReaggregator(KeyedProcessFunction):
    """Debounce CDC triggers per key, then recompute the project's qa_answer docs."""

    def open(self, ctx: RuntimeContext) -> None:
        self._timer = ctx.get_state(ValueStateDescriptor("timer_ts", Types.LONG()))
        self._retry_timer = ctx.get_state(ValueStateDescriptor("retry_timer_ts", Types.LONG()))
        self._fail = ctx.get_state(ValueStateDescriptor("recompute_fail_count", Types.INT()))
        self._pg_dsn = get_postgres_dsn()

    def process_element(
        self, trigger: dict[str, Any], ctx: "KeyedProcessFunction.Context"
    ) -> Iterator[dict[str, Any]]:
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
            lambda: load_qa_recommendation.build_documents(
                self._pg_dsn, tenant_id, gold_ts, project_id=int(project_id)
            ),
            fail_state=self._fail,
            timer_service=ctx.timer_service(),
            feature="qa_recommendation",
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
    """Wire up the qa_answer real-time recompute pipeline."""
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
        .flat_map(QaCdcRouter())
        .key_by(lambda r: f"{r['tenantId']}|{r['projectId']}")
        .process(QaReaggregator())
        .map(
            MongoUpsertSink(
                db_name=GOLD_DB,
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

    if not is_feature_enabled("qa_recommendation"):
        log.info("qa_recommendation disabled in gold_config.yml — exiting without starting the stream")
        return 0

    env = StreamExecutionEnvironment.get_execution_environment()

    _kafka_jar = _REPO_ROOT / "flink-jars" / "flink-sql-connector-kafka.jar"
    if _kafka_jar.exists():
        env.add_jars(f"file:///{_kafka_jar.as_posix()}")

    env.set_parallelism(int(os.environ.get("FLINK_PARALLELISM", "2")))
    # Never stop: auto-restart on any failure with backoff (see gold_bpe_stream).
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

    log.info("Submitting Gold qa_recommendation real-time streaming job...")
    env.execute("gold-qa-recommendation-stream")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
