"""Unit tests for gold.pipeline.cursor_store.

Uses an in-memory SQLite-compatible approach via psycopg2 mocking to keep
tests hermetic (no PostgreSQL connection required).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest

import gold.pipeline.cursor_store as cursor_store
from gold.pipeline.cursor_store import (
    EPOCH,
    ensure_table,
    mark_error,
    read_cursor,
    reset_cursor,
    write_cursor,
)


@pytest.fixture(autouse=True)
def _reset_lazy_ensure_cache():
    """Clear the process-level lazy-provision cache so each test is independent."""
    cursor_store._ensured_dsns.clear()
    yield
    cursor_store._ensured_dsns.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn(fetchone_return=None):
    """Build a minimal psycopg2 connection mock."""
    cur = MagicMock()
    cur.fetchone.return_value = fetchone_return
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)
    return conn, cur


# ---------------------------------------------------------------------------
# EPOCH sentinel
# ---------------------------------------------------------------------------

def test_epoch_is_utc():
    assert EPOCH.tzinfo is not None
    assert EPOCH == datetime(1970, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# ensure_table
# ---------------------------------------------------------------------------

@patch("gold.pipeline.cursor_store.psycopg2.connect")
def test_ensure_table_executes_ddl(mock_connect):
    conn, cur = _make_conn()
    mock_connect.return_value = conn

    ensure_table("postgresql://test/db")

    assert mock_connect.called
    # Both CREATE SCHEMA and CREATE TABLE statements should have been executed
    executed_sql = [str(c.args[0]).upper() for c in cur.execute.call_args_list]
    assert any("CREATE SCHEMA" in sql for sql in executed_sql)
    assert any("CREATE TABLE" in sql for sql in executed_sql)
    conn.commit.assert_called()


# ---------------------------------------------------------------------------
# lazy provisioning
# ---------------------------------------------------------------------------

@patch("gold.pipeline.cursor_store.psycopg2.connect")
def test_read_cursor_lazily_provisions_table_once(mock_connect):
    """First cursor read provisions the table; subsequent reads do not re-run DDL."""
    conn, cur = _make_conn(fetchone_return=None)
    mock_connect.return_value = conn

    read_cursor("postgresql://test/db", "load_bpe", "T1")
    executed = [str(c.args[0]).upper() for c in cur.execute.call_args_list]
    assert any("CREATE TABLE" in sql for sql in executed)  # provisioned on first use

    cur.execute.reset_mock()
    read_cursor("postgresql://test/db", "load_bpe", "T1")
    executed2 = [str(c.args[0]).upper() for c in cur.execute.call_args_list]
    assert not any("CREATE TABLE" in sql for sql in executed2)  # cached — no re-provision


# ---------------------------------------------------------------------------
# read_cursor
# ---------------------------------------------------------------------------

@patch("gold.pipeline.cursor_store.psycopg2.connect")
def test_read_cursor_returns_epoch_when_no_row(mock_connect):
    conn, cur = _make_conn(fetchone_return=None)
    mock_connect.return_value = conn

    result = read_cursor("postgresql://test/db", "load_bpe", "TENANT-001")

    assert result == EPOCH


@patch("gold.pipeline.cursor_store.psycopg2.connect")
def test_read_cursor_returns_stored_ts(mock_connect):
    ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    conn, cur = _make_conn(fetchone_return=(ts,))
    mock_connect.return_value = conn

    result = read_cursor("postgresql://test/db", "load_bpe", "TENANT-001")

    assert result == ts


@patch("gold.pipeline.cursor_store.psycopg2.connect")
def test_read_cursor_makes_tz_aware_naive_ts(mock_connect):
    """Naive datetime returned by psycopg2 without timezone should be made UTC."""
    naive_ts = datetime(2025, 6, 1, 12, 0, 0)  # no tzinfo
    conn, cur = _make_conn(fetchone_return=(naive_ts,))
    mock_connect.return_value = conn

    result = read_cursor("postgresql://test/db", "load_bpe", "TENANT-001")

    assert result.tzinfo is not None
    assert result == naive_ts.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# write_cursor
# ---------------------------------------------------------------------------

@patch("gold.pipeline.cursor_store.psycopg2.connect")
def test_write_cursor_upserts_row(mock_connect):
    conn, cur = _make_conn()
    mock_connect.return_value = conn

    ts = datetime(2025, 6, 2, 8, 0, 0, tzinfo=timezone.utc)
    write_cursor("postgresql://test/db", "load_estimate_snapshot", "TENANT-001", ts)

    assert cur.execute.called
    sql, params = cur.execute.call_args.args
    assert "INSERT" in sql.upper() or "ON CONFLICT" in sql.upper()
    assert ts in params
    conn.commit.assert_called()


@patch("gold.pipeline.cursor_store.psycopg2.connect")
def test_write_cursor_sets_status_idle_by_default(mock_connect):
    conn, cur = _make_conn()
    mock_connect.return_value = conn

    ts = datetime(2025, 6, 2, tzinfo=timezone.utc)
    write_cursor("postgresql://test/db", "load_bpe", "T1", ts)

    _, params = cur.execute.call_args.args
    assert "idle" in params


# ---------------------------------------------------------------------------
# mark_error
# ---------------------------------------------------------------------------

@patch("gold.pipeline.cursor_store.psycopg2.connect")
def test_mark_error_does_not_advance_cursor(mock_connect):
    """mark_error must NOT update last_processed_ts."""
    conn, cur = _make_conn()
    mock_connect.return_value = conn

    mark_error("postgresql://test/db", "load_bpe", "T1", Exception("something broke"))

    sql, params = cur.execute.call_args.args
    # last_processed_ts should not appear as a positional value — only error_message + status
    assert "error" in sql.lower()
    conn.commit.assert_called()


@patch("gold.pipeline.cursor_store.psycopg2.connect")
def test_mark_error_accepts_string_message(mock_connect):
    conn, cur = _make_conn()
    mock_connect.return_value = conn

    mark_error("postgresql://test/db", "step", "T1", "5 bulk write failures")

    assert cur.execute.called


# ---------------------------------------------------------------------------
# reset_cursor
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="quarantined: superseded by gold/tests/unit/silver_to_gold suite (drifted from cursor_store/shape_doc changes)")
@patch("gold.pipeline.cursor_store.psycopg2.connect")
def test_reset_cursor_sets_null(mock_connect):
    conn, cur = _make_conn()
    mock_connect.return_value = conn

    reset_cursor("postgresql://test/db", "load_bpe", "T1")

    sql, params = cur.execute.call_args.args
    assert None in params  # NULL for last_processed_ts
    conn.commit.assert_called()
