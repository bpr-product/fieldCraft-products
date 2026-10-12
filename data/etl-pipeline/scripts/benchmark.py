"""Pipeline throughput snapshot — Kafka lag + Silver row counts per env."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_env_file = os.path.join(os.path.dirname(__file__), "..", ".env.local")
if os.path.exists(_env_file):
    for _ln in open(_env_file).read().splitlines():
        _ln = _ln.strip()
        if not _ln or _ln.startswith("#") or "=" not in _ln:
            continue
        _k, _v = _ln.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip("\"'"))

import time
import psycopg2
from confluent_kafka import Consumer, TopicPartition
from shared.kafka_utils.auth import confluent_kafka_props

BOLD  = "\033[1m"
CYAN  = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"

BOOTSTRAP = os.environ.get("ETL_KAFKA_BOOTSTRAP_SERVERS", "")
ETL_ENV   = os.environ.get("ETL_ENV", "local")
topic     = f"{ETL_ENV}.cdc.events"
props     = confluent_kafka_props()

# ── Section 1: Kafka consumer lag ────────────────────────────────────────────
print(f"\n{BOLD}{'─'*60}{RESET}")
print(f"{BOLD} Kafka Consumer Lag  —  {topic}{RESET}")
print(f"{'─'*60}{RESET}")

c = Consumer({"bootstrap.servers": BOOTSTRAP, "group.id": "_bench_check", "enable.auto.commit": "false", **props})
md = c.list_topics(topic, timeout=10)
partitions = sorted(md.topics[topic].partitions.keys())
hi_total = 0
for p in partitions:
    _, hi = c.get_watermark_offsets(TopicPartition(topic, p), timeout=10)
    hi_total += hi
c.close()

print(f"  Hub events total : {hi_total:>14,}  ({len(partitions)} partition(s))")
print()

for group, label in [("flink-bronze-sql-v1", "Bronze"), ("silver-universal-consumer", "Silver")]:
    c2 = Consumer({"bootstrap.servers": BOOTSTRAP, "group.id": group, "enable.auto.commit": "false", **props})
    committed_list = c2.committed([TopicPartition(topic, p) for p in partitions], timeout=10)
    c2.close()
    comm = sum(tp.offset for tp in committed_list if tp.offset >= 0)
    lag  = max(0, hi_total - comm)
    pct  = int(comm / hi_total * 100) if hi_total > 0 else 0
    status = "caught up" if lag == 0 else "snapshot in progress" if lag > 1000 else "processing"
    color  = GREEN if lag == 0 else YELLOW
    print(f"  {label:<8} committed={comm:>12,}  lag={color}{lag:>12,}{RESET}  {pct:>3}%  [{status}]")

# ── Section 2: Silver row counts per env ─────────────────────────────────────
print(f"\n{BOLD}{'─'*60}{RESET}")
print(f"{BOLD} Silver Row Counts by Environment{RESET}")
print(f"{'─'*60}{RESET}")

try:
    conn = psycopg2.connect(
        host=os.environ["SILVER_PG_HOST"],
        port=int(os.environ.get("SILVER_PG_PORT", 5432)),
        dbname=os.environ.get("SILVER_PG_DB", "fieldcraft_silver"),
        user=os.environ.get("SILVER_PG_USER", "silver_writer"),
        password=os.environ["SILVER_PG_PASSWORD"],
        sslmode=os.environ.get("SILVER_PG_SSL_MODE", "require"),
        connect_timeout=10,
    )
    cur = conn.cursor()

    # Parent tables only (no partition children, no ext_*, no _* infra tables)
    cur.execute("""
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'silver'
          AND c.relkind IN ('p', 'r')
          AND c.relname NOT LIKE 'ext\\_%' ESCAPE '\\'
          AND c.relname NOT LIKE '\\_%' ESCAPE '\\'
          AND c.oid NOT IN (SELECT inhrelid FROM pg_inherits)
        ORDER BY c.relname
    """)
    tables = [r[0] for r in cur.fetchall()]

    # Aggregate across all entities per env
    env_totals: dict[str, int] = {}
    entity_counts: list[tuple[str, str, int]] = []

    for tbl in tables:
        try:
            cur.execute(f'SELECT _s_env, COUNT(*) FROM silver."{tbl}" GROUP BY _s_env')
            for env, cnt in cur.fetchall():
                entity_counts.append((tbl, env, cnt))
                env_totals[env] = env_totals.get(env, 0) + cnt
        except Exception:
            pass

    if env_totals:
        for env, total in sorted(env_totals.items()):
            print(f"\n  {CYAN}{env}{RESET}  —  {total:,} rows total")
            rows_for_env = sorted(
                [(e, c) for (e, ev, c) in entity_counts if ev == env],
                key=lambda x: -x[1]
            )[:15]
            for entity, cnt in rows_for_env:
                print(f"    {entity:<35} {cnt:>10,}")
    else:
        print("  (no Silver data yet)")

    # ── Section 3: DLQ counts ─────────────────────────────────────────────────
    print(f"\n{BOLD}{'─'*60}{RESET}")
    print(f"{BOLD} DLQ Summary{RESET}")
    print(f"{'─'*60}{RESET}")
    try:
        cur.execute("""
            SELECT error_type, COUNT(*) AS n
            FROM silver._dlq_corrections
            GROUP BY error_type
            ORDER BY n DESC
            LIMIT 10
        """)
        rows = cur.fetchall()
        if rows:
            for error_type, n in rows:
                print(f"  {error_type:<35} {n:>8,}")
        else:
            print("  (no DLQ records)")
    except Exception:
        print("  (silver._dlq_corrections not available)")

    cur.close()
    conn.close()

except Exception as exc:
    print(f"  Silver PG unavailable: {exc}")

print(f"\n{'─'*60}\n")
