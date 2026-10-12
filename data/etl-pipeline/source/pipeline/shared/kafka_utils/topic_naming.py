import os

_PG_SCHEMA = os.environ.get("ETL_PG_SCHEMA", "public")


def topic(table: str, schema: str | None = None) -> str:
    """Return the canonical Kafka topic name: {env}.{schema}.{table}."""
    env = os.environ.get("ETL_ENV", "local")
    s = schema if schema is not None else os.environ.get("ETL_PG_SCHEMA", _PG_SCHEMA)
    return f"{env}.{s}.{table}"


def dlq_topic(table: str | None = None, schema: str | None = None) -> str:
    """Return the unified DLQ topic: {env}.dlq.

    Path A (Event Hubs consolidation): all entities share ONE dead-letter hub
    instead of a per-entity `{env}.{schema}.{table}.dlq` topic — otherwise the
    per-entity DLQ topics alone would exceed the Event Hubs Standard 10-hub cap.
    The DLQ envelope already carries `entity`, so downstream triage is unaffected.
    The `table`/`schema` args are accepted for backward compatibility and ignored.
    """
    env = os.environ.get("ETL_ENV", "local")
    return f"{env}.dlq"


def consumer_group(layer: str, table: str) -> str:
    """Return a unique consumer group name for a layer/table pair."""
    return f"{layer}-{table}-consumer"
