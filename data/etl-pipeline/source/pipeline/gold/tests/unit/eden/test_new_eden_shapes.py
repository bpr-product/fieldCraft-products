"""Unit tests for the two new Eden Gold loaders: bom_stock_gap and weather_schedule_cross.

Mirrors the shape_doc / iter_silver_rows / --rebuild conventions in
test_eden_loaders.py.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TS = datetime(2024, 6, 1, tzinfo=timezone.utc)


def _make_conn(rows):
    conn = MagicMock()
    cur = MagicMock()
    cur.__iter__ = MagicMock(return_value=iter(rows))
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn, cur


# ---------------------------------------------------------------------------
# load_bom_stock_gap
# ---------------------------------------------------------------------------

@pytest.mark.eden
class TestBomStockGapShapeDoc:
    def _mod(self):
        import gold.src.eden.load_bom_stock_gap as m
        m._cb = None
        return m

    def _row(self):
        return {
            "tenantid": "T-001",
            "project_id": 12,
            "project_name": "DEMO-proj",
            "material_uid": "m-uuid",
            "material_id": 7,
            "material_name": "2x4 Lumber",
            "unit": "ea",
            "required_qty": 100,
            "stock_qty": 30,
            "preferred_vendor_id": 3,
            "preferred_vendor_name": "DEMO Vendor",
            "unit_price": 4.25,
            "is_deleted": False,
            "deleted_at": None,
            "silver_lsn": 600,
            "bom_silver_ts": _TS,
            "stock_silver_ts": _TS,
            "project_silver_ts": _TS,
        }

    def test_basic_shape(self):
        doc = self._mod().shape_doc(self._row(), _TS)
        assert doc["tenantId"] == "T-001"
        assert doc["projectId"] == "12"
        assert doc["materialUid"] == "m-uuid"
        assert doc["_grain"] == "eden_bom_stock_gap"
        assert doc["_schemaVersion"] == 1

    def test_gap_computation(self):
        doc = self._mod().shape_doc(self._row(), _TS)
        assert doc["requiredQty"] == 100.0
        assert doc["stockQty"] == 30.0
        assert doc["gapQty"] == 70.0   # required - stock

    def test_gap_negative_is_shortfall(self):
        row = self._row()
        row["required_qty"] = 10
        row["stock_qty"] = 40
        doc = self._mod().shape_doc(row, _TS)
        assert doc["gapQty"] == -30.0

    def test_vendor_and_price(self):
        doc = self._mod().shape_doc(self._row(), _TS)
        assert doc["preferredVendorId"] == 3
        assert doc["preferredVendorName"] == "DEMO Vendor"
        assert doc["unitPrice"] == 4.25

    def test_null_stock_defaults_zero_gap_uses_required(self):
        row = self._row()
        row["stock_qty"] = None
        doc = self._mod().shape_doc(row, _TS)
        assert doc["gapQty"] == 100.0

    def test_material_id_coerced_int(self):
        row = self._row()
        row["material_id"] = "7"
        doc = self._mod().shape_doc(row, _TS)
        assert doc["materialId"] == 7


@pytest.mark.eden
class TestBomStockGapIterSilverRows:
    def test_yields_rows(self):
        import gold.src.eden.load_bom_stock_gap as m
        rows = [{"tenantid": "T-001", "project_id": 1, "material_uid": "m-1"}]
        conn, cur = _make_conn(rows)
        cur.__iter__ = MagicMock(return_value=iter(rows))
        with patch("psycopg2.connect", return_value=conn):
            result = list(m.iter_silver_rows("dsn", "T-001"))
        assert len(result) == 1


# ---------------------------------------------------------------------------
# load_weather_schedule_cross
# ---------------------------------------------------------------------------

@pytest.mark.eden
class TestWeatherScheduleCrossShapeDoc:
    def _mod(self):
        import gold.src.eden.load_weather_schedule_cross as m
        m._cb = None
        return m

    def _row(self, weather):
        return {
            "tenantid": "T-001",
            "project_id": 12,
            "project_name": "DEMO-proj",
            "task_id": 99,
            "task_uid": "t-uuid",
            "task_name": "Pour slab",
            "scheduled_date": "2026-06-20",
            "weather_data": json.dumps(weather),
            "is_deleted": False,
            "deleted_at": None,
            "silver_lsn": 500,
            "task_silver_ts": _TS,
            "weather_silver_ts": _TS,
            "project_silver_ts": _TS,
        }

    def test_basic_shape(self):
        doc = self._mod().shape_doc(self._row({"condition": "Clear"}), _TS)
        assert doc["tenantId"] == "T-001"
        assert doc["projectId"] == "12"
        assert doc["taskId"] == "99"
        assert doc["scheduledDate"] == datetime(2026, 6, 20, tzinfo=timezone.utc)
        assert doc["_grain"] == "eden_weather_schedule_cross"
        assert doc["_schemaVersion"] == 1

    def test_blocked_on_heavy_rain(self):
        doc = self._mod().shape_doc(
            self._row({"condition": "Rain", "precipitation": 15.2, "windSpeed": 5}), _TS)
        assert doc["blocked"] is True
        assert "Heavy rain forecast" in doc["blockReason"]
        assert doc["precipitationMm"] == 15.2

    def test_blocked_on_high_wind(self):
        doc = self._mod().shape_doc(
            self._row({"condition": "Windy", "precipitation": 0, "windSpeed": 55}), _TS)
        assert doc["blocked"] is True
        assert "High winds" in doc["blockReason"]
        assert doc["windSpeedKmh"] == 55.0

    def test_not_blocked_below_threshold(self):
        doc = self._mod().shape_doc(
            self._row({"condition": "Clear", "precipitation": 1, "windSpeed": 10}), _TS)
        assert doc["blocked"] is False
        assert doc["blockReason"] is None

    def test_condition_parsed(self):
        doc = self._mod().shape_doc(self._row({"summary": "Partly cloudy"}), _TS)
        assert doc["weatherCondition"] == "Partly cloudy"

    def test_malformed_weather_json_is_safe(self):
        row = self._row({})
        row["weather_data"] = "not-json{"
        doc = self._mod().shape_doc(row, _TS)
        assert doc["blocked"] is False
        assert doc["precipitationMm"] is None
        assert doc["windSpeedKmh"] is None


@pytest.mark.eden
class TestWeatherScheduleCrossIterSilverRows:
    def test_yields_rows(self):
        import gold.src.eden.load_weather_schedule_cross as m
        rows = [{"tenantid": "T-001", "project_id": 1, "task_id": 2}]
        conn, cur = _make_conn(rows)
        cur.__iter__ = MagicMock(return_value=iter(rows))
        with patch("psycopg2.connect", return_value=conn):
            result = list(m.iter_silver_rows("dsn", "T-001"))
        assert len(result) == 1


# ---------------------------------------------------------------------------
# --rebuild flag parsing for both new loaders
# ---------------------------------------------------------------------------

_NEW_LOADER_MODULES = [
    "gold.src.eden.load_bom_stock_gap",
    "gold.src.eden.load_weather_schedule_cross",
]


@pytest.mark.eden
@pytest.mark.parametrize("mod_path", _NEW_LOADER_MODULES)
def test_rebuild_flag_parsed_true(mod_path):
    import importlib
    mod = importlib.import_module(mod_path)
    cfg = mod.LoaderConfig.from_args_and_env(
        ["--tenant", "T-001", "--mongo-uri", "m://x", "--pg-dsn", "p://x", "--rebuild"]
    )
    assert cfg.rebuild is True


@pytest.mark.eden
@pytest.mark.parametrize("mod_path", _NEW_LOADER_MODULES)
def test_rebuild_flag_default_false(mod_path):
    import importlib
    mod = importlib.import_module(mod_path)
    cfg = mod.LoaderConfig.from_args_and_env(
        ["--tenant", "T-001", "--mongo-uri", "m://x", "--pg-dsn", "p://x"]
    )
    assert cfg.rebuild is False
