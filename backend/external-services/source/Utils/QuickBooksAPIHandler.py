from __future__ import annotations

import threading
from typing import Any, Dict, Optional

import requests

from source.Utils import constants as const

import re

_RESPONSE_BODY_LIMIT = 4000
_STATE = threading.local()

_SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9_. -]")




def build_qbo_short_code(stable_value: Any) -> str:
    value = str(stable_value or "")
    if not value:
        return ""
    return value[: const.QBO_DISPLAY_SHORT_CODE_LENGTH].upper()


def build_qbo_display_name(base_name: Any, entity_type: str, stable_value: Any) -> str:
    name = str(base_name or "")
    code = build_qbo_short_code(stable_value)
    if not name or not entity_type or not code:
        return name
    return f"{name}-{entity_type}-{code}"


def clear_last_intuit_tid() -> None:
    _STATE.last_intuit_tid = None


def set_last_intuit_tid(intuit_tid: Optional[str]) -> None:
    _STATE.last_intuit_tid = (str(intuit_tid).strip() or None) if intuit_tid else None


def get_last_intuit_tid() -> Optional[str]:
    value = getattr(_STATE, "last_intuit_tid", None)
    return (str(value).strip() or None) if value else None


def extract_intuit_tid(response: Optional[requests.Response]) -> Optional[str]:
    if response is None:
        return None
    try:
        value = response.headers.get(const.HEADER_INTUIT_TID) or response.headers.get(const.HEADER_INTUIT_TID.title())
    except Exception:
        value = None
    value = (str(value).strip() or None) if value else None
    if value:
        set_last_intuit_tid(value)
    return value


def safe_json(response: Optional[requests.Response]) -> Dict[str, Any]:
    if response is None:
        return {}
    try:
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def truncate_text(value: Any, limit: int = _RESPONSE_BODY_LIMIT) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _extract_fault_text(payload: Dict[str, Any]) -> Optional[str]:
    fault = payload.get("Fault") or {}
    errors = fault.get("Error") or []
    if isinstance(errors, dict):
        errors = [errors]
    messages = []
    if isinstance(errors, list):
        for item in errors:
            if not isinstance(item, dict):
                continue
            message = (item.get("Message") or "").strip()
            detail = (item.get("Detail") or "").strip()
            code = (item.get("code") or item.get("Code") or "").strip()
            parts = [part for part in (message, detail) if part]
            text = " - ".join(parts) if parts else None
            if text and code:
                text = f"[{code}] {text}"
            elif code:
                text = f"[{code}]"
            if text:
                messages.append(text)
    if messages:
        return " | ".join(messages)
    return None


def build_response_error(response: Optional[requests.Response], default_message: str = "QuickBooks API request failed") -> str:
    if response is None:
        return default_message
    fault_text = _extract_fault_text(safe_json(response))
    if fault_text:
        return fault_text
    return f"{default_message} (HTTP {response.status_code})"


def attach_intuit_tid(detail_row: Dict[str, Any], *, intuit_tid: Optional[str] = None, response: Optional[requests.Response] = None) -> Dict[str, Any]:
    resolved_tid = (str(intuit_tid).strip() or None) if intuit_tid else extract_intuit_tid(response) or get_last_intuit_tid()
    if resolved_tid:
        detail_row[const.KEY_INTUIT_TID] = resolved_tid
    return detail_row


def add_response_metadata(
    detail_row: Dict[str, Any],
    *,
    response: Optional[requests.Response] = None,
    endpoint: Optional[str] = None,
    details: Optional[str] = None,
    include_response_body: bool = False,
) -> Dict[str, Any]:
    if response is not None:
        attach_intuit_tid(detail_row, response=response)
        detail_row[const.KEY_STATUS_CODE] = int(response.status_code)
        if include_response_body:
            response_body = truncate_text(getattr(response, "text", None))
            if response_body:
                detail_row[const.KEY_RESPONSE_BODY] = response_body
    if endpoint:
        detail_row[const.KEY_ENDPOINT] = endpoint
    if details:
        detail_row[const.KEY_DETAILS] = truncate_text(details)
    return detail_row


def mark_failed_detail(
    detail_row: Dict[str, Any],
    *,
    response: Optional[requests.Response] = None,
    error: Optional[str] = None,
    reason: Optional[str] = None,
    action: Optional[str] = None,
    endpoint: Optional[str] = None,
    details: Optional[str] = None,
    include_response_body: bool = False,
) -> Dict[str, Any]:
    detail_row[const.KEY_STATUS] = "failed"
    if action:
        detail_row[const.KEY_ACTION] = action
    if reason:
        detail_row[const.KEY_REASON] = reason
    detail_row[const.KEY_ERROR] = error or build_response_error(response)
    add_response_metadata(
        detail_row,
        response=response,
        endpoint=endpoint,
        details=details,
        include_response_body=include_response_body,
    )
    return detail_row


def append_failure(summary: Dict[str, Any], detail_row: Dict[str, Any]) -> None:
    attach_intuit_tid(detail_row)
    failure: Dict[str, Any] = {}
    for key in (
        const.KEY_STATUS,
        const.KEY_ACTION,
        const.KEY_REASON,
        const.KEY_ERROR,
        const.KEY_INTUIT_TID,
        const.KEY_STATUS_CODE,
        const.KEY_ENDPOINT,
        const.KEY_DETAILS,
        const.KEY_RESPONSE_BODY,
        const.KEY_FIELDCRAFT_VENDOR_ID,
        const.KEY_FIELDCRAFT_CUSTOMER_ID,
        const.KEY_FIELDCRAFT_ACCOUNT_ID,
        const.KEY_FIELDCRAFT_PAYMENT_ID,
        const.KEY_FIELDCRAFT_BILL_ID,
        const.KEY_QBO_VENDOR_ID,
        const.KEY_QBO_CUSTOMER_ID,
        const.KEY_QBO_ACCOUNT_ID,
        const.KEY_QBO_PAYMENT_ID,
        const.KEY_QBO_EMPLOYEE_ID,
        const.KEY_QBO_BILL_ID,
        const.KEY_DISPLAY_NAME,
        const.KEY_VENDOR_NAME,
        const.KEY_CUSTOMER_NAME,
        const.KEY_EMAIL,
        const.KEY_SYNC_TOKEN,
    ):
        value = detail_row.get(key)
        if value is not None:
            failure[key] = value

    for key, value in detail_row.items():
        if key not in failure and value is not None and key not in (const.KEY_RESULTS, const.KEY_FAILURES):
            failure[key] = value

    summary.setdefault(const.KEY_FAILURES, []).append(failure)


class QuickBooksApiRequestError(Exception):
    pass


def qbo_request(method: str, url: str, **kwargs: Any) -> requests.Response:
    kwargs.setdefault("timeout", 30)
    try:
        response = requests.request(method=method, url=url, **kwargs)
    except requests.RequestException as exc:
        raise QuickBooksApiRequestError(f"QuickBooks API request failed: {exc}") from exc
    extract_intuit_tid(response)
    return response


def qbo_get(url: str, **kwargs: Any) -> requests.Response:
    return qbo_request("GET", url, **kwargs)


def qbo_post(url: str, **kwargs: Any) -> requests.Response:
    return qbo_request("POST", url, **kwargs)


def prepare_json_request(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json_body: Optional[Dict[str, Any]] = None,
) -> requests.PreparedRequest:
    """Build the exact serialized HTTP request that will be sent to QuickBooks."""
    request = requests.Request(
        method=method,
        url=url,
        headers=headers,
        json=json_body,
    )
    return request.prepare()


def send_prepared_request(
    request: requests.PreparedRequest,
    *,
    timeout: int = 30,
) -> requests.Response:
    """Send a previously prepared request without rebuilding its payload."""
    try:
        with requests.Session() as session:
            environment = session.merge_environment_settings(
                request.url,
                proxies={},
                stream=None,
                verify=None,
                cert=None,
            )
            response = session.send(request, timeout=timeout, **environment)
    except requests.RequestException as exc:
        raise QuickBooksApiRequestError(f"QuickBooks API request failed: {exc}") from exc
    extract_intuit_tid(response)
    return response


def qbo_put(url: str, **kwargs: Any) -> requests.Response:
    return qbo_request("PUT", url, **kwargs)

def _safe_content_disposition_filename(file_name: Any) -> str:
    safe_name = _SAFE_FILENAME_PATTERN.sub("_", str(file_name or "")).strip()
    if not safe_name or safe_name in {".", ".."}:
        return "document"
    return safe_name
