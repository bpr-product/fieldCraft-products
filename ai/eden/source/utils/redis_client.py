import logging
import os
import time

import redis
from redis.backoff import ExponentialBackoff
from redis.retry import Retry

logger = logging.getLogger(__name__)

# Both rate_limit.py and conversation_memory.py talk to the SAME Redis
# instance for the same reason (short-TTL, non-durable state -- see the
# state-and-memory skill) -- one connection policy shared here instead of
# two hand-copied ones that can silently drift apart.
_SOCKET_TIMEOUT_SECONDS = float(os.getenv("REDIS_SOCKET_TIMEOUT_SECONDS", "2"))
_SOCKET_CONNECT_TIMEOUT_SECONDS = float(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS", "2"))
# How long to wait before trying to (re)connect again after a failure --
# bounds the cost of a down Redis to one connect attempt per window instead
# of paying a fresh socket_connect_timeout on every single request.
_RECONNECT_COOLDOWN_SECONDS = float(os.getenv("REDIS_RECONNECT_COOLDOWN_SECONDS", "30"))
_CALL_RETRIES = 2

_client: redis.Redis | None = None
_next_connect_attempt_at: float = 0.0


def get_redis_client() -> redis.Redis | None:
    """Shared Redis client factory. Returns None when Redis is currently
    unreachable -- every caller in this service already treats None as
    "state unavailable, fail open/soft," unchanged by this.

    Two distinct resiliency layers, not one:
    - Connection-level: bounded socket timeouts (previously UNSET on both
      call sites -- a hung/unreachable Redis could block a request
      indefinitely instead of failing fast) plus a cooldown-gated
      reconnect, so a Redis that recovers after a transient outage is
      picked back up automatically without a process restart. The old
      pattern (a one-shot "checked once, cache the verdict forever" flag in
      rate_limit.py) never retried again after the first failure, for the
      rest of the process's life -- a short blip at startup permanently
      disabled rate limiting/conversation memory until redeploy.
    - Call-level: redis-py's own retry_on_timeout/retry_on_error with a
      short exponential backoff, so a single transient blip on an already-
      established connection (a momentary network hiccup, not a real
      outage) is retried in place instead of immediately degrading."""
    global _client, _next_connect_attempt_at
    if _client is not None:
        return _client

    now = time.time()
    if now < _next_connect_attempt_at:
        return None

    try:
        client = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            decode_responses=True,
            socket_timeout=_SOCKET_TIMEOUT_SECONDS,
            socket_connect_timeout=_SOCKET_CONNECT_TIMEOUT_SECONDS,
            retry_on_timeout=True,
            retry_on_error=[redis.exceptions.ConnectionError, redis.exceptions.TimeoutError],
            retry=Retry(ExponentialBackoff(base=0.1, cap=0.5), retries=_CALL_RETRIES),
        )
        client.ping()
        _client = client
        return _client
    except Exception as e:
        _next_connect_attempt_at = now + _RECONNECT_COOLDOWN_SECONDS
        logger.warning(
            f"Redis unavailable — will retry connecting in "
            f"{_RECONNECT_COOLDOWN_SECONDS:.0f}s: {e}"
        )
        return None
