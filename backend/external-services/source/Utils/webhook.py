"""QuickBooks webhook verification and background dispatch helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from source.Utils import constants as const

logger = logging.getLogger(__name__)

_TRUE_VALUES = {"1", "true", "yes", "on"}
_LOCAL_ENV_VALUES = {"local", "dev", "development", "test", "testing"}


def _runtime_env() -> str:
    return (
        os.getenv("APP_ENV")
        or os.getenv("ENV")
        or os.getenv("ENVIRONMENT")
        or os.getenv("STAGE")
        or os.getenv("FASTAPI_ENV")
        or ""
    ).strip().lower()


def _signature_bypass_allowed() -> bool:
    requested = str(os.getenv("QBO_WEBHOOK_SKIP_SIGNATURE_VERIFY") or "").strip().lower()
    return requested in _TRUE_VALUES and _runtime_env() in _LOCAL_ENV_VALUES


def _verify_qbo_webhook_signature(raw_body: bytes, signature: Optional[str]) -> None:
    """Verify base64(HMAC-SHA256(verifier token, raw request body))."""
    if _signature_bypass_allowed():
        logger.warning("QuickBooks webhook signature verification bypassed in local/test runtime")
        return

    verifier_token = str(os.getenv("QBO_WEBHOOK_VERIFIER_TOKEN") or "").strip()
    if not verifier_token:
        logger.error("QBO_WEBHOOK_VERIFIER_TOKEN is not configured")
        raise HTTPException(status_code=500, detail="QuickBooks webhook verification is not configured")

    expected = base64.b64encode(
        hmac.new(verifier_token.encode("utf-8"), raw_body or b"", hashlib.sha256).digest()
    ).decode("ascii")

    if not signature or not hmac.compare_digest(expected, str(signature).strip()):
        logger.warning("QuickBooks webhook signature validation failed")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


def _extract_webhook_realm_and_changes(
    payload: Dict[str, Any],
) -> tuple[Optional[str], List[Dict[str, str]]]:
    """Extract and de-duplicate entity changes from an Intuit webhook payload."""
    if not isinstance(payload, dict):
        return None, []

    realm_id: Optional[str] = None
    changes: List[Dict[str, str]] = []
    events = payload.get("eventNotifications")
    if not isinstance(events, list):
        return None, []

    for event in events:
        if not isinstance(event, dict):
            continue
        if not realm_id and event.get(const.KEY_REALM_ID) is not None:
            realm_id = str(event.get(const.KEY_REALM_ID)).strip() or None

        data_change = event.get("dataChangeEvent")
        entities = data_change.get("entities") if isinstance(data_change, dict) else None
        if not isinstance(entities, list):
            continue

        for entity in entities:
            if not isinstance(entity, dict):
                continue
            name = str(entity.get(const.KEY_NAME) or "").strip()
            entity_id = str(entity.get("id") or "").strip()
            operation = str(entity.get("operation") or "").strip()
            if name and entity_id:
                changes.append({"name": name, "id": entity_id, "operation": operation})

    unique: List[Dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for change in changes:
        key = (change["name"], change["id"], change["operation"])
        if key not in seen:
            seen.add(key)
            unique.append(change)

    logger.info(
        "QuickBooks webhook parsed | realm_id_present=%s event_count=%s unique_change_count=%s",
        bool(realm_id),
        len(events),
        len(unique),
    )
    return realm_id, unique


def _process_qbo_webhook(
    tenant_id: str,
    service_id: str,
    changes: List[Dict[str, str]],
) -> None:
    """Dispatch supported QuickBooks webhook changes to idempotent import services."""
    payment_ids: List[str] = []
    account_ids: List[str] = []
    customer_ids: List[str] = []
    bill_ids: List[str] = []

    for change in changes or []:
        name = str(change.get(const.KEY_NAME) or "").strip().lower()
        operation = str(change.get("operation") or "").strip().lower()
        entity_id = str(change.get("id") or "").strip()
        if not entity_id:
            continue

        if name == "payment" and operation in {"", "create"}:
            payment_ids.append(entity_id)
        elif name == "account" and operation in {"", "create"}:
            account_ids.append(entity_id)
        elif name == "bill" and operation in {"", "create", "update"}:
            bill_ids.append(entity_id)
        elif name == "customer":
            customer_ids.append(entity_id)

    logger.info(
        "Dispatching QuickBooks webhook changes | tenant=%s service_id=%s payments=%s accounts=%s bills=%s customers=%s",
        tenant_id,
        service_id,
        len(payment_ids),
        len(account_ids),
        len(bill_ids),
        len(customer_ids),
    )

    try:
        if payment_ids:
            from source.quickbooks.QuickBooksPaymentImportService import QuickBooksPaymentImportService

            QuickBooksPaymentImportService().import_payments_by_ids(
                tenant_id=tenant_id,
                service_id=service_id,
                payment_ids=payment_ids,
            )

        if account_ids:
            from source.quickbooks.QuickBooksAccountImportService import QuickBooksAccountImportService

            QuickBooksAccountImportService().import_accounts_by_ids(
                tenant_id=tenant_id,
                service_id=service_id,
                account_ids=account_ids,
            )

        if bill_ids:
            from source.quickbooks.QuickBooksBillImportService import QuickBooksBillImportService

            QuickBooksBillImportService().import_bills_by_ids(
                tenant_id=tenant_id,
                service_id=service_id,
                bill_ids=bill_ids,
            )

        if customer_ids:
            from source.quickbooks.QuickBooksCustomerImportService import QuickBooksCustomerImportService

            QuickBooksCustomerImportService().import_customers_by_ids(
                tenant_id=tenant_id,
                service_id=service_id,
                customer_ids=customer_ids,
            )
    except Exception:
        logger.exception(
            "QuickBooks webhook background processing failed | tenant=%s service_id=%s",
            tenant_id,
            service_id,
        )
