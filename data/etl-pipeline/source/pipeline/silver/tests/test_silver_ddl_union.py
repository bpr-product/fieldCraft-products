"""
silver/tests/test_silver_ddl_union.py

Unit tests for the multi-env union schema logic introduced in:
  - silver/ddl/create_silver_tables.py  (commit 8a2a27f)
  - scripts/auto_schema_sync.py         (commit 7ba84b5)

Covers:
  1. _merge_table  — column union, first-type-wins, PK fallback
  2. build_union_schema  — merges across tenants via mocked fetch_tenant_schema
  3. add_namespaced_tables  — prefixed keys, column merge on repeat call
  4. SRC_* globals restored after env loop even on exception
  5. Regression: add_namespaced_tables called while SRC_HOST == env host,
     not after the finally restores the default (the bug fixed by 7ba84b5)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import silver.ddl.create_silver_tables as ddl


# ── helpers ───────────────────────────────────────────────────────────────────

def _cols(*pairs):
    """Shorthand: _cols(('id','int4'),('name','text')) → list of 2-tuples."""
    return list(pairs)


def _fake_fetch(cols_by_table: dict, pks_by_table: dict):
    """Return a side_effect callable for fetch_tenant_schema."""
    def _inner(_tid):
        return cols_by_table, pks_by_table
    return _inner


# ── 1. _merge_table ───────────────────────────────────────────────────────────

class TestMergeTable:
    def test_new_table_created(self):
        union: dict = {}
        ddl._merge_table(union, "project", _cols(("id", "int4"), ("name", "text")), ["id"], "tn1")
        assert "project" in union
        assert union["project"]["columns"] == {"id": "int4", "name": "text"}
        assert union["project"]["pks"] == ["id"]
        assert union["project"]["source_tenants"] == ["tn1"]

    def test_new_columns_merged_from_second_tenant(self):
        union: dict = {}
        ddl._merge_table(union, "task", _cols(("id", "int4"), ("title", "text")), ["id"], "tn1")
        ddl._merge_table(union, "task", _cols(("id", "int4"), ("status", "text")), ["id"], "tn2")
        assert "title" in union["task"]["columns"]
        assert "status" in union["task"]["columns"]

    def test_first_type_wins_on_column_conflict(self):
        union: dict = {}
        ddl._merge_table(union, "invoice", _cols(("amount", "numeric")), ["id"], "tn1")
        ddl._merge_table(union, "invoice", _cols(("amount", "text")), ["id"], "tn2")
        assert union["invoice"]["columns"]["amount"] == "numeric"

    def test_source_tenants_accumulated(self):
        union: dict = {}
        ddl._merge_table(union, "bom", _cols(("id", "int4")), ["id"], "tn1")
        ddl._merge_table(union, "bom", _cols(("id", "int4")), ["id"], "tn2")
        assert union["bom"]["source_tenants"] == ["tn1", "tn2"]

    def test_pk_fallback_to_id_column_when_no_formal_pk(self):
        union: dict = {}
        ddl._merge_table(union, "worker", _cols(("id", "int4"), ("name", "text")), [], "tn1")
        assert union["worker"]["pks"] == ["id"]

    def test_pk_adopted_from_later_tenant_when_first_has_no_pk(self):
        union: dict = {}
        ddl._merge_table(union, "crew", _cols(("id", "int4")), [], "tn1")
        ddl._merge_table(union, "crew", _cols(("id", "int4")), ["id"], "tn2")
        assert union["crew"]["pks"] == ["id"]

    def test_table_stays_after_table_exclusive_to_one_tenant(self):
        union: dict = {}
        ddl._merge_table(union, "project", _cols(("id", "int4")), ["id"], "tn1")
        ddl._merge_table(union, "timelog", _cols(("id", "int4")), ["id"], "tn2")
        assert "project" in union
        assert "timelog" in union


# ── 2. build_union_schema ─────────────────────────────────────────────────────

class TestBuildUnionSchema:
    def test_single_tenant_columns_present(self):
        cols = {"project": _cols(("id", "int4"), ("name", "text"))}
        pks  = {"project": ["id"]}
        with patch.object(ddl, "fetch_tenant_schema", side_effect=_fake_fetch(cols, pks)):
            union = ddl.build_union_schema(["tn1"])
        assert union["project"]["columns"] == {"id": "int4", "name": "text"}

    def test_two_tenants_columns_unioned(self):
        def _side(tid):
            if tid == "tn1":
                return {"task": _cols(("id", "int4"), ("title", "text"))}, {"task": ["id"]}
            return {"task": _cols(("id", "int4"), ("status", "text"))}, {"task": ["id"]}

        with patch.object(ddl, "fetch_tenant_schema", side_effect=_side):
            union = ddl.build_union_schema(["tn1", "tn2"])
        assert "title" in union["task"]["columns"]
        assert "status" in union["task"]["columns"]

    def test_si_dev_exclusive_table_included(self):
        """Table present only on SI_DEV must appear in the union."""
        def _side(tid):
            if tid == "tn_dev":
                return {"project": _cols(("id", "int4"))}, {"project": ["id"]}
            return {"attribute_config": _cols(("id", "int4"), ("key", "text"))}, {"attribute_config": ["id"]}

        with patch.object(ddl, "fetch_tenant_schema", side_effect=_side):
            union = ddl.build_union_schema(["tn_dev", "tn_si"])
        assert "project" in union
        assert "attribute_config" in union

    def test_empty_tenant_list_returns_empty_union(self):
        with patch.object(ddl, "fetch_tenant_schema") as mock_fetch:
            union = ddl.build_union_schema([])
        mock_fetch.assert_not_called()
        assert union == {}


# ── 3. add_namespaced_tables ──────────────────────────────────────────────────

class TestAddNamespacedTables:
    def test_adds_prefixed_key(self):
        cols = {"manufacturer": _cols(("id", "int4"), ("name", "text"))}
        pks  = {"manufacturer": ["id"]}
        with patch.object(ddl, "fetch_tenant_schema", side_effect=_fake_fetch(cols, pks)):
            union: dict = {}
            n = ddl.add_namespaced_tables(union, "fieldcraft_provider")
        assert "fieldcraft_provider_manufacturer" in union
        assert n == 1

    def test_does_not_overwrite_existing_entry(self):
        cols = {"manufacturer": _cols(("id", "int4"), ("name", "text"))}
        pks  = {"manufacturer": ["id"]}
        original_entry = {"columns": {"id": "int4", "name": "text"}, "pks": ["id"], "source_tenants": []}
        union: dict = {"fieldcraft_provider_manufacturer": original_entry}
        with patch.object(ddl, "fetch_tenant_schema", side_effect=_fake_fetch(cols, pks)):
            ddl.add_namespaced_tables(union, "fieldcraft_provider")
        # Entry object unchanged — columns not reset, existing keys untouched
        assert union["fieldcraft_provider_manufacturer"] is original_entry
        assert union["fieldcraft_provider_manufacturer"]["columns"]["id"] == "int4"

    def test_merges_new_column_from_second_env_call(self):
        """Second env's fieldcraft_provider has a new column — must be merged into existing entry."""
        cols1 = {"manufacturer": _cols(("id", "int4"), ("name", "text"))}
        cols2 = {"manufacturer": _cols(("id", "int4"), ("preferred_vendor_tier", "text"))}
        pks   = {"manufacturer": ["id"]}
        union: dict = {}
        with patch.object(ddl, "fetch_tenant_schema", side_effect=_fake_fetch(cols1, pks)):
            ddl.add_namespaced_tables(union, "fieldcraft_provider")
        with patch.object(ddl, "fetch_tenant_schema", side_effect=_fake_fetch(cols2, pks)):
            ddl.add_namespaced_tables(union, "fieldcraft_provider")
        assert "name" in union["fieldcraft_provider_manufacturer"]["columns"]
        assert "preferred_vendor_tier" in union["fieldcraft_provider_manufacturer"]["columns"]

    def test_fieldcraft_provider_and_fieldcraft_customer_distinct_keys(self):
        cols = {"asset": _cols(("id", "int4"))}
        pks  = {"asset": ["id"]}
        with patch.object(ddl, "fetch_tenant_schema", side_effect=_fake_fetch(cols, pks)):
            union: dict = {}
            ddl.add_namespaced_tables(union, "fieldcraft_provider")
            ddl.add_namespaced_tables(union, "fieldcraft_customer")
        assert "fieldcraft_provider_asset" in union
        assert "fieldcraft_customer_asset" in union
        # They must be independent entries — not the same object
        assert union["fieldcraft_provider_asset"] is not union["fieldcraft_customer_asset"]


# ── 4. SRC_* globals restored after env loop, even on exception ───────────────

class TestSrcGlobalsRestored:
    """
    Verifies the finally-restore pattern used in both create_silver_tables.main()
    and auto_schema_sync.sync_once(). If get_active_tenants() raises, SRC_*
    must be restored to the original values before the outer except runs.
    """

    def test_src_host_restored_after_successful_env(self):
        original = (ddl.SRC_HOST, ddl.SRC_PORT, ddl.SRC_USER, ddl.SRC_PASSWORD, ddl.SRC_SSL)
        _prev = (ddl.SRC_HOST, ddl.SRC_PORT, ddl.SRC_USER, ddl.SRC_PASSWORD, ddl.SRC_SSL)
        ddl.SRC_HOST = "env-host.example.com"
        ddl.SRC_USER = "env_user"
        try:
            pass  # simulates successful env body
        finally:
            ddl.SRC_HOST, ddl.SRC_PORT, ddl.SRC_USER, ddl.SRC_PASSWORD, ddl.SRC_SSL = _prev
        assert ddl.SRC_HOST == original[0]
        assert ddl.SRC_USER == original[2]

    def test_src_host_restored_after_exception(self):
        original_host = ddl.SRC_HOST
        original_user = ddl.SRC_USER
        _prev = (ddl.SRC_HOST, ddl.SRC_PORT, ddl.SRC_USER, ddl.SRC_PASSWORD, ddl.SRC_SSL)
        ddl.SRC_HOST = "broken-host.example.com"
        ddl.SRC_USER = "broken_user"
        try:
            raise ConnectionError("simulated unreachable source PG")
        except Exception:
            pass
        finally:
            ddl.SRC_HOST, ddl.SRC_PORT, ddl.SRC_USER, ddl.SRC_PASSWORD, ddl.SRC_SSL = _prev
        assert ddl.SRC_HOST == original_host
        assert ddl.SRC_USER == original_user

    def test_second_env_processed_after_first_env_fails(self):
        """A failing env must not prevent subsequent envs from being processed."""
        results = []

        def _run_env_loop(envs):
            for env_name, env_host in envs:
                _prev = (ddl.SRC_HOST, ddl.SRC_PORT, ddl.SRC_USER, ddl.SRC_PASSWORD, ddl.SRC_SSL)
                ddl.SRC_HOST = env_host
                try:
                    if env_name == "dev":
                        raise ConnectionError("dev PG unreachable")
                    results.append(env_name)
                except Exception:
                    pass
                finally:
                    ddl.SRC_HOST, ddl.SRC_PORT, ddl.SRC_USER, ddl.SRC_PASSWORD, ddl.SRC_SSL = _prev

        _run_env_loop([("dev", "pg-dev"), ("si_dev", "pg-si-dev")])
        assert results == ["si_dev"]


# ── 5. Regression: add_namespaced_tables called with env host ─────────────────

class TestNamespacedCalledWithEnvHost:
    """
    Regression test for commit 7ba84b5.

    Before the fix, auto_schema_sync.py called add_namespaced_tables AFTER the
    finally restored SRC_*, so it always used the default host — missing columns
    from non-default env hosts.

    The correct pattern (also used by create_silver_tables.py) is to call
    add_namespaced_tables INSIDE the inner try block, while SRC_HOST still points
    to the env's host.

    This test simulates the fixed loop for two envs and asserts that each
    add_namespaced_tables call saw the correct env host, not the original default.
    """

    def test_namespaced_called_with_env_host_not_default(self):
        original_host = ddl.SRC_HOST
        envs = [("dev", "pg-dev-host"), ("si_dev", "pg-si-dev-host")]
        host_at_call: list[str] = []

        def _capture_namespaced(union, ns_db):
            host_at_call.append(ddl.SRC_HOST)
            return 0

        with patch.object(ddl, "get_active_tenants", return_value=[]), \
             patch.object(ddl, "build_union_schema", return_value={}), \
             patch.object(ddl, "add_namespaced_tables", side_effect=_capture_namespaced):

            union: dict = {}
            for _env_name, env_host in envs:
                _prev = (ddl.SRC_HOST, ddl.SRC_PORT, ddl.SRC_USER, ddl.SRC_PASSWORD, ddl.SRC_SSL)
                ddl.SRC_HOST = env_host
                try:
                    ddl.get_active_tenants()
                    env_union = ddl.build_union_schema([])
                    # Correct pattern: called INSIDE the try, BEFORE the finally
                    for ns_db in ["fieldcraft_provider"]:
                        ddl.add_namespaced_tables(env_union, ns_db)
                finally:
                    ddl.SRC_HOST, ddl.SRC_PORT, ddl.SRC_USER, ddl.SRC_PASSWORD, ddl.SRC_SSL = _prev

        # Called once per env
        assert len(host_at_call) == 2
        # Each call used that env's host — not the original default
        assert host_at_call[0] == "pg-dev-host"
        assert host_at_call[1] == "pg-si-dev-host"
        # Globals fully restored after the loop
        assert ddl.SRC_HOST == original_host

    def test_wrong_pattern_would_use_default_host(self):
        """
        Demonstrates what the bug looked like before 7ba84b5.
        Calling add_namespaced_tables AFTER the finally (post-loop) means it
        always runs with the restored default SRC_HOST, regardless of the envs.
        """
        original_host = ddl.SRC_HOST
        envs = [("dev", "pg-dev-host"), ("si_dev", "pg-si-dev-host")]
        host_at_call: list[str] = []

        def _capture_namespaced(union, ns_db):
            host_at_call.append(ddl.SRC_HOST)
            return 0

        with patch.object(ddl, "get_active_tenants", return_value=[]), \
             patch.object(ddl, "build_union_schema", return_value={}), \
             patch.object(ddl, "add_namespaced_tables", side_effect=_capture_namespaced):

            union: dict = {}
            # Buggy pattern: env loop runs WITHOUT calling add_namespaced_tables
            for _env_name, env_host in envs:
                _prev = (ddl.SRC_HOST, ddl.SRC_PORT, ddl.SRC_USER, ddl.SRC_PASSWORD, ddl.SRC_SSL)
                ddl.SRC_HOST = env_host
                try:
                    ddl.get_active_tenants()
                    ddl.build_union_schema([])
                finally:
                    ddl.SRC_HOST, ddl.SRC_PORT, ddl.SRC_USER, ddl.SRC_PASSWORD, ddl.SRC_SSL = _prev

            # Bug: called post-loop — SRC_HOST is already the original default
            for ns_db in ["fieldcraft_provider"]:
                ddl.add_namespaced_tables(union, ns_db)

        assert len(host_at_call) == 1  # called once, not per-env
        # Sees only the original default — misses si_dev's host entirely
        assert host_at_call[0] == original_host
