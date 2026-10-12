"""Side-effectful correctors invoked when a Policy returns Action.CORRECT."""
from dlq_watcher.correctors.base import CorrectionOutcome, CorrectionResult, Corrector

__all__ = ["Corrector", "CorrectionResult", "CorrectionOutcome"]
