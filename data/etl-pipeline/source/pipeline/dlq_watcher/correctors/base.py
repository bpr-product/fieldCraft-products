"""
Corrector contract — side-effectful repair functions invoked by the watcher
when a Policy returns Action.CORRECT.

Correctors are paired 1:1 with error_types but live separately from Policies:
  Policy.evaluate(...)  → pure: returns Decision (no IO)
  Corrector.correct(...) → side-effects: runs DDL, mutates Redis, etc.

Outcome enum mirrors the audit table's outcome column.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class CorrectionOutcome(str, Enum):
    """Terminal status of a corrector run."""
    APPLIED  = "applied"   # the fix succeeded; replay should proceed
    SKIPPED  = "skipped"   # no-op (e.g. fix already in place); replay should proceed
    DEFERRED = "deferred"  # corrector cannot fix yet (e.g. waiting for parent); republish to DLQ
    REFUSED  = "refused"   # corrector deliberately declined (unsafe shape); park
    FAILED   = "failed"    # corrector errored; park


@dataclass(frozen=True)
class CorrectionResult:
    outcome:         CorrectionOutcome
    reason:          str
    original_value:  dict | None = None
    corrected_value: dict | None = None
    actions_taken:   list[str]   = field(default_factory=list)


class Corrector(Protocol):
    """All correctors expose a stable corrector_id + correct()."""
    corrector_id: str

    def correct(self, envelope: dict) -> CorrectionResult: ...
