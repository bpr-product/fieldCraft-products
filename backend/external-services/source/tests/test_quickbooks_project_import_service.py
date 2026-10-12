import json
from datetime import datetime, timezone
from unittest.mock import ANY, MagicMock, patch

from source.Utils import constants as const
from source.quickbooks.QuickBooksProjectImportService import QuickBooksProjectImportService
import source.quickbooks.QuickBooksProjectImportService as project_import_module


def make_service():
    project_import_module.safe_audit_log = MagicMock()
    svc = QuickBooksProjectImportService()
    svc.qbo_repo = MagicMock()
    svc.qbo_service = MagicMock()
    svc.project_repo = MagicMock()
    svc.project_repo.STATUS_SYNC_FAILED = "SYNC_FAILED"
    return svc


def summary():
    return {
        const.KEY_OK: True,
        "processed": 0,
        "validatedMapped": 0,
        "skippedNonProjects": 0,
        "skippedUnmapped": 0,
        "skippedDeleted": 0,
        "skippedAlreadyCurrent": 0,
        "skippedLocalNewer": 0,
        "syncFailed": 0,
        "failed": 0,
        const.KEY_FAILURES: [],
        const.KEY_RESULTS: [],
    }


def project_row():
    return {"id": 4727, "description": "FIELDCRAFT Project Name", "order_no": "172918"}


def mapping_row(sync_token="1"):
    return {
        "id": 4,
        "project_id": 4727,
        "external_project_id": "70",
        "external_account_id": "9341457091159444",
        "sync_status": "SYNCED",
        "sync_token": sync_token,
    }


def qbo_project(sync_token="2", updated="2026-05-15T11:00:00+00:00"):
    return {
        const.QBO_KEY_ID: "70",
        const.QBO_KEY_SYNC_TOKEN: sync_token,
        const.QBO_KEY_DISPLAY_NAME: "Changed In QuickBooks",
        "Job": True,
        "ParentRef": {"value": "60"},
        "MetaData": {"LastUpdatedTime": updated},
    }


def test_project_import_skips_unmapped_qbo_sub_customer():
    svc = make_service()
    svc.project_repo.get_current_mapping_by_external_project_id.return_value = None
    data = summary()

    svc._process_qbo_customer(
        summary=data,
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_customer=qbo_project(),
    )

    assert data["skippedUnmapped"] == 1
    svc.project_repo.mark_mapping_failed.assert_not_called()


def test_project_import_failure_reason_keeps_message_and_error_in_correct_fields():
    svc = make_service()
    svc.project_repo.get_current_mapping_by_external_project_id.side_effect = RuntimeError("database lookup failed")
    data = summary()

    svc._process_qbo_customer(
        summary=data,
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_customer=qbo_project(),
    )

    reason = json.loads(data[const.KEY_RESULTS][0][const.KEY_REASON])
    assert reason[const.KEY_MESSAGE] == "database lookup failed"
    assert reason[const.KEY_ERROR] == "database lookup failed"
    assert json.loads(data[const.KEY_FAILURES][0][const.KEY_REASON]) == reason


def test_project_import_marks_sync_failed_when_mapped_qbo_project_is_newer():
    svc = make_service()
    svc.project_repo.get_current_mapping_by_external_project_id.return_value = mapping_row(sync_token="1")
    svc.project_repo.get_active_project_by_id.return_value = project_row()
    svc.project_repo.get_project_latest_local_timestamp.return_value = datetime(2026, 5, 15, 10, 0, tzinfo=timezone.utc)
    data = summary()

    svc._process_qbo_customer(
        summary=data,
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_customer=qbo_project(sync_token="2", updated="2026-05-15T11:00:00+00:00"),
    )

    assert data["syncFailed"] == 1
    assert data["failed"] == 1
    svc.project_repo.mark_mapping_failed.assert_called_once_with(
        tenant_id="tn1",
        mapping_id=4,
        status="SYNC_FAILED",
        reason=ANY,
    )


def test_project_import_does_not_fail_when_fieldcraft_is_newer():
    svc = make_service()
    svc.project_repo.get_current_mapping_by_external_project_id.return_value = mapping_row(sync_token="1")
    svc.project_repo.get_active_project_by_id.return_value = project_row()
    svc.project_repo.get_project_latest_local_timestamp.return_value = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
    data = summary()

    svc._process_qbo_customer(
        summary=data,
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_customer=qbo_project(sync_token="2", updated="2026-05-15T11:00:00+00:00"),
    )

    assert data["skippedLocalNewer"] == 1
    assert data["failed"] == 0
    svc.project_repo.mark_mapping_failed.assert_not_called()


def test_project_import_marks_sync_failed_when_qbo_name_changed_even_without_stored_sync_token():
    svc = make_service()
    svc.project_repo.get_current_mapping_by_external_project_id.return_value = mapping_row(sync_token="")
    svc.project_repo.get_active_project_by_id.return_value = project_row()
    svc.project_repo.get_project_latest_local_timestamp.return_value = datetime(2026, 5, 15, 10, 0, tzinfo=timezone.utc)
    data = summary()

    svc._process_qbo_customer(
        summary=data,
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_customer=qbo_project(sync_token="2", updated="2026-05-15T11:00:00+00:00"),
    )

    assert data["syncFailed"] == 1
    assert data["failed"] == 1
    assert data[const.KEY_RESULTS][0][const.KEY_ACTION] == "qboProjectChanged"
    svc.project_repo.mark_mapping_failed.assert_called_once_with(
        tenant_id="tn1",
        mapping_id=4,
        status="SYNC_FAILED",
        reason=ANY,
    )


def test_project_import_marks_current_when_qbo_token_changed_but_name_matches_fieldcraft():
    svc = make_service()
    svc.project_repo.get_current_mapping_by_external_project_id.return_value = mapping_row(sync_token="24")
    svc.project_repo.get_active_project_by_id.return_value = project_row()
    svc.project_repo.get_project_latest_local_timestamp.return_value = datetime(2026, 5, 15, 10, 0, tzinfo=timezone.utc)
    data = summary()

    matching_qbo = qbo_project(sync_token="25", updated="2026-05-15T11:00:00+00:00")
    matching_qbo[const.QBO_KEY_DISPLAY_NAME] = "FIELDCRAFT Project Name"

    svc._process_qbo_customer(
        summary=data,
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_customer=matching_qbo,
    )

    assert data["validatedMapped"] == 1
    assert data["failed"] == 0
    svc.project_repo.mark_mapping_failed.assert_not_called()
    svc.project_repo.mark_mapping_synced.assert_called_once_with(
        tenant_id="tn1",
        mapping_id=4,
        external_project_id="70",
        external_account_id="RID",
        sync_token="25",
        reason=ANY,
    )


def test_project_import_refresh_access_token_uses_explicit_request_context():
    svc = make_service()
    svc.qbo_service.token_url = "https://token.example.com"

    with patch("source.quickbooks.QuickBooksProjectImportService.QuickBooksOAuthClient") as oauth_cls:
        oauth = oauth_cls.return_value
        oauth.refresh_access_token.return_value = "AT"

        out = svc._refresh_access_token(
            client_id="CID",
            client_secret="SECRET",
            refresh_token="RT",
            tenant_id="tn1",
            service_id="6",
            connection={"realmId": "R1"},
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


def test_import_projects_by_ids_returns_clean_message_when_qbo_is_not_connected():
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {const.KEY_STATUS: "disconnected"}

    result = svc.import_projects_by_ids(tenant_id="tn1", service_id="6", project_ids=["70"])

    assert result[const.KEY_OK] is False
    assert "not connected" in result[const.KEY_MESSAGE].lower()
    svc.qbo_service.get_quickbooks_config.assert_not_called()
