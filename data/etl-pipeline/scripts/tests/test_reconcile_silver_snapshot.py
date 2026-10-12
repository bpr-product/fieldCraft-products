"""Unit tests for scripts/reconcile_silver_snapshot.py's source resolution.

Regression coverage for a real bug found live 2026-08-10: `_resolve_source()`
had no exception for the `tenant` entity (unlike
universal_silver_merge.py::_namespaced_entity(), which already special-cases
it), so every tenant's row was reconciled against ITS OWN tenant database's
local public.tenant copy instead of the provider's tenant registry -- the
actual, sole source of truth for silver.tenant (see CLAUDE.md's "Tenant
registry" section). No tenant's own DB has a matching row (different PK
values, different table entirely), so every tenant -- including genuinely
Active ones -- was concluded to be a "ghost" and soft-deleted
(_s_is_deleted=TRUE), silently breaking --all-tenants tenant discovery.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

_MODULE_PATH = Path(__file__).resolve().parents[1] / "reconcile_silver_snapshot.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("reconcile_silver_snapshot", _MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["reconcile_silver_snapshot"] = module
    spec.loader.exec_module(module)
    return module


def _load_module_no_env():
    with patch.dict("os.environ", {}, clear=False):
        return _load_module()


rec = _load_module_no_env()


def test_tenant_entity_resolves_to_provider_db_not_tenant_db():
    """The exact regression: for entity == 'tenant', source must be the
    provider DB, regardless of which tenant_id is being reconciled."""
    for tenant_id in ("tn59695547", "tn88351775", "tn22473627"):
        src_db, src_table = rec._resolve_source("tenant", tenant_id, provider_db="fieldcraft_provider")
        assert src_db == "fieldcraft_provider"
        assert src_table == "tenant"


def test_tenant_entity_never_resolves_to_the_tenant_id_itself():
    """Direct assertion against the buggy behaviour this replaces: the
    source DB must never be the tenant's own database for this entity."""
    src_db, _ = rec._resolve_source("tenant", "tn59695547", provider_db="fieldcraft_provider")
    assert src_db != "tn59695547"


def test_namespaced_provider_table_still_resolves_correctly():
    """Non-'tenant' namespaced tables (silver.fieldcraft_provider_foo) must be
    unaffected by the new tenant-specific branch."""
    src_db, src_table = rec._resolve_source("fieldcraft_provider_trade", "tn59695547", provider_db="fieldcraft_provider")
    assert (src_db, src_table) == ("fieldcraft_provider", "trade")


def test_regular_tenant_entity_still_resolves_to_tenant_db():
    """Ordinary tenant-scoped entities (project, task, ...) must be
    unaffected -- still resolve to the tenant's own database."""
    src_db, src_table = rec._resolve_source("project", "tn59695547", provider_db="fieldcraft_provider")
    assert (src_db, src_table) == ("tn59695547", "project")
