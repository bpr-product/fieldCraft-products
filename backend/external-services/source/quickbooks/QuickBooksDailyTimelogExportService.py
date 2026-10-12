from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from contextlib import contextmanager, nullcontext
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from source.Utils import constants as const
from source.Utils.auditlog import safe_audit_log
from source.Utils.ProjectTimezoneResolver import (
    ProjectTimezoneResolution,
    ProjectTimezoneResolutionError,
    ProjectTimezoneResolver,
)
from source.dal.db_session import tenant_session_scope
from source.dal.TimelogRepository import TimelogRepository
from source.dal.timelog_models import TimelogExternalMapModel
from source.quickbooks.QuickBooksTimelogExportService import QuickBooksTimelogExportService
from source.quickbooks.QuickBooksPayrollCompensationService import (
    QuickBooksPayrollCompensationService,
)

logger = logging.getLogger(__name__)

GroupKey = Tuple[int, date, int]
DayKey = Tuple[int, date, str]
WorkUnit = Tuple[GroupKey, List[Dict[str, Any]], bool]


class DailyTimelogValidationError(ValueError):
    """Source-data validation failure with a stable UI/API reason code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class QuickBooksDailyTimelogExportService(QuickBooksTimelogExportService):
    """Export one QBO Accounting TimeActivity per worker, local date, and project.

    Every eligible source timelog in the group shares the same TimeActivity id.
    W-2 workers use EmployeeRef and 1099 workers use VendorRef. The existing
    timezone, break, overlap, and moved-entry handling stays day based.
    """

    DAILY_TARGET = "QUICKBOOKS_ACCOUNTING_DAILY_TIME_ACTIVITY"
    MAX_NOTES_LENGTH = 4000

    def _payroll_compensation_service(self) -> QuickBooksPayrollCompensationService:
        service = getattr(self, "payroll_compensation_service", None)
        if service is None:
            service = QuickBooksPayrollCompensationService()
            self.payroll_compensation_service = service
        return service

    def _w2_time_entry_setting(
        self,
        *,
        rows: Sequence[Dict[str, Any]],
        access_token: str,
        base_url: str,
        realm_id: str,
    ) -> Optional[Dict[str, Any]]:
        refs = self._daily_worker_context(rows)
        if refs["worker_entity_type"] != const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE:
            return None

        try:
            setting = self.get_employee_time_entry_setting(
                access_token=access_token,
                base_url=base_url,
                realm_id=realm_id,
                employee_id=refs["worker_id"],
            )
        except Exception as exc:
            logger.warning(
                "[DailyTimelogExport] unable to check W2 UseTimeEntry; continuing export | "
                "employee_id=%s error=%s",
                refs["worker_id"],
                exc,
                exc_info=True,
            )
            return {
                "employeeId": refs["worker_id"],
                "useTimeEntry": None,
                "useTimeEntryValue": None,
                "checkError": str(exc),
            }

        if setting.get("useTimeEntry") is not True:
            logger.warning(
                "[DailyTimelogExport] W2 employee is not configured to use time entries for payroll | "
                "employee_id=%s use_time_entry=%s raw_value=%s",
                refs["worker_id"],
                setting.get("useTimeEntry"),
                setting.get("useTimeEntryValue"),
            )
        return setting

    def _add_w2_payroll_item(
        self,
        *,
        payload: Dict[str, Any],
        rows: Sequence[Dict[str, Any]],
        access_token: str,
    ) -> Optional[Dict[str, str]]:
        refs = self._daily_worker_context(rows)
        if refs["worker_entity_type"] != const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE:
            return None

        employee_id = refs["worker_id"]
        payroll_service = self._payroll_compensation_service()
        compensation = payroll_service.resolve_regular_pay(
            access_token=access_token,
            qbo_employee_id=employee_id,
        )
        payload["PayrollItemRef"] = {"value": compensation["id"]}
        logger.info(
            "[DailyTimelogExport] W2 payroll pay type resolved | "
            "employee_id=%s payroll_item_id=%s payroll_item_name=%s",
            employee_id,
            compensation["id"],
            compensation["name"],
        )
        return compensation

    @staticmethod
    def _reason_payload(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        raw = str(value or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @classmethod
    def _daily_reason(cls, message: str, **extra: Any) -> str:
        payload: Dict[str, Any] = {
            "message": message,
            "externalEntityType": cls.EXTERNAL_ENTITY_TYPE,
            "target": cls.DAILY_TARGET,
            "dailyEntry": True,
        }
        payload.update({key: value for key, value in extra.items() if value is not None})
        return json.dumps(payload, default=str, separators=(",", ":"))

    def _timezone_resolver(self) -> ProjectTimezoneResolver:
        resolver = getattr(self, "project_timezone_resolver", None)
        if resolver is None:
            resolver = ProjectTimezoneResolver()
            self.project_timezone_resolver = resolver
        return resolver

    def _timezone_resolution(
        self,
        candidate: Dict[str, Any],
    ) -> ProjectTimezoneResolution:
        return self._timezone_resolver().resolve(candidate)

    @staticmethod
    def _as_aware_datetime(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            parsed = value
        else:
            raw = str(value or "").strip()
            if not raw:
                return None
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _work_date(self, candidate: Dict[str, Any]) -> Optional[date]:
        start = self._as_aware_datetime(candidate.get("start_time"))
        if start is None:
            return None
        return start.astimezone(self._timezone_resolution(candidate).timezone).date()

    def _group_key(self, candidate: Dict[str, Any]) -> Optional[GroupKey]:
        try:
            user_account_id = int(candidate.get("user_account_id") or 0)
            project_id = int(candidate.get("project_id") or 0)
        except (TypeError, ValueError):
            user_account_id = 0
            project_id = 0
        if not user_account_id or not project_id:
            return None
        work_date = self._work_date(candidate)
        if work_date is None:
            return None
        return user_account_id, work_date, project_id

    def _day_key(
        self,
        candidate: Dict[str, Any],
        *,
        previous: bool = False,
    ) -> Optional[DayKey]:
        group_key = (
            self._previous_group_key(candidate)
            if previous
            else self._group_key(candidate)
        )
        if group_key is None:
            return None
        resolution = self._timezone_resolution(candidate)
        return group_key[0], group_key[1], resolution.timezone_name

    def _group_timezone_resolution(
        self,
        triggers: Sequence[Dict[str, Any]],
    ) -> ProjectTimezoneResolution:
        resolutions = [self._timezone_resolution(row) for row in triggers]
        if not resolutions:
            raise ProjectTimezoneResolutionError(
                "PROJECT_TIMEZONE_UNRESOLVED",
                "The daily group does not contain a project timezone source.",
            )
        timezone_names = {item.timezone_name for item in resolutions}
        if len(timezone_names) != 1:
            raise ProjectTimezoneResolutionError(
                "DAILY_TIMEZONE_CONFLICT",
                "The same daily project group resolved to multiple timezones. "
                "Review the project location and retry.",
            )
        return resolutions[0]

    @classmethod
    def _previous_group_key(cls, candidate: Dict[str, Any]) -> Optional[GroupKey]:
        payload = cls._reason_payload(candidate.get("reason"))
        if not payload.get("dailyEntry"):
            return None
        try:
            user_account_id = int(
                payload.get("dailyUserAccountId") or payload.get("daily_user_account_id") or 0
            )
        except (TypeError, ValueError):
            user_account_id = 0
        raw_date = str(
            payload.get("dailyWorkDate") or payload.get("daily_work_date") or ""
        ).strip()
        try:
            project_id = int(
                payload.get("dailyProjectId") or payload.get("daily_project_id") or 0
            )
        except (TypeError, ValueError):
            project_id = 0
        try:
            work_date = date.fromisoformat(raw_date)
        except ValueError:
            work_date = None
        if not user_account_id or not work_date or not project_id:
            return None
        return user_account_id, work_date, project_id

    @staticmethod
    def _group_key_text(group_key: GroupKey) -> str:
        return f"{group_key[0]}:{group_key[1].isoformat()}:{group_key[2]}"

    @classmethod
    def _daily_marker(cls, group_key: GroupKey) -> str:
        return f"FIELDCRAFT Daily Project Entry: {cls._group_key_text(group_key)}"

    def _pending_trigger_candidates(
        self,
        *,
        tenant_id: str,
        service_id: str,
        limit: int,
        offset: int,
        after_mapping_id: Optional[int],
        external_account_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Load approved queue triggers through the schema-v3 task relationship."""
        return self.timelog_repo.get_daily_pending_candidates(
            tenant_id=tenant_id,
            service_id=service_id,
            limit=limit,
            offset=offset,
            after_mapping_id=after_mapping_id,
            external_account_id=external_account_id,
        )

    def _daily_group_stmt(
        self,
        *,
        tenant_id: str,
        service_id: str,
        user_account_id: int,
        work_date: date,
        project_id: Optional[int],
        external_account_id: Optional[str],
        timezone_name: str,
    ):
        """Build the schema-v3 worker/day/project query."""
        local_start = datetime.combine(work_date, time.min, tzinfo=ZoneInfo(timezone_name))
        utc_start = local_start.astimezone(timezone.utc)
        utc_end = (local_start + timedelta(days=1)).astimezone(timezone.utc)
        return self.timelog_repo._daily_schema_source_stmt(
            tenant_id=tenant_id,
            service_id=service_id,
            external_account_id=external_account_id,
            user_account_id=user_account_id,
            project_id=project_id,
            start_at=utc_start,
            end_at=utc_end,
        )

    def _load_rows_for_worker_day(
        self,
        *,
        tenant_id: str,
        service_id: str,
        user_account_id: int,
        work_date: date,
        project_id: Optional[int],
        realm_id: Optional[str],
        timezone_name: str,
    ) -> List[Dict[str, Any]]:
        local_start = datetime.combine(work_date, time.min, tzinfo=ZoneInfo(timezone_name))
        utc_start = local_start.astimezone(timezone.utc)
        utc_end = (local_start + timedelta(days=1)).astimezone(timezone.utc)
        return self.timelog_repo.get_daily_worker_day_rows(
            tenant_id=tenant_id,
            service_id=service_id,
            user_account_id=user_account_id,
            start_at=utc_start,
            end_at=utc_end,
            project_id=project_id,
            external_account_id=realm_id,
        )

    def _load_daily_group_rows(
        self,
        *,
        tenant_id: str,
        service_id: str,
        group_key: GroupKey,
        realm_id: Optional[str],
        timezone_name: str,
    ) -> List[Dict[str, Any]]:
        return self._load_rows_for_worker_day(
            tenant_id=tenant_id,
            service_id=service_id,
            user_account_id=group_key[0],
            work_date=group_key[1],
            project_id=group_key[2],
            realm_id=realm_id,
            timezone_name=timezone_name,
        )

    def _load_worker_day_rows(
        self,
        *,
        tenant_id: str,
        service_id: str,
        user_account_id: int,
        work_date: date,
        realm_id: Optional[str],
        timezone_name: str,
    ) -> List[Dict[str, Any]]:
        return self._load_rows_for_worker_day(
            tenant_id=tenant_id,
            service_id=service_id,
            user_account_id=user_account_id,
            work_date=work_date,
            project_id=None,
            realm_id=realm_id,
            timezone_name=timezone_name,
        )

    def _rows_for_group(
        self,
        rows: Sequence[Dict[str, Any]],
        *,
        group_key: GroupKey,
        timezone_name: str,
    ) -> List[Dict[str, Any]]:
        grouped: List[Dict[str, Any]] = []
        for row in rows:
            try:
                row_key = self._group_key(row)
                row_timezone = self._timezone_resolution(row).timezone_name
            except ProjectTimezoneResolutionError:
                # The unresolved row is handled through its own mapping trigger.
                # It must not contaminate another valid project-day group.
                continue
            if row_key == group_key and row_timezone == timezone_name:
                grouped.append(row)
        return grouped

    @classmethod
    def _source_is_eligible(cls, row: Dict[str, Any]) -> bool:
        entry_type = cls._clean(row.get("entry_type")).upper()
        start = cls._as_aware_datetime(row.get("start_time"))
        end = cls._as_aware_datetime(row.get("end_time"))
        return (
            cls._clean(row.get("approval_status")).upper()
            == TimelogRepository.APPROVAL_STATUS_APPROVED
            and cls._clean(row.get("timelog_status")).lower() == "completed"
            and not bool(row.get("timelog_invalid"))
            and entry_type in {"", "WORK"}
            and start is not None
            and end is not None
            and end > start
        )

    def _validate_eligible_row(
        self,
        row: Dict[str, Any],
        *,
        timezone_name: str,
    ) -> None:
        timelog_id = int(row.get("timelog_id") or 0)
        start = self._as_aware_datetime(row.get("start_time"))
        end = self._as_aware_datetime(row.get("end_time"))
        if not start or not end or end <= start:
            raise DailyTimelogValidationError(
                "TIMELOG_INVALID_TIME_RANGE",
                f"Timelog {timelog_id} has an invalid start or end time.",
            )
        row_timezone = self._timezone_resolution(row).timezone_name
        if row_timezone != timezone_name:
            raise DailyTimelogValidationError(
                "DAILY_TIMEZONE_CONFLICT",
                f"Timelog {timelog_id} resolved to {row_timezone}, but its daily group "
                f"uses {timezone_name}. Review the project location and retry.",
            )
        local_tz = ZoneInfo(timezone_name)
        if start.astimezone(local_tz).date() != end.astimezone(local_tz).date():
            raise DailyTimelogValidationError(
                "TIMELOG_CROSSES_PROJECT_MIDNIGHT",
                f"Timelog {timelog_id} crosses midnight in {timezone_name}. Split the "
                "source timelog before export because one existing mapping row cannot "
                "safely represent two QuickBooks daily TimeActivity records.",
            )
        if self._clean(row.get("employee_sync_status")).upper() != TimelogRepository.STATUS_SYNCED:
            raise ValueError(
                "Employee or contractor must be synced before exporting daily time."
            )
        if self._clean(row.get("project_sync_status")).upper() != TimelogRepository.STATUS_SYNCED:
            raise ValueError("Project must be synced before exporting daily time.")
        if not self._clean(row.get("role_name")):
            raise ValueError("Task assignee role is missing.")
        if self._clean(row.get("service_sync_status")).upper() != TimelogRepository.STATUS_SYNCED:
            raise ValueError("Service role must be synced before exporting daily time.")

    @classmethod
    def _parse_breaks(cls, row: Dict[str, Any]) -> List[Tuple[datetime, datetime]]:
        raw = row.get("break_times")
        if raw in (None, "", "NULL"):
            return []
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError):
                raise ValueError(
                    f"Timelog {int(row.get('timelog_id') or 0)} has invalid break_times JSON."
                )
        if isinstance(raw, dict):
            raw = raw.get("breaks") or raw.get("items") or [raw]
        if not isinstance(raw, list):
            raise ValueError(
                f"Timelog {int(row.get('timelog_id') or 0)} has invalid break_times data."
            )

        intervals: List[Tuple[datetime, datetime]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            break_start = cls._as_aware_datetime(
                item.get("startTime") or item.get("start_time") or item.get("start")
            )
            break_end = cls._as_aware_datetime(
                item.get("endTime") or item.get("end_time") or item.get("end")
            )
            if not break_start or not break_end or break_end <= break_start:
                continue
            intervals.append((break_start, break_end))
        return intervals

    @classmethod
    def _work_segments(cls, row: Dict[str, Any]) -> List[Tuple[datetime, datetime]]:
        start = cls._as_aware_datetime(row.get("start_time"))
        end = cls._as_aware_datetime(row.get("end_time"))
        if not start or not end or end <= start:
            return []

        breaks: List[Tuple[datetime, datetime]] = []
        for break_start, break_end in cls._parse_breaks(row):
            clipped_start = max(start, break_start)
            clipped_end = min(end, break_end)
            if clipped_end > clipped_start:
                breaks.append((clipped_start, clipped_end))
        breaks.sort(key=lambda item: item[0])

        merged: List[Tuple[datetime, datetime]] = []
        for break_start, break_end in breaks:
            if merged and break_start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], break_end))
            else:
                merged.append((break_start, break_end))

        segments: List[Tuple[datetime, datetime]] = []
        cursor = start
        for break_start, break_end in merged:
            if break_start > cursor:
                segments.append((cursor, break_start))
            cursor = max(cursor, break_end)
        if cursor < end:
            segments.append((cursor, end))
        return segments

    def _daily_duration_seconds(self, rows: Sequence[Dict[str, Any]]) -> int:
        row_segments: List[Tuple[datetime, datetime, int]] = []
        for row in rows:
            timelog_id = int(row.get("timelog_id") or 0)
            segments = self._work_segments(row)
            if not segments:
                raise ValueError(f"Timelog {timelog_id} has no payable duration after breaks.")
            row_segments.extend((start, end, timelog_id) for start, end in segments)

        row_segments.sort(key=lambda item: (item[0], item[1], item[2]))
        previous_end: Optional[datetime] = None
        previous_timelog_id: Optional[int] = None
        for start, end, timelog_id in row_segments:
            if previous_end and start < previous_end and timelog_id != previous_timelog_id:
                raise ValueError(
                    f"Timelog {timelog_id} overlaps timelog {previous_timelog_id}. "
                    "Correct the overlapping FIELDCRAFT time before daily export."
                )
            if previous_end is None or end > previous_end:
                previous_end = end
                previous_timelog_id = timelog_id

        total_seconds = int(sum((end - start).total_seconds() for start, end, _ in row_segments))
        if total_seconds <= 0:
            raise ValueError("Daily duration must be greater than zero.")
        return total_seconds

    @staticmethod
    def _format_duration(total_seconds: int) -> str:
        total_minutes, seconds = divmod(max(0, int(total_seconds)), 60)
        hours, minutes = divmod(total_minutes, 60)
        parts: List[str] = []
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if seconds:
            parts.append(f"{seconds}s")
        return " ".join(parts) or "0m"

    def _daily_notes(
        self,
        *,
        group_key: GroupKey,
        rows: Sequence[Dict[str, Any]],
        total_seconds: int,
        timezone_name: str,
        hourly_cost: Optional[float] = None,
        include_internal_cost: bool = False,
    ) -> str:
        local_tz = ZoneInfo(timezone_name)
        first = rows[0]
        employee_name = self._clean(
            f"{self._clean(first.get('firstname'))} {self._clean(first.get('lastname'))}"
        )
        marker = self._daily_marker(group_key)
        project_name = self._clean(first.get("project_name"))
        project_order_no = self._clean(first.get("project_order_no"))
        project_text = project_name
        if project_order_no and project_order_no.lower() not in project_name.lower():
            project_text = (
                f"{project_name} [{project_order_no}]" if project_name else project_order_no
            )
        header = [
            marker,
            f"Date: {group_key[1].isoformat()}",
            f"Employee: {employee_name or group_key[0]}",
            f"Project: {project_text or group_key[2]}",
            f"Total: {self._format_duration(total_seconds)}",
        ]
        if include_internal_cost and hourly_cost is not None:
            header.append(f"FIELDCRAFT internal labor cost: ${hourly_cost:.2f}/hour.")
        header.append("Tasks:")

        task_lines: List[str] = []
        ordered = sorted(
            rows,
            key=lambda row: (
                self._as_aware_datetime(row.get("start_time")) or datetime.min.replace(tzinfo=timezone.utc),
                int(row.get("timelog_id") or 0),
            ),
        )
        for row in ordered:
            start = self._as_aware_datetime(row.get("start_time"))
            end = self._as_aware_datetime(row.get("end_time"))
            row_seconds = int(
                sum(
                    (segment_end - segment_start).total_seconds()
                    for segment_start, segment_end in self._work_segments(row)
                )
            )
            time_range = ""
            if start and end:
                time_range = (
                    f"{start.astimezone(local_tz).strftime('%H:%M')}-"
                    f"{end.astimezone(local_tz).strftime('%H:%M')}"
                )
            project_name = self._clean(row.get("project_name"))
            project_order_no = self._clean(row.get("project_order_no"))
            project_text = project_name
            if project_order_no and project_order_no.lower() not in project_name.lower():
                project_text = f"{project_name} [{project_order_no}]" if project_name else project_order_no
            context = " / ".join(
                value
                for value in (project_text, self._clean(row.get("role_name")))
                if value
            )
            task_name = self._clean(row.get("task_name")) or "Task"
            line = (
                f"- {time_range} | {task_name} | {self._format_duration(row_seconds)}"
                f"{f' | {context}' if context else ''} | FIELDCRAFT {int(row.get('timelog_id') or 0)}"
            )
            task_lines.append(line)

        notes_lines = list(header)
        omitted = 0
        for index, line in enumerate(task_lines):
            remaining_count = len(task_lines) - index - 1
            suffix = f"\n... {remaining_count} additional task(s) available in FIELDCRAFT." if remaining_count else ""
            candidate = "\n".join(notes_lines + [line]) + suffix
            if len(candidate) > self.MAX_NOTES_LENGTH:
                omitted = len(task_lines) - index
                break
            notes_lines.append(line)
        if omitted:
            footer = f"... {omitted} additional task(s) available in FIELDCRAFT."
            while notes_lines and len("\n".join(notes_lines + [footer])) > self.MAX_NOTES_LENGTH:
                notes_lines.pop()
            notes_lines.append(footer)
        return "\n".join(notes_lines)[: self.MAX_NOTES_LENGTH]

    def _daily_worker_context(self, rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        if not rows:
            raise ValueError("Daily time activity has no eligible source rows.")

        worker_types = {self.worker_entity_type(row) for row in rows}
        if len(worker_types) != 1:
            raise ValueError("Daily project group resolved to multiple worker types.")
        worker_entity_type = next(iter(worker_types))

        if worker_entity_type == const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR:
            worker_ids = {self._clean(row.get("qbo_vendor_id")) for row in rows}
            worker_ids.discard("")
            if len(worker_ids) != 1:
                raise ValueError("1099 contractor must resolve to one QuickBooks Vendor.")
            name_of = "Vendor"
            ref_name = "VendorRef"
        else:
            worker_ids = {self._clean(row.get("qbo_employee_id")) for row in rows}
            worker_ids.discard("")
            if len(worker_ids) != 1:
                raise ValueError("W2 worker must resolve to one QuickBooks Employee.")
            name_of = "Employee"
            ref_name = "EmployeeRef"

        project_ids = {self._clean(row.get("qbo_project_id")) for row in rows}
        project_ids.discard("")
        if len(project_ids) != 1:
            raise ValueError("Daily project group must resolve to one QuickBooks project mapping.")
        qbo_project_id = next(iter(project_ids))

        project_targets = {self._project_sync_target(row) for row in rows}
        if len(project_targets) != 1:
            raise ValueError("Daily project group resolved to multiple QuickBooks project targets.")
        project_target = next(iter(project_targets))

        result: Dict[str, Any] = {
            "worker_entity_type": worker_entity_type,
            "worker_id": next(iter(worker_ids)),
            "name_of": name_of,
            "ref_name": ref_name,
            "qbo_project_id": qbo_project_id,
            "project_target": project_target,
        }
        if project_target == const.PROJECT_SYNC_TARGET_QBO_PROJECT:
            parent_ids = {self._clean(row.get("qbo_parent_customer_id")) for row in rows}
            parent_ids.discard("")
            if len(parent_ids) != 1:
                raise ValueError("QBO Project daily time requires one parent customer mapping.")
            result["qbo_parent_customer_id"] = next(iter(parent_ids))

        service_ids = {self._clean(row.get("qbo_service_id")) for row in rows}
        service_ids.discard("")
        result["service_ids"] = sorted(service_ids)
        return result

    def _daily_cost_context(
        self,
        *,
        rows: Sequence[Dict[str, Any]],
        tenant_id: Optional[str] = None,
        service_id: Optional[str] = None,
    ) -> Tuple[Optional[float], bool]:
        if not rows or not any(row.get("resource_cost") not in (None, "") for row in rows):
            return None, False

        override = getattr(self, "_employee_sync_config", None)
        config = (
            override
            if isinstance(override, dict)
            else self._load_employee_sync_config(tenant_id, service_id)
        )
        hourly_costs = [self._hourly_cost(row, config) for row in rows]
        # A grouped TimeActivity can carry only one rate. Apply it only when all
        # source timelogs resolve to the same hourly cost.
        first_cost = hourly_costs[0]
        hourly_cost = (
            first_cost
            if first_cost is not None and all(cost == first_cost for cost in hourly_costs)
            else None
        )
        qbo_cost_field = self._clean(
            config.get(const.KEY_EMPLOYEE_SYNC_COST_QBO_FIELD)
            or const.EMPLOYEE_COST_QBO_FIELD_COST_RATE
        )
        use_customer_bill_rate = (
            bool(config.get(const.KEY_EMPLOYEE_SYNC_COST_ENABLED, True))
            and qbo_cost_field == const.EMPLOYEE_COST_QBO_FIELD_BILL_RATE
            and hourly_cost is not None
        )
        return hourly_cost, use_customer_bill_rate

    def _build_daily_payload(
        self,
        *,
        group_key: GroupKey,
        rows: Sequence[Dict[str, Any]],
        total_seconds: int,
        timezone_name: str,
        tenant_id: Optional[str] = None,
        service_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if total_seconds > 24 * 60 * 60:
            raise ValueError("Daily time activity cannot exceed 24 hours.")
        # QuickBooks TimeActivity accepts whole hours and minutes. Preserve
        # seconds while aggregating FIELDCRAFT timelogs, then export only completed
        # whole minutes so QuickBooks never overstates worked time.
        total_minutes = int(total_seconds) // 60
        hours, minutes = divmod(total_minutes, 60)
        refs = self._daily_worker_context(rows)
        hourly_cost, use_customer_bill_rate = self._daily_cost_context(
            rows=rows,
            tenant_id=tenant_id,
            service_id=service_id,
        )
        payload: Dict[str, Any] = {
            "TxnDate": group_key[1].isoformat(),
            "NameOf": refs["name_of"],
            refs["ref_name"]: {"value": refs["worker_id"]},
            "Hours": hours,
            "Minutes": minutes,
            "Description": self._daily_notes(
                group_key=group_key,
                rows=rows,
                total_seconds=total_seconds,
                timezone_name=timezone_name,
                hourly_cost=hourly_cost,
                include_internal_cost=not use_customer_bill_rate,
            ),
        }
        if refs["project_target"] == const.PROJECT_SYNC_TARGET_QBO_PROJECT:
            payload["CustomerRef"] = {"value": refs["qbo_parent_customer_id"]}
            payload["ProjectRef"] = {"value": refs["qbo_project_id"]}
        else:
            payload["CustomerRef"] = {"value": refs["qbo_project_id"]}

        # One TimeActivity can carry only one service item. Keep the entry daily;
        # when tasks use multiple roles, the exact role/task breakdown stays in
        # Description instead of assigning an inaccurate ItemRef.
        if len(refs["service_ids"]) == 1:
            payload["ItemRef"] = {"value": refs["service_ids"][0]}
        if use_customer_bill_rate:
            payload["HourlyRate"] = hourly_cost
            payload["BillableStatus"] = "Billable"
        return payload

    def _mapped_time_activity_ids(
        self,
        *,
        rows: Iterable[Dict[str, Any]],
        realm_id: str,
    ) -> List[str]:
        ordered: List[str] = []
        seen: Set[str] = set()
        for row in rows:
            if self.timelog_repo.mapping_external_entity_type(row) != self.EXTERNAL_ENTITY_TYPE:
                continue
            external_id = self._clean(self.timelog_repo.mapping_external_id(row))
            if not external_id:
                continue
            mapped_realm = self._clean(row.get("external_account_id"))
            if mapped_realm and mapped_realm != self._clean(realm_id):
                raise ValueError(
                    "A daily timelog mapping belongs to a different QuickBooks company."
                )
            if external_id not in seen:
                seen.add(external_id)
                ordered.append(external_id)
        return ordered

    @contextmanager
    def _daily_group_lock(self, *, tenant_id: str, group_key: GroupKey):
        lock_text = f"qbo-daily-time:{tenant_id}:{self._group_key_text(group_key)}"
        digest = hashlib.blake2b(lock_text.encode("utf-8"), digest_size=8).digest()
        lock_id = int.from_bytes(digest, byteorder="big", signed=True)
        with tenant_session_scope(tenant_id) as session:
            bind = session.get_bind()
            if getattr(getattr(bind, "dialect", None), "name", "") != "postgresql":
                yield
                return
            acquired = bool(session.execute(select(func.pg_try_advisory_lock(lock_id))).scalar())
            if not acquired:
                raise ValueError(
                    f"Daily time activity {self._group_key_text(group_key)} is already being synchronized."
                )
            try:
                yield
            finally:
                session.execute(select(func.pg_advisory_unlock(lock_id)))

    def _save_pending_snapshot(
        self,
        *,
        tenant_id: str,
        eligible_rows: Sequence[Dict[str, Any]],
        reason: str,
    ) -> None:
        map_ids = {
            int(row.get("map_id") or 0)
            for row in eligible_rows
            if int(row.get("map_id") or 0)
        }
        if not map_ids:
            return
        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(
                select(TimelogExternalMapModel).where(
                    TimelogExternalMapModel.id.in_(map_ids),
                    TimelogExternalMapModel.tenant_id == tenant_id,
                )
            ).scalars().all()
            now = self.timelog_repo._now()
            for row in rows:
                row.sync_status = TimelogRepository.STATUS_PENDING_PUSH
                row.reason = reason
                row.updated_at = now
                session.add(row)

    def _save_group_success(
        self,
        *,
        tenant_id: str,
        eligible_rows: Sequence[Dict[str, Any]],
        clear_map_ids: Iterable[int],
        preserve_map_ids: Iterable[int],
        time_activity_id: str,
        realm_id: str,
        sync_token: Optional[str],
        reason: str,
        clear_reason: str,
    ) -> None:
        eligible_map_ids = {int(row.get("map_id") or 0) for row in eligible_rows}
        clear_ids = {int(value) for value in clear_map_ids if int(value or 0)}
        preserve_ids = {int(value) for value in preserve_map_ids if int(value or 0)}
        target_ids = eligible_map_ids | clear_ids | preserve_ids
        if not target_ids:
            return
        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(
                select(TimelogExternalMapModel).where(
                    TimelogExternalMapModel.id.in_(target_ids),
                    TimelogExternalMapModel.tenant_id == tenant_id,
                )
            ).scalars().all()
            now = self.timelog_repo._now()
            for row in rows:
                map_id = int(row.id)
                row.external_entity_type = self.EXTERNAL_ENTITY_TYPE
                if map_id in eligible_map_ids:
                    external_id = self._clean(time_activity_id)
                    row.external_time_activity_id = external_id or None
                    row.external_time_timesheet_id = None
                    row.external_account_id = (
                        (self._clean(realm_id) or None) if external_id else None
                    )
                    row.sync_token = (
                        (self._clean(sync_token) or None) if external_id else None
                    )
                    row.sync_status = TimelogRepository.STATUS_SYNCED
                    row.reason = reason
                else:
                    row.external_time_activity_id = None
                    row.external_time_timesheet_id = None
                    row.external_account_id = None
                    row.sync_token = None
                    row.sync_status = TimelogRepository.STATUS_PENDING_PUSH
                    row.reason = clear_reason
                row.updated_at = now
                session.add(row)

    def _clear_group_mappings(
        self,
        *,
        tenant_id: str,
        map_ids: Iterable[int],
        preserve_map_ids: Iterable[int],
        reason: str,
    ) -> None:
        self._save_group_success(
            tenant_id=tenant_id,
            eligible_rows=[],
            clear_map_ids=map_ids,
            preserve_map_ids=preserve_map_ids,
            time_activity_id="",
            realm_id="",
            sync_token=None,
            reason=reason,
            clear_reason=reason,
        )

    @staticmethod
    def _is_not_ready_error(error: Exception) -> bool:
        return isinstance(error, (ProjectTimezoneResolutionError, DailyTimelogValidationError))

    def _mark_trigger_failures(
        self,
        *,
        tenant_id: str,
        triggers: Sequence[Dict[str, Any]],
        group_key: GroupKey,
        error: Exception,
        timezone_resolution: Optional[ProjectTimezoneResolution] = None,
    ) -> str:
        not_ready = self._is_not_ready_error(error)
        status = (
            TimelogRepository.STATUS_PENDING_PUSH
            if not_ready
            else TimelogRepository.STATUS_SYNC_FAILED
        )
        reason = self._daily_reason(
            str(error),
            code=self._clean(getattr(error, "code", "")) or "QBO_DAILY_TIME_ACTIVITY_EXPORT_FAILED",
            dailyGroupKey=self._group_key_text(group_key),
            dailyUserAccountId=group_key[0] or None,
            dailyWorkDate=group_key[1].isoformat() if group_key[1] != date.min else None,
            dailyProjectId=group_key[2] or None,
            dailyTimezone=timezone_resolution.timezone_name if timezone_resolution else None,
            dailyTimezoneSource=timezone_resolution.source if timezone_resolution else None,
        )
        for row in triggers:
            map_id = int(row.get("map_id") or 0)
            if map_id:
                self.timelog_repo.mark_failed(
                    tenant_id=tenant_id,
                    map_id=map_id,
                    status=status,
                    reason=reason,
                )
        return "not_ready" if not_ready else "failed"

    def _process_daily_group(
        self,
        *,
        group_key: GroupKey,
        triggers: Sequence[Dict[str, Any]],
        is_previous_group: bool,
        tenant_id: str,
        service_id: str,
        realm_id: str,
        base_url: str,
        access_token: str,
        dry_run: bool,
        summary: Dict[str, Any],
    ) -> bool:
        trigger_map_ids = {
            int(row.get("map_id") or 0)
            for row in triggers
            if int(row.get("map_id") or 0)
        }
        detail: Dict[str, Any] = {
            "dailyGroupKey": self._group_key_text(group_key),
            "fieldcraftUserAccountId": group_key[0],
            "workDate": group_key[1].isoformat(),
            "fieldcraftProjectId": group_key[2],
            "triggerMapIds": sorted(trigger_map_ids),
            "previousGroupRecalculation": bool(is_previous_group),
            "destination": self.DAILY_TARGET,
        }
        timezone_resolution: Optional[ProjectTimezoneResolution] = None
        try:
            timezone_resolution = self._group_timezone_resolution(triggers)
            timezone_name = timezone_resolution.timezone_name
            detail.update(
                {
                    "projectTimezone": timezone_name,
                    "projectTimezoneSource": timezone_resolution.source,
                }
            )
            lock_context = nullcontext() if dry_run else self._daily_group_lock(
                tenant_id=tenant_id, group_key=group_key
            )
            with lock_context:
                loaded_rows = self._load_daily_group_rows(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    group_key=group_key,
                    realm_id=realm_id,
                    timezone_name=timezone_name,
                )
                all_rows = self._rows_for_group(
                    loaded_rows,
                    group_key=group_key,
                    timezone_name=timezone_name,
                )
                mapped_rows: Sequence[Dict[str, Any]] = (
                    [*all_rows, *triggers] if is_previous_group else all_rows
                )
                mapped_ids = self._mapped_time_activity_ids(rows=mapped_rows, realm_id=realm_id)
                if len(mapped_ids) > 1:
                    raise ValueError(
                        "This daily group is mapped to multiple QuickBooks TimeActivity records. "
                        "Use the development cleanup and sync it again."
                    )

                eligible_rows = [row for row in all_rows if self._source_is_eligible(row)]
                for row in eligible_rows:
                    self._validate_eligible_row(row, timezone_name=timezone_name)
                    validation_error = self.validate_candidate(
                        {**row, "map_sync_status": TimelogRepository.STATUS_PENDING_PUSH},
                        tenant_id=tenant_id,
                        service_id=service_id,
                    )
                    if validation_error and "pending or retryable" not in validation_error.lower():
                        raise ValueError(validation_error)

                all_group_map_ids = {
                    int(row.get("map_id") or 0)
                    for row in all_rows
                    if int(row.get("map_id") or 0)
                }
                eligible_map_ids = {
                    int(row.get("map_id") or 0)
                    for row in eligible_rows
                    if int(row.get("map_id") or 0)
                }
                preserve_map_ids = {
                    int(row.get("map_id") or 0)
                    for row in triggers
                    if int(row.get("map_id") or 0) and self._group_key(row) != group_key
                }
                clear_map_ids = (all_group_map_ids | trigger_map_ids) - eligible_map_ids

                if not eligible_rows:
                    if dry_run:
                        detail.update(
                            {
                                "status": "dry_run",
                                "action": "delete_or_clear",
                                "quickBooksTimeActivityIds": mapped_ids,
                            }
                        )
                        summary["skipped"] += 1
                        summary[const.KEY_RESULTS].append(detail)
                        return True
                    deleted = False
                    for external_id in mapped_ids:
                        deleted = self.delete_time_activity(
                            access_token=access_token,
                            base_url=base_url,
                            realm_id=realm_id,
                            time_activity_id=external_id,
                        ) or deleted
                    reason = self._daily_reason(
                        "Timelog excluded from the approved daily total.",
                        code="TIMELOG_NOT_READY",
                        dailyUserAccountId=group_key[0],
                        dailyWorkDate=group_key[1].isoformat(),
                        dailyProjectId=group_key[2],
                        dailyTimezone=timezone_name,
                        dailyTimezoneSource=timezone_resolution.source,
                    )
                    self._clear_group_mappings(
                        tenant_id=tenant_id,
                        map_ids=clear_map_ids,
                        preserve_map_ids=preserve_map_ids,
                        reason=reason,
                    )
                    detail.update(
                        {
                            "status": "not_ready",
                            "action": "deleted_and_kept_pending" if deleted else "kept_pending",
                            "quickBooksTimeActivityIds": mapped_ids,
                            "taskCount": 0,
                        }
                    )
                    if deleted:
                        summary["updatedInQbo"] += 1
                        summary["updatedInAccounting"] += 1
                        summary["updatedTimeActivities"] += 1
                    else:
                        summary["skipped"] += 1
                    summary[const.KEY_RESULTS].append(detail)
                    return True

                total_seconds = self._daily_duration_seconds(eligible_rows)
                payload = self._build_daily_payload(
                    group_key=group_key,
                    rows=eligible_rows,
                    total_seconds=total_seconds,
                    timezone_name=timezone_name,
                    tenant_id=tenant_id,
                    service_id=service_id,
                )
                time_entry_setting = self._w2_time_entry_setting(
                    rows=eligible_rows,
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=realm_id,
                )
                if time_entry_setting:
                    detail.update(
                        {
                            "useTimeEntry": time_entry_setting.get("useTimeEntry"),
                            "useTimeEntryValue": time_entry_setting.get("useTimeEntryValue"),
                            "useTimeEntryCheckError": time_entry_setting.get("checkError"),
                        }
                    )

                payroll_compensation = self._add_w2_payroll_item(
                    payload=payload,
                    rows=eligible_rows,
                    access_token=access_token,
                )
                if payroll_compensation:
                    detail.update(
                        {
                            "payrollItemId": payroll_compensation["id"],
                            "payrollItemName": payroll_compensation["name"],
                        }
                    )
                logger.info(
                    "[DailyTimelogExport] TimeActivity payload prepared | tenant_id=%s service_id=%s "
                    "group=%s timezone=%s task_count=%s duration=%s payload=%s",
                    tenant_id,
                    service_id,
                    self._group_key_text(group_key),
                    timezone_name,
                    len(eligible_rows),
                    total_seconds,
                    json.dumps(payload, sort_keys=True, default=str),
                )
                if dry_run:
                    detail.update(
                        {
                            "status": "dry_run",
                            "action": "create_or_update",
                            "taskCount": len(eligible_rows),
                            "fieldcraftTimelogIds": [int(row.get("timelog_id") or 0) for row in eligible_rows],
                            "durationSeconds": total_seconds,
                            "payload": payload,
                        }
                    )
                    summary["skipped"] += 1
                    summary[const.KEY_RESULTS].append(detail)
                    return True

                action = "created"
                result: Dict[str, Any]
                external_id = mapped_ids[0] if mapped_ids else ""
                if external_id:
                    current = self.get_time_activity(
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=realm_id,
                        time_activity_id=external_id,
                    )
                    if current:
                        sync_token = self._clean(current.get(const.QBO_KEY_SYNC_TOKEN))
                        if not sync_token:
                            raise ValueError(
                                f"QuickBooks TimeActivity {external_id} did not return a SyncToken."
                            )
                        result = self.update_time_activity(
                            access_token=access_token,
                            base_url=base_url,
                            realm_id=realm_id,
                            time_activity_id=external_id,
                            sync_token=sync_token,
                            payload=payload,
                        )
                        action = "updated"
                    else:
                        external_id = ""
                if not external_id:
                    pending_reason = self._daily_reason(
                        "QuickBooks daily TimeActivity creation pending.",
                        code="QBO_DAILY_TIME_ACTIVITY_CREATE_PENDING",
                        dailyGroupKey=self._group_key_text(group_key),
                        dailyUserAccountId=group_key[0],
                        dailyWorkDate=group_key[1].isoformat(),
                        dailyProjectId=group_key[2],
                        dailyTimezone=timezone_name,
                        dailyTimezoneSource=timezone_resolution.source,
                        dailyTimelogIds=[int(row.get("timelog_id") or 0) for row in eligible_rows],
                    )
                    self._save_pending_snapshot(
                        tenant_id=tenant_id,
                        eligible_rows=eligible_rows,
                        reason=pending_reason,
                    )
                    result = self.create_time_activity(
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=realm_id,
                        payload=payload,
                    )
                    action = "created"

                time_activity_id = self._clean(result.get(const.QBO_KEY_ID))
                sync_token = self._clean(result.get(const.QBO_KEY_SYNC_TOKEN)) or None
                if not time_activity_id:
                    raise ValueError("QuickBooks TimeActivity response did not include an Id.")

                refs = self._daily_worker_context(eligible_rows)
                reason = self._daily_reason(
                    f"QuickBooks daily TimeActivity {action}.",
                    dailyUserAccountId=group_key[0],
                    dailyWorkDate=group_key[1].isoformat(),
                    dailyProjectId=group_key[2],
                    dailyProjectName=self._clean(eligible_rows[0].get("project_name")),
                    dailyTimezone=timezone_name,
                    dailyTimezoneSource=timezone_resolution.source,
                    dailyTimezoneLatitude=timezone_resolution.latitude,
                    dailyTimezoneLongitude=timezone_resolution.longitude,
                    dailyTimelogIds=[int(row.get("timelog_id") or 0) for row in eligible_rows],
                    quickBooksTimeActivityId=time_activity_id,
                    workerType=("1099" if refs["worker_entity_type"] == const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR else "W2"),
                    qboEmployeeId=(refs["worker_id"] if refs["worker_entity_type"] == const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE else None),
                    qboVendorId=(refs["worker_id"] if refs["worker_entity_type"] == const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR else None),
                    payrollItemId=(payroll_compensation["id"] if payroll_compensation else None),
                    payrollItemName=(payroll_compensation["name"] if payroll_compensation else None),
                    qboServiceItemIds=refs["service_ids"],
                )
                clear_reason = self._daily_reason(
                    "Timelog excluded from the approved daily total.",
                    code="TIMELOG_NOT_READY",
                    dailyUserAccountId=group_key[0],
                    dailyWorkDate=group_key[1].isoformat(),
                    dailyProjectId=group_key[2],
                    dailyTimezone=timezone_name,
                    dailyTimezoneSource=timezone_resolution.source,
                )
                self._save_group_success(
                    tenant_id=tenant_id,
                    eligible_rows=eligible_rows,
                    clear_map_ids=clear_map_ids,
                    preserve_map_ids=preserve_map_ids,
                    time_activity_id=time_activity_id,
                    realm_id=realm_id,
                    sync_token=sync_token,
                    reason=reason,
                    clear_reason=clear_reason,
                )
                if action == "updated":
                    summary["updatedInQbo"] += 1
                    summary["updatedInAccounting"] += 1
                    summary["updatedTimeActivities"] += 1
                else:
                    summary["createdInQbo"] += 1
                    summary["createdInAccounting"] += 1
                    summary["createdTimeActivities"] += 1

                detail.update(
                    {
                        "status": "success",
                        "action": action,
                        "taskCount": len(eligible_rows),
                        "fieldcraftTimelogIds": [int(row.get("timelog_id") or 0) for row in eligible_rows],
                        "durationSeconds": total_seconds,
                        "quickBooksTimeActivityId": time_activity_id,
                        "externalEntityType": self.EXTERNAL_ENTITY_TYPE,
                    }
                )
                summary[const.KEY_RESULTS].append(detail)
                return True
        except Exception as exc:
            logger.exception(
                "[DailyTimelogExport] group failed | tenant_id=%s service_id=%s group=%s timezone=%s",
                tenant_id,
                service_id,
                self._group_key_text(group_key),
                timezone_resolution.timezone_name if timezone_resolution else "unresolved",
            )
            result_status = self._mark_trigger_failures(
                tenant_id=tenant_id,
                triggers=triggers,
                group_key=group_key,
                error=exc,
                timezone_resolution=timezone_resolution,
            )
            detail.update(
                {
                    "status": result_status,
                    "message": str(exc),
                    "error": str(exc),
                    "errorCode": self._clean(getattr(exc, "code", "")) or "QBO_DAILY_TIME_ACTIVITY_EXPORT_FAILED",
                }
            )
            if result_status == "not_ready":
                summary["skipped"] += max(1, len(trigger_map_ids))
            else:
                summary["syncFailed"] += max(1, len(trigger_map_ids))
                summary[const.KEY_FAILURES].append(detail)
            summary[const.KEY_RESULTS].append(detail)
            return False

    def _work_units(
        self,
        candidates: Sequence[Dict[str, Any]],
    ) -> Tuple[List[WorkUnit], List[Dict[str, Any]]]:
        previous_groups: Dict[GroupKey, List[Dict[str, Any]]] = defaultdict(list)
        current_groups: Dict[GroupKey, List[Dict[str, Any]]] = defaultdict(list)
        invalid: List[Dict[str, Any]] = []
        for candidate in candidates:
            try:
                current_key = self._group_key(candidate)
                previous_key = self._previous_group_key(candidate)
                if current_key is not None:
                    self._timezone_resolution(candidate)
            except (ProjectTimezoneResolutionError, DailyTimelogValidationError) as exc:
                candidate["_daily_group_error"] = exc
                invalid.append(candidate)
                continue
            if previous_key and previous_key != current_key:
                previous_groups[previous_key].append(candidate)
            if current_key:
                current_groups[current_key].append(candidate)
            elif not previous_key:
                invalid.append(candidate)
        units: List[WorkUnit] = []
        units.extend(
            (key, previous_groups[key], True)
            for key in sorted(previous_groups, key=lambda item: (item[1], item[0], item[2]))
        )
        units.extend(
            (key, current_groups[key], False)
            for key in sorted(current_groups, key=lambda item: (item[1], item[0], item[2]))
        )
        return units, invalid

    def _invalid_worker_days(
        self,
        *,
        candidates: Sequence[Dict[str, Any]],
        tenant_id: str,
        service_id: str,
        realm_id: str,
    ) -> Dict[DayKey, Exception]:
        day_keys: Set[DayKey] = set()
        for candidate in candidates:
            if candidate.get("_daily_group_error") is not None:
                continue
            for previous in (False, True):
                try:
                    day_key = self._day_key(candidate, previous=previous)
                except ProjectTimezoneResolutionError:
                    day_key = None
                if day_key:
                    day_keys.add(day_key)
        invalid: Dict[DayKey, Exception] = {}
        for user_account_id, work_date, timezone_name in sorted(
            day_keys, key=lambda item: (item[1], item[0], item[2])
        ):
            try:
                rows = self._load_worker_day_rows(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    user_account_id=user_account_id,
                    work_date=work_date,
                    realm_id=realm_id,
                    timezone_name=timezone_name,
                )
                eligible_rows: List[Dict[str, Any]] = []
                for row in rows:
                    if not self._source_is_eligible(row):
                        continue
                    try:
                        resolution = self._timezone_resolution(row)
                        row_work_date = self._work_date(row)
                    except ProjectTimezoneResolutionError:
                        continue
                    if resolution.timezone_name == timezone_name and row_work_date == work_date:
                        eligible_rows.append(row)
                if eligible_rows and self._daily_duration_seconds(eligible_rows) > 24 * 60 * 60:
                    raise ValueError("Combined project time for a worker-day cannot exceed 24 hours.")
            except Exception as exc:
                invalid[(user_account_id, work_date, timezone_name)] = exc
        return invalid

    def _candidate_matches_day_key(self, candidate: Dict[str, Any], day_key: DayKey) -> bool:
        for previous in (False, True):
            try:
                if self._day_key(candidate, previous=previous) == day_key:
                    return True
            except ProjectTimezoneResolutionError:
                continue
        return False

    def _process_candidates_as_daily_groups(
        self,
        *,
        candidates: Sequence[Dict[str, Any]],
        tenant_id: str,
        service_id: str,
        realm_id: str,
        base_url: str,
        access_token: str,
        dry_run: bool,
        summary: Dict[str, Any],
    ) -> None:
        units, invalid_candidates = self._work_units(candidates)
        summary["dailyGroups"] = len(units)
        blocked_map_ids: Set[int] = set()
        invalid_days = self._invalid_worker_days(
            candidates=candidates,
            tenant_id=tenant_id,
            service_id=service_id,
            realm_id=realm_id,
        )
        for day_key, error in invalid_days.items():
            day_triggers = [
                candidate
                for candidate in candidates
                if self._candidate_matches_day_key(candidate, day_key)
                and self._source_is_eligible(candidate)
            ]
            if not day_triggers:
                continue
            resolution = ProjectTimezoneResolution(
                timezone_name=day_key[2], source="WORKER_DAY_VALIDATION"
            )
            result_status = self._mark_trigger_failures(
                tenant_id=tenant_id,
                triggers=day_triggers,
                group_key=(day_key[0], day_key[1], 0),
                error=error,
                timezone_resolution=resolution,
            )
            day_map_ids = {
                int(row.get("map_id") or 0)
                for row in day_triggers
                if int(row.get("map_id") or 0)
            }
            blocked_map_ids.update(day_map_ids)
            detail = {
                "workerDay": f"{day_key[0]}:{day_key[1].isoformat()}:{day_key[2]}",
                "mapIds": sorted(day_map_ids),
                "status": result_status,
                "error": str(error),
            }
            if result_status == "not_ready":
                summary["skipped"] += max(1, len(day_map_ids))
                summary[const.KEY_RESULTS].append(detail)
            else:
                summary["syncFailed"] += max(1, len(day_map_ids))
                summary[const.KEY_FAILURES].append(detail)

        for candidate in invalid_candidates:
            map_id = int(candidate.get("map_id") or 0)
            error = candidate.get("_daily_group_error")
            if not isinstance(error, Exception):
                error = ValueError(
                    "Timelog does not have a worker, project, or start date and cannot be assigned to a daily entry."
                )
            user_account_id = int(candidate.get("user_account_id") or 0)
            project_id = int(candidate.get("project_id") or 0)
            result_status = self._mark_trigger_failures(
                tenant_id=tenant_id,
                triggers=[candidate],
                group_key=(user_account_id, date.min, project_id),
                error=error,
            )
            blocked_map_ids.add(map_id)
            detail = {"mapId": map_id, "status": result_status, "error": str(error)}
            if result_status == "not_ready":
                summary["skipped"] += 1
                summary[const.KEY_RESULTS].append(detail)
            else:
                summary["syncFailed"] += 1
                summary[const.KEY_FAILURES].append(detail)

        for group_key, triggers, is_previous in units:
            trigger_ids = {int(row.get("map_id") or 0) for row in triggers}
            if trigger_ids & blocked_map_ids:
                continue
            success = self._process_daily_group(
                group_key=group_key,
                triggers=triggers,
                is_previous_group=is_previous,
                tenant_id=tenant_id,
                service_id=service_id,
                realm_id=realm_id,
                base_url=base_url,
                access_token=access_token,
                dry_run=dry_run,
                summary=summary,
            )
            if not success:
                blocked_map_ids.update(trigger_ids)

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
        self._process_candidates_as_daily_groups(
            candidates=[candidate],
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
        summary["mode"] = "DAILY_TIME_ACTIVITY"
        safe_audit_log(
            tenant_id=tenant_id,
            action="daily_timelog_export_started",
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

            # Preferences are fetched at connection time and cached. Reading them
            # here makes the time-tracking prerequisite visible in every export
            # result without changing whether an existing export is allowed to run.
            try:
                capabilities = self.capability_service.get_capabilities(tenant_id, service_id)
                summary["quickBooksCapabilities"] = {
                    "timeTrackingEnabled": capabilities.get(const.CAPABILITY_TIME_TRACKING),
                    "projectsEnabled": capabilities.get(const.CAPABILITY_PROJECTS),
                }
                logger.info(
                    "[DailyTimelogExport] company time prerequisites | tenant_id=%s "
                    "time_tracking_enabled=%s projects_enabled=%s",
                    tenant_id,
                    capabilities.get(const.CAPABILITY_TIME_TRACKING),
                    capabilities.get(const.CAPABILITY_PROJECTS),
                )
            except Exception as exc:
                logger.warning(
                    "[DailyTimelogExport] unable to read company time prerequisites; continuing export | "
                    "tenant_id=%s error=%s",
                    tenant_id,
                    exc,
                    exc_info=True,
                )

            candidates = self._pending_trigger_candidates(
                tenant_id=tenant_id,
                service_id=service_id,
                limit=max(1, min(int(limit or 500), 500)),
                offset=max(0, int(offset or 0)),
                after_mapping_id=after_mapping_id,
                external_account_id=realm_id,
            )
            summary["queued"] = len(candidates)
            summary["requested"] = len(candidates)
            summary["lastMappingId"] = max(
                (int(row.get("map_id") or 0) for row in candidates), default=None
            )
            self._process_candidates_as_daily_groups(
                candidates=candidates,
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
                summary[const.KEY_MESSAGE] = "No PENDING_PUSH timelog mappings were found for daily export."
            elif failed_total and not successful_total:
                summary[const.KEY_MESSAGE] = "No QuickBooks daily TimeActivity records were exported."
            elif failed_total:
                summary[const.KEY_MESSAGE] = (
                    f"QuickBooks daily time activity export completed with {failed_total} failed mapping(s)."
                )
            else:
                summary[const.KEY_MESSAGE] = (
                    "All requested worker-day-project groups were exported to QuickBooks TimeActivity."
                )
            return summary
        except Exception as exc:
            logger.exception("[DailyTimelogExport] Accounting TimeActivity export failed")
            summary[const.KEY_OK] = False
            summary[const.KEY_MESSAGE] = "QuickBooks daily time activity export failed."
            summary[const.KEY_ERROR] = str(exc)
            return summary
        finally:
            safe_audit_log(
                tenant_id=tenant_id,
                action=(
                    "daily_timelog_export_completed"
                    if summary.get(const.KEY_OK)
                    else "daily_timelog_export_failed"
                ),
                user_id=object_id or None,
                email=email or None,
                user_role=user_role or None,
                status="success" if summary.get(const.KEY_OK) else "failed",
                resource_type="quickbooks_timelog",
                context=(
                    f"requested={summary.get('requested', 0)};"
                    f"groups={summary.get('dailyGroups', 0)};"
                    f"created={summary.get('createdInQbo', 0)};"
                    f"updated={summary.get('updatedInQbo', 0)};"
                    f"failed={summary.get('syncFailed', 0)}"
                ),
            )
