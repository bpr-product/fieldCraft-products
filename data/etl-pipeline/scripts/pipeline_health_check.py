#!/usr/bin/env python3
"""
pipeline_health_check.py

Verifies all pipeline services are healthy and data is flowing.
Run manually or via the daily digest CronJob.

Exit code 0 = all checks passed.
Exit code 1 = one or more critical checks failed.

Checks:
  1. HTTP services  — Kafka Connect, Schema Registry, Iceberg REST, Flink UI
  2. Flink jobs     — at least one RUNNING job in the cluster
  3. Connectors     — every Debezium connector reports RUNNING
  4. Replication slot lag — WAL disk-fill risk on source PostgreSQL
  5. Silver freshness     — _s_silver_ts lag per entity (SLA: < 30s)
  6. DLQ depth           — any events accumulating in DLQ topics
"""
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.pipeline_config import (
    PG_SCHEMA,
    INTERNAL_SILVER_TABLES,
    SLOT_LAG_WARN_BYTES,
    SLOT_LAG_CRIT_BYTES,
    SILVER_FRESHNESS_WARN_S,
    SILVER_FRESHNESS_CRIT_S,
)

# ── Config ────────────────────────────────────────────────────────────────────

KAFKA_CONNECT_URL    = os.environ.get("KAFKA_CONNECT_URL",       "http://localhost:8083")
SCHEMA_REGISTRY_URL  = os.environ.get("ETL_SCHEMA_REGISTRY_URL", "http://localhost:8082")
ICEBERG_REST_URL     = os.environ.get("ETL_ICEBERG_REST_URI",    "http://localhost:8181")
FLINK_UI_URL         = os.environ.get("FLINK_UI_URL",            "http://localhost:8081")
KAFKA_BOOTSTRAP      = os.environ.get("ETL_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
ETL_ENV              = os.environ.get("ETL_ENV",  "local")

SILVER_PG_HOST     = os.environ.get("SILVER_PG_HOST",     "localhost")
SILVER_PG_PORT     = int(os.environ.get("SILVER_PG_PORT", "5433"))
SILVER_PG_DB       = os.environ.get("SILVER_PG_DB", "")
SILVER_PG_USER     = os.environ.get("SILVER_PG_USER",     "silver_writer")
SILVER_PG_PASSWORD = os.environ.get("SILVER_PG_PASSWORD", "")

SOURCE_PG_HOST     = os.environ.get("POSTGRES_HOST", "")
SOURCE_PG_PORT     = int(os.environ.get("POSTGRES_PORT", "5432"))
SOURCE_PG_USER     = os.environ.get("POSTGRES_USER", "")
SOURCE_PG_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")
SOURCE_PG_DB       = os.environ.get("POSTGRES_DB", "")

# Only exclude internal pipeline control tables from freshness checks.
# These have no _s_silver_ts column — they are not source data tables.
_SKIP_TABLES = INTERNAL_SILVER_TABLES

all_ok = True
warnings = []
criticals = []


def _ok(section: str, msg: str) -> None:
    print(f"  {section:<28} OK    {msg}")


def _warn(section: str, msg: str) -> None:
    global all_ok
    print(f"  {section:<28} WARN  {msg}")
    warnings.append(f"{section}: {msg}")


def _crit(section: str, msg: str) -> None:
    global all_ok
    all_ok = False
    print(f"  {section:<28} CRIT  {msg}")
    criticals.append(f"{section}: {msg}")


def _fail(section: str, msg: str) -> None:
    global all_ok
    all_ok = False
    print(f"  {section:<28} FAIL  {msg}")
    criticals.append(f"{section}: {msg}")


# ── 1. HTTP Service Probes ────────────────────────────────────────────────────

print("=" * 60)
print("  FIELDCRAFT Pipeline Health Check")
print(f"  {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
print("=" * 60)

print("\n[1/6] Service Connectivity")
HTTP_SERVICES = {
    "Kafka Connect":   (KAFKA_CONNECT_URL,   "/connectors"),
    # Schema Registry removed — universal-job design uses dynamic schema
    # discovery (no Avro contracts, no Schema Registry).
    "Iceberg REST":    (ICEBERG_REST_URL,    "/v1/namespaces"),
    "Flink UI":        (FLINK_UI_URL,        "/jobs/overview"),
}
for name, (base, path) in HTTP_SERVICES.items():
    url = base.rstrip("/") + path
    try:
        r = requests.get(url, timeout=5)
        if r.status_code < 400:
            _ok(name, f"HTTP {r.status_code}")
        else:
            _crit(name, f"HTTP {r.status_code}")
    except Exception as exc:
        _fail(name, str(exc))

# ── 2. Flink Job Liveness ─────────────────────────────────────────────────────

print("\n[2/6] Flink Job Liveness")
try:
    r = requests.get(f"{FLINK_UI_URL}/jobs/overview", timeout=5)
    jobs = r.json().get("jobs", [])
    running = [j for j in jobs if j.get("state") == "RUNNING"]
    failed  = [j for j in jobs if j.get("state") in ("FAILED", "FAILING")]
    if running:
        _ok("Flink jobs", f"{len(running)} RUNNING, {len(failed)} FAILED")
    else:
        _crit("Flink jobs", f"0 RUNNING jobs — pipeline is stalled ({len(jobs)} total)")
    for j in failed:
        _crit(f"  job:{j.get('name','?')[:20]}", "state=FAILED")
except Exception as exc:
    _fail("Flink jobs", str(exc))

# ── 3. Debezium Connector Status ──────────────────────────────────────────────

print("\n[3/6] Debezium Connector Status")
try:
    r = requests.get(f"{KAFKA_CONNECT_URL}/connectors?expand=status", timeout=5)
    r.raise_for_status()
    connector_map = r.json()
    if not connector_map:
        _warn("connectors", "none deployed — run scripts/deploy_connectors.py")
    else:
        for connector, info in sorted(connector_map.items()):
            state = info.get("status", {}).get("connector", {}).get("state", "UNKNOWN")
            tasks = info.get("status", {}).get("tasks", [])
            failed_tasks = [t for t in tasks if t.get("state") != "RUNNING"]
            if state == "RUNNING" and not failed_tasks:
                _ok(connector, f"state={state}, tasks={len(tasks)} OK")
            elif state != "RUNNING":
                _crit(connector, f"state={state}")
            else:
                _warn(connector, f"{len(failed_tasks)} task(s) not RUNNING")
except Exception as exc:
    _fail("connectors", str(exc))

# ── 4. Replication Slot Lag ───────────────────────────────────────────────────

print("\n[4/6] WAL Replication Slot Lag")
if not SOURCE_PG_HOST:
    print("  SKIP — POSTGRES_HOST not set")
else:
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=SOURCE_PG_HOST, port=SOURCE_PG_PORT,
            user=SOURCE_PG_USER, password=SOURCE_PG_PASSWORD,
            dbname=SOURCE_PG_DB, sslmode=os.environ.get("POSTGRES_SSL_MODE", "require"), connect_timeout=5,
        )
        conn.autocommit = True
        cur = conn.cursor()
        # OOM/CDC RCA 2026-09-09: deliberately NOT filtered to REPLICATION_SLOTS
        # (that constant only names the provider main/realtime slots). Debezium
        # creates one slot per tenant dynamically (fieldcraft_slot_<env>_<tenant_id>,
        # see deploy_connectors.py) and a static allow-list here would have
        # silently excluded every tenant slot from this check — which is
        # exactly how a tenant slot pinned 10 GB of undetected WAL for ~2 weeks.
        cur.execute("""
            SELECT slot_name, active,
                   pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) AS lag_bytes
            FROM pg_replication_slots
            WHERE slot_type = 'logical'
        """)
        rows = cur.fetchall()
        if not rows:
            _warn("replication slots", "none found — Debezium may not have connected yet")
        for slot_name, active, lag_bytes in rows:
            lag_bytes = lag_bytes or 0
            lag_mb = lag_bytes / (1024 * 1024)
            status = "active" if active else "INACTIVE"
            if lag_bytes >= SLOT_LAG_CRIT_BYTES:
                _crit(slot_name, f"lag={lag_mb:.0f} MB [{status}] — disk fill risk")
            elif lag_bytes >= SLOT_LAG_WARN_BYTES:
                _warn(slot_name, f"lag={lag_mb:.0f} MB [{status}]")
            elif not active:
                _warn(slot_name, f"lag={lag_mb:.0f} MB [INACTIVE] — Debezium down?")
            else:
                _ok(slot_name, f"lag={lag_mb:.1f} MB [{status}]")
        cur.close()
        conn.close()
    except ImportError:
        print("  SKIP — psycopg2 not installed")
    except Exception as exc:
        print(f"  SKIP — cannot reach source PG: {exc}")

# ── 5. Silver Layer Freshness ─────────────────────────────────────────────────

print("\n[5/6] Silver Freshness (SLA: < 30s)")
try:
    import psycopg2
    conn = psycopg2.connect(
        host=SILVER_PG_HOST, port=SILVER_PG_PORT,
        dbname=SILVER_PG_DB, user=SILVER_PG_USER,
        password=SILVER_PG_PASSWORD, connect_timeout=5,
    )
    conn.autocommit = True
    cur = conn.cursor()

    # Discover only parent/standalone Silver tables — excludes partition children
    # (e.g. bom_datamart, bom_dev) because pg_class.relkind='p' parent queries
    # already aggregate all partition data.  Keeps query count ~90 vs ~400 and
    # avoids Azure PG connection timeouts that cause "cursor already closed" errors.
    cur.execute("""
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'silver'
          AND c.relkind IN ('p', 'r')
          AND c.oid NOT IN (SELECT inhrelid FROM pg_inherits)
        ORDER BY c.relname
    """)
    entities = [r[0] for r in cur.fetchall() if r[0] not in _SKIP_TABLES]

    if not entities:
        _warn("Silver tables", "no entity tables found — DDL may not have run")
    else:
        for entity in entities:
            try:
                cur.execute(
                    f"SELECT EXTRACT(EPOCH FROM (NOW() - MAX(_s_silver_ts))), COUNT(*)"
                    f" FROM silver.{entity}"
                )
                row = cur.fetchone()
                lag_s = row[0] if row and row[0] is not None else None
                row_cnt = row[1] if row else 0
                if lag_s is None:
                    _warn(entity, "no rows yet")
                elif lag_s >= SILVER_FRESHNESS_CRIT_S:
                    # "PIPELINE STALLED" only when table is empty — rows present but
                    # lag high means backfilling or a low-write reference table, not a stall.
                    if row_cnt == 0:
                        _crit(entity, f"lag={lag_s:.0f}s — PIPELINE STALLED (no rows)")
                    else:
                        _crit(entity, f"lag={lag_s:.0f}s ({row_cnt} rows — backfilling or low-write)")
                elif lag_s >= SILVER_FRESHNESS_WARN_S:
                    _warn(entity, f"lag={lag_s:.0f}s ({row_cnt} rows)")
                else:
                    _ok(entity, f"lag={lag_s:.1f}s ({row_cnt} rows)")
            except Exception as exc:
                _warn(entity, f"query failed: {exc}")
                # Recover cursor/connection — Azure PG may close idle connections
                # mid-loop; without recovery every subsequent table reports the same error
                try:
                    cur.close()
                except Exception:
                    pass
                try:
                    cur = conn.cursor()
                except Exception:
                    try:
                        conn = psycopg2.connect(
                            host=SILVER_PG_HOST, port=SILVER_PG_PORT,
                            dbname=SILVER_PG_DB, user=SILVER_PG_USER,
                            password=SILVER_PG_PASSWORD, connect_timeout=5,
                        )
                        conn.autocommit = True
                        cur = conn.cursor()
                    except Exception as reconnect_exc:
                        _fail("Silver PG", f"lost connection mid-check: {reconnect_exc}")
                        break

    cur.close()
    conn.close()
except ImportError:
    print("  SKIP — psycopg2 not installed")
except Exception as exc:
    _fail("Silver PG", str(exc))

# ── 6. DLQ Topic Depth ───────────────────────────────────────────────────────

print("\n[6/6] DLQ Topic Depth")
try:
    from confluent_kafka.admin import AdminClient
    from confluent_kafka import Consumer, TopicPartition
    # Use the shared auth helper so the probe authenticates to Azure Event Hubs
    # (SASL_SSL) as well as local PLAINTEXT — a bare bootstrap can't reach Event Hubs.
    from shared.kafka_utils.auth import confluent_kafka_props

    admin    = AdminClient({"bootstrap.servers": KAFKA_BOOTSTRAP, **confluent_kafka_props()})
    metadata = admin.list_topics(timeout=10)
    consumer = Consumer({
        "bootstrap.servers":  KAFKA_BOOTSTRAP,
        "group.id":           "health-check-dlq-probe",
        "auto.offset.reset":  "earliest",
        "enable.auto.commit": "false",
        **confluent_kafka_props(),
    })

    # Discover DLQ topics dynamically from Kafka metadata
    dlq_pattern = f"{ETL_ENV}.{PG_SCHEMA}."
    dlq_topics  = sorted(
        t for t in metadata.topics
        if t.startswith(dlq_pattern) and t.endswith(".dlq")
    )

    if not dlq_topics:
        print("  No DLQ topics exist yet — no events have been routed to DLQ.")
    else:
        any_nonempty = False
        for dlq_t in dlq_topics:
            partitions = metadata.topics[dlq_t].partitions
            tps = [TopicPartition(dlq_t, p) for p in partitions]
            depth = sum(
                max(0, consumer.get_watermark_offsets(tp, timeout=3)[1]
                       - consumer.get_watermark_offsets(tp, timeout=3)[0])
                for tp in tps
            )
            entity = dlq_t.replace(dlq_pattern, "").replace(".dlq", "")
            if depth > 0:
                _warn(f"DLQ:{entity}", f"{depth} messages — inspect {dlq_t}")
                any_nonempty = True
            else:
                _ok(f"DLQ:{entity}", "empty")
        if not any_nonempty:
            print("  All DLQ topics empty.")

    consumer.close()
except ImportError:
    print("  SKIP — confluent-kafka not installed")
except Exception as exc:
    print(f"  FAIL — {exc}")

# ── Summary ───────────────────────────────────────────────────────────────────

print("\n" + "=" * 60)
if criticals:
    print(f"  CRITICAL ({len(criticals)} issues):")
    for c in criticals:
        print(f"    • {c}")
if warnings:
    print(f"  WARNINGS ({len(warnings)}):")
    for w in warnings:
        print(f"    • {w}")
if all_ok and not warnings:
    print("  ALL CHECKS PASSED")
elif all_ok:
    print(f"  PASSED WITH {len(warnings)} WARNING(S)")
else:
    print(f"  FAILED — {len(criticals)} critical issue(s)")
print("=" * 60)

sys.exit(0 if all_ok else 1)
