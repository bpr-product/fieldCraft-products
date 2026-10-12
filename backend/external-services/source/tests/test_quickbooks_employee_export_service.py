from source.quickbooks.QuickBooksEmployeeExportService import QuickBooksEmployeeExportService


def _service():
    return QuickBooksEmployeeExportService.__new__(QuickBooksEmployeeExportService)


def test_build_display_name_uses_employee_name_and_phone_number():
    svc = _service()
    employee = {
        "id": 53,
        "firstname": "Max",
        "middlename": "",
        "lastname": "WGC",
        "mobile_no": "(980) 012-9831",
        "object_id": "8a77df28-02d2-44a6-bde5-dc091f07d99d",
    }

    assert svc.build_display_name(employee) == "Max WGC-9800129831"


def test_build_display_name_falls_back_to_employee_name_when_phone_is_missing():
    svc = _service()
    employee = {
        "id": 53,
        "firstname": "Max",
        "middlename": "",
        "lastname": "WGC",
        "object_id": "8a77df28-02d2-44a6-bde5-dc091f07d99d",
    }

    assert svc.build_display_name(employee) == "Max WGC"


def test_build_qbo_employee_payload_maps_core_fields():
    svc = _service()
    employee = {
        "id": 53,
        "active": True,
        "deleted": False,
        "invalid": False,
        "firstname": "Max",
        "middlename": "K",
        "lastname": "WGC",
        "email": "max@example.test",
        "mobile_no": "9800129831",
        "object_id": "8a77df28-02d2-44a6-bde5-dc091f07d99d",
    }

    payload = svc.build_qbo_employee_payload(employee)

    assert payload["DisplayName"] == "Max K WGC-9800129831"
    assert payload["GivenName"] == "Max"
    assert payload["MiddleName"] == "K"
    assert payload["FamilyName"] == "WGC"
    assert payload["PrimaryEmailAddr"] == {"Address": "max@example.test"}
    assert payload["PrimaryPhone"] == {"FreeFormNumber": "9800129831"}
    assert payload["Active"] is True


def test_build_qbo_employee_payload_requires_first_name():
    svc = _service()
    employee = {
        "id": 53,
        "active": True,
        "firstname": "",
        "lastname": "WGC",
    }

    try:
        svc.build_qbo_employee_payload(employee)
    except Exception as exc:
        assert "first name is missing" in str(exc)
    else:
        raise AssertionError("Expected first-name validation error")


def test_export_update_sends_display_name_for_existing_employee(monkeypatch):
    from datetime import datetime, timezone

    import source.quickbooks.QuickBooksEmployeeExportService as employee_export_module

    svc = _service()
    captured = {}

    class FakeQboRepo:
        def getAdditionalInfo(self, tenant_id, service_id):
            return {
                "refreshToken": "refresh-token",
                "realmId": "realm-1",
                "env": "sandbox",
                "status": "connected",
            }

    class FakeQboService:
        token_url = "https://example.test/token"

        def get_quickbooks_config(self, tenant_id, service_id):
            return {"ClientId": "client-id", "ClientSecret": "client-secret"}

    class FakeEmployeeRepo:
        STATUS_PENDING_PUSH = "PENDING_PUSH"
        STATUS_FAILED = "FAILED"
        STATUS_SYNC_FAILED = "SYNC_FAILED"

        def get_export_candidates(self, tenant_id, service_id, limit, offset):
            return [
                {
                    "employee": {
                        "id": 53,
                        "active": True,
                        "deleted": False,
                        "invalid": False,
                        "firstname": "Updated",
                        "middlename": "",
                        "lastname": "Employee",
                        "email": "updated.employee@example.com",
                        "mobile_no": "9800129831",
                        "object_id": "8a77df28-02d2-44a6-bde5-dc091f07d99d",
                        "updated_at": datetime(2024, 2, 1, tzinfo=timezone.utc),
                    },
                    "mapping": {
                        "id": 10,
                        "external_employee_id": "400000091",
                        "sync_status": "PENDING_PUSH",
                        "sync_token": "4",
                        "external_display_name": "Old Employee-employee-8A77DF",
                    },
                }
            ]

        def get_employee_latest_local_timestamp(self, employee):
            return employee.get("updated_at")

        def mark_mapping_synced(self, **kwargs):
            captured["mark_mapping_synced"] = kwargs

    def fake_update_employee_in_qbo(**kwargs):
        captured["payload"] = kwargs["payload"]
        return {
            "Id": kwargs["employee_id"],
            "SyncToken": "5",
            "DisplayName": kwargs["payload"].get("DisplayName"),
        }

    svc.qbo_repo = FakeQboRepo()
    svc.qbo_service = FakeQboService()
    svc.employee_repo = FakeEmployeeRepo()
    svc.refresh_access_token = lambda **kwargs: "access-token"
    svc.get_qbo_api_base = lambda env: "https://example.test/qbo"
    svc.get_employee_in_qbo = lambda **kwargs: {
        "Id": "400000091",
        "SyncToken": "4",
        "DisplayName": "Old Employee-employee-8A77DF",
        "MetaData": {"LastUpdatedTime": "2024-01-01T00:00:00+00:00"},
    }
    svc.update_employee_in_qbo = fake_update_employee_in_qbo
    monkeypatch.setattr(employee_export_module, "safe_audit_log", lambda **kwargs: None)

    summary = svc.export_employees(tenant_id="tenant-1", service_id="1")

    assert summary["updatedInQbo"] == 1
    assert captured["payload"]["DisplayName"] == "Updated Employee-9800129831"
    assert captured["mark_mapping_synced"]["external_display_name"] == "Updated Employee-9800129831"


def test_find_employee_candidates_checks_plain_name_for_legacy_qbo_records():
    svc = _service()
    calls = []

    def fake_query(access_token, base_url, realm_id, field_name, field_value):
        calls.append((field_name, field_value))
        if field_name == "DisplayName" and field_value == "William Turner":
            return [{"Id": "84", "DisplayName": "William Turner"}]
        return []

    svc._query_employees = fake_query
    employee = {
        "firstname": "William",
        "lastname": "Turner",
        "email": "williamturner@poc.com",
        "mobile_no": "9800129831",
    }

    candidates = svc.find_employee_candidates_in_qbo(
        access_token="token",
        base_url="https://example.test",
        realm_id="realm",
        employee=employee,
    )

    assert [item["Id"] for item in candidates] == ["84"]
    assert ("DisplayName", "William Turner-9800129831") in calls
    assert ("DisplayName", "William Turner") in calls


def test_employee_query_fails_closed_to_prevent_duplicate_create(monkeypatch):
    import requests
    import source.quickbooks.QuickBooksEmployeeExportService as employee_export_module

    svc = _service()
    monkeypatch.setattr(
        employee_export_module.qbo_err,
        "qbo_get",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.exceptions.ReadTimeout("timeout")),
    )

    try:
        svc._query_employees("token", "https://example.test", "realm", "DisplayName", "Test Employee")
    except RuntimeError as exc:
        assert "No employee was created" in str(exc)
    else:
        raise AssertionError("Expected employee lookup to fail closed")


def test_ambiguous_employee_create_is_recovered_without_retrying_post():
    svc = _service()
    svc.AMBIGUOUS_CREATE_LOOKUP_ATTEMPTS = 2
    svc.AMBIGUOUS_CREATE_LOOKUP_DELAY_SECONDS = 0
    svc.find_employee_candidates_in_qbo = lambda **kwargs: [
        {"Id": "E-84", "SyncToken": "0", "DisplayName": "William Turner"}
    ]

    recovered = svc._recover_employee_after_ambiguous_create(
        access_token="token",
        base_url="https://example.test",
        realm_id="realm",
        employee={"firstname": "William", "lastname": "Turner"},
    )

    assert recovered["Id"] == "E-84"


def test_export_timeout_recovers_created_employee_without_second_post(monkeypatch):
    from unittest.mock import MagicMock

    import requests
    import source.quickbooks.QuickBooksEmployeeExportService as employee_export_module

    svc = _service()

    class FakeQboRepo:
        def getAdditionalInfo(self, tenant_id, service_id):
            return {
                "refreshToken": "refresh-token",
                "realmId": "realm-1",
                "env": "sandbox",
                "status": "connected",
            }

    class FakeQboService:
        token_url = "https://example.test/token"

        def get_quickbooks_config(self, tenant_id, service_id):
            return {"ClientId": "client-id", "ClientSecret": "client-secret"}

    class FakeEmployeeRepo:
        STATUS_PENDING_PUSH = "PENDING_PUSH"
        STATUS_FAILED = "FAILED"
        STATUS_SYNC_FAILED = "SYNC_FAILED"

        def __init__(self):
            self.claim_external_employee_mapping = MagicMock()
            self.mark_mapping_failed = MagicMock()

        def get_export_candidates(self, tenant_id, service_id, limit, offset):
            return [{
                "employee": {
                    "id": 204,
                    "active": True,
                    "deleted": False,
                    "invalid": False,
                    "firstname": "William",
                    "lastname": "Turner",
                    "email": "william@example.com",
                    "mobile_no": "9800129831",
                    "object_id": "8a77df28-02d2-44a6-bde5-dc091f07d99d",
                },
                "mapping": {"id": 1438, "sync_status": "PENDING_PUSH"},
            }]

    svc.qbo_repo = FakeQboRepo()
    svc.qbo_service = FakeQboService()
    svc.employee_repo = FakeEmployeeRepo()
    svc.refresh_access_token = lambda **kwargs: "access-token"
    svc.get_qbo_api_base = lambda env: "https://example.test/qbo"
    svc.AMBIGUOUS_CREATE_LOOKUP_ATTEMPTS = 1
    svc.AMBIGUOUS_CREATE_LOOKUP_DELAY_SECONDS = 0
    svc.find_employee_candidates_in_qbo = MagicMock(side_effect=[
        [],
        [{"Id": "84", "SyncToken": "0", "DisplayName": "William Turner-9800129831"}],
    ])
    svc.create_employee_in_qbo = MagicMock(side_effect=requests.exceptions.ReadTimeout("timeout"))
    monkeypatch.setattr(employee_export_module, "safe_audit_log", lambda **kwargs: None)

    summary = svc.export_employees(tenant_id="tenant-1", service_id="6")

    assert summary["createdInQbo"] == 1
    assert summary["failed"] == 0
    svc.create_employee_in_qbo.assert_called_once()
    kwargs = svc.employee_repo.claim_external_employee_mapping.call_args.kwargs
    assert kwargs["mapping_id"] == 1438
    assert kwargs["external_employee_id"] == "84"


def test_ambiguous_recovery_prefers_exact_created_display_name_over_email_match():
    svc = _service()
    svc.AMBIGUOUS_CREATE_LOOKUP_ATTEMPTS = 1
    svc.AMBIGUOUS_CREATE_LOOKUP_DELAY_SECONDS = 0
    calls = []

    def fake_query(access_token, base_url, realm_id, field_name, field_value):
        calls.append((field_name, field_value))
        if field_name == "DisplayName" and field_value == "William Turner-emp-204":
            return [{"Id": "NEW", "DisplayName": field_value}]
        if field_name == "PrimaryEmailAddr":
            return [{"Id": "OLD", "DisplayName": "Old William"}]
        return []

    svc._query_employees = fake_query
    recovered = svc._recover_employee_after_ambiguous_create(
        access_token="token",
        base_url="https://example.test",
        realm_id="realm",
        employee={
            "id": 204,
            "firstname": "William",
            "lastname": "Turner",
            "email": "william@example.com",
        },
        display_name_override="William Turner-emp-204",
    )

    assert recovered["Id"] == "NEW"
    assert calls == [("DisplayName", "William Turner-emp-204")]


def test_export_claims_unbound_existing_qbo_employee_instead_of_reporting_false_conflict(monkeypatch):
    from unittest.mock import MagicMock

    import source.quickbooks.QuickBooksEmployeeExportService as employee_export_module

    svc = _service()

    class FakeQboRepo:
        def getAdditionalInfo(self, tenant_id, service_id):
            return {
                "refreshToken": "refresh-token",
                "realmId": "realm-1",
                "env": "sandbox",
                "status": "connected",
            }

    class FakeQboService:
        token_url = "https://example.test/token"

        def get_quickbooks_config(self, tenant_id, service_id):
            return {"ClientId": "client-id", "ClientSecret": "client-secret"}

    class FakeEmployeeRepo:
        STATUS_PENDING_PUSH = "PENDING_PUSH"
        STATUS_FAILED = "FAILED"
        STATUS_SYNC_FAILED = "SYNC_FAILED"

        def __init__(self):
            self.claim_external_employee_mapping = MagicMock()
            self.mark_mapping_failed = MagicMock()

        def get_export_candidates(self, tenant_id, service_id, limit, offset):
            return [{
                "employee": {
                    "id": 203,
                    "active": True,
                    "deleted": False,
                    "invalid": False,
                    "firstname": "Jim",
                    "lastname": "Hopper",
                    "email": "jimhopper@example.test",
                    "mobile_no": "2214648456",
                    "object_id": "d4e7503d-4d81-432b-b324-d5db72f4f85d",
                },
                "mapping": {"id": 1431, "sync_status": "FAILED"},
            }]

    svc.qbo_repo = FakeQboRepo()
    svc.qbo_service = FakeQboService()
    svc.employee_repo = FakeEmployeeRepo()
    svc.refresh_access_token = lambda **kwargs: "access-token"
    svc.get_qbo_api_base = lambda env: "https://example.test/qbo"
    svc.find_employee_candidates_in_qbo = MagicMock(return_value=[{
        "Id": "83",
        "SyncToken": "2",
        "DisplayName": "jim hopper",
    }])
    svc.create_employee_in_qbo = MagicMock()
    monkeypatch.setattr(employee_export_module, "safe_audit_log", lambda **kwargs: None)

    summary = svc.export_employees(tenant_id="tenant-1", service_id="6")

    assert summary["mapped from QBO"] == 1
    assert summary["failed"] == 0
    svc.create_employee_in_qbo.assert_not_called()
    kwargs = svc.employee_repo.claim_external_employee_mapping.call_args.kwargs
    assert kwargs["mapping_id"] == 1431
    assert kwargs["external_employee_id"] == "83"
    assert kwargs["user_account_id"] == 203


def test_build_qbo_employee_payload_syncs_cost_hire_and_birth_dates():
    from datetime import date, datetime, timezone

    svc = _service()
    employee = {
        "id": 71,
        "active": True,
        "deleted": False,
        "invalid": False,
        "firstname": "Ava",
        "lastname": "Stone",
        "email": "ava.stone@example.com",
        "mobile_no": "5550101000",
        "birthdate": datetime(1992, 4, 12, 9, 30, tzinfo=timezone.utc),
        "user_status": {"hired_date": date(2024, 1, 15)},
        "resource": {"cost": "800.00", "unit": "daily"},
    }

    payload = svc.build_qbo_employee_payload(
        employee,
        cost_sync_config={
            "cost_enabled": True,
            "cost_qbo_field": "CostRate",
            "cost_unit_hours": {"hourly": 1, "daily": 8},
        },
    )

    assert payload["BirthDate"] == "1992-04-12"
    assert payload["HiredDate"] == "2024-01-15"
    assert payload["CostRate"] == 100.0
    assert payload["BillRate"] == 100.0
    assert payload["BillableTime"] is True


def test_build_qbo_employee_payload_keeps_bill_rate_configuration_compatible():
    svc = _service()
    employee = {
        "id": 72,
        "active": True,
        "firstname": "Jordan",
        "lastname": "Kim",
        "resource": {"cost": "45.50", "unit": "hourly"},
    }

    payload = svc.build_qbo_employee_payload(
        employee,
        cost_sync_config={
            "cost_enabled": True,
            "cost_qbo_field": "BillRate",
            "cost_unit_hours": {"hourly": 1},
        },
    )

    assert payload["BillRate"] == 45.5
    assert payload["BillableTime"] is True
    assert "CostRate" not in payload


def test_build_qbo_employee_payload_omits_optional_cost_and_dates_when_missing():
    svc = _service()
    payload = svc.build_qbo_employee_payload(
        {
            "id": 73,
            "active": True,
            "firstname": "Noah",
            "lastname": "Reed",
        }
    )

    assert "BirthDate" not in payload
    assert "HiredDate" not in payload
    assert "CostRate" not in payload
    assert "BillRate" not in payload
    assert "BillableTime" not in payload
