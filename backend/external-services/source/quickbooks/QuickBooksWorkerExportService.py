from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from source.Utils import constants as const
from source.quickbooks.QuickBooksContractorVendorExportService import (
    QuickBooksContractorVendorExportService,
)
from source.quickbooks.QuickBooksEmployeeExportService import QuickBooksEmployeeExportService

logger = logging.getLogger(__name__)


class QuickBooksWorkerExportService:
    """Export W2 workers as QBO Employees and contractors as QBO Vendors."""

    def __init__(self) -> None:
        self.employee_exporter = QuickBooksEmployeeExportService()
        self.contractor_exporter = QuickBooksContractorVendorExportService()

    @staticmethod
    def _failed_summary(message: str) -> Dict[str, Any]:
        return {
            const.KEY_OK: False,
            const.KEY_MESSAGE: message,
            "requested": 0,
            "createdInQbo": 0,
            "updatedInQbo": 0,
            "mapped from QBO": 0,
            "skippedAlreadyMapped": 0,
            "skippedQboNewer": 0,
            "failed": 1,
            const.KEY_FAILURES: [{const.KEY_ERROR: message}],
            const.KEY_RESULTS: [],
        }

    def _validated_summary(self, value: Any, *, label: str) -> Dict[str, Any]:
        if not isinstance(value, dict) or not value:
            logger.error("%s exporter returned no result", label)
            return self._failed_summary(
                f"{label} export did not return a valid result. No worker was marked as synced."
            )
        if const.KEY_OK not in value:
            logger.error("%s exporter result has no ok flag", label)
            return self._failed_summary(
                f"{label} export returned an incomplete result. Review the server logs and retry."
            )
        return value

    @staticmethod
    def _merge_page_summaries(pages: List[Dict[str, Any]], *, empty_message: str) -> Dict[str, Any]:
        if not pages:
            return {
                const.KEY_OK: True,
                const.KEY_MESSAGE: empty_message,
                "requested": 0,
                "createdInQbo": 0,
                "updatedInQbo": 0,
                "mapped from QBO": 0,
                "skippedAlreadyMapped": 0,
                "skippedQboNewer": 0,
                "failed": 0,
                const.KEY_FAILURES: [],
                const.KEY_RESULTS: [],
            }

        merged: Dict[str, Any] = {
            const.KEY_OK: all(bool(page.get(const.KEY_OK)) for page in pages),
            "requested": sum(int(page.get("requested") or 0) for page in pages),
            "createdInQbo": sum(int(page.get("createdInQbo") or 0) for page in pages),
            "updatedInQbo": sum(int(page.get("updatedInQbo") or 0) for page in pages),
            "mapped from QBO": sum(int(page.get("mapped from QBO") or 0) for page in pages),
            "skippedAlreadyMapped": sum(int(page.get("skippedAlreadyMapped") or 0) for page in pages),
            "skippedQboNewer": sum(int(page.get("skippedQboNewer") or 0) for page in pages),
            "failed": sum(int(page.get("failed") or 0) for page in pages),
            const.KEY_FAILURES: [
                failure
                for page in pages
                for failure in list(page.get(const.KEY_FAILURES) or [])
            ],
            const.KEY_RESULTS: [
                result
                for page in pages
                for result in list(page.get(const.KEY_RESULTS) or [])
            ],
            "pageCount": len(pages),
        }
        failed = int(merged["failed"] or 0)
        successful = (
            int(merged["createdInQbo"] or 0)
            + int(merged["updatedInQbo"] or 0)
            + int(merged["mapped from QBO"] or 0)
        )
        merged["partialSuccess"] = bool(failed and successful)
        if not merged[const.KEY_OK]:
            messages = [
                str(page.get(const.KEY_MESSAGE))
                for page in pages
                if page.get(const.KEY_OK) is False and page.get(const.KEY_MESSAGE)
            ]
            merged[const.KEY_MESSAGE] = "; ".join(dict.fromkeys(messages)) or "Worker export failed."
        elif failed:
            merged[const.KEY_MESSAGE] = (
                f"Worker export completed with {failed} failed record(s). Review the listed records and retry."
            )
        else:
            merged[const.KEY_MESSAGE] = "Worker export completed successfully."
        return merged

    def _drain_exporter(
        self,
        *,
        exporter: Callable[..., Dict[str, Any]],
        label: str,
        tenant_id: str,
        service_id: str,
        batch_size: int,
        object_id: Optional[str],
        email: Optional[str],
        user_role: Optional[str],
        max_batches: int = 1000,
    ) -> Dict[str, Any]:
        pages: List[Dict[str, Any]] = []
        cursor: Optional[int] = None
        for _ in range(max_batches):
            raw = exporter(
                tenant_id=tenant_id,
                service_id=service_id,
                limit=batch_size,
                offset=0,
                after_mapping_id=cursor,
                object_id=object_id,
                email=email,
                user_role=user_role,
            )
            page = self._validated_summary(raw, label=label)
            pages.append(page)
            if page.get(const.KEY_OK) is False:
                break

            requested = int(page.get("requested") or 0)
            last_mapping_id = page.get("lastMappingId")
            if requested < batch_size or not last_mapping_id:
                break
            next_cursor = int(last_mapping_id)
            if cursor is not None and next_cursor <= cursor:
                pages.append(
                    self._failed_summary(
                        f"{label} export pagination did not advance. The sync stopped to prevent duplicate exports."
                    )
                )
                break
            cursor = next_cursor
        else:
            pages.append(
                self._failed_summary(
                    f"{label} export exceeded the safe batch limit. The sync stopped before processing more records."
                )
            )

        return self._merge_page_summaries(
            pages,
            empty_message=f"No pending {label.lower()} export candidates found.",
        )

    def _combine(
        self,
        *,
        tenant_id: str,
        service_id: str,
        employee_summary: Dict[str, Any],
        contractor_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        ok = bool(employee_summary.get(const.KEY_OK)) and bool(contractor_summary.get(const.KEY_OK))
        requested = int(employee_summary.get("requested") or 0) + int(contractor_summary.get("requested") or 0)
        created = int(employee_summary.get("createdInQbo") or 0) + int(contractor_summary.get("createdInQbo") or 0)
        updated = int(employee_summary.get("updatedInQbo") or 0) + int(contractor_summary.get("updatedInQbo") or 0)
        mapped = int(employee_summary.get("mapped from QBO") or 0) + int(contractor_summary.get("mapped from QBO") or 0)
        failed = int(employee_summary.get("failed") or 0) + int(contractor_summary.get("failed") or 0)
        successful = created + updated + mapped

        messages = [
            str(summary.get(const.KEY_MESSAGE))
            for summary in (employee_summary, contractor_summary)
            if summary.get(const.KEY_OK) is False and summary.get(const.KEY_MESSAGE)
        ]
        if not ok:
            message = "; ".join(dict.fromkeys(messages)) or "Worker export failed."
        elif failed:
            message = f"Worker export completed with {failed} failed record(s). Review the listed records and retry."
        elif requested == 0:
            message = "No pending worker export candidates found."
        else:
            message = "Worker export completed successfully."

        return {
            const.KEY_OK: ok,
            const.KEY_MESSAGE: message,
            "tenantId": tenant_id,
            "serviceId": str(service_id),
            "requested": requested,
            "skippedAlreadyMapped": int(employee_summary.get("skippedAlreadyMapped") or 0)
            + int(contractor_summary.get("skippedAlreadyMapped") or 0),
            "skippedQboNewer": int(employee_summary.get("skippedQboNewer") or 0)
            + int(contractor_summary.get("skippedQboNewer") or 0),
            "mapped from QBO": mapped,
            "createdInQbo": created,
            "updatedInQbo": updated,
            "failed": failed,
            "partialSuccess": bool(failed and successful),
            const.KEY_FAILURES: list(employee_summary.get(const.KEY_FAILURES) or [])
            + list(contractor_summary.get(const.KEY_FAILURES) or []),
            const.KEY_RESULTS: list(employee_summary.get(const.KEY_RESULTS) or [])
            + list(contractor_summary.get(const.KEY_RESULTS) or []),
            "workerBreakdown": {
                "w2Employees": employee_summary,
                "contractorVendors": contractor_summary,
            },
        }

    def export_workers(
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
        employee_summary = self._validated_summary(
            self.employee_exporter.export_employees(
                tenant_id=tenant_id,
                service_id=service_id,
                limit=limit,
                offset=offset,
                object_id=object_id,
                email=email,
                user_role=user_role,
            ),
            label="W2 employee",
        )
        contractor_summary = self._validated_summary(
            self.contractor_exporter.export_contractors_as_vendors(
                tenant_id=tenant_id,
                service_id=service_id,
                limit=limit,
                offset=offset,
                object_id=object_id,
                email=email,
                user_role=user_role,
            ),
            label="1099 contractor vendor",
        )
        return self._combine(
            tenant_id=tenant_id,
            service_id=service_id,
            employee_summary=employee_summary,
            contractor_summary=contractor_summary,
        )

    def export_all_workers(
        self,
        *,
        tenant_id: str,
        service_id: str,
        batch_size: int = 500,
        object_id: Optional[str] = None,
        email: Optional[str] = None,
        user_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        safe_batch_size = max(1, min(int(batch_size or 500), const.QBO_EXPORT_LIMIT_MAX))
        employee_summary = self._drain_exporter(
            exporter=self.employee_exporter.export_employees,
            label="W2 employee",
            tenant_id=tenant_id,
            service_id=service_id,
            batch_size=safe_batch_size,
            object_id=object_id,
            email=email,
            user_role=user_role,
        )
        contractor_summary = self._drain_exporter(
            exporter=self.contractor_exporter.export_contractors_as_vendors,
            label="1099 contractor vendor",
            tenant_id=tenant_id,
            service_id=service_id,
            batch_size=safe_batch_size,
            object_id=object_id,
            email=email,
            user_role=user_role,
        )
        return self._combine(
            tenant_id=tenant_id,
            service_id=service_id,
            employee_summary=employee_summary,
            contractor_summary=contractor_summary,
        )
