"""
FastAPI service — receives Azure Event Grid BlobCreated events and dispatches
a FileConnector for each new blob in external-landing/.

Schema-based table routing
--------------------------
Files under the same folder (source_name) are merged into ONE Silver table
if their column names match. If columns differ, they land in a separate table.

  Same prefix + same schema  → silver.ext_{source_name}
  Same prefix + diff schema  → silver.ext_{source_name}_{filename_stem}

Schema fingerprints are stored as small JSON blobs under:
  external-landing/_schema/{source_name}.json

Blob path convention (flexible — any depth works):
  external-landing/{source_name}/{filename}
  external-landing/{source_name}/{tenant_id}/{filename}
  external-landing/{source_name}/{tenant_id}/{YYYY-MM-DD}/{filename}
"""
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from external.config.schema import ConnectorConfig, FileConfig, RoutingConfig, load_config
from external.connectors.file_connector import FileConnector
from external.landing_zone.file_reader import detect_format, peek_headers, UnsupportedFormatError

log = logging.getLogger(__name__)

CONFIG_DIR  = os.environ.get("EXTERNAL_CONFIG_DIR", "external/config/sources")
ACCOUNT     = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME", "")
ACCOUNT_KEY = os.environ.get("AZURE_STORAGE_ACCOUNT_KEY", "")
CONTAINER   = os.environ.get("EXTERNAL_LANDING_CONTAINER", "external-landing")
LANDING_TRIGGER_SECRET = os.environ.get("LANDING_TRIGGER_SECRET", "")

app = FastAPI(title="FIELDCRAFT External Landing Zone Handler", version="1.0.0")


# ── schema fingerprint helpers ────────────────────────────────────────────────

def _fingerprint(columns: list[str]) -> str:
    """MD5 of sorted lowercase column names — used to detect schema changes."""
    key = ",".join(sorted(c.strip().lower() for c in columns if c.strip()))
    return hashlib.md5(key.encode()).hexdigest()


def _schema_blob_name(source_name: str) -> str:
    return f"_schema/{source_name}.json"


def _read_stored_schema(source_name: str) -> dict | None:
    """Return stored schema dict {fingerprint, columns} or None if not yet stored."""
    try:
        from azure.storage.blob import BlobServiceClient
        client = BlobServiceClient(
            account_url=f"https://{ACCOUNT}.blob.core.windows.net",
            credential=ACCOUNT_KEY,
        )
        blob = client.get_blob_client(container=CONTAINER, blob=_schema_blob_name(source_name))
        return json.loads(blob.download_blob().readall())
    except Exception:
        return None


def _store_schema(source_name: str, columns: list[str], fingerprint: str) -> None:
    """Persist schema fingerprint for a source so future files can compare."""
    try:
        from azure.storage.blob import BlobServiceClient
        client = BlobServiceClient(
            account_url=f"https://{ACCOUNT}.blob.core.windows.net",
            credential=ACCOUNT_KEY,
        )
        blob = client.get_blob_client(container=CONTAINER, blob=_schema_blob_name(source_name))
        blob.upload_blob(
            json.dumps({"fingerprint": fingerprint, "columns": columns}).encode(),
            overwrite=True,
        )
        log.info("Stored schema for source %r: %s", source_name, columns)
    except Exception as exc:
        log.warning("Could not store schema for %s: %s", source_name, exc)


def _resolve_entity(source_name: str, filename_stem: str, columns: list[str]) -> str:
    """
    Determine the Silver entity name based on schema matching:
      - No columns detected (empty file / binary)  → use filename_stem
      - First file for this source                  → store schema, use source_name
      - Same schema as stored                       → use source_name  (merged table)
      - Different schema                            → use source_name + filename_stem
    """
    if not columns:
        return filename_stem

    fp      = _fingerprint(columns)
    stored  = _read_stored_schema(source_name)

    if stored is None:
        # First file — register this schema as the canonical one for the source
        _store_schema(source_name, columns, fp)
        log.info("Source %r: registered schema with %d columns", source_name, len(columns))
        return source_name

    if stored["fingerprint"] == fp:
        log.info("Source %r: schema matches — merging into silver.ext_%s", source_name, source_name)
        return source_name

    # Schema differs — route to a separate table named after the file
    entity = f"{source_name}_{filename_stem}"
    log.info(
        "Source %r: schema differs from registered (%d cols vs %d cols) — "
        "routing to silver.ext_%s",
        source_name,
        len(columns),
        len(stored.get("columns", [])),
        entity,
    )
    return entity


# ── path parsing ──────────────────────────────────────────────────────────────

def _parse_blob_url(url: str) -> tuple[str, str, str]:
    """Return (container, source_name, blob_path) from Azure Blob URL."""
    parsed = urlparse(url)
    parts  = parsed.path.lstrip("/").split("/", 2)
    container   = parts[0] if len(parts) > 0 else ""
    source_name = parts[1] if len(parts) > 1 else ""
    blob_path   = parts[2] if len(parts) > 2 else ""
    return container, source_name, blob_path


def _tenant_from_path(blob_path: str) -> str:
    """Return tenant_id segment if present, empty string otherwise."""
    parts = [p for p in blob_path.split("/") if p]
    return parts[0] if len(parts) > 1 else ""


def _download_blob(blob_name: str) -> bytes:
    import adlfs
    fs   = adlfs.AzureBlobFileSystem(account_name=ACCOUNT, account_key=ACCOUNT_KEY)
    path = f"{CONTAINER}/{blob_name}"
    with fs.open(path, "rb") as f:
        return f.read()


def _build_config(source_name: str, entity: str, tenant_id: str) -> ConnectorConfig:
    return ConnectorConfig(
        source_name=source_name,
        source_entity=entity,
        connector_type="file",
        tenant_id=tenant_id,
        file=FileConfig(landing_container=CONTAINER),
        routing=RoutingConfig(strategy="kafka"),
    )


# ── endpoints ─────────────────────────────────────────────────────────────────

@app.get("/landing/health")
async def health():
    return {"status": "ok"}


@app.post("/landing/trigger")
async def on_blob_created(
    event: dict,
    x_landing_trigger_secret: str | None = Header(default=None),
) -> JSONResponse:
    event_type = event.get("eventType", "")

    # Azure Event Grid webhook validation handshake — must be answered before
    # any auth/secret check, since Event Grid sends this unauthenticated by design.
    if event_type == "Microsoft.EventGrid.SubscriptionValidationEvent":
        validation_code = event.get("data", {}).get("validationCode", "")
        return JSONResponse({"validationResponse": validation_code})

    # Shared-secret gate — fail closed if the secret isn't configured, don't
    # silently skip the check (consistent with this repo's other auth guards).
    if not LANDING_TRIGGER_SECRET:
        log.error("LANDING_TRIGGER_SECRET is not configured — refusing all /landing/trigger requests")
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not hmac.compare_digest(x_landing_trigger_secret or "", LANDING_TRIGGER_SECRET):
        raise HTTPException(status_code=401, detail="Unauthorized")

    if event_type != "Microsoft.Storage.BlobCreated":
        raise HTTPException(status_code=400, detail=f"Unexpected event type: {event_type}")

    blob_url = event.get("data", {}).get("url", "")
    if not blob_url:
        raise HTTPException(status_code=400, detail="No blob URL in event data")

    _container, source_name, blob_path = _parse_blob_url(blob_url)

    # Ignore blobs in reserved system prefixes (_processed, _failed, _quarantine, _schema)
    if not source_name or source_name.startswith("_"):
        return JSONResponse({"status": "ignored", "reason": "reserved prefix"})

    filename      = blob_path.rsplit("/", 1)[-1] if "/" in blob_path else blob_path
    filename_stem = Path(filename).stem.lower().replace("-", "_").replace(" ", "_")
    tenant_id     = _tenant_from_path(blob_path)
    blob_name     = blob_url.split("/", 3)[-1] if "/" in blob_url else blob_url

    # Detect file format — quarantine if unsupported
    try:
        fmt = detect_format(filename)
    except UnsupportedFormatError as exc:
        log.warning("Unsupported format for %s: %s", filename, exc)
        return JSONResponse({"status": "quarantined", "reason": str(exc)}, status_code=200)

    # Download once — shared between schema peek and FileConnector
    try:
        data = _download_blob(blob_name)
    except Exception as exc:
        log.exception("Failed to download blob %s: %s", blob_name, exc)
        raise HTTPException(status_code=500, detail=f"Blob download failed: {exc}")

    # Peek headers and resolve which Silver table this file belongs to
    try:
        yaml_config_path = Path(CONFIG_DIR) / f"{source_name}.yaml"
        if yaml_config_path.exists():
            config = load_config(yaml_config_path, tenant_id=tenant_id)
            log.info("Using YAML config for source %r", source_name)
        else:
            columns = peek_headers(data, fmt=fmt)
            entity  = _resolve_entity(source_name, filename_stem, columns)
            config  = _build_config(source_name, entity, tenant_id)
    except Exception as exc:
        log.error("Config resolution failed for %s: %s", source_name, exc)
        raise HTTPException(status_code=500, detail=str(exc))

    # The loaded config's declared connector_type must be file-based before we
    # dispatch to FileConnector — a source_name whose YAML declares rest_api/db
    # (e.g. fiserv/quickbooks) must never be routed through the file path just
    # because the blob path picked its config. (tenant_id trust here depends on
    # the /landing/trigger auth gate above — see Finding 3.)
    if config.connector_type != "file":
        log.error(
            "Config for source %r declares connector_type=%r, not 'file' — refusing to "
            "process blob %s via FileConnector",
            source_name, config.connector_type, blob_url,
        )
        raise HTTPException(
            status_code=400,
            detail=f"Source {source_name!r} is not file-based (connector_type={config.connector_type!r})",
        )

    # Run connector with pre-loaded data — no second download
    connector = FileConnector(config, blob_url=blob_url, tenant_id=tenant_id, data=data)
    try:
        result = connector.run()
    except Exception as exc:
        log.exception("FileConnector failed for %s: %s", blob_url, exc)
        raise HTTPException(status_code=500, detail=str(exc))

    result["entity"] = config.source_entity
    result["table"]  = f"silver.ext_{source_name}_{config.source_entity}" if config.source_entity != source_name else f"silver.ext_{source_name}"
    return JSONResponse(result, status_code=200)
