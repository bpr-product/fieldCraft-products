import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import requests
import os
import uuid
from source.dal.CustomerRepository import CustomerRepository
from source.dal.PaymentExternalMapRepository import PaymentExternalMapRepository
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.dal.TenantPaymentRepository import TenantPaymentRepository
from source.service.QuickBooksService import QuickBooksService
from source.Utils import QBODiscoveryClient
from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils import constants as const
from source.quickbooks.QuickBooksAuthentication import QuickBooksOAuthClient


logger = logging.getLogger(__name__)


class QuickBooksPaymentImportService:
    """Import QuickBooks payments into the tenant DB (payment_receives)."""

    @staticmethod
    def _debug(*parts: Any) -> None:
        """Log structural diagnostics only; never serialize payment values or identifiers."""
        if not logger.isEnabledFor(logging.DEBUG) or not parts:
            return

        safe_parts = [str(parts[0])[:200]]
        for part in parts[1:]:
            if isinstance(part, dict):
                safe_parts.append({"keys": sorted(str(key) for key in part.keys())})
            elif isinstance(part, (list, tuple, set)):
                safe_parts.append(f"<{type(part).__name__} length={len(part)}>")
            elif isinstance(part, (int, float, bool)) or part is None:
                safe_parts.append(part)
            else:
                safe_parts.append(f"<{type(part).__name__} redacted>")
        logger.debug("QBO payment import | %s", " | ".join(str(part) for part in safe_parts))

    def __init__(self):
        self._debug("[QBO IMPORT] __init__ start")
        self.qbo_repo = QuickBooksRepository()
        self.qbo_service = QuickBooksService()
        self.customer_repo = CustomerRepository()
        self.tenant_pay_repo = TenantPaymentRepository()
        self.payment_map_repo = PaymentExternalMapRepository()
        self._debug("[QBO IMPORT] __init__ done")


    @staticmethod
    def _is_deleted_customer_ref(customer_ref: Any) -> bool:
        """QuickBooks sometimes returns payments linked to deleted customers.
        In that case CustomerRef.name contains '(deleted)'."""
        try:
            if not isinstance(customer_ref, dict):
                return False
            name = (customer_ref.get("name") or "").strip()
            return "(deleted)" in name.lower()
        except Exception:
            return False

    def import_payments(
            self,
            *,
            tenant_id: str,
            service_id: str,
            page_size: int = 100,
            start_position: int = 1,
            max_payments: Optional[int] = None,
            dry_run: bool = False,
    ) -> Dict[str, Any]:

        self._debug("[QBO IMPORT] import_payments start")
        self._debug("[QBO IMPORT] params", {
            "tenant_id": tenant_id,
            "service_id": service_id,
            "page_size": page_size,
            "start_position": start_position,
            "max_payments": max_payments,
            "dry_run": dry_run,
        })
        pay_method_name = ""
        started = datetime.now(timezone.utc).isoformat()
        summary: Dict[str, Any] = {
            "ok": True,
            "tenantId": tenant_id,
            "serviceId": service_id,
            "startedAt": started,
            "dryRun": dry_run,
            "pageSize": page_size,
            "startPosition": start_position,
            "requestedMax": int(max_payments) if (max_payments and int(max_payments) > 0) else None,
            "processed": 0,
            "imported": 0,
            "skippedAlreadyMapped": 0,
            "skippedNoCustomerMapping": 0,
            "failed": 0,
            "failures": [],
            "results": [],
        }

        try:
            self._debug("[QBO IMPORT] fetching connection getAdditionalInfo")
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            self._debug("[QBO IMPORT] connection fetched")

            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = str(connection.get(const.KEY_ENV) or "sandbox").strip().lower()
            status = (connection.get(const.KEY_STATUS) or "").lower()
            external_account_id = connection.get(const.KEY_REALM_ID)

            self._debug("[QBO IMPORT] connection fields", {
                "env": env,
                "status": status,
                "has_refresh_token": bool(refresh_token),
                "has_realm_id": bool(realm_id),
                "external_account_id": external_account_id,
            })

            if status and status != "connected":
                self._debug("[QBO IMPORT] returning: not connected", status)
                return {**summary, "ok": False, "message": f"QuickBooks is not connected (status={status})"}
            if not refresh_token or not realm_id:
                self._debug("[QBO IMPORT] returning: missing refreshToken/realmId")
                return {**summary, "ok": False, "message": "QuickBooks not connected (missing refreshToken/realmId)."}

            self._debug("[QBO IMPORT] fetching config get_quickbooks_config")
            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id)
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)
            self._debug("[QBO IMPORT] config fields", {
                "has_client_id": bool(client_id),
                "has_client_secret": bool(client_secret),
            })

            if not client_id or not client_secret:
                self._debug("[QBO IMPORT] returning: missing ClientId/ClientSecret")
                return {**summary, "ok": False, "message": "QuickBooks client credentials missing (ClientId/ClientSecret)."}

            self._debug("[QBO IMPORT] refreshing access token")
            access_token = self._refresh_access_token(
                client_id,
                client_secret,
                refresh_token,
                tenant_id=tenant_id,
                service_id=service_id,
                current_connection=connection,
            )
            self._debug("[QBO IMPORT] access token refreshed")

            base_url = self._get_qbo_api_base(env)
            self._debug("[QBO IMPORT] base_url", base_url)

            processed = 0
            position = max(1, int(start_position))
            self._debug("[QBO IMPORT] starting loop", {"position": position, "page_size": page_size})

            while True:
                self._debug("[QBO IMPORT] querying batch", {"position": position, "page_size": page_size, "processed": processed})

                batch = self._query_qbo_payments(
                    base_url=base_url,
                    realm_id=str(realm_id),
                    access_token=access_token,
                    start_position=position,
                    page_size=page_size,
                )

                self._debug("[QBO IMPORT] batch result", {"batch_size": len(batch) if batch else 0})

                if not batch:
                    self._debug("[QBO IMPORT] breaking: no batch")
                    break

                for pay in batch:
                    processed += 1
                    summary["processed"] = processed
                    self._debug("[QBO IMPORT] processing payment", {"processed": processed})

                    try:
                        qbo_payment_id = str(pay.get(const.QBO_KEY_ID)) if pay.get(const.QBO_KEY_ID) is not None else None
                        self._debug("[QBO IMPORT] qbo_payment_id", qbo_payment_id)

                        if not qbo_payment_id:
                            self._debug("[QBO IMPORT] skip: missing payment Id")
                            continue

                        existing = self.payment_map_repo.get_by_external_payment_id(tenant_id, qbo_payment_id)
                        if existing:
                            summary["skippedAlreadyMapped"] += 1
                            fieldcraft_payment_id = existing.get("payment_id")
                            fieldcraft_customer_id = existing.get("customer_id")
                            fieldcraft_customer_uid = None
                            try:
                                if fieldcraft_customer_id is not None:
                                    fieldcraft_customer_uid = self.customer_repo.get_customer_uuid_by_id(int(fieldcraft_customer_id))
                            except Exception:
                                logger.debug(
                                    "QuickBooks payment import could not resolve mapped customer UUID",
                                    exc_info=True,
                                )

                            summary["results"].append({
                                "status": "skipped",
                                "action": "alreadyMapped",
                                "qboPaymentId": qbo_payment_id,
                                "paymentId": fieldcraft_payment_id,
                                "customerUid": fieldcraft_customer_uid,
                                "reason": "Payment already mapped in FIELDCRAFT",
                            })
                            self._debug("[QBO IMPORT] skip: already mapped", qbo_payment_id)
                            continue

                        cust_obj = (pay.get("CustomerRef") or {})
                        cust_ref = cust_obj.get("value")
                        cust_name = (cust_obj.get("name") or "").strip()
                        self._debug("[QBO IMPORT] cust_ref", cust_ref)

                        # If payment belongs to a deleted customer in QBO, skip with a clearer reason
                        if self._is_deleted_customer_ref(cust_obj):
                            summary.setdefault("skippedCustomerDeleted", 0)
                            summary["skippedCustomerDeleted"] += 1
                            summary["results"].append({
                            "status": "skipped",
                            "action": "customerDeleted",
                            "qboPaymentId": qbo_payment_id,
                            "customerRef": cust_ref,
                            "customerName": cust_name or None,
                            "reason": "Customer is deleted in QuickBooks",
                            })
                            self._debug("[QBO IMPORT] skip: customer deleted", {"cust_ref": cust_ref, "payment_id": qbo_payment_id, "customer": cust_name})
                            continue

                        customer_uid = self.customer_repo.get_customer_uuid_by_external_customer_id(
                        tenant_id, str(cust_ref) if cust_ref is not None else ""
                        )
                        self._debug("[QBO IMPORT] customer_uid", customer_uid)

                        if not customer_uid:
                            # Treat missing customer mapping as FAILED (not SKIPPED) because we cannot import this payment.
                            summary.setdefault("failedNoCustomerMapping", 0)
                            summary["failedNoCustomerMapping"] += 1
                            summary["failed"] += 1
                            summary["results"].append({
                            "status": "failed",
                            "action": "noCustomerMapping",
                            "qboPaymentId": qbo_payment_id,
                            "customerRef": cust_ref,
                            "customerName": cust_name or None,
                            "reason": "No customer mapping in FIELDCRAFT",
                            })
                            self._debug("[QBO IMPORT] fail: no customer mapping", {"cust_ref": cust_ref, "paymentId": qbo_payment_id})
                            continue

                        self._debug("[QBO IMPORT] payment payload received", pay)

                        total_amt = pay.get("TotalAmt")
                        txn_date = pay.get("TxnDate")
                        received_time = self._received_time_from_txn_date(txn_date)
                        ref_no = pay.get("PaymentRefNum")

                        deposit_ref = (pay.get("DepositToAccountRef") or {}).get("value")
                        invoice_id = self._extract_first_linked_invoice_id(pay)

                        self._debug("[QBO IMPORT] payment details", {
                            "total_amt": total_amt,
                            "txn_date": txn_date,
                            "received_time": str(received_time),
                            "ref_no": ref_no,
                            "deposit_ref": deposit_ref,
                            "invoice_id": invoice_id,
                            "dry_run": dry_run,
                        })

                        if not dry_run:
                            self._debug("[QBO IMPORT] inserting payment_receive_from_qbo", qbo_payment_id)
                            new_id = self.tenant_pay_repo.insert_payment_receive_from_qbo(
                                tenant_db=tenant_id,
                                customer_uid=customer_uid,
                                amount=float(total_amt) if total_amt is not None else 0.0,
                                received_time=received_time,
                                currency_code=None,
                                pay_method=QuickBooksPaymentImportService.get_payment_method_name(self,
                                                                                                  pay,
                                                                                                  base_url=base_url,
                                                                                                  realm_id=str(realm_id),
                                                                                                  access_token=access_token,
                                                                                                  ) or None,
                                reference_no=None,
                                status="Verified",
                                additional_info=json.dumps({"qboPayment": pay}, default=str)[:65000],
                                payment_transaction_id=None,
                                payment_reference_no=f"QBO Payment {qbo_payment_id}" if qbo_payment_id is not None else None,
                                payment_receipt_id=str(qbo_payment_id),
                                fieldcraft_transaction_id = self.transaction_id(),
                            )
                            self._debug("[QBO IMPORT] inserted payment_receive_from_qbo", {"qbo_payment_id": qbo_payment_id, "new_id": new_id})
                        else:
                            new_id = None
                            self._debug("[QBO IMPORT] dry_run: skip insert", qbo_payment_id)

                        # Store mapping (even in dry-run, we do NOT persist)
                        if not dry_run:
                            try:
                                self._debug("[QBO IMPORT] inserting mapping", {"qbo_payment_id": qbo_payment_id})
                                fieldcraft_customer_id = self.customer_repo.get_customer_id_by_uuid(customer_uid)
                                self._debug("[QBO IMPORT] fieldcraft_customer_id", fieldcraft_customer_id)
                                if fieldcraft_customer_id:
                                    self.payment_map_repo.insert_mapping(
                                        tenant_id=tenant_id,
                                        customer_id=int(fieldcraft_customer_id),
                                        input_source="QUICKBOOKS",
                                        external_payment_id=qbo_payment_id,
                                        external_invoice_id=str(invoice_id) if invoice_id else None,
                                        external_account_id=str(external_account_id) if external_account_id else None,
                                        sync_status="IMPORTED",
                                        payment_id=int(new_id) if new_id is not None else None,
                                    )
                                    self._debug("[QBO IMPORT] mapping inserted", qbo_payment_id)
                                else:
                                    self._debug("[QBO IMPORT] mapping skip: missing fieldcraft_customer_id", qbo_payment_id)
                            except Exception:
                                self._debug("[QBO IMPORT] mapping insert exception", qbo_payment_id)
                                logger.exception("QuickBooks payment mapping insert failed")

                        summary["imported"] += 1
                        summary["results"].append({
                            "status": "success",
                            "action": "createdInFieldCraft",
                            "qboPaymentId": qbo_payment_id,
                            "customerUid": customer_uid,
                            "amount": total_amt,
                            "txnDate": txn_date,
                            "paymentRef": ref_no,
                        })

                        self._debug("[QBO IMPORT] imported++", {"imported": summary["imported"], "processed": summary["processed"]})

                    except Exception as e:
                        summary["failed"] += 1
                        summary[const.KEY_FAILURES].append({"paymentId": pay.get(const.QBO_KEY_ID), "error": str(e)})
                        self._debug("[QBO IMPORT] payment processing exception", {"paymentId": pay.get(const.QBO_KEY_ID), "error": str(e)})
                        logger.exception("QuickBooks payment processing failed")

                if len(batch) < page_size:
                    self._debug("[QBO IMPORT] breaking: batch < page_size", {"batch_size": len(batch), "page_size": page_size})
                    break
                position += len(batch)
                self._debug("[QBO IMPORT] next position", position)

            self._debug("[QBO IMPORT] import_payments end")
            self._debug("[QBO IMPORT] summary", summary)
            return summary

        except Exception as e:
            self._debug("[QBO IMPORT] import_payments fatal exception", str(e))
            logger.exception(
                "QuickBooks payment import failed | tenant=%s service_id=%s",
                tenant_id,
                service_id,
            )
            summary[const.KEY_OK] = False
            summary[const.KEY_MESSAGE] = str(e)
            return summary

    def _refresh_access_token(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        *,
        tenant_id: Optional[str] = None,
        service_id: Optional[str] = None,
        current_connection: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not tenant_id or not service_id:
            # Kept for backward-compatible direct unit calls. The sync entrypoints
            # always provide tenant/service context so token rotation is persisted.
            token_url = self.qbo_service.token_url
            basic = requests.auth._basic_auth_str(client_id, client_secret)
            response = requests.post(
                token_url,
                headers={
                    "Authorization": basic,
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"grant_type": "refresh_token", "refresh_token": refresh_token},
                timeout=30,
            )
            if response.status_code != 200:
                raise RuntimeError(f"QuickBooks token refresh failed with status {response.status_code}")
            payload = qbo_api.safe_json(response)
            access_token = payload.get("access_token")
            if not access_token:
                raise RuntimeError("QuickBooks token refresh response is missing access_token")
            return str(access_token)

        oauth_client = QuickBooksOAuthClient(repository=self.qbo_repo)
        return oauth_client.refresh_access_token(
            tenant_id=tenant_id,
            service_id=service_id,
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            token_url=self.qbo_service.token_url,
            current_connection=current_connection or {},
            debug_context={"operation": "payment_import"},
        )


    def import_payments_by_ids(
            self,
            *,
            tenant_id: str,
            service_id: str,
            payment_ids: list[str],
            dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Import specific QBO Payment IDs into tenant DB.

        Used by QuickBooks webhooks so we don't have to run full paging import.
        Idempotent via payment_external_map (external_payment_id).
        """
        self._debug("[QBO IMPORT] import_payments_by_ids start")
        self._debug("[QBO IMPORT] params", {
            "tenant_id": tenant_id,
            "service_id": service_id,
            "requested_ids": len(payment_ids or []),
            "dry_run": dry_run,
        })
        pay_method_name = ""
        started = datetime.now(timezone.utc).isoformat()
        summary: Dict[str, Any] = {
            "ok": True,
            "tenantId": tenant_id,
            "serviceId": service_id,
            "startedAt": started,
            "dryRun": dry_run,
            "requestedIds": len(payment_ids or []),
            "processed": 0,
            "imported": 0,
            "skippedAlreadyMapped": 0,
            "skippedNoCustomerMapping": 0,
            "failed": 0,
            "failures": [],
            "results": [],
        }

        payment_ids = [str(x).strip() for x in (payment_ids or []) if str(x).strip()]
        self._debug("[QBO IMPORT] normalized payment_ids", payment_ids)

        if not payment_ids:
            self._debug("[QBO IMPORT] returning: No payment IDs")
            return {**summary, "ok": True, "message": "No payment IDs"}

        try:
            self._debug("[QBO IMPORT] fetching connection getAdditionalInfo")
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            self._debug("[QBO IMPORT] connection fetched")

            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = str(connection.get(const.KEY_ENV) or "sandbox").strip().lower()
            status = (connection.get(const.KEY_STATUS) or "").lower()
            external_account_id = connection.get(const.KEY_REALM_ID)

            self._debug("[QBO IMPORT] connection fields", {
                "env": env,
                "status": status,
                "has_refresh_token": bool(refresh_token),
                "has_realm_id": bool(realm_id),
                "external_account_id": external_account_id,
            })

            if status and status != "connected":
                self._debug("[QBO IMPORT] returning: not connected", status)
                return {**summary, "ok": False, "message": f"QuickBooks is not connected (status={status})"}
            if not refresh_token or not realm_id:
                self._debug("[QBO IMPORT] returning: missing refreshToken/realmId")
                return {**summary, "ok": False, "message": "QuickBooks not connected (missing refreshToken/realmId)."}

            self._debug("[QBO IMPORT] fetching config get_quickbooks_config")
            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id)
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)
            self._debug("[QBO IMPORT] config fields", {
                "has_client_id": bool(client_id),
                "has_client_secret": bool(client_secret),
            })

            if not client_id or not client_secret:
                self._debug("[QBO IMPORT] returning: missing ClientId/ClientSecret")
                return {
                    **summary,
                    "ok": False,
                    "message": "QuickBooks client credentials missing (ClientId/ClientSecret).",
                }

            self._debug("[QBO IMPORT] refreshing access token")
            access_token = self._refresh_access_token(
                client_id,
                client_secret,
                refresh_token,
                tenant_id=tenant_id,
                service_id=service_id,
                current_connection=connection,
            )
            self._debug("[QBO IMPORT] access token refreshed")

            base_url = self._get_qbo_api_base(env)
            self._debug("[QBO IMPORT] base_url", base_url)

            for pid in payment_ids:
                summary["processed"] += 1
                self._debug("[QBO IMPORT] processing pid", {"pid": pid, "processed": summary["processed"]})

                try:
                    existing = self.payment_map_repo.get_by_external_payment_id(tenant_id, pid)
                    if existing:
                        summary["skippedAlreadyMapped"] += 1
                        self._debug("[QBO IMPORT] skip: already mapped", pid)
                        continue

                    pay = self._get_qbo_payment_by_id(
                        base_url=base_url,
                        realm_id=str(realm_id),
                        access_token=access_token,
                        payment_id=pid,
                    )
                    self._debug("[QBO IMPORT] fetched payment", {"pid": pid, "found": bool(pay)})

                    if not pay:
                        summary["failed"] += 1
                        summary[const.KEY_FAILURES].append({"paymentId": pid, "error": "Payment not found"})
                        self._debug("[QBO IMPORT] failed: payment not found", pid)
                        continue

                    cust_obj = (pay.get("CustomerRef") or {})
                    cust_ref = cust_obj.get("value")
                    cust_name = (cust_obj.get("name") or "").strip()
                    self._debug("[QBO IMPORT] cust_ref", cust_ref)

                    # If payment belongs to a deleted customer in QBO, skip with a clearer reason
                    if self._is_deleted_customer_ref(cust_obj):
                        summary.setdefault("skippedCustomerDeleted", 0)
                        summary["skippedCustomerDeleted"] += 1
                        summary["results"].append({
                            "status": "skipped",
                            "action": "customerDeleted",
                            "qboPaymentId": qbo_payment_id,
                            "customerRef": cust_ref,
                            "customerName": cust_name or None,
                            "reason": "Customer is deleted in QuickBooks",
                        })
                        self._debug("[QBO IMPORT] skip: customer deleted", {"cust_ref": cust_ref, "payment_id": qbo_payment_id, "customer": cust_name})
                        continue

                    customer_uid = self.customer_repo.get_customer_uuid_by_external_customer_id(
                        tenant_id, str(cust_ref) if cust_ref is not None else ""
                    )
                    self._debug("[QBO IMPORT] customer_uid", customer_uid)

                    if not customer_uid:
                        summary["skippedNoCustomerMapping"] += 1
                        summary["results"].append({
                            "status": "skipped",
                            "action": "noCustomerMapping",
                            "qboPaymentId": qbo_payment_id,
                            "customerRef": cust_ref,
                            "customerName": cust_name or None,
                            "reason": "No customer mapping in FIELDCRAFT",
                        })
                        self._debug("[QBO IMPORT] skip: no customer mapping", {"cust_ref": cust_ref, "payment_id": qbo_payment_id, "customer": cust_name})
                        continue

                    self._debug("[QBO IMPORT] payment payload received", pay)

                    total_amt = pay.get("TotalAmt")
                    txn_date = pay.get("TxnDate")
                    received_time = self._received_time_from_txn_date(txn_date)
                    ref_no = pay.get("PaymentRefNum")
                    invoice_id = self._extract_first_linked_invoice_id(pay)

                    self._debug("[QBO IMPORT] payment details", {
                        "pid": pid,
                        "total_amt": total_amt,
                        "txn_date": txn_date,
                        "received_time": str(received_time),
                        "ref_no": ref_no,
                        "invoice_id": invoice_id,
                        "dry_run": dry_run,
                    })

                    if not dry_run:
                        self._debug("[QBO IMPORT] inserting payment_receive_from_qbo", pid)
                        new_id = self.tenant_pay_repo.insert_payment_receive_from_qbo(
                            tenant_db=tenant_id,
                            customer_uid=customer_uid,
                            amount=float(total_amt) if total_amt is not None else 0.0,
                            received_time=received_time,
                            currency_code=None,
                            pay_method=QuickBooksPaymentImportService.get_payment_method_name(self,
                                                                                              pay,base_url=base_url,realm_id=str(realm_id),access_token=access_token,) or None,
                            reference_no=None,
                            status="Verified",
                            additional_info=json.dumps({"qboPayment": pay}, default=str),
                            payment_transaction_id= None,
                            payment_reference_no=f"QBO Payment {pid}" if pid is not None else None,
                            payment_receipt_id=str(pid),
                            fieldcraft_transaction_id = self.transaction_id(),
                        )
                        self._debug("[QBO IMPORT] inserted payment_receive_from_qbo", {"pid": pid, "new_id": new_id})

                        # store mapping
                        try:
                            self._debug("[QBO IMPORT] inserting mapping", pid)
                            fieldcraft_customer_id = self.customer_repo.get_customer_id_by_uuid(customer_uid)
                            self._debug("[QBO IMPORT] fieldcraft_customer_id", fieldcraft_customer_id)
                            if fieldcraft_customer_id:
                                self.payment_map_repo.insert_mapping(
                                    tenant_id=tenant_id,
                                    customer_id=int(fieldcraft_customer_id),
                                    input_source="QUICKBOOKS",
                                    external_payment_id=str(pid),
                                    external_invoice_id=str(invoice_id) if invoice_id else None,
                                    external_account_id=str(external_account_id) if external_account_id else None,
                                    sync_status="IMPORTED",
                                    payment_id=int(new_id) if new_id is not None else None,
                                )
                                self._debug("[QBO IMPORT] mapping inserted", pid)
                            else:
                                self._debug("[QBO IMPORT] mapping skip: missing fieldcraft_customer_id", pid)
                        except Exception:
                            self._debug("[QBO IMPORT] mapping insert exception", pid)
                            logger.exception("QuickBooks payment processing failed")
                            pass
                    else:
                        self._debug("[QBO IMPORT] dry_run: skip insert + mapping", pid)

                    summary["imported"] += 1
                    summary["results"].append({
                        "status": "success",
                        "action": "createdInFieldCraft",
                        "qboPaymentId": qbo_payment_id,
                        "customerUid": customer_uid,
                        "amount": total_amt,
                        "txnDate": txn_date,
                        "paymentRef": ref_no,
                    })

                    self._debug("[QBO IMPORT] imported++", {"imported": summary["imported"], "processed": summary["processed"]})

                except Exception as e:
                    summary["failed"] += 1
                    summary[const.KEY_FAILURES].append({"paymentId": pid, "error": str(e)})
                    self._debug("[QBO IMPORT] pid processing exception", {"pid": pid, "error": str(e)})
                    logger.exception("QuickBooks payment processing failed")

            self._debug("[QBO IMPORT] import_payments_by_ids end")
            self._debug("[QBO IMPORT] summary", summary)
            return summary

        except Exception as e:
            self._debug("[QBO IMPORT] import_payments_by_ids fatal exception", str(e))
            logger.exception(
                "QuickBooks payment import-by-id failed | tenant=%s service_id=%s",
                tenant_id,
                service_id,
            )
            summary[const.KEY_OK] = False
            summary[const.KEY_MESSAGE] = str(e)
            return summary

    @staticmethod
    def _get_qbo_api_base(env: str) -> str:
        return QBODiscoveryClient.get_qbo_api_base(env)

    def _query_qbo_payments(
            self,
            *,
            base_url: str,
            realm_id: str,
            access_token: str,
            start_position: int,
            page_size: int,
    ):
        q = f"select * from Payment startposition {start_position} maxresults {page_size}"
        url = f"{base_url}/v3/company/{realm_id}/query"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        self._debug("[QBO IMPORT] _query_qbo_payments request", {"url": url, "query": q})

        resp = requests.get(url, headers=headers, params={"query": q}, timeout=60)
        self._debug("[QBO IMPORT] _query_qbo_payments response", {"status_code": resp.status_code})

        if resp.status_code != 200:
            raise RuntimeError(
                f"QuickBooks Payment query failed with status {resp.status_code}"
            )
        data = qbo_api.safe_json(resp)
        qr = data.get("QueryResponse") if isinstance(data, dict) else None
        payments = (qr or {}).get("Payment") if isinstance(qr, dict) else None
        if isinstance(payments, list):
            self._debug("[QBO IMPORT] _query_qbo_payments payments list", len(payments))
            return payments
        if isinstance(payments, dict):
            self._debug("[QBO IMPORT] _query_qbo_payments payments dict -> 1")
            return [payments]
        self._debug("[QBO IMPORT] _query_qbo_payments payments empty")
        return []

    def _get_qbo_payment_by_id(
            self,
            *,
            base_url: str,
            realm_id: str,
            access_token: str,
            payment_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetch a single Payment by id from QBO."""
        url = f"{base_url}/v3/company/{realm_id}/payment/{payment_id}"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        self._debug("[QBO IMPORT] _get_qbo_payment_by_id request", {"url": url, "payment_id": payment_id})

        resp = requests.get(url, headers=headers, timeout=60)
        self._debug("[QBO IMPORT] _get_qbo_payment_by_id response", {"status_code": resp.status_code})

        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise RuntimeError(
                f"QuickBooks Payment read failed with status {resp.status_code}"
            )
        data = qbo_api.safe_json(resp)
        if isinstance(data, dict):
            pay = data.get("Payment")
            if isinstance(pay, dict):
                self._debug("[QBO IMPORT] _get_qbo_payment_by_id found Payment dict")
                return pay
        self._debug("[QBO IMPORT] _get_qbo_payment_by_id Payment missing/not dict")
        return None

    def _received_time_from_txn_date(self, txn_date: Optional[str]) -> datetime:
        self._debug("[QBO IMPORT] _received_time_from_txn_date", txn_date)
        if txn_date:
            try:
                # TxnDate in QBO is YYYY-MM-DD
                dt = datetime.fromisoformat(txn_date).replace(tzinfo=timezone.utc)
                self._debug("[QBO IMPORT] _received_time_from_txn_date parsed", str(dt))
                return dt
            except Exception:
                self._debug("[QBO IMPORT] _received_time_from_txn_date parse failed")
                logger.debug("QuickBooks payment date parsing failed", exc_info=True)
        now = datetime.now(timezone.utc)
        self._debug("[QBO IMPORT] _received_time_from_txn_date fallback now", str(now))
        return now

    def _extract_first_linked_invoice_id(self, payment: Dict[str, Any]) -> Optional[str]:
        """Try to extract first linked invoice TxnId from the Payment payload."""
        self._debug("[QBO IMPORT] _extract_first_linked_invoice_id start")
        lines = payment.get("Line")
        if not isinstance(lines, list):
            self._debug("[QBO IMPORT] _extract_first_linked_invoice_id no Line list")
            return None
        for ln in lines:
            linked = ln.get("LinkedTxn") if isinstance(ln, dict) else None
            if not isinstance(linked, list):
                continue
            for lt in linked:
                if not isinstance(lt, dict):
                    continue
                if (lt.get("TxnType") or "").lower() == "invoice" and lt.get("TxnId"):
                    inv = str(lt.get("TxnId"))
                    self._debug("[QBO IMPORT] _extract_first_linked_invoice_id found", inv)
                    return inv
        self._debug("[QBO IMPORT] _extract_first_linked_invoice_id none")
        return None

    def get_payment_method_name(self,
                                payment: Dict[str, Any],
                                *,
                                base_url: str,
                                realm_id: str,
                                access_token: str,
                                ) -> Optional[str]:
        ref = payment.get("PaymentMethodRef") or {}
        if not isinstance(ref, dict):
            return None

        # If QBO already sent the name, use it
        name = ref.get(const.KEY_NAME)
        if isinstance(name, str) and name.strip():
            return name.strip()

        # Otherwise fetch PaymentMethod by id and return its Name
        pm_id = ref.get("value")
        if not pm_id:
            return None

        url = f"{base_url}/v3/company/{realm_id}/paymentmethod/{pm_id}"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        r = requests.get(url, headers=headers, timeout=30)
        if not r.ok:
            return None

        data = r.json() if r.text else {}
        pm = data.get("PaymentMethod") or {}
        return pm.get("Name")

    def transaction_id(self) -> str:

        mode = (os.getenv("FIELDCRAFT_TXN_ID_MODE") or "uuid128").strip().lower()
        u = uuid.uuid4().int

        if mode in ("uuid63", "bigint", "int64"):
            return str(u & ((1 << 63) - 1))

        return str(u)
