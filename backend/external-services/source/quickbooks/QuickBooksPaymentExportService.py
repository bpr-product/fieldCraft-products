from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

from source.Utils import QuickBooksErrorHandler as qbo_err
from source.Utils import constants as const
from source.Utils.auditlog import safe_audit_log
from source.dal.CustomerPaymentRepository import CustomerPaymentRepository
from source.dal.CustomerRepository import CustomerRepository
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.dal.ProjectRepository import ProjectRepository
from source.quickbooks.QuickBooksAuthentication import QuickBooksOAuthClient, QuickBooksReconnectRequiredError
from source.quickbooks.qbo_query_utils import escape_qbo_literal
from source.service.QuickBooksService import QuickBooksService

logger = logging.getLogger(__name__)


class QuickBooksPaymentExportService:
    """Export FIELDCRAFT customer receipts to QuickBooks Payments.

    One ``payment_receives`` row maps to one QBO Payment. Child
    ``payment_receives_entries`` rows become linked Invoice allocation lines.
    """

    MONEY_QUANTUM = Decimal("0.01")
    RAW_TOLERANCE = Decimal("0.01")

    def __init__(self) -> None:
        self.qbo_repo = QuickBooksRepository()
        self.qbo_service = QuickBooksService()
        self.payment_repo = CustomerPaymentRepository()
        self.customer_repo = CustomerRepository()
        self.project_repo = ProjectRepository()

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def _decimal(cls, value: Any) -> Decimal:
        try:
            return Decimal(str(value or 0))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid money value: {value!r}") from exc

    @classmethod
    def _money(cls, value: Any) -> Decimal:
        return cls._decimal(value).quantize(cls.MONEY_QUANTUM, rounding=ROUND_HALF_UP)

    @staticmethod
    def _date_string(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date().isoformat()
        text = str(value).strip()
        return text[:10] if text else None

    @staticmethod
    def _headers(access_token: str, *, json_content: bool = False) -> Dict[str, str]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        if json_content:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _fallback_payment_ref(payment_id: Any) -> str:
        try:
            value = int(payment_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("FIELDCRAFT payment id is required to build a QuickBooks payment reference.") from exc
        if value <= 0:
            raise ValueError("FIELDCRAFT payment id must be greater than zero.")
        return f"FIELDCRAFT-Pay-{value}"

    @staticmethod
    def _legacy_payment_ref(payment_id: Any) -> str:
        try:
            value = int(payment_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("FIELDCRAFT payment id is required to build the legacy QuickBooks payment reference.") from exc
        if value <= 0:
            raise ValueError("FIELDCRAFT payment id must be greater than zero.")
        return f"FIELDCRAFTPAY:{value}"

    def _payment_ref(self, payment: Dict[str, Any]) -> str:
        reference_id = payment.get("reference_id")
        if reference_id is not None and self._clean(reference_id):
            return self._clean(reference_id)

        transaction_id = self._clean(payment.get("fieldcraft_transaction_id"))
        if transaction_id:
            return transaction_id

        return self._fallback_payment_ref(payment.get("id"))

    @staticmethod
    def _reason(message: str) -> str:
        return str(message or "Customer payment sync failed.").strip()[:255]

    def _open_qbo_session(self, *, tenant_id: str, service_id: str) -> Dict[str, str]:
        connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
        config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}

        status = self._clean(connection.get(const.KEY_STATUS)).lower()
        realm_id = self._clean(connection.get(const.KEY_REALM_ID))
        refresh_token = self._clean(connection.get(const.KEY_REFRESH_TOKEN))
        env = self._clean(connection.get(const.KEY_ENV) or config.get(const.KEY_ENV) or "sandbox").lower()
        client_id = self._clean(config.get(const.KEY_CLIENT_ID))
        client_secret = self._clean(config.get(const.KEY_CLIENT_SECRET))

        if status != const.STATUS_CONNECTED:
            raise ValueError(f"QuickBooks is not connected (status={status or 'unknown'}).")
        if not realm_id or not refresh_token:
            raise ValueError("QuickBooks connection is missing realmId or refreshToken.")
        if not client_id or not client_secret:
            raise ValueError("QuickBooks client credentials are missing (ClientId/ClientSecret).")

        access_token = QuickBooksOAuthClient(repository=self.qbo_repo).refresh_access_token(
            tenant_id=str(tenant_id),
            service_id=str(service_id),
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            token_url=self.qbo_service.token_url,
            current_connection=connection,
            debug_context={"operation": "customer_payment_export", "env": env},
        )

        from source.Utils import QBODiscoveryClient

        return {
            "access_token": access_token,
            "realm_id": realm_id,
            "env": env,
            "base_url": QBODiscoveryClient.get_qbo_api_base(env).rstrip("/"),
        }

    def _assert_mapping_realm(self, mapping: Dict[str, Any], realm_id: str, label: str) -> None:
        mapped_realm = self._clean(mapping.get("external_account_id"))
        if mapped_realm and mapped_realm != self._clean(realm_id):
            raise ValueError(
                f"{label} belongs to a different QuickBooks company. Re-sync the prerequisite data and retry Customer Payment sync."
            )

    def get_qbo_invoice(self, *, access_token: str, base_url: str, realm_id: str, invoice_id: str) -> Dict[str, Any]:
        url = f"{base_url}/v3/company/{realm_id}/invoice/{invoice_id}"
        response = qbo_err.qbo_get(url, headers=self._headers(access_token), timeout=30)
        qbo_err.raise_for_qbo_response(response, fallback_message="QuickBooks invoice lookup failed during payment allocation.")
        return qbo_err.safe_json(response).get("Invoice") or {}

    def get_qbo_payment(self, *, access_token: str, base_url: str, realm_id: str, payment_id: str) -> Dict[str, Any]:
        url = f"{base_url}/v3/company/{realm_id}/payment/{payment_id}"
        response = qbo_err.qbo_get(url, headers=self._headers(access_token), timeout=30)
        qbo_err.raise_for_qbo_response(response, fallback_message="QuickBooks payment lookup failed.")
        return qbo_err.safe_json(response).get("Payment") or {}

    @staticmethod
    def _is_missing_entity_error(exc: Exception) -> bool:
        if isinstance(exc, qbo_err.QuickBooksAPIError) and exc.status_code == 404:
            return True
        text = " ".join(str(value or "") for value in (exc, getattr(exc, "detail", None))).lower()
        return "not found" in text or "does not exist" in text or "object not found" in text

    def _resolve_payment_customer_ref(self, *, tenant_id: str, service_id: str, realm_id: str, payment: Dict[str, Any]) -> str:
        customer_uid = self._clean(payment.get("customer_uid"))
        if not customer_uid:
            raise ValueError("Customer payment customer_uid is missing.")
        mapping = self.customer_repo.get_latest_qbo_linked_mapping_for_customer_uuid(
            tenant_id,
            service_id,
            customer_uid,
            external_account_id=realm_id,
        )
        if not mapping or not self._clean(mapping.get("external_customer_id")):
            raise ValueError("The payment customer is not mapped to QuickBooks. Sync Customers first.")
        self._assert_mapping_realm(mapping, realm_id, "Customer mapping")
        return self._clean(mapping.get("external_customer_id"))

    def _resolve_allocations(
        self,
        *,
        tenant_id: str,
        service_id: str,
        session: Dict[str, str],
        payment: Dict[str, Any],
        entries: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        total_raw = self._decimal(payment.get("amount"))
        if total_raw <= 0:
            raise ValueError("Customer payment amount must be greater than zero.")
        suspense_value = payment.get("suspense_amount")
        suspense_raw = self._decimal(suspense_value)
        if suspense_raw < 0:
            raise ValueError("Customer payment suspense amount cannot be negative.")

        all_allocated_raw = Decimal("0")
        linked_by_invoice: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        # A Customer Payment belongs to the FIELDCRAFT customer, not to an individual
        # project. Keep the QBO Payment on the mapped parent customer even when
        # invoices are billed through different project sub-customers.
        qbo_customer_ref = self._resolve_payment_customer_ref(
            tenant_id=tenant_id,
            service_id=service_id,
            realm_id=session["realm_id"],
            payment=payment,
        )
        resolved_invoice_count = 0
        invoice_numbers: List[str] = []
        qbo_invoice_cache: Dict[str, Dict[str, Any]] = {}

        for entry in entries:
            amount_raw = self._decimal(entry.get("payment_amount"))
            if amount_raw < 0:
                raise ValueError("Customer payment allocation amount cannot be negative.")
            all_allocated_raw += amount_raw

            interest = self._decimal(entry.get("interest_paid"))
            late_fee = self._decimal(entry.get("late_fee_paid"))
            if interest != 0 or late_fee != 0:
                raise ValueError(
                    "Customer payment contains interest or late-fee amounts. Their QuickBooks accounting treatment is not configured yet; review this receipt before syncing."
                )

            reference_no = self._clean(entry.get("reference_no"))
            if amount_raw == 0 or not reference_no:
                continue
            project_id = entry.get("project_id")
            if project_id is None:
                raise ValueError(f"Payment allocation for invoice {reference_no} is missing project_id.")

            resolved = self.payment_repo.resolve_invoice(
                tenant_id=tenant_id,
                service_id=service_id,
                invoice_no=reference_no,
                project_id=int(project_id),
            )
            invoice = resolved["invoice"]
            mapping = resolved["mapping"]
            if self._clean(invoice.get("customer_uid")) != self._clean(payment.get("customer_uid")):
                raise ValueError(
                    f"Invoice {reference_no} belongs to a different FIELDCRAFT customer than this payment receipt. Correct the reconciliation before syncing."
                )
            self._assert_mapping_realm(mapping, session["realm_id"], f"Invoice {reference_no} mapping")
            qbo_invoice_id = self._clean(mapping.get("external_invoice_id"))
            if not qbo_invoice_id:
                raise ValueError(f"Invoice {reference_no} has no QuickBooks invoice id. Sync Invoices first.")

            qbo_invoice = qbo_invoice_cache.get(qbo_invoice_id)
            if qbo_invoice is None:
                qbo_invoice = self.get_qbo_invoice(
                    access_token=session["access_token"],
                    base_url=session["base_url"],
                    realm_id=session["realm_id"],
                    invoice_id=qbo_invoice_id,
                )
                qbo_invoice_cache[qbo_invoice_id] = qbo_invoice
            invoice_customer_ref = self._clean((qbo_invoice.get("CustomerRef") or {}).get("value"))
            if not invoice_customer_ref:
                raise ValueError(f"QuickBooks invoice {reference_no} has no CustomerRef.")

            if invoice_customer_ref != qbo_customer_ref:
                project_mapping = self.project_repo.get_current_mapping_by_project_id(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    project_id=int(project_id),
                )
                if project_mapping:
                    self._assert_mapping_realm(
                        project_mapping,
                        session["realm_id"],
                        f"Project {project_id} mapping",
                    )

                project_target = self.project_repo.get_mapping_sync_target(project_mapping)
                expected_project_ref = self._clean((project_mapping or {}).get("external_project_id"))
                invoice_project_ref = self._clean((qbo_invoice.get("ProjectRef") or {}).get("value"))

                # The two supported project representations use different QBO
                # reference fields. A legacy sub-customer is the Invoice CustomerRef;
                # a real QBO Project is the Invoice ProjectRef. Do not compare a QBO
                # Project id to CustomerRef -- they are different entity identities.
                is_expected_project = (
                    project_target == const.PROJECT_SYNC_TARGET_QBO_PROJECT
                    and expected_project_ref
                    and invoice_project_ref == expected_project_ref
                )
                is_expected_sub_customer = (
                    project_target == const.PROJECT_SYNC_TARGET_SUB_CUSTOMER
                    and expected_project_ref
                    and invoice_customer_ref == expected_project_ref
                )
                if not (is_expected_project or is_expected_sub_customer):
                    logger.warning(
                        "[CustomerPaymentExport] invoice/customer reference mismatch "
                        "| payment_id=%s invoice_no=%s project_id=%s payment_customer_ref=%s "
                        "invoice_customer_ref=%s invoice_project_ref=%s project_target=%s expected_project_ref=%s",
                        payment.get("id"),
                        reference_no,
                        project_id,
                        qbo_customer_ref,
                        invoice_customer_ref,
                        invoice_project_ref,
                        project_target,
                        expected_project_ref,
                    )
                    raise ValueError(
                        f"Invoice {reference_no} is linked to a different QuickBooks customer than this FIELDCRAFT payment. "
                        "Review the customer/project mapping and reconciliation before syncing."
                    )

            linked_by_invoice[qbo_invoice_id] += amount_raw
            if reference_no not in invoice_numbers:
                invoice_numbers.append(reference_no)
            resolved_invoice_count += 1

        if all_allocated_raw > total_raw + self.RAW_TOLERANCE:
            raise ValueError(
                f"Payment allocations ({all_allocated_raw}) exceed the received amount ({total_raw}). Review the FIELDCRAFT payment receipt before syncing."
            )

        expected_suspense = total_raw - all_allocated_raw
        if suspense_value is not None and abs(expected_suspense - suspense_raw) > self.RAW_TOLERANCE:
            raise ValueError(
                f"Payment allocation total and suspense amount do not reconcile to the received amount. Expected unapplied amount {expected_suspense}, FIELDCRAFT suspense amount is {suspense_raw}."
            )

        total_qbo = self._money(total_raw)
        line_rows: List[Dict[str, Any]] = []
        linked_qbo = Decimal("0")
        for invoice_id, raw_amount in sorted(linked_by_invoice.items(), key=lambda pair: pair[0]):
            amount_qbo = self._money(raw_amount)
            if amount_qbo <= 0:
                continue
            linked_qbo += amount_qbo
            line_rows.append({
                "Amount": float(amount_qbo),
                "LinkedTxn": [{"TxnId": invoice_id, "TxnType": "Invoice"}],
            })

        if linked_qbo > total_qbo:
            overflow = linked_qbo - total_qbo
            if overflow <= self.RAW_TOLERANCE and line_rows:
                adjusted = self._money(Decimal(str(line_rows[-1]["Amount"])) - overflow)
                if adjusted <= 0:
                    raise ValueError("Payment allocation rounding produced an invalid QuickBooks line amount.")
                line_rows[-1]["Amount"] = float(adjusted)
                linked_qbo = total_qbo
            else:
                raise ValueError("Rounded QuickBooks payment allocations exceed the rounded payment total.")

        return {
            "customer_ref": qbo_customer_ref,
            "lines": line_rows,
            "total": total_qbo,
            "linked_total": linked_qbo,
            "unapplied_total": total_qbo - linked_qbo,
            "allocation_count": len(entries),
            "linked_invoice_count": len(linked_by_invoice),
            "resolved_entry_count": resolved_invoice_count,
            "invoice_numbers": invoice_numbers,
        }

    def build_payment_payload(self, *, payment: Dict[str, Any], allocation: Dict[str, Any]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "CustomerRef": {"value": allocation["customer_ref"]},
            "TotalAmt": float(allocation["total"]),
            "PaymentRefNum": self._payment_ref(payment),
        }
        txn_date = self._date_string(payment.get("received_time"))
        if txn_date:
            payload["TxnDate"] = txn_date
        if allocation.get("lines"):
            payload["Line"] = allocation["lines"]

        note_parts: List[str] = []
        description = self._clean(payment.get("description"))
        if description:
            note_parts.append(description)

        pay_method = self._clean(payment.get("pay_method"))
        if pay_method:
            note_parts.append(f"Payment method: {pay_method}")

        original_ref = self._clean(payment.get("payment_reference_no"))
        if original_ref and original_ref != payload["PaymentRefNum"]:
            note_parts.append(f"Payment reference: {original_ref}")

        invoice_numbers = [
            self._clean(value)
            for value in allocation.get("invoice_numbers") or []
            if self._clean(value)
        ]
        if len(invoice_numbers) == 1:
            note_parts.append(f"Applied to invoice: {invoice_numbers[0]}")
        elif invoice_numbers:
            shown = invoice_numbers[:5]
            suffix = (
                f" (+{len(invoice_numbers) - len(shown)} more)"
                if len(invoice_numbers) > len(shown)
                else ""
            )
            note_parts.append(f"Applied to invoices: {', '.join(shown)}{suffix}")
        elif allocation.get("unapplied_total", Decimal("0")) > 0:
            note_parts.append("Unapplied payment")

        payload["PrivateNote"] = " | ".join(note_parts)[:4000]
        return payload

    def find_payment_candidates_by_ref(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        payment_ref: str,
    ) -> List[Dict[str, Any]]:
        query = f"select * from Payment where PaymentRefNum = '{escape_qbo_literal(payment_ref)}' STARTPOSITION 1 MAXRESULTS 20"
        response = qbo_err.qbo_get(
            f"{base_url}/v3/company/{realm_id}/query",
            headers=self._headers(access_token),
            params={"query": query},
            timeout=30,
        )
        qbo_err.raise_for_qbo_response(response, fallback_message="QuickBooks payment identity lookup failed.")
        rows = (qbo_err.safe_json(response).get("QueryResponse") or {}).get("Payment") or []
        if isinstance(rows, dict):
            return [rows]
        return rows if isinstance(rows, list) else []

    def create_payment_in_qbo(self, *, session: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
        response = qbo_err.qbo_post(
            f"{session['base_url']}/v3/company/{session['realm_id']}/payment",
            headers=self._headers(session["access_token"], json_content=True),
            json=payload,
            timeout=30,
        )
        qbo_err.raise_for_qbo_response(response, success_statuses=(200, 201), fallback_message="QuickBooks payment creation failed.")
        return qbo_err.safe_json(response).get("Payment") or {}

    def update_payment_in_qbo(
        self,
        *,
        session: Dict[str, str],
        payment_id: str,
        payload: Dict[str, Any],
        known_sync_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        current_token = self._clean(known_sync_token)
        if not current_token:
            current = self.get_qbo_payment(
                access_token=session["access_token"],
                base_url=session["base_url"],
                realm_id=session["realm_id"],
                payment_id=payment_id,
            )
            current_token = self._clean(current.get("SyncToken"))
        if not current_token:
            raise ValueError("QuickBooks payment update failed because SyncToken is missing.")

        body = {"Id": str(payment_id), "SyncToken": current_token, "sparse": True, **payload}
        response = qbo_err.qbo_post(
            f"{session['base_url']}/v3/company/{session['realm_id']}/payment",
            headers=self._headers(session["access_token"], json_content=True),
            json=body,
            timeout=30,
        )
        try:
            qbo_err.raise_for_qbo_response(response, success_statuses=(200, 201), fallback_message="QuickBooks payment update failed.")
        except Exception as exc:
            text = " ".join(str(value or "") for value in (exc, getattr(exc, "detail", None))).lower()
            if "stale object" not in text and "synctoken" not in text and "sync token" not in text:
                raise
            current = self.get_qbo_payment(
                access_token=session["access_token"],
                base_url=session["base_url"],
                realm_id=session["realm_id"],
                payment_id=payment_id,
            )
            latest = self._clean(current.get("SyncToken"))
            if not latest:
                raise ValueError("QuickBooks payment update failed because the latest SyncToken is missing.")
            body["SyncToken"] = latest
            response = qbo_err.qbo_post(
                f"{session['base_url']}/v3/company/{session['realm_id']}/payment",
                headers=self._headers(session["access_token"], json_content=True),
                json=body,
                timeout=30,
            )
            qbo_err.raise_for_qbo_response(response, success_statuses=(200, 201), fallback_message="QuickBooks payment update retry failed.")
        return qbo_err.safe_json(response).get("Payment") or {}

    def _upsert_payment(
        self,
        *,
        session: Dict[str, str],
        payment: Dict[str, Any],
        mapping: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], str]:
        mapped_id = self._clean(mapping.get("external_payment_id"))
        if mapped_id:
            self._assert_mapping_realm(mapping, session["realm_id"], "Customer payment mapping")
            try:
                updated = self.update_payment_in_qbo(
                    session=session,
                    payment_id=mapped_id,
                    payload=payload,
                    known_sync_token=mapping.get("sync_token"),
                )
                return updated, "updatedInQbo"
            except Exception as exc:
                if not self._is_missing_entity_error(exc):
                    raise
                logger.warning("Mapped QuickBooks payment is missing; attempting identity recovery | fieldcraft_payment_id=%s", payment.get("id"))

        payment_ref = self._payment_ref(payment)
        candidates = self.find_payment_candidates_by_ref(
            access_token=session["access_token"],
            base_url=session["base_url"],
            realm_id=session["realm_id"],
            payment_ref=payment_ref,
        )
        legacy_ref = self._legacy_payment_ref(payment.get("id"))
        if not candidates and legacy_ref != payment_ref:
            candidates = self.find_payment_candidates_by_ref(
                access_token=session["access_token"],
                base_url=session["base_url"],
                realm_id=session["realm_id"],
                payment_ref=legacy_ref,
            )
        expected_customer = self._clean((payload.get("CustomerRef") or {}).get("value"))
        matches = [
            row for row in candidates
            if self._clean((row.get("CustomerRef") or {}).get("value")) == expected_customer
        ]
        if len(matches) > 1:
            raise ValueError("Multiple QuickBooks payments match this FIELDCRAFT payment reference/customer. Resolve the duplicates in QuickBooks before retrying.")
        if len(matches) == 1:
            existing = matches[0]
            existing_id = self._clean(existing.get("Id"))
            if not existing_id:
                raise ValueError("Matched QuickBooks payment has no Id.")
            updated = self.update_payment_in_qbo(
                session=session,
                payment_id=existing_id,
                payload=payload,
                known_sync_token=existing.get("SyncToken"),
            )
            return updated, "mappedFromQbo"
        if candidates and not matches:
            raise ValueError("A QuickBooks payment already uses this FIELDCRAFT payment reference for a different customer. Resolve it before retrying.")
        return self.create_payment_in_qbo(session=session, payload=payload), "createdInQbo"

    def export_payments(
        self,
        *,
        tenant_id: str,
        service_id: str,
        requested_max_payments: int = 2000,
        dry_run: bool = False,
        **_: Any,
    ) -> Dict[str, Any]:
        tenant_id = self._clean(tenant_id)
        service_id = self._clean(service_id)
        if not tenant_id or not service_id:
            return {const.KEY_OK: False, const.KEY_MESSAGE: "tenant_id and service_id are required."}

        safe_audit_log(
            tenant_id=tenant_id,
            action="customer_payment_export_started",
            status="started",
            resource_type="quickbooks_customer_payment",
        )

        try:
            candidates = self.payment_repo.get_export_candidates(
                tenant_id=tenant_id,
                service_id=service_id,
                limit=max(1, min(int(requested_max_payments or 2000), 5000)),
                offset=0,
            )
        except Exception as exc:
            logger.exception("Unable to load customer payment export candidates")
            return {const.KEY_OK: False, const.KEY_MESSAGE: f"Unable to load FIELDCRAFT customer payments for export: {exc}"}

        summary: Dict[str, Any] = {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": service_id,
            "requested": len(candidates),
            "processedPayments": 0,
            "exported": 0,
            "createdInQbo": 0,
            "updatedInQbo": 0,
            "mappedFromQbo": 0,
            "skippedAlreadyMapped": 0,
            "skippedInProgress": 0,
            "skippedSourceChanged": 0,
            "failed": 0,
            "syncFailed": 0,
            const.KEY_RESULTS: [],
            const.KEY_FAILURES: [],
            "dryRun": bool(dry_run),
        }
        if not candidates:
            return summary

        try:
            session = self._open_qbo_session(tenant_id=tenant_id, service_id=service_id)
        except QuickBooksReconnectRequiredError:
            raise
        except Exception as exc:
            summary[const.KEY_OK] = False
            summary[const.KEY_MESSAGE] = str(exc)
            return summary

        summary["env"] = session["env"]
        summary["realmIdPresent"] = True

        for candidate in candidates:
            payment = candidate.get("payment") or {}
            entries = candidate.get("entries") or []
            mapping = candidate.get("mapping") or {}
            mapping_id = mapping.get("id")
            detail: Dict[str, Any] = {
                "paymentId": payment.get("id"),
                "customerUid": payment.get("customer_uid"),
                "amount": float(self._decimal(payment.get("amount"))),
                "paymentRef": self._payment_ref(payment),
                "mappingId": mapping_id,
                "allocationCount": len(entries),
            }
            summary["processedPayments"] += 1
            try:
                if not mapping_id:
                    raise ValueError("Customer payment external mapping is missing.")
                allocation = self._resolve_allocations(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    session=session,
                    payment=payment,
                    entries=entries,
                )
                payload = self.build_payment_payload(payment=payment, allocation=allocation)
                detail.update({
                    "txnDate": payload.get("TxnDate"),
                    "linkedAmount": float(allocation["linked_total"]),
                    "unappliedAmount": float(allocation["unapplied_total"]),
                    "linkedInvoiceCount": allocation["linked_invoice_count"],
                })
                if dry_run:
                    detail.update({const.KEY_STATUS: "success", const.KEY_ACTION: "wouldSyncToQbo"})
                    summary["exported"] += 1
                    summary[const.KEY_RESULTS].append(detail)
                    continue

                fingerprint = self.payment_repo.source_fingerprint(payment, entries)
                claim = self.payment_repo.claim_export_mapping(
                    tenant_id=tenant_id,
                    service_id=service_id,
                    mapping_id=int(mapping_id),
                    source_fingerprint=fingerprint,
                )
                if not claim.get("claimed"):
                    claim_reason = self._clean(claim.get("reason"))
                    current_mapping = claim.get("mapping") or {}
                    if claim_reason == "already_synced":
                        summary["skippedAlreadyMapped"] += 1
                        action = "alreadySynced"
                    elif claim_reason == "source_changed":
                        summary["skippedSourceChanged"] += 1
                        action = "sourceChangedRetry"
                    else:
                        summary["skippedInProgress"] += 1
                        action = "syncAlreadyInProgress"
                    detail.update(
                        {
                            const.KEY_STATUS: "success",
                            const.KEY_ACTION: action,
                            "qboPaymentId": self._clean(current_mapping.get("external_payment_id")) or None,
                        }
                    )
                    summary[const.KEY_RESULTS].append(detail)
                    continue

                # claim_export_mapping commits before returning, so the outbound QBO
                # request does not hold a DB transaction, row lock, or advisory lock.
                locked_mapping = claim.get("mapping") or {}
                qbo_payment, action = self._upsert_payment(
                    session=session,
                    payment=payment,
                    mapping=locked_mapping,
                    payload=payload,
                )
                qbo_payment_id = self._clean(qbo_payment.get("Id"))
                if not qbo_payment_id:
                    raise ValueError("QuickBooks returned a payment without an Id.")

                metadata = {
                    "allocationCount": int(allocation["allocation_count"]),
                    "linkedInvoiceCount": int(allocation["linked_invoice_count"]),
                    "qboCustomerId": allocation["customer_ref"],
                    "sourceFingerprint": fingerprint,
                }
                self.payment_repo.mark_mapping_synced(
                    tenant_id=tenant_id,
                    mapping_id=int(mapping_id),
                    external_payment_id=qbo_payment_id,
                    external_account_id=session["realm_id"],
                    sync_token=self._clean(qbo_payment.get("SyncToken")) or None,
                    additional_info=metadata,
                    reason="FIELDCRAFT customer payment exported to QuickBooks.",
                )

                summary["exported"] += 1
                if action == "createdInQbo":
                    summary["createdInQbo"] += 1
                elif action == "mappedFromQbo":
                    summary["mappedFromQbo"] += 1
                else:
                    summary["updatedInQbo"] += 1
                detail.update({
                    const.KEY_STATUS: "success",
                    const.KEY_ACTION: action,
                    "qboPaymentId": qbo_payment_id,
                    const.KEY_SYNC_TOKEN: qbo_payment.get("SyncToken"),
                })
                summary[const.KEY_RESULTS].append(detail)
            except Exception as exc:
                logger.exception("Customer payment export failed | tenant_id=%s payment_id=%s", tenant_id, payment.get("id"))
                message = str(exc)
                if mapping_id:
                    try:
                        self.payment_repo.mark_mapping_failed(
                            tenant_id=tenant_id,
                            mapping_id=int(mapping_id),
                            reason=message,
                        )
                    except Exception:
                        logger.exception("Unable to persist customer payment mapping failure | mapping_id=%s", mapping_id)
                detail.update({const.KEY_STATUS: "failed", const.KEY_ACTION: "customerPaymentExportFailed", const.KEY_ERROR: message})
                if isinstance(exc, qbo_err.QuickBooksAPIError):
                    if exc.intuit_tid:
                        detail[const.KEY_INTUIT_TID] = exc.intuit_tid
                    if exc.status_code is not None:
                        detail[const.KEY_STATUS_CODE] = exc.status_code
                summary["failed"] += 1
                summary["syncFailed"] += 1
                summary[const.KEY_RESULTS].append(detail)
                summary[const.KEY_FAILURES].append(dict(detail))

        if summary["failed"]:
            summary["partialSuccess"] = bool(summary["exported"])
            summary[const.KEY_MESSAGE] = f"Customer payment sync completed with {summary['failed']} failed payment(s)."
        return summary
