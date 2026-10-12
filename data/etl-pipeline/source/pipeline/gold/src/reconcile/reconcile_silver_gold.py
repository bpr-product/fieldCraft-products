"""Silver ↔ Gold reconciliation / loss detection (closes the R1 blind spot).

Realtime Gold streams are the sole writer and have no automatic reconciliation:
a lost CDC trigger (cold-start gap, the Silver buffer-loss window, a quarantined
recompute) leaves Gold silently stale, and `--rebuild` only helps if someone
*notices*. This tool is the "someone notices" — it diffs, per (tenant, feature),
the key set + LSN in Silver against Gold and reports three kinds of drift:

  missing — a Silver row has NO Gold doc            (a lost insert/trigger)
  stale   — Gold doc's _silverLsn < Silver _s_lsn   (a missed update)
  orphan  — a Gold doc has no matching Silver row    (should be tombstoned/removed)

It is read-only. It emits a Prometheus metric (alertable) and exits non-zero when
drift exceeds the threshold (CI/cron-friendly). Recovery is then a targeted
`run_pipeline.py --feature <f> --tenant <t> --rebuild`.

Scope: BPE feature_store + qa_recommendation qa_answer + Eden 6 project-grain collections
(project_health, invoice_summary, task_intelligence, procurement_status, workforce_status,
bid_context). Eden tenant_dashboard is excluded: it is tenant-grain (one doc per tenant);
its reconcile logic differs from project-grain and is tracked separately.

Usage:
    python -m gold.src.reconcile.reconcile_silver_gold --all-tenants
    python -m gold.src.reconcile.reconcile_silver_gold --feature bpe --tenant tnXXX
    python -m gold.src.reconcile.reconcile_silver_gold --all-tenants --max-drift 0 --sample 10
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHARED = str(_REPO_ROOT / "shared")
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)

log = logging.getLogger("gold.reconcile")


# ---------------------------------------------------------------------------
# Feature specs — how to read the key+LSN set from Silver and from Gold
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FeatureSpec:
    feature: str
    silver_table: str          # silver.<table>
    silver_key: str            # PK column in Silver (maps to the Gold key)
    gold_db_env: str           # env var holding the Gold DB name
    gold_db_default: str
    gold_collection: str
    gold_key: str              # key field in the Gold doc
    builder: str               # "module:function" recompute fn (build_documents)


# Gold-side fields that legitimately differ run-to-run; excluded from the
# advisory field-level value comparison (--value-check).
VOLATILE_FIELDS = frozenset({
    "_id", "_etlUpdatedAt", "updatedAt", "processedAt", "goldUpdatedAt",
    "goldCreatedAt", "createdAt", "processingTimestamp", "refreshed_at",
    "_pipelineRunId", "_silverWatermarks", "_silverLsn", "lafieldcraft",
})


SPECS: dict[str, FeatureSpec] = {
    "bpe": FeatureSpec(
        feature="bpe", silver_table="project", silver_key="id",
        gold_db_env="GOLD_BPE_DB_NAME", gold_db_default="gold_bpe",
        gold_collection="feature_store", gold_key="project_id",
        builder="gold.src.bpe.load_estimate_snapshot:build_documents",
    ),
    "qa_recommendation": FeatureSpec(
        feature="qa_recommendation", silver_table="answers", silver_key="id",
        gold_db_env="GOLD_QA_RECOMMENDATION_DB", gold_db_default="gold_qa_recommendation",
        gold_collection="qa_answer", gold_key="answer_id",
        builder="gold.src.reference_project.load_qa_recommendation:build_documents",
    ),
    # Eden: 6 project-grain collections. Gold key is "projectId" (stored as str(project_id)).
    # _normalize_key converts both Silver bigint and Gold string to int for comparison.
    # tenant_dashboard is excluded: it is tenant-grain (one doc per tenant, not per project);
    # its key set and LSN semantics require different reconcile logic.
    "eden_project_health": FeatureSpec(
        feature="eden_project_health", silver_table="project", silver_key="id",
        gold_db_env="GOLD_EDEN_DB", gold_db_default="gold_eden",
        gold_collection="project_health", gold_key="projectId",
        builder="gold.src.eden.load_project_health:build_documents",
    ),
    "eden_invoice_summary": FeatureSpec(
        feature="eden_invoice_summary", silver_table="project", silver_key="id",
        gold_db_env="GOLD_EDEN_DB", gold_db_default="gold_eden",
        gold_collection="invoice_summary", gold_key="projectId",
        builder="gold.src.eden.load_invoice_summary:build_documents",
    ),
    "eden_task_intelligence": FeatureSpec(
        feature="eden_task_intelligence", silver_table="project", silver_key="id",
        gold_db_env="GOLD_EDEN_DB", gold_db_default="gold_eden",
        gold_collection="task_intelligence", gold_key="projectId",
        builder="gold.src.eden.load_task_intelligence:build_documents",
    ),
    "eden_procurement_status": FeatureSpec(
        feature="eden_procurement_status", silver_table="project", silver_key="id",
        gold_db_env="GOLD_EDEN_DB", gold_db_default="gold_eden",
        gold_collection="procurement_status", gold_key="projectId",
        builder="gold.src.eden.load_procurement_status:build_documents",
    ),
    "eden_workforce_status": FeatureSpec(
        feature="eden_workforce_status", silver_table="project", silver_key="id",
        gold_db_env="GOLD_EDEN_DB", gold_db_default="gold_eden",
        gold_collection="workforce_status", gold_key="projectId",
        builder="gold.src.eden.load_workforce_status:build_documents",
    ),
    "eden_bid_context": FeatureSpec(
        feature="eden_bid_context", silver_table="project", silver_key="id",
        gold_db_env="GOLD_EDEN_DB", gold_db_default="gold_eden",
        gold_collection="bid_context", gold_key="projectId",
        builder="gold.src.eden.load_bid_context:build_documents",
    ),
}


# ---------------------------------------------------------------------------
# Pure drift computation (no I/O — unit tested)
# ---------------------------------------------------------------------------

@dataclass
class Drift:
    expected: int = 0          # Silver rows that should have a Gold doc
    gold: int = 0              # Gold docs present
    # TRUE counts (not capped) — used for the verdict, exit code, and metrics.
    n_missing: int = 0         # in Silver, not in Gold
    n_stale: int = 0           # Gold LSN behind Silver
    n_orphan: int = 0          # in Gold, not in Silver
    # Capped key SAMPLES for human-readable logging only.
    missing: list = field(default_factory=list)
    stale: list = field(default_factory=list)
    orphan: list = field(default_factory=list)

    @property
    def loss(self) -> int:
        """Drift that means Gold is BEHIND truth (the part that needs --rebuild)."""
        return self.n_missing + self.n_stale

    @property
    def total(self) -> int:
        return self.n_missing + self.n_stale + self.n_orphan


def compute_drift(silver: dict, gold: dict, *, sample: int = 10) -> Drift:
    """Diff Silver vs Gold key→LSN maps.

    silver: {key -> _s_lsn}  (expected docs; key already normalized to match gold)
    gold:   {key -> _silverLsn}
    A None/0 LSN on either side is treated as "unknown" and never counted stale.
    Counts are exact; the key lists are truncated to `sample` for logging.
    """
    s_keys, g_keys = set(silver), set(gold)
    missing = sorted(s_keys - g_keys, key=str)
    orphan = sorted(g_keys - s_keys, key=str)
    stale = sorted(
        (k for k in (s_keys & g_keys)
         if silver[k] and gold.get(k) is not None and gold[k] < silver[k]),
        key=str,
    )
    return Drift(
        expected=len(s_keys),
        gold=len(g_keys),
        n_missing=len(missing),
        n_stale=len(stale),
        n_orphan=len(orphan),
        missing=missing[:sample],
        stale=stale[:sample],
        orphan=orphan[:sample],
    )


# ---------------------------------------------------------------------------
# Field-level value comparison (advisory; pure + unit-tested)
# ---------------------------------------------------------------------------

def _norm(v) -> str:
    """JSON-normalize a value so int/float/datetime/dict-order noise doesn't
    register as a false mismatch."""
    import json
    try:
        return json.dumps(v, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        return str(v)


def compare_docs(expected: dict, actual: dict, ignore=VOLATILE_FIELDS) -> list:
    """Return the sorted business field names whose value differs between the
    recomputed (expected) doc and the stored (actual) Gold doc, ignoring
    volatile Gold-side meta. Advisory — recompute-from-Silver means values
    should already match; a mismatch flags a shape/transform regression."""
    keys = (set(expected) | set(actual)) - set(ignore)
    return sorted(k for k in keys if _norm(expected.get(k)) != _norm(actual.get(k)))


# ---------------------------------------------------------------------------
# I/O — read key+LSN sets from Silver (PG) and Gold (Cosmos)
# ---------------------------------------------------------------------------

def _normalize_key(v):
    """Match Silver bigint ids to Gold keys (stored as int). Fall back to str."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return str(v)


def fetch_silver_keys(pg_dsn: str, spec: FeatureSpec, tenant: str) -> dict:
    """{key -> _s_lsn} for non-invalid Silver rows (those that should have a Gold doc)."""
    import psycopg2
    sql = (
        f"SELECT {spec.silver_key} AS k, _s_lsn FROM silver.{spec.silver_table} "
        f"WHERE _s_tenantid = %s AND invalid = FALSE"
    )
    conn = psycopg2.connect(pg_dsn)
    try:
        cur = conn.cursor()
        cur.execute(sql, (tenant,))
        out = {_normalize_key(k): (int(lsn) if lsn is not None else 0) for k, lsn in cur.fetchall()}
        cur.close()
        return out
    finally:
        conn.close()


def fetch_gold_keys(mongo_uri: str, spec: FeatureSpec, tenant: str,
                    env: str | None = None) -> dict:
    """{key -> _silverLsn} for Gold docs of this tenant (and env when given).

    When non-prod envs share a single Cosmos database (gold_bpe holds dev+qa+stg+sbx
    docs distinguished by the _env field), passing env ensures reconciliation only
    counts docs that belong to this deployment — not docs written by a different env
    for the same tenant+project.
    """
    from pymongo import MongoClient
    db_name = os.environ.get(spec.gold_db_env, spec.gold_db_default)
    cli = MongoClient(mongo_uri, serverSelectionTimeoutMS=15_000, appname="gold-reconcile")
    try:
        coll = cli[db_name][spec.gold_collection]
        query: dict = {"tenantId": tenant}
        if env:
            query["_env"] = env
        out = {}
        for d in coll.find(query, {spec.gold_key: 1, "_silverLsn": 1, "_id": 0}):
            if spec.gold_key in d:
                out[_normalize_key(d[spec.gold_key])] = d.get("_silverLsn")
        return out
    finally:
        cli.close()


def reconcile(spec: FeatureSpec, tenant: str, pg_dsn: str, mongo_uri: str,
              *, sample: int = 10, env: str | None = None) -> Drift:
    silver = fetch_silver_keys(pg_dsn, spec, tenant)
    gold = fetch_gold_keys(mongo_uri, spec, tenant, env=env)
    return compute_drift(silver, gold, sample=sample)


def _load_builder(spec: FeatureSpec):
    import importlib
    mod_name, fn_name = spec.builder.split(":")
    return getattr(importlib.import_module(mod_name), fn_name)


def fetch_gold_docs(mongo_uri: str, spec: FeatureSpec, tenant: str,
                    env: str | None = None) -> dict:
    """{key -> full Gold doc} for value comparison."""
    from pymongo import MongoClient
    db_name = os.environ.get(spec.gold_db_env, spec.gold_db_default)
    cli = MongoClient(mongo_uri, serverSelectionTimeoutMS=15_000, appname="gold-reconcile")
    try:
        coll = cli[db_name][spec.gold_collection]
        query: dict = {"tenantId": tenant}
        if env:
            query["_env"] = env
        return {_normalize_key(d[spec.gold_key]): d
                for d in coll.find(query)
                if spec.gold_key in d}
    finally:
        cli.close()


def value_check(spec: FeatureSpec, tenant: str, pg_dsn: str, mongo_uri: str,
                *, sample: int = 10, env: str | None = None) -> tuple[int, list, int]:
    """Recompute the tenant's docs from current Silver and field-diff vs stored
    Gold. Returns (n_mismatch, sample[(key, [fields])], n_compared). Advisory and
    expensive (full recompute) — opt-in via --value-check."""
    from datetime import datetime, timezone
    build = _load_builder(spec)
    expected_docs = build(pg_dsn, tenant, datetime.now(timezone.utc), project_id=None)
    expected = {_normalize_key(d[spec.gold_key]): d
                for d in expected_docs if spec.gold_key in d}
    actual = fetch_gold_docs(mongo_uri, spec, tenant, env=env)
    mism: list = []
    both = set(expected) & set(actual)
    for k in both:
        diff = compare_docs(expected[k], actual[k])
        if diff:
            mism.append((k, diff))
    return len(mism), sorted(mism, key=lambda x: str(x[0]))[:sample], len(both)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _discover_tenants(pg_dsn: str) -> list[str]:
    # Mirror gold.pipeline.tenant_discovery exactly: the reconciliation must run
    # against the SAME tenant set the pipeline runs against. Without the
    # status='active' / not-soft-deleted filters this tool scanned inactive,
    # suspended, and deleted tenants the pipeline intentionally skips, producing
    # false "Gold missing" drift reports for tenants that are correctly absent.
    import psycopg2
    conn = psycopg2.connect(pg_dsn)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT _s_tenantid
            FROM silver.tenant
            WHERE _s_tenantid <> ''
              AND _s_is_deleted = FALSE
              AND lower(status) = 'active'
        """)
        out = sorted(r[0] for r in cur.fetchall() if r[0])
        cur.close()
        return out
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(description="Silver↔Gold reconciliation / loss detection")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--tenant", help="single tenant id")
    g.add_argument("--all-tenants", action="store_true")
    p.add_argument("--feature", choices=sorted(SPECS), action="append",
                   help="feature(s) to check; default = all supported")
    p.add_argument("--sample", type=int, default=10, help="sample keys to print per drift kind")
    p.add_argument("--max-drift", type=int, default=0,
                   help="exit non-zero if loss-drift (missing+stale) exceeds this")
    p.add_argument("--value-check", action="store_true",
                   help="ALSO recompute each tenant's docs from Silver and field-diff "
                        "vs stored Gold (advisory, expensive). Mismatches add to loss-drift.")
    p.add_argument("--mongo-uri", default=None)
    p.add_argument("--pg-dsn", default=None)
    p.add_argument("--env", default=os.environ.get("PIPELINE_ENV"),
                   help="Filter Gold docs by _env (e.g. dev, qa, stg, sbx, prd). "
                        "Required when non-prod envs share a single Cosmos database. "
                        "Defaults to PIPELINE_ENV env var; omit to scan all envs.")
    ns = p.parse_args(argv)

    from secret_resolver import get_mongo_uri, get_postgres_dsn  # type: ignore[import]
    pg_dsn = ns.pg_dsn or get_postgres_dsn()
    mongo_uri = ns.mongo_uri or get_mongo_uri()
    features = ns.feature or sorted(SPECS)
    tenants = [ns.tenant] if ns.tenant else _discover_tenants(pg_dsn)
    env = ns.env or None

    total_loss = 0
    for tenant in tenants:
        for fname in features:
            spec = SPECS[fname]
            try:
                d = reconcile(spec, tenant, pg_dsn, mongo_uri, sample=ns.sample, env=env)
            except Exception as exc:   # noqa: BLE001
                log.error("reconcile failed | feature=%s tenant=%s err=%s", fname, tenant, exc)
                total_loss += 1
                continue
            total_loss += d.loss
            level = log.warning if d.total else log.info
            level(
                "reconcile | feature=%s tenant=%s expected=%d gold=%d "
                "missing=%d stale=%d orphan=%d",
                fname, tenant, d.expected, d.gold, d.n_missing, d.n_stale, d.n_orphan,
            )
            if d.missing:
                log.warning("  missing keys (sample): %s", d.missing)
            if d.stale:
                log.warning("  stale keys (sample): %s", d.stale)
            if d.orphan:
                log.warning("  orphan keys (sample): %s", d.orphan)
            _emit_metric(fname, tenant, d)

            if ns.value_check:
                try:
                    n_mism, sample_mism, n_cmp = value_check(
                        spec, tenant, pg_dsn, mongo_uri, sample=ns.sample, env=env)
                except Exception as exc:   # noqa: BLE001
                    log.error("value-check failed | feature=%s tenant=%s err=%s", fname, tenant, exc)
                else:
                    total_loss += n_mism
                    (log.warning if n_mism else log.info)(
                        "value-check | feature=%s tenant=%s compared=%d mismatched=%d",
                        fname, tenant, n_cmp, n_mism,
                    )
                    if sample_mism:
                        log.warning("  value mismatches (sample): %s", sample_mism)

    if total_loss > ns.max_drift:
        log.error("RECONCILE FAIL | loss-drift=%d > max-drift=%d — run --rebuild for the affected (feature, tenant)",
                  total_loss, ns.max_drift)
        return 1
    log.info("RECONCILE OK | loss-drift=%d (<= %d)", total_loss, ns.max_drift)
    return 0


def _emit_metric(feature: str, tenant: str, d: Drift) -> None:
    try:
        from gold.src.monitoring.metrics import record_reconciliation
        record_reconciliation(feature, tenant,
                              missing=d.n_missing, stale=d.n_stale, orphan=d.n_orphan)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    raise SystemExit(main())
