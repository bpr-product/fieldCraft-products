"""QuickBooks Online OAuth scope configuration.

The FIELDCRAFT QuickBooks integration uses three API families:

* Accounting REST API for customers, projects/sub-customers, employees,
  vendors, services, bills, and related accounting entities.
* Project Management GraphQL API for native QuickBooks Projects.
* Payroll Compensation GraphQL API to read employee pay types.

QuickBooks Time uses its own OAuth application and does not use these QBO
scopes. Keep the two authorization flows separate.
"""
from __future__ import annotations

import os
from typing import Iterable, List

QBO_ACCOUNTING_SCOPE = "com.intuit.quickbooks.accounting"
QBO_PROJECT_SCOPE = "project-management.project"
QBO_PAYROLL_COMPENSATION_SCOPE = "payroll.compensation.read"

REQUIRED_QBO_SCOPES = (
    QBO_ACCOUNTING_SCOPE,
    QBO_PROJECT_SCOPE,
    QBO_PAYROLL_COMPENSATION_SCOPE,
)

def _split_scopes(value: str | Iterable[str] | None) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = value.replace(",", " ").split()
    else:
        values = [str(item) for item in value]
    result: List[str] = []
    seen = set()
    for value_item in values:
        scope = str(value_item or "").strip()
        if not scope or scope in seen:
            continue
        seen.add(scope)
        result.append(scope)
    return result


def resolve_qbo_scopes(configured: str | Iterable[str] | None = None) -> List[str]:
    """Return the final QBO scope list in a deterministic order.

    Required integration scopes are always included. Operators may add OIDC or
    future approved scopes through ``QBO_SCOPES`` without accidentally removing
    the scopes needed by the current FIELDCRAFT implementation.
    """
    configured_scopes = _split_scopes(
        configured if configured is not None else os.getenv("QBO_SCOPES")
    )
    final_scopes: List[str] = []
    seen = set()
    for scope in (*REQUIRED_QBO_SCOPES, *configured_scopes):
        if scope in seen:
            continue
        seen.add(scope)
        final_scopes.append(scope)
    return final_scopes


def resolve_qbo_scope_text(configured: str | Iterable[str] | None = None) -> str:
    return " ".join(resolve_qbo_scopes(configured))
