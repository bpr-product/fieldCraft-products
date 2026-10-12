from __future__ import annotations

import json
import os
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Text, cast, func, or_, select, union

from source.Utils import constants as const
from source.dal.db_session import tenant_advisory_lock, tenant_session_scope
from source.dal.vendor_models import VendorExternalMapModel, VendorModel


class VendorRepository:
    """Vendor repository aligned to the production vendor sync design.

    Business truth:
      - vendor
    Integration truth:
      - vendor_external_map

    Vendor rows are updated in-place.
    Mapping rows are append-only logical history driven by sync_status.
    """

    STATUS_PENDING_PUSH = "PENDING_PUSH"
    STATUS_SYNCED = "SYNCED"
    STATUS_FAILED = "FAILED"
    STATUS_IMPORT_FAILED = "IMPORT_FAILED"
    STATUS_INVALID = "INVALID"
    EXPORTABLE_SYNC_STATUSES = (STATUS_PENDING_PUSH, STATUS_FAILED)

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

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

    def _service_id_int(self, service_id: Any) -> int:
        try:
            return int(service_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid service_id value: {service_id!r}") from exc

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()

    def _clean_lower(self, value: Any) -> str:
        return self._clean(value).lower()

    def _reason_payload(self, reason: Any) -> Dict[str, Any]:
        if not reason:
            return {}
        try:
            parsed = json.loads(str(reason))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _safe_uuid_string(self, value: Any) -> Optional[str]:
        raw = self._clean(value)
        if not raw:
            return None
        try:
            return str(uuid.UUID(raw))
        except Exception:
            return raw

    def _safe_uuid_obj(self, value: Any):
        raw = self._clean(value)
        if not raw:
            return None
        try:
            return uuid.UUID(raw)
        except Exception:
            return None

    def _normalize_vendor_row(self, row: VendorModel) -> Dict[str, Any]:
        return {
            "id": int(row.id),
            "account_no": row.account_no,
            "address": row.address,
            "check_name": row.check_name,
            "city": row.city,
            "company_name": row.company_name,
            "country": row.country,
            "created_at": row.created_at,
            "created_by": row.created_by,
            "description": row.description,
            "display_name": row.display_name,
            const.KEY_EMAIL: row.email,
            const.KEY_PHONE: row.primary_mobile_number,
            "primary_mobile_number": row.primary_mobile_number,
            "services": row.services,
            "state": row.state,
            const.KEY_STATUS: row.status,
            "updated_at": row.updated_at,
            "vendor_uid": row.vendor_uid,
            "website": row.website,
            "zip_code": row.zip_code,
            "additional_info": row.additional_info,
            "type": row.type,
            "categories": row.categories,
            "pay_terms": row.pay_terms,
            "lead_time": row.lead_time,
            "routing_no": row.routing_no,
            "location": row.location,
        }

    def _mapping_to_dict(self, row: VendorExternalMapModel) -> Dict[str, Any]:
        return {
            "id": int(row.id),
            "vendor_id": int(row.vendor_id) if row.vendor_id is not None else None,
            "tenant_id": row.tenant_id,
            "service_id": int(row.service_id) if row.service_id is not None else None,
            "external_vendor_id": row.external_vendor_id,
            "external_account_id": row.external_account_id,
            "sync_status": row.sync_status,
            "version": int(row.version or 1),
            "version_effective_date": row.version_effective_date,
            "version_expiry_date": row.version_expiry_date,
            "endorsement_effective_date": row.endorsement_effective_date,
            "endorsement_expiry_date": row.endorsement_expiry_date,
            "vendor_uuid": str(row.vendor_uuid) if row.vendor_uuid is not None else None,
            "sync_token": row.sync_token,
            "reason": row.reason,
            "updated_at": row.updated_at,
        }

    def _mapping_sync_projection(self, row: Dict[str, Any]) -> Dict[str, str]:
        return {
            "display_name": self._clean(row.get("display_name")),
            "company_name": self._clean(row.get("company_name")),
            "check_name": self._clean(row.get("check_name")),
            const.KEY_EMAIL: self._clean_lower(row.get(const.KEY_EMAIL)),
            const.KEY_PHONE: self._clean(row.get(const.KEY_PHONE) or row.get("primary_mobile_number")),
            "address": self._clean(row.get("address")),
            "city": self._clean(row.get("city")),
            "state": self._clean(row.get("state")),
            "zip_code": self._clean(row.get("zip_code")),
            "country": self._clean(row.get("country")),
            "website": self._clean(row.get("website")),
            "description": self._clean(row.get("description")),
            const.KEY_STATUS: self._clean(row.get(const.KEY_STATUS)),
        }

    def sync_projection_changed(self, left: Dict[str, Any], right: Dict[str, Any]) -> bool:
        return self._mapping_sync_projection(left) != self._mapping_sync_projection(right)

    def get_vendor_latest_local_timestamp(
        self,
        vendor: Optional[Dict[str, Any]],
        mapping: Optional[Dict[str, Any]] = None,
    ) -> Optional[datetime]:
        candidates: List[datetime] = []
        for row in (vendor or {}, mapping or {}):
            for key in ("updated_at", "endorsement_effective_date", "version_effective_date"):
                value = row.get(key)
                if value is None:
                    continue
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                candidates.append(value)
        return max(candidates) if candidates else None

    # ------------------------------------------------------------------
    # Vendor business rows
    # ------------------------------------------------------------------

    def get_vendor_by_id(self, tenant_id: str, vendor_id: int) -> Optional[Dict[str, Any]]:
        if vendor_id is None:
            return None
        with tenant_session_scope(tenant_id) as session:
            row = session.get(VendorModel, int(vendor_id))
            return self._normalize_vendor_row(row) if row else None

    def get_current_vendor_by_id(self, tenant_id: str, vendor_id: int) -> Optional[Dict[str, Any]]:
        return self.get_vendor_by_id(tenant_id=tenant_id, vendor_id=vendor_id)

    def get_vendors_for_tenant(self, tenant_id: str, limit: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            stmt = select(VendorModel).order_by(VendorModel.id.asc()).offset(int(offset)).limit(int(limit))
            rows = session.execute(stmt).scalars().all()
            return [self._normalize_vendor_row(row) for row in rows]

    def get_vendor_id_by_email(self, tenant_id: str, email: str) -> Optional[int]:
        email_value = self._clean_lower(email)
        if not email_value:
            return None
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(VendorModel)
                .where(func.lower(func.coalesce(VendorModel.email, "")) == email_value)
                .order_by(VendorModel.id.desc())
            )
            row = session.execute(stmt).scalars().first()
            return int(row.id) if row else None

    def create_vendor_for_tenant(self, tenant_id: str, vendor_data: Dict[str, Any], created_by: str = "qbo-import") -> int:
        now = self._now()
        email_value = self._clean(vendor_data.get(const.KEY_EMAIL))
        existing_id = self.get_vendor_id_by_email(tenant_id, email_value) if email_value else None
        if existing_id:
            return existing_id

        with tenant_session_scope(tenant_id) as session:
            row = VendorModel(
                account_no=self._clean(vendor_data.get("account_no")) or None,
                address=self._clean(vendor_data.get("address")) or None,
                approved_by=vendor_data.get("approved_by"),
                check_name=self._clean(vendor_data.get("check_name")) or None,
                city=self._clean(vendor_data.get("city")) or None,
                company_name=self._clean(vendor_data.get("company_name")) or None,
                country=self._clean(vendor_data.get("country")) or None,
                created_at=vendor_data.get("created_at") or now,
                created_by=vendor_data.get("created_by") or const.SERVICE_NAME_QUICKBOOKS,
                description=self._clean(vendor_data.get("description")) or None,
                display_name=self._clean(vendor_data.get("display_name")) or None,
                email=email_value or None,
                is_email_verified=vendor_data.get("is_email_verified"),
                lead_time=vendor_data.get("lead_time"),
                location=self._clean(vendor_data.get("location")) or None,
                pay_terms=vendor_data.get("pay_terms"),
                primary_mobile_number=self._clean(vendor_data.get("primary_mobile_number") or vendor_data.get(const.KEY_PHONE)) or None,
                routing_no=self._clean(vendor_data.get("routing_no")) or None,
                services=self._clean(vendor_data.get("services")) or os.getenv(const.ENV_QBO_VENDOR_DEFAULT_SERVICES, const.QBO_VENDOR_DEFAULT_SERVICES),
                state=self._clean(vendor_data.get("state")) or None,
                status=self._clean(vendor_data.get(const.KEY_STATUS) or vendor_data.get("status")) or "Active",
                updated_at=vendor_data.get("updated_at") or now,
                vendor_uid=self._safe_uuid_string(vendor_data.get("vendor_uid") or uuid.uuid4()),
                website=self._clean(vendor_data.get("website")) or None,
                zip_code=self._clean(vendor_data.get("zip_code")) or None,
                additional_info=self._clean(vendor_data.get("additional_info")) or None,
                type=self._clean(vendor_data.get("type")) or "otherVendors",
                categories=vendor_data.get("categories") if vendor_data.get("categories") is not None else "[]",
                subcontractor_id=vendor_data.get("subcontractor_id"),
            )
            session.add(row)
            session.flush()
            return int(row.id)

    def insert_vendor_from_qbo(
        self,
        tenant_id: str,
        *,
        qbo_vendor: Dict[str, Any],
        display_name: str,
        company_name: str,
        check_name: str,
        email: str,
        phone: str,
        address: str,
        city: str,
        state: str,
        zip_code: str,
        country: str,
        website: str,
        description: str,
        status: str,
        vendor_type: Optional[str] = None,
        categories_json: str = "[]",
        services: str = "",
        created_by: str = "",
        account_no: Optional[str] = None,
        routing_no: Optional[str] = None,
    ) -> int:
        additional_info = json.dumps({"qboVendor": qbo_vendor}, default=str)[:65000]
        return self.create_vendor_for_tenant(
            tenant_id=tenant_id,
            vendor_data={
                "account_no": account_no,
                "address": address,
                "check_name": check_name,
                "city": city,
                "company_name": company_name,
                "country": country,
                "created_by": const.SERVICE_NAME_QUICKBOOKS,
                "description": description,
                "display_name": display_name,
                const.KEY_EMAIL: email,
                const.KEY_PHONE: phone,
                "services": services,
                "state": state,
                const.KEY_STATUS: status,
                "vendor_uid": str(uuid.uuid4()),
                "website": website,
                "zip_code": zip_code,
                "additional_info": additional_info,
                "type": vendor_type or "otherVendors",
                "categories": categories_json,
                "routing_no": routing_no,
            },
            created_by=created_by or "qbo-import",
        )

    def update_vendor_from_qbo(self, tenant_id: str, vendor_id: int, qbo_vendor: Dict[str, Any]) -> Dict[str, Any]:
        payload = self.build_vendor_payload_from_qbo(qbo_vendor)
        with tenant_session_scope(tenant_id) as session:
            row = session.get(VendorModel, int(vendor_id))
            if not row:
                raise ValueError(f"Vendor {vendor_id} not found")
            row.display_name = payload.get("display_name") or row.display_name
            row.company_name = payload.get("company_name") or row.company_name
            row.check_name = payload.get("check_name") or row.check_name
            row.email = payload.get(const.KEY_EMAIL) or row.email
            row.primary_mobile_number = payload.get(const.KEY_PHONE) or row.primary_mobile_number
            row.address = payload.get("address") or row.address
            row.city = payload.get("city") or row.city
            row.state = payload.get("state") or row.state
            row.zip_code = payload.get("zip_code") or row.zip_code
            row.country = payload.get("country") or row.country
            row.website = payload.get("website") or row.website
            row.description = payload.get("description") or row.description
            row.status = payload.get(const.KEY_STATUS) or row.status
            row.account_no = payload.get("account_no") or row.account_no
            row.updated_at = self._now()
            session.add(row)
            session.flush()
            return self._normalize_vendor_row(row)

    def build_vendor_payload_from_qbo(self, qbo_vendor: Dict[str, Any]) -> Dict[str, Any]:
        bill_addr = qbo_vendor.get("BillAddr") or {}
        address_lines: List[str] = []
        if isinstance(bill_addr, dict):
            for key in ("Line1", "Line2", "Line3", "Line4", "Line5"):
                value = self._clean(bill_addr.get(key))
                if value:
                    address_lines.append(value)
        email_block = qbo_vendor.get("PrimaryEmailAddr") or {}
        phone_block = qbo_vendor.get("PrimaryPhone") or {}
        mobile_block = qbo_vendor.get("Mobile") or {}
        web_block = qbo_vendor.get("WebAddr") or {}
        display_name = self._clean(qbo_vendor.get(const.QBO_KEY_DISPLAY_NAME))
        company_name = self._clean(qbo_vendor.get("CompanyName")) or display_name
        check_name = self._clean(qbo_vendor.get("PrintOnCheckName")) or display_name
        phone_value = ""
        if isinstance(phone_block, dict):
            phone_value = self._clean(phone_block.get("FreeFormNumber"))
        if not phone_value and isinstance(mobile_block, dict):
            phone_value = self._clean(mobile_block.get("FreeFormNumber"))
        phone_value = "".join(c for c in phone_value if c.isdigit())
        return {
            "display_name": display_name or company_name or check_name or f"Vendor {self._clean(qbo_vendor.get(const.QBO_KEY_ID))}",
            "company_name": company_name or display_name,
            "check_name": check_name or display_name,
            const.KEY_EMAIL: self._clean(email_block.get("Address")) if isinstance(email_block, dict) else "",
            const.KEY_PHONE: phone_value,
            "address": ", ".join(address_lines),
            "city": self._clean(bill_addr.get("City")) if isinstance(bill_addr, dict) else "",
            "state": self._clean(bill_addr.get("CountrySubDivisionCode")) if isinstance(bill_addr, dict) else "",
            "zip_code": self._clean(bill_addr.get("PostalCode")) if isinstance(bill_addr, dict) else "",
            "country": self._clean(bill_addr.get("Country")) if isinstance(bill_addr, dict) else "",
            "website": self._clean(web_block.get("URI")) if isinstance(web_block, dict) else "",
            "description": self._clean(qbo_vendor.get("Notes")),
            const.KEY_STATUS: "Inactive" if qbo_vendor.get("Active") is False else "Active",
            "account_no": self._clean(qbo_vendor.get("AcctNum")) or None,
        }

    # ------------------------------------------------------------------
    # Mapping rows
    # ------------------------------------------------------------------

    def _get_next_mapping_version(self, session, tenant_id: str, service_id: Any, vendor_id: Optional[int], external_vendor_id: Optional[str]) -> int:
        stmt = select(func.max(VendorExternalMapModel.version)).where(
            VendorExternalMapModel.tenant_id == tenant_id,
            VendorExternalMapModel.service_id == self._service_id_int(service_id),
        )
        if vendor_id is not None:
            stmt = stmt.where(VendorExternalMapModel.vendor_id == int(vendor_id))
        elif self._clean(external_vendor_id):
            stmt = stmt.where(VendorExternalMapModel.external_vendor_id == self._clean(external_vendor_id))
        value = session.execute(stmt).scalar_one_or_none()
        return int(value or 0) + 1

    def _create_mapping(
        self,
        session,
        *,
        tenant_id: str,
        service_id: Any,
        vendor_id: Optional[int],
        vendor_uuid: Optional[str],
        external_vendor_id: Optional[str],
        external_account_id: Optional[str],
        sync_status: str,
        sync_token: Optional[str],
        reason: Optional[str],
        version: Optional[int] = None,
        effective_at: Optional[datetime] = None,
        updated_at: Optional[datetime] = None,
    ) -> VendorExternalMapModel:
        effective_ts = effective_at or updated_at or self._now()
        updated_ts = updated_at or effective_ts
        row = VendorExternalMapModel(
            vendor_id=int(vendor_id) if vendor_id is not None else None,
            tenant_id=tenant_id,
            service_id=self._service_id_int(service_id),
            external_vendor_id=self._clean(external_vendor_id) or None,
            external_account_id=self._clean(external_account_id) or None,
            sync_status=sync_status,
            version=int(version or self._get_next_mapping_version(session, tenant_id, service_id, vendor_id, external_vendor_id)),
            version_effective_date=None,
            version_expiry_date=None,
            endorsement_effective_date=effective_ts,
            endorsement_expiry_date=None,
            vendor_uuid=self._safe_uuid_obj(vendor_uuid),
            sync_token=self._clean(sync_token) or None,
            reason=self._clean(reason) or None,
            updated_at=updated_ts,
        )
        session.add(row)
        session.flush()
        return row

    def get_mapping_by_id(
        self,
        tenant_id: str,
        service_id: Any,
        mapping_id: int,
    ) -> Optional[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                select(VendorExternalMapModel).where(
                    VendorExternalMapModel.id == int(mapping_id),
                    VendorExternalMapModel.tenant_id == tenant_id,
                    VendorExternalMapModel.service_id == self._service_id_int(service_id),
                )
            ).scalars().first()
            return self._mapping_to_dict(row) if row else None

    def export_mapping_lock(self, tenant_id: str, service_id: Any, mapping_id: int):
        """Serialize QuickBooks export for one vendor mapping across auto/manual sync."""
        lock_key = f"quickbooks-vendor-export:{tenant_id}:{self._service_id_int(service_id)}:{int(mapping_id)}"
        return tenant_advisory_lock(tenant_id, lock_key)

    def get_export_candidate_by_mapping_id(
        self,
        tenant_id: str,
        service_id: Any,
        mapping_id: int,
    ) -> Optional[Dict[str, Any]]:
        mapping = self.get_mapping_by_id(
            tenant_id=tenant_id,
            service_id=service_id,
            mapping_id=mapping_id,
        )
        if not mapping:
            return None
        if self._clean(mapping.get("sync_status")).upper() not in self.EXPORTABLE_SYNC_STATUSES:
            return None
        vendor_id = mapping.get("vendor_id")
        if not vendor_id:
            return None
        current = self.get_current_mapping_by_vendor_id(
            tenant_id=tenant_id,
            service_id=service_id,
            vendor_id=int(vendor_id),
        )
        if not current or int(current.get("id") or 0) != int(mapping_id):
            return None
        vendor = self.get_current_vendor_by_id(tenant_id=tenant_id, vendor_id=int(vendor_id))
        if not vendor:
            return None
        return {"vendor": vendor, "mapping": mapping}

    def get_current_mapping_by_vendor_id(self, tenant_id: str, service_id: Any, vendor_id: int) -> Optional[Dict[str, Any]]:
        if vendor_id is None:
            return None
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(VendorExternalMapModel)
                .where(
                    VendorExternalMapModel.vendor_id == int(vendor_id),
                    VendorExternalMapModel.tenant_id == tenant_id,
                    VendorExternalMapModel.service_id == self._service_id_int(service_id),
                    func.upper(func.coalesce(VendorExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .order_by(VendorExternalMapModel.id.desc())
            )
            row = session.execute(stmt).scalars().first()
            return self._mapping_to_dict(row) if row else None

    def get_current_mapping_by_external_vendor_id(self, tenant_id: str, service_id: Any, external_vendor_id: str) -> Optional[Dict[str, Any]]:
        external_value = self._clean(external_vendor_id)
        if not external_value:
            return None
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(VendorExternalMapModel)
                .where(
                    VendorExternalMapModel.external_vendor_id == external_value,
                    VendorExternalMapModel.tenant_id == tenant_id,
                    VendorExternalMapModel.service_id == self._service_id_int(service_id),
                    func.upper(func.coalesce(VendorExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .order_by(VendorExternalMapModel.id.desc())
            )
            row = session.execute(stmt).scalars().first()
            return self._mapping_to_dict(row) if row else None

    def get_export_candidates(
        self,
        tenant_id: str,
        service_id: Any,
        limit: int = 500,
        offset: int = 0,
        include_mapping_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        with tenant_session_scope(tenant_id) as session:
            stmt = select(VendorExternalMapModel).where(
                VendorExternalMapModel.tenant_id == tenant_id,
                VendorExternalMapModel.service_id == self._service_id_int(service_id),
                func.upper(func.coalesce(VendorExternalMapModel.sync_status, "")).in_(self.EXPORTABLE_SYNC_STATUSES),
            )
            included = sorted({int(value) for value in (include_mapping_ids or []) if value is not None})
            if included:
                stmt = stmt.where(VendorExternalMapModel.id.in_(included))
            stmt = (
                stmt.order_by(VendorExternalMapModel.id.asc())
                .offset(int(offset))
                .limit(int(limit))
            )
            rows = session.execute(stmt).scalars().all()
            for mapping_row in rows:
                vendor = session.get(VendorModel, int(mapping_row.vendor_id)) if mapping_row.vendor_id is not None else None
                if not vendor:
                    continue
                results.append({
                    "vendor": self._normalize_vendor_row(vendor),
                    "mapping": self._mapping_to_dict(mapping_row),
                })
        return results

    def mark_mapping_synced(
        self,
        *,
        tenant_id: str,
        mapping_id: int,
        external_vendor_id: Optional[str],
        external_account_id: Optional[str] = None,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        with tenant_session_scope(tenant_id) as session:
            row = session.get(VendorExternalMapModel, int(mapping_id))
            if not row:
                return
            if external_vendor_id is not None:
                row.external_vendor_id = self._clean(external_vendor_id) or row.external_vendor_id
            if external_account_id is not None:
                row.external_account_id = self._clean(external_account_id) or None
            if sync_token is not None:
                row.sync_token = self._clean(sync_token) or None
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
    ) -> None:
        with tenant_session_scope(tenant_id) as session:
            row = session.get(VendorExternalMapModel, int(mapping_id))
            if not row:
                return
            row.sync_status = status or self.STATUS_FAILED
            row.reason = self._clean(reason) or None
            row.updated_at = self._now()
            session.add(row)

    def close_mapping_as_invalid(self, tenant_id: str, mapping_id: int) -> None:
        with tenant_session_scope(tenant_id) as session:
            row = session.get(VendorExternalMapModel, int(mapping_id))
            if not row:
                return
            row.sync_status = self.STATUS_INVALID
            row.updated_at = self._now()
            session.add(row)

    def rotate_mapping_for_vendor(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        vendor_id: Optional[int],
        external_vendor_id: Optional[str],
        external_account_id: Optional[str],
        sync_status: str,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = self._now()
        with tenant_session_scope(tenant_id) as session:
            vendor_uuid = None
            effective_at = now
            if vendor_id is not None:
                vendor = session.get(VendorModel, int(vendor_id))
                if vendor:
                    vendor_uuid = vendor.vendor_uid
                    effective_at = vendor.updated_at or now
            existing_stmt = (
                select(VendorExternalMapModel)
                .where(
                    VendorExternalMapModel.tenant_id == tenant_id,
                    VendorExternalMapModel.service_id == self._service_id_int(service_id),
                    func.upper(func.coalesce(VendorExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .order_by(VendorExternalMapModel.id.desc())
            )
            if vendor_id is not None:
                existing_stmt = existing_stmt.where(VendorExternalMapModel.vendor_id == int(vendor_id))
            elif self._clean(external_vendor_id):
                existing_stmt = existing_stmt.where(VendorExternalMapModel.external_vendor_id == self._clean(external_vendor_id))
            existing_rows = session.execute(existing_stmt).scalars().all()
            for existing in existing_rows:
                existing.sync_status = self.STATUS_INVALID
                existing.updated_at = now
                session.add(existing)
            new_row = self._create_mapping(
                session,
                tenant_id=tenant_id,
                service_id=service_id,
                vendor_id=vendor_id,
                vendor_uuid=vendor_uuid,
                external_vendor_id=external_vendor_id,
                external_account_id=external_account_id,
                sync_status=sync_status,
                sync_token=sync_token,
                reason=reason,
                effective_at=effective_at,
                updated_at=now,
            )
            return self._mapping_to_dict(new_row)

    def create_pending_push_mapping(
        self,
        tenant_id: str,
        service_id: Any,
        vendor_id: int,
        external_vendor_id: Optional[str] = None,
        external_account_id: Optional[str] = None,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not external_vendor_id:
            existing = self.get_current_mapping_by_vendor_id(tenant_id=tenant_id, service_id=service_id, vendor_id=vendor_id)
            if existing:
                external_vendor_id = existing.get("external_vendor_id")
                if external_account_id is None:
                    external_account_id = existing.get("external_account_id")
                if sync_token is None:
                    sync_token = existing.get("sync_token")
        return self.rotate_mapping_for_vendor(
            tenant_id=tenant_id,
            service_id=service_id,
            vendor_id=vendor_id,
            external_vendor_id=external_vendor_id,
            external_account_id=external_account_id,
            sync_status=self.STATUS_PENDING_PUSH,
            sync_token=sync_token,
            reason=reason,
        )

    def create_external_mapping(
        self,
        tenant_id: str,
        vendor_id: int,
        external_vendor_id: str,
        external_account_id: Optional[str] = None,
        status: str = STATUS_SYNCED,
        service_id: Any = 6,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.rotate_mapping_for_vendor(
            tenant_id=tenant_id,
            service_id=service_id,
            vendor_id=vendor_id,
            external_vendor_id=external_vendor_id,
            external_account_id=external_account_id,
            sync_status=status,
            sync_token=sync_token,
            reason=reason,
        )

    def upsert_import_failed_mapping(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        external_vendor_id: str,
        external_account_id: Optional[str] = None,
        vendor_id: Optional[int] = None,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        external_value = self._clean(external_vendor_id)
        if not external_value:
            raise ValueError("external_vendor_id is required for IMPORT_FAILED mapping")
        now = self._now()
        with tenant_session_scope(tenant_id) as session:
            current = session.execute(
                select(VendorExternalMapModel)
                .where(
                    VendorExternalMapModel.tenant_id == tenant_id,
                    VendorExternalMapModel.service_id == self._service_id_int(service_id),
                    VendorExternalMapModel.external_vendor_id == external_value,
                    func.upper(func.coalesce(VendorExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .order_by(VendorExternalMapModel.id.desc())
            ).scalars().first()
            if current:
                if vendor_id is not None:
                    current.vendor_id = int(vendor_id)
                    vendor = session.get(VendorModel, int(vendor_id))
                    current.vendor_uuid = self._safe_uuid_obj(vendor.vendor_uid) if vendor and vendor.vendor_uid else current.vendor_uuid
                if external_account_id is not None:
                    current.external_account_id = self._clean(external_account_id) or None
                if sync_token is not None:
                    current.sync_token = self._clean(sync_token) or None
                current.sync_status = self.STATUS_IMPORT_FAILED
                current.reason = self._clean(reason) or None
                current.updated_at = now
                session.add(current)
                session.flush()
                return self._mapping_to_dict(current)

            vendor_uuid = None
            effective_at = now
            if vendor_id is not None:
                vendor = session.get(VendorModel, int(vendor_id))
                if vendor:
                    vendor_uuid = vendor.vendor_uid
                    effective_at = vendor.updated_at or now
            row = self._create_mapping(
                session,
                tenant_id=tenant_id,
                service_id=service_id,
                vendor_id=vendor_id,
                vendor_uuid=vendor_uuid,
                external_vendor_id=external_value,
                external_account_id=external_account_id,
                sync_status=self.STATUS_IMPORT_FAILED,
                sync_token=sync_token,
                reason=reason,
                effective_at=effective_at,
                updated_at=now,
            )
            return self._mapping_to_dict(row)

    def get_open_import_failed_external_vendor_ids(self, tenant_id: str, service_id: Any) -> List[str]:
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(VendorExternalMapModel.external_vendor_id)
                .where(
                    VendorExternalMapModel.tenant_id == tenant_id,
                    VendorExternalMapModel.service_id == self._service_id_int(service_id),
                    VendorExternalMapModel.sync_status == self.STATUS_IMPORT_FAILED,
                    VendorExternalMapModel.external_vendor_id.is_not(None),
                    func.upper(func.coalesce(VendorExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .order_by(VendorExternalMapModel.updated_at.asc(), VendorExternalMapModel.id.asc())
            )
            values = []
            for row in session.execute(stmt).all():
                if row and row[0]:
                    values.append(str(row[0]))
            return values

    # ------------------------------------------------------------------
    # UI helper / debugging
    # ------------------------------------------------------------------

    def get_vendor_cdc_page(
        self,
        tenant_id: str,
        service_id: Any,
        *,
        page: int = 1,
        page_size: int = 25,
        search: Optional[str] = None,
        sync_status: Optional[str] = None,
        mapped_only: bool = False,
        changed_since: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Paginated vendor list driven by vendor_external_map as the base table.

        Only vendors that exist in vendor_external_map are shown — no unmapped
        FIELDCRAFT vendors appear.  vendor_external_map is the source of truth;
        the vendor table is joined for display info only.

        Two categories of rows are included:
        - Mapped vendors  (vendor_id IS NOT NULL): latest mapping per vendor_id
        - Orphan IMPORT_FAILED (vendor_id IS NULL): latest mapping per external_vendor_id
        """
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 25), 1), 100)
        offset = (page - 1) * page_size
        service_id_int = self._service_id_int(service_id)
        search_term = self._clean(search)
        normalized_status = self._clean(sync_status).upper()
        changed_since_value = self._parse_changed_since(changed_since)

        with tenant_session_scope(tenant_id) as session:

            # ── Step 1: latest mapping ID per vendor_id (vendor exists in FIELDCRAFT) ──
            max_eed_mapped = (
                select(
                    VendorExternalMapModel.vendor_id.label("vendor_id"),
                    func.max(VendorExternalMapModel.endorsement_effective_date).label("max_eed"),
                )
                .where(
                    VendorExternalMapModel.tenant_id == tenant_id,
                    VendorExternalMapModel.service_id == service_id_int,
                    func.upper(func.coalesce(VendorExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                    VendorExternalMapModel.vendor_id.is_not(None),
                )
                .group_by(VendorExternalMapModel.vendor_id)
                .subquery()
            )
            latest_mapped_ids = (
                select(func.max(VendorExternalMapModel.id).label("map_id"))
                .join(
                    max_eed_mapped,
                    (VendorExternalMapModel.vendor_id == max_eed_mapped.c.vendor_id)
                    & (VendorExternalMapModel.endorsement_effective_date == max_eed_mapped.c.max_eed),
                )
                .where(
                    VendorExternalMapModel.tenant_id == tenant_id,
                    VendorExternalMapModel.service_id == service_id_int,
                    func.upper(func.coalesce(VendorExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                    VendorExternalMapModel.vendor_id.is_not(None),
                )
                .group_by(VendorExternalMapModel.vendor_id)
            )

            # ── Step 2: latest mapping ID per external_vendor_id (orphan IMPORT_FAILED) ──
            latest_orphan_ids = (
                select(func.max(VendorExternalMapModel.id).label("map_id"))
                .where(
                    VendorExternalMapModel.tenant_id == tenant_id,
                    VendorExternalMapModel.service_id == service_id_int,
                    VendorExternalMapModel.vendor_id.is_(None),
                    func.upper(func.coalesce(VendorExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .group_by(VendorExternalMapModel.external_vendor_id)
            )

            # ── Step 3: combine both sets of IDs ──────────────────────────────
            combined_ids = union(latest_mapped_ids, latest_orphan_ids).subquery()

            # ── Step 4: base query — map as driver, vendor as optional join ───
            base_stmt = (
                select(VendorExternalMapModel, VendorModel)
                .where(VendorExternalMapModel.id.in_(select(combined_ids.c.map_id)))
                .outerjoin(VendorModel, VendorModel.id == VendorExternalMapModel.vendor_id)
            )

            if search_term:
                like_term = f"%{search_term.lower()}%"
                base_stmt = base_stmt.where(
                    or_(
                        func.lower(func.coalesce(VendorModel.display_name, "")).like(like_term),
                        func.lower(func.coalesce(VendorModel.company_name, "")).like(like_term),
                        func.lower(func.coalesce(VendorModel.email, "")).like(like_term),
                        func.lower(func.coalesce(cast(VendorModel.vendor_uid, Text), "")).like(like_term),
                        func.lower(func.coalesce(VendorExternalMapModel.external_vendor_id, "")).like(like_term),
                        func.lower(func.coalesce(VendorExternalMapModel.reason, "")).like(like_term),
                    )
                )

            if normalized_status and normalized_status != "ALL":
                base_stmt = base_stmt.where(
                    func.upper(func.coalesce(VendorExternalMapModel.sync_status, "")) == normalized_status
                )

            if changed_since_value is not None:
                base_stmt = base_stmt.where(
                    or_(
                        VendorExternalMapModel.updated_at >= changed_since_value,
                        VendorModel.updated_at >= changed_since_value,
                    )
                )

            total_records = int(session.execute(select(func.count()).select_from(base_stmt.subquery())).scalar() or 0)

            paged_stmt = (
                base_stmt
                .order_by(VendorExternalMapModel.updated_at.desc(), VendorExternalMapModel.id.desc())
                .offset(offset)
                .limit(page_size)
            )
            records = session.execute(paged_stmt).all()

            rows: List[Dict[str, Any]] = []
            for mapping_row, vendor_row in records:
                mapping = self._mapping_to_dict(mapping_row)
                vendor = self._normalize_vendor_row(vendor_row) if vendor_row else {}
                qbo_id = self._clean(mapping.get("external_vendor_id"))
                display_name = self._clean(vendor.get("display_name") or vendor.get("company_name"))
                reason_payload = self._reason_payload(mapping.get("reason"))
                qbo_display_name = self._clean(
                    reason_payload.get("display_name")
                    or reason_payload.get("displayName")
                    or reason_payload.get("qbo_display_name")
                )
                qbo_email = self._clean(reason_payload.get("email") or reason_payload.get("qbo_email"))
                is_qbo_origin = self._clean(vendor.get("created_by")) == const.SERVICE_NAME_QUICKBOOKS
                is_qbo_only_failure = vendor_row is None and self._clean(mapping.get("sync_status")).upper() == self.STATUS_IMPORT_FAILED
                source = "QuickBooks" if (is_qbo_origin or is_qbo_only_failure) else ("FIELDCRAFT" if vendor_row is not None else None)
                rows.append({
                    "vendorId": vendor.get("id"),
                    "vendorUuid": vendor.get("vendor_uid"),
                    "displayName": display_name or vendor.get("email") or qbo_display_name or (f"QBO Vendor {qbo_id}" if qbo_id else f"Mapping #{mapping['id']}"),
                    "companyName": vendor.get("company_name") or qbo_display_name or None,
                    "email": vendor.get("email") or qbo_email or None,
                    "phone": vendor.get("primary_mobile_number"),
                    "website": vendor.get("website"),
                    "vendorStatus": vendor.get("status"),
                    "mappingId": mapping.get("id"),
                    "externalVendorId": mapping.get("external_vendor_id"),
                    "externalAccountId": mapping.get("external_account_id"),
                    "syncStatus": mapping.get("sync_status") or self.STATUS_IMPORT_FAILED,
                    "syncToken": mapping.get("sync_token"),
                    "mappingVersion": mapping.get("version"),
                    "endorsementEffectiveDate": mapping.get("endorsement_effective_date"),
                    "mappingUpdatedAt": mapping.get("updated_at"),
                    "linked": vendor_row is not None,
                    "reason": mapping.get("reason"),
                    "createdBy": vendor.get("created_by"),
                    "isQboOrigin": is_qbo_origin,
                    "source": source,
                })

            # ── Summary: counts from the combined mapping set (unfiltered) ────
            summary_stmt = (
                select(VendorExternalMapModel.sync_status, func.count())
                .where(VendorExternalMapModel.id.in_(select(combined_ids.c.map_id)))
                .group_by(VendorExternalMapModel.sync_status)
            )
            raw_counts = session.execute(summary_stmt).all()
            status_counts: Dict[str, int] = {}
            for status, count in raw_counts:
                key = (status or "UNKNOWN").upper()
                status_counts[key] = int(count or 0)

            total_vendors = int(sum(status_counts.values()))
            summary = {
                "totalVendors": total_vendors,
                "mappedVendors": total_vendors,
                "unmappedVendors": 0,
                "syncedVendors": status_counts.get("SYNCED", 0),
                "pendingPushVendors": status_counts.get("PENDING_PUSH", 0),
                "failedVendors": status_counts.get("FAILED", 0),
                "importFailedVendors": status_counts.get("IMPORT_FAILED", 0),
            }

            return {
                "rows": rows,
                "summary": summary,
                "pagination": {
                    "page": page,
                    "pageSize": page_size,
                    "totalRecords": total_records,
                    "totalPages": (total_records + page_size - 1) // page_size if total_records else 0,
                },
            }
