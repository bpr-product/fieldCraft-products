from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from source.Utils import constants as const
from source.dal.TimelogRepository import TimelogRepository
from source.Utils.QuickBooksErrorHandler import QuickBooksAPIError
from source.quickbooks.QuickBooksTimelogExportService import QuickBooksTimelogExportService


def _make_service():
    svc = QuickBooksTimelogExportService()
    svc.project_repo = MagicMock()
    svc.project_repo.get_mapping_sync_target.side_effect = (
        lambda mapping: mapping.get(const.KEY_PROJECT_SYNC_TARGET) or const.PROJECT_SYNC_TARGET_SUB_CUSTOMER
    )
    svc.capability_service = MagicMock()
    svc.capability_service.projects_available.return_value = True
    svc._employee_sync_config = {}
    return svc


def _candidate(project_target=const.PROJECT_SYNC_TARGET_SUB_CUSTOMER, **overrides):
    start_time = datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)
    data = {
        "timelog_id": 101,
        "task_id": 501,
        "task_name": "Roof inspection",
        "timelog_status": "completed",
        "approval_status": "APPROVED",
        "start_time": start_time,
        "end_time": start_time + timedelta(minutes=75),
        "qbo_employee_id": "EMP-1",
        "employee_sync_status": TimelogRepository.STATUS_SYNCED,
        "qbo_project_id": "QBO-PROJECT-1",
        "project_sync_status": TimelogRepository.STATUS_SYNCED,
        const.KEY_PROJECT_SYNC_TARGET: project_target,
        "role_name": "Installer",
        "qbo_service_id": "SERVICE-1",
        "service_sync_status": TimelogRepository.STATUS_SYNCED,
    }
    data.update(overrides)
    return data


def test_build_time_activity_payload_uses_customer_ref_for_sub_customer_project():
    svc = _make_service()

    payload = svc.build_time_activity_payload(_candidate(const.PROJECT_SYNC_TARGET_SUB_CUSTOMER))

    assert payload["CustomerRef"] == {"value": "QBO-PROJECT-1"}
    assert "ProjectRef" not in payload
    assert payload["EmployeeRef"] == {"value": "EMP-1"}
    assert payload["ItemRef"] == {"value": "SERVICE-1"}
    assert payload["Hours"] == 1
    assert payload["Minutes"] == 15




def test_build_time_activity_payload_matches_fieldcraft_whole_minute_display():
    svc = _make_service()
    start_time = datetime(2026, 7, 10, 13, 14, 5, tzinfo=timezone.utc)

    payload = svc.build_time_activity_payload(
        _candidate(
            const.PROJECT_SYNC_TARGET_SUB_CUSTOMER,
            start_time=start_time,
            end_time=start_time + timedelta(minutes=6, seconds=27),
        )
    )

    assert payload["Hours"] == 0
    assert payload["Minutes"] == 6


def test_build_time_activity_payload_keeps_one_minute_for_sub_minute_timelog():
    svc = _make_service()
    start_time = datetime(2026, 7, 10, 13, 14, 5, tzinfo=timezone.utc)

    payload = svc.build_time_activity_payload(
        _candidate(
            const.PROJECT_SYNC_TARGET_SUB_CUSTOMER,
            start_time=start_time,
            end_time=start_time + timedelta(seconds=30),
        )
    )

    assert payload["Hours"] == 0
    assert payload["Minutes"] == 1


def test_build_time_activity_payload_uses_parent_customer_and_project_ref_for_qbo_project():
    svc = _make_service()

    payload = svc.build_time_activity_payload(
        _candidate(
            const.PROJECT_SYNC_TARGET_QBO_PROJECT,
            qbo_parent_customer_id="PARENT-CUSTOMER-1",
            parent_customer_sync_status=TimelogRepository.STATUS_SYNCED,
        )
    )

    assert payload["CustomerRef"] == {"value": "PARENT-CUSTOMER-1"}
    assert payload["ProjectRef"] == {"value": "QBO-PROJECT-1"}
    assert payload["EmployeeRef"] == {"value": "EMP-1"}
    assert payload["ItemRef"] == {"value": "SERVICE-1"}



def test_build_time_activity_payload_records_hourly_w2_internal_cost_without_customer_bill_rate():
    svc = _make_service()

    payload = svc.build_time_activity_payload(
        _candidate(
            const.PROJECT_SYNC_TARGET_SUB_CUSTOMER,
            resource_type=const.EMPLOYEE_TYPE_W2,
            external_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE,
            resource_cost=120,
            resource_cost_unit="hourly",
        )
    )

    assert "BillableStatus" not in payload
    assert "HourlyRate" not in payload
    assert "CostRate" not in payload
    assert "FIELDCRAFT internal labor cost: $120.00/hour." in payload["Description"]


def test_build_time_activity_payload_converts_daily_internal_cost_without_customer_bill_rate():
    svc = _make_service()

    payload = svc.build_time_activity_payload(
        _candidate(
            const.PROJECT_SYNC_TARGET_SUB_CUSTOMER,
            resource_cost=800,
            resource_cost_unit="daily",
        )
    )

    assert "BillableStatus" not in payload
    assert "HourlyRate" not in payload
    assert "CostRate" not in payload
    assert "FIELDCRAFT internal labor cost: $100.00/hour." in payload["Description"]


def test_build_time_activity_payload_omits_billable_fields_for_zero_resource_cost():
    svc = _make_service()

    payload = svc.build_time_activity_payload(
        _candidate(
            const.PROJECT_SYNC_TARGET_SUB_CUSTOMER,
            resource_cost=0,
            resource_cost_unit="hourly",
        )
    )

    assert "BillableStatus" not in payload
    assert "HourlyRate" not in payload
    assert "CostRate" not in payload

def test_validate_time_activity_allows_qbo_project_parent_customer_pending_push_when_qbo_id_exists():
    svc = _make_service()

    error = svc._validate_candidate(
        _candidate(
            const.PROJECT_SYNC_TARGET_QBO_PROJECT,
            qbo_parent_customer_id="PARENT-CUSTOMER-1",
            parent_customer_sync_status="PENDING_PUSH",
        ),
        tenant_id="tn1",
        service_id="6",
    )

    assert error is None
    svc.capability_service.projects_available.assert_called_once_with("tn1", "6", refresh=True)


def test_validate_time_activity_blocks_qbo_project_when_parent_customer_missing():
    svc = _make_service()

    error = svc._validate_candidate(
        _candidate(const.PROJECT_SYNC_TARGET_QBO_PROJECT),
        tenant_id="tn1",
        service_id="6",
    )

    assert "Parent customer" in error
    svc.capability_service.projects_available.assert_not_called()


def test_validate_time_activity_blocks_qbo_project_when_projects_are_disabled():
    svc = _make_service()
    svc.capability_service.projects_available.return_value = False

    error = svc._validate_candidate(
        _candidate(
            const.PROJECT_SYNC_TARGET_QBO_PROJECT,
            qbo_parent_customer_id="PARENT-CUSTOMER-1",
            parent_customer_sync_status=TimelogRepository.STATUS_SYNCED,
        ),
        tenant_id="tn1",
        service_id="6",
    )

    assert "QuickBooks Projects are not enabled" in error
    svc.capability_service.projects_available.assert_called_once_with("tn1", "6", refresh=True)


def test_timelog_repository_reads_project_target_from_mapping_reason():
    repo = TimelogRepository()

    assert repo._project_sync_target_from_reason(
        reason={const.KEY_PROJECT_SYNC_TARGET: const.PROJECT_SYNC_TARGET_QBO_PROJECT},
        qbo_project_id="QBO-PROJECT-1",
    ) == const.PROJECT_SYNC_TARGET_QBO_PROJECT


def test_timelog_repository_defaults_legacy_project_mapping_to_sub_customer():
    repo = TimelogRepository()

    assert repo._project_sync_target_from_reason(reason=None, qbo_project_id="QBO-SUBCUSTOMER-1") == const.PROJECT_SYNC_TARGET_SUB_CUSTOMER


def test_build_time_activity_payload_uses_vendor_ref_for_1099_contractor():
    svc = _make_service()

    payload = svc.build_time_activity_payload(
        _candidate(
            const.PROJECT_SYNC_TARGET_SUB_CUSTOMER,
            resource_type=const.EMPLOYEE_TYPE_1099,
            external_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
            qbo_employee_id=None,
            qbo_vendor_id="VEN-1",
        )
    )

    assert payload["NameOf"] == "Vendor"
    assert payload["VendorRef"] == {"value": "VEN-1"}
    assert "EmployeeRef" not in payload
    assert payload["CustomerRef"] == {"value": "QBO-PROJECT-1"}



def test_build_time_activity_payload_records_1099_internal_cost_without_customer_bill_rate():
    svc = _make_service()

    payload = svc.build_time_activity_payload(
        _candidate(
            const.PROJECT_SYNC_TARGET_SUB_CUSTOMER,
            resource_type=const.EMPLOYEE_TYPE_1099,
            external_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
            qbo_employee_id=None,
            qbo_vendor_id="VEN-1",
            resource_cost=75,
            resource_cost_unit="hourly",
        )
    )

    assert payload["NameOf"] == "Vendor"
    assert payload["VendorRef"] == {"value": "VEN-1"}
    assert "HourlyRate" not in payload
    assert "BillableStatus" not in payload
    assert "CostRate" not in payload
    assert "FIELDCRAFT internal labor cost: $75.00/hour." in payload["Description"]


def test_build_time_activity_payload_uses_hourly_rate_only_when_configured_as_customer_bill_rate():
    svc = _make_service()
    svc._employee_sync_config = {
        const.KEY_EMPLOYEE_SYNC_COST_ENABLED: True,
        const.KEY_EMPLOYEE_SYNC_DEFAULT_COST_UNIT: const.EMPLOYEE_COST_UNIT_HOURLY,
        const.KEY_EMPLOYEE_SYNC_COST_UNIT_HOURS: const.EMPLOYEE_COST_UNIT_HOURS_DEFAULT,
        const.KEY_EMPLOYEE_SYNC_COST_QBO_FIELD: const.EMPLOYEE_COST_QBO_FIELD_BILL_RATE,
    }

    payload = svc.build_time_activity_payload(
        _candidate(
            const.PROJECT_SYNC_TARGET_SUB_CUSTOMER,
            resource_cost=75,
            resource_cost_unit="hourly",
        )
    )

    assert payload["HourlyRate"] == 75.0
    assert payload["BillableStatus"] == "Billable"
    assert "internal labor cost" not in payload["Description"]


def test_build_time_activity_payload_uses_vendor_ref_and_project_ref_for_1099_qbo_project():
    svc = _make_service()

    payload = svc.build_time_activity_payload(
        _candidate(
            const.PROJECT_SYNC_TARGET_QBO_PROJECT,
            resource_type=const.EMPLOYEE_TYPE_1099,
            external_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
            qbo_employee_id=None,
            qbo_vendor_id="VEN-1",
            qbo_parent_customer_id="PARENT-CUSTOMER-1",
            parent_customer_sync_status=TimelogRepository.STATUS_SYNCED,
        )
    )

    assert payload["NameOf"] == "Vendor"
    assert payload["VendorRef"] == {"value": "VEN-1"}
    assert "EmployeeRef" not in payload
    assert payload["CustomerRef"] == {"value": "PARENT-CUSTOMER-1"}
    assert payload["ProjectRef"] == {"value": "QBO-PROJECT-1"}


def test_build_time_activity_payload_uses_employee_ref_and_project_ref_for_w2_qbo_project():
    svc = _make_service()

    payload = svc.build_time_activity_payload(
        _candidate(
            const.PROJECT_SYNC_TARGET_QBO_PROJECT,
            resource_type=const.EMPLOYEE_TYPE_W2,
            external_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE,
            qbo_employee_id="EMP-1",
            qbo_parent_customer_id="PARENT-CUSTOMER-1",
            parent_customer_sync_status=TimelogRepository.STATUS_SYNCED,
        )
    )

    assert payload["NameOf"] == "Employee"
    assert payload["EmployeeRef"] == {"value": "EMP-1"}
    assert "VendorRef" not in payload
    assert payload["CustomerRef"] == {"value": "PARENT-CUSTOMER-1"}
    assert payload["ProjectRef"] == {"value": "QBO-PROJECT-1"}

def test_validate_time_activity_requires_vendor_mapping_for_1099_contractor():
    svc = _make_service()

    error = svc._validate_candidate(
        _candidate(
            const.PROJECT_SYNC_TARGET_SUB_CUSTOMER,
            resource_type=const.EMPLOYEE_TYPE_1099,
            external_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
            qbo_employee_id=None,
            qbo_vendor_id=None,
        ),
        tenant_id="tn1",
        service_id="6",
    )

    assert error == "1099 contractor is not synced to QuickBooks Vendor."


def test_validate_time_activity_requires_employee_mapping_for_w2_employee():
    svc = _make_service()

    error = svc._validate_candidate(
        _candidate(
            const.PROJECT_SYNC_TARGET_SUB_CUSTOMER,
            resource_type=const.EMPLOYEE_TYPE_W2,
            external_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE,
            qbo_employee_id=None,
        ),
        tenant_id="tn1",
        service_id="6",
    )

    assert error == "W2 employee is not synced to QuickBooks Employee."


def test_create_time_activity_does_not_drop_project_ref_when_qbo_rejects_it(monkeypatch):
    svc = _make_service()
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(dict(json))
        resp = MagicMock()
        resp.status_code = 400
        resp.json.return_value = {
            "Fault": {
                "Error": [
                    {
                        "code": "2010",
                        "Message": "Request has invalid or unsupported property",
                        "Detail": "Request has invalid or unsupported property",
                    }
                ]
            }
        }
        return resp

    monkeypatch.setattr("source.quickbooks.QuickBooksTimelogExportService.qbo_api.qbo_post", fake_post)

    with pytest.raises(QuickBooksAPIError, match="invalid or unsupported property"):
        svc.create_time_activity_in_qbo(
            access_token="token",
            base_url="https://sandbox-quickbooks.api.intuit.com",
            realm_id="realm",
            payload={
                "TxnDate": "2026-07-08",
                "NameOf": "Vendor",
                "VendorRef": {"value": "68"},
                "ItemRef": {"value": "26"},
                "CustomerRef": {"value": "59"},
                "ProjectRef": {"value": "56077"},
                "Hours": 1,
                "Minutes": 0,
            },
        )

    assert len(calls) == 1
    assert calls[0]["ProjectRef"] == {"value": "56077"}


def test_create_time_activity_does_not_retry_non_project_ref_error(monkeypatch):
    svc = _make_service()
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(dict(json))
        resp = MagicMock()
        resp.status_code = 400
        resp.json.return_value = {
            "Fault": {
                "Error": [
                    {
                        "code": "2500",
                        "Message": "Invalid Reference Id",
                        "Detail": "Items element id 26 not found",
                    }
                ]
            }
        }
        return resp

    monkeypatch.setattr("source.quickbooks.QuickBooksTimelogExportService.qbo_api.qbo_post", fake_post)

    try:
        svc.create_time_activity_in_qbo(
            access_token="token",
            base_url="https://sandbox-quickbooks.api.intuit.com",
            realm_id="realm",
            payload={
                "TxnDate": "2026-07-08",
                "NameOf": "Vendor",
                "VendorRef": {"value": "68"},
                "ItemRef": {"value": "26"},
                "CustomerRef": {"value": "59"},
                "ProjectRef": {"value": "56077"},
                "Hours": 1,
                "Minutes": 0,
            },
        )
    except Exception as exc:
        assert "Items element id 26 not found" in str(exc)
    else:
        raise AssertionError("expected create_time_activity_in_qbo to raise")

    assert len(calls) == 1


def test_process_candidate_rejects_mapping_from_different_realm_before_qbo_write():
    svc = _make_service()
    svc.timelog_repo = MagicMock()
    svc.create_time_activity_in_qbo = MagicMock()
    summary = {
        "createdInQbo": 0,
        "updatedInQbo": 0,
        "skipped": 0,
        "failed": 0,
        "syncFailed": 0,
        const.KEY_FAILURES: [],
        const.KEY_RESULTS: [],
    }
    candidate = _candidate(
        map_id=7,
        external_time_activity_id=None,
        external_account_id="old-realm",
        map_sync_status=TimelogRepository.STATUS_PENDING_PUSH,
    )

    svc._process_candidate(
        candidate=candidate,
        tenant_id="tn1",
        service_id="6",
        realm_id="current-realm",
        base_url="https://sandbox-quickbooks.api.intuit.com",
        access_token="token",
        dry_run=False,
        summary=summary,
    )

    assert summary["syncFailed"] == 1
    assert "different QuickBooks company" in summary[const.KEY_FAILURES][0][const.KEY_ERROR]
    svc.create_time_activity_in_qbo.assert_not_called()
    svc.timelog_repo.mark_synced.assert_not_called()


def test_time_activity_create_request_id_is_stable_and_within_qbo_limit():
    svc = _make_service()

    first = svc._time_activity_request_id(
        operation="create",
        tenant_id="tn1",
        service_id="6",
        realm_id="realm-1",
        timelog_id=101,
    )
    second = svc._time_activity_request_id(
        operation="create",
        tenant_id="tn1",
        service_id="6",
        realm_id="realm-1",
        timelog_id=101,
        payload={"Hours": 99},
    )

    assert first == second
    assert first.startswith("fieldcraft-tl-c-")
    assert len(first) <= 50


def test_time_activity_update_request_id_changes_for_a_new_update_version():
    svc = _make_service()
    base = dict(
        operation="update",
        tenant_id="tn1",
        service_id="6",
        realm_id="realm-1",
        timelog_id=101,
        external_id="9001",
        sync_token="0",
        payload={"Hours": 1, "Minutes": 15},
    )

    first = svc._time_activity_request_id(**base)
    same = svc._time_activity_request_id(**base)
    changed_payload = svc._time_activity_request_id(
        **{**base, "payload": {"Hours": 1, "Minutes": 30}}
    )
    changed_sync_token = svc._time_activity_request_id(
        **{**base, "sync_token": "1"}
    )

    assert first == same
    assert first != changed_payload
    assert first != changed_sync_token
    assert first.startswith("fieldcraft-tl-u-")
    assert len(first) <= 50


def test_create_time_activity_sends_qbo_request_id(monkeypatch):
    svc = _make_service()
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["payload"] = dict(json)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"TimeActivity": {"Id": "9001", "SyncToken": "0"}}
        return resp

    monkeypatch.setattr("source.quickbooks.QuickBooksTimelogExportService.qbo_api.qbo_post", fake_post)

    result = svc.create_time_activity_in_qbo(
        access_token="token",
        base_url="https://sandbox-quickbooks.api.intuit.com",
        realm_id="realm",
        payload={"TxnDate": "2026-07-23", "Hours": 1, "Minutes": 0},
        request_id="fieldcraft-tl-c-1234567890abcdef",
    )

    assert result["Id"] == "9001"
    assert "minorversion=75" in captured["url"]
    assert "requestid=fieldcraft-tl-c-1234567890abcdef" in captured["url"]


def test_process_candidate_reuses_create_request_id_after_ambiguous_failure():
    svc = _make_service()
    svc.timelog_repo = MagicMock()
    svc.timelog_repo.get_current_mapping_by_external_id.return_value = None
    svc.create_time_activity_in_qbo = MagicMock(
        side_effect=[
            TimeoutError("QuickBooks response timed out"),
            {const.QBO_KEY_ID: "9001", const.QBO_KEY_SYNC_TOKEN: "0"},
        ]
    )
    candidate = _candidate(
        map_id=7,
        external_time_activity_id=None,
        external_account_id="realm-1",
        map_sync_status=TimelogRepository.STATUS_PENDING_PUSH,
    )

    def new_summary():
        return {
            "createdInQbo": 0,
            "updatedInQbo": 0,
            "skipped": 0,
            "failed": 0,
            "syncFailed": 0,
            const.KEY_FAILURES: [],
            const.KEY_RESULTS: [],
        }

    first_summary = new_summary()
    second_summary = new_summary()

    svc._process_candidate(
        candidate=candidate,
        tenant_id="tn1",
        service_id="6",
        realm_id="realm-1",
        base_url="https://sandbox-quickbooks.api.intuit.com",
        access_token="token",
        dry_run=False,
        summary=first_summary,
    )
    svc._process_candidate(
        candidate=candidate,
        tenant_id="tn1",
        service_id="6",
        realm_id="realm-1",
        base_url="https://sandbox-quickbooks.api.intuit.com",
        access_token="token",
        dry_run=False,
        summary=second_summary,
    )

    first_request_id = svc.create_time_activity_in_qbo.call_args_list[0].kwargs["request_id"]
    second_request_id = svc.create_time_activity_in_qbo.call_args_list[1].kwargs["request_id"]
    assert first_request_id == second_request_id
    assert first_summary["syncFailed"] == 1
    assert second_summary["createdInQbo"] == 1
    svc.timelog_repo.mark_synced.assert_called_once()


def test_employee_use_time_entry_setting_is_read_and_cached(monkeypatch):
    from source.Utils import QuickBooksErrorHandler as qbo_err

    svc = _make_service()
    response = MagicMock(status_code=200)
    get_mock = MagicMock(return_value=response)
    safe_json_mock = MagicMock(return_value={
        "Employee": {
            "Id": "400000001",
            "DisplayName": "manish paul",
            "UseTimeEntry": "UseTimeEntry",
        }
    })
    monkeypatch.setattr(qbo_err, "qbo_get", get_mock)
    monkeypatch.setattr(qbo_err, "safe_json", safe_json_mock)
    monkeypatch.setattr(qbo_err, "raise_for_qbo_response", MagicMock())

    first = svc.get_employee_time_entry_setting(
        access_token="token",
        base_url="https://quickbooks.api.intuit.com",
        realm_id="realm",
        employee_id="400000001",
    )
    second = svc.get_employee_time_entry_setting(
        access_token="token",
        base_url="https://quickbooks.api.intuit.com",
        realm_id="realm",
        employee_id="400000001",
    )

    assert first["useTimeEntry"] is True
    assert first["useTimeEntryValue"] == "UseTimeEntry"
    assert second == first
    get_mock.assert_called_once()


def test_employee_use_time_entry_enum_values_are_normalized():
    svc = _make_service()

    assert svc._use_time_entry_enabled("UseTimeEntry") is True
    assert svc._use_time_entry_enabled("DoNotUseTimeEntry") is False
    assert svc._use_time_entry_enabled("NotSet") is False
    assert svc._use_time_entry_enabled(None) is None
