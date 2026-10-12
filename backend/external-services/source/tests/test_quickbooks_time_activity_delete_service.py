from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from source.quickbooks.QuickBooksTimeActivityDeleteService import (
    QuickBooksTimeActivityDeleteService,
)


def _response(status_code: int, payload: dict, text: str = "") -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    response.text = text or str(payload)
    response.headers = {"intuit_tid": "tid-123"}
    return response


def _service() -> QuickBooksTimeActivityDeleteService:
    service = QuickBooksTimeActivityDeleteService.__new__(QuickBooksTimeActivityDeleteService)
    service.qbo_repo = MagicMock()
    service.qbo_service = MagicMock()
    service.timelog_repo = MagicMock()
    service.oauth_client = MagicMock()
    service._context = MagicMock(
        return_value={
            "tenantId": "tn1",
            "serviceId": "6",
            "realmId": "realm1",
            "env": "production",
            "baseUrl": "https://quickbooks.api.intuit.com",
            "accessToken": "access-token",
        }
    )
    return service


def test_requires_confirmation_for_live_delete() -> None:
    service = _service()

    with pytest.raises(ValueError, match="confirm_delete"):
        service.delete_all_mapped_time_activities(
            tenant_id="tn1",
            service_id="6",
            confirm_delete=False,
            dry_run=False,
        )


def test_dry_run_lists_only_mapped_records_without_calling_qbo() -> None:
    service = _service()
    service.timelog_repo.list_mapped_time_activities.return_value = [
        {"id": 10, "external_time_activity_id": "101", "sync_token": "0"},
        {"id": 11, "external_time_activity_id": "102", "sync_token": "1"},
    ]

    result = service.delete_all_mapped_time_activities(
        tenant_id="tn1",
        service_id="6",
        confirm_delete=False,
        dry_run=True,
    )

    assert result["ok"] is True
    assert result["scope"] == "FIELDCRAFT_MAPPED_TIME_ACTIVITIES_ONLY"
    assert result["mappedRows"] == 2
    assert result["uniqueTimeActivities"] == 2
    assert {row["timeActivityId"] for row in result["results"]} == {"101", "102"}
    service._context.assert_not_called()
    service.timelog_repo.reset_mapped_time_activities_for_reexport.assert_not_called()


def test_successful_delete_resets_mapping_to_pending_push() -> None:
    service = _service()
    service.timelog_repo.list_mapped_time_activities.return_value = [
        {"id": 10, "external_time_activity_id": "101", "sync_token": "0"},
    ]
    service._read_time_activity = MagicMock(
        return_value=_response(200, {"TimeActivity": {"Id": "101", "SyncToken": "4"}})
    )
    service._delete_time_activity = MagicMock(
        return_value=_response(200, {"TimeActivity": {"Id": "101", "status": "Deleted"}})
    )
    service.timelog_repo.reset_mapped_time_activities_for_reexport.return_value = 1

    result = service.delete_all_mapped_time_activities(
        tenant_id="tn1",
        service_id="6",
        confirm_delete=True,
    )

    assert result["ok"] is True
    assert result["deleted"] == 1
    assert result["resetMappings"] == 1
    service._delete_time_activity.assert_called_once_with(
        base_url="https://quickbooks.api.intuit.com",
        realm_id="realm1",
        access_token="access-token",
        time_activity_id="101",
        sync_token="4",
    )


def test_already_missing_time_activity_is_reset_without_delete_call() -> None:
    service = _service()
    service.timelog_repo.list_mapped_time_activities.return_value = [
        {"id": 10, "external_time_activity_id": "101", "sync_token": "0"},
    ]
    service._read_time_activity = MagicMock(
        return_value=_response(
            400,
            {
                "Fault": {
                    "Error": [
                        {
                            "code": "610",
                            "Message": "Object Not Found",
                            "Detail": "Object Not Found: the record was deleted.",
                        }
                    ]
                }
            },
        )
    )
    service._delete_time_activity = MagicMock()
    service.timelog_repo.reset_mapped_time_activities_for_reexport.return_value = 1

    result = service.delete_all_mapped_time_activities(
        tenant_id="tn1",
        service_id="6",
        confirm_delete=True,
    )

    assert result["ok"] is True
    assert result["alreadyMissing"] == 1
    assert result["resetMappings"] == 1
    service._delete_time_activity.assert_not_called()


def test_failed_delete_keeps_mapping_unchanged() -> None:
    service = _service()
    service.timelog_repo.list_mapped_time_activities.return_value = [
        {"id": 10, "external_time_activity_id": "101", "sync_token": "0"},
    ]
    service._read_time_activity = MagicMock(
        return_value=_response(200, {"TimeActivity": {"Id": "101", "SyncToken": "4"}})
    )
    service._delete_time_activity = MagicMock(
        return_value=_response(
            400,
            {
                "Fault": {
                    "Error": [
                        {
                            "code": "5010",
                            "Message": "Stale Object Error",
                            "Detail": "The object changed before deletion.",
                        }
                    ]
                }
            },
        )
    )

    result = service.delete_all_mapped_time_activities(
        tenant_id="tn1",
        service_id="6",
        confirm_delete=True,
    )

    assert result["ok"] is False
    assert result["failed"] == 1
    assert result["resetMappings"] == 0
    assert result["failures"][0]["timeActivityId"] == "101"
    service.timelog_repo.reset_mapped_time_activities_for_reexport.assert_not_called()


def test_duplicate_mapping_rows_delete_remote_record_once() -> None:
    service = _service()
    service.timelog_repo.list_mapped_time_activities.return_value = [
        {"id": 10, "external_time_activity_id": "101", "sync_token": "0"},
        {"id": 11, "external_time_activity_id": "101", "sync_token": "1"},
    ]
    service._read_time_activity = MagicMock(
        return_value=_response(200, {"TimeActivity": {"Id": "101", "SyncToken": "4"}})
    )
    service._delete_time_activity = MagicMock(
        return_value=_response(200, {"TimeActivity": {"Id": "101", "status": "Deleted"}})
    )
    service.timelog_repo.reset_mapped_time_activities_for_reexport.return_value = 2

    result = service.delete_all_mapped_time_activities(
        tenant_id="tn1",
        service_id="6",
        confirm_delete=True,
    )

    assert result["mappedRows"] == 2
    assert result["uniqueTimeActivities"] == 1
    assert result["resetMappings"] == 2
    service._delete_time_activity.assert_called_once()
