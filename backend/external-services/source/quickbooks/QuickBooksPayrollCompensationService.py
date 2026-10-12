from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from source.Utils import QuickBooksErrorHandler as qbo_api

logger = logging.getLogger(__name__)


class QuickBooksPayrollCompensationService:
    """Resolve the payroll pay type used by W-2 TimeActivity exports."""

    GRAPHQL_ENDPOINT = "https://qb.api.intuit.com/graphql"
    DEFAULT_PAY_TYPE = "Regular Pay"
    MINOR_VERSION = 75

    EMPLOYEE_COMPENSATIONS_QUERY = """
    query GetEmployeeCompensations($filter: Payroll_EmployeeCompensationsFilter!) {
      payrollEmployeeCompensations(filter: $filter) {
        edges {
          node {
            id
            active
            employerCompensation {
              id
              name
              type {
                key
                description
                value
              }
            }
          }
        }
      }
    }
    """

    def __init__(self) -> None:
        self._compensation_cache: Dict[str, List[Dict[str, Any]]] = {}

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _headers(access_token: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _safe_headers(headers: Dict[str, Any]) -> Dict[str, Any]:
        return {
            str(key): (
                "<redacted>"
                if str(key).lower() in {"authorization", "cookie", "set-cookie"}
                else value
            )
            for key, value in headers.items()
        }

    @staticmethod
    def _token_key(access_token: str) -> str:
        return hashlib.sha256(access_token.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def _log_request(
        cls,
        *,
        operation: str,
        method: str,
        url: str,
        headers: Dict[str, Any],
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        logger.info(
            "\n[QBO_WORKFORCE_PAYROLL] >>> REQUEST\n"
            "operation: %s\n"
            "method: %s\n"
            "url: %s\n"
            "headers: %s\n"
            "payload_raw: %s\n"
            "[QBO_WORKFORCE_PAYROLL] >>> END REQUEST",
            operation,
            method,
            url,
            json.dumps(cls._safe_headers(headers), indent=2, sort_keys=True, default=str),
            json.dumps(payload, indent=2, sort_keys=True, default=str)
            if payload is not None
            else "null",
        )

    @classmethod
    def _log_response(cls, *, operation: str, response: Any) -> None:
        try:
            body = response.text
        except Exception:
            body = "<unable to read response body>"
        logger.info(
            "\n[QBO_WORKFORCE_PAYROLL] <<< QUICKBOOKS RAW RESPONSE\n"
            "operation: %s\n"
            "http_status: %s\n"
            "http_reason: %s\n"
            "url: %s\n"
            "response_headers_exact: %s\n"
            "response_body_raw: %s\n"
            "[QBO_WORKFORCE_PAYROLL] <<< END QUICKBOOKS RAW RESPONSE",
            operation,
            getattr(response, "status_code", None),
            getattr(response, "reason", ""),
            getattr(response, "url", ""),
            json.dumps(
                cls._safe_headers(dict(getattr(response, "headers", {}) or {})),
                indent=2,
                sort_keys=True,
                default=str,
            ),
            body,
        )

    @classmethod
    def _graphql_error_text(cls, errors: Any) -> str:
        if not isinstance(errors, list):
            return cls._clean(errors)
        return "; ".join(
            cls._clean(item.get("message"))
            for item in errors
            if isinstance(item, dict) and cls._clean(item.get("message"))
        )

    def get_active_compensations(
        self,
        *,
        access_token: str,
        qbo_employee_id: str,
    ) -> List[Dict[str, Any]]:
        employee_id = self._clean(qbo_employee_id)
        if not employee_id:
            raise ValueError("QuickBooks employee id is required for payroll compensation lookup.")

        cache_key = f"{self._token_key(access_token)}:{employee_id}"
        cached = self._compensation_cache.get(cache_key)
        if cached is not None:
            return list(cached)

        payload = {
            "query": self.EMPLOYEE_COMPENSATIONS_QUERY,
            "variables": {
                "filter": {
                    "employeeId": employee_id,
                    "active": True,
                }
            },
        }
        headers = self._headers(access_token)
        self._log_request(
            operation="payrollEmployeeCompensations",
            method="POST",
            url=self.GRAPHQL_ENDPOINT,
            headers=headers,
            payload=payload,
        )
        response = qbo_api.qbo_post(
            self.GRAPHQL_ENDPOINT,
            headers=headers,
            json=payload,
            timeout=30,
        )
        self._log_response(operation="payrollEmployeeCompensations", response=response)
        qbo_api.raise_for_qbo_response(
            response,
            fallback_message="Unable to read QuickBooks employee payroll compensation types.",
        )

        response_payload = qbo_api.safe_json(response)
        errors = response_payload.get("errors") or []
        if errors:
            detail = self._graphql_error_text(errors)
            raise ValueError(
                "QuickBooks payroll compensation access is unavailable"
                + (f": {detail}" if detail else ".")
                + " Ensure payroll.compensation.read is enabled for the Intuit app and reconnect QuickBooks."
            )

        edges = (
            ((response_payload.get("data") or {}).get("payrollEmployeeCompensations") or {}).get(
                "edges"
            )
            or []
        )
        rows = [
            dict(edge.get("node") or {})
            for edge in edges
            if isinstance(edge, dict) and isinstance(edge.get("node"), dict)
        ]
        active_rows = [
            row
            for row in rows
            if row.get("active") is not False and self._clean(row.get("id"))
        ]
        self._compensation_cache[cache_key] = active_rows
        return list(active_rows)

    @classmethod
    def _search_values(cls, row: Dict[str, Any]) -> List[str]:
        employer = row.get("employerCompensation") or {}
        type_info = employer.get("type") or {}
        values = (
            employer.get("name"),
            type_info.get("description"),
            type_info.get("value"),
            type_info.get("key"),
        )
        return [cls._clean(value) for value in values if cls._clean(value)]

    @classmethod
    def _label(cls, row: Dict[str, Any]) -> str:
        values = cls._search_values(row)
        return values[0] if values else cls._clean(row.get("id"))

    def resolve_regular_pay(
        self,
        *,
        access_token: str,
        qbo_employee_id: str,
    ) -> Dict[str, str]:
        employee_id = self._clean(qbo_employee_id)
        rows = self.get_active_compensations(
            access_token=access_token,
            qbo_employee_id=employee_id,
        )
        if not rows:
            raise ValueError(
                f"QuickBooks employee {employee_id} has no active payroll compensation/pay type. "
                "Add Regular Pay for this employee in QuickBooks Payroll and retry."
            )

        preferred = self.DEFAULT_PAY_TYPE.casefold()
        matches = [
            row
            for row in rows
            if any(value.casefold() == preferred for value in self._search_values(row))
        ]
        if len(matches) == 1:
            row = matches[0]
            # Intuit's Track time workflow requires payrollEmployeeCompensations
            # compensation.id (the employee compensation assignment) for
            # TimeActivity.PayrollItemRef. employerCompensation.id identifies the
            # company-level compensation definition and must not be used here.
            return {
                "id": self._clean(row.get("id")),
                "name": self._label(row),
            }
        if len(matches) > 1:
            raise ValueError(
                f"QuickBooks employee {employee_id} has multiple active '{self.DEFAULT_PAY_TYPE}' "
                "pay types. Keep one active Regular Pay compensation and retry."
            )

        available = ", ".join(
            sorted({self._label(row) for row in rows if self._label(row)})
        )
        raise ValueError(
            f"QuickBooks employee {employee_id} does not have an active '{self.DEFAULT_PAY_TYPE}' pay type. "
            + (f"Available pay types: {available}. " if available else "")
            + "Configure Regular Pay for the employee in QuickBooks Payroll and retry."
        )
