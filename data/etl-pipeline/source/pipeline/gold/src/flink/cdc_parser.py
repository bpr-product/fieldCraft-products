"""Shared CDC envelope parser for Gold Flink streaming jobs."""

from __future__ import annotations

import json
from typing import Any

# Silver's list-partitioning convention (see silver/ddl/create_silver_tables.py):
# every entity is a partitioned parent (e.g. "user_account") with exactly two
# leaf partitions, "<entity>_staging" and "<entity>_default". Because the
# Silver CDC connector's publication was created without
# PUBLISH_VIA_PARTITION_ROOT, pgoutput reports each CHANGE under its actual
# leaf-partition relation name, never the parent's bare name -- so every
# Debezium CDC event's source.table is "user_account_staging"/"_default", not
# "user_account". Every consumer of _source_table downstream (gold_eden_stream's
# _ALL_TABLE_TO_COLLECTIONS, gold_user_permissions_stream's _TRIGGER_TABLES) is
# keyed by the bare entity name, so without stripping this suffix here, EVERY
# CDC event silently fails the `table in <trigger set>` check and gets dropped
# -- confirmed live: gold-eden-stream/gold-canonical-stream ran RUNNING with
# real throughput on their Kafka source (records-consumed-total climbing) but
# zero documents ever written to Mongo, no exception anywhere, no signal that
# anything was wrong.
_PARTITION_SUFFIXES = ("_staging", "_default")


def _strip_partition_suffix(table: str) -> str:
    for suffix in _PARTITION_SUFFIXES:
        if table.endswith(suffix):
            return table[: -len(suffix)]
    return table


def parse_cdc(raw: str) -> "dict[str, Any] | None":
    """Parse a Debezium CDC JSON envelope into a flat record dict.

    Returns None for unrecognised ops, tombstones, and malformed JSON.
    Messages pre-flattened by ExtractNewRecordState have no "after" wrapper;
    on delete the new image is absent so we fall back to the envelope so the
    tenant/project keys (if present) can still trigger a recompute.
    """
    try:
        msg = json.loads(raw)
        op = msg.get("op") or msg.get("__op")
        if op not in ("c", "u", "r", "d"):
            return None
        payload = msg.get("after") or msg
        if not payload:
            return None
        payload["_op"] = op
        payload["_tenantid"] = (
            payload.get("_s_tenantid")
            or payload.get("tenantid")
            or msg.get("source_db")
        )
        raw_table = msg.get("source_table") or msg.get("__source_table") or ""
        payload["_source_table"] = _strip_partition_suffix(raw_table.split(".")[-1])
        return payload
    except (json.JSONDecodeError, AttributeError):
        return None
