from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)


class TenantApiClient:
    """Thin, authenticated client for the FIELDCRAFT Tenant API."""

    def __init__(self) -> None:
        base = (os.getenv("TENANT_API_BASE_URL") or "").strip()
        self.base_url = base.rstrip("/") if base else ""
        self.session = requests.Session()
        self.default_token = (os.getenv("TENANT_API_BEARER_TOKEN") or "").strip() or None

    @staticmethod
    def _normalize_bearer_token(token: Optional[str]) -> str:
        value = str(token or "").strip()
        if value.lower().startswith("bearer "):
            value = value[7:].strip()
        return value

    def _headers(self, tenant_api_token: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        token = self._normalize_bearer_token(tenant_api_token or self.default_token)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _require_tenant_token(self, tenant_api_token: Optional[str] = None) -> None:
        if not self._normalize_bearer_token(tenant_api_token or self.default_token):
            raise ValueError(
                "Tenant API bearer token is required. Send tenant-api-token, "
                "Authorization: Bearer <token>, or configure TENANT_API_BEARER_TOKEN."
            )

    def _build_url(self, path: str) -> str:
        if not self.base_url:
            raise ValueError("TENANT_API_BASE_URL is not configured")
        normalized_path = str(path or "").strip()
        if not normalized_path:
            return self.base_url
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"
        return f"{self.base_url}{normalized_path}"

    @staticmethod
    def _ensure_success(
        response: requests.Response,
        *,
        operation: str,
        success_statuses: tuple[int, ...] = (200, 201),
    ) -> None:
        if response.status_code not in success_statuses:
            raise RuntimeError(f"Tenant API {operation} failed with HTTP {response.status_code}")

    @staticmethod
    def _parse_created_id(response: requests.Response) -> int:
        """Parse create responses that return a plain id or a small JSON wrapper."""
        try:
            created_id = response.json()
        except (TypeError, ValueError):
            created_id = response.text

        if isinstance(created_id, dict):
            for key in ("id", "data", "value", "result"):
                if key in created_id:
                    created_id = created_id[key]
                    break

        try:
            return int(str(created_id).strip())
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Tenant API create response did not contain a valid id") from exc

    @staticmethod
    def _parse_boolean_response(response: requests.Response) -> bool:
        try:
            value = response.json()
        except (TypeError, ValueError):
            value = (response.text or "").strip()

        if isinstance(value, bool):
            return value
        if isinstance(value, dict):
            for key in ("exists", "present", "available", "result", "value", "data"):
                if key in value:
                    return TenantApiClient._coerce_bool(value.get(key))
        return TenantApiClient._coerce_bool(value)

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes", "y"}
        return bool(value)

    def create_user(self, tenant_api_token: Optional[str], user_account_dto: Dict[str, Any]) -> int:
        url = ""
        try:
            self._require_tenant_token(tenant_api_token)
            url = self._build_url("/v2/userAccounts/createUser")
            response = self.session.post(
                url,
                headers=self._headers(tenant_api_token),
                json=user_account_dto,
                timeout=240,
            )
            self._ensure_success(response, operation="createUser")
            return self._parse_created_id(response)
        except Exception:
            logger.exception(
                "Tenant API create_user failed | url=%s",
                url or "TENANT_API_BASE_URL not configured",
            )
            raise

    def create_asset(self, tenant_api_token: Optional[str], asset_dto: Dict[str, Any]) -> int:
        """Create a customer asset and return the created tenant asset id."""
        url = ""
        try:
            self._require_tenant_token(tenant_api_token)
            url = self._build_url("/v2/assets/addasset")
            response = self.session.post(
                url,
                headers=self._headers(tenant_api_token),
                json=asset_dto,
                timeout=60,
            )
            self._ensure_success(response, operation="addAsset")
            return self._parse_created_id(response)
        except Exception:
            logger.exception(
                "Tenant API create_asset failed | url=%s",
                url or "TENANT_API_BASE_URL not configured",
            )
            raise

    def get_user(self, tenant_api_token: Optional[str], user_id: int) -> Dict[str, Any]:
        url = ""
        try:
            self._require_tenant_token(tenant_api_token)
            url = self._build_url(f"/api/v2/userAccounts/{int(user_id)}")
            response = self.session.get(url, headers=self._headers(tenant_api_token), timeout=60)
            self._ensure_success(response, operation="getUser")
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("Tenant API getUser returned an invalid response")
            return payload
        except Exception:
            logger.exception(
                "Tenant API get_user failed | url=%s",
                url or "TENANT_API_BASE_URL not configured",
            )
            raise

    def user_email_exists_in_fieldcraft_or_ad(
        self,
        tenant_api_token: Optional[str],
        tenant_id: str,
        email: str,
    ) -> bool:
        """Check for an existing FIELDCRAFT/AD user email.

        This validation intentionally fails open so a temporary Tenant API outage
        does not abort the complete QuickBooks import batch.
        """
        normalized_email = str(email or "").strip()
        normalized_tenant_id = str(tenant_id or "").strip()
        if not normalized_email:
            return False

        url = ""
        try:
            self._require_tenant_token(tenant_api_token)
            path_template = (
                os.getenv("TENANT_API_USER_EMAIL_EXISTS_PATH")
                or "/v2/userAccounts/hasUserInAd/email/{email}"
            )
            path = path_template.format(
                tenant_id=quote(normalized_tenant_id, safe=""),
                email=quote(normalized_email, safe=""),
            )
            url = self._build_url(path)
            response = self.session.get(
                url,
                headers=self._headers(tenant_api_token),
                timeout=30,
            )
            if response.status_code != 200:
                logger.warning(
                    "Tenant API email validation skipped | url=%s status=%s",
                    url,
                    response.status_code,
                )
                return False
            return self._parse_boolean_response(response)
        except Exception:
            logger.exception(
                "Tenant API email validation skipped | url=%s",
                url or "TENANT_API_BASE_URL not configured",
            )
            return False

    def customer_email_exists_in_fieldcraft_or_ad(
        self,
        tenant_api_token: Optional[str],
        tenant_id: str,
        email: str,
    ) -> bool:
        """Backward-compatible wrapper for customer import code."""
        return self.user_email_exists_in_fieldcraft_or_ad(tenant_api_token, tenant_id, email)

    def get_vendor_material(
        self,
        tenant_api_token: Optional[str],
        material_dto: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        url = ""
        try:
            self._require_tenant_token(tenant_api_token)
            path = os.getenv("TENANT_API_GET_VENDOR_MATERIAL_PATH") or "/v2/materials/getVendorMaterials"
            url = self._build_url(path)
            response = self.session.post(
                url,
                headers=self._headers(tenant_api_token),
                json=material_dto,
                timeout=60,
            )
            if response.status_code in (204, 404):
                return None
            self._ensure_success(response, operation="getVendorMaterials")
            try:
                payload = response.json()
            except (TypeError, ValueError):
                return None
            if isinstance(payload, dict) and payload.get("result") is not None:
                payload = payload.get("result")
            return payload if isinstance(payload, dict) and payload else None
        except Exception:
            logger.exception(
                "Tenant API get_vendor_material failed | url=%s",
                url or "TENANT_API_BASE_URL not configured",
            )
            raise

    def get_units_of_measure(self, tenant_api_token: Optional[str]) -> List[Dict[str, Any]]:
        url = ""
        try:
            self._require_tenant_token(tenant_api_token)
            path = os.getenv("TENANT_API_GET_UNITS_OF_MEASURE_PATH") or "/v2/unitOfMeasure/getAllUnits"
            url = self._build_url(path)
            response = self.session.get(url, headers=self._headers(tenant_api_token), timeout=60)
            self._ensure_success(response, operation="getAllUnits")
            payload = response.json()
            if isinstance(payload, dict):
                payload = payload.get("data") or payload.get("result") or payload.get("units") or []
            if not isinstance(payload, list):
                raise RuntimeError("Tenant API getAllUnits returned an invalid response")
            return [unit for unit in payload if isinstance(unit, dict)]
        except Exception:
            logger.exception(
                "Tenant API get_units_of_measure failed | url=%s",
                url or "TENANT_API_BASE_URL not configured",
            )
            raise

    def add_materials(
        self,
        tenant_api_token: Optional[str],
        materials: List[Dict[str, Any]],
    ) -> Any:
        url = ""
        try:
            if not materials:
                return None
            self._require_tenant_token(tenant_api_token)
            path = os.getenv("TENANT_API_ADD_MATERIALS_PATH") or "/v2/materials/addMaterials"
            url = self._build_url(path)
            response = self.session.post(
                url,
                headers=self._headers(tenant_api_token),
                json=materials,
                timeout=120,
            )
            self._ensure_success(response, operation="addMaterials")
            try:
                return response.json()
            except (TypeError, ValueError):
                return (response.text or "").strip() or None
        except Exception:
            logger.exception(
                "Tenant API add_materials failed | url=%s",
                url or "TENANT_API_BASE_URL not configured",
            )
            raise

    def has_user_in_ad_by_email(self, email: str) -> Optional[bool]:
        """Optional legacy helper; use customer_email_exists_in_fieldcraft_or_ad for imports."""
        normalized_email = str(email or "").strip()
        if not normalized_email:
            return None
        try:
            self._require_tenant_token()
            url = self._build_url(
                f"/v2/userAccounts/hasUserInAd/email/{quote(normalized_email, safe='')}"
            )
            response = self.session.get(url, headers=self._headers(), timeout=30)
            if response.status_code != 200:
                return None
            return self._parse_boolean_response(response)
        except Exception:
            logger.exception("Tenant API has_user_in_ad_by_email failed")
            return None
