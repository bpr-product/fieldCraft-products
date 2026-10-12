"""Per-tenant isolation in the multi-tenant pipeline loop.

One tenant raising an unhandled exception must not abort the whole run — the
remaining tenants still execute and the run reports failure (exit 1).
"""

from __future__ import annotations

import pytest

import gold.pipeline.pipeline_runner as pr

pytestmark = pytest.mark.pipeline


def test_one_tenant_crash_does_not_abort_loop(monkeypatch):
    calls: list[str] = []

    def fake_run_tenant(*, tenant, **_kw):
        calls.append(tenant)
        if tenant == "T2":
            raise RuntimeError("boom")   # unhandled — escapes run_tenant
        return []                        # no failed steps

    monkeypatch.setattr(pr, "run_tenant", fake_run_tenant)

    rc = pr.run_pipeline(
        tenants=["T1", "T2", "T3"],
        mongo_uri=None,
        pg_dsn=None,
        bpe_db="gold_bpe",
        batch_size=10,
        dry_run=True,
        skip_setup=True,
        tenant_timeout_s=0,    # avoids resolve_sla / config dependency
    )

    assert calls == ["T1", "T2", "T3"], "loop must continue past the crashing tenant"
    assert rc == 1, "a crashed tenant must make the run report failure"


def test_all_tenants_ok_returns_zero(monkeypatch):
    monkeypatch.setattr(pr, "run_tenant", lambda *, tenant, **_kw: [])
    rc = pr.run_pipeline(
        tenants=["T1", "T2"],
        mongo_uri=None,
        pg_dsn=None,
        bpe_db="gold_bpe",
        batch_size=10,
        dry_run=True,
        skip_setup=True,
        tenant_timeout_s=0,
    )
    assert rc == 0
