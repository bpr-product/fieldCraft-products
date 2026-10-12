from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Any, Callable, TypeVar

from sqlalchemy.exc import DBAPIError, OperationalError

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def with_retry(func: F) -> F:
    """Retry DAO calls that fail because of transient database errors."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        max_attempts = 3
        delay_seconds = 0.2

        for attempt in range(1, max_attempts + 1):
            try:
                return func(*args, **kwargs)
            except (OperationalError, DBAPIError):
                if attempt >= max_attempts:
                    raise
                logger.warning(
                    "Transient DB error in %s; retrying attempt %s/%s",
                    func.__qualname__,
                    attempt + 1,
                    max_attempts,
                    exc_info=True,
                )
                time.sleep(delay_seconds)
                delay_seconds *= 2

    return wrapper  # type: ignore[return-value]
