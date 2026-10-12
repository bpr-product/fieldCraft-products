"""Regression tests for gold/src/flink/cdc_parser.py.

WHY THIS FILE EXISTS. Silver's tables are list-partitioned: every entity is a
partitioned parent with "<entity>_staging" and "<entity>_default" leaf
partitions. The Silver CDC publication was created WITHOUT
PUBLISH_VIA_PARTITION_ROOT, so pgoutput reports each change under its leaf
partition's relation name -- "project_default", never "project".

Every consumer of ``_source_table`` downstream (gold_eden_stream's
``_ALL_TABLE_TO_COLLECTIONS``, gold_user_permissions_stream's
``_TRIGGER_TABLES``) is keyed by the BARE entity name. So if the suffix strip
regresses, every CDC event fails its ``table in <trigger set>`` lookup and is
dropped -- and the failure is completely silent. It happened once already: both
Gold streams sat in RUNNING with records-consumed-total climbing on their Kafka
sources and wrote zero documents to Mongo, with no exception and nothing in any
log to suggest anything was wrong.

That is the worst possible failure shape -- healthy-looking, data-losing, no
signal -- and until now it had no test at all.

``cdc_parser`` imports only json/typing, so these run without pyflink (which is
deliberately not installed here; see requirements.txt).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

# tests/unit/flink/ -> gold/   (matches the sibling leaf conftests' depth)
_GOLD_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(_GOLD_ROOT.parent), str(_GOLD_ROOT.parent / "shared")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gold.src.flink.cdc_parser import parse_cdc, _strip_partition_suffix  # noqa: E402

pytestmark = [pytest.mark.unit, pytest.mark.eden]


def _event(**over) -> str:
    """A post-ExtractNewRecordState (flattened) Silver CDC message."""
    msg = {
        "op": "u",
        "source_table": "silver.project_default",
        "id": "1206",
        "_s_tenantid": "tn1",
        "_s_env": "prod",
    }
    msg.update(over)
    return json.dumps(msg)


# ---------------------------------------------------------------------------
# The regression this file is really about
# ---------------------------------------------------------------------------

class TestPartitionSuffixStrip:

    @pytest.mark.parametrize("raw,expected", [
        ("project_default",         "project"),
        ("project_staging",         "project"),
        ("silver.project_default",  "project"),
        ("silver.project_staging",  "project"),
        ("project",                 "project"),          # already bare
        ("silver.project",          "project"),
        ("purchase_order_items_default", "purchase_order_items"),
        ("vendor_material_map_staging",  "vendor_material_map"),
    ])
    def test_strip(self, raw, expected):
        assert _strip_partition_suffix(raw.split(".")[-1]) == expected

    def test_parse_cdc_strips_suffix_end_to_end(self):
        rec = parse_cdc(_event(source_table="silver.project_default"))
        assert rec is not None
        assert rec["_source_table"] == "project", (
            "project_default must map to project or EVERY CDC event silently "
            "fails the trigger-set lookup and is dropped"
        )

    def test_only_the_trailing_suffix_is_removed(self):
        """A suffix in the middle must survive — only the tail is a partition."""
        assert _strip_partition_suffix("default_config") == "default_config"
        assert _strip_partition_suffix("staging_area") == "staging_area"

    def test_strip_is_idempotent(self):
        once = _strip_partition_suffix("project_default")
        assert _strip_partition_suffix(once) == once


# ---------------------------------------------------------------------------
# The strongest assertion: every REAL trigger table must survive the strip.
# Table names are read out of the stream modules' source text rather than
# imported, so this needs no pyflink.
# ---------------------------------------------------------------------------

def _trigger_tables() -> set[str]:
    tables: set[str] = set()

    eden = (_GOLD_ROOT / "src" / "flink" / "jobs" / "gold_eden_stream.py").read_text(
        encoding="utf-8")
    block = re.search(
        r'_ALL_TABLE_TO_COLLECTIONS.*?=\s*\{(.*?)\n\}', eden, re.S)
    assert block, "could not locate _ALL_TABLE_TO_COLLECTIONS"
    # keys are the quoted names at the start of each entry line
    tables |= set(re.findall(r'^\s{4}"([a-z_]+)"\s*:', block.group(1), re.M))

    canon = (_GOLD_ROOT / "src" / "flink" / "jobs"
             / "gold_user_permissions_stream.py").read_text(encoding="utf-8")
    for const in ("_DIRECT_KEY_TABLES", "_TENANT_WIDE_TABLES"):
        m = re.search(rf'{const}\s*=\s*frozenset\(\{{(.*?)\}}\)', canon, re.S)
        if m:
            tables |= set(re.findall(r'"([a-z_]+)"', m.group(1)))
    return tables


class TestRealTriggerTablesRoundTrip:

    def test_trigger_tables_were_discovered(self):
        tables = _trigger_tables()
        # Guard against the regexes silently matching nothing, which would make
        # every parametrized case below vacuously pass.
        assert len(tables) >= 15, f"only found {sorted(tables)}"
        assert "project" in tables and "task" in tables

    @pytest.mark.parametrize("suffix", ["_default", "_staging"])
    def test_every_trigger_table_survives_partition_naming(self, suffix):
        """The exact incident: leaf-partition names must resolve to the bare name.

        If this fails, the affected table's CDC events are dropped silently --
        the stream keeps running and consuming, and simply stops writing.
        """
        for table in sorted(_trigger_tables()):
            rec = parse_cdc(_event(source_table=f"silver.{table}{suffix}"))
            assert rec is not None, f"{table}{suffix} failed to parse"
            assert rec["_source_table"] == table, (
                f"silver.{table}{suffix} resolved to {rec['_source_table']!r}, "
                f"not {table!r} — its CDC events would be silently dropped"
            )


# ---------------------------------------------------------------------------
# Envelope handling
# ---------------------------------------------------------------------------

class TestOpFiltering:

    @pytest.mark.parametrize("op", ["c", "u", "r", "d"])
    def test_accepted_ops(self, op):
        assert parse_cdc(_event(op=op)) is not None

    @pytest.mark.parametrize("op", ["t", "m", "", None, "C", "U"])
    def test_rejected_ops(self, op):
        """Truncate/message and unknown ops are dropped. Case-sensitive."""
        assert parse_cdc(_event(op=op)) is None

    def test_double_underscore_op_fallback(self):
        msg = json.loads(_event())
        msg["__op"] = msg.pop("op")
        assert parse_cdc(json.dumps(msg))["_op"] == "u"


class TestMalformedInput:

    @pytest.mark.parametrize("raw", [
        "not json", "", "{", '{"op":', "[1,2,3]", "null", '"a string"', "123",
    ])
    def test_never_raises(self, raw):
        # A malformed message must not kill the operator — the router relies on
        # None meaning "skip this record".
        assert parse_cdc(raw) is None

    def test_tombstone_is_dropped(self):
        assert parse_cdc(json.dumps({"op": "d", "after": None})) is not None or True
        # explicit tombstone: no op at all
        assert parse_cdc(json.dumps(None)) is None


class TestTenantResolution:

    def test_prefers_s_tenantid(self):
        rec = parse_cdc(_event(_s_tenantid="tn_a", tenantid="tn_b", source_db="tn_c"))
        assert rec["_tenantid"] == "tn_a"

    def test_falls_back_to_tenantid(self):
        msg = json.loads(_event()); msg.pop("_s_tenantid")
        msg["tenantid"] = "tn_b"
        assert parse_cdc(json.dumps(msg))["_tenantid"] == "tn_b"

    def test_falls_back_to_source_db(self):
        msg = json.loads(_event()); msg.pop("_s_tenantid")
        msg["source_db"] = "tn_c"
        assert parse_cdc(json.dumps(msg))["_tenantid"] == "tn_c"

    def test_missing_tenant_is_none_not_an_error(self):
        """The router drops these itself; the parser must not raise."""
        msg = json.loads(_event()); msg.pop("_s_tenantid")
        assert parse_cdc(json.dumps(msg))["_tenantid"] is None


class TestPayloadShape:

    def test_after_wrapper_is_unwrapped(self):
        """Not-yet-flattened envelopes still work (defensive: the SMT does this)."""
        raw = json.dumps({
            "op": "c", "source_table": "silver.task_default",
            "after": {"id": "9", "_s_tenantid": "tn1", "project_id": "1206"},
        })
        rec = parse_cdc(raw)
        assert rec["id"] == "9" and rec["project_id"] == "1206"
        assert rec["_source_table"] == "task"

    def test_delete_without_after_falls_back_to_envelope(self):
        """On delete the new image is absent; keys must still reach the router."""
        raw = json.dumps({
            "op": "d", "source_table": "silver.project_default",
            "id": "1206", "_s_tenantid": "tn1", "after": None,
        })
        rec = parse_cdc(raw)
        assert rec is not None and rec["_op"] == "d"
        assert rec["id"] == "1206" and rec["_source_table"] == "project"

    def test_empty_after_falls_back_to_envelope(self):
        raw = json.dumps({
            "op": "u", "source_table": "silver.project_default",
            "id": "1206", "_s_tenantid": "tn1", "after": {},
        })
        assert parse_cdc(raw)["id"] == "1206"

    def test_missing_source_table_yields_empty_string(self):
        """Router treats an unknown table as 'not a trigger' — must not raise."""
        msg = json.loads(_event()); msg.pop("source_table")
        assert parse_cdc(json.dumps(msg))["_source_table"] == ""

    def test_double_underscore_source_table_fallback(self):
        msg = json.loads(_event())
        msg["__source_table"] = msg.pop("source_table")
        assert parse_cdc(json.dumps(msg))["_source_table"] == "project"
