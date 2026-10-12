from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils import constants as const
from source.Utils.QuickBooksErrorHandler import QuickBooksAPIError
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.dal.ReceiptExternalMapRepository import ReceiptExternalMapRepository
from source.dal.ReceiptRepository import ReceiptRepository
from source.dal.VendorExternalMapRepository import VendorExternalMapRepository
from source.quickbooks.QuickBooksBillExportService import QuickBooksBillExportService
from source.service.QuickBooksService import QuickBooksService
from source.Utils.auditlog import safe_audit_log


logger = logging.getLogger(__name__)


class QuickBooksBillCreateService:
    """
    Real-time bill export for application-created bills.

    Flow:
      1. Application sends receipt_external_map_id
      2. We load receipt_external_map by PK
      3. From that mapping, we get receipt_id
      4. We load the receipt row
      5. From receipt.vendor_id and receipt.chart_of_account_id,
         the exporter resolves the QuickBooks Vendor and Account mappings
      6. Export exactly one bill using the same bill export behaviour
         as the manual/batch export flow
    """

    def __init__(self) -> None:
        self.qbo_repo = QuickBooksRepository()
        self.qbo_service = QuickBooksService()
        self.receipt_repo = ReceiptRepository()
        self.receipt_map_repo = ReceiptExternalMapRepository()
        self.vendor_map_repo = VendorExternalMapRepository()
        self.bill_exporter = QuickBooksBillExportService()

    def create_bill(
        self,
        *,
        tenant_id: str,
        service_id: Optional[str],
        receipt_external_map_id: int,
        vendor_external_map_id: Optional[int] = None,
        object_id: Optional[str] = None,
        email: Optional[str] = None,
        user_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        tenant_id = (tenant_id or "").strip()
        service_id = str(service_id or "").strip()
        safe_audit_log(
            tenant_id=tenant_id,
            action="bill_create_started",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="started",
            resource_type="quickbooks_bill",
        )
        # Track final outcome so the finally block logs it regardless of which exit path is taken.
        _audit_action = "bill_create_failed"
        _audit_status = "failed"
        _audit_context: Optional[str] = None

        logger.debug(f"[BillCreate] create_bill called | tenant_id={tenant_id} service_id={service_id} receipt_external_map_id={receipt_external_map_id}")
        try:
            if not tenant_id:
                _audit_action, _audit_context = "bill_create_aborted", "missing_tenant_id"
                logger.debug(f"[BillCreate] ABORTED — missing tenant_id")
                return {"ok": False, "message": "tenant_id is required"}

            if receipt_external_map_id is None:
                _audit_action, _audit_context = "bill_create_aborted", "missing_receipt_external_map_id"
                logger.debug(f"[BillCreate] ABORTED — missing receipt_external_map_id")
                return {"ok": False, "message": "receipt_external_map_id is required"}

            if not service_id:
                service_id = str(
                    self.qbo_repo.getServiceIdByName(const.SERVICE_NAME_QUICKBOOKS) or ""
                ).strip()
                logger.debug(f"[BillCreate] service_id resolved from name → {service_id!r}")

            if not service_id:
                _audit_action, _audit_context = "bill_create_aborted", "missing_service_id"
                logger.debug(f"[BillCreate] ABORTED — could not resolve service_id")
                return {
                    "ok": False,
                    "message": "QuickBooks service_id could not be resolved",
                }

            receipt_mapping = self.receipt_map_repo.get_by_mapping_id(
                tenant_id=tenant_id,
                mapping_id=int(receipt_external_map_id),
            )
            logger.debug(f"[BillCreate] receipt_mapping lookup id={receipt_external_map_id} → found={receipt_mapping is not None} status={( receipt_mapping or {}).get('sync_status')!r} receipt_id={(receipt_mapping or {}).get('receipt_id')}")
            if not receipt_mapping:
                _audit_action, _audit_context = "bill_create_aborted", f"mapping_not_found id={receipt_external_map_id}"
                return {
                    "ok": False,
                    "message": f"Receipt external map not found for id={receipt_external_map_id}",
                }

            if str(receipt_mapping.get("service_id") or "") != service_id:
                _audit_action, _audit_context = "bill_create_aborted", "mapping_service_id_mismatch"
                logger.debug(f"[BillCreate] ABORTED — service_id mismatch: mapping.service_id={receipt_mapping.get('service_id')!r} != requested={service_id!r}")
                return {
                    "ok": False,
                    "message": "Receipt external map does not belong to the requested QuickBooks service",
                }

            current_status = self._clean(receipt_mapping.get("sync_status")).upper()

            allowed_statuses = {
                ReceiptRepository.STATUS_PENDING_PUSH,
                ReceiptRepository.STATUS_FAILED,
                ReceiptRepository.STATUS_SYNC_FAILED,
            }

            if current_status not in allowed_statuses:
                _audit_action, _audit_context = "bill_create_aborted", f"ineligible_status={current_status or 'EMPTY'}"
                logger.debug(f"[BillCreate] ABORTED — ineligible status={current_status!r} allowed={sorted(allowed_statuses)}")
                return {
                    "ok": False,
                    "message": (
                        "Bill is not eligible for real-time export. "
                        f"Expected sync_status in {sorted(allowed_statuses)}, got '{current_status or 'EMPTY'}'"
                    ),
                    "receiptExternalMapId": int(receipt_external_map_id),
                    "syncStatus": current_status or None,
                }

            receipt_id = receipt_mapping.get("receipt_id")
            if not receipt_id:
                _audit_action, _audit_context = "bill_create_aborted", "missing_receipt_id"
                logger.debug(f"[BillCreate] ABORTED — mapping has no receipt_id")
                return {
                    "ok": False,
                    "message": "Receipt external map is missing receipt_id",
                    "receiptExternalMapId": int(receipt_external_map_id),
                }

            receipt = self.receipt_repo.get_current_receipt_by_id(
                tenant_id,
                int(receipt_id),
            )
            logger.debug(f"[BillCreate] receipt lookup receipt_id={receipt_id} → found={receipt is not None} vendor_id={(receipt or {}).get('vendor_id')} coa_id={(receipt or {}).get('chart_of_account_id')}")
            if not receipt:
                _audit_action, _audit_context = "bill_create_aborted", f"receipt_not_found id={receipt_id}"
                return {
                    "ok": False,
                    "message": f"Receipt not found for receipt_id={receipt_id}",
                    "receiptExternalMapId": int(receipt_external_map_id),
                }

            is_stock_debit = self._is_stock_debit_receipt(receipt)
            is_stock_credit = self._is_stock_credit_receipt(receipt)
            is_stock_journal = is_stock_debit or is_stock_credit
            if vendor_external_map_id is not None and not is_stock_journal:
                supplied_vendor_mapping = self.vendor_map_repo.get_by_mapping_id(
                    tenant_id=tenant_id,
                    mapping_id=int(vendor_external_map_id),
                )
                if not supplied_vendor_mapping:
                    return {
                        "ok": False,
                        "message": "The selected vendor mapping no longer exists. Run vendor sync and retry the bill.",
                    }
                if str(supplied_vendor_mapping.get("service_id") or "") != service_id:
                    return {
                        "ok": False,
                        "message": "The selected vendor mapping belongs to a different QuickBooks connection.",
                    }
                receipt_vendor_id = str(receipt.get("vendor_id") or "").strip()
                mapped_vendor_id = str(supplied_vendor_mapping.get("vendor_id") or "").strip()
                if not receipt_vendor_id or receipt_vendor_id != mapped_vendor_id:
                    return {
                        "ok": False,
                        "message": "The selected vendor mapping does not belong to this bill's vendor. No data was exported.",
                    }

            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = (connection.get(const.KEY_ENV) or "sandbox").lower()
            conn_status = (
                connection.get(const.KEY_STATUS) or const.STATUS_CONNECTED
            ).lower()
            logger.debug(f"[BillCreate] QBO connection | realmId_present={bool(realm_id)} env={env!r} conn_status={conn_status!r} has_refresh_token={bool(refresh_token)}")

            if conn_status and conn_status != const.STATUS_CONNECTED:
                _audit_action, _audit_context = "bill_create_aborted", f"not_connected status={conn_status}"
                logger.debug(f"[BillCreate] ABORTED — QBO not connected status={conn_status!r}")
                return {
                    "ok": False,
                    "message": f"QuickBooks is not connected (status={conn_status})",
                }

            if not refresh_token or not realm_id:
                _audit_action, _audit_context = "bill_create_aborted", "missing_realmId_or_refreshToken"
                logger.debug(f"[BillCreate] ABORTED — missing realm_id or refresh_token")
                return {
                    "ok": False,
                    "message": "QuickBooks not connected for this tenant (missing realmId/refreshToken).",
                }

            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)

            if not client_id or not client_secret:
                _audit_action, _audit_context = "bill_create_aborted", "missing_credentials"
                logger.debug(f"[BillCreate] ABORTED — missing client_id or client_secret")
                return {
                    "ok": False,
                    "message": "QuickBooks client credentials missing (ClientId/ClientSecret).",
                }

            logger.debug(f"[BillCreate] refreshing access token...")
            access_token = self.bill_exporter.refresh_access_token(
                tenant_id=tenant_id,
                service_id=service_id,
                connection=connection,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
            )
            logger.debug(f"[BillCreate] access token refreshed OK")

            base_url = self.bill_exporter.get_qbo_api_base(env)
            qbo_service_master_id = self.qbo_repo.getServiceIdByName(
                const.SERVICE_NAME_QUICKBOOKS
            )
            logger.debug(f"[BillCreate] base_url={base_url} qbo_service_master_id={qbo_service_master_id}")
            if qbo_service_master_id is None:
                _audit_action, _audit_context = "bill_create_aborted", "missing_service_master"
                logger.debug(f"[BillCreate] ABORTED — qbo_service_master_id not found")
                return {
                    "ok": False,
                    "message": "QuickBooks service_master mapping not found.",
                }

            if is_stock_journal:
                result = self._export_single_stock_journal_entry(
                    stock_credit=is_stock_credit,
                    tenant_id=tenant_id,
                    service_id=service_id,
                    realm_id=str(realm_id),
                    env=env,
                    qbo_service_master_id=str(qbo_service_master_id),
                    access_token=access_token,
                    base_url=base_url,
                    receipt=receipt,
                    mapping=receipt_mapping,
                )
            else:
                result = self._export_single_bill(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    realm_id=str(realm_id),
                    env=env,
                    qbo_service_master_id=str(qbo_service_master_id),
                    access_token=access_token,
                    base_url=base_url,
                    receipt=receipt,
                    mapping=receipt_mapping,
                )

            _audit_action = "bill_create_completed" if result.get(const.KEY_OK) else "bill_create_failed"
            _audit_status = "success" if result.get(const.KEY_OK) else "failed"
            _audit_context = f"receipt_external_map_id={receipt_external_map_id}"
            logger.info(
                "Real-time QuickBooks transaction export completed | receipt_id=%s entity_type=%s ok=%s",
                receipt_id,
                const.QBO_RECEIPT_ENTITY_TYPE_JOURNAL_ENTRY if is_stock_journal else const.QBO_RECEIPT_ENTITY_TYPE_BILL,
                result.get(const.KEY_OK),
            )
            return result

        except Exception as e:
            _audit_action = "bill_create_failed"
            _audit_status = "failed"
            _audit_context = str(e)[:64]
            logger.exception("Real-time QuickBooks transaction export failed")
            return {
                "ok": False,
                "message": "Real-time bill export failed",
                "error": "Unexpected QuickBooks sync error.",
            }

        finally:
            # [AUDIT] Fires on every exit path — early return, success, or exception.
            safe_audit_log(
                tenant_id=tenant_id,
                action=_audit_action,
                user_id=object_id or None,
                email=email or None,
                user_role=user_role or None,
                status=_audit_status,
                context=_audit_context,
                resource_type="quickbooks_bill",
            )

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def _is_stock_bill_receipt(cls, receipt: Dict[str, Any], expected_name: str) -> bool:
        normalized_name = "".join(
            character
            for character in cls._clean((receipt or {}).get("name")).lower()
            if character.isalnum()
        )
        normalized_expected = "".join(
            character
            for character in cls._clean(expected_name).lower()
            if character.isalnum()
        )
        return normalized_name == normalized_expected

    @classmethod
    def _is_stock_debit_receipt(cls, receipt: Dict[str, Any]) -> bool:
        return cls._is_stock_bill_receipt(
            receipt,
            getattr(const, "QBO_STOCK_DEBIT_BILL_NAME", "StockDebitBill"),
        )

    @classmethod
    def _is_stock_credit_receipt(cls, receipt: Dict[str, Any]) -> bool:
        return cls._is_stock_bill_receipt(
            receipt,
            getattr(const, "QBO_STOCK_CREDIT_BILL_NAME", "StockCreditBill"),
        )

    def _export_single_stock_journal_entry(
        self,
        *,
        stock_credit: bool,
        tenant_id: str,
        service_id: str,
        realm_id: str,
        env: str,
        qbo_service_master_id: str,
        access_token: str,
        base_url: str,
        receipt: Dict[str, Any],
        mapping: Dict[str, Any],
    ) -> Dict[str, Any]:
        receipt_id = int(receipt["id"])
        mapping_id = int(mapping["id"])
        external_id = self.bill_exporter._clean(mapping.get("external_receipt_id")) or None
        stock_bill_name = "Stock Credit" if stock_credit else "Stock Debit"
        detail = {
            const.KEY_FIELDCRAFT_BILL_ID: receipt_id,
            "receiptExternalMapId": mapping_id,
            "displayName": receipt.get("doc_number") or f"{stock_bill_name} {receipt_id}",
            const.KEY_STATUS: None,
            const.KEY_ACTION: None,
            const.KEY_QBO_ENTITY_TYPE: "JournalEntry",
            const.KEY_QBO_BILL_ID: None,
            const.KEY_QBO_JOURNAL_ENTRY_ID: external_id,
            const.KEY_SYNC_TOKEN: mapping.get("sync_token"),
            const.KEY_REASON: None,
            const.KEY_ERROR: None,
        }
        summary: Dict[str, Any] = {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": service_id,
            "realmIdPresent": bool(realm_id),
            "env": env,
            "requested": 1,
            "skippedAlreadyMapped": 0,
            "skippedQboNewer": 0,
            "mapped from QBO": 0,
            "mappedJournalEntriesFromQBO": 0,
            "createdInQbo": 0,
            "createdJournalEntriesInQbo": 0,
            "updatedInQbo": 0,
            "updatedJournalEntriesInQbo": 0,
            "failed": 0,
            const.KEY_FAILURES: [],
            const.KEY_RESULTS: [],
            "receiptExternalMapId": mapping_id,
            "receiptId": receipt_id,
            "processedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        try:
            if external_id:
                self.bill_exporter._assert_mapping_realm(mapping, realm_id, "bill")

            self.bill_exporter._export_stock_debit_bill_as_journal_entry(
                stock_credit=stock_credit,
                tenant_id=tenant_id,
                service_id=service_id,
                realm_id=realm_id,
                qbo_service_master_id=qbo_service_master_id,
                access_token=access_token,
                base_url=base_url,
                receipt=receipt,
                mapping_id=mapping_id,
                external_receipt_id=external_id,
                mapping=mapping,
                summary=summary,
                detail=detail,
            )
            result = (summary.get(const.KEY_RESULTS) or [{}])[-1]
            summary[const.KEY_QBO_ENTITY_TYPE] = "JournalEntry"
            summary[const.KEY_QBO_JOURNAL_ENTRY_ID] = result.get(const.KEY_QBO_JOURNAL_ENTRY_ID)
            return summary
        except Exception as exc:
            intuit_tid = exc.intuit_tid if isinstance(exc, QuickBooksAPIError) else None
            message = exc.user_message if isinstance(exc, QuickBooksAPIError) else str(exc)
            logger.exception(
                "Real-time %s journal entry export failed | receipt_id=%s",
                "StockCreditBill" if stock_credit else "StockDebitBill",
                receipt_id,
            )
            self.receipt_map_repo.mark_mapping_failed(
                tenant_id=tenant_id,
                mapping_id=mapping_id,
                reason=self.bill_exporter._build_reason(message, intuit_tid=intuit_tid),
            )
            summary[const.KEY_OK] = False
            summary["failed"] = 1
            detail.update({
                const.KEY_STATUS: "failed",
                const.KEY_ACTION: "journal entry export failed",
                const.KEY_ERROR: message,
                const.KEY_REASON: self.bill_exporter._build_reason(message, intuit_tid=intuit_tid),
            })
            summary[const.KEY_RESULTS].append(detail)
            qbo_api.append_failure(
                summary,
                {
                    const.KEY_FIELDCRAFT_BILL_ID: receipt_id,
                    const.KEY_QBO_ENTITY_TYPE: "JournalEntry",
                    const.KEY_QBO_JOURNAL_ENTRY_ID: external_id,
                    const.KEY_ERROR: message,
                },
            )
            return summary

    def _export_single_bill(
        self,
        *,
        tenant_id: str,
        service_id: str,
        realm_id: str,
        env: str,
        qbo_service_master_id: str,
        access_token: str,
        base_url: str,
        receipt: Dict[str, Any],
        mapping: Dict[str, Any],
    ) -> Dict[str, Any]:
        receipt_id = int(receipt["id"])
        mapping_id = int(mapping["id"])

        detail = {
            const.KEY_FIELDCRAFT_BILL_ID: receipt_id,
            "receiptExternalMapId": mapping_id,
            "displayName": mapping.get("external_receipt_id")
            or receipt.get("doc_number")
            or f"Bill {receipt_id}",
            const.KEY_STATUS: None,
            const.KEY_ACTION: None,
            const.KEY_QBO_ENTITY_TYPE: const.QBO_RECEIPT_ENTITY_TYPE_BILL,
            const.KEY_QBO_BILL_ID: mapping.get("external_receipt_id") or None,
            const.KEY_SYNC_TOKEN: mapping.get("sync_token"),
            const.KEY_REASON: None,
            const.KEY_ERROR: None,
        }

        summary: Dict[str, Any] = {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": service_id,
            "realmIdPresent": bool(realm_id),
            "env": env,
            "requested": 1,
            "skippedAlreadyMapped": 0,
            "skippedQboNewer": 0,
            "mapped from QBO": 0,
            "createdInQbo": 0,
            "updatedInQbo": 0,
            "failed": 0,
            const.KEY_FAILURES: [],
            const.KEY_RESULTS: [],
            "receiptExternalMapId": mapping_id,
            "receiptId": receipt_id,
            "processedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

        logger.debug(f"[BillCreate] _export_single_bill | receipt_id={receipt_id} mapping_id={mapping_id} external_receipt_id={mapping.get('external_receipt_id')!r} sync_status={mapping.get('sync_status')!r}")
        try:
            external_receipt_id = self.bill_exporter._clean(
                mapping.get("external_receipt_id")
            )
            current_status = self.bill_exporter._clean(
                mapping.get("sync_status")
            ).upper()

            allowed_statuses = {
                ReceiptRepository.STATUS_PENDING_PUSH,
                ReceiptRepository.STATUS_FAILED,
                ReceiptRepository.STATUS_SYNC_FAILED,
            }

            if current_status not in allowed_statuses:
                logger.debug(f"[BillCreate] ✗ ineligible status={current_status!r} receipt_id={receipt_id}")
                summary[const.KEY_OK] = False
                summary["failed"] += 1
                detail.update(
                    {
                        const.KEY_STATUS: "failed",
                        const.KEY_ACTION: "not eligible",
                        const.KEY_ERROR: (
                            "Bill is not eligible for export because "
                            f"sync_status is '{current_status or 'EMPTY'}'"
                        ),
                        const.KEY_REASON: self.bill_exporter._build_reason(
                            f"Bill is not eligible for export because sync_status is '{current_status or 'EMPTY'}'"
                        ),
                    }
                )
                summary[const.KEY_RESULTS].append(detail)
                return summary

            if external_receipt_id:
                self.bill_exporter._assert_mapping_realm(mapping, realm_id, "bill")

            payload = self.bill_exporter._build_qbo_bill_payload(
                tenant_id=tenant_id,
                service_id=service_id,
                realm_id=realm_id,
                qbo_service_master_id=qbo_service_master_id,
                receipt=receipt,
            )
            if not payload:
                raise ValueError("Bill payload could not be built")

            if external_receipt_id:
                logger.debug(f"[BillCreate] bill already has QBO id={external_receipt_id} — checking if update needed...")
                try:
                    stored_sync_token = (
                        self.bill_exporter._clean(mapping.get("sync_token")) or None
                    )

                    qbo_current = self.bill_exporter.get_bill_in_qbo(
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=realm_id,
                        bill_id=external_receipt_id,
                    )

                    qbo_current_sync_token = (
                        self.bill_exporter._clean(
                            qbo_current.get(const.QBO_KEY_SYNC_TOKEN)
                        )
                        or None
                    )
                    qbo_last_updated_at = self.bill_exporter._get_qbo_last_updated_at(
                        qbo_current
                    )
                    local_updated_at = self.bill_exporter._get_local_receipt_updated_at(
                        receipt, None
                    )
                    logger.debug(f"[BillCreate] sync token check | stored={stored_sync_token!r} qbo_current={qbo_current_sync_token!r} | timestamps local={local_updated_at} qbo={qbo_last_updated_at}")

                    if stored_sync_token and qbo_current_sync_token:
                        qbo_changed_independently = (
                            stored_sync_token != qbo_current_sync_token
                        )
                    else:
                        qbo_changed_independently = bool(
                            qbo_last_updated_at
                            and local_updated_at
                            and qbo_last_updated_at > local_updated_at
                        )

                    if (
                        qbo_changed_independently
                        and qbo_last_updated_at
                        and local_updated_at
                        and qbo_last_updated_at > local_updated_at
                    ):
                        logger.debug(f"[BillCreate] → SKIP qbo_newer receipt_id={receipt_id} qbo_bill_id={external_receipt_id} local={local_updated_at} qbo={qbo_last_updated_at}")
                        self.receipt_map_repo.mark_mapping_synced(
                            tenant_id=tenant_id,
                            mapping_id=mapping_id,
                            external_receipt_id=external_receipt_id,
                            external_account_id=realm_id,
                            sync_token=qbo_current_sync_token,
                            reason=self.bill_exporter._build_reason(
                                "QuickBooks has a newer version of this bill. The next import will apply those changes here.",
                                managed_line=self.bill_exporter._get_managed_line_metadata(mapping),
                            ),
                        )
                        summary["skippedQboNewer"] += 1
                        detail.update(
                            {
                                const.KEY_STATUS: "skipped",
                                const.KEY_ACTION: "skippedQboNewer",
                                const.KEY_SYNC_TOKEN: qbo_current_sync_token,
                                const.KEY_REASON: self.bill_exporter._build_reason(
                                    "QuickBooks has a newer version of this bill. Changes will be applied on the next import."
                                ),
                            }
                        )
                        summary[const.KEY_RESULTS].append(detail)
                        return summary

                    logger.debug(f"[BillCreate] → UPDATE in QBO receipt_id={receipt_id} qbo_bill_id={external_receipt_id} sync_token={qbo_current_sync_token or stored_sync_token!r}")
                    updated = self.bill_exporter.update_bill_in_qbo(
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=realm_id,
                        bill_id=external_receipt_id,
                        payload=payload,
                        sync_token=qbo_current_sync_token or stored_sync_token,
                    )

                    updated_id = (
                        self.bill_exporter._clean(
                            (updated or {}).get(const.QBO_KEY_ID)
                        )
                        or external_receipt_id
                    )
                    updated_token = (
                        self.bill_exporter._clean(
                            (updated or {}).get(const.QBO_KEY_SYNC_TOKEN)
                        )
                        or None
                    )
                    logger.debug(f"[BillCreate] ✓ updated in QBO receipt_id={receipt_id} → qbo_bill_id={updated_id} new_sync_token={updated_token!r}")

                    self.bill_exporter._try_attach_document(
                        tenant_id=tenant_id,
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=realm_id,
                        bill_id=updated_id,
                        document_id=receipt.get("document_id"),
                    )

                    self.receipt_map_repo.mark_mapping_synced(
                        tenant_id=tenant_id,
                        mapping_id=mapping_id,
                        external_receipt_id=updated_id,
                        external_account_id=realm_id,
                        sync_token=updated_token,
                        reason=self.bill_exporter._build_reason(
                            "Bill updated in QuickBooks successfully.",
                            managed_line=self.bill_exporter._build_managed_line_metadata(
                                qbo_bill=updated or {},
                                payload=payload,
                                receipt=receipt,
                                allow_single_line_fallback=True,
                            ),
                        ),
                    )

                    summary["updatedInQbo"] += 1
                    detail.update(
                        {
                            const.KEY_STATUS: "success",
                            const.KEY_ACTION: "updated in QBO",
                            const.KEY_QBO_BILL_ID: updated_id,
                            const.KEY_SYNC_TOKEN: updated_token,
                            const.KEY_REASON: self.bill_exporter._build_reason(
                                "Bill updated in QuickBooks successfully."
                            ),
                        }
                    )
                    summary[const.KEY_RESULTS].append(detail)
                    return summary

                except Exception as exc:
                    if not self.bill_exporter._is_qbo_stale_reference_error(str(exc)):
                        raise
                    logger.debug(f"[BillCreate] stale reference on update receipt_id={receipt_id} qbo_bill_id={external_receipt_id} — falling through to find/create")

            logger.debug(f"[BillCreate] searching for existing bill in QBO by DocNumber for receipt_id={receipt_id}...")
            candidates_in_qbo = self.bill_exporter.find_bill_candidates_in_qbo(
                access_token=access_token,
                base_url=base_url,
                realm_id=realm_id,
                receipt=receipt,
            )
            logger.debug(f"[BillCreate] candidates_in_qbo={len(candidates_in_qbo)} for receipt_id={receipt_id}")

            if candidates_in_qbo:
                if len(candidates_in_qbo) > 1:
                    raise Exception(
                        "Multiple QuickBooks Bills match this document number. "
                        "Please make the bill number unique before syncing."
                    )

                matched = candidates_in_qbo[0]
                found_id = self.bill_exporter._clean(matched.get(const.QBO_KEY_ID))
                found_token = (
                    self.bill_exporter._clean(matched.get(const.QBO_KEY_SYNC_TOKEN))
                    or None
                )
                logger.debug(f"[BillCreate] → MATCHED in QBO receipt_id={receipt_id} → qbo_bill_id={found_id} sync_token={found_token!r}")

                self.bill_exporter._try_attach_document(
                    tenant_id=tenant_id,
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=realm_id,
                    bill_id=found_id,
                    document_id=receipt.get("document_id"),
                )

                self.receipt_map_repo.mark_mapping_synced(
                    tenant_id=tenant_id,
                    mapping_id=mapping_id,
                    external_receipt_id=found_id,
                    external_account_id=realm_id,
                    sync_token=found_token,
                    reason=self.bill_exporter._build_reason(
                        "Bill matched to an existing QuickBooks bill and linked successfully.",
                        managed_line=self.bill_exporter._build_managed_line_metadata(
                            qbo_bill=matched or {},
                            payload=payload,
                            receipt=receipt,
                        ),
                    ),
                )

                summary["mapped from QBO"] += 1
                detail.update(
                    {
                        const.KEY_STATUS: "success",
                        const.KEY_ACTION: "mapped from QBO",
                        const.KEY_QBO_BILL_ID: found_id,
                        const.KEY_SYNC_TOKEN: found_token,
                        const.KEY_REASON: self.bill_exporter._build_reason(
                            "Bill matched to an existing QuickBooks bill and linked successfully."
                        ),
                    }
                )
                summary[const.KEY_RESULTS].append(detail)
                return summary

            logger.debug(f"[BillCreate] → CREATE new bill in QBO for receipt_id={receipt_id}...")
            created = self.bill_exporter.create_bill_in_qbo(
                access_token=access_token,
                base_url=base_url,
                realm_id=realm_id,
                payload=payload,
            )

            created_id = self.bill_exporter._clean(
                (created or {}).get(const.QBO_KEY_ID)
            )
            if not created_id:
                raise Exception(f"Create Bill returned unexpected response: {created}")

            created_token = (
                self.bill_exporter._clean(
                    (created or {}).get(const.QBO_KEY_SYNC_TOKEN)
                )
                or None
            )
            logger.debug(f"[BillCreate] ✓ created in QBO receipt_id={receipt_id} → qbo_bill_id={created_id} sync_token={created_token!r}")

            self.bill_exporter._try_attach_document(
                tenant_id=tenant_id,
                access_token=access_token,
                base_url=base_url,
                realm_id=realm_id,
                bill_id=created_id,
                document_id=receipt.get("document_id"),
            )

            self.receipt_map_repo.mark_mapping_synced(
                tenant_id=tenant_id,
                mapping_id=mapping_id,
                external_receipt_id=created_id,
                external_account_id=realm_id,
                sync_token=created_token,
                reason=self.bill_exporter._build_reason(
                    "Bill created in QuickBooks successfully.",
                    managed_line=self.bill_exporter._build_managed_line_metadata(
                        qbo_bill=created or {},
                        payload=payload,
                        receipt=receipt,
                        allow_single_line_fallback=True,
                    ),
                ),
            )

            summary["createdInQbo"] += 1
            detail.update(
                {
                    const.KEY_STATUS: "success",
                    const.KEY_ACTION: "created in QBO",
                    const.KEY_QBO_BILL_ID: created_id,
                    const.KEY_SYNC_TOKEN: created_token,
                    const.KEY_REASON: self.bill_exporter._build_reason(
                        "Bill created in QuickBooks successfully."
                    ),
                    "bill": created,
                }
            )
            summary[const.KEY_RESULTS].append(detail)
            summary["qboBillId"] = created_id
            summary["bill"] = created
            summary[const.KEY_INTUIT_TID] = None
            return summary

        except Exception as exc:
            intuit_tid = exc.intuit_tid if isinstance(exc, QuickBooksAPIError) else None
            exc_msg = exc.user_message if isinstance(exc, QuickBooksAPIError) else str(exc)
            logger.debug(f"[BillCreate] ✗ FAILED receipt_id={receipt_id} error={exc_msg!r}")

            if self.bill_exporter._is_qbo_deleted_ref_error(exc_msg):
                vendor_id_for_exc = receipt.get("vendor_id")
                logger.debug(f"[BillCreate] deleted-ref error detected — marking vendor_id={vendor_id_for_exc} mapping as FAILED")
                if vendor_id_for_exc is not None:
                    try:
                        vendor_map = self.vendor_map_repo.get_by_vendor_id(
                            tenant_id,
                            service_id,
                            int(vendor_id_for_exc),
                        )
                        if vendor_map and vendor_map.get("id"):
                            self.vendor_map_repo.mark_mapping_failed(
                                tenant_id=tenant_id,
                                mapping_id=int(vendor_map["id"]),
                                reason=self.bill_exporter._build_reason(
                                    "This bill's vendor was deleted in QuickBooks. Re-sync vendors to restore the mapping."
                                ),
                            )
                            logger.debug(f"[BillCreate] vendor mapping id={vendor_map['id']} marked FAILED")
                    except Exception as vmap_exc:
                        logger.debug(f"[BillCreate] could not mark vendor mapping failed: {vmap_exc}")

            self.receipt_map_repo.mark_mapping_failed(
                tenant_id=tenant_id,
                mapping_id=mapping_id,
                reason=self.bill_exporter._build_reason(
                    exc_msg,
                    intuit_tid=intuit_tid,
                ),
            )
            logger.debug(f"[BillCreate] receipt mapping id={mapping_id} marked FAILED")

            summary[const.KEY_OK] = False
            summary["failed"] += 1

            qbo_api.append_failure(
                summary,
                {
                    const.KEY_FIELDCRAFT_BILL_ID: receipt_id,
                    "externalReceiptId": mapping.get("external_receipt_id") or None,
                    const.KEY_ERROR: exc_msg,
                                    },
            )

            detail.update(
                {
                    const.KEY_STATUS: "failed",
                    const.KEY_ACTION: "error",
                    const.KEY_ERROR: exc_msg,
                    const.KEY_REASON: self.bill_exporter._build_reason(
                        exc_msg,
                        intuit_tid=intuit_tid,
                    ),
                }
            )
            summary[const.KEY_RESULTS].append(detail)
            return summary
