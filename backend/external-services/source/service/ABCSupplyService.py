"""ABC Supply external integration service.

Retrieve ABC Supply product catalog/account materials and pricing.

Production credential model:
- ABC Supply client credentials are tenant/account-specific.
- Credentials are stored encrypted in service_subscriber.additional_info.
- Environment variables are only allowed as a local/test fallback when explicitly enabled.
"""

import base64
import logging
import os
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

from source.Utils import constants as const
from source.dal.ABCSupplyRepository import ABCSupplyRepository

load_dotenv()

logger = logging.getLogger(__name__)


class ABCSupplyConfigError(Exception):
    """Raised when ABC Supply integration configuration is missing or invalid."""


class ABCSupplyAPIError(Exception):
    """Raised when ABC Supply API returns a non-successful response."""


class ABCSupplyAuthError(ABCSupplyAPIError):
    """Raised when ABC Supply rejects the provided client credentials (invalid_client/invalid_grant).

    Distinct from ABCSupplyAPIError so callers (API layer) can tell "your credentials are
    wrong" apart from "ABC Supply's servers are having a transient problem" — both were
    previously indistinguishable, surfacing a misleading "temporarily unavailable" message
    for bad credentials.
    """


class ABCSupplyService:
    """Thin client for ABC Supply Account, Product, and Pricing APIs."""

    _class_token_cache: Dict[str, Any] = {}

    _RETRYABLE_STATUS_CODES = {502, 503, 504}
    _MAX_ATTEMPTS = 3
    _RETRY_BASE_DELAY_SECONDS = 0.3

    DEFAULT_AUTH_BASE_PRODUCTION = "https://auth.partners.abcsupply.com/oauth2/ausvvp0xuwGKLenYy357/v1"
    DEFAULT_AUTH_BASE_SANDBOX = "https://auth.partners.abcsupply.com/oauth2/ausvvp0xuwGKLenYy357/v1"
    DEFAULT_API_BASE_PRODUCTION = "https://partners.abcsupply.com"
    DEFAULT_API_BASE_SANDBOX = "https://partners-sb.abcsupply.com"
    DEFAULT_SCOPES = "product.read pricing.read account.read location.read"

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        repository: Optional[ABCSupplyRepository] = None,
    ):
        self.session = session or requests.Session()
        self.repository = repository or ABCSupplyRepository()
        if session is not None and not isinstance(session, requests.Session):
            # Keep test/mock sessions isolated while production instances share tokens.
            self._token_cache: Dict[str, Any] = {}
        else:
            self._token_cache = self.__class__._class_token_cache

    def get_abc_config(
        self,
        tenant_id: Optional[str] = None,
        service_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Load ABC Supply tenant connection from service_subscriber.additional_info.

        Production callers pass tenant_id only. service_id is resolved inside the
        repository from service_master.name = "ABC Supply".
        """
        try:
            if tenant_id:
                logger.debug(f"[ABC-SUPPLY] get_abc_config: loading from DB for tenant_id={tenant_id!r}")
                resolved_service_id = self.repository.resolve_service_id(service_id)
                logger.debug(f"[ABC-SUPPLY] get_abc_config: resolved service_id={resolved_service_id!r}")
                info = self.repository.get_additional_info(str(tenant_id), resolved_service_id)
                status = str(info.get("status") or "").strip().lower()
                logger.debug(f"[ABC-SUPPLY] get_abc_config: connection status={status!r}, clientId_present={bool(info.get('clientId'))}, branchNumber={info.get('branchNumber')!r}")
                if status != "connected":
                    raise ABCSupplyConfigError("ABC Supply is not connected for this tenant.")
                config = {
                    "clientId": info.get("clientId") or info.get(const.KEY_CLIENT_ID),
                    "clientSecret": info.get("clientSecret") or info.get(const.KEY_CLIENT_SECRET),
                    "branchNumber": info.get("branchNumber"),
                    "shipToNumber": info.get("shipToNumber"),
                    "billToNumber": info.get("billToNumber"),
                }
                return self._normalize_config(config)

            if self._allow_env_fallback():
                logger.debug(f"[ABC-SUPPLY] get_abc_config: no tenant_id, using env fallback (ABC_SUPPLY_ALLOW_ENV_FALLBACK=true)")
                config = {
                    "client_id": os.getenv("ABC_SUPPLY_CLIENT_ID"),
                    "client_secret": os.getenv("ABC_SUPPLY_CLIENT_SECRET"),
                    "env": os.getenv("ABC_SUPPLY_ENV", "production"),
                    "branch_number": os.getenv("ABC_SUPPLY_BRANCH_NUMBER"),
                    "ship_to_number": os.getenv("ABC_SUPPLY_SHIP_TO_NUMBER"),
                    "bill_to_number": os.getenv("ABC_SUPPLY_BILL_TO_NUMBER") or os.getenv("ABC_SUPPLY_SHIP_TO_NUMBER"),
                    "scopes": os.getenv("ABC_SUPPLY_SCOPES", self.DEFAULT_SCOPES),
                }
                return self._normalize_config(config)

            raise ABCSupplyConfigError("tenant_id is required for ABC Supply connection lookup")

        except ABCSupplyConfigError:
            raise
        except Exception as e:
            logger.exception("[ABCSupplyService.get_abc_config] Error loading ABC Supply config for tenant_id=%s", tenant_id)
            raise

    def _allow_env_fallback(self) -> bool:
        raw = (os.getenv("ABC_SUPPLY_ALLOW_ENV_FALLBACK", "false") or "").strip().lower()
        return raw in ("1", "true", "yes", "on")

    def _normalize_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        client_id = (
            config.get(const.KEY_CLIENT_ID)
            or config.get("clientId")
            or config.get("client_id")
            or config.get("ABC_CLIENT_ID")
        )
        client_secret = (
            config.get(const.KEY_CLIENT_SECRET)
            or config.get("ClientKey")
            or config.get("clientSecret")
            or config.get("client_secret")
            or config.get("ABC_CLIENT_SECRET")
        )

        if not client_id:
            raise ABCSupplyConfigError("ABC Supply Client ID is required")
        if not client_secret:
            raise ABCSupplyConfigError("ABC Supply Client Secret is required")

        env = str(os.getenv("ABC_SUPPLY_ENV", "production")).strip().lower()
        if env not in ("sandbox", "production", "prod"):
            raise ABCSupplyConfigError("ABC Supply Env must be either sandbox or production")
        if env == "prod":
            env = "production"

        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "env": env,
            "branch_number": config.get("BranchNumber") or config.get("branchNumber") or config.get("branch_number"),
            "ship_to_number": config.get("ShipToNumber") or config.get("shipToNumber") or config.get("ship_to_number"),
            "bill_to_number": config.get("BillToNumber") or config.get("billToNumber") or config.get("bill_to_number"),
            "scopes": os.getenv("ABC_SUPPLY_SCOPES", self.DEFAULT_SCOPES),
        }

    def _auth_base_url(self, env: str) -> str:
        if env == "production":
            return os.getenv("ABC_SUPPLY_AUTH_BASE_PRODUCTION", self.DEFAULT_AUTH_BASE_PRODUCTION).rstrip("/")
        return os.getenv("ABC_SUPPLY_AUTH_BASE_SANDBOX", self.DEFAULT_AUTH_BASE_SANDBOX).rstrip("/")

    def _api_base_url(self, env: str) -> str:
        if env == "production":
            return os.getenv("ABC_SUPPLY_API_BASE_PRODUCTION", self.DEFAULT_API_BASE_PRODUCTION).rstrip("/")
        return os.getenv("ABC_SUPPLY_API_BASE_SANDBOX", self.DEFAULT_API_BASE_SANDBOX).rstrip("/")

    def _cache_key(self, config: Dict[str, Any]) -> str:
        return f"{config.get('env')}:{config.get('client_id')}:{config.get('scopes')}"

    def get_access_token(self, config: Dict[str, Any], force_refresh: bool = False) -> str:
        cache_key = self._cache_key(config)
        if force_refresh:
            self._token_cache.pop(cache_key, None)

        cached = self._token_cache.get(cache_key) or {}
        if cached.get("access_token") and cached.get("expires_at", 0) > time.time() + 60:
            return cached["access_token"]

        token_url = f"{self._auth_base_url(config['env'])}/token"
        basic = base64.b64encode(f"{config['client_id']}:{config['client_secret']}".encode("utf-8")).decode("utf-8")
        headers = {
            "Authorization": f"Basic {basic}",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "grant_type": "client_credentials",
            "scope": config.get("scopes") or self.DEFAULT_SCOPES,
        }

        logger.info("[ABC-SUPPLY] requesting access token env=%s scopes=%s", config.get("env"), data.get("scope"))
        response = self._request_with_retry(
            "POST", token_url, request_label="token", headers=headers, data=data, timeout=30
        )
        try:
            payload = self._parse_response(response, request_label="token")
        except ABCSupplyAPIError as exc:
            if response.status_code in (400, 401):
                raise ABCSupplyAuthError(
                    "ABC Supply rejected the provided Client ID or Client Secret."
                ) from exc
            raise
        access_token = payload.get("access_token")
        if not access_token:
            raise ABCSupplyAPIError("ABC Supply token response did not include access_token")

        expires_in = int(payload.get("expires_in") or 1800)
        logger.info("[ABC-SUPPLY] access token obtained env=%s expires_in=%ss", config.get("env"), expires_in)
        self._token_cache[cache_key] = {
            "access_token": access_token,
            "expires_at": time.time() + expires_in,
        }
        return access_token

    def _headers(self, token: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _response_payload(response: requests.Response) -> Dict[str, Any]:
        try:
            payload = response.json() if response.content else {}
        except ValueError:
            payload = {"raw": response.text}
        return payload if isinstance(payload, dict) else {"data": payload}

    def _is_access_token_rejected(self, response: requests.Response) -> bool:
        if response.status_code != 401:
            return False

        payload = self._response_payload(response)
        auth_header = str(getattr(response, "headers", {}).get("WWW-Authenticate", ""))
        message = f"{payload} {auth_header}".lower()

        return "token" in message and any(
            marker in message
            for marker in ("revoked", "expired", "invalid", "invalid_token")
        )

    def _request_authorized_api(
        self,
        config: Dict[str, Any],
        method: str,
        url: str,
        request_label: str,
        **kwargs: Any,
    ) -> requests.Response:
        token = self.get_access_token(config)
        response = self._request_with_retry(
            method,
            url,
            request_label=request_label,
            headers=self._headers(token),
            **kwargs,
        )

        if not self._is_access_token_rejected(response):
            return response

        logger.warning(
            "[ABC-SUPPLY] access token rejected label=%s; refreshing token and retrying once",
            request_label,
        )
        fresh_token = self.get_access_token(config, force_refresh=True)
        return self._request_with_retry(
            method,
            url,
            request_label=request_label,
            headers=self._headers(fresh_token),
            **kwargs,
        )

    def _parse_response(self, response: requests.Response, request_label: Optional[str] = None) -> Dict[str, Any]:
        payload = self._response_payload(response)

        logger.info(
            "[ABC-SUPPLY] response label=%s status=%s content_length=%s",
            request_label,
            response.status_code,
            len(response.content),
        )

        if not 200 <= response.status_code < 300:
            logger.error(
                "[ABCSupplyService] ABC API error label=%s status=%s response=%s",
                request_label,
                response.status_code,
                payload,
            )
            raise ABCSupplyAPIError(
                f"ABC Supply API error label={request_label} status={response.status_code} response={payload}"
            )
        if not isinstance(payload, dict):
            return {"data": payload}
        return payload


    def connect_tenant(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        branch_number: Optional[str] = None,
        ship_to_number: Optional[str] = None,
        bill_to_number: Optional[str] = None,
        service_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        logger.debug(f"[ABC-SUPPLY] connect_tenant: tenant_id={tenant_id!r}, branch_number={branch_number!r}, ship_to_number={ship_to_number!r}")
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if hasattr(self.repository, "ensure_subscription_enabled"):
            logger.debug(f"[ABC-SUPPLY] connect_tenant: checking subscription for tenant_id={tenant_id!r}")
            resolved_service_id = self.repository.ensure_subscription_enabled(str(tenant_id), service_id)
            logger.debug(f"[ABC-SUPPLY] connect_tenant: subscription OK, service_id={resolved_service_id!r}")
        else:
            resolved_service_id = self.repository.resolve_service_id(service_id)
            logger.debug(f"[ABC-SUPPLY] connect_tenant: resolved service_id={resolved_service_id!r} (no subscription check)")

        config = self._normalize_config(
            {
                "clientId": client_id,
                "clientSecret": client_secret,
                "branchNumber": branch_number,
                "shipToNumber": ship_to_number,
                "billToNumber": bill_to_number or ship_to_number,
            }
        )
        logger.debug(f"[ABC-SUPPLY] connect_tenant: config normalized, env={config.get('env')!r}, validating credentials via token request...")

        # Token request proves the tenant/account credentials are valid. Force a
        # fresh token here so reconnect/save validates the submitted secret even
        # when a previous token exists for the same client id.
        self._token_cache.pop(self._cache_key(config), None)
        self.get_access_token(config)
        logger.debug(f"[ABC-SUPPLY] connect_tenant: credentials validated OK, saving connection for tenant_id={tenant_id!r}")
        validated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        result = self.repository.save_connection(str(tenant_id), resolved_service_id, config, validated_at=validated_at)
        logger.debug(f"[ABC-SUPPLY] connect_tenant: connection saved, status={result.get('status')!r}, connected={result.get('connected')}")
        return result

    def get_connection_status(self, tenant_id: str, service_id: Optional[str] = None) -> Dict[str, Any]:
        logger.debug(f"[ABC-SUPPLY] get_connection_status: tenant_id={tenant_id!r}")
        if not tenant_id:
            raise ValueError("tenant_id is required")
        resolved_service_id = self.repository.resolve_service_id(service_id)
        result = self.repository.get_status(str(tenant_id), resolved_service_id)
        logger.debug(f"[ABC-SUPPLY] get_connection_status: status={result.get('status')!r}, connected={result.get('connected')}, reconnectRequired={result.get('reconnectRequired')}")
        return result

    def disconnect_tenant(
        self,
        tenant_id: str,
        clear_credentials: bool = False,
        service_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        logger.debug(f"[ABC-SUPPLY] disconnect_tenant: tenant_id={tenant_id!r}, clear_credentials={clear_credentials}")
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self._token_cache.clear()
        logger.debug(f"[ABC-SUPPLY] disconnect_tenant: token cache cleared")
        resolved_service_id = self.repository.resolve_service_id(service_id)
        result = self.repository.disconnect(str(tenant_id), resolved_service_id, clear_credentials=clear_credentials)
        logger.debug(f"[ABC-SUPPLY] disconnect_tenant: done, status={result.get('status')!r}")
        return result

    def get_config_status(
        self,
        tenant_id: Optional[str] = None,
        service_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return non-secret config/status values for troubleshooting."""
        logger.debug(f"[ABC-SUPPLY] get_config_status: tenant_id={tenant_id!r}")
        if tenant_id:
            resolved_service_id = self.repository.resolve_service_id(service_id)
            status = self.repository.get_status(str(tenant_id), resolved_service_id)
            if not status.get("connected"):
                return status
            config = self.get_abc_config(tenant_id, resolved_service_id)
        else:
            config = self.get_abc_config(tenant_id, service_id)
            status = {
                "connected": True,
                "status": "env_fallback",
                "clientIdPresent": bool(config.get("client_id")),
                "clientSecretPresent": bool(config.get("client_secret")),
            }

        return {
            **status,
            "branchNumber": config.get("branch_number"),
            "shipToNumber": config.get("ship_to_number"),
            "billToNumberPresent": bool(config.get("bill_to_number")),
        }

    @staticmethod
    def _clean_label(value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        if "  -  " in text:
            return text.split("  -  ", 1)[1].strip()
        if " - " in text and text.split(" - ", 1)[0].strip().isdigit():
            return text.split(" - ", 1)[1].strip()
        return text

    @staticmethod
    def _deep_get(source: Dict[str, Any], path: str, default: Any = None) -> Any:
        current: Any = source
        for part in path.split("."):
            if not isinstance(current, dict):
                return default
            current = current.get(part)
            if current is None:
                return default
        return current

    def _first_value(self, source: Dict[str, Any], paths: List[str]) -> Any:
        for path in paths:
            value = self._deep_get(source, path) if "." in path else source.get(path)
            if value not in (None, "", []):
                return value
        return None

    def _extract_items(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
        candidates = [
            response.get("items"),
            response.get("results"),
            self._deep_get(response, "data.items"),
            self._deep_get(response, "data.results"),
        ]
        for candidate in candidates:
            if isinstance(candidate, list):
                return candidate
        return response if isinstance(response, list) else []

    def _extract_total(self, response: Dict[str, Any], items: List[Dict[str, Any]]) -> int:
        return (
            self._deep_get(response, "pagination.totalCount")
            or self._deep_get(response, "pagination.totalItems")
            or self._deep_get(response, "pagination.total")
            or response.get("totalCount")
            or response.get("totalItems")
            or response.get("total")
            or len(items)
        )

    def _preferred_uom(self, item: Dict[str, Any]) -> Dict[str, str]:
        uoms = item.get("uoms") or []
        if not isinstance(uoms, list):
            uoms = []

        selected = None
        for uom in uoms:
            if str(uom.get("description", "")).lower() == "stocking":
                selected = uom
                break
        if selected is None and uoms:
            selected = uoms[0]

        if selected:
            code = str(selected.get("code") or "").strip()
            name = str(selected.get("name") or "").strip()
            return {
                "code": code,
                "name": name,
                "display": f"{code} - {name}" if code and name else code or name,
                "type": str(selected.get("description") or "").strip(),
            }

        raw = item.get("uom") or item.get("unitOfMeasure") or item.get("sellingUnit") or ""
        raw_text = str(raw).strip()
        raw_code = raw_text.split(" - ", 1)[0].strip() if raw_text else ""
        return {"code": raw_code, "name": raw_text, "display": raw_text, "type": ""}

    def _normalize_uom_code(self, uom: Any) -> str:
        if uom is None:
            return ""
        if isinstance(uom, dict):
            return str(uom.get("code") or uom.get("uom") or uom.get("name") or "").strip()
        text = str(uom).strip()
        return text.split(" - ", 1)[0].strip() if " - " in text else text

    def _branch_availability_label(self, item: Dict[str, Any], branch_number: Optional[str]) -> str:
        if item.get("isAvailableForBranch") is True:
            return f"Available at Branch {item.get('branchNumber') or branch_number}" if (item.get("branchNumber") or branch_number) else "Available"
        if item.get("isAvailableForBranch") is False:
            return f"Not available at Branch {item.get('branchNumber') or branch_number}" if (item.get("branchNumber") or branch_number) else "Not available"

        branches = item.get("branches") or []
        if not isinstance(branches, list) or not branches:
            return ""

        if branch_number:
            branch_text = str(branch_number)
            for branch in branches:
                if str(branch.get("number")) == branch_text:
                    return f"Available at Branch {branch_text}"
            return f"Available at {len(branches)} branch(es)"
        return f"Available at {len(branches)} branch(es)"

    def _normalize_item(self, item: Dict[str, Any], branch_number: Optional[str] = None) -> Dict[str, Any]:
        if not isinstance(item, dict):
            return {"rawItem": item}

        uom = self._preferred_uom(item)
        manufacturer = self._first_value(
            item,
            [
                "supplierName",
                "manufacturerName",
                "manufacturer",
                "brand",
                "hierarchy.productGroup.category.productType.materialComposition.warranty.brandLine.label",
                "hierarchy.productGroup.category.productType.materialComposition.warranty.brandLine.description",
            ],
        )
        category = self._first_value(
            item,
            [
                "categoryName",
                "category",
                "productType",
                "hierarchy.productGroup.category.label",
                "hierarchy.productGroup.category.description",
                "hierarchy.productGroup.category.name",
            ],
        )
        product_type = self._first_value(
            item,
            [
                "hierarchy.productGroup.category.productType.label",
                "hierarchy.productGroup.category.productType.description",
                "hierarchy.productGroup.category.productType.name",
            ],
        )
        material_type = self._first_value(
            item,
            [
                "hierarchy.productGroup.category.productType.materialComposition.label",
                "hierarchy.productGroup.category.productType.materialComposition.description",
                "hierarchy.productGroup.category.productType.materialComposition.name",
            ],
        )

        normalized = dict(item)
        normalized.update(
            {
                "itemNumber": item.get("itemNumber") or item.get("sku") or item.get("externalId"),
                "itemDescription": item.get("itemDescription") or item.get("description") or item.get("name"),
                "manufacturerName": self._clean_label(manufacturer),
                "supplierName": self._clean_label(item.get("supplierName") or manufacturer),
                "categoryName": self._clean_label(category),
                "productTypeName": self._clean_label(product_type),
                "materialTypeName": self._clean_label(material_type),
                "uom": uom.get("code"),
                "uomName": uom.get("name"),
                "uomDisplay": uom.get("display"),
                "uomType": uom.get("type"),
                "branchAvailability": self._branch_availability_label(item, branch_number),
                "available": bool(item.get("isAvailableForBranch")) if "isAvailableForBranch" in item else bool(item.get("branches")),
                "rawItem": item,
            }
        )
        return normalized

    def _normalize_items_response(
        self,
        response: Dict[str, Any],
        branch_number: Optional[str] = None,
    ) -> Dict[str, Any]:
        items = self._extract_items(response)
        normalized_items = [self._normalize_item(item, branch_number) for item in items]
        output = dict(response) if isinstance(response, dict) else {"raw": response}
        output["items"] = normalized_items
        total = self._extract_total(response, items) if isinstance(response, dict) else len(normalized_items)
        output["totalCount"] = total
        output["normalized"] = True
        logger.debug(f"[ABC-SUPPLY] _normalize_items_response: {len(normalized_items)} items normalized, totalCount={total}")
        return output

    def _normalize_price_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        lines = response.get("lines") or self._deep_get(response, "data.lines") or []
        normalized_lines = []
        for line in lines:
            if not isinstance(line, dict):
                continue
            currency_obj = line.get("currency") if isinstance(line.get("currency"), dict) else {}
            currency_code = currency_obj.get("code") or line.get("currency") or "USD"
            unit_price = (
                line.get("unitPrice")
                or line.get("netPrice")
                or line.get("customerPrice")
                or line.get("branchPrice")
                or line.get("price")
            )
            normalized_line = dict(line)
            normalized_line.update(
                {
                    "unitPrice": unit_price,
                    "netPrice": unit_price,
                    "price": unit_price,
                    "currencyCode": currency_code,
                    "currencySymbol": currency_obj.get("symbol") or "$",
                    "statusCode": self._deep_get(line, "status.code"),
                    "statusMessage": self._deep_get(line, "status.message"),
                }
            )
            normalized_lines.append(normalized_line)
        output = dict(response)
        output["lines"] = normalized_lines
        output["normalized"] = True
        return output


    def search_accounts(
        self,
        tenant_id: Optional[str] = None,
        service_id: Optional[str] = None,
        account_type: str = "Ship-To",
        account_number: Optional[str] = None,
        state: Optional[str] = None,
        storefront: Optional[str] = "abc",
        items_per_page: int = 20,
        page_number: int = 1,
    ) -> Dict[str, Any]:
        logger.debug(f"[ABC-SUPPLY] search_accounts: account_type={account_type}, account_number={account_number}, state={state}, storefront={storefront}, page={page_number}")
        config = self.get_abc_config(tenant_id, service_id)
        filters: List[Dict[str, Any]] = []

        if account_number:
            filters.append({"key": "number", "condition": "equals", "values": [str(account_number)]})
        elif account_type:
            filters.append({"key": "accountType", "condition": "equals", "values": [account_type]})

        if state:
            filters.append({"key": "address.state", "condition": "equals", "values": [state], "joinCondition": "and"})
        elif storefront:
            filters.append({"key": "storefront", "condition": "equals", "values": [storefront], "joinCondition": "and"})

        self._validate_filter_limit(filters, "ABC Supply account search")
        payload = {
            "filters": filters,
            "pagination": {
                "itemsPerPage": items_per_page,
                "pageNumber": page_number,
            },
        }
        return self._post(config=config, path="/api/account/v1/search/accounts", payload=payload)

    def search_items(
        self,
        tenant_id: Optional[str] = None,
        service_id: Optional[str] = None,
        query: Optional[str] = None,
        item_number: Optional[str] = None,
        branch_number: Optional[str] = None,
        items_per_page: int = 20,
        page_number: int = 1,
        embed_branches: bool = False,
        include_family_items: bool = False,
        raw_filters: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        logger.debug(f"[ABC-SUPPLY] search_items: query={query!r}, item_number={item_number!r}, branch_number={branch_number!r}, page={page_number}, items_per_page={items_per_page}")
        config = self.get_abc_config(tenant_id, service_id)
        branch = branch_number or config.get("branch_number")
        payload = self._build_search_items_payload(
            query=query,
            item_number=item_number,
            branch_number=branch,
            items_per_page=items_per_page,
            page_number=page_number,
            embed_branches=embed_branches,
            raw_filters=raw_filters,
        )
        params = {"familyItems": "true"} if include_family_items else None
        response = self._post(
            config=config,
            path="/api/product/v1/search/items",
            payload=payload,
            params=params,
        )
        return self._normalize_items_response(response, branch)

    def _build_search_items_payload(
        self,
        query: Optional[str],
        item_number: Optional[str],
        branch_number: Optional[str],
        items_per_page: int,
        page_number: int,
        embed_branches: bool,
        raw_filters: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        if items_per_page < 1 or items_per_page > 1000:
            raise ValueError("items_per_page must be between 1 and 1000")
        if page_number < 1:
            raise ValueError("page_number must be greater than 0")

        filters: List[Dict[str, Any]] = list(raw_filters or [])
        if query:
            filters.append(
                {
                    "key": "itemDescription",
                    "condition": "contains",
                    "values": [query],
                    "joinCondition": "and" if filters else None,
                }
            )
        if item_number:
            filters.append(
                {
                    "key": "itemNumber",
                    "condition": "equals",
                    "values": [item_number],
                    "joinCondition": "and" if filters else None,
                }
            )
        if branch_number:
            filters.append(
                {
                    "key": "branchNumber",
                    "condition": "equals",
                    "values": [str(branch_number)],
                    "joinCondition": "and" if filters else None,
                }
            )

        if not filters:
            raise ValueError("At least one of query, item_number, branch_number, or raw_filters is required")

        self._validate_filter_limit(filters, "ABC Supply item search")
        payload: Dict[str, Any] = {
            "filters": filters,
            "pagination": {
                "itemsPerPage": items_per_page,
                "pageNumber": page_number,
            },
        }
        if embed_branches:
            payload["embed"] = ["branches"]
        return payload

    @staticmethod
    def _validate_filter_limit(filters: List[Dict[str, Any]], search_name: str) -> None:
        if len(filters) > 2:
            raise ValueError(f"{search_name} supports at most 2 filters per request; received {len(filters)}.")

    def get_all_items(
        self,
        tenant_id: Optional[str] = None,
        service_id: Optional[str] = None,
        items_per_page: int = 100,
        page_number: int = 1,
        embed_branches: bool = False,
        embed_variations: bool = False,
        include_family_items: bool = False,
        since_last_modified_date_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        logger.debug(f"[ABC-SUPPLY] get_all_items: page={page_number}, items_per_page={items_per_page}, since={since_last_modified_date_time!r}")
        config = self.get_abc_config(tenant_id, service_id)
        if items_per_page < 1 or items_per_page > 1000:
            raise ValueError("items_per_page must be between 1 and 1000")
        if page_number < 1:
            raise ValueError("page_number must be greater than 0")

        params: Dict[str, Any] = {
            "itemsPerPage": items_per_page,
            "pageNumber": page_number,
        }
        embeds: List[str] = []
        if embed_branches:
            embeds.append("branches")
        if embed_variations:
            embeds.append("variations")
        if embeds:
            params["embed"] = ",".join(embeds)
        if include_family_items:
            params["familyItems"] = "true"
        if since_last_modified_date_time:
            params["sinceLastModifiedDateTime"] = since_last_modified_date_time

        response = self._get(config=config, path="/api/product/v1/items", params=params)
        return self._normalize_items_response(response, config.get("branch_number"))

    def get_item(
        self,
        tenant_id: Optional[str],
        service_id: Optional[str],
        item_number: str,
        embed_branches: bool = True,
        embed_variations: bool = True,
        include_family_items: bool = True,
    ) -> Dict[str, Any]:
        logger.debug(f"[ABC-SUPPLY] get_item: item_number={item_number!r}, embed_branches={embed_branches}, embed_variations={embed_variations}")
        config = self.get_abc_config(tenant_id, service_id)
        params: Dict[str, Any] = {}
        embeds: List[str] = []
        if embed_branches:
            embeds.append("branches")
        if embed_variations:
            embeds.append("variations")
        if embeds:
            params["embed"] = ",".join(embeds)
        if include_family_items:
            params["familyItems"] = "true"

        response = self._get(config=config, path=f"/api/product/v1/items/{item_number}", params=params or None)
        return self._normalize_item(response, config.get("branch_number"))

    def price_items(
        self,
        tenant_id: Optional[str] = None,
        service_id: Optional[str] = None,
        lines: Optional[List[Dict[str, Any]]] = None,
        branch_number: Optional[str] = None,
        ship_to_number: Optional[str] = None,
        purpose: str = "estimating",
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        logger.debug(f"[ABC-SUPPLY] price_items: {len(lines or [])} lines, branch_number={branch_number!r}, ship_to_number={ship_to_number!r}, request_id={request_id!r}")
        config = self.get_abc_config(tenant_id, service_id)
        branch = branch_number or config.get("branch_number")
        ship_to = ship_to_number or config.get("ship_to_number") or config.get("bill_to_number")
        if not branch:
            raise ValueError("branch_number is required for ABC Supply pricing")
        if not ship_to:
            raise ValueError("ship_to_number is required for ABC Supply pricing")
        if not lines:
            raise ValueError("lines are required for ABC Supply pricing")
        if len(lines) > 50:
            raise ValueError("ABC Supply pricing supports a maximum of 50 lines per request")

        normalized_lines = []
        for idx, line in enumerate(lines, start=1):
            normalized_line = dict(line)
            normalized_line["id"] = str(normalized_line.get("id") or idx)
            uom_code = self._normalize_uom_code(normalized_line.get("uom"))
            if uom_code:
                normalized_line["uom"] = uom_code
            else:
                normalized_line.pop("uom", None)
            normalized_lines.append(normalized_line)

        payload: Dict[str, Any] = {
            "shipToNumber": str(ship_to),
            "branchNumber": str(branch),
            "purpose": purpose,
            "lines": normalized_lines,
        }
        if request_id:
            payload["requestId"] = request_id

        response = self._post(config=config, path="/api/pricing/v2/prices", payload=payload)
        return self._normalize_price_response(response)

    def get_item_availability(
        self,
        tenant_id: Optional[str] = None,
        service_id: Optional[str] = None,
        item_number: str = "",
    ) -> Dict[str, Any]:
        if not item_number:
            raise ValueError("item_number is required")
        logger.debug(f"[ABC-SUPPLY] get_item_availability: item_number={item_number!r}")
        config = self.get_abc_config(tenant_id, service_id)
        return self._get(config=config, path=f"/api/product/v1/availability/items/{item_number}/branches")

    def get_item_with_price(
        self,
        tenant_id: Optional[str] = None,
        service_id: Optional[str] = None,
        item_number: str = "",
        quantity: float = 1,
        uom: Optional[str] = None,
        branch_number: Optional[str] = None,
        ship_to_number: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not item_number:
            raise ValueError("item_number is required")

        item = self.get_item(
            tenant_id=tenant_id,
            service_id=service_id,
            item_number=item_number,
            embed_branches=True,
            embed_variations=True,
            include_family_items=True,
        )
        selected_uom = self._normalize_uom_code(uom or item.get("uom") or item.get("uomDisplay"))
        price = self.price_items(
            tenant_id=tenant_id,
            service_id=service_id,
            branch_number=branch_number,
            ship_to_number=ship_to_number,
            purpose="estimating",
            request_id=f"fieldcraft-item-detail-{item_number}",
            lines=[{"id": "1", "itemNumber": item_number, "quantity": quantity, "uom": selected_uom}],
        )
        return {
            "item": item,
            "price": price,
            "priceLine": (price.get("lines") or [None])[0],
        }

    def get_account_items(
        self,
        tenant_id: Optional[str] = None,
        service_id: Optional[str] = None,
        list_type: str = "recent",
        bill_to_number: Optional[str] = None,
        branch_number: Optional[str] = None,
        items_per_page: int = 100,
        page_number: int = 1,
    ) -> Dict[str, Any]:
        """Return account-specific materials from ABC account history.

        list_type options:
        - recent: items invoiced in the last 90 days
        - frequent: frequently purchased items in the last 180 days
        - favorite: items marked as favorites in myABCSupply
        """
        logger.debug(f"[ABC-SUPPLY] get_account_items: list_type={list_type!r}, bill_to_number={bill_to_number!r}, branch_number={branch_number!r}, page={page_number}")
        config = self.get_abc_config(tenant_id, service_id)
        bill_to = bill_to_number or config.get("bill_to_number")
        branch = branch_number or config.get("branch_number")
        if not bill_to:
            raise ValueError("bill_to_number is required for ABC Supply account items")
        if items_per_page < 1 or items_per_page > 1000:
            raise ValueError("items_per_page must be between 1 and 1000")
        if page_number < 1:
            raise ValueError("page_number must be greater than 0")

        normalized_type = (list_type or "recent").strip().lower()
        path_suffix_by_type = {
            "recent": "recents",
            "recents": "recents",
            "frequent": "frequents",
            "frequents": "frequents",
            "favorite": "favorites",
            "favorites": "favorites",
        }
        path_suffix = path_suffix_by_type.get(normalized_type)
        if not path_suffix:
            raise ValueError("list_type must be one of: recent, frequent, favorite")

        params: Dict[str, Any] = {
            "itemsPerPage": items_per_page,
            "pageNumber": page_number,
        }
        if branch:
            params["branchNumber"] = str(branch)

        response = self._get(
            config=config,
            path=f"/api/product/v1/items/{bill_to}/{path_suffix}",
            params=params,
        )
        return self._normalize_items_response(response, branch)

    def get_recent_items(
        self,
        tenant_id: Optional[str] = None,
        service_id: Optional[str] = None,
        bill_to_number: Optional[str] = None,
        branch_number: Optional[str] = None,
        items_per_page: int = 100,
        page_number: int = 1,
    ) -> Dict[str, Any]:
        return self.get_account_items(
            tenant_id=tenant_id,
            service_id=service_id,
            list_type="recent",
            bill_to_number=bill_to_number,
            branch_number=branch_number,
            items_per_page=items_per_page,
            page_number=page_number,
        )

    def _request_with_retry(
        self,
        method: str,
        url: str,
        request_label: str,
        **kwargs: Any,
    ) -> requests.Response:
        """Issue an HTTP request, retrying transient upstream failures.

        ABC Supply's gateway intermittently returns an empty-bodied 502/503/504
        (observed on /api/product/v1/search/items) even though a retry of the
        same request typically succeeds. Retry those plus connection/timeout
        errors with backoff; do not retry other status codes (e.g. 4xx),
        since those indicate a real request/auth problem, not a transient blip.
        """
        send = self.session.get if method == "GET" else self.session.post
        delay = self._RETRY_BASE_DELAY_SECONDS
        for attempt in range(1, self._MAX_ATTEMPTS + 1):
            try:
                response = send(url, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt >= self._MAX_ATTEMPTS:
                    raise ABCSupplyAPIError(
                        f"ABC Supply API request failed label={request_label}: {exc}"
                    ) from exc
                logger.warning(
                    "[ABC-SUPPLY] transient network error label=%s attempt=%s/%s: %s; retrying",
                    request_label,
                    attempt,
                    self._MAX_ATTEMPTS,
                    exc,
                )
                time.sleep(delay)
                delay *= 2
                continue

            if response.status_code in self._RETRYABLE_STATUS_CODES and attempt < self._MAX_ATTEMPTS:
                logger.warning(
                    "[ABC-SUPPLY] transient upstream error label=%s status=%s attempt=%s/%s; retrying",
                    request_label,
                    response.status_code,
                    attempt,
                    self._MAX_ATTEMPTS,
                )
                time.sleep(delay)
                delay *= 2
                continue

            return response

        raise ABCSupplyAPIError(f"ABC Supply API request failed label={request_label}: exhausted retries")

    def _get(self, config: Dict[str, Any], path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self._api_base_url(config['env'])}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        logger.debug(f"[ABC-SUPPLY] GET {url}")
        request_label = f"GET {path}"
        response = self._request_authorized_api(
            config=config,
            method="GET",
            url=url,
            request_label=request_label,
            timeout=30,
        )
        return self._parse_response(response, request_label=request_label)

    def _post(
        self,
        config: Dict[str, Any],
        path: str,
        payload: Dict[str, Any],
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self._api_base_url(config['env'])}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        logger.debug(f"[ABC-SUPPLY] POST {url}, payload_keys={list(payload.keys())}")
        request_label = f"POST {path}"
        response = self._request_authorized_api(
            config=config,
            method="POST",
            url=url,
            request_label=request_label,
            json=payload,
            timeout=30,
        )
        return self._parse_response(response, request_label=request_label)
