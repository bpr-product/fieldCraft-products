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


class AuthService:
    """Validates bearer tokens using OpenID Connect discovery and JWKS."""

    def __init__(self) -> None:
        runtime_environment = str(
            os.getenv("APP_ENV")
            or os.getenv("DEPLOYMENT_ENV")
            or os.getenv("ENVIRONMENT")
            or ""
        ).strip().lower()
        self.enabled = _is_enabled(os.getenv("AUTH_ENABLED")) or runtime_environment in {
            "production", "prod"
        }
        self.audience = os.getenv("AUTH_AUDIENCE")
        self.openid_config_url = os.getenv("AUTH_OPENID_CONFIG_URL")
        self.tenant_id = os.getenv("AUTH_TENANT_ID") or os.getenv("TenantID")

    def validate_configuration(self) -> None:
        if not self.enabled:
            return

        missing = [
            name
            for name, value in {
                "AUTH_AUDIENCE": self.audience,
                "AUTH_OPENID_CONFIG_URL": self.openid_config_url,
            }.items()
            if not value
        ]

        if missing:
            raise HTTPException(
                status_code=500,
                detail=f"Server authentication configuration is incomplete: {', '.join(missing)}",
            )

    def openid_config(self) -> Dict[str, Any]:
        self.validate_configuration()
        return _openid_config(str(self.openid_config_url))

    def jwks(self) -> Dict[str, Any]:
        return _jwks(self.openid_config()["jwks_uri"])

    def pick_key(self, token: str) -> Dict[str, Any]:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")

        if not kid:
            raise HTTPException(status_code=401, detail="Token missing kid")

        for key in self.jwks().get("keys", []):
            if key.get("kid") == kid:
                return key

        _jwks.cache_clear()

        for key in self.jwks().get("keys", []):
            if key.get("kid") == kid:
                return key

        raise HTTPException(status_code=401, detail="Signing key not found")

    def verify(self, credentials: Optional[HTTPAuthorizationCredentials]) -> Dict[str, Any]:
        self.validate_configuration()

        if not self.enabled:
            return {}

        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Missing or invalid bearer token")

        token = credentials.credentials

        try:
            # Keep the accepted JWT algorithm explicit to prevent algorithm-confusion attacks.
            payload = jwt.decode(
                token,
                self.pick_key(token),
                algorithms=["RS256"],
                issuer=self.openid_config()["issuer"],
                audience=self.audience,
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

        return payload


@lru_cache(maxsize=1)
def _openid_config(url: str) -> Dict[str, Any]:
    response = httpx.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


@lru_cache(maxsize=1)
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
