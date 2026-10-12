"""Unit tests for ConversationMemory.reset() -- the fix for the reset button
only ever clearing local frontend cache, not the real server-side state
(pending goal, last-discussed project) added since EDEN was first built."""
from unittest.mock import MagicMock, patch

from source.resolvers.conversation_memory import ConversationMemory


def _mock_redis_patch(fake):
    # _redis is now a property delegating to the shared redis_client
    # factory on every access (see conversation_memory.py) rather than a
    # plain attribute cached once at construction -- patch the factory
    # function itself so every access during the test sees the same fake.
    return patch("source.resolvers.conversation_memory.get_redis_client", return_value=fake)


def test_reset_deletes_all_short_term_keys():
    fake = MagicMock()
    with _mock_redis_patch(fake):
        memory = ConversationMemory()
        memory.reset("tenant-1", "user-1")
        # Real bug, fixed: this assertion was missing the notes-hint key
        # added after this test was written -- reset() genuinely deletes
        # all 5 short-term keys, the test just hadn't caught up.
        fake.delete.assert_called_once_with(
            "tenant:tenant-1:user:user-1:eden:history",
            "tenant:tenant-1:user:user-1:eden:pending_goal",
            "tenant:tenant-1:user:user-1:eden:last_project",
            "tenant:tenant-1:user:user-1:eden:last_project_id",
            "tenant:tenant-1:user:user-1:eden:notes_hint_shown",
        )


def test_reset_is_a_noop_when_redis_unavailable():
    with patch("source.resolvers.conversation_memory.get_redis_client", return_value=None):
        memory = ConversationMemory()
        # Must not raise even though there's no redis client at all.
        memory.reset("tenant-1", "user-1")


def test_reset_swallows_redis_failure():
    fake = MagicMock()
    fake.delete.side_effect = Exception("connection reset")
    with _mock_redis_patch(fake):
        memory = ConversationMemory()
        # Must not raise -- reset is best-effort, never blocks the user.
        memory.reset("tenant-1", "user-1")


class _FakeListRedis:
    """Minimal fake with REAL list semantics for rpush/ltrim/lrange (a plain
    MagicMock can't reproduce eviction behavior) -- just enough of the
    pipeline API append_turn actually calls."""

    def __init__(self):
        self.lists: dict[str, list[str]] = {}

    def pipeline(self):
        return _FakeListPipeline(self)

    def lrange(self, key, start, end):
        values = self.lists.get(key, [])
        if end == -1:
            end = len(values) - 1
        return values[start : end + 1]


class _FakeListPipeline:
    def __init__(self, redis: _FakeListRedis):
        self._redis = redis
        self._ops: list[tuple] = []

    def rpush(self, key, value):
        self._ops.append(("rpush", key, value))

    def ltrim(self, key, start, end):
        self._ops.append(("ltrim", key, start, end))

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))

    def execute(self):
        for op in self._ops:
            if op[0] == "rpush":
                _, key, value = op
                self._redis.lists.setdefault(key, []).append(value)
            elif op[0] == "ltrim":
                _, key, start, end = op
                values = self._redis.lists.get(key, [])
                if end == -1:
                    end = len(values) - 1
                self._redis.lists[key] = values[start:] if start >= 0 else values[start : end + 1 or None]
            # expire is a no-op here -- TTL expiry isn't what this test covers.
        self._ops = []


def test_go_back_history_survives_two_intervening_non_route_turns():
    """Real bug, fixed: confirmed live (fieldcraftmind, 2026-09-02) -- with
    HISTORY_MAX_TURNS at 3, a route turn (Materials) followed by two
    non-route turns (a fallback, then a disambiguation) evicted the
    Materials turn entirely before "go back" ever ran, since the raw
    ltrim'd buffer is shared by every turn type regardless of whether it
    carries a route. "back to the previous page" then found nothing but
    itself and silently no-op'd. Raised to 6 specifically so this
    2-intervening-turn shape (the one actually seen live) still leaves the
    earlier route turn in the window."""
    fake = _FakeListRedis()
    with _mock_redis_patch(fake):
        memory = ConversationMemory()
        memory.append_turn(
            "tenant-1", "user-1", "show me materials", "navigate",
            route="/admin", label="Materials",
        )
        memory.append_turn(
            "tenant-1", "user-1", "jessica brown", "fallback",
            route=None, label=None,
        )
        memory.append_turn(
            "tenant-1", "user-1", "customer name", "disambiguation",
            route=None, label=None,
        )
        memory.append_turn(
            "tenant-1", "user-1", "add new material", "navigate",
            route="/admin", label="Materials",
        )
        recent = memory.get_recent_turns("tenant-1", "user-1")
    routed = [t for t in recent if t.get("route")]
    # Both navigate turns (the Materials tab, twice) must still be present --
    # this is what lets _resolve_go_back find something other than a no-op
    # once it also filters for a genuinely different (route, routeState).
    assert len(routed) == 2
