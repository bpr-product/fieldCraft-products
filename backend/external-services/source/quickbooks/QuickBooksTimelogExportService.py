from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from source.Utils import constants as const
from source.Utils.auditlog import safe_audit_log
from source.dal.TimelogRepository import TimelogRepository
from source.quickbooks.QuickBooksTimeActivityExportService import (
    QuickBooksTimeActivityExportService,
)

logger = logging.getLogger(__name__)


class QuickBooksTimelogExportService(QuickBooksTimeActivityExportService):
    """Export approved FIELDCRAFT timelogs to QuickBooks Online Accounting TimeActivity.

    W-2 workers use EmployeeRef and 1099 workers use VendorRef. This exporter
    uses only the normal QuickBooks Online Accounting connection; it never calls
    the classic QuickBooks Time/TSheets API.
    """

    EXTERNAL_ENTITY_TYPE = "TIME_ACTIVITY"
    TARGET = "QUICKBOOKS_ACCOUNTING_TIME_ACTIVITY"

    def build_time_activity_payload(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        return self.build_payload(candidate)

    def _validate_candidate(
        self,
        candidate: Dict[str, Any],
        *,
        tenant_id: str,
        service_id: str,
    ) -> Optional[str]:
        candidate = dict(candidate)
        candidate.setdefault("map_sync_status", TimelogRepository.STATUS_PENDING_PUSH)
        candidate.setdefault("approval_status", TimelogRepository.APPROVAL_STATUS_APPROVED)
        return self.validate_candidate(
            candidate,
            tenant_id=tenant_id,
            service_id=service_id,
        )

    def create_time_activity_in_qbo(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        return QuickBooksTimeActivityExportService.create_time_activity(
            self,
            access_token=access_token,
            base_url=base_url,
            realm_id=realm_id,
            payload=payload,
        )

    # Keep the core service path routed through the public compatibility helper.
    # This also keeps existing tests and callers that patch this method working.
    def create_time_activity(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self.create_time_activity_in_qbo(
            access_token=access_token,
            base_url=base_url,
            realm_id=realm_id,
            payload=payload,
        )

    @staticmethod
    def _summary(tenant_id: str, service_id: str, dry_run: bool) -> Dict[str, Any]:
        return {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": str(service_id),
            "dryRun": bool(dry_run),
            "queued": 0,
            "requested": 0,
            "createdInQbo": 0,
            "updatedInQbo": 0,
            "createdInAccounting": 0,
            "updatedInAccounting": 0,
            "createdTimeActivities": 0,
            "updatedTimeActivities": 0,
            "skipped": 0,
            "syncFailed": 0,
            "failed": 0,
            const.KEY_RESULTS: [],
            const.KEY_FAILURES: [],
        }

    def _process_candidate(
        self,
        *,
        candidate: Dict[str, Any],
        tenant_id: str,
        service_id: str,
        realm_id: str,
        base_url: str,
        access_token: str,
        dry_run: bool,
        summary: Dict[str, Any],
        **_: Any,
    ) -> None:
        self.process_candidate(
            candidate=candidate,
            tenant_id=tenant_id,
            service_id=service_id,
            realm_id=realm_id,
            base_url=base_url,
            access_token=access_token,
            dry_run=dry_run,
            summary=summary,
        )

    def export_timelogs(
        self,
        *,
        tenant_id: str,
        service_id: str,
        limit: int = 500,
        offset: int = 0,
        after_mapping_id: Optional[int] = None,
        dry_run: bool = False,
        object_id: Optional[str] = None,
        email: Optional[str] = None,
        user_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        tenant_id = self._clean(tenant_id)
        service_id = self._clean(service_id)
        summary = self._summary(tenant_id, service_id, dry_run)
        safe_audit_log(
            tenant_id=tenant_id,
            action="timelog_export_started",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="started",
            resource_type="quickbooks_timelog",
        )
        try:
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            realm_id = self._clean(connection.get(const.KEY_REALM_ID))
            if not realm_id:
                raise ValueError("QuickBooks Accounting realmId is missing.")

            ctx = self.accounting_context(
                tenant_id=tenant_id,
                service_id=service_id,
                connection=connection,
            )
            kwargs: Dict[str, Any] = {
                "tenant_id": tenant_id,
                "service_id": service_id,
                "limit": max(1, min(int(limit or 500), 500)),
                "offset": max(0, int(offset or 0)),
                "external_account_id": realm_id,
            }
            if after_mapping_id is not None:
                kwargs["after_mapping_id"] = int(after_mapping_id)
            candidates = self.timelog_repo.get_export_candidates(**kwargs)
            summary["queued"] = len(candidates)
            summary["requested"] = len(candidates)
            summary["lastMappingId"] = max(
                (int(row.get("map_id") or 0) for row in candidates),
                default=None,
            )
            for candidate in candidates:
                self._process_candidate(
                    candidate=candidate,
                    tenant_id=tenant_id,
                    service_id=service_id,
                    realm_id=realm_id,
                    base_url=ctx["base_url"],
                    access_token=ctx["access_token"],
                    dry_run=dry_run,
                    summary=summary,
                )

            failed_total = int(summary["failed"]) + int(summary["syncFailed"])
            successful_total = int(summary["createdInQbo"]) + int(summary["updatedInQbo"])
            summary["overall"] = {
                "failedTotal": failed_total,
                "successfulTotal": successful_total,
            }
            summary["partialSuccess"] = bool(failed_total and successful_total)
            summary[const.KEY_OK] = failed_total == 0 or successful_total > 0
            if not candidates:
                summary[const.KEY_MESSAGE] = "No approved timelog export candidates found."
            elif failed_total and not successful_total:
                summary[const.KEY_MESSAGE] = "No QuickBooks TimeActivity records were exported."
            elif failed_total:
                summary[const.KEY_MESSAGE] = (
                    f"QuickBooks time activity export completed with {failed_total} failed record(s)."
                )
            else:
                summary[const.KEY_MESSAGE] = (
                    "All requested timelogs were exported to QuickBooks TimeActivity."
                )
            return summary
        except Exception as exc:
            logger.exception("[TimelogExport] Accounting TimeActivity export failed")
            summary[const.KEY_OK] = False
            summary[const.KEY_MESSAGE] = "QuickBooks time activity export failed."
            summary[const.KEY_ERROR] = str(exc)
            return summary
        finally:
            safe_audit_log(
                tenant_id=tenant_id,
                action=(
                    "timelog_export_completed"
                    if summary.get(const.KEY_OK)
                    else "timelog_export_failed"
                ),
                user_id=object_id or None,
                email=email or None,
                user_role=user_role or None,
                status="success" if summary.get(const.KEY_OK) else "failed",
                resource_type="quickbooks_timelog",
                context=(
                    f"requested={summary.get('requested', 0)};"
                    f"created={summary.get('createdInQbo', 0)};"
                    f"updated={summary.get('updatedInQbo', 0)};"
                    f"failed={summary.get('syncFailed', 0)}"
                ),
            )

    def export_all_timelogs(
        self,
        *,
        tenant_id: str,
        service_id: str,
        batch_size: int = 500,
        dry_run: bool = False,
        object_id: Optional[str] = None,
        email: Optional[str] = None,
        user_role: Optional[str] = None,
        max_batches: int = 1000,
    ) -> Dict[str, Any]:
        pages = []
        last_mapping_id: Optional[int] = None
        for _ in range(max(1, int(max_batches or 1))):
            page = self.export_timelogs(
                tenant_id=tenant_id,
                service_id=service_id,
                limit=batch_size,
                offset=0,
                after_mapping_id=last_mapping_id,
                dry_run=dry_run,
                object_id=object_id,
                email=email,
                user_role=user_role,
            )
            pages.append(page)
            requested = int(page.get("requested") or 0)
            next_last = page.get("lastMappingId")
            if requested == 0 or not next_last or int(next_last) == int(last_mapping_id or 0):
                break
            last_mapping_id = int(next_last)
            if requested < max(1, min(int(batch_size or 500), 500)):
                break

        result = self._summary(self._clean(tenant_id), self._clean(service_id), dry_run)
        result["pageCount"] = len(pages)
        for key in (
            "queued",
            "requested",
            "createdInQbo",
            "updatedInQbo",
            "createdInAccounting",
            "updatedInAccounting",
            "createdTimeActivities",
            "updatedTimeActivities",
            "skipped",
            "syncFailed",
            "failed",
        ):
            result[key] = sum(int(page.get(key) or 0) for page in pages)
        result[const.KEY_RESULTS] = [
            item for page in pages for item in list(page.get(const.KEY_RESULTS) or [])
        ]
        result[const.KEY_FAILURES] = [
            item for page in pages for item in list(page.get(const.KEY_FAILURES) or [])
        ]
        failed_total = result["failed"] + result["syncFailed"]
        successful_total = result["createdInQbo"] + result["updatedInQbo"]
        result["overall"] = {
            "failedTotal": failed_total,
            "successfulTotal": successful_total,
        }
        result["partialSuccess"] = bool(failed_total and successful_total)
        result[const.KEY_OK] = failed_total == 0 or successful_total > 0
        result[const.KEY_MESSAGE] = (
            "All requested timelogs were exported to QuickBooks TimeActivity."
            if failed_total == 0
            else f"QuickBooks time activity export completed with {failed_total} failed record(s)."
        )
        return result


def create_timelog_export_service() -> QuickBooksTimelogExportService:
    """Return the production daily QBO Accounting TimeActivity exporter."""
    from source.quickbooks.QuickBooksDailyTimelogExportService import (
        QuickBooksDailyTimelogExportService,
    )

    return QuickBooksDailyTimelogExportService()
