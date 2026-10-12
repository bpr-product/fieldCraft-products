from __future__ import annotations

import logging
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import Text, and_, cast, func, or_, select, text
from sqlalchemy.orm import aliased

from source.Utils import constants as const
from source.dal.customer_models import (
    OPEN_END_AT,
    CustomerExternalMapModel,
    CustomerModel,
    CustomerTenantModel,
)
from source.dal.db_session import (
    acquire_transaction_lock,
    acquire_transaction_locks,
    customer_session_scope,
)


logger = logging.getLogger(__name__)


class CustomerRepository:
    """Customer repository aligned to the new domain model.

    Business truth:
      - customers
    Integration truth:
      - customer_external_maps
    Tenant scope:
      - customers_tenant
    """

    OPEN_END_AT = OPEN_END_AT
    STATUS_PENDING_PUSH = "PENDING_PUSH"
    STATUS_SYNCED = "SYNCED"
    STATUS_FAILED = "FAILED"
    STATUS_INVALID = "INVALID"
    STATUS_IMPORT_FAILED = "IMPORT_FAILED"
    STATUS_SYNC_FAILED = "SYNC_FAILED"

    # Keep real-time and manual outbound Customer export on the same status rules.
    EXPORTABLE_SYNC_STATUSES = (STATUS_PENDING_PUSH, STATUS_FAILED)
    EXPORT_CLAIM_KEY = "customerExportClaim"
    EXPORT_CLAIM_TTL_SECONDS = 300

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _service_id_int(self, service_id: Any) -> int:
        try:
            return int(service_id)
        except Exception:
            return 0

    @staticmethod
    def _clean(value: Any) -> str:
        """Normalize optional identifier values used in mapping lookups."""
        return str(value or "").strip()

    def _customer_uuid_lock_key(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        customer_uuid: Any,
    ) -> str:
        return (
            f"qbo-customer-uuid:{self._clean(tenant_id)}:"
            f"{self._service_id_int(service_id)}:{self._clean(customer_uuid)}"
        )

    def _customer_external_lock_key(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        external_customer_id: Any,
    ) -> str:
        return (
            f"qbo-customer-external:{self._clean(tenant_id)}:"
            f"{self._service_id_int(service_id)}:{self._clean(external_customer_id)}"
        )

    def _acquire_customer_uuid_lock(
        self,
        session,
        *,
        tenant_id: str,
        service_id: Any,
        customer_uuid: Any,
    ) -> None:
        normalized_uuid = self._clean(customer_uuid)
        if not normalized_uuid:
            return
        acquire_transaction_lock(
            session,
            self._customer_uuid_lock_key(
                tenant_id=tenant_id,
                service_id=service_id,
                customer_uuid=normalized_uuid,
            ),
        )

    def _is_current_customer_clause(self):
        now = self._now()
        return and_(
            CustomerModel.version_effective_date <= now,
            CustomerModel.version_expiry_date >= now,
            CustomerModel.endorsement_effective_date <= now,
            CustomerModel.endorsement_expiry_date >= now,
            CustomerModel.is_deleted.is_(False),
        )

    def find_customer_uids_by_display_name(self, tenant_id: str, search_term: Any) -> set[str]:
        """Return current tenant customer UUIDs matching user-facing customer fields.

        Scalar columns are selected instead of ORM entities so no customer model leaves
        the customer DB session and becomes detached.
        """
        term = self._clean(search_term).lower()
        if not term:
            return set()

        like = f"%{term}%"
        full_name = func.lower(
            func.trim(
                func.coalesce(CustomerModel.firstname, "")
                + " "
                + func.coalesce(CustomerModel.lastname, "")
            )
        )
        try:
            with customer_session_scope() as session:
                rows = session.execute(
                    select(CustomerModel.customer_uuid)
                    .join(CustomerTenantModel, CustomerTenantModel.customer_id == CustomerModel.id)
                    .where(
                        CustomerTenantModel.tenant_id == tenant_id,
                        self._is_current_customer_clause(),
                        or_(
                            func.lower(func.coalesce(CustomerModel.firstname, "")).like(like),
                            func.lower(func.coalesce(CustomerModel.lastname, "")).like(like),
                            full_name.like(like),
                            func.lower(func.coalesce(CustomerModel.email, "")).like(like),
                        ),
                    )
                    .distinct()
                ).scalars().all()
        except Exception:
            logger.warning(
                "Could not search customer names for QuickBooks view | tenant=%s",
                tenant_id,
                exc_info=True,
            )
            return set()

        return {self._clean(value) for value in rows if self._clean(value)}

    def get_customer_display_names(
        self, tenant_id: str, customer_uids: Iterable[Any]
    ) -> Dict[str, str]:
        """Return current tenant customer names keyed by customer UUID.

        The query returns scalar values while the customer session is open. This avoids
        leaking SQLAlchemy ORM instances across the customer/tenant database boundary.
        """
        normalized = {self._clean(value) for value in customer_uids if self._clean(value)}
        if not normalized:
            return {}

        try:
            with customer_session_scope() as session:
                rows = session.execute(
                    select(
                        CustomerModel.customer_uuid,
                        CustomerModel.firstname,
                        CustomerModel.lastname,
                        CustomerModel.email,
                        CustomerModel.account_no,
                        CustomerModel.version,
                        CustomerModel.id,
                    )
                    .join(CustomerTenantModel, CustomerTenantModel.customer_id == CustomerModel.id)
                    .where(
                        CustomerTenantModel.tenant_id == tenant_id,
                        CustomerModel.customer_uuid.in_(sorted(normalized)),
                        self._is_current_customer_clause(),
                    )
                    .order_by(
                        CustomerModel.customer_uuid.asc(),
                        CustomerModel.version.desc(),
                        CustomerModel.id.desc(),
                    )
                ).all()

                names: Dict[str, str] = {}
                for customer_uuid, firstname, lastname, email, account_no, _, _ in rows:
                    uid = self._clean(customer_uuid)
                    if not uid or uid in names:
                        continue
                    name = " ".join(
                        part
                        for part in (self._clean(firstname), self._clean(lastname))
                        if part
                    ).strip()
                    name = name or self._clean(email) or self._clean(account_no)
                    if name:
                        names[uid] = name
                return names
        except Exception:
            logger.warning(
                "Could not load customer names for QuickBooks view | tenant=%s",
                tenant_id,
                exc_info=True,
            )
            return {}

    def _is_current_mapping_clause(self):
        now = self._now()
        return and_(
            CustomerExternalMapModel.version_effective_date <= now,
            CustomerExternalMapModel.version_expiry_date >= now,
            CustomerExternalMapModel.endorsement_effective_date <= now,
            CustomerExternalMapModel.endorsement_expiry_date >= now,
        )

    def _current_customer_ids_subquery(self, tenant_id: Optional[str] = None):
        """Return the current physical row id for each logical customer UUID."""
        stmt = (
            select(
                CustomerModel.customer_uuid.label("customer_uuid"),
                func.max(CustomerModel.id).label("customer_id"),
            )
            .where(self._is_current_customer_clause())
        )
        if tenant_id is not None:
            stmt = stmt.join(
                CustomerTenantModel,
                CustomerTenantModel.customer_id == CustomerModel.id,
            ).where(CustomerTenantModel.tenant_id == tenant_id)
        return stmt.group_by(CustomerModel.customer_uuid).subquery()

    @staticmethod
    def _customer_uuid_for_id(session, customer_id: int) -> Optional[str]:
        customer = session.get(CustomerModel, int(customer_id))
        return customer.customer_uuid if customer else None

    def _current_mapping_stmt_for_customer_uuid(
        self,
        *,
        tenant_id: str,
        customer_uuid: Any,
        service_id: Any | None = None,
    ):
        """Build a current mapping lookup using the stable customer UUID.

        Older mapping rows may not have customer_uuid populated. In that case,
        resolve the UUID from the historical customer row referenced by
        customer_id so existing integrations continue to work.
        """
        mapped_customer = aliased(CustomerModel)
        current_customer_ids = self._current_customer_ids_subquery()
        logical_customer_uuid = func.coalesce(
            CustomerExternalMapModel.customer_uuid,
            mapped_customer.customer_uuid,
        )
        stmt = (
            select(CustomerExternalMapModel)
            .outerjoin(
                mapped_customer,
                mapped_customer.id == CustomerExternalMapModel.customer_id,
            )
            .join(
                current_customer_ids,
                current_customer_ids.c.customer_uuid == logical_customer_uuid,
            )
            .where(
                current_customer_ids.c.customer_uuid == customer_uuid,
                CustomerExternalMapModel.tenant_id == tenant_id,
                self._is_current_mapping_clause(),
            )
        )
        if service_id is not None:
            stmt = stmt.where(
                CustomerExternalMapModel.service_id == self._service_id_int(service_id)
            )
        return stmt.order_by(
            CustomerExternalMapModel.updated_at.desc(),
            CustomerExternalMapModel.id.desc(),
        )

    def _close_other_current_mappings_for_customer_uuid(
        self,
        session,
        *,
        tenant_id: str,
        service_id: Any,
        customer_uuid: str,
        keep_mapping_id: int,
        now: datetime,
    ) -> None:
        """Close older active mappings for one logical, versioned customer.

        FIELDCRAFT customer updates create a new physical customer row while retaining the
        same customer UUID. QuickBooks must therefore enforce one current mapping
        per customer UUID, not one mapping per physical customer row.
        """
        customer_uuid = self._clean(customer_uuid)
        if not customer_uuid:
            return

        rows = session.execute(
            select(CustomerExternalMapModel).where(
                CustomerExternalMapModel.tenant_id == tenant_id,
                CustomerExternalMapModel.service_id == self._service_id_int(service_id),
                CustomerExternalMapModel.customer_uuid == customer_uuid,
                CustomerExternalMapModel.id != int(keep_mapping_id),
                self._is_current_mapping_clause(),
            )
        ).scalars().all()

        for row in rows:
            row.version_expiry_date = now
            row.endorsement_expiry_date = now
            row.updated_at = now
            session.add(row)

    def _normalize_customer_row(self, c: CustomerModel) -> Dict[str, Any]:
        return {
            "id": int(c.id),
            "customer_uuid": c.customer_uuid,
            "firstname": c.firstname,
            "middlename": c.middlename,
            "lastname": c.lastname,
            "mobile_no": c.mobile_no,
            "first_name": c.firstname,
            "last_name": c.lastname,
            "email": c.email,
            "phone": c.mobile_no,
            "service_id": c.service_id,
            "version": c.version,
            "status": c.status,
            "account_no": c.account_no,
            "birthdate": c.birthdate,
            "gender": c.gender,
            "alternate_mobile": c.alternate_mobile,
            "eula_accepted": c.eula_accepted,
            "is_deleted": c.is_deleted,
            "version_effective_date": c.version_effective_date,
            "version_expiry_date": c.version_expiry_date,
            "endorsement_effective_date": c.endorsement_effective_date,
            "endorsement_expiry_date": c.endorsement_expiry_date,
        }

    @staticmethod
    def _clean_phone(value: Any) -> Optional[str]:
        if not value:
            return None
        digits = re.sub(r"\D+", "", str(value))
        return digits[:16] or None

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
            "updated_at": data.get("updated_at"),
        }

    def get_personal_additional_info(self, tenant_id: str, customer_uuid: str) -> Optional[Dict[str, Any]]:
        customer_uuid = self._clean_pai_value(customer_uuid)
        if not tenant_id or not customer_uuid:
            return None
        from source.dal.db_session import tenant_session_scope

        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                text(
                    """
                    SELECT id, user_id, address, city, state, country, zip_code,
                           email, first_name, last_name, updated_at
                    FROM personal_additional_info
                    WHERE CAST(user_id AS TEXT) = :user_id
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ),
                {"user_id": customer_uuid},
            ).mappings().first()
            return self._normalize_personal_additional_info_row(row)

    def upsert_personal_additional_info(self, tenant_id: str, customer: Dict[str, Any], address_info: Dict[str, Any]) -> bool:
        """Create or update tenant personal_additional_info for a customer address.

        The customer UUID is stored in personal_additional_info.user_id. Address
        data lives in the tenant database, not in the shared customer database.
        Returns True when a row was inserted or changed.
        """
        customer = customer or {}
        address_info = address_info or {}
        customer_uuid = self._clean_pai_value(customer.get("customer_uuid") or customer.get("customerUuid"))
        if not tenant_id or not customer_uuid:
            return False

        address_payload = {
            "address": self._clean_pai_value(address_info.get("address") or address_info.get("line1")),
            "city": self._clean_pai_value(address_info.get("city")),
            "state": self._clean_pai_value(address_info.get("state") or address_info.get("country_subdivision_code")),
            "country": self._clean_pai_value(address_info.get("country")),
            "zip_code": self._clean_pai_value(address_info.get("zip_code") or address_info.get("postal_code")),
        }
        if not any(address_payload.values()):
            return False

        contact_payload = {
            "email": self._clean_pai_value(customer.get("email")),
            "first_name": self._clean_pai_value(customer.get("first_name") or customer.get("firstname")),
            "last_name": self._clean_pai_value(customer.get("last_name") or customer.get("lastname")),
        }
        now = self._now()

        from source.dal.db_session import tenant_session_scope

        with tenant_session_scope(tenant_id) as session:
            existing = session.execute(
                text(
                    """
                    SELECT id, user_id, address, city, state, country, zip_code,
                           email, first_name, last_name, updated_at
                    FROM personal_additional_info
                    WHERE CAST(user_id AS TEXT) = :user_id
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ),
                {"user_id": customer_uuid},
            ).mappings().first()

            if existing:
                existing_dict = self._normalize_personal_additional_info_row(existing) or {}
                update_payload = {**address_payload}
                for key, value in contact_payload.items():
                    update_payload[key] = value if value is not None else existing_dict.get(key)

                if all((existing_dict.get(key) or None) == (update_payload.get(key) or None) for key in update_payload):
                    return False

                session.execute(
                    text(
                        """
                        UPDATE personal_additional_info
                        SET address = :address,
                            city = :city,
                            state = :state,
                            country = :country,
                            zip_code = :zip_code,
                            email = :email,
                            first_name = :first_name,
                            last_name = :last_name,
                            updated_at = :updated_at
                        WHERE id = :id
                        """
                    ),
                    {**update_payload, "updated_at": now, "id": existing_dict.get("id")},
                )
                return True

            session.execute(
                text(
                    """
                    INSERT INTO personal_additional_info
                        (user_id, address, city, state, country, zip_code,
                         email, first_name, last_name, created_at, updated_at)
                    VALUES
                        (:user_id, :address, :city, :state, :country, :zip_code,
                         :email, :first_name, :last_name, :created_at, :updated_at)
                    """
                ),
                {
                    "user_id": customer_uuid,
                    **address_payload,
                    **contact_payload,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            return True

    def _customer_sync_projection(self, row: Dict[str, Any]) -> Dict[str, str]:
        return {
            "first_name": (row.get("first_name") or row.get("firstname") or "").strip(),
            "last_name": (row.get("last_name") or row.get("lastname") or "").strip(),
            const.KEY_EMAIL: (row.get(const.KEY_EMAIL) or "").strip().lower(),
            const.KEY_PHONE: (row.get(const.KEY_PHONE) or row.get("mobile_no") or "").strip(),
        }

    def sync_projection_changed(self, left: Dict[str, Any], right: Dict[str, Any]) -> bool:
        return self._customer_sync_projection(left) != self._customer_sync_projection(right)


    @staticmethod
    def _decode_additional_info(value: Any) -> Dict[str, Any]:
        if not value:
            return {}
        if isinstance(value, dict):
            return dict(value)
        try:
            decoded = json.loads(str(value))
            return decoded if isinstance(decoded, dict) else {}
        except (TypeError, ValueError):
            return {}

    @staticmethod
    def _encode_additional_info(value: Optional[Dict[str, Any]]) -> Optional[str]:
        if not value:
            return None
        return json.dumps(value, separators=(",", ":"), sort_keys=True)

    def _merge_additional_info_dict(
        self,
        current_value: Any,
        updates: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        current = self._decode_additional_info(current_value)
        for key, value in (updates or {}).items():
            if value not in (None, ""):
                current[str(key)] = value
        return current

    def _merge_additional_info(
        self,
        current_value: Any,
        updates: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        return self._encode_additional_info(
            self._merge_additional_info_dict(current_value, updates)
        )

    def _mapping_to_dict(self, m: CustomerExternalMapModel) -> Dict[str, Any]:
        return {
            "id": int(m.id),
            "customer_id": int(m.customer_id) if m.customer_id is not None else None,
            "customer_uuid": m.customer_uuid,
            "tenant_id": m.tenant_id,
            "service_id": int(m.service_id),
            "external_customer_id": m.external_customer_id,
            "external_account_id": m.external_account_id,
            "sync_status": m.sync_status,
            "reason": m.reason,
            "additional_info": self._decode_additional_info(m.additional_info),
            "version": m.version,
            "version_effective_date": m.version_effective_date,
            "version_expiry_date": m.version_expiry_date,
            "endorsement_effective_date": m.endorsement_effective_date,
            "endorsement_expiry_date": m.endorsement_expiry_date,
            "updated_at": m.updated_at,
        }

    def _close_current_customer(self, session, customer: CustomerModel, now: datetime) -> None:
        customer.version_expiry_date = now
        customer.endorsement_expiry_date = now
        session.add(customer)

    def _close_current_mapping(self, session, mapping: CustomerExternalMapModel, now: datetime) -> None:
        mapping.version_expiry_date = now
        mapping.endorsement_expiry_date = now
        mapping.updated_at = now
        session.add(mapping)

    def _ensure_customer_tenant_link(self, session, tenant_id: str, customer_id: int, now: datetime) -> None:
        stmt = select(CustomerTenantModel).where(
            CustomerTenantModel.customer_id == int(customer_id),
            CustomerTenantModel.tenant_id == tenant_id,
        )
        if session.execute(stmt).scalar_one_or_none():
            return
        session.add(
            CustomerTenantModel(
                tenant_uid=tenant_id,
                customer_id=int(customer_id),
                created_at=now,
                schema_version=0,
                tenant_id=tenant_id,
            )
        )

    def _move_customer_tenant_link(self, session, tenant_id: str, customer_uuid: str, to_customer_id: int, now: datetime) -> None:
        row = session.execute(
            select(CustomerTenantModel)
            .join(CustomerModel, CustomerModel.id == CustomerTenantModel.customer_id)
            .where(
                CustomerTenantModel.tenant_id == tenant_id,
                CustomerModel.customer_uuid == customer_uuid,
            )
        ).scalars().first()
        if row:
            row.customer_id = int(to_customer_id)
            session.add(row)
            return
        self._ensure_customer_tenant_link(session, tenant_id=tenant_id, customer_id=int(to_customer_id), now=now)

    def get_customer_latest_local_timestamp(
        self,
        customer: Optional[Dict[str, Any]],
        mapping: Optional[Dict[str, Any]] = None,
        personal_additional_info: Optional[Dict[str, Any]] = None,
    ) -> Optional[datetime]:
        candidates: List[datetime] = []
        for row in (customer or {}, mapping or {}, personal_additional_info or {}):
            for key in (
                "version_effective_date",
                "endorsement_effective_date",
                "updated_at",
            ):
                value = row.get(key)
                if value is None or not isinstance(value, datetime):
                    continue
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                candidates.append(value)
        return max(candidates) if candidates else None

    # ------------------------------------------------------------------
    # Public helpers still used by other modules
    # ------------------------------------------------------------------

    def get_customer_uuid_by_id(self, customer_id: int) -> Optional[str]:
        if customer_id is None:
            return None
        with customer_session_scope() as session:
            row = session.get(CustomerModel, int(customer_id))
            return row.customer_uuid if row else None

    def get_customer_id_by_uuid(self, customer_uuid: str) -> Optional[int]:
        customer_uuid = (customer_uuid or "").strip()
        if not customer_uuid:
            return None
        with customer_session_scope() as session:
            stmt = select(CustomerModel).where(CustomerModel.customer_uuid == customer_uuid).order_by(CustomerModel.id.desc())
            row = session.execute(stmt).scalars().first()
            return int(row.id) if row else None

    def get_customer_by_id(self, customer_id: int) -> Optional[Dict[str, Any]]:
        if customer_id is None:
            return None
        with customer_session_scope() as session:
            row = session.get(CustomerModel, int(customer_id))
            return self._normalize_customer_row(row) if row else None

    def get_current_customer_by_id(self, customer_id: int) -> Optional[Dict[str, Any]]:
        if customer_id is None:
            return None
        with customer_session_scope() as session:
            stmt = select(CustomerModel).where(CustomerModel.id == int(customer_id), self._is_current_customer_clause())
            row = session.execute(stmt).scalar_one_or_none()
            return self._normalize_customer_row(row) if row else None

    def get_current_customers_for_tenant(self, tenant_id: str, service_id: Any | None = None, limit: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
        with customer_session_scope() as session:
            stmt = (
                select(CustomerModel)
                .join(CustomerTenantModel, CustomerTenantModel.customer_id == CustomerModel.id)
                .where(
                    CustomerTenantModel.tenant_id == tenant_id,
                    self._is_current_customer_clause(),
                )
                .order_by(CustomerModel.id)
                .offset(int(offset))
                .limit(int(limit))
            )
            if service_id is not None:
                stmt = stmt.where(CustomerModel.service_id == self._service_id_int(service_id))
            rows = session.execute(stmt).scalars().all()
            return [self._normalize_customer_row(r) for r in rows]

    def get_customers_for_tenant(self, tenant_id: str, limit: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
        return self.get_current_customers_for_tenant(tenant_id=tenant_id, limit=limit, offset=offset)

    def get_customers_by_ids(self, tenant_id: str, customer_ids: List[int]) -> List[Dict[str, Any]]:
        if not customer_ids:
            return []
        with customer_session_scope() as session:
            stmt = (
                select(CustomerModel)
                .join(CustomerTenantModel, CustomerTenantModel.customer_id == CustomerModel.id)
                .where(
                    CustomerTenantModel.tenant_id == tenant_id,
                    CustomerModel.id.in_([int(x) for x in customer_ids]),
                )
                .order_by(CustomerModel.id)
            )
            rows = session.execute(stmt).scalars().all()
            return [self._normalize_customer_row(r) for r in rows]

    def get_current_mapping_by_customer_id(self, tenant_id: str, service_id: Any, customer_id: int) -> Optional[Dict[str, Any]]:
        with customer_session_scope() as session:
            customer_uuid = self._customer_uuid_for_id(session, customer_id)
            if not customer_uuid:
                return None
            stmt = self._current_mapping_stmt_for_customer_uuid(
                tenant_id=tenant_id,
                service_id=service_id,
                customer_uuid=customer_uuid,
            )
            row = session.execute(stmt).scalars().first()
            return self._mapping_to_dict(row) if row else None

    def get_mapping_by_id(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        mapping_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Return one current customer mapping scoped to tenant and service."""
        with customer_session_scope() as session:
            row = session.execute(
                select(CustomerExternalMapModel).where(
                    CustomerExternalMapModel.id == int(mapping_id),
                    CustomerExternalMapModel.tenant_id == tenant_id,
                    CustomerExternalMapModel.service_id == self._service_id_int(service_id),
                    self._is_current_mapping_clause(),
                )
            ).scalars().first()
            return self._mapping_to_dict(row) if row else None

    def get_current_mapping_by_external_customer_id(self, tenant_id: str, service_id: Any, external_customer_id: str) -> Optional[Dict[str, Any]]:
        external_customer_id = (external_customer_id or "").strip()
        if not external_customer_id:
            return None
        with customer_session_scope() as session:
            stmt = select(CustomerExternalMapModel).where(
                CustomerExternalMapModel.external_customer_id == external_customer_id,
                CustomerExternalMapModel.tenant_id == tenant_id,
                CustomerExternalMapModel.service_id == self._service_id_int(service_id),
                self._is_current_mapping_clause(),
            ).order_by(CustomerExternalMapModel.id.desc())
            row = session.execute(stmt).scalars().first()
            return self._mapping_to_dict(row) if row else None

    def get_latest_mapping_for_customer_uuid(self, tenant_id: str, service_id: Any, customer_uuid: str) -> Optional[Dict[str, Any]]:
        customer_uuid = (customer_uuid or "").strip()
        if not tenant_id or not customer_uuid:
            return None

        try:
            with customer_session_scope() as session:
                stmt = (
                    select(CustomerExternalMapModel)
                    .where(
                        CustomerExternalMapModel.tenant_id == tenant_id,
                        CustomerExternalMapModel.service_id == self._service_id_int(service_id),
                        CustomerExternalMapModel.customer_uuid == customer_uuid,
                    )
                    .order_by(
                        CustomerExternalMapModel.updated_at.desc(),
                        CustomerExternalMapModel.id.desc(),
                    )
                )
                row = session.execute(stmt).scalars().first()
                return self._mapping_to_dict(row) if row else None
        except Exception as exc:
            logger.exception(
                "[CustomerRepository] failed to resolve latest customer mapping | tenant_id=%s service_id=%s customer_uuid=%s",
                tenant_id,
                service_id,
                customer_uuid,
            )
            raise Exception(
                "Unable to verify the customer linked to this project. "
                "Please run customer sync, then retry project sync."
            ) from exc

    def get_latest_qbo_linked_mapping_for_customer_uuid(
        self,
        tenant_id: str,
        service_id: Any,
        customer_uuid: str,
        external_account_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return the latest current customer mapping that already has a QuickBooks Customer Id.

        Project export only needs a usable parent QuickBooks Customer reference.
        A customer can be previously exported and then move back to PENDING_PUSH
        after a FIELDCRAFT update, so this lookup must not require sync_status=SYNCED.
        """
        customer_uuid = (customer_uuid or "").strip()
        if not tenant_id or not customer_uuid:
            return None

        try:
            with customer_session_scope() as session:
                external_customer_id = func.trim(func.coalesce(CustomerExternalMapModel.external_customer_id, ""))
                stmt = select(CustomerExternalMapModel).where(
                    CustomerExternalMapModel.tenant_id == tenant_id,
                    CustomerExternalMapModel.service_id == self._service_id_int(service_id),
                    CustomerExternalMapModel.customer_uuid == customer_uuid,
                    self._is_current_mapping_clause(),
                    external_customer_id != "",
                )
                clean_account_id = self._clean(external_account_id)
                if clean_account_id:
                    stored_account_id = func.trim(
                        func.coalesce(CustomerExternalMapModel.external_account_id, "")
                    )
                    stmt = stmt.where(
                        or_(stored_account_id == clean_account_id, stored_account_id == "")
                    ).order_by(
                        (stored_account_id == clean_account_id).desc(),
                        CustomerExternalMapModel.updated_at.desc(),
                        CustomerExternalMapModel.id.desc(),
                    )
                else:
                    stmt = stmt.order_by(
                        CustomerExternalMapModel.updated_at.desc(),
                        CustomerExternalMapModel.id.desc(),
                    )
                row = session.execute(stmt).scalars().first()
                return self._mapping_to_dict(row) if row else None
        except Exception as exc:
            logger.exception(
                "[CustomerRepository] failed to resolve QuickBooks-linked customer mapping | tenant_id=%s service_id=%s customer_uuid=%s",
                tenant_id,
                service_id,
                customer_uuid,
            )
            raise Exception(
                "Unable to verify the QuickBooks customer linked to this project. "
                "Please run customer sync, then retry project sync."
            ) from exc

    def get_latest_qbo_linked_mappings_for_customer_uuids(
        self,
        tenant_id: str,
        service_id: Any,
        customer_uuids: List[str],
        external_account_id: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Resolve the latest usable QBO mapping for many customers in one query."""
        normalized = sorted({str(value or "").strip() for value in customer_uuids if str(value or "").strip()})
        if not tenant_id or not normalized:
            return {}
        try:
            with customer_session_scope() as session:
                external_customer_id = func.trim(func.coalesce(CustomerExternalMapModel.external_customer_id, ""))
                stmt = select(CustomerExternalMapModel).where(
                    CustomerExternalMapModel.tenant_id == tenant_id,
                    CustomerExternalMapModel.service_id == self._service_id_int(service_id),
                    CustomerExternalMapModel.customer_uuid.in_(normalized),
                    self._is_current_mapping_clause(),
                    external_customer_id != "",
                )
                clean_account_id = self._clean(external_account_id)
                stored_account_id = func.trim(
                    func.coalesce(CustomerExternalMapModel.external_account_id, "")
                )
                if clean_account_id:
                    stmt = stmt.where(
                        or_(stored_account_id == clean_account_id, stored_account_id == "")
                    )
                rows = session.execute(
                    stmt.order_by(
                        CustomerExternalMapModel.customer_uuid.asc(),
                        (stored_account_id == clean_account_id).desc()
                        if clean_account_id
                        else CustomerExternalMapModel.id.desc(),
                        CustomerExternalMapModel.updated_at.desc(),
                        CustomerExternalMapModel.id.desc(),
                    )
                ).scalars().all()
                resolved: Dict[str, Dict[str, Any]] = {}
                for row in rows:
                    key = str(row.customer_uuid or "").strip()
                    if key and key not in resolved:
                        resolved[key] = self._mapping_to_dict(row)
                return resolved
        except Exception as exc:
            logger.exception(
                "[CustomerRepository] failed bulk QBO customer mapping lookup | tenant_id=%s service_id=%s count=%s",
                tenant_id,
                service_id,
                len(normalized),
            )
            raise Exception(
                "Unable to verify the QuickBooks customers linked to these projects. "
                "Run customer sync, then retry time export."
            ) from exc

    def get_customer_mapping(self, tenant_id: str, customer_id: int) -> Optional[str]:
        mapping = self.get_current_mapping_by_customer_id(tenant_id=tenant_id, service_id=6, customer_id=customer_id)
        return (mapping or {}).get("external_customer_id") or None

    def get_existing_external_customer_id(self, tenant_id: str, fieldcraft_customer_id: int) -> Optional[str]:
        with customer_session_scope() as session:
            customer_uuid = self._customer_uuid_for_id(session, fieldcraft_customer_id)
            if not customer_uuid:
                return None
            stmt = self._current_mapping_stmt_for_customer_uuid(
                tenant_id=tenant_id,
                customer_uuid=customer_uuid,
            )
            row = session.execute(stmt).scalars().first()
            if row and row.external_customer_id:
                value = row.external_customer_id.strip()
                return value or None
            return None

    def get_customer_mapping_details(self, tenant_id: str, customer_id: int) -> Optional[Dict[str, Any]]:
        with customer_session_scope() as session:
            customer_uuid = self._customer_uuid_for_id(session, customer_id)
            if not customer_uuid:
                return None
            stmt = self._current_mapping_stmt_for_customer_uuid(
                tenant_id=tenant_id,
                customer_uuid=customer_uuid,
            )
            row = session.execute(stmt).scalars().first()
            return self._mapping_to_dict(row) if row else None

    def get_mapped_customers_for_payments(self, tenant_id: str) -> List[Dict[str, Any]]:
        with customer_session_scope() as session:
            mapped_customer = aliased(CustomerModel)
            current_customer_ids = self._current_customer_ids_subquery(tenant_id=tenant_id)
            logical_customer_uuid = func.coalesce(
                CustomerExternalMapModel.customer_uuid,
                mapped_customer.customer_uuid,
            )
            stmt = (
                select(CustomerModel, CustomerExternalMapModel)
                .select_from(CustomerExternalMapModel)
                .outerjoin(
                    mapped_customer,
                    mapped_customer.id == CustomerExternalMapModel.customer_id,
                )
                .join(
                    current_customer_ids,
                    current_customer_ids.c.customer_uuid == logical_customer_uuid,
                )
                .join(
                    CustomerModel,
                    CustomerModel.id == current_customer_ids.c.customer_id,
                )
                .where(
                    CustomerExternalMapModel.tenant_id == tenant_id,
                    self._is_current_mapping_clause(),
                    CustomerExternalMapModel.external_customer_id != "",
                )
                .order_by(CustomerModel.id, CustomerExternalMapModel.id.desc())
            )
            rows = session.execute(stmt).all()
            return [
                {
                    "customer_id": int(c.id),
                    "customer_uuid": c.customer_uuid,
                    "external_customer_id": m.external_customer_id,
                    "external_account_id": m.external_account_id,
                }
                for c, m in rows
            ]

    def get_customer_uuid_by_external_customer_id(self, tenant_id: str, external_customer_id: str) -> Optional[str]:
        mapping = self.get_current_mapping_by_external_customer_id(tenant_id=tenant_id, service_id=6, external_customer_id=external_customer_id)
        if not mapping:
            return None
        customer = self.get_customer_by_id(mapping["customer_id"])
        return customer.get("customer_uuid") if customer else None

    def get_customer_id_by_email(self, tenant_id: str, email: str) -> Optional[int]:
        email = (email or "").strip().lower()
        if not email:
            return None
        with customer_session_scope() as session:
            stmt = (
                select(CustomerModel)
                .join(CustomerTenantModel, CustomerTenantModel.customer_id == CustomerModel.id)
                .where(
                    CustomerTenantModel.tenant_id == tenant_id,
                    func.lower(CustomerModel.email) == email,
                    self._is_current_customer_clause(),
                )
                .order_by(CustomerModel.id.desc())
            )
            row = session.execute(stmt).scalars().first()
            return int(row.id) if row else None

    def get_customer_id_by_email_any_tenant(self, email: str) -> Optional[int]:
        email = (email or "").strip().lower()
        if not email:
            return None
        with customer_session_scope() as session:
            stmt = (
                select(CustomerModel)
                .where(
                    func.lower(CustomerModel.email) == email,
                    self._is_current_customer_clause(),
                )
                .order_by(CustomerModel.id.desc())
            )
            row = session.execute(stmt).scalars().first()
            return int(row.id) if row else None

    def get_customer_id_by_external_customer_id(self, tenant_id: str, external_customer_id: str) -> Optional[int]:
        with customer_session_scope() as session:
            stmt = select(CustomerExternalMapModel).where(
                CustomerExternalMapModel.tenant_id == tenant_id,
                CustomerExternalMapModel.external_customer_id == str(external_customer_id),
                self._is_current_mapping_clause(),
            ).order_by(CustomerExternalMapModel.id.desc())
            row = session.execute(stmt).scalars().first()
            return int(row.customer_id) if row and row.customer_id is not None else None

    # ------------------------------------------------------------------
    # Create / version / mapping rotation helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Import-failure tracking helpers
    # ------------------------------------------------------------------

    def upsert_import_failed_mapping(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        external_customer_id: str,
        external_account_id: Optional[str] = None,
        customer_id: Optional[int] = None,
        reason: Optional[str] = None,
        detach_customer: bool = False,
        sync_status: Optional[str] = None,
        additional_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create/update an IMPORT_FAILED or SYNC_FAILED mapping.

        When a QuickBooks customer cannot be imported as a new FIELDCRAFT customer
        because the email already exists, customer_id stays NULL. This mirrors
        the vendor import-failed flow and keeps the existing synced customer
        untouched.
        """
        external_customer_id = str(external_customer_id or "").strip()
        if not external_customer_id:
            raise ValueError("external_customer_id is required for IMPORT_FAILED mapping")

        now = self._now()
        with customer_session_scope() as session:
            current = session.execute(
                select(CustomerExternalMapModel).where(
                    CustomerExternalMapModel.tenant_id == tenant_id,
                    CustomerExternalMapModel.service_id == self._service_id_int(service_id),
                    CustomerExternalMapModel.external_customer_id == external_customer_id,
                    self._is_current_mapping_clause(),
                ).order_by(CustomerExternalMapModel.id.desc())
            ).scalars().first()

            if current:
                if detach_customer:
                    current.customer_id = None
                    current.customer_uuid = None
                    current.version = 1
                elif customer_id is not None:
                    customer = session.get(CustomerModel, int(customer_id))
                    if customer:
                        current.customer_id = int(customer.id)
                        current.customer_uuid = customer.customer_uuid
                        current.version = int(customer.version or current.version or 1)
                if external_account_id is not None:
                    current.external_account_id = external_account_id
                current.sync_status = sync_status or self.STATUS_IMPORT_FAILED
                current.reason = reason
                current.additional_info = self._merge_additional_info(
                    current.additional_info,
                    additional_info,
                )
                current.updated_at = now
                session.add(current)
                session.flush()
                return self._mapping_to_dict(current)

            customer = session.get(CustomerModel, int(customer_id)) if customer_id is not None else None
            if customer_id is not None and not customer:
                raise ValueError(f"Customer {customer_id} not found for failed mapping")

            row = self._create_mapping(
                session,
                tenant_id=tenant_id,
                service_id=service_id,
                customer_id=int(customer.id) if customer else None,
                customer_uuid=customer.customer_uuid if customer else None,
                external_customer_id=external_customer_id,
                external_account_id=external_account_id,
                sync_status=sync_status or self.STATUS_IMPORT_FAILED,
                version=int(customer.version or 1) if customer else 1,
                now=now,
                reason=reason,
                additional_info=additional_info,
            )
            return self._mapping_to_dict(row)

    def get_open_import_failed_external_customer_ids(self, tenant_id: str, service_id: Any) -> List[str]:
        with customer_session_scope() as session:
            stmt = (
                select(CustomerExternalMapModel.external_customer_id)
                .where(
                    CustomerExternalMapModel.tenant_id == tenant_id,
                    CustomerExternalMapModel.service_id == self._service_id_int(service_id),
                    CustomerExternalMapModel.sync_status.in_([self.STATUS_IMPORT_FAILED, self.STATUS_SYNC_FAILED]),
                    self._is_current_mapping_clause(),
                    CustomerExternalMapModel.external_customer_id != "",
                )
                .order_by(CustomerExternalMapModel.updated_at.asc(), CustomerExternalMapModel.id.asc())
            )
            return [str(row[0]) for row in session.execute(stmt).all() if row and row[0]]

    def close_mapping_as_invalid(self, mapping_id: int) -> None:
        now = self._now()
        with customer_session_scope() as session:
            row = session.get(CustomerExternalMapModel, int(mapping_id))
            if not row:
                return
            row.sync_status = self.STATUS_INVALID
            row.version_expiry_date = now
            row.endorsement_expiry_date = now
            row.updated_at = now
            session.add(row)

    def create_customer_for_tenant(self, tenant_id: str, customer_data: Dict[str, Any], created_by: str = "qbo-import") -> int:
        """Create one current customer row, idempotently within a tenant."""
        normalized_tenant_id = str(tenant_id or "").strip()
        if not normalized_tenant_id:
            raise ValueError("tenant_id is required")
        email = (customer_data.get(const.KEY_EMAIL) or "").strip()
        normalized_email = email.lower()
        now = self._now()

        with customer_session_scope() as session:
            if normalized_email:
                acquire_transaction_lock(
                    session,
                    f"customer:create:{normalized_tenant_id}:{normalized_email}",
                )
                existing = session.execute(
                    select(CustomerModel)
                    .join(CustomerTenantModel, CustomerTenantModel.customer_id == CustomerModel.id)
                    .where(
                        CustomerTenantModel.tenant_id == normalized_tenant_id,
                        func.lower(CustomerModel.email) == normalized_email,
                        self._is_current_customer_clause(),
                    )
                    .order_by(CustomerModel.id.desc())
                ).scalars().first()
                if existing:
                    logger.debug(
                        "Customer create reused existing row | tenant_id=%s customer_id=%s source=%s",
                        normalized_tenant_id,
                        existing.id,
                        created_by,
                    )
                    return int(existing.id)

            row = CustomerModel(
                customer_uuid=(customer_data.get("customer_uuid") or str(uuid.uuid4())),
                firstname=(customer_data.get("firstname") or customer_data.get("first_name") or "").strip() or None,
                lastname=(customer_data.get("lastname") or customer_data.get("last_name") or "").strip() or None,
                middlename=(customer_data.get("middlename") or "").strip() or None,
                account_no=(customer_data.get("account_no") or f"CUST-{str(uuid.uuid4()).replace('-', '')[:10]}"),
                birthdate=customer_data.get("birthdate"),
                gender=customer_data.get("gender"),
                email=email or None,
                mobile_no=(customer_data.get("mobile_no") or customer_data.get("mobileNo") or customer_data.get(const.KEY_PHONE) or "").strip() or None,
                alternate_mobile=(customer_data.get("alternate_mobile") or customer_data.get("alternateMobileNo") or "").strip() or None,
                status=(customer_data.get(const.KEY_STATUS) or "Invited"),
                eula_accepted=bool(customer_data.get("eula_accepted") if customer_data.get("eula_accepted") is not None else customer_data.get("eulaAccepted", False)),
                version=int(customer_data.get("version") or 1),
                version_effective_date=now,
                version_expiry_date=self.OPEN_END_AT,
                is_deleted=bool(customer_data.get("is_deleted", False)),
                endorsement_effective_date=now,
                endorsement_expiry_date=self.OPEN_END_AT,
                service_id=self._service_id_int(customer_data.get("service_id") or 0) or None,
            )
            session.add(row)
            session.flush()
            self._ensure_customer_tenant_link(session, tenant_id=normalized_tenant_id, customer_id=int(row.id), now=now)
            session.flush()
            logger.debug(
                "Customer created | tenant_id=%s customer_id=%s source=%s",
                normalized_tenant_id,
                row.id,
                created_by,
            )
            return int(row.id)

    def _create_mapping(
        self,
        session,
        *,
        tenant_id: str,
        service_id: Any,
        customer_id: Optional[int],
        customer_uuid: Optional[str],
        external_customer_id: Optional[str],
        external_account_id: Optional[str],
        sync_status: str,
        version: int,
        now: datetime,
        reason: Optional[str] = None,
        additional_info: Optional[Dict[str, Any]] = None,
    ) -> CustomerExternalMapModel:
        expiry = self.OPEN_END_AT
        row = CustomerExternalMapModel(
            customer_id=int(customer_id) if customer_id is not None else None,
            customer_uuid=customer_uuid,
            tenant_id=tenant_id,
            service_id=self._service_id_int(service_id),
            external_customer_id=(external_customer_id or ""),
            external_account_id=external_account_id,
            sync_status=sync_status,
            reason=reason,
            additional_info=self._encode_additional_info(additional_info),
            version=int(version),
            version_effective_date=now,
            version_expiry_date=expiry,
            endorsement_effective_date=now,
            endorsement_expiry_date=expiry,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return row

    def create_external_mapping(
        self,
        tenant_id: str,
        customer_id: int,
        external_customer_id: str,
        external_account_id: Optional[str] = None,
        status: str = "SYNCED",
        service_id: Any = 6,
        reason: Optional[str] = None,
        additional_info: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.upsert_customer_mapping(
            tenant_id=tenant_id,
            customer_id=customer_id,
            external_customer_id=external_customer_id,
            external_account_id=external_account_id,
            status=status,
            service_id=service_id,
            reason=reason,
            additional_info=additional_info,
        )

    def upsert_customer_mapping(
        self,
        tenant_id: str,
        customer_id: int,
        external_customer_id: str,
        external_account_id: Optional[str] = None,
        status: str = "SYNCED",
        service_id: Any = 6,
        reason: Optional[str] = None,
        additional_info: Optional[Dict[str, Any]] = None,
    ) -> None:
        logger.debug(
            "Customer mapping upsert started | tenant_id=%s customer_id=%s status=%s",
            tenant_id,
            customer_id,
            status,
        )
        now = self._now()
        with customer_session_scope() as session:
            customer = session.get(CustomerModel, int(customer_id))
            if customer is None:
                raise ValueError(f"Customer not found for customer_id={customer_id}")

            self._acquire_customer_uuid_lock(
                session,
                tenant_id=tenant_id,
                service_id=service_id,
                customer_uuid=customer.customer_uuid,
            )

            current = session.execute(
                select(CustomerExternalMapModel).where(
                    CustomerExternalMapModel.customer_uuid == customer.customer_uuid,
                    CustomerExternalMapModel.tenant_id == tenant_id,
                    CustomerExternalMapModel.service_id == self._service_id_int(service_id),
                    self._is_current_mapping_clause(),
                ).order_by(CustomerExternalMapModel.id.desc())
            ).scalars().first()

            if current:
                logger.debug(
                    "Customer mapping update | mapping_id=%s customer_uuid=%s",
                    current.id,
                    customer.customer_uuid,
                )
                current.customer_id = int(customer.id)
                current.customer_uuid = customer.customer_uuid
                current.external_customer_id = external_customer_id or current.external_customer_id
                current.external_account_id = external_account_id or current.external_account_id
                current.sync_status = status
                current.reason = reason
                current.additional_info = self._merge_additional_info(
                    current.additional_info,
                    additional_info,
                )
                current.version = int(customer.version or current.version or 1)
                current.updated_at = now
                session.add(current)
                self._close_other_current_mappings_for_customer_uuid(
                    session,
                    tenant_id=tenant_id,
                    service_id=service_id,
                    customer_uuid=customer.customer_uuid,
                    keep_mapping_id=int(current.id),
                    now=now,
                )
                return

            logger.debug(
                "Customer mapping create | customer_id=%s customer_uuid=%s",
                customer_id,
                customer.customer_uuid,
            )
            row = self._create_mapping(
                session,
                tenant_id=tenant_id,
                service_id=service_id,
                customer_id=customer_id,
                customer_uuid=customer.customer_uuid,
                external_customer_id=external_customer_id,
                external_account_id=external_account_id,
                sync_status=status,
                version=int(customer.version or 1),
                now=now,
                reason=reason,
                additional_info=additional_info,
            )
            self._close_other_current_mappings_for_customer_uuid(
                session,
                tenant_id=tenant_id,
                service_id=service_id,
                customer_uuid=customer.customer_uuid,
                keep_mapping_id=int(row.id),
                now=now,
            )

    def _active_export_claim(
        self,
        additional_info: Dict[str, Any],
        now: datetime,
    ) -> Optional[Dict[str, Any]]:
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
        """Claim one current customer mapping before QuickBooks network I/O."""
        now = self._now()
        with customer_session_scope() as session:
            row = session.execute(
                select(CustomerExternalMapModel)
                .where(
                    CustomerExternalMapModel.id == int(mapping_id),
                    CustomerExternalMapModel.tenant_id == tenant_id,
                    CustomerExternalMapModel.service_id == self._service_id_int(service_id),
                    self._is_current_mapping_clause(),
                )
                .with_for_update()
            ).scalars().first()
            if not row:
                return {"claimed": False, "reason": "not_current", "mapping": None}

            mapping = self._mapping_to_dict(row)
            sync_status = self._clean(row.sync_status).upper()
            if sync_status == self.STATUS_SYNCED and self._clean(row.external_customer_id):
                return {"claimed": False, "reason": "already_synced", "mapping": mapping}
            if sync_status not in self.EXPORTABLE_SYNC_STATUSES:
                return {"claimed": False, "reason": "not_eligible", "mapping": mapping}

            additional_info = self._decode_additional_info(row.additional_info)
            if self._active_export_claim(additional_info, now):
                return {"claimed": False, "reason": "in_progress", "mapping": mapping}

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
        mapping_id: int,
        external_customer_id: str,
        external_account_id: Optional[str] = None,
        reason: Optional[str] = None,
        additional_info: Optional[Dict[str, Any]] = None,
    ) -> None:
        logger.debug("Customer mapping sync update | mapping_id=%s", mapping_id)
        now = self._now()
        with customer_session_scope() as session:
            row = session.get(CustomerExternalMapModel, int(mapping_id))
            if not row:
                logger.warning(
                    "Customer mapping not found while marking synced | mapping_id=%s",
                    mapping_id,
                )
                return

            customer_uuid = self._clean(row.customer_uuid)
            if not customer_uuid and row.customer_id is not None:
                customer = session.get(CustomerModel, int(row.customer_id))
                customer_uuid = self._clean(customer.customer_uuid if customer else None)

            if customer_uuid:
                self._acquire_customer_uuid_lock(
                    session,
                    tenant_id=row.tenant_id,
                    service_id=row.service_id,
                    customer_uuid=customer_uuid,
                )
                # The row may have changed while waiting for the advisory lock.
                # Refresh before applying the sync result so the serialized writer
                # operates on the latest committed mapping state.
                session.refresh(row)
                customer_uuid = self._clean(row.customer_uuid) or customer_uuid

            row.external_customer_id = external_customer_id or row.external_customer_id
            if external_account_id is not None:
                row.external_account_id = external_account_id
            row.sync_status = self.STATUS_SYNCED
            if reason is not None:
                row.reason = reason
            stored_additional_info = self._decode_additional_info(row.additional_info)
            stored_additional_info.pop(self.EXPORT_CLAIM_KEY, None)
            stored_additional_info = self._merge_additional_info_dict(
                stored_additional_info,
                additional_info,
            )
            row.additional_info = self._encode_additional_info(stored_additional_info)
            row.updated_at = now
            session.add(row)

            if customer_uuid:
                current_customer_ids = self._current_customer_ids_subquery()
                current_customer_id = session.execute(
                    select(current_customer_ids.c.customer_id).where(
                        current_customer_ids.c.customer_uuid == customer_uuid
                    )
                ).scalar_one_or_none()
                if current_customer_id is not None:
                    row.customer_id = int(current_customer_id)
                row.customer_uuid = customer_uuid

                self._close_other_current_mappings_for_customer_uuid(
                    session,
                    tenant_id=row.tenant_id,
                    service_id=row.service_id,
                    customer_uuid=customer_uuid,
                    keep_mapping_id=int(row.id),
                    now=now,
                )

            logger.debug("Customer mapping marked synced | mapping_id=%s", mapping_id)

    def merge_mapping_additional_info(
        self,
        mapping_id: int,
        additional_info: Optional[Dict[str, Any]],
    ) -> None:
        """Merge non-empty metadata into an existing customer mapping."""
        if not additional_info:
            return
        now = self._now()
        with customer_session_scope() as session:
            row = session.get(CustomerExternalMapModel, int(mapping_id))
            if not row:
                logger.warning(
                    "Customer mapping not found while merging additional info | mapping_id=%s",
                    mapping_id,
                )
                return
            row.additional_info = self._merge_additional_info(
                row.additional_info,
                additional_info,
            )
            row.updated_at = now
            session.add(row)

    def mark_mapping_failed(
        self,
        mapping_id: int,
        status: str | None = None,
        reason: Optional[str] = None,
        additional_info: Optional[Dict[str, Any]] = None,
    ) -> None:
        logger.debug("Customer mapping failure update | mapping_id=%s status=%s", mapping_id, status or self.STATUS_FAILED)
        now = self._now()
        with customer_session_scope() as session:
            row = session.get(CustomerExternalMapModel, int(mapping_id))
            if not row:
                logger.warning("Customer mapping not found while marking failed | mapping_id=%s", mapping_id)
                return
            row.sync_status = status or self.STATUS_FAILED
            if reason is not None:
                row.reason = reason
            stored_additional_info = self._decode_additional_info(row.additional_info)
            stored_additional_info.pop(self.EXPORT_CLAIM_KEY, None)
            stored_additional_info = self._merge_additional_info_dict(
                stored_additional_info,
                additional_info,
            )
            row.additional_info = self._encode_additional_info(stored_additional_info)
            row.updated_at = now
            session.add(row)
            logger.debug("Customer mapping marked failed | mapping_id=%s status=%s", mapping_id, row.sync_status)

    def claim_external_customer_mapping(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        customer_id: int,
        external_customer_id: str,
        external_account_id: Optional[str] = None,
        reason: Optional[str] = None,
        additional_info: Optional[Dict[str, Any]] = None,
        mapping_id: Optional[int] = None,
        allow_create: bool = False,
    ) -> Dict[str, Any]:
        """Atomically recover a missing FIELDCRAFT-to-QuickBooks customer mapping.

        Customer rows are versioned, so identity is anchored on ``customer_uuid``
        and the recovered mapping always points at the current physical customer
        row. The method may reuse a detached QuickBooks IMPORT_FAILED row or an
        unbound FIELDCRAFT mapping, but it never replaces a customer mapping that already
        belongs to a different QuickBooks customer and never steals a QuickBooks
        id from another logical FIELDCRAFT customer.
        """
        normalized_tenant_id = self._clean(tenant_id)
        external_customer_id = self._clean(external_customer_id)
        if not normalized_tenant_id:
            raise ValueError("tenant_id is required to recover a customer mapping.")
        if not external_customer_id:
            raise ValueError("A QuickBooks customer id is required to recover a customer mapping.")

        service_id_int = self._service_id_int(service_id)
        now = self._now()

        with customer_session_scope() as session:
            requested_customer = session.get(CustomerModel, int(customer_id))
            if not requested_customer:
                raise ValueError(f"FIELDCRAFT customer {customer_id} was not found.")

            customer_uuid = self._clean(requested_customer.customer_uuid)
            if not customer_uuid:
                raise ValueError(f"FIELDCRAFT customer {customer_id} has no customer UUID.")

            acquire_transaction_locks(
                session,
                (
                    self._customer_uuid_lock_key(
                        tenant_id=normalized_tenant_id,
                        service_id=service_id_int,
                        customer_uuid=customer_uuid,
                    ),
                    self._customer_external_lock_key(
                        tenant_id=normalized_tenant_id,
                        service_id=service_id_int,
                        external_customer_id=external_customer_id,
                    ),
                ),
            )

            current_customer = session.execute(
                select(CustomerModel)
                .join(CustomerTenantModel, CustomerTenantModel.customer_id == CustomerModel.id)
                .where(
                    CustomerTenantModel.tenant_id == normalized_tenant_id,
                    CustomerModel.customer_uuid == customer_uuid,
                    self._is_current_customer_clause(),
                )
                .order_by(CustomerModel.version.desc(), CustomerModel.id.desc())
                .with_for_update()
            ).scalars().first()
            if not current_customer:
                raise ValueError(
                    "The matching FIELDCRAFT customer is no longer current for this tenant. "
                    "Refresh the customer and run sync again."
                )

            logical_customer_ids = list(
                session.execute(
                    select(CustomerModel.id)
                    .join(CustomerTenantModel, CustomerTenantModel.customer_id == CustomerModel.id)
                    .where(
                        CustomerTenantModel.tenant_id == normalized_tenant_id,
                        CustomerModel.customer_uuid == customer_uuid,
                    )
                ).scalars().all()
            )
            logical_customer_id_set = {int(value) for value in logical_customer_ids}

            rows = list(
                session.execute(
                    select(CustomerExternalMapModel)
                    .where(
                        CustomerExternalMapModel.tenant_id == normalized_tenant_id,
                        CustomerExternalMapModel.service_id == service_id_int,
                        self._is_current_mapping_clause(),
                        or_(
                            CustomerExternalMapModel.customer_uuid == customer_uuid,
                            CustomerExternalMapModel.customer_id.in_(logical_customer_ids),
                            CustomerExternalMapModel.external_customer_id == external_customer_id,
                        ),
                    )
                    .order_by(CustomerExternalMapModel.id.desc())
                    .with_for_update()
                ).scalars().all()
            )

            clean_account_id = self._clean(external_account_id)
            for row in rows:
                if self._clean(row.external_customer_id) != external_customer_id:
                    continue
                row_uuid = self._clean(row.customer_uuid)
                if not row_uuid and row.customer_id is not None:
                    mapped_customer = session.get(CustomerModel, int(row.customer_id))
                    row_uuid = self._clean(mapped_customer.customer_uuid if mapped_customer else None)
                if row_uuid and row_uuid != customer_uuid:
                    raise ValueError(
                        "This QuickBooks customer is already linked to another FIELDCRAFT customer. "
                        "Review the existing mapping before syncing again."
                    )
                row_account_id = self._clean(row.external_account_id)
                if clean_account_id and row_account_id and row_account_id != clean_account_id:
                    raise ValueError(
                        "The customer mapping belongs to a different QuickBooks company. "
                        "Reconnect the correct company and run customer sync again."
                    )

            customer_rows = [
                row
                for row in rows
                if self._clean(row.customer_uuid) == customer_uuid
                or (row.customer_id is not None and int(row.customer_id) in logical_customer_id_set)
            ]
            for row in customer_rows:
                mapped_external_id = self._clean(row.external_customer_id)
                if mapped_external_id and mapped_external_id != external_customer_id:
                    raise ValueError(
                        "This FIELDCRAFT customer is already linked to a different QuickBooks customer. "
                        "Review the existing mapping before syncing again."
                    )
                row_account_id = self._clean(row.external_account_id)
                if clean_account_id and row_account_id and row_account_id != clean_account_id:
                    raise ValueError(
                        "The FIELDCRAFT customer is already linked to a different QuickBooks company. "
                        "Review the existing mapping before syncing again."
                    )

            target = None
            if mapping_id is not None:
                target = session.get(CustomerExternalMapModel, int(mapping_id))
                if not target:
                    raise ValueError(f"Customer mapping {mapping_id} was not found.")
                if target.tenant_id != normalized_tenant_id or int(target.service_id or 0) != service_id_int:
                    raise ValueError("The customer mapping belongs to a different tenant or service.")
                if not any(int(row.id) == int(target.id) for row in rows):
                    raise ValueError("The customer mapping is no longer current. Run customer sync again.")
            else:
                target = next((row for row in customer_rows if self._clean(row.external_customer_id) in ("", external_customer_id)), None)
                if target is None:
                    target = next(
                        (
                            row
                            for row in rows
                            if self._clean(row.external_customer_id) == external_customer_id
                            and row.customer_id is None
                            and not self._clean(row.customer_uuid)
                        ),
                        None,
                    )

            if target is None and not allow_create:
                raise ValueError(
                    "No recoverable customer mapping was found for the matching FIELDCRAFT customer."
                )

            for row in rows:
                if target is not None and int(row.id) == int(target.id):
                    continue
                same_external = self._clean(row.external_customer_id) == external_customer_id
                same_customer = (
                    self._clean(row.customer_uuid) == customer_uuid
                    or (row.customer_id is not None and int(row.customer_id) in logical_customer_id_set)
                )
                if same_external or same_customer:
                    row.sync_status = self.STATUS_INVALID
                    row.version_expiry_date = now
                    row.endorsement_expiry_date = now
                    row.updated_at = now
                    session.add(row)

            if target is None:
                target = self._create_mapping(
                    session,
                    tenant_id=normalized_tenant_id,
                    service_id=service_id_int,
                    customer_id=int(current_customer.id),
                    customer_uuid=current_customer.customer_uuid,
                    external_customer_id=external_customer_id,
                    external_account_id=external_account_id,
                    sync_status=self.STATUS_SYNCED,
                    version=int(current_customer.version or 1),
                    now=now,
                    reason=reason,
                    additional_info=additional_info,
                )
                # The application creates customer mappings with the customer's
                # version timeline. Preserve that same contract for recovery.
                target.version_effective_date = current_customer.version_effective_date
                target.endorsement_effective_date = current_customer.endorsement_effective_date
            else:
                target.customer_id = int(current_customer.id)
                target.customer_uuid = current_customer.customer_uuid
                target.external_customer_id = external_customer_id
                if external_account_id is not None:
                    target.external_account_id = external_account_id
                target.sync_status = self.STATUS_SYNCED
                target.reason = reason
                target.additional_info = self._merge_additional_info(
                    target.additional_info,
                    additional_info,
                )
                target.version = int(current_customer.version or target.version or 1)
                target.version_effective_date = current_customer.version_effective_date
                target.version_expiry_date = self.OPEN_END_AT
                target.endorsement_effective_date = current_customer.endorsement_effective_date
                target.endorsement_expiry_date = self.OPEN_END_AT
                target.updated_at = now
                session.add(target)

            session.flush()
            self._close_other_current_mappings_for_customer_uuid(
                session,
                tenant_id=normalized_tenant_id,
                service_id=service_id_int,
                customer_uuid=customer_uuid,
                keep_mapping_id=int(target.id),
                now=now,
            )
            return self._mapping_to_dict(target)

    def rotate_mapping_for_customer(
        self,
        tenant_id: str,
        service_id: Any,
        customer_id: int,
        external_customer_id: str,
        external_account_id: Optional[str],
        sync_status: str,
        reason: Optional[str] = None,
        additional_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = self._now()
        with customer_session_scope() as session:
            customer = session.get(CustomerModel, int(customer_id))
            if not customer:
                raise ValueError(f"Customer {customer_id} not found")

            self._acquire_customer_uuid_lock(
                session,
                tenant_id=tenant_id,
                service_id=service_id,
                customer_uuid=customer.customer_uuid,
            )

            # Customer records are versioned. Close mappings across every physical
            # customer row that belongs to the same logical customer UUID.
            existing_rows = session.execute(
                select(CustomerExternalMapModel).where(
                    CustomerExternalMapModel.customer_uuid == customer.customer_uuid,
                    CustomerExternalMapModel.tenant_id == tenant_id,
                    CustomerExternalMapModel.service_id == self._service_id_int(service_id),
                    self._is_current_mapping_clause(),
                ).order_by(CustomerExternalMapModel.id.desc())
            ).scalars().all()

            inherited_additional_info = (
                existing_rows[0].additional_info
                if existing_rows
                else None
            )
            merged_additional_info = self._merge_additional_info_dict(
                inherited_additional_info,
                additional_info,
            )

            for existing in existing_rows:
                if existing.sync_status == self.STATUS_PENDING_PUSH:
                    existing.sync_status = self.STATUS_INVALID
                existing.version_expiry_date = now
                existing.endorsement_expiry_date = now
                existing.updated_at = now
                session.add(existing)

            new_row = self._create_mapping(
                session,
                tenant_id=tenant_id,
                service_id=service_id,
                customer_id=customer_id,
                customer_uuid=customer.customer_uuid,
                external_customer_id=external_customer_id,
                external_account_id=external_account_id,
                sync_status=sync_status,
                version=customer.version,
                now=now,
                reason=reason,
                additional_info=merged_additional_info,
            )
            return self._mapping_to_dict(new_row)

    def create_pending_push_mapping(self, tenant_id: str, service_id: Any, customer_id: int, external_customer_id: str = "", external_account_id: Optional[str] = None, reason: Optional[str] = None) -> Dict[str, Any]:
        # Inherit external_customer_id/account from the existing mapping so we don't lose the QBO link
        if not external_customer_id:
            existing = self.get_current_mapping_by_customer_id(tenant_id=tenant_id, service_id=service_id, customer_id=customer_id)
            if existing:
                external_customer_id = existing.get("external_customer_id") or ""
                if external_account_id is None:
                    external_account_id = existing.get("external_account_id")
        return self.rotate_mapping_for_customer(
            tenant_id=tenant_id,
            service_id=service_id,
            customer_id=customer_id,
            external_customer_id=external_customer_id or "",
            external_account_id=external_account_id,
            sync_status=self.STATUS_PENDING_PUSH,
            reason=reason,
        )

    def rotate_customer_version(self, tenant_id: str, customer_id: int, updates: Dict[str, Any], *, service_id: Any | None = None) -> Dict[str, Any]:
        now = self._now()
        with customer_session_scope() as session:
            current = session.get(CustomerModel, int(customer_id))
            if not current:
                raise ValueError(f"Customer {customer_id} not found")

            # Row 1: close only the endorsement period on the original row (version period stays open)
            current.endorsement_expiry_date = now
            session.add(current)

            # Row 2: history copy — same data as Row 1, version period closed, endorsement stays open
            history_row = CustomerModel(
                customer_uuid=current.customer_uuid,
                firstname=current.firstname,
                lastname=current.lastname,
                middlename=current.middlename,
                account_no=current.account_no,
                birthdate=current.birthdate,
                gender=current.gender,
                email=current.email,
                mobile_no=current.mobile_no,
                alternate_mobile=current.alternate_mobile,
                status=current.status,
                eula_accepted=current.eula_accepted,
                version=current.version,
                version_effective_date=current.version_effective_date,
                version_expiry_date=now,
                is_deleted=current.is_deleted,
                endorsement_effective_date=current.endorsement_effective_date,
                endorsement_expiry_date=self.OPEN_END_AT,
                service_id=current.service_id,
            )
            session.add(history_row)
            session.flush()

            # Row 3: new active row with updated data — use Row 3's PK for all downstream references
            eff_svc_id = self._service_id_int(service_id if service_id is not None else current.service_id) or current.service_id
            new_row = CustomerModel(
                customer_uuid=current.customer_uuid,
                firstname=(updates.get("firstname") if "firstname" in updates else current.firstname),
                lastname=(updates.get("lastname") if "lastname" in updates else current.lastname),
                middlename=(updates.get("middlename") if "middlename" in updates else current.middlename),
                account_no=(updates.get("account_no") if "account_no" in updates else current.account_no),
                birthdate=(updates.get("birthdate") if "birthdate" in updates else current.birthdate),
                gender=(updates.get("gender") if "gender" in updates else current.gender),
                email=(updates.get("email") if "email" in updates else current.email),
                mobile_no=(updates.get("mobile_no") if "mobile_no" in updates else updates.get(const.KEY_PHONE) if const.KEY_PHONE in updates else current.mobile_no),
                alternate_mobile=(updates.get("alternate_mobile") if "alternate_mobile" in updates else current.alternate_mobile),
                status=(updates.get(const.KEY_STATUS) if const.KEY_STATUS in updates else current.status),
                eula_accepted=(updates.get("eula_accepted") if "eula_accepted" in updates else current.eula_accepted),
                version=int(current.version) + 1,
                version_effective_date=current.version_effective_date,
                version_expiry_date=self.OPEN_END_AT,
                is_deleted=bool(updates.get("is_deleted", current.is_deleted)),
                endorsement_effective_date=now,
                endorsement_expiry_date=self.OPEN_END_AT,
                service_id=eff_svc_id,
            )
            session.add(new_row)
            session.flush()
            self._move_customer_tenant_link(session, tenant_id=tenant_id, customer_uuid=current.customer_uuid, to_customer_id=int(new_row.id), now=now)
            session.flush()
            return self._normalize_customer_row(new_row)

    def create_customer_version_from_qbo(self, tenant_id: str, customer_id: int, qbo_customer: Dict[str, Any]) -> Dict[str, Any]:
        name = (qbo_customer.get(const.QBO_KEY_DISPLAY_NAME) or "").strip()
        given = (qbo_customer.get("GivenName") or "").strip()
        family = (qbo_customer.get("FamilyName") or "").strip()
        if not given and name:
            parts = name.split()
            given = parts[0]
            family = " ".join(parts[1:]) if len(parts) > 1 else ""
        pe = qbo_customer.get("PrimaryEmailAddr") or {}
        phone_block = qbo_customer.get("PrimaryPhone") or {}
        mobile_block = qbo_customer.get("Mobile") or {}
        phone_value = (phone_block.get("FreeFormNumber") or "").strip()
        if not phone_value:
            phone_value = (mobile_block.get("FreeFormNumber") or "").strip()
        clean_phone = "".join(c for c in phone_value if c.isdigit()) or None
        logger.debug("Creating customer version from QuickBooks | customer_id=%s phone_present=%s", customer_id, bool(clean_phone))
        return self.rotate_customer_version(
            tenant_id=tenant_id,
            customer_id=customer_id,
            updates={
                "firstname": given or None,
                "lastname": family or None,
                "email": (pe.get("Address") or "").strip() or None,
                "mobile_no": clean_phone,
                const.KEY_STATUS: "Inactive" if qbo_customer.get("Active") is False else "Active",
                "is_deleted": qbo_customer.get("Active") is False,
            },
        )


    def get_import_changed_since(self, tenant_id: str, service_id: Any) -> Optional[datetime]:
        """Return the latest known customer-import watermark for a tenant/service.

        We use the latest mapping `updated_at` for linked customer rows as a practical
        watermark for incremental import. When no mapping exists yet, callers should
        perform a full import bootstrap.
        """
        with customer_session_scope() as session:
            stmt = (
                select(func.max(CustomerExternalMapModel.updated_at))
                .where(
                    CustomerExternalMapModel.tenant_id == tenant_id,
                    CustomerExternalMapModel.service_id == self._service_id_int(service_id),
                    CustomerExternalMapModel.external_customer_id != "",
                )
            )
            changed_since = session.execute(stmt).scalar_one_or_none()
            if changed_since is not None and changed_since.tzinfo is None:
                changed_since = changed_since.replace(tzinfo=timezone.utc)
            logger.debug("Customer import watermark loaded | tenant_id=%s service_id=%s present=%s", tenant_id, service_id, changed_since is not None)
            return changed_since

    def get_pending_push_mappings_for_tenant(
        self,
        tenant_id: str,
        service_id: Any,
        limit: int = 500,
        offset: int = 0,
        include_mapping_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Return current active outbound mapping rows for a tenant/service.

        The mapping table is the outbound sync queue. Only current active mappings for the
        given tenant/service in a pending or failed state are returned.
        """
        logger.debug("Loading pending customer mappings | tenant_id=%s service_id=%s", tenant_id, service_id)
        with customer_session_scope() as session:
            stmt = (
                select(CustomerExternalMapModel)
                .where(
                    CustomerExternalMapModel.tenant_id == tenant_id,
                    CustomerExternalMapModel.service_id == self._service_id_int(service_id),
                    self._is_current_mapping_clause(),
                    CustomerExternalMapModel.sync_status.in_(self.EXPORTABLE_SYNC_STATUSES),
                )
                .order_by(CustomerExternalMapModel.version_effective_date.asc(), CustomerExternalMapModel.id.asc())
                .offset(int(offset))
                .limit(int(limit))
            )
            included = sorted({int(value) for value in (include_mapping_ids or []) if value is not None})
            if included:
                stmt = stmt.where(CustomerExternalMapModel.id.in_(included))
            rows = session.execute(stmt).scalars().all()
            logger.debug("Pending customer mappings loaded | count=%s", len(rows))
            return [self._mapping_to_dict(row) for row in rows]

    def get_export_candidates(
        self,
        tenant_id: str,
        service_id: Any,
        limit: int = 500,
        offset: int = 0,
        include_mapping_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Return tenant-scoped outbound export candidates.

        Candidate selection is mapping-driven:
          - fetch only current active mapping rows for the tenant/service
          - include only mappings in PENDING_PUSH or FAILED state
          - load the linked current customer row to build the export payload

        This avoids scanning all current customers during export.
        """
        logger.debug("Loading customer export candidates | tenant_id=%s service_id=%s limit=%s offset=%s", tenant_id, service_id, limit, offset)
        results: List[Dict[str, Any]] = []
        pending_mappings = self.get_pending_push_mappings_for_tenant(
            tenant_id=tenant_id,
            service_id=service_id,
            limit=limit,
            offset=offset,
            include_mapping_ids=include_mapping_ids,
        )
        logger.debug("Customer candidate mappings loaded | count=%s", len(pending_mappings))
        for mapping in pending_mappings:
            customer = self.get_current_customer_by_id(mapping["customer_id"])
            if customer is None:
                logger.warning("Current customer missing for mapping | mapping_id=%s customer_id=%s", mapping.get("id"), mapping.get("customer_id"))
                continue
            customer["personal_additional_info"] = self.get_personal_additional_info(tenant_id, customer.get("customer_uuid") or "")
            logger.debug("Customer export candidate ready | customer_id=%s mapping_id=%s status=%s", customer.get("id"), mapping.get("id"), mapping.get("sync_status"))
            results.append({
                "customer": customer,
                "mapping": mapping,
            })
        logger.debug("Customer export candidates ready | count=%s", len(results))
        return results


    def _load_service_source_map(self) -> Dict[int, str]:
        """Return a map of service_master.id → 'QuickBooks' | 'FIELDCRAFT' by looking up names."""
        from source.dal.db_session import service_session_scope
        from source.dal.service_models import ServiceMasterModel
        result: Dict[int, str] = {}
        try:
            with service_session_scope() as session:
                rows = session.execute(
                    select(ServiceMasterModel).where(ServiceMasterModel.invalid.isnot(True))
                ).scalars().all()
                for row in rows:
                    name = (row.name or "").strip()
                    result[int(row.id)] = "QuickBooks" if name == "QuickBooks" else "FIELDCRAFT"
        except Exception as exc:
            logger.warning("Could not load service source names", exc_info=exc)
        return result

    def get_customer_cdc_page(
        self,
        tenant_id: str,
        service_id: Any,
        *,
        page: int = 1,
        page_size: int = 25,
        search: Optional[str] = None,
        sync_status: Optional[str] = None,
        mapped_only: bool = False,
    ) -> Dict[str, Any]:
        """Return the customer sync view using customer_external_map as the source of truth.

        The UI should show the same set of records that QuickBooks sync tracks.
        Customer rows are joined only for display/reference data.
        """
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 25), 1), 100)
        offset = (page - 1) * page_size
        service_id_int = self._service_id_int(service_id)
        search_term = (search or "").strip().lower()
        normalized_status = (sync_status or "").strip().upper()
        service_source_map = self._load_service_source_map()

        def _parse_reason(reason: Any) -> Dict[str, Any]:
            if not reason:
                return {}
            try:
                parsed = json.loads(str(reason))
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {"message": str(reason)}

        with customer_session_scope() as session:
            # customer_uuid is a PostgreSQL UUID column in both customer tables.
            # Cast it to text before applying NULLIF/COALESCE; comparing a UUID
            # directly with an empty string raises InvalidTextRepresentation.
            mapping_uuid_text = cast(CustomerExternalMapModel.customer_uuid, Text)
            mapping_group_key = func.coalesce(
                func.nullif(mapping_uuid_text, ""),
                func.nullif(CustomerExternalMapModel.external_customer_id, ""),
                cast(CustomerExternalMapModel.id, Text),
            )
            latest_mapping_ids = (
                select(func.max(CustomerExternalMapModel.id).label("map_id"))
                .where(
                    CustomerExternalMapModel.tenant_id == tenant_id,
                    CustomerExternalMapModel.service_id == service_id_int,
                    self._is_current_mapping_clause(),
                    func.upper(func.coalesce(CustomerExternalMapModel.sync_status, "")) != self.STATUS_INVALID,
                )
                .group_by(mapping_group_key)
                .subquery()
            )

            current_customer_ids = self._current_customer_ids_subquery()

            base_stmt = (
                select(CustomerExternalMapModel, CustomerModel)
                .where(CustomerExternalMapModel.id.in_(select(latest_mapping_ids.c.map_id)))
                .outerjoin(
                    current_customer_ids,
                    current_customer_ids.c.customer_uuid == CustomerExternalMapModel.customer_uuid,
                )
                .outerjoin(
                    CustomerModel,
                    CustomerModel.id == current_customer_ids.c.customer_id,
                )
            )

            if normalized_status and normalized_status != "ALL":
                status_col = func.upper(func.coalesce(CustomerExternalMapModel.sync_status, ""))
                if normalized_status == "NEEDS_ATTENTION":
                    # "Needs attention" is a UI summary bucket, not a stored status. Expand it
                    # to the same statuses the summary counts (failed + import-failed +
                    # sync-failed) so the filtered rows match the pill's count.
                    base_stmt = base_stmt.where(
                        status_col.in_([self.STATUS_FAILED, self.STATUS_IMPORT_FAILED, self.STATUS_SYNC_FAILED])
                    )
                else:
                    base_stmt = base_stmt.where(status_col == normalized_status)

            if search_term:
                like_term = f"%{search_term}%"
                base_stmt = base_stmt.where(
                    or_(
                        func.lower(func.coalesce(CustomerModel.firstname, "")).like(like_term),
                        func.lower(func.coalesce(CustomerModel.lastname, "")).like(like_term),
                        func.lower(func.coalesce(CustomerModel.email, "")).like(like_term),
                        func.lower(func.coalesce(CustomerModel.mobile_no, "")).like(like_term),
                        func.lower(func.coalesce(cast(CustomerModel.customer_uuid, Text), "")).like(like_term),
                        func.lower(func.coalesce(mapping_uuid_text, "")).like(like_term),
                        func.lower(func.coalesce(CustomerExternalMapModel.external_customer_id, "")).like(like_term),
                        func.lower(func.coalesce(CustomerExternalMapModel.external_account_id, "")).like(like_term),
                        func.lower(func.coalesce(CustomerExternalMapModel.reason, "")).like(like_term),
                    )
                )

            total_records = int(session.execute(select(func.count()).select_from(base_stmt.subquery())).scalar() or 0)
            records = session.execute(
                base_stmt
                .order_by(CustomerExternalMapModel.updated_at.desc().nullslast(), CustomerExternalMapModel.id.desc())
                .offset(offset)
                .limit(page_size)
            ).all()

            rows: List[Dict[str, Any]] = []
            for mapping_row, customer_row in records:
                mapping = self._mapping_to_dict(mapping_row)
                customer = self._normalize_customer_row(customer_row) if customer_row else {}
                reason_payload = _parse_reason(mapping.get("reason"))
                first_name = (customer.get("first_name") or "").strip()
                last_name = (customer.get("last_name") or "").strip()
                display_name = " ".join(part for part in (first_name, last_name) if part).strip()
                qbo_id = mapping.get("external_customer_id") or reason_payload.get("qbo_customer_id")
                qbo_display_name = (
                    reason_payload.get("display_name")
                    or reason_payload.get("displayName")
                    or reason_payload.get("qbo_display_name")
                    or reason_payload.get("company_name")
                )
                qbo_email = reason_payload.get("email") or reason_payload.get("qbo_email")
                qbo_phone = reason_payload.get("phone") or reason_payload.get("qbo_phone")

                rows.append({
                    "customerId": customer.get("id"),
                    "customerUuid": customer.get("customer_uuid") or mapping.get("customer_uuid"),
                    "displayName": display_name or customer.get("email") or qbo_display_name or (f"QuickBooks customer {qbo_id}" if qbo_id else f"Customer mapping #{mapping['id']}"),
                    "email": customer.get("email") or qbo_email,
                    "phone": customer.get("phone") or qbo_phone,
                    "customerStatus": customer.get("status"),
                    "mappingId": mapping.get("id"),
                    "externalCustomerId": qbo_id,
                    "externalAccountId": mapping.get("external_account_id"),
                    "syncStatus": mapping.get("sync_status") or "UNKNOWN",
                    "mappingVersion": mapping.get("version"),
                    "mappingUpdatedAt": mapping.get("updated_at"),
                    "linked": customer_row is not None,
                    "reason": mapping.get("reason"),
                    "source": service_source_map.get(customer.get("service_id"), "FIELDCRAFT") if customer_row is not None else "QuickBooks",
                })

            summary_stmt = (
                select(CustomerExternalMapModel.sync_status, func.count())
                .where(CustomerExternalMapModel.id.in_(select(latest_mapping_ids.c.map_id)))
                .group_by(CustomerExternalMapModel.sync_status)
            )
            status_counts: Dict[str, int] = {}
            for status, count in session.execute(summary_stmt).all():
                key = (status or "UNKNOWN").upper()
                status_counts[key] = int(count or 0)

            total_customers = int(sum(status_counts.values()))
            summary = {
                "totalCustomers": total_customers,
                "mappedCustomers": total_customers,
                "unmappedCustomers": 0,
                "syncedCustomers": status_counts.get(self.STATUS_SYNCED, 0),
                "pendingPushCustomers": status_counts.get(self.STATUS_PENDING_PUSH, 0),
                "failedCustomers": status_counts.get(self.STATUS_FAILED, 0),
                "importFailedCustomers": status_counts.get(self.STATUS_IMPORT_FAILED, 0),
                "syncFailedCustomers": status_counts.get(self.STATUS_SYNC_FAILED, 0),
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

    def get_customer_sync_state(self, tenant_id: str, service_id: Any, limit: int = 200, offset: int = 0) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        customers = self.get_current_customers_for_tenant(tenant_id=tenant_id, service_id=service_id, limit=limit, offset=offset)
        for customer in customers:
            mapping = self.get_current_mapping_by_customer_id(tenant_id=tenant_id, service_id=service_id, customer_id=customer["id"])
            rows.append(
                {
                    "customerId": customer["id"],
                    "customerUuid": customer["customer_uuid"],
                    "displayName": " ".join(filter(None, [customer.get("first_name"), customer.get("last_name")])).strip(),
                    const.KEY_EMAIL: customer.get(const.KEY_EMAIL),
                    const.KEY_PHONE: customer.get(const.KEY_PHONE),
                    "version": customer.get("version"),
                    "externalCustomerId": (mapping or {}).get("external_customer_id"),
                    "externalAccountId": (mapping or {}).get("external_account_id"),
                    "syncStatus": (mapping or {}).get("sync_status") or "UNMAPPED",
                    "mappingVersion": (mapping or {}).get("version"),
                    "mappingUpdatedAt": (mapping or {}).get("updated_at"),
                    "reason": (mapping or {}).get("reason"),
                }
            )
        return rows
