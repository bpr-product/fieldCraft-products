"""DLQ envelope builder and error-type taxonomy shared by Bronze and Silver."""
from shared.dlq.errors import (
    ErrorType,
    build_envelope,
    classify_pg_exception,
    classify_sqlstate,
)

__all__ = [
    "ErrorType",
    "build_envelope",
    "classify_pg_exception",
    "classify_sqlstate",
]
