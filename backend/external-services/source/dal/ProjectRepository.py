from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import Text, cast, func, or_, select

from source.dal.db_session import tenant_session_scope
from source.dal.project_models import ProjectExternalMapModel, ProjectModel
from source.Utils import constants as const


logger = logging.getLogger(__name__)


class ProjectRepository:
    """Tenant project repository used by QuickBooks project/sub-customer sync flows.

    Business truth:
      - project
    Integration truth:
      - project_external_map

    Projects are exported from FIELDCRAFT to QuickBooks as sub-customers/jobs.
    Project import is intentionally not supported because FIELDCRAFT owns projects.
    """

    STATUS_PENDING_PUSH = "PENDING_PUSH"
    STATUS_SYNCED = "SYNCED"
    STATUS_FAILED = "FAILED"
    STATUS_SYNC_FAILED = "SYNC_FAILED"
    STATUS_INVALID = "INVALID"
    EXPORTABLE_SYNC_STATUSES = (STATUS_PENDING_PUSH, STATUS_FAILED, STATUS_SYNC_FAILED)

    ADDITIONAL_INFO_QUICKBOOKS_TIME = "quickbooksTime"
    ADDITIONAL_INFO_QUICKBOOKS_PROJECT = "quickbooksProject"
    ADDITIONAL_INFO_JOB_CODE_ID = "jobCodeId"
    ADDITIONAL_INFO_ASSIGNEE_ID = "assigneeId"
    EXPORT_CLAIM_KEY = "projectExportClaim"
    EXPORT_CLAIM_TTL_SECONDS = 300

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()

    def _service_id_int(self, service_id: Any) -> int:
        try:
            return int(service_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid service_id value: {service_id!r}") from exc

    def _safe_uuid_string(self, value: Any) -> Optional[str]:
        raw = self._clean(value)
        if not raw:
            return None
        try:
            return str(uuid.UUID(raw))
        except Exception:
            logger.debug("[ProjectRepo] _safe_uuid_string: not a valid UUID, returning raw | value=%r", raw, exc_info=True)
            return raw

    def _safe_uuid_obj(self, value: Any):
        raw = self._clean(value)
        if not raw:
            return None
        try:
            return uuid.UUID(raw)
        except Exception:
            logger.debug("[ProjectRepo] _safe_uuid_obj: invalid UUID, returning None | value=%r", raw, exc_info=True)
            return None

    def _parse_jsonish(self, value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return {}
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            except (TypeError, ValueError):
                logger.warning("[ProjectRepo] ignored invalid JSON in additional metadata")
                return {}
        return {}

    def _encode_additional_info(self, value: Optional[Dict[str, Any]]) -> Optional[str]:
        if not value:
            return None
        return json.dumps(value, separators=(",", ":"), sort_keys=True)

    def _merge_dicts(self, current: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(current)
        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._merge_dicts(merged[key], value)
            elif value is not None:
                merged[key] = value
        return merged

    def _merge_additional_info(self, current: Any, updates: Optional[Dict[str, Any]]) -> Optional[str]:
        if not updates:
            if isinstance(current, str):
                return current or None
            return self._encode_additional_info(self._parse_jsonish(current))
        merged = self._merge_dicts(self._parse_jsonish(current), updates)
        return self._encode_additional_info(merged)

    def get_time_jobcode_id(self, mapping: Optional[Dict[str, Any]]) -> Optional[str]:
        additional_info = self._parse_jsonish((mapping or {}).get("additional_info"))
        value = (additional_info.get(self.ADDITIONAL_INFO_QUICKBOOKS_TIME) or {}).get(
            self.ADDITIONAL_INFO_JOB_CODE_ID
        )
        return self._clean(value) or None

    def _get_mapping_for_update(self, session, mapping_id: int) -> Optional[ProjectExternalMapModel]:
        return session.execute(
            select(ProjectExternalMapModel)
            .where(ProjectExternalMapModel.id == int(mapping_id))
            .with_for_update()
        ).scalars().first()

    def _is_current_mapping_clause(self):
        return func.upper(func.coalesce(ProjectExternalMapModel.sync_status, "")) != self.STATUS_INVALID

    def _normalize_project_row(self, row: ProjectModel | Dict[str, Any] | None) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        if isinstance(row, dict):
            return {
                "id": int(row["id"]),
                "assigned_to": row.get("assigned_to"),
                "created_at": row.get("created_at"),
                "created_by": row.get("created_by"),
                "customer": self._parse_jsonish(row.get("customer")),
                "customer_asset": self._parse_jsonish(row.get("customer_asset")),
                "customer_uid": self._safe_uuid_string(row.get("customer_uid")),
                "description": row.get("description"),
                "invalid": bool(row.get("invalid")) if row.get("invalid") is not None else False,
                "location": row.get("location"),
                "order_no": row.get("order_no"),
                "project_uid": self._safe_uuid_string(row.get("project_uid")),
                "status": row.get("status"),
                "trade": row.get("trade"),
                "trade_type": row.get("trade_type"),
                "updated_at": row.get("updated_at"),
                "version": int(row["version"]) if row.get("version") is not None else None,
                "zipcode": row.get("zipcode"),
            }
        return {
            "id": int(row.id),
            "assigned_to": row.assigned_to,
            "created_at": row.created_at,
            "created_by": row.created_by,
            "customer": self._parse_jsonish(row.customer),
            "customer_asset": self._parse_jsonish(row.customer_asset),
            "customer_uid": self._safe_uuid_string(row.customer_uid),
            "description": row.description,
            "invalid": bool(row.invalid) if row.invalid is not None else False,
            "location": row.location,
            "order_no": row.order_no,
            "project_uid": self._safe_uuid_string(row.project_uid),
            "status": row.status,
            "trade": row.trade,
            "trade_type": row.trade_type,
            "updated_at": row.updated_at,
            "version": int(row.version) if row.version is not None else None,
            "zipcode": row.zipcode,
        }

    def _customer_name_from_project_snapshot(self, project: Dict[str, Any]) -> str:
        customer = project.get("customer") if isinstance(project, dict) else None
        if not isinstance(customer, dict):
            return ""
        first = self._clean(
            customer.get("firstName")
            or customer.get("firstname")
            or customer.get("first_name")
        )
        last = self._clean(
            customer.get("lastName")
            or customer.get("lastname")
            or customer.get("last_name")
        )
        display = self._clean(
            customer.get("displayName")
            or customer.get("display_name")
            or customer.get("name")
        )
        return " ".join(part for part in (first, last) if part).strip() or display

    def _project_display_name(
        self, project: Dict[str, Any], *, customer_name: Optional[str] = None
    ) -> str:
        # Bill already treats project.description as the user-facing project name.
        # Keep Invoice and Customer Payment consistent with that behavior and never
        # use project.location here because some tenants store geocoordinates in it.
        description = self._clean(project.get("description"))
        if description:
            return description

        customer_label = self._clean(customer_name) or self._customer_name_from_project_snapshot(project)
        trade = self._clean(project.get("trade")) or self._clean(project.get("trade_type"))
        order_no = self._clean(project.get("order_no"))
        project_part = "-".join(part for part in (trade, order_no) if part)

        if customer_label and project_part:
            return f"{customer_label}, {project_part}"
        return project_part or order_no or customer_label

    def get_project_display_names(
        self,
        *,
        tenant_id: str,
        project_ids: Iterable[Any],
        customer_names: Optional[Dict[str, str]] = None,
    ) -> Dict[int, str]:
        """Return user-facing project names for the requested tenant project IDs."""
        normalized: set[int] = set()
        for value in project_ids:
            if value is None:
                continue
            try:
                normalized.add(int(value))
            except (TypeError, ValueError):
                continue
        if not normalized:
            return {}

        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(
                select(ProjectModel).where(ProjectModel.id.in_(sorted(normalized)))
            ).scalars().all()
            projects = [self._normalize_project_row(row) for row in rows]

        customer_names = customer_names or {}
        result: Dict[int, str] = {}
        for project in projects:
            if not project:
                continue
            project_id = int(project["id"])
            customer_uid = self._clean(project.get("customer_uid"))
            label = self._project_display_name(
                project, customer_name=customer_names.get(customer_uid)
            )
            if label:
                result[project_id] = label
        return result

    def _mapping_to_dict(self, row: ProjectExternalMapModel) -> Dict[str, Any]:
        mapping = {
            "id": int(row.id),
            "project_id": int(row.project_id) if row.project_id is not None else None,
            "tenant_id": row.tenant_id,
            "service_id": int(row.service_id) if row.service_id is not None else None,
            "external_project_id": row.external_project_id,
            "external_account_id": row.external_account_id,
            "additional_info": self._parse_jsonish(row.additional_info),
            "sync_status": row.sync_status,
            "version": int(row.version or 1),
            "version_effective_date": row.version_effective_date,
            "version_expiry_date": row.version_expiry_date,
            "endorsement_effective_date": row.endorsement_effective_date,
            "endorsement_expiry_date": row.endorsement_expiry_date,
            "updated_at": row.updated_at,
            "project_uuid": str(row.project_uuid) if row.project_uuid is not None else None,
            "sync_token": row.sync_token,
            "reason": row.reason,
        }
        mapping["project_sync_target"] = self.get_mapping_sync_target(mapping)
        return mapping

    def normalize_project_sync_target(self, target: Any, default: Optional[str] = None) -> Optional[str]:
        normalized = self._clean(target).upper()
        if normalized in const.PROJECT_SYNC_TARGETS_ALLOWED:
            return normalized
        return default

    def get_mapping_sync_target(self, mapping: Dict[str, Any] | None) -> Optional[str]:
        """Return how the FIELDCRAFT project is represented in QuickBooks.

        The target is stored with the mapping record in its structured reason
        payload. This keeps one durable source of truth for downstream flows:
        bills use QBO_PROJECT mappings as ProjectRef and SUB_CUSTOMER mappings
        as CustomerRef. Legacy mapped rows are treated as SUB_CUSTOMER because
        that was the only supported target before real QuickBooks Projects.
        """
        if not mapping:
            return None

        direct_target = self.normalize_project_sync_target((mapping or {}).get(const.KEY_PROJECT_SYNC_TARGET))
        if direct_target:
            return direct_target

        reason = self._parse_jsonish((mapping or {}).get("reason"))
        reason_target = self.normalize_project_sync_target(
            reason.get(const.KEY_PROJECT_SYNC_TARGET) or reason.get("syncTarget")
        )
        if reason_target:
            return reason_target

        if self._clean((mapping or {}).get("external_project_id")):
            return const.PROJECT_SYNC_TARGET_SUB_CUSTOMER
        return None

    def _with_project_sync_target(self, reason: Optional[str], project_sync_target: Optional[str]) -> Optional[str]:
        target = self.normalize_project_sync_target(project_sync_target)
        if not target:
            return self._clean(reason) or None
        payload = self._parse_jsonish(reason)
        if not payload:
            payload = {"message": self._clean(reason)} if self._clean(reason) else {}
        payload[const.KEY_PROJECT_SYNC_TARGET] = target
        return json.dumps(payload)

    def _project_sync_projection(self, row: Dict[str, Any]) -> Dict[str, str]:
        asset = self._parse_jsonish(row.get("customer_asset"))
        return {
            "description": self._clean(row.get("description")),
            "order_no": self._clean(row.get("order_no")),
            "status": self._clean(row.get("status")),
            "customer_uid": self._clean(row.get("customer_uid")),
            "address": self._clean(asset.get("address")),
            "city": self._clean(asset.get("city")),
            "state": self._clean(asset.get("state")),
            "country": self._clean(asset.get("country")),
            "zipcode": self._clean(asset.get("zipCode") or row.get("zipcode")),
        }

    def sync_projection_changed(self, left: Dict[str, Any], right: Dict[str, Any]) -> bool:
        return self._project_sync_projection(left) != self._project_sync_projection(right)

    def get_project_latest_local_timestamp(
        self,
        project: Optional[Dict[str, Any]],
        mapping: Optional[Dict[str, Any]] = None,
    ) -> Optional[datetime]:
        del mapping

        # For QuickBooks conflict resolution, use the actual FIELDCRAFT project update
        # time only. Do not use project_external_map timestamps because sync
        # failures and retries update the map row without changing the project.
        value = (project or {}).get("updated_at")
        if value is None:
            value = (project or {}).get("created_at")
        if not isinstance(value, datetime):
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    def get_active_project_by_description(self, *, tenant_id: str, description: str) -> Optional[Dict[str, Any]]:
        description_value = self._clean(description)
        if not description_value:
            return None

        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(ProjectModel)
                .where(
                    func.coalesce(ProjectModel.invalid, False) == False,
                    ProjectModel.description == description_value,
                )
                .order_by(ProjectModel.updated_at.desc().nullslast(), ProjectModel.id.desc())
                .limit(2)
            )
            rows = session.execute(stmt).scalars().all()
            if not rows:
                return None
            if len(rows) > 1:
                raise ValueError(
                    f"Multiple active projects found with description={description_value!r}. "
                    "Project description must be unique for QuickBooks bill sync."
                )
            return self._normalize_project_row(rows[0])

    def get_project_order_no_by_id(self, *, tenant_id: str, project_id: int) -> Optional[str]:
        if project_id is None:
            return None

        with tenant_session_scope(tenant_id) as session:
            stmt = select(ProjectModel).where(
                ProjectModel.id == int(project_id),
                func.coalesce(ProjectModel.invalid, False) == False,
            )
            row = session.execute(stmt).scalars().first()
            if not row:
                return None
            order_no = self._clean(row.order_no)
            return order_no or None

    def get_project_by_id(self, *, tenant_id: str, project_id: int) -> Optional[Dict[str, Any]]:
        if project_id is None:
            return None
        with tenant_session_scope(tenant_id) as session:
            row = session.get(ProjectModel, int(project_id))
            return self._normalize_project_row(row) if row else None

    def get_active_project_by_id(self, *, tenant_id: str, project_id: int) -> Optional[Dict[str, Any]]:
        if project_id is None:
            return None
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(ProjectModel)
                .where(
                    ProjectModel.id == int(project_id),
                    func.coalesce(ProjectModel.invalid, False) == False,
                )
                .limit(1)
            )
            row = session.execute(stmt).scalars().first()
            return self._normalize_project_row(row)

    def get_export_candidate(self, *, tenant_id: str, service_id: str, project_id: str) -> Optional[Dict[str, Any]]:
        logger.debug("[ProjectRepo] get_export_candidate | tenant_id=%s service_id=%s project_id=%s", tenant_id, service_id, project_id)
        project_value = self._clean(project_id)
        if not project_value:
            logger.warning("[ProjectRepo] get_export_candidate: empty project_id, returning None")
            return None
        try:
            project_pk = int(project_value)
        except (TypeError, ValueError):
            logger.warning("[ProjectRepo] get_export_candidate: non-integer project_id=%r, returning None", project_value, exc_info=True)
            return None

        project = self.get_active_project_by_id(tenant_id=tenant_id, project_id=project_pk)
        if not project:
            logger.warning("[ProjectRepo] get_export_candidate: no active project found for project_id=%s", project_pk)
            return None
        mapping = self.get_current_mapping_by_project_id(tenant_id=tenant_id, service_id=service_id, project_id=project_pk)
        logger.debug("[ProjectRepo] get_export_candidate: found | project_id=%s has_mapping=%s mapping_status=%s", project_pk, mapping is not None, (mapping or {}).get("sync_status"))
        return {"project": project, "mapping": mapping}

    def get_current_mapping_by_project_id(self, *, tenant_id: str, service_id: Any, project_id: int) -> Optional[Dict[str, Any]]:
        if project_id is None:
            return None
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(ProjectExternalMapModel)
                .where(
                    ProjectExternalMapModel.project_id == int(project_id),
                    ProjectExternalMapModel.tenant_id == tenant_id,
                    ProjectExternalMapModel.service_id == self._service_id_int(service_id),
                    self._is_current_mapping_clause(),
                )
                .order_by(ProjectExternalMapModel.id.desc())
            )
            row = session.execute(stmt).scalars().first()
            result = self._mapping_to_dict(row) if row else None
            logger.debug("[ProjectRepo] get_current_mapping_by_project_id | project_id=%s found=%s mapping_id=%s sync_status=%s external_project_id=%s", project_id, result is not None, (result or {}).get("id"), (result or {}).get("sync_status"), (result or {}).get("external_project_id"))
            return result

    def get_current_mapping_by_external_project_id(self, *, tenant_id: str, service_id: Any, external_project_id: str) -> Optional[Dict[str, Any]]:
        external_value = self._clean(external_project_id)
        if not external_value:
            return None
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(ProjectExternalMapModel)
                .where(
                    ProjectExternalMapModel.external_project_id == external_value,
                    ProjectExternalMapModel.tenant_id == tenant_id,
                    ProjectExternalMapModel.service_id == self._service_id_int(service_id),
                    self._is_current_mapping_clause(),
                )
                .order_by(ProjectExternalMapModel.id.desc())
            )
            row = session.execute(stmt).scalars().first()
            result = self._mapping_to_dict(row) if row else None
            logger.debug("[ProjectRepo] get_current_mapping_by_external_project_id | external_project_id=%s found=%s mapping_id=%s project_id=%s sync_status=%s", external_value, result is not None, (result or {}).get("id"), (result or {}).get("project_id"), (result or {}).get("sync_status"))
            return result

    def get_by_external_id(self, tenant_id: str, service_id: Any, external_project_id: str) -> Optional[Dict[str, Any]]:
        return self.get_current_mapping_by_external_project_id(
            tenant_id=tenant_id,
            service_id=service_id,
            external_project_id=external_project_id,
        )

    def get_by_mapping_id(self, tenant_id: str, mapping_id: int) -> Optional[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            row = session.get(ProjectExternalMapModel, int(mapping_id))
            return self._mapping_to_dict(row) if row else None

    def get_mapping_by_id(
        self, *, tenant_id: str, service_id: Any, mapping_id: int
    ) -> Optional[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                select(ProjectExternalMapModel).where(
                    ProjectExternalMapModel.id == int(mapping_id),
                    ProjectExternalMapModel.tenant_id == tenant_id,
                    ProjectExternalMapModel.service_id == self._service_id_int(service_id),
                    self._is_current_mapping_clause(),
                )
            ).scalars().first()
            return self._mapping_to_dict(row) if row else None

    def _create_mapping(
        self,
        session,
        *,
        tenant_id: str,
        service_id: Any,
        project_id: Optional[int],
        project_uuid: Optional[Any],
        external_project_id: Optional[str],
        external_account_id: Optional[str],
        sync_status: str,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
        additional_info: Optional[Dict[str, Any]] = None,
        effective_at: Optional[datetime] = None,
        updated_at: Optional[datetime] = None,
        version: Optional[int] = None,
    ) -> ProjectExternalMapModel:
        now = updated_at or self._now()
        effective_ts = effective_at or now
        if effective_ts.tzinfo is None:
            effective_ts = effective_ts.replace(tzinfo=timezone.utc)
        row = ProjectExternalMapModel(
            project_id=int(project_id) if project_id is not None else None,
            tenant_id=tenant_id,
            service_id=self._service_id_int(service_id),
            external_project_id=self._clean(external_project_id) or None,
            external_account_id=self._clean(external_account_id) or None,
            sync_status=self._clean(sync_status).upper() or self.STATUS_PENDING_PUSH,
            version=int(version or 1),
            version_effective_date=None,
            version_expiry_date=None,
            endorsement_effective_date=effective_ts,
            endorsement_expiry_date=None,
            updated_at=now,
            project_uuid=self._safe_uuid_obj(project_uuid),
            sync_token=self._clean(sync_token) or None,
            reason=self._clean(reason) or None,
            additional_info=self._encode_additional_info(additional_info),
        )
        session.add(row)
        session.flush()
        return row

    def rotate_mapping_for_project(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        project_id: int,
        external_project_id: Optional[str],
        external_account_id: Optional[str],
        sync_status: str,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
        additional_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        logger.info("[ProjectRepo] rotate_mapping_for_project | project_id=%s sync_status=%s external_project_id=%s", project_id, sync_status, external_project_id)
        now = self._now()
        with tenant_session_scope(tenant_id) as session:
            project = session.get(ProjectModel, int(project_id))
            if not project:
                logger.warning("[ProjectRepo] rotate_mapping_for_project: project not found | project_id=%s", project_id)
                raise ValueError(f"Project {project_id} not found")

            existing_rows = session.execute(
                select(ProjectExternalMapModel).where(
                    ProjectExternalMapModel.project_id == int(project_id),
                    ProjectExternalMapModel.tenant_id == tenant_id,
                    ProjectExternalMapModel.service_id == self._service_id_int(service_id),
                    self._is_current_mapping_clause(),
                )
            ).scalars().all()
            logger.info("[ProjectRepo] rotate_mapping_for_project: expiring %s existing mapping(s) | project_id=%s", len(existing_rows), project_id)
            if additional_info is None and existing_rows:
                latest_existing = max(existing_rows, key=lambda item: int(item.id or 0))
                additional_info = self._parse_jsonish(latest_existing.additional_info)
            for existing in existing_rows:
                logger.debug("[ProjectRepo] rotate_mapping_for_project: expiring mapping_id=%s old_status=%s", existing.id, existing.sync_status)
                existing.sync_status = self.STATUS_INVALID
                existing.version_expiry_date = now
                existing.endorsement_expiry_date = now
                existing.updated_at = now
                session.add(existing)

            new_row = self._create_mapping(
                session,
                tenant_id=tenant_id,
                service_id=service_id,
                project_id=int(project_id),
                project_uuid=project.project_uid,
                external_project_id=external_project_id,
                external_account_id=external_account_id,
                sync_status=sync_status,
                sync_token=sync_token,
                reason=reason,
                additional_info=additional_info,
                effective_at=project.updated_at or project.created_at or now,
                updated_at=now,
                version=project.version or 1,
            )
            result = self._mapping_to_dict(new_row)
            logger.info("[ProjectRepo] rotate_mapping_for_project: new mapping created | mapping_id=%s sync_status=%s external_project_id=%s", result.get("id"), result.get("sync_status"), result.get("external_project_id"))
            return result

    def create_pending_push_mapping(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        project_id: int,
        external_project_id: Optional[str] = None,
        external_account_id: Optional[str] = None,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
        additional_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        logger.info("[ProjectRepo] create_pending_push_mapping | project_id=%s external_project_id=%s", project_id, external_project_id)
        existing = self.get_current_mapping_by_project_id(
            tenant_id=tenant_id,
            service_id=service_id,
            project_id=project_id,
        )
        if existing:
            if not external_project_id:
                external_project_id = existing.get("external_project_id")
            if external_account_id is None:
                external_account_id = existing.get("external_account_id")
            if sync_token is None:
                sync_token = existing.get("sync_token")
            if additional_info is None:
                additional_info = existing.get("additional_info")
        result = self.rotate_mapping_for_project(
            tenant_id=tenant_id,
            service_id=service_id,
            project_id=int(project_id),
            external_project_id=external_project_id,
            external_account_id=external_account_id,
            sync_status=self.STATUS_PENDING_PUSH,
            sync_token=sync_token,
            reason=reason,
            additional_info=additional_info,
        )
        logger.info("[ProjectRepo] create_pending_push_mapping: done | project_id=%s mapping_id=%s sync_status=%s", project_id, result.get("id"), result.get("sync_status"))
        return result

    def get_pending_push_mappings_for_tenant(self, *, tenant_id: str, service_id: Any, limit: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
        logger.debug("[ProjectRepo] get_pending_push_mappings_for_tenant | tenant_id=%s service_id=%s limit=%s offset=%s", tenant_id, service_id, limit, offset)
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(ProjectExternalMapModel)
                .where(
                    ProjectExternalMapModel.tenant_id == tenant_id,
                    ProjectExternalMapModel.service_id == self._service_id_int(service_id),
                    self._is_current_mapping_clause(),
                    func.upper(func.coalesce(ProjectExternalMapModel.sync_status, "")).in_(
                        self.EXPORTABLE_SYNC_STATUSES
                    ),
                )
                .order_by(ProjectExternalMapModel.id.asc())
                .offset(int(offset))
                .limit(int(limit))
            )
            rows = session.execute(stmt).scalars().all()
            results = [self._mapping_to_dict(row) for row in rows]
            logger.debug("[ProjectRepo] get_pending_push_mappings_for_tenant: found %s pending mapping(s)", len(results))
            return results

    def get_export_candidates(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        limit: int = 500,
        offset: int = 0,
        include_mapping_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        logger.debug("[ProjectRepo] get_export_candidates | tenant_id=%s service_id=%s limit=%s offset=%s", tenant_id, service_id, limit, offset)
        results: List[Dict[str, Any]] = []
        with tenant_session_scope(tenant_id) as session:
            stmt = select(ProjectExternalMapModel).where(
                ProjectExternalMapModel.tenant_id == tenant_id,
                ProjectExternalMapModel.service_id == self._service_id_int(service_id),
                self._is_current_mapping_clause(),
                func.upper(func.coalesce(ProjectExternalMapModel.sync_status, "")).in_(
                    self.EXPORTABLE_SYNC_STATUSES
                ),
            )
            included = sorted({
                int(value) for value in (include_mapping_ids or []) if value is not None
            })
            if included:
                stmt = stmt.where(ProjectExternalMapModel.id.in_(included))
            stmt = (
                stmt.order_by(ProjectExternalMapModel.id.asc())
                .offset(int(offset))
                .limit(int(limit))
            )
            rows = session.execute(stmt).scalars().all()
            project_ids: List[int] = []
            for mapping_row in rows:
                if mapping_row.project_id is None:
                    continue
                try:
                    project_ids.append(int(mapping_row.project_id))
                except (TypeError, ValueError):
                    logger.warning(
                        "[ProjectRepo] get_export_candidates: invalid project_id on mapping | mapping_id=%s project_id=%r",
                        getattr(mapping_row, "id", None),
                        mapping_row.project_id,
                    )

            project_map: Dict[int, ProjectModel] = {}
            if project_ids:
                unique_project_ids = list(dict.fromkeys(project_ids))
                project_rows = session.execute(
                    select(ProjectModel).where(ProjectModel.id.in_(unique_project_ids))
                ).scalars().all()
                project_map = {int(row.id): row for row in project_rows}

            for mapping_row in rows:
                if mapping_row.project_id is None:
                    continue
                try:
                    project_id = int(mapping_row.project_id)
                except (TypeError, ValueError):
                    continue

                project_row = project_map.get(project_id)
                if not project_row or bool(project_row.invalid):
                    continue
                entry = {
                    "project": self._normalize_project_row(project_row),
                    "mapping": self._mapping_to_dict(mapping_row),
                }
                logger.debug("[ProjectRepo] get_export_candidates: candidate | project_id=%s project_uid=%s mapping_id=%s sync_status=%s external_project_id=%s", entry["project"].get("id"), entry["project"].get("project_uid"), entry["mapping"].get("id"), entry["mapping"].get("sync_status"), entry["mapping"].get("external_project_id"))
                results.append(entry)
        logger.debug("[ProjectRepo] get_export_candidates: total candidates returned=%s", len(results))
        return results

    def _active_export_claim(self, additional_info: Dict[str, Any], now: datetime) -> Optional[Dict[str, Any]]:
        claim = (additional_info or {}).get(self.EXPORT_CLAIM_KEY)
        if not isinstance(claim, dict):
            return None
        claimed_at_raw = self._clean(claim.get("claimedAt"))
        if not claimed_at_raw:
            return None
        try:
            claimed_at = datetime.fromisoformat(claimed_at_raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if claimed_at.tzinfo is None:
            claimed_at = claimed_at.replace(tzinfo=timezone.utc)
        age_seconds = (now - claimed_at.astimezone(timezone.utc)).total_seconds()
        if 0 <= age_seconds < self.EXPORT_CLAIM_TTL_SECONDS:
            return dict(claim)
        return None

    def claim_export_mapping(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        mapping_id: int,
    ) -> Dict[str, Any]:
        """Claim one current project mapping before QuickBooks network I/O."""
        now = self._now()
        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                select(ProjectExternalMapModel)
                .where(
                    ProjectExternalMapModel.id == int(mapping_id),
                    ProjectExternalMapModel.tenant_id == tenant_id,
                    ProjectExternalMapModel.service_id == self._service_id_int(service_id),
                    self._is_current_mapping_clause(),
                )
                .with_for_update()
            ).scalars().first()
            if not row:
                raise ValueError(f"Project external mapping {mapping_id} was not found or is no longer current")

            mapping = self._mapping_to_dict(row)
            sync_status = self._clean(row.sync_status).upper()
            if sync_status == self.STATUS_SYNCED and self._clean(row.external_project_id):
                return {"claimed": False, "reason": "already_synced", "mapping": mapping}
            if sync_status not in self.EXPORTABLE_SYNC_STATUSES:
                return {"claimed": False, "reason": "not_eligible", "mapping": mapping}

            active_claim = self._active_export_claim(self._parse_jsonish(row.additional_info), now)
            if active_claim:
                return {"claimed": False, "reason": "in_progress", "mapping": mapping}

            additional_info = self._parse_jsonish(row.additional_info)
            additional_info[self.EXPORT_CLAIM_KEY] = {"claimedAt": now.isoformat()}
            row.additional_info = self._encode_additional_info(additional_info)
            row.updated_at = now
            session.add(row)
            return {
                "claimed": True,
                "reason": "claimed",
                "mapping": self._mapping_to_dict(row),
            }

    def mark_mapping_synced(
        self,
        *,
        tenant_id: str,
        mapping_id: int,
        external_project_id: Optional[str],
        external_account_id: Optional[str] = None,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
        project_sync_target: Optional[str] = None,
        additional_info: Optional[Dict[str, Any]] = None,
    ) -> None:
        logger.info("[ProjectRepo] mark_mapping_synced | mapping_id=%s external_project_id=%s", mapping_id, external_project_id)
        with tenant_session_scope(tenant_id) as session:
            row = self._get_mapping_for_update(session, mapping_id)
            if not row:
                logger.warning("[ProjectRepo] mark_mapping_synced: mapping row not found | mapping_id=%s", mapping_id)
                return
            if external_project_id is not None:
                row.external_project_id = self._clean(external_project_id) or row.external_project_id
            if external_account_id is not None:
                row.external_account_id = self._clean(external_account_id) or None
            if sync_token is not None:
                row.sync_token = self._clean(sync_token) or None
            row.sync_status = self.STATUS_SYNCED
            row.reason = self._with_project_sync_target(reason, project_sync_target)
            stored_additional_info = self._parse_jsonish(row.additional_info)
            stored_additional_info.pop(self.EXPORT_CLAIM_KEY, None)
            if additional_info:
                stored_additional_info = self._merge_dicts(stored_additional_info, additional_info)
            row.additional_info = self._encode_additional_info(stored_additional_info)
            row.updated_at = self._now()
            session.add(row)
            logger.info("[ProjectRepo] mark_mapping_synced: done | mapping_id=%s project_id=%s external_project_id=%s sync_status=%s", mapping_id, row.project_id, row.external_project_id, row.sync_status)

    def merge_mapping_additional_info(
        self,
        *,
        tenant_id: str,
        mapping_id: int,
        updates: Dict[str, Any],
    ) -> None:
        if not updates:
            return
        with tenant_session_scope(tenant_id) as session:
            row = self._get_mapping_for_update(session, mapping_id)
            if not row:
                raise ValueError(f"Project external mapping {mapping_id} not found")
            row.additional_info = self._merge_additional_info(row.additional_info, updates)
            row.updated_at = self._now()
            session.add(row)

    def set_time_jobcode_id(
        self,
        *,
        tenant_id: str,
        mapping_id: int,
        jobcode_id: str,
    ) -> None:
        jobcode_id = self._clean(jobcode_id)
        if not jobcode_id:
            raise ValueError("QuickBooks Time jobcode ID is required")
        self.merge_mapping_additional_info(
            tenant_id=tenant_id,
            mapping_id=mapping_id,
            updates={
                self.ADDITIONAL_INFO_QUICKBOOKS_TIME: {
                    self.ADDITIONAL_INFO_JOB_CODE_ID: jobcode_id
                }
            },
        )

    def get_synced_mappings_missing_time_jobcode(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        normalized_limit = max(int(limit or 500), 1)
        batch_size = min(max(normalized_limit, 100), 1000)
        offset = 0
        results: List[Dict[str, Any]] = []

        while len(results) < normalized_limit:
            with tenant_session_scope(tenant_id) as session:
                stmt = (
                    select(ProjectExternalMapModel, ProjectModel)
                    .join(ProjectModel, ProjectModel.id == ProjectExternalMapModel.project_id)
                    .where(
                        ProjectExternalMapModel.tenant_id == tenant_id,
                        ProjectExternalMapModel.service_id == self._service_id_int(service_id),
                        func.upper(func.coalesce(ProjectExternalMapModel.sync_status, "")) == self.STATUS_SYNCED,
                        self._is_current_mapping_clause(),
                        func.coalesce(ProjectModel.invalid, False) == False,
                    )
                    .order_by(ProjectExternalMapModel.id.asc())
                    .offset(offset)
                    .limit(batch_size)
                )
                rows = session.execute(stmt).all()
                page_rows: List[Dict[str, Any]] = []
                for mapping_row, project_row in rows:
                    mapping = self._mapping_to_dict(mapping_row)
                    if self.get_time_jobcode_id(mapping):
                        continue
                    project = self._normalize_project_row(project_row) or {}
                    page_rows.append({
                        "mapping_id": mapping.get("id"),
                        "project_id": project.get("id"),
                        "description": project.get("description"),
                        "order_no": project.get("order_no"),
                        "customer": project.get("customer") or {},
                        "external_project_id": mapping.get("external_project_id"),
                    })

            if not rows:
                break

            for row in page_rows:
                results.append(row)
                if len(results) >= normalized_limit:
                    break

            if len(rows) < batch_size:
                break
            offset += batch_size

        return results

    def mark_mapping_failed(
        self,
        *,
        tenant_id: str,
        mapping_id: int,
        status: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        logger.info("[ProjectRepo] mark_mapping_failed | mapping_id=%s status=%s reason=%s", mapping_id, status or self.STATUS_FAILED, reason)
        with tenant_session_scope(tenant_id) as session:
            row = session.get(ProjectExternalMapModel, int(mapping_id))
            if not row:
                logger.warning("[ProjectRepo] mark_mapping_failed: mapping row not found | mapping_id=%s", mapping_id)
                return
            row.sync_status = status or self.STATUS_FAILED
            row.reason = self._clean(reason) or None
            stored_additional_info = self._parse_jsonish(row.additional_info)
            stored_additional_info.pop(self.EXPORT_CLAIM_KEY, None)
            row.additional_info = self._encode_additional_info(stored_additional_info)
            row.updated_at = self._now()
            session.add(row)
            logger.info("[ProjectRepo] mark_mapping_failed: done | mapping_id=%s project_id=%s external_project_id=%s sync_status=%s", mapping_id, row.project_id, row.external_project_id, row.sync_status)

    def close_mapping_as_invalid(self, *, tenant_id: str, mapping_id: int) -> None:
        logger.info("[ProjectRepo] close_mapping_as_invalid | mapping_id=%s", mapping_id)
        with tenant_session_scope(tenant_id) as session:
            row = session.get(ProjectExternalMapModel, int(mapping_id))
            if not row:
                logger.warning("[ProjectRepo] close_mapping_as_invalid: mapping row not found | mapping_id=%s", mapping_id)
                return
            row.sync_status = self.STATUS_INVALID
            row.updated_at = self._now()
            row.version_expiry_date = self._now()
            row.endorsement_expiry_date = self._now()
            session.add(row)
            logger.info("[ProjectRepo] close_mapping_as_invalid: done | mapping_id=%s project_id=%s external_project_id=%s", mapping_id, row.project_id, row.external_project_id)

    def has_current_project_mappings(self, *, tenant_id: str, service_id: Any) -> bool:
        """Return True when this tenant already has active project mappings."""
        with tenant_session_scope(tenant_id) as session:
            count = session.execute(
                select(func.count())
                .select_from(ProjectExternalMapModel)
                .where(
                    ProjectExternalMapModel.tenant_id == tenant_id,
                    ProjectExternalMapModel.service_id == self._service_id_int(service_id),
                    ProjectExternalMapModel.project_id.is_not(None),
                    self._is_current_mapping_clause(),
                )
            ).scalar()
            return int(count or 0) > 0

    def get_project_cdc_page(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        page: int = 1,
        page_size: int = 25,
        search: Optional[str] = None,
        sync_status: Optional[str] = None,
        mapped_only: bool = False,
    ) -> Dict[str, Any]:
        """Paginated project list for QuickBooks project/sub-customer sync UI."""
        del mapped_only
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 25), 1), 100)
        offset = (page - 1) * page_size
        service_id_int = self._service_id_int(service_id)
        search_term = self._clean(search)
        normalized_status = self._clean(sync_status).upper()

        with tenant_session_scope(tenant_id) as session:
            max_eed_mapped = (
                select(
                    ProjectExternalMapModel.project_id.label("project_id"),
                    func.max(ProjectExternalMapModel.endorsement_effective_date).label("max_eed"),
                )
                .where(
                    ProjectExternalMapModel.tenant_id == tenant_id,
                    ProjectExternalMapModel.service_id == service_id_int,
                    ProjectExternalMapModel.project_id.is_not(None),
                    func.upper(func.coalesce(ProjectExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .group_by(ProjectExternalMapModel.project_id)
                .subquery()
            )
            latest_mapped_ids = (
                select(func.max(ProjectExternalMapModel.id).label("map_id"))
                .join(
                    max_eed_mapped,
                    (ProjectExternalMapModel.project_id == max_eed_mapped.c.project_id)
                    & (ProjectExternalMapModel.endorsement_effective_date == max_eed_mapped.c.max_eed),
                )
                .where(
                    ProjectExternalMapModel.tenant_id == tenant_id,
                    ProjectExternalMapModel.service_id == service_id_int,
                    ProjectExternalMapModel.project_id.is_not(None),
                    func.upper(func.coalesce(ProjectExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .group_by(ProjectExternalMapModel.project_id)
            )
            combined_ids = latest_mapped_ids.subquery()
            base_stmt = (
                select(ProjectExternalMapModel, ProjectModel)
                .where(ProjectExternalMapModel.id.in_(select(combined_ids.c.map_id)))
                .outerjoin(ProjectModel, ProjectModel.id == ProjectExternalMapModel.project_id)
            )

            if normalized_status and normalized_status != "ALL":
                base_stmt = base_stmt.where(func.upper(func.coalesce(ProjectExternalMapModel.sync_status, "")) == normalized_status)

            if search_term:
                like_term = f"%{search_term.lower()}%"
                base_stmt = base_stmt.where(
                    or_(
                        func.lower(func.coalesce(ProjectModel.description, "")).like(like_term),
                        func.lower(func.coalesce(ProjectModel.order_no, "")).like(like_term),
                        func.lower(func.coalesce(ProjectModel.status, "")).like(like_term),
                        func.lower(func.coalesce(cast(ProjectModel.project_uid, Text), "")).like(like_term),
                        func.lower(func.coalesce(cast(ProjectModel.customer_uid, Text), "")).like(like_term),
                        func.lower(func.coalesce(ProjectExternalMapModel.external_project_id, "")).like(like_term),
                    )
                )

            total_records = int(session.execute(select(func.count()).select_from(base_stmt.subquery())).scalar() or 0)
            paged_stmt = (
                base_stmt
                .order_by(ProjectExternalMapModel.updated_at.desc(), ProjectExternalMapModel.id.desc())
                .offset(offset)
                .limit(page_size)
            )
            records = session.execute(paged_stmt).all()

            rows: List[Dict[str, Any]] = []
            for mapping_row, project_row in records:
                mapping = self._mapping_to_dict(mapping_row)
                project = self._normalize_project_row(project_row) if project_row else {}
                rows.append({
                    "projectId": project.get("id"),
                    "projectUid": project.get("project_uid"),
                    "customerUid": project.get("customer_uid"),
                    "displayName": project.get("description") or project.get("order_no") or f"Project {project.get('id')}",
                    "description": project.get("description"),
                    "orderNo": project.get("order_no"),
                    "projectStatus": project.get("status"),
                    "mappingId": mapping.get("id"),
                    "externalProjectId": mapping.get("external_project_id"),
                    "externalAccountId": mapping.get("external_account_id"),
                    "additionalInfo": mapping.get("additional_info"),
                    "projectSyncTarget": mapping.get("project_sync_target"),
                    "syncStatus": mapping.get("sync_status"),
                    "syncToken": mapping.get("sync_token"),
                    "mappingVersion": mapping.get("version"),
                    "mappingUpdatedAt": mapping.get("updated_at"),
                    "reason": mapping.get("reason"),
                    "source": "FIELDCRAFT",
                })

            summary_stmt = (
                select(ProjectExternalMapModel.sync_status, func.count())
                .where(ProjectExternalMapModel.id.in_(select(combined_ids.c.map_id)))
                .group_by(ProjectExternalMapModel.sync_status)
            )
            raw_counts = session.execute(summary_stmt).all()
            status_counts: Dict[str, int] = {}
            for status, count in raw_counts:
                status_counts[self._clean(status).upper() or "UNKNOWN"] = int(count or 0)

        return {
            "records": rows,
            "page": page,
            "pageSize": page_size,
            "totalRecords": total_records,
            "totalPages": (total_records + page_size - 1) // page_size if page_size else 0,
            "summary": {
                "total": sum(status_counts.values()),
                "synced": status_counts.get(self.STATUS_SYNCED, 0),
                "pending": status_counts.get(self.STATUS_PENDING_PUSH, 0),
                "failed": status_counts.get(self.STATUS_FAILED, 0) + status_counts.get(self.STATUS_SYNC_FAILED, 0),
                "byStatus": status_counts,
            },
        }
