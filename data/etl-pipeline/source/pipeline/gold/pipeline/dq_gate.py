"""Post-load data quality gate — assertions after each loader step.

The gate exists to make a green pipeline run *mean* the data is real. It runs
after every successful loader step and returns the list of FATAL failure codes
(empty = passed). ``tenant_runner`` turns a non-empty result into a failed step,
which propagates to a non-zero process exit — so cron/CI actually goes red.

Checks (fatal unless noted):
  1. Broken-empty — 0 Gold docs for a tenant that HAS non-invalid Silver source
     rows. The expectation comes from Silver (the independent source of truth),
     not from the loader's own build logic, so a loader that silently builds
     nothing (e.g. a mis-scoped join) is caught. A genuinely empty tenant
     (0 Silver source rows) is NOT flagged — that is a legitimate INFO.
     Evaluated **per ``sourceEnv``**, not on tenant totals: a tenantid is only
     unique within one Silver ``_s_env``, so a total lets one env's documents
     mask another env's genuinely-empty result.
  2. Contract violation — a sample of the freshly-written Gold docs fails its
     JSON-Schema contract (missing required field / null where disallowed).
  3. Missing _grain — a required platform field is absent (platform invariant).
  4. Null/empty tenantId — a cross-tenant leak guard, scoped to the documents
     this run wrote (``pipelineRunId``) so one legacy bad document does not fail
     every tenant on every step forever.

Non-fatal by design:
  - Gate-infrastructure errors (Mongo/PG connectivity) are logged as a warning
    and return no fatal codes — a transient blip must not masquerade as a data
    failure (a false red is its own kind of lie).
  - Skipped entirely in dry_run mode (nothing was written to inspect).

SCOPE — batch write path only, not a universal Gold enforcement backstop:
  ``run_dq_gate`` is invoked exclusively from the batch loader step loop in
  ``tenant_runner.py``, after each batch loader step. Realtime-only Gold
  collections (``user_permissions``/``tenant``/``project``/``customer``/
  ``asset`` and every Eden collection fed by the two Flink streaming jobs —
  see ``gold_eden_stream.py`` / ``gold_user_permissions_stream.py``, whose own
  docstrings call the Flink sink "the SOLE writer" for those collections when
  the feature is realtime) never pass through this gate at all. A contract
  violation, missing-``_grain``,
  or null-tenant doc written by a Flink streaming sink today has **no fatal
  enforcement equivalent** — the only check on that write path is
  ``contract_validator.py``'s warn-only ``GOLD_CONTRACT_VALIDATION`` mode
  (default ``"warn"``, log-only). If this gap is ever closed, it will need
  its own gate wired into the Flink sink path (e.g.
  ``gold/src/flink/sinks/mongo_sink.py``), not an extension of this function.
"""

from __future__ import annotations

import atexit
import logging
import os
from dataclasses import dataclass

log = logging.getLogger("gold.pipeline.dq_gate")

# Number of freshly-written docs to pull back and run through the contract.
_CONTRACT_SAMPLE = 25

# Shared MongoClient reused across all DQ checks in a pipeline run.
# Keyed by URI to handle multiple Mongo targets (rare but safe).
_mongo_clients: dict[str, "MongoClient"] = {}  # type: ignore[name-defined]
atexit.register(lambda: [c.close() for c in _mongo_clients.values()])


def _get_mongo_client(mongo_uri: str) -> "MongoClient":  # type: ignore[name-defined]
    if mongo_uri not in _mongo_clients:
        from pymongo import MongoClient
        _mongo_clients[mongo_uri] = MongoClient(
            mongo_uri, serverSelectionTimeoutMS=3_000, appname="gold-dq-gate"
        )
    return _mongo_clients[mongo_uri]


@dataclass(frozen=True)
class _GateSpec:
    env_var: str          # env var holding the Gold DB name
    db_default: str       # fallback Gold DB name
    collection: str       # Gold collection
    silver_table: str | None   # 1:1 source table for the expectation check; None = skip
    contract_name: str | None  # contract file stem for sample validation; None = skip


# feature_store has contract_name=None (no contract file yet), but it carries
# tenantId (camelCase, like every other Gold collection) and _grain like the
# rest — both are checked uniformly below, no per-collection carve-out.
# silver_table is set only where the feature is 1:1 with a single Silver table
# (every non-invalid source row must yield exactly one Gold doc) — verified for
# all five project-grain Eden collections, BPE, and QA. workforce_status and
# tenant_dashboard are NOT 1:1 with one table, so they get no expectation check.
_DQ_COLLECTIONS: dict[str, _GateSpec] = {
    "load_estimate_snapshot":  _GateSpec("GOLD_BPE_DB_NAME", "gold_bpe", "feature_store", "project", None),
    "load_project_health":     _GateSpec("GOLD_EDEN_DB", "gold_eden", "project_health",    "project", "eden_project_health"),
    "load_invoice_summary":    _GateSpec("GOLD_EDEN_DB", "gold_eden", "invoice_summary",   "project", "eden_invoice_summary"),
    "load_task_intelligence":  _GateSpec("GOLD_EDEN_DB", "gold_eden", "task_intelligence", "project", "eden_task_intelligence"),
    "load_workforce_status":   _GateSpec("GOLD_EDEN_DB", "gold_eden", "workforce_status",  None,      "eden_workforce_status"),
    "load_procurement_status": _GateSpec("GOLD_EDEN_DB", "gold_eden", "procurement_status","project", "eden_procurement_status"),
    "load_bid_context":        _GateSpec("GOLD_EDEN_DB", "gold_eden", "bid_context",       "project", "eden_bid_context"),
    "load_tenant_dashboard":   _GateSpec("GOLD_EDEN_DB", "gold_eden", "tenant_dashboard",  None,      "eden_tenant_dashboard"),
    "load_qa_recommendation":  _GateSpec("GOLD_QA_RECOMMENDATION_DB", "gold_qa_recommendation", "qa_answer", "answers", "qa_answer"),
}


# ---------------------------------------------------------------------------
# Pure decision logic (no I/O — unit tested)
# ---------------------------------------------------------------------------

# (severity, code, message). severity in {"error", "warning", "info"}.
GateFinding = tuple[str, str, str]


def evaluate_dq(
    *,
    collection: str,
    gold_count: int,
    expected_count: int | None,
    missing_grain: int,
    null_tenant: int,
    contract_violations: list[str],
    per_env: dict[str | None, tuple[int, int]] | None = None,
) -> list[GateFinding]:
    """Decide gate findings from gathered counts. Pure — no I/O.

    Args:
        gold_count: Gold docs present for the tenant (all source envs).
        expected_count: non-invalid Silver source rows for the tenant, or None
            when the feature is not 1:1 with a single Silver table (no claim).
        missing_grain: Gold docs lacking _grain (-1 = check skipped).
        null_tenant: Gold docs with null/empty tenant field.
        contract_violations: distinct contract-violation messages from a sample
            of freshly-written docs.
        per_env: ``{sourceEnv: (gold_docs, expected_rows)}`` — when supplied, the
            emptiness check runs **per source env** instead of on the totals,
            because a tenantid is only unique within one ``_s_env``. Without it,
            one env's documents mask another env's genuinely-empty result. Falls
            back to the scalar totals when None or empty (a tenant with no
            Silver rows in any env has no per-env expectation to test).
    """
    findings: list[GateFinding] = []

    if per_env:
        # Per-env emptiness: each env's expectation is independently falsifiable.
        # Envs present in Gold but absent from Silver are NOT flagged — that is a
        # decommissioned/archived env's leftover documents, not a loader defect
        # (the same case cursor_store's MIN-per-env pin accepts deliberately).
        for env in sorted(per_env, key=lambda e: (e is None, e or "")):
            env_gold, env_expected = per_env[env]
            if env_gold:
                continue
            label = env if env is not None else "<unstamped>"
            if env_expected > 0:
                findings.append((
                    "error", "DQ_BROKEN_EMPTY",
                    f"sourceEnv={label}: 0 documents written but Silver has "
                    f"{env_expected} non-invalid source row(s) for this tenant — "
                    f"loader produced nothing it should have",
                ))
            else:
                findings.append((
                    "info", "DQ_ZERO_DOCS",
                    f"sourceEnv={label}: zero documents "
                    f"(no relevant Silver source rows in this env)",
                ))
    elif gold_count == 0:
        if expected_count and expected_count > 0:
            findings.append((
                "error", "DQ_BROKEN_EMPTY",
                f"0 documents written but Silver has {expected_count} non-invalid "
                f"source row(s) for this tenant — loader produced nothing it should have",
            ))
        else:
            # No expectation, or expectation is genuinely zero: legitimate empty.
            findings.append((
                "info", "DQ_ZERO_DOCS",
                "zero documents (tenant has no relevant Silver source rows)",
            ))

    for msg in contract_violations:
        findings.append(("error", "DQ_CONTRACT_VIOLATION", msg))

    if missing_grain > 0:
        findings.append((
            "error", "DQ_MISSING_GRAIN",
            f"{missing_grain} doc(s) missing required platform field _grain",
        ))

    if null_tenant > 0:
        findings.append((
            "error", "DQ_NULL_TENANT_ID",
            f"{null_tenant} doc(s) have null/empty tenant id (cross-tenant leak guard)",
        ))

    return findings


# ---------------------------------------------------------------------------
# I/O wrapper
# ---------------------------------------------------------------------------

def _silver_expected_counts(
    pg_dsn: str, table: str, tenant: str,
) -> dict[str | None, int] | None:
    """Non-invalid Silver source rows for a tenant, **grouped by ``_s_env``**.

    Returns None on any error (the expectation is then treated as 'unknown' and
    never produces a false red).

    Grouped rather than totalled because Silver is partitioned by ``_s_env`` and
    a ``tenantid`` is only unique *within* one env — the same reason every Gold
    loader folds ``sourceEnv`` into its upsert key and every cursor takes
    MIN-of-per-env-MAX (see cursor_store.get_max_silver_ts_per_env). A single
    total lets one env's documents satisfy the broken-empty check on behalf of
    another env that produced nothing: the loader is red for env B, but the
    gate sees B's zero docs covered by A's rows and passes. Per-env counts make
    each env's expectation independently falsifiable.

    The ``invalid`` filter treats NULL as "not superseded", matching the
    loaders (see load_task_intelligence._resolved_query's docstring): on some
    tenants the column exists but every row's value is NULL rather than
    TRUE/FALSE (live-verified 36795/36795 on tn24047750, where the ASE 2.0
    migration stopped populating it without backfilling FALSE). A plain
    ``invalid = FALSE`` evaluates to NULL — i.e. excludes the row — so the
    count came back 0 for exactly the tenants whose Gold docs were most at
    risk, and ``evaluate_dq`` then took the DQ_ZERO_DOCS *info* branch instead
    of DQ_BROKEN_EMPTY *error*: the gate silently lost its ability to catch a
    loader that produced nothing. The column is also skipped entirely when
    absent from Silver (same self-healing probe the loaders use), so a
    migration-window schema gap downgrades the expectation to "all rows"
    rather than throwing and discarding it via the except below.
    """
    try:
        import psycopg2

        from gold.pipeline.silver_schema import get_silver_columns

        invalid_filter = (
            "AND (invalid IS NULL OR invalid = FALSE)"
            if "invalid" in get_silver_columns(pg_dsn, table)
            else ""
        )
        conn = psycopg2.connect(pg_dsn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT _s_env, COUNT(*) FROM silver.{table} "
                    f"WHERE _s_tenantid = %s {invalid_filter} "
                    f"GROUP BY _s_env",
                    (tenant,),
                )
                return {row[0]: int(row[1]) for row in cur.fetchall()}
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("DQ expectation count skipped (non-fatal) | table=%s tenant=%s — %s",
                    table, tenant, exc)
        return None


def _sample_contract_violations(coll, tenant_field: str, tenant: str, contract_name: str) -> list[str]:
    """Run a sample of the tenant's written docs through the contract validator.
    Returns distinct violation messages (capped)."""
    from gold.src.monitoring.contract_validator import ContractValidator
    validator = ContractValidator(contract_name, mode="warn")
    seen: list[str] = []
    for doc in coll.find({tenant_field: tenant}).limit(_CONTRACT_SAMPLE):
        violation = validator.check(doc)
        if violation and violation not in seen:
            seen.append(violation)
            if len(seen) >= 3:
                break
    return seen


def run_dq_gate(
    step_name: str,
    tenant: str,
    mongo_uri: str | None,
    dry_run: bool,
    run_id: str | None = None,
    pg_dsn: str | None = None,
) -> list[str]:
    """Run post-load DQ assertions for a completed loader step.

    Returns the list of FATAL failure codes (empty = the step's data passed).
    """
    if dry_run or step_name not in _DQ_COLLECTIONS:
        return []
    if not mongo_uri:
        return []

    spec = _DQ_COLLECTIONS[step_name]
    db_name = os.environ.get(spec.env_var, spec.db_default)
    coll_name = spec.collection
    # All Gold collections use camelCase tenantId -- no special-casing needed.
    tenant_field = "tenantId"

    try:
        from gold.pipeline.audit import record_event
        from gold.pipeline.step_registry import FEATURE_REGISTRY

        def _feature(sname: str) -> str:
            for feat, desc in FEATURE_REGISTRY.items():
                if sname in desc.step_names:
                    return feat
            return "unknown"

        client = _get_mongo_client(mongo_uri)
        coll = client[db_name][coll_name]

        gold_count = coll.count_documents({tenant_field: tenant})

        # Expectation is gathered per source env (see _silver_expected_counts);
        # the total is kept only for the scalar fallback and the audit metadata.
        silver_per_env = (
            _silver_expected_counts(pg_dsn, spec.silver_table, tenant)
            if spec.silver_table and pg_dsn else None
        )
        expected_count: int | None = (
            sum(silver_per_env.values()) if silver_per_env is not None else None
        )

        # Pair each env's Silver expectation with that env's own Gold doc count.
        # Counted per env in a loop rather than via a $group aggregation: the env
        # count is small (one per enabled source env in environments.yaml) and
        # count_documents is the one read path guaranteed across Cosmos's Mongo
        # API surface. sourceEnv=None matches both null and missing, which is
        # what we want for rows predating multi-env stamping.
        per_env: dict[str | None, tuple[int, int]] | None = None
        if silver_per_env:
            per_env = {
                env: (
                    coll.count_documents({tenant_field: tenant, "sourceEnv": env}),
                    rows,
                )
                for env, rows in silver_per_env.items()
            }

        # All Gold collections including feature_store carry _grain.
        missing_grain = coll.count_documents(
            {tenant_field: tenant, "_grain": {"$exists": False}}
        )

        # Cross-tenant leak guard. A doc with a null/empty tenantId cannot be
        # attributed to a tenant by definition, so this cannot be tenant-scoped —
        # but it CAN be scoped to the documents this run wrote (every loader
        # stamps pipelineRunId via lineage_fields). Unscoped, a single legacy bad
        # document failed the gate for every tenant on every step for the rest of
        # the collection's life, which trains operators to ignore the code. With
        # no run_id (ad-hoc loader invocation, or a streaming-written doc where
        # current_run_id() is unset) it falls back to the collection-wide count.
        leak_filter: dict = {tenant_field: {"$in": [None, ""]}}
        if run_id:
            leak_filter["pipelineRunId"] = run_id
        null_tenant = coll.count_documents(leak_filter)

        contract_violations: list[str] = []
        if spec.contract_name and gold_count > 0:
            contract_violations = _sample_contract_violations(
                coll, tenant_field, tenant, spec.contract_name
            )

        findings = evaluate_dq(
            collection=coll_name,
            gold_count=gold_count,
            expected_count=expected_count,
            missing_grain=max(missing_grain, 0),
            null_tenant=null_tenant,
            contract_violations=contract_violations,
            per_env=per_env,
        )

        fatal_codes: list[str] = []
        for severity, code, message in findings:
            event_type = {"error": "dq_failed", "warning": "dq_warning"}.get(severity, "dq_info")
            status = {"error": "failed", "warning": "warning"}.get(severity, "info")
            log_fn = {"error": log.error, "warning": log.warning}.get(severity, log.info)
            log_fn("DQ [%s.%s] tenant=%s — %s", db_name, coll_name, tenant, message)
            record_event(
                mongo_uri, run_id,
                tenant_id=tenant,
                feature=_feature(step_name),
                step_name=step_name,
                event_type=event_type,
                status=status,
                error_code=code,
                error_message=message,
                metadata={"db": db_name, "collection": coll_name,
                          "gold_count": gold_count, "expected_count": expected_count,
                          # Per-env breakdown so the audit trail shows WHICH env
                          # was empty, not just that the tenant total was short.
                          "per_env": (
                              {(env or "<unstamped>"): {"gold": g, "expected": e}
                               for env, (g, e) in per_env.items()}
                              if per_env else None
                          )},
            )
            if severity == "error":
                fatal_codes.append(code)

        if not fatal_codes:
            log.debug("DQ gate passed | %s.%s tenant=%s", db_name, coll_name, tenant)
        return fatal_codes

    except Exception as exc:  # noqa: BLE001
        # Gate-infrastructure failure (e.g. cannot reach Mongo) is NOT a data
        # failure — never turn a connectivity blip into a false red.
        log.warning("DQ gate skipped (non-fatal infra error): %s — %s", step_name, exc)
        return []
