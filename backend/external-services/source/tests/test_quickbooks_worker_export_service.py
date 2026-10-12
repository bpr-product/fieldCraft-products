from unittest.mock import MagicMock

from source.Utils import constants as const
from source.quickbooks.QuickBooksWorkerExportService import QuickBooksWorkerExportService


def _summary(*, ok=True, requested=0, created=0, updated=0, mapped=0, failed=0, last_mapping_id=None, message=None):
    value = {
        const.KEY_OK: ok,
        const.KEY_MESSAGE: message or ("ok" if ok else "failed"),
        "requested": requested,
        "createdInQbo": created,
        "updatedInQbo": updated,
        "mapped from QBO": mapped,
        "skippedAlreadyMapped": 0,
        "skippedQboNewer": 0,
        "failed": failed,
        const.KEY_FAILURES: ([{const.KEY_ERROR: "record failed"}] if failed else []),
        const.KEY_RESULTS: [],
    }
    if last_mapping_id is not None:
        value["lastMappingId"] = last_mapping_id
    return value


def test_export_workers_treats_missing_exporter_result_as_failure():
    svc = QuickBooksWorkerExportService()
    svc.employee_exporter.export_employees = MagicMock(return_value=None)
    svc.contractor_exporter.export_contractors_as_vendors = MagicMock(return_value=_summary())

    result = svc.export_workers(tenant_id="tn1", service_id="6")

    assert result[const.KEY_OK] is False
    assert result["failed"] == 1
    assert "did not return a valid result" in result[const.KEY_MESSAGE]


def test_export_workers_does_not_report_success_when_all_records_fail():
    svc = QuickBooksWorkerExportService()
    svc.employee_exporter.export_employees = MagicMock(
        return_value=_summary(ok=False, requested=1, failed=1, message="No W2 employees were exported.")
    )
    svc.contractor_exporter.export_contractors_as_vendors = MagicMock(
        return_value=_summary(ok=False, requested=1, failed=1, message="No 1099 contractors were exported.")
    )

    result = svc.export_workers(tenant_id="tn1", service_id="6")

    assert result[const.KEY_OK] is False
    assert result["failed"] == 2
    assert result["partialSuccess"] is False
    assert "No W2 employees were exported" in result[const.KEY_MESSAGE]
    assert "No 1099 contractors were exported" in result[const.KEY_MESSAGE]


def test_export_all_workers_stops_when_pagination_does_not_advance():
    svc = QuickBooksWorkerExportService()
    svc.employee_exporter.export_employees = MagicMock(
        side_effect=[
            _summary(requested=2, created=2, last_mapping_id=10),
            _summary(requested=2, created=2, last_mapping_id=10),
        ]
    )
    svc.contractor_exporter.export_contractors_as_vendors = MagicMock(return_value=_summary())

    result = svc.export_all_workers(tenant_id="tn1", service_id="6", batch_size=2)

    assert result[const.KEY_OK] is False
    assert result["createdInQbo"] == 4
    assert result["failed"] == 1
    assert "pagination did not advance" in result[const.KEY_MESSAGE]
    assert svc.employee_exporter.export_employees.call_count == 2


def test_export_all_workers_drains_w2_and_1099_queues_independently():
    svc = QuickBooksWorkerExportService()
    svc.employee_exporter.export_employees = MagicMock(
        side_effect=[
            _summary(requested=2, created=2, last_mapping_id=20),
            _summary(requested=1, created=1, last_mapping_id=30),
        ]
    )
    svc.contractor_exporter.export_contractors_as_vendors = MagicMock(
        side_effect=[
            _summary(requested=2, created=2, last_mapping_id=200),
            _summary(requested=0),
        ]
    )

    result = svc.export_all_workers(tenant_id="tn1", service_id="6", batch_size=2)

    assert result[const.KEY_OK] is True
    assert result["requested"] == 5
    assert result["createdInQbo"] == 5
    assert svc.employee_exporter.export_employees.call_args_list[1].kwargs["after_mapping_id"] == 20
    assert svc.contractor_exporter.export_contractors_as_vendors.call_args_list[1].kwargs["after_mapping_id"] == 200
