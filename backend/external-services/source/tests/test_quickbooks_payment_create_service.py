from unittest.mock import MagicMock
import requests

from source.quickbooks.QuickBooksPaymentCreateService import QuickBooksPaymentCreateService
from source.Utils import constants as const


class _Resp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


def _make_service():
    svc = QuickBooksPaymentCreateService()

    svc.customer_repo = MagicMock()
    svc.payment_repo = MagicMock()
    svc.qbo_repo = MagicMock()
    svc.qbo_service = MagicMock()
    svc.audit_service = MagicMock()

    # service calls self._audit(...), but method is not defined in the file
    svc._audit = MagicMock()

    return svc


def test_create_payment_validation_tenant_id_required():
    svc = _make_service()

    out = svc.create_payment(
        tenant_id="",
        service_id="6",
        customer_uid="CUID",
        txn_date="2026-02-25",
        total_amount=10.0,
        payment_receives_id=1,
    )

    assert out["ok"] is False
    assert "tenant_id is required" in out["message"]


def test_create_payment_validation_customer_uid_required():
    svc = _make_service()

    out = svc.create_payment(
        tenant_id="tn1",
        service_id="6",
        customer_uid="",
        txn_date="2026-02-25",
        total_amount=10.0,
        payment_receives_id=1,
    )

    assert out["ok"] is False
    assert "customer_uid is required" in out["message"]


def test_create_payment_validation_txn_date_required():
    svc = _make_service()

    out = svc.create_payment(
        tenant_id="tn1",
        service_id="6",
        customer_uid="CUID",
        txn_date="",
        total_amount=10.0,
        payment_receives_id=1,
    )

    assert out["ok"] is False
    assert "txn_date is required" in out["message"]


def test_create_payment_validation_total_amount_required():
    svc = _make_service()

    out = svc.create_payment(
        tenant_id="tn1",
        service_id="6",
        customer_uid="CUID",
        txn_date="2026-02-25",
        total_amount=None,  # type: ignore
        payment_receives_id=1,
    )

    assert out["ok"] is False
    assert "total_amount is required" in out["message"]


def test_create_payment_validation_payment_receives_id_required():
    svc = _make_service()

    out = svc.create_payment(
        tenant_id="tn1",
        service_id="6",
        customer_uid="CUID",
        txn_date="2026-02-25",
        total_amount=10.0,
        payment_receives_id=None,
    )

    assert out["ok"] is False
    assert "payment_receives_id is required" in out["message"]


def test_create_payment_resolves_service_id_when_missing():
    svc = _make_service()

    svc.qbo_repo.getServiceIdByName.return_value = "6"
    svc.customer_repo.get_customer_id_by_uuid.return_value = 101
    svc.payment_repo.get_payment_mapping_by_payment_id.return_value = {
        "external_payment_id": "P-1",
        "sync_status": "EXPORTED",
    }

    out = svc.create_payment(
        tenant_id="tn1",
        service_id=None,
        customer_uid="CUID",
        txn_date="2026-02-25",
        total_amount=10.0,
        payment_receives_id=55,
    )

    assert out["ok"] is True
    assert out["skipped"] is True
    assert out["serviceId"] == "6"
    svc.qbo_repo.getServiceIdByName.assert_called_once_with("QuickBooks")


def test_create_payment_customer_not_found():
    svc = _make_service()

    svc.customer_repo.get_customer_id_by_uuid.return_value = None

    out = svc.create_payment(
        tenant_id="tn1",
        service_id="6",
        customer_uid="CUID",
        txn_date="2026-02-25",
        total_amount=10.0,
        payment_receives_id=1,
    )

    assert out["ok"] is False
    assert "Customer not found" in out["message"]
    svc._audit.assert_called_once()


def test_create_payment_already_mapped_skips_export():
    svc = _make_service()

    svc.customer_repo.get_customer_id_by_uuid.return_value = 101
    svc.payment_repo.get_payment_mapping_by_payment_id.return_value = {
        "external_payment_id": "QBO-PAY-999",
        "sync_status": "EXPORTED",
    }

    out = svc.create_payment(
        tenant_id="tn1",
        service_id="6",
        customer_uid="CUID",
        txn_date="2026-02-25",
        total_amount=10.0,
        payment_receives_id=77,
    )

    assert out["ok"] is True
    assert out["skipped"] is True
    assert out["reason"] == "already_mapped"
    assert out["qboPaymentId"] == "QBO-PAY-999"


def test_create_payment_missing_customer_mapping():
    svc = _make_service()

    svc.customer_repo.get_customer_id_by_uuid.return_value = 101
    svc.payment_repo.get_payment_mapping_by_payment_id.return_value = None
    svc.customer_repo.get_customer_mapping_details.return_value = None

    out = svc.create_payment(
        tenant_id="tn1",
        service_id="6",
        customer_uid="CUID",
        txn_date="2026-02-25",
        total_amount=10.0,
        payment_receives_id=77,
    )

    assert out["ok"] is False
    assert "not mapped to QuickBooks" in out["message"]


def test_create_payment_qbo_not_connected():
    svc = _make_service()

    svc.customer_repo.get_customer_id_by_uuid.return_value = 101
    svc.payment_repo.get_payment_mapping_by_payment_id.return_value = None
    svc.customer_repo.get_customer_mapping_details.return_value = {
        "external_customer_id": "QBO-CUST-1",
        "external_account_id": "RID",
    }
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_STATUS: "disconnected",
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
        const.KEY_REFRESH_TOKEN: "RT",
    }

    out = svc.create_payment(
        tenant_id="tn1",
        service_id="6",
        customer_uid="CUID",
        txn_date="2026-02-25",
        total_amount=10.0,
        payment_receives_id=77,
    )

    assert out["ok"] is False
    assert "not connected" in out["message"].lower()


def test_create_payment_missing_client_credentials():
    svc = _make_service()

    svc.customer_repo.get_customer_id_by_uuid.return_value = 101
    svc.payment_repo.get_payment_mapping_by_payment_id.return_value = None
    svc.customer_repo.get_customer_mapping_details.return_value = {
        "external_customer_id": "QBO-CUST-1",
        "external_account_id": "RID",
    }
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_STATUS: "connected",
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
        const.KEY_REFRESH_TOKEN: "RT",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: None,
        const.KEY_CLIENT_SECRET: None,
    }

    out = svc.create_payment(
        tenant_id="tn1",
        service_id="6",
        customer_uid="CUID",
        txn_date="2026-02-25",
        total_amount=10.0,
        payment_receives_id=77,
    )

    assert out["ok"] is False
    assert "ClientId/ClientSecret".lower() in out["message"].lower()


def test_create_payment_success_inserts_mapping(monkeypatch):
    svc = _make_service()

    svc.customer_repo.get_customer_id_by_uuid.return_value = 101
    svc.payment_repo.get_payment_mapping_by_payment_id.return_value = None
    svc.customer_repo.get_customer_mapping_details.return_value = {
        "external_customer_id": "QBO-CUST-1",
        "external_account_id": "RID",
    }
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_STATUS: "connected",
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
        const.KEY_REFRESH_TOKEN: "RT",
        "refreshExpiresAt": None,
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SEC",
    }

    monkeypatch.setattr(
        svc,
        "_refresh_access_token_and_update_connection",
        lambda **kwargs: {"access_token": "AT", "token_json": {"access_token": "AT"}},
    )
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda env: "https://sandbox-qbo")

    def fake_post(url, headers=None, json=None, timeout=None):
        assert "/v3/company/RID/payment" in url
        assert headers["Authorization"] == "Bearer AT"
        assert json["CustomerRef"]["value"] == "QBO-CUST-1"
        assert json["TxnDate"] == "2026-02-25"
        assert json["TotalAmt"] == 10.0
        return _Resp(
            200,
            json_data={"Payment": {const.QBO_KEY_ID: "QBO-PAY-1"}},
            text='{"Payment":{"Id":"QBO-PAY-1"}}',
        )

    from source.quickbooks import QuickBooksPaymentCreateService as payment_module
    monkeypatch.setattr(payment_module.qbo_api, "qbo_post", fake_post)

    out = svc.create_payment(
        tenant_id="tn1",
        service_id="6",
        customer_uid="CUID",
        txn_date="2026-02-25",
        total_amount=10.0,
        payment_receives_id=77,
        qbo_invoice_id="INV-9",
        payment_method_id="PM-1",
        deposit_to_account_id="DA-1",
        payment_ref_num="REF-1",
        private_note="note",
    )

    assert out["ok"] is True
    assert out["qboPaymentId"] == "QBO-PAY-1"

    svc.payment_repo.insert_payment_mapping.assert_called_once()
    kwargs = svc.payment_repo.insert_payment_mapping.call_args.kwargs
    assert kwargs["tenant_id"] == "tn1"
    assert kwargs["customer_id"] == 101
    assert kwargs["external_payment_id"] == "QBO-PAY-1"
    assert kwargs["external_invoice_id"] == "INV-9"
    assert kwargs["external_account_id"] == "RID"
    assert kwargs["payment_id"] == 77
    assert kwargs["sync_status"] == "EXPORTED"
    assert kwargs["input_source"] == "FIELDCRAFT"

    # started + success
    assert svc._audit.call_count == 2


def test_create_payment_qbo_create_fails(monkeypatch):
    svc = _make_service()

    svc.customer_repo.get_customer_id_by_uuid.return_value = 101
    svc.payment_repo.get_payment_mapping_by_payment_id.return_value = None
    svc.customer_repo.get_customer_mapping_details.return_value = {
        "external_customer_id": "QBO-CUST-1",
        "external_account_id": "RID",
    }
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_STATUS: "connected",
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
        const.KEY_REFRESH_TOKEN: "RT",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SEC",
    }

    monkeypatch.setattr(
        svc,
        "_refresh_access_token_and_update_connection",
        lambda **kwargs: {"access_token": "AT", "token_json": {"access_token": "AT"}},
    )
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda env: "https://sandbox-qbo")
    from source.quickbooks import QuickBooksPaymentCreateService as payment_module
    monkeypatch.setattr(
        payment_module.qbo_api,
        "qbo_post",
        lambda *a, **k: _Resp(400, json_data={}, text="Bad Request"),
    )

    out = svc.create_payment(
        tenant_id="tn1",
        service_id="6",
        customer_uid="CUID",
        txn_date="2026-02-25",
        total_amount=10.0,
        payment_receives_id=77,
    )

    assert out["ok"] is False
    assert "create payment failed" in out["message"].lower()
    svc.payment_repo.insert_payment_mapping.assert_not_called()


def test_refresh_access_token_rotates_refresh_token_updates_connection(monkeypatch):
    svc = _make_service()

    current = {
        const.KEY_REFRESH_TOKEN: "OLD_RT",
        "refreshExpiresAt": None,
    }

    def fake_post(url, auth=None, headers=None, data=None, timeout=None):
        assert data["grant_type"] == "refresh_token"
        return _Resp(
            200,
            json_data={
                "access_token": "AT",
                "refresh_token": "NEW_RT",
                "x_refresh_token_expires_in": 3600,
            },
            text='{"access_token":"AT","refresh_token":"NEW_RT","x_refresh_token_expires_in":3600}',
        )

    monkeypatch.setattr(requests, "post", fake_post)

    svc.qbo_service.token_url = "https://token"
    svc.qbo_repo.updateAdditionalInfo = MagicMock()

    out = svc._refresh_access_token_and_update_connection(
        tenant_id="tn1",
        service_id="6",
        client_id="CID",
        client_secret="SEC",
        refresh_token="OLD_RT",
        current_connection=current,
        flow_id="t1",
    )

    assert out["access_token"] == "AT"
    svc.qbo_repo.updateAdditionalInfo.assert_called_once()
    args = svc.qbo_repo.updateAdditionalInfo.call_args.args
    assert args[0] == "tn1"
    assert args[1] == "6"
    updated = args[2]
    assert updated[const.KEY_REFRESH_TOKEN] == "NEW_RT"
