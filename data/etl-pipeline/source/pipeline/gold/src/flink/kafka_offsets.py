"""Configurable Kafka starting-offsets for Gold streams (R3).

The Gold streams default to `latest` — correct for prod steady-state (process
new CDC as it arrives). But because realtime features have **no batch
reconciliation** (the streams are the sole writer), there is no built-in way to
recover events missed during a cold start (fresh deploy / lost checkpoint /
events produced while the job was down). `latest` skips them.

This makes the starting offset an env knob so an operator can do a **controlled
replay** to close such a gap, then switch back:

    KAFKA_STARTING_OFFSETS = latest     (default — steady-state)
                           | earliest   (replay the whole topic — gap recovery)
                           | committed  (resume the consumer group's committed offset)

Note: on a normal restart Flink already resumes from its checkpoint regardless
of this setting; the initializer only applies on a *fresh* start with no state.
Re-reads are safe — recompute reads current Silver and upserts idempotently
(LSN guard), so replaying old triggers cannot corrupt Gold.
"""

from __future__ import annotations

import os


def starting_offsets_mode() -> str:
    """Normalized mode from KAFKA_STARTING_OFFSETS (default 'latest')."""
    mode = os.environ.get("KAFKA_STARTING_OFFSETS", "latest").strip().lower()
    return mode if mode in ("latest", "earliest", "committed") else "latest"


def make_offsets_initializer():
    """Return the KafkaOffsetsInitializer for the configured mode.

    'committed' falls back to latest when the group has no committed offset.
    """
    from pyflink.datastream.connectors.kafka import (
        KafkaOffsetsInitializer,
        KafkaOffsetResetStrategy,
    )

    mode = starting_offsets_mode()
    if mode == "earliest":
        return KafkaOffsetsInitializer.earliest()
    if mode == "committed":
        return KafkaOffsetsInitializer.committed_offsets(
            KafkaOffsetResetStrategy.LATEST
        )
    return KafkaOffsetsInitializer.latest()
