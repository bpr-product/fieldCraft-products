"""Unit tests for iter_silver_rows in BPE loaders — mocked psycopg2."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _make_cursor(rows):
    cur = MagicMock()
    cur.__iter__ = MagicMock(return_value=iter(rows))
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    return cur


def _make_conn(rows):
    conn = MagicMock()
    cur = _make_cursor(rows)
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn, cur


