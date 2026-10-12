"""Shared fixtures + mocking helpers for the Silver → Gold unit test suite.

Every test in this package is a *pure unit test*:
  * no real PostgreSQL  — psycopg2 / iter_silver_rows are mocked
  * no real MongoDB     — pymongo MongoClient / collections are mocked
  * no real Key Vault   — secret_resolver env is stubbed (root conftest)
  * no embedding service — a deterministic StubEmbedder is injected

The loaders all share the same skeleton (LoaderConfig, _mask_uri,
iter_silver_rows, shape_doc, bulk_write_with_retry, run, main), so the
mocking helpers here are written generically and bound to a *module* under
test.  Each helper monkeypatches attributes on the loader module itself —
this is correct because every loader does
``from gold.pipeline.cursor_store import read_cursor, ...`` and
``from pymongo import MongoClient`` at import time, binding those names into
the module namespace.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import ModuleType
from typing import Any, Iterable
from unittest.mock import MagicMock

import pytest

UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
# A stable "now" used as gold_ts across shape_doc tests.
GOLD_TS = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
# A representative silver watermark a few seconds before gold_ts.
SILVER_TS = datetime(2026, 6, 1, 11, 59, 58, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Deterministic embedding provider
# ---------------------------------------------------------------------------


class StubEmbedder:
    """Deterministic, offline embedding provider matching EmbeddingProvider."""

    name = "stub"
    version = "test-0"

    def __init__(self, dim: int = 4):
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t) % 7)] * self.dim for t in texts]


# ---------------------------------------------------------------------------
# LoaderConfig factory — works for any loader's dataclass shape
# ---------------------------------------------------------------------------


def make_cfg(mod: ModuleType, **overrides: Any):
    """Build a LoaderConfig for ``mod`` filling only the fields it declares."""
    fields = mod.LoaderConfig.__dataclass_fields__
    base: dict[str, Any] = {
        "mongo_uri": "mongodb://localhost:27017",
        "pg_dsn": "postgresql://localhost/fieldcraft_silver_test",
        "db_name": "gold_test",
        "tenantid": "DEMO-001",
        "batch_size": 2,
        "max_retries": 3,
        "retry_backoff_s": 0.0,  # no real sleeping in retry tests
        "dry_run": True,
    }
    if "max_docs" in fields:
        base["max_docs"] = 0
    base.update(overrides)
    # Drop any keys the dataclass does not accept.
    base = {k: v for k, v in base.items() if k in fields}
    return mod.LoaderConfig(**base)


# ---------------------------------------------------------------------------
# psycopg2 cursor mock (for iter_silver_rows / *_silver_ts helpers)
# ---------------------------------------------------------------------------


def make_pg_cursor(rows: Iterable[dict] | None = None, fetchone=None):
    """A MagicMock psycopg2 cursor usable as both context manager and iterator."""
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    cur.__iter__ = MagicMock(return_value=iter(list(rows or [])))
    cur.execute = MagicMock()
    if fetchone is not None:
        cur.fetchone = MagicMock(return_value=fetchone)
    return cur


def make_pg_conn(cur):
    """A MagicMock psycopg2 connection wrapping ``cur``.

    Supports both styles used in the loaders:
      * ``conn.cursor()`` returning a context-managed cursor (iter_silver_rows)
      * ``with psycopg2.connect(...) as conn, conn.cursor() as cur`` (helpers)
    """
    conn = MagicMock()
    conn.cursor = MagicMock(return_value=cur)
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.close = MagicMock()
    conn.commit = MagicMock()
    return conn


@pytest.fixture
def patch_pg(monkeypatch):
    """Return a function that patches ``mod.psycopg2.connect`` to yield rows."""

    def _patch(mod: ModuleType, rows: Iterable[dict] | None = None, fetchone=None):
        cur = make_pg_cursor(rows, fetchone=fetchone)
        conn = make_pg_conn(cur)
        monkeypatch.setattr(mod.psycopg2, "connect", MagicMock(return_value=conn))
        return conn, cur

    return _patch


# ---------------------------------------------------------------------------
# pymongo collection / client mock
# ---------------------------------------------------------------------------


def make_bulk_result(upserted=0, modified=0, matched=0):
    res = MagicMock()
    res.upserted_count = upserted
    res.modified_count = modified
    res.matched_count = matched
    return res


def make_collection(bulk_result=None, count=0, find_one=None):
    coll = MagicMock()
    coll.full_name = "gold_test.collection"
    coll.bulk_write = MagicMock(return_value=bulk_result or make_bulk_result(upserted=1))
    coll.count_documents = MagicMock(return_value=count)
    coll.find_one = MagicMock(return_value=find_one)
    return coll


@pytest.fixture
def patch_mongo(monkeypatch):
    """Patch ``mod.MongoClient`` so any DB/collection access returns mocks.

    Returns the shared collection mock so tests can assert on bulk_write etc.
    """

    def _patch(mod: ModuleType, coll=None, find_one=None):
        coll = coll or make_collection(find_one=find_one)
        client = MagicMock()
        client.__getitem__.return_value.__getitem__.return_value = coll
        client.close = MagicMock()
        monkeypatch.setattr(mod, "MongoClient", MagicMock(return_value=client))
        return coll

    return _patch


# ---------------------------------------------------------------------------
# cursor_store mock (read/write/mark_error are bound into each loader module)
# ---------------------------------------------------------------------------


@pytest.fixture
def patch_cursor(monkeypatch):
    """Patch the cursor_store functions bound into a loader module."""

    def _patch(mod: ModuleType, last_ts: datetime = EPOCH):
        if hasattr(mod, "read_cursor"):
            monkeypatch.setattr(mod, "read_cursor", MagicMock(return_value=last_ts))
        if hasattr(mod, "write_cursor"):
            monkeypatch.setattr(mod, "write_cursor", MagicMock())
        if hasattr(mod, "mark_error"):
            monkeypatch.setattr(mod, "mark_error", MagicMock())
        return mod

    return _patch


@pytest.fixture
def patch_contract(monkeypatch):
    """Replace a loader's module-level _contract_validator with a no-op mock."""

    def _patch(mod: ModuleType):
        if hasattr(mod, "_contract_validator"):
            monkeypatch.setattr(mod, "_contract_validator", MagicMock())
        return mod

    return _patch


@pytest.fixture(autouse=True)
def reset_circuit_breaker(request):
    """Reset the module-level circuit breaker singleton between tests.

    Loaders cache a CircuitBreaker in ``mod._cb``; a tripped breaker would
    leak across tests, so clear it before and after each test for any loader
    module that has been imported.
    """
    import sys

    def _clear():
        for name, mod in list(sys.modules.items()):
            if name.startswith("gold.src.") and hasattr(mod, "_cb"):
                try:
                    mod._cb = None
                except Exception:
                    pass

    _clear()
    yield
    _clear()
