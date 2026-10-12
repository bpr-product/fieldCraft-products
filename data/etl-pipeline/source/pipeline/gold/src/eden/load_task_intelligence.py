"""Silver → Gold loader: task_intelligence (Eden AI Assistant).

Reads silver.task and silver.resource to produce one document per project
containing overdue tasks (with assignee names), unassigned tasks, most
delayed task, and task completion counts.

Upsert key: (tenantId, projectId)

Exit codes: 0 success · 2 config error · 3 load failure
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse, urlunparse

import psycopg2
import psycopg2.extras
from pymongo import MongoClient, UpdateOne
from pymongo.collection import Collection
from pymongo.errors import (
    AutoReconnect, BulkWriteError, ConnectionFailure,
    NetworkTimeout, OperationFailure, ServerSelectionTimeoutError,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHARED_SRC = str(_REPO_ROOT / "shared")
_DATA_PIPELINE = str(_REPO_ROOT)
for _p in (_SHARED_SRC, _DATA_PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from secret_resolver import get_mongo_uri, get_postgres_dsn  # noqa: E402
from gold.src.monitoring.circuit_breaker import CircuitBreaker, CircuitOpenError  # noqa: E402
from gold.src.monitoring.contract_validator import ContractValidator  # noqa: E402
from gold.pipeline.cursor_store import (  # noqa: E402
    read_cursor, write_cursor, mark_error, get_max_silver_ts_per_env, EPOCH,
)
from gold.pipeline.lineage import lineage_fields, parse_ts, set_on_insert  # noqa: E402
from gold.pipeline.silver_schema import get_silver_columns, optional_value, table_exists  # noqa: E402

COLLECTION = "task_intelligence"
FEATURE_NAME = "eden"
_STEP_NAME = "load_task_intelligence"
_contract_validator = ContractValidator("eden_task_intelligence")
TRANSIENT_ERRORS = (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError,
                    CircuitOpenError)
log = logging.getLogger("eden.task_intelligence_loader")


@dataclass(frozen=True)
class LoaderConfig:
    mongo_uri: str
    pg_dsn: str
    db_name: str
    tenantid: str
    batch_size: int
    max_retries: int
    retry_backoff_s: float
    dry_run: bool
    max_docs: int = 0
    rebuild: bool = False

    @staticmethod
    def from_args_and_env(argv: list[str] | None = None) -> "LoaderConfig":
        p = argparse.ArgumentParser(description="Silver → Gold loader: task_intelligence")
        p.add_argument("--mongo-uri",     default=None)
        p.add_argument("--pg-dsn",        default=None)
        p.add_argument("--db",            dest="db_name",
                       default=os.environ.get("GOLD_EDEN_DB", "gold_eden"))
        p.add_argument("--tenant", required=not bool(os.environ.get("TENANT_ID")),
                       default=os.environ.get("TENANT_ID"))
        p.add_argument("--batch-size",    type=int, default=int(os.environ.get("BATCH_SIZE", "200")))
        p.add_argument("--max-retries",   type=int, default=int(os.environ.get("MAX_RETRIES", "5")))
        p.add_argument("--retry-backoff", type=float, default=float(os.environ.get("RETRY_BACKOFF_S", "1.5")))
        p.add_argument("--dry-run",       action="store_true")
        p.add_argument("--max-docs",      type=int, default=int(os.environ.get("MAX_DOCS", "0")))
        p.add_argument("--rebuild",       action="store_true",
                       help="Force full re-aggregate from current Silver (ignore the "
                            "newness check) and do NOT advance the cursor.")
        ns = p.parse_args(argv)
        return LoaderConfig(
            mongo_uri=ns.mongo_uri or get_mongo_uri(),
            pg_dsn=ns.pg_dsn or get_postgres_dsn(),
            db_name=ns.db_name, tenantid=ns.tenant,
            batch_size=ns.batch_size, max_retries=ns.max_retries,
            retry_backoff_s=ns.retry_backoff, dry_run=ns.dry_run,
            max_docs=ns.max_docs,
            rebuild=ns.rebuild,
        )


def _mask_uri(uri: str) -> str:
    try:
        u = urlparse(uri)
        if u.password:
            netloc = f"{u.username}:***@{u.hostname}"
            if u.port:
                netloc += f":{u.port}"
            return urlunparse(u._replace(netloc=netloc))
        return uri
    except Exception:
        return re.sub(r"://[^@]+@", "://***@", uri)


# One row per project — aggregates task lists as JSONB arrays
#
# silver.task.status is stored lowercase in practice ('scheduled', 'active',
# 'completed', 'cancelled', 'skipped', 'delete') — verified live, never the
# uppercase 'DONE'/'COMPLETED' the query previously compared against. That
# meant `status IN ('DONE','COMPLETED')` never matched a single real row, so
# doneTaskCount (and completionPct, derived from it) was always 0 regardless
# of actual completion, and every `NOT IN ('DONE','COMPLETED')` filter never
# excluded anything — overdueTasks/unassignedTasks/mostDelayedTask incorrectly
# included tasks that were actually completed, cancelled, skipped, or
# soft-deleted (status='delete') but merely past their end_time. Fixed to
# LOWER(status) = 'completed' (done) / LOWER(status) NOT IN
# ('completed','cancelled','skipped','delete') (still-open, could be overdue).
_SILVER_QUERY = """
WITH task_pivot AS (
    -- One row per silver.task, fully resolved: surviving flat columns read
    -- straight from silver.task; every ADO PR 7368 V54-dropped column reads
    -- ONLY from task_attribute (tae) — deliberately NOT falling back to
    -- silver.task's own frozen value, since that value is stale the moment
    -- V54 runs on a tenant's source Postgres and would otherwise silently
    -- serve stale data forever instead of surfacing the real gap. NULL
    -- until task_attribute/attributes exist in this env — self-healing via
    -- table_exists() same as everywhere else in this file (see
    -- _scoped_query()). task_additional_info is NOT read anywhere (its source table
    -- was already dropped by ASE's V45 migration — see
    -- fieldcraft-server's refactor-0000
    -- (remove-taskadditionalinfo-use-task-attribute.md); Java's
    -- TaskAdditionalInfoRepository was a permanent no-op stub even before
    -- that refactor started).
    --
    -- attribute_id is NEVER hardcoded — both the Java (TaskAttributeIds.java)
    -- and Python (task_attribute_ids.py) catalogs explicitly warn their
    -- numeric ids were only verified against one tenant (tn24047750) and are
    -- "environment-specific... differs per tenant/DB depending on which
    -- migration seeded the attributes catalog" — Gold spans every tenant, so
    -- ids are resolved by attribute_key (portable) via a join to the
    -- attributes catalog table instead (see _scoped_query()'s join below).
    --
    -- Per-field accounting (old task column -> new home), confirmed via the
    -- live V54 migration file + TaskAttributeIds.java/task_attribute_ids.py
    -- + schedule_writer.py's _CANONICAL_ATTR:
    --   status/task_type/resources/assignees/actions/queries       -> task_attribute (confirmed key, previously task_additional_info overlay too)
    --   task_name/parent_task_uid/is_milestone/estimated_days/
    --     estimated_hours/dependencies                             -> task_attribute (confirmed key, V54 drop-list additions)
    --   start_time/end_time                                        -> task_attribute keys scheduled_start/scheduled_end (confirmed RENAME, not same-key)
    --   allocated_unit/reference_id/reference_type/notes            -> task_attribute (never existed as raw task columns; came from task_additional_info, or new)
    --   reason                                                     -> NO confirmed key anywhere (schedule_writer.py's _TASK_FIELD_TO_ATTRIBUTE has a key but no pinned id, flagged unsafe by its own comments)
    --   reason/scheduled_type                                      -> task_attribute (confirmed live catalog rows on tn24047750, ids 537/540 — added
    --                                                                  after direct verification; no rows populated yet on that tenant as of this
    --                                                                  writing, but the key-based join makes this safe to read regardless)
    --   unit                                                       -> task_attribute, its OWN key (attribute_key='unit', e.g. 'EA') — verified
    --                                                                  live on tn24047750 22 active rows; NOT embedded in allocated_unit's JSON as
    --                                                                  originally assumed from the Java javadoc — allocated_unit has ZERO active
    --                                                                  rows on that tenant, so deriving unit from it would always be empty
    --   completed_unit/time_spent                                  -> confirmed absent from the attributes catalog entirely, even on tn24047750
    --                                                                  (the flagship reference tenant this whole EAV model was built against) —
    --                                                                  not just unpopulated, no catalog row exists at all. Treated as unmapped.
    --   invalid                                                    -> CONFIRMED GONE on tn24047750 (only 9 columns survive: id/task_uid/
    --                                                                  task_order/phase/project_id/parent_project_id/created_at/created_by/notes)
    --                                                                  — gated the same way as created_by (__INVALID_COL__/__INVALID_FILTER__),
    --                                                                  since referencing it unconditionally would be a hard SQL error, not a
    --                                                                  graceful null, on any tenant where it's already dropped
    --   tools/additional_data/schema_version/updated_at/version/
    --     version_start_date/version_end_date                      -> NO replacement found anywhere (task_version is project-scoped, no task_id column at all)
    SELECT
        at.id,
        at._s_tenantid,
        at._s_env,
        at._s_silver_ts,
        at.project_id,
        at.task_uid,
        at.task_order,
        at.phase,
        at.parent_project_id,
        at.created_at,
        __CREATED_BY_COL__                                  AS created_by,
        __INVALID_COL__                                      AS invalid,

        tae.status,
        tae.task_type,
        tae.resources,
        tae.assignees,
        tae.actions,
        tae.queries,
        tae.dependencies,
        tae.estimated_days,
        tae.estimated_hours,
        tae.task_name,
        tae.parent_task_uid,
        tae.is_milestone,
        tae.scheduled_start                                 AS start_time,
        tae.scheduled_end                                   AS end_time,
        tae.allocated_unit,
        tae.unit,
        tae.reference_id,
        tae.reference_type,
        tae.notes,
        tae.reason,
        tae.scheduled_type,

        -- V54-dropped, no confirmed tenant-portable task_attribute mapping —
        -- literal NULL, not read from anywhere (do not guess a key, do not
        -- trust the frozen raw value).
        NULL::text              AS completed_unit,
        NULL::double precision  AS time_spent,
        NULL::text              AS tools,
        NULL::text              AS additional_data,
        NULL::integer           AS schema_version,
        NULL::timestamptz       AS updated_at,
        NULL::integer           AS version,
        NULL::timestamptz       AS version_start_date,
        NULL::timestamptz       AS version_end_date,

        tp.payload             AS task_payload_payload,
        tp.scheduler_ready      AS task_payload_scheduler_ready,
        tp.ai_ready             AS task_payload_ai_ready,
        tp.payload_version      AS task_payload_version,
        tp.payload_updated_at   AS task_payload_updated_at

    FROM silver.task at
    __TAE_JOIN__
    __TP_JOIN__
    WHERE at._s_tenantid  = %(tenantid)s
      AND at._s_is_deleted = FALSE
      __INVALID_FILTER__
      -- Narrows task_pivot to one project in the real-time single-project
      -- call (see _scoped_query()) -- REQUIRED, not optional: every
      -- downstream reference to task_pivot below already filters on
      -- project_id = p.id, so without this, task_pivot still materializes
      -- every task in the tenant before those filters ever run. Same bug
      -- class/fix already applied to load_project_health.py's
      -- task_agg/estimate_latest CTEs.
      __TASK_SCOPE_FILTER__
)
SELECT
    p._s_tenantid                                                               AS tenantid,
    p._s_env                                                                     AS source_env,
    p._s_is_deleted                                                              AS is_deleted,
    p._s_deleted_at                                                              AS deleted_at,
    p._s_lsn                                                                     AS silver_lsn,
    p._s_silver_ts                                                               AS project_silver_ts,
    p.id                                                    AS project_id,

    -- Task counts
    COALESCE(t.total_tasks, 0)                              AS total_task_count,
    COALESCE(t.done_tasks, 0)                               AS done_task_count,
    COALESCE(t.overdue_count, 0)                            AS overdue_task_count,
    COALESCE(t.unassigned_count, 0)                         AS unassigned_task_count,

    -- Overdue task list (max 10, ordered by most overdue)
    t.overdue_tasks,

    -- Unassigned task list (max 10, ordered by due date)
    t.unassigned_tasks,

    -- Most delayed task
    t.most_delayed_task_name,
    t.most_delayed_days,
    t.most_delayed_task_id,

    -- Milestone summary (task_pivot.is_milestone)
    ms.total_milestones,
    ms.completed_milestones,
    ms.overdue_milestones,
    ms.next_milestone,
    ms.milestones,

    -- Full per-task detail — every active task in the project (no cap;
    -- verified live max ~136 tasks/project once invalid=FALSE is applied,
    -- well within Mongo's document size limit). See _shape_task() for how
    -- the free-text JSON-ish columns (assignees, dependencies, resources,
    -- tools, completedUnit, additionalData) are parsed.
    alltasks.tasks                                          AS all_tasks

FROM silver.project p

LEFT JOIN LATERAL (
    SELECT
        COUNT(*)                                                    AS total_tasks,
        COUNT(*) FILTER (WHERE LOWER(status) = 'completed')         AS done_tasks,

        COUNT(*) FILTER (
            WHERE end_time < NOW()
              AND LOWER(status) NOT IN ('completed','cancelled','skipped','delete')
        )                                                           AS overdue_count,

        COUNT(*) FILTER (
            WHERE (assignees IS NULL OR assignees::text = '[]')
              AND LOWER(status) NOT IN ('completed','cancelled','skipped','delete')
        )                                                           AS unassigned_count,

        -- Overdue tasks list with assignee names from resource join
        (
            SELECT JSONB_AGG(
                JSONB_BUILD_OBJECT(
                    'taskId',   ot.id::text,
                    'taskName', ot.task_name,
                    'daysLate', EXTRACT(DAY FROM NOW() - ot.end_time)::int,
                    'endTime',  ot.end_time::text,
                    'assignees', COALESCE(
                        (
                            -- assignee names are embedded in the assignees JSON
                            -- (task_pivot.assignees holds
                            --  [{"resourceUid","role","name"}, ...]) — no resource join needed
                            SELECT JSONB_AGG(elem->>'name' ORDER BY elem->>'name')
                            FROM JSONB_ARRAY_ELEMENTS(
                                     COALESCE(NULLIF(ot.assignees::text, ''), '[]')::jsonb
                                 ) elem
                            WHERE elem->>'name' IS NOT NULL
                        ),
                        '[]'::jsonb
                    ),
                    'reason',   ot.reason
                )
                ORDER BY ot.end_time ASC
            )
            FROM (
                SELECT id, task_name, end_time, assignees, reason
                FROM task_pivot
                WHERE _s_tenantid  = p._s_tenantid
                  AND _s_env       = p._s_env
                  AND project_id   = p.id
                  AND end_time     < NOW()
                  AND LOWER(status) NOT IN ('completed','cancelled','skipped','delete')
                ORDER BY end_time ASC
                LIMIT 10
            ) ot
        )                                                           AS overdue_tasks,

        -- Unassigned tasks list
        (
            SELECT JSONB_AGG(
                JSONB_BUILD_OBJECT(
                    'taskId',   ut.id::text,
                    'taskName', ut.task_name,
                    'endTime',  ut.end_time::text
                )
                ORDER BY ut.end_time ASC NULLS LAST
            )
            FROM (
                SELECT id, task_name, end_time
                FROM task_pivot
                WHERE _s_tenantid   = p._s_tenantid
                  AND _s_env        = p._s_env
                  AND project_id    = p.id
                  AND (assignees IS NULL OR assignees::text = '[]')
                  AND LOWER(status) NOT IN ('completed','cancelled','skipped','delete')
                ORDER BY end_time ASC NULLS LAST
                LIMIT 10
            ) ut
        )                                                           AS unassigned_tasks,

        -- Most delayed single task
        (
            SELECT task_name
            FROM task_pivot
            WHERE _s_tenantid  = p._s_tenantid
              AND _s_env       = p._s_env
              AND project_id   = p.id
              AND end_time     < NOW()
              AND LOWER(status) NOT IN ('completed','cancelled','skipped','delete')
            ORDER BY end_time ASC
            LIMIT 1
        )                                                           AS most_delayed_task_name,

        (
            SELECT EXTRACT(DAY FROM NOW() - end_time)::int
            FROM task_pivot
            WHERE _s_tenantid  = p._s_tenantid
              AND _s_env       = p._s_env
              AND project_id   = p.id
              AND end_time     < NOW()
              AND LOWER(status) NOT IN ('completed','cancelled','skipped','delete')
            ORDER BY end_time ASC
            LIMIT 1
        )                                                           AS most_delayed_days,

        (
            SELECT id::text
            FROM task_pivot
            WHERE _s_tenantid  = p._s_tenantid
              AND _s_env       = p._s_env
              AND project_id   = p.id
              AND end_time     < NOW()
              AND LOWER(status) NOT IN ('completed','cancelled','skipped','delete')
            ORDER BY end_time ASC
            LIMIT 1
        )                                                           AS most_delayed_task_id,

        MAX(_s_silver_ts)                                           AS task_silver_ts

    FROM task_pivot
    WHERE _s_tenantid  = p._s_tenantid
      AND _s_env       = p._s_env
      AND project_id   = p.id
) t ON TRUE

-- Milestone summary — tasks flagged is_milestone=TRUE for this project.
LEFT JOIN LATERAL (
    SELECT
        COUNT(*)                                                    AS total_milestones,
        COUNT(*) FILTER (WHERE LOWER(status) = 'completed')         AS completed_milestones,
        COUNT(*) FILTER (
            WHERE end_time < NOW()
              AND LOWER(status) NOT IN ('completed','cancelled','skipped','delete')
        )                                                           AS overdue_milestones,

        -- Next upcoming (not-done) milestone
        (
            SELECT JSONB_BUILD_OBJECT(
                'taskId',   nm.id::text,
                'taskName', nm.task_name,
                'endTime',  nm.end_time::text
            )
            FROM task_pivot nm
            WHERE nm._s_tenantid   = p._s_tenantid
              AND nm._s_env        = p._s_env
              AND nm.project_id    = p.id
              AND nm.is_milestone  = TRUE
              AND LOWER(nm.status) NOT IN ('completed','cancelled','skipped','delete')
            ORDER BY nm.end_time ASC NULLS LAST
            LIMIT 1
        )                                                           AS next_milestone,

        -- Milestone list (max 10, ordered by due date)
        (
            SELECT JSONB_AGG(
                JSONB_BUILD_OBJECT(
                    'taskId',    mt.id::text,
                    'taskName',  mt.task_name,
                    'endTime',   mt.end_time::text,
                    'status',    mt.status,
                    'isOverdue', (mt.end_time < NOW()
                                  AND LOWER(mt.status) NOT IN ('completed','cancelled','skipped','delete'))
                )
                ORDER BY mt.end_time ASC NULLS LAST
            )
            FROM (
                SELECT id, task_name, end_time, status
                FROM task_pivot
                WHERE _s_tenantid   = p._s_tenantid
                  AND _s_env        = p._s_env
                  AND project_id    = p.id
                  AND is_milestone  = TRUE
                ORDER BY end_time ASC NULLS LAST
                LIMIT 10
            ) mt
        )                                                           AS milestones

    FROM task_pivot
    WHERE _s_tenantid  = p._s_tenantid
      AND _s_env       = p._s_env
      AND project_id   = p.id
      AND is_milestone  = TRUE
) ms ON TRUE

-- Full task detail — every field already resolved in task_pivot above
-- (surviving raw silver.task columns, task_attribute pivot for V54-dropped
-- ones, NULL for fields with no confirmed replacement). See _shape_task()
-- for grouping into schedule/assignment/workDetails/reference/audit/
-- taskPayload sub-objects.
LEFT JOIN LATERAL (
    SELECT JSONB_AGG(
        JSONB_BUILD_OBJECT(
            'id',                 tv.id,
            'task_uid',           tv.task_uid,
            'task_order',         tv.task_order,
            'phase',              tv.phase,
            'parent_project_id',  tv.parent_project_id,
            'parent_task_uid',    tv.parent_task_uid,
            'task_name',          tv.task_name,
            'task_type',          tv.task_type,
            'status',             tv.status,
            'is_milestone',       tv.is_milestone,
            'invalid',            tv.invalid,
            'scheduled_type',     tv.scheduled_type,
            'start_time',         tv.start_time::text,
            'end_time',           tv.end_time::text,
            'estimated_days',     tv.estimated_days,
            'estimated_hours',    tv.estimated_hours,
            'time_spent',         tv.time_spent,
            'completed_unit',     tv.completed_unit,
            'dependencies',       tv.dependencies,
            'assignees',          tv.assignees,
            'resources',          tv.resources,
            'tools',              tv.tools,
            'actions',            tv.actions,
            'queries',            tv.queries,
            'reason',             tv.reason,
            'additional_data',    tv.additional_data,
            'created_at',         tv.created_at::text,
            'created_by',         tv.created_by,
            'updated_at',         tv.updated_at::text,
            'version',            tv.version,
            'version_start_date', tv.version_start_date::text,
            'version_end_date',   tv.version_end_date::text,
            'schema_version',     tv.schema_version,
            'allocated_unit',     tv.allocated_unit,
            'unit',                tv.unit,
            'reference_id',       tv.reference_id,
            'reference_type',     tv.reference_type,
            'notes',              tv.notes,
            'task_payload',       JSONB_BUILD_OBJECT(
                                      'payload',            tv.task_payload_payload,
                                      'scheduler_ready',    tv.task_payload_scheduler_ready,
                                      'ai_ready',           tv.task_payload_ai_ready,
                                      'payload_version',    tv.task_payload_version,
                                      'payload_updated_at', tv.task_payload_updated_at::text
                                  )
        )
        ORDER BY tv.end_time ASC NULLS LAST, tv.id ASC
    )                                                               AS tasks
    FROM task_pivot tv
    WHERE tv._s_tenantid  = p._s_tenantid
      AND tv._s_env       = p._s_env
      AND tv.project_id   = p.id
) alltasks ON TRUE

WHERE p._s_tenantid  = %(tenantid)s
  AND p.invalid       = FALSE
ORDER BY p.id
"""


# EAV pivot for task_attribute — resolves attribute_id by attribute_key via
# the attributes catalog (never hardcoded numeric ids; see the block comment
# above __TAE_JOIN__'s call site for why). Emitted only when both
# silver.task_attribute and silver.attributes exist.
_TAE_JOIN_REAL = """
    LEFT JOIN LATERAL (
        SELECT
            MAX(ta.attr_value_text)     FILTER (WHERE attrs.attribute_key = 'status')         AS status,
            MAX(ta.attr_value_text)     FILTER (WHERE attrs.attribute_key = 'task_type')      AS task_type,
            MAX(ta.attr_value_jsonb)    FILTER (WHERE attrs.attribute_key = 'actions')        AS actions,
            MAX(ta.attr_value_jsonb)    FILTER (WHERE attrs.attribute_key = 'queries')        AS queries,
            MAX(ta.attr_value_jsonb)    FILTER (WHERE attrs.attribute_key = 'resources')      AS resources,
            MAX(ta.attr_value_jsonb)    FILTER (WHERE attrs.attribute_key = 'assignees')      AS assignees,
            MAX(ta.attr_value_jsonb)    FILTER (WHERE attrs.attribute_key = 'allocated_unit') AS allocated_unit,
            MAX(ta.attr_value_text)     FILTER (WHERE attrs.attribute_key = 'reference_id')   AS reference_id,
            MAX(ta.attr_value_text)     FILTER (WHERE attrs.attribute_key = 'reference_type') AS reference_type,
            MAX(ta.attr_value_text)     FILTER (WHERE attrs.attribute_key = 'notes')          AS notes,
            -- V54 drop-list additions (confirmed pinned attribute_id, see
            -- refactor-21877-task-schema-eav-migration.md / schedule_writer.py
            -- _CANONICAL_ATTR): task_name=497, parent_task_uid=508,
            -- is_milestone=507, estimated_hours=178, estimated_days=511,
            -- dependencies=293. start_time/end_time are a confirmed semantic
            -- rename to scheduled_start=526/scheduled_end=527 (GoPAL's 6-field
            -- temporal model), not a 1:1 carry-over — see _shape_task().
            MAX(ta.attr_value_text)     FILTER (WHERE attrs.attribute_key = 'task_name')        AS task_name,
            MAX(ta.attr_value_text)     FILTER (WHERE attrs.attribute_key = 'parent_task_uid')   AS parent_task_uid,
            BOOL_OR(ta.attr_value_boolean) FILTER (WHERE attrs.attribute_key = 'is_milestone')   AS is_milestone,
            MAX(ta.attr_value_float)    FILTER (WHERE attrs.attribute_key = 'estimated_hours')   AS estimated_hours,
            MAX(ta.attr_value_float)    FILTER (WHERE attrs.attribute_key = 'estimated_days')    AS estimated_days,
            MAX(COALESCE(ta.attr_value_jsonb::text, ta.attr_value_text))
                                        FILTER (WHERE attrs.attribute_key = 'dependencies')      AS dependencies,
            MAX(ta.attr_value_datetime) FILTER (WHERE attrs.attribute_key = 'scheduled_start')   AS scheduled_start,
            MAX(ta.attr_value_datetime) FILTER (WHERE attrs.attribute_key = 'scheduled_end')     AS scheduled_end,
            -- unit is its OWN attribute_key (e.g. 'EA') — verified live on
            -- tn24047750, NOT embedded in allocated_unit's JSON as the Java
            -- javadoc implied (allocated_unit has zero active rows there).
            -- reason/scheduled_type: live catalog rows confirmed on
            -- tn24047750 (ids 537/540) but zero populated rows as of
            -- writing — safe to read regardless since this resolves by key.
            MAX(ta.attr_value_text)     FILTER (WHERE attrs.attribute_key = 'unit')             AS unit,
            MAX(ta.attr_value_text)     FILTER (WHERE attrs.attribute_key = 'reason')           AS reason,
            MAX(ta.attr_value_text)     FILTER (WHERE attrs.attribute_key = 'scheduled_type')   AS scheduled_type
        FROM silver.task_attribute ta
        JOIN silver.attributes attrs
          ON attrs.id           = ta.attribute_id
         AND attrs._s_tenantid  = ta._s_tenantid
         AND attrs._s_env       = ta._s_env
         AND attrs.namespace    = 'task'
         AND attrs.is_valid     = TRUE
        WHERE ta.task_id        = at.id
          AND ta._s_tenantid    = at._s_tenantid
          AND ta._s_env         = at._s_env
          AND ta.is_valid       = TRUE
          AND ta.version_effective_date <= NOW()
          AND ta.version_expiry_date    > NOW()
          AND ta._s_is_deleted  = FALSE
    ) tae ON TRUE
"""

_TAE_JOIN_INERT = """
    LEFT JOIN LATERAL (
        SELECT
            NULL::text  AS status, NULL::text AS task_type, NULL::jsonb AS actions,
            NULL::jsonb AS queries, NULL::jsonb AS resources, NULL::jsonb AS assignees,
            NULL::jsonb AS allocated_unit, NULL::text AS reference_id,
            NULL::text  AS reference_type, NULL::text AS notes,
            NULL::text  AS task_name, NULL::text AS parent_task_uid,
            NULL::boolean AS is_milestone, NULL::float AS estimated_hours,
            NULL::float AS estimated_days, NULL::text AS dependencies,
            NULL::timestamptz AS scheduled_start, NULL::timestamptz AS scheduled_end,
            NULL::text AS unit, NULL::text AS reason, NULL::text AS scheduled_type
    ) tae ON TRUE
"""

# task_payload — new ASE 2.0 data (not a task_additional_info replacement),
# the Python scheduling engine's versioned computed payload per task. Emitted
# only when silver.task_payload exists.
_TP_JOIN_REAL = """
    LEFT JOIN LATERAL (
        SELECT
            tp.payload         AS payload,
            tp.scheduler_ready AS scheduler_ready,
            tp.ai_ready        AS ai_ready,
            tp.payload_version AS payload_version,
            tp.updated_at      AS payload_updated_at
        FROM silver.task_payload tp
        WHERE tp.task_id       = at.id
          AND tp._s_tenantid   = at._s_tenantid
          AND tp._s_env        = at._s_env
          AND tp.is_latest     = TRUE
          AND tp._s_is_deleted = FALSE
        LIMIT 1
    ) tp ON TRUE
"""

_TP_JOIN_INERT = """
    LEFT JOIN LATERAL (
        SELECT
            NULL::text AS payload, NULL::boolean AS scheduler_ready,
            NULL::boolean AS ai_ready, NULL::integer AS payload_version,
            NULL::timestamptz AS payload_updated_at
    ) tp ON TRUE
"""


def _scoped_query(pg_dsn: str, project_id: str | None) -> str:
    """Return the Silver query, optionally scoped to a single project.

    When project_id is provided, splice an ``AND p.id = %(project_id)s``
    predicate into the outer WHERE clause so the same tested SQL runs for
    one project — used by the real-time streaming job.

    created_by was added to silver.task by ADO PR 7368 (NextGen ASE schema)
    but isn't yet present in every env during the migration window.
    __CREATED_BY_COL__ resolves to the real column once auto-schema-sync
    adds it, or NULL until then. invalid was previously believed CONFIRMED
    GONE on tn24047750 (an earlier live check found only 9 surviving
    columns) — that has since reversed: the column exists again table-wide
    (silver.task is one shared table across all tenant DBs, so its schema
    reflects whichever tenant's source still emits the field), but on
    tn24047750 specifically every row's value is NULL, never TRUE/FALSE
    (live-verified 2026-07-30: 36795/36795 rows). A plain `invalid = FALSE`
    filter therefore silently matched ZERO tasks for this tenant — NULL = FALSE
    evaluates to NULL, not TRUE, in a WHERE clause — emptying every Eden
    task_intelligence doc and gold_eden.project_health's task counts alike,
    even though the tasks themselves are very much present in Silver (61 for
    project 7867 alone). Fixed by treating NULL as "not superseded" (the same
    meaning a genuinely-absent column would have via __INVALID_COL__'s NULL
    fallback) rather than as excluded: `invalid IS NULL OR invalid = FALSE`.
    __TAE_JOIN__/__TP_JOIN__ resolve to the real task_attribute/attributes and
    task_payload joins once those tables exist in Silver, or an all-NULL
    inert join until then — same self-healing pattern, one level up (table
    existence, not just column existence).
    """
    cols = get_silver_columns(pg_dsn, "task")
    query = _SILVER_QUERY.replace("__CREATED_BY_COL__", optional_value("at", "created_by", cols))
    query = query.replace("__INVALID_COL__", optional_value("at", "invalid", cols))
    query = query.replace(
        "__INVALID_FILTER__",
        "AND (at.invalid IS NULL OR at.invalid = FALSE)" if "invalid" in cols else "",
    )
    query = query.replace(
        "__TASK_SCOPE_FILTER__",
        "AND at.project_id = %(project_id)s" if project_id is not None else "",
    )

    has_tae = table_exists(pg_dsn, "task_attribute") and table_exists(pg_dsn, "attributes")
    query = query.replace("__TAE_JOIN__", _TAE_JOIN_REAL if has_tae else _TAE_JOIN_INERT)

    has_tp = table_exists(pg_dsn, "task_payload")
    query = query.replace("__TP_JOIN__", _TP_JOIN_REAL if has_tp else _TP_JOIN_INERT)

    if project_id is None:
        return query
    return query.replace(
        "WHERE p._s_tenantid  = %(tenantid)s",
        "WHERE p._s_tenantid  = %(tenantid)s\n  AND p.id = %(project_id)s",
        1,
    )


def iter_silver_rows(
    pg_dsn: str, tenantid: str, max_docs: int = 0, project_id: str | None = None
) -> Iterator[dict]:
    conn = psycopg2.connect(pg_dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            params = {"tenantid": tenantid}
            if project_id is not None:
                params["project_id"] = project_id
            cur.execute(_scoped_query(pg_dsn, project_id), params)
            rows = cur if max_docs <= 0 else (r for _, r in zip(range(max_docs), cur))
            for row in rows:
                yield dict(row)
    finally:
        conn.close()


def build_documents(
    pg_dsn: str,
    tenantid: str,
    gold_ts: datetime,
    project_id: str | None = None,
    max_docs: int = 0,
) -> list[dict]:
    """Build shaped Gold documents for a tenant (or a single project).

    Reused by the real-time Eden streaming job (project_id set).
    """
    return [
        shape_doc(row, gold_ts)
        for row in iter_silver_rows(pg_dsn, tenantid, max_docs, project_id)
    ]


def _has_new_silver_rows(pg_dsn: str, tenantid: str, since: datetime) -> bool:
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM silver.project"
            "  WHERE _s_tenantid = %s AND _s_silver_ts > %s"
            ")",
            (tenantid, since),
        )
        return bool(cur.fetchone()[0])


def _get_max_silver_ts(pg_dsn: str, tenantid: str) -> datetime:
    # MIN of each _s_env's own MAX, not the overall MAX — see the canonical
    # loaders' get_max_silver_ts_per_env for why: a shared cursor advanced to
    # the fastest-moving env's watermark would mask a later update in a
    # slower-moving env still below that watermark.
    return get_max_silver_ts_per_env(pg_dsn, "project", "_s_tenantid", tenantid)


def _to_ts(v) -> "datetime | None":
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo is not None else v.replace(tzinfo=timezone.utc)
    return None


def _maybe_json(v):
    """Parse a free-text column that may hold JSON.

    Returns the parsed value (dict/list) when it is valid JSON, the original
    string when it isn't (so data is never silently dropped), or None for
    empty/null input. Used for silver.task/task_attribute TEXT columns
    (assignees, dependencies, resources, tools, completed_unit,
    additional_data) whose content isn't guaranteed to be JSON.
    """
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    if not isinstance(v, str):
        return v
    s = v.strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError, ValueError):
        return s


_TAE_SOURCED_FIELDS = (
    "status", "task_type", "resources", "assignees", "actions", "queries",
    "dependencies", "estimated_days", "estimated_hours", "task_name",
    "parent_task_uid", "is_milestone", "start_time", "end_time",
    "allocated_unit", "unit", "reference_id", "reference_type", "notes",
    "reason", "scheduled_type",
)


def _shape_task(raw: dict) -> dict:
    """Shape one already-resolved task_pivot row (see _SILVER_QUERY's WITH
    clause) into a single task record.

    All task_attribute-vs-silver.task resolution happens in SQL now, in the
    task_pivot CTE — by explicit decision, ADO PR 7368 V54-dropped columns
    are NEVER read from silver.task's own frozen value here, only from
    task_attribute (NULL until that table exists/has data for a given
    task). completed_unit/time_spent/reason/scheduled_type/tools/
    additional_data/schema_version/updated_at/version/version_start_date/
    version_end_date have no confirmed, tenant-portable task_attribute
    mapping and are therefore always NULL in task_pivot — not a bug here, a
    decision made upstream (see task_pivot's own comment for the full
    source-by-source accounting).

    Grouped into schedule / assignment / workDetails / reference / audit /
    taskPayload sub-objects (rather than 30+ flat keys) so a consumer can
    read only the slice it needs.
    """
    # task_payload is always present as a dict (the SQL join emits an
    # all-NULL row when silver.task_payload doesn't exist yet in this env —
    # see __TP_JOIN__), so "truthy dict" isn't a valid presence check;
    # check whether any value is actually populated instead.
    tp: dict = raw.get("task_payload") or {}
    has_task_payload = any(v not in (None, "") for v in tp.values())

    allocated_unit_parsed = _maybe_json(raw.get("allocated_unit"))
    # unit is its OWN task_attribute key (e.g. 'EA'), NOT embedded in
    # allocated_unit's JSON as originally assumed from the Java javadoc —
    # verified live on tn24047750 (allocated_unit has zero active rows there
    # while unit has 22 on its own).
    unit_val = raw.get("unit")

    reference_id = raw.get("reference_id")
    reference_type = raw.get("reference_type")
    reference = ({"referenceId": reference_id, "referenceType": reference_type}
                 if (reference_id or reference_type) else None)

    return {
        "taskId":          str(raw["id"]),
        "taskUid":         raw.get("task_uid"),
        "taskName":        raw.get("task_name"),
        "taskType":        raw.get("task_type"),
        "phase":           raw.get("phase"),
        "order":           raw.get("task_order"),
        "isMilestone":     bool(raw.get("is_milestone")),
        "status":          raw.get("status"),
        "invalid":         bool(raw.get("invalid")),
        "parentTaskUid":   raw.get("parent_task_uid"),
        "parentProjectId": (str(raw["parent_project_id"])
                             if raw.get("parent_project_id") is not None else None),
        "schedule": {
            "startTime":      parse_ts(raw.get("start_time")),
            "endTime":        parse_ts(raw.get("end_time")),
            "estimatedDays":  raw.get("estimated_days"),
            "estimatedHours": raw.get("estimated_hours"),
            "timeSpent":      raw.get("time_spent"),
            "scheduledType":  raw.get("scheduled_type"),
        },
        "assignment": {
            "assignees": _maybe_json(raw.get("assignees")) or [],
        },
        "workDetails": {
            "actions":        raw.get("actions"),
            "queries":        raw.get("queries"),
            "reason":         raw.get("reason"),
            "resources":      _maybe_json(raw.get("resources")),
            "completedUnit":  _maybe_json(raw.get("completed_unit")),
            "allocatedUnit":  allocated_unit_parsed,
            "unit":           unit_val,
            "notes":          _maybe_json(raw.get("notes")),
            "tools":          _maybe_json(raw.get("tools")),
            "dependencies":   _maybe_json(raw.get("dependencies")),
            "additionalData": _maybe_json(raw.get("additional_data")),
        },
        "reference": reference,
        "audit": {
            "createdAt":        parse_ts(raw.get("created_at")),
            "createdBy":        raw.get("created_by"),
            "updatedAt":        parse_ts(raw.get("updated_at")),
            "version":          raw.get("version"),
            "versionStartDate": parse_ts(raw.get("version_start_date")),
            "versionEndDate":   parse_ts(raw.get("version_end_date")),
            "schemaVersion":    raw.get("schema_version"),
        },
        "taskPayload": ({
            "payload":          _maybe_json(tp.get("payload")),
            "schedulerReady":   tp.get("scheduler_ready"),
            "aiReady":          tp.get("ai_ready"),
            "payloadVersion":   tp.get("payload_version"),
            "payloadUpdatedAt": parse_ts(tp.get("payload_updated_at")),
        } if has_task_payload else None),
        "hasEavAttributes": any(raw.get(k) not in (None, "") for k in _TAE_SOURCED_FIELDS),
    }


def _with_parsed_end_time(item: dict) -> dict:
    """Return a copy of a task-list item dict with its ``endTime`` key
    (a Postgres ``::text``-cast timestamp surfaced via JSONB_BUILD_OBJECT)
    replaced by a native ``datetime`` via ``parse_ts``."""
    if not item:
        return item
    out = dict(item)
    if "endTime" in out:
        out["endTime"] = parse_ts(out.get("endTime"))
    return out


def shape_doc(row: dict, gold_ts: datetime) -> dict:
    most_delayed = None
    if row.get("most_delayed_task_id"):
        most_delayed = {
            "taskId":   row["most_delayed_task_id"],
            "taskName": row.get("most_delayed_task_name"),
            "daysLate": int(row["most_delayed_days"]) if row.get("most_delayed_days") else 0,
        }
    total    = int(row.get("total_task_count") or 0)
    done     = int(row.get("done_task_count") or 0)
    comp_pct = round(done / total * 100, 2) if total > 0 else None

    # Milestone summary — built from silver.task.is_milestone. None (not a stub
    # object) when the project has no tasks flagged as milestones, matching the
    # convention used by mostDelayedTask above.
    total_milestones = int(row.get("total_milestones") or 0)
    milestone_summary = None
    critical_path_at_risk = None
    if total_milestones > 0:
        overdue_milestones = int(row.get("overdue_milestones") or 0)
        milestone_summary = {
            "totalMilestones":     total_milestones,
            "completedMilestones": int(row.get("completed_milestones") or 0),
            "overdueMilestones":   overdue_milestones,
            "nextMilestone":       _with_parsed_end_time(row.get("next_milestone")),
            "milestones":          [_with_parsed_end_time(m) for m in (row.get("milestones") or [])],
        }
        # Derived: any overdue, not-yet-done milestone puts the critical path at risk.
        critical_path_at_risk = overdue_milestones > 0

    watermarks = {
        "project":  _to_ts(row.get("project_silver_ts")),
        "task":     _to_ts(row.get("task_silver_ts")),
    }
    valid_wm = [ts for ts in watermarks.values() if ts is not None]
    lag_ms = int((gold_ts - min(valid_wm)).total_seconds() * 1000) if valid_wm else 0

    doc = {
        "tenantId":            row["tenantid"],
        "projectId":           str(row["project_id"]),
        "_grain":              "eden_task_intelligence",
        "_schemaVersion":      1,
        "_silverLsn":          int(row.get("silver_lsn") or 0),
        "isDeleted":                bool(row.get("is_deleted") or False),
        "deletedAt":                _to_ts(row.get("deleted_at")),
        "dataAsOf":            gold_ts,
        "totalTaskCount":      total,
        "doneTaskCount":       done,
        "overdueTaskCount":    int(row.get("overdue_task_count") or 0),
        "unassignedTaskCount": int(row.get("unassigned_task_count") or 0),
        "completionPct":       comp_pct,
        "overdueTasks":        [_with_parsed_end_time(t) for t in (row.get("overdue_tasks") or [])],
        "unassignedTasks":     [_with_parsed_end_time(t) for t in (row.get("unassigned_tasks") or [])],
        "mostDelayedTask":     most_delayed,
        "milestoneSummary":    milestone_summary,
        "completionTrend":     None,   # populated when weekly task tracking is available
        "criticalPathAtRisk":  critical_path_at_risk,
        # Full per-task detail (every silver.task + matched task_attribute
        # overlay, consolidated — see _shape_task()). Not capped: every active
        # task in the project (verified live max ~136/project).
        "tasks":               [_shape_task(t) for t in (row.get("all_tasks") or [])],
        "_silverWatermarks":   watermarks,
        "lafieldcraft":               lag_ms,
    }
    doc.update(lineage_fields(FEATURE_NAME, gold_ts, iso=False,
                              source_updated_at=watermarks.get("project"),
                              source_env=row.get("source_env")))
    return doc


@dataclass
class WriteStats:
    upserted: int = 0
    modified: int = 0
    failed:   int = 0
    batches:  int = 0


_cb: CircuitBreaker | None = None

def bulk_write_with_retry(coll: Collection, ops: list[UpdateOne],
                          cfg: LoaderConfig, stats: WriteStats) -> None:
    delay = cfg.retry_backoff_s
    for attempt in range(1, cfg.max_retries + 1):
        try:
            global _cb
            if _cb is None:
                _cb = CircuitBreaker(name=coll.full_name)
            res = _cb.call(lambda: coll.bulk_write(ops, ordered=False))
            stats.upserted += res.upserted_count or 0
            stats.modified += res.modified_count or 0
            stats.batches  += 1
            return
        except BulkWriteError as exc:
            errors = exc.details.get("writeErrors", []) if exc.details else []
            stats.failed += len(errors)
            stats.batches += 1
            return
        except TRANSIENT_ERRORS as exc:
            if attempt == cfg.max_retries:
                raise
            time.sleep(delay)
            delay *= 2
        except OperationFailure as exc:
            log.error("operation failure: %s", exc)
            raise


_shutdown = False


def _install_signal_handlers() -> None:
    def _handler(signum, _frame):
        global _shutdown
        _shutdown = True
    signal.signal(signal.SIGINT, _handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handler)


def run(cfg: LoaderConfig) -> int:
    log.info("loader start | db=%s.%s tenant=%s dry_run=%s",
             cfg.db_name, COLLECTION, cfg.tenantid, cfg.dry_run)
    last_ts = read_cursor(cfg.pg_dsn, _STEP_NAME, cfg.tenantid)
    if cfg.rebuild:
        log.info("rebuild mode | full re-aggregate from current Silver; cursor will NOT be advanced")
    elif not _has_new_silver_rows(cfg.pg_dsn, cfg.tenantid, last_ts):
        log.info("no new Silver rows since %s — skipping full re-aggregate", last_ts.isoformat())
        return 0
    gold_ts = datetime.now(timezone.utc)
    stats = WriteStats()
    client: MongoClient | None = None
    coll: Collection | None = None
    if not cfg.dry_run:
        client = MongoClient(cfg.mongo_uri, serverSelectionTimeoutMS=15_000,
                             connectTimeoutMS=15_000, socketTimeoutMS=60_000,
                             retryWrites=False, appname="eden-task-intelligence")
        coll = client[cfg.db_name][COLLECTION]

    pending: list[dict] = []
    seen = 0

    def flush() -> None:
        if not pending:
            return
        for d in pending:
            _contract_validator.validate(d, COLLECTION)
        ops = [UpdateOne({"tenantId": d["tenantId"], "projectId": d["projectId"], "sourceEnv": d.get("sourceEnv"), "$or": [{"_silverLsn": {"$exists": False}}, {"_silverLsn": {"$lte": d.get("_silverLsn", 0)}}]},
                         {"$set": d, "$setOnInsert": set_on_insert(gold_ts, iso=False)}, upsert=True) for d in pending]
        if ops and not cfg.dry_run and coll is not None:
            bulk_write_with_retry(coll, ops, cfg, stats)
        pending.clear()

    try:
        for row in iter_silver_rows(cfg.pg_dsn, cfg.tenantid, cfg.max_docs):
            if _shutdown:
                break
            seen += 1
            pending.append(shape_doc(row, gold_ts))
            if len(pending) >= cfg.batch_size:
                flush()
        flush()
    except TRANSIENT_ERRORS as exc:
        mark_error(cfg.pg_dsn, _STEP_NAME, cfg.tenantid, exc)
        log.error("aborting: %s", exc)
        return 3
    finally:
        if client:
            client.close()

    if cfg.dry_run:
        log.info("dry-run: cursor NOT advanced")
    elif cfg.rebuild:
        log.info("rebuild: cursor intentionally NOT advanced (incremental watermark preserved)")
    elif stats.failed > 0:
        mark_error(cfg.pg_dsn, _STEP_NAME, cfg.tenantid, f"{stats.failed} bulk write failures")
    else:
        max_ts = _get_max_silver_ts(cfg.pg_dsn, cfg.tenantid)
        if max_ts > last_ts:
            write_cursor(cfg.pg_dsn, _STEP_NAME, cfg.tenantid, max_ts)

    log.info("loader done | seen=%d upserted=%d failed=%d", seen, stats.upserted, stats.failed)
    return 0 if stats.failed == 0 else 3


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    _install_signal_handlers()
    try:
        cfg = LoaderConfig.from_args_and_env(argv)
    except RuntimeError as exc:
        log.error("config error: %s", exc)
        return 2
    return run(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
