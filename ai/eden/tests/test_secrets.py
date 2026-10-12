import pytest

from source.utils import secrets


@pytest.fixture(autouse=True)
def _reset_kv_client():
    secrets._kv_client = None
    secrets._kv_init_attempted = False
    yield
    secrets._kv_client = None
    secrets._kv_init_attempted = False


def test_falls_back_to_env_var_when_keyvault_not_configured(monkeypatch):
    monkeypatch.setattr(secrets, "AZURE_KEYVAULT_NAME", "")
    monkeypatch.setenv("SOME_SECRET", "env-value")
    assert secrets.get_secret("some-secret", "SOME_SECRET") == "env-value"


def test_falls_back_to_default_when_neither_source_has_it(monkeypatch):
    monkeypatch.setattr(secrets, "AZURE_KEYVAULT_NAME", "")
    monkeypatch.delenv("MISSING_SECRET", raising=False)
    assert secrets.get_secret("missing-secret", "MISSING_SECRET", default="fallback") == "fallback"


def test_uses_keyvault_when_configured_and_reachable(monkeypatch):
    monkeypatch.setattr(secrets, "AZURE_KEYVAULT_NAME", "kv-fieldcraft-test")
    fake_client = type("FakeClient", (), {
        "get_secret": lambda self, name: type("S", (), {"value": "kv-value"})(),
    })()
    monkeypatch.setattr(secrets, "_get_kv_client", lambda: fake_client)
    assert secrets.get_secret("mongodb-uri", "MONGODB_URI") == "kv-value"


def test_falls_back_to_env_var_when_keyvault_read_fails(monkeypatch):
    monkeypatch.setattr(secrets, "AZURE_KEYVAULT_NAME", "kv-fieldcraft-test")

    class _BoomClient:
        def get_secret(self, name):
            raise RuntimeError("Key Vault unreachable")

    monkeypatch.setattr(secrets, "_get_kv_client", lambda: _BoomClient())
    monkeypatch.setenv("MONGODB_URI", "env-fallback-uri")
    assert secrets.get_secret("mongodb-uri", "MONGODB_URI") == "env-fallback-uri"


def test_client_init_failure_falls_back_to_env_permanently(monkeypatch):
    monkeypatch.setattr(secrets, "AZURE_KEYVAULT_NAME", "kv-fieldcraft-test")
    monkeypatch.setattr(secrets, "_kv_init_attempted", False)

    import azure.identity

    def _boom(*a, **k):
        raise RuntimeError("no managed identity available")

    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", _boom)
    monkeypatch.setenv("MONGODB_URI", "env-fallback-uri")
    assert secrets.get_secret("mongodb-uri", "MONGODB_URI") == "env-fallback-uri"
