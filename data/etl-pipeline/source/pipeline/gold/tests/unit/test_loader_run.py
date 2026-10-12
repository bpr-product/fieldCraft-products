"""Unit tests for loader run() functions — mocked Mongo + Postgres."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _silver_rows(n=2, **extra):
    base = {
        "tenantid": "T-001",
        "trade": "roofing",
        "month": "2024-01",
        "total_projects": n,
        "projects_with_estimate": n,
        "total_estimated_cost": 5000.0,
        "avg_estimated_cost": 2500.0,
        "avg_material_cost": None,
        "avg_worker_cost": None,
        "avg_tool_cost": None,
        "avg_overhead_cost": None,
        "avg_margin": None,
        "avg_tax_pct": None,
        "total_bom_lines": 0,
        "avg_bom_lines_per_project": None,
        "is_deleted": False,
        "silver_lsn": 42,
        "_silver_lsn": 42,
    }
    base.update(extra)
    return [base] * n


def _make_mongo_mock():
    client = MagicMock()
    coll = MagicMock()
    result = MagicMock()
    result.upserted_count = 1
    result.modified_count = 0
    result.matched_count = 1
    coll.bulk_write.return_value = result
    coll.count_documents.return_value = 10
    client.__getitem__ = MagicMock(return_value=MagicMock(
        __getitem__=MagicMock(return_value=coll)
    ))
    return client, coll


# ---------------------------------------------------------------------------
# load_estimate_summary  run()
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="quarantined: superseded by gold/tests/unit/silver_to_gold suite (drifted from cursor_store/shape_doc changes)")
class TestEstimateSummaryRun:
    def _mod(self):
        import gold.src.bpe.load_estimate_summary as m
        m._cb = None
        return m

    def _cfg(self, mod, dry_run=True):
        return mod.LoaderConfig(
            mongo_uri="mongodb://localhost:27017",
            pg_dsn="postgresql://localhost/test",
            db_name="gold_bpe",
            tenantid="T-001",
            batch_size=10,
            max_retries=2,
            retry_backoff_s=0.0,
            dry_run=dry_run,
        )

    def _rows(self):
        return [{
            "project_id": "p-1", "tenantid": "T-001",
            "trade": "roofing", "status": "active", "project_type": "residential",
            "is_deleted": False, "created_at": None, "updated_at": None,
            "estimate_total_cost": 5000.0, "material_cost": None,
            "worker_cost": None, "tool_cost": None, "overhead_cost": None,
            "margin": None, "bom_line_count": 0, "services_json": None,
            "location": None, "embedding_text": None, "_silver_lsn": 1,
        }]

    def test_dry_run_returns_zero(self):
        mod = self._mod()
        with patch.object(mod, "iter_silver_rows", return_value=iter(self._rows())):
            rc = mod.run(self._cfg(mod))
        assert rc == 0

    def test_empty_silver(self):
        mod = self._mod()
        with patch.object(mod, "iter_silver_rows", return_value=iter([])):
            rc = mod.run(self._cfg(mod))
        assert rc == 0
