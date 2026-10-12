"""Pipeline runner — loops over all tenants and emits Prometheus metrics."""

from __future__ import annotations

import logging
import time

from gold.pipeline.audit import close_run, open_run
from gold.pipeline.logging_config import execution_context, set_execution_context
from gold.pipeline.sla import resolve_sla
from gold.pipeline.tenant_runner import run_tenant

log = logging.getLogger("gold.pipeline.runner")

_BAR = "━" * 64


def run_pipeline(
    tenants: list[str],
    mongo_uri: str | None,
    pg_dsn: str | None,
    bpe_db: str,
    batch_size: int,
    dry_run: bool,
    skip_setup: bool,
    tenant_timeout_s: int | None = None,
    max_docs_per_tenant: int = 0,
    eden_db: str = "gold_eden",
    skip_eden: bool = False,
    qa_recommendation_db: str = "gold_qa_recommendation",
    feature_filter: str | None = None,
    force_batch: bool = False,
) -> int:
    """Run the full pipeline (or a single feature) across all tenants.

    Returns:
        0 — all tenants passed
        1 — one or more tenants had failed steps
    """
    # Set feature context for the lifetime of this run
    if feature_filter:
        set_execution_context(feature=feature_filter)

    log.info(_BAR)
    log.info("GOLD PIPELINE START")
    log.info("  tenants    : %d", len(tenants))
    log.info("  feature    : %s", feature_filter or "all")
    log.info("  dry_run    : %s", dry_run)
    log.info("  skip_setup : %s", skip_setup)
    log.info("  timeout_s  : %s",
             tenant_timeout_s if tenant_timeout_s is not None else "per-tenant (config)")
    log.info("  max_docs   : %s", max_docs_per_tenant or "unlimited")
    log.info(_BAR)

    # NOTE: gold_pipeline._job_state is provisioned lazily by cursor_store on
    # first real (non-rebuild) cursor read/write — not eagerly here. Realtime
    # streams use Flink checkpoints and --rebuild reads from EPOCH, so neither
    # path provisions the table. See cursor_store._lazy_ensure.

    # Open audit run — non-fatal if mongo_uri is absent
    run_id = None
    if mongo_uri:
        run_id = open_run(mongo_uri, feature=feature_filter, dry_run=dry_run)
        if run_id:
            log.debug("audit run_id: %s", run_id)

    all_failed: dict[str, list[str]] = {}
    t_start = time.monotonic()

    for idx, tenant in enumerate(tenants, 1):
        with execution_context(tenant=tenant):
            log.info("═══ tenant %d/%d: %s ═══", idx, len(tenants), tenant)
            t0 = time.monotonic()

            # One tenant's unhandled failure (e.g. resolve_sla / run_tenant raising
            # before its own step-level handling, or Mongo dropping mid-run) must
            # NOT abort the whole multi-tenant loop — isolate it, mark the tenant
            # failed, and carry on to the next tenant.
            try:
                # Explicit --tenant-timeout overrides for all tenants; otherwise
                # resolve per tenant from gold_config.yml sla: block (None → no limit).
                if tenant_timeout_s is not None:
                    effective_timeout = tenant_timeout_s
                else:
                    effective_timeout = resolve_sla(
                        "tenant_timeout_s", tenant=tenant, feature=feature_filter
                    ) or 0

                failed = run_tenant(
                    tenant=tenant,
                    mongo_uri=mongo_uri,
                    pg_dsn=pg_dsn,
                    bpe_db=bpe_db,
                    batch_size=batch_size,
                    dry_run=dry_run,
                    skip_setup=skip_setup,
                    tenant_timeout_s=effective_timeout,
                    max_docs_per_tenant=max_docs_per_tenant,
                    eden_db=eden_db,
                    skip_eden=skip_eden,
                    qa_recommendation_db=qa_recommendation_db,
                    feature_filter=feature_filter,
                    run_id=run_id,
                    force_batch=force_batch,
                )
            except Exception:   # noqa: BLE001 — isolate per-tenant failure
                log.exception(
                    "tenant %s crashed with an unhandled exception — "
                    "marking failed and continuing", tenant,
                )
                failed = ["<unhandled exception>"]

            elapsed = time.monotonic() - t0
            status = "FAILED" if failed else "OK"
            if failed:
                log.error(
                    "tenant %s done | status=%s elapsed=%.1fs failed_steps=%s",
                    tenant, status, elapsed, failed,
                )
            else:
                log.info("tenant %s done | status=%s elapsed=%.1fs", tenant, status, elapsed)

            # Check freshness SLA after each tenant run (non-fatal)
            if not dry_run and pg_dsn and mongo_uri:
                try:
                    from gold.src.monitoring.freshness import check_all_freshness
                    check_all_freshness(
                        tenant=tenant,
                        pg_dsn=pg_dsn,
                        mongo_uri=mongo_uri,
                        failed_steps=failed,
                    )
                except Exception as exc:
                    log.debug("freshness check skipped (non-fatal): %s", exc)

            if failed:
                all_failed[tenant] = failed

    total_elapsed = time.monotonic() - t_start
    passed = len(tenants) - len(all_failed)
    overall = "FAILED" if all_failed else "OK"

    log.info(_BAR)
    log.info("GOLD PIPELINE DONE")
    log.info("  status     : %s", overall)
    log.info(
        "  tenants    : %d  (%d passed, %d failed)",
        len(tenants), passed, len(all_failed),
    )
    log.info("  elapsed    : %.1fs", total_elapsed)
    log.info(_BAR)

    # Close audit run (non-fatal)
    if mongo_uri and run_id:
        failed_summary = [
            {"tenant": t, "step": s}
            for t, steps in all_failed.items()
            for s in steps
        ]
        close_run(
            mongo_uri,
            run_id,
            tenants_total=len(tenants),
            tenants_passed=passed,
            tenants_failed=len(all_failed),
            elapsed_ms=int(total_elapsed * 1000),
            failed_summary=failed_summary,
        )

    # Emit Prometheus metrics (non-fatal)
    try:
        from gold.src.monitoring.metrics import record_pipeline_run
        record_pipeline_run(
            tenants_total=len(tenants),
            tenants_passed=passed,
            tenants_failed=len(all_failed),
            elapsed_s=total_elapsed,
        )
    except Exception as exc:
        log.warning("metrics emit failed (non-fatal): %s", exc)

    return 1 if all_failed else 0
