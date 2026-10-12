"""Guardrail for gold_eden_stream's selective fan-out.

Selective fan-out skips rebuilding a Gold collection whose read-set of a Silver
row is unchanged. That is only correct while ``_TABLE_COLUMN_READS`` matches the
columns the loaders' SQL actually reads. If a loader starts reading a new column
and the map is not updated, that collection silently stops refreshing when only
the new column changes -- no exception, no metric, just quietly stale data.

So the map is NOT trusted as hand-maintained documentation: this module
re-derives the read-sets from the loader modules' own SQL and fails when they
drift. The derivation is deliberately crude (regex over the SQL string constants
for ``p.<column>``); it does not need to be a parser, only to catch the case
where someone adds a column and forgets the map.

These tests import ONLY the loader modules and the pure helpers, never
gold_eden_stream itself, so they run without pyflink installed.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

# tests/unit/flink/ -> gold/  (matches the sibling leaf conftests' depth)
_GOLD_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(_GOLD_ROOT.parent), str(_GOLD_ROOT.parent / "shared")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytestmark = [pytest.mark.unit, pytest.mark.eden]

# Mirrors gold_eden_stream._TABLE_COLUMN_READS["project"]. Duplicated rather than
# imported because importing gold_eden_stream requires pyflink, which is
# deliberately not installed in this environment (see requirements.txt). The
# duplication is itself checked: test_map_matches_stream_module reads the
# constant straight out of the stream module's source text.
EXPECTED_PROJECT_READS: dict[str, set[str]] = {
    "project_health": {
        "_s_deleted_at", "_s_env", "_s_is_deleted", "_s_tenantid",
        "additional_info", "assigned_to", "created_at", "created_by",
        "customer", "customer_asset", "description", "id", "invalid",
        "job_done_by", "location", "order_no", "parent_project_id",
        "project_type", "project_uid", "reference_project_id",
        "state_change_history", "status", "tags", "tax_exemption",
        "team_members", "trade", "trade_type", "updated_at", "version",
        "zipcode",
    },
    "invoice_summary": {
        "_s_deleted_at", "_s_env", "_s_is_deleted", "_s_tenantid",
        "id", "invalid", "order_no",
    },
    "procurement_status": {
        "_s_deleted_at", "_s_env", "_s_is_deleted", "_s_tenantid",
        "id", "invalid", "order_no",
    },
    "task_intelligence": {
        "_s_deleted_at", "_s_env", "_s_is_deleted", "_s_tenantid",
        "id", "invalid",
    },
}

LOADER_MODULES = {
    "project_health":     "gold.src.eden.load_project_health",
    "invoice_summary":    "gold.src.eden.load_invoice_summary",
    "procurement_status": "gold.src.eden.load_procurement_status",
    "task_intelligence":  "gold.src.eden.load_task_intelligence",
}

# Excluded from the hash in gold_eden_stream because they change on every write.
LINEAGE = {"_s_lsn", "_s_silver_ts"}


def _project_columns_in_sql(module_name: str) -> set[str]:
    """Columns referenced as ``p.<col>`` inside a module's SQL string literals.

    Scoped to triple-quoted blocks so Python attribute access on unrelated
    locals named ``p`` (argparse parsers, payload dicts) is not picked up.
    """
    mod = importlib.import_module(module_name)
    src = Path(mod.__file__).read_text(encoding="utf-8")
    sql = "\n".join(re.findall(r'"""(.*?)"""', src, re.S))
    return set(re.findall(r"\bp\.([a-z_]+)", sql)) - LINEAGE


class TestReadSetsMatchSql:
    """The map must not drift from the SQL it claims to describe."""

    @pytest.mark.parametrize("collection", sorted(LOADER_MODULES))
    def test_declared_read_set_matches_loader_sql(self, collection):
        derived = _project_columns_in_sql(LOADER_MODULES[collection])
        declared = EXPECTED_PROJECT_READS[collection]

        missing = derived - declared
        assert not missing, (
            f"{collection}'s SQL now reads project column(s) {sorted(missing)} that are "
            f"NOT in _TABLE_COLUMN_READS['project']['{collection}'] in gold_eden_stream.py.\n"
            f"Selective fan-out will skip rebuilding {collection} when only those columns "
            f"change, so it will silently serve stale data. Add them to the map."
        )

    @pytest.mark.parametrize("collection", sorted(LOADER_MODULES))
    def test_declared_read_set_is_not_stale(self, collection):
        derived = _project_columns_in_sql(LOADER_MODULES[collection])
        declared = EXPECTED_PROJECT_READS[collection]

        extra = declared - derived
        # Over-declaring is safe (it only costs unnecessary rebuilds), so this is
        # a weaker assertion than the one above -- but it still catches a loader
        # that dropped a column, which is a signal the map is unmaintained.
        assert not extra, (
            f"_TABLE_COLUMN_READS declares project column(s) {sorted(extra)} for "
            f"{collection}, but its SQL no longer reads them. Harmless at runtime "
            f"(extra rebuilds only), but the map has drifted — please prune it."
        )


class TestReadSetsAreActuallyDifferent:
    """The optimization only pays if the read-sets genuinely differ."""

    def test_project_health_reads_far_more_than_the_others(self):
        ph = EXPECTED_PROJECT_READS["project_health"]
        for other in ("invoice_summary", "procurement_status", "task_intelligence"):
            assert len(EXPECTED_PROJECT_READS[other]) < len(ph)

    def test_common_business_columns_are_few(self):
        """The whole win rests on the other three reading almost nothing.

        If a future change makes them read most of what project_health reads,
        selective fan-out stops saving anything and this test should prompt a
        re-think rather than leaving dead complexity in place.
        """
        meta = {"_s_deleted_at", "_s_env", "_s_is_deleted", "_s_tenantid", "id", "invalid"}
        for other in ("invoice_summary", "procurement_status", "task_intelligence"):
            business = EXPECTED_PROJECT_READS[other] - meta
            assert len(business) <= 2, (
                f"{other} now reads business columns {sorted(business)} from project; "
                f"selective fan-out saves less than it used to — re-evaluate."
            )


class TestMapIsInSyncWithStreamModule:
    """The duplicated constant above must match gold_eden_stream's real one."""

    def test_map_matches_stream_module(self):
        stream_src = (
            _GOLD_ROOT / "src" / "flink" / "jobs" / "gold_eden_stream.py"
        ).read_text(encoding="utf-8")

        block = re.search(
            r'_TABLE_COLUMN_READS.*?=\s*\{(.*?)\n\}', stream_src, re.S
        )
        assert block, "could not locate _TABLE_COLUMN_READS in gold_eden_stream.py"
        body = block.group(1)

        proj = re.search(r'"project"\s*:\s*\{(.*)\}\s*,?\s*$', body, re.S)
        assert proj, "could not locate the 'project' entry in _TABLE_COLUMN_READS"

        for collection, expected in EXPECTED_PROJECT_READS.items():
            entry = re.search(
                rf'"{collection}"\s*:\s*frozenset\(\{{(.*?)\}}\)', proj.group(1), re.S
            )
            assert entry, f"{collection} missing from _TABLE_COLUMN_READS['project']"
            found = set(re.findall(r'"([a-z_]+)"', entry.group(1)))
            assert found == expected, (
                f"gold_eden_stream's _TABLE_COLUMN_READS['project']['{collection}'] "
                f"does not match this test's copy.\n"
                f"  only in stream module: {sorted(found - expected)}\n"
                f"  only in test:          {sorted(expected - found)}"
            )


# ---------------------------------------------------------------------------
# The four tables added after `project`: task, invoice, bom, purchase_order.
# Their loaders alias the table differently per query (task_pivot uses `at`,
# invoice_summary uses `ai`, bom appears as b/b2/v/v2 within one file, etc.), so
# unlike `project`'s single hardcoded `p.` pattern above, each spec below names
# its own alias set. This is deliberately per-table rather than forcing a false
# genericity — a real SQL parser would be needed to derive these automatically
# with no hardcoded knowledge, and is not worth building for a guardrail test.
# ---------------------------------------------------------------------------

EXPECTED_TABLE_READS: dict[str, dict[str, set[str]]] = {
    "task": {
        "project_health": {
            "_s_env", "_s_is_deleted", "_s_tenantid", "invalid", "project_id", "id",
        },
        "task_intelligence": {
            "_s_env", "_s_is_deleted", "_s_tenantid", "invalid", "project_id",
            "created_at", "id", "parent_project_id", "phase",
            "task_order", "task_uid",
        },
    },
    "invoice": {
        "invoice_summary": {
            "_s_env", "_s_is_deleted", "_s_tenantid", "project_id",
            "additional_info", "balance", "codes", "created_at", "created_by",
            "customer_uid", "document_id", "due_date", "id", "invalid",
            "invoice_message", "invoice_no", "invoiced_amount", "issue_at",
            "items", "reminder_at", "reminder_count", "reminder_status",
            "status", "tax_amount", "tax_info", "term", "total_amount",
            "type", "updated_at", "version",
        },
        # project_health's read of `invoice` is UNALIASED (bare column names
        # inside the invoice_agg CTE) — see test_invoice_agg_bare_columns_match
        # below for that one, since the alias-based extractor can't see it.
        "project_health": {
            "_s_env", "_s_is_deleted", "_s_tenantid", "invalid", "project_id",
            "invoiced_amount", "status",
        },
    },
    "bom": {
        "procurement_status": {
            "_s_env", "_s_is_deleted", "_s_tenantid", "invalid", "project_id",
            "material_name", "quantity", "total_cost", "unit", "unit_cost",
            "vendor_material_map_id",
        },
        "project_health": {
            "_s_env", "_s_is_deleted", "_s_tenantid", "id", "invalid",
            "project_id", "material_name", "quantity", "total_cost",
            "vendor_material_map_id", "version",
        },
    },
    "purchase_order": {
        "procurement_status": {
            "_s_env", "_s_is_deleted", "_s_tenantid", "project_id",
            "created_at", "id", "status", "total", "vendor_id",
        },
        "project_health": {
            "_s_env", "_s_is_deleted", "_s_tenantid", "invalid", "project_id",
            "additional_info", "approved_by", "billing_address", "comment",
            "counter_offer_date", "created_at", "created_by", "document_id",
            "id", "reason", "reminder_at", "reminder_count", "reminder_status",
            "shipping_address", "status", "sub_total", "tax_info", "total",
            "updated_at", "vendor_id",
        },
    },
}

# (table, collection) -> which alias(es) that query uses for the table, and any
# columns to exclude because they belong to a DIFFERENT thing that happens to
# share the alias name (a real hazard here, not hypothetical — see below).
_ALIAS_SPECS: dict[tuple[str, str], dict] = {
    ("task", "project_health"):            {"module": "project_health",  "aliases": {"at"}},
    ("task", "task_intelligence"):         {"module": "task_intelligence", "aliases": {"at"}},
    ("invoice", "invoice_summary"):        {"module": "invoice_summary", "aliases": {"ai"}},
    ("bom", "procurement_status"):         {"module": "procurement_status", "aliases": {"b"}},
    ("bom", "project_health"):             {"module": "project_health",  "aliases": {"b", "b2", "v", "v2"}},
    # pending_approvals / po_silver_ts excluded: load_procurement_status.py's
    # LATERAL subquery re-uses the alias `po` for its OWN output columns (the
    # subquery itself is aliased `) po ON TRUE`), so `po.pending_approvals` in
    # the outer SELECT is that alias shadowing, not a real silver.purchase_order
    # column — confirmed by reading the SQL, not assumed. Excluding it here
    # documents the collision instead of silently mismatching against the map.
    ("purchase_order", "procurement_status"): {
        "module": "procurement_status", "aliases": {"po"},
        "exclude": {"pending_approvals", "po_silver_ts"},
    },
    ("purchase_order", "project_health"):  {"module": "project_health",  "aliases": {"apo"}},
}


def _conditional_filter_bodies(src: str) -> str:
    """Text of every `"__X_FILTER__".replace(..., "<body>")`-style substitution.

    Several loaders resolve a schema-dependent predicate (e.g. __INVALID_FILTER__
    -> "AND (at.invalid IS NULL OR at.invalid = FALSE)" only when that column
    exists) via a plain Python string passed to str.replace(), NOT inside the
    module's triple-quoted SQL constant -- see load_task_intelligence.py's
    _scoped_query(). A column read only through one of these is invisible to an
    extractor that only scans triple-quoted blocks, which would make this
    guardrail silently blind to exactly the kind of schema-conditional read this
    codebase uses throughout. Concatenating these bodies into the searched text
    (in addition to the triple-quoted SQL) closes that gap without widening the
    scan to the whole file, which would reintroduce unrelated-code collisions
    (the reason the base extractor is triple-quote-scoped to begin with).
    """
    return "\n".join(re.findall(
        r'"__\w+_FILTER__",\s*\n?\s*"([^"]*)"', src
    ))


def _cols_for_spec(spec: dict) -> set[str]:
    mod = importlib.import_module(LOADER_MODULES[spec["module"]])
    src = Path(mod.__file__).read_text(encoding="utf-8")
    sql = "\n".join(re.findall(r'"""(.*?)"""', src, re.S))
    sql += "\n" + _conditional_filter_bodies(src)
    cols: set[str] = set()
    for alias in spec["aliases"]:
        cols |= set(re.findall(rf"\b{re.escape(alias)}\.([a-z_]+)", sql))
    return (cols - LINEAGE) - spec.get("exclude", set())


class TestExtraTableReadSetsMatchSql:
    """Same guardrail as TestReadSetsMatchSql, for task/invoice/bom/purchase_order."""

    @pytest.mark.parametrize("table,collection", sorted(_ALIAS_SPECS))
    def test_declared_read_set_matches_loader_sql(self, table, collection):
        derived = _cols_for_spec(_ALIAS_SPECS[(table, collection)])
        declared = EXPECTED_TABLE_READS[table][collection]
        missing = derived - declared
        assert not missing, (
            f"{collection}'s SQL now reads {table} column(s) {sorted(missing)} that are "
            f"NOT in _TABLE_COLUMN_READS['{table}']['{collection}'] in gold_eden_stream.py.\n"
            f"Selective fan-out will skip rebuilding {collection} when only those columns "
            f"change on a {table} row, so it will silently serve stale data. Add them."
        )

    @pytest.mark.parametrize("table,collection", sorted(_ALIAS_SPECS))
    def test_declared_read_set_is_not_stale(self, table, collection):
        derived = _cols_for_spec(_ALIAS_SPECS[(table, collection)])
        declared = EXPECTED_TABLE_READS[table][collection]
        extra = declared - derived
        assert not extra, (
            f"_TABLE_COLUMN_READS['{table}']['{collection}'] declares column(s) "
            f"{sorted(extra)} that its SQL no longer reads. Harmless (extra rebuilds "
            f"only), but the map has drifted — please prune it."
        )


class TestInvoiceProjectHealthBareColumns:
    """project_health reads `invoice` WITHOUT an alias (invoice_agg CTE) — the
    alias-based extractor above can't see this one, so it's pinned directly.
    """

    def test_invoice_agg_bare_columns_match_declared_set(self):
        mod = importlib.import_module(LOADER_MODULES["project_health"])
        src = Path(mod.__file__).read_text(encoding="utf-8")
        block = re.search(r"invoice_agg AS \((.*?)\n\),", src, re.S)
        assert block, (
            "could not locate the invoice_agg CTE in load_project_health.py — "
            "if it was renamed or restructured, this test and the bare-column "
            "entry in _TABLE_COLUMN_READS['invoice']['project_health'] both "
            "need to be re-verified against the new SQL, not just re-pointed."
        )
        cte = block.group(1)
        assert "FROM silver.invoice" in cte, "block located does not reference silver.invoice"

        declared = EXPECTED_TABLE_READS["invoice"]["project_health"]
        # Every declared business column must appear as a bare identifier
        # (not prefixed by some other alias) inside this specific CTE.
        for col in declared - LINEAGE - {"_s_tenantid", "_s_env", "project_id"}:
            assert re.search(rf"(?<![.\w]){col}\b", cte), (
                f"declared column {col!r} not found bare inside invoice_agg's SQL — "
                f"the map may be stale. Re-check load_project_health.py's invoice_agg CTE."
            )


class TestExtraTablesAreInSyncWithStreamModule:
    """The duplicated EXPECTED_TABLE_READS above must match the real map."""

    def test_extra_tables_match_stream_module(self):
        stream_src = (
            _GOLD_ROOT / "src" / "flink" / "jobs" / "gold_eden_stream.py"
        ).read_text(encoding="utf-8")
        block = re.search(
            r'_TABLE_COLUMN_READS.*?=\s*\{(.*?)\n\}', stream_src, re.S
        )
        assert block, "could not locate _TABLE_COLUMN_READS in gold_eden_stream.py"
        body = block.group(1)

        for table, colls in EXPECTED_TABLE_READS.items():
            # No trailing \n required after the closing "},": the LAST entry in
            # the dict (purchase_order, at the time of writing) has its closing
            # brace immediately followed by the outer dict's own "\n}", and that
            # outer capture (above) already consumed the newline in between --
            # requiring one here made this regex silently never match the last
            # table in the map, regardless of which table that happens to be.
            tbl = re.search(rf'"{table}"\s*:\s*\{{(.*?)\n    \}},', body, re.S)
            assert tbl, f"could not locate the '{table}' entry in _TABLE_COLUMN_READS"
            for collection, expected in colls.items():
                entry = re.search(
                    rf'"{collection}"\s*:\s*frozenset\(\{{(.*?)\}}\)', tbl.group(1), re.S
                )
                assert entry, f"{collection} missing from _TABLE_COLUMN_READS['{table}']"
                found = set(re.findall(r'"([a-z_]+)"', entry.group(1)))
                assert found == expected, (
                    f"gold_eden_stream's _TABLE_COLUMN_READS['{table}']['{collection}'] "
                    f"does not match this test's copy.\n"
                    f"  only in stream module: {sorted(found - expected)}\n"
                    f"  only in test:          {sorted(expected - found)}"
                )

    def test_every_multi_collection_table_has_an_entry(self):
        """Every table this stream can fan out to >1 collection for should be
        mapped -- otherwise it silently falls back to rebuild-everything with
        no signal that the optimization isn't covering it. Reads
        _ALL_TABLE_TO_COLLECTIONS + the Phase-1 enabled_collections gate
        straight out of the stream module's source, so it stays accurate as
        collections are enabled/disabled without needing pyflink to import
        the real dict comprehensions.
        """
        stream_src = (
            _GOLD_ROOT / "src" / "flink" / "jobs" / "gold_eden_stream.py"
        ).read_text(encoding="utf-8")

        # Phase 1 enabled_collections gate (see gold_config.yml) — hardcoded
        # here rather than importing step_registry, matching this file's
        # no-pyflink-import constraint. If the gate changes, the "unmapped but
        # multi-collection" set below should be re-checked by hand.
        enabled = {"project_health", "invoice_summary", "task_intelligence",
                   "procurement_status"}

        table_block = re.search(
            r'_ALL_TABLE_TO_COLLECTIONS.*?=\s*\{(.*?)\n\}', stream_src, re.S
        )
        assert table_block, "could not locate _ALL_TABLE_TO_COLLECTIONS"

        mapped_tables = set(EXPECTED_TABLE_READS) | {"project"}
        for m in re.finditer(
            r'"(\w+)":\s*frozenset\(\{(.*?)\}\)', table_block.group(1), re.S
        ):
            table, colls_src = m.group(1), m.group(2)
            colls = set(re.findall(r'"([a-z_]+)"', colls_src)) & enabled
            if len(colls) > 1 and table not in mapped_tables:
                pytest.fail(
                    f"'{table}' now triggers {sorted(colls)} (>1 enabled collection) "
                    f"but has no entry in _TABLE_COLUMN_READS or this test's "
                    f"EXPECTED_TABLE_READS -- it will always rebuild every collection "
                    f"unconditionally. Either add a mapping (like task/invoice/bom/"
                    f"purchase_order) or, if that's an intentional trade-off for a "
                    f"low-volume table, note that explicitly."
                )


class TestLineageExclusion:
    """_s_lsn / _s_silver_ts must stay out of the hash."""

    def test_lineage_columns_are_read_by_sql_but_excluded(self):
        # They ARE in the SQL (the LSN guard needs _s_lsn), which is exactly why
        # excluding them has to be deliberate: hashing them would make every
        # write produce a new hash and silently disable the optimization.
        mod = importlib.import_module(LOADER_MODULES["invoice_summary"])
        src = Path(mod.__file__).read_text(encoding="utf-8")
        sql = "\n".join(re.findall(r'"""(.*?)"""', src, re.S))
        assert "p._s_lsn" in sql

        for collection, cols in EXPECTED_PROJECT_READS.items():
            assert not (cols & LINEAGE), (
                f"{collection}'s declared read-set contains lineage column(s) "
                f"{sorted(cols & LINEAGE)} — these change on every write, so the "
                f"hash would never match and selective fan-out would do nothing."
            )

    def test_extra_table_read_sets_exclude_lineage(self):
        for table, colls in EXPECTED_TABLE_READS.items():
            for collection, cols in colls.items():
                assert not (cols & LINEAGE), (
                    f"_TABLE_COLUMN_READS['{table}']['{collection}'] contains "
                    f"lineage column(s) {sorted(cols & LINEAGE)} — these change on "
                    f"every write, so the hash would never match."
                )
