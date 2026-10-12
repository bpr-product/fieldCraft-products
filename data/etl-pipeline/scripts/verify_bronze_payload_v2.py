"""Download one bronze.raw_events parquet via Azure Blob REST + shared-key signing, then inspect payload column."""
import base64
import datetime
import hashlib
import hmac
import io
import json
import os
import sys
from pathlib import Path
import urllib.request
import xml.etree.ElementTree as ET

env_file = Path(__file__).parent.parent / ".env.local"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

import pyarrow.parquet as pq

ACCOUNT = os.environ["AZURE_STORAGE_ACCOUNT_NAME"]
KEY = os.environ["AZURE_STORAGE_ACCOUNT_KEY"]
CONTAINER = "bronze"
HOST = f"{ACCOUNT}.blob.core.windows.net"


def sign(method: str, url_path: str, query: dict, headers: dict) -> str:
    """Build SharedKey authorization header per Azure Blob REST spec."""
    canon_headers_pairs = sorted(
        [(k.lower(), v.strip()) for k, v in headers.items() if k.lower().startswith("x-ms-")]
    )
    canon_headers = "".join(f"{k}:{v}\n" for k, v in canon_headers_pairs)

    canon_resource = f"/{ACCOUNT}{url_path}"
    if query:
        canon_resource += "\n" + "\n".join(f"{k.lower()}:{v}" for k, v in sorted(query.items()))

    string_to_sign = (
        f"{method}\n"
        "\n"  # Content-Encoding
        "\n"  # Content-Language
        "\n"  # Content-Length (GET)
        "\n"  # Content-MD5
        "\n"  # Content-Type
        "\n"  # Date — use x-ms-date instead
        "\n"  # If-Modified-Since
        "\n"  # If-Match
        "\n"  # If-None-Match
        "\n"  # If-Unmodified-Since
        "\n"  # Range
        f"{canon_headers}"
        f"{canon_resource}"
    )
    sig = base64.b64encode(
        hmac.new(base64.b64decode(KEY), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")
    return f"SharedKey {ACCOUNT}:{sig}"


def request(method: str, url_path: str, query: dict = None):
    query = query or {}
    date = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    headers = {"x-ms-date": date, "x-ms-version": "2020-10-02"}
    headers["Authorization"] = sign(method, url_path, query, headers)

    qs = "&".join(f"{k}={v}" for k, v in query.items())
    url = f"https://{HOST}{url_path}" + (f"?{qs}" if qs else "")
    req = urllib.request.Request(url, method=method, headers=headers)
    with urllib.request.urlopen(req) as r:
        return r.read()


print("=" * 70)
print(f"Listing parquet files in container '{CONTAINER}'/bronze/raw_events/data/")
print("=" * 70)
body = request("GET", f"/{CONTAINER}", {"restype": "container", "comp": "list", "prefix": "bronze/raw_events/data/"})
root = ET.fromstring(body)
blobs = [b.findtext("Name") for b in root.iter("Blob") if b.findtext("Name", "").endswith(".parquet")]
print(f"Found {len(blobs)} parquet data files")
if not blobs:
    sys.exit(0)

latest = sorted(blobs)[-1]
print(f"Reading latest: {latest}")
body = request("GET", f"/{CONTAINER}/{latest}")
table = pq.read_table(io.BytesIO(body))

df = table.to_pandas()
print()
print("=" * 70)
print(f"Rows in this parquet file : {len(df)}")
print(f"Columns                   : {list(df.columns)}")
print("=" * 70)

row = df.iloc[0]
print()
print("--- Row 0 envelope ---")
print(f"  source_type : {row['source_type']}")
print(f"  entity      : {row['entity']}")
print(f"  op          : {row['op']}")
print(f"  tenantid    : {row['tenantid']}")
print(f"  payload len : {len(row['payload']) if row['payload'] else 0}")
print()
print("--- Row 0 PAYLOAD (first 1200 chars) ---")
print((row["payload"] or "")[:1200])
print()
print("--- Payload stats across this file ---")
print(f"  NULL payloads   : {df['payload'].isnull().sum()}")
print(f"  Empty payloads  : {(df['payload'] == '').sum()}")
nonempty = df["payload"].dropna()
if len(nonempty) > 0:
    lens = nonempty.str.len()
    print(f"  Min length      : {lens.min()}")
    print(f"  Median length   : {int(lens.median())}")
    print(f"  Max length      : {lens.max()}")
print()
try:
    p = json.loads(row["payload"])
    print(f"Row 0 payload parses as JSON. {len(p)} top-level keys:")
    print(f"  {list(p.keys())[:20]}")
except Exception as e:
    print("JSON parse error:", e)
