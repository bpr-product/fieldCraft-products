"""Gold — canonical (user_permissions / tenant / project / customer / asset /
lead / communication_log) real-time streaming job.

Converts gold_canonical.user_permissions (DB renamed from gold_user_permissions
once it grew beyond just RBAC — see setup_gold_canonical.py), .tenant, .project,
.customer, .asset, .lead, and .communication_log from batch (15-min cron) to
real time. Reads CDC from local.silver.cdc.events (the dedicated Silver CDC
hub) and, on a change to a Silver table feeding one of these seven
collections, re-runs the existing loader SQL for the affected entity and
upserts the result.

tenant/project were added to this job 2026-08-24; customer/asset followed in a
second pass the same day. lead/communication_log were added afterward (see
docs/crm/eden_crm_gold_requirements.md G2/G5) — both carry a direct natural
key on their own CDC row (lead_uid, id), same shape as project's own-id
trigger, so no fan-out or tenant-wide fallback was needed for either. All six
non-user_permissions grains share one job/consumer-group with the pre-existing
user_permissions streaming instead of getting their own gold_*_stream.py file
(unlike BPE/Eden/QA, each of which owns a dedicated job file per feature):
this file's job name (gold-canonical-stream) and entry path are wired into
flink_supervisor.py's JOBS list and start_pipeline.sh's gold_pair loop by
STRING, not by filename-implies-scope — renaming or splitting the file would
require touching both deploy scripts and any inflight job, so each new
collection was added as additional routing branches in the existing job
instead.

Grain notes:
  user_permissions is keyed (tenantId, user_uid) — one document per user.
    Reuses load_user_permissions.build_documents(pg_dsn, tenant, gold_ts,
    user_account_id=...) — the same SQL the batch path runs, scoped to one
    user where the CDC event carries a direct user key.
  tenant is keyed (tenantId, sourceEnv) — one document per tenant (silver.tenant
    is already 1:1). Reuses load_tenant.build_documents(pg_dsn, tenant, gold_ts)
    — always a tenant-wide recompute, there is no sub-tenant scope to narrow to.
  project is keyed (tenantId, projectId, sourceEnv) — one document per project.
    Reuses load_project.build_documents(pg_dsn, tenant, gold_ts, project_id=...)
    — the same SQL the batch path runs, scoped to one project.
  customer is keyed (tenantId, customerUid, sourceEnv) — one document per
    customer, aggregated across every silver.customer_asset row sharing that
    customer_uid. Reuses load_customer.build_documents(pg_dsn, tenant, gold_ts,
    customer_uid=...) — customer_uid is a new optional scope filter added to
    that loader's SQL (2026-08-24) so a single-customer recompute doesn't
    rescan every customer_asset row for the tenant, mirroring project_id on
    load_project.
  asset is keyed (tenantId, assetUid, sourceEnv) — one document per asset,
    aggregated across every silver.customer_asset row sharing that asset_uid.
    Reuses load_asset.build_documents(pg_dsn, tenant, gold_ts, asset_uid=...)
    — same new-scope-filter treatment as customer above.
  lead is keyed (tenantId, leadUid, sourceEnv) — one document per lead
    (silver.fieldcraft_customer_lead is already 1:1 on lead_uid). Reuses
    load_lead.build_documents(pg_dsn, tenant, gold_ts, lead_uid=...) — scoped
    to one lead, same treatment as project.
  communication_log is keyed (tenantId, communicationLogId, sourceEnv) — one
    document per log row (silver.communication_log is already 1:1 on its own
    id; append-only source, so CDC triggers are effectively INSERT-only in
    practice). Reuses load_communication_log.build_documents(pg_dsn, tenant,
    gold_ts, communication_log_id=...) — scoped to one row.
Like the other Gold streams, recompute-and-upsert reflects current Silver state
(not an incremental diff), so replay from any Kafka offset is idempotent.

Why grain-prefixed keys (not one flat scope, unlike Eden's projectId-only key):
user_permissions' scope is a user_account_id, project's is a project_id,
customer's a customer_uid, asset's an asset_uid — all ordinary Postgres
serial/uuid primary keys within the SAME tenant, so e.g. a numeric
user_account_id could collide with an unrelated project's id (both "5"). The
Flink key_by partitions state by the raw key string, so a bare "{tenantId}|5"
would silently merge two unrelated entities' pending-recompute state. Every
trigger's `scope` is therefore "<grain>:<value>" (e.g. "user:5" vs "project:5"
vs "tenant:__tenant__") so the five grains can never collide, even though
pending collections are still tracked per-key exactly like Eden's
multi-collection reaggregator (see CanonicalReaggregator below).

Why user_permissions has two trigger scopes (direct vs tenant-wide) while
tenant/project/customer/asset each have only one direct-key path: the RBAC
join graph has tables that carry a direct user key and tables that don't.
  Direct-key tables (scope = one user, cheap):
    user_account   → own id is the user_account_id
    user_role_map  → carries user_account_id directly
  Indirect tables (scope = whole tenant — see rationale below):
    roles            → a role can be held by many users; the CDC event has no
                        user_account_id, only the role's own id
    role_permission  → same problem, one level further removed (roles_id)
    resource         → carries user_id, but project_member (the next hop) does
                        not, so project_member is the one that actually needs
                        the tenant-wide fallback; resource itself IS direct-key
                        and is treated as such below
    project_member   → carries resource_id, not user_id; resolving it to a
                        single user would require a lookup query inside the
                        router — a pattern not used elsewhere in this codebase
                        (every other CDC router is a pure field-mapping,
                        no DB access) — so this recomputes the whole tenant
                        instead. Effective RBAC changes on these tables are
                        comparatively rare (role/permission edits, not everyday
                        CRUD), so tenant-wide recompute is an acceptable cost
                        for staying consistent with the router pattern used by
                        BPE/Eden/QA and avoiding a query-per-event hot path.
  tenant/project/customer/asset trigger tables all carry a usable direct key
  (silver.tenant's own row IS the tenant; silver.project's own id; and
  silver.customer_asset's project_id/customer_uid/asset_uid columns, all
  present on the same row) — no tenant-wide fallback is needed for any of them.

This job is the SOLE writer of user_permissions/tenant/project/customer/asset/
lead/communication_log when canonical.type is "realtime" in gold_config.yml
(the batch loaders are
skipped in step_registry.build_steps — see is_realtime("canonical") — except
under --rebuild, which still runs the batch loaders as a one-time full
backfill/seed; see docs/gold-layer/initial_load_cutover_runbook.md for the
required start-streams-then-backfill order). question/answer were NOT
promoted alongside these five — they stay on the 15-min batch cadence (see
step_registry.py's canonical section) since that reference data is global or
rarely-changing.

Trigger tables:
  user_account       → user_permissions, direct: id = user_account_id
  user_role_map      → user_permissions, direct: user_account_id column
  resource           → user_permissions, direct: user_id column
  roles              → user_permissions, tenant-wide: role -> many users via user_role_map
  role_permission    → user_permissions, tenant-wide: permission -> many users via roles/user_role_map
  project_member     → user_permissions, tenant-wide: resource_id -> user is not in the CDC payload
  tenant             → tenant, direct: the changed row IS the tenant (tenant-grain, no sub-key)
  project            → project, direct: own id = project_id
  customer_asset     → up to three fan-out triggers from ONE event (all three
                        keys live on the same row): project (project_id
                        column, asset-linkage refresh), customer (customer_uid
                        column), asset (asset_uid column)
  fieldcraft_customer_customer → customer, direct: customer_uuid column (aliased
                        customer_uid on the join in load_customer.py) —
                        enrichment-only table, carries no project/asset key
  fieldcraft_customer_lead  → lead, direct: own lead_uid column; tenant from the
                        row's tenant_id column, not _s_tenantid (same shared,
                        cross-tenant Customer DB as fieldcraft_customer_customer)
  communication_log  → communication_log, direct: own id column (per-tenant
                        DB, immutable/append-only, monthly-partitioned)

Checkpoint: EXACTLY_ONCE, 60s interval, 3 tolerable failures. Delivery is
at-least-once; each recompute reads current Silver and upserts by the target
collection's natural key (user_uid / sourceEnv / projectId+sourceEnv /
customerUid+sourceEnv / assetUid+sourceEnv / leadUid+sourceEnv /
communicationLogId+sourceEnv), so replay is idempotent.

Env vars:
  KAFKA_BOOTSTRAP_SERVERS         default localhost:9092
  GOLD_CANONICAL_KAFKA_GROUP_ID   default gold-canonical-v1 (per-job; avoids
                                  shared-.env group collision with bpe/eden/qa)
  KAFKA_CDC_TOPIC                 default local.silver.cdc.events
  GOLD_CANONICAL_DB               default gold_canonical
  CANONICAL_DEBOUNCE_MS           default 1000
  FLINK_PARALLELISM               default 2
  LOG_LEVEL                       default INFO

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
from pyflink.datastream.checkpoint_storage import FileSystemCheckpointStorage
from pyflink.common.restart_strategy import RestartStrategies
from pyflink.datastream.connectors.kafka import KafkaSource
from pyflink.datastream.functions import (
    FlatMapFunction,
    KeyedProcessFunction,
    RuntimeContext,
)
from pyflink.datastream.state import ValueStateDescriptor

from shared.kafka_utils.auth import flink_kafka_props    # type: ignore[import]
from shared.secret_resolver import get_postgres_dsn      # type: ignore[import]
from gold.src.flink.sinks.mongo_sink import MongoUpsertSink
from gold.src.flink.recompute_guard import recompute_with_retry
from gold.src.flink.kafka_offsets import make_offsets_initializer
from gold.src.flink.cdc_parser import parse_cdc as _parse_cdc

# Reuse the batch loaders' tested SQL + shape_doc, scoped to one user/tenant/
# project/customer/asset (or the whole tenant when scope is None, for
# user_permissions).
from gold.src.canonical import (
    load_user_permissions, load_tenant, load_project, load_customer, load_asset,
    load_lead, load_communication_log,
)
from gold.pipeline.step_registry import is_feature_enabled, is_collection_enabled

log = logging.getLogger("gold.flink.canonical")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KAFKA_BOOTSTRAP = (os.environ.get("KAFKA_BOOTSTRAP_SERVERS")
                    or os.environ.get("ETL_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"))
_ETL_ENV = os.environ.get("ETL_ENV", "local")
# Per-job env key (NOT the shared KAFKA_GROUP_ID): all four Gold stream
# services load the same /opt/fieldcraft/gold/.env, so a single KAFKA_GROUP_ID would
# put bpe/eden/qa/canonical in one consumer group and silently split the CDC
# stream between them. Also suffixed with ETL_ENV -- dev/nonprod/prod can
# share one physical Event Hubs namespace, so without this an env-agnostic
# group.id would ADDITIONALLY join dev's and nonprod's gold-canonical
# consumers into the same group across environments, on top of the
# intra-environment job collision this was already guarding against.
KAFKA_GROUP_ID  = os.environ.get("GOLD_CANONICAL_KAFKA_GROUP_ID", f"gold-canonical-v1-{_ETL_ENV}")
# Derived from ETL_ENV, not hardcoded -- must match the Silver-source
# Debezium connector's actual output topic (see fieldcraft-silver-cdc-connector
# .template.json's transforms.route.replacement:
# "${ETL_ENV}.silver.cdc.events"). A hardcoded "local.silver.cdc.events"
# default only happened to work in dev (where ETL_ENV=="local"); any other
# ETL_ENV value would silently point this job at a topic nothing ever
# produces to, and if dev/nonprod ever share ETL_ENV="local" on the same
# Event Hubs namespace, they'd collide on this exact topic.
KAFKA_CDC_TOPIC = os.environ.get("KAFKA_CDC_TOPIC", f"{_ETL_ENV}.silver.cdc.events")
GOLD_DB         = os.environ.get("GOLD_CANONICAL_DB", "gold_canonical")
DEBOUNCE_MS     = int(os.environ.get("CANONICAL_DEBOUNCE_MS", "1000"))

# Checkpoint storage + timeout — see the equivalent block in gold_eden_stream.py
# for the full rationale. Both Gold streams shared the same two gaps: no
# checkpoint storage (so they ran on the default JobManager/heap storage, and a
# JobManager restart dropped the queued CDC backlog by falling back to a fresh
# `latest` submission) and no checkpoint timeout (so Flink's 10-minute default
# outlived flink_supervisor.py's stall detector, which then killed the job
# before Flink could fail the checkpoint itself). Shared GOLD_CHECKPOINT_PATH
# with the eden stream is intentional -- Flink writes each job's checkpoints
# under its own job-id subdirectory, exactly as bronze/silver do per layer.
CHECKPOINT_PATH = os.environ.get("GOLD_CHECKPOINT_PATH", "file:///tmp/flink-checkpoints/gold/")
CHECKPOINT_TIMEOUT_MS = int(os.environ.get("GOLD_CHECKPOINT_TIMEOUT_MS", "180000"))

# Collection this job writes per grain, and the Mongo upsert filter key for each.
_ALL_FILTER_FIELDS: dict[str, list[str]] = {
    "user_permissions": ["tenantId", "user_uid", "_env", "sourceEnv"],
    "tenant":            ["tenantId", "sourceEnv"],
    "project":           ["tenantId", "projectId", "sourceEnv"],
    "customer":          ["tenantId", "customerUid", "sourceEnv"],
    "asset":             ["tenantId", "assetUid", "sourceEnv"],
    "lead":              ["tenantId", "leadUid", "sourceEnv"],
    "communication_log": ["tenantId", "communicationLogId", "sourceEnv"],
}

# Phase-in gate (infra/config/gold_config.yml — canonical.enabled_collections,
# same allowlist mechanism step_registry.py's build_steps() already applies to
# the batch/rebuild path for these five loaders). Drop any collection not in
# the allowlist from the router's yields and the sink fan-out below, so the
# streaming path — the sole writer once canonical.type is "realtime" — honors
# the same phase-in as the batch path instead of silently computing/upserting
# a collection ops disabled. Absent allowlist == all five enabled (unchanged
# default), same as is_collection_enabled()'s own fallback. Mirrors
# gold_eden_stream.py's identical _ALL_FILTER_FIELDS/_FILTER_FIELDS split.
_FILTER_FIELDS: dict[str, list[str]] = {
    name: fields for name, fields in _ALL_FILTER_FIELDS.items()
    if is_collection_enabled("canonical", name)
}

# Sentinel scope value for a tenant-wide recompute (mirrors Eden's _TENANT_SCOPE).
_TENANT_SCOPE = "__tenant__"

# Trigger tables whose CDC event carries a direct user_account_id (cheap,
# single-user recompute) for user_permissions.
_DIRECT_KEY_TABLES = frozenset({"user_account", "user_role_map", "resource"})
# Trigger tables with no direct user key in the CDC payload (tenant-wide
# recompute — see module docstring for rationale) for user_permissions.
_TENANT_WIDE_TABLES = frozenset({"roles", "role_permission", "project_member"})
# tenant/project/customer/asset trigger tables — see module docstring's
# "Trigger tables" section.
_TENANT_TABLES = frozenset({"tenant"})
_PROJECT_DIRECT_TABLES = frozenset({"project"})          # own id = project_id
# customer_asset carries project_id, customer_uid, AND asset_uid directly — one
# CDC event on it can fan out to up to three collections (project/customer/asset).
_CUSTOMER_ASSET_TABLES = frozenset({"customer_asset"})
# fieldcraft_customer_customer carries customer_uuid (aliased customer_uid on the
# join in load_customer.py) — no project/asset linkage of its own.
_CUSTOMER_FK_TABLES = frozenset({"fieldcraft_customer_customer"})
# lead's own row IS the lead (lead_uid column, direct — same shape as project).
# Customer DB tables are namespaced fieldcraft_customer_ in Silver, and their
# _s_tenantid (-> the parser's _tenantid) is the literal "fieldcraft_customer", so
# the router reads the row's own tenant_id column for this table instead.
_LEAD_DIRECT_TABLES = frozenset({"fieldcraft_customer_lead"})
# communication_log's own row IS the log entry (id column, direct — same
# shape as project). Append-only/immutable source, but CDC still needs a
# trigger for the initial INSERT of each row.
_COMMUNICATION_LOG_DIRECT_TABLES = frozenset({"communication_log"})
_TRIGGER_TABLES = (
    _DIRECT_KEY_TABLES | _TENANT_WIDE_TABLES | _TENANT_TABLES
    | _PROJECT_DIRECT_TABLES | _CUSTOMER_ASSET_TABLES | _CUSTOMER_FK_TABLES
    | _LEAD_DIRECT_TABLES | _COMMUNICATION_LOG_DIRECT_TABLES
)


# ---------------------------------------------------------------------------
# CanonicalCdcRouter — map each relevant CDC event to a trigger scope
# ---------------------------------------------------------------------------

class CanonicalCdcRouter(FlatMapFunction):
    """Emit a {tenantId, scope, collection} trigger for each relevant CDC event.

    scope is "<grain>:<value>" — "user:<user_account_id>" or "user:__tenant__"
    for user_permissions, "tenant:__tenant__" for tenant, "project:<project_id>"
    for project, "customer:<customer_uid>" for customer, "asset:<asset_uid>"
    for asset. The grain prefix keeps the five entity namespaces from
    colliding when key_by partitions on the raw scope string (see module
    docstring's "Why grain-prefixed keys" section) — an ordinary Postgres
    serial user_account_id and an unrelated project's id can share the same
    numeric value within one tenant.

    A single customer_asset CDC event can yield up to three triggers (project,
    customer, asset) since that row carries all three keys directly.

    Every yield is gated on `collection in _FILTER_FIELDS` (see the
    "Phase-in gate" comment above _ALL_FILTER_FIELDS) so a partial
    `canonical.enabled_collections` rollout in gold_config.yml is honored by
    the live stream the same way step_registry.py's build_steps() already
    honors it for the batch/rebuild path — otherwise a collection ops
    disabled via the allowlist would still get computed and upserted here,
    since this job is the sole writer once canonical.type is "realtime".
    """

    def _emit(self, tenant_id: str, grain: str, value: Any, collection: str) -> Iterator[dict[str, Any]]:
        if collection not in _FILTER_FIELDS:
            return
        yield {"tenantId": tenant_id, "scope": f"{grain}:{value}", "collection": collection}

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
        tenant_id = str(tenant_id)

        if table in _TENANT_WIDE_TABLES:
            yield from self._emit(tenant_id, "user", _TENANT_SCOPE, "user_permissions")
            return

        if table in _DIRECT_KEY_TABLES:
            if table == "user_account":
                user_account_id = record.get("id")
            elif table == "user_role_map":
                user_account_id = record.get("user_account_id")
            else:  # resource
                user_account_id = record.get("user_id")
            if user_account_id is None:
                return
            yield from self._emit(tenant_id, "user", user_account_id, "user_permissions")
            return

        if table in _TENANT_TABLES:
            yield from self._emit(tenant_id, "tenant", _TENANT_SCOPE, "tenant")
            return

        if table in _PROJECT_DIRECT_TABLES:
            project_id = record.get("id")
            if project_id is not None:
                yield from self._emit(tenant_id, "project", project_id, "project")
            return

        if table in _CUSTOMER_ASSET_TABLES:
            project_id = record.get("project_id")
            if project_id is not None:
                yield from self._emit(tenant_id, "project", project_id, "project")
            customer_uid = record.get("customer_uid")
            if customer_uid is not None:
                yield from self._emit(tenant_id, "customer", customer_uid, "customer")
            asset_uid = record.get("asset_uid")
            if asset_uid is not None:
                yield from self._emit(tenant_id, "asset", asset_uid, "asset")
            return

        if table in _LEAD_DIRECT_TABLES:
            lead_tenant = record.get("tenant_id")
            lead_uid = record.get("lead_uid")
            if lead_tenant and lead_uid is not None:
                yield from self._emit(str(lead_tenant), "lead", lead_uid, "lead")
            return

        if table in _COMMUNICATION_LOG_DIRECT_TABLES:
            communication_log_id = record.get("id")
            if communication_log_id is not None:
                yield from self._emit(tenant_id, "communication_log", communication_log_id, "communication_log")
            return

        # _CUSTOMER_FK_TABLES (fieldcraft_customer_customer) — enriches an existing
        # customer doc; carries no project_id/asset_uid of its own.
        customer_uid = record.get("customer_uuid")
        if customer_uid is not None:
            yield from self._emit(tenant_id, "customer", customer_uid, "customer")


# ---------------------------------------------------------------------------
# CanonicalReaggregator — debounced recompute per (tenantId, grain:value)
# ---------------------------------------------------------------------------

class CanonicalReaggregator(KeyedProcessFunction):
    """Debounce CDC triggers per key, then recompute the affected doc(s).

    Mirrors Eden's multi-collection reaggregator (bounded retry + quarantine
    per collection via recompute_with_retry), even though in practice each
    grain-prefixed key only ever accumulates one collection — the grain
    prefix in the key guarantees a "user:" key never also carries a "tenant:"
    or "project:" trigger. Kept collection-set-based (not single-collection)
    for consistency with the rest of the Gold streams and to make a future
    same-grain multi-collection addition a non-event.
    """

    def open(self, ctx: RuntimeContext) -> None:
        self._pending = ctx.get_state(
            ValueStateDescriptor("pending_collections", Types.PICKLED_BYTE_ARRAY())
        )
        self._timer = ctx.get_state(ValueStateDescriptor("timer_ts", Types.LONG()))
        self._retry_timers = ctx.get_state(
            ValueStateDescriptor("retry_timer_ts_map", Types.PICKLED_BYTE_ARRAY())
        )
        self._fail = {
            coll: ctx.get_state(ValueStateDescriptor(f"recompute_fail_{coll}", Types.INT()))
            for coll in _FILTER_FIELDS
        }
        self._pg_dsn = get_postgres_dsn()

    def process_element(
        self, trigger: dict[str, Any], ctx: "KeyedProcessFunction.Context"
    ) -> Iterator[dict[str, Any]]:
        pending: set[str] = set(self._pending.value() or [])
        pending.add(trigger["collection"])
        self._pending.update(sorted(pending))

        retry_timers: dict = self._retry_timers.value() or {}
        for ts in retry_timers.values():
            ctx.timer_service().delete_processing_time_timer(ts)
        self._retry_timers.clear()

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
        pending = list(self._pending.value() or [])
        self._timer.clear()
        if not pending:
            return

        key = ctx.get_current_key()               # "tenantId|grain:value"
        tenant_id, _, scope = key.partition("|")
        grain, _, value = scope.partition(":")
        gold_ts = datetime.now(timezone.utc)

        new_retry_timers: dict[str, int] = {}
        for collection in pending:
            if collection == "user_permissions":
                user_account_id = None if value == _TENANT_SCOPE else value
                build = lambda uid=user_account_id: load_user_permissions.build_documents(
                    self._pg_dsn, tenant_id, gold_ts, user_account_id=uid
                )
            elif collection == "tenant":
                build = lambda: load_tenant.build_documents(self._pg_dsn, tenant_id, gold_ts)
            elif collection == "project":
                build = lambda pid=value: load_project.build_documents(
                    self._pg_dsn, tenant_id, gold_ts, project_id=pid
                )
            elif collection == "customer":
                build = lambda cid=value: load_customer.build_documents(
                    self._pg_dsn, tenant_id, gold_ts, customer_uid=cid
                )
            elif collection == "asset":
                build = lambda aid=value: load_asset.build_documents(
                    self._pg_dsn, tenant_id, gold_ts, asset_uid=aid
                )
            elif collection == "lead":
                build = lambda lid=value: load_lead.build_documents(
                    self._pg_dsn, tenant_id, gold_ts, lead_uid=lid
                )
            elif collection == "communication_log":
                build = lambda cid=value: load_communication_log.build_documents(
                    self._pg_dsn, tenant_id, gold_ts, communication_log_id=cid
                )
            else:
                continue

            docs, retry_ts = recompute_with_retry(
                build,
                fail_state=self._fail[collection],
                timer_service=ctx.timer_service(),
                feature=f"canonical.{collection}",
                tenant=tenant_id,
                project_id=value,   # recompute_guard's audit field name; holds our scope value here
                log=log,
            )
            if retry_ts is not None:
                new_retry_timers[collection] = retry_ts
            for doc in docs:
                tagged = dict(doc)
                tagged["_collection"] = collection
                yield tagged

        if new_retry_timers:
            self._retry_timers.update(new_retry_timers)
        else:
            self._retry_timers.clear()

        still_failing = sorted(
            c for c in pending if (self._fail[c].value() or 0) > 0
        )
        if still_failing:
            self._pending.update(still_failing)
        else:
            self._pending.clear()


# ---------------------------------------------------------------------------
# Job entry point
# ---------------------------------------------------------------------------

def _strip_tag(record: dict[str, Any]) -> dict[str, Any]:
    """Remove the internal routing tag before the document is upserted."""
    record.pop("_collection", None)
    return record


def build_job(env: StreamExecutionEnvironment) -> None:
    """Wire up the canonical (user_permissions/tenant/project/customer/asset) real-time recompute pipeline."""
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

    reaggregated = (
        cdc_stream
        .flat_map(CanonicalCdcRouter())
        .key_by(lambda r: f"{r['tenantId']}|{r['scope']}")
        .process(CanonicalReaggregator())
    )

    # Fan out to one Mongo sink per collection, filtering by the tagged name
    # (same pattern as gold_eden_stream.py).
    for collection, filter_fields in _FILTER_FIELDS.items():
        (
            reaggregated
            .filter(lambda r, _c=collection: r.get("_collection") == _c)
            .map(_strip_tag)
            .map(
                MongoUpsertSink(
                    db_name=GOLD_DB,
                    collection_name=collection,
                    filter_fields=filter_fields,
                )
            )
            .name(f"Cosmos: {collection}")
        )


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not is_feature_enabled("canonical"):
        log.info("canonical disabled in gold_config.yml — exiting without starting the stream")
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
    _cp = env.get_checkpoint_config()
    _cp.set_tolerable_checkpoint_failure_number(3)
    _cp.set_checkpoint_timeout(CHECKPOINT_TIMEOUT_MS)
    _cp.set_min_pause_between_checkpoints(30_000)
    _cp.set_checkpoint_storage(FileSystemCheckpointStorage(CHECKPOINT_PATH))

    build_job(env)

    log.info("Submitting Gold canonical (user_permissions/tenant/project/customer/asset) real-time streaming job...")
    env.execute("gold-canonical-stream")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
