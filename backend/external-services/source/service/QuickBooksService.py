# source/service/QuickBooksService.py

import os
import logging
import secrets
from urllib.parse import urlencode, quote
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

from source.Utils.DecryptionUtil import Decryption
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils import constants as const
from source.quickbooks.QuickBooksAuthentication import QuickBooksCSRFError
from source.quickbooks.QuickBooksOAuthScopes import resolve_qbo_scope_text
from source.quickbooks.QuickBooksOAuthConfig import (
    normalize_qbo_environment,
    qbo_oauth_timeout_seconds,
    resolve_qbo_redirect_uri,
    resolve_quickbooks_frontend_return_uri,
)

load_dotenv()

logger = logging.getLogger(__name__)


class QuickBooksService:

    def __init__(self):
        self.repository = QuickBooksRepository()

    @property
    def token_url(self) -> str:
        """Shared Intuit OAuth token endpoint resolved from discovery metadata."""
        from source.Utils import QBODiscoveryClient
        return QBODiscoveryClient.get_token_endpoint("production")

    def start_connect(self, tenant_id: str, service_id: str):
        config = self.get_quickbooks_config(tenant_id, service_id)
        client_id = str(config.get(const.KEY_CLIENT_ID) or "").strip()
        if not client_id:
            raise ValueError("ClientId missing in QuickBooks config")

        environment = normalize_qbo_environment(
            config.get(const.KEY_ENV) or config.get("Env") or "sandbox"
        )
        redirect_uri = resolve_qbo_redirect_uri(environment)
        resolve_quickbooks_frontend_return_uri()
        scopes = resolve_qbo_scope_text()

        from source.Utils import QBODiscoveryClient
        auth_base = QBODiscoveryClient.get_authorization_endpoint(environment)
        state = secrets.token_urlsafe(32)

        self.repository.saveOauthState(
            subscriber_id=tenant_id,
            service_id=service_id,
            state=state,
            ttl_minutes=10,
        )

        params = {
            "client_id": client_id,
            "response_type": "code",
            "scope": scopes,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        authorization_url = f"{auth_base}?{urlencode(params, quote_via=quote)}"

        logger.info(
            "QuickBooks OAuth connection started | tenant=%s service_id=%s env=%s",
            tenant_id,
            service_id,
            environment,
        )
        return {
            "authorizationUrl": authorization_url,
            "env": environment,
            "requestedScopes": scopes.split(),
        }

    def handle_callback(self, code: str, state: str, realm_id: str):
        try:
            try:
                tenant_id, service_id, state_already_used = self.repository.consumeOauthState(state)
            except Exception as exc:
                raise QuickBooksCSRFError(
                    "Invalid or expired QuickBooks authorization state. Please start the connection flow again."
                ) from exc

            status = self.repository.getStatus(tenant_id, service_id)
            if state_already_used:
                if status.get(const.KEY_STATUS) == const.STATUS_CONNECTED:
                    return {
                        "ok": True,
                        "tenantId": tenant_id,
                        "serviceId": service_id,
                        "realmIdPresent": bool(status.get("realmIdPresent") or realm_id),
                        "refreshExpiresAt": status.get("refreshExpiresAt"),
                        "message": "Already connected (idempotent).",
                    }
                raise QuickBooksCSRFError(
                    "This QuickBooks authorization link has already been used. Please start the connection flow again."
                )

            if status.get(const.KEY_STATUS) == const.STATUS_CONNECTED:
                return {
                    "ok": True,
                    "tenantId": tenant_id,
                    "serviceId": service_id,
                    "realmIdPresent": bool(status.get("realmIdPresent") or realm_id),
                    "refreshExpiresAt": status.get("refreshExpiresAt"),
                    "message": "Already connected (idempotent).",
                }

            existing = self.repository.findSubscriberByRealmIdAndServiceId(
                str(realm_id), str(service_id)
            )
            if existing and str(existing.get("subscriber_id")) != str(tenant_id):
                raise ValueError(
                    "This QuickBooks company is already connected to another FIELDCRAFT tenant."
                )

            config = self.get_quickbooks_config(tenant_id, service_id)
            client_id = str(config.get(const.KEY_CLIENT_ID) or "").strip()
            client_secret = str(config.get(const.KEY_CLIENT_SECRET) or "").strip()
            if not client_id or not client_secret:
                raise ValueError("ClientId/ClientSecret missing in QuickBooks config")

            environment = normalize_qbo_environment(
                config.get(const.KEY_ENV) or config.get("Env") or "sandbox"
            )
            redirect_uri = resolve_qbo_redirect_uri(environment)
            logger.info(
                "Exchanging QuickBooks OAuth authorization code | tenant=%s service_id=%s env=%s realm_id_present=%s",
                tenant_id,
                service_id,
                environment,
                bool(realm_id),
            )
            token_json = self.exchange_code_for_tokens(
                client_id=client_id,
                client_secret=client_secret,
                code=code,
                redirect_uri=redirect_uri,
                environment=environment,
            )

            refresh_token = token_json["refresh_token"]
            expires_in = int(token_json.get("expires_in", 3600))
            refresh_expires_in = token_json.get("x_refresh_token_expires_in")
            now = datetime.now(timezone.utc)
            refresh_expires_at = (
                (now + timedelta(seconds=int(refresh_expires_in))).isoformat()
                if refresh_expires_in
                else None
            )
            access_expires_at = (now + timedelta(seconds=expires_in)).isoformat()

            requested_scopes = resolve_qbo_scope_text().split()
            granted_scopes = str(token_json.get("scope") or "").split()
            try:
                self.repository.saveConnectionInfo(
                    subscriber_id=tenant_id,
                    service_id=service_id,
                    realm_id=realm_id,
                    env=environment,
                    refresh_token=refresh_token,
                    refresh_expires_at=refresh_expires_at,
                    access_expires_at=access_expires_at,
                    requested_scopes=requested_scopes,
                    granted_scopes=granted_scopes,
                )
            except Exception:
                # Do not leave a valid Intuit grant orphaned when local persistence fails.
                try:
                    self.revoke_token(
                        client_id=client_id,
                        client_secret=client_secret,
                        refresh_token=refresh_token,
                        environment=environment,
                    )
                except Exception:
                    logger.exception(
                        "QuickBooks connection persistence failed and token cleanup also failed | tenant=%s service_id=%s",
                        tenant_id,
                        service_id,
                    )
                raise

            try:
                from source.quickbooks.QuickBooksCapabilityService import QuickBooksCapabilityService
                QuickBooksCapabilityService().fetch_and_save(tenant_id, service_id)
            except Exception:
                logger.warning(
                    "QuickBooks capability detection failed after OAuth connection | tenant=%s service_id=%s",
                    tenant_id,
                    service_id,
                    exc_info=True,
                )

            return {
                "ok": True,
                "tenantId": tenant_id,
                "serviceId": service_id,
                "realmIdPresent": bool(realm_id),
                "refreshExpiresAt": refresh_expires_at,
            }
        except Exception:
            logger.exception("QuickBooks OAuth callback failed")
            raise

    def get_quickbooks_config(self, tenant_id: str, service_id: str):
        try:
            salt = os.getenv("SALT")
            iv = os.getenv("IV")
            use_v2 = (os.getenv("ENCRYPTION_DECRYPTION_V2", "false") or "").strip().lower() in (
                "1", "true", "yes", "on"
            )
            if not salt:
                raise ValueError("SALT must be set in the runtime environment")
            if not use_v2 and not iv:
                raise ValueError("IV must be set when ENCRYPTION_DECRYPTION_V2 is false")

            decryptor = Decryption(salt, iv)
            config = decryptor.fetch_quickbooks_credentials(tenant_id, service_id)
            if not isinstance(config, dict):
                raise TypeError(
                    f"Expected dict from fetch_quickbooks_credentials, got {type(config)}"
                )
            return config
        except Exception:
            logger.exception(
                "Unable to load QuickBooks configuration | tenant=%s service_id=%s",
                tenant_id,
                service_id,
            )
            raise

    def exchange_code_for_tokens(
        self,
        *,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
        environment: str,
    ):
        from source.Utils import QBODiscoveryClient

        token_url = QBODiscoveryClient.get_token_endpoint(environment)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "x-include-refresh-token-hard-expires-in": "true",
        }
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }
        try:
            response = requests.post(
                token_url,
                auth=requests.auth.HTTPBasicAuth(client_id, client_secret),
                headers=headers,
                data=data,
                timeout=qbo_oauth_timeout_seconds(),
            )
        except requests.RequestException as exc:
            logger.exception("QuickBooks OAuth token request failed")
            raise RuntimeError("QuickBooks token exchange request failed.") from exc

        token_data = qbo_api.safe_json(response)
        if response.status_code != 200:
            error_code = str(token_data.get("error") or "token_exchange_failed")
            logger.warning(
                "QuickBooks OAuth token exchange rejected | status=%s error=%s",
                response.status_code,
                error_code,
            )
            raise RuntimeError(
                f"QuickBooks token exchange failed with status {response.status_code}."
            )
        if not token_data.get("access_token") or not token_data.get("refresh_token"):
            raise RuntimeError("QuickBooks token response is missing required tokens.")

        logger.info(
            "QuickBooks OAuth token exchange succeeded | token_type=%s expires_in=%s granted_scope_count=%s",
            token_data.get("token_type") or "",
            token_data.get("expires_in"),
            len(str(token_data.get("scope") or "").split()),
        )
        return token_data

    def get_status(self, tenant_id: str, service_id: str):
        try:
            status = self.repository.getStatus(tenant_id, service_id)
            required_scopes = resolve_qbo_scope_text().split()
            granted_scopes = list(status.get("oauthScopesGranted") or [])
            requested_scopes = list(status.get("oauthScopesRequested") or [])
            effective_scopes = granted_scopes or requested_scopes
            missing_scopes = [scope for scope in required_scopes if scope not in effective_scopes]
            status["requiredScopes"] = required_scopes
            status["effectiveScopes"] = effective_scopes
            status["missingScopes"] = missing_scopes
            status["scopeUpgradeRequiresReconnect"] = bool(missing_scopes)
            return status
        except Exception:
            logger.exception(
                "Unable to read QuickBooks status | tenant=%s service_id=%s",
                tenant_id,
                service_id,
            )
            raise

    def disconnect(self, tenant_id: str, service_id: str, object_id: str | None = None):
        connection = self.repository.getAdditionalInfo(tenant_id, service_id) or {}
        refresh_token = str(connection.get(const.KEY_REFRESH_TOKEN) or "").strip()
        if refresh_token:
            config = self.get_quickbooks_config(tenant_id, service_id)
            client_id = str(config.get(const.KEY_CLIENT_ID) or "").strip()
            client_secret = str(config.get(const.KEY_CLIENT_SECRET) or "").strip()
            environment = normalize_qbo_environment(
                connection.get(const.KEY_ENV)
                or config.get(const.KEY_ENV)
                or config.get("Env")
                or "sandbox"
            )
            if not client_id or not client_secret:
                raise ValueError("ClientId/ClientSecret missing in QuickBooks config")
            self.revoke_token(
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
                environment=environment,
            )

        self.repository.disconnect(tenant_id, service_id)
        return {
            "ok": True,
            "tenantId": tenant_id,
            "serviceId": service_id,
            "status": "disconnected",
        }

    def revoke_token(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        environment: str,
    ) -> None:
        from source.Utils import QBODiscoveryClient

        try:
            response = requests.post(
                QBODiscoveryClient.get_revocation_endpoint(environment),
                auth=requests.auth.HTTPBasicAuth(client_id, client_secret),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json={"token": refresh_token},
                timeout=qbo_oauth_timeout_seconds(),
            )
        except requests.RequestException as exc:
            logger.warning("QuickBooks OAuth token revocation request failed")
            raise RuntimeError("QuickBooks token revocation request failed.") from exc
        if response.status_code == 200:
            logger.info("QuickBooks OAuth token revoked successfully")
            return
        if response.status_code == 400:
            logger.warning(
                "QuickBooks OAuth revocation returned 400; clearing the local connection because the token is already unusable"
            )
            return
        raise RuntimeError(
            f"QuickBooks token revocation failed with status {response.status_code}."
        )

    # ------------------------------------------------------------------
    # Optional: Auto-run sync after successful OAuth connection
    # ------------------------------------------------------------------

    def run_initial_customer_sync(self, tenant_id: str, service_id: str) -> None:
        """Run a basic bi-directional customer sync.

        This is intended to be invoked ...
        """
        try:
            from source.quickbooks.QuickBooksCustomerExportService import QuickBooksCustomerExportService
            from source.quickbooks.QuickBooksCustomerImportService import QuickBooksCustomerImportService

            exporter = QuickBooksCustomerExportService()
            importer = QuickBooksCustomerImportService()

            logger.info(
                "Starting initial QuickBooks customer sync | tenant=%s service_id=%s",
                tenant_id,
                service_id,
            )

            # 1) FIELDCRAFT -> QBO
            exporter.export_customers(
                tenant_id=str(tenant_id),
                service_id=str(service_id),
                limit=200,
                offset=0,
            )

            # 2) QBO -> FIELDCRAFT
            importer.import_customers(
                tenant_id=str(tenant_id),
                service_id=str(service_id),
                # tenant_api_token= str(ui_token),
                page_size=100,
                start_position=1,
                max_customers=200,
                dry_run=False,
            )

            logger.info(
                "Initial QuickBooks customer sync completed | tenant=%s service_id=%s",
                tenant_id,
                service_id,
            )

        except Exception:
            logger.exception(
                "Initial QuickBooks customer sync failed | tenant=%s service_id=%s",
                tenant_id,
                service_id,
            )
