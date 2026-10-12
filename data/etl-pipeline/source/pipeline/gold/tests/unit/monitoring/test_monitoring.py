"""Unit tests for Gold monitoring: CircuitBreaker, ContractValidator, metrics."""

from __future__ import annotations

import sys
import time
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------

@pytest.mark.monitoring
class TestCircuitBreakerClosed:
    def _cb(self, threshold=3, recovery=60):
        from gold.src.monitoring.circuit_breaker import CircuitBreaker
        return CircuitBreaker(name="test", failure_threshold=threshold,
                              recovery_timeout_s=recovery)

    def test_initial_state_closed(self):
        from gold.src.monitoring.circuit_breaker import CircuitState
        cb = self._cb()
        assert cb.state == CircuitState.CLOSED

    def test_success_returns_value(self):
        cb = self._cb()
        result = cb.call(lambda: 42)
        assert result == 42

    def test_failure_increments_count(self):
        cb = self._cb(threshold=3)
        try:
            cb.call(lambda: (_ for _ in ()).throw(ValueError("oops")))
        except ValueError:
            pass
        assert cb._failure_count == 1

    def test_threshold_opens_circuit(self):
        from gold.src.monitoring.circuit_breaker import CircuitState
        cb = self._cb(threshold=2)
        for _ in range(2):
            try:
                cb.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
            except RuntimeError:
                pass
        assert cb.state == CircuitState.OPEN

    def test_open_raises_circuit_open_error(self):
        from gold.src.monitoring.circuit_breaker import CircuitOpenError
        cb = self._cb(threshold=1)
        try:
            cb.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        except RuntimeError:
            pass
        with pytest.raises(CircuitOpenError):
            cb.call(lambda: 1)

    def test_reset_closes_circuit(self):
        from gold.src.monitoring.circuit_breaker import CircuitState
        cb = self._cb(threshold=1)
        try:
            cb.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        except RuntimeError:
            pass
        cb.reset()
        assert cb.state == CircuitState.CLOSED

    def test_success_resets_failure_count(self):
        cb = self._cb(threshold=5)
        for _ in range(3):
            try:
                cb.call(lambda: (_ for _ in ()).throw(ValueError("x")))
            except ValueError:
                pass
        cb.call(lambda: "ok")
        assert cb._failure_count == 0

    def test_half_open_after_recovery_timeout(self):
        from gold.src.monitoring.circuit_breaker import CircuitState
        cb = self._cb(threshold=1, recovery=0)
        try:
            cb.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        except RuntimeError:
            pass
        state = cb.state
        assert state in (CircuitState.HALF_OPEN, CircuitState.OPEN)

    def test_half_open_success_closes_circuit(self):
        from gold.src.monitoring.circuit_breaker import CircuitState
        cb = self._cb(threshold=1, recovery=0)
        try:
            cb.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        except RuntimeError:
            pass
        cb._state = CircuitState.HALF_OPEN
        cb.call(lambda: "probe")
        assert cb.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# ContractValidator
# ---------------------------------------------------------------------------

@pytest.mark.monitoring
class TestContractValidator:
    def _valid_doc(self):
        # goldUpdatedAt is the required platform timestamp field (was
        # _etlUpdatedAt until 2026-07-12 — see contract_validator.py's
        # _REQUIRED_FIELDS and gold/pipeline/lineage.py's "Consolidation" note).
        return {
            "tenantId": "T-001",
            "_grain": "bpe_kpi_monthly",
            "_schemaVersion": 1,
            "goldUpdatedAt": "2024-01-01T00:00:00+00:00",
            "trade": "roofing",
        }

    def test_valid_doc_no_violation(self):
        from gold.src.monitoring.contract_validator import ContractValidator
        cv = ContractValidator("nonexistent_contract", mode="warn")
        assert cv.check(self._valid_doc()) is None

    def test_missing_required_field(self):
        from gold.src.monitoring.contract_validator import ContractValidator
        cv = ContractValidator("nonexistent_contract", mode="warn")
        doc = self._valid_doc()
        del doc["tenantId"]
        result = cv.check(doc)
        assert result is not None
        assert "tenantId" in result

    def test_mode_off_skips_validation(self):
        from gold.src.monitoring.contract_validator import ContractValidator
        cv = ContractValidator("nonexistent_contract", mode="off")
        doc = {}
        assert cv.check(doc) is None

    def test_mode_error_raises_on_violation(self):
        from gold.src.monitoring.contract_validator import ContractValidator
        cv = ContractValidator("nonexistent_contract", mode="error")
        doc = self._valid_doc()
        del doc["_grain"]
        with pytest.raises(ValueError, match="contract violation"):
            cv.validate(doc, "test_collection")

    def test_mode_warn_does_not_raise(self):
        from gold.src.monitoring.contract_validator import ContractValidator
        cv = ContractValidator("nonexistent_contract", mode="warn")
        doc = {}
        cv.validate(doc, "test_collection")

    def test_validate_passes_valid_doc(self):
        from gold.src.monitoring.contract_validator import ContractValidator
        cv = ContractValidator("nonexistent_contract", mode="error")
        cv.validate(self._valid_doc(), "test_collection")

    def test_missing_schema_version(self):
        from gold.src.monitoring.contract_validator import ContractValidator
        cv = ContractValidator("nonexistent_contract", mode="warn")
        doc = self._valid_doc()
        del doc["_schemaVersion"]
        assert cv.check(doc) is not None

    def test_all_required_fields_present(self):
        from gold.src.monitoring.contract_validator import ContractValidator
        cv = ContractValidator("nonexistent_contract", mode="warn")
        doc = {
            "tenantId": "T-001",
            "_grain": "test_grain",
            "_schemaVersion": 1,
            "goldUpdatedAt": "2024-01-01T00:00:00",
        }
        assert cv.check(doc) is None


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

@pytest.mark.monitoring
class TestMetrics:
    def _temp_metrics_path(self, tmp_path):
        return tmp_path / "test_metrics.prom"

    def test_record_loader_run_writes_file(self, tmp_path):
        metrics_file = self._temp_metrics_path(tmp_path)
        with patch("gold.src.monitoring.metrics._METRICS_FILE", metrics_file):
            from gold.src.monitoring import metrics
            metrics._METRICS_FILE = metrics_file
            metrics.record_loader_run("estimate_kpi", "T-001",
                                       upserted=5, failed=0, elapsed_s=1.2)
        assert metrics_file.exists()
        content = metrics_file.read_text()
        assert "gold_loader_upserted_total" in content
        assert "estimate_kpi" in content
        assert "T-001" in content

    def test_record_loader_run_includes_failed(self, tmp_path):
        metrics_file = self._temp_metrics_path(tmp_path)
        with patch("gold.src.monitoring.metrics._METRICS_FILE", metrics_file):
            from gold.src.monitoring import metrics
            metrics._METRICS_FILE = metrics_file
            metrics.record_loader_run("estimate_kpi", "T-002",
                                       upserted=0, failed=3, elapsed_s=0.5)
        content = metrics_file.read_text()
        assert "gold_loader_failed_total" in content

    def test_record_pipeline_run_writes_file(self, tmp_path):
        metrics_file = self._temp_metrics_path(tmp_path)
        with patch("gold.src.monitoring.metrics._METRICS_FILE", metrics_file):
            from gold.src.monitoring import metrics
            metrics._METRICS_FILE = metrics_file
            metrics.record_pipeline_run(
                tenants_total=3, tenants_passed=2, tenants_failed=1,
                elapsed_s=45.6,
            )
        content = metrics_file.read_text()
        assert "gold_pipeline_tenants_total" in content
        assert "gold_pipeline_tenants_passed" in content
        assert "gold_pipeline_tenants_failed" in content

    def test_record_loader_run_appends_to_existing(self, tmp_path):
        metrics_file = self._temp_metrics_path(tmp_path)
        metrics_file.write_text("existing_metric 1\n")
        with patch("gold.src.monitoring.metrics._METRICS_FILE", metrics_file):
            from gold.src.monitoring import metrics
            metrics._METRICS_FILE = metrics_file
            metrics.record_loader_run("estimate_kpi", "T-001",
                                       upserted=1, failed=0, elapsed_s=0.1)
        content = metrics_file.read_text()
        assert "existing_metric" in content
        assert "gold_loader_upserted_total" in content

    def test_write_handles_os_error_gracefully(self, tmp_path):
        from gold.src.monitoring.metrics import _write
        with patch("gold.src.monitoring.metrics._METRICS_FILE",
                   Path("/nonexistent_dir_xyz/metrics.prom")):
            from gold.src.monitoring import metrics
            metrics._METRICS_FILE = Path("/nonexistent_dir_xyz/metrics.prom")
            metrics._write("test content")


# ---------------------------------------------------------------------------
# freshness (pure logic, no DB calls)
# ---------------------------------------------------------------------------

@pytest.mark.monitoring
class TestFreshnessLogic:
    def test_untracked_step_returns_none(self):
        from gold.src.monitoring.freshness import check_freshness
        with patch("gold.src.monitoring.freshness._get_silver_watermark", return_value=None), \
             patch("gold.src.monitoring.freshness._get_gold_watermark", return_value=None):
            result = check_freshness("unknown_step", "T-001", "dsn", "mongo://x")
        assert result is None

    def test_freshness_result_not_stale(self):
        from datetime import datetime, timezone
        from gold.src.monitoring.freshness import check_freshness, _STEP_TO_COLLECTION
        step = "load_estimate_snapshot"
        assert step in _STEP_TO_COLLECTION

        now = datetime.now(timezone.utc)
        with patch("gold.src.monitoring.freshness._get_silver_watermark", return_value=now), \
             patch("gold.src.monitoring.freshness._get_gold_watermark", return_value=now), \
             patch("gold.src.monitoring.freshness._emit_metric"):
            result = check_freshness(step, "T-001", "dsn", "mongo://x")
        assert result is not None
        assert result.is_stale is False

    def test_freshness_result_is_stale(self):
        from datetime import datetime, timezone, timedelta
        from gold.src.monitoring.freshness import check_freshness
        step = "load_estimate_snapshot"
        now = datetime.now(timezone.utc)
        old = now - timedelta(seconds=3600)
        with patch("gold.src.monitoring.freshness._get_silver_watermark", return_value=now), \
             patch("gold.src.monitoring.freshness._get_gold_watermark", return_value=old), \
             patch("gold.src.monitoring.freshness._emit_metric"), \
             patch("gold.src.monitoring.freshness._get_sla", return_value=900):
            result = check_freshness(step, "T-001", "dsn", "mongo://x")
        assert result is not None
        assert result.is_stale is True

    def test_lag_seconds_none_when_no_timestamps(self):
        from gold.src.monitoring.freshness import check_freshness
        step = "load_estimate_snapshot"
        with patch("gold.src.monitoring.freshness._get_silver_watermark", return_value=None), \
             patch("gold.src.monitoring.freshness._get_gold_watermark", return_value=None), \
             patch("gold.src.monitoring.freshness._emit_metric"):
            result = check_freshness(step, "T-001", "dsn", "mongo://x")
        assert result is not None
        assert result.lag_seconds is None
        assert result.is_stale is False
