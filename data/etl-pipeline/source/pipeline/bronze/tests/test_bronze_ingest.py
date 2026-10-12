"""
Integration tests for the universal Bronze sink `bronze.raw_events`
(Iceberg on ADLS Gen2), read-only — no writes.

Architecture note: the pre-refactor per-entity `bronze.project` tables no
longer exist. The universal job (`universal_bronze_ingest.py`) writes every
entity's full Debezium envelope into the single `bronze.raw_events` table.
These tests assert the envelope contract on that table and skip gracefully
when the catalog/table is unavailable (e.g. CI without a live stack).
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Auto-load .env.local so ETL_ICEBERG_REST_URI / Azure settings are present
# when the suite is run standalone (matches scripts/inspect_bronze.py pattern).
_ENV = Path(__file__).resolve().parents[2] / ".env.local"
if _ENV.exists():
    for _ln in _ENV.read_text(encoding="utf-8").splitlines():
        _ln = _ln.strip()
        if not _ln or _ln.startswith("#") or "=" not in _ln:
            continue
        _k, _v = _ln.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from shared.iceberg_utils.catalog_factory import get_catalog

VALID_OPS = {"c", "u", "d", "r"}

# The 10-column envelope schema of bronze.raw_events (9 envelope cols + payload).
ENVELOPE_COLS = {
    "source_type", "op", "lsn", "ts_ms", "tenantid",
    "entity", "record_id", "source_name", "_ingested_at", "payload",
}


@pytest.fixture(scope="module")
def raw_events():
    """Sample rows from bronze.raw_events; skip when the layer isn't available."""
    try:
        catalog = get_catalog("bronze")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Bronze Iceberg catalog unavailable: {exc}")
    try:
        table = catalog.load_table("bronze.raw_events")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"bronze.raw_events not found: {exc}")

    rows = list(table.scan(limit=1000).to_arrow().to_pylist())
    if not rows:
        pytest.skip("bronze.raw_events is empty — pipeline not yet started")
    return rows


def test_envelope_columns_present(raw_events):
    """Every raw_events row carries the full CDC envelope schema."""
    missing = ENVELOPE_COLS - set(raw_events[0].keys())
    assert not missing, f"bronze.raw_events missing envelope columns: {missing}"


def test_valid_op_values(raw_events):
    """Every event has a valid CDC op (c/u/d/r)."""
    bad = sorted({r.get("op") for r in raw_events if r.get("op") not in VALID_OPS})
    assert not bad, f"Invalid op values in bronze.raw_events: {bad}"


def test_entity_populated(raw_events):
    """Every event is tagged with a non-empty entity — required for Silver routing."""
    blank = [r for r in raw_events if not r.get("entity")]
    assert not blank, f"{len(blank)} rows have an empty entity"


def test_payload_is_parseable_json(raw_events):
    """The payload column holds the full Debezium JSON, parseable for replay."""
    sample = [r for r in raw_events if r.get("payload")][:100]
    if not sample:
        pytest.skip("no payloads present to validate")
    for r in sample:
        parsed = json.loads(r["payload"])  # raises if not valid JSON
        assert isinstance(parsed, dict), "payload must decode to a JSON object"


def test_internal_events_have_tenantid(raw_events):
    """Internal CDC events must carry a tenantid (external events are exempt)."""
    internal = [r for r in raw_events if r.get("source_type") != "external"]
    if not internal:
        pytest.skip("no internal events present")
    missing = [r for r in internal if not r.get("tenantid")]
    assert not missing, f"{len(missing)} internal events are missing tenantid"
