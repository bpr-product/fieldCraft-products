"""Post-export approval orchestration for QuickBooks Time timesheets.

QuickBooks Time approval is not a property of an individual timesheet. The API
approves a user's time through a date by updating ``users.approved_to``. This
service keeps that broad operation isolated from the normal timesheet create
and update flow, and only runs when explicitly enabled.
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional

from source.quickbooks.QuickBooksTimeApiService import QuickBooksTimeApiService

logger = logging.getLogger(__name__)


class QuickBooksTimeApprovalService:
    """Approve successfully exported QuickBooks Time entries by worker/date."""

    ENABLED_ENV = "QBO_TIME_AUTO_APPROVE_EXPORTED_TIME_ENABLED"
    TARGETS_KEY = "_quickBooksTimeApprovalTargets"
    BLOCKS_KEY = "_quickBooksTimeApprovalBlocks"

    @classmethod
    def enabled(cls) -> bool:
        value = str(os.getenv(cls.ENABLED_ENV) or "").strip().lower()
        return value in {"1", "true", "yes", "on"}

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _date(value: Any) -> Optional[date]:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None

    @classmethod
    def register_success(
        cls,
        summary: MutableMapping[str, Any],
        *,
        user_id: Any,
        work_date: Any,
        map_ids: Iterable[Any],
        employee_name: Optional[str] = None,
        fieldcraft_user_account_id: Optional[Any] = None,
        timezone_name: str = "UTC",
    ) -> None:
        resolved_date = cls._date(work_date)
        try:
            resolved_user_id = int(user_id or 0)
        except (TypeError, ValueError):
            resolved_user_id = 0
        if resolved_user_id <= 0 or resolved_date is None:
            return

        targets = summary.setdefault(cls.TARGETS_KEY, {})
        key = f"{resolved_user_id}:{resolved_date.isoformat()}"
        target = targets.setdefault(
            key,
            {
                "userId": resolved_user_id,
                "workDate": resolved_date.isoformat(),
                "mapIds": [],
                "employeeName": cls._clean(employee_name) or None,
                "fieldcraftUserAccountIds": [],
                "timezoneName": cls._clean(timezone_name) or "UTC",
                "timezoneNames": [],
            },
        )
        existing = set()
        for value in target.get("mapIds") or []:
            try:
                map_id = int(value or 0)
            except (TypeError, ValueError):
                continue
            if map_id > 0:
                existing.add(map_id)
        for value in map_ids:
            try:
                map_id = int(value or 0)
            except (TypeError, ValueError):
                continue
            if map_id > 0:
                existing.add(map_id)
        target["mapIds"] = sorted(existing)
        if not target.get("employeeName") and cls._clean(employee_name):
            target["employeeName"] = cls._clean(employee_name)
        try:
            resolved_fieldcraft_user_id = int(fieldcraft_user_account_id or 0)
        except (TypeError, ValueError):
            resolved_fieldcraft_user_id = 0
        fieldcraft_user_ids = {
            int(value)
            for value in (target.get("fieldcraftUserAccountIds") or [])
            if str(value or "").strip().isdigit() and int(value) > 0
        }
        if resolved_fieldcraft_user_id > 0:
            fieldcraft_user_ids.add(resolved_fieldcraft_user_id)
        target["fieldcraftUserAccountIds"] = sorted(fieldcraft_user_ids)
        timezone_names = {
            cls._clean(value)
            for value in (target.get("timezoneNames") or [])
            if cls._clean(value)
        }
        existing_timezone = cls._clean(target.get("timezoneName"))
        if existing_timezone:
            timezone_names.add(existing_timezone)
        resolved_timezone = cls._clean(timezone_name)
        if resolved_timezone:
            timezone_names.add(resolved_timezone)
        target["timezoneNames"] = sorted(timezone_names)
        if target["timezoneNames"]:
            # Compatibility-only metadata for legacy persisted readers. For a
            # multi-timezone target this sorted first value is not authoritative;
            # approval decisions must always use the complete timezoneNames list.
            target["timezoneName"] = target["timezoneNames"][0]

    @classmethod
    def register_block(
        cls,
        summary: MutableMapping[str, Any],
        *,
        user_id: Any,
        work_date: Any,
        reason: Any,
    ) -> None:
        resolved_date = cls._date(work_date)
        try:
            resolved_user_id = int(user_id or 0)
        except (TypeError, ValueError):
            resolved_user_id = 0
        if resolved_user_id <= 0 or resolved_date is None:
            return
        blocks = summary.setdefault(cls.BLOCKS_KEY, {})
        key = f"{resolved_user_id}:{resolved_date.isoformat()}"
        blocks[key] = {
            "userId": resolved_user_id,
            "workDate": resolved_date.isoformat(),
            "reason": cls._clean(reason) or "One or more entries for this worker-day failed to export.",
        }

    @staticmethod
    def _verified(user: Mapping[str, Any], target_date: date) -> bool:
        raw = str(user.get("approved_to") or "").strip()
        if not raw:
            return False
        try:
            return date.fromisoformat(raw[:10]) >= target_date
        except ValueError:
            return False

    @staticmethod
    def _map_ids(targets: Iterable[Mapping[str, Any]]) -> List[int]:
        resolved = set()
        for target in targets:
            for raw_map_id in target.get("mapIds") or []:
                try:
                    map_id = int(raw_map_id or 0)
                except (TypeError, ValueError):
                    continue
                if map_id > 0:
                    resolved.add(map_id)
        return sorted(resolved)

    @classmethod
    def _public_target(cls, target: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "quickBooksTimeUserId": int(target.get("userId") or 0),
            "workDate": cls._clean(target.get("workDate")),
            "employeeName": target.get("employeeName"),
            "mapIds": list(target.get("mapIds") or []),
        }

    def finalize(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        time_api: QuickBooksTimeApiService,
        timelog_repo: Any,
        summary: MutableMapping[str, Any],
        dry_run: bool,
    ) -> None:
        """Approve collected worker dates and update mapping audit metadata.

        Targets are combined by QuickBooks Time user. Because ``approved_to`` is
        cumulative, one API request is made for the latest safe date for each
        user. If a date failed to export, that date and every later date for the
        same user are not approved in this run.
        """
        raw_targets = summary.pop(self.TARGETS_KEY, {}) or {}
        raw_blocks = summary.pop(self.BLOCKS_KEY, {}) or {}
        blocks = [dict(value) for value in raw_blocks.values() if isinstance(value, Mapping)]

        summary["autoApprovalEnabled"] = self.enabled()
        summary["approvalRequested"] = 0
        summary["approvalSucceeded"] = 0
        summary["approvalFailed"] = 0
        summary["approvalBlocked"] = 0
        summary["approvalSkipped"] = 0
        summary["approvalResults"] = []
        summary["approvalFailures"] = []

        current_targets = [
            dict(value) for value in raw_targets.values() if isinstance(value, Mapping)
        ]
        if not self.enabled():
            if not current_targets:
                return
            summary["approvalSkipped"] = len(current_targets)
            summary["approvalResults"] = [
                {
                    **self._public_target(target),
                    "status": "disabled",
                    "message": f"Set {self.ENABLED_ENV}=true to approve exported time.",
                }
                for target in current_targets
            ]
            return

        if not dry_run:
            target_buffer: MutableMapping[str, Any] = {self.TARGETS_KEY: raw_targets}
            touched_user_ids = {
                int(target.get("userId") or 0)
                for target in current_targets
                if int(target.get("userId") or 0) > 0
            }
            touched_user_ids.update(
                int(block.get("userId") or 0)
                for block in blocks
                if int(block.get("userId") or 0) > 0
            )
            for user_id in sorted(touched_user_ids):
                persisted_targets = (
                    timelog_repo.list_pending_quickbooks_time_approval_targets(
                        tenant_id=tenant_id,
                        service_id=service_id,
                        quickbooks_time_user_id=user_id,
                    )
                    or []
                )
                for persisted in persisted_targets:
                    if not isinstance(persisted, Mapping):
                        continue
                    self.register_success(
                        target_buffer,
                        user_id=user_id,
                        work_date=persisted.get("work_date"),
                        map_ids=[persisted.get("map_id")],
                        fieldcraft_user_account_id=persisted.get("fieldcraft_user_account_id"),
                        timezone_name=self._clean(persisted.get("timezone_name")) or "UTC",
                    )
            raw_targets = target_buffer.get(self.TARGETS_KEY, {}) or {}

        targets = [dict(value) for value in raw_targets.values() if isinstance(value, Mapping)]
        if not targets:
            return

        grouped: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for target in targets:
            grouped[int(target["userId"])].append(target)
        blocked_dates: Dict[int, List[date]] = defaultdict(list)
        for block in blocks:
            parsed = self._date(block.get("workDate"))
            if parsed:
                blocked_dates[int(block["userId"])].append(parsed)

        for user_id in sorted(grouped):
            user_targets = sorted(grouped[user_id], key=lambda item: str(item.get("workDate") or ""))
            earliest_block = min(blocked_dates.get(user_id) or [date.max])
            safe_targets = [
                item
                for item in user_targets
                if (self._date(item.get("workDate")) or date.max) < earliest_block
            ]
            blocked_targets = [item for item in user_targets if item not in safe_targets]

            for target in blocked_targets:
                blocked_message = (
                    "Approval was skipped because another entry for this user on or before "
                    "the target date failed to export."
                )
                blocked_map_ids = self._map_ids([target])
                if not dry_run and blocked_map_ids:
                    timelog_repo.update_quickbooks_time_approval_metadata(
                        tenant_id=tenant_id,
                        map_ids=blocked_map_ids,
                        approval_status="BLOCKED",
                        approved_through=self._clean(target.get("workDate")),
                        error=blocked_message,
                    )
                summary["approvalSkipped"] += 1
                summary["approvalResults"].append(
                    {
                        **self._public_target(target),
                        "status": "blocked",
                        "message": blocked_message,
                    }
                )

            if not safe_targets:
                continue

            target_dates = [
                parsed
                for item in safe_targets
                for parsed in [self._date(item.get("workDate"))]
                if parsed is not None
            ]
            if not target_dates:
                continue
            target_date = max(target_dates)
            approved_map_ids = self._map_ids(safe_targets)
            summary["approvalRequested"] += 1

            if dry_run:
                summary["approvalSkipped"] += 1
                summary["approvalResults"].append(
                    {
                        "quickBooksTimeUserId": user_id,
                        "approvedThrough": target_date.isoformat(),
                        "mapIds": approved_map_ids,
                        "status": "dry_run",
                    }
                )
                continue

            unresolved_rows: List[Dict[str, Any]] = []
            readiness_checks = set()
            for item in safe_targets:
                timezone_names = {
                    self._clean(value)
                    for value in (item.get("timezoneNames") or [])
                    if self._clean(value)
                }
                # Older persisted targets may only contain timezoneName. New
                # approval decisions use every value in timezoneNames.
                legacy_timezone = self._clean(item.get("timezoneName"))
                if legacy_timezone:
                    timezone_names.add(legacy_timezone)
                if not timezone_names:
                    timezone_names.add("UTC")
                for raw_fieldcraft_user_id in item.get("fieldcraftUserAccountIds") or []:
                    try:
                        fieldcraft_user_id = int(raw_fieldcraft_user_id or 0)
                    except (TypeError, ValueError):
                        continue
                    if fieldcraft_user_id > 0:
                        readiness_checks.update(
                            (fieldcraft_user_id, timezone_name)
                            for timezone_name in timezone_names
                        )
            for fieldcraft_user_id, timezone_name in sorted(readiness_checks):
                unresolved_rows.extend(
                    timelog_repo.list_unresolved_approved_timelogs_through_date(
                        tenant_id=tenant_id,
                        service_id=service_id,
                        user_account_id=fieldcraft_user_id,
                        approved_through=target_date,
                        timezone_name=timezone_name,
                    )
                    or []
                )
            if unresolved_rows:
                unresolved_ids = sorted(
                    {
                        int(row.get("timelog_id") or 0)
                        for row in unresolved_rows
                        if int(row.get("timelog_id") or 0) > 0
                    }
                )
                blocked_message = (
                    "Approval was skipped because older or same-day queued FIELDCRAFT timelogs "
                    "are not fully synced to QuickBooks Time."
                )
                timelog_repo.update_quickbooks_time_approval_metadata(
                    tenant_id=tenant_id,
                    map_ids=approved_map_ids,
                    approval_status="BLOCKED",
                    approved_through=target_date.isoformat(),
                    error=blocked_message,
                )
                summary["approvalSkipped"] += 1
                summary["approvalBlocked"] += 1
                summary["approvalResults"].append(
                    {
                        "quickBooksTimeUserId": user_id,
                        "approvedThrough": target_date.isoformat(),
                        "mapIds": approved_map_ids,
                        "unresolvedTimelogIds": unresolved_ids,
                        "status": "blocked_unresolved_timelogs",
                        "message": blocked_message,
                    }
                )
                continue

            try:
                response = time_api.approve_user_through_date(
                    user_id=user_id,
                    approved_to=target_date,
                    confirmation=QuickBooksTimeApiService.APPROVAL_CONFIRMATION,
                )
                user = response.get("user") if isinstance(response, Mapping) else None
                if not isinstance(user, Mapping) or not self._verified(user, target_date):
                    user = time_api.get_user(user_id)
                if not isinstance(user, Mapping) or not self._verified(user, target_date):
                    raise ValueError(
                        f"QuickBooks Time did not verify approval through {target_date.isoformat()}."
                    )

                timelog_repo.update_quickbooks_time_approval_metadata(
                    tenant_id=tenant_id,
                    map_ids=approved_map_ids,
                    approval_status="APPROVED",
                    approved_through=target_date.isoformat(),
                    error=None,
                )
                summary["approvalSucceeded"] += 1
                summary["approvalResults"].append(
                    {
                        "quickBooksTimeUserId": user_id,
                        "approvedThrough": target_date.isoformat(),
                        "mapIds": approved_map_ids,
                        "status": "approved",
                    }
                )
            except Exception as exc:
                logger.exception(
                    "[QuickBooksTimeApproval] approval failed | tenant_id=%s user_id=%s approved_to=%s",
                    tenant_id,
                    user_id,
                    target_date.isoformat(),
                )
                # A timeout can be ambiguous. Verify the user once more before
                # reporting a failure, because the approval may have succeeded.
                try:
                    user = time_api.get_user(user_id)
                except Exception:
                    user = None
                if isinstance(user, Mapping) and self._verified(user, target_date):
                    timelog_repo.update_quickbooks_time_approval_metadata(
                        tenant_id=tenant_id,
                        map_ids=approved_map_ids,
                        approval_status="APPROVED",
                        approved_through=target_date.isoformat(),
                        error=None,
                    )
                    summary["approvalSucceeded"] += 1
                    summary["approvalResults"].append(
                        {
                            "quickBooksTimeUserId": user_id,
                            "approvedThrough": target_date.isoformat(),
                            "mapIds": approved_map_ids,
                            "status": "approved_after_verification",
                        }
                    )
                    continue

                error_text = self._clean(exc) or "QuickBooks Time approval failed."
                timelog_repo.update_quickbooks_time_approval_metadata(
                    tenant_id=tenant_id,
                    map_ids=approved_map_ids,
                    approval_status="FAILED",
                    approved_through=target_date.isoformat(),
                    error=error_text,
                )
                summary["approvalFailed"] += 1
                summary["approvalFailures"].append(
                    {
                        "quickBooksTimeUserId": user_id,
                        "approvedThrough": target_date.isoformat(),
                        "mapIds": approved_map_ids,
                        "status": "failed",
                        "error": error_text,
                    }
                )
