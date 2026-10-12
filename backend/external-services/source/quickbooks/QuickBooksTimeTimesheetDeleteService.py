from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Set

from source.Utils import constants as const
from source.dal.TimelogRepository import TimelogRepository
from source.quickbooks.QuickBooksTimeApiService import QuickBooksTimeApiError
from source.service.QuickBooksTimeAuthService import QuickBooksTimeAuthService

logger = logging.getLogger(__name__)


class QuickBooksTimeTimesheetDeleteService:
    """Delete every timesheet in the connected QuickBooks Time company.

    Remote timesheets are discovered through the paginated QuickBooks Time API,
    including entries that were created manually, were created by another
    integration, no longer have a FIELDCRAFT mapping, or are currently on the clock.
    Delete requests are sent in documented batches of at most 50.

    A FIELDCRAFT mapping is reset only after QuickBooks confirms that its timesheet was
    deleted or was already missing. Legacy QuickBooks Accounting TimeActivity
    records are handled separately by ``QuickBooksTimeActivityDeleteService``.
    """

    DELETE_BATCH_SIZE = 50

    def __init__(self) -> None:
        self.timelog_repo = TimelogRepository()
        self.time_auth = QuickBooksTimeAuthService()

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def _positive_id(cls, value: Any) -> str:
        text = cls._clean(value)
        try:
            number = int(text)
        except (TypeError, ValueError):
            return ""
        return str(number) if number > 0 else ""

    @staticmethod
    def _chunks(values: List[str], size: int) -> Iterable[List[str]]:
        for index in range(0, len(values), size):
            yield values[index : index + size]

    @staticmethod
    def _status_code(row: Dict[str, Any]) -> int | None:
        try:
            value = row.get("_status_code")
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _status_message(row: Dict[str, Any]) -> str:
        return str(
            row.get("_status_extra")
            or row.get("_status_message")
            or "QuickBooks Time returned an unknown delete status."
        ).strip()

    @staticmethod
    def _mapping_ids(rows: List[Dict[str, Any]]) -> List[int]:
        return [int(row["id"]) for row in rows if row.get("id") is not None]

    def delete_all_mapped_timesheets(
        self,
        *,
        tenant_id: str,
        service_id: str,
        confirm_delete: bool,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Delete all remote timesheets while preserving the existing public contract."""
        tenant_id = self._clean(tenant_id)
        service_id = self._clean(service_id)
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if not service_id:
            raise ValueError("service_id is required")
        if not dry_run and not confirm_delete:
            raise ValueError("confirm_delete must be true to delete QuickBooks Time timesheets")

        mappings = self.timelog_repo.list_mapped_quickbooks_time_timesheets(
            tenant_id=tenant_id,
            service_id=service_id,
        )
        by_timesheet_id: Dict[str, List[Dict[str, Any]]] = {}
        for mapping in mappings:
            timesheet_id = self._positive_id(
                self.timelog_repo.mapping_external_id(mapping)
            )
            if timesheet_id:
                by_timesheet_id.setdefault(timesheet_id, []).append(mapping)

        summary: Dict[str, Any] = {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": service_id,
            "dryRun": bool(dry_run),
            "scope": "ALL_QUICKBOOKS_TIME_TIMESHEETS",
            "mappedRows": len(mappings),
            "mappedTimesheets": len(by_timesheet_id),
            "discoveredRemoteTimesheets": 0,
            "unmappedRemoteTimesheets": 0,
            "uniqueTimesheets": 0,
            "deleted": 0,
            "alreadyMissing": 0,
            "failed": 0,
            "resetMappings": 0,
            const.KEY_RESULTS: [],
            const.KEY_FAILURES: [],
        }

        try:
            time_api = self.time_auth.time_api_client(tenant_id, service_id)
            remote_response = time_api.all_timesheets()
        except QuickBooksTimeApiError as exc:
            logger.exception(
                "[QuickBooksTimeTimesheetDelete] unable to list company timesheets | "
                "tenant_id=%s service_id=%s",
                tenant_id,
                service_id,
            )
            summary[const.KEY_OK] = False
            summary["failed"] = 1
            summary[const.KEY_FAILURES].append(
                {
                    "action": "list_failed",
                    "error": str(exc),
                }
            )
            summary[const.KEY_MESSAGE] = (
                "QuickBooks Time timesheets could not be listed, so no deletion was attempted."
            )
            return summary

        remote_ids: Set[str] = {
            timesheet_id
            for row in list(remote_response.get("timesheets") or [])
            if isinstance(row, dict)
            for timesheet_id in [self._positive_id(row.get("id"))]
            if timesheet_id
        }
        target_ids = sorted(remote_ids | set(by_timesheet_id), key=int)
        summary["discoveredRemoteTimesheets"] = len(remote_ids)
        summary["unmappedRemoteTimesheets"] = len(remote_ids - set(by_timesheet_id))
        summary["uniqueTimesheets"] = len(target_ids)

        def detail_for(timesheet_id: str) -> Dict[str, Any]:
            mapping_rows = by_timesheet_id.get(timesheet_id, [])
            return {
                "timesheetId": timesheet_id,
                "mappingIds": self._mapping_ids(mapping_rows),
                "mappedByFieldCraft": bool(mapping_rows),
                "discoveredInQuickBooksTime": timesheet_id in remote_ids,
            }

        if dry_run:
            summary[const.KEY_RESULTS] = [
                {**detail_for(timesheet_id), "action": "would_delete"}
                for timesheet_id in target_ids
            ]
            summary[const.KEY_MESSAGE] = (
                f"{len(target_ids)} QuickBooks Time timesheet(s) would be deleted."
            )
            return summary

        if not target_ids:
            summary[const.KEY_MESSAGE] = "No QuickBooks Time timesheets were found."
            return summary

        successfully_absent: Set[str] = set()

        for batch_ids in self._chunks(target_ids, self.DELETE_BATCH_SIZE):
            try:
                response = time_api.delete_timesheets([int(value) for value in batch_ids])
                response_rows = list(response.get("timesheets") or [])
            except QuickBooksTimeApiError as exc:
                logger.exception(
                    "[QuickBooksTimeTimesheetDelete] batch request failed | timesheet_ids=%s",
                    batch_ids,
                )
                for timesheet_id in batch_ids:
                    summary["failed"] += 1
                    summary[const.KEY_FAILURES].append(
                        {
                            **detail_for(timesheet_id),
                            "action": "delete_failed",
                            "error": str(exc),
                        }
                    )
                continue

            rows_by_id = {
                timesheet_id: row
                for row in response_rows
                if isinstance(row, dict)
                for timesheet_id in [self._positive_id(row.get("id"))]
                if timesheet_id
            }
            for timesheet_id in batch_ids:
                detail = detail_for(timesheet_id)
                row = rows_by_id.get(timesheet_id)
                if row is None:
                    summary["failed"] += 1
                    summary[const.KEY_FAILURES].append(
                        {
                            **detail,
                            "action": "delete_failed",
                            "error": "QuickBooks Time did not return a status for this timesheet.",
                        }
                    )
                    continue

                status_code = self._status_code(row)
                if status_code is not None and 200 <= status_code < 300:
                    successfully_absent.add(timesheet_id)
                    summary["deleted"] += 1
                    summary[const.KEY_RESULTS].append(
                        {**detail, "action": "deleted", "statusCode": status_code}
                    )
                elif status_code == 404:
                    successfully_absent.add(timesheet_id)
                    summary["alreadyMissing"] += 1
                    summary[const.KEY_RESULTS].append(
                        {**detail, "action": "already_missing", "statusCode": status_code}
                    )
                else:
                    summary["failed"] += 1
                    summary[const.KEY_FAILURES].append(
                        {
                            **detail,
                            "action": "delete_failed",
                            "statusCode": status_code,
                            "error": self._status_message(row),
                        }
                    )

        mapped_successfully_absent = successfully_absent & set(by_timesheet_id)
        if mapped_successfully_absent:
            reason = json.dumps(
                {
                    "message": "QuickBooks Time timesheet was deleted and the timelog was queued for export again.",
                    "code": "QUICKBOOKS_TIME_TIMESHEET_DELETED",
                    "externalEntityType": "TIMESHEET",
                    "target": "QUICKBOOKS_TIME_TIMESHEET",
                },
                separators=(",", ":"),
            )
            summary["resetMappings"] = self.timelog_repo.reset_mapped_quickbooks_time_timesheets(
                tenant_id=tenant_id,
                service_id=service_id,
                timesheet_ids=sorted(mapped_successfully_absent, key=int),
                reason=reason,
            )

        summary[const.KEY_OK] = summary["failed"] == 0
        if summary["failed"]:
            summary[const.KEY_MESSAGE] = (
                "Some QuickBooks Time timesheets could not be deleted. Only confirmed deleted "
                "or already-missing FIELDCRAFT mappings were reset."
            )
        else:
            summary[const.KEY_MESSAGE] = (
                "All QuickBooks Time timesheets were deleted and mapped timelogs are ready to sync again."
            )
        return summary
