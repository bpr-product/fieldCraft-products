"""Silver → Gold loader: project_health (Eden AI Assistant).

Reads silver.project, silver.task, silver.project_estimate,
silver.project_estimate_schedule, silver.purchase_order, silver.invoice,
silver.project_schedule, silver.roles, silver.user_role_map, silver.user_account
and produces one document per project containing:
  - completion_pct, task counts
  - estimated cost breakdown (latest estimate version)
  - estimate change history (collapsed per-version timeline + reason) and a summary
  - committed spend (approved POs + invoices)
  - schedule risk, slip days, schedule revision count
  - schedule change history (collapsed reschedule timeline + reason) and a summary
  - assignees: the tenant's effective 'admin'-role users, embedded identically
    on every project doc (field named assignees to match the legacy source
    system's project.assignees array — see fetch_tenant_admins() docstring for
    why this is a faithful port of the legacy appendAdmins()/fetchTenantAdmins()
    Java flow, not project-scoped RBAC — the Silver model has no project-scoped
    role)

Upsert key: (tenantId, projectId)

Exit codes:
  0  success
  2  configuration / connection error
  3  load failure after retries exhausted
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
from datetime import date, datetime, timezone
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
from gold.pipeline.cursor_store import read_cursor, write_cursor, mark_error, EPOCH  # noqa: E402
from gold.pipeline.lineage import (  # noqa: E402
    cleanup_stale_order_no_siblings, lineage_fields, parse_ts, set_on_insert,
)
from gold.pipeline.silver_schema import get_silver_columns, table_exists  # noqa: E402
from gold.src.flink.sinks.mongo_sink import build_lsn_guard  # noqa: E402

COLLECTION = "project_health"
FEATURE_NAME = "eden"
_STEP_NAME = "load_project_health"
_contract_validator = ContractValidator("eden_project_health")
TRANSIENT_ERRORS = (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError,
                    CircuitOpenError)
log = logging.getLogger("eden.project_health_loader")


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
        p = argparse.ArgumentParser(description="Silver → Gold loader: project_health")
        p.add_argument("--mongo-uri",     default=None)
        p.add_argument("--pg-dsn",        default=None)
        p.add_argument("--db",            dest="db_name",
                       default=os.environ.get("GOLD_EDEN_DB", "gold_eden"))
        p.add_argument("--tenant", required=not bool(os.environ.get("TENANT_ID")),
                       default=os.environ.get("TENANT_ID"))
        p.add_argument("--batch-size",    type=int,
                       default=int(os.environ.get("BATCH_SIZE", "200")))
        p.add_argument("--max-retries",   type=int,
                       default=int(os.environ.get("MAX_RETRIES", "5")))
        p.add_argument("--retry-backoff", type=float,
                       default=float(os.environ.get("RETRY_BACKOFF_S", "1.5")))
        p.add_argument("--dry-run",       action="store_true")
        p.add_argument("--max-docs",      type=int,
                       default=int(os.environ.get("MAX_DOCS", "0")), metavar="N")
        p.add_argument("--rebuild",       action="store_true",
                       help="Force full re-aggregate from current Silver (ignore the "
                            "newness check) and do NOT advance the cursor.")
        ns = p.parse_args(argv)
        return LoaderConfig(
            mongo_uri=ns.mongo_uri or get_mongo_uri(),
            pg_dsn=ns.pg_dsn or get_postgres_dsn(),
            db_name=ns.db_name,
            tenantid=ns.tenant,
            batch_size=ns.batch_size,
            max_retries=ns.max_retries,
            retry_backoff_s=ns.retry_backoff,
            dry_run=ns.dry_run,
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


_SILVER_QUERY = """
WITH task_agg AS (
    -- Was a per-row LEFT JOIN LATERAL correlated to p._s_tenantid/_s_env/id --
    -- executed once per project in the batch (per-tenant) call. Converted to
    -- a GROUP BY CTE computed once for the whole tenant instead of once per
    -- output row -- the nested tae LATERAL stays row-correlated to `at`
    -- (evaluated before the GROUP BY collapses rows), unchanged from before.
    -- The project-filter placeholder below narrows this CTE to one project
    -- in the real-time single-project call (see _scoped_query()) so this
    -- doesn't regress that call's cost by aggregating every project in the
    -- tenant when only one row is actually needed. NOTE: written in prose
    -- here deliberately without the literal token spelled out -- _scoped_
    -- query()'s str.replace() has no notion of "this is a comment", so
    -- spelling out the exact placeholder text in ANY comment inside this
    -- string gets it substituted too, splicing SQL into the middle of a
    -- comment line (confirmed live: this broke the task-attribute join
    -- placeholder's own explanatory comment below, the same way, before
    -- that comment was reworded too).
    SELECT
        at._s_tenantid AS _s_tenantid,
        at._s_env      AS _s_env,
        at.project_id  AS project_id,
        COUNT(*)                                                           AS total_tasks,
        COUNT(*) FILTER (WHERE LOWER(tae.status) = 'completed')            AS done_tasks,
        COUNT(*) FILTER (
            WHERE tae.scheduled_end < NOW()
              AND LOWER(tae.status) NOT IN ('completed','cancelled','skipped','delete')
        )                                                                  AS overdue_tasks,
        COUNT(*) FILTER (
            WHERE (tae.assignees IS NULL OR tae.assignees::text = '[]')
              AND LOWER(tae.status) NOT IN ('completed','cancelled','skipped','delete')
        )                                                                  AS unassigned_tasks,
        MAX(at._s_silver_ts)                                               AS task_silver_ts
    FROM silver.task at
    LEFT JOIN LATERAL (
        __TAE_JOIN__
    ) tae ON TRUE
    WHERE at._s_tenantid  = %(tenantid)s
      AND at._s_is_deleted = FALSE
      __PROJECT_FILTER__
      __INVALID_FILTER__
    GROUP BY at._s_tenantid, at._s_env, at.project_id
),
estimate_latest AS (
    -- Was ORDER BY version DESC NULLS LAST LIMIT 1 per project (top-1-per-group).
    -- DISTINCT ON is Postgres's standard idiom for exactly this pattern --
    -- same filter (_s_is_deleted/invalid), same ordering, same top-1 pick,
    -- computed once per (tenant, env, project) group instead of once per row.
    SELECT DISTINCT ON (_s_tenantid, _s_env, project_id)
        _s_tenantid, _s_env, project_id,
        total_cost, version, material_cost, worker_cost, tool_cost, overhead_cost, margin,
        _s_silver_ts AS estimate_silver_ts
    FROM silver.project_estimate
    WHERE _s_tenantid  = %(tenantid)s
      AND _s_is_deleted = FALSE
      AND invalid       = FALSE
      __PROJECT_FILTER__
    ORDER BY _s_tenantid, _s_env, project_id, version DESC NULLS LAST
),
po_agg AS (
    SELECT
        _s_tenantid, _s_env, project_id,
        COALESCE(SUM(total), 0) AS committed_pos,
        MAX(_s_silver_ts)       AS po_silver_ts
    FROM silver.purchase_order
    WHERE _s_tenantid  = %(tenantid)s
      AND status       IN ('APPROVED', 'ISSUED')
      AND _s_is_deleted = FALSE
      AND invalid       = FALSE
      __PROJECT_FILTER__
    GROUP BY _s_tenantid, _s_env, project_id
),
po_full_agg AS (
    SELECT
        apo._s_tenantid AS _s_tenantid,
        apo._s_env      AS _s_env,
        apo.project_id  AS project_id,
        JSONB_AGG(
            JSONB_BUILD_OBJECT(
                'id',               apo.id,
                'status',           apo.status,
                'vendorId',         apo.vendor_id,
                'documentId',       apo.document_id,
                'subTotal',         apo.sub_total,
                'total',            apo.total,
                'taxInfo',          apo.tax_info,
                'reason',           apo.reason,
                'comment',          apo.comment,
                'approvedBy',       apo.approved_by,
                'billingAddress',   apo.billing_address,
                'shippingAddress',  apo.shipping_address,
                'counterOfferDate', apo.counter_offer_date::text,
                'reminderStatus',   apo.reminder_status,
                'reminderCount',    apo.reminder_count,
                'reminderAt',       apo.reminder_at::text,
                'additionalInfo',   apo.additional_info,
                'invalid',          apo.invalid,
                'createdAt',        apo.created_at::text,
                'createdBy',        apo.created_by,
                'updatedAt',        apo.updated_at::text,
                'items', (
                    SELECT JSONB_AGG(
                        JSONB_BUILD_OBJECT(
                            'id',               poi.id,
                            'referenceId',      poi.reference_id,
                            'referenceType',    poi.reference_type,
                            'quantity',         poi.quantity,
                            'rate',             poi.rate,
                            'receivedQuantity', poi.received_quantity,
                            'deliveryStatus',   poi.delivery_status,
                            'status',           poi.status,
                            'createdAt',        poi.created_at::text,
                            'updatedAt',        poi.updated_at::text
                        )
                        ORDER BY poi.id ASC
                    )
                    FROM silver.purchase_order_items poi
                    WHERE poi._s_tenantid   = apo._s_tenantid
                      AND poi._s_env        = apo._s_env
                      AND poi.purchase_order = apo.id
                      AND poi._s_is_deleted = FALSE
                )
            )
            ORDER BY apo.id ASC
        )                                                               AS purchase_orders
    FROM silver.purchase_order apo
    WHERE apo._s_tenantid  = %(tenantid)s
      AND apo._s_is_deleted = FALSE
      AND apo.invalid       = FALSE
      __PROJECT_FILTER__
    GROUP BY apo._s_tenantid, apo._s_env, apo.project_id
),
invoice_agg AS (
    SELECT
        _s_tenantid, _s_env, project_id,
        COALESCE(SUM(invoiced_amount), 0)                                           AS committed_invoices,
        COALESCE(SUM(invoiced_amount) FILTER (WHERE status NOT IN ('PAID','VOID')), 0) AS outstanding_invoices_total,
        MAX(_s_silver_ts)                                                            AS invoice_silver_ts
    FROM silver.invoice
    WHERE _s_tenantid  = %(tenantid)s
      AND _s_is_deleted = FALSE
      AND invalid       = FALSE
      __PROJECT_FILTER__
    GROUP BY _s_tenantid, _s_env, project_id
),
schedule_latest AS (
    -- Was ORDER BY version DESC NULLS LAST LIMIT 1 per project, deliberately
    -- WITHOUT an invalid=FALSE filter (unlike estimate_latest/po_agg/inv_agg
    -- above) -- preserved exactly as-is, not "fixed" here: this rewrite is a
    -- performance change, not a correctness change to which row wins.
    SELECT DISTINCT ON (_s_tenantid, _s_env, project_id)
        _s_tenantid, _s_env, project_id,
        job_startdate, actual_job_startdate,
        job_enddate,   actual_job_enddate,
        version, _s_silver_ts AS schedule_silver_ts
    FROM silver.project_schedule
    WHERE _s_tenantid  = %(tenantid)s
      AND _s_is_deleted = FALSE
      __PROJECT_FILTER__
    ORDER BY _s_tenantid, _s_env, project_id, version DESC NULLS LAST
)
SELECT
    p._s_tenantid                                                               AS tenantid,
    p._s_env                                                                     AS source_env,
    p._s_is_deleted                                                              AS is_deleted,
    p._s_deleted_at                                                              AS deleted_at,
    p._s_lsn                                                                     AS silver_lsn,
    p._s_silver_ts                                                               AS p_silver_ts,
    p.id                                                        AS project_id,
    p.order_no,
    -- silver.project has no plain "name" column; project_uid is a UUID (kept
    -- as a fallback below), and p.customer is a JSON customer object
    -- (firstName/lastName/...), NOT a name string. projectName is derived
    -- from the parsed customer in shape_doc(); the full project record
    -- (customer, customer_asset, tags, geocode, etc.) is under "project".
    p.project_uid                                               AS project_uid,
    p.customer                                                   AS customer_raw,
    p.customer_asset                                             AS customer_asset_raw,
    p.status,
    p.trade,
    p.location,
    p.description,
    p.trade_type,
    p.project_type,
    p.tags                                                      AS tags_raw,
    p.zipcode,
    -- metro_code/jobsite_lat/jobsite_lng/geocode_status/scheduling_priority/
    -- last_solve_status commented out: none of these columns exist on silver.project
    p.assigned_to,
    p.job_done_by,
    p.team_members                                              AS team_members_raw,
    p.reference_project_id,
    p.parent_project_id,
    p.tax_exemption,
    p.additional_info                                            AS project_additional_info_raw,
    -- Status-transition audit trail (see historical_data_strategy.md §8).
    -- This is the platform's own recorded state-change log, NOT a synthesized
    -- Change Order entity — there is no dedicated Change Order table, so no
    -- CO fidelity is claimed here (that needs backend confirmation of the
    -- real CO path first).
    p.state_change_history                                       AS state_change_history_raw,
    p.created_at                                                  AS project_created_at,
    p.created_by                                                  AS project_created_by,
    p.updated_at                                                  AS project_updated_at,
    p.version                                                     AS project_version,

    -- Task counts
    COALESCE(t.total_tasks, 0)                                  AS total_task_count,
    COALESCE(t.done_tasks, 0)                                   AS done_task_count,
    COALESCE(t.overdue_tasks, 0)                                AS overdue_task_count,
    COALESCE(t.unassigned_tasks, 0)                             AS unassigned_task_count,
    ROUND(
        COALESCE(t.done_tasks, 0) * 100.0
        / NULLIF(COALESCE(t.total_tasks, 0), 0), 2
    )                                                           AS completion_pct,

    -- Latest estimate
    e.total_cost    AS estimated_cost,
    e.version       AS estimate_version,
    e.material_cost,
    e.worker_cost,
    e.tool_cost,
    e.overhead_cost,
    e.margin,

    -- Committed spend: approved POs + invoices
    COALESCE(po.committed_pos, 0)                               AS committed_pos,
    COALESCE(inv.committed_invoices, 0)                         AS committed_invoices,
    COALESCE(po.committed_pos, 0)
        + COALESCE(inv.committed_invoices, 0)                   AS committed_spend,
    COALESCE(inv.outstanding_invoices_total, 0)                 AS outstanding_invoices_total,

    -- Schedule
    s.job_startdate,
    s.actual_job_startdate,
    s.job_enddate,
    s.actual_job_enddate,
    s.version                                                   AS schedule_revised_count,
    CASE
        WHEN s.actual_job_startdate IS NOT NULL
             AND s.actual_job_startdate > s.job_startdate
        THEN EXTRACT(DAY FROM s.actual_job_startdate - s.job_startdate)::int
        ELSE 0
    END                                                         AS slip_days,

    -- Per-table Silver freshness watermarks
    t.task_silver_ts,
    e.estimate_silver_ts,
    po.po_silver_ts,
    inv.invoice_silver_ts,
    s.schedule_silver_ts,

    -- Full purchase order detail (not just the committed-spend sum above) —
    -- every column + line items. Not covered by any other Gold collection
    -- (invoice_summary covers invoices/payments/expenses; POs are only ever
    -- summed here today). Not capped: verified live max 1 PO per project.
    pofull.purchase_orders                                      AS all_purchase_orders

FROM silver.project p

-- silver.task no longer carries status/end_time/assignees as plain columns
-- (ADO PR 7368 / V54 EAV migration moved them to task_attribute, resolved by
-- attribute_key via the attributes catalog — same schema change already
-- handled in load_task_intelligence.py's EAV pivot join). Referencing the
-- bare column names here silently resolved to the OUTER p.status (silver.
-- project DOES have a "status" column) instead of raising "column does not
-- exist", and Postgres rejected the resulting aggregate-over-outer-column as
-- "aggregate functions are not allowed in FROM clause of their own query
-- level". end_time is a confirmed semantic rename to the task_attribute key
-- scheduled_end (not a 1:1 carry-over). status is stored lowercase in
-- practice ('scheduled','active','completed','cancelled','skipped','delete').
-- task_agg's task-attribute join placeholder (see its definition above)
-- resolves to the real task_attribute/attributes pivot once those tables
-- exist in a given env's Silver, or an all-NULL inert join until then
-- (done/overdue/unassigned degrade to 0; total_tasks is unaffected either
-- way).
LEFT JOIN task_agg t
       ON t._s_tenantid = p._s_tenantid AND t._s_env = p._s_env AND t.project_id = p.id

LEFT JOIN estimate_latest e
       ON e._s_tenantid = p._s_tenantid AND e._s_env = p._s_env AND e.project_id = p.id

LEFT JOIN po_agg po
       ON po._s_tenantid = p._s_tenantid AND po._s_env = p._s_env AND po.project_id = p.id

LEFT JOIN po_full_agg pofull
       ON pofull._s_tenantid = p._s_tenantid AND pofull._s_env = p._s_env AND pofull.project_id = p.id

LEFT JOIN invoice_agg inv
       ON inv._s_tenantid = p._s_tenantid AND inv._s_env = p._s_env AND inv.project_id = p.id

LEFT JOIN schedule_latest s
       ON s._s_tenantid = p._s_tenantid AND s._s_env = p._s_env AND s.project_id = p.id

WHERE p._s_tenantid  = %(tenantid)s
  AND p.invalid       = FALSE
ORDER BY p.id
"""

# EAV pivot for task_attribute, resolving attribute_id by attribute_key via
# the attributes catalog (never hardcoded numeric ids). Only the three keys
# the task-count aggregates above need — the full field set (task_name,
# resources, dependencies, etc.) is load_task_intelligence.py's job, not
# duplicated here. Emitted only when both silver.task_attribute and
# silver.attributes exist (see _scoped_query).
_TAE_JOIN_REAL = """
        SELECT
            MAX(ta.attr_value_text)     FILTER (WHERE attrs.attribute_key = 'status')         AS status,
            MAX(ta.attr_value_jsonb)    FILTER (WHERE attrs.attribute_key = 'assignees')      AS assignees,
            MAX(ta.attr_value_datetime) FILTER (WHERE attrs.attribute_key = 'scheduled_end')  AS scheduled_end
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
"""

_TAE_JOIN_INERT = """
        SELECT
            NULL::text AS status, NULL::jsonb AS assignees, NULL::timestamptz AS scheduled_end
"""


# ---------------------------------------------------------------------------
# Estimate history (WI 21303) — per-version change timeline + reason.
#
# Reads ALL versions of an estimate, including superseded rows. In Silver the
# source's SCD mechanism marks superseded versions with invalid=TRUE and keeps
# the single current version as invalid=FALSE — so history MUST read both and
# only filter the hard soft-delete (_s_is_deleted). project_estimate_schedule
# is a 1:many estimate→schedule link, aggregated to distinct schedule_ids per
# version to avoid row fan-out.
#
# Verified-live constraints baked in here:
#   * Order/collapse on `version` + `created_at`, never `_s_lsn` — a backfilled
#     project shares one _s_lsn across every version.
#   * `created_at` is valid-time (the real timeline); `_s_silver_ts` is load-time
#     only and surfaced as `recordedAt`.
#   * Costs churn sub-cent between saves → collapse no-op versions (rounded cents)
#     so Eden never reports "revised N times" when nothing material changed.
# ---------------------------------------------------------------------------
_ESTIMATE_HISTORY_QUERY = """
SELECT
    pe.project_id                                          AS project_id,
    pe._s_env                                              AS source_env,
    CAST(pe.version AS INT)                                AS version,
    pe.total_cost,
    pe.material_cost,
    pe.worker_cost,
    pe.tool_cost,
    pe.overhead_cost,
    pe.margin,
    pe.reason,
    pe.created_at,
    pe._s_silver_ts                                        AS recorded_at,
    pe.invalid                                             AS is_superseded,
    COALESCE(sl.schedule_ids, ARRAY[]::bigint[])           AS schedule_ids,
    bom_agg.bom_total_cost                                 AS bom_total_cost,
    bom_agg.attributed_line                                AS bom_attributed_line,
    price_hist_agg.materials_price_history                 AS materials_price_history
FROM silver.project_estimate pe
LEFT JOIN LATERAL (
    SELECT array_agg(DISTINCT pes.schedule_id ORDER BY pes.schedule_id) AS schedule_ids
    FROM silver.project_estimate_schedule pes
    WHERE pes._s_tenantid   = pe._s_tenantid
      AND pes._s_env        = pe._s_env
      AND pes.estimate_id   = pe.id
      AND pes.schedule_id  IS NOT NULL
      AND pes._s_is_deleted = FALSE
      AND pes.invalid       = FALSE
) sl ON TRUE
-- Confidence evidence (constraint #4/#11 — see EDEN_CustomerSupport_Reasoning_
-- Constraints.md Q1): deterministic BOM reconciliation for THIS version, plus
-- whether at least one BOM line can be attributed to an actual vendor price
-- change (silver.material_cost via silver.vendor_material_map, resolved at
-- this estimate's created_at vs the immediately preceding price). This is the
-- ONLY input to `confidence` computed in _history_entry() below — never an
-- LLM self-assessment.
LEFT JOIN LATERAL (
    SELECT
        -- Reconciliation is recomputed as quantity x price-at-pe.created_at
        -- (via mc_at, the SAME time-consistent lookup used for attribution)
        -- rather than trusting b.total_cost, which is frozen at whatever
        -- price was last WRITTEN to the bom row — for a line whose price was
        -- bumped multiple times without a matching bom.version bump (see the
        -- version-resolution comment below), b.total_cost reflects only the
        -- CURRENT price, not the price at this specific estimate version's
        -- created_at, and reconciliation would never match. Falls back to
        -- b.total_cost when no material_cost row is resolvable (e.g. no
        -- vendor_material_map_id on this line) so untracked-price lines still
        -- contribute their known cost. Verified live: tenant tn22473627,
        -- project order_no 597339.
        SUM(COALESCE(b.quantity * mc_at.cost, b.total_cost)) AS bom_total_cost,
        -- The specific attributed line (largest absolute price delta) — the
        -- "receipt" a human/test can inspect to verify `confidence` wasn't
        -- asserted arbitrarily. NULL when no line has a resolvable price
        -- change (both prices known and different). Field names match the
        -- gold_eden.project_health.estimateHistory[].evidence contract
        -- (EDEN Customer Support Master Constraints Reference, Part B).
        (ARRAY_AGG(
            JSONB_BUILD_OBJECT(
                'bomLineId',     b.id,
                'materialName',  b.material_name,
                'priorUnitCost', mc_prev.cost,
                'newUnitCost',   mc_at.cost,
                'quantity',      b.quantity,
                'effectiveDate', mc_at.effective_startdate
            ) ORDER BY ABS(mc_at.cost - mc_prev.cost) DESC NULLS LAST
        ) FILTER (WHERE mc_prev.cost IS NOT NULL
                    AND mc_at.cost IS NOT NULL
                    AND ROUND(mc_at.cost::numeric, 2) <> ROUND(mc_prev.cost::numeric, 2))
        )[1]                                                AS attributed_line
    FROM silver.bom b
    LEFT JOIN LATERAL (
        SELECT mc.cost, mc.effective_startdate
        FROM silver.material_cost mc
        WHERE mc.vendor_material_map_id = b.vendor_material_map_id
          AND mc._s_tenantid   = b._s_tenantid
          AND mc._s_env        = b._s_env
          AND mc._s_is_deleted = FALSE
          AND mc.effective_startdate <= pe.created_at
          AND (mc.effective_enddate IS NULL OR mc.effective_enddate >= pe.created_at)
        -- Ordered by id DESC (insertion order) ALONE, not effective_startdate.
        -- Verified live (tenant tn22473627, vendor_material_map_id 71/72):
        -- a price bump writes the new price with a bounded effective_enddate
        -- alongside a same-instant re-assertion of the PRIOR price under a
        -- year-3026 "open" sentinel — and that stale re-assertion row's own
        -- effective_startdate can be a few microseconds LATER than the real
        -- new-price row's, so effective_startdate DESC (even with an id
        -- tie-break) picks the wrong row. Among every row whose window
        -- covers pe.created_at, the most-recently-INSERTED one is reliably
        -- the semantically-current price for that instant — ordering on id
        -- alone fixed a real case where startdate-first ordering did not.
        ORDER BY mc.id DESC
        LIMIT 1
    ) mc_at ON TRUE
    LEFT JOIN LATERAL (
        SELECT mc2.cost
        FROM silver.material_cost mc2
        WHERE mc2.vendor_material_map_id = b.vendor_material_map_id
          AND mc2._s_tenantid   = b._s_tenantid
          AND mc2._s_env        = b._s_env
          AND mc2._s_is_deleted = FALSE
          AND mc2.effective_enddate < pe.created_at
        ORDER BY mc2.id DESC
        LIMIT 1
    ) mc_prev ON TRUE
    WHERE b._s_tenantid   = pe._s_tenantid
      AND b._s_env        = pe._s_env
      AND b.project_id    = pe.project_id
      -- Resolve the EFFECTIVE bom version rather than requiring an exact
      -- match on pe.version. Verified live (tenant tn22473627, project
      -- order_no 597339): bom.version does not always increment in lockstep
      -- with project_estimate.version — a material's price can be bumped
      -- (old row invalidated, new row inserted) without bom.version ever
      -- moving, while project_estimate.version keeps advancing. An exact
      -- `b.version = pe.version` match then finds zero bom rows for most
      -- versions, silently breaking reconciliation. Falling back to "the
      -- greatest bom.version not exceeding pe.version" is a no-op for the
      -- well-behaved lockstep case (tn24047750/project 7890, where an exact
      -- match already exists at every version) and fixes the sparse case.
      AND b.version = (
          SELECT MAX(v.version)
          FROM silver.bom v
          WHERE v._s_tenantid  = pe._s_tenantid
            AND v._s_env       = pe._s_env
            AND v.project_id   = pe.project_id
            AND v.version     <= pe.version
            AND v._s_is_deleted = FALSE
      )
      AND b._s_is_deleted  = FALSE
      AND b.invalid        = FALSE
) bom_agg ON TRUE
-- Full, UNCAPPED price-version-history per material on this version's BOM —
-- deliberately NOT gated by bomReconciles/confidence. `evidence` above only
-- surfaces a single attributed line when the strict reconciliation math
-- passes; this shows what price history exists for every material on the
-- BOM regardless, so a project whose estimate/BOM numbers don't reconcile
-- (verified live: tenant tn22473627, project order_no 597339) still exposes
-- the real, known price history for reasoning/manual investigation.
LEFT JOIN LATERAL (
    SELECT JSONB_AGG(
        JSONB_BUILD_OBJECT(
            'materialName',        bm.material_name,
            'vendorMaterialMapId', bm.vendor_material_map_id,
            'quantity',            bm.quantity,
            'currentCost',         bm.current_cost,
            'history',             bm.history
        )
    )                                                       AS materials_price_history
    FROM (
        SELECT DISTINCT b2.vendor_material_map_id, b2.material_name, b2.quantity,
            (
                -- "Current" is NOT simply MAX(effective_startdate) — verified
                -- live (tenant tn22473627, vendor_material_map_id 71) that two
                -- open-ended (year-3026 sentinel) rows can have startdate
                -- values that don't match their actual write/insertion order
                -- (row with the LATER startdate was NOT the most recently
                -- written). The genuinely current price is the most recently
                -- INSERTED row that is still open-ended — prefer sentinel
                -- (effective_enddate far future) rows first, then break ties
                -- by id (insertion order), matching the same
                -- _OPEN_ENDED_SENTINEL_YEAR convention used in
                -- load_bom_stock_gap.py's collapse_price_history().
                SELECT mc_cur.cost
                FROM silver.material_cost mc_cur
                WHERE mc_cur.vendor_material_map_id = b2.vendor_material_map_id
                  AND mc_cur._s_tenantid   = pe._s_tenantid
                  AND mc_cur._s_env        = pe._s_env
                  AND mc_cur._s_is_deleted = FALSE
                ORDER BY (mc_cur.effective_enddate >= '3000-01-01'::timestamptz) DESC,
                         mc_cur.id DESC
                LIMIT 1
            )                                               AS current_cost,
            (
                SELECT COALESCE(JSONB_AGG(
                    JSONB_BUILD_OBJECT(
                        'cost',         mc_h.cost,
                        'costType',     mc_h.cost_type,
                        'effectiveFrom', mc_h.effective_startdate,
                        'effectiveTo',  mc_h.effective_enddate
                    ) ORDER BY mc_h.effective_startdate ASC, mc_h.id ASC
                ), '[]'::jsonb)
                FROM silver.material_cost mc_h
                WHERE mc_h.vendor_material_map_id = b2.vendor_material_map_id
                  AND mc_h._s_tenantid   = pe._s_tenantid
                  AND mc_h._s_env        = pe._s_env
                  AND mc_h._s_is_deleted = FALSE
            )                                               AS history
        FROM silver.bom b2
        WHERE b2._s_tenantid    = pe._s_tenantid
          AND b2._s_env         = pe._s_env
          AND b2.project_id     = pe.project_id
          AND b2.version = (
              SELECT MAX(v2.version)
              FROM silver.bom v2
              WHERE v2._s_tenantid  = pe._s_tenantid
                AND v2._s_env       = pe._s_env
                AND v2.project_id   = pe.project_id
                AND v2.version     <= pe.version
                AND v2._s_is_deleted = FALSE
          )
          AND b2._s_is_deleted   = FALSE
          AND b2.invalid         = FALSE
          AND b2.vendor_material_map_id IS NOT NULL
    ) bm
) price_hist_agg ON TRUE
WHERE pe._s_tenantid   = %(tenantid)s
  AND pe._s_is_deleted  = FALSE
{project_filter}
-- Secondary sort on _s_silver_ts makes duplicate-`version` groups
-- deterministic and contiguous for _dedupe_same_version_rows() below (real
-- data-quality issue: a version number can repeat across two genuine
-- duplicate rows — see that function's docstring).
ORDER BY pe.project_id, CAST(pe.version AS INT), pe._s_silver_ts
"""

# Reconciliation tolerance (currency-cents drift, not a real discrepancy) —
# same value used by gold/src/eden/load_estimate_price_evidence.py.
_RECONCILIATION_TOLERANCE = 0.01

# Cost components compared (rounded to cents) when deciding whether a version is a
# *meaningful* change vs no-op churn. A change in any one of these — or in `reason` —
# makes the version worth emitting.
_HISTORY_COST_FIELDS = (
    "total_cost", "material_cost", "worker_cost",
    "tool_cost", "overhead_cost", "margin",
)
_MAX_HISTORY_ENTRIES = 25


def fetch_estimate_history(
    pg_dsn: str, tenantid: str, project_id: str | None = None
) -> dict[tuple, list[dict]]:
    """Fetch every retained estimate version per project, grouped by (source_env, project_id).

    Keyed by a (source_env, project_id) tuple — not bare project_id — so a
    project_id that happens to collide across two Silver `_s_env` sources under
    the same tenantid never has its estimate history merged together (see
    ``gold/pipeline/lineage.py``'s ``sourceEnv`` docs for why tenantid alone
    isn't a safe grain). Scoped to one project when project_id is given (the
    streaming path). Returned rows are ordered by version ascending within each
    project.
    """
    project_filter = "  AND pe.project_id = %(project_id)s" if project_id is not None else ""
    sql = _ESTIMATE_HISTORY_QUERY.format(project_filter=project_filter)
    params: dict = {"tenantid": tenantid}
    if project_id is not None:
        params["project_id"] = project_id
    by_project: dict[tuple, list[dict]] = {}
    conn = psycopg2.connect(pg_dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            for row in cur:
                key = (row.get("source_env"), str(row["project_id"]))
                by_project.setdefault(key, []).append(dict(row))
    finally:
        conn.close()
    for key, rows in by_project.items():
        by_project[key] = _dedupe_same_version_rows(rows, _is_meaningful)
    return by_project


def _dedupe_same_version_rows(rows: list[dict], is_meaningful_fn) -> list[dict]:
    """Collapse rows that share the identical `version` number into one row.

    Real, verified data-quality issue (2026-08-01): a project can have two
    silver.project_estimate rows both carrying the same `version` integer —
    a genuine duplicate write, not a version increment. Naively passing both
    to the collapse logic makes the later-written duplicate's delta compute
    against itself (all-zero), silently discarding the real cost change the
    earlier row for that same version correctly recorded (real example: Gold
    project 7890, two `estimateHistory` entries both tagged version 6).

    Fix applied here, at the source, rather than worked around by the reading
    agent: when a version number repeats, keep whichever row actually differs
    from the last already-kept distinct version (the meaningful write) —
    never whichever was physically written last. Falls back to the last row
    in the group when every candidate is equally (non-)meaningful, so
    behavior stays deterministic. Requires rows ordered by version ascending,
    with a stable secondary order (recorded_at/`_s_silver_ts`) so duplicate
    groups are contiguous — see the query's ORDER BY.
    """
    if not rows:
        return rows
    deduped: list[dict] = []
    i = 0
    while i < len(rows):
        version = rows[i].get("version")
        group = [rows[i]]
        j = i + 1
        while j < len(rows) and rows[j].get("version") == version:
            group.append(rows[j])
            j += 1
        if len(group) == 1:
            deduped.append(group[0])
        else:
            prev = deduped[-1] if deduped else None
            candidates = (
                [r for r in group if prev is None or is_meaningful_fn(r, prev)]
                or group
            )
            deduped.append(candidates[-1])
        i = j
    return deduped


def _cents(v) -> float | None:
    f = _float(v)
    return round(f, 2) if f is not None else None


def _delta(curr, prev) -> float | None:
    c, p = _float(curr), _float(prev)
    if c is None or p is None:
        return None
    return round(c - p, 2) + 0.0  # + 0.0 normalises -0.0 → 0.0 for clean output


def _iso(v) -> "datetime | None":
    """Despite the name (kept to minimize call-site churn), returns a native
    datetime, not a string — every Gold date field must be a native BSON date."""
    return _to_ts(v)


def _history_entry(row: dict, prev: dict | None, is_current: bool) -> dict:
    snapshot = {
        "totalCost":    _float(row.get("total_cost")),
        "materialCost": _float(row.get("material_cost")),
        "workerCost":   _float(row.get("worker_cost")),
        "toolCost":     _float(row.get("tool_cost")),
        "overheadCost": _float(row.get("overhead_cost")),
        "margin":       _float(row.get("margin")),
    }
    if prev is None:
        delta = None
    else:
        delta = {
            "totalCost":    _delta(row.get("total_cost"),    prev.get("total_cost")),
            "materialCost": _delta(row.get("material_cost"), prev.get("material_cost")),
            "workerCost":   _delta(row.get("worker_cost"),   prev.get("worker_cost")),
            "toolCost":     _delta(row.get("tool_cost"),     prev.get("tool_cost")),
            "overheadCost": _delta(row.get("overhead_cost"), prev.get("overhead_cost")),
            "margin":       _delta(row.get("margin"),        prev.get("margin")),
        }
    schedule_ids = [str(s) for s in (row.get("schedule_ids") or [])][:_MAX_HISTORY_ENTRIES]
    confidence, evidence = _estimate_confidence_and_evidence(row)
    return {
        "version":       _int(row.get("version")),
        "validFrom":     _iso(row.get("created_at")),
        "recordedAt":    _iso(row.get("recorded_at")),
        "reason":        row.get("reason"),
        "inferredReason": _inferred_reason(confidence, evidence),
        "snapshot":      snapshot,
        "delta":         delta,
        "scheduleIds":   schedule_ids,
        "isCurrent":     is_current,
        "confidence":    confidence,
        "evidence":      evidence,
    }


def _inferred_reason(confidence: str, evidence: dict) -> str | None:
    """ETL-computed cause label — deliberately a SEPARATE field from the
    native `reason` column, never written into it. `reason` is a faithful
    passthrough of whatever the source app recorded (often null — see
    EDEN Customer Support Master Constraints Reference: reason is only
    populated when a human types one, not for system-triggered re-estimates
    like a cascading price change); `inferredReason` is the ETL's own
    deterministic verdict, kept distinct so a consumer never mistakes an
    inferred label for something the source app or a user actually recorded.
    """
    if confidence == "high":
        material = evidence.get("materialName")
        return f"Material price change: {material}" if material else "Material price change"
    if confidence == "low":
        return "Cost change (no specific material attributable)"
    return None


def _estimate_confidence_and_evidence(row: dict) -> tuple[str, dict]:
    """Deterministic evidence-support level for this estimate version — never
    an LLM self-assessment. Computed once here, at ETL write time, by joining
    the real Postgres tables (silver.bom, silver.material_cost); Eden only
    reads and renders this pre-computed verdict, choosing wording based on it
    (a "low"/"none" entry must never be rendered with "high"-confidence
    assertive wording).

    * "high" — SUM(silver.bom.total_cost) for this (project, version) exactly
      reconciles against material_cost AND at least one BOM line is
      attributable to an actual vendor price change (constraint #4/#11) —
      the attributed line is the `evidence` receipt.
    * "low"  — the BOM sum reconciles but no specific line/material price
      change could be attributed (partial evidence — no receipt).
    * "none" — the BOM sum does not reconcile (or no BOM data exists at all
      for this version) — whatever caused the change isn't visible in the
      BOM data, so no cause should be asserted (no receipt).

    Returns (confidence, evidence) — evidence is the "receipt" a human or
    test can inspect to verify the confidence level wasn't asserted
    arbitrarily; always present (possibly with null members) so downstream
    consumers never have to special-case a missing key.
    """
    bom_total = _float(row.get("bom_total_cost"))
    material_cost = _float(row.get("material_cost"))
    reconciled = (
        bom_total is not None and material_cost is not None
        and abs(round(bom_total, 2) - round(material_cost, 2)) <= _RECONCILIATION_TOLERANCE
    )
    attributed_line_raw = row.get("bom_attributed_line")
    attributed_line_raw = (_maybe_json(attributed_line_raw)
                            if isinstance(attributed_line_raw, str) else attributed_line_raw)
    attributed_line = attributed_line_raw if reconciled else None

    # Field names match gold_eden.project_health.estimateHistory[].evidence
    # exactly (EDEN Customer Support Master Constraints Reference, Part B) —
    # this is what source/agents/insight_agent.py (a separate repo) actually
    # reads. `triggeringReceiptId` is always null: silver.receipts.project_id
    # is populated on only 0/3 rows in the tenant checked, and the
    # receipts→stock_item.receipt_id→stock_allocation FK chain, while
    # structurally complete, is data-broken by a missing row — no reliable
    # join exists yet to resolve this field. Documented gap, not a bug.
    evidence = {
        "bomLineId":     (str(attributed_line["bomLineId"])
                           if attributed_line and attributed_line.get("bomLineId") is not None else None),
        "materialName":  attributed_line.get("materialName") if attributed_line else None,
        "priorUnitCost": _float(attributed_line.get("priorUnitCost")) if attributed_line else None,
        "newUnitCost":   _float(attributed_line.get("newUnitCost")) if attributed_line else None,
        "quantity":      _float(attributed_line.get("quantity")) if attributed_line else None,
        "effectiveDate": parse_ts(attributed_line.get("effectiveDate")) if attributed_line else None,
        "triggeringReceiptId": None,
        "bomReconciles": reconciled,
        # A weaker, UNPROVEN signal — deliberately separate from confidence/
        # bomLineId/etc above. "A material's price changed near this
        # version's created_at" (the same window-based mc_at/mc_prev lookup,
        # unconditional on bomReconciles) vs. "this price change fully
        # explains the dollar delta" (the strict, gated fields above). Never
        # conflate the two: priceChangeDetected can be true while confidence
        # stays "none" — verified live, tenant tn22473627 project order_no
        # 597339, where the estimate/BOM never reconciles but a real,
        # traceable price change still occurred in the same window.
        "priceChangeDetected": bool(attributed_line_raw),
        "priceChangeReason":   _price_change_reason(attributed_line_raw),
        # Full, UNCAPPED price-version history per material on this version's
        # BOM — deliberately NOT gated by `reconciled`/confidence. Verified
        # live (tenant tn22473627, project order_no 597339) that a project's
        # estimate/BOM numbers can fail to reconcile even when real, known
        # price history exists for its materials — this surfaces that known
        # history for manual investigation regardless of whether the strict
        # reconciliation math passed.
        "priceHistory":  _shape_materials_price_history(row.get("materials_price_history")),
    }

    if not reconciled:
        return "none", evidence
    return ("high" if attributed_line else "low"), evidence


def _price_change_reason(attributed_line_raw: dict | None) -> str | None:
    """Human-readable label for the loose priceChangeDetected signal — names
    the material and, when both prices are known, the old/new cost and the
    timestamp the new price took effect. Never asserts causation of the
    dollar delta (that's confidence/inferredReason's job); this only reports
    that a price change exists in the window, regardless of reconciliation.
    """
    if not attributed_line_raw:
        return None
    name = attributed_line_raw.get("materialName")
    if not name:
        return None
    prior = _float(attributed_line_raw.get("priorUnitCost"))
    new = _float(attributed_line_raw.get("newUnitCost"))
    eff = parse_ts(attributed_line_raw.get("effectiveDate"))
    if prior is None or new is None:
        return f"Material price change: {name}"
    eff_str = eff.isoformat() if eff else "unknown time"
    return f"Material price change: {name} (${prior:,.2f} -> ${new:,.2f}, {eff_str})"


def _shape_materials_price_history(raw) -> list[dict]:
    materials = _maybe_json(raw) if isinstance(raw, str) else raw
    if not isinstance(materials, list):
        return []
    shaped = []
    for m in materials:
        if not isinstance(m, dict):
            continue
        history_raw = m.get("history")
        history_raw = _maybe_json(history_raw) if isinstance(history_raw, str) else history_raw
        history = [
            {
                "cost":         _float(h.get("cost")),
                "costType":     h.get("costType"),
                "effectiveFrom": parse_ts(h.get("effectiveFrom")),
                "effectiveTo":  parse_ts(h.get("effectiveTo")),
            }
            for h in (history_raw or []) if isinstance(h, dict)
        ]
        shaped.append({
            "materialName":        m.get("materialName"),
            "vendorMaterialMapId": (str(m["vendorMaterialMapId"])
                                     if m.get("vendorMaterialMapId") is not None else None),
            "quantity":            _float(m.get("quantity")),
            "currentCost":         _float(m.get("currentCost")),
            "history":             history,
        })
    return shaped


def _is_meaningful(row: dict, prev: dict) -> bool:
    """A version is meaningful vs the previous *emitted* one when any tracked cost
    component changed (compared at cent precision) or the reason text changed."""
    for f in _HISTORY_COST_FIELDS:
        if _cents(row.get(f)) != _cents(prev.get(f)):
            return True
    return (row.get("reason") or None) != (prev.get("reason") or None)


def collapse_estimate_history(rows: list[dict]) -> tuple[list[dict], dict | None]:
    """Collapse raw version rows into a meaningful-change timeline + a summary.

    Pure function (no I/O) — the core tested unit. `rows` must be ordered by
    version ascending. Returns ``(history, summary)``; ``([], None)`` when empty.

    Collapse rule: always keep the first (baseline) and the last (current)
    version; in between, keep a version only when it is a meaningful change vs the
    last kept version (cost delta at cent precision, or a reason change). The
    kept list is capped to the most recent ``_MAX_HISTORY_ENTRIES`` while the
    baseline is always retained so deltas-vs-baseline stay computable.
    """
    if not rows:
        return [], None

    kept: list[dict] = [rows[0]]
    meaningful_count = 0
    for row in rows[1:]:
        if _is_meaningful(row, kept[-1]):
            kept.append(row)
            meaningful_count += 1
    # Ensure the true current version is present even if it was a no-op vs the
    # prior kept version (an agent still needs the live row flagged isCurrent).
    # This forced entry is NOT a meaningful revision — it must not inflate the count.
    if kept[-1] is not rows[-1]:
        kept.append(rows[-1])

    baseline, current = rows[0], rows[-1]
    current_id = id(current)

    history: list[dict] = []
    prev: dict | None = None
    for row in kept:
        history.append(_history_entry(row, prev, is_current=id(row) == current_id))
        prev = row

    # Cap embedded length for Cosmos point-read economics, but never drop the
    # baseline (index 0) — keep it plus the most recent (N-1) entries.
    if len(history) > _MAX_HISTORY_ENTRIES:
        history = [history[0]] + history[-(_MAX_HISTORY_ENTRIES - 1):]

    summary = {
        "currentVersion":         _int(current.get("version")),
        "baselineVersion":        _int(baseline.get("version")),
        "rawVersionCount":        len(rows),
        "meaningfulRevisionCount": meaningful_count,
        "totalCostDelta":         _delta(current.get("total_cost"), baseline.get("total_cost")),
        "marginDelta":            _delta(current.get("margin"),     baseline.get("margin")),
        "firstEstimatedAt":       _iso(baseline.get("created_at")),
        "lastChangedAt":          _iso(current.get("created_at")),
        "lastReason":             current.get("reason"),
    }
    return history, summary


# ---------------------------------------------------------------------------
# Schedule history (WI 22463) — per-version reschedule timeline + reason.
#
# Same SCD shape as project_estimate: superseded versions carry invalid=TRUE and
# the single current version is invalid=FALSE — so read both, filter only the hard
# soft-delete. project_estimate_schedule provides the reverse link
# (schedule_id → estimate_id, verified 1:1 against project_schedule.id) so each
# revision can name the estimate(s) it was planned against.
#
# Verified-live constraints (tenant tn88351775, project 1032 = 595 raw versions):
#   * version is integer here, but CAST(version AS INT) kept for the mixed-type
#     case the strategy doc flagged for project_schedule.
#   * Heavy churn — collapse on any of the 4 job-date fields changing OR a reason
#     change; project 1032 v1 adds an end date (meaningful), v2/v3 identical (no-op).
#   * Order/collapse on version + created_at (valid time); _s_silver_ts is load-time
#     only → recordedAt.
#   * reason AND comment are native columns ('Task Delay Reschedule', etc.).
# ---------------------------------------------------------------------------
_SCHEDULE_HISTORY_QUERY = """
SELECT
    ps.project_id                                          AS project_id,
    ps._s_env                                              AS source_env,
    CAST(ps.version AS INT)                                AS version,
    ps.job_startdate,
    ps.job_enddate,
    ps.actual_job_startdate,
    ps.actual_job_enddate,
    ps.reason,
    ps.comment,
    ps.created_at,
    ps._s_silver_ts                                        AS recorded_at,
    ps.invalid                                             AS is_superseded,
    COALESCE(el.estimate_ids, ARRAY[]::bigint[])           AS estimate_ids,
    freed.freed_resource                                    AS freed_resource,
    reschedule.reschedule_event                             AS reschedule_event
FROM silver.project_schedule ps
LEFT JOIN LATERAL (
    SELECT array_agg(DISTINCT pes.estimate_id ORDER BY pes.estimate_id) AS estimate_ids
    FROM silver.project_estimate_schedule pes
    WHERE pes._s_tenantid   = ps._s_tenantid
      AND pes._s_env        = ps._s_env
      AND pes.schedule_id   = ps.id
      AND pes.estimate_id  IS NOT NULL
      AND pes._s_is_deleted = FALSE
      AND pes.invalid       = FALSE
) el ON TRUE
-- Confidence evidence (constraint #16/#36 — see EDEN_CustomerSupport_Reasoning_
-- Constraints.md Q2). Ceiling is always "low" here (never "high") — no FK in
-- this schema proves a freed-up resource caused THIS project's schedule to
-- move. "low" requires BOTH signals below within a 7-day-back/1-day-forward
-- window of this version's created_at: (1) a resource also assigned to THIS
-- project that completed an assignment on a DIFFERENT project, and (2) a
-- reschedule-tagged silver.audit_log event on THIS project. Confidence/
-- evidence computed in _schedule_entry() below — never an LLM self-assessment.
LEFT JOIN LATERAL (
    -- Field names match gold_eden.project_health.scheduleRevisions[].evidence
    -- exactly (EDEN Customer Support Master Constraints Reference, Part B).
    -- Sentinel-date filter (exclude 2050+ rows) applies per the cross-cutting
    -- constraint — non-'completed' resource_schedule rows carry far-future
    -- placeholder dates, but even 'completed' rows are defensively filtered
    -- here since the constraint doc names resource_schedule generally.
    -- Pick the winning resource_schedule row BEFORE joining to resource /
    -- project / project_schedule, not after. Semantically identical -- the
    -- outer ORDER BY ... LIMIT 1 always discarded every other candidate, and
    -- none of the joined columns participate in the filter or the ordering,
    -- so no join could ever change which row wins. Only the winner's fields
    -- appear in the output object.
    --
    -- The old shape joined all ~4,340 candidates and then threw away 4,339.
    -- Worst hit was the nested sched_other lateral, which re-resolved
    -- "latest schedule version" per candidate row: 47,745 executions for one
    -- project (candidates x schedule versions), 2,961,518 of the query's
    -- 3,148,560 buffer hits. Now it runs once per schedule version.
    -- Measured on prod tn59695547/1183 2026-08-24.
    SELECT JSONB_BUILD_OBJECT(
        'resourceId',            rs_other.resource_id,
        'resourceName',          r_other.name,
        'priorProjectId',        rs_other.project_id,
        'priorProjectOrderNo',   p_other.order_no,
        'priorProjectPlannedEnd', sched_other.job_enddate,
        'priorProjectActualEnd', rs_other.end_time
    )                                                        AS freed_resource
    FROM (
        SELECT rs_win.resource_id,
               rs_win.project_id,
               rs_win.end_time,
               rs_win._s_tenantid,
               rs_win._s_env
        FROM silver.resource_schedule rs_win
        WHERE rs_win._s_tenantid   = ps._s_tenantid
          AND rs_win._s_env        = ps._s_env
          AND rs_win.project_id   <> ps.project_id
          AND rs_win._s_is_deleted = FALSE
          AND rs_win.status        = 'completed'
          AND EXTRACT(YEAR FROM rs_win.end_time) < 2050
          AND rs_win.end_time BETWEEN ps.created_at - INTERVAL '7 days'
                                   AND ps.created_at + INTERVAL '1 day'
          AND rs_win.resource_id IN (
              SELECT DISTINCT rs_this.resource_id
              FROM silver.resource_schedule rs_this
              WHERE rs_this._s_tenantid   = ps._s_tenantid
                AND rs_this._s_env        = ps._s_env
                AND rs_this.project_id    = ps.project_id
                AND rs_this._s_is_deleted = FALSE
          )
        ORDER BY rs_win.end_time DESC
        LIMIT 1
    ) rs_other
    LEFT JOIN silver.resource r_other
           ON r_other.id            = rs_other.resource_id
          AND r_other._s_tenantid   = rs_other._s_tenantid
          AND r_other._s_env        = rs_other._s_env
          AND r_other._s_is_deleted = FALSE
    LEFT JOIN silver.project p_other
           ON p_other.id            = rs_other.project_id
          AND p_other._s_tenantid   = rs_other._s_tenantid
          AND p_other._s_env        = rs_other._s_env
          AND p_other._s_is_deleted = FALSE
    LEFT JOIN LATERAL (
        SELECT ps_other.job_enddate
        FROM silver.project_schedule ps_other
        WHERE ps_other._s_tenantid   = rs_other._s_tenantid
          AND ps_other._s_env        = rs_other._s_env
          AND ps_other.project_id    = rs_other.project_id
          AND ps_other._s_is_deleted = FALSE
          AND ps_other.invalid       = FALSE
        ORDER BY ps_other.version DESC NULLS LAST
        LIMIT 1
    ) sched_other ON TRUE
    -- Retained as a guard, not for filtering: the derived table above yields
    -- at most one row and both LEFT JOINs are on unique keys
    -- (silver.resource / silver.project PK is (_s_env, _s_tenantid, id)), so
    -- this is already single-row. Kept so the subquery's one-row contract
    -- stays explicit if those joins ever change.
    LIMIT 1
) freed ON TRUE
LEFT JOIN silver.project p_sched
       ON p_sched.id            = ps.project_id
      AND p_sched._s_tenantid   = ps._s_tenantid
      AND p_sched._s_env        = ps._s_env
      AND p_sched._s_is_deleted = FALSE
LEFT JOIN LATERAL (
    -- silver.audit_log.resource_id is a UUID matching silver.project.project_uid,
    -- never project.id.
    SELECT JSONB_BUILD_OBJECT(
        'action',    al.action,
        'createdAt', al.created_at
    )                                                        AS reschedule_event
    FROM silver.audit_log al
    WHERE al._s_tenantid  = ps._s_tenantid
      AND al._s_env       = ps._s_env
      AND al.resource_id  = p_sched.project_uid
      AND al.action ILIKE '%%RESCHEDUL%%'
      AND al.created_at BETWEEN ps.created_at - INTERVAL '7 days'
                             AND ps.created_at + INTERVAL '1 day'
    ORDER BY al.created_at DESC
    LIMIT 1
) reschedule ON TRUE
WHERE ps._s_tenantid   = %(tenantid)s
  AND ps._s_is_deleted  = FALSE
{project_filter}
-- Secondary sort for deterministic duplicate-`version` grouping — see
-- _dedupe_same_version_rows() / the same fix applied to estimate history.
ORDER BY ps.project_id, CAST(ps.version AS INT), ps._s_silver_ts
"""

# Date fields compared (date-only) when deciding whether a schedule version is a
# *meaningful* revision vs no-op churn. A change in any one — or in `reason` —
# makes the version worth emitting.
_HISTORY_DATE_FIELDS = (
    "job_startdate", "job_enddate",
    "actual_job_startdate", "actual_job_enddate",
)


def fetch_schedule_history(
    pg_dsn: str, tenantid: str, project_id: str | None = None
) -> dict[tuple, list[dict]]:
    """Fetch every retained schedule version per project, grouped by (source_env, project_id).

    Keyed by a (source_env, project_id) tuple — see ``fetch_estimate_history``'s
    docstring for why bare project_id is not a safe grain across Silver `_s_env`
    sources. Scoped to one project when project_id is given (the streaming path).
    Rows are ordered by version ascending within each project.
    """
    project_filter = "  AND ps.project_id = %(project_id)s" if project_id is not None else ""
    sql = _SCHEDULE_HISTORY_QUERY.format(project_filter=project_filter)
    params: dict = {"tenantid": tenantid}
    if project_id is not None:
        params["project_id"] = project_id
    by_project: dict[tuple, list[dict]] = {}
    conn = psycopg2.connect(pg_dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            for row in cur:
                key = (row.get("source_env"), str(row["project_id"]))
                by_project.setdefault(key, []).append(dict(row))
    finally:
        conn.close()
    for key, rows in by_project.items():
        by_project[key] = _dedupe_same_version_rows(rows, _is_schedule_meaningful)
    return by_project


# ---------------------------------------------------------------------------
# Tenant admins — embedded as doc["assignees"] on every project doc (WI:
# appendAdmins() port).
#
# Ported from the legacy Java appendAdmins()/fetchTenantAdmins() flow, which
# pushes the SAME tenant-wide admin list onto every project's Mongo document's
# "assignees" array — the field is named assignees here to match that legacy
# structure, even though today it only ever contains admin-role entries.
# This is NOT an approximation of a project-scoped "Project Admin" role — per
# docs/canonical/user_access_management_model.md §"No project-scoped role",
# user_role_map assigns roles tenant-wide and project_member carries no role,
# so a per-project admin can't be computed from Silver. The legacy behavior of
# blasting the identical list onto every project is therefore reproduced
# faithfully here, not worked around.
#
# Two fixes vs the legacy SQL (both verified against
# docs/canonical/user_access_management_model.md):
#   1. Join on user_role_map.user_account_id = user_account.id — the real FK.
#      The legacy query joined urm.user_id = ua.object_id, which the model
#      doc explicitly flags as a redundant, ambiguous external-id duplicate
#      that must not be used as a join key.
#   2. Field renamed userUid (was resource_uid in the legacy doc) — object_id
#      is the user's external IdP id, not silver.resource.resource_uid (an
#      unrelated workforce-resource identifier). Reusing that name would have
#      implied a join to silver.resource that never happens.
#
# Keyed by source_env (like estimate/schedule history) so a multi-env tenant's
# projects only ever see admins from their own Silver environment.
#
# DISTINCT is required, not cosmetic — verified live on tenant tn59395620:
# silver.user_role_map holds 7 duplicate rows for the same (user, role,
# assign_start_date, assign_end_date) assignment. Every duplicate found there
# was temporally expired, so it happened not to double-count in that sample,
# but a user with two *concurrently effective* admin rows (dup ingestion,
# overlapping assignments) would otherwise appear twice in the embedded list.
# ---------------------------------------------------------------------------
_TENANT_ADMINS_QUERY = """
SELECT DISTINCT
    ua.object_id                                             AS admin_uid,
    ua._s_env                                                AS source_env,
    CONCAT_WS(' ', ua.firstname, ua.middlename, ua.lastname)  AS name
FROM silver.roles r
JOIN silver.user_role_map urm
  ON urm.roles_id       = r.id
 AND urm._s_tenantid     = r._s_tenantid
 AND urm._s_env          = r._s_env
JOIN silver.user_account ua
  ON ua.id               = urm.user_account_id
 AND ua._s_tenantid       = urm._s_tenantid
 AND ua._s_env            = urm._s_env
WHERE r._s_tenantid        = %(tenantid)s
  AND LOWER(r.name)        = 'admin'
  AND r.active              = TRUE
  AND r._s_is_deleted       = FALSE
  AND ua.active              = TRUE
  AND ua.deleted             = FALSE
  AND ua.object_id          IS NOT NULL
  AND urm._s_is_deleted      = FALSE
  AND urm.assign_start_date <= NOW()
  -- assign_end_date deliberately NOT checked — admin assignments are treated
  -- as still-effective past their end date (business rule change 2026-07-30;
  -- same fix applied to canonical/load_user_permissions.py's role/permission
  -- resolution, see that file's LATERAL join comment for the tn24047750 case
  -- that surfaced this). Safe to drop unconditionally here since this query
  -- is already scoped to LOWER(r.name) = 'admin' above.
"""


def fetch_tenant_admins(pg_dsn: str, tenantid: str) -> dict[str, list[dict]]:
    """Fetch the tenant's effective 'admin'-role users, grouped by source_env.

    Tenant-wide (not project-scoped) — see module note above. Returned dict
    maps source_env -> list of {admin_uid, name} rows.
    """
    by_env: dict[str, list[dict]] = {}
    conn = psycopg2.connect(pg_dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(_TENANT_ADMINS_QUERY, {"tenantid": tenantid})
            for row in cur:
                by_env.setdefault(row.get("source_env"), []).append(dict(row))
    finally:
        conn.close()
    return by_env


def _shape_admin(row: dict) -> dict:
    return {
        "name":    row.get("name"),
        "userUid": row.get("admin_uid"),
        "roles":   ["admin"],
    }


def _date_only(v) -> "datetime | None":
    """Coerce a date/datetime value to a UTC-midnight datetime (date-part-only
    precision) — a native BSON date, never a string."""
    ts = _to_ts(v)
    if ts is None:
        return None
    return datetime(ts.year, ts.month, ts.day, tzinfo=timezone.utc)


def _day_shift(curr, prev) -> int | None:
    """Whole-day shift between two date/datetime values; None if either is missing."""
    c, p = _to_ts(curr), _to_ts(prev)
    if c is None or p is None:
        return None
    return (c.date() - p.date()).days


def _schedule_entry(row: dict, prev: dict | None, is_current: bool) -> dict:
    snapshot = {
        "jobStartDate":       _date_only(row.get("job_startdate")),
        "jobEndDate":         _date_only(row.get("job_enddate")),
        "actualJobStartDate": _date_only(row.get("actual_job_startdate")),
        "actualJobEndDate":   _date_only(row.get("actual_job_enddate")),
    }
    if prev is None:
        delta = None
    else:
        delta = {
            "jobStartShiftDays": _day_shift(row.get("job_startdate"), prev.get("job_startdate")),
            "jobEndShiftDays":   _day_shift(row.get("job_enddate"),   prev.get("job_enddate")),
        }
    estimate_ids = [str(e) for e in (row.get("estimate_ids") or [])][:_MAX_HISTORY_ENTRIES]
    confidence, evidence = _schedule_confidence_and_evidence(row)
    return {
        "version":     _int(row.get("version")),
        "validFrom":   _iso(row.get("created_at")),
        "recordedAt":  _iso(row.get("recorded_at")),
        "reason":      row.get("reason"),
        "comment":     row.get("comment"),
        "snapshot":    snapshot,
        "delta":       delta,
        "estimateIds": estimate_ids,
        "isCurrent":   is_current,
        "confidence":  confidence,
        "evidence":    evidence,
    }


def _schedule_confidence_and_evidence(row: dict) -> tuple[str, dict]:
    """Deterministic evidence-support level for this schedule revision — never
    an LLM self-assessment. Computed once here, at ETL write time, by joining
    the real Postgres tables (silver.resource_schedule, silver.audit_log);
    Eden only reads and renders this pre-computed verdict.

    Ceiling is always "low" (never "high") — no foreign key in this schema
    proves a freed-up resource caused THIS project's schedule to move.

    * "low"  — BOTH signals found within the correlation window: a resource
      also assigned to THIS project completed an assignment on a DIFFERENT
      project (freedResource), AND a reschedule-tagged silver.audit_log event
      exists for THIS project (rescheduleEvent). A real timing correlation,
      never elevated to certainty (constraint #16/#36).
    * "none" — one or both signals are absent within the window.
    """
    freed = row.get("freed_resource")
    freed = _maybe_json(freed) if isinstance(freed, str) else freed
    reschedule = row.get("reschedule_event")
    reschedule = _maybe_json(reschedule) if isinstance(reschedule, str) else reschedule

    planned_end = _to_ts(freed.get("priorProjectPlannedEnd")) if freed else None
    actual_end = _to_ts(freed.get("priorProjectActualEnd")) if freed else None
    days_finished_early = (
        (planned_end.date() - actual_end.date()).days
        if planned_end is not None and actual_end is not None else None
    )

    # Field names match gold_eden.project_health.scheduleRevisions[].evidence
    # exactly (EDEN Customer Support Master Constraints Reference, Part B).
    evidence = {
        "resourceId":             (str(freed["resourceId"])
                                     if freed and freed.get("resourceId") is not None else None),
        "resourceName":           freed.get("resourceName") if freed else None,
        "priorProjectId":         (str(freed["priorProjectId"])
                                     if freed and freed.get("priorProjectId") is not None else None),
        "priorProjectOrderNo":    freed.get("priorProjectOrderNo") if freed else None,
        "priorProjectPlannedEnd": planned_end,
        "priorProjectActualEnd":  actual_end,
        "daysFinishedEarly":      days_finished_early,
        "reschedulePersistedAt":  parse_ts(reschedule.get("createdAt")) if reschedule else None,
    }
    confidence = "low" if (freed and reschedule) else "none"
    return confidence, evidence


def _is_schedule_meaningful(row: dict, prev: dict) -> bool:
    """A revision is meaningful vs the previous *kept* one when any tracked job-date
    field changed (date precision) or the reason text changed."""
    for f in _HISTORY_DATE_FIELDS:
        if _date_only(row.get(f)) != _date_only(prev.get(f)):
            return True
    return (row.get("reason") or None) != (prev.get("reason") or None)


def collapse_schedule_history(rows: list[dict]) -> tuple[list[dict], dict | None]:
    """Collapse raw schedule version rows into a meaningful-revision timeline + summary.

    Pure function (no I/O). `rows` must be ordered by version ascending. Returns
    ``(history, summary)``; ``([], None)`` when empty. Same collapse rule as
    estimates: always keep baseline + current; in between keep a version only when
    it is a meaningful revision vs the last kept version. Capped to the most recent
    ``_MAX_HISTORY_ENTRIES`` with the baseline always retained.
    """
    if not rows:
        return [], None

    kept: list[dict] = [rows[0]]
    meaningful_count = 0
    for row in rows[1:]:
        if _is_schedule_meaningful(row, kept[-1]):
            kept.append(row)
            meaningful_count += 1
    # Keep the true current version even if it was a no-op vs the prior kept one;
    # this forced entry is NOT a meaningful revision and must not inflate the count.
    if kept[-1] is not rows[-1]:
        kept.append(rows[-1])

    baseline, current = rows[0], rows[-1]
    current_id = id(current)

    history: list[dict] = []
    prev: dict | None = None
    for row in kept:
        history.append(_schedule_entry(row, prev, is_current=id(row) == current_id))
        prev = row

    if len(history) > _MAX_HISTORY_ENTRIES:
        history = [history[0]] + history[-(_MAX_HISTORY_ENTRIES - 1):]

    # Original planned finish = the first non-null job_enddate across all versions
    # (the baseline may have no end date yet, as in project 1032 v0).
    first_planned_end = next(
        (r.get("job_enddate") for r in rows if r.get("job_enddate") is not None), None
    )
    summary = {
        "currentVersion":          _int(current.get("version")),
        "baselineVersion":         _int(baseline.get("version")),
        "rawVersionCount":         len(rows),
        "meaningfulRevisionCount": meaningful_count,
        "originalJobStartDate":    _date_only(baseline.get("job_startdate")),
        "originalJobEndDate":      _date_only(first_planned_end),
        "currentJobStartDate":     _date_only(current.get("job_startdate")),
        "currentJobEndDate":       _date_only(current.get("job_enddate")),
        "totalEndShiftDays":       _day_shift(current.get("job_enddate"), first_planned_end),
        "lastReason":              current.get("reason"),
        "firstPlannedAt":          _iso(baseline.get("created_at")),
        "lastChangedAt":           _iso(current.get("created_at")),
    }
    return history, summary


def _scoped_query(pg_dsn: str, project_id: str | None) -> str:
    """Return the Silver query, optionally scoped to a single project.

    When project_id is provided, splice an ``AND p.id = %(project_id)s``
    predicate into the outer WHERE clause (before ORDER BY) so the same
    tested SQL runs for one project — used by the real-time streaming job.

    __TAE_JOIN__ resolves to the real task_attribute/attributes pivot once
    those tables exist in a given env's Silver, or an all-NULL inert join
    until then — same self-healing pattern used in load_task_intelligence.py.

    __INVALID_FILTER__ resolves to the at.invalid predicate only when that
    column currently exists on silver.task, else an empty string — see the
    comment at the filter's call site in _SILVER_QUERY.

    __PROJECT_FILTER__ (six occurrences, one per CTE in _SILVER_QUERY) scopes
    each CTE down to the single project when project_id is given, matching
    the outer `AND p.id = %(project_id)s` splice below. This is REQUIRED, not
    an optional optimization: without it, the CTEs would aggregate every
    project in the tenant even for the real-time single-project call, since a
    CTE has no correlation to the outer query the way the LATERAL joins this
    replaced did — Postgres's planner MAY inline/push the outer predicate
    down into a singly-referenced CTE, but that's not guaranteed, so this
    scopes it explicitly rather than relying on planner behavior.
    """
    has_tae = table_exists(pg_dsn, "task_attribute") and table_exists(pg_dsn, "attributes")
    query = _SILVER_QUERY.replace("__TAE_JOIN__", _TAE_JOIN_REAL if has_tae else _TAE_JOIN_INERT)

    task_cols = get_silver_columns(pg_dsn, "task")
    query = query.replace(
        "__INVALID_FILTER__",
        "AND (at.invalid IS NULL OR at.invalid = FALSE)" if "invalid" in task_cols else "",
    )

    if project_id is None:
        return query.replace("__PROJECT_FILTER__", "")
    query = query.replace("__PROJECT_FILTER__", "AND project_id = %(project_id)s")
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

    Reused by the real-time Eden streaming job (project_id set) and available
    for any caller needing the shaped docs without the Mongo write path.
    """
    est_by_project = fetch_estimate_history(pg_dsn, tenantid, project_id)
    sch_by_project = fetch_schedule_history(pg_dsn, tenantid, project_id)
    admins_by_env = fetch_tenant_admins(pg_dsn, tenantid)
    return [
        shape_doc(
            row, gold_ts,
            est_by_project.get((row.get("source_env"), str(row["project_id"]))),
            sch_by_project.get((row.get("source_env"), str(row["project_id"]))),
            admins_by_env.get(row.get("source_env")),
        )
        for row in iter_silver_rows(pg_dsn, tenantid, max_docs, project_id)
    ]


# Tables the batch newness-check watches beyond silver.project itself —
# any one of these changing alone (no project row touched) must still
# trigger a full re-aggregate, or the batch loader would skip re-embedding
# the affected field indefinitely. user_role_map/user_account back the
# tenant-wide admins list (fetch_tenant_admins); bom/material_cost/
# vendor_material_map back estimateHistory[].confidence/.evidence (added
# 2026-08-01 — Q1 reasoning evidence); resource_schedule/audit_log back
# scheduleRevisions[].confidence/.evidence (added 2026-08-01 — Q2 reasoning
# evidence). NOTE: in steady state `eden` is realtime (gold_config.yml), so
# this batch path only actually runs on `--rebuild` (which bypasses this
# check entirely) or if eden is ever reconfigured to batch — see
# gold_eden_stream.py's CDC trigger table for the realtime-path equivalent.
_NEWNESS_WATCH_TABLES = (
    "project", "user_role_map", "user_account",
    "bom", "material_cost", "vendor_material_map",
    "resource_schedule", "audit_log",
)


def _has_new_silver_rows(pg_dsn: str, tenantid: str, since: datetime) -> bool:
    union_sql = " UNION ALL ".join(
        f"SELECT 1 FROM silver.{t} WHERE _s_tenantid = %s AND _s_silver_ts > %s"
        for t in _NEWNESS_WATCH_TABLES
    )
    params = tuple(x for _ in _NEWNESS_WATCH_TABLES for x in (tenantid, since))
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT EXISTS ({union_sql})", params)
        return bool(cur.fetchone()[0])


def _get_max_silver_ts(pg_dsn: str, tenantid: str) -> datetime:
    # MIN of each _s_env's own MAX (across all watched tables combined for
    # that env), not the overall MAX — see the canonical loaders'
    # get_max_silver_ts_per_env for why: a shared cursor advanced to the
    # fastest-moving env's watermark would mask a later update in a
    # slower-moving env still below that watermark.
    union_sql = " UNION ALL ".join(
        f"SELECT _s_env, _s_silver_ts AS ts FROM silver.{t} WHERE _s_tenantid = %s"
        for t in _NEWNESS_WATCH_TABLES
    )
    params = tuple(tenantid for _ in _NEWNESS_WATCH_TABLES)
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT MIN(env_max) FROM ("
            f"  SELECT _s_env, MAX(ts) AS env_max FROM ({union_sql}) rows"
            f"  GROUP BY _s_env"
            f") per_env",
            params,
        )
        ts = cur.fetchone()[0]
    if ts is None:
        return EPOCH
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _to_ts(v) -> "datetime | None":
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo is not None else v.replace(tzinfo=timezone.utc)
    # psycopg2 returns datetime.date for PG DATE columns; datetime is a subclass
    # of date so the isinstance(v, datetime) check above must come first.
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
    return None


def _float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v) -> int:
    if v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _maybe_json(v):
    """Parse a free-text column that may hold JSON.

    Returns the parsed value when valid JSON, the original string when it
    isn't (never silently dropped), or None for empty/null input. Used for
    project.customer/customer_asset/tags/team_members/additional_info and
    purchase_order.tax_info/additional_info.
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


_MAX_STATUS_TIMELINE_ENTRIES = 25


def _parse_status_timeline(raw) -> list[dict]:
    """Normalize silver.project.state_change_history into a status-transition
    timeline, oldest first.

    This is a direct read of the platform's own recorded state-change log
    (``{changedAt, changedBy, changeReason, newState}`` entries) — it is NOT a
    synthesized Change Order entity. There is no dedicated Change Order table
    in Silver (see docs/gold-layer/historical_data_strategy.md §8), so no CO
    fidelity (approval workflow, scope/value delta) is claimed here; that
    requires backend confirmation of the real CO path first. Tolerant of both
    camelCase (documented shape) and snake_case keys in case the source varies.
    """
    events = _maybe_json(raw)
    if not isinstance(events, list):
        return []
    timeline: list[dict] = []
    for e in events:
        if not isinstance(e, dict):
            continue
        timeline.append({
            "changedAt":     e.get("changedAt") or e.get("changed_at"),
            "changedBy":     e.get("changedBy") or e.get("changed_by"),
            "changeReason":  e.get("changeReason") or e.get("change_reason"),
            "newState":      e.get("newState") or e.get("new_state") or e.get("status"),
        })
    timeline.sort(key=lambda x: x.get("changedAt") or "")
    if len(timeline) > _MAX_STATUS_TIMELINE_ENTRIES:
        timeline = [timeline[0]] + timeline[-(_MAX_STATUS_TIMELINE_ENTRIES - 1):]
    return timeline


def _customer_display_name(customer) -> str | None:
    """Build a display name from the parsed silver.project.customer object."""
    if not isinstance(customer, dict):
        return None
    parts = [customer.get("firstName"), customer.get("lastName")]
    name = " ".join(p.strip() for p in parts if p and str(p).strip())
    return name or None


def _shape_purchase_order(raw: dict) -> dict:
    """Consolidate one raw purchase_order row (+ its line items) into a
    full-detail Gold record."""
    shaped = dict(raw)
    shaped["taxInfo"] = _maybe_json(raw.get("taxInfo"))
    shaped["additionalInfo"] = _maybe_json(raw.get("additionalInfo"))
    shaped["billingAddress"] = _maybe_json(raw.get("billingAddress"))
    shaped["shippingAddress"] = _maybe_json(raw.get("shippingAddress"))
    # Date fields arrive as strings (JSONB_BUILD_OBJECT in SQL has no native
    # date type) — parse_ts() converts them back to native BSON dates.
    shaped["counterOfferDate"] = parse_ts(raw.get("counterOfferDate"))
    shaped["reminderAt"] = parse_ts(raw.get("reminderAt"))
    shaped["createdAt"] = parse_ts(raw.get("createdAt"))
    shaped["updatedAt"] = parse_ts(raw.get("updatedAt"))
    shaped["items"] = [
        {**item,
         "createdAt": parse_ts(item.get("createdAt")),
         "updatedAt": parse_ts(item.get("updatedAt"))}
        for item in (raw.get("items") or [])
    ]
    return shaped


def shape_doc(row: dict, gold_ts: datetime,
              estimate_history: list[dict] | None = None,
              schedule_history: list[dict] | None = None,
              tenant_admins: list[dict] | None = None) -> dict:
    estimated_cost = _float(row.get("estimated_cost"))
    committed_spend = _float(row.get("committed_spend")) or 0.0
    remaining = (estimated_cost - committed_spend) if estimated_cost is not None else None

    slip_days = _int(row.get("slip_days"))
    # Schedule risk must distinguish a *known* on-track project from one that has
    # no project_schedule row at all. With no schedule, slip_days falls through
    # to 0, which previously reported "on_track" — a silent false-negative that
    # an agent could read as reassurance. Surface absence explicitly as
    # "unknown" so downstream reasoning can treat it as missing, not healthy.
    has_schedule = (
        row.get("schedule_revised_count") is not None
        or row.get("job_startdate") is not None
        or row.get("schedule_silver_ts") is not None
    )
    if not has_schedule:
        schedule_risk = "unknown"
    else:
        schedule_risk = "at_risk" if slip_days > 0 else "on_track"

    watermarks = {
        "project":          _to_ts(row.get("p_silver_ts")),
        "task":             _to_ts(row.get("task_silver_ts")),
        "projectEstimate":  _to_ts(row.get("estimate_silver_ts")),
        "purchaseOrder":    _to_ts(row.get("po_silver_ts")),
        "invoice":          _to_ts(row.get("invoice_silver_ts")),
        "projectSchedule":  _to_ts(row.get("schedule_silver_ts")),
    }
    valid_wm = [ts for ts in watermarks.values() if ts is not None]
    lag_ms = int((gold_ts - min(valid_wm)).total_seconds() * 1000) if valid_wm else 0

    # Per-domain data completeness + an aggregate confidence (fraction of the
    # contributing source domains that actually had data for this project).
    # Lets an agent tell "known-good zero" from "absent data" before reasoning.
    data_completeness = {
        "project":       watermarks["project"] is not None,
        "task":          watermarks["task"] is not None,
        "estimate":      watermarks["projectEstimate"] is not None,
        "purchaseOrder": watermarks["purchaseOrder"] is not None,
        "invoice":       watermarks["invoice"] is not None,
        "schedule":      has_schedule,
    }
    confidence = round(sum(data_completeness.values()) / len(data_completeness), 4)

    # Estimate change timeline (WI 21303). Collapsed to meaningful changes with
    # the native `reason`; empty list + null summary when the project has no
    # estimate rows. Built from valid-time (created_at) ordering.
    estimate_history_list, estimate_summary = collapse_estimate_history(
        estimate_history or []
    )

    # Schedule change timeline (WI 22463). Collapsed reschedule history with the
    # native reason/comment; empty list + null summary when the project has no
    # schedule rows. Built from valid-time (created_at) ordering.
    schedule_history_list, schedule_summary = collapse_schedule_history(
        schedule_history or []
    )

    customer = _maybe_json(row.get("customer_raw"))
    customer = customer if isinstance(customer, dict) else None

    status_timeline = _parse_status_timeline(row.get("state_change_history_raw"))

    # Full project record — every silver.project column not already surfaced
    # as a top-level field above (trade/location/orderNo/etc. stay top-level
    # for backward compatibility; everything else nests here).
    project_detail = {
        "customer":            customer,
        "customerAsset":       _maybe_json(row.get("customer_asset_raw")),
        "description":         row.get("description"),
        "tradeType":           row.get("trade_type"),
        "projectType":         row.get("project_type"),
        "tags":                _maybe_json(row.get("tags_raw")),
        "zipcode":             row.get("zipcode"),
        "assignedTo":          row.get("assigned_to"),
        "jobDoneBy":           row.get("job_done_by"),
        "teamMembers":         _maybe_json(row.get("team_members_raw")),
        "referenceProjectId":  (str(row["reference_project_id"])
                                 if row.get("reference_project_id") is not None else None),
        "parentProjectId":     (str(row["parent_project_id"])
                                 if row.get("parent_project_id") is not None else None),
        "taxExemption":        row.get("tax_exemption"),
        "additionalInfo":      _maybe_json(row.get("project_additional_info_raw")),
        "createdAt":           row.get("project_created_at"),
        "createdBy":           row.get("project_created_by"),
        "updatedAt":           row.get("project_updated_at"),
        "version":             row.get("project_version"),
    }

    doc = {
        "tenantId":                 row["tenantid"],
        "projectId":                str(row["project_id"]),
        "orderNo":                  row.get("order_no"),
        # Contract documents this as "Denormalized project name" — silver.project
        # has no name column, but the source system pre-formats description as
        # "{customerFirstName customerLastName}, {city}-{trade}-{orderNo}" (e.g.
        # "vijay cus, Chennai-Roof Enhancement-708869") — verified live on
        # tn24047750 2026-07-30 — so it's the best available project name and is
        # preferred over re-deriving just the customer's first/last name.
        # Preference order: description (pre-formatted, richest) -> customer's
        # display name -> order_no (human-readable, populated on ~99.7% of
        # projects per EDEN's live audit 2026-07-13) -> project_uid (last
        # resort — a raw UUID a user could never type/search for). Full customer
        # record is under "project.customer" below.
        "projectName":              (row.get("description")
                                       or _customer_display_name(customer)
                                       or row.get("order_no")
                                       or row.get("project_uid")),
        "trade":                    row.get("trade"),
        "location":                 row.get("location"),
        "project":                  project_detail,
        "purchaseOrders":           [_shape_purchase_order(o) for o in (row.get("all_purchase_orders") or [])],
        "assignees":                [_shape_admin(a) for a in (tenant_admins or [])],
        "_grain":                   "eden_project_health",
        "_schemaVersion":           1,
        "_silverLsn":               int(row.get("silver_lsn") or 0),
        "isDeleted":                bool(row.get("is_deleted") or False),
        "deletedAt":                _to_ts(row.get("deleted_at")),
        "dataAsOf":                 gold_ts,
        "completionPct":            _float(row.get("completion_pct")),
        "totalTaskCount":           _int(row.get("total_task_count")),
        "doneTaskCount":            _int(row.get("done_task_count")),
        "overdueTaskCount":         _int(row.get("overdue_task_count")),
        "unassignedTaskCount":      _int(row.get("unassigned_task_count")),
        "estimatedCost":            estimated_cost,
        "estimateVersion":          _int(row.get("estimate_version")) or None,
        "materialCost":             _float(row.get("material_cost")),
        "workerCost":               _float(row.get("worker_cost")),
        "toolCost":                 _float(row.get("tool_cost")),
        "overheadCost":             _float(row.get("overhead_cost")),
        "margin":                   _float(row.get("margin")),
        "estimateHistory":          estimate_history_list,
        "estimateSummary":          estimate_summary,
        "committedSpend":           committed_spend,
        "remainingBudget":          remaining,
        "outstandingInvoicesTotal": _float(row.get("outstanding_invoices_total")),
        "scheduleRisk":             schedule_risk,
        "slipDays":                 slip_days,
        "scheduleRevisedCount":     _int(row.get("schedule_revised_count")),
        "scheduleRevisions":        schedule_history_list,
        "scheduleSummary":          schedule_summary,
        "statusTimeline":           status_timeline,
        "jobStartDate":             _to_ts(row.get("job_startdate")),
        "actualJobStartDate":       _to_ts(row.get("actual_job_startdate")),
        "jobEndDate":               _to_ts(row.get("job_enddate")),
        "actualJobEndDate":         _to_ts(row.get("actual_job_enddate")),
        "_silverWatermarks":        watermarks,
        "_dataCompleteness":        data_completeness,
        "_confidence":              confidence,
        "lafieldcraft":                    lag_ms,
    }
    doc.update(lineage_fields(FEATURE_NAME, gold_ts, iso=False,
                              source_updated_at=watermarks.get("project"),
                              source_env=row.get("source_env")))
    return doc


@dataclass
class WriteStats:
    upserted: int = 0
    modified: int = 0
    matched:  int = 0
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
            stats.matched  += res.matched_count  or 0
            stats.batches  += 1
            return
        except BulkWriteError as exc:
            errors = exc.details.get("writeErrors", []) if exc.details else []
            stats.failed += len(errors)
            log.error("bulk write partial failure: %d errors", len(errors))
            stats.batches += 1
            return
        except TRANSIENT_ERRORS as exc:
            if attempt == cfg.max_retries:
                raise
            log.warning("transient error attempt %d/%d: %s — retry %.1fs",
                        attempt, cfg.max_retries, exc, delay)
            time.sleep(delay)
            delay *= 2
        except OperationFailure as exc:
            log.error("operation failure (non-retryable): %s", exc)
            raise


_shutdown = False


def _install_signal_handlers() -> None:
    def _handler(signum, _frame):
        global _shutdown
        log.warning("signal %s — finishing current batch then stopping", signum)
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
                             retryWrites=False, appname="eden-project-health")
        coll = client[cfg.db_name][COLLECTION]

    pending: list[dict] = []
    seen = 0

    def flush() -> None:
        if not pending:
            return
        for d in pending:
            _contract_validator.validate(d, COLLECTION)
        ops = [
            UpdateOne(
                build_lsn_guard(
                    {"tenantId": d["tenantId"], "projectId": d["projectId"],
                     "sourceEnv": d.get("sourceEnv")},
                    d.get("_silverLsn"),
                ),
                {"$set": d, "$setOnInsert": set_on_insert(gold_ts, iso=False)},
                upsert=True,
            )
            for d in pending
        ]
        if ops and not cfg.dry_run and coll is not None:
            # See lineage.cleanup_stale_order_no_siblings' module comment: the
            # source system creates a NEW internal project id when a draft is
            # converted, leaving an abandoned duplicate doc under the old id.
            # Only clean up after confirming THIS batch wrote cleanly (failed
            # count unchanged) -- a partial failure means we can't be sure
            # every doc in `pending` actually landed, so cleanup waits for the
            # next clean pass rather than risk deleting a sibling based on an
            # unconfirmed write.
            _failed_before = stats.failed
            bulk_write_with_retry(coll, ops, cfg, stats)
            if stats.failed == _failed_before:
                for d in pending:
                    order_no = d.get("orderNo")
                    if not order_no:
                        continue
                    deleted = cleanup_stale_order_no_siblings(
                        coll, d["tenantId"], order_no, d["projectId"], d.get("sourceEnv")
                    )
                    if deleted:
                        log.info(
                            "cleaned up %d stale sibling doc(s) | orderNo=%s "
                            "tenant=%s sourceEnv=%s currentProjectId=%s",
                            deleted, order_no, d["tenantId"], d.get("sourceEnv"), d["projectId"],
                        )
        pending.clear()

    try:
        # Estimate/schedule history are fetched PER PROJECT here, not once for
        # the whole tenant. They used to run unscoped (fetch_estimate_history/
        # fetch_schedule_history(pg_dsn, tenantid) with no project_id), which
        # matched the "avoid N+1 round trips" instinct but backfires badly:
        # both queries LATERAL-join silver.bom/silver.material_cost per
        # estimate/schedule VERSION with no supporting index on either table
        # (confirmed live — see create_silver_tables.py's _GOLD_SUPPORT_INDEXES
        # comment for the identical bug class this file already hit once
        # before, on task_attribute). Run unscoped, that unindexed per-row
        # cost is paid ACROSS EVERY PROJECT IN THE TENANT inside one query —
        # observed as a 5-6 min stall on a --rebuild sweep. Scoping per
        # project bounds each call to one project's own versions/BOM lines
        # (exactly what the real-time streaming path already does via
        # build_documents(project_id=...) for every live CDC update — this
        # just applies the same scoping to the batch/--rebuild sweep too).
        # Trade-off: one extra Postgres connection open/close per project per
        # history fetch (fetch_estimate_history/fetch_schedule_history each
        # manage their own connection) instead of one connection for the
        # whole tenant — acceptable here since --rebuild is a deliberate,
        # infrequent operator action, not the steady-state hot path.
        admins_by_env = fetch_tenant_admins(cfg.pg_dsn, cfg.tenantid)
        for row in iter_silver_rows(cfg.pg_dsn, cfg.tenantid, cfg.max_docs):
            if _shutdown:
                break
            seen += 1
            project_id = str(row["project_id"])
            source_env = row.get("source_env")
            key = (source_env, project_id)
            est_by_project = fetch_estimate_history(cfg.pg_dsn, cfg.tenantid, project_id)
            sch_by_project = fetch_schedule_history(cfg.pg_dsn, cfg.tenantid, project_id)
            pending.append(
                shape_doc(row, gold_ts,
                          est_by_project.get(key), sch_by_project.get(key),
                          admins_by_env.get(source_env))
            )
            if len(pending) >= cfg.batch_size:
                flush()
                log.info("progress | seen=%d upserted=%d", seen, stats.upserted)
        flush()
    except TRANSIENT_ERRORS as exc:
        mark_error(cfg.pg_dsn, _STEP_NAME, cfg.tenantid, exc)
        log.error("aborting after exhausted retries: %s", exc)
        return 3
    except psycopg2.OperationalError as exc:
        mark_error(cfg.pg_dsn, _STEP_NAME, cfg.tenantid, exc)
        log.error("transient PG error during history fetch: %s", exc)
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

    log.info("loader done | seen=%d upserted=%d modified=%d failed=%d batches=%d",
             seen, stats.upserted, stats.modified, stats.failed, stats.batches)
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
