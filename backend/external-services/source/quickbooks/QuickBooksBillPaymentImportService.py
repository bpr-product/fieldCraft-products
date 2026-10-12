from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from source.dal.BillPaymentExpenseRepository import BillPaymentExpenseRepository
from source.dal.FinalPaymentExternalMapRepository import FinalPaymentExternalMapRepository
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.dal.ReceiptExternalMapRepository import ReceiptExternalMapRepository
from source.quickbooks.QuickBooksAuthentication import QuickBooksOAuthClient
from source.quickbooks.qbo_query_utils import escape_qbo_literal
from source.service.QuickBooksService import QuickBooksService
from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils import QuickBooksErrorHandler as qbo_err
from source.Utils.QuickBooksErrorHandler import QuickBooksAPIError
from source.Utils import constants as const
from source.Utils.auditlog import safe_audit_log

logger = logging.getLogger(__name__)


class QuickBooksBillPaymentImportService:
    def __init__(self) -> None:
        self.qbo_repo = QuickBooksRepository()
        self.qbo_service = QuickBooksService()
        self.payment_repo = BillPaymentExpenseRepository()
        self.payment_map_repo = FinalPaymentExternalMapRepository()
        self.receipt_map_repo = ReceiptExternalMapRepository()

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

    @staticmethod
    def _build_account_additional_info(
        qbo_payment_account: Dict[str, Optional[str]],
        realm_id: str,
    ) -> Optional[str]:
        account_id = (qbo_payment_account.get("external_account_id") or "").strip()
        if not account_id:
            return None
        account_type = (qbo_payment_account.get("account_type") or "").strip()
        pay_type = "CreditCard" if account_type.lower() == "credit card" else "Check"
        payload: Dict[str, Any] = {
            "qboRealmId": str(realm_id or "").strip(),
            "qboAccountId": account_id,
            "eligiblePayType": pay_type,
            "source": "import",
        }
        for key, field in [("qboAccountName", "account_name"), ("qboAccountType", "account_type"), ("qboAccountDetailType", "account_detail_type")]:
            val = (qbo_payment_account.get(field) or "").strip()
            if val:
                payload[key] = val
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

    def _assert_payment_mapping_realm(
        self,
        mapping: Optional[Dict[str, Any]],
        realm_id: str,
    ) -> None:
        raw = (mapping or {}).get("additional_info")
        if isinstance(raw, dict):
            info = raw
        else:
            try:
                parsed = json.loads(self._clean(raw)) if self._clean(raw) else {}
            except (TypeError, ValueError):
                parsed = {}
            info = parsed if isinstance(parsed, dict) else {}
        mapped_realm = self._clean(info.get("qboRealmId"))
        if mapped_realm and mapped_realm != self._clean(realm_id):
            raise ValueError(
                "This bill payment mapping belongs to a different QuickBooks company. "
                "Clear the old mapping before importing this payment."
            )

    def import_bill_payments(self, *, tenant_id: str, service_id: str, page_size: int = 100, start_position: int = 1, max_payments: Optional[int] = None, dry_run: bool = False, changed_since_override: Optional[str] = None, force_full: bool = False, object_id: Optional[str] = None, email: Optional[str] = None, user_role: Optional[str] = None) -> Dict[str, Any]:
        logger.debug(
            "Bill payment import started | page_size=%s dry_run=%s force_full=%s changed_since_supplied=%s",
            page_size, dry_run, force_full, changed_since_override is not None,
        )
        safe_audit_log(tenant_id=tenant_id, action="bill_payment_import_started", user_id=object_id, email=email, user_role=user_role, status="started", resource_type="quickbooks_bill_payment")
        summary: Dict[str, Any] = {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": str(service_id),
            "processed": 0,
            "imported": 0,
            "updated": 0,
            "resolvedImportFailed": 0,
            "skippedUnchanged": 0,
            "importDeferred": 0,
            "failed": 0,
            const.KEY_FAILURES: [],
            const.KEY_RESULTS: [],
        }
        try:
            logger.debug("Loading QuickBooks connection for bill payment import")
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = (connection.get(const.KEY_ENV) or "sandbox").lower()
            logger.debug(
                "Bill payment import connection loaded | realm_present=%s refresh_token_present=%s",
                bool(realm_id), bool(refresh_token),
            )
            if not refresh_token or not realm_id:
                logger.debug("Bill payment import stopped because connection data is incomplete")
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks not connected for this tenant."}
            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)
            if not client_id or not client_secret:
                logger.debug("Bill payment import stopped because credentials are unavailable")
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks client credentials missing (ClientId/ClientSecret)."}
            logger.debug("Refreshing QuickBooks access token for bill payment import")
            access_token = self.refresh_access_token(tenant_id=tenant_id, service_id=service_id, connection=connection, client_id=client_id, client_secret=client_secret, refresh_token=refresh_token)
            logger.debug("QuickBooks access token refreshed for bill payment import")
            base_url = self.get_qbo_api_base(env)
            pending_retry_ids = self.payment_map_repo.get_open_import_failed_external_payment_ids(tenant_id, service_id)
            changed_since = None if force_full else self._coerce_changed_since(changed_since_override)
            if not force_full and self._clean(changed_since_override) and not changed_since:
                return {
                    const.KEY_OK: False,
                    const.KEY_MESSAGE: "changed_since_override must be a valid ISO-8601 timestamp.",
                }
            logger.debug(
                "Bill payment import fetch prepared | tenant_id=%s service_id=%s force_full=%s watermark_present=%s retry_count=%s",
                tenant_id, service_id, force_full, bool(changed_since), len(pending_retry_ids),
            )
            logger.debug("Fetching QuickBooks bill payments")
            fetched = self.fetch_bill_payments(access_token=access_token, base_url=base_url, realm_id=str(realm_id), changed_since=changed_since, page_size=page_size, start_position=start_position, max_payments=max_payments)
            if pending_retry_ids:
                logger.debug("Fetching deferred bill payments | count=%s", len(pending_retry_ids))
                fetched_by_id = self.fetch_bill_payments_by_ids(access_token=access_token, base_url=base_url, realm_id=str(realm_id), payment_ids=pending_retry_ids)
                logger.debug("Deferred bill payment fetch completed | count=%s", len(fetched_by_id))
                by_id = {self._clean(item.get(const.QBO_KEY_ID)): item for item in fetched}
                for item in fetched_by_id:
                    qid = self._clean(item.get(const.QBO_KEY_ID))
                    if qid and qid not in by_id:
                        fetched.append(item)
                        by_id[qid] = item
            logger.debug("QuickBooks bill payments fetched | count=%s", len(fetched))
            if not fetched:
                logger.debug("No QuickBooks bill payments found")
                summary[const.KEY_MESSAGE] = "No QuickBooks bill payments found for import."
                return summary

            for qbo_payment in fetched:
                qbo_payment_id = self._clean(qbo_payment.get(const.QBO_KEY_ID))
                detail = {"qboBillPaymentId": qbo_payment_id, const.KEY_STATUS: None, const.KEY_ACTION: None, const.KEY_REASON: None, const.KEY_ERROR: None}
                linked_bill_id: Optional[str] = None
                qbo_payment_account: Dict[str, Optional[str]] = {}
                receipt_id: Optional[int] = None
                current_payment: Optional[Dict[str, Any]] = None
                try:
                    summary["processed"] += 1
                    if not qbo_payment_id:
                        raise ValueError("QuickBooks bill payment is missing its Id")
                    linked_bill_id = self._extract_linked_bill_id(qbo_payment)
                    qbo_payment_account = self._extract_payment_account_meta(qbo_payment)
                    logger.debug(
                        "Bill payment import record loaded | linked_bill_present=%s account_present=%s",
                        bool(linked_bill_id),
                        bool(qbo_payment_account.get("external_account_id")),
                    )
                    if not linked_bill_id:
                        msg = "This payment has no linked bill in QuickBooks. Link it to a bill in QuickBooks and sync again."
                        logger.debug("Bill payment import deferred because required references are missing")
                        if not dry_run:
                            self.payment_map_repo.upsert_import_failed_mapping(
                                tenant_id=tenant_id,
                                service_id=service_id,
                                external_payment_id=qbo_payment_id,
                                external_invoice_id=None,
                                external_account_id=qbo_payment_account.get("external_account_id"),
                                reason=self._build_reason(msg),
                            )
                        summary["failed"] += 1
                        qbo_api.append_failure(summary, {"qboBillPaymentId": qbo_payment_id, const.KEY_ERROR: msg})
                        detail.update({const.KEY_STATUS: "failed", const.KEY_ACTION: "importDeferred", const.KEY_ERROR: msg, const.KEY_REASON: self._build_reason(msg)})
                        summary[const.KEY_RESULTS].append(detail)
                        continue

                    logger.debug("Resolving linked bill mapping")
                    receipt_mapping = self.receipt_map_repo.get_by_external_id(tenant_id, service_id, linked_bill_id)
                    logger.debug("Linked bill mapping resolved | found=%s", bool(receipt_mapping))
                    if not receipt_mapping or receipt_mapping.get("receipt_id") is None:
                        qbo_linked_bill = self.fetch_bill_by_id(
                            access_token=access_token,
                            base_url=base_url,
                            realm_id=str(realm_id),
                            bill_id=linked_bill_id,
                        )
                        reason = self._build_linked_bill_not_mapped_reason(
                            qbo_bill_payment=qbo_payment,
                            qbo_payment_id=qbo_payment_id,
                            linked_bill_id=linked_bill_id,
                            qbo_linked_bill=qbo_linked_bill,
                        )
                        msg = reason["message"]
                        reason_json = self._build_reason(**reason)
                        logger.debug("Bill payment import deferred because the linked bill is unavailable")
                        if not dry_run:
                            self.payment_map_repo.upsert_import_failed_mapping(
                                tenant_id=tenant_id,
                                service_id=service_id,
                                external_payment_id=qbo_payment_id,
                                external_invoice_id=linked_bill_id,
                                external_account_id=qbo_payment_account.get("external_account_id"),
                                reason=reason_json,
                            )
                        summary["importDeferred"] += 1
                        detail.update({const.KEY_STATUS: "deferred", const.KEY_ACTION: "importDeferred", const.KEY_REASON: reason_json})
                        summary[const.KEY_RESULTS].append(detail)
                        continue
                    self._assert_mapping_realm(receipt_mapping, str(realm_id), "bill")
                    receipt_id = int(receipt_mapping["receipt_id"])
                    receipt = self.payment_repo.receipt_repo.get_current_receipt_by_id(tenant_id, receipt_id)
                    logger.debug("Building FIELDCRAFT bill payment payload")
                    payload = self.payment_repo.build_payment_payload_from_qbo(qbo_payment, receipt_id=receipt_id, chart_of_account_id=(receipt or {}).get("chart_of_account_id"), project_id=(receipt or {}).get("project_id"), source_updated_at=self._get_qbo_last_updated_at(qbo_payment))
                    logger.debug(
                        "FIELDCRAFT bill payment payload built | amount_present=%s account_present=%s project_present=%s",
                        payload.get("amount") is not None, payload.get("chart_of_account_id") is not None,
                        payload.get("project_id") is not None,
                    )
                    logger.debug("Looking up existing bill payment mapping")
                    current_mapping = self.payment_map_repo.get_by_external_id(
                        tenant_id, service_id, qbo_payment_id
                    )
                    self._assert_payment_mapping_realm(current_mapping, str(realm_id))
                    logger.debug(
                        "Bill payment mapping lookup completed | found=%s linked_payment_present=%s status=%s",
                        bool(current_mapping),
                        (current_mapping or {}).get("payment_expense_id") is not None,
                        (current_mapping or {}).get("sync_status"),
                    )
                    if current_mapping and current_mapping.get("payment_expense_id") is not None:
                        current_payment = self.payment_repo.get_payment_expense_by_id(tenant_id, int(current_mapping["payment_expense_id"]))
                    logger.debug("Existing FIELDCRAFT bill payment resolved | found=%s", bool(current_payment))

                    if current_payment and not self.payment_repo.sync_projection_changed(current_payment, payload):
                        logger.debug("Bill payment is unchanged")
                        if not dry_run:
                            if current_mapping and current_mapping.get("id"):
                                self.payment_map_repo.mark_mapping_synced(tenant_id=tenant_id, mapping_id=int(current_mapping["id"]), external_payment_id=qbo_payment_id, external_invoice_id=linked_bill_id, external_account_id=qbo_payment_account.get("external_account_id"), additional_info=self._build_account_additional_info(qbo_payment_account, str(realm_id)), reason=self._build_reason("No changes detected — this payment is already up to date."))
                                logger.debug("Bill payment mapping marked synchronized for unchanged record")
                            else:
                                logger.debug("Creating missing bill payment mapping for existing record")
                                self.payment_map_repo.insert_mapping(tenant_id=tenant_id, service_id=service_id, payment_expense_id=int(current_payment["id"]), external_payment_id=qbo_payment_id, external_invoice_id=linked_bill_id, external_account_id=qbo_payment_account.get("external_account_id"), additional_info=self._build_account_additional_info(qbo_payment_account, str(realm_id)), sync_status=self.payment_repo.STATUS_SYNCED, reason=self._build_reason("Payment matched and linked to an existing FIELDCRAFT record."))
                                logger.debug("Missing bill payment mapping created")
                        else:
                            logger.debug("Dry run: skipped mapping update for unchanged bill payment")
                        summary["skippedUnchanged"] += 1
                        detail.update({const.KEY_STATUS: "success", const.KEY_ACTION: "unchanged", "paymentExpenseId": current_payment["id"]})
                        summary[const.KEY_RESULTS].append(detail)
                        continue

                    if dry_run:
                        logger.debug("Dry run: skipped bill payment write")
                        detail.update({const.KEY_STATUS: "skipped", const.KEY_ACTION: "dryRun", "paymentExpenseId": (current_payment or {}).get("id")})
                        summary[const.KEY_RESULTS].append(detail)
                        continue

                    if current_payment:
                        logger.debug("Updating existing FIELDCRAFT bill payment")
                        updated = self.payment_repo.update_payment_expense_from_qbo(tenant_id, payment_expense_id=int(current_payment["id"]), payload=payload)
                        logger.debug("FIELDCRAFT bill payment update completed")
                        self.payment_repo.receipt_repo.mark_receipt_paid(tenant_id, receipt_id, qbo_payment.get("TxnDate"))
                        logger.debug("Linked receipt marked paid after bill payment update")
                        if current_mapping and current_mapping.get("id"):
                            self.payment_map_repo.mark_mapping_synced(tenant_id=tenant_id, mapping_id=int(current_mapping["id"]), external_payment_id=qbo_payment_id, external_invoice_id=linked_bill_id, external_account_id=qbo_payment_account.get("external_account_id"), additional_info=self._build_account_additional_info(qbo_payment_account, str(realm_id)), reason=self._build_reason("Payment updated to match the latest changes from QuickBooks."))
                            logger.debug("Bill payment mapping marked synchronized after update")
                            if self._clean(current_mapping.get("sync_status")).upper() == self.payment_repo.STATUS_IMPORT_FAILED:
                                summary["resolvedImportFailed"] += 1
                                logger.debug("Deferred bill payment import resolved")
                        else:
                            logger.debug("Creating bill payment mapping after update")
                            self.payment_map_repo.insert_mapping(tenant_id=tenant_id, service_id=service_id, payment_expense_id=int(updated["id"]), external_payment_id=qbo_payment_id, external_invoice_id=linked_bill_id, external_account_id=qbo_payment_account.get("external_account_id"), additional_info=self._build_account_additional_info(qbo_payment_account, str(realm_id)), sync_status=self.payment_repo.STATUS_SYNCED, reason=self._build_reason("Payment matched and linked to an existing payment record."))
                            logger.debug("Bill payment mapping created after update")
                        summary["updated"] += 1
                        detail.update({const.KEY_STATUS: "success", const.KEY_ACTION: "updatedInFieldCraft", "paymentExpenseId": updated["id"]})
                        summary[const.KEY_RESULTS].append(detail)
                        continue

                    logger.debug("Creating FIELDCRAFT bill payment")
                    new_id = self.payment_repo.create_payment_expense_for_tenant(tenant_id, payload)
                    logger.debug("FIELDCRAFT bill payment creation completed")
                    self.payment_repo.receipt_repo.mark_receipt_paid(tenant_id, receipt_id, qbo_payment.get("TxnDate"))
                    logger.debug("Linked receipt marked paid after bill payment creation")

                    if current_mapping and current_mapping.get("id"):
                        self.payment_map_repo.mark_mapping_synced(
                            tenant_id=tenant_id,
                            mapping_id=int(current_mapping["id"]),
                            external_payment_id=qbo_payment_id,
                            external_invoice_id=linked_bill_id,
                            external_account_id=qbo_payment_account.get("external_account_id"),
                            additional_info=self._build_account_additional_info(qbo_payment_account, str(realm_id)),
                            reason=self._build_reason("Bill payment imported from QuickBooks."),
                            payment_expense_id=int(new_id),
                        )
                        logger.debug("Existing bill payment mapping linked to the imported record")
                        if self._clean(current_mapping.get("sync_status")).upper() == self.payment_repo.STATUS_IMPORT_FAILED:
                            summary["resolvedImportFailed"] += 1
                            logger.debug("Deferred bill payment import resolved")
                    else:
                        self.payment_map_repo.insert_mapping(
                            tenant_id=tenant_id,
                            service_id=service_id,
                            payment_expense_id=int(new_id),
                            external_payment_id=qbo_payment_id,
                            external_invoice_id=linked_bill_id,
                            external_account_id=qbo_payment_account.get("external_account_id"),
                            additional_info=self._build_account_additional_info(qbo_payment_account, str(realm_id)),
                            sync_status=self.payment_repo.STATUS_SYNCED,
                            reason=self._build_reason("Bill payment imported from QuickBooks."),
                        )
                        logger.debug("Bill payment mapping created for new record")

                    summary["imported"] += 1
                    logger.debug("FIELDCRAFT bill payment created from QuickBooks")
                    detail.update({const.KEY_STATUS: "success", const.KEY_ACTION: "createdInFieldCraft", "paymentExpenseId": new_id})
                    summary[const.KEY_RESULTS].append(detail)
                except Exception as exc:
                    intuit_tid = exc.intuit_tid if isinstance(exc, QuickBooksAPIError) else None
                    if isinstance(exc, QuickBooksAPIError):
                        msg = exc.user_message
                    elif isinstance(exc, ValueError):
                        msg = str(exc)
                    else:
                        msg = "The bill payment could not be imported because of an unexpected error."
                    logger.exception("Bill payment import record failed")
                    try:
                        self.payment_map_repo.upsert_import_failed_mapping(
                            tenant_id=tenant_id,
                            service_id=service_id,
                            external_payment_id=qbo_payment_id,
                            external_invoice_id=linked_bill_id or self._extract_linked_bill_id(qbo_payment),
                            external_account_id=(qbo_payment_account or self._extract_payment_account_meta(qbo_payment)).get("external_account_id"),
                            payment_expense_id=int(current_payment["id"]) if current_payment and current_payment.get("id") is not None else None,
                            receipt_id=receipt_id,
                            reason=self._build_reason(msg, intuit_tid=intuit_tid),
                        )
                        logger.debug("Deferred bill payment mapping saved")
                    except Exception:
                        logger.exception("Failed to persist bill payment import failure")
                    summary["failed"] += 1
                    qbo_api.append_failure(summary, {"qboBillPaymentId": qbo_payment_id, const.KEY_ERROR: msg})
                    detail.update({const.KEY_STATUS: "failed", const.KEY_ACTION: "error", const.KEY_ERROR: msg})
                    summary[const.KEY_RESULTS].append(detail)
            logger.debug(
                "Bill payment import completed | imported=%s updated=%s unchanged=%s deferred=%s resolved=%s failed=%s",
                summary["imported"], summary["updated"], summary["skippedUnchanged"],
                summary["importDeferred"], summary["resolvedImportFailed"], summary["failed"],
            )
            return summary
        except Exception:
            logger.exception("Bill payment import batch failed")
            raise
        finally:
            safe_audit_log(tenant_id=tenant_id, action="bill_payment_import_completed" if summary.get(const.KEY_OK, True) else "bill_payment_import_failed", user_id=object_id, email=email, user_role=user_role, status="success" if summary.get(const.KEY_OK, True) else "failed", resource_type="quickbooks_bill_payment")

    def fetch_bill_payments(self, *, access_token: str, base_url: str, realm_id: str, changed_since: Optional[str], page_size: int, start_position: int, max_payments: Optional[int]) -> List[Dict[str, Any]]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        query_url = f"{base_url}/v3/company/{realm_id}/query"
        results: List[Dict[str, Any]] = []
        position = max(int(start_position or 1), 1)
        page_size = min(max(int(page_size or 100), 1), 1000)
        requested_max = max_payments if max_payments is not None and int(max_payments) > 0 else None
        logger.debug(
            "Fetching QuickBooks bill payments | incremental=%s page_size=%s start_position=%s max_payments=%s",
            bool(changed_since), page_size, position, requested_max,
        )
        while True:
            parts = ["select * from BillPayment"]
            if changed_since:
                parts.append(f"where MetaData.LastUpdatedTime > '{escape_qbo_literal(changed_since)}'")
            parts.append(f"STARTPOSITION {position} MAXRESULTS {int(page_size)}")
            query = " ".join(parts)
            resp = qbo_err.qbo_get(query_url, headers=headers, params={"query": query}, timeout=30)
            logger.debug("QuickBooks bill payment query response | status=%s", resp.status_code)
            qbo_err.raise_for_qbo_response(resp, fallback_message="QBO bill payment query failed")
            data = qbo_err.safe_json(resp)
            logger.debug("QuickBooks bill payment query parsed | has_query_response=%s", bool(data.get("QueryResponse")))
            batch = (data.get("QueryResponse") or {}).get("BillPayment") or []
            if not isinstance(batch, list) or not batch:
                logger.debug("QuickBooks bill payment page returned no records")
                break
            logger.debug("QuickBooks bill payment page parsed | count=%s", len(batch))
            results.extend(batch)
            if requested_max is not None and len(results) >= requested_max:
                logger.debug("QuickBooks bill payment fetch reached requested limit")
                return results[:requested_max]
            if len(batch) < int(page_size):
                logger.debug("QuickBooks bill payment fetch reached final page")
                break
            position += int(page_size)
        logger.debug("QuickBooks bill payment fetch completed | count=%s", len(results))
        return results

    def fetch_bill_payments_by_ids(self, *, access_token: str, base_url: str, realm_id: str, payment_ids: List[str]) -> List[Dict[str, Any]]:
        logger.debug("Fetching deferred QuickBooks bill payments | count=%s", len(payment_ids))
        results: List[Dict[str, Any]] = []
        for payment_id in payment_ids:
            pid = self._clean(payment_id)
            if not pid:
                continue
            headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
            url = f"{base_url}/v3/company/{realm_id}/billpayment/{pid}"
            try:
                resp = qbo_err.qbo_get(url, headers=headers, timeout=30)
                logger.debug("QuickBooks bill payment lookup response | status=%s", resp.status_code)
                qbo_err.raise_for_qbo_response(resp, fallback_message="QBO get bill payment failed")
                raw_data = qbo_err.safe_json(resp) or {}
                logger.debug("QuickBooks bill payment lookup parsed | bill_payment_present=%s", bool(raw_data.get("BillPayment")))
                entity = raw_data.get("BillPayment") or {}
                if entity:
                    logger.debug("Deferred QuickBooks bill payment found")
                    results.append(entity)
                else:
                    logger.debug("Deferred QuickBooks bill payment lookup returned no record")
            except Exception:
                logger.warning(
                    "QuickBooks bill payment retry lookup failed; continuing",
                    exc_info=True,
                )
                continue
        logger.debug("Deferred QuickBooks bill payment fetch completed | count=%s", len(results))
        return results

    def fetch_bill_by_id(self, *, access_token: str, base_url: str, realm_id: str, bill_id: str) -> Optional[Dict[str, Any]]:
        bid = self._clean(bill_id)
        if not bid:
            return None

        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/bill/{bid}"

        try:
            resp = qbo_err.qbo_get(url, headers=headers, timeout=30)
            qbo_err.raise_for_qbo_response(resp, fallback_message="QBO get linked bill failed")
            raw_data = qbo_err.safe_json(resp) or {}
            bill = raw_data.get("Bill") or {}

            if bill:
                logger.debug("Linked QuickBooks bill found")
                return bill

            logger.debug("Linked QuickBooks bill lookup returned no record")
        except Exception:
            logger.debug("Unable to read linked QuickBooks bill", exc_info=True)

        return None

    def get_qbo_api_base(self, env: str) -> str:
        from source.Utils import QBODiscoveryClient
        return QBODiscoveryClient.get_qbo_api_base(env)

    def refresh_access_token(self, *, tenant_id: str, service_id: str, connection: Optional[Dict[str, Any]], client_id: str, client_secret: str, refresh_token: str) -> str:
        return QuickBooksOAuthClient(repository=self.qbo_repo).refresh_access_token(tenant_id=str(tenant_id), service_id=str(service_id), client_id=client_id, client_secret=client_secret, refresh_token=refresh_token, token_url=self.qbo_service.token_url, current_connection=connection)

    def _extract_linked_bill_id(self, qbo_bill_payment: Dict[str, Any]) -> Optional[str]:
        for line in qbo_bill_payment.get("Line") or []:
            linked = line.get("LinkedTxn") or []
            if isinstance(linked, dict):
                linked = [linked]
            for item in linked:
                if self._clean(item.get("TxnType")).lower() == "bill":
                    return self._clean(item.get("TxnId")) or None
        return None

    def _extract_payment_account_meta(self, qbo_bill_payment: Dict[str, Any]) -> Dict[str, Optional[str]]:
        check_payment = qbo_bill_payment.get("CheckPayment") or {}
        credit_card_payment = qbo_bill_payment.get("CreditCardPayment") or {}
        bank_ref = check_payment.get("BankAccountRef") or {}
        cc_ref = credit_card_payment.get("CCAccountRef") or {}
        if self._clean(bank_ref.get("value")):
            return {
                "external_account_id": self._clean(bank_ref.get("value")) or None,
                "account_name": self._clean(bank_ref.get("name")) or None,
                "account_type": "Bank",
                "account_detail_type": "Checking",
            }
        if self._clean(cc_ref.get("value")):
            return {
                "external_account_id": self._clean(cc_ref.get("value")) or None,
                "account_name": self._clean(cc_ref.get("name")) or None,
                "account_type": "Credit Card",
                "account_detail_type": "CreditCard",
            }
        return {
            "external_account_id": None,
            "account_name": None,
            "account_type": None,
            "account_detail_type": None,
        }

    def _build_linked_bill_not_mapped_reason(
        self,
        *,
        qbo_bill_payment: Dict[str, Any],
        qbo_payment_id: str,
        linked_bill_id: str,
        qbo_linked_bill: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        payment_number = self._clean(qbo_bill_payment.get("DocNumber")) or None
        payment_amount = qbo_bill_payment.get("TotalAmt")
        bill_number = self._clean((qbo_linked_bill or {}).get("DocNumber")) or None

        bill_vendor_ref = (qbo_linked_bill or {}).get("VendorRef") or {}
        payment_vendor_ref = qbo_bill_payment.get("VendorRef") or {}
        vendor_name = self._clean(bill_vendor_ref.get("name") or payment_vendor_ref.get("name")) or None
        vendor_id = self._clean(bill_vendor_ref.get("value") or payment_vendor_ref.get("value")) or None

        payment_label = f'QuickBooks bill payment "{payment_number}"' if payment_number else f"QuickBooks bill payment ID {qbo_payment_id}"
        bill_label = f'QuickBooks bill "{bill_number}"' if bill_number else f"QuickBooks bill ID {linked_bill_id}"
        vendor_label = f' for vendor "{vendor_name}"' if vendor_name else (f" for QuickBooks vendor ID {vendor_id}" if vendor_id else "")

        message = (
            f"{payment_label} can't be imported yet — its linked {bill_label}{vendor_label} "
            "is not mapped in FIELDCRAFT. Run bill sync first to import or map that bill, then retry bill payment sync."
        )

        return {
            "message": message,
            "qboBillPaymentId": qbo_payment_id,
            "qboBillPaymentNumber": payment_number,
            "qboBillPaymentAmount": payment_amount,
            "linkedQboBillId": linked_bill_id,
            "linkedQboBillNumber": bill_number,
            "qboVendorId": vendor_id,
            "qboVendorName": vendor_name,
            "action": "Run bill sync first to import or map the linked QuickBooks bill, then retry bill payment sync.",
        }

    def _parse_timestamp(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            raw = str(value).strip().replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(raw)
            except Exception:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _get_qbo_last_updated_at(self, qbo_bill_payment: Dict[str, Any]) -> Optional[datetime]:
        meta = qbo_bill_payment.get("MetaData") or {}
        return self._parse_timestamp(meta.get("LastUpdatedTime") or meta.get("CreateTime"))

    def _coerce_changed_since(self, value: Optional[str]) -> Optional[str]:
        text = self._clean(value)
        if not text:
            return None
        dt = self._parse_timestamp(text)
        return dt.isoformat() if dt else None
