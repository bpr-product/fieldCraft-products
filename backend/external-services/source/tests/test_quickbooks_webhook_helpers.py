from __future__ import annotations

import base64
import hashlib
import hmac
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from source.Utils.webhook import (
    _extract_webhook_realm_and_changes,
    _process_qbo_webhook,
    _verify_qbo_webhook_signature,
)


def _signature(token: str, body: bytes) -> str:
    digest = hmac.new(token.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def test_webhook_signature_is_verified(monkeypatch) -> None:
    body = b'{"eventNotifications":[]}'
    monkeypatch.setenv("QBO_WEBHOOK_VERIFIER_TOKEN", "verifier")
    monkeypatch.delenv("QBO_WEBHOOK_SKIP_SIGNATURE_VERIFY", raising=False)

    _verify_qbo_webhook_signature(body, _signature("verifier", body))

    with pytest.raises(HTTPException) as exc_info:
        _verify_qbo_webhook_signature(body, "invalid")
    assert exc_info.value.status_code == 401


def test_signature_bypass_is_rejected_outside_local_runtime(monkeypatch) -> None:
    monkeypatch.setenv("QBO_WEBHOOK_SKIP_SIGNATURE_VERIFY", "true")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("QBO_WEBHOOK_VERIFIER_TOKEN", raising=False)

    with pytest.raises(HTTPException) as exc_info:
        _verify_qbo_webhook_signature(b"{}", None)
    assert exc_info.value.status_code == 500


def test_signature_bypass_is_allowed_for_tests(monkeypatch) -> None:
    monkeypatch.setenv("QBO_WEBHOOK_SKIP_SIGNATURE_VERIFY", "true")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("QBO_WEBHOOK_VERIFIER_TOKEN", raising=False)

    _verify_qbo_webhook_signature(b"{}", None)


def test_extract_changes_deduplicates_entities() -> None:
    payload = {
        "eventNotifications": [
            {
                "realmId": "realm-1",
                "dataChangeEvent": {
                    "entities": [
                        {"name": "Bill", "id": "10", "operation": "Update"},
                        {"name": "Bill", "id": "10", "operation": "Update"},
                        {"name": "Customer", "id": "20", "operation": "Create"},
                    ]
                },
            }
        ]
    }

    realm_id, changes = _extract_webhook_realm_and_changes(payload)

    assert realm_id == "realm-1"
    assert changes == [
        {"name": "Bill", "id": "10", "operation": "Update"},
        {"name": "Customer", "id": "20", "operation": "Create"},
    ]


def test_dispatch_groups_supported_entity_types() -> None:
    payment_importer = MagicMock()
    bill_importer = MagicMock()
    customer_importer = MagicMock()
    account_importer = MagicMock()

    with patch(
        "source.quickbooks.QuickBooksPaymentImportService.QuickBooksPaymentImportService",
        return_value=payment_importer,
    ), patch(
        "source.quickbooks.QuickBooksBillImportService.QuickBooksBillImportService",
        return_value=bill_importer,
    ), patch(
        "source.quickbooks.QuickBooksCustomerImportService.QuickBooksCustomerImportService",
        return_value=customer_importer,
    ), patch(
        "source.quickbooks.QuickBooksAccountImportService.QuickBooksAccountImportService",
        return_value=account_importer,
    ):
        _process_qbo_webhook(
            "tn1",
            "6",
            [
                {"name": "Payment", "id": "1", "operation": "Create"},
                {"name": "Payment", "id": "2", "operation": "Update"},
                {"name": "Bill", "id": "3", "operation": "Update"},
                {"name": "Customer", "id": "4", "operation": "Delete"},
                {"name": "Account", "id": "5", "operation": "Create"},
            ],
        )

    payment_importer.import_payments_by_ids.assert_called_once_with(
        tenant_id="tn1", service_id="6", payment_ids=["1"]
    )
    bill_importer.import_bills_by_ids.assert_called_once_with(
        tenant_id="tn1", service_id="6", bill_ids=["3"]
    )
    customer_importer.import_customers_by_ids.assert_called_once_with(
        tenant_id="tn1", service_id="6", customer_ids=["4"]
    )
    account_importer.import_accounts_by_ids.assert_called_once_with(
        tenant_id="tn1", service_id="6", account_ids=["5"]
    )
