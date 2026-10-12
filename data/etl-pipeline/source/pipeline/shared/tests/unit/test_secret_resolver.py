"""Unit tests for shared/secret_resolver.py.

Tests cover:
  - Environment variable fallback (no Key Vault)
  - Missing env var raises RuntimeError
  - Fail-closed env-var-fallback gate (ETL_ENV != local and CI unset)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SHARED = str(_ROOT / "shared")
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)

import secret_resolver  # noqa: E402


class TestGetMongoUri:
    def test_returns_env_var_when_set(self, monkeypatch):
        monkeypatch.delenv("KEY_VAULT_URL", raising=False)
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        assert secret_resolver.get_mongo_uri() == "mongodb://localhost:27017"

    def test_raises_when_no_source(self, monkeypatch):
        monkeypatch.delenv("KEY_VAULT_URL", raising=False)
        monkeypatch.delenv("MONGO_URI", raising=False)
        with pytest.raises(RuntimeError):
            secret_resolver.get_mongo_uri()


class TestGetPostgresDsn:
    def test_returns_env_var_when_set(self, monkeypatch):
        monkeypatch.delenv("KEY_VAULT_URL", raising=False)
        monkeypatch.setenv("SILVER_PG_DSN", "postgresql://localhost/test")
        assert secret_resolver.get_postgres_dsn() == "postgresql://localhost/test"

    def test_raises_when_no_source(self, monkeypatch):
        monkeypatch.delenv("KEY_VAULT_URL", raising=False)
        monkeypatch.delenv("SILVER_PG_DSN", raising=False)
        with pytest.raises(RuntimeError):
            secret_resolver.get_postgres_dsn()


class TestGetRedisUri:
    def test_returns_env_var_when_set(self, monkeypatch):
        monkeypatch.delenv("KEY_VAULT_URL", raising=False)
        monkeypatch.setenv("REDIS_URI", "redis://localhost:6379")
        assert secret_resolver.get_redis_uri() == "redis://localhost:6379"

    def test_raises_when_no_source(self, monkeypatch):
        monkeypatch.delenv("KEY_VAULT_URL", raising=False)
        monkeypatch.delenv("REDIS_URI", raising=False)
        with pytest.raises(RuntimeError):
            secret_resolver.get_redis_uri()


class TestGetAzureOpenAIKey:
    def test_returns_env_var_when_set(self, monkeypatch):
        monkeypatch.delenv("KEY_VAULT_URL", raising=False)
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key-123")
        assert secret_resolver.get_azure_openai_key() == "test-key-123"

    def test_raises_when_no_source(self, monkeypatch):
        monkeypatch.delenv("KEY_VAULT_URL", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        with pytest.raises(RuntimeError):
            secret_resolver.get_azure_openai_key()


class TestEnvFallbackGate:
    """secret_resolver.get_secret()'s fail-closed gate: the plaintext
    env-var fallback is only permitted when ETL_ENV=local, or CI is truthy —
    every other tier must set KEY_VAULT_URL instead of silently succeeding
    off a plaintext env var. See secret_resolver.get_secret()'s ETL_ENV/CI
    check just before it raises the "refusing plaintext env-var fallback" error.
    """

    def test_blocked_outside_local_without_ci(self, monkeypatch):
        monkeypatch.delenv("KEY_VAULT_URL", raising=False)
        monkeypatch.setenv("ETL_ENV", "staging")
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        with pytest.raises(RuntimeError, match="refusing plaintext env-var fallback"):
            secret_resolver.get_mongo_uri()

    def test_blocked_in_prod_even_with_value_present(self, monkeypatch):
        monkeypatch.delenv("KEY_VAULT_URL", raising=False)
        monkeypatch.setenv("ETL_ENV", "prod")
        monkeypatch.setenv("CI", "false")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        with pytest.raises(RuntimeError, match="refusing plaintext env-var fallback"):
            secret_resolver.get_mongo_uri()

    def test_allowed_when_etl_env_local(self, monkeypatch):
        monkeypatch.delenv("KEY_VAULT_URL", raising=False)
        monkeypatch.setenv("ETL_ENV", "local")
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        assert secret_resolver.get_mongo_uri() == "mongodb://localhost:27017"

    def test_allowed_outside_local_when_ci_true(self, monkeypatch):
        monkeypatch.delenv("KEY_VAULT_URL", raising=False)
        monkeypatch.setenv("ETL_ENV", "staging")
        monkeypatch.setenv("CI", "true")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        assert secret_resolver.get_mongo_uri() == "mongodb://localhost:27017"

    def test_etl_env_defaults_to_local_when_unset(self, monkeypatch):
        monkeypatch.delenv("KEY_VAULT_URL", raising=False)
        monkeypatch.delenv("ETL_ENV", raising=False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        assert secret_resolver.get_mongo_uri() == "mongodb://localhost:27017"
