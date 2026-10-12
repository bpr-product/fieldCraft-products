"""Generic bearer-token authentication for protected API routes."""

import os
from functools import lru_cache
from typing import Any, Dict, Optional

import httpx
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt


_bearer = HTTPBearer(auto_error=False)


def _is_enabled(value: Optional[str]) -> bool:
    return str(value or "false").strip().lower() in {"1", "true", "yes", "on"}


def _split_csv(value: Optional[str]) -> list:
    """Split a comma-separated env value into a clean list."""
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


class AuthService:
    """Validates bearer tokens via OpenID Connect discovery + JWKS.

    Multi-tenant aware: accepts tokens issued by any of the configured B2C
    tenants. The token's ``iss`` claim selects the matching JWKS for signature
    verification. The ``aud`` claim must match one of the configured audiences.

    Env vars (plural takes precedence; singular kept for backwards compat):
      AUTH_OPENID_CONFIG_URLS  CSV of OpenID discovery URLs
      AUTH_OPENID_CONFIG_URL   single URL fallback
      AUTH_AUDIENCES           CSV of accepted API audiences
      AUTH_AUDIENCE            single audience fallback
    """

    def __init__(self) -> None:
        self.enabled = _is_enabled(os.getenv("AUTH_ENABLED"))
        self.audiences = _split_csv(
            os.getenv("AUTH_AUDIENCES") or os.getenv("AUTH_AUDIENCE")
        )
        self.openid_config_urls = _split_csv(
            os.getenv("AUTH_OPENID_CONFIG_URLS") or os.getenv("AUTH_OPENID_CONFIG_URL")
        )
        self.tenant_id = os.getenv("AUTH_TENANT_ID") or os.getenv("TenantID")

    def validate_configuration(self) -> None:
        if not self.enabled:
            return
        missing = []
        if not self.audiences:
            missing.append("AUTH_AUDIENCES")
        if not self.openid_config_urls:
            missing.append("AUTH_OPENID_CONFIG_URLS")
        if missing:
            raise HTTPException(
                status_code=500,
                detail=(
                    "Server authentication configuration is incomplete: "
                    + ", ".join(missing)
                ),
            )

    def _config_for_issuer(self, issuer: str) -> Optional[Dict[str, Any]]:
        """Find the OpenID config whose ``issuer`` matches the token's ``iss``.

        Only fetches the discovery URL whose host matches the token's iss host,
        so an unreachable or slow B2C tenant elsewhere in the allow-list cannot
        time out unrelated requests. Falls back to the remaining URLs (skipping
        any that error out) if the host-matched probe doesn't resolve.
        """
        from urllib.parse import urlparse

        try:
            iss_host = urlparse(issuer).hostname
        except Exception:
            iss_host = None

        primary: list = []
        secondary: list = []
        for url in self.openid_config_urls:
            try:
                url_host = urlparse(url).hostname
            except Exception:
                url_host = None
            if iss_host and url_host == iss_host:
                primary.append(url)
            else:
                secondary.append(url)

        for url in primary + secondary:
            try:
                cfg = _openid_config(url)
            except Exception:
                continue
            if cfg.get("issuer") == issuer:
                return cfg
        return None

    def pick_key(self, token: str, jwks_uri: str) -> Dict[str, Any]:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            raise HTTPException(status_code=401, detail="Token missing kid")

        for key in _jwks(jwks_uri).get("keys", []):
            if key.get("kid") == kid:
                return key

        _jwks.cache_clear()
        for key in _jwks(jwks_uri).get("keys", []):
            if key.get("kid") == kid:
                return key

        raise HTTPException(status_code=401, detail="Signing key not found")

    def verify(self, credentials: Optional[HTTPAuthorizationCredentials]) -> Dict[str, Any]:
        # C.1 — auth is mandatory. The previous `if not self.enabled: return {}`
        # bypass has been removed. If AUTH_ENABLED is not true the service must
        # refuse to start (see start.py lifespan check); reaching here in that
        # state indicates a misconfigured deployment.
        self.validate_configuration()

        if not self.enabled:
            raise HTTPException(
                status_code=500,
                detail="Authentication is disabled at runtime — refusing to serve",
            )

        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Missing or invalid bearer token")

        token = credentials.credentials

        try:
            unverified = jwt.get_unverified_claims(token)
        except JWTError as exc:
            raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc

        token_iss = unverified.get("iss")
        if not token_iss:
            raise HTTPException(status_code=401, detail="Token missing iss")
        tenant_cfg = self._config_for_issuer(str(token_iss))
        if tenant_cfg is None:
            raise HTTPException(
                status_code=401,
                detail=f"Untrusted token issuer: {token_iss}",
            )

        # python-jose's jwt.decode requires audience to be a single string.
        # Match the token's aud against the configured allow-list ourselves, then
        # pass the matched value through for verify_aud to confirm.
        token_aud = unverified.get("aud")
        if isinstance(token_aud, list):
            matched_aud = next((a for a in token_aud if a in self.audiences), None)
        elif isinstance(token_aud, str):
            matched_aud = token_aud if token_aud in self.audiences else None
        else:
            matched_aud = None
        if matched_aud is None:
            raise HTTPException(status_code=401, detail="Token audience not allowed")

        try:
            payload = jwt.decode(
                token,
                self.pick_key(token, tenant_cfg["jwks_uri"]),
                algorithms=["RS256"],
                issuer=tenant_cfg["issuer"],
                audience=matched_aud,
                options={
                    "verify_signature": True,
                    "verify_iss": True,
                    "verify_aud": True,
                    "verify_exp": True,
                },
            )
        except JWTError as exc:
            raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc

        if self.tenant_id and payload.get("tid") != self.tenant_id:
            raise HTTPException(status_code=401, detail="Invalid token tenant")

        # C.2 — Normalize claims so callers can rely on a stable shape.
        # tid → tenant_id, oid → user_oid, emails → email (or upn fallback).
        payload.setdefault("tenant_id", payload.get("tid"))
        payload.setdefault("user_oid", payload.get("oid"))
        payload.setdefault(
            "email",
            payload.get("emails") or payload.get("upn") or payload.get("preferred_username"),
        )
        return payload


@lru_cache(maxsize=16)
def _openid_config(url: str) -> Dict[str, Any]:
    response = httpx.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


@lru_cache(maxsize=16)
def _jwks(url: str) -> Dict[str, Any]:
    response = httpx.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


@lru_cache(maxsize=1)
def get_auth_service() -> AuthService:
    return AuthService()


def verify_bearer_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Dict[str, Any]:
    return get_auth_service().verify(credentials)
