from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils import constants as const
from source.Utils.auditlog import safe_audit_log
from source.dal.CustomerRepository import CustomerRepository
from source.dal.ProjectRepository import ProjectRepository
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.quickbooks.QuickBooksAuthentication import QuickBooksOAuthClient
from source.quickbooks.QuickBooksCapabilityService import QBOFeatureUnavailableError, QuickBooksCapabilityService
from source.quickbooks.QuickBooksProjectApiClient import QuickBooksProjectApiClient
from source.quickbooks.QuickBooksProjectAssigneeResolver import QuickBooksProjectAssigneeResolver
from source.quickbooks.qbo_query_utils import escape_qbo_literal
from source.service.QuickBooksService import QuickBooksService

logger = logging.getLogger(__name__)

AUDIT_ACTION_PROJECT_CREATED_IN_QBO = "project_created_in_qbo"
AUDIT_ACTION_PROJECT_SYNC_FAILED_PERSISTED = "project_sync_failed_persisted"


class QuickBooksProjectExportService:
    """Export new FIELDCRAFT projects to QuickBooks Projects or sub-customers.

    Rules:
      - FIELDCRAFT is the source of truth for projects.
      - This phase is create-only. Once a project is linked to QuickBooks,
        project sync leaves that QuickBooks record unchanged.
      - Each FIELDCRAFT project must have a mapped parent customer before it can be
        exported to QuickBooks.
    """

    def __init__(self) -> None:
        self.qbo_repo = QuickBooksRepository()
        self.qbo_service = QuickBooksService()
        self.project_repo = ProjectRepository()
        self.customer_repo = CustomerRepository()
        self.capability_service = QuickBooksCapabilityService()
        self.project_api_client = QuickBooksProjectApiClient()
        self.project_assignee_resolver = QuickBooksProjectAssigneeResolver(self.project_api_client)

    @staticmethod
    def _build_reason(message: str, intuit_tid: Optional[str] = None, **extra: Any) -> str:
        data: Dict[str, Any] = {"message": message}
        if intuit_tid:
            data["intuit_tid"] = intuit_tid
        for key, value in extra.items():
            if value is not None:
                data[key] = value
        return json.dumps(data)

    def _audit_project_event(
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
            resource_type="quickbooks_project",
        )

    def _get_configured_project_sync_method(self, *, tenant_id: str, service_id: str) -> str:
        config = self.qbo_repo.getProjectSyncConfig(tenant_id, service_id) or {}
        method = self._clean(config.get(const.KEY_PROJECT_SYNC_METHOD)).upper()
        if method in const.PROJECT_SYNC_METHODS_ALLOWED:
            return method

        # Backward compatibility: tenants with existing project mappings keep the
        # sub-customer workflow until an admin explicitly changes the setting.
        try:
            if self.project_repo.has_current_project_mappings(tenant_id=tenant_id, service_id=service_id):
                return const.PROJECT_SYNC_METHOD_SUB_CUSTOMER
        except Exception:
            logger.debug("[ProjectExport] could not inspect existing project mappings", exc_info=True)
        return const.PROJECT_SYNC_METHOD_AUTO

    def _resolve_default_project_sync_target(self, *, tenant_id: str, service_id: str) -> Dict[str, Any]:
        method = self._get_configured_project_sync_method(tenant_id=tenant_id, service_id=service_id)
        projects_available = False
        capability_error = None
        if method in (const.PROJECT_SYNC_METHOD_AUTO, const.PROJECT_SYNC_METHOD_QBO_PROJECT):
            try:
                projects_available = self.capability_service.projects_available(tenant_id, service_id, refresh=True)
            except Exception as exc:
                capability_error = str(exc)
                logger.warning("[ProjectExport] project capability detection failed | tenant_id=%s error=%s", tenant_id, exc)

        if method == const.PROJECT_SYNC_METHOD_SUB_CUSTOMER:
            target = const.PROJECT_SYNC_TARGET_SUB_CUSTOMER
        elif method == const.PROJECT_SYNC_METHOD_QBO_PROJECT:
            if not projects_available:
                raise QBOFeatureUnavailableError(const.CAPABILITY_PROJECTS, const.PROJECTS_NOT_AVAILABLE_MESSAGE)
            target = const.PROJECT_SYNC_TARGET_QBO_PROJECT
        else:
            target = const.PROJECT_SYNC_TARGET_QBO_PROJECT if projects_available else const.PROJECT_SYNC_TARGET_SUB_CUSTOMER

        return {
            "configuredMethod": method,
            "effectiveTarget": target,
            "projectsAvailable": projects_available,
            "capabilityError": capability_error,
        }

    def export_projects(
        self,
        *,
        tenant_id: str,
        service_id: str,
        limit: int = 500,
        offset: int = 0,
        object_id: Optional[str] = None,
        email: Optional[str] = None,
        user_role: Optional[str] = None,
        include_mapping_ids: Optional[list[int]] = None,
    ) -> Dict[str, Any]:
        logger.info(
            "[ProjectExport] export_projects called | tenant_id=%s service_id=%s limit=%s offset=%s",
            tenant_id,
            service_id,
            limit,
            offset,
        )
        safe_audit_log(
            tenant_id=tenant_id,
            action="project_export_started",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="started",
            resource_type="quickbooks_project",
        )
        audit_action = "project_export_failed"
        audit_status = "failed"
        audit_context: Optional[str] = None
        try:
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = (connection.get(const.KEY_ENV) or config.get(const.KEY_ENV) or "sandbox").lower()
            conn_status = (connection.get(const.KEY_STATUS) or const.STATUS_CONNECTED).lower()
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)

            logger.info(
                "[ProjectExport] connection loaded | env=%s conn_status=%s realm_id_present=%s has_refresh_token=%s",
                env,
                conn_status,
                bool(realm_id),
                bool(refresh_token),
            )


            if conn_status and conn_status != const.STATUS_CONNECTED:
                audit_action, audit_context = "project_export_aborted", f"not_connected status={conn_status}"
                return {const.KEY_OK: False, const.KEY_MESSAGE: f"QuickBooks is not connected (status={conn_status})"}
            if not refresh_token or not realm_id:
                audit_action, audit_context = "project_export_aborted", "missing_realmId_or_refreshToken"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks not connected for this tenant (missing realmId/refreshToken)."}

            if not client_id or not client_secret:
                audit_action, audit_context = "project_export_aborted", "missing_credentials"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks client credentials missing (ClientId/ClientSecret)."}

            access_token = self.refresh_access_token(
                tenant_id=tenant_id,
                service_id=service_id,
                connection=connection,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
                env=env,
                realm_id=realm_id,
            )
            base_url = self.get_qbo_api_base(env)
            logger.info(
                "[ProjectExport] access token refreshed | env=%s base_url=%s",
                env,
                base_url,
            )

            target_resolution = self._resolve_default_project_sync_target(
                tenant_id=tenant_id,
                service_id=service_id,
            )
            default_project_sync_target = target_resolution["effectiveTarget"]
            logger.debug(
                "[ProjectExport] project sync target resolved | method=%s target=%s projects_available=%s",
                target_resolution["configuredMethod"],
                default_project_sync_target,
                target_resolution["projectsAvailable"],
            )

            candidate_args: Dict[str, Any] = {
                "tenant_id": tenant_id,
                "service_id": service_id,
                "limit": limit,
                "offset": offset,
            }
            if include_mapping_ids:
                candidate_args["include_mapping_ids"] = include_mapping_ids
            candidates = self.project_repo.get_export_candidates(**candidate_args)

            summary: Dict[str, Any] = {
                const.KEY_OK: True,
                "tenantId": tenant_id,
                "serviceId": str(service_id),
                "realmIdPresent": bool(realm_id),
                "env": env,
                "requested": len(candidates),
                "projectSyncMethod": target_resolution.get("configuredMethod"),
                "effectiveProjectSyncTarget": default_project_sync_target,
                "projectsAvailable": target_resolution.get("projectsAvailable"),
                "mapped from QBO": 0,
                "createdInQbo": 0,
                "updatedInQbo": 0,
                "skippedAlreadySynced": 0,
                "skippedInProgress": 0,
                "skippedNotEligible": 0,
                "skippedQboNewer": 0,
                "syncFailed": 0,
                "failed": 0,
                const.KEY_FAILURES: [],
                const.KEY_RESULTS: [],
            }
            logger.debug("[ProjectExport] candidates fetched | count=%s", len(candidates))
            if not candidates:
                summary[const.KEY_MESSAGE] = "No pending project export candidates found."
                audit_action, audit_status, audit_context = "project_export_completed", "success", "no_candidates"
                return summary

            for item in candidates:
                project = item.get("project") or {}
                mapping = item.get("mapping") or {}
                project_id = int(project.get("id") or 0)
                display_name = self.build_project_display_name(project)
                detail = {
                    "fieldcraftProjectId": project_id,
                    "projectUid": project.get("project_uid"),
                    "customerUid": project.get("customer_uid"),
                    const.KEY_DISPLAY_NAME: display_name,
                    const.KEY_STATUS: None,
                    const.KEY_ACTION: None,
                    "qboProjectId": mapping.get("external_project_id") or None,
                    const.KEY_SYNC_TOKEN: mapping.get("sync_token"),
                    const.KEY_REASON: None,
                    const.KEY_ERROR: None,
                    "projectSyncTarget": None,
                }
                logger.debug(
                    "[ProjectExport] processing project | project_id=%s display_name=%r "
                    "external_project_id_present=%s mapping_id=%s",
                    project_id,
                    display_name,
                    bool(mapping.get("external_project_id")),
                    mapping.get("id"),
                )
                try:
                    mapping_id = mapping.get("id")
                    if not mapping_id:
                        raise Exception("Missing project external map row for export candidate")
                    if not project_id:
                        raise Exception("Project id is missing on export candidate")
                    if not display_name:
                        raise Exception("Project name is missing. Add a project description before syncing to QuickBooks.")

                    claim = self.project_repo.claim_export_mapping(
                        tenant_id=tenant_id,
                        service_id=service_id,
                        mapping_id=int(mapping_id),
                    )
                    if not claim.get("claimed"):
                        claim_reason = self._clean(claim.get("reason"))
                        current_mapping = claim.get("mapping") or {}
                        if claim_reason == "already_synced":
                            summary["skippedAlreadySynced"] += 1
                            action = "already synced"
                        elif claim_reason == "not_eligible":
                            summary["skippedNotEligible"] += 1
                            action = "no longer eligible for export"
                        else:
                            summary["skippedInProgress"] += 1
                            action = "sync already in progress"
                        detail.update({
                            const.KEY_STATUS: "success",
                            const.KEY_ACTION: action,
                            "qboProjectId": self._clean(current_mapping.get("external_project_id")) or None,
                            const.KEY_SYNC_TOKEN: current_mapping.get("sync_token"),
                            "projectSyncTarget": self.project_repo.get_mapping_sync_target(current_mapping),
                        })
                        summary[const.KEY_RESULTS].append(detail)
                        continue

                    claimed_mapping = claim.get("mapping")
                    if isinstance(claimed_mapping, dict):
                        mapping = claimed_mapping

                    external_project_id = self._clean(mapping.get("external_project_id"))
                    if external_project_id:
                        item_project_sync_target = self.project_repo.get_mapping_sync_target(mapping) or const.PROJECT_SYNC_TARGET_SUB_CUSTOMER
                        detail["projectSyncTarget"] = item_project_sync_target
                        self._assert_external_project_not_mapped_elsewhere(
                            tenant_id=tenant_id,
                            service_id=service_id,
                            external_project_id=external_project_id,
                            project_id=project_id,
                        )
                        reason = self._build_reason(
                            "Project is already linked to QuickBooks. Project updates are not included in this phase, so the existing QuickBooks record was left unchanged.",
                            code="PROJECT_ALREADY_SYNCED_NO_UPDATE",
                            project_sync_target=item_project_sync_target,
                        )
                        self.project_repo.mark_mapping_synced(
                            tenant_id=tenant_id,
                            mapping_id=int(mapping_id),
                            external_project_id=external_project_id,
                            external_account_id=str(realm_id),
                            sync_token=mapping.get("sync_token"),
                            reason=reason,
                            project_sync_target=item_project_sync_target,
                        )
                        summary["skippedAlreadySynced"] += 1
                        detail.update({
                            const.KEY_STATUS: "success",
                            const.KEY_ACTION: "already synced",
                            "qboProjectId": external_project_id,
                            const.KEY_SYNC_TOKEN: mapping.get("sync_token"),
                            const.KEY_REASON: reason,
                        })
                        summary[const.KEY_RESULTS].append(detail)
                        continue

                    item_project_sync_target = default_project_sync_target
                    detail["projectSyncTarget"] = item_project_sync_target
                    if item_project_sync_target == const.PROJECT_SYNC_TARGET_QBO_PROJECT:
                        self._export_project_as_qbo_project(
                            tenant_id=tenant_id,
                            service_id=service_id,
                            realm_id=str(realm_id),
                            env=env,
                            access_token=access_token,
                            project=project,
                            mapping=mapping,
                            detail=detail,
                            summary=summary,
                            object_id=object_id,
                            email=email,
                            user_role=user_role,
                        )
                        continue

                    parent_qbo_customer_id = self._resolve_parent_qbo_customer_id(
                        tenant_id=tenant_id,
                        service_id=service_id,
                        project=project,
                    )
                    detail["qboParentCustomerId"] = parent_qbo_customer_id
                    logger.debug(
                        "[ProjectExport] parent customer resolved | project_id=%s parent_qbo_customer_id=%s",
                        project_id,
                        parent_qbo_customer_id,
                    )
                    payload = self.build_qbo_project_payload(project=project, parent_qbo_customer_id=parent_qbo_customer_id)
                    logger.debug(
                        "[ProjectExport] sub-customer payload built | project_id=%s parent_ref_present=%s display_name=%r",
                        project_id,
                        bool((payload or {}).get("ParentRef")),
                        (payload or {}).get("DisplayName"),
                    )

                    found_project = self.find_project_in_qbo(
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=str(realm_id),
                        parent_qbo_customer_id=parent_qbo_customer_id,
                        display_name=display_name,
                    )
                    logger.debug(
                        "[ProjectExport] QBO project lookup completed | project_id=%s found=%s",
                        project_id,
                        bool(found_project and found_project.get(const.QBO_KEY_ID)),
                    )
                    if found_project and found_project.get(const.QBO_KEY_ID):
                        found_id = self._clean(found_project.get(const.QBO_KEY_ID))
                        found_token = self._clean(found_project.get(const.QBO_KEY_SYNC_TOKEN)) or None
                        self._assert_external_project_not_mapped_elsewhere(
                            tenant_id=tenant_id,
                            service_id=service_id,
                            external_project_id=found_id,
                            project_id=project_id,
                        )
                        logger.info(
                            "[ProjectExport] mapped existing QBO project | project_id=%s qbo_id=%s sync_token_present=%s",
                            project_id,
                            found_id,
                            bool(found_token),
                        )
                        reason = self._build_reason(
                            "Project already exists in QuickBooks under the mapped customer, so it was linked without changing the QuickBooks record.",
                            code="PROJECT_MAPPED_TO_EXISTING_QBO_SUB_CUSTOMER",
                            project_sync_target=const.PROJECT_SYNC_TARGET_SUB_CUSTOMER,
                        )
                        self.project_repo.mark_mapping_synced(
                            tenant_id=tenant_id,
                            mapping_id=int(mapping_id),
                            external_project_id=found_id,
                            external_account_id=str(realm_id),
                            sync_token=found_token,
                            reason=reason,
                            project_sync_target=const.PROJECT_SYNC_TARGET_SUB_CUSTOMER,
                        )
                        summary["mapped from QBO"] += 1
                        detail.update({
                            const.KEY_STATUS: "success",
                            const.KEY_ACTION: "mapped from QBO",
                            "qboProjectId": found_id,
                            const.KEY_SYNC_TOKEN: found_token,
                            const.KEY_REASON: reason,
                        })
                        summary[const.KEY_RESULTS].append(detail)
                        continue

                    created = self.create_project_in_qbo(
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=str(realm_id),
                        payload=payload,
                    )
                    created_id = self._clean(created.get(const.QBO_KEY_ID) or created.get("id"))
                    created_token = self._clean(created.get(const.QBO_KEY_SYNC_TOKEN)) or None
                    logger.info(
                        "[ProjectExport] created project in QBO | project_id=%s qbo_id=%s sync_token_present=%s",
                        project_id,
                        created_id,
                        bool(created_token),
                    )
                    if not created_id:
                        raise Exception(f"Create QuickBooks project returned unexpected response: {created}")
                    reason = self._build_reason(
                        "Project was created in QuickBooks as a sub-customer and synced successfully.",
                        code="PROJECT_CREATED_IN_QBO",
                        project_sync_target=const.PROJECT_SYNC_TARGET_SUB_CUSTOMER,
                    )
                    self.project_repo.mark_mapping_synced(
                        tenant_id=tenant_id,
                        mapping_id=int(mapping_id),
                        external_project_id=created_id,
                        external_account_id=str(realm_id),
                        sync_token=created_token,
                        reason=reason,
                        project_sync_target=const.PROJECT_SYNC_TARGET_SUB_CUSTOMER,
                    )
                    self._audit_project_event(
                        tenant_id=tenant_id,
                        action=AUDIT_ACTION_PROJECT_CREATED_IN_QBO,
                        resource_id=created_id,
                        context=f"fieldcraftProjectId={project_id}",
                        user_id=object_id,
                        email=email,
                        user_role=user_role,
                    )
                    summary["createdInQbo"] += 1
                    detail.update({
                        const.KEY_STATUS: "success",
                        const.KEY_ACTION: "created in QBO",
                        "qboProjectId": created_id,
                        const.KEY_SYNC_TOKEN: created_token,
                        const.KEY_REASON: reason,
                    })
                    summary[const.KEY_RESULTS].append(detail)
                    continue

                except Exception as exc:
                    logger.exception("[ProjectExport] ERROR processing project_id=%s | %s", project_id, exc)
                    error_message = str(exc).strip() or "Project export failed. Please review the error and retry sync."
                    reason = self._build_reason(
                        error_message,
                        code="PROJECT_EXPORT_FAILED",
                        error=error_message,
                        project_sync_target=detail.get("projectSyncTarget") or None,
                    )
                    if mapping.get("id"):
                        self.project_repo.mark_mapping_failed(
                            tenant_id=tenant_id,
                            mapping_id=int(mapping["id"]),
                            reason=reason,
                        )
                    self._audit_project_event(
                        tenant_id=tenant_id,
                        action=AUDIT_ACTION_PROJECT_SYNC_FAILED_PERSISTED,
                        status="failed",
                        resource_id=str(mapping.get("external_project_id") or project_id),
                        context=f"fieldcraftProjectId={project_id}",
                        user_id=object_id,
                        email=email,
                        user_role=user_role,
                    )
                    summary["failed"] += 1
                    failure = {
                        "projectId": project_id,
                        "externalProjectId": mapping.get("external_project_id") or None,
                        const.KEY_ERROR: str(exc),
                        const.KEY_REASON: reason,
                    }
                    qbo_api.append_failure(summary, failure)
                    detail.update({const.KEY_STATUS: "failed", const.KEY_ACTION: "error", const.KEY_ERROR: str(exc), const.KEY_REASON: reason})
                    summary[const.KEY_RESULTS].append(detail)

            audit_action = "project_export_completed"
            audit_status = "success"
            audit_context = f"created={summary.get('createdInQbo', 0)} updated={summary.get('updatedInQbo', 0)} failed={summary.get('failed', 0)}"
            logger.info("[ProjectExport] complete | %s", audit_context)
            return summary
        except QBOFeatureUnavailableError as exc:
            logger.warning("[ProjectExport] blocked by capability | tenant_id=%s feature=%s", tenant_id, exc.feature)
            audit_action = "project_export_aborted"
            audit_status = "failed"
            audit_context = str(exc)[:128]
            return {
                const.KEY_OK: False,
                const.KEY_MESSAGE: str(exc),
                const.KEY_ERROR: "feature_not_available",
                "feature": exc.feature,
            }
        except Exception as exc:
            logger.exception("[ProjectExport] FATAL ERROR | tenant_id=%s", tenant_id)
            audit_action = "project_export_failed"
            audit_status = "failed"
            audit_context = str(exc)[:128]
            return {const.KEY_OK: False, const.KEY_MESSAGE: "Project export failed.", const.KEY_ERROR: "Unexpected internal error. See server logs for details."}
        finally:
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

    def _export_project_as_qbo_project(
        self,
        *,
        tenant_id: str,
        service_id: str,
        realm_id: str,
        env: str,
        access_token: str,
        project: Dict[str, Any],
        mapping: Dict[str, Any],
        detail: Dict[str, Any],
        summary: Dict[str, Any],
        object_id: Optional[str],
        email: Optional[str],
        user_role: Optional[str],
    ) -> Optional[int]:
        project_id = int(project.get("id") or 0)
        mapping_id = int(mapping.get("id"))
        customer_context = self._resolve_parent_qbo_customer_context(
            tenant_id=tenant_id,
            service_id=service_id,
            project=project,
        )
        parent_qbo_customer_id = customer_context["qbo_customer_id"]
        client_entity_id = customer_context.get("client_entity_id")
        customer_mapping_id = customer_context.get("mapping_id")
        detail["qboParentCustomerId"] = parent_qbo_customer_id
        detail["qboClientEntityIdPresent"] = bool(client_entity_id)
        display_name = self.build_project_display_name(project)
        description = self._build_project_notes(project)
        external_project_id = self._clean(mapping.get("external_project_id"))

        if external_project_id:
            self._assert_external_project_not_mapped_elsewhere(
                tenant_id=tenant_id,
                service_id=service_id,
                external_project_id=external_project_id,
                project_id=project_id,
            )
            reason = self._build_reason(
                "Project is already linked to QuickBooks Projects. Project updates are not included in this phase, so the existing QuickBooks record was left unchanged.",
                code="PROJECT_ALREADY_SYNCED_NO_UPDATE",
                project_sync_target=const.PROJECT_SYNC_TARGET_QBO_PROJECT,
            )
            self.project_repo.mark_mapping_synced(
                tenant_id=tenant_id,
                mapping_id=mapping_id,
                external_project_id=external_project_id,
                external_account_id=realm_id,
                sync_token=None,
                reason=reason,
                project_sync_target=const.PROJECT_SYNC_TARGET_QBO_PROJECT,
            )
            summary["skippedAlreadySynced"] += 1
            detail.update({
                const.KEY_STATUS: "success",
                const.KEY_ACTION: "already synced",
                "qboProjectId": external_project_id,
                const.KEY_SYNC_TOKEN: None,
                const.KEY_REASON: reason,
            })
            summary[const.KEY_RESULTS].append(detail)
            return

        start_date = self._project_start_date(project)
        due_date = self._project_due_date(project, start_date=start_date)
        logger.info(
            "[ProjectExport] QBO Project create context | project_id=%s env=%s realm_id=%s parent_customer_id=%s customer_mapping_id=%s client_entity_id=%r start_date=%s due_date=%s",
            project_id,
            env,
            realm_id,
            parent_qbo_customer_id,
            customer_mapping_id,
            client_entity_id,
            start_date,
            due_date,
        )
        if not client_entity_id:
            logger.warning(
                "[ProjectExport] linked customer mapping has no ClientEntityId; GraphQL payload will omit client | project_id=%s customer_mapping_id=%s parent_customer_id=%s",
                project_id,
                customer_mapping_id,
                parent_qbo_customer_id,
            )

        assignee_id = None
        try:
            assignee_id = self.project_assignee_resolver.resolve(
                access_token=access_token,
                env=env,
                preferred_client_entity_id=client_entity_id,
                account_id=realm_id,
            )
        except Exception:
            logger.warning(
                "[ProjectExport] Could not resolve a QuickBooks project assignee; "
                "continuing without assignee | project_id=%s",
                project_id,
                exc_info=True,
            )
        detail["qboAssigneeIdPresent"] = bool(assignee_id)

        created = self.project_api_client.create_project(
            access_token=access_token,
            env=env,
            realm_id=realm_id,
            client_entity_id=client_entity_id,
            name=display_name,
            description=description,
            customer_id=parent_qbo_customer_id,
            assignee_id=assignee_id,
            start_date=start_date,
            due_date=due_date,
        )
        created_id = self._clean(created.get(const.QBO_KEY_ID) or created.get("id"))
        if not created_id:
            raise Exception(f"Create QuickBooks Project returned unexpected response: {created}")
        created_assignee = created.get("assignee") if isinstance(created.get("assignee"), dict) else {}
        stored_assignee_id = self._clean(created_assignee.get("id")) or assignee_id
        additional_info = None
        if stored_assignee_id:
            additional_info = {
                "quickbooksProject": {
                    "assigneeId": stored_assignee_id,
                }
            }
        reason = self._build_reason(
            "Project was created in QuickBooks Projects and synced successfully.",
            code="PROJECT_CREATED_AS_QBO_PROJECT",
            project_sync_target=const.PROJECT_SYNC_TARGET_QBO_PROJECT,
        )
        self.project_repo.mark_mapping_synced(
            tenant_id=tenant_id,
            mapping_id=mapping_id,
            external_project_id=created_id,
            external_account_id=realm_id,
            sync_token=None,
            reason=reason,
            project_sync_target=const.PROJECT_SYNC_TARGET_QBO_PROJECT,
            additional_info=additional_info,
        )
        self._audit_project_event(
            tenant_id=tenant_id,
            action=AUDIT_ACTION_PROJECT_CREATED_IN_QBO,
            resource_id=created_id,
            context=f"fieldcraftProjectId={project_id};target=QBO_PROJECT",
            user_id=object_id,
            email=email,
            user_role=user_role,
        )
        summary["createdInQbo"] += 1
        detail.update({
            const.KEY_STATUS: "success",
            const.KEY_ACTION: "created in QBO Project",
            "qboProjectId": created_id,
            const.KEY_SYNC_TOKEN: None,
            const.KEY_REASON: reason,
        })
        summary[const.KEY_RESULTS].append(detail)
        return mapping_id

    def _project_start_date(self, project: Dict[str, Any]) -> Optional[str]:
        value = project.get("created_at") or project.get("updated_at")
        if isinstance(value, datetime):
            return value.isoformat()
        return self._clean(value) or None

    def _project_due_date(self, project: Dict[str, Any], *, start_date: Optional[str]) -> str:
        for key in ("due_date", "end_date", "completion_date", "expected_completion_date"):
            parsed = self._parse_project_datetime(project.get(key))
            if parsed is not None:
                return parsed.isoformat()

        parsed_start = self._parse_project_datetime(start_date)
        if parsed_start is None:
            parsed_start = datetime.now(timezone.utc)
        return (parsed_start + timedelta(days=30)).isoformat()

    def _parse_project_datetime(self, value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            parsed = value
        else:
            raw = self._clean(value)
            if not raw:
                return None
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _resolve_parent_qbo_customer_context(
        self,
        *,
        tenant_id: str,
        service_id: str,
        project: Dict[str, Any],
    ) -> Dict[str, Any]:
        customer_uid = self._clean(project.get("customer_uid"))
        if not customer_uid:
            raise Exception(
                "This project is not linked to a FIELDCRAFT customer. "
                "Link the project to a customer before syncing it to QuickBooks."
            )

        customer_mapping = self.customer_repo.get_latest_qbo_linked_mapping_for_customer_uuid(
            tenant_id=tenant_id,
            service_id=service_id,
            customer_uuid=customer_uid,
        )
        parent_qbo_customer_id = self._clean((customer_mapping or {}).get("external_customer_id"))
        if not parent_qbo_customer_id:
            logger.warning(
                "[ProjectExport] parent customer QuickBooks link missing | tenant_id=%s service_id=%s project_id=%s customer_uid=%s mapping_id=%s sync_status=%s",
                tenant_id,
                service_id,
                project.get("id"),
                customer_uid,
                (customer_mapping or {}).get("id"),
                (customer_mapping or {}).get("sync_status"),
            )
            raise Exception(
                "The customer linked to this project does not have a QuickBooks Customer Id yet. "
                "Run customer sync first, then retry project sync."
            )

        additional_info = (customer_mapping or {}).get("additional_info") or {}
        if not isinstance(additional_info, dict):
            try:
                decoded = json.loads(str(additional_info))
                additional_info = decoded if isinstance(decoded, dict) else {}
            except (TypeError, ValueError):
                additional_info = {}

        client_entity_id = self._clean(
            additional_info.get(const.KEY_QBO_CLIENT_ENTITY_ID)
        ) or None
        return {
            "qbo_customer_id": parent_qbo_customer_id,
            "client_entity_id": client_entity_id,
            "mapping_id": (customer_mapping or {}).get("id"),
            "mapping_sync_status": (customer_mapping or {}).get("sync_status"),
        }

    def _resolve_parent_qbo_customer_id(
        self,
        *,
        tenant_id: str,
        service_id: str,
        project: Dict[str, Any],
    ) -> str:
        return str(
            self._resolve_parent_qbo_customer_context(
                tenant_id=tenant_id,
                service_id=service_id,
                project=project,
            )["qbo_customer_id"]
        )

    def _assert_external_project_not_mapped_elsewhere(self, *, tenant_id: str, service_id: str, external_project_id: str, project_id: int) -> None:
        existing = self.project_repo.get_current_mapping_by_external_project_id(
            tenant_id=tenant_id,
            service_id=service_id,
            external_project_id=external_project_id,
        )
        if existing and existing.get("project_id") is not None and int(existing["project_id"]) != int(project_id):
            raise Exception(
                f"QuickBooks project Id {external_project_id} is already mapped to a different FIELDCRAFT project ({existing.get('project_id')})."
            )

    def build_project_display_name(self, project: Dict[str, Any]) -> str:
        name = self._clean(project.get("description")) or self._clean(project.get("order_no")) or f"Project {project.get('id')}"
        return name

    def build_qbo_project_payload(self, *, project: Dict[str, Any], parent_qbo_customer_id: str) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            const.QBO_KEY_DISPLAY_NAME: self.build_project_display_name(project),
            "Job": True,
            "BillWithParent": True,
            "ParentRef": {"value": str(parent_qbo_customer_id)},
        }
        bill_addr = self._build_qbo_address(project)
        if bill_addr:
            payload["BillAddr"] = bill_addr
        notes = self._build_project_notes(project)
        if notes:
            payload["Notes"] = notes[:2000]
        return payload

    def _build_qbo_address(self, project: Dict[str, Any]) -> Dict[str, str]:
        asset = project.get("customer_asset") or {}
        if not isinstance(asset, dict):
            asset = {}
        address: Dict[str, str] = {}
        line1 = self._clean(asset.get("address"))
        city = self._clean(asset.get("city"))
        state = self._clean(asset.get("state"))
        country = self._clean(asset.get("country"))
        postal_code = self._clean(asset.get("zipCode") or asset.get("zipcode") or project.get("zipcode"))
        if line1:
            address["Line1"] = line1
        if city:
            address["City"] = city
        if state:
            address["CountrySubDivisionCode"] = state
        if country:
            address["Country"] = country
        if postal_code:
            address["PostalCode"] = postal_code
        return address

    def _build_project_notes(self, project: Dict[str, Any]) -> str:
        parts = [f"FIELDCRAFT Project ID: {project.get('id')}"]
        if self._clean(project.get("project_uid")):
            parts.append(f"Project UID: {project.get('project_uid')}")
        if self._clean(project.get("order_no")):
            parts.append(f"Order No: {project.get('order_no')}")
        if self._clean(project.get("status")):
            parts.append(f"Status: {project.get('status')}")
        return " | ".join(parts)

    def find_project_in_qbo(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        parent_qbo_customer_id: str,
        display_name: str,
    ) -> Optional[Dict[str, Any]]:
        if not display_name:
            return None
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        query_url = f"{base_url}/v3/company/{realm_id}/query"
        parent_value = str(parent_qbo_customer_id)
        page_size = 100
        start_position = 1
        while True:
            query = (
                "SELECT * FROM Customer "
                f"WHERE DisplayName = '{escape_qbo_literal(display_name)}' "
                f"STARTPOSITION {start_position} MAXRESULTS {page_size}"
            )
            resp = qbo_api.qbo_get(query_url, headers=headers, params={"query": query}, timeout=30)
            if resp.status_code != 200:
                raise Exception(qbo_api.build_response_error(resp, "QBO project lookup failed"))

            data = qbo_api.safe_json(resp)
            customers = (data.get("QueryResponse") or {}).get("Customer") or []
            if not customers:
                return None

            for customer in customers:
                parent_ref = customer.get("ParentRef") or {}
                if str(parent_ref.get("value") or "") == parent_value:
                    return customer

            if len(customers) < page_size:
                return None
            start_position += page_size

    def create_project_in_qbo(self, *, access_token: str, base_url: str, realm_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/customer"
        resp = qbo_api.qbo_post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code not in (200, 201):
            raise Exception(qbo_api.build_response_error(resp, "QBO create project failed"))
        data = qbo_api.safe_json(resp)
        return data.get("Customer") or {}

    def refresh_access_token(
        self,
        *,
        tenant_id: str,
        service_id: str,
        connection: Optional[Dict[str, Any]],
        client_id: str,
        client_secret: str,
        refresh_token: str,
        env: Optional[str] = None,
        realm_id: Optional[str] = None,
    ) -> str:
        from source.Utils import QBODiscoveryClient
        token_url = QBODiscoveryClient.get_token_endpoint(env or "sandbox")
        return QuickBooksOAuthClient(repository=self.qbo_repo).refresh_access_token(
            tenant_id=str(tenant_id),
            service_id=str(service_id),
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            token_url=token_url,
            current_connection=connection,
            debug_context={
                "module": "projects",
                "env": env,
                "realm_id": realm_id,
            },
        )


    def get_qbo_api_base(self, env: str) -> str:
        from source.Utils import QBODiscoveryClient
        return QBODiscoveryClient.get_qbo_api_base(env)

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()
