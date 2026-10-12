import pytest
from fastapi import HTTPException

from source.utils import rate_limit, redis_client
from source.utils.auth import AuthContext


@pytest.fixture(autouse=True)
def _reset_redis_probe(monkeypatch):
    # Each test controls Redis reachability explicitly via monkeypatching
    # _get_redis, so the shared redis_client module's cached connection/
    # reconnect-cooldown state never leaks a real connection attempt (or
    # its absence) between tests.
    monkeypatch.setattr(redis_client, "_client", None)
    monkeypatch.setattr(redis_client, "_next_connect_attempt_at", 0.0)


class _FakeRedis:
    def __init__(self):
        self.counts = {}

    def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def expire(self, key, ttl):
        pass


def test_allows_requests_under_the_limit(monkeypatch):
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_PER_MINUTE", 5)
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit, "_get_redis", lambda: fake)

    auth = AuthContext(user_id="u1", tenant_id="tn1", roles=[], permissions=[])
    for _ in range(5):
        result = rate_limit.enforce_rate_limit(auth)
        assert result is auth


def test_blocks_requests_over_the_limit(monkeypatch):
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_PER_MINUTE", 3)
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit, "_get_redis", lambda: fake)

    auth = AuthContext(user_id="u1", tenant_id="tn1", roles=[], permissions=[])
    for _ in range(3):
        rate_limit.enforce_rate_limit(auth)
    with pytest.raises(HTTPException) as exc_info:
        rate_limit.enforce_rate_limit(auth)
    assert exc_info.value.status_code == 429


def test_tenants_are_scoped_independently(monkeypatch):
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_PER_MINUTE", 1)
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit, "_get_redis", lambda: fake)

    auth_a = AuthContext(user_id="u1", tenant_id="tenant-a", roles=[], permissions=[])
    auth_b = AuthContext(user_id="u2", tenant_id="tenant-b", roles=[], permissions=[])
    # tenant-a exhausts its own limit...
    rate_limit.enforce_rate_limit(auth_a)
    with pytest.raises(HTTPException):
        rate_limit.enforce_rate_limit(auth_a)
    # ...but tenant-b is unaffected -- limits are per-tenant, not global.
    result = rate_limit.enforce_rate_limit(auth_b)
    assert result is auth_b


def test_fails_open_when_redis_unreachable(monkeypatch):
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_PER_MINUTE", 1)
    monkeypatch.setattr(rate_limit, "_get_redis", lambda: None)

    auth = AuthContext(user_id="u1", tenant_id="tn1", roles=[], permissions=[])
    # Redis down -> every call must still succeed (fail open), not 429 or crash.
    for _ in range(10):
        result = rate_limit.enforce_rate_limit(auth)
        assert result is auth


def test_fails_open_on_redis_error_mid_request(monkeypatch):
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_PER_MINUTE", 1)

    class _BoomRedis:
        def incr(self, key):
            raise ConnectionError("redis down mid-flight")

    monkeypatch.setattr(rate_limit, "_get_redis", lambda: _BoomRedis())
    auth = AuthContext(user_id="u1", tenant_id="tn1", roles=[], permissions=[])
    result = rate_limit.enforce_rate_limit(auth)
    assert result is auth
