from __future__ import annotations

from typing import Any, Dict, List, Optional

from source.dal.ReceiptRepository import ReceiptRepository
from source.dal.db_session import tenant_session_scope
from source.dal.receipt_models import ReceiptExternalMapModel


class ReceiptExternalMapRepository:
    """Compatibility wrapper over the receipt mapping implementation."""

    def __init__(self) -> None:
        self._repo = ReceiptRepository()

    def get_by_mapping_id(self, tenant_id: str, mapping_id: int) -> Optional[Dict[str, Any]]:
        with tenant_session_scope(tenant_id) as session:
            row = session.get(ReceiptExternalMapModel, int(mapping_id))
            return self._repo._mapping_to_dict(row) if row else None

    def get_by_receipt_id(self, tenant_id: str, service_id: Any, receipt_id: int) -> Optional[Dict[str, Any]]:
        return self._repo.get_current_mapping_by_receipt_id(
            tenant_id=tenant_id, service_id=service_id, receipt_id=receipt_id
        )

    def get_by_external_id(
        self, tenant_id: str, service_id: Any, external_id: str
    ) -> Optional[Dict[str, Any]]:
        return self._repo.get_current_mapping_by_external_receipt_id(
            tenant_id=tenant_id,
            service_id=service_id,
            external_receipt_id=external_id,
        )

    def insert_mapping(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        receipt_id: Optional[int],
        external_id: Optional[str],
        external_account_id: Optional[str] = None,
        sync_token: Optional[str] = None,
        sync_status: str = "SYNCED",
        sync_type: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._repo.rotate_mapping_for_receipt(
            tenant_id=tenant_id,
            service_id=service_id,
            receipt_id=receipt_id,
            external_receipt_id=external_id,
            external_account_id=external_account_id,
            sync_status=sync_status,
            sync_token=sync_token,
            reason=reason or sync_type,
        )

    def mark_mapping_synced(
        self,
        *,
        tenant_id: str,
        mapping_id: int,
        external_receipt_id: Optional[str],
        external_account_id: Optional[str] = None,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
        sync_status: Optional[str] = None,
    ) -> None:
        self._repo.mark_mapping_synced(
            tenant_id=tenant_id,
            mapping_id=mapping_id,
            external_receipt_id=external_receipt_id,
            external_account_id=external_account_id,
            sync_token=sync_token,
            reason=reason,
            sync_status=sync_status,
        )

    def mark_mapping_failed(
        self, *, tenant_id: str, mapping_id: int, status: Optional[str] = None, reason: Optional[str] = None
    ) -> None:
        self._repo.mark_mapping_failed(
            tenant_id=tenant_id, mapping_id=mapping_id, status=status, reason=reason
        )

    def close_mapping_as_invalid(self, tenant_id: str, mapping_id: int) -> None:
        self._repo.close_mapping_as_invalid(tenant_id=tenant_id, mapping_id=mapping_id)

    def rotate_mapping_for_receipt(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        receipt_id: Optional[int],
        external_receipt_id: Optional[str],
        external_account_id: Optional[str],
        sync_status: str,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._repo.rotate_mapping_for_receipt(
            tenant_id=tenant_id,
            service_id=service_id,
            receipt_id=receipt_id,
            external_receipt_id=external_receipt_id,
            external_account_id=external_account_id,
            sync_status=sync_status,
            sync_token=sync_token,
            reason=reason,
        )

    def create_pending_push_mapping(
        self,
        tenant_id: str,
        service_id: Any,
        receipt_id: int,
        external_receipt_id: Optional[str] = None,
        external_account_id: Optional[str] = None,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._repo.create_pending_push_mapping(
            tenant_id=tenant_id,
            service_id=service_id,
            receipt_id=receipt_id,
            external_receipt_id=external_receipt_id,
            external_account_id=external_account_id,
            sync_token=sync_token,
            reason=reason,
        )

    def upsert_import_failed_mapping(
        self,
        *,
        tenant_id: str,
        service_id: Any,
        external_receipt_id: str,
        external_account_id: Optional[str] = None,
        receipt_id: Optional[int] = None,
        sync_token: Optional[str] = None,
        reason: Optional[str] = None,
        sync_status: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._repo.upsert_import_failed_mapping(
            tenant_id=tenant_id,
            service_id=service_id,
            external_receipt_id=external_receipt_id,
            external_account_id=external_account_id,
            receipt_id=receipt_id,
            sync_token=sync_token,
            reason=reason,
            sync_status=sync_status,
        )

    def get_open_import_failed_external_receipt_ids(
        self, tenant_id: str, service_id: Any
    ) -> List[str]:
        return self._repo.get_open_import_failed_external_receipt_ids(
            tenant_id=tenant_id, service_id=service_id
        )
