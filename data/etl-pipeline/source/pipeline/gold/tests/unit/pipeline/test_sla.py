"""Unit tests for gold.pipeline.sla.resolve_sla precedence + fallback.

Verifies the layered resolution order
(project > tenant > environment > feature/loader > global default), env-var
selection, runtime override source, and safe fallback when config is absent.
"""

from __future__ import annotations

import importlib

import pytest

pytestmark = [pytest.mark.unit]

# A config exercising every layer for the same metric so precedence is testable.
_CONFIG = {
    "bpe": {"freshness_sla": 1800},                       # feature-level default
    "loaders": {"load_qa_recommendation": {"freshness_sla": 600}},
    "sla": {
        "defaults": {"freshness_sla": 900, "tenant_timeout_s": 300, "project_timeout_s": 0},
        "environments": {"dev": {"freshness_sla": 3600}},
        "tenants": {"tnBIG": {"tenant_timeout_s": 600}},
        "projects": {"tnBIG:1006": {"project_timeout_s": 120, "freshness_sla": 60}},
    },
}


@pytest.fixture
def sla(monkeypatch):
    mod = importlib.import_module("gold.pipeline.sla")
    importlib.reload(mod)
    monkeypatch.setattr(mod, "gold_config", lambda: _CONFIG)
    monkeypatch.delenv("GOLD_ENV", raising=False)
    mod.set_override_source(None)
    return mod


class TestPrecedence:
    def test_project_beats_everything(self, sla):
        # project freshness_sla=60 wins over tenant/env/feature/default
        assert sla.resolve_sla("freshness_sla", env="dev", tenant="tnBIG",
                               project="1006", feature="bpe") == 60

    def test_tenant_over_env_and_default(self, sla):
        assert sla.resolve_sla("tenant_timeout_s", env="dev", tenant="tnBIG") == 600

    def test_environment_over_feature_and_default(self, sla):
        # no tenant/project override for freshness in dev → env layer (3600)
        assert sla.resolve_sla("freshness_sla", env="dev") == 3600

    def test_feature_default_when_no_higher_layer(self, sla):
        # prod env has no freshness override → falls to feature-level bpe (1800)
        assert sla.resolve_sla("freshness_sla", env="prod", feature="bpe") == 1800

    def test_loader_level_default(self, sla):
        assert sla.resolve_sla("freshness_sla", feature="load_qa_recommendation") == 600

    def test_global_default_last_resort(self, sla):
        assert sla.resolve_sla("tenant_timeout_s") == 300
        assert sla.resolve_sla("freshness_sla") == 900


class TestEnvAndFallback:
    def test_env_var_selects_environment(self, sla, monkeypatch):
        monkeypatch.setenv("GOLD_ENV", "dev")
        assert sla.resolve_sla("freshness_sla") == 3600

    def test_unknown_metric_returns_none(self, sla):
        assert sla.resolve_sla("nonexistent_metric") is None

    def test_missing_config_no_crash(self, sla, monkeypatch):
        monkeypatch.setattr(sla, "gold_config", lambda: {})
        assert sla.resolve_sla("tenant_timeout_s", tenant="tnBIG") is None


class TestOverrideSource:
    def test_runtime_override_wins(self, sla):
        sla.set_override_source(lambda metric, **kw: 42 if metric == "tenant_timeout_s" else None)
        # beats even the project layer
        assert sla.resolve_sla("tenant_timeout_s", tenant="tnBIG", project="1006") == 42
        # but a None from the source falls through to the static layers
        assert sla.resolve_sla("freshness_sla", env="dev") == 3600
