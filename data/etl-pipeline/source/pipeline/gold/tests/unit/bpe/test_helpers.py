"""Unit tests for BPE helper functions — pure, no external connections."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.mark.bpe
class TestFloatOrNone:
    def _fn(self):
        from gold.src.bpe.load_estimate_snapshot import _float_or_none
        return _float_or_none

    def test_none_returns_none(self):       assert self._fn()(None) is None
    def test_valid_int(self):               assert self._fn()(5) == 5.0
    def test_valid_float_string(self):      assert self._fn()("3.14") == 3.14
    def test_invalid_string_returns_none(self): assert self._fn()("abc") is None
    def test_zero(self):                    assert self._fn()(0) == 0.0
    def test_negative(self):                assert self._fn()(-1.5) == -1.5
    def test_list_returns_none(self):       assert self._fn()([1, 2]) is None


