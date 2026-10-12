from unittest.mock import MagicMock

from source.Utils import constants as const
from source.Utils.QuickBooksErrorHandler import QuickBooksAPIError
from source.quickbooks.QuickBooksContractorVendorExportService import QuickBooksContractorVendorExportService


def _employee(**overrides):
    data = {
        "id": 42,
        "object_id": "65ef4efe-84a0-48ad-8e16-7f0a1e5275db",
        "firstname": "Max",
        "middlename": "",
        "lastname": "Mayfield",
        "email": "max@example.com",
        "mobile_no": "(980) 012-9831",
        "active": True,
        "resource": {"type": const.EMPLOYEE_TYPE_1099},
        "personal_additional_info": {
            "address": "409 Cherry Lane",
            "city": "Hawkins",
            "state": "IN",
            "zip_code": "46001",
            "country": "USA",
        },
    }
    data.update(overrides)
    return data


def test_contractor_display_name_uses_name_phone_without_1099_suffix():
    svc = QuickBooksContractorVendorExportService()

    assert svc.build_contractor_display_name(_employee()) == "Max Mayfield-9800129831"


def test_contractor_vendor_payload_marks_vendor_1099_and_active():
    svc = QuickBooksContractorVendorExportService()
    vendor_projection = svc._contractor_vendor_projection(_employee())

    payload = svc.build_qbo_contractor_vendor_payload(vendor_projection, for_create=True)

    assert payload[const.QBO_KEY_DISPLAY_NAME] == "Max Mayfield-9800129831"
    assert payload["Vendor1099"] is True
    assert payload["Active"] is True
    assert payload["PrimaryEmailAddr"] == {"Address": "max@example.com"}
    assert payload["PrimaryPhone"] == {"FreeFormNumber": "(980) 012-9831"}
    assert payload["BillAddr"]["Line1"] == "409 Cherry Lane"


def test_process_candidate_creates_qbo_vendor_and_marks_worker_mapping_synced():
    svc = QuickBooksContractorVendorExportService()
    svc.employee_repo = MagicMock()
    svc.employee_repo.STATUS_PENDING_PUSH = "PENDING_PUSH"
    svc.employee_repo.STATUS_FAILED = "FAILED"
    svc.employee_repo.STATUS_SYNC_FAILED = "SYNC_FAILED"
    svc.employee_repo.get_by_external_vendor_id.return_value = None
    svc.vendor_map_repo = MagicMock()
    svc.vendor_map_repo.get_by_external_id.return_value = None
    svc.find_vendor_candidates_in_qbo = MagicMock(return_value=[])
    svc.create_vendor_in_qbo = MagicMock(
        return_value={
            const.QBO_KEY_ID: "VEN-42",
            const.QBO_KEY_SYNC_TOKEN: "0",
            const.QBO_KEY_DISPLAY_NAME: "Max Mayfield-9800129831",
        }
    )

    summary = {
        "skippedAlreadyMapped": 0,
        "skippedQboNewer": 0,
        "mapped from QBO": 0,
        "createdInQbo": 0,
        "updatedInQbo": 0,
        "failed": 0,
        const.KEY_FAILURES: [],
        const.KEY_RESULTS: [],
    }

    svc._process_candidate(
        item={"employee": _employee(), "mapping": {"id": 7, "sync_status": "PENDING_PUSH"}},
        tenant_id="tn1",
        service_id="6",
        realm_id="realm-1",
        base_url="https://sandbox-quickbooks.api.intuit.com",
        access_token="token",
        summary=summary,
    )

    assert summary["createdInQbo"] == 1
    svc.employee_repo.claim_external_vendor_mapping.assert_called_once()
    kwargs = svc.employee_repo.claim_external_vendor_mapping.call_args.kwargs
    assert kwargs["mapping_id"] == 7
    assert kwargs["external_vendor_id"] == "VEN-42"
    assert kwargs["external_display_name"] == "Max Mayfield-9800129831"


def test_process_candidate_retries_without_optional_qbo_vendor_fields_when_unsupported():
    svc = QuickBooksContractorVendorExportService()
    svc.employee_repo = MagicMock()
    svc.employee_repo.STATUS_PENDING_PUSH = "PENDING_PUSH"
    svc.employee_repo.STATUS_FAILED = "FAILED"
    svc.employee_repo.STATUS_SYNC_FAILED = "SYNC_FAILED"
    svc.employee_repo.get_by_external_vendor_id.return_value = None
    svc.vendor_map_repo = MagicMock()
    svc.vendor_map_repo.get_by_external_id.return_value = None
    svc.find_vendor_candidates_in_qbo = MagicMock(return_value=[])
    svc.create_vendor_in_qbo = MagicMock(
        side_effect=[
            QuickBooksAPIError("Request has invalid or unsupported property", status_code=400, intuit_tid="tid-1"),
            {
                const.QBO_KEY_ID: "VEN-42",
                const.QBO_KEY_SYNC_TOKEN: "0",
                const.QBO_KEY_DISPLAY_NAME: "Max Mayfield-9800129831",
            },
        ]
    )

    summary = {
        "skippedAlreadyMapped": 0,
        "skippedQboNewer": 0,
        "mapped from QBO": 0,
        "createdInQbo": 0,
        "updatedInQbo": 0,
        "failed": 0,
        const.KEY_FAILURES: [],
        const.KEY_RESULTS: [],
    }

    svc._process_candidate(
        item={"employee": _employee(), "mapping": {"id": 7, "sync_status": "PENDING_PUSH"}},
        tenant_id="tn1",
        service_id="6",
        realm_id="realm-1",
        base_url="https://sandbox-quickbooks.api.intuit.com",
        access_token="token",
        summary=summary,
    )

    assert summary["createdInQbo"] == 1
    assert svc.create_vendor_in_qbo.call_count == 2
    first_payload = svc.create_vendor_in_qbo.call_args_list[0].kwargs["payload"]
    retry_payload = svc.create_vendor_in_qbo.call_args_list[1].kwargs["payload"]
    assert first_payload["Active"] is True
    assert first_payload["Vendor1099"] is True
    assert "Notes" not in retry_payload
    assert retry_payload["Active"] is True
    assert retry_payload["Vendor1099"] is True
    reason = svc.employee_repo.claim_external_vendor_mapping.call_args.kwargs["reason"]
    assert "Notes" in reason


def test_process_candidate_retries_to_minimal_1099_payload_when_all_optional_fields_are_rejected():
    svc = QuickBooksContractorVendorExportService()
    svc.employee_repo = MagicMock()
    svc.employee_repo.STATUS_PENDING_PUSH = "PENDING_PUSH"
    svc.employee_repo.STATUS_FAILED = "FAILED"
    svc.employee_repo.STATUS_SYNC_FAILED = "SYNC_FAILED"
    svc.employee_repo.get_by_external_vendor_id.return_value = None
    svc.vendor_map_repo = MagicMock()
    svc.vendor_map_repo.get_by_external_id.return_value = None
    svc.find_vendor_candidates_in_qbo = MagicMock(return_value=[])
    unsupported = QuickBooksAPIError("Request has invalid or unsupported property", status_code=400, intuit_tid="tid-1")
    svc.create_vendor_in_qbo = MagicMock(
        side_effect=[
            unsupported,
            unsupported,
            unsupported,
            unsupported,
            unsupported,
            {
                const.QBO_KEY_ID: "VEN-42",
                const.QBO_KEY_SYNC_TOKEN: "0",
                const.QBO_KEY_DISPLAY_NAME: "Max Mayfield-9800129831",
                "Vendor1099": True,
            },
        ]
    )

    summary = {
        "skippedAlreadyMapped": 0,
        "skippedQboNewer": 0,
        "mapped from QBO": 0,
        "createdInQbo": 0,
        "updatedInQbo": 0,
        "failed": 0,
        const.KEY_FAILURES: [],
        const.KEY_RESULTS: [],
    }

    svc._process_candidate(
        item={"employee": _employee(), "mapping": {"id": 7, "sync_status": "PENDING_PUSH"}},
        tenant_id="tn1",
        service_id="6",
        realm_id="realm-1",
        base_url="https://sandbox-quickbooks.api.intuit.com",
        access_token="token",
        summary=summary,
    )

    assert summary["createdInQbo"] == 1
    final_payload = svc.create_vendor_in_qbo.call_args_list[-1].kwargs["payload"]
    assert final_payload == {const.QBO_KEY_DISPLAY_NAME: "Max Mayfield-9800129831", "Vendor1099": True}
    reason = svc.employee_repo.claim_external_vendor_mapping.call_args.kwargs["reason"]
    assert "PrimaryEmailAddr" in reason
    assert "BillAddr" in reason
    assert "Vendor1099" not in reason


def test_contractor_vendor_retry_variants_never_drop_vendor_1099():
    svc = QuickBooksContractorVendorExportService()
    payload = svc.build_qbo_contractor_vendor_payload(svc._contractor_vendor_projection(_employee()), for_create=True)

    variants = svc._contractor_vendor_payload_retry_variants(payload)

    assert variants
    for retry_payload, omitted_fields in variants:
        assert retry_payload["Vendor1099"] is True
        assert "Vendor1099" not in omitted_fields
    assert variants[-1][0] == {const.QBO_KEY_DISPLAY_NAME: "Max Mayfield-9800129831", "Vendor1099": True}


def test_ambiguous_contractor_vendor_create_is_recovered_by_exact_display_name():
    svc = QuickBooksContractorVendorExportService.__new__(QuickBooksContractorVendorExportService)
    svc.AMBIGUOUS_CREATE_LOOKUP_ATTEMPTS = 2
    svc.AMBIGUOUS_CREATE_LOOKUP_DELAY_SECONDS = 0
    svc._query_vendors_by_field = MagicMock(return_value=[
        {const.QBO_KEY_ID: "VEN-42", const.QBO_KEY_DISPLAY_NAME: "Max Mayfield-9800129831"}
    ])

    recovered = svc._recover_vendor_after_ambiguous_create(
        access_token="token",
        base_url="https://example.test",
        realm_id="realm",
        display_name="Max Mayfield-9800129831",
    )

    assert recovered[const.QBO_KEY_ID] == "VEN-42"
    assert svc._query_vendors_by_field.call_args.kwargs["field_value"] == "Max Mayfield-9800129831"


def test_contractor_timeout_recovers_vendor_without_second_post():
    import requests

    svc = QuickBooksContractorVendorExportService()
    svc.employee_repo = MagicMock()
    svc.employee_repo.STATUS_PENDING_PUSH = "PENDING_PUSH"
    svc.employee_repo.STATUS_FAILED = "FAILED"
    svc.employee_repo.STATUS_SYNC_FAILED = "SYNC_FAILED"
    svc.employee_repo.get_by_external_vendor_id.return_value = None
    svc.vendor_map_repo = MagicMock()
    svc.vendor_map_repo.get_by_external_id.return_value = None
    svc.find_vendor_candidates_in_qbo = MagicMock(return_value=[])
    svc.create_vendor_in_qbo = MagicMock(side_effect=requests.exceptions.ReadTimeout("timeout"))
    svc.AMBIGUOUS_CREATE_LOOKUP_ATTEMPTS = 1
    svc.AMBIGUOUS_CREATE_LOOKUP_DELAY_SECONDS = 0
    svc._query_vendors_by_field = MagicMock(return_value=[{
        const.QBO_KEY_ID: "VEN-42",
        const.QBO_KEY_SYNC_TOKEN: "0",
        const.QBO_KEY_DISPLAY_NAME: "Max Mayfield-9800129831",
    }])

    summary = {
        "skippedAlreadyMapped": 0,
        "skippedQboNewer": 0,
        "mapped from QBO": 0,
        "createdInQbo": 0,
        "updatedInQbo": 0,
        "failed": 0,
        const.KEY_FAILURES: [],
        const.KEY_RESULTS: [],
    }

    svc._process_candidate(
        item={"employee": _employee(), "mapping": {"id": 7, "sync_status": "PENDING_PUSH"}},
        tenant_id="tn1",
        service_id="6",
        realm_id="realm-1",
        base_url="https://sandbox-quickbooks.api.intuit.com",
        access_token="token",
        summary=summary,
    )

    assert summary["createdInQbo"] == 1
    assert summary["failed"] == 0
    svc.create_vendor_in_qbo.assert_called_once()
    kwargs = svc.employee_repo.claim_external_vendor_mapping.call_args.kwargs
    assert kwargs["external_vendor_id"] == "VEN-42"
