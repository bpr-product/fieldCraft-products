"""Unit tests for shape_doc functions in loaders not yet covered.

All tests are pure — no Mongo, no PostgreSQL.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


class _StubEmbedder:
    name = "stub"
    version = "0.0.0"
    dim = 4


_TS = datetime(2024, 6, 1, tzinfo=timezone.utc)
