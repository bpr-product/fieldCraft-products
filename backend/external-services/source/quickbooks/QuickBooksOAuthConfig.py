"""Validated runtime configuration for QuickBooks Online OAuth 2.0.

The OAuth callback is handled by the external-integration backend. Authorization
codes and OAuth tokens must never be forwarded through the tenant web app.
"""
from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlencode, urlparse, urlunparse


_SUPPORTED_ENVIRONMENTS = {"sandbox", "production"}
_DEFAULT_TIMEOUT_SECONDS = 30


def normalize_qbo_environment(value: object) -> str:
    environment = str(value or "sandbox").strip().lower()
    if environment not in _SUPPORTED_ENVIRONMENTS:
        raise ValueError("QuickBooks environment must be 'sandbox' or 'production'.")
    return environment


def resolve_qbo_redirect_uri(environment: str) -> str:
    """Return and validate the backend OAuth callback URI for an environment.

    Environment-specific variables prevent sandbox and production app settings
    from being mixed accidentally.
    """
    environment = normalize_qbo_environment(environment)
    variable_name = (
        "QBO_PRODUCTION_REDIRECT_URI"
        if environment == "production"
        else "QBO_SANDBOX_REDIRECT_URI"
    )
    redirect_uri = (os.getenv(variable_name) or "").strip()
    if not redirect_uri:
        raise ValueError(
            f"{variable_name} must be configured with the public backend QuickBooks callback URL."
        )

    parsed = urlparse(redirect_uri)
    if not parsed.scheme or not parsed.hostname:
        raise ValueError(f"{variable_name} must be an absolute URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            f"{variable_name} must not contain credentials, query parameters, or a fragment."
        )
    if parsed.path.rstrip("/") != "/extsvc/api/v1/qbo/callback":
        raise ValueError(
            f"{variable_name} must point to /extsvc/api/v1/qbo/callback on the backend."
        )

    hostname = parsed.hostname.lower()
    is_localhost = hostname in {"localhost", "127.0.0.1", "::1"}

    if environment == "production":
        if parsed.scheme.lower() != "https":
            raise ValueError("The production QuickBooks redirect URI must use HTTPS.")
        if is_localhost:
            raise ValueError("The production QuickBooks redirect URI cannot use localhost.")
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            raise ValueError("The production QuickBooks redirect URI must use a DNS hostname, not an IP address.")
    elif parsed.scheme.lower() != "https" and not (
        parsed.scheme.lower() == "http" and is_localhost
    ):
        raise ValueError("The sandbox QuickBooks redirect URI must use HTTPS or HTTP localhost.")

    return redirect_uri



_FRONTEND_RETURN_RESULTS = {
    "success",
    "denied",
    "incomplete",
    "invalid_state",
    "reconnect",
    "failed",
}
_FRONTEND_RETURN_PROVIDERS = {"accounting", "time"}


def resolve_quickbooks_frontend_return_uri() -> str:
    """Return the frontend-only landing route used after backend OAuth processing.

    Intuit still redirects to the backend callback. After the backend validates the
    callback and stores the connection, it redirects the popup to this React route.
    No authorization code, state, realm ID, or token is forwarded to the frontend.
    """
    variable_name = "QUICKBOOKS_OAUTH_FRONTEND_RETURN_URI"
    return_uri = (os.getenv(variable_name) or "").strip()
    if not return_uri:
        raise ValueError(
            f"{variable_name} must be configured with the public frontend QuickBooks OAuth return URL."
        )

    parsed = urlparse(return_uri)
    if not parsed.scheme or not parsed.hostname:
        raise ValueError(f"{variable_name} must be an absolute URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            f"{variable_name} must not contain credentials, query parameters, or a fragment."
        )
    if parsed.path.rstrip("/") != "/quickbooks/oauth-return":
        raise ValueError(
            f"{variable_name} must point to /quickbooks/oauth-return on the frontend."
        )

    hostname = parsed.hostname.lower()
    is_localhost = hostname in {"localhost", "127.0.0.1", "::1"}
    runtime_environment = str(
        os.getenv("APP_ENV")
        or os.getenv("DEPLOYMENT_ENV")
        or os.getenv("ENVIRONMENT")
        or ""
    ).strip().lower()
    is_production = runtime_environment in {"production", "prod"}

    if is_production:
        if parsed.scheme.lower() != "https":
            raise ValueError("The production QuickBooks frontend return URI must use HTTPS.")
        if is_localhost:
            raise ValueError("The production QuickBooks frontend return URI cannot use localhost.")
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            raise ValueError(
                "The production QuickBooks frontend return URI must use a DNS hostname, not an IP address."
            )
    elif parsed.scheme.lower() != "https" and not (
        parsed.scheme.lower() == "http" and is_localhost
    ):
        raise ValueError(
            "The QuickBooks frontend return URI must use HTTPS or HTTP localhost outside production."
        )

    return return_uri


def build_quickbooks_frontend_return_url(*, provider: str, result: str) -> str:
    """Build the safe React landing URL for a completed OAuth popup flow."""
    normalized_provider = str(provider or "").strip().lower()
    normalized_result = str(result or "").strip().lower()
    if normalized_provider not in _FRONTEND_RETURN_PROVIDERS:
        raise ValueError("Unsupported QuickBooks OAuth provider result.")
    if normalized_result not in _FRONTEND_RETURN_RESULTS:
        raise ValueError("Unsupported QuickBooks OAuth result.")

    return_uri = resolve_quickbooks_frontend_return_uri()
    parsed = urlparse(return_uri)
    query = urlencode({"provider": normalized_provider, "result": normalized_result})
    return urlunparse(parsed._replace(query=query))

def qbo_oauth_timeout_seconds() -> int:
    raw = (os.getenv("QBO_OAUTH_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return _DEFAULT_TIMEOUT_SECONDS
    try:
        timeout = int(raw)
    except ValueError as exc:
        raise ValueError("QBO_OAUTH_TIMEOUT_SECONDS must be an integer.") from exc
    if timeout < 5 or timeout > 120:
        raise ValueError("QBO_OAUTH_TIMEOUT_SECONDS must be between 5 and 120 seconds.")
    return timeout


def resolve_qbtime_redirect_uri() -> str:
    """Return and validate the backend callback URI used by QuickBooks Time."""
    variable_name = "QBTIME_REDIRECT_URI"
    redirect_uri = (os.getenv(variable_name) or "").strip()
    if not redirect_uri:
        raise ValueError(
            f"{variable_name} must be configured with the public backend QuickBooks Time callback URL."
        )

    parsed = urlparse(redirect_uri)
    if not parsed.scheme or not parsed.hostname:
        raise ValueError(f"{variable_name} must be an absolute URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            f"{variable_name} must not contain credentials, query parameters, or a fragment."
        )
    if parsed.path.rstrip("/") != "/extsvc/api/v1/quickbooks-time/callback":
        raise ValueError(
            f"{variable_name} must point to /extsvc/api/v1/quickbooks-time/callback on the backend."
        )

    hostname = parsed.hostname.lower()
    is_localhost = hostname in {"localhost", "127.0.0.1", "::1"}
    runtime_environment = str(
        os.getenv("APP_ENV")
        or os.getenv("DEPLOYMENT_ENV")
        or os.getenv("ENVIRONMENT")
        or ""
    ).strip().lower()
    is_production = runtime_environment in {"production", "prod"}

    if is_production:
        if parsed.scheme.lower() != "https":
            raise ValueError("The production QuickBooks Time redirect URI must use HTTPS.")
        if is_localhost:
            raise ValueError("The production QuickBooks Time redirect URI cannot use localhost.")
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            raise ValueError(
                "The production QuickBooks Time redirect URI must use a DNS hostname, not an IP address."
            )
    elif parsed.scheme.lower() != "https" and not (
        parsed.scheme.lower() == "http" and is_localhost
    ):
        raise ValueError(
            "The QuickBooks Time redirect URI must use HTTPS or HTTP localhost outside production."
        )

    return redirect_uri


def qbtime_oauth_timeout_seconds() -> int:
    raw = (os.getenv("QBTIME_OAUTH_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return _DEFAULT_TIMEOUT_SECONDS
    try:
        timeout = int(raw)
    except ValueError as exc:
        raise ValueError("QBTIME_OAUTH_TIMEOUT_SECONDS must be an integer.") from exc
    if timeout < 5 or timeout > 120:
        raise ValueError("QBTIME_OAUTH_TIMEOUT_SECONDS must be between 5 and 120 seconds.")
    return timeout
