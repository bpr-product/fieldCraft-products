from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from source.Utils import constants as const
from source.Utils.auditlog import safe_audit_log
from source.dal.ProjectRepository import ProjectRepository
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.quickbooks.QuickBooksAuthentication import QuickBooksReconnectRequiredError
from source.quickbooks.QuickBooksProjectExportService import QuickBooksProjectExportService

logger = logging.getLogger(__name__)


class QuickBooksProjectCreateService:
    """Real-time export for one FIELDCRAFT project mapping.

    The application sends the current ``project_external_map`` id after the
    project mapping becomes ``PENDING_PUSH``. The service validates that exact
    mapping and delegates to the normal project exporter so manual and real-time
    sync use the same customer prerequisites, project/sub-customer target rules,
    duplicate recovery, and mapping persistence.
    """

    def __init__(self) -> None:
        self.qbo_repo = QuickBooksRepository()
        self.project_repo = ProjectRepository()
        self.project_exporter = QuickBooksProjectExportService()

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    def create_project(
        self,
        *,
        tenant_id: str,
        service_id: Optional[str],
        project_external_map_id: int,
        object_id: Optional[str] = None,
        email: Optional[str] = None,
        user_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        tenant_id = self._clean(tenant_id)
        service_id = self._clean(service_id)
        mapping_id = int(project_external_map_id or 0)

        if not tenant_id:
            return {const.KEY_OK: False, const.KEY_MESSAGE: "tenant_id is required"}
        if not mapping_id:
            return {
                const.KEY_OK: False,
                const.KEY_MESSAGE: "project_external_map_id is required",
            }
        if not service_id:
            service_id = self._clean(
                self.qbo_repo.getServiceIdByName(const.SERVICE_NAME_QUICKBOOKS)
            )
        if not service_id:
            return {
                const.KEY_OK: False,
                const.KEY_MESSAGE: "QuickBooks service_id could not be resolved",
            }

        safe_audit_log(
            tenant_id=tenant_id,
            action="project_create_started",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="started",
            resource_type="quickbooks_project",
        )

        result: Dict[str, Any] = {const.KEY_OK: False}
        project_id: Optional[int] = None
        try:
            mapping = self.project_repo.get_mapping_by_id(
                tenant_id=tenant_id,
                service_id=service_id,
                mapping_id=mapping_id,
            )
            if not mapping:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: "Project external map was not found or is no longer active.",
                    "projectExternalMapId": mapping_id,
                }
                return result

            project_id = int(mapping.get("project_id") or 0)
            if not project_id:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: "Project external map is missing project_id.",
                    "projectExternalMapId": mapping_id,
                }
                return result

            current_mapping = self.project_repo.get_current_mapping_by_project_id(
                tenant_id=tenant_id,
                service_id=service_id,
                project_id=project_id,
            )
            if not current_mapping or int(current_mapping.get("id") or 0) != mapping_id:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: "Project external map is no longer the current mapping.",
                    "projectId": project_id,
                    "projectExternalMapId": mapping_id,
                }
                return result

            sync_status = self._clean(mapping.get("sync_status")).upper()
            external_project_id = self._clean(mapping.get("external_project_id"))

            if sync_status == self.project_repo.STATUS_SYNCED and external_project_id:
                result = {
                    const.KEY_OK: True,
                    const.KEY_MESSAGE: "Project is already synced to QuickBooks.",
                    "tenantId": tenant_id,
                    "serviceId": service_id,
                    "projectId": project_id,
                    "projectExternalMapId": mapping_id,
                    "qboProjectId": external_project_id,
                    "projectSyncTarget": self.project_repo.get_mapping_sync_target(mapping),
                    const.KEY_STATUS: "already_synced",
                }
                return result

            allowed_statuses = {
                self.project_repo.STATUS_PENDING_PUSH,
                self.project_repo.STATUS_FAILED,
                self.project_repo.STATUS_SYNC_FAILED,
            }
            if sync_status not in allowed_statuses:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: (
                        "Project is not eligible for real-time export. "
                        f"Expected sync_status in {sorted(allowed_statuses)}, "
                        f"got '{sync_status or 'EMPTY'}'."
                    ),
                    "projectId": project_id,
                    "projectExternalMapId": mapping_id,
                    "syncStatus": sync_status or None,
                }
                return result

            project = self.project_repo.get_active_project_by_id(
                tenant_id=tenant_id,
                project_id=project_id,
            )
            if not project:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: f"Active project was not found for project_id={project_id}.",
                    "projectExternalMapId": mapping_id,
                }
                return result

            result = self.project_exporter.export_projects(
                tenant_id=tenant_id,
                service_id=service_id,
                limit=1,
                offset=0,
                object_id=object_id,
                email=email,
                user_role=user_role,
                include_mapping_ids=[mapping_id],
            )
            result["projectId"] = project_id
            result["projectExternalMapId"] = mapping_id

            if int(result.get("requested") or 0) == 0:
                latest = self.project_repo.get_mapping_by_id(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    mapping_id=mapping_id,
                )
                latest_external_id = self._clean((latest or {}).get("external_project_id"))
                latest_status = self._clean((latest or {}).get("sync_status")).upper()
                if latest_status == self.project_repo.STATUS_SYNCED and latest_external_id:
                    result.update(
                        {
                            const.KEY_OK: True,
                            const.KEY_MESSAGE: "Project is already synced to QuickBooks.",
                            "qboProjectId": latest_external_id,
                            "projectSyncTarget": self.project_repo.get_mapping_sync_target(latest),
                            const.KEY_STATUS: "already_synced",
                        }
                    )
                    return result
                result.update(
                    {
                        const.KEY_OK: False,
                        const.KEY_MESSAGE: "Project is no longer eligible for real-time export.",
                    }
                )
                return result

            failed = int(result.get("failed") or 0)
            sync_failed = int(result.get("syncFailed") or 0)
            result[const.KEY_OK] = bool(result.get(const.KEY_OK, True)) and failed == 0 and sync_failed == 0
            if result[const.KEY_OK]:
                result.setdefault(const.KEY_MESSAGE, "Project exported to QuickBooks.")
            else:
                result.setdefault(
                    const.KEY_MESSAGE,
                    "Project export to QuickBooks needs attention. Review the project sync status and retry.",
                )
            return result
        except QuickBooksReconnectRequiredError:
            raise
        except Exception as exc:
            logger.exception(
                "[ProjectCreate] real-time project export failed | tenant_id=%s mapping_id=%s",
                tenant_id,
                mapping_id,
            )
            result = {
                const.KEY_OK: False,
                const.KEY_MESSAGE: "Real-time QuickBooks project export failed.",
                const.KEY_ERROR: str(exc),
                "projectId": project_id,
                "projectExternalMapId": mapping_id,
            }
            return result
        finally:
            safe_audit_log(
                tenant_id=tenant_id,
                action=(
                    "project_create_completed"
                    if result.get(const.KEY_OK)
                    else "project_create_failed"
                ),
                user_id=object_id or None,
                email=email or None,
                user_role=user_role or None,
                status="success" if result.get(const.KEY_OK) else "failed",
                resource_type="quickbooks_project",
                context=f"mapping_id={mapping_id}",
            )
