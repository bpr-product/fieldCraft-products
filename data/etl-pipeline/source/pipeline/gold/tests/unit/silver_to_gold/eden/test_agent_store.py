"""Unit tests for gold.src.eden.agent_store — the Eden agent-operational
write surface (agent_memory, agent_plan, tool_execution, recommendation,
agent_audit).

Pure shaping functions are tested directly; AgentStore is tested against a
MagicMock pymongo database so no real Mongo is needed.
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.eden]

MOD = "gold.src.eden.agent_store"
GOLD_TS = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_CONTRACTS = Path(__file__).resolve().parents[5] / "contracts" / "gold" / "eden"


@pytest.fixture
def mod():
    return importlib.import_module(MOD)


# ---------------------------------------------------------------------------
# Shaping functions
# ---------------------------------------------------------------------------

class TestShapeMemory:
    def test_grain_keys_and_linkage(self, mod):
        doc = mod.shape_memory(
            "DEMO-001", "eden-planner", "episodic", "session",
            {"note": "user asked about overdue tasks"},
            session_id="S1", gold_ts=GOLD_TS,
        )
        assert doc["_grain"] == "eden_agent_memory"
        assert doc["_schemaVersion"] == 1
        assert doc["tenantId"] == "DEMO-001"
        assert doc["memoryType"] == "episodic" and doc["scope"] == "session"
        assert doc["memoryId"]  # generated
        # vector linkage only — no stored vector
        assert "contentEmbedding" not in doc and "embedding" not in doc
        assert doc["vectorSyncedAt"] is None

    def test_expires_at_serialised(self, mod):
        exp = datetime(2026, 6, 2, tzinfo=timezone.utc)
        doc = mod.shape_memory("T", "a", "working", "session", "x",
                               expires_at=exp, gold_ts=GOLD_TS)
        assert doc["expiresAt"] == exp

    def test_explicit_memory_id_preserved(self, mod):
        doc = mod.shape_memory("T", "a", "semantic", "tenant", "x",
                               memory_id="M-1", gold_ts=GOLD_TS)
        assert doc["memoryId"] == "M-1"


class TestShapePlan:
    def test_defaults_and_steps(self, mod):
        steps = [{"stepId": "s1", "action": "fetch", "status": "pending",
                  "dependsOn": [], "hitlRequired": False}]
        doc = mod.shape_plan("T", "eden-planner", "Reduce overdue tasks",
                             steps=steps, gold_ts=GOLD_TS)
        assert doc["_grain"] == "eden_agent_plan"
        assert doc["status"] == "pending"
        assert doc["steps"] == steps
        assert doc["planId"]


class TestShapeToolExecution:
    def test_fields(self, mod):
        doc = mod.shape_tool_execution(
            "T", "eden-actor", "get_project_health", "success",
            input_args={"projectId": "42"}, output={"completionPct": 50},
            latency_ms=12, gold_ts=GOLD_TS,
        )
        assert doc["_grain"] == "eden_tool_execution"
        assert doc["toolName"] == "get_project_health"
        assert doc["status"] == "success" and doc["latencyMs"] == 12
        assert doc["inputArgs"] == {"projectId": "42"}


class TestShapeRecommendation:
    def test_supporting_facts_and_status(self, mod):
        facts = [{"collection": "project_health",
                  "docKey": {"tenantId": "T", "projectId": "42"},
                  "field": "slipDays", "value": 3}]
        doc = mod.shape_recommendation(
            "T", "eden-advisor", "schedule_risk", "Project 42 is slipping",
            rationale="slipDays=3 and at_risk", supporting_facts=facts,
            confidence=0.8, severity="high", gold_ts=GOLD_TS,
        )
        assert doc["_grain"] == "eden_recommendation"
        assert doc["status"] == "proposed"
        assert doc["supportingFacts"] == facts
        assert doc["confidence"] == 0.8 and doc["severity"] == "high"


class TestShapeAudit:
    def test_event_ts_defaults_to_gold_ts(self, mod):
        doc = mod.shape_audit("T", "eden-actor", "read",
                              target_collection="project_health", gold_ts=GOLD_TS)
        assert doc["_grain"] == "eden_agent_audit"
        assert doc["action"] == "read"
        assert doc["ts"] == GOLD_TS


# ---------------------------------------------------------------------------
# AgentStore — Mongo-backed (mocked)
# ---------------------------------------------------------------------------

def _store(mod):
    db = MagicMock()
    coll = MagicMock()
    db.__getitem__.return_value = coll
    return mod.AgentStore(db), db, coll


class TestAgentStore:
    def test_record_memory_upserts(self, mod):
        store, db, coll = _store(mod)
        mid = store.record_memory(tenantid="T", agent_id="a",
                                  memory_type="episodic", scope="session", content="x")
        assert mid
        coll.update_one.assert_called_once()
        args, kwargs = coll.update_one.call_args
        assert kwargs.get("upsert") is True
        assert args[0] == {"tenantId": "T", "memoryId": mid}
        assert "$set" in args[1] and "$setOnInsert" in args[1]

    def test_save_plan_and_log_tool(self, mod):
        store, db, coll = _store(mod)
        pid = store.save_plan(tenantid="T", agent_id="a", goal="g")
        eid = store.log_tool_execution(tenantid="T", agent_id="a",
                                       tool_name="t", status="success")
        assert pid and eid
        assert coll.update_one.call_count == 2

    def test_record_audit_is_insert_only(self, mod):
        store, db, coll = _store(mod)
        aid = store.record_audit(tenantid="T", agent_id="a", action="write")
        assert aid
        args, kwargs = coll.update_one.call_args
        # Audit must never $set (mutate) an existing event — insert-only.
        assert "$set" not in args[1]
        assert "$setOnInsert" in args[1]
        assert kwargs.get("upsert") is True


# ---------------------------------------------------------------------------
# Contract validity for the new agent-operational collections
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,grain,key", [
    ("eden_agent_memory",   "eden_agent_memory",   "memoryId"),
    ("eden_agent_plan",     "eden_agent_plan",     "planId"),
    ("eden_tool_execution", "eden_tool_execution", "executionId"),
    ("eden_recommendation", "eden_recommendation", "recommendationId"),
    ("eden_agent_audit",    "eden_agent_audit",    "auditId"),
])
def test_agent_contracts_valid(name, grain, key):
    contract = json.loads((_CONTRACTS / f"{name}.json").read_text(encoding="utf-8"))
    props = contract["properties"]
    assert contract["additionalProperties"] is False
    assert props["_grain"]["const"] == grain
    # compound key + platform lineage required. featureName/_etlUpdatedAt were
    # dropped 2026-07-12 (see gold/pipeline/lineage.py's "Consolidation" note);
    # goldUpdatedAt is now the sole gold-side write-time stamp, and _env
    # (deployment-tier stamp) is required on every agent-operational contract.
    for req in ("tenantId", key, "_grain", "_schemaVersion", "goldUpdatedAt", "_env"):
        assert req in contract["required"], f"{name} missing required {req}"
    assert contract["x-upsert-key"] == ["tenantId", key]
