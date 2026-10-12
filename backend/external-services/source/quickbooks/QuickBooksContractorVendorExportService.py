from __future__ import annotations

import json
import logging
import time

import requests
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils import QuickBooksErrorHandler as qbo_err
from source.Utils.QuickBooksErrorHandler import QuickBooksAPIError
from source.Utils import constants as const
from source.Utils.auditlog import safe_audit_log
from source.dal.EmployeeRepository import EmployeeRepository
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.dal.VendorExternalMapRepository import VendorExternalMapRepository
from source.quickbooks.QuickBooksAuthentication import QuickBooksOAuthClient
from source.quickbooks.QuickBooksVendorExportService import QuickBooksVendorExportService
from source.service.QuickBooksService import QuickBooksService

logger = logging.getLogger(__name__)


class QuickBooksContractorVendorExportService(QuickBooksVendorExportService):
    AMBIGUOUS_CREATE_LOOKUP_ATTEMPTS = 5
    AMBIGUOUS_CREATE_LOOKUP_DELAY_SECONDS = 1.0

    """Export FIELDCRAFT 1099/contractor resources as QuickBooks Vendors.

    FIELDCRAFT still treats these people as resources/users for scheduling and time
    logging. Only the accounting representation changes: QuickBooks receives a
    Vendor with Vendor1099=true, and user_account_external_map stores the QBO
    Vendor Id.
    """

    def __init__(self) -> None:
        super().__init__()
        self.qbo_repo = QuickBooksRepository()
        self.qbo_service = QuickBooksService()
        self.employee_repo = EmployeeRepository()
        self.vendor_map_repo = VendorExternalMapRepository()

    def _assert_mapping_realm(self, mapping: Optional[Dict[str, Any]], realm_id: str) -> None:
        if not isinstance(mapping, dict):
            return
        mapped_realm = self._clean(mapping.get("external_account_id"))
        if mapped_realm and mapped_realm != self._clean(realm_id):
            raise ValueError(
                "This contractor mapping belongs to a different QuickBooks company. Re-sync the contractor and retry."
            )

    @staticmethod
    def _build_reason(message: str, intuit_tid: Optional[str] = None, **extra: Any) -> str:
        data: Dict[str, Any] = {"message": message}
        if intuit_tid:
            data["intuit_tid"] = intuit_tid
        for key, value in extra.items():
            if value is not None:
                data[key] = value
        return json.dumps(data, default=str)

    def export_contractors_as_vendors(
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
            "[ContractorVendorExport] export_contractors_as_vendors called | tenant_id=%s service_id=%s limit=%s offset=%s",
            tenant_id,
            service_id,
            limit,
            offset,
        )
        safe_audit_log(
            tenant_id=tenant_id,
            action="contractor_vendor_export_started",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="started",
            resource_type="quickbooks_contractor_vendor",
        )
        audit_action = "contractor_vendor_export_failed"
        audit_status = "failed"
        audit_context: Optional[str] = None

        summary: Dict[str, Any] = {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": str(service_id),
            "requested": 0,
            "skippedAlreadyMapped": 0,
            "skippedQboNewer": 0,
            "mapped from QBO": 0,
            "createdInQbo": 0,
            "updatedInQbo": 0,
            "failed": 0,
            const.KEY_FAILURES: [],
            const.KEY_RESULTS: [],
        }

        try:
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}

            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = (connection.get(const.KEY_ENV) or "sandbox").lower()
            conn_status = (connection.get(const.KEY_STATUS) or const.STATUS_CONNECTED).lower()
            summary.update({"realmIdPresent": bool(realm_id), "env": env})

            if conn_status and conn_status != const.STATUS_CONNECTED:
                audit_action, audit_context = "contractor_vendor_export_aborted", f"not_connected status={conn_status}"
                return {const.KEY_OK: False, const.KEY_MESSAGE: f"QuickBooks is not connected (status={conn_status})"}
            if not refresh_token or not realm_id:
                audit_action, audit_context = "contractor_vendor_export_aborted", "missing_realmId_or_refreshToken"
                return {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: "QuickBooks not connected for this tenant (missing realmId/refreshToken).",
                }

            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)
            if not client_id or not client_secret:
                audit_action, audit_context = "contractor_vendor_export_aborted", "missing_credentials"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks client credentials missing (ClientId/ClientSecret)."}

            access_token = self._refresh_access_token(
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
                tenant_id=tenant_id,
                service_id=service_id,
                connection=connection,
            )
            base_url = self.get_qbo_api_base(env)

            candidate_kwargs = dict(
                tenant_id=tenant_id,
                service_id=service_id,
                limit=limit,
                offset=offset,
            )
            if after_mapping_id is not None:
                candidate_kwargs["after_mapping_id"] = after_mapping_id
            candidates = self.employee_repo.get_contractor_vendor_export_candidates(**candidate_kwargs)
            if not isinstance(candidates, list):
                candidates = []
            summary["requested"] = len(candidates)
            summary["lastMappingId"] = max(
                (int((item.get("mapping") or {}).get("id") or 0) for item in candidates),
                default=None,
            )

            if not candidates:
                summary[const.KEY_MESSAGE] = "No pending 1099 contractor vendor export candidates found."
                audit_action, audit_status, audit_context = "contractor_vendor_export_completed", "success", "no_candidates"
                return summary

            for item in candidates:
                self._process_candidate(
                    item=item,
                    tenant_id=tenant_id,
                    service_id=service_id,
                    realm_id=str(realm_id),
                    base_url=base_url,
                    access_token=access_token,
                    summary=summary,
                )

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
                    "No 1099 contractors were exported. Review the failed records, correct the listed issue, and retry."
                )
                audit_action = "contractor_vendor_export_failed"
                audit_status = "failed"
            elif failed:
                summary[const.KEY_OK] = True
                summary[const.KEY_MESSAGE] = (
                    f"1099 contractor export completed with {failed} failed record(s). Review those records and retry."
                )
                audit_action = "contractor_vendor_export_completed_with_errors"
                audit_status = "partial_success"
            else:
                summary[const.KEY_OK] = True
                summary[const.KEY_MESSAGE] = "1099 contractor export completed successfully."
                audit_action = "contractor_vendor_export_completed"
                audit_status = "success"
            audit_context = (
                f"created={summary['createdInQbo']};updated={summary['updatedInQbo']};"
                f"mapped={summary['mapped from QBO']};failed={summary['failed']}"
            )
            return summary
        except Exception as exc:
            audit_action = "contractor_vendor_export_failed"
            audit_status = "failed"
            audit_context = str(exc)[:128]
            logger.exception("[ContractorVendorExport] export failed")
            summary[const.KEY_OK] = False
            summary[const.KEY_MESSAGE] = "1099 contractor vendor export failed."
            summary[const.KEY_ERROR] = "Unexpected contractor export error."
            return summary
        finally:
            safe_audit_log(
                tenant_id=tenant_id,
                action=audit_action,
                user_id=object_id or None,
                email=email or None,
                user_role=user_role or None,
                status=audit_status,
                context=audit_context,
                resource_type="quickbooks_contractor_vendor",
            )

    def _process_candidate(
        self,
        *,
        item: Dict[str, Any],
        tenant_id: str,
        service_id: str,
        realm_id: str,
        base_url: str,
        access_token: str,
        summary: Dict[str, Any],
    ) -> None:
        employee = item.get("employee") or {}
        mapping = item.get("mapping") or {}
        employee_id = int(employee.get("id") or 0)
        mapping_id = int(mapping.get("id") or 0)
        vendor_projection = self._contractor_vendor_projection(employee)
        display_name = self.resolve_vendor_create_display_name(vendor_projection)
        detail: Dict[str, Any] = {
            const.KEY_FIELDCRAFT_EMPLOYEE_ID: employee_id,
            const.KEY_DISPLAY_NAME: display_name,
            const.KEY_EMAIL: employee.get(const.KEY_EMAIL),
            const.KEY_STATUS: None,
            const.KEY_ACTION: None,
            const.KEY_QBO_VENDOR_ID: mapping.get("external_vendor_id") or None,
            const.KEY_QBO_WORKER_ENTITY_TYPE: const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
            const.KEY_SYNC_TOKEN: mapping.get("sync_token"),
            const.KEY_REASON: None,
            const.KEY_ERROR: None,
        }

        try:
            if not mapping_id:
                raise Exception("Missing user account external map row for contractor vendor export candidate")
            if self._is_blank(display_name):
                raise Exception("Contractor vendor display name is missing. Please add a name before exporting to QuickBooks.")
            if not self._clean(employee.get("firstname") or employee.get("first_name") or employee.get("firstName")):
                raise Exception("Contractor first name is missing. Please add a first name before exporting to QuickBooks.")

            repair_mapping = getattr(
                self.employee_repo,
                "repair_pending_mapping_identity",
                None,
            )
            if callable(repair_mapping):
                repaired_mapping = repair_mapping(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    mapping_id=mapping_id,
                    user_account_id=employee_id,
                    expected_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
                )
                if isinstance(repaired_mapping, dict) and repaired_mapping:
                    mapping = repaired_mapping
            current_status = self._clean(mapping.get("sync_status")).upper()
            external_vendor_id = self._clean(mapping.get("external_vendor_id"))
            detail[const.KEY_QBO_VENDOR_ID] = external_vendor_id or None
            detail[const.KEY_SYNC_TOKEN] = mapping.get("sync_token")
            self._assert_mapping_realm(mapping, realm_id)
            if external_vendor_id and current_status not in (
                self.employee_repo.STATUS_PENDING_PUSH,
                self.employee_repo.STATUS_FAILED,
                self.employee_repo.STATUS_SYNC_FAILED,
                "",
            ):
                summary["skippedAlreadyMapped"] += 1
                detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "alreadyMapped"})
                summary[const.KEY_RESULTS].append(detail)
                return

            stale_mapping_detected = False
            if external_vendor_id:
                try:
                    self._assert_external_vendor_is_not_mapped_elsewhere(
                        tenant_id=tenant_id,
                        service_id=service_id,
                        realm_id=realm_id,
                        qbo_vendor_id=external_vendor_id,
                        user_account_id=employee_id,
                    )
                    qbo_current = self.get_vendor_in_qbo(
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=realm_id,
                        vendor_id=external_vendor_id,
                    )
                    if self._is_qbo_vendor_inactive(qbo_current) and bool(employee.get("active")):
                        raise Exception(
                            "This contractor vendor is inactive in QuickBooks. Make the vendor active in QuickBooks, then run worker sync again."
                        )

                    stored_sync_token = self._clean(mapping.get("sync_token")) or None
                    qbo_current_sync_token = self._clean(qbo_current.get(const.QBO_KEY_SYNC_TOKEN)) or None
                    qbo_last_updated_at = self._get_qbo_last_updated_at(qbo_current)
                    fieldcraft_updated_at = self.employee_repo.get_employee_latest_local_timestamp(employee)
                    qbo_changed_independently = bool(stored_sync_token and qbo_current_sync_token and stored_sync_token != qbo_current_sync_token)
                    if not qbo_changed_independently:
                        qbo_changed_independently = bool(qbo_last_updated_at and fieldcraft_updated_at and qbo_last_updated_at > fieldcraft_updated_at)

                    if qbo_changed_independently and qbo_last_updated_at and fieldcraft_updated_at and qbo_last_updated_at > fieldcraft_updated_at:
                        reason = self._build_reason(
                            "This 1099 contractor is already linked to QuickBooks, and QuickBooks has the latest version. "
                            "No export was needed; employee sync will keep the FIELDCRAFT record aligned.",
                            code="CONTRACTOR_VENDOR_QBO_NEWER",
                            qbo_updated_at=qbo_last_updated_at.isoformat(),
                            fieldcraft_updated_at=fieldcraft_updated_at.isoformat(),
                        )
                        self.employee_repo.mark_mapping_synced(
                            tenant_id=tenant_id,
                            mapping_id=mapping_id,
                            external_vendor_id=external_vendor_id,
                            external_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
                            external_account_id=realm_id,
                            sync_token=qbo_current_sync_token,
                            reason=reason,
                            external_display_name=self._qbo_display_name(qbo_current, display_name),
                        )
                        summary["skippedQboNewer"] += 1
                        detail.update({
                            const.KEY_STATUS: "skipped",
                            const.KEY_ACTION: "skippedQboNewer",
                            const.KEY_QBO_VENDOR_ID: external_vendor_id,
                            const.KEY_SYNC_TOKEN: qbo_current_sync_token,
                            const.KEY_REASON: reason,
                        })
                        summary[const.KEY_RESULTS].append(detail)
                        return

                    update_payload = self.build_qbo_contractor_vendor_payload(vendor_projection, for_create=False)
                    updated, omitted_qbo_fields = self._update_contractor_vendor_in_qbo(
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=realm_id,
                        vendor_id=external_vendor_id,
                        payload=update_payload,
                        sync_token=qbo_current_sync_token or stored_sync_token,
                    )
                    updated_id = self._clean((updated or {}).get(const.QBO_KEY_ID)) or external_vendor_id
                    updated_token = self._clean((updated or {}).get(const.QBO_KEY_SYNC_TOKEN)) or None
                    reason = self._build_reason(
                        "1099 contractor vendor version exported to QuickBooks",
                        code="CONTRACTOR_VENDOR_UPDATED_IN_QBO",
                        omitted_qbo_fields=omitted_qbo_fields or None,
                    )
                    self.employee_repo.mark_mapping_synced(
                        tenant_id=tenant_id,
                        mapping_id=mapping_id,
                        external_vendor_id=updated_id,
                        external_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
                        external_account_id=realm_id,
                        sync_token=updated_token,
                        reason=reason,
                        external_display_name=self._qbo_display_name(updated, display_name),
                    )
                    summary["updatedInQbo"] += 1
                    detail.update({
                        const.KEY_STATUS: "success",
                        const.KEY_ACTION: "updated in QBO",
                        const.KEY_QBO_VENDOR_ID: updated_id,
                        const.KEY_SYNC_TOKEN: updated_token,
                        const.KEY_REASON: reason,
                    })
                    summary[const.KEY_RESULTS].append(detail)
                    return
                except Exception as exc:
                    if self._is_qbo_stale_reference_error(str(exc)):
                        stale_mapping_detected = True
                        external_vendor_id = ""
                    else:
                        raise

            qbo_candidates = self.find_vendor_candidates_in_qbo(
                access_token=access_token,
                base_url=base_url,
                realm_id=realm_id,
                vendor=vendor_projection,
            )
            matched_vendor = qbo_candidates[0] if len(qbo_candidates) == 1 else None
            if len(qbo_candidates) > 1:
                raise Exception(
                    "Multiple QuickBooks vendors matched this 1099 contractor by display name. "
                    "Please resolve the duplicate vendors in QuickBooks, then run worker sync again."
                )

            if matched_vendor and matched_vendor.get(const.QBO_KEY_ID):
                found_id = self._clean(matched_vendor.get(const.QBO_KEY_ID))
                self._assert_external_vendor_is_not_mapped_elsewhere(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    realm_id=realm_id,
                    qbo_vendor_id=found_id,
                    user_account_id=employee_id,
                )
                found_token = self._clean(matched_vendor.get(const.QBO_KEY_SYNC_TOKEN)) or None
                reason = self._build_reason(
                    "1099 contractor matched to existing QuickBooks Vendor and mapping synced",
                    code="CONTRACTOR_VENDOR_MAPPED_TO_EXISTING_QBO",
                )
                self.employee_repo.claim_external_vendor_mapping(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    user_account_id=employee_id,
                    mapping_id=mapping_id,
                    external_vendor_id=found_id,
                    external_account_id=realm_id,
                    sync_token=found_token,
                    reason=reason,
                    external_display_name=self._qbo_display_name(matched_vendor, display_name),
                )
                summary["mapped from QBO"] += 1
                detail.update({
                    const.KEY_STATUS: "success",
                    const.KEY_ACTION: "mapped from QBO",
                    const.KEY_QBO_VENDOR_ID: found_id,
                    const.KEY_SYNC_TOKEN: found_token,
                    const.KEY_REASON: reason,
                })
                summary[const.KEY_RESULTS].append(detail)
                return

            create_payload = self.build_qbo_contractor_vendor_payload(vendor_projection, for_create=True)
            create_reason = "1099 contractor vendor created in QuickBooks and mapping synced"
            if stale_mapping_detected:
                create_reason = "1099 contractor vendor created in QuickBooks after stale mapped vendor reference was detected"
            try:
                created, omitted_qbo_fields = self._create_contractor_vendor_in_qbo(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=realm_id,
                    payload=create_payload,
                )
            except Exception as exc:
                if self._is_ambiguous_create_failure(exc):
                    created = self._recover_vendor_after_ambiguous_create(
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=realm_id,
                        display_name=self._clean(create_payload.get(const.QBO_KEY_DISPLAY_NAME)),
                    )
                    omitted_qbo_fields = []
                    create_reason = (
                        "1099 contractor create response timed out, but the created QuickBooks Vendor "
                        "was safely recovered and mapped"
                    )
                elif self._is_qbo_duplicate_name_error(str(exc)):
                    unique_display_name = self.build_unique_display_name(vendor_projection)
                    create_payload = self.build_qbo_contractor_vendor_payload(
                        vendor_projection,
                        display_name_override=unique_display_name,
                        for_create=True,
                    )
                    try:
                        created, omitted_qbo_fields = self._create_contractor_vendor_in_qbo(
                            access_token=access_token,
                            base_url=base_url,
                            realm_id=realm_id,
                            payload=create_payload,
                        )
                    except Exception as retry_exc:
                        if not self._is_ambiguous_create_failure(retry_exc):
                            raise
                        created = self._recover_vendor_after_ambiguous_create(
                            access_token=access_token,
                            base_url=base_url,
                            realm_id=realm_id,
                            display_name=self._clean(create_payload.get(const.QBO_KEY_DISPLAY_NAME)),
                        )
                        omitted_qbo_fields = []
                        create_reason = (
                            "1099 contractor create response timed out, but the uniquely named QuickBooks Vendor "
                            "was safely recovered and mapped"
                        )
                    else:
                        create_reason = (
                            "1099 contractor vendor created in QuickBooks with a unique DisplayName "
                            "to avoid duplicate name conflict"
                        )
                else:
                    raise

            created_id = self._clean((created or {}).get(const.QBO_KEY_ID))
            if not created_id:
                raise Exception("QuickBooks created no usable Vendor ID for this contractor.")
            created_token = self._clean((created or {}).get(const.QBO_KEY_SYNC_TOKEN)) or None
            reason = self._build_reason(
                create_reason,
                code="CONTRACTOR_VENDOR_CREATED_IN_QBO",
                omitted_qbo_fields=omitted_qbo_fields or None,
            )
            self.employee_repo.claim_external_vendor_mapping(
                tenant_id=tenant_id,
                service_id=service_id,
                user_account_id=employee_id,
                mapping_id=mapping_id,
                external_vendor_id=created_id,
                external_account_id=realm_id,
                sync_token=created_token,
                reason=reason,
                external_display_name=self._qbo_display_name(created, create_payload.get(const.QBO_KEY_DISPLAY_NAME)),
            )
            summary["createdInQbo"] += 1
            detail.update({
                const.KEY_STATUS: "success",
                const.KEY_ACTION: "created in QBO",
                const.KEY_QBO_VENDOR_ID: created_id,
                const.KEY_SYNC_TOKEN: created_token,
                const.KEY_REASON: reason,
            })
            summary[const.KEY_RESULTS].append(detail)
        except Exception as exc:
            intuit_tid = exc.intuit_tid if isinstance(exc, QuickBooksAPIError) else None
            message = exc.user_message if isinstance(exc, QuickBooksAPIError) else str(exc)
            reason = self._build_reason(message, intuit_tid=intuit_tid, code="CONTRACTOR_VENDOR_EXPORT_FAILED")
            if mapping_id:
                self.employee_repo.mark_mapping_failed(
                    tenant_id=tenant_id,
                    mapping_id=mapping_id,
                    reason=reason,
                    external_display_name=display_name,
                )
            summary["failed"] += 1
            failure = {
                const.KEY_FIELDCRAFT_EMPLOYEE_ID: employee_id,
                const.KEY_QBO_VENDOR_ID: external_vendor_id or mapping.get("external_vendor_id") or None,
                const.KEY_ERROR: message,
            }
            qbo_api.append_failure(summary, failure)
            detail.update({
                const.KEY_STATUS: "failed",
                const.KEY_ACTION: "error",
                const.KEY_ERROR: message,
                const.KEY_REASON: reason,
            })
            summary[const.KEY_RESULTS].append(detail)

    def _is_ambiguous_create_failure(self, exc: Exception) -> bool:
        return isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError))

    def _recover_vendor_after_ambiguous_create(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        display_name: str,
    ) -> Dict[str, Any]:
        display_name = self._clean(display_name)
        if not display_name:
            raise RuntimeError(
                "QuickBooks did not return the contractor Vendor create response, and no DisplayName was available for safe recovery."
            )

        last_error: Optional[Exception] = None
        for attempt in range(self.AMBIGUOUS_CREATE_LOOKUP_ATTEMPTS):
            if attempt and self.AMBIGUOUS_CREATE_LOOKUP_DELAY_SECONDS > 0:
                time.sleep(self.AMBIGUOUS_CREATE_LOOKUP_DELAY_SECONDS * attempt)
            try:
                candidates = self._query_vendors_by_field(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=realm_id,
                    field_name=const.QBO_KEY_DISPLAY_NAME,
                    field_value=display_name,
                )
            except Exception as lookup_exc:
                last_error = lookup_exc
                continue
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                raise RuntimeError(
                    "The QuickBooks contractor Vendor create result was uncertain and multiple matching Vendors were found. "
                    "No mapping was changed. Review the duplicate Vendors in QuickBooks before retrying."
                )

        message = (
            "QuickBooks did not return the contractor Vendor create response, and the Vendor could not be safely located afterward. "
            "The create request was not retried because that could create a duplicate. Check QuickBooks, then run worker sync again."
        )
        if last_error:
            raise RuntimeError(message) from last_error
        raise RuntimeError(message)

    def _create_contractor_vendor_in_qbo(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        payload: Dict[str, Any],
    ) -> tuple[Dict[str, Any], List[str]]:
        last_error: Optional[Exception] = None
        for retry_payload, omitted_fields in self._contractor_vendor_payload_retry_variants(payload):
            try:
                return self.create_vendor_in_qbo(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=realm_id,
                    payload=retry_payload,
                ), omitted_fields
            except Exception as exc:
                last_error = exc
                if not self._is_qbo_unsupported_property_error(str(exc)):
                    raise
                logger.warning(
                    "[ContractorVendorExport] QBO rejected contractor vendor create payload as unsupported | omitted_fields=%s error=%s",
                    omitted_fields,
                    exc,
                )
                continue
        if last_error:
            raise Exception(
                "QuickBooks rejected every 1099 Vendor create payload, including the minimal DisplayName + Vendor1099 payload. "
                "The vendor was not marked synced because 1099 tracking could not be guaranteed."
            ) from last_error
        raise Exception("QBO contractor vendor create failed before sending a request")

    def _update_contractor_vendor_in_qbo(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        vendor_id: str,
        payload: Dict[str, Any],
        sync_token: Optional[str],
    ) -> tuple[Dict[str, Any], List[str]]:
        last_error: Optional[Exception] = None
        for retry_payload, omitted_fields in self._contractor_vendor_payload_retry_variants(payload):
            try:
                return self.update_vendor_in_qbo(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=realm_id,
                    vendor_id=vendor_id,
                    payload=retry_payload,
                    sync_token=sync_token,
                ), omitted_fields
            except Exception as exc:
                last_error = exc
                if not self._is_qbo_unsupported_property_error(str(exc)):
                    raise
                logger.warning(
                    "[ContractorVendorExport] QBO rejected contractor vendor update payload as unsupported | vendor_id=%s omitted_fields=%s error=%s",
                    vendor_id,
                    omitted_fields,
                    exc,
                )
                continue
        if last_error:
            raise Exception(
                "QuickBooks rejected every 1099 Vendor update payload, including the minimal DisplayName + Vendor1099 payload. "
                "The worker mapping was not marked synced because 1099 tracking could not be guaranteed."
            ) from last_error
        raise Exception("QBO contractor vendor update failed before sending a request")

    def _contractor_vendor_payload_retry_variants(self, payload: Dict[str, Any]) -> List[tuple[Dict[str, Any], List[str]]]:
        """Return safe retry variants for QBO contractor vendor writes.

        QBO sometimes returns only "Request has invalid or unsupported property"
        without naming the field. The first request keeps the rich contractor
        payload. Retries then remove optional fields in groups, and the final
        retry sends the smallest valid Vendor create/update body so a demo or
        production sync is not blocked by one optional QBO field.
        """
        base = dict(payload or {})
        variants: List[tuple[Dict[str, Any], List[str]]] = []

        def add_variant(remove_fields: List[str]) -> None:
            body = dict(base)
            omitted: List[str] = []
            for field in remove_fields:
                if field in body:
                    body.pop(field, None)
                    omitted.append(field)
            signature = json.dumps(body, sort_keys=True, default=str)
            for existing, _ in variants:
                if json.dumps(existing, sort_keys=True, default=str) == signature:
                    return
            variants.append((body, omitted))

        def add_minimal_variant(keep_fields: List[str]) -> None:
            body = {field: base[field] for field in keep_fields if field in base and base[field] not in (None, "")}
            omitted = [field for field in base.keys() if field not in body]
            if not body.get(const.QBO_KEY_DISPLAY_NAME):
                return
            signature = json.dumps(body, sort_keys=True, default=str)
            for existing, _ in variants:
                if json.dumps(existing, sort_keys=True, default=str) == signature:
                    return
            variants.append((body, omitted))

        # Vendor1099 is the core accounting requirement for this flow. Keep it
        # in every retry variant so a 1099 FIELDCRAFT worker is never marked synced as a
        # plain QuickBooks Vendor. The field most commonly rejected on Vendor
        # create is Active; contact/address fields can also be rejected depending
        # on company settings/data. Remove those optional fields first, while
        # preserving Vendor1099.
        optional_contact_fields = ["Notes", "AcctNum", "WebAddr", "BillAddr", "PrimaryPhone", "PrimaryEmailAddr"]
        optional_name_fields = ["CompanyName", "PrintOnCheckName"]

        add_variant([])
        add_variant(["Notes"])
        add_variant(["Active"])
        add_variant(["Active", "Notes"])
        add_variant(["Active"] + optional_contact_fields)
        add_variant(["Active"] + optional_contact_fields + optional_name_fields)
        add_minimal_variant([const.QBO_KEY_DISPLAY_NAME, "Vendor1099"])
        return variants

    def _is_qbo_unsupported_property_error(self, message: Any) -> bool:
        text = self._clean(message).lower()
        return "unsupported property" in text or "invalid or unsupported property" in text

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

    def _contractor_vendor_projection(self, employee: Dict[str, Any]) -> Dict[str, Any]:
        personal_info = employee.get("personal_additional_info") or {}
        display_name = self.build_contractor_display_name(employee)
        full_name = self._full_name(employee) or display_name
        return {
            "id": employee.get("id"),
            "display_name": display_name,
            "company_name": full_name,
            "check_name": full_name,
            const.KEY_EMAIL: self._clean(employee.get(const.KEY_EMAIL) or personal_info.get("email")),
            const.KEY_PHONE: self._clean(employee.get("mobile_no") or employee.get(const.KEY_PHONE)),
            "address": self._clean(personal_info.get("address") or personal_info.get("line1")),
            "city": self._clean(personal_info.get("city")),
            "state": self._clean(personal_info.get("state") or personal_info.get("country_subdivision_code")),
            "zip_code": self._clean(personal_info.get("zip_code") or personal_info.get("postal_code")),
            "country": self._clean(personal_info.get("country")),
            "description": "1099 contractor synced from FIELDCRAFT resource",
            "vendor_uid": employee.get("object_id"),
            "updated_at": employee.get("updated_at"),
        }

    def build_qbo_contractor_vendor_payload(
        self,
        vendor: Dict[str, Any],
        display_name_override: Optional[str] = None,
        *,
        for_create: bool = True,
    ) -> Dict[str, Any]:
        payload = self.build_qbo_vendor_payload(vendor, display_name_override=display_name_override, for_create=for_create)
        payload["Vendor1099"] = True
        payload["Active"] = True
        return payload

    def build_contractor_display_name(self, employee: Dict[str, Any]) -> str:
        base_name = self._full_name(employee) or "Contractor"
        phone = self._build_display_phone(employee)
        name = f"{base_name}-{phone}" if phone else base_name
        return self._truncate_display_name(name)

    def build_unique_display_name(self, vendor: Dict[str, Any]) -> str:
        base_name = self.resolve_vendor_create_display_name(vendor) or "Contractor"
        suffix_seed = self._clean(vendor.get("vendor_uid"))[:8] or self._clean(vendor.get("id"))
        return self._append_display_name_suffix(base_name, suffix_seed) if suffix_seed else self._truncate_display_name(base_name)

    def _append_display_name_suffix(self, base_name: str, suffix: str, max_length: int = 100) -> str:
        base_name = self._clean(base_name) or "Contractor"
        suffix = self._clean(suffix)
        if not suffix:
            return self._truncate_display_name(base_name, max_length)
        formatted_suffix = f"-{suffix}"
        available_base_length = max_length - len(formatted_suffix)
        if available_base_length <= 0:
            return self._truncate_display_name(f"{base_name}{formatted_suffix}", max_length)
        return f"{base_name[:available_base_length].rstrip()}{formatted_suffix}"

    def _full_name(self, employee: Dict[str, Any]) -> str:
        parts = [
            self._clean(employee.get("firstname") or employee.get("first_name") or employee.get("firstName")),
            self._clean(employee.get("middlename") or employee.get("middle_name") or employee.get("middleName")),
            self._clean(employee.get("lastname") or employee.get("last_name") or employee.get("lastName")),
        ]
        return " ".join(part for part in parts if part)

    def _build_display_phone(self, employee: Dict[str, Any]) -> str:
        raw_phone = (
            employee.get("mobile_no")
            or employee.get(const.KEY_PHONE)
            or employee.get("phone")
            or employee.get("alternate_mobile_no")
            or employee.get("alternateMobileNo")
            or ""
        )
        return "".join(ch for ch in str(raw_phone) if ch.isdigit())[:20]

    def _qbo_display_name(self, qbo_vendor: Optional[Dict[str, Any]], fallback: Optional[str] = None) -> Optional[str]:
        if isinstance(qbo_vendor, dict):
            display_name = self._clean(qbo_vendor.get(const.QBO_KEY_DISPLAY_NAME))
            if display_name:
                return display_name
        return self._clean(fallback) or None

    def _is_qbo_vendor_inactive(self, qbo_vendor: Optional[Dict[str, Any]]) -> bool:
        if not qbo_vendor:
            return False
        display_name = self._clean(qbo_vendor.get(const.QBO_KEY_DISPLAY_NAME)).lower()
        return qbo_vendor.get("Active") is False or "(deleted)" in display_name or "(deleted-" in display_name

    def _assert_external_vendor_is_not_mapped_elsewhere(
        self,
        *,
        tenant_id: str,
        service_id: str,
        realm_id: str,
        qbo_vendor_id: str,
        user_account_id: int,
    ) -> None:
        qbo_vendor_id = self._clean(qbo_vendor_id)
        if not qbo_vendor_id:
            return

        worker_mapping = self.employee_repo.get_by_external_vendor_id(
            tenant_id,
            service_id,
            qbo_vendor_id,
            external_account_id=realm_id,
        )
        if worker_mapping and int(worker_mapping.get("user_account_id") or 0) not in (0, int(user_account_id)):
            raise Exception(
                "This QuickBooks vendor is already linked to another FIELDCRAFT worker. "
                "Review the worker mapping before syncing this contractor."
            )

        vendor_mapping = self.vendor_map_repo.get_by_external_id(
            tenant_id=tenant_id,
            service_id=service_id,
            external_id=qbo_vendor_id,
        )
        if vendor_mapping:
            mapped_realm = self._clean(vendor_mapping.get("external_account_id"))
            if mapped_realm and mapped_realm != self._clean(realm_id):
                vendor_mapping = None
        if vendor_mapping and vendor_mapping.get(const.KEY_VENDOR_ID):
            raise Exception(
                "This QuickBooks vendor is already linked to a FIELDCRAFT vendor record. "
                "1099 worker mappings must stay separate from normal FIELDCRAFT vendor mappings."
            )

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
