#!/usr/bin/env python3
"""
daily_digest.py

Generates a structured daily health report for the FIELDCRAFT pipeline and
sends it to MicroFieldCrafts. Designed to run as a Kubernetes CronJob
at 07:00 every morning.

Usage:
    python scripts/daily_digest.py

Environment variables required:
    ALERT_TEAMS_WEBHOOK_URL   — Teams Incoming Webhook URL
    SILVER_PG_HOST/PORT/DB/USER/PASSWORD
    POSTGRES_HOST/PORT/USER/PASSWORD/DB — source PG (replication-slot lag check)
    ETL_KAFKA_BOOTSTRAP_SERVERS
    ETL_ICEBERG_REST_URI
    KAFKA_CONNECT_URL
    FLINK_UI_URL

OOM/CDC RCA 2026-09-09: this is the ONLY continuously-running check (launched
via docker-compose as its own pipeline-monitor sidecar, see
docker/datapipeline/docker-compose.{prod,nonprod}.yml) — the one-shot
pipeline_health_check.py only runs once at the tail of start_pipeline.sh.
Replication-slot lag was NOT part of this watch loop before this fix, so the
exact failure that pinned 10 GB of WAL on the prod source DB for ~2 weeks
(a deadlocked tenant slot, see the RCA doc at repo root) went completely
unalerted the whole time. _collect_slot_lag() below queries
pg_replication_slots directly rather than checking a fixed list of names, so
it covers every tenant slot Debezium ever creates (fieldcraft_slot_<env>_<tenant>)
without needing this file updated per tenant onboarded.

2026-09-09 follow-up: also auto-heals ONE specific, well-understood failure —
a connector whose task trace matches a stale-password signature. Confirmed
live on prod: this was NOT a rotated-vs-not-rotated password mismatch (the
password was never actually expired) — 4 of 6 connectors correctly stored
their password as a Kafka Connect FileConfigProvider reference
(${file:/opt/kafka/connect-secrets.properties:KEY}), which always resolves
the CURRENT secret file and can never go stale, but 2 tenant connectors had
the password baked into their stored config as a plaintext literal (an
artifact of however they were originally deployed), so they kept the OLD
value forever once the canonical password changed. On detection this
regenerates connect-secrets.properties, then runs
`python scripts/deploy_connectors.py`, which rebuilds EVERY connector's full
config from scratch via _build_tenant_connector() — that function always
emits the ${file:...} reference form, never a literal — so this both fixes
the immediate auth failure and permanently converts a hardcoded-literal
connector back to the reference form (a no-op PUT for the other, already-
correct connectors). Restarting the connector alone would NOT have fixed
this — a restart re-applies the SAME stored (still-literal, still-stale)
config, so it would keep failing forever; the fix has to replace the config,
not just restart against it. Deliberately narrow: pattern-matches the
auth-failure signature specifically, capped at HEAL_CREDENTIAL_MAX_ATTEMPTS
with a cooldown between tries, and escalates to a CRITICAL page instead of
retrying forever once exhausted — this is NOT a general "restart anything
that's FAILED" loop. A slot deadlock (a different, data-integrity-sensitive
failure — see the RCA) does NOT match this pattern and is deliberately left
alert-only; recreating a slot discards WAL that can't be recovered and must
stay a human decision.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.pipeline_config import (
    PG_SCHEMA,
    INTERNAL_SILVER_TABLES,
    SILVER_FRESHNESS_WARN_S,
    SILVER_FRESHNESS_CRIT_S,
    DIGEST_INTERVAL_S,
    SLOT_LAG_WARN_BYTES,
    SLOT_LAG_CRIT_BYTES,
)

# Windows: force UTF-8 output so emoji don't crash cp1252 terminal
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Load .env.local automatically so the script works from any shell
_ENV_FILE = Path(__file__).parent.parent / ".env.local"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))

from shared.alerting.notify import (
    ALERT_ENV, TEAMS_WEBHOOK,
    alert_flink_no_jobs,
    alert_silver_stale_grouped,
    alert_dlq_grouped,
    alert_connectors_down_grouped,
    notify_pipeline_started,
    notify_pipeline_stopped,
    alert_wal_slot_lag,
    critical,
    warning,
)

KAFKA_CONNECT_URL   = os.environ.get("KAFKA_CONNECT_URL",        "http://localhost:8083")
SCHEMA_REGISTRY_URL = os.environ.get("ETL_SCHEMA_REGISTRY_URL",  "http://localhost:8082")
ICEBERG_REST_URL    = os.environ.get("ETL_ICEBERG_REST_URI",     "http://localhost:8181")
FLINK_UI_URL        = os.environ.get("FLINK_UI_URL",             "http://localhost:8081")
KAFKA_BOOTSTRAP     = os.environ.get("ETL_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
ETL_ENV_VAR         = os.environ.get("ETL_ENV",  "local")
PG_SCHEMA           = os.environ.get("ETL_PG_SCHEMA", "public")

SILVER_PG_HOST      = os.environ.get("SILVER_PG_HOST",     "localhost")
SILVER_PG_PORT      = int(os.environ.get("SILVER_PG_PORT", "5433"))
SILVER_PG_DB        = os.environ.get("SILVER_PG_DB", "")
SILVER_PG_USER      = os.environ.get("SILVER_PG_USER",     "silver_writer")
SILVER_PG_PASSWORD  = os.environ.get("SILVER_PG_PASSWORD", "")

# Source PG — same server the provider connector and every tenant connector
# read from (tenants are separate databases on this one server, not separate
# hosts; see deploy_connectors.py's _get_active_tenants). pg_replication_slots
# is a cluster-wide catalog view, so one connection here sees every slot on
# the server regardless of which database it belongs to.
SOURCE_PG_HOST      = os.environ.get("POSTGRES_HOST", "")
SOURCE_PG_PORT      = int(os.environ.get("POSTGRES_PORT", "5432"))
SOURCE_PG_USER      = os.environ.get("POSTGRES_USER", "")
SOURCE_PG_PASSWORD  = os.environ.get("POSTGRES_PASSWORD", "")
SOURCE_PG_DB        = os.environ.get("POSTGRES_DB", "")
SOURCE_PG_SSL_MODE  = os.environ.get("POSTGRES_SSL_MODE", "require")

# ── Credential-drift auto-heal (narrow, capped, see module docstring) ────────
HEAL_CREDENTIAL_MAX_ATTEMPTS = int(os.environ.get("HEAL_CREDENTIAL_MAX_ATTEMPTS", "3"))
HEAL_CREDENTIAL_COOLDOWN_S   = int(os.environ.get("HEAL_CREDENTIAL_COOLDOWN_S", "600"))
_AUTH_FAILURE_RE = re.compile(
    r"password authentication failed|authentication failed for user|FATAL:.*password",
    re.IGNORECASE,
)
# connector_name -> {"attempts": int, "last_ts": float, "exhausted": bool}
_HEAL_STATE: dict[str, dict] = {}

_SKIP_TABLES        = INTERNAL_SILVER_TABLES
SILVER_CRIT_LAG_S   = SILVER_FRESHNESS_CRIT_S
SILVER_WARN_LAG_S   = SILVER_FRESHNESS_WARN_S


# ── Data collection helpers ───────────────────────────────────────────────────

def _http_get(url: str) -> Optional[dict]:
    try:
        r = requests.get(url, timeout=5)
        return r.json() if r.status_code < 400 else None
    except Exception:
        return None


def _collect_services() -> list[dict]:
    checks = [
        ("Kafka Connect",   KAFKA_CONNECT_URL,   "/connectors"),
        ("Schema Registry", SCHEMA_REGISTRY_URL, "/subjects"),
        ("Iceberg REST",    ICEBERG_REST_URL,    "/v1/namespaces"),
        ("Flink UI",        FLINK_UI_URL,        "/jobs/overview"),
    ]
    results = []
    for name, base, path in checks:
        try:
            r = requests.get(base.rstrip("/") + path, timeout=5)
            results.append({"name": name, "ok": r.status_code < 400, "detail": f"HTTP {r.status_code}"})
        except Exception as exc:
            results.append({"name": name, "ok": False, "detail": str(exc)[:60]})
    return results


def _collect_flink() -> dict:
    data = _http_get(f"{FLINK_UI_URL}/jobs/overview")
    if data is None:
        return {"running": 0, "failed": 0, "total": 0, "reachable": False}
    jobs    = data.get("jobs", [])
    running = [j for j in jobs if j.get("state") == "RUNNING"]
    failed  = [j for j in jobs if j.get("state") in ("FAILED", "FAILING")]
    return {"running": len(running), "failed": len(failed), "total": len(jobs), "reachable": True}


def _discover_connectors() -> list[str]:
    """Return all connector names registered in Kafka Connect."""
    try:
        r = requests.get(f"{KAFKA_CONNECT_URL}/connectors", timeout=5)
        if r.status_code < 400:
            return r.json()
    except Exception:
        pass
    return []


def _collect_connectors() -> list[dict]:
    connectors = _discover_connectors()
    if not connectors:
        return [{"name": "kafka-connect", "state": "UNREACHABLE", "ok": False, "tasks": 0}]
    results = []
    for connector in sorted(connectors):
        try:
            r = requests.get(f"{KAFKA_CONNECT_URL}/connectors/{connector}/status", timeout=5)
            if r.status_code == 404:
                results.append({"name": connector, "state": "NOT_DEPLOYED", "ok": False, "tasks": 0})
                continue
            data  = r.json()
            state = data.get("connector", {}).get("state", "UNKNOWN")
            tasks = data.get("tasks", [])
            tasks_ok = all(t.get("state") == "RUNNING" for t in tasks) if tasks else True
            ok    = state == "RUNNING" and tasks_ok
            # Trace text from the connector itself or any failed task -- used
            # by _heal_credential_drift() below to recognize a stale-password
            # signature specifically, not any arbitrary failure.
            trace = data.get("connector", {}).get("trace", "") or " ".join(
                t.get("trace", "") for t in tasks if t.get("trace")
            )
            results.append({"name": connector, "state": state, "ok": ok, "tasks": len(tasks),
                            "trace": trace})
        except Exception as exc:
            results.append({"name": connector, "state": "UNREACHABLE", "ok": False,
                            "tasks": 0, "error": str(exc)[:60]})
    return results


def _collect_silver_freshness() -> list[dict]:
    results = []
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=SILVER_PG_HOST, port=SILVER_PG_PORT,
            dbname=SILVER_PG_DB, user=SILVER_PG_USER,
            password=SILVER_PG_PASSWORD, connect_timeout=5,
        )
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'silver' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        entities = [r[0] for r in cur.fetchall() if r[0] not in _SKIP_TABLES]
        for entity in entities:
            try:
                cur.execute(
                    f"SELECT EXTRACT(EPOCH FROM (NOW() - MAX(_s_silver_ts))), COUNT(*) FROM silver.{entity}"
                )
                row = cur.fetchone()
                lag_s     = row[0] if row and row[0] is not None else None
                row_count = int(row[1]) if row else 0
                results.append({
                    "entity":    entity,
                    "lag_s":     lag_s,
                    "row_count": row_count,
                    "ok":        lag_s is not None and lag_s < SILVER_WARN_LAG_S,
                })
            except Exception:
                results.append({"entity": entity, "lag_s": None, "row_count": 0, "ok": False})
        cur.close()
        conn.close()
    except Exception:
        pass
    return results


def _collect_dlq() -> list[dict]:
    results = []
    try:
        from confluent_kafka.admin import AdminClient
        from confluent_kafka import Consumer, TopicPartition
        admin    = AdminClient({"bootstrap.servers": KAFKA_BOOTSTRAP})
        metadata = admin.list_topics(timeout=10)
        consumer = Consumer({
            "bootstrap.servers":  KAFKA_BOOTSTRAP,
            "group.id":           "daily-digest-dlq-probe",
            "auto.offset.reset":  "earliest",
            "enable.auto.commit": "false",
        })
        prefix = f"{ETL_ENV_VAR}.{PG_SCHEMA}."
        dlq_topics = sorted(t for t in metadata.topics if t.startswith(prefix) and t.endswith(".dlq"))
        for dlq_t in dlq_topics:
            tps   = [TopicPartition(dlq_t, p) for p in metadata.topics[dlq_t].partitions]
            depth = sum(
                max(0, consumer.get_watermark_offsets(tp, timeout=3)[1]
                       - consumer.get_watermark_offsets(tp, timeout=3)[0])
                for tp in tps
            )
            entity = dlq_t.replace(prefix, "").replace(".dlq", "")
            results.append({"entity": entity, "depth": depth, "topic": dlq_t})
        consumer.close()
    except Exception:
        pass
    return results


def _heal_credential_drift(connectors: list[dict]) -> None:
    """Auto-heal ONE narrow failure signature: a connector whose task trace
    shows a Postgres auth failure. See the module docstring for why this is
    scoped this tightly (a slot deadlock must NOT match and must NOT auto-heal).
    """
    now = time.time()
    for c in connectors:
        if c.get("ok"):
            _HEAL_STATE.pop(c["name"], None)  # recovered — clear attempt/cooldown history
            continue
        trace = c.get("trace", "") or ""
        if not _AUTH_FAILURE_RE.search(trace):
            continue

        name  = c["name"]
        state = _HEAL_STATE.setdefault(name, {"attempts": 0, "last_ts": 0.0, "exhausted": False})

        if state["exhausted"]:
            continue  # already paged CRITICAL below on the attempt that exhausted it
        if now - state["last_ts"] < HEAL_CREDENTIAL_COOLDOWN_S:
            continue  # cooldown — give the last attempt time to take effect
        if state["attempts"] >= HEAL_CREDENTIAL_MAX_ATTEMPTS:
            state["exhausted"] = True
            critical(
                title=f"Credential auto-heal exhausted for {name} — manual fix required",
                body=(
                    f"**WHAT HAPPENED**\n"
                    f"Connector `{name}` still fails Postgres auth after "
                    f"{HEAL_CREDENTIAL_MAX_ATTEMPTS} automatic regenerate+redeploy attempts.\n\n"
                    f"**WHAT AUTO-HEAL ALREADY TRIED**\n"
                    f"  1. Regenerated connect-secrets.properties from the current source of truth\n"
                    f"  2. python scripts/deploy_connectors.py (full config redeploy)\n"
                    f"  ...{HEAL_CREDENTIAL_MAX_ATTEMPTS} times, {HEAL_CREDENTIAL_COOLDOWN_S}s apart\n\n"
                    f"**LIKELY CAUSE** — the regenerated password itself is wrong, i.e. the\n"
                    f"canonical source (.env / Key Vault) has not actually been updated to the\n"
                    f"real current DB password — auto-heal cannot fix that, only a human can.\n\n"
                    f"**HOW TO FIX**\n"
                    f"  Step 1 — verify the real DB password vs POSTGRES_PASSWORD / the per-env\n"
                    f"           *_PG_PASSWORD in src/datapipeline/.env\n"
                    f"  Step 2 — bash scripts/generate_connect_secrets.py, then\n"
                    f"           python scripts/deploy_connectors.py"
                ),
                details={"connector": name, "attempts": str(state["attempts"])},
                dedup_key=f"credential-heal-exhausted-{name}-{ALERT_ENV}",
            )
            continue

        state["attempts"] += 1
        state["last_ts"]   = now
        print(f"Auth-failure signature on {name} — auto-heal attempt {state['attempts']}/{HEAL_CREDENTIAL_MAX_ATTEMPTS}")

        try:
            gen = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("generate_connect_secrets.py"))],
                env={**os.environ, "CONNECT_SECRETS_OUT": "/opt/kafka/connect-secrets.properties"},
                capture_output=True, text=True, timeout=60,
            )
            gen_ok = gen.returncode == 0
            if not gen_ok:
                print(f"generate_connect_secrets.py failed: {gen.stderr[-500:]}")
        except Exception as exc:
            gen_ok = False
            print(f"generate_connect_secrets.py raised: {exc}")

        # Deliberately a full deploy_connectors.py run, NOT a
        # POST /connectors/{name}/restart. A restart re-applies the
        # connector's EXISTING stored config unchanged — if that config has
        # the password baked in as a literal (confirmed live on prod: 2 of 6
        # tenant connectors had this, the other 4 correctly used a
        # ${file:...} FileConfigProvider reference), a restart just retries
        # the SAME stale literal and fails again forever. deploy_connectors.py
        # rebuilds every connector's config from scratch via
        # _build_tenant_connector(), which ALWAYS emits the ${file:...} form,
        # never a literal — so this PUT both fixes the immediate auth failure
        # AND permanently converts a hardcoded-literal connector back to the
        # reference form in one action (idempotent for the other, already-
        # correct connectors — this is the exact command notify.py's own
        # alert_connectors_down_grouped() body already recommends as the
        # standard manual fix, not a new untested action).
        deploy_ok = False
        if gen_ok:
            try:
                dep = subprocess.run(
                    [sys.executable, str(Path(__file__).with_name("deploy_connectors.py"))],
                    env=os.environ.copy(),
                    capture_output=True, text=True, timeout=120,
                )
                deploy_ok = dep.returncode == 0
                if not deploy_ok:
                    print(f"deploy_connectors.py failed: {dep.stderr[-500:]}")
            except Exception as exc:
                print(f"deploy_connectors.py raised: {exc}")

        warning(
            title=f"Auto-heal attempt {state['attempts']}/{HEAL_CREDENTIAL_MAX_ATTEMPTS} for {name} "
                  f"— stale-credential signature detected",
            body=(
                f"**WHAT HAPPENED**\n"
                f"`{name}` is failing Postgres auth (matches the exact signature that took down "
                f"2 tenant connectors for ~2 weeks — see the OOM/CDC RCA at repo root).\n\n"
                f"**AUTO-HEAL ACTION TAKEN**\n"
                f"  1. Regenerate connect-secrets.properties: {'OK' if gen_ok else 'FAILED'}\n"
                f"  2. python scripts/deploy_connectors.py (full config redeploy, converts any\n"
                f"     hardcoded-literal password back to a ${{file:...}} reference): "
                f"{'OK' if deploy_ok else 'FAILED / SKIPPED'}\n\n"
                f"**WHAT THIS DOES NOT DO**\n"
                f"Does not touch any replication slot and does not restart the kafka-connect worker "
                f"process. It DOES re-apply config to every enabled connector (idempotent PUT — a "
                f"no-op for connectors whose config is already correct), same as running "
                f"deploy_connectors.py manually.\n\n"
                f"Will re-check in ~60s. If the connector doesn't recover after "
                f"{HEAL_CREDENTIAL_MAX_ATTEMPTS} attempts this escalates to a CRITICAL page."
            ),
            details={"connector": name, "attempt": str(state["attempts"]),
                      "regenerate_ok": str(gen_ok), "redeploy_ok": str(deploy_ok)},
            dedup_key=f"credential-heal-attempt-{name}-{state['attempts']}-{ALERT_ENV}",
        )


def _collect_slot_lag() -> list[dict]:
    """Every logical replication slot on the source PG server, with lag.

    Deliberately does NOT filter by a known slot-name list — Debezium creates
    one slot per tenant (fieldcraft_slot_<env>_<tenant_id>) dynamically as tenants
    onboard, plus the provider main/realtime slots, and a hardcoded list goes
    stale the moment a new tenant is added. See the OOM/CDC RCA 2026-09-09 at
    repo root: the deadlocked tn59695547 slot pinned 10 GB of WAL for ~2 weeks
    with nothing watching it.
    """
    if not SOURCE_PG_HOST:
        return []
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=SOURCE_PG_HOST, port=SOURCE_PG_PORT,
            user=SOURCE_PG_USER, password=SOURCE_PG_PASSWORD,
            dbname=SOURCE_PG_DB, sslmode=SOURCE_PG_SSL_MODE, connect_timeout=5,
        )
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("""
            SELECT slot_name, active,
                   pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) AS lag_bytes
            FROM pg_replication_slots
            WHERE slot_type = 'logical'
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [
            {"slot_name": name, "active": active, "lag_bytes": lag_bytes or 0}
            for name, active, lag_bytes in rows
        ]
    except Exception as exc:
        print(f"Slot-lag check failed: {exc}")
        return []


# ── Report formatting ─────────────────────────────────────────────────────────

def _status_icon(ok: bool) -> str:
    return "✅" if ok else "❌"


def _fmt_lag(lag_s: float) -> str:
    if lag_s >= 3600:
        return f"{lag_s / 3600:.1f}h"
    if lag_s >= 60:
        return f"{lag_s / 60:.0f}m"
    return f"{lag_s:.0f}s"


def _section_services(services: list, flink: dict, connectors: list) -> list[str]:
    svc_line = "  " + "  ".join(
        f"{_status_icon(s['ok'])} {s['name']}" for s in services
    )
    if flink["reachable"]:
        flink_line = (
            f"  {_status_icon(flink['running'] > 0)} Flink Jobs: "
            f"{flink['running']} RUNNING | {flink['failed']} FAILED | {flink['total']} total"
        )
    else:
        flink_line = "  ❌ Flink UI unreachable"

    conn_ok      = [c for c in connectors if c["ok"]]
    conn_failed  = [c for c in connectors if not c["ok"]]
    conn_summary = f"  {_status_icon(not conn_failed)} Connectors: {len(conn_ok)} OK"
    if conn_failed:
        conn_summary += f" | {len(conn_failed)} FAILED: " + ", ".join(c["name"] for c in conn_failed[:3])
        if len(conn_failed) > 3:
            conn_summary += f" (+{len(conn_failed) - 3} more)"

    return ["**SERVICES & JOBS**", svc_line, flink_line, conn_summary]


def _section_freshness(freshness: list) -> list[str]:
    if not freshness:
        return ["**SILVER DATA**", "  ⚠️ Could not reach Silver PostgreSQL"]

    healthy = [f for f in freshness if f["ok"]]
    no_data = [f for f in freshness if f["lag_s"] is None]
    stale   = [f for f in freshness if f["lag_s"] is not None and not f["ok"]]
    worst5  = sorted(stale, key=lambda x: x["lag_s"], reverse=True)[:5]

    lines = ["**SILVER DATA**"]
    lines.append(
        f"  {_status_icon(not stale)} "
        f"{len(healthy)} healthy  |  {len(stale)} stale  |  {len(no_data)} empty  |  {len(freshness)} total tables"
    )
    if worst5:
        lines.append("  Worst stale tables:")
        for f in worst5:
            lines.append(
                f"    • {f['entity']:<30} {_fmt_lag(f['lag_s'])} ago  ({f['row_count']:,} rows)"
            )
    return lines


def _section_dlq(dlq: list) -> list[str]:
    if not dlq:
        return ["**DLQ**", "  ℹ️ No DLQ topics found"]
    nonempty = [d for d in dlq if d["depth"] > 0]
    if nonempty:
        lines = ["**DLQ**"]
        for d in nonempty:
            lines.append(f"  ⚠️ {d['entity']}: {d['depth']} events quarantined")
        return lines
    return ["**DLQ**", "  ✅ All DLQ topics empty"]


def _overall_status(flink: dict, connectors: list, freshness: list, dlq: list) -> list[str]:
    issues = []
    if flink["reachable"] and flink["running"] == 0:
        issues.append("🚨 No Flink jobs running — pipeline is stalled")
    failed_conn = [c for c in connectors if not c["ok"]]
    if failed_conn:
        issues.append(f"🚨 {len(failed_conn)} connector(s) failed — CDC events not flowing")
    stale = [f for f in freshness if f["lag_s"] is not None and f["lag_s"] >= SILVER_CRIT_LAG_S]
    if stale:
        issues.append(f"⚠️ {len(stale)} Silver tables stale (>{SILVER_CRIT_LAG_S}s lag)")
    dlq_total = sum(d["depth"] for d in dlq) if dlq else 0
    if dlq_total > 0:
        issues.append(f"⚠️ {dlq_total} events in DLQ — run python scripts/dlq_consumer.py")

    if issues:
        return ["**STATUS: ACTION REQUIRED**"] + [f"  {i}" for i in issues]
    return ["**STATUS: ✅ Pipeline healthy — no action required**"]


def _section_slots(slots: list) -> list[str]:
    if not slots:
        return ["**Replication slots**", "  ℹ️ None found / source PG unreachable"]
    lagging = [s for s in slots if s["lag_bytes"] >= SLOT_LAG_WARN_BYTES]
    if lagging:
        lines = ["**Replication slots**"]
        for s in sorted(lagging, key=lambda s: -s["lag_bytes"]):
            mb = s["lag_bytes"] / (1024 * 1024)
            crit = s["lag_bytes"] >= SLOT_LAG_CRIT_BYTES
            icon = "🚨" if crit else "⚠️"
            active = "active" if s["active"] else "INACTIVE"
            lines.append(f"  {icon} {s['slot_name']}: {mb:.0f} MB behind [{active}]")
        return lines
    return ["**Replication slots**", f"  ✅ All {len(slots)} slot(s) within lag threshold"]


def _build_report(services, flink, connectors, freshness, dlq, slots) -> str:
    ts      = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    divider = "─" * 40

    sections = [
        [f"📊 **FIELDCRAFT Pipeline Health Report**", f"{ts}  |  Environment: `{ALERT_ENV}`"],
        _section_services(services, flink, connectors),
        _section_freshness(freshness),
        _section_dlq(dlq),
        _section_slots(slots),
        _overall_status(flink, connectors, freshness, dlq),
    ]

    return f"\n{divider}\n".join("\n".join(s) for s in sections)


def _send_teams_digest(report: str) -> None:
    if not TEAMS_WEBHOOK:
        print("ALERT_TEAMS_WEBHOOK_URL not set — printing digest to stdout only")
        return
    ssl_verify = os.environ.get("ALERT_SSL_VERIFY", "true").lower() != "false"
    payload = {"text": report}
    try:
        r = requests.post(TEAMS_WEBHOOK, json=payload, timeout=10, verify=ssl_verify)
        r.raise_for_status()
        print("Daily digest sent to Teams.")
    except Exception as exc:
        print(f"Failed to send Teams digest: {exc}")


# ── Instant alerts for critical findings — grouped, one alert per issue ───────

def _fire_instant_alerts(flink, connectors, freshness, dlq, slots) -> None:
    # 1. Flink — one alert if no jobs running
    if flink["reachable"] and flink["running"] == 0:
        alert_flink_no_jobs()

    # 2. Connectors — one grouped alert for all failed connectors
    failed_connectors = [c for c in connectors if not c["ok"]]
    if failed_connectors:
        alert_connectors_down_grouped(failed_connectors)

    # 3. Silver freshness — one grouped alert for all stale tables
    stale = [
        {"entity": f["entity"], "lag_s": f["lag_s"], "row_count": f.get("row_count", 0)}
        for f in freshness
        if f["lag_s"] is not None and f["lag_s"] >= SILVER_CRIT_LAG_S
    ]
    if stale:
        alert_silver_stale_grouped(stale)

    # 4. DLQ — one grouped alert for all non-empty DLQ topics
    nonempty_dlq = [d for d in dlq if d["depth"] > 0]
    if nonempty_dlq:
        alert_dlq_grouped(nonempty_dlq)

    # 5. Replication slot lag — one alert per slot past the WARN threshold.
    # A slot deadlocked against a stale catalog LSN behaves identically here
    # to a slot that's merely fallen behind: lag_bytes keeps growing because
    # nothing ever advances restart_lsn. Either way, this is a disk-fill risk
    # on the SOURCE production database, so it pages regardless of which of
    # the two it turns out to be.
    for slot in slots:
        if slot["lag_bytes"] >= SLOT_LAG_WARN_BYTES:
            alert_wal_slot_lag(slot["slot_name"], slot["lag_bytes"] / (1024 * 1024))


def _collect_all() -> tuple:
    services   = _collect_services()
    flink      = _collect_flink()
    connectors = _collect_connectors()
    freshness  = _collect_silver_freshness()
    dlq        = _collect_dlq()
    slots      = _collect_slot_lag()
    return services, flink, connectors, freshness, dlq, slots


def _pipeline_state_key(flink, connectors, freshness, dlq, slots) -> str:
    """Fingerprint of current pipeline state — used to suppress duplicate alerts."""
    failed_connectors = sorted(c["name"] for c in connectors if not c["ok"])
    stale_count       = sum(1 for f in freshness if f["lag_s"] is not None and f["lag_s"] >= SILVER_CRIT_LAG_S)
    dlq_total         = sum(d["depth"] for d in dlq)
    flink_running     = flink.get("running", 0)
    lagging_slots     = sorted(s["slot_name"] for s in slots if s["lag_bytes"] >= SLOT_LAG_WARN_BYTES)
    return (
        f"flink={flink_running}|conn={','.join(failed_connectors)}|stale={stale_count}"
        f"|dlq={dlq_total}|slots={','.join(lagging_slots)}"
    )


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="FIELDCRAFT Pipeline health digest")
    parser.add_argument("--watch",           action="store_true", help="Run continuously, alerting on state changes")
    parser.add_argument("--interval",        type=int, default=60, help="Check interval in seconds (default: 60)")
    parser.add_argument("--notify-started",  type=int, nargs=2, metavar=("FLINK_JOBS", "CONNECTORS"),
                        help="Send pipeline-started notification with job/connector counts")
    parser.add_argument("--notify-stopped",  action="store_true", help="Send pipeline-stopped notification")
    args = parser.parse_args()

    if args.notify_started is not None:
        flink_jobs, connectors_count = args.notify_started
        notify_pipeline_started(flink_jobs=flink_jobs, connectors=connectors_count)
        return

    if args.notify_stopped:
        notify_pipeline_stopped()
        return

    if not args.watch:
        # Single run
        print("Collecting pipeline metrics...")
        services, flink, connectors, freshness, dlq, slots = _collect_all()
        report = _build_report(services, flink, connectors, freshness, dlq, slots)
        print("\n" + "=" * 60)
        print(report)
        print("=" * 60 + "\n")
        _fire_instant_alerts(flink, connectors, freshness, dlq, slots)
        _send_teams_digest(report)
        return

    # Watch mode — runs continuously, only alerts when state changes
    print(f"Watch mode started — checking every {args.interval}s. Press Ctrl+C to stop.\n")
    last_state_key   = None
    last_digest_time = 0.0
    DIGEST_INTERVAL  = DIGEST_INTERVAL_S

    while True:
        try:
            services, flink, connectors, freshness, dlq, slots = _collect_all()
            # Runs every tick regardless of state_changed below -- it has its
            # own internal cooldown/attempt-cap pacing (see the function), and
            # needs to see recovery on a tick where the rest of the pipeline
            # state key didn't otherwise change.
            _heal_credential_drift(connectors)
            state_key = _pipeline_state_key(flink, connectors, freshness, dlq, slots)
            now       = time.time()
            ts        = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

            state_changed   = state_key != last_state_key
            digest_due      = (now - last_digest_time) >= DIGEST_INTERVAL

            if state_changed:
                print(f"[{ts}] State changed — firing alerts")
                _fire_instant_alerts(flink, connectors, freshness, dlq, slots)
                last_state_key = state_key

            if state_changed or digest_due:
                report = _build_report(services, flink, connectors, freshness, dlq, slots)
                print("\n" + "=" * 60)
                print(report)
                print("=" * 60 + "\n")
                _send_teams_digest(report)
                last_digest_time = now
            else:
                print(f"[{ts}] Pipeline state unchanged — no alert sent")

        except KeyboardInterrupt:
            print("\nWatch mode stopped.")
            break
        except Exception as exc:
            print(f"[ERROR] Check failed: {exc}")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
