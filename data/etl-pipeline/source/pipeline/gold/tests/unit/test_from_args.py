"""Unit tests for LoaderConfig.from_args_and_env across all Gold loaders."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Patch get_mongo_uri / get_postgres_dsn so tests never need real secrets.
_MONGO = "mongodb://test:27017"
_PG = "postgresql://test/db"

_ENV_BASE = {
    "TENANT_ID": "T-001",
}


def _run(import_path: str, extra_args: list[str] | None = None, env: dict | None = None):
    """Import module and call from_args_and_env with patched secret functions."""
    import importlib
    mod = importlib.import_module(import_path)
    args = ["--mongo-uri", _MONGO, "--pg-dsn", _PG, "--tenant", "T-001"]
    if extra_args:
        args += extra_args
    merged_env = {**_ENV_BASE, **(env or {})}
    with patch.dict(os.environ, merged_env, clear=False):
        return mod.LoaderConfig.from_args_and_env(args)


# ---------------------------------------------------------------------------
# BPE loaders
# ---------------------------------------------------------------------------

class TestFromArgsEstimateSnapshot:
    MOD = "gold.src.bpe.load_estimate_snapshot"

    def test_defaults(self):
        cfg = _run(self.MOD)
        assert cfg.db_name == "gold_bpe"
        assert cfg.max_docs == 0

    def test_db_env_var(self):
        cfg = _run(self.MOD, env={"GOLD_BPE_DB_NAME": "snap_db"})
        assert cfg.db_name == "snap_db"

    def test_dry_run(self):
        cfg = _run(self.MOD, ["--dry-run"])
        assert cfg.dry_run is True

    def test_max_docs(self):
        cfg = _run(self.MOD, ["--max-docs", "200"])
        assert cfg.max_docs == 200


# ---------------------------------------------------------------------------
# Eden loaders
# ---------------------------------------------------------------------------

class TestFromArgsProjectHealth:
    MOD = "gold.src.eden.load_project_health"

    def test_defaults(self):
        cfg = _run(self.MOD)
        assert cfg.db_name == "gold_eden"
        assert cfg.max_docs == 0

    def test_db_env_var(self):
        cfg = _run(self.MOD, env={"GOLD_EDEN_DB": "my_eden"})
        assert cfg.db_name == "my_eden"

    def test_dry_run(self):
        cfg = _run(self.MOD, ["--dry-run"])
        assert cfg.dry_run is True

    def test_max_docs(self):
        cfg = _run(self.MOD, ["--max-docs", "500"])
        assert cfg.max_docs == 500

    def test_retry_backoff(self):
        cfg = _run(self.MOD, ["--retry-backoff", "2.5"])
        assert cfg.retry_backoff_s == 2.5


class TestFromArgsInvoiceSummary:
    MOD = "gold.src.eden.load_invoice_summary"

    def test_defaults(self):
        cfg = _run(self.MOD)
        assert cfg.db_name == "gold_eden"

    def test_db_env_var(self):
        cfg = _run(self.MOD, env={"GOLD_EDEN_DB": "my_eden"})
        assert cfg.db_name == "my_eden"

    def test_dry_run(self):
        cfg = _run(self.MOD, ["--dry-run"])
        assert cfg.dry_run is True

    def test_max_docs(self):
        cfg = _run(self.MOD, ["--max-docs", "10"])
        assert cfg.max_docs == 10


class TestFromArgsTaskIntelligence:
    MOD = "gold.src.eden.load_task_intelligence"

    def test_defaults(self):
        cfg = _run(self.MOD)
        assert cfg.db_name == "gold_eden"

    def test_db_env_var(self):
        cfg = _run(self.MOD, env={"GOLD_EDEN_DB": "my_eden"})
        assert cfg.db_name == "my_eden"

    def test_dry_run(self):
        cfg = _run(self.MOD, ["--dry-run"])
        assert cfg.dry_run is True

    def test_batch_size(self):
        cfg = _run(self.MOD, ["--batch-size", "150"])
        assert cfg.batch_size == 150


class TestFromArgsProcurementStatus:
    MOD = "gold.src.eden.load_procurement_status"

    def test_defaults(self):
        cfg = _run(self.MOD)
        assert cfg.db_name == "gold_eden"

    def test_db_env_var(self):
        cfg = _run(self.MOD, env={"GOLD_EDEN_DB": "my_eden"})
        assert cfg.db_name == "my_eden"

    def test_dry_run(self):
        cfg = _run(self.MOD, ["--dry-run"])
        assert cfg.dry_run is True


class TestFromArgsBidContext:
    MOD = "gold.src.eden.load_bid_context"

    def test_defaults(self):
        cfg = _run(self.MOD)
        assert cfg.db_name == "gold_eden"

    def test_db_env_var(self):
        cfg = _run(self.MOD, env={"GOLD_EDEN_DB": "my_eden"})
        assert cfg.db_name == "my_eden"

    def test_dry_run(self):
        cfg = _run(self.MOD, ["--dry-run"])
        assert cfg.dry_run is True

    def test_max_docs(self):
        cfg = _run(self.MOD, ["--max-docs", "25"])
        assert cfg.max_docs == 25


class TestFromArgsTenantDashboard:
    MOD = "gold.src.eden.load_tenant_dashboard"

    def test_defaults(self):
        cfg = _run(self.MOD)
        assert cfg.db_name == "gold_eden"

    def test_db_env_var(self):
        cfg = _run(self.MOD, env={"GOLD_EDEN_DB": "my_eden"})
        assert cfg.db_name == "my_eden"

    def test_dry_run(self):
        cfg = _run(self.MOD, ["--dry-run"])
        assert cfg.dry_run is True

    def test_max_docs(self):
        cfg = _run(self.MOD, ["--max-docs", "5"])
        assert cfg.max_docs == 5


# ---------------------------------------------------------------------------
# Q&A Recommendation loader
# ---------------------------------------------------------------------------

class TestFromArgsQaRecommendation:
    MOD = "gold.src.reference_project.load_qa_recommendation"

    def test_defaults(self):
        cfg = _run(self.MOD)
        assert cfg.db_name == "gold_qa_recommendation"
        assert cfg.max_docs == 0

    def test_db_env_var(self):
        cfg = _run(self.MOD, env={"GOLD_QA_RECOMMENDATION_DB": "my_qa"})
        assert cfg.db_name == "my_qa"

    def test_dry_run(self):
        cfg = _run(self.MOD, ["--dry-run"])
        assert cfg.dry_run is True

    def test_max_docs(self):
        cfg = _run(self.MOD, ["--max-docs", "50"])
        assert cfg.max_docs == 50

    def test_batch_size(self):
        cfg = _run(self.MOD, ["--batch-size", "100"])
        assert cfg.batch_size == 100
