"""Configurable ABC Supply to FIELDCRAFT UOM mapping loader.

The import service keeps its built-in UOM mapping as the fallback. This loader
only reads optional mapping configuration from Azure Blob so new ABC UOM aliases
can be added without code changes.

Supported env:
  ABC_SUPPLY_UOM_MAPPING_CONNECTION_STRING  - optional, falls back to Provider_Connection_String
  ABC_SUPPLY_UOM_MAPPING_CONTAINER          - optional, default external-service-configuration
  ABC_SUPPLY_UOM_MAPPING_BLOB               - optional, default abc-supply/uom-mapping.json
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

PROVIDER_CONNECTION_STRING_ENV = "Provider_Connection_String"
ABC_UOM_MAPPING_CONNECTION_STRING_ENV = "ABC_SUPPLY_UOM_MAPPING_CONNECTION_STRING"
ABC_UOM_MAPPING_CONTAINER_ENV = "ABC_SUPPLY_UOM_MAPPING_CONTAINER"
ABC_UOM_MAPPING_BLOB_ENV = "ABC_SUPPLY_UOM_MAPPING_BLOB"

DEFAULT_ABC_UOM_MAPPING_CONTAINER = "external-service-configuration"
DEFAULT_ABC_UOM_MAPPING_BLOB = "abc-supply/uom-mapping.json"


class ABCSupplyUomMappingConfig:
    """Read ABC Supply UOM mapping JSON from Azure Blob Storage."""

    def __init__(self) -> None:
        self._client: Optional[Any] = None

    def get_uom_map(self) -> Dict[str, str]:
        """Return normalized ABC UOM -> FIELDCRAFT UOM mapping from blob config.

        Missing, unavailable, or invalid blob config is non-fatal. The import
        service will continue with its built-in fallback map when this returns
        an empty dictionary.
        """
        try:
            payload = self._get_json()
            mapping = self._extract_uom_map(payload)
            normalized = self._normalize_mapping(mapping)
            if normalized:
                logger.info(
                    "[ABCSupplyUomMappingConfig] loaded %s ABC Supply UOM mappings from blob",
                    len(normalized),
                )
            return normalized
        except FileNotFoundError:
            container, blob_name = self._resolve_blob_location()
            logger.warning(
                "[ABCSupplyUomMappingConfig] UOM mapping blob not found; using code fallback mapping | container=%s blob=%s",
                container,
                blob_name,
            )
            return {}
        except Exception:
            logger.warning(
                "[ABCSupplyUomMappingConfig] failed to load ABC Supply UOM mapping blob; using code fallback mapping",
                exc_info=True,
            )
            return {}

    def _blob_service_client(self) -> Any:
        if self._client:
            return self._client

        connection_string = (
            os.getenv(ABC_UOM_MAPPING_CONNECTION_STRING_ENV)
            or os.getenv(PROVIDER_CONNECTION_STRING_ENV)
            or ""
        ).strip()
        if not connection_string:
            raise RuntimeError(
                f"Azure Blob connection string not configured. Set {ABC_UOM_MAPPING_CONNECTION_STRING_ENV} or {PROVIDER_CONNECTION_STRING_ENV}."
            )

        try:
            from azure.storage.blob import BlobServiceClient
        except Exception as exc:
            raise RuntimeError("azure-storage-blob package is required to load ABC Supply UOM mapping from blob") from exc

        self._client = BlobServiceClient.from_connection_string(connection_string)
        return self._client

    def _resolve_blob_location(self) -> tuple[str, str]:
        container = (os.getenv(ABC_UOM_MAPPING_CONTAINER_ENV) or DEFAULT_ABC_UOM_MAPPING_CONTAINER).strip()
        blob_name = (os.getenv(ABC_UOM_MAPPING_BLOB_ENV) or DEFAULT_ABC_UOM_MAPPING_BLOB).strip()
        if not container:
            raise ValueError(f"{ABC_UOM_MAPPING_CONTAINER_ENV} is required")
        if not blob_name:
            raise ValueError(f"{ABC_UOM_MAPPING_BLOB_ENV} is required")
        return container, blob_name

    def _get_json(self) -> Dict[str, Any]:
        container, blob_name = self._resolve_blob_location()
        blob_client = self._blob_service_client().get_blob_client(container=container, blob=blob_name)
        try:
            raw = blob_client.download_blob().readall()
        except Exception as exc:
            if exc.__class__.__name__ == "ResourceNotFoundError":
                raise FileNotFoundError(f"Blob not found: {container}/{blob_name}") from exc
            raise

        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("ABC Supply UOM mapping config must be a JSON object")
        return payload

    @staticmethod
    def _extract_uom_map(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Support service-nested, named-map, and flat JSON shapes."""
        if not isinstance(payload, dict):
            return {}

        service_config = payload.get("ABC_SUPPLY") or payload.get("abcSupply") or payload.get("abc_supply")
        if isinstance(service_config, dict):
            configured_map = service_config.get("uomMap") or service_config.get("uom_map")
            if isinstance(configured_map, dict):
                return configured_map

        configured_map = payload.get("uomMap") or payload.get("uom_map")
        if isinstance(configured_map, dict):
            return configured_map

        return payload

    @classmethod
    def _normalize_mapping(cls, mapping: Dict[str, Any]) -> Dict[str, str]:
        normalized: Dict[str, str] = {}
        for external_uom, fieldcraft_uom in (mapping or {}).items():
            external_code = cls.normalize_uom(external_uom)
            fieldcraft_code = cls.normalize_uom(fieldcraft_uom)
            if external_code and fieldcraft_code:
                normalized[external_code] = fieldcraft_code
        return normalized

    @staticmethod
    def normalize_uom(value: Any) -> str:
        text = str(value or "").strip()
        if " - " in text:
            text = text.split(" - ", 1)[0].strip()
        return text.upper().replace(" ", "_").replace("-", "_")
