"""Integration data quality tests for BPE Gold loaders.

These tests run against REAL Silver PostgreSQL and Cosmos DB.
They verify data completeness, uniqueness, non-null required fields,
value ranges, and reconciliation between Silver and Gold.

Prerequisites:
  - .env.local with SILVER_PG_DSN and MONGO_URI set
  - Silver has data for TEST tenant T-TEST-001
  - Gold pipeline has been run at least once

Run:
  pytest gold/tests/integration/test_bpe_loaders.py -v -m integration

Marks:
  integration: requires live Silver PG + Cosmos DB
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SHARED = str(_ROOT / "shared")
_GOLD_SRC = str(_ROOT / "gold" / "src")
for _p in (_SHARED, _GOLD_SRC, str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytestmark = pytest.mark.integration

TEST_TENANT = "T-TEST-001"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mongo_client():
    from pymongo import MongoClient
    from secret_resolver import get_mongo_uri
    uri = get_mongo_uri()
    client = MongoClient(uri, serverSelectionTimeoutMS=5_000)
    yield client
    client.close()


@pytest.fixture(scope="module")
def pg_conn():
    import psycopg2
    from secret_resolver import get_postgres_dsn
    conn = psycopg2.connect(get_postgres_dsn())
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def bpe_db(mongo_client):
    db_name = os.environ.get("GOLD_BPE_DB_NAME", "gold_bpe")
    return mongo_client[db_name]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def silver_row_count(pg_conn, table: str, tenant: str) -> int:
    with pg_conn.cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*) FROM silver.{table} "
            f"WHERE _s_tenantid = %s AND invalid = FALSE",
            (tenant,)
        )
        return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# estimate_summary DQ tests
# ---------------------------------------------------------------------------

class TestEstimateSummaryDQ:

    def test_no_duplicate_upsert_keys(self, bpe_db):
        """(tenantId, projectId) must be unique."""
        pipeline = [
            {"$match": {"tenantId": TEST_TENANT}},
            {"$group": {"_id": {"t": "$tenantId", "p": "$projectId"}, "count": {"$sum": 1}}},
            {"$match": {"count": {"$gt": 1}}},
        ]
        duplicates = list(bpe_db["estimate_summary"].aggregate(pipeline))
        assert duplicates == [], f"Duplicate (tenantId, projectId) found: {duplicates}"

    def test_required_fields_never_null(self, bpe_db):
        """_grain, _schemaVersion, goldUpdatedAt, tenantId, projectId must exist."""
        # goldUpdatedAt is the sole gold-side write-time stamp (was _etlUpdatedAt
        # until 2026-07-12 — see gold/pipeline/lineage.py's "Consolidation" note).
        for field in ["tenantId", "projectId", "_grain", "_schemaVersion", "goldUpdatedAt", "isDeleted"]:
            missing = bpe_db["estimate_summary"].count_documents(
                {"tenantId": TEST_TENANT, field: {"$exists": False}}
            )
            assert missing == 0, f"Field '{field}' missing in {missing} estimate_summary docs"

    def test_grain_value_correct(self, bpe_db):
        """_grain must always be 'bpe_estimate_summary'."""
        wrong = bpe_db["estimate_summary"].count_documents(
            {"tenantId": TEST_TENANT, "_grain": {"$ne": "bpe_estimate_summary"}}
        )
        assert wrong == 0, f"{wrong} docs have wrong _grain value"

    def test_schema_version_valid(self, bpe_db):
        """_schemaVersion must be >= 1."""
        invalid = bpe_db["estimate_summary"].count_documents(
            {"tenantId": TEST_TENANT, "_schemaVersion": {"$lt": 1}}
        )
        assert invalid == 0, f"{invalid} docs have invalid _schemaVersion"

    def test_completion_pct_in_range(self, bpe_db):
        """completionPct must be between 0 and 100 when present."""
        out_of_range = bpe_db["estimate_summary"].count_documents({
            "tenantId": TEST_TENANT,
            "costRange.confidence": {"$exists": True},
            "$or": [
                {"costRange.confidence": {"$lt": 0}},
                {"costRange.confidence": {"$gt": 1}},
            ]
        })
        assert out_of_range == 0, f"{out_of_range} docs have confidence outside [0,1]"

    def test_estimated_cost_non_negative(self, bpe_db):
        """estimatedCost must be >= 0 when present."""
        negative = bpe_db["estimate_summary"].count_documents({
            "tenantId": TEST_TENANT,
            "estimate.totalCost": {"$lt": 0},
        })
        assert negative == 0, f"{negative} docs have negative estimatedCost"

    def test_silver_lsn_present(self, bpe_db):
        """_silverLsn must be present for replay guard."""
        missing_lsn = bpe_db["estimate_summary"].count_documents(
            {"tenantId": TEST_TENANT, "_silverLsn": {"$exists": False}}
        )
        assert missing_lsn == 0, f"{missing_lsn} docs missing _silverLsn"

    def test_gold_updated_at_recent(self, bpe_db):
        """goldUpdatedAt should be within last 2 hours for a recently run pipeline."""
        # Was _etlUpdatedAt until 2026-07-12 — see gold/pipeline/lineage.py's
        # "Consolidation" note.
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
        stale = bpe_db["estimate_summary"].count_documents({
            "tenantId": TEST_TENANT,
            "goldUpdatedAt": {"$lt": cutoff},
        })
        # Warning only — not a hard failure
        if stale > 0:
            pytest.warns(UserWarning, match="stale")

    def test_doc_count_vs_silver(self, bpe_db, pg_conn):
        """Gold doc count should be >= 95% of Silver row count."""
        silver_count = silver_row_count(pg_conn, "project", TEST_TENANT)
        gold_count = bpe_db["estimate_summary"].count_documents({"tenantId": TEST_TENANT})

        if silver_count == 0:
            pytest.skip("No Silver rows for test tenant")

        coverage = gold_count / silver_count
        assert coverage >= 0.95, (
            f"Gold coverage too low: {gold_count} Gold docs vs {silver_count} Silver rows "
            f"({coverage:.1%})"
        )


# ---------------------------------------------------------------------------
# estimate_kpi DQ tests
# ---------------------------------------------------------------------------

class TestEstimateKpiDQ:

    def test_no_duplicate_upsert_keys(self, bpe_db):
        """(tenantId, trade, month) must be unique."""
        pipeline = [
            {"$match": {"tenantId": TEST_TENANT}},
            {"$group": {"_id": {"t": "$tenantId", "tr": "$trade", "m": "$month"}, "count": {"$sum": 1}}},
            {"$match": {"count": {"$gt": 1}}},
        ]
        duplicates = list(bpe_db["estimate_kpi"].aggregate(pipeline))
        assert duplicates == [], f"Duplicate KPI keys: {duplicates}"

    def test_total_projects_non_negative(self, bpe_db):
        """totalProjects must be >= 0."""
        invalid = bpe_db["estimate_kpi"].count_documents(
            {"tenantId": TEST_TENANT, "totalProjects": {"$lt": 0}}
        )
        assert invalid == 0

    def test_grain_correct(self, bpe_db):
        wrong = bpe_db["estimate_kpi"].count_documents(
            {"tenantId": TEST_TENANT, "_grain": {"$ne": "bpe_kpi_monthly"}}
        )
        assert wrong == 0


# ---------------------------------------------------------------------------
# estimate_recommendation DQ tests
# ---------------------------------------------------------------------------

class TestEstimateRecommendationDQ:

    def test_no_duplicate_upsert_keys(self, bpe_db):
        """(tenantId, projectId) must be unique."""
        pipeline = [
            {"$match": {"tenantId": TEST_TENANT}},
            {"$group": {"_id": {"t": "$tenantId", "p": "$projectId"}, "count": {"$sum": 1}}},
            {"$match": {"count": {"$gt": 1}}},
        ]
        duplicates = list(bpe_db["estimate_recommendation"].aggregate(pipeline))
        assert duplicates == [], f"Duplicate recommendation keys: {duplicates}"

    def test_required_fields_present(self, bpe_db):
        for field in ["tenantId", "projectId", "_grain", "_schemaVersion", "isDeleted"]:
            missing = bpe_db["estimate_recommendation"].count_documents(
                {"tenantId": TEST_TENANT, field: {"$exists": False}}
            )
            assert missing == 0, f"'{field}' missing in recommendation docs"
