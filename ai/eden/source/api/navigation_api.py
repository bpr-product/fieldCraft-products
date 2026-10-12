import logging
import os

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException

from source.model.navigation_models import (
    HistoryResponse,
    NavigationResponse,
    ResetRequest,
    ResetResponse,
    SessionContext,
)
from source.orchestrator.agent_orchestrator import AgentOrchestrator
from source.utils.auth import AuthContext
from source.utils.rate_limit import enforce_rate_limit

load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter()
orchestrator = AgentOrchestrator()


def _enforce_admin_only(auth: AuthContext) -> None:
    """Temporary pilot gate: EDEN_ADMIN_ONLY (default false, off) restricts
    ALL THREE endpoints -- navigate/history/reset -- to the exactly-"admin"
    role, same is_admin() check margin visibility already uses (narrower
    than is_customer's "not customer" split; sales/projectManager/superAdmin
    etc. are refused too, not just customer roles). Explicit user direction:
    "right now it should only [work] for admin role" -- everyone else gets a
    clear 403, not a silent degrade, so this is never mistaken for an outage.
    Off by default so it reverts instantly with no code change -- same
    revert-without-redeploy shape as EDEN_NAV_ENABLED/MULTI_STEP_VERIFICATION."""
    if os.getenv("EDEN_ADMIN_ONLY", "false").lower() != "true":
        return
    if not orchestrator.role_resolver.is_admin(auth.tenant_id, auth.user_id):
        # Logged BEFORE raising -- the 403 itself is visible to the caller
        # from the HTTP response alone, but without this line a blocked
        # attempt leaves no server-side trace at all (no other logging runs
        # on this path). tenant_id/user_id only, no request body -- same
        # minimal-PII discipline as every other audit line in this service.
        logger.warning(
            f"EDEN_ADMIN_ONLY blocked non-admin request: "
            f"tenant_id={auth.tenant_id} user_id={auth.user_id}"
        )
        raise HTTPException(
            status_code=403,
            detail="EDEN is currently available to admin users only.",
        )


@router.post("/api/v1/agent/navigate", response_model=NavigationResponse)
def navigate(
    context: SessionContext,
    auth: AuthContext = Depends(enforce_rate_limit),
) -> NavigationResponse:
    # Deliberately sync (`def`, not `async def`): dispatch blocks on LLM
    # calls (up to 30s each) plus Redis/Mongo/Postgres I/O. As a coroutine
    # that froze the whole event loop — including /health — for every other
    # request while one was in flight. A sync handler runs in FastAPI's
    # threadpool instead, keeping the loop free.
    if not os.getenv("EDEN_NAV_ENABLED", "true").lower() == "true":
        raise HTTPException(status_code=503, detail="Navigation agent not enabled")
    _enforce_admin_only(auth)

    # userId/tenantId in the body are legacy fields other clients may still
    # send -- always overwritten with the verified JWT identity so nothing
    # downstream (session state, audit log, role resolution) can ever be
    # driven by a client-asserted identity. See docs/eden/AUTH_IMPLEMENTATION_PLAN.md.
    context.userId = auth.user_id
    context.tenantId = auth.tenant_id

    try:
        return orchestrator.dispatch(context)
    except Exception as e:
        logger.error(f"Navigation failed: {e}")
        return NavigationResponse(
            type="fallback",
            message="Navigation service temporarily unavailable. Please try again."
        )


@router.get("/api/v1/agent/history", response_model=HistoryResponse)
def history(
    limit: int = 20,
    auth: AuthContext = Depends(enforce_rate_limit),
) -> HistoryResponse:
    """Durable chat-history readback so the frontend can repopulate the chat
    window on reopen instead of starting blank every time (see
    ConversationMemory.get_history — this repo previously only wrote this
    data, never served it back). Identity comes from the verified JWT
    (auth.user_id/auth.tenant_id), never from client-supplied params."""
    if not os.getenv("EDEN_NAV_ENABLED", "true").lower() == "true":
        raise HTTPException(status_code=503, detail="Navigation agent not enabled")
    _enforce_admin_only(auth)

    try:
        turns = orchestrator.get_history(auth.tenant_id, auth.user_id, limit)
        return HistoryResponse(turns=turns)
    except Exception as e:
        logger.error(f"History fetch failed: {e}")
        return HistoryResponse(turns=[])


@router.post("/api/v1/agent/reset", response_model=ResetResponse)
def reset(
    body: ResetRequest,
    auth: AuthContext = Depends(enforce_rate_limit),
) -> ResetResponse:
    """Clears server-side conversation state (pending goal, last-discussed
    project, recent turns) for this tenant/user. Real gap this closes: the
    frontend's reset button previously only cleared its own local cache --
    accurate when EDEN had no server-side state at all, but the pending-goal
    and last-project features (each with a 5-10 min TTL) ARE real state now.
    Without this, resetting and immediately asking something new could
    still silently inherit an open goal or a remembered project from before
    the reset. Does NOT touch the durable Mongo history -- that's an
    intentional permanent record; see ConversationMemory.reset.

    body.tenantId/body.userId are ignored for authorization -- identity
    comes exclusively from the verified JWT (auth.tenant_id/auth.user_id)."""
    if not os.getenv("EDEN_NAV_ENABLED", "true").lower() == "true":
        raise HTTPException(status_code=503, detail="Navigation agent not enabled")
    _enforce_admin_only(auth)

    try:
        orchestrator.reset(auth.tenant_id, auth.user_id)
    except Exception as e:
        logger.error(f"Conversation reset failed: {e}")
    return ResetResponse(status="ok")
