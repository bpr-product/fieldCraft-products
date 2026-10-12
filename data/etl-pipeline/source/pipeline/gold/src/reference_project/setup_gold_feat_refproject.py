"""Setup the Q&A Recommendation Gold database on Cosmos DB for MongoDB vCore.

Database: gold_qa_recommendation
  qa_answer  — one document per Q&A answer (raw passthrough of silver.answers +
               platform plumbing + embedded BOM lines). Source:
               gold/src/reference_project/load_qa_recommendation.py.
               Upsert key: (tenantId, answer_id).

All collections are idempotent — safe to re-run.

Env:
  MONGO_URI / KEY_VAULT_URL     resolved by shared.secret_resolver
  GOLD_QA_RECOMMENDATION_DB     default "gold_qa_recommendation"
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from pymongo import ASCENDING, MongoClient
from pymongo.errors import OperationFailure

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHARED_SRC = str(_REPO_ROOT / "shared")
if _SHARED_SRC not in sys.path:
    sys.path.insert(0, _SHARED_SRC)
from secret_resolver import get_mongo_uri  # noqa: E402

log = logging.getLogger("qa_recommendation.setup")


def _ensure_collection(db, name: str, validator: dict) -> None:
    try:
        if name in db.list_collection_names():
            db.command({"collMod": name, "validator": validator,
                        "validationLevel": "strict", "validationAction": "error"})
            log.info("[ok] updated validator on %s.%s", db.name, name)
        else:
            db.create_collection(name, validator=validator,
                                 validationLevel="strict", validationAction="error")
            log.info("[ok] created %s.%s", db.name, name)
        _ensure_shard_key(db, name)
    except OperationFailure as exc:
        errmsg = exc.details.get("errmsg", str(exc)) if exc.details else str(exc)
        log.warning("[warn] validator not applied on %s (%s): %s", name, exc.code, errmsg)
        if name not in db.list_collection_names():
            db.create_collection(name)
            log.info("[ok] created %s.%s without validator", db.name, name)
        _ensure_shard_key(db, name)


# ---------------------------------------------------------------------------
# qa_answer — one doc per (tenantId, answer_id)
# ---------------------------------------------------------------------------

def setup_qa_answer(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "answer_id", "_grain", "_schemaVersion", "goldUpdatedAt"],
            "properties": {
                "tenantId":  {"bsonType": "string", "minLength": 1},
                "answer_id": {"bsonType": ["int", "long"]},
                "project_id": {"bsonType": ["int", "long", "null"]},
                "sourceEnv": {"bsonType": ["string", "null"]},
                "isDeleted": {"bsonType": "bool"},
                "_grain":         {"bsonType": "string"},
                "_schemaVersion": {"bsonType": ["int", "long"], "minimum": 1},
                "goldUpdatedAt":  {"bsonType": ["date", "string"]},
            },
        }
    }
    _ensure_collection(db, "qa_answer", validator)
    coll = db["qa_answer"]
    # Matches the loader's upsert filter exactly (tenantId, answer_id,
    # sourceEnv). sourceEnv is included because a tenantid is only unique
    # WITHIN one Silver _s_env, not across envs — without it, two envs that
    # happen to share a tenantid+answer_id would collide on this unique index.
    # Index name kept unchanged even though the key list grew.
    coll.create_index([("tenantId", ASCENDING), ("answer_id", ASCENDING),
                       ("sourceEnv", ASCENDING)],
                      unique=True, name="uq_tenant_answer_id")
    # Per-project Q&A history — the collection's documented access pattern
    # (CLAUDE.md: "Q&A answer history per project").
    coll.create_index([("tenantId", ASCENDING), ("project_id", ASCENDING),
                       ("sourceEnv", ASCENDING)],
                      name="ix_tenant_project_id")
    log.info("[ok] indexes ensured on %s.qa_answer", db.name)


def _ensure_shard_key(db, collection_name: str, shard_key: str = "tenantId") -> None:
    try:
        db.command({
            "shardCollection": f"{db.name}.{collection_name}",
            "key": {shard_key: "hashed"},
        })
        log.info("[ok] shard key '%s' set on %s.%s", shard_key, db.name, collection_name)
    except Exception as exc:
        msg = str(exc)
        if "already sharded" in msg.lower() or "already exists" in msg.lower():
            log.info("[ok] shard key already set on %s.%s", db.name, collection_name)
        else:
            log.warning("[warn] could not set shard key on %s: %s", collection_name, msg)


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        uri = get_mongo_uri()
    except RuntimeError as exc:
        log.error("cannot resolve Mongo URI: %s", exc)
        return 2

    db_name = os.environ.get("GOLD_QA_RECOMMENDATION_DB", "gold_qa_recommendation")
    client = MongoClient(uri, appname="qa-recommendation-setup")
    try:
        db = client[db_name]
        setup_qa_answer(db)
    finally:
        client.close()

    log.info("[done] Q&A Recommendation Gold collections ready in %s", db_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
