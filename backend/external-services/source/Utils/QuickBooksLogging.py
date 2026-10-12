from __future__ import annotations

import logging
from typing import Any, Mapping


_SAFE_CDC_FIELDS = frozenset(
    {
        "changedSince",
        "changedSinceOverride",
        "count",
        "forceFull",
        "maxResults",
        "mode",
        "module",
        "normalVendorCount",
        "previousSyncStartAt",
        "previousSyncStartTime",
        "reused",
        "selected",
        "skippedNormalVendorCount",
        "source",
        "startPosition",
        "syncMode",
        "vendor1099Count",
        "watermarkSource",
    }
)


def log_cdc_debug(log: logging.Logger, message: str, **data: Any) -> None:
    """Write a structural CDC trace without tenant, entity, token, or payload data."""
    if not log.isEnabledFor(logging.DEBUG):
        return

    safe_data: Mapping[str, Any] = {
        key: value
        for key, value in data.items()
        if key in _SAFE_CDC_FIELDS and isinstance(value, (str, int, float, bool, type(None)))
    }
    if safe_data:
        log.debug("[QBO CDC] %s | %s", message, safe_data)
    else:
        log.debug("[QBO CDC] %s", message)
