"""Unit tests for build_steps --rebuild / force_batch behavior (R1).

For a realtime feature the batch loaders are normally skipped (streaming job is
the sole writer). force_batch=True re-runs the STREAMING-owned loaders to rebuild
Gold from current Silver — but NOT estimate_summary (ML-owned).

EDEN IS THE EXCEPTION: its initial population is Debezium-based (see
step_registry.py's eden branch and deploy_silver_cdc_connector.py's SNAPSHOT
MODE note), so force_batch is a no-op for eden — it must NOT run any of the 6
streaming-owned Eden loaders, unlike BPE/qa_recommendation/canonical.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import gold.pipeline.step_registry as sr  # noqa: E402


def _names(monkeypatch, *, realtime: bool, force_batch: bool) -> list[str]:
    monkeypatch.setattr(sr, "is_feature_enabled", lambda f: True)
    monkeypatch.setattr(sr, "is_realtime", lambda f: realtime)
    # This suite tests the is_realtime/force_batch gate in isolation from the
    # Phase 1 `enabled_collections` allowlist (a separate, orthogonal gate) —
    # so pretend every collection is enabled regardless of gold_config.yml.
    monkeypatch.setattr(sr, "is_collection_enabled", lambda f, c: True)
    steps = sr.build_steps(
        tenant="T-1", mongo_uri=None, pg_dsn=None, bpe_db="gold_bpe",
        batch_size=10, dry_run=True, skip_setup=True,
        force_batch=force_batch,
    )
    return [n for n, _ in steps]


class TestRebuildGate:
    def test_realtime_steady_state_skips_loaders(self, monkeypatch):
        names = _names(monkeypatch, realtime=True, force_batch=False)
        # streams are sole writer for every realtime-classified loader —
        # canonical's user_permissions/tenant/project/customer/asset
        # (2026-08-24: tenant/project promoted first, customer/asset in a
        # second pass the same day, all five sharing gold_user_permissions_
        # stream.py) — but several loaders are batch-only (no streaming job)
        # and always run regardless of is_realtime(): canonical's question/
        # answer (global/rarely-changing reference data) and eden's
        # task_timeline/estimate_price_evidence/schedule_move_evidence (not
        # yet wired into gold_eden_stream.py's CDC trigger table — see
        # CLAUDE.md).
        assert names == [
            "load_task_timeline",
            "load_estimate_price_evidence",
            "load_schedule_move_evidence",
            "load_question",
            "load_answer",
        ]

    def test_realtime_rebuild_runs_streaming_owned_loaders(self, monkeypatch):
        names = _names(monkeypatch, realtime=True, force_batch=True)
        assert "load_estimate_snapshot" in names      # BPE feature_store
        assert "load_qa_recommendation" in names      # qa_answer
        assert "load_user_permissions" in names       # canonical: RBAC
        assert "load_tenant" in names                 # canonical: tenant (2026-08-24)
        assert "load_project" in names                # canonical: project (2026-08-24)
        assert "load_customer" in names                # canonical: customer (2026-08-24)
        assert "load_asset" in names                   # canonical: asset (2026-08-24)

    def test_realtime_rebuild_is_a_noop_for_eden(self, monkeypatch):
        # Eden's initial population is Debezium-based (fieldcraft-silver-cdc-connector's
        # snapshot.mode=initial feeds gold_eden_stream.py the same way ordinary
        # CDC does), unlike BPE/qa_recommendation/canonical above, which still
        # rebuild via direct-Silver-SQL loaders. force_batch must NOT run any of
        # Eden's 6 streaming-owned loaders -- see step_registry.py's eden branch.
        names = _names(monkeypatch, realtime=True, force_batch=True)
        for eden in ("load_project_health", "load_invoice_summary",
                     "load_task_intelligence", "load_procurement_status",
                     "load_bid_context", "load_tenant_dashboard"):
            assert eden not in names

    def test_batch_mode_runs_full_set(self, monkeypatch):
        names = _names(monkeypatch, realtime=False, force_batch=False)
        assert "load_estimate_snapshot" in names
        assert "load_qa_recommendation" in names

    def test_force_batch_noop_in_batch_mode(self, monkeypatch):
        # force_batch has no effect when the feature is already batch
        a = _names(monkeypatch, realtime=False, force_batch=False)
        b = _names(monkeypatch, realtime=False, force_batch=True)
        assert a == b


class TestCanonicalEnabledCollectionsGating:
    """Exercises the REAL is_collection_enabled() against a real gold_config
    dict, rather than the stubbed-out `lambda f, c: True` used by
    TestRebuildGate above — that stub means no test in this suite previously
    caught a regression in the actual Phase-1 `enabled_collections` allowlist
    gating mechanism for canonical (see infra/config/gold_config.yml).
    """

    def test_restricted_allowlist_excludes_disabled_collections(self, monkeypatch):
        monkeypatch.setattr(sr, "_GOLD_CONFIG", {
            "bpe": {"enabled": False},
            "qa_recommendation": {"enabled": False},
            "canonical": {
                "enabled": True,
                "type": "realtime",
                "enabled_collections": ["tenant", "project"],
            },
            "eden": {"enabled": False},
        })
        # is_realtime forced False so the allowlist gate (is_collection_enabled)
        # is exercised in isolation from the realtime/batch skip gate covered
        # by TestRebuildGate above — tenant/project are realtime-classified
        # (2026-08-24) so with is_realtime=True they'd be skipped here for a
        # reason unrelated to the allowlist, defeating the point of this test.
        monkeypatch.setattr(sr, "is_realtime", lambda f: False)
        steps = sr.build_steps(
            tenant="T-1", mongo_uri=None, pg_dsn=None, bpe_db="gold_bpe",
            batch_size=10, dry_run=True, skip_setup=True,
        )
        names = [n for n, _ in steps]
        assert "load_tenant" in names
        assert "load_project" in names
        assert "load_customer" not in names
        assert "load_asset" not in names
        assert "load_question" not in names
        assert "load_answer" not in names

    def test_unrestricted_allowlist_runs_every_collection(self, monkeypatch):
        monkeypatch.setattr(sr, "_GOLD_CONFIG", {
            "bpe": {"enabled": False},
            "qa_recommendation": {"enabled": False},
            "canonical": {"enabled": True, "type": "realtime"},
            "eden": {"enabled": False},
        })
        # See the isolation note above — is_realtime forced False so
        # tenant/project (realtime-classified, 2026-08-24) aren't skipped for
        # a reason unrelated to the allowlist under test.
        monkeypatch.setattr(sr, "is_realtime", lambda f: False)
        steps = sr.build_steps(
            tenant="T-1", mongo_uri=None, pg_dsn=None, bpe_db="gold_bpe",
            batch_size=10, dry_run=True, skip_setup=True,
        )
        names = [n for n, _ in steps]
        for loader in ("load_tenant", "load_customer", "load_project",
                       "load_asset", "load_question", "load_answer"):
            assert loader in names
