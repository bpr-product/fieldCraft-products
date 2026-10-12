from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from source.Utils import constants as const
from source.Utils.auditlog import safe_audit_log
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.dal.TimelogRepository import TimelogRepository
from source.quickbooks.QuickBooksAuthentication import QuickBooksReconnectRequiredError
from source.quickbooks.QuickBooksTimelogExportService import create_timelog_export_service

logger = logging.getLogger(__name__)


class QuickBooksTimelogCreateService:
    """Export one mapped FIELDCRAFT timelog through the Accounting TimeActivity flow."""

    def __init__(self) -> None:
        self.qbo_repo = QuickBooksRepository()
        self.timelog_repo = TimelogRepository()
        self.timelog_exporter = create_timelog_export_service()

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    def create_timelog(
        self,
        *,
        tenant_id: str,
        service_id: Optional[str],
        timelog_external_map_id: int,
        object_id: Optional[str] = None,
        email: Optional[str] = None,
        user_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        tenant_id = self._clean(tenant_id)
        service_id = self._clean(service_id)
        mapping_id = int(timelog_external_map_id or 0)
        if not tenant_id:
            return {const.KEY_OK: False, const.KEY_MESSAGE: "tenant_id is required"}
        if not mapping_id:
            return {
                const.KEY_OK: False,
                const.KEY_MESSAGE: "timelog_external_map_id is required",
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
            action="timelog_create_started",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="started",
            resource_type="quickbooks_timelog",
        )
        result: Dict[str, Any] = {const.KEY_OK: False}
        try:
            mapping = self.timelog_repo.get_mapping_by_id(
                tenant_id=tenant_id,
                service_id=service_id,
                mapping_id=mapping_id,
            )
            if not mapping:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: "Timelog external map was not found or is no longer active.",
                    "timelogExternalMapId": mapping_id,
                }
                return result

            timelog_id = int(mapping.get("timelog_id") or 0)
            entity_type = TimelogRepository.mapping_external_entity_type(mapping)
            external_id = self._clean(TimelogRepository.mapping_external_id(mapping))
            sync_status = self._clean(mapping.get("sync_status")).upper()
            if (
                sync_status == TimelogRepository.STATUS_SYNCED
                and external_id
                and entity_type == "TIME_ACTIVITY"
            ):
                result = {
                    const.KEY_OK: True,
                    const.KEY_MESSAGE: "Timelog is already synced to QuickBooks TimeActivity.",
                    "tenantId": tenant_id,
                    "serviceId": service_id,
                    "timelogId": timelog_id,
                    "timelogExternalMapId": mapping_id,
                    "qboTimeActivityId": external_id,
                    "externalEntityType": "TIME_ACTIVITY",
                    const.KEY_STATUS: "already_synced",
                }
                return result
            if external_id and entity_type == "TIMESHEET":
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: (
                        "This mapping still points to a legacy QuickBooks Time timesheet. "
                        "Reset that mapping before exporting it as a TimeActivity."
                    ),
                    "tenantId": tenant_id,
                    "serviceId": service_id,
                    "timelogId": timelog_id,
                    "timelogExternalMapId": mapping_id,
                    "legacyTimesheetId": external_id,
                    "externalEntityType": "TIMESHEET",
                    const.KEY_STATUS: "legacy_cleanup_required",
                }
                return result

            retryable_statuses = {
                TimelogRepository.STATUS_PENDING_PUSH,
                TimelogRepository.STATUS_SYNC_FAILED,
            }
            recoverable_synced_mapping = (
                sync_status == TimelogRepository.STATUS_SYNCED and not external_id
            )
            if sync_status not in retryable_statuses and not recoverable_synced_mapping:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: (
                        "Timelog is not eligible for real-time export. "
                        "It must be pending or retryable before QuickBooks sync."
                    ),
                    "tenantId": tenant_id,
                    "serviceId": service_id,
                    "timelogId": timelog_id,
                    "timelogExternalMapId": mapping_id,
                    "syncStatus": sync_status or None,
                    const.KEY_STATUS: "not_eligible",
                }
                return result

            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            realm_id = self._clean(connection.get(const.KEY_REALM_ID))
            if not realm_id:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: "QuickBooks Accounting realmId is missing.",
                }
                return result

            candidate = self.timelog_repo.get_export_candidate_by_mapping_id(
                tenant_id=tenant_id,
                service_id=service_id,
                mapping_id=mapping_id,
                external_account_id=realm_id,
            )
            if not candidate:
                result = {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: (
                        "Timelog is not ready for QuickBooks export. It must be approved, "
                        "completed, valid, and queued for sync."
                    ),
                    "timelogId": timelog_id,
                    "timelogExternalMapId": mapping_id,
                }
                return result

            ctx = self.timelog_exporter.accounting_context(
                tenant_id=tenant_id,
                service_id=service_id,
                connection=connection,
            )
            result = self.timelog_exporter._summary(tenant_id, service_id, False)
            result.update(
                {
                    "timelogId": timelog_id,
                    "timelogExternalMapId": mapping_id,
                    "requested": 1,
                }
            )
            # The shared daily exporter acquires its worker/project/date advisory lock
            # before any QuickBooks TimeActivity create/update, covering auto and manual sync.
            self.timelog_exporter._process_candidate(
                candidate=candidate,
                tenant_id=tenant_id,
                service_id=service_id,
                realm_id=realm_id,
                base_url=ctx["base_url"],
                access_token=ctx["access_token"],
                dry_run=False,
                summary=result,
            )
            result[const.KEY_OK] = (
                int(result.get("failed") or 0) == 0
                and int(result.get("syncFailed") or 0) == 0
            )
            result[const.KEY_MESSAGE] = (
                "Timelog exported to QuickBooks TimeActivity."
                if result[const.KEY_OK]
                else "Timelog export to QuickBooks TimeActivity failed."
            )
            return result
        except QuickBooksReconnectRequiredError:
            raise
        except Exception as exc:
            logger.exception("[TimelogCreate] real-time Accounting TimeActivity export failed")
            result = {
                const.KEY_OK: False,
                const.KEY_MESSAGE: "Real-time QuickBooks TimeActivity export failed.",
                const.KEY_ERROR: str(exc),
            }
            return result
        finally:
            safe_audit_log(
                tenant_id=tenant_id,
                action=(
                    "timelog_create_completed"
                    if result.get(const.KEY_OK)
                    else "timelog_create_failed"
                ),
                user_id=object_id or None,
                email=email or None,
                user_role=user_role or None,
                status="success" if result.get(const.KEY_OK) else "failed",
                resource_type="quickbooks_timelog",
                context=f"mapping_id={mapping_id}",
            )
