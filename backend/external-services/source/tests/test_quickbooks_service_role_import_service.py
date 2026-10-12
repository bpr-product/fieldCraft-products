from source.quickbooks.QuickBooksServiceRoleImportService import QuickBooksServiceRoleImportService


def _service():
    return QuickBooksServiceRoleImportService.__new__(QuickBooksServiceRoleImportService)


def test_service_role_import_skips_unmapped_quickbooks_services(monkeypatch):
    svc = _service()

    class Repo:
        def get_current_mapping_by_external_service_id(self, **_kwargs):
            return None

    svc.role_repo = Repo()
    summary = {
        "processed": 0,
        "validatedMapped": 0,
        "skippedUnmapped": 0,
        "skippedNonServiceItems": 0,
        "skippedAlreadyCurrent": 0,
        "skippedLocalNewer": 0,
        "skippedDeleted": 0,
        "syncFailed": 0,
        "failed": 0,
        "failures": [],
        "results": [],
    }

    svc._process_qbo_item(
        summary=summary,
        tenant_id="tn24047750",
        service_id="6",
        realm_id="123",
        qbo_item={"Id": "9", "Name": "QBO Only Service", "Type": "Service", "Active": True},
    )

    assert summary["skippedUnmapped"] == 1
    assert summary["validatedMapped"] == 0
    assert summary["results"] == []


def test_service_role_import_failure_keeps_existing_external_display_name():
    svc = _service()
    captured = {}

    class Repo:
        STATUS_SYNC_FAILED = "SYNC_FAILED"

        def mark_mapping_failed(self, **kwargs):
            captured.update(kwargs)

    svc.role_repo = Repo()
    summary = {"syncFailed": 0, "failed": 0, "failures": [], "results": []}
    detail = {"qboServiceItemId": "26"}

    svc._mark_sync_failed(
        summary=summary,
        tenant_id="tn24047750",
        mapping={"id": 3, "role_id": 3, "external_display_name": "admin", "sync_token": "1"},
        detail=detail,
        reason="inactive",
        error="QuickBooks service item is inactive or deleted.",
        action="qboServiceInactive",
        qbo_item={"Id": "26", "Name": "admin (deleted)", "SyncToken": "2"},
    )

    assert "external_display_name" not in captured
    assert captured["sync_token"] == "2"
    assert summary["syncFailed"] == 1


def test_fetch_qbo_items_filters_full_import_query_to_service_items(monkeypatch):
    svc = _service()
    captured = {}

    class Response:
        status_code = 200
        headers = {}

        def json(self):
            return {"QueryResponse": {"Item": []}}

    def fake_qbo_get(_url, *, headers, params, timeout):
        captured["query"] = params["query"]
        return Response()

    import sys
    module = sys.modules[QuickBooksServiceRoleImportService.__module__]
    monkeypatch.setattr(module.qbo_api, "qbo_get", fake_qbo_get)

    items, _intuit_tid = svc._fetch_qbo_items(
        access_token="token",
        base_url="https://quickbooks.example",
        realm_id="123",
        start_position=1,
        max_results=100,
    )

    assert items == []
    assert "WHERE Type = 'Service'" in captured["query"]
    assert "startposition 1 maxresults 100" in captured["query"]
