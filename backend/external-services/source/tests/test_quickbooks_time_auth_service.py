from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from source.Utils import constants as const
from source.service.QuickBooksTimeAuthService import (
    QuickBooksTimeAuthService,
    QuickBooksTimeReconnectRequiredError,
    QuickBooksTimeTokenError,
)


class FakeRepository:
    def __init__(self, connection=None, status=None, state_result=("tn1", "6", False)):
        self.connection = connection or {}
        self.status = status or {"connected": False}
        self.state_result = state_result
        self.calls = []

    def saveTimeOauthState(self, **kwargs):
        self.calls.append(("save_state", kwargs))

    def consumeTimeOauthState(self, state):
        self.calls.append(("consume_state", state))
        return self.state_result

    def getTimeStatus(self, tenant_id, service_id):
        self.calls.append(("status", tenant_id, service_id))
        return dict(self.status)

    def saveTimeConnectionInfo(self, **kwargs):
        self.calls.append(("save_connection", kwargs))

    def getAdditionalInfo(self, tenant_id, service_id):
        self.calls.append(("additional_info", tenant_id, service_id))
        return dict(self.connection)

    def updateTimeConnectionTokens(self, **kwargs):
        self.calls.append(("update_tokens", kwargs))

    def markTimeReconnectRequired(self, **kwargs):
        self.calls.append(("mark_reconnect", kwargs))

    def disconnectTime(self, tenant_id, service_id):
        self.calls.append(("disconnect", tenant_id, service_id))


class FakeQboService:
    def __init__(self, config=None):
        self.config = config or {
            const.KEY_TIME_CLIENT_ID: "time-client",
            const.KEY_TIME_CLIENT_SECRET: "time-secret",
        }

    def get_quickbooks_config(self, tenant_id, service_id):
        return dict(self.config)


def service(repo=None, config=None):
    instance = QuickBooksTimeAuthService.__new__(QuickBooksTimeAuthService)
    instance.repository = repo or FakeRepository()
    instance.qbo_service = FakeQboService(config)
    return instance


def test_start_connect_saves_short_lived_state_and_builds_authorization_url(monkeypatch):
    repo = FakeRepository()
    auth = service(repo)
    monkeypatch.setenv("QBTIME_REDIRECT_URI", "https://example.test/qbtime/callback")
    monkeypatch.setattr("source.service.QuickBooksTimeAuthService.secrets.token_urlsafe", lambda _: "STATE")

    result = auth.start_connect("tn1", "6")

    parsed = urlparse(result["authorizationUrl"])
    query = parse_qs(parsed.query)
    assert query == {
        "response_type": ["code"],
        "client_id": ["time-client"],
        "redirect_uri": ["https://example.test/qbtime/callback"],
        "state": ["STATE"],
    }
    assert repo.calls == [
        (
            "save_state",
            {
                "subscriber_id": "tn1",
                "service_id": "6",
                "state": "STATE",
                "ttl_minutes": 10,
            },
        )
    ]


def test_handle_callback_persists_rotated_tokens(monkeypatch):
    repo = FakeRepository()
    auth = service(repo)
    monkeypatch.setenv("QBTIME_REDIRECT_URI", "https://example.test/qbtime/callback")
    monkeypatch.setattr(
        auth,
        "_post_grant",
        lambda body: {
            "access_token": "ACCESS",
            "refresh_token": "REFRESH",
            "expires_in": 3600,
            "company_id": 123,
            "user_id": 456,
        },
    )

    result = auth.handle_callback("CODE", "STATE")

    assert result == {
        "ok": True,
        "tenantId": "tn1",
        "serviceId": "6",
        "companyIdPresent": True,
    }
    save_call = next(call for call in repo.calls if call[0] == "save_connection")
    assert save_call[1]["refresh_token"] == "REFRESH"
    assert save_call[1]["access_token"] == "ACCESS"
    assert save_call[1]["company_id"] == "123"
    assert save_call[1]["user_id"] == "456"
    assert save_call[1]["access_expires_at"]


def test_handle_callback_replay_is_idempotent_when_connection_exists():
    repo = FakeRepository(
        status={"connected": True, "companyIdPresent": True},
        state_result=("tn1", "6", True),
    )
    auth = service(repo)

    result = auth.handle_callback("CODE", "STATE")

    assert result["ok"] is True
    assert result["message"] == "Already connected (idempotent)."
    assert not any(call[0] == "save_connection" for call in repo.calls)


def test_fresh_access_token_reuses_unexpired_token():
    repo = FakeRepository(
        connection={
            const.KEY_TIME_ACCESS_TOKEN: "ACCESS",
            const.KEY_TIME_ACCESS_EXPIRES_AT: (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat(),
        }
    )
    auth = service(repo)

    assert auth.get_fresh_access_token("tn1", "6") == "ACCESS"
    assert not any(call[0] == "update_tokens" for call in repo.calls)


def test_expired_access_token_refreshes_and_persists_rotated_refresh_token(monkeypatch):
    repo = FakeRepository(
        connection={
            const.KEY_TIME_ACCESS_TOKEN: "OLD_ACCESS",
            const.KEY_TIME_ACCESS_EXPIRES_AT: (
                datetime.now(timezone.utc) - timedelta(minutes=1)
            ).isoformat(),
            const.KEY_TIME_REFRESH_TOKEN: "OLD_REFRESH",
        }
    )
    auth = service(repo)
    monkeypatch.setattr(
        auth,
        "_post_grant",
        lambda body: {
            "access_token": "NEW_ACCESS",
            "refresh_token": "NEW_REFRESH",
            "expires_in": 3600,
        },
    )

    assert auth.get_fresh_access_token("tn1", "6") == "NEW_ACCESS"
    update_call = next(call for call in repo.calls if call[0] == "update_tokens")
    assert update_call[1]["refresh_token"] == "NEW_REFRESH"
    assert update_call[1]["access_token"] == "NEW_ACCESS"


def test_refresh_grant_failure_marks_reconnect_required(monkeypatch):
    repo = FakeRepository(
        connection={
            const.KEY_TIME_REFRESH_TOKEN: "REFRESH",
            const.KEY_TIME_ACCESS_EXPIRES_AT: None,
        }
    )
    auth = service(repo)

    def fail(_body):
        raise QuickBooksTimeTokenError("grant failed")

    monkeypatch.setattr(auth, "_post_grant", fail)

    with pytest.raises(QuickBooksTimeReconnectRequiredError):
        auth.get_fresh_access_token("tn1", "6")

    reconnect_call = next(call for call in repo.calls if call[0] == "mark_reconnect")
    assert reconnect_call[1]["error_code"] == const.AUTH_ERROR_INVALID_GRANT


def test_token_error_does_not_include_provider_response_body(monkeypatch):
    auth = service()
    response = SimpleNamespace(
        status_code=401,
        text='{"client_secret":"must-not-leak"}',
    )
    monkeypatch.setattr("source.service.QuickBooksTimeAuthService.requests.post", lambda *a, **k: response)

    with pytest.raises(QuickBooksTimeTokenError) as captured:
        auth._post_grant({"grant_type": "refresh_token"})

    assert "must-not-leak" not in str(captured.value)
    assert "HTTP 401" in str(captured.value)
