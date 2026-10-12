"""Reusable retry decorator for transient DB/network errors in the Gold pipeline.

Per FIELDCRAFT coding standards, DB-access functions that call psycopg2/pymongo should
retry transient errors (connection reset, deadlock, lock timeout) rather than
crash the whole run on a single blip. This decorator centralises that behaviour
so callers don't hand-roll their own loops.

Usage:
    import psycopg2
    from gold.pipeline.retry import with_retry

    @with_retry(max_attempts=3, base_delay_ms=200, exceptions=(psycopg2.OperationalError,))
    def _query(...):
        ...
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Callable, TypeVar

log = logging.getLogger("gold.pipeline.retry")

_T = TypeVar("_T")


def with_retry(
    *,
    max_attempts: int = 3,
    base_delay_ms: int = 200,
    exceptions: tuple[type[BaseException], ...],
) -> Callable[[Callable[..., _T]], Callable[..., _T]]:
    """Retry the wrapped function on transient ``exceptions`` with exponential backoff.

    Args:
        max_attempts: total attempts (including the first). Must be >= 1.
        base_delay_ms: initial backoff; doubles after each failed attempt.
        exceptions: exception types treated as transient/retryable. Anything not
                    in this tuple propagates immediately. Required — no default,
                    by design: a bare ``(Exception,)`` default would silently
                    retry logic bugs (KeyError/TypeError/etc.) up to
                    max_attempts times with real sleeps, and for a
                    non-idempotent write, retrying a logic bug that already
                    partially wrote data could duplicate that partial write on
                    each retry. Every call site must state explicitly which
                    errors are actually transient (e.g.
                    ``exceptions=(psycopg2.OperationalError,)``).

    The final attempt's exception is re-raised so callers still see real failures.
    """
    attempts = max(1, max_attempts)

    def decorator(fn: Callable[..., _T]) -> Callable[..., _T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> _T:
            delay = base_delay_ms / 1000.0
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    if attempt >= attempts:
                        raise
                    log.warning(
                        "transient error in %s (attempt %d/%d), retrying in %.1fs: %s",
                        fn.__name__, attempt, attempts, delay, exc,
                    )
                    time.sleep(delay)
                    delay *= 2
            # Unreachable: the loop either returns or raises.
            raise AssertionError("with_retry exhausted without return or raise")

        return wrapper

    return decorator
