"""Create all platform databases and collections on Cosmos DB for MongoDB vCore.

Covers every layer defined in docs/architecture/platform_data_architecture.md
that has an implemented setup function:

  Canonical Gold   : gold_event
                     (gold_project/gold_task/gold_inventory/gold_subscription/
                     gold_rule are planned per the architecture doc but not
                     yet implemented -- their setup functions do not exist
                     yet, so they are intentionally omitted from `plan` below
                     rather than referencing undefined names)
  Feature Store    : ml_feature_db
  Model Training   : ml_training
  Model Registry   : ml_model_registry
  Model Serving    : prediction_serving_db, recommendation_serving,
                     ai_assistant_serving
  Search           : search_index

Vector embeddings are NOT stored in Mongo — they live in the dedicated vector
DB. Feature documents keep only the embeddingModel/embeddingVersion/
vectorSyncedAt linkage; no Mongo vector collections or cosmosSearch indexes are
created here.

Idempotent — safe to re-run. Existing collections get their validator updated;
existing indexes are left in place (Cosmos vCore does not support in-place
index redefinition).

Env vars:
  MONGO_URI or KEY_VAULT_URL + secret "gold-mongo-uri"
  ML_FEATURE_DB_NAME       default "ml_feature_db"
  FEATURE_STORE_DB_NAME    alias for ML_FEATURE_DB_NAME
  PREDICTION_SERVING_DB_NAME default "prediction_serving_db"
  PREDICTION_STORE_DB_NAME alias for PREDICTION_SERVING_DB_NAME
  LOG_LEVEL                default INFO

Exit codes:
  0  success
  2  configuration error
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from pymongo import ASCENDING, DESCENDING, MongoClient, TEXT
from pymongo.errors import OperationFailure

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHARED_SRC = str(_REPO_ROOT / "shared")
if _SHARED_SRC not in sys.path:
    sys.path.insert(0, _SHARED_SRC)
from secret_resolver import get_mongo_uri  # noqa: E402

log = logging.getLogger("platform.setup")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_collection(db, name: str, validator: dict) -> None:
    """Create or update a collection with a $jsonSchema validator.

    Falls back to plain collection when Cosmos vCore rejects the validator
    (unsupported tier or command syntax).
    """
    try:
        if name in db.list_collection_names():
            db.command({
                "collMod": name,
                "validator": validator,
                "validationLevel": "strict",
                "validationAction": "error",
            })
            log.info("[ok] updated validator  %s.%s", db.name, name)
        else:
            db.create_collection(
                name,
                validator=validator,
                validationLevel="strict",
                validationAction="error",
            )
            log.info("[ok] created            %s.%s", db.name, name)
    except OperationFailure as exc:
        errmsg = exc.details.get("errmsg", str(exc)) if exc.details else str(exc)
        log.warning("[warn] validator not applied on %s.%s (%s): %s",
                    db.name, name, exc.code, errmsg)
        if name not in db.list_collection_names():
            db.create_collection(name)
            log.info("[ok] created (no validator) %s.%s", db.name, name)


# Common bsonType shortcuts
_STR      = {"bsonType": "string", "minLength": 1}
_STR_NULL = {"bsonType": ["string", "null"]}
_BOOL     = {"bsonType": "bool"}
_DATE     = {"bsonType": ["date", "string"]}
_INT      = {"bsonType": ["int", "long"]}
_NUM      = {"bsonType": ["double", "int", "long", "decimal", "null"]}


def _base_required() -> list[str]:
    return ["tenantId", "_schemaVersion", "_grain", "_etlUpdatedAt",
            "isDeleted", "updatedAt"]


def _base_props() -> dict:
    return {
        "tenantId":       _STR,
        "_schemaVersion": {"bsonType": ["int", "long"], "minimum": 1},
        "_grain":         _STR,
        "_etlUpdatedAt":  _DATE,
        "isDeleted":      _BOOL,
        "updatedAt":      _DATE,
    }


# ---------------------------------------------------------------------------
# ── CANONICAL GOLD ──────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def setup_gold_event(db) -> None:
    """gold_event: event_processing_sum · workflow_metrics"""

    _ensure_collection(db, "event_processing_sum", {"$jsonSchema": {
        "bsonType": "object",
        "required": _base_required() + ["entity", "month"],
        "properties": {
            **_base_props(),
            "entity":        _STR,
            "month":         _STR,
            "eventCount":    _INT,
            "errorCount":    _INT,
            "errorRatePct":  _NUM,
            "throughputEps": _NUM,
        },
    }})
    coll = db["event_processing_sum"]
    coll.create_index(
        [("tenantId", ASCENDING), ("entity", ASCENDING), ("month", ASCENDING)],
        unique=True, name="uq_tenant_entity_month")
    log.info("[ok] indexes  gold_event.event_processing_sum")

    _ensure_collection(db, "workflow_metrics", {"$jsonSchema": {
        "bsonType": "object",
        "required": _base_required() + ["workflowType", "month"],
        "properties": {
            **_base_props(),
            "workflowType":        _STR,
            "month":               _STR,
            "completionRatePct":   _NUM,
            "avgStageDurationMs":  _NUM,
            "avgHandoffLatencyMs": _NUM,
        },
    }})
    coll = db["workflow_metrics"]
    coll.create_index(
        [("tenantId", ASCENDING), ("workflowType", ASCENDING), ("month", ASCENDING)],
        unique=True, name="uq_tenant_workflow_month")
    log.info("[ok] indexes  gold_event.workflow_metrics")


# ---------------------------------------------------------------------------
# ── FEATURE STORE ────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def setup_ml_feature(db) -> None:
    """ml_feature_db feature store: feature_project · feature_task · feature_inventory
                      feature_subscription · feature_resource
    """


def setup_feature_store(db) -> None:
    """Alias for the ml_feature_db feature store setup."""
    setup_ml_feature(db)
    feature_collections = {
        "feature_project": {
            "entityId": "projectId",
            "extra_props": {
                "budgetVariancePct":    _NUM,
                "scheduleDelayDays":    _NUM,
                "phaseCompletionRate":  _NUM,
                "activeTaskCount":      _INT,
            },
        },
        "feature_task": {
            "entityId": "taskId",
            "extra_props": {
                "completionRate7d":    _NUM,
                "avgCycleTimeHours":   _NUM,
                "blockedRatio":        _NUM,
                "reassignmentCount":   _INT,
            },
        },
        "feature_inventory": {
            "entityId": "materialId",
            "extra_props": {
                "stockUtilizationRate":  _NUM,
                "orderLeadTimeDays":     _NUM,
                "wasteRatio":            _NUM,
                "reorderFrequency":      _NUM,
            },
        },
        "feature_subscription": {
            "entityId": "subscriptionId",
            "extra_props": {
                "arpu30d":            _NUM,
                "featureAdoptionScore": _NUM,
                "paymentDelayDays":   _NUM,
                "churnSignal":        _NUM,
            },
        },
        "feature_resource": {
            "entityId": "workerId",
            "extra_props": {
                "utilizationRate7d":  _NUM,
                "overtimeRatio":      _NUM,
                "skillMatchScore":    _NUM,
            },
        },
    }

    for col_name, meta in feature_collections.items():
        entity_id_field = meta["entityId"]
        _ensure_collection(db, col_name, {"$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", entity_id_field, "asOfTs", "featureVersion"],
            "properties": {
                "tenantId":      _STR,
                entity_id_field: _STR,
                "asOfTs":        _DATE,
                "featureVersion": {"bsonType": "string"},
                "isDeleted":     _BOOL,
                **meta["extra_props"],
            },
        }})
        coll = db[col_name]
        coll.create_index(
            [("tenantId", ASCENDING), (entity_id_field, ASCENDING),
             ("asOfTs", DESCENDING)],
            unique=True, name=f"uq_tenant_{entity_id_field}_ts")
        # TTL — prune stale feature versions after 7 days
        coll.create_index([("asOfTs", ASCENDING)],
                          expireAfterSeconds=7 * 24 * 3600,
                          name="ttl_as_of_ts")
        log.info("[ok] indexes  ml_feature.%s (TTL 7d)", col_name)


# ---------------------------------------------------------------------------
# ── MODEL TRAINING ───────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def setup_ml_training(db) -> None:
    """ml_training: training_cost_forecast · training_delay_prediction
                       training_recommendation · training_risk_prediction
    Stores dataset manifests (metadata only; actual data is Parquet on Blob).
    """
    training_collections = [
        "training_cost_forecast",
        "training_delay_prediction",
        "training_recommendation",
        "training_risk_prediction",
    ]

    for col_name in training_collections:
        _ensure_collection(db, col_name, {"$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "datasetId", "labelCutoffTs",
                         "featureVersion", "createdAt"],
            "properties": {
                "tenantId":       _STR,
                "datasetId":      _STR,
                "labelCutoffTs":  _DATE,
                "featureVersion": {"bsonType": "string"},
                "blobPath":       _STR_NULL,
                "rowCount":       _INT,
                "schemaHash":     _STR_NULL,
                "trainSplitPct":  _NUM,
                "valSplitPct":    _NUM,
                "testSplitPct":   _NUM,
                "status": {
                    "bsonType": "string",
                    "pattern": "^(pending|ready|failed)$",
                },
                "createdAt":      _DATE,
                "isDeleted":      _BOOL,
            },
        }})
        coll = db[col_name]
        coll.create_index(
            [("tenantId", ASCENDING), ("datasetId", ASCENDING)],
            unique=True, name="uq_tenant_dataset")
        coll.create_index(
            [("tenantId", ASCENDING), ("labelCutoffTs", DESCENDING)],
            name="ix_tenant_cutoff")
        coll.create_index([("status", ASCENDING)], name="ix_status")
        log.info("[ok] indexes  ml_training.%s", col_name)


# ---------------------------------------------------------------------------
# ── MODEL REGISTRY ───────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def setup_ml_model_registry(db) -> None:
    """ml_model_registry: cost_forecast_model · recommendation_model
                          delay_prediction_model · risk_prediction_model
    Mirrors the Azure ML Model Registry with a MongoDB read model for
    fast serving-layer lookups (champion model version, feature schema hash).
    """
    model_collections = [
        "cost_forecast_model",
        "recommendation_model",
        "delay_prediction_model",
        "risk_prediction_model",
    ]

    for col_name in model_collections:
        _ensure_collection(db, col_name, {"$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "modelId", "modelVersion",
                         "status", "registeredAt"],
            "properties": {
                "tenantId":        _STR,
                "modelId":         _STR,
                "modelVersion":    {"bsonType": "string"},
                "framework":       _STR_NULL,
                "datasetId":       _STR_NULL,
                "featureSchemaHash": _STR_NULL,
                "primaryMetric":   _STR_NULL,
                "primaryMetricValue": _NUM,
                "status": {
                    "bsonType": "string",
                    "pattern": "^(staging|champion|challenger|archived)$",
                },
                "trafficSplitPct": _NUM,
                "registeredAt":    _DATE,
                "promotedAt":      _DATE,
                "isDeleted":       _BOOL,
            },
        }})
        coll = db[col_name]
        coll.create_index(
            [("tenantId", ASCENDING), ("modelId", ASCENDING),
             ("modelVersion", ASCENDING)],
            unique=True, name="uq_tenant_model_version")
        coll.create_index(
            [("tenantId", ASCENDING), ("status", ASCENDING)],
            name="ix_tenant_status")
        coll.create_index(
            [("tenantId", ASCENDING), ("registeredAt", DESCENDING)],
            name="ix_tenant_registered")
        log.info("[ok] indexes  ml_model_registry.%s", col_name)


# ---------------------------------------------------------------------------
# ── MODEL SERVING ────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def setup_prediction_serving(db) -> None:
    """prediction_serving_db: cost_forecast · delay_prediction
                               project_risk_prediction
    """


def setup_prediction_store(db) -> None:
    """Alias for the prediction_serving_db setup."""
    setup_prediction_serving(db)
    # cost_forecast
    _ensure_collection(db, "cost_forecast", {"$jsonSchema": {
        "bsonType": "object",
        "required": ["tenantId", "projectId", "modelVersion",
                     "forecastedCostUsd", "generatedAt"],
        "properties": {
            "tenantId":           _STR,
            "projectId":          _STR,
            "modelVersion":       {"bsonType": "string"},
            "forecastedCostUsd":  _NUM,
            "confidenceLow":      _NUM,
            "confidenceHigh":     _NUM,
            "generatedAt":        _DATE,
            "expiresAt":          _DATE,
            "isDeleted":          _BOOL,
        },
    }})
    coll = db["cost_forecast"]
    coll.create_index([("tenantId", ASCENDING), ("projectId", ASCENDING)],
                      unique=True, name="uq_tenant_project")
    coll.create_index([("expiresAt", ASCENDING)],
                      expireAfterSeconds=0, name="ttl_expires_at")
    log.info("[ok] indexes  prediction_serving.cost_forecast (TTL)")

    # delay_prediction
    _ensure_collection(db, "delay_prediction", {"$jsonSchema": {
        "bsonType": "object",
        "required": ["tenantId", "entityId", "entityType",
                     "delayProbability", "modelVersion", "generatedAt"],
        "properties": {
            "tenantId":         _STR,
            "entityId":         _STR,
            "entityType": {
                "bsonType": "string",
                "pattern": "^(project|task)$",
            },
            "delayProbability": _NUM,
            "modelVersion":     {"bsonType": "string"},
            "generatedAt":      _DATE,
            "expiresAt":        _DATE,
            "isDeleted":        _BOOL,
        },
    }})
    coll = db["delay_prediction"]
    coll.create_index(
        [("tenantId", ASCENDING), ("entityId", ASCENDING),
         ("entityType", ASCENDING)],
        unique=True, name="uq_tenant_entity")
    coll.create_index([("expiresAt", ASCENDING)],
                      expireAfterSeconds=0, name="ttl_expires_at")
    log.info("[ok] indexes  prediction_serving.delay_prediction (TTL)")

    # project_risk_prediction
    _ensure_collection(db, "project_risk_prediction", {"$jsonSchema": {
        "bsonType": "object",
        "required": ["tenantId", "projectId", "riskTier",
                     "modelVersion", "generatedAt"],
        "properties": {
            "tenantId":     _STR,
            "projectId":    _STR,
            "riskTier": {
                "bsonType": "string",
                "pattern": "^(low|medium|high|critical)$",
            },
            "riskScore":    _NUM,
            "topRiskFactors": {"bsonType": "array"},
            "modelVersion": {"bsonType": "string"},
            "generatedAt":  _DATE,
            "expiresAt":    _DATE,
            "isDeleted":    _BOOL,
        },
    }})
    coll = db["project_risk_prediction"]
    coll.create_index([("tenantId", ASCENDING), ("projectId", ASCENDING)],
                      unique=True, name="uq_tenant_project")
    coll.create_index([("tenantId", ASCENDING), ("riskTier", ASCENDING)],
                      name="ix_tenant_risk_tier")
    coll.create_index([("expiresAt", ASCENDING)],
                      expireAfterSeconds=0, name="ttl_expires_at")
    log.info("[ok] indexes  prediction_serving.project_risk_prediction (TTL)")


def setup_recommendation_serving(db) -> None:
    """recommendation_serving: project_recommendation · task_recommendation
                                   resource_recommendation
    """
    # project_recommendation
    _ensure_collection(db, "project_recommendation", {"$jsonSchema": {
        "bsonType": "object",
        "required": ["tenantId", "projectId", "recommendations",
                     "modelVersion", "generatedAt"],
        "properties": {
            "tenantId":       _STR,
            "projectId":      _STR,
            "recommendations": {"bsonType": "array"},
            "modelVersion":   {"bsonType": "string"},
            "generatedAt":    _DATE,
            "expiresAt":      _DATE,
            "isDeleted":      _BOOL,
        },
    }})
    coll = db["project_recommendation"]
    coll.create_index([("tenantId", ASCENDING), ("projectId", ASCENDING)],
                      unique=True, name="uq_tenant_project")
    coll.create_index([("expiresAt", ASCENDING)],
                      expireAfterSeconds=0, name="ttl_expires_at")
    log.info("[ok] indexes  recommendation_serving.project_recommendation (TTL)")

    # task_recommendation — streaming, TTL 5 min
    _ensure_collection(db, "task_recommendation", {"$jsonSchema": {
        "bsonType": "object",
        "required": ["tenantId", "workerId", "recommendations",
                     "modelVersion", "generatedAt"],
        "properties": {
            "tenantId":        _STR,
            "workerId":        _STR,
            "recommendations": {"bsonType": "array"},
            "modelVersion":    {"bsonType": "string"},
            "generatedAt":     _DATE,
            "expiresAt":       _DATE,
            "isDeleted":       _BOOL,
        },
    }})
    coll = db["task_recommendation"]
    coll.create_index([("tenantId", ASCENDING), ("workerId", ASCENDING)],
                      unique=True, name="uq_tenant_worker")
    coll.create_index([("expiresAt", ASCENDING)],
                      expireAfterSeconds=0, name="ttl_expires_at")
    log.info("[ok] indexes  recommendation_serving.task_recommendation (TTL)")

    # resource_recommendation — streaming, TTL 5 min
    _ensure_collection(db, "resource_recommendation", {"$jsonSchema": {
        "bsonType": "object",
        "required": ["tenantId", "taskId", "recommendations",
                     "modelVersion", "generatedAt"],
        "properties": {
            "tenantId":        _STR,
            "taskId":          _STR,
            "recommendations": {"bsonType": "array"},
            "modelVersion":    {"bsonType": "string"},
            "generatedAt":     _DATE,
            "expiresAt":       _DATE,
            "isDeleted":       _BOOL,
        },
    }})
    coll = db["resource_recommendation"]
    coll.create_index([("tenantId", ASCENDING), ("taskId", ASCENDING)],
                      unique=True, name="uq_tenant_task")
    coll.create_index([("expiresAt", ASCENDING)],
                      expireAfterSeconds=0, name="ttl_expires_at")
    log.info("[ok] indexes  recommendation_serving.resource_recommendation (TTL)")


def setup_ai_assistant_serving(db) -> None:
    """ai_assistant_serving: intent_prediction · ai_generated_summary
                                 conversation_context · ai_response_cache
    """
    # intent_prediction — real-time, written per query, no TTL (audit trail)
    _ensure_collection(db, "intent_prediction", {"$jsonSchema": {
        "bsonType": "object",
        "required": ["tenantId", "sessionId", "queryId",
                     "intent", "confidence", "classifiedAt"],
        "properties": {
            "tenantId":    _STR,
            "sessionId":   _STR,
            "queryId":     _STR,
            "intent":      _STR,
            "confidence":  _NUM,
            "rawQuery":    _STR_NULL,
            "modelVersion": {"bsonType": "string"},
            "classifiedAt": _DATE,
            "isDeleted":   _BOOL,
        },
    }})
    coll = db["intent_prediction"]
    coll.create_index(
        [("tenantId", ASCENDING), ("queryId", ASCENDING)],
        unique=True, name="uq_tenant_query")
    coll.create_index(
        [("tenantId", ASCENDING), ("sessionId", ASCENDING),
         ("classifiedAt", DESCENDING)],
        name="ix_tenant_session")
    log.info("[ok] indexes  ai_assistant_serving.intent_prediction")

    # ai_generated_summary — batch nightly, TTL 12 h
    _ensure_collection(db, "ai_generated_summary", {"$jsonSchema": {
        "bsonType": "object",
        "required": ["tenantId", "entityId", "entityType",
                     "summary", "generatedAt"],
        "properties": {
            "tenantId":    _STR,
            "entityId":    _STR,
            "entityType": {
                "bsonType": "string",
                "pattern": "^(project|task)$",
            },
            "summary":     _STR_NULL,
            "modelVersion": {"bsonType": "string"},
            "generatedAt": _DATE,
            "expiresAt":   _DATE,
            "isDeleted":   _BOOL,
        },
    }})
    coll = db["ai_generated_summary"]
    coll.create_index(
        [("tenantId", ASCENDING), ("entityId", ASCENDING),
         ("entityType", ASCENDING)],
        unique=True, name="uq_tenant_entity")
    coll.create_index([("expiresAt", ASCENDING)],
                      expireAfterSeconds=0, name="ttl_expires_at")
    log.info("[ok] indexes  ai_assistant_serving.ai_generated_summary (TTL 12h)")

    # conversation_context — session TTL 30 min
    _ensure_collection(db, "conversation_context", {"$jsonSchema": {
        "bsonType": "object",
        "required": ["tenantId", "sessionId", "userId",
                     "turns", "createdAt"],
        "properties": {
            "tenantId":  _STR,
            "sessionId": _STR,
            "userId":    _STR,
            "turns":     {"bsonType": "array"},
            "createdAt": _DATE,
            "expiresAt": _DATE,
            "isDeleted": _BOOL,
        },
    }})
    coll = db["conversation_context"]
    coll.create_index(
        [("tenantId", ASCENDING), ("sessionId", ASCENDING)],
        unique=True, name="uq_tenant_session")
    coll.create_index([("expiresAt", ASCENDING)],
                      expireAfterSeconds=0, name="ttl_expires_at")
    log.info("[ok] indexes  ai_assistant_serving.conversation_context (TTL 30min)")

    # ai_response_cache — TTL 1 h
    _ensure_collection(db, "ai_response_cache", {"$jsonSchema": {
        "bsonType": "object",
        "required": ["tenantId", "queryHash", "response",
                     "cachedAt"],
        "properties": {
            "tenantId":    _STR,
            "queryHash":   _STR,
            "response":    _STR_NULL,
            "modelVersion": {"bsonType": "string"},
            "cachedAt":    _DATE,
            "expiresAt":   _DATE,
            "isDeleted":   _BOOL,
        },
    }})
    coll = db["ai_response_cache"]
    coll.create_index(
        [("tenantId", ASCENDING), ("queryHash", ASCENDING)],
        unique=True, name="uq_tenant_query_hash")
    coll.create_index([("expiresAt", ASCENDING)],
                      expireAfterSeconds=0, name="ttl_expires_at")
    log.info("[ok] indexes  ai_assistant_serving.ai_response_cache (TTL 1h)")


# ---------------------------------------------------------------------------
# ── SEARCH / VECTOR ──────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def setup_search_index(db) -> None:
    """search_index: project_search · task_search"""

    _ensure_collection(db, "project_search", {"$jsonSchema": {
        "bsonType": "object",
        "required": ["tenantId", "projectId"],
        "properties": {
            "tenantId":    _STR,
            "projectId":   _STR,
            "name":        _STR_NULL,
            "description": _STR_NULL,
            "trade":       _STR_NULL,
            "status":      _STR_NULL,
            "updatedAt":   _DATE,
            "isDeleted":   _BOOL,
        },
    }})
    coll = db["project_search"]
    coll.create_index([("tenantId", ASCENDING), ("projectId", ASCENDING)],
                      unique=True, name="uq_tenant_project")
    coll.create_index(
        [("tenantId", ASCENDING),
         ("name", TEXT), ("description", TEXT),
         ("trade", TEXT)],
        name="ft_project_text",
    )
    log.info("[ok] indexes  search_index.project_search (text index)")

    _ensure_collection(db, "task_search", {"$jsonSchema": {
        "bsonType": "object",
        "required": ["tenantId", "taskId"],
        "properties": {
            "tenantId":    _STR,
            "taskId":      _STR,
            "title":       _STR_NULL,
            "description": _STR_NULL,
            "status":      _STR_NULL,
            "workType":    _STR_NULL,
            "updatedAt":   _DATE,
            "isDeleted":   _BOOL,
        },
    }})
    coll = db["task_search"]
    coll.create_index([("tenantId", ASCENDING), ("taskId", ASCENDING)],
                      unique=True, name="uq_tenant_task")
    coll.create_index(
        [("tenantId", ASCENDING),
         ("title", TEXT), ("description", TEXT),
         ("workType", TEXT)],
        name="ft_task_text",
    )
    log.info("[ok] indexes  search_index.task_search (text index)")


# ---------------------------------------------------------------------------
# ── Entry point ──────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

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

    ml_feature_db_name = (
        os.environ.get("ML_FEATURE_DB_NAME")
        or os.environ.get("FEATURE_STORE_DB_NAME")
        or "ml_feature_db"
    )
    prediction_serving_db_name = (
        os.environ.get("PREDICTION_SERVING_DB_NAME")
        or os.environ.get("PREDICTION_STORE_DB_NAME")
        or "prediction_serving_db"
    )

    client = MongoClient(uri, appname="platform-setup", serverSelectionTimeoutMS=15_000)

    try:
        plan = [
            # (db_name, setup_fn, kwargs)
            # gold_project/gold_task/gold_inventory/gold_subscription/gold_rule
            # are planned canonical Gold databases (see module docstring) but
            # have no setup function yet -- intentionally omitted rather than
            # referencing undefined names. Add them here once implemented.
            ("gold_event",             setup_gold_event,             {}),
            (ml_feature_db_name,        setup_feature_store,          {}),
            ("ml_training",            setup_ml_training,            {}),
            ("ml_model_registry",      setup_ml_model_registry,      {}),
            (prediction_serving_db_name, setup_prediction_store,       {}),
            ("recommendation_serving", setup_recommendation_serving, {}),
            ("ai_assistant_serving",   setup_ai_assistant_serving,   {}),
            # Mongo "vector" DB retired — embeddings now live in the dedicated
            # vector DB; Mongo keeps only embeddingModel/Version/vectorSyncedAt
            # linkage on the feature documents.
            ("search_index",           setup_search_index,           {}),
        ]

        failed: list[str] = []
        for db_name, fn, kwargs in plan:
            log.info("══ setting up %s", db_name)
            try:
                fn(client[db_name], **kwargs)
            except Exception as exc:
                log.exception("FAILED %s: %s", db_name, exc)
                failed.append(db_name)

        if failed:
            log.error("setup failed for: %s", failed)
            return 1

        log.info("══ platform setup complete — %d databases ready", len(plan))
        return 0

    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
