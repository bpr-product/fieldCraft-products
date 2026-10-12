import json
from unittest.mock import MagicMock

from source.quickbooks.QuickBooksEmployeeImportService import QuickBooksEmployeeImportService
from source.Utils import constants as const


def _service():
    svc = QuickBooksEmployeeImportService.__new__(QuickBooksEmployeeImportService)
    svc.employee_repo = MagicMock()
    svc.employee_repo.STATUS_SYNCED = "SYNCED"
    svc.employee_repo.STATUS_PENDING_PUSH = "PENDING_PUSH"
    svc.employee_repo.STATUS_FAILED = "FAILED"
    svc.employee_repo.STATUS_IMPORT_FAILED = "IMPORT_FAILED"
    svc.employee_repo.STATUS_SYNC_FAILED = "SYNC_FAILED"
    svc.employee_repo.get_current_mapping_by_user_account_id.return_value = None
    svc.tenant_client = MagicMock()
    svc.tenant_client.user_email_exists_in_fieldcraft_or_ad.return_value = False
    svc._enrich_qbo_employee_for_cost_if_needed = MagicMock(side_effect=lambda employee, **_kwargs: employee)
    svc._build_employee_payload_from_qbo = MagicMock(return_value={
        const.KEY_EMAIL: "employee@test.com",
        "mobileNo": "1234567890",
        "resource": {},
        "personal_additional_info": {},
    })
    svc._get_qbo_last_updated_at = MagicMock(return_value=None)
    svc._is_qbo_employee_inactive = MagicMock(return_value=False)
    svc._is_payload_valid_for_tenant_api = MagicMock(return_value=(True, ""))
    svc._save_employee_through_tenant_api = MagicMock(return_value=501)
    return svc


def _summary():
    return {
        const.KEY_OK: True,
        "processed": 0,
        "linkedExistingInFieldCraft": 0,
        "created in FIELDCRAFT": 0,
        "updatedInFIELDCRAFT": 0,
        "skippedAlreadyCurrent": 0,
        "skippedLocalNewer": 0,
        "skippedDeleted": 0,
        "skippedMissingRequiredFields": 0,
        "failed": 0,
        const.KEY_FAILURES: [],
        const.KEY_RESULTS: [],
    }


def _qbo_employee():
    return {
        const.QBO_KEY_ID: "E-1",
        const.QBO_KEY_SYNC_TOKEN: "0",
        const.QBO_KEY_DISPLAY_NAME: "Test Employee",
    }


def test_import_employee_blocks_email_found_only_in_fieldcraft_or_ad():
    svc = _service()
    svc.employee_repo.get_by_external_id.return_value = None
    svc.employee_repo.get_employee_id_by_email.return_value = None
    svc.tenant_client.user_email_exists_in_fieldcraft_or_ad.return_value = True

    summary = _summary()
    svc._process_qbo_employee(
        summary=summary,
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_employee=_qbo_employee(),
        dry_run=False,
        tenant_api_token="TOKEN",
    )

    assert summary["failed"] == 1
    assert summary[const.KEY_RESULTS][0][const.KEY_ACTION] == "duplicateEmailImportFailed"
    assert summary[const.KEY_RESULTS][0]["emailExistsInFieldCraftOrAd"] is True
    reason = json.loads(summary[const.KEY_RESULTS][0][const.KEY_REASON])
    assert reason["code"] == svc.REASON_DUPLICATE_EMAIL_NOT_ALLOWED
    assert "FIELDCRAFT or Azure AD" in reason["message"]
    svc.tenant_client.user_email_exists_in_fieldcraft_or_ad.assert_called_once_with("TOKEN", "tn1", "employee@test.com")
    svc.employee_repo.upsert_import_failed_mapping.assert_called_once()
    assert svc.employee_repo.upsert_import_failed_mapping.call_args.kwargs["detach_employee"] is True
    svc._save_employee_through_tenant_api.assert_not_called()


def test_import_employee_validates_java_api_before_create_user():
    svc = _service()
    svc.employee_repo.get_by_external_id.return_value = None
    svc.employee_repo.get_employee_id_by_email.return_value = None
    svc.tenant_client.user_email_exists_in_fieldcraft_or_ad.return_value = False
    svc.employee_repo.get_employee_by_id.return_value = {"id": 501}
    svc.employee_repo.upsert_resource_for_employee.return_value = None

    summary = _summary()
    svc._process_qbo_employee(
        summary=summary,
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_employee=_qbo_employee(),
        dry_run=False,
        tenant_api_token="TOKEN",
    )

    assert summary["created in FIELDCRAFT"] == 1
    svc.tenant_client.user_email_exists_in_fieldcraft_or_ad.assert_called_once_with("TOKEN", "tn1", "employee@test.com")
    svc._save_employee_through_tenant_api.assert_called_once()
    svc.employee_repo.claim_external_employee_mapping.assert_called_once()
    assert svc.employee_repo.claim_external_employee_mapping.call_args.kwargs["allow_create"] is True


def test_import_reconciles_unbound_qbo_placeholder_with_failed_fieldcraft_export_mapping():
    svc = _service()
    svc.employee_repo.get_by_external_id.return_value = {
        "id": 99,
        "user_account_id": None,
        "external_employee_id": "E-1",
        "sync_status": "IMPORT_FAILED",
    }
    svc.employee_repo.get_employee_id_by_email.return_value = 501
    svc.employee_repo.get_current_mapping_by_user_account_id.return_value = {
        "id": 10,
        "user_account_id": 501,
        "external_employee_id": None,
        "external_vendor_id": None,
        "external_entity_type": const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE,
        "external_account_id": "RID",
        "sync_status": "FAILED",
    }

    summary = _summary()
    svc._process_qbo_employee(
        summary=summary,
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_employee=_qbo_employee(),
        dry_run=False,
        tenant_api_token="TOKEN",
    )

    assert summary["linkedExistingInFieldCraft"] == 1
    assert summary["failed"] == 0
    kwargs = svc.employee_repo.claim_external_employee_mapping.call_args.kwargs
    assert kwargs["mapping_id"] == 10
    assert kwargs["user_account_id"] == 501
    assert kwargs["external_employee_id"] == "E-1"
    svc.employee_repo.upsert_import_failed_mapping.assert_not_called()
    svc._save_employee_through_tenant_api.assert_not_called()


def test_import_recovers_completely_missing_w2_mapping_from_same_tenant_email():
    svc = _service()
    svc.employee_repo.get_by_external_id.return_value = None
    svc.employee_repo.get_employee_id_by_email.return_value = 501
    svc.employee_repo.get_current_mapping_by_user_account_id.return_value = None
    svc.employee_repo.get_employee_by_id.return_value = {
        "id": 501,
        const.KEY_EMAIL: "employee@test.com",
        "resource": {"type": "W-2"},
    }
    svc.employee_repo.claim_external_employee_mapping.return_value = {
        "id": 55,
        "user_account_id": 501,
        "external_employee_id": "E-1",
        "external_account_id": "RID",
        "sync_status": "SYNCED",
    }
    svc.employee_repo.sync_projection_changed.return_value = False

    summary = _summary()
    svc._process_qbo_employee(
        summary=summary,
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_employee=_qbo_employee(),
        dry_run=False,
        tenant_api_token="TOKEN",
    )

    assert summary["linkedExistingInFieldCraft"] == 1
    assert summary["skippedAlreadyCurrent"] == 1
    assert summary["failed"] == 0
    claim = svc.employee_repo.claim_external_employee_mapping.call_args.kwargs
    assert claim["user_account_id"] == 501
    assert claim["external_employee_id"] == "E-1"
    assert claim["mapping_id"] is None
    assert claim["allow_create"] is True
    recovery_reason = json.loads(claim["reason"])
    assert recovery_reason["code"] == "EMPLOYEE_MISSING_MAPPING_RECOVERED"
    assert recovery_reason["message"] == (
        "This employee was already in FIELDCRAFT, so it was linked to the matching QuickBooks employee."
    )
    svc.tenant_client.user_email_exists_in_fieldcraft_or_ad.assert_not_called()
    svc._save_employee_through_tenant_api.assert_not_called()


def test_import_does_not_replace_a_distinct_synced_employee_mapping():
    svc = _service()
    svc.employee_repo.get_by_external_id.return_value = {
        "id": 99,
        "user_account_id": None,
        "external_employee_id": "E-1",
        "sync_status": "IMPORT_FAILED",
    }
    svc.employee_repo.get_employee_id_by_email.return_value = 501
    svc.employee_repo.get_current_mapping_by_user_account_id.return_value = {
        "id": 10,
        "user_account_id": 501,
        "external_employee_id": "E-OLD",
        "external_vendor_id": None,
        "external_entity_type": const.WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE,
        "external_account_id": "RID",
        "sync_status": "SYNCED",
    }

    summary = _summary()
    svc._process_qbo_employee(
        summary=summary,
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_employee=_qbo_employee(),
        dry_run=False,
        tenant_api_token="TOKEN",
    )

    assert summary["failed"] == 1
    assert summary[const.KEY_RESULTS][0][const.KEY_ACTION] == "duplicateEmailImportFailed"
    svc.employee_repo.claim_external_employee_mapping.assert_not_called()


def test_1099_vendor_cannot_take_over_w2_employee_with_same_email():
    svc = _service()
    svc.employee_repo.get_current_mapping_by_user_account_id.return_value = None

    try:
        svc._validate_existing_1099_owner_for_vendor_link(
            tenant_id="tn1",
            service_id="6",
            realm_id="RID",
            user_account_id=501,
            qbo_vendor_id="V-1",
            employee={"id": 501, "resource": {"type": const.EMPLOYEE_TYPE_W2}},
        )
    except ValueError as exc:
        assert "W-2" in str(exc)
        assert "1099" in str(exc)
    else:
        raise AssertionError("Expected W-2/1099 mapping conflict")


def test_build_employee_payload_imports_cost_rate_hire_date_and_birthdate():
    svc = QuickBooksEmployeeImportService.__new__(QuickBooksEmployeeImportService)

    payload = svc._build_employee_payload_from_qbo(
        {
            "Id": "E-88",
            "DisplayName": "Ava Stone",
            "GivenName": "Ava",
            "FamilyName": "Stone",
            "PrimaryEmailAddr": {"Address": "ava.stone@example.com"},
            "PrimaryPhone": {"FreeFormNumber": "5550101000"},
            "BirthDate": "1992-04-12",
            "HiredDate": "2024-01-15",
            "CostRate": 38.75,
        },
        employee_type=const.EMPLOYEE_TYPE_W2,
        cost_sync_enabled=True,
        cost_qbo_field=const.EMPLOYEE_COST_QBO_FIELD_COST_RATE,
    )

    assert payload["birthdate"].startswith("1992-04-12")
    assert payload["hiredDate"].startswith("2024-01-15")
    assert payload["resource"]["cost"] == "38.75"
    assert payload["resource"]["qbo_field"] == "CostRate"
    assert payload["resource"]["qbo_cost_rate"] == "38.75"


def test_build_employee_payload_falls_back_to_existing_bill_rate_data():
    svc = QuickBooksEmployeeImportService.__new__(QuickBooksEmployeeImportService)

    payload = svc._build_employee_payload_from_qbo(
        {
            "Id": "E-89",
            "DisplayName": "Legacy Employee",
            "GivenName": "Legacy",
            "PrimaryEmailAddr": {"Address": "legacy@example.com"},
            "PrimaryPhone": {"FreeFormNumber": "5550101001"},
            "BillRate": 52.25,
            "BillableTime": True,
        },
        cost_qbo_field=const.EMPLOYEE_COST_QBO_FIELD_COST_RATE,
    )

    assert payload["resource"]["cost"] == "52.25"
    assert payload["resource"]["qbo_field"] == "BillRate"
    assert payload["resource"]["qbo_bill_rate"] == "52.25"
    assert payload["resource"]["qbo_billable_time"] is True


def test_configured_bill_rate_is_preferred_when_both_rates_exist():
    svc = QuickBooksEmployeeImportService.__new__(QuickBooksEmployeeImportService)

    cost, field = svc.get_qbo_employee_cost_rate(
        {"CostRate": 31.0, "BillRate": 48.0},
        preferred_field=const.EMPLOYEE_COST_QBO_FIELD_BILL_RATE,
    )

    assert str(cost) == "48.00"
    assert field == "BillRate"


def test_work_start_date_uses_employee_sync_offset():
    svc = QuickBooksEmployeeImportService.__new__(QuickBooksEmployeeImportService)

    assert svc._work_start_date_from_hire(
        "2024-01-15",
        days_after_hire=2,
    ).startswith("2024-01-17")


def test_missing_mapping_recovery_only_claims_for_w2_employee_owner():
    svc = _service()
    svc.employee_repo.get_by_external_id.return_value = None
    svc.employee_repo.get_employee_id_by_email.return_value = 501
    svc.employee_repo.get_current_mapping_by_user_account_id.return_value = None
    svc.employee_repo.get_employee_by_id.return_value = {
        "id": 501,
        const.KEY_EMAIL: "employee@test.com",
        "resource": {"type": const.EMPLOYEE_TYPE_1099},
    }

    summary = _summary()
    svc._process_qbo_employee(
        summary=summary,
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_employee=_qbo_employee(),
        dry_run=False,
        tenant_api_token="TOKEN",
    )

    svc.employee_repo.claim_external_employee_mapping.assert_not_called()
    assert summary["linkedExistingInFieldCraft"] == 0
    assert summary["failed"] == 1
    assert summary[const.KEY_RESULTS][0][const.KEY_ACTION] == "duplicateEmailImportFailed"


def test_missing_mapping_recovery_is_skipped_during_dry_run():
    svc = _service()
    svc.employee_repo.get_by_external_id.return_value = None
    svc.employee_repo.get_employee_id_by_email.return_value = 501
    svc.employee_repo.get_current_mapping_by_user_account_id.return_value = None
    svc.employee_repo.get_employee_by_id.return_value = {
        "id": 501,
        const.KEY_EMAIL: "employee@test.com",
        "resource": {"type": const.EMPLOYEE_TYPE_W2},
    }

    summary = _summary()
    svc._process_qbo_employee(
        summary=summary,
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_employee=_qbo_employee(),
        dry_run=True,
        tenant_api_token="TOKEN",
    )

    svc.employee_repo.claim_external_employee_mapping.assert_not_called()
    assert summary["linkedExistingInFieldCraft"] == 0
    assert summary["failed"] == 1
    assert summary[const.KEY_RESULTS][0][const.KEY_ACTION] == "duplicateEmailImportFailed"


def test_missing_mapping_recovery_value_error_preserves_duplicate_owner_state():
    svc = _service()
    svc.employee_repo.get_by_external_id.return_value = {
        "id": 99,
        "user_account_id": 501,
        "external_employee_id": "E-1",
        "sync_status": "IMPORT_FAILED",
    }
    svc.employee_repo.get_employee_id_by_email.return_value = 501
    svc.employee_repo.has_current_mapping_for_user_with_different_external_id.return_value = True
    svc.employee_repo.get_current_mapping_by_user_account_id.return_value = None
    svc.employee_repo.get_employee_by_id.return_value = {
        "id": 501,
        const.KEY_EMAIL: "employee@test.com",
        "resource": {"type": const.EMPLOYEE_TYPE_W2},
    }
    svc.employee_repo.claim_external_employee_mapping.side_effect = ValueError("mapping ownership conflict")

    summary = _summary()
    svc._process_qbo_employee(
        summary=summary,
        tenant_id="tn1",
        service_id="6",
        realm_id="RID",
        qbo_employee=_qbo_employee(),
        dry_run=False,
        tenant_api_token="TOKEN",
    )

    svc.employee_repo.claim_external_employee_mapping.assert_called_once()
    assert summary["linkedExistingInFieldCraft"] == 0
    assert summary["failed"] == 1
    assert summary[const.KEY_RESULTS][0][const.KEY_ACTION] == "duplicateEmailImportFailed"
