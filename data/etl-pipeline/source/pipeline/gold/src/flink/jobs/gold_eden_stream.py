"""Gold — Eden real-time streaming job (CDC-triggered re-aggregation).

Converts the 6 Eden collections from batch (cron every 15 min) to real time.
Reads all CDC events from local.silver.cdc.events (the dedicated Silver CDC hub —
NOT local.cdc.events which is shared with Bronze/Silver), and on any change to a Silver table that feeds an Eden
collection it re-runs the existing, tested per-loader SQL for the affected
(tenantId, projectId) and upserts the result into Cosmos DB.

Why CDC-triggered re-aggregation (not native Flink joins):
  Each Eden collection is a multi-table join/aggregation against Silver PG
  (project_health joins 6 tables).  Re-expressing those as in-stream stateful
  joins would be a large, fragile rewrite.  Instead we reuse each loader's
  build_documents(pg_dsn, tenant, gold_ts, project_id=...) — the same SQL the
  batch path runs — but scoped to one project and triggered by CDC.  Join
  correctness is preserved; freshness drops from 15 min to seconds.

Architecture:
  Kafka (local.silver.cdc.events) ── EdenCdcRouter ──→ key_by(tenantId|projectId)
                              (maps source_table        │
                               → affected trigger)       ↓
                                              EdenReaggregator (KeyedProcessFunction)
                                              debounce timer per key, then re-run
                                              the affected loaders' SQL for that
                                              project (or tenant for the dashboard)
                                                          │
                                              ┌───────────┴─────── per collection ───────────┐
                                              ↓                                                ↓
                                     MongoUpsertSink(project_health)  …  MongoUpsertSink(tenant_dashboard)

Trigger routing (Silver table → Eden collections re-aggregated):
  project             → all 6 (project-grain + tenant_dashboard)
  task                → project_health, task_intelligence, workforce_status, tenant_dashboard
  resource_schedule   → workforce_status
  project_estimate    → project_health
  (project_estimate_schedule omitted — junction table has no project_id;
   project_estimate CDC covers the same trigger in practice)
  purchase_order      → project_health, procurement_status
  purchase_order_items→ procurement_status
  invoice             → project_health, invoice_summary, tenant_dashboard
  payment_receives    → invoice_summary
  project_schedule    → project_health, tenant_dashboard
  bom                 → procurement_status, project_health (added 2026-08-01 —
                        Q1 estimateHistory[].confidence/.evidence BOM reconciliation;
                        bom carries project_id directly, no lookup needed)
  answers             → bid_context
  vendor / question_template / resource → tenant-wide reference data:
                        trigger tenant_dashboard refresh only (no project key
                        available — avoids fanning out to every project).

  LOOKUP-JOIN TABLES (added 2026-08-01 — no project_id on the event itself,
  resolved via a synchronous per-event Postgres query in EdenCdcRouter,
  see _lookup_project_ids / _LOOKUP_TABLES):
    material_cost       → project_health (Q1 — vendor price change; resolved
                           via vendor_material_map_id → silver.bom.project_id)
    vendor_material_map → project_health (Q1 — same resolution, keyed by its
                           own id instead of material_cost's FK to it)
    audit_log           → project_health (Q2 — reschedule events only, action
                           ILIKE '%RESCHEDUL%' filtered BEFORE the DB lookup;
                           resolved via resource_id (=project_uid) →
                           silver.project.id)
    resource_schedule   → project_health (Q2 — freed-resource correlation;
                           resolved via resource_id → OTHER project_ids the
                           same resource is also assigned to, excluding the
                           event's own project_id) — IN ADDITION to its
                           existing direct-project_id route to workforce_status.
  This is an intentional, scoped exception to the "no per-event DB lookup"
  design used for roles/role_permission/project_member elsewhere — these four
  tables are comparatively low-volume (price-catalog updates, reschedule-
  tagged audit events, resource assignment changes), not the hot task/timelog
  path. Lookup fan-out is capped (_LOOKUP_FANOUT_LIMIT) and failures are
  logged + skipped (never crash the router) so a lookup outage degrades to
  "confidence/evidence stays stale until the next real change to that same
  lookup table re-triggers this router", not job failure — step_registry.py's
  --rebuild (force_batch) is a no-op for eden (see its own comment there), so
  a stuck lookup-driven gap does not self-heal via a rebuild trigger.

Debounce: a burst of related CDC events for one project collapses into a single
re-aggregation after EDEN_DEBOUNCE_MS of quiet (default 500 ms), bounded by
EDEN_DEBOUNCE_MAX_MS (default 2000 ms) measured from the FIRST event of the
window — so a key written to faster than the debounce window is still
re-aggregated instead of having its timer postponed indefinitely.

Checkpoint: EXACTLY_ONCE, 60s interval, 3 tolerable failures.
Delivery to Cosmos is at-least-once; each re-aggregation reads current Silver
state and upserts by (tenantId, projectId), so replay is idempotent.

Env vars:
  KAFKA_BOOTSTRAP_SERVERS   default localhost:9092
  GOLD_EDEN_KAFKA_GROUP_ID  default gold-eden-v1 (per-job; avoids shared-.env group collision)
  KAFKA_CDC_TOPIC           default local.silver.cdc.events
  GOLD_EDEN_DB              default gold_eden
  EDEN_DEBOUNCE_MS          default 500
  EDEN_DEBOUNCE_MAX_MS      default 2000 (cap, measured from first event)
  FLINK_PARALLELISM         default 2
  LOG_LEVEL                 default INFO

Exit codes:
  0  job completed (or terminated by signal)
  1  startup / configuration error
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SHARED_SRC = str(_REPO_ROOT / "shared")
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if _SHARED_SRC not in sys.path:
    sys.path.insert(0, _SHARED_SRC)

import psycopg2

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
from gold.src.flink.kafka_offsets import make_offsets_initializer
from gold.src.flink.recompute_guard import recompute_with_retry
from gold.src.flink.cdc_parser import parse_cdc as _parse_cdc
from gold.pipeline.step_registry import is_collection_enabled

# Per-loader document builders (reuse the batch loaders' tested SQL + shape_doc)
from gold.src.eden import (
    load_project_health,
    load_invoice_summary,
    load_task_intelligence,
    load_workforce_status,
    load_procurement_status,
    load_bid_context,
    load_tenant_dashboard,
)

log = logging.getLogger("gold.flink.eden")

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
# dev's and nonprod's gold-eden consumers into the same group across
# environments, on top of the intra-environment job collision this was
# already guarding against.
KAFKA_GROUP_ID  = os.environ.get("GOLD_EDEN_KAFKA_GROUP_ID", f"gold-eden-v1-{_ETL_ENV}")
# Derived from ETL_ENV, not hardcoded -- must match the Silver-source
# Debezium connector's actual output topic (see fieldcraft-silver-cdc-connector
# .template.json's transforms.route.replacement:
# "${ETL_ENV}.silver.cdc.events"). A hardcoded "local.silver.cdc.events"
# default only happened to work in dev (where ETL_ENV=="local"); any other
# ETL_ENV value would silently point this job at a topic nothing ever
# produces to, and if dev/nonprod ever share ETL_ENV="local" on the same
# Event Hubs namespace, they'd collide on this exact topic.
KAFKA_CDC_TOPIC = os.environ.get("KAFKA_CDC_TOPIC", f"{_ETL_ENV}.silver.cdc.events")
GOLD_EDEN_DB    = os.environ.get("GOLD_EDEN_DB", "gold_eden")
# Quiet period after the last CDC event before a key is re-aggregated. Lowered
# 1000 -> 500 (2026-08-25): the debounce was tuned when a recompute took minutes,
# so collapsing a project-create burst of 5+ events into one was worth a full
# second of latency. Once the project-scoping fixes take a recompute to ~0.3s,
# the arithmetic inverts -- an extra redundant recompute is cheap, a second of
# added latency is not.
DEBOUNCE_MS     = int(os.environ.get("EDEN_DEBOUNCE_MS", "500"))

# Hard ceiling on how long coalescing may postpone a recompute, measured from
# the FIRST trigger of the window rather than the last.
#
# Why this is needed and lowering DEBOUNCE_MS alone is not enough: every arriving
# event re-arms the timer, with nothing bounding how many times. A project whose
# writes trickle in slower than the debounce window (a create that fans out to
# project/project_schedule/task/answers/project_member, a long-running import, a
# CDC backlog draining) can postpone its own recompute indefinitely -- each event
# pushes the fire time out again and the data never lands. Shrinking the window
# makes that less likely per-event but still leaves it unbounded.
#
# With the cap, the timer fires at min(now + DEBOUNCE_MS, first_seen + MAX) --
# so a quiet key still gets full coalescing, and a continuously-written key is
# guaranteed to be re-aggregated within EDEN_DEBOUNCE_MAX_MS of its first change.
# Recompute reads current Silver and upserts idempotently, so firing mid-burst is
# always safe: it publishes a correct intermediate state, and the next event
# simply re-arms for another pass.
DEBOUNCE_MAX_MS = int(os.environ.get("EDEN_DEBOUNCE_MAX_MS", "2000"))

# Checkpoint storage — Bronze/Silver both pin this to a real filesystem path
# (BRONZE_CHECKPOINT_PATH / SILVER_CHECKPOINT_PATH); the Gold streams never did,
# so they silently ran on Flink's default JobManager (heap) checkpoint storage.
# That means Gold's checkpoints lived only in the JobManager's memory: a
# JobManager restart lost them entirely, and recovery fell back to a fresh
# submission from KAFKA_STARTING_OFFSETS (`latest`), silently dropping whatever
# CDC backlog was queued. Same code default shape as bronze/silver; compose
# overrides it to the /opt/flink/checkpoints bind mount.
CHECKPOINT_PATH = os.environ.get("GOLD_CHECKPOINT_PATH", "file:///tmp/flink-checkpoints/gold/")

# Checkpoint timeout. Was never set, so Flink's 10-minute default applied: a
# task thread blocked inside EdenReaggregator.on_timer (which runs every pending
# collection's build_documents SYNCHRONOUSLY) could hold a checkpoint in-flight
# for the full 10 minutes before it was declared expired -- observed on prod
# 2026-08-25 as "Checkpoint expired before completing" on 6 of the last 7.
#
# The 10-minute default is longer than flink_supervisor.py's stall detector
# (SUPERVISOR_STALL_THRESHOLD_S, which measures the age of the last COMPLETED
# checkpoint), so the supervisor always killed the job before Flink could fail
# the checkpoint honestly -- and that kill path (cancel_with_savepoint) needs
# the same checkpoint barrier that is stuck, so it timed out too and the
# resubmit fell back to `latest`. Bounding this to 3 minutes lets Flink's own
# tolerable-failure counter act first; SUPERVISOR_STALL_THRESHOLD_S is raised
# above interval+timeout in the compose files so the supervisor is the backstop
# rather than the first responder.
CHECKPOINT_TIMEOUT_MS = int(os.environ.get("GOLD_CHECKPOINT_TIMEOUT_MS", "180000"))

# EdenCdcRouter._lookup_project_ids runs a SYNCHRONOUS Postgres query on the
# source operator's thread for the four _LOOKUP_TABLES, so a slow one stalls CDC
# consumption itself, not just one key's recompute. Logging every lookup would
# be per-CDC-event noise on a hot topic; log only the ones that actually cost
# something, so the signal stays proportional to the problem.
SLOW_LOOKUP_MS = int(os.environ.get("GOLD_SLOW_LOOKUP_MS", "250"))

# Sentinel projectId used for tenant-grain triggers (tenant_dashboard + ref data)
_TENANT_SCOPE = "__tenant__"

# Order in which on_timer recomputes the pending collections.
#
# This is load-bearing, not cosmetic. on_timer builds collections SEQUENTIALLY on
# the task thread and yields each one's documents as soon as that collection
# finishes, so position in this tuple directly determines how soon a collection
# reaches Cosmos. Previously the pending set was stored `sorted()`, i.e. plain
# alphabetical, which put project_health THIRD (invoice_summary,
# procurement_status, project_health, task_intelligence) purely by accident of
# spelling — it waited on two unrelated collections before it even started.
#
# project_health leads because it is the collection EDEN's Insight agent reads
# for "how is this project doing" — the question most likely to be asked right
# after a project is created, and the one whose staleness is most visible.
# task_intelligence trails because it is consistently the most expensive build,
# so putting it last lets the three cheaper collections land while it runs
# rather than queueing behind it.
#
# Ordering has no effect on correctness: each collection writes a different
# Cosmos collection under its own filter key, they share no state within
# on_timer, and gold_ts is stamped once before the loop so every document from
# one fire carries the same timestamp regardless of order.
_COLLECTION_PRIORITY: tuple[str, ...] = (
    "project_health",
    "invoice_summary",
    "procurement_status",
    "task_intelligence",
    "workforce_status",
    "bid_context",
    "tenant_dashboard",
)
_PRIORITY_INDEX = {name: i for i, name in enumerate(_COLLECTION_PRIORITY)}


def _ordered_collections(collections) -> list[str]:
    """Return `collections` in recompute priority order, deterministically.

    Unknown names (a collection added to the routing map but not to
    _COLLECTION_PRIORITY) sort last, alphabetically among themselves, rather
    than raising — a missing priority entry should cost that collection its
    place in the queue, never drop it from the recompute or crash the job.
    """
    return sorted(
        collections,
        key=lambda c: (_PRIORITY_INDEX.get(c, len(_COLLECTION_PRIORITY)), c),
    )

# Each per-project collection → its loader module's build_documents().
# tenant_dashboard is handled separately (tenant grain).
_ALL_PROJECT_BUILDERS: dict[str, Any] = {
    "project_health":     load_project_health,
    "invoice_summary":    load_invoice_summary,
    "task_intelligence":  load_task_intelligence,
    "workforce_status":   load_workforce_status,
    "procurement_status": load_procurement_status,
    "bid_context":        load_bid_context,
}

# Mongo upsert key per collection.
_ALL_FILTER_FIELDS: dict[str, list[str]] = {
    "project_health":     ["tenantId", "projectId", "_env", "sourceEnv"],
    "invoice_summary":    ["tenantId", "projectId", "_env", "sourceEnv"],
    "task_intelligence":  ["tenantId", "projectId", "_env", "sourceEnv"],
    "workforce_status":   ["tenantId", "projectId", "_env", "sourceEnv"],
    "procurement_status": ["tenantId", "projectId", "_env", "sourceEnv"],
    "bid_context":        ["tenantId", "projectId", "_env", "sourceEnv"],
    # tenant_dashboard is tenant-grain (one doc per tenant); _env still needed
    # so a dev rebuild does not overwrite staging's tenant doc on the shared cluster.
    "tenant_dashboard":   ["tenantId", "_env", "sourceEnv"],
}

# Silver source_table → set of Eden collections to re-aggregate.
# "tenant_dashboard" entries are routed to the tenant-grain trigger.
_ALL_TABLE_TO_COLLECTIONS: dict[str, frozenset[str]] = {
    "project":              frozenset({"project_health", "invoice_summary",
                                        "task_intelligence", "procurement_status",
                                        "bid_context", "tenant_dashboard"}),
    "task":                 frozenset({"project_health", "task_intelligence",
                                        "workforce_status", "tenant_dashboard"}),
    # resource_schedule triggers workforce_status directly (own project_id)
    # AND project_health via a lookup-join (added 2026-08-01) — see
    # _LOOKUP_TABLES / EdenCdcRouter._lookup_project_ids below: a resource
    # completing an assignment on project A is evidence for OTHER projects
    # this resource is also assigned to (scheduleRevisions[].evidence, Q2),
    # not for project A itself.
    "resource_schedule":    frozenset({"workforce_status", "project_health"}),
    "project_estimate":     frozenset({"project_health"}),
    # project_estimate_schedule is a junction table (estimate_id, schedule_id) with
    # no project_id column — the router cannot extract a project key from its CDC
    # events. project_estimate CDC already triggers project_health for the common
    # case (new estimate version). Proper support requires a lookup-join through
    # estimate_id → silver.project_estimate.project_id (tracked separately).
    "purchase_order":       frozenset({"project_health", "procurement_status"}),
    "purchase_order_items": frozenset({"procurement_status"}),
    "invoice":              frozenset({"project_health", "invoice_summary",
                                        "tenant_dashboard"}),
    "payment_receives":     frozenset({"invoice_summary"}),
    "project_schedule":     frozenset({"project_health", "tenant_dashboard"}),
    # bom also triggers project_health (added 2026-08-01): estimateHistory[].
    # confidence/.evidence (Q1 reasoning evidence) reconciles SUM(bom.total_cost)
    # against project_estimate.material_cost, so a bom-only change (no
    # project_estimate row touched) must still recompute project_health, not
    # just procurement_status. bom carries project_id directly, so no lookup
    # join is needed here (unlike material_cost/vendor_material_map below).
    "bom":                  frozenset({"procurement_status", "project_health"}),
    "answers":              frozenset({"bid_context"}),
    # tenant-wide reference data — no project_id on the event; refresh the
    # tenant dashboard only rather than fanning out to every project.
    "vendor":               frozenset({"tenant_dashboard"}),
    "question_template":    frozenset({"tenant_dashboard"}),
    "resource":             frozenset({"tenant_dashboard"}),
    # LOOKUP-JOIN TABLES (added 2026-08-01, fixing the gap noted above for
    # resource_schedule): material_cost/vendor_material_map feed
    # estimateHistory[].evidence (Q1 — the attributed vendor price change);
    # audit_log feeds scheduleRevisions[].evidence (Q2 — the reschedule
    # event). None carry a usable project_id directly — material_cost/
    # vendor_material_map are keyed by vendor_material_map_id/material_id
    # (many BOM lines across many projects can reference one), and
    # audit_log.resource_id is a project_uid, not a project_id. Unlike every
    # other table in this map, these three (plus resource_schedule's
    # project_health route above) are resolved via a synchronous per-event
    # Postgres lookup in EdenCdcRouter._lookup_project_ids() rather than a
    # pure field-mapping — an intentional exception to the "no per-event DB
    # lookup" rule used elsewhere (roles/role_permission/project_member in
    # gold_user_permissions_stream.py), justified because these are
    # comparatively low-volume tables (price-catalog updates, reschedule-
    # tagged audit events, resource assignment changes) — not the hot
    # task/timelog CDC path this router otherwise optimizes for.
    "material_cost":        frozenset({"project_health"}),
    "vendor_material_map":  frozenset({"project_health"}),
    "audit_log":            frozenset({"project_health"}),
}

# Tables whose project_id(s) must be resolved via a Postgres lookup rather
# than read directly off the CDC event — see EdenCdcRouter._lookup_project_ids.
_LOOKUP_TABLES = frozenset({
    "material_cost", "vendor_material_map", "audit_log", "resource_schedule",
})
# Safety cap on lookup fan-out (mirrors the "avoids fanning out to every
# project" rationale for tenant-wide reference tables above) — a single
# vendor_material_map_id/resource_id should only ever match a handful of
# projects in practice; this just bounds the worst case.
_LOOKUP_FANOUT_LIMIT = 200

# Phase 1 release gate (infra/config/gold_config.yml — eden.enabled_collections):
# drop any collection not in the allowlist from every routing/build/sink
# structure below, so disabled collections are never re-aggregated or written.
_PROJECT_BUILDERS: dict[str, Any] = {
    name: builder for name, builder in _ALL_PROJECT_BUILDERS.items()
    if is_collection_enabled("eden", name)
}
_FILTER_FIELDS: dict[str, list[str]] = {
    name: fields for name, fields in _ALL_FILTER_FIELDS.items()
    if is_collection_enabled("eden", name)
}
_TABLE_TO_COLLECTIONS: dict[str, frozenset[str]] = {
    table: frozenset(c for c in collections if c in _FILTER_FIELDS)
    for table, collections in _ALL_TABLE_TO_COLLECTIONS.items()
}

# Which tables carry the project key in `project_id` vs the project row's own `id`.
_PROJECT_ID_IS_OWN_ID = "project"

# Guard: every collection name that can appear in a CDC trigger (via
# _TABLE_TO_COLLECTIONS) must have an entry in _FILTER_FIELDS and a fail-state
# key in EdenReaggregator._fail — otherwise on_timer raises KeyError at runtime.
_ALL_ROUTED_COLLECTIONS = frozenset().union(*_TABLE_TO_COLLECTIONS.values())
assert _ALL_ROUTED_COLLECTIONS <= set(_FILTER_FIELDS), (
    f"Collections in _TABLE_TO_COLLECTIONS missing from _FILTER_FIELDS: "
    f"{_ALL_ROUTED_COLLECTIONS - set(_FILTER_FIELDS)}"
)


# ---------------------------------------------------------------------------
# Selective fan-out — skip rebuilding a collection whose inputs did not change
# ---------------------------------------------------------------------------
#
# THE PROBLEM. _TABLE_TO_COLLECTIONS routes on source table alone, so ANY change
# to silver.project rebuilds all four enabled collections. But the four read
# wildly different amounts of that row:
#
#     project_health      26 business columns (status, description, location,
#                         assigned_to, tags, customer, version, updated_at, ...)
#     invoice_summary      order_no, invalid
#     procurement_status   order_no, invalid
#     task_intelligence    invalid
#
# So editing a project's status/description/location/assignee/tags — the common
# case — rebuilds three collections that produce byte-identical documents. Three
# of four rebuilds are pure waste, each one a multi-table aggregation against
# Silver.
#
# WHY THE ROUTER CAN'T JUST DIFF. The Silver CDC connector applies
# ExtractNewRecordState, which flattens the Debezium envelope to the AFTER image
# only. The before image is dropped before the message reaches Kafka, so there
# is nothing to diff against. Recovering it would mean either removing the
# unwrap SMT (changing the payload shape for every other Gold stream that shares
# cdc_parser) or REPLICA IDENTITY FULL on the Silver tables (which writes the
# entire old row into the WAL on every update — a real cost on a CDC sink this
# write-heavy).
#
# WHAT WE DO INSTEAD. Hash only the columns a collection actually reads, and
# keep that hash in Flink keyed state per (tenant, project, collection). State
# is the "before". If the hash is unchanged, the rebuild is skipped. No
# connector change, no WAL cost, no impact on the other streams.
#
# SAFETY. Every uncertain path falls through to rebuilding:
#   * table not in _TABLE_COLUMN_READS      -> no hash emitted -> rebuild
#   * collection not in that table's map    -> no hash emitted -> rebuild
#   * no stored hash yet (first sight, state loss, savepoint restore) -> rebuild
#   * EDEN_SELECTIVE_FANOUT=false           -> rebuild (kill switch, no redeploy)
# The stored hash is committed only after a CLEAN build (see on_timer), so a
# failed or retrying rebuild can never leave a hash claiming work that did not
# happen. Wrongly rebuilding costs time; wrongly skipping costs correctness, so
# every branch errs toward rebuilding.
SELECTIVE_FANOUT = os.environ.get("EDEN_SELECTIVE_FANOUT", "true").strip().lower() != "false"

# Columns that change on EVERY write and carry no semantic content. They must be
# excluded or the hash always differs and the optimization silently does nothing.
# _s_lsn feeds the document's _silverLsn (the LSN guard) — skipping a rebuild
# leaves an older _silverLsn stored, which is safe because the guard compares
# with $lte, so a later genuine write still applies.
_LINEAGE_COLUMNS = frozenset({"_s_lsn", "_s_silver_ts"})

# source table -> collection -> the columns that collection's SQL reads from it.
# MUST match the loaders' actual SQL. tests/unit/flink/test_selective_fanout.py
# re-derives these from the loader modules and fails if they drift — that test is
# the guardrail, because a silently stale map means a collection quietly stops
# refreshing with no error anywhere.
#
# Covers every table that fans out to more than one enabled Eden collection:
# project (4-way), task/invoice/bom/purchase_order (2-way each). purchase_order_
# items and the other single-collection tables are absent deliberately — with
# only one target collection there is nothing to skip a rebuild AGAINST, so a
# map entry there would cost upkeep for zero benefit. Any table absent from this
# map keeps the previous rebuild-everything behaviour. Every entry below was
# derived by reading each loader's real SQL (aliased AND bare-column CTEs), not
# guessed from field names — see test_selective_fanout.py's module docstring
# for the extraction method, and its TestExtraTableReadSetsMatchSql for the
# per-table guardrail that fails if these drift from the SQL.
_TABLE_COLUMN_READS: dict[str, dict[str, frozenset[str]]] = {
    "project": {
        "project_health": frozenset({
            "_s_deleted_at", "_s_env", "_s_is_deleted", "_s_tenantid",
            "additional_info", "assigned_to", "created_at", "created_by",
            "customer", "customer_asset", "description", "id", "invalid",
            "job_done_by", "location", "order_no", "parent_project_id",
            "project_type", "project_uid", "reference_project_id",
            "state_change_history", "status", "tags", "tax_exemption",
            "team_members", "trade", "trade_type", "updated_at", "version",
            "zipcode",
        }),
        "invoice_summary": frozenset({
            "_s_deleted_at", "_s_env", "_s_is_deleted", "_s_tenantid",
            "id", "invalid", "order_no",
        }),
        "procurement_status": frozenset({
            "_s_deleted_at", "_s_env", "_s_is_deleted", "_s_tenantid",
            "id", "invalid", "order_no",
        }),
        "task_intelligence": frozenset({
            "_s_deleted_at", "_s_env", "_s_is_deleted", "_s_tenantid",
            "id", "invalid",
        }),
    },
    # project_health's task_agg (load_project_health.py) COUNTs rows and joins
    # task_attribute for their actual status/dates — it never reads a business
    # column off `task` itself, only scope + soft-delete + invalid + the join
    # key `id` (used to correlate task_attribute rows, never SELECTed). A task's
    # OWN fields (phase, task_order, ...) changing does not affect project_health
    # at all; only a task row appearing/disappearing/moving projects does. This
    # is deliberately narrower than task_intelligence's set below, not an
    # oversight -- confirmed by reading task_agg's SQL directly, not inferred.
    "task": {
        "project_health": frozenset({
            "_s_env", "_s_is_deleted", "_s_tenantid", "id", "invalid",
            "project_id",
        }),
        "task_intelligence": frozenset({
            "_s_env", "_s_is_deleted", "_s_tenantid", "created_at", "id",
            "invalid", "parent_project_id", "phase", "project_id",
            "task_order", "task_uid",
        }),
    },
    "invoice": {
        "invoice_summary": frozenset({
            "_s_env", "_s_is_deleted", "_s_tenantid", "additional_info",
            "balance", "codes", "created_at", "created_by", "customer_uid",
            "document_id", "due_date", "id", "invalid", "invoice_message",
            "invoice_no", "invoiced_amount", "issue_at", "items",
            "project_id", "reminder_at", "reminder_count", "reminder_status",
            "status", "tax_amount", "tax_info", "term", "total_amount",
            "type", "updated_at", "version",
        }),
        # project_health's invoice_agg only SUMs invoiced_amount and buckets by
        # status -- none of invoice_summary's richer detail (line items, payment
        # history, reminder fields) feeds project_health. Read UNALIASED (bare
        # columns) in that CTE, unlike every other entry in this map.
        "project_health": frozenset({
            "_s_env", "_s_is_deleted", "_s_tenantid", "invalid",
            "invoiced_amount", "project_id", "status",
        }),
    },
    "bom": {
        "procurement_status": frozenset({
            "_s_env", "_s_is_deleted", "_s_tenantid", "invalid",
            "material_name", "project_id", "quantity", "total_cost", "unit",
            "unit_cost", "vendor_material_map_id",
        }),
        # project_health's bom_agg/price_hist_agg (Q1 reasoning evidence,
        # estimateHistory[].evidence): reconciliation math + price-history
        # lookup. unit/unit_cost feed procurement_status only, not this.
        "project_health": frozenset({
            "_s_env", "_s_is_deleted", "_s_tenantid", "id", "invalid",
            "material_name", "project_id", "quantity", "total_cost",
            "vendor_material_map_id", "version",
        }),
    },
    "purchase_order": {
        # pending_approvals / po_silver_ts deliberately excluded: the LATERAL
        # subquery in load_procurement_status.py reuses the alias `po` for its
        # OWN output columns (an alias-shadowing naming collision, confirmed by
        # reading the SQL directly), not real silver.purchase_order columns.
        "procurement_status": frozenset({
            "_s_env", "_s_is_deleted", "_s_tenantid", "created_at", "id",
            "project_id", "status", "total", "vendor_id",
        }),
        # project_health embeds the full PO detail (po_full_agg) plus its
        # nested purchase_order_items via a correlated subquery -- much richer
        # than procurement_status's summary row.
        "project_health": frozenset({
            "_s_env", "_s_is_deleted", "_s_tenantid", "additional_info",
            "approved_by", "billing_address", "comment", "counter_offer_date",
            "created_at", "created_by", "document_id", "id", "invalid",
            "project_id", "reason", "reminder_at", "reminder_count",
            "reminder_status", "shipping_address", "status", "sub_total",
            "tax_info", "total", "updated_at", "vendor_id",
        }),
    },
}


def _content_hashes(table: str, record: dict[str, Any],
                    collections) -> dict[str, str]:
    """Hash each collection's read-set of `record`. {} means "always rebuild".

    json.dumps with sort_keys and default=str keeps the digest stable across
    dict ordering and non-JSON types (datetime, Decimal) that CDC payloads can
    carry. Any failure returns {} so the caller rebuilds rather than skips.
    """
    reads = _TABLE_COLUMN_READS.get(table)
    if not reads:
        return {}
    out: dict[str, str] = {}
    try:
        for coll in collections:
            cols = reads.get(coll)
            if not cols:
                continue          # unmapped collection -> no hash -> rebuild
            payload = json.dumps(
                {c: record.get(c) for c in sorted(cols - _LINEAGE_COLUMNS)},
                sort_keys=True, default=str,
            )
            out[coll] = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    except Exception:  # noqa: BLE001 — never let hashing break routing
        log.exception("content-hash failed | table=%s — falling back to full rebuild", table)
        return {}
    return out


def _record_fanout_skip(collections, tenant: str) -> None:
    """Count rebuilds avoided by selective fan-out. Best-effort, never raises.

    Worth a metric rather than only a debug log: if this counter is zero in
    production the optimization is silently inert (a drifted _TABLE_COLUMN_READS
    map, or lineage columns leaking into the hash), and that is invisible
    otherwise because the fallback is simply the old behaviour.
    """
    try:
        from gold.src.monitoring.metrics import record_loader_run
        for coll in collections:
            record_loader_run(
                f"flink.fanout_skipped.{coll}", tenant or "unknown",
                upserted=0, failed=0, elapsed_s=0.0,
            )
    except Exception:  # noqa: BLE001
        pass


def _record_build_timing(collection: str, tenant: str, elapsed_s: float, docs: int) -> None:
    """Emit per-collection build cost to Prometheus. Best-effort, never raises.

    Deliberately a DIFFERENT metric label from mongo_sink's
    record_loader_run("flink.<collection>", ...): that one measures the Cosmos
    bulk_write, this one measures the Silver-side build_documents() call. They
    are the two halves of the Silver->Gold cost and conflating them under one
    label is exactly what made the ~6-minute latency unattributable.
    """
    try:
        from gold.src.monitoring.metrics import record_loader_run
        record_loader_run(
            f"flink.build.{collection}", tenant or "unknown",
            upserted=docs, failed=0, elapsed_s=elapsed_s,
        )
    except Exception:  # noqa: BLE001 — metrics must never break the stream
        pass


# ---------------------------------------------------------------------------
# EdenCdcRouter — maps each CDC event to trigger key(s)
# ---------------------------------------------------------------------------

class EdenCdcRouter(FlatMapFunction):
    """Emit trigger records {tenantId, projectId, collections} per CDC event.

    A change on a table that feeds project-grain collections is keyed on the
    affected (tenantId, projectId).  Collections that are tenant-grain
    (tenant_dashboard) — and changes to tenant-wide reference tables — are
    keyed on (tenantId, __tenant__).

    Four tables (_LOOKUP_TABLES — material_cost, vendor_material_map,
    audit_log, resource_schedule) don't carry a usable project_id directly
    and are resolved via a synchronous per-event Postgres lookup instead —
    see _lookup_project_ids and the module docstring's "LOOKUP-JOIN TABLES"
    section for the full rationale.
    """

    def open(self, ctx: RuntimeContext) -> None:
        self._pg_dsn = get_postgres_dsn()
        self._conn = None  # lazy-connect; only opened if a lookup table is seen

    def close(self) -> None:
        if getattr(self, "_conn", None) is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _cursor(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self._pg_dsn)
            # autocommit is REQUIRED here, not a preference. psycopg2 defaults
            # to autocommit=False, so a bare SELECT opens a transaction that
            # stays open until commit/rollback. _lookup_project_ids only closes
            # the CURSOR, so this long-lived connection sat "idle in
            # transaction" between events -- observed on prod 2026-08-24 at 22+
            # minutes per subtask.
            #
            # That is not merely untidy: an open transaction pins Silver's xmin
            # horizon, so autovacuum cannot reclaim dead tuples for as long as
            # the operator is idle. Silver then bloats, planner row estimates
            # inflate, and the same queries this job runs get slower -- the
            # operator degrades the storage it depends on.
            #
            # Every lookup here is a read; there is nothing to commit. Setting
            # autocommit ends each statement's transaction immediately and
            # covers the exception path too, which an explicit commit() after
            # each execute() would not.
            self._conn.autocommit = True
        return self._conn.cursor()

    def _lookup_project_ids(self, table: str, tenant_id: str, env: "str | None",
                             record: dict[str, Any]) -> list[str]:
        """Resolve the project_id(s) affected by a lookup-only table's CDC event.

        Returns [] (never raises) on any resolvable-but-absent key, an
        unrecognised table, or a DB error — a failed/skipped lookup means
        that project's confidence/evidence just stays stale until the next
        real change to the same lookup row re-triggers this router (--rebuild
        is a no-op for eden — see step_registry.py), not a job crash.
        """
        try:
            if table == "material_cost":
                vmm_id = record.get("vendor_material_map_id")
                if vmm_id is None:
                    return []
                sql = ("SELECT DISTINCT project_id FROM silver.bom "
                       "WHERE _s_tenantid = %s AND _s_env = %s "
                       "AND vendor_material_map_id = %s AND _s_is_deleted = FALSE "
                       "AND invalid = FALSE LIMIT %s")
                params = (tenant_id, env, vmm_id, _LOOKUP_FANOUT_LIMIT)
            elif table == "vendor_material_map":
                vmm_id = record.get("id")
                if vmm_id is None:
                    return []
                sql = ("SELECT DISTINCT project_id FROM silver.bom "
                       "WHERE _s_tenantid = %s AND _s_env = %s "
                       "AND vendor_material_map_id = %s AND _s_is_deleted = FALSE "
                       "AND invalid = FALSE LIMIT %s")
                params = (tenant_id, env, vmm_id, _LOOKUP_FANOUT_LIMIT)
            elif table == "audit_log":
                # Filter to reschedule-tagged events BEFORE the DB round trip —
                # audit_log is a high-volume append-only compliance trail
                # (100-300 events/min/tenant per docs/architecture); most
                # events are irrelevant to Q2 evidence and must not each cost
                # a lookup query.
                action = (record.get("action") or "")
                if "RESCHEDUL" not in action.upper():
                    return []
                project_uid = record.get("resource_id")
                if not project_uid:
                    return []
                sql = ("SELECT id FROM silver.project WHERE _s_tenantid = %s "
                       "AND _s_env = %s AND project_uid = %s AND _s_is_deleted = FALSE "
                       "LIMIT 1")
                params = (tenant_id, env, project_uid)
            elif table == "resource_schedule":
                resource_id = record.get("resource_id")
                own_project_id = record.get("project_id")
                if resource_id is None:
                    return []
                sql = ("SELECT DISTINCT project_id FROM silver.resource_schedule "
                       "WHERE _s_tenantid = %s AND _s_env = %s AND resource_id = %s "
                       "AND project_id IS DISTINCT FROM %s AND _s_is_deleted = FALSE "
                       "LIMIT %s")
                params = (tenant_id, env, resource_id, own_project_id, _LOOKUP_FANOUT_LIMIT)
            else:
                return []

            _t0 = time.monotonic()
            cur = self._cursor()
            cur.execute(sql, params)
            rows = [str(r[0]) for r in cur.fetchall() if r and r[0] is not None]
            cur.close()
            _elapsed_ms = (time.monotonic() - _t0) * 1000
            if _elapsed_ms >= SLOW_LOOKUP_MS:
                log.warning(
                    "EdenCdcRouter slow lookup-join | table=%s tenant=%s "
                    "elapsed_ms=%.0f rows=%d — this blocks CDC consumption",
                    table, tenant_id, _elapsed_ms, len(rows),
                )
            return rows
        except Exception:
            log.exception("EdenCdcRouter lookup-join failed | table=%s tenant=%s", table, tenant_id)
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
            return []

    def flat_map(self, raw: str) -> Iterator[dict[str, Any]]:
        record = _parse_cdc(raw)
        if not record:
            return
        table = record.get("_source_table")
        collections = _TABLE_TO_COLLECTIONS.get(table)
        if not collections:
            return
        tenant_id = record.get("_tenantid")
        if not tenant_id:
            return

        # Lookup-join path: resolve project_health's project_id(s) via a
        # Postgres query instead of reading them off the event. Runs BEFORE
        # (and independently of) the direct-project_id path below so
        # resource_schedule can still also trigger workforce_status directly.
        if table in _LOOKUP_TABLES and "project_health" in collections:
            env = record.get("_s_env")
            for pid in self._lookup_project_ids(table, tenant_id, env, record):
                yield {
                    "tenantId":    str(tenant_id),
                    "projectId":   str(pid),
                    "collections": ["project_health"],
                }

        # Direct project_id path — skip project_health here for lookup
        # tables (already handled above); everything else is unaffected.
        direct_collections = collections - {"project_health"} if table in _LOOKUP_TABLES else collections
        if not direct_collections:
            return

        # Resolve the project key: own id for `project`, else project_id.
        if table == _PROJECT_ID_IS_OWN_ID:
            project_id = record.get("id")
        else:
            project_id = record.get("project_id")

        project_cols = sorted(c for c in direct_collections if c != "tenant_dashboard")
        has_tenant_col = "tenant_dashboard" in direct_collections

        # Project-grain re-aggregation (needs a project_id).
        if project_cols and project_id:
            # Per-collection hash of just that collection's read-set, so the
            # reaggregator can skip a rebuild whose inputs are unchanged. Empty
            # dict (unmapped table, or any failure) => rebuild everything, i.e.
            # exactly the previous behaviour.
            yield {
                "tenantId":    str(tenant_id),
                "projectId":   str(project_id),
                "collections": project_cols,
                "hashes":      _content_hashes(table, record, project_cols),
            }

        # Tenant-grain re-aggregation (tenant_dashboard + reference-table changes).
        if has_tenant_col:
            yield {
                "tenantId":    str(tenant_id),
                "projectId":   _TENANT_SCOPE,
                "collections": ["tenant_dashboard"],
            }


# ---------------------------------------------------------------------------
# EdenReaggregator — debounced re-aggregation per (tenantId, projectId)
# ---------------------------------------------------------------------------

class EdenReaggregator(KeyedProcessFunction):
    """Debounce CDC triggers per key, then re-run the affected loaders' SQL.

    On each trigger we union the requested collections into ValueState and
    (re)arm a processing-time timer DEBOUNCE_MS in the future.  When the timer
    fires we re-aggregate every pending collection for the key and emit one
    tagged document per result: {"_collection": <name>, ...doc}.

    Failure handling (R2 parity with the BPE/QA streams): each collection is run
    through ``recompute_with_retry`` with its OWN per-collection fail-state, so a
    transient failure on one collection is retried (exponential backoff via a
    re-registered timer) and, if it exhausts retries, quarantined + audited —
    rather than dropped to self-heal on the next unrelated CDC change. A failing
    collection stays in ``pending`` (its fail-state > 0) so the retry timer
    re-processes only it; succeeded/quarantined collections drop out.
    """

    def open(self, ctx: RuntimeContext) -> None:
        self._pending = ctx.get_state(
            ValueStateDescriptor("pending_collections", Types.PICKLED_BYTE_ARRAY())
        )
        self._timer = ctx.get_state(
            ValueStateDescriptor("timer_ts", Types.LONG())
        )
        # Per-collection retry timer timestamps — dict[collection, timer_ts].
        # Stored so process_element can cancel them when a new CDC event arrives,
        # preventing phantom on_timer fires from accumulating after recovery.
        self._retry_timers = ctx.get_state(
            ValueStateDescriptor("retry_timer_ts_map", Types.PICKLED_BYTE_ARRAY())
        )
        # One fail-counter ValueState per collection — recompute_with_retry needs
        # an independent counter per recompute target (Eden recomputes several
        # collections per key, unlike the single-target BPE/QA streams).
        self._fail = {
            coll: ctx.get_state(ValueStateDescriptor(f"recompute_fail_{coll}", Types.INT()))
            for coll in _FILTER_FIELDS
        }
        # --- Instrumentation state (see the timing block in on_timer) ---------
        # Processing time at which the FIRST trigger of the current debounce
        # window arrived, plus how many triggers that window coalesced. Together
        # these measure what the debounce actually costs in practice: a project
        # create writes project/project_schedule/task/answers/project_member in
        # a burst, and every one of those re-arms the timer (process_element
        # below), so the real wait can be much longer than DEBOUNCE_MS. Without
        # this the only way to tell a long debounce from a slow query was to
        # guess.
        self._first_seen = ctx.get_state(
            ValueStateDescriptor("first_trigger_ts", Types.LONG())
        )
        self._trigger_count = ctx.get_state(
            ValueStateDescriptor("trigger_count", Types.INT())
        )
        # --- Selective fan-out state -----------------------------------------
        # _content_hash: the read-set hash of the last CLEANLY BUILT document per
        # collection. _pending_hash: hashes observed for collections currently in
        # `pending`, promoted into _content_hash by on_timer only after a clean
        # build -- so a failed or retrying rebuild never leaves a hash claiming
        # work that did not happen, which would skip the retry and strand the
        # collection permanently stale.
        self._content_hash = {
            coll: ctx.get_state(ValueStateDescriptor(f"content_hash_{coll}", Types.STRING()))
            for coll in _FILTER_FIELDS
        }
        self._pending_hash = ctx.get_state(
            ValueStateDescriptor("pending_content_hash", Types.PICKLED_BYTE_ARRAY())
        )
        # psycopg2 DSN resolved once per task (not per event).
        self._pg_dsn = get_postgres_dsn()

    def process_element(
        self, trigger: dict[str, Any], ctx: "KeyedProcessFunction.Context"
    ) -> Iterator[dict[str, Any]]:
        incoming = list(trigger.get("collections", []))
        hashes: dict = trigger.get("hashes") or {}

        # Selective fan-out: drop collections whose read-set is provably
        # unchanged since their last clean build. Only a collection with BOTH an
        # incoming hash AND a stored hash that matches is skipped -- every other
        # case (no hash, no stored value, kill switch off) rebuilds as before.
        pending_hash: dict = self._pending_hash.value() or {}
        selected: list[str] = []
        skipped: list[str] = []
        for coll in incoming:
            h = hashes.get(coll) if SELECTIVE_FANOUT else None
            if h:
                state = self._content_hash.get(coll)
                if state is not None and state.value() == h:
                    skipped.append(coll)
                    continue
                # Remember what this rebuild is meant to produce; on_timer
                # promotes it to _content_hash only if the build succeeds.
                pending_hash[coll] = h
            selected.append(coll)

        if skipped:
            log.debug(
                "selective fan-out skipped unchanged | tenant=%s project=%s skipped=%s",
                trigger.get("tenantId"), trigger.get("projectId"), ",".join(skipped),
            )
            _record_fanout_skip(skipped, str(trigger.get("tenantId") or ""))

        pending: set[str] = set(self._pending.value() or [])
        pending.update(selected)
        # Nothing new to do and nothing already queued -> don't arm a timer at
        # all. This is where the win actually lands: a project edit touching only
        # project_health columns stops waking the other three collections.
        if not pending:
            return iter(())
        self._pending.update(_ordered_collections(pending))
        if pending_hash:
            self._pending_hash.update(pending_hash)

        # Cancel any pending per-collection retry timers before arming the new
        # debounce timer so they don't fire as phantom on_timer calls.
        retry_timers: dict = self._retry_timers.value() or {}
        for ts in retry_timers.values():
            ctx.timer_service().delete_processing_time_timer(ts)
        self._retry_timers.clear()

        now = ctx.timer_service().current_processing_time()

        # Window start — set on the first trigger, cleared by on_timer. Also read
        # back there as `wait_ms` for the timing log, so this single piece of
        # state both bounds the coalescing and measures what it cost.
        first_seen = self._first_seen.value()
        if not first_seen:
            first_seen = now
            self._first_seen.update(first_seen)
        self._trigger_count.update((self._trigger_count.value() or 0) + 1)

        # (Re)arm a single debounce timer — coalesce bursts into one re-aggregate,
        # but never postpone past first_seen + DEBOUNCE_MAX_MS (see the constant's
        # comment: unbounded re-arming can starve a continuously-written key).
        # Clamped to `now` so a key that has already blown through the cap fires
        # on the next tick rather than registering a timer in the past.
        fire_at = max(now, min(now + DEBOUNCE_MS, first_seen + DEBOUNCE_MAX_MS))

        existing = self._timer.value()
        if existing:
            ctx.timer_service().delete_processing_time_timer(existing)
        ctx.timer_service().register_processing_time_timer(fire_at)
        self._timer.update(fire_at)
        return iter(())

    def on_timer(
        self, timestamp: int, ctx: "KeyedProcessFunction.OnTimerContext"
    ) -> Iterator[dict[str, Any]]:
        # Re-apply priority order here rather than trusting the stored order:
        # state restored from a savepoint written by an older build carries the
        # old alphabetical order, and this makes the first fire after an upgrade
        # behave like every later one.
        pending = _ordered_collections(self._pending.value() or [])
        self._timer.clear()
        if not pending:
            return

        key = ctx.get_current_key()           # "tenantId|projectId"
        tenant_id, _, project_id = key.partition("|")
        gold_ts = datetime.now(timezone.utc)

        is_tenant_scope = project_id == _TENANT_SCOPE
        scoped_project = None if is_tenant_scope else project_id

        # --- Instrumentation: how long did this key wait before we got here? --
        # `wait_ms` is debounce + coalescing + any head-of-line queueing behind
        # other keys on this subtask. A value near DEBOUNCE_MS means the debounce
        # behaved; a much larger one means either the burst kept re-arming the
        # timer or this subtask was busy in another key's recompute. `triggers`
        # separates those two: many triggers => re-arming, few triggers with a
        # large wait => queued behind someone else.
        _first = self._first_seen.value()
        _now = ctx.timer_service().current_processing_time()
        wait_ms = (_now - _first) if _first else -1
        triggers = self._trigger_count.value() or 0
        self._first_seen.clear()
        self._trigger_count.clear()
        _t_timer_start = time.monotonic()
        _timings: list[str] = []

        # Hashes staged by process_element for the collections in `pending`,
        # promoted into _content_hash below only for builds that come back clean.
        _pending_hash: dict = dict(self._pending_hash.value() or {})

        new_retry_timers: dict[str, int] = {}
        for collection in pending:
            if collection == "tenant_dashboard":
                build = lambda: load_tenant_dashboard.build_documents(
                    self._pg_dsn, tenant_id, gold_ts
                )
            else:
                module = _PROJECT_BUILDERS.get(collection)
                if module is None:
                    continue
                build = lambda m=module: m.build_documents(
                    self._pg_dsn, tenant_id, gold_ts, project_id=scoped_project
                )

            # Bounded retry + quarantine per collection (R2). On a transient
            # failure the guard re-registers a processing-time timer and returns
            # ([], retry_ts); the collection stays in `pending` (its fail-state > 0)
            # so the next fire re-processes only it. On success/quarantine its
            # fail-state is cleared and it drops out below.
            _fail_before = self._fail[collection].value() or 0
            _t0 = time.monotonic()
            docs, retry_ts = recompute_with_retry(
                build,
                fail_state=self._fail[collection],
                timer_service=ctx.timer_service(),
                feature=f"eden.{collection}",
                tenant=tenant_id,
                project_id=project_id,
                log=log,
            )
            _elapsed = time.monotonic() - _t0

            # Per-collection build cost. This is the number that was missing:
            # the four build_documents() calls run sequentially in this loop, so
            # the only way to attribute the total was to time each one here.
            # Logged per collection AND accumulated into the one-line summary
            # below, so a single grep gives the whole breakdown for one project.
            _timings.append(f"{collection}={_elapsed * 1000:.0f}ms/{len(docs)}d")
            log.info(
                "eden recompute timing | tenant=%s project=%s collection=%s "
                "elapsed_ms=%.1f docs=%d retry=%s",
                tenant_id, project_id, collection, _elapsed * 1000,
                len(docs), retry_ts is not None,
            )
            _record_build_timing(collection, tenant_id, _elapsed, len(docs))

            # Promote the pending hash only on a demonstrably clean build:
            # no retry scheduled, no failure streak before this attempt, and no
            # failure recorded after it. Deliberately conservative -- a build
            # that succeeds while recovering from an earlier failure
            # (_fail_before > 0) does NOT commit its hash, so the next event
            # rebuilds once more. Extra work is the safe direction; committing a
            # hash for a build that quarantined would skip the collection until
            # some unrelated column changed, stranding it stale.
            _clean = (
                retry_ts is None
                and _fail_before == 0
                and (self._fail[collection].value() or 0) == 0
            )
            if _clean and collection in _pending_hash:
                state = self._content_hash.get(collection)
                if state is not None:
                    state.update(_pending_hash.pop(collection))
            elif not _clean:
                # Build did not cleanly succeed — drop any stored hash so the
                # next CDC event for this key definitely rebuilds it.
                state = self._content_hash.get(collection)
                if state is not None:
                    state.clear()
                _pending_hash.pop(collection, None)

            if retry_ts is not None:
                new_retry_timers[collection] = retry_ts
            for doc in docs:
                tagged = dict(doc)
                tagged["_collection"] = collection
                yield tagged

        # Persist the retry timer timestamps so process_element can cancel them
        # if a new CDC event arrives before they fire.
        if new_retry_timers:
            self._retry_timers.update(new_retry_timers)
        else:
            self._retry_timers.clear()

        # Keep only collections still mid-retry (fail-state > 0); succeeded and
        # quarantined collections are dropped.
        still_failing = _ordered_collections(
            c for c in pending if (self._fail[c].value() or 0) > 0
        )
        if still_failing:
            self._pending.update(still_failing)
        else:
            self._pending.clear()

        # Keep staged hashes only for collections still queued; anything else was
        # either promoted into _content_hash or deliberately dropped above.
        _pending_hash = {c: h for c, h in _pending_hash.items() if c in still_failing}
        if _pending_hash:
            self._pending_hash.update(_pending_hash)
        else:
            self._pending_hash.clear()

        # One-line summary per fired timer — the Silver->Gold breakdown for a
        # single project, greppable as "eden on_timer". `wait_ms` covers
        # debounce + coalescing + head-of-line queueing; `build_ms` is the sum
        # of the sequential build_documents() calls broken out in `timings`.
        # Cosmos write latency is NOT here: the sink is downstream and batches
        # on its own 2s timer, so it is reported separately by mongo_sink's
        # record_loader_run("flink.<collection>", ...).
        log.info(
            "eden on_timer | tenant=%s project=%s wait_ms=%d triggers=%d "
            "build_ms=%.0f collections=%d timings=[%s]",
            tenant_id, project_id, wait_ms, triggers,
            (time.monotonic() - _t_timer_start) * 1000, len(pending),
            " ".join(_timings),
        )


# ---------------------------------------------------------------------------
# Job entry point
# ---------------------------------------------------------------------------

def build_job(env: StreamExecutionEnvironment) -> None:
    """Wire up the Eden real-time re-aggregation pipeline on the given env."""

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

    # Route CDC events → trigger keys → debounced re-aggregation.
    reaggregated = (
        cdc_stream
        .flat_map(EdenCdcRouter())
        .key_by(lambda r: f"{r['tenantId']}|{r['projectId']}")
        .process(EdenReaggregator())
    )

    # Fan out to one Mongo sink per collection, filtering by the tagged name.
    for collection, filter_fields in _FILTER_FIELDS.items():
        (
            reaggregated
            .filter(lambda r, _c=collection: r.get("_collection") == _c)
            .map(_strip_tag)
            .map(
                MongoUpsertSink(
                    db_name=GOLD_EDEN_DB,
                    collection_name=collection,
                    filter_fields=filter_fields,
                )
            )
            .name(f"Cosmos: {collection}")
        )


def _strip_tag(record: dict[str, Any]) -> dict[str, Any]:
    """Remove the internal routing tag before the document is upserted."""
    record.pop("_collection", None)
    return record


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    env = StreamExecutionEnvironment.get_execution_environment()

    # Add Kafka connector JAR — required when running via `python script.py`.
    _kafka_jar = _REPO_ROOT / "flink-jars" / "flink-sql-connector-kafka.jar"
    if _kafka_jar.exists():
        env.add_jars(f"file:///{_kafka_jar.as_posix()}")

    # Parallelism — GOLD_EDEN_PARALLELISM overrides the shared FLINK_PARALLELISM
    # so Eden can be scaled WITHOUT also scaling gold-canonical-stream, which
    # reads the same shared FLINK_PARALLELISM and has a very different profile
    # (its roles/role_permission/project_member triggers recompute a whole
    # tenant, so more subtasks there means more concurrent tenant-wide scans).
    #
    # Why this knob exists: EdenReaggregator.on_timer builds collections
    # sequentially and BLOCKS its subtask for the duration. Every
    # (tenantId|projectId) key is hashed onto one of N subtasks, so while one
    # project is mid-recompute every other project on that subtask waits --
    # head-of-line blocking. Raising N spreads keys over more subtasks and cuts
    # the probability of that collision proportionally.
    #
    # It is deliberately NOT the primary fix. Blocking hurts because a build can
    # take minutes; the project-scoping fixes take a build to ~0.3-1.4s, at
    # which point a queued key waits ~1s and the blocking is invisible. That is
    # a ~180x improvement against this knob's ~Nx. Deploy the query fixes first.
    #
    # Two ceilings to respect before raising this:
    #   * Task slots. The job graph is bronze 4 + silver 4 + eden N + canonical 2.
    #     If that exceeds taskmanager.numberOfTaskSlots the job cannot be placed,
    #     sits in RESTARTING holding no slots, and the supervisor's duplicate
    #     detection has to clean it up -- exactly the failure this stack already
    #     hit once (see flink_supervisor.py's _ACTIVE_JOB_STATES comment).
    #   * Postgres. pgbouncer runs DEFAULT_POOL_SIZE 20 shared with Silver, and
    #     each Eden subtask holds a long-lived router lookup connection plus one
    #     build connection at a time. N=4 is roughly 8-10 connections from Eden.
    #     Verify pool saturation before going beyond that.
    # The Kafka source is separately bounded by the CDC topic's partition count
    # (4 on prod.silver.cdc.events), so N>4 adds keyed-operator parallelism but
    # leaves extra source subtasks idle.
    env.set_parallelism(int(
        os.environ.get("GOLD_EDEN_PARALLELISM")
        or os.environ.get("FLINK_PARALLELISM", "2")
    ))
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
    # Without a min pause, a checkpoint that takes most of the 60s interval is
    # immediately followed by the next one, so a slow on_timer never gets an
    # unblocked window to drain in. Same 30s value Bronze and Silver both use.
    _cp.set_min_pause_between_checkpoints(30_000)
    _cp.set_checkpoint_storage(FileSystemCheckpointStorage(CHECKPOINT_PATH))

    build_job(env)

    log.info("Submitting Gold Eden real-time streaming job...")
    env.execute("gold-eden-stream")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
