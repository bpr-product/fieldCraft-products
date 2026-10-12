import pytest
from unittest.mock import MagicMock

from source.service.QuickBooksService import QuickBooksService


def test_start_connect_success(monkeypatch):
    svc = QuickBooksService()

    # mock audit to avoid db logging
    svc.audit = MagicMock()
    monkeypatch.setattr(svc, "_audit", lambda *args, **kwargs: "audit-id")

    # mock repo
    svc.repository = MagicMock()

    # mock config
    monkeypatch.setattr(
        svc,
        "get_quickbooks_config",
        lambda tenant_id, service_id: {"ClientId": "CID", "Env": "sandbox"},
    )

    # mock env
    def fake_getenv(key, default=None):
        if key == "QBO_REDIRECT_URI":
            return "https://example.com/extsvc/api/v1/qbo/callback"
        if key == "QBO_SCOPES":
            return "com.intuit.quickbooks.accounting"
        if key == "QBO_AUTH_URL":
            return None
        return default

    monkeypatch.setattr("source.service.QuickBooksService.os.getenv", fake_getenv)

    out = svc.start_connect("tn1", "6")

    assert out["env"] == "sandbox"
    assert "authorizationUrl" in out
    assert "state" in out

    svc.repository.saveOauthState.assert_called_once()
    args, kwargs = svc.repository.saveOauthState.call_args
    assert kwargs["subscriber_id"] == "tn1"
    assert kwargs["service_id"] == "6"
    assert kwargs["ttl_minutes"] == 10


def test_start_connect_missing_client_id(monkeypatch):
    svc = QuickBooksService()
    svc.audit = MagicMock()
    monkeypatch.setattr(svc, "_audit", lambda *args, **kwargs: "audit-id")
    svc.repository = MagicMock()

    monkeypatch.setattr(
        svc,
        "get_quickbooks_config",
        lambda tenant_id, service_id: {"Env": "sandbox"},  # no ClientId
    )

    monkeypatch.setattr("source.service.QuickBooksService.os.getenv", lambda k, d=None: "x")

    with pytest.raises(ValueError):
        svc.start_connect("tn1", "6")


def test_handle_callback_already_connected(monkeypatch):
    svc = QuickBooksService()
    svc.repository = MagicMock()

    svc.repository.consumeOauthState.return_value = ("tn1", "6", False)
    svc.repository.getStatus.return_value = {
        "status": "connected",
        "realmId": "R1",
        "refreshExpiresAt": "2099-01-01T00:00:00+00:00",
    }

    out = svc.handle_callback(code="c", state="s", realm_id="R1")

    assert out["ok"] is True
    assert out["message"].lower().startswith("already connected")
    svc.repository.saveConnectionInfo.assert_not_called()


def test_handle_callback_saves_connection(monkeypatch):
    svc = QuickBooksService()
    svc.repository = MagicMock()

    svc.repository.consumeOauthState.return_value = ("tn1", "6", False)
    svc.repository.getStatus.return_value = {"status": "disconnected"}

    monkeypatch.setattr(
        svc,
        "get_quickbooks_config",
        lambda tenant_id, service_id: {"ClientId": "CID", "ClientSecret": "SEC", "Env": "sandbox"},
    )

    monkeypatch.setattr("source.service.QuickBooksService.os.getenv", lambda k, d=None: "https://cb" if k == "QBO_REDIRECT_URI" else d)

    monkeypatch.setattr(
        svc,
        "exchange_code_for_tokens",
        lambda client_id, client_secret, code, redirect_uri: {
            "access_token": "AT",
            "refresh_token": "RT",
            "expires_in": 3600,
            "x_refresh_token_expires_in": 7200,
        },
    )

    out = svc.handle_callback(code="CODE", state="STATE", realm_id="REALM")

    assert out["ok"] is True
    assert out["tenantId"] == "tn1"
    assert out["serviceId"] == "6"
    assert out["realmIdPresent"] is True
    assert out["refreshExpiresAt"] is not None

    svc.repository.saveConnectionInfo.assert_called_once()


def test_disconnect_calls_repo(monkeypatch):
    svc = QuickBooksService()
    svc.audit = MagicMock()
    monkeypatch.setattr(svc, "_audit", lambda *args, **kwargs: "audit-id")
    svc.repository = MagicMock()

    out = svc.disconnect("tn1", "6", "obj1")

    assert out["ok"] is True
    assert out["status"] == "disconnected"
    svc.repository.disconnect.assert_called_once_with("tn1", "6")