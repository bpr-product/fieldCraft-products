"""
Regression tests for the DLQ watcher's Path A topic wiring (dlq_watcher/main.py).

Path A consolidated internal CDC onto a single `{env}.cdc.events` hub and the DLQ
onto a single `{env}.dlq` hub (Event Hubs Standard 10-hub cap). These tests pin
the watcher's topic functions and subscription pattern to that topology so they
can't silently drift back to the pre-Path-A per-entity shape — which made the
watcher miss the unified DLQ and replay to dead per-entity topics.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import dlq_watcher.main as m


def test_source_topic_is_consolidated_cdc_hub():
    # Replay target MUST be the hub Silver actually consumes — not local.public.<e>.
    assert m._source_topic("project") == f"{m.ETL_ENV}.cdc.events"
    assert m._source_topic("invoice") == f"{m.ETL_ENV}.cdc.events"


def test_dlq_topic_is_unified():
    # DEFERRED republish target MUST be the unified DLQ hub.
    assert m._dlq_topic("task") == f"{m.ETL_ENV}.dlq"


def test_no_parking_topic_function():
    # Parking is now audit-only (recorded in silver._dlq_corrections); there must
    # be NO parking-hub function, to keep the DLQ family within the 10-hub cap.
    assert not hasattr(m, "_parking_topic")


def test_subscription_matches_unified_dlq():
    """The watcher must now subscribe to the unified `{env}.dlq` hub (the bug:
    the old pattern `^{env}\\..+\\.dlq$` did NOT match `{env}.dlq`)."""
    pat = re.compile(m.DLQ_TOPIC_PATTERN)
    assert pat.match(f"{m.ETL_ENV}.dlq")          # unified entity DLQ — previously MISSED
    assert pat.match(f"{m.ETL_ENV}.silver.dlq")   # malformed / no-entity DLQ


def test_subscription_excludes_terminal_and_data_topics():
    pat = re.compile(m.DLQ_TOPIC_PATTERN)
    # Parking is terminal — must NOT be re-consumed.
    assert not pat.match(f"{m.ETL_ENV}.dlq.parking")
    # The CDC data hub must never be treated as a DLQ.
    assert not pat.match(f"{m.ETL_ENV}.cdc.events")
