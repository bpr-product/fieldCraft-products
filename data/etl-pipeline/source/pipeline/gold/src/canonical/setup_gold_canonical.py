"""Setup the Canonical Gold database on Cosmos DB for MongoDB vCore.

Canonical Gold (as distinct from Feature Gold, e.g. gold_bpe / gold_eden) holds
cross-feature, platform-owned reference data — a single definition consumed by
every feature, not owned by one.

Database: gold_canonical (renamed from gold_user_permissions once it grew
beyond just RBAC — now holds tenant/customer/project/asset reference data too)
  user_permissions  — one document per (tenant, user): resolved effective
                       roles, flattened permissions, and active project
                       membership. Source: docs/canonical/user_access_management_model.md
                       (§8 serving materialization). Built for Eden first;
                       any future feature needing RBAC reads this collection
                       instead of re-deriving the same Silver joins.
  tenant            — one document per tenantId: tenant business record
                       (silver.tenant — provider-authoritative).
  customer          — one document per (tenantId, customerUid): customer
                       identity (silver.customer_asset deduped by customer_uid,
                       enriched from silver.fieldcraft_customer_customer when populated).
  project           — one document per (tenantId, projectId): lean project
                       directory (silver.project) — NOT the full task/financial
                       detail that stays feature-scoped in gold_eden.project_health.
  asset             — one document per (tenantId, assetUid): physical asset/
                       property record (silver.customer_asset deduped by asset_uid).
  question          — one document per Q&A template version: global,
                       provider-owned reference data (silver.fieldcraft_provider_question_template
                       — NOT the bare, empty silver.question_template; see
                       load_question.py header for the routing-gap history).
                       tenantId is stamped as the constant "fieldcraft_provider" (matches
                       the source row's own _s_tenantid) since this is not
                       per-tenant data. Cross-feature: any feature needing the
                       parsed ODM question set (Eden bid_context, Q&A
                       recommendation, future consumers) reads this instead of
                       re-parsing the SDM JSON blob independently.
  answer            — one document per (tenantId, answerId): lean passthrough
                       of silver.answers, precomputed with a templateId FK into
                       this same database's question collection (resolved via
                       trade_oid + question_template_version) so consumers never
                       have to redo that join themselves. Distinct from
                       gold_qa_recommendation.qa_answer, which additionally
                       embeds BOM lines — a qa_recommendation-feature-specific
                       enrichment that does not belong in cross-feature
                       canonical data.
  lead              — one document per (tenantId, leadUid): pre-conversion CRM
                       lead record (source: lead table in the shared,
                       cross-tenant Customer DB — see
                       docs/crm/eden_crm_gold_requirements.md, G2). Explicitly
                       NOT synced: the raw `data` JSON payload (no defined
                       schema) and automation-engine bookkeeping columns
                       (ase_job_id/retry_count/last_error/locked_by/locked_at/
                       next_attempt_at) — internal, never user-facing.
                       customerId links to this same database's customer
                       collection once a lead converts. serviceName is
                       resolved from the raw service_id FK via a LEFT JOIN
                       directly on silver.fieldcraft_provider_external_service_
                       service_master inside load_lead.py's own Silver query
                       (docs/crm/eden_crm_gold_requirements.md, G3a) — no
                       separate Gold service_lookup collection exists; the raw
                       serviceId is deliberately NOT surfaced as its own
                       field, serviceName alone is sufficient (confirmed
                       2026-09-25). Real-time via load_lead.py +
                       gold_user_permissions_stream.py's gold-canonical-stream
                       Flink job (direct-key trigger on silver.fieldcraft_customer_lead's own
                       lead_uid), plus batch --rebuild.
  communication_log — one document per (tenantId, communicationLogId):
                       CRM communication metadata (source: communication_log
                       table, immutable/append-only, monthly-partitioned,
                       lives in EACH TENANT'S OWN database — NOT the shared
                       Customer DB lead/customer live in; see the same
                       requirements doc, G5). communicationLogId is a UUID
                       (silver.communication_log.id — confirmed 2026-09-25),
                       not an auto-increment int. projectId is a plain FK to
                       silver.project.id (confirmed 2026-09-25), nullable — a
                       communication can be logged before a project exists.
                       Deliberately excludes all PII columns (body_snapshot,
                       body_tokens, to_address, from_address) — confirmed
                       NULL on every row written today (no application code
                       path populates them) and gated on DPO sign-off
                       regardless. Real-time via load_communication_log.py +
                       gold_user_permissions_stream.py, same pattern as lead
                       above.

All collections are idempotent — safe to re-run.

Env:
  MONGO_URI / KEY_VAULT_URL   resolved by shared.secret_resolver
  GOLD_CANONICAL_DB           default "gold_canonical"
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

log = logging.getLogger("canonical.setup")


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
# user_permissions — one doc per (tenantId, user_uid)
# ---------------------------------------------------------------------------

def setup_user_permissions(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "user_uid", "_grain", "_schemaVersion", "goldUpdatedAt"],
            "properties": {
                "tenantId":        {"bsonType": "string", "minLength": 1},
                "user_uid":        {"bsonType": "string", "minLength": 1},
                "user_account_id": {"bsonType": ["int", "long", "null"]},
                "sourceEnv":       {"bsonType": ["string", "null"]},
                "roles":           {"bsonType": "array", "items": {"bsonType": "string"}},
                "permissions":     {"bsonType": "array", "items": {"bsonType": "string"}},
                "project_ids":     {"bsonType": "array", "items": {"bsonType": "string"}},
                "_grain":          {"bsonType": "string"},
                "_schemaVersion":  {"bsonType": ["int", "long"], "minimum": 1},
                "goldUpdatedAt":   {"bsonType": ["date", "string"]},
            },
        }
    }
    _ensure_collection(db, "user_permissions", validator)
    coll = db["user_permissions"]
    coll.create_index([("tenantId", ASCENDING), ("sourceEnv", ASCENDING), ("user_uid", ASCENDING)],
                      unique=True, name="uq_tenant_user_uid")
    coll.create_index([("tenantId", ASCENDING), ("sourceEnv", ASCENDING), ("user_account_id", ASCENDING)],
                      unique=True, name="uq_tenant_user_account_id")
    coll.create_index([("tenantId", ASCENDING), ("permissions", ASCENDING)],
                      name="ix_tenant_permissions")
    coll.create_index([("tenantId", ASCENDING), ("roles", ASCENDING)],
                      name="ix_tenant_roles")
    coll.create_index([("tenantId", ASCENDING), ("project_ids", ASCENDING)],
                      name="ix_tenant_project_ids")
    log.info("[ok] indexes ensured on %s.user_permissions", db.name)


# ---------------------------------------------------------------------------
# tenant — one doc per tenantId
# ---------------------------------------------------------------------------

def setup_tenant(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "_grain", "_schemaVersion", "goldUpdatedAt"],
            "properties": {
                "tenantId":       {"bsonType": "string", "minLength": 1},
                "sourceEnv":      {"bsonType": ["string", "null"]},
                "name":           {"bsonType": ["string", "null"]},
                "status":         {"bsonType": ["string", "null"]},
                "email":          {"bsonType": ["string", "null"]},
                "_grain":         {"bsonType": "string"},
                "_schemaVersion": {"bsonType": ["int", "long"], "minimum": 1},
                "goldUpdatedAt":  {"bsonType": ["date", "string"]},
            },
        }
    }
    _ensure_collection(db, "tenant", validator)
    coll = db["tenant"]
    coll.create_index([("tenantId", ASCENDING), ("sourceEnv", ASCENDING)],
                      unique=True, name="uq_tenant")
    coll.create_index([("status", ASCENDING)], name="ix_status")
    log.info("[ok] indexes ensured on %s.tenant", db.name)


# ---------------------------------------------------------------------------
# customer — one doc per (tenantId, customerUid)
# ---------------------------------------------------------------------------

def setup_customer(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "customerUid", "_grain", "_schemaVersion", "goldUpdatedAt"],
            "properties": {
                "tenantId":     {"bsonType": "string", "minLength": 1},
                "customerUid":  {"bsonType": "string", "minLength": 1},
                "sourceEnv":    {"bsonType": ["string", "null"]},
                "name":         {"bsonType": ["string", "null"]},
                "email":        {"bsonType": ["string", "null"]},
                "projectIds":   {"bsonType": "array", "items": {"bsonType": "string"}},
                "assetUids":    {"bsonType": "array", "items": {"bsonType": "string"}},
                "_grain":         {"bsonType": "string"},
                "_schemaVersion": {"bsonType": ["int", "long"], "minimum": 1},
                "goldUpdatedAt":  {"bsonType": ["date", "string"]},
            },
        }
    }
    _ensure_collection(db, "customer", validator)
    coll = db["customer"]
    coll.create_index([("tenantId", ASCENDING), ("sourceEnv", ASCENDING), ("customerUid", ASCENDING)],
                      unique=True, name="uq_tenant_customer")
    coll.create_index([("tenantId", ASCENDING), ("projectIds", ASCENDING)],
                      name="ix_tenant_project_ids")
    coll.create_index([("tenantId", ASCENDING), ("assetUids", ASCENDING)],
                      name="ix_tenant_asset_uids")
    log.info("[ok] indexes ensured on %s.customer", db.name)


# ---------------------------------------------------------------------------
# project — one doc per (tenantId, projectId)
# ---------------------------------------------------------------------------

def setup_project(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "projectId", "_grain", "_schemaVersion", "goldUpdatedAt"],
            "properties": {
                "tenantId":     {"bsonType": "string", "minLength": 1},
                "projectId":    {"bsonType": "string", "minLength": 1},
                "sourceEnv":    {"bsonType": ["string", "null"]},
                "customerUid":  {"bsonType": ["string", "null"]},
                "assetUid":     {"bsonType": ["string", "null"]},
                "status":       {"bsonType": ["string", "null"]},
                "_grain":         {"bsonType": "string"},
                "_schemaVersion": {"bsonType": ["int", "long"], "minimum": 1},
                "goldUpdatedAt":  {"bsonType": ["date", "string"]},
            },
        }
    }
    _ensure_collection(db, "project", validator)
    coll = db["project"]
    coll.create_index([("tenantId", ASCENDING), ("sourceEnv", ASCENDING), ("projectId", ASCENDING)],
                      unique=True, name="uq_tenant_project")
    coll.create_index([("tenantId", ASCENDING), ("customerUid", ASCENDING)],
                      name="ix_tenant_customer")
    coll.create_index([("tenantId", ASCENDING), ("assetUid", ASCENDING)],
                      name="ix_tenant_asset")
    log.info("[ok] indexes ensured on %s.project", db.name)


# ---------------------------------------------------------------------------
# asset — one doc per (tenantId, assetUid)
# ---------------------------------------------------------------------------

def setup_asset(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "assetUid", "_grain", "_schemaVersion", "goldUpdatedAt"],
            "properties": {
                "tenantId":    {"bsonType": "string", "minLength": 1},
                "assetUid":    {"bsonType": "string", "minLength": 1},
                "sourceEnv":   {"bsonType": ["string", "null"]},
                "customerUid": {"bsonType": ["string", "null"]},
                "projectIds":  {"bsonType": "array", "items": {"bsonType": "string"}},
                "_grain":         {"bsonType": "string"},
                "_schemaVersion": {"bsonType": ["int", "long"], "minimum": 1},
                "goldUpdatedAt":  {"bsonType": ["date", "string"]},
            },
        }
    }
    _ensure_collection(db, "asset", validator)
    coll = db["asset"]
    coll.create_index([("tenantId", ASCENDING), ("sourceEnv", ASCENDING), ("assetUid", ASCENDING)],
                      unique=True, name="uq_tenant_asset")
    coll.create_index([("tenantId", ASCENDING), ("customerUid", ASCENDING)],
                      name="ix_tenant_customer")
    coll.create_index([("tenantId", ASCENDING), ("projectIds", ASCENDING)],
                      name="ix_tenant_project_ids")
    log.info("[ok] indexes ensured on %s.asset", db.name)


# ---------------------------------------------------------------------------
# question — one doc per fieldcraft_provider_question_template row (templateId)
# ---------------------------------------------------------------------------

def setup_question(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "templateId", "_grain", "_schemaVersion", "goldUpdatedAt"],
            "properties": {
                "tenantId":    {"bsonType": "string", "minLength": 1},
                "templateId":  {"bsonType": ["int", "long"]},
                "sourceEnv":   {"bsonType": ["string", "null"]},
                "tradeOid":    {"bsonType": ["string", "null"]},
                "tradeId":     {"bsonType": ["int", "long", "null"]},
                "version":     {"bsonType": ["int", "long", "null"]},
                "isDeleted":   {"bsonType": "bool"},
                "items":       {"bsonType": "array"},
                "tenantIds":   {"bsonType": "array", "items": {"bsonType": "string"}},
                "_grain":         {"bsonType": "string"},
                "_schemaVersion": {"bsonType": ["int", "long"], "minimum": 1},
                "goldUpdatedAt":  {"bsonType": ["date", "string"]},
            },
        }
    }
    _ensure_collection(db, "question", validator)
    coll = db["question"]
    # tenantId is the constant "fieldcraft_provider" for every doc in this collection
    # (it is global reference data, not per-tenant) — sourceEnv + templateId is
    # the real uniqueness key. Kept as (tenantId, sourceEnv, templateId) so the
    # index shape matches every other canonical collection's upsert filter.
    # NOTE: _ensure_shard_key() hashes on tenantId like every other canonical
    # collection, which puts all ~450 question docs on one logical shard since
    # tenantId is constant here. Accepted deliberately — this table is small,
    # rarely written (batch-only reference data), and read-scaled by Cosmos
    # replicas, so a hot partition is not a practical concern at this volume.
    # If this collection ever grows into the tens of thousands of rows,
    # reconsider sharding on templateId instead.
    coll.create_index([("tenantId", ASCENDING), ("sourceEnv", ASCENDING), ("templateId", ASCENDING)],
                      unique=True, name="uq_tenant_template_id")
    coll.create_index([("tradeOid", ASCENDING), ("version", ASCENDING), ("sourceEnv", ASCENDING)],
                      name="ix_trade_oid_version")
    # "which templates can this tenant use" — reverse lookup on the tenantIds
    # array populated from silver.fieldcraft_provider_tenant_trade_map.
    coll.create_index([("tenantIds", ASCENDING)], name="ix_tenant_ids")
    log.info("[ok] indexes ensured on %s.question", db.name)


# ---------------------------------------------------------------------------
# answer — one doc per (tenantId, answerId)
# ---------------------------------------------------------------------------

def setup_answer(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "answerId", "_grain", "_schemaVersion", "goldUpdatedAt"],
            "properties": {
                "tenantId":   {"bsonType": "string", "minLength": 1},
                "answerId":   {"bsonType": ["int", "long"]},
                "projectId":  {"bsonType": ["int", "long", "null"]},
                "sourceEnv":  {"bsonType": ["string", "null"]},
                "templateId": {"bsonType": ["int", "long", "null"]},
                "isDeleted":  {"bsonType": "bool"},
                "_grain":         {"bsonType": "string"},
                "_schemaVersion": {"bsonType": ["int", "long"], "minimum": 1},
                "goldUpdatedAt":  {"bsonType": ["date", "string"]},
            },
        }
    }
    _ensure_collection(db, "answer", validator)
    coll = db["answer"]
    coll.create_index([("tenantId", ASCENDING), ("answerId", ASCENDING), ("sourceEnv", ASCENDING)],
                      unique=True, name="uq_tenant_answer_id")
    coll.create_index([("tenantId", ASCENDING), ("projectId", ASCENDING), ("sourceEnv", ASCENDING)],
                      name="ix_tenant_project_id")
    # Cross-tenant "who answered this template" lookup — the value-add of
    # precomputing templateId here instead of leaving every consumer to
    # re-join trade_oid + question_template_version themselves.
    coll.create_index([("templateId", ASCENDING)], name="ix_template_id")
    log.info("[ok] indexes ensured on %s.answer", db.name)


# ---------------------------------------------------------------------------
# lead — one doc per (tenantId, leadUid). SETUP ONLY — no loader yet; see
# docs/crm/eden_crm_gold_requirements.md G2 and the module docstring above.
# ---------------------------------------------------------------------------

def setup_lead(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "leadUid", "_grain", "_schemaVersion", "goldUpdatedAt"],
            "properties": {
                "tenantId":            {"bsonType": "string", "minLength": 1},
                "leadUid":             {"bsonType": "string", "minLength": 1},
                "sourceEnv":           {"bsonType": ["string", "null"]},
                "serviceName":         {"bsonType": ["string", "null"]},
                "externalDealId":      {"bsonType": ["string", "null"]},
                "externalContactId":   {"bsonType": ["string", "null"]},
                "firstName":           {"bsonType": ["string", "null"]},
                "lastName":            {"bsonType": ["string", "null"]},
                "email":               {"bsonType": ["string", "null"]},
                "phone":               {"bsonType": ["string", "null"]},
                "status":              {"bsonType": ["string", "null"]},
                "salesStage":          {"bsonType": ["string", "null"]},
                "salesStageEnteredAt": {"bsonType": ["date", "string", "null"]},
                "customerId":          {"bsonType": ["string", "null"]},
                "convertedAt":         {"bsonType": ["date", "string", "null"]},
                "projectUid":          {"bsonType": ["string", "null"]},
                "projectId":           {"bsonType": ["string", "null"]},
                "createdAt":           {"bsonType": ["date", "string", "null"]},
                "_grain":         {"bsonType": "string"},
                "_schemaVersion": {"bsonType": ["int", "long"], "minimum": 1},
                "goldUpdatedAt":  {"bsonType": ["date", "string"]},
            },
        }
    }
    _ensure_collection(db, "lead", validator)
    coll = db["lead"]
    coll.create_index([("tenantId", ASCENDING), ("sourceEnv", ASCENDING), ("leadUid", ASCENDING)],
                      unique=True, name="uq_tenant_lead")
    coll.create_index([("tenantId", ASCENDING), ("customerId", ASCENDING)],
                      name="ix_tenant_customer_id")
    coll.create_index([("tenantId", ASCENDING), ("status", ASCENDING)],
                      name="ix_tenant_status")
    coll.create_index([("tenantId", ASCENDING), ("salesStage", ASCENDING)],
                      name="ix_tenant_sales_stage")
    log.info("[ok] indexes ensured on %s.lead", db.name)


# ---------------------------------------------------------------------------
# communication_log — one doc per (tenantId, communicationLogId). SETUP
# ONLY — no loader yet; see docs/crm/eden_crm_gold_requirements.md G5 and the
# module docstring above.
# ---------------------------------------------------------------------------

def setup_communication_log(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenantId", "communicationLogId", "_grain", "_schemaVersion", "goldUpdatedAt"],
            "properties": {
                "tenantId":            {"bsonType": "string", "minLength": 1},
                "communicationLogId":  {"bsonType": "string"},
                "sourceEnv":           {"bsonType": ["string", "null"]},
                "channel":             {"bsonType": ["string", "null"]},
                "direction":           {"bsonType": ["string", "null"]},
                "status":              {"bsonType": ["string", "null"]},
                "subject":             {"bsonType": ["string", "null"]},
                "occurredAt":          {"bsonType": ["date", "string", "null"]},
                "actorType":           {"bsonType": ["string", "null"]},
                "actorRef":            {"bsonType": ["string", "null"]},
                "customerUuid":        {"bsonType": ["string", "null"]},
                "projectId":           {"bsonType": ["string", "null"]},
                "providerMessageRef":  {"bsonType": ["string", "null"]},
                "_grain":         {"bsonType": "string"},
                "_schemaVersion": {"bsonType": ["int", "long"], "minimum": 1},
                "goldUpdatedAt":  {"bsonType": ["date", "string"]},
            },
        }
    }
    _ensure_collection(db, "communication_log", validator)
    coll = db["communication_log"]
    coll.create_index(
        [("tenantId", ASCENDING), ("sourceEnv", ASCENDING), ("communicationLogId", ASCENDING)],
        unique=True, name="uq_tenant_communication_log")
    coll.create_index([("tenantId", ASCENDING), ("customerUuid", ASCENDING)],
                      name="ix_tenant_customer_uuid")
    coll.create_index([("tenantId", ASCENDING), ("projectId", ASCENDING)],
                      name="ix_tenant_project_id")
    coll.create_index([("tenantId", ASCENDING), ("occurredAt", ASCENDING)],
                      name="ix_tenant_occurred_at")
    log.info("[ok] indexes ensured on %s.communication_log", db.name)


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

    db_name = os.environ.get("GOLD_CANONICAL_DB", "gold_canonical")
    client = MongoClient(uri, appname="canonical-setup")
    try:
        db = client[db_name]
        setup_user_permissions(db)
        setup_tenant(db)
        setup_customer(db)
        setup_project(db)
        setup_asset(db)
        setup_question(db)
        setup_answer(db)
        setup_lead(db)
        setup_communication_log(db)
    finally:
        client.close()

    log.info("[done] Canonical Gold collections ready in %s", db_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
