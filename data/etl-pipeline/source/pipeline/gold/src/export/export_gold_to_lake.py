"""Export Gold feature documents to the ADLS Gen2 data lake as Parquet.

WHY: BPE feature_store feeds ML training. Cosmos (NoSQL serving DB) is the wrong
shape for that — point-reads, not columnar; current-state, no history. This
publishes BPE to the lake (open Parquet, partitioned by snapshot_date → gives
feature HISTORY) so data science trains off the lake while apps keep reading Cosmos.

SCOPE: **BPE only.** QA (qa_answer) and other Gold features stay Mongo-only — they
are served (point reads / vector similarity), not training targets. Only BPE is in
EXPORTABLE; the CLI will not export anything else.

  Cosmos = serve fast (current state).   Lake = train/analyze cheap (history, columnar).

Layout:  {dest}/{feature}/snapshot_date=YYYY-MM-DD/tenant={tenant}.parquet
  - each run writes today's full feature set per tenant → snapshot_date partitions
    accumulate a time series ML can train on (point-in-time or trend).
  - dest is a local path (testing) OR az://{container}/... on **Azure Blob Storage**
    (prod; needs the `adlfs` package). adlfs talks to the blob endpoint
    (*.blob.core.windows.net) — works for plain Blob Storage (no HNS required).
    Hive-style "directories" are just key prefixes on blob; Parquet reads fine.

Source is Gold (Cosmos) — a faithful mirror of what is served. Ensure Gold is
populated first (`run_pipeline.py --rebuild`) so the export is complete.

Nested fields (features, bom_items, answers_data, …) are JSON-encoded to strings
so the Parquet schema is stable across docs and any engine (Spark/pandas/Synapse)
can read it; ML does `json.loads` on the columns it needs.

Usage:
    python -m gold.src.export.export_gold_to_lake --all-tenants
    python -m gold.src.export.export_gold_to_lake --feature bpe --tenant tnXXX --dest C:/tmp/gold_lake
    python -m gold.src.export.export_gold_to_lake --all-tenants --snapshot-date 2026-06-12
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for p in (str(_REPO), str(_REPO / "shared")):
    if p not in sys.path:
        sys.path.insert(0, p)

from gold.src.reconcile.reconcile_silver_gold import SPECS  # reuse feature→db/collection

# Only BPE is exported to the lake (ML training). QA/others are Mongo-only (serving).
EXPORTABLE = ["bpe"]

log = logging.getLogger("gold.export")


# ---------------------------------------------------------------------------
# Pure helpers (unit tested)
# ---------------------------------------------------------------------------

def normalize_doc(doc: dict) -> dict:
    """Flatten a Gold doc into a Parquet-friendly row: drop _id, JSON-encode
    nested dict/list values (stable schema, engine-agnostic), keep scalars."""
    out: dict = {}
    for k, v in doc.items():
        if k == "_id":
            continue
        if isinstance(v, (dict, list)):
            out[k] = json.dumps(v, default=str)
        else:
            out[k] = v
    return out


def partition_path(dest_base: str, feature: str, snapshot_date: str, tenant: str) -> str:
    """Hive-style partitioned path: {dest}/{feature}/snapshot_date=.../tenant=....parquet"""
    return (f"{dest_base.rstrip('/')}/{feature}"
            f"/snapshot_date={snapshot_date}/tenant={tenant}.parquet")


_AZURE_PROTOCOLS = ("az://", "abfs://", "abfss://", "wasb://", "wasbs://")


def _storage_options(dest: str) -> dict | None:
    """For Azure (blob) destinations, supply account creds (adlfs → blob endpoint).
    None for a local path. adlfs uses account_name to reach *.blob.core.windows.net,
    so plain Blob Storage (no HNS) works."""
    if dest.startswith(_AZURE_PROTOCOLS):
        # Resolve the storage account key via Key Vault (consistent with how the
        # rest of the codebase fetches credentials), falling back to the env var.
        # Keeping the key out of a plain .env on disk shrinks the blast radius of
        # a compromised host.
        try:
            from secret_resolver import get_secret  # type: ignore[import]
            account_key = get_secret(
                "azure-storage-account-key", env_fallback="AZURE_STORAGE_ACCOUNT_KEY"
            )
        except Exception:
            account_key = os.environ.get("AZURE_STORAGE_ACCOUNT_KEY", "")
        return {
            "account_name": os.environ.get("AZURE_STORAGE_ACCOUNT_NAME", ""),
            "account_key": account_key,
        }
    return None


def _default_dest() -> str:
    """Azure Blob gold-zone from env (az://), else a local default."""
    if os.environ.get("GOLD_LAKE_DEST"):
        return os.environ["GOLD_LAKE_DEST"]
    acct = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME")
    container = os.environ.get("GOLD_LAKE_CONTAINER", "gold")
    if acct:
        # Blob Storage: az:// + account_name (adlfs → {acct}.blob.core.windows.net).
        return f"az://{container}/gold-zone"
    return str(_REPO / "_gold_lake")   # local fallback


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def _gold_db(spec) -> str:
    return os.environ.get(spec.gold_db_env, spec.gold_db_default)


def discover_tenants(mongo_uri: str, feature: str) -> list[str]:
    from pymongo import MongoClient
    spec = SPECS[feature]
    cli = MongoClient(mongo_uri, serverSelectionTimeoutMS=15000, appname="gold-export")
    try:
        return sorted(t for t in cli[_gold_db(spec)][spec.gold_collection].distinct("tenantId") if t)
    finally:
        cli.close()


def export_feature(feature: str, tenant: str, dest_base: str, snapshot_date: str,
                   mongo_uri: str, *, dry_run: bool = False) -> tuple[int, str]:
    """Export one (feature, tenant) snapshot to Parquet. Returns (rows, path)."""
    from pymongo import MongoClient
    spec = SPECS[feature]
    cli = MongoClient(mongo_uri, serverSelectionTimeoutMS=15000, appname="gold-export")
    try:
        docs = list(cli[_gold_db(spec)][spec.gold_collection].find({"tenantId": tenant}))
    finally:
        cli.close()
    rows = [normalize_doc(d) for d in docs]
    path = partition_path(dest_base, feature, snapshot_date, tenant)
    if dry_run or not rows:
        return len(rows), path
    storage_options = _storage_options(dest_base)
    if storage_options is None:
        # Local filesystem: pyarrow won't create parent dirs; abfs/fsspec does.
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    import pandas as pd
    pd.DataFrame(rows).to_parquet(path, engine="pyarrow", index=False,
                                  storage_options=storage_options)
    return len(rows), path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(description="Export Gold → ADLS lake (Parquet) for ML/analytics")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--tenant", help="single tenant id")
    g.add_argument("--all-tenants", action="store_true")
    p.add_argument("--feature", choices=EXPORTABLE, action="append",
                   help="feature(s) to export; only BPE is exportable (QA/others are Mongo-only)")
    p.add_argument("--dest", default=None, help="lake base path (local dir or abfs://...); default from env")
    p.add_argument("--snapshot-date", default=None, help="YYYY-MM-DD; default today (UTC)")
    p.add_argument("--mongo-uri", default=None)
    p.add_argument("--dry-run", action="store_true", help="count rows + show paths, write nothing")
    ns = p.parse_args(argv)

    from secret_resolver import get_mongo_uri  # type: ignore[import]
    mongo_uri = ns.mongo_uri or get_mongo_uri()
    dest = ns.dest or _default_dest()
    snapshot_date = ns.snapshot_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    features = ns.feature or list(EXPORTABLE)

    total = 0
    for feature in features:
        tenants = [ns.tenant] if ns.tenant else discover_tenants(mongo_uri, feature)
        for tenant in tenants:
            try:
                n, path = export_feature(feature, tenant, dest, snapshot_date, mongo_uri,
                                         dry_run=ns.dry_run)
            except Exception as exc:   # noqa: BLE001
                log.error("export failed | feature=%s tenant=%s err=%s", feature, tenant, exc)
                return 1
            total += n
            verb = "would export" if ns.dry_run else "exported"
            log.info("%s %d row(s) | %s/%s → %s", verb, n, feature, tenant, path)
    log.info("DONE | %s %d row(s) to %s (snapshot_date=%s)",
             "would export" if ns.dry_run else "exported", total, dest, snapshot_date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
