import json
import logging
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from source.dal.dbconnector import postgres_service_connection
import os
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient, ContentSettings
from source.Utils import constants as const
from source.Utils.DecryptionUtil import Decryption

# Azure storage account connection string
AZURE_STORAGE_CONNECTION_STRING = "AZURE_TENANT_CONNECTION_STRING"

logger = logging.getLogger(__name__)

class QuickBooksRepository:

    _ENCRYPTED_VALUE_PREFIX = "enc:v2:"
    _REALM_ID_HASH_KEY = "realmIdHash"
    _OAUTH_STATE_HASH_KEY = "oauthStateHash"
    _TIME_OAUTH_STATE_HASH_KEY = "timeOauthStateHash"
    _ENCRYPTION_PASSWORD_ENV = "QUICKBOOKS_ENCRYPTION_PASSWORD"
    _CONNECTION_SECRET_KEYS = (
        const.KEY_REFRESH_TOKEN,
        getattr(const, "KEY_ACCESS_TOKEN", "accessToken"),
        const.KEY_REALM_ID,
        const.KEY_PROJECT_CLIENT_REALM_ID,
        const.KEY_TIME_REFRESH_TOKEN,
        const.KEY_TIME_ACCESS_TOKEN,
    )

    def _crypto(self) -> Decryption:
        salt = os.getenv("SALT")
        if not salt:
            raise RuntimeError("SALT must be configured before storing QuickBooks secrets.")
        return Decryption(salt, os.getenv("IV") or "")

    def _encryption_context(self) -> str:
        context = (os.getenv(self._ENCRYPTION_PASSWORD_ENV) or "").strip()
        if not context:
            raise RuntimeError(f"{self._ENCRYPTION_PASSWORD_ENV} must be configured before storing QuickBooks secrets.")
        return context

    def _encrypt_secret(self, value: Any) -> Any:
        if value is None or value == "":
            return value
        text = str(value)
        if text.startswith(self._ENCRYPTED_VALUE_PREFIX):
            return text
        encrypted = self._crypto().encrypt_v2(text, self._encryption_context())
        return f"{self._ENCRYPTED_VALUE_PREFIX}{encrypted}"

    def _decrypt_secret(self, value: Any) -> Any:
        if value is None or value == "":
            return value
        text = str(value)
        if not text.startswith(self._ENCRYPTED_VALUE_PREFIX):
            return value
        encrypted = text[len(self._ENCRYPTED_VALUE_PREFIX):]
        return self._crypto().decrypt_v2(encrypted, self._encryption_context())

    def _oauth_state_hash(self, state: Any) -> str:
        state_text = str(state or "").strip()
        if not state_text:
            raise ValueError("OAuth state is required")
        salt = os.getenv("SALT")
        if not salt:
            raise RuntimeError("SALT must be configured before hashing QuickBooks OAuth state.")
        return hmac.new(
            salt.encode("utf-8"), state_text.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def _realm_id_hash(self, realm_id: Any) -> str | None:
        if realm_id is None or realm_id == "":
            return None
        salt = os.getenv("SALT")
        if not salt:
            raise RuntimeError("SALT must be configured before hashing QuickBooks realm IDs.")
        digest = hmac.new(salt.encode("utf-8"), str(realm_id).encode("utf-8"), hashlib.sha256).hexdigest()
        return digest

    def _encrypt_connection_secrets(self, additional_info: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(additional_info or {})

        realm_id = payload.get(const.KEY_REALM_ID)
        if realm_id:
            payload[self._REALM_ID_HASH_KEY] = self._realm_id_hash(realm_id)

        for key in self._CONNECTION_SECRET_KEYS:
            if payload.get(key):
                payload[key] = self._encrypt_secret(payload[key])

        return payload

    def _decrypt_connection_secrets(self, additional_info: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(additional_info or {})
        for key in self._CONNECTION_SECRET_KEYS:
            if payload.get(key):
                payload[key] = self._decrypt_secret(payload[key])
        return payload

    def findSubscriberByRealmIdAndServiceId(self, realm_id: str, service_id: str):
        """Resolve tenant (subscriber_id) + service_id by QuickBooks realmId.

        Webhooks do not include tenant_id. We map realmId -> service_subscriber.subscriber_id.
        This variant additionally filters by service_id (QuickBooks) to avoid accidental matches.
        """
        realm_id = (realm_id or "").strip()
        service_id = (service_id or "").strip()
        if not realm_id or not service_id:
            return None

        realm_id_hash = self._realm_id_hash(realm_id)
        conn = postgres_service_connection()
        cur = conn.cursor()
        try:
            # additional_info is a TEXT column containing JSON. Extract only the
            # candidate values in SQL, then perform the authoritative exact match
            # after parsing/decrypting the payload below.
            cur.execute(
                """
                SELECT subscriber_id, service_id, additional_info
                FROM service_subscriber
                WHERE service_id = %s
                  AND (invalid IS NULL OR invalid = false)
                  AND (
                        substring(
                            CAST(additional_info AS TEXT)
                            FROM '"realmIdHash"[[:space:]]*:[[:space:]]*"([^"]+)"'
                        ) = %s
                     OR substring(
                            CAST(additional_info AS TEXT)
                            FROM '"realmId"[[:space:]]*:[[:space:]]*"([^"]+)"'
                        ) = %s
                  )
                LIMIT 50
                """,
                (service_id, realm_id_hash, realm_id),
            )
            rows = cur.fetchall() or []

            for subscriber_id, svc_id, additional_info_raw in rows:
                try:
                    info = self.parseAdditionalInfo(additional_info_raw)
                    if info.get(const.KEY_STATUS) == const.STATUS_DISCONNECTED:
                        continue
                    if str(info.get(const.KEY_REALM_ID)) == str(realm_id):
                        return {
                            "subscriber_id": str(subscriber_id),
                            "service_id": str(svc_id),
                            "additional_info": info,
                        }
                except Exception:
                    continue
            return None
        finally:
            try:
                cur.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

    def findSubscriberByRealmId(self, realm_id: str):
        """Resolve tenant (subscriber_id) + service_id by QuickBooks realmId.

        Webhooks do not include tenant_id. We map realmId -> service_subscriber.subscriber_id
        by scanning additional_info (JSON) and confirming an exact realmId match.
        """
        realm_id = (realm_id or "").strip()
        if not realm_id:
            return None

        realm_id_hash = self._realm_id_hash(realm_id)
        conn = postgres_service_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT subscriber_id, service_id, additional_info
                FROM service_subscriber
                WHERE (invalid IS NULL OR invalid = false)
                  AND (
                        substring(
                            CAST(additional_info AS TEXT)
                            FROM '"realmIdHash"[[:space:]]*:[[:space:]]*"([^"]+)"'
                        ) = %s
                     OR substring(
                            CAST(additional_info AS TEXT)
                            FROM '"realmId"[[:space:]]*:[[:space:]]*"([^"]+)"'
                        ) = %s
                  )
                LIMIT 50
                """,
                (realm_id_hash, realm_id),
            )
            rows = cur.fetchall() or []
            for subscriber_id, service_id, additional_info_raw in rows:
                try:
                    info = self.parseAdditionalInfo(additional_info_raw)
                    if info.get(const.KEY_STATUS) == const.STATUS_DISCONNECTED:
                        continue
                    if str(info.get(const.KEY_REALM_ID)) == str(realm_id):
                        return {
                            "subscriber_id": str(subscriber_id),
                            "service_id": str(service_id),
                            "additional_info": info,
                        }
                except Exception:
                    continue
            return None
        finally:
            try:
                cur.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

    def getServiceIdByName(self, service_name: str) -> str:
        """Resolve external_service.service_master.id by service name."""
        service_name = (service_name or "").strip()
        if not service_name:
            raise Exception("service_name is required")

        conn = postgres_service_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT id
                FROM service_master
                WHERE name = %s
                LIMIT 1
                """,
                (service_name,),
            )
            row = cur.fetchone()
            if not row:
                raise Exception(f"service_master row not found for name={service_name}")
            return str(row[0])
        finally:
            try:
                cur.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

    def saveOauthState(self, subscriber_id: str, service_id: str, state: str, ttl_minutes: int = 10) -> None:
        """Persist only a keyed hash of the short-lived accounting OAuth state."""
        try:
            now = datetime.now(timezone.utc)
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            additional_info.pop("state", None)  # Remove legacy plaintext accounting state.
            additional_info.update({
                self._OAUTH_STATE_HASH_KEY: self._oauth_state_hash(state),
                "oauthStateCreatedAt": now.isoformat(),
                "oauthStateExpiresAt": (now + timedelta(minutes=ttl_minutes)).isoformat(),
                "oauthStateUsedAt": None,
            })
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception:
            logger.exception("QuickBooks repository operation failed | method=saveOauthState")
            raise

    def _consume_accounting_oauth_state(self, state: str):
        """Atomically validate and consume an accounting OAuth state hash."""
        state_hash = self._oauth_state_hash(state)
        conn = postgres_service_connection()
        if conn is None:
            raise RuntimeError("Unable to connect to the service database")
        cur = conn.cursor()
        try:
            conn.set_session(autocommit=False)
            cur.execute(
                """
                SELECT subscriber_id, service_id, additional_info
                FROM service_subscriber
                WHERE substring(
                    CAST(additional_info AS TEXT)
                    FROM '"oauthStateHash"[[:space:]]*:[[:space:]]*"([^"]+)"'
                ) = %s
                FOR UPDATE
                LIMIT 1
                """,
                (state_hash,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Invalid state")

            subscriber_id, service_id, additional_info_raw = row
            additional_info = self.parseAdditionalInfo(additional_info_raw)
            if not hmac.compare_digest(
                str(additional_info.get(self._OAUTH_STATE_HASH_KEY) or ""), state_hash
            ):
                raise ValueError("Invalid state")

            if additional_info.get("oauthStateUsedAt") is not None:
                conn.commit()
                return str(subscriber_id), str(service_id), True

            expires_at_raw = additional_info.get("oauthStateExpiresAt")
            if not expires_at_raw:
                raise ValueError("State expiry is missing")
            expires_at = datetime.fromisoformat(str(expires_at_raw).replace("Z", "+00:00"))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < datetime.now(timezone.utc):
                raise ValueError("State expired")

            additional_info["oauthStateUsedAt"] = datetime.now(timezone.utc).isoformat()
            stored_payload = json.dumps(
                self._encrypt_connection_secrets(additional_info), separators=(",", ":")
            )
            cur.execute(
                """
                UPDATE service_subscriber
                SET additional_info = %s
                WHERE subscriber_id = %s AND service_id = %s
                """,
                (stored_payload, subscriber_id, service_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError("OAuth state row could not be updated")
            conn.commit()
            return str(subscriber_id), str(service_id), False
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.exception("QuickBooks OAuth state validation failed")
            raise
        finally:
            cur.close()
            conn.close()

    def consumeOauthState(self, state: str):
        return self._consume_accounting_oauth_state(state)

    def saveConnectionInfo(
            self,
            subscriber_id: str,
            service_id: str,
            realm_id: str,
            env: str,
            refresh_token: str,
            refresh_expires_at: str | None,
            access_expires_at: str | None = None,
            requested_scopes: list[str] | None = None,
            granted_scopes: list[str] | None = None,
    ) -> None:
        try:
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            previous_realm_id = str(additional_info.get(const.KEY_REALM_ID) or "").strip()
            new_realm_id = str(realm_id or "").strip()
            if previous_realm_id and previous_realm_id != new_realm_id:
                # The configured client company belongs to the previous firm
                # connection and must not be reused for another QBO company.
                additional_info.pop(const.KEY_PROJECT_CLIENT_REALM_ID, None)
            additional_info[const.KEY_REALM_ID] = realm_id
            additional_info[const.KEY_ENV] = env
            now_iso = datetime.now(timezone.utc).isoformat()
            additional_info[const.KEY_REFRESH_TOKEN] = refresh_token
            additional_info[const.KEY_REFRESH_EXPIRES_AT] = refresh_expires_at
            additional_info[const.KEY_ACCESS_EXPIRES_AT] = access_expires_at
            additional_info[const.KEY_STATUS] = const.STATUS_CONNECTED
            additional_info["oauthScopesRequested"] = list(requested_scopes or [])
            additional_info["oauthScopesGranted"] = list(granted_scopes or [])
            additional_info[const.KEY_RECONNECT_REQUIRED] = False
            additional_info[const.KEY_AUTH_ERROR] = None
            additional_info["connectedAt"] = now_iso
            additional_info["updatedAt"] = now_iso

            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)

        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=saveConnectionInfo")
            raise

    def updateAdditionalInfo(self, subscriber_id: str, service_id: str, additional_info: dict) -> None:
        conn = postgres_service_connection()
        cur = conn.cursor()
        try:
            try:
                additional_info_json = json.dumps(
                    self._encrypt_connection_secrets(dict(additional_info or {})),
                    separators=(",", ":"),
                )
                cur.execute(
                    """
                    UPDATE service_subscriber
                    SET additional_info = %s
                    WHERE subscriber_id = %s AND service_id = %s
                    """,
                    (additional_info_json, subscriber_id, service_id),
                )
                if cur.rowcount == 0:
                    raise Exception(
                        f"service_subscriber row not found for subscriber_id={subscriber_id}, service_id={service_id}"
                    )
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                logger.exception("QuickBooks repository operation failed | method=updateAdditionalInfo")
                raise
        finally:
            cur.close()
            conn.close()

    def getAdditionalInfo(self, subscriber_id: str, service_id: str) -> Dict[str, Any]:
        conn = postgres_service_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT additional_info
                FROM service_subscriber
                WHERE subscriber_id = %s AND service_id = %s
                LIMIT 1
                """,
                (subscriber_id, service_id),
            )
            row = cur.fetchone()
            if not row:
                raise Exception(
                    f"service_subscriber row not found for subscriber_id={subscriber_id}, service_id={service_id}"
                )
            return self.parseAdditionalInfo(row[0])
        except Exception:
            logger.exception("QuickBooks repository operation failed | method=getAdditionalInfo")
            raise
        finally:
            cur.close()
            conn.close()

    def parseAdditionalInfo(self, value) -> Dict[str, Any]:
        try:
            if value is None:
                return {}
            if isinstance(value, dict):
                return self._decrypt_connection_secrets(value)
            if isinstance(value, str):
                text = value.strip()
                if text == "":
                    return {}
                try:
                    return self._decrypt_connection_secrets(json.loads(text))
                except Exception:
                    return {}
            return {}
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=parseAdditionalInfo")
            raise


    def setLastSyncAt(self, subscriber_id: str, service_id: str, last_sync_at_iso: str | None = None) -> None:
        """Persist last successful customer sync timestamp in additional_info.

        Stored key: lastSyncAt (ISO-8601, UTC).
        """
        try:
            now_iso = last_sync_at_iso or datetime.now(timezone.utc).isoformat()
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            additional_info["lastSyncAt"] = now_iso
            additional_info["updatedAt"] = now_iso
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=setLastSyncAt")
            raise

    def setLastPaymentSyncAt(
            self,
            subscriber_id: str,
            service_id: str,
            last_sync_at_iso: str | None = None,
    ) -> None:
        try:
            now_iso = last_sync_at_iso or datetime.now(timezone.utc).isoformat()
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            additional_info["lastPaymentSyncAt"] = now_iso
            additional_info["updatedAt"] = now_iso
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=setLastPaymentSyncAt")
            raise


    def setLastVendorSyncAt(
            self,
            subscriber_id: str,
            service_id: str,
            last_sync_at_iso: str | None = None,
    ) -> None:
        """Persist last successful vendor sync timestamp in additional_info.

        Stored key: lastVendorSyncAt (ISO-8601, UTC).
        """
        try:
            now_iso = last_sync_at_iso or datetime.now(timezone.utc).isoformat()
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            additional_info["lastVendorSyncAt"] = now_iso
            additional_info["updatedAt"] = now_iso
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=setLastVendorSyncAt")
            raise


    def setLastAccountSyncAt(
            self,
            subscriber_id: str,
            service_id: str,
            last_sync_at_iso: str | None = None,
    ) -> None:
        """Persist last successful chart-of-accounts sync timestamp in additional_info.

        Stored key: lastAccountSyncAt (ISO-8601, UTC).
        """
        try:
            now_iso = last_sync_at_iso or datetime.now(timezone.utc).isoformat()
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            additional_info["lastAccountSyncAt"] = now_iso
            additional_info["updatedAt"] = now_iso
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=setLastAccountSyncAt")
            raise


    def setLastBillSyncAt(
            self,
            subscriber_id: str,
            service_id: str,
            last_sync_at_iso: str | None = None,
    ) -> None:
        """Persist last successful bill export/sync timestamp in additional_info.

        Stored key: lastBillSyncAt (ISO-8601, UTC).
        """
        try:
            now_iso = last_sync_at_iso or datetime.now(timezone.utc).isoformat()
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            additional_info["lastBillSyncAt"] = now_iso
            additional_info["updatedAt"] = now_iso
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=setLastBillSyncAt")
            raise


    def setLastBillPaymentSyncAt(
            self,
            subscriber_id: str,
            service_id: str,
            last_sync_at_iso: str | None = None,
    ) -> None:
        """Persist last successful bill payment sync timestamp in additional_info."""
        try:
            now_iso = last_sync_at_iso or datetime.now(timezone.utc).isoformat()
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            additional_info["lastBillPaymentSyncAt"] = now_iso
            additional_info["updatedAt"] = now_iso
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=setLastBillPaymentSyncAt")
            raise

    def setLastCustomerSyncAt(
            self,
            subscriber_id: str,
            service_id: str,
            last_sync_at_iso: str | None = None,
    ) -> None:
        """Persist last successful customer sync timestamp in additional_info.

        Stored key: lastCustomerSyncAt (ISO-8601, UTC).
        """
        try:
            now_iso = last_sync_at_iso or datetime.now(timezone.utc).isoformat()
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            additional_info["lastCustomerSyncAt"] = now_iso
            additional_info["updatedAt"] = now_iso
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=setLastCustomerSyncAt")
            raise

    def setLastProjectSyncAt(
            self,
            subscriber_id: str,
            service_id: str,
            last_sync_at_iso: str | None = None,
    ) -> None:
        """Persist last successful project sync timestamp in additional_info."""
        try:
            now_iso = last_sync_at_iso or datetime.now(timezone.utc).isoformat()
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            additional_info["lastProjectSyncAt"] = now_iso
            additional_info["updatedAt"] = now_iso
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=setLastProjectSyncAt")
            raise

    def setLastInvoiceSyncAt(
            self,
            subscriber_id: str,
            service_id: str,
            last_sync_at_iso: str | None = None,
    ) -> None:
        """Persist last successful invoice sync timestamp in additional_info."""
        try:
            now_iso = last_sync_at_iso or datetime.now(timezone.utc).isoformat()
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            additional_info["lastInvoiceSyncAt"] = now_iso
            additional_info["updatedAt"] = now_iso
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception:
            logger.exception("QuickBooks repository operation failed | method=setLastInvoiceSyncAt")
            raise

    def setLastEmployeeSyncAt(
            self,
            subscriber_id: str,
            service_id: str,
            last_sync_at_iso: str | None = None,
    ) -> None:
        """Persist last successful employee sync timestamp in additional_info."""
        try:
            now_iso = last_sync_at_iso or datetime.now(timezone.utc).isoformat()
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            additional_info["lastEmployeeSyncAt"] = now_iso
            additional_info["updatedAt"] = now_iso
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=setLastEmployeeSyncAt")
            raise

    def setLastServiceSyncAt(
            self,
            subscriber_id: str,
            service_id: str,
            last_sync_at_iso: str | None = None,
    ) -> None:
        """Persist last successful service-role sync timestamp in additional_info."""
        try:
            now_iso = last_sync_at_iso or datetime.now(timezone.utc).isoformat()
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            additional_info["lastServiceSyncAt"] = now_iso
            additional_info["updatedAt"] = now_iso
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=setLastServiceSyncAt")
            raise

    def setLastTimelogSyncAt(
            self,
            subscriber_id: str,
            service_id: str,
            last_sync_at_iso: str | None = None,
    ) -> None:
        """Persist last successful timelog sync timestamp in additional_info."""
        try:
            now_iso = last_sync_at_iso or datetime.now(timezone.utc).isoformat()
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            additional_info["lastTimelogSyncAt"] = now_iso
            additional_info["updatedAt"] = now_iso
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=setLastTimelogSyncAt")
            raise

    def updateConnectionTokens(
            self,
            subscriber_id: str,
            service_id: str,
            refresh_token: str,
            refresh_expires_at: str | None = None,
            access_expires_at: str | None = None,
            current_connection: Dict[str, Any] | None = None,
    ) -> None:
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            additional_info = dict(current_connection or self.getAdditionalInfo(subscriber_id, service_id) or {})
            additional_info[const.KEY_REFRESH_TOKEN] = refresh_token
            if refresh_expires_at:
                additional_info[const.KEY_REFRESH_EXPIRES_AT] = refresh_expires_at
            if access_expires_at:
                additional_info[const.KEY_ACCESS_EXPIRES_AT] = access_expires_at
            additional_info[const.KEY_STATUS] = const.STATUS_CONNECTED
            additional_info[const.KEY_RECONNECT_REQUIRED] = False
            additional_info[const.KEY_AUTH_ERROR] = None
            additional_info["updatedAt"] = now_iso
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=updateConnectionTokens")
            raise


    def markReconnectRequired(
            self,
            subscriber_id: str,
            service_id: str,
            error_code: str,
            reason: str,
            detail: str | None = None,
            current_connection: Dict[str, Any] | None = None,
    ) -> None:
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            additional_info = dict(current_connection or self.getAdditionalInfo(subscriber_id, service_id) or {})
            additional_info[const.KEY_STATUS] = const.STATUS_REAUTH_REQUIRED
            additional_info[const.KEY_RECONNECT_REQUIRED] = True
            additional_info[const.KEY_AUTH_ERROR] = {
                "code": error_code,
                "reason": reason,
                "detail": detail,
                "at": now_iso,
            }
            additional_info["updatedAt"] = now_iso
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=markReconnectRequired")
            raise


    def clearAuthError(self, subscriber_id: str, service_id: str) -> None:
        try:
            additional_info = self.getAdditionalInfo(subscriber_id, service_id) or {}
            changed = False
            if additional_info.get(const.KEY_RECONNECT_REQUIRED):
                additional_info[const.KEY_RECONNECT_REQUIRED] = False
                changed = True
            if additional_info.get(const.KEY_AUTH_ERROR) is not None:
                additional_info[const.KEY_AUTH_ERROR] = None
                changed = True
            if additional_info.get(const.KEY_STATUS) == const.STATUS_REAUTH_REQUIRED and additional_info.get(const.KEY_REFRESH_TOKEN):
                additional_info[const.KEY_STATUS] = const.STATUS_CONNECTED
                changed = True
            if changed:
                additional_info["updatedAt"] = datetime.now(timezone.utc).isoformat()
                self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=clearAuthError")
            raise

    # ------------------------------------------------------------------
    # QuickBooks Time connection (stored in the same service_subscriber row as the
    # Accounting connection, under time* keys — see constants.KEY_TIME_*). The
    # refresh token is encrypted via _CONNECTION_SECRET_KEYS like the Accounting one.
    # ------------------------------------------------------------------

    def saveTimeOauthState(self, subscriber_id: str, service_id: str, state: str, ttl_minutes: int = 10) -> None:
        """Persist only a keyed hash of the short-lived QuickBooks Time OAuth state."""
        try:
            now = datetime.now(timezone.utc)
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            additional_info.pop(const.KEY_TIME_STATE, None)
            additional_info.update({
                self._TIME_OAUTH_STATE_HASH_KEY: self._oauth_state_hash(state),
                "timeOauthStateCreatedAt": now.isoformat(),
                const.KEY_TIME_STATE_EXPIRES_AT: (now + timedelta(minutes=ttl_minutes)).isoformat(),
                const.KEY_TIME_STATE_USED_AT: None,
            })
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception:
            logger.exception("QuickBooks repository operation failed | method=saveTimeOauthState")
            raise

    def _consume_time_oauth_state(self, state: str):
        state_hash = self._oauth_state_hash(state)
        conn = postgres_service_connection()
        if conn is None:
            raise RuntimeError("Unable to connect to the service database")
        cur = conn.cursor()
        try:
            conn.set_session(autocommit=False)
            cur.execute(
                """
                SELECT subscriber_id, service_id, additional_info
                FROM service_subscriber
                WHERE substring(
                    CAST(additional_info AS TEXT)
                    FROM '"timeOauthStateHash"[[:space:]]*:[[:space:]]*"([^"]+)"'
                ) = %s
                FOR UPDATE
                LIMIT 1
                """,
                (state_hash,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Invalid state")

            subscriber_id, service_id, additional_info_raw = row
            additional_info = self.parseAdditionalInfo(additional_info_raw)
            if not hmac.compare_digest(
                str(additional_info.get(self._TIME_OAUTH_STATE_HASH_KEY) or ""), state_hash
            ):
                raise ValueError("Invalid state")

            if additional_info.get(const.KEY_TIME_STATE_USED_AT) is not None:
                conn.commit()
                return str(subscriber_id), str(service_id), True

            expires_at_raw = additional_info.get(const.KEY_TIME_STATE_EXPIRES_AT)
            if not expires_at_raw:
                raise ValueError("State expiry is missing")
            expires_at = datetime.fromisoformat(str(expires_at_raw).replace("Z", "+00:00"))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < datetime.now(timezone.utc):
                raise ValueError("State expired")

            additional_info[const.KEY_TIME_STATE_USED_AT] = datetime.now(timezone.utc).isoformat()
            stored_payload = json.dumps(
                self._encrypt_connection_secrets(additional_info), separators=(",", ":")
            )
            cur.execute(
                """
                UPDATE service_subscriber
                SET additional_info = %s
                WHERE subscriber_id = %s AND service_id = %s
                """,
                (stored_payload, subscriber_id, service_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError("OAuth state row could not be updated")
            conn.commit()
            return str(subscriber_id), str(service_id), False
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.exception("QuickBooks Time OAuth state validation failed")
            raise
        finally:
            cur.close()
            conn.close()

    def consumeTimeOauthState(self, state: str):
        return self._consume_time_oauth_state(state)

    def saveTimeConnectionInfo(
        self,
        subscriber_id: str,
        service_id: str,
        company_id: str | None,
        user_id: str | None,
        refresh_token: str,
        access_token: str | None = None,
        access_expires_at: str | None = None,
    ) -> None:
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            additional_info[const.KEY_TIME_COMPANY_ID] = company_id
            additional_info[const.KEY_TIME_USER_ID] = user_id
            additional_info[const.KEY_TIME_REFRESH_TOKEN] = refresh_token
            additional_info[const.KEY_TIME_ACCESS_TOKEN] = access_token
            additional_info[const.KEY_TIME_ACCESS_EXPIRES_AT] = access_expires_at
            additional_info[const.KEY_TIME_STATUS] = const.STATUS_CONNECTED
            additional_info[const.KEY_TIME_RECONNECT_REQUIRED] = False
            additional_info[const.KEY_TIME_AUTH_ERROR] = None
            additional_info[const.KEY_TIME_CONNECTED_AT] = additional_info.get(const.KEY_TIME_CONNECTED_AT) or now_iso
            additional_info["updatedAt"] = now_iso
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=saveTimeConnectionInfo")
            raise

    def updateTimeConnectionTokens(
        self,
        subscriber_id: str,
        service_id: str,
        refresh_token: str,
        access_token: str | None = None,
        access_expires_at: str | None = None,
        current_connection: Dict[str, Any] | None = None,
    ) -> None:
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            additional_info = dict(current_connection or self.getAdditionalInfo(subscriber_id, service_id) or {})
            additional_info[const.KEY_TIME_REFRESH_TOKEN] = refresh_token
            if access_token:
                additional_info[const.KEY_TIME_ACCESS_TOKEN] = access_token
            if access_expires_at:
                additional_info[const.KEY_TIME_ACCESS_EXPIRES_AT] = access_expires_at
            additional_info[const.KEY_TIME_STATUS] = const.STATUS_CONNECTED
            additional_info[const.KEY_TIME_RECONNECT_REQUIRED] = False
            additional_info[const.KEY_TIME_AUTH_ERROR] = None
            additional_info["updatedAt"] = now_iso
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=updateTimeConnectionTokens")
            raise

    def markTimeReconnectRequired(
        self,
        subscriber_id: str,
        service_id: str,
        error_code: str,
        reason: str,
        detail: str | None = None,
        current_connection: Dict[str, Any] | None = None,
    ) -> None:
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            additional_info = dict(current_connection or self.getAdditionalInfo(subscriber_id, service_id) or {})
            additional_info[const.KEY_TIME_STATUS] = const.STATUS_REAUTH_REQUIRED
            additional_info[const.KEY_TIME_RECONNECT_REQUIRED] = True
            additional_info[const.KEY_TIME_AUTH_ERROR] = {
                "code": error_code,
                "reason": reason,
                "detail": detail,
                "at": now_iso,
            }
            additional_info["updatedAt"] = now_iso
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=markTimeReconnectRequired")
            raise

    def disconnectTime(self, subscriber_id: str, service_id: str) -> None:
        try:
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            additional_info[const.KEY_TIME_STATUS] = const.STATUS_DISCONNECTED
            additional_info[const.KEY_TIME_RECONNECT_REQUIRED] = False
            additional_info[const.KEY_TIME_AUTH_ERROR] = None
            additional_info[const.KEY_TIME_REFRESH_TOKEN] = None
            additional_info[const.KEY_TIME_ACCESS_TOKEN] = None
            additional_info[const.KEY_TIME_ACCESS_EXPIRES_AT] = None
            additional_info[const.KEY_TIME_COMPANY_ID] = None
            additional_info[const.KEY_TIME_USER_ID] = None
            additional_info.pop(const.KEY_TIME_STATE, None)
            additional_info.pop(self._TIME_OAUTH_STATE_HASH_KEY, None)
            additional_info.pop("timeOauthStateCreatedAt", None)
            additional_info.pop(const.KEY_TIME_STATE_EXPIRES_AT, None)
            additional_info.pop(const.KEY_TIME_STATE_USED_AT, None)
            additional_info["updatedAt"] = datetime.now(timezone.utc).isoformat()
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=disconnectTime")
            raise

    def getTimeStatus(self, subscriber_id: str, service_id: str) -> Dict[str, Any]:
        try:
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            status = additional_info.get(const.KEY_TIME_STATUS)
            if not status:
                if additional_info.get(const.KEY_TIME_RECONNECT_REQUIRED):
                    status = const.STATUS_REAUTH_REQUIRED
                else:
                    status = const.STATUS_CONNECTED if additional_info.get(const.KEY_TIME_REFRESH_TOKEN) else const.STATUS_DISCONNECTED
            return {
                "status": status,
                "connected": status == const.STATUS_CONNECTED,
                "companyId": additional_info.get(const.KEY_TIME_COMPANY_ID),
                "companyIdPresent": bool(additional_info.get(const.KEY_TIME_COMPANY_ID)),
                "userId": additional_info.get(const.KEY_TIME_USER_ID),
                "accessExpiresAt": additional_info.get(const.KEY_TIME_ACCESS_EXPIRES_AT),
                "connectedAt": additional_info.get(const.KEY_TIME_CONNECTED_AT),
                "reconnectRequired": bool(additional_info.get(const.KEY_TIME_RECONNECT_REQUIRED)),
                "authError": additional_info.get(const.KEY_TIME_AUTH_ERROR),
                "updatedAt": additional_info.get("updatedAt"),
            }
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=getTimeStatus")
            raise

    def saveCapabilities(
        self,
        subscriber_id: str,
        service_id: str,
        capabilities: Dict[str, Any],
    ) -> None:
        """Persist detected QBO feature capabilities into additional_info."""
        try:
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            additional_info[const.KEY_CAPABILITIES] = capabilities
            additional_info["updatedAt"] = datetime.now(timezone.utc).isoformat()
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=saveCapabilities")
            raise

    def getCapabilities(self, subscriber_id: str, service_id: str) -> Dict[str, Any]:
        """Return stored QBO capabilities, or an empty dict if not yet fetched."""
        try:
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            return additional_info.get(const.KEY_CAPABILITIES) or {}
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=getCapabilities")
            raise

    def saveAutoSyncConfig(
        self,
        subscriber_id: str,
        service_id: str,
        enabled: bool,
    ) -> None:
        """Persist tenant-level QuickBooks automatic sync preference."""
        try:
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            additional_info[const.KEY_AUTO_SYNC] = {
                const.KEY_AUTO_SYNC_ENABLED: bool(enabled),
            }
            additional_info["updatedAt"] = datetime.now(timezone.utc).isoformat()
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception:
            logger.exception("QuickBooks repository operation failed | method=saveAutoSyncConfig")
            raise

    def getAutoSyncConfig(self, subscriber_id: str, service_id: str) -> Dict[str, Any]:
        """Return automatic sync settings, defaulting to off when none is stored."""
        try:
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            stored = additional_info.get(const.KEY_AUTO_SYNC) or {}
            enabled = const.DEFAULT_AUTO_SYNC_ENABLED
            if isinstance(stored, dict) and isinstance(stored.get(const.KEY_AUTO_SYNC_ENABLED), bool):
                enabled = stored[const.KEY_AUTO_SYNC_ENABLED]
            return {const.KEY_AUTO_SYNC_ENABLED: enabled}
        except Exception:
            logger.exception("QuickBooks repository operation failed | method=getAutoSyncConfig")
            raise

    def isAutoSyncEnabled(self, subscriber_id: str, service_id: str) -> bool:
        return bool(
            self.getAutoSyncConfig(subscriber_id, service_id).get(
                const.KEY_AUTO_SYNC_ENABLED,
                const.DEFAULT_AUTO_SYNC_ENABLED,
            )
        )

    def saveEmployeeSyncConfig(
        self,
        subscriber_id: str,
        service_id: str,
        config: Dict[str, Any],
    ) -> None:
        """Persist employee sync settings into additional_info["employee_sync"].

        Expected config shape::

            {
                "days_after_hire": 0,     # int, 0–365
                "employee_type":  "W2"    # "W2" | "1099"
            }
        """
        try:
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            additional_info[const.KEY_EMPLOYEE_SYNC] = config
            additional_info["updatedAt"] = datetime.now(timezone.utc).isoformat()
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=saveEmployeeSyncConfig")
            raise

    def getEmployeeSyncConfig(self, subscriber_id: str, service_id: str) -> Dict[str, Any]:
        """Return stored employee sync config, or an empty dict if not yet configured.

        Callers should apply their own defaults for absent keys.
        """
        try:
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            return additional_info.get(const.KEY_EMPLOYEE_SYNC) or {}
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=getEmployeeSyncConfig")
            raise

    def saveProjectSyncConfig(
        self,
        subscriber_id: str,
        service_id: str,
        config: Dict[str, Any],
    ) -> None:
        """Persist tenant project settings in service_subscriber.additional_info.

        The sync method remains in ``additional_info["project_sync"]``. The client
        company Realm ID is stored at the top level so it can be encrypted with the
        other tenant connection identifiers. Omitting the key preserves the current
        value; sending it as blank clears it.
        """
        try:
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            project_config = dict(config or {})
            marker = object()
            client_realm_id = project_config.pop(const.KEY_PROJECT_CLIENT_REALM_ID, marker)

            if client_realm_id is not marker:
                normalized_realm_id = str(client_realm_id or "").strip()
                if normalized_realm_id:
                    additional_info[const.KEY_PROJECT_CLIENT_REALM_ID] = normalized_realm_id
                else:
                    additional_info.pop(const.KEY_PROJECT_CLIENT_REALM_ID, None)

            additional_info[const.KEY_PROJECT_SYNC] = project_config
            additional_info["updatedAt"] = datetime.now(timezone.utc).isoformat()
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=saveProjectSyncConfig")
            raise

    def getProjectSyncConfig(self, subscriber_id: str, service_id: str) -> Dict[str, Any]:
        """Return the tenant project configuration without exposing provider credentials."""
        try:
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            stored_config = additional_info.get(const.KEY_PROJECT_SYNC) or {}
            config = dict(stored_config) if isinstance(stored_config, dict) else {}
            client_realm_id = str(
                additional_info.get(const.KEY_PROJECT_CLIENT_REALM_ID) or ""
            ).strip()
            if client_realm_id:
                config[const.KEY_PROJECT_CLIENT_REALM_ID] = client_realm_id
            return config
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=getProjectSyncConfig")
            raise

    def _build_service_role_account_config(
        self,
        account: Dict[str, Any],
        *,
        selected_by: str | None = None,
    ) -> Dict[str, Any]:
        external_id = str(account.get("external_id") or "").strip()
        if not external_id:
            raise ValueError("Selected chart of account is not synced to QuickBooks.")
        return {
            "chart_of_account_id": account.get("id"),
            "external_id": external_id,
            "name": str(account.get("name") or "").strip() or None,
            "account_type": str(account.get("account_type") or "").strip() or None,
            "detail_type": str(account.get("detail_type") or "").strip() or None,
            "selected_at": datetime.now(timezone.utc).isoformat(),
            "selected_by": str(selected_by or "").strip() or None,
        }

    def buildServiceRoleIncomeAccountConfig(
        self,
        account: Dict[str, Any],
        selected_by: str | None = None,
    ) -> Dict[str, Any]:
        """Build a normalized COA selection payload without persisting it.

        This is used for per-role COA assignments. Per-role selections are stored
        only in role_external_map.additional_info, while service_subscriber
        additional_info stores only the tenant/service default configuration.
        """
        return self._build_service_role_account_config(account, selected_by=selected_by)

    def saveServiceRoleIncomeAccountConfig(
        self,
        subscriber_id: str,
        service_id: str,
        account: Dict[str, Any],
        selected_by: str | None = None,
    ) -> Dict[str, Any]:
        """Persist only the default service-role COA in service_subscriber.additional_info.

        Per-role selected COA snapshots belong to role_external_map.additional_info.
        service_subscriber.additional_info is only the tenant/service configuration store.
        """
        try:
            selected_account = self._build_service_role_account_config(account, selected_by=selected_by)

            additional_info = self.getAdditionalInfo(subscriber_id, service_id) or {}
            service_role_sync = additional_info.get("service_role_sync")
            if not isinstance(service_role_sync, dict):
                service_role_sync = {}

            service_role_sync["qbo_service_income_account"] = selected_account
            service_role_sync.pop("qbo_service_income_account_by_role", None)

            additional_info["service_role_sync"] = service_role_sync
            additional_info["updatedAt"] = datetime.now(timezone.utc).isoformat()
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
            return selected_account
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=saveServiceRoleIncomeAccountConfig")
            raise

    def getServiceRoleIncomeAccountConfig(self, subscriber_id: str, service_id: str) -> Dict[str, Any]:
        """Return default service-role COA from service_subscriber.additional_info."""
        try:
            additional_info = self.getAdditionalInfo(subscriber_id, service_id) or {}
            service_role_sync = additional_info.get("service_role_sync") or {}
            if not isinstance(service_role_sync, dict):
                return {}
            account = service_role_sync.get("qbo_service_income_account") or {}
            return account if isinstance(account, dict) else {}
        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=getServiceRoleIncomeAccountConfig")
            raise

    def disconnect(self, subscriber_id: str, service_id: str) -> None:
        try:
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)
            additional_info.update({
                const.KEY_STATUS: const.STATUS_DISCONNECTED,
                const.KEY_RECONNECT_REQUIRED: False,
                const.KEY_AUTH_ERROR: None,
                const.KEY_REFRESH_TOKEN: None,
                getattr(const, "KEY_ACCESS_TOKEN", "accessToken"): None,
                const.KEY_REALM_ID: None,
                self._REALM_ID_HASH_KEY: None,
                const.KEY_PROJECT_CLIENT_REALM_ID: None,
                const.KEY_REFRESH_EXPIRES_AT: None,
                const.KEY_ACCESS_EXPIRES_AT: None,
                "oauthScopesRequested": [],
                "oauthScopesGranted": [],
                self._OAUTH_STATE_HASH_KEY: None,
                "oauthStateCreatedAt": None,
                "oauthStateExpiresAt": None,
                "oauthStateUsedAt": None,
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            })
            additional_info.pop("state", None)
            additional_info.pop("expiresAt", None)
            additional_info.pop("usedAt", None)
            self.updateAdditionalInfo(subscriber_id, service_id, additional_info)
        except Exception:
            logger.exception("QuickBooks repository operation failed | method=disconnect")
            raise


    def getStatus(self, subscriber_id: str, service_id: str) -> Dict[str, Any]:
        try:
            additional_info = self.getAdditionalInfo(subscriber_id, service_id)

            status = additional_info.get(const.KEY_STATUS)
            if not status:
                if additional_info.get(const.KEY_RECONNECT_REQUIRED):
                    status = const.STATUS_REAUTH_REQUIRED
                else:
                    status = const.STATUS_CONNECTED if additional_info.get(const.KEY_REFRESH_TOKEN) else const.STATUS_DISCONNECTED

            connected = status == const.STATUS_CONNECTED
            realm_id = additional_info.get(const.KEY_REALM_ID)

            return {
                "status": status,
                "connected": connected,
                "realmId": realm_id,
                "realmIdPresent": bool(realm_id),
                "env": additional_info.get(const.KEY_ENV),
                "refreshExpiresAt": additional_info.get(const.KEY_REFRESH_EXPIRES_AT),
                "accessExpiresAt": additional_info.get(const.KEY_ACCESS_EXPIRES_AT),
                "connectedAt": additional_info.get("connectedAt"),
                "lastSyncAt": additional_info.get("lastSyncAt"),
                "lastPaymentSyncAt": additional_info.get("lastPaymentSyncAt"),
                "lastVendorSyncAt": additional_info.get("lastVendorSyncAt"),
                "lastAccountSyncAt": additional_info.get("lastAccountSyncAt"),
                "lastBillSyncAt": additional_info.get("lastBillSyncAt"),
                "lastBillPaymentSyncAt": additional_info.get("lastBillPaymentSyncAt"),
                "lastCustomerSyncAt": additional_info.get("lastCustomerSyncAt"),
                "lastEmployeeSyncAt": additional_info.get("lastEmployeeSyncAt"),
                "lastProjectSyncAt": additional_info.get("lastProjectSyncAt"),
                "lastInvoiceSyncAt": additional_info.get("lastInvoiceSyncAt"),
                "lastServiceSyncAt": additional_info.get("lastServiceSyncAt"),
                "lastTimelogSyncAt": additional_info.get("lastTimelogSyncAt"),
                "updatedAt": additional_info.get("updatedAt"),
                "reconnectRequired": bool(additional_info.get(const.KEY_RECONNECT_REQUIRED)),
                "authError": additional_info.get(const.KEY_AUTH_ERROR),
                "capabilities": additional_info.get(const.KEY_CAPABILITIES),
                "projectSync": additional_info.get(const.KEY_PROJECT_SYNC),
                "oauthScopesRequested": additional_info.get("oauthScopesRequested") or [],
                "oauthScopesGranted": additional_info.get("oauthScopesGranted") or [],
            }

        except Exception as e:
            logger.exception("QuickBooks repository operation failed | method=getStatus")
            raise

class QboBlobResultStore:
    """Store scheduled_job JSON results in Azure Blob Storage.

    Container: <tenant_id>
    Blob name: QuickBooks/<Object>/<job_uuid>/result.json

    scheduled_job.result stores the blob path:
      <tenant_id>/QuickBooks/<Object>/<job_uuid>/result.json
    """

    OBJECT_DIR = {
        "customers": "Customer",
        "payments": "Payment",
        "vendors": "Vendor",
        "accounts": "Account",
        "bills": "Bill",
        "bill_payments": "BillPayment",
        "employees": "Employee",
        "projects": "Project",
        "services": "Services",
        "timelogs": "Timelogs",
        "invoices": "Invoice",
}

    def _blob_name(self, *, module: str, job_uuid: str) -> str:
        module = (module or "").strip().lower()
        job_uuid = (job_uuid or "").strip()

        obj_dir = self.OBJECT_DIR.get(module)
        if not obj_dir:
            raise ValueError(f"Unsupported module: {module}")
        if not job_uuid:
            raise ValueError("job_uuid is required")

        return f"QuickBooks/{obj_dir}/{job_uuid}/result.json"

    def put_json(self, *, tenant_id: str, module: str, job_uuid: str, payload: Dict[str, Any]) -> str:
        tenant_id = (tenant_id or "").strip().lower()
        if not tenant_id:
            raise ValueError("tenant_id is required")

        _ensure_container(tenant_id)

        blob_name = self._blob_name(module=module, job_uuid=job_uuid)
        bc = _blob_service_client().get_blob_client(container=tenant_id, blob=blob_name)

        body = json.dumps(payload or {}, separators=(",", ":"), default=str)
        bc.upload_blob(
            body,
            overwrite=True,
            content_settings=ContentSettings(content_type="application/json"),
        )

        # Store full path (includes tenant container)
        return f"{tenant_id}/{blob_name}"

    def get_json(self, *, tenant_id: str, stored_path: str) -> Dict[str, Any]:
        tenant_id = (tenant_id or "").strip().lower()
        stored_path = (stored_path or "").strip()
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if not stored_path:
            raise ValueError("stored_path is required")

        blob_name = stored_path
        prefix = f"{tenant_id}/"
        if stored_path.startswith(prefix):
            blob_name = stored_path[len(prefix):]

        bc = _blob_service_client().get_blob_client(container=tenant_id, blob=blob_name)
        raw = bc.download_blob().readall()
        return json.loads(raw.decode("utf-8"))



def _blob_service_client() -> BlobServiceClient:
    conn_str = os.getenv(AZURE_STORAGE_CONNECTION_STRING) or os.getenv("Provider_Connection_String")
    if not conn_str:
        raise RuntimeError(
            f"Azure Blob connection string not set. Please configure {AZURE_STORAGE_CONNECTION_STRING} (or Provider_Connection_String)."
        )
    return BlobServiceClient.from_connection_string(conn_str)


def _ensure_container(container_name: str) -> None:
    container_name = (container_name or "").strip().lower()
    if not container_name:
        raise ValueError("tenant_id (container_name) is required")

    cc = _blob_service_client().get_container_client(container_name)
    try:
        cc.create_container()
    except ResourceExistsError:
        return
    except Exception:
        return
