from unittest.mock import MagicMock

from source.quickbooks.QuickBooksVendorExportService import QuickBooksVendorExportService
from source.Utils import constants as const


def make_service():
    svc = QuickBooksVendorExportService()
    svc.qbo_repo = MagicMock()
    svc.qbo_service = MagicMock()
    svc.vendor_repo = MagicMock()
    svc.vendor_map_repo = MagicMock()
    svc.vendor_repo.STATUS_PENDING_PUSH = "PENDING_PUSH"
    svc.vendor_repo.STATUS_FAILED = "FAILED"
    svc.vendor_repo.STATUS_SYNCED = "SYNCED"
    return svc


def connected_info(status="connected"):
    return {
        const.KEY_REFRESH_TOKEN: "RT",
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
        const.KEY_STATUS: status,
    }


def config_ok():
    return {const.KEY_CLIENT_ID: "CID", const.KEY_CLIENT_SECRET: "SEC"}


def sample_vendor():
    return {"id": 10, "display_name": "ABC Vendor", "company_name": "ABC Vendor", "email": "vendor@test.com"}


def sample_candidate(mapping=None):
    return {"vendor": sample_vendor(), "mapping": mapping or {"id": 1, "sync_status": "PENDING_PUSH", "external_vendor_id": None}}


def test_export_vendors_missing_connection():
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {}
    result = svc.export_vendors(tenant_id="t1", service_id="6")
    assert result["ok"] is False
    assert "not connected" in result["message"].lower()


def test_export_vendors_missing_client_credentials():
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = {}
    result = svc.export_vendors(tenant_id="t1", service_id="6")
    assert result["ok"] is False
    assert "client credentials missing" in result["message"].lower()


def test_skip_already_mapped_vendor():
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()
    svc.refresh_access_token = MagicMock(return_value="TOKEN")
    svc.get_qbo_api_base = MagicMock(return_value="https://sandbox-qbo")
    svc.vendor_repo.get_export_candidates.return_value = [sample_candidate({"id": 1, "sync_status": "SYNCED", "external_vendor_id": "QBO-1"})]

    result = svc.export_vendors(tenant_id="t1", service_id="6")

    assert result["skippedAlreadyMapped"] == 1
    svc.vendor_map_repo.mark_mapping_synced.assert_not_called()


def test_map_existing_vendor_from_qbo(monkeypatch):
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()
    svc.refresh_access_token = MagicMock(return_value="TOKEN")
    svc.get_qbo_api_base = MagicMock(return_value="https://sandbox-qbo")
    svc.vendor_repo.get_export_candidates.return_value = [sample_candidate()]
    monkeypatch.setattr(svc, "find_vendor_candidates_in_qbo", lambda **kwargs: [{const.QBO_KEY_ID: "QBO-99", const.QBO_KEY_SYNC_TOKEN: "1"}])
    monkeypatch.setattr(svc, "choose_best_matching_vendor", lambda **kwargs: ({const.QBO_KEY_ID: "QBO-99", const.QBO_KEY_SYNC_TOKEN: "1"}, 100))
    svc.vendor_map_repo.get_by_external_id.return_value = None

    result = svc.export_vendors(tenant_id="t1", service_id="6")

    assert result["mapped from QBO"] == 1
    svc.vendor_map_repo.mark_mapping_synced.assert_called_once()


def test_create_vendor_in_qbo(monkeypatch):
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()
    svc.refresh_access_token = MagicMock(return_value="TOKEN")
    svc.get_qbo_api_base = MagicMock(return_value="https://sandbox-qbo")
    svc.vendor_repo.get_export_candidates.return_value = [sample_candidate()]
    monkeypatch.setattr(svc, "find_vendor_candidates_in_qbo", lambda **kwargs: [])
    monkeypatch.setattr(svc, "choose_best_matching_vendor", lambda **kwargs: (None, 0))
    monkeypatch.setattr(svc, "create_vendor_in_qbo", lambda **kwargs: {const.QBO_KEY_ID: "QBO-55", const.QBO_KEY_SYNC_TOKEN: "0"})

    result = svc.export_vendors(tenant_id="t1", service_id="6")

    assert result["createdInQbo"] == 1
    svc.vendor_map_repo.mark_mapping_synced.assert_called_once()


def test_vendor_external_id_conflict(monkeypatch):
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()
    svc.refresh_access_token = MagicMock(return_value="TOKEN")
    svc.get_qbo_api_base = MagicMock(return_value="https://sandbox-qbo")
    svc.vendor_repo.get_export_candidates.return_value = [sample_candidate()]
    monkeypatch.setattr(svc, "find_vendor_candidates_in_qbo", lambda **kwargs: [{const.QBO_KEY_ID: "QBO-123", const.QBO_KEY_SYNC_TOKEN: "1"}])
    monkeypatch.setattr(svc, "choose_best_matching_vendor", lambda **kwargs: ({const.QBO_KEY_ID: "QBO-123", const.QBO_KEY_SYNC_TOKEN: "1"}, 100))
    svc.vendor_map_repo.get_by_external_id.return_value = {const.KEY_VENDOR_ID: 999}

    result = svc.export_vendors(tenant_id="t1", service_id="6")

    assert result["failed"] == 1
    assert result["results"][0]["status"] == "failed"
    assert result["results"][0]["action"] == "error"
    assert "already linked to another FIELDCRAFT vendor" in result["results"][0]["error"]


def test_build_vendor_display_name_uses_vendor_type_and_short_stable_code():
    svc = make_service()
    display_name = svc.build_display_name({
        "display_name": "ABC Vendor",
        "vendor_uid": "91a2bc44-82aa-45d2-8b92-111122223333",
        "primary_mobile_number": "8888888888",
    })

    assert display_name == "ABC Vendor-vendor-91A2BC"
    assert "8888888888" not in display_name
