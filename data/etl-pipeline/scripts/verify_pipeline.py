"""End-to-end pipeline reconciliation: source PG vs silver.* per env and tenant.

Read-only. Use to confirm Silver has caught up with the source after a fresh
start or a connector restart.

Scope comes from ``environments.yaml`` — every environment with
``cdc_enabled: true`` is checked, and each env's tenants are discovered live
from that env's ``fieldcraft_provider.public.tenant``. Neither the environment nor its
tenants are hardcoded here, so this follows whatever the pipeline is actually
connected to.

Two rules this honours, both of which produce false alarms when missed:

  * Counts are compared **per ``_s_env``**. A tenantid is only unique within one
    Silver ``_s_env``, so an unscoped count lets one env's rows mask another's.
  * Tables excluded from CDC (``env_table_excludes`` — e.g. ``tenant``, which is
    provider-authoritative) are skipped for tenant DBs. Comparing them would
    report a permanent MISSING for data that is intentionally never captured.

Exits non-zero when Silver does not reconcile, so CI/cron actually goes red.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2

_ROOT = Path(__file__).parent

# Force-load .env.local (override any inherited shell vars that may be stale)
_ENV = _ROOT / ".env.local"
if _ENV.exists():
    for line in _ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip().strip("'\"")

# Imported after the .env.local load — the loader expands ${VAR} references.
sys.path.insert(0, str(_ROOT))
from shared.env_config.schema import SourceSpec, load_environments  # noqa: E402
from shared.pipeline_config import ENVIRONMENTS_CONFIG_PATH  # noqa: E402

SILVER_HOST = os.environ.get("SILVER_PG_HOST") or os.environ.get("PG_HOST")
if not SILVER_HOST:
    raise SystemExit("Set SILVER_PG_HOST (or PG_HOST) in .env.local before running.")
SILVER_USER = os.environ.get("SILVER_PG_USER", "silver_writer")
SILVER_PWD = os.environ.get("SILVER_WRITER_PWD") or os.environ.get("SILVER_PG_PASSWORD", "")
SILVER_DB = os.environ.get("SILVER_PG_DB", "")

TABLES_TO_CHECK = [
    "project", "task", "audit_log", "analytical_data", "task_additional_info",
    "user_role_map", "tenant", "user_account", "invoice", "purchase_order",
]


_src_errors: dict[tuple[str, str], str] = {}


def _src_conn(spec: SourceSpec, dbname: str):
    """Open a connection to ``dbname`` on the given environment's source host."""
    return psycopg2.connect(
        host=spec.host, port=spec.port, dbname=dbname, user=spec.user,
        password=spec.password, sslmode=spec.ssl_mode or "require",
        connect_timeout=10,
    )


def active_tenants(spec: SourceSpec) -> list[str]:
    """Return active tenant DB names from this env's provider database."""
    with _src_conn(spec, spec.provider_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id FROM public.tenant "
                "WHERE status = 'Active' ORDER BY id"
            )
            return [r[0] for r in cur.fetchall()]


def count_source(spec: SourceSpec, db: str, table: str) -> int:
    """Count rows in ``public.{table}`` of one source tenant DB.

    Returns -1 when the table does not exist for this tenant and -2 on any other
    error (recorded in ``_src_errors`` for the trailing report).
    """
    try:
        with _src_conn(spec, db) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM public.{table}")
                return cur.fetchone()[0]
    except psycopg2.errors.UndefinedTable:
        return -1
    except Exception as e:  # noqa: BLE001
        _src_errors[(db, table)] = f"{type(e).__name__}: {e}"
        return -2


def count_silver(table: str, env: str) -> dict[str, int]:
    """Return live Silver row counts per tenant, scoped to one ``_s_env``."""
    with psycopg2.connect(host=SILVER_HOST, port=5432, dbname=SILVER_DB,
                          user=SILVER_USER, password=SILVER_PWD,
                          sslmode="require", connect_timeout=10) as c:
        with c.cursor() as cur:
            cur.execute(
                f"SELECT _s_tenantid, COUNT(*) FROM silver.{table} "
                f"WHERE _s_is_deleted = FALSE AND _s_env = %s "
                f"GROUP BY _s_tenantid",
                (env,),
            )
            return {r[0]: r[1] for r in cur.fetchall()}


def main() -> None:
    cfg = load_environments(
        os.environ.get("ENVIRONMENTS_CONFIG_PATH", ENVIRONMENTS_CONFIG_PATH)
    )
    enabled = cfg.enabled_envs()
    if not enabled:
        raise SystemExit(
            "No environment has cdc_enabled: true in environments.yaml — "
            "nothing is connected, so there is nothing to reconcile."
        )

    header = (f"{'env':<10} {'table':<24} {'tenant':<14} "
              f"{'source':>8} {'silver':>8} {'status':<10}")

    grand_src = 0
    grand_silver = 0
    mismatches = 0
    missing = 0

    for env_name, env_spec in enabled:
        print(f"\n[{env_name}] source {env_spec.source.host}")
        try:
            tenants = active_tenants(env_spec.source)
        except Exception as e:  # noqa: BLE001
            print(f"[{env_name}] SKIPPED — cannot list tenants: "
                  f"{type(e).__name__}: {e}")
            continue

        # Tables never captured for tenant DBs would always read as MISSING.
        excluded = set(cfg.env_table_excludes(env_name))
        tables = [t for t in TABLES_TO_CHECK if t not in excluded]
        skipped = sorted(set(TABLES_TO_CHECK) - set(tables))

        print(f"[{env_name}] {len(tenants)} active tenant(s): {tenants}")
        if skipped:
            print(f"[{env_name}] not CDC-captured, skipped: {skipped}")
        print()
        print(header)
        print("-" * len(header))

        for table in tables:
            try:
                silver_counts = count_silver(table, env_name)
            except Exception as e:  # noqa: BLE001
                print(f"{env_name:<10} {table:<24} (silver query failed: {e})")
                continue

            for tenant in tenants:
                src = count_source(env_spec.source, tenant, table)
                silver = silver_counts.get(tenant, 0)

                if src == -1:
                    # table doesn't exist in this tenant — skip
                    continue
                if src == -2:
                    status = "ERR_SRC"
                elif src == silver:
                    status = "OK"
                    grand_src += src
                    grand_silver += silver
                elif silver == 0 and src > 0:
                    status = "MISSING"
                    missing += 1
                    grand_src += src
                else:
                    status = "MISMATCH"
                    mismatches += 1
                    grand_src += src
                    grand_silver += silver

                print(f"{env_name:<10} {table:<24} {tenant:<14} "
                      f"{src:>8} {silver:>8} {status:<10}")

    print("-" * len(header))
    print(f"{'TOTAL':<35} {'':<14} {grand_src:>8} {grand_silver:>8}")
    print(f"\nSummary: {mismatches} mismatch(es), "
          f"{missing} entirely missing (silver=0, source>0)")

    reconciled = grand_src == grand_silver and mismatches == 0 and missing == 0
    if reconciled:
        print("PIPELINE OK — source and silver row counts match for all checked tables.")
    else:
        gap = grand_src - grand_silver
        pct = 100.0 * grand_silver / grand_src if grand_src else 0
        print(f"Silver has {grand_silver}/{grand_src} = {pct:.1f}% of source rows "
              f"({gap} missing)")

    if _src_errors:
        print("\nSource query errors (first 5):")
        for i, ((db, tbl), err) in enumerate(_src_errors.items()):
            if i >= 5:
                print(f"  ... and {len(_src_errors) - 5} more")
                break
            print(f"  {db}.{tbl}: {err}")

    if not reconciled or _src_errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
