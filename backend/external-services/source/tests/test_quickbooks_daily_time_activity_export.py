from unittest.mock import MagicMock
from collections import defaultdict
from contextlib import nullcontext
from datetime import date
from types import SimpleNamespace

import pytest

from source.Utils import constants as const
from source.quickbooks.QuickBooksDailyTimelogExportService import (
    QuickBooksDailyTimelogExportService,
)
from source.quickbooks.QuickBooksPayrollCompensationService import (
    QuickBooksPayrollCompensationService,
)
from source.quickbooks.QuickBooksTimelogExportService import create_timelog_export_service


def _service():
    service = QuickBooksDailyTimelogExportService.__new__(QuickBooksDailyTimelogExportService)
    service.project_repo = type(
        "ProjectRepo",
        (),
        {"get_mapping_sync_target": staticmethod(lambda row: row[const.KEY_PROJECT_SYNC_TARGET])},
    )()
    return service


def _row(**overrides):
    data = {
        "map_id": 501,
        "timelog_id": 101,
        "task_name": "Morning setup",
        "employee_name": "Mary Sales",
        "project_name": "Madison Roof",
        "role_name": "Labour",
        "start_time": "2026-08-06T14:00:00+00:00",
        "end_time": "2026-08-06T14:30:00+00:00",
        "resource_type": const.EMPLOYEE_TYPE_W2,
        "external_entity_type": const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE,
        "qbo_employee_id": "83",
        "qbo_vendor_id": None,
        "qbo_project_id": "809093613",
        "qbo_parent_customer_id": "71",
        const.KEY_PROJECT_SYNC_TARGET: const.PROJECT_SYNC_TARGET_QBO_PROJECT,
        "qbo_service_id": "6",
        "approval_status": "APPROVED",
        "timelog_status": "completed",
        "timelog_invalid": False,
        "entry_type": "WORK",
        "employee_sync_status": "SYNCED",
        "project_sync_status": "SYNCED",
        "service_sync_status": "SYNCED",
    }
    data.update(overrides)
    return data


def _daily_summary():
    summary = defaultdict(int)
    summary[const.KEY_RESULTS] = []
    summary[const.KEY_FAILURES] = []
    return summary


def _wire_daily_group_test(service, row):
    resolution = SimpleNamespace(
        timezone_name="America/Chicago",
        source="test",
        latitude=None,
        longitude=None,
    )
    service._timezone_resolution = lambda _row: resolution
    service._daily_group_lock = lambda **_kwargs: nullcontext()
    service._load_daily_group_rows = lambda **_kwargs: [row]
    service._rows_for_group = lambda rows, **_kwargs: list(rows)
    service._mapped_time_activity_ids = lambda **_kwargs: []
    service.validate_candidate = lambda *_args, **_kwargs: None
    service._w2_time_entry_setting = lambda **_kwargs: None
    service._save_pending_snapshot = lambda **_kwargs: None
    service._save_group_success = lambda **_kwargs: None


def _process_daily_group(service, row, summary):
    return service._process_daily_group(
        group_key=(246, date(2026, 8, 6), 2237),
        triggers=[row],
        is_previous_group=False,
        tenant_id="tn59395620",
        service_id="6",
        realm_id="realm-1",
        base_url="https://quickbooks.api.intuit.com",
        access_token="token",
        dry_run=False,
        summary=summary,
    )


def test_factory_uses_daily_accounting_time_activity_exporter():
    assert isinstance(create_timelog_export_service(), QuickBooksDailyTimelogExportService)


def test_w2_daily_payload_uses_employee_ref_and_one_daily_duration():
    service = _service()
    rows = [_row(timelog_id=101), _row(timelog_id=102, task_name="Field review")]

    payload = service._build_daily_payload(
        group_key=(246, date(2026, 8, 6), 2237),
        rows=rows,
        total_seconds=61 * 60,
        timezone_name="America/Chicago",
    )

    assert payload["NameOf"] == "Employee"
    assert payload["EmployeeRef"] == {"value": "83"}
    assert "VendorRef" not in payload
    assert payload["CustomerRef"] == {"value": "71"}
    assert payload["ProjectRef"] == {"value": "809093613"}
    assert payload["ItemRef"] == {"value": "6"}
    assert payload["TxnDate"] == "2026-08-06"
    assert payload["Hours"] == 1
    assert payload["Minutes"] == 1


def test_1099_daily_payload_uses_vendor_ref():
    service = _service()
    row = _row(
        resource_type=const.EMPLOYEE_TYPE_1099,
        external_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
        qbo_employee_id=None,
        qbo_vendor_id="68",
        qbo_project_id="59",
        qbo_parent_customer_id=None,
        **{const.KEY_PROJECT_SYNC_TARGET: const.PROJECT_SYNC_TARGET_SUB_CUSTOMER},
    )

    payload = service._build_daily_payload(
        group_key=(247, date(2026, 8, 6), 2237),
        rows=[row],
        total_seconds=30 * 60,
        timezone_name="America/Chicago",
    )

    assert payload["NameOf"] == "Vendor"
    assert payload["VendorRef"] == {"value": "68"}
    assert "EmployeeRef" not in payload
    assert payload["CustomerRef"] == {"value": "59"}
    assert "ProjectRef" not in payload
    assert payload["Hours"] == 0
    assert payload["Minutes"] == 30


def test_1099_daily_payload_preserves_hourly_internal_cost_in_description():
    service = _service()
    service._employee_sync_config = {}
    row = _row(
        resource_type=const.EMPLOYEE_TYPE_1099,
        external_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
        qbo_employee_id=None,
        qbo_vendor_id="68",
        qbo_project_id="59",
        qbo_parent_customer_id=None,
        resource_cost=75,
        resource_cost_unit="hourly",
        **{const.KEY_PROJECT_SYNC_TARGET: const.PROJECT_SYNC_TARGET_SUB_CUSTOMER},
    )

    payload = service._build_daily_payload(
        group_key=(247, date(2026, 8, 6), 2237),
        rows=[row],
        total_seconds=30 * 60,
        timezone_name="America/Chicago",
    )

    assert payload["VendorRef"] == {"value": "68"}
    assert "HourlyRate" not in payload
    assert "BillableStatus" not in payload
    assert "CostRate" not in payload
    assert "FIELDCRAFT internal labor cost: $75.00/hour." in payload["Description"]


def test_1099_daily_payload_uses_hourly_rate_only_for_customer_bill_rate_config():
    service = _service()
    service._employee_sync_config = {
        const.KEY_EMPLOYEE_SYNC_COST_ENABLED: True,
        const.KEY_EMPLOYEE_SYNC_DEFAULT_COST_UNIT: const.EMPLOYEE_COST_UNIT_HOURLY,
        const.KEY_EMPLOYEE_SYNC_COST_UNIT_HOURS: const.EMPLOYEE_COST_UNIT_HOURS_DEFAULT,
        const.KEY_EMPLOYEE_SYNC_COST_QBO_FIELD: const.EMPLOYEE_COST_QBO_FIELD_BILL_RATE,
    }
    row = _row(
        resource_type=const.EMPLOYEE_TYPE_1099,
        external_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
        qbo_employee_id=None,
        qbo_vendor_id="68",
        qbo_project_id="59",
        qbo_parent_customer_id=None,
        resource_cost=75,
        resource_cost_unit="hourly",
        **{const.KEY_PROJECT_SYNC_TARGET: const.PROJECT_SYNC_TARGET_SUB_CUSTOMER},
    )

    payload = service._build_daily_payload(
        group_key=(247, date(2026, 8, 6), 2237),
        rows=[row],
        total_seconds=30 * 60,
        timezone_name="America/Chicago",
    )

    assert payload["HourlyRate"] == 75.0
    assert payload["BillableStatus"] == "Billable"
    assert "internal labor cost" not in payload["Description"]


def test_1099_multi_row_daily_payload_keeps_one_vendor_and_avoids_inaccurate_item_ref():
    service = _service()
    rows = [
        _row(
            timelog_id=101,
            resource_type=const.EMPLOYEE_TYPE_1099,
            external_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
            qbo_employee_id=None,
            qbo_vendor_id="68",
            qbo_project_id="59",
            qbo_parent_customer_id=None,
            qbo_service_id="6",
            **{const.KEY_PROJECT_SYNC_TARGET: const.PROJECT_SYNC_TARGET_SUB_CUSTOMER},
        ),
        _row(
            timelog_id=102,
            resource_type=const.EMPLOYEE_TYPE_1099,
            external_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
            qbo_employee_id=None,
            qbo_vendor_id="68",
            qbo_project_id="59",
            qbo_parent_customer_id=None,
            qbo_service_id="7",
            **{const.KEY_PROJECT_SYNC_TARGET: const.PROJECT_SYNC_TARGET_SUB_CUSTOMER},
        ),
    ]

    payload = service._build_daily_payload(
        group_key=(247, date(2026, 8, 6), 2237),
        rows=rows,
        total_seconds=90 * 60,
        timezone_name="America/Chicago",
    )

    assert payload["NameOf"] == "Vendor"
    assert payload["VendorRef"] == {"value": "68"}
    assert payload["Hours"] == 1
    assert payload["Minutes"] == 30
    assert "ItemRef" not in payload
    assert "FIELDCRAFT 101" in payload["Description"]
    assert "FIELDCRAFT 102" in payload["Description"]


def test_daily_worker_context_rejects_missing_1099_vendor_id():
    service = _service()
    row = _row(
        resource_type=const.EMPLOYEE_TYPE_1099,
        external_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
        qbo_employee_id=None,
        qbo_vendor_id=None,
    )

    with pytest.raises(ValueError, match="1099 contractor must resolve to one QuickBooks Vendor"):
        service._daily_worker_context([row])


def test_mixed_service_roles_stay_one_daily_entry_without_inaccurate_item_ref():
    service = _service()
    rows = [_row(qbo_service_id="6"), _row(timelog_id=102, qbo_service_id="7")]

    payload = service._build_daily_payload(
        group_key=(246, date(2026, 8, 6), 2237),
        rows=rows,
        total_seconds=60 * 60,
        timezone_name="America/Chicago",
    )

    assert "ItemRef" not in payload
    assert payload["Hours"] == 1
    assert payload["Minutes"] == 0


def test_w2_payroll_enrichment_adds_regular_pay_item():
    service = _service()

    class PayrollService:
        def resolve_regular_pay(self, **kwargs):
            assert kwargs["qbo_employee_id"] == "83"
            return {"id": "626270109", "name": "Regular Pay"}

    service.payroll_compensation_service = PayrollService()
    rows = [_row()]
    payload = service._build_daily_payload(
        group_key=(246, date(2026, 8, 6), 2237),
        rows=rows,
        total_seconds=30 * 60,
        timezone_name="America/Chicago",
    )

    compensation = service._add_w2_payroll_item(
        payload=payload,
        rows=rows,
        access_token="token",
    )

    assert compensation == {"id": "626270109", "name": "Regular Pay"}
    assert payload["PayrollItemRef"] == {"value": "626270109"}


def test_w2_payroll_enrichment_uses_employee_compensation_id_from_graphql(monkeypatch):
    service = _service()
    payroll_service = QuickBooksPayrollCompensationService()
    response = MagicMock()
    response.status_code = 200
    response.reason = "OK"
    response.url = payroll_service.GRAPHQL_ENDPOINT
    response.headers = {"Content-Type": "application/json"}
    response.text = "payload"
    response.json.return_value = {
        "data": {
            "payrollEmployeeCompensations": {
                "edges": [
                    {
                        "node": {
                            "id": "employee-compensation-626270109",
                            "active": True,
                            "employerCompensation": {
                                "id": "employer-compensation-regular-pay",
                                "name": "Regular Pay",
                                "type": {"value": "Regular Pay"},
                            },
                        }
                    }
                ]
            }
        }
    }
    module = __import__(payroll_service.__class__.__module__, fromlist=["qbo_api"])
    monkeypatch.setattr(module.qbo_api, "qbo_post", lambda *args, **kwargs: response)
    service.payroll_compensation_service = payroll_service
    rows = [_row()]
    payload = service._build_daily_payload(
        group_key=(246, date(2026, 8, 6), 2237),
        rows=rows,
        total_seconds=30 * 60,
        timezone_name="America/Chicago",
    )

    compensation = service._add_w2_payroll_item(
        payload=payload,
        rows=rows,
        access_token="token",
    )

    assert compensation["id"] == "employee-compensation-626270109"
    assert payload["PayrollItemRef"] == {"value": "employee-compensation-626270109"}
    assert payload["PayrollItemRef"]["value"] != "employer-compensation-regular-pay"


def test_daily_group_passes_w2_payroll_item_to_time_activity_create():
    service = _service()
    row = _row()
    _wire_daily_group_test(service, row)

    class PayrollService:
        def resolve_regular_pay(self, **kwargs):
            assert kwargs["qbo_employee_id"] == "83"
            return {"id": "626270109", "name": "Regular Pay"}

    captured = {}
    service.payroll_compensation_service = PayrollService()

    def create_time_activity(**kwargs):
        captured["payload"] = dict(kwargs["payload"])
        return {"Id": "9001", "SyncToken": "0"}

    service.create_time_activity = create_time_activity
    summary = _daily_summary()

    assert _process_daily_group(service, row, summary) is True
    assert captured["payload"]["PayrollItemRef"] == {"value": "626270109"}
    assert summary["createdTimeActivities"] == 1


def test_daily_group_marks_w2_payroll_resolution_failure_without_creating_time_activity():
    service = _service()
    row = _row()
    _wire_daily_group_test(service, row)

    class PayrollService:
        def resolve_regular_pay(self, **_kwargs):
            raise ValueError("QuickBooks employee 83 does not have an active 'Regular Pay' pay type.")

    failures = []

    class TimelogRepo:
        def mark_failed(self, **kwargs):
            failures.append(kwargs)

    service.payroll_compensation_service = PayrollService()
    service.timelog_repo = TimelogRepo()
    service.create_time_activity = lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("TimeActivity must not be created when Regular Pay resolution fails")
    )
    summary = _daily_summary()

    assert _process_daily_group(service, row, summary) is False
    assert summary["syncFailed"] == 1
    assert len(failures) == 1
    assert failures[0]["status"] == "SYNC_FAILED"
    assert "Regular Pay" in summary[const.KEY_FAILURES][0]["error"]


def test_1099_payroll_enrichment_does_not_call_payroll_api():
    service = _service()

    class PayrollService:
        def resolve_regular_pay(self, **kwargs):
            raise AssertionError("1099 should not resolve payroll compensation")

    service.payroll_compensation_service = PayrollService()
    row = _row(
        resource_type=const.EMPLOYEE_TYPE_1099,
        external_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
        qbo_employee_id=None,
        qbo_vendor_id="68",
        qbo_project_id="59",
        qbo_parent_customer_id=None,
        **{const.KEY_PROJECT_SYNC_TARGET: const.PROJECT_SYNC_TARGET_SUB_CUSTOMER},
    )
    payload = service._build_daily_payload(
        group_key=(247, date(2026, 8, 6), 2237),
        rows=[row],
        total_seconds=30 * 60,
        timezone_name="America/Chicago",
    )

    compensation = service._add_w2_payroll_item(
        payload=payload,
        rows=[row],
        access_token="token",
    )

    assert compensation is None
    assert "PayrollItemRef" not in payload


def test_w2_time_entry_setting_is_checked_for_payroll_diagnostic():
    service = _service()
    service.get_employee_time_entry_setting = lambda **kwargs: {
        "employeeId": kwargs["employee_id"],
        "displayName": "Mary Sales",
        "useTimeEntry": True,
        "useTimeEntryValue": "UseTimeEntry",
    }

    setting = service._w2_time_entry_setting(
        rows=[_row()],
        access_token="token",
        base_url="https://quickbooks.api.intuit.com",
        realm_id="realm",
    )

    assert setting["employeeId"] == "83"
    assert setting["useTimeEntry"] is True


def test_1099_does_not_check_employee_use_time_entry():
    service = _service()
    service.get_employee_time_entry_setting = lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("1099 should not query Employee.UseTimeEntry")
    )
    row = _row(
        resource_type=const.EMPLOYEE_TYPE_1099,
        external_entity_type=const.WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
        qbo_employee_id=None,
        qbo_vendor_id="68",
    )

    setting = service._w2_time_entry_setting(
        rows=[row],
        access_token="token",
        base_url="https://quickbooks.api.intuit.com",
        realm_id="realm",
    )

    assert setting is None
