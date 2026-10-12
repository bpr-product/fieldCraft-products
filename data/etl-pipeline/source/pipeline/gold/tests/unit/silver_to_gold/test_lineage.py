"""Unit tests for gold.pipeline.lineage — the shared Gold doc audit stamp."""

from __future__ import annotations

import importlib
from datetime import datetime, timezone

import pytest

pytestmark = [pytest.mark.unit]

MOD = "gold.pipeline.lineage"
TS = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def lin():
    return importlib.import_module(MOD)


class TestCurrentRunId:
    def test_none_when_unset(self, lin, monkeypatch):
        monkeypatch.delenv(lin.RUN_ID_ENV, raising=False)
        assert lin.current_run_id() is None

    def test_empty_is_none(self, lin, monkeypatch):
        monkeypatch.setenv(lin.RUN_ID_ENV, "")
        assert lin.current_run_id() is None

    def test_reads_env(self, lin, monkeypatch):
        monkeypatch.setenv(lin.RUN_ID_ENV, "RUN-42")
        assert lin.current_run_id() == "RUN-42"


class TestLineageFields:
    def test_keys(self, lin, monkeypatch):
        # featureName/processingTimestamp were dropped 2026-07-12 (see
        # gold/pipeline/lineage.py's "Consolidation" note): featureName had no
        # functional reader, and processingTimestamp always duplicated
        # goldUpdatedAt. lineage_fields() still accepts feature_name for
        # call-site compatibility but no longer stamps it into the dict.
        monkeypatch.delenv(lin.RUN_ID_ENV, raising=False)
        out = lin.lineage_fields("eden", TS)
        assert set(out) == {
            "pipelineRunId", "goldUpdatedAt",
            "sourceUpdatedAt", "dataOrigin", "sourceEnv", "_env",
        }
        assert out["pipelineRunId"] is None
        assert out["goldUpdatedAt"] == TS

    def test_data_origin_defaults_source(self, lin, monkeypatch):
        monkeypatch.delenv(lin.RUN_ID_ENV, raising=False)
        out = lin.lineage_fields("bpe", TS)
        assert out["dataOrigin"] == "SOURCE" == lin.DATA_ORIGIN_SOURCE
        # not threaded → null, not absent
        assert out["sourceUpdatedAt"] is None

    def test_data_origin_override(self, lin):
        out = lin.lineage_fields("bpe", TS, data_origin="MASKED")
        assert out["dataOrigin"] == "MASKED"

    def test_source_updated_at_passthrough(self, lin):
        src = datetime(2026, 5, 31, 9, 0, 0, tzinfo=timezone.utc)
        out = lin.lineage_fields("bpe", TS, source_updated_at=src)
        assert out["sourceUpdatedAt"] == src

    def test_source_updated_at_iso(self, lin):
        src = datetime(2026, 5, 31, 9, 0, 0, tzinfo=timezone.utc)
        out = lin.lineage_fields("bpe", TS, iso=True, source_updated_at=src)
        assert out["sourceUpdatedAt"] == src.isoformat()

    def test_explicit_run_id_wins(self, lin, monkeypatch):
        monkeypatch.setenv(lin.RUN_ID_ENV, "ENV-RUN")
        out = lin.lineage_fields("bpe", TS, run_id="ARG-RUN")
        assert out["pipelineRunId"] == "ARG-RUN"

    def test_falls_back_to_env(self, lin, monkeypatch):
        monkeypatch.setenv(lin.RUN_ID_ENV, "ENV-RUN")
        assert lin.lineage_fields("bpe", TS)["pipelineRunId"] == "ENV-RUN"

    def test_iso_mode(self, lin, monkeypatch):
        monkeypatch.delenv(lin.RUN_ID_ENV, raising=False)
        out = lin.lineage_fields("reference_project", TS, iso=True)
        assert out["goldUpdatedAt"] == TS.isoformat()


class TestSetOnInsert:
    def test_datetime(self, lin):
        assert lin.set_on_insert(TS) == {"goldCreatedAt": TS}

    def test_iso(self, lin):
        assert lin.set_on_insert(TS, iso=True) == {"goldCreatedAt": TS.isoformat()}


def test_feature_names_constant(lin):
    assert lin.FEATURE_NAMES == {"bpe", "eden", "reference_project", "canonical"}


class _FakeDeleteResult:
    def __init__(self, deleted_count):
        self.deleted_count = deleted_count


class _FakeCollection:
    """Minimal double covering only what cleanup_stale_order_no_siblings
    calls: find(filter, projection) and delete_many(filter). `docs` is the
    full backing set; find() applies the filter's tenantId/orderNo/projectId
    (with $ne/$in support) against it, same shape a real Mongo query would
    match, so tests can assert on delete_many's effect rather than mocking
    return values by hand."""

    def __init__(self, docs):
        self.docs = list(docs)
        self.delete_many_calls = []
        self.find_raises = False

    def find(self, flt, _projection):
        if self.find_raises:
            raise RuntimeError("simulated find failure")
        out = []
        for d in self.docs:
            if d.get("tenantId") != flt["tenantId"]:
                continue
            if d.get("orderNo") != flt["orderNo"]:
                continue
            if d.get("sourceEnv") != flt.get("sourceEnv"):
                continue
            ne = flt.get("projectId", {}).get("$ne")
            if ne is not None and d.get("projectId") == ne:
                continue
            out.append(d)
        return out

    def delete_many(self, flt):
        self.delete_many_calls.append(flt)
        stale_ids = set(flt["projectId"]["$in"])
        before = len(self.docs)
        self.docs = [
            d for d in self.docs
            if not (d.get("tenantId") == flt["tenantId"]
                    and d.get("orderNo") == flt["orderNo"]
                    and d.get("sourceEnv") == flt.get("sourceEnv")
                    and d.get("projectId") in stale_ids)
        ]
        return _FakeDeleteResult(before - len(self.docs))


class TestIsOlderProjectId:
    def test_smaller_int_string_is_older(self, lin):
        assert lin._is_older_project_id("1232", "1233") is True

    def test_larger_int_string_is_not_older(self, lin):
        assert lin._is_older_project_id("1233", "1232") is False

    def test_equal_is_not_older(self, lin):
        assert lin._is_older_project_id("1233", "1233") is False

    def test_digit_length_boundary_compares_numerically_not_lexically(self, lin):
        # Real bug class this guards against: "999" > "1000" as strings
        # (lexicographic), but 999 < 1000 numerically.
        assert lin._is_older_project_id("999", "1000") is True
        assert lin._is_older_project_id("1000", "999") is False

    def test_non_numeric_candidate_never_treated_as_older(self, lin):
        assert lin._is_older_project_id("not-a-number", "1233") is False

    def test_non_numeric_current_never_treated_as_older(self, lin):
        assert lin._is_older_project_id("1232", "not-a-number") is False

    def test_none_candidate_never_treated_as_older(self, lin):
        assert lin._is_older_project_id(None, "1233") is False


class TestCleanupStaleOrderNoSiblings:
    def test_deletes_only_the_older_sibling(self, lin):
        coll = _FakeCollection([
            {"tenantId": "tn1", "orderNo": "482999", "projectId": "1242"},
            {"tenantId": "tn1", "orderNo": "482999", "projectId": "1243"},
        ])
        deleted = lin.cleanup_stale_order_no_siblings(coll, "tn1", "482999", "1243")
        assert deleted == 1
        assert [d["projectId"] for d in coll.docs] == ["1243"]

    def test_never_deletes_a_newer_sibling(self, lin):
        # If the OLD id's own (delayed) CDC event is what's currently being
        # written, its sibling check must not delete the newer doc -- see
        # cleanup_stale_order_no_siblings' module comment on this residual,
        # accepted race.
        coll = _FakeCollection([
            {"tenantId": "tn1", "orderNo": "482999", "projectId": "1242"},
            {"tenantId": "tn1", "orderNo": "482999", "projectId": "1243"},
        ])
        deleted = lin.cleanup_stale_order_no_siblings(coll, "tn1", "482999", "1242")
        assert deleted == 0
        assert {d["projectId"] for d in coll.docs} == {"1242", "1243"}

    def test_no_sibling_is_a_no_op(self, lin):
        coll = _FakeCollection([
            {"tenantId": "tn1", "orderNo": "482999", "projectId": "1243"},
        ])
        deleted = lin.cleanup_stale_order_no_siblings(coll, "tn1", "482999", "1243")
        assert deleted == 0
        assert coll.delete_many_calls == []

    def test_does_not_touch_a_different_tenant_sharing_the_order_no(self, lin):
        coll = _FakeCollection([
            {"tenantId": "tn1", "orderNo": "482999", "projectId": "1242"},
            {"tenantId": "tn1", "orderNo": "482999", "projectId": "1243"},
            {"tenantId": "tn2", "orderNo": "482999", "projectId": "1242"},
        ])
        lin.cleanup_stale_order_no_siblings(coll, "tn1", "482999", "1243")
        assert any(d["tenantId"] == "tn2" for d in coll.docs)

    def test_does_not_touch_a_different_source_env_sharing_the_order_no(self, lin):
        # PR 8243 review (thread 71329): a legitimate document from a
        # DIFFERENT Silver source environment can share (tenantId, orderNo)
        # with the doc just written -- see this pipeline's own sourceEnv
        # field-semantics note (gold/pipeline/lineage.py's module docstring)
        # on why tenantid alone isn't unique across envs. Without scoping by
        # sourceEnv too, this would wrongly delete that other env's doc,
        # mistaking it for the same-env abandoned-draft duplicate this
        # function actually exists to clean up.
        coll = _FakeCollection([
            {"tenantId": "tn1", "orderNo": "482999", "projectId": "1242", "sourceEnv": "prod"},
            {"tenantId": "tn1", "orderNo": "482999", "projectId": "1243", "sourceEnv": "prod"},
            {"tenantId": "tn1", "orderNo": "482999", "projectId": "1242", "sourceEnv": "qa"},
        ])
        deleted = lin.cleanup_stale_order_no_siblings(coll, "tn1", "482999", "1243", "prod")
        assert deleted == 1
        remaining = [(d["projectId"], d["sourceEnv"]) for d in coll.docs]
        assert ("1242", "prod") not in remaining
        assert ("1243", "prod") in remaining
        assert ("1242", "qa") in remaining  # different env, must survive untouched

    def test_falsy_order_no_is_a_no_op(self, lin):
        coll = _FakeCollection([{"tenantId": "tn1", "orderNo": None, "projectId": "1242"}])
        assert lin.cleanup_stale_order_no_siblings(coll, "tn1", None, "1243") == 0
        assert lin.cleanup_stale_order_no_siblings(coll, "tn1", "", "1243") == 0

    def test_none_current_project_id_is_a_no_op(self, lin):
        coll = _FakeCollection([{"tenantId": "tn1", "orderNo": "482999", "projectId": "1242"}])
        assert lin.cleanup_stale_order_no_siblings(coll, "tn1", "482999", None) == 0

    def test_find_failure_returns_zero_not_raises(self, lin):
        coll = _FakeCollection([])
        coll.find_raises = True
        assert lin.cleanup_stale_order_no_siblings(coll, "tn1", "482999", "1243") == 0
