import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from source.agents.navigation_agent import NavigationAgent
from source.model.navigation_models import ProjectMatch, SessionContext

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def mock_permissions() -> dict:
    with open(FIXTURES_DIR / "mock_permissions.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def rbac_bypassed(monkeypatch):
    """Most navigation tests care about routing logic, not permissions —
    default every test to SKIP_RBAC_CHECK so route resolution is isolated.
    test_navigation_agent's permission-specific tests override this."""
    monkeypatch.setenv("SKIP_RBAC_CHECK", "true")


@pytest.fixture(autouse=True)
def no_real_mongo_audit_writes(monkeypatch):
    """Audit writes (write_navigation_audit_log, write_reasoning_audit_log)
    gate on mongo_client.is_reachable(), which pings a real Mongo server
    with an 8s timeout the first time it's called and caches the result
    for the rest of the process. Without this, the first test in the suite
    to exercise an audited code path (e.g. any Q1/Q2 reasoning test) would
    pay that full 8s against whatever MONGODB_URI happens to resolve to in
    this environment -- never a real assertion the test suite should
    depend on. Force it False so every audit write is the same safe no-op
    it would be in a real dev environment with no Mongo configured."""
    monkeypatch.setattr("source.utils.audit_log.is_reachable", lambda: False)


@pytest.fixture(autouse=True)
def no_real_redis_conversation_memory(monkeypatch):
    """ConversationMemory() connects to a REAL Redis server if one happens
    to be reachable at REDIS_HOST/REDIS_PORT (localhost:6379 by default) --
    true in some environments this suite runs in. Without this, tests would
    write real keys (last_project, last_reasoning_answer, etc.) that
    persist across test runs and are keyed by tenant/user/project ids many
    tests reuse verbatim (e.g. "henderson"/"uid-1") -- a prior run's leaked
    "As I mentioned earlier" prefix could silently break an unrelated
    test's exact-message assertion, entirely order/history-dependent.
    Forcing get_redis_client to return None makes every ConversationMemory
    instance behave exactly like "Redis unavailable" (an already-supported,
    already-tested degrade-to-no-op path), the same safe default as the
    Mongo-blocking fixture above. _redis is now a property delegating to
    this shared factory on every access (see source/utils/redis_client.py)
    rather than a value cached once at construction, so patching the
    factory function covers every access, not just the first."""
    monkeypatch.setattr(
        "source.resolvers.conversation_memory.get_redis_client",
        lambda: None,
    )


@pytest.fixture(autouse=True)
def deterministic_retrieval_backend(monkeypatch):
    """Pin every test's real NavigationAgent() to the tfidf backend,
    regardless of whether FastEmbed/semantic actually works on the machine
    running the suite. Tests that mock top_candidates with tfidf-scale
    scores (e.g. 0.301/0.276) rely on nav.candidate_threshold resolving to
    tfidf's 0.08 — on a machine where semantic retrieval genuinely loads
    (Developer Mode + a working model download), that same fixture would
    otherwise pick up RETRIEVAL_BACKEND=semantic from .env, resolving
    candidate_threshold to 0.3 instead and silently breaking every
    near-tie/threshold calculation the tests were written against. Whether
    semantic actually works on a given developer's machine must never
    change what the test suite exercises."""
    monkeypatch.setenv("RETRIEVAL_BACKEND", "tfidf")


@pytest.fixture
def nav_agent(monkeypatch) -> NavigationAgent:
    """A real NavigationAgent with the real route_registry.json, but network
    resolvers (Postgres, Mongo, Redis) replaced with in-memory fakes so tests
    never touch a live database."""
    agent = NavigationAgent()
    agent.entity_resolver = MagicMock()
    agent.entity_resolver.resolve_project.return_value = []
    agent.entity_resolver.resolve_by_id.return_value = []
    # Page-level permission now resolves from the same Mongo source as auth
    # (UserRoleResolver, gold_canonical.user_permissions) -- not Postgres.
    agent.role_resolver = MagicMock()
    agent.role_resolver.get_permissions.return_value = set()
    agent.llm_client = MagicMock()
    agent.llm_client.understand.return_value = None
    # Default to "LLM unreachable" (None), not an unconfigured MagicMock --
    # extract_project_name is now also called from the fast keyword-match
    # tier (see NavigationAgent._finalize/_resolve_entity_option), and an
    # unconfigured mock's `.get("project_name")` returns a truthy MagicMock
    # that gets treated as a real (garbage) project name instead of
    # falling back to the deterministic stripper these tests exercise.
    agent.llm_client.extract_project_name.return_value = None
    # Same "unconfigured MagicMock is truthy" gap -- data_question_response
    # now tries LLMClient.suggest_related_routes first (see
    # RELATED_ROUTES_SYSTEM_PROMPT); None here means "call failed," which
    # degrades to the deterministic DATA_TOPIC_ROUTES table these tests
    # actually exercise.
    agent.llm_client.suggest_related_routes.return_value = None
    return agent


def make_context(
    utterance: str,
    currentRoute: str = "/dashboard",
    currentEntity: dict | None = None,
    timeOnPage: int = 0,
    userId: str = "test-user",
    tenantId: str = "test-tenant",
    platform: str | None = None,
) -> SessionContext:
    return SessionContext(
        utterance=utterance,
        currentRoute=currentRoute,
        currentEntity=currentEntity,
        timeOnPage=timeOnPage,
        userId=userId,
        tenantId=tenantId,
        platform=platform,
    )


@pytest.fixture
def ctx():
    return make_context


def make_project_match(name: str, uid: str = "uid-1", status: str = "active") -> ProjectMatch:
    return ProjectMatch(project_uid=uid, project_name=name, status=status)


@pytest.fixture
def project_match():
    return make_project_match
