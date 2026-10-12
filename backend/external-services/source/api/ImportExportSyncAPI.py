"""Import/Export/Sync API (single entrypoint).

This router hosts the QuickBooks integration APIs in one place.

Security
--------
All application endpoints require **both** authentication factors — the ``api-secret``
header and a validated bearer token. Only the provider-facing OAuth callbacks are public:

* ``/extsvc/api/v1/qbo/callback``
These routes must remain public because Intuit redirects a browser to them and cannot
send either credential. After processing, the backend redirects the popup to a public
React route; the backend does not render any OAuth user interface.
"""

import logging
import os
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import AliasChoices, BaseModel, Field, model_validator
from source.dal.ScheduledJobRepository import ScheduledJobRepository
from source.dal.CustomerRepository import CustomerRepository
from source.Utils.ScheduledJobHandler import JOB_TYPES, start_sync_job
from source.dal.QuickBooksRepository import QboBlobResultStore, QuickBooksRepository
from source.Utils.handler import get_bearer_token
from source.quickbooks.QuickBooksAuthentication import QuickBooksCSRFError, QuickBooksReconnectRequiredError
from source.quickbooks.QuickBooksOAuthConfig import build_quickbooks_frontend_return_url
from source.quickbooks.QuickBooksCapabilityService import QBOFeatureUnavailableError, QuickBooksCapabilityService

from source.Utils.security import verify_api_key
from source.Utils.authenticationService import verify_bearer_token
from source.Utils import constants as const
from source.Utils.auditlog import safe_audit_log
from source.dal.ScheduledJobRepository import result_from_blob

from source.Utils.QuickBooksAPIHandler import _safe_content_disposition_filename

logger = logging.getLogger(__name__)

# Public endpoints - required for Intuit webhook and OAuth callback.
QBO_PUBLIC_API = APIRouter(tags=["QuickBooks"])

# Protected QuickBooks endpoints. Both factors are required on every route:
# the shared api-secret header AND a validated bearer token.
QBO_API = APIRouter(
    tags=["QuickBooks"],
    dependencies=[Depends(verify_api_key), Depends(verify_bearer_token)],
)

# Single router imported by the app.
SYNC_API = APIRouter()
SYNC_API.include_router(QBO_PUBLIC_API)
SYNC_API.include_router(QBO_API)

PUBLIC_PATHS = {
    "/extsvc/api/v1/qbo/callback",
}


def _qbo_reconnect_response(exc: QuickBooksReconnectRequiredError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "ok": False,
            "error": exc.error_code,
            "message": str(exc),
            "detail": exc.detail,
            "reconnectRequired": True,
        },
    )


def _internal_error_response(*, error: str, message: str, status_code: int = 500) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "error": error,
            "message": message,
        },
    )


def _quickbooks_internal_exception() -> HTTPException:
    return HTTPException(
        status_code=500,
        detail="The QuickBooks operation failed. Please retry or contact support.",
    )


def _safe_validation_detail(exc: ValueError, fallback: str) -> str:
    message = str(exc).strip()
    if not message or len(message) > 300 or "\n" in message or "\r" in message:
        return fallback
    return message


def _resolve_quickbooks_service_id(service_id: Optional[str]) -> str:
    resolved = str(service_id or "").strip()
    if resolved:
        return resolved
    return str(
        QuickBooksRepository().getServiceIdByName(const.SERVICE_NAME_QUICKBOOKS) or ""
    ).strip()


def _auto_sync_skip_response(
    *,
    tenant_id: str,
    service_id: Optional[str],
    module: str,
) -> Optional[Dict[str, Any]]:
    resolved_service_id = _resolve_quickbooks_service_id(service_id)
    if not resolved_service_id:
        raise HTTPException(status_code=500, detail="QuickBooks service_id could not be resolved")

    if QuickBooksRepository().isAutoSyncEnabled(tenant_id, resolved_service_id):
        return None

    logger.info(
        "QuickBooks auto sync skipped because it is disabled | tenant=%s service_id=%s module=%s",
        tenant_id,
        resolved_service_id,
        module,
    )
    return {
        const.KEY_OK: True,
        "skipped": True,
        const.KEY_STATUS: "auto_sync_disabled",
        const.KEY_MESSAGE: "QuickBooks auto sync is disabled for this tenant.",
        "tenantId": tenant_id,
        "serviceId": resolved_service_id,
    }


def _record_last_sync(
    result: Optional[Dict[str, Any]],
    *,
    tenant_id: str,
    service_id: str,
    setter_name: str,
) -> None:
    if not bool((result or {}).get(const.KEY_OK, True)):
        return
    try:
        setter = getattr(QuickBooksRepository(), setter_name)
        setter(tenant_id, service_id)
    except Exception:
        logger.warning(
            "QuickBooks sync completed but watermark update failed | setter=%s",
            setter_name,
            exc_info=True,
        )


def _require_opt_in_endpoint(env_name: str) -> None:
    enabled = str(os.getenv(env_name) or "").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        # Return 404 rather than advertising internal diagnostics or destructive
        # maintenance operations in environments where they are not explicitly enabled.
        raise HTTPException(status_code=404, detail="Not found")


def _feature_not_available_response(exc: QBOFeatureUnavailableError) -> JSONResponse:
    logger.info("[API] capability check blocked | feature=%s", exc.feature)
    return JSONResponse(
        status_code=403,
        content={
            "ok": False,
            "error": "feature_not_available",
            "feature": exc.feature,
            "message": str(exc),
        },
    )


def _normalize_project_sync_method(method: Optional[str]) -> str:
    value = str(method or "").strip().upper()
    if value not in const.PROJECT_SYNC_METHODS_ALLOWED:
        raise HTTPException(
            status_code=400,
            detail=f"method must be one of: {', '.join(const.PROJECT_SYNC_METHODS_ALLOWED)}.",
        )
    return value


def _normalize_project_client_realm_id(value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if len(normalized) > 100:
        raise HTTPException(status_code=400, detail="project_client_realm_id is invalid.")
    return normalized


def _default_project_sync_method(*, tenant_id: str, service_id: str, config: Dict[str, Any]) -> str:
    method = str((config or {}).get(const.KEY_PROJECT_SYNC_METHOD) or "").strip().upper()
    if method in const.PROJECT_SYNC_METHODS_ALLOWED:
        return method

    try:
        from source.dal.ProjectRepository import ProjectRepository
        if ProjectRepository().has_current_project_mappings(tenant_id=tenant_id, service_id=service_id):
            return const.PROJECT_SYNC_METHOD_SUB_CUSTOMER
    except Exception:
        logger.debug("[ProjectAPI] could not inspect existing project mappings", exc_info=True)
    return const.PROJECT_SYNC_METHOD_AUTO


def _project_sync_config_payload(*, tenant_id: str, service_id: str, refresh: bool = False) -> Dict[str, Any]:
    repo = QuickBooksRepository()
    config = repo.getProjectSyncConfig(tenant_id, service_id) or {}
    method = _default_project_sync_method(tenant_id=tenant_id, service_id=service_id, config=config)
    projects_available = False
    capability_error = None

    if method in (const.PROJECT_SYNC_METHOD_AUTO, const.PROJECT_SYNC_METHOD_QBO_PROJECT):
        try:
            projects_available = QuickBooksCapabilityService().projects_available(tenant_id, service_id, refresh=refresh)
        except Exception as exc:
            capability_error = str(exc)
            logger.warning("[ProjectAPI] project capability detection failed | tenant_id=%s error=%s", tenant_id, exc)

    if method == const.PROJECT_SYNC_METHOD_QBO_PROJECT:
        effective_target = const.PROJECT_SYNC_TARGET_QBO_PROJECT if projects_available else None
    elif method == const.PROJECT_SYNC_METHOD_SUB_CUSTOMER:
        effective_target = const.PROJECT_SYNC_TARGET_SUB_CUSTOMER
    else:
        effective_target = const.PROJECT_SYNC_TARGET_QBO_PROJECT if projects_available else const.PROJECT_SYNC_TARGET_SUB_CUSTOMER

    message = (
        const.PROJECTS_NOT_AVAILABLE_MESSAGE
        if method == const.PROJECT_SYNC_METHOD_QBO_PROJECT and not projects_available
        else None
    )
    project_client_realm_id = str(
        config.get(const.KEY_PROJECT_CLIENT_REALM_ID) or ""
    ).strip() or None
    return {
        const.KEY_OK: True,
        "tenantId": tenant_id,
        "serviceId": service_id,
        "configured": bool(config),
        const.KEY_PROJECT_SYNC_METHOD: method,
        const.KEY_PROJECT_CLIENT_REALM_ID: project_client_realm_id,
        "project_client_realm_id": project_client_realm_id,
        "projectClientRealmIdPresent": bool(project_client_realm_id),
        "projectsAvailable": bool(projects_available),
        const.KEY_PROJECTS_AVAILABLE: bool(projects_available),
        "effectiveProjectSyncTarget": effective_target,
        const.KEY_EFFECTIVE_PROJECT_SYNC_TARGET: effective_target,
        const.KEY_MESSAGE: message,
        "capabilityError": capability_error,
        "capability_error": capability_error,
    }


# ----------------------
# Models
# ----------------------


class QuickBooksRequest(BaseModel):
    tenant_id: str
    service_id: str
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None

class QuickBooksAutoSyncConfigRequest(BaseModel):
    tenant_id: str
    service_id: str
    enabled: bool
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None

class QuickBooksSyncJobRequest(BaseModel):
    tenant_id: str
    service_id: str
    object_id: str
    sync_mode: Optional[str] = None  # "full" = force full scan, None/"cdc" = auto (CDC if watermark exists)

class QuickBooksPaymentCreateRequest(BaseModel):
    tenant_id: str
    service_id: Optional[str] = None
    customer_uid: str
    txn_date: str
    total_amount: float = Field(gt=0)
    payment_receives_id: Optional[int] = Field(default=None, ge=1)
    payment_ref_num: Optional[str] = None
    private_note: Optional[str] = None


class QuickBooksBillCreateRequest(BaseModel):
    tenant_id: str
    service_id: Optional[str] = None
    receipt_external_map_id: int = Field(ge=1)
    vendor_external_map_id: Optional[int] = Field(default=None, ge=1, deprecated=True)


class QuickBooksPaymentsExportRequest(BaseModel):
    tenant_id: str
    service_id: str
    dry_run: bool = False
    requested_max_payments: int = Field(default=2000, ge=1, le=const.QBO_IMPORT_REQUESTED_MAX)
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


class QuickBooksPaymentsImportRequest(BaseModel):
    tenant_id: str
    service_id: str
    page_size: int = Field(default=100, ge=1, le=const.QBO_IMPORT_PAGE_SIZE_MAX)
    start_position: int = Field(default=1, ge=1)
    requested_max: int = Field(default=500, ge=1, le=const.QBO_IMPORT_REQUESTED_MAX)
    dry_run: bool = False
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


class QuickBooksCustomerCreateRequest(BaseModel):
    tenant_id: str
    service_id: Optional[str] = None
    customer_external_map_id: int = Field(ge=1)


class QuickBooksCustomerExportRequest(BaseModel):
    tenant_id: str
    service_id: str


class QuickBooksCustomerImportRequest(BaseModel):
    tenant_id: str
    service_id: str
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


class QuickBooksVendorCreateRequest(BaseModel):
    tenant_id: str
    service_id: Optional[str] = None
    vendor_external_map_id: int = Field(ge=1)


class QuickBooksVendorExportRequest(BaseModel):
    tenant_id: str
    service_id: str
    limit: int = Field(default=200, ge=1, le=const.QBO_EXPORT_LIMIT_MAX)
    offset: int = Field(default=0, ge=0)
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None

class QuickBooksVendorImportRequest(BaseModel):
    tenant_id: str
    service_id: str
    page_size: int = Field(default=100, ge=1, le=const.QBO_IMPORT_PAGE_SIZE_MAX)
    start_position: int = Field(default=1, ge=1)
    requested_max: int = Field(default=500, ge=1, le=const.QBO_IMPORT_REQUESTED_MAX)
    dry_run: bool = False
    changed_since_override: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("changed_since_override", "changedSince", "changed_since"),
    )
    force_full: bool = False
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


class QuickBooksEmployeeExportRequest(BaseModel):
    tenant_id: str
    service_id: str
    limit: int = Field(default=500, ge=1, le=const.QBO_EXPORT_LIMIT_MAX)
    offset: int = Field(default=0, ge=0)
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


class QuickBooksEmployeeImportRequest(BaseModel):
    tenant_id: str
    service_id: str
    page_size: int = Field(default=100, ge=1, le=const.QBO_IMPORT_PAGE_SIZE_MAX)
    start_position: int = Field(default=1, ge=1)
    requested_max: int = Field(default=500, ge=1, le=const.QBO_IMPORT_REQUESTED_MAX)
    dry_run: bool = False
    changed_since_override: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("changed_since_override", "changedSince", "changed_since"),
    )
    force_full: bool = False
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


class QuickBooksEmployeeSyncConfigRequest(BaseModel):
    """Payload for saving per-tenant employee sync settings."""
    tenant_id: str
    service_id: str
    days_after_hire: int = Field(default=1, ge=1, le=365)
    # Worker type (W2/1099) is no longer configured — it is derived from QuickBooks on
    # import (QBO Employee -> W2, 1099-tracked QBO Vendor -> 1099).
    cost_enabled: bool = True
    default_cost_unit: str = const.EMPLOYEE_COST_UNIT_HOURLY
    cost_unit_hours: Optional[Dict[str, float]] = None
    cost_qbo_field: str = const.EMPLOYEE_COST_QBO_FIELD_COST_RATE
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


class QuickBooksAccountExportRequest(BaseModel):
    tenant_id: str
    service_id: str
    limit: int = Field(default=500, ge=1, le=const.QBO_EXPORT_LIMIT_MAX)
    offset: int = Field(default=0, ge=0)
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


class QuickBooksAccountImportRequest(BaseModel):
    tenant_id: str
    service_id: str
    page_size: int = Field(default=100, ge=1, le=const.QBO_IMPORT_PAGE_SIZE_MAX)
    start_position: int = Field(default=1, ge=1)
    requested_max: int = Field(default=2000, ge=1, le=const.QBO_IMPORT_REQUESTED_MAX)
    dry_run: bool = False
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


class QuickBooksChartOfAccountsExportRequest(QuickBooksAccountExportRequest):
    pass

class QuickBooksChartOfAccountsImportRequest(QuickBooksAccountImportRequest):
    pass


class QuickBooksProjectCreateRequest(BaseModel):
    tenant_id: str
    service_id: Optional[str] = None
    project_external_map_id: int = Field(ge=1)


class QuickBooksProjectExportRequest(BaseModel):
    tenant_id: str
    service_id: str
    limit: int = Field(default=500, ge=1, le=const.QBO_EXPORT_LIMIT_MAX)
    offset: int = Field(default=0, ge=0)
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


class QuickBooksProjectImportRequest(BaseModel):
    tenant_id: str
    service_id: str
    page_size: int = Field(default=100, ge=1, le=const.QBO_IMPORT_PAGE_SIZE_MAX)
    start_position: int = Field(default=1, ge=1)
    requested_max: Optional[int] = Field(default=None, ge=1, le=const.QBO_IMPORT_REQUESTED_MAX)
    force_full: bool = False
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None




class QuickBooksProjectSyncConfigRequest(BaseModel):
    tenant_id: str
    service_id: str
    method: str
    project_client_realm_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "project_client_realm_id",
            "projectClientRealmId",
            "clientCompanyRealmId",
        ),
    )
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


class QuickBooksInvoiceCreateRequest(BaseModel):
    tenant_id: str
    service_id: Optional[str] = None
    invoice_external_map_id: int = Field(ge=1)


class QuickBooksInvoiceExportRequest(BaseModel):
    tenant_id: str
    service_id: str
    limit: int = Field(default=500, ge=1, le=const.QBO_EXPORT_LIMIT_MAX)
    offset: int = Field(default=0, ge=0)
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


class QuickBooksTimelogExportRequest(BaseModel):
    tenant_id: str
    service_id: str
    limit: int = Field(default=500, ge=1, le=const.QBO_EXPORT_LIMIT_MAX)
    offset: int = Field(default=0, ge=0)
    dry_run: bool = False
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


class QuickBooksTimelogCreateRequest(BaseModel):
    tenant_id: str
    service_id: Optional[str] = None
    timelog_external_map_id: int = Field(ge=1)
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


# class QuickBooksTimelogDeleteAllRequest(BaseModel):
#     tenant_id: str
#     service_id: str
#     confirm_delete: bool = False
#     dry_run: bool = False
#     object_id: Optional[str] = None
#     email: Optional[str] = None
#     user_role: Optional[str] = None
#
#
#
#
class QuickBooksServiceRoleExportRequest(BaseModel):
    tenant_id: str
    service_id: str
    limit: int = Field(default=500, ge=1, le=const.QBO_EXPORT_LIMIT_MAX)
    offset: int = Field(default=0, ge=0)
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


class QuickBooksServiceRoleImportRequest(BaseModel):
    tenant_id: str
    service_id: str
    page_size: int = Field(default=100, ge=1, le=const.QBO_IMPORT_PAGE_SIZE_MAX)
    start_position: int = Field(default=1, ge=1)
    requested_max: Optional[int] = Field(default=None, ge=1, le=const.QBO_IMPORT_REQUESTED_MAX)
    force_full: bool = False
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


class QuickBooksServiceRoleAssignIncomeAccountRequest(BaseModel):
    tenant_id: str
    service_id: str
    chart_of_account_id: int = Field(ge=1)
    role_ids: Optional[list[int]] = None
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


class QuickBooksBillsExportRequest(BaseModel):
    tenant_id: str
    service_id: str
    limit: int = Field(default=200, ge=1, le=const.QBO_EXPORT_LIMIT_MAX)
    offset: int = Field(default=0, ge=0)
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


class QuickBooksBillsImportRequest(BaseModel):
    tenant_id: str
    service_id: str
    page_size: int = Field(default=100, ge=1, le=const.QBO_IMPORT_PAGE_SIZE_MAX)
    start_position: int = Field(default=1, ge=1)
    requested_max: int = Field(default=500, ge=1, le=const.QBO_IMPORT_REQUESTED_MAX)
    dry_run: bool = False
    changed_since_override: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("changed_since_override", "changedSince", "changed_since"),
    )
    force_full: bool = False
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


class QuickBooksBillPaymentsExportRequest(BaseModel):
    tenant_id: str
    service_id: str
    limit: int = Field(default=200, ge=1, le=const.QBO_EXPORT_LIMIT_MAX)
    offset: int = Field(default=0, ge=0)
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


class QuickBooksBillPaymentsImportRequest(BaseModel):
    tenant_id: str
    service_id: str
    page_size: int = Field(default=100, ge=1, le=const.QBO_IMPORT_PAGE_SIZE_MAX)
    start_position: int = Field(default=1, ge=1)
    requested_max: int = Field(default=500, ge=1, le=const.QBO_IMPORT_REQUESTED_MAX)
    dry_run: bool = False
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


class QuickBooksBillPaymentAssignAccountRequest(BaseModel):
    tenant_id: str
    service_id: str
    payment_expense_ids: list[int]
    qbo_external_account_id: str
    object_id: Optional[str] = None
    email: Optional[str] = None
    user_role: Optional[str] = None


# ----------------------
# Scheduled job helpers (tenant DB)
# ----------------------

# Job create/reuse + background execution lives in:
#   source/Utils/ScheduledJobHandler.py

def _parse_sync_job_dt(value: Optional[str]):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _latest_sync_pack(*packs: Dict[str, Any]) -> Dict[str, Any]:
    active = next((pack for pack in packs if pack.get("activeJobId")), None)
    if active:
        return active
    best = None
    best_dt = None
    for pack in packs:
        dt = _parse_sync_job_dt(pack.get("lastCompletedAt"))
        if dt is not None and (best_dt is None or dt > best_dt):
            best = pack
            best_dt = dt
    return best or (packs[0] if packs else {})

@QBO_API.get("/extsvc/api/v1/quickbooks/sync/state")
def quickbooks_sync_state(tenantId: str, serviceId: str, objectId: str):
    """Return running status + last result for each module (customers/payments/vendors/accounts).

    UI uses this to restore:
      - loading state after refresh/tab switch
      - last sync report after refresh/tab switch
    """
    try:
        repo = ScheduledJobRepository()

        def pack(job_type: str) -> Dict[str, Any]:
            active = None
            last = None
            try:
                active = repo.get_active_job(tenant_id=tenantId, job_type=job_type, created_by=objectId, service_id=serviceId)
            except Exception:
                active = None
            try:
                last = repo.get_last_done_job(tenant_id=tenantId, job_type=job_type, created_by=objectId, service_id=serviceId)
            except Exception:
                last = None

            raw_last = (last or {}).get("result")
            return {
                "activeJobId": (active or {}).get("id"),
                "activeStatus": (active or {}).get(const.KEY_STATUS),
                "lastJobId": (last or {}).get("id"),
                "lastStatus": (last or {}).get(const.KEY_STATUS),
                "lastCompletedAt": (last or {}).get("completedTime"),
                "lastResultPath": raw_last if isinstance(raw_last, str) else None,
                "lastResult": result_from_blob(tenant_id=tenantId, result_value=raw_last),
            }

        return {
            "ok": True,
            "tenantId": tenantId,
            "serviceId": serviceId,
            "objectId": objectId,
            "jobs": {
                "customers": _latest_sync_pack(pack(JOB_TYPES["customers"]), pack(JOB_TYPES["customers_full"])),
                "payments": pack(JOB_TYPES["payments"]),
                "vendors": _latest_sync_pack(pack(JOB_TYPES["vendors"]), pack(JOB_TYPES["vendors_full"])),
                "accounts": pack(JOB_TYPES["accounts"]),
                "bills": _latest_sync_pack(pack(JOB_TYPES["bills"]), pack(JOB_TYPES["bills_full"])),
                "bill_payments": pack(JOB_TYPES["bill_payments"]),
                "employees": pack(JOB_TYPES["employees"]),
                "projects": pack(JOB_TYPES["projects"]),
                "services": pack(JOB_TYPES["services"]),
                "timelogs": pack(JOB_TYPES["timelogs"]),
                "invoices": pack(JOB_TYPES["invoices"]),
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("QuickBooks sync_state failed")
        raise _quickbooks_internal_exception()


@QBO_API.get("/extsvc/api/v1/quickbooks/sync/job")
def quickbooks_sync_job(tenantId: str, jobId: int):
    try:
        repo = ScheduledJobRepository()
        job = repo.get_job_by_id(tenant_id=tenantId, job_id=int(jobId))
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # If job.result is a blob path, resolve it.
        raw_result = job.get("result")
        if isinstance(raw_result, str):
            job["resultPath"] = raw_result
        job["result"] = result_from_blob(tenant_id=tenantId, result_value=raw_result)
        return {"ok": True, "job": job}
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("QuickBooks sync_job fetch failed")
        raise _quickbooks_internal_exception()


@QBO_API.get("/extsvc/api/v1/quickbooks/customers/cdc-view")
def customer_cdc_view(
    tenantId: str,
    serviceId: str,
    page: int = 1,
    pageSize: int = 25,
    search: Optional[str] = None,
    syncStatus: Optional[str] = None,
    mappedOnly: bool = False,
    changedSince: Optional[str] = None,
):
    """Database-driven customer CDC view for the UI.

    Replaces blob-driven summary rendering with a paginated view sourced from
    the customers business table plus current active customer_external_maps rows.
    """
    try:
        repo = CustomerRepository()
        payload = repo.get_customer_cdc_page(
            tenant_id=tenantId,
            service_id=serviceId,
            page=page,
            page_size=pageSize,
            search=search,
            sync_status=syncStatus,
            mapped_only=mappedOnly,
        )
        response = {
            "ok": True,
            "tenantId": tenantId,
            "serviceId": serviceId,
            **payload,
        }
        logger.info(
            "[CustomerAPI] customer_cdc_view completed | rows=%s totalRecords=%s",
            len((payload or {}).get("rows") or []),
            ((payload or {}).get("pagination") or {}).get("totalRecords"),
        )
        return response
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("QuickBooks customer CDC view failed")
        raise _quickbooks_internal_exception()

@QBO_API.get("/extsvc/api/v1/quickbooks/vendors/cdc-view")
def vendor_cdc_view(
    tenantId: str,
    serviceId: str,
    page: int = 1,
    pageSize: int = 25,
    search: Optional[str] = None,
    syncStatus: Optional[str] = None,
    mappedOnly: bool = False,
    changedSince: Optional[str] = None,
):
    """Paginated vendor CDC view for the UI.

    Returns all vendors in the tenant DB joined with their latest
    vendor_external_map row (by endorsement_effective_date), with
    aggregate summary counts and pagination metadata.
    """
    try:
        from source.dal.VendorRepository import VendorRepository
        repo = VendorRepository()
        payload = repo.get_vendor_cdc_page(
            tenant_id=tenantId,
            service_id=serviceId,
            page=page,
            page_size=pageSize,
            search=search,
            sync_status=syncStatus,
            mapped_only=mappedOnly,
            changed_since=changedSince,
        )
        return {
            "ok": True,
            "tenantId": tenantId,
            "serviceId": serviceId,
            **payload,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        logging.exception("QuickBooks vendor CDC view failed")
        raise _quickbooks_internal_exception()


@QBO_API.get("/extsvc/api/v1/quickbooks/employees/cdc-view")
def employee_cdc_view(
    tenantId: str,
    serviceId: str,
    page: int = 1,
    pageSize: int = 25,
    search: Optional[str] = None,
    syncStatus: Optional[str] = None,
    mappedOnly: bool = False,
    changedSince: Optional[str] = None,
):
    """Paginated employee CDC view for QuickBooks employee sync."""
    logger.info(
        "[EmployeeAPI] employee_cdc_view started | tenantId=%s serviceId=%s page=%s pageSize=%s syncStatus=%r",
        tenantId,
        serviceId,
        page,
        pageSize,
        syncStatus,
    )
    try:
        from source.dal.EmployeeRepository import EmployeeRepository

        repo = EmployeeRepository()
        payload = repo.get_employee_cdc_page(
            tenant_id=tenantId,
            service_id=serviceId,
            page=page,
            page_size=pageSize,
            search=search,
            sync_status=syncStatus,
            mapped_only=mappedOnly,
            changed_since=changedSince,
        )
        response = {
            "ok": True,
            "tenantId": tenantId,
            "serviceId": serviceId,
            **payload,
        }
        logger.info(
            "[EmployeeAPI] employee_cdc_view completed | rows=%s totalRecords=%s",
            len((payload or {}).get("rows") or []),
            ((payload or {}).get("pagination") or {}).get("totalRecords"),
        )
        return response
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        logger.exception("QuickBooks employee CDC view failed")
        raise _quickbooks_internal_exception()


@QBO_API.get("/extsvc/api/v1/quickbooks/bill-payments/cdc-view")
def bill_payment_cdc_view(
    tenantId: str,
    serviceId: str,
    page: int = 1,
    pageSize: int = 25,
    search: Optional[str] = None,
    syncStatus: Optional[str] = None,
    mappedOnly: bool = False,
):
    try:
        from source.dal.BillPaymentExpenseRepository import BillPaymentExpenseRepository
        from source.dal.ChartOfAccountRepository import ChartOfAccountRepository
        from source.dal.QuickBooksRepository import QuickBooksRepository
        repo = BillPaymentExpenseRepository()
        payload = repo.get_bill_payment_cdc_page(
            tenant_id=tenantId,
            service_id=serviceId,
            page=page,
            page_size=pageSize,
            search=search,
            sync_status=syncStatus,
            mapped_only=mappedOnly,
        )
        rows = payload.get("rows") or []
        account_ids = {row["externalAccountId"] for row in rows if row.get("externalAccountId")}
        if account_ids:
            connection = QuickBooksRepository().getAdditionalInfo(tenantId, serviceId) or {}
            realm_id = str(connection.get(const.KEY_REALM_ID) or "").strip() or None
            eligible = ChartOfAccountRepository().list_bill_payment_eligible_accounts(tenantId, service_id=serviceId, external_acc_id=realm_id)
            account_map = {acc["external_id"]: acc for acc in eligible if acc.get("external_id")}
            for row in rows:
                aid = row.get("externalAccountId")
                match = account_map.get(aid) if aid else None
                row["qboAccountName"] = (row.get("assignedAccountName") or (match or {}).get("name")) if aid else None
                row["assignedAccountName"] = row.get("assignedAccountName") or ((match or {}).get("name") if aid else None)
                row["assignedAccountType"] = row.get("assignedAccountType") or ((match or {}).get("account_type") if aid else None)
                row["assignedAccountDetailType"] = row.get("assignedAccountDetailType") or ((match or {}).get("detail_type") if aid else None)
                row["assignedEligiblePayType"] = row.get("assignedEligiblePayType") or ("CreditCard" if str((match or {}).get("account_type") or "").strip().lower() == "credit card" else ("Check" if aid else None))
        return {"ok": True, "tenantId": tenantId, "serviceId": serviceId, **payload}
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("QuickBooks bill payment CDC view failed")
        raise _quickbooks_internal_exception()


@QBO_API.get("/extsvc/api/v1/quickbooks/bill-payments/payment-accounts")
def bill_payment_accounts(tenantId: str, serviceId: str):
    try:
        from source.dal.ChartOfAccountRepository import ChartOfAccountRepository
        from source.dal.QuickBooksRepository import QuickBooksRepository

        connection = QuickBooksRepository().getAdditionalInfo(tenantId, serviceId) or {}
        realm_id = str(connection.get(const.KEY_REALM_ID) or "").strip() or None
        accounts = ChartOfAccountRepository().list_bill_payment_eligible_accounts(
            tenantId,
            service_id=serviceId,
            external_acc_id=realm_id,
        )
        return {
            "ok": True,
            "tenantId": tenantId,
            "serviceId": serviceId,
            "accounts": accounts,
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("QuickBooks bill payment account list failed")
        raise _quickbooks_internal_exception()


@QBO_API.post("/extsvc/api/v1/quickbooks/bill-payments/assign-account")
def assign_bill_payment_account(req: QuickBooksBillPaymentAssignAccountRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()
    qbo_external_account_id = (req.qbo_external_account_id or "").strip()
    payment_expense_ids = [int(pid) for pid in (req.payment_expense_ids or []) if str(pid).strip()]
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")
    if not qbo_external_account_id:
        raise HTTPException(status_code=400, detail="qbo_external_account_id is required")
    if not payment_expense_ids:
        raise HTTPException(status_code=400, detail="At least one payment_expense_id is required")
    try:
        from source.dal.ChartOfAccountRepository import ChartOfAccountRepository
        from source.dal.FinalPaymentExternalMapRepository import FinalPaymentExternalMapRepository
        from source.dal.QuickBooksRepository import QuickBooksRepository

        connection = QuickBooksRepository().getAdditionalInfo(tenant_id, service_id) or {}
        realm_id = str(connection.get(const.KEY_REALM_ID) or "").strip() or None
        coa_repo = ChartOfAccountRepository()
        selected_account = coa_repo.get_bill_payment_eligible_account_by_external_id(
            tenant_id,
            service_id=service_id,
            external_id=qbo_external_account_id,
            external_acc_id=realm_id,
        )
        if not selected_account:
            raise HTTPException(status_code=400, detail="Selected QuickBooks payment account is missing, inactive, or not eligible for Bill Payment export")
        result = FinalPaymentExternalMapRepository().assign_qbo_payment_account(
            tenant_id=tenant_id,
            service_id=service_id,
            payment_expense_ids=payment_expense_ids,
            external_account_id=qbo_external_account_id,
            qbo_account_name=selected_account.get("name"),
            qbo_account_type=selected_account.get("account_type"),
            qbo_account_detail_type=selected_account.get("detail_type"),
            assigned_by=request.headers.get("x-object-id") or req.object_id,
        )
        safe_audit_log(
            tenant_id=tenant_id,
            action="bill_payment_account_assigned",
            user_id=request.headers.get("x-object-id") or req.object_id,
            email=request.headers.get("x-user-email") or req.email,
            user_role=request.headers.get("x-user-role") or req.user_role,
            status="success",
            context=f"assigned={len(payment_expense_ids)} qboAccount={qbo_external_account_id}",
            resource_type="quickbooks_bill_payment",
        )
        return {
            "ok": True,
            "tenantId": tenant_id,
            "serviceId": service_id,
            "assignedCount": result.get("assignedCount", 0),
            "paymentExpenseIds": result.get("paymentExpenseIds", []),
            "selectedAccount": selected_account,
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("QuickBooks assign_bill_payment_account failed")
        raise _quickbooks_internal_exception()


@QBO_PUBLIC_API.get("/extsvc/api/v1/qbo/callback")
def qbo_callback(
    code: str = None,
    state: str = None,
    realmId: str = None,
    error: str = None,
):
    """Process Intuit's callback server-side, then remove OAuth parameters from the URL."""
    if error:
        return _qbo_callback_redirect("denied")

    if not code or not state or not realmId:
        return _qbo_callback_redirect("incomplete")

    from source.service.QuickBooksService import QuickBooksService

    service = QuickBooksService()
    try:
        result = service.handle_callback(code=code, state=state, realm_id=realmId)
    except QuickBooksCSRFError:
        safe_audit_log(
            tenant_id="unknown",
            action="quickbooks_oauth_callback_csrf_error",
            status="failed",
            context="QuickBooks realmId provided",
            resource_type="quickbooks_auth",
        )
        return _qbo_callback_redirect("invalid_state")
    except QuickBooksReconnectRequiredError:
        return _qbo_callback_redirect("reconnect")
    except Exception:
        logging.exception("QuickBooks OAuth callback failed")
        safe_audit_log(
            tenant_id="unknown",
            action="quickbooks_oauth_callback_failed",
            status="failed",
            context="QuickBooks realmId provided",
            resource_type="quickbooks_auth",
        )
        return _qbo_callback_redirect("failed")

    callback_tenant = (result or {}).get("tenantId") or "unknown"
    safe_audit_log(
        tenant_id=callback_tenant,
        action="quickbooks_oauth_callback_completed",
        status="success",
        context="QuickBooks realmId provided",
        resource_type="quickbooks_auth",
    )
    return _qbo_callback_redirect("success")


def _qbo_callback_redirect(result: str) -> RedirectResponse:
    response = RedirectResponse(
        url=build_quickbooks_frontend_return_url(
            provider="accounting",
            result=result,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


# ----------------------
# Protected: QuickBooks APIs
# ----------------------


@QBO_API.post("/extsvc/api/v1/quickbooksAuth")
def quickbooks_auth(req: QuickBooksRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")

    object_id = request.headers.get("x-object-id", "") or None
    email = request.headers.get("x-user-email", "") or None
    user_role = request.headers.get("x-user-role", "") or None

    from source.service.QuickBooksService import QuickBooksService

    service = QuickBooksService()
    try:
        credentials = service.start_connect(tenant_id, service_id)
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("QuickBooks start_connect failed")
        # [AUDIT] Connect initiation failed — isolated, never raises.
        safe_audit_log(
            tenant_id=tenant_id,
            action="quickbooks_connect_failed",
            user_id=object_id,
            email=email,
            user_role=user_role,
            status="failed",
            context=str(e)[:64],
            resource_type="quickbooks_auth",
        )
        raise _quickbooks_internal_exception()

    # [AUDIT] Connect flow initiated successfully — isolated, never raises.
    safe_audit_log(
        tenant_id=tenant_id,
        action="quickbooks_connect_initiated",
        user_id=object_id,
        email=email,
        user_role=user_role,
        status="started",
        resource_type="quickbooks_auth",
    )
    return {"ok": True, "tenantId": tenant_id, "serviceId": service_id, "credentials": credentials}


@QBO_API.get("/extsvc/api/v1/quickbooks/status")
def quickbooks_status(tenantId: str, serviceId: str):
    from source.service.QuickBooksService import QuickBooksService

    service = QuickBooksService()
    try:
        return service.get_status(tenantId.strip(), str(serviceId).strip())
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("QuickBooks get_status failed")
        raise _quickbooks_internal_exception()


@QBO_API.get("/extsvc/api/v1/quickbooks/auto-sync-config")
def get_quickbooks_auto_sync_config(tenantId: str, serviceId: str):
    tenant_id = (tenantId or "").strip()
    service_id = str(serviceId or "").strip()
    if not tenant_id or not service_id:
        raise HTTPException(status_code=400, detail="tenantId and serviceId are required")

    try:
        config = QuickBooksRepository().getAutoSyncConfig(tenant_id, service_id)
        return {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": service_id,
            const.KEY_AUTO_SYNC_ENABLED: bool(config.get(const.KEY_AUTO_SYNC_ENABLED)),
        }
    except HTTPException:
        raise
    except Exception:
        logging.exception("QuickBooks get auto-sync config failed")
        raise _quickbooks_internal_exception()


@QBO_API.post("/extsvc/api/v1/quickbooks/auto-sync-config")
def save_quickbooks_auto_sync_config(req: QuickBooksAutoSyncConfigRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()
    if not tenant_id or not service_id:
        raise HTTPException(status_code=400, detail="tenant_id and service_id are required")

    try:
        QuickBooksRepository().saveAutoSyncConfig(tenant_id, service_id, req.enabled)
        logger.info(
            "QuickBooks auto sync setting updated | tenant=%s service_id=%s enabled=%s",
            tenant_id,
            service_id,
            req.enabled,
        )
        safe_audit_log(
            tenant_id=tenant_id,
            action="quickbooks_auto_sync_setting_updated",
            user_id=req.object_id or request.headers.get("x-object-id") or None,
            email=req.email or request.headers.get("x-user-email") or None,
            user_role=req.user_role or request.headers.get("x-user-role") or None,
            status="success",
            context=f"enabled={str(req.enabled).lower()}",
            resource_type="quickbooks_service",
        )
        return {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": service_id,
            const.KEY_AUTO_SYNC_ENABLED: req.enabled,
        }
    except HTTPException:
        raise
    except Exception:
        logging.exception("QuickBooks save auto-sync config failed")
        raise _quickbooks_internal_exception()


@QBO_API.post("/extsvc/api/v1/quickbooks/disconnect")
def quickbooks_disconnect(req: QuickBooksRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")

    object_id = request.headers.get("x-object-id", "") or None
    email = request.headers.get("x-user-email", "") or None
    user_role = request.headers.get("x-user-role", "") or None

    from source.service.QuickBooksService import QuickBooksService

    service = QuickBooksService()
    try:
        result = service.disconnect(tenant_id, service_id)
        # [AUDIT] Disconnect succeeded — isolated, never raises.
        safe_audit_log(
            tenant_id=tenant_id,
            action="quickbooks_disconnected",
            user_id=object_id,
            email=email,
            user_role=user_role,
            status="success",
            resource_type="quickbooks_auth",
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("QuickBooks disconnect failed")
        # [AUDIT] Disconnect failed — isolated, never raises.
        safe_audit_log(
            tenant_id=tenant_id,
            action="quickbooks_disconnect_failed",
            user_id=object_id,
            email=email,
            user_role=user_role,
            status="failed",
            context=str(e)[:64],
            resource_type="quickbooks_auth",
        )
        raise _quickbooks_internal_exception()


# QuickBooks Time OAuth routes are intentionally not registered.
# The product uses the normal QuickBooks Online Accounting connection only.


@QBO_API.get("/extsvc/api/v1/quickbooks/projects/access-test")
def quickbooks_project_access_test(tenantId: str, serviceId: str):
    """Read-only smoke test for QuickBooks Projects GraphQL entitlement."""
    _require_opt_in_endpoint("QBO_INTERNAL_DIAGNOSTICS_ENABLED")
    tenant_id = (tenantId or "").strip()
    service_id = str(serviceId or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")

    from source.quickbooks.QuickBooksProjectAccessTestService import QuickBooksProjectAccessTestService

    try:
        return QuickBooksProjectAccessTestService().check_project_graphql_access(tenant_id, service_id)
    except QuickBooksReconnectRequiredError as exc:
        return _qbo_reconnect_response(exc)
    except Exception as exc:
        logger.exception("QuickBooks project access smoke test failed")
        raise _quickbooks_internal_exception()


@QBO_API.get("/extsvc/api/v1/quickbooks/payroll/company-info")
def quickbooks_payroll_company_info(tenantId: str, serviceId: str):
    """Read-only production payroll smoke test step 1: verify QBO company access."""
    _require_opt_in_endpoint("QBO_INTERNAL_DIAGNOSTICS_ENABLED")
    tenant_id = (tenantId or "").strip()
    service_id = str(serviceId or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")

    from source.quickbooks.QuickBooksPayrollTestService import QuickBooksPayrollTestService

    try:
        return QuickBooksPayrollTestService().company_info(tenant_id, service_id)
    except QuickBooksReconnectRequiredError as exc:
        return _qbo_reconnect_response(exc)
    except Exception as exc:
        logger.exception("QuickBooks payroll company-info smoke test failed")
        raise _quickbooks_internal_exception()


@QBO_API.get("/extsvc/api/v1/quickbooks/payroll/employees")
def quickbooks_payroll_employees(tenantId: str, serviceId: str, maxResults: int = 50):
    """Read-only production payroll smoke test step 2: list QBO employees."""
    _require_opt_in_endpoint("QBO_INTERNAL_DIAGNOSTICS_ENABLED")
    tenant_id = (tenantId or "").strip()
    service_id = str(serviceId or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")

    from source.quickbooks.QuickBooksPayrollTestService import QuickBooksPayrollTestService

    try:
        return QuickBooksPayrollTestService().employees(tenant_id, service_id, max_results=maxResults)
    except QuickBooksReconnectRequiredError as exc:
        return _qbo_reconnect_response(exc)
    except Exception as exc:
        logger.exception("QuickBooks payroll employees smoke test failed")
        raise _quickbooks_internal_exception()


@QBO_API.get("/extsvc/api/v1/quickbooks/payroll/employee-compensations")
def quickbooks_payroll_employee_compensations(tenantId: str, serviceId: str, qboEmployeeId: str):
    """Read-only production payroll smoke test step 3: get employee compensation IDs."""
    _require_opt_in_endpoint("QBO_INTERNAL_DIAGNOSTICS_ENABLED")
    tenant_id = (tenantId or "").strip()
    service_id = str(serviceId or "").strip()
    employee_id = str(qboEmployeeId or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")
    if not employee_id:
        raise HTTPException(status_code=400, detail="qboEmployeeId is required")

    from source.quickbooks.QuickBooksPayrollTestService import QuickBooksPayrollTestService

    try:
        return QuickBooksPayrollTestService().employee_compensations(tenant_id, service_id, employee_id)
    except QuickBooksReconnectRequiredError as exc:
        return _qbo_reconnect_response(exc)
    except Exception as exc:
        logger.exception("QuickBooks payroll employee-compensations smoke test failed")
        raise _quickbooks_internal_exception()




# Classic QuickBooks Time/TSheets diagnostic and write routes are intentionally disabled.


@QBO_API.post(
    "/extsvc/api/v1/quickbooks/customers/create",
    summary="Create or update one QuickBooks customer from a pending FIELDCRAFT customer mapping",
    description=(
        "Internal real-time customer export endpoint. The application sends the current "
        "customer_external_map id after the customer mapping becomes eligible for QuickBooks sync."
    ),
)
def create_customer(req: QuickBooksCustomerCreateRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if req.customer_external_map_id is None:
        raise HTTPException(status_code=400, detail="customer_external_map_id is required")

    skip = _auto_sync_skip_response(
        tenant_id=tenant_id,
        service_id=req.service_id,
        module="customers",
    )
    if skip:
        return skip

    object_id = request.headers.get("x-object-id", "")
    email = request.headers.get("x-user-email", "")
    user_role = request.headers.get("x-user-role", "")

    from source.quickbooks.QuickBooksCustomerCreateService import QuickBooksCustomerCreateService

    try:
        return QuickBooksCustomerCreateService().create_customer(
            tenant_id=tenant_id,
            service_id=req.service_id,
            customer_external_map_id=int(req.customer_external_map_id),
            object_id=object_id,
            email=email,
            user_role=user_role,
        )
    except QuickBooksReconnectRequiredError as exc:
        return _qbo_reconnect_response(exc)
    except Exception:
        logging.exception("QuickBooks create_customer failed")
        raise _quickbooks_internal_exception()


@QBO_API.post("/extsvc/api/v1/quickbooks/customers/export")
def export_customers(req: QuickBooksCustomerExportRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")

    object_id = request.headers.get("x-object-id") or None
    email = request.headers.get("x-user-email") or None
    user_role = request.headers.get("x-user-role") or None

    safe_audit_log(
        tenant_id=tenant_id,
        action="customer_export_started",
        user_id=object_id,
        email=email,
        user_role=user_role,
        status="started",
        resource_type="quickbooks_service",
    )

    from source.quickbooks.QuickBooksCustomerExportService import QuickBooksCustomerExportService

    exporter = QuickBooksCustomerExportService()
    try:
        return exporter.export_customers(
            tenant_id=tenant_id, service_id=service_id, limit=200, offset=0,
        )
    except Exception:
        logging.exception("QuickBooks export_customers failed")
        raise HTTPException(
            status_code=500,
            detail="QuickBooks customers could not be exported. Please retry or contact support.",
        )


@QBO_API.post("/extsvc/api/v1/quickbooks/customers/import")
def import_customers(req: QuickBooksCustomerImportRequest, request: Request):
    """Import QuickBooks customers into FIELDCRAFT.

    Production requirement: this endpoint needs the Tenant API bearer token (UI token) to create tenant users.
    """

    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")

    ui_token = get_bearer_token(request) or ""
    object_id = request.headers.get("x-object-id") or None
    email = request.headers.get("x-user-email") or None
    user_role = request.headers.get("x-user-role") or None

    safe_audit_log(
        tenant_id=tenant_id,
        action="customer_import_started",
        user_id=object_id,
        email=email,
        user_role=user_role,
        status="started",
        resource_type="quickbooks_service",
    )

    from source.quickbooks.QuickBooksCustomerImportService import QuickBooksCustomerImportService

    importer = QuickBooksCustomerImportService()
    try:
        return importer.import_customers(
            tenant_id=tenant_id,
            service_id=service_id,
            tenant_api_token=ui_token,
            page_size=100,
            start_position=1,
            max_customers=200,
            dry_run=False,
        )
    except Exception:
        logging.exception("QuickBooks import_customers failed")
        raise HTTPException(
            status_code=500,
            detail="QuickBooks customers could not be imported. Please retry or contact support.",
        )




@QBO_API.get("/extsvc/api/v1/quickbooks/customers/state")
def customer_state(tenantId: str, serviceId: str, limit: int = 200, offset: int = 0):
    tenant_id = (tenantId or "").strip()
    service_id = (serviceId or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")

    from source.dal.CustomerRepository import CustomerRepository

    repo = CustomerRepository()
    try:
        return {
            "ok": True,
            "tenantId": tenant_id,
            "serviceId": service_id,
            "customers": repo.get_customer_sync_state(tenant_id=tenant_id, service_id=service_id, limit=limit, offset=offset),
        }
    except Exception:
        logging.exception("QuickBooks customer_state failed")
        raise HTTPException(
            status_code=500,
            detail="Customer sync status could not be loaded. Please retry or contact support.",
        )


@QBO_API.post("/extsvc/api/v1/quickbooks/customers/sync")
def sync_customers(payload: QuickBooksSyncJobRequest, request: Request, background_tasks: BackgroundTasks):
    tenant_id = (payload.tenant_id or "").strip()
    service_id = str(payload.service_id or "").strip()
    object_id = (payload.object_id or "").strip()

    if not tenant_id or not service_id or not object_id:
        raise HTTPException(status_code=400, detail="tenant_id, service_id and object_id are required")

    token = get_bearer_token(request) or ""
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    safe_audit_log(
        tenant_id=tenant_id,
        action="customer_sync_started",
        user_id=object_id or None,
        email=request.headers.get("x-user-email") or None,
        user_role=request.headers.get("x-user-role") or None,
        status="started",
        resource_type="quickbooks_service",
    )

    try:
        return start_sync_job(
            module="customers",
            tenant_id=tenant_id,
            service_id=service_id,
            object_id=object_id,
            background_tasks=background_tasks,
            tenant_api_token=token,
            sync_mode=payload.sync_mode,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unable to start customer sync: {e}")


@QBO_API.post("/extsvc/api/v1/quickbooks/payments/create")
def create_payment(req: QuickBooksPaymentCreateRequest):
    payment_receives_id = req.payment_receives_id
    tenant_id = (req.tenant_id or "").strip()
    customer_uid = (req.customer_uid or "").strip()
    txn_date = (req.txn_date or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not customer_uid:
        raise HTTPException(status_code=400, detail="customer_uid is required")
    if not txn_date:
        raise HTTPException(status_code=400, detail="txn_date is required")
    if payment_receives_id is None:
        raise HTTPException(status_code=400, detail="payment_receives_id is required")

    skip = _auto_sync_skip_response(
        tenant_id=tenant_id,
        service_id=None,
        module="payments",
    )
    if skip:
        return skip

    from source.quickbooks.QuickBooksPaymentCreateService import QuickBooksPaymentCreateService

    svc = QuickBooksPaymentCreateService()
    try:
        return svc.create_payment(
            tenant_id=tenant_id,
            service_id=None,
            customer_uid=customer_uid,
            txn_date=txn_date,
            total_amount=float(req.total_amount),
            payment_ref_num=req.payment_ref_num,
            private_note=req.private_note,
            payment_receives_id=int(payment_receives_id),
        )
    except Exception as e:
        logging.exception("QuickBooks create_payment failed")
        raise _quickbooks_internal_exception()




@QBO_API.post("/extsvc/api/v1/quickbooks/bills/create")
def create_bill(req: QuickBooksBillCreateRequest):
    tenant_id = (req.tenant_id or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if req.receipt_external_map_id is None:
        raise HTTPException(status_code=400, detail="receipt_external_map_id is required")

    skip = _auto_sync_skip_response(
        tenant_id=tenant_id,
        service_id=req.service_id,
        module="bills",
    )
    if skip:
        return skip

    if req.service_id:
        try:
            QuickBooksCapabilityService().require(tenant_id, str(req.service_id), const.CAPABILITY_BILLS)
        except QBOFeatureUnavailableError as exc:
            return _feature_not_available_response(exc)

    from source.quickbooks.QuickBooksBillCreateService import QuickBooksBillCreateService

    svc = QuickBooksBillCreateService()
    try:
        return svc.create_bill(
            tenant_id=tenant_id,
            service_id=req.service_id,
            receipt_external_map_id=int(req.receipt_external_map_id),
            vendor_external_map_id=(
                int(req.vendor_external_map_id)
                if req.vendor_external_map_id is not None
                else None
            ),
        )
    except Exception:
        logging.exception("QuickBooks create_bill failed")
        raise HTTPException(
            status_code=500,
            detail="The bill could not be exported because of an unexpected server error. Retry once, then contact support with the sync job details.",
        )


@QBO_API.get(
    "/extsvc/api/v1/quickbooks/payments/cdc-view",
    summary="Get QuickBooks customer payment sync view",
    description=(
        "Protected endpoint for tenant web to fetch paginated FIELDCRAFT customer receipts and "
        "payment_external_map sync state."
    ),
)
def payment_cdc_view(
    tenantId: str,
    serviceId: str,
    page: int = 1,
    pageSize: int = 25,
    search: Optional[str] = None,
    syncStatus: Optional[str] = None,
):
    tenantId = (tenantId or "").strip()
    serviceId = (serviceId or "").strip()
    if not tenantId or not serviceId:
        raise HTTPException(status_code=400, detail="tenantId and serviceId are required")
    try:
        from source.dal.CustomerPaymentRepository import CustomerPaymentRepository

        payload = CustomerPaymentRepository().get_payment_cdc_page(
            tenant_id=tenantId,
            service_id=serviceId,
            page=page,
            page_size=pageSize,
            search=search,
            sync_status=syncStatus,
        )
        return {
            "ok": True,
            "tenantId": tenantId,
            "serviceId": serviceId,
            **payload,
        }
    except HTTPException:
        raise
    except Exception:
        logging.exception("QuickBooks customer payment CDC view failed")
        raise _quickbooks_internal_exception()


@QBO_API.get(
    "/extsvc/api/v1/quickbooks/payments/{mapping_id}/details",
    summary="Get QuickBooks customer payment details",
)
def payment_detail_view(mapping_id: int, tenantId: str, serviceId: str):
    tenant_id = (tenantId or "").strip()
    service_id = (serviceId or "").strip()
    if not tenant_id or not service_id:
        raise HTTPException(status_code=400, detail="tenantId and serviceId are required")
    try:
        from source.dal.CustomerPaymentRepository import CustomerPaymentRepository

        payload = CustomerPaymentRepository().get_payment_detail(
            tenant_id=tenant_id,
            service_id=service_id,
            mapping_id=mapping_id,
        )
        if payload is None:
            raise HTTPException(status_code=404, detail="Customer payment mapping was not found.")
        return {"ok": True, "tenantId": tenant_id, "serviceId": service_id, **payload}
    except HTTPException:
        raise
    except Exception:
        logging.exception("QuickBooks customer payment detail view failed")
        raise _quickbooks_internal_exception()


@QBO_API.post("/extsvc/api/v1/quickbooks/payments/export")
def export_payments(req: QuickBooksPaymentsExportRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")

    object_id = request.headers.get("x-object-id") or None
    email = request.headers.get("x-user-email") or None
    user_role = request.headers.get("x-user-role") or None

    safe_audit_log(
        tenant_id=tenant_id,
        action="payment_export_started",
        user_id=object_id,
        email=email,
        user_role=user_role,
        status="started",
        resource_type="quickbooks_service",
    )

    from source.quickbooks.QuickBooksPaymentExportService import QuickBooksPaymentExportService

    svc = QuickBooksPaymentExportService()
    try:
        result = svc.export_payments(
            tenant_id=tenant_id,
            service_id=service_id,
            requested_max_payments=int(req.requested_max_payments),
            dry_run=bool(req.dry_run),
        )
        logger.debug(
            "[API.export_payments] service_result | ok=%s processed=%s exported=%s failed=%s",
            bool((result or {}).get(const.KEY_OK, True)),
            (result or {}).get("processedPayments"),
            (result or {}).get("exported"),
            (result or {}).get("failed"),
        )
        _record_last_sync(
            result,
            tenant_id=tenant_id,
            service_id=service_id,
            setter_name="setLastPaymentSyncAt",
        )

        return result
    except Exception as e:
        logging.exception("QuickBooks export_payments failed")
        raise _quickbooks_internal_exception()


@QBO_API.post("/extsvc/api/v1/quickbooks/payments/import")
def import_payments(req: QuickBooksPaymentsImportRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")

    object_id = request.headers.get("x-object-id") or None
    email = request.headers.get("x-user-email") or None
    user_role = request.headers.get("x-user-role") or None

    safe_audit_log(
        tenant_id=tenant_id,
        action="payment_import_started",
        user_id=object_id,
        email=email,
        user_role=user_role,
        status="started",
        resource_type="quickbooks_service",
    )

    from source.quickbooks.QuickBooksPaymentImportService import QuickBooksPaymentImportService

    svc = QuickBooksPaymentImportService()
    try:
        result = svc.import_payments(
            tenant_id=tenant_id,
            service_id=service_id,
            page_size=int(req.page_size),
            start_position=int(req.start_position),
            max_payments=int(req.requested_max),
            dry_run=bool(req.dry_run),
        )
        logger.debug(
            "[API.import_payments] service_result | ok=%s processed=%s exported=%s failed=%s",
            bool((result or {}).get(const.KEY_OK, True)),
            (result or {}).get("processed"),
            (result or {}).get("exported"),
            (result or {}).get("failed"),
        )
        _record_last_sync(
            result,
            tenant_id=tenant_id,
            service_id=service_id,
            setter_name="setLastPaymentSyncAt",
        )

        return result
    except Exception as e:
        logging.exception("QuickBooks import_payments failed")
        raise _quickbooks_internal_exception()


@QBO_API.get("/extsvc/api/v1/quickbooks/bills/cdc-view")
def bill_cdc_view(
    tenantId: str,
    serviceId: str,
    page: int = 1,
    pageSize: int = 25,
    search: Optional[str] = None,
    syncStatus: Optional[str] = None,
    mappedOnly: bool = False,
    projectSearch: Optional[str] = None,
    sortBy: Optional[str] = None,
    sortDirection: Optional[str] = None,
):
    try:
        from source.dal.ReceiptRepository import ReceiptRepository
        repo = ReceiptRepository()
        payload = repo.get_bill_cdc_page(
            tenant_id=tenantId,
            service_id=serviceId,
            page=page,
            page_size=pageSize,
            search=search,
            sync_status=syncStatus,
            mapped_only=mappedOnly,
            project_search=projectSearch,
            sort_by=sortBy,
            sort_direction=sortDirection,
        )
        return {
            "ok": True,
            "tenantId": tenantId,
            "serviceId": serviceId,
            **payload,
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("QuickBooks bill CDC view failed")
        raise _quickbooks_internal_exception()


@QBO_API.get("/extsvc/api/v1/quickbooks/bills/{receipt_id}/line-items")
def bill_line_items(receipt_id: int, tenantId: str, serviceId: str):
    try:
        service_id = (serviceId or "").strip()
        if not service_id:
            raise HTTPException(status_code=400, detail="serviceId is required")
        from source.quickbooks.QuickBooksBillService import QuickBooksBillService
        svc = QuickBooksBillService()
        line_items = svc.get_bill_line_items(tenantId, receipt_id, service_id)
        return {"ok": True, "receiptId": receipt_id, "lineItems": line_items}
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("QuickBooks bill line items failed")
        raise _quickbooks_internal_exception()


@QBO_API.get("/extsvc/api/v1/quickbooks/bills/{receipt_id}/document-info")
def bill_document_info(receipt_id: int, tenantId: str, serviceId: str):
    try:
        service_id = (serviceId or "").strip()
        if not service_id:
            raise HTTPException(status_code=400, detail="serviceId is required")
        from source.quickbooks.QuickBooksBillService import QuickBooksBillService
        info = QuickBooksBillService().get_bill_document_info(tenantId, receipt_id, service_id)
        if not info:
            return {"ok": True, "receiptId": receipt_id, "hasDocument": False}
        return {
            "ok": True,
            "receiptId": receipt_id,
            "hasDocument": True,
            "documentId": info["id"],
            "name": info["name"],
            "mimeType": info["mime_type"],
            "size": info["size"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("QuickBooks bill document info failed")
        raise _quickbooks_internal_exception()


@QBO_API.get("/extsvc/api/v1/quickbooks/bills/{receipt_id}/document")
def bill_document_download(receipt_id: int, tenantId: str, serviceId: str):
    try:
        service_id = (serviceId or "").strip()
        if not service_id:
            raise HTTPException(status_code=400, detail="serviceId is required")
        from fastapi.responses import Response
        from source.quickbooks.QuickBooksBillService import QuickBooksBillService
        file_bytes, file_name, mime_type = QuickBooksBillService().get_bill_document_bytes(tenantId, receipt_id, service_id)
        safe_name = _safe_content_disposition_filename(file_name)
        safe_audit_log(
            tenant_id=tenantId,
            action="bill_document_downloaded",
            status="success",
            context=f"receiptId={receipt_id}",
            resource_type="quickbooks_bill_document",
        )
        return Response(
            content=file_bytes,
            media_type=mime_type,
            headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="Bill document not found.")
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("QuickBooks bill document download failed")
        raise _quickbooks_internal_exception()


@QBO_API.post("/extsvc/api/v1/quickbooks/bills/export")
def export_bills(req: QuickBooksBillsExportRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")
    try:
        QuickBooksCapabilityService().require(tenant_id, service_id, const.CAPABILITY_BILLS)
    except QBOFeatureUnavailableError as exc:
        return _feature_not_available_response(exc)
    object_id = request.headers.get("x-object-id", "")
    email = request.headers.get("x-user-email", "")
    user_role = request.headers.get("x-user-role", "")
    from source.quickbooks.QuickBooksBillExportService import QuickBooksBillExportService
    svc = QuickBooksBillExportService()
    try:
        result = svc.export_bills(
            tenant_id=tenant_id, service_id=service_id, limit=int(req.limit), offset=int(req.offset),
            object_id=object_id, email=email, user_role=user_role,
        )
        _record_last_sync(
            result,
            tenant_id=tenant_id,
            service_id=service_id,
            setter_name="setLastBillSyncAt",
        )
        return result
    except Exception as e:
        logging.exception("QuickBooks export_bills failed")
        raise _quickbooks_internal_exception()


@QBO_API.post("/extsvc/api/v1/quickbooks/bills/import")
def import_bills(req: QuickBooksBillsImportRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")
    try:
        QuickBooksCapabilityService().require(tenant_id, service_id, const.CAPABILITY_BILLS)
    except QBOFeatureUnavailableError as exc:
        return _feature_not_available_response(exc)
    object_id = request.headers.get("x-object-id", "")
    email = request.headers.get("x-user-email", "")
    user_role = request.headers.get("x-user-role", "")
    from source.quickbooks.QuickBooksBillImportService import QuickBooksBillImportService
    svc = QuickBooksBillImportService()
    try:
        result = svc.import_bills(
            tenant_id=tenant_id,
            service_id=service_id,
            page_size=int(req.page_size),
            start_position=int(req.start_position),
            max_bills=int(req.requested_max),
            dry_run=bool(req.dry_run),
            changed_since_override=req.changed_since_override,
            force_full=bool(req.force_full),
            object_id=object_id,
            email=email,
            user_role=user_role,
        )
        _record_last_sync(
            result,
            tenant_id=tenant_id,
            service_id=service_id,
            setter_name="setLastBillSyncAt",
        )
        return result
    except Exception as e:
        logging.exception("QuickBooks import_bills failed")
        raise _quickbooks_internal_exception()


@QBO_API.post("/extsvc/api/v1/quickbooks/bills/sync")
def sync_bills(payload: QuickBooksSyncJobRequest, request: Request, background_tasks: BackgroundTasks):
    tenant_id = (payload.tenant_id or "").strip()
    service_id = str(payload.service_id or "").strip()
    object_id = (payload.object_id or "").strip()
    if not tenant_id or not service_id or not object_id:
        raise HTTPException(status_code=400, detail="tenant_id, service_id and object_id are required")
    try:
        QuickBooksCapabilityService().require(tenant_id, service_id, const.CAPABILITY_BILLS)
    except QBOFeatureUnavailableError as exc:
        return _feature_not_available_response(exc)
    safe_audit_log(
        tenant_id=tenant_id,
        action="bill_sync_started",
        user_id=object_id or None,
        email=request.headers.get("x-user-email") or None,
        user_role=request.headers.get("x-user-role") or None,
        status="started",
        resource_type="quickbooks_service",
    )
    try:
        return start_sync_job(
            module="bills",
            tenant_id=tenant_id,
            service_id=service_id,
            object_id=object_id,
            background_tasks=background_tasks,
            tenant_api_token="",
            sync_mode=payload.sync_mode,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unable to start bill sync: {e}")


@QBO_API.post("/extsvc/api/v1/quickbooks/bill-payments/export")
def export_bill_payments(req: QuickBooksBillPaymentsExportRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")
    try:
        QuickBooksCapabilityService().require(tenant_id, service_id, const.CAPABILITY_BILLS)
    except QBOFeatureUnavailableError as exc:
        return _feature_not_available_response(exc)
    from source.quickbooks.QuickBooksBillPaymentExportService import QuickBooksBillPaymentExportService
    svc = QuickBooksBillPaymentExportService()
    try:
        return svc.export_bill_payments(
            tenant_id=tenant_id,
            service_id=service_id,
            limit=int(req.limit),
            offset=int(req.offset),
            object_id=request.headers.get("x-object-id") or req.object_id,
            email=request.headers.get("x-user-email") or req.email,
            user_role=request.headers.get("x-user-role") or req.user_role,
        )
    except Exception as e:
        logging.exception("QuickBooks export_bill_payments failed")
        raise _quickbooks_internal_exception()


@QBO_API.post("/extsvc/api/v1/quickbooks/bill-payments/import")
def import_bill_payments(req: QuickBooksBillPaymentsImportRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")
    try:
        QuickBooksCapabilityService().require(tenant_id, service_id, const.CAPABILITY_BILLS)
    except QBOFeatureUnavailableError as exc:
        return _feature_not_available_response(exc)
    from source.quickbooks.QuickBooksBillPaymentImportService import QuickBooksBillPaymentImportService
    svc = QuickBooksBillPaymentImportService()
    try:
        return svc.import_bill_payments(
            tenant_id=tenant_id,
            service_id=service_id,
            page_size=int(req.page_size),
            start_position=int(req.start_position),
            max_payments=int(req.requested_max),
            dry_run=bool(req.dry_run),
            object_id=request.headers.get("x-object-id") or req.object_id,
            email=request.headers.get("x-user-email") or req.email,
            user_role=request.headers.get("x-user-role") or req.user_role,
        )
    except Exception as e:
        logging.exception("QuickBooks import_bill_payments failed")
        raise _quickbooks_internal_exception()


@QBO_API.post("/extsvc/api/v1/quickbooks/bill-payments/sync")
def sync_bill_payments(payload: QuickBooksSyncJobRequest, request: Request, background_tasks: BackgroundTasks):
    tenant_id = (payload.tenant_id or "").strip()
    service_id = str(payload.service_id or "").strip()
    object_id = (payload.object_id or "").strip()
    if not tenant_id or not service_id or not object_id:
        raise HTTPException(status_code=400, detail="tenant_id, service_id and object_id are required")
    try:
        QuickBooksCapabilityService().require(tenant_id, service_id, const.CAPABILITY_BILLS)
    except QBOFeatureUnavailableError as exc:
        return _feature_not_available_response(exc)
    token = get_bearer_token(request) or ""
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    try:
        return start_sync_job(module="bill_payments", tenant_id=tenant_id, service_id=service_id, object_id=object_id, background_tasks=background_tasks, tenant_api_token=token, sync_mode=payload.sync_mode)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unable to start bill payment sync: {e}")


@QBO_API.post("/extsvc/api/v1/quickbooks/payments/sync")
def sync_payments(payload: QuickBooksSyncJobRequest, request: Request, background_tasks: BackgroundTasks):
    tenant_id = (payload.tenant_id or "").strip()
    service_id = str(payload.service_id or "").strip()
    object_id = (payload.object_id or "").strip()

    if not tenant_id or not service_id or not object_id:
        raise HTTPException(status_code=400, detail="tenant_id, service_id and object_id are required")

    safe_audit_log(
        tenant_id=tenant_id,
        action="payment_sync_started",
        user_id=object_id or None,
        email=request.headers.get("x-user-email") or None,
        user_role=request.headers.get("x-user-role") or None,
        status="started",
        resource_type="quickbooks_service",
    )

    try:
        return start_sync_job(
            module="payments",
            tenant_id=tenant_id,
            service_id=service_id,
            object_id=object_id,
            background_tasks=background_tasks,
            tenant_api_token="",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unable to start payment sync: {e}")



@QBO_API.post(
    "/extsvc/api/v1/quickbooks/vendors/create",
    summary="Create or update one QuickBooks vendor from a pending FIELDCRAFT vendor mapping",
    description=(
        "Internal real-time vendor export endpoint. The application sends the current "
        "vendor_external_map id after the vendor mapping becomes eligible for QuickBooks sync."
    ),
)
def create_vendor(req: QuickBooksVendorCreateRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if req.vendor_external_map_id is None:
        raise HTTPException(status_code=400, detail="vendor_external_map_id is required")

    skip = _auto_sync_skip_response(
        tenant_id=tenant_id,
        service_id=req.service_id,
        module="vendors",
    )
    if skip:
        return skip

    object_id = request.headers.get("x-object-id", "")
    email = request.headers.get("x-user-email", "")
    user_role = request.headers.get("x-user-role", "")

    from source.quickbooks.QuickBooksVendorCreateService import QuickBooksVendorCreateService

    try:
        return QuickBooksVendorCreateService().create_vendor(
            tenant_id=tenant_id,
            service_id=req.service_id,
            vendor_external_map_id=int(req.vendor_external_map_id),
            object_id=object_id,
            email=email,
            user_role=user_role,
        )
    except QuickBooksReconnectRequiredError as exc:
        return _qbo_reconnect_response(exc)
    except Exception:
        logging.exception("QuickBooks create_vendor failed")
        raise _quickbooks_internal_exception()


@QBO_API.post("/extsvc/api/v1/quickbooks/vendors/export")
def export_vendors(req: QuickBooksVendorExportRequest, request: Request):
    """Export FIELDCRAFT vendors (tenant DB) -> QuickBooks vendors.

    Stores mappings in tenant_db.vendor_external_map.
    """

    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")

    from source.quickbooks.QuickBooksVendorExportService import QuickBooksVendorExportService

    object_id = request.headers.get("x-object-id", "")
    email = request.headers.get("x-user-email", "")
    user_role = request.headers.get("x-user-role", "")

    svc = QuickBooksVendorExportService()
    try:
        return svc.export_vendors(
            tenant_id=tenant_id,
            service_id=service_id,
            limit=int(req.limit or 200),
            offset=int(req.offset or 0),
            object_id=object_id,
            email=email,
            user_role=user_role,
        )
    except QuickBooksReconnectRequiredError as exc:
        return _qbo_reconnect_response(exc)
    except Exception:
        logging.exception("QuickBooks export_vendors failed")
        raise HTTPException(status_code=500, detail="QuickBooks vendor export failed.")

@QBO_API.post("/extsvc/api/v1/quickbooks/vendors/import")
def import_vendors(req: QuickBooksVendorImportRequest, request: Request):
    """Import QuickBooks vendors -> FIELDCRAFT tenant DB."""

    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")

    object_id = request.headers.get("x-object-id", "")
    email = request.headers.get("x-user-email", "")
    user_role = request.headers.get("x-user-role", "")

    from source.quickbooks.QuickBooksVendorImportService import QuickBooksVendorImportService

    svc = QuickBooksVendorImportService()
    try:
        result = svc.import_vendors(
            tenant_id=tenant_id,
            service_id=service_id,
            page_size=int(req.page_size),
            start_position=int(req.start_position),
            max_vendors=int(req.requested_max),
            dry_run=bool(req.dry_run),
            changed_since_override=req.changed_since_override,
            force_full=bool(req.force_full),
            object_id=object_id,
            email=email,
            user_role=user_role,
        )
        logger.debug(
            "[API.import_vendors] service_result | ok=%s processed=%s exported=%s failed=%s",
            bool((result or {}).get(const.KEY_OK, True)),
            (result or {}).get("processed"),
            (result or {}).get("exported"),
            (result or {}).get("failed"),
        )
        _record_last_sync(
            result,
            tenant_id=tenant_id,
            service_id=service_id,
            setter_name="setLastVendorSyncAt",
        )
        return result
    except QuickBooksReconnectRequiredError as exc:
        return _qbo_reconnect_response(exc)
    except Exception:
        logging.exception("QuickBooks import_vendors failed")
        raise HTTPException(status_code=500, detail="QuickBooks vendor import failed.")


@QBO_API.post("/extsvc/api/v1/quickbooks/vendors/sync")
def sync_vendors(payload: QuickBooksSyncJobRequest, request: Request, background_tasks: BackgroundTasks):
    tenant_id = (payload.tenant_id or "").strip()
    service_id = str(payload.service_id or "").strip()
    object_id = (payload.object_id or "").strip()
    sync_mode = (payload.sync_mode or "").strip().lower() or None

    if not tenant_id or not service_id or not object_id:
        raise HTTPException(status_code=400, detail="tenant_id, service_id and object_id are required")

    safe_audit_log(
        tenant_id=tenant_id,
        action="vendor_sync_started",
        user_id=object_id or None,
        email=request.headers.get("x-user-email") or None,
        user_role=request.headers.get("x-user-role") or None,
        status="started",
        resource_type="quickbooks_service",
    )

    try:
        return start_sync_job(
            module="vendors",
            tenant_id=tenant_id,
            service_id=service_id,
            object_id=object_id,
            background_tasks=background_tasks,
            tenant_api_token="",
            sync_mode=sync_mode,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unable to start vendor sync: {e}")




@QBO_API.post("/extsvc/api/v1/quickbooks/employees/export")
def export_employees(req: QuickBooksEmployeeExportRequest, request: Request):
    """Export FIELDCRAFT workers to QuickBooks.

    W2 resources are exported as QuickBooks Employees. 1099/contractor
    resources are exported as QuickBooks Vendors with Vendor1099=true. Both
    store mappings in tenant_db.user_account_external_map.
    """

    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")

    logger.info(
        "[EmployeeAPI] export_employees started | tenant_id=%s service_id=%s limit=%s offset=%s",
        tenant_id,
        service_id,
        req.limit or 500,
        req.offset or 0,
    )

    from source.quickbooks.QuickBooksWorkerExportService import QuickBooksWorkerExportService

    object_id = req.object_id or request.headers.get("x-object-id", "")
    email = req.email or request.headers.get("x-user-email", "")
    user_role = req.user_role or request.headers.get("x-user-role", "")

    svc = QuickBooksWorkerExportService()
    try:
        result = svc.export_workers(
            tenant_id=tenant_id,
            service_id=service_id,
            limit=int(req.limit or 500),
            offset=int(req.offset or 0),
            object_id=object_id,
            email=email,
            user_role=user_role,
        )
        try:
            if bool((result or {}).get(const.KEY_OK, True)):
                QuickBooksRepository().setLastEmployeeSyncAt(tenant_id, service_id)
                logger.info("[EmployeeAPI] lastEmployeeSyncAt updated")
        except Exception as exc:
            logger.exception("[EmployeeAPI] failed to update lastEmployeeSyncAt")
        logger.info(
            "[EmployeeAPI] export_employees completed | ok=%s requested=%s created=%s updated=%s failed=%s",
            (result or {}).get(const.KEY_OK),
            (result or {}).get("requested"),
            (result or {}).get("createdInQbo"),
            (result or {}).get("updatedInQbo"),
            (result or {}).get("failed"),
        )
        return result
    except QuickBooksReconnectRequiredError as exc:
        logger.warning("[EmployeeAPI] export_employees reconnect required | error=%s", exc)
        return _qbo_reconnect_response(exc)
    except Exception as exc:
        logger.exception("QuickBooks export_employees failed")
        raise HTTPException(status_code=500, detail="QuickBooks employee export failed.")


@QBO_API.post("/extsvc/api/v1/quickbooks/employees/import")
def import_employees(req: QuickBooksEmployeeImportRequest, request: Request):
    """Import QuickBooks employees into FIELDCRAFT through the Tenant API."""

    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")

    ui_token = get_bearer_token(request) or ""
    if not ui_token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    object_id = req.object_id or request.headers.get("x-object-id", "")
    email = req.email or request.headers.get("x-user-email", "")
    user_role = req.user_role or request.headers.get("x-user-role", "")

    safe_audit_log(
        tenant_id=tenant_id,
        action="employee_import_started",
        user_id=object_id or None,
        email=email or None,
        user_role=user_role or None,
        status="started",
        resource_type="quickbooks_service",
    )

    from source.quickbooks.QuickBooksEmployeeImportService import QuickBooksEmployeeImportService

    svc = QuickBooksEmployeeImportService()
    try:
        result = svc.import_employees(
            tenant_id=tenant_id,
            service_id=service_id,
            tenant_api_token=ui_token,
            page_size=int(req.page_size or 100),
            start_position=int(req.start_position or 1),
            max_employees=int(req.requested_max or 500),
            dry_run=bool(req.dry_run),
            changed_since_override=req.changed_since_override,
            force_full=bool(req.force_full),
            object_id=object_id,
            email=email,
            user_role=user_role,
        )
        try:
            if bool((result or {}).get(const.KEY_OK, True)):
                QuickBooksRepository().setLastEmployeeSyncAt(tenant_id, service_id)
                logger.info("[EmployeeAPI] lastEmployeeSyncAt updated after import")
        except Exception as exc:
            logger.exception("[EmployeeAPI] failed to complete employee import post-processing")
        return result
    except QuickBooksReconnectRequiredError as exc:
        return _qbo_reconnect_response(exc)
    except Exception as exc:
        logger.exception("QuickBooks import_employees failed")
        raise HTTPException(status_code=500, detail="QuickBooks employee import failed.")


@QBO_API.get("/extsvc/api/v1/quickbooks/employees/sync-config")
def get_employee_sync_config(tenantId: str, serviceId: str, request: Request):
    """Return the current employee sync configuration for a tenant/service.

    Returns the stored config with safe defaults applied so the UI always
    receives a fully populated object.
    """
    tenant_id = (tenantId or "").strip()
    service_id = str(serviceId or "").strip()
    if not tenant_id or not service_id:
        raise HTTPException(status_code=400, detail="tenantId and serviceId are required")

    token = get_bearer_token(request) or ""
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    try:
        raw = QuickBooksRepository().getEmployeeSyncConfig(tenant_id, service_id)
        days_raw = raw.get(const.KEY_EMPLOYEE_SYNC_DAYS_AFTER_HIRE)
        configured = bool(raw)  # True only when the tenant has explicitly saved settings
        days_safe = max(1, min(int(days_raw or 1), const.EMPLOYEE_SYNC_DAYS_MAX)) if days_raw else 1
        cost_unit_hours = raw.get(const.KEY_EMPLOYEE_SYNC_COST_UNIT_HOURS) or {}
        if not isinstance(cost_unit_hours, dict):
            cost_unit_hours = {}
        merged_cost_unit_hours = dict(const.EMPLOYEE_COST_UNIT_HOURS_DEFAULT)
        for unit, hours in cost_unit_hours.items():
            try:
                hours_value = float(hours)
            except (TypeError, ValueError):
                continue
            if unit in const.EMPLOYEE_COST_UNITS_ALLOWED and hours_value > 0:
                merged_cost_unit_hours[unit] = hours_value

        default_cost_unit = str(raw.get(const.KEY_EMPLOYEE_SYNC_DEFAULT_COST_UNIT) or const.EMPLOYEE_COST_UNIT_HOURLY).strip().lower()
        if default_cost_unit not in const.EMPLOYEE_COST_UNITS_ALLOWED:
            default_cost_unit = const.EMPLOYEE_COST_UNIT_HOURLY

        cost_qbo_field = str(raw.get(const.KEY_EMPLOYEE_SYNC_COST_QBO_FIELD) or const.EMPLOYEE_COST_QBO_FIELD_COST_RATE).strip()
        if cost_qbo_field not in const.EMPLOYEE_COST_QBO_FIELDS_ALLOWED:
            cost_qbo_field = const.EMPLOYEE_COST_QBO_FIELD_COST_RATE

        return {
            const.KEY_OK: True,
            "configured": configured,
            "tenantId": tenant_id,
            "serviceId": service_id,
            const.KEY_EMPLOYEE_SYNC_DAYS_AFTER_HIRE: days_safe,
            const.KEY_EMPLOYEE_SYNC_COST_ENABLED: bool(raw.get(const.KEY_EMPLOYEE_SYNC_COST_ENABLED, True)),
            const.KEY_EMPLOYEE_SYNC_DEFAULT_COST_UNIT: default_cost_unit,
            const.KEY_EMPLOYEE_SYNC_COST_UNIT_HOURS: merged_cost_unit_hours,
            const.KEY_EMPLOYEE_SYNC_COST_QBO_FIELD: cost_qbo_field,
        }
    except Exception as exc:
        logger.exception("[EmployeeAPI] get_employee_sync_config failed")
        raise HTTPException(status_code=500, detail="Failed to retrieve employee sync configuration.")


@QBO_API.post("/extsvc/api/v1/quickbooks/employees/sync-config")
def save_employee_sync_config(req: QuickBooksEmployeeSyncConfigRequest, request: Request):
    """Save employee sync configuration for a tenant/service.

    Validates and persists ``days_after_hire`` and ``employee_type`` into
    ``additional_info["employee_sync"]``.  Applied on every subsequent sync run.
    """
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()
    if not tenant_id or not service_id:
        raise HTTPException(status_code=400, detail="tenant_id and service_id are required")

    token = get_bearer_token(request) or ""
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    # Validate days_after_hire range — minimum 1 (work must start at least 1 day after hire)
    days_after_hire = int(req.days_after_hire or 1)
    if not (1 <= days_after_hire <= const.EMPLOYEE_SYNC_DAYS_MAX):
        raise HTTPException(
            status_code=400,
            detail=f"days_after_hire must be between 1 and {const.EMPLOYEE_SYNC_DAYS_MAX}.",
        )

    default_cost_unit = str(req.default_cost_unit or const.EMPLOYEE_COST_UNIT_HOURLY).strip().lower()
    if default_cost_unit not in const.EMPLOYEE_COST_UNITS_ALLOWED:
        raise HTTPException(
            status_code=400,
            detail=f"default_cost_unit must be one of: {', '.join(const.EMPLOYEE_COST_UNITS_ALLOWED)}.",
        )

    cost_qbo_field = str(req.cost_qbo_field or const.EMPLOYEE_COST_QBO_FIELD_COST_RATE).strip()
    if cost_qbo_field not in const.EMPLOYEE_COST_QBO_FIELDS_ALLOWED:
        raise HTTPException(
            status_code=400,
            detail=f"cost_qbo_field must be one of: {', '.join(const.EMPLOYEE_COST_QBO_FIELDS_ALLOWED)}.",
        )

    cost_unit_hours = dict(const.EMPLOYEE_COST_UNIT_HOURS_DEFAULT)
    for unit, hours in (req.cost_unit_hours or {}).items():
        unit_key = str(unit or "").strip().lower()
        if unit_key not in const.EMPLOYEE_COST_UNITS_ALLOWED:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported cost unit: {unit}.",
            )
        try:
            hours_value = float(hours)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"cost_unit_hours.{unit_key} must be a number.")
        if hours_value <= 0:
            raise HTTPException(status_code=400, detail=f"cost_unit_hours.{unit_key} must be greater than zero.")
        cost_unit_hours[unit_key] = hours_value

    object_id = req.object_id or request.headers.get("x-object-id", "")
    email = req.email or request.headers.get("x-user-email", "")
    user_role = req.user_role or request.headers.get("x-user-role", "")

    safe_audit_log(
        tenant_id=tenant_id,
        action="employee_sync_config_saved",
        user_id=object_id or None,
        email=email or None,
        user_role=user_role or None,
        status="started",
        resource_type="quickbooks_service",
    )

    try:
        config = {
            const.KEY_EMPLOYEE_SYNC_DAYS_AFTER_HIRE: days_after_hire,
            const.KEY_EMPLOYEE_SYNC_COST_ENABLED: bool(req.cost_enabled),
            const.KEY_EMPLOYEE_SYNC_DEFAULT_COST_UNIT: default_cost_unit,
            const.KEY_EMPLOYEE_SYNC_COST_UNIT_HOURS: cost_unit_hours,
            const.KEY_EMPLOYEE_SYNC_COST_QBO_FIELD: cost_qbo_field,
        }
        QuickBooksRepository().saveEmployeeSyncConfig(tenant_id, service_id, config)
        return {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": service_id,
            const.KEY_EMPLOYEE_SYNC_DAYS_AFTER_HIRE: days_after_hire,
            const.KEY_EMPLOYEE_SYNC_COST_ENABLED: bool(req.cost_enabled),
            const.KEY_EMPLOYEE_SYNC_DEFAULT_COST_UNIT: default_cost_unit,
            const.KEY_EMPLOYEE_SYNC_COST_UNIT_HOURS: cost_unit_hours,
            const.KEY_EMPLOYEE_SYNC_COST_QBO_FIELD: cost_qbo_field,
        }
    except Exception as exc:
        logger.exception("[EmployeeAPI] save_employee_sync_config failed")
        raise HTTPException(status_code=500, detail="Failed to save employee sync configuration.")


@QBO_API.post("/extsvc/api/v1/quickbooks/employees/sync")
def sync_employees(payload: QuickBooksSyncJobRequest, request: Request, background_tasks: BackgroundTasks):
    tenant_id = (payload.tenant_id or "").strip()
    service_id = str(payload.service_id or "").strip()
    object_id = (payload.object_id or "").strip()
    sync_mode = (payload.sync_mode or "").strip().lower() or None

    if not tenant_id or not service_id or not object_id:
        raise HTTPException(status_code=400, detail="tenant_id, service_id and object_id are required")

    token = get_bearer_token(request) or ""
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    safe_audit_log(
        tenant_id=tenant_id,
        action="employee_sync_started",
        user_id=object_id or None,
        email=request.headers.get("x-user-email") or None,
        user_role=request.headers.get("x-user-role") or None,
        status="started",
        resource_type="quickbooks_service",
    )

    try:
        return start_sync_job(
            module="employees",
            tenant_id=tenant_id,
            service_id=service_id,
            object_id=object_id,
            background_tasks=background_tasks,
            tenant_api_token=token,
            sync_mode=sync_mode,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unable to start employee sync: {exc}")


@QBO_API.get("/extsvc/api/v1/quickbooks/accounts/cdc-view")
def account_cdc_view(
    tenantId: str,
    serviceId: str,
    page: int = 1,
    pageSize: int = 25,
    search: Optional[str] = None,
    syncStatus: Optional[str] = None,
):
    """Paginated chart-of-account CDC view for the UI.

    Status is derived per row — SYNCED if external_id is set for this service,
    PENDING_PUSH otherwise.
    """
    try:
        from source.dal.ChartOfAccountRepository import ChartOfAccountRepository
        repo = ChartOfAccountRepository()
        payload = repo.get_account_cdc_page(
            tenant_id=tenantId,
            service_id=serviceId,
            page=page,
            page_size=pageSize,
            search=search,
            sync_status=syncStatus,
        )
        return {
            "ok": True,
            "tenantId": tenantId,
            "serviceId": serviceId,
            **payload,
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("QuickBooks account CDC view failed")
        raise _quickbooks_internal_exception()



@QBO_API.post("/extsvc/api/v1/quickbooks/accounts/export")
def export_accounts(req: QuickBooksAccountExportRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")

    object_id = request.headers.get("x-object-id", "")
    email = request.headers.get("x-user-email", "")
    user_role = request.headers.get("x-user-role", "")

    from source.quickbooks.QuickBooksAccountExportService import QuickBooksAccountExportService

    svc = QuickBooksAccountExportService()
    try:
        result = svc.export_accounts(
            tenant_id=tenant_id,
            service_id=service_id,
            limit=int(req.limit),
            offset=int(req.offset),
            object_id=object_id,
            email=email,
            user_role=user_role,
        )
        _record_last_sync(
            result,
            tenant_id=tenant_id,
            service_id=service_id,
            setter_name="setLastAccountSyncAt",
        )
        return result
    except Exception as e:
        logging.exception("QuickBooks export_accounts failed")
        raise _quickbooks_internal_exception()



@QBO_API.post("/extsvc/api/v1/quickbooks/chartofaccounts/export")
def export_chart_of_accounts(req: QuickBooksChartOfAccountsExportRequest, request: Request):
    """Alias of /quickbooks/accounts/export (Chart of Accounts)."""

    # Reuse the same implementation to avoid drift.
    return export_accounts(
        QuickBooksAccountExportRequest(
            tenant_id=req.tenant_id,
            service_id=req.service_id,
            limit=getattr(req, "limit", 500),
            offset=getattr(req, "offset", 0),
        ),
        request,
    )


@QBO_API.post("/extsvc/api/v1/quickbooks/accounts/import")
def import_accounts(req: QuickBooksAccountImportRequest, request: Request):
    """Import QuickBooks accounts -> tenant DB chart_of_account."""

    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")

    object_id = request.headers.get("x-object-id", "")
    email = request.headers.get("x-user-email", "")
    user_role = request.headers.get("x-user-role", "")

    from source.quickbooks.QuickBooksAccountImportService import QuickBooksAccountImportService

    svc = QuickBooksAccountImportService()
    try:
        result = svc.import_accounts(
            tenant_id=tenant_id,
            service_id=service_id,
            page_size=int(req.page_size),
            start_position=int(req.start_position),
            max_accounts=int(req.requested_max),
            dry_run=bool(req.dry_run),
            object_id=object_id,
            email=email,
            user_role=user_role,
        )
        logger.debug(
            "[API.import_accounts] service_result | ok=%s processed=%s exported=%s failed=%s",
            bool((result or {}).get(const.KEY_OK, True)),
            (result or {}).get("processed"),
            (result or {}).get("exported"),
            (result or {}).get("failed"),
        )
        _record_last_sync(
            result,
            tenant_id=tenant_id,
            service_id=service_id,
            setter_name="setLastAccountSyncAt",
        )
        return result
    except Exception as e:
        logging.exception("QuickBooks import_accounts failed")
        raise _quickbooks_internal_exception()


@QBO_API.post("/extsvc/api/v1/quickbooks/chartofaccounts/import")
def import_chart_of_accounts(req: QuickBooksChartOfAccountsImportRequest, request: Request):
    """Alias of /quickbooks/accounts/import (Chart of Accounts)."""

    return import_accounts(
        QuickBooksAccountImportRequest(
            tenant_id=req.tenant_id,
            service_id=req.service_id,
            page_size=getattr(req, "page_size", 100),
            start_position=getattr(req, "start_position", 1),
            requested_max=getattr(req, "requested_max", 2000),
            dry_run=getattr(req, "dry_run", False),
        ),
        request,
    )


@QBO_API.post("/extsvc/api/v1/quickbooks/accounts/sync")
def sync_accounts(payload: QuickBooksSyncJobRequest, request: Request, background_tasks: BackgroundTasks):
    tenant_id = (payload.tenant_id or "").strip()
    service_id = str(payload.service_id or "").strip()
    object_id = (payload.object_id or "").strip()

    if not tenant_id or not service_id or not object_id:
        raise HTTPException(status_code=400, detail="tenant_id, service_id and object_id are required")

    safe_audit_log(
        tenant_id=tenant_id,
        action="account_sync_started",
        user_id=object_id or None,
        email=request.headers.get("x-user-email") or None,
        user_role=request.headers.get("x-user-role") or None,
        status="started",
        resource_type="quickbooks_service",
    )

    try:
        return start_sync_job(
            module="accounts",
            tenant_id=tenant_id,
            service_id=service_id,
            object_id=object_id,
            background_tasks=background_tasks,
            tenant_api_token="",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unable to start accounts sync: {e}")


@QBO_API.post("/extsvc/api/v1/quickbooks/chartofaccounts/sync")
def sync_chart_of_accounts(payload: QuickBooksSyncJobRequest, request: Request, background_tasks: BackgroundTasks):
    """Alias of /quickbooks/accounts/sync (Chart of Accounts)."""
    return sync_accounts(payload=payload, request=request, background_tasks=background_tasks)

@QBO_API.get(
    "/extsvc/api/v1/quickbooks/projects/cdc-view",
    summary="Get QuickBooks project sync view",
    description=(
        "Protected endpoint for tenant web to fetch paginated project/sub-customer "
        "sync state from the tenant database. Requires bearer token and api-secret."
    ),
)
def project_cdc_view(
    tenantId: str,
    serviceId: str,
    page: int = 1,
    pageSize: int = 25,
    search: Optional[str] = None,
    syncStatus: Optional[str] = None,
    mappedOnly: bool = False,
):
    """Paginated project CDC view for QuickBooks project/sub-customer sync."""
    try:
        from source.dal.ProjectRepository import ProjectRepository
        repo = ProjectRepository()
        payload = repo.get_project_cdc_page(
            tenant_id=tenantId,
            service_id=serviceId,
            page=page,
            page_size=pageSize,
            search=search,
            sync_status=syncStatus,
            mapped_only=mappedOnly,
        )
        return {
            "ok": True,
            "tenantId": tenantId,
            "serviceId": serviceId,
            **payload,
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("QuickBooks project CDC view failed")
        raise _quickbooks_internal_exception()


@QBO_API.get(
    "/extsvc/api/v1/quickbooks/projects/sync-config",
    summary="Get QuickBooks project sync configuration",
)
def get_project_sync_config(tenantId: str, serviceId: str, request: Request):
    tenant_id = (tenantId or "").strip()
    service_id = str(serviceId or "").strip()
    if not tenant_id or not service_id:
        raise HTTPException(status_code=400, detail="tenantId and serviceId are required")
    if not get_bearer_token(request):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    try:
        return _project_sync_config_payload(tenant_id=tenant_id, service_id=service_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[ProjectAPI] get_project_sync_config failed")
        raise _quickbooks_internal_exception()


@QBO_API.post(
    "/extsvc/api/v1/quickbooks/projects/sync-config",
    summary="Save QuickBooks project sync configuration",
)
def save_project_sync_config(req: QuickBooksProjectSyncConfigRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()
    if not tenant_id or not service_id:
        raise HTTPException(status_code=400, detail="tenant_id and service_id are required")
    if not get_bearer_token(request):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    method = _normalize_project_sync_method(req.method)
    object_id = req.object_id or request.headers.get("x-object-id", "")
    email = req.email or request.headers.get("x-user-email", "")
    user_role = req.user_role or request.headers.get("x-user-role", "")

    if method == const.PROJECT_SYNC_METHOD_QBO_PROJECT:
        try:
            projects_available = QuickBooksCapabilityService().projects_available(tenant_id, service_id, refresh=True)
        except Exception:
            logger.exception("[ProjectAPI] project capability validation failed")
            projects_available = False
        if not projects_available:
            return _feature_not_available_response(
                QBOFeatureUnavailableError(const.CAPABILITY_PROJECTS, const.PROJECTS_NOT_AVAILABLE_MESSAGE)
            )

    config = {
        const.KEY_PROJECT_SYNC_METHOD: method,
        "updatedBy": object_id or None,
    }
    if "project_client_realm_id" in req.model_fields_set:
        config[const.KEY_PROJECT_CLIENT_REALM_ID] = _normalize_project_client_realm_id(
            req.project_client_realm_id
        )
    try:
        QuickBooksRepository().saveProjectSyncConfig(tenant_id, service_id, config)
        response = _project_sync_config_payload(
            tenant_id=tenant_id,
            service_id=service_id,
            refresh=False,
        )
        safe_audit_log(
            tenant_id=tenant_id,
            action="project_sync_config_saved",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="success",
            context=(
                f"method={method};"
                f"projectClientRealmIdPresent={bool(response.get(const.KEY_PROJECT_CLIENT_REALM_ID))}"
            ),
            resource_type="quickbooks_project",
        )
        return response
    except Exception as exc:
        logger.exception("[ProjectAPI] save_project_sync_config failed")
        raise _quickbooks_internal_exception()


@QBO_API.post(
    "/extsvc/api/v1/quickbooks/projects/create",
    summary="Create one QuickBooks project from a pending FIELDCRAFT project mapping",
    description=(
        "Internal real-time project export endpoint. The application sends the current "
        "project_external_map id after the project becomes eligible for QuickBooks sync."
    ),
)
def create_project(req: QuickBooksProjectCreateRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if req.project_external_map_id is None:
        raise HTTPException(status_code=400, detail="project_external_map_id is required")

    skip = _auto_sync_skip_response(
        tenant_id=tenant_id,
        service_id=req.service_id,
        module="projects",
    )
    if skip:
        return skip

    object_id = request.headers.get("x-object-id", "")
    email = request.headers.get("x-user-email", "")
    user_role = request.headers.get("x-user-role", "")

    from source.quickbooks.QuickBooksProjectCreateService import QuickBooksProjectCreateService

    try:
        return QuickBooksProjectCreateService().create_project(
            tenant_id=tenant_id,
            service_id=req.service_id,
            project_external_map_id=int(req.project_external_map_id),
            object_id=object_id,
            email=email,
            user_role=user_role,
        )
    except QuickBooksReconnectRequiredError as exc:
        return _qbo_reconnect_response(exc)
    except Exception:
        logging.exception("QuickBooks create_project failed")
        raise _quickbooks_internal_exception()


@QBO_API.post(
    "/extsvc/api/v1/quickbooks/projects/export",
    summary="Export projects to QuickBooks",
    description=(
        "Protected endpoint used by tenant web or scheduled sync handlers to export "
        "eligible FIELDCRAFT projects as QuickBooks sub-customers. Requires bearer token and api-secret."
    ),
)
def export_projects(req: QuickBooksProjectExportRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")
    object_id = req.object_id or request.headers.get("x-object-id", "")
    email = req.email or request.headers.get("x-user-email", "")
    user_role = req.user_role or request.headers.get("x-user-role", "")

    from source.quickbooks.QuickBooksProjectExportService import QuickBooksProjectExportService

    svc = QuickBooksProjectExportService()
    try:
        result = svc.export_projects(
            tenant_id=tenant_id,
            service_id=service_id,
            limit=int(req.limit),
            offset=int(req.offset),
            object_id=object_id,
            email=email,
            user_role=user_role,
        )
        _record_last_sync(
            result,
            tenant_id=tenant_id,
            service_id=service_id,
            setter_name="setLastProjectSyncAt",
        )
        return result
    except Exception as e:
        logging.exception("QuickBooks export_projects failed")
        raise _quickbooks_internal_exception()


@QBO_API.post(
    "/extsvc/api/v1/quickbooks/projects/import",
    summary="Import projects from QuickBooks",
    description=(
        "Protected endpoint used by tenant web or scheduled sync handlers to import "
        "QuickBooks sub-customer project changes into FIELDCRAFT. Requires bearer token and api-secret."
    ),
)
def import_projects(req: QuickBooksProjectImportRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")
    object_id = req.object_id or request.headers.get("x-object-id", "")
    email = req.email or request.headers.get("x-user-email", "")
    user_role = req.user_role or request.headers.get("x-user-role", "")

    from source.quickbooks.QuickBooksProjectImportService import QuickBooksProjectImportService

    svc = QuickBooksProjectImportService()
    try:
        return svc.import_projects(
            tenant_id=tenant_id,
            service_id=service_id,
            page_size=int(req.page_size),
            start_position=int(req.start_position),
            max_projects=req.requested_max,
            force_full=bool(req.force_full),
            object_id=object_id,
            email=email,
            user_role=user_role,
        )
    except Exception as e:
        logging.exception("QuickBooks import_projects failed")
        raise _quickbooks_internal_exception()


@QBO_API.post(
    "/extsvc/api/v1/quickbooks/projects/sync",
    summary="Start project sync job",
    description=(
        "Protected endpoint used by tenant web or scheduled sync handlers to start "
        "a QuickBooks project sync job. Requires bearer token and api-secret."
    ),
)
def sync_projects(payload: QuickBooksSyncJobRequest, request: Request, background_tasks: BackgroundTasks):
    tenant_id = (payload.tenant_id or "").strip()
    service_id = str(payload.service_id or "").strip()
    object_id = (payload.object_id or "").strip()

    if not tenant_id or not service_id or not object_id:
        raise HTTPException(status_code=400, detail="tenant_id, service_id and object_id are required")
    try:
        config = QuickBooksRepository().getProjectSyncConfig(tenant_id, service_id) or {}
        method = str(config.get(const.KEY_PROJECT_SYNC_METHOD) or "").strip().upper()
        if method == const.PROJECT_SYNC_METHOD_QBO_PROJECT:
            if not QuickBooksCapabilityService().projects_available(tenant_id, service_id, refresh=True):
                return _feature_not_available_response(
                    QBOFeatureUnavailableError(const.CAPABILITY_PROJECTS, const.PROJECTS_NOT_AVAILABLE_MESSAGE)
                )
    except QBOFeatureUnavailableError as exc:
        return _feature_not_available_response(exc)
    except Exception:
        logger.warning("[ProjectAPI] project sync capability pre-check failed; allowing exporter to validate the job", exc_info=True)

    safe_audit_log(
        tenant_id=tenant_id,
        action="project_sync_started",
        user_id=object_id or None,
        email=request.headers.get("x-user-email") or None,
        user_role=request.headers.get("x-user-role") or None,
        status="started",
        resource_type="quickbooks_project",
    )

    try:
        return start_sync_job(
            module="projects",
            tenant_id=tenant_id,
            service_id=service_id,
            object_id=object_id,
            background_tasks=background_tasks,
            tenant_api_token="",
            sync_mode=payload.sync_mode,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unable to start project sync: {e}")




@QBO_API.get("/extsvc/api/v1/quickbooks/services/coa-accounts")
@QBO_API.get("/extsvc/api/v1/quickbooks/services/income-accounts")
def service_role_income_accounts(tenantId: str, serviceId: str):
    """Return QuickBooks-synced chart of account rows for service-role selection.

    The endpoint name keeps backward compatibility with the earlier UI, but the
    response now includes all active COA rows that have a QuickBooks external id.
    """
    try:
        from source.dal.ChartOfAccountRepository import ChartOfAccountRepository
        accounts = ChartOfAccountRepository().list_service_coa_accounts(
            tenantId,
            service_id=serviceId,
        )
        return {
            "ok": True,
            "tenantId": tenantId,
            "serviceId": serviceId,
            "accounts": accounts,
            "records": accounts,
            "rows": accounts,
            "selectedAccount": None,
            "serviceIncomeAccount": None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("QuickBooks service COA account list failed")
        raise _quickbooks_internal_exception()


@QBO_API.post("/extsvc/api/v1/quickbooks/services/assign-income-account")
def assign_service_role_income_account(req: QuickBooksServiceRoleAssignIncomeAccountRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()
    chart_of_account_id = int(req.chart_of_account_id or 0)
    role_ids = [int(role_id) for role_id in (req.role_ids or []) if int(role_id or 0) > 0]

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")
    if chart_of_account_id <= 0:
        raise HTTPException(status_code=400, detail="chart_of_account_id is required")

    try:
        from source.dal.ChartOfAccountRepository import ChartOfAccountRepository
        from source.dal.QuickBooksRepository import QuickBooksRepository
        from source.dal.ServiceRoleRepository import ServiceRoleRepository

        qbo_repo = QuickBooksRepository()
        coa_repo = ChartOfAccountRepository()
        selected_account = coa_repo.get_service_coa_account_by_id(
            tenant_id,
            service_id=service_id,
            account_id=chart_of_account_id,
        )
        if not selected_account:
            raise HTTPException(
                status_code=400,
                detail="Selected chart of account is missing, inactive, or not synced to QuickBooks.",
            )

        selected_by = request.headers.get("x-object-id") or req.object_id
        if not role_ids:
            raise HTTPException(
                status_code=400,
                detail="role_ids is required. Select a Chart of Account for one or more service roles.",
            )
        selected_config = qbo_repo.buildServiceRoleIncomeAccountConfig(selected_account, selected_by=selected_by)
        repo = ServiceRoleRepository()
        reason = '{"message":"Service role chart of account selection changed and the selected roles were queued for QuickBooks service sync.","code":"SERVICE_ROLE_COA_CHANGED"}'
        queued_count = repo.set_income_account_for_roles(
            tenant_id=tenant_id,
            service_id=service_id,
            role_ids=role_ids,
            account=selected_config,
            reason=reason,
        )
        safe_audit_log(
            tenant_id=tenant_id,
            action="service_role_income_account_assigned",
            user_id=request.headers.get("x-object-id") or req.object_id,
            email=request.headers.get("x-user-email") or req.email,
            user_role=request.headers.get("x-user-role") or req.user_role,
            status="success",
            context=f"chartOfAccountId={chart_of_account_id} roleIds={role_ids or 'default'} queuedMappings={queued_count}",
            resource_type="quickbooks_service_role",
        )
        return {
            "ok": True,
            "tenantId": tenant_id,
            "serviceId": service_id,
            "selectedAccount": selected_config,
            "roleIds": role_ids,
            "queuedMappings": queued_count,
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("QuickBooks assign_service_role_income_account failed")
        raise _quickbooks_internal_exception()


@QBO_API.get(
    "/extsvc/api/v1/quickbooks/services/cdc-view",
    summary="Get QuickBooks service role sync view",
    description=(
        "Protected endpoint for tenant web to fetch paginated role-to-QuickBooks-service-item "
        "sync state from the tenant database. Requires bearer token and api-secret."
    ),
)
def service_role_cdc_view(
    tenantId: str,
    serviceId: str,
    page: int = 1,
    pageSize: int = 25,
    search: Optional[str] = None,
    syncStatus: Optional[str] = None,
    mappedOnly: bool = False,
):
    """Paginated FIELDCRAFT role -> QuickBooks service item sync view."""
    try:
        from source.dal.ServiceRoleRepository import ServiceRoleRepository
        from source.dal.QuickBooksRepository import QuickBooksRepository

        repo = ServiceRoleRepository()
        payload = repo.get_service_role_cdc_page(
            tenant_id=tenantId,
            service_id=serviceId,
            page=page,
            page_size=pageSize,
            search=search,
            sync_status=syncStatus,
            mapped_only=mappedOnly,
        )
        for row in payload.get("records") or []:
            # Row-level COA comes only from role_external_map.additional_info.
            # Do not fallback to service_subscriber config here; the table should
            # show the COA that was actually stored/used for this role mapping.
            selected_account = row.get("serviceIncomeAccount") or {}
            row["serviceIncomeAccount"] = selected_account
            row["serviceIncomeAccountId"] = selected_account.get("chart_of_account_id")
            row["serviceIncomeAccountExternalId"] = selected_account.get("external_id")
            row["serviceIncomeAccountName"] = selected_account.get("name")
            row["serviceIncomeAccountType"] = selected_account.get("account_type")
            row["serviceIncomeAccountDetailType"] = selected_account.get("detail_type")
        return {
            "ok": True,
            "tenantId": tenantId,
            "serviceId": serviceId,
            "serviceIncomeAccount": None,
            **payload,
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("QuickBooks service role CDC view failed")
        raise _quickbooks_internal_exception()




@QBO_API.get(
    "/extsvc/api/v1/quickbooks/invoices/cdc-view",
    summary="Get QuickBooks invoice sync view",
    description=(
        "Protected endpoint for tenant web to fetch paginated FIELDCRAFT invoice and "
        "invoice_external_map sync state. Requires bearer token and api-secret."
    ),
)
def invoice_cdc_view(
    tenantId: str,
    serviceId: str,
    page: int = 1,
    pageSize: int = 25,
    search: Optional[str] = None,
    syncStatus: Optional[str] = None,
):
    try:
        from source.dal.InvoiceRepository import InvoiceRepository

        payload = InvoiceRepository().get_invoice_cdc_page(
            tenant_id=tenantId,
            service_id=serviceId,
            page=page,
            page_size=pageSize,
            search=search,
            sync_status=syncStatus,
        )
        return {
            "ok": True,
            "tenantId": tenantId,
            "serviceId": serviceId,
            **payload,
        }
    except HTTPException:
        raise
    except Exception:
        logging.exception("QuickBooks invoice CDC view failed")
        raise _quickbooks_internal_exception()


@QBO_API.get(
    "/extsvc/api/v1/quickbooks/invoices/{mapping_id}/details",
    summary="Get QuickBooks invoice details",
)
def invoice_detail_view(mapping_id: int, tenantId: str, serviceId: str):
    tenant_id = (tenantId or "").strip()
    service_id = (serviceId or "").strip()
    if not tenant_id or not service_id:
        raise HTTPException(status_code=400, detail="tenantId and serviceId are required")
    try:
        from source.dal.InvoiceRepository import InvoiceRepository

        payload = InvoiceRepository().get_invoice_detail(
            tenant_id=tenant_id,
            service_id=service_id,
            mapping_id=mapping_id,
        )
        if payload is None:
            raise HTTPException(status_code=404, detail="Invoice mapping was not found.")
        return {"ok": True, "tenantId": tenant_id, "serviceId": service_id, **payload}
    except HTTPException:
        raise
    except Exception:
        logging.exception("QuickBooks invoice detail view failed")
        raise _quickbooks_internal_exception()


@QBO_API.get(
    "/extsvc/api/v1/quickbooks/invoices/{mapping_id}/document",
    summary="View or download a FIELDCRAFT Invoice attachment",
)
def invoice_document_download(mapping_id: int, tenantId: str, serviceId: str):
    tenant_id = (tenantId or "").strip()
    service_id = (serviceId or "").strip()
    if not tenant_id or not service_id:
        raise HTTPException(status_code=400, detail="tenantId and serviceId are required")
    try:
        from fastapi.responses import Response
        from source.dal.InvoiceRepository import InvoiceRepository
        from source.quickbooks.QBOInvoiceAttachmentService import QBOInvoiceAttachmentService

        detail = InvoiceRepository().get_invoice_detail(
            tenant_id=tenant_id, service_id=service_id, mapping_id=mapping_id
        )
        if not detail:
            raise HTTPException(status_code=404, detail="Invoice mapping was not found.")
        invoice = detail.get("invoice") or {}
        document = detail.get("document") or {}
        document_id = document.get("id") or invoice.get("document_id")
        if not document_id:
            raise HTTPException(status_code=404, detail="Invoice attachment was not found.")

        file_bytes, file_name, mime_type = QBOInvoiceAttachmentService().get_invoice_document_bytes(
            tenant_id=tenant_id,
            document_id=int(document_id),
            invoice_project_id=invoice.get("project_id"),
        )
        safe_name = _safe_content_disposition_filename(file_name)
        safe_audit_log(
            tenant_id=tenant_id,
            action="invoice_document_downloaded",
            status="success",
            context=f"mappingId={mapping_id};documentId={document_id}",
            resource_type="quickbooks_invoice_document",
        )
        return Response(
            content=file_bytes,
            media_type=mime_type,
            headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Invoice attachment was not found.")
    except HTTPException:
        raise
    except Exception:
        logging.exception("QuickBooks invoice document download failed")
        raise _quickbooks_internal_exception()


@QBO_API.post(
    "/extsvc/api/v1/quickbooks/invoices/create",
    summary="Create or update one QuickBooks invoice from an approved FIELDCRAFT invoice",
    description=(
        "Internal real-time invoice export endpoint. The application sends the current "
        "invoice_external_map id after invoice approval."
    ),
)
def create_invoice(req: QuickBooksInvoiceCreateRequest):
    tenant_id = (req.tenant_id or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if req.invoice_external_map_id is None:
        raise HTTPException(status_code=400, detail="invoice_external_map_id is required")

    skip = _auto_sync_skip_response(
        tenant_id=tenant_id,
        service_id=req.service_id,
        module="invoices",
    )
    if skip:
        return skip

    from source.quickbooks.QuickBooksInvoiceCreateService import QuickBooksInvoiceCreateService

    try:
        return QuickBooksInvoiceCreateService().create_invoice(
            tenant_id=tenant_id,
            service_id=req.service_id,
            invoice_external_map_id=int(req.invoice_external_map_id),
        )
    except QuickBooksReconnectRequiredError as exc:
        return _qbo_reconnect_response(exc)
    except Exception:
        logging.exception("QuickBooks create_invoice failed")
        raise HTTPException(
            status_code=500,
            detail=(
                "The invoice could not be exported because of an unexpected server error. "
                "Retry once, then contact support with the sync details."
            ),
        )


@QBO_API.post(
    "/extsvc/api/v1/quickbooks/invoices/export",
    summary="Export FIELDCRAFT invoices to QuickBooks",
    description=(
        "Protected endpoint for explicit invoice export. FIELDCRAFT is the source of truth; "
        "customer/project references come from their existing QuickBooks mappings."
    ),
)
def export_invoices(req: QuickBooksInvoiceExportRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()
    if not tenant_id or not service_id:
        raise HTTPException(status_code=400, detail="tenant_id and service_id are required")

    object_id = req.object_id or request.headers.get("x-object-id", "")
    email = req.email or request.headers.get("x-user-email", "")
    user_role = req.user_role or request.headers.get("x-user-role", "")

    from source.quickbooks.QuickBooksInvoiceExportService import QuickBooksInvoiceExportService

    try:
        return QuickBooksInvoiceExportService().export_invoices(
            tenant_id=tenant_id,
            service_id=service_id,
            limit=int(req.limit or 500),
            offset=int(req.offset or 0),
            object_id=object_id,
            email=email,
            user_role=user_role,
        )
    except QuickBooksReconnectRequiredError as exc:
        return _qbo_reconnect_response(exc)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=_safe_validation_detail(exc, "Invalid invoice export request."),
        ) from exc
    except Exception as exc:
        logging.exception("QuickBooks invoice export failed")
        raise _quickbooks_internal_exception() from exc


@QBO_API.post(
    "/extsvc/api/v1/quickbooks/invoices/sync",
    summary="Start QuickBooks invoice sync job",
    description=(
        "Protected endpoint that starts export-only FIELDCRAFT Invoice to QuickBooks Invoice sync."
    ),
)
def sync_invoices(payload: QuickBooksSyncJobRequest, request: Request, background_tasks: BackgroundTasks):
    tenant_id = (payload.tenant_id or "").strip()
    service_id = str(payload.service_id or "").strip()
    object_id = (payload.object_id or "").strip()
    if not tenant_id or not service_id or not object_id:
        raise HTTPException(status_code=400, detail="tenant_id, service_id and object_id are required")

    safe_audit_log(
        tenant_id=tenant_id,
        action="invoice_sync_started",
        user_id=object_id or None,
        email=request.headers.get("x-user-email") or None,
        user_role=request.headers.get("x-user-role") or None,
        status="started",
        resource_type="quickbooks_invoice",
    )

    try:
        return start_sync_job(
            module="invoices",
            tenant_id=tenant_id,
            service_id=service_id,
            object_id=object_id,
            background_tasks=background_tasks,
            tenant_api_token="",
            sync_mode=payload.sync_mode,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unable to start invoice sync: {exc}") from exc


@QBO_API.get(
    "/extsvc/api/v1/quickbooks/timelogs/cdc-view",
    summary="Get QuickBooks TimeActivity mapping view",
    description=(
        "Protected endpoint for tenant web to fetch paginated timelog_external_map rows "
        "with FIELDCRAFT timelog reference details and QuickBooks TimeActivity sync state. Requires bearer token and api-secret."
    ),
)
def timelog_cdc_view(
    tenantId: str,
    serviceId: str,
    page: int = 1,
    pageSize: int = 25,
    search: Optional[str] = None,
    syncStatus: Optional[str] = None,
):
    """Paginated timelog_external_map -> QuickBooks TimeActivity sync view."""
    try:
        from source.dal.TimelogRepository import TimelogRepository

        repo = TimelogRepository()
        payload = repo.get_timelog_cdc_page(
            tenant_id=tenantId,
            service_id=serviceId,
            page=page,
            page_size=pageSize,
            search=search,
            sync_status=syncStatus,
        )
        return {
            "ok": True,
            "tenantId": tenantId,
            "serviceId": serviceId,
            **payload,
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("QuickBooks timelog CDC view failed")
        raise _quickbooks_internal_exception()


@QBO_API.post(
    "/extsvc/api/v1/quickbooks/timelogs/create",
    summary="Export one approved completed timelog to QuickBooks TimeActivity",
    description=(
        "Protected endpoint used by tenant API for real-time export of one "
        "approved, completed FIELDCRAFT timelog as a QuickBooks Accounting TimeActivity. The request sends "
        "the timelog_external_map_id, and the source timelog is resolved from that mapping. "
        "Requires bearer token and api-secret."
    ),
)
def create_timelog(req: QuickBooksTimelogCreateRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip() or None
    timelog_external_map_id = int(req.timelog_external_map_id or 0)

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required")
    if not timelog_external_map_id:
        raise HTTPException(status_code=400, detail="timelog_external_map_id is required")

    skip = _auto_sync_skip_response(
        tenant_id=tenant_id,
        service_id=service_id,
        module="timelogs",
    )
    if skip:
        return skip

    object_id = req.object_id or request.headers.get("x-object-id", "")
    email = req.email or request.headers.get("x-user-email", "")
    user_role = req.user_role or request.headers.get("x-user-role", "")

    from source.quickbooks.QuickBooksTimelogCreateService import QuickBooksTimelogCreateService

    svc = QuickBooksTimelogCreateService()
    try:
        return svc.create_timelog(
            tenant_id=tenant_id,
            service_id=service_id,
            timelog_external_map_id=timelog_external_map_id,
            object_id=object_id,
            email=email,
            user_role=user_role,
        )
    except QuickBooksReconnectRequiredError as exc:
        return _qbo_reconnect_response(exc)
    except Exception:
        logging.exception("QuickBooks create_timelog failed")
        raise _quickbooks_internal_exception()


# @QBO_API.post(
#     "/extsvc/api/v1/quickbooks/timelogs/delete-all",
#     summary="Delete mapped QuickBooks TimeActivity records (developer maintenance)",
#     description=(
#         "Developer/test-only cleanup. Deletes only QuickBooks Accounting TimeActivity "
#         "records currently mapped by FIELDCRAFT and resets those mappings to PENDING_PUSH."
#     ),
# )
# def delete_all_timelog_time_activities(
#     req: QuickBooksTimelogDeleteAllRequest,
#     request: Request,
# ):
#     _require_opt_in_endpoint("QBO_DESTRUCTIVE_MAINTENANCE_ENABLED")
#     tenant_id = (req.tenant_id or "").strip()
#     service_id = str(req.service_id or "").strip()
#     if not tenant_id:
#         raise HTTPException(status_code=400, detail="tenant_id is required")
#     if not service_id:
#         raise HTTPException(status_code=400, detail="service_id is required")
#     if not req.dry_run and not req.confirm_delete:
#         raise HTTPException(
#             status_code=400,
#             detail="confirm_delete must be true to delete mapped QuickBooks TimeActivity records",
#         )
#
#     object_id = req.object_id or request.headers.get("x-object-id", "")
#     email = req.email or request.headers.get("x-user-email", "")
#     user_role = req.user_role or request.headers.get("x-user-role", "")
#     safe_audit_log(
#         tenant_id=tenant_id,
#         action="timelog_time_activity_delete_started",
#         user_id=object_id or None,
#         email=email or None,
#         user_role=user_role or None,
#         status="started",
#         resource_type="quickbooks_timelog",
#         context=f"dry_run={bool(req.dry_run)} scope=FIELDCRAFT_MAPPED_QBO_TIME_ACTIVITIES_ONLY",
#     )
#
#     from source.quickbooks.QuickBooksTimelogDeleteService import QuickBooksTimelogDeleteService
#
#     try:
#         result = QuickBooksTimelogDeleteService().delete_all_mapped_time_activities(
#             tenant_id=tenant_id,
#             service_id=service_id,
#             confirm_delete=bool(req.confirm_delete),
#             dry_run=bool(req.dry_run),
#         )
#         safe_audit_log(
#             tenant_id=tenant_id,
#             action="timelog_time_activity_delete_completed",
#             user_id=object_id or None,
#             email=email or None,
#             user_role=user_role or None,
#             status="completed" if result.get("ok") else "partial",
#             resource_type="quickbooks_timelog",
#             context=(
#                 f"deleted={result.get('deleted', 0)} "
#                 f"already_missing={result.get('alreadyMissing', 0)} "
#                 f"failed={result.get('failed', 0)} "
#                 f"reset_mappings={result.get('resetMappings', 0)}"
#             ),
#         )
#         return result
#     except QuickBooksReconnectRequiredError as exc:
#         return _qbo_reconnect_response(exc)
#     except ValueError as exc:
#         raise HTTPException(
#             status_code=400,
#             detail=_safe_validation_detail(exc, "Invalid maintenance request."),
#         ) from exc
#     except Exception as exc:
#         logging.exception("QuickBooks mapped TimeActivity cleanup failed")
#         safe_audit_log(
#             tenant_id=tenant_id,
#             action="timelog_time_activity_delete_failed",
#             user_id=object_id or None,
#             email=email or None,
#             user_role=user_role or None,
#             status="failed",
#             resource_type="quickbooks_timelog",
#             context=str(exc),
#         )
#         raise _quickbooks_internal_exception() from exc
#
#
@QBO_API.post(
    "/extsvc/api/v1/quickbooks/timelogs/export",
    summary="Export completed timelogs to QuickBooks TimeActivity",
    description=(
        "Protected endpoint used by tenant web or scheduled sync handlers to export "
        "completed FIELDCRAFT timelogs as QuickBooks Accounting TimeActivity records. Requires bearer token and api-secret."
    ),
)
def export_timelogs(req: QuickBooksTimelogExportRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")

    object_id = req.object_id or request.headers.get("x-object-id", "")
    email = req.email or request.headers.get("x-user-email", "")
    user_role = req.user_role or request.headers.get("x-user-role", "")

    safe_audit_log(
        tenant_id=tenant_id,
        action="timelog_export_started",
        user_id=object_id or None,
        email=email or None,
        user_role=user_role or None,
        status="started",
        resource_type="quickbooks_timelog",
    )

    from source.quickbooks.QuickBooksTimelogExportService import create_timelog_export_service

    svc = create_timelog_export_service()
    try:
        return svc.export_timelogs(
            tenant_id=tenant_id,
            service_id=service_id,
            limit=int(req.limit or 500),
            offset=int(req.offset or 0),
            dry_run=bool(req.dry_run),
            object_id=object_id,
            email=email,
            user_role=user_role,
        )
    except Exception as e:
        logging.exception("QuickBooks export_timelogs failed")
        raise _quickbooks_internal_exception()


@QBO_API.post(
    "/extsvc/api/v1/quickbooks/timelogs/sync",
    summary="Start QuickBooks TimeActivity sync job",
    description=(
        "Protected endpoint used by tenant web or scheduled sync handlers to start "
        "a FIELDCRAFT timelog to QuickBooks Accounting TimeActivity sync job. Requires bearer token and api-secret."
    ),
)
def sync_timelogs(payload: QuickBooksSyncJobRequest, request: Request, background_tasks: BackgroundTasks):
    tenant_id = (payload.tenant_id or "").strip()
    service_id = str(payload.service_id or "").strip()
    object_id = (payload.object_id or "").strip()

    if not tenant_id or not service_id or not object_id:
        raise HTTPException(status_code=400, detail="tenant_id, service_id and object_id are required")

    safe_audit_log(
        tenant_id=tenant_id,
        action="timelog_sync_started",
        user_id=object_id or None,
        email=request.headers.get("x-user-email") or None,
        user_role=request.headers.get("x-user-role") or None,
        status="started",
        resource_type="quickbooks_timelog",
    )

    try:
        return start_sync_job(
            module="timelogs",
            tenant_id=tenant_id,
            service_id=service_id,
            object_id=object_id,
            background_tasks=background_tasks,
            tenant_api_token="",
            sync_mode=payload.sync_mode,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unable to start timelog sync: {e}")


@QBO_API.post(
    "/extsvc/api/v1/quickbooks/services/export",
    summary="Export FIELDCRAFT roles to QuickBooks service items",
    description=(
        "Protected endpoint used by tenant web or scheduled sync handlers to export "
        "eligible FIELDCRAFT roles as QuickBooks service items. Requires bearer token and api-secret."
    ),
)
def export_service_roles(req: QuickBooksServiceRoleExportRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")

    object_id = req.object_id or request.headers.get("x-object-id", "")
    email = req.email or request.headers.get("x-user-email", "")
    user_role = req.user_role or request.headers.get("x-user-role", "")

    from source.quickbooks.QuickBooksServiceRoleExportService import QuickBooksServiceRoleExportService

    svc = QuickBooksServiceRoleExportService()
    try:
        return svc.export_roles(
            tenant_id=tenant_id,
            service_id=service_id,
            limit=int(req.limit),
            offset=int(req.offset),
            object_id=object_id,
            email=email,
            user_role=user_role,
        )
    except Exception as e:
        logging.exception("QuickBooks export_service_roles failed")
        raise _quickbooks_internal_exception()


@QBO_API.post(
    "/extsvc/api/v1/quickbooks/services/import",
    summary="Validate mapped QuickBooks service items",
    description=(
        "Protected endpoint used by tenant web or scheduled sync handlers to validate "
        "already-mapped QuickBooks service items for FIELDCRAFT roles. It does not import "
        "QuickBooks-created products/services into FIELDCRAFT. Requires bearer token and api-secret."
    ),
)
def import_service_roles(req: QuickBooksServiceRoleImportRequest, request: Request):
    tenant_id = (req.tenant_id or "").strip()
    service_id = str(req.service_id or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenantId is required")
    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId is required")

    object_id = req.object_id or request.headers.get("x-object-id", "")
    email = req.email or request.headers.get("x-user-email", "")
    user_role = req.user_role or request.headers.get("x-user-role", "")

    from source.quickbooks.QuickBooksServiceRoleImportService import QuickBooksServiceRoleImportService

    svc = QuickBooksServiceRoleImportService()
    try:
        return svc.import_roles(
            tenant_id=tenant_id,
            service_id=service_id,
            page_size=int(req.page_size),
            start_position=int(req.start_position),
            max_items=req.requested_max,
            force_full=bool(req.force_full),
            object_id=object_id,
            email=email,
            user_role=user_role,
        )
    except Exception as e:
        logging.exception("QuickBooks import_service_roles failed")
        raise _quickbooks_internal_exception()


@QBO_API.post(
    "/extsvc/api/v1/quickbooks/services/sync",
    summary="Start service role sync job",
    description=(
        "Protected endpoint used by tenant web or scheduled sync handlers to start "
        "a FIELDCRAFT role to QuickBooks service item sync job. Requires bearer token and api-secret."
    ),
)
def sync_service_roles(payload: QuickBooksSyncJobRequest, request: Request, background_tasks: BackgroundTasks):
    tenant_id = (payload.tenant_id or "").strip()
    service_id = str(payload.service_id or "").strip()
    object_id = (payload.object_id or "").strip()

    if not tenant_id or not service_id or not object_id:
        raise HTTPException(status_code=400, detail="tenant_id, service_id and object_id are required")

    safe_audit_log(
        tenant_id=tenant_id,
        action="service_role_sync_started",
        user_id=object_id or None,
        email=request.headers.get("x-user-email") or None,
        user_role=request.headers.get("x-user-role") or None,
        status="started",
        resource_type="quickbooks_service_role",
    )

    try:
        return start_sync_job(
            module="services",
            tenant_id=tenant_id,
            service_id=service_id,
            object_id=object_id,
            background_tasks=background_tasks,
            tenant_api_token="",
            sync_mode=payload.sync_mode,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unable to start service role sync: {e}")
