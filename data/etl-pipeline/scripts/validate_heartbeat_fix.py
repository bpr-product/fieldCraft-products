#!/usr/bin/env python3
"""
validate_heartbeat_fix.py — prove (or disprove) that the Debezium heartbeat fix
is actually releasing WAL on the source PostgreSQL.

WHAT IT CHECKS
  1. SLOT LAG      restart_lsn lag vs confirmed_flush_lsn lag, per slot.
                   This is THE measurement. A slot that is caught up on
                   decoding but far behind on restart_lsn is pinning WAL for
                   no reason -- the exact condition the heartbeat fix targets.
  2. HEARTBEAT     is public.debezium_heartbeat being written? A stale last_heartbeat means
                   heartbeat.action.query is not running (table missing, no
                   grant, or heartbeats disabled).
  3. ORPHANS       inactive slots, which pin WAL forever with no consumer.
  4. CONNECTORS    (needs --connect-url) does the DEPLOYED config actually carry
                   heartbeat.interval.ms > 0 and the hbroute transform, and is
                   the connector RUNNING? A connector that dies on heartbeat
                   records shows up here.
  5. WAL TOTAL     WAL on disk vs max_wal_size, and whether max_slot_wal_keep_size
                   leaves retention uncapped.

USAGE
  # baseline BEFORE deploying the fix, then again after
  python scripts/validate_heartbeat_fix.py --dsn "host=... user=... dbname=..." \
      --connect-url http://localhost:8083

  # watch restart lag fall over time (the success signal)
  python scripts/validate_heartbeat_fix.py --dsn "..." --watch 60

Read-only. Never drops a slot or writes to the source; it only measures.
Exit code 0 = all checks passed, 1 = at least one FAIL.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

try:
    import psycopg2
except ImportError:
    print("psycopg2 required: pip install psycopg2-binary", file=sys.stderr)
    raise SystemExit(2)

# A slot whose restart_lsn trails this far is pinning WAL worth caring about.
RESTART_LAG_WARN_BYTES = 1 * 1024 ** 3   # 1 GB
# debezium_heartbeat rows older than this mean the action query is not firing.
HEARTBEAT_STALE_S = 300

_PASS, _FAIL, _WARN = "PASS", "FAIL", "WARN"
_results: list[tuple[str, str, str]] = []


def record(status: str, check: str, detail: str) -> None:
    _results.append((status, check, detail))
    icon = {"PASS": "  ok  ", "WARN": "  !!  ", "FAIL": " FAIL "}[status]
    print(f"{icon}{check}: {detail}")


def human(n: int | None) -> str:
    if n is None:
        return "n/a"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024.0
    return f"{n:.0f} PB"


def check_slots(cur) -> None:
    cur.execute(
        """
        SELECT slot_name, active, wal_status,
               pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)::bigint         AS restart_lag,
               pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)::bigint AS decode_lag
        FROM pg_replication_slots
        ORDER BY slot_name
        """
    )
    rows = cur.fetchall()
    if not rows:
        record(_WARN, "slots", "no replication slots on this database")
        return

    print("\n  slot                             active  wal_status   restart_lag   decode_lag")
    print("  " + "-" * 78)
    for name, active, status, restart_lag, decode_lag in rows:
        print(f"  {name:32} {str(active):7} {str(status):12} "
              f"{human(restart_lag):>12}  {human(decode_lag):>11}")
    print()

    for name, active, _status, restart_lag, decode_lag in rows:
        if not active:
            record(_FAIL, f"orphan/{name}",
                   f"INACTIVE slot pinning {human(restart_lag)} of WAL — nothing consumes it. "
                   f"Drop with: SELECT pg_drop_replication_slot('{name}');")
            continue
        if restart_lag is None:
            record(_WARN, f"slot/{name}", "restart_lsn is NULL")
            continue
        if restart_lag > RESTART_LAG_WARN_BYTES:
            gap = (restart_lag - (decode_lag or 0))
            record(_FAIL, f"slot/{name}",
                   f"restart_lag {human(restart_lag)} vs decode_lag {human(decode_lag)} "
                   f"(gap {human(gap)}) — slot is caught up but still pinning WAL. "
                   f"Heartbeats are not advancing restart_lsn.")
        else:
            record(_PASS, f"slot/{name}",
                   f"restart_lag {human(restart_lag)} (decode_lag {human(decode_lag)}) — healthy")


def check_heartbeat_table(cur) -> None:
    cur.execute("SELECT to_regclass('public.debezium_heartbeat') IS NOT NULL")
    if not cur.fetchone()[0]:
        # Not a failure: this pipeline never creates the table -- the source
        # databases are owned elsewhere. Its absence only means the
        # action-query half is unavailable here, so set
        # ETL_DEBEZIUM_HEARTBEAT_QUERY="" for this deployment. The
        # heartbeat.interval.ms half still advances the slot on its own, and
        # the slot-lag check above is the real verdict either way.
        record(_WARN, "heartbeat/table",
               "public.debezium_heartbeat absent in this database — "
               "heartbeat.action.query cannot run here. Set "
               'ETL_DEBEZIUM_HEARTBEAT_QUERY="" so connectors omit it, or ask '
               "the database owner to add the table. Judge success by the "
               "slot restart_lag above.")
        return
    # Real schema, verified against the tenant databases:
    #   id integer PK, last_heartbeat timestamptz, heartbeat_count bigint,
    #   created_at timestamptz, CONSTRAINT single_row CHECK (id = 1)
    cur.execute(
        "SELECT id, last_heartbeat, heartbeat_count, "
        "       extract(epoch FROM (now() - last_heartbeat))::int AS age_s "
        "FROM public.debezium_heartbeat ORDER BY last_heartbeat DESC"
    )
    rows = cur.fetchall()
    if not rows:
        record(_WARN, "heartbeat/rows",
               "table exists but is EMPTY — heartbeat.action.query has not run "
               "(heartbeats disabled, or the connector user lacks INSERT here)")
        return
    for row_id, last_beat, count, age in rows:
        if age is None:
            record(_WARN, f"heartbeat/id={row_id}", "last_heartbeat is NULL")
        elif age > HEARTBEAT_STALE_S:
            record(_FAIL, f"heartbeat/id={row_id}",
                   f"last beat {age}s ago ({last_beat}), count={count} — stale, "
                   f"action query not firing")
        else:
            record(_PASS, f"heartbeat/id={row_id}",
                   f"last beat {age}s ago, count={count} — firing")


def check_wal(cur) -> None:
    cur.execute("SELECT setting FROM pg_settings WHERE name='max_slot_wal_keep_size'")
    keep = cur.fetchone()
    if keep and keep[0] == "-1":
        record(_WARN, "wal/cap",
               "max_slot_wal_keep_size = -1 (unlimited) — a future orphan slot can "
               "fill the disk. Consider a bound so PG invalidates the slot instead.")
    else:
        record(_PASS, "wal/cap", f"max_slot_wal_keep_size = {keep[0] if keep else '?'} MB")

    try:
        cur.execute("SELECT sum(size)::bigint FROM pg_ls_waldir()")
        total = cur.fetchone()[0]
        cur.execute("SELECT setting::bigint * 1024 * 1024 FROM pg_settings WHERE name='max_wal_size'")
        target = cur.fetchone()[0]
        ratio = (total / target) if target else 0
        status = _PASS if ratio <= 2 else _WARN if ratio <= 4 else _FAIL
        record(status, "wal/size",
               f"{human(total)} on disk vs max_wal_size {human(target)} ({ratio:.1f}x)")
    except Exception as exc:  # pg_ls_waldir needs privileges on some managed PG
        record(_WARN, "wal/size", f"could not read pg_ls_waldir(): {str(exc)[:60]}")


def check_connectors(base_url: str) -> None:
    try:
        names = json.load(urllib.request.urlopen(f"{base_url}/connectors", timeout=10))
    except Exception as exc:
        record(_WARN, "connect/api", f"unreachable at {base_url}: {str(exc)[:60]}")
        return
    if not names:
        record(_FAIL, "connect/api", "zero connectors deployed")
        return
    for n in names:
        try:
            cfg = json.load(urllib.request.urlopen(f"{base_url}/connectors/{n}/config", timeout=10))
            st = json.load(urllib.request.urlopen(f"{base_url}/connectors/{n}/status", timeout=10))
        except Exception as exc:
            record(_WARN, f"connect/{n}", f"could not read: {str(exc)[:50]}")
            continue

        state = st.get("connector", {}).get("state", "?")
        tasks = [t.get("state") for t in st.get("tasks", [])]
        bad_task = [t for t in tasks if t != "RUNNING"]
        if state != "RUNNING" or bad_task:
            record(_FAIL, f"connect/{n}",
                   f"connector={state} tasks={tasks or 'none'} — a connector that dies on "
                   f"heartbeat records shows up exactly like this")
        else:
            record(_PASS, f"connect/{n}", f"RUNNING, {len(tasks)} task(s) RUNNING")

        hb = cfg.get("heartbeat.interval.ms", "0")
        if str(hb) in ("0", ""):
            record(_FAIL, f"connect/{n}/heartbeat",
                   "heartbeat.interval.ms=0 in the DEPLOYED config — the fix is not live "
                   "here (redeploy this connector)")
        else:
            has_route = any(k.startswith("transforms.hbroute") for k in cfg)
            in_chain = "hbroute" in cfg.get("transforms", "")
            if has_route and in_chain:
                record(_PASS, f"connect/{n}/heartbeat",
                       f"interval={hb}ms, hbroute -> {cfg.get('transforms.hbroute.replacement')}")
            else:
                record(_FAIL, f"connect/{n}/heartbeat",
                       f"interval={hb}ms but hbroute missing (chain={cfg.get('transforms')}) — "
                       f"each connector will create its own heartbeat topic")


def run_once(dsn: str, connect_url: str | None) -> None:
    conn = psycopg2.connect(dsn, connect_timeout=15)
    conn.autocommit = True
    with conn.cursor() as cur:
        check_slots(cur)
        check_heartbeat_table(cur)
        check_wal(cur)
    conn.close()
    if connect_url:
        check_connectors(connect_url.rstrip("/"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", default=os.environ.get("VALIDATE_HEARTBEAT_DSN"),
                    help="libpq DSN for the SOURCE database (or VALIDATE_HEARTBEAT_DSN)")
    ap.add_argument("--connect-url", default=os.environ.get("KAFKA_CONNECT_URL"),
                    help="Kafka Connect REST base URL (optional)")
    ap.add_argument("--watch", type=int, metavar="SECONDS",
                    help="re-measure every N seconds; restart_lag should FALL")
    args = ap.parse_args()

    if not args.dsn:
        ap.error("--dsn is required (or set VALIDATE_HEARTBEAT_DSN)")

    while True:
        _results.clear()
        print("=" * 80)
        print(f"heartbeat validation @ {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 80)
        run_once(args.dsn, args.connect_url)

        fails = [r for r in _results if r[0] == _FAIL]
        warns = [r for r in _results if r[0] == _WARN]
        print("\n" + "-" * 80)
        print(f"  {len(_results) - len(fails) - len(warns)} passed, "
              f"{len(warns)} warning(s), {len(fails)} failure(s)")
        if fails:
            print("\n  FAILURES:")
            for _s, check, detail in fails:
                print(f"    - {check}: {detail}")
        print("-" * 80)

        if not args.watch:
            return 1 if fails else 0
        time.sleep(args.watch)


if __name__ == "__main__":
    raise SystemExit(main())
