from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from source.dal.CustomerRepository import CustomerRepository
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.service.QuickBooksService import QuickBooksService
from source.Utils import constants as const
from source.quickbooks.QuickBooksAuthentication import QuickBooksOAuthClient
from source.quickbooks.qbo_query_utils import escape_qbo_literal
from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils.auditlog import safe_audit_log

logger = logging.getLogger(__name__)

AUDIT_ACTION_CUSTOMER_CREATED_IN_QBO = "customer_created_in_qbo"
AUDIT_ACTION_CUSTOMER_UPDATED_IN_QBO = "customer_updated_in_qbo"
AUDIT_ACTION_CUSTOMER_SYNC_FAILED_PERSISTED = "customer_sync_failed_persisted"


class QuickBooksCustomerExportService:
    """Delta-oriented customer export using the new domain model.

    - Mapping table drives outbound work selection.
    - Customer table provides the actual business payload.
    - Current active mapping rows with PENDING_PUSH/FAILED are exported.
    - New customers without mapping are first materialized into a pending mapping row.
    """

    def __init__(self):
        self.qbo_repo = QuickBooksRepository()
        self.customer_repo = CustomerRepository()
        self.qbo_service = QuickBooksService()

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

    @staticmethod
    def _qbo_customer_from_response(response: Any, fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if isinstance(response, dict):
            nested_customer = response.get("Customer")
            if isinstance(nested_customer, dict):
                return nested_customer
            return response
        if response:
            customer = dict(fallback or {})
            customer[const.QBO_KEY_ID] = str(response)
            return customer
        return dict(fallback or {})

    def _qbo_customer_id_from_response(self, response: Any, fallback: Optional[str] = None) -> str:
        customer = self._qbo_customer_from_response(response)
        qbo_id = customer.get(const.QBO_KEY_ID) if isinstance(customer, dict) else response
        qbo_id = str(qbo_id or fallback or "").strip()
        if not qbo_id:
            raise Exception("QBO customer response did not include Id")
        return qbo_id

    def _mark_mapping_synced_from_qbo(
        self,
        *,
        mapping_id: int,
        external_customer_id: str,
        external_account_id: Optional[str],
        reason: str,
        qbo_customer: Optional[Dict[str, Any]] = None,
    ) -> None:
        additional_info = self._customer_additional_info_from_qbo(qbo_customer)
        kwargs: Dict[str, Any] = {
            "mapping_id": mapping_id,
            "external_customer_id": external_customer_id,
            "external_account_id": external_account_id,
            "reason": reason,
        }
        if additional_info:
            kwargs["additional_info"] = additional_info
        self.customer_repo.mark_mapping_synced(**kwargs)

    def _merge_mapping_additional_info_from_qbo(self, *, mapping_id: int, qbo_customer: Optional[Dict[str, Any]]) -> None:
        additional_info = self._customer_additional_info_from_qbo(qbo_customer)
        if additional_info:
            self.customer_repo.merge_mapping_additional_info(mapping_id, additional_info)

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

    def _qbo_newer_reason(self, qbo_last_updated_at: Optional[datetime], local_latest_at: Optional[datetime]) -> str:
        message = (
            "We did not export this customer because QuickBooks has a newer update than FIELDCRAFT. "
            "Run customer import to bring the QuickBooks change into FIELDCRAFT, then update the customer again if needed."
        )
        return self._build_reason(
            message,
            code="CUSTOMER_QBO_NEWER",
            qbo_updated_at=self._timestamp_for_reason(qbo_last_updated_at),
            fieldcraft_updated_at=self._timestamp_for_reason(local_latest_at),
            action_required="Import customers from QuickBooks first, then retry export if FIELDCRAFT still has changes to send.",
        )

    def _qbo_customer_inactive_reason(self, qbo_customer_id: str, qbo_customer: Optional[Dict[str, Any]]) -> str:
        qbo_customer = qbo_customer or {}
        return self._build_reason(
            "This customer was made inactive in QuickBooks. Make the customer active in QuickBooks, then run customer sync again.",
            code="CUSTOMER_QBO_INACTIVE",
            qbo_customer_id=qbo_customer_id,
            qbo_display_name=qbo_customer.get(const.QBO_KEY_DISPLAY_NAME),
            action_required="Make the customer active in QuickBooks, then run customer sync again.",
        )

    def _is_qbo_customer_inactive(self, qbo_customer: Optional[Dict[str, Any]]) -> bool:
        if not qbo_customer:
            return False
        display_name = str(qbo_customer.get(const.QBO_KEY_DISPLAY_NAME) or "").lower()
        return (
            qbo_customer.get("Active") is False
            or "(deleted)" in display_name
            or "(deleted-" in display_name
        )

    def _mark_qbo_customer_inactive_failure(
        self,
        *,
        tenant_id: str,
        mapping_id: int,
        customer_id: Any,
        qbo_customer_id: str,
        qbo_customer: Optional[Dict[str, Any]],
        summary: Dict[str, Any],
        detail: Dict[str, Any],
    ) -> None:
        reason = self._qbo_customer_inactive_reason(qbo_customer_id, qbo_customer)
        kwargs: Dict[str, Any] = {
            "mapping_id": mapping_id,
            "status": self.customer_repo.STATUS_SYNC_FAILED,
            "reason": reason,
        }
        additional_info = self._customer_additional_info_from_qbo(qbo_customer)
        if additional_info:
            kwargs["additional_info"] = additional_info
        self.customer_repo.mark_mapping_failed(**kwargs)
        self._audit_customer_event(
            tenant_id=tenant_id,
            action=AUDIT_ACTION_CUSTOMER_SYNC_FAILED_PERSISTED,
            status="failed",
            resource_id=qbo_customer_id,
            context=f"fieldcraftCustomerId={customer_id}",
        )
        summary["failed"] += 1
        qbo_api.append_failure(summary, {
            "customerId": customer_id,
            "externalCustomerId": qbo_customer_id,
            const.KEY_ERROR: "QuickBooks customer is inactive",
            const.KEY_DETAILS: reason,
        })
        detail.update({
            "status": "failed",
            "action": "qboCustomerInactive",
            "qboCustomerId": qbo_customer_id,
            "reason": reason,
            "error": "QuickBooks customer is inactive",
        })

    def export_customers(
        self,
        tenant_id: str,
        service_id: str,
        limit: int = 500,
        offset: int = 0,
        include_mapping_ids: Optional[List[int]] = None,
        **_: Any,
    ) -> Dict[str, Any]:
        logger.info(f"[ExportService] export_customers called | tenant_id={tenant_id} service_id={service_id} limit={limit} offset={offset}")
        try:
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id)
            logger.debug(f"[ExportService] QBO connection fetched | realmId_present={bool(connection.get(const.KEY_REALM_ID))} env={connection.get(const.KEY_ENV)}")
            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = (connection.get(const.KEY_ENV) or "sandbox").lower()
            if not refresh_token or not realm_id:
                logger.error(f"[ExportService] ERROR: Missing refreshToken or realmId for tenant={tenant_id}")
                return {"ok": False, "message": "QuickBooks not connected for this tenant (missing refreshToken/realmId)."}

            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id)
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)
            if not client_id or not client_secret:
                logger.error(f"[ExportService] ERROR: Missing ClientId or ClientSecret for tenant={tenant_id}")
                return {"ok": False, "message": "QuickBooks client credentials missing (ClientId/ClientSecret)."}

            logger.debug(f"[ExportService] Refreshing access token for tenant={tenant_id}")
            access_token = self.refresh_access_token(
                tenant_id=tenant_id,
                service_id=service_id,
                connection=connection,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
            )
            logger.debug(f"[ExportService] Access token refreshed successfully")
            base_url = self.get_qbo_api_base(env)
            logger.debug(f"[ExportService] QBO base_url={base_url}")
            candidates = self.customer_repo.get_export_candidates(
                tenant_id=tenant_id,
                service_id=service_id,
                limit=limit,
                offset=offset,
                include_mapping_ids=include_mapping_ids,
            )
            logger.debug(f"[ExportService] Export candidates fetched | count={len(candidates)}")
            if not candidates:
                logger.info(f"[ExportService] No PENDING_PUSH/FAILED candidates — nothing to export for tenant_id={tenant_id} service_id={service_id}")

            summary: Dict[str, Any] = {
                "ok": True,
                "tenantId": tenant_id,
                "serviceId": service_id,
                "realmIdPresent": bool(realm_id),
                "env": env,
                "requested": len(candidates),
                "mapped from QBO": 0,
                "skippedQboNewer": 0,
                "createdInQbo": 0,
                "updatedInQbo": 0,
                "skippedAlreadySynced": 0,
                "skippedInProgress": 0,
                "skippedNotEligible": 0,
                "failed": 0,
                "failures": [],
                const.KEY_RESULTS: [],
            }

            for item in candidates:
                customer = item["customer"]
                mapping = item["mapping"]
                logger.debug(f"[ExportService] Processing customer | fieldcraftCustomerId={customer['id']} email={customer.get(const.KEY_EMAIL)} mapping_id={mapping['id']} sync_status={mapping.get('sync_status')} external_customer_id={mapping.get('external_customer_id')!r}")
                detail = {
                    "fieldcraftCustomerId": customer["id"],
                    "displayName": self.build_display_name(customer),
                    const.KEY_EMAIL: customer.get(const.KEY_EMAIL),
                    "status": None,
                    "action": None,
                    "qboCustomerId": mapping.get("external_customer_id") or None,
                    "reason": None,
                    "error": None,
                }
                try:
                    mapping_id = int(mapping.get("id") or 0)
                    if not mapping_id:
                        raise Exception("Missing customer external map row for export candidate")

                    claim = self.customer_repo.claim_export_mapping(
                        tenant_id=tenant_id,
                        service_id=service_id,
                        mapping_id=mapping_id,
                    )
                    if not claim.get("claimed"):
                        claim_reason = str(claim.get("reason") or "").strip()
                        current_mapping = claim.get("mapping") or {}
                        if claim_reason == "already_synced":
                            summary["skippedAlreadySynced"] += 1
                            action = "already synced"
                        elif claim_reason == "in_progress":
                            summary["skippedInProgress"] += 1
                            action = "sync already in progress"
                        else:
                            summary["skippedNotEligible"] += 1
                            action = "no longer eligible for export"
                        detail.update({
                            "status": "success",
                            "action": action,
                            "qboCustomerId": (current_mapping.get("external_customer_id") or None),
                        })
                        summary[const.KEY_RESULTS].append(detail)
                        continue

                    claimed_mapping = claim.get("mapping")
                    if isinstance(claimed_mapping, dict):
                        mapping = claimed_mapping

                    external_customer_id = (mapping.get("external_customer_id") or "").strip()
                    if not external_customer_id:
                        search_email = (customer.get(const.KEY_EMAIL) or "").strip()
                        search_display = detail["displayName"]
                        logger.debug(f"[ExportService] No external_customer_id — searching QBO | fieldcraftCustomerId={customer['id']} email={search_email!r} displayName={search_display!r}")
                        found_id = self.find_customer_in_qbo(
                            access_token=access_token,
                            base_url=base_url,
                            realm_id=str(realm_id),
                            email=search_email,
                            display_name=search_display,
                        )
                        logger.debug(f"[ExportService] QBO search result | fieldcraftCustomerId={customer['id']} found_qbo_id={found_id!r}")
                        if found_id:
                            logger.debug(f"[ExportService] Found existing QBO customer | qboCustomerId={found_id} — checking timestamps before update")
                            payload = self.build_qbo_customer_payload(customer, include_display_name=False)
                            qbo_current = self.get_customer_in_qbo(
                                access_token=access_token,
                                base_url=base_url,
                                realm_id=str(realm_id),
                                customer_id=str(found_id),
                            )
                            if self._is_qbo_customer_inactive(qbo_current):
                                logger.info(f"[ExportService] Customer export blocked because QBO customer is inactive | fieldcraftCustomerId={customer['id']} qboCustomerId={found_id}")
                                self._mark_qbo_customer_inactive_failure(
                                    tenant_id=tenant_id,
                                    mapping_id=mapping["id"],
                                    customer_id=customer["id"],
                                    qbo_customer_id=str(found_id),
                                    qbo_customer=qbo_current,
                                    summary=summary,
                                    detail=detail,
                                )
                                summary[const.KEY_RESULTS].append(detail)
                                continue
                            qbo_last_updated_at = self._get_qbo_last_updated_at(qbo_current)
                            local_latest_at = self._get_local_latest_at(customer)
                            if payload.get("BillAddr") and self._is_qbo_newer(qbo_last_updated_at, local_latest_at):
                                reason = self._qbo_newer_reason(qbo_last_updated_at, local_latest_at)
                                self._mark_mapping_synced_from_qbo(
                                    mapping_id=mapping["id"],
                                    external_customer_id=str(found_id),
                                    external_account_id=str(realm_id),
                                    reason=reason,
                                    qbo_customer=qbo_current,
                                )
                                summary["skippedQboNewer"] += 1
                                detail.update({"status": "skipped", "action": "skippedQboNewer", "qboCustomerId": str(found_id), "reason": reason})
                                summary[const.KEY_RESULTS].append(detail)
                                continue
                            if payload.get("BillAddr"):
                                logger.debug(f"[ExportService] Existing QBO customer found; updating address from personal_additional_info | qboCustomerId={found_id}")
                                self.update_customer_in_qbo(
                                    access_token=access_token,
                                    base_url=base_url,
                                    realm_id=str(realm_id),
                                    customer_id=str(found_id),
                                    payload=payload,
                                    existing_qbo_customer=qbo_current,
                                )
                                self._audit_customer_event(
                                    tenant_id=tenant_id,
                                    action=AUDIT_ACTION_CUSTOMER_UPDATED_IN_QBO,
                                    resource_id=str(found_id),
                                    context=f"fieldcraftCustomerId={customer['id']}",
                                )
                            reason = self._build_reason("Customer already exists in QuickBooks, so we linked the FIELDCRAFT customer to it.", code="CUSTOMER_MAPPED_TO_EXISTING_QBO")
                            self._mark_mapping_synced_from_qbo(
                                mapping_id=mapping["id"],
                                external_customer_id=str(found_id),
                                external_account_id=str(realm_id),
                                reason=reason,
                                qbo_customer=qbo_current,
                            )
                            summary["mapped from QBO"] += 1
                            detail.update({"status": "success", "action": "mapped from QBO", "qboCustomerId": str(found_id), "reason": reason})
                            summary[const.KEY_RESULTS].append(detail)
                            continue

                        logger.info(f"[ExportService] Customer not found in QBO — creating new QBO customer for fieldcraftCustomerId={customer['id']}")
                        payload = self.build_qbo_customer_payload(customer, include_display_name=True)
                        logger.debug(f"[ExportService] QBO create payload={payload}")
                        created_response = self.create_customer_in_qbo(access_token=access_token, base_url=base_url, realm_id=str(realm_id), payload=payload)
                        created_customer = self._qbo_customer_from_response(created_response)
                        created_id = self._qbo_customer_id_from_response(created_customer)
                        logger.info(f"[ExportService] Created QBO customer | qboCustomerId={created_id}")
                        reason = self._build_reason("Customer was created in QuickBooks and synced successfully.", code="CUSTOMER_CREATED_IN_QBO")
                        self._mark_mapping_synced_from_qbo(
                            mapping_id=mapping["id"],
                            external_customer_id=str(created_id),
                            external_account_id=str(realm_id),
                            reason=reason,
                            qbo_customer=created_customer,
                        )
                        self._audit_customer_event(
                            tenant_id=tenant_id,
                            action=AUDIT_ACTION_CUSTOMER_CREATED_IN_QBO,
                            resource_id=str(created_id),
                            context=f"fieldcraftCustomerId={customer['id']}",
                        )
                        summary["createdInQbo"] += 1
                        detail.update({"status": "success", "action": "created in QBO", "qboCustomerId": str(created_id), "reason": reason})
                        summary[const.KEY_RESULTS].append(detail)
                        continue

                    logger.info(f"[ExportService] Updating existing QBO customer | qboCustomerId={external_customer_id} for fieldcraftCustomerId={customer['id']}")
                    qbo_current = self.get_customer_in_qbo(
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=str(realm_id),
                        customer_id=external_customer_id,
                    )
                    if self._is_qbo_customer_inactive(qbo_current):
                        logger.info(f"[ExportService] Customer export blocked because QBO customer is inactive | fieldcraftCustomerId={customer['id']} qboCustomerId={external_customer_id}")
                        self._mark_qbo_customer_inactive_failure(
                            tenant_id=tenant_id,
                            mapping_id=mapping["id"],
                            customer_id=customer["id"],
                            qbo_customer_id=external_customer_id,
                            qbo_customer=qbo_current,
                            summary=summary,
                            detail=detail,
                        )
                        summary[const.KEY_RESULTS].append(detail)
                        continue
                    qbo_last_updated_at = self._get_qbo_last_updated_at(qbo_current)
                    local_latest_at = self._get_local_latest_at(customer)
                    payload = self.build_qbo_customer_payload(customer, include_display_name=False)
                    if self._is_qbo_newer(qbo_last_updated_at, local_latest_at):
                        reason = self._qbo_newer_reason(qbo_last_updated_at, local_latest_at)
                        self._mark_mapping_synced_from_qbo(
                            mapping_id=mapping["id"],
                            external_customer_id=external_customer_id,
                            external_account_id=str(realm_id),
                            reason=reason,
                            qbo_customer=qbo_current,
                        )
                        logger.info(f"[ExportService] Skipped QBO update because QBO is newer | qboCustomerId={external_customer_id} qbo_last_updated_at={qbo_last_updated_at} local_latest_at={local_latest_at}")
                        summary["skippedQboNewer"] += 1
                        detail.update({"status": "skipped", "action": "skippedQboNewer", "qboCustomerId": external_customer_id, "reason": reason})
                        summary[const.KEY_RESULTS].append(detail)
                        continue
                    logger.debug(f"[ExportService] QBO update payload={payload}")
                    updated_response = self.update_customer_in_qbo(
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=str(realm_id),
                        customer_id=external_customer_id,
                        payload=payload,
                        existing_qbo_customer=qbo_current,
                    )
                    updated_customer = self._qbo_customer_from_response(updated_response, fallback=qbo_current)
                    reason = self._build_reason("Customer was updated in QuickBooks and synced successfully.", code="CUSTOMER_UPDATED_IN_QBO")
                    self._mark_mapping_synced_from_qbo(
                        mapping_id=mapping["id"],
                        external_customer_id=external_customer_id,
                        external_account_id=str(realm_id),
                        reason=reason,
                        qbo_customer=updated_customer,
                    )
                    self._audit_customer_event(
                        tenant_id=tenant_id,
                        action=AUDIT_ACTION_CUSTOMER_UPDATED_IN_QBO,
                        resource_id=external_customer_id,
                        context=f"fieldcraftCustomerId={customer['id']}",
                    )
                    logger.info(f"[ExportService] Updated QBO customer | qboCustomerId={external_customer_id}")
                    summary["updatedInQbo"] += 1
                    detail.update({"status": "success", "action": "updated in QBO", "qboCustomerId": external_customer_id, "reason": reason})
                    summary[const.KEY_RESULTS].append(detail)
                except Exception as exc:
                    logger.exception(f"[ExportService] ERROR processing fieldcraftCustomerId={customer['id']} | {exc}")
                    reason = self._build_reason("Customer export failed. Please review the error and retry sync.", code="CUSTOMER_EXPORT_FAILED", error=str(exc))
                    self.customer_repo.mark_mapping_failed(mapping_id=mapping["id"], reason=reason)
                    self._audit_customer_event(
                        tenant_id=tenant_id,
                        action=AUDIT_ACTION_CUSTOMER_SYNC_FAILED_PERSISTED,
                        status="failed",
                        resource_id=str(mapping.get("external_customer_id") or customer["id"]),
                        context=f"fieldcraftCustomerId={customer['id']}",
                    )
                    summary["failed"] += 1
                    failure = {
                        "customerId": customer["id"],
                        "externalCustomerId": mapping.get("external_customer_id") or None,
                        const.KEY_ERROR: str(exc),
                    }
                    qbo_api.append_failure(summary, failure)
                    detail.update({"status": "failed", "action": "error", "error": str(exc), "reason": reason})
                    summary[const.KEY_RESULTS].append(detail)
            logger.info(f"[ExportService] Export complete | createdInQbo={summary['createdInQbo']} updatedInQbo={summary['updatedInQbo']} mappedFromQbo={summary['mapped from QBO']} failed={summary['failed']}")
            return summary
        except Exception:
            logger.exception(f"[ExportService] FATAL ERROR in export_customers | tenant_id={tenant_id}")
            return {"ok": False, "message": "Customer export failed.", "error": "Unexpected internal error. See server logs for details."}

    def get_qbo_api_base(self, env: str) -> str:
        from source.Utils import QBODiscoveryClient
        return QBODiscoveryClient.get_qbo_api_base(env)

    def refresh_access_token(
        self,
        *,
        tenant_id: str,
        service_id: str,
        connection: Optional[Dict[str, Any]],
        client_id: str,
        client_secret: str,
        refresh_token: str,
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

    def find_customer_in_qbo(self, access_token: str, base_url: str, realm_id: str, email: str, display_name: str) -> Optional[str]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        query_url = f"{base_url}/v3/company/{realm_id}/query"
        logger.debug(
            "[ExportService] find_customer_in_qbo | realm_id=%s email=%r display_name=%r",
            realm_id,
            email,
            display_name,
        )

        def run_query(q: str) -> Optional[str]:
            resp = qbo_api.qbo_get(query_url, headers=headers, params={"query": q}, timeout=30)
            logger.debug(
                "[ExportService] find_customer_in_qbo response | status=%s",
                resp.status_code,
            )
            if resp.status_code != 200:
                raise Exception(qbo_api.build_response_error(resp, "QBO query failed"))
            data = qbo_api.safe_json(resp)
            customers = (data.get("QueryResponse") or {}).get("Customer") or []
            if not customers:
                return None
            first = customers[0]
            return str(first.get(const.QBO_KEY_ID)) if first.get(const.QBO_KEY_ID) is not None else None

        if email:
            logger.debug(f"[ExportService] find_customer_in_qbo | strategy=email email={email!r}")
            try:
                result = run_query(f"SELECT Id FROM Customer WHERE PrimaryEmailAddr = '{escape_qbo_literal(email)}' MAXRESULTS 1")
                logger.debug(f"[ExportService] find_customer_in_qbo | email search result={result!r}")
                if result:
                    return result
            except Exception as exc:
                logger.warning(f"[ExportService] find_customer_in_qbo | email search failed: {exc}")
        if display_name:
            logger.debug(f"[ExportService] find_customer_in_qbo | strategy=displayName displayName={display_name!r}")
            try:
                result = run_query(f"SELECT Id FROM Customer WHERE DisplayName = '{escape_qbo_literal(display_name)}' MAXRESULTS 1")
                logger.debug(f"[ExportService] find_customer_in_qbo | displayName search result={result!r}")
                return result
            except Exception as exc:
                logger.warning(f"[ExportService] find_customer_in_qbo | displayName search failed: {exc}")
                return None
        logger.debug(f"[ExportService] find_customer_in_qbo | no email or displayName to search — returning None")
        return None

    def get_customer_in_qbo(self, access_token: str, base_url: str, realm_id: str, customer_id: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/customer/{customer_id}"
        resp = qbo_api.qbo_get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise Exception(qbo_api.build_response_error(resp, "QBO get customer failed"))
        data = qbo_api.safe_json(resp)
        return data.get("Customer") or {}

    def create_customer_in_qbo(self, access_token: str, base_url: str, realm_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/customer"
        logger.debug("[ExportService] create_customer_in_qbo | realm_id=%s", realm_id)
        resp = qbo_api.qbo_post(url, headers=headers, json=payload, timeout=30)
        logger.debug("[ExportService] create_customer_in_qbo response | status=%s", resp.status_code)
        if resp.status_code not in (200, 201):
            raise Exception(qbo_api.build_response_error(resp, "QBO create customer failed"))
        data = qbo_api.safe_json(resp)
        customer = data.get("Customer") or {}
        customer_id = customer.get(const.QBO_KEY_ID)
        if not customer_id:
            raise Exception("QBO create customer succeeded but Id missing")
        return customer

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

    def _get_qbo_last_updated_at(self, qbo_customer: Dict[str, Any]) -> Optional[datetime]:
        metadata = qbo_customer.get("MetaData") or {}
        return self._parse_timestamp(metadata.get("LastUpdatedTime") or metadata.get("CreateTime"))

    def _get_local_latest_at(self, customer: Dict[str, Any]) -> Optional[datetime]:
        personal_info = customer.get("personal_additional_info") if isinstance(customer.get("personal_additional_info"), dict) else None
        return self.customer_repo.get_customer_latest_local_timestamp(customer, None, personal_info)

    def _is_qbo_newer(self, qbo_last_updated_at: Optional[datetime], local_latest_at: Optional[datetime]) -> bool:
        if qbo_last_updated_at is None or local_latest_at is None:
            return False
        return qbo_last_updated_at > local_latest_at

    def update_customer_in_qbo(
        self,
        access_token: str,
        base_url: str,
        realm_id: str,
        customer_id: str,
        payload: Dict[str, Any],
        existing_qbo_customer: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        existing = existing_qbo_customer or self.get_customer_in_qbo(access_token=access_token, base_url=base_url, realm_id=realm_id, customer_id=customer_id)
        sync_token = existing.get(const.QBO_KEY_SYNC_TOKEN)
        if sync_token is None:
            raise Exception("QBO customer update failed: missing SyncToken")
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/customer"
        body = {
            const.QBO_KEY_ID: str(customer_id),
            const.QBO_KEY_SYNC_TOKEN: str(sync_token),
            "sparse": True,
            **payload,
        }
        logger.debug(
            "[ExportService] update_customer_in_qbo | realm_id=%s customer_id=%s",
            realm_id,
            customer_id,
        )
        resp = qbo_api.qbo_post(url, headers=headers, json=body, timeout=30)
        logger.debug("[ExportService] update_customer_in_qbo response | status=%s", resp.status_code)
        if resp.status_code not in (200, 201):
            raise Exception(qbo_api.build_response_error(resp, "QBO update customer failed"))
        data = qbo_api.safe_json(resp)
        customer = data.get("Customer") or {}
        updated_id = customer.get(const.QBO_KEY_ID)
        if not updated_id:
            raise Exception("QBO update customer succeeded but Id missing")
        return customer

    def build_display_name(self, fieldcraft_customer: Dict[str, Any]) -> str:
        first_name = self._clean_display_name_part(fieldcraft_customer.get("firstname"))
        last_name = self._clean_display_name_part(fieldcraft_customer.get("lastname"))
        base_name = " ".join(part for part in (first_name, last_name) if part) or "Customer"
        phone = self._build_display_phone(fieldcraft_customer)
        return f"{base_name}-{phone}" if phone else base_name

    def _clean_display_name_part(self, value: Any) -> str:
        return " ".join(str(value or "").strip().split())

    def _build_display_phone(self, fieldcraft_customer: Dict[str, Any]) -> str:
        raw_phone = (
            fieldcraft_customer.get("mobile_no")
            or fieldcraft_customer.get(const.KEY_PHONE)
            or fieldcraft_customer.get("phone")
            or ""
        )
        digits = "".join(ch for ch in str(raw_phone) if ch.isdigit())
        return digits[:20]

    def _clean_address_value(self, value: Any) -> str:
        return str(value or "").strip()

    def _build_qbo_address(self, address_info: Optional[Dict[str, Any]]) -> Dict[str, str]:
        address_info = address_info or {}
        qbo_address: Dict[str, str] = {}

        line1 = self._clean_address_value(address_info.get("address") or address_info.get("line1"))
        city = self._clean_address_value(address_info.get("city"))
        state = self._clean_address_value(address_info.get("state") or address_info.get("country_subdivision_code"))
        country = self._clean_address_value(address_info.get("country"))
        postal_code = self._clean_address_value(address_info.get("zip_code") or address_info.get("postal_code"))

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

    def build_qbo_customer_payload(self, fieldcraft_customer: Dict[str, Any], *, include_display_name: bool = True) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if include_display_name:
            payload[const.QBO_KEY_DISPLAY_NAME] = self.build_display_name(fieldcraft_customer)
        first_name = (fieldcraft_customer.get("firstname") or "").strip()
        last_name = (fieldcraft_customer.get("lastname") or "").strip()
        email = (fieldcraft_customer.get(const.KEY_EMAIL) or "").strip()
        phone = (fieldcraft_customer.get("mobile_no") or "").strip()
        if first_name:
            payload["GivenName"] = first_name
        if last_name:
            payload["FamilyName"] = last_name
        if email:
            payload["PrimaryEmailAddr"] = {"Address": email}
        if phone:
            payload["PrimaryPhone"] = {"FreeFormNumber": phone}

        address_info = fieldcraft_customer.get("personal_additional_info") or {
            "address": fieldcraft_customer.get("address"),
            "city": fieldcraft_customer.get("city"),
            "state": fieldcraft_customer.get("state"),
            "country": fieldcraft_customer.get("country"),
            "zip_code": fieldcraft_customer.get("zip_code"),
        }
        qbo_address = self._build_qbo_address(address_info)
        if qbo_address:
            payload["BillAddr"] = qbo_address
        return payload
