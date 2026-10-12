from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Dict, Optional

from source.Utils import constants as const

logger = logging.getLogger(__name__)


_REDACTED_KEYS = ("token", "secret", "authorization", "password", "client_secret", "refresh_token", "access_token")


def support_debug_enabled() -> bool:
    raw = (os.getenv("QBO_PROJECT_SUPPORT_DEBUG", "false") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def fingerprint(value: Any) -> Optional[str]:
    raw = str(value or "")
    if not raw:
        return None
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"sha256:{digest};len:{len(raw)}"


def truthy_env(name: str) -> bool:
    return (os.getenv(name, "") or "").strip().lower() in ("1", "true", "yes", "on")


def _redact_value(key: str, value: Any) -> Any:
    lowered = str(key or "").lower()
    if any(marker in lowered for marker in _REDACTED_KEYS):
        if value in (None, ""):
            return None
        return f"[REDACTED {fingerprint(value)}]"
    return value


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            redacted = _redact_value(str(key), item)
            cleaned[str(key)] = sanitize(redacted) if redacted is item else redacted
        return cleaned
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def to_json(value: Any, *, limit: int = 20000) -> str:
    try:
        text = json.dumps(sanitize(value), default=str, ensure_ascii=False, indent=2)
    except Exception:
        text = str(value)
    if len(text) > limit:
        return text[:limit] + f"... [truncated {len(text) - limit} chars]"
    return text


def response_headers_snapshot(response: Any) -> Dict[str, Any]:
    headers = getattr(response, "headers", {}) or {}
    def h(name: str) -> Optional[str]:
        try:
            return headers.get(name) or headers.get(name.lower()) or headers.get(name.title())
        except Exception:
            return None

    intuit_tid = h(const.HEADER_INTUIT_TID) or h("Intuit-Tid") or h("intuit_tid")
    return {
        "status_code": getattr(response, "status_code", None),
        "intuit_tid": intuit_tid,
        "content_type": h("Content-Type"),
        "date": h("Date"),
        "server": h("Server"),
    }


def print_support_block(title: str, rows: Dict[str, Any]) -> None:
    """Write an opt-in, sanitized support diagnostic through application logging."""
    if not support_debug_enabled():
        return

    logger.info("[QBO_PROJECT_SUPPORT] %s | %s", title, to_json(rows))


def log_support_event(logger: Any, event: str, payload: Dict[str, Any], *, level: str = "info") -> None:
    if not support_debug_enabled():
        return
    message = f"[QBO_PROJECT_SUPPORT] {event} | {to_json(payload)}"
    log_fn = getattr(logger, level, None) or logger.info
    log_fn(message)
