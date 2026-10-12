import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import text

from source.dal.db_session import service_session_scope

logger = logging.getLogger(__name__)

class ABCSupplyRepository:
    """Tenant-level ABC Supply connection store.

    ABC Supply credentials are tenant/account credentials. They must be stored
    on service_subscriber.additional_info, not in provider service_configuration
    and not in process environment variables.
    """

    _SERVICE_NAME_ENV = "ABC_SUPPLY_SERVICE_NAME"
    _DEFAULT_SERVICE_NAME = "ABC Supply"
    _ENCRYPTED_VALUE_PREFIX = "enc:v2:"
    _ENCRYPTION_CONTEXT_ENV = "ABC_SUPPLY_CONNECTION_ENCRYPTION_CONTEXT"
    _DEFAULT_ENCRYPTION_CONTEXT = "ABC Supply.connection"
    _SENSITIVE_KEYS = (
        "clientId",
        "clientSecret",
        "ClientId",
        "ClientSecret",
        "client_id",
        "client_secret",
        "shipToNumber",
        "billToNumber",
        "ship_to_number",
        "bill_to_number",
    )

    def _crypto(self):
        from source.Utils.DecryptionUtil import Decryption

        salt = os.getenv("SALT")
        if not salt:
            raise RuntimeError("SALT must be configured before storing ABC Supply credentials.")
        return Decryption(salt, os.getenv("IV") or "")

    def _encryption_context(self) -> str:
        return (os.getenv(self._ENCRYPTION_CONTEXT_ENV) or self._DEFAULT_ENCRYPTION_CONTEXT).strip()

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

    def _encrypt_connection_secrets(self, additional_info: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(additional_info or {})
        for key in self._SENSITIVE_KEYS:
            if payload.get(key):
                payload[key] = self._encrypt_secret(payload[key])
        return payload

    def _decrypt_connection_secrets(self, additional_info: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(additional_info or {})
        for key in self._SENSITIVE_KEYS:
            if payload.get(key):
                payload[key] = self._decrypt_secret(payload[key])
        return payload

    def parse_additional_info(self, value: Any) -> Dict[str, Any]:
        try:
            if value is None:
                logger.debug("[ABC-SUPPLY-REPO] parse_additional_info: value is None, returning {}")
                return {}
            if isinstance(value, dict):
                result = self._decrypt_connection_secrets(value)
                logger.debug(f"[ABC-SUPPLY-REPO] parse_additional_info: dict input, keys={list(result.keys())}, status={result.get('status')!r}")
                return result
            if isinstance(value, str):
                text = value.strip()
                if not text:
                    logger.debug("[ABC-SUPPLY-REPO] parse_additional_info: empty string, returning {}")
                    return {}
                try:
                    result = self._decrypt_connection_secrets(json.loads(text))
                    logger.debug(f"[ABC-SUPPLY-REPO] parse_additional_info: parsed JSON, keys={list(result.keys())}, status={result.get('status')!r}")
                    return result
                except Exception:
                    # Some legacy services stored plain URLs in additional_info.
                    return {}
            return {}
        except Exception:
            logger.exception("[ABCSupplyRepository.parse_additional_info] Error")
            raise

    def abc_service_name(self) -> str:
        return (os.getenv(self._SERVICE_NAME_ENV) or self._DEFAULT_SERVICE_NAME).strip()

    def get_abc_supply_service_id(self) -> str:
        """Resolve ABC Supply service id from service_master by service name.

        The UI should not pass service_id for ABC Supply. The backend owns this
        resolution so tenants cannot accidentally connect credentials against
        the wrong service_master row.
        """
        service_name = self.abc_service_name()
        logger.debug("[ABC-SUPPLY-REPO] get_abc_supply_service_id: querying service_master for name=%r", service_name)

        with service_session_scope() as session:
            row = session.execute(
                text(
                    """
                    SELECT id
                    FROM service_master
                    WHERE LOWER(name) = LOWER(:service_name)
                      AND (invalid IS NULL OR invalid = false)
                      AND (status IS NULL OR LOWER(status) = 'active')
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ),
                {"service_name": service_name},
            ).fetchone()

        if not row:
            raise ValueError(f"ABC Supply service_master row not found for name={service_name!r}")

        service_id = str(row[0])
        logger.debug("[ABC-SUPPLY-REPO] get_abc_supply_service_id: found service_id=%r", service_id)
        return service_id

    def resolve_service_id(self, service_id: Optional[str] = None) -> str:
        """Resolve the ABC Supply service id from service_master.

        Production standard: service_id is backend-owned and resolved from
        service_master.name. Public UI requests should not decide which
        service_id receives tenant credentials.

        ABC_SUPPLY_ALLOW_EXPLICIT_SERVICE_ID exists only for legacy/local tests
        and should remain false in production.
        """
        allow_explicit = (os.getenv("ABC_SUPPLY_ALLOW_EXPLICIT_SERVICE_ID", "false") or "").strip().lower()
        if allow_explicit in ("1", "true", "yes", "on") and service_id not in (None, ""):
            logger.debug(f"[ABC-SUPPLY-REPO] resolve_service_id: using explicit service_id={service_id!r} (ABC_SUPPLY_ALLOW_EXPLICIT_SERVICE_ID=true)")
            return str(service_id).strip()
        logger.debug(f"[ABC-SUPPLY-REPO] resolve_service_id: resolving from service_master (passed service_id={service_id!r})")
        return self.get_abc_supply_service_id()

    def _subscription_missing_message(self, subscriber_id: str, service_id: str) -> str:
        return (
            "ABC Supply service is not enabled for this tenant. "
            f"Enable service '{self.abc_service_name()}' for tenant {subscriber_id} before connecting credentials. "
            f"Resolved service_id={service_id}."
        )

    def ensure_subscription_enabled(self, subscriber_id: str, service_id: Optional[str] = None) -> str:
        """Validate that the tenant has ABC Supply enabled before any ABC API call.

        This keeps connection/save production-safe: the backend checks the FIELDCRAFT
        service subscription first, then validates external ABC credentials.
        """
        resolved_service_id = self.resolve_service_id(service_id)
        logger.debug(
            "[ABC-SUPPLY-REPO] ensure_subscription_enabled: checking subscriber_id=%r service_id=%r",
            subscriber_id,
            resolved_service_id,
        )

        with service_session_scope() as session:
            row = session.execute(
                text(
                    """
                    SELECT 1
                    FROM service_subscriber
                    WHERE subscriber_id = :subscriber_id
                      AND service_id = :service_id
                      AND (invalid IS NULL OR invalid = false)
                    LIMIT 1
                    """
                ),
                {"subscriber_id": subscriber_id, "service_id": resolved_service_id},
            ).fetchone()

        if not row:
            logger.info(
                "[ABC-SUPPLY-REPO] ensure_subscription_enabled: FAILED - no active subscription for subscriber_id=%r",
                subscriber_id,
            )
            raise ValueError(self._subscription_missing_message(subscriber_id, resolved_service_id))

        logger.debug(
            "[ABC-SUPPLY-REPO] ensure_subscription_enabled: OK - subscription active for subscriber_id=%r",
            subscriber_id,
        )
        return resolved_service_id

    def get_additional_info(self, subscriber_id: str, service_id: Optional[str] = None) -> Dict[str, Any]:
        resolved_service_id = self.resolve_service_id(service_id)
        logger.debug(
            "[ABC-SUPPLY-REPO] get_additional_info: querying service_subscriber for subscriber_id=%r service_id=%r",
            subscriber_id,
            resolved_service_id,
        )

        with service_session_scope() as session:
            row = session.execute(
                text(
                    """
                    SELECT additional_info
                    FROM service_subscriber
                    WHERE subscriber_id = :subscriber_id
                      AND service_id = :service_id
                      AND (invalid IS NULL OR invalid = false)
                    LIMIT 1
                    """
                ),
                {"subscriber_id": subscriber_id, "service_id": resolved_service_id},
            ).fetchone()

        if not row:
            logger.info(
                "[ABC-SUPPLY-REPO] get_additional_info: no row found for subscriber_id=%r",
                subscriber_id,
            )
            raise ValueError(self._subscription_missing_message(subscriber_id, resolved_service_id))

        logger.debug("[ABC-SUPPLY-REPO] get_additional_info: row found, parsing additional_info")
        return self.parse_additional_info(row[0])

    def update_additional_info(self, subscriber_id: str, service_id: Optional[str], additional_info: Dict[str, Any]) -> None:
        resolved_service_id = self.resolve_service_id(service_id)
        logger.debug(
            "[ABC-SUPPLY-REPO] update_additional_info: updating service_subscriber for subscriber_id=%r service_id=%r keys=%s",
            subscriber_id,
            resolved_service_id,
            list((additional_info or {}).keys()),
        )

        now_iso = datetime.now(timezone.utc).isoformat()
        payload = dict(additional_info or {})
        payload["updatedAt"] = now_iso
        encrypted_json = json.dumps(self._encrypt_connection_secrets(payload), separators=(",", ":"))

        with service_session_scope() as session:
            result = session.execute(
                text(
                    """
                    UPDATE service_subscriber
                    SET additional_info = :additional_info,
                        updated_at = NOW()
                    WHERE subscriber_id = :subscriber_id
                      AND service_id = :service_id
                      AND (invalid IS NULL OR invalid = false)
                    """
                ),
                {
                    "additional_info": encrypted_json,
                    "subscriber_id": subscriber_id,
                    "service_id": resolved_service_id,
                },
            )
            rows_updated = result.rowcount or 0

        if rows_updated == 0:
            logger.info(
                "[ABC-SUPPLY-REPO] update_additional_info: UPDATE affected 0 rows for subscriber_id=%r",
                subscriber_id,
            )
            raise ValueError(self._subscription_missing_message(subscriber_id, resolved_service_id))

        logger.debug("[ABC-SUPPLY-REPO] update_additional_info: committed, rows_updated=%s", rows_updated)

    def save_connection(
        self,
        subscriber_id: str,
        service_id: Optional[str],
        config: Dict[str, Any],
        validated_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        logger.debug(f"[ABC-SUPPLY-REPO] save_connection: subscriber_id={subscriber_id!r}, branchNumber={config.get('branch_number') or config.get('branchNumber')!r}, validated_at={validated_at!r}")
        now_iso = datetime.now(timezone.utc).isoformat()
        additional_info = self.get_additional_info(subscriber_id, service_id)
        additional_info.update(
            {
                "status": "connected",
                "clientId": config.get("client_id") or config.get("clientId"),
                "clientSecret": config.get("client_secret") or config.get("clientSecret"),
                "branchNumber": config.get("branch_number") or config.get("branchNumber"),
                "shipToNumber": config.get("ship_to_number") or config.get("shipToNumber"),
                "billToNumber": config.get("bill_to_number") or config.get("billToNumber"),
                "connectedAt": additional_info.get("connectedAt") or now_iso,
                "validatedAt": validated_at or now_iso,
                "authError": None,
                "reconnectRequired": False,
            }
        )
        self.update_additional_info(subscriber_id, service_id, additional_info)
        result = self.get_status(subscriber_id, service_id)
        logger.debug(f"[ABC-SUPPLY-REPO] save_connection: done, status={result.get('status')!r}, connected={result.get('connected')}")
        return result

    def disconnect(self, subscriber_id: str, service_id: Optional[str] = None, clear_credentials: bool = False) -> Dict[str, Any]:
        logger.debug(f"[ABC-SUPPLY-REPO] disconnect: subscriber_id={subscriber_id!r}, clear_credentials={clear_credentials}")
        now_iso = datetime.now(timezone.utc).isoformat()
        additional_info = self.get_additional_info(subscriber_id, service_id)
        additional_info["status"] = "disconnected"
        additional_info["disconnectedAt"] = now_iso
        additional_info["updatedAt"] = now_iso
        additional_info["reconnectRequired"] = True
        if clear_credentials:
            logger.debug(f"[ABC-SUPPLY-REPO] disconnect: clearing credentials for subscriber_id={subscriber_id!r}")
            for key in self._SENSITIVE_KEYS:
                additional_info.pop(key, None)
            for key in ("branchNumber", "validatedAt", "authError"):
                additional_info.pop(key, None)
        self.update_additional_info(subscriber_id, service_id, additional_info)
        result = self.get_status(subscriber_id, service_id)
        logger.debug(f"[ABC-SUPPLY-REPO] disconnect: done, status={result.get('status')!r}")
        return result

    def get_status(self, subscriber_id: str, service_id: Optional[str] = None) -> Dict[str, Any]:
        logger.debug(f"[ABC-SUPPLY-REPO] get_status: subscriber_id={subscriber_id!r}")
        try:
            additional_info = self.get_additional_info(subscriber_id, service_id)
        except Exception:
            raise

        resolved_service_id = self.resolve_service_id(service_id)
        status = (additional_info.get("status") or "disconnected").lower()
        logger.debug(f"[ABC-SUPPLY-REPO] get_status: status={status!r}, clientIdPresent={bool(additional_info.get('clientId'))}, branchNumber={additional_info.get('branchNumber')!r}")
        return {
            "connected": status == "connected",
            "serviceId": resolved_service_id,
            "status": status,
            "branchNumber": additional_info.get("branchNumber") or additional_info.get("branch_number"),
            "clientIdPresent": bool(additional_info.get("clientId") or additional_info.get("ClientId") or additional_info.get("client_id")),
            "clientSecretPresent": bool(additional_info.get("clientSecret") or additional_info.get("ClientSecret") or additional_info.get("client_secret")),
            "shipToNumber": additional_info.get("shipToNumber") or additional_info.get("ship_to_number"),
            "billToNumberPresent": bool(additional_info.get("billToNumber") or additional_info.get("bill_to_number")),
            "connectedAt": additional_info.get("connectedAt"),
            "validatedAt": additional_info.get("validatedAt"),
            "updatedAt": additional_info.get("updatedAt"),
            "disconnectedAt": additional_info.get("disconnectedAt"),
            "reconnectRequired": bool(additional_info.get("reconnectRequired")),
            "authError": additional_info.get("authError"),
        }
