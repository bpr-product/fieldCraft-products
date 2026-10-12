import json
import os
import traceback
from typing import Any, Dict, Optional

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient

DEFAULT_CONFIG_CONTAINER = "quickbooks-configuration"
DEFAULT_COA_MAPPING_BLOB = "quickbooks-mapping/coa-mapping.json"

AZURE_PROVIDER_CONNECTION_STRING_ENV = "Provider_Connection_String"

QBO_CONFIG_CONTAINER_ENV = "QBO_CONFIGURATION_CONTAINER"
QBO_COA_MAPPING_BLOB_ENV = "QBO_COA_MAPPING_BLOB"


class QboBlobConfigStore:
    """Read shared QuickBooks configuration JSON from Azure Blob Storage."""

    def __init__(self) -> None:
        self._client: Optional[BlobServiceClient] = None

    def _blob_service_client(self) -> BlobServiceClient:
        if self._client:
            return self._client

        conn_str = os.getenv(AZURE_PROVIDER_CONNECTION_STRING_ENV)
        if not conn_str:
            raise RuntimeError(
                f"Azure Blob connection string not set. Configure {AZURE_PROVIDER_CONNECTION_STRING_ENV}"
            )
        self._client = BlobServiceClient.from_connection_string(conn_str)
        return self._client

    def _resolve(self, *, container: Optional[str] = None, blob_name: Optional[str] = None) -> tuple[str, str]:
        container_name = (container or os.getenv(QBO_CONFIG_CONTAINER_ENV) or DEFAULT_CONFIG_CONTAINER).strip()
        blob_path = (blob_name or os.getenv(QBO_COA_MAPPING_BLOB_ENV) or DEFAULT_COA_MAPPING_BLOB).strip()

        if not container_name:
            raise ValueError("container is required")
        if not blob_path:
            raise ValueError("blob_name is required")

        return container_name, blob_path

    def get_json(self, *, container: Optional[str] = None, blob_name: Optional[str] = None) -> Dict[str, Any]:
        container_name, blob_path = self._resolve(container=container, blob_name=blob_name)

        bc = self._blob_service_client().get_blob_client(container=container_name, blob=blob_path)
        raw = bc.download_blob().readall()
        return json.loads(raw.decode("utf-8"))

    def try_get_json(self, *, container: Optional[str] = None, blob_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        try:
            return self.get_json(container=container, blob_name=blob_name)
        except ResourceNotFoundError:
            container_name, blob_path = self._resolve(container=container, blob_name=blob_name)
            print(
                f"[QboBlobConfigStore] WARN: mapping blob not found (container={container_name!r}, blob={blob_path!r}). "
                "Upload the JSON or update QBO_CONFIGURATION_CONTAINER / QBO_COA_MAPPING_BLOB.",
                flush=True,
            )
            return None
        except Exception:
            print("[QboBlobConfigStore] WARN: failed to read config JSON from blob", flush=True)
            print(traceback.format_exc(), flush=True)
            return None
