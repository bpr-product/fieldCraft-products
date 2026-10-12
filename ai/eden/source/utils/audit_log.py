import logging
import os
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv

from source.utils.mongo_client import get_collection, is_reachable

load_dotenv()

logger = logging.getLogger(__name__)

AGENT_AUDIT_COLLECTION = "agent_audit"
PENDING_ESCALATIONS_COLLECTION = "pending_escalations"


def write_navigation_audit_log(
    tenant_id: str,
    user_id: str,
    utterance: str,
    resolved_route: str | None,
    confidence: float | None,
    status: str,
    platform: str | None = None,
) -> None:
    """
    Per EDEN_Navigation_Pilot_Plan.md (Week 9-10): every agent call is logged
    with userId, tenantId, utterance, resolved_route, confidence, timestamp.

    Per explicit user direction (migrated from a separate `eden_operational`
    database): the single audit destination is now `gold_eden.agent_audit` --
    a collection Data Engineering already defined for exactly this purpose
    (see datafinal.md section 3.3.1: "Append-only, immutable record of what
    an agent read/wrote/decided/executed -- satisfies SOC 2 / ISO 27001 /
    GDPR agent-activity monitoring requirements"), written directly by the
    agent runtime with no Silver source, same as EDEN already does here.
    Field names/enums below match that documented contract so this document
    is indistinguishable from one `AgentStore.record_audit()` would have
    written, even though no such shared client exists in this repo yet.
    `action="decision"` is the closest fit in that contract's enum (read/
    write/tool_call/recommend/plan/decision/memory_write/login/denied) for
    "the agent decided how to respond to this message" -- the free-form
    `detail` object carries the rest (utterance/route/confidence/status)
    that this contract has no dedicated fields for. An earlier version also
    wrote a slimmed row to the shared Postgres audit_log table, but that was
    dropped (2026-07-09, per product direction): the Postgres table couldn't
    hold the full record anyway (no utterance/confidence columns), and the
    synchronous write added its connection latency to every navigation
    request whenever Postgres was slow or down.

    Non-blocking: any failure is logged and swallowed, never raised — an
    audit write must never break a user's navigation request.
    """
    logger.info(
        "nav_audit utterance=%r resolved_route=%r confidence=%r tenant_id=%s user_id=%s "
        "status=%s platform=%s",
        utterance, resolved_route, confidence, tenant_id, user_id, status, platform,
    )

    if is_reachable():
        try:
            now = datetime.now(timezone.utc)
            get_collection(AGENT_AUDIT_COLLECTION).insert_one({
                "tenantId": tenant_id,
                "auditId": str(uuid.uuid4()),
                "agentId": "navigation",
                "userId": user_id,
                "sessionId": None,
                "action": "decision",
                "targetCollection": None,
                "targetDocKey": None,
                "outcome": None,
                "detail": {
                    "utterance": utterance,
                    "resolved_route": resolved_route,
                    "confidence": confidence,
                    "status": status,
                    "platform": platform,
                },
                "ts": now,
                "_grain": "eden_agent_audit",
                "_schemaVersion": 1,
                "goldUpdatedAt": now,
                "_env": os.getenv("PIPELINE_ENV", "dev"),
            })
        except Exception as e:
            logger.error(f"Mongo audit log write failed (non-blocking): {e}")


def write_reasoning_audit_log(
    tenant_id: str,
    user_id: str,
    agent_id: str,
    project_id: str | None,
    question_type: str,
    confidence: str | None,
    status: str,
) -> None:
    """
    Backlog #26159 (human escalation/handoff for unresolved queries):
    captures every Q1 (estimate-change)/Q2 (schedule-move) reasoning
    answer, so an unresolved one (no confirmed cause) can be surfaced to a
    human for follow-up. `question_type` is `"estimate_change"` or
    `"schedule_move"`; `confidence` is the same `"high"`/`"low"`/`"none"`/
    `None` vocabulary the reasoning code already uses (see
    EDEN_CustomerSupport_Constraints_Master_Reference.md) --
    `status="unresolved"` is written only when confidence is `"none"` or
    absent, i.e. exactly the cases where the user got the honest
    "I don't have a confirmed reason" fallback, never for a `"high"`/`"low"`
    answer or a Q3 margin refusal (that's a deliberate answer, not an
    unresolved one).

    Writes TWO things:
    1. Always: a row to `gold_eden.agent_audit` (same append-only contract
       as write_navigation_audit_log) -- the general observability trail,
       resolved and unresolved alike.
    2. Only when `status == "unresolved"`: a second, dedicated row to
       `gold_eden.pending_escalations` -- a queryable worklist a human/
       support tool reads directly (`handled: false`), rather than mining
       the general audit log. Per explicit user direction: DB-stored, not
       Slack -- see scripts/list_pending_escalations.py for the read side.

    Non-blocking: any failure in either write is logged and swallowed,
    never raised, same as every other audit write in this file.
    """
    logger.info(
        "reasoning_audit agent_id=%s question_type=%s confidence=%r "
        "tenant_id=%s user_id=%s project_id=%s status=%s",
        agent_id, question_type, confidence, tenant_id, user_id, project_id, status,
    )

    if not is_reachable():
        return

    now = datetime.now(timezone.utc)
    try:
        get_collection(AGENT_AUDIT_COLLECTION).insert_one({
            "tenantId": tenant_id,
            "auditId": str(uuid.uuid4()),
            "agentId": agent_id,
            "userId": user_id,
            "sessionId": None,
            "action": "decision",
            "targetCollection": "project_health",
            "targetDocKey": project_id,
            "outcome": None,
            "detail": {
                "question_type": question_type,
                "confidence": confidence,
                "status": status,
            },
            "ts": now,
            "_grain": "eden_agent_audit",
            "_schemaVersion": 1,
            "goldUpdatedAt": now,
            "_env": os.getenv("PIPELINE_ENV", "dev"),
        })
    except Exception as e:
        logger.error(f"Mongo reasoning audit log write failed (non-blocking): {e}")

    if status != "unresolved":
        return
    try:
        get_collection(PENDING_ESCALATIONS_COLLECTION).insert_one({
            "tenantId": tenant_id,
            "escalationId": str(uuid.uuid4()),
            "agentId": agent_id,
            "userId": user_id,
            "projectId": project_id,
            "questionType": question_type,
            "confidence": confidence,
            "handled": False,
            "handledAt": None,
            "handledBy": None,
            "createdAt": now,
            "_grain": "eden_pending_escalation",
            "_schemaVersion": 1,
            "_env": os.getenv("PIPELINE_ENV", "dev"),
        })
    except Exception as e:
        logger.error(f"Mongo pending-escalation write failed (non-blocking): {e}")
