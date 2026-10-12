from unittest.mock import MagicMock, patch

from source.Utils import constants as const
from source.quickbooks.QuickBooksAccountExportService import QuickBooksAccountExportService
from source.quickbooks.QuickBooksBillCreateService import QuickBooksBillCreateService
from source.quickbooks.QuickBooksBillExportService import QuickBooksBillExportService


class _Resp:
    status_code = 200


class GuardedBillExporter:
    def __setattr__(self, name, value):
        if name in {"_tenant_id", "_service_id", "_connection"}:
            raise AssertionError(f"unexpected mutable request state assignment: {name}")
        object.__setattr__(self, name, value)

    def _clean(self, value):
        return str(value or "").strip()

    def refresh_access_token(self, **kwargs):
        self.refresh_kwargs = kwargs
        return "AT"

    def get_qbo_api_base(self, env):
        return "https://sandbox-quickbooks.api.intuit.com"

    def _build_qbo_bill_payload(self, **kwargs):
        return {"DocNumber": "B-1"}


def test_account_export_find_account_in_qbo_escapes_literals():
    svc = QuickBooksAccountExportService()

    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["query"] = params["query"]
        return _Resp()

    with patch("source.quickbooks.QuickBooksAccountExportService.qbo_api.qbo_get", side_effect=fake_get), \
         patch("source.quickbooks.QuickBooksAccountExportService.qbo_api.safe_json", return_value={"QueryResponse": {}}):
        svc.find_account_in_qbo(
            access_token="AT",
            base_url="https://example.com",
            realm_id="R1",
            name="O'Brien\\x",
            acct_num="",
            account_type="Expense",
            detail_type="Office_Expense%",
        )

    assert "Name = 'O\\'Brien\\\\x'" in captured["query"]
    assert "AccountType = 'Expense'" in captured["query"]
    assert "AccountSubType = 'Office_Expense%'" in captured["query"]


def test_account_export_refresh_access_token_uses_explicit_request_context():
    svc = QuickBooksAccountExportService()
    svc.qbo_repo = MagicMock()
    svc.qbo_service = MagicMock()
    svc.qbo_service.token_url = "https://token.example.com"

    with patch("source.quickbooks.QuickBooksAccountExportService.QuickBooksOAuthClient") as oauth_cls:
        oauth = oauth_cls.return_value
        oauth.refresh_access_token.return_value = "AT"

        out = svc.refresh_access_token(
            tenant_id="tn1",
            service_id="6",
            connection={"realmId": "R1"},
            client_id="CID",
            client_secret="SECRET",
            refresh_token="RT",
        )

    assert out == "AT"
    oauth.refresh_access_token.assert_called_once_with(
        tenant_id="tn1",
        service_id="6",
        client_id="CID",
        client_secret="SECRET",
        refresh_token="RT",
        token_url="https://token.example.com",
        current_connection={"realmId": "R1"},
    )


def test_bill_export_refresh_access_token_uses_explicit_request_context():
    svc = QuickBooksBillExportService()
    svc.qbo_repo = MagicMock()
    svc.qbo_service = MagicMock()
    svc.qbo_service.token_url = "https://token.example.com"

    with patch("source.quickbooks.QuickBooksBillExportService.QuickBooksOAuthClient") as oauth_cls:
        oauth = oauth_cls.return_value
        oauth.refresh_access_token.return_value = "AT"

        out = svc.refresh_access_token(
            tenant_id="tn1",
            service_id="6",
            connection={"realmId": "R1"},
            client_id="CID",
            client_secret="SECRET",
            refresh_token="RT",
        )

    assert out == "AT"
    oauth.refresh_access_token.assert_called_once_with(
        tenant_id="tn1",
        service_id="6",
        client_id="CID",
        client_secret="SECRET",
        refresh_token="RT",
        token_url="https://token.example.com",
        current_connection={"realmId": "R1"},
    )


def test_bill_create_does_not_mutate_exporter_request_state():
    svc = QuickBooksBillCreateService()
    svc.qbo_repo = MagicMock()
    svc.qbo_service = MagicMock()
    svc.receipt_repo = MagicMock()
    svc.receipt_map_repo = MagicMock()
    svc.vendor_map_repo = MagicMock()
    svc.bill_exporter = GuardedBillExporter()

    svc.qbo_repo.getServiceIdByName.return_value = "6"
    svc.receipt_map_repo.get_by_mapping_id.return_value = {
        "id": 101,
        "receipt_id": 501,
        "service_id": 6,
        "sync_status": "PENDING_PUSH",
    }
    svc.receipt_repo.STATUS_PENDING_PUSH = "PENDING_PUSH"
    svc.receipt_repo.STATUS_FAILED = "FAILED"
    svc.receipt_repo.get_current_receipt_by_id.return_value = {
        "id": 501,
        "vendor_id": 20,
    }
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_STATUS: const.STATUS_CONNECTED,
        const.KEY_REALM_ID: "R1",
        const.KEY_ENV: "sandbox",
        const.KEY_REFRESH_TOKEN: "RT",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SECRET",
    }
    svc._export_single_bill = MagicMock(return_value={"ok": True, "createdInQbo": 1})

    out = svc.create_bill(
        tenant_id="tn1",
        service_id=None,
        receipt_external_map_id=101,
    )

    assert out["ok"] is True
    assert svc.bill_exporter.refresh_kwargs == {
        "tenant_id": "tn1",
        "service_id": "6",
        "connection": {
            const.KEY_STATUS: const.STATUS_CONNECTED,
            const.KEY_REALM_ID: "R1",
            const.KEY_ENV: "sandbox",
            const.KEY_REFRESH_TOKEN: "RT",
        },
        "client_id": "CID",
        "client_secret": "SECRET",
        "refresh_token": "RT",
    }
