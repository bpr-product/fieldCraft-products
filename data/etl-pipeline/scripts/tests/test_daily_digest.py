"""Unit tests for scripts/daily_digest.py's OOM/CDC RCA 2026-09-09 additions:
replication-slot lag checking and the narrow credential-drift auto-heal.

These cover logic that was previously only syntax-checked, never exercised —
see the RCA docs at repo root and this branch's own follow-up notes. Nothing
here needs a live Kafka Connect / Flink / Postgres; psycopg2, subprocess, and
the alerting calls are all mocked.
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[1] / "daily_digest.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("daily_digest", _MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["daily_digest"] = module
    spec.loader.exec_module(module)
    return module


dd = _load_module()


@pytest.fixture(autouse=True)
def _reset_heal_state():
    """_HEAL_STATE is module-level, persists across calls by design (that's
    the whole point of the cooldown/attempt-cap) -- but tests must not leak
    state into each other."""
    dd._HEAL_STATE.clear()
    yield
    dd._HEAL_STATE.clear()


# ── _AUTH_FAILURE_RE — must match the real signature, must NOT match a slot
#    deadlock (the RCA's other, deliberately-not-auto-healed failure class) ──

@pytest.mark.parametrize("trace", [
    'FATAL: password authentication failed for user "fieldcraft_demo_user"',
    "org.postgresql.util.PSQLException: FATAL: password authentication failed for user \"fieldcraft_demo_user\"",
    "authentication failed for user \"someuser\"",
])
def test_auth_failure_regex_matches_real_signature(trace):
    assert dd._AUTH_FAILURE_RE.search(trace)


@pytest.mark.parametrize("trace", [
    'ERROR: publication "fieldcraft_cdc_publication" does not exist',
    "org.postgresql.util.PSQLException: ERROR: replication slot \"fieldcraft_slot_prod_tn59695547\" is active",
    "",
    "connection refused",
])
def test_auth_failure_regex_does_not_match_other_failures(trace):
    """The slot-deadlock signature (and anything else) must NOT trigger
    auto-heal -- that failure class is deliberately alert-only, since
    recreating a slot discards unrecoverable WAL. If this regex ever starts
    matching the slot-deadlock trace, auto-heal would fire the wrong
    remediation for it."""
    assert not dd._AUTH_FAILURE_RE.search(trace)


# ── _collect_slot_lag() ───────────────────────────────────────────────────

def test_collect_slot_lag_returns_empty_without_source_host():
    with patch.object(dd, "SOURCE_PG_HOST", ""):
        assert dd._collect_slot_lag() == []


def test_collect_slot_lag_parses_all_rows_dynamically():
    """Regression guard for the exact RCA bug: this must NOT filter by a
    fixed slot-name list (that's what let a tenant slot go unmonitored for
    ~2 weeks) -- it must return whatever pg_replication_slots has, however
    many tenants that is."""
    fake_rows = [
        ("fieldcraft_debezium_slot", True, 1_000_000),
        ("fieldcraft_slot_prod_tn59695547", False, 10_737_418_240),  # the 10GB deadlock
        ("fieldcraft_slot_prod_tn88351775", True, 0),
    ]
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = fake_rows
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor

    with patch.object(dd, "SOURCE_PG_HOST", "source.example.com"), \
         patch("psycopg2.connect", return_value=fake_conn):
        slots = dd._collect_slot_lag()

    assert len(slots) == 3
    deadlocked = next(s for s in slots if s["slot_name"] == "fieldcraft_slot_prod_tn59695547")
    assert deadlocked["lag_bytes"] == 10_737_418_240
    assert deadlocked["active"] is False


def test_collect_slot_lag_fails_soft_on_connection_error():
    with patch.object(dd, "SOURCE_PG_HOST", "source.example.com"), \
         patch("psycopg2.connect", side_effect=Exception("connection refused")):
        assert dd._collect_slot_lag() == []


# ── _fire_instant_alerts — slot lag wiring ──────────────────────────────────

def test_fire_instant_alerts_pages_on_slot_lag_past_warn_threshold():
    slots = [{"slot_name": "fieldcraft_slot_prod_tn59695547", "active": False,
              "lag_bytes": dd.SLOT_LAG_WARN_BYTES + 1}]
    with patch.object(dd, "alert_wal_slot_lag") as mock_alert:
        dd._fire_instant_alerts(
            flink={"reachable": True, "running": 1},
            connectors=[], freshness=[], dlq=[], slots=slots,
        )
    mock_alert.assert_called_once()
    args, _ = mock_alert.call_args
    assert args[0] == "fieldcraft_slot_prod_tn59695547"


def test_fire_instant_alerts_silent_below_warn_threshold():
    slots = [{"slot_name": "healthy_slot", "active": True, "lag_bytes": 1024}]
    with patch.object(dd, "alert_wal_slot_lag") as mock_alert:
        dd._fire_instant_alerts(
            flink={"reachable": True, "running": 1},
            connectors=[], freshness=[], dlq=[], slots=slots,
        )
    mock_alert.assert_not_called()


# ── _pipeline_state_key — must change when a slot crosses the threshold, so
#    watch mode actually re-alerts / re-digests on a new lagging slot ───────

def test_pipeline_state_key_changes_when_a_slot_starts_lagging():
    base = dd._pipeline_state_key(
        flink={"running": 1}, connectors=[], freshness=[], dlq=[], slots=[],
    )
    with_lag = dd._pipeline_state_key(
        flink={"running": 1}, connectors=[], freshness=[], dlq=[],
        slots=[{"slot_name": "fieldcraft_slot_prod_tn59695547", "active": False,
                "lag_bytes": dd.SLOT_LAG_WARN_BYTES + 1}],
    )
    assert base != with_lag


# ── _heal_credential_drift() — the narrow, capped auto-heal ────────────────

def _connector(name="fieldcraft-prod-tenant-tn22473627", ok=False, trace=""):
    return {"name": name, "state": "FAILED", "ok": ok, "tasks": 1, "trace": trace}


def test_heal_ignores_healthy_connectors():
    with patch("subprocess.run") as mock_run:
        dd._heal_credential_drift([_connector(ok=True, trace="")])
    mock_run.assert_not_called()
    assert dd._HEAL_STATE == {}


def test_heal_ignores_non_auth_failures():
    """A FAILED connector that ISN'T the auth-failure signature (e.g. the
    slot deadlock) must never trigger subprocess calls -- narrow by design."""
    c = _connector(trace='ERROR: publication "fieldcraft_cdc_publication" does not exist')
    with patch("subprocess.run") as mock_run:
        dd._heal_credential_drift([c])
    mock_run.assert_not_called()
    assert dd._HEAL_STATE == {}


def test_heal_first_attempt_regenerates_and_redeploys():
    c = _connector(trace='FATAL: password authentication failed for user "fieldcraft_demo_user"')
    gen_ok = MagicMock(returncode=0, stderr="")
    deploy_ok = MagicMock(returncode=0, stderr="")

    with patch("subprocess.run", side_effect=[gen_ok, deploy_ok]) as mock_run, \
         patch.object(dd, "warning") as mock_warning, \
         patch.object(dd, "critical") as mock_critical:
        dd._heal_credential_drift([c])

    assert mock_run.call_count == 2
    # First call = generate_connect_secrets.py, second = deploy_connectors.py
    assert "generate_connect_secrets.py" in str(mock_run.call_args_list[0])
    assert "deploy_connectors.py" in str(mock_run.call_args_list[1])
    mock_warning.assert_called_once()
    mock_critical.assert_not_called()
    assert dd._HEAL_STATE[c["name"]]["attempts"] == 1


def test_heal_does_not_redeploy_if_secret_regeneration_fails():
    """If generate_connect_secrets.py itself fails, deploy_connectors.py must
    NOT run against a possibly-stale/partial secrets file."""
    c = _connector(trace="password authentication failed")
    gen_fail = MagicMock(returncode=1, stderr="boom")

    with patch("subprocess.run", return_value=gen_fail) as mock_run, \
         patch.object(dd, "warning"):
        dd._heal_credential_drift([c])

    assert mock_run.call_count == 1  # only the generate step ran


def test_heal_respects_cooldown_between_attempts():
    c = _connector(trace="password authentication failed")
    ok = MagicMock(returncode=0, stderr="")

    t0 = 1_000_000.0
    with patch("subprocess.run", return_value=ok) as mock_run, \
         patch.object(dd, "warning"), patch("time.time", return_value=t0):
        dd._heal_credential_drift([c])
    assert mock_run.call_count == 2  # attempt 1: generate + deploy

    # Same connector, well within the cooldown window -- must NOT re-attempt.
    with patch("subprocess.run", return_value=ok) as mock_run, \
         patch.object(dd, "warning"), \
         patch("time.time", return_value=t0 + dd.HEAL_CREDENTIAL_COOLDOWN_S - 1):
        dd._heal_credential_drift([c])
    mock_run.assert_not_called()
    assert dd._HEAL_STATE[c["name"]]["attempts"] == 1

    # Cooldown elapsed -- attempt 2 proceeds.
    with patch("subprocess.run", return_value=ok) as mock_run, \
         patch.object(dd, "warning"), \
         patch("time.time", return_value=t0 + dd.HEAL_CREDENTIAL_COOLDOWN_S + 1):
        dd._heal_credential_drift([c])
    assert mock_run.call_count == 2
    assert dd._HEAL_STATE[c["name"]]["attempts"] == 2


def test_heal_escalates_to_critical_after_max_attempts_and_stops_retrying():
    """This is the safety property that matters most: auto-heal must NOT
    loop forever the way tn59695547's connector did (18s-forever retries for
    days) if the underlying password itself is simply wrong."""
    c = _connector(trace="password authentication failed")
    ok = MagicMock(returncode=0, stderr="")
    t = [1_000_000.0]

    with patch("subprocess.run", return_value=ok), patch.object(dd, "warning"), \
         patch("time.time", side_effect=lambda: t[0]):
        for _ in range(dd.HEAL_CREDENTIAL_MAX_ATTEMPTS):
            dd._heal_credential_drift([c])
            t[0] += dd.HEAL_CREDENTIAL_COOLDOWN_S + 1

    assert dd._HEAL_STATE[c["name"]]["attempts"] == dd.HEAL_CREDENTIAL_MAX_ATTEMPTS
    assert dd._HEAL_STATE[c["name"]]["exhausted"] is False  # not yet marked -- next call does it

    # One more tick past cooldown: must escalate to CRITICAL, and must NOT
    # attempt another subprocess call (attempts already at the cap).
    with patch("subprocess.run") as mock_run, patch.object(dd, "critical") as mock_critical, \
         patch("time.time", side_effect=lambda: t[0]):
        dd._heal_credential_drift([c])

    mock_run.assert_not_called()
    mock_critical.assert_called_once()
    assert dd._HEAL_STATE[c["name"]]["exhausted"] is True

    # Further ticks must stay silent -- no repeated paging once exhausted.
    with patch("subprocess.run") as mock_run, patch.object(dd, "critical") as mock_critical, \
         patch("time.time", side_effect=lambda: t[0] + 999999):
        dd._heal_credential_drift([c])
    mock_run.assert_not_called()
    mock_critical.assert_not_called()


def test_heal_state_clears_on_recovery():
    """Once a connector reports healthy again, its attempt/cooldown history
    must reset -- otherwise a LATER, unrelated failure inherits a stale
    attempt count and could hit the exhaustion cap prematurely."""
    c = _connector(trace="password authentication failed")
    ok = MagicMock(returncode=0, stderr="")
    with patch("subprocess.run", return_value=ok), patch.object(dd, "warning"):
        dd._heal_credential_drift([c])
    assert c["name"] in dd._HEAL_STATE

    healthy = _connector(name=c["name"], ok=True)
    dd._heal_credential_drift([healthy])
    assert c["name"] not in dd._HEAL_STATE
