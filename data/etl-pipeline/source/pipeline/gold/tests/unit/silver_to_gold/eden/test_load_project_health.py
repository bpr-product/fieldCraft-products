"""Unit tests for gold.src.eden.load_project_health (Silver → Gold).

Reference implementation of the suite's testing pattern:
  * pure transformation / utility functions are tested directly
  * every conditional branch + exception path is exercised
  * I/O (psycopg2, pymongo, cursor_store) is mocked via conftest fixtures
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import pytest
from pymongo.errors import AutoReconnect, BulkWriteError, OperationFailure

from gold.tests.unit.silver_to_gold.conftest import (
    EPOCH,
    GOLD_TS,
    SILVER_TS,
    make_bulk_result,
    make_cfg,
    make_collection,
)

pytestmark = [pytest.mark.unit, pytest.mark.eden]

MOD = "gold.src.eden.load_project_health"


@pytest.fixture
def mod():
    m = importlib.import_module(MOD)
    importlib.reload(m)
    m._cb = None
    m._shutdown = False
    return m


@pytest.fixture(autouse=True)
def _default_table_exists(mod, monkeypatch):
    """Default table_exists() to "everything present" so tests that don't
    care about the task_attribute/attributes schema-probe path (TestIterSilverRows
    etc.) don't hit a real Postgres connection via _scoped_query(). TestScopedQuery
    overrides this per-test where the probe result matters.
    """
    monkeypatch.setattr(mod, "table_exists", lambda pg_dsn, table: True)


def _row(**over):
    base = {
        "tenantid": "DEMO-001",
        "project_id": 42,
        "silver_lsn": 100,
        "p_silver_ts": SILVER_TS,
        "order_no": "ORD-9",
        "description": "Roof Job, Austin-Roofing-ORD-9",
        "customer_raw": None,
        "project_uid": "PUID-1",
        "status": "ACTIVE",
        "trade": "ROOFING",
        "location": "TX",
        "completion_pct": 50.0,
        "total_task_count": 10,
        "done_task_count": 5,
        "overdue_task_count": 1,
        "unassigned_task_count": 2,
        "estimated_cost": 1000.0,
        "estimate_version": 3,
        "material_cost": 400.0,
        "worker_cost": 300.0,
        "tool_cost": 100.0,
        "overhead_cost": 50.0,
        "margin": 150.0,
        "committed_spend": 600.0,
        "outstanding_invoices_total": 200.0,
        "schedule_revised_count": 2,
        "slip_days": 3,
        "job_startdate": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "actual_job_startdate": datetime(2026, 1, 4, tzinfo=timezone.utc),
        "job_enddate": datetime(2026, 3, 1, tzinfo=timezone.utc),
        "actual_job_enddate": None,
        "task_silver_ts": SILVER_TS,
        "estimate_silver_ts": None,
        "po_silver_ts": None,
        "invoice_silver_ts": None,
        "schedule_silver_ts": None,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# LoaderConfig.from_args_and_env
# ---------------------------------------------------------------------------


class TestFromArgs:
    def test_defaults(self, mod, monkeypatch):
        monkeypatch.delenv("TENANT_ID", raising=False)
        cfg = mod.LoaderConfig.from_args_and_env(["--tenant", "T1"])
        assert cfg.tenantid == "T1"
        assert cfg.db_name == "gold_eden"
        assert cfg.batch_size == 200
        assert cfg.dry_run is False

    def test_flags_and_env(self, mod, monkeypatch):
        monkeypatch.setenv("TENANT_ID", "ENVT")
        cfg = mod.LoaderConfig.from_args_and_env(
            ["--dry-run", "--batch-size", "5", "--db", "gx", "--max-docs", "7"]
        )
        assert cfg.tenantid == "ENVT"
        assert cfg.dry_run is True
        assert cfg.batch_size == 5
        assert cfg.db_name == "gx"
        assert cfg.max_docs == 7


# ---------------------------------------------------------------------------
# _mask_uri
# ---------------------------------------------------------------------------


class TestMaskUri:
    def test_with_password_and_port(self, mod):
        masked = mod._mask_uri("postgresql://user:secret@host:5432/db")
        assert "secret" not in masked
        assert "***" in masked
        assert ":5432" in masked

    def test_with_password_no_port(self, mod):
        masked = mod._mask_uri("postgresql://user:secret@host/db")
        assert "secret" not in masked and "***" in masked

    def test_no_password_returned_asis(self, mod):
        assert mod._mask_uri("mongodb://host:27017") == "mongodb://host:27017"

    def test_exception_path_falls_back_to_regex(self, mod, monkeypatch):
        # Force urlparse to raise → except branch uses re.sub fallback.
        def boom(_):
            raise ValueError("bad uri")

        monkeypatch.setattr(mod, "urlparse", boom)
        masked = mod._mask_uri("postgresql://user:secret@host/db")
        assert "secret" not in masked and "***" in masked


# ---------------------------------------------------------------------------
# _scoped_query
# ---------------------------------------------------------------------------


class TestScopedQuery:
    def test_none_returns_base(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "table_exists", lambda pg_dsn, table: True)
        monkeypatch.setattr(mod, "get_silver_columns", lambda pg_dsn, table: frozenset({"invalid"}))
        assert mod._scoped_query("dsn", None) != mod._SILVER_QUERY
        assert "__TAE_JOIN__" not in mod._scoped_query("dsn", None)
        assert "__INVALID_FILTER__" not in mod._scoped_query("dsn", None)

    def test_scoped_adds_predicate(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "table_exists", lambda pg_dsn, table: True)
        monkeypatch.setattr(mod, "get_silver_columns", lambda pg_dsn, table: frozenset({"invalid"}))
        unscoped = mod._scoped_query("dsn", None)
        q = mod._scoped_query("dsn", "P9")
        assert "AND p.id = %(project_id)s" in q
        assert q != unscoped

    def test_tae_join_inert_when_tables_absent(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "table_exists", lambda pg_dsn, table: False)
        monkeypatch.setattr(mod, "get_silver_columns", lambda pg_dsn, table: frozenset({"invalid"}))
        q = mod._scoped_query("dsn", None)
        assert "FROM silver.task_attribute ta" not in q
        assert "NULL::text AS status" in q

    def test_tae_join_real_when_tables_present(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "table_exists", lambda pg_dsn, table: True)
        monkeypatch.setattr(mod, "get_silver_columns", lambda pg_dsn, table: frozenset({"invalid"}))
        q = mod._scoped_query("dsn", None)
        assert "FROM silver.task_attribute ta" in q
        assert "JOIN silver.attributes attrs" in q

    def test_tae_join_inert_when_only_one_table_present(self, mod, monkeypatch):
        monkeypatch.setattr(
            mod, "table_exists",
            lambda pg_dsn, table: table == "task_attribute",
        )
        monkeypatch.setattr(mod, "get_silver_columns", lambda pg_dsn, table: frozenset({"invalid"}))
        q = mod._scoped_query("dsn", None)
        assert "FROM silver.task_attribute ta" not in q

    def test_invalid_filter_present_when_column_exists(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "table_exists", lambda pg_dsn, table: True)
        monkeypatch.setattr(mod, "get_silver_columns", lambda pg_dsn, table: frozenset({"invalid"}))
        q = mod._scoped_query("dsn", None)
        assert "AND (at.invalid IS NULL OR at.invalid = FALSE)" in q

    def test_invalid_filter_absent_when_column_missing(self, mod, monkeypatch):
        """Regression test: silver.task.invalid does not exist on the live
        schema (confirmed 2026-08-11 against tn88351775) — referencing it
        unconditionally raised psycopg2.errors.UndefinedColumn on every
        project_health rebuild for that tenant."""
        monkeypatch.setattr(mod, "table_exists", lambda pg_dsn, table: True)
        monkeypatch.setattr(mod, "get_silver_columns", lambda pg_dsn, table: frozenset())
        q = mod._scoped_query("dsn", None)
        assert "at.invalid" not in q


class TestFetchHistoryScoping:
    """Regression guard for this PR's two headline query-scoping claims:
    fetch_estimate_history/fetch_schedule_history now accept project_id and
    scope the SQL to it (6610fb7), and the freed-resource LATERAL inside
    fetch_schedule_history was restructured to pick its winning row before
    joining (b249ae8, verified live: 730ms -> 90ms across 18 real projects,
    byte-identical output). This does NOT re-verify byte-identical output --
    that needs live Silver data and was already done manually per that
    commit's message -- it guards the narrower, regressable thing a unit test
    actually can: that the project_id parameter is still being bound into the
    executed SQL and params at all. Without this, someone could silently drop
    the `if project_id is not None:` branch in either function and every
    OTHER test in this file would keep passing, since none of them call these
    two functions with project_id set.
    """

    def test_estimate_history_scoped_adds_project_filter(self, mod, patch_pg):
        conn, cur = patch_pg(mod, rows=[])
        mod.fetch_estimate_history("dsn", "T1", project_id="P9")
        sql, params = cur.execute.call_args.args
        assert "AND pe.project_id = %(project_id)s" in sql
        assert params.get("project_id") == "P9"

    def test_estimate_history_unscoped_omits_project_filter(self, mod, patch_pg):
        conn, cur = patch_pg(mod, rows=[])
        mod.fetch_estimate_history("dsn", "T1", project_id=None)
        sql, params = cur.execute.call_args.args
        assert "AND pe.project_id = %(project_id)s" not in sql
        assert "project_id" not in params

    def test_schedule_history_scoped_adds_project_filter(self, mod, patch_pg):
        conn, cur = patch_pg(mod, rows=[])
        mod.fetch_schedule_history("dsn", "T1", project_id="P9")
        sql, params = cur.execute.call_args.args
        assert "AND ps.project_id = %(project_id)s" in sql
        assert params.get("project_id") == "P9"

    def test_schedule_history_unscoped_omits_project_filter(self, mod, patch_pg):
        conn, cur = patch_pg(mod, rows=[])
        mod.fetch_schedule_history("dsn", "T1", project_id=None)
        sql, params = cur.execute.call_args.args
        assert "AND ps.project_id = %(project_id)s" not in sql
        assert "project_id" not in params

    def test_schedule_history_freed_resource_lateral_selects_winner_before_join(self, mod):
        """b249ae8's rewrite: pick ORDER BY end_time DESC LIMIT 1 in a scalar
        subquery BEFORE joining to resource/project/the nested schedule
        lateral, instead of joining every candidate row and discarding all
        but one after. Asserts the shape (winner picked first), not full SQL
        text, so this survives incidental rewording of the query."""
        sql = mod._SCHEDULE_HISTORY_QUERY
        # The freed-resource candidate selection must appear, and the
        # ORDER BY ... LIMIT 1 that picks the single winning row must be
        # inside that same candidate subquery -- not applied to the joined
        # result afterward, which is exactly the shape b249ae8 replaced.
        freed_idx = sql.index("rs_other")
        first_limit_after_freed = sql.index("LIMIT 1", freed_idx)
        first_join_after_freed = min(
            (i for i in (
                sql.find(" JOIN silver.resource ", freed_idx),
                sql.find(" JOIN silver.project ", freed_idx),
            ) if i != -1),
            default=len(sql),
        )
        assert first_limit_after_freed < first_join_after_freed, (
            "freed-resource winner must be selected (ORDER BY ... LIMIT 1) "
            "BEFORE joining to resource/project -- if a join appears first, "
            "the O(candidates x versions) join-then-discard shape b249ae8 "
            "fixed has regressed back in."
        )


class TestTenantAdminsQuery:
    """Business rule (2026-07-30): admin assignees are treated as still-
    effective past their assign_end_date — tn24047750's only admin grant had
    a 1-day assign_end_date window that expired months ago, silently dropping
    the tenant's sole admin from every project's assignees array."""

    def test_end_date_not_checked(self, mod):
        assert "urm.assign_end_date" not in mod._TENANT_ADMINS_QUERY

    def test_start_date_still_enforced(self, mod):
        assert "urm.assign_start_date <= NOW()" in mod._TENANT_ADMINS_QUERY

    def test_scoped_to_admin_role(self, mod):
        assert "LOWER(r.name)        = 'admin'" in mod._TENANT_ADMINS_QUERY


# ---------------------------------------------------------------------------
# _to_ts / _float / _int
# ---------------------------------------------------------------------------


class TestCoercion:
    def test_to_ts_none(self, mod):
        assert mod._to_ts(None) is None

    def test_to_ts_naive_gets_utc(self, mod):
        out = mod._to_ts(datetime(2026, 1, 1))
        assert out.tzinfo is timezone.utc

    def test_to_ts_aware_passthrough(self, mod):
        aware = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert mod._to_ts(aware) is aware

    def test_to_ts_non_datetime(self, mod):
        assert mod._to_ts("2026-01-01") is None

    def test_float_none(self, mod):
        assert mod._float(None) is None

    def test_float_parses(self, mod):
        assert mod._float("1.5") == 1.5
        assert mod._float(2) == 2.0

    def test_float_bad(self, mod):
        assert mod._float("not-a-number") is None

    def test_int_none_is_zero(self, mod):
        assert mod._int(None) == 0

    def test_int_parses(self, mod):
        assert mod._int("4") == 4

    def test_int_bad_is_zero(self, mod):
        assert mod._int("x") == 0


# ---------------------------------------------------------------------------
# shape_doc — the core transformation
# ---------------------------------------------------------------------------


class TestShapeDoc:
    def test_full_row_at_risk(self, mod):
        doc = mod.shape_doc(_row(), GOLD_TS)
        assert doc["tenantId"] == "DEMO-001"
        assert doc["projectId"] == "42"
        assert doc["scheduleRisk"] == "at_risk"  # slip_days > 0
        assert doc["slipDays"] == 3
        assert doc["remainingBudget"] == 400.0  # 1000 - 600
        assert doc["completionPct"] == 50.0
        assert doc["_grain"] == "eden_project_health"
        assert doc["_schemaVersion"] == 1
        assert doc["lafieldcraft"] > 0  # watermarks present
        assert doc["jobStartDate"] is not None
        assert doc["actualJobEndDate"] is None

    def test_on_track_and_null_estimate(self, mod):
        doc = mod.shape_doc(
            _row(slip_days=0, estimated_cost=None, estimate_version=0), GOLD_TS
        )
        assert doc["scheduleRisk"] == "on_track"
        assert doc["remainingBudget"] is None  # estimated_cost None
        assert doc["estimatedCost"] is None
        assert doc["estimateVersion"] is None  # _int(0) or None -> None

    def test_no_schedule_is_unknown_not_on_track(self, mod):
        # No project_schedule row at all → must surface as "unknown", never the
        # silent false-negative "on_track".
        doc = mod.shape_doc(
            _row(slip_days=0, schedule_revised_count=None,
                 job_startdate=None, schedule_silver_ts=None),
            GOLD_TS,
        )
        assert doc["scheduleRisk"] == "unknown"
        assert doc["_dataCompleteness"]["schedule"] is False

    def test_data_completeness_and_confidence(self, mod):
        doc = mod.shape_doc(_row(), GOLD_TS)
        dc = doc["_dataCompleteness"]
        # default _row has project + task watermarks and a schedule; estimate /
        # PO / invoice watermarks are None.
        assert dc["project"] is True and dc["task"] is True
        assert dc["schedule"] is True
        assert dc["estimate"] is False and dc["purchaseOrder"] is False
        assert dc["invoice"] is False
        assert doc["_confidence"] == round(3 / 6, 4)

    def test_no_watermarks_lag_zero(self, mod):
        doc = mod.shape_doc(
            _row(
                p_silver_ts=None,
                task_silver_ts=None,
                estimate_silver_ts=None,
                po_silver_ts=None,
                invoice_silver_ts=None,
                schedule_silver_ts=None,
            ),
            GOLD_TS,
        )
        assert doc["lafieldcraft"] == 0

    def test_project_name_prefers_description(self, mod):
        # description is pre-formatted by the source system as
        # "{customer name}, {city}-{trade}-{orderNo}" — the richest available
        # label, so it wins over the parsed customer name and order_no.
        doc = mod.shape_doc(
            _row(description="vijay cus, Chennai-Roof Enhancement-708869",
                 customer_raw='{"firstName":"vijay","lastName":"cus"}'),
            GOLD_TS,
        )
        assert doc["projectName"] == "vijay cus, Chennai-Roof Enhancement-708869"

    def test_project_name_falls_back_to_customer_name(self, mod):
        doc = mod.shape_doc(
            _row(description=None, customer_raw='{"firstName":"vijay","lastName":"cus"}'),
            GOLD_TS,
        )
        assert doc["projectName"] == "vijay cus"

    def test_project_name_falls_back_to_order_no(self, mod):
        doc = mod.shape_doc(_row(description=None, customer_raw=None), GOLD_TS)
        assert doc["projectName"] == "ORD-9"

    def test_project_name_fallback_to_uid(self, mod):
        doc = mod.shape_doc(
            _row(description=None, customer_raw=None, order_no=None), GOLD_TS
        )
        assert doc["projectName"] == "PUID-1"

    def test_live_project_not_tombstoned(self, mod):
        doc = mod.shape_doc(_row(), GOLD_TS)
        assert doc["isDeleted"] is False
        assert doc["deletedAt"] is None

    def test_soft_deleted_project_is_tombstoned(self, mod):
        # A soft-deleted project still recomputes (query keeps deleted rows) and
        # the Gold doc is marked deleted rather than left orphaned. deleted_at
        # arrives as a native datetime (no ::text cast in SQL) and is stored as
        # a native BSON date, not a string.
        deleted_at = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
        doc = mod.shape_doc(
            _row(is_deleted=True, deleted_at=deleted_at), GOLD_TS
        )
        assert doc["isDeleted"] is True
        assert doc["deletedAt"] == deleted_at


# ---------------------------------------------------------------------------
# Estimate history (WI 21303) — collapse + shaping
# ---------------------------------------------------------------------------


def _ev(version, total, *, reason=None, material=None, created=None, schedule_ids=None,
        superseded=True):
    """Build a raw project_estimate version row as fetch_estimate_history returns."""
    return {
        "project_id": 1052,
        "version": version,
        "total_cost": total,
        "material_cost": material if material is not None else total,
        "worker_cost": 0,
        "tool_cost": 0,
        "overhead_cost": 0,
        "margin": 0,
        "reason": reason,
        "created_at": created or (datetime(2026, 6, 10, tzinfo=timezone.utc)
                                  + timedelta(minutes=version)),
        "recorded_at": SILVER_TS,
        "is_superseded": superseded,
        "schedule_ids": schedule_ids or [],
    }


class TestCollapseEstimateHistory:
    def test_empty_returns_empty_and_none(self, mod):
        history, summary = mod.collapse_estimate_history([])
        assert history == [] and summary is None

    def test_single_version_is_baseline_and_current(self, mod):
        history, summary = mod.collapse_estimate_history([_ev(0, 100.0, superseded=False)])
        assert len(history) == 1
        assert history[0]["isCurrent"] is True
        assert history[0]["delta"] is None  # baseline has no delta
        assert summary["currentVersion"] == 0 and summary["baselineVersion"] == 0
        assert summary["meaningfulRevisionCount"] == 0

    def test_subcent_churn_collapses_to_baseline_plus_current(self, mod):
        # Mirrors live project 1052: 5 versions, costs differ only sub-cent, no
        # reason → collapses. Baseline + current kept; meaningful count 0.
        rows = [
            _ev(0, 4925.63477778),
            _ev(1, 4925.63477778),
            _ev(2, 4925.63477777),
            _ev(3, 4925.63477777),
            _ev(4, 4925.63477777, superseded=False),
        ]
        history, summary = mod.collapse_estimate_history(rows)
        assert summary["rawVersionCount"] == 5
        assert summary["meaningfulRevisionCount"] == 0
        assert [h["version"] for h in history] == [0, 4]
        assert history[-1]["isCurrent"] is True
        assert summary["totalCostDelta"] == 0.0  # cent-rounded delta

    def test_reason_change_is_meaningful(self, mod):
        rows = [
            _ev(0, 5000.0),
            _ev(1, 5000.0, reason="Customer Requested To Change", superseded=False),
        ]
        history, summary = mod.collapse_estimate_history(rows)
        assert [h["version"] for h in history] == [0, 1]
        assert summary["meaningfulRevisionCount"] == 1
        assert summary["lastReason"] == "Customer Requested To Change"
        assert history[1]["reason"] == "Customer Requested To Change"

    def test_cost_change_emits_delta(self, mod):
        rows = [
            _ev(0, 5000.0),
            _ev(1, 5300.0, reason="re-estimate", superseded=False),
        ]
        history, summary = mod.collapse_estimate_history(rows)
        assert history[1]["delta"]["totalCost"] == 300.0
        assert summary["totalCostDelta"] == 300.0

    def test_schedule_ids_stringified(self, mod):
        rows = [_ev(0, 100.0, schedule_ids=[412801, 412802], superseded=False)]
        history, _ = mod.collapse_estimate_history(rows)
        assert history[0]["scheduleIds"] == ["412801", "412802"]

    def test_baseline_retained_when_over_cap(self, mod):
        # More than _MAX_HISTORY_ENTRIES meaningful versions → baseline kept + tail.
        n = mod._MAX_HISTORY_ENTRIES + 5
        rows = [_ev(i, 100.0 + i, reason=f"r{i}", superseded=(i != n - 1))
                for i in range(n)]
        history, summary = mod.collapse_estimate_history(rows)
        assert len(history) == mod._MAX_HISTORY_ENTRIES
        assert history[0]["version"] == 0           # baseline always present
        assert history[-1]["version"] == n - 1      # current always present
        assert summary["rawVersionCount"] == n


class TestShapeDocWithHistory:
    def test_history_absent_yields_empty(self, mod):
        doc = mod.shape_doc(_row(), GOLD_TS)
        assert doc["estimateHistory"] == []
        assert doc["estimateSummary"] is None

    def test_history_present_populates_doc(self, mod):
        rows = [
            _ev(0, 5000.0),
            _ev(1, 5300.0, reason="Customer Requested To Change", superseded=False),
        ]
        doc = mod.shape_doc(_row(), GOLD_TS, rows)
        assert len(doc["estimateHistory"]) == 2
        assert doc["estimateSummary"]["currentVersion"] == 1
        assert doc["estimateSummary"]["lastReason"] == "Customer Requested To Change"


# ---------------------------------------------------------------------------
# Schedule history (WI 22463) — collapse + shaping
# ---------------------------------------------------------------------------


def _sv(version, js, je, *, ajs=None, aje=None, reason=None, comment=None,
        estimate_ids=None, superseded=True):
    """Build a raw project_schedule version row as fetch_schedule_history returns.

    js/je/ajs/aje are 'YYYY-MM-DD' strings (or None) → tz-aware datetimes.
    """
    def _d(s):
        return None if s is None else datetime.fromisoformat(s + "T00:00:00+00:00")
    return {
        "project_id": 1032,
        "version": version,
        "job_startdate": _d(js),
        "job_enddate": _d(je),
        "actual_job_startdate": _d(ajs),
        "actual_job_enddate": _d(aje),
        "reason": reason,
        "comment": comment,
        "created_at": datetime(2026, 5, 14, tzinfo=timezone.utc) + timedelta(minutes=version),
        "recorded_at": SILVER_TS,
        "is_superseded": superseded,
        "estimate_ids": estimate_ids or [],
    }


class TestCollapseScheduleHistory:
    def test_empty_returns_empty_and_none(self, mod):
        history, summary = mod.collapse_schedule_history([])
        assert history == [] and summary is None

    def test_single_version_baseline_current(self, mod):
        history, summary = mod.collapse_schedule_history(
            [_sv(0, "2026-05-19", None, superseded=False)]
        )
        assert len(history) == 1
        assert history[0]["isCurrent"] is True
        assert history[0]["delta"] is None
        assert summary["meaningfulRevisionCount"] == 0
        assert summary["originalJobStartDate"] == datetime(2026, 5, 19, tzinfo=timezone.utc)
        assert summary["originalJobEndDate"] is None  # no end date anywhere yet

    def test_identical_versions_collapse(self, mod):
        # Mirrors live 1032 v1→v3: same dates, no reason → collapse to baseline+current.
        rows = [
            _sv(1, "2026-05-19", "2026-05-21"),
            _sv(2, "2026-05-19", "2026-05-21"),
            _sv(3, "2026-05-19", "2026-05-21", superseded=False),
        ]
        history, summary = mod.collapse_schedule_history(rows)
        assert [h["version"] for h in history] == [1, 3]
        assert summary["meaningfulRevisionCount"] == 0
        assert summary["rawVersionCount"] == 3
        assert history[-1]["isCurrent"] is True

    def test_date_change_is_meaningful_with_shift_delta(self, mod):
        rows = [
            _sv(0, "2026-05-19", "2026-05-21"),
            _sv(1, "2026-05-28", "2026-05-29", reason="Task Delay Reschedule",
                superseded=False),
        ]
        history, summary = mod.collapse_schedule_history(rows)
        assert [h["version"] for h in history] == [0, 1]
        assert summary["meaningfulRevisionCount"] == 1
        assert history[1]["delta"]["jobStartShiftDays"] == 9
        assert history[1]["delta"]["jobEndShiftDays"] == 8
        assert summary["totalEndShiftDays"] == 8           # 05-29 vs first end 05-21
        assert summary["lastReason"] == "Task Delay Reschedule"

    def test_end_date_appears_is_meaningful_null_shift(self, mod):
        # v0 has no end; v1 adds one → meaningful, but jobEndShiftDays is null
        # (can't diff against a missing prior end).
        rows = [
            _sv(0, "2026-05-19", None),
            _sv(1, "2026-05-19", "2026-05-21", superseded=False),
        ]
        history, summary = mod.collapse_schedule_history(rows)
        assert summary["meaningfulRevisionCount"] == 1
        assert history[1]["delta"]["jobEndShiftDays"] is None
        assert history[1]["delta"]["jobStartShiftDays"] == 0
        assert summary["originalJobEndDate"] == datetime(2026, 5, 21, tzinfo=timezone.utc)

    def test_estimate_ids_stringified(self, mod):
        rows = [_sv(0, "2026-05-19", "2026-05-21", estimate_ids=[27, 31],
                    superseded=False)]
        history, _ = mod.collapse_schedule_history(rows)
        assert history[0]["estimateIds"] == ["27", "31"]

    def test_baseline_retained_when_over_cap(self, mod):
        n = mod._MAX_HISTORY_ENTRIES + 5
        # every version shifts the end date by 1 day → all meaningful
        rows = [
            _sv(i, "2026-05-19",
                (datetime(2026, 5, 21, tzinfo=timezone.utc)
                 + timedelta(days=i)).date().isoformat(),
                superseded=(i != n - 1))
            for i in range(n)
        ]
        history, summary = mod.collapse_schedule_history(rows)
        assert len(history) == mod._MAX_HISTORY_ENTRIES
        assert history[0]["version"] == 0           # baseline kept
        assert history[-1]["version"] == n - 1      # current kept
        assert summary["meaningfulRevisionCount"] == n - 1


class TestShapeDocWithScheduleHistory:
    def test_absent_yields_empty(self, mod):
        doc = mod.shape_doc(_row(), GOLD_TS)
        assert doc["scheduleRevisions"] == []
        assert doc["scheduleSummary"] is None

    def test_present_populates_doc(self, mod):
        rows = [
            _sv(0, "2026-05-19", "2026-05-21"),
            _sv(1, "2026-05-28", "2026-05-29", reason="Task Delay Reschedule",
                superseded=False),
        ]
        doc = mod.shape_doc(_row(), GOLD_TS, None, rows)
        assert len(doc["scheduleRevisions"]) == 2
        assert doc["scheduleSummary"]["currentVersion"] == 1
        assert doc["scheduleSummary"]["totalEndShiftDays"] == 8


# ---------------------------------------------------------------------------
# iter_silver_rows / build_documents
# ---------------------------------------------------------------------------


class TestIterSilverRows:
    def test_yields_dicts_and_closes(self, mod, patch_pg):
        conn, cur = patch_pg(mod, rows=[_row(), _row(project_id=2)])
        out = list(mod.iter_silver_rows("dsn", "DEMO-001"))
        assert len(out) == 2 and all(isinstance(r, dict) for r in out)
        conn.close.assert_called_once()

    def test_max_docs_limits(self, mod, patch_pg):
        patch_pg(mod, rows=[_row(), _row(project_id=2), _row(project_id=3)])
        out = list(mod.iter_silver_rows("dsn", "DEMO-001", max_docs=2))
        assert len(out) == 2

    def test_project_id_param(self, mod, patch_pg):
        conn, cur = patch_pg(mod, rows=[_row()])
        list(mod.iter_silver_rows("dsn", "DEMO-001", project_id="P9"))
        # the scoped query + project_id param were used
        args, kwargs = cur.execute.call_args
        assert "AND p.id = %(project_id)s" in args[0]
        assert args[1]["project_id"] == "P9"

    def test_build_documents(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "iter_silver_rows", lambda *a, **k: iter([_row()]))
        monkeypatch.setattr(mod, "fetch_estimate_history", lambda *a, **k: {})
        monkeypatch.setattr(mod, "fetch_schedule_history", lambda *a, **k: {})
        monkeypatch.setattr(mod, "fetch_tenant_admins", lambda *a, **k: {})
        docs = mod.build_documents("dsn", "DEMO-001", GOLD_TS)
        assert len(docs) == 1 and docs[0]["projectId"] == "42"

    def test_build_documents_attaches_history(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "iter_silver_rows", lambda *a, **k: iter([_row()]))
        monkeypatch.setattr(mod, "fetch_tenant_admins", lambda *a, **k: {})
        # Keyed by (source_env, project_id) — _row() carries no "source_env" key,
        # so row.get("source_env") is None, matching the (None, "42") lookup key.
        monkeypatch.setattr(
            mod, "fetch_estimate_history",
            lambda *a, **k: {(None, "42"): [_ev(0, 5000.0),
                                    _ev(1, 5300.0, reason="re-bid", superseded=False)]},
        )
        monkeypatch.setattr(
            mod, "fetch_schedule_history",
            lambda *a, **k: {(None, "42"): [_sv(0, "2026-05-19", "2026-05-21"),
                                    _sv(1, "2026-05-28", "2026-05-29",
                                        reason="Task Delay Reschedule", superseded=False)]},
        )
        docs = mod.build_documents("dsn", "DEMO-001", GOLD_TS)
        assert docs[0]["estimateSummary"]["currentVersion"] == 1
        assert docs[0]["estimateSummary"]["totalCostDelta"] == 300.0
        assert docs[0]["scheduleSummary"]["totalEndShiftDays"] == 8


class TestSilverTsHelpers:
    def test_has_new_rows_true(self, mod, patch_pg):
        patch_pg(mod, fetchone=(True,))
        assert mod._has_new_silver_rows("dsn", "DEMO-001", EPOCH) is True

    def test_has_new_rows_false(self, mod, patch_pg):
        patch_pg(mod, fetchone=(False,))
        assert mod._has_new_silver_rows("dsn", "DEMO-001", EPOCH) is False

    def test_max_ts_naive_gets_utc(self, mod, patch_pg):
        patch_pg(mod, fetchone=(datetime(2026, 5, 1),))
        out = mod._get_max_silver_ts("dsn", "DEMO-001")
        assert out.tzinfo is timezone.utc

    def test_max_ts_none_is_epoch(self, mod, patch_pg):
        patch_pg(mod, fetchone=(None,))
        assert mod._get_max_silver_ts("dsn", "DEMO-001") == EPOCH

    def test_max_ts_aware_passthrough(self, mod, patch_pg):
        aware = datetime(2026, 5, 1, tzinfo=timezone.utc)
        patch_pg(mod, fetchone=(aware,))
        assert mod._get_max_silver_ts("dsn", "DEMO-001") == aware


# ---------------------------------------------------------------------------
# bulk_write_with_retry
# ---------------------------------------------------------------------------


class TestBulkWrite:
    def test_success(self, mod):
        coll = make_collection(make_bulk_result(upserted=2, modified=1, matched=1))
        stats = mod.WriteStats()
        mod.bulk_write_with_retry(coll, [object()], make_cfg(mod), stats)
        assert stats.upserted == 2 and stats.modified == 1 and stats.batches == 1

    def test_bulk_write_error_counts_failures(self, mod):
        coll = make_collection()
        coll.bulk_write.side_effect = BulkWriteError(
            {"writeErrors": [{"errmsg": "dup"}, {"errmsg": "dup2"}]}
        )
        stats = mod.WriteStats()
        mod.bulk_write_with_retry(coll, [object()], make_cfg(mod), stats)
        assert stats.failed == 2 and stats.batches == 1

    def test_transient_then_success(self, mod):
        coll = make_collection()
        coll.bulk_write.side_effect = [
            AutoReconnect("boom"),
            make_bulk_result(upserted=1),
        ]
        stats = mod.WriteStats()
        mod.bulk_write_with_retry(coll, [object()], make_cfg(mod, max_retries=3), stats)
        assert stats.upserted == 1

    def test_transient_exhausted_raises(self, mod):
        coll = make_collection()
        coll.bulk_write.side_effect = AutoReconnect("down")
        with pytest.raises(AutoReconnect):
            mod.bulk_write_with_retry(
                coll, [object()], make_cfg(mod, max_retries=2), mod.WriteStats()
            )

    def test_operation_failure_raises(self, mod):
        coll = make_collection()
        coll.bulk_write.side_effect = OperationFailure("bad")
        with pytest.raises(OperationFailure):
            mod.bulk_write_with_retry(
                coll, [object()], make_cfg(mod), mod.WriteStats()
            )


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _wire_run(mod, monkeypatch, rows, *, has_new=True, max_ts=GOLD_TS):
    monkeypatch.setattr(mod, "read_cursor", lambda *a, **k: EPOCH)
    monkeypatch.setattr(mod, "write_cursor", lambda *a, **k: None)
    monkeypatch.setattr(mod, "mark_error", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_has_new_silver_rows", lambda *a, **k: has_new)
    monkeypatch.setattr(mod, "_get_max_silver_ts", lambda *a, **k: max_ts)
    monkeypatch.setattr(mod, "iter_silver_rows", lambda *a, **k: iter(rows))
    monkeypatch.setattr(mod, "fetch_estimate_history", lambda *a, **k: {})
    monkeypatch.setattr(mod, "fetch_schedule_history", lambda *a, **k: {})
    # fetch_tenant_admins is called unconditionally alongside the two above
    # (see run()'s history-fetch block) -- must be mocked too, or run() hits
    # a real Postgres connection for the assignees lookup.
    monkeypatch.setattr(mod, "fetch_tenant_admins", lambda *a, **k: {})
    monkeypatch.setattr(mod, "_contract_validator", __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock())


class TestRun:
    def test_no_new_rows_returns_zero(self, mod, monkeypatch):
        _wire_run(mod, monkeypatch, [], has_new=False)
        assert mod.run(make_cfg(mod, dry_run=True)) == 0

    def test_dry_run_success(self, mod, monkeypatch):
        _wire_run(mod, monkeypatch, [_row(), _row(project_id=2)])
        wrote = []
        monkeypatch.setattr(mod, "write_cursor", lambda *a, **k: wrote.append(a))
        assert mod.run(make_cfg(mod, dry_run=True, batch_size=1)) == 0
        assert not wrote  # dry-run must NOT advance the cursor

    def test_wet_run_writes(self, mod, monkeypatch, patch_mongo):
        coll = patch_mongo(mod)
        _wire_run(mod, monkeypatch, [_row()])
        assert mod.run(make_cfg(mod, dry_run=False)) == 0
        coll.bulk_write.assert_called_once()

    def test_transient_during_iter_returns_3(self, mod, monkeypatch):
        _wire_run(mod, monkeypatch, [])

        def boom(*a, **k):
            raise AutoReconnect("lost")

        monkeypatch.setattr(mod, "iter_silver_rows", boom)
        marks = []
        monkeypatch.setattr(mod, "mark_error", lambda *a, **k: marks.append(a))
        assert mod.run(make_cfg(mod, dry_run=True)) == 3
        assert marks

    def test_bulk_failures_return_3(self, mod, monkeypatch, patch_mongo):
        coll = make_collection()
        coll.bulk_write.side_effect = BulkWriteError({"writeErrors": [{"e": 1}]})
        patch_mongo(mod, coll=coll)
        _wire_run(mod, monkeypatch, [_row()])
        marks = []
        monkeypatch.setattr(mod, "mark_error", lambda *a, **k: marks.append(a))
        assert mod.run(make_cfg(mod, dry_run=False)) == 3
        assert marks

    def test_shutdown_breaks_loop(self, mod, monkeypatch):
        _wire_run(mod, monkeypatch, [_row(), _row(project_id=2)])
        mod._shutdown = True
        try:
            assert mod.run(make_cfg(mod, dry_run=True)) == 0
        finally:
            mod._shutdown = False


# ---------------------------------------------------------------------------
# main + signal handlers
# ---------------------------------------------------------------------------


class TestMainAndSignals:
    def test_main_success(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "_install_signal_handlers", lambda: None)
        monkeypatch.setattr(mod.LoaderConfig, "from_args_and_env", staticmethod(lambda argv=None: make_cfg(mod)))
        monkeypatch.setattr(mod, "run", lambda cfg: 0)
        assert mod.main([]) == 0

    def test_main_config_error_returns_2(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "_install_signal_handlers", lambda: None)

        def raise_cfg(argv=None):
            raise RuntimeError("missing tenant")

        monkeypatch.setattr(mod.LoaderConfig, "from_args_and_env", staticmethod(raise_cfg))
        assert mod.main([]) == 2

    def test_install_signal_handlers_and_handler(self, mod, monkeypatch):
        captured = {}

        def fake_signal(sig, handler):
            captured[sig] = handler

        monkeypatch.setattr(mod.signal, "signal", fake_signal)
        mod._install_signal_handlers()
        assert captured  # at least SIGINT registered
        mod._shutdown = False
        list(captured.values())[0](2, None)  # invoke handler
        assert mod._shutdown is True
        mod._shutdown = False
