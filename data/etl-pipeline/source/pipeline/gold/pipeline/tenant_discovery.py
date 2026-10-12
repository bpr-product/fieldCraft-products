"""Tenant discovery — find active tenants from Silver PostgreSQL."""

from __future__ import annotations

import logging
import os

import psycopg2

from gold.pipeline.retry import with_retry

log = logging.getLogger("gold.pipeline.tenant_discovery")

_RETRY_MAX = int(os.environ.get("SILVER_PG_RETRY_MAX_ATTEMPTS", "3"))
_RETRY_BASE_MS = int(os.environ.get("SILVER_PG_RETRY_BASE_DELAY_MS", "200"))


def discover_tenants(pg_dsn: str) -> list[str]:
    """Return distinct active tenant IDs from the Silver tenant roster.

    The active roster in ``silver.tenant`` is the single source of truth for
    which tenants every Gold feature runs against — not the presence of
    project/invoice/task data. This means a newly-onboarded active tenant is
    picked up immediately (even before it has any project data), and
    config/reference-only tenants are still processed (each feature loader
    no-ops cleanly when it finds no relevant Silver rows).

    A tenant is included when it is not soft-deleted and ``status`` is
    ``active`` (matched case-insensitively — the source stores ``Active``).
    ``DISTINCT`` collapses the multiple source rows that exist per tenant
    (e.g. partial + complete records sharing one ``tenant_id``).

    ``silver.tenant`` is sourced from the provider DB (``fieldcraft_provider.tenant``),
    a non-tenant-scoped table — its CDC metadata column ``_s_tenantid`` is
    therefore always the literal string ``"fieldcraft_provider"`` for every row, never
    a real tenant ID. The actual tenant identifier is the business column
    ``tenant_id`` (e.g. ``tn64064399``). Selecting ``_s_tenantid`` collapses
    every tenant into the single pseudo-tenant ``fieldcraft_provider``, so this query
    reads ``tenant_id`` instead.

    Returns tenants sorted alphabetically for deterministic run order.
    Transient PG errors are retried via the shared @with_retry decorator on
    _query_tenants (up to SILVER_PG_RETRY_MAX_ATTEMPTS with exponential backoff).
    """
    return _query_tenants(pg_dsn)


@with_retry(
    max_attempts=_RETRY_MAX,
    base_delay_ms=_RETRY_BASE_MS,
    exceptions=(psycopg2.OperationalError,),
)
def _query_tenants(pg_dsn: str) -> list[str]:
    log.debug("discovering active tenants from silver.tenant roster...")
    conn = psycopg2.connect(pg_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT tenant_id
                FROM silver.tenant
                WHERE _s_is_deleted = FALSE
                  AND lower(status) = 'active'
                ORDER BY tenant_id
            """)
            tenants = [row[0] for row in cur.fetchall()]
            if not tenants:
                # silver.tenant is the single source of truth for the run set, so
                # an empty result means EVERY feature processes zero tenants. That
                # is almost always a roster/replication problem (silver.tenant not
                # built, CDC behind, or all rows inactive/deleted) rather than a
                # genuine "no active tenants" state — make it loud, not silent.
                log.warning(
                    "tenant discovery returned 0 active tenants from silver.tenant "
                    "(status='active', not soft-deleted) — the pipeline will process "
                    "NOTHING this run. Verify silver.tenant is populated and CDC is "
                    "current."
                )
            else:
                log.info("discovered %d active tenant(s)", len(tenants))
            return tenants
    finally:
        conn.close()
