"""Gold wipe — clears Cosmos DB Gold collections before a pipeline run.

Companion to Bronze/Silver's scripts/wipe_pipeline_data.py, scoped to the Gold
layer's Cosmos DB (MongoDB API) collections instead of Iceberg/ADLS/Postgres.
There is no Gold equivalent of wipe_pipeline_data.py in this repo prior to
this module — see CLAUDE.md's Gold section.

Two modes, chosen by whether the run targets one/some tenants or --all-tenants:

  scoped — a specific --tenant (or a resolved subset): deletes only documents
           matching those tenantId values, from the collections in scope for
           this run (respects --feature and gold_config.yml's
           enabled_collections allowlist). Leaves the collection itself and
           every other tenant's documents untouched.
  full   — --all-tenants: drops ALL documents from every in-scope collection
           (`delete_many({})`), regardless of tenant. Destructive on a SHARED
           cluster (cosdbvc-fieldcraft-etl-ncus-dev-001 — see memory
           gold_mongo_cluster.md) that other features/tenants may depend on.
           Requires typing a confirmation phrase, and a second phrase when
           ETL_ENV=prod — mirroring wipe_pipeline_data.py's guard.

Global (non-tenant-scoped) collections — currently only canonical.question,
the provider-owned Q&A template catalog stamped with the constant tenantId
"fieldcraft_provider" (see CLAUDE.md's canonical question/answer section) — are never
touched by a scoped wipe, since no real tenant ID would match them anyway;
only a full wipe drops them.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("gold.pipeline.wipe")

# Static map of feature -> Mongo collections it owns. Kept in sync by hand
# with each feature's setup_gold_*.py — not discovered via
# list_collection_names(), since that would only enumerate collections that
# already exist, which is backwards for a wipe that runs before a fresh
# deployment's first setup step.
FEATURE_COLLECTIONS: dict[str, list[str]] = {
    "bpe": ["feature_store", "model_registry", "model_config", "encoder_catalog"],
    "eden": [
        "project_health", "invoice_summary", "task_intelligence", "workforce_status",
        "procurement_status", "bom_stock_gap", "weather_schedule_cross", "bid_context",
        "tenant_dashboard", "knowledge_chunk", "task_timeline", "tool_execution",
        "recommendation", "agent_audit", "agent_memory", "agent_plan",
    ],
    "qa_recommendation": ["qa_answer"],
    "canonical": ["user_permissions", "tenant", "customer", "project", "asset", "question", "answer"],
}

# Collections with no per-tenant scoping (global reference data) — excluded
# from scoped (per-tenant) wipes; only a full wipe touches them.
GLOBAL_COLLECTIONS: set[str] = {"question"}


def _feature_db_map(bpe_db: str, eden_db: str, qa_recommendation_db: str, canonical_db: str) -> dict[str, str]:
    return {
        "bpe": bpe_db,
        "eden": eden_db,
        "qa_recommendation": qa_recommendation_db,
        "canonical": canonical_db,
    }


def _target_collections(feature_filter: str | None) -> dict[str, list[str]]:
    """Return {feature: [collection names in scope]} honoring gold_config.yml."""
    from gold.pipeline.step_registry import FEATURE_REGISTRY, is_feature_enabled, is_collection_enabled

    out: dict[str, list[str]] = {}
    features = [feature_filter] if feature_filter else sorted(FEATURE_REGISTRY)
    for feat in features:
        if feat not in FEATURE_REGISTRY or not is_feature_enabled(feat):
            continue
        cols = [c for c in FEATURE_COLLECTIONS.get(feat, []) if is_collection_enabled(feat, c)]
        if cols:
            out[feat] = cols
    return out


def _redact_uri(mongo_uri: str) -> str:
    return mongo_uri.split("@")[-1] if "@" in mongo_uri else mongo_uri


def wipe_gold_data(
    mongo_uri: str,
    tenants: list[str],
    all_tenants: bool,
    feature_filter: str | None,
    bpe_db: str,
    eden_db: str,
    qa_recommendation_db: str,
    canonical_db: str,
    dry_run: bool = False,
    assume_yes: bool = False,
    source_env: str | None = None,
) -> int:
    """Wipe Gold Cosmos DB data ahead of a pipeline run.

    Returns 0 on success (including a no-op dry-run or confirmed abort by the
    caller's own dry_run flag), 2 if the user declines confirmation or a
    connection/argument error occurs — matching wipe_pipeline_data.py's
    exit-code convention so start_pipeline.sh-style `|| warn` chaining works.

    source_env: when set, folds `{"sourceEnv": source_env}` into a SCOPED
    (non --all-tenants) wipe's delete query. Gold documents are keyed by
    (tenantId, sourceEnv) — see gold/pipeline/lineage.py — because a
    tenantId is only unique within one Silver source environment. Without
    this, a scoped wipe intended to clear one env's copy of a tenant's data
    (e.g. before a rebuild in si_dev) silently deletes that same tenantId's
    documents from every other env too (staging/prod included), on the
    shared Cosmos cluster. Has no effect on a full (--all-tenants) wipe,
    which is already unscoped by design.
    """
    targets = _target_collections(feature_filter)
    if not targets:
        log.warning("wipe: no enabled features/collections in scope — nothing to do")
        return 0

    db_map = _feature_db_map(bpe_db, eden_db, qa_recommendation_db, canonical_db)
    etl_env = os.environ.get("ETL_ENV", "local")
    scope_desc = (
        "ALL TENANTS — full collection drop"
        if all_tenants
        else f"{len(tenants)} tenant(s): {tenants}"
    )

    print("\n[WIPE] Gold Cosmos DB wipe requested")
    print(f"[WIPE]   Mongo cluster : {_redact_uri(mongo_uri)}")
    print(f"[WIPE]   Scope         : {scope_desc}")
    print(f"[WIPE]   Source env    : {source_env or '(all envs — no sourceEnv filter)'}")
    print(f"[WIPE]   ETL_ENV       : {etl_env}")
    for feat, cols in targets.items():
        print(f"[WIPE]   {db_map[feat]}: {', '.join(cols)}")
    if all_tenants and source_env:
        print(
            f"[WIPE]   WARNING       : --all-tenants ignores --source-env "
            f"({source_env!r} will NOT be used to scope this wipe). This is a "
            f"full, UNSCOPED delete_many({{}}) across every environment sharing "
            f"this Cosmos cluster — it will also wipe other environments' "
            f"(e.g. staging/prod) data in the collections listed above, not just {source_env!r}."
        )

    if dry_run:
        print("[WIPE] --dry-run — no data will be deleted\n")
        return 0

    if not assume_yes:
        confirm = input("\nType 'WIPE GOLD' to confirm: ")
        if confirm != "WIPE GOLD":
            print("Aborted — confirmation phrase did not match.")
            return 2
        if etl_env == "prod":
            confirm2 = input("This is PRODUCTION. Type 'WIPE PRODUCTION' to confirm: ")
            if confirm2 != "WIPE PRODUCTION":
                print("Aborted — production confirmation phrase did not match.")
                return 2

    try:
        from pymongo import MongoClient
    except ImportError:
        log.error("pymongo not installed — cannot wipe Gold data")
        return 2

    client = MongoClient(mongo_uri)
    try:
        for feat, cols in targets.items():
            db = client[db_map[feat]]
            for coll_name in cols:
                if not all_tenants and coll_name in GLOBAL_COLLECTIONS:
                    log.info("wipe: skipping global collection %s.%s in scoped wipe", db.name, coll_name)
                    continue
                coll = db[coll_name]
                if all_tenants:
                    query = {}
                else:
                    query = {"tenantId": {"$in": tenants}}
                    if source_env:
                        query["sourceEnv"] = source_env
                result = coll.delete_many(query)
                log.info(
                    "wipe: %s.%s — %d doc(s) removed%s%s",
                    db.name, coll_name, result.deleted_count,
                    "" if all_tenants else f" for tenants={tenants}",
                    "" if all_tenants or not source_env else f" sourceEnv={source_env}",
                )
    finally:
        client.close()

    print("[WIPE] done.\n")
    return 0
