from __future__ import annotations

import stat
from unittest.mock import MagicMock, patch

import pytest

from source.Utils import constants as const
from source.quickbooks.QuickBooksAuthentication import (
    QuickBooksOAuthClient,
    QuickBooksReconnectRequiredError,
)


class _Response:
    def __init__(self, status_code: int, payload: dict, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = {}

    def json(self):
        return self._payload


def test_refresh_persists_rotated_refresh_token() -> None:
    repository = MagicMock()
    client = QuickBooksOAuthClient(repository=repository)
    response = _Response(
        200,
        {
            "access_token": "access-new",
            "refresh_token": "refresh-new",
            "expires_in": 3600,
            "x_refresh_token_expires_in": 86400,
            "scope": "com.intuit.quickbooks.accounting",
            "token_type": "bearer",
        },
    )

    with patch(
        "source.quickbooks.QuickBooksAuthentication.requests.post",
        return_value=response,
    ):
        access_token = client.refresh_access_token(
            tenant_id="tn1",
            service_id="6",
            client_id="client",
            client_secret="secret",
            refresh_token="refresh-old",
            token_url="https://example.test/token",
            current_connection={const.KEY_ENV: "sandbox"},
        )

    assert access_token == "access-new"
    repository.updateConnectionTokens.assert_called_once()
    assert repository.updateConnectionTokens.call_args.kwargs["refresh_token"] == "refresh-new"


def test_invalid_grant_marks_connection_for_reconnect() -> None:
    repository = MagicMock()
    client = QuickBooksOAuthClient(repository=repository)
    response = _Response(
        400,
        {"error": "invalid_grant", "error_description": "Token expired"},
    )

    with patch(
        "source.quickbooks.QuickBooksAuthentication.requests.post",
        return_value=response,
    ), pytest.raises(QuickBooksReconnectRequiredError) as exc_info:
        client.refresh_access_token(
            tenant_id="tn1",
            service_id="6",
            client_id="client",
            client_secret="secret",
            refresh_token="refresh-old",
            token_url="https://example.test/token",
            current_connection={},
        )

    assert exc_info.value.error_code == const.AUTH_ERROR_INVALID_GRANT
    repository.markReconnectRequired.assert_called_once()


def test_local_rotated_token_file_is_owner_only(tmp_path, monkeypatch) -> None:
    repository = MagicMock()
    client = QuickBooksOAuthClient(repository=repository)
    token_file = tmp_path / "rotated-token.txt"
    monkeypatch.setenv("QBO_PROD_TEST_ROTATED_REFRESH_TOKEN_FILE", str(token_file))
    response = _Response(
        200,
        {
            "access_token": "access-new",
            "refresh_token": "refresh-new",
            "expires_in": 3600,
        },
    )

    with patch(
        "source.quickbooks.QuickBooksAuthentication.requests.post",
        return_value=response,
    ):
        client.refresh_access_token(
            tenant_id="tn1",
            service_id="6",
            client_id="client",
            client_secret="secret",
            refresh_token="refresh-old",
            token_url="https://example.test/token",
            current_connection={},
            persist_rotated_token=False,
        )

    assert token_file.read_text() == "refresh-new"
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    repository.updateConnectionTokens.assert_not_called()
