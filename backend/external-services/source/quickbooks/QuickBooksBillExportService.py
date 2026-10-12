
from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

from source.dal.ChartOfAccountRepository import ChartOfAccountRepository
from source.dal.CustomerRepository import CustomerRepository
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.dal.ProjectRepository import ProjectRepository
from source.dal.ReceiptExternalMapRepository import ReceiptExternalMapRepository
from source.dal.ReceiptRepository import ReceiptRepository
from source.dal.VendorExternalMapRepository import VendorExternalMapRepository
from source.dal.VendorRepository import VendorRepository
from source.quickbooks.QuickBooksAuthentication import QuickBooksOAuthClient
from source.quickbooks.QuickBooksCapabilityService import QuickBooksCapabilityService
from source.quickbooks.QBOBillAttachmentService import QBOBillAttachmentService
from source.service.QuickBooksService import QuickBooksService
from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils import QuickBooksErrorHandler as qbo_err
from source.Utils.QuickBooksErrorHandler import QuickBooksAPIError
from source.Utils import constants as const
from source.quickbooks.qbo_query_utils import escape_qbo_literal
from source.quickbooks.qbo_bill_utils import (
    build_receipt_item_description,
    build_receipt_items_summary,
    clean,
    description_fingerprint,
    extract_money_amount,
    is_fieldcraft_receipt_item,
    managed_bill_line_baseline,
    parse_item_list,
    parse_jsonish,
)
from source.Utils.auditlog import safe_audit_log


logger = logging.getLogger(__name__)


class QuickBooksBillExportService:
    """Delta-oriented bill export using the redesigned receipt mapping domain."""

    _MANAGED_LINE_REASON_KEY = "fieldcraftManagedBillLine"

    @staticmethod
    def _build_reason(
        message: str,
        intuit_tid: Optional[str] = None,
        managed_line: Optional[Dict[str, Any]] = None,
    ) -> str:
        data: Dict[str, Any] = {"message": message}
        if intuit_tid:
            data["intuit_tid"] = intuit_tid
        if managed_line:
            data[QuickBooksBillExportService._MANAGED_LINE_REASON_KEY] = managed_line
        return json.dumps(data)

    @staticmethod
    def _normalize_receipt_name(value: Any) -> str:
        return ''.join(ch for ch in str(value or "").strip().lower() if ch.isalnum())

    def _assert_mapping_realm(self, mapping: Optional[Dict[str, Any]], realm_id: str, label: str) -> None:
        if not isinstance(mapping, dict):
            return
        mapped_realm = self._clean(mapping.get("external_account_id"))
        if mapped_realm and mapped_realm != self._clean(realm_id):
            raise ValueError(
                f"The mapped {label} belongs to a different QuickBooks company. Re-sync it and retry."
            )

    def __init__(self) -> None:
        self.qbo_repo = QuickBooksRepository()
        self.qbo_service = QuickBooksService()
        self.receipt_repo = ReceiptRepository()
        self.receipt_map_repo = ReceiptExternalMapRepository()
        self.vendor_map_repo = VendorExternalMapRepository()
        self.vendor_repo = VendorRepository()
        self.coa_repo = ChartOfAccountRepository()
        self.customer_repo = CustomerRepository()
        self.project_repo = ProjectRepository()
        self.capability_service = QuickBooksCapabilityService()
        self.attachment_service = QBOBillAttachmentService()
        self._projects_available_cache: Dict[Tuple[str, str], bool] = {}

    def export_bills(self, *, tenant_id: str, service_id: str, limit: int = 500, offset: int = 0, object_id: Optional[str] = None, email: Optional[str] = None, user_role: Optional[str] = None) -> Dict[str, Any]:
        logger.info("export_bills called | tenant_id=%s service_id=%s limit=%s offset=%s", tenant_id, service_id, limit, offset)
        self._projects_available_cache = {}
        safe_audit_log(
            tenant_id=tenant_id,
            action="bill_export_started",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="started",
            resource_type="quickbooks_bill",
        )
        # Track final outcome so the finally block logs it regardless of which exit path is taken.
        _audit_action = "bill_export_failed"
        _audit_status = "failed"
        _audit_context: Optional[str] = None
        try:
            logger.debug(
                "bill export request accepted | tenant_id=%s service_id=%s limit=%s offset=%s",
                tenant_id,
                service_id,
                limit,
                offset,
            )
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}

            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = (connection.get(const.KEY_ENV) or "sandbox").lower()
            conn_status = (connection.get(const.KEY_STATUS) or const.STATUS_CONNECTED).lower()
            if conn_status and conn_status != const.STATUS_CONNECTED:
                _audit_action, _audit_context = "bill_export_aborted", f"not_connected status={conn_status}"
                return {const.KEY_OK: False, const.KEY_MESSAGE: f"QuickBooks is not connected (status={conn_status})"}
            if not refresh_token or not realm_id:
                _audit_action, _audit_context = "bill_export_aborted", "missing_realmId_or_refreshToken"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks not connected for this tenant (missing realmId/refreshToken)."}

            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)
            if not client_id or not client_secret:
                _audit_action, _audit_context = "bill_export_aborted", "missing_credentials"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks client credentials missing (ClientId/ClientSecret)."}

            access_token = self.refresh_access_token(
                tenant_id=tenant_id,
                service_id=service_id,
                connection=connection,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
            )
            base_url = self.get_qbo_api_base(env)
            logger.debug("bill export connection ready | env=%s realm_id_present=%s base_url=%s", env, bool(realm_id), base_url)
            qbo_service_master_id = self.qbo_repo.getServiceIdByName(const.SERVICE_NAME_QUICKBOOKS)
            if qbo_service_master_id is None:
                _audit_action, _audit_context = "bill_export_aborted", "missing_service_master"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks service_master mapping not found."}

            candidates = self.receipt_repo.get_export_candidates(tenant_id=tenant_id, service_id=service_id, limit=limit, offset=offset)
            logger.debug("bill export candidates fetched | count=%s", len(candidates))
            logger.info("export_bills started | tenant=%s service=%s candidates=%s", tenant_id, service_id, len(candidates))
            summary: Dict[str, Any] = {
                const.KEY_OK: True,
                "tenantId": tenant_id,
                "serviceId": str(service_id),
                "realmIdPresent": bool(realm_id),
                "env": env,
                "requested": len(candidates),
                "skippedAlreadyMapped": 0,
                "skippedQboNewer": 0,
                "mapped from QBO": 0,
                "createdInQbo": 0,
                "updatedInQbo": 0,
                "mappedJournalEntriesFromQBO": 0,
                "createdJournalEntriesInQbo": 0,
                "updatedJournalEntriesInQbo": 0,
                "failed": 0,
                const.KEY_FAILURES: [],
                const.KEY_RESULTS: [],
            }
            if not candidates:
                summary[const.KEY_MESSAGE] = "No pending bill export candidates found."
                _audit_action, _audit_status, _audit_context = "bill_export_completed", "success", "no_candidates"
                return summary

            for item in candidates:
                receipt = item["receipt"]
                mapping = item["mapping"] or {}
                receipt_id = int(receipt["id"])
                info = self.receipt_repo.parse_additional_info(receipt.get("receipt_additional_info"))
                doc_number = self.receipt_repo._pick_doc_number(receipt, info)
                logger.debug("processing receipt export candidate | receipt_id=%s doc_number=%r vendor_id=%s chart_of_account_id=%s mapping_status=%r external_receipt_id=%r", receipt_id, doc_number, receipt.get("vendor_id"), receipt.get("chart_of_account_id"), mapping.get("sync_status"), mapping.get("external_receipt_id"))
                is_stock_debit = self._is_stock_debit_bill(receipt)
                is_stock_credit = self._is_stock_credit_bill(receipt)
                is_stock_journal_entry = is_stock_debit or is_stock_credit
                expected_entity_type = (
                    const.QBO_RECEIPT_ENTITY_TYPE_JOURNAL_ENTRY
                    if is_stock_journal_entry
                    else const.QBO_RECEIPT_ENTITY_TYPE_BILL
                )
                external_mapping_id = mapping.get("external_receipt_id") or None
                detail = {
                    const.KEY_FIELDCRAFT_BILL_ID: receipt_id,
                    "displayName": doc_number or f"Bill {receipt_id}",
                    const.KEY_STATUS: None,
                    const.KEY_ACTION: None,
                    const.KEY_QBO_ENTITY_TYPE: expected_entity_type,
                    const.KEY_QBO_BILL_ID: external_mapping_id if not is_stock_journal_entry else None,
                    const.KEY_QBO_JOURNAL_ENTRY_ID: external_mapping_id if is_stock_journal_entry else None,
                    const.KEY_SYNC_TOKEN: mapping.get("sync_token"),
                    const.KEY_REASON: None,
                    const.KEY_ERROR: None,
                }
                try:
                    mapping_id = mapping.get("id")
                    if not mapping_id:
                        raise Exception("Missing receipt external map row for export candidate")

                    external_receipt_id = self._clean(mapping.get("external_receipt_id"))
                    if external_receipt_id:
                        self._assert_mapping_realm(mapping, str(realm_id), "bill")
                    current_status = self._clean(mapping.get("sync_status")).upper()
                    logger.debug(
                        "processing bill export candidate | receipt_id=%s doc_number=%r qbo_bill_id_present=%s status=%s",
                        receipt_id,
                        doc_number,
                        bool(external_receipt_id),
                        current_status,
                    )
                    if external_receipt_id and current_status not in (self.receipt_repo.STATUS_PENDING_PUSH, self.receipt_repo.STATUS_FAILED, ""):
                        logger.info("skipping already mapped receipt | receipt_id=%s qbo_bill_id=%s status=%s", receipt_id, external_receipt_id, current_status)
                        logger.debug("skipping already mapped bill | receipt_id=%s qbo_bill_id=%s status=%s", receipt_id, external_receipt_id, current_status)
                        summary["skippedAlreadyMapped"] += 1
                        continue

                    if is_stock_journal_entry:
                        self._export_stock_debit_bill_as_journal_entry(
                            stock_credit=is_stock_credit,
                            tenant_id=tenant_id,
                            service_id=service_id,
                            realm_id=str(realm_id),
                            qbo_service_master_id=str(qbo_service_master_id),
                            access_token=access_token,
                            base_url=base_url,
                            receipt=receipt,
                            mapping_id=mapping_id,
                            external_receipt_id=external_receipt_id,
                            mapping=mapping,
                            summary=summary,
                            detail=detail,
                        )
                        continue

                    payload = self._build_qbo_bill_payload(
                        tenant_id=tenant_id,
                        service_id=service_id,
                        realm_id=str(realm_id),
                        qbo_service_master_id=str(qbo_service_master_id),
                        receipt=receipt,
                    )
                    if not payload:
                        raise Exception("Bill payload could not be built")
                    logger.debug(
                        "bill payload summary built | receipt_id=%s vendor_ref_present=%s txn_date=%s "
                        "doc_number=%r line_count=%s total_amount=%s",
                        receipt_id,
                        bool(payload.get("VendorRef")),
                        payload.get("TxnDate"),
                        payload.get("DocNumber"),
                        len(payload.get("Line", [])),
                        payload.get("TotalAmt"),
                    )

                    if external_receipt_id:
                        try:
                            stored_sync_token = self._clean(mapping.get("sync_token")) or None
                            logger.debug(
                                "bill export update path selected | receipt_id=%s qbo_bill_id=%s stored_sync_token_present=%s",
                                receipt_id,
                                external_receipt_id,
                                bool(stored_sync_token),
                            )
                            qbo_current = self.get_bill_in_qbo(
                                access_token=access_token,
                                base_url=base_url,
                                realm_id=str(realm_id),
                                bill_id=external_receipt_id,
                            )
                            qbo_current_sync_token = self._clean(qbo_current.get(const.QBO_KEY_SYNC_TOKEN)) or None
                            qbo_last_updated_at = self._get_qbo_last_updated_at(qbo_current)
                            local_updated_at = self._get_local_receipt_updated_at(receipt, None)
                            logger.debug(
                                "QBO bill fetched for update | receipt_id=%s qbo_bill_id=%s "
                                "qbo_sync_token_present=%s qbo_updated_at=%s local_updated_at=%s",
                                receipt_id,
                                external_receipt_id,
                                bool(qbo_current_sync_token),
                                qbo_last_updated_at,
                                local_updated_at,
                            )
                            if stored_sync_token and qbo_current_sync_token:
                                qbo_changed_independently = stored_sync_token != qbo_current_sync_token
                            else:
                                qbo_changed_independently = bool(qbo_last_updated_at and local_updated_at and qbo_last_updated_at > local_updated_at)
                            if qbo_changed_independently and qbo_last_updated_at and local_updated_at and qbo_last_updated_at > local_updated_at:
                                logger.debug(
                                    "skipping bill export because QBO is newer | receipt_id=%s qbo_bill_id=%s "
                                    "local_updated_at=%s qbo_updated_at=%s",
                                    receipt_id,
                                    external_receipt_id,
                                    local_updated_at,
                                    qbo_last_updated_at,
                                )
                                logger.info("skipping export because QBO is newer | receipt_id=%s qbo_bill_id=%s local_updated_at=%s qbo_updated_at=%s", receipt_id, external_receipt_id, local_updated_at, qbo_last_updated_at)
                                self.receipt_map_repo.mark_mapping_synced(
                                    tenant_id=tenant_id,
                                    mapping_id=mapping_id,
                                    external_receipt_id=external_receipt_id,
                                    external_account_id=str(realm_id),
                                    sync_token=qbo_current_sync_token,
                                    reason=self._build_reason(
                                        "QuickBooks has a newer version of this bill. The next import will apply those changes here.",
                                        managed_line=self._get_managed_line_metadata(mapping),
                                    ),
                                )
                                summary["skippedQboNewer"] += 1
                                detail.update({
                                    const.KEY_STATUS: "skipped",
                                    const.KEY_ACTION: "skippedQboNewer",
                                    const.KEY_QBO_BILL_ID: external_receipt_id,
                                    const.KEY_SYNC_TOKEN: qbo_current_sync_token,
                                    const.KEY_REASON: self._build_reason("QuickBooks has a newer version of this bill. Changes will be applied on the next import."),
                                })
                                summary[const.KEY_RESULTS].append(detail)
                                continue

                            logger.info("updating bill in QBO | receipt_id=%s qbo_bill_id=%s", receipt_id, external_receipt_id)
                            logger.debug("updating bill in QBO | receipt_id=%s qbo_bill_id=%s", receipt_id, external_receipt_id)
                            updated = self.update_bill_in_qbo(
                                access_token=access_token,
                                base_url=base_url,
                                realm_id=str(realm_id),
                                bill_id=external_receipt_id,
                                payload=payload,
                                sync_token=qbo_current_sync_token or stored_sync_token,
                            )
                            updated_id = self._clean((updated or {}).get(const.QBO_KEY_ID)) or external_receipt_id
                            updated_token = self._clean((updated or {}).get(const.QBO_KEY_SYNC_TOKEN)) or None
                            logger.info(
                                "updated bill in QBO | receipt_id=%s qbo_bill_id=%s sync_token_present=%s",
                                receipt_id,
                                updated_id,
                                bool(updated_token),
                            )
                            self._try_attach_document(
                                tenant_id=tenant_id,
                                access_token=access_token,
                                base_url=base_url,
                                realm_id=str(realm_id),
                                bill_id=updated_id,
                                document_id=receipt.get("document_id"),
                            )
                            self.receipt_map_repo.mark_mapping_synced(
                                tenant_id=tenant_id,
                                mapping_id=mapping_id,
                                external_receipt_id=updated_id,
                                external_account_id=str(realm_id),
                                sync_token=updated_token,
                                reason=self._build_reason(
                                    "Bill updated in QuickBooks successfully.",
                                    managed_line=self._build_managed_line_metadata(
                                        qbo_bill=updated or {},
                                        payload=payload,
                                        receipt=receipt,
                                        allow_single_line_fallback=True,
                                    ),
                                ),
                            )
                            summary["updatedInQbo"] += 1
                            detail.update({
                                const.KEY_STATUS: "success",
                                const.KEY_ACTION: "updated in QBO",
                                const.KEY_QBO_BILL_ID: updated_id,
                                const.KEY_SYNC_TOKEN: updated_token,
                                const.KEY_REASON: self._build_reason("Bill updated in QuickBooks successfully."),
                            })
                            summary[const.KEY_RESULTS].append(detail)
                            continue
                        except Exception as exc:
                            if not self._is_qbo_stale_reference_error(str(exc)):
                                raise

                    logger.debug("bill export match/create path selected | receipt_id=%s doc_number=%r", receipt_id, doc_number)
                    candidates_in_qbo = self.find_bill_candidates_in_qbo(
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=str(realm_id),
                        receipt=receipt,
                    )
                    logger.debug("QBO bill candidate lookup completed | receipt_id=%s candidates=%s", receipt_id, len(candidates_in_qbo))
                    logger.debug("QBO bill candidates found | receipt_id=%s count=%s", receipt_id, len(candidates_in_qbo))
                    if candidates_in_qbo:
                        if len(candidates_in_qbo) > 1:
                            raise Exception("Multiple bills with this document number were found in QuickBooks. Make the bill number unique in QuickBooks and try again.")
                        matched = candidates_in_qbo[0]
                        found_id = self._clean(matched.get(const.QBO_KEY_ID))
                        found_token = self._clean(matched.get(const.QBO_KEY_SYNC_TOKEN)) or None
                        logger.info(
                            "matched existing QBO bill | receipt_id=%s qbo_bill_id=%s sync_token_present=%s",
                            receipt_id,
                            found_id,
                            bool(found_token),
                        )
                        self._try_attach_document(
                            tenant_id=tenant_id,
                            access_token=access_token,
                            base_url=base_url,
                            realm_id=str(realm_id),
                            bill_id=found_id,
                            document_id=receipt.get("document_id"),
                        )
                        self.receipt_map_repo.mark_mapping_synced(
                            tenant_id=tenant_id,
                            mapping_id=mapping_id,
                            external_receipt_id=found_id,
                            external_account_id=str(realm_id),
                            sync_token=found_token,
                            reason=self._build_reason(
                                "Bill matched to an existing QuickBooks bill and linked successfully.",
                                managed_line=self._build_managed_line_metadata(
                                    qbo_bill=matched or {},
                                    payload=payload,
                                    receipt=receipt,
                                ),
                            ),
                        )
                        summary["mapped from QBO"] += 1
                        detail.update({
                            const.KEY_STATUS: "success",
                            const.KEY_ACTION: "mapped from QBO",
                            const.KEY_QBO_BILL_ID: found_id,
                            const.KEY_SYNC_TOKEN: found_token,
                            const.KEY_REASON: self._build_reason("Bill matched to an existing QuickBooks bill and linked successfully."),
                        })
                        summary[const.KEY_RESULTS].append(detail)
                        continue

                    logger.debug(
                        "creating bill in QBO | receipt_id=%s vendor_ref_present=%s txn_date=%s "
                        "doc_number=%r line_count=%s total_amount=%s",
                        receipt_id,
                        bool(payload.get("VendorRef")),
                        payload.get("TxnDate"),
                        payload.get("DocNumber"),
                        len(payload.get("Line", [])),
                        payload.get("TotalAmt"),
                    )
                    created = self.create_bill_in_qbo(
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=str(realm_id),
                        payload=payload,
                    )
                    created_id = self._clean((created or {}).get(const.QBO_KEY_ID))
                    logger.info("created bill in QBO | receipt_id=%s qbo_bill_id=%s", receipt_id, created_id)
                    if not created_id:
                        raise Exception("QuickBooks created no usable Bill ID.")
                    created_token = self._clean((created or {}).get(const.QBO_KEY_SYNC_TOKEN)) or None
                    self._try_attach_document(
                        tenant_id=tenant_id,
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=str(realm_id),
                        bill_id=created_id,
                        document_id=receipt.get("document_id"),
                    )
                    self.receipt_map_repo.mark_mapping_synced(
                        tenant_id=tenant_id,
                        mapping_id=mapping_id,
                        external_receipt_id=created_id,
                        external_account_id=str(realm_id),
                        sync_token=created_token,
                        reason=self._build_reason(
                            "Bill created in QuickBooks successfully.",
                            managed_line=self._build_managed_line_metadata(
                                qbo_bill=created or {},
                                payload=payload,
                                receipt=receipt,
                                allow_single_line_fallback=True,
                            ),
                        ),
                    )
                    summary["createdInQbo"] += 1
                    detail.update({
                        const.KEY_STATUS: "success",
                        const.KEY_ACTION: "created in QBO",
                        const.KEY_QBO_BILL_ID: created_id,
                        const.KEY_SYNC_TOKEN: created_token,
                        const.KEY_REASON: self._build_reason("Bill created in QuickBooks successfully."),
                    })
                    summary[const.KEY_RESULTS].append(detail)
                except Exception as exc:
                    intuit_tid = exc.intuit_tid if isinstance(exc, QuickBooksAPIError) else None
                    exc_msg = exc.user_message if isinstance(exc, QuickBooksAPIError) else str(exc)
                    logger.warning("bill export failed for receipt | receipt_id=%s error=%r", receipt_id, exc_msg, exc_info=True)

                    # When QBO rejects because a referenced vendor was deleted, mark that
                    # vendor mapping as FAILED so the next vendor sync will re-evaluate it.
                    if self._is_qbo_deleted_ref_error(exc_msg):
                        vendor_id_for_exc = receipt.get("vendor_id")
                        if vendor_id_for_exc is not None:
                            try:
                                vendor_label = self._get_vendor_label(tenant_id=tenant_id, vendor_id=vendor_id_for_exc)
                                v_map = self.vendor_map_repo.get_by_vendor_id(tenant_id, service_id, int(vendor_id_for_exc))
                                if v_map and v_map.get("id"):
                                    self.vendor_map_repo.mark_mapping_failed(
                                        tenant_id=tenant_id,
                                        mapping_id=int(v_map["id"]),
                                        reason=self._build_reason(
                                            "This bill's vendor was deleted in QuickBooks. Re-sync vendors to restore the mapping."
                                        ),
                                    )
                                    logger.warning(
                                        "vendor mapping marked failed because vendor was deleted in QBO | vendor=%s",
                                        vendor_label,
                                    )
                            except Exception as vmap_exc:
                                logger.warning("could not mark vendor mapping failed | error=%s", vmap_exc)

                    if mapping.get("id"):
                        self.receipt_map_repo.mark_mapping_failed(
                            tenant_id=tenant_id,
                            mapping_id=mapping_id,
                            reason=self._build_reason(exc_msg, intuit_tid=intuit_tid),
                        )
                    summary["failed"] += 1
                    qbo_api.append_failure(summary, {
                        const.KEY_FIELDCRAFT_BILL_ID: receipt_id,
                        "externalReceiptId": mapping.get("external_receipt_id") or None,
                        const.KEY_ERROR: exc_msg,
                    })
                    detail.update({
                        const.KEY_STATUS: "failed",
                        const.KEY_ACTION: "error",
                        const.KEY_ERROR: exc_msg,
                        const.KEY_REASON: self._build_reason(exc_msg, intuit_tid=intuit_tid),
                    })
                    summary[const.KEY_RESULTS].append(detail)
            _audit_action = "bill_export_completed"
            _audit_status = "success"
            _audit_context = f"exported={summary.get('createdInQbo', 0) + summary.get('updatedInQbo', 0)} failed={summary.get('failed', 0)}"
            logger.info(
                "bill export completed | created=%s updated=%s matched=%s skipped_mapped=%s skipped_qbo_newer=%s failed=%s",
                summary.get("createdInQbo", 0),
                summary.get("updatedInQbo", 0),
                summary.get("mapped from QBO", 0),
                summary.get("skippedAlreadyMapped", 0),
                summary.get("skippedQboNewer", 0),
                summary.get("failed", 0),
            )
            return summary
        except Exception as e:
            logger.exception("bill export failed | error=%s", e)
            _audit_action = "bill_export_failed"
            _audit_status = "failed"
            _audit_context = str(e)[:64]
            return {
                const.KEY_OK: False,
                const.KEY_MESSAGE: "Bill export could not be completed. Review the server logs using the request correlation details and retry.",
                const.KEY_ERROR: "Unexpected bill export error.",
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

    def _is_stock_debit_bill(self, receipt: Dict[str, Any]) -> bool:
        return self._normalize_receipt_name((receipt or {}).get("name")) == self._normalize_receipt_name(
            getattr(const, "QBO_STOCK_DEBIT_BILL_NAME", "StockDebitBill")
        )

    def _is_stock_credit_bill(self, receipt: Dict[str, Any]) -> bool:
        return self._normalize_receipt_name((receipt or {}).get("name")) == self._normalize_receipt_name(
            getattr(const, "QBO_STOCK_CREDIT_BILL_NAME", "StockCreditBill")
        )

    def _export_stock_debit_bill_as_journal_entry(
        self,
        *,
        stock_credit: bool = False,
        tenant_id: str,
        service_id: str,
        realm_id: str,
        qbo_service_master_id: str,
        access_token: str,
        base_url: str,
        receipt: Dict[str, Any],
        mapping_id: int,
        external_receipt_id: Optional[str],
        mapping: Dict[str, Any],
        summary: Dict[str, Any],
        detail: Dict[str, Any],
    ) -> None:
        """Export a StockDebitBill as a QuickBooks JournalEntry.

        StockDebitBill is an internal inventory consumption event. It does not
        create vendor payable, so it must not be pushed to QBO as a Bill.
        """
        stock_bill_name = "StockCreditBill" if stock_credit else "StockDebitBill"
        receipt_id = int(receipt["id"])
        logger.info(
            "%s journal entry export started | receipt_id=%s project_id=%s mapping_id=%s existing_qbo_journal_entry_id=%s",
            stock_bill_name,
            receipt_id,
            receipt.get("project_id"),
            mapping_id,
            external_receipt_id or None,
        )
        payload = (
            self._build_stock_credit_journal_entry_payload(
                tenant_id=tenant_id,
                service_id=service_id,
                realm_id=realm_id,
                qbo_service_master_id=qbo_service_master_id,
                receipt=receipt,
            )
            if stock_credit
            else self._build_stock_debit_journal_entry_payload(
                tenant_id=tenant_id,
                service_id=service_id,
                realm_id=realm_id,
                qbo_service_master_id=qbo_service_master_id,
                receipt=receipt,
            )
        )
        doc_number = self._clean(payload.get("DocNumber"))
        logger.info(
            "%s journal entry payload ready | receipt_id=%s doc_number=%s txn_date=%s amount=%s project_ref=%s entity_ref=%s",
            stock_bill_name,
            receipt_id,
            doc_number or None,
            payload.get("TxnDate"),
            self._journal_entry_total_debit_amount(payload),
            self._first_journal_entry_project_ref(payload),
            self._first_journal_entry_entity_ref(payload),
        )
        logger.debug(
            "%s journal entry payload summary | receipt_id=%s payload_summary=%s",
            stock_bill_name,
            receipt_id,
            self._summarize_journal_entry_payload(payload),
        )
        detail[const.KEY_QBO_ENTITY_TYPE] = "JournalEntry"
        detail[const.KEY_QBO_JOURNAL_ENTRY_ID] = external_receipt_id or None
        detail[const.KEY_QBO_BILL_ID] = None

        if external_receipt_id:
            try:
                stored_sync_token = self._clean(mapping.get("sync_token")) or None
                qbo_current = self.get_journal_entry_in_qbo(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=realm_id,
                    journal_entry_id=external_receipt_id,
                )
                qbo_current_sync_token = self._clean(qbo_current.get(const.QBO_KEY_SYNC_TOKEN)) or None
                qbo_last_updated_at = self._get_qbo_last_updated_at(qbo_current)
                local_updated_at = self._get_local_receipt_updated_at(receipt, None)

                if stored_sync_token and qbo_current_sync_token:
                    qbo_changed_independently = stored_sync_token != qbo_current_sync_token
                else:
                    qbo_changed_independently = bool(qbo_last_updated_at and local_updated_at and qbo_last_updated_at > local_updated_at)

                if qbo_changed_independently and qbo_last_updated_at and local_updated_at and qbo_last_updated_at > local_updated_at:
                    self.receipt_map_repo.mark_mapping_synced(
                        tenant_id=tenant_id,
                        mapping_id=mapping_id,
                        external_receipt_id=external_receipt_id,
                        external_account_id=realm_id,
                        sync_token=qbo_current_sync_token,
                        reason=self._build_reason(
                            f"QuickBooks has a newer version of this {stock_bill_name} journal entry. The next import/review should apply those changes here."
                        ),
                    )
                    summary["skippedQboNewer"] += 1
                    detail.update({
                        const.KEY_STATUS: "skipped",
                        const.KEY_ACTION: "skippedQboNewer",
                        const.KEY_QBO_JOURNAL_ENTRY_ID: external_receipt_id,
                        const.KEY_SYNC_TOKEN: qbo_current_sync_token,
                        const.KEY_REASON: self._build_reason(f"QuickBooks has a newer version of this {stock_bill_name} journal entry."),
                    })
                    summary[const.KEY_RESULTS].append(detail)
                    return

                updated = self.update_journal_entry_in_qbo(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=realm_id,
                    journal_entry_id=external_receipt_id,
                    payload=payload,
                    sync_token=qbo_current_sync_token or stored_sync_token,
                )
                updated_id = self._clean((updated or {}).get(const.QBO_KEY_ID)) or external_receipt_id
                updated_token = self._clean((updated or {}).get(const.QBO_KEY_SYNC_TOKEN)) or None
                self.receipt_map_repo.mark_mapping_synced(
                    tenant_id=tenant_id,
                    mapping_id=mapping_id,
                    external_receipt_id=updated_id,
                    external_account_id=realm_id,
                    sync_token=updated_token,
                    reason=self._build_reason(f"{stock_bill_name} journal entry updated in QuickBooks successfully."),
                )
                summary["updatedInQbo"] += 1
                summary["updatedJournalEntriesInQbo"] += 1
                detail.update({
                    const.KEY_STATUS: "success",
                    const.KEY_ACTION: "journal entry updated in QBO",
                    const.KEY_QBO_JOURNAL_ENTRY_ID: updated_id,
                    const.KEY_SYNC_TOKEN: updated_token,
                    const.KEY_REASON: self._build_reason(f"{stock_bill_name} journal entry updated in QuickBooks successfully."),
                })
                summary[const.KEY_RESULTS].append(detail)
                return
            except Exception as exc:
                if not self._is_qbo_stale_reference_error(str(exc)):
                    raise

        candidates_in_qbo = self.find_journal_entry_candidates_in_qbo(
            access_token=access_token,
            base_url=base_url,
            realm_id=realm_id,
            doc_number=doc_number,
        )
        if candidates_in_qbo:
            if len(candidates_in_qbo) > 1:
                raise Exception(
                    f"Multiple journal entries with this {stock_bill_name} document number were found in QuickBooks. Make the document number unique and try again."
                )
            matched = candidates_in_qbo[0]
            found_id = self._clean(matched.get(const.QBO_KEY_ID))
            found_token = self._clean(matched.get(const.QBO_KEY_SYNC_TOKEN)) or None
            self.receipt_map_repo.mark_mapping_synced(
                tenant_id=tenant_id,
                mapping_id=mapping_id,
                external_receipt_id=found_id,
                external_account_id=realm_id,
                sync_token=found_token,
                reason=self._build_reason(f"{stock_bill_name} matched to an existing QuickBooks journal entry and linked successfully."),
            )
            summary["mapped from QBO"] += 1
            summary["mappedJournalEntriesFromQBO"] += 1
            detail.update({
                const.KEY_STATUS: "success",
                const.KEY_ACTION: "journal entry mapped from QBO",
                const.KEY_QBO_JOURNAL_ENTRY_ID: found_id,
                const.KEY_SYNC_TOKEN: found_token,
                const.KEY_REASON: self._build_reason(f"{stock_bill_name} matched to an existing QuickBooks journal entry and linked successfully."),
            })
            summary[const.KEY_RESULTS].append(detail)
            return

        created = self.create_journal_entry_in_qbo(
            access_token=access_token,
            base_url=base_url,
            realm_id=realm_id,
            payload=payload,
        )
        created_id = self._clean((created or {}).get(const.QBO_KEY_ID))
        if not created_id:
            raise Exception("QuickBooks created no usable JournalEntry ID.")
        created_token = self._clean((created or {}).get(const.QBO_KEY_SYNC_TOKEN)) or None
        self.receipt_map_repo.mark_mapping_synced(
            tenant_id=tenant_id,
            mapping_id=mapping_id,
            external_receipt_id=created_id,
            external_account_id=realm_id,
            sync_token=created_token,
            reason=self._build_reason(f"{stock_bill_name} journal entry created in QuickBooks successfully."),
        )
        summary["createdInQbo"] += 1
        summary["createdJournalEntriesInQbo"] += 1
        detail.update({
            const.KEY_STATUS: "success",
            const.KEY_ACTION: "journal entry created in QBO",
            const.KEY_QBO_JOURNAL_ENTRY_ID: created_id,
            const.KEY_SYNC_TOKEN: created_token,
            const.KEY_REASON: self._build_reason(f"{stock_bill_name} journal entry created in QuickBooks successfully."),
        })
        summary[const.KEY_RESULTS].append(detail)

    def _build_stock_debit_journal_entry_payload(
        self,
        *,
        tenant_id: str,
        service_id: str,
        realm_id: str,
        qbo_service_master_id: str,
        receipt: Dict[str, Any],
    ) -> Dict[str, Any]:
        info = self.receipt_repo.parse_additional_info(receipt.get("receipt_additional_info"))
        receipt_id = receipt.get("id")
        stock_bill_name = "StockCreditBill" if self._is_stock_credit_bill(receipt) else "StockDebitBill"
        debit_account_id = receipt.get("chart_of_account_id")
        if debit_account_id is None:
            raise Exception("This StockDebitBill is missing the debit chart of accounts assignment. Assign a project material/COGS account and try again.")
        debit_label = self._get_coa_label(tenant_id=tenant_id, account_id=debit_account_id)

        debit_account_map = self.coa_repo.get_external_id_for_account_id(
            tenant_id=tenant_id,
            account_id=int(debit_account_id),
            service_id=str(qbo_service_master_id),
            external_acc_id=realm_id,
        )
        qbo_debit_account_id = self._clean((debit_account_map or {}).get("external_id"))
        if not qbo_debit_account_id:
            raise Exception(
                f"StockDebitBill debit account '{debit_label}' hasn't been synced to QuickBooks yet. Sync your Chart of Accounts first, then retry."
            )

        inventory_account_name = getattr(const, "QBO_INVENTORY_ASSET_ACCOUNT_NAME", "Inventory Asset")
        inventory_account_map = self.coa_repo.get_external_id_for_account_name(
            tenant_id=tenant_id,
            name=inventory_account_name,
            service_id=str(qbo_service_master_id),
            external_acc_id=realm_id,
            allowed_account_types=getattr(
                const,
                "QBO_INVENTORY_ASSET_ACCOUNT_TYPES",
                ("Other Current Asset", "OtherCurrentAsset"),
            ),
            allowed_detail_types=getattr(
                const,
                "QBO_INVENTORY_ASSET_DETAIL_TYPES",
                ("Inventory",),
            ),
        )
        qbo_inventory_account_id = self._clean((inventory_account_map or {}).get("external_id"))
        if not qbo_inventory_account_id:
            raise Exception(
                "Inventory Asset account hasn't been synced to QuickBooks yet. Sync your Chart of Accounts first, then retry the StockDebitBill journal entry."
            )
        logger.info(
            "StockDebitBill COA mapping resolved | receipt_id=%s debit_coa_id=%s debit_coa_name=%s debit_qbo_account_id=%s credit_coa_name=%s credit_qbo_account_id=%s",
            receipt_id,
            debit_account_id,
            debit_label,
            qbo_debit_account_id,
            inventory_account_name,
            qbo_inventory_account_id,
        )

        qbo_project_ref = self._resolve_qbo_project_ref_for_stock_debit(
            tenant_id=tenant_id,
            service_id=service_id,
            realm_id=realm_id,
            receipt=receipt,
        )
        amount = (
            self._resolve_stock_credit_amount(receipt=receipt, info=info)
            if self._is_stock_credit_bill(receipt)
            else self._resolve_stock_debit_amount(receipt=receipt, info=info)
        )
        if amount is None or amount <= 0:
            raise Exception(
                f"This {stock_bill_name} has no valid cost amount. "
                f"Update the {stock_bill_name} cost and try again."
            )
        doc_number = self.receipt_repo._pick_doc_number(receipt, info) or f"SDB-{receipt_id}"
        item_summary = self._build_receipt_items_summary_description(receipt=receipt, info=info)
        logger.info(
            "StockDebitBill amount resolved | receipt_id=%s amount=%s source_doc=%s item_summary=%s",
            receipt_id,
            amount,
            doc_number or None,
            item_summary or None,
        )

        txn_date = self.receipt_repo._pick_txn_date(receipt, info)
        if not txn_date:
            raise Exception("This StockDebitBill is missing a date. Add a bill date and try again.")

        debit_description = self._clean(item_summary or receipt.get("description") or f"{stock_bill_name} {receipt_id}")
        credit_description = self._clean(f"Inventory reduction for StockDebitBill {doc_number or receipt_id}")

        payload: Dict[str, Any] = {
            "TxnDate": txn_date,
            "Line": [
                self._build_journal_entry_line(
                    amount=float(amount),
                    posting_type="Debit",
                    qbo_account_id=qbo_debit_account_id,
                    description=debit_description,
                    qbo_project_ref=qbo_project_ref,
                ),
                self._build_journal_entry_line(
                    amount=float(amount),
                    posting_type="Credit",
                    qbo_account_id=qbo_inventory_account_id,
                    description=credit_description,
                ),
            ],
        }
        if doc_number:
            payload["DocNumber"] = str(doc_number)[:21]

        private_note = self._clean(
            info.get("category")
            or receipt.get("description")
            or f"StockDebitBill {receipt_id} - stock used for project {receipt.get('project_id')}"
        )
        if private_note:
            payload["PrivateNote"] = private_note[:4000]
        return payload

    def _build_stock_credit_journal_entry_payload(
        self,
        *,
        tenant_id: str,
        service_id: str,
        realm_id: str,
        qbo_service_master_id: str,
        receipt: Dict[str, Any],
    ) -> Dict[str, Any]:
        payload = self._build_stock_debit_journal_entry_payload(
            tenant_id=tenant_id,
            service_id=service_id,
            realm_id=realm_id,
            qbo_service_master_id=qbo_service_master_id,
            receipt=receipt,
        )

        account_line, inventory_line = payload["Line"]
        account_line["JournalEntryLineDetail"]["PostingType"] = "Credit"
        inventory_line["JournalEntryLineDetail"]["PostingType"] = "Debit"
        payload["Line"] = [inventory_line, account_line]

        receipt_id = receipt.get("id")
        if payload.get("DocNumber") == f"SDB-{receipt_id}":
            payload["DocNumber"] = f"SCB-{receipt_id}"[:21]
        inventory_line["Description"] = f"Inventory return for StockCreditBill {payload.get('DocNumber') or receipt_id}"
        if str(payload.get("PrivateNote") or "").startswith("StockDebitBill"):
            payload["PrivateNote"] = f"StockCreditBill {receipt_id} - stock returned from project {receipt.get('project_id')}"
        return payload

    def _resolve_stock_debit_amount(self, *, receipt: Dict[str, Any], info: Dict[str, Any]) -> Optional[float]:
        buying_total = self._extract_money_amount(info.get("buyingTotal"))
        if buying_total is not None and buying_total > 0:
            return round(float(buying_total), 2)

        items = self._extract_receipt_items(receipt=receipt, info=info)
        if not items:
            return None

        total = 0.0
        for item in items:
            item_amount = self._extract_money_amount(item.get("amount"))
            if item_amount is None:
                return None
            total += float(item_amount)
        return round(total, 2) if total > 0 else None

    def _resolve_stock_credit_amount(self, *, receipt: Dict[str, Any], info: Dict[str, Any]) -> Optional[float]:
        buying_total = self._extract_money_amount(info.get("buyingTotal"))
        if buying_total is not None and buying_total > 0:
            return round(float(buying_total), 2)

        items = self._extract_receipt_items(receipt=receipt, info=info)
        if not items:
            return None

        total = 0.0
        for item in items:
            item_amount = self._extract_money_amount(item.get("amount"))
            if item_amount is None:
                return None
            total += float(item_amount)
        return round(total, 2) if total > 0 else None

    def _resolve_qbo_project_ref_for_stock_debit(
        self,
        *,
        tenant_id: str,
        service_id: str,
        realm_id: str,
        receipt: Dict[str, Any],
    ) -> Dict[str, str]:
        project_id = receipt.get("project_id")
        if project_id is None or self._clean(project_id).upper() == "NULL":
            raise Exception("This StockDebitBill is missing a project. Assign the project that consumed the stock and try again.")
        project_ref = self._resolve_qbo_project_ref_for_receipt(
            tenant_id=tenant_id,
            service_id=service_id,
            realm_id=realm_id,
            receipt=receipt,
        )
        if not project_ref or not self._clean(project_ref.get("id")):
            raise Exception("This StockDebitBill project hasn't been synced to QuickBooks yet. Run project sync first, then retry.")
        if project_ref.get("target") == const.PROJECT_SYNC_TARGET_QBO_PROJECT:
            project_ref = dict(project_ref)
            project_ref["parent_customer_id"] = self._resolve_qbo_parent_customer_id_for_project(
                tenant_id=tenant_id,
                service_id=service_id,
                realm_id=realm_id,
                project_id=project_id,
            )
        logger.info(
            "StockDebitBill project mapping resolved | receipt_id=%s fieldcraft_project_id=%s qbo_project_id=%s sync_target=%s qbo_parent_customer_id=%s",
            receipt.get("id"),
            project_id,
            project_ref.get("id"),
            project_ref.get("target"),
            project_ref.get("parent_customer_id"),
        )
        return project_ref

    def _build_journal_entry_line(
        self,
        *,
        amount: float,
        posting_type: str,
        qbo_account_id: str,
        description: Optional[str] = None,
        qbo_project_ref: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        detail: Dict[str, Any] = {
            "PostingType": posting_type,
            "AccountRef": {"value": qbo_account_id},
        }
        qbo_project_id = self._clean((qbo_project_ref or {}).get("id"))
        line_project_ref: Optional[Dict[str, str]] = None
        if qbo_project_id:
            qbo_project_target = self.project_repo.normalize_project_sync_target(
                (qbo_project_ref or {}).get("target"),
                default=const.PROJECT_SYNC_TARGET_SUB_CUSTOMER,
            )
            if qbo_project_target == const.PROJECT_SYNC_TARGET_QBO_PROJECT:
                parent_customer_id = self._clean((qbo_project_ref or {}).get("parent_customer_id"))
                if not parent_customer_id:
                    raise Exception(
                        "This StockDebitBill project is synced as a QuickBooks Project, but its parent customer is not synced. "
                        "Run customer sync first, then retry."
                    )
                # JournalEntry Entity is a QBO Name reference. Real QBO Project ids are not Name ids,
                # so keep the Name/Entity on the parent customer and place the project id in ProjectRef.
                detail["Entity"] = {
                    "Type": "Customer",
                    "EntityRef": {"value": parent_customer_id},
                }
                line_project_ref = {"value": qbo_project_id}
            else:
                # Sub-customer projects are QBO Customer/Name records, so they can be used directly
                # as the JournalEntry Entity reference.
                detail["Entity"] = {
                    "Type": "Customer",
                    "EntityRef": {"value": qbo_project_id},
                }
                line_project_ref = None
            detail["BillableStatus"] = "NotBillable"

        line: Dict[str, Any] = {
            "Amount": round(float(amount), 2),
            "DetailType": "JournalEntryLineDetail",
            "JournalEntryLineDetail": detail,
        }
        if qbo_project_id and line_project_ref:
            line["ProjectRef"] = line_project_ref
        clean_description = self._clean(description)
        if clean_description:
            line["Description"] = clean_description[:4000]
        return line

    def _summarize_journal_entry_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        lines_summary: List[Dict[str, Any]] = []
        for index, line in enumerate(payload.get("Line") or [], start=1):
            detail = line.get("JournalEntryLineDetail") or {}
            entity = detail.get("Entity") or {}
            entity_ref = entity.get("EntityRef") or {}
            lines_summary.append({
                "line": index,
                "postingType": detail.get("PostingType"),
                "amount": line.get("Amount"),
                "accountRef": (detail.get("AccountRef") or {}).get("value"),
                "entityType": entity.get("Type"),
                "entityRef": entity_ref.get("value"),
                "projectRef": (line.get("ProjectRef") or {}).get("value"),
                "billableStatus": detail.get("BillableStatus"),
                "description": self._clean(line.get("Description"))[:120] or None,
            })
        return {
            "docNumber": payload.get("DocNumber"),
            "txnDate": payload.get("TxnDate"),
            "privateNote": self._clean(payload.get("PrivateNote"))[:120] or None,
            "lines": lines_summary,
        }

    def _journal_entry_total_debit_amount(self, payload: Dict[str, Any]) -> float:
        total = 0.0
        for line in payload.get("Line") or []:
            detail = line.get("JournalEntryLineDetail") or {}
            if self._clean(detail.get("PostingType")).lower() == "debit":
                try:
                    total += float(line.get("Amount") or 0)
                except (TypeError, ValueError):
                    continue
        return round(total, 2)

    def _first_journal_entry_project_ref(self, payload: Dict[str, Any]) -> Optional[str]:
        for line in payload.get("Line") or []:
            project_ref = self._clean((line.get("ProjectRef") or {}).get("value"))
            if project_ref:
                return project_ref
        return None

    def _first_journal_entry_entity_ref(self, payload: Dict[str, Any]) -> Optional[str]:
        for line in payload.get("Line") or []:
            detail = line.get("JournalEntryLineDetail") or {}
            entity = detail.get("Entity") or {}
            entity_ref = self._clean((entity.get("EntityRef") or {}).get("value"))
            if entity_ref:
                return entity_ref
        return None

    def _build_qbo_bill_payload(self, *, tenant_id: str, service_id: str, realm_id: str, qbo_service_master_id: str, receipt: Dict[str, Any], vendor_map_override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        info = self.receipt_repo.parse_additional_info(receipt.get("receipt_additional_info"))
        receipt_id = receipt.get("id")
        vendor_id = receipt.get("vendor_id")
        if vendor_id is None:
            raise Exception("This bill is missing a vendor ID. Assign a vendor and try again.")
        vendor_map = vendor_map_override or self.vendor_map_repo.get_by_vendor_id(tenant_id, service_id, int(vendor_id))
        vendor_sync_status = self._clean((vendor_map or {}).get("sync_status")).upper()
        qbo_vendor_id = self._clean((vendor_map or {}).get("external_vendor_id"))
        vendor_label = self._get_vendor_label(tenant_id=tenant_id, vendor_id=vendor_id)
        logger.debug(
            "resolved vendor mapping | vendor_id=%s qbo_vendor_id_present=%s status=%s",
            vendor_id,
            bool(qbo_vendor_id),
            vendor_sync_status,
        )
        if qbo_vendor_id:
            self._assert_mapping_realm(vendor_map, realm_id, "vendor")
        if not qbo_vendor_id:
            raise Exception(
                f"Vendor '{vendor_label}' has no QuickBooks mapping. Run vendor sync, then retry this bill."
            )
        if vendor_sync_status in ("FAILED", "IMPORT_FAILED"):
            raise Exception(
                f"Vendor '{vendor_label}' failed to sync with QuickBooks (status: {vendor_sync_status}). "
                "Run a vendor sync to resolve this, then retry the bill export."
            )
        qbo_account_id, qbo_account_label = self._resolve_qbo_bill_expense_account(
            tenant_id=tenant_id,
            qbo_service_master_id=str(qbo_service_master_id),
            realm_id=realm_id,
            receipt=receipt,
        )
        logger.debug(
            "resolved bill expense account | receipt_id=%s is_stock_receipt=%s account_label=%r qbo_account_id=%r",
            receipt_id,
            self._is_stock_inventory_bill(receipt),
            qbo_account_label,
            qbo_account_id,
        )

        qbo_project_ref = self._resolve_qbo_project_ref_for_receipt(
            tenant_id=tenant_id,
            service_id=service_id,
            receipt=receipt,
        )
        amount = self.receipt_repo._pick_total_amount(info)
        if amount is None or amount <= 0:
            raise Exception("This bill has no valid total amount. Update the bill amount and try again.")
        txn_date = self.receipt_repo._pick_txn_date(receipt, info)
        if not txn_date:
            raise Exception("This bill is missing a date. Add a bill date and try again.")
        due_date = self.receipt_repo._pick_due_date(receipt, info)
        doc_number = self.receipt_repo._pick_doc_number(receipt, info)
        # Keep FIELDCRAFT material rows unchanged. Only the QuickBooks bill payload is
        # flattened to one account-based line for the full bill total.
        material_summary = self._build_receipt_items_summary_description(
            receipt=receipt,
            info=info,
        )
        lines = [
            self._build_account_based_bill_line(
                amount=float(amount),
                qbo_account_id=qbo_account_id,
                qbo_project_ref=qbo_project_ref,
                description=material_summary,
            )
        ]

        payload: Dict[str, Any] = {
            "VendorRef": {"value": qbo_vendor_id},
            "TxnDate": txn_date,
            "Line": lines,
        }
        if due_date:
            payload["DueDate"] = due_date
        if doc_number:
            payload["DocNumber"] = str(doc_number)[:21]
        private_note = self._clean(info.get("category") or doc_number or f"FIELDCRAFT Bill {receipt_id}")
        if private_note:
            payload["PrivateNote"] = private_note[:4000]
        return payload

    def _build_managed_line_metadata(
        self,
        *,
        qbo_bill: Dict[str, Any],
        payload: Dict[str, Any],
        receipt: Dict[str, Any],
        allow_single_line_fallback: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Capture the QBO line that represents the flattened FIELDCRAFT bill.

        The mapping metadata lets the import side ignore only this transport
        line while continuing to import any lines a user adds or edits in QBO.
        """
        payload_lines = payload.get("Line") or []
        if len(payload_lines) != 1:
            return None

        expected = payload_lines[0] or {}
        expected_description = self._clean(expected.get("Description"))
        expected_amount = self._extract_money_amount(expected.get("Amount"))
        expected_fingerprint = description_fingerprint(expected_description)

        qbo_lines = qbo_bill.get("Line") or []
        managed_line = None
        for line in qbo_lines:
            if not isinstance(line, dict):
                continue
            if description_fingerprint(line.get("Description")) == expected_fingerprint:
                managed_line = line
                break

        if (
            managed_line is None
            and allow_single_line_fallback
            and len(qbo_lines) == 1
            and isinstance(qbo_lines[0], dict)
        ):
            managed_line = qbo_lines[0]

        metadata: Dict[str, Any] = {
            "amount": expected_amount,
            "descriptionFingerprint": expected_fingerprint,
            "lineBaseline": managed_bill_line_baseline(expected),
        }
        info = self.receipt_repo.parse_additional_info(receipt.get("receipt_additional_info"))
        metadata["itemCount"] = len(
            self._extract_receipt_item_candidates(receipt=receipt, info=info)
        )
        managed_line_id = self._clean((managed_line or {}).get("Id"))
        if managed_line_id:
            metadata["id"] = managed_line_id
        return metadata

    def _get_managed_line_metadata(self, mapping: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        reason = self._clean((mapping or {}).get("reason"))
        if not reason:
            return None
        try:
            payload = json.loads(reason)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        metadata = payload.get(self._MANAGED_LINE_REASON_KEY) if isinstance(payload, dict) else None
        return metadata if isinstance(metadata, dict) else None

    def _is_stock_inventory_bill(self, receipt: Dict[str, Any]) -> bool:
        """Return True only for explicitly classified inventory transactions."""
        receipt = receipt or {}
        receipt_type = self._normalize_receipt_name(
            receipt.get("name") or receipt.get("receipt_type")
        )
        return receipt_type in {
            "stockbill",
            "stockpurchasebill",
            "inventorybill",
            "stockdebitbill",
        } or bool(self._clean(receipt.get("stock_reference")))

    def _resolve_qbo_bill_expense_account(
        self,
        *,
        tenant_id: str,
        qbo_service_master_id: str,
        realm_id: str,
        receipt: Dict[str, Any],
    ) -> Tuple[str, str]:
        if self._is_stock_inventory_bill(receipt):
            inventory_account_name = getattr(const, "QBO_INVENTORY_ASSET_ACCOUNT_NAME", "Inventory Asset")
            inventory_account_map = self.coa_repo.get_external_id_for_account_name(
                tenant_id=tenant_id,
                name=inventory_account_name,
                service_id=str(qbo_service_master_id),
                external_acc_id=realm_id,
                allowed_account_types=getattr(
                    const,
                    "QBO_INVENTORY_ASSET_ACCOUNT_TYPES",
                    ("Other Current Asset", "OtherCurrentAsset"),
                ),
                allowed_detail_types=getattr(
                    const,
                    "QBO_INVENTORY_ASSET_DETAIL_TYPES",
                    ("Inventory",),
                ),
            )
            qbo_inventory_account_id = self._clean((inventory_account_map or {}).get("external_id"))
            if not qbo_inventory_account_id:
                raise Exception(
                    "Inventory Asset account hasn't been synced to QuickBooks yet. "
                    "Sync your Chart of Accounts first, then retry the stock bill export."
                )
            return qbo_inventory_account_id, inventory_account_name

        chart_of_account_id = receipt.get("chart_of_account_id")
        if chart_of_account_id is None:
            raise Exception("This bill is missing a chart of accounts assignment. Assign an account and try again.")

        coa_map = self.coa_repo.get_external_id_for_account_id(
            tenant_id=tenant_id,
            account_id=int(chart_of_account_id),
            service_id=str(qbo_service_master_id),
            external_acc_id=realm_id,
        )
        qbo_account_id = self._clean((coa_map or {}).get("external_id"))
        logger.debug(
            "resolved chart of account mapping | chart_of_account_id=%s qbo_account_id_present=%s",
            chart_of_account_id,
            bool(qbo_account_id),
        )
        if not qbo_account_id:
            coa_label = self._get_coa_label(tenant_id=tenant_id, account_id=chart_of_account_id)
            raise Exception(
                f"Account '{coa_label}' hasn't been synced to QuickBooks yet. "
                "Sync your Chart of Accounts first, then retry the bill export."
            )
        return qbo_account_id, self._get_coa_label(tenant_id=tenant_id, account_id=chart_of_account_id)


    def _build_account_based_bill_line(
        self,
        *,
        amount: float,
        qbo_account_id: str,
        qbo_project_ref: Optional[Dict[str, str]] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        detail: Dict[str, Any] = {"AccountRef": {"value": qbo_account_id}}
        line: Dict[str, Any] = {
            "Amount": amount,
            "DetailType": "AccountBasedExpenseLineDetail",
            "AccountBasedExpenseLineDetail": detail,
        }
        clean_description = self._clean(description)
        if clean_description:
            line["Description"] = clean_description[:4000]

        qbo_project_id = self._clean((qbo_project_ref or {}).get("id"))
        if not qbo_project_id:
            # Inventory/stock bills have no FIELDCRAFT project. Keep them account-only
            # so QuickBooks posts the cost under the selected COA without
            # customer/project/billable attribution.
            return line

        qbo_project_target = self.project_repo.normalize_project_sync_target(
            (qbo_project_ref or {}).get("target"),
            default=const.PROJECT_SYNC_TARGET_SUB_CUSTOMER,
        )
        if qbo_project_target == const.PROJECT_SYNC_TARGET_QBO_PROJECT:
            # Real QuickBooks Projects are linked at the bill line level.
            # Keep CustomerRef out of this flow because QuickBooks expects
            # ProjectRef for true project associations.
            line["ProjectRef"] = {"value": qbo_project_id}
            detail["BillableStatus"] = "Billable"
        else:
            # Legacy/current sub-customer flow uses CustomerRef on the line detail.
            detail["CustomerRef"] = {"value": qbo_project_id}
            detail["BillableStatus"] = "Billable"
        return line

    def _build_account_based_lines_from_receipt_items(
        self,
        *,
        receipt: Dict[str, Any],
        info: Dict[str, Any],
        qbo_account_id: str,
        qbo_project_ref: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        """Build QBO category/account lines from FIELDCRAFT receipt item details.

        These are AccountBasedExpenseLineDetail lines. We do not create
        QuickBooks product/service items here. Every receipt item is placed
        under the bill's already-selected COA mapping.
        """
        receipt_items = self._extract_receipt_items(receipt=receipt, info=info)
        lines: List[Dict[str, Any]] = []

        for item in receipt_items:
            amount = self._extract_money_amount(item.get("totalPrice") or item.get("amount") or item.get("Amount") or item.get("totalCost"))
            quantity = self._extract_money_amount(item.get("quantity") or item.get("Qty"))
            unit_price = self._extract_money_amount(item.get("price") or item.get("unitPrice") or item.get("UnitPrice") or item.get("unitCost") or item.get("rate"))
            if amount is None and quantity is not None and unit_price is not None:
                amount = round(quantity * unit_price, 2)
            if amount is None or amount == 0:
                continue
            if amount < 0:
                item_label = self._build_receipt_item_description(item) or "receipt item"
                raise ValueError(
                    f"Bill item '{item_label[:120]}' has a negative amount. "
                    "Use the Stock Debit or credit workflow instead of exporting a negative bill line."
                )

            description = self._build_receipt_item_description(item)
            line = self._build_account_based_bill_line(
                amount=float(amount),
                qbo_account_id=qbo_account_id,
                qbo_project_ref=qbo_project_ref,
            )
            if description:
                line["Description"] = description[:4000]
            lines.append(line)

        return lines

    @staticmethod
    def _money_decimal(value: Any) -> Decimal:
        try:
            return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid money value: {value!r}") from exc

    def _reconcile_bill_lines_to_total(
        self,
        *,
        lines: List[Dict[str, Any]],
        bill_total: Any,
        qbo_account_id: str,
        qbo_project_ref: Optional[Dict[str, str]],
    ) -> List[Dict[str, Any]]:
        """Ensure the exported QBO line total exactly matches the FIELDCRAFT bill total."""
        expected = self._money_decimal(bill_total)
        actual = sum(
            (self._money_decimal(line.get("Amount") or 0) for line in lines),
            Decimal("0.00"),
        )
        difference = (expected - actual).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if difference < Decimal("-0.01"):
            raise ValueError(
                f"The bill item total ({actual:.2f}) exceeds the bill total ({expected:.2f}). "
                "Correct the receipt items or total before syncing."
            )
        if difference > Decimal("0.01"):
            lines.append(
                self._build_account_based_bill_line(
                    amount=float(difference),
                    qbo_account_id=qbo_account_id,
                    qbo_project_ref=qbo_project_ref,
                    description="Bill total adjustment from FIELDCRAFT (tax, fees, or rounding).",
                )
            )
        elif difference != Decimal("0.00") and lines:
            corrected = self._money_decimal(lines[-1].get("Amount") or 0) + difference
            if corrected <= 0:
                raise ValueError(
                    "The bill line total cannot be reconciled to the FIELDCRAFT total without creating a non-positive line."
                )
            lines[-1]["Amount"] = float(corrected)
        return lines

    def _extract_receipt_items(self, *, receipt: Dict[str, Any], info: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return normal FIELDCRAFT/AI receipt item rows from receiptInfo or ai_result.

        Imported QBO receiptInfo can contain QBO AccountBasedExpenseLineDetail
        rows. Those are not split again, because they already represent QBO
        bill lines and not the original FIELDCRAFT item list.
        """
        if "receiptInfo" in info:
            items = self._parse_item_list(info.get("receiptInfo"))
            return [item for item in items if self._is_fieldcraft_receipt_item(item)]

        if "lineItems" in info:
            line_items = self._parse_item_list(info.get("lineItems"))
            return [item for item in line_items if self._is_fieldcraft_receipt_item(item)]

        ai_result = self._parse_jsonish(receipt.get("ai_result"))
        if isinstance(ai_result, dict):
            ai_items = self._parse_item_list(ai_result.get("items"))
            return [item for item in ai_items if self._is_fieldcraft_receipt_item(item)]
        return []

    # Shared parsing helpers. Kept as class attributes so existing call sites stay unchanged.
    _parse_item_list = staticmethod(parse_item_list)
    _parse_jsonish = staticmethod(parse_jsonish)
    _is_fieldcraft_receipt_item = staticmethod(is_fieldcraft_receipt_item)

    def _build_receipt_item_description(self, item: Dict[str, Any]) -> str:
        """Build a clear QBO line description from FIELDCRAFT material fields.

        QuickBooks account-based bill lines do not have a separate material name
        field, so the material name, description, and quantity are combined into
        the line Description field.
        """
        return build_receipt_item_description(item)

    def _build_receipt_items_summary_description(self, *, receipt: Dict[str, Any], info: Dict[str, Any]) -> str:
        """Build the QuickBooks bill-line description from all FIELDCRAFT materials."""
        candidates = self._extract_receipt_item_candidates(receipt=receipt, info=info)
        description = build_receipt_items_summary(candidates)
        if description:
            return description
        return self._clean(info.get("category") or receipt.get("description") or receipt.get("name"))

    def _extract_receipt_item_candidates(self, *, receipt: Dict[str, Any], info: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return receipt item dictionaries that have material identifying text."""
        if "receiptInfo" in info:
            return self._filter_descriptive_receipt_items(self._parse_item_list(info.get("receiptInfo")))
        if "lineItems" in info:
            return self._filter_descriptive_receipt_items(self._parse_item_list(info.get("lineItems")))

        ai_result = self._parse_jsonish(receipt.get("ai_result"))
        if isinstance(ai_result, dict):
            return self._filter_descriptive_receipt_items(self._parse_item_list(ai_result.get("items")))
        return []

    def _filter_descriptive_receipt_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            item
            for item in items
            if not item.get("DetailType")
            and bool(
                self._clean(
                    item.get("materialName")
                    or item.get("material_name")
                    or item.get("itemName")
                    or item.get("item_name")
                    or item.get("productName")
                    or item.get("product_name")
                    or item.get("name")
                    or item.get("service")
                    or item.get("description")
                    or item.get("productDescription")
                    or item.get("product_description")
                    or item.get("productCode")
                    or item.get("sku")
                    or item.get("itemCode")
                    or item.get("code")
                )
            )
        ]

    @staticmethod
    def _format_quantity(quantity: Optional[float]) -> str:
        if quantity is None:
            return ""
        if float(quantity).is_integer():
            return str(int(quantity))
        return f"{quantity:g}"

    _extract_money_amount = staticmethod(extract_money_amount)

    def _resolve_qbo_project_ref_for_receipt(
        self,
        *,
        tenant_id: str,
        service_id: str,
        realm_id: Optional[str] = None,
        receipt: Dict[str, Any],
    ) -> Optional[Dict[str, str]]:
        project_id = receipt.get("project_id")
        if project_id is None or self._clean(project_id).upper() == "NULL":
            if self._is_stock_inventory_bill(receipt):
                logger.debug(
                    "explicit stock receipt has no project link | receipt_id=%s stock_reference=%r",
                    receipt.get("id"),
                    receipt.get("stock_reference"),
                )
                return None
            raise ValueError(
                "This bill has no project and is not marked as a stock bill. "
                "Assign a project or change the receipt type to StockBill, then retry."
            )

        project_map = self.project_repo.get_current_mapping_by_project_id(
            tenant_id=tenant_id,
            service_id=service_id,
            project_id=int(project_id),
        )
        qbo_project_id = self._clean((project_map or {}).get("external_project_id"))
        if qbo_project_id and realm_id:
            self._assert_mapping_realm(project_map, realm_id, "project")

        if not qbo_project_id:
            project_label = self._get_project_label(tenant_id=tenant_id, project_id=project_id)
            raise Exception(
                f"Project '{project_label}' hasn't been synced to QuickBooks yet. "
                "Run project sync first, then retry the bill export."
            )
        # A project can be FAILED/SYNC_FAILED because of project sync validation,
        # but if it already has a QuickBooks id, bills can still be linked to it.
        target = self.project_repo.get_mapping_sync_target(project_map) or const.PROJECT_SYNC_TARGET_SUB_CUSTOMER
        if target not in const.PROJECT_SYNC_TARGETS_ALLOWED:
            target = const.PROJECT_SYNC_TARGET_SUB_CUSTOMER

        if target == const.PROJECT_SYNC_TARGET_QBO_PROJECT and not self._projects_available_for_sync(tenant_id=tenant_id, service_id=service_id):
            raise Exception(
                "QuickBooks Projects are not enabled for this company. "
                "This bill is linked to a QuickBooks Project, so it cannot be exported until Projects are enabled in QuickBooks. "
                "Choose Sub-customer for new projects, or enable Projects in QuickBooks and retry bill sync."
            )

        logger.debug(
            "resolved QuickBooks project reference for receipt | receipt_id=%s fieldcraft_project_id=%s qbo_project_id=%s target=%s",
            receipt.get("id"),
            project_id,
            qbo_project_id,
            target,
        )
        return {"id": qbo_project_id, "target": target}

    def _resolve_qbo_parent_customer_id_for_project(
        self, *, tenant_id: str, service_id: str, realm_id: str, project_id: Any
    ) -> str:
        project = self.project_repo.get_project_by_id(tenant_id=tenant_id, project_id=int(project_id)) or {}
        customer_uid = self._clean(project.get("customer_uid"))
        if not customer_uid:
            raise Exception(
                "This StockDebitBill project is synced as a QuickBooks Project, but the FIELDCRAFT project is not linked to a customer. "
                "Link the project to a customer, run customer sync, then retry."
            )
        customer_mapping = self.customer_repo.get_latest_qbo_linked_mapping_for_customer_uuid(
            tenant_id=tenant_id,
            service_id=service_id,
            customer_uuid=customer_uid,
            external_account_id=realm_id,
        ) or {}
        parent_customer_id = self._clean(customer_mapping.get("external_customer_id"))
        if not parent_customer_id:
            raise Exception(
                "This StockDebitBill project is synced as a QuickBooks Project, but the parent customer is not synced to QuickBooks. "
                "Run customer sync first, then retry."
            )
        logger.debug(
            "resolved QuickBooks parent customer for project | fieldcraft_project_id=%s customer_uid=%s qbo_parent_customer_id=%s customer_mapping_status=%s",
            project_id,
            customer_uid,
            parent_customer_id,
            customer_mapping.get("sync_status"),
        )
        return parent_customer_id

    def _projects_available_for_sync(self, *, tenant_id: str, service_id: str) -> bool:
        key = (str(tenant_id), str(service_id))
        if key not in self._projects_available_cache:
            self._projects_available_cache[key] = bool(
                self.capability_service.projects_available(tenant_id, service_id, refresh=True)
            )
        return self._projects_available_cache[key]

    def _get_project_label(self, *, tenant_id: str, project_id: Any) -> str:
        try:
            project = self.project_repo.get_project_by_id(tenant_id=tenant_id, project_id=int(project_id))
            return self._clean((project or {}).get("description") or (project or {}).get("order_no")) or str(project_id)
        except Exception:
            return str(project_id)

    def get_qbo_api_base(self, env: str) -> str:
        from source.Utils import QBODiscoveryClient
        return QBODiscoveryClient.get_qbo_api_base(env)

    def refresh_access_token(
        self,
        *,
        tenant_id: str,
        service_id: str,
        connection: Optional[Dict[str, Any]],
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> str:
        return QuickBooksOAuthClient(repository=self.qbo_repo).refresh_access_token(
            tenant_id=str(tenant_id),
            service_id=str(service_id),
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            token_url=self.qbo_service.token_url,
            current_connection=connection,
        )

    def get_bill_in_qbo(self, *, access_token: str, base_url: str, realm_id: str, bill_id: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/bill/{bill_id}"
        resp = qbo_err.qbo_get(url, headers=headers, timeout=30)
        qbo_err.raise_for_qbo_response(resp, fallback_message="QBO get bill failed")
        data = qbo_err.safe_json(resp)
        return data.get("Bill") or {}

    def find_bill_candidates_in_qbo(self, *, access_token: str, base_url: str, realm_id: str, receipt: Dict[str, Any]) -> List[Dict[str, Any]]:
        info = self.receipt_repo.parse_additional_info(receipt.get("receipt_additional_info"))
        doc_number = self.receipt_repo._pick_doc_number(receipt, info)
        if not self._clean(doc_number):
            return []
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        query_url = f"{base_url}/v3/company/{realm_id}/query"
        escaped_doc = escape_qbo_literal(doc_number)
        query = f"select * from Bill where DocNumber = '{escaped_doc}' STARTPOSITION 1 MAXRESULTS 10"
        resp = qbo_err.qbo_get(query_url, headers=headers, params={"query": query}, timeout=30)
        qbo_err.raise_for_qbo_response(resp, fallback_message="QBO bill candidate lookup failed")
        data = qbo_err.safe_json(resp)
        bills = (data.get("QueryResponse") or {}).get("Bill") or []
        return bills if isinstance(bills, list) else []

    def find_journal_entry_candidates_in_qbo(self, *, access_token: str, base_url: str, realm_id: str, doc_number: Optional[str]) -> List[Dict[str, Any]]:
        if not self._clean(doc_number):
            return []
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        query_url = f"{base_url}/v3/company/{realm_id}/query"
        escaped_doc = escape_qbo_literal(str(doc_number))
        query = f"select * from JournalEntry where DocNumber = '{escaped_doc}' STARTPOSITION 1 MAXRESULTS 10"
        resp = qbo_err.qbo_get(query_url, headers=headers, params={"query": query}, timeout=30)
        qbo_err.raise_for_qbo_response(resp, fallback_message="QBO journal entry candidate lookup failed")
        data = qbo_err.safe_json(resp)
        journal_entries = (data.get("QueryResponse") or {}).get("JournalEntry") or []
        return journal_entries if isinstance(journal_entries, list) else []

    def create_bill_in_qbo(self, *, access_token: str, base_url: str, realm_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/bill"
        resp = qbo_err.qbo_post(url, headers=headers, json=payload, timeout=30)
        qbo_err.raise_for_qbo_response(resp, success_statuses=(200, 201), fallback_message="QBO create bill failed")
        data = qbo_err.safe_json(resp)
        return data.get("Bill") or {}

    def get_journal_entry_in_qbo(self, *, access_token: str, base_url: str, realm_id: str, journal_entry_id: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/journalentry/{journal_entry_id}?minorversion=75"
        resp = qbo_err.qbo_get(url, headers=headers, timeout=30)
        qbo_err.raise_for_qbo_response(resp, fallback_message="QBO get journal entry failed")
        data = qbo_err.safe_json(resp)
        return data.get("JournalEntry") or {}

    def create_journal_entry_in_qbo(self, *, access_token: str, base_url: str, realm_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/journalentry?minorversion=75"
        logger.info(
            "QBO JournalEntry create request | doc_number=%s txn_date=%s payload_summary=%s",
            payload.get("DocNumber"),
            payload.get("TxnDate"),
            self._summarize_journal_entry_payload(payload),
        )
        resp = qbo_err.qbo_post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code not in (200, 201):
            logger.warning(
                "QBO JournalEntry create failed | status_code=%s intuit_tid=%s payload_summary=%s",
                resp.status_code,
                qbo_err.get_intuit_tid(resp),
                self._summarize_journal_entry_payload(payload),
            )
        qbo_err.raise_for_qbo_response(resp, success_statuses=(200, 201), fallback_message="QBO create journal entry failed")
        data = qbo_err.safe_json(resp)
        journal_entry = data.get("JournalEntry") or {}
        logger.info(
            "QBO JournalEntry create success | qbo_journal_entry_id=%s sync_token_present=%s doc_number=%s",
            journal_entry.get(const.QBO_KEY_ID),
            bool(journal_entry.get(const.QBO_KEY_SYNC_TOKEN)),
            payload.get("DocNumber"),
        )
        return journal_entry

    def update_journal_entry_in_qbo(self, *, access_token: str, base_url: str, realm_id: str, journal_entry_id: str, payload: Dict[str, Any], sync_token: Optional[str]) -> Dict[str, Any]:
        token_value = self._clean(sync_token)
        if token_value:
            try:
                return self._post_journal_entry_update(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=realm_id,
                    journal_entry_id=journal_entry_id,
                    sync_token=token_value,
                    payload=payload,
                )
            except Exception as exc:
                if self._is_qbo_stale_reference_error(str(exc)):
                    raise
                if not self._is_qbo_sync_token_error(str(exc)):
                    raise
        existing = self.get_journal_entry_in_qbo(
            access_token=access_token,
            base_url=base_url,
            realm_id=realm_id,
            journal_entry_id=journal_entry_id,
        )
        current_sync_token = self._clean(existing.get(const.QBO_KEY_SYNC_TOKEN))
        if not current_sync_token:
            raise Exception("QBO journal entry update failed: missing SyncToken")
        return self._post_journal_entry_update(
            access_token=access_token,
            base_url=base_url,
            realm_id=realm_id,
            journal_entry_id=journal_entry_id,
            sync_token=current_sync_token,
            payload=payload,
        )

    def _post_journal_entry_update(self, *, access_token: str, base_url: str, realm_id: str, journal_entry_id: str, sync_token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/journalentry?minorversion=75"
        body = {const.QBO_KEY_ID: str(journal_entry_id), const.QBO_KEY_SYNC_TOKEN: str(sync_token), "sparse": True, **(payload or {})}
        logger.info(
            "QBO JournalEntry update request | qbo_journal_entry_id=%s doc_number=%s payload_summary=%s",
            journal_entry_id,
            payload.get("DocNumber"),
            self._summarize_journal_entry_payload(payload),
        )
        resp = qbo_err.qbo_post(url, headers=headers, json=body, timeout=30)
        if resp.status_code not in (200, 201):
            logger.warning(
                "QBO JournalEntry update failed | qbo_journal_entry_id=%s status_code=%s intuit_tid=%s payload_summary=%s",
                journal_entry_id,
                resp.status_code,
                qbo_err.get_intuit_tid(resp),
                self._summarize_journal_entry_payload(payload),
            )
        qbo_err.raise_for_qbo_response(resp, success_statuses=(200, 201), fallback_message="QBO update journal entry failed")
        data = qbo_err.safe_json(resp)
        journal_entry = data.get("JournalEntry") or {}
        logger.info(
            "QBO JournalEntry update success | qbo_journal_entry_id=%s sync_token_present=%s doc_number=%s",
            journal_entry.get(const.QBO_KEY_ID) or journal_entry_id,
            bool(journal_entry.get(const.QBO_KEY_SYNC_TOKEN)),
            payload.get("DocNumber"),
        )
        return journal_entry

    def update_bill_in_qbo(self, *, access_token: str, base_url: str, realm_id: str, bill_id: str, payload: Dict[str, Any], sync_token: Optional[str]) -> Dict[str, Any]:
        token_value = self._clean(sync_token)
        if token_value:
            try:
                return self._post_bill_update(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=realm_id,
                    bill_id=bill_id,
                    sync_token=token_value,
                    payload=payload,
                )
            except Exception as exc:
                if self._is_qbo_stale_reference_error(str(exc)):
                    raise
                if not self._is_qbo_sync_token_error(str(exc)):
                    raise
        existing = self.get_bill_in_qbo(access_token=access_token, base_url=base_url, realm_id=realm_id, bill_id=bill_id)
        current_sync_token = self._clean(existing.get(const.QBO_KEY_SYNC_TOKEN))
        if not current_sync_token:
            raise Exception("QBO bill update failed: missing SyncToken")
        return self._post_bill_update(
            access_token=access_token,
            base_url=base_url,
            realm_id=realm_id,
            bill_id=bill_id,
            sync_token=current_sync_token,
            payload=payload,
        )

    def _post_bill_update(self, *, access_token: str, base_url: str, realm_id: str, bill_id: str, sync_token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/bill"
        body = {const.QBO_KEY_ID: str(bill_id), const.QBO_KEY_SYNC_TOKEN: str(sync_token), "sparse": True, **(payload or {})}
        resp = qbo_err.qbo_post(url, headers=headers, json=body, timeout=30)
        qbo_err.raise_for_qbo_response(resp, success_statuses=(200, 201), fallback_message="QBO update bill failed")
        data = qbo_err.safe_json(resp)
        return data.get("Bill") or {}

    def _try_attach_document(
        self,
        *,
        tenant_id: str,
        access_token: str,
        base_url: str,
        realm_id: str,
        bill_id: str,
        document_id: Optional[str],
    ) -> None:
        """Attach the FIELDCRAFT document to the QBO bill. Logs and swallows errors so attachment
        failures never block bill sync."""
        if not document_id:
            return
        try:
            self.attachment_service.attach_bill_document(
                tenant_id=tenant_id,
                access_token=access_token,
                base_url=base_url,
                realm_id=realm_id,
                bill_id=bill_id,
                document_id=str(document_id),
            )
            logger.info("attachment uploaded | qbo_bill_id=%s document_id=%s", bill_id, document_id)
        except Exception as exc:
            logger.warning("attachment upload skipped | qbo_bill_id=%s document_id=%s error=%s", bill_id, document_id, exc, exc_info=True)

    _clean = staticmethod(clean)

    def _get_vendor_label(self, *, tenant_id: str, vendor_id: Any) -> str:
        try:
            vendor = self.vendor_repo.get_current_vendor_by_id(tenant_id=tenant_id, vendor_id=int(vendor_id))
        except Exception:
            vendor = None
        if not isinstance(vendor, dict):
            vendor = {}

        for key in ("display_name", "company_name", "check_name"):
            label = self._clean(vendor.get(key))
            if label:
                return label
        return f"#{self._clean(vendor_id)}" if self._clean(vendor_id) else "Unknown"

    def _get_coa_label(self, *, tenant_id: str, account_id: Any) -> str:
        try:
            account = self.coa_repo.get_account_by_id(tenant_id=tenant_id, account_id=int(account_id))
        except Exception:
            account = None
        if not isinstance(account, dict):
            account = {}

        name = self._clean(account.get("name"))
        if name:
            return name
        account_type = self._clean(account.get("account_type"))
        detail_type = self._clean(account.get("detail_type"))
        if account_type and detail_type:
            return f"{account_type} / {detail_type}"
        if account_type:
            return account_type
        return f"#{self._clean(account_id)}" if self._clean(account_id) else "Unknown"

    def _escape_qbo_literal(self, value: str) -> str:
        return escape_qbo_literal(value)

    def _parse_timestamp(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            raw = str(value).strip().replace("Z", "+00:00")
            if not raw:
                return None
            try:
                dt = datetime.fromisoformat(raw)
            except ValueError:
                return None
        from datetime import timezone
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _get_local_receipt_updated_at(
        self,
        receipt: Optional[Dict[str, Any]],
        mapping: Optional[Dict[str, Any]] = None,
    ) -> Optional[datetime]:
        """Return a real local timestamp and ignore malformed repository values."""
        candidate = self.receipt_repo.get_receipt_latest_local_timestamp(receipt, mapping)
        if isinstance(candidate, datetime):
            return self._parse_timestamp(candidate)
        timestamps = []
        for row in (receipt or {}, mapping or {}):
            for key in ("updated_at", "endorsement_effective_date", "version_effective_date"):
                parsed = self._parse_timestamp(row.get(key))
                if parsed is not None:
                    timestamps.append(parsed)
        return max(timestamps) if timestamps else None

    def _get_qbo_last_updated_at(self, qbo_bill: Dict[str, Any]) -> Optional[datetime]:
        metadata = qbo_bill.get("MetaData") or {}
        return self._parse_timestamp(metadata.get("LastUpdatedTime") or metadata.get("CreateTime"))

    def _is_qbo_stale_reference_error(self, message: str) -> bool:
        text = self._clean(message).lower()
        return "[610]" in text or "object not found" in text or "made inactive" in text

    def _is_qbo_deleted_ref_error(self, message: str) -> bool:
        """Detects QBO errors where a referenced entity (vendor, account) was deleted in QBO."""
        text = self._clean(message).lower()
        return "has been deleted" in text or "invalid reference id" in text

    def _is_qbo_sync_token_error(self, message: str) -> bool:
        text = self._clean(message).lower()
        return (
            "synctoken" in text
            or "stale object" in text
            or "[5010]" in text
            or ("required field missing" in text and "synctoken" in text)
        )
