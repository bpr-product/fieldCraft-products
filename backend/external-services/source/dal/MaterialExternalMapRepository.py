from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

from source.dal.db_session import tenant_session_scope
from source.dal.material_external_map_models import (
    MaterialCostModel,
    MaterialExternalMapModel,
    MaterialModel,
    VendorMaterialMapModel,
)
from source.dal.vendor_models import VendorModel


logger = logging.getLogger(__name__)


class MaterialExternalMapRepository:
    """Repository for tenant-level material_external_map using SQLAlchemy.

    The table is provider-neutral. ABC Supply uses it now, and future material
    providers can reuse the same source/external columns without adding another
    provider-specific table.
    """

    @staticmethod
    def build_external_account_id(
        *,
        branch_number: Optional[Any] = None,
        ship_to_number: Optional[Any] = None,
        bill_to_number: Optional[Any] = None,
    ) -> str:
        parts: List[str] = []
        if bill_to_number not in (None, ""):
            parts.append(f"billTo:{str(bill_to_number).strip()}")
        if ship_to_number not in (None, ""):
            parts.append(f"shipTo:{str(ship_to_number).strip()}")
        if branch_number not in (None, ""):
            parts.append(f"branch:{str(branch_number).strip()}")
        return "|".join(parts)

    @staticmethod
    def _clean_id(value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _service_id(value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _uuid_value(value: Any) -> Optional[UUID]:
        if value in (None, ""):
            return None
        if isinstance(value, UUID):
            return value
        try:
            return UUID(str(value).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _decode_additional_info(value: Any) -> Any:
        if value in (None, ""):
            return None
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(str(value))
        except Exception:
            return value

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, Decimal):
            return str(value)
        return value

    def _mapping_to_dict(self, row: MaterialExternalMapModel) -> Dict[str, Any]:
        result = {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "service_id": row.service_id,
            "source_entity_type": row.source_entity_type,
            "source_entity_id": row.source_entity_id,
            "source_entity_uuid": str(row.source_entity_uuid) if row.source_entity_uuid else None,
            "external_item_id": row.external_item_id,
            "external_account_id": row.external_account_id,
            "external_display_name": row.external_display_name,
            "external_item_type": row.external_item_type,
            "sync_status": row.sync_status,
            "version": row.version,
            "version_effective_date": row.version_effective_date,
            "version_expiry_date": row.version_expiry_date,
            "endorsement_effective_date": row.endorsement_effective_date,
            "endorsement_expiry_date": row.endorsement_expiry_date,
            "sync_token": row.sync_token,
            "reason": row.reason,
            "additional_info": self._decode_additional_info(row.additional_info),
            "created_by": str(row.created_by) if row.created_by else None,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        return result

    def _active_mapping_filter(self):
        return and_(
            MaterialExternalMapModel.endorsement_expiry_date.is_(None),
            func.coalesce(MaterialExternalMapModel.sync_status, "") != "INVALID",
        )

    def _external_account_filter(self, external_account_id: Optional[str]):
        return func.coalesce(MaterialExternalMapModel.external_account_id, "") == str(external_account_id or "").strip()

    def _current_by_external_item_stmt(
        self,
        *,
        tenant_id: str,
        service_id: int,
        external_item_id: str,
        external_account_id: Optional[str],
    ):
        return (
            select(MaterialExternalMapModel)
            .where(
                MaterialExternalMapModel.tenant_id == tenant_id,
                MaterialExternalMapModel.service_id == service_id,
                MaterialExternalMapModel.external_item_id == external_item_id,
                self._external_account_filter(external_account_id),
                self._active_mapping_filter(),
            )
            .order_by(MaterialExternalMapModel.id.desc())
            .limit(1)
        )

    def get_current_by_external_item(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        external_item_id: str,
        external_account_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        service_id_int = self._service_id(service_id)
        item_id = str(external_item_id or "").strip()
        if not tenant_id or service_id_int is None or not item_id:
            return None

        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                self._current_by_external_item_stmt(
                    tenant_id=tenant_id,
                    service_id=service_id_int,
                    external_item_id=item_id,
                    external_account_id=external_account_id,
                )
            ).scalars().first()
            return self._mapping_to_dict(row) if row else None

    def get_current_by_external_items(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        external_item_ids: Iterable[str],
        external_account_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        service_id_int = self._service_id(service_id)
        item_ids = sorted({str(value or "").strip() for value in external_item_ids if str(value or "").strip()})
        if not tenant_id or service_id_int is None or not item_ids:
            return []

        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(MaterialExternalMapModel)
                .where(
                    MaterialExternalMapModel.tenant_id == tenant_id,
                    MaterialExternalMapModel.service_id == service_id_int,
                    MaterialExternalMapModel.external_item_id.in_(item_ids),
                    self._external_account_filter(external_account_id),
                    self._active_mapping_filter(),
                )
                .order_by(MaterialExternalMapModel.id.desc())
            )
            rows = session.execute(stmt).scalars().all()
            return [self._mapping_to_dict(row) for row in rows]


    def find_vendor_materials_for_mapping(
        self,
        *,
        tenant_id: str,
        vendor_id: Any,
        materials: Iterable[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Resolve newly-created FIELDCRAFT material ids in one tenant DB query.

        The Tenant addMaterials endpoint currently returns only a success message.
        After a successful batch create, ABC import needs the local FIELDCRAFT ids to
        write material_external_map. This lookup keeps the existing Tenant API
        contract unchanged and avoids calling getVendorMaterials once per item.
        """
        vendor_id_int = self._clean_id(vendor_id)
        material_list = list(materials or [])
        if not tenant_id or vendor_id_int is None:
            return []

        item_codes = sorted({
            str((material or {}).get("itemCode") or (material or {}).get("item_code") or "").strip()
            for material in material_list
            if str((material or {}).get("itemCode") or (material or {}).get("item_code") or "").strip()
        })
        names = sorted({
            str((material or {}).get("name") or "").strip().lower().replace("\n", " ")
            for material in material_list
            if str((material or {}).get("name") or "").strip()
        })
        if not item_codes and not names:
            return []

        conditions = []
        if item_codes:
            conditions.append(MaterialCostModel.item_code.in_(item_codes))
        if names:
            conditions.append(func.replace(func.lower(func.trim(MaterialModel.name)), "\n", " ").in_(names))

        latest_cost = (
            select(
                MaterialCostModel.vendor_material_map_id.label("vendor_material_map_id"),
                func.max(MaterialCostModel.id).label("material_cost_id"),
            )
            .group_by(MaterialCostModel.vendor_material_map_id)
            .subquery()
        )

        stmt = (
            select(
                VendorMaterialMapModel.id.label("vendorMaterialMapId"),
                VendorMaterialMapModel.material_id.label("materialId"),
                VendorMaterialMapModel.vendor_id.label("vendorId"),
                MaterialCostModel.id.label("materialCostId"),
                MaterialCostModel.item_code.label("itemCode"),
                MaterialCostModel.cost.label("cost"),
                MaterialCostModel.unit.label("unit"),
                MaterialModel.name.label("name"),
                MaterialModel.grade.label("grade"),
                MaterialModel.manufacturer.label("manufacturer"),
            )
            .select_from(VendorMaterialMapModel)
            .join(MaterialModel, MaterialModel.id == VendorMaterialMapModel.material_id)
            .outerjoin(latest_cost, latest_cost.c.vendor_material_map_id == VendorMaterialMapModel.id)
            .outerjoin(MaterialCostModel, MaterialCostModel.id == latest_cost.c.material_cost_id)
            .where(VendorMaterialMapModel.vendor_id == vendor_id_int)
            .where(or_(*conditions))
            .order_by(VendorMaterialMapModel.id.desc(), MaterialCostModel.id.desc())
        )

        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(stmt).mappings().all()
            return [{key: self._json_safe(value) for key, value in dict(row).items()} for row in rows]

    def upsert_current_mapping(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        source_entity_type: str,
        source_entity_id: Optional[Any],
        source_entity_uuid: Optional[Any] = None,
        external_item_id: str,
        external_account_id: Optional[str] = None,
        external_display_name: Optional[str] = None,
        external_item_type: str = "MATERIAL",
        sync_status: str = "SYNCED",
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
        additional_info: Optional[Dict[str, Any]] = None,
        created_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        service_id_int = self._service_id(service_id)
        source_id = self._clean_id(source_entity_id)
        source_uuid = self._uuid_value(source_entity_uuid)
        item_id = str(external_item_id or "").strip()
        account_id = str(external_account_id or "").strip() or None
        source_type = (str(source_entity_type or "").strip() or "vendor_material_map").lower()
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if service_id_int is None:
            raise ValueError("service_id is required")
        if not item_id:
            raise ValueError("external_item_id is required")
        if source_id is None and source_uuid is None:
            raise ValueError("source_entity_id or source_entity_uuid is required")

        payload = json.dumps(additional_info or {}, default=str, separators=(",", ":"))
        created_by_value = self._uuid_value(created_by)
        now = datetime.now(timezone.utc)

        try:
            return self._upsert_current_mapping_once(
                tenant_id=tenant_id,
                service_id_int=service_id_int,
                source_type=source_type,
                source_id=source_id,
                source_uuid=source_uuid,
                item_id=item_id,
                account_id=account_id,
                external_display_name=external_display_name,
                external_item_type=external_item_type,
                sync_status=sync_status,
                sync_token=sync_token,
                reason=reason,
                payload=payload,
                created_by_value=created_by_value,
                now=now,
            )
        except IntegrityError:
            logger.info(
                "[MaterialExternalMapRepository] concurrent material_external_map insert detected for service_id=%s external_item_id=%s; retrying as update",
                service_id_int,
                item_id,
            )
            return self._upsert_current_mapping_once(
                tenant_id=tenant_id,
                service_id_int=service_id_int,
                source_type=source_type,
                source_id=source_id,
                source_uuid=source_uuid,
                item_id=item_id,
                account_id=account_id,
                external_display_name=external_display_name,
                external_item_type=external_item_type,
                sync_status=sync_status,
                sync_token=sync_token,
                reason=reason,
                payload=payload,
                created_by_value=created_by_value,
                now=datetime.now(timezone.utc),
            )

    def _upsert_current_mapping_once(
        self,
        *,
        tenant_id: str,
        service_id_int: int,
        source_type: str,
        source_id: Optional[int],
        source_uuid: Optional[UUID],
        item_id: str,
        account_id: str,
        external_display_name: Optional[str],
        external_item_type: str,
        sync_status: str,
        sync_token: Optional[str],
        reason: Optional[str],
        payload: str,
        created_by_value: Optional[UUID],
        now: datetime,
    ) -> Dict[str, Any]:
        with tenant_session_scope(tenant_id) as session:
            existing = session.execute(
                self._current_by_external_item_stmt(
                    tenant_id=tenant_id,
                    service_id=service_id_int,
                    external_item_id=item_id,
                    external_account_id=account_id,
                )
            ).scalars().first()

            if existing:
                existing.source_entity_type = source_type
                existing.source_entity_id = source_id
                existing.source_entity_uuid = source_uuid
                existing.external_display_name = external_display_name
                existing.external_item_type = external_item_type
                existing.sync_status = sync_status
                existing.sync_token = sync_token
                existing.reason = reason
                existing.additional_info = payload
                existing.updated_at = now
                session.flush()
                session.refresh(existing)
                return {"inserted": False, "row": self._mapping_to_dict(existing)}

            row = MaterialExternalMapModel(
                tenant_id=tenant_id,
                service_id=service_id_int,
                source_entity_type=source_type,
                source_entity_id=source_id,
                source_entity_uuid=source_uuid,
                external_item_id=item_id,
                external_account_id=account_id,
                external_display_name=external_display_name,
                external_item_type=external_item_type,
                sync_status=sync_status,
                version=1,
                version_effective_date=now,
                endorsement_effective_date=now,
                sync_token=sync_token,
                reason=reason,
                additional_info=payload,
                created_by=created_by_value,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return {"inserted": True, "row": self._mapping_to_dict(row)}

    def list_imported_materials(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        external_item_ids: Optional[Iterable[str]] = None,
        external_account_id: Optional[str] = None,
        vendor_id: Optional[Any] = None,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        service_id_int = self._service_id(service_id)
        if not tenant_id or service_id_int is None:
            return []

        item_ids = sorted({str(value or "").strip() for value in (external_item_ids or []) if str(value or "").strip()})
        vendor_id_int = self._clean_id(vendor_id)
        search_text = str(search or "").strip()
        limit_value = max(1, min(int(limit or 100), 500))
        offset_value = max(0, int(offset or 0))

        latest_cost = (
            select(
                MaterialCostModel.vendor_material_map_id.label("vendor_material_map_id"),
                func.max(MaterialCostModel.id).label("material_cost_id"),
            )
            .group_by(MaterialCostModel.vendor_material_map_id)
            .subquery()
        )

        stmt = (
            select(
                MaterialExternalMapModel.id.label("mapping_id"),
                MaterialExternalMapModel.tenant_id,
                MaterialExternalMapModel.service_id,
                MaterialExternalMapModel.source_entity_type,
                MaterialExternalMapModel.source_entity_id,
                MaterialExternalMapModel.external_item_id,
                MaterialExternalMapModel.external_account_id,
                MaterialExternalMapModel.external_display_name,
                MaterialExternalMapModel.external_item_type,
                MaterialExternalMapModel.sync_status,
                MaterialExternalMapModel.reason,
                MaterialExternalMapModel.additional_info,
                MaterialExternalMapModel.created_by,
                MaterialExternalMapModel.created_at,
                MaterialExternalMapModel.updated_at,
                VendorMaterialMapModel.material_id,
                VendorMaterialMapModel.vendor_id,
                MaterialModel.name.label("material_name"),
                MaterialModel.manufacturer,
                MaterialModel.tags,
                MaterialModel.unit.label("material_unit"),
                MaterialModel.grade,
                MaterialCostModel.id.label("material_cost_id"),
                MaterialCostModel.item_code,
                MaterialCostModel.cost,
                MaterialCostModel.unit.label("cost_unit"),
                VendorModel.company_name.label("vendor_name"),
            )
            .select_from(MaterialExternalMapModel)
            .outerjoin(
                VendorMaterialMapModel,
                and_(
                    func.lower(MaterialExternalMapModel.source_entity_type) == "vendor_material_map",
                    MaterialExternalMapModel.source_entity_id == VendorMaterialMapModel.id,
                ),
            )
            .outerjoin(MaterialModel, MaterialModel.id == VendorMaterialMapModel.material_id)
            .outerjoin(latest_cost, latest_cost.c.vendor_material_map_id == VendorMaterialMapModel.id)
            .outerjoin(MaterialCostModel, MaterialCostModel.id == latest_cost.c.material_cost_id)
            .outerjoin(VendorModel, VendorModel.id == VendorMaterialMapModel.vendor_id)
            .where(
                MaterialExternalMapModel.tenant_id == tenant_id,
                MaterialExternalMapModel.service_id == service_id_int,
                self._active_mapping_filter(),
            )
            .order_by(func.coalesce(MaterialExternalMapModel.updated_at, MaterialExternalMapModel.created_at).desc(), MaterialExternalMapModel.id.desc())
            .limit(limit_value)
            .offset(offset_value)
        )

        if item_ids:
            stmt = stmt.where(MaterialExternalMapModel.external_item_id.in_(item_ids))
        if external_account_id is not None:
            stmt = stmt.where(self._external_account_filter(external_account_id))
        if vendor_id_int is not None:
            stmt = stmt.where(VendorMaterialMapModel.vendor_id == vendor_id_int)
        if search_text:
            like_value = f"%{search_text.lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(func.coalesce(MaterialExternalMapModel.external_item_id, "")).like(like_value),
                    func.lower(func.coalesce(MaterialExternalMapModel.external_display_name, "")).like(like_value),
                    func.lower(func.coalesce(MaterialModel.name, "")).like(like_value),
                    func.lower(func.coalesce(MaterialCostModel.item_code, "")).like(like_value),
                    func.lower(func.coalesce(VendorModel.company_name, "")).like(like_value),
                )
            )

        with tenant_session_scope(tenant_id) as session:
            rows = session.execute(stmt).mappings().all()
            results: List[Dict[str, Any]] = []
            for row in rows:
                record = {key: self._json_safe(value) for key, value in dict(row).items()}
                record["additional_info"] = self._decode_additional_info(record.get("additional_info"))
                results.append(record)
            return results


    def count_imported_materials(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        external_item_ids: Optional[Iterable[str]] = None,
        external_account_id: Optional[str] = None,
        vendor_id: Optional[Any] = None,
        search: Optional[str] = None,
    ) -> int:
        service_id_int = self._service_id(service_id)
        if not tenant_id or service_id_int is None:
            return 0

        item_ids = sorted({str(value or "").strip() for value in (external_item_ids or []) if str(value or "").strip()})
        vendor_id_int = self._clean_id(vendor_id)
        search_text = str(search or "").strip()

        latest_cost = (
            select(
                MaterialCostModel.vendor_material_map_id.label("vendor_material_map_id"),
                func.max(MaterialCostModel.id).label("material_cost_id"),
            )
            .group_by(MaterialCostModel.vendor_material_map_id)
            .subquery()
        )

        stmt = (
            select(func.count(func.distinct(MaterialExternalMapModel.id)))
            .select_from(MaterialExternalMapModel)
            .outerjoin(
                VendorMaterialMapModel,
                and_(
                    func.lower(MaterialExternalMapModel.source_entity_type) == "vendor_material_map",
                    MaterialExternalMapModel.source_entity_id == VendorMaterialMapModel.id,
                ),
            )
            .outerjoin(MaterialModel, MaterialModel.id == VendorMaterialMapModel.material_id)
            .outerjoin(latest_cost, latest_cost.c.vendor_material_map_id == VendorMaterialMapModel.id)
            .outerjoin(MaterialCostModel, MaterialCostModel.id == latest_cost.c.material_cost_id)
            .outerjoin(VendorModel, VendorModel.id == VendorMaterialMapModel.vendor_id)
            .where(
                MaterialExternalMapModel.tenant_id == tenant_id,
                MaterialExternalMapModel.service_id == service_id_int,
                self._active_mapping_filter(),
            )
        )

        if item_ids:
            stmt = stmt.where(MaterialExternalMapModel.external_item_id.in_(item_ids))
        if external_account_id is not None:
            stmt = stmt.where(self._external_account_filter(external_account_id))
        if vendor_id_int is not None:
            stmt = stmt.where(VendorMaterialMapModel.vendor_id == vendor_id_int)
        if search_text:
            like_value = f"%{search_text.lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(func.coalesce(MaterialExternalMapModel.external_item_id, "")).like(like_value),
                    func.lower(func.coalesce(MaterialExternalMapModel.external_display_name, "")).like(like_value),
                    func.lower(func.coalesce(MaterialModel.name, "")).like(like_value),
                    func.lower(func.coalesce(MaterialCostModel.item_code, "")).like(like_value),
                    func.lower(func.coalesce(VendorModel.company_name, "")).like(like_value),
                )
            )

        with tenant_session_scope(tenant_id) as session:
            return int(session.execute(stmt).scalar() or 0)
