
from __future__ import annotations

import json
import logging
import mimetypes
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from source.dal.ChartOfAccountRepository import ChartOfAccountRepository
from source.dal.DocumentRepository import DocumentRepository
from source.dal.ProjectRepository import ProjectRepository
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.dal.ReceiptExternalMapRepository import ReceiptExternalMapRepository
from source.dal.ReceiptRepository import ReceiptRepository
from source.dal.ScheduledJobRepository import ScheduledJobRepository
from source.dal.VendorExternalMapRepository import VendorExternalMapRepository
from source.quickbooks.QBOBillAttachmentService import QBOBillAttachmentService
from source.service.QuickBooksService import QuickBooksService
from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils import QuickBooksErrorHandler as qbo_err
from source.Utils.QuickBooksErrorHandler import QuickBooksAPIError
from source.Utils import constants as const
from source.quickbooks.QuickBooksAuthentication import QuickBooksOAuthClient
from source.quickbooks.qbo_bill_utils import (
    build_receipt_items_summary,
    clean,
    description_fingerprint,
    extract_money_amount,
    managed_bill_line_baseline,
    parse_item_list,
    parse_jsonish,
)
from source.Utils.auditlog import safe_audit_log


logger = logging.getLogger(__name__)


class QuickBooksBillImportService:
    """QuickBooks -> FIELDCRAFT bill reconciliation using the redesigned receipt mapping model."""

    _MANAGED_LINE_REASON_KEY = "fieldcraftManagedBillLine"
    _QBO_IMPORTED_KEY = "_quickbooksImported"
    _QBO_LINE_ID_KEY = "_quickbooksLineId"

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
            data[QuickBooksBillImportService._MANAGED_LINE_REASON_KEY] = managed_line
        return json.dumps(data)

    _clean = staticmethod(clean)

    def __init__(self):
        self.qbo_repo = QuickBooksRepository()
        self.receipt_repo = ReceiptRepository()
        self.receipt_map_repo = ReceiptExternalMapRepository()
        self.vendor_map_repo = VendorExternalMapRepository()
        self.project_repo = ProjectRepository()
        self.coa_repo = ChartOfAccountRepository()
        self.qbo_service = QuickBooksService()
        self.scheduled_job_repo = ScheduledJobRepository()
        self.doc_repo = DocumentRepository()
        self.attachment_service = QBOBillAttachmentService()

    def _is_quickbooks_origin(self, receipt: Optional[Dict[str, Any]]) -> bool:
        return self._clean((receipt or {}).get("created_by")).casefold() == const.SERVICE_NAME_QUICKBOOKS.casefold()

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

    def _replace_payload_receipt_info(
        self,
        payload: Dict[str, Any],
        receipt_items: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        updated = dict(payload or {})
        raw_info = updated.get("receipt_additional_info")
        parsed = parse_jsonish(raw_info)
        list_shape = isinstance(parsed, list)
        if list_shape:
            info = dict(parsed[0]) if parsed and isinstance(parsed[0], dict) else {}
        elif isinstance(parsed, dict):
            info = dict(parsed)
        else:
            info = {}
        info["receiptInfo"] = json.dumps(receipt_items, default=str)
        updated["receipt_additional_info"] = json.dumps([info] if list_shape else info, default=str)
        return updated

    def _preserve_paid_fieldcraft_state(
        self,
        *,
        current_receipt: Optional[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Protect an already-paid FIELDCRAFT bill from being downgraded by Bill CDC.

        A FIELDCRAFT bill and its payment are synchronized as separate QuickBooks
        entities. Immediately after the Bill is created in QBO, its Balance can
        still be non-zero until the BillPayment is synchronized. Treating that
        transient Bill balance as authoritative would incorrectly flip a paid
        FIELDCRAFT bill back to unpaid.

        This guard is deliberately narrow:
        - it only applies to bills created in FIELDCRAFT;
        - it only protects a bill that is already paid in FIELDCRAFT;
        - unpaid -> paid updates from QuickBooks are left unchanged;
        - QuickBooks-originated bills keep the existing import behaviour.
        """
        if not current_receipt or self._is_quickbooks_origin(current_receipt):
            return payload

        current_info = self.receipt_repo.parse_additional_info(
            current_receipt.get("receipt_additional_info")
        )
        paid_value = current_info.get("paid")
        local_paid = (
            paid_value is True
            or self._clean(paid_value).casefold() == "true"
            or current_receipt.get("paid_date") is not None
        )
        if not local_paid:
            return payload

        updated = dict(payload or {})
        parsed = parse_jsonish(updated.get("receipt_additional_info"))
        list_shape = isinstance(parsed, list)
        if list_shape:
            info = dict(parsed[0]) if parsed and isinstance(parsed[0], dict) else {}
        elif isinstance(parsed, dict):
            info = dict(parsed)
        else:
            info = {}

        info["paid"] = True
        for field in ("paymentMethod", "paymentType", "billPaymentDate", "paymentDate"):
            if field in current_info:
                info[field] = current_info.get(field)

        updated["receipt_additional_info"] = json.dumps(
            [info] if list_shape else info,
            default=str,
        )

        # Keep the existing FIELDCRAFT payment state/date. BillPayment synchronization
        # remains responsible for payment-specific updates.
        if current_receipt.get("status") is not None:
            updated["status"] = current_receipt.get("status")
        if current_receipt.get("paid_date") is not None:
            updated["paid_date"] = current_receipt.get("paid_date")
        return updated

    def _find_managed_qbo_line(
        self,
        *,
        qbo_bill: Dict[str, Any],
        metadata: Dict[str, Any],
        preferred_fingerprint: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        lines = [line for line in (qbo_bill.get("Line") or []) if isinstance(line, dict)]
        fingerprints = []
        for fingerprint in (
            preferred_fingerprint,
            self._clean(metadata.get("descriptionFingerprint")),
        ):
            fingerprint = self._clean(fingerprint)
            if fingerprint and fingerprint not in fingerprints:
                fingerprints.append(fingerprint)

        # Description is the safest discriminator because the managed FIELDCRAFT
        # transport line is generated deterministically from the FIELDCRAFT items.
        # Use the stored line Id only as a fallback; QBO line ids can become
        # stale after a user restructures the bill in QuickBooks.
        for expected_fingerprint in fingerprints:
            for line in lines:
                if description_fingerprint(line.get("Description")) == expected_fingerprint:
                    return line

        managed_id = self._clean(metadata.get("id"))
        if managed_id:
            for line in lines:
                if self._clean(line.get("Id")) == managed_id:
                    return line
        return None

    def _infer_managed_line_metadata(
        self,
        *,
        current_receipt: Dict[str, Any],
        qbo_bill: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Recognize an older FIELDCRAFT summary line that predates managed-line metadata."""
        current_info = self.receipt_repo.parse_additional_info(current_receipt.get("receipt_additional_info"))
        current_items = parse_item_list(current_info.get("receiptInfo"))
        if not current_items:
            return None

        expected_description = build_receipt_items_summary(current_items)[:4000]
        expected_fingerprint = description_fingerprint(expected_description)
        if not expected_fingerprint:
            return None

        for line in qbo_bill.get("Line") or []:
            if not isinstance(line, dict):
                continue
            if description_fingerprint(line.get("Description")) != expected_fingerprint:
                continue
            metadata: Dict[str, Any] = {
                "amount": extract_money_amount(current_info.get("total")),
                "descriptionFingerprint": expected_fingerprint,
                "itemCount": len(current_items),
                "lineBaseline": managed_bill_line_baseline(line),
            }
            line_id = self._clean(line.get("Id"))
            if line_id:
                metadata["id"] = line_id
            return metadata
        return None

    def _normalize_qbo_line_for_fieldcraft(self, line: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        amount = extract_money_amount(line.get("Amount"))
        if amount is None or abs(amount) < 0.000001:
            return None

        detail_type = self._clean(line.get("DetailType"))
        description = self._clean(line.get("Description"))
        item: Dict[str, Any] = {
            "description": description or "QuickBooks bill line",
            "totalPrice": amount,
            self._QBO_IMPORTED_KEY: True,
        }
        line_id = self._clean(line.get("Id"))
        if line_id:
            item[self._QBO_LINE_ID_KEY] = line_id

        if detail_type == "ItemBasedExpenseLineDetail":
            detail = line.get("ItemBasedExpenseLineDetail") or {}
            item_ref = detail.get("ItemRef") or {}
            item_name = self._clean(item_ref.get("name"))
            if not description and item_name:
                item["description"] = item_name
            if description and item_name and description != item_name:
                item["productCode"] = item_name
            quantity = extract_money_amount(detail.get("Qty"))
            unit_price = extract_money_amount(detail.get("UnitPrice"))
            if quantity is not None:
                item["quantity"] = quantity
            if unit_price is not None:
                item["price"] = unit_price
        return item

    def _get_managed_local_items(
        self,
        *,
        current_items: List[Dict[str, Any]],
        managed_line: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Return local rows represented by the exported managed QBO line.

        itemCount is captured at export time, after the local receipt items have
        been ordered. Imported QBO rows are always appended after the managed
        local rows, so the count remains a stable boundary until the next FIELDCRAFT
        export refreshes the metadata.
        """
        candidates = [
            dict(item)
            for item in current_items
            if not item.get("DetailType") and not bool(item.get("_quickbooksSynthetic"))
        ]
        try:
            item_count = int(managed_line.get("itemCount") or 0)
        except (TypeError, ValueError):
            item_count = 0

        if item_count > 0 and item_count <= len(candidates):
            return candidates[:item_count]

        # Backward compatibility for mappings created before itemCount was
        # recorded. Preserve normal local rows and rebuild QBO-added rows from
        # the latest QuickBooks payload.
        return [
            item
            for item in candidates
            if not bool(item.get(self._QBO_IMPORTED_KEY))
        ] or candidates

    def _managed_line_change_fields(
        self,
        *,
        metadata: Dict[str, Any],
        managed_qbo_line: Optional[Dict[str, Any]],
    ) -> List[str]:
        """Return protected summarized-line fields changed in QuickBooks.

        The summarized line is an integration-owned transport row. Separate
        QuickBooks lines remain fully two-way, but this line itself must stay
        unchanged because it represents multiple detailed FIELDCRAFT material rows.
        """
        if managed_qbo_line is None:
            return ["line removed"]

        changes: List[str] = []
        baseline_amount = extract_money_amount(metadata.get("amount"))
        current_amount = extract_money_amount(managed_qbo_line.get("Amount"))
        if baseline_amount is not None:
            if current_amount is None or abs(current_amount - baseline_amount) >= 0.005:
                changes.append("amount")

        baseline_description = self._clean(metadata.get("descriptionFingerprint"))
        if baseline_description:
            current_description = description_fingerprint(managed_qbo_line.get("Description"))
            if current_description != baseline_description:
                changes.append("description")

        baseline_line = metadata.get("lineBaseline")
        if isinstance(baseline_line, dict) and baseline_line:
            current_line = managed_bill_line_baseline(managed_qbo_line)
            labels = {
                "detailType": "line type",
                "accountRef": "account",
                "customerRef": "customer/project",
                "projectRef": "project",
                "billableStatus": "billable status",
            }
            for key, expected in baseline_line.items():
                if current_line.get(key) != expected:
                    label = labels.get(key, key)
                    if label not in changes:
                        changes.append(label)

        return changes

    def _validate_managed_summary_line(
        self,
        *,
        current_receipt: Optional[Dict[str, Any]],
        current_mapping: Optional[Dict[str, Any]],
        qbo_bill: Dict[str, Any],
    ) -> Tuple[Optional[Dict[str, Any]], List[str]]:
        """Validate that FIELDCRAFT's summarized QuickBooks line was not edited.

        Returns the clean baseline metadata and a list of changed fields. An
        empty change list means the normal two-way line-item import may proceed.
        """
        if not current_receipt or self._is_quickbooks_origin(current_receipt):
            return None, []

        managed_line = self._get_managed_line_metadata(current_mapping)
        if not managed_line:
            managed_line = self._infer_managed_line_metadata(
                current_receipt=current_receipt,
                qbo_bill=qbo_bill,
            )
        if not managed_line:
            return None, []

        baseline = dict(managed_line)
        baseline.pop("qboCurrent", None)

        current_info = self.receipt_repo.parse_additional_info(
            current_receipt.get("receipt_additional_info")
        )
        current_items = parse_item_list(current_info.get("receiptInfo"))
        managed_items = self._get_managed_local_items(
            current_items=current_items,
            managed_line=baseline,
        )
        current_summary_fingerprint = description_fingerprint(
            build_receipt_items_summary(managed_items)[:4000]
        )
        managed_qbo_line = self._find_managed_qbo_line(
            qbo_bill=qbo_bill,
            metadata=baseline,
            preferred_fingerprint=current_summary_fingerprint,
        )
        return baseline, self._managed_line_change_fields(
            metadata=baseline,
            managed_qbo_line=managed_qbo_line,
        )

    def _prepare_mapped_fieldcraft_payload(
        self,
        *,
        current_receipt: Optional[Dict[str, Any]],
        current_mapping: Optional[Dict[str, Any]],
        qbo_bill: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """Preserve FIELDCRAFT material rows while importing real QBO bill-line changes.

        FIELDCRAFT exports its current local bill rows as one managed QBO account line.
        That transport line never replaces the stored FIELDCRAFT rows. Lines users add
        separately in QuickBooks remain normal two-way updates and are rebuilt
        from the latest QBO bill on every import.
        """
        if not current_receipt or self._is_quickbooks_origin(current_receipt):
            return payload, None

        payload = self._preserve_paid_fieldcraft_state(
            current_receipt=current_receipt,
            payload=payload,
        )

        managed_line = self._get_managed_line_metadata(current_mapping)
        if not managed_line:
            managed_line = self._infer_managed_line_metadata(
                current_receipt=current_receipt,
                qbo_bill=qbo_bill,
            )
        if not managed_line:
            # This mapped bill was not created by the summarized FIELDCRAFT export flow.
            # Keep the original two-way import behaviour unchanged.
            return payload, None
        managed_line = dict(managed_line)
        managed_line.pop("qboCurrent", None)

        current_info = self.receipt_repo.parse_additional_info(current_receipt.get("receipt_additional_info"))
        current_items = parse_item_list(current_info.get("receiptInfo"))
        managed_items = self._get_managed_local_items(
            current_items=current_items,
            managed_line=managed_line,
        )

        current_summary_fingerprint = description_fingerprint(
            build_receipt_items_summary(managed_items)[:4000]
        )
        managed_qbo_line = self._find_managed_qbo_line(
            qbo_bill=qbo_bill,
            metadata=managed_line,
            preferred_fingerprint=current_summary_fingerprint,
        )
        managed_qbo_id = self._clean((managed_qbo_line or {}).get("Id"))

        extra_items: List[Dict[str, Any]] = []
        for line in qbo_bill.get("Line") or []:
            if not isinstance(line, dict):
                continue
            if managed_qbo_line is line:
                continue
            if managed_qbo_id and self._clean(line.get("Id")) == managed_qbo_id:
                continue
            normalized = self._normalize_qbo_line_for_fieldcraft(line)
            if normalized:
                extra_items.append(normalized)

        logger.debug(
            "FIELDCRAFT managed bill merge | qbo_bill_id=%s qbo_lines=%s managed_line_id=%s "
            "managed_local_items=%s imported_qbo_lines=%s total=%s",
            self._clean(qbo_bill.get("Id")) or None,
            len([line for line in (qbo_bill.get("Line") or []) if isinstance(line, dict)]),
            managed_qbo_id or None,
            len(managed_items),
            len(extra_items),
            qbo_bill.get("TotalAmt"),
        )

        # The managed summary is validated before this method is called. Only
        # genuine separate QuickBooks lines are merged into receiptInfo.
        merged_items = managed_items + extra_items
        return self._replace_payload_receipt_info(payload, merged_items), managed_line

    def _refresh_managed_fieldcraft_bill_if_needed(
        self,
        *,
        current_mapping: Optional[Dict[str, Any]],
        qbo_bill: Dict[str, Any],
        tenant_id: str,
        access_token: str,
        base_url: str,
        realm_id: str,
    ) -> Dict[str, Any]:
        """Refresh a mapped FIELDCRAFT bill when the received line payload looks incomplete.

        CDC normally returns the full entity, but the managed-summary merge is
        intentionally conservative because losing a user-added QBO line would
        make the FIELDCRAFT total and item view disagree. A targeted read by Id is
        therefore used only when the bill total changed while the received
        lines do not explain that total.
        """
        if not current_mapping or not current_mapping.get("receipt_id"):
            return qbo_bill

        current_receipt = self.receipt_repo.get_current_receipt_by_id(
            tenant_id,
            current_mapping["receipt_id"],
        )
        if not current_receipt or self._is_quickbooks_origin(current_receipt):
            return qbo_bill

        managed_line = self._get_managed_line_metadata(current_mapping)
        if not managed_line:
            managed_line = self._infer_managed_line_metadata(
                current_receipt=current_receipt,
                qbo_bill=qbo_bill,
            )
        if not managed_line:
            return qbo_bill

        total = extract_money_amount(qbo_bill.get("TotalAmt"))
        lines = [line for line in (qbo_bill.get("Line") or []) if isinstance(line, dict)]
        line_total = sum(
            extract_money_amount(line.get("Amount")) or 0.0
            for line in lines
        )
        current_info = self.receipt_repo.parse_additional_info(
            current_receipt.get("receipt_additional_info")
        )
        local_total = extract_money_amount(current_info.get("total"))

        total_not_explained = total is not None and abs(total - line_total) >= 0.005
        changed_total_with_single_line = (
            total is not None
            and local_total is not None
            and abs(total - local_total) >= 0.005
            and len(lines) <= 1
        )
        if not total_not_explained and not changed_total_with_single_line:
            return qbo_bill

        bill_id = self._clean(qbo_bill.get(const.QBO_KEY_ID))
        if not bill_id:
            return qbo_bill
        try:
            refreshed = self._fetch_qbo_bill_by_id(
                access_token=access_token,
                base_url=base_url,
                realm_id=realm_id,
                bill_id=bill_id,
            )
        except Exception:
            logger.warning(
                "Unable to refresh managed FIELDCRAFT bill before line merge | qbo_bill_id=%s",
                bill_id,
                exc_info=True,
            )
            return qbo_bill

        logger.debug(
            "Refreshed managed FIELDCRAFT bill before line merge | qbo_bill_id=%s received_lines=%s refreshed_lines=%s",
            bill_id,
            len(lines),
            len([line for line in (refreshed.get("Line") or []) if isinstance(line, dict)]),
        )
        return refreshed or qbo_bill

    def _managed_receipt_info_changed(self, current_receipt: Dict[str, Any], payload: Dict[str, Any]) -> bool:
        current_info = self.receipt_repo.parse_additional_info(current_receipt.get("receipt_additional_info"))
        payload_info = self.receipt_repo.parse_additional_info(payload.get("receipt_additional_info"))
        current_items = parse_item_list(current_info.get("receiptInfo"))
        payload_items = parse_item_list(payload_info.get("receiptInfo"))
        return json.dumps(current_items, sort_keys=True, default=str) != json.dumps(payload_items, sort_keys=True, default=str)

    def _assert_mapping_realm(self, mapping: Optional[Dict[str, Any]], realm_id: str, label: str) -> None:
        if not isinstance(mapping, dict):
            return
        mapped_realm = self._clean(mapping.get("external_account_id"))
        if mapped_realm and mapped_realm != self._clean(realm_id):
            raise ValueError(
                f"The mapped {label} belongs to a different QuickBooks company. Re-sync it and retry."
            )

    def import_bills(self, *, tenant_id: str, service_id: str, page_size: int = 100, start_position: int = 1, max_bills: Optional[int] = None, dry_run: bool = False, changed_since_override: Optional[Any] = None, force_full: bool = False, object_id: Optional[str] = None, email: Optional[str] = None, user_role: Optional[str] = None) -> Dict[str, Any]:
        safe_audit_log(
            tenant_id=tenant_id,
            action="bill_import_started",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="started",
            resource_type="quickbooks_bill",
        )
        # Track final outcome so the finally block logs it regardless of which exit path is taken.
        _audit_action = "bill_import_failed"
        _audit_status = "failed"
        _audit_context: Optional[str] = None
        summary: Dict[str, Any] = {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": str(service_id),
            "processed": 0,
            "linkedExistingInFieldCraft": 0,
            "created in FIELDCRAFT": 0,
            "updatedInFIELDCRAFT": 0,
            "skippedAlreadyCurrent": 0,
            "skippedLocalNewer": 0,
            "skippedDeleted": 0,
            "blockedManagedSummary": 0,
            "failed": 0,
            const.KEY_FAILURES: [],
            const.KEY_RESULTS: [],
            "startedAt": datetime.now(timezone.utc).isoformat(),
        }
        try:
            logger.debug(f"[BillImport] import_bills called | tenant={tenant_id} service_id={service_id} force_full={force_full} changed_since_override={changed_since_override!r} dry_run={dry_run}")
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = (connection.get(const.KEY_ENV) or "sandbox").lower()
            conn_status = (connection.get(const.KEY_STATUS) or const.STATUS_CONNECTED).lower()
            if conn_status and conn_status != const.STATUS_CONNECTED:
                _audit_action, _audit_context = "bill_import_aborted", f"not_connected status={conn_status}"
                return {const.KEY_OK: False, const.KEY_MESSAGE: f"QuickBooks is not connected (status={conn_status})"}
            if not refresh_token or not realm_id:
                _audit_action, _audit_context = "bill_import_aborted", "missing_realmId_or_refreshToken"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks not connected (missing refreshToken/realmId)."}

            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)
            if not client_id or not client_secret:
                _audit_action, _audit_context = "bill_import_aborted", "missing_credentials"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks client credentials missing (ClientId/ClientSecret)."}

            access_token = self._refresh_access_token(
                tenant_id=tenant_id,
                service_id=service_id,
                connection=connection,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
            )
            base_url = self._get_qbo_api_base(env)
            changed_since, watermark_source = self._resolve_changed_since(tenant_id=tenant_id, service_id=service_id, changed_since_override=changed_since_override, force_full=force_full)
            use_cdc = changed_since is not None
            if use_cdc and self._is_cdc_too_old(changed_since):
                use_cdc = False
                watermark_source = None
                changed_since = None
            summary["mode"] = "CDC" if use_cdc else "FULL"
            summary["changedSince"] = changed_since.isoformat() if changed_since else None
            summary["watermarkSource"] = watermark_source
            logger.debug(f"[BillImport] import_bills started | tenant={tenant_id} service={service_id} realmId_present={bool(realm_id)} mode={summary['mode']} changedSince={summary['changedSince']} watermark={watermark_source}")

            pending_import_failed_ids = self.receipt_map_repo.get_open_import_failed_external_receipt_ids(tenant_id=tenant_id, service_id=service_id)
            logger.debug(f"[BillImport] persistentRetry candidates={len(pending_import_failed_ids)} ids={pending_import_failed_ids}")
            if pending_import_failed_ids:
                summary["persistentRetry"] = self.import_bills_by_ids(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    bill_ids=pending_import_failed_ids,
                )

            if use_cdc:
                qbo_bills, intuit_tid = self._fetch_qbo_bills_cdc(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=str(realm_id),
                    changed_since=changed_since,
                )
                if max_bills is not None:
                    qbo_bills = qbo_bills[: int(max_bills)]
                summary["intuitTid"] = intuit_tid
                logger.debug(f"[BillImport] CDC fetched {len(qbo_bills)} bill(s) intuitTid={intuit_tid}")
                for qbo_bill in qbo_bills:
                    summary["processed"] += 1
                    self._process_qbo_bill(summary=summary, tenant_id=tenant_id, service_id=service_id, realm_id=str(realm_id), qbo_bill=qbo_bill, dry_run=dry_run, access_token=access_token, base_url=base_url)
                _audit_action = "bill_import_completed"
                _audit_status = "success"
                _audit_context = f"exported={summary.get('created in FIELDCRAFT', 0) + summary.get('updatedInFIELDCRAFT', 0)} failed={summary.get('failed', 0)}"
                logger.debug(f"[BillImport] CDC done | processed={summary['processed']} created={summary.get('created in FIELDCRAFT', 0)} updated={summary.get('updatedInFIELDCRAFT', 0)} linked={summary.get('linkedExistingInFieldCraft', 0)} skippedCurrent={summary.get('skippedAlreadyCurrent', 0)} failed={summary['failed']}")
                return summary

            pos = max(1, int(start_position or 1))
            processed = 0
            while True:
                if max_bills is not None and processed >= int(max_bills):
                    break
                batch_size = int(page_size or 100)
                if max_bills is not None:
                    batch_size = min(batch_size, int(max_bills) - processed)
                qbo_bills, intuit_tid = self._query_qbo_bills(base_url=base_url, realm_id=str(realm_id), access_token=access_token, start_position=pos, max_results=batch_size)
                if not qbo_bills:
                    break
                summary["intuitTid"] = intuit_tid
                logger.debug(f"[BillImport] FULL query pos={pos} fetched {len(qbo_bills)} bill(s) intuitTid={intuit_tid}")
                for qbo_bill in qbo_bills:
                    processed += 1
                    summary["processed"] += 1
                    logger.debug(
                        "Bill import record fetched | qbo_bill_id=%s has_lines=%s",
                        qbo_bill.get(const.QBO_KEY_ID), bool(qbo_bill.get("Line")),
                    )
                    self._process_qbo_bill(summary=summary, tenant_id=tenant_id, service_id=service_id, realm_id=str(realm_id), qbo_bill=qbo_bill, dry_run=dry_run, access_token=access_token, base_url=base_url)
                pos += len(qbo_bills)
                if len(qbo_bills) < batch_size:
                    break
            _audit_action = "bill_import_completed"
            _audit_status = "success"
            _audit_context = f"exported={summary.get('created in FIELDCRAFT', 0) + summary.get('updatedInFIELDCRAFT', 0)} failed={summary.get('failed', 0)}"
            logger.debug(f"[BillImport] FULL done | processed={summary['processed']} created={summary.get('created in FIELDCRAFT', 0)} updated={summary.get('updatedInFIELDCRAFT', 0)} linked={summary.get('linkedExistingInFieldCraft', 0)} skippedCurrent={summary.get('skippedAlreadyCurrent', 0)} failed={summary['failed']}")
            return summary
        except Exception as e:
            logger.debug(f"[BillImport] EXCEPTION in import_bills: {e}")
            logger.exception("QuickBooks bill import batch failed | tenant_id=%s service_id=%s", tenant_id, service_id)
            summary[const.KEY_OK] = False
            summary[const.KEY_ERROR] = "Unexpected QuickBooks sync error."
            _audit_action = "bill_import_failed"
            _audit_status = "failed"
            _audit_context = str(e)[:64]
            return summary
        finally:
            summary["finishedAt"] = datetime.now(timezone.utc).isoformat()
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

    def import_bills_by_ids(self, *, tenant_id: str, service_id: str, bill_ids: List[str]) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": str(service_id),
            "processed": 0,
            "linkedExistingInFieldCraft": 0,
            "created in FIELDCRAFT": 0,
            "updatedInFIELDCRAFT": 0,
            "skippedAlreadyCurrent": 0,
            "skippedLocalNewer": 0,
            "skippedDeleted": 0,
            "blockedManagedSummary": 0,
            "failed": 0,
            const.KEY_FAILURES: [],
            const.KEY_RESULTS: [],
            "startedAt": datetime.now(timezone.utc).isoformat(),
        }
        try:
            logger.debug(f"[BillImport] import_bills_by_ids | tenant={tenant_id} service_id={service_id} bill_ids={bill_ids}")
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = (connection.get(const.KEY_ENV) or "sandbox").lower()
            if not refresh_token or not realm_id:
                logger.debug(f"[BillImport] import_bills_by_ids ABORTED — missing refreshToken/realmId")
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks not connected (missing refreshToken/realmId)."}
            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)
            if not client_id or not client_secret:
                logger.debug(f"[BillImport] import_bills_by_ids ABORTED — missing client credentials")
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks client credentials missing (ClientId/ClientSecret)."}
            access_token = self._refresh_access_token(
                tenant_id=tenant_id,
                service_id=service_id,
                connection=connection,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
            )
            base_url = self._get_qbo_api_base(env)
            for bill_id in bill_ids or []:
                logger.debug(f"[BillImport] import_bills_by_ids fetching bill_id={bill_id!r}")
                qbo_bill = self._fetch_qbo_bill_by_id(access_token=access_token, base_url=base_url, realm_id=str(realm_id), bill_id=str(bill_id))
                logger.debug(f"[BillImport] import_bills_by_ids fetched bill_id={bill_id!r} → Id={(qbo_bill or {}).get('Id')!r} TxnDate={(qbo_bill or {}).get('TxnDate')} TotalAmt={(qbo_bill or {}).get('TotalAmt')}")
                summary["processed"] += 1
                self._process_qbo_bill(summary=summary, tenant_id=tenant_id, service_id=service_id, realm_id=str(realm_id), qbo_bill=qbo_bill, dry_run=False, access_token=access_token, base_url=base_url)
            logger.debug(f"[BillImport] import_bills_by_ids done | processed={summary['processed']} failed={summary['failed']}")
            return summary
        except Exception:
            logger.exception("QuickBooks bill import-by-id batch failed | tenant_id=%s service_id=%s", tenant_id, service_id)
            summary[const.KEY_OK] = False
            summary[const.KEY_ERROR] = "Unexpected QuickBooks sync error."
            return summary
        finally:
            summary["finishedAt"] = datetime.now(timezone.utc).isoformat()

    def _process_qbo_bill(self, *, summary: Dict[str, Any], tenant_id: str, service_id: str, realm_id: str, qbo_bill: Dict[str, Any], dry_run: bool, access_token: str, base_url: str) -> None:
        qbo_bill_id = str(qbo_bill.get(const.QBO_KEY_ID) or "").strip()
        doc_number = str(qbo_bill.get("DocNumber") or "").strip()
        private_note = str(qbo_bill.get("PrivateNote") or "").strip()
        logger.debug(f"[BillImport] ── processing QBO Bill id={qbo_bill_id} DocNumber={doc_number!r} TxnDate={qbo_bill.get('TxnDate')} TotalAmt={qbo_bill.get('TotalAmt')}")
        detail: Dict[str, Any] = {
            const.KEY_QBO_BILL_ID: qbo_bill_id,
            "displayName": doc_number or f"Bill {qbo_bill_id}",
            const.KEY_STATUS: None,
            const.KEY_ACTION: None,
            const.KEY_FIELDCRAFT_BILL_ID: None,
            const.KEY_REASON: None,
            const.KEY_ERROR: None,
        }
        try:
            if not qbo_bill_id:
                raise ValueError("QuickBooks bill is missing its Id")
            current_mapping = self.receipt_map_repo.get_by_external_id(tenant_id, service_id, qbo_bill_id)
            self._assert_mapping_realm(current_mapping, realm_id, "bill")
            current_mapping_is_import_failed = bool(current_mapping and current_mapping.get("sync_status") == self.receipt_repo.STATUS_IMPORT_FAILED)
            logger.debug(
                "Bill mapping lookup | qbo_bill_id=%s mapping_id=%s status=%s",
                qbo_bill_id, (current_mapping or {}).get("id"), (current_mapping or {}).get("sync_status"),
            )
            if self._is_deleted_qbo_bill(qbo_bill):
                logger.debug(f"[BillImport]   → SKIP deleted bill id={qbo_bill_id}")
                if current_mapping_is_import_failed and current_mapping.get("id"):
                    self.receipt_map_repo.close_mapping_as_invalid(tenant_id, current_mapping["id"])
                summary["skippedDeleted"] += 1
                detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "deletedInQbo", const.KEY_REASON: self._build_reason("This bill was deleted in QuickBooks and has been skipped.")})
                summary[const.KEY_RESULTS].append(detail)
                return

            qbo_bill = self._refresh_managed_fieldcraft_bill_if_needed(
                current_mapping=current_mapping,
                qbo_bill=qbo_bill,
                tenant_id=tenant_id,
                access_token=access_token,
                base_url=base_url,
                realm_id=realm_id,
            )
            doc_number = str(qbo_bill.get("DocNumber") or "").strip()
            private_note = str(qbo_bill.get("PrivateNote") or "").strip()
            detail["displayName"] = doc_number or f"Bill {qbo_bill_id}"

            mapped_receipt_for_guard = None
            if current_mapping and current_mapping.get("receipt_id"):
                mapped_receipt_for_guard = self.receipt_repo.get_current_receipt_by_id(
                    tenant_id,
                    current_mapping["receipt_id"],
                )

            managed_line_meta, managed_line_changes = self._validate_managed_summary_line(
                current_receipt=mapped_receipt_for_guard,
                current_mapping=current_mapping,
                qbo_bill=qbo_bill,
            )
            if managed_line_changes:
                changed_fields = ", ".join(managed_line_changes)
                reason = (
                    "This bill was not imported because the FIELDCRAFT-managed summarized line was modified "
                    f"in QuickBooks ({changed_fields}). Restore that summarized line to the values "
                    "exported by FIELDCRAFT, or leave it unchanged and add bill changes as separate "
                    "QuickBooks line items."
                )
                logger.warning(
                    "Blocked QuickBooks bill import because managed summary changed | "
                    "qbo_bill_id=%s receipt_id=%s changes=%s",
                    qbo_bill_id,
                    (current_mapping or {}).get("receipt_id"),
                    managed_line_changes,
                )
                if not dry_run and current_mapping and current_mapping.get("id"):
                    current_status = self._clean(current_mapping.get("sync_status")).upper()
                    pending_statuses = {
                        self.receipt_repo.STATUS_PENDING_PUSH,
                        self.receipt_repo.STATUS_FAILED,
                        self.receipt_repo.STATUS_SYNC_FAILED,
                    }
                    blocked_status = (
                        current_status
                        if current_status in pending_statuses
                        else self.receipt_repo.STATUS_SYNC_FAILED
                    )
                    self.receipt_map_repo.mark_mapping_synced(
                        tenant_id=tenant_id,
                        mapping_id=current_mapping["id"],
                        external_receipt_id=qbo_bill_id,
                        external_account_id=realm_id,
                        sync_token=str(qbo_bill.get(const.QBO_KEY_SYNC_TOKEN) or "") or None,
                        reason=self._build_reason(reason, managed_line=managed_line_meta),
                        sync_status=blocked_status,
                    )
                summary["blockedManagedSummary"] += 1
                summary["failed"] += 1
                qbo_api.append_failure(
                    summary,
                    {
                        const.KEY_QBO_BILL_ID: qbo_bill_id,
                        const.KEY_ERROR: reason,
                        const.KEY_DETAILS: reason,
                    },
                )
                detail.update({
                    const.KEY_STATUS: "failed",
                    const.KEY_ACTION: "managedSummaryModified",
                    const.KEY_FIELDCRAFT_BILL_ID: (current_mapping or {}).get("receipt_id"),
                    const.KEY_ERROR: reason,
                    const.KEY_REASON: self._build_reason(reason, managed_line=managed_line_meta),
                })
                summary[const.KEY_RESULTS].append(detail)
                return

            vendor_ref = qbo_bill.get("VendorRef") or {}
            qbo_vendor_id = str((vendor_ref or {}).get("value") or "").strip()
            qbo_vendor_name = str(vendor_ref.get("name") or "").strip() or None
            vendor_mapping = self.vendor_map_repo.get_by_external_id(tenant_id, service_id, qbo_vendor_id) if qbo_vendor_id else None
            self._assert_mapping_realm(vendor_mapping, realm_id, "vendor")
            local_vendor_id = vendor_mapping.get("vendor_id") if vendor_mapping else None
            logger.debug(
                "Bill vendor mapping lookup | qbo_vendor_present=%s local_vendor_present=%s",
                bool(qbo_vendor_id), local_vendor_id is not None,
            )

            account_ref_value = self._extract_qbo_account_id(qbo_bill)
            coa_mapping = None
            if account_ref_value:
                coa_mapping = self.coa_repo.get_by_external_id(
                    tenant_id,
                    external_id=account_ref_value,
                    service_id=str(service_id),
                    external_acc_id=str(realm_id),
                )
            local_account_id = coa_mapping.get("id") if coa_mapping else None
            logger.debug(
                "Bill account mapping lookup | qbo_account_present=%s local_account_present=%s",
                bool(account_ref_value), local_account_id is not None,
            )

            bill_label_parts = [doc_number] if doc_number else [f"QuickBooks bill {qbo_bill_id}"]
            if private_note:
                bill_label_parts.append(f'memo: "{private_note}"')
            if qbo_vendor_name:
                bill_label_parts.append(f'vendor: "{qbo_vendor_name}"')
            bill_label = " - ".join(bill_label_parts)

            project_resolution = self._resolve_local_project_for_bill(
                tenant_id=tenant_id,
                service_id=service_id,
                realm_id=realm_id,
                qbo_bill=qbo_bill,
                bill_label=bill_label,
            )
            project_name = project_resolution.get("project_name")
            local_project_id = project_resolution.get("project_id")
            project_error = project_resolution.get("error")
            project_warning = project_resolution.get("warning")
            qbo_has_project_link = self._qbo_bill_has_project_link(qbo_bill)
            stock_project_link_block_reason = None

            logger.debug(f"[BillImport]   ProjectRef project_name={project_name!r} qbo_project_id={project_resolution.get('qbo_project_id')!r} -> local_project_id={local_project_id}")

            qbo_last_updated_at = self._get_qbo_last_updated_at(qbo_bill)
            payload = self.receipt_repo.build_receipt_payload_from_qbo(
                qbo_bill,
                vendor_id=local_vendor_id,
                chart_of_account_id=local_account_id,
                project_id=local_project_id,
                qbo_last_updated_at=qbo_last_updated_at,
            )
            stock_guard_receipt = mapped_receipt_for_guard
            if qbo_has_project_link and not stock_guard_receipt and payload.get("invoice_number") and local_vendor_id:
                matched_receipt_id = self.receipt_repo.get_receipt_id_by_doc_number(
                    tenant_id=tenant_id,
                    doc_number=payload.get("invoice_number"),
                    vendor_id=local_vendor_id,
                )
                if matched_receipt_id:
                    stock_guard_receipt = self.receipt_repo.get_current_receipt_by_id(tenant_id, matched_receipt_id)

            if qbo_has_project_link and self._is_stock_receipt_without_project(stock_guard_receipt):
                stock_project_link_block_reason = self._build_stock_bill_project_link_block_reason(
                    project_name=project_name,
                    qbo_project_id=project_resolution.get("qbo_project_id"),
                )
                # This receipt was created in FIELDCRAFT as a stock bill. A project/customer
                # link added later in QuickBooks must not convert it into a project bill.
                project_name = None
                local_project_id = None
                project_error = None
                project_warning = None
                payload["project_id"] = None
                logger.debug(
                    f"[BillImport]   stock bill id={qbo_bill_id} has QBO project/customer link; keeping FIELDCRAFT project_id empty"
                )

            logger.debug(f"[BillImport]   payload invoice_number={payload.get('invoice_number')!r} invoice_date={payload.get('invoice_date')} project_id={payload.get('project_id')} qbo_last_updated_at={qbo_last_updated_at}")

            if project_error:
                logger.debug(f"[BillImport]   ✗ BILL_SYNC_FAILED bill id={qbo_bill_id} reason={project_error}")
                self._persist_import_failed_mapping(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    realm_id=realm_id,
                    qbo_bill_id=qbo_bill_id,
                    sync_token=str(qbo_bill.get(const.QBO_KEY_SYNC_TOKEN) or "") or None,
                    payload=payload,
                    current_mapping=locals().get("current_mapping"),
                    reason=self._build_reason(project_error),
                )
                summary["failed"] += 1
                qbo_api.append_failure(summary, {const.KEY_QBO_BILL_ID: qbo_bill_id, const.KEY_ERROR: project_error, const.KEY_DETAILS: project_error})
                detail.update({const.KEY_STATUS: "failed", const.KEY_ACTION: "projectLookupFailed", const.KEY_ERROR: project_error, const.KEY_REASON: self._build_reason(project_error)})
                summary[const.KEY_RESULTS].append(detail)
                return

            sync_reason_override = stock_project_link_block_reason
            if project_warning:
                mapping_receipt_id = current_mapping.get("receipt_id") if current_mapping else None
                if not mapping_receipt_id and payload.get("invoice_number") and local_vendor_id:
                    mapping_receipt_id = self.receipt_repo.get_receipt_id_by_doc_number(
                        tenant_id=tenant_id,
                        doc_number=payload.get("invoice_number"),
                        vendor_id=local_vendor_id,
                    )

                mapped_receipt = None
                if mapping_receipt_id:
                    mapped_receipt = self.receipt_repo.get_current_receipt_by_id(tenant_id, mapping_receipt_id)

                if self._is_stock_receipt_without_project(mapped_receipt):
                    # FIELDCRAFT stock bills are intentionally not project-scoped.
                    # They should remain normal synced bills when QBO returns
                    # them without ProjectRef/CustomerRef.
                    logger.debug(
                        f"[BillImport]   ✓ stock bill id={qbo_bill_id} has no QBO project link by design — keeping SYNCED"
                    )
                    payload["project_id"] = None
                    local_project_id = None
                else:
                    if not mapping_receipt_id:
                        project_not_linked_reason = (
                            f'Bill "{bill_label}" is not linked to a project in QuickBooks. '
                            "Please open this bill in QuickBooks, link it to the correct project, and sync again."
                        )
                        logger.debug(f"[BillImport]   ✗ BILL_SYNC_FAILED bill id={qbo_bill_id} no mapping/receipt — bill not linked to project in QBO")
                        self._persist_import_failed_mapping(
                            tenant_id=tenant_id,
                            service_id=service_id,
                            realm_id=realm_id,
                            qbo_bill_id=qbo_bill_id,
                            sync_token=str(qbo_bill.get(const.QBO_KEY_SYNC_TOKEN) or "") or None,
                            payload=payload,
                            current_mapping=current_mapping,
                            reason=self._build_reason(project_not_linked_reason),
                        )
                        summary["failed"] += 1
                        qbo_api.append_failure(summary, {const.KEY_QBO_BILL_ID: qbo_bill_id, const.KEY_ERROR: project_not_linked_reason, const.KEY_DETAILS: project_not_linked_reason})
                        detail.update({const.KEY_STATUS: "failed", const.KEY_ACTION: "notLinkedToProject", const.KEY_ERROR: project_not_linked_reason, const.KEY_REASON: self._build_reason(project_not_linked_reason)})
                        summary[const.KEY_RESULTS].append(detail)
                        return
                    logger.debug(f"[BillImport]   ⚠ bill id={qbo_bill_id} not linked to project in QBO but has mapping+receipt — syncing with warning as reason")
                    sync_reason_override = project_warning

            if current_mapping and not current_mapping_is_import_failed:
                logger.debug(f"[BillImport]   → path: UPDATE existing mapped receipt_id={current_mapping.get('receipt_id')}")
                current_receipt = self.receipt_repo.get_current_receipt_by_id(tenant_id, current_mapping["receipt_id"]) if current_mapping.get("receipt_id") else None
                managed_line_meta = None
                if current_receipt:
                    payload, managed_line_meta = self._prepare_mapped_fieldcraft_payload(
                        current_receipt=current_receipt,
                        current_mapping=current_mapping,
                        qbo_bill=qbo_bill,
                        payload=payload,
                    )
                local_latest_at = self.receipt_repo.get_receipt_latest_local_timestamp(current_receipt, None)
                has_change = bool(
                    current_receipt
                    and (
                        self.receipt_repo.sync_projection_changed(current_receipt, payload)
                        or (managed_line_meta and self._managed_receipt_info_changed(current_receipt, payload))
                    )
                )
                if current_receipt and not has_change:
                    logger.debug(f"[BillImport]   → SKIP already current bill id={qbo_bill_id} receipt_id={current_mapping.get('receipt_id')}")
                    self.receipt_map_repo.mark_mapping_synced(
                        tenant_id=tenant_id,
                        mapping_id=current_mapping["id"],
                        external_receipt_id=qbo_bill_id,
                        external_account_id=realm_id,
                        sync_token=str(qbo_bill.get(const.QBO_KEY_SYNC_TOKEN) or "") or None,
                        reason=self._build_reason(
                            sync_reason_override or "No changes detected — this bill is already up to date.",
                            managed_line=managed_line_meta,
                        ),
                        sync_status=self.receipt_repo.STATUS_SYNCED_INCOMPLETE if sync_reason_override else None,
                    )
                    summary["skippedAlreadyCurrent"] += 1
                    detail.update({const.KEY_STATUS: "notice" if sync_reason_override else "skipped", const.KEY_ACTION: "alreadyCurrent", const.KEY_FIELDCRAFT_BILL_ID: current_mapping.get("receipt_id"), const.KEY_REASON: self._build_reason(sync_reason_override or "No changes detected — this bill is already up to date.")})
                    summary[const.KEY_RESULTS].append(detail)
                    return
                if self._should_skip_import_update(qbo_last_updated_at, local_latest_at):
                    logger.debug(f"[BillImport]   → SKIP local newer bill id={qbo_bill_id} receipt_id={current_mapping.get('receipt_id')} local={local_latest_at} qbo={qbo_last_updated_at}")
                    summary["skippedLocalNewer"] += 1
                    detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "localNewer", const.KEY_FIELDCRAFT_BILL_ID: current_mapping.get("receipt_id"), const.KEY_REASON: self._build_reason("Your record is more recent than QuickBooks. No changes were imported to protect your latest edits.")})
                    summary[const.KEY_RESULTS].append(detail)
                    return
                if dry_run:
                    detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "dryRun", const.KEY_FIELDCRAFT_BILL_ID: current_mapping.get("receipt_id"), const.KEY_REASON: self._build_reason("Preview only — no changes have been saved.")})
                    summary[const.KEY_RESULTS].append(detail)
                    return
                logger.debug(f"[BillImport]   ✓ updating receipt_id={current_mapping['receipt_id']} from QBO bill id={qbo_bill_id}")
                updated_receipt = self.receipt_repo.update_receipt_from_qbo(tenant_id=tenant_id, receipt_id=current_mapping["receipt_id"], payload=payload)
                self.receipt_map_repo.rotate_mapping_for_receipt(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    receipt_id=updated_receipt["id"],
                    external_receipt_id=qbo_bill_id,
                    external_account_id=realm_id,
                    sync_status=self.receipt_repo.STATUS_SYNCED_INCOMPLETE if sync_reason_override else self.receipt_repo.STATUS_SYNCED,
                    sync_token=str(qbo_bill.get(const.QBO_KEY_SYNC_TOKEN) or "") or None,
                    reason=self._build_reason(
                        sync_reason_override or "Bill updated to match the latest changes from QuickBooks.",
                        managed_line=managed_line_meta,
                    ),
                )
                self._try_import_attachment(tenant_id=tenant_id, access_token=access_token, base_url=base_url, realm_id=realm_id, qbo_bill_id=qbo_bill_id, receipt_id=int(updated_receipt["id"]), receipt_project_id=local_project_id)
                summary["updatedInFIELDCRAFT"] += 1
                detail.update({const.KEY_STATUS: "notice" if sync_reason_override else "success", const.KEY_ACTION: "updated in FIELDCRAFT", const.KEY_FIELDCRAFT_BILL_ID: updated_receipt["id"], const.KEY_REASON: self._build_reason(sync_reason_override or "Bill updated to match the latest changes from QuickBooks.")})
                summary[const.KEY_RESULTS].append(detail)
                return

            if current_mapping_is_import_failed and current_mapping.get("receipt_id"):
                logger.debug(f"[BillImport]   → path: RETRY import_failed receipt_id={current_mapping.get('receipt_id')} bill id={qbo_bill_id}")
                tracked_receipt = self.receipt_repo.get_current_receipt_by_id(tenant_id, current_mapping["receipt_id"])
                payload = self._preserve_paid_fieldcraft_state(
                    current_receipt=tracked_receipt,
                    payload=payload,
                )
                if dry_run:
                    detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "dryRun", const.KEY_FIELDCRAFT_BILL_ID: current_mapping.get("receipt_id"), const.KEY_REASON: self._build_reason("Preview only — no changes have been saved (retry of a previous import error).")})
                    summary[const.KEY_RESULTS].append(detail)
                    return
                if tracked_receipt and not self.receipt_repo.sync_projection_changed(tracked_receipt, payload):
                    self.receipt_map_repo.mark_mapping_synced(
                        tenant_id=tenant_id,
                        mapping_id=current_mapping["id"],
                        external_receipt_id=qbo_bill_id,
                        external_account_id=realm_id,
                        sync_token=str(qbo_bill.get(const.QBO_KEY_SYNC_TOKEN) or "") or None,
                        reason=self._build_reason(sync_reason_override or "A previous import error was resolved. This bill is now in sync with QuickBooks."),
                        sync_status=self.receipt_repo.STATUS_SYNCED_INCOMPLETE if sync_reason_override else None,
                    )
                    detail.update({const.KEY_STATUS: "notice" if sync_reason_override else "success", const.KEY_ACTION: "resolved import failure", const.KEY_FIELDCRAFT_BILL_ID: current_mapping.get("receipt_id"), const.KEY_REASON: self._build_reason(sync_reason_override or "A previous import error was resolved. This bill is now in sync with QuickBooks.")})
                    summary[const.KEY_RESULTS].append(detail)
                    return
                if tracked_receipt:
                    tracked_receipt = self.receipt_repo.update_receipt_from_qbo(tenant_id=tenant_id, receipt_id=current_mapping["receipt_id"], payload=payload)
                target_receipt_id = tracked_receipt["id"] if tracked_receipt else current_mapping["receipt_id"]
                self.receipt_map_repo.rotate_mapping_for_receipt(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    receipt_id=target_receipt_id,
                    external_receipt_id=qbo_bill_id,
                    external_account_id=realm_id,
                    sync_status=self.receipt_repo.STATUS_SYNCED_INCOMPLETE if sync_reason_override else self.receipt_repo.STATUS_SYNCED,
                    sync_token=str(qbo_bill.get(const.QBO_KEY_SYNC_TOKEN) or "") or None,
                    reason=self._build_reason(sync_reason_override or "A previous import error was resolved and the bill has been updated."),
                )
                self.receipt_map_repo.close_mapping_as_invalid(tenant_id, current_mapping["id"])
                self._try_import_attachment(tenant_id=tenant_id, access_token=access_token, base_url=base_url, realm_id=realm_id, qbo_bill_id=qbo_bill_id, receipt_id=int(target_receipt_id), receipt_project_id=local_project_id)
                summary["updatedInFIELDCRAFT"] += 1
                detail.update({const.KEY_STATUS: "notice" if sync_reason_override else "success", const.KEY_ACTION: "resolved import failure", const.KEY_FIELDCRAFT_BILL_ID: target_receipt_id, const.KEY_REASON: self._build_reason(sync_reason_override or "A previous import error was resolved and the bill has been updated.")})
                summary[const.KEY_RESULTS].append(detail)
                return

            existing_receipt_id = None
            if payload.get("invoice_number"):
                existing_receipt_id = self.receipt_repo.get_receipt_id_by_doc_number(
                    tenant_id=tenant_id,
                    doc_number=payload.get("invoice_number"),
                    vendor_id=local_vendor_id,
                )
            logger.debug(f"[BillImport]   doc_number lookup invoice_number={payload.get('invoice_number')!r} vendor={local_vendor_id} → existing_receipt_id={existing_receipt_id}")
            if existing_receipt_id:
                if dry_run:
                    detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "dryRun", const.KEY_FIELDCRAFT_BILL_ID: existing_receipt_id, const.KEY_REASON: self._build_reason("Preview only — no changes have been saved.")})
                    summary[const.KEY_RESULTS].append(detail)
                    return
                current_receipt = self.receipt_repo.get_current_receipt_by_id(tenant_id, existing_receipt_id)
                managed_line_meta = None
                if current_receipt:
                    payload, managed_line_meta = self._prepare_mapped_fieldcraft_payload(
                        current_receipt=current_receipt,
                        current_mapping=None,
                        qbo_bill=qbo_bill,
                        payload=payload,
                    )
                local_latest_at = self.receipt_repo.get_receipt_latest_local_timestamp(current_receipt, None)
                has_change = bool(
                    current_receipt
                    and (
                        self.receipt_repo.sync_projection_changed(current_receipt, payload)
                        or (managed_line_meta and self._managed_receipt_info_changed(current_receipt, payload))
                    )
                )
                if has_change and self._should_skip_import_update(qbo_last_updated_at, local_latest_at):
                    sync_status = self.receipt_repo.STATUS_PENDING_PUSH
                    reason = sync_reason_override or "Matched to an existing bill. Your local version is newer and will be pushed to QuickBooks."
                    action = "linked existing pending push"
                    target_receipt_id = existing_receipt_id
                    summary["skippedLocalNewer"] += 1
                else:
                    sync_status = self.receipt_repo.STATUS_SYNCED_INCOMPLETE if sync_reason_override else self.receipt_repo.STATUS_SYNCED
                    reason = sync_reason_override or "Matched and linked to an existing bill."
                    action = "linked existing in FIELDCRAFT"
                    if has_change:
                        updated_receipt = self.receipt_repo.update_receipt_from_qbo(tenant_id=tenant_id, receipt_id=existing_receipt_id, payload=payload)
                        target_receipt_id = updated_receipt["id"]
                    else:
                        target_receipt_id = existing_receipt_id
                self.receipt_map_repo.rotate_mapping_for_receipt(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    receipt_id=target_receipt_id,
                    external_receipt_id=qbo_bill_id,
                    external_account_id=realm_id,
                    sync_status=sync_status,
                    sync_token=str(qbo_bill.get(const.QBO_KEY_SYNC_TOKEN) or "") or None,
                    reason=self._build_reason(reason, managed_line=managed_line_meta),
                )
                if current_mapping_is_import_failed and current_mapping:
                    self.receipt_map_repo.close_mapping_as_invalid(tenant_id, current_mapping["id"])
                logger.debug(f"[BillImport]   ✓ linked existing receipt_id={target_receipt_id} to QBO bill id={qbo_bill_id} action={action!r}")
                self._try_import_attachment(tenant_id=tenant_id, access_token=access_token, base_url=base_url, realm_id=realm_id, qbo_bill_id=qbo_bill_id, receipt_id=int(target_receipt_id), receipt_project_id=local_project_id)
                summary["linkedExistingInFieldCraft"] += 1
                detail.update({const.KEY_STATUS: "notice" if sync_reason_override else "success", const.KEY_ACTION: action, const.KEY_FIELDCRAFT_BILL_ID: target_receipt_id, const.KEY_REASON: self._build_reason(reason)})
                summary[const.KEY_RESULTS].append(detail)
                return

            if dry_run:
                detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "dryRun", const.KEY_REASON: self._build_reason("Preview only — no changes have been saved.")})
                summary[const.KEY_RESULTS].append(detail)
                return

            missing_fields = self._get_missing_required_fields(payload=payload, local_vendor_id=local_vendor_id, local_account_id=local_account_id)
            logger.debug(f"[BillImport]   → path: CREATE new receipt | missing_fields={missing_fields}")
            if missing_fields:
                qbo_account_name = self._extract_qbo_account_name(qbo_bill)
                reason = self._build_missing_fields_reason(
                    bill_label=bill_label,
                    missing_fields=missing_fields,
                    qbo_vendor_name=qbo_vendor_name,
                    qbo_vendor_id=qbo_vendor_id,
                    qbo_account_name=qbo_account_name,
                    account_ref_value=account_ref_value or "",
                )
                logger.debug(f"[BillImport]   ✗ BILL_SYNC_FAILED bill id={qbo_bill_id} reason={reason}")
                self._persist_import_failed_mapping(tenant_id=tenant_id, service_id=service_id, realm_id=realm_id, qbo_bill_id=qbo_bill_id, sync_token=str(qbo_bill.get(const.QBO_KEY_SYNC_TOKEN) or "") or None, payload=payload, current_mapping=locals().get("current_mapping"), reason=self._build_reason(reason))
                summary["failed"] += 1
                qbo_api.append_failure(summary, {const.KEY_QBO_BILL_ID: qbo_bill_id, const.KEY_ERROR: reason, const.KEY_DETAILS: reason})
                detail.update({const.KEY_STATUS: "failed", const.KEY_ACTION: "missingRequired", const.KEY_ERROR: reason, const.KEY_REASON: self._build_reason(reason)})
                summary[const.KEY_RESULTS].append(detail)
                return

            logger.debug(f"[BillImport]   ✓ creating receipt for bill id={qbo_bill_id} vendor={local_vendor_id} account={local_account_id}")
            receipt_id = self.receipt_repo.create_receipt_for_tenant(
                tenant_id=tenant_id,
                receipt_data={**payload, "receipt_uuid": None},
                created_by=const.SERVICE_NAME_QUICKBOOKS,
            )
            self.receipt_map_repo.rotate_mapping_for_receipt(
                tenant_id=tenant_id,
                service_id=service_id,
                receipt_id=receipt_id,
                external_receipt_id=qbo_bill_id,
                external_account_id=realm_id,
                sync_status=self.receipt_repo.STATUS_SYNCED,
                sync_token=str(qbo_bill.get(const.QBO_KEY_SYNC_TOKEN) or "") or None,
                reason=self._build_reason("Bill imported from QuickBooks."),
            )
            if current_mapping_is_import_failed and current_mapping:
                self.receipt_map_repo.close_mapping_as_invalid(tenant_id, current_mapping["id"])
            self._try_import_attachment(tenant_id=tenant_id, access_token=access_token, base_url=base_url, realm_id=realm_id, qbo_bill_id=qbo_bill_id, receipt_id=int(receipt_id), receipt_project_id=local_project_id)
            summary["created in FIELDCRAFT"] += 1
            detail.update({const.KEY_STATUS: "success", const.KEY_ACTION: "created in FIELDCRAFT", const.KEY_FIELDCRAFT_BILL_ID: receipt_id, const.KEY_REASON: self._build_reason("Bill imported from QuickBooks.")})
            summary[const.KEY_RESULTS].append(detail)
        except Exception as exc:
            intuit_tid = exc.intuit_tid if isinstance(exc, QuickBooksAPIError) else None
            exc_msg = exc.user_message if isinstance(exc, QuickBooksAPIError) else str(exc)
            logger.debug(f"[BillImport]   ✗ EXCEPTION bill id={qbo_bill_id}: {exc_msg}")
            logger.exception("QuickBooks bill record processing failed | qbo_bill_id=%s", qbo_bill_id)
            self._persist_import_failed_mapping(tenant_id=tenant_id, service_id=service_id, realm_id=realm_id, qbo_bill_id=qbo_bill_id, sync_token=str(qbo_bill.get(const.QBO_KEY_SYNC_TOKEN) or "") or None, payload=locals().get("payload"), current_mapping=locals().get("current_mapping"), reason=self._build_reason(exc_msg, intuit_tid=intuit_tid))
            summary["failed"] += 1
            qbo_api.append_failure(summary, {const.KEY_QBO_BILL_ID: qbo_bill_id, const.KEY_ERROR: str(exc)})
            detail.update({const.KEY_STATUS: "failed", const.KEY_ACTION: "error", const.KEY_ERROR: str(exc), const.KEY_REASON: self._build_reason("This bill couldn't be imported and has been queued for automatic retry.", intuit_tid=intuit_tid)})
            summary[const.KEY_RESULTS].append(detail)

    def _is_stock_receipt_without_project(self, receipt: Optional[Dict[str, Any]]) -> bool:
        """Return True for local FIELDCRAFT stock bills.

        A bill imported from QuickBooks with no project link is not enough to
        classify it as stock. We only treat it as stock when an existing local
        receipt is mapped/matched and that FIELDCRAFT receipt has no project_id.
        """
        if not receipt or "project_id" not in receipt:
            return False
        project_id = receipt.get("project_id")
        return project_id is None or self._clean(project_id).upper() == "NULL"

    def _qbo_bill_has_project_link(self, qbo_bill: Dict[str, Any]) -> bool:
        """Return True when a QBO bill carries any project/customer reference."""
        refs_by_target = self._extract_qbo_project_refs(qbo_bill)
        return any(refs_by_target.get(target) for target in refs_by_target)

    def _build_stock_bill_project_link_block_reason(
        self,
        *,
        project_name: Optional[str] = None,
        qbo_project_id: Optional[str] = None,
    ) -> str:
        project_label = self._clean(project_name) or self._clean(qbo_project_id)
        linked_text = f'We ignored the project link ("{project_label}") added in QuickBooks.' if project_label else "We ignored the project link added in QuickBooks."
        return (
            "This is a stock bill, so it cannot be linked to a project. "
            f"{linked_text} "
            "If this cost needs to be tracked against a project, create a separate project bill."
        )

    def _try_import_attachment(
        self,
        *,
        tenant_id: str,
        access_token: str,
        base_url: str,
        realm_id: str,
        qbo_bill_id: str,
        receipt_id: int,
        receipt_project_id: Optional[int] = None,
    ) -> None:
        """Import the first file attachment from a QBO bill into blob storage and link it to
        the receipt via the documents table. Logs and swallows errors so attachment failures
        never block bill sync."""
        logger.debug(f"[BillImport] _try_import_attachment | tenant={tenant_id} qbo_bill_id={qbo_bill_id} receipt_id={receipt_id}")
        try:
            attachments = self.attachment_service.fetch_qbo_attachments(
                access_token=access_token,
                base_url=base_url,
                realm_id=realm_id,
                bill_id=qbo_bill_id,
            )
            if not attachments:
                logger.debug(f"[BillImport] _try_import_attachment — no file attachments on QBO bill {qbo_bill_id}")
                return

            # Take the first file attachment only
            attachable = attachments[0]
            file_name = os.path.basename((attachable.get("FileName") or "attachment").strip()) or "attachment"
            content_type = self._normalize_attachment_content_type(
                file_name=file_name,
                qbo_content_type=attachable.get("ContentType"),
            )
            temp_uri = attachable["TempDownloadUri"]
            file_size = int(attachable.get("FileSize") or attachable.get("Size") or 0)
            logger.debug(f"[BillImport] _try_import_attachment | file={file_name!r} content_type={content_type!r} size={file_size}")

            attachment_context = self._resolve_receipt_attachment_context(
                tenant_id=tenant_id,
                receipt_id=receipt_id,
            )
            if attachment_context.get("document_id"):
                logger.debug(
                    f"[BillImport] _try_import_attachment skipped — receipt_id={receipt_id} already has document_id={attachment_context.get('document_id')}"
                )
                return

            order_no = attachment_context.get("order_no")
            if not order_no:
                raise ValueError(
                    f"Cannot import QBO bill attachment for receipt_id={receipt_id}: project order_no not found"
                )

            file_bytes = self.attachment_service.download_qbo_attachment(temp_uri)
            if not file_size:
                file_size = len(file_bytes)

            document_uid = str(uuid.uuid4())
            blob_path = f"trade/{order_no}/Bill/{document_uid}/{file_name}"

            self.attachment_service.upload_to_blob(tenant_id, blob_path, file_bytes, content_type=content_type)

            document_id = self.doc_repo.create_bill_document(
                tenant_id,
                name=file_name,
                path=blob_path,
                mime_type=content_type,
                size=file_size,
                document_uid=document_uid,
                project_id=attachment_context.get("project_id") or receipt_project_id,
            )

            self.receipt_repo.set_receipt_document_id(tenant_id, receipt_id, document_id)
            logger.debug(f"[BillImport] _try_import_attachment ✓ receipt_id={receipt_id} → document_id={document_id} blob={blob_path!r}")
        except Exception as exc:
            logger.debug(f"[BillImport] _try_import_attachment ⚠ skipped for receipt_id={receipt_id} qbo_bill_id={qbo_bill_id}: {exc}")
            logger.exception("QuickBooks bill attachment import failed | qbo_bill_id=%s", qbo_bill_id)

    def _resolve_receipt_attachment_context(self, *, tenant_id: str, receipt_id: int) -> Dict[str, Any]:
        """Resolve attachment path context from receipts.project_id -> project.order_no."""
        receipt = self.receipt_repo.get_current_receipt_by_id(tenant_id, receipt_id)
        project_id = receipt.get("project_id") if receipt else None
        if project_id is None:
            return {"project_id": None, "order_no": None, "document_id": receipt.get("document_id") if receipt else None}

        order_no = self.project_repo.get_project_order_no_by_id(
            tenant_id=tenant_id,
            project_id=int(project_id),
        )
        return {
            "project_id": int(project_id),
            "order_no": order_no,
            "document_id": receipt.get("document_id"),
        }

    def _normalize_attachment_content_type(self, *, file_name: str, qbo_content_type: Any = None) -> str:
        """Prefer image content types for image files so blob metadata is useful in FIELDCRAFT."""
        guessed_type = mimetypes.guess_type(file_name)[0]
        qbo_type = str(qbo_content_type or "").strip().lower()

        if guessed_type and guessed_type.startswith("image/"):
            return guessed_type
        if qbo_type.startswith("image/"):
            return qbo_type
        if file_name.lower().endswith((".jpg", ".jpeg")):
            return "image/jpeg"
        return guessed_type or qbo_type or "application/octet-stream"

    def _extract_qbo_project_refs(self, qbo_bill: Dict[str, Any]) -> Dict[str, List[Dict[str, Optional[str]]]]:
        refs: Dict[str, List[Dict[str, Optional[str]]]] = {
            const.PROJECT_SYNC_TARGET_QBO_PROJECT: [],
            const.PROJECT_SYNC_TARGET_SUB_CUSTOMER: [],
        }
        seen = {
            const.PROJECT_SYNC_TARGET_QBO_PROJECT: set(),
            const.PROJECT_SYNC_TARGET_SUB_CUSTOMER: set(),
        }

        def _collect_ref(ref: Any, target: str) -> None:
            if not isinstance(ref, dict):
                return
            value = self._clean(ref.get("value") or ref.get("Value"))
            name = self._clean(ref.get("name") or ref.get("Name"))
            if not value or value in seen[target]:
                return
            seen[target].add(value)
            refs[target].append({"id": value, "name": name or None, "target": target})

        _collect_ref(qbo_bill.get("ProjectRef"), const.PROJECT_SYNC_TARGET_QBO_PROJECT)
        _collect_ref(qbo_bill.get("CustomerRef"), const.PROJECT_SYNC_TARGET_SUB_CUSTOMER)
        for line in qbo_bill.get("Line") or []:
            if not isinstance(line, dict):
                continue
            _collect_ref(line.get("ProjectRef"), const.PROJECT_SYNC_TARGET_QBO_PROJECT)
            _collect_ref(line.get("CustomerRef"), const.PROJECT_SYNC_TARGET_SUB_CUSTOMER)
            for detail_key in ("AccountBasedExpenseLineDetail", "ItemBasedExpenseLineDetail"):
                detail = line.get(detail_key) or {}
                if isinstance(detail, dict):
                    _collect_ref(detail.get("ProjectRef"), const.PROJECT_SYNC_TARGET_QBO_PROJECT)
                    _collect_ref(detail.get("CustomerRef"), const.PROJECT_SYNC_TARGET_SUB_CUSTOMER)
        return refs

    def _resolve_local_project_for_bill(
        self,
        *,
        tenant_id: str,
        service_id: str,
        realm_id: str,
        qbo_bill: Dict[str, Any],
        bill_label: str = "",
    ) -> Dict[str, Any]:
        refs_by_target = self._extract_qbo_project_refs(qbo_bill)
        project_refs = refs_by_target.get(const.PROJECT_SYNC_TARGET_QBO_PROJECT) or []
        customer_refs = refs_by_target.get(const.PROJECT_SYNC_TARGET_SUB_CUSTOMER) or []
        label = f'Bill "{bill_label}"' if bill_label else "This bill"

        if len(project_refs) > 1:
            joined_refs = ", ".join(sorted([ref.get("name") or ref.get("id") or "unknown project" for ref in project_refs]))
            return {
                "project_name": None,
                "qbo_project_id": None,
                "project_sync_target": const.PROJECT_SYNC_TARGET_QBO_PROJECT,
                "project_id": None,
                "error": (
                    f"{label} is linked to multiple QuickBooks Projects ({joined_refs}). "
                    "Please link the bill to exactly one project in QuickBooks and sync again."
                ),
            }

        # Prefer ProjectRef. QuickBooks can return both ProjectRef and CustomerRef
        # for the same bill line; CustomerRef usually represents the parent customer
        # and must not be treated as a second project when ProjectRef is present.
        expected_target = const.PROJECT_SYNC_TARGET_QBO_PROJECT if project_refs else const.PROJECT_SYNC_TARGET_SUB_CUSTOMER
        refs_to_use = project_refs if project_refs else customer_refs

        if not refs_to_use:
            return {
                "project_name": None,
                "qbo_project_id": None,
                "project_sync_target": None,
                "project_id": None,
                "warning": (
                    f"{label} has been synced, but it is not linked to a project in QuickBooks. "
                    "Please link the bill to the correct synced QuickBooks project/sub-customer and sync again."
                ),
            }

        if len(refs_to_use) > 1:
            joined_refs = ", ".join(sorted([ref.get("name") or ref.get("id") or "unknown project" for ref in refs_to_use]))
            target_label = "QuickBooks Projects" if expected_target == const.PROJECT_SYNC_TARGET_QBO_PROJECT else "QuickBooks sub-customers"
            return {
                "project_name": None,
                "qbo_project_id": None,
                "project_sync_target": expected_target,
                "project_id": None,
                "error": (
                    f"{label} is linked to multiple {target_label} ({joined_refs}). "
                    "Please link the bill to exactly one project in QuickBooks and sync again."
                ),
            }

        project_ref = refs_to_use[0]
        qbo_project_id = self._clean(project_ref.get("id"))
        project_name = self._clean(project_ref.get("name"))
        mapping = self.project_repo.get_current_mapping_by_external_project_id(
            tenant_id=tenant_id,
            service_id=service_id,
            external_project_id=qbo_project_id,
        )
        self._assert_mapping_realm(mapping, realm_id, "project")
        mapping_target = self.project_repo.get_mapping_sync_target(mapping) if mapping else None
        if mapping and mapping_target != expected_target:
            mapping = None

        if not mapping:
            project_label = project_name or qbo_project_id
            target_label = "QuickBooks Project" if expected_target == const.PROJECT_SYNC_TARGET_QBO_PROJECT else "QuickBooks sub-customer"
            return {
                "project_name": project_name or None,
                "qbo_project_id": qbo_project_id,
                "project_sync_target": expected_target,
                "project_id": None,
                "error": (
                    f'{label} couldn\'t be imported — {target_label} "{project_label}" is not mapped to a FIELDCRAFT project. '
                    "Run project sync first, link the bill to the synced QuickBooks project/sub-customer, then sync bills again."
                ),
            }

        project_id = mapping.get("project_id")
        project = self.project_repo.get_active_project_by_id(tenant_id=tenant_id, project_id=int(project_id or 0))
        if not project:
            project_label = project_name or qbo_project_id
            return {
                "project_name": project_name or None,
                "qbo_project_id": qbo_project_id,
                "project_sync_target": expected_target,
                "project_id": None,
                "error": (
                    f'{label} couldn\'t be imported — QuickBooks project "{project_label}" is mapped, but the FIELDCRAFT project is not active. '
                    "Review the project mapping, then sync again."
                ),
            }

        return {
            "project_name": project_name or self._clean(project.get("description")) or None,
            "qbo_project_id": qbo_project_id,
            "project_sync_target": expected_target,
            "project_id": int(project_id),
            "error": None,
        }

    def _persist_import_failed_mapping(self, *, tenant_id: str, service_id: str, realm_id: str, qbo_bill_id: str, sync_token: Optional[str], payload: Optional[Dict[str, Any]], current_mapping: Optional[Dict[str, Any]], reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
        try:
            target_receipt_id = None
            if current_mapping and current_mapping.get("receipt_id"):
                target_receipt_id = current_mapping.get("receipt_id")
            elif payload and payload.get("invoice_number"):
                target_receipt_id = self.receipt_repo.get_receipt_id_by_doc_number(
                    tenant_id=tenant_id,
                    doc_number=payload.get("invoice_number"),
                    vendor_id=payload.get("vendor_id"),
                )
            failed_status = (
                self.receipt_repo.STATUS_SYNC_FAILED
                if target_receipt_id is not None
                else self.receipt_repo.STATUS_IMPORT_FAILED
            )
            return self.receipt_map_repo.upsert_import_failed_mapping(
                tenant_id=tenant_id,
                service_id=service_id,
                external_receipt_id=qbo_bill_id,
                external_account_id=realm_id,
                receipt_id=target_receipt_id,
                sync_token=sync_token,
                reason=reason or self._build_reason("This bill couldn't be synced. It will be retried automatically on the next sync."),
                sync_status=failed_status,
            )
        except Exception:
            logger.exception(
                "Bill import failed to persist retry mapping | qbo_bill_id_present=%s",
                bool(qbo_bill_id),
            )
            return None

    def _get_missing_required_fields(self, *, payload: Dict[str, Any], local_vendor_id: Optional[int], local_account_id: Optional[int]) -> List[str]:
        """Returns simple field keys for each missing requirement."""
        missing = []
        if not local_vendor_id:
            missing.append("vendor")
        if not local_account_id:
            missing.append("account")
        info = self.receipt_repo.parse_additional_info(payload.get("receipt_additional_info"))
        if not self.receipt_repo._pick_txn_date(payload, info):
            missing.append("date")
        if self.receipt_repo._pick_total_amount(info) in (None, 0):
            missing.append("amount")
        return missing

    def _extract_qbo_account_name(self, qbo_bill: Dict[str, Any]) -> Optional[str]:
        """Returns the display name of the first expense account found on the bill line items."""
        for line in qbo_bill.get("Line") or []:
            if not isinstance(line, dict):
                continue
            account_ref = (line.get("AccountBasedExpenseLineDetail") or {}).get("AccountRef") or {}
            name = str(account_ref.get("name") or "").strip()
            if name:
                return name
        ap_ref = qbo_bill.get("APAccountRef") or {}
        return str(ap_ref.get("name") or "").strip() or None

    def _build_missing_fields_reason(
        self,
        *,
        bill_label: str,
        missing_fields: List[str],
        qbo_vendor_name: Optional[str],
        qbo_vendor_id: str,
        qbo_account_name: Optional[str],
        account_ref_value: str,
    ) -> str:
        """Builds a user-friendly error message that names the specific vendor, account, or field blocking import."""
        parts = []
        if "vendor" in missing_fields:
            vendor_hint = f'"{qbo_vendor_name}"' if qbo_vendor_name else f"QuickBooks vendor ID {qbo_vendor_id}"
            parts.append(f"vendor {vendor_hint} hasn't been imported yet — run a vendor sync first, then retry the bill sync")
        if "account" in missing_fields:
            account_hint = f'"{qbo_account_name}"' if qbo_account_name else f"QuickBooks account ID {account_ref_value}"
            parts.append(f"chart of account {account_hint} hasn't been imported yet — run an accounts sync first, then retry the bill sync")
        if "date" in missing_fields:
            parts.append("bill date is missing in QuickBooks — update the bill in QuickBooks and sync again")
        if "amount" in missing_fields:
            parts.append("bill total is missing or zero in QuickBooks — update the bill in QuickBooks and sync again")

        if len(parts) == 1:
            return f'Bill "{bill_label}" can\'t be imported yet — {parts[0]}.'
        numbered = "; ".join(f"({i + 1}) {p}" for i, p in enumerate(parts))
        return f'Bill "{bill_label}" can\'t be imported yet. Please resolve the following: {numbered}.'

    def _extract_qbo_account_id(self, qbo_bill: Dict[str, Any]) -> str:
        for line in qbo_bill.get("Line") or []:
            if not isinstance(line, dict):
                continue
            detail = line.get("AccountBasedExpenseLineDetail") or {}
            account_ref = detail.get("AccountRef") or {}
            value = str(account_ref.get("value") or "").strip()
            if value:
                return value
        ap_ref = qbo_bill.get("APAccountRef") or {}
        return str(ap_ref.get("value") or "").strip()

    def _is_deleted_qbo_bill(self, qbo_bill: Dict[str, Any]) -> bool:
        status = str(qbo_bill.get("status") or qbo_bill.get("Status") or "").strip().lower()
        return status == "deleted"

    def _should_skip_import_update(self, qbo_last_updated_at: Optional[datetime], local_latest_at: Optional[datetime]) -> bool:
        if qbo_last_updated_at is None or local_latest_at is None:
            return False
        return qbo_last_updated_at <= local_latest_at

    def _is_cdc_too_old(self, changed_since: datetime) -> bool:
        if changed_since.tzinfo is None:
            changed_since = changed_since.replace(tzinfo=timezone.utc)
        return changed_since < (datetime.now(timezone.utc) - timedelta(days=29))

    def _normalize_changed_since(self, changed_since: Optional[datetime]) -> Optional[datetime]:
        if changed_since is None:
            return None
        if changed_since.tzinfo is None:
            changed_since = changed_since.replace(tzinfo=timezone.utc)
        return changed_since - timedelta(minutes=1)

    def _get_bill_module_changed_since(self, tenant_id: str, service_id: str) -> Optional[datetime]:
        try:
            last_sync_start = self.scheduled_job_repo.get_last_successful_module_sync_start(
                tenant_id=tenant_id,
                job_type="QBO_BILL_SYNC",
                service_id=str(service_id),
            )
            return self._normalize_changed_since(last_sync_start)
        except Exception:
            return None

    def _get_bill_manual_changed_since(self, tenant_id: str, service_id: str) -> Optional[datetime]:
        try:
            additional_info = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            return self._normalize_changed_since(self._parse_timestamp(additional_info.get("lastBillSyncAt")))
        except Exception:
            return None

    def _resolve_changed_since(self, tenant_id: str, service_id: str, changed_since_override: Optional[Any] = None, force_full: bool = False) -> Tuple[Optional[datetime], Optional[str]]:
        if force_full:
            return None, "forceFull"
        if changed_since_override is not None:
            parsed_override = self._parse_timestamp(changed_since_override)
            if parsed_override is not None:
                return self._normalize_changed_since(parsed_override), "override"
        module_changed_since = self._get_bill_module_changed_since(tenant_id=tenant_id, service_id=service_id)
        if module_changed_since is not None:
            return module_changed_since, "scheduledJob"
        manual_changed_since = self._get_bill_manual_changed_since(tenant_id=tenant_id, service_id=service_id)
        if manual_changed_since is not None:
            return manual_changed_since, "lastBillSyncAt"
        return None, None

    def _fetch_qbo_bills_cdc(self, *, access_token: str, base_url: str, realm_id: str, changed_since: datetime) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        if changed_since.tzinfo is None:
            changed_since = changed_since.replace(tzinfo=timezone.utc)
        changed_since_value = changed_since.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        url = f"{base_url}/v3/company/{realm_id}/cdc"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "text/plain"}
        params = {"entities": "Bill", "changedSince": changed_since_value}
        resp = qbo_err.qbo_get(url, headers=headers, params=params, timeout=60)
        qbo_err.raise_for_qbo_response(resp, fallback_message="QBO bill CDC failed")
        data = qbo_err.safe_json(resp)
        logger.debug(
            "QuickBooks bill CDC response parsed | entity_count=%s",
            len(((data.get("CDCResponse") or [{}])[0].get("QueryResponse") or [{}])) if isinstance(data, dict) else 0,
        )
        bills_by_id: Dict[str, Dict[str, Any]] = {}
        for cdc_block in data.get("CDCResponse") or []:
            for query_response in cdc_block.get("QueryResponse") or []:
                for bill in query_response.get("Bill") or []:
                    bill_id = str(bill.get(const.QBO_KEY_ID) or "").strip()
                    if bill_id:
                        bills_by_id[bill_id] = bill
        logger.debug(f"[BillImport] CDC extracted {len(bills_by_id)} bill(s): ids={list(bills_by_id.keys())}")
        return list(bills_by_id.values()), qbo_api.extract_intuit_tid(resp)

    def _get_qbo_api_base(self, env: str) -> str:
        from source.Utils import QBODiscoveryClient
        return QBODiscoveryClient.get_qbo_api_base(env)

    def _refresh_access_token(
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

    def _query_qbo_bills(self, *, base_url: str, realm_id: str, access_token: str, start_position: int, max_results: int) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        query_url = f"{base_url}/v3/company/{realm_id}/query"
        query = f"select * from Bill STARTPOSITION {int(start_position)} MAXRESULTS {int(max_results)}"
        resp = qbo_err.qbo_get(query_url, headers=headers, params={"query": query}, timeout=30)
        qbo_err.raise_for_qbo_response(resp, fallback_message="QBO bill query failed")
        data = qbo_err.safe_json(resp)
        bills = (data.get("QueryResponse") or {}).get("Bill") or []
        return (bills if isinstance(bills, list) else [], qbo_api.extract_intuit_tid(resp))

    def _fetch_qbo_bill_by_id(self, *, access_token: str, base_url: str, realm_id: str, bill_id: str) -> Dict[str, Any]:
        url = f"{base_url}/v3/company/{realm_id}/bill/{bill_id}"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        resp = qbo_err.qbo_get(url, headers=headers, timeout=60)
        qbo_err.raise_for_qbo_response(resp, fallback_message="QBO get bill failed")
        data = qbo_err.safe_json(resp)
        return data.get("Bill") or {}

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
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _get_qbo_last_updated_at(self, qbo_bill: Dict[str, Any]) -> Optional[datetime]:
        metadata = qbo_bill.get("MetaData") or {}
        return self._parse_timestamp(metadata.get("LastUpdatedTime") or metadata.get("CreateTime"))
