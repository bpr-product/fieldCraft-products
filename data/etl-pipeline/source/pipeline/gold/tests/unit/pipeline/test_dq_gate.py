"""Unit tests for the post-load DQ gate decision logic (evaluate_dq).

These cover the "false green" closure: a loader that writes nothing while Silver
has source rows must produce a FATAL finding, while a genuinely empty tenant must
not. Pure function — no Mongo/PG needed.

The second half covers _silver_expected_counts' query construction, which is
what FEEDS evaluate_dq's expected_count — the decision logic above is only as
good as that number, and a filter bug there downgrades DQ_BROKEN_EMPTY to
DQ_ZERO_DOCS silently. PG is stubbed; no live connection needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from gold.pipeline import dq_gate as dq_gate_mod  # noqa: E402
from gold.pipeline.dq_gate import evaluate_dq  # noqa: E402

pytestmark = pytest.mark.pipeline


def _fatal(findings):
    return [code for sev, code, _ in findings if sev == "error"]


def _base(**over):
    kw = dict(
        collection="bid_context", gold_count=10, expected_count=10,
        missing_grain=0, null_tenant=0, contract_violations=[],
    )
    kw.update(over)
    return evaluate_dq(**kw)


def test_healthy_run_has_no_findings():
    assert _base() == []


def test_broken_empty_is_fatal():
    # 0 docs written but Silver has source rows — the bid_context-class bug.
    findings = _base(gold_count=0, expected_count=57)
    assert "DQ_BROKEN_EMPTY" in _fatal(findings)


def test_legitimately_empty_tenant_is_info_not_fatal():
    findings = _base(gold_count=0, expected_count=0)
    assert _fatal(findings) == []
    assert any(code == "DQ_ZERO_DOCS" and sev == "info" for sev, code, _ in findings)


def test_unknown_expectation_does_not_false_red():
    # expected_count None (feature not 1:1 with one table) => never a hard fail.
    findings = _base(gold_count=0, expected_count=None)
    assert _fatal(findings) == []
    assert any(code == "DQ_ZERO_DOCS" for _, code, _ in findings)


def test_contract_violation_is_fatal():
    findings = _base(contract_violations=["missing required contract fields: ['goldUpdatedAt']"])
    assert "DQ_CONTRACT_VIOLATION" in _fatal(findings)


def test_missing_grain_is_fatal():
    assert "DQ_MISSING_GRAIN" in _fatal(_base(missing_grain=3))


def test_null_tenant_is_fatal():
    assert "DQ_NULL_TENANT_ID" in _fatal(_base(null_tenant=2))


def test_multiple_findings_accumulate():
    findings = _base(gold_count=0, expected_count=5, null_tenant=1)
    codes = _fatal(findings)
    assert "DQ_BROKEN_EMPTY" in codes and "DQ_NULL_TENANT_ID" in codes


# ---------------------------------------------------------------------------
# _silver_expected_counts — the query that produces expected_count, per _s_env
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Captures the SQL/params the gate builds and returns canned rows."""

    def __init__(self, sink: dict, rows: list[tuple]):
        self._sink = sink
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=None):
        self._sink["sql"] = " ".join(sql.split())
        self._sink["params"] = params

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, sink: dict, rows: list[tuple]):
        self._sink = sink
        self._rows = rows
        self.closed = False

    def cursor(self):
        return _FakeCursor(self._sink, self._rows)

    def close(self):
        self.closed = True


@pytest.fixture()
def captured_sql(monkeypatch):
    """Stub psycopg2.connect for dq_gate and expose the executed SQL."""
    sink: dict = {}

    def _install(columns: set[str], rows: list[tuple] | None = None):
        canned = rows if rows is not None else [("si_dev", 7)]
        import psycopg2

        monkeypatch.setattr(
            psycopg2, "connect", lambda *_a, **_k: _FakeConn(sink, canned)
        )
        from gold.pipeline import silver_schema

        monkeypatch.setattr(
            silver_schema, "get_silver_columns",
            lambda _dsn, _table, schema="silver": frozenset(columns),
        )
        return sink

    return _install


def test_expected_counts_treat_null_invalid_as_not_superseded(captured_sql):
    """NULL `invalid` must COUNT, not be silently excluded.

    Regression: on tenants where the column exists but every row is NULL
    (tn24047750, 36795/36795 live-verified), `invalid = FALSE` evaluated to
    NULL and counted 0 rows — so a loader that wrote nothing reported
    DQ_ZERO_DOCS (info) instead of DQ_BROKEN_EMPTY (error).
    """
    sink = captured_sql({"_s_tenantid", "invalid"}, [("si_dev", 57)])
    result = dq_gate_mod._silver_expected_counts("dsn", "project", "tn24047750")

    assert result == {"si_dev": 57}
    assert "(invalid IS NULL OR invalid = FALSE)" in sink["sql"]
    assert "AND invalid = FALSE" not in sink["sql"]
    assert sink["params"] == ("tn24047750",)


def test_expected_counts_omit_filter_when_column_absent(captured_sql):
    """A missing `invalid` column must degrade to "all rows", not raise.

    Raising would be swallowed by the caller's except and discard the
    expectation entirely (expected_count=None), which disables the
    broken-empty check just as effectively as the NULL bug did.
    """
    sink = captured_sql({"_s_tenantid"}, [("datamart", 12)])
    result = dq_gate_mod._silver_expected_counts("dsn", "answers", "tn59395620")

    assert result == {"datamart": 12}
    assert "invalid" not in sink["sql"]


def test_expected_counts_group_by_source_env(captured_sql):
    """The expectation must be per _s_env, not one tenant-wide total."""
    sink = captured_sql(
        {"_s_tenantid", "invalid"}, [("si_dev", 26), ("datamart", 4), (None, 1)],
    )
    result = dq_gate_mod._silver_expected_counts("dsn", "project", "tn59395620")

    assert result == {"si_dev": 26, "datamart": 4, None: 1}
    assert "GROUP BY _s_env" in sink["sql"]


def test_expected_counts_return_none_on_pg_error(monkeypatch):
    """Infra failure stays 'unknown' — never a false red."""
    import psycopg2

    def _boom(*_a, **_k):
        raise psycopg2.OperationalError("connection refused")

    monkeypatch.setattr(psycopg2, "connect", _boom)
    from gold.pipeline import silver_schema

    monkeypatch.setattr(
        silver_schema, "get_silver_columns",
        lambda _dsn, _table, schema="silver": frozenset({"invalid"}),
    )

    assert dq_gate_mod._silver_expected_counts("dsn", "project", "tn1") is None


# ---------------------------------------------------------------------------
# Per-env emptiness — one env's docs must not cover another env's zero
# ---------------------------------------------------------------------------


def test_per_env_empty_env_is_fatal_even_when_tenant_total_is_healthy():
    """The core sourceEnv bug: si_dev wrote 26 docs, datamart wrote none.

    On tenant totals (26 docs vs 30 rows) the gate saw documents and passed.
    Per env, datamart's 0-vs-4 is independently falsifiable and goes red.
    """
    findings = _base(
        gold_count=26, expected_count=30,
        per_env={"si_dev": (26, 26), "datamart": (0, 4)},
    )
    assert "DQ_BROKEN_EMPTY" in _fatal(findings)
    assert any("datamart" in msg for _, _, msg in findings)
    assert not any("si_dev" in msg for _, _, msg in findings)


def test_per_env_all_populated_has_no_findings():
    assert _base(
        gold_count=30, expected_count=30,
        per_env={"si_dev": (26, 26), "datamart": (4, 4)},
    ) == []


def test_per_env_genuinely_empty_env_is_info_not_fatal():
    findings = _base(
        gold_count=26, expected_count=26,
        per_env={"si_dev": (26, 26), "datamart": (0, 0)},
    )
    assert _fatal(findings) == []
    assert any(code == "DQ_ZERO_DOCS" and sev == "info" for sev, code, _ in findings)


def test_per_env_unstamped_env_is_labelled_not_dropped():
    """Rows predating multi-env stamping (_s_env NULL) still get checked."""
    findings = _base(gold_count=0, expected_count=3, per_env={None: (0, 3)})
    assert "DQ_BROKEN_EMPTY" in _fatal(findings)
    assert any("<unstamped>" in msg for _, _, msg in findings)


def test_empty_per_env_falls_back_to_scalar_totals():
    """No Silver rows in any env => no per-env expectation to test."""
    findings = _base(gold_count=0, expected_count=0, per_env={})
    assert _fatal(findings) == []
    assert any(code == "DQ_ZERO_DOCS" for _, code, _ in findings)


def test_per_env_labels_do_not_collide_across_envs():
    """Two empty envs produce two distinct, attributable findings."""
    findings = _base(
        gold_count=0, expected_count=9,
        per_env={"si_dev": (0, 5), "datamart": (0, 4)},
    )
    assert _fatal(findings) == ["DQ_BROKEN_EMPTY", "DQ_BROKEN_EMPTY"]
    msgs = " ".join(msg for _, _, msg in findings)
    assert "si_dev" in msgs and "datamart" in msgs


def test_per_env_does_not_suppress_the_aggregate_checks():
    """grain/leak/contract findings are env-agnostic and must still fire once."""
    findings = _base(
        gold_count=30, expected_count=30,
        per_env={"si_dev": (30, 30)},
        missing_grain=2, null_tenant=1,
        contract_violations=["missing required contract fields: ['goldUpdatedAt']"],
    )
    codes = _fatal(findings)
    assert codes.count("DQ_MISSING_GRAIN") == 1
    assert codes.count("DQ_NULL_TENANT_ID") == 1
    assert codes.count("DQ_CONTRACT_VIOLATION") == 1


# ---------------------------------------------------------------------------
# Cross-tenant leak guard — scoped to the documents THIS run wrote
# ---------------------------------------------------------------------------


class _FakeCollection:
    """Records every count_documents filter so the gate's scoping is assertable."""

    def __init__(self):
        self.filters: list[dict] = []

    def count_documents(self, flt):
        self.filters.append(flt)
        # 1 doc for the tenant, 0 for every negative check (grain/leak).
        if flt.get("_grain") or "$in" in str(flt.get("tenantId", "")):
            return 0
        return 1

    def find(self, *_a, **_k):  # pragma: no cover — contract sample stubbed out
        return []


def _run_gate(monkeypatch, run_id):
    """Run the real gate against a fake Mongo, returning the recorded filters."""
    coll = _FakeCollection()
    fake_db = {"project_health": coll}
    monkeypatch.setattr(
        dq_gate_mod, "_get_mongo_client", lambda _uri: {"gold_eden": fake_db}
    )
    monkeypatch.setattr(dq_gate_mod, "_sample_contract_violations",
                        lambda *_a, **_k: [])
    import gold.pipeline.audit as audit_mod
    monkeypatch.setattr(audit_mod, "record_event", lambda *_a, **_k: None)

    # pg_dsn=None => no Silver expectation, so this exercises the leak guard only.
    dq_gate_mod.run_dq_gate(
        "load_project_health", "tn59395620", "mongodb://stub",
        dry_run=False, run_id=run_id, pg_dsn=None,
    )
    return [f for f in coll.filters if "$in" in str(f.get("tenantId", ""))]


def test_leak_guard_is_scoped_to_this_run(monkeypatch):
    """Unscoped, one legacy null-tenantId doc failed every tenant forever."""
    leak_filters = _run_gate(monkeypatch, run_id="run-abc123")
    assert len(leak_filters) == 1
    assert leak_filters[0].get("pipelineRunId") == "run-abc123"


def test_leak_guard_falls_back_to_collection_wide_without_run_id(monkeypatch):
    """Ad-hoc loader invocation has no run to scope to — keep the broad check."""
    leak_filters = _run_gate(monkeypatch, run_id=None)
    assert len(leak_filters) == 1
    assert "pipelineRunId" not in leak_filters[0]
