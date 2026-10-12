from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils import constants as const
from source.Utils.auditlog import safe_audit_log
from source.dal.ProjectRepository import ProjectRepository
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.dal.ScheduledJobRepository import ScheduledJobRepository
from source.quickbooks.QuickBooksAuthentication import QuickBooksOAuthClient
from source.service.QuickBooksService import QuickBooksService

logger = logging.getLogger(__name__)


class QuickBooksProjectImportService:
    """Read-only validation for QuickBooks project sub-customers.

    FIELDCRAFT is the source of truth for projects. This service never creates or
    updates FIELDCRAFT projects from QuickBooks. It only validates already-mapped QBO
    sub-customers and marks the mapping as SYNC_FAILED when QBO has the newer
    change.
    """

    def __init__(self) -> None:
        self.qbo_repo = QuickBooksRepository()
        self.qbo_service = QuickBooksService()
        self.project_repo = ProjectRepository()
        self.scheduled_job_repo = ScheduledJobRepository()

    @staticmethod
    def _build_reason(message: str, **extra: Any) -> str:
        payload: Dict[str, Any] = {"message": message}
        for key, value in extra.items():
            if value is not None:
                payload[key] = value
        return json.dumps(payload)

    def import_projects(
        self,
        *,
        tenant_id: str,
        service_id: str,
        page_size: int = 100,
        start_position: int = 1,
        max_projects: Optional[int] = None,
        changed_since_override: Optional[Any] = None,
        force_full: bool = False,
        object_id: Optional[str] = None,
        email: Optional[str] = None,
        user_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        safe_audit_log(
            tenant_id=tenant_id,
            action="project_import_started",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="started",
            resource_type="quickbooks_project",
        )
        audit_action = "project_import_failed"
        audit_status = "failed"
        audit_context: Optional[str] = None

        summary: Dict[str, Any] = {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": str(service_id),
            "processed": 0,
            "validatedMapped": 0,
            "skippedNonProjects": 0,
            "skippedUnmapped": 0,
            "skippedDeleted": 0,
            "skippedAlreadyCurrent": 0,
            "skippedLocalNewer": 0,
            "syncFailed": 0,
            "failed": 0,
            const.KEY_FAILURES: [],
            const.KEY_RESULTS: [],
            "startedAt": datetime.now(timezone.utc).isoformat(),
        }

        try:
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}

            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = (connection.get(const.KEY_ENV) or "sandbox").lower()
            conn_status = (connection.get(const.KEY_STATUS) or const.STATUS_CONNECTED).lower()

            if conn_status and conn_status != const.STATUS_CONNECTED:
                audit_action, audit_context = "project_import_aborted", f"not_connected status={conn_status}"
                return {const.KEY_OK: False, const.KEY_MESSAGE: f"QuickBooks is not connected (status={conn_status})"}
            if not refresh_token or not realm_id:
                audit_action, audit_context = "project_import_aborted", "missing_realmId_or_refreshToken"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks not connected for this tenant (missing realmId/refreshToken)."}

            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)
            if not client_id or not client_secret:
                audit_action, audit_context = "project_import_aborted", "missing_credentials"
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
                qbo_customers, intuit_tid = self._fetch_qbo_customers_cdc(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=str(realm_id),
                    changed_since=changed_since,
                )
                if max_projects is not None:
                    qbo_customers = qbo_customers[: int(max_projects)]
                summary["intuitTid"] = intuit_tid
                for qbo_customer in qbo_customers:
                    summary["processed"] += 1
                    self._process_qbo_customer(summary=summary, tenant_id=tenant_id, service_id=service_id, realm_id=str(realm_id), qbo_customer=qbo_customer)
                audit_action, audit_status = "project_import_completed", "success"
                audit_context = f"validated={summary['validatedMapped']} syncFailed={summary['syncFailed']} failed={summary['failed']}"
                return summary

            position = max(1, int(start_position or 1))
            processed = 0
            while True:
                if max_projects is not None and processed >= int(max_projects):
                    break
                batch_size = max(1, int(page_size or 100))
                if max_projects is not None:
                    batch_size = min(batch_size, int(max_projects) - processed)

                qbo_customers, intuit_tid = self._fetch_qbo_customers(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=str(realm_id),
                    start_position=position,
                    max_results=batch_size,
                )
                if not qbo_customers:
                    break
                summary["intuitTid"] = intuit_tid
                for qbo_customer in qbo_customers:
                    processed += 1
                    summary["processed"] += 1
                    self._process_qbo_customer(summary=summary, tenant_id=tenant_id, service_id=service_id, realm_id=str(realm_id), qbo_customer=qbo_customer)
                position += len(qbo_customers)
                if len(qbo_customers) < batch_size:
                    break

            audit_action, audit_status = "project_import_completed", "success"
            audit_context = f"validated={summary['validatedMapped']} syncFailed={summary['syncFailed']} failed={summary['failed']}"
            return summary
        except Exception as exc:
            logger.exception("[ProjectImport] import failed | tenant_id=%s service_id=%s", tenant_id, service_id)
            summary[const.KEY_OK] = False
            summary[const.KEY_ERROR] = "Unexpected internal error. See server logs for details."
            audit_action, audit_status, audit_context = "project_import_failed", "failed", str(exc)[:128]
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
                resource_type="quickbooks_project",
            )

    def _process_qbo_customer(self, *, summary: Dict[str, Any], tenant_id: str, service_id: str, realm_id: str, qbo_customer: Dict[str, Any]) -> None:
        qbo_project_id = self._clean(qbo_customer.get(const.QBO_KEY_ID))
        display_name = self._clean(qbo_customer.get(const.QBO_KEY_DISPLAY_NAME))
        detail = {
            "qboProjectId": qbo_project_id or None,
            const.KEY_DISPLAY_NAME: display_name,
            const.KEY_STATUS: None,
            const.KEY_ACTION: None,
            const.KEY_REASON: None,
            const.KEY_ERROR: None,
            const.KEY_SYNC_TOKEN: self._clean(qbo_customer.get(const.QBO_KEY_SYNC_TOKEN)) or None,
        }

        try:
            if not qbo_project_id or not self._is_qbo_sub_customer(qbo_customer):
                summary["skippedNonProjects"] += 1
                return

            mapping = self.project_repo.get_current_mapping_by_external_project_id(
                tenant_id=tenant_id,
                service_id=service_id,
                external_project_id=qbo_project_id,
            )
            if not mapping:
                summary["skippedUnmapped"] += 1
                return

            project = self.project_repo.get_active_project_by_id(
                tenant_id=tenant_id,
                project_id=int(mapping.get("project_id") or 0),
            )
            detail["fieldcraftProjectId"] = mapping.get("project_id")
            if not project:
                reason = self._build_reason("Project sync failed because the mapped FIELDCRAFT project could not be found.", code="FIELDCRAFT_PROJECT_NOT_FOUND")
                self._mark_sync_failed(summary=summary, tenant_id=tenant_id, mapping=mapping, detail=detail, reason=reason, error="Mapped FIELDCRAFT project not found.", action="fieldcraftProjectMissing")
                return

            if self._is_deleted_qbo_customer(qbo_customer):
                summary["skippedDeleted"] += 1
                reason = self._build_reason("This QuickBooks project is inactive or deleted, so project sync requires review.", code="QBO_PROJECT_INACTIVE")
                self._mark_sync_failed(summary=summary, tenant_id=tenant_id, mapping=mapping, detail=detail, reason=reason, error="QuickBooks project is inactive or deleted.", action="qboProjectInactive")
                return

            project_name_changed = self._project_name_changed(project=project, qbo_customer=qbo_customer)
            qbo_changed = self._mapped_qbo_changed(mapping=mapping, qbo_customer=qbo_customer)
            qbo_last_updated_at = self._get_qbo_last_updated_at(qbo_customer)
            local_latest_at = self.project_repo.get_project_latest_local_timestamp(project, mapping)

            if project_name_changed and self._qbo_should_win(qbo_last_updated_at=qbo_last_updated_at, local_latest_at=local_latest_at):
                reason = self._qbo_project_changed_reason(project=project, qbo_customer=qbo_customer)
                self._mark_sync_failed(summary=summary, tenant_id=tenant_id, mapping=mapping, detail=detail, reason=reason, error="QuickBooks project was changed after the last successful sync.", action="qboProjectChanged")
                return

            if project_name_changed:
                summary["skippedLocalNewer"] += 1
                reason = self._build_reason("FIELDCRAFT has the latest project change, so QuickBooks import did not update FIELDCRAFT.", code="PROJECT_FIELDCRAFT_NEWER")
                detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "localNewer", const.KEY_REASON: reason})
                summary[const.KEY_RESULTS].append(detail)
                return

            if qbo_changed:
                # QBO SyncToken can change for metadata or other fields. For project
                # conflict handling we only block when the project display name is
                # different and QBO is newer. If the names match, keep the mapping
                # current by saving the latest QBO token below.
                logger.info("[ProjectImport] QBO token changed but project name matches FIELDCRAFT | qbo_project_id=%s", qbo_project_id)

            reason = self._build_reason("Project is already up to date in FIELDCRAFT and QuickBooks.", code="PROJECT_ALREADY_CURRENT")
            self.project_repo.mark_mapping_synced(
                tenant_id=tenant_id,
                mapping_id=int(mapping["id"]),
                external_project_id=qbo_project_id,
                external_account_id=realm_id,
                sync_token=self._clean(qbo_customer.get(const.QBO_KEY_SYNC_TOKEN)) or mapping.get("sync_token"),
                reason=reason,
            )
            summary["validatedMapped"] += 1
            summary["skippedAlreadyCurrent"] += 1
            detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "alreadyCurrent", const.KEY_REASON: reason})
            summary[const.KEY_RESULTS].append(detail)
        except Exception as exc:
            logger.exception("[ProjectImport] failed processing qbo_project_id=%s", qbo_project_id)
            summary["failed"] += 1
            error_message = str(exc).strip() or "Project validation failed. Please review the error and retry sync."
            reason = self._build_reason(error_message, code="PROJECT_IMPORT_VALIDATION_FAILED", error=error_message)
            detail.update({const.KEY_STATUS: "failed", const.KEY_ACTION: "error", const.KEY_REASON: reason, const.KEY_ERROR: str(exc)})
            qbo_api.append_failure(summary, {"qboProjectId": qbo_project_id, const.KEY_ERROR: str(exc), const.KEY_REASON: reason})
            summary[const.KEY_RESULTS].append(detail)

    def _mark_sync_failed(self, *, summary: Dict[str, Any], tenant_id: str, mapping: Dict[str, Any], detail: Dict[str, Any], reason: str, error: str, action: str) -> None:
        self.project_repo.mark_mapping_failed(
            tenant_id=tenant_id,
            mapping_id=int(mapping["id"]),
            status=self.project_repo.STATUS_SYNC_FAILED,
            reason=reason,
        )
        summary["syncFailed"] += 1
        summary["failed"] += 1
        detail.update({const.KEY_STATUS: "failed", const.KEY_ACTION: action, const.KEY_REASON: reason, const.KEY_ERROR: error})
        qbo_api.append_failure(summary, {"qboProjectId": detail.get("qboProjectId"), "projectId": mapping.get("project_id"), const.KEY_ERROR: error, const.KEY_REASON: reason})
        summary[const.KEY_RESULTS].append(detail)

    def import_projects_by_ids(self, *, tenant_id: str, service_id: str, project_ids: List[str]) -> Dict[str, Any]:
        ids = [self._clean(value) for value in (project_ids or []) if self._clean(value)]
        summary: Dict[str, Any] = {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": str(service_id),
            "processed": 0,
            "validatedMapped": 0,
            "skippedNonProjects": 0,
            "skippedUnmapped": 0,
            "skippedDeleted": 0,
            "skippedAlreadyCurrent": 0,
            "skippedLocalNewer": 0,
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
            logger.warning("Tenant %s not connected to QBO - skipping import_projects_by_ids", tenant_id)
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
            qbo_customer = self._fetch_qbo_customer_by_id(access_token=access_token, base_url=base_url, realm_id=realm_id, customer_id=qbo_id)
            summary["processed"] += 1
            self._process_qbo_customer(summary=summary, tenant_id=tenant_id, service_id=service_id, realm_id=realm_id, qbo_customer=qbo_customer)
        return summary

    def _is_qbo_sub_customer(self, qbo_customer: Dict[str, Any]) -> bool:
        return bool(qbo_customer.get("Job")) or bool(qbo_customer.get("ParentRef"))

    def _is_deleted_qbo_customer(self, qbo_customer: Dict[str, Any]) -> bool:
        return bool(qbo_customer.get("domain") == "deleted" or qbo_customer.get("Active") is False)

    def _mapped_qbo_changed(self, *, mapping: Dict[str, Any], qbo_customer: Dict[str, Any]) -> bool:
        stored_token = self._clean(mapping.get("sync_token"))
        current_token = self._clean(qbo_customer.get(const.QBO_KEY_SYNC_TOKEN))
        if stored_token and current_token:
            return stored_token != current_token
        return False

    def _project_name_changed(self, *, project: Dict[str, Any], qbo_customer: Dict[str, Any]) -> bool:
        return self._normalize_name(self._project_display_name(project)) != self._normalize_name(qbo_customer.get(const.QBO_KEY_DISPLAY_NAME))

    def _qbo_should_win(self, *, qbo_last_updated_at: Optional[datetime], local_latest_at: Optional[datetime]) -> bool:
        if qbo_last_updated_at and local_latest_at:
            return qbo_last_updated_at > local_latest_at
        return qbo_last_updated_at is not None

    def _is_qbo_newer(self, qbo_last_updated_at: Optional[datetime], local_latest_at: Optional[datetime]) -> bool:
        return bool(qbo_last_updated_at and local_latest_at and qbo_last_updated_at > local_latest_at)

    def _qbo_project_changed_reason(self, *, project: Dict[str, Any], qbo_customer: Dict[str, Any]) -> str:
        fieldcraft_name = self._project_display_name(project)
        qbo_name = self._clean(qbo_customer.get(const.QBO_KEY_DISPLAY_NAME)) or "the QuickBooks project"
        return self._build_reason(
            (
                "Project sync failed because the QuickBooks project was changed after the last successful sync. "
                "FIELDCRAFT is the source of truth for projects. "
                f"FIELDCRAFT currently has {fieldcraft_name}, while QuickBooks has {qbo_name}. "
                "Update the project name in QuickBooks to match FIELDCRAFT, then run project sync again."
            ),
            code="QBO_PROJECT_CHANGED",
            fieldcraft_project_name=fieldcraft_name,
            qbo_project_name=qbo_name,
            action_required="Update the QuickBooks project name to match the FIELDCRAFT project name, then run project sync again.",
        )

    def _project_display_name(self, project: Dict[str, Any]) -> str:
        return self._clean(project.get("description")) or self._clean(project.get("order_no")) or f"Project {project.get('id')}"

    def _normalize_name(self, value: Any) -> str:
        return " ".join(self._clean(value).lower().split())

    def _get_qbo_last_updated_at(self, qbo_customer: Dict[str, Any]) -> Optional[datetime]:
        metadata = qbo_customer.get("MetaData") or {}
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
            job_type="QBO_PROJECT_SYNC",
            service_id=str(service_id),
        )
        return (last_sync, "lastSuccessfulProjectModuleSyncStart") if last_sync else (None, None)

    def _fetch_qbo_customers_cdc(self, *, access_token: str, base_url: str, realm_id: str, changed_since: datetime) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        if changed_since.tzinfo is None:
            changed_since = changed_since.replace(tzinfo=timezone.utc)
        changed_since_value = changed_since.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        url = f"{base_url}/v3/company/{realm_id}/cdc"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "text/plain"}
        resp = qbo_api.qbo_get(url, headers=headers, params={"entities": "Customer", "changedSince": changed_since_value}, timeout=60)
        if resp.status_code != 200:
            raise Exception(qbo_api.build_response_error(resp, "QBO project CDC failed"))
        data = qbo_api.safe_json(resp)
        by_id: Dict[str, Dict[str, Any]] = {}
        for cdc_block in data.get("CDCResponse") or []:
            for query_response in cdc_block.get("QueryResponse") or []:
                for customer in query_response.get("Customer") or []:
                    customer_id = self._clean(customer.get(const.QBO_KEY_ID))
                    if customer_id:
                        by_id[customer_id] = customer
        return list(by_id.values()), qbo_api.extract_intuit_tid(resp)

    def _fetch_qbo_customers(self, *, access_token: str, base_url: str, realm_id: str, start_position: int, max_results: int) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        url = f"{base_url}/v3/company/{realm_id}/query"
        query = f"select * from Customer startposition {int(start_position)} maxresults {int(max_results)}"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        resp = qbo_api.qbo_get(url, headers=headers, params={"query": query}, timeout=60)
        if resp.status_code != 200:
            raise Exception(qbo_api.build_response_error(resp, "QBO project query failed"))
        data = qbo_api.safe_json(resp)
        customers = (data.get("QueryResponse") or {}).get("Customer") or []
        return customers if isinstance(customers, list) else [], qbo_api.extract_intuit_tid(resp)

    def _fetch_qbo_customer_by_id(self, *, access_token: str, base_url: str, realm_id: str, customer_id: str) -> Dict[str, Any]:
        url = f"{base_url}/v3/company/{realm_id}/customer/{customer_id}"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        resp = qbo_api.qbo_get(url, headers=headers, timeout=60)
        if resp.status_code != 200:
            raise Exception(qbo_api.build_response_error(resp, "QBO get project failed"))
        return (qbo_api.safe_json(resp) or {}).get("Customer") or {}

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
