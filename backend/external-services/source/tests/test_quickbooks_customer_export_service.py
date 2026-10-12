from datetime import datetime, timezone
from unittest.mock import MagicMock, ANY

from source.quickbooks.QuickBooksCustomerExportService import QuickBooksCustomerExportService
from source.Utils import constants as const


def _make_service():
    svc = QuickBooksCustomerExportService()
    svc.qbo_repo = MagicMock()
    svc.customer_repo = MagicMock()
    svc.customer_repo.STATUS_SYNCED = "SYNCED"
    svc.customer_repo.STATUS_PENDING_PUSH = "PENDING_PUSH"
    svc.customer_repo.STATUS_FAILED = "FAILED"
    svc.customer_repo.STATUS_SYNC_FAILED = "SYNC_FAILED"
    svc.qbo_service = MagicMock()
    return svc


def _customer_row(cid=101, email="a@b.com", phone="123", version=1):
    return {
        "id": cid,
        "customer_uuid": "496e6d3a-0e9e-48b1-8b0c-28d7f061fa0e",
        "firstname": "A",
        "lastname": "B",
        const.KEY_EMAIL: email,
        const.KEY_PHONE: phone,
        "version": version,
    }


def _mapping_row(mid=1, customer_id=101, external_customer_id="", status="PENDING_PUSH"):
    return {
        "id": mid,
        "customer_id": customer_id,
        "external_customer_id": external_customer_id,
        "external_account_id": None,
        "sync_status": status,
        "version": 1,
    }



def test_build_display_name_uses_customer_name_and_phone_number():
    svc = _make_service()

    display_name = svc.build_display_name({
        "id": 301,
        "customer_uuid": "496e6d3a-0e9e-48b1-8b0c-28d7f061fa0e",
        "firstname": "Vijay",
        "lastname": "Sham",
        const.KEY_PHONE: "(987) 654-3210",
    })

    assert display_name == "Vijay Sham-9876543210"
    assert "customer" not in display_name
    assert "496e6d3a" not in display_name


def test_build_display_name_falls_back_to_name_when_phone_is_missing():
    svc = _make_service()

    display_name = svc.build_display_name({
        "id": 301,
        "customer_uuid": "496e6d3a-0e9e-48b1-8b0c-28d7f061fa0e",
        "firstname": "Vijay",
        "lastname": "Sham",
    })

    assert display_name == "Vijay Sham"

def test_export_customers_not_connected():
    svc = _make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {const.KEY_ENV: "sandbox"}

    out = svc.export_customers(tenant_id="tn1", service_id="6")

    assert out[const.KEY_OK] is False
    assert "not connected" in out[const.KEY_MESSAGE].lower()


def test_export_customers_missing_client_credentials():
    svc = _make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_REFRESH_TOKEN: "RT",
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {}

    out = svc.export_customers(tenant_id="tn1", service_id="6")

    assert out[const.KEY_OK] is False
    assert "client credentials" in out[const.KEY_MESSAGE].lower()


def test_export_customers_creates_in_qbo(monkeypatch):
    svc = _make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_REFRESH_TOKEN: "RT",
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SEC",
    }
    svc.customer_repo.get_export_candidates.return_value = [
        {"customer": _customer_row(cid=301), "mapping": _mapping_row(mid=11, customer_id=301, external_customer_id="", status="PENDING_PUSH")}
    ]

    monkeypatch.setattr(svc, "refresh_access_token", lambda **_kwargs: "AT")
    monkeypatch.setattr(svc, "get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    monkeypatch.setattr(svc, "find_customer_in_qbo", lambda **_kwargs: None)
    monkeypatch.setattr(svc, "create_customer_in_qbo", lambda **_kwargs: "QBO-777")

    out = svc.export_customers(tenant_id="tn1", service_id="6")

    assert out[const.KEY_OK] is True
    assert out["createdInQbo"] == 1
    svc.customer_repo.mark_mapping_synced.assert_called_once_with(mapping_id=11, external_customer_id="QBO-777", external_account_id="RID", reason=ANY)


def test_export_customers_maps_existing_qbo(monkeypatch):
    svc = _make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_REFRESH_TOKEN: "RT",
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SEC",
    }
    svc.customer_repo.get_export_candidates.return_value = [
        {"customer": _customer_row(cid=201), "mapping": _mapping_row(mid=7, customer_id=201, external_customer_id="", status="PENDING_PUSH")}
    ]

    monkeypatch.setattr(svc, "refresh_access_token", lambda **_kwargs: "AT")
    monkeypatch.setattr(svc, "get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    monkeypatch.setattr(svc, "find_customer_in_qbo", lambda **_kwargs: "QBO-123")
    monkeypatch.setattr(svc, "get_customer_in_qbo", lambda **_kwargs: {const.QBO_KEY_ID: "QBO-123", const.QBO_KEY_SYNC_TOKEN: "0", "Active": True})

    out = svc.export_customers(tenant_id="tn1", service_id="6")

    assert out["mapped from QBO"] == 1
    svc.customer_repo.mark_mapping_synced.assert_called_once_with(mapping_id=7, external_customer_id="QBO-123", external_account_id="RID", reason=ANY)


def test_export_customers_marks_sync_failed_when_qbo_customer_is_inactive(monkeypatch):
    svc = _make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_REFRESH_TOKEN: "RT",
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SEC",
    }
    svc.customer_repo.get_export_candidates.return_value = [
        {
            "customer": _customer_row(cid=402),
            "mapping": _mapping_row(mid=13, customer_id=402, external_customer_id="QBO-402", status="PENDING_PUSH"),
        }
    ]

    monkeypatch.setattr(svc, "refresh_access_token", lambda **_kwargs: "AT")
    monkeypatch.setattr(svc, "get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    monkeypatch.setattr(svc, "get_customer_in_qbo", lambda **_kwargs: {
        const.QBO_KEY_ID: "QBO-402",
        const.QBO_KEY_SYNC_TOKEN: "0",
        const.QBO_KEY_DISPLAY_NAME: "A B (deleted-1)",
        "Active": False,
    })
    update_mock = MagicMock(return_value="QBO-402")
    monkeypatch.setattr(svc, "update_customer_in_qbo", update_mock)

    out = svc.export_customers(tenant_id="tn1", service_id="6")

    assert out["failed"] == 1
    assert out[const.KEY_RESULTS][0]["action"] == "qboCustomerInactive"
    assert "CUSTOMER_QBO_INACTIVE" in out[const.KEY_RESULTS][0]["reason"]
    update_mock.assert_not_called()
    svc.customer_repo.mark_mapping_failed.assert_called_once_with(
        mapping_id=13,
        status="SYNC_FAILED",
        reason=ANY,
    )


def test_export_customers_updates_existing_mapping(monkeypatch):
    svc = _make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_REFRESH_TOKEN: "RT",
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SEC",
    }
    svc.customer_repo.get_export_candidates.return_value = [
        {"customer": _customer_row(cid=401, version=2), "mapping": _mapping_row(mid=12, customer_id=401, external_customer_id="QBO-999", status="PENDING_PUSH")}
    ]

    monkeypatch.setattr(svc, "refresh_access_token", lambda **_kwargs: "AT")
    monkeypatch.setattr(svc, "get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    monkeypatch.setattr(svc, "get_customer_in_qbo", lambda **_kwargs: {const.QBO_KEY_ID: "QBO-999", const.QBO_KEY_SYNC_TOKEN: "0"})
    monkeypatch.setattr(svc, "update_customer_in_qbo", lambda **_kwargs: "QBO-999")

    out = svc.export_customers(tenant_id="tn1", service_id="6")

    assert out["updatedInQbo"] == 1
    svc.customer_repo.mark_mapping_synced.assert_called_once_with(mapping_id=12, external_customer_id="QBO-999", external_account_id="RID", reason=ANY)


def test_export_customers_marks_failure(monkeypatch):
    svc = _make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_REFRESH_TOKEN: "RT",
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SEC",
    }
    svc.customer_repo.get_export_candidates.return_value = [
        {"customer": _customer_row(cid=501), "mapping": _mapping_row(mid=18, customer_id=501, external_customer_id="", status="PENDING_PUSH")}
    ]

    monkeypatch.setattr(svc, "refresh_access_token", lambda **_kwargs: "AT")
    monkeypatch.setattr(svc, "get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    monkeypatch.setattr(svc, "find_customer_in_qbo", lambda **_kwargs: None)

    def boom(**_kwargs):
        raise Exception("QBO create failed")

    monkeypatch.setattr(svc, "create_customer_in_qbo", boom)

    out = svc.export_customers(tenant_id="tn1", service_id="6")

    assert out["failed"] == 1
    svc.customer_repo.mark_mapping_failed.assert_called_once_with(mapping_id=18, reason=ANY)


def test_export_customers_skips_when_qbo_is_newer(monkeypatch):
    svc = _make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_REFRESH_TOKEN: "RT",
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SEC",
    }
    local_updated = datetime(2026, 5, 1, tzinfo=timezone.utc)
    qbo_updated = datetime(2026, 5, 2, tzinfo=timezone.utc)
    svc.customer_repo.get_export_candidates.return_value = [
        {
            "customer": {**_customer_row(cid=601), "updated_at": local_updated},
            "mapping": _mapping_row(mid=19, customer_id=601, external_customer_id="QBO-601", status="PENDING_PUSH"),
        }
    ]
    svc.customer_repo.get_customer_latest_local_timestamp.return_value = local_updated

    monkeypatch.setattr(svc, "refresh_access_token", lambda **_kwargs: "AT")
    monkeypatch.setattr(svc, "get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    monkeypatch.setattr(svc, "get_customer_in_qbo", lambda **_kwargs: {
        const.QBO_KEY_ID: "QBO-601",
        const.QBO_KEY_SYNC_TOKEN: "2",
        "MetaData": {"LastUpdatedTime": qbo_updated.isoformat()},
    })
    update_mock = MagicMock(return_value="QBO-601")
    monkeypatch.setattr(svc, "update_customer_in_qbo", update_mock)

    out = svc.export_customers(tenant_id="tn1", service_id="6")

    assert out["skippedQboNewer"] == 1
    assert out[const.KEY_RESULTS][0]["action"] == "skippedQboNewer"
    update_mock.assert_not_called()
    svc.customer_repo.mark_mapping_synced.assert_called_once_with(mapping_id=19, external_customer_id="QBO-601", external_account_id="RID", reason=ANY)


def test_personal_additional_info_lookup_stringifies_uuid_user_id(monkeypatch):
    import uuid
    from source.dal.CustomerRepository import CustomerRepository

    customer_uuid = uuid.UUID("9cb801a8-6d71-4b36-a526-97c3f9d89322")
    captured = {}

    class FakeResult:
        def mappings(self):
            return self

        def first(self):
            return {
                "id": 1,
                "user_id": str(customer_uuid),
                "address": "123 Main St",
                "city": "Dallas",
                "state": "TX",
                "country": "US",
                "zip_code": "75001",
                "email": None,
                "first_name": None,
                "lastname": None,
                "updated_at": None,
            }

    class FakeSession:
        def execute(self, statement, params):
            captured["sql"] = str(statement)
            captured["params"] = params
            return FakeResult()

    class FakeTenantSessionScope:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_tenant_session_scope(tenant_id):
        captured["tenant_id"] = tenant_id
        return FakeTenantSessionScope()

    monkeypatch.setattr("source.dal.db_session.tenant_session_scope", fake_tenant_session_scope)

    result = CustomerRepository().get_personal_additional_info("tn1", customer_uuid)

    assert captured["tenant_id"] == "tn1"
    assert captured["params"]["user_id"] == str(customer_uuid)
    assert "CAST(user_id AS TEXT) = :user_id" in captured["sql"]
    assert result["user_id"] == str(customer_uuid)
