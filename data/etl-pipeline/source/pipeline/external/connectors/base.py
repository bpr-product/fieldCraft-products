import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Iterator

from external.config.schema import ConnectorConfig
from shared.dlq.errors import redact_pii
from shared.external_utils.payload_normaliser import normalise

log = logging.getLogger(__name__)


def _redacted_preview(raw: Any, limit: int) -> str:
    """PII-redact a raw source record before it's ever logged or written to
    the external DLQ — same redact_pii() used by shared.dlq.errors.build_envelope
    for the standard {env}.dlq hub, so this connector-local DLQ path (which
    doesn't go through build_envelope) gets the same protection."""
    return redact_pii(json.dumps(raw, default=str))[:limit]


class BaseConnector(ABC):
    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config

    @abstractmethod
    def fetch(self) -> Iterator[dict]:
        """Yield raw source records one at a time."""
        ...

    def _commit_cursor(self) -> None:
        """Override in subclasses to persist watermark/cursor after all routing succeeds."""

    def run(self) -> dict:
        from external.writers.kafka_writer import KafkaWriter
        from external.writers.iceberg_writer import IcebergWriter
        from external.writers.silver_writer import SilverDirectWriter
        from shared.external_utils.validator import validate_record

        kafka   = KafkaWriter(self.config)
        iceberg = IcebergWriter(self.config)
        silver  = SilverDirectWriter(self.config)
        buffer: list[dict[str, Any]] = []
        dlq_buffer: list[dict] = []
        total = 0
        dlq_count = 0

        try:
            for raw in self.fetch():
                # Step 1: structural validation
                is_valid, err_type, err_msg = validate_record(raw, self.config)
                if not is_valid:
                    log.warning(
                        "Validation failed [%s/%s] %s: %s — raw=%s",
                        self.config.source_name, self.config.source_entity,
                        err_type.value if err_type else "UNKNOWN", err_msg,
                        _redacted_preview(raw, 200),
                    )
                    # TODO(arch): external validation failures go to silver._external_dlq
                    # (not the standard {env}.dlq hub consumed by dlq_watcher). This is a
                    # known gap — auto-retry, circuit breaker, and dlq_corrections audit
                    # do not apply to external DLQ events. Tracked for future routing via
                    # shared/dlq/errors.py + {env}.dlq hub.
                    dlq_buffer.append({
                        "source_name":   self.config.source_name,
                        "source_entity": self.config.source_entity,
                        "error_type":    err_type.value if err_type else "UNKNOWN",
                        "error_message": err_msg,
                        "raw_truncated": _redacted_preview(raw, 500),
                    })
                    dlq_count += 1
                    continue

                # Step 2: normalise valid record into CDC envelope
                try:
                    envelope = normalise(
                        raw=raw,
                        source_name=self.config.source_name,
                        source_entity=self.config.source_entity,
                        tenantid=self.config.resolved_tenantid,
                        id_field=self.config.id_field,
                        ts_field=self.config.ts_field or None,
                    )
                except Exception as exc:
                    log.warning("Normalisation failed: %s — %s", _redacted_preview(raw, 200), exc)
                    dlq_buffer.append({
                        "source_name":   self.config.source_name,
                        "source_entity": self.config.source_entity,
                        "error_type":    "MALFORMED_JSON",
                        "error_message": str(exc),
                        "raw_truncated": _redacted_preview(raw, 500),
                    })
                    dlq_count += 1
                    continue

                buffer.append(envelope)
                total += 1

                if len(buffer) >= self.config.routing.batch_flush_size:
                    self._route(buffer, kafka, iceberg, silver, total)
                    buffer.clear()

            if buffer:
                self._route(buffer, kafka, iceberg, silver, total)

            # All batches routed successfully — safe to advance the cursor/watermark.
            self._commit_cursor()

            # Flush DLQ buffer
            if dlq_buffer:
                try:
                    silver.write_dlq(dlq_buffer)
                except Exception as exc:
                    log.error("Failed to write %d records to DLQ: %s", len(dlq_buffer), exc)

        finally:
            kafka.close()
            silver.close()

        log.info(
            "Connector %s/%s finished: processed=%d dlq=%d",
            self.config.source_name, self.config.source_entity, total, dlq_count,
        )
        return {
            "source":            self.config.source_name,
            "entity":            self.config.source_entity,
            "records_processed": total,
            "dlq_count":         dlq_count,
        }

    def _route(self, batch: list[dict], kafka, iceberg, silver, total_so_far: int) -> None:
        strategy  = self.config.routing.strategy
        threshold = self.config.routing.kafka_threshold_rows

        # Always write directly to Silver — bypasses Flink for ext_ tables
        silver.write(batch)

        # Also publish to Kafka so Bronze picks it up for the audit trail
        if strategy == "iceberg":
            iceberg.write(batch)
        elif strategy == "kafka":
            kafka.write(batch)
        else:  # auto
            if total_so_far <= threshold:
                kafka.write(batch)
            else:
                iceberg.write(batch)
