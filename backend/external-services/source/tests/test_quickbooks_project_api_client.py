from datetime import timezone
from unittest.mock import MagicMock, patch

import pytest

from source.quickbooks.QuickBooksProjectApiClient import QuickBooksProjectApiClient


def _response(status_code=200):
    return MagicMock(status_code=status_code)


def test_create_project_happy_path_builds_graphql_mutation_body():
    client = QuickBooksProjectApiClient()
    qbo_project = {
        "id": "QBO-PROJECT-1",
        "name": "Roof Replacement",
        "description": "Main roof project",
        "startDate": "2024-01-15T00:00:00Z",
        "customer": {"id": "QBO-CUST-1"},
    }

    with patch("source.quickbooks.QuickBooksProjectApiClient.qbo_api.qbo_post", return_value=_response()) as post, \
         patch("source.quickbooks.QuickBooksProjectApiClient.qbo_api.safe_json", return_value={
             "data": {"projectManagementCreateProject": qbo_project}
         }):
        result = client.create_project(
            access_token="AT",
            env="sandbox",
            realm_id="REALM-1",
            name=" Roof Replacement ",
            description=" Main roof project ",
            customer_id="QBO-CUST-1",
            start_date="2024-01-15",
        )

    assert result == qbo_project
    body = post.call_args.kwargs["json"]
    assert "projectManagementCreateProject" in body["query"]
    assert "projectManagementUpdateProject" not in body["query"]
    assert body["variables"] == {
        "name": "Roof Replacement",
        "description": "Main roof project",
        "status": "OPEN",
        "customer": {"id": "QBO-CUST-1"},
        "account": {"id": "REALM-1"},
        "startDate": "2024-01-15T00:00:00Z",
    }
    assert "dueDate" not in body["variables"]
    assert post.call_args.args[0] == "https://qb-sandbox.api.intuit.com/graphql"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer AT"


def test_create_project_raises_graphql_error_from_data_errors():
    client = QuickBooksProjectApiClient()

    with patch("source.quickbooks.QuickBooksProjectApiClient.qbo_api.qbo_post", return_value=_response()), \
         patch("source.quickbooks.QuickBooksProjectApiClient.qbo_api.safe_json", return_value={
             "data": {"errors": [{"message": "Bad project input"}]}
         }):
        with pytest.raises(Exception, match="Bad project input"):
            client.create_project(
                access_token="AT",
                env="sandbox",
                realm_id="REALM-1",
                name="Roof Replacement",
                description=None,
                customer_id="QBO-CUST-1",
            )


def test_update_project_happy_path_builds_graphql_mutation_body():
    client = QuickBooksProjectApiClient()
    qbo_project = {
        "id": "QBO-PROJECT-1",
        "name": "Updated Project",
        "dueDate": "2024-12-31T00:00:00Z",
    }

    with patch("source.quickbooks.QuickBooksProjectApiClient.qbo_api.qbo_post", return_value=_response()) as post, \
         patch("source.quickbooks.QuickBooksProjectApiClient.qbo_api.safe_json", return_value={
             "data": {"projectManagementUpdateProject": qbo_project}
         }):
        result = client.update_project(
            access_token="AT",
            env="production",
            realm_id="REALM-1",
            project_id="QBO-PROJECT-1",
            name=" Updated Project ",
            description=" Updated description ",
            customer_id="QBO-CUST-1",
            due_date="2024-12-31T00:00:00Z",
        )

    assert result == qbo_project
    body = post.call_args.kwargs["json"]
    assert "projectManagementUpdateProject" in body["query"]
    assert body["variables"] == {
        "id": "QBO-PROJECT-1",
        "name": "Updated Project",
        "description": "Updated description",
        "status": "OPEN",
        "customer": {"id": "QBO-CUST-1"},
        "account": {"id": "REALM-1"},
        "dueDate": "2024-12-31T00:00:00Z",
    }
    assert "startDate" not in body["variables"]
    assert post.call_args.args[0] == "https://qb.api.intuit.com/graphql"


def test_parse_datetime_edge_cases():
    client = QuickBooksProjectApiClient()

    assert client._parse_datetime(None) is None
    assert client._parse_datetime("") is None
    assert client._parse_datetime("not-a-date") is None

    parsed = client._parse_datetime("2024-01-15T12:30:00Z")
    assert parsed is not None
    assert parsed.tzinfo == timezone.utc
    assert parsed.isoformat() == "2024-01-15T12:30:00+00:00"
    assert client._project_date("2024-01-15") == "2024-01-15T00:00:00Z"
