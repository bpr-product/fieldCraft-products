import json
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

BOOTSTRAP = os.environ.get("ETL_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
ETL_ENV   = os.environ.get("ETL_ENV", "local")


class KafkaWriter:
    def __init__(self, config) -> None:
        self._config = config
        self._producer = None

    def _get_producer(self):
        if self._producer is None:
            from confluent_kafka import Producer
            from shared.kafka_utils.auth import confluent_kafka_props
            self._producer = Producer({
                "bootstrap.servers": BOOTSTRAP,
                "acks": "all",
                "retries": 5,
                "retry.backoff.ms": 200,
                **confluent_kafka_props(),
            })
        return self._producer

    def _topic(self) -> str:
        # Path A: all external data goes to one consolidated hub. The entity
        # (source_entity) and source_name travel in the message envelope, so
        # Bronze/Silver route on fields, not the topic name.
        return f"{ETL_ENV}.external.events"

    def write(self, envelopes: list[dict[str, Any]]) -> None:
        producer = self._get_producer()
        topic = self._topic()
        for env in envelopes:
            key = f"{env.get('tenantid', '')}:{env.get('external_id', '')}".encode()
            producer.produce(
                topic,
                key=key,
                value=json.dumps(env, default=str).encode(),
            )
            producer.poll(0)
        producer.flush(timeout=10)
        log.info("KafkaWriter: %d records → %s", len(envelopes), topic)

    def close(self) -> None:
        if self._producer:
            self._producer.flush(timeout=10)
