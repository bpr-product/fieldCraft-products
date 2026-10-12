from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from source.Utils import constants as const
from source.dal.QuickBooksRepository import QuickBooksRepository


def _connection_with_row(payload: dict):
    cursor = MagicMock()
    cursor.fetchall.return_value = [("tn1", "6", payload)]
    cursor.rowcount = 1
    connection = MagicMock()
    connection.cursor.return_value = cursor
    return connection, cursor


def test_accounting_oauth_state_is_consumed_atomically() -> None:
    payload = {
        "state": "state-123",
        "expiresAt": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        "usedAt": None,
    }
    connection, cursor = _connection_with_row(payload)

    with patch(
        "source.dal.QuickBooksRepository.postgres_service_connection",
        return_value=connection,
    ):
        result = QuickBooksRepository().consumeOauthState("state-123")

    assert result == ("tn1", "6", False)
    connection.set_session.assert_called_once_with(autocommit=False)
    assert "FOR UPDATE" in cursor.execute.call_args_list[0].args[0]
    assert cursor.execute.call_count == 2
    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()


def test_used_time_oauth_state_is_idempotent() -> None:
    payload = {
        const.KEY_TIME_STATE: "time-state",
        const.KEY_TIME_STATE_EXPIRES_AT: (
            datetime.now(timezone.utc) + timedelta(minutes=5)
        ).isoformat(),
        const.KEY_TIME_STATE_USED_AT: datetime.now(timezone.utc).isoformat(),
    }
    connection, cursor = _connection_with_row(payload)

    with patch(
        "source.dal.QuickBooksRepository.postgres_service_connection",
        return_value=connection,
    ):
        result = QuickBooksRepository().consumeTimeOauthState("time-state")

    assert result == ("tn1", "6", True)
    assert cursor.execute.call_count == 1
    connection.commit.assert_called_once()


def test_expired_oauth_state_rolls_back() -> None:
    payload = {
        "state": "expired-state",
        "expiresAt": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "usedAt": None,
    }
    connection, _ = _connection_with_row(payload)

    with patch(
        "source.dal.QuickBooksRepository.postgres_service_connection",
        return_value=connection,
    ), pytest.raises(ValueError, match="expired"):
        QuickBooksRepository().consumeOauthState("expired-state")

    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()
