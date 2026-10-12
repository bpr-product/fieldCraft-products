from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils import constants as const
from source.Utils.auditlog import safe_audit_log
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.dal.ScheduledJobRepository import ScheduledJobRepository
from source.dal.ChartOfAccountRepository import ChartOfAccountRepository
from source.dal.ServiceRoleRepository import ServiceRoleRepository
from source.quickbooks.QuickBooksAuthentication import QuickBooksOAuthClient
from source.service.QuickBooksService import QuickBooksService

logger = logging.getLogger(__name__)


class QuickBooksServiceRoleImportService:
    """Read-only validation for mapped QuickBooks service items.

    FIELDCRAFT roles are the source of truth. This service does not import or create
    FIELDCRAFT roles from QuickBooks. It only refreshes already-mapped QBO service
    items and marks a mapping as SYNC_FAILED when the mapped item is inactive
    or renamed in QuickBooks.
    """

    def __init__(self) -> None:
        self.qbo_repo = QuickBooksRepository()
        self.qbo_service = QuickBooksService()
        self.role_repo = ServiceRoleRepository()
        self.coa_repo = ChartOfAccountRepository()
        self.scheduled_job_repo = ScheduledJobRepository()

    @staticmethod
    def _build_reason(message: str, **extra: Any) -> str:
        payload: Dict[str, Any] = {"message": message}
        for key, value in extra.items():
            if value is not None:
                payload[key] = value
        return json.dumps(payload)

    def import_roles(
        self,
        *,
        tenant_id: str,
        service_id: str,
        page_size: int = 100,
        start_position: int = 1,
        max_items: Optional[int] = None,
        changed_since_override: Optional[Any] = None,
        force_full: bool = False,
        object_id: Optional[str] = None,
        email: Optional[str] = None,
        user_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        safe_audit_log(
            tenant_id=tenant_id,
            action="service_role_import_started",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="started",
            resource_type="quickbooks_service_role",
        )
        audit_action = "service_role_import_failed"
        audit_status = "failed"
        audit_context: Optional[str] = None

        summary: Dict[str, Any] = {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": str(service_id),
            "processed": 0,
            "validatedMapped": 0,
            "skippedUnmapped": 0,
            "skippedNonServiceItems": 0,
            "skippedAlreadyCurrent": 0,
            "skippedLocalNewer": 0,
            "skippedDeleted": 0,
            "syncFailed": 0,
            "failed": 0,
            const.KEY_FAILURES: [],
            const.KEY_RESULTS: [],
            "startedAt": datetime.now(timezone.utc).isoformat(),
        }

        try:
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = str(connection.get(const.KEY_REALM_ID) or "")
            env = (connection.get(const.KEY_ENV) or "sandbox").lower()
            conn_status = (connection.get(const.KEY_STATUS) or const.STATUS_CONNECTED).lower()

            if conn_status and conn_status != const.STATUS_CONNECTED:
                audit_action, audit_context = "service_role_import_aborted", f"not_connected status={conn_status}"
                return {const.KEY_OK: False, const.KEY_MESSAGE: f"QuickBooks is not connected (status={conn_status})"}
            if not refresh_token or not realm_id:
                audit_action, audit_context = "service_role_import_aborted", "missing_realmId_or_refreshToken"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks not connected for this tenant (missing realmId/refreshToken)."}

            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)
            if not client_id or not client_secret:
                audit_action, audit_context = "service_role_import_aborted", "missing_credentials"
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

            if use_cdc:
                qbo_items, intuit_tid = self._fetch_qbo_items_cdc(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=realm_id,
                    changed_since=changed_since,
                )
                if max_items is not None:
                    qbo_items = qbo_items[: int(max_items)]
                summary["intuitTid"] = intuit_tid
                for qbo_item in qbo_items:
                    summary["processed"] += 1
                    self._process_qbo_item(summary=summary, tenant_id=tenant_id, service_id=service_id, realm_id=realm_id, qbo_item=qbo_item)
                audit_action, audit_status = "service_role_import_completed", "success"
                audit_context = f"validated={summary['validatedMapped']} syncFailed={summary['syncFailed']} failed={summary['failed']}"
                return summary

            position = max(1, int(start_position or 1))
            processed = 0
            while True:
                if max_items is not None and processed >= int(max_items):
                    break
                batch_size = max(1, int(page_size or 100))
                if max_items is not None:
                    batch_size = min(batch_size, int(max_items) - processed)

                qbo_items, intuit_tid = self._fetch_qbo_items(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=realm_id,
                    start_position=position,
                    max_results=batch_size,
                )
                if not qbo_items:
                    break
                summary["intuitTid"] = intuit_tid
                for qbo_item in qbo_items:
                    processed += 1
                    summary["processed"] += 1
                    self._process_qbo_item(summary=summary, tenant_id=tenant_id, service_id=service_id, realm_id=realm_id, qbo_item=qbo_item)
                position += len(qbo_items)
                if len(qbo_items) < batch_size:
                    break

            audit_action, audit_status = "service_role_import_completed", "success"
            audit_context = f"validated={summary['validatedMapped']} syncFailed={summary['syncFailed']} failed={summary['failed']}"
            return summary
        except Exception as exc:
            logger.exception("[ServiceRoleImport] import failed | tenant_id=%s service_id=%s", tenant_id, service_id)
            summary[const.KEY_OK] = False
            summary[const.KEY_ERROR] = "Unexpected internal error. See server logs for details."
            audit_action, audit_status, audit_context = "service_role_import_failed", "failed", str(exc)[:128]
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
                resource_type="quickbooks_service_role",
            )

    def _process_qbo_item(self, *, summary: Dict[str, Any], tenant_id: str, service_id: str, realm_id: str, qbo_item: Dict[str, Any]) -> None:
        qbo_item_id = self._clean(qbo_item.get(const.QBO_KEY_ID))
        qbo_name = self._clean(qbo_item.get("Name"))
        detail = {
            "qboServiceItemId": qbo_item_id or None,
            const.KEY_DISPLAY_NAME: qbo_name,
            const.KEY_STATUS: None,
            const.KEY_ACTION: None,
            const.KEY_REASON: None,
            const.KEY_ERROR: None,
            const.KEY_SYNC_TOKEN: self._clean(qbo_item.get(const.QBO_KEY_SYNC_TOKEN)) or None,
        }

        try:
            if not qbo_item_id or not self._is_qbo_service_item(qbo_item):
                summary["skippedNonServiceItems"] += 1
                return

            mapping = self.role_repo.get_current_mapping_by_external_service_id(
                tenant_id=tenant_id,
                service_id=service_id,
                external_service_id=qbo_item_id,
            )
            if not mapping:
                # Important: do not import QuickBooks-created services/products.
                summary["skippedUnmapped"] += 1
                return

            role = self.role_repo.get_role_by_id(tenant_id=tenant_id, role_id=int(mapping.get("role_id") or 0))
            detail["fieldcraftRoleId"] = mapping.get("role_id")
            if not role:
                reason = self._build_reason(
                    "Service sync failed because the mapped FIELDCRAFT role could not be found.",
                    code="FIELDCRAFT_ROLE_NOT_FOUND",
                    action_required="Restore the FIELDCRAFT role or remove the invalid QuickBooks service mapping.",
                )
                self._mark_sync_failed(
                    summary=summary,
                    tenant_id=tenant_id,
                    mapping=mapping,
                    detail=detail,
                    reason=reason,
                    error="Mapped FIELDCRAFT role not found.",
                    action="fieldcraftRoleMissing",
                    qbo_item=qbo_item,
                )
                return

            if self._is_deleted_qbo_item(qbo_item):
                summary["skippedDeleted"] += 1
                reason = self._qbo_service_inactive_reason(role=role, qbo_item=qbo_item)
                self._mark_sync_failed(
                    summary=summary,
                    tenant_id=tenant_id,
                    mapping=mapping,
                    detail=detail,
                    reason=reason,
                    error="QuickBooks service item is inactive or deleted.",
                    action="qboServiceInactive",
                    qbo_item=qbo_item,
                )
                return

            name_changed = self._role_name_changed(role=role, qbo_item=qbo_item)
            qbo_changed = self._mapped_qbo_changed(mapping=mapping, qbo_item=qbo_item)
            qbo_last_updated_at = self._get_qbo_last_updated_at(qbo_item)
            local_latest_at = self.role_repo.get_role_latest_local_timestamp(role)

            if name_changed and self._qbo_should_win(qbo_last_updated_at=qbo_last_updated_at, local_latest_at=local_latest_at):
                reason = self._qbo_service_name_changed_reason(role=role, qbo_item=qbo_item)
                self._mark_sync_failed(
                    summary=summary,
                    tenant_id=tenant_id,
                    mapping=mapping,
                    detail=detail,
                    reason=reason,
                    error="QuickBooks service item name was changed after the last successful sync.",
                    action="qboServiceNameChanged",
                    qbo_item=qbo_item,
                )
                return

            if name_changed:
                summary["skippedLocalNewer"] += 1
                reason = self._build_reason(
                    "FIELDCRAFT has the latest role name, so QuickBooks import did not update FIELDCRAFT.",
                    code="ROLE_FIELDCRAFT_NEWER",
                    action_required="Run service sync to push the FIELDCRAFT role name to QuickBooks.",
                )
                detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "localNewer", const.KEY_REASON: reason})
                summary[const.KEY_RESULTS].append(detail)
                return

            if qbo_changed:
                logger.info("[ServiceRoleImport] QBO token changed but role name matches FIELDCRAFT | qbo_item_id=%s", qbo_item_id)

            reason = self._build_reason(
                "Service role is already up to date in FIELDCRAFT and QuickBooks.",
                code="ROLE_SERVICE_ALREADY_CURRENT",
            )
            qbo_account_info = self._build_qbo_income_account_additional_info(
                tenant_id=tenant_id,
                service_id=service_id,
                qbo_item=qbo_item,
                existing_info=mapping.get("additional_info"),
            )
            self.role_repo.mark_mapping_synced(
                tenant_id=tenant_id,
                mapping_id=int(mapping["id"]),
                external_service_id=qbo_item_id,
                external_account_id=realm_id,
                external_display_name=self._clean(role.get("name")) or mapping.get("external_display_name"),
                sync_token=self._clean(qbo_item.get(const.QBO_KEY_SYNC_TOKEN)) or mapping.get("sync_token"),
                reason=reason,
                additional_info=qbo_account_info,
            )
            summary["validatedMapped"] += 1
            summary["skippedAlreadyCurrent"] += 1
            detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "alreadyCurrent", const.KEY_REASON: reason})
            summary[const.KEY_RESULTS].append(detail)
        except Exception as exc:
            logger.exception("[ServiceRoleImport] ERROR processing QBO item id=%s", qbo_item_id)
            summary["failed"] += 1
            reason = self._build_reason("Service role import validation failed. Please review the error and run service sync again.", code="ROLE_IMPORT_VALIDATION_FAILED", error=str(exc))
            detail.update({const.KEY_STATUS: "failed", const.KEY_ACTION: "error", const.KEY_ERROR: str(exc), const.KEY_REASON: reason})
            qbo_api.append_failure(summary, {"qboServiceItemId": qbo_item_id, const.KEY_ERROR: str(exc), const.KEY_REASON: reason})
            summary[const.KEY_RESULTS].append(detail)

    def _mark_sync_failed(
        self,
        *,
        summary: Dict[str, Any],
        tenant_id: str,
        mapping: Dict[str, Any],
        detail: Dict[str, Any],
        reason: str,
        error: str,
        action: str,
        qbo_item: Dict[str, Any],
    ) -> None:
        self.role_repo.mark_mapping_failed(
            tenant_id=tenant_id,
            mapping_id=int(mapping["id"]),
            status=self.role_repo.STATUS_SYNC_FAILED,
            reason=reason,
            sync_token=self._clean(qbo_item.get(const.QBO_KEY_SYNC_TOKEN)) or mapping.get("sync_token"),
        )
        summary["syncFailed"] += 1
        summary["failed"] += 1
        detail.update({const.KEY_STATUS: "failed", const.KEY_ACTION: action, const.KEY_REASON: reason, const.KEY_ERROR: error})
        qbo_api.append_failure(summary, {
            "qboServiceItemId": detail.get("qboServiceItemId"),
            "roleId": mapping.get("role_id"),
            const.KEY_ERROR: error,
            const.KEY_REASON: reason,
        })
        summary[const.KEY_RESULTS].append(detail)

    def import_roles_by_ids(self, *, tenant_id: str, service_id: str, item_ids: List[str]) -> Dict[str, Any]:
        ids = [self._clean(value) for value in (item_ids or []) if self._clean(value)]
        summary: Dict[str, Any] = {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": str(service_id),
            "processed": 0,
            "validatedMapped": 0,
            "skippedUnmapped": 0,
            "skippedNonServiceItems": 0,
            "skippedAlreadyCurrent": 0,
            "skippedLocalNewer": 0,
            "skippedDeleted": 0,
            "syncFailed": 0,
            "failed": 0,
            const.KEY_FAILURES: [],
            const.KEY_RESULTS: [],
        }
        if not ids:
            return summary

        connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
        conn_status = (connection.get(const.KEY_STATUS) or const.STATUS_CONNECTED).lower()
        if conn_status and conn_status != const.STATUS_CONNECTED:
            summary[const.KEY_OK] = False
            summary[const.KEY_MESSAGE] = f"QuickBooks is not connected (status={conn_status})"
            return summary

        refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
        realm_id = str(connection.get(const.KEY_REALM_ID) or "")
        if not refresh_token or not realm_id:
            summary[const.KEY_OK] = False
            summary[const.KEY_MESSAGE] = "QuickBooks not connected for this tenant (missing realmId/refreshToken)."
            return summary

        config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
        client_id = config.get(const.KEY_CLIENT_ID)
        client_secret = config.get(const.KEY_CLIENT_SECRET)
        if not client_id or not client_secret:
            summary[const.KEY_OK] = False
            summary[const.KEY_MESSAGE] = "QuickBooks client credentials missing (ClientId/ClientSecret)."
            return summary

        access_token = self._refresh_access_token(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            tenant_id=tenant_id,
            service_id=service_id,
            connection=connection,
        )
        base_url = self._get_qbo_api_base((connection.get(const.KEY_ENV) or "sandbox").lower())
        for qbo_id in ids:
            qbo_item = self._fetch_qbo_item_by_id(access_token=access_token, base_url=base_url, realm_id=realm_id, item_id=qbo_id)
            summary["processed"] += 1
            self._process_qbo_item(summary=summary, tenant_id=tenant_id, service_id=service_id, realm_id=realm_id, qbo_item=qbo_item)
        return summary

    def _is_qbo_service_item(self, qbo_item: Dict[str, Any]) -> bool:
        return self._clean(qbo_item.get("Type")).lower() == "service"

    def _is_deleted_qbo_item(self, qbo_item: Dict[str, Any]) -> bool:
        return bool(qbo_item.get("domain") == "deleted" or qbo_item.get("Active") is False)

    def _mapped_qbo_changed(self, *, mapping: Dict[str, Any], qbo_item: Dict[str, Any]) -> bool:
        stored_token = self._clean(mapping.get("sync_token"))
        current_token = self._clean(qbo_item.get(const.QBO_KEY_SYNC_TOKEN))
        if stored_token and current_token:
            return stored_token != current_token
        return False

    def _role_name_changed(self, *, role: Dict[str, Any], qbo_item: Dict[str, Any]) -> bool:
        return self._normalize_name(role.get("name")) != self._normalize_name(qbo_item.get("Name"))

    def _qbo_should_win(self, *, qbo_last_updated_at: Optional[datetime], local_latest_at: Optional[datetime]) -> bool:
        if qbo_last_updated_at and local_latest_at:
            return qbo_last_updated_at > local_latest_at
        return qbo_last_updated_at is not None

    def _qbo_service_inactive_reason(self, *, role: Dict[str, Any], qbo_item: Dict[str, Any]) -> str:
        role_name = self._clean(role.get("name")) or "this role"
        qbo_name = self._clean(qbo_item.get("Name")) or role_name
        return self._build_reason(
            (
                f"The QuickBooks service item for role '{role_name}' is inactive or deleted. "
                f"Please make the '{role_name}' service item active in QuickBooks, then run service sync again. "
                "This is required so time activity and bill sync can use the service item smoothly."
            ),
            code="QBO_SERVICE_ITEM_INACTIVE",
            fieldcraft_role_name=role_name,
            qbo_service_item_name=qbo_name,
            action_required=f"Activate the '{role_name}' service item in QuickBooks, then run service sync again.",
        )

    def _qbo_service_name_changed_reason(self, *, role: Dict[str, Any], qbo_item: Dict[str, Any]) -> str:
        role_name = self._clean(role.get("name")) or "this role"
        qbo_name = self._clean(qbo_item.get("Name")) or "the QuickBooks service item"
        return self._build_reason(
            (
                f"The QuickBooks service item name does not match the FIELDCRAFT role '{role_name}'. "
                f"QuickBooks currently shows '{qbo_name}'. "
                f"Please rename the QuickBooks service item to '{role_name}', then run service sync again."
            ),
            code="QBO_SERVICE_ITEM_NAME_CHANGED",
            fieldcraft_role_name=role_name,
            qbo_service_item_name=qbo_name,
            action_required=f"Rename the QuickBooks service item to '{role_name}', then run service sync again.",
        )

    def _get_qbo_last_updated_at(self, qbo_item: Dict[str, Any]) -> Optional[datetime]:
        metadata = qbo_item.get("MetaData") or {}
        return self._parse_timestamp(metadata.get("LastUpdatedTime") or metadata.get("CreateTime"))

    def _parse_timestamp(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            parsed = value
        else:
            raw = str(value).strip().replace("Z", "+00:00")
            if not raw:
                return None
            try:
                parsed = datetime.fromisoformat(raw)
            except ValueError:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _is_cdc_too_old(self, changed_since: datetime) -> bool:
        if changed_since.tzinfo is None:
            changed_since = changed_since.replace(tzinfo=timezone.utc)
        return changed_since < (datetime.now(timezone.utc) - timedelta(days=29))

    def _resolve_changed_since(self, *, tenant_id: str, service_id: str, changed_since_override: Optional[Any], force_full: bool) -> Tuple[Optional[datetime], Optional[str]]:
        if force_full:
            return None, None
        override_dt = self._parse_timestamp(changed_since_override)
        if override_dt:
            return override_dt, "override"
        last_sync = self.scheduled_job_repo.get_last_successful_module_sync_start(
            tenant_id=tenant_id,
            job_type="QBO_SERVICE_ROLE_SYNC",
            service_id=str(service_id),
        )
        return (last_sync, "lastSuccessfulServiceRoleModuleSyncStart") if last_sync else (None, None)

    def _fetch_qbo_items_cdc(self, *, access_token: str, base_url: str, realm_id: str, changed_since: datetime) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        if changed_since.tzinfo is None:
            changed_since = changed_since.replace(tzinfo=timezone.utc)
        changed_since_value = changed_since.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        url = f"{base_url}/v3/company/{realm_id}/cdc"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "text/plain"}
        resp = qbo_api.qbo_get(url, headers=headers, params={"entities": "Item", "changedSince": changed_since_value}, timeout=60)
        if resp.status_code != 200:
            raise Exception(qbo_api.build_response_error(resp, "QBO service item CDC failed"))
        data = qbo_api.safe_json(resp)
        by_id: Dict[str, Dict[str, Any]] = {}
        for cdc_block in data.get("CDCResponse") or []:
            for query_response in cdc_block.get("QueryResponse") or []:
                for item in query_response.get("Item") or []:
                    item_id = self._clean(item.get(const.QBO_KEY_ID))
                    if item_id:
                        by_id[item_id] = item
        return list(by_id.values()), qbo_api.extract_intuit_tid(resp)

    def _fetch_qbo_items(self, *, access_token: str, base_url: str, realm_id: str, start_position: int, max_results: int) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        url = f"{base_url}/v3/company/{realm_id}/query"
        query = f"select * from Item WHERE Type = 'Service' startposition {int(start_position)} maxresults {int(max_results)}"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        resp = qbo_api.qbo_get(url, headers=headers, params={"query": query}, timeout=60)
        if resp.status_code != 200:
            raise Exception(qbo_api.build_response_error(resp, "QBO service item query failed"))
        data = qbo_api.safe_json(resp)
        items = (data.get("QueryResponse") or {}).get("Item") or []
        return items if isinstance(items, list) else [], qbo_api.extract_intuit_tid(resp)

    def _fetch_qbo_item_by_id(self, *, access_token: str, base_url: str, realm_id: str, item_id: str) -> Dict[str, Any]:
        url = f"{base_url}/v3/company/{realm_id}/item/{item_id}"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        resp = qbo_api.qbo_get(url, headers=headers, timeout=60)
        if resp.status_code != 200:
            raise Exception(qbo_api.build_response_error(resp, "QBO get service item failed"))
        return (qbo_api.safe_json(resp) or {}).get("Item") or {}


    def _build_qbo_income_account_additional_info(
        self,
        *,
        tenant_id: str,
        service_id: str,
        qbo_item: Dict[str, Any],
        existing_info: Any = None,
    ) -> Dict[str, Any]:
        """Build role_external_map.additional_info from the current QBO Item.

        When a mapped service item is changed directly in QuickBooks and the
        service item still matches the FIELDCRAFT role, keep the map current by saving
        the latest QBO income account snapshot.
        """
        info = self._load_json(existing_info)
        account_ref = qbo_item.get("IncomeAccountRef") or {}
        external_id = self._clean(account_ref.get("value"))
        if not external_id:
            return info

        account = None
        try:
            account = self.coa_repo.get_by_external_id_and_service(
                tenant_id,
                external_id=external_id,
                service_id=str(service_id),
            )
        except Exception:
            logger.warning(
                "[ServiceRoleImport] Unable to resolve QBO account ref from FIELDCRAFT COA | tenant_id=%s service_id=%s external_id=%s",
                tenant_id,
                service_id,
                external_id,
                exc_info=True,
            )

        if account:
            snapshot = {
                "chart_of_account_id": account.get("id"),
                "external_id": account.get("external_id"),
                "name": account.get("name") or account_ref.get("name"),
                "account_type": account.get("account_type"),
                "detail_type": account.get("detail_type"),
            }
        else:
            snapshot = {
                "chart_of_account_id": None,
                "external_id": external_id,
                "name": self._clean(account_ref.get("name")) or f"QuickBooks account {external_id}",
                "account_type": None,
                "detail_type": None,
            }

        info["qbo_service_income_account"] = {key: value for key, value in snapshot.items() if value not in (None, "")}
        return info

    def _load_json(self, value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        raw = self._clean(value)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _get_qbo_api_base(self, env: str) -> str:
        from source.Utils import QBODiscoveryClient
        return QBODiscoveryClient.get_qbo_api_base(env)

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
        if not client_id or not client_secret or not refresh_token:
            raise Exception("QuickBooks client credentials or refresh token missing.")
        return QuickBooksOAuthClient(repository=self.qbo_repo).refresh_access_token(
            tenant_id=str(tenant_id),
            service_id=str(service_id),
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            token_url=self.qbo_service.token_url,
            current_connection=connection,
        )

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()

    def _normalize_name(self, value: Any) -> str:
        return " ".join(self._clean(value).lower().split())
