from typing import Any


def clean_qbo_value(value: Any) -> str:
    return str(value or "").strip()


def escape_qbo_literal(value: Any) -> str:
    """Escape a QBQL string literal safely for equality comparisons.

    Keep this helper small and deterministic so services can share the same
    literal escaping behaviour. We escape both backslashes and single quotes to
    avoid malformed QBQL literals and inconsistent matching.
    """
    return clean_qbo_value(value).replace("\\", "\\\\").replace("'", "\\'")
