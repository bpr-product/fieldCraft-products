from unittest.mock import MagicMock
from shared.external_utils.cursor_store import CursorStore


def test_get_returns_fallback_when_redis_is_none():
    store = CursorStore()
    store._attempted = True
    store._redis = None
    assert store.get("qb", "invoice", "t1", fallback="2026-01-01") == "2026-01-01"


def test_get_returns_stored_value():
    store = CursorStore()
    mock_r = MagicMock()
    mock_r.get.return_value = "2026-05-01T10:00:00"
    store._redis = mock_r
    store._attempted = True
    assert store.get("qb", "invoice", "t1", fallback="2026-01-01") == "2026-05-01T10:00:00"


def test_get_returns_fallback_when_key_absent():
    store = CursorStore()
    mock_r = MagicMock()
    mock_r.get.return_value = None
    store._redis = mock_r
    store._attempted = True
    assert store.get("qb", "invoice", "t1", fallback="1970-01-01") == "1970-01-01"


def test_set_writes_correct_key():
    store = CursorStore()
    mock_r = MagicMock()
    store._redis = mock_r
    store._attempted = True
    store.set("qb", "invoice", "t1", "2026-06-01T00:00:00")
    mock_r.set.assert_called_once_with(
        "external:cursor:qb:invoice:t1", "2026-06-01T00:00:00"
    )


def test_set_is_noop_when_redis_unavailable():
    store = CursorStore()
    store._attempted = True
    store._redis = None
    store.set("qb", "invoice", "t1", "2026-06-01")  # must not raise


def test_get_returns_fallback_on_redis_exception():
    store = CursorStore()
    mock_r = MagicMock()
    mock_r.get.side_effect = ConnectionError("Redis down")
    store._redis = mock_r
    store._attempted = True
    assert store.get("qb", "invoice", "t1", fallback="safe") == "safe"
