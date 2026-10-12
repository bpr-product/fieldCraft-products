"""Gold pipeline Prometheus metrics.

Exposes pipeline run outcomes so the existing Prometheus + Grafana stack
(infra/prometheus/) can scrape and alert.

Usage in a loader:
    from gold.src.monitoring.metrics import record_loader_run
    record_loader_run("estimate_summary", tenant_id, upserted=120, failed=0, elapsed_s=3.4)

Metrics written to GOLD_METRICS_FILE (default /tmp/gold_pipeline_metrics.prom).
The Prometheus node_exporter textfile collector picks this up if pointed at the
same directory via --collector.textfile.directory.

For Azure Container Apps: mount an Azure Files share at /tmp/metrics and point
the node_exporter sidecar at the same share path.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from threading import Lock

_METRICS_FILE = Path(os.environ.get("GOLD_METRICS_FILE", "/tmp/gold_pipeline_metrics.prom"))
_lock = Lock()
log = logging.getLogger("gold.src.monitoring.metrics")


def _write(content: str) -> None:
    """Atomically write metric content to the textfile."""
    try:
        _METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _METRICS_FILE.with_suffix(".prom.tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(_METRICS_FILE)
    except OSError as e:
        log.debug("metrics write failed (non-fatal): %s", e)


def record_heartbeat(component: str) -> None:
    """Liveness heartbeat for an always-on stream, emitted INDEPENDENT of data flow.

    Answers "is the consumer alive when no CDC has arrived for a long time?" — a
    stalled/dead stream is otherwise indistinguishable from a legitimately quiet
    source. Written to its OWN small file (overwritten, never appended) so a
    frequent heartbeat doesn't grow the metrics textfile. node_exporter picks up
    every *.prom in the textfile dir.

    Emits: gold_stream_heartbeat_timestamp_seconds{component="..."}
    Alert: time() - gold_stream_heartbeat_timestamp_seconds > threshold (or absent).
    """
    ts = time.time()
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in component)
    hb_file = _METRICS_FILE.with_name(f"gold_heartbeat_{safe}.prom")
    content = (
        "# HELP gold_stream_heartbeat_timestamp_seconds Last liveness tick of an always-on Gold stream\n"
        "# TYPE gold_stream_heartbeat_timestamp_seconds gauge\n"
        f'gold_stream_heartbeat_timestamp_seconds{{component="{component}"}} {ts:.0f}\n'
    )
    try:
        hb_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = hb_file.with_suffix(".prom.tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(hb_file)
    except OSError as e:
        log.debug("heartbeat write failed (non-fatal): %s", e)


def record_loader_run(
    loader: str,
    tenant: str,
    *,
    upserted: int,
    failed: int,
    elapsed_s: float,
) -> None:
    """Record outcome of a single loader step for one tenant."""
    ts = int(time.time() * 1000)
    labels = f'loader="{loader}",tenant="{tenant}"'
    with _lock:
        existing = ""
        if _METRICS_FILE.exists():
            existing = _METRICS_FILE.read_text(encoding="utf-8")

        new_lines = [
            f"# HELP gold_loader_upserted_total Documents upserted to Gold Mongo per loader/tenant",
            f"# TYPE gold_loader_upserted_total counter",
            f"gold_loader_upserted_total{{{labels}}} {upserted} {ts}",
            f"# HELP gold_loader_failed_total Documents that failed to write per loader/tenant",
            f"# TYPE gold_loader_failed_total counter",
            f"gold_loader_failed_total{{{labels}}} {failed} {ts}",
            f"# HELP gold_loader_duration_seconds Time taken for loader step",
            f"# TYPE gold_loader_duration_seconds gauge",
            f"gold_loader_duration_seconds{{{labels}}} {elapsed_s:.3f} {ts}",
            f"# HELP gold_loader_last_run_timestamp_seconds Unix timestamp of last run",
            f"# TYPE gold_loader_last_run_timestamp_seconds gauge",
            f"gold_loader_last_run_timestamp_seconds{{{labels}}} {ts / 1000:.0f} {ts}",
        ]
        content = (existing + "\n" if existing else "") + "\n".join(new_lines) + "\n"
        _write(content)


def record_reconciliation(
    feature: str,
    tenant: str,
    *,
    missing: int,
    stale: int,
    orphan: int,
) -> None:
    """Record Silver↔Gold reconciliation drift per feature/tenant.

    gold_reconcile_drift{feature,tenant,kind="missing|stale|orphan"} — alert on
    missing/stale > 0 (Gold behind Silver → run --rebuild). orphan is softer
    (a Gold doc with no Silver row, usually a not-yet-tombstoned delete).
    """
    ts = int(time.time() * 1000)
    with _lock:
        existing = ""
        if _METRICS_FILE.exists():
            existing = _METRICS_FILE.read_text(encoding="utf-8")
        lines = [
            "# HELP gold_reconcile_drift Silver↔Gold key/LSN drift per feature/tenant/kind",
            "# TYPE gold_reconcile_drift gauge",
        ]
        for kind, val in (("missing", missing), ("stale", stale), ("orphan", orphan)):
            labels = f'feature="{feature}",tenant="{tenant}",kind="{kind}"'
            lines.append(f"gold_reconcile_drift{{{labels}}} {val} {ts}")
        lines += [
            "# HELP gold_reconcile_last_run_timestamp_seconds Unix ts of last reconciliation",
            "# TYPE gold_reconcile_last_run_timestamp_seconds gauge",
            f'gold_reconcile_last_run_timestamp_seconds{{feature="{feature}",tenant="{tenant}"}} {ts / 1000:.0f} {ts}',
        ]
        content = (existing + "\n" if existing else "") + "\n".join(lines) + "\n"
        _write(content)


def record_pipeline_run(
    *,
    tenants_total: int,
    tenants_passed: int,
    tenants_failed: int,
    elapsed_s: float,
) -> None:
    """Record high-level pipeline outcome (all tenants).

    Called last in the run, after every record_loader_run. It must PRESERVE the
    per-loader / reconciliation series already written (otherwise the loader
    alerts — GoldLoaderZeroUpserts / SlowStep / WriteFailures — go blind). We
    keep all non-pipeline lines and replace only this function's own (unlabeled)
    ``gold_pipeline_*`` block so it doesn't accumulate duplicate series.
    """
    ts = int(time.time() * 1000)
    with _lock:
        existing = ""
        if _METRICS_FILE.exists():
            existing = _METRICS_FILE.read_text(encoding="utf-8")
        # No other metric name contains "gold_pipeline_", so this safely drops
        # the prior pipeline block (HELP/TYPE/sample) and keeps loader/reconcile.
        kept = [ln for ln in existing.splitlines() if "gold_pipeline_" not in ln]
        lines = [
            f"# HELP gold_pipeline_tenants_total Tenants processed in last pipeline run",
            f"# TYPE gold_pipeline_tenants_total gauge",
            f"gold_pipeline_tenants_total {tenants_total} {ts}",
            f"# HELP gold_pipeline_tenants_passed Tenants with all steps succeeded",
            f"# TYPE gold_pipeline_tenants_passed gauge",
            f"gold_pipeline_tenants_passed {tenants_passed} {ts}",
            f"# HELP gold_pipeline_tenants_failed Tenants with one or more failed steps",
            f"# TYPE gold_pipeline_tenants_failed gauge",
            f"gold_pipeline_tenants_failed {tenants_failed} {ts}",
            f"# HELP gold_pipeline_duration_seconds Total wall-clock time for pipeline run",
            f"# TYPE gold_pipeline_duration_seconds gauge",
            f"gold_pipeline_duration_seconds {elapsed_s:.3f} {ts}",
            f"# HELP gold_pipeline_last_run_timestamp_seconds Unix timestamp of last pipeline run",
            f"# TYPE gold_pipeline_last_run_timestamp_seconds gauge",
            f"gold_pipeline_last_run_timestamp_seconds {ts / 1000:.0f} {ts}",
        ]
        prefix = "\n".join(kept).strip("\n")
        content = (prefix + "\n" if prefix else "") + "\n".join(lines) + "\n"
        _write(content)
