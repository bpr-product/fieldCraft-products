import json
import logging
import os
import time
from typing import Any

log = logging.getLogger(__name__)

ICEBERG_URI   = os.environ.get("ETL_ICEBERG_REST_URI",       "http://localhost:8181")
AZURE_ACCOUNT = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME", "")
AZURE_KEY     = os.environ.get("AZURE_STORAGE_ACCOUNT_KEY",  "")


class IcebergWriter:
    def __init__(self, config) -> None:
        self._config = config

    def write(self, envelopes: list[dict[str, Any]]) -> None:
        from pyiceberg.catalog import load_catalog
        import pyarrow as pa

        # Dual-mode ADLS auth: include the account key only when it is set
        # (local dev). When absent (prod/AKS), adlfs falls back to the Azure SDK
        # DefaultAzureCredential chain, picking up Workload/Managed Identity — no
        # account key on the pod.
        catalog_props = {
            "type":              "rest",
            "uri":               ICEBERG_URI,
            "io-impl":           "pyiceberg.io.fsspec.FsspecFileIO",
            "adls.account-name": AZURE_ACCOUNT,
        }
        if AZURE_KEY:
            catalog_props["adls.account-key"] = AZURE_KEY
            log.info("IcebergWriter ADLS auth: shared-key (account=%s)", AZURE_ACCOUNT)
        else:
            log.info("IcebergWriter ADLS auth: DefaultAzureCredential (Workload/Managed Identity)")
        catalog = load_catalog("bronze", **catalog_props)
        table = catalog.load_table("bronze.raw_events")
        now_ms = int(time.time() * 1000)
        rows = [{
            "source_type":  "external",
            "op":           env.get("op", "r"),
            "lsn":          int(env.get("lsn", now_ms)),
            "ts_ms":        int(env.get("ts_ms", now_ms)),
            "tenantid":     env.get("tenantid", ""),
            "entity":       env.get("source_entity", ""),
            "record_id":    f"{env.get('tenantid', '')}:{env.get('external_id', '')}",
            "source_name":  env.get("source_name", ""),
            "payload":      json.dumps(env, default=str),
            "_ingested_at": now_ms,
        } for env in envelopes]

        table.append(pa.Table.from_pylist(rows))
        log.info("IcebergWriter: wrote %d records to bronze.raw_events", len(rows))
