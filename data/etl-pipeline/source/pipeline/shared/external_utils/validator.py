"""
Structural validation for external source records before normalisation.

Rules:
  1. Record must be a non-empty dict.
  2. At least one value must be non-null.
  3. If id_field is explicitly configured (not the default "id"), it must be present.
  4. Record JSON representation must be under MAX_RECORD_BYTES.

Returns (is_valid: bool, error_type: ErrorType | None, message: str).
"""
import json
import logging
from typing import Any, Protocol

from shared.dlq.errors import ErrorType

log = logging.getLogger(__name__)

MAX_RECORD_BYTES = 1 * 1024 * 1024  # 1 MB
_DEFAULT_ID_FIELD = "id"


class _ConnectorConfigLike(Protocol):
    id_field: str
    source_name: str
    source_entity: str


def validate_record(
    raw: Any,
    config: _ConnectorConfigLike,
) -> tuple[bool, ErrorType | None, str]:
    if not isinstance(raw, dict):
        msg = f"record is not a dict: {type(raw).__name__}"
        log.warning("Validation failed: %s", msg)
        return False, ErrorType.MALFORMED_JSON, msg

    if not raw:
        msg = "empty record dict"
        log.warning("Validation failed: %s", msg)
        return False, ErrorType.MALFORMED_JSON, msg

    if all(v is None for v in raw.values()):
        msg = "all null values — record carries no data"
        log.warning("Validation failed: %s", msg)
        return False, ErrorType.MALFORMED_JSON, msg

    id_field = config.id_field
    if id_field and id_field != _DEFAULT_ID_FIELD:
        pk_val = raw.get(id_field)
        if pk_val is None or str(pk_val).strip() == "":
            msg = f"configured id_field '{id_field}' is absent or null"
            log.warning("Validation failed: %s", msg)
            return (
                False,
                ErrorType.NO_PK,
                msg,
            )

    try:
        size = len(json.dumps(raw, default=str).encode("utf-8"))
        if size > MAX_RECORD_BYTES:
            # MALFORMED_JSON is the closest available ErrorType for pre-ingestion
            # policy rejections. An oversized record is structurally valid but
            # exceeds the platform's ingest limit.
            msg = f"record too large: {size:,} bytes (max {MAX_RECORD_BYTES:,})"
            log.warning("Validation failed: %s", msg)
            return (
                False,
                ErrorType.MALFORMED_JSON,
                msg,
            )
    except Exception as exc:
        msg = f"record not JSON-serialisable: {exc}"
        log.warning("Validation failed: %s", msg)
        return False, ErrorType.MALFORMED_JSON, msg

    return True, None, ""
