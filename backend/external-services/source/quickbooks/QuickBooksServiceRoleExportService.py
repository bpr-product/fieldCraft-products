from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils import constants as const
from source.Utils.auditlog import safe_audit_log
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.dal.ServiceRoleRepository import ServiceRoleRepository
from source.quickbooks.QuickBooksAuthentication import QuickBooksOAuthClient
from source.quickbooks.qbo_query_utils import escape_qbo_literal
from source.service.QuickBooksService import QuickBooksService

logger = logging.getLogger(__name__)

AUDIT_ACTION_SERVICE_ROLE_CREATED_IN_QBO = "service_role_created_in_qbo"
AUDIT_ACTION_SERVICE_ROLE_UPDATED_IN_QBO = "service_role_updated_in_qbo"
AUDIT_ACTION_SERVICE_ROLE_SYNC_FAILED_PERSISTED = "service_role_sync_failed_persisted"


class QuickBooksServiceRoleExportService:
    """Export FIELDCRAFT roles to QuickBooks as service items.

    Rules:
      - FIELDCRAFT roles are the source of truth.
      - Sync is export-only. QuickBooks service items are not imported into FIELDCRAFT.
      - Active roles are created/updated as QBO Item(Type=Service).
      - Inactive roles are only pushed when they already have a mapped QBO item.
    """

    def __init__(self) -> None:
        self.qbo_repo = QuickBooksRepository()
        self.qbo_service = QuickBooksService()
        self.role_repo = ServiceRoleRepository()

    @staticmethod
    def _build_reason(message: str, intuit_tid: Optional[str] = None, **extra: Any) -> str:
        data: Dict[str, Any] = {"message": message}
        if intuit_tid:
            data["intuit_tid"] = intuit_tid
        for key, value in extra.items():
            if value is not None:
                data[key] = value
        return json.dumps(data)

    def _audit_service_role_event(
        self,
        *,
        tenant_id: str,
        action: str,
        status: str = "success",
        resource_id: Optional[str] = None,
        context: Optional[str] = None,
        user_id: Optional[str] = None,
        email: Optional[str] = None,
        user_role: Optional[str] = None,
    ) -> None:
        safe_audit_log(
            tenant_id=tenant_id,
            action=action,
            user_id=user_id or None,
            email=email or None,
            user_role=user_role or None,
            status=status,
            context=context,
            resource_id=resource_id,
            resource_type="quickbooks_service_role",
        )

    def export_roles(
        self,
        *,
        tenant_id: str,
        service_id: str,
        limit: int = 500,
        offset: int = 0,
        object_id: Optional[str] = None,
        email: Optional[str] = None,
        user_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        logger.info("[ServiceRoleExport] export_roles called | tenant_id=%s service_id=%s limit=%s offset=%s", tenant_id, service_id, limit, offset)
        safe_audit_log(
            tenant_id=tenant_id,
            action="service_role_export_started",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="started",
            resource_type="quickbooks_service_role",
        )
        audit_action = "service_role_export_failed"
        audit_status = "failed"
        audit_context: Optional[str] = None
        try:
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = (connection.get(const.KEY_ENV) or "sandbox").lower()
            conn_status = (connection.get(const.KEY_STATUS) or const.STATUS_CONNECTED).lower()

            if conn_status and conn_status != const.STATUS_CONNECTED:
                audit_action, audit_context = "service_role_export_aborted", f"not_connected status={conn_status}"
                return {const.KEY_OK: False, const.KEY_MESSAGE: f"QuickBooks is not connected (status={conn_status})"}
            if not refresh_token or not realm_id:
                audit_action, audit_context = "service_role_export_aborted", "missing_realmId_or_refreshToken"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks not connected for this tenant (missing realmId/refreshToken)."}

            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)
            if not client_id or not client_secret:
                audit_action, audit_context = "service_role_export_aborted", "missing_credentials"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks client credentials missing (ClientId/ClientSecret)."}

            access_token = self.refresh_access_token(
                tenant_id=tenant_id,
                service_id=service_id,
                connection=connection,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
            )
            base_url = self.get_qbo_api_base(env)

            candidates = self.role_repo.get_export_candidates(
                tenant_id=tenant_id,
                service_id=service_id,
                limit=limit,
                offset=offset,
            )

            summary: Dict[str, Any] = {
                const.KEY_OK: True,
                "tenantId": tenant_id,
                "serviceId": str(service_id),
                "realmIdPresent": bool(realm_id),
                "env": env,
                "requested": len(candidates),
                "mapped from QBO": 0,
                "createdInQbo": 0,
                "updatedInQbo": 0,
                "inactivatedInQbo": 0,
                "skippedInactive": 0,
                "skippedQboNewer": 0,
                "syncFailed": 0,
                "failed": 0,
                const.KEY_FAILURES: [],
                const.KEY_RESULTS: [],
            }

            if not candidates:
                summary[const.KEY_MESSAGE] = "No pending service role export candidates found."
                audit_action, audit_status, audit_context = "service_role_export_completed", "success", "no_candidates"
                return summary

            for item in candidates:
                role = item.get("role") or {}
                mapping = item.get("mapping") or {}
                role_id = int(role.get("id") or 0)
                display_name = self.build_service_item_name(role)
                external_service_id = self._clean(mapping.get("external_service_id"))
                detail = {
                    "fieldcraftRoleId": role_id,
                    "roleName": role.get("name"),
                    const.KEY_DISPLAY_NAME: display_name,
                    const.KEY_STATUS: None,
                    const.KEY_ACTION: None,
                    "qboServiceItemId": external_service_id or None,
                    const.KEY_SYNC_TOKEN: mapping.get("sync_token"),
                    const.KEY_REASON: None,
                    const.KEY_ERROR: None,
                }
                role_income_account = self.get_role_income_account(mapping=mapping, role=role, connection=connection)
                role_income_account_id = self.get_selected_income_account_id(role_income_account)
                detail["serviceIncomeAccount"] = role_income_account
                try:
                    mapping_id = mapping.get("id")
                    if not mapping_id:
                        raise Exception("Missing role external map row for export candidate")
                    if not role_id:
                        raise Exception("Role id is missing on export candidate")
                    if not display_name:
                        raise Exception("Role name is missing. Add a role name before syncing to QuickBooks.")

                    role_active = bool(role.get("active"))
                    if not role_active and not external_service_id:
                        reason = self._build_reason(
                            "Inactive role was not exported because it has no mapped QuickBooks service item.",
                            code="INACTIVE_ROLE_NOT_EXPORTED",
                        )
                        self.role_repo.close_mapping_as_invalid(
                            tenant_id=tenant_id,
                            mapping_id=int(mapping_id),
                            reason=reason,
                        )
                        summary["skippedInactive"] += 1
                        detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "inactive role skipped", const.KEY_REASON: reason})
                        summary[const.KEY_RESULTS].append(detail)
                        continue

                    if not external_service_id:
                        found_item = self.find_service_item_in_qbo(
                            access_token=access_token,
                            base_url=base_url,
                            realm_id=str(realm_id),
                            name=display_name,
                        )
                        if found_item and found_item.get(const.QBO_KEY_ID):
                            found_id = self._clean(found_item.get(const.QBO_KEY_ID))
                            if self._is_deleted_qbo_service_item(found_item):
                                reason = self._build_qbo_service_inactive_reason(role=role, qbo_item=found_item)
                                self.role_repo.mark_mapping_failed(
                                    tenant_id=tenant_id,
                                    mapping_id=int(mapping_id),
                                    status=self.role_repo.STATUS_SYNC_FAILED,
                                    reason=reason,
                                    sync_token=self._clean(found_item.get(const.QBO_KEY_SYNC_TOKEN)) or None,
                                )
                                summary["syncFailed"] += 1
                                summary["failed"] += 1
                                detail.update({
                                    const.KEY_STATUS: "failed",
                                    const.KEY_ACTION: "qboServiceInactive",
                                    "qboServiceItemId": found_id,
                                    const.KEY_SYNC_TOKEN: self._clean(found_item.get(const.QBO_KEY_SYNC_TOKEN)) or None,
                                    const.KEY_REASON: reason,
                                    const.KEY_ERROR: "QuickBooks service item is inactive or deleted.",
                                })
                                qbo_api.append_failure(summary, {
                                    "roleId": role_id,
                                    "externalServiceId": found_id,
                                    const.KEY_ERROR: "QuickBooks service item is inactive or deleted.",
                                    const.KEY_REASON: reason,
                                })
                                summary[const.KEY_RESULTS].append(detail)
                                continue

                            self._assert_external_service_not_mapped_elsewhere(
                                tenant_id=tenant_id,
                                service_id=service_id,
                                external_service_id=found_id,
                                role_id=role_id,
                            )
                            updated = self.update_service_item_in_qbo(
                                access_token=access_token,
                                base_url=base_url,
                                realm_id=str(realm_id),
                                item_id=found_id,
                                payload=self.build_qbo_service_item_payload(
                                    role=role,
                                    income_account_id=role_income_account_id,
                                    include_type=False,
                                ),
                                existing_qbo_item=found_item,
                            )
                            updated_id = self._clean(updated.get(const.QBO_KEY_ID)) or found_id
                            updated_token = self._clean(updated.get(const.QBO_KEY_SYNC_TOKEN)) or None
                            qbo_name = self._clean(updated.get("Name")) or display_name
                            reason = self._build_reason(
                                "Role service item already existed in QuickBooks, so it was linked and updated from FIELDCRAFT.",
                                code="ROLE_MAPPED_TO_EXISTING_QBO_SERVICE_ITEM",
                            )
                            self.role_repo.mark_mapping_synced(
                                tenant_id=tenant_id,
                                mapping_id=int(mapping_id),
                                external_service_id=updated_id,
                                external_account_id=str(realm_id),
                                external_display_name=display_name,
                                sync_token=updated_token,
                                reason=reason,
                                additional_info=self.build_role_income_account_additional_info(role_income_account),
                            )
                            summary["mapped from QBO"] += 1
                            detail.update({
                                const.KEY_STATUS: "success",
                                const.KEY_ACTION: "mapped from QBO",
                                "qboServiceItemId": updated_id,
                                const.KEY_SYNC_TOKEN: updated_token,
                                const.KEY_REASON: reason,
                            })
                            summary[const.KEY_RESULTS].append(detail)
                            continue

                        if not role_income_account_id:
                            raise Exception(self.build_missing_income_account_message(display_name))
                        created = self.create_service_item_in_qbo(
                            access_token=access_token,
                            base_url=base_url,
                            realm_id=str(realm_id),
                            payload=self.build_qbo_service_item_payload(role=role, income_account_id=role_income_account_id),
                        )
                        created_id = self._clean(created.get(const.QBO_KEY_ID))
                        created_token = self._clean(created.get(const.QBO_KEY_SYNC_TOKEN)) or None
                        qbo_name = self._clean(created.get("Name")) or display_name
                        if not created_id:
                            raise Exception(f"Create QuickBooks service item returned unexpected response: {created}")
                        reason = self._build_reason(
                            "Role was created in QuickBooks as a service item and synced successfully.",
                            code="ROLE_CREATED_IN_QBO_SERVICE_ITEM",
                        )
                        self.role_repo.mark_mapping_synced(
                            tenant_id=tenant_id,
                            mapping_id=int(mapping_id),
                            external_service_id=created_id,
                            external_account_id=str(realm_id),
                            external_display_name=display_name,
                            sync_token=created_token,
                            reason=reason,
                            additional_info=self.build_role_income_account_additional_info(role_income_account),
                        )
                        self._audit_service_role_event(
                            tenant_id=tenant_id,
                            action=AUDIT_ACTION_SERVICE_ROLE_CREATED_IN_QBO,
                            resource_id=created_id,
                            context=f"fieldcraftRoleId={role_id}",
                            user_id=object_id,
                            email=email,
                            user_role=user_role,
                        )
                        summary["createdInQbo"] += 1
                        detail.update({
                            const.KEY_STATUS: "success",
                            const.KEY_ACTION: "created in QBO",
                            "qboServiceItemId": created_id,
                            const.KEY_SYNC_TOKEN: created_token,
                            const.KEY_REASON: reason,
                        })
                        summary[const.KEY_RESULTS].append(detail)
                        continue

                    self._assert_external_service_not_mapped_elsewhere(
                        tenant_id=tenant_id,
                        service_id=service_id,
                        external_service_id=external_service_id,
                        role_id=role_id,
                    )
                    current = self.get_service_item_in_qbo(
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=str(realm_id),
                        item_id=external_service_id,
                    )
                    self._assert_qbo_item_is_service(current, item_id=external_service_id)
                    if self._is_deleted_qbo_service_item(current):
                        qbo_token = self._clean(current.get(const.QBO_KEY_SYNC_TOKEN)) or mapping.get("sync_token")
                        qbo_name = self._clean(current.get("Name")) or mapping.get("external_display_name") or display_name
                        if role_active:
                            reason = self._build_qbo_service_inactive_reason(role=role, qbo_item=current)
                            self.role_repo.mark_mapping_failed(
                                tenant_id=tenant_id,
                                mapping_id=int(mapping_id),
                                status=self.role_repo.STATUS_SYNC_FAILED,
                                reason=reason,
                                sync_token=qbo_token,
                            )
                            summary["skippedQboNewer"] += 1
                            summary["syncFailed"] += 1
                            summary["failed"] += 1
                            detail.update({
                                const.KEY_STATUS: "failed",
                                const.KEY_ACTION: "qboServiceInactive",
                                "qboServiceItemId": external_service_id,
                                const.KEY_SYNC_TOKEN: qbo_token,
                                const.KEY_REASON: reason,
                                const.KEY_ERROR: "QuickBooks service item is inactive or deleted.",
                            })
                            qbo_api.append_failure(summary, {
                                "roleId": role_id,
                                "externalServiceId": external_service_id,
                                const.KEY_ERROR: "QuickBooks service item is inactive or deleted.",
                                const.KEY_REASON: reason,
                            })
                            summary[const.KEY_RESULTS].append(detail)
                            continue

                        reason = self._build_reason(
                            "Role service item is already inactive in QuickBooks and synced successfully.",
                            code="ROLE_SERVICE_ALREADY_INACTIVE_IN_QBO",
                        )
                        self.role_repo.mark_mapping_synced(
                            tenant_id=tenant_id,
                            mapping_id=int(mapping_id),
                            external_service_id=external_service_id,
                            external_account_id=str(realm_id),
                            external_display_name=display_name,
                            sync_token=qbo_token,
                            reason=reason,
                            additional_info=self.build_role_income_account_additional_info(role_income_account),
                        )
                        summary["inactivatedInQbo"] += 1
                        detail.update({
                            const.KEY_STATUS: "success",
                            const.KEY_ACTION: "already inactive in QBO",
                            "qboServiceItemId": external_service_id,
                            const.KEY_SYNC_TOKEN: qbo_token,
                            const.KEY_REASON: reason,
                        })
                        summary[const.KEY_RESULTS].append(detail)
                        continue

                    if self._qbo_service_name_changed_in_qbo(role=role, qbo_item=current):
                        reason = self._build_qbo_service_changed_reason(role=role, qbo_item=current)
                        self.role_repo.mark_mapping_failed(
                            tenant_id=tenant_id,
                            mapping_id=int(mapping_id),
                            status=self.role_repo.STATUS_SYNC_FAILED,
                            reason=reason,
                            sync_token=self._clean(current.get(const.QBO_KEY_SYNC_TOKEN)) or mapping.get("sync_token"),
                        )
                        summary["skippedQboNewer"] += 1
                        summary["syncFailed"] += 1
                        summary["failed"] += 1
                        detail.update({
                            const.KEY_STATUS: "failed",
                            const.KEY_ACTION: "qboServiceItemChanged",
                            "qboServiceItemId": external_service_id,
                            const.KEY_SYNC_TOKEN: self._clean(current.get(const.QBO_KEY_SYNC_TOKEN)) or mapping.get("sync_token"),
                            const.KEY_REASON: reason,
                            const.KEY_ERROR: "QuickBooks service item name was changed after the last successful sync.",
                        })
                        qbo_api.append_failure(summary, {
                            "roleId": role_id,
                            "externalServiceId": external_service_id,
                            const.KEY_ERROR: "QuickBooks service item name was changed after the last successful sync.",
                            const.KEY_REASON: reason,
                        })
                        summary[const.KEY_RESULTS].append(detail)
                        continue

                    updated = self.update_service_item_in_qbo(
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=str(realm_id),
                        item_id=external_service_id,
                        payload=self.build_qbo_service_item_payload(
                            role=role,
                            income_account_id=role_income_account_id,
                            include_type=False,
                        ),
                        existing_qbo_item=current,
                    )
                    updated_id = self._clean(updated.get(const.QBO_KEY_ID)) or external_service_id
                    updated_token = self._clean(updated.get(const.QBO_KEY_SYNC_TOKEN)) or None
                    qbo_name = self._clean(updated.get("Name")) or display_name
                    reason = self._build_reason(
                        "Role service item was updated in QuickBooks from FIELDCRAFT and synced successfully.",
                        code="ROLE_UPDATED_IN_QBO_SERVICE_ITEM",
                    )
                    self.role_repo.mark_mapping_synced(
                        tenant_id=tenant_id,
                        mapping_id=int(mapping_id),
                        external_service_id=updated_id,
                        external_account_id=str(realm_id),
                        external_display_name=display_name,
                        sync_token=updated_token,
                        reason=reason,
                        additional_info=self.build_role_income_account_additional_info(role_income_account),
                    )
                    self._audit_service_role_event(
                        tenant_id=tenant_id,
                        action=AUDIT_ACTION_SERVICE_ROLE_UPDATED_IN_QBO,
                        resource_id=updated_id,
                        context=f"fieldcraftRoleId={role_id}",
                        user_id=object_id,
                        email=email,
                        user_role=user_role,
                    )
                    if role_active:
                        summary["updatedInQbo"] += 1
                    else:
                        summary["inactivatedInQbo"] += 1
                    detail.update({
                        const.KEY_STATUS: "success",
                        const.KEY_ACTION: "updated in QBO" if role_active else "inactivated in QBO",
                        "qboServiceItemId": updated_id,
                        const.KEY_SYNC_TOKEN: updated_token,
                        const.KEY_REASON: reason,
                    })
                    summary[const.KEY_RESULTS].append(detail)
                except Exception as exc:
                    logger.exception("[ServiceRoleExport] ERROR processing role_id=%s | %s", role_id, exc)
                    error_message = str(exc).strip() or "Service role export failed. Please review the error and retry sync."
                    error_code = "SERVICE_COA_NOT_SELECTED" if "no chart of account is selected" in error_message.lower() else "ROLE_EXPORT_FAILED"
                    reason = self._build_reason(error_message, code=error_code, error=error_message)
                    if mapping.get("id"):
                        self.role_repo.mark_mapping_failed(
                            tenant_id=tenant_id,
                            mapping_id=int(mapping["id"]),
                            reason=reason,
                        )
                    self._audit_service_role_event(
                        tenant_id=tenant_id,
                        action=AUDIT_ACTION_SERVICE_ROLE_SYNC_FAILED_PERSISTED,
                        status="failed",
                        resource_id=str(mapping.get("external_service_id") or role_id),
                        context=f"fieldcraftRoleId={role_id}",
                        user_id=object_id,
                        email=email,
                        user_role=user_role,
                    )
                    summary["failed"] += 1
                    qbo_api.append_failure(summary, {
                        "roleId": role_id,
                        "externalServiceId": mapping.get("external_service_id") or None,
                        const.KEY_ERROR: str(exc),
                        const.KEY_REASON: reason,
                    })
                    detail.update({const.KEY_STATUS: "failed", const.KEY_ACTION: "error", const.KEY_ERROR: str(exc), const.KEY_REASON: reason})
                    summary[const.KEY_RESULTS].append(detail)

            audit_action = "service_role_export_completed"
            audit_status = "success"
            audit_context = (
                f"created={summary.get('createdInQbo', 0)} updated={summary.get('updatedInQbo', 0)} "
                f"inactivated={summary.get('inactivatedInQbo', 0)} failed={summary.get('failed', 0)}"
            )
            return summary
        except Exception as exc:
            logger.exception("[ServiceRoleExport] FATAL ERROR | tenant_id=%s", tenant_id)
            audit_action = "service_role_export_failed"
            audit_status = "failed"
            audit_context = str(exc)[:128]
            return {const.KEY_OK: False, const.KEY_MESSAGE: "Service role export failed.", const.KEY_ERROR: "Unexpected internal error. See server logs for details."}
        finally:
            safe_audit_log(
                tenant_id=tenant_id,
                action=audit_action,
                user_id=object_id or None,
                email=email or None,
                user_role=user_role or None,
                status=audit_status,
                context=audit_context,
                resource_type="quickbooks_service_role",
            )

    def build_service_item_name(self, role: Dict[str, Any]) -> str:
        return self._clean(role.get("name"))[:100]

    def build_qbo_service_item_payload(
        self,
        *,
        role: Dict[str, Any],
        income_account_id: Optional[str] = None,
        include_type: bool = True,
    ) -> Dict[str, Any]:
        name = self.build_service_item_name(role)
        active = bool(role.get("active"))
        payload: Dict[str, Any] = {
            "Name": name,
            "Active": active,
        }
        if include_type:
            payload["Type"] = "Service"
        description = self._clean(role.get("description"))
        if description:
            payload["Description"] = description[:4000]
        if income_account_id:
            payload["IncomeAccountRef"] = {"value": str(income_account_id)}
        return payload

    def get_selected_income_account(self, connection: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        service_role_sync = connection.get("service_role_sync")
        if isinstance(service_role_sync, dict):
            account = service_role_sync.get("qbo_service_income_account")
            if isinstance(account, dict):
                return account
        account = connection.get("qbo_service_income_account")
        return account if isinstance(account, dict) else None

    def get_role_income_account(
        self,
        *,
        mapping: Dict[str, Any],
        role: Dict[str, Any],
        connection: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self.role_repo.get_mapping_income_account(mapping)

    def get_selected_income_account_id(self, account: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(account, dict):
            return None
        selected = account if "external_id" in account else self.get_selected_income_account(account)
        if not isinstance(selected, dict):
            return None
        return self._clean(selected.get("external_id")) or None

    def build_role_income_account_additional_info(self, account: Dict[str, Any]) -> Dict[str, Any]:
        if not self.get_selected_income_account_id(account):
            return {}
        return {"qbo_service_income_account": dict(account)}

    def build_missing_income_account_message(self, role_name: str) -> str:
        return (
            f"Cannot create QuickBooks service item for role '{role_name}' because no chart of account is selected. "
            "Select a synced QuickBooks chart of account in the Service Sync module, then run service sync again."
        )

    def find_service_item_in_qbo(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        name: str,
    ) -> Optional[Dict[str, Any]]:
        if not name:
            return None
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        query_url = f"{base_url}/v3/company/{realm_id}/query"
        query = f"SELECT * FROM Item WHERE Name = '{escape_qbo_literal(name)}' AND Type = 'Service' STARTPOSITION 1 MAXRESULTS 50"
        resp = qbo_api.qbo_get(query_url, headers=headers, params={"query": query}, timeout=30)
        if resp.status_code != 200:
            raise Exception(qbo_api.build_response_error(resp, "QBO service item lookup failed"))
        items = (qbo_api.safe_json(resp).get("QueryResponse") or {}).get("Item") or []
        for item in items:
            if self._clean(item.get("Name")).lower() == self._clean(name).lower():
                self._assert_qbo_item_is_service(item, item_id=self._clean(item.get(const.QBO_KEY_ID)))
                return item
        return None

    def get_service_item_in_qbo(self, *, access_token: str, base_url: str, realm_id: str, item_id: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/item/{item_id}"
        resp = qbo_api.qbo_get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise Exception(qbo_api.build_response_error(resp, "QBO get service item failed"))
        item = qbo_api.safe_json(resp).get("Item") or {}
        self._assert_qbo_item_is_service(item, item_id=item_id)
        return item

    def create_service_item_in_qbo(self, *, access_token: str, base_url: str, realm_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/item"
        resp = qbo_api.qbo_post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code not in (200, 201):
            raise Exception(qbo_api.build_response_error(resp, "QBO create service item failed"))
        item = qbo_api.safe_json(resp).get("Item") or {}
        self._assert_qbo_item_is_service(item, item_id=self._clean(item.get(const.QBO_KEY_ID)))
        return item

    def update_service_item_in_qbo(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        item_id: str,
        payload: Dict[str, Any],
        existing_qbo_item: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        existing = existing_qbo_item or self.get_service_item_in_qbo(
            access_token=access_token,
            base_url=base_url,
            realm_id=realm_id,
            item_id=item_id,
        )
        sync_token = existing.get(const.QBO_KEY_SYNC_TOKEN)
        if sync_token is None:
            raise Exception("QBO service item update failed: missing SyncToken")
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/item"
        body = {
            const.QBO_KEY_ID: str(item_id),
            const.QBO_KEY_SYNC_TOKEN: str(sync_token),
            "sparse": True,
            **payload,
        }
        resp = qbo_api.qbo_post(url, headers=headers, json=body, timeout=30)
        if resp.status_code not in (200, 201):
            raise Exception(qbo_api.build_response_error(resp, "QBO update service item failed"))
        item = qbo_api.safe_json(resp).get("Item") or {}
        self._assert_qbo_item_is_service(item, item_id=item_id)
        return item

    def _assert_external_service_not_mapped_elsewhere(self, *, tenant_id: str, service_id: str, external_service_id: str, role_id: int) -> None:
        existing = self.role_repo.get_current_mapping_by_external_service_id(
            tenant_id=tenant_id,
            service_id=service_id,
            external_service_id=external_service_id,
        )
        if existing and existing.get("role_id") is not None and int(existing["role_id"]) != int(role_id):
            raise Exception(
                f"QuickBooks service item Id {external_service_id} is already mapped to a different FIELDCRAFT role ({existing.get('role_id')})."
            )

    def _assert_qbo_item_is_service(self, item: Dict[str, Any], *, item_id: Optional[str]) -> None:
        if not item:
            raise Exception(f"QuickBooks service item {item_id or ''} was not found.")
        item_type = self._clean(item.get("Type"))
        if item_type and item_type.lower() != "service":
            raise Exception(f"QuickBooks item {item_id or item.get(const.QBO_KEY_ID)} exists but is Type={item_type}, not Service.")

    def _qbo_service_name_changed_in_qbo(self, *, role: Dict[str, Any], qbo_item: Dict[str, Any]) -> bool:
        fieldcraft_name = self._normalize_name(self.build_service_item_name(role))
        qbo_name = self._normalize_name(qbo_item.get("Name"))
        if fieldcraft_name == qbo_name:
            return False
        qbo_last_updated_at = self._get_qbo_last_updated_at(qbo_item)
        fieldcraft_latest_at = self.role_repo.get_role_latest_local_timestamp(role)
        if not qbo_last_updated_at or not fieldcraft_latest_at:
            return True
        return qbo_last_updated_at > fieldcraft_latest_at

    def _is_deleted_qbo_service_item(self, qbo_item: Dict[str, Any]) -> bool:
        return bool(qbo_item.get("domain") == "deleted" or qbo_item.get("Active") is False)

    def _build_qbo_service_inactive_reason(self, *, role: Dict[str, Any], qbo_item: Dict[str, Any]) -> str:
        fieldcraft_name = self.build_service_item_name(role) or "this role"
        qbo_name = self._clean(qbo_item.get("Name")) or fieldcraft_name
        return self._build_reason(
            (
                f"The QuickBooks service item for role '{fieldcraft_name}' is inactive or deleted. "
                f"Please make the '{fieldcraft_name}' service item active in QuickBooks, then run service sync again. "
                "This is required so time activity and bill sync can use the service item smoothly."
            ),
            code="QBO_SERVICE_ITEM_INACTIVE",
            fieldcraft_role_name=fieldcraft_name,
            qbo_service_item_name=qbo_name,
            action_required=f"Activate the '{fieldcraft_name}' service item in QuickBooks, then run service sync again.",
        )

    def _build_qbo_service_changed_reason(self, *, role: Dict[str, Any], qbo_item: Dict[str, Any]) -> str:
        fieldcraft_name = self.build_service_item_name(role) or "this role"
        qbo_name = self._clean(qbo_item.get("Name")) or "the QuickBooks service item"
        return self._build_reason(
            (
                f"The QuickBooks service item name does not match the FIELDCRAFT role '{fieldcraft_name}'. "
                f"QuickBooks currently shows '{qbo_name}'. "
                f"Please rename the QuickBooks service item to '{fieldcraft_name}', then run service sync again."
            ),
            code="QBO_SERVICE_ITEM_NAME_CHANGED",
            fieldcraft_role_name=fieldcraft_name,
            qbo_service_item_name=qbo_name,
            action_required=f"Rename the QuickBooks service item to '{fieldcraft_name}', then run service sync again.",
        )

    def _get_qbo_last_updated_at(self, qbo_object: Dict[str, Any]) -> Optional[datetime]:
        metadata = qbo_object.get("MetaData") or {}
        return self._parse_timestamp(metadata.get("LastUpdatedTime") or metadata.get("CreateTime"))

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

    def get_qbo_api_base(self, env: str) -> str:
        from source.Utils import QBODiscoveryClient
        return QBODiscoveryClient.get_qbo_api_base(env)

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()

    def _normalize_name(self, value: Any) -> str:
        return " ".join(self._clean(value).lower().split())

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
