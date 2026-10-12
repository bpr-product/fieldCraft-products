"""Unit tests for the Canonical Gold lead loader (load_lead.py).

All tests run without external connections (no Mongo, no PostgreSQL).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TS = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
_TENANT = "DEMO-T1"


def _base_row(**overrides) -> dict:
    row = {
        "tenantid": _TENANT,
        "source_env": "dev",
        "lead_uid": "lead-1",
        "service_name": "Roof Enhancement",
        "external_deal_id": "deal-1",
        "external_contact_id": "contact-1",
        "first_name": "Jane",
        "last_name": "Prospect",
        "email": "jane@example.invalid",
        "phone": "000-000-0000",
        "status": "NEW",
        "sales_stage": "LEAD",
        "sales_stage_entered_at": datetime(2026, 6, 29, tzinfo=timezone.utc),
        "customer_id": None,
        "converted_at": None,
        "project_uid": None,
        "project_id": None,
        "created_at": datetime(2026, 6, 28, tzinfo=timezone.utc),
        "is_deleted": False,
        "silver_lsn": 5,
        "lead_silver_ts": datetime(2026, 6, 30, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


class TestLeadShapeDoc:
    def test_basic_shape(self):
        from gold.src.canonical.load_lead import shape_doc
        doc = shape_doc(_base_row(), _TS)
        assert doc["tenantId"] == _TENANT
        assert doc["leadUid"] == "lead-1"
        assert doc["_grain"] == "canonical_lead"

    def test_service_name_passthrough(self):
        from gold.src.canonical.load_lead import shape_doc
        doc = shape_doc(_base_row(), _TS)
        assert doc["serviceName"] == "Roof Enhancement"

    def test_no_service_id_field_on_doc(self):
        # Explicit direction 2026-09-25: the raw service_id FK is never
        # surfaced as its own Gold field — only the resolved serviceName is.
        from gold.src.canonical.load_lead import shape_doc
        doc = shape_doc(_base_row(), _TS)
        assert "serviceId" not in doc

    def test_service_name_null_when_join_does_not_resolve(self):
        from gold.src.canonical.load_lead import shape_doc
        doc = shape_doc(_base_row(service_name=None), _TS)
        assert doc["serviceName"] is None

    def test_dates_are_parsed(self):
        from gold.src.canonical.load_lead import shape_doc
        doc = shape_doc(_base_row(), _TS)
        assert doc["salesStageEnteredAt"] == datetime(2026, 6, 29, tzinfo=timezone.utc)
        assert doc["createdAt"] == datetime(2026, 6, 28, tzinfo=timezone.utc)
        assert doc["convertedAt"] is None

    def test_lineage_and_source_env(self):
        from gold.src.canonical.load_lead import shape_doc
        doc = shape_doc(_base_row(), _TS)
        assert doc["goldUpdatedAt"] == _TS
        assert doc["sourceEnv"] == "dev"

    def test_contract_validation_passes(self):
        from gold.src.canonical.load_lead import shape_doc, _contract_validator
        doc = shape_doc(_base_row(), _TS)
        assert _contract_validator.check(doc) is None

    def test_numeric_customer_and_project_ids_are_stringified(self):
        # Both are numeric FKs in the source; the contract types them as
        # strings, matching gold_canonical.project.projectId.
        from gold.src.canonical.load_lead import shape_doc
        doc = shape_doc(_base_row(customer_id=4521, project_id=9001), _TS)
        assert doc["customerId"] == "4521"
        assert doc["projectId"] == "9001"

    def test_converted_lead_passes_contract_validation(self):
        # Covers the non-null FK path the base row (unconverted, all None)
        # never reaches — a numeric id would fail the string-typed contract.
        from gold.src.canonical.load_lead import shape_doc, _contract_validator
        doc = shape_doc(_base_row(customer_id=4521, project_id=9001,
                                  converted_at=datetime(2026, 6, 30, tzinfo=timezone.utc)), _TS)
        assert _contract_validator.check(doc) is None


class TestGetMaxSilverTs:
    def test_delegates_to_shared_per_env_helper(self):
        from gold.src.canonical import load_lead
        with patch.object(load_lead, "get_max_silver_ts_per_env",
                          return_value=_TS) as mock_helper:
            result = load_lead._get_max_silver_ts("dsn", _TENANT)
        mock_helper.assert_called_once_with("dsn", "fieldcraft_customer_lead", "tenant_id", _TENANT)
        assert result == _TS


class TestSilverQueryShape:
    def test_reads_namespaced_customer_db_table(self):
        # Customer DB tables land in Silver as fieldcraft_customer_<table>
        # (universal_silver_merge._namespaced_entity), never bare silver.lead.
        from gold.src.canonical.load_lead import _SILVER_QUERY
        assert "FROM silver.fieldcraft_customer_lead" in _SILVER_QUERY
        assert "silver.lead " not in _SILVER_QUERY

    def test_scoped_on_business_tenant_id_not_s_tenantid(self):
        # _s_tenantid is the constant "fieldcraft_customer" for every row of this
        # table, so filtering on it would return nothing for a real tenant.
        from gold.src.canonical.load_lead import _SILVER_QUERY
        assert "l.tenant_id = %(tenantid)s" in _SILVER_QUERY
        assert "_s_tenantid = %(tenantid)s" not in _SILVER_QUERY

    def test_service_master_join_is_env_scoped(self):
        # An id is only unique within one _s_env; an id-only join would fan
        # one lead out into one row per env sharing that service id.
        from gold.src.canonical.load_lead import _SILVER_QUERY
        assert "LEFT JOIN silver.external_service_service_master" in _SILVER_QUERY
        assert "sm._s_env = l._s_env" in _SILVER_QUERY
        assert "sm.name" in _SILVER_QUERY

    def test_optional_lead_uid_filter_placeholder(self):
        from gold.src.canonical.load_lead import _SILVER_QUERY
        assert "{lead_filter}" in _SILVER_QUERY


class TestLeadUidCliWiring:
    def test_cli_flag_populates_config(self):
        # The --lead-uid flag must reach LoaderConfig so a CDC-triggered
        # single-lead recompute is actually reachable from the script's own
        # interface (the SQL-level filter existed before this was wired up).
        from gold.src.canonical.load_lead import LoaderConfig
        with patch.dict("os.environ", {"MONGO_URI": "mongodb://x", "PG_DSN": "postgresql://x"}, clear=False), \
             patch("gold.src.canonical.load_lead.get_mongo_uri", return_value="mongodb://x"), \
             patch("gold.src.canonical.load_lead.get_postgres_dsn", return_value="postgresql://x"):
            cfg = LoaderConfig.from_args_and_env(["--tenant", _TENANT, "--lead-uid", "lead-42"])
        assert cfg.lead_uid == "lead-42"

    def test_cli_flag_defaults_to_none(self):
        from gold.src.canonical.load_lead import LoaderConfig
        with patch.dict("os.environ", {"MONGO_URI": "mongodb://x", "PG_DSN": "postgresql://x"}, clear=False), \
             patch("gold.src.canonical.load_lead.get_mongo_uri", return_value="mongodb://x"), \
             patch("gold.src.canonical.load_lead.get_postgres_dsn", return_value="postgresql://x"):
            cfg = LoaderConfig.from_args_and_env(["--tenant", _TENANT])
        assert cfg.lead_uid is None

    def test_run_passes_lead_uid_through_to_iter_silver_rows(self):
        # run() must forward cfg.lead_uid to iter_silver_rows — the filter is
        # dead from the user's perspective otherwise. rebuild=True bypasses
        # the _has_new_silver_rows freshness check (its own real-Postgres
        # call is out of scope for this unit test).
        from gold.src.canonical import load_lead
        from gold.src.canonical.load_lead import LoaderConfig
        cfg = LoaderConfig(
            mongo_uri="mongodb://x", pg_dsn="postgresql://x", db_name="gold_canonical",
            tenantid=_TENANT, batch_size=200, max_retries=5, retry_backoff_s=1.5,
            dry_run=True, rebuild=True, lead_uid="lead-42",
        )
        with patch.object(load_lead, "read_cursor", return_value=_TS), \
             patch.object(load_lead, "iter_silver_rows", return_value=iter([])) as mock_iter:
            load_lead.run(cfg)
        mock_iter.assert_called_once_with("postgresql://x", _TENANT, 0, "lead-42")


class TestLafieldcraftFloor:
    def test_lag_ms_never_negative(self):
        # gold_ts before the Silver watermark (clock skew) must clamp to 0,
        # matching the contract's lafieldcraft minimum:0 constraint.
        from gold.src.canonical.load_lead import shape_doc
        future_silver_ts = datetime(2026, 7, 1, 13, 0, 0, tzinfo=timezone.utc)
        doc = shape_doc(_base_row(**{"lead_silver_ts": future_silver_ts}), _TS)
        assert doc["lafieldcraft"] == 0


class TestRunExceptionHandling:
    def test_operation_failure_maps_to_exit_code_3(self):
        # bulk_write_with_retry re-raises OperationFailure; run() must catch
        # it (not just TRANSIENT_ERRORS) and return the documented exit code.
        from pymongo.errors import OperationFailure
        from gold.src.canonical import load_lead
        from gold.src.canonical.load_lead import LoaderConfig

        def _raise(*_a, **_kw):
            raise OperationFailure("not authorized")
            yield  # pragma: no cover — makes this a generator

        cfg = LoaderConfig(
            mongo_uri="mongodb://x", pg_dsn="postgresql://x", db_name="gold_canonical",
            tenantid=_TENANT, batch_size=200, max_retries=5, retry_backoff_s=1.5,
            dry_run=True, rebuild=True,
        )
        with patch.object(load_lead, "read_cursor", return_value=_TS), \
             patch.object(load_lead, "iter_silver_rows", side_effect=_raise), \
             patch.object(load_lead, "mark_error") as mock_mark_error:
            rc = load_lead.run(cfg)
        assert rc == 3
        mock_mark_error.assert_called_once()

    def test_postgres_error_maps_to_exit_code_3(self):
        # A Postgres connection failure raised from iter_silver_rows must not
        # propagate as an unhandled traceback.
        import psycopg2
        from gold.src.canonical import load_lead
        from gold.src.canonical.load_lead import LoaderConfig

        def _raise(*_a, **_kw):
            raise psycopg2.OperationalError("connection refused")
            yield  # pragma: no cover — makes this a generator

        cfg = LoaderConfig(
            mongo_uri="mongodb://x", pg_dsn="postgresql://x", db_name="gold_canonical",
            tenantid=_TENANT, batch_size=200, max_retries=5, retry_backoff_s=1.5,
            dry_run=True, rebuild=True,
        )
        with patch.object(load_lead, "read_cursor", return_value=_TS), \
             patch.object(load_lead, "iter_silver_rows", side_effect=_raise), \
             patch.object(load_lead, "mark_error") as mock_mark_error:
            rc = load_lead.run(cfg)
        assert rc == 3
        mock_mark_error.assert_called_once()

    def test_mark_error_failure_itself_does_not_crash_run(self):
        # If Postgres is down, mark_error's own new connection will also
        # raise — run() must not let that escape as a second traceback.
        import psycopg2
        from gold.src.canonical import load_lead
        from gold.src.canonical.load_lead import LoaderConfig

        def _raise(*_a, **_kw):
            raise psycopg2.OperationalError("connection refused")
            yield  # pragma: no cover — makes this a generator

        cfg = LoaderConfig(
            mongo_uri="mongodb://x", pg_dsn="postgresql://x", db_name="gold_canonical",
            tenantid=_TENANT, batch_size=200, max_retries=5, retry_backoff_s=1.5,
            dry_run=True, rebuild=True,
        )
        with patch.object(load_lead, "read_cursor", return_value=_TS), \
             patch.object(load_lead, "iter_silver_rows", side_effect=_raise), \
             patch.object(load_lead, "mark_error", side_effect=psycopg2.OperationalError("still down")):
            rc = load_lead.run(cfg)
        assert rc == 3
