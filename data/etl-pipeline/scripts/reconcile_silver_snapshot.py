#!/usr/bin/env python3
"""
reconcile_silver_snapshot.py — Post-snapshot Silver reconciliation.

After a re-snapshot, Silver may contain ghost rows: rows that were deleted from
the source while the connector was down (and WAL was recycled), so the DELETE
event never arrived. The re-snapshot only sends currently-existing rows; it does
not signal what was removed. This script closes that gap.

For each enabled environment + entity + tenant:
  1. Fetch all PKs currently in the source DB.
  2. Fetch all non-deleted Silver PKs for that (env, entity, tenant).
  3. Rows in Silver that are absent from source → soft-delete (_s_is_deleted=TRUE).

Safe to re-run — idempotent. Rows already soft-deleted are skipped.

Usage:
  python scripts/reconcile_silver_snapshot.py                          # all envs
  python scripts/reconcile_silver_snapshot.py --env datamart           # one env
  python scripts/reconcile_silver_snapshot.py --env datamart --entity project
  python scripts/reconcile_silver_snapshot.py --dry-run                # report only
  python scripts/reconcile_silver_snapshot.py --env <env> --tenant <tenant_id>
                                                                        # one tenant
                                                                        # after a
                                                                        # tenant-reset
                                                                        # (see below)

Post tenant-reset use: fieldcraft-tenant-reset's DROP SCHEMA public CASCADE +
baseline restore removes rows via DDL, not row-level DML, so Debezium's
replication slot (which survives a reset — see fieldcraft-tenant-reset's fix
for the CDC-publication incident it caused before that fix) never emits a
DELETE for them. Those rows linger in Silver as ghosts until something
notices. --tenant scopes this script to just the reset tenant so a routine
post-reset reconcile doesn't sweep every other tenant's data too. Requires
--env alongside --tenant (a tenant id is only meaningful within one source
environment).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

# ── Load .env.local ───────────────────────────────────────────────────────────
_ENV_FILE = _HERE.parent / ".env.local"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))

import psycopg2
import psycopg2.extras

from shared.env_config.schema import load_environments, EnvironmentsConfig, SourceSpec

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger(__name__)

# ── Silver connection constants ───────────────────────────────────────────────
_DST_HOST = os.environ.get("SILVER_PG_HOST", "localhost")
_DST_PORT = int(os.environ.get("SILVER_PG_PORT", "5433"))
_DST_USER = os.environ.get("SILVER_PG_USER", "silver_writer")
_DST_PASS = os.environ.get("SILVER_PG_PASSWORD", "")
_DST_DB   = os.environ.get("SILVER_PG_DB", "fieldcraft_silver")
_DST_SSL  = os.environ.get("SILVER_PG_SSL_MODE", "prefer")

# Tables that have no source equivalent or are infra-only — skip reconciliation.
# _cdc_watermark and _dlq are Silver-internal tables with no _s_tenantid column.
_SKIP_TABLES: frozenset[str] = frozenset({
    "_dlq_corrections",
    "_external_dlq",
    "_cdc_watermark",
    "_dlq",
    "tenant_configuration",
})

# Tables from non-tenant source DBs — source is provider_db, not tenant DB
_NAMESPACED_DBS: list[str] = [
    s.strip()
    for s in os.environ.get("SILVER_NAMESPACED_SOURCE_DBS", "fieldcraft_provider,fieldcraft_customer").split(",")
    if s.strip()
]

BATCH_SIZE = int(os.environ.get("RECONCILE_BATCH_SIZE", "1000"))


# ── Connection helpers ────────────────────────────────────────────────────────

def _silver_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=_DST_HOST, port=_DST_PORT, user=_DST_USER,
        password=_DST_PASS, dbname=_DST_DB, sslmode=_DST_SSL,
        connect_timeout=30,
    )


def _src_conn(spec: SourceSpec, dbname: str) -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=spec.host, port=spec.port, user=spec.user,
        password=spec.password, dbname=dbname, sslmode=spec.ssl_mode,
        connect_timeout=30,
    )


# ── Schema helpers ────────────────────────────────────────────────────────────

def _silver_entities(cur) -> list[str]:
    """Non-partition, non-ext Silver tables."""
    cur.execute("""
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'silver'
          AND c.relkind IN ('p', 'r')
          AND c.oid NOT IN (SELECT inhrelid FROM pg_inherits)
          AND c.relname NOT LIKE 'ext\_%' ESCAPE '\\'
        ORDER BY c.relname
    """)
    return [r[0] for r in cur.fetchall() if r[0] not in _SKIP_TABLES]


def _pk_cols(cur, entity: str) -> list[str]:
    """Business PK columns for a Silver table (excludes _s_env, _s_tenantid)."""
    cur.execute("""
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
           AND tc.table_schema    = kcu.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema    = 'silver'
          AND tc.table_name      = %s
          AND kcu.column_name NOT IN ('_s_env', '_s_tenantid')
        ORDER BY kcu.ordinal_position
    """, (entity,))
    return [r[0] for r in cur.fetchall()]


def _silver_tenants(cur, entity: str, env_name: str) -> list[str]:
    try:
        cur.execute(
            f"SELECT DISTINCT _s_tenantid FROM silver.{entity} "
            "WHERE _s_env = %s AND _s_is_deleted = FALSE",
            (env_name,),
        )
        return [r[0] for r in cur.fetchall() if r[0]]
    except Exception as exc:
        log.warning("Could not list tenants for silver.%s env=%s: %s", entity, env_name, exc)
        cur.connection.rollback()
        return []


# ── Source table name resolution ──────────────────────────────────────────────

def _resolve_source(entity: str, tenant_id: str, provider_db: str) -> tuple[str, str]:
    """
    Return (source_dbname, source_table) for a Silver entity row.

    Namespaced Silver tables (silver.fieldcraft_provider_foo, silver.fieldcraft_customer_bar)
    map to the non-tenant source DB; all others map to the tenant DB.

    `tenant` is a deliberate exception to the namespacing rule, mirroring
    universal_silver_merge.py::_namespaced_entity() — bare silver.tenant is
    populated solely from fieldcraft_provider.tenant (the tenant registry's single
    source of truth; see CLAUDE.md's "Tenant registry" section), never from
    any individual tenant DB's own excluded public.tenant copy. Missing this
    exception here (found live 2026-08-10) meant every tenant's row was
    checked against ITS OWN database's public.tenant instead of the
    provider's, found no matching row there, and got soft-deleted as a
    "ghost" -- silently flipping every tenant to _s_is_deleted=TRUE,
    including tenants that were genuinely Active.
    """
    if entity == "tenant":
        return provider_db, "tenant"
    for ns_db in _NAMESPACED_DBS:
        prefix = f"{ns_db}_"
        if entity.startswith(prefix):
            src_table = entity[len(prefix):]
            return ns_db, src_table
    # Regular tenant entity — source DB = tenant_id
    return tenant_id, entity


# ── Reconciliation core ───────────────────────────────────────────────────────

def _soft_delete_all(
    silver_conn: psycopg2.extensions.connection,
    entity: str,
    env_name: str,
    tenant_id: str,
    dry_run: bool,
) -> tuple[int, int]:
    """Soft-delete every active Silver row for (env, entity, tenant).

    Used when the source table no longer exists — the entire table was dropped,
    so every Silver row for that entity/tenant is a ghost.
    """
    silver_cur = silver_conn.cursor()
    try:
        silver_cur.execute(
            f"SELECT COUNT(*) FROM silver.{entity} "
            "WHERE _s_env = %s AND _s_tenantid = %s AND _s_is_deleted = FALSE",
            (env_name, tenant_id),
        )
        count = silver_cur.fetchone()[0]
        if count == 0:
            return 0, 0
        if not dry_run:
            silver_cur.execute(
                f"UPDATE silver.{entity} "
                "SET _s_is_deleted = TRUE, _s_deleted_at = NOW() "
                "WHERE _s_env = %s AND _s_tenantid = %s AND _s_is_deleted = FALSE",
                (env_name, tenant_id),
            )
            silver_conn.commit()
        log.info(
            "%-40s env=%-12s tenant=%-20s checked=%6d  ghosts=%6d%s",
            f"silver.{entity}", env_name, tenant_id, count, count,
            "  [DRY RUN]" if dry_run else "  [TABLE DROPPED]",
        )
        return count, count
    except Exception as exc:
        log.warning("soft_delete_all failed %s/%s/%s: %s", env_name, entity, tenant_id, exc)
        silver_conn.rollback()
        return 0, 0
    finally:
        silver_cur.close()


def _reconcile_entity_tenant(
    silver_conn: psycopg2.extensions.connection,
    src_spec: SourceSpec,
    env_name: str,
    entity: str,
    tenant_id: str,
    pk_cols: list[str],
    dry_run: bool,
) -> tuple[int, int]:
    """
    Reconcile one (env, entity, tenant) triple.
    Returns (rows_checked, ghost_rows_found).
    """
    src_dbname, src_table = _resolve_source(entity, tenant_id, src_spec.provider_db)
    pk_select = ", ".join(pk_cols)

    # 1. Fetch all source PKs
    try:
        conn_src = _src_conn(src_spec, src_dbname)
        with conn_src.cursor() as sc:
            sc.execute(f"SELECT {pk_select} FROM public.{src_table}")
            source_pks: set[tuple] = {tuple(r) for r in sc.fetchall()}
        conn_src.close()
    except psycopg2.OperationalError as exc:
        # Transient connection failure — skip safely, retry on next run.
        log.warning("Cannot connect to source %s/%s: %s", env_name, src_dbname, exc)
        return 0, 0
    except Exception as exc:
        if "does not exist" in str(exc):
            # Source table was dropped entirely — every Silver row is a ghost.
            # Soft-delete all active rows rather than skipping.
            log.warning(
                "Source table dropped %s/%s/%s — soft-deleting all Silver rows",
                env_name, tenant_id, entity,
            )
            return _soft_delete_all(silver_conn, entity, env_name, tenant_id, dry_run)
        log.warning("Source query failed %s/%s/%s: %s", env_name, src_dbname, src_table, exc)
        return 0, 0

    # 2. Fetch non-deleted Silver PKs for this (env, tenant)
    not_null = " AND ".join(f"{c} IS NOT NULL" for c in pk_cols)
    silver_cur = silver_conn.cursor()
    try:
        silver_cur.execute(
            f"SELECT {pk_select} FROM silver.{entity} "
            f"WHERE _s_env = %s AND _s_tenantid = %s "
            f"AND _s_is_deleted = FALSE AND {not_null}",
            (env_name, tenant_id),
        )
        silver_pks: set[tuple] = {tuple(r) for r in silver_cur.fetchall()}
    except Exception as exc:
        log.warning("Silver query failed %s/%s/%s: %s", env_name, entity, tenant_id, exc)
        silver_conn.rollback()
        silver_cur.close()
        return 0, 0

    # 3. Ghost rows = in Silver but absent from source
    ghost_pks = silver_pks - source_pks
    checked = len(silver_pks)

    if not ghost_pks:
        silver_cur.close()
        return checked, 0

    log.info(
        "%-40s  env=%-12s  tenant=%-15s  checked=%6d  ghosts=%4d%s",
        f"silver.{entity}", env_name, tenant_id, checked, len(ghost_pks),
        "  [DRY RUN]" if dry_run else "",
    )

    if dry_run:
        silver_cur.close()
        return checked, len(ghost_pks)

    # 4. Soft-delete ghost rows in batches
    ghost_list = list(ghost_pks)
    for i in range(0, len(ghost_list), BATCH_SIZE):
        batch = ghost_list[i : i + BATCH_SIZE]
        if len(pk_cols) == 1:
            col = pk_cols[0]
            vals = [pk[0] for pk in batch]
            silver_cur.execute(
                f"UPDATE silver.{entity} "
                f"SET _s_is_deleted = TRUE, _s_deleted_at = NOW() "
                f"WHERE _s_env = %s AND _s_tenantid = %s "
                f"AND {col} = ANY(%s) AND _s_is_deleted = FALSE",
                (env_name, tenant_id, vals),
            )
        else:
            # Composite PK — VALUES list approach
            row_placeholders = ",".join(
                "(" + ",".join(["%s"] * len(pk_cols)) + ")"
                for _ in batch
            )
            flat_vals: list = [v for pk in batch for v in pk]
            pk_match = " AND ".join(f"t.{c} = v.{c}" for c in pk_cols)
            silver_cur.execute(
                f"UPDATE silver.{entity} t "
                f"SET _s_is_deleted = TRUE, _s_deleted_at = NOW() "
                f"FROM (VALUES {row_placeholders}) AS v({pk_select}) "
                f"WHERE t._s_env = %s AND t._s_tenantid = %s "
                f"AND {pk_match} AND t._s_is_deleted = FALSE",
                flat_vals + [env_name, tenant_id],
            )

    silver_conn.commit()
    silver_cur.close()
    return checked, len(ghost_pks)


# ── Per-environment orchestration ─────────────────────────────────────────────

def reconcile_env(
    silver_conn: psycopg2.extensions.connection,
    env_name: str,
    src_spec: SourceSpec,
    target_entity: Optional[str],
    dry_run: bool,
    target_tenant: Optional[str] = None,
) -> dict:
    stats = {"env": env_name, "entities": 0, "checked": 0, "ghosts": 0, "errors": 0}
    cur = silver_conn.cursor()
    entities = _silver_entities(cur)
    if target_entity:
        entities = [e for e in entities if e == target_entity]

    log.info(
        "Reconciling env=%-12s — %d entities%s",
        env_name, len(entities),
        f"  tenant={target_tenant}" if target_tenant else "",
    )

    for entity in entities:
        pk_cols = _pk_cols(cur, entity)
        if not pk_cols:
            log.debug("Skipping silver.%s — no PK", entity)
            continue

        tenants = _get_tenants_for_entity(cur, entity, env_name, src_spec)
        if target_tenant:
            # Namespaced entities (fieldcraft_provider_*, fieldcraft_customer_*) use their
            # source DB name as a synthetic tenant_id — never a real tenant,
            # so a --tenant filter never matches them and they're correctly
            # skipped rather than silently reconciled anyway.
            tenants = [t for t in tenants if t == target_tenant]
        if not tenants:
            continue

        for tenant_id in tenants:
            try:
                checked, ghosts = _reconcile_entity_tenant(
                    silver_conn, src_spec, env_name, entity, tenant_id, pk_cols, dry_run,
                )
                stats["checked"] += checked
                stats["ghosts"] += ghosts
            except Exception as exc:
                log.error("Error reconciling %s/%s/%s: %s", env_name, entity, tenant_id, exc)
                silver_conn.rollback()
                stats["errors"] += 1

        stats["entities"] += 1

    cur.close()
    return stats


def _get_tenants_for_entity(cur, entity: str, env_name: str, src_spec: SourceSpec) -> list[str]:
    """
    For namespaced tables (fieldcraft_provider_*, fieldcraft_customer_*) there is no per-tenant
    split — use the DB name as the synthetic tenant_id.
    For regular tenant tables, query Silver for distinct _s_tenantid values.
    """
    for ns_db in _NAMESPACED_DBS:
        if entity.startswith(f"{ns_db}_"):
            # Source is a single non-tenant DB — use its name as the tenant key
            return [ns_db]
    return _silver_tenants(cur, entity, env_name)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Post-snapshot Silver reconciliation — soft-delete ghost rows."
    )
    parser.add_argument("--env",      help="Reconcile only this environment (e.g. datamart)")
    parser.add_argument("--entity",   help="Reconcile only this entity (e.g. project)")
    parser.add_argument("--tenant",   help="Reconcile only this tenant id — requires --env "
                                            "(post tenant-reset use, see module docstring)")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Report ghost rows without writing to Silver")
    args = parser.parse_args()

    if args.tenant and not args.env:
        parser.error("--tenant requires --env (a tenant id is only meaningful "
                     "within one source environment)")

    cfg = load_environments()
    silver_conn = _silver_conn()

    total_ghosts = 0
    total_errors = 0

    for env_name, spec in cfg.enabled_envs():
        if args.env and env_name != args.env:
            continue
        if spec.source is None:
            continue

        stats = reconcile_env(
            silver_conn, env_name, spec.source, args.entity, args.dry_run,
            target_tenant=args.tenant,
        )
        log.info(
            "RESULT env=%-12s  entities=%3d  checked=%7d  ghosts=%5d  errors=%d%s",
            stats["env"], stats["entities"], stats["checked"],
            stats["ghosts"], stats["errors"],
            "  [DRY RUN]" if args.dry_run else "",
        )
        total_ghosts += stats["ghosts"]
        total_errors += stats["errors"]

    silver_conn.close()

    if total_ghosts == 0:
        log.info("No ghost rows found — Silver is clean.")
    elif args.dry_run:
        log.warning("DRY RUN — would soft-delete %d ghost rows across all envs.", total_ghosts)
    else:
        log.info("Reconciliation complete — soft-deleted %d ghost rows.", total_ghosts)

    return 1 if total_errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
