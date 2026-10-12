import logging
import os

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import ConnectionFailure

from source.utils.secrets import get_secret

load_dotenv()

logger = logging.getLogger(__name__)

_client: MongoClient | None = None
_reachable: bool | None = None


def get_mongo_client() -> MongoClient:
    global _client
    if _client is None:
        # Key Vault first (production), env var fallback (local dev / Key
        # Vault unreachable) -- see source/utils/secrets.py. AZURE_KEYVAULT_NAME
        # unset means this resolves to the env var exactly as before.
        uri = get_secret("mongodb-uri", "MONGODB_URI", "mongodb://localhost:27017")
        # 2000ms was tuned for a local/placeholder Mongo failing fast; a real
        # remote cluster (e.g. Azure CosmosDB's Mongo API) needs longer for
        # the TLS+SCRAM handshake over the internet — verified directly:
        # the same URI that times out at 2000ms connects fine at 8000ms.
        # Still bounded well under request-hanging territory, and this only
        # pays the cost once per process via is_reachable()'s cache below.
        _client = MongoClient(
            uri,
            serverSelectionTimeoutMS=8000,
            connectTimeoutMS=8000,
            socketTimeoutMS=8000,
        )
    return _client


def get_collection(collection_name: str, db_name: str | None = None) -> Collection:
    """db_name defaults to eden_gold (Data Engineering's curated, ETL-maintained
    analytical store — see EDEN_Data_Requirements.md / datafinal.md's Gold Layer
    Reference).

    Per explicit user direction, EDEN's own operational writes (audit log,
    conversation history) now ALSO land in this same database, in the two
    collections Data Engineering already defined for exactly this purpose --
    `agent_audit` and `agent_memory` (see datafinal.md section 3.3,
    "Agent-Operational Collections": both have no Silver source and are meant
    to be written directly by the agent runtime, same as EDEN already does
    here). This replaces a previous separate `eden_operational` database
    (`nav_audit_log`/`nav_conversation_history`) that duplicated what these
    two collections already exist for. See source/utils/audit_log.py and
    source/resolvers/conversation_memory.py for the writers."""
    client = get_mongo_client()
    db_name = db_name or os.getenv("MONGODB_DB", "eden_gold")
    return client[db_name][collection_name]


def ping() -> bool:
    try:
        get_mongo_client().admin.command("ping")
        return True
    except ConnectionFailure:
        logger.error("MongoDB connection failed")
        return False


def is_reachable() -> bool:
    """Cached reachability check — ping() alone still pays the full 2s
    serverSelectionTimeoutMS on every call, which meant every audit-log and
    conversation-history write attempted (and failed) a fresh 2s connection
    on every single request while MONGODB_URI is a placeholder/unset,
    adding ~4s of latency to every navigation request regardless of how it
    was resolved. Checked once per process (same restart-required pattern as
    every other .env change here — see CROSS_MACHINE_TESTING.md); if Mongo
    comes back up mid-process, callers won't notice until next restart, but
    that's the right trade for not re-paying 2s per request indefinitely."""
    global _reachable
    if _reachable is None:
        _reachable = ping()
        if not _reachable:
            logger.warning(
                "MongoDB unreachable — operational writes (audit log, "
                "conversation history) disabled for this process"
            )
    return _reachable
