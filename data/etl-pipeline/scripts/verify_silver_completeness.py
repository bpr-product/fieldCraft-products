#!/usr/bin/env python3
"""
verify_silver_completeness.py

Compares exact row counts between each source tenant database and Silver, and
fails when Silver is short.

Why this exists
---------------
Debezium's ``snapshot.mode=initial`` only snapshots when the connector has no
stored offsets. If a slot or offset survives a redeploy, the snapshot is
silently SKIPPED and the connector streams from that point on -- so rows that
existed before the deploy but were never modified afterwards produce no WAL
event and never reach Silver. Everything reports healthy: the connector is
RUNNING, containers are green, no DLQ entries. The only visible symptom is
missing data, discovered days later by a user.

``start_pipeline.sh`` step 0b already drops replication slots on ``--fresh``
specifically to force the snapshot, but that drop fails soft -- it warns and
continues. Nothing confirms the snapshot actually ran.

This check closes that loop. It deliberately verifies the DATA, not the
connector's snapshot log: a connector whose snapshot was skipped in the past
reports SKIPPED forever, so a log-based gate would fail every deploy even after
the gap has been repaired. Row counts describe the state that actually matters
and clear themselves once the data is correct.

Tolerance
---------
Normal CDC lag makes source counts run slightly ahead of Silver -- rows commit
at the source and land in Silver a few seconds later. Small shortfalls are
therefore expected and are not gaps. A shortfall must exceed BOTH the absolute
and percentage tolerances to be reported. With --grace-seconds, candidate gaps
are re-counted after a pause and dropped if Silver caught up.

Exit codes
----------
0  Silver matches source within tolerance
1  one or more real gaps found
2  the check could not run (config/connection failure)

Examples
--------
    python scripts/verify_silver_completeness.py
    python scripts/verify_silver_completeness.py --grace-seconds 30
    python scripts/verify_silver_completeness.py --env prod --tenant tn59695547
    python scripts/verify_silver_completeness.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from shared.env_config.schema import load_environments
    _HAS_ENV_CONFIG = True
except ImportError:  # pragma: no cover - same soft-import as deploy_connectors.py
    _HAS_ENV_CONFIG = False

# Publication membership is the contract for "should be in Silver". Tables the
# connector explicitly excludes are absent by design, not by failure.
_ALWAYS_EXCLUDED = frozenset({"debezium_heartbeat", "flyway_schema_history"})

DEFAULT_TOLERANCE_ROWS = int(os.environ.get("VERIFY_TOLERANCE_ROWS", "10"))
DEFAULT_TOLERANCE_PCT = float(os.environ.get("VERIFY_TOLERANCE_PCT", "0.5"))
DEFAULT_STATEMENT_TIMEOUT_MS = int(os.environ.get("VERIFY_STATEMENT_TIMEOUT_MS", "60000"))


@dataclass
class Gap:
    """One table where Silver holds fewer rows than the source."""

    env: str
    tenant: str
    table: str
    source_rows: int
    silver_rows: int

    @property
    def short(self) -> int:
        return self.source_rows - self.silver_rows

    @property
    def short_pct(self) -> float:
        return (self.short / self.source_rows * 100.0) if self.source_rows else 0.0


@dataclass
class Result:
    gaps: list[Gap] = field(default_factory=list)
    missing_tables: list[tuple[str, str, str]] = field(default_factory=list)
    checked: int = 0
    matched: int = 0
    tenants: int = 0
    errors: list[str] = field(default_factory=list)


def _connect(dsn_kwargs: dict, statement_timeout_ms: int):
    conn = psycopg2.connect(connect_timeout=15, **dsn_kwargs)
    conn.set_session(readonly=True)
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = %s", (statement_timeout_ms,))
    return conn


def _silver_tables(silver_cur) -> set[str]:
    silver_cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'silver'"
    )
    return {r[0] for r in silver_cur.fetchall()}


def _published_tables(src_cur, publication: str) -> list[str]:
    src_cur.execute(
        "SELECT tablename FROM pg_publication_tables WHERE pubname = %s ORDER BY tablename",
        (publication,),
    )
    return [r[0] for r in src_cur.fetchall()]


def _count(cur, sql: str, params: tuple = ()) -> int:
    cur.execute(sql, params)
    row = cur.fetchone()
    return int(row[0]) if row else 0


def _is_gap(source_rows: int, silver_rows: int, tol_rows: int, tol_pct: float) -> bool:
    """A shortfall counts only if it exceeds BOTH tolerances.

    Either alone misbehaves at one end of the scale: a percentage-only rule
    flags 3 missing rows out of 12 as a 25% gap, and a row-count-only rule lets
    a 200k-row table lose 9 rows unnoticed.
    """
    short = source_rows - silver_rows
    if short <= 0:
        return False
    if short <= tol_rows:
        return False
    return (short / source_rows * 100.0) > tol_pct if source_rows else True


def _active_tenants(src_cur) -> list[str]:
    """Active tenant ids from the provider DB, matching deploy_connectors.py."""
    src_cur.execute("SELECT tenant_id FROM public.tenant WHERE status = 'Active' ORDER BY id")
    return [r[0] for r in src_cur.fetchall()]


def check_tenant(
    *,
    env_name: str,
    tenant: str,
    src_conn_kwargs: dict,
    silver_cur,
    silver_tables: set[str],
    publication: str,
    excludes: set[str],
    tol_rows: int,
    tol_pct: float,
    statement_timeout_ms: int,
    result: Result,
) -> None:
    """Count every published table for one tenant and record any shortfalls."""
    conn = _connect({**src_conn_kwargs, "dbname": tenant}, statement_timeout_ms)
    try:
        with conn.cursor() as cur:
            for table in _published_tables(cur, publication):
                if table in excludes or table in _ALWAYS_EXCLUDED:
                    continue
                source_rows = _count(cur, f'SELECT count(*) FROM public."{table}"')
                if table not in silver_tables:
                    if source_rows > 0:
                        result.missing_tables.append((env_name, tenant, table))
                    continue
                silver_rows = _count(
                    silver_cur,
                    f"SELECT count(*) FROM silver.{table} "
                    "WHERE _s_env = %s AND _s_tenantid = %s",
                    (env_name, tenant),
                )
                result.checked += 1
                if _is_gap(source_rows, silver_rows, tol_rows, tol_pct):
                    result.gaps.append(Gap(env_name, tenant, table, source_rows, silver_rows))
                else:
                    result.matched += 1
    finally:
        conn.close()


def recheck(gaps: list[Gap], *, src_conn_kwargs: dict, silver_cur, tol_rows: int,
            tol_pct: float, statement_timeout_ms: int) -> list[Gap]:
    """Re-count candidate gaps so rows that were merely in flight drop out."""
    survivors: list[Gap] = []
    for gap in gaps:
        conn = _connect({**src_conn_kwargs, "dbname": gap.tenant}, statement_timeout_ms)
        try:
            with conn.cursor() as cur:
                source_rows = _count(cur, f'SELECT count(*) FROM public."{gap.table}"')
            silver_rows = _count(
                silver_cur,
                f"SELECT count(*) FROM silver.{gap.table} "
                "WHERE _s_env = %s AND _s_tenantid = %s",
                (gap.env, gap.tenant),
            )
            if _is_gap(source_rows, silver_rows, tol_rows, tol_pct):
                survivors.append(Gap(gap.env, gap.tenant, gap.table, source_rows, silver_rows))
        finally:
            conn.close()
    return survivors


def _report(result: Result, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps({
            "tenants": result.tenants,
            "tables_checked": result.checked,
            "tables_matched": result.matched,
            "gaps": [
                {"env": g.env, "tenant": g.tenant, "table": g.table,
                 "source_rows": g.source_rows, "silver_rows": g.silver_rows,
                 "short": g.short, "short_pct": round(g.short_pct, 2)}
                for g in result.gaps
            ],
            "missing_silver_tables": [
                {"env": e, "tenant": t, "table": tb} for e, t, tb in result.missing_tables
            ],
            "errors": result.errors,
        }, indent=2))
        return

    print("=" * 78)
    print(f"Silver completeness — {result.tenants} tenant(s), "
          f"{result.checked} table(s) checked, {result.matched} matched")
    print("=" * 78)
    for gap in sorted(result.gaps, key=lambda g: -g.short):
        print(f"  GAP  {gap.tenant}/{gap.table:<34} source={gap.source_rows:<9} "
              f"silver={gap.silver_rows:<9} short={gap.short} ({gap.short_pct:.1f}%)")
    for env_name, tenant, table in result.missing_tables:
        print(f"  WARN  no silver table for {tenant}/{table}  (rows exist at source) — "
              "missing DDL, not a CDC gap")
    for err in result.errors:
        print(f"  ERROR  {err}")
    # Errors must suppress the all-clear: a partial fleet failure (some tenants
    # checked, others raised) has no gaps to report, and printing the OK line
    # there told the operator Silver matched source when most of it was never
    # compared at all. The ERROR lines above are the real result in that case.
    if not result.gaps and not result.errors:
        print("  OK — Silver matches source within tolerance")
    elif not result.gaps and result.errors:
        print(f"  NOT VERIFIED — {len(result.errors)} error(s) above; "
              f"only {result.checked} table(s) were actually compared")
    if result.gaps:
        print()
        print("  A gap means rows exist at source but never reached Silver. The usual")
        print("  cause is a skipped Debezium snapshot (snapshot.mode=initial with")
        print("  surviving offsets): CDC carries changes only, so rows never modified")
        print("  after the connector started produce no WAL event and never arrive.")
        print("  Repair by re-snapshotting the affected tables — drop the slot and")
        print("  offsets and redeploy the connector, or run an incremental snapshot —")
        print("  then re-run this check.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", help="Limit to one logical environment")
    parser.add_argument("--tenant", help="Limit to one tenant id")
    parser.add_argument("--grace-seconds", type=int, default=0,
                        help="Pause and re-count candidate gaps, so rows still in "
                             "flight are not reported (default: 0)")
    parser.add_argument("--tolerance-rows", type=int, default=DEFAULT_TOLERANCE_ROWS,
                        help=f"Absolute shortfall allowed (default: {DEFAULT_TOLERANCE_ROWS})")
    parser.add_argument("--tolerance-pct", type=float, default=DEFAULT_TOLERANCE_PCT,
                        help=f"Percentage shortfall allowed (default: {DEFAULT_TOLERANCE_PCT})")
    parser.add_argument("--statement-timeout-ms", type=int,
                        default=DEFAULT_STATEMENT_TIMEOUT_MS,
                        help="Per-query timeout on the source (default: "
                             f"{DEFAULT_STATEMENT_TIMEOUT_MS})")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    parser.add_argument("--strict", action="store_true",
                        help="Also fail when a published table has no Silver table at all. "
                             "Off by default: that is missing DDL (e.g. the monthly "
                             "communication_log_YYYY_MM partitions), not a CDC gap, and it "
                             "would otherwise hold the gate red on a pre-existing condition "
                             "every run.")
    args = parser.parse_args()

    if not _HAS_ENV_CONFIG:
        print("ERROR: shared.env_config.schema unavailable — run inside flink-jobmanager "
              "or set PYTHONPATH=source/pipeline", file=sys.stderr)
        return 2

    try:
        from shared.secret_resolver import get_silver_pg_dsn
        cfg = load_environments()
        silver_conn = psycopg2.connect(get_silver_pg_dsn(), connect_timeout=15)
    except Exception as exc:
        print(f"ERROR: could not initialise check: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    result = Result()
    publication = os.environ.get("PG_PUBLICATION_NAME", "fieldcraft_cdc_publication")

    try:
        with silver_conn.cursor() as silver_cur:
            silver_tables = _silver_tables(silver_cur)

            for env_name, env_spec in cfg.enabled_envs():
                if args.env and env_name != args.env:
                    continue
                src = env_spec.source
                if src is None:
                    continue
                src_conn_kwargs = {
                    "host": src.host, "port": src.port, "user": src.user,
                    "password": src.password, "sslmode": src.ssl_mode,
                }
                excludes = {t.split(".")[-1] for t in cfg.env_table_excludes(env_name)}

                try:
                    provider = _connect(
                        {**src_conn_kwargs, "dbname": src.provider_db},
                        args.statement_timeout_ms,
                    )
                    with provider.cursor() as cur:
                        tenants = _active_tenants(cur)
                    provider.close()
                except Exception as exc:
                    result.errors.append(f"{env_name}: tenant discovery failed: {exc}")
                    continue

                # result.gaps accumulates across every environment this outer
                # loop visits — it is never reset per-env. Recording the
                # length here, before this env's tenant loop appends anything,
                # is what lets the recheck below target ONLY the gaps THIS
                # environment just produced, instead of every gap collected
                # so far (including earlier environments', already resolved
                # during their own turn below).
                _gaps_before_this_env = len(result.gaps)

                for tenant in tenants:
                    if args.tenant and tenant != args.tenant:
                        continue
                    result.tenants += 1
                    try:
                        check_tenant(
                            env_name=env_name, tenant=tenant,
                            src_conn_kwargs=src_conn_kwargs, silver_cur=silver_cur,
                            silver_tables=silver_tables, publication=publication,
                            excludes=excludes, tol_rows=args.tolerance_rows,
                            tol_pct=args.tolerance_pct,
                            statement_timeout_ms=args.statement_timeout_ms,
                            result=result,
                        )
                    except Exception as exc:
                        result.errors.append(f"{env_name}/{tenant}: {type(exc).__name__}: {exc}")

                # Recheck only the slice added during THIS environment's
                # iteration (result.gaps[_gaps_before_this_env:]), not the
                # full accumulated list. src_conn_kwargs below is this env's
                # connection params — passing it the full list would recheck
                # an earlier environment's gaps against the CURRENT env's
                # host/user/password in a multi-env run, either raising a
                # connection/auth error against the wrong host or, in a
                # shared-DSN topology, silently recounting the wrong tenant's
                # data. Confirmed by reading recheck(): it takes one
                # src_conn_kwargs for the whole batch it's given, so it can
                # only ever be correct for gaps from a single environment.
                _new_gaps = result.gaps[_gaps_before_this_env:]
                if _new_gaps and args.grace_seconds > 0:
                    print(f"  {len(_new_gaps)} candidate gap(s) — re-counting in "
                          f"{args.grace_seconds}s to rule out in-flight rows...")
                    time.sleep(args.grace_seconds)
                    result.gaps[_gaps_before_this_env:] = recheck(
                        _new_gaps, src_conn_kwargs=src_conn_kwargs,
                        silver_cur=silver_cur, tol_rows=args.tolerance_rows,
                        tol_pct=args.tolerance_pct,
                        statement_timeout_ms=args.statement_timeout_ms,
                    )
    finally:
        silver_conn.close()

    _report(result, as_json=args.json)

    # Exit-code precedence. rc=1 (confirmed gap) is a HARDER signal than rc=2
    # ("could not fully verify"): start_pipeline.sh turns rc=1 into a gate
    # failure under --verify-completeness, but rc=2 only into a warning. So a
    # run that found BOTH gaps and errors must still return 1 -- returning 2
    # for "any error" would silently downgrade a real gap to a warning.
    if result.gaps:
        return 1
    # Any error at all means the fleet was not fully verified. This previously
    # read `if result.errors and not result.checked`, so it only fired when
    # EVERY tenant/env errored; a partial failure (some tenants checked fine,
    # others raised) left result.checked > 0 and fell through to return 0 --
    # reporting "Silver matches source within tolerance" for a fleet that was
    # never fully checked.
    if result.errors:
        return 2
    return 1 if (args.strict and result.missing_tables) else 0


if __name__ == "__main__":
    sys.exit(main())
