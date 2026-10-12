from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from source.api import navigation_api
from source.model.navigation_models import NavigationResponse
from source.utils.auth import AuthContext
from source.utils.rate_limit import enforce_rate_limit


def _make_app(auth: AuthContext) -> FastAPI:
    app = FastAPI()
    app.include_router(navigation_api.router)
    app.dependency_overrides[enforce_rate_limit] = lambda: auth
    return app


def _admin_auth() -> AuthContext:
    return AuthContext(
        user_id="admin-user", tenant_id="tn00000001", roles=["admin"], permissions=[]
    )


def _sales_auth() -> AuthContext:
    return AuthContext(
        user_id="sales-user", tenant_id="tn00000001", roles=["sales"], permissions=[]
    )


def test_admin_only_off_by_default_allows_non_admin(monkeypatch):
    monkeypatch.delenv("EDEN_ADMIN_ONLY", raising=False)
    client = TestClient(_make_app(_sales_auth()))
    with patch.object(navigation_api.orchestrator, "dispatch") as dispatch, \
         patch.object(navigation_api.orchestrator.role_resolver, "is_admin", return_value=False):
        dispatch.return_value = NavigationResponse(type="fallback", message="ok")
        r = client.post("/api/v1/agent/navigate", json={"utterance": "hi", "currentRoute": "/", "userId": "x", "tenantId": "x"})
    assert r.status_code == 200


def test_admin_only_enabled_blocks_non_admin_navigate(monkeypatch):
    monkeypatch.setenv("EDEN_ADMIN_ONLY", "true")
    client = TestClient(_make_app(_sales_auth()))
    with patch.object(navigation_api.orchestrator.role_resolver, "is_admin", return_value=False):
        r = client.post("/api/v1/agent/navigate", json={"utterance": "hi", "currentRoute": "/", "userId": "x", "tenantId": "x"})
    assert r.status_code == 403
    assert "admin" in r.json()["detail"].lower()


def test_admin_only_enabled_allows_admin_navigate(monkeypatch):
    monkeypatch.setenv("EDEN_ADMIN_ONLY", "true")
    client = TestClient(_make_app(_admin_auth()))
    with patch.object(navigation_api.orchestrator, "dispatch") as dispatch, \
         patch.object(navigation_api.orchestrator.role_resolver, "is_admin", return_value=True):
        dispatch.return_value = NavigationResponse(type="fallback", message="ok")
        r = client.post("/api/v1/agent/navigate", json={"utterance": "hi", "currentRoute": "/", "userId": "x", "tenantId": "x"})
    assert r.status_code == 200


def test_admin_only_enabled_blocks_non_admin_history(monkeypatch):
    monkeypatch.setenv("EDEN_ADMIN_ONLY", "true")
    client = TestClient(_make_app(_sales_auth()))
    with patch.object(navigation_api.orchestrator.role_resolver, "is_admin", return_value=False):
        r = client.get("/api/v1/agent/history")
    assert r.status_code == 403


def test_admin_only_enabled_blocks_non_admin_reset(monkeypatch):
    monkeypatch.setenv("EDEN_ADMIN_ONLY", "true")
    client = TestClient(_make_app(_sales_auth()))
    with patch.object(navigation_api.orchestrator.role_resolver, "is_admin", return_value=False):
        r = client.post(
            "/api/v1/agent/reset",
            json={"tenantId": "tn00000001", "userId": "sales-user"},
        )
    assert r.status_code == 403
