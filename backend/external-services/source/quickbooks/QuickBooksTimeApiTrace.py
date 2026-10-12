"""Opt-in local diagnostics for QuickBooks Time API calls.

Diagnostics are structural only. Tokens, URLs, identifiers, parameters, payload
values, response headers, and response bodies are never logged.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Mapping, Optional

import requests

logger = logging.getLogger(__name__)

_TRUE_VALUES = {"1", "true", "yes", "on"}
_LOCAL_RUNTIME_ENVS = {"local", "dev", "development", "test", "testing"}
_TRACE_ENV = "QBO_TIME_API_RAW_HTTP_TRACE"


def _env_bool(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in _TRUE_VALUES


def _runtime_environment() -> str:
    return str(os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "").strip().lower()


def is_enabled() -> bool:
    return _runtime_environment() in _LOCAL_RUNTIME_ENVS and _env_bool(_TRACE_ENV)


def require_enabled() -> None:
    if _runtime_environment() not in _LOCAL_RUNTIME_ENVS:
        raise RuntimeError("QuickBooks Time diagnostics are blocked outside a local/test runtime.")
    if not _env_bool(_TRACE_ENV):
        raise RuntimeError(
            "QuickBooks Time diagnostics are disabled. Set QBO_TIME_API_RAW_HTTP_TRACE=true "
            "only for a controlled local test."
        )


def _shape(value: Any) -> str:
    if isinstance(value, Mapping):
        return f"mapping(keys={sorted(str(key) for key in value.keys())})"
    if isinstance(value, (list, tuple, set)):
        return f"{type(value).__name__}(length={len(value)})"
    if value is None:
        return "none"
    return type(value).__name__


def print_request(
    *,
    operation: str,
    method: str,
    url: str,
    headers: Mapping[str, Any],
    params: Optional[Mapping[str, Any]] = None,
    payload: Any = None,
) -> None:
    del url, headers
    if not is_enabled():
        return
    require_enabled()
    logger.info(
        "[QBO_TIME_API] request | operation=%s method=%s param_keys=%s payload_shape=%s",
        operation,
        method,
        sorted(str(key) for key in (params or {}).keys()),
        _shape(payload),
    )


def print_response(*, operation: str, response: requests.Response) -> None:
    if not is_enabled():
        return
    require_enabled()
    body_length = len(getattr(response, "content", b"") or b"")
    logger.info(
        "[QBO_TIME_API] response | operation=%s status=%s body_length=%s",
        operation,
        response.status_code,
        body_length,
    )
