"""Unit tests for qa_weightage bounds validation in setup_gold_feat_bpe.

Pure unit tests on the validation helpers — no MongoDB required.
"""

from __future__ import annotations

import importlib

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.bpe]

MOD = "gold.src.bpe.setup_gold_feat_bpe"


@pytest.fixture
def mod():
    return importlib.import_module(MOD)


class TestIterMultipliers:
    def test_scalar(self, mod):
        assert list(mod._iter_multipliers(1.2)) == [1.2]

    def test_bool_ignored(self, mod):
        assert list(mod._iter_multipliers(True)) == []

    def test_thresholds_list(self, mod):
        # [[pct, mult], ...] → yields the multipliers
        assert list(mod._iter_multipliers([[0, 1.0], [100, 1.15]])) == [1.0, 1.15]

    def test_non_numeric_skipped(self, mod):
        assert list(mod._iter_multipliers("metal")) == []


class TestValidateQaWeightageBounds:
    def test_clean_config_no_violations(self, mod):
        qaw = {"I.66": {"TuneUp": 0.55, "Enhancement": 1.0}}
        assert mod.validate_qa_weightage_bounds(qaw) == []

    def test_detects_too_high(self, mod):
        qaw = {"I.1": {"Wood": 4.0}}
        out = mod.validate_qa_weightage_bounds(qaw)
        assert len(out) == 1 and "I.1.Wood=4.0" in out[0]

    def test_detects_too_low(self, mod):
        qaw = {"I.2": {"Flat": 0.1}}
        out = mod.validate_qa_weightage_bounds(qaw)
        assert len(out) == 1 and "I.2.Flat=0.1" in out[0]

    def test_thresholds_within_bounds(self, mod):
        qaw = {"I.52": {"_thresholds": [[0, 1.0], [100, 1.15]]}}
        assert mod.validate_qa_weightage_bounds(qaw) == []

    def test_non_dict_options_skipped(self, mod):
        assert mod.validate_qa_weightage_bounds({"bad": "not-a-dict"}) == []

    def test_seeded_defaults_are_clean(self, mod):
        # The defaults shipped in setup must pass their own bounds check.
        qaw = mod._MODEL_CONFIG_DEFAULTS["qa_weightage"]
        assert mod.validate_qa_weightage_bounds(qaw) == []
