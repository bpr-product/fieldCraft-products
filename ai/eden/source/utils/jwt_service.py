import logging
import os
import time
from typing import Optional
from urllib.parse import urlparse

import httpx
from jose import jwt
from jose.exceptions import JOSEError

logger = logging.getLogger(__name__)


def _split_csv(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


# Multi-tenant B2C config -- the SAME env vars every other service on this VM
# uses (src/guardrails, src/bpe, src/vllm-proxy's bearer_token_guard.py),
# reused as-is rather than a divergent EDEN-only scheme. A token's `iss`
# claim selects which configured tenant's JWKS applies (see
# _config_for_issuer), so this validates across every configured B2C tenant,
# not just one.
AUTH_OPENID_CONFIG_URLS = _split_csv(os.getenv("AUTH_OPENID_CONFIG_URLS"))
AUTH_AUDIENCES = _split_csv(os.getenv("AUTH_AUDIENCES"))

# Nonprod multi-environment scoping (see nonprod-multi-env-scoping skill).
# Row-matched against AUTH_AUDIENCES: entry N here is the environment name for
# entry N in AUTH_AUDIENCES. Empty/missing entries resolve to None (unmapped),
# which callers must treat as fail-closed -- never guess an environment.
_AUTH_AUDIENCE_ENVIRONMENTS = _split_csv(os.getenv("AUTH_AUDIENCE_ENVIRONMENTS"))
_AUDIENCE_TO_ENVIRONMENT = dict(zip(AUTH_AUDIENCES, _AUTH_AUDIENCE_ENVIRONMENTS))


def resolve_environment(claims: dict) -> Optional[str]:
    """Which nonprod sub-environment (dev/qa/stag/sb/dm) issued this token,
    derived from the same `aud` claim validate_token already verified --
    never a client-supplied header/param, never tenant_id (a different axis:
    which customer, not which deploy stage). Returns None when the token's
    audience has no configured mapping -- callers must fail closed on None,
    not default to any particular environment."""
    token_aud = claims.get("aud")
    auds = token_aud if isinstance(token_aud, list) else [token_aud]
    for aud in auds:
        env = _AUDIENCE_TO_ENVIRONMENT.get(aud)
        if env:
            return env
    return None

_OPENID_CONFIG_CACHE_TTL_SECONDS = 3600
_JWKS_CACHE_TTL_SECONDS = 3600
_openid_config_cache: dict = {}
_jwks_cache: dict = {}


class TokenValidationError(Exception):
    """Raised for any invalid/expired/untrusted bearer token. Callers must
    treat this as 401 -- never fall back to a client-supplied identity."""


def _fetch_openid_config(url: str) -> dict:
    resp = httpx.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _openid_config(url: str) -> dict:
    """Cached OpenID discovery fetch, one entry per configured tenant URL."""
    now = time.time()
    cached = _openid_config_cache.get(url)
    if cached and now - cached["fetched_at"] < _OPENID_CONFIG_CACHE_TTL_SECONDS:
        return cached["config"]
    config = _fetch_openid_config(url)
    _openid_config_cache[url] = {"config": config, "fetched_at": now}
    return config


def _config_for_issuer(issuer: str) -> Optional[dict]:
    """Finds the OpenID config whose `issuer` matches the token's `iss`.
    Tries the host-matched URL(s) first -- same reasoning as the shared
    bearer_token_guard.py: an unreachable/slow tenant elsewhere in the
    allow-list must not delay or break requests for a different, healthy
    tenant -- then falls back to the rest, skipping any that error out."""
    try:
        iss_host = urlparse(issuer).hostname
    except ValueError:
        iss_host = None

    primary: list[str] = []
    secondary: list[str] = []
    for url in AUTH_OPENID_CONFIG_URLS:
        try:
            url_host = urlparse(url).hostname
        except ValueError:
            url_host = None
        (primary if iss_host and url_host == iss_host else secondary).append(url)

    for url in primary + secondary:
        try:
            config = _openid_config(url)
        except (httpx.HTTPError, ValueError) as e:
            logger.warning(f"OpenID config fetch failed for {url}: {e}")
            continue
        if config.get("issuer") == issuer:
            return config
    return None


def _fetch_jwks(jwks_uri: str, force: bool = False) -> list[dict]:
    """Cached JWKS fetch per tenant. `force=True` re-fetches once on an
    unknown `kid` (key rotation) instead of failing every request until the
    TTL expires."""
    now = time.time()
    cached = _jwks_cache.get(jwks_uri)
    if not force and cached and now - cached["fetched_at"] < _JWKS_CACHE_TTL_SECONDS:
        return cached["keys"]

    resp = httpx.get(jwks_uri, timeout=10)
    resp.raise_for_status()
    keys = resp.json().get("keys", [])
    _jwks_cache[jwks_uri] = {"keys": keys, "fetched_at": now}
    return keys


def _signing_key_for(token: str, jwks_uri: str) -> dict:
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")

    keys = _fetch_jwks(jwks_uri)
    key = next((k for k in keys if k.get("kid") == kid), None)
    if key is None:
        # Key rotation: unknown kid isn't necessarily invalid, re-fetch once.
        keys = _fetch_jwks(jwks_uri, force=True)
        key = next((k for k in keys if k.get("kid") == kid), None)
    if key is None:
        raise TokenValidationError(f"No matching JWKS key for kid={kid!r}")
    return key


def validate_token(token: str) -> dict:
    """Verifies signature (RS256 via JWKS), audience, issuer, expiry, and
    not-before, across all configured B2C tenants (AUTH_OPENID_CONFIG_URLS).
    A token's `iss` claim selects which tenant's JWKS to verify against --
    rejected if `iss` doesn't match any configured tenant, or if `aud`
    doesn't match any configured audience. Returns the decoded claims on
    success. Raises TokenValidationError on any failure -- this is the ONLY
    trusted source of identity; nothing downstream may substitute a
    client-supplied value for the oid/tenant claims this returns."""
    try:
        unverified = jwt.get_unverified_claims(token)
    except JOSEError as e:
        raise TokenValidationError(f"Malformed token: {e}") from e

    issuer = unverified.get("iss")
    if not issuer:
        raise TokenValidationError("Token missing iss")
    tenant_config = _config_for_issuer(str(issuer))
    if tenant_config is None:
        raise TokenValidationError(f"Untrusted token issuer: {issuer}")

    token_aud = unverified.get("aud")
    if isinstance(token_aud, list):
        matched_aud = next((a for a in token_aud if a in AUTH_AUDIENCES), None)
    else:
        matched_aud = token_aud if token_aud in AUTH_AUDIENCES else None
    if matched_aud is None:
        raise TokenValidationError("Token audience not allowed")

    try:
        signing_key = _signing_key_for(token, tenant_config["jwks_uri"])
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=matched_aud,
            issuer=tenant_config["issuer"],
        )
    except JOSEError as e:
        raise TokenValidationError(str(e)) from e
    except httpx.HTTPError as e:
        raise TokenValidationError(f"JWKS fetch failed: {e}") from e

    if not claims.get("oid"):
        raise TokenValidationError("Token missing required 'oid' claim")

    return claims
