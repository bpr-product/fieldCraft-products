"""Unit tests for gold.pipeline.tenant_discovery roster-based discovery.

Covers the silver.tenant active-roster query: active filter, soft-delete
exclusion, case-insensitive status match, and DISTINCT dedup of the multiple
source rows that share one _s_tenantid.
"""

from __future__ import annotations

import importlib

import pytest

pytestmark = [pytest.mark.unit]


class _FakeCursor:
    """Minimal psycopg2 cursor stand-in that records SQL and replays rows."""

    def __init__(self, rows):
        self._rows = rows
        self.executed_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def execute(self, sql, *_a, **_k):
        self.executed_sql = sql

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, rows):
        self.cursor_obj = _FakeCursor(rows)
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


@pytest.fixture
def td():
    mod = importlib.import_module("gold.pipeline.tenant_discovery")
    importlib.reload(mod)
    return mod


def _patch_connect(monkeypatch, td, rows):
    conn = _FakeConn(rows)
    monkeypatch.setattr(td.psycopg2, "connect", lambda *_a, **_k: conn)
    return conn


class TestRosterDiscovery:
    def test_returns_distinct_tenant_ids_from_roster(self, td, monkeypatch):
        # The DB returns rows already DISTINCT + ORDER BY (query enforces both);
        # discovery passes them through unchanged.
        rows = [("tn35643604",), ("tn70619321",), ("tn78010693",), ("tn87639573",)]
        _patch_connect(monkeypatch, td, rows)

        result = td.discover_tenants("postgresql://x")

        assert result == ["tn35643604", "tn70619321", "tn78010693", "tn87639573"]

    def test_query_targets_active_roster_with_correct_filters(self, td, monkeypatch):
        conn = _patch_connect(monkeypatch, td, [("tn1",)])

        td.discover_tenants("postgresql://x")

        sql = " ".join(conn.cursor_obj.executed_sql.split()).lower()
        # source of truth is the tenant roster, not project/invoice/task
        assert "from silver.tenant" in sql
        assert "silver.project" not in sql
        # active, non-deleted, deduped, deterministic order
        assert "_s_is_deleted = false" in sql
        assert "lower(status) = 'active'" in sql
        # silver.tenant is sourced from the non-tenant-scoped provider DB, so
        # its _s_tenantid CDC column is always the literal "fieldcraft_provider" for
        # every row -- the real tenant identifier is the business column
        # tenant_id (see discover_tenants()'s docstring).
        assert "select distinct tenant_id" in sql
        assert "order by tenant_id" in sql

    def test_empty_roster_returns_empty_list(self, td, monkeypatch):
        _patch_connect(monkeypatch, td, [])

        assert td.discover_tenants("postgresql://x") == []

    def test_connection_always_closed(self, td, monkeypatch):
        conn = _patch_connect(monkeypatch, td, [("tn1",)])

        td.discover_tenants("postgresql://x")

        assert conn.closed is True
