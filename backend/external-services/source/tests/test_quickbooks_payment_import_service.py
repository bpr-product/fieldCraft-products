from unittest.mock import MagicMock
import requests

from source.quickbooks.QuickBooksPaymentImportService import QuickBooksPaymentImportService
from source.Utils import constants as const


class FakeResp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json

    @property
    def ok(self):
        return 200 <= int(self.status_code) < 300


def make_service():
    svc = QuickBooksPaymentImportService()

    svc.qbo_repo = MagicMock()
    svc.qbo_service = MagicMock()
    svc.customer_repo = MagicMock()
    svc.tenant_pay_repo = MagicMock()
    svc.payment_map_repo = MagicMock()

    # class calls self._audit(...) but method is not defined in file
    svc._audit = MagicMock()

    return svc


def connected_info():
    return {
        const.KEY_REFRESH_TOKEN: "RT",
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
        const.KEY_STATUS: "connected",
    }


def config_ok():
    return {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SEC",
    }


def sample_payment(
    qbo_payment_id="P1",
    customer_ref="C1",
    customer_name="John Doe",
    total_amt=100.0,
    txn_date="2026-03-01",
    deleted=False,
):
    name = f"{customer_name} (deleted)" if deleted else customer_name
    return {
        const.QBO_KEY_ID: qbo_payment_id,
        "CustomerRef": {"value": customer_ref, "name": name},
        "TotalAmt": total_amt,
        "TxnDate": txn_date,
        "PaymentRefNum": "REF-1",
        "PaymentMethodRef": {"name": "Cash"},
        "Line": [{"LinkedTxn": [{"TxnType": "Invoice", "TxnId": "INV-1"}]}],
    }


# ---------------------------------------------------------
# import_payments - validation / abort
# ---------------------------------------------------------
def test_import_payments_not_connected():
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_STATUS: "disconnected",
        const.KEY_ENV: "sandbox",
        const.KEY_REFRESH_TOKEN: "RT",
        const.KEY_REALM_ID: "RID",
    }

    out = svc.import_payments(tenant_id="tn1", service_id="6")

    assert out["ok"] is False
    assert "not connected" in out["message"].lower()


def test_import_payments_missing_refresh_or_realm():
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_STATUS: "connected",
        const.KEY_ENV: "sandbox",
        const.KEY_REFRESH_TOKEN: None,
        const.KEY_REALM_ID: None,
    }

    out = svc.import_payments(tenant_id="tn1", service_id="6")

    assert out["ok"] is False
    assert "missing refreshtoken/realmid" in out["message"].lower()


def test_import_payments_missing_client_credentials():
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: None,
        const.KEY_CLIENT_SECRET: None,
    }

    out = svc.import_payments(tenant_id="tn1", service_id="6")

    assert out["ok"] is False
    assert "client credentials missing" in out["message"].lower()


# ---------------------------------------------------------
# import_payments - loop behavior
# ---------------------------------------------------------
def test_import_payments_breaks_when_batch_empty(monkeypatch):
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *a, **k: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda env: "https://sandbox-qbo")
    monkeypatch.setattr(svc, "_query_qbo_payments", lambda **kwargs: [])

    out = svc.import_payments(tenant_id="tn1", service_id="6", page_size=10)

    assert out["ok"] is True
    assert out["processed"] == 0
    assert out["imported"] == 0


def test_import_payments_skip_already_mapped(monkeypatch):
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *a, **k: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda env: "https://sandbox-qbo")

    calls = {"n": 0}

    def fake_query(**kwargs):
        calls["n"] += 1
        return [sample_payment()] if calls["n"] == 1 else []

    monkeypatch.setattr(svc, "_query_qbo_payments", fake_query)

    svc.payment_map_repo.get_by_external_payment_id.return_value = {
        "payment_id": 777,
        "customer_id": 10,
    }
    svc.customer_repo.get_customer_uuid_by_id.return_value = "CUST-UUID"

    out = svc.import_payments(tenant_id="tn1", service_id="6")

    assert out["processed"] == 1
    assert out["skippedAlreadyMapped"] == 1
    assert out["imported"] == 0
    assert out["results"][0]["status"] == "skipped"
    assert out["results"][0]["action"] == "alreadyMapped"


def test_import_payments_skip_deleted_customer(monkeypatch):
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *a, **k: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda env: "https://sandbox-qbo")

    calls = {"n": 0}

    def fake_query(**kwargs):
        calls["n"] += 1
        return [sample_payment(deleted=True)] if calls["n"] == 1 else []

    monkeypatch.setattr(svc, "_query_qbo_payments", fake_query)

    svc.payment_map_repo.get_by_external_payment_id.return_value = None

    out = svc.import_payments(tenant_id="tn1", service_id="6")

    assert out["processed"] == 1
    assert out["skippedCustomerDeleted"] == 1
    assert out["imported"] == 0
    assert out["results"][0]["action"] == "customerDeleted"


def test_import_payments_fail_no_customer_mapping(monkeypatch):
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *a, **k: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda env: "https://sandbox-qbo")

    calls = {"n": 0}

    def fake_query(**kwargs):
        calls["n"] += 1
        return [sample_payment()] if calls["n"] == 1 else []

    monkeypatch.setattr(svc, "_query_qbo_payments", fake_query)

    svc.payment_map_repo.get_by_external_payment_id.return_value = None
    svc.customer_repo.get_customer_uuid_by_external_customer_id.return_value = None

    out = svc.import_payments(tenant_id="tn1", service_id="6")

    assert out["processed"] == 1
    assert out["failed"] == 1
    assert out["failedNoCustomerMapping"] == 1
    assert out["results"][0]["status"] == "failed"
    assert out["results"][0]["action"] == "noCustomerMapping"


def test_import_payments_dry_run_success(monkeypatch):
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *a, **k: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda env: "https://sandbox-qbo")

    calls = {"n": 0}

    def fake_query(**kwargs):
        calls["n"] += 1
        return [sample_payment()] if calls["n"] == 1 else []

    monkeypatch.setattr(svc, "_query_qbo_payments", fake_query)

    svc.payment_map_repo.get_by_external_payment_id.return_value = None
    svc.customer_repo.get_customer_uuid_by_external_customer_id.return_value = "CUST-UUID"

    out = svc.import_payments(tenant_id="tn1", service_id="6", dry_run=True)

    assert out["processed"] == 1
    assert out["imported"] == 1
    svc.tenant_pay_repo.insert_payment_receive_from_qbo.assert_not_called()
    svc.payment_map_repo.insert_mapping.assert_not_called()

    assert out["results"][0]["status"] == "success"
    assert out["results"][0]["action"] == "createdInFieldCraft"


def test_import_payments_success_inserts_payment_and_mapping(monkeypatch):
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *a, **k: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda env: "https://sandbox-qbo")

    calls = {"n": 0}

    def fake_query(**kwargs):
        calls["n"] += 1
        return [sample_payment()] if calls["n"] == 1 else []

    monkeypatch.setattr(svc, "_query_qbo_payments", fake_query)

    svc.payment_map_repo.get_by_external_payment_id.return_value = None
    svc.customer_repo.get_customer_uuid_by_external_customer_id.return_value = "CUST-UUID"
    svc.tenant_pay_repo.insert_payment_receive_from_qbo.return_value = 999
    svc.customer_repo.get_customer_id_by_uuid.return_value = 123

    out = svc.import_payments(tenant_id="tn1", service_id="6", dry_run=False)

    assert out["processed"] == 1
    assert out["imported"] == 1
    svc.tenant_pay_repo.insert_payment_receive_from_qbo.assert_called_once()
    svc.payment_map_repo.insert_mapping.assert_called_once()

    kwargs = svc.payment_map_repo.insert_mapping.call_args.kwargs
    assert kwargs["tenant_id"] == "tn1"
    assert kwargs["customer_id"] == 123
    assert kwargs["external_payment_id"] == "P1"
    assert kwargs["sync_status"] == "IMPORTED"
    assert kwargs["payment_id"] == 999


# ---------------------------------------------------------
# import_payments_by_ids
# ---------------------------------------------------------
def test_import_payments_by_ids_empty():
    svc = make_service()

    out = svc.import_payments_by_ids(
        tenant_id="tn1",
        service_id="6",
        payment_ids=[],
        dry_run=False,
    )

    assert out["ok"] is True
    assert "No payment IDs" in out["message"]


def test_import_payments_by_ids_skip_already_mapped(monkeypatch):
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *a, **k: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda env: "https://sandbox-qbo")

    svc.payment_map_repo.get_by_external_payment_id.return_value = {"id": 1}

    out = svc.import_payments_by_ids(
        tenant_id="tn1",
        service_id="6",
        payment_ids=["P1"],
    )

    assert out["processed"] == 1
    assert out["skippedAlreadyMapped"] == 1


def test_import_payments_by_ids_payment_not_found(monkeypatch):
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *a, **k: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda env: "https://sandbox-qbo")
    monkeypatch.setattr(svc, "_get_qbo_payment_by_id", lambda **kwargs: None)

    svc.payment_map_repo.get_by_external_payment_id.return_value = None

    out = svc.import_payments_by_ids(
        tenant_id="tn1",
        service_id="6",
        payment_ids=["P404"],
    )

    assert out["processed"] == 1
    assert out["failed"] == 1
    assert out["failures"][0]["paymentId"] == "P404"


def test_import_payments_by_ids_skip_deleted_customer(monkeypatch):
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *a, **k: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda env: "https://sandbox-qbo")
    svc.payment_map_repo.get_by_external_payment_id.return_value = None

    pay = sample_payment(deleted=True)
    monkeypatch.setattr(svc, "_get_qbo_payment_by_id", lambda **kwargs: pay)

    out = svc.import_payments_by_ids(
        tenant_id="tn1",
        service_id="6",
        payment_ids=["P1"],
    )

    assert out["processed"] == 1
    assert out["skippedCustomerDeleted"] == 1


def test_import_payments_by_ids_skip_no_customer_mapping(monkeypatch):
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *a, **k: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda env: "https://sandbox-qbo")
    svc.payment_map_repo.get_by_external_payment_id.return_value = None

    pay = sample_payment()
    monkeypatch.setattr(svc, "_get_qbo_payment_by_id", lambda **kwargs: pay)
    svc.customer_repo.get_customer_uuid_by_external_customer_id.return_value = None

    out = svc.import_payments_by_ids(
        tenant_id="tn1",
        service_id="6",
        payment_ids=["P1"],
    )

    assert out["processed"] == 1
    assert out["skippedNoCustomerMapping"] == 1


def test_import_payments_by_ids_dry_run(monkeypatch):
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *a, **k: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda env: "https://sandbox-qbo")
    svc.payment_map_repo.get_by_external_payment_id.return_value = None

    pay = sample_payment()
    monkeypatch.setattr(svc, "_get_qbo_payment_by_id", lambda **kwargs: pay)
    svc.customer_repo.get_customer_uuid_by_external_customer_id.return_value = "CUST-UUID"

    out = svc.import_payments_by_ids(
        tenant_id="tn1",
        service_id="6",
        payment_ids=["P1"],
        dry_run=True,
    )

    assert out["processed"] == 1
    assert out["imported"] == 1
    svc.tenant_pay_repo.insert_payment_receive_from_qbo.assert_not_called()


def test_import_payments_by_ids_success(monkeypatch):
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *a, **k: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda env: "https://sandbox-qbo")
    svc.payment_map_repo.get_by_external_payment_id.return_value = None

    pay = sample_payment()
    monkeypatch.setattr(svc, "_get_qbo_payment_by_id", lambda **kwargs: pay)
    svc.customer_repo.get_customer_uuid_by_external_customer_id.return_value = "CUST-UUID"
    svc.tenant_pay_repo.insert_payment_receive_from_qbo.return_value = 777
    svc.customer_repo.get_customer_id_by_uuid.return_value = 12

    out = svc.import_payments_by_ids(
        tenant_id="tn1",
        service_id="6",
        payment_ids=["P1"],
        dry_run=False,
    )

    assert out["processed"] == 1
    assert out["imported"] == 1
    svc.tenant_pay_repo.insert_payment_receive_from_qbo.assert_called_once()
    svc.payment_map_repo.insert_mapping.assert_called_once()

    kwargs = svc.payment_map_repo.insert_mapping.call_args.kwargs
    assert kwargs["tenant_id"] == "tn1"
    assert kwargs["customer_id"] == 12
    assert kwargs["external_payment_id"] == "P1"
    assert kwargs["sync_status"] == "IMPORTED"


# ---------------------------------------------------------
# helper methods
# ---------------------------------------------------------
def test_is_deleted_customer_ref():
    assert QuickBooksPaymentImportService._is_deleted_customer_ref(
        {"name": "John (deleted)"}
    ) is True

    assert QuickBooksPaymentImportService._is_deleted_customer_ref(
        {"name": "John"}
    ) is False

    assert QuickBooksPaymentImportService._is_deleted_customer_ref(None) is False


def test_extract_first_linked_invoice_id():
    svc = make_service()

    payment = {
        "Line": [
            {"LinkedTxn": [{"TxnType": "Estimate", "TxnId": "E1"}]},
            {"LinkedTxn": [{"TxnType": "Invoice", "TxnId": "INV-22"}]},
        ]
    }

    assert svc._extract_first_linked_invoice_id(payment) == "INV-22"


def test_transaction_id_returns_numeric_string():
    svc = make_service()
    tid = svc.transaction_id()
    assert isinstance(tid, str)
    assert tid.isdigit()


def test_get_payment_method_name_from_inline_name():
    svc = make_service()

    payment = {"PaymentMethodRef": {"name": "Cash"}}
    name = svc.get_payment_method_name(
        payment,
        base_url="https://x",
        realm_id="RID",
        access_token="AT",
    )

    assert name == "Cash"


def test_get_payment_method_name_from_api(monkeypatch):
    svc = make_service()

    payment = {"PaymentMethodRef": {"value": "10"}}

    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **k: FakeResp(
            200,
            json_data={"PaymentMethod": {"Name": "Card"}},
            text='{"PaymentMethod":{"Name":"Card"}}',
        ),
    )

    name = svc.get_payment_method_name(
        payment,
        base_url="https://x",
        realm_id="RID",
        access_token="AT",
    )

    assert name == "Card"