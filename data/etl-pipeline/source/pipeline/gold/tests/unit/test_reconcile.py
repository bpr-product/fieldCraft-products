"""Unit tests for Silver↔Gold reconciliation drift computation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from gold.src.reconcile.reconcile_silver_gold import (  # noqa: E402
    compute_drift, _normalize_key, compare_docs, VOLATILE_FIELDS, SPECS,
)


class TestComputeDrift:
    def test_perfect_match_no_drift(self):
        silver = {1: 100, 2: 100, 3: 100}
        gold = {1: 100, 2: 100, 3: 100}
        d = compute_drift(silver, gold)
        assert d.total == 0 and d.loss == 0
        assert d.expected == 3 and d.gold == 3

    def test_missing_in_gold(self):
        # project 3 exists in Silver but never made it to Gold (lost trigger)
        d = compute_drift({1: 100, 2: 100, 3: 100}, {1: 100, 2: 100})
        assert d.missing == [3]
        assert d.loss == 1

    def test_stale_gold_behind_silver_lsn(self):
        # Gold doc for 2 is behind Silver's LSN (missed update)
        d = compute_drift({1: 100, 2: 200}, {1: 100, 2: 150})
        assert d.stale == [2]
        assert d.missing == [] and d.loss == 1

    def test_not_stale_when_gold_ahead_or_equal(self):
        d = compute_drift({1: 100, 2: 200}, {1: 100, 2: 200})
        assert d.stale == []
        d2 = compute_drift({1: 100}, {1: 250})   # gold ahead — not stale
        assert d2.stale == []

    def test_orphan_in_gold_not_silver(self):
        # Gold has a doc for 9 with no Silver row (e.g. row went invalid / hard-deleted)
        d = compute_drift({1: 100}, {1: 100, 9: 100})
        assert d.orphan == [9]
        assert d.loss == 0          # orphan is not "loss" (Gold isn't behind)
        assert d.total == 1

    def test_unknown_lsn_never_stale(self):
        # silver lsn 0/None or gold lsn None → cannot judge stale, so don't flag
        assert compute_drift({1: 0}, {1: None}).stale == []
        assert compute_drift({1: None}, {1: 50}).stale == []
        assert compute_drift({1: 100}, {1: None}).stale == []

    def test_sample_caps_keys_but_not_counts(self):
        silver = {k: 100 for k in range(50)}
        gold = {}
        d = compute_drift(silver, gold, sample=5)
        assert len(d.missing) == 5            # key list capped for logging
        assert d.n_missing == 50              # TRUE count not capped
        assert d.loss == 50                   # verdict uses true count
        assert d.expected == 50

    def test_combined(self):
        silver = {1: 100, 2: 200, 3: 100}      # 3 expected
        gold = {1: 100, 2: 150, 9: 100}        # 2 ok-ish: 2 stale, 3 missing, 9 orphan
        d = compute_drift(silver, gold)
        assert d.missing == [3]
        assert d.stale == [2]
        assert d.orphan == [9]
        assert d.loss == 2 and d.total == 3


class TestCompareDocs:
    def test_identical_no_diff(self):
        a = {"x": 1, "y": "v", "answers_data": {"k": [1, 2]}}
        assert compare_docs(a, dict(a)) == []

    def test_value_mismatch_flagged(self):
        assert compare_docs({"status": "open"}, {"status": "closed"}) == ["status"]

    def test_volatile_fields_ignored(self):
        # different _etlUpdatedAt / _silverLsn must NOT count as a mismatch
        a = {"x": 1, "_etlUpdatedAt": "t1", "_silverLsn": 5, "updatedAt": "t1"}
        b = {"x": 1, "_etlUpdatedAt": "t2", "_silverLsn": 9, "updatedAt": "t2"}
        assert compare_docs(a, b) == []

    def test_type_noise_normalized(self):
        # int vs float-ish, dict order — JSON-normalized, not a mismatch
        assert compare_docs({"n": 1, "d": {"a": 1, "b": 2}},
                            {"n": 1, "d": {"b": 2, "a": 1}}) == []

    def test_missing_field_is_mismatch(self):
        assert compare_docs({"x": 1, "y": 2}, {"x": 1}) == ["y"]

    def test_volatile_set_has_expected_members(self):
        for f in ("_etlUpdatedAt", "_silverLsn", "_pipelineRunId", "createdAt", "_id"):
            assert f in VOLATILE_FIELDS


class TestNormalizeKey:
    def test_int_passthrough(self):
        assert _normalize_key(42) == 42

    def test_numeric_string_to_int(self):
        assert _normalize_key("42") == 42      # Silver bigint vs Gold int align

    def test_non_numeric_to_str(self):
        assert _normalize_key("abc") == "abc"

    def test_none_to_str(self):
        assert _normalize_key(None) == "None"


_EDEN_PROJECT_GRAIN_SPECS = [
    ("eden_project_health",     "project_health",     "projectId"),
    ("eden_invoice_summary",    "invoice_summary",    "projectId"),
    ("eden_task_intelligence",  "task_intelligence",  "projectId"),
    ("eden_procurement_status", "procurement_status", "projectId"),
    ("eden_bid_context",        "bid_context",        "projectId"),
]


class TestEdenReconcileSpecs:
    """All 5 Eden project-grain specs are registered in SPECS with correct shape."""

    def test_all_five_specs_present(self):
        for name, _, _ in _EDEN_PROJECT_GRAIN_SPECS:
            assert name in SPECS, f"{name} missing from SPECS"

    @pytest.mark.parametrize("spec_name,collection,gold_key", _EDEN_PROJECT_GRAIN_SPECS)
    def test_spec_fields(self, spec_name, collection, gold_key):
        spec = SPECS[spec_name]
        assert spec.gold_collection == collection
        assert spec.gold_key == gold_key
        assert spec.silver_table == "project"
        assert spec.silver_key == "id"
        assert spec.gold_db_default == "gold_eden"

    @pytest.mark.parametrize("spec_name,_c,_k", _EDEN_PROJECT_GRAIN_SPECS)
    def test_builder_is_importable(self, spec_name, _c, _k):
        import importlib
        spec = SPECS[spec_name]
        mod_name, fn_name = spec.builder.split(":")
        mod = importlib.import_module(mod_name)
        assert callable(getattr(mod, fn_name))

    def test_tenant_dashboard_excluded(self):
        """tenant_dashboard is tenant-grain; must not appear in SPECS."""
        assert "eden_tenant_dashboard" not in SPECS

    def test_bpe_and_qa_still_present(self):
        assert "bpe" in SPECS
        assert "qa_recommendation" in SPECS
