from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from sqlalchemy import MetaData, Table, Text, cast, desc, func, or_, select, text

from source.dal.db_session import (
    acquire_transaction_lock,
    acquire_transaction_locks,
    tenant_session_scope,
)
from source.dal.employee_models import UserAccountExternalMapModel, UserAccountModel
from source.Utils import constants as const


logger = logging.getLogger(__name__)


class EmployeeRepository:
    """Tenant employee repository used by QuickBooks employee export.

    Business truth:
      - user_account
    Integration truth:
      - user_accounts_external_map

    Only active, non-deleted, non-invalid user accounts with current pending
    mapping rows are exported to QuickBooks Employee records.
    """

    def __init__(self) -> None:
        self._resource_table_cache: Dict[int, Table] = {}
        self._personal_info_table_cache: Dict[int, Table] = {}
        self._user_status_table_cache: Dict[int, Table] = {}

    STATUS_PENDING_PUSH = "PENDING_PUSH"
    STATUS_SYNCED = "SYNCED"
    STATUS_FAILED = "FAILED"
    STATUS_SYNC_FAILED = "SYNC_FAILED"
    STATUS_IMPORT_FAILED = "IMPORT_FAILED"
    STATUS_INVALID = "INVALID"

    def _normalize_resource_type(self, value: Any) -> str:
        return " ".join(str(value or "").strip().split()).lower()

    def _worker_entity_type_for_resource(self, resource: Optional[Dict[str, Any]]) -> str:
        resource_type = self._normalize_resource_type((resource or {}).get("type"))
        contractor_types = {
            self._normalize_resource_type(const.EMPLOYEE_TYPE_1099),
            self._normalize_resource_type(getattr(const, "EMPLOYEE_TYPE_SUBCONTRACTOR", "Subcontractor")),
            self._normalize_resource_type(getattr(const, "EMPLOYEE_TYPE_CONTRACTOR", "Contractor")),
        }
        if resource_type in contractor_types:
            return const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR
        return const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE

    def _is_target_worker_entity_type(self, employee: Dict[str, Any], target_entity_type: Optional[str]) -> bool:
        if not target_entity_type:
            return True
        actual = self._worker_entity_type_for_resource(employee.get("resource") or {})
        return actual == self._clean(target_entity_type).upper()

    def _mapping_entity_type(self, mapping: Dict[str, Any]) -> str:
        entity_type = self._clean(mapping.get("external_entity_type")).upper()
        if entity_type in const.WORKER_EXTERNAL_ENTITY_TYPES_ALLOWED:
            return entity_type
        if self._clean(mapping.get("external_vendor_id")):
            return const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR
        return const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE

    def _worker_quickbooks_id(self, mapping: Dict[str, Any]) -> Optional[str]:
        """Return the QuickBooks id that the UI should display for a worker.

        W2 workers are represented as QuickBooks Employees and store their id in
        external_employee_id. 1099/contractor workers are represented as
        QuickBooks Vendors and store their id in external_vendor_id. The CDC
        response exposes this resolved value so the UI does not need to know the
        storage detail for each worker type.
        """
        external_employee_id = self._clean(mapping.get("external_employee_id"))
        external_vendor_id = self._clean(mapping.get("external_vendor_id"))
        entity_type = self._mapping_entity_type(mapping)
        if entity_type == const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR:
            return external_vendor_id or None
        return external_employee_id or None

    def _worker_type_label(self, employee: Optional[Dict[str, Any]], mapping: Optional[Dict[str, Any]]) -> str:
        """Return the display classification used by employee and timelog UIs."""
        mapping = mapping or {}
        entity_type = self._mapping_entity_type(mapping)
        if entity_type == const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR:
            return const.EMPLOYEE_TYPE_1099

        resource_type = self._clean(((employee or {}).get("resource") or {}).get("type"))
        if self._normalize_resource_type(resource_type) in {
            self._normalize_resource_type(const.EMPLOYEE_TYPE_1099),
            self._normalize_resource_type(getattr(const, "EMPLOYEE_TYPE_SUBCONTRACTOR", "Subcontractor")),
            self._normalize_resource_type(getattr(const, "EMPLOYEE_TYPE_CONTRACTOR", "Contractor")),
        }:
            return const.EMPLOYEE_TYPE_1099
        return const.EMPLOYEE_TYPE_W2

    def _worker_entity_type_for_candidate(
        self,
        employee: Dict[str, Any],
        mapping: Dict[str, Any],
    ) -> str:
        """Resolve worker routing without requiring optional enrichment data.

        An import-created worker may temporarily have no resource row if optional
        enrichment failed. The explicit external-map entity type is integration
        identity and must route that row back to the correct W2/1099 flow. Legacy
        rows without an explicit type continue to use the resource classification.
        """
        explicit = self._clean(mapping.get("external_entity_type")).upper()
        if explicit in const.WORKER_EXTERNAL_ENTITY_TYPES_ALLOWED:
            return explicit
        return self._worker_entity_type_for_resource(employee.get("resource") or {})

    def _mapping_allowed_for_worker_entity_type(self, mapping: Dict[str, Any], target_entity_type: Optional[str]) -> bool:
        if not target_entity_type:
            return True
        explicit_entity_type = self._clean(mapping.get("external_entity_type")).upper()
        if not explicit_entity_type:
            # Legacy mappings did not store the entity type. Allow the resource
            # classification to drive routing after migration/backfill.
            return True
        return explicit_entity_type == self._clean(target_entity_type).upper()


    def _log_debug(self, message: str, **data: Any) -> None:
        logger.debug("[EmployeeRepo] %s | %s", message, data or {})

    def _log_exception(self, message: str, exc: Exception) -> None:
        logger.exception("[EmployeeRepo] %s | error=%s", message, exc)

    def _parse_changed_since(self, value: Any) -> Optional[datetime]:
        raw = self._clean(value)
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("changedSince must be a valid ISO-8601 timestamp") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()

    def _clean_pai_value(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    def _normalize_personal_additional_info_row(self, row: Any) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        data = dict(row)
        return {
            "id": int(data["id"]) if data.get("id") is not None else None,
            "user_id": str(data.get("user_id")) if data.get("user_id") is not None else None,
            "address": data.get("address"),
            "city": data.get("city"),
            "state": data.get("state"),
            "country": data.get("country"),
            "zip_code": data.get("zip_code"),
            "email": data.get("email"),
            "first_name": data.get("first_name"),
            "last_name": data.get("last_name"),
            "hire_date": data.get("hire_date"),
            "updated_at": data.get("updated_at"),
        }

    def _reason_payload(self, reason: Any) -> Dict[str, Any]:
        if not reason:
            return {}
        try:
            parsed = json.loads(str(reason))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

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
            logger.debug("[EmployeeRepo] _safe_uuid_string: invalid UUID | value=%r", raw, exc_info=True)
            return raw

    def _safe_uuid_obj(self, value: Any):
        raw = self._clean(value)
        if not raw:
            return None
        try:
            return uuid.UUID(raw)
        except Exception:
            logger.debug("[EmployeeRepo] _safe_uuid_obj: invalid UUID | value=%r", raw, exc_info=True)
            return None

    def _is_current_mapping_clause(self):
        return func.upper(func.coalesce(UserAccountExternalMapModel.sync_status, "")) != self.STATUS_INVALID

    def _normalize_employee_row(self, row: UserAccountModel | None) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        return {
            "id": int(row.id),
            "active": bool(row.active) if row.active is not None else False,
            "alternate_mobile_no": row.alternate_mobile_no,
            "avatar": row.avatar,
            "birthdate": row.birthdate,
            "deleted": bool(row.deleted) if row.deleted is not None else False,
            "email": row.email,
            "eula_accepted": bool(row.eula_accepted) if row.eula_accepted is not None else False,
            "firstname": row.firstname,
            "gender": row.gender,
            "lastname": row.lastname,
            "middlename": row.middlename,
            "mobile_no": row.mobile_no,
            "object_id": str(row.object_id) if row.object_id is not None else None,
            "updated_at": row.updated_at,
            "invalid": bool(row.invalid) if row.invalid is not None else False,
            "schema_version": int(row.schema_version) if row.schema_version is not None else None,
            "version": int(row.version) if row.version is not None else None,
            "created_at": row.created_at,
            "status_id": int(row.status_id) if row.status_id is not None else None,
            "service_id": int(row.service_id) if row.service_id is not None else None,
        }

    def _mapping_to_dict(self, row: UserAccountExternalMapModel | None) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        return {
            "id": int(row.id),
            "user_account_id": int(row.user_account_id) if row.user_account_id is not None else None,
            "tenant_id": row.tenant_id,
            "service_id": int(row.service_id) if row.service_id is not None else None,
            "external_employee_id": row.external_employee_id,
            "external_vendor_id": row.external_vendor_id,
            "external_entity_type": row.external_entity_type,
            "external_account_id": row.external_account_id,
            "external_display_name": row.external_display_name,
            "external_time_user_id": row.external_time_user_id,
            "sync_status": row.sync_status,
            "version": int(row.version or 1),
            "version_effective_date": row.version_effective_date,
            "version_expiry_date": row.version_expiry_date,
            "endorsement_effective_date": row.endorsement_effective_date,
            "endorsement_expiry_date": row.endorsement_expiry_date,
            "updated_at": row.updated_at,
            "user_account_uuid": str(row.user_account_uuid) if row.user_account_uuid is not None else None,
            "sync_token": row.sync_token,
            "reason": row.reason,
        }

    def get_time_user_backfill_candidates(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Return current synced workers missing a QuickBooks Time user id.

        A worker may have historical mapping versions. Only the newest current
        mapping for each FIELDCRAFT user is returned so a stale row can never receive
        the Time user id. QuickBooks-created workers are eligible while their FIELDCRAFT
        account is still Invited/inactive; activation is unrelated to the external
        identity link required for time-entry synchronization.
        """
        service_id_int = self._service_id_int(service_id)
        safe_limit = max(1, min(int(limit or 500), 5000))
        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(
                select(UserAccountExternalMapModel, UserAccountModel)
                .join(
                    UserAccountModel,
                    UserAccountModel.id == UserAccountExternalMapModel.user_account_id,
                )
                .where(
                    UserAccountExternalMapModel.tenant_id == tenant_id,
                    UserAccountExternalMapModel.service_id == service_id_int,
                    func.upper(func.coalesce(UserAccountExternalMapModel.sync_status, ""))
                    == self.STATUS_SYNCED,
                    or_(
                        UserAccountExternalMapModel.endorsement_expiry_date.is_(None),
                        UserAccountExternalMapModel.endorsement_expiry_date > func.now(),
                    ),
                    func.trim(func.coalesce(UserAccountExternalMapModel.external_time_user_id, ""))
                    == "",
                    UserAccountExternalMapModel.user_account_id.is_not(None),
                    # Workers imported from QuickBooks are intentionally created in
                    # FIELDCRAFT with an Invited/inactive account until registration is
                    # completed. Their QuickBooks identity is already valid and must
                    # still receive a Time user ID, so account activation is not a
                    # prerequisite for this integration backfill.
                    UserAccountModel.deleted.is_not(True),
                    UserAccountModel.invalid.is_not(True),
                )
                .order_by(UserAccountExternalMapModel.id.desc())
                .limit(safe_limit * 3)
            ).all()

            # Convert ORM rows while the session is still open. Returning ORM
            # instances from this scope can raise DetachedInstanceError after
            # commit/close when SQLAlchemy expires their attributes.
            candidates: List[Dict[str, Any]] = []
            seen_user_ids = set()
            for mapping_row, employee_row in rows:
                user_account_id = int(mapping_row.user_account_id)
                if user_account_id in seen_user_ids:
                    continue
                seen_user_ids.add(user_account_id)
                mapping = self._mapping_to_dict(mapping_row) or {}
                employee = self._normalize_employee_row(employee_row) or {}
                candidates.append({"mapping": mapping, "employee": employee})
                if len(candidates) >= safe_limit:
                    break
            return candidates

    def set_time_user_id(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        mapping_id: int,
        time_user_id: str,
    ) -> None:
        """Persist a resolved QuickBooks Time user id on one current mapping."""
        resolved = self._clean(time_user_id)
        if not resolved:
            raise ValueError("QuickBooks Time user id is required")
        service_id_int = self._service_id_int(service_id)
        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                select(UserAccountExternalMapModel)
                .where(
                    UserAccountExternalMapModel.id == int(mapping_id),
                    UserAccountExternalMapModel.tenant_id == tenant_id,
                    UserAccountExternalMapModel.service_id == service_id_int,
                )
                .with_for_update()
            ).scalars().first()
            if row is None:
                raise ValueError(f"Employee mapping {mapping_id} was not found")
            if self._clean(row.sync_status).upper() != self.STATUS_SYNCED:
                raise ValueError(
                    f"Employee mapping {mapping_id} is not synced and cannot receive a Time user id"
                )
            if row.endorsement_expiry_date is not None and row.endorsement_expiry_date <= self._now():
                raise ValueError(
                    f"Employee mapping {mapping_id} is no longer current and cannot receive a Time user id"
                )

            duplicate = session.execute(
                select(UserAccountExternalMapModel.id)
                .where(
                    UserAccountExternalMapModel.tenant_id == tenant_id,
                    UserAccountExternalMapModel.service_id == service_id_int,
                    UserAccountExternalMapModel.id != int(mapping_id),
                    UserAccountExternalMapModel.external_time_user_id == resolved,
                    func.upper(func.coalesce(UserAccountExternalMapModel.sync_status, ""))
                    != self.STATUS_INVALID,
                    or_(
                        UserAccountExternalMapModel.endorsement_expiry_date.is_(None),
                        UserAccountExternalMapModel.endorsement_expiry_date > func.now(),
                    ),
                )
                .limit(1)
            ).scalar()
            if duplicate is not None:
                raise ValueError(
                    "This QuickBooks Time user is already linked to another active FIELDCRAFT worker mapping."
                )
            row.external_time_user_id = resolved
            row.updated_at = self._now()
            session.add(row)

    def _to_decimal(self, value: Any) -> Optional[Decimal]:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        raw = self._clean(value)
        if not raw or raw.lower() in ("nan", "none", "null"):
            return None
        try:
            return Decimal(raw.replace(",", ""))
        except (InvalidOperation, ValueError):
            return None

    def _money(self, value: Any, default: str = "0.00") -> Decimal:
        parsed = self._to_decimal(value)
        if parsed is None:
            parsed = Decimal(default)
        return parsed.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def _decimal_changed(self, left: Any, right: Any) -> bool:
        left_value = self._to_decimal(left)
        right_value = self._to_decimal(right)
        if left_value is None and right_value is None:
            return False
        if left_value is None or right_value is None:
            return True
        return self._money(left_value) != self._money(right_value)

    def _json_to_dict(self, value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        raw = self._clean(value)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _resource_table(self, session: Any) -> Table:
        bind = session.get_bind()
        cache_key = id(bind)
        table = self._resource_table_cache.get(cache_key)
        if table is None:
            metadata = MetaData()
            table = Table("resource", metadata, autoload_with=bind)
            self._resource_table_cache[cache_key] = table
        return table

    def _personal_info_table(self, session: Any) -> Table:
        bind = session.get_bind()
        cache_key = id(bind)
        table = self._personal_info_table_cache.get(cache_key)
        if table is None:
            metadata = MetaData()
            table = Table("personal_additional_info", metadata, autoload_with=bind)
            self._personal_info_table_cache[cache_key] = table
        return table

    def _user_status_table(self, session: Any) -> Table:
        bind = session.get_bind()
        cache_key = id(bind)
        table = self._user_status_table_cache.get(cache_key)
        if table is None:
            metadata = MetaData()
            table = Table("user_status", metadata, autoload_with=bind)
            self._user_status_table_cache[cache_key] = table
        return table

    def _resource_columns(self, resource_table: Table) -> set[str]:
        return set(resource_table.c.keys())

    def _normalize_resource_row(self, row: Any) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        data = dict(row)
        info = self._json_to_dict(data.get("additional_info"))
        return {
            "id": int(data["id"]) if data.get("id") is not None else None,
            "user_id": int(data["user_id"]) if data.get("user_id") is not None else None,
            "resource_uid": str(data.get("resource_uid")) if data.get("resource_uid") is not None else None,
            "name": data.get("name"),
            "cost": data.get("cost"),
            "unit": data.get("unit"),
            "type": data.get("type"),
            "status": data.get("status"),
            "additional_info": info,
            "updated_at": data.get("updated_at"),
            "version": int(data.get("version") or 0),
            "invalid": bool(data.get("invalid")) if data.get("invalid") is not None else False,
            "can_overallocate": data.get("can_overallocate"),
            "codes": data.get("codes"),
            "designation": data.get("designation"),
            "performance": data.get("performance"),
            "skills": data.get("skills"),
        }

    def _normalize_user_status_row(self, row: Any) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        data = dict(row)
        return {
            "id": int(data["id"]) if data.get("id") is not None else None,
            "user_id": int(data["user_id"]) if data.get("user_id") is not None else None,
            "hired_date": data.get("hired_date"),
            "effective_start_date": data.get("effective_start_date"),
            "effective_end_date": data.get("effective_end_date"),
            "terminated_date": data.get("terminated_date"),
            "type": data.get("type"),
            "updated_at": data.get("updated_at"),
            "version": int(data.get("version") or 0),
            "invalid": bool(data.get("invalid")) if data.get("invalid") is not None else False,
        }

    def _select_resource_by_user_id(
        self,
        session: Any,
        *,
        user_account_id: Any,
        include_invalid: bool = True,
    ) -> Optional[Dict[str, Any]]:
        rows_by_user_id = self._select_resources_by_user_ids(
            session,
            user_account_ids=[user_account_id],
            include_invalid=include_invalid,
        )
        return rows_by_user_id.get(int(user_account_id))

    def _select_resources_by_user_ids(
        self,
        session: Any,
        *,
        user_account_ids: List[Any],
        include_invalid: bool = True,
    ) -> Dict[int, Dict[str, Any]]:
        ids = list(dict.fromkeys(int(value) for value in user_account_ids if value is not None))
        if not ids:
            return {}

        resource_table = self._resource_table(session)
        stmt = select(resource_table).where(resource_table.c.user_id.in_(ids))
        if not include_invalid and "invalid" in resource_table.c:
            stmt = stmt.where(or_(resource_table.c.invalid.is_(None), resource_table.c.invalid.is_(False)))

        order_by = [resource_table.c.user_id.asc()]
        if "invalid" in resource_table.c:
            order_by.append(resource_table.c.invalid.asc().nullsfirst())
        if "id" in resource_table.c:
            order_by.append(desc(resource_table.c.id))
        stmt = stmt.order_by(*order_by)

        resources_by_user_id: Dict[int, Dict[str, Any]] = {}
        for row in session.execute(stmt).mappings().all():
            user_id = row.get("user_id")
            if user_id is None:
                continue
            user_id_int = int(user_id)
            if user_id_int not in resources_by_user_id:
                normalized = self._normalize_resource_row(row)
                if normalized:
                    resources_by_user_id[user_id_int] = normalized
        return resources_by_user_id

    def _select_user_statuses_by_user_ids(
        self,
        session: Any,
        *,
        user_account_ids: List[Any],
    ) -> Dict[int, Dict[str, Any]]:
        ids = list(dict.fromkeys(int(value) for value in user_account_ids if value is not None))
        if not ids:
            return {}

        table = self._user_status_table(session)
        if "user_id" not in table.c:
            return {}

        stmt = select(table).where(table.c.user_id.in_(ids))
        if "invalid" in table.c:
            stmt = stmt.where(or_(table.c.invalid.is_(None), table.c.invalid.is_(False)))

        order_by = [table.c.user_id.asc()]
        if "id" in table.c:
            order_by.append(desc(table.c.id))
        stmt = stmt.order_by(*order_by)

        statuses: Dict[int, Dict[str, Any]] = {}
        for row in session.execute(stmt).mappings().all():
            user_id = row.get("user_id")
            if user_id is None:
                continue
            user_id_int = int(user_id)
            if user_id_int in statuses:
                continue
            normalized = self._normalize_user_status_row(row)
            if normalized:
                statuses[user_id_int] = normalized
        return statuses

    def _select_personal_additional_info_by_user_ids(
        self,
        session: Any,
        *,
        user_account_uuids: List[Any],
    ) -> Dict[str, Dict[str, Any]]:
        user_ids: List[str] = []
        for value in user_account_uuids:
            user_id = self._clean_pai_value(value)
            if user_id and user_id not in user_ids:
                user_ids.append(user_id)
        if not user_ids:
            return {}

        personal_info_table = self._personal_info_table(session)
        user_id_expr = cast(personal_info_table.c.user_id, Text)
        selected_columns = [
            personal_info_table.c.id,
            personal_info_table.c.user_id,
            personal_info_table.c.address,
            personal_info_table.c.city,
            personal_info_table.c.state,
            personal_info_table.c.country,
            personal_info_table.c.zip_code,
            personal_info_table.c.email,
            personal_info_table.c.first_name,
            personal_info_table.c.last_name,
            personal_info_table.c.updated_at,
        ]
        if "hire_date" in personal_info_table.c:
            selected_columns.append(personal_info_table.c.hire_date)
        stmt = (
            select(*selected_columns)
            .where(user_id_expr.in_(user_ids))
            .order_by(user_id_expr.asc(), desc(personal_info_table.c.id))
        )

        info_by_user_id: Dict[str, Dict[str, Any]] = {}
        for row in session.execute(stmt).mappings().all():
            user_id = self._clean_pai_value(row.get("user_id"))
            if not user_id or user_id in info_by_user_id:
                continue
            normalized = self._normalize_personal_additional_info_row(row)
            if normalized:
                info_by_user_id[user_id] = normalized
        return info_by_user_id

    def _employee_resource_name(self, employee: Dict[str, Any]) -> str:
        parts = [
            self._clean(employee.get("firstname") or employee.get("first_name") or employee.get("firstName")),
            self._clean(employee.get("middlename") or employee.get("middle_name") or employee.get("middleName")),
            self._clean(employee.get("lastname") or employee.get("last_name") or employee.get("lastName")),
        ]
        name = " ".join(part for part in parts if part) or self._clean(employee.get("email")) or "Employee"
        return name[:64]

    def _resource_type(self, resource_payload: Dict[str, Any], existing_resource: Optional[Dict[str, Any]]) -> str:
        resource_type = self._clean(resource_payload.get("type"))
        if resource_type in const.EMPLOYEE_TYPES_ALLOWED:
            return resource_type
        existing_type = self._clean((existing_resource or {}).get("type"))
        if existing_type in const.EMPLOYEE_TYPES_ALLOWED:
            return existing_type
        return const.EMPLOYEE_TYPE_W2

    def _resource_unit(self, resource_payload: Dict[str, Any], existing_resource: Optional[Dict[str, Any]]) -> str:
        unit = self._clean(resource_payload.get("unit")).lower()
        if unit in const.EMPLOYEE_COST_UNITS_ALLOWED:
            return unit
        existing_unit = self._clean((existing_resource or {}).get("unit")).lower()
        if existing_unit in const.EMPLOYEE_COST_UNITS_ALLOWED:
            return existing_unit
        return const.EMPLOYEE_COST_UNIT_HOURLY

    def get_resource_by_user_account_id(self, tenant_id: str, user_account_id: Any) -> Optional[Dict[str, Any]]:
        if user_account_id is None:
            return None
        try:
            with tenant_session_scope(tenant_id) as session:
                return self._select_resource_by_user_id(
                    session,
                    user_account_id=user_account_id,
                    include_invalid=True,
                )
        except Exception as exc:
            self._log_exception("get_resource_by_user_account_id failed", exc)
            raise

    def _resource_additional_info(
        self,
        *,
        existing_resource: Optional[Dict[str, Any]],
        resource_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        info = self._json_to_dict((existing_resource or {}).get("additional_info"))
        cost_present = bool(resource_payload.get("cost_present"))
        overtime_present = bool(resource_payload.get("overtime_cost_present"))

        if overtime_present:
            overtime = self._to_decimal(resource_payload.get("overtime_cost"))
            if overtime is not None:
                info["overTimeCost"] = str(self._money(overtime))

        source = self._clean(resource_payload.get("source"))
        if source and (cost_present or overtime_present):
            info["costSource"] = source

        qbo_field = self._clean(resource_payload.get("qbo_field"))
        if qbo_field and cost_present:
            info["quickBooksCostField"] = qbo_field

        if "qbo_billable_time" in resource_payload and resource_payload.get("qbo_billable_time") is not None:
            info["qboBillableTime"] = resource_payload.get("qbo_billable_time")
        if resource_payload.get("qbo_cost_rate") is not None:
            info["qboCostRate"] = resource_payload.get("qbo_cost_rate")
        if resource_payload.get("qbo_bill_rate") is not None:
            info["qboBillRate"] = resource_payload.get("qbo_bill_rate")
        return info

    def _existing_resource_value(self, existing_resource: Optional[Dict[str, Any]], key: str, default: Any) -> Any:
        if not existing_resource:
            return default
        value = existing_resource.get(key)
        if value is None:
            return default
        if isinstance(value, str) and not value.strip():
            return default
        return value

    def _resource_insert_values(
        self,
        *,
        employee: Dict[str, Any],
        existing_resource: Optional[Dict[str, Any]],
        resource_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        employee_id = int(employee.get("id"))
        has_cost = bool(resource_payload.get("cost_present"))
        cost = self._money(resource_payload.get("cost") if has_cost else self._existing_resource_value(existing_resource, "cost", "0.00"))
        unit = self._resource_unit(resource_payload if has_cost else {}, existing_resource)
        resource_type = self._resource_type(resource_payload, existing_resource)
        info = self._resource_additional_info(existing_resource=existing_resource, resource_payload=resource_payload)
        now = self._now()
        resource_uid = (
            self._safe_uuid_obj(employee.get("object_id"))
            or self._safe_uuid_obj((existing_resource or {}).get("resource_uid"))
            or uuid.uuid4()
        )

        return {
            "additional_info": json.dumps(info) if info else None,
            "can_overallocate": self._existing_resource_value(existing_resource, "can_overallocate", True),
            "codes": self._existing_resource_value(existing_resource, "codes", ""),
            "cost": cost,
            "created_at": self._existing_resource_value(existing_resource, "created_at", now),
            "designation": self._existing_resource_value(existing_resource, "designation", self._clean(employee.get("roles")) or "General Employee"),
            "name": self._employee_resource_name(employee),
            "performance": self._existing_resource_value(existing_resource, "performance", "0"),
            "resource_uid": resource_uid,
            "skills": self._existing_resource_value(existing_resource, "skills", ""),
            "status": "Active" if employee.get("active", True) else "Inactive",
            "unit": unit,
            "updated_at": now,
            "version": int((existing_resource or {}).get("version") or 0),
            "user_id": employee_id,
            "type": resource_type,
            "invalid": False,
        }

    def _upsert_resource_with_session(
        self,
        session: Any,
        *,
        employee: Dict[str, Any],
        resource_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        employee_id = int(employee.get("id"))
        resource_table = self._resource_table(session)
        columns = self._resource_columns(resource_table)
        existing_resource = self._select_resource_by_user_id(
            session,
            user_account_id=employee_id,
            include_invalid=True,
        )

        values = self._resource_insert_values(
            employee=employee,
            existing_resource=existing_resource,
            resource_payload=resource_payload or {},
        )
        self._log_debug(
            "resource upsert prepared",
            employee_id=employee_id,
            existing_resource_id=(existing_resource or {}).get("id"),
            existing_cost=str((existing_resource or {}).get("cost")) if existing_resource else None,
            incoming_cost_present=bool((resource_payload or {}).get("cost_present")),
            incoming_cost=(resource_payload or {}).get("cost"),
            incoming_qbo_field=(resource_payload or {}).get("qbo_field"),
            final_cost=str(values.get("cost")),
            final_unit=values.get("unit"),
            final_type=values.get("type"),
        )

        if existing_resource and existing_resource.get("id"):
            values["version"] = int((existing_resource or {}).get("version") or 0) + 1
            update_values = {
                key: value for key, value in values.items()
                if key in columns and key not in {"id", "created_at", "user_id"}
            }
            if update_values:
                session.execute(
                    resource_table.update()
                    .where(resource_table.c.id == int(existing_resource["id"]))
                    .values(**update_values)
                )
            resource_id = int(existing_resource["id"])
            self._log_debug("resource row updated", employee_id=employee_id, resource_id=resource_id)
        else:
            insert_values = {key: value for key, value in values.items() if key in columns and key != "id"}
            row = session.execute(
                resource_table.insert()
                .values(**insert_values)
                .returning(resource_table.c.id)
            ).mappings().first()
            resource_id = int(row["id"]) if row and row.get("id") is not None else 0
            self._log_debug("resource row inserted", employee_id=employee_id, resource_id=resource_id)

        refreshed = session.execute(
            select(resource_table).where(resource_table.c.id == resource_id).limit(1)
        ).mappings().first()
        normalized = self._normalize_resource_row(refreshed) or {"id": resource_id}
        self._log_debug(
            "resource upsert completed",
            employee_id=employee_id,
            resource_id=normalized.get("id"),
            cost=str(normalized.get("cost")),
            unit=normalized.get("unit"),
            additional_info=normalized.get("additional_info"),
        )
        return normalized

    def upsert_resource_for_employee(
        self,
        *,
        tenant_id: str,
        employee: Dict[str, Any],
        resource_payload: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not employee or employee.get("id") is None:
            raise ValueError("employee.id is required to upsert resource cost")
        try:
            with tenant_session_scope(tenant_id) as session:
                return self._upsert_resource_with_session(
                    session,
                    employee=employee,
                    resource_payload=resource_payload or {},
                )
        except Exception as exc:
            self._log_exception("upsert_resource_for_employee failed", exc)
            raise

    def get_current_mapping_by_user_account_id(self, *, tenant_id: str, service_id: Any, user_account_id: int) -> Optional[Dict[str, Any]]:
        self._log_debug(
            "get_current_mapping_by_user_account_id started",
            tenant_id=tenant_id,
            service_id=service_id,
            user_account_id=user_account_id,
        )
        try:
            with tenant_session_scope(tenant_id) as session:
                stmt = (
                    select(UserAccountExternalMapModel)
                    .where(
                        UserAccountExternalMapModel.user_account_id == int(user_account_id),
                        UserAccountExternalMapModel.tenant_id == tenant_id,
                        UserAccountExternalMapModel.service_id == self._service_id_int(service_id),
                        self._is_current_mapping_clause(),
                    )
                    .order_by(UserAccountExternalMapModel.id.desc())
                )
                row = session.execute(stmt).scalars().first()
                result = self._mapping_to_dict(row) if row else None
                self._log_debug(
                    "get_current_mapping_by_user_account_id completed",
                    user_account_id=user_account_id,
                    found=bool(result),
                    mapping_id=(result or {}).get("id"),
                )
                return result
        except Exception as exc:
            self._log_exception("get_current_mapping_by_user_account_id failed", exc)
            raise

    def repair_pending_mapping_identity(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        mapping_id: int,
        user_account_id: int,
        expected_entity_type: str,
    ) -> Dict[str, Any]:
        """Carry a worker's existing QuickBooks identity into a new pending row.

        The Tenant application creates a new external-map version when a worker is
        updated. Older application versions did not copy the QuickBooks IDs into
        that row, which made the exporter treat an update as a brand-new worker.
        This method repairs the pending row under a transaction lock and blocks
        W2/1099 reclassification after the worker has been linked.
        """
        entity_type = self._clean(expected_entity_type).upper()
        if entity_type not in const.WORKER_EXTERNAL_ENTITY_TYPES_ALLOWED:
            raise ValueError("A valid QuickBooks worker entity type is required.")
        service_id_int = self._service_id_int(service_id)
        user_account_id_int = int(user_account_id)

        with tenant_session_scope(tenant_id) as session:
            acquire_transaction_lock(
                session,
                f"qbo-worker-user:{tenant_id}:{service_id_int}:{user_account_id_int}",
            )
            target = session.get(UserAccountExternalMapModel, int(mapping_id))
            if not target:
                raise ValueError(f"Employee mapping {mapping_id} was not found.")
            if (
                target.tenant_id != tenant_id
                or int(target.service_id or 0) != service_id_int
            ):
                raise ValueError("The employee mapping belongs to a different tenant or service.")
            if target.user_account_id is None or int(target.user_account_id) != user_account_id_int:
                raise ValueError("The employee mapping belongs to another FIELDCRAFT employee.")

            other_rows = list(
                session.execute(
                    select(UserAccountExternalMapModel)
                    .where(
                        UserAccountExternalMapModel.tenant_id == tenant_id,
                        UserAccountExternalMapModel.service_id == service_id_int,
                        UserAccountExternalMapModel.user_account_id == user_account_id_int,
                        UserAccountExternalMapModel.id != int(mapping_id),
                    )
                    .order_by(UserAccountExternalMapModel.id.desc())
                    .with_for_update()
                ).scalars().all()
            )
            linked_rows = [
                row
                for row in other_rows
                if self._clean(row.external_employee_id) or self._clean(row.external_vendor_id)
            ]
            same_type_rows = [
                row
                for row in linked_rows
                if self._mapping_entity_type(self._mapping_to_dict(row) or {}) == entity_type
            ]
            opposite_rows = [row for row in linked_rows if row not in same_type_rows]
            if opposite_rows:
                previous = self._mapping_entity_type(self._mapping_to_dict(opposite_rows[0]) or {})
                previous_label = (
                    "1099 Vendor"
                    if previous == const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR
                    else "W-2 Employee"
                )
                requested_label = (
                    "1099 Vendor"
                    if entity_type == const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR
                    else "W-2 Employee"
                )
                raise ValueError(
                    f"This worker is already linked in QuickBooks as a {previous_label}. "
                    f"The worker type cannot be changed to {requested_label} after synchronization."
                )

            def linked_external_id(row: UserAccountExternalMapModel) -> str:
                value = (
                    row.external_vendor_id
                    if entity_type == const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR
                    else row.external_employee_id
                )
                return self._clean(value)

            distinct_ids = {
                linked_external_id(row)
                for row in same_type_rows
                if linked_external_id(row)
            }
            if len(distinct_ids) > 1:
                raise ValueError(
                    "Multiple historical QuickBooks worker mappings exist for this FIELDCRAFT employee. "
                    "Resolve the duplicate mappings before syncing again."
                )

            source = same_type_rows[0] if same_type_rows else None
            if source:
                if entity_type == const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR:
                    target.external_vendor_id = self._clean(source.external_vendor_id) or None
                    target.external_employee_id = None
                else:
                    target.external_employee_id = self._clean(source.external_employee_id) or None
                    target.external_vendor_id = None
                target.external_entity_type = entity_type
                if not self._clean(target.external_account_id):
                    target.external_account_id = self._clean(source.external_account_id) or None
                if not self._clean(target.external_display_name):
                    target.external_display_name = self._clean(source.external_display_name) or None
                if not self._clean(target.external_time_user_id):
                    target.external_time_user_id = self._clean(source.external_time_user_id) or None
                if not self._clean(target.sync_token):
                    target.sync_token = self._clean(source.sync_token) or None
                now = self._now()
                target.updated_at = now
                session.add(target)
                for row in other_rows:
                    if self._clean(row.sync_status).upper() == self.STATUS_INVALID:
                        continue
                    row_entity_type = self._mapping_entity_type(self._mapping_to_dict(row) or {})
                    if row_entity_type == entity_type:
                        self._invalidate_mapping_row(row, now)
                        session.add(row)
                session.flush()

            return self._mapping_to_dict(target) or {}

    def mark_unbound_import_mapping_failed(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        user_account_id: int,
        expected_entity_type: str,
        reason: str,
    ) -> Optional[Dict[str, Any]]:
        """Quarantine an import-created worker whose QBO mapping was not claimed.

        A newly imported worker starts with a local ``PENDING_PUSH`` row. If the
        external-ID claim fails, leaving that row pending would make the next
        export create a duplicate QuickBooks worker. Only an unbound, claimable
        row is changed to ``IMPORT_FAILED``; existing linked identities are never
        modified here.
        """
        entity_type = self._clean(expected_entity_type).upper()
        if entity_type not in const.WORKER_EXTERNAL_ENTITY_TYPES_ALLOWED:
            raise ValueError("A valid QuickBooks worker entity type is required.")
        service_id_int = self._service_id_int(service_id)
        user_account_id_int = int(user_account_id)
        claimable_statuses = {
            "",
            self.STATUS_PENDING_PUSH,
            self.STATUS_FAILED,
            self.STATUS_SYNC_FAILED,
            self.STATUS_IMPORT_FAILED,
        }

        with tenant_session_scope(tenant_id) as session:
            acquire_transaction_lock(
                session,
                f"qbo-worker-user:{tenant_id}:{service_id_int}:{user_account_id_int}",
            )
            row = session.execute(
                select(UserAccountExternalMapModel)
                .where(
                    UserAccountExternalMapModel.tenant_id == tenant_id,
                    UserAccountExternalMapModel.service_id == service_id_int,
                    UserAccountExternalMapModel.user_account_id == user_account_id_int,
                    self._is_current_mapping_clause(),
                )
                .order_by(UserAccountExternalMapModel.id.desc())
                .with_for_update()
            ).scalars().first()
            if not row:
                return None
            if self._clean(row.external_employee_id) or self._clean(row.external_vendor_id):
                return self._mapping_to_dict(row)
            if self._clean(row.sync_status).upper() not in claimable_statuses:
                return self._mapping_to_dict(row)

            row.external_entity_type = entity_type
            row.sync_status = self.STATUS_IMPORT_FAILED
            row.reason = self._clean(reason) or None
            row.updated_at = self._now()
            session.add(row)
            session.flush()
            return self._mapping_to_dict(row)

    def get_by_external_id(
        self,
        tenant_id: str,
        service_id: Any,
        external_employee_id: str,
        external_account_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        external_value = self._clean(external_employee_id)
        self._log_debug(
            "get_by_external_id started",
            tenant_id=tenant_id,
            service_id=service_id,
            external_employee_id=external_value,
        )
        if not external_value:
            return None
        try:
            with tenant_session_scope(tenant_id) as session:
                stmt = select(UserAccountExternalMapModel).where(
                    UserAccountExternalMapModel.external_employee_id == external_value,
                    UserAccountExternalMapModel.tenant_id == tenant_id,
                    UserAccountExternalMapModel.service_id == self._service_id_int(service_id),
                    self._is_current_mapping_clause(),
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
                row = session.execute(stmt).scalars().first()
                result = self._mapping_to_dict(row) if row else None
                self._log_debug(
                    "get_by_external_id completed",
                    external_employee_id=external_value,
                    found=bool(result),
                    mapping_id=(result or {}).get("id"),
                    user_account_id=(result or {}).get("user_account_id"),
                )
                return result
        except Exception as exc:
            self._log_exception("get_by_external_id failed", exc)
            raise

    def get_by_external_vendor_id(
        self,
        tenant_id: str,
        service_id: Any,
        external_vendor_id: str,
        external_account_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        external_value = self._clean(external_vendor_id)
        self._log_debug(
            "get_by_external_vendor_id started",
            tenant_id=tenant_id,
            service_id=service_id,
            external_vendor_id=external_value,
        )
        if not external_value:
            return None
        try:
            with tenant_session_scope(tenant_id) as session:
                stmt = select(UserAccountExternalMapModel).where(
                    UserAccountExternalMapModel.external_vendor_id == external_value,
                    UserAccountExternalMapModel.tenant_id == tenant_id,
                    UserAccountExternalMapModel.service_id == self._service_id_int(service_id),
                    self._is_current_mapping_clause(),
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
                row = session.execute(stmt).scalars().first()
                result = self._mapping_to_dict(row) if row else None
                self._log_debug(
                    "get_by_external_vendor_id completed",
                    external_vendor_id=external_value,
                    found=bool(result),
                    mapping_id=(result or {}).get("id"),
                    user_account_id=(result or {}).get("user_account_id"),
                )
                return result
        except Exception as exc:
            self._log_exception("get_by_external_vendor_id failed", exc)
            raise

    def get_current_worker_external_vendor_ids(
        self,
        *,
        tenant_id: str,
        service_id: Any,
    ) -> set[str]:
        """Return QuickBooks Vendor IDs currently linked to FIELDCRAFT 1099 workers."""
        service_id_int = self._service_id_int(service_id)
        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(
                select(UserAccountExternalMapModel.external_vendor_id).where(
                    UserAccountExternalMapModel.tenant_id == tenant_id,
                    UserAccountExternalMapModel.service_id == service_id_int,
                    self._is_current_mapping_clause(),
                    func.trim(
                        func.coalesce(UserAccountExternalMapModel.external_vendor_id, "")
                    )
                    != "",
                )
            ).scalars().all()
        return {self._clean(value) for value in rows if self._clean(value)}

    def has_current_mapping_for_user_with_different_external_id(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        user_account_id: int,
        external_employee_id: str,
    ) -> bool:
        external_value = self._clean(external_employee_id)
        try:
            with tenant_session_scope(tenant_id) as session:
                stmt = (
                    select(UserAccountExternalMapModel.id)
                    .where(
                        UserAccountExternalMapModel.tenant_id == tenant_id,
                        UserAccountExternalMapModel.service_id == self._service_id_int(service_id),
                        UserAccountExternalMapModel.user_account_id == int(user_account_id),
                        func.upper(func.coalesce(UserAccountExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                    )
                    .limit(1)
                )
                if external_value:
                    stmt = stmt.where(UserAccountExternalMapModel.external_employee_id != external_value)
                return session.execute(stmt).first() is not None
        except Exception as exc:
            self._log_exception("has_current_mapping_for_user_with_different_external_id failed", exc)
            raise

    def get_export_candidates(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        limit: int = 500,
        offset: int = 0,
        worker_entity_type: Optional[str] = const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE,
        after_mapping_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return a correctly paged worker-specific export queue.

        Mapping rows are scanned in stable ID order, but offset/limit are applied
        only after worker classification. This prevents a page dominated by W2
        mappings from hiding later 1099 contractors, and vice versa.
        """
        safe_limit = max(1, min(int(limit or 500), const.QBO_EXPORT_LIMIT_MAX))
        safe_offset = max(0, int(offset or 0))
        target_type = self._clean(worker_entity_type).upper() or None
        scan_size = max(500, min(2000, safe_limit * 2))
        selected: List[Dict[str, Any]] = []
        matched_before_page = 0
        scan_offset = 0

        self._log_debug(
            "get_export_candidates started",
            tenant_id=tenant_id,
            service_id=service_id,
            limit=safe_limit,
            offset=safe_offset,
            worker_entity_type=target_type,
        )
        try:
            with tenant_session_scope(tenant_id) as session:
                while len(selected) < safe_limit:
                    mapping_stmt = select(UserAccountExternalMapModel).where(
                        UserAccountExternalMapModel.tenant_id == tenant_id,
                        UserAccountExternalMapModel.service_id == self._service_id_int(service_id),
                        func.upper(func.coalesce(UserAccountExternalMapModel.sync_status, "")).in_(
                            [self.STATUS_PENDING_PUSH, self.STATUS_FAILED, self.STATUS_SYNC_FAILED]
                        ),
                    )
                    if after_mapping_id is not None:
                        mapping_stmt = mapping_stmt.where(
                            UserAccountExternalMapModel.id > int(after_mapping_id)
                        )
                    mapping_rows = session.execute(
                        mapping_stmt
                        .order_by(UserAccountExternalMapModel.id.asc())
                        .offset(scan_offset)
                        .limit(scan_size)
                    ).scalars().all()
                    if not mapping_rows:
                        break
                    scan_offset += len(mapping_rows)

                    user_ids = sorted({
                        int(row.user_account_id)
                        for row in mapping_rows
                        if row.user_account_id is not None
                    })
                    users = session.execute(
                        select(UserAccountModel).where(UserAccountModel.id.in_(user_ids))
                    ).scalars().all() if user_ids else []
                    users_by_id = {int(row.id): row for row in users}
                    resources_by_user_id = self._select_resources_by_user_ids(
                        session,
                        user_account_ids=user_ids,
                        include_invalid=True,
                    )
                    statuses_by_user_id = self._select_user_statuses_by_user_ids(
                        session,
                        user_account_ids=user_ids,
                    )

                    for mapping_row in mapping_rows:
                        user_id = int(mapping_row.user_account_id) if mapping_row.user_account_id is not None else None
                        employee_row = users_by_id.get(user_id) if user_id is not None else None
                        if not employee_row:
                            continue
                        if bool(employee_row.invalid) or bool(employee_row.deleted) or employee_row.active is False:
                            continue
                        employee = self._normalize_employee_row(employee_row) or {}
                        employee["resource"] = resources_by_user_id.get(user_id)
                        employee["user_status"] = statuses_by_user_id.get(user_id)
                        mapping = self._mapping_to_dict(mapping_row) or {}
                        if (
                            target_type
                            and self._worker_entity_type_for_candidate(employee, mapping)
                            != target_type
                        ):
                            continue
                        if matched_before_page < safe_offset:
                            matched_before_page += 1
                            continue
                        selected.append({"employee": employee, "mapping": mapping})
                        if len(selected) >= safe_limit:
                            break

                    if len(mapping_rows) < scan_size:
                        break

                employee_uuids = [
                    item["employee"].get("object_id")
                    for item in selected
                    if item.get("employee") and item["employee"].get("object_id")
                ]
                personal_info_by_uuid = self._select_personal_additional_info_by_user_ids(
                    session,
                    user_account_uuids=employee_uuids,
                )
                for item in selected:
                    employee = item.get("employee") or {}
                    employee_uuid = employee.get("object_id")
                    if employee_uuid:
                        employee["personal_additional_info"] = personal_info_by_uuid.get(str(employee_uuid))

            self._log_debug(
                "get_export_candidates completed",
                candidate_count=len(selected),
                scanned_mapping_count=scan_offset,
                worker_entity_type=target_type,
            )
            return selected
        except Exception as exc:
            self._log_exception("get_export_candidates failed", exc)
            raise

    def get_contractor_vendor_export_candidates(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        limit: int = 500,
        offset: int = 0,
        after_mapping_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        return self.get_export_candidates(
            tenant_id=tenant_id,
            service_id=service_id,
            limit=limit,
            offset=offset,
            worker_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
            after_mapping_id=after_mapping_id,
        )

    def mark_mapping_synced(
        self,
        *,
        tenant_id: str,
        mapping_id: int,
        external_employee_id: Optional[str] = None,
        external_account_id: Optional[str] = None,
        external_vendor_id: Optional[str] = None,
        external_entity_type: Optional[str] = None,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
        external_display_name: Optional[str] = None,
    ) -> None:
        logger.info("[EmployeeRepo] mark_mapping_synced | mapping_id=%s", mapping_id)
        self._log_debug(
            "mark_mapping_synced started",
            tenant_id=tenant_id,
            mapping_id=mapping_id,
            external_employee_id=external_employee_id,
            externalAccountIdPresent=bool(external_account_id),
            syncTokenPresent=bool(sync_token),
        )
        try:
            with tenant_session_scope(tenant_id) as session:
                row = session.get(UserAccountExternalMapModel, int(mapping_id))
                if not row:
                    logger.warning("[EmployeeRepo] mark_mapping_synced: mapping row not found | mapping_id=%s", mapping_id)
                    self._log_debug("mark_mapping_synced mapping row not found", mapping_id=mapping_id)
                    return
                clean_entity_type = self._clean(external_entity_type).upper()
                if clean_entity_type not in const.WORKER_EXTERNAL_ENTITY_TYPES_ALLOWED:
                    clean_entity_type = (
                        const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR
                        if self._clean(external_vendor_id)
                        else const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE
                    )

                if clean_entity_type == const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR:
                    clean_vendor_id = self._clean(external_vendor_id)
                    if clean_vendor_id:
                        row.external_vendor_id = clean_vendor_id
                    row.external_employee_id = None
                else:
                    clean_employee_id = self._clean(external_employee_id)
                    if clean_employee_id:
                        row.external_employee_id = clean_employee_id
                    row.external_vendor_id = None

                row.external_entity_type = clean_entity_type
                if external_account_id is not None:
                    row.external_account_id = self._clean(external_account_id) or None
                if sync_token is not None:
                    row.sync_token = self._clean(sync_token) or None
                if external_display_name is not None:
                    row.external_display_name = self._clean(external_display_name) or None
                row.sync_status = self.STATUS_SYNCED
                row.reason = self._clean(reason) or None
                row.updated_at = self._now()
                session.add(row)
            self._log_debug("mark_mapping_synced completed", mapping_id=mapping_id, sync_status=self.STATUS_SYNCED)
        except Exception as exc:
            self._log_exception("mark_mapping_synced failed", exc)
            raise

    def _invalidate_mapping_row(self, row: UserAccountExternalMapModel, now: datetime) -> None:
        row.sync_status = self.STATUS_INVALID
        row.version_expiry_date = now
        row.endorsement_expiry_date = now
        row.updated_at = now

    def _claim_worker_external_mapping(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        user_account_id: int,
        external_id: str,
        external_entity_type: str,
        external_account_id: Optional[str] = None,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
        external_display_name: Optional[str] = None,
        mapping_id: Optional[int] = None,
        allow_create: bool = False,
    ) -> Dict[str, Any]:
        """Atomically bind one QuickBooks worker id to one FIELDCRAFT worker.

        Import failures can leave an unbound placeholder row for the QuickBooks
        object while export owns a separate pending row for the FIELDCRAFT worker. This
        method consolidates those rows in one transaction. It never steals an
        external id from another FIELDCRAFT worker and never replaces a distinct,
        successfully synced mapping.
        """
        entity_type = self._clean(external_entity_type).upper()
        if entity_type not in const.WORKER_EXTERNAL_ENTITY_TYPES_ALLOWED:
            raise ValueError(f"Unsupported worker external entity type: {external_entity_type!r}")
        external_value = self._clean(external_id)
        if not external_value:
            raise ValueError("A QuickBooks worker id is required to claim a mapping.")

        service_id_int = self._service_id_int(service_id)
        user_account_id_int = int(user_account_id)
        external_column = (
            UserAccountExternalMapModel.external_vendor_id
            if entity_type == const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR
            else UserAccountExternalMapModel.external_employee_id
        )
        claimable_statuses = {
            "",
            self.STATUS_PENDING_PUSH,
            self.STATUS_FAILED,
            self.STATUS_SYNC_FAILED,
            self.STATUS_IMPORT_FAILED,
        }
        now = self._now()
        self._log_debug(
            "claim worker external mapping started",
            tenant_id=tenant_id,
            service_id=service_id_int,
            user_account_id=user_account_id_int,
            external_entity_type=entity_type,
            external_id=external_value,
            mapping_id=mapping_id,
            allow_create=allow_create,
        )

        with tenant_session_scope(tenant_id) as session:
            acquire_transaction_locks(
                session,
                (
                    f"qbo-worker-user:{tenant_id}:{service_id_int}:{user_account_id_int}",
                    f"qbo-worker-external:{tenant_id}:{service_id_int}:{entity_type}:{external_value}",
                ),
            )

            employee = session.get(UserAccountModel, user_account_id_int)
            if not employee:
                raise ValueError(f"FIELDCRAFT employee {user_account_id_int} was not found.")

            external_match = external_column == external_value
            clean_account_id = self._clean(external_account_id)
            if clean_account_id:
                stored_account_id = func.trim(func.coalesce(UserAccountExternalMapModel.external_account_id, ""))
                external_match = (
                    external_match
                    & or_(stored_account_id == clean_account_id, stored_account_id == "")
                )
            stmt = (
                select(UserAccountExternalMapModel)
                .where(
                    UserAccountExternalMapModel.tenant_id == tenant_id,
                    UserAccountExternalMapModel.service_id == service_id_int,
                    self._is_current_mapping_clause(),
                    or_(
                        UserAccountExternalMapModel.user_account_id == user_account_id_int,
                        external_match,
                    ),
                )
                .order_by(UserAccountExternalMapModel.id.desc())
                .with_for_update()
            )
            rows = list(session.execute(stmt).scalars().all())
            carried_time_user_id = next(
                (
                    self._clean(row.external_time_user_id)
                    for row in rows
                    if row.user_account_id is not None
                    and int(row.user_account_id) == user_account_id_int
                    and self._clean(row.external_time_user_id)
                ),
                "",
            )

            for row in rows:
                if self._clean(getattr(row, external_column.key)) != external_value:
                    continue
                if row.user_account_id is not None and int(row.user_account_id) != user_account_id_int:
                    raise ValueError(
                        "This QuickBooks worker is already linked to another FIELDCRAFT employee. "
                        "Review the existing mapping before syncing again."
                    )

            target = None
            if mapping_id is not None:
                target = session.get(UserAccountExternalMapModel, int(mapping_id))
                if not target:
                    raise ValueError(f"Employee mapping {mapping_id} was not found.")
                if target.tenant_id != tenant_id or int(target.service_id or 0) != service_id_int:
                    raise ValueError("The employee mapping belongs to a different tenant or service.")
                if target.user_account_id is not None and int(target.user_account_id) != user_account_id_int:
                    raise ValueError("The employee mapping belongs to another FIELDCRAFT employee.")
                target_account_id = self._clean(target.external_account_id)
                if clean_account_id and target_account_id and target_account_id != clean_account_id:
                    raise ValueError(
                        "The employee mapping belongs to a different QuickBooks company. Re-sync the employee and retry."
                    )
                if self._clean(target.sync_status).upper() == self.STATUS_INVALID:
                    raise ValueError("The employee mapping is no longer current. Re-sync the employee and retry.")
            else:
                target = next(
                    (row for row in rows if row.user_account_id is not None and int(row.user_account_id) == user_account_id_int),
                    None,
                )

            if target is None and not allow_create:
                raise ValueError(
                    "No current FIELDCRAFT worker mapping is available to link with this QuickBooks record."
                )

            if target is not None:
                target_status = self._clean(target.sync_status).upper()
                target_entity = self._clean(target.external_entity_type).upper()
                target_employee_id = self._clean(target.external_employee_id)
                target_vendor_id = self._clean(target.external_vendor_id)
                target_same_id = (
                    target_vendor_id == external_value
                    if entity_type == const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR
                    else target_employee_id == external_value
                )
                has_distinct_external_id = bool(
                    (target_employee_id and not (entity_type == const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE and target_same_id))
                    or (target_vendor_id and not (entity_type == const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR and target_same_id))
                )
                entity_conflict = bool(
                    target_entity
                    and target_entity in const.WORKER_EXTERNAL_ENTITY_TYPES_ALLOWED
                    and target_entity != entity_type
                )
                if (has_distinct_external_id or entity_conflict) and target_status not in claimable_statuses:
                    raise ValueError(
                        "This FIELDCRAFT employee is already linked to a different QuickBooks worker record. "
                        "Review the existing mapping before syncing again."
                    )

            for row in rows:
                if target is not None and int(row.id) == int(target.id):
                    continue
                row_status = self._clean(row.sync_status).upper()
                row_employee_id = self._clean(row.external_employee_id)
                row_vendor_id = self._clean(row.external_vendor_id)
                same_external = (
                    row_vendor_id == external_value
                    if entity_type == const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR
                    else row_employee_id == external_value
                )
                same_user = row.user_account_id is not None and int(row.user_account_id) == user_account_id_int

                if same_external and row.user_account_id is None:
                    self._invalidate_mapping_row(row, now)
                    session.add(row)
                    continue
                if same_user:
                    distinct_synced = row_status == self.STATUS_SYNCED and not same_external
                    if distinct_synced:
                        raise ValueError(
                            "This FIELDCRAFT employee is already linked to a different QuickBooks worker record. "
                            "Review the existing mapping before syncing again."
                        )
                    self._invalidate_mapping_row(row, now)
                    session.add(row)

            if target is None:
                effective_at = employee.updated_at or employee.created_at or now
                target = UserAccountExternalMapModel(
                    user_account_id=user_account_id_int,
                    tenant_id=tenant_id,
                    service_id=service_id_int,
                    version=1,
                    version_effective_date=effective_at,
                    version_expiry_date=None,
                    endorsement_effective_date=effective_at,
                    endorsement_expiry_date=None,
                    updated_at=now,
                    user_account_uuid=self._safe_uuid_obj(employee.object_id),
                )

            target.user_account_id = user_account_id_int
            target.user_account_uuid = self._safe_uuid_obj(employee.object_id)
            if not self._clean(target.external_time_user_id) and carried_time_user_id:
                target.external_time_user_id = carried_time_user_id
            if external_account_id is not None:
                target.external_account_id = self._clean(external_account_id) or None
            target.external_entity_type = entity_type
            if entity_type == const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR:
                target.external_vendor_id = external_value
                target.external_employee_id = None
            else:
                target.external_employee_id = external_value
                target.external_vendor_id = None
            if sync_token is not None:
                target.sync_token = self._clean(sync_token) or None
            if external_display_name is not None:
                target.external_display_name = self._clean(external_display_name) or None
            target.sync_status = self.STATUS_SYNCED
            target.reason = self._clean(reason) or None
            target.updated_at = now
            session.add(target)
            session.flush()
            result = self._mapping_to_dict(target) or {}
            self._log_debug(
                "claim worker external mapping completed",
                mapping_id=result.get("id"),
                user_account_id=user_account_id_int,
                external_entity_type=entity_type,
                external_id=external_value,
            )
            return result

    def claim_external_employee_mapping(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        user_account_id: int,
        external_employee_id: str,
        external_account_id: Optional[str] = None,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
        external_display_name: Optional[str] = None,
        mapping_id: Optional[int] = None,
        allow_create: bool = False,
    ) -> Dict[str, Any]:
        return self._claim_worker_external_mapping(
            tenant_id=tenant_id,
            service_id=service_id,
            user_account_id=user_account_id,
            external_id=external_employee_id,
            external_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE,
            external_account_id=external_account_id,
            sync_token=sync_token,
            reason=reason,
            external_display_name=external_display_name,
            mapping_id=mapping_id,
            allow_create=allow_create,
        )

    def claim_external_vendor_mapping(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        user_account_id: int,
        external_vendor_id: str,
        external_account_id: Optional[str] = None,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
        external_display_name: Optional[str] = None,
        mapping_id: Optional[int] = None,
        allow_create: bool = False,
    ) -> Dict[str, Any]:
        return self._claim_worker_external_mapping(
            tenant_id=tenant_id,
            service_id=service_id,
            user_account_id=user_account_id,
            external_id=external_vendor_id,
            external_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
            external_account_id=external_account_id,
            sync_token=sync_token,
            reason=reason,
            external_display_name=external_display_name,
            mapping_id=mapping_id,
            allow_create=allow_create,
        )

    def create_worker_vendor_mapping(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        user_account_id: int,
        external_vendor_id: str,
        external_account_id: Optional[str] = None,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
        external_display_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or safely consolidate a 1099 worker-to-QBO Vendor mapping."""
        return self.claim_external_vendor_mapping(
            tenant_id=tenant_id,
            service_id=service_id,
            user_account_id=user_account_id,
            external_vendor_id=external_vendor_id,
            external_account_id=external_account_id,
            sync_token=sync_token,
            reason=reason,
            external_display_name=external_display_name,
            allow_create=True,
        )

    def mark_mapping_failed(
        self,
        *,
        tenant_id: str,
        mapping_id: int,
        status: Optional[str] = None,
        reason: Optional[str] = None,
        external_display_name: Optional[str] = None,
    ) -> None:
        logger.info("[EmployeeRepo] mark_mapping_failed | mapping_id=%s status=%s", mapping_id, status or self.STATUS_FAILED)
        failed_status = status or self.STATUS_FAILED
        self._log_debug(
            "mark_mapping_failed started",
            tenant_id=tenant_id,
            mapping_id=mapping_id,
            sync_status=failed_status,
        )
        try:
            with tenant_session_scope(tenant_id) as session:
                row = session.get(UserAccountExternalMapModel, int(mapping_id))
                if not row:
                    logger.warning("[EmployeeRepo] mark_mapping_failed: mapping row not found | mapping_id=%s", mapping_id)
                    self._log_debug("mark_mapping_failed mapping row not found", mapping_id=mapping_id)
                    return
                row.sync_status = failed_status
                row.reason = self._clean(reason) or None
                if external_display_name is not None:
                    row.external_display_name = self._clean(external_display_name) or None
                row.updated_at = self._now()
                session.add(row)
            self._log_debug("mark_mapping_failed completed", mapping_id=mapping_id, sync_status=failed_status)
        except Exception as exc:
            self._log_exception("mark_mapping_failed failed", exc)
            raise

    def get_personal_additional_info(self, tenant_id: str, user_account_uuid: Any) -> Optional[Dict[str, Any]]:
        user_account_uuid = self._clean_pai_value(user_account_uuid)
        if not tenant_id or not user_account_uuid:
            return None
        with tenant_session_scope(tenant_id) as session:
            table = self._personal_info_table(session)
            columns = [
                table.c.id,
                table.c.user_id,
                table.c.address,
                table.c.city,
                table.c.state,
                table.c.country,
                table.c.zip_code,
                table.c.email,
                table.c.first_name,
                table.c.last_name,
                table.c.updated_at,
            ]
            if "hire_date" in table.c:
                columns.append(table.c.hire_date)
            row = session.execute(
                select(*columns)
                .where(cast(table.c.user_id, Text) == user_account_uuid)
                .order_by(desc(table.c.id))
                .limit(1)
            ).mappings().first()
            return self._normalize_personal_additional_info_row(row)

    def _upsert_personal_additional_info_with_session(
        self,
        session,
        *,
        user_account_uuid: Any,
        employee: Dict[str, Any],
        address_info: Dict[str, Any],
    ) -> bool:
        """Upsert worker address data using the tenant's actual table schema.

        Tenant databases are not always upgraded at the same time. Reflecting the
        table prevents optional columns such as ``shipping_location`` from
        breaking the worker import after the FIELDCRAFT employee has already been
        created.
        """
        user_account_uuid = self._clean_pai_value(user_account_uuid)
        if not user_account_uuid:
            return False

        address_payload = {
            "address": self._clean_pai_value(
                address_info.get("address") or address_info.get("line1")
            ),
            "city": self._clean_pai_value(address_info.get("city")),
            "state": self._clean_pai_value(
                address_info.get("state")
                or address_info.get("country_subdivision_code")
            ),
            "country": self._clean_pai_value(address_info.get("country")),
            "zip_code": self._clean_pai_value(
                address_info.get("zip_code") or address_info.get("postal_code")
            ),
        }
        hire_date = address_info.get("hire_date") or address_info.get("hireDate") or address_info.get("hiredDate")
        if not any(address_payload.values()) and hire_date is None:
            return False

        contact_payload = {
            "email": self._clean_pai_value(employee.get("email")),
            "first_name": self._clean_pai_value(
                employee.get("firstname") or employee.get("first_name")
            ),
            "last_name": self._clean_pai_value(employee.get("lastname") or employee.get("last_name")) or "",
        }
        now = self._now()
        table = self._personal_info_table(session)
        columns = set(table.c.keys())
        if "user_id" not in columns:
            raise ValueError("personal_additional_info.user_id is required")

        existing = session.execute(
            select(table)
            .where(cast(table.c.user_id, Text) == user_account_uuid)
            .order_by(desc(table.c.id))
            .limit(1)
        ).mappings().first()

        values: Dict[str, Any] = {
            **address_payload,
            **contact_payload,
            "hire_date": hire_date,
            "location": address_payload.get("address") or "",
            "shipping_location": address_payload.get("address") or "",
            "updated_at": now,
        }
        values = {key: value for key, value in values.items() if key in columns}

        if existing:
            existing_dict = dict(existing)
            update_payload = {
                key: value if value is not None else existing_dict.get(key)
                for key, value in values.items()
                if key not in {"id", "user_id", "created_at"}
            }
            if all(
                (existing_dict.get(key) or None) == (update_payload.get(key) or None)
                for key in update_payload
            ):
                return False
            session.execute(
                table.update().where(table.c.id == existing_dict.get("id")).values(**update_payload)
            )
            return True

        insert_payload: Dict[str, Any] = {
            "user_id": user_account_uuid,
            **values,
        }
        if "created_at" in columns:
            insert_payload["created_at"] = now
        session.execute(table.insert().values(**insert_payload))
        return True

    def upsert_personal_additional_info(
        self,
        *,
        tenant_id: str,
        employee: Dict[str, Any],
        address_info: Dict[str, Any],
    ) -> bool:
        employee = employee or {}
        user_account_uuid = employee.get("object_id") or employee.get("objectId")
        if not tenant_id or not user_account_uuid:
            return False
        with tenant_session_scope(tenant_id) as session:
            return self._upsert_personal_additional_info_with_session(
                session,
                user_account_uuid=user_account_uuid,
                employee=employee,
                address_info=address_info or {},
            )

    def _update_user_status_dates_with_session(
        self,
        session: Any,
        *,
        user_account_id: int,
        hired_date: Optional[datetime],
        work_start_date: Optional[datetime],
    ) -> Optional[Dict[str, Any]]:
        if hired_date is None and work_start_date is None:
            return None

        table = self._user_status_table(session)
        if "user_id" not in table.c:
            return None

        stmt = select(table).where(table.c.user_id == int(user_account_id))
        if "invalid" in table.c:
            stmt = stmt.where(or_(table.c.invalid.is_(None), table.c.invalid.is_(False)))
        if "id" in table.c:
            stmt = stmt.order_by(desc(table.c.id))
        existing = session.execute(stmt.limit(1)).mappings().first()
        if not existing:
            raise RuntimeError(
                "Employee work dates could not be updated because the active "
                f"user_status record was not found for employee {user_account_id}."
            )

        existing_dict = dict(existing)
        update_values: Dict[str, Any] = {}
        if hired_date is not None and "hired_date" in table.c:
            update_values["hired_date"] = hired_date
        if work_start_date is not None and "effective_start_date" in table.c:
            update_values["effective_start_date"] = work_start_date
        if "updated_at" in table.c:
            update_values["updated_at"] = self._now()

        changed_values = {
            key: value
            for key, value in update_values.items()
            if existing_dict.get(key) != value
        }
        if changed_values:
            session.execute(
                table.update()
                .where(table.c.id == existing_dict.get("id"))
                .values(**changed_values)
            )
            existing_dict.update(changed_values)
        return self._normalize_user_status_row(existing_dict)

    def _ensure_aware(self, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    def get_employee_latest_local_timestamp(self, employee: Optional[Dict[str, Any]]) -> Optional[datetime]:
        employee = employee or {}
        candidates = []
        for value in (
            employee.get("updated_at"),
            employee.get("created_at"),
            (employee.get("personal_additional_info") or {}).get("updated_at"),
            (employee.get("resource") or {}).get("updated_at"),
            (employee.get("user_status") or {}).get("updated_at"),
        ):
            if isinstance(value, datetime):
                candidates.append(self._ensure_aware(value))
        return max(candidates) if candidates else None


    def get_employee_by_id(self, tenant_id: str, user_account_id: int) -> Optional[Dict[str, Any]]:
        if user_account_id is None:
            return None
        try:
            with tenant_session_scope(tenant_id) as session:
                row = session.get(UserAccountModel, int(user_account_id))
                employee = self._normalize_employee_row(row) if row else None
            if employee and employee.get("object_id"):
                employee["personal_additional_info"] = self.get_personal_additional_info(tenant_id, employee.get("object_id"))
            if employee and employee.get("id") is not None:
                employee["resource"] = self.get_resource_by_user_account_id(tenant_id, employee.get("id"))
            if employee and employee.get("id") is not None:
                with tenant_session_scope(tenant_id) as session:
                    employee["user_status"] = self._select_user_statuses_by_user_ids(
                        session,
                        user_account_ids=[employee.get("id")],
                    ).get(int(employee.get("id")))
            return employee
        except Exception as exc:
            self._log_exception("get_employee_by_id failed", exc)
            raise

    def get_employee_id_by_email(self, *, tenant_id: str, email: str) -> Optional[int]:
        email_value = self._clean(email).lower()
        if not email_value:
            return None
        try:
            with tenant_session_scope(tenant_id) as session:
                row = session.execute(
                    select(UserAccountModel.id)
                    .where(
                        func.lower(UserAccountModel.email) == email_value,
                        func.coalesce(UserAccountModel.invalid, False) == False,  # noqa: E712
                        func.coalesce(UserAccountModel.deleted, False) == False,  # noqa: E712
                    )
                    .order_by(UserAccountModel.id.desc())
                ).first()
                return int(row[0]) if row and row[0] is not None else None
        except Exception as exc:
            self._log_exception("get_employee_id_by_email failed", exc)
            raise

    def sync_projection_changed(self, employee: Optional[Dict[str, Any]], incoming: Dict[str, Any]) -> bool:
        if not employee:
            return True

        def clean(value: Any) -> str:
            return self._clean(value).lower()

        def phone(value: Any) -> str:
            return "".join(ch for ch in self._clean(value) if ch.isdigit())

        def date_value(value: Any) -> str:
            if value is None:
                return ""
            if isinstance(value, datetime):
                return value.date().isoformat()
            raw = self._clean(value)
            if not raw:
                return ""
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
            except ValueError:
                return raw[:10]

        checks = [
            (clean(employee.get("firstname")), clean(incoming.get("firstname"))),
            (clean(employee.get("lastname")), clean(incoming.get("lastname"))),
            (clean(employee.get("middlename")), clean(incoming.get("middlename"))),
            (clean(employee.get("email")), clean(incoming.get("email"))),
            (phone(employee.get("mobile_no")), phone(incoming.get("mobileNo") or incoming.get("mobile_no") or incoming.get("phone"))),
        ]
        if any(left != right for left, right in checks):
            return True

        incoming_birthdate = date_value(incoming.get("birthdate"))
        if incoming_birthdate and date_value(employee.get("birthdate")) != incoming_birthdate:
            return True

        incoming_hire_date = date_value(incoming.get("hiredDate") or incoming.get("hireDate"))
        current_hire_date = date_value(
            (employee.get("user_status") or {}).get("hired_date")
            or (employee.get("personal_additional_info") or {}).get("hire_date")
        )
        if incoming_hire_date and current_hire_date != incoming_hire_date:
            return True

        incoming_resource = incoming.get("resource") or {}
        current_resource = employee.get("resource") or {}
        if incoming_resource and not current_resource:
            return True
        if incoming_resource and current_resource.get("invalid"):
            return True
        if incoming_resource.get("cost_present") and self._decimal_changed(current_resource.get("cost"), incoming_resource.get("cost")):
            return True
        if incoming_resource.get("cost_present") and clean(current_resource.get("unit")) != clean(incoming_resource.get("unit")):
            return True
        if incoming_resource.get("overtime_cost_present"):
            current_info = self._json_to_dict(current_resource.get("additional_info"))
            if self._decimal_changed(current_info.get("overTimeCost"), incoming_resource.get("overtime_cost")):
                return True

        incoming_address = incoming.get("personal_additional_info") or incoming.get("addressInfo") or {}
        if not any(self._clean(incoming_address.get(key)) for key in ("address", "line1", "city", "state", "country_subdivision_code", "country", "zip_code", "postal_code")):
            return False

        current_address = employee.get("personal_additional_info") or {}
        address_checks = [
            (clean(current_address.get("address")), clean(incoming_address.get("address") or incoming_address.get("line1"))),
            (clean(current_address.get("city")), clean(incoming_address.get("city"))),
            (clean(current_address.get("state")), clean(incoming_address.get("state") or incoming_address.get("country_subdivision_code"))),
            (clean(current_address.get("country")), clean(incoming_address.get("country"))),
            (clean(current_address.get("zip_code")), clean(incoming_address.get("zip_code") or incoming_address.get("postal_code"))),
        ]
        return any(left != right for left, right in address_checks)

    def update_employee_from_qbo_payload(
        self,
        *,
        tenant_id: str,
        user_account_id: int,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Update a mapped employee directly in user_account from QuickBooks data.

        New employee imports still go through the Tenant API. For mapped updates,
        employee records are not versioned, so we update the same user_account row
        just like vendor sync. Empty QuickBooks fields do not erase existing FIELDCRAFT
        values.
        """
        if user_account_id is None:
            raise ValueError("user_account_id is required for employee update")

        def first_present(*keys: str) -> str:
            for key in keys:
                value = self._clean(payload.get(key))
                if value:
                    return value
            return ""

        def parse_datetime(value: Any) -> Optional[datetime]:
            raw = self._clean(value)
            if not raw:
                return None
            if isinstance(value, datetime):
                return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                return None

        with tenant_session_scope(tenant_id) as session:
            row = session.get(UserAccountModel, int(user_account_id))
            if not row:
                raise ValueError(f"Employee {user_account_id} not found")

            firstname = first_present("firstname", "firstName")
            lastname = first_present("lastname", "lastName")
            middlename = first_present("middlename", "middleName")
            email = first_present("email")
            phone = first_present("mobileNo", "mobile_no", "phone")
            birthdate = parse_datetime(payload.get("birthdate"))
            hired_date = parse_datetime(payload.get("hiredDate") or payload.get("hireDate"))
            work_start_date = parse_datetime(payload.get("workStartDate"))
            address_info = payload.get("personal_additional_info") or payload.get("addressInfo") or {}

            if firstname:
                row.firstname = firstname
            if lastname:
                row.lastname = lastname
            if middlename:
                row.middlename = middlename
            if email:
                row.email = email
            if phone:
                row.mobile_no = phone
            if birthdate:
                row.birthdate = birthdate
            row.updated_at = self._now()
            session.add(row)
            session.flush()
            employee = self._normalize_employee_row(row) or {}
            personal_info_payload = dict(address_info) if isinstance(address_info, dict) else {}
            if hired_date is not None:
                personal_info_payload["hire_date"] = hired_date
            if personal_info_payload and row.object_id:
                self._upsert_personal_additional_info_with_session(
                    session,
                    user_account_uuid=str(row.object_id),
                    employee=employee,
                    address_info=personal_info_payload,
                )
                employee["personal_additional_info"] = personal_info_payload
            if hired_date is not None or work_start_date is not None:
                employee["user_status"] = self._update_user_status_dates_with_session(
                    session,
                    user_account_id=int(row.id),
                    hired_date=hired_date,
                    work_start_date=work_start_date,
                )
            if "resource" in payload:
                employee["resource"] = self._upsert_resource_with_session(
                    session,
                    employee=employee,
                    resource_payload=payload.get("resource") or {},
                )
            return employee

    def close_mapping_as_invalid(self, tenant_id: str, mapping_id: int) -> None:
        try:
            with tenant_session_scope(tenant_id) as session:
                row = session.get(UserAccountExternalMapModel, int(mapping_id))
                if not row:
                    return
                row.sync_status = self.STATUS_INVALID
                row.updated_at = self._now()
                session.add(row)
        except Exception as exc:
            self._log_exception("close_mapping_as_invalid failed", exc)
            raise

    def rotate_mapping_for_employee(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        user_account_id: Optional[int],
        external_employee_id: Optional[str],
        external_account_id: Optional[str],
        sync_status: str,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
        external_display_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = self._now()
        external_value = self._clean(external_employee_id) or None
        service_id_int = self._service_id_int(service_id)
        with tenant_session_scope(tenant_id) as session:
            user_uuid = None
            effective_at = now
            if user_account_id is not None:
                employee = session.get(UserAccountModel, int(user_account_id))
                if employee:
                    user_uuid = self._safe_uuid_obj(employee.object_id)
                    effective_at = employee.updated_at or employee.created_at or now

            existing_stmt = (
                select(UserAccountExternalMapModel)
                .where(
                    UserAccountExternalMapModel.tenant_id == tenant_id,
                    UserAccountExternalMapModel.service_id == service_id_int,
                    func.upper(func.coalesce(UserAccountExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .order_by(UserAccountExternalMapModel.id.desc())
            )
            if user_account_id is not None:
                existing_stmt = existing_stmt.where(UserAccountExternalMapModel.user_account_id == int(user_account_id))
            elif external_value:
                existing_stmt = existing_stmt.where(UserAccountExternalMapModel.external_employee_id == external_value)

            existing_rows = list(session.execute(existing_stmt).scalars().all())
            carried_time_user_id = next(
                (
                    self._clean(existing.external_time_user_id)
                    for existing in existing_rows
                    if self._clean(existing.external_time_user_id)
                ),
                "",
            )
            for existing in existing_rows:
                existing.sync_status = self.STATUS_INVALID
                existing.updated_at = now
                session.add(existing)

            row = UserAccountExternalMapModel(
                user_account_id=int(user_account_id) if user_account_id is not None else None,
                tenant_id=tenant_id,
                service_id=service_id_int,
                external_employee_id=external_value,
                external_account_id=self._clean(external_account_id) or None,
                sync_status=self._clean(sync_status) or self.STATUS_SYNCED,
                version=1,
                version_effective_date=effective_at,
                version_expiry_date=None,
                endorsement_effective_date=effective_at,
                endorsement_expiry_date=None,
                updated_at=now,
                user_account_uuid=user_uuid,
                sync_token=self._clean(sync_token) or None,
                reason=self._clean(reason) or None,
                external_display_name=self._clean(external_display_name) or None,
                external_time_user_id=carried_time_user_id or None,
            )
            session.add(row)
            session.flush()
            return self._mapping_to_dict(row) or {}

    def create_external_mapping(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        user_account_id: int,
        external_employee_id: str,
        external_account_id: Optional[str] = None,
        sync_token: Optional[str] = None,
        status: str = STATUS_SYNCED,
        reason: Optional[str] = None,
        external_display_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.rotate_mapping_for_employee(
            tenant_id=tenant_id,
            service_id=service_id,
            user_account_id=user_account_id,
            external_employee_id=external_employee_id,
            external_account_id=external_account_id,
            sync_status=status,
            sync_token=sync_token,
            reason=reason,
            external_display_name=external_display_name,
        )

    def upsert_import_failed_mapping(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        external_employee_id: str,
        external_account_id: Optional[str] = None,
        user_account_id: Optional[int] = None,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
        sync_status: Optional[str] = None,
        detach_employee: bool = False,
        external_display_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        external_value = self._clean(external_employee_id)
        if not external_value:
            raise ValueError("external_employee_id is required for employee import failure mapping")
        status_value = self._clean(sync_status) or self.STATUS_IMPORT_FAILED
        now = self._now()
        service_id_int = self._service_id_int(service_id)
        with tenant_session_scope(tenant_id) as session:
            current = session.execute(
                select(UserAccountExternalMapModel)
                .where(
                    UserAccountExternalMapModel.tenant_id == tenant_id,
                    UserAccountExternalMapModel.service_id == service_id_int,
                    UserAccountExternalMapModel.external_employee_id == external_value,
                    func.upper(func.coalesce(UserAccountExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .order_by(UserAccountExternalMapModel.id.desc())
            ).scalars().first()
            if current:
                if detach_employee:
                    current.user_account_id = None
                    current.user_account_uuid = None
                elif user_account_id is not None:
                    current.user_account_id = int(user_account_id)
                    employee = session.get(UserAccountModel, int(user_account_id))
                    if employee and employee.object_id:
                        current.user_account_uuid = self._safe_uuid_obj(employee.object_id)
                if external_account_id is not None:
                    current.external_account_id = self._clean(external_account_id) or None
                if sync_token is not None:
                    current.sync_token = self._clean(sync_token) or None
                if external_display_name is not None:
                    current.external_display_name = self._clean(external_display_name) or None
                current.sync_status = status_value
                current.reason = self._clean(reason) or None
                current.updated_at = now
                session.add(current)
                session.flush()
                return self._mapping_to_dict(current) or {}

            user_uuid = None
            effective_at = now
            if user_account_id is not None:
                employee = session.get(UserAccountModel, int(user_account_id))
                if employee:
                    user_uuid = self._safe_uuid_obj(employee.object_id)
                    effective_at = employee.updated_at or employee.created_at or now
            row = UserAccountExternalMapModel(
                user_account_id=int(user_account_id) if user_account_id is not None else None,
                tenant_id=tenant_id,
                service_id=service_id_int,
                external_employee_id=external_value,
                external_account_id=self._clean(external_account_id) or None,
                sync_status=status_value,
                version=1,
                version_effective_date=effective_at,
                version_expiry_date=None,
                endorsement_effective_date=effective_at,
                endorsement_expiry_date=None,
                updated_at=now,
                user_account_uuid=user_uuid,
                sync_token=self._clean(sync_token) or None,
                reason=self._clean(reason) or None,
                external_display_name=self._clean(external_display_name) or None,
            )
            session.add(row)
            session.flush()
            return self._mapping_to_dict(row) or {}

    def get_open_import_failed_external_employee_ids(self, *, tenant_id: str, service_id: Any) -> List[str]:
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(UserAccountExternalMapModel.external_employee_id)
                .where(
                    UserAccountExternalMapModel.tenant_id == tenant_id,
                    UserAccountExternalMapModel.service_id == self._service_id_int(service_id),
                    UserAccountExternalMapModel.sync_status == self.STATUS_IMPORT_FAILED,
                    UserAccountExternalMapModel.external_employee_id.is_not(None),
                )
                .order_by(UserAccountExternalMapModel.updated_at.asc(), UserAccountExternalMapModel.id.asc())
            )
            return [str(row[0]) for row in session.execute(stmt).all() if row and row[0]]

    def _load_service_source_map(self) -> Dict[int, str]:
        """Return service IDs mapped to the source label used by the UI."""
        from source.dal.db_session import service_session_scope
        from source.dal.service_models import ServiceMasterModel

        result: Dict[int, str] = {}
        try:
            with service_session_scope() as session:
                rows = session.execute(
                    select(ServiceMasterModel).where(ServiceMasterModel.invalid.isnot(True))
                ).scalars().all()
                for row in rows:
                    service_name = self._clean(row.name)
                    result[int(row.id)] = "QuickBooks" if service_name == "QuickBooks" else "FIELDCRAFT"
        except Exception as exc:
            logger.warning("[EmployeeRepo] could not load service source map | error=%s", exc)
        return result

    def _employee_source(
        self,
        employee: Dict[str, Any],
        service_source_map: Dict[int, str],
        quickbooks_service_id: int,
    ) -> str:
        service_id = employee.get("service_id")
        if service_id is None:
            return "FIELDCRAFT"
        try:
            service_id_int = int(service_id)
        except (TypeError, ValueError):
            return "FIELDCRAFT"
        source = service_source_map.get(service_id_int)
        if source:
            return source
        return "QuickBooks" if service_id_int == quickbooks_service_id else "FIELDCRAFT"

    def get_employee_cdc_page(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        page: int = 1,
        page_size: int = 25,
        search: Optional[str] = None,
        sync_status: Optional[str] = None,
        mapped_only: bool = False,
        changed_since: Optional[str] = None,
    ) -> Dict[str, Any]:
        del mapped_only
        self._log_debug(
            "get_employee_cdc_page started",
            tenant_id=tenant_id,
            service_id=service_id,
            page=page,
            page_size=page_size,
            search=search,
            sync_status=sync_status,
            changed_since=changed_since,
        )
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 25), 1), 100)
        offset = (page - 1) * page_size
        service_id_int = self._service_id_int(service_id)
        search_term = self._clean(search)
        normalized_status = self._clean(sync_status).upper()
        changed_since_value = self._parse_changed_since(changed_since)
        service_source_map = self._load_service_source_map()

        with tenant_session_scope(tenant_id) as session:
            mapping_group_key = func.coalesce(
                cast(UserAccountExternalMapModel.user_account_id, Text),
                UserAccountExternalMapModel.external_employee_id,
                UserAccountExternalMapModel.external_vendor_id,
                cast(UserAccountExternalMapModel.id, Text),
            )
            latest_ids = (
                select(func.max(UserAccountExternalMapModel.id).label("map_id"))
                .where(
                    UserAccountExternalMapModel.tenant_id == tenant_id,
                    UserAccountExternalMapModel.service_id == service_id_int,
                    func.upper(func.coalesce(UserAccountExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .group_by(mapping_group_key)
                .subquery()
            )

            base_stmt = (
                select(UserAccountExternalMapModel, UserAccountModel)
                .where(UserAccountExternalMapModel.id.in_(select(latest_ids.c.map_id)))
                .outerjoin(UserAccountModel, UserAccountModel.id == UserAccountExternalMapModel.user_account_id)
            )

            if normalized_status and normalized_status != "ALL":
                base_stmt = base_stmt.where(func.upper(func.coalesce(UserAccountExternalMapModel.sync_status, "")) == normalized_status)

            if changed_since_value is not None:
                base_stmt = base_stmt.where(
                    or_(
                        UserAccountExternalMapModel.updated_at >= changed_since_value,
                        UserAccountModel.updated_at >= changed_since_value,
                    )
                )

            if search_term:
                like_term = f"%{search_term.lower()}%"
                base_stmt = base_stmt.where(
                    or_(
                        func.lower(func.coalesce(UserAccountModel.firstname, "")).like(like_term),
                        func.lower(func.coalesce(UserAccountModel.lastname, "")).like(like_term),
                        func.lower(func.coalesce(UserAccountModel.email, "")).like(like_term),
                        func.lower(func.coalesce(UserAccountModel.mobile_no, "")).like(like_term),
                        func.lower(func.coalesce(cast(UserAccountModel.object_id, Text), "")).like(like_term),
                        func.lower(func.coalesce(UserAccountExternalMapModel.external_employee_id, "")).like(like_term),
                        func.lower(func.coalesce(UserAccountExternalMapModel.external_vendor_id, "")).like(like_term),
                        func.lower(func.coalesce(UserAccountExternalMapModel.external_time_user_id, "")).like(like_term),
                        func.lower(func.coalesce(UserAccountExternalMapModel.external_display_name, "")).like(like_term),
                    )
                )

            total_records = int(session.execute(select(func.count()).select_from(base_stmt.subquery())).scalar() or 0)
            records = session.execute(
                base_stmt
                .order_by(UserAccountExternalMapModel.updated_at.desc().nullslast(), UserAccountExternalMapModel.id.desc())
                .offset(offset)
                .limit(page_size)
            ).all()

            rows: List[Dict[str, Any]] = []
            for mapping_row, employee_row in records:
                mapping = self._mapping_to_dict(mapping_row) or {}
                employee = self._normalize_employee_row(employee_row) if employee_row else {}
                first_name = self._clean(employee.get("firstname"))
                middle_name = self._clean(employee.get("middlename"))
                last_name = self._clean(employee.get("lastname"))
                display_name = " ".join(part for part in (first_name, middle_name, last_name) if part).strip()
                reason_payload = self._reason_payload(mapping.get("reason"))
                qbo_display_name = self._clean(mapping.get("external_display_name") or reason_payload.get("display_name"))
                qbo_email = self._clean(reason_payload.get("email") or reason_payload.get("qbo_email"))
                external_employee_id = self._clean(mapping.get("external_employee_id")) or None
                external_vendor_id = self._clean(mapping.get("external_vendor_id")) or None
                external_entity_type = self._mapping_entity_type(mapping)
                quickbooks_id = self._worker_quickbooks_id(mapping)
                worker_type = self._worker_type_label(employee, mapping)
                rows.append({
                    "employeeId": employee.get("id"),
                    "employeeUuid": employee.get("object_id") or mapping.get("user_account_uuid"),
                    "displayName": display_name or employee.get("email") or qbo_display_name or f"QuickBooks worker #{quickbooks_id or mapping.get('id')}",
                    "firstName": employee.get("firstname"),
                    "middleName": employee.get("middlename"),
                    "lastName": employee.get("lastname"),
                    "email": employee.get("email") or qbo_email,
                    "phone": employee.get("mobile_no"),
                    "roles": employee.get("roles"),
                    "employeeStatus": employee.get("status"),
                    "active": employee.get("active"),
                    "mappingId": mapping.get("id"),
                    # Backward-compatible display id for the existing UI column.
                    # W2 workers resolve to the QBO Employee id; 1099/contractor
                    # workers resolve to the QBO Vendor id.
                    "externalEmployeeId": quickbooks_id,
                    # Raw ids are kept for consumers that need the exact QBO entity type.
                    "qboEmployeeId": external_employee_id,
                    "externalVendorId": external_vendor_id,
                    "qboVendorId": external_vendor_id,
                    "externalEntityType": external_entity_type,
                    "employeeType": worker_type,
                    "workerType": worker_type,
                    "workerTypeLabel": worker_type,
                    "workerClassification": worker_type,
                    # Generic worker id used by new UI/API consumers.
                    "externalWorkerId": quickbooks_id,
                    "quickBooksId": quickbooks_id,
                    "quickbooksId": quickbooks_id,
                    "qboWorkerId": quickbooks_id,
                    "externalTimeUserId": mapping.get("external_time_user_id"),
                    "quickBooksTimeUserId": mapping.get("external_time_user_id"),
                    "quickBooksEntityType": external_entity_type,
                    "qboWorkerEntityType": external_entity_type,
                    "externalAccountId": mapping.get("external_account_id"),
                    "externalDisplayName": mapping.get("external_display_name"),
                    "syncStatus": mapping.get("sync_status"),
                    "syncToken": mapping.get("sync_token"),
                    "mappingVersion": mapping.get("version"),
                    "endorsementEffectiveDate": mapping.get("endorsement_effective_date"),
                    "mappingUpdatedAt": mapping.get("updated_at"),
                    "linked": employee_row is not None,
                    "reason": mapping.get("reason"),
                    "source": (
                        self._employee_source(employee, service_source_map, service_id_int)
                        if employee_row is not None
                        else "QuickBooks"
                    ),
                })

            summary_stmt = (
                select(UserAccountExternalMapModel.sync_status, func.count())
                .where(UserAccountExternalMapModel.id.in_(select(latest_ids.c.map_id)))
                .group_by(UserAccountExternalMapModel.sync_status)
            )
            status_counts: Dict[str, int] = {}
            for status, count in session.execute(summary_stmt).all():
                status_counts[(status or "UNKNOWN").upper()] = int(count or 0)

            total_employees = int(sum(status_counts.values()))
            summary = {
                "totalEmployees": total_employees,
                "mappedEmployees": total_employees,
                "unmappedEmployees": 0,
                "syncedEmployees": status_counts.get(self.STATUS_SYNCED, 0),
                "pendingPushEmployees": status_counts.get(self.STATUS_PENDING_PUSH, 0),
                "failedEmployees": status_counts.get(self.STATUS_FAILED, 0),
                "importFailedEmployees": status_counts.get(self.STATUS_IMPORT_FAILED, 0),
                "syncFailedEmployees": status_counts.get(self.STATUS_SYNC_FAILED, 0),
            }

            payload = {
                "rows": rows,
                "summary": summary,
                "pagination": {
                    "page": page,
                    "pageSize": page_size,
                    "totalRecords": total_records,
                    "totalPages": (total_records + page_size - 1) // page_size if total_records else 0,
                },
            }
            self._log_debug(
                "get_employee_cdc_page completed",
                row_count=len(rows),
                total_records=total_records,
                summary=summary,
            )
            return payload
