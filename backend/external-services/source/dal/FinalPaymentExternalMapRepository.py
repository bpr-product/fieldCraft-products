from __future__ import annotations

from typing import Any, Dict, List, Optional

from source.dao.baseDao import with_retry
from source.dal.BillPaymentExpenseRepository import BillPaymentExpenseRepository
from source.dal.bill_payment_models import FinalPaymentExternalMapModel
from source.dal.db_session import tenant_session_scope


class FinalPaymentExternalMapRepository:
    """Compatibility wrapper for redesigned AP bill payment mapping."""

    def __init__(self) -> None:
        self._repo = BillPaymentExpenseRepository()

    @with_retry
    def get_by_mapping_id(self, tenant_id: str, mapping_id: int) -> Optional[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            row = session.get(FinalPaymentExternalMapModel, int(mapping_id))
            return self._repo._mapping_to_dict(row) if row else None

    @with_retry
    def get_by_payment_expense_id(self, tenant_id: str, service_id: Any, payment_expense_id: int) -> Optional[Dict[str, Any]]:
        return self._repo.get_current_mapping_by_payment_expense_id(tenant_id=tenant_id, service_id=service_id, payment_expense_id=payment_expense_id)

    @with_retry
    def get_by_external_id(self, tenant_id: str, service_id: Any, external_payment_id: str) -> Optional[Dict[str, Any]]:
        return self._repo.get_current_mapping_by_external_payment_id(tenant_id=tenant_id, service_id=service_id, external_payment_id=external_payment_id)

    @with_retry
    def insert_mapping(self, *, tenant_id: str, service_id: Any, payment_expense_id: Optional[int], external_payment_id: Optional[str], external_invoice_id: Optional[str] = None, external_account_id: Optional[str] = None, additional_info: Optional[str] = None, sync_status: str = "SYNCED", reason: Optional[str] = None) -> Dict[str, Any]:
        return self._repo.rotate_mapping_for_payment_expense(
            tenant_id=tenant_id,
            service_id=service_id,
            payment_expense_id=payment_expense_id,
            external_payment_id=external_payment_id,
            external_invoice_id=external_invoice_id,
            external_account_id=external_account_id,
            additional_info=additional_info,
            sync_status=sync_status,
            reason=reason,
        )

    @with_retry
    def mark_mapping_synced(
        self,
        *,
        tenant_id: str,
        mapping_id: int,
        external_payment_id: Optional[str],
        external_invoice_id: Optional[str] = None,
        external_account_id: Optional[str] = None,
        additional_info: Optional[str] = None,
        reason: Optional[str] = None,
        sync_status: Optional[str] = None,
        payment_expense_id: Optional[int] = None,
    ) -> None:
        self._repo.mark_mapping_synced(
            tenant_id=tenant_id,
            mapping_id=mapping_id,
            external_payment_id=external_payment_id,
            external_invoice_id=external_invoice_id,
            external_account_id=external_account_id,
            additional_info=additional_info,
            reason=reason,
            sync_status=sync_status,
            payment_expense_id=payment_expense_id,
        )

    @with_retry
    def mark_mapping_failed(self, *, tenant_id: str, mapping_id: int, status: Optional[str] = None, reason: Optional[str] = None) -> None:
        self._repo.mark_mapping_failed(tenant_id=tenant_id, mapping_id=mapping_id, status=status, reason=reason)

    @with_retry
    def close_mapping_as_invalid(self, tenant_id: str, mapping_id: int) -> None:
        self._repo.close_mapping_as_invalid(tenant_id=tenant_id, mapping_id=mapping_id)

    @with_retry
    def create_pending_push_mapping(self, tenant_id: str, service_id: Any, payment_expense_id: int, external_payment_id: Optional[str] = None, external_invoice_id: Optional[str] = None, external_account_id: Optional[str] = None, additional_info: Optional[str] = None, reason: Optional[str] = None) -> Dict[str, Any]:
        return self._repo.create_pending_push_mapping(
            tenant_id=tenant_id,
            service_id=service_id,
            payment_expense_id=payment_expense_id,
            external_payment_id=external_payment_id,
            external_invoice_id=external_invoice_id,
            external_account_id=external_account_id,
            additional_info=additional_info,
            reason=reason,
        )

    @with_retry
    def upsert_import_failed_mapping(self, *, tenant_id: str, service_id: Any, external_payment_id: str, external_invoice_id: Optional[str] = None, external_account_id: Optional[str] = None, payment_expense_id: Optional[int] = None, receipt_id: Optional[int] = None, reason: Optional[str] = None) -> Dict[str, Any]:
        return self._repo.upsert_import_failed_mapping(
            tenant_id=tenant_id,
            service_id=service_id,
            external_payment_id=external_payment_id,
            external_invoice_id=external_invoice_id,
            external_account_id=external_account_id,
            payment_expense_id=payment_expense_id,
            receipt_id=receipt_id,
            reason=reason,
        )

    @with_retry
    def get_open_import_failed_external_payment_ids(self, tenant_id: str, service_id: Any) -> List[str]:
        return self._repo.get_open_import_failed_external_payment_ids(tenant_id=tenant_id, service_id=service_id)

    @with_retry
    def assign_qbo_payment_account(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        payment_expense_ids: List[int],
        external_account_id: str,
        qbo_account_name: Optional[str] = None,
        qbo_account_type: Optional[str] = None,
        qbo_account_detail_type: Optional[str] = None,
        assigned_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._repo.assign_qbo_payment_account(
            tenant_id=tenant_id,
            service_id=service_id,
            payment_expense_ids=payment_expense_ids,
            external_account_id=external_account_id,
            qbo_account_name=qbo_account_name,
            qbo_account_type=qbo_account_type,
            qbo_account_detail_type=qbo_account_detail_type,
            assigned_by=assigned_by,
        )
