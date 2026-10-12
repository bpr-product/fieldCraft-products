"""
Policy contract — every error-type handler implements this Protocol.

A Policy is a pure function of (envelope, retry_state, now_ms). It must not
do IO. Decisions are interpreted by the watcher orchestrator:

    REPLAY  — publish envelope.raw_truncated back to the source Kafka topic.
              In shadow mode the watcher writes an audit row but does NOT publish.
    WAIT    — backoff: re-emit attempt count, set next_retry_at, do nothing else.
    PARK    — route to <entity>.dlq.parking. Terminal for this event — human review.
    SKIP    — drop quietly. Use only for envelopes the watcher cannot even parse.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class Action(str, Enum):
    REPLAY  = "REPLAY"
    CORRECT = "CORRECT"   # invoke the registered corrector, then replay on success
    WAIT    = "WAIT"
    PARK    = "PARK"
    SKIP    = "SKIP"


@dataclass(frozen=True)
class RetryState:
    """Per-event state pulled from Redis before policy evaluation."""
    attempts:        int  = 0
    next_retry_at_ms: int = 0
    first_seen_ms:   int  = 0
    last_error:      str  = ""


@dataclass(frozen=True)
class Decision:
    action:    Action
    reason:    str           # short, human-readable — written to audit table
    policy_id: str           # stable identifier of which policy + version made this call
    delay_ms:  int  = 0      # only meaningful for Action.WAIT


class Policy(Protocol):
    """All policies expose policy_id and evaluate()."""
    policy_id: str

    def evaluate(self, envelope: dict, state: RetryState, now_ms: int) -> Decision: ...
