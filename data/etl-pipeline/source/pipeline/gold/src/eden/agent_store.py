"""Writers for the Eden agent-operational collections.

Unlike the Eden read-model loaders (project_health, invoice_summary, …) which
project Silver into Gold on the 15-min batch, the agent-operational collections
are written at AGENT RUNTIME — the Eden agent runtime calls these helpers as it
remembers, plans, executes tools, recommends, and audits. There is therefore no
Silver source and no pipeline step; this module is the typed write surface the
runtime uses.

Collections (all in gold_eden):
  agent_memory    — record_memory()
  agent_plan      — save_plan()
  tool_execution  — log_tool_execution()
  recommendation  — write_recommendation()
  agent_audit     — record_audit()   (append-only / immutable)

Each shape_* function is pure (no I/O) so it can be unit-tested without Mongo;
``AgentStore`` wraps a pymongo database and performs the idempotent upserts
(insert-only for the audit trail). Every document carries the standard Gold
lineage block (_grain, _schemaVersion, goldUpdatedAt, …) via
``gold.pipeline.lineage`` so agent state shares the platform audit conventions.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pymongo import UpdateOne
from pymongo.collection import Collection

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(_REPO_ROOT / "shared"), str(_REPO_ROOT / "gold" / "src"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gold.pipeline.lineage import lineage_fields, parse_ts, set_on_insert  # noqa: E402

FEATURE_NAME = "eden"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


def _base(grain: str, gold_ts: datetime) -> dict[str, Any]:
    """Common platform/lineage fields shared by every agent-operational doc."""
    doc = {
        "_grain":         grain,
        "_schemaVersion": 1,
    }
    doc.update(lineage_fields(FEATURE_NAME, gold_ts, iso=False))
    return doc


# ---------------------------------------------------------------------------
# Pure shaping functions — no I/O, unit-testable
# ---------------------------------------------------------------------------

def shape_memory(
    tenantid: str,
    agent_id: str,
    memory_type: str,
    scope: str,
    content: Any,
    *,
    memory_id: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    project_id: str | None = None,
    summary: str | None = None,
    importance: float | None = None,
    embedding_model: str | None = None,
    embedding_version: str | None = None,
    expires_at: datetime | None = None,
    gold_ts: datetime | None = None,
) -> dict[str, Any]:
    gold_ts = gold_ts or _now()
    doc = _base("eden_agent_memory", gold_ts)
    doc.update({
        "tenantId":   tenantid,
        "memoryId":   memory_id or _new_id(),
        "agentId":    agent_id,
        "sessionId":  session_id,
        "userId":     user_id,
        "projectId":  project_id,
        "memoryType": memory_type,
        "scope":      scope,
        "content":    content,
        "summary":    summary,
        "importance": importance,
        # Vector lives in the dedicated vector DB; keep only the linkage.
        "embeddingModel":   embedding_model,
        "embeddingVersion": embedding_version,
        "vectorSyncedAt":   None,
        "expiresAt":  parse_ts(expires_at),
        "createdAt":  parse_ts(gold_ts),
        "isDeleted":  False,
    })
    return doc


def shape_plan(
    tenantid: str,
    agent_id: str,
    goal: str,
    *,
    plan_id: str | None = None,
    status: str = "pending",
    steps: list[dict] | None = None,
    goal_id: str | None = None,
    session_id: str | None = None,
    project_id: str | None = None,
    current_step_id: str | None = None,
    gold_ts: datetime | None = None,
) -> dict[str, Any]:
    gold_ts = gold_ts or _now()
    doc = _base("eden_agent_plan", gold_ts)
    doc.update({
        "tenantId":      tenantid,
        "planId":        plan_id or _new_id(),
        "agentId":       agent_id,
        "sessionId":     session_id,
        "projectId":     project_id,
        "goalId":        goal_id,
        "goal":          goal,
        "status":        status,
        "steps":         steps or [],
        "currentStepId": current_step_id,
        "createdAt":     parse_ts(gold_ts),
        "isDeleted":     False,
    })
    return doc


def shape_tool_execution(
    tenantid: str,
    agent_id: str,
    tool_name: str,
    status: str,
    *,
    execution_id: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    plan_id: str | None = None,
    step_id: str | None = None,
    tool_kind: str | None = None,
    input_args: dict | None = None,
    output: Any = None,
    error_message: str | None = None,
    latency_ms: int | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    gold_ts: datetime | None = None,
) -> dict[str, Any]:
    gold_ts = gold_ts or _now()
    doc = _base("eden_tool_execution", gold_ts)
    doc.update({
        "tenantId":     tenantid,
        "executionId":  execution_id or _new_id(),
        "agentId":      agent_id,
        "sessionId":    session_id,
        "userId":       user_id,
        "planId":       plan_id,
        "stepId":       step_id,
        "toolName":     tool_name,
        "toolKind":     tool_kind,
        "inputArgs":    input_args,
        "output":       output,
        "status":       status,
        "errorMessage": error_message,
        "latencyMs":    latency_ms,
        "startedAt":    parse_ts(started_at),
        "finishedAt":   parse_ts(finished_at),
    })
    return doc


def shape_recommendation(
    tenantid: str,
    agent_id: str,
    recommendation_type: str,
    title: str,
    *,
    recommendation_id: str | None = None,
    status: str = "proposed",
    detail: str | None = None,
    rationale: str | None = None,
    supporting_facts: list[dict] | None = None,
    confidence: float | None = None,
    severity: str | None = None,
    session_id: str | None = None,
    project_id: str | None = None,
    user_feedback: dict | None = None,
    expires_at: datetime | None = None,
    gold_ts: datetime | None = None,
) -> dict[str, Any]:
    gold_ts = gold_ts or _now()
    doc = _base("eden_recommendation", gold_ts)
    doc.update({
        "tenantId":           tenantid,
        "recommendationId":   recommendation_id or _new_id(),
        "agentId":            agent_id,
        "sessionId":          session_id,
        "projectId":          project_id,
        "recommendationType": recommendation_type,
        "title":              title,
        "detail":             detail,
        "rationale":          rationale,
        "supportingFacts":    supporting_facts or [],
        "confidence":         confidence,
        "severity":           severity,
        "status":             status,
        "userFeedback":       user_feedback,
        "expiresAt":          parse_ts(expires_at),
        "createdAt":          parse_ts(gold_ts),
        "isDeleted":          False,
    })
    return doc


def shape_audit(
    tenantid: str,
    agent_id: str,
    action: str,
    *,
    audit_id: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    target_collection: str | None = None,
    target_doc_key: dict | None = None,
    outcome: str | None = None,
    detail: dict | None = None,
    ts: datetime | None = None,
    gold_ts: datetime | None = None,
) -> dict[str, Any]:
    gold_ts = gold_ts or _now()
    event_ts = ts or gold_ts
    doc = _base("eden_agent_audit", gold_ts)
    doc.update({
        "tenantId":         tenantid,
        "auditId":          audit_id or _new_id(),
        "agentId":          agent_id,
        "userId":           user_id,
        "sessionId":        session_id,
        "action":           action,
        "targetCollection": target_collection,
        "targetDocKey":     target_doc_key,
        "outcome":          outcome,
        "detail":           detail,
        "ts":               parse_ts(event_ts),
    })
    return doc


# ---------------------------------------------------------------------------
# AgentStore — Mongo-backed write surface
# ---------------------------------------------------------------------------

class AgentStore:
    """Idempotent writer for the gold_eden agent-operational collections.

    Pass a pymongo database handle (``client[gold_eden_db]``). All writes are
    LSN-free upserts keyed by the collection's compound key; the audit trail is
    insert-only (``record_audit`` never updates an existing event).
    """

    def __init__(self, db) -> None:
        self._db = db

    # -- memory ------------------------------------------------------------
    def record_memory(self, **kwargs) -> str:
        doc = shape_memory(**kwargs)
        self._upsert("agent_memory", {"tenantId": doc["tenantId"], "memoryId": doc["memoryId"]}, doc)
        return doc["memoryId"]

    # -- plan --------------------------------------------------------------
    def save_plan(self, **kwargs) -> str:
        doc = shape_plan(**kwargs)
        self._upsert("agent_plan", {"tenantId": doc["tenantId"], "planId": doc["planId"]}, doc)
        return doc["planId"]

    # -- tool execution ----------------------------------------------------
    def log_tool_execution(self, **kwargs) -> str:
        doc = shape_tool_execution(**kwargs)
        self._upsert("tool_execution", {"tenantId": doc["tenantId"], "executionId": doc["executionId"]}, doc)
        return doc["executionId"]

    # -- recommendation ----------------------------------------------------
    def write_recommendation(self, **kwargs) -> str:
        doc = shape_recommendation(**kwargs)
        self._upsert("recommendation", {"tenantId": doc["tenantId"], "recommendationId": doc["recommendationId"]}, doc)
        return doc["recommendationId"]

    # -- audit (append-only) ----------------------------------------------
    def record_audit(self, **kwargs) -> str:
        """Insert an immutable audit event. Never updates an existing row."""
        doc = shape_audit(**kwargs)
        gold_ts = parse_ts(doc["goldUpdatedAt"])
        coll: Collection = self._db["agent_audit"]
        coll.update_one(
            {"tenantId": doc["tenantId"], "auditId": doc["auditId"]},
            {"$setOnInsert": {**doc, **set_on_insert(gold_ts, iso=False)}},
            upsert=True,
        )
        return doc["auditId"]

    # -- internal ----------------------------------------------------------
    def _upsert(self, collection: str, key: dict, doc: dict) -> None:
        gold_ts = parse_ts(doc["goldUpdatedAt"])
        coll: Collection = self._db[collection]
        coll.update_one(
            key,
            {"$set": doc, "$setOnInsert": set_on_insert(gold_ts, iso=False)},
            upsert=True,
        )
