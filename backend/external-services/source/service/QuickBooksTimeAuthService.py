"""OAuth lifecycle for the QuickBooks Time (TSheets) connection.

Mirrors ``QuickBooksService`` (Accounting) but targets QuickBooks Time's own OAuth
server (``rest.tsheets.com``). Key differences from Accounting:

  * Credentials come from the *same* ``service_configuration`` JSON as the Accounting
    app, under ``TimeClientId`` / ``TimeClientSecret`` — so both connections run under
    the same QuickBooks ``service_id``.
  * The token endpoint is a fixed URL (no Intuit discovery document) and the code /
    refresh grants are sent as a **JSON body** with ``client_id`` / ``client_secret``
    inline (not an HTTP Basic auth header).
  * The callback carries ``code`` + ``state`` only (no ``realmId``); the grant response
    returns ``company_id`` / ``user_id`` instead.

Per-tenant tokens are stored by ``QuickBooksRepository`` in the same
``service_subscriber.additional_info`` blob, under ``time*`` keys.
"""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import quote, urlencode

import requests

from source.Utils import constants as const
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.service.QuickBooksService import QuickBooksService
from source.quickbooks.QuickBooksOAuthConfig import (
    qbtime_oauth_timeout_seconds,
    resolve_qbtime_redirect_uri,
    resolve_quickbooks_frontend_return_uri,
)

logger = logging.getLogger(__name__)

DEFAULT_AUTHORIZE_URL = "https://rest.tsheets.com/api/v1/authorize"
DEFAULT_TOKEN_URL = "https://rest.tsheets.com/api/v1/grant"


class QuickBooksTimeOAuthError(Exception):
    """Base error for the QuickBooks Time OAuth flow."""


class QuickBooksTimeCSRFError(QuickBooksTimeOAuthError):
    """The callback state token is invalid, expired, or already used."""


class QuickBooksTimeReconnectRequiredError(QuickBooksTimeOAuthError):
    """The tenant must reconnect QuickBooks Time before retrying."""

    def __init__(self, message: str, *, error_code: str, detail: Optional[str] = None, status_code: int = 401):
        super().__init__(message)
        self.error_code = error_code
        self.detail = detail or message
        self.status_code = int(status_code)


class QuickBooksTimeTokenError(QuickBooksTimeOAuthError):
    """A non-terminal token exchange/refresh failure."""


class QuickBooksTimeAuthService:
    """Connect, callback, refresh, status and disconnect for QuickBooks Time."""

    def __init__(self) -> None:
        self.repository = QuickBooksRepository()
        self.qbo_service = QuickBooksService()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @property
    def authorize_url(self) -> str:
        return (os.getenv("QBTIME_AUTHORIZE_URL") or DEFAULT_AUTHORIZE_URL).strip()

    @property
    def token_url(self) -> str:
        return (os.getenv("QBTIME_TOKEN_URL") or DEFAULT_TOKEN_URL).strip()

    def _redirect_uri(self) -> str:
        return resolve_qbtime_redirect_uri()

    def _time_credentials(self, tenant_id: str, service_id: str) -> tuple[str, str]:
        """Read the QuickBooks Time client id/secret from the shared QuickBooks config."""
        config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
        client_id = (config.get(const.KEY_TIME_CLIENT_ID) or "").strip()
        client_secret = (config.get(const.KEY_TIME_CLIENT_SECRET) or "").strip()
        return client_id, client_secret

    # ------------------------------------------------------------------
    # Connect
    # ------------------------------------------------------------------

    def start_connect(self, tenant_id: str, service_id: str) -> Dict[str, Any]:
        client_id, _client_secret = self._time_credentials(tenant_id, service_id)
        if not client_id:
            raise ValueError("TimeClientId is not configured for QuickBooks (service_configuration).")

        redirect_uri = self._redirect_uri()
        resolve_quickbooks_frontend_return_uri()
        state = secrets.token_urlsafe(32)
        self.repository.saveTimeOauthState(subscriber_id=tenant_id, service_id=service_id, state=state, ttl_minutes=10)

        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        authorization_url = f"{self.authorize_url}?{urlencode(params, quote_via=quote)}"
        logger.info(
            "[QBTimeAuth] start_connect | tenant_id=%s service_id=%s redirect_uri_set=%s",
            tenant_id, service_id, bool(redirect_uri),
        )
        return {
            "authorizationUrl": authorization_url,
            "scopeModel": "QuickBooks Time OAuth does not use the QBO scope parameter.",
        }

    # ------------------------------------------------------------------
    # Callback
    # ------------------------------------------------------------------

    def handle_callback(self, code: str, state: str) -> Dict[str, Any]:
        try:
            tenant_id, service_id, state_already_used = self.repository.consumeTimeOauthState(state)
        except Exception as exc:
            raise QuickBooksTimeCSRFError(
                "Invalid or expired QuickBooks Time authorization state. Please start the connection flow again."
            ) from exc

        status = self.repository.getTimeStatus(tenant_id, service_id)
        if state_already_used:
            if status.get("connected"):
                return self._connected_response(tenant_id, service_id, status, idempotent=True)
            raise QuickBooksTimeCSRFError(
                "This QuickBooks Time authorization link has already been used. Please start the connection flow again."
            )
        if status.get("connected"):
            return self._connected_response(tenant_id, service_id, status, idempotent=True)

        client_id, client_secret = self._time_credentials(tenant_id, service_id)
        if not client_id or not client_secret:
            raise ValueError("TimeClientId/TimeClientSecret missing in QuickBooks config.")

        token_json = self._post_grant(
            {
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": self._redirect_uri(),
            }
        )
        refresh_token = (token_json.get("refresh_token") or "").strip()
        if not refresh_token:
            raise QuickBooksTimeTokenError("QuickBooks Time token response did not include a refresh_token.")

        self.repository.saveTimeConnectionInfo(
            subscriber_id=tenant_id,
            service_id=service_id,
            company_id=self._as_str(token_json.get("company_id")),
            user_id=self._as_str(token_json.get("user_id")),
            refresh_token=refresh_token,
            access_token=(token_json.get("access_token") or "").strip() or None,
            access_expires_at=self._expires_at(token_json.get("expires_in")),
        )
        logger.info("[QBTimeAuth] handle_callback success | tenant_id=%s service_id=%s", tenant_id, service_id)
        return {
            "ok": True,
            "tenantId": tenant_id,
            "serviceId": service_id,
            "companyIdPresent": bool(token_json.get("company_id")),
        }

    # ------------------------------------------------------------------
    # Refresh (used by the export path to get a live access token)
    # ------------------------------------------------------------------

    def get_fresh_access_token(self, tenant_id: str, service_id: str) -> str:
        connection = self.repository.getAdditionalInfo(tenant_id, service_id) or {}

        # Reuse the stored access token while it's still valid. QuickBooks Time access
        # tokens live ~10 days, so refreshing on every call is unnecessary and — because
        # TSheets rotates refresh tokens on each grant — actively risks desyncing them.
        access_token = (connection.get(const.KEY_TIME_ACCESS_TOKEN) or "").strip()
        if access_token and not self._is_expired(connection.get(const.KEY_TIME_ACCESS_EXPIRES_AT)):
            return access_token

        client_id, client_secret = self._time_credentials(tenant_id, service_id)
        refresh_token = (connection.get(const.KEY_TIME_REFRESH_TOKEN) or "").strip()

        if not client_id or not client_secret:
            raise ValueError("TimeClientId/TimeClientSecret missing in QuickBooks config.")
        if not refresh_token:
            self.repository.markTimeReconnectRequired(
                subscriber_id=tenant_id,
                service_id=service_id,
                error_code=const.AUTH_ERROR_REFRESH_TOKEN_EXPIRED,
                reason="QuickBooks Time refresh token is missing. Please reconnect QuickBooks Time.",
                current_connection=connection,
            )
            raise QuickBooksTimeReconnectRequiredError(
                "QuickBooks Time connection expired. Please reconnect QuickBooks Time.",
                error_code=const.AUTH_ERROR_REFRESH_TOKEN_EXPIRED,
            )

        try:
            token_json = self._post_grant(
                {
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                }
            )
        except QuickBooksTimeTokenError:
            # QuickBooks Time's error taxonomy for a revoked/expired refresh token is not
            # yet confirmed against a real failure — treat any grant failure as reconnect.
            self.repository.markTimeReconnectRequired(
                subscriber_id=tenant_id,
                service_id=service_id,
                error_code=const.AUTH_ERROR_INVALID_GRANT,
                reason="QuickBooks Time authorization expired or was revoked. Please reconnect QuickBooks Time.",
                current_connection=connection,
            )
            raise QuickBooksTimeReconnectRequiredError(
                "QuickBooks Time authorization expired or was revoked. Please reconnect QuickBooks Time.",
                error_code=const.AUTH_ERROR_INVALID_GRANT,
            )

        new_access_token = (token_json.get("access_token") or "").strip()
        if not new_access_token:
            raise QuickBooksTimeTokenError("QuickBooks Time refresh succeeded but access_token was missing.")

        rotated_refresh_token = (token_json.get("refresh_token") or "").strip() or refresh_token
        self.repository.updateTimeConnectionTokens(
            subscriber_id=tenant_id,
            service_id=service_id,
            refresh_token=rotated_refresh_token,
            access_token=new_access_token,
            access_expires_at=self._expires_at(token_json.get("expires_in")),
            current_connection=connection,
        )
        return new_access_token

    # ------------------------------------------------------------------
    # Status / disconnect
    # ------------------------------------------------------------------

    def time_api_client(self, tenant_id: str, service_id: str):
        """Return a QuickBooks Time API client bound to this tenant's fresh access token.

        Raises if the tenant hasn't connected QuickBooks Time (reconnect required),
        so callers that treat the connection as optional should wrap this in try/except.
        """
        from source.quickbooks.QuickBooksTimeApiService import QuickBooksTimeApiService

        access_token = self.get_fresh_access_token(tenant_id, service_id)
        return QuickBooksTimeApiService(access_token=access_token, token_source="tenant_oauth")

    def get_status(self, tenant_id: str, service_id: str) -> Dict[str, Any]:
        status = self.repository.getTimeStatus(tenant_id, service_id)
        status["scopeModel"] = "QuickBooks Time OAuth does not use the QBO scope parameter."
        return status

    def disconnect(self, tenant_id: str, service_id: str) -> Dict[str, Any]:
        self.repository.disconnectTime(tenant_id, service_id)
        return {"ok": True, "tenantId": tenant_id, "serviceId": service_id, "status": const.STATUS_DISCONNECTED}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _post_grant(self, body: Dict[str, Any]) -> Dict[str, Any]:
        try:
            response = requests.post(self.token_url, json=body, timeout=qbtime_oauth_timeout_seconds())
        except requests.RequestException as exc:
            logger.warning("QuickBooks Time token request failed due to a network error")
            raise QuickBooksTimeTokenError("QuickBooks Time token request failed.") from exc
        if response.status_code != 200:
            logger.warning(
                "QuickBooks Time token grant failed | status=%s",
                response.status_code,
            )
            raise QuickBooksTimeTokenError(
                f"QuickBooks Time token request failed (HTTP {response.status_code})."
            )
        try:
            payload = response.json()
        except Exception as exc:
            raise QuickBooksTimeTokenError("QuickBooks Time token response was not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise QuickBooksTimeTokenError("QuickBooks Time token response had an unexpected shape.")
        return payload

    def _connected_response(self, tenant_id: str, service_id: str, status: Dict[str, Any], *, idempotent: bool) -> Dict[str, Any]:
        result = {
            "ok": True,
            "tenantId": tenant_id,
            "serviceId": service_id,
            "companyIdPresent": bool(status.get("companyIdPresent")),
        }
        if idempotent:
            result["message"] = "Already connected (idempotent)."
        return result

    @staticmethod
    def _as_str(value: Any) -> Optional[str]:
        if value is None or value == "":
            return None
        return str(value)

    @staticmethod
    def _expires_at(expires_in: Any) -> Optional[str]:
        if not expires_in:
            return None
        try:
            return (datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))).isoformat()
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_expired(access_expires_at: Any, *, buffer_seconds: int = 300) -> bool:
        """True when the stored access token is missing an expiry or is at/near expiry.

        A safety buffer means we refresh slightly early rather than mid-request. If the
        stored expiry can't be parsed, treat the token as expired (force a refresh).
        """
        if not access_expires_at:
            return True
        try:
            expires_at = datetime.fromisoformat(str(access_expires_at).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return True
        return datetime.now(timezone.utc) >= (expires_at - timedelta(seconds=buffer_seconds))
