from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

import requests

from source.Utils import QBODiscoveryClient
from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils import constants as const
from source.Utils.auditlog import safe_audit_log

logger = logging.getLogger(__name__)


class QuickBooksPaymentCreateService:
    """Export one FIELDCRAFT customer payment to QuickBooks and persist its mapping."""

    def __init__(self) -> None:
        from source.dal.CustomerRepository import CustomerRepository
        from source.dal.PaymentRepository import PaymentRepository
        from source.dal.QuickBooksRepository import QuickBooksRepository
        from source.service.QuickBooksService import QuickBooksService

        self.customer_repo = CustomerRepository()
        self.payment_repo = PaymentRepository()
        self.qbo_repo = QuickBooksRepository()
        self.qbo_service = QuickBooksService()

    def _audit(
        self,
        *,
        tenant_id: str,
        action: str,
        status: str,
        payment_id: Optional[int] = None,
        context: Optional[str] = None,
    ) -> None:
        safe_audit_log(
            tenant_id=tenant_id,
            action=action,
            status=status,
            context=context,
            resource_id=str(payment_id) if payment_id is not None else None,
            resource_type="quickbooks_payment",
        )

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _validated_amount(value: Any) -> float:
        if value is None:
            raise ValueError("total_amount is required")
        try:
            amount = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("total_amount must be a valid number") from exc
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError("total_amount must be greater than zero")
        return amount

    @staticmethod
    def _validated_txn_date(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("txn_date is required")
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError as exc:
            raise ValueError("txn_date must use YYYY-MM-DD format") from exc

    def _failure(
        self,
        *,
        tenant_id: str,
        payment_id: Optional[int],
        message: str,
        action: str = "payment_export_failed",
        context: Optional[str] = None,
        **extra: Any,
    ) -> Dict[str, Any]:
        self._audit(
            tenant_id=tenant_id,
            action=action,
            status="failed",
            payment_id=payment_id,
            context=context or message,
        )
        return {"ok": False, "message": message, **extra}

    def create_payment(
        self,
        tenant_id: str,
        service_id: Optional[str],
        customer_uid: str,
        txn_date: str,
        total_amount: float,
        payment_receives_id: Optional[int] = None,
        qbo_invoice_id: Optional[str] = None,
        payment_method_id: Optional[str] = None,
        deposit_to_account_id: Optional[str] = None,
        payment_ref_num: Optional[str] = None,
        private_note: Optional[str] = None,
    ) -> Dict[str, Any]:
        tenant_id = self._clean(tenant_id)
        service_id = self._clean(service_id)
        customer_uid = self._clean(customer_uid)

        if not tenant_id:
            return {"ok": False, "message": "tenant_id is required"}
        if not service_id:
            service_id = self._clean(self.qbo_repo.getServiceIdByName("QuickBooks"))
        if not service_id:
            return self._failure(tenant_id=tenant_id, payment_id=payment_receives_id, message="QuickBooks service is not configured")
        if not customer_uid:
            return {"ok": False, "message": "customer_uid is required"}
        if payment_receives_id is None:
            return {"ok": False, "message": "payment_receives_id is required"}

        try:
            payment_id = int(payment_receives_id)
        except (TypeError, ValueError):
            return {"ok": False, "message": "payment_receives_id must be a valid integer"}
        if payment_id <= 0:
            return {"ok": False, "message": "payment_receives_id must be greater than zero"}

        try:
            amount = self._validated_amount(total_amount)
            resolved_txn_date = self._validated_txn_date(txn_date)
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}

        started = False
        try:
            customer_id = self.customer_repo.get_customer_id_by_uuid(customer_uid)
            if not customer_id:
                return self._failure(
                    tenant_id=tenant_id,
                    payment_id=payment_id,
                    message=f"Customer not found for customer_uid={customer_uid}",
                    context="customer_not_found",
                )

            existing_map = self.payment_repo.get_payment_mapping_by_payment_id(
                tenant_id=tenant_id, payment_id=payment_id
            )
            if existing_map:
                return {
                    "ok": True,
                    "skipped": True,
                    "reason": "already_mapped",
                    "tenantId": tenant_id,
                    "serviceId": service_id,
                    "customerUid": customer_uid,
                    "customerId": customer_id,
                    "payment_receives_id": payment_id,
                    "qboPaymentId": existing_map.get("external_payment_id"),
                }

            mapping = self.customer_repo.get_customer_mapping_details(tenant_id, customer_id) or {}
            qbo_customer_id = self._clean(mapping.get("external_customer_id"))
            mapped_realm_id = self._clean(mapping.get("external_account_id"))
            if not qbo_customer_id:
                return self._failure(
                    tenant_id=tenant_id,
                    payment_id=payment_id,
                    message="Customer is not mapped to QuickBooks (missing customer_external_map row)",
                    context="customer_mapping_missing",
                    tenantId=tenant_id,
                    customerId=customer_id,
                )

            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            if self._clean(connection.get(const.KEY_STATUS)).lower() != const.STATUS_CONNECTED.lower():
                return self._failure(
                    tenant_id=tenant_id, payment_id=payment_id,
                    message="QuickBooks not connected for this tenant", context="quickbooks_not_connected"
                )

            realm_id = self._clean(connection.get(const.KEY_REALM_ID))
            refresh_token = self._clean(connection.get(const.KEY_REFRESH_TOKEN))
            env = self._clean(connection.get(const.KEY_ENV) or "sandbox").lower()
            if not realm_id or not refresh_token:
                return self._failure(
                    tenant_id=tenant_id, payment_id=payment_id,
                    message="QuickBooks connection is missing realmId/refreshToken",
                    context="connection_incomplete",
                )
            if mapped_realm_id and mapped_realm_id != realm_id:
                return self._failure(
                    tenant_id=tenant_id, payment_id=payment_id,
                    message="Customer mapping belongs to a different QuickBooks company",
                    context="customer_realm_mismatch",
                )

            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
            client_id = self._clean(config.get(const.KEY_CLIENT_ID))
            client_secret = self._clean(config.get(const.KEY_CLIENT_SECRET))
            if not client_id or not client_secret:
                return self._failure(
                    tenant_id=tenant_id, payment_id=payment_id,
                    message="QuickBooks ClientId/ClientSecret missing", context="credentials_missing"
                )

            self._audit(
                tenant_id=tenant_id, action="payment_export_started", status="started",
                payment_id=payment_id, context=f"customerId={customer_id}"
            )
            started = True

            token = self._refresh_access_token_and_update_connection(
                tenant_id=tenant_id,
                service_id=service_id,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
                current_connection=connection,
            )
            access_token = self._clean(token.get("access_token"))
            if not access_token:
                raise RuntimeError("QuickBooks access token refresh returned no access token")

            url = f"{self._get_qbo_api_base(env)}/v3/company/{realm_id}/payment"
            payload: Dict[str, Any] = {
                "CustomerRef": {"value": qbo_customer_id},
                "TxnDate": resolved_txn_date,
                "TotalAmt": amount,
            }
            if payment_ref_num:
                payload["PaymentRefNum"] = self._clean(payment_ref_num)
            if private_note:
                payload["PrivateNote"] = self._clean(private_note)
            if payment_method_id:
                payload["PaymentMethodRef"] = {"value": self._clean(payment_method_id)}
            if deposit_to_account_id:
                payload["DepositToAccountRef"] = {"value": self._clean(deposit_to_account_id)}
            if qbo_invoice_id:
                payload["Line"] = [{
                    "Amount": amount,
                    "LinkedTxn": [{"TxnId": self._clean(qbo_invoice_id), "TxnType": "Invoice"}],
                }]

            response = qbo_api.qbo_post(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=60,
            )
            if response.status_code not in (200, 201):
                message = qbo_api.build_response_error(response, "QuickBooks create payment failed")
                return self._failure(
                    tenant_id=tenant_id, payment_id=payment_id, message=message,
                    context="qbo_create_failed", status=response.status_code
                )

            data = qbo_api.safe_json(response)
            payment_obj = data.get("Payment") if isinstance(data, dict) else None
            qbo_payment_id = self._clean((payment_obj or {}).get(const.QBO_KEY_ID))
            if not qbo_payment_id:
                return self._failure(
                    tenant_id=tenant_id, payment_id=payment_id,
                    message="QuickBooks created the payment but returned no payment ID",
                    context="qbo_payment_id_missing",
                )

            try:
                self.payment_repo.insert_payment_mapping(
                    customer_id=int(customer_id),
                    tenant_id=tenant_id,
                    input_source="FIELDCRAFT",
                    external_payment_id=qbo_payment_id,
                    external_invoice_id=self._clean(qbo_invoice_id) or None,
                    external_account_id=realm_id,
                    payment_id=payment_id,
                    sync_status="EXPORTED",
                )
            except Exception:
                logger.exception(
                    "QuickBooks payment was created but its local mapping could not be saved | tenant=%s payment_id=%s qbo_payment_id=%s",
                    tenant_id, payment_id, qbo_payment_id,
                )
                return self._failure(
                    tenant_id=tenant_id, payment_id=payment_id,
                    message="QuickBooks payment was created, but its FIELDCRAFT mapping could not be saved",
                    context="payment_mapping_save_failed",
                    qboPaymentId=qbo_payment_id, remoteCreated=True,
                )

            self._audit(
                tenant_id=tenant_id, action="payment_export_completed", status="success",
                payment_id=payment_id, context=f"qboPaymentId={qbo_payment_id}"
            )
            return {
                "ok": True,
                "tenantId": tenant_id,
                "serviceId": service_id,
                "customerUid": customer_uid,
                "customerId": customer_id,
                "payment_receives_id": payment_id,
                "qboCustomerId": qbo_customer_id,
                "externalAccountId": realm_id,
                "realmIdPresent": True,
                "env": env,
                "qboPaymentId": qbo_payment_id,
                "payment": payment_obj or data,
            }
        except Exception as exc:
            logger.exception(
                "QuickBooks payment export failed | tenant=%s payment_id=%s", tenant_id, payment_id
            )
            if started:
                self._audit(
                    tenant_id=tenant_id, action="payment_export_failed", status="failed",
                    payment_id=payment_id, context=type(exc).__name__
                )
            return {"ok": False, "message": "Create payment failed"}

    def _get_qbo_api_base(self, env: str) -> str:
        return QBODiscoveryClient.get_qbo_api_base(env).rstrip("/")

    def _refresh_access_token_and_update_connection(
        self,
        tenant_id: str,
        service_id: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        current_connection: Dict[str, Any],
        flow_id: str = "qbo_pay",
    ) -> Dict[str, Any]:
        token_url = self.qbo_service.token_url
        response = requests.post(
            token_url,
            auth=requests.auth.HTTPBasicAuth(client_id, client_secret),
            headers={"Accept": "application/json"},
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError("QuickBooks refresh token exchange failed")

        token_json = response.json() or {}
        access_token = self._clean(token_json.get("access_token"))
        if not access_token:
            raise RuntimeError("QuickBooks refresh token exchange returned no access token")

        new_refresh_token = self._clean(token_json.get("refresh_token"))
        if new_refresh_token:
            now = datetime.now(timezone.utc)
            expires_in = token_json.get("x_refresh_token_expires_in")
            updated = dict(current_connection or {})
            updated[const.KEY_REFRESH_TOKEN] = new_refresh_token
            if expires_in:
                updated[const.KEY_REFRESH_EXPIRES_AT] = (
                    now + timedelta(seconds=int(expires_in))
                ).isoformat()
            updated["updatedAt"] = now.isoformat()
            self.qbo_repo.updateAdditionalInfo(tenant_id, service_id, updated)

        return {"access_token": access_token, "token_json": token_json}
