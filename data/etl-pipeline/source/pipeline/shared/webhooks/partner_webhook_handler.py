"""
partner_webhook_handler.py

FastAPI webhook receiver for external partner data.

Normalises incoming payloads into the platform CDC envelope and publishes
to the SAME Kafka topics as internal CDC (local.public.{entity}) — not a
separate external namespace. The `source_type=external` field in the
envelope distinguishes external events from Debezium CDC events.

This means the universal Bronze ingest job and all Silver jobs handle
external data automatically — no separate external pipeline needed.

Endpoints
---------
POST /webhook/{source_name}/{entity}
    Headers:
        X-Tenant-ID   : tenant identifier (required)
        X-Signature   : HMAC-SHA256 of body using source secret (optional)
        X-Event-Type  : op hint ("create", "update", "delete") — optional
        X-ID-Field    : which field is the primary key (default "id")
        X-TS-Field    : which field is the timestamp (optional)

POST /webhook/{source_name}/{entity}/batch
    Body: JSON array of records

GET /webhook/health

Configuration (env vars)
------------------------
  ETL_KAFKA_BOOTSTRAP_SERVERS
  ETL_ENV                     : topic prefix (default local)
  WEBHOOK_SECRET_{SOURCE}     : HMAC secret per source (e.g. WEBHOOK_SECRET_QUICKBOOKS)
  WEBHOOK_MAX_BATCH_SIZE      : max records per batch (default 1000)

Run
---
    uvicorn shared.webhooks.partner_webhook_handler:app --host 0.0.0.0 --port 8090
"""
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.responses import JSONResponse
from confluent_kafka import Producer, KafkaException

from shared.kafka_utils.topic_naming import topic as cdc_topic, dlq_topic
from shared.external_utils.payload_normaliser import normalise, normalise_batch
from shared.alerting.notify import warning as alert_warning

log = logging.getLogger(__name__)

BOOTSTRAP      = os.environ.get("ETL_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
ETL_ENV        = os.environ.get("ETL_ENV",                     "local")
MAX_BATCH_SIZE = int(os.environ.get("WEBHOOK_MAX_BATCH_SIZE",  "1000"))

app = FastAPI(title="FIELDCRAFT External Webhook Handler", version="2.0.0")

_producer: Producer | None = None


def _get_producer() -> Producer:
    global _producer
    if _producer is None:
        _producer = Producer({
            "bootstrap.servers": BOOTSTRAP,
            "acks":              "all",
            "retries":           5,
            "retry.backoff.ms":  200,
            "compression.type":  "lz4",
        })
    return _producer


def _delivery_report(err, msg) -> None:
    if err:
        log.error("Kafka delivery failed: %s | topic=%s", err, msg.topic())


def _verify_signature(source_name: str, body: bytes, signature: str | None) -> None:
    secret = os.environ.get(f"WEBHOOK_SECRET_{source_name.upper()}")
    if not secret:
        # Fail CLOSED, not open: an unconfigured source-specific secret must
        # never be treated as "no signature check required" — source_name is an
        # unvalidated path parameter, so this previously let anyone post
        # unsigned events for any misconfigured/unrecognized source. Reject the
        # request and alert so the missing config gets noticed and fixed.
        log.warning(
            "Webhook request rejected: WEBHOOK_SECRET_%s is not configured",
            source_name.upper(),
        )
        alert_warning(
            title=f"Webhook rejected — missing secret config for source '{source_name}'",
            body=(
                f"A webhook request for source '{source_name}' was rejected because "
                f"WEBHOOK_SECRET_{source_name.upper()} is not configured. The request "
                f"was refused rather than accepted unsigned."
            ),
            details={"source_name": source_name},
        )
        raise HTTPException(
            status_code=401,
            detail=f"Signature verification is not configured for source '{source_name}'",
        )
    if not signature:
        raise HTTPException(status_code=401, detail="X-Signature header required for this source")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.strip()):
        raise HTTPException(status_code=401, detail="Invalid signature")


def _publish(envelope: dict[str, Any], kafka_topic: str) -> None:
    """Publish envelope to the unified CDC topic namespace."""
    producer = _get_producer()
    key = f"{envelope['tenantid']}:{envelope.get('external_id', '')}".encode()
    producer.produce(
        kafka_topic,
        key=key,
        value=json.dumps(envelope, default=str).encode(),
        callback=_delivery_report,
    )
    producer.poll(0)


def _envelope_to_unified(envelope: dict[str, Any]) -> dict[str, Any]:
    """
    Add source_type=external and map source_entity → _entity so the
    universal Bronze and Silver jobs can process it without changes.
    """
    return {
        **envelope,
        "source_type": "external",
        "_entity":     envelope.get("source_entity", "unknown"),
        "table_name":  f"public.{envelope.get('source_entity', 'unknown')}",
        # Map external_id to id so Silver PK lookup works for matching entities
        "id":          envelope.get("external_id"),
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/webhook/health")
async def health() -> JSONResponse:
    try:
        p = _get_producer()
        meta = p.list_topics(timeout=3)
        return JSONResponse({"status": "ok", "brokers": len(meta.brokers)})
    except KafkaException as exc:
        return JSONResponse({"status": "degraded", "error": str(exc)}, status_code=503)


@app.post("/webhook/{source_name}/{entity}")
async def receive_event(
    source_name: str,
    entity: str,
    request: Request,
    x_tenant_id:  str | None = Header(default=None, alias="X-Tenant-ID"),
    x_signature:  str | None = Header(default=None, alias="X-Signature"),
    x_event_type: str | None = Header(default=None, alias="X-Event-Type"),
    x_id_field:   str | None = Header(default=None, alias="X-ID-Field"),
    x_ts_field:   str | None = Header(default=None, alias="X-TS-Field"),
) -> JSONResponse:
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header is required")

    body = await request.body()
    _verify_signature(source_name, body, x_signature)

    try:
        raw = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")

    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object (use /batch for arrays)")

    envelope = normalise(
        raw=raw,
        source_name=source_name,
        source_entity=entity,
        tenantid=x_tenant_id,
        id_field=x_id_field or "id",
        ts_field=x_ts_field or None,
        op=x_event_type or None,
    )
    unified = _envelope_to_unified(envelope)

    # Publish to the same topic namespace as internal CDC
    target_topic = cdc_topic(entity)
    try:
        _publish(unified, target_topic)
        _get_producer().flush(timeout=5)
    except KafkaException as exc:
        log.error("Kafka publish failed: %s", exc)
        raise HTTPException(status_code=503, detail=f"Kafka unavailable: {exc}")

    return JSONResponse(
        {"accepted": 1, "topic": target_topic, "external_id": envelope["external_id"]},
        status_code=202,
    )


@app.post("/webhook/{source_name}/{entity}/batch")
async def receive_batch(
    source_name: str,
    entity: str,
    request: Request,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_signature: str | None = Header(default=None, alias="X-Signature"),
    x_id_field:  str | None = Header(default=None, alias="X-ID-Field"),
    x_ts_field:  str | None = Header(default=None, alias="X-TS-Field"),
) -> JSONResponse:
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header is required")

    body = await request.body()
    _verify_signature(source_name, body, x_signature)

    try:
        records = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")

    if not isinstance(records, list):
        raise HTTPException(status_code=400, detail="Batch body must be a JSON array")

    if len(records) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Batch size {len(records)} exceeds limit {MAX_BATCH_SIZE}",
        )

    envelopes = normalise_batch(
        records=records,
        source_name=source_name,
        source_entity=entity,
        tenantid=x_tenant_id,
        id_field=x_id_field or "id",
        ts_field=x_ts_field or None,
    )

    target_topic = cdc_topic(entity)
    try:
        for env in envelopes:
            _publish(_envelope_to_unified(env), target_topic)
        _get_producer().flush(timeout=10)
    except KafkaException as exc:
        log.error("Kafka publish failed: %s", exc)
        raise HTTPException(status_code=503, detail=f"Kafka unavailable: {exc}")

    return JSONResponse(
        {"accepted": len(envelopes), "skipped": len(records) - len(envelopes), "topic": target_topic},
        status_code=202,
    )
