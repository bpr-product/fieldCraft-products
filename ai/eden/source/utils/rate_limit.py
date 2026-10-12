import logging
import os
import time

import redis
from fastapi import Depends, HTTPException

from source.utils.auth import AuthContext, get_authenticated_subject
from source.utils.redis_client import get_redis_client

logger = logging.getLogger(__name__)

# Fixed-window counter per tenant, per minute -- simple and cheap (one INCR +
# one conditional EXPIRE per request) rather than a true sliding window; good
# enough for an abuse/cost control, not a precision billing meter. Fails
# OPEN on Redis being unreachable (same graceful-degradation philosophy as
# ConversationMemory) -- a rate limiter that takes the service down when its
# own backing store hiccups is worse than temporarily unmetered traffic.
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))


def _get_redis() -> redis.Redis | None:
    """Delegates to the shared client factory (source/utils/redis_client.py)
    -- socket timeouts, call-level retry, and cooldown-gated reconnect all
    live there now. Real gap this closed: the old one-shot "checked once,
    cache None forever" flag here meant a Redis blip at process startup
    permanently disabled rate limiting (failing open silently) for the rest
    of the process's life; the shared factory retries on a cooldown instead."""
    return get_redis_client()


def _check_and_increment(tenant_id: str) -> tuple[bool, int]:
    """Returns (allowed, current_count). Fails open (allowed=True) on any
    Redis error -- see module docstring."""
    client = _get_redis()
    if client is None:
        return True, 0
    window = int(time.time() // 60)
    key = f"ratelimit:tenant:{tenant_id}:{window}"
    try:
        count = client.incr(key)
        if count == 1:
            client.expire(key, 60)
        return count <= RATE_LIMIT_PER_MINUTE, count
    except Exception as e:
        logger.warning(f"Rate limit check failed for tenant={tenant_id!r} — failing open: {e}")
        return True, 0


def enforce_rate_limit(auth: AuthContext = Depends(get_authenticated_subject)) -> AuthContext:
    """FastAPI dependency: per-tenant request-rate cap. Placed AFTER
    get_authenticated_subject (depends on it) so the limit is keyed on the
    verified tenant id, never a client-supplied one -- same trust rule as
    every other identity use in this service. Returns `auth` unchanged so
    routes can depend on this alone instead of stacking both dependencies."""
    allowed, count = _check_and_increment(auth.tenant_id)
    if not allowed:
        logger.warning(
            f"Rate limit exceeded: tenant={auth.tenant_id} count={count} "
            f"limit={RATE_LIMIT_PER_MINUTE}/min"
        )
        raise HTTPException(status_code=429, detail="Too many requests — slow down")
    return auth
