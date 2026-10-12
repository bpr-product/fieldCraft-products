from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import Text, and_, case, cast, func, select

from source.dal.chart_of_account_models import ChartOfAccountModel
from source.dal.db_session import tenant_session_scope


logger = logging.getLogger(__name__)


class ChartOfAccountRepository:
    """Read/write chart_of_account in the tenant DB using SQLAlchemy."""

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()

    def _active_record_filter(self):
        return func.coalesce(ChartOfAccountModel.invalid, False).is_(False)

    def _service_id_text(self, service_id: Any) -> str:
        return self._clean(service_id)

    def _service_id_match(self, service_id: Any):
        return cast(ChartOfAccountModel.service_id, Text) == self._service_id_text(service_id)

    def _normalize_account_row(self, row: ChartOfAccountModel, *, service_id_as_str: bool = False) -> Dict[str, Any]:
        service_id = row.service_id
        if service_id_as_str and service_id is not None:
            service_id = str(service_id)

        return {
            "id": int(row.id),
            "account_type": row.account_type,
            "active": bool(row.active) if row.active is not None else True,
            "builtin": bool(row.builtin) if row.builtin is not None else False,
            "code": row.code,
            "code_provider": row.code_provider,
            "currency_code": row.currency_code,
            "description": row.description,
            "name": row.name,
            "account_no": row.account_no,
            "detail_type": row.detail_type,
            "external_id": row.external_id,
            "sync_token": row.sync_token,
            "service_id": service_id,
            "external_acc_id": row.external_acc_id,
            "invalid": bool(row.invalid) if row.invalid is not None else False,
            "reason": row.reason,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }


    def _normalize_account_kind(self, value: Any) -> str:
        return ''.join(ch for ch in self._clean(value).lower() if ch.isalnum())

    def get_bill_payment_pay_type(self, account_row: Optional[Dict[str, Any]]) -> Optional[str]:
        if not account_row:
            return None
        account_type = self._normalize_account_kind(account_row.get("account_type"))
        detail_type = self._normalize_account_kind(account_row.get("detail_type"))
        tokens = {account_type, detail_type}
        if any(token in {"creditcard", "ccard", "credit"} for token in tokens):
            return "CreditCard"
        if any(token in {"bank", "checking", "savings"} for token in tokens):
            return "Check"
        return None

    def is_bill_payment_eligible_account(self, account_row: Optional[Dict[str, Any]]) -> bool:
        if not account_row:
            return False
        if not bool(account_row.get("active", True)):
            return False
        if bool(account_row.get("invalid", False)):
            return False
        if not self._clean(account_row.get("external_id")):
            return False
        return self.get_bill_payment_pay_type(account_row) in {"Check", "CreditCard"}

    def is_service_income_account(self, account_row: Optional[Dict[str, Any]]) -> bool:
        if not account_row:
            return False
        if not bool(account_row.get("active", True)):
            return False
        if bool(account_row.get("invalid", False)):
            return False
        if not self._clean(account_row.get("external_id")):
            return False
        account_type = self._normalize_account_kind(account_row.get("account_type"))
        detail_type = self._normalize_account_kind(account_row.get("detail_type"))
        return account_type in {"income", "otherincome"} or detail_type in {"income", "servicefeeincome", "salesofproductincome"}


    def list_service_coa_accounts(
        self,
        tenant_id: str,
        *,
        service_id: str,
        external_acc_id: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Return active synced COA rows that have a QuickBooks external id.

        Service-role configuration can use any QuickBooks-mapped COA selected by
        the user. We do not limit the dropdown to Income accounts here.
        """
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(ChartOfAccountModel)
                .where(
                    self._service_id_match(service_id),
                    self._active_record_filter(),
                    func.coalesce(ChartOfAccountModel.active, True).is_(True),
                    ChartOfAccountModel.external_id.is_not(None),
                    ChartOfAccountModel.external_id != "",
                )
                .order_by(ChartOfAccountModel.account_type.asc(), ChartOfAccountModel.name.asc(), ChartOfAccountModel.id.asc())
                .limit(int(limit))
            )
            if self._clean(external_acc_id):
                stmt = stmt.where(ChartOfAccountModel.external_acc_id == self._clean(external_acc_id))
            rows = session.execute(stmt).scalars().all()
            return [self._normalize_account_row(row, service_id_as_str=True) for row in rows]

    def get_service_coa_account_by_id(
        self,
        tenant_id: str,
        *,
        service_id: str,
        account_id: int,
        external_acc_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return one active synced COA row by FIELDCRAFT chart_of_account id."""
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(ChartOfAccountModel)
                .where(
                    ChartOfAccountModel.id == int(account_id),
                    self._service_id_match(service_id),
                    self._active_record_filter(),
                    func.coalesce(ChartOfAccountModel.active, True).is_(True),
                    ChartOfAccountModel.external_id.is_not(None),
                    ChartOfAccountModel.external_id != "",
                )
                .limit(1)
            )
            if self._clean(external_acc_id):
                stmt = stmt.where(ChartOfAccountModel.external_acc_id == self._clean(external_acc_id))
            row = session.execute(stmt).scalars().first()
            return self._normalize_account_row(row, service_id_as_str=True) if row else None

    def list_service_income_accounts(
        self,
        tenant_id: str,
        *,
        service_id: str,
        external_acc_id: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(ChartOfAccountModel)
                .where(
                    self._service_id_match(service_id),
                    self._active_record_filter(),
                    func.coalesce(ChartOfAccountModel.active, True).is_(True),
                    ChartOfAccountModel.external_id.is_not(None),
                    ChartOfAccountModel.external_id != "",
                )
                .order_by(ChartOfAccountModel.name.asc(), ChartOfAccountModel.id.asc())
                .limit(int(limit))
            )
            if self._clean(external_acc_id):
                stmt = stmt.where(ChartOfAccountModel.external_acc_id == self._clean(external_acc_id))
            rows = session.execute(stmt).scalars().all()
            results: List[Dict[str, Any]] = []
            for row in rows:
                normalized = self._normalize_account_row(row, service_id_as_str=True)
                if not self.is_service_income_account(normalized):
                    continue
                results.append(normalized)
            return results

    def get_service_income_account_by_id(
        self,
        tenant_id: str,
        *,
        service_id: str,
        account_id: int,
        external_acc_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(ChartOfAccountModel)
                .where(
                    ChartOfAccountModel.id == int(account_id),
                    self._service_id_match(service_id),
                    self._active_record_filter(),
                    func.coalesce(ChartOfAccountModel.active, True).is_(True),
                    ChartOfAccountModel.external_id.is_not(None),
                    ChartOfAccountModel.external_id != "",
                )
                .limit(1)
            )
            if self._clean(external_acc_id):
                stmt = stmt.where(ChartOfAccountModel.external_acc_id == self._clean(external_acc_id))
            row = session.execute(stmt).scalars().first()
            if not row:
                return None
            normalized = self._normalize_account_row(row, service_id_as_str=True)
            return normalized if self.is_service_income_account(normalized) else None

    def _mapping_dict(self, row: ChartOfAccountModel, *, service_id_as_str: bool = False) -> Dict[str, Any]:
        service_id = row.service_id
        if service_id_as_str and service_id is not None:
            service_id = str(service_id)
        return {
            "id": int(row.id),
            "external_id": self._clean(row.external_id) or None,
            "sync_token": self._clean(row.sync_token) or None,
            "service_id": service_id,
            "invalid": bool(row.invalid) if row.invalid is not None else False,
        }

    def get_accounts_for_tenant(self, tenant_id: str, *, limit: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
        """Return paged, non-invalid chart of account rows for export.

        Soft-deleted rows are filtered at the database layer to avoid wasting
        DB I/O and pagination slots on invalid records.
        """
        try:
            with tenant_session_scope(tenant_id) as session:
                stmt = (
                    select(ChartOfAccountModel)
                    .where(self._active_record_filter())
                    .order_by(ChartOfAccountModel.id.asc())
                    .offset(int(offset))
                    .limit(int(limit))
                )
                rows = session.execute(stmt).scalars().all()
                return [self._normalize_account_row(row) for row in rows]
        except Exception:
            logger.exception("Failed to list chart of account export candidates | tenant_id=%s", tenant_id)
            raise

    def get_by_external_id(
        self,
        tenant_id: str,
        *,
        external_id: str,
        service_id: str,
        external_acc_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Find a chart_of_account row already mapped to the given QBO Id."""
        external_id = self._clean(external_id)
        if not external_id:
            return None

        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(ChartOfAccountModel)
                .where(
                    ChartOfAccountModel.external_id == external_id,
                    self._service_id_match(service_id),
                    ChartOfAccountModel.external_acc_id == self._clean(external_acc_id),
                )
                .limit(1)
            )
            row = session.execute(stmt).scalars().first()
            if not row:
                return None
            return {
                "id": int(row.id),
                "name": row.name,
                "account_no": row.account_no,
                "external_id": row.external_id,
                "sync_token": row.sync_token,
                "service_id": row.service_id,
                "external_acc_id": row.external_acc_id,
            }

    def upsert_mapping(
        self,
        tenant_id: str,
        *,
        account_id: int,
        external_id: str,
        sync_token: Optional[str],
        service_id: str,
        external_acc_id: str,
    ) -> None:
        """Update mapping fields on chart_of_account (QBO Id + SyncToken)."""
        try:
            with tenant_session_scope(tenant_id) as session:
                row = session.get(ChartOfAccountModel, int(account_id))
                if row is None:
                    raise Exception(f"chart_of_account not found for id={account_id}")

                row.external_id = self._clean(external_id) or None
                row.sync_token = self._clean(sync_token) or None
                row.service_id = int(self._service_id_text(service_id)) if self._service_id_text(service_id) else None
                row.external_acc_id = self._clean(external_acc_id) or None
                row.updated_at = datetime.now(timezone.utc)
                session.commit()
        except Exception:
            logger.exception("Failed to update chart of account mapping | tenant_id=%s account_id=%s", tenant_id, account_id)
            raise

    def upsert_reason(
        self,
        tenant_id: str,
        *,
        account_id: int,
        reason: Optional[str],
    ) -> None:
        """Persist a user-facing reason/message on the chart_of_account row."""
        try:
            with tenant_session_scope(tenant_id) as session:
                row = session.get(ChartOfAccountModel, int(account_id))
                if row is None:
                    raise Exception(f"chart_of_account not found for id={account_id}")
                row.reason = self._clean(reason) or None
                row.updated_at = datetime.now(timezone.utc)
                session.commit()
        except Exception:
            logger.exception("Failed to update chart of account reason | tenant_id=%s account_id=%s", tenant_id, account_id)
            raise

    def get_external_id_for_account_id(
        self,
        tenant_id: str,
        *,
        account_id: int,
        service_id: str,
        external_acc_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return one active QuickBooks account mapping for the current company."""
        try:
            with tenant_session_scope(tenant_id) as session:
                stmt = select(ChartOfAccountModel).where(
                    ChartOfAccountModel.id == int(account_id),
                    self._service_id_match(service_id),
                    self._active_record_filter(),
                    func.coalesce(ChartOfAccountModel.active, True).is_(True),
                    ChartOfAccountModel.external_id.is_not(None),
                    ChartOfAccountModel.external_id != "",
                )
                realm_id = self._clean(external_acc_id)
                if realm_id:
                    stmt = stmt.where(ChartOfAccountModel.external_acc_id == realm_id)
                row = session.execute(stmt.limit(1)).scalars().first()
                return self._mapping_dict(row, service_id_as_str=True) if row else None
        except Exception:
            logger.exception(
                "Failed to resolve QuickBooks account mapping by FIELDCRAFT account id | "
                "tenant_id=%s account_id=%s service_id=%s",
                tenant_id,
                account_id,
                service_id,
            )
            raise

    def get_external_id_for_account_name(
        self,
        tenant_id: str,
        *,
        name: str,
        service_id: str,
        external_acc_id: Optional[str] = None,
        allowed_account_types: Optional[Iterable[str]] = None,
        allowed_detail_types: Optional[Iterable[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Resolve an exact-name QuickBooks account without ambiguous fallback.

        The current QuickBooks company and optional account classifications are
        enforced before an identifier is returned. Multiple valid matches are
        rejected instead of silently choosing an arbitrary ledger account.
        """
        account_name = self._clean(name)
        if not account_name:
            return None

        try:
            allowed_types = {
                self._normalize_account_kind(value)
                for value in (allowed_account_types or [])
                if self._clean(value)
            }
            allowed_details = {
                self._normalize_account_kind(value)
                for value in (allowed_detail_types or [])
                if self._clean(value)
            }

            with tenant_session_scope(tenant_id) as session:
                stmt = select(ChartOfAccountModel).where(
                    func.lower(func.coalesce(ChartOfAccountModel.name, "")) == account_name.lower(),
                    self._service_id_match(service_id),
                    self._active_record_filter(),
                    func.coalesce(ChartOfAccountModel.active, True).is_(True),
                    ChartOfAccountModel.external_id.is_not(None),
                    ChartOfAccountModel.external_id != "",
                )
                realm_id = self._clean(external_acc_id)
                if realm_id:
                    stmt = stmt.where(ChartOfAccountModel.external_acc_id == realm_id)

                candidates: List[Dict[str, Any]] = []
                rows = session.execute(
                    stmt.order_by(ChartOfAccountModel.id.asc())
                ).scalars().all()
                for row in rows:
                    if allowed_types and self._normalize_account_kind(row.account_type) not in allowed_types:
                        continue
                    if allowed_details and self._normalize_account_kind(row.detail_type) not in allowed_details:
                        continue
                    candidates.append(self._mapping_dict(row, service_id_as_str=True))

                if not candidates:
                    return None
                if len(candidates) > 1:
                    raise ValueError(
                        f"Multiple active QuickBooks accounts named '{account_name}' match the required "
                        "account type. Keep one active mapping for the connected company and retry."
                    )
                return candidates[0]
        except Exception:
            logger.exception(
                "Failed to resolve QuickBooks account mapping by name | "
                "tenant_id=%s account_name=%s service_id=%s",
                tenant_id,
                account_name,
                service_id,
            )
            raise

    # ----------------------
    # Import helpers (QBO -> FIELDCRAFT)
    # ----------------------

    def get_by_account_no(self, tenant_id: str, *, account_no: str) -> Optional[Dict[str, Any]]:
        account_no = self._clean(account_no)
        if not account_no:
            return None

        with tenant_session_scope(tenant_id) as session:
            stmt = select(ChartOfAccountModel).where(ChartOfAccountModel.account_no == account_no).limit(1)
            row = session.execute(stmt).scalars().first()
            return self._normalize_account_row(row) if row else None

    def get_by_name(self, tenant_id: str, *, name: str) -> Optional[Dict[str, Any]]:
        name = self._clean(name)
        if not name:
            return None

        with tenant_session_scope(tenant_id) as session:
            stmt = select(ChartOfAccountModel).where(ChartOfAccountModel.name == name).limit(1)
            row = session.execute(stmt).scalars().first()
            return self._normalize_account_row(row) if row else None

    def get_by_external_id_and_service(
        self,
        tenant_id: str,
        *,
        external_id: str,
        service_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Find by QBO Id for a given service (ignores realm/external_acc_id)."""
        external_id = self._clean(external_id)
        if not external_id:
            return None

        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(ChartOfAccountModel)
                .where(
                    ChartOfAccountModel.external_id == external_id,
                    self._service_id_match(service_id),
                )
                .limit(1)
            )
            row = session.execute(stmt).scalars().first()
            if not row:
                return None
            return self._normalize_account_row(row, service_id_as_str=True)

    def get_by_name_type_detail(
        self,
        tenant_id: str,
        *,
        name: str,
        account_type: str,
        detail_type: str,
    ) -> Optional[Dict[str, Any]]:
        """Fallback match: name + account_type + detail_type."""
        name = self._clean(name)
        account_type = self._clean(account_type)
        detail_type = self._clean(detail_type)
        if not name or not account_type or not detail_type:
            return None

        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(ChartOfAccountModel)
                .where(
                    ChartOfAccountModel.name == name,
                    ChartOfAccountModel.account_type == account_type,
                    ChartOfAccountModel.detail_type == detail_type,
                )
                .limit(1)
            )
            row = session.execute(stmt).scalars().first()
            if not row:
                return None
            return {
                "id": int(row.id),
                "name": row.name,
                "account_no": row.account_no,
                "account_type": row.account_type,
                "detail_type": row.detail_type,
                "external_id": row.external_id,
                "sync_token": row.sync_token,
                "service_id": row.service_id,
                "external_acc_id": row.external_acc_id,
            }

    def insert_account_from_qbo(
        self,
        *,
        tenant_id: str,
        name: str,
        account_no: str,
        qbo_account_type: str,
        qbo_account_sub_type: Optional[str],
        description: Optional[str],
        active: bool,
        currency_code: Optional[str],
        external_id: str,
        sync_token: Optional[str],
        service_id: str,
        external_acc_id: str,
        source_updated_at: Optional[datetime] = None,
    ) -> int:
        """Create a new chart_of_account row based on a QBO Account."""
        now = source_updated_at or datetime.now(timezone.utc)
        with tenant_session_scope(tenant_id) as session:
            row = ChartOfAccountModel(
                account_type=self._clean(qbo_account_type) or None,
                active=bool(active),
                builtin=False,
                code=None,
                code_provider=None,
                currency_code=self._clean(currency_code) or None,
                description=self._clean(description) or None,
                name=self._clean(name),
                account_no=self._clean(account_no),
                detail_type=self._clean(qbo_account_sub_type) or None,
                external_id=self._clean(external_id) or None,
                sync_token=self._clean(sync_token) or None,
                service_id=int(self._service_id_text(service_id)) if self._service_id_text(service_id) else None,
                external_acc_id=self._clean(external_acc_id) or None,
                invalid=False,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            if row.id is None:
                raise Exception("Insert chart_of_account failed: no id returned")
            row_id = int(row.id)
            session.commit()
            return row_id

    def update_account_from_qbo(
        self,
        *,
        tenant_id: str,
        account_id: int,
        name: Optional[str],
        account_no: Optional[str],
        qbo_account_type: Optional[str],
        qbo_account_sub_type: Optional[str],
        description: Optional[str],
        active: Optional[bool],
        currency_code: Optional[str],
        source_updated_at: Optional[datetime] = None,
    ) -> None:
        """Update selected fields on chart_of_account based on a QBO Account."""
        with tenant_session_scope(tenant_id) as session:
            row = session.get(ChartOfAccountModel, int(account_id))
            if row is None:
                return

            clean_name = self._clean(name)
            clean_account_no = self._clean(account_no)
            clean_detail_type = self._clean(qbo_account_sub_type)
            clean_account_type = self._clean(qbo_account_type)
            clean_description = self._clean(description)
            clean_currency_code = self._clean(currency_code)

            if clean_name:
                row.name = clean_name
            if clean_account_no:
                row.account_no = clean_account_no
            row.detail_type = clean_detail_type or None
            if (row.account_type is None or self._clean(row.account_type) == "") and clean_account_type:
                row.account_type = clean_account_type
            if clean_description:
                row.description = clean_description
            if clean_currency_code:
                row.currency_code = clean_currency_code
            if active is not None:
                row.active = bool(active)
            row.updated_at = source_updated_at or datetime.now(timezone.utc)
            session.commit()

    def list_by_name(self, tenant_id: str, *, name: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Return up to `limit` COA rows that match the given name (case-insensitive)."""
        name = self._clean(name)
        if not name:
            return []

        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(ChartOfAccountModel)
                .where(func.lower(func.coalesce(ChartOfAccountModel.name, "")) == name.lower())
                .order_by(ChartOfAccountModel.id.asc())
                .limit(int(limit))
            )
            rows = session.execute(stmt).scalars().all()
            return [
                {
                    "id": int(row.id),
                    "account_type": row.account_type,
                    "detail_type": row.detail_type,
                    "name": row.name,
                    "account_no": row.account_no,
                    "external_id": row.external_id,
                    "sync_token": row.sync_token,
                    "service_id": row.service_id,
                    "external_acc_id": row.external_acc_id,
                    "invalid": bool(row.invalid) if row.invalid is not None else False,
                }
                for row in rows
            ]

    def update_account_safe_fields_from_qbo(
        self,
        *,
        tenant_id: str,
        account_id: int,
        name: Optional[str],
        account_no: Optional[str],
        description: Optional[str],
        active: Optional[bool],
        currency_code: Optional[str],
        account_type: Optional[str] = None,
        detail_type: Optional[str] = None,
        source_updated_at: Optional[datetime] = None,
    ) -> None:
        """Update fields from QBO when QBO is newer than FIELDCRAFT (timestamp-based)."""
        with tenant_session_scope(tenant_id) as session:
            row = session.get(ChartOfAccountModel, int(account_id))
            if row is None:
                return

            clean_name = self._clean(name)
            clean_account_no = self._clean(account_no)
            clean_description = self._clean(description)
            clean_currency_code = self._clean(currency_code)
            clean_account_type = self._clean(account_type)
            clean_detail_type = self._clean(detail_type)

            if clean_name:
                row.name = clean_name
            if clean_account_no:
                row.account_no = clean_account_no
            if clean_description:
                row.description = clean_description
            if clean_currency_code:
                row.currency_code = clean_currency_code
            if active is not None:
                row.active = bool(active)
            if clean_account_type:
                row.account_type = clean_account_type
            row.detail_type = clean_detail_type or None
            row.updated_at = source_updated_at or datetime.now(timezone.utc)
            session.commit()

    def get_account_by_id(self, tenant_id: str, *, account_id: int) -> Optional[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            row = session.get(ChartOfAccountModel, int(account_id))
            return self._normalize_account_row(row) if row else None


    def list_bill_payment_eligible_accounts(
        self,
        tenant_id: str,
        *,
        service_id: str,
        external_acc_id: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(ChartOfAccountModel)
                .where(
                    self._service_id_match(service_id),
                    self._active_record_filter(),
                    func.coalesce(ChartOfAccountModel.active, True).is_(True),
                    ChartOfAccountModel.external_id.is_not(None),
                    ChartOfAccountModel.external_id != "",
                )
                .order_by(ChartOfAccountModel.name.asc(), ChartOfAccountModel.id.asc())
                .limit(int(limit))
            )
            if self._clean(external_acc_id):
                stmt = stmt.where(ChartOfAccountModel.external_acc_id == self._clean(external_acc_id))
            rows = session.execute(stmt).scalars().all()
            results: List[Dict[str, Any]] = []
            for row in rows:
                normalized = self._normalize_account_row(row, service_id_as_str=True)
                pay_type = self.get_bill_payment_pay_type(normalized)
                if pay_type is None:
                    continue
                normalized["eligible_pay_type"] = pay_type
                results.append(normalized)
            return results

    def get_bill_payment_eligible_account_by_external_id(
        self,
        tenant_id: str,
        *,
        service_id: str,
        external_id: str,
        external_acc_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        external_id = self._clean(external_id)
        if not external_id:
            return None
        with tenant_session_scope(tenant_id) as session:
            stmt = (
                select(ChartOfAccountModel)
                .where(
                    self._service_id_match(service_id),
                    self._active_record_filter(),
                    func.coalesce(ChartOfAccountModel.active, True).is_(True),
                    ChartOfAccountModel.external_id == external_id,
                )
                .limit(1)
            )
            if self._clean(external_acc_id):
                stmt = stmt.where(ChartOfAccountModel.external_acc_id == self._clean(external_acc_id))
            row = session.execute(stmt).scalars().first()
            if not row:
                return None
            normalized = self._normalize_account_row(row, service_id_as_str=True)
            pay_type = self.get_bill_payment_pay_type(normalized)
            if pay_type is None:
                return None
            normalized["eligible_pay_type"] = pay_type
            return normalized

    def get_account_cdc_page(
        self,
        tenant_id: str,
        service_id: str,
        *,
        page: int = 1,
        page_size: int = 25,
        search: Optional[str] = None,
        sync_status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Paginated chart-of-account view for the UI."""
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 25), 1), 100)
        offset = (page - 1) * page_size
        service_id_str = self._service_id_text(service_id)
        search_term = self._clean(search)
        normalized_status = self._clean(sync_status).upper()

        synced_expr = and_(
            self._service_id_match(service_id_str),
            ChartOfAccountModel.external_id.is_not(None),
            ChartOfAccountModel.external_id != "",
        )

        filters = [self._active_record_filter()]
        if normalized_status and normalized_status not in {"", "ALL"}:
            if normalized_status == "SYNCED":
                filters.append(synced_expr)
            else:
                filters.append(~synced_expr)

        if search_term:
            like_term = f"%{search_term.lower()}%"
            filters.append(
                (
                    func.lower(func.coalesce(ChartOfAccountModel.name, "")).like(like_term)
                    | func.lower(func.coalesce(ChartOfAccountModel.account_no, "")).like(like_term)
                    | func.lower(func.coalesce(ChartOfAccountModel.account_type, "")).like(like_term)
                    | func.lower(func.coalesce(ChartOfAccountModel.detail_type, "")).like(like_term)
                )
            )

        try:
            with tenant_session_scope(tenant_id) as session:
                total_records = int(
                    session.execute(
                        select(func.count())
                        .select_from(ChartOfAccountModel)
                        .where(*filters)
                    ).scalar_one()
                    or 0
                )

                derived_sync_status = case(
                    (synced_expr, "SYNCED"),
                    else_="PENDING_PUSH",
                ).label("derived_sync_status")

                stmt = (
                    select(ChartOfAccountModel, derived_sync_status)
                    .where(*filters)
                    .order_by(ChartOfAccountModel.name.asc(), ChartOfAccountModel.id.asc())
                    .offset(offset)
                    .limit(page_size)
                )
                db_rows = session.execute(stmt).all()

                rows: List[Dict[str, Any]] = []
                for account_row, derived_status in db_rows:
                    rows.append(
                        {
                            "accountId": int(account_row.id),
                            "name": account_row.name,
                            "accountType": account_row.account_type,
                            "detailType": account_row.detail_type,
                            "accountNo": account_row.account_no,
                            "description": account_row.description,
                            "active": bool(account_row.active) if account_row.active is not None else True,
                            "externalId": account_row.external_id,
                            "syncToken": account_row.sync_token,
                            "serviceId": str(account_row.service_id) if account_row.service_id is not None else None,
                            "externalAccountId": account_row.external_acc_id,
                            "updatedAt": account_row.updated_at.isoformat() if account_row.updated_at is not None else None,
                            "syncStatus": derived_status,
                            "reason": account_row.reason,
                        }
                    )

                summary_row = session.execute(
                    select(
                        func.count().label("total"),
                        func.coalesce(func.sum(case((synced_expr, 1), else_=0)), 0).label("synced"),
                        func.coalesce(func.sum(case((~synced_expr, 1), else_=0)), 0).label("pending_push"),
                    )
                    .select_from(ChartOfAccountModel)
                    .where(self._active_record_filter())
                ).one()

                total = int(summary_row.total or 0)
                synced = int(summary_row.synced or 0)
                pending = int(summary_row.pending_push or 0)

                return {
                    "rows": rows,
                    "summary": {
                        "totalAccounts": total,
                        "syncedAccounts": synced,
                        "pendingPushAccounts": pending,
                    },
                    "pagination": {
                        "page": page,
                        "pageSize": page_size,
                        "totalRecords": total_records,
                        "totalPages": (total_records + page_size - 1) // page_size if total_records else 0,
                    },
                }
        except Exception:
            logger.exception("Failed to read chart of account CDC page | tenant_id=%s", tenant_id)
            raise

    def sync_projection_changed(self, left: Dict[str, Any], right: Dict[str, Any]) -> bool:
        def _norm_text(value: Any) -> str:
            return str(value or "").strip().lower()

        def _norm_bool(value: Any) -> bool:
            return False if value is False else True

        return {
            "name": _norm_text(left.get("name")),
            "account_no": _norm_text(left.get("account_no")),
            "description": _norm_text(left.get("description")),
            "currency_code": _norm_text(left.get("currency_code")),
            "active": _norm_bool(left.get("active")),
            "account_type": _norm_text(left.get("account_type")),
            "detail_type": _norm_text(left.get("detail_type")),
        } != {
            "name": _norm_text(right.get("name")),
            "account_no": _norm_text(right.get("account_no")),
            "description": _norm_text(right.get("description")),
            "currency_code": _norm_text(right.get("currency_code")),
            "active": _norm_bool(right.get("active")),
            "account_type": _norm_text(right.get("account_type")),
            "detail_type": _norm_text(right.get("detail_type")),
        }

    def get_account_latest_local_timestamp(self, account: Optional[Dict[str, Any]]) -> Optional[datetime]:
        if not account:
            return None
        value = account.get("updated_at") or account.get("created_at")
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        except Exception:
            return None
