from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import String, Text, and_, case, cast, func, literal, or_, select, true

from source.Utils import constants as const
from source.Utils.ProjectTimezoneResolver import (
    ProjectTimezoneResolutionError,
    ProjectTimezoneResolver,
)
from source.dal.CustomerRepository import CustomerRepository
from source.dal.db_session import tenant_session_scope
from source.dal.employee_models import UserAccountExternalMapModel, UserAccountModel
from source.dal.project_models import ProjectExternalMapModel, ProjectModel
from source.dal.service_models import RoleExternalMapModel, RoleModel
from source.dal.timelog_models import (
    AttributeModel,
    ResourceModel,
    ResourceScheduleModel,
    TaskAdditionalInfoModel,
    TaskAttributeModel,
    TaskModel,
    TimelogExternalMapModel,
    TimelogModel,
)

logger = logging.getLogger(__name__)


class TimelogRepository:
    """Repository for exporting completed FIELDCRAFT timelogs to QuickBooks Time timesheets."""

    STATUS_PENDING_PUSH = "PENDING_PUSH"
    STATUS_SYNCED = "SYNCED"
    STATUS_FAILED = "FAILED"
    STATUS_SYNC_FAILED = "SYNC_FAILED"
    STATUS_INVALID = "INVALID"
    APPROVAL_STATUS_APPROVED = "APPROVED"

    TASK_NAME_ATTRIBUTE_KEYS = (
        "task_name",
        "task.name",
        "name",
    )
    TASK_DESCRIPTION_ATTRIBUTE_KEYS = (
        "task_description",
        "task.description",
        "description",
    )
    TASK_ASSIGNEE_ATTRIBUTE_KEYS = (
        "assignees",
        "task.assignees",
        "task_assignees",
    )
    TASK_ROLE_ATTRIBUTE_KEYS = (
        "service_role",
        "service_role_name",
        "task.service_role",
        "task_role",
        "role_name",
        "role",
    )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _daily_schema_enabled() -> bool:
        # Timelogs are always exported as one daily TimeActivity per worker/project.
        return True

    @staticmethod
    def _service_id_int(service_id: Any) -> int:
        try:
            return int(service_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid service_id value: {service_id!r}") from exc

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def _reason_payload(cls, value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        raw = cls._clean(value)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @classmethod
    def mapping_external_entity_type(cls, mapping: Dict[str, Any] | None) -> Optional[str]:
        mapping = mapping or {}
        reason = cls._reason_payload(mapping.get("reason"))
        explicit_values = [mapping.get("mapping_external_entity_type")]
        # Candidate rows also contain the employee mapping's entity type under
        # external_entity_type (EMPLOYEE/VENDOR). Only treat that key as the
        # timelog mapping type when no dedicated candidate alias is present.
        if "mapping_external_entity_type" not in mapping:
            explicit_values.append(mapping.get("external_entity_type"))
        explicit_values.extend(
            [
                reason.get("externalEntityType"),
                reason.get("external_entity_type"),
                reason.get("target"),
            ]
        )
        for value in explicit_values:
            explicit = cls._clean(value).upper()
            if explicit in {"TIMESHEET", "QUICKBOOKS_TIME_TIMESHEET"}:
                return "TIMESHEET"
            if explicit in {"TIME_ACTIVITY", "QBO_TIME_ACTIVITY"}:
                return "TIME_ACTIVITY"
        if cls._clean(mapping.get("external_time_timesheet_id")):
            return "TIMESHEET"
        if cls._clean(mapping.get("external_time_activity_id")):
            return "TIME_ACTIVITY"
        return None

    @classmethod
    def mapping_external_id(cls, mapping: Dict[str, Any] | None) -> Optional[str]:
        """Return the external id from the column matching its entity type.

        Older rows stored QuickBooks Time timesheet ids in
        ``external_time_activity_id``. Keep that read fallback while all new
        writes use the dedicated ``external_time_timesheet_id`` column.
        """
        mapping = mapping or {}
        entity_type = cls.mapping_external_entity_type(mapping)
        if entity_type == "TIMESHEET":
            return (
                cls._clean(mapping.get("external_time_timesheet_id"))
                or cls._clean(mapping.get("external_time_activity_id"))
                or None
            )
        if entity_type == "TIME_ACTIVITY":
            return cls._clean(mapping.get("external_time_activity_id")) or None
        return (
            cls._clean(mapping.get("external_time_timesheet_id"))
            or cls._clean(mapping.get("external_time_activity_id"))
            or None
        )

    @staticmethod
    def _normalize_resource_type(value: Any) -> str:
        return " ".join(str(value or "").strip().split()).lower()

    def _worker_entity_type_for_resource_type(self, resource_type: Any) -> str:
        normalized = self._normalize_resource_type(resource_type)
        contractor_types = {
            self._normalize_resource_type(const.EMPLOYEE_TYPE_1099),
            self._normalize_resource_type(getattr(const, "EMPLOYEE_TYPE_SUBCONTRACTOR", "Subcontractor")),
            self._normalize_resource_type(getattr(const, "EMPLOYEE_TYPE_CONTRACTOR", "Contractor")),
        }
        if normalized in contractor_types:
            return const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR
        return const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE

    def _mapping_worker_entity_type(self, candidate: Dict[str, Any]) -> str:
        explicit_type = self._clean(candidate.get("external_entity_type")).upper()
        if explicit_type in const.WORKER_EXTERNAL_ENTITY_TYPES_ALLOWED:
            return explicit_type
        if self._clean(candidate.get("qbo_vendor_id")):
            return const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR
        return self._worker_entity_type_for_resource_type(candidate.get("resource_type"))

    def _is_current_mapping_clause(self):
        return func.upper(func.coalesce(TimelogExternalMapModel.sync_status, "")) != self.STATUS_INVALID

    @staticmethod
    def _timelog_export_ready_clause(*, require_active_schedule: bool = True):
        """Return the source conditions required for a QuickBooks TimeActivity export."""
        clauses = [
            func.upper(func.trim(func.coalesce(TimelogModel.approval_status, "")))
            == TimelogRepository.APPROVAL_STATUS_APPROVED,
            func.lower(func.trim(func.coalesce(TimelogModel.status, ""))) == "completed",
            TimelogModel.start_time.is_not(None),
            TimelogModel.end_time.is_not(None),
            TimelogModel.end_time > TimelogModel.start_time,
            func.coalesce(TimelogModel.invalid, False).is_(False),
        ]
        if require_active_schedule:
            clauses.append(func.coalesce(ResourceScheduleModel.invalid, False).is_(False))
        return and_(*clauses)

    def _mapping_to_dict(self, row: TimelogExternalMapModel | None) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        return {
            "id": int(row.id),
            "timelog_id": int(row.timelog_id) if row.timelog_id is not None else None,
            "tenant_id": row.tenant_id,
            "service_id": int(row.service_id) if row.service_id is not None else None,
            "external_time_activity_id": row.external_time_activity_id,
            "external_entity_type": row.external_entity_type,
            "external_time_timesheet_id": row.external_time_timesheet_id,
            "external_account_id": row.external_account_id,
            "external_display_name": row.external_display_name,
            "sync_status": row.sync_status,
            "version": int(row.version or 1),
            "version_effective_date": row.version_effective_date,
            "version_expiry_date": row.version_expiry_date,
            "endorsement_effective_date": row.endorsement_effective_date,
            "endorsement_expiry_date": row.endorsement_expiry_date,
            "updated_at": row.updated_at,
            "sync_token": row.sync_token,
            "reason": row.reason,
        }

    def _mapping_row_to_dict(self, row: Any) -> Optional[Dict[str, Any]]:
        if not row:
            return None
        if isinstance(row, TimelogExternalMapModel):
            return self._mapping_to_dict(row)
        return dict(row._mapping if hasattr(row, "_mapping") else row)

    def get_current_mapping(self, *, tenant_id: str, service_id: Any, timelog_id: Any) -> Optional[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(TimelogExternalMapModel)
                .where(
                    TimelogExternalMapModel.tenant_id == tenant_id,
                    TimelogExternalMapModel.service_id == self._service_id_int(service_id),
                    TimelogExternalMapModel.timelog_id == int(timelog_id),
                    self._is_current_mapping_clause(),
                    (
                        (TimelogExternalMapModel.endorsement_expiry_date.is_(None))
                        | (TimelogExternalMapModel.endorsement_expiry_date > func.now())
                    ),
                )
                .order_by(TimelogExternalMapModel.id.desc())
                .limit(1)
            )
            row = session.execute(stmt).scalars().first()
            return self._mapping_to_dict(row)

    def get_mapping_by_id(self, *, tenant_id: str, service_id: Any, mapping_id: Any) -> Optional[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(TimelogExternalMapModel)
                .where(
                    TimelogExternalMapModel.id == int(mapping_id),
                    TimelogExternalMapModel.tenant_id == tenant_id,
                    TimelogExternalMapModel.service_id == self._service_id_int(service_id),
                    self._is_current_mapping_clause(),
                    (
                        (TimelogExternalMapModel.endorsement_expiry_date.is_(None))
                        | (TimelogExternalMapModel.endorsement_expiry_date > func.now())
                    ),
                )
                .limit(1)
            )
            row = session.execute(stmt).scalars().first()
            return self._mapping_to_dict(row)

    def get_current_mapping_by_external_id(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        external_time_activity_id: str,
        external_account_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        external_id = self._clean(external_time_activity_id)
        if not external_id:
            return None
        with tenant_session_scope(tenant_id) as session:
            stmt = select(TimelogExternalMapModel).where(
                TimelogExternalMapModel.tenant_id == tenant_id,
                TimelogExternalMapModel.service_id == self._service_id_int(service_id),
                or_(
                    TimelogExternalMapModel.external_time_timesheet_id == external_id,
                    TimelogExternalMapModel.external_time_activity_id == external_id,
                ),
                self._is_current_mapping_clause(),
                (
                    (TimelogExternalMapModel.endorsement_expiry_date.is_(None))
                    | (TimelogExternalMapModel.endorsement_expiry_date > func.now())
                ),
            )
            clean_account_id = self._clean(external_account_id)
            if clean_account_id:
                stored_account_id = func.trim(
                    func.coalesce(TimelogExternalMapModel.external_account_id, "")
                )
                stmt = stmt.where(
                    or_(stored_account_id == clean_account_id, stored_account_id == "")
                ).order_by(
                    (stored_account_id == clean_account_id).desc(),
                    TimelogExternalMapModel.id.desc(),
                )
            else:
                stmt = stmt.order_by(TimelogExternalMapModel.id.desc())
            row = session.execute(stmt.limit(1)).scalars().first()
            return self._mapping_to_dict(row)

    def _latest_employee_map_lateral(
        self,
        *,
        tenant_id: str,
        service_id_int: int,
        external_account_id: Optional[str] = None,
    ):
        stmt = select(
            UserAccountExternalMapModel.id.label("employee_mapping_id"),
            UserAccountExternalMapModel.external_employee_id.label("qbo_employee_id"),
            UserAccountExternalMapModel.external_vendor_id.label("qbo_vendor_id"),
            UserAccountExternalMapModel.external_entity_type.label("external_entity_type"),
            UserAccountExternalMapModel.external_display_name.label("employee_external_display_name"),
            UserAccountExternalMapModel.external_time_user_id.label("external_time_user_id"),
            UserAccountExternalMapModel.sync_status.label("employee_sync_status"),
        ).where(
            UserAccountExternalMapModel.user_account_id == UserAccountModel.id,
            UserAccountExternalMapModel.tenant_id == tenant_id,
            UserAccountExternalMapModel.service_id == service_id_int,
            (
                (UserAccountExternalMapModel.endorsement_expiry_date.is_(None))
                | (UserAccountExternalMapModel.endorsement_expiry_date > func.now())
            ),
            func.coalesce(UserAccountExternalMapModel.sync_status, "") != self.STATUS_INVALID,
        )
        clean_account_id = self._clean(external_account_id)
        if clean_account_id:
            stored_account_id = func.trim(
                func.coalesce(UserAccountExternalMapModel.external_account_id, "")
            )
            stmt = stmt.where(
                or_(stored_account_id == clean_account_id, stored_account_id == "")
            ).order_by(
                (stored_account_id == clean_account_id).desc(),
                UserAccountExternalMapModel.id.desc(),
            )
        else:
            stmt = stmt.order_by(UserAccountExternalMapModel.id.desc())
        return stmt.limit(1).lateral("uem")

    def _latest_project_map_lateral(
        self,
        *,
        tenant_id: str,
        service_id_int: int,
        external_account_id: Optional[str] = None,
    ):
        stmt = select(
            ProjectExternalMapModel.id.label("project_mapping_id"),
            ProjectExternalMapModel.external_project_id.label("qbo_project_id"),
            ProjectExternalMapModel.additional_info.label("project_additional_info"),
            ProjectExternalMapModel.sync_status.label("project_sync_status"),
            ProjectExternalMapModel.reason.label("project_mapping_reason"),
        ).where(
            ProjectExternalMapModel.project_id == ResourceScheduleModel.project_id,
            ProjectExternalMapModel.tenant_id == tenant_id,
            ProjectExternalMapModel.service_id == service_id_int,
            (
                (ProjectExternalMapModel.endorsement_expiry_date.is_(None))
                | (ProjectExternalMapModel.endorsement_expiry_date > func.now())
            ),
            func.coalesce(ProjectExternalMapModel.sync_status, "") != self.STATUS_INVALID,
        )
        clean_account_id = self._clean(external_account_id)
        if clean_account_id:
            stored_account_id = func.trim(
                func.coalesce(ProjectExternalMapModel.external_account_id, "")
            )
            stmt = stmt.where(
                or_(stored_account_id == clean_account_id, stored_account_id == "")
            ).order_by(
                (stored_account_id == clean_account_id).desc(),
                ProjectExternalMapModel.id.desc(),
            )
        else:
            stmt = stmt.order_by(ProjectExternalMapModel.id.desc())
        return stmt.limit(1).lateral("pem")

    def _resource_lateral(self):
        """Resolve the scheduled FIELDCRAFT resource for a timelog.

        resource_schedule.resource_id points to resource.id. The worker/user
        account is resolved from resource.user_id. This is required for both
        QuickBooks worker mappings and resource cost/rate lookup.
        """
        return (
            select(
                ResourceModel.id.label("fieldcraft_resource_id"),
                ResourceModel.user_id.label("resource_user_account_id"),
                ResourceModel.cost.label("resource_cost"),
                ResourceModel.unit.label("resource_cost_unit"),
                ResourceModel.type.label("resource_type"),
            )
            .where(
                ResourceModel.id == ResourceScheduleModel.resource_id,
                func.coalesce(ResourceModel.invalid, False) == False,
            )
            .limit(1)
            .lateral("res")
        )

    @staticmethod
    def _env_attribute_keys(name: str, defaults: tuple[str, ...]) -> tuple[str, ...]:
        configured = [
            item.strip().lower()
            for item in str(os.getenv(name) or "").split(",")
            if item.strip()
        ]
        values = configured or list(defaults)
        # Preserve priority and remove duplicates.
        return tuple(dict.fromkeys(values))

    @staticmethod
    def _active_attribute_clause(model: Any):
        now = func.now()
        return and_(
            func.coalesce(model.is_valid, True) == True,
            or_(model.version_effective_date.is_(None), model.version_effective_date <= now),
            or_(model.version_expiry_date.is_(None), model.version_expiry_date > now),
            or_(model.endorsement_effective_date.is_(None), model.endorsement_effective_date <= now),
            or_(model.endorsement_expiry_date.is_(None), model.endorsement_expiry_date > now),
        )

    def _task_attribute_lateral(
        self,
        *,
        alias: str,
        attribute_keys: tuple[str, ...],
    ):
        """Resolve the latest current value for one logical task attribute.

        Attribute IDs are tenant-specific, so the integration always resolves
        by attributes.attribute_key and then joins task_attribute by the
        resulting attribute id.
        """
        normalized_key = func.lower(func.trim(AttributeModel.attribute_key))
        priority = case(
            *[(normalized_key == key, index) for index, key in enumerate(attribute_keys)],
            else_=len(attribute_keys),
        )
        return (
            select(
                normalized_key.label("attribute_key"),
                TaskAttributeModel.attr_value_text.label("value_text"),
                TaskAttributeModel.attr_value_jsonb.label("value_jsonb"),
                TaskAttributeModel.attr_value_integer.label("value_integer"),
                TaskAttributeModel.attr_value_float.label("value_float"),
                TaskAttributeModel.attr_value_boolean.label("value_boolean"),
                TaskAttributeModel.attr_value_datetime.label("value_datetime"),
                cast(TaskAttributeModel.attr_value_uuid, String).label("value_uuid"),
            )
            .select_from(TaskAttributeModel)
            .join(AttributeModel, AttributeModel.id == TaskAttributeModel.attribute_id)
            .where(
                TaskAttributeModel.task_id == ResourceScheduleModel.task_id,
                normalized_key.in_(attribute_keys),
                self._active_attribute_clause(TaskAttributeModel),
                self._active_attribute_clause(AttributeModel),
            )
            .order_by(
                priority,
                TaskAttributeModel.version.desc().nullslast(),
                TaskAttributeModel.endorsement_effective_date.desc().nullslast(),
                TaskAttributeModel.id.desc(),
            )
            .limit(1)
            .lateral(alias)
        )

    @staticmethod
    def _task_additional_info_lateral():
        return (
            select(
                TaskAdditionalInfoModel.assignees.label("legacy_assignees"),
            )
            .where(TaskAdditionalInfoModel.task_id == ResourceScheduleModel.task_id)
            .order_by(
                TaskAdditionalInfoModel.updated_at.desc().nullslast(),
                TaskAdditionalInfoModel.id.desc(),
            )
            .limit(1)
            .lateral("task_info")
        )

    def _task_metadata_laterals(self) -> Dict[str, Any]:
        return {
            "name": self._task_attribute_lateral(
                alias="task_name_attr",
                attribute_keys=self._env_attribute_keys(
                    "QBO_TIME_TASK_NAME_ATTRIBUTE_KEYS",
                    self.TASK_NAME_ATTRIBUTE_KEYS,
                ),
            ),
            "description": self._task_attribute_lateral(
                alias="task_description_attr",
                attribute_keys=self._env_attribute_keys(
                    "QBO_TIME_TASK_DESCRIPTION_ATTRIBUTE_KEYS",
                    self.TASK_DESCRIPTION_ATTRIBUTE_KEYS,
                ),
            ),
            "assignees": self._task_attribute_lateral(
                alias="task_assignees_attr",
                attribute_keys=self._env_attribute_keys(
                    "QBO_TIME_TASK_ASSIGNEE_ATTRIBUTE_KEYS",
                    self.TASK_ASSIGNEE_ATTRIBUTE_KEYS,
                ),
            ),
            "role": self._task_attribute_lateral(
                alias="task_role_attr",
                attribute_keys=self._env_attribute_keys(
                    "QBO_TIME_TASK_ROLE_ATTRIBUTE_KEYS",
                    self.TASK_ROLE_ATTRIBUTE_KEYS,
                ),
            ),
            "legacy": self._task_additional_info_lateral(),
        }

    def _latest_project_map_lateral_schema_v3(
        self,
        *,
        tenant_id: str,
        service_id_int: int,
        external_account_id: Optional[str] = None,
    ):
        """Project mapping lookup that uses only columns present in schema v3."""
        stmt = select(
            ProjectExternalMapModel.id.label("project_mapping_id"),
            ProjectExternalMapModel.external_project_id.label("qbo_project_id"),
            ProjectExternalMapModel.sync_status.label("project_sync_status"),
            ProjectExternalMapModel.reason.label("project_mapping_reason"),
        ).where(
            ProjectExternalMapModel.project_id == ResourceScheduleModel.project_id,
            ProjectExternalMapModel.tenant_id == tenant_id,
            ProjectExternalMapModel.service_id == service_id_int,
            (
                (ProjectExternalMapModel.endorsement_expiry_date.is_(None))
                | (ProjectExternalMapModel.endorsement_expiry_date > func.now())
            ),
            func.coalesce(ProjectExternalMapModel.sync_status, "") != self.STATUS_INVALID,
        )
        clean_account_id = self._clean(external_account_id)
        if clean_account_id:
            stored_account_id = func.trim(
                func.coalesce(ProjectExternalMapModel.external_account_id, "")
            )
            stmt = stmt.where(
                or_(stored_account_id == clean_account_id, stored_account_id == "")
            ).order_by(
                (stored_account_id == clean_account_id).desc(),
                ProjectExternalMapModel.id.desc(),
            )
        else:
            stmt = stmt.order_by(ProjectExternalMapModel.id.desc())
        return stmt.limit(1).lateral("pem_v3")

    @staticmethod
    def _task_attribute_scalar(columns: Any):
        return func.coalesce(
            columns.value_text,
            cast(columns.value_integer, String),
            cast(columns.value_float, String),
            cast(columns.value_boolean, String),
            cast(columns.value_datetime, String),
            columns.value_uuid,
        )

    @classmethod
    def _decode_jsonish(cls, value: Any) -> Any:
        if value in (None, "", "NULL"):
            return None
        if isinstance(value, (dict, list)):
            return value
        raw = cls._clean(value)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return value

    def _normalize_task_metadata(self, candidates: List[Dict[str, Any]]) -> None:
        for candidate in candidates:
            task_name = self._clean(
                candidate.pop("task_name_value", None) or candidate.get("task_name")
            )
            if not task_name:
                task_name = self._clean(candidate.pop("task_description_value", None))
            else:
                candidate.pop("task_description_value", None)
            candidate["task_name"] = task_name or None

            role_value = candidate.pop("task_role_value", None)
            if role_value in (None, ""):
                role_value = candidate.pop("task_role_json", None)
            else:
                candidate.pop("task_role_json", None)
            decoded_role = self._decode_jsonish(role_value)
            if isinstance(decoded_role, dict):
                role_value = (
                    decoded_role.get("role")
                    or decoded_role.get("roleName")
                    or decoded_role.get("serviceRole")
                    or decoded_role.get("name")
                )
            candidate["task_role_name"] = self._clean(role_value) or None

            assignees = candidate.pop("task_assignees_json", None)
            if assignees in (None, "", []):
                assignees = candidate.pop("task_assignees_text", None)
            else:
                candidate.pop("task_assignees_text", None)
            if assignees in (None, "", []):
                assignees = candidate.pop("legacy_task_assignees", None)
            else:
                candidate.pop("legacy_task_assignees", None)
            candidate["assignees"] = self._decode_jsonish(assignees)

            # Schema-v3 project and service maps do not have the old dedicated
            # Time metadata columns. Resolve exact Time references at runtime and
            # avoid writes to columns that no longer exist.
            candidate["project_additional_info"] = None
            candidate["persist_project_time_metadata"] = False
            candidate["persist_service_time_metadata"] = False

    def _apply_parent_customer_resolution(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        candidates: List[Dict[str, Any]],
        external_account_id: Optional[str] = None,
    ) -> None:
        """Resolve parent customer QBO mappings outside the tenant timelog query.

        Timelog export reads source data from the tenant database, but
        customer mapping ownership already lives in CustomerRepository. Keeping
        this lookup here avoids joining tenant queries to customer tables that
        may not exist in every deployment schema.
        """
        customer_uids = {
            self._clean(candidate.get("project_customer_uid"))
            for candidate in candidates
            if self._clean(candidate.get("project_customer_uid"))
        }

        resolved_by_uid: Dict[str, Dict[str, Any]] = {}
        customer_repo = CustomerRepository()
        try:
            mappings = customer_repo.get_latest_qbo_linked_mappings_for_customer_uuids(
                tenant_id=tenant_id,
                service_id=service_id,
                customer_uuids=list(customer_uids),
                external_account_id=external_account_id,
            )
        except Exception as exc:
            logger.warning(
                "[TimelogRepo] bulk parent customer mapping lookup failed | tenant_id=%s service_id=%s count=%s error=%s",
                tenant_id,
                service_id,
                len(customer_uids),
                exc,
            )
            mappings = {}
        for customer_uid in customer_uids:
            mapping = mappings.get(customer_uid) or {}
            resolved_by_uid[customer_uid] = {
                "qbo_parent_customer_id": self._clean(mapping.get("external_customer_id")) or None,
                "parent_customer_sync_status": self._clean(mapping.get("sync_status")) or None,
            }

        for candidate in candidates:
            customer_uid = self._clean(candidate.get("project_customer_uid"))
            candidate.update(
                resolved_by_uid.get(
                    customer_uid,
                    {"qbo_parent_customer_id": None, "parent_customer_sync_status": None},
                )
            )

    def _project_sync_target_from_reason(self, *, reason: Any, qbo_project_id: Any = None) -> Optional[str]:
        payload: Dict[str, Any] = {}
        if isinstance(reason, dict):
            payload = reason
        else:
            raw_reason = self._clean(reason)
            if raw_reason:
                try:
                    parsed = json.loads(raw_reason)
                    payload = parsed if isinstance(parsed, dict) else {}
                except Exception:
                    payload = {}

        target = self._clean(
            payload.get(const.KEY_PROJECT_SYNC_TARGET)
            or payload.get("projectSyncTarget")
            or payload.get("syncTarget")
        ).upper()
        if target in const.PROJECT_SYNC_TARGETS_ALLOWED:
            return target
        if self._clean(qbo_project_id):
            return const.PROJECT_SYNC_TARGET_SUB_CUSTOMER
        return None

    def _export_candidate_stmt(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        limit: int = 500,
        offset: int = 0,
        mapping_id: Any = None,
        after_mapping_id: Optional[int] = None,
        external_account_id: Optional[str] = None,
        ready_only: bool = True,
    ):
        service_id_int = self._service_id_int(service_id)
        uem = self._latest_employee_map_lateral(
            tenant_id=tenant_id,
            service_id_int=service_id_int,
            external_account_id=external_account_id,
        )
        pem = self._latest_project_map_lateral(
            tenant_id=tenant_id,
            service_id_int=service_id_int,
            external_account_id=external_account_id,
        )
        res = self._resource_lateral()

        stmt = (
            select(
                TimelogExternalMapModel.id.label("map_id"),
                TimelogExternalMapModel.external_time_activity_id,
                TimelogExternalMapModel.external_entity_type.label(
                    "mapping_external_entity_type"
                ),
                TimelogExternalMapModel.external_time_timesheet_id,
                TimelogExternalMapModel.external_account_id,
                TimelogExternalMapModel.sync_token,
                TimelogExternalMapModel.reason.label("reason"),
                TimelogExternalMapModel.sync_status.label("map_sync_status"),
                TimelogModel.id.label("timelog_id"),
                TimelogModel.start_time,
                TimelogModel.end_time,
                TimelogModel.status.label("timelog_status"),
                TimelogModel.approval_status,
                TimelogModel.invalid.label("timelog_invalid"),
                func.coalesce(TimelogModel.updated_at, TimelogModel.created_at).label("timelog_updated_at"),
                ResourceScheduleModel.id.label("resource_schedule_id"),
                ResourceScheduleModel.project_id,
                ResourceScheduleModel.resource_id,
                res.c.fieldcraft_resource_id,
                res.c.resource_user_account_id,
                ResourceScheduleModel.task_id,
                cast(ProjectModel.customer_uid, String).label("project_customer_uid"),
                ProjectModel.description.label("project_name"),
                ProjectModel.order_no.label("project_order_no"),
                TaskModel.task_name,
                TaskModel.task_uid,
                TaskModel.assignees,
                UserAccountModel.id.label("user_account_id"),
                UserAccountModel.object_id.label("user_account_uuid"),
                UserAccountModel.firstname,
                UserAccountModel.lastname,
                UserAccountModel.email.label("employee_email"),
                res.c.resource_cost,
                res.c.resource_cost_unit,
                res.c.resource_type,
                uem.c.employee_mapping_id,
                uem.c.qbo_employee_id,
                uem.c.qbo_vendor_id,
                uem.c.external_entity_type,
                uem.c.employee_external_display_name,
                uem.c.external_time_user_id,
                uem.c.employee_sync_status,
                pem.c.project_mapping_id,
                pem.c.qbo_project_id,
                pem.c.project_additional_info,
                pem.c.project_sync_status,
                pem.c.project_mapping_reason,
            )
            .select_from(TimelogExternalMapModel)
            .join(TimelogModel, TimelogModel.id == TimelogExternalMapModel.timelog_id)
            .join(ResourceScheduleModel, ResourceScheduleModel.id == TimelogModel.resource_schedule_id)
            .join(TaskModel, TaskModel.id == ResourceScheduleModel.task_id)
            .outerjoin(ProjectModel, ProjectModel.id == ResourceScheduleModel.project_id)
            .outerjoin(res, true())
            .outerjoin(UserAccountModel, UserAccountModel.id == res.c.resource_user_account_id)
            .outerjoin(uem, true())
            .outerjoin(pem, true())
            .where(
                TimelogExternalMapModel.tenant_id == tenant_id,
                TimelogExternalMapModel.service_id == service_id_int,
                func.upper(
                    func.trim(func.coalesce(TimelogExternalMapModel.sync_status, ""))
                )
                == self.STATUS_PENDING_PUSH,
                (
                    (TimelogExternalMapModel.endorsement_expiry_date.is_(None))
                    | (TimelogExternalMapModel.endorsement_expiry_date > func.now())
                ),
            )
        )

        if ready_only:
            stmt = stmt.where(self._timelog_export_ready_clause())

        if mapping_id is not None:
            return stmt.where(TimelogExternalMapModel.id == int(mapping_id)).order_by(TimelogExternalMapModel.id).limit(1)
        if after_mapping_id is not None:
            stmt = stmt.where(TimelogExternalMapModel.id > int(after_mapping_id))

        return stmt.order_by(TimelogExternalMapModel.id).limit(int(limit or 500)).offset(int(offset or 0))

    def _daily_schema_source_stmt(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        external_account_id: Optional[str] = None,
        pending_only: bool = False,
        ready_only: bool = False,
        mapping_id: Any = None,
        after_mapping_id: Optional[int] = None,
        user_account_id: Optional[int] = None,
        project_id: Optional[int] = None,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ):
        service_id_int = self._service_id_int(service_id)
        uem = self._latest_employee_map_lateral(
            tenant_id=tenant_id,
            service_id_int=service_id_int,
            external_account_id=external_account_id,
        )
        pem = self._latest_project_map_lateral_schema_v3(
            tenant_id=tenant_id,
            service_id_int=service_id_int,
            external_account_id=external_account_id,
        )
        res = self._resource_lateral()
        task_meta = self._task_metadata_laterals()

        name_value = self._task_attribute_scalar(task_meta["name"].c)
        description_value = self._task_attribute_scalar(task_meta["description"].c)
        role_value = self._task_attribute_scalar(task_meta["role"].c)

        stmt = (
            select(
                TimelogExternalMapModel.id.label("map_id"),
                TimelogExternalMapModel.external_time_activity_id,
                TimelogExternalMapModel.external_entity_type.label(
                    "mapping_external_entity_type"
                ),
                TimelogExternalMapModel.external_time_timesheet_id,
                TimelogExternalMapModel.external_account_id,
                TimelogExternalMapModel.sync_token,
                TimelogExternalMapModel.reason.label("reason"),
                TimelogExternalMapModel.sync_status.label("map_sync_status"),
                TimelogModel.id.label("timelog_id"),
                TimelogModel.start_time,
                TimelogModel.end_time,
                TimelogModel.status.label("timelog_status"),
                TimelogModel.approval_status,
                TimelogModel.invalid.label("timelog_invalid"),
                TimelogModel.break_times,
                TimelogModel.entry_type,
                func.coalesce(TimelogModel.updated_at, TimelogModel.created_at).label(
                    "timelog_updated_at"
                ),
                ResourceScheduleModel.id.label("resource_schedule_id"),
                ResourceScheduleModel.invalid.label("schedule_invalid"),
                ResourceScheduleModel.project_id,
                ResourceScheduleModel.resource_id,
                ResourceScheduleModel.task_id,
                res.c.fieldcraft_resource_id,
                res.c.resource_user_account_id,
                cast(ProjectModel.customer_uid, String).label("project_customer_uid"),
                ProjectModel.description.label("project_name"),
                ProjectModel.order_no.label("project_order_no"),
                ProjectModel.location.label("project_location"),
                ProjectModel.zipcode.label("project_zipcode"),
                ProjectModel.customer_asset.label("project_customer_asset"),
                ProjectModel.jobsite_lat.label("project_jobsite_lat"),
                ProjectModel.jobsite_lng.label("project_jobsite_lng"),
                ProjectModel.geocode_status.label("project_geocode_status"),
                cast(TaskModel.task_uid, String).label("task_uid"),
                name_value.label("task_name_value"),
                description_value.label("task_description_value"),
                role_value.label("task_role_value"),
                task_meta["role"].c.value_jsonb.label("task_role_json"),
                task_meta["assignees"].c.value_jsonb.label("task_assignees_json"),
                task_meta["assignees"].c.value_text.label("task_assignees_text"),
                task_meta["legacy"].c.legacy_assignees.label("legacy_task_assignees"),
                UserAccountModel.id.label("user_account_id"),
                UserAccountModel.object_id.label("user_account_uuid"),
                UserAccountModel.firstname,
                UserAccountModel.lastname,
                UserAccountModel.email.label("employee_email"),
                res.c.resource_cost,
                res.c.resource_cost_unit,
                res.c.resource_type,
                uem.c.employee_mapping_id,
                uem.c.qbo_employee_id,
                uem.c.qbo_vendor_id,
                uem.c.external_entity_type,
                uem.c.employee_external_display_name,
                uem.c.external_time_user_id,
                uem.c.employee_sync_status,
                pem.c.project_mapping_id,
                pem.c.qbo_project_id,
                pem.c.project_sync_status,
                pem.c.project_mapping_reason,
            )
            .select_from(TimelogExternalMapModel)
            .join(TimelogModel, TimelogModel.id == TimelogExternalMapModel.timelog_id)
            .join(
                ResourceScheduleModel,
                ResourceScheduleModel.id == TimelogModel.resource_schedule_id,
            )
            .outerjoin(TaskModel, TaskModel.id == ResourceScheduleModel.task_id)
            .outerjoin(ProjectModel, ProjectModel.id == ResourceScheduleModel.project_id)
            .outerjoin(res, true())
            .outerjoin(UserAccountModel, UserAccountModel.id == res.c.resource_user_account_id)
            .outerjoin(uem, true())
            .outerjoin(pem, true())
            .outerjoin(task_meta["name"], true())
            .outerjoin(task_meta["description"], true())
            .outerjoin(task_meta["assignees"], true())
            .outerjoin(task_meta["role"], true())
            .outerjoin(task_meta["legacy"], true())
            .where(
                TimelogExternalMapModel.tenant_id == tenant_id,
                TimelogExternalMapModel.service_id == service_id_int,
                self._is_current_mapping_clause(),
                (
                    (TimelogExternalMapModel.endorsement_expiry_date.is_(None))
                    | (TimelogExternalMapModel.endorsement_expiry_date > func.now())
                ),
            )
        )

        if pending_only:
            stored_status = func.upper(
                func.trim(func.coalesce(TimelogExternalMapModel.sync_status, ""))
            )
            stmt = stmt.where(
                or_(
                    stored_status == self.STATUS_PENDING_PUSH,
                    # Daily sync failures are retryable. Source-ready rows are
                    # exported; source-not-ready rows are normalized back to a
                    # pending/not-ready state without calling QuickBooks Time.
                    stored_status == self.STATUS_SYNC_FAILED,
                    and_(
                        stored_status == self.STATUS_SYNCED,
                        func.trim(
                            func.coalesce(
                                TimelogExternalMapModel.external_time_timesheet_id,
                                "",
                            )
                        )
                        == "",
                        func.trim(
                            func.coalesce(
                                TimelogExternalMapModel.external_time_activity_id,
                                "",
                            )
                        )
                        == "",
                    ),
                )
            )
        if ready_only:
            stmt = stmt.where(
                self._timelog_export_ready_clause(require_active_schedule=False)
            )
        if mapping_id is not None:
            stmt = stmt.where(TimelogExternalMapModel.id == int(mapping_id))
        if after_mapping_id is not None:
            stmt = stmt.where(TimelogExternalMapModel.id > int(after_mapping_id))
        if user_account_id is not None:
            stmt = stmt.where(UserAccountModel.id == int(user_account_id))
        if project_id is not None:
            stmt = stmt.where(ResourceScheduleModel.project_id == int(project_id))
        if start_at is not None:
            stmt = stmt.where(TimelogModel.start_time >= start_at)
        if end_at is not None:
            stmt = stmt.where(TimelogModel.start_time < end_at)

        stmt = stmt.order_by(TimelogModel.start_time, TimelogExternalMapModel.id)
        if limit is not None:
            stmt = stmt.limit(max(int(limit), 1)).offset(max(int(offset or 0), 0))
        return stmt

    def _prepare_daily_schema_candidates(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        rows: Any,
        external_account_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        candidates = [dict(row._mapping) for row in rows]
        self._normalize_task_metadata(candidates)
        self._apply_role_resolution(
            tenant_id=tenant_id,
            service_id=service_id,
            candidates=candidates,
            external_account_id=external_account_id,
        )
        self._apply_parent_customer_resolution(
            tenant_id=tenant_id,
            service_id=service_id,
            candidates=candidates,
            external_account_id=external_account_id,
        )
        return candidates

    def get_daily_pending_candidates(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        limit: int = 500,
        offset: int = 0,
        after_mapping_id: Optional[int] = None,
        external_account_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(
                self._daily_schema_source_stmt(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    external_account_id=external_account_id,
                    pending_only=True,
                    ready_only=False,
                    after_mapping_id=after_mapping_id,
                    limit=limit,
                    offset=offset,
                )
            ).all()
        return self._prepare_daily_schema_candidates(
            tenant_id=tenant_id,
            service_id=service_id,
            rows=rows,
            external_account_id=external_account_id,
        )

    def get_daily_worker_day_rows(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        user_account_id: int,
        start_at: datetime,
        end_at: datetime,
        project_id: Optional[int],
        external_account_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(
                self._daily_schema_source_stmt(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    external_account_id=external_account_id,
                    pending_only=False,
                    ready_only=False,
                    user_account_id=user_account_id,
                    project_id=project_id,
                    start_at=start_at,
                    end_at=end_at,
                )
            ).all()
        return self._prepare_daily_schema_candidates(
            tenant_id=tenant_id,
            service_id=service_id,
            rows=rows,
            external_account_id=external_account_id,
        )

    def get_export_candidates(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        limit: int = 500,
        offset: int = 0,
        after_mapping_id: Optional[int] = None,
        external_account_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return queued timelogs with all source data needed for QuickBooks Time export."""
        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(
                self._export_candidate_stmt(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    limit=limit,
                    offset=offset,
                    after_mapping_id=after_mapping_id,
                    external_account_id=external_account_id,
                )
            ).all()
        candidates = [dict(row._mapping) for row in rows]
        self._apply_role_resolution(
            tenant_id=tenant_id,
            service_id=service_id,
            candidates=candidates,
            external_account_id=external_account_id,
        )
        self._apply_parent_customer_resolution(
            tenant_id=tenant_id,
            service_id=service_id,
            candidates=candidates,
            external_account_id=external_account_id,
        )
        return candidates

    def get_export_candidate_by_mapping_id(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        mapping_id: Any,
        external_account_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return one queued timelog export candidate by timelog_external_map id."""
        if self._daily_schema_enabled():
            with tenant_session_scope(tenant_id) as session:
                row = session.execute(
                    self._daily_schema_source_stmt(
                        tenant_id=tenant_id,
                        service_id=service_id,
                        mapping_id=mapping_id,
                        external_account_id=external_account_id,
                        ready_only=False,
                    )
                ).first()
            if not row:
                return None
            candidates = self._prepare_daily_schema_candidates(
                tenant_id=tenant_id,
                service_id=service_id,
                rows=[row],
                external_account_id=external_account_id,
            )
            return candidates[0] if candidates else None

        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                self._export_candidate_stmt(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    mapping_id=mapping_id,
                    external_account_id=external_account_id,
                    ready_only=False,
                )
            ).first()
        if not row:
            return None
        data = dict(row._mapping)
        self._apply_role_resolution(
            tenant_id=tenant_id,
            service_id=service_id,
            candidates=[data],
            external_account_id=external_account_id,
        )
        self._apply_parent_customer_resolution(
            tenant_id=tenant_id,
            service_id=service_id,
            candidates=[data],
            external_account_id=external_account_id,
        )
        return data

    def resolve_role_for_candidate(self, *, tenant_id: str, service_id: Any, candidate: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve service role data for a single candidate.

        Kept for callers that need single-row resolution. Batch paths should use
        _apply_role_resolution() to avoid opening one DB session per candidate.
        """
        data = dict(candidate)
        self._apply_role_resolution(
            tenant_id=tenant_id, service_id=service_id, candidates=[data], external_account_id=None
        )
        return {
            "role_name": data.get("role_name"),
            "role_id": data.get("role_id"),
            "role_mapping_id": data.get("role_mapping_id"),
            "qbo_service_id": data.get("qbo_service_id"),
            "service_sync_status": data.get("service_sync_status"),
            "service_additional_info": data.get("service_additional_info") or {},
        }

    def _candidate_role_name(self, candidate: Dict[str, Any]) -> str:
        direct_role = self._clean(candidate.get("task_role_name"))
        if direct_role:
            return direct_role
        assignee = self._find_assignee(candidate.get("assignees"), candidate.get("user_account_uuid"))
        assignee = assignee or {}
        return self._clean(
            assignee.get("role")
            or assignee.get("roleName")
            or assignee.get("serviceRole")
            or assignee.get("service_role")
            or assignee.get("name")
        )

    @staticmethod
    def _empty_role_result(role_name: str) -> Dict[str, Any]:
        return {
            "role_name": role_name,
            "role_id": None,
            "role_mapping_id": None,
            "qbo_service_id": None,
            "service_sync_status": None,
            "service_additional_info": {},
        }

    def _apply_role_resolution(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        candidates: List[Dict[str, Any]],
        external_account_id: Optional[str] = None,
    ) -> None:
        role_names_by_key: Dict[str, str] = {}
        for candidate in candidates:
            role_name = self._candidate_role_name(candidate)
            candidate.update(self._empty_role_result(role_name))
            if role_name:
                role_names_by_key[role_name.lower()] = role_name

        if not role_names_by_key:
            return

        resolved_roles = self._resolve_roles_by_name(
            tenant_id=tenant_id,
            service_id=service_id,
            role_name_keys=list(role_names_by_key.keys()),
            external_account_id=external_account_id,
        )
        for candidate in candidates:
            role_key = self._clean(candidate.get("role_name")).lower()
            if role_key in resolved_roles:
                candidate.update(resolved_roles[role_key])

    def _resolve_roles_by_name(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        role_name_keys: List[str],
        external_account_id: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        normalized_role_name = func.lower(func.trim(RoleModel.name))
        service_id_int = self._service_id_int(service_id)
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(
                    normalized_role_name.label("normalized_role_name"),
                    RoleModel.id.label("role_id"),
                    RoleExternalMapModel.id.label("role_mapping_id"),
                    RoleExternalMapModel.external_service_id.label("qbo_service_id"),
                    RoleExternalMapModel.sync_status.label("service_sync_status"),
                    RoleExternalMapModel.additional_info.label("service_additional_info"),
                    RoleExternalMapModel.external_time_customfield_item_id.label(
                        "service_time_customfield_item_id"
                    ),
                )
                .select_from(RoleModel)
                .outerjoin(
                    RoleExternalMapModel,
                    (RoleExternalMapModel.role_id == RoleModel.id)
                    & (RoleExternalMapModel.tenant_id == tenant_id)
                    & (RoleExternalMapModel.service_id == service_id_int)
                    & (
                        (RoleExternalMapModel.endorsement_expiry_date.is_(None))
                        | (RoleExternalMapModel.endorsement_expiry_date > func.now())
                    )
                    & (func.upper(func.coalesce(RoleExternalMapModel.sync_status, "")) != self.STATUS_INVALID)
                    & (
                        or_(
                            func.trim(func.coalesce(RoleExternalMapModel.external_account_id, ""))
                            == self._clean(external_account_id),
                            func.trim(func.coalesce(RoleExternalMapModel.external_account_id, "")) == "",
                        )
                        if self._clean(external_account_id)
                        else true()
                    ),
                )
                .where(normalized_role_name.in_(role_name_keys))
                .order_by(
                    normalized_role_name,
                    (
                        func.trim(func.coalesce(RoleExternalMapModel.external_account_id, ""))
                        == self._clean(external_account_id)
                    ).desc(),
                    RoleExternalMapModel.id.desc().nullslast(),
                    RoleModel.id.desc(),
                )
            )
            rows = session.execute(stmt).all()

        resolved: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            data = dict(row._mapping)
            role_key = self._clean(data.pop("normalized_role_name")).lower()
            data["service_additional_info"] = self._reason_payload(
                data.get("service_additional_info")
            )
            if role_key and role_key not in resolved:
                resolved[role_key] = data
        return resolved

    def _find_assignee(self, assignees_raw: Any, user_account_uuid: Any) -> Optional[Dict[str, Any]]:
        data = self._decode_jsonish(assignees_raw)
        if isinstance(data, dict):
            data = data.get("assignees") or data.get("items") or [data]
        if not isinstance(data, list):
            return None

        normalized: List[Dict[str, Any]] = []
        for item in data:
            decoded = self._decode_jsonish(item)
            if isinstance(decoded, dict):
                normalized.append(decoded)
        data = normalized
        if not data:
            return None

        resource_uid = self._clean(user_account_uuid).lower()
        for item in data:
            item_resource_uid = self._clean(
                item.get("resourceUid")
                or item.get("resource_uid")
                or item.get("userAccountUuid")
                or item.get("user_account_uuid")
                or item.get("userUuid")
                or item.get("user_uuid")
            ).lower()
            if resource_uid and item_resource_uid == resource_uid:
                return item
        return data[0]


    @staticmethod
    def _trusted_timezone_snapshot_condition(columns: Any):
        normalized_reason = func.replace(
            func.upper(func.coalesce(columns.reason, "")),
            " ",
            "",
        )
        timezone_present = or_(
            normalized_reason.like('%"DAILYTIMEZONE"%'),
            normalized_reason.like('%"DAILY_TIMEZONE"%'),
        )
        trusted_source_patterns = [
            normalized_reason.like(f'%"{key}":"{source}"%')
            for source in sorted(ProjectTimezoneResolver.TRUSTED_SNAPSHOT_SOURCES)
            for key in ("DAILYTIMEZONESOURCE", "DAILY_TIMEZONE_SOURCE")
        ]
        return and_(timezone_present, or_(*trusted_source_patterns))

    def _timelog_effective_status_expr(self, columns: Any):
        not_ready_conditions = [
            columns.timelog_id.is_(None),
            columns.source_timelog_id.is_(None),
            func.lower(func.trim(func.coalesce(columns.timelog_status, "")))
            != "completed",
            columns.start_time.is_(None),
            columns.end_time.is_(None),
            columns.end_time <= columns.start_time,
        ]
        if self._daily_schema_enabled():
            # In schema v3, a completed task's schedule is retired and marked
            # invalid. The historical schedule still owns the employee,
            # project and task references needed by its approved timelog, so it
            # must not make the timelog ineligible.
            not_ready_conditions.extend(
                [
                    func.upper(
                        func.trim(func.coalesce(columns.approval_status, ""))
                    )
                    != self.APPROVAL_STATUS_APPROVED,
                    func.coalesce(columns.timelog_invalid, False) == True,
                    ~func.upper(
                        func.trim(func.coalesce(columns.entry_type, ""))
                    ).in_(["", "WORK"]),
                    ~or_(
                        and_(
                            columns.project_jobsite_lat.is_not(None),
                            columns.project_jobsite_lng.is_not(None),
                            columns.project_jobsite_lat >= -90,
                            columns.project_jobsite_lat <= 90,
                            columns.project_jobsite_lng >= -180,
                            columns.project_jobsite_lng <= 180,
                        ),
                        func.trim(
                            func.coalesce(columns.project_location, "")
                        )
                        != "",
                        and_(
                            columns.project_customer_asset.is_not(None),
                            or_(
                                cast(columns.project_customer_asset, Text).like(
                                    '%"location"%'
                                ),
                                cast(columns.project_customer_asset, Text).like(
                                    '%"latitude"%'
                                ),
                                cast(columns.project_customer_asset, Text).like(
                                    '%"longitude"%'
                                ),
                                cast(columns.project_customer_asset, Text).like(
                                    '%"lat"%'
                                ),
                                cast(columns.project_customer_asset, Text).like(
                                    '%"lng"%'
                                ),
                                cast(columns.project_customer_asset, Text).like(
                                    '%"coordinates"%'
                                ),
                            ),
                        ),
                        self._trusted_timezone_snapshot_condition(columns),
                    ),
                ]
            )
            external_id_missing = and_(
                func.trim(
                    func.coalesce(columns.external_time_timesheet_id, "")
                )
                == "",
                func.trim(func.coalesce(columns.external_time_activity_id, ""))
                == "",
            )
        else:
            not_ready_conditions.append(
                func.coalesce(columns.schedule_invalid, False) == True
            )
            external_id_missing = (
                func.trim(func.coalesce(columns.external_time_activity_id, ""))
                == ""
            )

        return case(
            (
                or_(*not_ready_conditions),
                "NOT_READY",
            ),
            (
                or_(
                    columns.sync_status.is_(None),
                    func.trim(columns.sync_status) == "",
                    and_(
                        func.upper(func.trim(columns.sync_status))
                        == self.STATUS_SYNCED,
                        external_id_missing,
                    ),
                ),
                self.STATUS_PENDING_PUSH,
            ),
            else_=func.upper(columns.sync_status),
        )

    @classmethod
    def _timelog_not_ready_reason(cls, data: Dict[str, Any]) -> str:
        """Return the exact source-data reason a timelog is not exportable."""

        def reason(code: str, message: str, **details: Any) -> str:
            payload: Dict[str, Any] = {"message": message, "code": code}
            payload.update(
                {key: value for key, value in details.items() if value is not None}
            )
            return json.dumps(payload, default=str, separators=(",", ":"))

        if not data.get("source_timelog_id"):
            return reason(
                "TIMELOG_SOURCE_MISSING",
                "This mapping row exists, but the source timelog could not be found.",
            )

        status = cls._clean(data.get("timelog_status")).lower()
        if status != "completed":
            return reason(
                "TIMELOG_NOT_COMPLETED",
                "The timelog must be completed before it can be exported.",
                timelogStatus=data.get("timelog_status"),
            )

        if cls._daily_schema_enabled():
            approval_status = cls._clean(data.get("approval_status")).upper()
            if approval_status != cls.APPROVAL_STATUS_APPROVED:
                return reason(
                    "TIMELOG_NOT_APPROVED",
                    "The timelog must be approved before it can be exported.",
                    approvalStatus=data.get("approval_status"),
                )

            if bool(data.get("timelog_invalid")):
                return reason(
                    "TIMELOG_INVALID",
                    "The timelog is marked invalid and cannot be exported.",
                )

            entry_type = cls._clean(data.get("entry_type")).upper()
            if entry_type not in {"", "WORK"}:
                return reason(
                    "TIMELOG_ENTRY_TYPE_NOT_SUPPORTED",
                    "Only work timelogs can be exported to QuickBooks Time.",
                    entryType=data.get("entry_type"),
                )

        start_time = data.get("start_time")
        if start_time is None:
            return reason(
                "TIMELOG_START_TIME_MISSING",
                "The timelog does not have a start time.",
            )

        end_time = data.get("end_time")
        if end_time is None:
            return reason(
                "TIMELOG_END_TIME_MISSING",
                "The timelog does not have an end time.",
            )

        try:
            if end_time <= start_time:
                return reason(
                    "TIMELOG_NON_POSITIVE_DURATION",
                    "The timelog end time must be later than its start time.",
                    startTime=start_time,
                    endTime=end_time,
                )
        except TypeError:
            return reason(
                "TIMELOG_INVALID_TIME_RANGE",
                "The timelog start or end time is invalid.",
                startTime=start_time,
                endTime=end_time,
            )

        if not cls._daily_schema_enabled() and bool(data.get("schedule_invalid")):
            return reason(
                "RESOURCE_SCHEDULE_INVALID",
                "The resource schedule is invalid and cannot be exported.",
            )

        timezone_error_code = cls._clean(data.get("timezone_error_code"))
        if timezone_error_code:
            return reason(
                timezone_error_code,
                cls._clean(data.get("timezone_error_message"))
                or "The project timezone could not be resolved safely.",
                projectId=data.get("project_id"),
                geocodeStatus=data.get("project_geocode_status"),
            )

        return reason(
            "TIMELOG_NOT_READY",
            "The timelog does not currently satisfy the export requirements.",
        )

    def _timelog_cdc_base_cte_schema_v3(
        self,
        *,
        tenant_id: str,
        service_id_int: int,
    ):
        latest_map = (
            select(
                TimelogExternalMapModel.id.label("mapping_id"),
                TimelogExternalMapModel.timelog_id,
                TimelogExternalMapModel.external_time_activity_id,
                TimelogExternalMapModel.external_entity_type.label(
                    "mapping_external_entity_type"
                ),
                TimelogExternalMapModel.external_time_timesheet_id,
                TimelogExternalMapModel.external_account_id,
                TimelogExternalMapModel.external_display_name,
                TimelogExternalMapModel.sync_status,
                TimelogExternalMapModel.sync_token,
                TimelogExternalMapModel.version.label("mapping_version"),
                TimelogExternalMapModel.updated_at.label("mapping_updated_at"),
                TimelogExternalMapModel.reason,
            )
            .where(
                TimelogExternalMapModel.tenant_id == tenant_id,
                TimelogExternalMapModel.service_id == service_id_int,
                func.coalesce(TimelogExternalMapModel.sync_status, "") != self.STATUS_INVALID,
                (
                    (TimelogExternalMapModel.endorsement_expiry_date.is_(None))
                    | (TimelogExternalMapModel.endorsement_expiry_date > func.now())
                ),
            )
            .distinct(TimelogExternalMapModel.timelog_id)
            .order_by(TimelogExternalMapModel.timelog_id, TimelogExternalMapModel.id.desc())
            .cte("latest_map")
        )

        uem = self._latest_employee_map_lateral(
            tenant_id=tenant_id,
            service_id_int=service_id_int,
        )
        pem = self._latest_project_map_lateral_schema_v3(
            tenant_id=tenant_id,
            service_id_int=service_id_int,
        )
        res = self._resource_lateral()
        task_meta = self._task_metadata_laterals()
        employee_name = func.nullif(
            func.trim(
                func.coalesce(UserAccountModel.firstname, "")
                + literal(" ")
                + func.coalesce(UserAccountModel.lastname, "")
            ),
            "",
        )

        return (
            select(
                latest_map.c.mapping_id,
                latest_map.c.timelog_id,
                latest_map.c.external_time_activity_id,
                latest_map.c.mapping_external_entity_type,
                latest_map.c.external_time_timesheet_id,
                latest_map.c.external_account_id,
                latest_map.c.external_display_name,
                latest_map.c.sync_status,
                latest_map.c.sync_token,
                latest_map.c.mapping_version,
                latest_map.c.mapping_updated_at,
                latest_map.c.reason,
                TimelogModel.id.label("source_timelog_id"),
                TimelogModel.resource_schedule_id,
                TimelogModel.start_time,
                TimelogModel.end_time,
                TimelogModel.status.label("timelog_status"),
                TimelogModel.approval_status,
                TimelogModel.invalid.label("timelog_invalid"),
                TimelogModel.entry_type,
                func.coalesce(
                    TimelogModel.updated_at,
                    TimelogModel.created_at,
                    latest_map.c.mapping_updated_at,
                ).label("timelog_updated_at"),
                ResourceScheduleModel.id.label("schedule_id"),
                ResourceScheduleModel.invalid.label("schedule_invalid"),
                ResourceScheduleModel.project_id,
                ResourceScheduleModel.resource_id,
                res.c.fieldcraft_resource_id,
                res.c.resource_user_account_id,
                ResourceScheduleModel.task_id,
                cast(ProjectModel.customer_uid, String).label("project_customer_uid"),
                cast(TaskModel.task_uid, String).label("task_uid"),
                func.coalesce(
                    self._task_attribute_scalar(task_meta["name"].c),
                    self._task_attribute_scalar(task_meta["description"].c),
                ).label("task_name"),
                self._task_attribute_scalar(task_meta["role"].c).label("task_role_value"),
                task_meta["role"].c.value_jsonb.label("task_role_json"),
                task_meta["assignees"].c.value_jsonb.label("task_assignees_json"),
                task_meta["assignees"].c.value_text.label("task_assignees_text"),
                task_meta["legacy"].c.legacy_assignees.label("legacy_task_assignees"),
                ProjectModel.description.label("project_name"),
                ProjectModel.location.label("project_location"),
                ProjectModel.zipcode.label("project_zipcode"),
                ProjectModel.customer_asset.label("project_customer_asset"),
                ProjectModel.jobsite_lat.label("project_jobsite_lat"),
                ProjectModel.jobsite_lng.label("project_jobsite_lng"),
                ProjectModel.geocode_status.label("project_geocode_status"),
                UserAccountModel.id.label("user_account_id"),
                UserAccountModel.object_id.label("user_account_uuid"),
                employee_name.label("employee_name"),
                res.c.resource_type,
                uem.c.employee_mapping_id,
                uem.c.qbo_employee_id,
                uem.c.qbo_vendor_id,
                uem.c.external_entity_type,
                uem.c.employee_external_display_name,
                uem.c.external_time_user_id,
                uem.c.employee_sync_status,
                pem.c.qbo_project_id,
                pem.c.project_sync_status,
                pem.c.project_mapping_reason,
            )
            .select_from(latest_map)
            .outerjoin(TimelogModel, TimelogModel.id == latest_map.c.timelog_id)
            .outerjoin(
                ResourceScheduleModel,
                ResourceScheduleModel.id == TimelogModel.resource_schedule_id,
            )
            .outerjoin(TaskModel, TaskModel.id == ResourceScheduleModel.task_id)
            .outerjoin(ProjectModel, ProjectModel.id == ResourceScheduleModel.project_id)
            .outerjoin(res, true())
            .outerjoin(UserAccountModel, UserAccountModel.id == res.c.resource_user_account_id)
            .outerjoin(uem, true())
            .outerjoin(pem, true())
            .outerjoin(task_meta["name"], true())
            .outerjoin(task_meta["description"], true())
            .outerjoin(task_meta["assignees"], true())
            .outerjoin(task_meta["role"], true())
            .outerjoin(task_meta["legacy"], true())
            .cte("base")
        )

    def _timelog_cdc_base_cte(self, *, tenant_id: str, service_id_int: int):
        if self._daily_schema_enabled():
            return self._timelog_cdc_base_cte_schema_v3(
                tenant_id=tenant_id,
                service_id_int=service_id_int,
            )
        latest_map = (
            select(
                TimelogExternalMapModel.id.label("mapping_id"),
                TimelogExternalMapModel.timelog_id,
                TimelogExternalMapModel.external_time_activity_id,
                TimelogExternalMapModel.external_entity_type.label(
                    "mapping_external_entity_type"
                ),
                TimelogExternalMapModel.external_time_timesheet_id,
                TimelogExternalMapModel.external_account_id,
                TimelogExternalMapModel.external_display_name,
                TimelogExternalMapModel.sync_status,
                TimelogExternalMapModel.sync_token,
                TimelogExternalMapModel.version.label("mapping_version"),
                TimelogExternalMapModel.updated_at.label("mapping_updated_at"),
                TimelogExternalMapModel.reason,
            )
            .where(
                TimelogExternalMapModel.tenant_id == tenant_id,
                TimelogExternalMapModel.service_id == service_id_int,
                func.coalesce(TimelogExternalMapModel.sync_status, "") != self.STATUS_INVALID,
                (
                    (TimelogExternalMapModel.endorsement_expiry_date.is_(None))
                    | (TimelogExternalMapModel.endorsement_expiry_date > func.now())
                ),
            )
            .distinct(TimelogExternalMapModel.timelog_id)
            .order_by(TimelogExternalMapModel.timelog_id, TimelogExternalMapModel.id.desc())
            .cte("latest_map")
        )

        uem = self._latest_employee_map_lateral(tenant_id=tenant_id, service_id_int=service_id_int)
        pem = self._latest_project_map_lateral(tenant_id=tenant_id, service_id_int=service_id_int)
        res = self._resource_lateral()
        employee_name = func.nullif(
            func.trim(func.coalesce(UserAccountModel.firstname, "") + literal(" ") + func.coalesce(UserAccountModel.lastname, "")),
            "",
        )

        return (
            select(
                latest_map.c.mapping_id,
                latest_map.c.timelog_id,
                latest_map.c.external_time_activity_id,
                latest_map.c.mapping_external_entity_type,
                latest_map.c.external_time_timesheet_id,
                latest_map.c.external_account_id,
                latest_map.c.external_display_name,
                latest_map.c.sync_status,
                latest_map.c.sync_token,
                latest_map.c.mapping_version,
                latest_map.c.mapping_updated_at,
                latest_map.c.reason,
                TimelogModel.id.label("source_timelog_id"),
                TimelogModel.resource_schedule_id,
                TimelogModel.start_time,
                TimelogModel.end_time,
                TimelogModel.status.label("timelog_status"),
                func.coalesce(TimelogModel.updated_at, TimelogModel.created_at, latest_map.c.mapping_updated_at).label("timelog_updated_at"),
                ResourceScheduleModel.id.label("schedule_id"),
                ResourceScheduleModel.invalid.label("schedule_invalid"),
                ResourceScheduleModel.project_id,
                ResourceScheduleModel.resource_id,
                res.c.fieldcraft_resource_id,
                res.c.resource_user_account_id,
                ResourceScheduleModel.task_id,
                cast(ProjectModel.customer_uid, String).label("project_customer_uid"),
                TaskModel.task_name,
                TaskModel.task_uid,
                TaskModel.assignees,
                ProjectModel.description.label("project_name"),
                UserAccountModel.id.label("user_account_id"),
                UserAccountModel.object_id.label("user_account_uuid"),
                employee_name.label("employee_name"),
                res.c.resource_type,
                uem.c.employee_mapping_id,
                uem.c.qbo_employee_id,
                uem.c.qbo_vendor_id,
                uem.c.external_entity_type,
                uem.c.employee_external_display_name,
                uem.c.external_time_user_id,
                uem.c.employee_sync_status,
                pem.c.qbo_project_id,
                pem.c.project_sync_status,
                pem.c.project_mapping_reason,
            )
            .select_from(latest_map)
            .outerjoin(TimelogModel, TimelogModel.id == latest_map.c.timelog_id)
            .outerjoin(ResourceScheduleModel, ResourceScheduleModel.id == TimelogModel.resource_schedule_id)
            .outerjoin(TaskModel, TaskModel.id == ResourceScheduleModel.task_id)
            .outerjoin(ProjectModel, ProjectModel.id == ResourceScheduleModel.project_id)
            .outerjoin(res, true())
            .outerjoin(UserAccountModel, UserAccountModel.id == res.c.resource_user_account_id)
            .outerjoin(uem, true())
            .outerjoin(pem, true())
            .cte("base")
        )

    def _timelog_cdc_filtered_cte(self, *, base: Any, search_text: str):
        effective_status = self._timelog_effective_status_expr(base.c).label("effective_sync_status")
        stmt = select(*list(base.c), effective_status)

        if search_text:
            search_value = f"%{search_text.lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(func.coalesce(base.c.task_name, "")).like(search_value),
                    func.lower(func.coalesce(base.c.employee_name, "")).like(search_value),
                    func.lower(func.coalesce(base.c.project_name, "")).like(search_value),
                    func.lower(func.coalesce(base.c.external_time_activity_id, "")).like(search_value),
                    func.lower(func.coalesce(base.c.external_time_timesheet_id, "")).like(
                        search_value
                    ),
                    func.lower(func.coalesce(base.c.external_account_id, "")).like(search_value),
                    func.lower(func.coalesce(base.c.external_display_name, "")).like(search_value),
                    func.lower(func.coalesce(base.c.qbo_employee_id, "")).like(search_value),
                    func.lower(func.coalesce(base.c.qbo_vendor_id, "")).like(search_value),
                    func.lower(func.coalesce(base.c.qbo_project_id, "")).like(search_value),
                    func.lower(func.coalesce(base.c.resource_type, "")).like(search_value),
                    func.lower(func.coalesce(base.c.sync_status, "")).like(search_value),
                    func.lower(func.coalesce(base.c.reason, "")).like(search_value),
                    cast(base.c.timelog_id, String).like(search_value),
                    cast(base.c.mapping_id, String).like(search_value),
                )
            )

        return stmt.cte("filtered")

    def get_timelog_cdc_page(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        page: int = 1,
        page_size: int = 25,
        search: Optional[str] = None,
        sync_status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Paginated mapping-led view for FIELDCRAFT timelog -> QuickBooks TimeActivity sync status.

        The tracking page must show only rows that exist in timelog_external_map.
        Source timelog, task, employee, project, and service data are joined only as
        reference details for display and dependency checks.
        """
        page = max(1, int(page or 1))
        page_size = max(1, min(200, int(page_size or 25)))
        offset = (page - 1) * page_size
        service_id_int = self._service_id_int(service_id)
        search_text = self._clean(search)
        status_text = self._clean(sync_status).upper()

        base = self._timelog_cdc_base_cte(tenant_id=tenant_id, service_id_int=service_id_int)
        filtered = self._timelog_cdc_filtered_cte(base=base, search_text=search_text)
        if status_text and status_text != "ALL":
            if status_text == "NEEDS_ATTENTION":
                # "Needs attention" is a UI summary bucket, not a stored status. Expand it to
                # the statuses the summary counts as needing attention (failed + sync-failed +
                # not-ready) so the filtered rows match the pill's count.
                status_filter = filtered.c.effective_sync_status.in_(
                    [self.STATUS_FAILED, self.STATUS_SYNC_FAILED, "NOT_READY"]
                )
            else:
                status_filter = filtered.c.effective_sync_status == status_text
        else:
            status_filter = None

        total_stmt = select(func.count()).select_from(filtered)
        rows_stmt = (
            select(filtered)
            .order_by(
                func.coalesce(filtered.c.mapping_updated_at, filtered.c.timelog_updated_at, filtered.c.start_time).desc().nullslast(),
                filtered.c.mapping_id.desc(),
            )
            .limit(page_size)
            .offset(offset)
        )
        if status_filter is not None:
            total_stmt = total_stmt.where(status_filter)
            rows_stmt = rows_stmt.where(status_filter)

        summary_stmt = (
            select(filtered.c.effective_sync_status, func.count().label("count"))
            .select_from(filtered)
            .group_by(filtered.c.effective_sync_status)
        )

        with tenant_session_scope(tenant_id) as session:
            total_records = int(session.execute(total_stmt).scalar() or 0)
            summary_rows = session.execute(summary_stmt).all()
            rows = session.execute(rows_stmt).all()

        by_status = {str(row._mapping.get("effective_sync_status") or "").upper(): int(row._mapping.get("count") or 0) for row in summary_rows}
        row_data = [dict(row._mapping) for row in rows]
        if self._daily_schema_enabled():
            self._normalize_task_metadata(row_data)
        self._apply_role_resolution(tenant_id=tenant_id, service_id=service_id, candidates=row_data)
        self._apply_parent_customer_resolution(tenant_id=tenant_id, service_id=service_id, candidates=row_data)
        timezone_resolver = ProjectTimezoneResolver() if self._daily_schema_enabled() else None

        records: List[Dict[str, Any]] = []
        for data in row_data:
            effective_status = self._clean(data.get("effective_sync_status")).upper() or self.STATUS_PENDING_PUSH
            timezone_ready = True
            project_timezone = None
            project_timezone_source = None
            work_date = None
            if timezone_resolver is not None:
                try:
                    timezone_resolution = timezone_resolver.resolve(data)
                    project_timezone = timezone_resolution.timezone_name
                    project_timezone_source = timezone_resolution.source
                    start_value = data.get("start_time")
                    end_value = data.get("end_time")
                    if isinstance(start_value, datetime):
                        if start_value.tzinfo is None:
                            start_value = start_value.replace(tzinfo=timezone.utc)
                        local_start = start_value.astimezone(
                            timezone_resolution.timezone
                        )
                        work_date = local_start.date().isoformat()
                        if isinstance(end_value, datetime):
                            if end_value.tzinfo is None:
                                end_value = end_value.replace(tzinfo=timezone.utc)
                            local_end = end_value.astimezone(
                                timezone_resolution.timezone
                            )
                            if local_start.date() != local_end.date():
                                timezone_ready = False
                                effective_status = "NOT_READY"
                                data["timezone_error_code"] = (
                                    "TIMELOG_CROSSES_PROJECT_MIDNIGHT"
                                )
                                data["timezone_error_message"] = (
                                    "The timelog crosses midnight in the project "
                                    f"timezone {project_timezone}. Split the source "
                                    "timelog before exporting daily time."
                                )
                except ProjectTimezoneResolutionError as exc:
                    timezone_ready = False
                    effective_status = "NOT_READY"
                    data["timezone_error_code"] = exc.code
                    data["timezone_error_message"] = str(exc)

            mapping_identity = {
                "reason": data.get("reason"),
                "external_entity_type": data.get("mapping_external_entity_type"),
                "external_time_activity_id": data.get("external_time_activity_id"),
                "external_time_timesheet_id": data.get("external_time_timesheet_id"),
            }
            mapping_entity_type = self.mapping_external_entity_type(mapping_identity)
            mapping_external_id = self.mapping_external_id(mapping_identity)
            start_time = data.get("start_time")
            end_time = data.get("end_time")
            duration_seconds = None
            duration_minutes = None
            try:
                if start_time and end_time and end_time > start_time:
                    seconds = max(0, int((end_time - start_time).total_seconds()))
                    duration_seconds = seconds
                    duration_minutes = seconds // 60
            except Exception:
                duration_seconds = None
                duration_minutes = None

            employee_name = self._clean(data.get("employee_name"))
            if not employee_name:
                employee_fallback_id = data.get("user_account_id") or data.get("resource_user_account_id") or data.get("resource_id")
                if employee_fallback_id:
                    employee_name = f"Employee #{employee_fallback_id}"
            project_name = self._clean(data.get("project_name"))
            if not project_name and data.get("project_id"):
                project_name = f"Project #{data.get('project_id')}"

            worker_entity_type = self._mapping_worker_entity_type(data)
            raw_qbo_employee_id = self._clean(data.get("qbo_employee_id"))
            qbo_vendor_id = self._clean(data.get("qbo_vendor_id"))
            qbo_worker_id = (
                qbo_vendor_id
                if worker_entity_type == const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR
                else raw_qbo_employee_id
            )
            qbo_employee_id = raw_qbo_employee_id
            employee_linked = bool(qbo_worker_id and self._clean(data.get("employee_sync_status")).upper() == self.STATUS_SYNCED)
            project_sync_target = self._project_sync_target_from_reason(
                reason=data.get("project_mapping_reason"),
                qbo_project_id=data.get("qbo_project_id"),
            )
            project_linked = self._clean(data.get("qbo_project_id")) and self._clean(data.get("project_sync_status")).upper() == self.STATUS_SYNCED
            parent_customer_linked = True
            if project_sync_target == const.PROJECT_SYNC_TARGET_QBO_PROJECT:
                parent_customer_linked = bool(self._clean(data.get("qbo_parent_customer_id")))
            service_linked = self._clean(data.get("qbo_service_id")) and self._clean(data.get("service_sync_status")).upper() == self.STATUS_SYNCED
            ready_to_sync = effective_status != "NOT_READY" and bool(
                timezone_ready
                and employee_linked
                and project_linked
                and parent_customer_linked
                and service_linked
            )

            mapping_reason = data.get("reason")
            reason = mapping_reason
            if effective_status == "NOT_READY":
                # The source readiness reason is authoritative for a Not Ready
                # row. Keep the stored mapping reason separately so the UI can
                # still show previous sync history when needed.
                reason = self._timelog_not_ready_reason(data)
            elif effective_status == "PENDING_PUSH" and not ready_to_sync and not reason:
                missing = []
                if not employee_linked:
                    missing.append("worker")
                if not project_linked:
                    missing.append("project")
                if not parent_customer_linked:
                    missing.append("parent customer")
                if not service_linked:
                    missing.append("service")
                if missing:
                    reason = '{"message":"Waiting for synced QuickBooks reference information for: ' + ", ".join(missing) + '.","code":"TIMELOG_DEPENDENCY_PENDING"}'

            resource_type = self._clean(data.get("resource_type"))
            worker_type_label = "1099" if worker_entity_type == const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR else "W2"

            records.append({
                "timelogId": data.get("timelog_id"),
                "resourceScheduleId": data.get("resource_schedule_id"),
                "taskId": data.get("task_id"),
                "taskName": data.get("task_name"),
                "projectId": data.get("project_id"),
                "projectName": project_name or None,
                "projectTimezone": project_timezone,
                "projectTimezoneSource": project_timezone_source,
                "workDate": work_date,
                "employeeId": data.get("user_account_id") or data.get("resource_user_account_id"),
                "resourceId": data.get("fieldcraft_resource_id") or data.get("resource_id"),
                "employeeName": employee_name or None,
                "resourceType": resource_type or None,
                "employeeType": worker_type_label,
                "workerType": worker_type_label,
                "workerTypeLabel": worker_type_label,
                "workerClassification": worker_type_label,
                "roleId": data.get("role_id"),
                "roleName": data.get("role_name"),
                "startTime": data.get("start_time"),
                "endTime": data.get("end_time"),
                "durationSeconds": duration_seconds,
                "durationMinutes": duration_minutes,
                "timelogStatus": data.get("timelog_status"),
                "mappingId": data.get("mapping_id"),
                # Kept for UI/API compatibility. For a QuickBooks Time
                # timesheet this is the canonical timesheet id, even though
                # new database writes use external_time_timesheet_id.
                "externalTimeActivityId": mapping_external_id,
                "rawExternalTimeActivityId": self._clean(
                    data.get("external_time_activity_id")
                )
                or None,
                "externalTimeTimesheetId": self._clean(
                    data.get("external_time_timesheet_id")
                )
                or None,
                "externalAccountId": data.get("external_account_id"),
                "syncStatus": effective_status,
                "syncToken": data.get("sync_token"),
                "mappingVersion": data.get("mapping_version"),
                "mappingUpdatedAt": data.get("mapping_updated_at"),
                "reason": reason,
                "mappingReason": mapping_reason,
                "readyToSync": ready_to_sync,
                "employeeLinked": bool(employee_linked),
                "workerLinked": bool(employee_linked),
                "workerEntityType": worker_entity_type,
                "projectLinked": bool(project_linked),
                "serviceLinked": bool(service_linked),
                "qboEmployeeId": qbo_employee_id or None,
                "externalEmployeeId": qbo_employee_id or None,
                "quickBooksId": qbo_worker_id or None,
                "quickbooksId": qbo_worker_id or None,
                "qboWorkerId": qbo_worker_id or None,
                "externalWorkerId": qbo_worker_id or None,
                # Raw entity-specific ids for API consumers that need the exact QBO object.
                "rawQboEmployeeId": raw_qbo_employee_id or None,
                "qboEmployeeEntityId": raw_qbo_employee_id or None,
                "qboVendorId": qbo_vendor_id or None,
                "externalVendorId": qbo_vendor_id or None,
                "qboWorkerEntityType": worker_entity_type,
                "quickBooksEntityType": worker_entity_type,
                "externalEntityType": mapping_entity_type,
                "externalEntityId": mapping_external_id,
                "quickBooksTimeTimesheetId": (
                    mapping_external_id
                    if mapping_entity_type == "TIMESHEET"
                    else None
                ),
                "qboProjectId": self._clean(data.get("qbo_project_id")) or None,
                "projectSyncTarget": project_sync_target,
                "qboParentCustomerId": self._clean(data.get("qbo_parent_customer_id")) or None,
                "qboServiceId": self._clean(data.get("qbo_service_id")) or None,
                "source": "MAPPING",
            })

        failed_total = by_status.get(self.STATUS_FAILED, 0) + by_status.get(self.STATUS_SYNC_FAILED, 0)
        return {
            "summary": {
                "total": sum(by_status.values()),
                "synced": by_status.get(self.STATUS_SYNCED, 0),
                "pending": by_status.get(self.STATUS_PENDING_PUSH, 0),
                "failed": failed_total,
                "notReady": by_status.get("NOT_READY", 0),
            },
            "pagination": {
                "page": page,
                "pageSize": page_size,
                "totalRecords": total_records,
                "totalPages": (total_records + page_size - 1) // page_size if total_records else 0,
            },
            "records": records,
        }

    def mark_synced(
        self,
        *,
        tenant_id: str,
        map_id: int,
        external_time_activity_id: str,
        external_account_id: Optional[str],
        sync_token: Optional[str],
        reason: Optional[str] = None,
        external_entity_type: Optional[str] = None,
    ) -> None:
        with tenant_session_scope(tenant_id) as session:
            row = session.get(TimelogExternalMapModel, int(map_id))
            if row is None:
                logger.warning("[TimelogRepo] mark_synced skipped; mapping not found | map_id=%s", map_id)
                return
            external_id = self._clean(external_time_activity_id)
            entity_type = self._clean(external_entity_type).upper()
            if not entity_type:
                entity_type = self.mapping_external_entity_type({"reason": reason}) or "TIME_ACTIVITY"
            if entity_type == "TIMESHEET":
                row.external_time_timesheet_id = external_id
                row.external_time_activity_id = None
                row.external_entity_type = "TIMESHEET"
            else:
                row.external_time_activity_id = external_id
                row.external_time_timesheet_id = None
                row.external_entity_type = "TIME_ACTIVITY"
            row.external_account_id = self._clean(external_account_id) or None
            row.sync_token = self._clean(sync_token) or None
            row.sync_status = self.STATUS_SYNCED
            row.reason = reason
            row.updated_at = self._now()
            session.add(row)

    def list_pending_quickbooks_time_approval_targets(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        quickbooks_time_user_id: int,
    ) -> List[Dict[str, Any]]:
        """Return synced mappings whose QuickBooks Time approval is still pending.

        Approval can be blocked by another project or an older queued timelog.
        Keeping the target date and worker identifiers in the existing mapping
        reason lets a later successful export retry approval without a new table.
        """
        service_id_int = self._service_id_int(service_id)
        user_id = int(quickbooks_time_user_id)
        user_pattern = f'%"quickBooksTimeUserId":{user_id},%'
        stmt = (
            select(
                TimelogExternalMapModel.id.label("map_id"),
                TimelogExternalMapModel.reason,
            )
            .where(
                TimelogExternalMapModel.tenant_id == tenant_id,
                TimelogExternalMapModel.service_id == service_id_int,
                self._is_current_mapping_clause(),
                (
                    TimelogExternalMapModel.endorsement_expiry_date.is_(None)
                    | (TimelogExternalMapModel.endorsement_expiry_date > func.now())
                ),
                func.upper(func.trim(func.coalesce(TimelogExternalMapModel.sync_status, "")))
                == self.STATUS_SYNCED,
                or_(
                    func.trim(
                        func.coalesce(
                            TimelogExternalMapModel.external_time_timesheet_id, ""
                        )
                    )
                    != "",
                    func.trim(
                        func.coalesce(
                            TimelogExternalMapModel.external_time_activity_id, ""
                        )
                    )
                    != "",
                ),
                TimelogExternalMapModel.reason.like(user_pattern),
                TimelogExternalMapModel.reason.like('%"approvalTarget":"APPROVED"%'),
            )
            .order_by(TimelogExternalMapModel.id)
        )
        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(stmt).all()

        targets: List[Dict[str, Any]] = []
        for row in rows:
            payload = self._reason_payload(row.reason)
            try:
                payload_user_id = int(payload.get("quickBooksTimeUserId") or 0)
            except (TypeError, ValueError):
                continue
            approval_status = self._clean(payload.get("approvalStatus")).upper()
            work_date = self._clean(
                payload.get("approvalWorkDate") or payload.get("dailyWorkDate")
            )
            if (
                payload_user_id != user_id
                or approval_status not in {"PENDING", "BLOCKED", "FAILED"}
                or not work_date
            ):
                continue
            try:
                date.fromisoformat(work_date[:10])
            except ValueError:
                continue
            targets.append(
                {
                    "map_id": int(row.map_id),
                    "work_date": work_date[:10],
                    "fieldcraft_user_account_id": payload.get("approvalFieldCraftUserAccountId")
                    or payload.get("dailyUserAccountId"),
                    # This value belongs to one mapping/project. The approval
                    # service aggregates all returned values into timezoneNames.
                    "timezone_name": self._clean(payload.get("approvalTimezone")) or "UTC",
                }
            )
        return targets

    def list_unresolved_approved_timelogs_through_date(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        user_account_id: int,
        approved_through: Any,
        timezone_name: str = "UTC",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return queued approved timelogs that are not safely synced yet.

        QuickBooks Time approval locks all user time through a date. Before
        moving ``approved_to``, the exporter checks every current mapping queued
        for this QuickBooks service. Historical timelogs that were never queued
        are outside the export contract and do not block approval.
        """
        try:
            if isinstance(approved_through, datetime):
                target_date = approved_through.date()
            elif isinstance(approved_through, date):
                target_date = approved_through
            else:
                target_date = date.fromisoformat(str(approved_through or "").strip()[:10])
        except (TypeError, ValueError) as exc:
            raise ValueError("approved_through must use YYYY-MM-DD format") from exc
        try:
            local_timezone = ZoneInfo(self._clean(timezone_name) or "UTC")
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Invalid approval timezone: {timezone_name!r}") from exc

        local_end = datetime.combine(
            target_date + timedelta(days=1),
            time.min,
            tzinfo=local_timezone,
        )
        utc_end = local_end.astimezone(timezone.utc)
        service_id_int = self._service_id_int(service_id)

        latest_map = (
            select(
                TimelogExternalMapModel.id.label("mapping_id"),
                TimelogExternalMapModel.timelog_id,
                TimelogExternalMapModel.sync_status,
                TimelogExternalMapModel.external_time_activity_id,
                TimelogExternalMapModel.external_time_timesheet_id,
                TimelogExternalMapModel.external_entity_type,
            )
            .where(
                TimelogExternalMapModel.tenant_id == tenant_id,
                TimelogExternalMapModel.service_id == service_id_int,
                self._is_current_mapping_clause(),
                (
                    TimelogExternalMapModel.endorsement_expiry_date.is_(None)
                    | (TimelogExternalMapModel.endorsement_expiry_date > func.now())
                ),
            )
            .distinct(TimelogExternalMapModel.timelog_id)
            .order_by(TimelogExternalMapModel.timelog_id, TimelogExternalMapModel.id.desc())
            .cte("approval_latest_timelog_map")
        )

        stmt = (
            select(
                TimelogModel.id.label("timelog_id"),
                TimelogModel.start_time,
                latest_map.c.mapping_id,
                latest_map.c.sync_status,
                latest_map.c.external_time_activity_id,
                latest_map.c.external_time_timesheet_id,
                latest_map.c.external_entity_type,
            )
            .select_from(TimelogModel)
            .join(
                ResourceScheduleModel,
                ResourceScheduleModel.id == TimelogModel.resource_schedule_id,
            )
            .join(ResourceModel, ResourceModel.id == ResourceScheduleModel.resource_id)
            .join(latest_map, latest_map.c.timelog_id == TimelogModel.id)
            .where(
                ResourceModel.user_id == int(user_account_id),
                func.coalesce(ResourceModel.invalid, False).is_(False),
                self._timelog_export_ready_clause(
                    require_active_schedule=not self._daily_schema_enabled()
                ),
                TimelogModel.start_time < utc_end,
                or_(
                    func.upper(func.trim(func.coalesce(latest_map.c.sync_status, "")))
                    != self.STATUS_SYNCED,
                    and_(
                        func.trim(
                            func.coalesce(
                                latest_map.c.external_time_timesheet_id, ""
                            )
                        )
                        == "",
                        func.trim(
                            func.coalesce(
                                latest_map.c.external_time_activity_id, ""
                            )
                        )
                        == "",
                    ),
                ),
            )
            .order_by(TimelogModel.start_time, TimelogModel.id)
            .limit(max(1, min(int(limit or 20), 100)))
        )
        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(stmt).all()
        return [dict(row._mapping) for row in rows]

    def update_quickbooks_time_approval_metadata(
        self,
        *,
        tenant_id: str,
        map_ids: List[int],
        approval_status: str,
        approved_through: str,
        error: Optional[str] = None,
    ) -> int:
        """Persist the QuickBooks Time approval audit state in mapping reasons.

        QuickBooks Time exposes approval on the user (``approved_to``), not on
        an individual timesheet. The existing JSON reason is therefore the
        correct audit location; no schema change is required.
        """
        clean_id_set = set()
        for value in map_ids:
            try:
                map_id = int(value or 0)
            except (TypeError, ValueError):
                continue
            if map_id > 0:
                clean_id_set.add(map_id)
        clean_ids = sorted(clean_id_set)
        if not clean_ids:
            return 0
        clean_status = self._clean(approval_status).upper() or "UNKNOWN"
        updated = 0
        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(
                select(TimelogExternalMapModel).where(
                    TimelogExternalMapModel.tenant_id == tenant_id,
                    TimelogExternalMapModel.id.in_(clean_ids),
                    self._is_current_mapping_clause(),
                )
            ).scalars().all()
            now = self._now()
            for row in rows:
                payload: Dict[str, Any]
                try:
                    parsed = json.loads(row.reason or "{}")
                    payload = dict(parsed) if isinstance(parsed, dict) else {}
                except (TypeError, ValueError):
                    payload = {"message": self._clean(row.reason)} if self._clean(row.reason) else {}
                payload.update(
                    {
                        "approvalTarget": "APPROVED",
                        "approvalStatus": clean_status,
                        "approvalTargetDate": self._clean(approved_through),
                        "approvalUpdatedAt": now.isoformat(),
                    }
                )
                if clean_status == "APPROVED":
                    payload["quickBooksTimeApprovedThrough"] = self._clean(approved_through)
                    payload["approvalVerifiedAt"] = now.isoformat()
                else:
                    payload.pop("quickBooksTimeApprovedThrough", None)
                    payload.pop("approvalVerifiedAt", None)
                if error:
                    payload["approvalError"] = self._clean(error)
                else:
                    payload.pop("approvalError", None)
                row.reason = json.dumps(payload, default=str, separators=(",", ":"))
                row.updated_at = now
                session.add(row)
                updated += 1
        return updated

    def mark_failed(self, *, tenant_id: str, map_id: int, status: str, reason: str) -> None:
        clean_status = self._clean(status).upper() or self.STATUS_FAILED
        if clean_status not in {self.STATUS_FAILED, self.STATUS_SYNC_FAILED, self.STATUS_INVALID, self.STATUS_PENDING_PUSH}:
            clean_status = self.STATUS_FAILED
        with tenant_session_scope(tenant_id) as session:
            row = session.get(TimelogExternalMapModel, int(map_id))
            if row is None:
                logger.warning("[TimelogRepo] mark_failed skipped; mapping not found | map_id=%s", map_id)
                return
            row.sync_status = clean_status
            row.reason = reason
            row.updated_at = self._now()
            session.add(row)

    def list_mapped_quickbooks_time_timesheets(
        self,
        *,
        tenant_id: str,
        service_id: Any,
    ) -> List[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(
                select(TimelogExternalMapModel).where(
                    TimelogExternalMapModel.tenant_id == tenant_id,
                    TimelogExternalMapModel.service_id == self._service_id_int(service_id),
                    or_(
                        TimelogExternalMapModel.external_time_timesheet_id.is_not(None),
                        TimelogExternalMapModel.external_time_activity_id.is_not(None),
                    ),
                    self._is_current_mapping_clause(),
                ).order_by(TimelogExternalMapModel.id)
            ).scalars().all()
            mappings = [self._mapping_to_dict(row) for row in rows]
        return [
            mapping
            for mapping in mappings
            if mapping and self.mapping_external_entity_type(mapping) == "TIMESHEET"
        ]

    def reset_mapped_quickbooks_time_timesheets(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        timesheet_ids: List[str],
        reason: str,
    ) -> int:
        clean_ids = {self._clean(value) for value in timesheet_ids if self._clean(value)}
        if not clean_ids:
            return 0
        updated = 0
        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(
                select(TimelogExternalMapModel).where(
                    TimelogExternalMapModel.tenant_id == tenant_id,
                    TimelogExternalMapModel.service_id == self._service_id_int(service_id),
                    or_(
                        TimelogExternalMapModel.external_time_timesheet_id.in_(
                            sorted(clean_ids)
                        ),
                        TimelogExternalMapModel.external_time_activity_id.in_(
                            sorted(clean_ids)
                        ),
                    ),
                    self._is_current_mapping_clause(),
                )
            ).scalars().all()
            for row in rows:
                mapping = self._mapping_to_dict(row) or {}
                if self.mapping_external_entity_type(mapping) != "TIMESHEET":
                    continue
                row.external_time_activity_id = None
                row.external_time_timesheet_id = None
                row.external_entity_type = "TIMESHEET"
                row.external_account_id = None
                row.sync_token = None
                row.sync_status = self.STATUS_PENDING_PUSH
                row.reason = reason
                row.updated_at = self._now()
                session.add(row)
                updated += 1
        return updated

    # Backward-compatible legacy cleanup helpers.
    def list_mapped_time_activities(
        self,
        *,
        tenant_id: str,
        service_id: Any,
    ) -> List[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(
                select(TimelogExternalMapModel).where(
                    TimelogExternalMapModel.tenant_id == tenant_id,
                    TimelogExternalMapModel.service_id == self._service_id_int(service_id),
                    TimelogExternalMapModel.external_time_activity_id.is_not(None),
                    self._is_current_mapping_clause(),
                ).order_by(TimelogExternalMapModel.id)
            ).scalars().all()
            mappings = [self._mapping_to_dict(row) for row in rows]
        return [
            mapping
            for mapping in mappings
            if mapping and self.mapping_external_entity_type(mapping) == "TIME_ACTIVITY"
        ]

    def reset_mapped_time_activities_for_reexport(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        external_time_activity_ids: List[str],
        reason: str,
    ) -> int:
        clean_ids = {self._clean(value) for value in external_time_activity_ids if self._clean(value)}
        if not clean_ids:
            return 0
        updated = 0
        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(
                select(TimelogExternalMapModel).where(
                    TimelogExternalMapModel.tenant_id == tenant_id,
                    TimelogExternalMapModel.service_id == self._service_id_int(service_id),
                    TimelogExternalMapModel.external_time_activity_id.in_(sorted(clean_ids)),
                    self._is_current_mapping_clause(),
                )
            ).scalars().all()
            for row in rows:
                mapping = self._mapping_to_dict(row) or {}
                if self.mapping_external_entity_type(mapping) != "TIME_ACTIVITY":
                    continue
                row.external_time_activity_id = None
                row.external_time_timesheet_id = None
                row.external_entity_type = "TIME_ACTIVITY"
                row.external_account_id = None
                row.sync_token = None
                row.sync_status = self.STATUS_PENDING_PUSH
                row.reason = reason
                row.updated_at = self._now()
                session.add(row)
                updated += 1
        return updated

