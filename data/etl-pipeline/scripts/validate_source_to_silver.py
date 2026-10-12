#!/usr/bin/env python3
"""
validate_source_to_silver.py — Source-to-Silver row-count completeness check.

For each enabled environment's source database(s), compares live row counts
against the corresponding Silver `<table>_<env>` partition (excluding rows
soft-deleted via `_s_is_deleted`). Surfaces tables where Silver is missing
rows that exist at source right now -- the CDC-completeness question, not
the ghost-row question `reconcile_silver_snapshot.py` already covers.

This does NOT catch async lag by design (a table 30s behind will show a
small diff every time) -- treat small diffs on high-churn tables (timelog,
resource_schedule, audit_log) as expected; treat large/persistent diffs
on low-churn tables as real gaps worth investigating (connector paused,
table hit an exclude list, DLQ'd rows, schema drift blocking merge).

Usage:
  python scripts/validate_source_to_silver.py                 # staging (default)
  python scripts/validate_source_to_silver.py --env staging
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ENV_FILE = _HERE.parent / ".env.local"


def _load_env():
    env = {}
    if _ENV_FILE.exists():
        with open(_ENV_FILE, "rb") as f:
            for raw in f:
                line = raw.decode("utf-8", "replace").rstrip("\r\n").strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    return env


def _psql(host, port, db, user, pw, sql, timeout=30):
    e = dict(os.environ)
    e["PGPASSWORD"] = pw
    r = subprocess.run(
        ["psql", f"host={host} port={port} dbname={db} user={user} sslmode=require",
         "-t", "-A", "-F", "\t", "-c", sql],
        capture_output=True, text=True, env=e, timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.replace(pw, "***"))
    return r.stdout.strip()


# Framework/internal tables never expected to land in Silver 1:1.
_SKIP_TABLES = {"debezium_heartbeat", "flyway_schema_history", "invoice_id", "tenant"}


def _source_table_counts(host, port, db, user, pw):
    tables = _psql(host, port, db, user, pw,
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1;")
    counts = {}
    for t in tables.splitlines():
        t = t.strip()
        if not t or t in _SKIP_TABLES:
            continue
        try:
            n = _psql(host, port, db, user, pw, f'SELECT count(*) FROM public."{t}";')
            counts[t] = int(n)
        except Exception as exc:
            counts[t] = None
            print(f"  WARN: could not count {db}.{t}: {exc}", file=sys.stderr)
    return counts


def _silver_table_exists(host, port, db, user, pw, table):
    r = _psql(host, port, db, user, pw,
               f"SELECT to_regclass('silver.\"{table}\"') IS NOT NULL;")
    return r.strip() == "t"


def _silver_count(host, port, db, user, pw, table, env_name):
    if _silver_table_exists(host, port, db, user, pw, table):
        try:
            n = _psql(host, port, db, user, pw,
                       f'SELECT count(*) FROM silver."{table}" WHERE _s_is_deleted = FALSE;')
            return int(n)
        except Exception as exc:
            print(f"  WARN: could not count silver.{table}: {exc}", file=sys.stderr)
            return None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="staging")
    args = ap.parse_args()

    env = _load_env()
    prefix = args.env.upper()
    host = env.get(f"{prefix}_PG_HOST")
    user = env.get(f"{prefix}_PG_USER")
    pw = env.get(f"{prefix}_PG_PASSWORD")
    if not all([host, user, pw]):
        print(f"Missing {prefix}_PG_HOST/USER/PASSWORD in .env.local", file=sys.stderr)
        sys.exit(1)
    port = "5432"

    # Discover source databases for this env from the deployed Kafka Connect
    # connectors rather than hardcoding db names.
    kc_url = env.get("KAFKA_CONNECT_URL", "http://localhost:8083")
    names = subprocess.run(["curl", "-s", f"{kc_url}/connectors"], capture_output=True, text=True).stdout
    import json
    connector_names = [n for n in json.loads(names) if args.env in n and "silver-cdc" not in n and "realtime" not in n]

    source_dbs = set()
    for name in connector_names:
        cfg = json.loads(subprocess.run(["curl", "-s", f"{kc_url}/connectors/{name}/config"],
                                         capture_output=True, text=True).stdout)
        db = cfg.get("database.dbname")
        h = cfg.get("database.hostname")
        u = cfg.get("database.user")
        if db and h:
            source_dbs.add((h, u or user, db))

    if not source_dbs:
        print(f"No connectors found matching env '{args.env}' -- falling back to {prefix}_PG_DBNAME")
        source_dbs = {(host, user, env.get(f"{prefix}_PG_DBNAME", ""))}

    silver_host = env.get("SILVER_PG_HOST")
    silver_port = env.get("SILVER_PG_PORT", "5432")
    silver_db = env.get("SILVER_PG_DB", "fieldcraft_silver")
    silver_user = env.get("SILVER_PG_USER")
    silver_pw = env.get("SILVER_PG_PASSWORD")

    print(f"Source DBs for env='{args.env}': {[db for _, _, db in source_dbs]}")
    print(f"Silver: {silver_host}/{silver_db}\n")

    source_totals = {}
    for h, u, db in source_dbs:
        print(f"-- counting {db} --")
        pw_for_db = pw  # same admin creds for all source DBs on this server
        for table, n in _source_table_counts(h, port, db, u, pw_for_db).items():
            if n is None:
                continue
            source_totals[table] = source_totals.get(table, 0) + n

    print("\n-- comparing against Silver --")
    rows = []
    for table, src_n in sorted(source_totals.items()):
        silver_n = _silver_count(silver_host, silver_port, silver_db, silver_user, silver_pw, table, args.env)
        diff = None if silver_n is None else src_n - silver_n
        rows.append((table, src_n, silver_n, diff))

    print(f"\n{'table':40s} {'source':>10s} {'silver':>10s} {'diff':>10s}")
    missing_flag = False
    for table, src_n, silver_n, diff in rows:
        marker = ""
        if silver_n is None:
            marker = "  <-- NO SILVER TABLE"
            missing_flag = True
        elif diff and diff > 0:
            marker = "  <-- MISSING IN SILVER"
            missing_flag = True
        elif diff and diff < 0:
            marker = "  <-- SILVER HAS MORE (dup/env overlap?)"
        print(f"{table:40s} {src_n:>10d} {str(silver_n):>10s} {str(diff):>10s}{marker}")

    sys.exit(1 if missing_flag else 0)


if __name__ == "__main__":
    main()
