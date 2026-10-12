"""Setup all Eden Gold collections on Cosmos DB for MongoDB vCore.

All collections live in a single database: gold_eden

Read-model context projections (written by the Silver→Gold ETL):
  project_health      — project completion, cost, schedule risk per project
  invoice_summary     — invoice totals, aging, payment history per project
  task_intelligence   — overdue/unassigned tasks with assignee details per project
  procurement_status  — BOM vs PO gap, pending approvals per project
  bom_stock_gap       — BOM requirement vs on-hand stock gap per project+material
  estimate_price_evidence — why a project's estimate price changed (Q1 reasoning evidence),
                        one doc per (tenantId, projectId, version) of silver.project_estimate
  schedule_move_evidence — why a project's tentative dates moved (Q2 reasoning evidence),
                        one doc per (tenantId, projectId, version) of silver.project_schedule
  weather_schedule_cross — weather-blocked scheduled tasks per project+task+day
  bid_context         — SDM form state, answered/visible questions per project
  tenant_dashboard    — cross-project risk summary per tenant
  knowledge_chunk     — RAG knowledge base (vectors live in the dedicated vector DB)

Agent-operational collections (written at AGENT RUNTIME, not by the ETL):
  agent_memory        — persistent agent memory across turns/sessions
  agent_plan          — goal + multi-step plan + execution state
  tool_execution      — append-mostly log of agent tool/API invocations
  recommendation      — explainable decision-support outputs + feedback loop
  agent_audit         — append-only, immutable agent activity audit trail

Vector embeddings are NOT stored in Mongo — they live in the dedicated vector
DB; collections keep only embeddingModel/embeddingVersion/vectorSyncedAt
linkage. No Cosmos vector index is created here.

All collections are idempotent — safe to re-run.

Env:
  MONGO_URI / KEY_VAULT_URL   resolved by shared.secret_resolver
  GOLD_EDEN_DB                default "gold_eden"
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import OperationFailure

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHARED_SRC = str(_REPO_ROOT / "shared")
if _SHARED_SRC not in sys.path:
    sys.path.insert(0, _SHARED_SRC)
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from secret_resolver import get_mongo_uri  # noqa: E402
from gold.pipeline.step_registry import is_collection_enabled  # noqa: E402

log = logging.getLogger("eden.setup")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
# project_health
# ---------------------------------------------------------------------------

def setup_project_health(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "projectId", "_grain", "goldUpdatedAt"],
            "properties": {
                "tenantId":            {"bsonType": "string", "minLength": 1},
                "projectId":           {"bsonType": "string", "minLength": 1},
                "sourceEnv":           {"bsonType": ["string", "null"]},
                "orderNo":             {"bsonType": ["string", "null"]},
                "trade":               {"bsonType": ["string", "null"]},
                "location":            {"bsonType": ["string", "null"]},
                "_grain":              {"bsonType": "string"},
                "goldUpdatedAt":       {"bsonType": ["date", "string"]},
                "completionPct":       {"bsonType": ["double", "int", "null"]},
                "totalTaskCount":      {"bsonType": ["int", "long"], "minimum": 0},
                "doneTaskCount":       {"bsonType": ["int", "long"], "minimum": 0},
                "overdueTaskCount":    {"bsonType": ["int", "long"], "minimum": 0},
                "estimatedCost":       {"bsonType": ["double", "int", "null"]},
                "materialCost":        {"bsonType": ["double", "int", "null"]},
                "workerCost":          {"bsonType": ["double", "int", "null"]},
                "toolCost":            {"bsonType": ["double", "int", "null"]},
                "overheadCost":        {"bsonType": ["double", "int", "null"]},
                "margin":              {"bsonType": ["double", "int", "null"]},
                "committedSpend":      {"bsonType": ["double", "int", "null"]},
                "remainingBudget":     {"bsonType": ["double", "int", "null"]},
                "scheduleRisk":        {"bsonType": ["string", "null"]},
                "slipDays":            {"bsonType": ["int", "long", "null"]},
                "scheduleRevisedCount":{"bsonType": ["int", "long", "null"]},
                "jobStartDate":        {"bsonType": ["date", "null"]},
                "actualJobStartDate":  {"bsonType": ["date", "null"]},
                "jobEndDate":          {"bsonType": ["date", "null"]},
                "actualJobEndDate":    {"bsonType": ["date", "null"]},
                "projectName":         {"bsonType": ["string", "null"]},
                "project":             {"bsonType": ["object", "null"]},
                "purchaseOrders":      {"bsonType": "array"},
            },
        }
    }
    _ensure_collection(db, "project_health", validator)
    coll = db["project_health"]
    coll.create_index([("tenantId", ASCENDING), ("sourceEnv", ASCENDING), ("projectId", ASCENDING)],
                      unique=True, name="uq_tenant_project")
    coll.create_index([("tenantId", ASCENDING), ("scheduleRisk", ASCENDING)],
                      name="ix_tenant_risk")
    coll.create_index([("tenantId", ASCENDING), ("overdueTaskCount", DESCENDING)],
                      name="ix_tenant_overdue")
    log.info("[ok] indexes ensured on %s.project_health", db.name)


# ---------------------------------------------------------------------------
# invoice_summary
# ---------------------------------------------------------------------------

def setup_invoice_summary(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "projectId", "_grain", "goldUpdatedAt"],
            "properties": {
                "tenantId":         {"bsonType": "string", "minLength": 1},
                "projectId":        {"bsonType": "string", "minLength": 1},
                "sourceEnv":        {"bsonType": ["string", "null"]},
                "_grain":           {"bsonType": "string"},
                "goldUpdatedAt":    {"bsonType": ["date", "string"]},
                "totalBilled":      {"bsonType": ["double", "int", "null"]},
                "totalReceived":    {"bsonType": ["double", "int", "null"]},
                "totalOutstanding": {"bsonType": ["double", "int", "null"]},
                "overdueInvoices":  {"bsonType": "array"},
                "upcomingInvoices": {"bsonType": "array"},
                "lastPayment":      {"bsonType": ["object", "null"]},
            },
        }
    }
    _ensure_collection(db, "invoice_summary", validator)
    coll = db["invoice_summary"]
    coll.create_index([("tenantId", ASCENDING), ("sourceEnv", ASCENDING), ("projectId", ASCENDING)],
                      unique=True, name="uq_tenant_project")
    coll.create_index([("tenantId", ASCENDING), ("totalOutstanding", DESCENDING)],
                      name="ix_tenant_outstanding")
    log.info("[ok] indexes ensured on %s.invoice_summary", db.name)


# ---------------------------------------------------------------------------
# task_intelligence
# ---------------------------------------------------------------------------

def setup_task_intelligence(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "projectId", "_grain", "goldUpdatedAt"],
            "properties": {
                "tenantId":            {"bsonType": "string", "minLength": 1},
                "projectId":           {"bsonType": "string", "minLength": 1},
                "sourceEnv":           {"bsonType": ["string", "null"]},
                "_grain":              {"bsonType": "string"},
                "goldUpdatedAt":       {"bsonType": ["date", "string"]},
                "overdueTaskCount":    {"bsonType": ["int", "long"], "minimum": 0},
                "unassignedTaskCount": {"bsonType": ["int", "long"], "minimum": 0},
                "totalTaskCount":      {"bsonType": ["int", "long"], "minimum": 0},
                "doneTaskCount":       {"bsonType": ["int", "long"], "minimum": 0},
                "overdueTasks":        {"bsonType": "array"},
                "unassignedTasks":     {"bsonType": "array"},
                "mostDelayedTask":     {"bsonType": ["object", "null"]},
            },
        }
    }
    _ensure_collection(db, "task_intelligence", validator)
    coll = db["task_intelligence"]
    coll.create_index([("tenantId", ASCENDING), ("sourceEnv", ASCENDING), ("projectId", ASCENDING)],
                      unique=True, name="uq_tenant_project")
    coll.create_index([("tenantId", ASCENDING), ("overdueTaskCount", DESCENDING)],
                      name="ix_tenant_overdue")
    log.info("[ok] indexes ensured on %s.task_intelligence", db.name)


# ---------------------------------------------------------------------------
# workforce_status
# ---------------------------------------------------------------------------

def setup_workforce_status(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "projectId", "_grain", "goldUpdatedAt"],
            "properties": {
                "tenantId":               {"bsonType": "string", "minLength": 1},
                "projectId":              {"bsonType": "string", "minLength": 1},
                "sourceEnv":              {"bsonType": ["string", "null"]},
                "_grain":                 {"bsonType": "string"},
                "goldUpdatedAt":          {"bsonType": ["date", "string"]},
                "headcount":              {"bsonType": ["int", "long", "null"], "minimum": 0},
                "w2Count":                {"bsonType": ["int", "long", "null"], "minimum": 0},
                "contractorCount":        {"bsonType": ["int", "long", "null"], "minimum": 0},
                "scheduledAssignments":   {"bsonType": ["int", "long", "null"], "minimum": 0},
                "activeAssignments":      {"bsonType": ["int", "long", "null"], "minimum": 0},
                "completedAssignments":   {"bsonType": ["int", "long", "null"], "minimum": 0},
                "hoursLogged":            {"bsonType": ["double", "int", "long", "null"], "minimum": 0},
                "assignedResources":      {"bsonType": "array"},
            },
        }
    }
    _ensure_collection(db, "workforce_status", validator)
    coll = db["workforce_status"]
    coll.create_index([("tenantId", ASCENDING), ("sourceEnv", ASCENDING), ("projectId", ASCENDING)],
                      unique=True, name="uq_tenant_project")
    coll.create_index([("tenantId", ASCENDING), ("headcount", DESCENDING)],
                      name="ix_tenant_headcount")
    log.info("[ok] indexes ensured on %s.workforce_status", db.name)


# ---------------------------------------------------------------------------
# procurement_status
# ---------------------------------------------------------------------------

def setup_procurement_status(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "projectId", "_grain", "goldUpdatedAt"],
            "properties": {
                "tenantId":          {"bsonType": "string", "minLength": 1},
                "projectId":         {"bsonType": "string", "minLength": 1},
                "sourceEnv":         {"bsonType": ["string", "null"]},
                "_grain":            {"bsonType": "string"},
                "goldUpdatedAt":     {"bsonType": ["date", "string"]},
                "bomTotalValue":     {"bsonType": ["double", "int", "null"]},
                "orderedValue":      {"bsonType": ["double", "int", "null"]},
                "unorderedValue":    {"bsonType": ["double", "int", "null"]},
                "unorderedItems":    {"bsonType": "array"},
                "pendingApprovals":  {"bsonType": "array"},
            },
        }
    }
    _ensure_collection(db, "procurement_status", validator)
    coll = db["procurement_status"]
    coll.create_index([("tenantId", ASCENDING), ("sourceEnv", ASCENDING), ("projectId", ASCENDING)],
                      unique=True, name="uq_tenant_project")
    coll.create_index([("tenantId", ASCENDING), ("unorderedValue", DESCENDING)],
                      name="ix_tenant_unordered")
    log.info("[ok] indexes ensured on %s.procurement_status", db.name)


# ---------------------------------------------------------------------------
# bom_stock_gap  (Procurement agent) — one doc per (tenantId, projectId, materialUid)
# ---------------------------------------------------------------------------

def setup_bom_stock_gap(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "projectId", "materialUid", "_grain", "goldUpdatedAt"],
            "properties": {
                "tenantId":            {"bsonType": "string", "minLength": 1},
                "projectId":           {"bsonType": "string", "minLength": 1},
                "materialUid":         {"bsonType": "string", "minLength": 1},
                "sourceEnv":           {"bsonType": ["string", "null"]},
                "_grain":              {"bsonType": "string"},
                "goldUpdatedAt":       {"bsonType": ["date", "string"]},
                "materialId":          {"bsonType": ["int", "long", "null"]},
                "materialName":        {"bsonType": ["string", "null"]},
                "unit":                {"bsonType": ["string", "null"]},
                "requiredQty":         {"bsonType": ["double", "int", "long", "null"]},
                "stockQty":            {"bsonType": ["double", "int", "long", "null"]},
                "gapQty":              {"bsonType": ["double", "int", "long", "null"]},
                "preferredVendorId":   {"bsonType": ["int", "long", "null"]},
                "preferredVendorName": {"bsonType": ["string", "null"]},
                "unitPrice":           {"bsonType": ["double", "int", "long", "null"]},
                "projectName":         {"bsonType": ["string", "null"]},
                "orderNo":             {"bsonType": ["string", "null"]},
                "bomLines":            {"bsonType": "array"},
                "material":            {"bsonType": ["object", "null"]},
                "stockItems":          {"bsonType": "array"},
                "vendorOptions":       {"bsonType": "array"},
            },
        }
    }
    _ensure_collection(db, "bom_stock_gap", validator)
    coll = db["bom_stock_gap"]
    coll.create_index([("tenantId", ASCENDING), ("sourceEnv", ASCENDING),
                        ("projectId", ASCENDING), ("materialUid", ASCENDING)],
                      unique=True, name="uq_tenant_project_material")
    coll.create_index([("tenantId", ASCENDING), ("projectId", ASCENDING), ("gapQty", ASCENDING)],
                      name="ix_tenant_project_gap")
    log.info("[ok] indexes ensured on %s.bom_stock_gap", db.name)


# ---------------------------------------------------------------------------
# estimate_price_evidence (Finance agent — Q1) — one doc per
# (tenantId, projectId, version) of silver.project_estimate
# ---------------------------------------------------------------------------

def setup_estimate_price_evidence(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "projectId", "version", "_grain", "goldUpdatedAt"],
            "properties": {
                "tenantId":            {"bsonType": "string", "minLength": 1},
                "projectId":           {"bsonType": "string", "minLength": 1},
                "version":             {"bsonType": ["int", "long", "null"]},
                "sourceEnv":           {"bsonType": ["string", "null"]},
                "_grain":              {"bsonType": "string"},
                "goldUpdatedAt":       {"bsonType": ["date", "string"]},
                "materialCost":        {"bsonType": ["double", "int", "long", "null"]},
                "margin":              {"bsonType": ["double", "int", "long", "null"]},
                "totalCost":           {"bsonType": ["double", "int", "long", "null"]},
                "bomReconciliation":   {"bsonType": ["object", "null"]},
                "bomLines":            {"bsonType": "array"},
                "materialPriceChanges": {"bsonType": "array"},
            },
        }
    }
    _ensure_collection(db, "estimate_price_evidence", validator)
    coll = db["estimate_price_evidence"]
    coll.create_index([("tenantId", ASCENDING), ("sourceEnv", ASCENDING),
                        ("projectId", ASCENDING), ("version", ASCENDING)],
                      unique=True, name="uq_tenant_project_version")
    coll.create_index([("tenantId", ASCENDING), ("projectId", ASCENDING),
                        ("version", DESCENDING)],
                      name="ix_tenant_project_version_desc")
    log.info("[ok] indexes ensured on %s.estimate_price_evidence", db.name)


# ---------------------------------------------------------------------------
# schedule_move_evidence (Schedule agent — Q2) — one doc per
# (tenantId, projectId, version) of silver.project_schedule
# ---------------------------------------------------------------------------

def setup_schedule_move_evidence(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "projectId", "version", "_grain", "goldUpdatedAt"],
            "properties": {
                "tenantId":            {"bsonType": "string", "minLength": 1},
                "projectId":           {"bsonType": "string", "minLength": 1},
                "version":             {"bsonType": ["int", "long", "null"]},
                "sourceEnv":           {"bsonType": ["string", "null"]},
                "_grain":              {"bsonType": "string"},
                "goldUpdatedAt":       {"bsonType": ["date", "string"]},
                "anyMoved":            {"bsonType": ["bool", "null"]},
                "moved":               {"bsonType": ["object", "null"]},
                "resourceCompletions": {"bsonType": "array"},
                "auditEvents":         {"bsonType": "array"},
                "ruleSolveSnapshots":  {"bsonType": "array"},
                "evidenceSummary":     {"bsonType": ["object", "null"]},
            },
        }
    }
    _ensure_collection(db, "schedule_move_evidence", validator)
    coll = db["schedule_move_evidence"]
    coll.create_index([("tenantId", ASCENDING), ("sourceEnv", ASCENDING),
                        ("projectId", ASCENDING), ("version", ASCENDING)],
                      unique=True, name="uq_tenant_project_version")
    coll.create_index([("tenantId", ASCENDING), ("projectId", ASCENDING),
                        ("version", DESCENDING)],
                      name="ix_tenant_project_version_desc")
    coll.create_index([("tenantId", ASCENDING), ("anyMoved", DESCENDING)],
                      name="ix_tenant_any_moved")
    log.info("[ok] indexes ensured on %s.schedule_move_evidence", db.name)


# ---------------------------------------------------------------------------
# weather_schedule_cross  (Schedule agent) — one doc per (tenantId, projectId, taskId, scheduledDate)
# ---------------------------------------------------------------------------

def setup_weather_schedule_cross(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "projectId", "taskId", "scheduledDate", "_grain", "goldUpdatedAt"],
            "properties": {
                "tenantId":         {"bsonType": "string", "minLength": 1},
                "projectId":        {"bsonType": "string", "minLength": 1},
                "taskId":           {"bsonType": "string", "minLength": 1},
                "taskUid":          {"bsonType": ["string", "null"]},
                "taskName":         {"bsonType": ["string", "null"]},
                "scheduledDate":    {"bsonType": ["date", "string"], "minLength": 1},
                "sourceEnv":        {"bsonType": ["string", "null"]},
                "_grain":           {"bsonType": "string"},
                "goldUpdatedAt":    {"bsonType": ["date", "string"]},
                "weatherCondition": {"bsonType": ["string", "null"]},
                "precipitationMm":  {"bsonType": ["double", "int", "long", "null"]},
                "windSpeedKmh":     {"bsonType": ["double", "int", "long", "null"]},
                "blocked":          {"bsonType": "bool"},
                "blockReason":      {"bsonType": ["string", "null"]},
            },
        }
    }
    _ensure_collection(db, "weather_schedule_cross", validator)
    coll = db["weather_schedule_cross"]
    coll.create_index(
        [("tenantId", ASCENDING), ("sourceEnv", ASCENDING), ("projectId", ASCENDING),
         ("taskId", ASCENDING), ("scheduledDate", ASCENDING)],
        unique=True, name="uq_tenant_project_task_date")
    coll.create_index([("tenantId", ASCENDING), ("projectId", ASCENDING), ("blocked", DESCENDING)],
                      name="ix_tenant_project_blocked")
    log.info("[ok] indexes ensured on %s.weather_schedule_cross", db.name)


# ---------------------------------------------------------------------------
# bid_context
# ---------------------------------------------------------------------------

def setup_bid_context(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "projectId", "_grain", "goldUpdatedAt"],
            "properties": {
                "tenantId":                {"bsonType": "string", "minLength": 1},
                "projectId":               {"bsonType": "string", "minLength": 1},
                "sourceEnv":               {"bsonType": ["string", "null"]},
                "_grain":                  {"bsonType": "string"},
                "goldUpdatedAt":           {"bsonType": ["date", "string"]},
                "questionTemplateVersion": {"bsonType": ["string", "null"]},
                "status":                  {"bsonType": ["string", "null"]},
                "totalQuestions":          {"bsonType": ["int", "long"], "minimum": 0},
                "answeredCount":           {"bsonType": ["int", "long"], "minimum": 0},
                "answeredItems":           {"bsonType": "array"},
                "visibleUnanswered":       {"bsonType": "array"},
                "nextRequiredItem":        {"bsonType": ["object", "null"]},
            },
        }
    }
    _ensure_collection(db, "bid_context", validator)
    coll = db["bid_context"]
    coll.create_index([("tenantId", ASCENDING), ("sourceEnv", ASCENDING), ("projectId", ASCENDING)],
                      unique=True, name="uq_tenant_project")
    log.info("[ok] indexes ensured on %s.bid_context", db.name)


# ---------------------------------------------------------------------------
# tenant_dashboard
# ---------------------------------------------------------------------------

def setup_tenant_dashboard(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "_grain", "goldUpdatedAt"],
            "properties": {
                "tenantId":        {"bsonType": "string", "minLength": 1},
                "sourceEnv":       {"bsonType": ["string", "null"]},
                "_grain":          {"bsonType": "string"},
                "goldUpdatedAt":   {"bsonType": ["date", "string"]},
                "projectCount":    {"bsonType": ["int", "long"], "minimum": 0},
                "atRiskCount":     {"bsonType": ["int", "long"], "minimum": 0},
                "totalOutstanding":{"bsonType": ["double", "int", "null"]},
                "activeProjects":  {"bsonType": "array"},
                "nextMilestone":   {"bsonType": ["object", "null"]},
            },
        }
    }
    _ensure_collection(db, "tenant_dashboard", validator)
    coll = db["tenant_dashboard"]
    coll.create_index([("tenantId", ASCENDING), ("sourceEnv", ASCENDING)],
                      unique=True, name="uq_tenant")
    coll.create_index([("tenantId", ASCENDING), ("atRiskCount", DESCENDING)],
                      name="ix_tenant_risk")
    log.info("[ok] indexes ensured on %s.tenant_dashboard", db.name)


# ---------------------------------------------------------------------------
# knowledge_chunk — AI Assistant RAG knowledge base (migrated from ai_assistant)
# ---------------------------------------------------------------------------

def _knowledge_chunk_validator() -> dict:
    return {
        "$jsonSchema": {
            "bsonType": "object",
            "required": [
                "tenantId", "chunkId",
                "_schemaVersion", "_grain", "goldUpdatedAt",
                "isDeleted", "createdAt", "updatedAt",
            ],
            "properties": {
                "tenantId":       {"bsonType": "string", "minLength": 1},
                "chunkId":        {"bsonType": "string", "minLength": 1},
                "_schemaVersion": {"bsonType": ["int", "long"], "minimum": 1},
                "_grain": {
                    "bsonType": "string",
                    "pattern": "^knowledge_chunk_rag$",
                },
                "goldUpdatedAt":  {"bsonType": ["date", "string"]},
                "isDeleted":      {"bsonType": "bool"},
                "createdAt":      {"bsonType": ["date", "string"]},
                "updatedAt":      {"bsonType": ["date", "string"]},
                "sourceType": {
                    "bsonType": "string",
                    "pattern": "^(sop|spec|help|faq)$",
                },
                "sourceDocId":  {"bsonType": ["string", "null"]},
                "sourceTitle":  {"bsonType": ["string", "null"]},
                "text":         {"bsonType": ["string", "null"]},
                "metadata":     {"bsonType": "object"},
                # Vector-linkage mapping — the vector itself lives in the
                # dedicated vector DB; Mongo keeps only the model/version it was
                # embedded with and a sync timestamp for drift detection.
                "embeddingModel":   {"bsonType": ["string", "null"]},
                "embeddingVersion": {"bsonType": ["string", "null"]},
                "vectorSyncedAt":   {"bsonType": ["date", "string", "null"]},
            },
        }
    }


def setup_knowledge_chunk(db) -> None:
    """knowledge_chunk — chunked KB documents for semantic retrieval.

    Embeddings are NOT stored here — they live in the dedicated vector DB.
    This collection holds the chunk text + metadata plus a minimal vector
    linkage (embeddingModel/embeddingVersion/vectorSyncedAt); no Cosmos vector
    index is created.
    """
    _ensure_collection(db, "knowledge_chunk", _knowledge_chunk_validator())

    coll = db["knowledge_chunk"]
    coll.create_index([("tenantId", ASCENDING), ("chunkId", ASCENDING)],
                      unique=True, name="uq_tenant_chunk")
    coll.create_index([("tenantId", ASCENDING), ("sourceType", ASCENDING)],
                      name="ix_tenant_source_type")
    coll.create_index([("tenantId", ASCENDING), ("updatedAt", DESCENDING)],
                      name="ix_tenant_updated")
    log.info("[ok] standard indexes ensured on %s.knowledge_chunk", db.name)


# ---------------------------------------------------------------------------
# Agent-operational collections (written at AGENT RUNTIME, not by Silver→Gold ETL)
#   agent_memory · agent_plan · tool_execution · recommendation · agent_audit
# These give Eden agent memory, planning/execution state, tool-execution
# auditability, explainable recommendations, and immutable activity audit.
# ---------------------------------------------------------------------------

def _agent_runtime_validator(grain: str, key_field: str, extra_required: list[str]) -> dict:
    required = ["tenantId", key_field, "_grain", "goldUpdatedAt"] + extra_required
    return {
        "$jsonSchema": {
            "bsonType": "object",
            "required": required,
            "properties": {
                "tenantId":      {"bsonType": "string", "minLength": 1},
                key_field:       {"bsonType": "string", "minLength": 1},
                "_grain":        {"bsonType": "string", "pattern": f"^{grain}$"},
                "goldUpdatedAt": {"bsonType": ["date", "string"]},
            },
        }
    }


def setup_agent_memory(db) -> None:
    """agent_memory — persistent agent memory across turns/sessions."""
    _ensure_collection(
        db, "agent_memory",
        _agent_runtime_validator("eden_agent_memory", "memoryId", ["agentId", "memoryType", "scope"]),
    )
    coll = db["agent_memory"]
    coll.create_index([("tenantId", ASCENDING), ("memoryId", ASCENDING)],
                      unique=True, name="uq_tenant_memory")
    coll.create_index([("tenantId", ASCENDING), ("agentId", ASCENDING), ("sessionId", ASCENDING)],
                      name="ix_tenant_agent_session")
    coll.create_index([("tenantId", ASCENDING), ("memoryType", ASCENDING)],
                      name="ix_tenant_memory_type")
    # TTL reaper for short-lived (working/session) memory carrying expiresAt.
    coll.create_index([("expiresAt", ASCENDING)], expireAfterSeconds=0,
                      name="ttl_expires_at")
    log.info("[ok] indexes ensured on %s.agent_memory", db.name)


def setup_agent_plan(db) -> None:
    """agent_plan — goal + multi-step plan + execution state."""
    _ensure_collection(
        db, "agent_plan",
        _agent_runtime_validator("eden_agent_plan", "planId", ["agentId", "goal", "status"]),
    )
    coll = db["agent_plan"]
    coll.create_index([("tenantId", ASCENDING), ("planId", ASCENDING)],
                      unique=True, name="uq_tenant_plan")
    coll.create_index([("tenantId", ASCENDING), ("agentId", ASCENDING), ("status", ASCENDING)],
                      name="ix_tenant_agent_status")
    coll.create_index([("tenantId", ASCENDING), ("projectId", ASCENDING)],
                      name="ix_tenant_project")
    log.info("[ok] indexes ensured on %s.agent_plan", db.name)


def setup_tool_execution(db) -> None:
    """tool_execution — append-mostly log of agent tool/API invocations."""
    _ensure_collection(
        db, "tool_execution",
        _agent_runtime_validator("eden_tool_execution", "executionId", ["agentId", "toolName", "status"]),
    )
    coll = db["tool_execution"]
    coll.create_index([("tenantId", ASCENDING), ("executionId", ASCENDING)],
                      unique=True, name="uq_tenant_execution")
    coll.create_index([("tenantId", ASCENDING), ("planId", ASCENDING)],
                      name="ix_tenant_plan")
    coll.create_index([("tenantId", ASCENDING), ("toolName", ASCENDING), ("startedAt", DESCENDING)],
                      name="ix_tenant_tool_started")
    log.info("[ok] indexes ensured on %s.tool_execution", db.name)


def setup_recommendation(db) -> None:
    """recommendation — explainable decision-support outputs with feedback loop."""
    _ensure_collection(
        db, "recommendation",
        _agent_runtime_validator("eden_recommendation", "recommendationId", ["agentId", "recommendationType", "status"]),
    )
    coll = db["recommendation"]
    coll.create_index([("tenantId", ASCENDING), ("recommendationId", ASCENDING)],
                      unique=True, name="uq_tenant_recommendation")
    coll.create_index([("tenantId", ASCENDING), ("projectId", ASCENDING), ("status", ASCENDING)],
                      name="ix_tenant_project_status")
    coll.create_index([("tenantId", ASCENDING), ("status", ASCENDING), ("severity", ASCENDING)],
                      name="ix_tenant_status_severity")
    log.info("[ok] indexes ensured on %s.recommendation", db.name)


def setup_agent_audit(db) -> None:
    """agent_audit — append-only, immutable agent activity audit trail."""
    _ensure_collection(
        db, "agent_audit",
        _agent_runtime_validator("eden_agent_audit", "auditId", ["agentId", "action", "ts"]),
    )
    coll = db["agent_audit"]
    coll.create_index([("tenantId", ASCENDING), ("auditId", ASCENDING)],
                      unique=True, name="uq_tenant_audit")
    coll.create_index([("tenantId", ASCENDING), ("agentId", ASCENDING), ("ts", DESCENDING)],
                      name="ix_tenant_agent_ts")
    coll.create_index([("tenantId", ASCENDING), ("action", ASCENDING), ("ts", DESCENDING)],
                      name="ix_tenant_action_ts")
    log.info("[ok] indexes ensured on %s.agent_audit", db.name)


def _ensure_shard_key(db, collection_name: str, shard_key: str = "tenantId") -> None:
    try:
        db.command({
            "shardCollection": f"{db.name}.{collection_name}",
            "key": {shard_key: "hashed"},
        })
        print(f"[ok] shard key '{shard_key}' set on {db.name}.{collection_name}")
    except Exception as exc:
        msg = str(exc)
        if "already sharded" in msg.lower() or "already exists" in msg.lower():
            print(f"[ok] shard key already set on {db.name}.{collection_name}")
        else:
            print(f"[warn] could not set shard key on {collection_name}: {msg}", file=__import__('sys').stderr)


# ---------------------------------------------------------------------------
# Main
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

    db_name = os.environ.get("GOLD_EDEN_DB", "gold_eden")
    client = MongoClient(uri, appname="eden-setup")
    try:
        db = client[db_name]
        # Each collection is gated by the `enabled_collections` allowlist
        # (Phase 1 release — see infra/config/gold_config.yml under `eden`).
        # No allowlist configured == every collection below is set up, same
        # as before Phase 1.
        _collection_setups = [
            ("project_health",        setup_project_health),
            ("invoice_summary",       setup_invoice_summary),
            ("task_intelligence",     setup_task_intelligence),
            ("workforce_status",      setup_workforce_status),
            ("procurement_status",    setup_procurement_status),
            ("bom_stock_gap",         setup_bom_stock_gap),
            ("estimate_price_evidence", setup_estimate_price_evidence),
            ("schedule_move_evidence", setup_schedule_move_evidence),
            ("weather_schedule_cross",setup_weather_schedule_cross),
            ("bid_context",           setup_bid_context),
            ("tenant_dashboard",      setup_tenant_dashboard),
            ("knowledge_chunk",       setup_knowledge_chunk),
            # Agent-operational collections (runtime-written)
            ("agent_memory",          setup_agent_memory),
            ("agent_plan",            setup_agent_plan),
            ("tool_execution",        setup_tool_execution),
            ("recommendation",        setup_recommendation),
            ("agent_audit",           setup_agent_audit),
        ]
        for collection_name, setup_fn in _collection_setups:
            if is_collection_enabled("eden", collection_name):
                setup_fn(db)
            else:
                log.info("[skip] %s disabled via enabled_collections allowlist", collection_name)
    finally:
        client.close()

    log.info("[done] all Eden Gold collections ready in %s", db_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
