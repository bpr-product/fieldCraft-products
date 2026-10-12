import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

from source.quickbooks.QuickBooksVendorImportService import QuickBooksVendorImportService
from source.Utils import constants as const


def make_service():
    svc = QuickBooksVendorImportService()
    svc.qbo_repo = MagicMock()
    svc.qbo_service = MagicMock()
    svc.vendor_repo = MagicMock()
    svc.vendor_map_repo = MagicMock()
    svc.employee_repo = MagicMock()
    svc.employee_repo.get_by_external_vendor_id.return_value = None
    svc.scheduled_job_repo = MagicMock()
    svc.vendor_repo.STATUS_IMPORT_FAILED = "IMPORT_FAILED"
    svc.vendor_repo.STATUS_SYNCED = "SYNCED"
    svc.vendor_repo.STATUS_PENDING_PUSH = "PENDING_PUSH"
    return svc


def connected_info(status="connected"):
    return {
        const.KEY_STATUS: status,
        const.KEY_REFRESH_TOKEN: "RT",
        const.KEY_REALM_ID: "RID",
        const.KEY_ENV: "sandbox",
    }


def config_ok():
    return {const.KEY_CLIENT_ID: "CID", const.KEY_CLIENT_SECRET: "SEC"}


def sample_qbo_vendor():
    return {
        const.QBO_KEY_ID: "V1",
        const.QBO_KEY_SYNC_TOKEN: "1",
        const.QBO_KEY_DISPLAY_NAME: "Vendor One",
        "PrimaryEmailAddr": {"Address": "vendor@test.com"},
        "PrimaryPhone": {"FreeFormNumber": "1234567890"},
        "BillAddr": {
            "Line1": "1 Main St",
            "City": "Madison",
            "CountrySubDivisionCode": "WI",
            "PostalCode": "53703",
            "Country": "USA",
        },
        "MetaData": {"LastUpdatedTime": "2026-01-01T00:00:00Z"},
    }


def test_import_vendors_not_connected():
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info(status="disconnected")
    out = svc.import_vendors(tenant_id="tn1", service_id="6")
    assert out["ok"] is False
    assert "not connected" in out["message"].lower()


def test_import_vendors_missing_refresh_or_realm():
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {const.KEY_STATUS: "connected", const.KEY_REFRESH_TOKEN: None, const.KEY_REALM_ID: None}
    out = svc.import_vendors(tenant_id="tn1", service_id="6")
    assert out["ok"] is False
    assert "missing refreshtoken/realmid" in out["message"].lower()


def test_import_vendors_missing_client_credentials():
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = {}
    out = svc.import_vendors(tenant_id="tn1", service_id="6")
    assert out["ok"] is False
    assert "client credentials missing" in out["message"].lower()


def test_import_vendors_full_mode_stops_when_no_batch(monkeypatch):
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()
    monkeypatch.setattr(svc, "_refresh_access_token", lambda *a, **k: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda env: "https://sandbox-qbo")
    monkeypatch.setattr(svc, "_resolve_changed_since", lambda **kwargs: (None, None))
    monkeypatch.setattr(svc, "_query_qbo_vendors", lambda **kwargs: [])
    svc.vendor_map_repo.get_open_import_failed_external_vendor_ids.return_value = []

    out = svc.import_vendors(tenant_id="tn1", service_id="6")

    assert out["ok"] is True
    assert out["mode"] == "FULL"
    assert out["processed"] == 0
    assert out["failed"] == 0


def test_import_vendors_cdc_mode_uses_watermark(monkeypatch):
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()
    changed_since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    seen = {}
    monkeypatch.setattr(svc, "_refresh_access_token", lambda *a, **k: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda env: "https://sandbox-qbo")
    monkeypatch.setattr(svc, "_resolve_changed_since", lambda **kwargs: (changed_since, "module-sync"))
    monkeypatch.setattr(svc, "_is_cdc_too_old", lambda value: False)
    def fake_cdc(**kwargs):
        seen.update(kwargs)
        return ([sample_qbo_vendor()], "tid-1")
    monkeypatch.setattr(svc, "_fetch_qbo_vendors_cdc", fake_cdc)
    monkeypatch.setattr(svc, "_process_qbo_vendor", lambda **kwargs: kwargs["summary"][const.KEY_RESULTS].append({"status": "ok"}))
    svc.vendor_map_repo.get_open_import_failed_external_vendor_ids.return_value = []

    out = svc.import_vendors(tenant_id="tn1", service_id="6")

    assert out["mode"] == "CDC"
    assert out["changedSince"] == changed_since.isoformat()
    assert out["watermarkSource"] == "module-sync"
    assert out["processed"] == 1
    assert seen["changed_since"] == changed_since


def test_import_vendors_runs_import_failed_retry(monkeypatch):
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()
    monkeypatch.setattr(svc, "_refresh_access_token", lambda *a, **k: "AT")
    monkeypatch.setattr(svc, "_get_qbo_api_base", lambda env: "https://sandbox-qbo")
    monkeypatch.setattr(svc, "_resolve_changed_since", lambda **kwargs: (None, None))
    monkeypatch.setattr(svc, "_query_qbo_vendors", lambda **kwargs: [])
    svc.vendor_map_repo.get_open_import_failed_external_vendor_ids.return_value = ["V1", "V2"]
    svc.import_vendors_by_ids = MagicMock(return_value={"processed": 2, "failed": 0})

    out = svc.import_vendors(tenant_id="tn1", service_id="6")

    assert out["persistentRetry"]["processed"] == 2
    svc.import_vendors_by_ids.assert_called_once()


def test_process_qbo_vendor_marks_import_failed_for_missing_required_fields():
    svc = make_service()
    summary = {const.KEY_RESULTS: [], const.KEY_FAILURES: [], "failed": 0}
    svc.vendor_map_repo.get_by_external_id.return_value = None
    svc.vendor_repo.build_vendor_payload_from_qbo.return_value = {const.KEY_EMAIL: "", const.KEY_PHONE: "", "address": "", "city": "", "state": "", "zip_code": ""}
    svc.vendor_repo.get_vendor_id_by_email.return_value = None

    svc._process_qbo_vendor(
        summary=summary,
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_vendor=sample_qbo_vendor(),
        dry_run=False,
    )

    assert summary["failed"] == 1
    assert summary["results"][0]["action"] == "missingRequired"
    svc.vendor_map_repo.upsert_import_failed_mapping.assert_called_once()
    reason = svc.vendor_map_repo.upsert_import_failed_mapping.call_args.kwargs["reason"]
    reason_payload = json.loads(reason)
    assert reason_payload["display_name"] == "Vendor One"
    assert reason_payload["qbo_vendor_id"] == "V1"


def test_process_qbo_vendor_skips_when_local_is_newer():
    svc = make_service()
    summary = {const.KEY_RESULTS: [], const.KEY_FAILURES: [], "skippedLocalNewer": 0}
    svc.vendor_map_repo.get_by_external_id.return_value = {"id": 11, "vendor_id": 77, "sync_status": "SYNCED"}
    svc.vendor_repo.build_vendor_payload_from_qbo.return_value = {const.KEY_EMAIL: "vendor@test.com"}
    svc.vendor_repo.get_current_vendor_by_id.return_value = {"id": 77}
    svc.vendor_repo.sync_projection_changed.return_value = True
    svc.vendor_repo.get_vendor_latest_local_timestamp.return_value = datetime(2026, 1, 2, tzinfo=timezone.utc)

    svc._process_qbo_vendor(
        summary=summary,
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_vendor=sample_qbo_vendor(),
        dry_run=False,
    )

    assert summary["skippedLocalNewer"] == 1
    assert summary["results"][0]["action"] == "localNewer"


def test_process_qbo_vendor_updates_existing_mapping_when_already_current():
    svc = make_service()
    summary = {const.KEY_RESULTS: [], const.KEY_FAILURES: [], "skippedAlreadyCurrent": 0}
    svc.vendor_map_repo.get_by_external_id.return_value = {"id": 11, "vendor_id": 77, "sync_status": "SYNCED"}
    svc.vendor_repo.build_vendor_payload_from_qbo.return_value = {const.KEY_EMAIL: "vendor@test.com"}
    svc.vendor_repo.get_current_vendor_by_id.return_value = {"id": 77}
    svc.vendor_repo.sync_projection_changed.return_value = False

    svc._process_qbo_vendor(
        summary=summary,
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_vendor=sample_qbo_vendor(),
        dry_run=False,
    )

    assert summary["skippedAlreadyCurrent"] == 1
    assert summary["results"][0]["action"] == "alreadyCurrent"
    svc.vendor_map_repo.mark_mapping_synced.assert_called_once()


def test_process_qbo_vendor_skips_when_mapped_to_fieldcraft_worker():
    svc = make_service()
    summary = {const.KEY_RESULTS: [], const.KEY_FAILURES: [], "skippedWorkerVendor": 0}
    svc.employee_repo.get_by_external_vendor_id.return_value = {"id": 55, "user_account_id": 42}

    svc._process_qbo_vendor(
        summary=summary,
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_vendor=sample_qbo_vendor(),
        dry_run=False,
    )

    assert summary["skippedWorkerVendor"] == 1
    assert summary["results"][0]["action"] == "workerVendor"
    assert summary["results"][0][const.KEY_FIELDCRAFT_EMPLOYEE_ID] == 42
    svc.vendor_map_repo.get_by_external_id.assert_not_called()
    svc.vendor_repo.create_vendor_for_tenant.assert_not_called()
