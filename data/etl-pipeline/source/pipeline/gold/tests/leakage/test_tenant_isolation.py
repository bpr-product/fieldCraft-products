"""Cross-tenant data leakage checks for the Gold-layer Silver->Gold loaders.

This suite guards the one property every Silver->Gold loader must hold: a
pull for tenant A must never return, or even query for, tenant B's rows.

Two complementary checks, both runnable without a live Silver PostgreSQL or
Mongo connection (so they run as real `pytest.mark.leakage` coverage, not a
skipped/no-op placeholder):

  1. TestQueryIsTenantScoped — static check. Every batch loader's Silver SQL
     (whether a module-level `_SILVER_QUERY` constant or built by a
     `_build_silver_query()`-style function) must contain a `_s_tenantid`
     predicate bound to the `%(tenantid)s` parameter. This is the actual
     enforcement point: a loader that forgets this WHERE clause returns
     every tenant's rows to whichever tenant happens to be running.

  2. TestIterSilverRowsTenantScoping — behavioural check against a mocked
     psycopg2 connection. Calls each loader's `iter_silver_rows()` once per
     tenant and asserts the SQL parameters actually executed carry that
     exact tenant id — not the other tenant's, and not a stale/cached value
     left over from a previous call.

`load_question.py` is intentionally excluded from both checks: canonical
question/template rows are tenant-agnostic reference data (see its
`iter_silver_rows(pg_dsn, max_docs)` signature, which takes no tenantid),
not a leakage surface.
"""

from __future__ import annotations

import importlib
import inspect
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

pytestmark = [pytest.mark.leakage]

_GOLD_SRC = Path(__file__).resolve().parents[2] / "src"

# Tenant-scoped batch loaders that take no extra positional/keyword args
# beyond (pg_dsn, tenantid, max_docs=...).
_STANDARD_LOADER_MODULES = [
    "gold.src.bpe.load_estimate_snapshot",
    "gold.src.canonical.load_answer",
    "gold.src.canonical.load_asset",
    "gold.src.canonical.load_communication_log",
    "gold.src.canonical.load_customer",
    "gold.src.canonical.load_project",
    "gold.src.canonical.load_tenant",
    "gold.src.canonical.load_user_permissions",
    "gold.src.eden.load_bid_context",
    "gold.src.eden.load_bom_stock_gap",
    "gold.src.eden.load_estimate_price_evidence",
    "gold.src.eden.load_invoice_summary",
    "gold.src.eden.load_procurement_status",
    "gold.src.eden.load_project_health",
    "gold.src.eden.load_schedule_move_evidence",
    "gold.src.eden.load_task_intelligence",
    "gold.src.eden.load_weather_schedule_cross",
    "gold.src.eden.load_workforce_status",
    "gold.src.reference_project.load_qa_recommendation",
]

# load_task_timeline.iter_silver_rows has a required positional `since` arg
# in addition to (pg_dsn, tenantid) — handled separately below.
_SINCE_LOADER_MODULE = "gold.src.eden.load_task_timeline"

# load_tenant_dashboard exposes fetch_tenant_rows(pg_dsn, tenantid) -> list,
# not the iter_silver_rows(...) -> Iterator generator shape every other
# loader uses — its Silver SQL is still tenant-scoped (covered by the static
# check below), but needs its own fetchall()-style behavioural test.
_FETCH_TENANT_ROWS_MODULE = "gold.src.eden.load_tenant_dashboard"

# load_knowledge_chunk reads from a flat source file (iter_source_records),
# not a Silver SQL pull, so it has no _s_tenantid predicate to check. Its
# tenant boundary is enforced at the Mongo write layer instead: shape_doc()
# stamps the requested tenantId into every doc, and the upsert filter (and
# the stale-chunk cleanup filter) are keyed on {"tenantId": cfg.tenantid, ...}
# — checked separately below.
_MONGO_FILTER_SCOPED_MODULES = {"gold.src.eden.load_knowledge_chunk"}

# Reference data — no per-tenant filtering by design (see module docstring).
_TENANT_AGNOSTIC_MODULES = {"gold.src.canonical.load_question"}

# Loaders reading a shared, namespaced source table (e.g. silver.fieldcraft_customer_*)
# whose _s_tenantid is a constant pseudo-tenant, so the tenant boundary is the
# row's business tenant_id column instead. Same (pg_dsn, tenantid) signature
# as the standard loaders, so they share the behavioural check below.
_BUSINESS_TENANT_COL_MODULES = ["gold.src.canonical.load_lead"]

_TENANT_SCOPE_RE = re.compile(r"_s_tenantid\s*=\s*%\(tenantid\)s")
_BUSINESS_TENANT_SCOPE_RE = re.compile(r"\btenant_id\s*=\s*%\(tenantid\)s")
_MONGO_TENANT_FILTER_RE = re.compile(r'"tenantId"\s*:\s*cfg\.tenantid')


def _make_conn(rows):
    """Mocked psycopg2 connection whose cursor yields `rows` and records
    every execute() call for inspection."""
    cur = MagicMock()
    cur.__iter__ = MagicMock(return_value=iter(rows))
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


class TestQueryIsTenantScoped:
    """Static check: every tenant-scoped loader's Silver SQL text must
    filter on _s_tenantid = %(tenantid)s. Catches a loader whose query was
    written (or edited) without a tenant filter, which would return every
    tenant's data to whichever tenant happens to be running the pull.
    """

    @pytest.mark.parametrize(
        "module_path",
        _STANDARD_LOADER_MODULES + [_SINCE_LOADER_MODULE, _FETCH_TENANT_ROWS_MODULE],
    )
    def test_module_source_scopes_by_tenant(self, module_path):
        mod = importlib.import_module(module_path)
        source = inspect.getsource(mod)
        assert _TENANT_SCOPE_RE.search(source), (
            f"{module_path}: no '_s_tenantid = %(tenantid)s' predicate found in "
            "the module source — this loader's Silver query may not be tenant-scoped"
        )

    @pytest.mark.parametrize("module_path", _BUSINESS_TENANT_COL_MODULES)
    def test_module_source_scopes_by_business_tenant_column(self, module_path):
        mod = importlib.import_module(module_path)
        source = inspect.getsource(mod)
        assert _BUSINESS_TENANT_SCOPE_RE.search(source), (
            f"{module_path}: no 'tenant_id = %(tenantid)s' predicate found in "
            "the module source — this loader's Silver query may not be tenant-scoped"
        )

    @pytest.mark.parametrize("module_path", sorted(_MONGO_FILTER_SCOPED_MODULES))
    def test_module_source_scopes_mongo_writes_by_tenant(self, module_path):
        mod = importlib.import_module(module_path)
        source = inspect.getsource(mod)
        assert _MONGO_TENANT_FILTER_RE.search(source), (
            f"{module_path}: no '\"tenantId\": cfg.tenantid' Mongo filter found — "
            "this loader's upsert/cleanup filters may not be tenant-scoped"
        )

    def test_every_load_module_is_covered_or_explicitly_excluded(self):
        """Fails if a new gold/src/**/load_*.py file is added without being
        triaged into one of the categories above — prevents this suite from
        silently going stale as loaders are added.
        """
        covered = (
            set(_STANDARD_LOADER_MODULES)
            | set(_BUSINESS_TENANT_COL_MODULES)
            | {_SINCE_LOADER_MODULE, _FETCH_TENANT_ROWS_MODULE}
            | _MONGO_FILTER_SCOPED_MODULES
            | _TENANT_AGNOSTIC_MODULES
        )

        found = set()
        for path in _GOLD_SRC.rglob("load_*.py"):
            rel = path.relative_to(_GOLD_SRC).with_suffix("")
            found.add("gold.src." + ".".join(rel.parts))

        untriaged = found - covered
        assert not untriaged, (
            f"New loader module(s) not covered by the tenant-isolation leakage "
            f"suite: {sorted(untriaged)} — triage into _STANDARD_LOADER_MODULES, "
            f"_FETCH_TENANT_ROWS_MODULE, _MONGO_FILTER_SCOPED_MODULES, or "
            f"_TENANT_AGNOSTIC_MODULES in gold/tests/leakage/test_tenant_isolation.py"
        )


class TestIterSilverRowsTenantScoping:
    """Behavioural check against a mocked psycopg2 connection: a call for
    tenant A must execute SQL parameterised with tenant A's id, never
    tenant B's — even when B's pull ran moments earlier against the same
    mocked connection object.
    """

    @pytest.mark.parametrize(
        "module_path", _STANDARD_LOADER_MODULES + _BUSINESS_TENANT_COL_MODULES,
    )
    def test_execute_params_match_requested_tenant(self, module_path):
        mod = importlib.import_module(module_path)
        conn, cur = _make_conn([])

        with patch("psycopg2.connect", return_value=conn):
            list(mod.iter_silver_rows("dsn", "T-TEST-TENANT-A"))
            call_a = cur.execute.call_args
            list(mod.iter_silver_rows("dsn", "T-TEST-TENANT-B"))
            call_b = cur.execute.call_args

        params_a = call_a.args[1] if len(call_a.args) > 1 else call_a.kwargs.get("vars")
        params_b = call_b.args[1] if len(call_b.args) > 1 else call_b.kwargs.get("vars")

        assert isinstance(params_a, dict) and params_a.get("tenantid") == "T-TEST-TENANT-A", (
            f"{module_path}: expected params for tenant A's call to carry "
            f"tenantid='T-TEST-TENANT-A', got {params_a!r}"
        )
        assert isinstance(params_b, dict) and params_b.get("tenantid") == "T-TEST-TENANT-B", (
            f"{module_path}: expected params for tenant B's call to carry "
            f"tenantid='T-TEST-TENANT-B', got {params_b!r} — a stale/cached "
            "tenantid here would mean tenant A's rows leak into tenant B's pull"
        )

    def test_load_task_timeline_execute_params_match_requested_tenant(self):
        mod = importlib.import_module(_SINCE_LOADER_MODULE)
        conn, cur = _make_conn([])
        since = datetime(2026, 1, 1, tzinfo=timezone.utc)

        with patch("psycopg2.connect", return_value=conn):
            list(mod.iter_silver_rows("dsn", "T-TEST-TENANT-A", since))
            call_a = cur.execute.call_args
            list(mod.iter_silver_rows("dsn", "T-TEST-TENANT-B", since))
            call_b = cur.execute.call_args

        params_a = call_a.args[1] if len(call_a.args) > 1 else call_a.kwargs.get("vars")
        params_b = call_b.args[1] if len(call_b.args) > 1 else call_b.kwargs.get("vars")

        assert isinstance(params_a, dict) and params_a.get("tenantid") == "T-TEST-TENANT-A"
        assert isinstance(params_b, dict) and params_b.get("tenantid") == "T-TEST-TENANT-B"

    def test_load_tenant_dashboard_execute_params_match_requested_tenant(self):
        """load_tenant_dashboard.fetch_tenant_rows() returns a list via
        fetchall() rather than iterating the cursor — same tenant-scoping
        property, different mock shape (see module docstring note)."""
        mod = importlib.import_module(_FETCH_TENANT_ROWS_MODULE)

        def _make_fetchall_conn():
            cur = MagicMock()
            cur.fetchall.return_value = []
            cur.__enter__ = MagicMock(return_value=cur)
            cur.__exit__ = MagicMock(return_value=False)
            conn = MagicMock()
            conn.cursor.return_value = cur
            return conn, cur

        conn, cur = _make_fetchall_conn()
        with patch("psycopg2.connect", return_value=conn):
            mod.fetch_tenant_rows("dsn", "T-TEST-TENANT-A")
            call_a = cur.execute.call_args
            mod.fetch_tenant_rows("dsn", "T-TEST-TENANT-B")
            call_b = cur.execute.call_args

        params_a = call_a.args[1] if len(call_a.args) > 1 else call_a.kwargs.get("vars")
        params_b = call_b.args[1] if len(call_b.args) > 1 else call_b.kwargs.get("vars")

        assert isinstance(params_a, dict) and params_a.get("tenantid") == "T-TEST-TENANT-A"
        assert isinstance(params_b, dict) and params_b.get("tenantid") == "T-TEST-TENANT-B"


class TestKnowledgeChunkTenantScoping:
    """load_knowledge_chunk has no Silver SQL — its tenant boundary is
    shape_doc() stamping the requested tenantId onto every doc it builds
    (see _MONGO_FILTER_SCOPED_MODULES note above)."""

    def test_shape_doc_stamps_requested_tenant_not_a_stale_one(self):
        mod = importlib.import_module(next(iter(_MONGO_FILTER_SCOPED_MODULES)))
        record = {"chunkId": "chunk-1", "sourceType": "faq", "text": "hello"}
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)

        doc_a = mod.shape_doc(dict(record), "T-TEST-TENANT-A", ts)
        doc_b = mod.shape_doc(dict(record), "T-TEST-TENANT-B", ts)

        assert doc_a["tenantId"] == "T-TEST-TENANT-A"
        assert doc_b["tenantId"] == "T-TEST-TENANT-B"
