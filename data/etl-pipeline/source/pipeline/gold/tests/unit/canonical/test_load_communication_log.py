"""Unit tests for the Canonical Gold communication_log loader
(load_communication_log.py).

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
        "communication_log_id": "5c0a5e2e-6e2b-4b1a-9a4d-000000000001",
        "channel": "EMAIL",
        "direction": "OUTBOUND",
        "status": "SENT",
        "subject": "Thanks for your interest",
        "occurred_at": datetime(2026, 6, 29, tzinfo=timezone.utc),
        "actor_type": "AUTOMATION",
        "actor_ref": "AUTOMATION-LEAD-WELCOME",
        "customer_uuid": None,
        "project_id": "ORD-1001",
        "provider_message_ref": "PROVIDER-EMAIL-1001",
        "is_deleted": False,
        "silver_lsn": 7,
        "communication_log_silver_ts": datetime(2026, 6, 30, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


class TestCommunicationLogShapeDoc:
    def test_basic_shape(self):
        from gold.src.canonical.load_communication_log import shape_doc
        doc = shape_doc(_base_row(), _TS)
        assert doc["tenantId"] == _TENANT
        assert doc["communicationLogId"] == "5c0a5e2e-6e2b-4b1a-9a4d-000000000001"
        assert doc["_grain"] == "canonical_communication_log"

    def test_communication_log_id_is_always_a_string(self):
        # communicationLogId is stringified unconditionally — the source column
        # may be a UUID or an auto-increment int depending on tenant/env
        # (real data for tn59395620 is integer, not UUID as the original Silver
        # schema comment claimed). Same null-safe str() pattern as load_lead's
        # customerId/projectId.
        from gold.src.canonical.load_communication_log import shape_doc
        doc = shape_doc(_base_row(), _TS)
        assert isinstance(doc["communicationLogId"], str)
        # Also verify integers are stringified, not left as int
        doc_int = shape_doc(_base_row(communication_log_id=780), _TS)
        assert doc_int["communicationLogId"] == "780"
        assert isinstance(doc_int["communicationLogId"], str)

    def test_project_id_is_the_resolved_order_number(self):
        # Deliberately the project's order_no (via LEFT JOIN on silver.project),
        # not the raw project_id FK — see load_communication_log.py's
        # module docstring and _SILVER_QUERY comment.
        from gold.src.canonical.load_communication_log import shape_doc
        doc = shape_doc(_base_row(project_id="ORD-1001"), _TS)
        assert doc["projectId"] == "ORD-1001"

    def test_project_id_null_when_no_project_exists_yet(self):
        # A communication can be logged before a project exists (e.g. a
        # pre-conversion lead's welcome email) — the LEFT JOIN must not drop
        # the row.
        from gold.src.canonical.load_communication_log import shape_doc
        doc = shape_doc(_base_row(project_id=None), _TS)
        assert doc["projectId"] is None

    def test_pii_fields_are_never_present(self):
        # body_snapshot/body_tokens/to_address/from_address are excluded at
        # the SQL level (never selected), so they must never appear on the
        # shaped doc regardless of row contents.
        from gold.src.canonical.load_communication_log import shape_doc
        doc = shape_doc(_base_row(), _TS)
        for pii_field in ("bodySnapshot", "bodyTokens", "toAddress", "fromAddress"):
            assert pii_field not in doc

    def test_occurred_at_is_parsed(self):
        from gold.src.canonical.load_communication_log import shape_doc
        doc = shape_doc(_base_row(), _TS)
        assert doc["occurredAt"] == datetime(2026, 6, 29, tzinfo=timezone.utc)

    def test_lineage_and_source_env(self):
        from gold.src.canonical.load_communication_log import shape_doc
        doc = shape_doc(_base_row(), _TS)
        assert doc["goldUpdatedAt"] == _TS
        assert doc["sourceEnv"] == "dev"

    def test_contract_validation_passes(self):
        from gold.src.canonical.load_communication_log import shape_doc, _contract_validator
        doc = shape_doc(_base_row(), _TS)
        assert _contract_validator.check(doc) is None


class TestGetMaxSilverTs:
    def test_delegates_to_shared_per_env_helper(self):
        from gold.src.canonical import load_communication_log
        with patch.object(load_communication_log, "get_max_silver_ts_per_env",
                          return_value=_TS) as mock_helper:
            result = load_communication_log._get_max_silver_ts("dsn", _TENANT)
        mock_helper.assert_called_once_with(
            "dsn", "communication_log", "_s_tenantid", _TENANT
        )
        assert result == _TS


class TestSilverQueryShape:
    def test_query_left_joins_project_and_is_tenant_scoped(self):
        from gold.src.canonical.load_communication_log import _SILVER_QUERY
        assert "_s_tenantid = %(tenantid)s" in _SILVER_QUERY
        assert "LEFT JOIN silver.project" in _SILVER_QUERY
        assert "p.order_no" in _SILVER_QUERY

    def test_project_join_is_env_scoped(self):
        # A project id is only unique within one _s_env; an id-only join would
        # fan one log row out into one row per env sharing that project id.
        from gold.src.canonical.load_communication_log import _SILVER_QUERY
        assert "p._s_env = c._s_env" in _SILVER_QUERY

    def test_pii_columns_never_selected(self):
        from gold.src.canonical.load_communication_log import _SILVER_QUERY
        lowered = _SILVER_QUERY.lower()
        for pii_col in ("body_snapshot", "body_tokens", "to_address", "from_address"):
            assert pii_col not in lowered

    def test_optional_row_filter_placeholder(self):
        from gold.src.canonical.load_communication_log import _SILVER_QUERY
        assert "{row_filter}" in _SILVER_QUERY


class TestCommunicationLogIdCliWiring:
    def test_cli_flag_populates_config(self):
        # The --communication-log-id flag must reach LoaderConfig so a
        # CDC-triggered single-row recompute is actually reachable from the
        # script's own interface (the SQL-level filter existed before this
        # was wired up).
        from gold.src.canonical.load_communication_log import LoaderConfig
        with patch.dict("os.environ", {"MONGO_URI": "mongodb://x", "PG_DSN": "postgresql://x"}, clear=False), \
             patch("gold.src.canonical.load_communication_log.get_mongo_uri", return_value="mongodb://x"), \
             patch("gold.src.canonical.load_communication_log.get_postgres_dsn", return_value="postgresql://x"):
            cfg = LoaderConfig.from_args_and_env(["--tenant", _TENANT, "--communication-log-id", "cl-42"])
        assert cfg.communication_log_id == "cl-42"

    def test_cli_flag_defaults_to_none(self):
        from gold.src.canonical.load_communication_log import LoaderConfig
        with patch.dict("os.environ", {"MONGO_URI": "mongodb://x", "PG_DSN": "postgresql://x"}, clear=False), \
             patch("gold.src.canonical.load_communication_log.get_mongo_uri", return_value="mongodb://x"), \
             patch("gold.src.canonical.load_communication_log.get_postgres_dsn", return_value="postgresql://x"):
            cfg = LoaderConfig.from_args_and_env(["--tenant", _TENANT])
        assert cfg.communication_log_id is None

    def test_run_passes_communication_log_id_through_to_iter_silver_rows(self):
        # rebuild=True bypasses the _has_new_silver_rows freshness check
        # (its own real-Postgres call is out of scope for this unit test).
        from gold.src.canonical import load_communication_log
        from gold.src.canonical.load_communication_log import LoaderConfig
        cfg = LoaderConfig(
            mongo_uri="mongodb://x", pg_dsn="postgresql://x", db_name="gold_canonical",
            tenantid=_TENANT, batch_size=200, max_retries=5, retry_backoff_s=1.5,
            dry_run=True, rebuild=True, communication_log_id="cl-42",
        )
        with patch.object(load_communication_log, "read_cursor", return_value=_TS), \
             patch.object(load_communication_log, "iter_silver_rows", return_value=iter([])) as mock_iter:
            load_communication_log.run(cfg)
        mock_iter.assert_called_once_with("postgresql://x", _TENANT, 0, "cl-42")


class TestLafieldcraftFloor:
    def test_lag_ms_never_negative(self):
        # gold_ts before the Silver watermark (clock skew) must clamp to 0,
        # matching the contract's lafieldcraft minimum:0 constraint.
        from gold.src.canonical.load_communication_log import shape_doc
        future_silver_ts = datetime(2026, 7, 1, 13, 0, 0, tzinfo=timezone.utc)
        doc = shape_doc(_base_row(**{"communication_log_silver_ts": future_silver_ts}), _TS)
        assert doc["lafieldcraft"] == 0


class TestRunExceptionHandling:
    def test_operation_failure_maps_to_exit_code_3(self):
        # bulk_write_with_retry re-raises OperationFailure; run() must catch
        # it (not just TRANSIENT_ERRORS) and return the documented exit code.
        from pymongo.errors import OperationFailure
        from gold.src.canonical import load_communication_log
        from gold.src.canonical.load_communication_log import LoaderConfig

        def _raise(*_a, **_kw):
            raise OperationFailure("not authorized")
            yield  # pragma: no cover — makes this a generator

        cfg = LoaderConfig(
            mongo_uri="mongodb://x", pg_dsn="postgresql://x", db_name="gold_canonical",
            tenantid=_TENANT, batch_size=200, max_retries=5, retry_backoff_s=1.5,
            dry_run=True, rebuild=True,
        )
        with patch.object(load_communication_log, "read_cursor", return_value=_TS), \
             patch.object(load_communication_log, "iter_silver_rows", side_effect=_raise), \
             patch.object(load_communication_log, "mark_error") as mock_mark_error:
            rc = load_communication_log.run(cfg)
        assert rc == 3
        mock_mark_error.assert_called_once()

    def test_postgres_error_maps_to_exit_code_3(self):
        # A Postgres connection failure raised from iter_silver_rows must not
        # propagate as an unhandled traceback.
        import psycopg2
        from gold.src.canonical import load_communication_log
        from gold.src.canonical.load_communication_log import LoaderConfig

        def _raise(*_a, **_kw):
            raise psycopg2.OperationalError("connection refused")
            yield  # pragma: no cover — makes this a generator

        cfg = LoaderConfig(
            mongo_uri="mongodb://x", pg_dsn="postgresql://x", db_name="gold_canonical",
            tenantid=_TENANT, batch_size=200, max_retries=5, retry_backoff_s=1.5,
            dry_run=True, rebuild=True,
        )
        with patch.object(load_communication_log, "read_cursor", return_value=_TS), \
             patch.object(load_communication_log, "iter_silver_rows", side_effect=_raise), \
             patch.object(load_communication_log, "mark_error") as mock_mark_error:
            rc = load_communication_log.run(cfg)
        assert rc == 3
        mock_mark_error.assert_called_once()

    def test_mark_error_failure_itself_does_not_crash_run(self):
        # If Postgres is down, mark_error's own new connection will also
        # raise — run() must not let that escape as a second traceback.
        import psycopg2
        from gold.src.canonical import load_communication_log
        from gold.src.canonical.load_communication_log import LoaderConfig

        def _raise(*_a, **_kw):
            raise psycopg2.OperationalError("connection refused")
            yield  # pragma: no cover — makes this a generator

        cfg = LoaderConfig(
            mongo_uri="mongodb://x", pg_dsn="postgresql://x", db_name="gold_canonical",
            tenantid=_TENANT, batch_size=200, max_retries=5, retry_backoff_s=1.5,
            dry_run=True, rebuild=True,
        )
        with patch.object(load_communication_log, "read_cursor", return_value=_TS), \
             patch.object(load_communication_log, "iter_silver_rows", side_effect=_raise), \
             patch.object(load_communication_log, "mark_error", side_effect=psycopg2.OperationalError("still down")):
            rc = load_communication_log.run(cfg)
        assert rc == 3
