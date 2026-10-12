"""Unit tests for Gold loader document shaping functions.

Tests run without any external connections (no Mongo, no PostgreSQL).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make gold importable from the tests directory
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# setup_gold_feat_bpe vector index error propagation
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="quarantined: superseded by gold/tests/unit/silver_to_gold suite (drifted from cursor_store/shape_doc changes)")
class TestVectorIndexErrorPropagation:
    def test_raises_on_non_existing_failure(self, monkeypatch):
        """_ensure_vector_index must raise RuntimeError for non-'already exists' errors."""
        from pymongo.errors import OperationFailure
        from gold.src.bpe import setup_gold_feat_bpe

        class _FakeDB:
            name = "gold_bpe"
            def command(self, cmd):
                raise OperationFailure("disk quota exceeded", code=1)

        with pytest.raises(RuntimeError, match="disk quota exceeded"):
            setup_gold_feat_bpe._ensure_vector_index(
                _FakeDB(), "estimate_summary", "embedding",
                "bpe_estimate_vsearch", 4, "vector-ivf", "COS",
            )

    def test_no_raise_on_already_exists(self, monkeypatch):
        """_ensure_vector_index must NOT raise when index already exists."""
        from pymongo.errors import OperationFailure
        from gold.src.bpe import setup_gold_feat_bpe

        class _FakeDB:
            name = "gold_bpe"
            def command(self, cmd):
                raise OperationFailure("index already exists", code=85)

        # should not raise
        setup_gold_feat_bpe._ensure_vector_index(
            _FakeDB(), "estimate_summary", "embedding",
            "bpe_estimate_vsearch", 4, "vector-ivf", "COS",
        )
