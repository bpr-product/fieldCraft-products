"""Local-only QuickBooks production credential override.

This helper is intentionally narrow: it lets a developer run the normal local
FIELDCRAFT sync flow against a production QuickBooks company token generated through
OAuth Playground, without changing tenant DB rows or committing secrets.

Never enable this in deployed environments.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from source.Utils import constants as const

_ENABLED_ENV = "QBO_PROD_TEST_ENABLED"
_SENTINEL_KEY = "__qboLocalProductionOverride"
_TOKEN_FILE_ENV = "QBO_PROD_TEST_ROTATED_REFRESH_TOKEN_FILE"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_LOCAL_ENV_VALUES = {"local", "dev", "development", "test", "testing"}


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in _TRUE_VALUES


def _runtime_env() -> str:
    return (
        _env("APP_ENV")
        or _env("ENV")
        or _env("ENVIRONMENT")
        or _env("STAGE")
        or _env("FASTAPI_ENV")
    ).lower()


def is_enabled() -> bool:
    """Return True only for an explicitly enabled local or test runtime."""
    return _env_bool(_ENABLED_ENV) and _runtime_env() in _LOCAL_ENV_VALUES


def validate_required() -> None:
    """Fail fast when override is enabled but required secrets are missing."""
    if not is_enabled():
        return

    required = (
        "QBO_PROD_TEST_CLIENT_ID",
        "QBO_PROD_TEST_CLIENT_SECRET",
        "QBO_PROD_TEST_REALM_ID",
        "QBO_PROD_TEST_REFRESH_TOKEN",
    )
    missing = [name for name in required if not _env(name)]
    if missing:
        raise RuntimeError(
            "QuickBooks production test override is enabled, but these env vars are missing: "
            + ", ".join(missing)
        )


def enabled_marker() -> str:
    return _SENTINEL_KEY


def effective_env(default: str = "sandbox") -> str:
    """Return the QuickBooks environment after applying the local production override."""
    if is_enabled():
        return "production"
    return (default or "sandbox").strip().lower()


def effective_token_url(default_token_url: str = "") -> str:
    """Return a safe token URL, honoring the local override only when it has a value."""
    override = _env("QBO_PROD_TEST_TOKEN_URL")
    if is_enabled() and override:
        return override
    return default_token_url


def connection_has_override(connection: Optional[Dict[str, Any]]) -> bool:
    return bool(isinstance(connection, dict) and connection.get(_SENTINEL_KEY))


def apply_connection_override(connection: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Overlay production realm/refresh token for local testing only.

    The returned dict carries a sentinel so repository writes can strip the
    override before persisting anything back to service_subscriber.additional_info.
    """
    payload = dict(connection or {})
    if not is_enabled():
        return payload

    validate_required()

    payload[const.KEY_REFRESH_TOKEN] = _env("QBO_PROD_TEST_REFRESH_TOKEN")
    payload[const.KEY_REALM_ID] = _env("QBO_PROD_TEST_REALM_ID")
    payload[const.KEY_ENV] = "production"
    payload[const.KEY_STATUS] = const.STATUS_CONNECTED
    payload[const.KEY_RECONNECT_REQUIRED] = False
    payload[const.KEY_AUTH_ERROR] = None
    payload[_SENTINEL_KEY] = True
    return payload


def apply_config_override(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Overlay production client credentials for local testing only."""
    payload = dict(config or {})
    if not is_enabled():
        return payload

    validate_required()

    payload[const.KEY_CLIENT_ID] = _env("QBO_PROD_TEST_CLIENT_ID")
    payload[const.KEY_CLIENT_SECRET] = _env("QBO_PROD_TEST_CLIENT_SECRET")
    payload["Env"] = "production"
    payload[const.KEY_ENV] = "production"
    return payload


def apply_oauth_inputs(
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    token_url: str,
) -> Tuple[str, str, str, str]:
    """Return OAuth inputs with local production overrides applied."""
    if not is_enabled():
        return client_id, client_secret, refresh_token, token_url

    validate_required()

    return (
        _env("QBO_PROD_TEST_CLIENT_ID"),
        _env("QBO_PROD_TEST_CLIENT_SECRET"),
        _env("QBO_PROD_TEST_REFRESH_TOKEN"),
        _env("QBO_PROD_TEST_TOKEN_URL", token_url),
    )


def strip_override_before_persist(
    additional_info: Optional[Dict[str, Any]],
    stored_additional_info: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Remove local override secrets before repository writes to the database.

    If an update starts from an overridden connection, restore the original stored
    connection secrets/status while preserving non-secret updates such as
    lastSyncAt, capabilities, and sync configuration.
    """
    payload = dict(additional_info or {})
    if not payload.pop(_SENTINEL_KEY, False):
        return payload

    stored = dict(stored_additional_info or {})
    restore_keys = (
        const.KEY_REFRESH_TOKEN,
        const.KEY_ACCESS_TOKEN,
        const.KEY_REALM_ID,
        const.KEY_ENV,
        const.KEY_REFRESH_EXPIRES_AT,
        const.KEY_ACCESS_EXPIRES_AT,
        const.KEY_STATUS,
        const.KEY_RECONNECT_REQUIRED,
        const.KEY_AUTH_ERROR,
        "connectedAt",
    )

    for key in restore_keys:
        if key in stored:
            payload[key] = stored.get(key)
        else:
            payload.pop(key, None)

    return payload


def store_rotated_refresh_token(rotated_refresh_token: Optional[str]) -> None:
    """Optionally store rotated refresh token in a local-only file.

    This is disabled unless QBO_PROD_TEST_ROTATED_REFRESH_TOKEN_FILE is set.
    The token is never printed to logs.
    """
    token = (rotated_refresh_token or "").strip()
    path_value = _env(_TOKEN_FILE_ENV)
    if not token or not is_enabled() or not path_value:
        return

    path = Path(path_value).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as token_file:
        token_file.write(token)


def public_status() -> Dict[str, Any]:
    return {
        "enabled": is_enabled(),
        "runtimeEnv": _runtime_env() or "unknown",
        "realmIdOverridePresent": bool(_env("QBO_PROD_TEST_REALM_ID")),
        "refreshTokenOverridePresent": bool(_env("QBO_PROD_TEST_REFRESH_TOKEN")),
        "clientIdOverridePresent": bool(_env("QBO_PROD_TEST_CLIENT_ID")),
        "clientSecretOverridePresent": bool(_env("QBO_PROD_TEST_CLIENT_SECRET")),
        "rotatedRefreshTokenFileConfigured": bool(_env(_TOKEN_FILE_ENV)),
    }
