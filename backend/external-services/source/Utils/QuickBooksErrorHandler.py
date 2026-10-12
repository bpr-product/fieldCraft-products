from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Dict, Optional

import requests

from source.Utils import constants as const


class QuickBooksAPIError(Exception):
    """Normalized QuickBooks API error with user-safe message and Intuit trace id."""

    def __init__(
        self,
        user_message: str,
        *,
        status_code: Optional[int] = None,
        intuit_tid: Optional[str] = None,
        detail: Optional[str] = None,
        response_text: Optional[str] = None,
    ):
        super().__init__(user_message)
        self.user_message = (user_message or 'QuickBooks request failed.').strip()
        self.status_code = status_code
        self.intuit_tid = (intuit_tid or '').strip() or None
        self.detail = (detail or '').strip() or None
        self.response_text = response_text

    def __str__(self) -> str:
        if self.intuit_tid:
            return f"{self.user_message} Reference ID: {self.intuit_tid}"
        return self.user_message


_ACTIVE_QBO_CAPTURE: ContextVar[Optional[Dict[str, Any]]] = ContextVar('active_qbo_capture', default=None)


def begin_intuit_tid_capture(target: Optional[Dict[str, Any]]):
    if isinstance(target, dict):
        target.setdefault('intuitTids', [])
    return _ACTIVE_QBO_CAPTURE.set(target if isinstance(target, dict) else None)


def end_intuit_tid_capture(token) -> None:
    _ACTIVE_QBO_CAPTURE.reset(token)


def get_intuit_tid(response: requests.Response) -> Optional[str]:
    return (
        response.headers.get('intuit_tid')
        or response.headers.get('Intuit-Tid')
        or response.headers.get('intuit-tid')
    )


def _append_tid_list(target: Dict[str, Any], tid: Optional[str]) -> None:
    if not tid:
        return
    items = target.setdefault('intuitTids', [])
    if isinstance(items, list) and tid not in items:
        items.append(tid)


def capture_response_metadata(response: requests.Response, *, metadata_target: Optional[Dict[str, Any]] = None) -> Optional[str]:
    tid = get_intuit_tid(response)
    if isinstance(metadata_target, dict):
        if tid:
            metadata_target['intuitTid'] = tid
        metadata_target['statusCode'] = response.status_code
    capture_target = _ACTIVE_QBO_CAPTURE.get()
    if isinstance(capture_target, dict):
        _append_tid_list(capture_target, tid)
    return tid


def qbo_get(*args, metadata_target: Optional[Dict[str, Any]] = None, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", 30)
    response = requests.get(*args, **kwargs)
    capture_response_metadata(response, metadata_target=metadata_target)
    return response


def qbo_post(*args, metadata_target: Optional[Dict[str, Any]] = None, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", 30)
    response = requests.post(*args, **kwargs)
    capture_response_metadata(response, metadata_target=metadata_target)
    return response


def safe_json(response: requests.Response) -> Dict[str, Any]:
    try:
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _extract_fault_message(payload: Dict[str, Any]) -> Optional[str]:
    fault = payload.get('Fault')
    if isinstance(fault, dict):
        errors = fault.get('Error')
        if isinstance(errors, list) and errors:
            first = errors[0] if isinstance(errors[0], dict) else None
            if first:
                for key in ('Detail', 'Message'):
                    val = first.get(key)
                    if val:
                        return str(val).strip()
        for key in ('Detail', 'Message'):
            val = fault.get(key)
            if val:
                return str(val).strip()
    for key in ('message', 'error_description', 'error'):
        val = payload.get(key)
        if val:
            return str(val).strip()
    return None


def build_user_message(response: requests.Response, fallback: str) -> str:
    extracted = _extract_fault_message(safe_json(response))
    return extracted or fallback


def raise_for_qbo_response(
    response: requests.Response,
    *,
    success_statuses: tuple[int, ...] = (200,),
    fallback_message: str = 'QuickBooks request failed.',
) -> None:
    if response.status_code in success_statuses:
        return
    raise QuickBooksAPIError(
        build_user_message(response, fallback_message),
        status_code=response.status_code,
        intuit_tid=get_intuit_tid(response),
        detail=_extract_fault_message(safe_json(response)),
        response_text=None,
    )


def apply_error_to_summary(target: Dict[str, Any], exc: Exception, *, error_key: str = const.KEY_ERROR) -> Dict[str, Any]:
    if isinstance(exc, QuickBooksAPIError):
        if exc.intuit_tid:
            target['intuitTid'] = exc.intuit_tid
        if exc.status_code is not None:
            target['statusCode'] = exc.status_code
        if exc.detail and exc.detail != exc.user_message:
            target['detail'] = exc.detail
        target[error_key] = str(exc)
        return target
    target[error_key] = str(exc)
    return target
