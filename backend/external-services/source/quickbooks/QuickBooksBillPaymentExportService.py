from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from source.dal.BillPaymentExpenseRepository import BillPaymentExpenseRepository
from source.dal.ChartOfAccountRepository import ChartOfAccountRepository
from source.dal.FinalPaymentExternalMapRepository import FinalPaymentExternalMapRepository
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.dal.ReceiptExternalMapRepository import ReceiptExternalMapRepository
from source.dal.ReceiptRepository import ReceiptRepository
from source.dal.VendorExternalMapRepository import VendorExternalMapRepository
from source.quickbooks.QuickBooksAuthentication import QuickBooksOAuthClient
from source.service.QuickBooksService import QuickBooksService
from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils import QuickBooksErrorHandler as qbo_err
from source.Utils.QuickBooksErrorHandler import QuickBooksAPIError
from source.Utils import constants as const
from source.Utils.auditlog import safe_audit_log

logger = logging.getLogger(__name__)


class QuickBooksBillPaymentExportService:
    def __init__(self) -> None:
        self.qbo_repo = QuickBooksRepository()
        self.qbo_service = QuickBooksService()
        self.payment_repo = BillPaymentExpenseRepository()
        self.payment_map_repo = FinalPaymentExternalMapRepository()
        self.receipt_repo = ReceiptRepository()
        self.receipt_map_repo = ReceiptExternalMapRepository()
        self.vendor_map_repo = VendorExternalMapRepository()
        self.coa_repo = ChartOfAccountRepository()

    @staticmethod
    def _build_reason(message: str, intuit_tid: Optional[str] = None, **extra: Any) -> str:
        payload: Dict[str, Any] = {"message": message}
        if intuit_tid:
            payload["intuit_tid"] = intuit_tid
        for key, value in extra.items():
            if value is None:
                continue
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    continue
            payload[key] = value
        return json.dumps(payload)

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()

    def _assert_mapping_realm(self, mapping: Optional[Dict[str, Any]], realm_id: str, label: str) -> None:
        if not isinstance(mapping, dict):
            return
        mapped_realm = self._clean(mapping.get("external_account_id"))
        if mapped_realm and mapped_realm != self._clean(realm_id):
            raise ValueError(
                f"The mapped {label} belongs to a different QuickBooks company. Re-sync it and retry."
            )

    def _payment_mapping_info(self, mapping: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        raw = (mapping or {}).get("additional_info")
        if isinstance(raw, dict):
            return dict(raw)
        text = self._clean(raw)
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _assert_payment_mapping_realm(
        self,
        mapping: Optional[Dict[str, Any]],
        realm_id: str,
    ) -> None:
        mapped_realm = self._clean(self._payment_mapping_info(mapping).get("qboRealmId"))
        if mapped_realm and mapped_realm != self._clean(realm_id):
            raise ValueError(
                "This bill payment mapping belongs to a different QuickBooks company. "
                "Clear or reassign the mapping, then retry."
            )

    def _build_payment_mapping_info(
        self,
        *,
        mapping: Optional[Dict[str, Any]],
        context: Dict[str, Any],
        realm_id: str,
    ) -> str:
        info = self._payment_mapping_info(mapping)
        info.update(
            {
                "qboRealmId": self._clean(realm_id),
                "qboAccountId": self._clean(context.get("qbo_payment_account_id")),
                "eligiblePayType": self._clean(context.get("pay_type")),
                "source": "export",
            }
        )
        optional_fields = {
            "qboAccountName": context.get("qbo_payment_account_name"),
            "qboAccountType": context.get("qbo_payment_account_type"),
            "qboAccountDetailType": context.get("qbo_payment_account_detail_type"),
        }
        for key, value in optional_fields.items():
            clean_value = self._clean(value)
            if clean_value:
                info[key] = clean_value
            else:
                info.pop(key, None)
        return json.dumps(info)

    def _build_outbound_reference(self, payment: Dict[str, Any]) -> Optional[str]:
        payment_id = self._clean(payment.get("id"))
        if payment_id:
            return payment_id
        ref_no = self._clean(payment.get("reference_no"))
        return ref_no if ref_no else None

    def export_bill_payments(self, *, tenant_id: str, service_id: str, limit: int = 500, offset: int = 0, object_id: Optional[str] = None, email: Optional[str] = None, user_role: Optional[str] = None) -> Dict[str, Any]:
        logger.debug("Bill payment export started | limit=%s offset=%s", limit, offset)
        safe_audit_log(tenant_id=tenant_id, action="bill_payment_export_started", user_id=object_id, email=email, user_role=user_role, status="started", resource_type="quickbooks_bill_payment")
        summary: Dict[str, Any] = {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": str(service_id),
            "requested": 0,
            "createdInQbo": 0,
            "updatedInQbo": 0,
            "mappedFromQbo": 0,
            "skippedAlreadyMapped": 0,
            "skippedQboNewer": 0,
            "failed": 0,
            const.KEY_FAILURES: [],
            const.KEY_RESULTS: [],
        }
        try:
            logger.debug("Loading QuickBooks connection for bill payment export")
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = (connection.get(const.KEY_ENV) or "sandbox").lower()
            conn_status = (connection.get(const.KEY_STATUS) or const.STATUS_CONNECTED).lower()
            logger.debug(
                "Bill payment export connection loaded | realm_present=%s status=%s refresh_token_present=%s",
                bool(realm_id), conn_status, bool(refresh_token),
            )
            if conn_status and conn_status != const.STATUS_CONNECTED:
                logger.debug("Bill payment export stopped because QuickBooks is not connected")
                return {const.KEY_OK: False, const.KEY_MESSAGE: f"QuickBooks is not connected (status={conn_status})"}
            if not refresh_token or not realm_id:
                logger.debug("Bill payment export stopped because connection data is incomplete")
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks not connected for this tenant (missing realmId/refreshToken)."}
            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)
            if not client_id or not client_secret:
                logger.debug("Bill payment export stopped because credentials are unavailable")
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks client credentials missing (ClientId/ClientSecret)."}
            logger.debug("Refreshing QuickBooks access token for bill payment export")
            access_token = self.refresh_access_token(tenant_id=tenant_id, service_id=service_id, connection=connection, client_id=client_id, client_secret=client_secret, refresh_token=refresh_token)
            logger.debug("QuickBooks access token refreshed for bill payment export")
            base_url = self.get_qbo_api_base(env)

            logger.debug("Loading bill payment export candidates")
            candidates = self.payment_repo.get_export_candidates(tenant_id=tenant_id, service_id=service_id, limit=limit, offset=offset)
            summary["requested"] = len(candidates)
            logger.debug("Bill payment export candidates loaded | count=%s", len(candidates))
            if not candidates:
                logger.debug("No bill payment export candidates found")
                summary[const.KEY_MESSAGE] = "No pending bill payment export candidates found."
                return summary

            for item in candidates:
                payment = item["payment"]
                mapping = item["mapping"] or {}
                mapping_id = int(mapping.get("id")) if mapping.get("id") is not None else None
                detail = {"paymentExpenseId": payment.get("id"), const.KEY_STATUS: None, const.KEY_ACTION: None, const.KEY_QBO_PAYMENT_ID: mapping.get("external_payment_id"), const.KEY_REASON: None, const.KEY_ERROR: None}
                logger.debug(
                    "Bill payment export candidate loaded | status=%s mapped=%s",
                    mapping.get("sync_status"),
                    bool(mapping.get("external_payment_id")),
                )
                try:
                    if not mapping_id:
                        raise ValueError("The payment sync mapping is missing. Run the payment sync again.")
                    self._assert_payment_mapping_realm(mapping, str(realm_id))
                    logger.debug("Building bill payment export context")
                    context = self._build_export_context(
                        tenant_id=tenant_id,
                        service_id=service_id,
                        realm_id=str(realm_id),
                        payment=payment,
                        mapping=mapping,
                    )
                    mapping_info = self._build_payment_mapping_info(
                        mapping=mapping,
                        context=context,
                        realm_id=str(realm_id),
                    )
                    logger.debug(
                        "Bill payment export context resolved | vendor_mapped=%s bill_mapped=%s account_mapped=%s pay_type=%s",
                        bool(context.get("qbo_vendor_id")), bool(context.get("external_bill_id")),
                        bool(context.get("qbo_payment_account_id")), context.get("pay_type"),
                    )
                    logger.debug("Building QuickBooks bill payment payload")
                    payload = self._build_qbo_bill_payment_payload(payment=payment, context=context)
                    logger.debug("QuickBooks bill payment payload built")
                    external_payment_id = self._clean(mapping.get("external_payment_id"))
                    if external_payment_id and self._clean(mapping.get("sync_status")).upper() not in {self.payment_repo.STATUS_PENDING_PUSH, self.payment_repo.STATUS_FAILED, ""}:
                        logger.debug("Skipping bill payment because its mapping is already synchronized")
                        summary["skippedAlreadyMapped"] += 1
                        detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "alreadyMapped"})
                        summary[const.KEY_RESULTS].append(detail)
                        continue

                    if external_payment_id:
                        try:
                            logger.debug("Reading current QuickBooks bill payment before update")
                            local_last = self.payment_repo.get_payment_latest_local_timestamp(payment, mapping)
                            qbo_current = self.get_bill_payment_in_qbo(access_token=access_token, base_url=base_url, realm_id=str(realm_id), bill_payment_id=external_payment_id)
                            qbo_last = self._get_qbo_last_updated_at(qbo_current)
                            logger.debug(
                                "Bill payment timestamp comparison completed | local_present=%s qbo_present=%s",
                                bool(local_last), bool(qbo_last),
                            )
                            if qbo_last and local_last and qbo_last > local_last:
                                logger.debug("Skipping bill payment because QuickBooks has the newer version")
                                self.payment_map_repo.mark_mapping_synced(tenant_id=tenant_id, mapping_id=mapping_id, external_payment_id=external_payment_id, external_invoice_id=context["external_bill_id"], external_account_id=context["qbo_payment_account_id"], additional_info=mapping_info, reason=self._build_reason("QuickBooks has a newer version of this payment. The next import will apply those changes here."))
                                logger.debug("Bill payment mapping updated after QuickBooks-newer decision")
                                summary["skippedQboNewer"] += 1
                                detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "skippedQboNewer"})
                                summary[const.KEY_RESULTS].append(detail)
                                continue
                            logger.debug("Updating existing QuickBooks bill payment")
                            updated = self.update_bill_payment_in_qbo(access_token=access_token, base_url=base_url, realm_id=str(realm_id), bill_payment_id=external_payment_id, payload=payload, sync_token=self._clean(qbo_current.get(const.QBO_KEY_SYNC_TOKEN)) or None)
                            updated_id = self._clean((updated or {}).get(const.QBO_KEY_ID)) or external_payment_id
                            logger.debug("QuickBooks bill payment update completed")
                            self.payment_map_repo.mark_mapping_synced(tenant_id=tenant_id, mapping_id=mapping_id, external_payment_id=updated_id, external_invoice_id=context["external_bill_id"], external_account_id=context["qbo_payment_account_id"], additional_info=mapping_info, reason=self._build_reason("Bill payment updated in QuickBooks successfully."))
                            logger.debug("Bill payment mapping marked synchronized after update")
                            summary["updatedInQbo"] += 1
                            detail.update({const.KEY_STATUS: "success", const.KEY_ACTION: "updated in QBO", const.KEY_QBO_PAYMENT_ID: updated_id})
                            summary[const.KEY_RESULTS].append(detail)
                            continue
                        except Exception as exc:
                            if not self._is_qbo_stale_reference_error(str(exc)):
                                raise
                            logger.info(
                                "Mapped QuickBooks bill payment no longer exists; recreating it",
                            )

                    logger.debug("Creating QuickBooks bill payment")
                    created = self.create_bill_payment_in_qbo(access_token=access_token, base_url=base_url, realm_id=str(realm_id), payload=payload)
                    created_id = self._clean((created or {}).get(const.QBO_KEY_ID))
                    logger.debug("QuickBooks bill payment creation completed | id_present=%s", bool(created_id))
                    if not created_id:
                        raise RuntimeError("QuickBooks created no identifiable bill payment record")
                    self.payment_map_repo.mark_mapping_synced(tenant_id=tenant_id, mapping_id=mapping_id, external_payment_id=created_id, external_invoice_id=context["external_bill_id"], external_account_id=context["qbo_payment_account_id"], additional_info=mapping_info, reason=self._build_reason("Bill payment created in QuickBooks successfully."))
                    logger.debug("Bill payment mapping marked synchronized after creation")
                    summary["createdInQbo"] += 1
                    detail.update({const.KEY_STATUS: "success", const.KEY_ACTION: "created in QBO", const.KEY_QBO_PAYMENT_ID: created_id})
                    summary[const.KEY_RESULTS].append(detail)
                except Exception as exc:
                    intuit_tid = exc.intuit_tid if isinstance(exc, QuickBooksAPIError) else None
                    if isinstance(exc, QuickBooksAPIError):
                        msg = exc.user_message
                    elif isinstance(exc, ValueError):
                        msg = str(exc)
                    else:
                        msg = "The bill payment could not be exported because of an unexpected error."
                    logger.exception("Bill payment export record failed")
                    if mapping_id:
                        self.payment_map_repo.mark_mapping_failed(tenant_id=tenant_id, mapping_id=mapping_id, reason=self._build_reason(msg, intuit_tid=intuit_tid))
                        logger.debug("Bill payment mapping marked failed")
                    summary["failed"] += 1
                    qbo_api.append_failure(summary, {"paymentExpenseId": payment.get("id"), const.KEY_ERROR: msg})
                    detail.update({const.KEY_STATUS: "failed", const.KEY_ACTION: "error", const.KEY_ERROR: msg})
                    summary[const.KEY_RESULTS].append(detail)
            logger.debug(
                "Bill payment export completed | created=%s updated=%s skipped_mapped=%s skipped_qbo_newer=%s failed=%s",
                summary["createdInQbo"], summary["updatedInQbo"],
                summary["skippedAlreadyMapped"], summary["skippedQboNewer"], summary["failed"],
            )
            return summary
        except Exception:
            logger.exception("Bill payment export batch failed")
            raise
        finally:
            safe_audit_log(tenant_id=tenant_id, action="bill_payment_export_completed" if summary.get(const.KEY_OK, True) else "bill_payment_export_failed", user_id=object_id, email=email, user_role=user_role, status="success" if summary.get(const.KEY_OK, True) else "failed", resource_type="quickbooks_bill_payment")

    def _build_export_context(self, *, tenant_id: str, service_id: str, realm_id: str, payment: Dict[str, Any], mapping: Dict[str, Any]) -> Dict[str, Any]:
        logger.debug("Resolving bill payment export context")
        receipt_id = payment.get("reference_id")
        if receipt_id is None:
            raise ValueError("This payment is not linked to a bill. Please link it to a bill and try again.")
        receipt = self.receipt_repo.get_current_receipt_by_id(tenant_id, int(receipt_id))
        logger.debug("Bill payment receipt resolved | found=%s", bool(receipt))
        if not receipt:
            raise ValueError("The bill linked to this payment could not be found.")
        receipt_mapping = self.receipt_map_repo.get_by_receipt_id(tenant_id, service_id, int(receipt_id))
        logger.debug("Bill payment receipt mapping resolved | found=%s", bool(receipt_mapping))
        if not receipt_mapping or not self._clean(receipt_mapping.get("external_receipt_id")):
            raise ValueError("The linked bill hasn't been synced to QuickBooks yet. Run a bill sync first, then retry.")
        self._assert_mapping_realm(receipt_mapping, realm_id, "bill")
        vendor_id = receipt.get("vendor_id")
        if vendor_id is None:
            raise ValueError("The linked bill is missing a vendor. Assign a vendor to the bill and try again.")
        vendor_mapping = self.vendor_map_repo.get_by_vendor_id(tenant_id, service_id, int(vendor_id))
        qbo_vendor_id = self._clean((vendor_mapping or {}).get("external_vendor_id"))
        logger.debug("Bill payment vendor mapping resolved | found=%s", bool(qbo_vendor_id))
        if not qbo_vendor_id:
            raise ValueError("The vendor on this bill hasn't been synced to QuickBooks yet. Run a vendor sync first, then retry.")
        self._assert_mapping_realm(vendor_mapping, realm_id, "vendor")

        selected_qbo_account_id = self._clean((mapping or {}).get("external_account_id"))
        logger.debug("Bill payment account selection loaded | selected=%s", bool(selected_qbo_account_id))
        if not selected_qbo_account_id:
            raise ValueError("No QuickBooks payment account is assigned for this bill payment. Assign a payment account and try again.")
        qbo_payment_account = self.coa_repo.get_bill_payment_eligible_account_by_external_id(
            tenant_id,
            service_id=str(service_id),
            external_id=selected_qbo_account_id,
            external_acc_id=realm_id,
        )
        logger.debug("Bill payment account resolved | found=%s", bool(qbo_payment_account))
        if not qbo_payment_account:
            raise ValueError("The assigned payment account is no longer active or valid in QuickBooks. Re-sync your accounts and reassign the payment account, then try again.")
        pay_type = self.coa_repo.get_bill_payment_pay_type(qbo_payment_account)
        logger.debug("Bill payment pay type resolved | pay_type=%s", pay_type)
        if pay_type not in {"Check", "CreditCard"}:
            raise ValueError("The assigned payment account is not valid for bill payments (must be a bank or credit card account).")
        return {
            "receipt": receipt,
            "receipt_mapping": receipt_mapping,
            "vendor_mapping": vendor_mapping,
            "qbo_vendor_id": qbo_vendor_id,
            "qbo_payment_account_id": self._clean(qbo_payment_account.get("external_id")),
            "qbo_payment_account_name": qbo_payment_account.get("name"),
            "qbo_payment_account_type": qbo_payment_account.get("account_type"),
            "qbo_payment_account_detail_type": qbo_payment_account.get("detail_type"),
            "external_bill_id": self._clean(receipt_mapping.get("external_receipt_id")),
            "payment_date": self._resolve_bill_payment_date(payment=payment, receipt=receipt),
            "pay_type": pay_type,
        }

    def _resolve_bill_payment_date(self, *, payment: Dict[str, Any], receipt: Dict[str, Any]) -> Optional[str]:
        info = self.receipt_repo.parse_additional_info(receipt.get("receipt_additional_info"))
        for value in (
            receipt.get("paid_date"),
            info.get("billPaymentDate"),
            info.get("paymentDate"),
            payment.get("updated_at"),
            payment.get("created_at"),
        ):
            parsed = self.payment_repo._to_yyyy_mm_dd(value)
            if parsed:
                return parsed
        return None

    def _build_qbo_bill_payment_payload(self, *, payment: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        amount = self.payment_repo._to_float(payment.get("amount"))
        if amount is None or amount <= 0:
            raise ValueError("Bill payment amount must be greater than zero. Update the amount and try again.")
        txn_date = context.get("payment_date") or self.payment_repo._to_yyyy_mm_dd(payment.get("updated_at") or payment.get("created_at"))
        outbound_reference = self._build_outbound_reference(payment)
        payload: Dict[str, Any] = {
            "VendorRef": {"value": context["qbo_vendor_id"]},
            "TotalAmt": amount,
            "TxnDate": txn_date,
            "DocNumber": outbound_reference,
            "PrivateNote": outbound_reference,
            "Line": [{"Amount": amount, "LinkedTxn": [{"TxnId": context["external_bill_id"], "TxnType": "Bill"}]}],
            "PayType": context["pay_type"],
        }
        if context["pay_type"] == "CreditCard":
            payload["CreditCardPayment"] = {"CCAccountRef": {"value": context["qbo_payment_account_id"]}}
        else:
            payload["CheckPayment"] = {"BankAccountRef": {"value": context["qbo_payment_account_id"]}}
        return {k: v for k, v in payload.items() if v is not None}

    def get_qbo_api_base(self, env: str) -> str:
        from source.Utils import QBODiscoveryClient
        return QBODiscoveryClient.get_qbo_api_base(env)

    def refresh_access_token(self, *, tenant_id: str, service_id: str, connection: Optional[Dict[str, Any]], client_id: str, client_secret: str, refresh_token: str) -> str:
        return QuickBooksOAuthClient(repository=self.qbo_repo).refresh_access_token(tenant_id=str(tenant_id), service_id=str(service_id), client_id=client_id, client_secret=client_secret, refresh_token=refresh_token, token_url=self.qbo_service.token_url, current_connection=connection)

    def get_bill_payment_in_qbo(self, *, access_token: str, base_url: str, realm_id: str, bill_payment_id: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/billpayment/{bill_payment_id}"
        try:
            resp = qbo_err.qbo_get(url, headers=headers, timeout=30)
            qbo_err.raise_for_qbo_response(resp, fallback_message="QBO get bill payment failed")
            result = (qbo_err.safe_json(resp) or {}).get("BillPayment") or {}
            logger.debug("QuickBooks bill payment lookup completed | found=%s", bool(result))
            return result
        except Exception as exc:
            logger.exception("QuickBooks bill payment lookup failed")
            raise

    def create_bill_payment_in_qbo(self, *, access_token: str, base_url: str, realm_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/billpayment"
        logger.debug("Creating QuickBooks bill payment")
        try:
            resp = qbo_err.qbo_post(url, headers=headers, json=payload, timeout=30)
            qbo_err.raise_for_qbo_response(resp, success_statuses=(200, 201), fallback_message="QBO create bill payment failed")
            result = (qbo_err.safe_json(resp) or {}).get("BillPayment") or {}
            logger.debug("QuickBooks bill payment create response parsed | id_present=%s", bool(result.get("Id")))
            return result
        except Exception as exc:
            logger.exception("QuickBooks bill payment create failed")
            raise

    def update_bill_payment_in_qbo(self, *, access_token: str, base_url: str, realm_id: str, bill_payment_id: str, payload: Dict[str, Any], sync_token: Optional[str]) -> Dict[str, Any]:
        current_token = self._clean(sync_token)
        if not current_token:
            logger.debug("QuickBooks bill payment SyncToken missing; loading current record")
            existing = self.get_bill_payment_in_qbo(access_token=access_token, base_url=base_url, realm_id=realm_id, bill_payment_id=bill_payment_id)
            current_token = self._clean(existing.get(const.QBO_KEY_SYNC_TOKEN))
        if not current_token:
            raise Exception("QBO bill payment update failed: missing SyncToken")
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/billpayment"
        body = {const.QBO_KEY_ID: str(bill_payment_id), const.QBO_KEY_SYNC_TOKEN: str(current_token), "sparse": True, **(payload or {})}
        logger.debug("Updating QuickBooks bill payment")
        try:
            resp = qbo_err.qbo_post(url, headers=headers, json=body, timeout=30)
            qbo_err.raise_for_qbo_response(resp, success_statuses=(200, 201), fallback_message="QBO update bill payment failed")
            result = (qbo_err.safe_json(resp) or {}).get("BillPayment") or {}
            logger.debug("QuickBooks bill payment update response parsed | id_present=%s", bool(result.get("Id")))
            return result
        except Exception as exc:
            logger.exception("QuickBooks bill payment update failed")
            raise

    def _extract_linked_bill_id(self, qbo_bill_payment: Dict[str, Any]) -> Optional[str]:
        for line in qbo_bill_payment.get("Line") or []:
            linked = line.get("LinkedTxn") or []
            if isinstance(linked, dict):
                linked = [linked]
            for item in linked:
                if self._clean(item.get("TxnType")).lower() == "bill":
                    return self._clean(item.get("TxnId")) or None
        return None

    def _extract_payment_account_id(self, qbo_bill_payment: Dict[str, Any]) -> Optional[str]:
        check_payment = qbo_bill_payment.get("CheckPayment") or {}
        credit_card_payment = qbo_bill_payment.get("CreditCardPayment") or {}
        bank_ref = (check_payment.get("BankAccountRef") or {}).get("value")
        cc_ref = (credit_card_payment.get("CCAccountRef") or {}).get("value")
        return self._clean(bank_ref or cc_ref) or None

    def _parse_timestamp(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        raw = str(value).strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(raw)
        except Exception:
            return None

    def _get_qbo_last_updated_at(self, qbo_bill_payment: Dict[str, Any]) -> Optional[datetime]:
        meta = qbo_bill_payment.get("MetaData") or {}
        return self._parse_timestamp(meta.get("LastUpdatedTime") or meta.get("CreateTime"))

    def _is_qbo_stale_reference_error(self, message: str) -> bool:
        text = self._clean(message).lower()
        return "[610]" in text or "object not found" in text or "made inactive" in text or "has been deleted" in text
