"""Silver → Gold loader: invoice_summary (Eden AI Assistant).

Reads silver.project (order_no), silver.invoice, silver.payment_receives
(+ payment_receives_entries), and silver.payment_expense to produce one
document per project with invoice totals, aging breakdown, upcoming
invoices, last payment details, and full per-record detail for
invoices/payments/expenses (see _shape_invoice/_shape_payment/_shape_expense).

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
from gold.pipeline.lineage import lineage_fields, set_on_insert, parse_ts  # noqa: E402

COLLECTION = "invoice_summary"
FEATURE_NAME = "eden"
_STEP_NAME = "load_invoice_summary"
_contract_validator = ContractValidator("eden_invoice_summary")
TRANSIENT_ERRORS = (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError,
                    CircuitOpenError)
log = logging.getLogger("eden.invoice_summary_loader")


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
        p = argparse.ArgumentParser(description="Silver → Gold loader: invoice_summary")
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
        p.add_argument("--rebuild",       action="store_true",
                       help="Force full re-aggregate from current Silver (ignore the "
                            "newness check) and do NOT advance the cursor.")
        p.add_argument("--max-docs",      type=int, default=int(os.environ.get("MAX_DOCS", "0")))
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


# One row per project — aggregates all invoices and payments.
#
# Payment linkage fix (verified live): the original ``pay`` join matched
# payment_receives to invoice via ``reference_type = 'Invoice'`` — but in real
# data reference_type is NEVER 'Invoice' (223/225 rows are NULL, 2 are
# 'Documents'), and payment_receives.project_id is NULL on every row. That
# join has therefore never matched anything; totalReceived/lastPayment have
# always been 0/null. The real, working link is silver.payment_receives_entries
# — it carries a populated project_id AND payment_receives_id (→
# payment_receives.id) AND reference_no (→ invoice.invoice_no); verified all 6
# live rows resolve to a matching invoice with an agreeing project_id. Payments
# are now joined through this table instead.
_SILVER_QUERY = """
SELECT
    p._s_tenantid                                                               AS tenantid,
    p._s_env                                                                     AS source_env,
    p._s_is_deleted                                                              AS is_deleted,
    p._s_deleted_at::text                                                        AS deleted_at,
    p._s_lsn                                                                     AS silver_lsn,
    p._s_silver_ts                                                               AS project_silver_ts,
    p.id                                                AS project_id,
    p.order_no                                          AS order_no,

    -- Invoice aggregates
    COALESCE(inv.total_billed, 0)                       AS total_billed,
    inv.overdue_invoices,
    inv.upcoming_invoices,
    inv.invoice_silver_ts,

    -- Payment aggregates (now via payment_receives_entries — see note above)
    COALESCE(pay.total_received, 0)                     AS total_received,
    pay.last_payment_amount,
    pay.last_payment_date,
    pay.last_payment_method,
    pay.payment_silver_ts,

    -- Full detail — every column, consolidated (see shape_doc/_shape_invoice/
    -- _shape_payment/_shape_expense). Not capped: verified live max well under
    -- Mongo's document size limit (9 invoices / 225 payments / 61 expenses
    -- TOTAL across the entire tenant today).
    invfull.invoices                                    AS all_invoices,
    payfull.payments                                     AS all_payments,
    expfull.expenses                                     AS all_expenses

FROM silver.project p

LEFT JOIN LATERAL (
    SELECT
        SUM(invoiced_amount)                                        AS total_billed,
        -- NOT the outstanding total — status-based only (any invoice whose
        -- status hasn't flipped to 'PAID' counts as fully outstanding here,
        -- ignoring reconciled partial payments). Verified live 2026-08-11,
        -- tenant tn88351775 project 1239, invoice INV-20260805-1024: status
        -- stayed 'Un Paid' after 3 Reconciled payment_receives_entries rows
        -- totaling $300 against a $16,676.059 invoice, so this column came
        -- out equal to total_billed instead of $16,376.059. Renamed to make
        -- clear it must not be used as the Gold totalOutstanding value —
        -- shape_doc() computes the real figure as total_billed - total_received.
        SUM(invoiced_amount) FILTER (WHERE status != 'PAID')       AS unpaid_status_total,
        JSONB_AGG(
            JSONB_BUILD_OBJECT(
                'invoiceNo',      invoice_no,
                'invoicedAmount', invoiced_amount,
                'dueDate',        due_date::text,
                'daysOverdue',    EXTRACT(DAY FROM NOW() - due_date)::int,
                'status',         status
            )
            ORDER BY due_date ASC
        ) FILTER (
            WHERE status != 'PAID'
              AND due_date < NOW()
        )                                                           AS overdue_invoices,
        JSONB_AGG(
            JSONB_BUILD_OBJECT(
                'invoiceNo',      invoice_no,
                'invoicedAmount', invoiced_amount,
                'dueDate',        due_date::text,
                'status',         status
            )
            ORDER BY due_date ASC
        ) FILTER (
            WHERE status NOT IN ('PAID','VOID')
              AND due_date >= NOW()
        )                                                           AS upcoming_invoices,
        MAX(_s_silver_ts)                                           AS invoice_silver_ts
    FROM silver.invoice
    WHERE _s_tenantid  = p._s_tenantid
      AND _s_env       = p._s_env
      AND project_id   = p.id
      AND _s_is_deleted = FALSE
      AND invalid       = FALSE
) inv ON TRUE

LEFT JOIN LATERAL (
    SELECT
        SUM(pre.payment_amount)                                                     AS total_received,
        (ARRAY_AGG(pre.payment_amount ORDER BY pre.created_at DESC NULLS LAST))[1]   AS last_payment_amount,
        (ARRAY_AGG(pr.received_time   ORDER BY pre.created_at DESC NULLS LAST))[1]   AS last_payment_date,
        (ARRAY_AGG(pr.pay_method      ORDER BY pre.created_at DESC NULLS LAST))[1]   AS last_payment_method,
        MAX(pre._s_silver_ts)                                                       AS payment_silver_ts
    FROM silver.payment_receives_entries pre
    JOIN silver.payment_receives pr
      ON pr.id           = pre.payment_receives_id
     AND pr._s_tenantid  = pre._s_tenantid
     AND pr._s_env       = pre._s_env
     AND pr._s_is_deleted = FALSE
    WHERE pre._s_tenantid  = p._s_tenantid
      AND pre._s_env       = p._s_env
      AND pre.project_id   = p.id
      AND pre._s_is_deleted = FALSE
) pay ON TRUE

LEFT JOIN LATERAL (
    SELECT JSONB_AGG(
        JSONB_BUILD_OBJECT(
            'id',               ai.id,
            'invoice_no',       ai.invoice_no,
            'type',             ai.type,
            'status',           ai.status,
            'total_amount',     ai.total_amount,
            'invoiced_amount',  ai.invoiced_amount,
            'tax_amount',       ai.tax_amount,
            'balance',          ai.balance,
            'due_date',         ai.due_date::text,
            'issue_at',         ai.issue_at::text,
            'term',             ai.term,
            'invoice_message',  ai.invoice_message,
            'customer_uid',     ai.customer_uid,
            'document_id',      ai.document_id,
            'items',            ai.items,
            'codes',            ai.codes,
            'tax_info',         ai.tax_info,
            'additional_info',  ai.additional_info,
            'reminder_status',  ai.reminder_status,
            'reminder_count',   ai.reminder_count,
            'reminder_at',      ai.reminder_at::text,
            'invalid',          ai.invalid,
            'created_at',       ai.created_at::text,
            'created_by',       ai.created_by,
            'updated_at',       ai.updated_at::text,
            'version',          ai.version
        )
        ORDER BY ai.due_date ASC NULLS LAST, ai.id ASC
    )                                                               AS invoices
    FROM silver.invoice ai
    WHERE ai._s_tenantid  = p._s_tenantid
      AND ai._s_env       = p._s_env
      AND ai.project_id   = p.id
      AND ai._s_is_deleted = FALSE
      AND ai.invalid       = FALSE
) invfull ON TRUE

-- Full payment detail — one entry per payment_receives_entries row (the
-- actual applied-amount-to-this-invoice breakdown), joined to its parent
-- payment_receives record for the receive-level fields.
LEFT JOIN LATERAL (
    SELECT JSONB_AGG(
        JSONB_BUILD_OBJECT(
            'entry_id',              ape.id,
            'payment_receives_id',   ape.payment_receives_id,
            'invoice_no',            ape.reference_no,
            'payment_amount',        ape.payment_amount,
            'interest_paid',         ape.interest_paid,
            'late_fee_paid',         ape.late_fee_paid,
            'entry_codes',           ape.codes,
            'entry_created_at',      ape.created_at::text,
            'entry_updated_at',      ape.updated_at::text,
            'amount',                apr.amount,
            'invoiced_amount',       apr.invoice_amount,
            'suspense_amount',       apr.suspense_amount,
            'pay_method',            apr.pay_method,
            'received_time',         apr.received_time::text,
            'status',                apr.status,
            'description',           apr.description,
            'currency_code',         apr.currency_code,
            'exchange_rate',         apr.exchange_rate,
            'fieldcraft_transaction_id',    apr.fieldcraft_transaction_id,
            'payment_transaction_id', apr.payment_transaction_id,
            'payment_receipt_id',    apr.payment_receipt_id,
            'payment_reference_no',  apr.payment_reference_no,
            'chart_of_account_id',   apr.chart_of_account_id,
            'customer_uid',          apr.customer_uid,
            'codes',                 apr.codes,
            'additional_info',       apr.additional_info,
            'updated_at',            apr.updated_at::text
        )
        ORDER BY ape.created_at DESC NULLS LAST, ape.id ASC
    )                                                               AS payments
    FROM silver.payment_receives_entries ape
    JOIN silver.payment_receives apr
      ON apr.id           = ape.payment_receives_id
     AND apr._s_tenantid  = ape._s_tenantid
     AND apr._s_env       = ape._s_env
     AND apr._s_is_deleted = FALSE
    WHERE ape._s_tenantid  = p._s_tenantid
      AND ape._s_env       = p._s_env
      AND ape.project_id   = p.id
      AND ape._s_is_deleted = FALSE
) payfull ON TRUE

-- Full expense detail — project costs paid out (labor/salary, vendor bills),
-- distinct from invoices/payments (money billed/received from the customer).
LEFT JOIN LATERAL (
    SELECT JSONB_AGG(
        JSONB_BUILD_OBJECT(
            'id',                aex.id,
            'amount',            aex.amount,
            'status',            aex.status,
            'billable',          aex.billable,
            'description',       aex.description,
            'reference_id',      aex.reference_id,
            'reference_no',      aex.reference_no,
            'reference_type',    aex.reference_type,
            'payee_id',          aex.payee_id,
            'payee_account_no',  aex.payee_account_no,
            'user_id',           aex.user_id,
            'chart_of_account_id',   aex.chart_of_account_id,
            'chart_of_account_name', coa.name,
            'currency_code',     aex.currency_code,
            'exchange_rate',     aex.exchange_rate,
            'codes',             aex.codes,
            'created_at',        aex.created_at::text,
            'updated_at',        aex.updated_at::text
        )
        ORDER BY aex.created_at DESC NULLS LAST, aex.id ASC
    )                                                               AS expenses
    FROM silver.payment_expense aex
    LEFT JOIN silver.chart_of_account coa
           ON coa.id           = aex.chart_of_account_id
          AND coa._s_tenantid  = aex._s_tenantid
          AND coa._s_env       = aex._s_env
          AND coa._s_is_deleted = FALSE
    WHERE aex._s_tenantid  = p._s_tenantid
      AND aex._s_env       = p._s_env
      AND aex.project_id   = p.id
      AND aex._s_is_deleted = FALSE
) expfull ON TRUE

WHERE p._s_tenantid  = %(tenantid)s
  AND p.invalid       = FALSE
ORDER BY p.id
"""


def _scoped_query(project_id: str | None) -> str:
    """Return the Silver query, optionally scoped to a single project.

    When project_id is provided, splice an ``AND p.id = %(project_id)s``
    predicate into the outer WHERE clause so the same tested SQL runs for
    one project — used by the real-time streaming job.
    """
    if project_id is None:
        return _SILVER_QUERY
    return _SILVER_QUERY.replace(
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


def _float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _maybe_json(v):
    """Parse a free-text column that may hold JSON.

    Returns the parsed value when it is valid JSON, the original string when
    it isn't (never silently dropped), or None for empty/null input. Used for
    invoice/payment/expense TEXT columns (items, codes, tax_info,
    additional_info) whose content isn't guaranteed to be JSON.
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


def _shape_invoice(raw: dict) -> dict:
    """Consolidate one silver.invoice row into a full-detail Gold record."""
    return {
        "invoiceId":     str(raw["id"]),
        "invoiceNo":     raw.get("invoice_no"),
        "type":          raw.get("type"),
        "status":        raw.get("status"),
        "totalAmount":   _float(raw.get("total_amount")),
        "invoicedAmount": _float(raw.get("invoiced_amount")),
        "taxAmount":     _float(raw.get("tax_amount")),
        "balance":       _float(raw.get("balance")),
        "dueDate":       parse_ts(raw.get("due_date")),
        "issuedAt":      parse_ts(raw.get("issue_at")),
        "term":          raw.get("term"),
        "message":       raw.get("invoice_message"),
        "customerUid":   raw.get("customer_uid"),
        "documentId":    str(raw["document_id"]) if raw.get("document_id") is not None else None,
        "lineItems":     _maybe_json(raw.get("items")),
        "codes":         _maybe_json(raw.get("codes")),
        "taxInfo":       _maybe_json(raw.get("tax_info")),
        "additionalInfo": _maybe_json(raw.get("additional_info")),
        "reminder": {
            "status": raw.get("reminder_status"),
            "count":  raw.get("reminder_count"),
            "at":     parse_ts(raw.get("reminder_at")),
        },
        "invalid": bool(raw.get("invalid")),
        "audit": {
            "createdAt": parse_ts(raw.get("created_at")),
            "createdBy": raw.get("created_by"),
            "updatedAt": parse_ts(raw.get("updated_at")),
            "version":   raw.get("version"),
        },
    }


def _shape_payment(raw: dict) -> dict:
    """Consolidate one payment_receives_entries row (+ its parent
    payment_receives record) into a full-detail Gold record.

    A legacy, un-underscored ``apr.paymentreferenceno`` column was assumed
    to exist alongside ``payment_reference_no`` but does not (verified
    2026-07-23 against tn59395620 — raised ``UndefinedColumn``).

    ``apr.invoiced_amount`` was also assumed to be the real column name,
    but silver.payment_receives only ever had ``invoice_amount`` — the
    query queried the wrong (non-existent) name until fixed 2026-08-11
    (confirmed live against tn88351775: ``UndefinedColumn``, "Perhaps you
    meant apr.invoice_amount"), which failed load_invoice_summary on every
    run. The SQL now selects ``apr.invoice_amount`` under the JSON key
    ``invoiced_amount`` (the Gold field name), so no downstream/consumer
    change is needed. The ``raw.get("paymentreferenceno")`` /
    ``raw.get("invoice_amount")`` fallbacks below are kept as harmless
    no-ops in case a differently provisioned tenant DB still has the
    legacy names.
    """
    return {
        "paymentId":        str(raw["payment_receives_id"]),
        "entryId":          str(raw["entry_id"]),
        "invoiceNo":        raw.get("invoice_no"),
        "paymentAmount":    _float(raw.get("payment_amount")),
        "interestPaid":     _float(raw.get("interest_paid")),
        "lateFeePaid":      _float(raw.get("late_fee_paid")),
        "amount":           _float(raw.get("amount")),
        "invoicedAmount":   _float(raw.get("invoiced_amount")
                                    if raw.get("invoiced_amount") is not None
                                    else raw.get("invoice_amount")),
        "suspenseAmount":   _float(raw.get("suspense_amount")),
        "payMethod":        raw.get("pay_method"),
        "receivedDate":     parse_ts(raw.get("received_time")),
        "status":           raw.get("status"),
        "description":      raw.get("description"),
        "customerUid":      raw.get("customer_uid"),
        "chartOfAccountId": (str(raw["chart_of_account_id"])
                              if raw.get("chart_of_account_id") is not None else None),
        "currency": {
            "code":         raw.get("currency_code"),
            "exchangeRate": raw.get("exchange_rate"),
        },
        "transaction": {
            "fieldcraftTransactionId":     raw.get("fieldcraft_transaction_id"),
            "paymentTransactionId": raw.get("payment_transaction_id"),
            "paymentReceiptId":     raw.get("payment_receipt_id"),
            "paymentReferenceNo":   (raw.get("payment_reference_no")
                                      or raw.get("paymentreferenceno")),
        },
        "codes":          _maybe_json(raw.get("codes") or raw.get("entry_codes")),
        "additionalInfo": _maybe_json(raw.get("additional_info")),
        "audit": {
            "createdAt": parse_ts(raw.get("entry_created_at")),
            "updatedAt": parse_ts(raw.get("entry_updated_at") or raw.get("updated_at")),
        },
    }


def _mask_account_no(acct) -> str | None:
    """Mask a bank/payee account number to its last 4 digits only.

    SECURITY (finding 30, PR #7999): payee_account_no was previously passed
    through unmasked into gold_eden.invoice_summary, a collection EDEN's
    Reporting/Insight chat agents query directly for billing answers — an
    unredacted account number is one hop from surfacing in an LLM-generated
    response. Mirrors BPE's I.1/A.1 last-4-only redaction convention.
    """
    if not acct:
        return acct
    s = str(acct)
    return ("*" * max(0, len(s) - 4)) + s[-4:]


def _shape_expense(raw: dict) -> dict:
    """Consolidate one silver.payment_expense row into a full-detail Gold
    record — project costs paid OUT (labor/salary, vendor bills), distinct
    from invoices/payments (money billed/received from the customer)."""
    return {
        "expenseId":  str(raw["id"]),
        "amount":     _float(raw.get("amount")),
        "status":     raw.get("status"),
        "billable":   bool(raw.get("billable")) if raw.get("billable") is not None else None,
        "description": raw.get("description"),
        "reference": {
            "referenceId":   (str(raw["reference_id"]) if raw.get("reference_id") is not None else None),
            "referenceNo":   raw.get("reference_no"),
            "referenceType": raw.get("reference_type"),
        },
        "payee": {
            "payeeId":        raw.get("payee_id"),
            "payeeAccountNo": _mask_account_no(raw.get("payee_account_no")),
            "userId":         raw.get("user_id"),
        },
        "chartOfAccountId": (str(raw["chart_of_account_id"])
                              if raw.get("chart_of_account_id") is not None else None),
        "chartOfAccountName": raw.get("chart_of_account_name"),
        "currency": {
            "code":         raw.get("currency_code"),
            "exchangeRate": raw.get("exchange_rate"),
        },
        "codes": _maybe_json(raw.get("codes")),
        "audit": {
            "createdAt": parse_ts(raw.get("created_at")),
            "updatedAt": parse_ts(raw.get("updated_at")),
        },
    }


def _shape_due_date_item(item: dict) -> dict:
    """overdueInvoices/upcomingInvoices entries built directly in SQL
    JSONB_AGG — parse_ts() the dueDate string back into a native datetime."""
    shaped = dict(item)
    shaped["dueDate"] = parse_ts(shaped.get("dueDate"))
    return shaped


def shape_doc(row: dict, gold_ts: datetime) -> dict:
    last_payment = None
    if row.get("last_payment_amount") is not None:
        last_payment = {
            "amount":       _float(row["last_payment_amount"]),
            "receivedDate": parse_ts(row.get("last_payment_date")),
            "payMethod":    row.get("last_payment_method"),
        }

    watermarks = {
        "project":          _to_ts(row.get("project_silver_ts")),
        "invoice":          _to_ts(row.get("invoice_silver_ts")),
        "payment_receives": _to_ts(row.get("payment_silver_ts")),
    }
    valid_wm = [ts for ts in watermarks.values() if ts is not None]
    lag_ms = int((gold_ts - min(valid_wm)).total_seconds() * 1000) if valid_wm else 0

    doc = {
        "tenantId":         row["tenantid"],
        "projectId":        str(row["project_id"]),
        "orderNo":          row.get("order_no"),
        "_grain":           "eden_invoice_summary",
        "_schemaVersion":   1,
        "_silverLsn":       int(row.get("silver_lsn") or 0),
        "isDeleted":                bool(row.get("is_deleted") or False),
        "deletedAt":                parse_ts(row.get("deleted_at")),
        "dataAsOf":         gold_ts,
        "totalBilled":      _float(row.get("total_billed")),
        "totalReceived":    _float(row.get("total_received")),
        # billed minus received, floored at 0 — NOT the status-filtered SQL
        # aggregate (see the unpaid_status_total comment in _SILVER_QUERY):
        # invoice.status doesn't reliably flip off 'Un Paid'/'PAID' when a
        # partial payment is reconciled, so a status filter alone silently
        # ignores real payments already applied against the invoice.
        "totalOutstanding": max(
            (_float(row.get("total_billed")) or 0.0) - (_float(row.get("total_received")) or 0.0),
            0.0,
        ),
        "overdueInvoices":  [_shape_due_date_item(i) for i in (row.get("overdue_invoices") or [])],
        "upcomingInvoices": [_shape_due_date_item(i) for i in (row.get("upcoming_invoices") or [])],
        "lastPayment":      last_payment,
        # Full per-record detail (consolidated — see _shape_invoice/
        # _shape_payment/_shape_expense). Not capped: verified live total well
        # under Mongo's document size limit.
        "invoices":         [_shape_invoice(i) for i in (row.get("all_invoices") or [])],
        "payments":         [_shape_payment(p) for p in (row.get("all_payments") or [])],
        "expenses":         [_shape_expense(e) for e in (row.get("all_expenses") or [])],
        "_silverWatermarks": watermarks,
        "lafieldcraft":            lag_ms,
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
            log.warning("transient error attempt %d/%d: %s — retry %.1fs",
                        attempt, cfg.max_retries, exc, delay)
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
                             retryWrites=False, appname="eden-invoice-summary")
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
                log.info("progress | seen=%d upserted=%d", seen, stats.upserted)
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
