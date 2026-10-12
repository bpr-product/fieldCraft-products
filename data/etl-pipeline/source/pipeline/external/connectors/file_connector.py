import logging
import os
from typing import Iterator

from external.connectors.base import BaseConnector
from external.config.schema import ConnectorConfig
from external.landing_zone.file_reader import detect_format, read_file, UnsupportedFormatError

log = logging.getLogger(__name__)

ACCOUNT     = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME", "")
ACCOUNT_KEY = os.environ.get("AZURE_STORAGE_ACCOUNT_KEY",  "")


class FileConnector(BaseConnector):
    def __init__(
        self,
        config: ConnectorConfig,
        blob_url: str,
        tenant_id: str = "",
        data: bytes | None = None,
    ) -> None:
        if tenant_id:
            config = config.model_copy(update={"tenant_id": tenant_id})
        super().__init__(config)
        self._blob_url   = blob_url
        # Split https://<account>/<container>/<blob_name> → take everything after container
        _parts = blob_url.split("/", 4)
        self._blob_name  = _parts[4] if len(_parts) > 4 else _parts[-1]
        self._preloaded  = data  # avoids re-download when handler already fetched the file

    def _download(self) -> bytes:
        import adlfs
        fs        = adlfs.AzureBlobFileSystem(account_name=ACCOUNT, account_key=ACCOUNT_KEY)
        container = self.config.file.landing_container
        path      = f"{container}/{self._blob_name}"
        with fs.open(path, "rb") as f:
            return f.read()

    def _mark_processed(self, rows_written: int) -> None:
        try:
            from azure.storage.blob import BlobServiceClient
            client = BlobServiceClient(
                account_url=f"https://{ACCOUNT}.blob.core.windows.net",
                credential=ACCOUNT_KEY,
            )
            blob = client.get_blob_client(
                container=self.config.file.landing_container,
                blob=self._blob_name,
            )
            blob.set_blob_metadata({
                "processed":           "true",
                "processed_at":        str(__import__("time").time()),
                "bronze_rows_written": str(rows_written),
                "status":              "success",
            })
        except Exception as exc:
            log.warning("Could not set processed metadata on blob %s: %s", self._blob_name, exc)

    def _quarantine(self, reason: str) -> None:
        log.error("Quarantining blob %s — %s", self._blob_name, reason)
        try:
            from azure.storage.blob import BlobServiceClient
            client = BlobServiceClient(
                account_url=f"https://{ACCOUNT}.blob.core.windows.net",
                credential=ACCOUNT_KEY,
            )
            src      = client.get_blob_client(container=self.config.file.landing_container, blob=self._blob_name)
            dest     = client.get_blob_client(container=self.config.file.landing_container, blob=f"_quarantine/{self._blob_name}")
            dest.start_copy_from_url(src.url)
        except Exception as exc:
            log.warning("Quarantine copy failed for %s: %s", self._blob_name, exc)

    def fetch(self) -> Iterator[dict]:
        filename = self._blob_name.rsplit("/", 1)[-1]
        try:
            fmt = detect_format(filename)
        except UnsupportedFormatError as exc:
            self._quarantine(str(exc))
            return

        data     = self._preloaded if self._preloaded is not None else self._download()
        file_cfg = self.config.file
        rows     = read_file(
            data, fmt=fmt,
            delimiter=file_cfg.delimiter,
            has_header=file_cfg.has_header,
            sheet_name=file_cfg.sheet_name,
        )
        log.info("FileConnector: read %d rows from %s (%s)", len(rows), filename, fmt)
        yield from rows

    def run(self) -> dict:
        result = super().run()
        self._mark_processed(result["records_processed"])
        return result
