from __future__ import annotations

import json
import logging
import re
import time

import requests
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from source.dal.EmployeeRepository import EmployeeRepository
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.quickbooks.QuickBooksAuthentication import QuickBooksOAuthClient
from source.service.QuickBooksService import QuickBooksService
from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils import QuickBooksErrorHandler as qbo_err
from source.Utils.QuickBooksErrorHandler import QuickBooksAPIError
from source.Utils import constants as const
from source.Utils.auditlog import safe_audit_log


logger = logging.getLogger(__name__)


class QuickBooksEmployeeExportService:
    """Export FIELDCRAFT user_account employees to QuickBooks Employee records."""

    QBO_EMPLOYEE_MINOR_VERSION = "75"
    AMBIGUOUS_CREATE_LOOKUP_ATTEMPTS = 5
    AMBIGUOUS_CREATE_LOOKUP_DELAY_SECONDS = 1.0

    def __init__(self):
        self.qbo_repo = QuickBooksRepository()
        self.qbo_service = QuickBooksService()
        self.employee_repo = EmployeeRepository()

    def _assert_mapping_realm(self, mapping: Optional[Dict[str, Any]], realm_id: str) -> None:
        if not isinstance(mapping, dict):
            return
        mapped_realm = self._clean(mapping.get("external_account_id"))
        if mapped_realm and mapped_realm != self._clean(realm_id):
            raise ValueError(
                "This employee mapping belongs to a different QuickBooks company. Re-sync the employee and retry."
            )

    def _log_debug(self, message: str, **data: Any) -> None:
        logger.debug("[EmployeeExport] %s | %s", message, data or {})

    def _log_exception(self, message: str, exc: Exception) -> None:
        logger.exception("[EmployeeExport] %s | error=%s", message, exc)

    @staticmethod
    def _build_reason(message: str, intuit_tid: Optional[str] = None, **extra: Any) -> str:
        data: Dict[str, Any] = {"message": message}
        if intuit_tid:
            data["intuit_tid"] = intuit_tid
        for key, value in extra.items():
            if value is not None:
                data[key] = value
        return json.dumps(data)

    def _qbo_display_name(self, qbo_employee: Optional[Dict[str, Any]], fallback: Optional[str] = None) -> Optional[str]:
        if isinstance(qbo_employee, dict):
            display_name = self._clean(qbo_employee.get(const.QBO_KEY_DISPLAY_NAME))
            if display_name:
                return display_name
        return self._clean(fallback) or None

    def export_employees(
        self,
        *,
        tenant_id: str,
        service_id: str,
        limit: int = 500,
        offset: int = 0,
        after_mapping_id: Optional[int] = None,
        object_id: Optional[str] = None,
        email: Optional[str] = None,
        user_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        logger.info(
            "[EmployeeExport] export_employees called | tenant_id=%s service_id=%s limit=%s offset=%s",
            tenant_id,
            service_id,
            limit,
            offset,
        )
        self._log_debug(
            "export_employees started",
            tenant_id=tenant_id,
            service_id=service_id,
            limit=limit,
            offset=offset,
        )
        safe_audit_log(
            tenant_id=tenant_id,
            action="employee_export_started",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="started",
            resource_type="quickbooks_employee",
        )

        audit_action = "employee_export_failed"
        audit_status = "failed"
        audit_context: Optional[str] = None

        try:
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}

            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = (connection.get(const.KEY_ENV) or "sandbox").lower()
            conn_status = (connection.get(const.KEY_STATUS) or const.STATUS_CONNECTED).lower()
            self._log_debug(
                "connection loaded",
                env=env,
                connection_status=conn_status,
                realmIdPresent=bool(realm_id),
                refreshTokenPresent=bool(refresh_token),
            )

            if conn_status and conn_status != const.STATUS_CONNECTED:
                audit_action, audit_context = "employee_export_aborted", f"not_connected status={conn_status}"
                return {const.KEY_OK: False, const.KEY_MESSAGE: f"QuickBooks is not connected (status={conn_status})"}
            if not refresh_token or not realm_id:
                audit_action, audit_context = "employee_export_aborted", "missing_realmId_or_refreshToken"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks not connected for this tenant (missing realmId/refreshToken)."}

            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)
            self._log_debug(
                "quickbooks config loaded",
                clientIdPresent=bool(client_id),
                clientSecretPresent=bool(client_secret),
            )
            if not client_id or not client_secret:
                audit_action, audit_context = "employee_export_aborted", "missing_credentials"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks client credentials missing (ClientId/ClientSecret)."}

            access_token = self.refresh_access_token(
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
                tenant_id=tenant_id,
                service_id=service_id,
                connection=connection,
            )
            self._log_debug("access token refreshed", accessTokenPresent=bool(access_token))
            base_url = self.get_qbo_api_base(env)
            self._log_debug("qbo base url resolved", base_url=base_url)
            employee_sync_config = self._employee_sync_config(
                tenant_id=tenant_id,
                service_id=service_id,
            )
            candidate_kwargs = dict(
                tenant_id=tenant_id,
                service_id=service_id,
                limit=limit,
                offset=offset,
            )
            if after_mapping_id is not None:
                candidate_kwargs["after_mapping_id"] = after_mapping_id
            candidates = self.employee_repo.get_export_candidates(**candidate_kwargs)
            if not isinstance(candidates, list):
                candidates = []
            self._log_debug("export candidates loaded", count=len(candidates))

            summary: Dict[str, Any] = {
                const.KEY_OK: True,
                "tenantId": tenant_id,
                "serviceId": str(service_id),
                "realmIdPresent": bool(realm_id),
                "env": env,
                "requested": len(candidates),
                "lastMappingId": max((int((item.get("mapping") or {}).get("id") or 0) for item in candidates), default=None),
                "skippedAlreadyMapped": 0,
                "skippedQboNewer": 0,
                "mapped from QBO": 0,
                "createdInQbo": 0,
                "updatedInQbo": 0,
                "failed": 0,
                const.KEY_FAILURES: [],
                const.KEY_RESULTS: [],
            }

            if not candidates:
                self._log_debug("no employee export candidates found")
                summary[const.KEY_MESSAGE] = "No pending employee export candidates found."
                audit_action, audit_status, audit_context = "employee_export_completed", "success", "no_candidates"
                return summary

            for item in candidates:
                employee = item["employee"]
                mapping = item["mapping"] or {}
                employee_id = int(employee["id"])
                display_name = self.build_display_name(employee)
                mapping_id = mapping.get("id")
                detail = {
                    const.KEY_FIELDCRAFT_EMPLOYEE_ID: employee_id,
                    const.KEY_DISPLAY_NAME: display_name,
                    const.KEY_EMAIL: employee.get(const.KEY_EMAIL),
                    const.KEY_STATUS: None,
                    const.KEY_ACTION: None,
                    const.KEY_QBO_EMPLOYEE_ID: mapping.get("external_employee_id") or None,
                    const.KEY_SYNC_TOKEN: mapping.get("sync_token"),
                    const.KEY_REASON: None,
                    const.KEY_ERROR: None,
                }

                self._log_debug(
                    "processing employee",
                    employee_id=employee_id,
                    mapping_id=mapping_id,
                    current_sync_status=mapping.get("sync_status"),
                    external_employee_id=mapping.get("external_employee_id"),
                    email=employee.get(const.KEY_EMAIL),
                    display_name=display_name,
                )

                try:
                    if not mapping_id:
                        raise Exception("Missing employee external map row for export candidate")
                    if self._is_blank(display_name):
                        raise Exception("Employee display name is missing. Please add a name to this employee before exporting to QuickBooks.")
                    if not self._clean(employee.get("firstname")):
                        raise Exception("Employee first name is missing. Please add a first name before exporting to QuickBooks.")

                    repair_mapping = getattr(
                        self.employee_repo,
                        "repair_pending_mapping_identity",
                        None,
                    )
                    if callable(repair_mapping):
                        repaired_mapping = repair_mapping(
                            tenant_id=tenant_id,
                            service_id=service_id,
                            mapping_id=int(mapping_id),
                            user_account_id=employee_id,
                            expected_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE,
                        )
                        if isinstance(repaired_mapping, dict) and repaired_mapping:
                            mapping = repaired_mapping
                    external_employee_id = self._clean(mapping.get("external_employee_id"))
                    detail[const.KEY_QBO_EMPLOYEE_ID] = external_employee_id or None
                    detail[const.KEY_SYNC_TOKEN] = mapping.get("sync_token")
                    self._assert_mapping_realm(mapping, str(realm_id))
                    current_status = self._clean(mapping.get("sync_status")).upper()
                    if external_employee_id and current_status not in (
                        self.employee_repo.STATUS_PENDING_PUSH,
                        self.employee_repo.STATUS_FAILED,
                        self.employee_repo.STATUS_SYNC_FAILED,
                        "",
                    ):
                        self._log_debug(
                            "skipping already mapped employee",
                            employee_id=employee_id,
                            mapping_id=mapping_id,
                            external_employee_id=external_employee_id,
                            current_sync_status=current_status,
                        )
                        summary["skippedAlreadyMapped"] += 1
                        continue

                    stale_mapping_detected = False
                    if external_employee_id:
                        try:
                            self._log_debug(
                                "mapped employee found; checking QuickBooks employee",
                                employee_id=employee_id,
                                qbo_employee_id=external_employee_id,
                            )
                            stored_sync_token = self._clean(mapping.get("sync_token")) or None
                            qbo_current = self.get_employee_in_qbo(
                                access_token=access_token,
                                base_url=base_url,
                                realm_id=str(realm_id),
                                employee_id=external_employee_id,
                            )
                            if self._is_qbo_employee_inactive(qbo_current) and bool(employee.get("active")):
                                self._log_debug(
                                    "QuickBooks employee is inactive",
                                    employee_id=employee_id,
                                    qbo_employee_id=external_employee_id,
                                )
                                reason = self._qbo_employee_inactive_reason(external_employee_id, qbo_current)
                                self.employee_repo.mark_mapping_failed(
                                    tenant_id=tenant_id,
                                    mapping_id=int(mapping_id),
                                    status=self.employee_repo.STATUS_SYNC_FAILED,
                                    reason=reason,
                                    external_display_name=self._qbo_display_name(qbo_current),
                                )
                                summary["failed"] += 1
                                qbo_api.append_failure(summary, {
                                    const.KEY_FIELDCRAFT_EMPLOYEE_ID: employee_id,
                                    const.KEY_QBO_EMPLOYEE_ID: external_employee_id,
                                    const.KEY_ERROR: "QuickBooks employee is inactive",
                                    const.KEY_DETAILS: reason,
                                })
                                detail.update({
                                    const.KEY_STATUS: "failed",
                                    const.KEY_ACTION: "qboEmployeeInactive",
                                    const.KEY_ERROR: "QuickBooks employee is inactive",
                                    const.KEY_REASON: reason,
                                })
                                summary[const.KEY_RESULTS].append(detail)
                                continue

                            qbo_current_sync_token = self._clean(qbo_current.get(const.QBO_KEY_SYNC_TOKEN)) or None
                            qbo_last_updated_at = self._get_qbo_last_updated_at(qbo_current)
                            fieldcraft_updated_at = self.employee_repo.get_employee_latest_local_timestamp(employee)

                            qbo_changed_independently = bool(
                                stored_sync_token
                                and qbo_current_sync_token
                                and stored_sync_token != qbo_current_sync_token
                            )
                            if not qbo_changed_independently:
                                qbo_changed_independently = bool(
                                    qbo_last_updated_at and fieldcraft_updated_at and qbo_last_updated_at > fieldcraft_updated_at
                                )

                            self._log_debug(
                                "version check completed",
                                employee_id=employee_id,
                                qbo_employee_id=external_employee_id,
                                storedSyncTokenPresent=bool(stored_sync_token),
                                qboSyncTokenPresent=bool(qbo_current_sync_token),
                                qboChangedIndependently=qbo_changed_independently,
                                qboLastUpdatedAt=qbo_last_updated_at.isoformat() if qbo_last_updated_at else None,
                                fieldcraftUpdatedAt=fieldcraft_updated_at.isoformat() if fieldcraft_updated_at else None,
                            )

                            if qbo_changed_independently and qbo_last_updated_at and fieldcraft_updated_at and qbo_last_updated_at > fieldcraft_updated_at:
                                self._log_debug(
                                    "skipping export because QuickBooks has newer data",
                                    employee_id=employee_id,
                                    qbo_employee_id=external_employee_id,
                                )
                                reason = self._build_reason(
                                    "This employee is already linked to QuickBooks, and QuickBooks has the latest version. "
                                    "No export was needed; employee sync will keep the FIELDCRAFT record aligned.",
                                    code="EMPLOYEE_QBO_NEWER",
                                    qbo_updated_at=qbo_last_updated_at.isoformat(),
                                    fieldcraft_updated_at=fieldcraft_updated_at.isoformat(),
                                )
                                self.employee_repo.mark_mapping_synced(
                                    tenant_id=tenant_id,
                                    mapping_id=int(mapping_id),
                                    external_employee_id=external_employee_id,
                                    external_account_id=str(realm_id),
                                    sync_token=qbo_current_sync_token,
                                    reason=reason,
                                    external_display_name=self._qbo_display_name(qbo_current),
                                )
                                summary["skippedQboNewer"] += 1
                                detail.update({
                                    const.KEY_STATUS: "skipped",
                                    const.KEY_ACTION: "skippedQboNewer",
                                    const.KEY_QBO_EMPLOYEE_ID: external_employee_id,
                                    const.KEY_SYNC_TOKEN: qbo_current_sync_token,
                                    const.KEY_REASON: reason,
                                })
                                summary[const.KEY_RESULTS].append(detail)
                                continue

                            self._log_debug(
                                "updating employee in QuickBooks",
                                employee_id=employee_id,
                                qbo_employee_id=external_employee_id,
                            )
                            updated = self.update_employee_in_qbo(
                                access_token=access_token,
                                base_url=base_url,
                                realm_id=str(realm_id),
                                employee_id=external_employee_id,
                                payload=self.build_qbo_employee_payload(
                                    employee,
                                    include_display_name=True,
                                    cost_sync_config=employee_sync_config,
                                ),
                                sync_token=qbo_current_sync_token or stored_sync_token,
                            )
                            updated_id = self._clean((updated or {}).get(const.QBO_KEY_ID)) or external_employee_id
                            updated_token = self._clean((updated or {}).get(const.QBO_KEY_SYNC_TOKEN)) or None
                            reason = self._build_reason("Employee version exported to QuickBooks", code="EMPLOYEE_UPDATED_IN_QBO")
                            self.employee_repo.mark_mapping_synced(
                                tenant_id=tenant_id,
                                mapping_id=int(mapping_id),
                                external_employee_id=updated_id,
                                external_account_id=str(realm_id),
                                sync_token=updated_token,
                                reason=reason,
                                external_display_name=self._qbo_display_name(updated, self._qbo_display_name(qbo_current, mapping.get("external_display_name"))),
                            )
                            summary["updatedInQbo"] += 1
                            self._log_debug(
                                "employee updated in QuickBooks",
                                employee_id=employee_id,
                                qbo_employee_id=updated_id,
                                syncTokenPresent=bool(updated_token),
                            )
                            detail.update({
                                const.KEY_STATUS: "success",
                                const.KEY_ACTION: "updated in QBO",
                                const.KEY_QBO_EMPLOYEE_ID: updated_id,
                                const.KEY_SYNC_TOKEN: updated_token,
                                const.KEY_REASON: reason,
                            })
                            summary[const.KEY_RESULTS].append(detail)
                            continue
                        except Exception as exc:
                            if self._is_qbo_stale_reference_error(str(exc)):
                                self._log_exception("stale QuickBooks employee mapping detected", exc)
                                logger.info(
                                    "[EmployeeExport] stale employee mapping detected | employee_id=%s qbo_employee_id=%s",
                                    employee_id,
                                    external_employee_id,
                                )
                                stale_mapping_detected = True
                                external_employee_id = ""
                            else:
                                raise

                    self._log_debug(
                        "searching QuickBooks for existing employee match",
                        employee_id=employee_id,
                        display_name=display_name,
                        email=employee.get(const.KEY_EMAIL),
                    )
                    qbo_candidates = self.find_employee_candidates_in_qbo(
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=str(realm_id),
                        employee=employee,
                    )
                    self._log_debug(
                        "QuickBooks employee search completed",
                        employee_id=employee_id,
                        candidate_count=len(qbo_candidates),
                    )
                    if len(qbo_candidates) > 1:
                        raise Exception(
                            "Multiple QuickBooks employees matched this FIELDCRAFT employee by email/display name. "
                            "Please resolve the duplicate employees in QuickBooks, then run employee sync again."
                        )

                    matched_employee = qbo_candidates[0] if len(qbo_candidates) == 1 else None
                    if matched_employee and matched_employee.get(const.QBO_KEY_ID):
                        found_id = self._clean(matched_employee.get(const.QBO_KEY_ID))
                        found_token = self._clean(matched_employee.get(const.QBO_KEY_SYNC_TOKEN)) or None
                        reason = self._build_reason(
                            "Employee matched in QuickBooks and mapping synced",
                            code="EMPLOYEE_MAPPED_TO_EXISTING_QBO",
                        )
                        self.employee_repo.claim_external_employee_mapping(
                            tenant_id=tenant_id,
                            service_id=service_id,
                            user_account_id=employee_id,
                            mapping_id=int(mapping_id),
                            external_employee_id=found_id,
                            external_account_id=str(realm_id),
                            sync_token=found_token,
                            reason=reason,
                            external_display_name=self._qbo_display_name(matched_employee),
                        )
                        summary["mapped from QBO"] += 1
                        self._log_debug(
                            "employee mapped to existing QuickBooks employee",
                            employee_id=employee_id,
                            qbo_employee_id=found_id,
                            syncTokenPresent=bool(found_token),
                        )
                        detail.update({
                            const.KEY_STATUS: "success",
                            const.KEY_ACTION: "mapped from QBO",
                            const.KEY_QBO_EMPLOYEE_ID: found_id,
                            const.KEY_SYNC_TOKEN: found_token,
                            const.KEY_REASON: reason,
                        })
                        summary[const.KEY_RESULTS].append(detail)
                        continue

                    display_name_override = None
                    create_reason = "Employee created in QuickBooks and mapping synced"
                    if stale_mapping_detected or qbo_candidates:
                        display_name_override = self.build_unique_display_name(employee)
                        if display_name_override and display_name_override != display_name:
                            create_reason = "Employee created in QuickBooks with a unique DisplayName to avoid a name collision"

                    try:
                        self._log_debug(
                            "creating employee in QuickBooks",
                            employee_id=employee_id,
                            displayNameOverride=display_name_override,
                        )
                        created = self.create_employee_in_qbo(
                            access_token=access_token,
                            base_url=base_url,
                            realm_id=str(realm_id),
                            payload=self.build_qbo_employee_payload(
                                employee,
                                display_name_override=display_name_override,
                                cost_sync_config=employee_sync_config,
                            ),
                        )
                    except Exception as exc:
                        self._log_exception("employee create attempt failed", exc)
                        if self._is_ambiguous_create_failure(exc):
                            created = self._recover_employee_after_ambiguous_create(
                                access_token=access_token,
                                base_url=base_url,
                                realm_id=str(realm_id),
                                employee=employee,
                                display_name_override=display_name_override,
                            )
                            create_reason = (
                                "Employee create response timed out, but the created QuickBooks employee "
                                "was safely recovered and mapped"
                            )
                        elif not display_name_override and self._is_qbo_duplicate_name_error(str(exc)):
                            display_name_override = self.build_unique_display_name(employee)
                            self._log_debug(
                                "retrying employee create with unique display name",
                                employee_id=employee_id,
                                displayNameOverride=display_name_override,
                            )
                            try:
                                created = self.create_employee_in_qbo(
                                    access_token=access_token,
                                    base_url=base_url,
                                    realm_id=str(realm_id),
                                    payload=self.build_qbo_employee_payload(
                                        employee,
                                        display_name_override=display_name_override,
                                        cost_sync_config=employee_sync_config,
                                    ),
                                )
                            except Exception as retry_exc:
                                if not self._is_ambiguous_create_failure(retry_exc):
                                    raise
                                created = self._recover_employee_after_ambiguous_create(
                                    access_token=access_token,
                                    base_url=base_url,
                                    realm_id=str(realm_id),
                                    employee=employee,
                                    display_name_override=display_name_override,
                                )
                                create_reason = (
                                    "Employee create response timed out, but the uniquely named QuickBooks employee "
                                    "was safely recovered and mapped"
                                )
                            else:
                                create_reason = (
                                    "Employee created in QuickBooks with a unique DisplayName "
                                    "to avoid duplicate name conflict"
                                )
                        else:
                            raise

                    created_id = self._clean((created or {}).get(const.QBO_KEY_ID))
                    if not created_id:
                        raise Exception("QuickBooks created no usable Employee ID.")
                    created_token = self._clean((created or {}).get(const.QBO_KEY_SYNC_TOKEN)) or None
                    reason = self._build_reason(create_reason, code="EMPLOYEE_CREATED_IN_QBO")
                    self.employee_repo.claim_external_employee_mapping(
                        tenant_id=tenant_id,
                        service_id=service_id,
                        user_account_id=employee_id,
                        mapping_id=int(mapping_id),
                        external_employee_id=created_id,
                        external_account_id=str(realm_id),
                        sync_token=created_token,
                        reason=reason,
                        external_display_name=self._qbo_display_name(created, display_name_override or display_name),
                    )
                    summary["createdInQbo"] += 1
                    self._log_debug(
                        "employee created in QuickBooks",
                        employee_id=employee_id,
                        qbo_employee_id=created_id,
                        syncTokenPresent=bool(created_token),
                    )
                    detail.update({
                        const.KEY_STATUS: "success",
                        const.KEY_ACTION: "created in QBO",
                        const.KEY_QBO_EMPLOYEE_ID: created_id,
                        const.KEY_SYNC_TOKEN: created_token,
                        const.KEY_REASON: reason,
                    })
                    summary[const.KEY_RESULTS].append(detail)
                except Exception as exc:
                    self._log_exception("employee export item failed", exc)
                    intuit_tid = exc.intuit_tid if isinstance(exc, QuickBooksAPIError) else None
                    reason = self._build_reason(
                        exc.user_message if isinstance(exc, QuickBooksAPIError) else str(exc),
                        intuit_tid=intuit_tid,
                        code="EMPLOYEE_EXPORT_FAILED",
                    )
                    if mapping_id:
                        self.employee_repo.mark_mapping_failed(
                            tenant_id=tenant_id,
                            mapping_id=int(mapping_id),
                            reason=reason,
                        )
                    summary["failed"] += 1
                    failure = {
                        const.KEY_FIELDCRAFT_EMPLOYEE_ID: employee_id,
                        const.KEY_QBO_EMPLOYEE_ID: mapping.get("external_employee_id") or None,
                        const.KEY_ERROR: str(exc),
                    }
                    qbo_api.append_failure(summary, failure)
                    detail.update({
                        const.KEY_STATUS: "failed",
                        const.KEY_ACTION: "error",
                        const.KEY_ERROR: str(exc),
                        const.KEY_REASON: reason,
                    })
                    summary[const.KEY_RESULTS].append(detail)

            failed = int(summary.get("failed") or 0)
            successful = (
                int(summary.get("createdInQbo") or 0)
                + int(summary.get("updatedInQbo") or 0)
                + int(summary.get("mapped from QBO") or 0)
            )
            summary["partialSuccess"] = bool(failed and successful)
            if failed and not successful:
                summary[const.KEY_OK] = False
                summary[const.KEY_MESSAGE] = (
                    "No W2 employees were exported. Review the failed records, correct the listed issue, and retry."
                )
                audit_action = "employee_export_failed"
                audit_status = "failed"
            elif failed:
                summary[const.KEY_OK] = True
                summary[const.KEY_MESSAGE] = (
                    f"W2 employee export completed with {failed} failed record(s). Review those records and retry."
                )
                audit_action = "employee_export_completed_with_errors"
                audit_status = "partial_success"
            else:
                summary[const.KEY_OK] = True
                summary[const.KEY_MESSAGE] = "W2 employee export completed successfully."
                audit_action = "employee_export_completed"
                audit_status = "success"
            audit_context = f"exported={successful} failed={failed}"
            self._log_debug("employee export completed", summary=audit_context)
            return summary
        except Exception as exc:
            audit_action = "employee_export_failed"
            audit_status = "failed"
            audit_context = str(exc)[:64]
            self._log_exception("employee export failed", exc)
            logger.exception("[EmployeeExport] export failed")
            return {
                const.KEY_OK: False,
                const.KEY_MESSAGE: "W2 employee export could not be completed. Please retry or contact support.",
                const.KEY_ERROR: "Unexpected employee export error.",
            }
        finally:
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

    def get_qbo_api_base(self, env: str) -> str:
        from source.Utils import QBODiscoveryClient

        return QBODiscoveryClient.get_qbo_api_base(env)

    def refresh_access_token(
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

    def get_employee_in_qbo(self, *, access_token: str, base_url: str, realm_id: str, employee_id: str) -> Dict[str, Any]:
        self._log_debug("QBO get employee request", qbo_employee_id=employee_id)
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/employee/{employee_id}"
        resp = qbo_err.qbo_get(url, headers=headers, params={"minorversion": self.QBO_EMPLOYEE_MINOR_VERSION}, timeout=30)
        self._log_debug("QBO get employee response", qbo_employee_id=employee_id, status_code=resp.status_code)
        qbo_err.raise_for_qbo_response(resp, fallback_message="QBO get employee failed")
        data = qbo_err.safe_json(resp)
        return data.get("Employee") or {}

    def create_employee_in_qbo(self, *, access_token: str, base_url: str, realm_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = payload or {}
        self._log_debug("QBO create employee request", displayName=payload.get(const.QBO_KEY_DISPLAY_NAME))
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/employee"
        resp = qbo_err.qbo_post(url, headers=headers, params={"minorversion": self.QBO_EMPLOYEE_MINOR_VERSION}, json=payload, timeout=30)
        self._log_debug("QBO create employee response", status_code=resp.status_code)
        qbo_err.raise_for_qbo_response(resp, success_statuses=(200, 201), fallback_message="QBO create employee failed")
        data = qbo_err.safe_json(resp)
        return data.get("Employee") or {}

    def update_employee_in_qbo(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        employee_id: str,
        payload: Dict[str, Any],
        sync_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        token_value = self._clean(sync_token) or None
        if not token_value:
            existing = self.get_employee_in_qbo(access_token=access_token, base_url=base_url, realm_id=realm_id, employee_id=employee_id)
            token_value = self._clean(existing.get(const.QBO_KEY_SYNC_TOKEN)) or None
        if not token_value:
            raise Exception("QBO employee update failed: missing SyncToken")

        self._log_debug("QBO update employee request", qbo_employee_id=employee_id, syncTokenPresent=bool(token_value))
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/employee"
        body = {
            const.QBO_KEY_ID: str(employee_id),
            const.QBO_KEY_SYNC_TOKEN: str(token_value),
            "sparse": True,
            **(payload or {}),
        }
        self._log_debug(
            "QBO sparse employee update payload prepared",
            qbo_employee_id=employee_id,
            payload_keys=sorted(body.keys()),
            includes_rate_fields=any(
                self._normalize_qbo_field_name(key) in {
                    "billabletime",
                    "billrate",
                    "costrate",
                    "billablerate",
                    "billingrate",
                    "hourlycostrate",
                    "hourlyrate",
                    "hourlybillrate",
                    "hourlybillingrate",
                    "payrate",
                    "overtimecost",
                    "overtimerate",
                }
                for key in body.keys()
            ),
        )
        resp = qbo_err.qbo_post(url, headers=headers, params={"minorversion": self.QBO_EMPLOYEE_MINOR_VERSION}, json=body, timeout=30)
        self._log_debug("QBO update employee response", qbo_employee_id=employee_id, status_code=resp.status_code)
        if resp.status_code not in (200, 201) and self._is_qbo_sync_token_error(qbo_err.build_user_message(resp, "")):
            self._log_debug("QBO update failed with stale SyncToken; refreshing employee before retry", qbo_employee_id=employee_id)
            existing = self.get_employee_in_qbo(access_token=access_token, base_url=base_url, realm_id=realm_id, employee_id=employee_id)
            body[const.QBO_KEY_SYNC_TOKEN] = str(existing.get(const.QBO_KEY_SYNC_TOKEN))
            resp = qbo_err.qbo_post(url, headers=headers, params={"minorversion": self.QBO_EMPLOYEE_MINOR_VERSION}, json=body, timeout=30)
            self._log_debug("QBO update employee retry response", qbo_employee_id=employee_id, status_code=resp.status_code)
        qbo_err.raise_for_qbo_response(resp, success_statuses=(200, 201), fallback_message="QBO update employee failed")
        data = qbo_err.safe_json(resp)
        return data.get("Employee") or {}

    def find_employee_candidates_in_qbo(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        employee: Dict[str, Any],
        display_name_overrides: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        preferred_display_names = [
            self._clean(value) for value in (display_name_overrides or []) if self._clean(value)
        ]
        for preferred_name in preferred_display_names:
            self._log_debug("querying QuickBooks employee by preferred display name", display_name=preferred_name)
            matches = self._dedupe_qbo_employees(
                self._query_employees(
                    access_token,
                    base_url,
                    realm_id,
                    const.QBO_KEY_DISPLAY_NAME,
                    preferred_name,
                )
            )
            if matches:
                return matches

        email = self._clean(employee.get(const.KEY_EMAIL))
        if email:
            self._log_debug("querying QuickBooks employee by email", email=email)
            email_matches = self._dedupe_qbo_employees(
                self._query_employees(access_token, base_url, realm_id, "PrimaryEmailAddr", email)
            )
            if email_matches:
                return email_matches

        display_names: List[str] = []
        display_names.extend(preferred_display_names)
        display_names.extend([self.build_display_name(employee), self.build_plain_display_name(employee)])
        seen_names = set()
        for display_name in display_names:
            normalized = self._clean(display_name)
            key = normalized.casefold()
            if not normalized or key in seen_names:
                continue
            seen_names.add(key)
            self._log_debug("querying QuickBooks employee by display name", display_name=normalized)
            matches = self._dedupe_qbo_employees(
                self._query_employees(
                    access_token,
                    base_url,
                    realm_id,
                    const.QBO_KEY_DISPLAY_NAME,
                    normalized,
                )
            )
            if matches:
                return matches
        return []

    def _dedupe_qbo_employees(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        by_id: Dict[str, Dict[str, Any]] = {}
        for item in candidates or []:
            item_id = self._clean(item.get(const.QBO_KEY_ID))
            if item_id:
                by_id[item_id] = item
        return list(by_id.values())

    def _query_employees(
        self,
        access_token: str,
        base_url: str,
        realm_id: str,
        field_name: str,
        field_value: str,
    ) -> List[Dict[str, Any]]:
        value = self._clean(field_value)
        if not value:
            return []
        query_url = f"{base_url}/v3/company/{realm_id}/query"
        query = f"select * from Employee where {field_name} = '{self._escape_qbo_literal(value)}'"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        try:
            resp = qbo_err.qbo_get(
                query_url,
                headers=headers,
                params={"query": query, "minorversion": self.QBO_EMPLOYEE_MINOR_VERSION},
                timeout=30,
            )
            self._log_debug("QBO employee query response", field=field_name, status_code=resp.status_code)
            qbo_err.raise_for_qbo_response(resp, fallback_message="QuickBooks employee query failed")
            data = qbo_err.safe_json(resp)
            employees = (data.get("QueryResponse") or {}).get("Employee") or []
            result = employees if isinstance(employees, list) else []
            self._log_debug("QBO employee query parsed", field=field_name, count=len(result))
            return result
        except Exception as exc:
            self._log_exception("QBO employee query failed", exc)
            raise RuntimeError(
                "QuickBooks employee lookup could not be completed. No employee was created to avoid a duplicate record. "
                "Please retry the sync."
            ) from exc

    def _is_ambiguous_create_failure(self, exc: Exception) -> bool:
        return isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError))

    def _recover_employee_after_ambiguous_create(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        employee: Dict[str, Any],
        display_name_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        last_error: Optional[Exception] = None
        expected_display_name = self._clean(display_name_override) or self.build_display_name(employee)
        overrides = [expected_display_name] if expected_display_name else None
        for attempt in range(self.AMBIGUOUS_CREATE_LOOKUP_ATTEMPTS):
            if attempt and self.AMBIGUOUS_CREATE_LOOKUP_DELAY_SECONDS > 0:
                time.sleep(self.AMBIGUOUS_CREATE_LOOKUP_DELAY_SECONDS * attempt)
            try:
                candidates = self.find_employee_candidates_in_qbo(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=realm_id,
                    employee=employee,
                    display_name_overrides=overrides,
                )
            except Exception as lookup_exc:
                last_error = lookup_exc
                continue
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                raise RuntimeError(
                    "The QuickBooks employee create result was uncertain and multiple matching employees were found. "
                    "No mapping was changed. Review the duplicate employees in QuickBooks before retrying."
                )

        message = (
            "QuickBooks did not return the employee create response, and the employee could not be safely located afterward. "
            "The create request was not retried because that could create a duplicate. Check QuickBooks, then run employee sync again."
        )
        if last_error:
            raise RuntimeError(message) from last_error
        raise RuntimeError(message)

    def build_display_name(self, employee: Dict[str, Any]) -> str:
        first_name = self._clean_display_name_part(employee.get("firstname") or employee.get("first_name") or employee.get("firstName"))
        middle_name = self._clean_display_name_part(employee.get("middlename") or employee.get("middle_name") or employee.get("middleName"))
        last_name = self._clean_display_name_part(employee.get("lastname") or employee.get("last_name") or employee.get("lastName"))
        base_name = " ".join(part for part in (first_name, middle_name, last_name) if part) or "Employee"
        phone = self._build_display_phone(employee)
        display_name = f"{base_name}-{phone}" if phone else base_name
        return self._truncate_display_name(display_name)

    def build_plain_display_name(self, employee: Dict[str, Any]) -> str:
        parts = (
            employee.get("firstname") or employee.get("first_name") or employee.get("firstName"),
            employee.get("middlename") or employee.get("middle_name") or employee.get("middleName"),
            employee.get("lastname") or employee.get("last_name") or employee.get("lastName"),
        )
        return self._truncate_display_name(
            " ".join(self._clean_display_name_part(part) for part in parts if self._clean_display_name_part(part))
        )

    def _clean_display_name_part(self, value: Any) -> str:
        return " ".join(str(value or "").strip().split())

    def _build_display_phone(self, employee: Dict[str, Any]) -> str:
        raw_phone = (
            employee.get("mobile_no")
            or employee.get(const.KEY_PHONE)
            or employee.get("phone")
            or employee.get("alternate_mobile_no")
            or employee.get("alternateMobileNo")
            or ""
        )
        digits = "".join(ch for ch in str(raw_phone) if ch.isdigit())
        return digits[:20]

    def build_unique_display_name(self, employee: Dict[str, Any]) -> str:
        base_name = self.build_display_name(employee)
        suffix = self._clean(employee.get("object_id"))[:8] or self._clean(employee.get("id"))
        if not suffix:
            suffix = self._clean(employee.get("email")).split("@", 1)[0][:16]
        if not suffix:
            return base_name
        return self._append_display_name_suffix(base_name, f"emp-{suffix}")

    def _append_display_name_suffix(self, base_name: str, suffix: str, max_length: int = 100) -> str:
        base_name = self._clean(base_name) or "Employee"
        suffix = self._clean(suffix)
        if not suffix:
            return self._truncate_display_name(base_name, max_length)

        formatted_suffix = f"-{suffix}"
        available_base_length = max_length - len(formatted_suffix)
        if available_base_length <= 0:
            return self._truncate_display_name(f"{base_name}{formatted_suffix}", max_length)

        trimmed_base = base_name[:available_base_length].rstrip()
        return f"{trimmed_base}{formatted_suffix}"

    def _build_qbo_address(self, address_info: Optional[Dict[str, Any]]) -> Dict[str, str]:
        address_info = address_info or {}
        line1 = self._clean(address_info.get("address") or address_info.get("line1"))
        city = self._clean(address_info.get("city"))
        state = self._clean(address_info.get("state") or address_info.get("country_subdivision_code"))
        country = self._clean(address_info.get("country"))
        postal_code = self._clean(address_info.get("zip_code") or address_info.get("postal_code"))

        qbo_address: Dict[str, str] = {}
        if line1:
            qbo_address["Line1"] = line1
        if city:
            qbo_address["City"] = city
        if state:
            qbo_address["CountrySubDivisionCode"] = state
        if country:
            qbo_address["Country"] = country
        if postal_code:
            qbo_address["PostalCode"] = postal_code
        return qbo_address

    def _employee_sync_config(self, *, tenant_id: str, service_id: str) -> Dict[str, Any]:
        raw: Dict[str, Any] = {}
        getter = getattr(self.qbo_repo, "getEmployeeSyncConfig", None)
        if callable(getter):
            raw = getter(tenant_id, service_id) or {}

        unit_hours = dict(const.EMPLOYEE_COST_UNIT_HOURS_DEFAULT)
        configured_hours = raw.get(const.KEY_EMPLOYEE_SYNC_COST_UNIT_HOURS) or {}
        if isinstance(configured_hours, dict):
            for unit, hours in configured_hours.items():
                try:
                    value = Decimal(str(hours))
                except (InvalidOperation, TypeError, ValueError):
                    continue
                if unit in const.EMPLOYEE_COST_UNITS_ALLOWED and value > 0:
                    unit_hours[unit] = float(value)

        qbo_field = self._clean(
            raw.get(const.KEY_EMPLOYEE_SYNC_COST_QBO_FIELD)
            or const.EMPLOYEE_COST_QBO_FIELD_COST_RATE
        )
        if qbo_field not in const.EMPLOYEE_COST_QBO_FIELDS_ALLOWED:
            qbo_field = const.EMPLOYEE_COST_QBO_FIELD_COST_RATE

        return {
            const.KEY_EMPLOYEE_SYNC_COST_ENABLED: bool(
                raw.get(const.KEY_EMPLOYEE_SYNC_COST_ENABLED, True)
            ),
            const.KEY_EMPLOYEE_SYNC_COST_QBO_FIELD: qbo_field,
            const.KEY_EMPLOYEE_SYNC_COST_UNIT_HOURS: unit_hours,
        }

    def _qbo_hourly_cost_from_resource(
        self,
        resource: Optional[Dict[str, Any]],
        *,
        unit_hours: Optional[Dict[str, Any]] = None,
    ) -> Optional[float]:
        """Convert the FIELDCRAFT resource cost into an hourly QuickBooks employee rate."""
        if not isinstance(resource, dict):
            return None

        raw_cost = resource.get("cost")
        if raw_cost in (None, ""):
            return None

        try:
            cost = Decimal(str(raw_cost))
        except (InvalidOperation, ValueError, TypeError):
            return None

        if cost <= 0:
            return None

        unit = self._clean(resource.get("unit")).lower() or const.EMPLOYEE_COST_UNIT_HOURLY
        hours_by_unit = unit_hours or const.EMPLOYEE_COST_UNIT_HOURS_DEFAULT
        hours = Decimal(str(hours_by_unit.get(unit, 1)))
        if hours <= 0:
            hours = Decimal("1")

        hourly_rate = (cost / hours).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return float(hourly_rate)

    def _qbo_bill_rate_from_resource(self, resource: Optional[Dict[str, Any]]) -> Optional[float]:
        """Backward-compatible wrapper for existing tests and callers."""
        return self._qbo_hourly_cost_from_resource(resource)

    def _qbo_date(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()

        raw = self._clean(value)
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            try:
                return date.fromisoformat(raw[:10]).isoformat()
            except ValueError:
                return None

    def _employee_hire_date(self, employee: Dict[str, Any]) -> Any:
        return (
            (employee.get("user_status") or {}).get("hired_date")
            or (employee.get("personal_additional_info") or {}).get("hire_date")
            or employee.get("hired_date")
            or employee.get("hiredDate")
        )

    def build_qbo_employee_payload(
        self,
        employee: Dict[str, Any],
        display_name_override: Optional[str] = None,
        include_display_name: bool = True,
        cost_sync_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        display_name = self._clean(display_name_override) or self.build_display_name(employee)
        first_name = self._clean(employee.get("firstname") or employee.get("first_name") or employee.get("firstName"))
        middle_name = self._clean(employee.get("middlename") or employee.get("middle_name") or employee.get("middleName"))
        last_name = self._clean(employee.get("lastname") or employee.get("last_name") or employee.get("lastName"))
        email = self._clean(employee.get(const.KEY_EMAIL))
        phone = self._clean(employee.get("mobile_no") or employee.get(const.KEY_PHONE))
        alternate_phone = self._clean(employee.get("alternate_mobile_no") or employee.get("alternateMobileNo"))

        if self._is_blank(first_name):
            raise Exception("Employee first name is missing. Please add a first name before exporting to QuickBooks.")

        payload: Dict[str, Any] = {
            "GivenName": first_name,
            "Active": bool(employee.get("active")) and not bool(employee.get("deleted")) and not bool(employee.get("invalid")),
        }
        if include_display_name:
            payload[const.QBO_KEY_DISPLAY_NAME] = self._truncate_display_name(display_name)
        if middle_name:
            payload["MiddleName"] = middle_name
        if last_name:
            payload["FamilyName"] = last_name
        if email:
            payload["PrimaryEmailAddr"] = {"Address": email}
        if phone:
            payload["PrimaryPhone"] = {"FreeFormNumber": phone}
        if alternate_phone:
            payload["Mobile"] = {"FreeFormNumber": alternate_phone}

        qbo_address = self._build_qbo_address(employee.get("personal_additional_info") or {})
        if qbo_address:
            payload["PrimaryAddr"] = qbo_address

        birth_date = self._qbo_date(employee.get("birthdate"))
        if birth_date:
            payload["BirthDate"] = birth_date

        hired_date = self._qbo_date(self._employee_hire_date(employee))
        if hired_date:
            payload["HiredDate"] = hired_date

        sync_config = cost_sync_config or {
            const.KEY_EMPLOYEE_SYNC_COST_ENABLED: True,
            const.KEY_EMPLOYEE_SYNC_COST_QBO_FIELD: const.EMPLOYEE_COST_QBO_FIELD_COST_RATE,
            const.KEY_EMPLOYEE_SYNC_COST_UNIT_HOURS: const.EMPLOYEE_COST_UNIT_HOURS_DEFAULT,
        }
        if bool(sync_config.get(const.KEY_EMPLOYEE_SYNC_COST_ENABLED, True)):
            hourly_cost = self._qbo_hourly_cost_from_resource(
                employee.get("resource"),
                unit_hours=sync_config.get(const.KEY_EMPLOYEE_SYNC_COST_UNIT_HOURS),
            )
            qbo_cost_field = self._clean(
                sync_config.get(const.KEY_EMPLOYEE_SYNC_COST_QBO_FIELD)
                or const.EMPLOYEE_COST_QBO_FIELD_COST_RATE
            )
            if qbo_cost_field not in const.EMPLOYEE_COST_QBO_FIELDS_ALLOWED:
                qbo_cost_field = const.EMPLOYEE_COST_QBO_FIELD_COST_RATE
            if hourly_cost is not None:
                payload[qbo_cost_field] = hourly_cost
                payload["BillableTime"] = True

                # CostRate is the current source-of-truth field for FIELDCRAFT cost.
                # Keep the legacy BillRate value aligned as well so companies
                # that used the previous mapping do not retain a stale rate.
                if qbo_cost_field == const.EMPLOYEE_COST_QBO_FIELD_COST_RATE:
                    payload[const.EMPLOYEE_COST_QBO_FIELD_BILL_RATE] = hourly_cost

        return payload

    def _qbo_employee_inactive_reason(self, qbo_employee_id: str, qbo_employee: Optional[Dict[str, Any]]) -> str:
        qbo_employee = qbo_employee or {}
        return self._build_reason(
            "This employee was made inactive in QuickBooks. Make the employee active in QuickBooks, then run employee sync again.",
            code="EMPLOYEE_QBO_INACTIVE",
            qbo_employee_id=qbo_employee_id,
            qbo_display_name=qbo_employee.get(const.QBO_KEY_DISPLAY_NAME),
            action_required="Make the employee active in QuickBooks, then run employee sync again.",
        )

    def _is_qbo_employee_inactive(self, qbo_employee: Optional[Dict[str, Any]]) -> bool:
        if not qbo_employee:
            return False
        display_name = self._clean(qbo_employee.get(const.QBO_KEY_DISPLAY_NAME)).lower()
        return qbo_employee.get("Active") is False or "(deleted)" in display_name or "(deleted-" in display_name

    def _get_qbo_last_updated_at(self, qbo_employee: Dict[str, Any]) -> Optional[datetime]:
        metadata = qbo_employee.get("MetaData") or {}
        return self._parse_timestamp(metadata.get("LastUpdatedTime") or metadata.get("CreateTime"))

    def _parse_timestamp(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            raw = str(value).strip().replace("Z", "+00:00")
            if not raw:
                return None
            try:
                dt = datetime.fromisoformat(raw)
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()

    def _normalize_phone(self, value: Any) -> str:
        return re.sub(r"\D+", "", self._clean(value))

    def _truncate_display_name(self, value: str, max_length: int = 100) -> str:
        text = self._clean(value)
        return text if len(text) <= max_length else text[:max_length].rstrip()

    def _is_blank(self, value: Optional[str]) -> bool:
        return not self._clean(value)

    def _normalize_qbo_field_name(self, value: str) -> str:
        return re.sub(r"[_\s-]", "", self._clean(value)).lower()

    def _escape_qbo_literal(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    def _is_qbo_stale_reference_error(self, message: str) -> bool:
        text = self._clean(message).lower()
        return "[610]" in text or "object not found" in text or "made inactive" in text

    def _is_qbo_sync_token_error(self, message: str) -> bool:
        text = self._clean(message).lower()
        return "synctoken" in text or "stale object" in text or "[5010]" in text

    def _is_qbo_duplicate_name_error(self, message: str) -> bool:
        text = self._clean(message).lower()
        return "duplicate name" in text or "[6240]" in text or ("display name" in text and "exists" in text)
