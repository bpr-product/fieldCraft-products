"""Silver → Gold loader: bom_stock_gap (Eden Procurement agent).

Pre-computes the gap between BOM material requirements and current on-hand
stock per project+material so the Procurement agent does not join at query time
on every PO-drafting request. One document per (tenantId, projectId, materialUid)
— grain ``eden_bom_stock_gap``.

CORRECTED SOURCE MODEL (vs the EDEN requirements doc — see
docs/gold-layer/eden_data_contract_reconciliation.md sec 3):
  * silver.bom IS the line-item table (there is no silver.bom_item). It carries
    quantity / material_uid / material_name / unit / unit_cost / vendor_material_map_id.
  * bom.material_uid is TEXT (UUID); stock_item.material_uid is BIGINT and
    vendor_material_map.material_id is BIGINT — both reference material.id. So the
    join routes through silver.material: bom.material_uid=material.material_uid,
    then material.id = stock_item.material_uid / vendor_material_map.material_id.
  * silver.vendor_material_map has NO price column; preferred-vendor unit price is
    silver.material_cost.cost (joined via vendor_material_map_id), fallback bom.unit_cost.
  * silver.vendor has NO 'name' column; vendor name is company_name.

Upsert key: (tenantId, projectId, materialUid)
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
from gold.pipeline.cursor_store import read_cursor, write_cursor, mark_error, EPOCH  # noqa: E402
from gold.pipeline.lineage import lineage_fields, set_on_insert, parse_ts  # noqa: E402

COLLECTION = "bom_stock_gap"
FEATURE_NAME = "eden"
_STEP_NAME = "load_bom_stock_gap"
_contract_validator = ContractValidator("eden_bom_stock_gap")
TRANSIENT_ERRORS = (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError)
log = logging.getLogger("eden.bom_stock_gap_loader")


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
        p = argparse.ArgumentParser(description="Silver → Gold loader: bom_stock_gap")
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
            max_docs=ns.max_docs, rebuild=ns.rebuild,
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


# Grain: one row per (project, material_uid). BOM lines are summed per material;
# stock summed per material; preferred vendor/unit_price resolved lowest-cost.
_SILVER_QUERY = """
SELECT
    b._s_tenantid                          AS tenantid,
    b._s_env                               AS source_env,
    b.project_id                           AS project_id,
    -- silver.project has no plain "name" column; the source system
    -- pre-formats p.description as "{customer name}, {city}-{trade}-{orderNo}"
    -- (e.g. "vijay cus, Chennai-Roof Enhancement-708869") — the best available
    -- project name, preferred in shape_doc() over p.customer's raw JSON
    -- (firstName/lastName/customerUuid/...) and order_no. Raw customer object
    -- kept as a fallback under "customer".
    p.customer                             AS customer_raw,
    p.order_no                             AS order_no,
    p.description                          AS description,
    b.material_uid                         AS material_uid,
    MAX(m.id)                              AS material_id,
    COALESCE(MAX(b.material_name), MAX(m.name))  AS material_name,
    COALESCE(MAX(b.unit), MAX(m.unit))     AS unit,
    SUM(b.quantity)                        AS required_qty,
    COALESCE(MAX(st.stock_qty), 0)         AS stock_qty,
    MAX(pv.preferred_vendor_id)            AS preferred_vendor_id,
    MAX(pv.preferred_vendor_name)          AS preferred_vendor_name,
    COALESCE(MIN(pv.unit_price), MIN(b.unit_cost)) AS unit_price,
    BOOL_AND(b._s_is_deleted)              AS is_deleted,
    MAX(b._s_lsn)                          AS silver_lsn,
    MAX(b._s_silver_ts)                    AS bom_silver_ts,
    MAX(st.stock_silver_ts)                AS stock_silver_ts,
    MAX(p._s_silver_ts)                    AS project_silver_ts,

    -- Full detail — the individual BOM lines, full material master record,
    -- every stock_item row, and every vendor option (not just the auto-picked
    -- cheapest) backing this project+material aggregate. See shape_doc()/
    -- _shape_bom_line/_shape_stock_item/_shape_vendor_option. Not capped:
    -- verified live max 5 BOM lines and 2 vendor options per material. Each
    -- sub-lateral is keyed by (project_id, material.id) — identical for every
    -- b-row in the GROUP BY, so (ARRAY_AGG(...))[1] just picks the one value.
    (ARRAY_AGG(bld.detail))[1]             AS bom_lines,
    (ARRAY_AGG(mtd.detail))[1]             AS material_detail,
    (ARRAY_AGG(sid.detail))[1]             AS stock_items,
    (ARRAY_AGG(vod.detail))[1]             AS vendor_options

FROM silver.bom b
JOIN silver.project p
       ON p.id          = b.project_id
      AND p._s_tenantid = b._s_tenantid
      AND p._s_env      = b._s_env
      AND p._s_is_deleted = FALSE
-- bom.material_uid is TEXT (UUID); resolve it to the integer material.id, which
-- is what stock_item.material_uid (bigint, misnamed) and vendor_material_map.material_id
-- actually reference.
LEFT JOIN silver.material m
       ON m.material_uid = b.material_uid
      AND m._s_tenantid  = b._s_tenantid
      AND m._s_env       = b._s_env
      AND m._s_is_deleted = FALSE
LEFT JOIN LATERAL (
    SELECT SUM(si.quantity)       AS stock_qty,
           MAX(si._s_silver_ts)   AS stock_silver_ts
    FROM silver.stock_item si
    WHERE si._s_tenantid  = b._s_tenantid
      AND si._s_env       = b._s_env
      AND si.material_uid = m.id
      AND si._s_is_deleted = FALSE
) st ON TRUE
LEFT JOIN LATERAL (
    SELECT vmm.vendor_id      AS preferred_vendor_id,
           v.company_name     AS preferred_vendor_name,
           mc.cost            AS unit_price
    FROM silver.vendor_material_map vmm
    LEFT JOIN silver.material_cost mc
           ON mc.vendor_material_map_id = vmm.id
          AND mc._s_tenantid            = vmm._s_tenantid
          AND mc._s_env                 = vmm._s_env
          AND mc._s_is_deleted          = FALSE
    LEFT JOIN silver.vendor v
           ON v.id          = vmm.vendor_id
          AND v._s_tenantid = vmm._s_tenantid
          AND v._s_env      = vmm._s_env
          AND v._s_is_deleted = FALSE
    WHERE vmm._s_tenantid  = b._s_tenantid
      AND vmm._s_env       = b._s_env
      AND vmm.material_id   = m.id
      AND vmm._s_is_deleted = FALSE
    ORDER BY mc.cost ASC NULLS LAST
    LIMIT 1
) pv ON TRUE

-- Full detail: every BOM line for this project+material (raw, uncollapsed).
LEFT JOIN LATERAL (
    SELECT JSONB_AGG(
        JSONB_BUILD_OBJECT(
            'id',                     bl.id,
            'taskId',                 bl.task_id,
            'costCode',               bl.cost_code,
            'quantity',               bl.quantity,
            'unit',                   bl.unit,
            'unitCost',               bl.unit_cost,
            'totalCost',              bl.total_cost,
            'overHead',               bl.over_head,
            'profitMargin',           bl.profit_margin,
            'tax',                    bl.tax,
            'active',                 bl.active,
            'statusId',               bl.status_id,
            'sourceTypeId',           bl.source_type_id,
            'vendorMaterialMapId',    bl.vendor_material_map_id,
            'additionalInfo',         bl.additional_info,
            'version',                bl.version,
            'createdAt',              bl.created_at::text,
            'updatedAt',              bl.updated_at::text
        )
        ORDER BY bl.id ASC
    )                                                               AS detail
    FROM silver.bom bl
    WHERE bl._s_tenantid  = b._s_tenantid
      AND bl._s_env       = b._s_env
      AND bl.project_id   = b.project_id
      AND bl.material_uid = b.material_uid
      AND bl._s_is_deleted = FALSE
      AND bl.invalid       = FALSE
) bld ON TRUE

-- Full material master record (single row per material_uid).
LEFT JOIN LATERAL (
    SELECT JSONB_BUILD_OBJECT(
        'id',                  mm.id,
        'aliasName',           mm.alias_name,
        'grade',               mm.grade,
        'manufacturer',        mm.manufacturer,
        'status',              mm.status,
        'description',         mm.description,
        'codes',               mm.codes,
        'tags',                mm.tags,
        'baseUomId',           mm.base_uom_id,
        'grossUomId',          mm.gross_uom_id,
        'conversionFactor',    mm.conversion_factor,
        'purchaseToGrossFactor', mm.purchase_to_gross_factor,
        'grossToBaseFactor',   mm.gross_to_base_factor,
        'allowDecimal',        mm.allow_decimal,
        'serviceId',           mm.service_id,
        'effectiveStartdate',  mm.effective_startdate::text,
        'expiryDate',          mm.expiry_date::text,
        'createdBy',           mm.created_by,
        'createdAt',           mm.created_at::text,
        'updatedAt',           mm.updated_at::text,
        'version',             mm.version
    )                                                               AS detail
    FROM silver.material mm
    WHERE mm._s_tenantid  = b._s_tenantid
      AND mm._s_env       = b._s_env
      AND mm.material_uid = b.material_uid
      AND mm._s_is_deleted = FALSE
    LIMIT 1
) mtd ON TRUE

-- Full stock_item detail (not just summed quantity).
LEFT JOIN LATERAL (
    SELECT JSONB_AGG(
        JSONB_BUILD_OBJECT(
            'id',                 ssi.id,
            'stockUid',           ssi.stock_uid,
            'sku',                ssi.sku,
            'name',               ssi.name,
            'description',        ssi.description,
            'quantity',           ssi.quantity,
            'minimumStock',       ssi.minimum_stock,
            'maximumStock',       ssi.maximum_stock,
            'unitCost',           ssi.unit_cost,
            'buyingPrice',        ssi.buying_price,
            'buyingPricePerBaseUnit', ssi.buying_price_per_base_unit,
            'sellingPrice',       ssi.selling_price,
            'totalCost',          ssi.total_cost,
            'batchNumber',        ssi.batch_number,
            'serialNumber',       ssi.serial_number,
            'mpn',                ssi.mpn,
            'location',           ssi.location,
            'address',            ssi.address,
            'vendorId',           ssi.vendor_id,
            'manufacturerId',     ssi.manufacturer_id,
            'receiptId',          ssi.receipt_id,
            'receivedAt',         ssi.received_at::text,
            'receivedQuantity',   ssi.received_quantity,
            'expiryDate',         ssi.expiry_date::text,
            'active',             ssi.active,
            'invalid',            ssi.invalid,
            'locked',             ssi.locked,
            'purchaseable',       ssi.purchasable,
            'salable',            ssi.salable,
            'trackable',          ssi.trackable,
            'testable',           ssi.testable,
            'tags',               ssi.tags,
            'createdAt',          ssi.created_at::text,
            'updatedAt',          ssi.updated_at::text,
            'version',            ssi.version
        )
        ORDER BY ssi.id ASC
    )                                                               AS detail
    FROM silver.stock_item ssi
    WHERE ssi._s_tenantid  = b._s_tenantid
      AND ssi._s_env       = b._s_env
      AND ssi.material_uid = m.id
      AND ssi._s_is_deleted = FALSE
) sid ON TRUE

-- Full vendor option list — every vendor_material_map + vendor + material_cost
-- combination for this material, not just the auto-picked cheapest (pv above).
LEFT JOIN LATERAL (
    SELECT JSONB_AGG(
        JSONB_BUILD_OBJECT(
            'vendorMaterialMapId', vvmm.id,
            'vendorId',            vvmm.vendor_id,
            'vendorName',          vv.company_name,
            'vendorDisplayName',   vv.display_name,
            'vendorStatus',        vv.status,
            'vendorType',          vv.type,
            'vendorEmail',         vv.email,
            'vendorLeadTimeDays',  COALESCE(vvmm.lead_time, vv.lead_time),
            'inStock',             vvmm.in_stock,
            'cost',                vmc.cost,
            'costType',            vmc.cost_type,
            'costUnit',            vmc.unit,
            'costEffectiveStartdate', vmc.effective_startdate::text,
            'costEffectiveEnddate',   vmc.effective_enddate::text,
            -- Raw price-version history for this vendor+material combination
            -- (WI: material price history). silver.material_cost retains
            -- multiple effective-dated rows per vendor_material_map_id — see
            -- docs/gold-layer/historical_data_strategy.md §7. Collapsed/
            -- de-overlapped in shape_doc()/_shape_vendor_option() below;
            -- 'version' is unreliable here (stuck at 0 across distinct
            -- prices in live data) so ordering is by effective_startdate.
            'costHistory', (
                SELECT COALESCE(JSONB_AGG(
                    JSONB_BUILD_OBJECT(
                        'cost',               hmc.cost,
                        'costType',           hmc.cost_type,
                        'unit',               hmc.unit,
                        'grade',              hmc.grade,
                        'effectiveStartdate', hmc.effective_startdate::text,
                        'effectiveEnddate',   hmc.effective_enddate::text,
                        'version',            hmc.version,
                        'recordedAt',         hmc._s_silver_ts::text
                    )
                    ORDER BY hmc.effective_startdate ASC NULLS LAST, hmc.id ASC
                ), '[]'::jsonb)
                FROM silver.material_cost hmc
                WHERE hmc.vendor_material_map_id = vvmm.id
                  AND hmc._s_tenantid            = vvmm._s_tenantid
                  AND hmc._s_env                 = vvmm._s_env
                  AND hmc._s_is_deleted          = FALSE
            )
        )
        ORDER BY vmc.cost ASC NULLS LAST
    )                                                               AS detail
    FROM silver.vendor_material_map vvmm
    LEFT JOIN silver.material_cost vmc
           ON vmc.vendor_material_map_id = vvmm.id
          AND vmc._s_tenantid            = vvmm._s_tenantid
          AND vmc._s_env                 = vvmm._s_env
          AND vmc._s_is_deleted          = FALSE
    LEFT JOIN silver.vendor vv
           ON vv.id          = vvmm.vendor_id
          AND vv._s_tenantid = vvmm._s_tenantid
          AND vv._s_env      = vvmm._s_env
          AND vv._s_is_deleted = FALSE
    WHERE vvmm._s_tenantid  = b._s_tenantid
      AND vvmm._s_env       = b._s_env
      AND vvmm.material_id   = m.id
      AND vvmm._s_is_deleted = FALSE
) vod ON TRUE

WHERE b._s_tenantid   = %(tenantid)s
  AND b.invalid        = FALSE
  AND b._s_is_deleted  = FALSE
  AND b.material_uid IS NOT NULL
GROUP BY b._s_tenantid, b._s_env, b.project_id, p.customer, p.order_no, p.description, b.material_uid
ORDER BY b.project_id, b.material_uid
"""


def _scoped_query(project_id: str | None) -> str:
    if project_id is None:
        return _SILVER_QUERY
    return _SILVER_QUERY.replace(
        "WHERE b._s_tenantid   = %(tenantid)s",
        "WHERE b._s_tenantid   = %(tenantid)s\n  AND b.project_id = %(project_id)s",
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
            cur.execute(_scoped_query(project_id), params)
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


_NEWNESS_SQL = """
SELECT EXISTS (
    SELECT 1 FROM silver.bom        WHERE _s_tenantid = %(t)s AND _s_silver_ts > %(s)s
    UNION ALL
    SELECT 1 FROM silver.stock_item WHERE _s_tenantid = %(t)s AND _s_silver_ts > %(s)s
)
"""

# MIN of each _s_env's own MAX (across BOTH tables combined for that env), not
# the overall MAX — see the canonical loaders' get_max_silver_ts_per_env for
# why: a shared cursor advanced to the fastest-moving env's watermark would
# mask a later update in a slower-moving env still below that watermark.
_MAX_TS_SQL = """
SELECT MIN(env_max) FROM (
    SELECT _s_env, MAX(ts) AS env_max FROM (
        SELECT _s_env, _s_silver_ts AS ts FROM silver.bom        WHERE _s_tenantid = %(t)s
        UNION ALL
        SELECT _s_env, _s_silver_ts AS ts FROM silver.stock_item WHERE _s_tenantid = %(t)s
    ) rows
    GROUP BY _s_env
) per_env
"""


def _has_new_silver_rows(pg_dsn: str, tenantid: str, since: datetime) -> bool:
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(_NEWNESS_SQL, {"t": tenantid, "s": since})
        return bool(cur.fetchone()[0])


def _get_max_silver_ts(pg_dsn: str, tenantid: str) -> datetime:
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(_MAX_TS_SQL, {"t": tenantid})
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
    return None


def _float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _maybe_json(v):
    """Parse a free-text column that may hold JSON.

    Returns the parsed value when valid JSON, the original string when it
    isn't (never silently dropped), or None for empty/null input. Used for
    bom.additional_info / material.codes / material.tags / bom.tax.
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


def _shape_bom_line(raw: dict) -> dict:
    """Consolidate one raw silver.bom line into a full-detail Gold record."""
    return {
        "bomId":              str(raw["id"]),
        "taskId":              str(raw["taskId"]) if raw.get("taskId") is not None else None,
        "costCode":           raw.get("costCode"),
        "quantity":           _float(raw.get("quantity")),
        "unit":               raw.get("unit"),
        "unitCost":           _float(raw.get("unitCost")),
        "totalCost":          _float(raw.get("totalCost")),
        "overHead":           _float(raw.get("overHead")),
        "profitMargin":       _float(raw.get("profitMargin")),
        "tax":                _maybe_json(raw.get("tax")),
        "active":             bool(raw.get("active")) if raw.get("active") is not None else None,
        "statusId":           raw.get("statusId"),
        "sourceTypeId":       raw.get("sourceTypeId"),
        "vendorMaterialMapId": (str(raw["vendorMaterialMapId"])
                                 if raw.get("vendorMaterialMapId") is not None else None),
        "additionalInfo":     _maybe_json(raw.get("additionalInfo")),
        "version":            raw.get("version"),
        "createdAt":          parse_ts(raw.get("createdAt")),
        "updatedAt":          parse_ts(raw.get("updatedAt")),
    }


def _shape_material(raw: dict | None) -> dict | None:
    """Consolidate the material master record into a full-detail Gold object."""
    if not raw:
        return None
    shaped = dict(raw)
    shaped["codes"] = _maybe_json(raw.get("codes"))
    shaped["tags"] = _maybe_json(raw.get("tags"))
    shaped["effectiveStartdate"] = parse_ts(raw.get("effectiveStartdate"))
    shaped["expiryDate"] = parse_ts(raw.get("expiryDate"))
    shaped["createdAt"] = parse_ts(raw.get("createdAt"))
    shaped["updatedAt"] = parse_ts(raw.get("updatedAt"))
    return shaped


def _shape_stock_item(raw: dict) -> dict:
    """Consolidate one raw silver.stock_item row into a full-detail Gold record."""
    shaped = dict(raw)
    shaped["tags"] = _maybe_json(raw.get("tags"))
    shaped["receivedAt"] = parse_ts(raw.get("receivedAt"))
    shaped["receivedDate"] = parse_ts(raw.get("receivedDate"))
    shaped["expiryDate"] = parse_ts(raw.get("expiryDate"))
    shaped["createdAt"] = parse_ts(raw.get("createdAt"))
    shaped["updatedAt"] = parse_ts(raw.get("updatedAt"))
    return shaped


# ---------------------------------------------------------------------------
# Material price history — silver.material_cost retains effective-dated
# versions per vendor_material_map_id (verified live: 6 materials, 2-3
# versions each, e.g. $199 -> $217.21). Collapsed here per the DQ landmines
# flagged in docs/gold-layer/historical_data_strategy.md §7:
#   * overlapping effective windows (two rows can share effective_startdate)
#     -> de-duplicate on (cost, effectiveStartdate) before collapsing.
#   * sentinel end-year 3026 for "open-ended" -> normalised to null so
#     Eden doesn't read it as a literal expiry date.
#   * version is stuck at 0 across distinct prices -> never used for
#     ordering; effective_startdate is the only reliable timeline axis.
# ---------------------------------------------------------------------------
_MAX_PRICE_HISTORY_ENTRIES = 25
_OPEN_ENDED_SENTINEL_YEAR = 3000  # any effective_enddate year >= this is "open"


def _cents(v) -> float | None:
    f = _float(v)
    return round(f, 2) if f is not None else None


def _price_delta(curr, prev) -> float | None:
    c, p = _float(curr), _float(prev)
    if c is None or p is None:
        return None
    return round(c - p, 2) + 0.0


def _normalise_end_date(v: str | None) -> str | None:
    """Treat the material_cost sentinel end-year (e.g. 3026-...) as open-ended."""
    if not v:
        return None
    try:
        year = int(v[:4])
    except (TypeError, ValueError):
        return v
    return None if year >= _OPEN_ENDED_SENTINEL_YEAR else v


def _dedupe_price_rows(rows: list[dict]) -> list[dict]:
    """Drop exact duplicate (cost, effectiveStartdate) rows — overlapping
    effective windows in live data otherwise double-count the same price
    point. Input is already ordered by effective_startdate ascending."""
    deduped: list[dict] = []
    seen: set[tuple] = set()
    for row in rows:
        key = (_cents(row.get("cost")), row.get("effectiveStartdate"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _price_history_entry(row: dict, prev: dict | None, is_current: bool) -> dict:
    return {
        "cost":               _float(row.get("cost")),
        "costType":           row.get("costType"),
        "unit":               row.get("unit"),
        "grade":              row.get("grade"),
        "effectiveFrom":      parse_ts(row.get("effectiveStartdate")),
        "effectiveTo":        parse_ts(_normalise_end_date(row.get("effectiveEnddate"))),
        "recordedAt":         parse_ts(row.get("recordedAt")),
        "delta":              _price_delta(row.get("cost"), prev.get("cost")) if prev else None,
        "isCurrent":          is_current,
    }


def _is_price_meaningful(row: dict, prev: dict) -> bool:
    return (_cents(row.get("cost")) != _cents(prev.get("cost"))
            or (row.get("costType") or None) != (prev.get("costType") or None))


def collapse_price_history(rows: list[dict]) -> tuple[list[dict], dict | None]:
    """Collapse raw material_cost rows (ordered by effective_startdate
    ascending) into a meaningful price-change timeline + summary. Pure
    function — no I/O. Mirrors collapse_estimate_history's shape/rules."""
    rows = _dedupe_price_rows(rows)
    if not rows:
        return [], None

    kept: list[dict] = [rows[0]]
    change_count = 0
    for row in rows[1:]:
        if _is_price_meaningful(row, kept[-1]):
            kept.append(row)
            change_count += 1
    if kept[-1] is not rows[-1]:
        kept.append(rows[-1])

    baseline, current = rows[0], rows[-1]
    current_id = id(current)

    history: list[dict] = []
    prev: dict | None = None
    for row in kept:
        history.append(_price_history_entry(row, prev, is_current=id(row) == current_id))
        prev = row

    if len(history) > _MAX_PRICE_HISTORY_ENTRIES:
        history = [history[0]] + history[-(_MAX_PRICE_HISTORY_ENTRIES - 1):]

    summary = {
        "currentCost":       _float(current.get("cost")),
        "baselineCost":      _float(baseline.get("cost")),
        "rawVersionCount":   len(rows),
        "priceChangeCount":  change_count,
        "totalCostDelta":    _price_delta(current.get("cost"), baseline.get("cost")),
        "firstEffectiveAt":  baseline.get("effectiveStartdate"),
        "lastChangedAt":     current.get("effectiveStartdate"),
    }
    return history, summary


def _shape_vendor_option(raw: dict) -> dict:
    """Consolidate one vendor+material_cost combination into a full-detail
    Gold record — every vendor option for this material, not just the one
    auto-picked as cheapest (that stays in the top-level preferredVendor*/
    unitPrice fields for backward compatibility). Embeds the collapsed
    ``priceHistory[]``/``priceSummary`` for this specific vendor option."""
    shaped = dict(raw)
    shaped["costEffectiveStartdate"] = parse_ts(raw.get("costEffectiveStartdate"))
    shaped["costEffectiveEnddate"] = parse_ts(raw.get("costEffectiveEnddate"))
    raw_history = shaped.pop("costHistory", None) or []
    history, summary = collapse_price_history(raw_history)
    shaped["priceHistory"] = history
    shaped["priceSummary"] = summary
    return shaped


def _customer_display_name(customer: dict | None) -> str | None:
    """Build a display name from the parsed silver.project.customer object."""
    if not customer:
        return None
    parts = [customer.get("firstName"), customer.get("lastName")]
    name = " ".join(p.strip() for p in parts if p and str(p).strip())
    return name or None


def shape_doc(row: dict, gold_ts: datetime) -> dict:
    watermarks = {
        "project":    _to_ts(row.get("project_silver_ts")),
        "bom":        _to_ts(row.get("bom_silver_ts")),
        "stock_item": _to_ts(row.get("stock_silver_ts")),
    }
    valid_wm = [ts for ts in watermarks.values() if ts is not None]
    lag_ms = int((gold_ts - min(valid_wm)).total_seconds() * 1000) if valid_wm else 0

    required = _float(row.get("required_qty"))
    stock = _float(row.get("stock_qty"))
    gap = None
    if required is not None or stock is not None:
        gap = (required or 0.0) - (stock or 0.0)

    customer = _maybe_json(row.get("customer_raw"))
    customer = customer if isinstance(customer, dict) else None

    doc = {
        "tenantId":            row["tenantid"],
        "projectId":           str(row["project_id"]),
        # Contract documents this as "Denormalized project name" — silver.project
        # has no name column, but description is pre-formatted by the source
        # system as "{customer name}, {city}-{trade}-{orderNo}" (e.g. "vijay
        # cus, Chennai-Roof Enhancement-708869") — the richest available label,
        # same preference order as load_project_health.py's projectName. The
        # full customer record lives in gold_canonical.customer (keyed by
        # customerUid) — not duplicated here; bom_stock_gap has no actual use
        # for anything beyond the display name (removed 2026-07-20).
        "projectName":         (row.get("description")
                                  or _customer_display_name(customer)
                                  or row.get("order_no")),
        "orderNo":             row.get("order_no"),
        "materialUid":         str(row["material_uid"]),
        "materialId":          _int(row.get("material_id")),
        "materialName":        row.get("material_name"),
        "unit":                row.get("unit"),
        "requiredQty":         required,
        "stockQty":            stock,
        "gapQty":              gap,
        "preferredVendorId":   _int(row.get("preferred_vendor_id")),
        "preferredVendorName": row.get("preferred_vendor_name"),
        "unitPrice":           _float(row.get("unit_price")),
        # Full detail (consolidated — see _shape_bom_line/_shape_material/
        # _shape_stock_item/_shape_vendor_option). Not capped: verified live
        # max 5 BOM lines and 2 vendor options per material.
        "bomLines":            [_shape_bom_line(b) for b in (row.get("bom_lines") or [])],
        "material":            _shape_material(row.get("material_detail")),
        "stockItems":          [_shape_stock_item(s) for s in (row.get("stock_items") or [])],
        "vendorOptions":       [_shape_vendor_option(v) for v in (row.get("vendor_options") or [])],
        "_grain":              "eden_bom_stock_gap",
        "_schemaVersion":      1,
        "_silverLsn":          int(row.get("silver_lsn") or 0),
        "isDeleted":           bool(row.get("is_deleted") or False),
        "deletedAt":           row.get("deleted_at"),
        "dataAsOf":            gold_ts,
        "_silverWatermarks":   watermarks,
        "lafieldcraft":               lag_ms,
    }
    doc.update(lineage_fields(FEATURE_NAME, gold_ts, iso=False,
                              source_updated_at=watermarks.get("bom"),
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
            stats.failed += len(exc.details.get("writeErrors", []) if exc.details else [])
            stats.batches += 1
            return
        except TRANSIENT_ERRORS:
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


def _upsert_op(d: dict, gold_ts: datetime) -> UpdateOne:
    return UpdateOne(
        {"tenantId": d["tenantId"], "projectId": d["projectId"],
         "materialUid": d["materialUid"], "sourceEnv": d.get("sourceEnv"),
         "$or": [{"_silverLsn": {"$exists": False}},
                 {"_silverLsn": {"$lte": d.get("_silverLsn", 0)}}]},
        {"$set": d, "$setOnInsert": set_on_insert(gold_ts, iso=False)},
        upsert=True,
    )


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
                             retryWrites=False, appname="eden-bom-stock-gap")
        coll = client[cfg.db_name][COLLECTION]

    pending: list[dict] = []
    seen = 0

    def flush() -> None:
        if not pending:
            return
        ops = [_upsert_op(d, gold_ts) for d in pending]
        for d in pending:
            _contract_validator.validate(d, COLLECTION)
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
