"""
payload_normaliser.py

Converts arbitrary external payloads (REST API responses, CSV rows, webhook
bodies) into the platform's standard CDC envelope so they can flow through the
same Bronze → Silver pipeline as internal CDC events — with no hardcoded schema.

Platform envelope fields
------------------------
  op            : always "r" (read/snapshot) for external; "c"/"u"/"d" if source signals it
  lsn           : monotonic sequence number — derived from ingestion timestamp (ms) when absent
  ts_ms         : event timestamp in epoch milliseconds
  tenantid      : injected from context (env var or request header)
  source_name   : name of the external source system (e.g. "quickbooks", "weather_api")
  source_entity : entity/table name within the source (e.g. "invoice", "sensor_reading")
  external_id   : source-side primary key (string); required for Silver dedup
  payload       : remaining business fields as a JSON string (fully schema-agnostic)
  _ingested_at  : wall-clock time this record entered the platform

The payload field stores all source fields that don't map to envelope fields.
Silver jobs read payload as a JSON string and use JSON_VALUE() for any
projection they need — no Silver job needs regenerating when source fields change.

Usage
-----
    from shared.external_utils.payload_normaliser import normalise, infer_op

    envelope = normalise(
        raw={"id": "INV-001", "amount": 150.0, "status": "paid"},
        source_name="quickbooks",
        source_entity="invoice",
        tenantid="tenant_abc",
        id_field="id",        # which source field is the PK
    )
    # envelope is a dict ready to JSON-serialise and publish to Kafka
"""
import json
import os
import time
from typing import Any

# Fields that are part of the CDC envelope — everything else goes into payload
_ENVELOPE_FIELDS = frozenset({"op", "lsn", "ts_ms", "tenantid", "source_name",
                               "source_entity", "external_id", "payload", "_ingested_at"})

# Source fields that hint at CDC operation type
_OP_HINT_FIELDS = {
    "op": None,
    "operation": None,
    "event_type": None,
    "action": None,
    "change_type": None,
}
_OP_MAP = {
    "create": "c", "insert": "c", "new": "c",
    "update": "u", "modify": "u", "change": "u",
    "delete": "d", "remove": "d", "destroy": "d",
    "read":   "r", "snapshot": "r", "sync": "r",
    "c": "c", "u": "u", "d": "d", "r": "r",
}

_ENV_TENANTID = os.environ.get("TENANTID", "unknown")


def infer_op(raw: dict[str, Any]) -> str:
    """Best-effort: extract CDC op from source payload hints; default 'r'."""
    for hint in _OP_HINT_FIELDS:
        val = raw.get(hint)
        if val and isinstance(val, str):
            mapped = _OP_MAP.get(val.lower().strip())
            if mapped:
                return mapped
    return "r"


def normalise(
    raw: dict[str, Any],
    source_name: str,
    source_entity: str,
    tenantid: str | None = None,
    id_field: str = "id",
    ts_field: str | None = None,
    op: str | None = None,
    lsn: int | None = None,
) -> dict[str, Any]:
    """
    Convert a raw source record into a platform CDC envelope.

    Parameters
    ----------
    raw           : source record (dict from JSON, CSV row, webhook body, etc.)
    source_name   : logical name of the source system
    source_entity : logical entity/table name within the source
    tenantid      : tenant identifier; falls back to TENANTID env var
    id_field      : field name in `raw` that holds the source primary key
    ts_field      : field name in `raw` that holds event timestamp (epoch ms or ISO)
    op            : override CDC operation; if None, inferred from raw hints
    lsn           : override monotonic sequence; if None, derived from ts_ms
    """
    now_ms = int(time.time() * 1000)

    # Resolve timestamp
    ts_ms: int
    if ts_field and ts_field in raw:
        ts_raw = raw[ts_field]
        if isinstance(ts_raw, (int, float)):
            ts_ms = int(ts_raw)
        else:
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                ts_ms = int(dt.timestamp() * 1000)
            except Exception:
                ts_ms = now_ms
    else:
        ts_ms = now_ms

    resolved_op  = op or infer_op(raw)
    resolved_lsn = lsn if lsn is not None else ts_ms

    # external_id: coerce PK to string; fall back to uuid4 so every record is
    # unique even when the source has no id field or processes many rows per ms.
    import uuid as _uuid
    pk_val = raw.get(id_field)
    external_id = str(pk_val) if pk_val is not None else str(_uuid.uuid4())

    # Everything that isn't an envelope field becomes the payload
    business_fields = {k: v for k, v in raw.items() if k not in _ENVELOPE_FIELDS}

    return {
        "op":            resolved_op,
        "lsn":           resolved_lsn,
        "ts_ms":         ts_ms,
        "tenantid":      tenantid or _ENV_TENANTID,
        "source_name":   source_name,
        "source_entity": source_entity,
        "external_id":   external_id,
        "payload":       json.dumps(business_fields, default=str),
        "_ingested_at":  now_ms,
    }


def normalise_batch(
    records: list[dict[str, Any]],
    source_name: str,
    source_entity: str,
    tenantid: str | None = None,
    id_field: str = "id",
    ts_field: str | None = None,
) -> list[dict[str, Any]]:
    """Normalise a list of raw records. Skips records that raise exceptions."""
    envelopes = []
    for raw in records:
        try:
            envelopes.append(normalise(raw, source_name, source_entity, tenantid, id_field, ts_field))
        except Exception as exc:
            import logging
            # Never log raw record values — `raw` is an unredacted external
            # record (webhook body/REST row/CSV row) that may carry PII or
            # partner-confidential data. Log only its shape.
            keys = list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__
            logging.getLogger(__name__).warning(
                "Skipping malformed record (keys=%s): %s", keys, exc
            )
    return envelopes
