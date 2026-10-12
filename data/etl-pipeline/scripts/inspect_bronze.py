#!/usr/bin/env python3
"""
inspect_bronze.py

Connects to Azure Blob Storage and analyzes the Bronze Iceberg layer.
Parses .env.local manually — no python-dotenv dependency required.

Reports per entity:
  - Number of metadata files
  - Number of data (Parquet) files
  - Whether snapshots exist (current-snapshot-id != -1 in table metadata)
  - Total size of data files in KB

Prints a summary table distinguishing entities with data from
entities that are schema-only (empty tables).

Usage:
    # Load env vars first (PowerShell):
    Get-Content .env.local | Where-Object { $_ -notmatch '^#' -and $_ -match '=' } |
        ForEach-Object { $parts = $_ -split '=',2;
          [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), 'Process') }
    python scripts/inspect_bronze.py

    # Or run standalone — it auto-loads .env.local from the repo root:
    python scripts/inspect_bronze.py
"""

import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# ── azure-storage-blob is the only non-stdlib dependency ─────────────────────
try:
    from azure.storage.blob import BlobServiceClient, ContainerClient
except ImportError:
    sys.exit(
        "ERROR: azure-storage-blob is not installed.\n"
        "Install with:  pip install azure-storage-blob"
    )

# ── Constants ─────────────────────────────────────────────────────────────────

ENV_LOCAL_PATH = (
    Path(__file__).resolve().parent.parent / ".env.local"
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def load_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file manually, returning a {key: value} dict.

    Rules:
    - Lines starting with # are comments and are skipped.
    - Blank lines are skipped.
    - Only lines containing '=' are parsed.
    - The key is everything before the first '='; value is everything after.
    - Values are NOT unquoted (Azure connection strings contain '=' internally,
      so splitting on only the first '=' is intentional).
    """
    env: dict[str, str] = {}
    if not path.exists():
        return env
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.lstrip().startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def resolve_config() -> tuple[str, str]:
    """Return (connection_string, container_name) from env or .env.local."""
    # Prefer values already exported into the process environment.
    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
    container = os.environ.get("AZURE_STORAGE_CONTAINER_BRONZE", "bronze")

    if not conn_str:
        env = load_env_file(ENV_LOCAL_PATH)
        conn_str = env.get("AZURE_STORAGE_CONNECTION_STRING", "")
        container = env.get("AZURE_STORAGE_CONTAINER_BRONZE", container)

    if not conn_str:
        sys.exit(
            "ERROR: AZURE_STORAGE_CONNECTION_STRING not found.\n"
            f"Expected in environment variables or {ENV_LOCAL_PATH}"
        )

    return conn_str, container


def classify_blob(blob_name: str) -> str:
    """Return 'metadata', 'data', or 'other' based on the blob path."""
    lower = blob_name.lower()
    if lower.endswith(".parquet") or lower.endswith(".avro") or lower.endswith(".orc"):
        return "data"
    # Iceberg metadata: *.metadata.json, snap-*.avro (snapshots inside metadata/),
    # *.avro manifests, version-hint.text
    if "/metadata/" in lower:
        return "metadata"
    return "other"


def extract_entity(blob_name: str, container_prefix: str) -> str | None:
    """Extract the entity name (first path segment after container root).

    Blob paths look like:
        bronze/project/data/...     → 'project'
        bronze/task/metadata/...    → 'task'
        warehouse/bronze/project/   → 'project'  (REST catalog layout)

    Returns None for blobs that don't fit the expected depth.
    """
    # Strip a leading container-name prefix if present (some SDK paths include it)
    name = blob_name
    if name.startswith(container_prefix + "/"):
        name = name[len(container_prefix) + 1:]

    parts = [p for p in name.split("/") if p]
    if len(parts) < 2:
        return None
    # Skip hidden / system directories
    if parts[0].startswith(".") or parts[0].startswith("_"):
        return None
    return parts[0]


# ── Core analysis logic ───────────────────────────────────────────────────────


def fetch_table_metadata(
    container_client: ContainerClient,
    entity: str,
    metadata_blobs: list[str],
) -> dict[str, Any]:
    """Download the most-recent *.metadata.json for the entity and parse it.

    Iceberg stores multiple metadata JSON files; the newest one (alphabetically
    largest, which corresponds to the latest sequence number) is the current
    table state.

    Returns a dict with keys:
        has_snapshots (bool)
        snapshot_count (int)
        current_snapshot_id (int | None)
    """
    # Filter to *.metadata.json files for this entity only
    meta_jsons = sorted(
        [b for b in metadata_blobs if b.endswith(".metadata.json")],
        reverse=True,  # latest version first (v12 > v11 > ... lexicographically)
    )

    if not meta_jsons:
        return {"has_snapshots": False, "snapshot_count": 0, "current_snapshot_id": None}

    latest_meta_blob = meta_jsons[0]
    try:
        blob_client = container_client.get_blob_client(latest_meta_blob)
        raw = blob_client.download_blob().readall()
        meta = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        print(f"  WARN: could not read metadata for {entity}: {exc}")
        return {"has_snapshots": False, "snapshot_count": 0, "current_snapshot_id": None}

    current_snapshot_id = meta.get("current-snapshot-id", -1)
    snapshots = meta.get("snapshots", [])

    has_snapshots = (
        current_snapshot_id is not None
        and current_snapshot_id != -1
        and len(snapshots) > 0
    )

    return {
        "has_snapshots": has_snapshots,
        "snapshot_count": len(snapshots),
        "current_snapshot_id": current_snapshot_id,
    }


def analyze_bronze(connection_string: str, container_name: str) -> None:
    """Main analysis routine."""

    print(f"\nConnecting to Azure Blob Storage...")
    print(f"  Container : {container_name}")
    # Parse account name from connection string for display (never log the key)
    acct_match = re.search(r"AccountName=([^;]+)", connection_string, re.IGNORECASE)
    account_name = acct_match.group(1) if acct_match else "<unknown>"
    print(f"  Account   : {account_name}")
    print()

    service_client = BlobServiceClient.from_connection_string(connection_string)
    container_client = service_client.get_container_client(container_name)

    # Verify the container exists
    try:
        props = container_client.get_container_properties()
        print(f"  Container last-modified : {props['last_modified']}")
    except Exception as exc:
        sys.exit(f"ERROR: cannot access container '{container_name}': {exc}")

    print("\nListing all blobs (this may take a moment for large layers)...")

    # Accumulate per-entity stats in a single pass
    entity_metadata_blobs: dict[str, list[str]] = defaultdict(list)
    entity_data_blobs: dict[str, list[tuple[str, int]]] = defaultdict(list)
    entity_other_blobs: dict[str, list[str]] = defaultdict(list)
    total_blob_count = 0

    for blob in container_client.list_blobs():
        total_blob_count += 1
        entity = extract_entity(blob.name, container_name)
        if entity is None:
            continue
        kind = classify_blob(blob.name)
        if kind == "metadata":
            entity_metadata_blobs[entity].append(blob.name)
        elif kind == "data":
            entity_data_blobs[entity].append((blob.name, blob.size or 0))
        else:
            entity_other_blobs[entity].append(blob.name)

    print(f"  Total blobs found : {total_blob_count}")

    all_entities = sorted(
        set(entity_metadata_blobs) | set(entity_data_blobs) | set(entity_other_blobs)
    )

    if not all_entities:
        print("\n  No entities found in the container.  The Bronze layer may be empty.")
        return

    print(f"  Entities detected : {len(all_entities)}\n")

    # Per-entity detail pass
    rows: list[dict[str, Any]] = []

    for entity in all_entities:
        meta_blobs = entity_metadata_blobs.get(entity, [])
        data_blobs = entity_data_blobs.get(entity, [])

        meta_count = len(meta_blobs)
        data_count = len(data_blobs)
        data_size_kb = sum(size for _, size in data_blobs) / 1024

        snap_info = fetch_table_metadata(container_client, entity, meta_blobs)

        rows.append(
            {
                "entity": entity,
                "meta_files": meta_count,
                "data_files": data_count,
                "has_snapshots": snap_info["has_snapshots"],
                "snapshot_count": snap_info["snapshot_count"],
                "current_snapshot_id": snap_info["current_snapshot_id"],
                "data_size_kb": data_size_kb,
                "status": "HAS DATA" if snap_info["has_snapshots"] and data_count > 0 else "SCHEMA ONLY",
            }
        )

    # ── Print summary table ────────────────────────────────────────────────────

    col_entity  = max(len(r["entity"]) for r in rows)
    col_entity  = max(col_entity, 6)   # min width = len("Entity")

    header = (
        f"{'Entity':<{col_entity}}  "
        f"{'Meta':>5}  "
        f"{'Data':>5}  "
        f"{'Snapshots':>9}  "
        f"{'Snap Count':>10}  "
        f"{'Size (KB)':>10}  "
        f"{'Status':<12}"
    )
    separator = "-" * len(header)

    print(separator)
    print(header)
    print(separator)

    has_data_entities: list[str] = []
    schema_only_entities: list[str] = []

    for r in rows:
        snap_yn = "YES" if r["has_snapshots"] else "NO"
        print(
            f"{r['entity']:<{col_entity}}  "
            f"{r['meta_files']:>5}  "
            f"{r['data_files']:>5}  "
            f"{snap_yn:>9}  "
            f"{r['snapshot_count']:>10}  "
            f"{r['data_size_kb']:>10.1f}  "
            f"{r['status']:<12}"
        )
        if r["status"] == "HAS DATA":
            has_data_entities.append(r["entity"])
        else:
            schema_only_entities.append(r["entity"])

    print(separator)
    print()

    # ── Summary section ────────────────────────────────────────────────────────
    print("SUMMARY")
    print("-------")
    print(f"  Total entities   : {len(rows)}")
    print(f"  With data        : {len(has_data_entities)}")
    print(f"  Schema-only      : {len(schema_only_entities)}")

    if has_data_entities:
        print(f"\n  Entities with data ({len(has_data_entities)}):")
        for e in has_data_entities:
            print(f"    - {e}")

    if schema_only_entities:
        print(f"\n  Schema-only entities ({len(schema_only_entities)}):")
        for e in schema_only_entities:
            print(f"    - {e}")

    # ── Grand total ───────────────────────────────────────────────────────────
    total_data_files = sum(r["data_files"] for r in rows)
    total_meta_files = sum(r["meta_files"] for r in rows)
    total_size_kb    = sum(r["data_size_kb"] for r in rows)
    print()
    print(f"  Total metadata files : {total_meta_files}")
    print(f"  Total data files     : {total_data_files}")
    print(f"  Total data size      : {total_size_kb:.1f} KB  ({total_size_kb / 1024:.2f} MB)")
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    conn_str, container = resolve_config()
    analyze_bronze(conn_str, container)
