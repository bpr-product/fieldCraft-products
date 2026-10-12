"""Intuit OpenID Connect Discovery Client.

Fetches the Intuit OIDC discovery document once and caches it for 24 hours.
All QBO OAuth endpoints are derived from this document instead of being
hardcoded or stored in environment variables.

Discovery document URLs (Intuit documentation):
  Production : https://developer.api.intuit.com/.well-known/openid_configuration
  Sandbox    : https://developer.api.intuit.com/.well-known/openid_sandbox_configuration

QBO data API base URLs are NOT in the discovery document — they are fixed,
well-known Intuit constants documented at:
  https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/account
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Discovery document URLs ────────────────────────────────────────────────────
_DISCOVERY_URL_PRODUCTION = (
    "https://developer.api.intuit.com/.well-known/openid_configuration"
)
_DISCOVERY_URL_SANDBOX = (
    "https://developer.api.intuit.com/.well-known/openid_sandbox_configuration"
)

# ── QBO data API base URLs (fixed — not in the discovery document) ─────────────
_QBO_API_BASE_PRODUCTION = os.getenv("QBO_API_BASE_PRODUCTION", "https://quickbooks.api.intuit.com")
_QBO_API_BASE_SANDBOX = os.getenv("QBO_API_BASE_SANDBOX", "https://sandbox-quickbooks.api.intuit.com")

# ── Fallback endpoints ─────────────────────────────────────────────────────────
# Used if the discovery document is temporarily unreachable.
# Values are read from ENV (see .env); hardcoded strings here are last-resort defaults only.
_FALLBACK: Dict[str, str] = {
    "authorization_endpoint": os.getenv("QBO_FALLBACK_AUTH_URL", "https://appcenter.intuit.com/connect/oauth2"),
    "token_endpoint": os.getenv("QBO_FALLBACK_TOKEN_URL", "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"),
    "revocation_endpoint": os.getenv("QBO_FALLBACK_REVOCATION_URL", "https://developer.api.intuit.com/v2/oauth2/tokens/revoke"),
}

_CACHE_TTL_SECONDS = 86_400  # 24 hours
_REQUEST_TIMEOUT = 10         # seconds


class _DiscoveryCache:
    """Thread-safe single-document cache with TTL."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._document: Optional[Dict[str, Any]] = None
        self._fetched_at: float = 0.0

    def get(self, url: str) -> Dict[str, Any]:
        with self._lock:
            if self._document is None or (time.monotonic() - self._fetched_at) >= _CACHE_TTL_SECONDS:
                self._document = self._fetch(url)
                self._fetched_at = time.monotonic()
            return self._document

    @staticmethod
    def _fetch(url: str) -> Dict[str, Any]:
        response = requests.get(url, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()
        doc = response.json()
        if not isinstance(doc, dict):
            raise ValueError(f"Unexpected discovery document format: {type(doc)}")
        logger.info("[QBODiscovery] Fetched discovery document from %s", url)
        return doc


_prod_cache = _DiscoveryCache()
_sandbox_cache = _DiscoveryCache()


def _is_production(env: str) -> bool:
    return (env or "sandbox").strip().lower() == "production"


def _get_document(env: str) -> Dict[str, Any]:
    if _is_production(env):
        return _prod_cache.get(_DISCOVERY_URL_PRODUCTION)
    return _sandbox_cache.get(_DISCOVERY_URL_SANDBOX)


def _get_endpoint(env: str, key: str) -> str:
    """Return an OAuth endpoint, falling back to known values if discovery fails."""
    try:
        value = _get_document(env).get(key)
        if value:
            return str(value)
    except Exception:
        logger.warning(
            "[QBODiscovery] Discovery document unavailable for env=%s; using fallback for %s",
            env, key,
        )
    return _FALLBACK[key]


# ── Public API ─────────────────────────────────────────────────────────────────

def get_authorization_endpoint(env: str = "sandbox") -> str:
    """Return the OAuth 2.0 authorization endpoint from the Intuit discovery document."""
    return _get_endpoint(env, "authorization_endpoint")


def get_token_endpoint(env: str = "sandbox") -> str:
    """Return the OAuth 2.0 token endpoint from the Intuit discovery document."""
    return _get_endpoint(env, "token_endpoint")


def get_revocation_endpoint(env: str = "sandbox") -> str:
    """Return the OAuth 2.0 token revocation endpoint from the Intuit discovery document."""
    return _get_endpoint(env, "revocation_endpoint")


def get_qbo_api_base(env: str = "sandbox") -> str:
    """Return the QBO data API base URL for the given environment.

    These are fixed Intuit constants — not sourced from the discovery document.
    """
    if _is_production(env):
        return _QBO_API_BASE_PRODUCTION
    return _QBO_API_BASE_SANDBOX
