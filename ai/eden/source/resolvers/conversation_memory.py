import json
import logging
import os
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv

from source.utils.mongo_client import get_collection, is_reachable
from source.utils.redis_client import get_redis_client

load_dotenv()

logger = logging.getLogger(__name__)

AGENT_MEMORY_COLLECTION = "agent_memory"
# Durable "user hit Reset" marker, one doc per tenant/user -- deliberately
# NOT the sessionStorage-based marker the frontend used to rely on alone
# (Chat.tsx's resetMarkerKey), which only survives as long as the browser
# tab/session does. Real gap that closed: sessionStorage is cleared on tab
# close / new login session, so a reset "reverted" the moment the user came
# back later -- the full permanent agent_memory history (which is, by
# design, never deleted -- see ConversationMemory.reset's own comment)
# reappeared as if reset had never happened. This collection is the
# single source of truth get_history() now filters against, so a reset
# stays reset across logins/devices, without touching (or needing to
# touch) the permanent audit record itself.
RESET_MARKER_COLLECTION = "agent_reset_markers"

HISTORY_TTL_SECONDS = int(os.getenv("CONVERSATION_HISTORY_TTL_SECONDS", "600"))
# Real bug, fixed: at 3, this raw buffer is shared by EVERY turn regardless
# of type -- a "go back" a couple of turns after a non-navigating exchange
# (a fallback like an unmatched name search, or a disambiguation ask) can
# find its real target already ltrim'd out, since those turns consume the
# same fixed budget without themselves carrying a route. Confirmed live
# (fieldcraftmind, 2026-09-02): Materials -> [fallback turn] -> [disambiguation
# turn] -> Materials again, then "back to the previous page" had nothing
# left in the buffer but the Materials turn itself, and _resolve_go_back's
# own "nothing else remembered" fallback replayed it as a silent no-op --
# reading as "go back isn't working" even though the code was correct given
# what it could see. Raised to 6 so two or three intervening non-route
# turns no longer starve go-back of the actual earlier page; still small
# enough to keep the LLM-injected recent-turns context this window also
# feeds (see get_recent_turns' docstring) cheap.
HISTORY_MAX_TURNS = 6
# Pending navigation goal (the conversation-loop state, see
# AgentOrchestrator._update_goal_state): expires on its own so an abandoned
# clarification ("which did you mean?" the user never answers) can't leak
# into an unrelated session tomorrow. Shorter than the history TTL — a
# clarification exchange is an active back-and-forth, not a browsing window.
PENDING_GOAL_TTL_SECONDS = int(os.getenv("PENDING_GOAL_TTL_SECONDS", "300"))
# Last project Insight/Reporting successfully resolved and answered about —
# lets a topic-switching follow-up ("is it delayed too?" right after a cost
# question, no project named, not on that project's page) still resolve to
# the same project instead of re-asking "which project?" from scratch. Same
# TTL window as the turn history — this is "what were we just discussing,"
# not a long-lived preference.
LAST_PROJECT_TTL_SECONDS = int(os.getenv("LAST_PROJECT_TTL_SECONDS", "600"))


class ConversationMemory:
    """Short-term, per-user memory so the LLM-fallback path can resolve
    follow-ups ("no, the other one", "show me its schedule instead").

    EDEN's navigation agent is otherwise deliberately stateless (see
    AIChatService.ts: "EDEN has no server-side conversation state") — this
    does not change that for the deterministic keyword/feature paths, which
    stay pure per-request lookups. It only gives the LLM a short, bounded
    window of recent turns for the ~10% of requests that reach it, since
    that's the only place "what did I just ask" can plausibly matter.

    Write-through: every turn goes to Redis (fast path RBACResolver also
    uses — this is what get_recent_turns actually reads for the live LLM
    call, needs to be low-latency) AND to MongoDB's gold_eden database
    (agent_memory collection — a durable, permanent record for review/
    analytics). Per explicit user direction (migrated from a separate
    `eden_operational` database): `agent_memory` is a collection Data
    Engineering already defined for exactly this purpose (see datafinal.md
    section 3.3.2, "Persistent agent memory across turns/sessions"), written
    directly by the agent runtime with no Silver source, in the SAME
    database EDEN already reads project_health/invoice_summary from — no
    reason for a second, home-grown database duplicating what this
    collection already exists for. Each turn is written with `memoryType:
    "episodic"` (a specific past event, not a standing preference) and
    `expiresAt: null` (no TTL — this is the durable, permanent transcript,
    same as the old nav_conversation_history was, NOT the short-lived
    Redis-only state below). The two failure modes are independent and both
    non-blocking: if Redis is down, live follow-up resolution degrades but
    the durable Mongo record still gets written; if Mongo is down (e.g.
    still on the placeholder URI), live follow-up resolution is unaffected.
    """

    @property
    def _redis(self):
        """Delegates to the shared client factory (source/utils/
        redis_client.py) on every access rather than caching a connection
        (or lack of one) once at construction. Real gap this closed: this
        class is a long-lived singleton (built once in AgentOrchestrator),
        so the old `self._redis = self._connect_redis()` in __init__ meant
        a Redis blip at process startup permanently disabled conversation
        memory for the rest of the process's life. The factory itself is
        cooldown-gated (see its docstring), so this doesn't hammer Redis on
        every single call while it's down."""
        return get_redis_client()

    def _key(self, tenant_id: str, user_id: str) -> str:
        return f"tenant:{tenant_id}:user:{user_id}:eden:history"

    def _goal_key(self, tenant_id: str, user_id: str) -> str:
        return f"tenant:{tenant_id}:user:{user_id}:eden:pending_goal"

    def _last_project_key(self, tenant_id: str, user_id: str) -> str:
        return f"tenant:{tenant_id}:user:{user_id}:eden:last_project"

    def _last_project_id_key(self, tenant_id: str, user_id: str) -> str:
        return f"tenant:{tenant_id}:user:{user_id}:eden:last_project_id"

    def _notes_hint_key(self, tenant_id: str, user_id: str) -> str:
        return f"tenant:{tenant_id}:user:{user_id}:eden:notes_hint_shown"

    def _reasoning_answer_key(self, tenant_id: str, user_id: str, project_id: str, question_type: str) -> str:
        return f"tenant:{tenant_id}:user:{user_id}:eden:reasoning:{project_id}:{question_type}"

    # ── pending navigation goal (conversation-loop state) ──────────────────
    # A goal is opened when a turn ends unresolved (EDEN asked a question or
    # offered options instead of navigating), carried across turns so the
    # user's next message is read as an ANSWER to that question rather than
    # a brand-new request, and closed on successful navigation. All three
    # methods degrade to no-ops when Redis is down — the loop simply doesn't
    # persist, which is exactly the pre-loop behavior.

    def get_pending_goal(self, tenant_id: str, user_id: str) -> dict | None:
        if not self._redis:
            return None
        try:
            raw = self._redis.get(self._goal_key(tenant_id, user_id))
            return json.loads(raw) if raw else None
        except Exception as e:
            logger.warning(f"Pending goal read failed: {e}")
            return None

    def set_pending_goal(self, tenant_id: str, user_id: str, goal: dict) -> None:
        if not self._redis:
            return
        try:
            self._redis.setex(
                self._goal_key(tenant_id, user_id),
                PENDING_GOAL_TTL_SECONDS,
                json.dumps(goal),
            )
        except Exception as e:
            logger.warning(f"Pending goal write failed: {e}")

    def clear_pending_goal(self, tenant_id: str, user_id: str) -> None:
        if not self._redis:
            return
        try:
            self._redis.delete(self._goal_key(tenant_id, user_id))
        except Exception as e:
            logger.warning(f"Pending goal clear failed: {e}")

    # ── last resolved project (cross-topic continuity) ─────────────────────
    # Insight and Reporting both resolve "which project is this about" the
    # same way (see EntityResolver) -- this remembers whatever name that
    # resolution last succeeded with, so a follow-up that switches topic
    # (cost question -> schedule question) but not project still knows what
    # "it" refers to, without either agent needing to know the other ran.
    # Degrades to no-ops when Redis is down, same as the rest of this class.

    def get_last_project(self, tenant_id: str, user_id: str) -> str | None:
        if not self._redis:
            return None
        try:
            return self._redis.get(self._last_project_key(tenant_id, user_id))
        except Exception as e:
            logger.warning(f"Last-project read failed: {e}")
            return None

    def set_last_project(self, tenant_id: str, user_id: str, project_name: str) -> None:
        if not self._redis or not project_name:
            return
        try:
            self._redis.setex(
                self._last_project_key(tenant_id, user_id),
                LAST_PROJECT_TTL_SECONDS,
                project_name,
            )
        except Exception as e:
            logger.warning(f"Last-project write failed: {e}")

    # Real gap, fixed: last_project only ever remembered the NAME -- real
    # Gold data has severe projectName duplication (one customer can have a
    # dozen+ real projects all named after themselves, see
    # EntityResolver.project_ids_for_customer), so a name-only fallback
    # re-triggers "which project?" disambiguation even immediately after
    # the exact project was uniquely pinned down by ORDER NUMBER the turn
    # before ("272000" resolves to exactly one project; asking about it
    # again by name alone does not). Remembering the ID too lets
    # continuity re-resolve EXACTLY, never ambiguously, whenever it's
    # available -- name stays as the fallback for when no id was captured.
    def get_last_project_id(self, tenant_id: str, user_id: str) -> str | None:
        if not self._redis:
            return None
        try:
            return self._redis.get(self._last_project_id_key(tenant_id, user_id))
        except Exception as e:
            logger.warning(f"Last-project-id read failed: {e}")
            return None

    def set_last_project_id(self, tenant_id: str, user_id: str, project_id: str) -> None:
        if not self._redis or not project_id:
            return
        try:
            self._redis.setex(
                self._last_project_id_key(tenant_id, user_id),
                LAST_PROJECT_TTL_SECONDS,
                project_id,
            )
        except Exception as e:
            logger.warning(f"Last-project-id write failed: {e}")

    # ── prior Q1/Q2/Q3 reasoning answer, per project+question type ──────────
    # Lets EDEN honestly reference its OWN prior answer ("as I mentioned
    # earlier" / "this is different from what I told you before") without
    # ever fabricating a connection BETWEEN different question types (e.g.
    # asserting Q1's estimate-change cause is "related to" Q2's schedule
    # move) -- that would assert a link the underlying evidence doesn't
    # prove, the same discipline the confidence-gating in insight_agent.py/
    # reporting_agent.py already enforces. This only ever compares an
    # answer against ITS OWN PAST SELF (same project, same question_type),
    # never across question types. Same TTL family as last_project --
    # "what did I just tell this user," not a permanent record.

    def get_last_reasoning_answer(
        self, tenant_id: str, user_id: str, project_id: str, question_type: str
    ) -> dict | None:
        if not self._redis:
            return None
        try:
            raw = self._redis.get(self._reasoning_answer_key(tenant_id, user_id, project_id, question_type))
            return json.loads(raw) if raw else None
        except Exception as e:
            logger.warning(f"Last-reasoning-answer read failed: {e}")
            return None

    def set_last_reasoning_answer(
        self, tenant_id: str, user_id: str, project_id: str, question_type: str, message: str, confidence
    ) -> None:
        if not self._redis or not project_id:
            return
        try:
            self._redis.setex(
                self._reasoning_answer_key(tenant_id, user_id, project_id, question_type),
                LAST_PROJECT_TTL_SECONDS,
                json.dumps({"message": message, "confidence": confidence}),
            )
        except Exception as e:
            logger.warning(f"Last-reasoning-answer write failed: {e}")

    # ── one-time "you can jot notes here" hint per project ─────────────────
    # Tracks which project ids have already gotten the proactive Notes-panel
    # suggestion this conversation, so it's offered once per project (the
    # first time it's discussed) instead of on every single turn about it.
    # A Redis SET, not a single key -- unlike last_project (one value,
    # overwritten each turn), this needs to remember EVERY project already
    # shown the hint, since the conversation can move between several.
    # Same TTL/degradation story as the rest of this class: expires with the
    # session, no-ops if Redis is down (hint just won't show, never crashes).

    def has_shown_notes_hint(self, tenant_id: str, user_id: str, project_id: str) -> bool:
        if not self._redis or not project_id:
            return False
        try:
            return bool(self._redis.sismember(self._notes_hint_key(tenant_id, user_id), project_id))
        except Exception as e:
            logger.warning(f"Notes-hint read failed: {e}")
            return False

    def mark_notes_hint_shown(self, tenant_id: str, user_id: str, project_id: str) -> None:
        if not self._redis or not project_id:
            return
        try:
            key = self._notes_hint_key(tenant_id, user_id)
            self._redis.sadd(key, project_id)
            self._redis.expire(key, LAST_PROJECT_TTL_SECONDS)
        except Exception as e:
            logger.warning(f"Notes-hint write failed: {e}")

    def reset(self, tenant_id: str, user_id: str) -> None:
        """Clears every short-term Redis key for this tenant/user: recent
        turns, pending goal, last-discussed project. Real gap this closes:
        the frontend's reset button only ever cleared its OWN local cache
        (EDEN was originally built with no server-side conversation state at
        all, per AIChatService.ts's own comment) -- but the pending-goal and
        last-project features added since then ARE real server-side state,
        with their own TTLs (5-10 min). Without this, clicking reset and
        immediately asking something new could still silently inherit an
        open goal or a remembered project from before the reset, for up to
        that TTL window. Does NOT delete the durable Mongo history
        (gold_eden.agent_memory) -- that's an intentional permanent record.
        Real gap, fixed: this used to rely ENTIRELY on the frontend's own
        sessionStorage-based reset-marker timestamp to hide that history
        from view, which only survives as long as the browser tab/session
        does -- reopening in a new tab, after a browser restart, or on a
        new login lost the marker, and the full pre-reset history reappeared
        as if reset had never happened (confirmed live). Now also records a
        DURABLE marker (see set_durable_reset_marker/get_history) that
        survives across logins/devices, independent of any client-side
        state. Degrades to a no-op if Redis is down, same as every other
        method here (the durable Mongo marker write is separate and
        independently best-effort)."""
        self.set_durable_reset_marker(tenant_id, user_id)
        if not self._redis:
            return
        try:
            self._redis.delete(
                self._key(tenant_id, user_id),
                self._goal_key(tenant_id, user_id),
                self._last_project_key(tenant_id, user_id),
                self._last_project_id_key(tenant_id, user_id),
                self._notes_hint_key(tenant_id, user_id),
            )
        except Exception as e:
            logger.warning(f"Conversation reset failed: {e}")

    def get_recent_turns(self, tenant_id: str, user_id: str) -> list[dict]:
        if not self._redis:
            return []
        try:
            raw = self._redis.lrange(self._key(tenant_id, user_id), 0, -1)
            return [json.loads(t) for t in raw]
        except Exception as e:
            logger.warning(f"Conversation memory read failed: {e}")
            return []

    def append_turn(
        self,
        tenant_id: str,
        user_id: str,
        utterance: str,
        response_type: str,
        route: str | None,
        label: str | None,
        options: list[dict] | None = None,
        response_payload: dict | None = None,
        route_state: dict | None = None,
        project: str | None = None,
        is_soft_suggestion: bool = False,
    ) -> None:
        if self._redis:
            try:
                key = self._key(tenant_id, user_id)
                # `options` rides along for disambiguation turns (label+route
                # per offered choice) so affirmation/follow-up resolution has
                # referents; omitted otherwise to keep the Redis window lean.
                turn_data = {
                    "utterance": utterance,
                    "type": response_type,
                    "route": route,
                    "label": label,
                }
                # Real bug, fixed: `label` is the PAGE (a suggestion's or
                # route's label_template, e.g. "Projects") -- a completely
                # different concept from the PROJECT actually being
                # discussed (e.g. "Peter Parker"). Insight/Reporting's
                # guidance answers always attach a generic page suggestion
                # (see AgentOrchestrator._attach_page_suggestion), which was
                # overwriting `label` with that page name before entity
                # continuity ever got a chance -- "Recent conversation" then
                # told the next turn's LLM the referent was "(Projects)"
                # instead of "(Peter Parker)", actively misleading it rather
                # than just omitting the signal. Stored separately so
                # route/label-based mechanisms (_route_confirmed_by_context,
                # verify_remembered_suggestion_permission) are unaffected.
                if project:
                    turn_data["project"] = project
                if options:
                    turn_data["options"] = options
                # Real bug, fixed: without this, a bare "yes" confirming an
                # entity-scoped suggestion (e.g. "Peter Parker — Project
                # Invoices") had no way to recall WHICH project made that
                # suggestion meaningful -- re-deriving the route from its
                # bare pattern string tried to re-extract a project name
                # from the confirmation word itself ("yes"), which
                # obviously matches nothing, and fell back to "which
                # project did you mean?". Remembering the already-resolved
                # routeState lets a confirmation replay it directly instead
                # of re-resolving from scratch (see
                # AgentOrchestrator._resolve_affirmation).
                if route_state:
                    turn_data["routeState"] = route_state
                # See AgentOrchestrator._finalize_turn's is_soft_suggestion
                # comment -- distinguishes an unsolicited "by the way, want to
                # see X?" tail on a data answer from a genuine navigation
                # offer, so _resolve_affirmation can require more than bare
                # "sure"/"ok" filler before treating it as accepted. Omitted
                # when False to keep the common case's stored turn unchanged.
                if is_soft_suggestion:
                    turn_data["isSoftSuggestion"] = True
                turn = json.dumps(turn_data)
                pipe = self._redis.pipeline()
                pipe.rpush(key, turn)
                pipe.ltrim(key, -HISTORY_MAX_TURNS, -1)
                pipe.expire(key, HISTORY_TTL_SECONDS)
                pipe.execute()
            except Exception as e:
                logger.warning(f"Conversation memory Redis write failed: {e}")

        # response_payload carries the full NavigationResponse (message,
        # options, suggestion, question, highlight, ...) so chat-history
        # readback (get_history) can re-render the exact bubble the user
        # saw, not just the resolved route/label used for LLM follow-up
        # resolution above. Redis intentionally stays lean (route/label
        # only) — it's a short TTL window for the LLM path, not a transcript
        # store; Mongo is the durable, full-fidelity record. Field names
        # below match gold_eden.agent_memory's documented contract (see
        # datafinal.md 3.3.2) -- everything this collection has no
        # dedicated field for (utterance/type/route/label/response) lives
        # inside `content`, the same free-form-object escape hatch the
        # contract provides.
        if is_reachable():
            try:
                now = datetime.now(timezone.utc)
                get_collection(AGENT_MEMORY_COLLECTION).insert_one({
                    "tenantId": tenant_id,
                    "memoryId": str(uuid.uuid4()),
                    "agentId": "navigation",
                    "userId": user_id,
                    "projectId": None,
                    "sessionId": None,
                    "memoryType": "episodic",
                    "scope": "session",
                    "content": {
                        "utterance": utterance,
                        "type": response_type,
                        "route": route,
                        "label": label,
                        "response": response_payload or {},
                    },
                    "summary": utterance,
                    "importance": 0.5,
                    "embeddingModel": None,
                    "embeddingVersion": None,
                    "vectorSyncedAt": None,
                    "expiresAt": None,
                    "createdAt": now,
                    "isDeleted": False,
                    "_grain": "eden_agent_memory",
                    "_schemaVersion": 1,
                    "goldUpdatedAt": now,
                    "_env": os.getenv("PIPELINE_ENV", "dev"),
                })
            except Exception as e:
                logger.warning(f"Conversation memory Mongo write failed: {e}")

    def set_durable_reset_marker(self, tenant_id: str, user_id: str) -> None:
        """Records "reset happened at time T" durably in Mongo, so get_history
        can filter out everything before it regardless of which device/tab/
        login session reads history back afterward. Degrades to a no-op if
        Mongo is unreachable -- reset still clears the (short-TTL) Redis
        state either way; only the durable-history filtering is skipped."""
        if not is_reachable():
            return
        try:
            get_collection(RESET_MARKER_COLLECTION).update_one(
                {"tenantId": tenant_id, "userId": user_id},
                {"$set": {"resetAt": datetime.now(timezone.utc)}},
                upsert=True,
            )
        except Exception as e:
            logger.warning(f"Durable reset-marker write failed: {e}")

    def get_durable_reset_marker(self, tenant_id: str, user_id: str) -> datetime | None:
        if not is_reachable():
            return None
        try:
            doc = get_collection(RESET_MARKER_COLLECTION).find_one(
                {"tenantId": tenant_id, "userId": user_id}, {"resetAt": 1, "_id": 0}
            )
            return doc.get("resetAt") if doc else None
        except Exception as e:
            logger.warning(f"Durable reset-marker read failed: {e}")
            return None

    def get_history(self, tenant_id: str, user_id: str, limit: int = 20) -> list[dict]:
        """Durable chat-history readback for the frontend (Mongo, not Redis —
        Redis holds only a short TTL window for LLM follow-up resolution, not
        a transcript). Returns turns oldest-first, capped at `limit`, each
        with the original utterance and the full response payload so the
        frontend can re-render the same bubble the user originally saw.
        Reads gold_eden.agent_memory (see append_turn) filtered to this
        agent's own episodic turns -- `agentId: "navigation"` keeps this
        readback scoped to chat turns even after other Eden agents start
        writing their own memories into the same shared collection."""
        if not is_reachable():
            return []
        try:
            query = {"tenantId": tenant_id, "userId": user_id, "agentId": "navigation"}
            reset_at = self.get_durable_reset_marker(tenant_id, user_id)
            if reset_at:
                query["createdAt"] = {"$gt": reset_at}
            cursor = (
                get_collection(AGENT_MEMORY_COLLECTION)
                .find(query)
                .sort("createdAt", -1)
                .limit(limit)
            )
            turns = list(cursor)
            turns.reverse()
            return [
                {
                    "utterance": (t.get("content") or {}).get("utterance"),
                    "type": (t.get("content") or {}).get("type"),
                    "route": (t.get("content") or {}).get("route"),
                    "label": (t.get("content") or {}).get("label"),
                    "response": (t.get("content") or {}).get("response", {}),
                    "created_at": t.get("createdAt").isoformat() if t.get("createdAt") else None,
                }
                for t in turns
            ]
        except Exception as e:
            logger.warning(f"Conversation memory Mongo read failed: {e}")
            return []
