"""Regression test: a dependency that isn't in THIS run's step set must not block
its dependent (e.g. a feature filter that slices steps out must not block remaining
steps). Bug caught by the rebuild drill 2026-06-11.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import gold.pipeline.tenant_runner as tr  # noqa: E402

pytestmark = pytest.mark.pipeline


def _run(monkeypatch, steps, profiles):
    monkeypatch.setattr(tr, "build_steps", lambda **kw: steps)
    monkeypatch.setattr(tr, "get_profile", lambda name: profiles.get(name))
    monkeypatch.setattr(tr, "run_dq_gate", lambda *a, **k: None)
    return tr.run_tenant(
        tenant="T-1", mongo_uri=None, pg_dsn=None, bpe_db="gold_bpe",
        batch_size=10, dry_run=True, skip_setup=True,
    )


def _prof(depends_on):
    return {"depends_on": depends_on, "weight": "light", "reason": ""}


def test_dep_not_in_plan_does_not_block(monkeypatch):
    ran = []
    steps = [("B", lambda: (ran.append("B"), 0)[1])]
    # B depends on A, but A is NOT in this run's plan (e.g. excluded by --rebuild)
    profiles = {"B": _prof(["A"])}
    failed = _run(monkeypatch, steps, profiles)
    assert ran == ["B"], "B should run even though its dep A is not scheduled"
    assert failed == []


def test_dep_in_plan_but_failed_blocks(monkeypatch):
    ran = []
    steps = [
        ("A", lambda: (ran.append("A"), 1)[1]),   # A fails (rc=1)
        ("B", lambda: (ran.append("B"), 0)[1]),
    ]
    profiles = {"A": _prof([]), "B": _prof(["A"])}
    failed = _run(monkeypatch, steps, profiles)
    assert "A" in failed                     # A failed
    assert "B" in failed                     # B skipped because its scheduled dep A failed
    assert "B" not in ran                    # B never executed
