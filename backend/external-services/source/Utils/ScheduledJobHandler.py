"""Scheduled job helper for QuickBooks sync.

Goal
----
Keep the API thin: "start a job" and "run a job" lives here.

Design
------
- The API endpoint creates (or reuses) a RUNNING scheduled_job row.
- The actual sync work runs in a background task.
- The final summary is persisted into scheduled_job.result.

This module is intentionally generic and minimal.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional
from uuid import uuid4
import os

from source.dal.ScheduledJobRepository import ScheduledJobRepository
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.dal.QuickBooksRepository import QboBlobResultStore
from source.Utils import constants as const
from source.Utils.QuickBooksLogging import log_cdc_debug

logger = logging.getLogger(__name__)



JOB_TYPES: Dict[str, str] = {
    "customers": "QBO_CUSTOMER_SYNC",
    "customers_full": "QBO_CUSTOMER_FULL_SYNC",
    "payments": "QBO_PAYMENT_SYNC",
    "vendors": "QBO_VENDOR_SYNC",
    "vendors_full": "QBO_VENDOR_FULL_SYNC",
    "accounts": "QBO_ACCOUNT_SYNC",
    "bills": "QBO_BILL_SYNC",
    "bills_full": "QBO_BILL_FULL_SYNC",
    "bill_payments": "QBO_BILL_PAYMENT_SYNC",
    "projects": "QBO_PROJECT_SYNC",
    "employees": "QBO_EMPLOYEE_SYNC",
    "services": "QBO_SERVICE_ROLE_SYNC",
    "timelogs": "QBO_TIMELOG_SYNC",
    "invoices": "QBO_INVOICE_SYNC",
}

MODULE_WATERMARK_JOB_TYPES: Dict[str, tuple[str, ...]] = {
    "customers": (JOB_TYPES["customers"], JOB_TYPES["customers_full"]),
    "vendors": (JOB_TYPES["vendors"], JOB_TYPES["vendors_full"]),
    "bills": (JOB_TYPES["bills"], JOB_TYPES["bills_full"]),
}

MODULE_LAST_SYNC_KEYS: Dict[str, str] = {
    "customers": "lastCustomerSyncAt",
    "vendors": "lastVendorSyncAt",
    "payments": "lastPaymentSyncAt",
    "accounts": "lastAccountSyncAt",
    "bills": "lastBillSyncAt",
    "bill_payments": "lastBillPaymentSyncAt",
    "projects": "lastProjectSyncAt",
    "employees": "lastEmployeeSyncAt",
    "services": "lastServiceSyncAt",
    "timelogs": "lastTimelogSyncAt",
    "invoices": "lastInvoiceSyncAt",
}




def _summary_has_fatal_failure(summary: Any) -> bool:
    """Return True when a sync stage itself failed, not just individual records.

    Record-level validation/import/export failures are expected partial outcomes. They
    must complete the scheduled job with errors so the UI can show the affected rows.
    Connection, configuration, or unexpected stage failures remain hard failures.
    """
    if not isinstance(summary, dict):
        return True
    if bool(summary.get(const.KEY_OK, True)):
        return False

    results = summary.get(const.KEY_RESULTS) or []
    has_failed_record = any(
        isinstance(row, dict)
        and str(row.get(const.KEY_STATUS) or "").strip().lower() == "failed"
        for row in results
    )
    return not has_failed_record

def _first_record_failure_message(summary: Any) -> Optional[str]:
    """Return the first actionable row-level failure message, when available."""
    if not isinstance(summary, dict):
        return None

    rows = summary.get(const.KEY_FAILURES) or summary.get(const.KEY_RESULTS) or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = str(row.get(const.KEY_STATUS) or "").strip().lower()
        if status and status != "failed":
            continue
        for key in (const.KEY_ERROR, const.KEY_MESSAGE, const.KEY_REASON):
            value = row.get(key)
            if isinstance(value, dict):
                value = value.get(const.KEY_MESSAGE) or value.get("message")
            message = str(value or "").strip()
            if message:
                return message
    return None

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timing_update(*, field: str, value: Optional[str] = None) -> Dict[str, Any]:
    return {"timings": {field: value or _iso_now()}}


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _latest_iso_datetime(values: list[Optional[str]]) -> Optional[str]:
    latest_raw: Optional[str] = None
    latest_dt: Optional[datetime] = None
    for value in values:
        if not value:
            continue
        parsed = _parse_iso_datetime(value)
        if parsed is None:
            continue
        if latest_dt is None or parsed > latest_dt:
            latest_dt = parsed
            latest_raw = value
    return latest_raw


def _module_watermark_job_types(module: str, selected_job_type: Optional[str] = None) -> tuple[str, ...]:
    job_types = MODULE_WATERMARK_JOB_TYPES.get(module)
    if job_types:
        return job_types
    if selected_job_type:
        return (selected_job_type,)
    return (JOB_TYPES[module],)


def _get_previous_sync_start_time(
    *,
    repo: ScheduledJobRepository,
    tenant_id: str,
    service_id: str,
    module: str,
    selected_job_type: Optional[str] = None,
    exclude_job_id: Optional[int] = None,
) -> Optional[str]:
    values: list[Optional[str]] = []
    for job_type in _module_watermark_job_types(module, selected_job_type):
        try:
            value = repo.get_last_job_start_time(
                tenant_id=tenant_id,
                job_type=job_type,
                service_id=service_id,
                exclude_job_id=exclude_job_id,
            )
            log_cdc_debug(logger,
                "watermark candidate",
                module=module,
                jobType=job_type,
                serviceId=service_id,
                excludeJobId=exclude_job_id,
                value=value,
            )
            values.append(value)
        except Exception:
            logger.warning(
                "[QBO-SYNC-JOB] previous sync lookup failed | module=%s job_type=%s",
                module,
                job_type,
                exc_info=True,
            )
    selected = _latest_iso_datetime(values)
    if selected:
        log_cdc_debug(logger,
            "watermark selected",
            module=module,
            serviceId=service_id,
            source="scheduledJob",
            selected=selected,
        )
        return selected

    last_sync_key = MODULE_LAST_SYNC_KEYS.get(module)
    if last_sync_key:
        try:
            additional_info = QuickBooksRepository().getAdditionalInfo(tenant_id, service_id) or {}
            value = additional_info.get(last_sync_key)
            log_cdc_debug(logger,
                "watermark fallback candidate",
                module=module,
                serviceId=service_id,
                source=last_sync_key,
                value=value,
            )
            selected = _latest_iso_datetime([value])
        except Exception:
            logger.warning(
                "[QBO-SYNC-JOB] previous sync fallback lookup failed | module=%s key=%s",
                module,
                last_sync_key,
                exc_info=True,
            )

    log_cdc_debug(logger,
        "watermark selected",
        module=module,
        serviceId=service_id,
        source=(last_sync_key if selected else None),
        selected=selected,
    )
    return selected


def complete_job(*, tenant_id: str, job_id: int, job_uuid: str, module: str, result: Dict[str, Any]) -> None:
    """Best-effort: persist job completion (store result JSON in blob, keep path in DB)."""
    try:
        result_path: Any = None
        try:
            store = QboBlobResultStore()
            result_path = store.put_json(tenant_id=tenant_id, module=module, job_uuid=job_uuid, payload=(result or {}))
        except Exception:
            logger.warning("[QBO-SYNC-JOB] WARN: failed to store job result in blob storage for module=%s job_id=%s job_uuid=%s", module, job_id, job_uuid, exc_info=True)

        repo = ScheduledJobRepository()
        completed_at = datetime.now(timezone.utc)
        try:
            repo.update_job_additional_data(
                tenant_id=tenant_id,
                job_id=int(job_id),
                updates=_timing_update(field='syncCompletedAt', value=completed_at.isoformat()),
            )
        except Exception:
            logger.warning(
                "[QBO-SYNC-JOB] failed to store completion timing | job_id=%s",
                job_id,
                exc_info=True,
            )
        result_payload = result or {}
        failed_total = int((result_payload.get("overall") or {}).get("failedTotal") or 0)
        if not bool(result_payload.get(const.KEY_OK, True)):
            final_status = const.JOB_STATUS_FAILED
        elif bool(result_payload.get("partialSuccess")) or failed_total > 0:
            final_status = const.JOB_STATUS_COMPLETED_WITH_ERRORS
        else:
            final_status = const.JOB_STATUS_COMPLETED
        repo.update_job(
            tenant_id=tenant_id,
            job_id=int(job_id),
            status=final_status,
            result=result_path,
            completed_time=completed_at,
        )
    except Exception:
        logger.warning("[QBO-SYNC-JOB] WARN: failed to update scheduled_job", exc_info=True)


def fail_job(*, tenant_id: str, job_id: int, job_uuid: str, module: str, err: str) -> None:
    """Best-effort: persist job failure (store error JSON in blob, keep path in DB)."""
    del err  # Details are logged server-side and are not stored in the UI-visible result.
    try:
        payload = {
            "ok": False,
            "message": "The QuickBooks sync could not be completed. Please retry or contact support.",
            "errorCode": "QBO_SYNC_JOB_FAILED",
        }

        result_path: Any = None
        try:
            store = QboBlobResultStore()
            result_path = store.put_json(tenant_id=tenant_id, module=module, job_uuid=job_uuid, payload=payload)
        except Exception:
            logger.warning("[QBO-SYNC-JOB] WARN: failed to store job failure details in blob storage for module=%s job_id=%s job_uuid=%s", module, job_id, job_uuid, exc_info=True)

        repo = ScheduledJobRepository()
        completed_at = datetime.now(timezone.utc)
        try:
            repo.update_job_additional_data(
                tenant_id=tenant_id,
                job_id=int(job_id),
                updates=_timing_update(field='syncCompletedAt', value=completed_at.isoformat()),
            )
        except Exception:
            logger.warning(
                "[QBO-SYNC-JOB] failed to store failure timing | job_id=%s",
                job_id,
                exc_info=True,
            )
        repo.update_job(
            tenant_id=tenant_id,
            job_id=int(job_id),
            status=const.JOB_STATUS_FAILED,
            result=result_path,
            completed_time=completed_at,
        )
    except Exception:
        logger.warning("[QBO-SYNC-JOB] WARN: failed to update scheduled_job (FAILED)", exc_info=True)


def _sync_customers(tenant_id: str, service_id: str, tenant_api_token: str, previous_sync_start_at: Optional[str] = None, before_stage: Optional[Callable[[str], None]] = None, after_stage: Optional[Callable[[str], None]] = None, force_full: bool = False) -> Dict[str, Any]:
    """One customer sync run (export + import)."""
    from source.quickbooks.QuickBooksCustomerExportService import QuickBooksCustomerExportService
    from source.quickbooks.QuickBooksCustomerImportService import QuickBooksCustomerImportService
    from source.Utils.handler import build_customer_sync_overall, build_customer_sync_overall_results

    logger.info(f"\n[CUSTOMER-SYNC] ══════════════════════════════════════════")
    logger.info(f"[CUSTOMER-SYNC] START | tenant_id={tenant_id!r} service_id={service_id!r}")
    logger.info(f"[CUSTOMER-SYNC] previous_sync_start_at={previous_sync_start_at!r} force_full={force_full}")
    logger.info(f"[CUSTOMER-SYNC] ══════════════════════════════════════════")

    exporter = QuickBooksCustomerExportService()
    importer = QuickBooksCustomerImportService()

    logger.info(f"\n[CUSTOMER-SYNC] ── STAGE 1: EXPORT ──────────────────────")
    if before_stage:
        before_stage('export')
    export_summary = exporter.export_customers(tenant_id=tenant_id, service_id=service_id, limit=200, offset=0)
    if after_stage:
        after_stage('export')
    export_ok = bool((export_summary or {}).get(const.KEY_OK, True))
    logger.info(f"[CUSTOMER-SYNC] EXPORT done | ok={export_ok} created={( export_summary or {}).get('createdInQbo', 0)} updated={(export_summary or {}).get('updatedInQbo', 0)} mapped={(export_summary or {}).get('mapped from QBO', 0)} failed={(export_summary or {}).get('failed', 0)}")
    if not export_ok:
        logger.error(f"[CUSTOMER-SYNC] EXPORT ERROR | message={(export_summary or {}).get(const.KEY_MESSAGE)} error={(export_summary or {}).get(const.KEY_ERROR)}")

    page_size = int(os.getenv("QBO_IMPORT_PAGE_SIZE", "100") or 100)
    if page_size <= 0:
        page_size = 100
    logger.info(f"\n[CUSTOMER-SYNC] ── STAGE 2: IMPORT ──────────────────────")
    logger.info(f"[CUSTOMER-SYNC] IMPORT config | page_size={page_size} changed_since_override={previous_sync_start_at!r} force_full={force_full}")
    if before_stage:
        before_stage('import')
    import_summary = importer.import_customers(
        tenant_id=tenant_id,
        service_id=service_id,
        tenant_api_token=(tenant_api_token or ""),
        page_size=page_size,
        start_position=1,
        max_customers=None,
        changed_since_override=previous_sync_start_at,
        force_full=force_full,
    )
    if after_stage:
        after_stage('import')

    # Retry any individually-failed customers by ID so they are not permanently
    # lost behind the watermark on the next CDC sync.
    failed_qbo_ids = [
        f.get("qboCustomerId") or f.get("externalCustomerId")
        for f in (import_summary or {}).get(const.KEY_FAILURES, [])
        if f.get("qboCustomerId") or f.get("externalCustomerId")
    ]
    if failed_qbo_ids:
        logger.info(f"[CUSTOMER-SYNC] Retrying {len(failed_qbo_ids)} failed customer(s) by ID: {failed_qbo_ids}")
        retry_summary = importer.import_customers_by_ids(
            tenant_id=tenant_id,
            service_id=service_id,
            customer_ids=failed_qbo_ids,
            tenant_api_token=(tenant_api_token or ""),
        )
        logger.info(f"[CUSTOMER-SYNC] Retry complete | processed={retry_summary.get('processed', 0)} created={retry_summary.get('created in FIELDCRAFT', 0)} updated={retry_summary.get('updatedInFIELDCRAFT', 0)} failed={retry_summary.get('failed', 0)}")
        import_summary["retry"] = retry_summary

    import_ok = bool((import_summary or {}).get(const.KEY_OK, True))
    logger.info(f"[CUSTOMER-SYNC] IMPORT done | ok={import_ok} mode={(import_summary or {}).get('mode')} processed={(import_summary or {}).get('processed', 0)} created={(import_summary or {}).get('created in FIELDCRAFT', 0)} updated={(import_summary or {}).get('updatedInFIELDCRAFT', 0)} linked={(import_summary or {}).get('linkedExistingInFieldCraft', 0)} skipped_already_current={(import_summary or {}).get('skippedAlreadyCurrent', 0)} skipped_local_newer={(import_summary or {}).get('skippedLocalNewer', 0)} failed={(import_summary or {}).get('failed', 0)}")
    if not import_ok:
        logger.error(f"[CUSTOMER-SYNC] IMPORT ERROR | error={(import_summary or {}).get(const.KEY_ERROR)}")

    top_ok = export_ok and import_ok

    msg_parts = []
    if not export_ok and (export_summary or {}).get(const.KEY_MESSAGE):
        msg_parts.append(f"export: {(export_summary or {}).get(const.KEY_MESSAGE)}")
    if not import_ok and (import_summary or {}).get(const.KEY_MESSAGE):
        msg_parts.append(f"import: {(import_summary or {}).get(const.KEY_MESSAGE)}")
    top_msg = "; ".join(msg_parts) if msg_parts else None

    overall_pack = build_customer_sync_overall_results(export_summary or {}, import_summary or {})

    logger.info(f"\n[CUSTOMER-SYNC] ── COMPLETE ───────────────────────────────")
    logger.info(f"[CUSTOMER-SYNC] overall ok={top_ok} | export_ok={export_ok} import_ok={import_ok}")
    if top_msg:
        logger.info(f"[CUSTOMER-SYNC] message={top_msg!r}")
    logger.info(f"[CUSTOMER-SYNC] ══════════════════════════════════════════\n")

    return {
        "ok": top_ok,
        "message": top_msg,
        "tenantId": tenant_id,
        "serviceId": service_id,
        "export": export_summary,
        "import": import_summary,
        "overall": build_customer_sync_overall(export_summary or {}, import_summary or {}),
        "overallCounts": (overall_pack or {}).get("counts"),
        "overallResults": (overall_pack or {}).get(const.KEY_RESULTS),
        "previousSyncStartAt": previous_sync_start_at,
    }


def _sync_vendors(tenant_id: str, service_id: str, _unused_token: str = "", previous_sync_start_at: Optional[str] = None, before_stage: Optional[Callable[[str], None]] = None, after_stage: Optional[Callable[[str], None]] = None, force_full: bool = False) -> Dict[str, Any]:
    """One vendor sync run (export + import)."""
    from source.quickbooks.QuickBooksVendorExportService import QuickBooksVendorExportService
    from source.quickbooks.QuickBooksVendorImportService import QuickBooksVendorImportService
    from source.Utils.handler import build_vendor_sync_overall, build_vendor_sync_overall_results

    exporter = QuickBooksVendorExportService()
    importer = QuickBooksVendorImportService()

    log_cdc_debug(logger,
        "vendor sync runner",
        tenantId=tenant_id,
        serviceId=service_id,
        previousSyncStartAt=previous_sync_start_at,
        forceFull=force_full,
    )

    if before_stage:
        before_stage('export')
    export_summary = exporter.export_vendors(tenant_id=tenant_id, service_id=service_id, limit=200, offset=0)
    if after_stage:
        after_stage('export')
    page_size = int(os.getenv("QBO_IMPORT_PAGE_SIZE", "100") or 100)
    if page_size <= 0:
        page_size = 100

    if before_stage:
        before_stage('import')
    import_summary = importer.import_vendors(
        tenant_id=tenant_id,
        service_id=service_id,
        page_size=page_size,
        start_position=1,
        max_vendors=None,
        dry_run=False,
        changed_since_override=previous_sync_start_at,
        force_full=force_full,
    )
    if after_stage:
        after_stage('import')

    failed_qbo_ids = [
        f.get("qboVendorId") or f.get("externalVendorId")
        for f in (import_summary or {}).get(const.KEY_FAILURES, [])
        if f.get("qboVendorId") or f.get("externalVendorId")
    ]
    if failed_qbo_ids:
        retry_summary = importer.import_vendors_by_ids(
            tenant_id=tenant_id,
            service_id=service_id,
            vendor_ids=failed_qbo_ids,
        )
        import_summary["retry"] = retry_summary

    export_ok = bool((export_summary or {}).get(const.KEY_OK, True))
    import_ok = bool((import_summary or {}).get(const.KEY_OK, True))
    top_ok = export_ok and import_ok

    msg_parts = []
    if not export_ok and (export_summary or {}).get(const.KEY_MESSAGE):
        msg_parts.append(f"export: {(export_summary or {}).get(const.KEY_MESSAGE)}")
    if not import_ok and (import_summary or {}).get(const.KEY_MESSAGE):
        msg_parts.append(f"import: {(import_summary or {}).get(const.KEY_MESSAGE)}")
    top_msg = "; ".join(msg_parts) if msg_parts else None

    overall_pack = build_vendor_sync_overall_results(export_summary or {}, import_summary or {})

    return {
        "ok": top_ok,
        "message": top_msg,
        "tenantId": tenant_id,
        "serviceId": service_id,
        "export": export_summary,
        "import": import_summary,
        "overall": build_vendor_sync_overall(export_summary or {}, import_summary or {}),
        "overallCounts": (overall_pack or {}).get("counts"),
        "overallResults": (overall_pack or {}).get(const.KEY_RESULTS),
        "previousSyncStartAt": previous_sync_start_at,
    }


def _sync_payments(tenant_id: str, service_id: str, _unused_token: str = "", previous_sync_start_at: Optional[str] = None, before_stage: Optional[Callable[[str], None]] = None, after_stage: Optional[Callable[[str], None]] = None, force_full: bool = False) -> Dict[str, Any]:
    """One customer payment sync run (FIELDCRAFT -> QuickBooks export only).

    FIELDCRAFT owns customer receipt/reconciliation state. The legacy explicit import API is
    retained for compatibility, but scheduled Customer Payment sync does not import
    QuickBooks Payments back into FIELDCRAFT.
    """
    from source.quickbooks.QuickBooksPaymentExportService import QuickBooksPaymentExportService
    from source.Utils.handler import build_payment_sync_overall, build_payment_sync_overall_results

    exporter = QuickBooksPaymentExportService()

    if before_stage:
        before_stage('export')
    export_summary = exporter.export_payments(
        tenant_id=tenant_id,
        service_id=service_id,
        requested_max_payments=2000,
        dry_run=False,
    )
    if after_stage:
        after_stage('export')

    import_summary: Dict[str, Any] = {
        const.KEY_OK: True,
        "skipped": True,
        const.KEY_MESSAGE: "Customer Payment scheduled sync is export-only; FIELDCRAFT is the source of truth.",
        const.KEY_RESULTS: [],
        const.KEY_FAILURES: [],
    }
    export_ok = bool((export_summary or {}).get(const.KEY_OK, True))
    overall_pack = build_payment_sync_overall_results(export_summary or {}, import_summary)

    return {
        "ok": export_ok,
        "message": (export_summary or {}).get(const.KEY_MESSAGE),
        "tenantId": tenant_id,
        "serviceId": service_id,
        "export": export_summary,
        "import": import_summary,
        "overall": build_payment_sync_overall(export_summary or {}, import_summary),
        "overallCounts": (overall_pack or {}).get("counts"),
        "overallResults": (overall_pack or {}).get(const.KEY_RESULTS),
        "previousSyncStartAt": previous_sync_start_at,
    }


def _sync_bills(tenant_id: str, service_id: str, _unused_token: str = "", previous_sync_start_at: Optional[str] = None, before_stage: Optional[Callable[[str], None]] = None, after_stage: Optional[Callable[[str], None]] = None, force_full: bool = False) -> Dict[str, Any]:
    """One bill sync run (export + import)."""
    from source.quickbooks.QuickBooksBillExportService import QuickBooksBillExportService
    from source.quickbooks.QuickBooksBillImportService import QuickBooksBillImportService
    from source.Utils.handler import build_bill_sync_overall, build_bill_sync_overall_results

    exporter = QuickBooksBillExportService()
    importer = QuickBooksBillImportService()

    if before_stage:
        before_stage('export')
    export_summary = exporter.export_bills(tenant_id=tenant_id, service_id=service_id, limit=200, offset=0)
    if after_stage:
        after_stage('export')

    page_size = int(os.getenv("QBO_IMPORT_PAGE_SIZE", "100") or 100)
    if page_size <= 0:
        page_size = 100

    if before_stage:
        before_stage('import')
    import_summary = importer.import_bills(
        tenant_id=tenant_id,
        service_id=service_id,
        page_size=page_size,
        start_position=1,
        max_bills=None,
        dry_run=False,
        changed_since_override=previous_sync_start_at,
        force_full=force_full,
    )
    if after_stage:
        after_stage('import')

    failed_qbo_ids = [
        f.get("qboBillId") or f.get("externalReceiptId")
        for f in (import_summary or {}).get(const.KEY_FAILURES, [])
        if f.get("qboBillId") or f.get("externalReceiptId")
    ]
    if failed_qbo_ids:
        retry_summary = importer.import_bills_by_ids(
            tenant_id=tenant_id,
            service_id=service_id,
            bill_ids=failed_qbo_ids,
        )
        import_summary["retry"] = retry_summary

    export_ok = bool((export_summary or {}).get(const.KEY_OK, True))
    import_ok = bool((import_summary or {}).get(const.KEY_OK, True))
    top_ok = export_ok and import_ok

    msg_parts = []
    if not export_ok and (export_summary or {}).get(const.KEY_MESSAGE):
        msg_parts.append(f"export: {(export_summary or {}).get(const.KEY_MESSAGE)}")
    if not import_ok and (import_summary or {}).get(const.KEY_MESSAGE):
        msg_parts.append(f"import: {(import_summary or {}).get(const.KEY_MESSAGE)}")
    top_msg = "; ".join(msg_parts) if msg_parts else None

    overall_pack = build_bill_sync_overall_results(export_summary or {}, import_summary or {})

    return {
        "ok": top_ok,
        "message": top_msg,
        "tenantId": tenant_id,
        "serviceId": service_id,
        "export": export_summary,
        "import": import_summary,
        "overall": build_bill_sync_overall(export_summary or {}, import_summary or {}),
        "overallCounts": (overall_pack or {}).get("counts"),
        "overallResults": (overall_pack or {}).get(const.KEY_RESULTS),
        "previousSyncStartAt": previous_sync_start_at,
    }


def _sync_bill_payments(tenant_id: str, service_id: str, _unused_token: str = "", previous_sync_start_at: Optional[str] = None, before_stage: Optional[Callable[[str], None]] = None, after_stage: Optional[Callable[[str], None]] = None, force_full: bool = False) -> Dict[str, Any]:
    """One AP bill payment sync run (export + import)."""
    from source.quickbooks.QuickBooksBillPaymentExportService import QuickBooksBillPaymentExportService
    from source.quickbooks.QuickBooksBillPaymentImportService import QuickBooksBillPaymentImportService

    exporter = QuickBooksBillPaymentExportService()
    importer = QuickBooksBillPaymentImportService()

    if before_stage:
        before_stage('export')
    export_summary = exporter.export_bill_payments(tenant_id=tenant_id, service_id=service_id, limit=200, offset=0)
    if after_stage:
        after_stage('export')

    page_size = int(os.getenv("QBO_IMPORT_PAGE_SIZE", "100") or 100)
    if page_size <= 0:
        page_size = 100

    if before_stage:
        before_stage('import')
    import_summary = importer.import_bill_payments(
        tenant_id=tenant_id,
        service_id=service_id,
        page_size=page_size,
        start_position=1,
        max_payments=None,
        dry_run=False,
        changed_since_override=previous_sync_start_at,
        force_full=force_full,
    )
    if after_stage:
        after_stage('import')

    export_ok = bool((export_summary or {}).get(const.KEY_OK, True))
    import_ok = bool((import_summary or {}).get(const.KEY_OK, True))
    top_ok = export_ok and import_ok

    msg_parts = []
    if not export_ok and (export_summary or {}).get(const.KEY_MESSAGE):
        msg_parts.append(f"export: {(export_summary or {}).get(const.KEY_MESSAGE)}")
    if not import_ok and (import_summary or {}).get(const.KEY_MESSAGE):
        msg_parts.append(f"import: {(import_summary or {}).get(const.KEY_MESSAGE)}")
    top_msg = "; ".join(msg_parts) if msg_parts else None

    overall_counts = {
        "exportCreated": int((export_summary or {}).get("createdInQbo", 0)),
        "exportUpdated": int((export_summary or {}).get("updatedInQbo", 0)),
        "importCreated": int((import_summary or {}).get("imported", 0)),
        "importUpdated": int((import_summary or {}).get("updated", 0)),
        "failed": int((export_summary or {}).get("failed", 0)) + int((import_summary or {}).get("failed", 0)),
    }
    overall_results = list((export_summary or {}).get(const.KEY_RESULTS, [])) + list((import_summary or {}).get(const.KEY_RESULTS, []))

    return {
        "ok": top_ok,
        "message": top_msg,
        "tenantId": tenant_id,
        "serviceId": service_id,
        "export": export_summary,
        "import": import_summary,
        "overall": {
            "createdInQbo": overall_counts["exportCreated"],
            "updatedInQbo": overall_counts["exportUpdated"],
            "imported": overall_counts["importCreated"],
            "updatedInFieldCraft": overall_counts["importUpdated"],
            "failed": overall_counts["failed"],
        },
        "overallCounts": overall_counts,
        "overallResults": overall_results,
        "previousSyncStartAt": previous_sync_start_at,
    }





def _sync_services(
    tenant_id: str,
    service_id: str,
    _unused_token: str = "",
    previous_sync_start_at: Optional[str] = None,
    before_stage: Optional[Callable[[str], None]] = None,
    after_stage: Optional[Callable[[str], None]] = None,
    force_full: bool = False,
) -> Dict[str, Any]:
    """One service role sync run.

    Export pushes FIELDCRAFT roles to QuickBooks service items. Import is a read-only
    validation pass: it checks only already-mapped QBO service items, skips
    QBO-only products/services, and never creates or updates FIELDCRAFT roles.
    """
    del _unused_token
    from source.quickbooks.QuickBooksServiceRoleExportService import QuickBooksServiceRoleExportService
    from source.quickbooks.QuickBooksServiceRoleImportService import QuickBooksServiceRoleImportService

    exporter = QuickBooksServiceRoleExportService()
    importer = QuickBooksServiceRoleImportService()

    if before_stage:
        before_stage("export")
    export_summary = exporter.export_roles(tenant_id=tenant_id, service_id=service_id, limit=500, offset=0)
    if after_stage:
        after_stage("export")

    page_size = int(os.getenv("QBO_IMPORT_PAGE_SIZE", "100") or 100)
    if page_size <= 0:
        page_size = 100

    if before_stage:
        before_stage("import")
    import_summary = importer.import_roles(
        tenant_id=tenant_id,
        service_id=service_id,
        page_size=page_size,
        start_position=1,
        max_items=None,
        changed_since_override=previous_sync_start_at,
        force_full=force_full,
    )
    if after_stage:
        after_stage("import")

    export_ok = bool((export_summary or {}).get(const.KEY_OK, True))
    import_ok = bool((import_summary or {}).get(const.KEY_OK, True))
    top_ok = export_ok and import_ok

    msg_parts = []
    if not export_ok and (export_summary or {}).get(const.KEY_MESSAGE):
        msg_parts.append(f"export: {(export_summary or {}).get(const.KEY_MESSAGE)}")
    if not import_ok and (import_summary or {}).get(const.KEY_MESSAGE):
        msg_parts.append(f"import: {(import_summary or {}).get(const.KEY_MESSAGE)}")
    top_msg = "; ".join(msg_parts) if msg_parts else None

    overall_counts = {
        "exportCreated": int((export_summary or {}).get("createdInQbo", 0)),
        "exportUpdated": int((export_summary or {}).get("updatedInQbo", 0)),
        "exportInactivated": int((export_summary or {}).get("inactivatedInQbo", 0)),
        "mappedFromQbo": int((export_summary or {}).get("mapped from QBO", 0)),
        "validatedMapped": int((import_summary or {}).get("validatedMapped", 0)),
        "skippedUnmappedQboServices": int((import_summary or {}).get("skippedUnmapped", 0)),
        "skippedNonServiceItems": int((import_summary or {}).get("skippedNonServiceItems", 0)),
        "syncFailed": int((export_summary or {}).get("syncFailed", 0)) + int((import_summary or {}).get("syncFailed", 0)),
        "failed": (
            int((export_summary or {}).get("failed", 0))
            + int((import_summary or {}).get("failed", 0))
        ),
    }
    overall_results = (
        list((export_summary or {}).get(const.KEY_RESULTS, []))
        + list((import_summary or {}).get(const.KEY_RESULTS, []))
    )

    return {
        "ok": top_ok,
        "message": top_msg,
        "tenantId": tenant_id,
        "serviceId": service_id,
        "export": export_summary,
        "import": import_summary,
        "overall": {
            "createdInQbo": overall_counts["exportCreated"],
            "updatedInQbo": overall_counts["exportUpdated"],
            "inactivatedInQbo": overall_counts["exportInactivated"],
            "mappedFromQbo": overall_counts["mappedFromQbo"],
            "validatedMapped": overall_counts["validatedMapped"],
            "skippedUnmappedQboServices": overall_counts["skippedUnmappedQboServices"],
            "syncFailed": overall_counts["syncFailed"],
            "failed": overall_counts["failed"],
        },
        "overallCounts": overall_counts,
        "overallResults": overall_results,
        "previousSyncStartAt": previous_sync_start_at,
    }

def _sync_projects(tenant_id: str, service_id: str, _unused_token: str = "", previous_sync_start_at: Optional[str] = None, before_stage: Optional[Callable[[str], None]] = None, after_stage: Optional[Callable[[str], None]] = None, force_full: bool = False) -> Dict[str, Any]:
    """One project sync run.

    Export pushes FIELDCRAFT projects to QuickBooks as sub-customers. Import is a
    read-only validation pass: it checks only already-mapped QBO sub-customers,
    skips QBO-only sub-customers, and never creates or updates FIELDCRAFT projects.
    """
    from source.quickbooks.QuickBooksProjectExportService import QuickBooksProjectExportService
    from source.quickbooks.QuickBooksProjectImportService import QuickBooksProjectImportService

    exporter = QuickBooksProjectExportService()
    importer = QuickBooksProjectImportService()

    if before_stage:
        before_stage('export')
    export_summary = exporter.export_projects(tenant_id=tenant_id, service_id=service_id, limit=500, offset=0)
    if after_stage:
        after_stage('export')

    page_size = int(os.getenv("QBO_IMPORT_PAGE_SIZE", "100") or 100)
    if page_size <= 0:
        page_size = 100

    effective_target = (export_summary or {}).get("effectiveProjectSyncTarget")
    if effective_target == const.PROJECT_SYNC_TARGET_QBO_PROJECT:
        if before_stage:
            before_stage('import')
        import_summary = {
            const.KEY_OK: True,
            const.KEY_MESSAGE: "Project import skipped because this sync used QuickBooks Project mode.",
            "skipped": True,
            "validatedMapped": 0,
            "skippedUnmapped": 0,
            "skippedNonProjects": 0,
            "failed": 0,
            "syncFailed": 0,
            const.KEY_RESULTS: [],
            const.KEY_FAILURES: [],
        }
        if after_stage:
            after_stage('import')
    else:
        if before_stage:
            before_stage('import')
        import_summary = importer.import_projects(
            tenant_id=tenant_id,
            service_id=service_id,
            page_size=page_size,
            start_position=1,
            max_projects=None,
            changed_since_override=previous_sync_start_at,
            force_full=force_full,
        )
        if after_stage:
            after_stage('import')

    export_ok = bool((export_summary or {}).get(const.KEY_OK, True))
    import_ok = bool((import_summary or {}).get(const.KEY_OK, True))
    top_ok = export_ok and import_ok

    msg_parts = []
    if not export_ok and (export_summary or {}).get(const.KEY_MESSAGE):
        msg_parts.append(f"export: {(export_summary or {}).get(const.KEY_MESSAGE)}")
    if not import_ok and (import_summary or {}).get(const.KEY_MESSAGE):
        msg_parts.append(f"import: {(import_summary or {}).get(const.KEY_MESSAGE)}")
    top_msg = "; ".join(msg_parts) if msg_parts else None

    overall_counts = {
        "exportCreated": int((export_summary or {}).get("createdInQbo", 0)),
        "exportUpdated": int((export_summary or {}).get("updatedInQbo", 0)),
        "mappedFromQbo": int((export_summary or {}).get("mapped from QBO", 0)),
        "validatedMapped": int((import_summary or {}).get("validatedMapped", 0)),
        "skippedUnmappedQboProjects": int((import_summary or {}).get("skippedUnmapped", 0)),
        "skippedNonProjects": int((import_summary or {}).get("skippedNonProjects", 0)),
        "syncFailed": int((export_summary or {}).get("syncFailed", 0)) + int((import_summary or {}).get("syncFailed", 0)),
        "failed": int((export_summary or {}).get("failed", 0)) + int((import_summary or {}).get("failed", 0)),
    }
    overall_results = list((export_summary or {}).get(const.KEY_RESULTS, [])) + list((import_summary or {}).get(const.KEY_RESULTS, []))

    return {
        "ok": top_ok,
        "message": top_msg,
        "tenantId": tenant_id,
        "serviceId": service_id,
        "export": export_summary,
        "import": import_summary,
        "overall": {
            "createdInQbo": overall_counts["exportCreated"],
            "updatedInQbo": overall_counts["exportUpdated"],
            "mappedFromQbo": overall_counts["mappedFromQbo"],
            "validatedMapped": overall_counts["validatedMapped"],
            "skippedUnmappedQboProjects": overall_counts["skippedUnmappedQboProjects"],
            "syncFailed": overall_counts["syncFailed"],
            "failed": overall_counts["failed"],
        },
        "overallCounts": overall_counts,
        "overallResults": overall_results,
        "previousSyncStartAt": previous_sync_start_at,
    }

def _sync_accounts(tenant_id: str, service_id: str, _unused_token: str = "", previous_sync_start_at: Optional[str] = None, before_stage: Optional[Callable[[str], None]] = None, after_stage: Optional[Callable[[str], None]] = None, force_full: bool = False) -> Dict[str, Any]:
    """One chart-of-accounts sync run (import + export)."""
    from source.quickbooks.QuickBooksAccountExportService import QuickBooksAccountExportService
    from source.quickbooks.QuickBooksAccountImportService import QuickBooksAccountImportService
    from source.Utils.handler import build_account_sync_overall, build_account_sync_overall_results

    importer = QuickBooksAccountImportService()
    exporter = QuickBooksAccountExportService()

    page_size = int(os.getenv("QBO_IMPORT_PAGE_SIZE", "100") or 100)
    if page_size <= 0:
        page_size = 100

    if before_stage:
        before_stage('import')
    import_summary = importer.import_accounts(
        tenant_id=tenant_id,
        service_id=service_id,
        page_size=page_size,
        start_position=1,
        max_accounts=None,
        dry_run=False,
        changed_since_override=previous_sync_start_at,
    )
    if after_stage:
        after_stage('import')

    if before_stage:
        before_stage('export')
    export_summary = exporter.export_accounts(tenant_id=tenant_id, service_id=service_id, limit=500, offset=0)
    if after_stage:
        after_stage('export')

    import_ok = bool((import_summary or {}).get(const.KEY_OK, True))
    export_ok = bool((export_summary or {}).get(const.KEY_OK, True))
    top_ok = import_ok and export_ok

    msg_parts = []
    if not import_ok and (import_summary or {}).get(const.KEY_MESSAGE):
        msg_parts.append(f"import: {(import_summary or {}).get(const.KEY_MESSAGE)}")
    if not export_ok and (export_summary or {}).get(const.KEY_MESSAGE):
        msg_parts.append(f"export: {(export_summary or {}).get(const.KEY_MESSAGE)}")
    top_msg = "; ".join(msg_parts) if msg_parts else None

    overall_pack = build_account_sync_overall_results(export_summary or {}, import_summary or {})

    return {
        "ok": top_ok,
        "message": top_msg,
        "tenantId": tenant_id,
        "serviceId": service_id,
        "import": import_summary,
        "export": export_summary,
        "overall": build_account_sync_overall(export_summary or {}, import_summary or {}),
        "overallCounts": (overall_pack or {}).get("counts"),
        "overallResults": (overall_pack or {}).get(const.KEY_RESULTS),
        "previousSyncStartAt": previous_sync_start_at,
    }


def _sync_employees(tenant_id: str, service_id: str, tenant_api_token: str = "", previous_sync_start_at: Optional[str] = None, before_stage: Optional[Callable[[str], None]] = None, after_stage: Optional[Callable[[str], None]] = None, force_full: bool = False) -> Dict[str, Any]:
    """One employee sync run (export + import)."""
    from source.quickbooks.QuickBooksWorkerExportService import QuickBooksWorkerExportService
    from source.quickbooks.QuickBooksEmployeeImportService import QuickBooksEmployeeImportService
    from source.Utils.handler import build_employee_sync_overall, build_employee_sync_overall_results

    exporter = QuickBooksWorkerExportService()
    importer = QuickBooksEmployeeImportService()

    log_cdc_debug(logger,
        "employee sync runner",
        tenantId=tenant_id,
        serviceId=service_id,
        previousSyncStartAt=previous_sync_start_at,
        forceFull=force_full,
    )

    if before_stage:
        before_stage('export')
    export_summary = exporter.export_all_workers(tenant_id=tenant_id, service_id=service_id, batch_size=500)
    if after_stage:
        after_stage('export')

    page_size = int(os.getenv("QBO_IMPORT_PAGE_SIZE", "100") or 100)
    if page_size <= 0:
        page_size = 100

    if before_stage:
        before_stage('import')
    import_summary = importer.import_employees(
        tenant_id=tenant_id,
        service_id=service_id,
        tenant_api_token=tenant_api_token or "",
        page_size=page_size,
        start_position=1,
        max_employees=None,
        dry_run=False,
        changed_since_override=previous_sync_start_at,
        force_full=force_full,
    )
    if after_stage:
        after_stage('import')

    failed_qbo_ids = [
        f.get("qboEmployeeId") or f.get("externalEmployeeId")
        for f in (import_summary or {}).get(const.KEY_FAILURES, [])
        if f.get("qboEmployeeId") or f.get("externalEmployeeId")
    ]
    if failed_qbo_ids:
        import_summary["retry"] = importer.import_employees_by_ids(
            tenant_id=tenant_id,
            service_id=service_id,
            employee_ids=failed_qbo_ids,
            tenant_api_token=tenant_api_token or "",
        )

    export_fatal = _summary_has_fatal_failure(export_summary)
    import_fatal = _summary_has_fatal_failure(import_summary)
    top_ok = not export_fatal and not import_fatal

    failed_total = (
        int((export_summary or {}).get("failed") or 0)
        + int((import_summary or {}).get("failed") or 0)
    )

    msg_parts = []
    if export_fatal and (export_summary or {}).get(const.KEY_MESSAGE):
        msg_parts.append(f"export: {(export_summary or {}).get(const.KEY_MESSAGE)}")
    if import_fatal and (import_summary or {}).get(const.KEY_MESSAGE):
        msg_parts.append(f"import: {(import_summary or {}).get(const.KEY_MESSAGE)}")
    if not msg_parts and failed_total > 0:
        noun = "record" if failed_total == 1 else "records"
        verb = "needs" if failed_total == 1 else "need"
        msg_parts.append(
            f"Employee sync completed with {failed_total} worker {noun} that {verb} attention."
        )
    top_msg = "; ".join(msg_parts) if msg_parts else None

    overall_pack = build_employee_sync_overall_results(export_summary or {}, import_summary or {})

    return {
        "ok": top_ok,
        "partialSuccess": bool(top_ok and failed_total > 0),
        "message": top_msg,
        "tenantId": tenant_id,
        "serviceId": service_id,
        "export": export_summary,
        "import": import_summary,
        "overall": build_employee_sync_overall(export_summary or {}, import_summary or {}),
        "overallCounts": (overall_pack or {}).get("counts"),
        "overallResults": (overall_pack or {}).get(const.KEY_RESULTS),
        "previousSyncStartAt": previous_sync_start_at,
    }


def _sync_timelogs(
    tenant_id: str,
    service_id: str,
    _unused_token: str = "",
    previous_sync_start_at: Optional[str] = None,
    before_stage: Optional[Callable[[str], None]] = None,
    after_stage: Optional[Callable[[str], None]] = None,
    force_full: bool = False,
) -> Dict[str, Any]:
    """One timelog sync run (export-only; FIELDCRAFT is the source of truth)."""
    del _unused_token
    from source.quickbooks.QuickBooksTimelogExportService import create_timelog_export_service

    exporter = create_timelog_export_service()

    if before_stage:
        before_stage("export")
    export_summary = exporter.export_all_timelogs(tenant_id=tenant_id, service_id=service_id, batch_size=500)
    if after_stage:
        after_stage("export")

    failed_total = int((export_summary or {}).get("failed") or 0) + int(
        (export_summary or {}).get("syncFailed") or 0
    )
    successful_total = int((export_summary or {}).get("createdInQbo") or 0) + int(
        (export_summary or {}).get("updatedInQbo") or 0
    )
    export_fatal = _summary_has_fatal_failure(export_summary)
    message = (export_summary or {}).get(const.KEY_MESSAGE)
    if failed_total and not export_fatal:
        message = _first_record_failure_message(export_summary) or (
            f"QuickBooks time activity sync completed with {failed_total} record(s) that need attention."
        )

    return {
        "ok": not export_fatal,
        "message": message,
        "tenantId": tenant_id,
        "serviceId": service_id,
        "export": export_summary,
        "partialSuccess": bool(failed_total and successful_total),
        "overall": {
            "failedTotal": failed_total,
            "successfulTotal": successful_total,
        },
        "previousSyncStartAt": previous_sync_start_at,
    }


def _sync_invoices(
    tenant_id: str,
    service_id: str,
    _unused_token: str = "",
    previous_sync_start_at: Optional[str] = None,
    before_stage: Optional[Callable[[str], None]] = None,
    after_stage: Optional[Callable[[str], None]] = None,
    force_full: bool = False,
) -> Dict[str, Any]:
    """One invoice sync run (export-only; FIELDCRAFT is the source of truth)."""
    del _unused_token, force_full
    from source.quickbooks.QuickBooksAuthentication import QuickBooksReconnectRequiredError
    from source.quickbooks.QuickBooksInvoiceExportService import QuickBooksInvoiceExportService

    exporter = QuickBooksInvoiceExportService()
    if before_stage:
        before_stage("export")
    try:
        export_summary = exporter.export_all_invoices(
            tenant_id=tenant_id,
            service_id=service_id,
            batch_size=500,
        )
    except QuickBooksReconnectRequiredError as exc:
        return {
            "ok": False,
            "message": str(exc) or "QuickBooks authorization expired. Please reconnect QuickBooks and retry Invoice sync.",
            "reconnectRequired": True,
            "errorCode": exc.error_code,
            "tenantId": tenant_id,
            "serviceId": service_id,
            "partialSuccess": False,
            "overall": {"failedTotal": 0, "successfulTotal": 0},
            "previousSyncStartAt": previous_sync_start_at,
        }
    if after_stage:
        after_stage("export")

    failed_total = int((export_summary or {}).get("failed") or 0) + int(
        (export_summary or {}).get("syncFailed") or 0
    )
    successful_total = (
        int((export_summary or {}).get("createdInQbo") or 0)
        + int((export_summary or {}).get("updatedInQbo") or 0)
        + int((export_summary or {}).get("mappedFromQbo") or 0)
    )
    export_fatal = _summary_has_fatal_failure(export_summary)
    message = (export_summary or {}).get(const.KEY_MESSAGE)
    if failed_total and not export_fatal:
        message = _first_record_failure_message(export_summary) or (
            f"QuickBooks invoice sync completed with {failed_total} invoice(s) that need attention."
        )

    return {
        "ok": not export_fatal,
        "message": message,
        "tenantId": tenant_id,
        "serviceId": service_id,
        "export": export_summary,
        "partialSuccess": bool(failed_total and successful_total),
        "overall": {
            "failedTotal": failed_total,
            "successfulTotal": successful_total,
        },
        "previousSyncStartAt": previous_sync_start_at,
    }


SYNC_RUNNERS: Dict[str, Callable[[str, str, str], Dict[str, Any]]] = {
    "customers": _sync_customers,
    "vendors": _sync_vendors,
    "payments": _sync_payments,
    "accounts": _sync_accounts,
    "bills": _sync_bills,
    "bill_payments": _sync_bill_payments,
    "projects": _sync_projects,
    "employees": _sync_employees,
    "services": _sync_services,
    "timelogs": _sync_timelogs,
    "invoices": _sync_invoices,
}


def run_sync_job(*, module: str, tenant_id: str, service_id: str, tenant_api_token: str, job_id: int, job_uuid: str, sync_mode: Optional[str] = None) -> None:
    """Generic background runner (safe; never raises)."""
    logger.info(f"\n[QBO-SYNC-JOB] ▶ START | module={module!r} job_id={job_id} job_uuid={job_uuid} tenant_id={tenant_id!r} service_id={service_id!r}")
    repo = ScheduledJobRepository()

    def _before_stage(stage: str) -> None:
        field = 'exportStartedAt' if stage == 'export' else 'importStartedAt'
        repo.update_job_additional_data(
            tenant_id=tenant_id,
            job_id=int(job_id),
            updates=_timing_update(field=field),
        )

    def _after_stage(stage: str) -> None:
        field = 'exportCompletedAt' if stage == 'export' else 'importCompletedAt'
        repo.update_job_additional_data(
            tenant_id=tenant_id,
            job_id=int(job_id),
            updates=_timing_update(field=field),
        )

    try:
        previous_sync_start_at = _get_previous_sync_start_time(
            repo=repo,
            tenant_id=tenant_id,
            service_id=service_id,
            module=module,
            selected_job_type=JOB_TYPES[module],
            exclude_job_id=int(job_id),
        )
        repo.update_job_additional_data(
            tenant_id=tenant_id,
            job_id=int(job_id),
            updates={
                'previousSyncStartTime': previous_sync_start_at,
                'timings': {'syncStartedAt': _iso_now()},
            },
        )

        force_full = (sync_mode or "").strip().lower() == "full"
        log_cdc_debug(logger,
            "run sync decision",
            module=module,
            jobId=job_id,
            syncMode=sync_mode,
            previousSyncStartAt=previous_sync_start_at,
            forceFull=force_full,
        )
        result = SYNC_RUNNERS[module](
            tenant_id,
            service_id,
            tenant_api_token or '',
            previous_sync_start_at=previous_sync_start_at,
            before_stage=_before_stage,
            after_stage=_after_stage,
            force_full=force_full,
        )

        try:
            if isinstance(result, dict):
                result['previousSyncStartTime'] = previous_sync_start_at
            if isinstance(result, dict) and bool(result.get(const.KEY_OK, True)):
                now_iso = datetime.now(timezone.utc).isoformat()
                qb_repo = QuickBooksRepository()
                if module == 'customers':
                    qb_repo.setLastCustomerSyncAt(tenant_id, service_id, now_iso)
                elif module == 'payments':
                    qb_repo.setLastPaymentSyncAt(tenant_id, service_id, now_iso)
                elif module == 'vendors':
                    qb_repo.setLastVendorSyncAt(tenant_id, service_id, now_iso)
                elif module == 'accounts':
                    qb_repo.setLastAccountSyncAt(tenant_id, service_id, now_iso)
                elif module == 'bills':
                    qb_repo.setLastBillSyncAt(tenant_id, service_id, now_iso)
                elif module == 'bill_payments':
                    qb_repo.setLastBillPaymentSyncAt(tenant_id, service_id, now_iso)
                elif module == 'projects':
                    qb_repo.setLastProjectSyncAt(tenant_id, service_id, now_iso)
                elif module == 'employees':
                    qb_repo.setLastEmployeeSyncAt(tenant_id, service_id, now_iso)
                elif module == 'services':
                    qb_repo.setLastServiceSyncAt(tenant_id, service_id, now_iso)
                elif module == 'timelogs':
                    successful_total = int(((result.get("overall") or {}).get("successfulTotal")) or 0)
                    failed_total = int(((result.get("overall") or {}).get("failedTotal")) or 0)
                    if successful_total <= 0 and failed_total > 0:
                        now_iso = None
                    else:
                        qb_repo.setLastTimelogSyncAt(tenant_id, service_id, now_iso)
                elif module == 'invoices':
                    successful_total = int(((result.get("overall") or {}).get("successfulTotal")) or 0)
                    failed_total = int(((result.get("overall") or {}).get("failedTotal")) or 0)
                    if successful_total <= 0 and failed_total > 0:
                        now_iso = None
                    else:
                        qb_repo.setLastInvoiceSyncAt(tenant_id, service_id, now_iso)
                if now_iso:
                    result['lastSyncAt'] = now_iso
        except Exception:
            logger.warning(
                "[QBO-SYNC-JOB] failed to persist module watermark | module=%s job_id=%s",
                module,
                job_id,
                exc_info=True,
            )

        sync_ok = bool((result or {}).get(const.KEY_OK, True))
        logger.info(f"[QBO-SYNC-JOB] ■ COMPLETE | module={module!r} job_id={job_id} ok={sync_ok}")
        complete_job(tenant_id=tenant_id, job_id=job_id, job_uuid=job_uuid, module=module, result=result)
    except Exception as e:
        logger.exception("[QBO-SYNC-JOB] ✗ FAILED | module=%r job_id=%s", module, job_id)
        fail_job(tenant_id=tenant_id, job_id=job_id, job_uuid=job_uuid, module=module, err=str(e))


def start_sync_job(
        *,
        module: str,
        tenant_id: str,
        service_id: str,
        object_id: str,
        background_tasks: Any,
        tenant_api_token: str = "",
        sync_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Create/reuse a scheduled job and enqueue the background task.

    Parameters
    ----------
    module:
        One of: customers | vendors | payments | accounts
    object_id:
        User UID (created_by)
    tenant_api_token:
        Required only for customer sync (import needs tenant token).

    Returns
    -------
    { ok, jobId, status, reused? }
    """

    tenant_id = (tenant_id or "").strip()
    service_id = str(service_id or "").strip()
    object_id = (object_id or "").strip()

    if not tenant_id:
        raise ValueError("tenant_id is required")
    if not service_id:
        raise ValueError("service_id is required")
    if not object_id:
        raise ValueError("object_id is required")
    if module not in JOB_TYPES:
        raise ValueError(f"Unsupported module: {module}")

    repo = ScheduledJobRepository()
    force_full = (sync_mode or "").strip().lower() == "full"
    if module == "customers" and force_full:
        job_type = JOB_TYPES["customers_full"]
    elif module == "vendors" and force_full:
        job_type = JOB_TYPES["vendors_full"]
    elif module == "bills" and force_full:
        job_type = JOB_TYPES["bills_full"]
    else:
        job_type = JOB_TYPES[module]

    job_uuid = str(uuid4())
    previous_sync_start_time = _get_previous_sync_start_time(
        repo=repo,
        tenant_id=tenant_id,
        service_id=service_id,
        module=module,
        selected_job_type=job_type,
    )
    job_started_at = _iso_now()
    job = repo.create_or_get_active_job(
        tenant_id=tenant_id,
        created_by=object_id,
        job_type=job_type,
        service_id=service_id,
        additional_data={
            "tenantId": tenant_id,
            "serviceId": service_id,
            "module": module,
            "previousSyncStartTime": previous_sync_start_time,
            "timings": {
                "syncStartedAt": job_started_at,
                "exportStartedAt": None,
                "exportCompletedAt": None,
                "importStartedAt": None,
                "importCompletedAt": None,
                "syncCompletedAt": None,
            },
        },
        status=const.JOB_STATUS_RUNNING,
        job_uuid=job_uuid,
    )
    job_id = int(job["id"])
    log_cdc_debug(logger,
        "start sync job",
        module=module,
        jobType=job_type,
        jobId=job_id,
        reused=bool(job.get("reused")),
        syncMode=sync_mode,
        previousSyncStartTime=previous_sync_start_time,
    )
    if job.get("reused"):
        return {
            "ok": True,
            "jobId": job_id,
            "status": job.get("status") or const.JOB_STATUS_RUNNING,
            "reused": True,
        }

    # FastAPI BackgroundTasks interface: add_task(fn, **kwargs)
    background_tasks.add_task(
        run_sync_job,
        module=module,
        tenant_id=tenant_id,
        service_id=service_id,
        tenant_api_token=tenant_api_token,
        job_id=int(job_id),
        job_uuid=job_uuid,
        sync_mode=sync_mode,
    )

    return {"ok": True, "jobId": int(job_id), "status": const.JOB_STATUS_RUNNING}
