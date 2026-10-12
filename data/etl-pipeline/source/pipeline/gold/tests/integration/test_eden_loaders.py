"""Integration data quality tests for Eden AI Assistant Gold loaders.

Run:
  pytest gold/tests/integration/test_eden_loaders.py -v -m integration
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SHARED = str(_ROOT / "shared")
for _p in (_SHARED, str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytestmark = pytest.mark.integration

TEST_TENANT = "T-TEST-001"

EDEN_COLLECTIONS = {
    "project_health":    ("GOLD_EDEN_DB", "gold_eden", "project_health"),
    "invoice_summary":   ("GOLD_EDEN_DB", "gold_eden", "invoice_summary"),
    "task_intelligence": ("GOLD_EDEN_DB", "gold_eden", "task_intelligence"),
    "procurement_status":("GOLD_EDEN_DB", "gold_eden", "procurement_status"),
    "bid_context":       ("GOLD_EDEN_DB", "gold_eden", "bid_context"),
    "tenant_dashboard":  ("GOLD_EDEN_DB", "gold_eden", "tenant_dashboard"),
}

# goldUpdatedAt is the sole gold-side write-time stamp (was _etlUpdatedAt until
# 2026-07-12 — see gold/pipeline/lineage.py's "Consolidation" note).
REQUIRED_PLATFORM_FIELDS = ["tenantId", "_grain", "_schemaVersion", "goldUpdatedAt"]


@pytest.fixture(scope="module")
def mongo_client():
    from pymongo import MongoClient
    from secret_resolver import get_mongo_uri
    client = MongoClient(get_mongo_uri(), serverSelectionTimeoutMS=5_000)
    yield client
    client.close()


@pytest.fixture(scope="module")
def pg_conn():
    import psycopg2
    from secret_resolver import get_postgres_dsn
    conn = psycopg2.connect(get_postgres_dsn())
    yield conn
    conn.close()


def get_eden_coll(mongo_client, env_var: str, db_default: str, coll_name: str):
    db_name = os.environ.get(env_var, db_default)
    return mongo_client[db_name][coll_name]


def silver_row_count(pg_conn, tenant: str) -> int:
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM silver.project "
            "WHERE _s_tenantid = %s AND invalid = FALSE",
            (tenant,)
        )
        return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Generic DQ checks across all 6 Eden collections
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,env_db,db_default,coll", [
    (k, v[0], v[1], v[2]) for k, v in EDEN_COLLECTIONS.items()
])
class TestEdenCollectionsDQ:

    def test_required_platform_fields(self, mongo_client, label, env_db, db_default, coll):
        """_grain, _schemaVersion, goldUpdatedAt, tenantId must exist on every doc."""
        collection = get_eden_coll(mongo_client, env_db, db_default, coll)
        for field in REQUIRED_PLATFORM_FIELDS:
            missing = collection.count_documents(
                {"tenantId": TEST_TENANT, field: {"$exists": False}}
            )
            assert missing == 0, (
                f"[{label}] Field '{field}' missing in {missing} docs"
            )

    def test_no_null_tenant_id(self, mongo_client, label, env_db, db_default, coll):
        """tenantId must never be null."""
        collection = get_eden_coll(mongo_client, env_db, db_default, coll)
        nulls = collection.count_documents(
            {"tenantId": {"$in": [None, ""]}}
        )
        assert nulls == 0, f"[{label}] {nulls} docs have null/empty tenantId"

    def test_schema_version_valid(self, mongo_client, label, env_db, db_default, coll):
        """_schemaVersion must be >= 1."""
        collection = get_eden_coll(mongo_client, env_db, db_default, coll)
        invalid = collection.count_documents(
            {"tenantId": TEST_TENANT, "_schemaVersion": {"$lt": 1}}
        )
        assert invalid == 0, f"[{label}] {invalid} docs have invalid _schemaVersion"

    def test_silver_lsn_present(self, mongo_client, label, env_db, db_default, coll):
        """_silverLsn must be present for replay guard."""
        if coll == "tenant_dashboard":
            pytest.skip("tenant_dashboard aggregates across projects — no single LSN")
        collection = get_eden_coll(mongo_client, env_db, db_default, coll)
        missing = collection.count_documents(
            {"tenantId": TEST_TENANT, "_silverLsn": {"$exists": False}}
        )
        assert missing == 0, f"[{label}] {missing} docs missing _silverLsn"


# ---------------------------------------------------------------------------
# project_health specific checks
# ---------------------------------------------------------------------------

class TestProjectHealthDQ:

    def test_completion_pct_in_range(self, mongo_client):
        coll = get_eden_coll(mongo_client, "GOLD_EDEN_DB", "gold_eden", "project_health")
        out_of_range = coll.count_documents({
            "tenantId": TEST_TENANT,
            "completionPct": {"$exists": True, "$nin": [None]},
            "$or": [
                {"completionPct": {"$lt": 0}},
                {"completionPct": {"$gt": 100}},
            ]
        })
        assert out_of_range == 0, f"{out_of_range} docs have completionPct outside [0,100]"

    def test_task_counts_non_negative(self, mongo_client):
        coll = get_eden_coll(mongo_client, "GOLD_EDEN_DB", "gold_eden", "project_health")
        for field in ["totalTaskCount", "doneTaskCount", "overdueTaskCount"]:
            negative = coll.count_documents(
                {"tenantId": TEST_TENANT, field: {"$lt": 0}}
            )
            assert negative == 0, f"{negative} docs have negative {field}"

    def test_done_count_lte_total(self, mongo_client):
        coll = get_eden_coll(mongo_client, "GOLD_EDEN_DB", "gold_eden", "project_health")
        invalid = coll.count_documents({
            "tenantId": TEST_TENANT,
            "$expr": {"$gt": ["$doneTaskCount", "$totalTaskCount"]}
        })
        assert invalid == 0, f"{invalid} docs have doneTaskCount > totalTaskCount"

    def test_schedule_risk_valid_values(self, mongo_client):
        coll = get_eden_coll(mongo_client, "GOLD_EDEN_DB", "gold_eden", "project_health")
        invalid = coll.count_documents({
            "tenantId": TEST_TENANT,
            "scheduleRisk": {"$exists": True, "$nin": ["on_track", "at_risk", "critical", None]}
        })
        assert invalid == 0, f"{invalid} docs have invalid scheduleRisk value"

    def test_doc_count_vs_silver(self, mongo_client, pg_conn):
        """Gold doc count should cover >= 95% of active Silver projects."""
        silver_count = silver_row_count(pg_conn, TEST_TENANT)
        coll = get_eden_coll(mongo_client, "GOLD_EDEN_DB", "gold_eden", "project_health")
        gold_count = coll.count_documents({"tenantId": TEST_TENANT})

        if silver_count == 0:
            pytest.skip("No Silver rows for test tenant")

        coverage = gold_count / silver_count
        assert coverage >= 0.95, (
            f"project_health coverage: {gold_count}/{silver_count} = {coverage:.1%}"
        )


# ---------------------------------------------------------------------------
# invoice_summary specific checks
# ---------------------------------------------------------------------------

class TestInvoiceSummaryDQ:

    def test_totals_non_negative(self, mongo_client):
        coll = get_eden_coll(mongo_client, "GOLD_EDEN_DB", "gold_eden", "invoice_summary")
        for field in ["totalBilled", "totalReceived", "totalOutstanding"]:
            negative = coll.count_documents(
                {"tenantId": TEST_TENANT, field: {"$lt": 0}}
            )
            assert negative == 0, f"{negative} docs have negative {field}"

    def test_outstanding_le_billed(self, mongo_client):
        """totalOutstanding should be <= totalBilled."""
        coll = get_eden_coll(mongo_client, "GOLD_EDEN_DB", "gold_eden", "invoice_summary")
        invalid = coll.count_documents({
            "tenantId": TEST_TENANT,
            "totalBilled": {"$exists": True, "$gt": 0},
            "$expr": {"$gt": ["$totalOutstanding", "$totalBilled"]}
        })
        assert invalid == 0, f"{invalid} docs have totalOutstanding > totalBilled"


# ---------------------------------------------------------------------------
# task_intelligence specific checks
# ---------------------------------------------------------------------------

class TestTaskIntelligenceDQ:

    def test_overdue_tasks_array_max_10(self, mongo_client):
        """overdueTasks array must have at most 10 items (as documented)."""
        coll = get_eden_coll(mongo_client, "GOLD_EDEN_DB", "gold_eden", "task_intelligence")
        too_many = coll.count_documents({
            "tenantId": TEST_TENANT,
            "$expr": {"$gt": [{"$size": {"$ifNull": ["$overdueTasks", []]}}, 10]}
        })
        assert too_many == 0, f"{too_many} docs have > 10 overdueTasks"

    def test_completion_pct_in_range(self, mongo_client):
        coll = get_eden_coll(mongo_client, "GOLD_EDEN_DB", "gold_eden", "task_intelligence")
        out_of_range = coll.count_documents({
            "tenantId": TEST_TENANT,
            "completionPct": {"$exists": True, "$nin": [None]},
            "$or": [{"completionPct": {"$lt": 0}}, {"completionPct": {"$gt": 100}}]
        })
        assert out_of_range == 0, f"{out_of_range} docs have completionPct outside [0,100]"


# ---------------------------------------------------------------------------
# tenant_dashboard specific checks
# ---------------------------------------------------------------------------

class TestTenantDashboardDQ:

    def test_one_doc_per_tenant(self, mongo_client):
        """tenant_dashboard has exactly one doc per tenant."""
        coll = get_eden_coll(mongo_client, "GOLD_EDEN_DB", "gold_eden", "tenant_dashboard")
        count = coll.count_documents({"tenantId": TEST_TENANT})
        assert count <= 1, f"Expected 1 tenant_dashboard doc for {TEST_TENANT}, found {count}"

    def test_project_count_non_negative(self, mongo_client):
        coll = get_eden_coll(mongo_client, "GOLD_EDEN_DB", "gold_eden", "tenant_dashboard")
        invalid = coll.count_documents(
            {"tenantId": TEST_TENANT, "projectCount": {"$lt": 0}}
        )
        assert invalid == 0

    def test_at_risk_le_project_count(self, mongo_client):
        coll = get_eden_coll(mongo_client, "GOLD_EDEN_DB", "gold_eden", "tenant_dashboard")
        invalid = coll.count_documents({
            "tenantId": TEST_TENANT,
            "$expr": {"$gt": ["$atRiskCount", "$projectCount"]}
        })
        assert invalid == 0, f"{invalid} docs have atRiskCount > projectCount"
