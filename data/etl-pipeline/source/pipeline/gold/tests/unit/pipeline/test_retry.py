"""Unit tests for gold.pipeline.retry.with_retry and its use in tenant_discovery."""

from __future__ import annotations

import importlib

import psycopg2
import pytest

pytestmark = [pytest.mark.unit]


@pytest.fixture
def retry_mod(monkeypatch):
    m = importlib.import_module("gold.pipeline.retry")
    # never actually sleep in tests
    monkeypatch.setattr(m.time, "sleep", lambda *_a, **_k: None)
    return m


class TestWithRetry:
    def test_succeeds_first_try(self, retry_mod):
        calls = {"n": 0}

        # `exceptions` is required with no default (retry.py:33) — retrying
        # everything would swallow programming errors. The other tests in this
        # file already pass it; only this one predates the change.
        @retry_mod.with_retry(max_attempts=3, base_delay_ms=0, exceptions=(ValueError,))
        def f():
            calls["n"] += 1
            return "ok"

        assert f() == "ok"
        assert calls["n"] == 1

    def test_retries_then_succeeds(self, retry_mod):
        calls = {"n": 0}

        @retry_mod.with_retry(max_attempts=3, base_delay_ms=0, exceptions=(ValueError,))
        def f():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ValueError("transient")
            return "ok"

        assert f() == "ok"
        assert calls["n"] == 3

    def test_exhausts_and_reraises(self, retry_mod):
        calls = {"n": 0}

        @retry_mod.with_retry(max_attempts=2, base_delay_ms=0, exceptions=(ValueError,))
        def f():
            calls["n"] += 1
            raise ValueError("always")

        with pytest.raises(ValueError, match="always"):
            f()
        assert calls["n"] == 2  # no more than max_attempts

    def test_unlisted_exception_not_retried(self, retry_mod):
        calls = {"n": 0}

        @retry_mod.with_retry(max_attempts=3, base_delay_ms=0, exceptions=(ValueError,))
        def f():
            calls["n"] += 1
            raise KeyError("not transient")

        with pytest.raises(KeyError):
            f()
        assert calls["n"] == 1  # propagated immediately


class TestTenantDiscoveryRetry:
    def test_discover_retries_on_operational_error(self, monkeypatch):
        td = importlib.import_module("gold.pipeline.tenant_discovery")
        importlib.reload(td)
        monkeypatch.setattr("gold.pipeline.retry.time.sleep", lambda *_a, **_k: None)

        calls = {"n": 0}

        def boom(*_a, **_k):
            calls["n"] += 1
            raise psycopg2.OperationalError("connection reset")

        monkeypatch.setattr(td.psycopg2, "connect", boom)
        with pytest.raises(psycopg2.OperationalError):
            td.discover_tenants("postgresql://x")
        # retried up to the configured max (default 3) rather than failing once
        assert calls["n"] == td._RETRY_MAX
