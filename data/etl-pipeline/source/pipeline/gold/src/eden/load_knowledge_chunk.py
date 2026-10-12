"""Load knowledge_chunk documents from a JSON source file into Gold MongoDB.

Reads a newline-delimited JSON (NDJSON) or JSON-array file supplied via the
SOURCE_PATH env var or --source argument.  Each record is upserted into
gold_eden.knowledge_chunk.

Vector embeddings are NOT stored in Mongo — they live in the dedicated vector
DB now.  This loader writes only the chunk text + metadata plus a minimal
vector-linkage mapping (``embeddingModel`` / ``embeddingVersion`` recording the
model that the vector-DB side embeds with, and ``vectorSyncedAt`` which the
vector-DB upsert path stamps once the chunk's vector is published).  Keeping
those three scalars — not the vector — lets us re-embed on a model change and
detect Mongo/vector-DB drift without bloating the document.

Moved into the Eden feature (was gold/src/ai_assistant/) — the AI Assistant
RAG knowledge base is now an Eden batch loader writing to the gold_eden
database.

Expected input record shape (per item):
  {
    "chunkId":     "...",
    "sourceType":  "sop|spec|help|faq",
    "sourceDocId": "...",          # optional
    "sourceTitle": "...",          # optional
    "text":        "...",
    "metadata":    {}              # optional
  }

Upsert key : (tenantId, chunkId) — one document per chunk.

Exit codes:
  0  success
  2  configuration / connection error
  3  load failure after retries exhausted
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse, urlunparse

from pymongo import MongoClient, UpdateOne
from pymongo.collection import Collection
from pymongo.errors import (
    AutoReconnect,
    BulkWriteError,
    ConnectionFailure,
    NetworkTimeout,
    OperationFailure,
    ServerSelectionTimeoutError,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHARED_SRC = str(_REPO_ROOT / "shared")
_GOLD_SRC = str(_REPO_ROOT / "gold" / "src")
_DATA_PIPELINE = str(_REPO_ROOT)
if _SHARED_SRC not in sys.path:
    sys.path.insert(0, _SHARED_SRC)
if _GOLD_SRC not in sys.path:
    sys.path.insert(0, _GOLD_SRC)
if _DATA_PIPELINE not in sys.path:
    sys.path.insert(0, _DATA_PIPELINE)
from secret_resolver import get_mongo_uri  # noqa: E402
from gold.pipeline.lineage import lineage_fields, parse_ts, set_on_insert  # noqa: E402

COLLECTION = "knowledge_chunk"
FEATURE_NAME = "eden"

# Vector-linkage config — vectors live in the dedicated vector DB, not Mongo.
# These record which model the vector-DB side embeds with so Mongo docs stay
# joinable / re-embeddable; they are intentionally nullable (the vector-DB push
# is the source of truth and stamps vectorSyncedAt).
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL") or None
EMBEDDING_VERSION = os.environ.get("EMBEDDING_VERSION") or None
TRANSIENT_ERRORS = (
    AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError
)

log = logging.getLogger("eden.knowledge_chunk_loader")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LoaderConfig:
    mongo_uri: str
    db_name: str
    tenantid: str
    source_path: Path
    batch_size: int
    max_retries: int
    retry_backoff_s: float
    dry_run: bool

    @staticmethod
    def from_args_and_env(argv: list[str] | None = None) -> "LoaderConfig":
        p = argparse.ArgumentParser(
            description="JSON file → Gold loader: knowledge_chunk."
        )
        p.add_argument("--mongo-uri", default=None)
        p.add_argument(
            "--db",
            dest="db_name",
            default=os.environ.get("GOLD_EDEN_DB", "gold_eden"),
        )
        p.add_argument("--tenant", required=not bool(os.environ.get("TENANT_ID")), default=os.environ.get("TENANT_ID"))
        p.add_argument(
            "--source",
            default=os.environ.get("SOURCE_PATH"),
            help="Path to NDJSON or JSON-array source file.",
        )
        p.add_argument(
            "--batch-size",
            type=int,
            default=int(os.environ.get("BATCH_SIZE", "200")),
        )
        p.add_argument(
            "--max-retries",
            type=int,
            default=int(os.environ.get("MAX_RETRIES", "5")),
        )
        p.add_argument(
            "--retry-backoff",
            type=float,
            default=float(os.environ.get("RETRY_BACKOFF_S", "1.5")),
        )
        p.add_argument("--dry-run", action="store_true")
        ns = p.parse_args(argv)

        if not ns.source:
            raise RuntimeError(
                "SOURCE_PATH env var or --source argument is required"
            )

        return LoaderConfig(
            mongo_uri=ns.mongo_uri or get_mongo_uri(),
            db_name=ns.db_name,
            tenantid=ns.tenant,
            source_path=Path(ns.source),
            batch_size=ns.batch_size,
            max_retries=ns.max_retries,
            retry_backoff_s=ns.retry_backoff,
            dry_run=ns.dry_run,
        )


def _mask_uri(uri: str) -> str:
    try:
        u = urlparse(uri)
        if u.password:
            netloc = f"{u.username}:***@{u.hostname}"
            if u.port:
                netloc += f":{u.port}"
            return urlunparse(u._replace(netloc=netloc))
        return uri
    except Exception:
        return re.sub(r"://[^@]+@", "://***@", uri)


# ---------------------------------------------------------------------------
# Source file reader — supports NDJSON and JSON array
# ---------------------------------------------------------------------------

def iter_source_records(source_path: Path) -> Iterator[dict]:
    """Yield records from a NDJSON or JSON-array source file."""
    raw = source_path.read_text(encoding="utf-8").strip()
    if raw.startswith("["):
        for record in json.loads(raw):
            yield record
    else:
        for line in raw.splitlines():
            line = line.strip()
            if line:
                yield json.loads(line)


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------

def shape_doc(
    record: dict,
    tenantid: str,
    gold_ts: datetime,
) -> dict:
    doc = {
        "tenantId":        tenantid,
        "chunkId":         record["chunkId"],
        "_schemaVersion":  1,
        "_grain":          "knowledge_chunk_rag",
        "sourceType":      record["sourceType"],
        "sourceDocId":     record.get("sourceDocId"),
        "sourceTitle":     record.get("sourceTitle"),
        "text":            record.get("text"),
        # Vector-linkage mapping — the vector itself lives in the vector DB.
        # vectorSyncedAt is None until the vector-DB upsert path stamps it.
        "embeddingModel":  EMBEDDING_MODEL,
        "embeddingVersion": EMBEDDING_VERSION,
        "vectorSyncedAt":  None,
        "metadata":        record.get("metadata", {}),
        "isDeleted":  False,
        "createdAt":  parse_ts(gold_ts),
        "updatedAt":  parse_ts(gold_ts),
    }
    doc.update(lineage_fields(FEATURE_NAME, gold_ts, iso=False))
    return doc


# ---------------------------------------------------------------------------
# Bulk write with retry
# ---------------------------------------------------------------------------

@dataclass
class WriteStats:
    upserted: int = 0
    modified: int = 0
    matched:  int = 0
    skipped:  int = 0
    failed:   int = 0
    batches:  int = 0


def bulk_write_with_retry(
    coll: Collection,
    ops: list[UpdateOne],
    cfg: LoaderConfig,
    stats: WriteStats,
) -> None:
    delay = cfg.retry_backoff_s
    for attempt in range(1, cfg.max_retries + 1):
        try:
            res = coll.bulk_write(ops, ordered=False)
            stats.upserted += res.upserted_count or 0
            stats.modified += res.modified_count or 0
            stats.matched  += res.matched_count  or 0
            stats.batches  += 1
            return
        except BulkWriteError as exc:
            errors = exc.details.get("writeErrors", []) if exc.details else []
            stats.failed += len(errors)
            log.error(
                "bulk write partial failure: %d errors (first: %s)",
                len(errors), errors[0] if errors else "",
            )
            stats.batches += 1
            return
        except TRANSIENT_ERRORS as exc:
            if attempt == cfg.max_retries:
                log.error("bulk write failed after %d attempts: %s", attempt, exc)
                raise
            log.warning(
                "transient error attempt %d/%d: %s — retry in %.1fs",
                attempt, cfg.max_retries, exc, delay,
            )
            time.sleep(delay)
            delay *= 2
        except OperationFailure as exc:
            log.error("operation failure (non-retryable): %s", exc)
            raise


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

_shutdown = False


def _install_signal_handlers() -> None:
    def _handler(signum, _frame):
        global _shutdown
        log.warning("signal %s received — finishing current batch then stopping", signum)
        _shutdown = True

    signal.signal(signal.SIGINT, _handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handler)


def run(cfg: LoaderConfig) -> int:
    log.info(
        "loader start | db=%s.%s tenant=%s dry_run=%s source=%s mongo=%s",
        cfg.db_name, COLLECTION, cfg.tenantid,
        cfg.dry_run, cfg.source_path, _mask_uri(cfg.mongo_uri),
    )

    if not cfg.source_path.exists():
        log.error("source file not found: %s", cfg.source_path)
        return 2

    log.info(
        "vector linkage | embeddingModel=%s embeddingVersion=%s "
        "(vectors stored in the dedicated vector DB, not Mongo)",
        EMBEDDING_MODEL, EMBEDDING_VERSION,
    )

    gold_ts = datetime.now(timezone.utc)
    stats = WriteStats()

    client: MongoClient | None = None
    coll: Collection | None = None
    if not cfg.dry_run:
        client = MongoClient(
            cfg.mongo_uri,
            serverSelectionTimeoutMS=15_000,
            connectTimeoutMS=15_000,
            socketTimeoutMS=60_000,
            retryWrites=False,
            appname="eden-knowledge-chunk-loader",
        )
        coll = client[cfg.db_name][COLLECTION]

    pending: list[dict] = []
    seen = 0

    def flush() -> None:
        if not pending:
            return
        ops: list[UpdateOne] = []
        for record in pending:
            doc = shape_doc(record, cfg.tenantid, gold_ts)
            ops.append(
                UpdateOne(
                    {"tenantId": cfg.tenantid, "chunkId": doc["chunkId"]},
                    {"$set": doc, "$setOnInsert": set_on_insert(gold_ts, iso=False)},
                    upsert=True,
                )
            )
        if ops and not cfg.dry_run and coll is not None:
            bulk_write_with_retry(coll, ops, cfg, stats)
        pending.clear()

    try:
        for record in iter_source_records(cfg.source_path):
            if _shutdown:
                break
            seen += 1
            pending.append(record)
            if len(pending) >= cfg.batch_size:
                flush()
                log.info(
                    "progress | seen=%d upserted=%d modified=%d skipped=%d",
                    seen, stats.upserted, stats.modified, stats.skipped,
                )
        flush()

    except (OSError, json.JSONDecodeError) as exc:
        log.error("source file read error: %s", exc)
        return 3
    except TRANSIENT_ERRORS as exc:
        log.error("aborting after exhausted retries: %s", exc)
        return 3
    finally:
        if client is not None:
            client.close()

    total: int | None = None
    if not cfg.dry_run:
        verify_client = MongoClient(
            cfg.mongo_uri, appname="eden-knowledge-chunk-verify"
        )
        try:
            total = verify_client[cfg.db_name][COLLECTION].count_documents(
                {"tenantId": cfg.tenantid}
            )
        finally:
            verify_client.close()

    log.info(
        "loader done | seen=%d upserted=%d modified=%d matched=%d "
        "skipped=%d failed=%d batches=%d tenant_total=%s",
        seen, stats.upserted, stats.modified, stats.matched,
        stats.skipped, stats.failed, stats.batches, total,
    )
    return 0 if stats.failed == 0 else 3


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _install_signal_handlers()
    try:
        cfg = LoaderConfig.from_args_and_env(argv)
    except RuntimeError as exc:
        log.error("config error: %s", exc)
        return 2
    return run(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
