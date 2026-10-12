"""Configurable SLA / timeout resolver — no hardcoded thresholds in code.

Every SLA-style value (data-freshness ceiling, per-tenant wall-clock timeout,
per-project timeout) is resolved from the ``sla:`` block of gold_config.yml
rather than being baked into the code. This lets thresholds be tuned per
environment, tenant, and project without a code change, and lets different
features carry different SLAs (e.g. BPE nightly with a long freshness SLA while
qa_recommendation stays on a tight one).

Resolution precedence (highest priority first):

    1. runtime override source   (Phase 2 — pluggable metadata store; off by default)
    2. project override          sla.projects."<tenant>:<project>"
    3. tenant override           sla.tenants."<tenant>"
    4. environment               sla.environments."<GOLD_ENV>"
    5. feature/loader default     <feature>.freshness_sla  /  loaders.<feature>.freshness_sla
    6. global default            sla.defaults.<metric>

A metric with no value anywhere resolves to ``None`` — callers treat that as
"unset / no enforcement" (e.g. timeout None → no limit), so a missing config
never crashes and no numeric literal lives in this module.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from gold.pipeline.step_registry import gold_config

# Phase 2 hook: a callable(metric, *, env, tenant, project, feature) -> int | None
# that reads a runtime metadata store. None (default) means "no runtime source".
_override_source: Optional[Callable[..., Optional[int]]] = None


def set_override_source(source: Optional[Callable[..., Optional[int]]]) -> None:
    """Register (or clear with None) a runtime SLA override source.

    Lets a metadata-store layer be added later without touching call sites —
    the resolver consults it ahead of the static gold_config.yml layers.
    """
    global _override_source
    _override_source = source


def _layer(block: dict, *keys: str) -> Optional[dict]:
    """Walk nested dict keys, returning the final dict or None if any miss."""
    cur = block
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)  # type: ignore[assignment]
    return cur if isinstance(cur, dict) else None


def resolve_sla(
    metric: str,
    *,
    env: Optional[str] = None,
    tenant: Optional[str] = None,
    project: Optional[str] = None,
    feature: Optional[str] = None,
) -> Optional[int]:
    """Resolve a single SLA metric by precedence. Returns None if unset anywhere.

    Args:
        metric:  one of "freshness_sla" | "tenant_timeout_s" | "project_timeout_s"
                 (any key present in the sla block).
        env:     environment name; defaults to the GOLD_ENV env var.
        tenant:  tenant id for tenant/project-scoped overrides.
        project: project id (requires tenant) for the most specific override.
        feature: feature/loader key for the feature-level default (freshness_sla).
    """
    cfg = gold_config()
    sla = cfg.get("sla", {}) if isinstance(cfg, dict) else {}
    env = env if env is not None else os.environ.get("GOLD_ENV")

    # 1. runtime override source (Phase 2)
    if _override_source is not None:
        v = _override_source(metric, env=env, tenant=tenant, project=project, feature=feature)
        if v is not None:
            return v

    # 2. project override (tenant-scoped composite key)
    if tenant and project:
        layer = _layer(sla, "projects", f"{tenant}:{project}")
        if layer and metric in layer:
            return layer[metric]

    # 3. tenant override
    if tenant:
        layer = _layer(sla, "tenants", tenant)
        if layer and metric in layer:
            return layer[metric]

    # 4. environment
    if env:
        layer = _layer(sla, "environments", env)
        if layer and metric in layer:
            return layer[metric]

    # 5. feature / loader default (mainly freshness_sla)
    if feature:
        feat = cfg.get(feature) if isinstance(cfg, dict) else None
        if isinstance(feat, dict) and metric in feat:
            return feat[metric]
        loader = _layer(cfg, "loaders", feature)
        if loader and metric in loader:
            return loader[metric]

    # 6. global default
    defaults = sla.get("defaults", {}) if isinstance(sla, dict) else {}
    return defaults.get(metric)
