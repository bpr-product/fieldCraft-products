from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import requests

from source.Utils import constants as const
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.quickbooks.QuickBooksSupportDiagnostics import fingerprint


logger = logging.getLogger(__name__)


class QuickBooksOAuthError(Exception):
    """Base exception for QuickBooks OAuth and authorization failures."""


class QuickBooksReconnectRequiredError(QuickBooksOAuthError):
    """Raised when the tenant must reconnect QuickBooks before retrying."""

    def __init__(self, message: str, *, error_code: str, detail: Optional[str] = None, status_code: int = 401):
        super().__init__(message)
        self.error_code = error_code
        self.detail = detail or message
        self.status_code = int(status_code)


class QuickBooksCSRFError(QuickBooksOAuthError):
    """Raised when the OAuth callback state token is invalid or expired."""


class QuickBooksTokenRefreshError(QuickBooksOAuthError):
    """Raised for non-terminal token refresh failures."""


class QuickBooksOAuthClient:
    """Shared OAuth helper used by QuickBooks services.

    Responsibilities:
      * refresh access tokens
      * persist rotated refresh tokens returned by Intuit
      * mark connections as reconnect-required on invalid_grant / expired refresh tokens
      * clear stale auth errors after successful authorization
    """

    def __init__(self, *, repository: Optional[QuickBooksRepository] = None):
        self.repository = repository or QuickBooksRepository()

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _safe_json(response: requests.Response) -> Dict[str, Any]:
        try:
            payload = response.json()
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def clear_auth_error(self, tenant_id: str, service_id: str) -> None:
        try:
            self.repository.clearAuthError(tenant_id, service_id)
        except Exception:
            logger.exception(
                "Unable to clear QuickBooks authentication error | tenant=%s service_id=%s",
                tenant_id,
                service_id,
            )
            raise

    def mark_reconnect_required(
        self,
        *,
        tenant_id: str,
        service_id: str,
        error_code: str,
        reason: str,
        detail: Optional[str] = None,
        current_connection: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.repository.markReconnectRequired(
            subscriber_id=tenant_id,
            service_id=service_id,
            error_code=error_code,
            reason=reason,
            detail=detail,
            current_connection=current_connection,
        )

    def refresh_access_token(
        self,
        *,
        tenant_id: str,
        service_id: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        token_url: str,
        current_connection: Optional[Dict[str, Any]] = None,
        request_timeout: int = 30,
        debug_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        del debug_context  # Kept for backward-compatible call sites; secrets are never diagnostic payloads.
        if not refresh_token:
            self.mark_reconnect_required(
                tenant_id=tenant_id,
                service_id=service_id,
                error_code=const.AUTH_ERROR_REFRESH_TOKEN_EXPIRED,
                reason="QuickBooks refresh token is missing. Please reconnect QuickBooks.",
                detail="Missing refresh token in stored QuickBooks connection.",
                current_connection=current_connection,
            )
            raise QuickBooksReconnectRequiredError(
                "QuickBooks connection expired. Please reconnect QuickBooks.",
                error_code=const.AUTH_ERROR_REFRESH_TOKEN_EXPIRED,
                detail="Missing refresh token in stored QuickBooks connection.",
            )

        logger.info(
            "Refreshing QuickBooks access token | tenant=%s service_id=%s env=%s refresh_token_fingerprint=%s",
            tenant_id,
            service_id,
            (current_connection or {}).get(const.KEY_ENV),
            fingerprint(refresh_token),
        )
        try:
            response = requests.post(
                token_url,
                auth=requests.auth.HTTPBasicAuth(client_id, client_secret),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "x-include-refresh-token-hard-expires-in": "true",
                },
                data={"grant_type": "refresh_token", "refresh_token": refresh_token},
                timeout=request_timeout,
            )
        except requests.RequestException as exc:
            logger.warning(
                "QuickBooks token refresh network failure | tenant=%s service_id=%s",
                tenant_id,
                service_id,
            )
            raise QuickBooksTokenRefreshError("QuickBooks token refresh request failed.") from exc

        payload = self._safe_json(response)
        error_code = str(payload.get("error") or "").strip().lower()
        error_detail = str(payload.get("error_description") or "").strip()[:500]
        if response.status_code != 200:
            logger.warning(
                "QuickBooks token refresh rejected | tenant=%s service_id=%s status=%s error=%s",
                tenant_id, service_id, response.status_code, error_code or "unknown",
            )
            if response.status_code == 400 and error_code == "invalid_grant":
                reason = "QuickBooks authorization expired or was revoked. Please reconnect QuickBooks."
                self.mark_reconnect_required(
                    tenant_id=tenant_id, service_id=service_id,
                    error_code=const.AUTH_ERROR_INVALID_GRANT, reason=reason,
                    detail=error_detail or "Token refresh failed with invalid_grant.",
                    current_connection=current_connection,
                )
                raise QuickBooksReconnectRequiredError(
                    reason, error_code=const.AUTH_ERROR_INVALID_GRANT,
                    detail=error_detail or "Token refresh failed with invalid_grant.",
                )
            if response.status_code in (401, 403):
                reason = "QuickBooks authorization failed. Please reconnect QuickBooks and try again."
                self.mark_reconnect_required(
                    tenant_id=tenant_id, service_id=service_id,
                    error_code=const.AUTH_ERROR_REFRESH_TOKEN_EXPIRED, reason=reason,
                    detail=error_detail or f"QuickBooks token endpoint returned status {response.status_code}.",
                    current_connection=current_connection,
                )
                raise QuickBooksReconnectRequiredError(
                    reason, error_code=const.AUTH_ERROR_REFRESH_TOKEN_EXPIRED,
                    detail=error_detail or f"QuickBooks token endpoint returned status {response.status_code}.",
                    status_code=response.status_code,
                )
            raise QuickBooksTokenRefreshError(
                f"QuickBooks token refresh failed with status {response.status_code}."
            )

        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            raise QuickBooksTokenRefreshError(
                "QuickBooks token refresh succeeded but access_token was missing."
            )

        rotated_refresh_token = str(payload.get("refresh_token") or "").strip()
        update_token = rotated_refresh_token or refresh_token
        now = self._utc_now()

        def expiry(seconds: object) -> Optional[str]:
            if not seconds:
                return None
            try:
                return (now + timedelta(seconds=int(seconds))).isoformat()
            except (TypeError, ValueError):
                return None

        self.repository.updateConnectionTokens(
            subscriber_id=tenant_id,
            service_id=service_id,
            refresh_token=update_token,
            refresh_expires_at=expiry(payload.get("x_refresh_token_expires_in")),
            access_expires_at=expiry(payload.get("expires_in")),
            current_connection=current_connection,
        )
        logger.info(
            "QuickBooks token refresh succeeded | tenant=%s service_id=%s expires_in=%s rotated_refresh_token=%s access_token_fingerprint=%s",
            tenant_id, service_id, payload.get("expires_in"), bool(rotated_refresh_token),
            fingerprint(access_token),
        )

        return access_token
