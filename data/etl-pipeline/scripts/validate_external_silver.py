#!/usr/bin/env python3
"""
validate_external_silver.py

Post-ingestion quality checks on all silver.ext_* tables.
Runs SQL checks and reports PASS / FAIL per check per table.
Exits with code 1 if any CRITICAL check fails.

Usage:
    python scripts/validate_external_silver.py
    python scripts/validate_external_silver.py --table ext_eagleview
    python scripts/validate_external_silver.py --fail-fast
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ENV = Path(__file__).parent.parent / ".env.local"
if _ENV.exists():
    for _line in _ENV.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))

sys.path.insert(0, str(Path(__file__).parent.parent))

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"
INFO = "INFO"

CHECKS = [
    (
        "Row count > 0",
        "CRITICAL",
        "SELECT CASE WHEN COUNT(*) = 0 THEN 1 ELSE 0 END FROM {table}",
    ),
    (
        "No NULL _s_external_id",
        "CRITICAL",
        "SELECT COUNT(*) FROM {table} WHERE _s_external_id IS NULL",
    ),
    (
        "No duplicate _s_external_id",
        "CRITICAL",
        "SELECT COUNT(*) FROM ("
        " SELECT _s_external_id FROM {table}"
        " GROUP BY _s_external_id HAVING COUNT(*) > 1"
        ") dupes",
    ),
    (
        "No NULL payload",
        "WARNING",
        "SELECT COUNT(*) FROM {table} WHERE payload IS NULL",
    ),
    (
        "No empty payload",
        "WARNING",
        "SELECT COUNT(*) FROM {table} WHERE payload IN ('{{}}', '', 'null')",
    ),
    (
        "_s_silver_ts not null",
        "WARNING",
        "SELECT COUNT(*) FROM {table} WHERE _s_silver_ts IS NULL",
    ),
    (
        "Recent data (last 7 days)",
        "INFO",
        "SELECT CASE WHEN COUNT(*) = 0 THEN 1 ELSE 0 END"
        " FROM {table}"
        " WHERE _s_silver_ts >= NOW() - INTERVAL '7 days'",
    ),
]


def _get_conn():
    import psycopg2
    return psycopg2.connect(
        host=os.environ["SILVER_PG_HOST"],
        port=int(os.environ.get("SILVER_PG_PORT", 5433)),
        dbname=os.environ["SILVER_PG_DB"],
        user=os.environ.get("SILVER_PG_USER", "silver_writer"),
        password=os.environ.get("SILVER_PG_PASSWORD", ""),
        sslmode=os.environ.get("SILVER_PG_SSL_MODE", "require"),
    )


def _get_ext_tables(cur, filter_table: str | None) -> list[str]:
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'silver'
          AND table_type   = 'BASE TABLE'
          AND table_name   LIKE 'ext_%'
        ORDER BY table_name
    """)
    tables = [f"silver.{r[0]}" for r in cur.fetchall()]
    if filter_table:
        tables = [t for t in tables if filter_table in t]
    return tables


def run_checks(cur, table: str) -> list[tuple[str, str, str, int]]:
    results = []
    for label, severity, sql_tpl in CHECKS:
        sql = sql_tpl.format(table=table)
        try:
            cur.execute(sql)
            fail_count = cur.fetchone()[0]
            if fail_count == 0:
                status = PASS
            elif severity == "CRITICAL":
                status = FAIL
            elif severity == "WARNING":
                status = WARN
            else:
                status = INFO
        except Exception as exc:
            fail_count = -1
            status = FAIL
            label = f"{label} [ERROR: {exc}]"
        results.append((label, severity, status, fail_count))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--table",     help="Only check this table (partial match, e.g. ext_eagleview)")
    parser.add_argument("--fail-fast", action="store_true", help="Stop after first CRITICAL failure")
    args = parser.parse_args()

    conn = _get_conn()
    cur  = conn.cursor()

    tables = _get_ext_tables(cur, args.table)
    if not tables:
        print("No silver.ext_* tables found.")
        conn.close()
        return 0

    print(f"\nExternal Silver Quality Report — {len(tables)} table(s)\n")
    print("=" * 70)

    critical_failures = 0

    for table in tables:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        row_count = cur.fetchone()[0]
        print(f"\n{table}  ({row_count:,} rows)")
        print("-" * 50)

        results = run_checks(cur, table)
        for label, severity, status, fail_count in results:
            count_str = f"  ({fail_count} failing)" if fail_count > 0 else ""
            print(f"  [{status:<4}] [{severity:<8}]  {label}{count_str}")

            if severity == "CRITICAL" and fail_count != 0:
                critical_failures += 1
                if args.fail_fast:
                    print(f"\nFAIL-FAST: critical failure on {table} — {label}")
                    conn.close()
                    return 1

    print("\n" + "=" * 70)

    try:
        cur.execute("""
            SELECT source_name, source_entity, error_type, COUNT(*) AS cnt
            FROM silver._external_dlq
            GROUP BY source_name, source_entity, error_type
            ORDER BY cnt DESC
            LIMIT 20
        """)
        dlq_rows = cur.fetchall()
        if dlq_rows:
            print(f"\nDLQ Summary (silver._external_dlq):")
            print(f"  {'source':<20} {'entity':<20} {'error_type':<25} count")
            print(f"  {'-'*20} {'-'*20} {'-'*25} -----")
            for src, ent, err, cnt in dlq_rows:
                print(f"  {src:<20} {ent:<20} {err:<25} {cnt}")
        else:
            print(f"\nDLQ: 0 records — clean")
    except Exception:
        print("\nDLQ table not yet created — run python silver/ddl/create_silver_tables.py")

    print()
    if critical_failures:
        print(f"FAIL: {critical_failures} CRITICAL check(s) failed.")
        conn.close()
        return 1
    else:
        print(f"PASS: All CRITICAL checks passed.")
        conn.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())
