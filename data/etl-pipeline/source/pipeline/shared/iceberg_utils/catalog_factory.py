import logging
import os
from pyiceberg.catalog import load_catalog

logger = logging.getLogger(__name__)


def get_catalog(layer: str = "bronze"):
    """
    Return a PyIceberg catalog for the requested layer.

    ETL_ENV=local   → REST catalog backed by MinIO (Docker)
    ETL_ENV=azure   → SQL catalog backed by Azure Blob Storage (abfs://)

    Bronze warehouse layout on Azure:
        abfs://fieldcraft-bronze@<account>.dfs.core.windows.net/iceberg/
    """
    if layer == "silver":
        return _get_silver_pg_conn()
    return _get_bronze_catalog()


def _get_bronze_catalog():
    # Always use Azure REST catalog — MinIO is removed
    return _azure_rest_catalog()


def _ensure_bronze_container_exists():
    """
    Auto-create the Bronze Azure Blob container if it's missing.

    Neither iceberg-rest's ADLSFileIO (JVM side) nor pyiceberg's own fsspec
    writes auto-create the abfs:// container the way a local filesystem path
    would -- it must already exist. Confirmed live on prod 2026-08-20: a
    fresh stgetlncusprod storage account had no "bronze" container at all, so
    every table-create request failed with
    com.azure.storage.blob.models.BlobStorageException: Status code 404,
    "ContainerNotFound" -- wrapped by iceberg-rest into a generic
    RuntimeIOException / 500 with nothing indicating the real cause was a
    missing container, not a credential or connectivity problem. Runs on
    every catalog acquisition (one cheap properties check, no-op once the
    container exists) so a brand-new storage account self-heals on the very
    next pipeline run instead of requiring someone to provision it by hand
    first, same as create_bronze_tables.py's own idempotent-table philosophy.
    """
    account = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME", "")
    if not account:
        return  # nothing to check against — load_catalog() below will fail loudly if this was actually required

    container = os.environ.get("AZURE_STORAGE_CONTAINER_BRONZE", "bronze")
    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
    key = os.environ.get("AZURE_STORAGE_ACCOUNT_KEY", "")

    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError:
        logger.warning("azure-storage-blob not installed — skipping Bronze container existence check")
        return

    try:
        if conn_str:
            client = BlobServiceClient.from_connection_string(conn_str)
        elif key:
            client = BlobServiceClient(account_url=f"https://{account}.blob.core.windows.net", credential=key)
        else:
            from azure.identity import DefaultAzureCredential
            client = BlobServiceClient(account_url=f"https://{account}.blob.core.windows.net", credential=DefaultAzureCredential())

        container_client = client.get_container_client(container)
        try:
            container_client.get_container_properties()
        except Exception:
            logger.warning("Bronze container %r not found on %r — creating it now", container, account)
            container_client.create_container()
    except Exception as exc:
        # Non-fatal: if this check itself fails (network, auth), fall through
        # and let the real Iceberg operation surface the actual error with
        # full context, same behavior as before this guard existed.
        logger.warning("Could not verify/create Bronze container %r: %s", container, exc)


def _azure_rest_catalog():
    """PyIceberg REST catalog → iceberg-rest container backed by Azure Blob Storage."""
    _ensure_bronze_container_exists()
    uri = os.environ.get("ETL_ICEBERG_REST_URI", "http://localhost:8181")
    props = {
        "type": "rest",
        "uri":  uri,
    }
    # PyIceberg's default PyArrow IO cannot read abfs:// — route reads through
    # fsspec/adlfs whenever the storage account is configured.
    account = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME", "")
    key     = os.environ.get("AZURE_STORAGE_ACCOUNT_KEY", "")
    if account:
        props.update({
            "py-io-impl":        "pyiceberg.io.fsspec.FsspecFileIO",
            "adls.account-name": account,
            "adls.account-key":  key,
        })
    return load_catalog("bronze", **props)


def _azure_catalog():
    """
    PyIceberg SQL catalog backed by Azure Blob Storage (abfs://).

    Requires:
        AZURE_STORAGE_ACCOUNT_NAME  — storage account name
        AZURE_STORAGE_ACCOUNT_KEY   — storage account key
        AZURE_ICEBERG_CATALOG_URI   — SQLite/Postgres URI for catalog metadata
                                      e.g. sqlite:////tmp/iceberg_catalog.db  (local test)
                                      or   postgresql+psycopg2://user:pw@host/db  (prod)

    Warehouse:
        abfs://bronze@<account>.dfs.core.windows.net/iceberg/
    """
    account   = os.environ["AZURE_STORAGE_ACCOUNT_NAME"]
    key       = os.environ["AZURE_STORAGE_ACCOUNT_KEY"]
    container = os.environ.get("AZURE_STORAGE_CONTAINER_BRONZE", "bronze")

    # abfs:// URI understood by adlfs/fsspec
    warehouse = f"abfs://{container}@{account}.dfs.core.windows.net/iceberg/"

    catalog_uri = os.environ.get(
        "AZURE_ICEBERG_CATALOG_URI",
        "sqlite:////tmp/fieldcraft_iceberg_catalog.db",
    )

    try:
        import adlfs
        import fsspec
        fsspec.config.conf["adlfs"] = {
            "account_name": account,
            "account_key":  key,
        }
    except ImportError:
        pass

    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")

    return load_catalog(
        "bronze",
        **{
            "type":                    "sql",
            "uri":                     catalog_uri,
            "warehouse":               warehouse,
            "py-io-impl":              "pyiceberg.io.fsspec.FsspecFileIO",
            "adlfs.account-name":      account,
            "adlfs.account-key":       key,
            "adlfs.connection-string": conn_str,
        },
    )


def _get_silver_pg_conn():
    """Return a psycopg2 connection to the Silver PostgreSQL database.

    Resolved via shared.secret_resolver.get_silver_pg_dsn() rather than
    building the connection from individual os.environ.get(..., "") reads —
    that ad-hoc pattern bypassed the resolver's fail-hard behavior on a
    missing secret and could silently connect with an empty password.
    """
    import psycopg2
    from shared.secret_resolver import get_silver_pg_dsn
    return psycopg2.connect(get_silver_pg_dsn())
