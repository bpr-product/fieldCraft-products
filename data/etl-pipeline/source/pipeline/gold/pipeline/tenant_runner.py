"""Tenant runner — executes all pipeline steps for a single tenant.

Responsibilities:
  - Enforce per-tenant wall-clock timeout
  - Run each step sequentially on the main thread
  - Run post-load DQ gate after each successful step
  - Collect and return failed step names
"""

from __future__ import annotations

import logging
import os
import time
from typing import Callable

from gold.pipeline.audit import record_event, upsert_job_state
# NOTE: gold_event.pipeline_runs (event_writer.record_step) retired 2026-06-11 —
# it duplicated gold_audit.gold_event (no in-repo consumer). gold_audit is the
# single audit system of record.
from gold.pipeline.dq_gate import run_dq_gate
from gold.pipeline.lineage import RUN_ID_ENV
from gold.pipeline.logging_config import execution_context
from gold.pipeline.step_registry import build_steps, get_profile, FEATURE_REGISTRY

log = logging.getLogger("gold.pipeline.tenant_runner")


def _feature_for_step(step_name: str) -> str:
    """Return the feature key that owns this step, or 'unknown'."""
    for feature, descriptor in FEATURE_REGISTRY.items():
        if step_name in descriptor.step_names:
            return feature
    return "unknown"


def run_tenant(
    tenant: str,
    mongo_uri: str | None,
    pg_dsn: str | None,
    bpe_db: str,
    batch_size: int,
    dry_run: bool,
    skip_setup: bool,
    tenant_timeout_s: int = 0,
    max_docs_per_tenant: int = 0,
    eden_db: str = "gold_eden",
    skip_eden: bool = False,
    qa_recommendation_db: str = "gold_qa_recommendation",
    feature_filter: str | None = None,
    run_id: str | None = None,
    force_batch: bool = False,
) -> list[str]:
    """Run all pipeline steps for one tenant.

    Returns list of failed step names (empty = all passed).

    Args:
        tenant_timeout_s: wall-clock seconds allowed for the entire tenant run.
            0 = no limit. When exceeded, remaining steps are skipped.
        max_docs_per_tenant: cap on Silver rows read per loader per run.
            0 = unlimited.
    """
    steps = build_steps(
        tenant=tenant,
        mongo_uri=mongo_uri,
        pg_dsn=pg_dsn,
        bpe_db=bpe_db,
        batch_size=batch_size,
        dry_run=dry_run,
        skip_setup=skip_setup,
        max_docs=max_docs_per_tenant,
        eden_db=eden_db,
        skip_eden=skip_eden,
        qa_recommendation_db=qa_recommendation_db,
        feature_filter=feature_filter,
        force_batch=force_batch,
    )

    # Export the active run id so each loader stamps it onto its Gold documents
    # (read via gold.pipeline.lineage.current_run_id). Restored before returning;
    # the subprocess-isolation path inherits this env var automatically.
    _prev_run_env = os.environ.get(RUN_ID_ENV)
    if run_id:
        os.environ[RUN_ID_ENV] = run_id

    def _restore_run_env() -> None:
        if not run_id:
            return
        if _prev_run_env is None:
            os.environ.pop(RUN_ID_ENV, None)
        else:
            os.environ[RUN_ID_ENV] = _prev_run_env

    failed: list[str] = []
    completed_ok: set[str] = set()
    total = len(steps)
    # A dependency only blocks if it is actually part of THIS run. Steps excluded
    # from the plan (e.g. a feature filter slices steps out) must not block their
    # dependents — you can't wait on a step that was never scheduled.
    _planned_steps = {s for s, _ in steps}
    deadline = (time.monotonic() + tenant_timeout_s) if tenant_timeout_s > 0 else None

    try:
        for idx, (name, fn) in enumerate(steps, 1):

            # Enforce wall-clock timeout before starting each step
            if deadline is not None and time.monotonic() >= deadline:
                remaining = [s for s, _ in steps[idx - 1:]]
                log.error(
                    "tenant %s TIMEOUT after %ds — skipping: %s",
                    tenant, tenant_timeout_s, remaining,
                )
                failed.extend(remaining)
                if mongo_uri:
                    for skipped_name in remaining:
                        feature = _feature_for_step(skipped_name)
                        record_event(
                            mongo_uri, run_id,
                            tenant_id=tenant, feature=feature,
                            step_name=skipped_name, event_type="step_skipped",
                            status="skipped",
                            error_code="TENANT_TIMEOUT",
                            error_message=f"tenant timeout after {tenant_timeout_s}s",
                            dry_run=dry_run,
                        )
                        upsert_job_state(
                            mongo_uri, run_id,
                            feature=feature, tenant_id=tenant,
                            status="error", error="tenant timeout",
                        )
                break

            # Enforce depends_on: skip this step if any prerequisite step failed
            profile = get_profile(name)
            deps = profile.get("depends_on", []) if profile else []
            missing_deps = [d for d in deps if d in _planned_steps and d not in completed_ok]
            if missing_deps:
                log.warning(
                    "✗ %s SKIPPED — prerequisite(s) not completed: %s",
                    name, missing_deps,
                )
                failed.append(name)
                feature = _feature_for_step(name)
                if mongo_uri:
                    record_event(
                        mongo_uri, run_id,
                        tenant_id=tenant, feature=feature,
                        step_name=name, event_type="step_skipped",
                        status="skipped",
                        error_code="MISSING_DEPENDENCY",
                        error_message=f"prerequisite(s) not completed: {missing_deps}",
                        dry_run=dry_run,
                    )
                    upsert_job_state(
                        mongo_uri, run_id,
                        feature=feature, tenant_id=tenant,
                        status="error", error=f"dependency failed: {missing_deps}",
                    )
                continue

            with execution_context(step=name):
                feature = _feature_for_step(name)
                weight  = profile["weight"] if profile else "unknown"
                reason  = profile["reason"] if profile else ""
                log.info(
                    "▶ step %d/%d: %s [%s]%s",
                    idx, total, name, weight,
                    f" — {reason}" if reason else "",
                )
                t0 = time.monotonic()
                exc_str: str | None = None

                try:
                    rc = fn()
                except Exception as exc:
                    elapsed = time.monotonic() - t0
                    log.exception("✗ %s RAISED after %.1fs", name, elapsed)
                    exc_str = str(exc)
                    rc = 3

                elapsed = time.monotonic() - t0

                if rc == 0:
                    # The loader reported success — now the DQ gate decides whether
                    # the DATA it wrote is real. A fatal gate finding (broken-empty,
                    # contract violation, missing _grain, cross-tenant leak) turns
                    # the step red so it propagates to a non-zero process exit.
                    dq_failures = run_dq_gate(
                        name, tenant, mongo_uri, dry_run, run_id=run_id, pg_dsn=pg_dsn,
                    )
                    if dq_failures:
                        log.error("✗ %s DATA-QUALITY GATE FAILED (%.1fs): %s",
                                  name, elapsed, dq_failures)
                        failed.append(name)
                        error_msg = f"dq gate: {', '.join(dq_failures)}"
                        if mongo_uri:
                            record_event(
                                mongo_uri, run_id,
                                tenant_id=tenant, feature=feature,
                                step_name=name, event_type="step_failed",
                                status="failed", duration_ms=int(elapsed * 1000),
                                error_code="DQ_GATE_FAILED",
                                error_message=error_msg,
                                dry_run=dry_run,
                            )
                            upsert_job_state(
                                mongo_uri, run_id,
                                feature=feature, tenant_id=tenant,
                                status="error", error=error_msg,
                            )
                    else:
                        completed_ok.add(name)
                        log.info("✓ %s completed (%.1fs)", name, elapsed)
                        if mongo_uri:
                            record_event(
                                mongo_uri, run_id,
                                tenant_id=tenant, feature=feature,
                                step_name=name, event_type="step_complete",
                                status="ok", duration_ms=int(elapsed * 1000),
                                dry_run=dry_run,
                            )
                            upsert_job_state(
                                mongo_uri, run_id,
                                feature=feature, tenant_id=tenant, status="idle",
                            )
                else:
                    log.error("✗ %s FAILED rc=%d (%.1fs)", name, rc, elapsed)
                    failed.append(name)
                    error_msg = exc_str or f"rc={rc}"
                    if mongo_uri:
                        record_event(
                            mongo_uri, run_id,
                            tenant_id=tenant, feature=feature,
                            step_name=name, event_type="step_failed",
                            status="failed", duration_ms=int(elapsed * 1000),
                            error_code="STEP_EXCEPTION" if exc_str else f"RC_{rc}",
                            error_message=error_msg,
                            dry_run=dry_run,
                        )
                        upsert_job_state(
                            mongo_uri, run_id,
                            feature=feature, tenant_id=tenant,
                            status="error", error=error_msg,
                        )

    finally:
        _restore_run_env()
    return failed
