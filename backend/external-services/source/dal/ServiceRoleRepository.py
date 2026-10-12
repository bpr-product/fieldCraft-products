from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import cast, func, or_, select, Text

from source.dal.db_session import tenant_session_scope
from source.dal.service_models import RoleExternalMapModel, RoleModel

logger = logging.getLogger(__name__)


class ServiceRoleRepository:
    """Repository for exporting FIELDCRAFT roles as QuickBooks service items."""

    STATUS_PENDING_PUSH = "PENDING_PUSH"
    STATUS_SYNCED = "SYNCED"
    STATUS_FAILED = "FAILED"
    STATUS_SYNC_FAILED = "SYNC_FAILED"
    STATUS_INVALID = "INVALID"

    TIME_INFO_KEY = "quickbooksTime"
    TIME_CUSTOMFIELD_ID_KEY = "serviceItemCustomFieldId"
    TIME_CUSTOMFIELD_ITEM_ID_KEY = "serviceItemCustomFieldItemId"
    TIME_SERVICE_ITEM_NAME_KEY = "serviceItemName"
    TIME_RESOLVED_AT_KEY = "resolvedAt"

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _service_id_int(service_id: Any) -> int:
        try:
            return int(service_id)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

    def _load_json(self, value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        raw = self._clean(value)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _dump_json(self, value: Dict[str, Any]) -> str:
        return json.dumps(value or {}, default=str)

    def _merge_additional_info(
        self,
        existing: Any,
        updates: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Merge mapping metadata without dropping nested integration IDs."""
        merged = self._load_json(existing)
        if not isinstance(updates, dict):
            return merged

        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                nested = dict(merged[key])
                nested.update(value)
                merged[key] = nested
            else:
                merged[key] = value
        return merged

    def _is_current_mapping_clause(self):
        now = self._now()
        return or_(
            RoleExternalMapModel.endorsement_expiry_date.is_(None),
            RoleExternalMapModel.endorsement_expiry_date > now,
        )

    def _role_to_dict(self, row: RoleModel) -> Dict[str, Any]:
        return {
            "id": int(row.id),
            "active": self._bool(row.active),
            "builtin": self._bool(row.builtin),
            "created_at": row.created_at,
            "description": row.description,
            "name": row.name,
            "updated_at": row.updated_at,
        }

    def _mapping_to_dict(self, row: RoleExternalMapModel) -> Dict[str, Any]:
        return {
            "id": int(row.id),
            "role_id": int(row.role_id) if row.role_id is not None else None,
            "tenant_id": row.tenant_id,
            "service_id": int(row.service_id) if row.service_id is not None else None,
            "external_service_id": row.external_service_id,
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
            "additional_info": self._load_json(getattr(row, "additional_info", None)),
            "external_time_customfield_item_id": self._clean(
                getattr(row, "external_time_customfield_item_id", None)
            ) or None,
        }

    def normalize_role_row(self, role: RoleModel | Dict[str, Any] | None) -> Optional[Dict[str, Any]]:
        if role is None:
            return None
        if isinstance(role, dict):
            return {
                "id": int(role.get("id") or 0),
                "active": self._bool(role.get("active")),
                "builtin": self._bool(role.get("builtin")),
                "created_at": role.get("created_at"),
                "description": role.get("description"),
                "name": role.get("name"),
                "updated_at": role.get("updated_at"),
            }
        return self._role_to_dict(role)

    def get_role_by_id(self, *, tenant_id: str, role_id: int) -> Optional[Dict[str, Any]]:
        try:
            role_id_int = int(role_id)
        except (TypeError, ValueError):
            return None
        if role_id_int <= 0:
            return None
        with tenant_session_scope(tenant_id) as session:
            row = session.get(RoleModel, role_id_int)
            return self._role_to_dict(row) if row else None

    def get_active_role_by_name(self, *, tenant_id: str, name: str) -> Optional[Dict[str, Any]]:
        role_name = self._clean(name)
        if not role_name:
            return None
        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                select(RoleModel)
                .where(
                    RoleModel.active.is_(True),
                    func.lower(func.trim(func.coalesce(RoleModel.name, ""))) == role_name.lower(),
                )
                .order_by(RoleModel.id.asc())
            ).scalars().first()
            return self._role_to_dict(row) if row else None

    def get_role_latest_local_timestamp(self, role: Optional[Dict[str, Any]]) -> Optional[datetime]:
        """Return the latest FIELDCRAFT timestamp for role conflict checks.

        Only use the role row timestamps here. Mapping timestamps change during
        sync attempts/failures and should not make FIELDCRAFT appear newer than the
        actual role data.
        """
        if not isinstance(role, dict):
            return None
        value = role.get("updated_at") or role.get("created_at")
        if not isinstance(value, datetime):
            return None
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    def get_current_mapping_by_role_id(self, *, tenant_id: str, service_id: Any, role_id: int) -> Optional[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                select(RoleExternalMapModel)
                .where(
                    RoleExternalMapModel.tenant_id == tenant_id,
                    RoleExternalMapModel.service_id == self._service_id_int(service_id),
                    RoleExternalMapModel.role_id == int(role_id),
                    self._is_current_mapping_clause(),
                    func.upper(func.coalesce(RoleExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .order_by(RoleExternalMapModel.id.desc())
            ).scalars().first()
            return self._mapping_to_dict(row) if row else None

    def get_current_mapping_by_external_service_id(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        external_service_id: str,
    ) -> Optional[Dict[str, Any]]:
        external_value = self._clean(external_service_id)
        if not external_value:
            return None
        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                select(RoleExternalMapModel)
                .where(
                    RoleExternalMapModel.tenant_id == tenant_id,
                    RoleExternalMapModel.service_id == self._service_id_int(service_id),
                    RoleExternalMapModel.external_service_id == external_value,
                    self._is_current_mapping_clause(),
                    func.upper(func.coalesce(RoleExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .order_by(RoleExternalMapModel.id.desc())
            ).scalars().first()
            return self._mapping_to_dict(row) if row else None

    def _create_mapping(
        self,
        session,
        *,
        tenant_id: str,
        service_id: Any,
        role_id: int,
        external_service_id: Optional[str],
        external_account_id: Optional[str],
        external_display_name: Optional[str],
        sync_status: str,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
        additional_info: Optional[Dict[str, Any]] = None,
        effective_at: Optional[datetime] = None,
        updated_at: Optional[datetime] = None,
    ) -> RoleExternalMapModel:
        now = updated_at or self._now()
        effective_ts = effective_at or now
        if effective_ts.tzinfo is None:
            effective_ts = effective_ts.replace(tzinfo=timezone.utc)
        normalized_additional_info = additional_info or {}
        time_info = (
            normalized_additional_info.get(self.TIME_INFO_KEY)
            if isinstance(normalized_additional_info, dict)
            else None
        )
        carried_time_item_id = (
            self._clean(time_info.get(self.TIME_CUSTOMFIELD_ITEM_ID_KEY))
            if isinstance(time_info, dict)
            else ""
        )
        row = RoleExternalMapModel(
            role_id=int(role_id),
            tenant_id=tenant_id,
            service_id=self._service_id_int(service_id),
            external_service_id=self._clean(external_service_id) or None,
            external_account_id=self._clean(external_account_id) or None,
            external_display_name=self._clean(external_display_name) or None,
            sync_status=self._clean(sync_status).upper() or self.STATUS_PENDING_PUSH,
            version=1,
            version_effective_date=None,
            version_expiry_date=None,
            endorsement_effective_date=effective_ts,
            endorsement_expiry_date=None,
            updated_at=now,
            sync_token=self._clean(sync_token) or None,
            reason=self._clean(reason) or None,
            additional_info=self._dump_json(normalized_additional_info),
            external_time_customfield_item_id=carried_time_item_id or None,
        )
        session.add(row)
        session.flush()
        return row

    def rotate_mapping_for_role(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        role_id: int,
        external_service_id: Optional[str],
        external_account_id: Optional[str],
        external_display_name: Optional[str],
        sync_status: str,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
        additional_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = self._now()
        with tenant_session_scope(tenant_id) as session:
            role = session.get(RoleModel, int(role_id))
            if not role:
                raise ValueError(f"Role {role_id} not found")

            existing_rows = session.execute(
                select(RoleExternalMapModel).where(
                    RoleExternalMapModel.tenant_id == tenant_id,
                    RoleExternalMapModel.service_id == self._service_id_int(service_id),
                    RoleExternalMapModel.role_id == int(role_id),
                    self._is_current_mapping_clause(),
                )
            ).scalars().all()
            for existing in existing_rows:
                existing.sync_status = self.STATUS_INVALID
                existing.version_expiry_date = now
                existing.endorsement_expiry_date = now
                existing.updated_at = now
                session.add(existing)

            new_row = self._create_mapping(
                session,
                tenant_id=tenant_id,
                service_id=service_id,
                role_id=int(role_id),
                external_service_id=external_service_id,
                external_account_id=external_account_id,
                external_display_name=external_display_name,
                sync_status=sync_status,
                sync_token=sync_token,
                reason=reason,
                additional_info=additional_info,
                effective_at=role.updated_at or role.created_at or now,
                updated_at=now,
            )
            return self._mapping_to_dict(new_row)

    def create_pending_push_mapping(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        role_id: int,
        external_service_id: Optional[str] = None,
        external_account_id: Optional[str] = None,
        external_display_name: Optional[str] = None,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
        additional_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not external_service_id:
            existing = self.get_current_mapping_by_role_id(tenant_id=tenant_id, service_id=service_id, role_id=role_id)
            if existing:
                external_service_id = existing.get("external_service_id")
                external_account_id = external_account_id if external_account_id is not None else existing.get("external_account_id")
                external_display_name = external_display_name if external_display_name is not None else existing.get("external_display_name")
                sync_token = sync_token if sync_token is not None else existing.get("sync_token")
                additional_info = additional_info if additional_info is not None else existing.get("additional_info")
        return self.rotate_mapping_for_role(
            tenant_id=tenant_id,
            service_id=service_id,
            role_id=int(role_id),
            external_service_id=external_service_id,
            external_account_id=external_account_id,
            external_display_name=external_display_name,
            sync_status=self.STATUS_PENDING_PUSH,
            sync_token=sync_token,
            reason=reason,
            additional_info=additional_info,
        )

    def ensure_pending_mappings_for_roles(self, *, tenant_id: str, service_id: Any) -> int:
        """Create pending mappings for new/changed roles.

        Active roles without a map are queued for creation. Existing mapped roles
        are queued again when the FIELDCRAFT role was updated after the last map update,
        including inactive roles so QuickBooks can be inactivated.
        """
        created = 0
        now = self._now()
        service_id_int = self._service_id_int(service_id)
        with tenant_session_scope(tenant_id) as session:
            roles = session.execute(select(RoleModel).order_by(RoleModel.id.asc())).scalars().all()
            for role in roles:
                role_id = int(role.id)
                role_name = self._clean(role.name)
                if not role_name:
                    continue

                current = session.execute(
                    select(RoleExternalMapModel)
                    .where(
                        RoleExternalMapModel.tenant_id == tenant_id,
                        RoleExternalMapModel.service_id == service_id_int,
                        RoleExternalMapModel.role_id == role_id,
                        self._is_current_mapping_clause(),
                        func.upper(func.coalesce(RoleExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                    )
                    .order_by(RoleExternalMapModel.id.desc())
                ).scalars().first()

                role_latest_at = role.updated_at or role.created_at or now
                if role_latest_at.tzinfo is None:
                    role_latest_at = role_latest_at.replace(tzinfo=timezone.utc)

                if not current:
                    if not self._bool(role.active):
                        continue
                    self._create_mapping(
                        session,
                        tenant_id=tenant_id,
                        service_id=service_id,
                        role_id=role_id,
                        external_service_id=None,
                        external_account_id=None,
                        external_display_name=role_name,
                        sync_status=self.STATUS_PENDING_PUSH,
                        reason='{"message":"Role queued for QuickBooks service item export.","code":"ROLE_PENDING_PUSH_CREATED"}',
                        effective_at=role_latest_at,
                        updated_at=now,
                    )
                    created += 1
                    continue

                current_status = self._clean(current.sync_status).upper()
                if current_status in {self.STATUS_PENDING_PUSH, self.STATUS_FAILED, self.STATUS_SYNC_FAILED}:
                    continue

                current_updated_at = current.updated_at or current.endorsement_effective_date or now
                if current_updated_at.tzinfo is None:
                    current_updated_at = current_updated_at.replace(tzinfo=timezone.utc)
                if role_latest_at > current_updated_at:
                    current.sync_status = self.STATUS_INVALID
                    current.version_expiry_date = now
                    current.endorsement_expiry_date = now
                    current.updated_at = now
                    session.add(current)
                    self._create_mapping(
                        session,
                        tenant_id=tenant_id,
                        service_id=service_id,
                        role_id=role_id,
                        external_service_id=current.external_service_id,
                        external_account_id=current.external_account_id,
                        external_display_name=current.external_display_name or role_name,
                        sync_status=self.STATUS_PENDING_PUSH,
                        sync_token=current.sync_token,
                        reason='{"message":"Role changed in FIELDCRAFT and was queued for QuickBooks service item update.","code":"ROLE_CHANGED_PENDING_PUSH"}',
                        additional_info=self._load_json(getattr(current, "additional_info", None)),
                        effective_at=role_latest_at,
                        updated_at=now,
                    )
                    created += 1
        return created

    def get_export_candidates(self, *, tenant_id: str, service_id: Any, limit: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(
                select(RoleExternalMapModel)
                .where(
                    RoleExternalMapModel.tenant_id == tenant_id,
                    RoleExternalMapModel.service_id == self._service_id_int(service_id),
                    self._is_current_mapping_clause(),
                    func.upper(func.coalesce(RoleExternalMapModel.sync_status, "")).in_(
                        [self.STATUS_PENDING_PUSH, self.STATUS_FAILED, self.STATUS_SYNC_FAILED]
                    ),
                )
                .order_by(RoleExternalMapModel.id.asc())
                .offset(int(offset))
                .limit(int(limit))
            ).scalars().all()

            role_ids = [int(row.role_id) for row in rows if row.role_id is not None]
            role_map: Dict[int, RoleModel] = {}
            if role_ids:
                role_rows = session.execute(select(RoleModel).where(RoleModel.id.in_(list(dict.fromkeys(role_ids))))).scalars().all()
                role_map = {int(role.id): role for role in role_rows}

            for mapping_row in rows:
                if mapping_row.role_id is None:
                    continue
                role_row = role_map.get(int(mapping_row.role_id))
                if not role_row:
                    continue
                mapping = self._mapping_to_dict(mapping_row)
                selected_account = self.get_mapping_income_account(mapping)
                if not self._clean(selected_account.get("external_id")):
                    continue
                results.append({"role": self._role_to_dict(role_row), "mapping": mapping})
        return results

    def mark_mapping_synced(
        self,
        *,
        tenant_id: str,
        mapping_id: int,
        external_service_id: Optional[str],
        external_account_id: Optional[str] = None,
        external_display_name: Optional[str] = None,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
        additional_info: Optional[Dict[str, Any]] = None,
    ) -> None:
        with tenant_session_scope(tenant_id) as session:
            row = session.get(RoleExternalMapModel, int(mapping_id))
            if not row:
                return
            if external_service_id is not None:
                row.external_service_id = self._clean(external_service_id) or row.external_service_id
            if external_account_id is not None:
                row.external_account_id = self._clean(external_account_id) or None
            if external_display_name is not None:
                row.external_display_name = self._clean(external_display_name) or None
            if sync_token is not None:
                row.sync_token = self._clean(sync_token) or None
            if additional_info is not None:
                merged_info = self._merge_additional_info(row.additional_info, additional_info)
                row.additional_info = self._dump_json(merged_info)
                merged_time_info = merged_info.get(self.TIME_INFO_KEY)
                if isinstance(merged_time_info, dict):
                    merged_item_id = self._clean(
                        merged_time_info.get(self.TIME_CUSTOMFIELD_ITEM_ID_KEY)
                    )
                    if merged_item_id:
                        row.external_time_customfield_item_id = merged_item_id
            row.sync_status = self.STATUS_SYNCED
            row.reason = self._clean(reason) or None
            row.updated_at = self._now()
            session.add(row)

    def mark_mapping_failed(
        self,
        *,
        tenant_id: str,
        mapping_id: int,
        status: Optional[str] = None,
        reason: Optional[str] = None,
        external_display_name: Optional[str] = None,
        sync_token: Optional[str] = None,
    ) -> None:
        with tenant_session_scope(tenant_id) as session:
            row = session.get(RoleExternalMapModel, int(mapping_id))
            if not row:
                return
            row.sync_status = status or self.STATUS_FAILED
            row.reason = self._clean(reason) or None
            # Do not update external_display_name while marking failures.
            # For role/service sync, FIELDCRAFT is the source of truth for the service name.
            # QuickBooks may return labels like "admin (deleted)" for inactive items;
            # storing that value makes the FIELDCRAFT role look deleted in the UI.
            if sync_token is not None:
                row.sync_token = self._clean(sync_token) or row.sync_token
            row.updated_at = self._now()
            session.add(row)

    def close_mapping_as_invalid(self, *, tenant_id: str, mapping_id: int, reason: Optional[str] = None) -> None:
        with tenant_session_scope(tenant_id) as session:
            row = session.get(RoleExternalMapModel, int(mapping_id))
            if not row:
                return
            row.sync_status = self.STATUS_INVALID
            row.reason = self._clean(reason) or row.reason
            row.version_expiry_date = self._now()
            row.endorsement_expiry_date = self._now()
            row.updated_at = self._now()
            session.add(row)

    def mark_all_current_mappings_pending_push(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        reason: Optional[str] = None,
    ) -> int:
        """Queue current role mappings so service sync can apply changed tenant-level COA config."""
        service_id_int = self._service_id_int(service_id)
        updated = 0
        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(
                select(RoleExternalMapModel)
                .where(
                    RoleExternalMapModel.tenant_id == tenant_id,
                    RoleExternalMapModel.service_id == service_id_int,
                    self._is_current_mapping_clause(),
                    func.upper(func.coalesce(RoleExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
            ).scalars().all()
            for row in rows:
                if self._clean(row.sync_status).upper() == self.STATUS_INVALID:
                    continue
                row.sync_status = self.STATUS_PENDING_PUSH
                row.reason = self._clean(reason) or row.reason
                row.updated_at = self._now()
                session.add(row)
                updated += 1
        return updated

    def get_mapping_income_account(self, mapping: Dict[str, Any] | None) -> Dict[str, Any]:
        """Return the COA snapshot stored on role_external_map.additional_info."""
        if not isinstance(mapping, dict):
            return {}
        additional_info = mapping.get("additional_info")
        if not isinstance(additional_info, dict):
            additional_info = self._load_json(additional_info)
        account = additional_info.get("qbo_service_income_account") if isinstance(additional_info, dict) else None
        return account if isinstance(account, dict) else {}

    def get_mapping_time_service_item(self, mapping: Dict[str, Any] | None) -> Dict[str, Any]:
        """Return normalized QuickBooks Time Service Item metadata.

        Older tenant schemas store the Time custom-field item ID in the dedicated
        ``external_time_customfield_item_id`` column, while newer code also stores
        the field and item IDs in ``additional_info.quickbooksTime``. Read both so
        existing mappings remain usable and the two storage representations can be
        repaired during the next successful sync.
        """
        if not isinstance(mapping, dict):
            return {}
        additional_info = mapping.get("additional_info")
        if not isinstance(additional_info, dict):
            additional_info = self._load_json(additional_info)
        raw_time_info = (
            additional_info.get(self.TIME_INFO_KEY)
            if isinstance(additional_info, dict)
            else None
        )
        time_info = dict(raw_time_info) if isinstance(raw_time_info, dict) else {}
        legacy_item_id = self._clean(mapping.get("external_time_customfield_item_id"))
        if legacy_item_id and not self._clean(
            time_info.get(self.TIME_CUSTOMFIELD_ITEM_ID_KEY)
        ):
            time_info[self.TIME_CUSTOMFIELD_ITEM_ID_KEY] = legacy_item_id
        return time_info

    def get_synced_mappings_missing_time_customfield_item(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        limit: int = 5000,
    ) -> List[Dict[str, Any]]:
        """Return synced role mappings that still need a Time Service Item ID."""
        service_id_int = self._service_id_int(service_id)
        results: List[Dict[str, Any]] = []
        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(
                select(RoleExternalMapModel, RoleModel)
                .join(RoleModel, RoleModel.id == RoleExternalMapModel.role_id)
                .where(
                    RoleExternalMapModel.tenant_id == tenant_id,
                    RoleExternalMapModel.service_id == service_id_int,
                    self._is_current_mapping_clause(),
                    func.upper(func.coalesce(RoleExternalMapModel.sync_status, "")) == self.STATUS_SYNCED,
                    func.trim(func.coalesce(RoleExternalMapModel.external_service_id, "")) != "",
                )
                .order_by(RoleExternalMapModel.id.asc())
                .limit(max(int(limit or 0), 1))
            ).all()

            # Convert ORM rows to plain dictionaries while the tenant session is
            # still open. Tenant sessions expire ORM attributes on commit, so
            # reading model fields after leaving this block can raise
            # DetachedInstanceError and prevent the QuickBooks Time lookup.
            for mapping_row, role_row in rows:
                mapping = self._mapping_to_dict(mapping_row)
                time_info = self.get_mapping_time_service_item(mapping)
                field_id = self._clean(time_info.get(self.TIME_CUSTOMFIELD_ID_KEY))
                item_id = self._clean(time_info.get(self.TIME_CUSTOMFIELD_ITEM_ID_KEY))
                if field_id and item_id:
                    # Keep the dedicated legacy/reporting column synchronized with
                    # the structured metadata. This repairs mappings created by an
                    # older code version without requiring a separate data script.
                    if not self._clean(mapping.get("external_time_customfield_item_id")):
                        mapping_row.external_time_customfield_item_id = item_id
                        session.add(mapping_row)
                    continue
                results.append(
                    {
                        "mapping_id": mapping.get("id"),
                        "role_id": mapping.get("role_id"),
                        "role_name": self._clean(role_row.name),
                        "external_service_id": mapping.get("external_service_id"),
                        "external_display_name": mapping.get("external_display_name"),
                        "additional_info": mapping.get("additional_info") or {},
                    }
                )
        return results

    def set_time_customfield_item_id(
        self,
        *,
        tenant_id: str,
        mapping_id: int,
        external_time_customfield_id: str,
        external_time_customfield_item_id: str,
        service_item_name: Optional[str] = None,
    ) -> None:
        """Store QuickBooks Time field/item IDs without changing Accounting state."""
        field_id = self._clean(external_time_customfield_id)
        item_id = self._clean(external_time_customfield_item_id)
        if not field_id or not item_id:
            raise ValueError("QuickBooks Time custom field ID and item ID are required.")

        with tenant_session_scope(tenant_id) as session:
            row = session.get(RoleExternalMapModel, int(mapping_id))
            if not row:
                raise ValueError(f"Role external mapping {mapping_id} was not found.")

            additional_info = self._load_json(row.additional_info)
            time_info = additional_info.get(self.TIME_INFO_KEY)
            if not isinstance(time_info, dict):
                time_info = {}
            time_info.update(
                {
                    self.TIME_CUSTOMFIELD_ID_KEY: field_id,
                    self.TIME_CUSTOMFIELD_ITEM_ID_KEY: item_id,
                    self.TIME_RESOLVED_AT_KEY: self._now().isoformat(),
                }
            )
            if self._clean(service_item_name):
                time_info[self.TIME_SERVICE_ITEM_NAME_KEY] = self._clean(service_item_name)
            additional_info[self.TIME_INFO_KEY] = time_info

            row.additional_info = self._dump_json(additional_info)
            row.external_time_customfield_item_id = item_id
            # Do not advance mapping.updated_at here. Role change detection compares
            # the FIELDCRAFT role timestamp with this Accounting-sync timestamp.
            session.add(row)

    def set_income_account_for_roles(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        role_ids: List[int],
        account: Dict[str, Any],
        reason: Optional[str] = None,
    ) -> int:
        """Store a per-role COA selection in role_external_map.additional_info and queue sync."""
        if not isinstance(account, dict) or not self._clean(account.get("external_id")):
            raise ValueError("Selected chart of account is not synced to QuickBooks.")
        service_id_int = self._service_id_int(service_id)
        role_id_values = [int(role_id) for role_id in role_ids if int(role_id or 0) > 0]
        if not role_id_values:
            return 0
        now = self._now()
        updated = 0
        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(
                select(RoleExternalMapModel)
                .where(
                    RoleExternalMapModel.tenant_id == tenant_id,
                    RoleExternalMapModel.service_id == service_id_int,
                    RoleExternalMapModel.role_id.in_(role_id_values),
                    self._is_current_mapping_clause(),
                    func.upper(func.coalesce(RoleExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
            ).scalars().all()
            existing_by_role = {int(row.role_id): row for row in rows if row.role_id is not None}
            for role_id in role_id_values:
                row = existing_by_role.get(role_id)
                if row is None:
                    role = session.get(RoleModel, role_id)
                    if not role:
                        continue
                    row = self._create_mapping(
                        session,
                        tenant_id=tenant_id,
                        service_id=service_id,
                        role_id=role_id,
                        external_service_id=None,
                        external_account_id=None,
                        external_display_name=self._clean(role.name) or None,
                        sync_status=self.STATUS_PENDING_PUSH,
                        reason=reason,
                        additional_info={"qbo_service_income_account": dict(account)},
                        effective_at=role.updated_at or role.created_at or now,
                        updated_at=now,
                    )
                else:
                    current_info = self._load_json(getattr(row, "additional_info", None))
                    current_info["qbo_service_income_account"] = dict(account)
                    row.additional_info = self._dump_json(current_info)
                    row.sync_status = self.STATUS_PENDING_PUSH
                    row.reason = self._clean(reason) or row.reason
                    row.updated_at = now
                    session.add(row)
                updated += 1
        return updated

    def get_service_role_cdc_page(
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
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 25), 1), 100)
        offset = (page - 1) * page_size
        service_id_int = self._service_id_int(service_id)
        search_term = self._clean(search)
        normalized_status = self._clean(sync_status).upper()

        with tenant_session_scope(tenant_id) as session:
            latest_ids = (
                select(func.max(RoleExternalMapModel.id).label("map_id"))
                .where(
                    RoleExternalMapModel.tenant_id == tenant_id,
                    RoleExternalMapModel.service_id == service_id_int,
                    RoleExternalMapModel.role_id.is_not(None),
                    self._is_current_mapping_clause(),
                    func.upper(func.coalesce(RoleExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .group_by(RoleExternalMapModel.role_id)
                .subquery()
            )
            base_stmt = (
                select(RoleExternalMapModel, RoleModel)
                .where(RoleExternalMapModel.id.in_(select(latest_ids.c.map_id)))
                .outerjoin(RoleModel, RoleModel.id == RoleExternalMapModel.role_id)
            )

            if mapped_only:
                base_stmt = base_stmt.where(RoleExternalMapModel.external_service_id.is_not(None))
            if normalized_status and normalized_status != "ALL":
                base_stmt = base_stmt.where(func.upper(func.coalesce(RoleExternalMapModel.sync_status, "")) == normalized_status)
            if search_term:
                like_term = f"%{search_term.lower()}%"
                base_stmt = base_stmt.where(
                    or_(
                        func.lower(func.coalesce(RoleModel.name, "")).like(like_term),
                        func.lower(func.coalesce(RoleModel.description, "")).like(like_term),
                        func.lower(func.coalesce(RoleExternalMapModel.external_service_id, "")).like(like_term),
                        func.lower(func.coalesce(RoleExternalMapModel.external_display_name, "")).like(like_term),
                        func.lower(func.coalesce(cast(RoleModel.id, Text), "")).like(like_term),
                    )
                )

            total_records = int(session.execute(select(func.count()).select_from(base_stmt.subquery())).scalar() or 0)
            rows_raw = session.execute(
                base_stmt.order_by(RoleExternalMapModel.updated_at.desc(), RoleExternalMapModel.id.desc())
                .offset(offset)
                .limit(page_size)
            ).all()

            rows: List[Dict[str, Any]] = []
            for mapping_row, role_row in rows_raw:
                mapping = self._mapping_to_dict(mapping_row)
                role = self._role_to_dict(role_row) if role_row else {}
                time_service_item = self.get_mapping_time_service_item(mapping)
                rows.append(
                    {
                        "roleId": role.get("id") or mapping.get("role_id"),
                        "name": role.get("name"),
                        "description": role.get("description"),
                        "active": role.get("active"),
                        "builtin": role.get("builtin"),
                        "mappingId": mapping.get("id"),
                        "externalServiceId": mapping.get("external_service_id"),
                        "externalAccountId": mapping.get("external_account_id"),
                        "externalDisplayName": mapping.get("external_display_name"),
                        "syncStatus": mapping.get("sync_status"),
                        "syncToken": mapping.get("sync_token"),
                        "mappingUpdatedAt": mapping.get("updated_at"),
                        "reason": mapping.get("reason"),
                        "additionalInfo": mapping.get("additional_info") or {},
                        "additional_info": mapping.get("additional_info") or {},
                        "serviceIncomeAccount": self.get_mapping_income_account(mapping),
                        "externalTimeCustomFieldId": time_service_item.get(
                            self.TIME_CUSTOMFIELD_ID_KEY
                        ),
                        "externalTimeCustomFieldItemId": time_service_item.get(
                            self.TIME_CUSTOMFIELD_ITEM_ID_KEY
                        ),
                        "source": "FIELDCRAFT",
                    }
                )

            raw_counts = session.execute(
                select(RoleExternalMapModel.sync_status, func.count())
                .where(RoleExternalMapModel.id.in_(select(latest_ids.c.map_id)))
                .group_by(RoleExternalMapModel.sync_status)
            ).all()
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
