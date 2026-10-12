from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

from source.dal.EmployeeRepository import EmployeeRepository
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.dal.ScheduledJobRepository import ScheduledJobRepository
from source.dal.VendorExternalMapRepository import VendorExternalMapRepository
from source.quickbooks.QuickBooksAuthentication import QuickBooksOAuthClient
from source.service.QuickBooksService import QuickBooksService
from source.service.TenantApiClient import TenantApiClient
from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils import QuickBooksErrorHandler as qbo_err
from source.Utils.QuickBooksErrorHandler import QuickBooksAPIError
from source.Utils import constants as const
from source.Utils.QuickBooksLogging import log_cdc_debug
from source.Utils.auditlog import safe_audit_log


logger = logging.getLogger(__name__)



class QuickBooksEmployeeImportService:
    """Import QuickBooks Employee records into FIELDCRAFT user_account through Tenant API."""

    QBO_EMPLOYEE_MINOR_VERSION = "75"

    REASON_EMAIL_CHANGE_NOT_ALLOWED = "EMPLOYEE_EMAIL_CHANGE_NOT_ALLOWED"
    REASON_1099_EMAIL_CHANGE_NOT_ALLOWED = "CONTRACTOR_1099_EMAIL_CHANGE_NOT_ALLOWED"
    REASON_DUPLICATE_EMAIL_NOT_ALLOWED = "EMPLOYEE_DUPLICATE_EMAIL_NOT_ALLOWED"
    REASON_MISSING_REQUIRED_FIELDS = "EMPLOYEE_MISSING_REQUIRED_FIELDS"
    REASON_INACTIVE_IN_QBO = "EMPLOYEE_INACTIVE_IN_QBO"
    REASON_1099_TRACKING_REMOVED = "CONTRACTOR_1099_TRACKING_REMOVED"
    REASON_VENDOR_1099_ENABLED_AFTER_SYNC = "VENDOR_1099_ENABLED_AFTER_SYNC"

    QBO_COST_RATE_FIELD = "CostRate"
    QBO_BILL_RATE_FIELD = "BillRate"
    QBO_BILLABLE_TIME_FIELD = "BillableTime"

    def __init__(self):
        self.qbo_repo = QuickBooksRepository()
        self.employee_repo = EmployeeRepository()
        self.qbo_service = QuickBooksService()
        self.tenant_client = TenantApiClient()
        self.scheduled_job_repo = ScheduledJobRepository()
        self.vendor_map_repo = VendorExternalMapRepository()



    @staticmethod
    def _build_reason(message: str, intuit_tid: Optional[str] = None, **extra: Any) -> str:
        data: Dict[str, Any] = {"message": message}
        if intuit_tid:
            data["intuit_tid"] = intuit_tid
        for key, value in extra.items():
            if value is not None:
                data[key] = value
        return json.dumps(data)

    @staticmethod
    def _log_debug(message: str, data: Optional[Any] = None) -> None:
        if data is None:
            logger.debug("[EmployeeImport] %s", message)
            return
        logger.debug("[EmployeeImport] %s | %s", message, data)

    def import_employees(
        self,
        *,
        tenant_id: str,
        service_id: str,
        tenant_api_token: str = "",
        page_size: int = 100,
        start_position: int = 1,
        max_employees: Optional[int] = None,
        dry_run: bool = False,
        changed_since_override: Optional[Any] = None,
        force_full: bool = False,
        object_id: Optional[str] = None,
        email: Optional[str] = None,
        user_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        logger.info(
            "[EmployeeImport] import_employees called | tenant_id=%s service_id=%s page_size=%s start_position=%s max_employees=%s dry_run=%s",
            tenant_id,
            service_id,
            page_size,
            start_position,
            max_employees,
            dry_run,
        )
        safe_audit_log(
            tenant_id=tenant_id,
            action="employee_import_started",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="started",
            resource_type="quickbooks_employee",
        )

        audit_action = "employee_import_failed"
        audit_status = "failed"
        audit_context: Optional[str] = None
        summary = self._empty_summary(tenant_id=tenant_id, service_id=service_id)

        try:
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}

            # ── Employee sync config (read once, applied to every employee this run) ──
            _sync_cfg = connection.get(const.KEY_EMPLOYEE_SYNC) or {}
            days_after_hire: int = max(
                const.EMPLOYEE_SYNC_DAYS_MIN, min(int(_sync_cfg.get(const.KEY_EMPLOYEE_SYNC_DAYS_AFTER_HIRE) or const.EMPLOYEE_SYNC_DAYS_MIN), const.EMPLOYEE_SYNC_DAYS_MAX)
            )
            # Worker classification is derived from QuickBooks, never configured: a QBO
            # Employee is always a W2 worker. (1099 workers are derived from 1099-tracked
            # QBO Vendors — imported separately below via _import_1099_vendors_as_employees.)
            employee_type: str = const.EMPLOYEE_TYPE_W2
            cost_sync_enabled = bool(_sync_cfg.get(const.KEY_EMPLOYEE_SYNC_COST_ENABLED, True))
            cost_qbo_field = self._normalize_cost_qbo_field(
                _sync_cfg.get(const.KEY_EMPLOYEE_SYNC_COST_QBO_FIELD)
            )

            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = (connection.get(const.KEY_ENV) or "sandbox").lower()
            conn_status = (connection.get(const.KEY_STATUS) or const.STATUS_CONNECTED).lower()
            if conn_status and conn_status != const.STATUS_CONNECTED:
                audit_action, audit_context = "employee_import_aborted", f"not_connected status={conn_status}"
                return {const.KEY_OK: False, const.KEY_MESSAGE: f"QuickBooks is not connected (status={conn_status})"}
            if not refresh_token or not realm_id:
                audit_action, audit_context = "employee_import_aborted", "missing_realmId_or_refreshToken"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks not connected for this tenant (missing realmId/refreshToken)."}

            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)
            if not client_id or not client_secret:
                audit_action, audit_context = "employee_import_aborted", "missing_credentials"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks client credentials missing (ClientId/ClientSecret)."}

            access_token = self._refresh_access_token(
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
                tenant_id=tenant_id,
                service_id=service_id,
                connection=connection,
            )
            base_url = self._get_qbo_api_base(env)

            changed_since, watermark_source = self._resolve_changed_since(
                tenant_id=tenant_id,
                service_id=service_id,
                changed_since_override=changed_since_override,
                force_full=force_full,
            )
            use_cdc = changed_since is not None
            if use_cdc and self._is_cdc_too_old(changed_since):
                use_cdc = False
                changed_since = None
                watermark_source = None

            summary["mode"] = "CDC" if use_cdc else "FULL"
            summary["changedSince"] = changed_since.isoformat() if changed_since else None
            summary["watermarkSource"] = watermark_source
            log_cdc_debug(logger,
                "employee import mode",
                tenantId=tenant_id,
                serviceId=service_id,
                mode=summary["mode"],
                changedSince=summary["changedSince"],
                watermarkSource=watermark_source,
                changedSinceOverride=changed_since_override,
                forceFull=force_full,
            )

            pending_import_failed_ids = self.employee_repo.get_open_import_failed_external_employee_ids(
                tenant_id=tenant_id,
                service_id=service_id,
            )
            if pending_import_failed_ids:
                summary["persistentRetry"] = self.import_employees_by_ids(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    employee_ids=pending_import_failed_ids,
                    tenant_api_token=tenant_api_token,
                )

            if use_cdc:
                qbo_employees, intuit_tid = self._fetch_qbo_employees_cdc(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=str(realm_id),
                    changed_since=changed_since,
                )
                if max_employees is not None:
                    qbo_employees = qbo_employees[: int(max_employees)]
                self._log_debug("CDC fetched employees", {"count": len(qbo_employees), "intuitTid": intuit_tid})
                log_cdc_debug(logger,
                    "employee CDC result",
                    count=len(qbo_employees),
                    ids=[self._clean(emp.get(const.QBO_KEY_ID)) for emp in qbo_employees],
                    intuitTid=intuit_tid,
                )
                summary["intuitTid"] = intuit_tid
                for qbo_employee in qbo_employees:
                    summary["processed"] += 1
                    self._process_qbo_employee(
                        summary=summary,
                        tenant_id=tenant_id,
                        service_id=service_id,
                        realm_id=str(realm_id),
                        qbo_employee=qbo_employee,
                        dry_run=dry_run,
                        tenant_api_token=tenant_api_token,
                        days_after_hire=days_after_hire,
                        employee_type=employee_type,
                        access_token=access_token,
                        base_url=base_url,
                        cost_sync_enabled=cost_sync_enabled,
                        cost_qbo_field=cost_qbo_field,
                    )
                self._import_1099_vendors_as_employees(
                    summary=summary,
                    tenant_id=tenant_id,
                    service_id=service_id,
                    realm_id=str(realm_id),
                    access_token=access_token,
                    base_url=base_url,
                    tenant_api_token=tenant_api_token,
                    days_after_hire=days_after_hire,
                    cost_sync_enabled=cost_sync_enabled,
                    dry_run=dry_run,
                    changed_since=changed_since,
                )
                audit_action = "employee_import_completed"
                audit_status = "success" if int(summary.get("failed") or 0) == 0 else "failed"
                audit_context = f"imported={summary.get('created in FIELDCRAFT', 0) + summary.get('updatedInFIELDCRAFT', 0)} failed={summary.get('failed', 0)}"
                return self._finalize_summary(summary)

            pos = max(1, int(start_position or 1))
            processed = 0
            while True:
                if max_employees is not None and processed >= int(max_employees):
                    break
                batch_size = int(page_size or 100)
                if max_employees is not None:
                    batch_size = min(batch_size, int(max_employees) - processed)
                qbo_employees, intuit_tid = self._query_qbo_employees(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=str(realm_id),
                    start_position=pos,
                    max_results=batch_size,
                )
                self._log_debug("FULL query fetched employees", {
                    "start_position": pos,
                    "max_results": batch_size,
                    "count": len(qbo_employees),
                    "intuitTid": intuit_tid,
                })
                log_cdc_debug(logger,
                    "employee FULL page fetched",
                    startPosition=pos,
                    maxResults=batch_size,
                    count=len(qbo_employees),
                    ids=[self._clean(emp.get(const.QBO_KEY_ID)) for emp in qbo_employees],
                    intuitTid=intuit_tid,
                )
                if not qbo_employees:
                    break
                summary["intuitTid"] = intuit_tid
                for qbo_employee in qbo_employees:
                    processed += 1
                    summary["processed"] += 1
                    self._process_qbo_employee(
                        summary=summary,
                        tenant_id=tenant_id,
                        service_id=service_id,
                        realm_id=str(realm_id),
                        qbo_employee=qbo_employee,
                        dry_run=dry_run,
                        tenant_api_token=tenant_api_token,
                        days_after_hire=days_after_hire,
                        employee_type=employee_type,
                        access_token=access_token,
                        base_url=base_url,
                        cost_sync_enabled=cost_sync_enabled,
                        cost_qbo_field=cost_qbo_field,
                    )
                pos += len(qbo_employees)
                if len(qbo_employees) < batch_size:
                    break

            self._import_1099_vendors_as_employees(
                summary=summary,
                tenant_id=tenant_id,
                service_id=service_id,
                realm_id=str(realm_id),
                access_token=access_token,
                base_url=base_url,
                tenant_api_token=tenant_api_token,
                days_after_hire=days_after_hire,
                cost_sync_enabled=cost_sync_enabled,
                dry_run=dry_run,
                changed_since=None,
            )
            audit_action = "employee_import_completed"
            audit_status = "success" if int(summary.get("failed") or 0) == 0 else "failed"
            audit_context = f"imported={summary.get('created in FIELDCRAFT', 0) + summary.get('updatedInFIELDCRAFT', 0)} failed={summary.get('failed', 0)}"
            return self._finalize_summary(summary)
        except Exception as exc:
            logger.exception("[EmployeeImport] import_employees failed")
            summary[const.KEY_OK] = False
            summary[const.KEY_ERROR] = "Unexpected internal error. See server logs for details."
            audit_action = "employee_import_failed"
            audit_status = "failed"
            audit_context = str(exc)[:64]
            return summary
        finally:
            summary["finishedAt"] = datetime.now(timezone.utc).isoformat()
            safe_audit_log(
                tenant_id=tenant_id,
                action=audit_action,
                user_id=object_id or None,
                email=email or None,
                user_role=user_role or None,
                status=audit_status,
                context=audit_context,
                resource_type="quickbooks_employee",
            )

    def import_employees_by_ids(
        self,
        *,
        tenant_id: str,
        service_id: str,
        employee_ids: List[str],
        tenant_api_token: str = "",
    ) -> Dict[str, Any]:
        summary = self._empty_summary(tenant_id=tenant_id, service_id=service_id)
        try:
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            conn_status = (connection.get(const.KEY_STATUS) or const.STATUS_CONNECTED).lower()
            if conn_status and conn_status != const.STATUS_CONNECTED:
                return {const.KEY_OK: False, const.KEY_MESSAGE: f"QuickBooks is not connected (status={conn_status})"}

            # Read same employee sync config so retry runs are consistent with normal runs.
            # Retries here are QBO Employees (by external employee id) -> always W2.
            _sync_cfg = connection.get(const.KEY_EMPLOYEE_SYNC) or {}
            days_after_hire: int = max(
                const.EMPLOYEE_SYNC_DAYS_MIN, min(int(_sync_cfg.get(const.KEY_EMPLOYEE_SYNC_DAYS_AFTER_HIRE) or const.EMPLOYEE_SYNC_DAYS_MIN), const.EMPLOYEE_SYNC_DAYS_MAX)
            )
            employee_type: str = const.EMPLOYEE_TYPE_W2
            cost_sync_enabled = bool(_sync_cfg.get(const.KEY_EMPLOYEE_SYNC_COST_ENABLED, True))
            cost_qbo_field = self._normalize_cost_qbo_field(
                _sync_cfg.get(const.KEY_EMPLOYEE_SYNC_COST_QBO_FIELD)
            )

            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = (connection.get(const.KEY_ENV) or "sandbox").lower()
            if not refresh_token or not realm_id:
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks not connected (missing refreshToken/realmId)."}

            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)
            if not client_id or not client_secret:
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks client credentials missing (ClientId/ClientSecret)."}

            access_token = self._refresh_access_token(
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
                tenant_id=tenant_id,
                service_id=service_id,
                connection=connection,
            )
            base_url = self._get_qbo_api_base(env)
            for employee_id in employee_ids or []:
                qbo_employee = self._fetch_qbo_employee_by_id(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=str(realm_id),
                    employee_id=str(employee_id),
                )
                summary["processed"] += 1
                self._process_qbo_employee(
                    summary=summary,
                    tenant_id=tenant_id,
                    service_id=service_id,
                    realm_id=str(realm_id),
                    qbo_employee=qbo_employee,
                    dry_run=False,
                    tenant_api_token=tenant_api_token,
                    days_after_hire=days_after_hire,
                    employee_type=employee_type,
                    access_token=access_token,
                    base_url=base_url,
                    cost_sync_enabled=cost_sync_enabled,
                    cost_qbo_field=cost_qbo_field,
                )
            return self._finalize_summary(summary)
        except Exception as exc:
            logger.exception(
                "[EmployeeImport] import_employees_by_ids failed | employee_ids=%s",
                employee_ids,
            )
            summary[const.KEY_OK] = False
            summary[const.KEY_ERROR] = "Unexpected internal error. See server logs for details."
            return summary
        finally:
            summary["finishedAt"] = datetime.now(timezone.utc).isoformat()

    def _reason_has_code(self, reason: Any, code: str) -> bool:
        if not reason or not code:
            return False
        try:
            parsed = json.loads(str(reason))
            return parsed.get("code") == code
        except Exception:
            return code in str(reason)

    def _employee_email_changed(self, current_employee: Optional[Dict[str, Any]], incoming_payload: Dict[str, Any]) -> bool:
        if not current_employee:
            return False
        return self._clean(current_employee.get(const.KEY_EMAIL)).lower() != self._clean(incoming_payload.get(const.KEY_EMAIL)).lower()

    def _email_change_reason(
        self,
        *,
        current_employee: Dict[str, Any],
        qbo_employee_id: str,
        qbo_email: str,
        display_name: str,
    ) -> Tuple[str, str]:
        local_email = self._clean(current_employee.get(const.KEY_EMAIL))
        qbo_email = self._clean(qbo_email)
        employee_label = display_name or " ".join(
            part for part in [
                self._clean(current_employee.get("firstname")),
                self._clean(current_employee.get("lastname")),
            ] if part
        ) or f"ID {qbo_employee_id}"
        message = (
            f"Email sync was skipped for QuickBooks employee {employee_label} because FIELDCRAFT locks employee email after creation. "
            f"FIELDCRAFT currently has {local_email or 'no email'}, while QuickBooks has {qbo_email or 'no email'}. "
            "Update the email in QuickBooks to match FIELDCRAFT, then run employee sync again."
        )
        return message, self._build_reason(
            message,
            code=self.REASON_EMAIL_CHANGE_NOT_ALLOWED,
            fieldcraft_email=local_email,
            qbo_email=qbo_email,
            qbo_employee_id=qbo_employee_id,
            display_name=display_name or None,
            action_required="Update the employee email in QuickBooks to match the FIELDCRAFT employee email, then run employee sync again.",
        )

    @staticmethod
    def _worker_entity_type_from_employee(employee: Optional[Dict[str, Any]]) -> str:
        resource_type = " ".join(
            str(((employee or {}).get("resource") or {}).get("type") or "").split()
        ).casefold()
        contractor_types = {
            " ".join(str(const.EMPLOYEE_TYPE_1099).split()).casefold(),
            " ".join(
                str(
                    getattr(
                        const,
                        "EMPLOYEE_TYPE_SUBCONTRACTOR",
                        "Subcontractor",
                    )
                ).split()
            ).casefold(),
            " ".join(
                str(getattr(const, "EMPLOYEE_TYPE_CONTRACTOR", "Contractor")).split()
            ).casefold(),
        }
        if resource_type in contractor_types:
            return const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR
        return const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE

    def _classification_change_reason(
        self,
        *,
        employee: Dict[str, Any],
        expected_entity_type: str,
        external_id: str,
        display_name: str,
    ) -> Tuple[str, str]:
        current_entity_type = self._worker_entity_type_from_employee(employee)
        current_label = (
            "1099 contractor"
            if current_entity_type == const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR
            else "W-2 employee"
        )
        expected_label = (
            "1099 contractor"
            if expected_entity_type == const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR
            else "W-2 employee"
        )
        message = (
            f"Worker type change was blocked for {display_name or external_id}. "
            f"This worker is currently a {current_label} in FIELDCRAFT but is linked as a {expected_label} in QuickBooks. "
            "A worker cannot switch between W-2 and 1099 after synchronization. "
            "Restore the original worker type, then run employee sync again."
        )
        return message, self._build_reason(
            message,
            code="WORKER_TYPE_CHANGE_NOT_ALLOWED",
            current_worker_type=current_label,
            linked_worker_type=expected_label,
            external_id=external_id,
            display_name=display_name or None,
            action_required="Restore the original W-2/1099 worker type in FIELDCRAFT, then run employee sync again.",
        )

    def _enrich_imported_worker_best_effort(
        self,
        *,
        tenant_id: str,
        employee: Dict[str, Any],
        payload: Dict[str, Any],
        detail: Dict[str, Any],
    ) -> None:
        warnings: List[str] = []
        address_info = payload.get("personal_additional_info") or {}
        if address_info:
            try:
                self.employee_repo.upsert_personal_additional_info(
                    tenant_id=tenant_id,
                    employee=employee,
                    address_info=address_info,
                )
            except Exception as exc:
                logger.exception(
                    "[EmployeeImport] worker address enrichment failed after mapping was stored | employee_id=%s",
                    employee.get("id"),
                )
                warnings.append(f"Address details could not be saved: {exc}")

        try:
            resource = self.employee_repo.upsert_resource_for_employee(
                tenant_id=tenant_id,
                employee=employee,
                resource_payload=payload.get("resource") or {},
            )
            if resource and resource.get("id") is not None:
                detail["resourceId"] = resource.get("id")
        except Exception as exc:
            logger.exception(
                "[EmployeeImport] worker resource enrichment failed after mapping was stored | employee_id=%s",
                employee.get("id"),
            )
            warnings.append(f"Worker details could not be fully saved: {exc}")

        if warnings:
            detail["warnings"] = warnings

    def _quarantine_unbound_import_mapping(
        self,
        *,
        tenant_id: str,
        service_id: str,
        user_account_id: int,
        expected_entity_type: str,
        reason: str,
    ) -> None:
        """Prevent an import-created worker from being exported as a duplicate."""
        quarantine = getattr(
            self.employee_repo,
            "mark_unbound_import_mapping_failed",
            None,
        )
        if not callable(quarantine):
            return
        try:
            quarantine(
                tenant_id=tenant_id,
                service_id=service_id,
                user_account_id=int(user_account_id),
                expected_entity_type=expected_entity_type,
                reason=reason,
            )
        except Exception:
            logger.exception(
                "[EmployeeImport] failed to quarantine unbound imported worker | "
                "tenant_id=%s service_id=%s employee_id=%s",
                tenant_id,
                service_id,
                user_account_id,
            )

    def _user_email_exists_in_fieldcraft_or_ad(
        self,
        *,
        tenant_id: str,
        tenant_api_token: str,
        email: str,
    ) -> bool:
        email = self._clean(email)
        if not email:
            return False
        result = bool(self.tenant_client.user_email_exists_in_fieldcraft_or_ad(tenant_api_token, tenant_id, email))
        return result

    def _duplicate_email_reason(
        self,
        *,
        existing_employee_id: Optional[int],
        email: str,
        qbo_employee_id: str,
        display_name: str,
    ) -> Tuple[str, str]:
        employee_label = display_name or f"ID {qbo_employee_id}"
        if existing_employee_id:
            message = (
                f"Sync failed: QuickBooks employee {employee_label} uses {email or 'an email'} "
                "that already belongs to an existing FIELDCRAFT employee. "
                "Update the email in QuickBooks to a unique address, then run employee sync again."
            )
            action_required = "Update the QuickBooks employee email to a unique address, then run employee sync again."
        else:
            message = (
                "This employee could not be synced, there is an issue with the employee account. "
                "Please contact FIELDCRAFT Support for assistance."
            )
            action_required = "Please contact FIELDCRAFT Support for assistance."

        return message, self._build_reason(
            message,
            code=self.REASON_DUPLICATE_EMAIL_NOT_ALLOWED,
            email=email,
            existing_employee_id=existing_employee_id,
            qbo_employee_id=qbo_employee_id,
            display_name=display_name or None,
            action_required=action_required,
        )

    def _mark_employee_missing_required(
        self,
        *,
        summary: Dict[str, Any],
        detail: Dict[str, Any],
        tenant_id: str,
        service_id: str,
        realm_id: str,
        qbo_employee_id: str,
        display_name: str,
        sync_token: Optional[str],
        payload: Optional[Dict[str, Any]],
        current_mapping: Optional[Dict[str, Any]],
        missing_message: str,
    ) -> None:
        reason_text = f"QuickBooks employee {display_name or qbo_employee_id} cannot be imported: {missing_message}"
        reason_payload = self._build_reason(
            reason_text,
            code=self.REASON_MISSING_REQUIRED_FIELDS,
            qbo_employee_id=qbo_employee_id,
            display_name=display_name or None,
            action_required="Add the missing employee fields in QuickBooks, then run employee sync again.",
        )
        employee_id = (current_mapping or {}).get("user_account_id")
        self._mark_employee_import_failed(
            summary=summary,
            detail=detail,
            tenant_id=tenant_id,
            service_id=service_id,
            realm_id=realm_id,
            qbo_employee_id=qbo_employee_id,
            display_name=display_name,
            sync_token=sync_token,
            payload=payload,
            current_mapping=current_mapping,
            reason_text=reason_text,
            reason_payload=reason_payload,
            action="missingRequired",
            employee_id=int(employee_id) if employee_id else None,
            qbo_only_import_failed=not bool(employee_id),
            sync_status=self.employee_repo.STATUS_SYNC_FAILED if employee_id else self.employee_repo.STATUS_IMPORT_FAILED,
        )
        summary["skippedMissingRequiredFields"] += 1

    def _mark_employee_import_failed(
        self,
        *,
        summary: Dict[str, Any],
        detail: Dict[str, Any],
        tenant_id: str,
        service_id: str,
        realm_id: str,
        qbo_employee_id: str,
        display_name: str,
        sync_token: Optional[str],
        payload: Optional[Dict[str, Any]],
        current_mapping: Optional[Dict[str, Any]],
        reason_text: str,
        reason_payload: str,
        action: str,
        employee_id: Optional[int] = None,
        qbo_only_import_failed: bool = False,
        sync_status: Optional[str] = None,
        extra_detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        persisted_mapping = self._persist_import_failed_mapping(
            tenant_id=tenant_id,
            service_id=service_id,
            realm_id=realm_id,
            qbo_employee_id=qbo_employee_id,
            sync_token=sync_token,
            payload=payload,
            current_mapping=current_mapping,
            reason=reason_payload,
            employee_id=employee_id,
            qbo_only_import_failed=qbo_only_import_failed,
            sync_status=sync_status,
            external_display_name=display_name,
        )
        summary["failed"] += 1
        qbo_api.append_failure(summary, {
            const.KEY_QBO_EMPLOYEE_ID: qbo_employee_id,
            const.KEY_DISPLAY_NAME: display_name,
            const.KEY_ERROR: reason_text,
            const.KEY_DETAILS: reason_text,
            const.KEY_REASON: reason_payload,
        })
        detail_payload = {
            const.KEY_STATUS: "failed",
            const.KEY_ACTION: action,
            const.KEY_FIELDCRAFT_EMPLOYEE_ID: None if qbo_only_import_failed else (employee_id or (current_mapping or {}).get("user_account_id")),
            const.KEY_ERROR: reason_text,
            const.KEY_REASON: reason_payload,
        }
        if extra_detail:
            detail_payload.update(extra_detail)
        detail.update(detail_payload)
        summary[const.KEY_RESULTS].append(detail)

    def _process_qbo_employee(
        self,
        *,
        summary: Dict[str, Any],
        tenant_id: str,
        service_id: str,
        realm_id: str,
        qbo_employee: Dict[str, Any],
        dry_run: bool,
        tenant_api_token: str = "",
        days_after_hire: int = 0,
        employee_type: str = "W2",
        access_token: Optional[str] = None,
        base_url: Optional[str] = None,
        cost_sync_enabled: bool = True,
        cost_qbo_field: str = const.EMPLOYEE_COST_QBO_FIELD_COST_RATE,
    ) -> None:
        qbo_employee_id = self._clean(qbo_employee.get(const.QBO_KEY_ID))
        sync_token = self._clean(qbo_employee.get(const.QBO_KEY_SYNC_TOKEN)) or None
        display_name = self._clean(qbo_employee.get(const.QBO_KEY_DISPLAY_NAME))
        detail: Dict[str, Any] = {
            const.KEY_QBO_EMPLOYEE_ID: qbo_employee_id,
            const.KEY_DISPLAY_NAME: display_name,
            const.KEY_STATUS: None,
            const.KEY_ACTION: None,
            const.KEY_FIELDCRAFT_EMPLOYEE_ID: None,
            const.KEY_SYNC_TOKEN: sync_token,
            const.KEY_REASON: None,
            const.KEY_ERROR: None,
        }
        payload: Optional[Dict[str, Any]] = None
        current_mapping: Optional[Dict[str, Any]] = None
        created_id: Optional[int] = None


        try:
            qbo_employee = self._enrich_qbo_employee_for_cost_if_needed(
                qbo_employee,
                access_token=access_token,
                base_url=base_url,
                realm_id=realm_id,
                cost_sync_enabled=cost_sync_enabled,
                cost_qbo_field=cost_qbo_field,
            )
            payload = self._build_employee_payload_from_qbo(
                qbo_employee,
                employee_type=employee_type,
                cost_sync_enabled=cost_sync_enabled,
                cost_qbo_field=cost_qbo_field,
            )
            payload["workStartDate"] = self._work_start_date_from_hire(
                payload.get("hiredDate"),
                days_after_hire=days_after_hire,
            )
            detail[const.KEY_EMAIL] = payload.get(const.KEY_EMAIL)
            detail[const.KEY_PHONE] = payload.get("mobileNo")
            qbo_last_updated_at = self._get_qbo_last_updated_at(qbo_employee)
            current_mapping = self.employee_repo.get_by_external_id(tenant_id, service_id, qbo_employee_id)
            current_mapping_is_import_failed = bool(
                current_mapping
                and current_mapping.get("sync_status") in (self.employee_repo.STATUS_IMPORT_FAILED, self.employee_repo.STATUS_SYNC_FAILED)
            )

            if self._is_qbo_employee_inactive(qbo_employee):
                if current_mapping and current_mapping.get("user_account_id"):
                    reason = self._build_reason(
                        "This employee was made inactive in QuickBooks. Make the employee active in QuickBooks, then run employee sync again.",
                        code=self.REASON_INACTIVE_IN_QBO,
                        qbo_employee_id=qbo_employee_id,
                        display_name=display_name or None,
                        action_required="Make the employee active in QuickBooks, then run employee sync again.",
                    )
                    self.employee_repo.mark_mapping_failed(
                        tenant_id=tenant_id,
                        mapping_id=int(current_mapping["id"]),
                        status=self.employee_repo.STATUS_SYNC_FAILED,
                        reason=reason,
                        external_display_name=display_name,
                    )
                    summary["failed"] += 1
                    qbo_api.append_failure(summary, {
                        const.KEY_QBO_EMPLOYEE_ID: qbo_employee_id,
                        const.KEY_FIELDCRAFT_EMPLOYEE_ID: current_mapping.get("user_account_id"),
                        const.KEY_DISPLAY_NAME: display_name,
                        const.KEY_ERROR: "QuickBooks employee is inactive",
                        const.KEY_DETAILS: reason,
                    })
                    detail.update({
                        const.KEY_STATUS: "failed",
                        const.KEY_ACTION: "qboEmployeeInactive",
                        const.KEY_FIELDCRAFT_EMPLOYEE_ID: current_mapping.get("user_account_id"),
                        const.KEY_ERROR: "QuickBooks employee is inactive",
                        const.KEY_REASON: reason,
                    })
                    summary[const.KEY_RESULTS].append(detail)
                    return

                if current_mapping_is_import_failed and current_mapping and current_mapping.get("id"):
                    self.employee_repo.close_mapping_as_invalid(tenant_id, int(current_mapping["id"]))
                reason = self._build_reason("This employee is inactive in QuickBooks, so it was skipped.", code="EMPLOYEE_INACTIVE_SKIPPED")
                summary["skippedDeleted"] += 1
                detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "inactiveInQbo", const.KEY_REASON: reason})
                summary[const.KEY_RESULTS].append(detail)
                return

            existing_email_owner_id = self.employee_repo.get_employee_id_by_email(
                tenant_id=tenant_id,
                email=payload.get(const.KEY_EMAIL) or "",
            )
            mapping_is_attached_to_duplicate_owner = bool(
                current_mapping_is_import_failed
                and current_mapping
                and current_mapping.get("user_account_id")
                and existing_email_owner_id
                and int(existing_email_owner_id) == int(current_mapping.get("user_account_id"))
                and self.employee_repo.has_current_mapping_for_user_with_different_external_id(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    user_account_id=int(current_mapping.get("user_account_id")),
                    external_employee_id=qbo_employee_id,
                )
            )
            mapped_employee_id = (current_mapping or {}).get("user_account_id")
            if existing_email_owner_id and mapped_employee_id and int(existing_email_owner_id) != int(mapped_employee_id):
                reason_text, reason_payload = self._duplicate_email_reason(
                    existing_employee_id=existing_email_owner_id,
                    email=(payload.get(const.KEY_EMAIL) or "").strip(),
                    qbo_employee_id=qbo_employee_id,
                    display_name=display_name,
                )
                self._mark_employee_import_failed(
                    summary=summary,
                    detail=detail,
                    tenant_id=tenant_id,
                    service_id=service_id,
                    realm_id=realm_id,
                    qbo_employee_id=qbo_employee_id,
                    display_name=display_name,
                    sync_token=sync_token,
                    payload=payload,
                    current_mapping=current_mapping,
                    reason_text=reason_text,
                    reason_payload=reason_payload,
                    action="duplicateEmailSyncFailed",
                    employee_id=int(mapped_employee_id),
                    sync_status=self.employee_repo.STATUS_SYNC_FAILED,
                    extra_detail={"existingFieldCraftEmployeeId": existing_email_owner_id},
                )
                return

            reconcile_owner_mapping = None
            if existing_email_owner_id and (
                not current_mapping
                or not current_mapping.get("user_account_id")
                or mapping_is_attached_to_duplicate_owner
            ):
                reconcile_owner_mapping = self._get_reconcilable_w2_mapping(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    realm_id=realm_id,
                    user_account_id=int(existing_email_owner_id),
                    qbo_employee_id=qbo_employee_id,
                )

            if reconcile_owner_mapping:
                reason = self._build_reason(
                    "The QuickBooks employee was safely linked to the existing pending FIELDCRAFT employee mapping.",
                    code="EMPLOYEE_PENDING_MAPPING_RECONCILED",
                    qbo_employee_id=qbo_employee_id,
                )
                self.employee_repo.claim_external_employee_mapping(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    user_account_id=int(existing_email_owner_id),
                    mapping_id=int(reconcile_owner_mapping["id"]),
                    external_employee_id=qbo_employee_id,
                    external_account_id=realm_id,
                    sync_token=sync_token,
                    reason=reason,
                    external_display_name=display_name,
                )
                summary["linkedExistingInFieldCraft"] += 1
                detail.update({
                    const.KEY_STATUS: "success",
                    const.KEY_ACTION: "reconciled pending employee mapping",
                    const.KEY_FIELDCRAFT_EMPLOYEE_ID: int(existing_email_owner_id),
                    const.KEY_REASON: reason,
                })
                summary[const.KEY_RESULTS].append(detail)
                return

            if existing_email_owner_id and (
                not current_mapping
                or not current_mapping.get("user_account_id")
                or mapping_is_attached_to_duplicate_owner
            ):
                owner_mapping = self.employee_repo.get_current_mapping_by_user_account_id(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    user_account_id=int(existing_email_owner_id),
                )
                owner_employee = self.employee_repo.get_employee_by_id(
                    tenant_id,
                    int(existing_email_owner_id),
                )
                can_recover_missing_mapping = bool(
                    not owner_mapping
                    and owner_employee
                    and self._worker_entity_type_from_employee(owner_employee)
                    == const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE
                )
                if can_recover_missing_mapping and not dry_run:
                    recovery_reason = self._build_reason(
                        "This employee was already in FIELDCRAFT, so it was linked to the matching QuickBooks employee.",
                        code="EMPLOYEE_MISSING_MAPPING_RECOVERED",
                        qbo_employee_id=qbo_employee_id,
                    )
                    try:
                        recovered_mapping = self.employee_repo.claim_external_employee_mapping(
                            tenant_id=tenant_id,
                            service_id=service_id,
                            user_account_id=int(existing_email_owner_id),
                            external_employee_id=qbo_employee_id,
                            external_account_id=realm_id,
                            sync_token=sync_token,
                            reason=recovery_reason,
                            external_display_name=display_name,
                            mapping_id=(
                                int(current_mapping["id"])
                                if current_mapping and current_mapping.get("id")
                                else None
                            ),
                            allow_create=not bool(current_mapping and current_mapping.get("id")),
                        )
                    except ValueError as exc:
                        logger.warning(
                            "[EmployeeImport] missing_mapping_recovery_not_safe | "
                            "tenant_id=%s employee_id=%s qbo_employee_id=%s error=%s",
                            tenant_id,
                            existing_email_owner_id,
                            qbo_employee_id,
                            exc,
                        )
                    else:
                        current_mapping = recovered_mapping
                        current_mapping_is_import_failed = False
                        mapping_is_attached_to_duplicate_owner = False
                        summary["linkedExistingInFieldCraft"] += 1

            if existing_email_owner_id and (
                not current_mapping
                or not current_mapping.get("user_account_id")
                or mapping_is_attached_to_duplicate_owner
            ):
                reason_text, reason_payload = self._duplicate_email_reason(
                    existing_employee_id=existing_email_owner_id,
                    email=(payload.get(const.KEY_EMAIL) or "").strip(),
                    qbo_employee_id=qbo_employee_id,
                    display_name=display_name,
                )
                self._mark_employee_import_failed(
                    summary=summary,
                    detail=detail,
                    tenant_id=tenant_id,
                    service_id=service_id,
                    realm_id=realm_id,
                    qbo_employee_id=qbo_employee_id,
                    display_name=display_name,
                    sync_token=sync_token,
                    payload=payload,
                    current_mapping=current_mapping,
                    reason_text=reason_text,
                    reason_payload=reason_payload,
                    action="duplicateEmailImportFailed",
                    qbo_only_import_failed=True,
                    extra_detail={"existingFieldCraftEmployeeId": existing_email_owner_id},
                )
                return

            if current_mapping and not current_mapping_is_import_failed:
                employee_id = current_mapping.get("user_account_id")
                current_employee = self.employee_repo.get_employee_by_id(tenant_id, employee_id) if employee_id else None
                if not current_employee:
                    raise Exception(f"Mapped FIELDCRAFT employee was not found for QuickBooks employee {qbo_employee_id}.")

                if self._worker_entity_type_from_employee(current_employee) != const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE:
                    reason_text, reason = self._classification_change_reason(
                        employee=current_employee,
                        expected_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE,
                        external_id=qbo_employee_id,
                        display_name=display_name,
                    )
                    self.employee_repo.mark_mapping_failed(
                        tenant_id=tenant_id,
                        mapping_id=int(current_mapping["id"]),
                        status=self.employee_repo.STATUS_SYNC_FAILED,
                        reason=reason,
                        external_display_name=display_name,
                    )
                    summary["failed"] += 1
                    qbo_api.append_failure(summary, {
                        const.KEY_QBO_EMPLOYEE_ID: qbo_employee_id,
                        const.KEY_FIELDCRAFT_EMPLOYEE_ID: employee_id,
                        const.KEY_DISPLAY_NAME: display_name,
                        const.KEY_ERROR: reason_text,
                        const.KEY_DETAILS: reason,
                    })
                    detail.update({
                        const.KEY_STATUS: "failed",
                        const.KEY_ACTION: "workerTypeChangeBlocked",
                        const.KEY_FIELDCRAFT_EMPLOYEE_ID: employee_id,
                        const.KEY_ERROR: reason_text,
                        const.KEY_REASON: reason,
                    })
                    summary[const.KEY_RESULTS].append(detail)
                    return

                if self._employee_email_changed(current_employee, payload):
                    reason_text, reason = self._email_change_reason(
                        current_employee=current_employee,
                        qbo_employee_id=qbo_employee_id,
                        qbo_email=payload.get(const.KEY_EMAIL) or "",
                        display_name=display_name,
                    )
                    self.employee_repo.mark_mapping_failed(
                        tenant_id=tenant_id,
                        mapping_id=int(current_mapping["id"]),
                        status=self.employee_repo.STATUS_SYNC_FAILED,
                        reason=reason,
                        external_display_name=display_name,
                    )
                    summary["failed"] += 1
                    qbo_api.append_failure(summary, {
                        const.KEY_QBO_EMPLOYEE_ID: qbo_employee_id,
                        const.KEY_FIELDCRAFT_EMPLOYEE_ID: employee_id,
                        const.KEY_DISPLAY_NAME: display_name,
                        const.KEY_ERROR: reason_text,
                        const.KEY_DETAILS: reason,
                    })
                    detail.update({
                        const.KEY_STATUS: "failed",
                        const.KEY_ACTION: "employeeEmailChangeBlocked",
                        const.KEY_FIELDCRAFT_EMPLOYEE_ID: employee_id,
                        const.KEY_ERROR: reason_text,
                        const.KEY_REASON: reason,
                    })
                    summary[const.KEY_RESULTS].append(detail)
                    return

                local_latest_at = self.employee_repo.get_employee_latest_local_timestamp(current_employee)
                employee_changed = self.employee_repo.sync_projection_changed(current_employee, payload)
                if not employee_changed:
                    reason = self._build_reason("Employee is already up to date in FIELDCRAFT and QuickBooks.", code="EMPLOYEE_ALREADY_CURRENT")
                    self.employee_repo.mark_mapping_synced(
                        tenant_id=tenant_id,
                        mapping_id=int(current_mapping["id"]),
                        external_employee_id=qbo_employee_id,
                        external_account_id=realm_id,
                        sync_token=sync_token,
                        reason=reason,
                        external_display_name=display_name,
                    )
                    summary["skippedAlreadyCurrent"] += 1
                    detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "alreadyCurrent", const.KEY_FIELDCRAFT_EMPLOYEE_ID: employee_id, const.KEY_REASON: reason})
                    summary[const.KEY_RESULTS].append(detail)
                    return

                if self._should_skip_import_update(qbo_last_updated_at, local_latest_at):
                    reason = self._build_reason(
                        "Your FIELDCRAFT employee is more recent than QuickBooks. No changes were imported to protect your latest edits.",
                        code="EMPLOYEE_FIELDCRAFT_NEWER",
                        qbo_updated_at=qbo_last_updated_at.isoformat() if qbo_last_updated_at else None,
                        fieldcraft_updated_at=local_latest_at.isoformat() if local_latest_at else None,
                    )
                    summary["skippedLocalNewer"] += 1
                    detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "localNewer", const.KEY_FIELDCRAFT_EMPLOYEE_ID: employee_id, const.KEY_REASON: reason})
                    summary[const.KEY_RESULTS].append(detail)
                    return

                if dry_run:
                    reason = self._build_reason("Preview only — no employee changes have been saved.", code="EMPLOYEE_DRY_RUN")
                    detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "dryRun", const.KEY_FIELDCRAFT_EMPLOYEE_ID: employee_id, const.KEY_REASON: reason})
                    summary[const.KEY_RESULTS].append(detail)
                    return

                updated_employee = self.employee_repo.update_employee_from_qbo_payload(
                    tenant_id=tenant_id,
                    user_account_id=int(employee_id),
                    payload=payload,
                )
                updated_id = int(updated_employee.get("id") or employee_id)
                reason = self._build_reason("Employee details updated in FIELDCRAFT from QuickBooks.", code="EMPLOYEE_UPDATED_IN_FIELDCRAFT")
                self.employee_repo.mark_mapping_synced(
                    tenant_id=tenant_id,
                    mapping_id=int(current_mapping["id"]),
                    external_employee_id=qbo_employee_id,
                    external_account_id=realm_id,
                    sync_token=sync_token,
                    reason=reason,
                    external_display_name=display_name,
                )
                summary["updatedInFIELDCRAFT"] += 1
                detail.update({const.KEY_STATUS: "success", const.KEY_ACTION: "updated in FIELDCRAFT", const.KEY_FIELDCRAFT_EMPLOYEE_ID: updated_id, const.KEY_REASON: reason})
                summary[const.KEY_RESULTS].append(detail)
                return

            if current_mapping_is_import_failed and current_mapping and current_mapping.get("user_account_id"):
                employee_id = current_mapping.get("user_account_id")
                current_employee = self.employee_repo.get_employee_by_id(tenant_id, employee_id) if employee_id else None

                if not current_employee:
                    reason_text = f"Mapped FIELDCRAFT employee was not found for QuickBooks employee {display_name or qbo_employee_id}."
                    reason_payload = self._build_reason(
                        reason_text,
                        code="EMPLOYEE_MAPPED_FIELDCRAFT_RECORD_MISSING",
                        qbo_employee_id=qbo_employee_id,
                        display_name=display_name or None,
                        action_required="Restore the FIELDCRAFT employee or unlink the QuickBooks employee mapping, then run employee sync again.",
                    )
                    self._mark_employee_import_failed(
                        summary=summary,
                        detail=detail,
                        tenant_id=tenant_id,
                        service_id=service_id,
                        realm_id=realm_id,
                        qbo_employee_id=qbo_employee_id,
                        display_name=display_name,
                        sync_token=sync_token,
                        payload=payload,
                        current_mapping=current_mapping,
                        reason_text=reason_text,
                        reason_payload=reason_payload,
                        action="mappedEmployeeMissing",
                        employee_id=int(employee_id) if employee_id else None,
                        sync_status=self.employee_repo.STATUS_SYNC_FAILED,
                    )
                    return

                if self._employee_email_changed(current_employee, payload):
                    reason_text, reason = self._email_change_reason(
                        current_employee=current_employee,
                        qbo_employee_id=qbo_employee_id,
                        qbo_email=payload.get(const.KEY_EMAIL) or "",
                        display_name=display_name,
                    )
                    self.employee_repo.mark_mapping_failed(
                        tenant_id=tenant_id,
                        mapping_id=int(current_mapping["id"]),
                        status=self.employee_repo.STATUS_SYNC_FAILED,
                        reason=reason,
                        external_display_name=display_name,
                    )
                    summary["failed"] += 1
                    qbo_api.append_failure(summary, {
                        const.KEY_QBO_EMPLOYEE_ID: qbo_employee_id,
                        const.KEY_FIELDCRAFT_EMPLOYEE_ID: employee_id,
                        const.KEY_DISPLAY_NAME: display_name,
                        const.KEY_ERROR: reason_text,
                        const.KEY_DETAILS: reason,
                    })
                    detail.update({
                        const.KEY_STATUS: "failed",
                        const.KEY_ACTION: "employeeEmailChangeBlocked",
                        const.KEY_FIELDCRAFT_EMPLOYEE_ID: employee_id,
                        const.KEY_ERROR: reason_text,
                        const.KEY_REASON: reason,
                    })
                    summary[const.KEY_RESULTS].append(detail)
                    return

                local_latest_at = self.employee_repo.get_employee_latest_local_timestamp(current_employee)
                employee_changed = self.employee_repo.sync_projection_changed(current_employee, payload)
                if not employee_changed:
                    reason = self._build_reason(
                        "Previous employee sync issue was resolved. Employee is already up to date in FIELDCRAFT and QuickBooks.",
                        code="EMPLOYEE_PREVIOUS_FAILURE_RESOLVED",
                    )
                    self.employee_repo.mark_mapping_synced(
                        tenant_id=tenant_id,
                        mapping_id=int(current_mapping["id"]),
                        external_employee_id=qbo_employee_id,
                        external_account_id=realm_id,
                        sync_token=sync_token,
                        reason=reason,
                        external_display_name=display_name,
                    )
                    summary["linkedExistingInFieldCraft"] += 1
                    detail.update({
                        const.KEY_STATUS: "success",
                        const.KEY_ACTION: "resolved import failure",
                        const.KEY_FIELDCRAFT_EMPLOYEE_ID: employee_id,
                        const.KEY_REASON: reason,
                    })
                    summary[const.KEY_RESULTS].append(detail)
                    return

                if self._should_skip_import_update(qbo_last_updated_at, local_latest_at):
                    reason = self._build_reason(
                        "Previous employee sync issue is ready to retry, but FIELDCRAFT has newer employee edits. The employee will be pushed to QuickBooks on the export step.",
                        code="EMPLOYEE_PREVIOUS_FAILURE_PENDING_PUSH",
                        qbo_updated_at=qbo_last_updated_at.isoformat() if qbo_last_updated_at else None,
                        fieldcraft_updated_at=local_latest_at.isoformat() if local_latest_at else None,
                    )
                    self.employee_repo.rotate_mapping_for_employee(
                        tenant_id=tenant_id,
                        service_id=service_id,
                        user_account_id=int(employee_id),
                        external_employee_id=qbo_employee_id,
                        external_account_id=realm_id,
                        sync_status=self.employee_repo.STATUS_PENDING_PUSH,
                        sync_token=sync_token,
                        reason=reason,
                        external_display_name=display_name,
                    )
                    summary["skippedLocalNewer"] += 1
                    detail.update({
                        const.KEY_STATUS: "skipped",
                        const.KEY_ACTION: "localNewer",
                        const.KEY_FIELDCRAFT_EMPLOYEE_ID: employee_id,
                        const.KEY_REASON: reason,
                    })
                    summary[const.KEY_RESULTS].append(detail)
                    return

                if dry_run:
                    reason = self._build_reason("Preview only - no employee changes have been saved.", code="EMPLOYEE_DRY_RUN")
                    detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "dryRun", const.KEY_FIELDCRAFT_EMPLOYEE_ID: employee_id, const.KEY_REASON: reason})
                    summary[const.KEY_RESULTS].append(detail)
                    return

                updated_employee = self.employee_repo.update_employee_from_qbo_payload(
                    tenant_id=tenant_id,
                    user_account_id=int(employee_id),
                    payload=payload,
                )
                updated_id = int(updated_employee.get("id") or employee_id)
                reason = self._build_reason(
                    "Previous employee sync issue was resolved, and QuickBooks employee details were imported to FIELDCRAFT.",
                    code="EMPLOYEE_PREVIOUS_FAILURE_IMPORTED",
                )
                self.employee_repo.mark_mapping_synced(
                    tenant_id=tenant_id,
                    mapping_id=int(current_mapping["id"]),
                    external_employee_id=qbo_employee_id,
                    external_account_id=realm_id,
                    sync_token=sync_token,
                    reason=reason,
                    external_display_name=display_name,
                )
                summary["updatedInFIELDCRAFT"] += 1
                detail.update({
                    const.KEY_STATUS: "success",
                    const.KEY_ACTION: "resolved import failure",
                    const.KEY_FIELDCRAFT_EMPLOYEE_ID: updated_id,
                    const.KEY_REASON: reason,
                })
                summary[const.KEY_RESULTS].append(detail)
                return

            if current_mapping_is_import_failed and current_mapping:
                if self._reason_has_code(current_mapping.get("reason"), self.REASON_DUPLICATE_EMAIL_NOT_ALLOWED):
                    email_lookup = payload.get(const.KEY_EMAIL) or ""
                    existing_email_owner_id = self.employee_repo.get_employee_id_by_email(
                        tenant_id=tenant_id,
                        email=email_lookup,
                    )
                    email_exists_in_fieldcraft_or_ad = False
                    if not existing_email_owner_id:
                        email_exists_in_fieldcraft_or_ad = self._user_email_exists_in_fieldcraft_or_ad(
                            tenant_id=tenant_id,
                            tenant_api_token=tenant_api_token,
                            email=email_lookup,
                        )
                    if existing_email_owner_id or email_exists_in_fieldcraft_or_ad:
                        reason_text, reason_payload = self._duplicate_email_reason(
                            existing_employee_id=existing_email_owner_id,
                            email=(email_lookup or "").strip(),
                            qbo_employee_id=qbo_employee_id,
                            display_name=display_name,
                        )
                        extra_detail = {"emailExistsInFieldCraftOrAd": bool(email_exists_in_fieldcraft_or_ad)}
                        if existing_email_owner_id:
                            extra_detail["existingFieldCraftEmployeeId"] = existing_email_owner_id
                        self._mark_employee_import_failed(
                            summary=summary,
                            detail=detail,
                            tenant_id=tenant_id,
                            service_id=service_id,
                            realm_id=realm_id,
                            qbo_employee_id=qbo_employee_id,
                            display_name=display_name,
                            sync_token=sync_token,
                            payload=payload,
                            current_mapping=current_mapping,
                            reason_text=reason_text,
                            reason_payload=reason_payload,
                            action="duplicateEmailImportFailed",
                            qbo_only_import_failed=True,
                            extra_detail=extra_detail,
                        )
                        return

                    self.employee_repo.close_mapping_as_invalid(tenant_id, int(current_mapping["id"]))
                    current_mapping = None
                    current_mapping_is_import_failed = False

            email_lookup = payload.get(const.KEY_EMAIL) or ""
            existing_employee_id = self.employee_repo.get_employee_id_by_email(tenant_id=tenant_id, email=email_lookup)
            email_exists_in_fieldcraft_or_ad = False
            if not existing_employee_id:
                email_exists_in_fieldcraft_or_ad = self._user_email_exists_in_fieldcraft_or_ad(
                    tenant_id=tenant_id,
                    tenant_api_token=tenant_api_token,
                    email=email_lookup,
                )
            if existing_employee_id or email_exists_in_fieldcraft_or_ad:
                reason_text, reason_payload = self._duplicate_email_reason(
                    existing_employee_id=existing_employee_id,
                    email=(email_lookup or "").strip(),
                    qbo_employee_id=qbo_employee_id,
                    display_name=display_name,
                )
                extra_detail = {"emailExistsInFieldCraftOrAd": bool(email_exists_in_fieldcraft_or_ad)}
                if existing_employee_id:
                    extra_detail["existingFieldCraftEmployeeId"] = existing_employee_id
                self._mark_employee_import_failed(
                    summary=summary,
                    detail=detail,
                    tenant_id=tenant_id,
                    service_id=service_id,
                    realm_id=realm_id,
                    qbo_employee_id=qbo_employee_id,
                    display_name=display_name,
                    sync_token=sync_token,
                    payload=payload,
                    current_mapping=current_mapping,
                    reason_text=reason_text,
                    reason_payload=reason_payload,
                    action="duplicateEmailImportFailed",
                    qbo_only_import_failed=True,
                    extra_detail=extra_detail,
                )
                return

            if dry_run:
                reason = self._build_reason("Preview only — no employee changes have been saved.", code="EMPLOYEE_DRY_RUN")
                detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "dryRun", const.KEY_REASON: reason})
                summary[const.KEY_RESULTS].append(detail)
                return

            ok_payload, missing_message = self._is_payload_valid_for_tenant_api(payload)
            if not ok_payload:
                self._mark_employee_missing_required(
                    summary=summary,
                    detail=detail,
                    tenant_id=tenant_id,
                    service_id=service_id,
                    realm_id=realm_id,
                    qbo_employee_id=qbo_employee_id,
                    display_name=display_name,
                    sync_token=sync_token,
                    payload=payload,
                    current_mapping=current_mapping,
                    missing_message=missing_message,
                )
                return

            created_id = self._save_employee_through_tenant_api(
                tenant_api_token=tenant_api_token,
                qbo_employee=qbo_employee,
                service_id=service_id,
                existing_employee=None,
                days_after_hire=days_after_hire,
                employee_type=employee_type,
                cost_sync_enabled=cost_sync_enabled,
                cost_qbo_field=cost_qbo_field,
            )
            reason = self._build_reason(
                "Employee was created in FIELDCRAFT from QuickBooks and linked successfully.",
                code="EMPLOYEE_CREATED_IN_FIELDCRAFT",
            )
            try:
                self.employee_repo.claim_external_employee_mapping(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    user_account_id=int(created_id),
                    external_employee_id=qbo_employee_id,
                    external_account_id=realm_id,
                    sync_token=sync_token,
                    reason=reason,
                    external_display_name=display_name,
                    allow_create=True,
                )
            except Exception as exc:
                quarantine_reason = self._build_reason(
                    "Employee was created in FIELDCRAFT, but its QuickBooks Employee mapping "
                    "could not be stored. The worker was held for import retry to prevent "
                    "a duplicate QuickBooks employee.",
                    code="EMPLOYEE_MAPPING_CLAIM_FAILED",
                    qbo_employee_id=qbo_employee_id,
                    error=str(exc),
                )
                self._quarantine_unbound_import_mapping(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    user_account_id=int(created_id),
                    expected_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE,
                    reason=quarantine_reason,
                )
                raise
            created_employee = self.employee_repo.get_employee_by_id(tenant_id, int(created_id))
            created_employee = created_employee or {"id": int(created_id)}
            self._enrich_imported_worker_best_effort(
                tenant_id=tenant_id,
                employee=created_employee,
                payload=payload,
                detail=detail,
            )
            summary["created in FIELDCRAFT"] += 1
            detail.update({
                const.KEY_STATUS: "success",
                const.KEY_ACTION: "created in FIELDCRAFT",
                const.KEY_FIELDCRAFT_EMPLOYEE_ID: created_id,
                const.KEY_REASON: reason,
            })
            summary[const.KEY_RESULTS].append(detail)
        except Exception as exc:
            intuit_tid = exc.intuit_tid if isinstance(exc, QuickBooksAPIError) else None
            reason = self._build_reason(
                exc.user_message if isinstance(exc, QuickBooksAPIError) else str(exc),
                intuit_tid=intuit_tid,
                code="EMPLOYEE_IMPORT_FAILED_RETRY_SAVED",
            )
            failed_mapping = self._persist_import_failed_mapping(
                tenant_id=tenant_id,
                service_id=service_id,
                realm_id=realm_id,
                qbo_employee_id=qbo_employee_id,
                sync_token=sync_token,
                payload=payload,
                current_mapping=current_mapping,
                reason=reason,
                external_display_name=display_name,
            )
            summary["failed"] += 1
            qbo_api.append_failure(summary, {
                const.KEY_QBO_EMPLOYEE_ID: qbo_employee_id,
                const.KEY_DISPLAY_NAME: display_name,
                const.KEY_ERROR: str(exc),
            })
            detail.update({const.KEY_STATUS: "failed", const.KEY_ACTION: "error", const.KEY_ERROR: str(exc), const.KEY_REASON: reason})
            summary[const.KEY_RESULTS].append(detail)

    def _get_reconcilable_w2_mapping(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        realm_id: str,
        user_account_id: int,
        qbo_employee_id: str,
    ) -> Optional[Dict[str, Any]]:
        mapping = self.employee_repo.get_current_mapping_by_user_account_id(
            tenant_id=tenant_id,
            service_id=service_id,
            user_account_id=int(user_account_id),
        )
        if not isinstance(mapping, dict) or not mapping.get("id"):
            return None

        status = str(mapping.get("sync_status") or "").strip().upper()
        claimable_statuses = {
            self.employee_repo.STATUS_PENDING_PUSH,
            self.employee_repo.STATUS_FAILED,
            self.employee_repo.STATUS_SYNC_FAILED,
            self.employee_repo.STATUS_IMPORT_FAILED,
        }
        if status not in claimable_statuses:
            return None

        mapped_realm = str(mapping.get("external_account_id") or "").strip()
        if mapped_realm and mapped_realm != str(realm_id or "").strip():
            return None

        entity_type = str(mapping.get("external_entity_type") or "").strip().upper()
        if entity_type and entity_type != const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE:
            return None
        if str(mapping.get("external_vendor_id") or "").strip():
            return None

        mapped_employee_id = str(mapping.get("external_employee_id") or "").strip()
        if mapped_employee_id and mapped_employee_id != str(qbo_employee_id or "").strip():
            return None
        return mapping

    def _empty_summary(self, *, tenant_id: str, service_id: str) -> Dict[str, Any]:
        return {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": str(service_id),
            "processed": 0,
            "linkedExistingInFieldCraft": 0,
            "created in FIELDCRAFT": 0,
            "updatedInFIELDCRAFT": 0,
            "skippedAlreadyCurrent": 0,
            "skippedLocalNewer": 0,
            "skippedDeleted": 0,
            "skippedMissingRequiredFields": 0,
            "failed": 0,
            const.KEY_FAILURES: [],
            const.KEY_RESULTS: [],
            "startedAt": datetime.now(timezone.utc).isoformat(),
            "qboCdcEntities": ["Employee", "Vendor"],
            "qboCdcNote": "QuickBooks 1099 workers are returned by the Vendor CDC entity.",
        }

    def _finalize_summary(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        failed = int(summary.get("failed") or 0)
        summary[const.KEY_OK] = failed == 0
        if failed:
            summary[const.KEY_MESSAGE] = (
                f"Employee sync completed with {failed} worker record(s) that need attention. "
                "Review the reason shown for each worker, correct it in QuickBooks, and sync again."
            )
        else:
            summary[const.KEY_MESSAGE] = "Employees and 1099 contractors are synced and up to date."
        return summary

    def _save_employee_through_tenant_api(
        self,
        *,
        tenant_api_token: str,
        qbo_employee: Dict[str, Any],
        service_id: Any,
        existing_employee: Optional[Dict[str, Any]],
        days_after_hire: int = 0,
        employee_type: str = "W2",
        cost_sync_enabled: bool = True,
        cost_qbo_field: str = const.EMPLOYEE_COST_QBO_FIELD_COST_RATE,
    ) -> int:
        dto = self._build_tenant_employee_payload_from_qbo(
            qbo_employee,
            service_id=service_id,
            existing_employee=existing_employee,
            days_after_hire=days_after_hire,
            employee_type=employee_type,
            cost_sync_enabled=cost_sync_enabled,
            cost_qbo_field=cost_qbo_field,
        )
        self._validate_tenant_employee_dto(dto)
        try:
            created_or_updated_id = self.tenant_client.create_user(tenant_api_token, dto)
            return int(created_or_updated_id)
        except Exception as exc:
            raise

    def _validate_tenant_employee_dto(self, dto: Dict[str, Any]) -> None:
        roles = dto.get("roles")
        if not isinstance(roles, list) or not roles:
            raise ValueError("Tenant employee DTO must contain a non-empty roles list")
        role = roles[0]
        if not isinstance(role, dict):
            raise ValueError("Tenant employee DTO roles[0] must be an object")
        if not self._clean(role.get("roles")):
            raise ValueError("Tenant employee DTO roles[0].roles is required")
        if not self._clean(role.get("assignStartDate")):
            raise ValueError(
                "Employee work start date could not be calculated. "
                "Ensure the employee has a hire date set in QuickBooks, then run employee sync again."
            )

    def _persist_import_failed_mapping(
        self,
        *,
        tenant_id: str,
        service_id: str,
        realm_id: str,
        qbo_employee_id: str,
        sync_token: Optional[str],
        payload: Optional[Dict[str, Any]],
        current_mapping: Optional[Dict[str, Any]],
        reason: Optional[str],
        employee_id: Optional[int] = None,
        qbo_only_import_failed: bool = False,
        sync_status: Optional[str] = None,
        external_display_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            target_employee_id = None
            if not qbo_only_import_failed:
                if employee_id is not None:
                    target_employee_id = int(employee_id)
                elif current_mapping and current_mapping.get("user_account_id"):
                    target_employee_id = current_mapping.get("user_account_id")
                elif payload:
                    email_lookup = self._clean(payload.get(const.KEY_EMAIL))
                    if email_lookup:
                        target_employee_id = self.employee_repo.get_employee_id_by_email(tenant_id=tenant_id, email=email_lookup)
            persisted = self.employee_repo.upsert_import_failed_mapping(
                tenant_id=tenant_id,
                service_id=service_id,
                external_employee_id=qbo_employee_id,
                external_account_id=realm_id,
                user_account_id=target_employee_id,
                sync_token=sync_token,
                reason=reason or self._build_reason("Employee import failed and will be retried on the next employee sync."),
                sync_status=sync_status,
                detach_employee=qbo_only_import_failed,
                external_display_name=external_display_name,
            )
            return persisted
        except Exception as exc:
            logger.warning(
                "[EmployeeImport] failed to persist IMPORT_FAILED mapping | tenant_id=%s service_id=%s qbo_employee_id=%s error=%s",
                tenant_id,
                service_id,
                qbo_employee_id,
                str(exc),
                exc_info=True,
            )
            return None

    def _first_qbo_phone(self, qbo_employee: Dict[str, Any]) -> str:
        for field_name in ("PrimaryPhone", "Mobile", "AlternatePhone", "HomePhone", "Fax"):
            phone_block = qbo_employee.get(field_name) or {}
            if not isinstance(phone_block, dict):
                continue
            phone = self._normalize_phone(phone_block.get("FreeFormNumber"))
            if phone:
                return phone
        return ""

    def _extract_employee_address_from_qbo(self, qbo_employee: Dict[str, Any]) -> Optional[Dict[str, str]]:
        address = qbo_employee.get("PrimaryAddr") or qbo_employee.get("BillAddr") or {}
        if not isinstance(address, dict):
            return None

        lines = []
        for key in ("Line1", "Line2", "Line3", "Line4", "Line5"):
            value = self._clean(address.get(key))
            if value:
                lines.append(value)

        address_info = {
            "address": ", ".join(lines),
            "city": self._clean(address.get("City")),
            "state": self._clean(address.get("CountrySubDivisionCode")),
            "country": self._clean(address.get("Country")),
            "zip_code": self._clean(address.get("PostalCode")),
        }
        return address_info if any(address_info.values()) else None

    def _decimal_from_qbo(self, value: Any) -> Optional[Decimal]:
        if value is None:
            return None
        if isinstance(value, dict):
            for key in ("value", "Value", "amount", "Amount", "rate", "Rate"):
                parsed = self._decimal_from_qbo(value.get(key))
                if parsed is not None:
                    return parsed
            return None
        raw = self._clean(value)
        if not raw or raw.lower() in ("nan", "none", "null"):
            return None
        try:
            return Decimal(raw.replace(",", "")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            return None

    def _normalize_cost_qbo_field(self, value: Any) -> str:
        field = self._clean(value) or const.EMPLOYEE_COST_QBO_FIELD_COST_RATE
        if field not in const.EMPLOYEE_COST_QBO_FIELDS_ALLOWED:
            return const.EMPLOYEE_COST_QBO_FIELD_COST_RATE
        return field

    def get_qbo_employee_billing_rate(self, qbo_employee: Dict[str, Any]) -> Optional[Decimal]:
        """Return QuickBooks Employee.BillRate for backward compatibility."""
        if not isinstance(qbo_employee, dict) or self.QBO_BILL_RATE_FIELD not in qbo_employee:
            return None

        parsed = self._decimal_from_qbo(qbo_employee.get(self.QBO_BILL_RATE_FIELD))
        if parsed is None or parsed < Decimal("0"):
            return None
        return parsed

    def get_qbo_employee_cost_rate(
        self,
        qbo_employee: Dict[str, Any],
        *,
        preferred_field: str = const.EMPLOYEE_COST_QBO_FIELD_COST_RATE,
    ) -> Tuple[Optional[Decimal], Optional[str]]:
        """Return the configured QBO employee cost value and the field used.

        CostRate is the default. BillRate remains a supported fallback so existing
        tenants and historical QuickBooks employee data continue to sync unchanged.
        """
        if not isinstance(qbo_employee, dict):
            return None, None

        preferred = self._normalize_cost_qbo_field(preferred_field)
        fallback = (
            const.EMPLOYEE_COST_QBO_FIELD_BILL_RATE
            if preferred == const.EMPLOYEE_COST_QBO_FIELD_COST_RATE
            else const.EMPLOYEE_COST_QBO_FIELD_COST_RATE
        )
        for field in (preferred, fallback):
            if field not in qbo_employee:
                continue
            parsed = self._decimal_from_qbo(qbo_employee.get(field))
            if parsed is not None and parsed >= Decimal("0"):
                return parsed, field
        return None, None

    def _has_qbo_employee_cost(
        self,
        qbo_employee: Dict[str, Any],
        *,
        preferred_field: str = const.EMPLOYEE_COST_QBO_FIELD_COST_RATE,
    ) -> bool:
        rate, _ = self.get_qbo_employee_cost_rate(
            qbo_employee or {},
            preferred_field=preferred_field,
        )
        return rate is not None

    def _merge_qbo_employee_snapshots(self, snapshots: List[Dict[str, Any]]) -> Dict[str, Any]:
        valid_snapshots = [item for item in snapshots if isinstance(item, dict) and item]
        if not valid_snapshots:
            return {}

        merged: Dict[str, Any] = {}
        rate_sources: Dict[str, Dict[str, Any]] = {}
        for snapshot in valid_snapshots:
            merged.update(snapshot)
            for field in const.EMPLOYEE_COST_QBO_FIELDS_ALLOWED:
                parsed = self._decimal_from_qbo(snapshot.get(field)) if field in snapshot else None
                if parsed is not None and parsed >= Decimal("0"):
                    rate_sources[field] = snapshot

        for field, source in rate_sources.items():
            merged[field] = source.get(field)
        bill_rate_source = rate_sources.get(const.EMPLOYEE_COST_QBO_FIELD_BILL_RATE)
        if bill_rate_source is not None and self.QBO_BILLABLE_TIME_FIELD in bill_rate_source:
            merged[self.QBO_BILLABLE_TIME_FIELD] = bill_rate_source.get(self.QBO_BILLABLE_TIME_FIELD)
        return merged

    def _enrich_qbo_employee_for_cost_if_needed(
        self,
        qbo_employee: Dict[str, Any],
        *,
        access_token: Optional[str],
        base_url: Optional[str],
        realm_id: str,
        cost_sync_enabled: bool = True,
        cost_qbo_field: str = const.EMPLOYEE_COST_QBO_FIELD_COST_RATE,
    ) -> Dict[str, Any]:
        """Fetch employee detail when a list/CDC row omits the configured rate."""
        preferred_field = self._normalize_cost_qbo_field(cost_qbo_field)
        if not cost_sync_enabled or self._has_qbo_employee_cost(
            qbo_employee,
            preferred_field=preferred_field,
        ):
            return qbo_employee

        qbo_employee_id = self._clean(qbo_employee.get(const.QBO_KEY_ID))
        if not access_token or not base_url or not realm_id or not qbo_employee_id:
            return qbo_employee

        try:
            detailed_employee = self._fetch_qbo_employee_by_id(
                access_token=access_token,
                base_url=base_url,
                realm_id=realm_id,
                employee_id=qbo_employee_id,
            )
            return self._merge_qbo_employee_snapshots([qbo_employee, detailed_employee])
        except Exception:
            logger.warning(
                "[EmployeeImport] QBO employee detail fetch failed | qbo_employee_id=%s",
                qbo_employee_id,
                exc_info=True,
            )
            return qbo_employee

    def _build_resource_payload_from_qbo(
        self,
        qbo_employee: Dict[str, Any],
        *,
        employee_type: str,
        cost_sync_enabled: bool = True,
        cost_qbo_field: str = const.EMPLOYEE_COST_QBO_FIELD_COST_RATE,
    ) -> Dict[str, Any]:
        preferred_field = self._normalize_cost_qbo_field(cost_qbo_field)
        cost, source_field = (
            self.get_qbo_employee_cost_rate(qbo_employee, preferred_field=preferred_field)
            if cost_sync_enabled
            else (None, None)
        )
        resource_type = employee_type if employee_type in const.EMPLOYEE_TYPES_ALLOWED else const.EMPLOYEE_TYPE_W2

        payload: Dict[str, Any] = {
            "type": resource_type,
            "unit": const.EMPLOYEE_COST_UNIT_HOURLY,
            "cost_present": cost is not None,
        }
        if self.QBO_BILLABLE_TIME_FIELD in qbo_employee:
            payload["qbo_billable_time"] = qbo_employee.get(self.QBO_BILLABLE_TIME_FIELD)
        if cost is not None and source_field:
            source_name = (
                "quickbooksEmployeeCostRate"
                if source_field == const.EMPLOYEE_COST_QBO_FIELD_COST_RATE
                else "quickbooksEmployeeBillRate"
            )
            payload.update(
                {
                    "cost": str(cost),
                    "source": source_name,
                    "qbo_field": source_field,
                }
            )
            if source_field == const.EMPLOYEE_COST_QBO_FIELD_COST_RATE:
                payload["qbo_cost_rate"] = str(cost)
            else:
                payload["qbo_bill_rate"] = str(cost)
        return payload

    def _build_employee_payload_from_qbo(
        self,
        qbo_employee: Dict[str, Any],
        *,
        employee_type: str = "W2",
        cost_sync_enabled: bool = True,
        cost_qbo_field: str = const.EMPLOYEE_COST_QBO_FIELD_COST_RATE,
    ) -> Dict[str, Any]:
        display_name = self._clean(qbo_employee.get(const.QBO_KEY_DISPLAY_NAME))
        given = self._clean(qbo_employee.get("GivenName"))
        middle = self._clean(qbo_employee.get("MiddleName"))
        family = self._clean(qbo_employee.get("FamilyName"))
        if not given:
            given, family = self._split_name(display_name)
        email_block = qbo_employee.get("PrimaryEmailAddr") or {}
        phone = self._first_qbo_phone(qbo_employee)
        address_info = self._extract_employee_address_from_qbo(qbo_employee)
        payload = {
            "firstname": given,
            "lastname": family,
            "middlename": middle,
            const.KEY_EMAIL: self._clean(email_block.get("Address")),
            "mobileNo": phone,
            "mobile_no": phone,
            const.KEY_PHONE: phone,
            const.KEY_STATUS: "Inactive" if qbo_employee.get("Active") is False else "Invited",
            "active": False if qbo_employee.get("Active") is False else None,
            "birthdate": self._qbo_date_to_iso(qbo_employee.get("BirthDate")),
            "hiredDate": self._qbo_date_to_iso(qbo_employee.get("HiredDate")),
            "workStartDate": self._qbo_date_to_iso(qbo_employee.get("HiredDate")),
        }
        if address_info:
            payload["personal_additional_info"] = address_info
            payload["address"] = address_info.get("address")
            payload["city"] = address_info.get("city")
            payload["state"] = address_info.get("state")
            payload["country"] = address_info.get("country")
            payload["zip_code"] = address_info.get("zip_code")
        payload["resource"] = self._build_resource_payload_from_qbo(
            qbo_employee,
            employee_type=employee_type,
            cost_sync_enabled=cost_sync_enabled,
            cost_qbo_field=cost_qbo_field,
        )
        return payload

    def _work_start_date_from_hire(
        self,
        hired_date: Optional[str],
        *,
        days_after_hire: int,
    ) -> Optional[str]:
        if not hired_date:
            return None
        if days_after_hire <= 0:
            return hired_date

        hired_dt = self._parse_timestamp(hired_date)
        if hired_dt is None:
            return hired_date
        return (
            self._ensure_aware(hired_dt) + timedelta(days=int(days_after_hire))
        ).isoformat().replace("+00:00", "Z")

    def _tenant_api_datetime(self, value: Optional[str] = None) -> str:
        parsed = self._parse_timestamp(value) if value else None
        if parsed is None:
            parsed = datetime.now(timezone.utc)
        return parsed.isoformat().replace("+00:00", "Z")

    def _tenant_api_employee_role(
        self,
        *,
        assign_start_date: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        # Do NOT fall back to today when assign_start_date is None.
        # A None here means the employee is missing a hire date; we let
        # _validate_tenant_employee_dto surface that with a clear message.
        start_value = self._tenant_api_datetime(assign_start_date) if assign_start_date else None
        return {
            "roles": "General Employee",
            "createdBy": created_by or "",
            "isPrimary": True,
            "roleOrder": 1,
            "assignStartDate": start_value,
        }

    def _build_tenant_employee_payload_from_qbo(
        self,
        qbo_employee: Dict[str, Any],
        *,
        service_id: Any = None,
        existing_employee: Optional[Dict[str, Any]] = None,
        days_after_hire: int = 0,
        employee_type: str = "W2",
        cost_sync_enabled: bool = True,
        cost_qbo_field: str = const.EMPLOYEE_COST_QBO_FIELD_COST_RATE,
    ) -> Dict[str, Any]:
        """Build the Tenant API DTO for a new employee.

        ``assignStartDate`` is computed as ``HiredDate + days_after_hire``.
        We intentionally do **not** fall back to today's date — a missing hire
        date must surface as an IMPORT_FAILED record, not a silent wrong date.

        ``employee_type`` comes from the tenant's employee sync config.
        QBO Employee objects represent W-2 workers; 1099 contractors live in QBO
        as Vendors.  The default is therefore "W2", but admins can override.
        """
        payload = self._build_employee_payload_from_qbo(
            qbo_employee,
            employee_type=employee_type,
            cost_sync_enabled=cost_sync_enabled,
            cost_qbo_field=cost_qbo_field,
        )
        existing_employee = existing_employee or {}

        # Compute work-start date: HiredDate + days_after_hire days.
        # Never manufacture a date — let _validate_tenant_employee_dto / the
        # _is_payload_valid_for_tenant_api (hire date check) surface the gap cleanly.
        hired_date_str: Optional[str] = payload.get("hiredDate")
        assign_start_date = self._work_start_date_from_hire(
            hired_date_str,
            days_after_hire=days_after_hire,
        )

        return {
            "id": existing_employee.get("id"),
            "objectId": existing_employee.get("object_id"),
            const.KEY_EMAIL: payload.get(const.KEY_EMAIL),
            "mobileNo": payload.get("mobileNo"),
            "alternateMobileNo": existing_employee.get("alternate_mobile_no"),
            "firstname": payload.get("firstname") or "Employee",
            "lastname": payload.get("lastname") or None,
            "middlename": payload.get("middlename") or None,
            "birthdate": payload.get("birthdate"),
            "gender": existing_employee.get("gender"),
            "avatar": existing_employee.get("avatar"),
            "roles": [
                self._tenant_api_employee_role(
                    assign_start_date=assign_start_date,
                    created_by=(payload.get("firstname") or ""),
                )
            ],
            const.KEY_STATUS: existing_employee.get("status") or "Invited",
            "count": None,
            "active": bool(existing_employee.get("active")) if existing_employee.get("id") else False,
            "eulaAccepted": bool(existing_employee.get("eula_accepted")) if existing_employee.get("id") else False,
            "deleted": False,
            "hiredDate": hired_date_str,
            "updatedAt": None,
            "displayName": None,
            "workStartDate": assign_start_date,
            "type": employee_type,
            "serviceId": int(service_id) if service_id is not None else None,
        }

    def _is_payload_valid_for_tenant_api(self, payload: Dict[str, Any]) -> Tuple[bool, str]:
        missing: List[str] = []
        if not self._clean(payload.get("firstname")):
            missing.append("first name")
        if not self._clean(payload.get(const.KEY_EMAIL)):
            missing.append("email")
        if not self._clean(payload.get("mobileNo") or payload.get(const.KEY_PHONE)):
            missing.append("phone")
        if not self._clean(payload.get("hiredDate")):
            # hire date is required to compute assignStartDate; catch it here so
            # the employee gets a proper IMPORT_FAILED mapping (retryable) rather
            # than an unhandled exception deep in the DTO builder.
            missing.append("hire date (required to calculate work start date — add it in QuickBooks and re-sync)")
        if missing:
            return False, f"missing required fields for tenant employee create: {', '.join(missing)}"
        return True, ""

    def _resolve_changed_since(
        self,
        *,
        tenant_id: str,
        service_id: str,
        changed_since_override: Optional[Any] = None,
        force_full: bool = False,
    ) -> Tuple[Optional[datetime], Optional[str]]:
        if force_full:
            return None, "forceFull"
        if changed_since_override is not None:
            parsed = self._parse_timestamp(changed_since_override)
            if parsed is not None:
                return self._normalize_changed_since(parsed), "override"
        try:
            last_sync_start = self.scheduled_job_repo.get_last_successful_module_sync_start(
                tenant_id=tenant_id,
                job_type="QBO_EMPLOYEE_SYNC",
                service_id=str(service_id),
            )
            log_cdc_debug(logger,
                "employee scheduled-job watermark lookup",
                tenantId=tenant_id,
                serviceId=service_id,
                rawValue=last_sync_start,
            )
            changed_since = self._normalize_changed_since(last_sync_start)
            if changed_since is not None:
                return changed_since, "scheduledJob"
        except Exception as exc:
            log_cdc_debug(logger,
                "employee scheduled-job watermark lookup failed",
                tenantId=tenant_id,
                serviceId=service_id,
                error=str(exc),
            )

        try:
            additional_info = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            raw_last_employee_sync_at = additional_info.get("lastEmployeeSyncAt")
            log_cdc_debug(logger,
                "employee manual watermark lookup",
                tenantId=tenant_id,
                serviceId=service_id,
                rawValue=raw_last_employee_sync_at,
            )
            changed_since = self._normalize_changed_since(
                self._parse_timestamp(raw_last_employee_sync_at)
            )
            if changed_since is not None:
                return changed_since, "lastEmployeeSyncAt"
        except Exception as exc:
            log_cdc_debug(logger,
                "employee manual watermark lookup failed",
                tenantId=tenant_id,
                serviceId=service_id,
                error=str(exc),
            )

        return None, None

    def _fetch_qbo_employees_cdc(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        changed_since: datetime,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        changed_since = self._ensure_aware(changed_since)
        changed_since_value = changed_since.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        url = f"{base_url}/v3/company/{realm_id}/cdc"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "text/plain",
        }
        params = {"entities": "Employee", "changedSince": changed_since_value, "minorversion": self.QBO_EMPLOYEE_MINOR_VERSION}
        log_cdc_debug(logger,
            "QBO Employee CDC request",
            url=url,
            params=params,
        )
        resp = qbo_err.qbo_get(url, headers=headers, params=params, timeout=60)
        qbo_err.raise_for_qbo_response(resp, fallback_message="QBO employee CDC failed")
        data = qbo_err.safe_json(resp)
        employees_by_id: Dict[str, Dict[str, Any]] = {}
        for cdc_block in data.get("CDCResponse") or []:
            for query_response in cdc_block.get("QueryResponse") or []:
                for employee in query_response.get("Employee") or []:
                    employee_id = self._clean(employee.get(const.QBO_KEY_ID))
                    if employee_id:
                        employees_by_id[employee_id] = employee
        employees = list(employees_by_id.values())
        log_cdc_debug(logger,
            "QBO Employee CDC parsed",
            count=len(employees),
            ids=list(employees_by_id.keys()),
            intuitTid=qbo_api.extract_intuit_tid(resp),
        )
        return employees, qbo_api.extract_intuit_tid(resp)

    # ------------------------------------------------------------------
    # 1099 contractors: imported from QBO Vendors (Vendor1099 = true) as FIELDCRAFT
    # 1099 employees. QBO Vendors are NOT bridged the way Employees are, and they
    # have no HiredDate, so employee import reads Vendor1099 records too. CDC mode
    # reads only changed vendors; full mode scans all vendors. The start date is
    # derived from the vendor's QBO create date. This never touches the normal
    # vendor-sync CDC watermark.
    # ------------------------------------------------------------------

    @staticmethod
    def _is_1099_vendor(qbo_vendor: Dict[str, Any]) -> bool:
        return qbo_vendor.get("Vendor1099") is True

    def _query_qbo_vendors(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        start_position: int,
        max_results: int,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        query_url = f"{base_url}/v3/company/{realm_id}/query"
        query = f"select * from Vendor STARTPOSITION {int(start_position)} MAXRESULTS {int(max_results)}"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        resp = qbo_err.qbo_get(query_url, headers=headers, params={"query": query}, timeout=60)
        qbo_err.raise_for_qbo_response(resp, fallback_message="QBO vendor query failed")
        data = qbo_err.safe_json(resp)
        vendors = (data.get("QueryResponse") or {}).get("Vendor") or []
        return (vendors if isinstance(vendors, list) else [], qbo_api.extract_intuit_tid(resp))

    def _fetch_qbo_vendors_cdc(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        changed_since: datetime,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        changed_since = self._ensure_aware(changed_since)
        changed_since_value = changed_since.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        url = f"{base_url}/v3/company/{realm_id}/cdc"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "text/plain",
        }
        params = {"entities": "Vendor", "changedSince": changed_since_value, "minorversion": self.QBO_EMPLOYEE_MINOR_VERSION}
        log_cdc_debug(logger,
            "QBO Vendor CDC request for employee 1099",
            url=url,
            params=params,
        )
        resp = qbo_err.qbo_get(url, headers=headers, params=params, timeout=60)
        qbo_err.raise_for_qbo_response(resp, fallback_message="QBO 1099 vendor CDC failed")
        data = qbo_err.safe_json(resp)
        vendors_by_id: Dict[str, Dict[str, Any]] = {}
        for cdc_block in data.get("CDCResponse") or []:
            for query_response in cdc_block.get("QueryResponse") or []:
                for vendor in query_response.get("Vendor") or []:
                    vendor_id = self._clean(vendor.get(const.QBO_KEY_ID))
                    if vendor_id:
                        vendors_by_id[vendor_id] = vendor
        vendors = list(vendors_by_id.values())
        log_cdc_debug(logger,
            "QBO Vendor CDC parsed for employee 1099",
            count=len(vendors),
            vendor1099Count=sum(1 for vendor in vendors if self._is_1099_vendor(vendor)),
            normalVendorCount=sum(1 for vendor in vendors if not self._is_1099_vendor(vendor)),
            ids=list(vendors_by_id.keys()),
            vendor1099Ids=[self._clean(vendor.get(const.QBO_KEY_ID)) for vendor in vendors if self._is_1099_vendor(vendor)],
            intuitTid=qbo_api.extract_intuit_tid(resp),
        )
        return vendors, qbo_api.extract_intuit_tid(resp)

    def _normalize_1099_vendor_as_employee(self, qbo_vendor: Dict[str, Any]) -> Dict[str, Any]:
        """Shape a QBO Vendor so the employee DTO/resource builders can consume it.

        Vendor and Employee share the fields the builders read (GivenName/FamilyName/
        MiddleName/DisplayName/PrimaryEmailAddr/PrimaryPhone/Mobile/BillAddr/Active). The
        only gap is HiredDate — vendors have none — so we derive a work start baseline
        from the vendor's QBO create date (MetaData.CreateTime), falling back to today.

        Older FIELDCRAFT-created 1099 vendors may only have DisplayName/CompanyName/PrintOnCheckName.
        DisplayName can include the phone suffix used for QuickBooks uniqueness, so never
        split that value into first/last name unless there is no cleaner name source.
        """
        normalized = dict(qbo_vendor)

        if not self._clean(normalized.get("GivenName")) and not self._clean(normalized.get("FamilyName")):
            clean_name = self._vendor_person_name_without_phone_suffix(qbo_vendor)
            first_name, last_name = self._split_name(clean_name)
            normalized["GivenName"] = first_name
            normalized["FamilyName"] = last_name

        if not normalized.get("HiredDate"):
            create_time = self._clean((qbo_vendor.get("MetaData") or {}).get("CreateTime"))
            normalized["HiredDate"] = create_time[:10] if create_time else datetime.now(timezone.utc).date().isoformat()
        return normalized

    def _vendor_person_name_without_phone_suffix(self, qbo_vendor: Dict[str, Any]) -> str:
        for key in ("PrintOnCheckName", "CompanyName"):
            value = self._clean(qbo_vendor.get(key))
            if value:
                return value
        display_name = self._clean(qbo_vendor.get(const.QBO_KEY_DISPLAY_NAME))
        without_suffix = re.sub(r"-\d{6,20}$", "", display_name).strip()
        return without_suffix or display_name or "Employee"

    def _import_1099_vendors_as_employees(
        self,
        *,
        summary: Dict[str, Any],
        tenant_id: str,
        service_id: str,
        realm_id: str,
        access_token: str,
        base_url: str,
        tenant_api_token: str,
        days_after_hire: int,
        cost_sync_enabled: bool,
        dry_run: bool = False,
        page_size: int = 100,
        changed_since: Optional[datetime] = None,
    ) -> None:
        """Best-effort: import QBO Vendor1099=true records as FIELDCRAFT 1099 employees.

        CDC mode only processes changed vendors. Full mode scans all vendors. Never
        raises — a contractor-side failure must not fail W2 employee import.
        """
        try:
            linked_worker_vendor_ids = self.employee_repo.get_current_worker_external_vendor_ids(
                tenant_id=tenant_id,
                service_id=service_id,
            )

            def should_process(vendor: Dict[str, Any]) -> bool:
                vendor_id = self._clean(vendor.get(const.QBO_KEY_ID))
                return self._is_1099_vendor(vendor) or vendor_id in linked_worker_vendor_ids

            if changed_since is not None:
                vendors, tid = self._fetch_qbo_vendors_cdc(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=realm_id,
                    changed_since=changed_since,
                )
                if tid and not summary.get("intuitTid"):
                    summary["intuitTid"] = tid
                log_cdc_debug(logger,
                    "employee 1099 vendor CDC filter",
                    totalVendors=len(vendors),
                    vendor1099Count=sum(1 for vendor in vendors if self._is_1099_vendor(vendor)),
                    skippedNormalVendorCount=sum(1 for vendor in vendors if not self._is_1099_vendor(vendor)),
                    vendor1099Ids=[self._clean(vendor.get(const.QBO_KEY_ID)) for vendor in vendors if self._is_1099_vendor(vendor)],
                )
                for qbo_vendor in vendors:
                    if not should_process(qbo_vendor):
                        continue
                    summary["processed"] += 1
                    self._process_1099_vendor_as_employee(
                        summary=summary,
                        tenant_id=tenant_id,
                        service_id=service_id,
                        realm_id=realm_id,
                        access_token=access_token,
                        base_url=base_url,
                        qbo_vendor=qbo_vendor,
                        dry_run=dry_run,
                        tenant_api_token=tenant_api_token,
                        days_after_hire=days_after_hire,
                        cost_sync_enabled=cost_sync_enabled,
                    )
                return

            pos = 1
            while True:
                vendors, _tid = self._query_qbo_vendors(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=realm_id,
                    start_position=pos,
                    max_results=int(page_size),
                )
                if not vendors:
                    break
                log_cdc_debug(logger,
                    "employee 1099 vendor FULL page fetched",
                    startPosition=pos,
                    maxResults=int(page_size),
                    totalVendors=len(vendors),
                    vendor1099Count=sum(1 for vendor in vendors if self._is_1099_vendor(vendor)),
                    vendor1099Ids=[self._clean(vendor.get(const.QBO_KEY_ID)) for vendor in vendors if self._is_1099_vendor(vendor)],
                )
                for qbo_vendor in vendors:
                    if not should_process(qbo_vendor):
                        continue
                    summary["processed"] += 1
                    self._process_1099_vendor_as_employee(
                        summary=summary,
                        tenant_id=tenant_id,
                        service_id=service_id,
                        realm_id=realm_id,
                        access_token=access_token,
                        base_url=base_url,
                        qbo_vendor=qbo_vendor,
                        dry_run=dry_run,
                        tenant_api_token=tenant_api_token,
                        days_after_hire=days_after_hire,
                        cost_sync_enabled=cost_sync_enabled,
                    )
                if len(vendors) < int(page_size):
                    break
                pos += len(vendors)
        except Exception:
            logger.exception("[EmployeeImport] 1099-vendor import phase failed (non-fatal)")

    def _process_1099_vendor_as_employee(
        self,
        *,
        summary: Dict[str, Any],
        tenant_id: str,
        service_id: str,
        realm_id: str,
        access_token: str,
        base_url: str,
        qbo_vendor: Dict[str, Any],
        dry_run: bool,
        tenant_api_token: str,
        days_after_hire: int,
        cost_sync_enabled: bool,
    ) -> None:
        qbo_vendor_id = self._clean(qbo_vendor.get(const.QBO_KEY_ID))
        sync_token = self._clean(qbo_vendor.get(const.QBO_KEY_SYNC_TOKEN)) or None
        display_name = self._clean(qbo_vendor.get(const.QBO_KEY_DISPLAY_NAME))
        detail: Dict[str, Any] = {
            "qboVendorId": qbo_vendor_id,
            const.KEY_DISPLAY_NAME: display_name,
            const.KEY_STATUS: None,
            const.KEY_ACTION: None,
            const.KEY_FIELDCRAFT_EMPLOYEE_ID: None,
        }
        try:
            if not qbo_vendor_id:
                return
            existing = self.employee_repo.get_by_external_vendor_id(tenant_id, service_id, qbo_vendor_id)

            # Vendor CDC/list rows are not guaranteed to include the identity
            # fields used by validation. Always read the full Vendor object for
            # a linked 1099 worker, and for any Vendor currently marked 1099.
            qbo_vendor = self._enrich_qbo_vendor_for_cost_if_needed(
                qbo_vendor,
                access_token=access_token,
                base_url=base_url,
                realm_id=realm_id,
                cost_sync_enabled=cost_sync_enabled,
                force_detail=bool(existing) or self._is_1099_vendor(qbo_vendor),
            )
            sync_token = self._clean(qbo_vendor.get(const.QBO_KEY_SYNC_TOKEN)) or sync_token
            display_name = self._clean(qbo_vendor.get(const.QBO_KEY_DISPLAY_NAME)) or display_name

            if not self._is_1099_vendor(qbo_vendor):
                if existing and existing.get("id"):
                    reason_text, reason = self._1099_tracking_removed_reason(
                        qbo_vendor_id=qbo_vendor_id,
                        display_name=display_name,
                    )
                    self.employee_repo.mark_mapping_failed(
                        tenant_id=tenant_id,
                        mapping_id=int(existing["id"]),
                        status=self.employee_repo.STATUS_SYNC_FAILED,
                        reason=reason,
                        external_display_name=display_name,
                    )
                    summary["failed"] += 1
                    qbo_api.append_failure(summary, {
                        "qboVendorId": qbo_vendor_id,
                        const.KEY_FIELDCRAFT_EMPLOYEE_ID: existing.get("user_account_id"),
                        const.KEY_DISPLAY_NAME: display_name,
                        const.KEY_ERROR: reason_text,
                        const.KEY_DETAILS: reason,
                    })
                    detail.update({
                        const.KEY_STATUS: "failed",
                        const.KEY_ACTION: "contractor1099TrackingRemoved",
                        const.KEY_FIELDCRAFT_EMPLOYEE_ID: existing.get("user_account_id"),
                        const.KEY_ERROR: reason_text,
                        const.KEY_REASON: reason,
                    })
                    summary[const.KEY_RESULTS].append(detail)
                return

            if qbo_vendor.get("Active") is False:
                if existing and existing.get("id"):
                    reason = self._build_reason(
                        "This 1099 contractor is inactive in QuickBooks. Make the vendor active in QuickBooks, then run employee sync again.",
                        code="CONTRACTOR_1099_INACTIVE_IN_QBO",
                        qbo_vendor_id=qbo_vendor_id,
                        display_name=display_name or None,
                        action_required="Make the vendor active in QuickBooks, then run employee sync again.",
                    )
                    self.employee_repo.mark_mapping_failed(
                        tenant_id=tenant_id,
                        mapping_id=int(existing["id"]),
                        status=self.employee_repo.STATUS_SYNC_FAILED,
                        reason=reason,
                        external_display_name=display_name,
                    )
                    summary["failed"] += 1
                    qbo_api.append_failure(summary, {
                        "qboVendorId": qbo_vendor_id,
                        const.KEY_FIELDCRAFT_EMPLOYEE_ID: existing.get("user_account_id"),
                        const.KEY_DISPLAY_NAME: display_name,
                        const.KEY_ERROR: "QuickBooks 1099 vendor is inactive",
                        const.KEY_DETAILS: reason,
                    })
                    detail.update({
                        const.KEY_STATUS: "failed",
                        const.KEY_ACTION: "qbo1099VendorInactive",
                        const.KEY_FIELDCRAFT_EMPLOYEE_ID: existing.get("user_account_id"),
                        const.KEY_ERROR: "QuickBooks 1099 vendor is inactive",
                        const.KEY_REASON: reason,
                    })
                    summary[const.KEY_RESULTS].append(detail)
                    return
                summary["skippedDeleted"] += 1
                detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "inactiveInQbo"})
                summary[const.KEY_RESULTS].append(detail)
                return

            normalized = self._normalize_1099_vendor_as_employee(qbo_vendor)
            payload = self._build_employee_payload_from_qbo(
                normalized,
                employee_type=const.EMPLOYEE_TYPE_1099,
                cost_sync_enabled=cost_sync_enabled,
                cost_qbo_field=const.EMPLOYEE_COST_QBO_FIELD_BILL_RATE,
            )
            email = self._clean(payload.get(const.KEY_EMAIL))

            if existing and existing.get("user_account_id"):
                self._update_existing_1099_vendor_worker(
                    summary=summary,
                    detail=detail,
                    tenant_id=tenant_id,
                    realm_id=realm_id,
                    qbo_vendor=qbo_vendor,
                    qbo_vendor_id=qbo_vendor_id,
                    display_name=display_name,
                    sync_token=sync_token,
                    payload=payload,
                    mapping=existing,
                    dry_run=dry_run,
                )
                return

            vendor_mapping = self.vendor_map_repo.get_by_external_id(
                tenant_id=tenant_id,
                service_id=service_id,
                external_id=qbo_vendor_id,
            )
            if vendor_mapping and vendor_mapping.get(const.KEY_VENDOR_ID):
                reason_text = (
                    f"1099 tracking was enabled for QuickBooks vendor {display_name or qbo_vendor_id}, "
                    "but this record is already synced as a normal FIELDCRAFT vendor. A synced vendor cannot "
                    "be converted into a FIELDCRAFT 1099 employee because that would create a duplicate record. "
                    "Turn off 1099 tracking in QuickBooks, then run employee sync again."
                )
                reason = self._build_reason(
                    reason_text,
                    code=self.REASON_VENDOR_1099_ENABLED_AFTER_SYNC,
                    qbo_vendor_id=qbo_vendor_id,
                    fieldcraft_vendor_id=vendor_mapping.get(const.KEY_VENDOR_ID),
                    action_required=(
                        "Turn off 1099 tracking for this QuickBooks vendor. If the record must become "
                        "a 1099 employee, unlink and correct the existing vendor record first."
                    ),
                )
                if vendor_mapping.get("id"):
                    self.vendor_map_repo.mark_mapping_failed(
                        tenant_id=tenant_id,
                        mapping_id=int(vendor_mapping["id"]),
                        status=self.employee_repo.STATUS_SYNC_FAILED,
                        reason=reason,
                    )
                summary["failed"] += 1
                qbo_api.append_failure(summary, {
                    "qboVendorId": qbo_vendor_id,
                    const.KEY_DISPLAY_NAME: display_name,
                    const.KEY_ERROR: reason_text,
                    const.KEY_DETAILS: reason,
                })
                detail.update({
                    const.KEY_STATUS: "failed",
                    const.KEY_ACTION: "vendor1099ConversionBlocked",
                    const.KEY_ERROR: reason_text,
                    const.KEY_REASON: reason,
                })
                summary[const.KEY_RESULTS].append(detail)
                return

            if dry_run:
                summary["skippedAlreadyCurrent"] += 1
                detail.update({const.KEY_STATUS: "dry_run", "payload": payload})
                summary[const.KEY_RESULTS].append(detail)
                return

            # De-dup by email: if this person is already a FIELDCRAFT worker, link the vendor
            # mapping to them rather than creating a duplicate employee.
            existing_owner_id = (
                self.employee_repo.get_employee_id_by_email(
                    tenant_id=tenant_id,
                    email=email,
                )
                if email
                else None
            )
            if not existing_owner_id and email and self._user_email_exists_in_fieldcraft_or_ad(
                tenant_id=tenant_id,
                tenant_api_token=tenant_api_token,
                email=email,
            ):
                reason_text = (
                    "This employee could not be synced, there is an issue with the employee account. "
                    "Please contact FIELDCRAFT Support for assistance."
                )
                reason = self._build_reason(
                    reason_text,
                    code=self.REASON_DUPLICATE_EMAIL_NOT_ALLOWED,
                    email=email,
                    qbo_vendor_id=qbo_vendor_id,
                    display_name=display_name or None,
                    action_required="Please contact FIELDCRAFT Support for assistance.",
                )
                summary["failed"] += 1
                qbo_api.append_failure(summary, {
                    "qboVendorId": qbo_vendor_id,
                    const.KEY_DISPLAY_NAME: display_name,
                    const.KEY_ERROR: reason_text,
                    const.KEY_DETAILS: reason,
                })
                detail.update({
                    const.KEY_STATUS: "failed",
                    const.KEY_ACTION: "duplicateEmailImportFailed",
                    const.KEY_ERROR: reason_text,
                    const.KEY_REASON: reason,
                })
                summary[const.KEY_RESULTS].append(detail)
                return

            target_employee: Dict[str, Any]
            if existing_owner_id:
                user_account_id = int(existing_owner_id)
                target_employee = self.employee_repo.get_employee_by_id(tenant_id, user_account_id) or {}
                self._validate_existing_1099_owner_for_vendor_link(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    realm_id=realm_id,
                    user_account_id=user_account_id,
                    qbo_vendor_id=qbo_vendor_id,
                    employee=target_employee,
                )
                action = "linked existing FIELDCRAFT 1099 worker"
            else:
                user_account_id = int(self._save_employee_through_tenant_api(
                    tenant_api_token=tenant_api_token,
                    qbo_employee=normalized,
                    service_id=service_id,
                    existing_employee=None,
                    days_after_hire=days_after_hire,
                    employee_type=const.EMPLOYEE_TYPE_1099,
                    cost_sync_enabled=cost_sync_enabled,
                    cost_qbo_field=const.EMPLOYEE_COST_QBO_FIELD_BILL_RATE,
                ))
                target_employee = self.employee_repo.get_employee_by_id(tenant_id, user_account_id) or {"id": user_account_id}
                action = "created in FIELDCRAFT"

            reason = self._build_reason(
                "1099 contractor imported from a QuickBooks Vendor and linked as a FIELDCRAFT 1099 employee.",
                code="CONTRACTOR_1099_IMPORTED_FROM_VENDOR",
            )
            try:
                self.employee_repo.create_worker_vendor_mapping(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    user_account_id=user_account_id,
                    external_vendor_id=qbo_vendor_id,
                    external_account_id=realm_id,
                    sync_token=sync_token,
                    reason=reason,
                    external_display_name=display_name,
                )
            except Exception as exc:
                quarantine_reason = self._build_reason(
                    "The FIELDCRAFT 1099 worker could not be linked to the QuickBooks Vendor. "
                    "The worker was held for import retry to prevent a duplicate Vendor.",
                    code="CONTRACTOR_MAPPING_CLAIM_FAILED",
                    qbo_vendor_id=qbo_vendor_id,
                    error=str(exc),
                )
                self._quarantine_unbound_import_mapping(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    user_account_id=user_account_id,
                    expected_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
                    reason=quarantine_reason,
                )
                raise
            self._enrich_imported_worker_best_effort(
                tenant_id=tenant_id,
                employee=target_employee,
                payload=payload,
                detail=detail,
            )
            summary["created in FIELDCRAFT"] += 1
            detail.update({const.KEY_STATUS: "success", const.KEY_ACTION: action, const.KEY_FIELDCRAFT_EMPLOYEE_ID: user_account_id, const.KEY_REASON: reason})
            summary[const.KEY_RESULTS].append(detail)
        except Exception as exc:
            logger.warning("[EmployeeImport] 1099-vendor import failed | vendorId=%s error=%s", qbo_vendor_id, exc)
            summary["failed"] += 1
            qbo_api.append_failure(summary, {
                "qboVendorId": qbo_vendor_id,
                const.KEY_DISPLAY_NAME: display_name,
                const.KEY_ERROR: str(exc),
            })
            detail.update({const.KEY_STATUS: "failed", const.KEY_ERROR: str(exc)})
            summary[const.KEY_RESULTS].append(detail)

    def _validate_existing_1099_owner_for_vendor_link(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        realm_id: str,
        user_account_id: int,
        qbo_vendor_id: str,
        employee: Dict[str, Any],
    ) -> None:
        resource_type = " ".join(str(((employee or {}).get("resource") or {}).get("type") or "").split()).casefold()
        allowed_resource_types = {
            " ".join(str(const.EMPLOYEE_TYPE_1099).split()).casefold(),
            " ".join(str(getattr(const, "EMPLOYEE_TYPE_SUBCONTRACTOR", "Subcontractor")).split()).casefold(),
            " ".join(str(getattr(const, "EMPLOYEE_TYPE_CONTRACTOR", "Contractor")).split()).casefold(),
        }
        if resource_type not in allowed_resource_types:
            raise ValueError(
                "A QuickBooks 1099 Vendor has the same email as an existing W-2 FIELDCRAFT employee. "
                "The records were not linked because W-2 employees and 1099 contractors must keep separate worker mappings."
            )

        mapping = self.employee_repo.get_current_mapping_by_user_account_id(
            tenant_id=tenant_id,
            service_id=service_id,
            user_account_id=int(user_account_id),
        )
        if not isinstance(mapping, dict):
            return

        mapped_realm = str(mapping.get("external_account_id") or "").strip()
        if mapped_realm and mapped_realm != str(realm_id or "").strip():
            raise ValueError(
                "This FIELDCRAFT 1099 worker is mapped to a different QuickBooks company. Reconnect or correct the mapping before syncing."
            )

        entity_type = str(mapping.get("external_entity_type") or "").strip().upper()
        external_employee_id = str(mapping.get("external_employee_id") or "").strip()
        external_vendor_id = str(mapping.get("external_vendor_id") or "").strip()
        if entity_type == const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE or external_employee_id:
            raise ValueError(
                "This FIELDCRAFT worker is already linked as a QuickBooks W-2 Employee. "
                "It cannot also be linked to a QuickBooks 1099 Vendor."
            )
        if external_vendor_id and external_vendor_id != str(qbo_vendor_id or "").strip():
            raise ValueError(
                "This FIELDCRAFT 1099 worker is already linked to a different QuickBooks Vendor. Review the existing mapping before syncing."
            )

        status = str(mapping.get("sync_status") or "").strip().upper()
        claimable_statuses = {
            self.employee_repo.STATUS_PENDING_PUSH,
            self.employee_repo.STATUS_FAILED,
            self.employee_repo.STATUS_SYNC_FAILED,
            self.employee_repo.STATUS_IMPORT_FAILED,
        }
        if external_vendor_id != str(qbo_vendor_id or "").strip() and status not in claimable_statuses:
            raise ValueError(
                "This FIELDCRAFT 1099 worker already has a current QuickBooks mapping that cannot be replaced automatically."
            )

    def _update_existing_1099_vendor_worker(
        self,
        *,
        summary: Dict[str, Any],
        detail: Dict[str, Any],
        tenant_id: str,
        realm_id: str,
        qbo_vendor: Dict[str, Any],
        qbo_vendor_id: str,
        display_name: str,
        sync_token: Optional[str],
        payload: Dict[str, Any],
        mapping: Dict[str, Any],
        dry_run: bool,
    ) -> None:
        user_account_id = int(mapping["user_account_id"])
        current_employee = self.employee_repo.get_employee_by_id(tenant_id, user_account_id)
        if not current_employee:
            raise Exception(
                f"Mapped FIELDCRAFT 1099 employee was not found for QuickBooks Vendor {display_name or qbo_vendor_id}."
            )

        if self._worker_entity_type_from_employee(current_employee) != const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR:
            reason_text, reason = self._classification_change_reason(
                employee=current_employee,
                expected_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
                external_id=qbo_vendor_id,
                display_name=display_name,
            )
            self.employee_repo.mark_mapping_failed(
                tenant_id=tenant_id,
                mapping_id=int(mapping["id"]),
                status=self.employee_repo.STATUS_SYNC_FAILED,
                reason=reason,
                external_display_name=display_name,
            )
            summary["failed"] += 1
            qbo_api.append_failure(summary, {
                "qboVendorId": qbo_vendor_id,
                const.KEY_FIELDCRAFT_EMPLOYEE_ID: user_account_id,
                const.KEY_DISPLAY_NAME: display_name,
                const.KEY_ERROR: reason_text,
                const.KEY_DETAILS: reason,
            })
            detail.update({
                const.KEY_STATUS: "failed",
                const.KEY_ACTION: "workerTypeChangeBlocked",
                const.KEY_FIELDCRAFT_EMPLOYEE_ID: user_account_id,
                const.KEY_ERROR: reason_text,
                const.KEY_REASON: reason,
            })
            summary[const.KEY_RESULTS].append(detail)
            return

        if self._employee_email_changed(current_employee, payload):
            reason_text, reason = self._1099_email_change_reason(
                current_employee=current_employee,
                qbo_vendor_id=qbo_vendor_id,
                qbo_email=payload.get(const.KEY_EMAIL) or "",
                display_name=display_name,
            )
            self.employee_repo.mark_mapping_failed(
                tenant_id=tenant_id,
                mapping_id=int(mapping["id"]),
                status=self.employee_repo.STATUS_SYNC_FAILED,
                reason=reason,
                external_display_name=display_name,
            )
            summary["failed"] += 1
            qbo_api.append_failure(summary, {
                "qboVendorId": qbo_vendor_id,
                const.KEY_FIELDCRAFT_EMPLOYEE_ID: user_account_id,
                const.KEY_DISPLAY_NAME: display_name,
                const.KEY_ERROR: reason_text,
                const.KEY_DETAILS: reason,
            })
            detail.update({
                const.KEY_STATUS: "failed",
                const.KEY_ACTION: "contractorEmailChangeBlocked",
                const.KEY_FIELDCRAFT_EMPLOYEE_ID: user_account_id,
                const.KEY_ERROR: reason_text,
                const.KEY_REASON: reason,
            })
            summary[const.KEY_RESULTS].append(detail)
            return

        if not self.employee_repo.sync_projection_changed(current_employee, payload):
            reason = self._build_reason(
                "1099 contractor is already up to date in FIELDCRAFT and QuickBooks.",
                code="CONTRACTOR_1099_ALREADY_CURRENT",
            )
            self.employee_repo.mark_mapping_synced(
                tenant_id=tenant_id,
                mapping_id=int(mapping["id"]),
                external_vendor_id=qbo_vendor_id,
                external_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
                external_account_id=realm_id,
                sync_token=sync_token,
                reason=reason,
                external_display_name=display_name,
            )
            summary["skippedAlreadyCurrent"] += 1
            detail.update({
                const.KEY_STATUS: "skipped",
                const.KEY_ACTION: "alreadyCurrent",
                const.KEY_FIELDCRAFT_EMPLOYEE_ID: user_account_id,
                const.KEY_REASON: reason,
            })
            summary[const.KEY_RESULTS].append(detail)
            return

        qbo_last_updated_at = self._get_qbo_last_updated_at(qbo_vendor)
        local_latest_at = self.employee_repo.get_employee_latest_local_timestamp(current_employee)
        if self._should_skip_import_update(qbo_last_updated_at, local_latest_at):
            reason = self._build_reason(
                "Your FIELDCRAFT 1099 employee is more recent than QuickBooks. No changes were imported to protect your latest edits.",
                code="CONTRACTOR_1099_FIELDCRAFT_NEWER",
                qbo_updated_at=qbo_last_updated_at.isoformat() if qbo_last_updated_at else None,
                fieldcraft_updated_at=local_latest_at.isoformat() if local_latest_at else None,
            )
            summary["skippedLocalNewer"] += 1
            detail.update({
                const.KEY_STATUS: "skipped",
                const.KEY_ACTION: "localNewer",
                const.KEY_FIELDCRAFT_EMPLOYEE_ID: user_account_id,
                const.KEY_REASON: reason,
            })
            summary[const.KEY_RESULTS].append(detail)
            return

        if dry_run:
            reason = self._build_reason(
                "Preview only — no 1099 employee changes have been saved.",
                code="CONTRACTOR_1099_DRY_RUN",
            )
            detail.update({
                const.KEY_STATUS: "skipped",
                const.KEY_ACTION: "dryRun",
                const.KEY_FIELDCRAFT_EMPLOYEE_ID: user_account_id,
                const.KEY_REASON: reason,
            })
            summary[const.KEY_RESULTS].append(detail)
            return

        updated_employee = self.employee_repo.update_employee_from_qbo_payload(
            tenant_id=tenant_id,
            user_account_id=user_account_id,
            payload=payload,
        )
        reason = self._build_reason(
            "1099 contractor details and billing rate updated in FIELDCRAFT from QuickBooks.",
            code="CONTRACTOR_1099_UPDATED_IN_FIELDCRAFT",
            qbo_bill_rate=(payload.get("resource") or {}).get("qbo_bill_rate"),
        )
        self.employee_repo.mark_mapping_synced(
            tenant_id=tenant_id,
            mapping_id=int(mapping["id"]),
            external_vendor_id=qbo_vendor_id,
            external_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
            external_account_id=realm_id,
            sync_token=sync_token,
            reason=reason,
            external_display_name=display_name,
        )
        summary["updatedInFIELDCRAFT"] += 1
        detail.update({
            const.KEY_STATUS: "success",
            const.KEY_ACTION: "updated in FIELDCRAFT",
            const.KEY_FIELDCRAFT_EMPLOYEE_ID: int(updated_employee.get("id") or user_account_id),
            const.KEY_REASON: reason,
        })
        summary[const.KEY_RESULTS].append(detail)

    def _fetch_qbo_vendor_by_id(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        vendor_id: str,
    ) -> Dict[str, Any]:
        url = f"{base_url}/v3/company/{realm_id}/vendor/{vendor_id}"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        resp = qbo_err.qbo_get(
            url,
            headers=headers,
            params={"minorversion": self.QBO_EMPLOYEE_MINOR_VERSION},
            timeout=60,
        )
        qbo_err.raise_for_qbo_response(resp, fallback_message="QBO get 1099 vendor failed")
        data = qbo_err.safe_json(resp)
        return data.get("Vendor") or {}

    def _enrich_qbo_vendor_for_cost_if_needed(
        self,
        qbo_vendor: Dict[str, Any],
        *,
        access_token: Optional[str],
        base_url: Optional[str],
        realm_id: str,
        cost_sync_enabled: bool = True,
        force_detail: bool = False,
    ) -> Dict[str, Any]:
        """Read the full Vendor object when a list row omits BillRate.

        QuickBooks exposes a 1099 vendor's hourly billing rate as Vendor.BillRate.
        Some list responses can omit optional rate fields, so the mapped worker must
        be enriched by ID before the FIELDCRAFT resource cost is compared or updated.
        """
        if not force_detail and (
            not cost_sync_enabled or self._has_qbo_bill_rate(qbo_vendor)
        ):
            return qbo_vendor

        qbo_vendor_id = self._clean(qbo_vendor.get(const.QBO_KEY_ID))
        if not access_token or not base_url or not realm_id or not qbo_vendor_id:
            return qbo_vendor

        try:
            detailed_vendor = self._fetch_qbo_vendor_by_id(
                access_token=access_token,
                base_url=base_url,
                realm_id=realm_id,
                vendor_id=qbo_vendor_id,
            )
            return self._merge_qbo_employee_snapshots([qbo_vendor, detailed_vendor])
        except Exception:
            logger.warning(
                "[EmployeeImport] QBO 1099 vendor detail fetch failed | qbo_vendor_id=%s",
                qbo_vendor_id,
                exc_info=True,
            )
            return qbo_vendor

    def _1099_email_change_reason(
        self,
        *,
        current_employee: Dict[str, Any],
        qbo_vendor_id: str,
        qbo_email: str,
        display_name: str,
    ) -> Tuple[str, str]:
        local_email = self._clean(current_employee.get(const.KEY_EMAIL))
        qbo_email = self._clean(qbo_email)
        contractor_label = display_name or " ".join(
            part for part in [
                self._clean(current_employee.get("firstname")),
                self._clean(current_employee.get("lastname")),
            ] if part
        ) or f"Vendor ID {qbo_vendor_id}"
        message = (
            f"The email change was not synced for 1099 contractor {contractor_label}. "
            "FIELDCRAFT uses the employee email as the permanent worker identity after QuickBooks is linked. "
            f"FIELDCRAFT currently has {local_email or 'no email'}, while QuickBooks has {qbo_email or 'no email'}. "
            "Change the QuickBooks email back to the FIELDCRAFT email, then run employee sync again."
        )
        return message, self._build_reason(
            message,
            code=self.REASON_1099_EMAIL_CHANGE_NOT_ALLOWED,
            worker_type=const.EMPLOYEE_TYPE_1099,
            fieldcraft_email=local_email,
            qbo_email=qbo_email,
            qbo_vendor_id=qbo_vendor_id,
            display_name=display_name or None,
            action_required=(
                "Make the FIELDCRAFT and QuickBooks contractor emails match the originally "
                "linked employee identity, then run employee sync again."
            ),
        )

    def _1099_tracking_removed_reason(
        self,
        *,
        qbo_vendor_id: str,
        display_name: str,
    ) -> Tuple[str, str]:
        contractor_label = display_name or f"Vendor ID {qbo_vendor_id}"
        message = (
            f"1099 tracking was turned off for QuickBooks vendor {contractor_label}. "
            "This record is already linked to a FIELDCRAFT 1099 employee, so its worker type "
            "cannot be changed after synchronization. "
            "Turn 1099 tracking back on in QuickBooks, then run employee sync again."
        )
        return message, self._build_reason(
            message,
            code=self.REASON_1099_TRACKING_REMOVED,
            qbo_vendor_id=qbo_vendor_id,
            display_name=display_name or None,
            action_required=(
                "Turn 1099 tracking back on for this QuickBooks vendor, then run employee sync again."
            ),
        )

    def _query_qbo_employees(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        start_position: int,
        max_results: int,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        query_url = f"{base_url}/v3/company/{realm_id}/query"
        query = f"select * from Employee STARTPOSITION {int(start_position)} MAXRESULTS {int(max_results)}"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        resp = qbo_err.qbo_get(query_url, headers=headers, params={"query": query, "minorversion": self.QBO_EMPLOYEE_MINOR_VERSION}, timeout=60)
        qbo_err.raise_for_qbo_response(resp, fallback_message="QBO employee query failed")
        data = qbo_err.safe_json(resp)
        employees = (data.get("QueryResponse") or {}).get("Employee") or []
        return (employees if isinstance(employees, list) else [], qbo_api.extract_intuit_tid(resp))

    def _fetch_qbo_employee_by_id(self, *, access_token: str, base_url: str, realm_id: str, employee_id: str) -> Dict[str, Any]:
        url = f"{base_url}/v3/company/{realm_id}/employee/{employee_id}"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        resp = qbo_err.qbo_get(url, headers=headers, params={"minorversion": self.QBO_EMPLOYEE_MINOR_VERSION}, timeout=60)
        qbo_err.raise_for_qbo_response(resp, fallback_message="QBO get employee failed")
        data = qbo_err.safe_json(resp)
        return data.get("Employee") or {}

    def _refresh_access_token(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        tenant_id: str,
        service_id: str,
        connection: Dict[str, Any],
    ) -> str:
        return QuickBooksOAuthClient(repository=self.qbo_repo).refresh_access_token(
            tenant_id=str(tenant_id),
            service_id=str(service_id),
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            token_url=self.qbo_service.token_url,
            current_connection=connection,
        )

    def _get_qbo_api_base(self, env: str) -> str:
        from source.Utils import QBODiscoveryClient
        return QBODiscoveryClient.get_qbo_api_base(env)

    def _get_qbo_last_updated_at(self, qbo_employee: Dict[str, Any]) -> Optional[datetime]:
        metadata = qbo_employee.get("MetaData") or {}
        return self._parse_timestamp(metadata.get("LastUpdatedTime") or metadata.get("CreateTime"))

    def _should_skip_import_update(self, qbo_last_updated_at: Optional[datetime], local_latest_at: Optional[datetime]) -> bool:
        if qbo_last_updated_at is None or local_latest_at is None:
            return False
        return self._ensure_aware(qbo_last_updated_at) <= self._ensure_aware(local_latest_at)

    def _is_qbo_employee_inactive(self, qbo_employee: Dict[str, Any]) -> bool:
        display_name = self._clean(qbo_employee.get(const.QBO_KEY_DISPLAY_NAME)).lower()
        status = self._clean(qbo_employee.get("status") or qbo_employee.get("Status")).lower()
        return qbo_employee.get("Active") is False or status == "deleted" or "(deleted)" in display_name or "(deleted-" in display_name

    def _is_cdc_too_old(self, changed_since: datetime) -> bool:
        return self._ensure_aware(changed_since) < (datetime.now(timezone.utc) - timedelta(days=29))

    def _normalize_changed_since(self, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        return self._ensure_aware(value) - timedelta(minutes=1)

    def _parse_timestamp(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return self._ensure_aware(value)
        raw = str(value).strip().replace("Z", "+00:00")
        if not raw:
            return None
        try:
            return self._ensure_aware(datetime.fromisoformat(raw))
        except ValueError:
            return None

    def _qbo_date_to_iso(self, value: Any) -> Optional[str]:
        if not value:
            return None
        if isinstance(value, datetime):
            return self._ensure_aware(value).isoformat().replace("+00:00", "Z")
        if isinstance(value, date):
            return datetime.combine(value, time.min, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        raw = str(value).strip()
        if not raw:
            return None
        parsed = self._parse_timestamp(raw)
        if parsed:
            return parsed.isoformat().replace("+00:00", "Z")
        try:
            parsed_date = date.fromisoformat(raw[:10])
            return datetime.combine(parsed_date, time.min, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            return None

    def _ensure_aware(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _split_name(self, name: str) -> Tuple[str, str]:
        parts = [part for part in self._clean(name).split() if part]
        if not parts:
            return "Employee", ""
        if len(parts) == 1:
            return parts[0], ""
        return parts[0], " ".join(parts[1:])

    def _normalize_phone(self, value: Any) -> str:
        return re.sub(r"\D+", "", self._clean(value))

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()
