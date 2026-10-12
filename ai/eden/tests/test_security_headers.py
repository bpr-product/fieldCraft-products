from fastapi import FastAPI
from fastapi.testclient import TestClient

from source.utils.security_headers import SecurityHeadersMiddleware


def _make_app():
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    @app.post("/echo")
    def echo(body: dict):
        return body

    return app


def test_adds_security_headers_to_response():
    client = TestClient(_make_app())
    r = client.get("/ping")
    assert r.status_code == 200
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert r.headers["Content-Security-Policy"] == "default-src 'none'"
    assert "Strict-Transport-Security" in r.headers


def test_rejects_oversized_body():
    client = TestClient(_make_app())
    big_payload = {"utterance": "x" * 2_000_000}
    r = client.post("/echo", json=big_payload)
    assert r.status_code == 413


def test_allows_normal_sized_body():
    client = TestClient(_make_app())
    r = client.post("/echo", json={"utterance": "take me to invoices"})
    assert r.status_code == 200
