import logging
import os

log = logging.getLogger(__name__)

_KEY_PREFIX = "external:cursor"


class CursorStore:
    """Redis-backed cursor/watermark store for poll-based external connectors.

    Falls back gracefully to the caller-supplied default when Redis is
    unavailable — connectors must not abort on cursor store failure.
    """

    def __init__(self, redis_url: str = "") -> None:
        self._url = redis_url or os.environ.get("EXTERNAL_CURSOR_REDIS_URL") or os.environ.get("REDIS_URL", "")
        self._redis = None
        self._attempted = False

    def _client(self):
        if self._attempted:
            return self._redis
        self._attempted = True
        if not self._url:
            return None
        try:
            import redis
            self._redis = redis.Redis.from_url(self._url, decode_responses=True, socket_timeout=2)
            self._redis.ping()
        except Exception as exc:
            log.warning("CursorStore: Redis unavailable (%s) — cursors will use fallback values", exc)
            self._redis = None
        return self._redis

    @staticmethod
    def _key(source: str, entity: str, tenant_id: str) -> str:
        return f"{_KEY_PREFIX}:{source}:{entity}:{tenant_id}"

    def get(self, source: str, entity: str, tenant_id: str, fallback: str = "") -> str:
        r = self._client()
        if r is None:
            return fallback
        try:
            val = r.get(self._key(source, entity, tenant_id))
            return val if val is not None else fallback
        except Exception as exc:
            log.warning("CursorStore.get failed (%s) — using fallback", exc)
            return fallback

    def set(self, source: str, entity: str, tenant_id: str, value: str) -> None:
        r = self._client()
        if r is None:
            return
        try:
            r.set(self._key(source, entity, tenant_id), value)
        except Exception as exc:
            log.warning("CursorStore.set failed (%s) — cursor not persisted", exc)
