from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from source.dal.CustomerRepository import CustomerRepository
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.service.QuickBooksService import QuickBooksService
from source.service.TenantApiClient import TenantApiClient
from source.dal.TenantUserAccountRepository import TenantUserAccountRepository
from source.dal.ScheduledJobRepository import ScheduledJobRepository
from source.Utils import constants as const
from source.quickbooks.QuickBooksAuthentication import QuickBooksOAuthClient
from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils.auditlog import safe_audit_log

logger = logging.getLogger(__name__)

AUDIT_ACTION_CUSTOMER_CREATED_IN_FIELDCRAFT = "customer_created_in_fieldcraft"
AUDIT_ACTION_CUSTOMER_UPDATED_IN_FIELDCRAFT = "customer_updated_in_fieldcraft"
AUDIT_ACTION_CUSTOMER_IMPORT_FAILED_PERSISTED = "customer_import_failed_persisted"
AUDIT_ACTION_CUSTOMER_SYNC_FAILED_PERSISTED = "customer_sync_failed_persisted"


class QuickBooksCustomerImportService:
    """QuickBooks -> FIELDCRAFT customer reconciliation against the new domain model.

    Notes:
      - mapping-first resolution
      - safe link/create for first-time external records
      - new internal version rows for external updates
      - old Tenant API coupling removed from the core customer sync path
    """

    REASON_EMAIL_CHANGE_NOT_ALLOWED = "CUSTOMER_EMAIL_CHANGE_NOT_ALLOWED"
    REASON_DUPLICATE_EMAIL_NOT_ALLOWED = "CUSTOMER_DUPLICATE_EMAIL_NOT_ALLOWED"
    REASON_MISSING_REQUIRED_FIELDS = "CUSTOMER_MISSING_REQUIRED_FIELDS"
    REASON_SUB_CUSTOMER_SKIPPED = "CUSTOMER_SUB_CUSTOMER_SKIPPED"

    def _audit_customer_event(
        self,
        *,
        tenant_id: str,
        action: str,
        status: str = "success",
        resource_id: Optional[str] = None,
        context: Optional[str] = None,
    ) -> None:
        safe_audit_log(
            tenant_id=tenant_id,
            action=action,
            status=status,
            context=context,
            resource_id=resource_id,
            resource_type="quickbooks_customer",
        )

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
    def _timestamp_for_reason(value: Optional[datetime]) -> Optional[str]:
        return value.isoformat() if isinstance(value, datetime) else None

    @staticmethod
    def _customer_additional_info_from_qbo(qbo_customer: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(qbo_customer, dict):
            return {}
        client_entity_id = str(qbo_customer.get(const.KEY_QBO_CLIENT_ENTITY_ID) or "").strip()
        return {const.KEY_QBO_CLIENT_ENTITY_ID: client_entity_id} if client_entity_id else {}

    def _mark_mapping_synced_from_qbo(
        self,
        mapping_id: int,
        external_customer_id: str,
        external_account_id: Optional[str],
        *,
        reason: str,
        qbo_customer: Optional[Dict[str, Any]],
    ) -> None:
        additional_info = self._customer_additional_info_from_qbo(qbo_customer)
        if additional_info:
            self.customer_repo.mark_mapping_synced(
                mapping_id,
                external_customer_id,
                external_account_id,
                reason=reason,
                additional_info=additional_info,
            )
            return
        self.customer_repo.mark_mapping_synced(mapping_id, external_customer_id, external_account_id, reason=reason)

    def _merge_mapping_additional_info_from_qbo(self, mapping_id: int, qbo_customer: Optional[Dict[str, Any]]) -> None:
        additional_info = self._customer_additional_info_from_qbo(qbo_customer)
        if additional_info:
            self.customer_repo.merge_mapping_additional_info(mapping_id, additional_info)

    def _local_newer_reason(self, scope: str, qbo_last_updated_at: Optional[datetime], local_latest_at: Optional[datetime]) -> str:
        message = (
            f"We kept the FIELDCRAFT {scope} because it has a newer update than QuickBooks. "
            "QuickBooks data was not imported for this customer."
        )
        return self._build_reason(
            message,
            code="CUSTOMER_FIELDCRAFT_NEWER",
            qbo_updated_at=self._timestamp_for_reason(qbo_last_updated_at),
            fieldcraft_updated_at=self._timestamp_for_reason(local_latest_at),
            action_required="Review the latest customer details in FIELDCRAFT. Update QuickBooks with the same details if QuickBooks should match FIELDCRAFT, then run sync again.",
        )

    def _sync_success_reason(self, message: str, code: str) -> str:
        return self._build_reason(message, code=code)

    def _qbo_customer_inactive_reason(self, qbo_customer_id: str, display_name: str) -> str:
        return self._build_reason(
            "This customer was made inactive in QuickBooks. Make the customer active in QuickBooks, then run customer sync again.",
            code="CUSTOMER_INACTIVE_IN_QBO",
            qbo_customer_id=qbo_customer_id or None,
            display_name=(display_name or None),
            action_required="Make the customer active in QuickBooks, then run customer sync again.",
        )

    def _mark_mapped_qbo_customer_inactive_failure(
        self,
        *,
        summary: Dict[str, Any],
        detail: Dict[str, Any],
        tenant_id: str,
        current_mapping: Dict[str, Any],
        qbo_customer_id: str,
        display_name: str,
    ) -> None:
        reason = self._qbo_customer_inactive_reason(qbo_customer_id, display_name)
        customer_id = current_mapping.get("customer_id")
        self.customer_repo.mark_mapping_failed(
            current_mapping["id"],
            status=self.customer_repo.STATUS_SYNC_FAILED,
            reason=reason,
        )
        self._audit_customer_event(
            tenant_id=tenant_id,
            action=AUDIT_ACTION_CUSTOMER_SYNC_FAILED_PERSISTED,
            status="failed",
            resource_id=str(qbo_customer_id or customer_id or ""),
            context=reason,
        )
        summary["failed"] += 1
        detail.update({
            const.KEY_STATUS: "failed",
            const.KEY_ACTION: "inactiveInQbo",
            const.KEY_FIELDCRAFT_CUSTOMER_ID: customer_id,
            const.KEY_REASON: reason,
            const.KEY_ERROR: "QuickBooks customer is inactive",
        })
        summary[const.KEY_FAILURES].append(detail.copy())
        summary[const.KEY_RESULTS].append(detail)

    @staticmethod
    def _normalize_email(value: Any) -> str:
        return str(value or "").strip().lower()

    def _reason_has_code(self, reason: Any, code: str) -> bool:
        if not reason or not code:
            return False
        try:
            parsed = json.loads(str(reason))
            return parsed.get("code") == code
        except Exception:
            return code in str(reason)

    def _customer_email_changed(self, current_customer: Optional[Dict[str, Any]], incoming_payload: Dict[str, Any]) -> bool:
        if not current_customer:
            return False
        local_email = self._normalize_email(current_customer.get(const.KEY_EMAIL))
        qbo_email = self._normalize_email(incoming_payload.get(const.KEY_EMAIL))
        return local_email != qbo_email

    def _email_change_reason(self, current_customer: Dict[str, Any], qbo_customer_id: str, qbo_email: str, display_name: str = "") -> Tuple[str, str]:
        local_email = str(current_customer.get(const.KEY_EMAIL) or "").strip()
        qbo_email = str(qbo_email or "").strip()
        customer_name = (
            str(display_name or "").strip()
            or " ".join(
                part for part in [
                    str(current_customer.get("first_name") or current_customer.get("firstname") or "").strip(),
                    str(current_customer.get("last_name") or current_customer.get("lastname") or "").strip(),
                ] if part
            )
            or "this customer"
        )
        message = (
            f"Email sync was skipped for {customer_name} because FIELDCRAFT locks customer email after creation. "
            f"FIELDCRAFT currently has {local_email or 'no email'}, while QuickBooks has {qbo_email or 'no email'}. "
            "Update the email in QuickBooks to match FIELDCRAFT, then run customer sync again."
        )
        return message, self._build_reason(
            message,
            code=self.REASON_EMAIL_CHANGE_NOT_ALLOWED,
            fieldcraft_email=local_email,
            qbo_email=qbo_email,
            qbo_customer_id=qbo_customer_id,
            qbo_customer_name=customer_name if customer_name != "this customer" else None,
            action_required="Update the email in QuickBooks to match the FIELDCRAFT customer email, then run customer sync again.",
        )

    def _duplicate_email_reason(
        self,
        *,
        email: str,
        qbo_customer_id: str,
        display_name: str,
        existing_customer_id: Optional[int] = None,
    ) -> Tuple[str, str]:
        customer_label = display_name or f"ID {qbo_customer_id}"
        if existing_customer_id:
            message = (
                f"Sync failed: QuickBooks customer {customer_label} uses {email or 'an email'} "
                "that already belongs to an existing FIELDCRAFT customer. "
                "Use a different email in QuickBooks, then run customer sync again."
            )
            action_required = "Use a different email in QuickBooks, then run customer sync again."
        else:
            message = (
                "This customer could not be synced, there is an issue with the customer account. "
                "Please contact FIELDCRAFT Support for assistance."
            )
            action_required = "Please contact FIELDCRAFT Support for assistance."

        return message, self._build_reason(
            message,
            code=self.REASON_DUPLICATE_EMAIL_NOT_ALLOWED,
            email=email,
            existing_customer_id=existing_customer_id,
            qbo_customer_id=qbo_customer_id,
            display_name=display_name,
            source="FIELDCRAFT_AD_EMAIL_VALIDATION",
            action_required=action_required,
        )

    def _recover_missing_customer_mapping(
        self,
        *,
        tenant_id: str,
        service_id: str,
        realm_id: str,
        qbo_customer_id: str,
        display_name: str,
        email: str,
        current_mapping: Optional[Dict[str, Any]],
        additional_info: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Recover only a missing customer mapping for an exact tenant email match.

        Normal mapped customer sync is intentionally left untouched. Recovery is
        considered only when QuickBooks has no bound mapping, or when a previous
        duplicate-email attempt left a detached IMPORT_FAILED row.
        """
        if dry_run:
            return current_mapping, False
        if current_mapping and current_mapping.get("customer_id"):
            return current_mapping, False
        if current_mapping and not self._reason_has_code(
            current_mapping.get("reason"),
            self.REASON_DUPLICATE_EMAIL_NOT_ALLOWED,
        ):
            return current_mapping, False

        email = self._normalize_email(email)
        if not email:
            return current_mapping, False

        customer_id = self.customer_repo.get_customer_id_by_email(
            tenant_id=tenant_id,
            email=email,
        )
        if not customer_id:
            return current_mapping, False

        customer_mapping = self.customer_repo.get_current_mapping_by_customer_id(
            tenant_id=tenant_id,
            service_id=service_id,
            customer_id=int(customer_id),
        )
        if customer_mapping:
            mapped_qbo_id = str(customer_mapping.get("external_customer_id") or "").strip()
            if mapped_qbo_id and mapped_qbo_id != str(qbo_customer_id or "").strip():
                logger.info(
                    "[CustomerImport] missing_mapping_recovery_skipped_existing_link | "
                    "tenant_id=%s customer_id=%s mapped_qbo_customer_id=%s incoming_qbo_customer_id=%s",
                    tenant_id,
                    customer_id,
                    mapped_qbo_id,
                    qbo_customer_id,
                )
                return current_mapping, False

            mapped_realm = str(customer_mapping.get("external_account_id") or "").strip()
            if mapped_realm and mapped_realm != str(realm_id or "").strip():
                logger.info(
                    "[CustomerImport] missing_mapping_recovery_skipped_realm_mismatch | "
                    "tenant_id=%s customer_id=%s",
                    tenant_id,
                    customer_id,
                )
                return current_mapping, False

        mapping_id = None
        if customer_mapping and customer_mapping.get("id"):
            mapping_id = int(customer_mapping["id"])
        elif current_mapping and current_mapping.get("id"):
            mapping_id = int(current_mapping["id"])

        reason = self._sync_success_reason(
            "This customer was already in FIELDCRAFT, so it was linked to the matching QuickBooks customer.",
            "CUSTOMER_MISSING_MAPPING_RECOVERED",
        )
        try:
            recovered = self.customer_repo.claim_external_customer_mapping(
                tenant_id=tenant_id,
                service_id=service_id,
                customer_id=int(customer_id),
                external_customer_id=qbo_customer_id,
                external_account_id=realm_id,
                reason=reason,
                additional_info=additional_info,
                mapping_id=mapping_id,
                allow_create=mapping_id is None,
            )
        except ValueError as exc:
            # A concurrent sync or an existing different mapping wins. Fall back
            # to the existing conflict path rather than changing normal behavior.
            logger.warning(
                "[CustomerImport] missing_mapping_recovery_not_safe | "
                "tenant_id=%s customer_id=%s qbo_customer_id=%s error=%s",
                tenant_id,
                customer_id,
                qbo_customer_id,
                exc,
            )
            return current_mapping, False

        logger.info(
            "[CustomerImport] missing_mapping_recovered | tenant_id=%s customer_id=%s "
            "qbo_customer_id=%s display_name=%s",
            tenant_id,
            customer_id,
            qbo_customer_id,
            display_name,
        )
        return recovered, True

    def _resolve_created_customer_id(
        self,
        *,
        tenant_id: str,
        email: str,
        attempts: int = 3,
        retry_delay_seconds: float = 0.1,
    ) -> Optional[int]:
        """Resolve a customer created by Tenant API across transaction/read-replica delay."""
        for attempt in range(max(1, attempts)):
            customer_id = self.customer_repo.get_customer_id_by_email(tenant_id, email)
            if customer_id:
                return int(customer_id)
            if attempt + 1 < attempts and retry_delay_seconds > 0:
                time.sleep(retry_delay_seconds)
        return None

    def _customer_email_exists_in_fieldcraft_or_ad(
        self,
        *,
        tenant_id: str,
        tenant_api_token: str,
        email: str,
    ) -> bool:
        email = (email or "").strip()
        if not email:
            return False
        return bool(self.tenant_client.customer_email_exists_in_fieldcraft_or_ad(tenant_api_token, tenant_id, email))

    def _existing_customer_detail_for_email(self, tenant_id: str, email: str) -> Dict[str, Any]:
        existing_customer_id = self.customer_repo.get_customer_id_by_email(tenant_id=tenant_id, email=email)
        return {"existingFieldCraftCustomerId": existing_customer_id} if existing_customer_id else {"emailExistsInFieldCraftOrAd": True}

    def _mark_customer_import_failed(
        self,
        *,
        summary: Dict[str, Any],
        detail: Dict[str, Any],
        tenant_id: str,
        service_id: str,
        realm_id: str,
        qbo_customer_id: str,
        display_name: str,
        payload: Optional[Dict[str, Any]],
        current_mapping: Optional[Dict[str, Any]],
        reason_text: str,
        reason_payload: str,
        action: str,
        customer_id: Optional[int] = None,
        qbo_only_import_failed: bool = False,
        sync_status: Optional[str] = None,
        extra_detail: Optional[Dict[str, Any]] = None,
        additional_info: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._persist_import_failed_mapping(
            tenant_id=tenant_id,
            service_id=service_id,
            realm_id=realm_id,
            qbo_customer_id=qbo_customer_id,
            payload=payload,
            current_mapping=current_mapping,
            reason=reason_payload,
            customer_id=customer_id,
            qbo_only_import_failed=qbo_only_import_failed,
            sync_status=sync_status,
            additional_info=additional_info,
        )
        summary["failed"] += 1
        qbo_api.append_failure(summary, {
            const.KEY_QBO_CUSTOMER_ID: qbo_customer_id,
            const.KEY_DISPLAY_NAME: display_name,
            const.KEY_ERROR: reason_text,
            const.KEY_DETAILS: reason_text,
            const.KEY_REASON: reason_payload,
        })
        detail_payload = {
            const.KEY_STATUS: "failed",
            const.KEY_ACTION: action,
            const.KEY_FIELDCRAFT_CUSTOMER_ID: None if qbo_only_import_failed else (customer_id or (current_mapping or {}).get("customer_id")),
            const.KEY_ERROR: reason_text,
            const.KEY_REASON: reason_payload,
        }
        if extra_detail:
            detail_payload.update(extra_detail)
        detail.update(detail_payload)
        summary[const.KEY_RESULTS].append(detail)

    def __init__(self):
        self.qbo_repo = QuickBooksRepository()
        self.customer_repo = CustomerRepository()
        self.qbo_service = QuickBooksService()
        self.tenant_client = TenantApiClient()
        self.scheduled_job_repo = ScheduledJobRepository()

    def import_customers(
        self,
        tenant_id: str,
        service_id: str,
        tenant_api_token: str = "",
        page_size: int = 100,
        start_position: int = 1,
        max_customers: Optional[int] = None,
        dry_run: bool = False,
        changed_since_override: Optional[Any] = None,
        force_full: bool = False,
    ) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": str(service_id),
            "processed": 0,
            "linkedExistingInFieldCraft": 0,
            "created in FIELDCRAFT": 0,
            "updatedInFIELDCRAFT": 0,
            "skippedAlreadyCurrent": 0,
            "skippedLocalNewer": 0,
            "skippedSubCustomers": 0,
            "skippedDeleted": 0,
            "failed": 0,
            const.KEY_FAILURES: [],
            const.KEY_RESULTS: [],
            "startedAt": datetime.now(timezone.utc).isoformat(),
        }
        try:
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id)
            self._tenant_id = tenant_id
            self._service_id = service_id
            self._connection = connection

            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = (connection.get(const.KEY_ENV) or "sandbox").lower()
            if not refresh_token or not realm_id:
                return {const.KEY_OK: False, const.KEY_ERROR: "QuickBooks not connected for this tenant (missing realmId/refreshToken)."}

            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id)
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)
            if not client_id or not client_secret:
                return {const.KEY_OK: False, const.KEY_ERROR: "QuickBooks client credentials missing (ClientId/ClientSecret)."}

            access_token = self._refresh_access_token(client_id, client_secret, refresh_token)
            base_url = self._get_qbo_api_base(env)

            changed_since, watermark_source = self._resolve_changed_since(
                tenant_id=tenant_id,
                service_id=service_id,
                changed_since_override=changed_since_override,
                force_full=force_full,
            )
            logger.debug(f"[ImportService] Customer import watermark | tenant_id={tenant_id} service_id={service_id} force_full={force_full} changed_since={changed_since} source={watermark_source}")
            use_cdc = changed_since is not None
            if use_cdc and self._is_cdc_too_old(changed_since):
                logger.info(f"[ImportService] CDC watermark too old, falling back to full import | tenant_id={tenant_id} changed_since={changed_since}")
                use_cdc = False
                watermark_source = None
                changed_since = None
            summary["mode"] = "CDC" if use_cdc else "FULL"
            summary["changedSince"] = changed_since.isoformat() if changed_since else None
            summary["watermarkSource"] = watermark_source
            logger.info(f"[ImportService] ── import mode={summary['mode']!r} | changed_since={summary['changedSince']!r} max_customers={max_customers!r} page_size={page_size}")

            pending_import_failed_ids = self.customer_repo.get_open_import_failed_external_customer_ids(tenant_id=tenant_id, service_id=service_id)
            if pending_import_failed_ids:
                logger.info(f"[ImportService] Retrying persistent IMPORT_FAILED customer(s) first: {pending_import_failed_ids}")
                retry_summary = self.import_customers_by_ids(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    customer_ids=pending_import_failed_ids,
                    tenant_api_token=tenant_api_token,
                )
                summary["persistentRetry"] = retry_summary

            if use_cdc:
                logger.debug(f"[ImportService] CDC fetch | realmId_present={bool(realm_id)!r} changed_since={changed_since}")
                qbo_customers, intuit_tid = self._fetch_qbo_customers_cdc(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=str(realm_id),
                    changed_since=changed_since,
                )
                if max_customers is not None:
                    qbo_customers = qbo_customers[: int(max_customers)]
                summary["intuitTid"] = intuit_tid
                logger.debug(f"[ImportService] CDC total customers to process={len(qbo_customers)} intuit_tid={intuit_tid!r}")
                for qbo_customer in qbo_customers:
                    summary["processed"] += 1
                    self._process_qbo_customer(
                        summary=summary,
                        tenant_id=tenant_id,
                        service_id=service_id,
                        realm_id=str(realm_id),
                        qbo_customer=qbo_customer,
                        dry_run=dry_run,
                        tenant_api_token=tenant_api_token,
                    )
                logger.info(f"[ImportService] CDC complete | processed={summary['processed']} created={summary.get('created in FIELDCRAFT', 0)} updated={summary.get('updatedInFIELDCRAFT', 0)} linked={summary.get('linkedExistingInFieldCraft', 0)} failed={summary.get('failed', 0)}")
                return summary

            pos = max(1, int(start_position or 1))
            processed = 0
            page_num = 0
            logger.info(f"[ImportService] FULL import starting | start_position={pos} max_customers={max_customers!r}")
            while True:
                if max_customers is not None and processed >= int(max_customers):
                    logger.info(f"[ImportService] FULL import reached max_customers={max_customers} — stopping")
                    break
                batch_size = int(page_size or 100)
                if max_customers is not None:
                    batch_size = min(batch_size, int(max_customers) - processed)
                page_num += 1
                logger.debug(f"[ImportService] FULL page={page_num} | fetching pos={pos} batch_size={batch_size}")
                qbo_customers, intuit_tid = self._fetch_qbo_customers(access_token, base_url, str(realm_id), pos, batch_size)
                if not qbo_customers:
                    logger.info(f"[ImportService] FULL page={page_num} returned 0 customers — stopping")
                    break
                summary["intuitTid"] = intuit_tid
                logger.debug(f"[ImportService] FULL page={page_num} returned {len(qbo_customers)} customers | intuit_tid={intuit_tid!r}")
                for qbo_customer in qbo_customers:
                    processed += 1
                    summary["processed"] += 1
                    self._process_qbo_customer(
                        summary=summary,
                        tenant_id=tenant_id,
                        service_id=service_id,
                        realm_id=str(realm_id),
                        qbo_customer=qbo_customer,
                        dry_run=dry_run,
                        tenant_api_token=tenant_api_token,
                    )
                pos += len(qbo_customers)
                if len(qbo_customers) < batch_size:
                    logger.info(f"[ImportService] FULL last page (got {len(qbo_customers)} < batch_size {batch_size}) — stopping")
                    break
            logger.info(f"[ImportService] FULL complete | pages={page_num} processed={summary['processed']} created={summary.get('created in FIELDCRAFT', 0)} updated={summary.get('updatedInFIELDCRAFT', 0)} linked={summary.get('linkedExistingInFieldCraft', 0)} failed={summary.get('failed', 0)}")
            return summary
        except Exception:
            logger.exception(f"[ImportService] FATAL ERROR in import_customers | tenant_id={tenant_id}")
            summary[const.KEY_OK] = False
            summary[const.KEY_ERROR] = "Unexpected internal error. See server logs for details."
            return summary
        finally:
            summary["finishedAt"] = datetime.now(timezone.utc).isoformat()

    def import_customers_by_ids(self, tenant_id: str, service_id: str, customer_ids: List[str], tenant_api_token: str = "") -> Dict[str, Any]:
        summary: Dict[str, Any] = {
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
            "failed": 0,
            const.KEY_FAILURES: [],
            const.KEY_RESULTS: [],
            "startedAt": datetime.now(timezone.utc).isoformat(),
        }
        try:
            logger.info(f"[ImportService] import_customers_by_ids | tenant_id={tenant_id!r} service_id={service_id!r} ids={customer_ids}")
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id)
            self._tenant_id = tenant_id
            self._service_id = service_id
            self._connection = connection

            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = (connection.get(const.KEY_ENV) or "sandbox").lower()
            logger.debug(f"[ImportService] import_customers_by_ids | realmId_present={bool(realm_id)!r} env={env!r}")
            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id)
            access_token = self._refresh_access_token(config.get(const.KEY_CLIENT_ID), config.get(const.KEY_CLIENT_SECRET), refresh_token)
            base_url = self._get_qbo_api_base(env)
            for customer_id in customer_ids or []:
                logger.debug(f"[ImportService] import_customers_by_ids | fetching qbo_customer_id={customer_id!r}")
                qbo_customer = self._fetch_qbo_customer_by_id(access_token, base_url, str(realm_id), str(customer_id))
                if not qbo_customer:
                    logger.info(f"[ImportService] import_customers_by_ids | qbo_customer_id={customer_id!r} not found — skipping")
                    continue
                summary["processed"] += 1
                self._process_qbo_customer(
                    summary=summary,
                    tenant_id=tenant_id,
                    service_id=service_id,
                    realm_id=str(realm_id),
                    qbo_customer=qbo_customer,
                    dry_run=False,
                    tenant_api_token=tenant_api_token,
                )
            logger.info(f"[ImportService] import_customers_by_ids complete | processed={summary['processed']} created={summary.get('created in FIELDCRAFT', 0)} updated={summary.get('updatedInFIELDCRAFT', 0)} failed={summary.get('failed', 0)}")
            return summary
        except Exception:
            logger.exception(f"[ImportService] FATAL ERROR in import_customers_by_ids | tenant_id={tenant_id}")
            summary[const.KEY_OK] = False
            summary[const.KEY_ERROR] = "Unexpected internal error. See server logs for details."
            return summary
        finally:
            summary["finishedAt"] = datetime.now(timezone.utc).isoformat()


    def _get_last_customer_module_sync_start(self, tenant_id: str, service_id: str) -> Optional[datetime]:
        return self.scheduled_job_repo.get_last_successful_module_sync_start(
            tenant_id=tenant_id,
            job_type="QBO_CUSTOMER_SYNC",
            service_id=str(service_id),
        )

    def _get_cdc_changed_since(self, tenant_id: str, service_id: str) -> Optional[datetime]:
        last_sync_start = self._get_last_customer_module_sync_start(tenant_id=tenant_id, service_id=service_id)
        if last_sync_start is None:
            return None
        if last_sync_start.tzinfo is None:
            last_sync_start = last_sync_start.replace(tzinfo=timezone.utc)
        # Small overlap to avoid boundary misses. Import is mapping-first and idempotent.
        return last_sync_start - timedelta(minutes=1)

    def _resolve_changed_since(
        self,
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
                return parsed, "override"

        module_changed_since = self._get_cdc_changed_since(tenant_id=tenant_id, service_id=service_id)
        if module_changed_since is not None:
            return module_changed_since, "lastSuccessfulCustomerModuleSyncStart"

        return None, None

    def _process_qbo_customer(
        self,
        *,
        summary: Dict[str, Any],
        tenant_id: str,
        service_id: str,
        realm_id: str,
        qbo_customer: Dict[str, Any],
        dry_run: bool,
        tenant_api_token: str = "",
    ) -> None:
        qbo_customer_id = str(qbo_customer.get(const.QBO_KEY_ID) or "").strip()
        display_name = (qbo_customer.get(const.QBO_KEY_DISPLAY_NAME) or "").strip()
        detail: Dict[str, Any] = {
            const.KEY_QBO_CUSTOMER_ID: qbo_customer_id,
            const.KEY_DISPLAY_NAME: display_name,
            const.KEY_STATUS: None,
            const.KEY_ACTION: None,
            const.KEY_FIELDCRAFT_CUSTOMER_ID: None,
            const.KEY_REASON: None,
            const.KEY_ERROR: None,
        }
        payload: Optional[Dict[str, Any]] = None
        current_mapping: Optional[Dict[str, Any]] = None
        customer_additional_info: Dict[str, Any] = {}
        try:
            payload = self._build_customer_payload_from_qbo(qbo_customer)
            customer_additional_info = self._customer_additional_info_from_qbo(qbo_customer)
            qbo_last_updated_at = self._get_qbo_last_updated_at(qbo_customer)
            logger.debug(f"[CustomerImport] customer.received | qbo_customer_id={qbo_customer_id!r} display_name={display_name!r} qbo_last_updated_at={qbo_last_updated_at}")

            if self._is_qbo_sub_customer(qbo_customer):
                reason = self._sub_customer_skipped_reason(qbo_customer)
                summary["skippedSubCustomers"] += 1
                detail.update({
                    const.KEY_STATUS: "skipped",
                    const.KEY_ACTION: "subCustomerSkipped",
                    const.KEY_REASON: reason,
                })
                summary[const.KEY_RESULTS].append(detail)
                logger.info(f"[CustomerImport] customer.skip_sub_customer | qbo_customer_id={qbo_customer_id!r} display_name={display_name!r}")
                return

            current_mapping = self.customer_repo.get_current_mapping_by_external_customer_id(
                tenant_id=tenant_id,
                service_id=service_id,
                external_customer_id=qbo_customer_id,
            )

            if self._is_deleted_qbo_customer(qbo_customer):
                if current_mapping and current_mapping.get("customer_id"):
                    logger.info(
                        f"[CustomerImport] customer.inactive_mapped_sync_failed | "
                        f"customer_id={current_mapping.get('customer_id')} qbo_customer_id={qbo_customer_id!r} display_name={display_name!r}"
                    )
                    self._mark_mapped_qbo_customer_inactive_failure(
                        summary=summary,
                        detail=detail,
                        tenant_id=tenant_id,
                        current_mapping=current_mapping,
                        qbo_customer_id=qbo_customer_id,
                        display_name=display_name,
                    )
                    return

                summary["skippedDeleted"] += 1
                reason = self._build_reason(
                    "This customer is deleted in QuickBooks, so it was skipped.",
                    code="CUSTOMER_DELETED_IN_QBO",
                    qbo_customer_id=qbo_customer_id or None,
                    display_name=display_name or None,
                )
                detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "deletedInQbo", const.KEY_REASON: reason})
                summary[const.KEY_RESULTS].append(detail)
                logger.info(f"[CustomerImport] customer.skip_deleted | qbo_customer_id={qbo_customer_id!r} display_name={display_name!r}")
                return
            current_mapping_is_import_failed = bool(current_mapping and current_mapping.get("sync_status") in (self.customer_repo.STATUS_IMPORT_FAILED, self.customer_repo.STATUS_SYNC_FAILED))

            current_mapping, mapping_recovered = self._recover_missing_customer_mapping(
                tenant_id=tenant_id,
                service_id=service_id,
                realm_id=realm_id,
                qbo_customer_id=qbo_customer_id,
                display_name=display_name,
                email=payload.get(const.KEY_EMAIL) or "",
                current_mapping=current_mapping,
                additional_info=customer_additional_info or None,
                dry_run=dry_run,
            )
            if mapping_recovered:
                current_mapping_is_import_failed = False
                summary["linkedExistingInFieldCraft"] += 1

            if current_mapping and not current_mapping_is_import_failed:
                current_customer = self.customer_repo.get_current_customer_by_id(current_mapping["customer_id"]) or self.customer_repo.get_customer_by_id(current_mapping["customer_id"])
                incoming_projection = payload
                personal_info = self._get_personal_additional_info(tenant_id, current_customer)
                local_latest_at = self.customer_repo.get_customer_latest_local_timestamp(current_customer, None, personal_info)
                customer_changed = bool(current_customer and self.customer_repo.sync_projection_changed(current_customer, incoming_projection))
                address_changed = self._qbo_address_differs_from_personal_info(qbo_customer, personal_info)
                logger.debug(f"[CustomerImport] customer.mapping_found | customer_id={current_mapping['customer_id']} local_latest_at={local_latest_at} customer_changed={customer_changed} address_changed={address_changed}")
                if current_customer and self._customer_email_changed(current_customer, incoming_projection):
                    reason_text, reason_payload = self._email_change_reason(current_customer, qbo_customer_id, incoming_projection.get(const.KEY_EMAIL) or "", display_name)
                    logger.info(f"[CustomerImport] customer.email_change_blocked | customer_id={current_mapping['customer_id']} qbo_customer_id={qbo_customer_id!r} reason={reason_text}")
                    self._mark_customer_import_failed(
                        summary=summary,
                        detail=detail,
                        tenant_id=tenant_id,
                        service_id=service_id,
                        realm_id=realm_id,
                        qbo_customer_id=qbo_customer_id,
                        display_name=display_name,
                        payload=payload,
                        current_mapping=current_mapping,
                        reason_text=reason_text,
                        reason_payload=reason_payload,
                        action="emailChangeNotAllowed",
                        customer_id=current_mapping.get("customer_id"),
                        sync_status=self.customer_repo.STATUS_SYNC_FAILED,
                        additional_info=customer_additional_info or None,
                    )
                    return
                if not customer_changed:
                    if address_changed and self._should_skip_import_update(qbo_last_updated_at, local_latest_at):
                        reason = self._local_newer_reason("address", qbo_last_updated_at, local_latest_at)
                        summary["skippedLocalNewer"] += 1
                        detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "localNewer", const.KEY_FIELDCRAFT_CUSTOMER_ID: current_mapping["customer_id"], const.KEY_REASON: reason})
                        self._merge_mapping_additional_info_from_qbo(current_mapping["id"], qbo_customer)
                        summary[const.KEY_RESULTS].append(detail)
                        logger.info(f"[CustomerImport] customer.skip_local_newer_address | customer_id={current_mapping['customer_id']} qbo_last_updated_at={qbo_last_updated_at} local_latest_at={local_latest_at}")
                        return
                    address_synced = self._sync_personal_additional_info_from_qbo(
                        tenant_id=tenant_id,
                        customer=current_customer,
                        qbo_customer=qbo_customer,
                        dry_run=dry_run,
                    )
                    if address_synced:
                        reason = self._sync_success_reason("Customer address was updated in FIELDCRAFT from QuickBooks.", "CUSTOMER_ADDRESS_IMPORTED_FROM_QBO")
                    else:
                        reason = self._sync_success_reason("Customer is already up to date in FIELDCRAFT and QuickBooks.", "CUSTOMER_ALREADY_CURRENT")
                    self._mark_mapping_synced_from_qbo(current_mapping["id"], qbo_customer_id, realm_id, reason=reason, qbo_customer=qbo_customer)
                    if address_synced:
                        self._audit_customer_event(
                            tenant_id=tenant_id,
                            action=AUDIT_ACTION_CUSTOMER_UPDATED_IN_FIELDCRAFT,
                            resource_id=str(current_mapping["customer_id"]),
                            context=f"qboCustomerId={qbo_customer_id}",
                        )
                        summary["updatedInFIELDCRAFT"] += 1
                        detail.update({const.KEY_STATUS: "success", const.KEY_ACTION: "updated address in FIELDCRAFT", const.KEY_FIELDCRAFT_CUSTOMER_ID: current_mapping["customer_id"], const.KEY_REASON: reason})
                    else:
                        summary["skippedAlreadyCurrent"] += 1
                        detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "alreadyCurrent", const.KEY_FIELDCRAFT_CUSTOMER_ID: current_mapping["customer_id"], const.KEY_REASON: reason})
                    summary[const.KEY_RESULTS].append(detail)
                    return
                if self._should_skip_import_update(qbo_last_updated_at, local_latest_at):
                    reason = self._local_newer_reason("customer", qbo_last_updated_at, local_latest_at)
                    summary["skippedLocalNewer"] += 1
                    detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "localNewer", const.KEY_FIELDCRAFT_CUSTOMER_ID: current_mapping["customer_id"], const.KEY_REASON: reason})
                    self._merge_mapping_additional_info_from_qbo(current_mapping["id"], qbo_customer)
                    summary[const.KEY_RESULTS].append(detail)
                    logger.info(f"[CustomerImport] customer.skip_local_newer | customer_id={current_mapping['customer_id']} qbo_last_updated_at={qbo_last_updated_at} local_latest_at={local_latest_at}")
                    return
                if dry_run:
                    reason = self._build_reason("Dry run enabled, so no customer changes were saved.", code="CUSTOMER_DRY_RUN")
                    detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "dryRun", const.KEY_FIELDCRAFT_CUSTOMER_ID: current_mapping["customer_id"], const.KEY_REASON: reason})
                    summary[const.KEY_RESULTS].append(detail)
                    return
                new_customer = self.customer_repo.create_customer_version_from_qbo(tenant_id=tenant_id, customer_id=current_mapping["customer_id"], qbo_customer=qbo_customer)
                self._sync_personal_additional_info_from_qbo(
                    tenant_id=tenant_id,
                    customer=new_customer,
                    qbo_customer=qbo_customer,
                    dry_run=False,
                )
                rotate_kwargs = {
                    "tenant_id": tenant_id,
                    "service_id": service_id,
                    "customer_id": new_customer["id"],
                    "external_customer_id": qbo_customer_id,
                    "external_account_id": realm_id,
                    "sync_status": self.customer_repo.STATUS_SYNCED,
                    "reason": self._sync_success_reason("Customer was updated in FIELDCRAFT from QuickBooks.", "CUSTOMER_IMPORTED_FROM_QBO"),
                }
                if customer_additional_info:
                    rotate_kwargs["additional_info"] = customer_additional_info
                self.customer_repo.rotate_mapping_for_customer(**rotate_kwargs)
                self._audit_customer_event(
                    tenant_id=tenant_id,
                    action=AUDIT_ACTION_CUSTOMER_UPDATED_IN_FIELDCRAFT,
                    resource_id=str(new_customer["id"]),
                    context=f"qboCustomerId={qbo_customer_id}",
                )
                summary["updatedInFIELDCRAFT"] += 1
                detail.update({const.KEY_STATUS: "success", const.KEY_ACTION: "updated in FIELDCRAFT", const.KEY_FIELDCRAFT_CUSTOMER_ID: new_customer["id"], const.KEY_REASON: self._sync_success_reason("Customer was updated in FIELDCRAFT from QuickBooks.", "CUSTOMER_IMPORTED_FROM_QBO")})
                summary[const.KEY_RESULTS].append(detail)
                return

            if current_mapping_is_import_failed:
                if self._reason_has_code(current_mapping.get("reason"), self.REASON_DUPLICATE_EMAIL_NOT_ALLOWED):
                    email_lookup = (payload.get(const.KEY_EMAIL) or "").strip()
                    if self._customer_email_exists_in_fieldcraft_or_ad(tenant_id=tenant_id, tenant_api_token=tenant_api_token, email=email_lookup):
                        reason_text, reason_payload = self._duplicate_email_reason(
                            email=email_lookup,
                            qbo_customer_id=qbo_customer_id,
                            display_name=display_name,
                            existing_customer_id=self.customer_repo.get_customer_id_by_email(tenant_id=tenant_id, email=email_lookup),
                        )
                        # This QBO customer only conflicts by email; it is not the existing FIELDCRAFT customer.
                        # Keep the failed import row detached so it does not shadow the synced customer mapping.
                        self._mark_customer_import_failed(
                            summary=summary,
                            detail=detail,
                            tenant_id=tenant_id,
                            service_id=service_id,
                            realm_id=realm_id,
                            qbo_customer_id=qbo_customer_id,
                            display_name=display_name,
                            payload=payload,
                            current_mapping=current_mapping,
                            reason_text=reason_text,
                            reason_payload=reason_payload,
                            action="duplicateEmailImportFailed",
                            qbo_only_import_failed=True,
                            extra_detail=self._existing_customer_detail_for_email(tenant_id, email_lookup),
                            additional_info=customer_additional_info or None,
                        )
                        return

                    # The duplicate email was fixed in QuickBooks. Close the failed row and
                    # continue through the normal new-customer import path.
                    self.customer_repo.close_mapping_as_invalid(current_mapping["id"])
                    current_mapping = None
                    current_mapping_is_import_failed = False

                elif self._reason_has_code(current_mapping.get("reason"), self.REASON_MISSING_REQUIRED_FIELDS):
                    ok_payload, skip_reason = self._is_payload_valid_for_tenant_api(payload)
                    if not ok_payload:
                        reason_text = f"QuickBooks customer {display_name or qbo_customer_id} cannot be imported: {skip_reason}"
                        reason_payload = self._build_reason(
                            reason_text,
                            code=self.REASON_MISSING_REQUIRED_FIELDS,
                            qbo_customer_id=qbo_customer_id,
                            display_name=display_name,
                            action_required="Add the missing required customer fields in QuickBooks, then run customer sync again.",
                        )
                        self._mark_customer_import_failed(
                            summary=summary,
                            detail=detail,
                            tenant_id=tenant_id,
                            service_id=service_id,
                            realm_id=realm_id,
                            qbo_customer_id=qbo_customer_id,
                            display_name=display_name,
                            payload=payload,
                            current_mapping=current_mapping,
                            reason_text=reason_text,
                            reason_payload=reason_payload,
                            action="missingRequired",
                            customer_id=current_mapping.get("customer_id"),
                            additional_info=customer_additional_info or None,
                        )
                        return

                    # The missing required fields were fixed in QuickBooks. Close the failed
                    # row and continue through the normal new-customer import path.
                    self.customer_repo.close_mapping_as_invalid(current_mapping["id"])
                    current_mapping = None
                    current_mapping_is_import_failed = False

                else:
                    tracked_customer = None
                    if current_mapping.get("customer_id") is not None:
                        tracked_customer = self.customer_repo.get_current_customer_by_id(current_mapping["customer_id"]) or self.customer_repo.get_customer_by_id(current_mapping["customer_id"])
                    if tracked_customer and self._customer_email_changed(tracked_customer, payload):
                        reason_text, reason_payload = self._email_change_reason(tracked_customer, qbo_customer_id, payload.get(const.KEY_EMAIL) or "", display_name)
                        self._mark_customer_import_failed(
                            summary=summary,
                            detail=detail,
                            tenant_id=tenant_id,
                            service_id=service_id,
                            realm_id=realm_id,
                            qbo_customer_id=qbo_customer_id,
                            display_name=display_name,
                            payload=payload,
                            current_mapping=current_mapping,
                            reason_text=reason_text,
                            reason_payload=reason_payload,
                            action="emailChangeNotAllowed",
                            customer_id=current_mapping.get("customer_id"),
                            sync_status=self.customer_repo.STATUS_SYNC_FAILED,
                            additional_info=customer_additional_info or None,
                        )
                        return
                    if current_mapping.get("customer_id") is None:
                        # Orphan IMPORT_FAILED row was fixed in QuickBooks. Close it and
                        # continue through the normal new-customer import path below.
                        self.customer_repo.close_mapping_as_invalid(current_mapping["id"])
                        current_mapping = None
                        current_mapping_is_import_failed = False
                    else:
                        if dry_run:
                            reason = self._build_reason("Dry run enabled, so the customer retry was checked but no changes were saved.", code="CUSTOMER_DRY_RUN")
                            detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "dryRun", const.KEY_FIELDCRAFT_CUSTOMER_ID: current_mapping["customer_id"], const.KEY_REASON: reason})
                            summary[const.KEY_RESULTS].append(detail)
                            return
                        personal_info = self._get_personal_additional_info(tenant_id, tracked_customer)
                        local_latest_at = self.customer_repo.get_customer_latest_local_timestamp(tracked_customer, None, personal_info)
                        customer_changed = bool(tracked_customer and self.customer_repo.sync_projection_changed(tracked_customer, payload))
                        address_changed = self._qbo_address_differs_from_personal_info(qbo_customer, personal_info)
                        if tracked_customer and not customer_changed:
                            if address_changed and self._should_skip_import_update(qbo_last_updated_at, local_latest_at):
                                reason = self._local_newer_reason("address", qbo_last_updated_at, local_latest_at)
                                summary["skippedLocalNewer"] += 1
                                detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "localNewer", const.KEY_FIELDCRAFT_CUSTOMER_ID: current_mapping["customer_id"], const.KEY_REASON: reason})
                                self._merge_mapping_additional_info_from_qbo(current_mapping["id"], qbo_customer)
                                summary[const.KEY_RESULTS].append(detail)
                                return
                            address_synced = self._sync_personal_additional_info_from_qbo(
                                tenant_id=tenant_id,
                                customer=tracked_customer,
                                qbo_customer=qbo_customer,
                                dry_run=False,
                            )
                            if address_synced:
                                reason = self._sync_success_reason("Previous customer sync issue was resolved, and the QuickBooks address was imported to FIELDCRAFT.", "CUSTOMER_IMPORT_RETRY_ADDRESS_IMPORTED")
                            else:
                                reason = self._sync_success_reason("Previous customer sync issue was resolved. No customer data changes were needed.", "CUSTOMER_IMPORT_RETRY_ALREADY_CURRENT")
                            self._mark_mapping_synced_from_qbo(current_mapping["id"], qbo_customer_id, realm_id, reason=reason, qbo_customer=qbo_customer)
                            if address_synced:
                                self._audit_customer_event(
                                    tenant_id=tenant_id,
                                    action=AUDIT_ACTION_CUSTOMER_UPDATED_IN_FIELDCRAFT,
                                    resource_id=str(current_mapping["customer_id"]),
                                    context=f"qboCustomerId={qbo_customer_id}",
                                )
                                summary["updatedInFIELDCRAFT"] += 1
                                detail.update({const.KEY_STATUS: "success", const.KEY_ACTION: "resolved import failure", const.KEY_FIELDCRAFT_CUSTOMER_ID: current_mapping["customer_id"], const.KEY_REASON: reason})
                            else:
                                detail.update({const.KEY_STATUS: "success", const.KEY_ACTION: "resolved import failure", const.KEY_FIELDCRAFT_CUSTOMER_ID: current_mapping["customer_id"], const.KEY_REASON: reason})
                            summary[const.KEY_RESULTS].append(detail)
                            return
                        if tracked_customer and self._should_skip_import_update(qbo_last_updated_at, local_latest_at):
                            reason = self._local_newer_reason("customer", qbo_last_updated_at, local_latest_at)
                            summary["skippedLocalNewer"] += 1
                            detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "localNewer", const.KEY_FIELDCRAFT_CUSTOMER_ID: current_mapping["customer_id"], const.KEY_REASON: reason})
                            self._merge_mapping_additional_info_from_qbo(current_mapping["id"], qbo_customer)
                            summary[const.KEY_RESULTS].append(detail)
                            return
                        target_customer = current_mapping["customer_id"]
                        if tracked_customer:
                            new_customer = self.customer_repo.create_customer_version_from_qbo(tenant_id=tenant_id, customer_id=current_mapping["customer_id"], qbo_customer=qbo_customer)
                            self._sync_personal_additional_info_from_qbo(
                                tenant_id=tenant_id,
                                customer=new_customer,
                                qbo_customer=qbo_customer,
                                dry_run=False,
                            )
                            target_customer = new_customer["id"]
                        rotate_kwargs = {
                            "tenant_id": tenant_id,
                            "service_id": service_id,
                            "customer_id": target_customer,
                            "external_customer_id": qbo_customer_id,
                            "external_account_id": realm_id,
                            "sync_status": self.customer_repo.STATUS_SYNCED,
                            "reason": self._sync_success_reason("Previous customer sync issue was resolved, and the QuickBooks customer was imported to FIELDCRAFT.", "CUSTOMER_IMPORT_RETRY_IMPORTED"),
                        }
                        if customer_additional_info:
                            rotate_kwargs["additional_info"] = customer_additional_info
                        self.customer_repo.rotate_mapping_for_customer(**rotate_kwargs)
                        self.customer_repo.close_mapping_as_invalid(current_mapping["id"])
                        self._audit_customer_event(
                            tenant_id=tenant_id,
                            action=AUDIT_ACTION_CUSTOMER_UPDATED_IN_FIELDCRAFT,
                            resource_id=str(target_customer),
                            context=f"qboCustomerId={qbo_customer_id}",
                        )
                        summary["updatedInFIELDCRAFT"] += 1
                        detail.update({const.KEY_STATUS: "success", const.KEY_ACTION: "resolved import failure", const.KEY_FIELDCRAFT_CUSTOMER_ID: target_customer, const.KEY_REASON: self._sync_success_reason("Previous customer sync issue was resolved, and the QuickBooks customer was imported to FIELDCRAFT.", "CUSTOMER_IMPORT_RETRY_IMPORTED")})
                        summary[const.KEY_RESULTS].append(detail)
                        return

            email_lookup = (payload.get(const.KEY_EMAIL) or "").strip()
            if self._customer_email_exists_in_fieldcraft_or_ad(tenant_id=tenant_id, tenant_api_token=tenant_api_token, email=email_lookup):
                existing_customer_id = self.customer_repo.get_customer_id_by_email(
                    tenant_id=tenant_id,
                    email=email_lookup,
                )
                reason_text, reason_payload = self._duplicate_email_reason(
                    email=email_lookup,
                    qbo_customer_id=qbo_customer_id,
                    display_name=display_name,
                    existing_customer_id=existing_customer_id,
                )
                logger.warning(f"[CustomerImport] customer.duplicate_email_blocked | existing_customer_id={existing_customer_id} qbo_customer_id={qbo_customer_id!r} email={email_lookup!r}")
                # This QBO customer only conflicts by email; it is not the existing FIELDCRAFT customer.
                # Keep the failed import row detached so it does not shadow the synced customer mapping.
                self._mark_customer_import_failed(
                    summary=summary,
                    detail=detail,
                    tenant_id=tenant_id,
                    service_id=service_id,
                    realm_id=realm_id,
                    qbo_customer_id=qbo_customer_id,
                    display_name=display_name,
                    payload=payload,
                    current_mapping=current_mapping,
                    reason_text=reason_text,
                    reason_payload=reason_payload,
                    action="duplicateEmailImportFailed",
                    qbo_only_import_failed=True,
                    extra_detail=self._existing_customer_detail_for_email(tenant_id, email_lookup),
                    additional_info=customer_additional_info or None,
                )
                return

            ok_payload, skip_reason = self._is_payload_valid_for_tenant_api(payload)
            if not ok_payload:
                reason_text = f"QuickBooks customer {display_name or qbo_customer_id} cannot be imported: {skip_reason}"
                reason_payload = self._build_reason(
                    reason_text,
                    code=self.REASON_MISSING_REQUIRED_FIELDS,
                    qbo_customer_id=qbo_customer_id,
                    display_name=display_name,
                    action_required="Add the missing required customer fields in QuickBooks, then run customer sync again.",
                )
                self._mark_customer_import_failed(
                    summary=summary,
                    detail=detail,
                    tenant_id=tenant_id,
                    service_id=service_id,
                    realm_id=realm_id,
                    qbo_customer_id=qbo_customer_id,
                    display_name=display_name,
                    payload=payload,
                    current_mapping=current_mapping,
                    reason_text=reason_text,
                    reason_payload=reason_payload,
                    action="missingRequired",
                    additional_info=customer_additional_info or None,
                )
                return

            if dry_run:
                reason = self._build_reason("Dry run enabled, so no customer changes were saved.", code="CUSTOMER_DRY_RUN")
                detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "dryRun", const.KEY_REASON: reason})
                summary[const.KEY_RESULTS].append(detail)
                return

            tenant_repo = TenantUserAccountRepository(tenant_id)
            try:
                customer_value = self.tenant_client.create_user(tenant_api_token, self._build_tenant_user_payload_from_qbo(qbo_customer, service_id=service_id))
                logger.debug(f"[ImportService] Tenant API create_user response | customer_value={customer_value}")
            except Exception:
                email_lookup = (payload.get(const.KEY_EMAIL) or "").strip()
                if email_lookup:
                    existing_user = tenant_repo.get_user_by_email(email_lookup)
                    if not (existing_user and existing_user.get("id")):
                        raise
                else:
                    raise

            # Tenant API owns customer creation (customers table + initial PENDING_PUSH mapping).
            # Resolve the FIELDCRAFT customer_id it created, then upsert the QBO link to SYNCED.
            email_lookup = (payload.get(const.KEY_EMAIL) or "").strip()
            customer_id = self._resolve_created_customer_id(tenant_id=tenant_id, email=email_lookup)
            if not customer_id:
                raise Exception(f"Customer not found in FIELDCRAFT after Tenant API creation (email={email_lookup!r})")
            logger.info(f"[ImportService] Tenant API created customer resolved | customer_id={customer_id} email={email_lookup!r}")

            if self._extract_customer_address_from_qbo(qbo_customer):
                created_customer = self.customer_repo.get_current_customer_by_id(customer_id) or self.customer_repo.get_customer_by_id(customer_id)
                self._sync_personal_additional_info_from_qbo(
                    tenant_id=tenant_id,
                    customer=created_customer,
                    qbo_customer=qbo_customer,
                    dry_run=False,
                )

            mapping_kwargs = {
                "tenant_id": tenant_id,
                "customer_id": customer_id,
                "external_customer_id": qbo_customer_id,
                "external_account_id": realm_id,
                "status": self.customer_repo.STATUS_SYNCED,
                "service_id": service_id,
                "reason": self._sync_success_reason("Customer was created in FIELDCRAFT from QuickBooks and linked successfully.", "CUSTOMER_CREATED_IN_FIELDCRAFT"),
            }
            if customer_additional_info:
                mapping_kwargs["additional_info"] = customer_additional_info
            self.customer_repo.create_external_mapping(**mapping_kwargs)
            if current_mapping_is_import_failed:
                self.customer_repo.close_mapping_as_invalid(current_mapping["id"])
            self._audit_customer_event(
                tenant_id=tenant_id,
                action=AUDIT_ACTION_CUSTOMER_CREATED_IN_FIELDCRAFT,
                resource_id=str(customer_id),
                context=f"qboCustomerId={qbo_customer_id}",
            )
            summary["created in FIELDCRAFT"] += 1
            detail.update({const.KEY_STATUS: "success", const.KEY_ACTION: "created in FIELDCRAFT", const.KEY_FIELDCRAFT_CUSTOMER_ID: customer_id, const.KEY_REASON: self._sync_success_reason("Customer was created in FIELDCRAFT from QuickBooks and linked successfully.", "CUSTOMER_CREATED_IN_FIELDCRAFT")})
            summary[const.KEY_RESULTS].append(detail)
        except Exception as exc:
            logger.exception(f"[ImportService] ERROR processing qboCustomerId={qbo_customer_id} | {exc}")
            self._persist_import_failed_mapping(
                tenant_id=tenant_id,
                service_id=service_id,
                realm_id=realm_id,
                qbo_customer_id=qbo_customer_id,
                payload=payload,
                current_mapping=current_mapping,
                reason=self._build_reason(str(exc)),
                additional_info=customer_additional_info or None,
            )
            summary["failed"] += 1
            failure = {
                const.KEY_QBO_CUSTOMER_ID: qbo_customer_id,
                const.KEY_DISPLAY_NAME: display_name,
                const.KEY_ERROR: str(exc),
            }
            qbo_api.append_failure(summary, failure)
            detail.update({const.KEY_STATUS: "failed", const.KEY_ACTION: "error", const.KEY_ERROR: str(exc), const.KEY_REASON: self._build_reason("Customer import failed. We saved it for retry on the next customer sync.", code="CUSTOMER_IMPORT_FAILED_RETRY_SAVED", error=str(exc))})
            summary[const.KEY_RESULTS].append(detail)


    def _persist_import_failed_mapping(
        self,
        *,
        tenant_id: str,
        service_id: str,
        realm_id: str,
        qbo_customer_id: str,
        payload: Optional[Dict[str, Any]],
        current_mapping: Optional[Dict[str, Any]],
        reason: Optional[str] = None,
        customer_id: Optional[int] = None,
        qbo_only_import_failed: bool = False,
        sync_status: Optional[str] = None,
        additional_info: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            # QBO-only failures, such as duplicate email, must not be linked to
            # the existing synced FIELDCRAFT customer. Store the failed QuickBooks
            # customer as its own IMPORT_FAILED external-map row.
            target_customer_id = None
            detach_customer = bool(qbo_only_import_failed)

            if not detach_customer:
                target_customer_id = int(customer_id) if customer_id is not None else None
                if target_customer_id is None and current_mapping and current_mapping.get("customer_id"):
                    target_customer_id = current_mapping.get("customer_id")
                elif target_customer_id is None and payload:
                    email_lookup = (payload.get(const.KEY_EMAIL) or "").strip()
                    if email_lookup:
                        target_customer_id = self.customer_repo.get_customer_id_by_email(tenant_id=tenant_id, email=email_lookup)

            row = self.customer_repo.upsert_import_failed_mapping(
                tenant_id=tenant_id,
                service_id=service_id,
                external_customer_id=qbo_customer_id,
                external_account_id=realm_id,
                customer_id=target_customer_id,
                reason=reason or self._build_reason("Customer import failed and will be retried on next sync"),
                detach_customer=detach_customer,
                sync_status=sync_status,
                additional_info=additional_info,
            )
            logger.info(f"[ImportService] IMPORT_FAILED persisted | qbo_customer_id={qbo_customer_id} mapping_id={row.get('id')} customer_id={row.get('customer_id')} sync_status={row.get('sync_status')} reason={row.get('reason')}")
            persisted_status = row.get("sync_status") or sync_status or self.customer_repo.STATUS_IMPORT_FAILED
            audit_action = (
                AUDIT_ACTION_CUSTOMER_SYNC_FAILED_PERSISTED
                if persisted_status == self.customer_repo.STATUS_SYNC_FAILED
                else AUDIT_ACTION_CUSTOMER_IMPORT_FAILED_PERSISTED
            )
            self._audit_customer_event(
                tenant_id=tenant_id,
                action=audit_action,
                status="failed",
                resource_id=str(qbo_customer_id),
                context=f"mappingId={row.get('id')}",
            )
            return row
        except Exception as persist_exc:
            logger.warning(f"[ImportService] WARN: IMPORT_FAILED persistence failed | qbo_customer_id={qbo_customer_id} error={persist_exc}", exc_info=True)
            return None


    def _is_payload_valid_for_tenant_api(self, user_payload: Dict[str, Any]) -> Tuple[bool, str]:
        email = (user_payload.get(const.KEY_EMAIL) or "").strip()
        phone = (user_payload.get("mobileNo") or user_payload.get("mobile_no") or user_payload.get(const.KEY_PHONE) or "").strip()
        missing = []
        if not email:
            missing.append("email")
        if not phone:
            missing.append("phone")
        if missing:
            return False, f"missing required fields for tenant customer create: {', '.join(missing)}"
        return True, ""

    def _build_tenant_user_payload_from_qbo(self, qbo_customer: Dict[str, Any], service_id: Any = None) -> Dict[str, Any]:
        payload = self._build_customer_payload_from_qbo(qbo_customer)
        firstname = (payload.get("firstname") or payload.get("first_name") or "").strip() or "Customer"
        lastname = (payload.get("lastname") or payload.get("last_name") or "").strip()
        email = (payload.get(const.KEY_EMAIL) or "").strip()
        phone = (payload.get("mobileNo") or payload.get("mobile_no") or payload.get(const.KEY_PHONE) or "").strip()
        return {
            "firstname": firstname,
            "lastname": lastname,
            const.KEY_EMAIL: email,
            "mobileNo": phone,
            "serviceId": int(service_id) if service_id is not None else None,
            "roles": [
                {
                    "roles": "customer",
                    "isPrimary": True,
                    "roleOrder": 1,
                    "assignStartDate": "",
                    "createdBy": "",
                }
            ],
            "deleted": False,
            "eulaAccepted": False,
            "active": False,
            "gender": None,
            "avatar": None,
            "birthdate": None,
            "middlename": None,
            "updatedAt": None,
            "hiredDate": "",
            "workStartDate": "",
            "objectId": None,
            "alternateMobileNo": None,
            "id": None,
            const.KEY_STATUS: "Invited",
        }

    def _is_cdc_too_old(self, changed_since: datetime) -> bool:
        if changed_since.tzinfo is None:
            changed_since = changed_since.replace(tzinfo=timezone.utc)
        return changed_since < (datetime.now(timezone.utc) - timedelta(days=29))

    def _fetch_qbo_customers_cdc(self, access_token: str, base_url: str, realm_id: str, changed_since: datetime) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        if changed_since.tzinfo is None:
            changed_since = changed_since.replace(tzinfo=timezone.utc)
        changed_since_value = changed_since.replace(microsecond=0).isoformat().replace('+00:00', 'Z')
        url = f"{base_url}/v3/company/{realm_id}/cdc"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "text/plain",
        }
        params = {
            "entities": "Customer",
            "changedSince": changed_since_value,
        }
        logger.debug(f"[ImportService] Fetching QBO customers via CDC | realmId_present={bool(realm_id)} changedSince={changed_since_value}")
        resp = qbo_api.qbo_get(url, headers=headers, params=params, timeout=60)

        if resp.status_code != 200:
            raise Exception(qbo_api.build_response_error(resp, "QBO customer CDC failed"))
        data = qbo_api.safe_json(resp)
        customers_by_id: Dict[str, Dict[str, Any]] = {}
        for cdc_block in data.get("CDCResponse") or []:
            for query_response in cdc_block.get("QueryResponse") or []:
                for customer in query_response.get("Customer") or []:
                    customer_id = str(customer.get(const.QBO_KEY_ID) or "").strip()
                    if customer_id:
                        customers_by_id[customer_id] = customer
        customers = list(customers_by_id.values())
        intuit_tid = qbo_api.extract_intuit_tid(resp)
        logger.debug(f"[ImportService] CDC returned {len(customers)} customer(s)")
        return customers, intuit_tid

    def _get_qbo_api_base(self, env: str) -> str:
        from source.Utils import QBODiscoveryClient
        return QBODiscoveryClient.get_qbo_api_base(env)

    def _refresh_access_token(self, client_id: str, client_secret: str, refresh_token: str) -> str:
        return QuickBooksOAuthClient(repository=self.qbo_repo).refresh_access_token(
            tenant_id=str(self._tenant_id),
            service_id=str(self._service_id),
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            token_url=self.qbo_service.token_url,
            current_connection=getattr(self, "_connection", None),
        )

    def _fetch_qbo_customers(self, access_token: str, base_url: str, realm_id: str, start_position: int, max_results: int) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        url = f"{base_url}/v3/company/{realm_id}/query"
        query = f"select * from Customer startposition {int(start_position)} maxresults {int(max_results)}"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        resp = qbo_api.qbo_get(url, headers=headers, params={"query": query}, timeout=60)
        if resp.status_code != 200:
            raise Exception(qbo_api.build_response_error(resp, "QBO query failed"))
        data = qbo_api.safe_json(resp)
        q = data.get("QueryResponse") or {}
        customers = q.get("Customer") or []
        return customers if isinstance(customers, list) else [], qbo_api.extract_intuit_tid(resp)

    def _fetch_qbo_customer_by_id(self, access_token: str, base_url: str, realm_id: str, customer_id: str) -> Dict[str, Any]:
        url = f"{base_url}/v3/company/{realm_id}/customer/{customer_id}"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        resp = qbo_api.qbo_get(url, headers=headers, timeout=60)
        if resp.status_code != 200:
            raise Exception(qbo_api.build_response_error(resp, "QBO get customer failed"))
        data = qbo_api.safe_json(resp)
        return data.get("Customer") or {}

    def _clean_phone(self, value: str) -> str:
        if not value:
            return ""
        digits = re.sub(r"\D+", "", value)
        return digits[:16]

    def _split_name(self, display_name: str) -> Tuple[str, str]:
        dn = (display_name or "").strip()
        if not dn:
            return "Customer", ""
        parts = dn.split()
        if len(parts) == 1:
            return parts[0], ""
        return parts[0], " ".join(parts[1:])

    def _parse_timestamp(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            raw = str(value).strip()
            if not raw:
                return None
            raw = raw.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(raw)
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _get_qbo_last_updated_at(self, qbo_customer: Dict[str, Any]) -> Optional[datetime]:
        metadata = qbo_customer.get("MetaData") or {}
        return self._parse_timestamp(metadata.get("LastUpdatedTime") or metadata.get("CreateTime"))

    def _is_deleted_qbo_customer(self, qbo_customer: Dict[str, Any]) -> bool:
        status = str(qbo_customer.get("status") or qbo_customer.get("Status") or "").strip().lower()
        display_name = str(qbo_customer.get(const.QBO_KEY_DISPLAY_NAME) or "").lower()
        return (
            status == "deleted"
            or qbo_customer.get("Active") is False
            or "(deleted)" in display_name
            or "(deleted-" in display_name
        )

    def _is_qbo_sub_customer(self, qbo_customer: Dict[str, Any]) -> bool:
        """Return True for QBO sub-customers/jobs that belong to project sync."""
        parent_ref = qbo_customer.get("ParentRef")
        is_job = bool(qbo_customer.get("Job"))
        return bool(parent_ref) or is_job

    def _sub_customer_skipped_reason(self, qbo_customer: Dict[str, Any]) -> str:
        parent_ref = qbo_customer.get("ParentRef") or {}
        parent_id = parent_ref.get("value") if isinstance(parent_ref, dict) else None
        parent_name = parent_ref.get("name") if isinstance(parent_ref, dict) else None
        message = (
            "This QuickBooks record is a sub-customer/project, so customer sync skipped it. "
            "It will be handled by the project sync flow when project sync is enabled."
        )
        return self._build_reason(
            message,
            code=self.REASON_SUB_CUSTOMER_SKIPPED,
            qbo_customer_id=str(qbo_customer.get(const.QBO_KEY_ID) or "").strip() or None,
            display_name=str(qbo_customer.get(const.QBO_KEY_DISPLAY_NAME) or "").strip() or None,
            parent_qbo_customer_id=str(parent_id or "").strip() or None,
            parent_qbo_customer_name=str(parent_name or "").strip() or None,
            action_required="No customer action is needed. This record will sync as a project after project sync is available.",
        )

    def _should_skip_import_update(self, qbo_last_updated_at: Optional[datetime], local_latest_at: Optional[datetime]) -> bool:
        if qbo_last_updated_at is None or local_latest_at is None:
            return False
        return qbo_last_updated_at <= local_latest_at

    def _get_personal_additional_info(self, tenant_id: str, customer: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        customer_uuid = ((customer or {}).get("customer_uuid") or (customer or {}).get("customerUuid") or "")
        if not tenant_id or not customer_uuid:
            return None
        try:
            info = self.customer_repo.get_personal_additional_info(tenant_id, customer_uuid)
        except Exception as exc:
            logger.warning(f"[CustomerImport] WARN: could not load personal_additional_info | customer_uuid={customer_uuid} error={exc}")
            return None
        return info if isinstance(info, dict) else None

    def _normalize_address_for_compare(self, address_info: Optional[Dict[str, Any]]) -> Dict[str, str]:
        address_info = address_info or {}
        return {
            "address": str(address_info.get("address") or address_info.get("line1") or "").strip(),
            "city": str(address_info.get("city") or "").strip(),
            "state": str(address_info.get("state") or address_info.get("country_subdivision_code") or "").strip(),
            "country": str(address_info.get("country") or "").strip(),
            "zip_code": str(address_info.get("zip_code") or address_info.get("postal_code") or "").strip(),
        }

    def _qbo_address_differs_from_personal_info(self, qbo_customer: Dict[str, Any], personal_info: Optional[Dict[str, Any]]) -> bool:
        qbo_address = self._extract_customer_address_from_qbo(qbo_customer)
        if not qbo_address:
            return False
        return self._normalize_address_for_compare(qbo_address) != self._normalize_address_for_compare(personal_info)

    def _extract_customer_address_from_qbo(self, qbo_customer: Dict[str, Any]) -> Optional[Dict[str, str]]:
        address = qbo_customer.get("BillAddr") or qbo_customer.get("ShipAddr") or {}
        if not isinstance(address, dict):
            return None

        lines = []
        for key in ("Line1", "Line2", "Line3", "Line4", "Line5"):
            value = str(address.get(key) or "").strip()
            if value:
                lines.append(value)

        address_info = {
            "address": ", ".join(lines),
            "city": str(address.get("City") or "").strip(),
            "state": str(address.get("CountrySubDivisionCode") or "").strip(),
            "country": str(address.get("Country") or "").strip(),
            "zip_code": str(address.get("PostalCode") or "").strip(),
        }
        return address_info if any(address_info.values()) else None

    def _sync_personal_additional_info_from_qbo(
        self,
        *,
        tenant_id: str,
        customer: Optional[Dict[str, Any]],
        qbo_customer: Dict[str, Any],
        dry_run: bool,
    ) -> bool:
        address_info = self._extract_customer_address_from_qbo(qbo_customer)
        if dry_run or not address_info or not customer:
            return False
        changed = self.customer_repo.upsert_personal_additional_info(
            tenant_id=tenant_id,
            customer=customer,
            address_info=address_info,
        )
        if changed:
            logger.debug(f"[CustomerImport] customer.address_synced | customer_uuid={customer.get('customer_uuid')} tenant_id={tenant_id}")
        return bool(changed)

    def _build_customer_payload_from_qbo(self, qbo_customer: Dict[str, Any]) -> Dict[str, Any]:
        display_name = (qbo_customer.get(const.QBO_KEY_DISPLAY_NAME) or "").strip()
        given = (qbo_customer.get("GivenName") or "").strip()
        family = (qbo_customer.get("FamilyName") or "").strip()
        if not given:
            given, family = self._split_name(display_name)
        pe = qbo_customer.get("PrimaryEmailAddr") or {}
        phone_block = qbo_customer.get("PrimaryPhone") or {}
        mobile_block = qbo_customer.get("Mobile") or {}
        phone_value = (phone_block.get("FreeFormNumber") or "").strip()
        if not phone_value:
            phone_value = (mobile_block.get("FreeFormNumber") or "").strip()
        phone_value = "".join(c for c in phone_value if c.isdigit())
        logger.debug(f"[CustomerImport] _build_customer_payload_from_qbo | raw_primary={phone_block.get('FreeFormNumber')!r} raw_mobile={mobile_block.get('FreeFormNumber')!r} clean_phone={phone_value!r}")
        return {
            "firstname": given,
            "first_name": given,
            "lastname": family,
            "last_name": family,
            const.KEY_EMAIL: (pe.get("Address") or "").strip(),
            "mobile_no": phone_value,
            const.KEY_PHONE: phone_value,
            const.KEY_STATUS: "Inactive" if qbo_customer.get("Active") is False else "Active",
            "is_deleted": qbo_customer.get("Active") is False,
        }
