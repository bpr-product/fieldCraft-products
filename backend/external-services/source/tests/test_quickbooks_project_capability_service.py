from unittest.mock import MagicMock, patch

from source.Utils import constants as const
from source.quickbooks.QuickBooksCapabilityService import QuickBooksCapabilityService, _parse_preferences
from source.quickbooks.QuickBooksProjectApiClient import QuickBooksProjectApiClient


def test_parse_preferences_reads_projects_enabled_when_name_value_is_dict():
    caps = _parse_preferences({
        "OtherPrefs": {
            "NameValue": {
                "Name": "ProjectsEnabled",
                "Value": "true",
            }
        }
    })

    assert caps[const.CAPABILITY_PROJECTS] is True


def test_projects_available_uses_live_preferences_when_refreshing():
    svc = QuickBooksCapabilityService()
    svc.fetch_and_save = MagicMock(return_value={const.CAPABILITY_PROJECTS: True})
    result = svc.projects_available("tenant-1", "6", refresh=True)

    assert result is True
    svc.fetch_and_save.assert_called_once_with("tenant-1", "6")


def test_projects_available_does_not_override_explicit_projects_disabled():
    svc = QuickBooksCapabilityService()
    svc.fetch_and_save = MagicMock(return_value={const.CAPABILITY_PROJECTS: False})
    result = svc.projects_available("tenant-1", "6", refresh=True)

    assert result is False
    svc.fetch_and_save.assert_called_once_with("tenant-1", "6")


def test_project_api_capability_check_is_read_only():
    client = QuickBooksProjectApiClient()
    response = MagicMock(status_code=200)

    with patch("source.quickbooks.QuickBooksProjectApiClient.qbo_api.qbo_post", return_value=response) as post, \
         patch("source.quickbooks.QuickBooksProjectApiClient.qbo_api.safe_json", return_value={
             "data": {
                 "projectManagementProjects": {
                     "edges": [],
                     "pageInfo": {"hasNextPage": False},
                 }
             }
         }):
        assert client.can_access_projects(access_token="AT", env="sandbox") is True

    body = post.call_args.kwargs["json"]
    assert "projectManagementProjects" in body["query"]
    assert "projectManagementCreateProject" not in body["query"]


def test_parse_preferences_reads_time_tracking_enabled():
    caps = _parse_preferences({
        "OtherPrefs": {
            "NameValue": [
                {"Name": "ProjectsEnabled", "Value": "true"},
                {"Name": "TimeTrackingFeatureEnabled", "Value": "true"},
            ]
        }
    })

    assert caps[const.CAPABILITY_TIME_TRACKING] is True
