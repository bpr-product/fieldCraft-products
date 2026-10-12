"""A fatal DQ gate finding must fail the step so it propagates to the process
exit code (closes the false-green where a loader returns rc=0 but wrote bad data).
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


def _prof(depends_on):
    return {"depends_on": depends_on, "weight": "light", "reason": ""}


def _run(monkeypatch, steps, profiles, gate_return):
    monkeypatch.setattr(tr, "build_steps", lambda **kw: steps)
    monkeypatch.setattr(tr, "get_profile", lambda name: profiles.get(name))
    monkeypatch.setattr(tr, "run_dq_gate", lambda *a, **k: gate_return)
    return tr.run_tenant(
        tenant="T-1", mongo_uri=None, pg_dsn=None, bpe_db="gold_bpe",
        batch_size=10, dry_run=True, skip_setup=True,
    )


def test_fatal_gate_fails_step(monkeypatch):
    steps = [("load_bid_context", lambda: 0)]   # loader "succeeds"
    failed = _run(monkeypatch, steps, {"load_bid_context": _prof([])},
                  gate_return=["DQ_BROKEN_EMPTY"])
    assert "load_bid_context" in failed, "fatal gate finding must fail the step"


def test_clean_gate_keeps_step_green(monkeypatch):
    steps = [("load_bid_context", lambda: 0)]
    failed = _run(monkeypatch, steps, {"load_bid_context": _prof([])},
                  gate_return=[])
    assert failed == []


def test_gate_failure_blocks_dependents(monkeypatch):
    # A passes its loader but fails the gate; B depends on A => B must be skipped.
    steps = [("A", lambda: 0), ("B", lambda: 0)]
    profiles = {"A": _prof([]), "B": _prof(["A"])}
    monkeypatch.setattr(tr, "build_steps", lambda **kw: steps)
    monkeypatch.setattr(tr, "get_profile", lambda name: profiles.get(name))
    # Only A is gated; its gate fails. B's gate (if reached) would pass.
    monkeypatch.setattr(tr, "run_dq_gate",
                        lambda name, *a, **k: ["DQ_CONTRACT_VIOLATION"] if name == "A" else [])
    failed = tr.run_tenant(
        tenant="T-1", mongo_uri=None, pg_dsn=None, bpe_db="gold_bpe",
        batch_size=10, dry_run=True, skip_setup=True,
    )
    assert "A" in failed and "B" in failed
