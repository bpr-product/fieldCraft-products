"""Regression test for the freshness Gauge re-instantiation bug (PR #7120 / WI 22227).

_emit_metric used to construct new prometheus_client.Gauge objects on every call.
prometheus_client forbids registering the same metric name twice in the default
registry, so the second tenant raised — and the bare `except: pass` swallowed it,
silently dropping every freshness metric after the first tenant in a multi-tenant
run. The fix builds each Gauge once at module scope and only calls
.labels(...).set(...) per call.

prometheus_client is not a test dependency, so we inject a fake module that
reproduces the duplicate-registration failure mode.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


class _FakeGauge:
    """Mimics prometheus_client.Gauge: a metric name may only be created once."""
    _registered: set[str] = set()
    instantiations: int = 0

    def __init__(self, name, doc, labelnames):
        if name in _FakeGauge._registered:
            raise ValueError(f"Duplicated timeseries in CollectorRegistry: {name}")
        _FakeGauge._registered.add(name)
        _FakeGauge.instantiations += 1
        self.name = name
        self.sets: list[tuple] = []

    def labels(self, **kwargs):
        # return a setter object capturing .set(value)
        sets = self.sets
        return SimpleNamespace(set=lambda v: sets.append((tuple(sorted(kwargs.items())), v)))


@pytest.mark.monitoring
def test_emit_metric_reuses_gauges_across_tenants():
    import gold.src.monitoring.freshness as fr

    # Reset module-level cache + fake registry for a clean run.
    fr._lag_gauge = None
    fr._stale_gauge = None
    _FakeGauge._registered = set()
    _FakeGauge.instantiations = 0

    fake_mod = SimpleNamespace(Gauge=_FakeGauge)

    with patch.dict(sys.modules, {"prometheus_client": fake_mod}):
        # Three tenants — the old code would raise on the 2nd (duplicate Gauge).
        fr._emit_metric("T-001", "project_health", 12.0, False)
        fr._emit_metric("T-002", "project_health", 800.0, True)
        fr._emit_metric("T-003", "invoice_summary", None, False)

    # Exactly two Gauge objects ever created (lag + stale), not one pair per call.
    assert _FakeGauge.instantiations == 2
    # Same cached objects returned on subsequent lookups.
    g1, g2 = fr._freshness_gauges()
    assert g1 is fr._lag_gauge and g2 is fr._stale_gauge


@pytest.mark.monitoring
def test_emit_metric_silent_when_prometheus_absent():
    import gold.src.monitoring.freshness as fr
    fr._lag_gauge = None
    fr._stale_gauge = None

    # Simulate prometheus_client not installed → import raises inside the helper.
    with patch.dict(sys.modules, {"prometheus_client": None}):
        # Must not raise even though the import fails.
        fr._emit_metric("T-001", "project_health", 5.0, False)
