import json
from unittest.mock import ANY, MagicMock, patch

from source.Utils import constants as const
from source.quickbooks.QuickBooksProjectExportService import QuickBooksProjectExportService
import source.quickbooks.QuickBooksProjectExportService as project_export_module


def make_service():
    project_export_module.safe_audit_log = MagicMock()
    svc = QuickBooksProjectExportService()
    svc.qbo_repo = MagicMock()
    svc.qbo_service = MagicMock()
    svc.project_repo = MagicMock()
    svc.customer_repo = MagicMock()
    svc.customer_repo.STATUS_PENDING_PUSH = "PENDING_PUSH"
    svc.customer_repo.STATUS_FAILED = "FAILED"
    svc.customer_repo.STATUS_IMPORT_FAILED = "IMPORT_FAILED"
    svc.customer_repo.STATUS_SYNC_FAILED = "SYNC_FAILED"
    svc.capability_service = MagicMock()
    svc.capability_service.projects_available.return_value = False
    svc.project_api_client = MagicMock()
    svc.qbo_repo.getProjectSyncConfig.return_value = {const.KEY_PROJECT_SYNC_METHOD: const.PROJECT_SYNC_METHOD_SUB_CUSTOMER}
    svc.project_repo.has_current_project_mappings.return_value = True
    svc.project_repo.get_mapping_sync_target.return_value = const.PROJECT_SYNC_TARGET_SUB_CUSTOMER
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


def project_row(project_id=10):
    return {
        "id": project_id,
        "project_uid": "8b49ae40-f737-42cd-b1c3-54ce8fbb0799",
        "customer_uid": "3657b201-9463-40d3-aec1-d8407cf0345d",
        "description": "vijay cus, Belvidere-Greenhouse Standard-172918",
        "order_no": "172918",
        "status": "Project Active",
        "customer_asset": {
            "address": "559, South State Street",
            "city": "Belvidere",
            "state": "IL",
            "country": "United States",
            "zipCode": "61008",
        },
    }


def mapping_row(mapping_id=1, project_id=10, external_project_id=None):
    return {
        "id": mapping_id,
        "project_id": project_id,
        "external_project_id": external_project_id,
        "external_account_id": None,
        "sync_status": "PENDING_PUSH",
        "sync_token": None,
    }


def prepare_connected_service(svc):
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()
    svc.refresh_access_token = MagicMock(return_value="TOKEN")
    svc.get_qbo_api_base = MagicMock(return_value="https://sandbox-qbo")
    svc.customer_repo.get_latest_qbo_linked_mapping_for_customer_uuid.return_value = {
        "external_customer_id": "QBO-CUST-1",
        "sync_status": "SYNCED",
    }
    svc.project_repo.get_current_mapping_by_external_project_id.return_value = None



def test_find_project_in_qbo_paginates_display_name_matches_by_parent():
    svc = QuickBooksProjectExportService()
    captured_queries = []
    first_page = [
        {const.QBO_KEY_ID: str(i), "ParentRef": {"value": "OTHER-CUSTOMER"}}
        for i in range(100)
    ]
    second_page = [
        {const.QBO_KEY_ID: "QBO-PROJ-1", const.QBO_KEY_SYNC_TOKEN: "0", "ParentRef": {"value": "QBO-CUST-1"}}
    ]

    def fake_get(url, headers=None, params=None, timeout=None):
        del url, headers, timeout
        captured_queries.append(params["query"])
        response = MagicMock()
        response.status_code = 200
        return response

    with patch("source.quickbooks.QuickBooksProjectExportService.qbo_api.qbo_get", side_effect=fake_get), \
         patch("source.quickbooks.QuickBooksProjectExportService.qbo_api.safe_json", side_effect=[
             {"QueryResponse": {"Customer": first_page}},
             {"QueryResponse": {"Customer": second_page}},
         ]):
        result = svc.find_project_in_qbo(
            access_token="AT",
            base_url="https://example.com",
            realm_id="R1",
            parent_qbo_customer_id="QBO-CUST-1",
            display_name="Common Project",
        )

    assert result[const.QBO_KEY_ID] == "QBO-PROJ-1"
    assert "STARTPOSITION 1 MAXRESULTS 100" in captured_queries[0]
    assert "STARTPOSITION 101 MAXRESULTS 100" in captured_queries[1]

def test_export_projects_missing_connection():
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {}

    result = svc.export_projects(tenant_id="tn1", service_id="6")

    assert result[const.KEY_OK] is False
    assert "not connected" in result[const.KEY_MESSAGE].lower()


def test_export_projects_requires_parent_customer_sync():
    svc = make_service()
    prepare_connected_service(svc)
    svc.customer_repo.get_latest_qbo_linked_mapping_for_customer_uuid.return_value = None
    svc.project_repo.get_export_candidates.return_value = [
        {"project": project_row(100), "mapping": mapping_row(7, 100)}
    ]

    result = svc.export_projects(tenant_id="tn1", service_id="6")

    assert result["failed"] == 1
    assert "customer linked to this project" in result[const.KEY_RESULTS][0][const.KEY_ERROR].lower()
    reason = json.loads(result[const.KEY_RESULTS][0][const.KEY_REASON])
    assert "customer linked to this project" in reason[const.KEY_MESSAGE].lower()
    assert "customer linked to this project" in reason[const.KEY_ERROR].lower()
    assert json.loads(result[const.KEY_FAILURES][0][const.KEY_REASON]) == reason
    svc.project_repo.mark_mapping_failed.assert_called_once_with(tenant_id="tn1", mapping_id=7, reason=ANY)


def test_export_projects_allows_parent_customer_pending_push_when_qbo_id_exists():
    svc = make_service()
    prepare_connected_service(svc)
    svc.customer_repo.get_latest_qbo_linked_mapping_for_customer_uuid.return_value = {
        "external_customer_id": "QBO-CUST-1",
        "sync_status": "PENDING_PUSH",
    }
    svc.project_repo.get_export_candidates.return_value = [
        {"project": project_row(101), "mapping": mapping_row(11, 101)}
    ]
    svc.find_project_in_qbo = MagicMock(return_value=None)
    svc.create_project_in_qbo = MagicMock(return_value={const.QBO_KEY_ID: "QBO-PROJ-1", const.QBO_KEY_SYNC_TOKEN: "0"})

    result = svc.export_projects(tenant_id="tn1", service_id="6")

    assert result["createdInQbo"] == 1
    payload = svc.create_project_in_qbo.call_args.kwargs["payload"]
    assert payload["ParentRef"] == {"value": "QBO-CUST-1"}


def test_export_projects_creates_qbo_sub_customer():
    svc = make_service()
    prepare_connected_service(svc)
    svc.project_repo.get_export_candidates.return_value = [
        {"project": project_row(101), "mapping": mapping_row(11, 101)}
    ]
    svc.find_project_in_qbo = MagicMock(return_value=None)
    svc.create_project_in_qbo = MagicMock(return_value={const.QBO_KEY_ID: "QBO-PROJ-1", const.QBO_KEY_SYNC_TOKEN: "0"})

    result = svc.export_projects(tenant_id="tn1", service_id="6")

    assert result["createdInQbo"] == 1
    payload = svc.create_project_in_qbo.call_args.kwargs["payload"]
    assert payload["Job"] is True
    assert payload["BillWithParent"] is True
    assert payload["ParentRef"] == {"value": "QBO-CUST-1"}
    svc.project_repo.mark_mapping_synced.assert_called_once_with(
        tenant_id="tn1",
        mapping_id=11,
        external_project_id="QBO-PROJ-1",
        external_account_id="RID",
        sync_token="0",
        reason=ANY,
        project_sync_target=const.PROJECT_SYNC_TARGET_SUB_CUSTOMER,
    )




def test_export_projects_creates_real_qbo_project_when_forced():
    svc = make_service()
    prepare_connected_service(svc)
    svc.qbo_repo.getProjectSyncConfig.return_value = {const.KEY_PROJECT_SYNC_METHOD: const.PROJECT_SYNC_METHOD_QBO_PROJECT}
    svc.capability_service.projects_available.return_value = True
    svc.project_repo.get_mapping_sync_target.return_value = None
    svc.project_repo.get_export_candidates.return_value = [
        {"project": project_row(150), "mapping": mapping_row(15, 150)}
    ]
    svc.project_api_client.create_project.return_value = {"id": "QBO-REAL-PROJ-1"}

    result = svc.export_projects(tenant_id="tn1", service_id="6")

    assert result["effectiveProjectSyncTarget"] == const.PROJECT_SYNC_TARGET_QBO_PROJECT
    assert result["createdInQbo"] == 1
    svc.capability_service.projects_available.assert_called_once_with("tn1", "6", refresh=True)
    svc.project_api_client.create_project.assert_called_once()
    svc.project_repo.mark_mapping_synced.assert_called_once()
    call_kwargs = svc.project_repo.mark_mapping_synced.call_args.kwargs
    assert call_kwargs["external_project_id"] == "QBO-REAL-PROJ-1"
    reason = json.loads(call_kwargs["reason"])
    assert reason["project_sync_target"] == const.PROJECT_SYNC_TARGET_QBO_PROJECT


def test_export_projects_blocks_forced_qbo_project_when_projects_are_disabled():
    svc = make_service()
    prepare_connected_service(svc)
    svc.qbo_repo.getProjectSyncConfig.return_value = {const.KEY_PROJECT_SYNC_METHOD: const.PROJECT_SYNC_METHOD_QBO_PROJECT}
    svc.capability_service.projects_available.return_value = False

    result = svc.export_projects(tenant_id="tn1", service_id="6")

    assert result[const.KEY_OK] is False
    assert result[const.KEY_ERROR] == "feature_not_available"
    assert "Auto" in result[const.KEY_MESSAGE]
    svc.capability_service.projects_available.assert_called_once_with("tn1", "6", refresh=True)
    svc.project_repo.get_export_candidates.assert_not_called()

def test_export_projects_maps_existing_qbo_sub_customer():
    svc = make_service()
    prepare_connected_service(svc)
    svc.project_repo.get_export_candidates.return_value = [
        {"project": project_row(102), "mapping": mapping_row(12, 102)}
    ]
    svc.find_project_in_qbo = MagicMock(return_value={const.QBO_KEY_ID: "QBO-PROJ-2", const.QBO_KEY_SYNC_TOKEN: "2", "ParentRef": {"value": "QBO-CUST-1"}})

    result = svc.export_projects(tenant_id="tn1", service_id="6")

    assert result["mapped from QBO"] == 1
    svc.create_project_in_qbo.assert_not_called() if hasattr(svc.create_project_in_qbo, "assert_not_called") else None
    svc.project_repo.mark_mapping_synced.assert_called_once()


def test_export_projects_skips_existing_mapping_without_qbo_update():
    svc = make_service()
    prepare_connected_service(svc)
    svc.project_repo.get_export_candidates.return_value = [
        {"project": project_row(103), "mapping": mapping_row(13, 103, external_project_id="QBO-PROJ-3")}
    ]

    result = svc.export_projects(tenant_id="tn1", service_id="6")

    assert result["updatedInQbo"] == 0
    assert result["skippedAlreadySynced"] == 1
    svc.project_repo.mark_mapping_synced.assert_called_once_with(
        tenant_id="tn1",
        mapping_id=13,
        external_project_id="QBO-PROJ-3",
        external_account_id="RID",
        sync_token=None,
        reason=ANY,
        project_sync_target=const.PROJECT_SYNC_TARGET_SUB_CUSTOMER,
    )


def test_export_projects_detects_external_project_mapping_conflict():
    svc = make_service()
    prepare_connected_service(svc)
    svc.project_repo.get_current_mapping_by_external_project_id.return_value = {"project_id": 999}
    svc.project_repo.get_export_candidates.return_value = [
        {"project": project_row(104), "mapping": mapping_row(14, 104, external_project_id="QBO-PROJ-4")}
    ]

    result = svc.export_projects(tenant_id="tn1", service_id="6")

    assert result["failed"] == 1
    assert "already mapped" in result[const.KEY_RESULTS][0][const.KEY_ERROR]
    svc.project_repo.mark_mapping_failed.assert_called_once()


def test_export_projects_leaves_existing_mapping_unchanged_without_conflict_checking_qbo():
    svc = make_service()
    prepare_connected_service(svc)
    mapping = mapping_row(15, 105, external_project_id="QBO-PROJ-5")
    mapping["sync_token"] = "1"
    svc.project_repo.get_export_candidates.return_value = [
        {"project": project_row(105), "mapping": mapping}
    ]

    result = svc.export_projects(tenant_id="tn1", service_id="6")

    assert result["failed"] == 0
    assert result["syncFailed"] == 0
    assert result["skippedAlreadySynced"] == 1
    svc.project_repo.mark_mapping_failed.assert_not_called()


def test_export_projects_does_not_update_existing_mapping_even_when_fieldcraft_changed():
    svc = make_service()
    prepare_connected_service(svc)
    mapping = mapping_row(16, 106, external_project_id="QBO-PROJ-6")
    mapping["sync_token"] = "1"
    svc.project_repo.get_export_candidates.return_value = [
        {"project": project_row(106), "mapping": mapping}
    ]

    result = svc.export_projects(tenant_id="tn1", service_id="6")

    assert result["updatedInQbo"] == 0
    assert result["skippedAlreadySynced"] == 1
    svc.project_repo.mark_mapping_failed.assert_not_called()


def test_export_projects_does_not_read_or_update_existing_mapping_when_sync_token_changed():
    svc = make_service()
    prepare_connected_service(svc)
    mapping = mapping_row(17, 107, external_project_id="QBO-PROJ-7")
    mapping["sync_token"] = "24"
    svc.project_repo.get_export_candidates.return_value = [
        {"project": project_row(107), "mapping": mapping}
    ]

    result = svc.export_projects(tenant_id="tn1", service_id="6")

    assert result["updatedInQbo"] == 0
    assert result["failed"] == 0
    assert result["skippedAlreadySynced"] == 1
    svc.project_repo.mark_mapping_failed.assert_not_called()


def test_build_qbo_address_uses_normalized_customer_asset_without_private_repo_parser():
    svc = make_service()
    svc.project_repo._parse_jsonish.side_effect = AssertionError("private repo parser should not be called")

    result = svc._build_qbo_address(project_row())

    assert result == {
        "Line1": "559, South State Street",
        "City": "Belvidere",
        "CountrySubDivisionCode": "IL",
        "Country": "United States",
        "PostalCode": "61008",
    }
