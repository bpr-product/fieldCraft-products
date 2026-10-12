"""Unit tests for the diff + ALTER generator added to pg_schema_inferrer."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.schema_utils.pg_schema_inferrer import (
    diff_schema,
    generate_alter_statements,
)


def _col(name, dt="text", nullable=True):
    return {"name": name, "data_type": dt, "is_nullable": nullable}


# ── diff_schema ──────────────────────────────────────────────────────────────

def test_diff_missing_in_silver():
    source = [_col("id", "integer"), _col("name", "text"), _col("note", "text")]
    silver = [_col("id", "integer"), _col("name", "text")]
    d = diff_schema(source, silver)
    assert [c["name"] for c in d["missing_in_silver"]] == ["note"]
    assert d["missing_in_source"] == []
    assert d["type_mismatches"] == []


def test_diff_envelope_fields_ignored():
    source = [_col("id", "integer"), _col("op", "text"), _col("ts_ms", "bigint")]
    silver = [_col("id", "integer")]
    d = diff_schema(source, silver)
    # op and ts_ms are envelope fields — never auto-added to Silver
    assert d["missing_in_silver"] == []


def test_diff_silver_metadata_fields_ignored():
    source = [_col("id", "integer")]
    silver = [_col("id", "integer"), _col("_s_tenantid", "text"),
              _col("_s_lsn", "bigint"), _col("_s_is_deleted", "boolean")]
    d = diff_schema(source, silver)
    # _s_* are Silver-only metadata; not flagged as missing_in_source
    assert d["missing_in_source"] == []


def test_diff_missing_in_source_reported_not_corrected():
    source = [_col("id", "integer")]
    silver = [_col("id", "integer"), _col("legacy_col", "text")]
    d = diff_schema(source, silver)
    assert d["missing_in_source"] == ["legacy_col"]
    assert d["missing_in_silver"] == []  # nothing to auto-add


def test_diff_type_mismatch_reported_not_corrected():
    source = [_col("id", "integer"), _col("amount", "numeric")]
    silver = [_col("id", "integer"), _col("amount", "text")]
    d = diff_schema(source, silver)
    assert d["missing_in_silver"] == []
    assert len(d["type_mismatches"]) == 1
    assert d["type_mismatches"][0]["name"] == "amount"


def test_diff_timestamp_normalisation_does_not_falsely_mismatch():
    # Silver stores TIMESTAMPTZ as "timestamp with time zone" in info_schema.
    source = [_col("ts", "timestamp without time zone")]
    silver = [_col("ts", "timestamp with time zone")]
    d = diff_schema(source, silver)
    assert d["type_mismatches"] == []


# ── generate_alter_statements ────────────────────────────────────────────────

def test_generate_alter_statements_basic():
    missing = [_col("note", "text"), _col("amount", "numeric")]
    stmts = generate_alter_statements("silver.invoice", missing)
    assert len(stmts) == 2
    assert stmts[0] == "ALTER TABLE silver.invoice ADD COLUMN IF NOT EXISTS note TEXT"
    assert stmts[1] == "ALTER TABLE silver.invoice ADD COLUMN IF NOT EXISTS amount NUMERIC"


def test_generate_alter_statements_maps_unknown_types_to_text():
    missing = [_col("weird", "geography")]
    stmts = generate_alter_statements("silver.invoice", missing)
    assert "ADD COLUMN IF NOT EXISTS weird TEXT" in stmts[0]


def test_generate_alter_statements_empty_when_no_missing():
    assert generate_alter_statements("silver.invoice", []) == []


def test_generate_alter_statements_uses_if_not_exists_for_race_safety():
    """Two watcher replicas racing must both succeed; IF NOT EXISTS guarantees it."""
    stmts = generate_alter_statements("silver.x", [_col("c", "text")])
    assert "IF NOT EXISTS" in stmts[0]
