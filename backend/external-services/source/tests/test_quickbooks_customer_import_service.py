import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, ANY

from source.quickbooks.QuickBooksCustomerImportService import QuickBooksCustomerImportService
from source.Utils import constants as const


def _make_service():
    svc = QuickBooksCustomerImportService()
    svc.qbo_repo = MagicMock()
    svc.customer_repo = MagicMock()
    svc.customer_repo.STATUS_SYNCED = "SYNCED"
    svc.customer_repo.STATUS_PENDING_PUSH = "PENDING_PUSH"
    svc.customer_repo.STATUS_FAILED = "FAILED"
    svc.customer_repo.STATUS_IMPORT_FAILED = "IMPORT_FAILED"
    svc.customer_repo.STATUS_SYNC_FAILED = "SYNC_FAILED"
    svc.customer_repo.get_open_import_failed_external_customer_ids.return_value = []
    svc.customer_repo.is_import_failure_anchor_customer.return_value = False
    svc.customer_repo.get_customer_id_by_email.return_value = None
    svc.customer_repo.get_current_mapping_by_customer_id.return_value = None
    svc.qbo_service = MagicMock()
    svc.tenant_client = MagicMock()
    svc.tenant_client.customer_email_exists_in_fieldcraft_or_ad.return_value = False
    svc.scheduled_job_repo = MagicMock()
    return svc


def _qbo_customer(qbo_id="Q1", display_name="John Doe", email="john@test.com", phone="1234567890", active=True):
    return {
        const.QBO_KEY_ID: qbo_id,
        const.QBO_KEY_DISPLAY_NAME: display_name,
        "GivenName": "John",
        "FamilyName": "Doe",
        "PrimaryEmailAddr": {"Address": email},
        "PrimaryPhone": {"FreeFormNumber": phone},
        "Active": active,
    }


def _connected_service(svc):
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
        const.KEY_REFRESH_TOKEN: "RT",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SEC",
    }


def test_import_customers_not_connected():
    svc = _make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_REALM_ID: None,
        const.KEY_ENV: "sandbox",
        const.KEY_REFRESH_TOKEN: None,
    }

    out = svc.import_customers(tenant_id="tn1", service_id="6")

    assert out[const.KEY_OK] is False
    assert "missing realmid/refreshtoken" in out[const.KEY_ERROR].lower()


def test_import_customers_mapping_first_noop(monkeypatch):
    svc = _make_service()
    _connected_service(svc)

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *_args: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    svc._fetch_qbo_customers = MagicMock(side_effect=[([_qbo_customer()], None), ([], None)])
    svc.scheduled_job_repo.get_last_successful_module_sync_start.return_value = None

    svc.customer_repo.get_current_mapping_by_external_customer_id.return_value = {"id": 1, "customer_id": 777}
    svc.customer_repo.get_current_customer_by_id.return_value = {
        "id": 777,
        "first_name": "John",
        "last_name": "Doe",
        const.KEY_EMAIL: "john@test.com",
        const.KEY_PHONE: "1234567890",
    }
    svc.customer_repo.sync_projection_changed.return_value = False

    out = svc.import_customers(tenant_id="tn1", service_id="6")

    assert out["skippedAlreadyCurrent"] == 1
    svc.customer_repo.mark_mapping_synced.assert_called_once_with(1, "Q1", "RID", reason=ANY)


def test_import_customers_skips_qbo_sub_customer(monkeypatch):
    svc = _make_service()
    _connected_service(svc)

    qbo_project = _qbo_customer(qbo_id="Q-PROJECT", display_name="Acme: Roof Replacement")
    qbo_project["Job"] = True
    qbo_project["ParentRef"] = {"value": "Q-PARENT", "name": "Acme"}

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *_args: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    svc._fetch_qbo_customers = MagicMock(side_effect=[([qbo_project], None), ([], None)])
    svc.scheduled_job_repo.get_last_successful_module_sync_start.return_value = None

    out = svc.import_customers(tenant_id="tn1", service_id="6")

    assert out["skippedSubCustomers"] == 1
    assert out[const.KEY_RESULTS][0][const.KEY_ACTION] == "subCustomerSkipped"
    reason = json.loads(out[const.KEY_RESULTS][0][const.KEY_REASON])
    assert reason["code"] == svc.REASON_SUB_CUSTOMER_SKIPPED
    assert reason["parent_qbo_customer_id"] == "Q-PARENT"
    svc.customer_repo.get_current_mapping_by_external_customer_id.assert_not_called()
    svc.customer_repo.get_customer_id_by_email.assert_not_called()
    svc.tenant_client.create_user.assert_not_called()
    svc.customer_repo.create_external_mapping.assert_not_called()


def test_import_customers_marks_sync_failed_when_mapped_qbo_customer_is_inactive(monkeypatch):
    svc = _make_service()
    _connected_service(svc)

    inactive_qbo_customer = _qbo_customer(
        qbo_id="Q-DELETED",
        display_name="John Doe (deleted-1)",
        email="john@test.com",
        active=False,
    )

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *_args: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    svc._fetch_qbo_customers = MagicMock(side_effect=[([inactive_qbo_customer], None), ([], None)])
    svc.scheduled_job_repo.get_last_successful_module_sync_start.return_value = None
    svc.customer_repo.get_current_mapping_by_external_customer_id.return_value = {
        "id": 10,
        "customer_id": 777,
        "sync_status": "SYNCED",
    }

    out = svc.import_customers(tenant_id="tn1", service_id="6")

    assert out["failed"] == 1
    assert out["skippedDeleted"] == 0
    assert out[const.KEY_FAILURES][0][const.KEY_ACTION] == "inactiveInQbo"
    reason = json.loads(out[const.KEY_FAILURES][0][const.KEY_REASON])
    assert reason["code"] == "CUSTOMER_INACTIVE_IN_QBO"
    assert "made inactive in QuickBooks" in reason["message"]
    svc.customer_repo.mark_mapping_failed.assert_called_once_with(
        10,
        status="SYNC_FAILED",
        reason=out[const.KEY_FAILURES][0][const.KEY_REASON],
    )
    svc.customer_repo.get_customer_id_by_email.assert_not_called()
    svc.tenant_client.create_user.assert_not_called()


def test_import_customers_blocks_mapped_customer_email_change(monkeypatch):
    svc = _make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
        const.KEY_REFRESH_TOKEN: "RT",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SEC",
    }

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *_args: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    svc._fetch_qbo_customers = MagicMock(side_effect=[([_qbo_customer(email="new@test.com")], None), ([], None)])
    svc.scheduled_job_repo.get_last_successful_module_sync_start.return_value = None

    svc.customer_repo.get_current_mapping_by_external_customer_id.return_value = {"id": 1, "customer_id": 777}
    svc.customer_repo.get_current_customer_by_id.return_value = {
        "id": 777,
        "first_name": "John",
        "last_name": "Doe",
        const.KEY_EMAIL: "old@test.com",
        const.KEY_PHONE: "1234567890",
    }

    out = svc.import_customers(tenant_id="tn1", service_id="6")

    assert out["failed"] == 1
    assert out[const.KEY_RESULTS][0][const.KEY_ACTION] == "emailChangeNotAllowed"
    reason = json.loads(out[const.KEY_RESULTS][0][const.KEY_REASON])
    assert reason["code"] == svc.REASON_EMAIL_CHANGE_NOT_ALLOWED
    assert "QuickBooks customer" not in reason["message"]
    assert "FIELDCRAFT currently has old@test.com, while QuickBooks has new@test.com" in reason["message"]
    svc.customer_repo.upsert_import_failed_mapping.assert_called_once()
    assert svc.customer_repo.upsert_import_failed_mapping.call_args.kwargs["sync_status"] == svc.customer_repo.STATUS_SYNC_FAILED
    svc.customer_repo.create_customer_version_from_qbo.assert_not_called()


def test_import_customers_updates_existing_mapping(monkeypatch):
    svc = _make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
        const.KEY_REFRESH_TOKEN: "RT",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SEC",
    }

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *_args: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    svc._fetch_qbo_customers = MagicMock(side_effect=[([_qbo_customer(phone="9999999999")], None), ([], None)])
    svc.scheduled_job_repo.get_last_successful_module_sync_start.return_value = None

    svc.customer_repo.get_current_mapping_by_external_customer_id.return_value = {"id": 1, "customer_id": 777}
    svc.customer_repo.get_current_customer_by_id.return_value = {
        "id": 777,
        "first_name": "John",
        "last_name": "Doe",
        const.KEY_EMAIL: "john@test.com",
        const.KEY_PHONE: "1234567890",
    }
    svc.customer_repo.sync_projection_changed.return_value = True
    svc.customer_repo.create_customer_version_from_qbo.return_value = {"id": 778}

    out = svc.import_customers(tenant_id="tn1", service_id="6")

    assert out["updatedInFIELDCRAFT"] == 1
    svc.customer_repo.rotate_mapping_for_customer.assert_called_once_with(
        tenant_id="tn1", service_id="6", customer_id=778, external_customer_id="Q1", external_account_id="RID", sync_status=svc.customer_repo.STATUS_SYNCED, reason=ANY
    )


def test_import_customers_blocks_duplicate_existing_email(monkeypatch):
    svc = _make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
        const.KEY_REFRESH_TOKEN: "RT",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SEC",
    }

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *_args: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    svc._fetch_qbo_customers = MagicMock(side_effect=[([_qbo_customer(email="exist@test.com")], None), ([], None)])
    svc.scheduled_job_repo.get_last_successful_module_sync_start.return_value = None

    svc.customer_repo.get_current_mapping_by_external_customer_id.return_value = None
    svc.customer_repo.get_customer_id_by_email_any_tenant.return_value = 555
    svc.tenant_client.customer_email_exists_in_fieldcraft_or_ad.return_value = True

    out = svc.import_customers(tenant_id="tn1", service_id="6")

    assert out["failed"] == 1
    assert out[const.KEY_RESULTS][0][const.KEY_ACTION] == "duplicateEmailImportFailed"
    assert out[const.KEY_RESULTS][0][const.KEY_FIELDCRAFT_CUSTOMER_ID] is None
    assert out[const.KEY_RESULTS][0]["existingFieldCraftCustomerId"] == 555
    svc.customer_repo.upsert_import_failed_mapping.assert_called_once()
    call = svc.customer_repo.upsert_import_failed_mapping.call_args.kwargs
    assert call["customer_id"] is None
    assert call["detach_customer"] is True
    assert call["sync_status"] is None
    assert call["external_customer_id"] == "Q1"
    svc.customer_repo.rotate_mapping_for_customer.assert_not_called()


def test_import_customers_recovers_completely_missing_mapping_from_same_tenant_email(monkeypatch):
    svc = _make_service()
    _connected_service(svc)

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *_args: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    svc._fetch_qbo_customers = MagicMock(
        side_effect=[([_qbo_customer(qbo_id="100000021", email="emma@watsonx.com")], None), ([], None)]
    )
    svc.scheduled_job_repo.get_last_successful_module_sync_start.return_value = None

    svc.customer_repo.get_current_mapping_by_external_customer_id.return_value = None
    svc.customer_repo.get_customer_id_by_email.return_value = 12716
    svc.customer_repo.get_current_mapping_by_customer_id.return_value = None
    svc.customer_repo.claim_external_customer_mapping.return_value = {
        "id": 1494,
        "customer_id": 12716,
        "customer_uuid": "emma-uuid",
        "external_customer_id": "100000021",
        "external_account_id": "RID",
        "sync_status": "SYNCED",
    }
    svc.customer_repo.get_current_customer_by_id.return_value = {
        "id": 12716,
        "first_name": "John",
        "last_name": "Doe",
        const.KEY_EMAIL: "emma@watsonx.com",
        const.KEY_PHONE: "1234567890",
    }
    svc.customer_repo.sync_projection_changed.return_value = False

    out = svc.import_customers(tenant_id="tn1", service_id="6", tenant_api_token="TOKEN")

    assert out["linkedExistingInFieldCraft"] == 1
    assert out["skippedAlreadyCurrent"] == 1
    assert out["failed"] == 0
    claim = svc.customer_repo.claim_external_customer_mapping.call_args.kwargs
    assert claim["customer_id"] == 12716
    assert claim["external_customer_id"] == "100000021"
    assert claim["mapping_id"] is None
    assert claim["allow_create"] is True
    recovery_reason = json.loads(claim["reason"])
    assert recovery_reason["code"] == "CUSTOMER_MISSING_MAPPING_RECOVERED"
    assert recovery_reason["message"] == (
        "This customer was already in FIELDCRAFT, so it was linked to the matching QuickBooks customer."
    )
    svc.tenant_client.customer_email_exists_in_fieldcraft_or_ad.assert_not_called()
    svc.tenant_client.create_user.assert_not_called()


def test_import_customers_repairs_detached_duplicate_email_failure_mapping(monkeypatch):
    svc = _make_service()
    _connected_service(svc)

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *_args: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    svc._fetch_qbo_customers = MagicMock(
        side_effect=[([_qbo_customer(qbo_id="100000021", email="emma@watsonx.com")], None), ([], None)]
    )
    svc.scheduled_job_repo.get_last_successful_module_sync_start.return_value = None

    detached_mapping = {
        "id": 1494,
        "customer_id": None,
        "customer_uuid": None,
        "external_customer_id": "100000021",
        "external_account_id": "RID",
        "sync_status": svc.customer_repo.STATUS_IMPORT_FAILED,
        "reason": json.dumps({"code": svc.REASON_DUPLICATE_EMAIL_NOT_ALLOWED}),
    }
    svc.customer_repo.get_current_mapping_by_external_customer_id.return_value = detached_mapping
    svc.customer_repo.get_customer_id_by_email.return_value = 12716
    svc.customer_repo.get_current_mapping_by_customer_id.return_value = None
    svc.customer_repo.claim_external_customer_mapping.return_value = {
        "id": 1494,
        "customer_id": 12716,
        "customer_uuid": "emma-uuid",
        "external_customer_id": "100000021",
        "external_account_id": "RID",
        "sync_status": "SYNCED",
    }
    svc.customer_repo.get_current_customer_by_id.return_value = {
        "id": 12716,
        "first_name": "John",
        "last_name": "Doe",
        const.KEY_EMAIL: "emma@watsonx.com",
        const.KEY_PHONE: "1234567890",
    }
    svc.customer_repo.sync_projection_changed.return_value = False

    out = svc.import_customers(tenant_id="tn1", service_id="6", tenant_api_token="TOKEN")

    assert out["linkedExistingInFieldCraft"] == 1
    assert out["failed"] == 0
    claim = svc.customer_repo.claim_external_customer_mapping.call_args.kwargs
    assert claim["mapping_id"] == 1494
    assert claim["allow_create"] is False
    svc.customer_repo.upsert_import_failed_mapping.assert_not_called()
    svc.tenant_client.customer_email_exists_in_fieldcraft_or_ad.assert_not_called()


def test_import_customers_blocks_email_found_only_in_fieldcraft_or_ad(monkeypatch):
    svc = _make_service()
    _connected_service(svc)

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *_args: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    svc._fetch_qbo_customers = MagicMock(side_effect=[([_qbo_customer(email="ad-user@test.com")], None), ([], None)])
    svc.scheduled_job_repo.get_last_successful_module_sync_start.return_value = None

    svc.customer_repo.get_current_mapping_by_external_customer_id.return_value = None
    svc.customer_repo.get_customer_id_by_email_any_tenant.return_value = None
    svc.tenant_client.customer_email_exists_in_fieldcraft_or_ad.return_value = True

    out = svc.import_customers(tenant_id="tn1", service_id="6", tenant_api_token="TOKEN")

    assert out["failed"] == 1
    assert out[const.KEY_RESULTS][0][const.KEY_ACTION] == "duplicateEmailImportFailed"
    assert out[const.KEY_RESULTS][0]["emailExistsInFieldCraftOrAd"] is True
    svc.tenant_client.customer_email_exists_in_fieldcraft_or_ad.assert_called_once_with("TOKEN", "tn1", "ad-user@test.com")
    svc.tenant_client.create_user.assert_not_called()


def test_import_customers_repairs_duplicate_failed_mapping_without_customer_link(monkeypatch):
    svc = _make_service()
    _connected_service(svc)

    reason = json.dumps({"code": svc.REASON_DUPLICATE_EMAIL_NOT_ALLOWED})
    existing_bad_mapping = {
        "id": 451,
        "customer_id": 6406,
        "sync_status": svc.customer_repo.STATUS_SYNC_FAILED,
        "reason": reason,
    }

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *_args: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    svc._fetch_qbo_customers = MagicMock(side_effect=[([_qbo_customer(qbo_id="70", display_name="gg gg", email="predator@pre.com")], None), ([], None)])
    svc.scheduled_job_repo.get_last_successful_module_sync_start.return_value = None

    svc.customer_repo.get_current_mapping_by_external_customer_id.return_value = existing_bad_mapping
    svc.customer_repo.get_customer_id_by_email_any_tenant.return_value = 6406
    svc.tenant_client.customer_email_exists_in_fieldcraft_or_ad.return_value = True

    out = svc.import_customers(tenant_id="tn24047750", service_id="6")

    assert out["failed"] == 1
    assert out[const.KEY_RESULTS][0][const.KEY_ACTION] == "duplicateEmailImportFailed"
    assert out[const.KEY_RESULTS][0][const.KEY_FIELDCRAFT_CUSTOMER_ID] is None
    assert out[const.KEY_RESULTS][0]["existingFieldCraftCustomerId"] == 6406

    svc.customer_repo.upsert_import_failed_mapping.assert_called_once()
    call = svc.customer_repo.upsert_import_failed_mapping.call_args.kwargs
    assert call["external_customer_id"] == "70"
    assert call["customer_id"] is None
    assert call["detach_customer"] is True
    assert call["sync_status"] is None
    svc.customer_repo.create_customer_version_from_qbo.assert_not_called()
    svc.customer_repo.create_external_mapping.assert_not_called()
    svc.tenant_client.create_user.assert_not_called()


def test_import_customers_creates_in_fieldcraft(monkeypatch):
    svc = _make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
        const.KEY_REFRESH_TOKEN: "RT",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SEC",
    }

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *_args: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    svc._fetch_qbo_customers = MagicMock(side_effect=[([_qbo_customer()], None), ([], None)])
    svc.scheduled_job_repo.get_last_successful_module_sync_start.return_value = None

    class FakeTenantRepo:
        def __init__(self, tenant_id):
            self.tenant_id = tenant_id
        def get_user_by_id(self, user_id):
            return {"id": user_id, "objectId": "OBJ-123", "firstname": "John", "lastname": "Doe", const.KEY_EMAIL: "john@test.com", "mobileNo": "1234567890"}
        def get_user_by_email(self, email):
            return None

    monkeypatch.setattr("source.quickbooks.QuickBooksCustomerImportService.TenantUserAccountRepository", FakeTenantRepo)
    svc.tenant_client.create_user.return_value = 77
    svc.customer_repo.get_current_mapping_by_external_customer_id.return_value = None
    svc.customer_repo.get_customer_id_by_email.side_effect = [None, 123]

    out = svc.import_customers(tenant_id="tn1", service_id="6", tenant_api_token="TOKEN")

    assert out["created in FIELDCRAFT"] == 1
    svc.tenant_client.create_user.assert_called_once()
    svc.customer_repo.create_external_mapping.assert_called_once()


def test_import_customers_by_ids_fetches_targeted(monkeypatch):
    svc = _make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
        const.KEY_REFRESH_TOKEN: "RT",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SEC",
    }

    class FakeTenantRepo:
        def __init__(self, tenant_id):
            self.tenant_id = tenant_id
        def get_user_by_id(self, user_id):
            return {"id": user_id, "objectId": "OBJ-321", "firstname": "John", "lastname": "Doe", const.KEY_EMAIL: "john@test.com", "mobileNo": "1234567890"}
        def get_user_by_email(self, email):
            return None

    monkeypatch.setattr("source.quickbooks.QuickBooksCustomerImportService.TenantUserAccountRepository", FakeTenantRepo)
    monkeypatch.setattr(svc, "_refresh_access_token", lambda *_args: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    monkeypatch.setattr(svc, "_fetch_qbo_customer_by_id", lambda *_args: _qbo_customer(qbo_id="Q9"))

    svc.tenant_client.create_user.return_value = 88
    svc.customer_repo.get_current_mapping_by_external_customer_id.return_value = None
    svc.customer_repo.get_customer_id_by_email.side_effect = [None, 321]

    out = svc.import_customers_by_ids(tenant_id="tn1", service_id="6", customer_ids=["Q9"])

    assert out[const.KEY_OK] is True
    assert out["processed"] == 1
    svc.tenant_client.create_user.assert_called_once()
    svc.customer_repo.create_external_mapping.assert_called_once()


def test_import_customers_uses_last_successful_module_sync_start_for_cdc(monkeypatch):
    svc = _make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
        const.KEY_REFRESH_TOKEN: "RT",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SEC",
    }

    watermark = datetime.now(timezone.utc)
    svc.scheduled_job_repo.get_last_successful_module_sync_start.return_value = watermark

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *_args: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    captured = {}
    def fake_cdc(**kwargs):
        captured.update(kwargs)
        return ([_qbo_customer()], "tid-1")
    monkeypatch.setattr(svc, "_fetch_qbo_customers_cdc", fake_cdc)
    svc.customer_repo.get_current_mapping_by_external_customer_id.return_value = {"id": 1, "customer_id": 777}
    svc.customer_repo.get_current_customer_by_id.return_value = {
        "id": 777,
        "first_name": "John",
        "last_name": "Doe",
        const.KEY_EMAIL: "john@test.com",
        const.KEY_PHONE: "1234567890",
    }
    svc.customer_repo.sync_projection_changed.return_value = False

    out = svc.import_customers(tenant_id="tn1", service_id="6")

    assert out["mode"] == "CDC"
    assert out["watermarkSource"] == "lastSuccessfulCustomerModuleSyncStart"
    assert captured["changed_since"] == watermark - timedelta(minutes=1)
    svc.scheduled_job_repo.get_last_successful_module_sync_start.assert_called_once_with(tenant_id="tn1", job_type="QBO_CUSTOMER_SYNC", service_id="6")


def test_import_failure_persists_import_failed_mapping(monkeypatch):
    svc = _make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
        const.KEY_REFRESH_TOKEN: "RT",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SEC",
    }

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *_args: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    svc._fetch_qbo_customers = MagicMock(side_effect=[([_qbo_customer(qbo_id="Q437")], None), ([], None)])
    svc.scheduled_job_repo.get_last_successful_module_sync_start.return_value = None

    svc.customer_repo.get_current_mapping_by_external_customer_id.return_value = None
    svc.customer_repo.get_customer_id_by_email.side_effect = [None, None, None]
    svc.tenant_client.create_user.side_effect = Exception("Tenant API createUser failed: 401")

    class FakeTenantRepo:
        def __init__(self, tenant_id):
            self.tenant_id = tenant_id
        def get_user_by_email(self, email):
            return None

    monkeypatch.setattr("source.quickbooks.QuickBooksCustomerImportService.TenantUserAccountRepository", FakeTenantRepo)

    out = svc.import_customers(tenant_id="tn1", service_id="6", tenant_api_token="TOKEN")

    assert out["failed"] == 1
    svc.customer_repo.upsert_import_failed_mapping.assert_called_once()


def test_import_customers_force_full_ignores_existing_watermark(monkeypatch):
    svc = _make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
        const.KEY_REFRESH_TOKEN: "RT",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SEC",
    }

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *_args: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    svc.scheduled_job_repo.get_last_successful_module_sync_start.return_value = datetime.now(timezone.utc)
    svc._fetch_qbo_customers = MagicMock(return_value=([], None))
    svc._fetch_qbo_customers_cdc = MagicMock(return_value=([_qbo_customer()], None))

    out = svc.import_customers(tenant_id="tn1", service_id="6", force_full=True)

    assert out["mode"] == "FULL"
    assert out["changedSince"] is None
    assert out["watermarkSource"] == "forceFull"
    svc._fetch_qbo_customers.assert_called_once()
    svc._fetch_qbo_customers_cdc.assert_not_called()


def test_import_customers_skips_address_when_local_pai_is_newer(monkeypatch):
    svc = _make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
        const.KEY_REFRESH_TOKEN: "RT",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SEC",
    }
    qbo_updated = datetime(2026, 5, 1, tzinfo=timezone.utc)
    local_updated = datetime(2026, 5, 2, tzinfo=timezone.utc)
    qbo_customer = _qbo_customer()
    qbo_customer["MetaData"] = {"LastUpdatedTime": qbo_updated.isoformat()}
    qbo_customer["BillAddr"] = {"Line1": "Old QBO Address", "City": "Dallas", "CountrySubDivisionCode": "TX", "PostalCode": "75001"}

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *_args: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    svc._fetch_qbo_customers = MagicMock(side_effect=[([qbo_customer], None), ([], None)])
    svc.scheduled_job_repo.get_last_successful_module_sync_start.return_value = None

    svc.customer_repo.get_current_mapping_by_external_customer_id.return_value = {"id": 1, "customer_id": 777}
    svc.customer_repo.get_current_customer_by_id.return_value = {
        "id": 777,
        "customer_uuid": "cust-uuid",
        "first_name": "John",
        "last_name": "Doe",
        const.KEY_EMAIL: "john@test.com",
        const.KEY_PHONE: "1234567890",
    }
    svc.customer_repo.get_personal_additional_info.return_value = {
        "user_id": "cust-uuid",
        "address": "New FIELDCRAFT Address",
        "city": "Dallas",
        "state": "TX",
        "zip_code": "75001",
        "updated_at": local_updated,
    }
    svc.customer_repo.get_customer_latest_local_timestamp.return_value = local_updated
    svc.customer_repo.sync_projection_changed.return_value = False

    out = svc.import_customers(tenant_id="tn1", service_id="6")

    assert out["skippedLocalNewer"] == 1
    assert out[const.KEY_RESULTS][0][const.KEY_ACTION] == "localNewer"
    svc.customer_repo.upsert_personal_additional_info.assert_not_called()
    svc.customer_repo.mark_mapping_synced.assert_not_called()


def test_recover_missing_customer_mapping_skips_already_bound_mapping():
    svc = _make_service()
    current_mapping = {
        "id": 10,
        "customer_id": 777,
        "external_customer_id": "Q1",
        "sync_status": svc.customer_repo.STATUS_SYNCED,
    }

    recovered, changed = svc._recover_missing_customer_mapping(
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_customer_id="Q1",
        display_name="John Doe",
        email="john@test.com",
        current_mapping=current_mapping,
    )

    assert recovered is current_mapping
    assert changed is False
    svc.customer_repo.get_customer_id_by_email.assert_not_called()
    svc.customer_repo.claim_external_customer_mapping.assert_not_called()


def test_recover_missing_customer_mapping_skips_non_duplicate_failure_reason():
    svc = _make_service()
    current_mapping = {
        "id": 10,
        "customer_id": None,
        "external_customer_id": "Q1",
        "sync_status": svc.customer_repo.STATUS_IMPORT_FAILED,
        "reason": json.dumps({"code": svc.REASON_MISSING_REQUIRED_FIELDS}),
    }

    recovered, changed = svc._recover_missing_customer_mapping(
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_customer_id="Q1",
        display_name="John Doe",
        email="john@test.com",
        current_mapping=current_mapping,
    )

    assert recovered is current_mapping
    assert changed is False
    svc.customer_repo.get_customer_id_by_email.assert_not_called()
    svc.customer_repo.claim_external_customer_mapping.assert_not_called()


def test_recover_missing_customer_mapping_skips_empty_email():
    svc = _make_service()

    recovered, changed = svc._recover_missing_customer_mapping(
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_customer_id="Q1",
        display_name="John Doe",
        email="   ",
        current_mapping=None,
    )

    assert recovered is None
    assert changed is False
    svc.customer_repo.get_customer_id_by_email.assert_not_called()
    svc.customer_repo.claim_external_customer_mapping.assert_not_called()


def test_recover_missing_customer_mapping_claims_detached_failure_on_exact_email():
    svc = _make_service()
    detached_mapping = {
        "id": 1494,
        "customer_id": None,
        "customer_uuid": None,
        "external_customer_id": "100000021",
        "external_account_id": "RID",
        "sync_status": svc.customer_repo.STATUS_IMPORT_FAILED,
        "reason": json.dumps({"code": svc.REASON_DUPLICATE_EMAIL_NOT_ALLOWED}),
    }
    svc.customer_repo.get_customer_id_by_email.return_value = 12716
    svc.customer_repo.get_current_mapping_by_customer_id.return_value = None
    svc.customer_repo.claim_external_customer_mapping.return_value = {
        "id": 1494,
        "customer_id": 12716,
        "customer_uuid": "emma-uuid",
        "external_customer_id": "100000021",
        "external_account_id": "RID",
        "sync_status": "SYNCED",
    }

    recovered, changed = svc._recover_missing_customer_mapping(
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_customer_id="100000021",
        display_name="Current Customer Name",
        email="stable.customer@example.com",
        current_mapping=detached_mapping,
    )

    assert changed is True
    assert recovered["customer_id"] == 12716
    claim = svc.customer_repo.claim_external_customer_mapping.call_args.kwargs
    assert claim["mapping_id"] == 1494
    assert claim["allow_create"] is False


def test_import_customers_recovers_by_unique_email_when_name_and_phone_changed(monkeypatch):
    svc = _make_service()
    _connected_service(svc)

    qbo_customer = _qbo_customer(
        qbo_id="Q-HISTORY",
        display_name="Current Customer Name",
        email="stable.customer@example.com",
        phone="9998887777",
    )
    qbo_customer["GivenName"] = "Current"
    qbo_customer["FamilyName"] = "Name"

    monkeypatch.setattr(svc, "_refresh_access_token", lambda *_args: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda _env: "https://sandbox-qbo")
    svc._fetch_qbo_customers = MagicMock(side_effect=[([qbo_customer], None), ([], None)])
    svc.scheduled_job_repo.get_last_successful_module_sync_start.return_value = None

    svc.customer_repo.get_current_mapping_by_external_customer_id.return_value = None
    svc.customer_repo.get_customer_id_by_email.return_value = 12716
    svc.customer_repo.get_current_mapping_by_customer_id.return_value = None
    svc.customer_repo.claim_external_customer_mapping.return_value = {
        "id": 2000,
        "customer_id": 12716,
        "customer_uuid": "customer-uuid-history",
        "external_customer_id": "Q-HISTORY",
        "external_account_id": "RID",
        "sync_status": "SYNCED",
    }
    svc.customer_repo.get_current_customer_by_id.return_value = {
        "id": 12716,
        "first_name": "Historical",
        "last_name": "Customer",
        const.KEY_EMAIL: "stable.customer@example.com",
        const.KEY_PHONE: "1112223333",
    }
    svc.customer_repo.sync_projection_changed.return_value = False

    out = svc.import_customers(
        tenant_id="tn1",
        service_id="6",
        tenant_api_token="TOKEN",
    )

    assert out["linkedExistingInFieldCraft"] == 1
    assert out["failed"] == 0
    claim = svc.customer_repo.claim_external_customer_mapping.call_args.kwargs
    assert claim["customer_id"] == 12716
    assert claim["external_customer_id"] == "Q-HISTORY"
    assert claim["allow_create"] is True
    svc.tenant_client.customer_email_exists_in_fieldcraft_or_ad.assert_not_called()
    svc.tenant_client.create_user.assert_not_called()


def test_recover_missing_customer_mapping_does_not_reassign_unique_email_from_other_qbo_customer():
    svc = _make_service()
    svc.customer_repo.get_customer_id_by_email.return_value = 12716
    svc.customer_repo.get_current_mapping_by_customer_id.return_value = {
        "id": 2000,
        "customer_id": 12716,
        "external_customer_id": "Q-EXISTING",
        "external_account_id": "RID",
        "sync_status": svc.customer_repo.STATUS_SYNCED,
    }

    recovered, changed = svc._recover_missing_customer_mapping(
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_customer_id="Q-OTHER",
        display_name="Another QuickBooks Customer",
        email="stable.customer@example.com",
        current_mapping=None,
    )

    assert recovered is None
    assert changed is False
    svc.customer_repo.claim_external_customer_mapping.assert_not_called()
