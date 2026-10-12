from source.quickbooks.QuickBooksServiceRoleExportService import QuickBooksServiceRoleExportService


def _service():
    svc = QuickBooksServiceRoleExportService.__new__(QuickBooksServiceRoleExportService)
    return svc


def test_build_qbo_service_item_payload_for_active_role_create():
    svc = _service()
    role = {"id": 5, "active": True, "name": "labour", "description": "Labour role"}

    payload = svc.build_qbo_service_item_payload(role=role, income_account_id="79")

    assert payload == {
        "Name": "labour",
        "Type": "Service",
        "Active": True,
        "Description": "Labour role",
        "IncomeAccountRef": {"value": "79"},
    }


def test_build_qbo_service_item_payload_for_update_without_income_account():
    svc = _service()
    role = {"id": 4, "active": True, "name": "foreman", "description": ""}

    payload = svc.build_qbo_service_item_payload(role=role, include_type=False)

    assert payload == {"Name": "foreman", "Active": True}


def test_build_qbo_service_item_payload_for_inactive_role():
    svc = _service()
    role = {"id": 7, "active": False, "name": "superAdmin", "description": None}

    payload = svc.build_qbo_service_item_payload(role=role)

    assert payload == {"Name": "superAdmin", "Type": "Service", "Active": False}


def test_qbo_service_inactive_reason_is_user_friendly():
    svc = _service()
    role = {"id": 1, "active": True, "name": "admin"}
    qbo_item = {"Id": "10", "Name": "admin (deleted)", "Active": False}

    reason = svc._build_qbo_service_inactive_reason(role=role, qbo_item=qbo_item)

    assert "Please make the 'admin' service item active in QuickBooks" in reason
    assert "time activity and bill sync" in reason
    assert "QBO_SERVICE_ITEM_INACTIVE" in reason


def test_qbo_service_name_changed_reason_is_user_friendly():
    svc = _service()
    role = {"id": 1, "active": True, "name": "admin"}
    qbo_item = {"Id": "10", "Name": "admin old", "Active": True}

    reason = svc._build_qbo_service_changed_reason(role=role, qbo_item=qbo_item)

    assert "QuickBooks service item name does not match" in reason
    assert "rename the QuickBooks service item to 'admin'" in reason
    assert "QBO_SERVICE_ITEM_NAME_CHANGED" in reason


def test_get_selected_income_account_id_from_service_subscriber_additional_info():
    svc = _service()
    connection = {
        "service_role_sync": {
            "qbo_service_income_account": {
                "chart_of_account_id": 12,
                "external_id": "79",
                "name": "Services Income",
            }
        }
    }

    assert svc.get_selected_income_account_id(connection) == "79"


def test_missing_income_account_message_is_user_friendly():
    svc = _service()

    message = svc.build_missing_income_account_message("labour")

    assert "Cannot create QuickBooks service item for role 'labour'" in message
    assert "Select a synced QuickBooks chart of account" in message


def test_find_service_item_in_qbo_filters_lookup_to_service_items(monkeypatch):
    svc = _service()
    captured = {}

    class Response:
        status_code = 200

        def json(self):
            return {"QueryResponse": {"Item": [{"Id": "10", "Name": "Carpenter", "Type": "Service"}]}}

    def fake_qbo_get(_url, *, headers, params, timeout):
        captured["query"] = params["query"]
        return Response()

    import sys
    module = sys.modules[QuickBooksServiceRoleExportService.__module__]
    monkeypatch.setattr(module.qbo_api, "qbo_get", fake_qbo_get)

    item = svc.find_service_item_in_qbo(
        access_token="token",
        base_url="https://quickbooks.example",
        realm_id="123",
        name="Carpenter",
    )

    assert item["Id"] == "10"
    assert "Name = 'Carpenter'" in captured["query"]
    assert "AND Type = 'Service'" in captured["query"]
    assert "MAXRESULTS 50" in captured["query"]
