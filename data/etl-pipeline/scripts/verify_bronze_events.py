"""Read-only verifier: confirm bronze.raw_events stores full event payload."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

env_file = Path(__file__).parent.parent / ".env.local"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

import adlfs
import fsspec
fsspec.config.conf["adlfs"] = {
    "account_name": os.environ["AZURE_STORAGE_ACCOUNT_NAME"],
    "account_key":  os.environ["AZURE_STORAGE_ACCOUNT_KEY"],
}

from pyiceberg.catalog import load_catalog

cat = load_catalog(
    "bronze",
    **{
        "type": "rest",
        "uri": os.environ.get("ETL_ICEBERG_REST_URI", "http://localhost:8181"),
        "py-io-impl": "pyiceberg.io.fsspec.FsspecFileIO",
        "adls.account-name": os.environ["AZURE_STORAGE_ACCOUNT_NAME"],
        "adls.account-key": os.environ["AZURE_STORAGE_ACCOUNT_KEY"],
    },
)
tbl = cat.load_table("bronze.raw_events")
df = tbl.scan(limit=20).to_arrow().to_pandas()

print("=" * 60)
print("Total rows in bronze.raw_events :", len(df))
print("=" * 60)

if len(df) == 0:
    print("NO DATA YET (Bronze still on first checkpoint — wait 60s and retry)")
    sys.exit(0)

print("Entities present:")
print(df["entity"].value_counts().head(15).to_string())
print()

print("=" * 60)
print("FULL PAYLOAD CHECK")
print("=" * 60)
row = df.iloc[0]
print("entity      :", row["entity"])
print("op          :", row["op"])
print("tenantid    :", row["tenantid"])
print("payload len :", len(row["payload"]))
print("payload (first 500 chars):")
print(row["payload"][:500])
print()
try:
    p = json.loads(row["payload"])
    print("payload parses as JSON: YES")
    print("payload top-level keys :", list(p.keys())[:15])
except Exception as e:
    print("payload JSON parse failed:", e)
print()
print("Median payload length across all rows:",
      int(df["payload"].dropna().str.len().median()))
