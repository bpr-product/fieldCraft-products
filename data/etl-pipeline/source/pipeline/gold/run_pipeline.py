"""Gold pipeline — entry point.

Orchestrates Silver → Gold batch processing for all tenants.
Processing logic lives in gold/pipeline/:

  tenant_discovery  — find active tenants from Silver PostgreSQL
  step_registry     — ordered loader steps per tenant
  tenant_runner     — execute steps with timeout + DQ gate
  pipeline_runner   — multi-tenant loop + Prometheus metrics

Architecture — two execution modes:

  BATCH (this file — cron every 15 min on VM)
  ─────────────────────────────────────────────
  Freshness SLA : 15 min
  Transport     : psycopg2 SELECT → pymongo bulk upsert

  BPE (gold_bpe):
    1. setup_gold_feat_bpe
    2. load_estimate_snapshot       silver.project + BOM     → feature_store

  Eden AI Assistant:
    10. setup_gold_eden
    11. load_project_health
    12. load_invoice_summary
    13. load_task_intelligence
    14. load_procurement_status
    15. load_bid_context
    16. load_tenant_dashboard

  STREAMING (Flink jobs — always on via systemd)
  ───────────────────────────────────────────────
  Freshness SLA : < 2 s
  Transport     : Kafka CDC (local.silver.cdc.events) → PyFlink DataStream → Cosmos DB

Usage:
  python gold/run_pipeline.py --tenant <id>
  python gold/run_pipeline.py --all-tenants
  python gold/run_pipeline.py --all-tenants --dry-run
  python gold/run_pipeline.py --all-tenants --skip-setup
  python gold/run_pipeline.py --list-tenants
  python gold/run_pipeline.py --tenant <id> --wipe --dry-run   # preview a scoped wipe
  python gold/run_pipeline.py --tenant <id> --wipe             # wipe that tenant, then run
  python gold/run_pipeline.py --all-tenants --wipe             # full collection drop, then run

--wipe (see gold/pipeline/wipe.py) clears Gold Cosmos DB collections before the
loaders run. Scoped to the resolved tenant(s) unless --all-tenants is also
given, in which case it drops every document in every in-scope collection —
destructive on the shared Cosmos cluster. Always prompts for a typed
confirmation unless --yes is passed; --dry-run previews scope without
deleting or running loaders.

Exit codes:
  0  all loaders succeeded (or --wipe --dry-run preview completed)
  1  one or more loaders failed
  2  configuration / connection error, or --wipe confirmation declined
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

# Load .env.local before any other imports so env vars are available pipeline-wide
# __file__ is source/pipeline/gold/run_pipeline.py -- climb 4 levels to reach
# src/datapipeline/.env.local (2 levels only reaches source/pipeline/.env.local,
# a nonexistent path -- see create_silver_tables.py's identical fix).
_ENV_FILE = Path(__file__).resolve().parent.parent.parent.parent / ".env.local"
if _ENV_FILE.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_FILE, override=False)
    except ImportError:
        pass  # dotenv not installed — rely on shell env

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SHARED    = str(_REPO_ROOT / "shared")
for _p in (str(_REPO_ROOT), _SHARED):
    if _p not in sys.path:
        sys.path.insert(0, _p)

log = logging.getLogger("gold.pipeline")


def main(argv: list[str] | None = None) -> int:
    # Install rich logging early (INFO by default) so any import-time warnings
    # are formatted correctly.  Reconfigured below once --log-level is parsed.
    from gold.pipeline.logging_config import configure_logging
    configure_logging()

    from gold.pipeline.step_registry import FEATURE_REGISTRY
    feature_choices = sorted(FEATURE_REGISTRY)

    p = argparse.ArgumentParser(
        description="Gold pipeline: Silver → Cosmos DB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python gold/run_pipeline.py --all-tenants --skip-setup\n"
            "  python gold/run_pipeline.py --feature bpe --tenant DEMO-001 --dry-run\n"
            "  python gold/run_pipeline.py --feature eden --all-tenants --log-level DEBUG\n"
            "  python gold/run_pipeline.py --list-features\n"
        ),
    )

    tenant_group = p.add_mutually_exclusive_group()
    tenant_group.add_argument("--tenant",        help="Single tenant ID")
    tenant_group.add_argument("--all-tenants",   action="store_true",
                              help="Discover all tenants from Silver and run for each")
    tenant_group.add_argument("--list-tenants",  action="store_true",
                              help="Print tenants found in Silver and exit")
    tenant_group.add_argument("--list-features", action="store_true",
                              help="Print available features and their steps, then exit")

    p.add_argument(
        "--source-env",
        default=None,
        help=(
            "Silver _s_env value to scope a --wipe to (e.g. si_dev, datamart). "
            "Gold documents are keyed by (tenantId, sourceEnv) -- a tenantId is "
            "only unique within one Silver source environment. Without this, a "
            "scoped (non --all-tenants) wipe deletes a tenant's documents "
            "across every source env at once. Has no effect on --all-tenants "
            "(already unscoped) or outside --wipe."
        ),
    )

    p.add_argument(
        "--feature",
        choices=feature_choices,
        metavar="FEATURE",
        default=None,
        help=(
            f"Run only a single feature in isolation.  Choices: {', '.join(feature_choices)}. "
            "Omit to run all enabled features."
        ),
    )
    p.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=os.environ.get("LOG_LEVEL", "INFO").upper(),
        metavar="LEVEL",
        help="Log verbosity (default: INFO, or LOG_LEVEL env var)",
    )

    p.add_argument("--mongo-uri",  default=None)
    p.add_argument("--pg-dsn",     default=None)
    p.add_argument("--bpe-db",     default=os.environ.get("GOLD_BPE_DB_NAME", "gold_bpe"))
    p.add_argument("--batch-size", type=int, default=int(os.environ.get("BATCH_SIZE", "200")))
    p.add_argument("--dry-run",    action="store_true", help="Skip all MongoDB writes")
    p.add_argument("--skip-setup", action="store_true", help="Skip collection/index setup steps")
    p.add_argument("--skip-eden",  action="store_true", help="Skip Eden AI Assistant steps")
    p.add_argument(
        "--wipe", action="store_true",
        help="Clear Gold Cosmos DB collections before running (see gold/pipeline/wipe.py). "
             "With --tenant/a resolved tenant subset: deletes only that tenant's documents "
             "(tenantId-scoped) from the in-scope collections. With --all-tenants: drops ALL "
             "documents from every in-scope collection — destructive on the shared Cosmos "
             "cluster. Prompts for a typed confirmation phrase unless --yes is also passed. "
             "Always --dry-run first to preview scope.",
    )
    p.add_argument(
        "--yes", action="store_true",
        help="Skip the interactive confirmation prompt for --wipe (for scripted/CI use only).",
    )
    p.add_argument("--rebuild",    action="store_true",
                   help="On-demand rebuild (R1): re-run the loaders for REALTIME features "
                        "to refresh Gold from current Silver — recovers a gap the stream "
                        "missed (realtime features have no batch reconciliation). Rebuilds "
                        "only streaming-owned collections (BPE feature_store, qa_answer, "
                        "Eden). Idempotent; safe while streams run. "
                        "Always --dry-run first.")
    p.add_argument("--tenant-timeout", type=int, default=None,
                   metavar="SECONDS",
                   help="Wall-clock seconds per tenant; explicit value overrides the "
                        "config-resolved SLA for ALL tenants (0 = no limit). "
                        "Default: resolve per tenant from gold_config.yml sla: block.")
    p.add_argument("--max-docs", type=int,
                   default=int(os.environ.get("MAX_DOCS_PER_TENANT", "0")),
                   metavar="N",
                   help="Max Silver rows per tenant per run (0 = unlimited)")
    p.add_argument(
        "--isolate-features",
        action="store_true",
        help=(
            "Run each feature in a separate subprocess for fault isolation. "
            "A crash or OOM in one feature will not abort others."
        ),
    )

    # Eden AI Assistant database (single db, 6 collections)
    p.add_argument("--eden-db", default=os.environ.get("GOLD_EDEN_DB", "gold_eden"))
    p.add_argument("--qa-recommendation-db", default=os.environ.get("GOLD_QA_RECOMMENDATION_DB", "gold_qa_recommendation"))

    ns = p.parse_args(argv)

    # Reconfigure logging now that we have the explicit --log-level value
    configure_logging(level=ns.log_level)

    # --list-features — no connection needed
    if ns.list_features:
        print("\nAvailable features (use with --feature <name>):\n")
        for name, desc in sorted(FEATURE_REGISTRY.items()):
            steps_str = ", ".join(sorted(desc.step_names))
            print(f"  {name:<16} {desc.description}")
            print(f"  {'':16} steps: {steps_str}")
            print()
        return 0

    # When --feature eden is given, --skip-eden would produce 0 steps — override.
    if ns.feature == "eden" and ns.skip_eden:
        log.warning("--skip-eden ignored when --feature=eden is active")
        ns.skip_eden = False

    # Resolve secrets early
    pg_dsn = ns.pg_dsn
    if pg_dsn is None:
        try:
            from secret_resolver import get_postgres_dsn
            pg_dsn = get_postgres_dsn()
        except RuntimeError as exc:
            log.error("cannot resolve Silver PG DSN: %s", exc)
            return 2

    mongo_uri = ns.mongo_uri
    if mongo_uri is None:
        try:
            from secret_resolver import get_mongo_uri
            mongo_uri = get_mongo_uri()
        except RuntimeError as exc:
            log.error("cannot resolve Mongo URI: %s", exc)
            return 2

    # Tenant discovery
    from gold.pipeline.tenant_discovery import discover_tenants

    if ns.list_tenants:
        try:
            tenants = discover_tenants(pg_dsn)
        except Exception as exc:
            log.error("failed to query Silver: %s", exc)
            return 2
        print(f"Found {len(tenants)} tenant(s):")
        for t in tenants:
            print(f"  {t}")
        return 0

    if ns.all_tenants:
        try:
            tenants = discover_tenants(pg_dsn)
        except Exception as exc:
            log.error("failed to discover tenants: %s", exc)
            return 2
        if not tenants:
            # Applies even under --wipe: tenant_discovery.py's own docstring
            # says a 0-tenant result is "almost always a roster/replication
            # problem", not a genuine no-active-tenants state. --wipe
            # previously bypassed this guard, so a transient Silver outage
            # during a scheduled `--all-tenants --wipe --yes` job could fully
            # empty every in-scope Gold collection (wipe.py's full-wipe query
            # is `{}` — an empty tenants list has no scoping effect) with no
            # human catching the anomalous count. Refuse outright instead.
            log.warning("no tenants found in Silver — nothing to do"
                        + (" (refusing --wipe on a 0-tenant result — this is "
                           "very likely a discovery/connectivity problem, not "
                           "a genuine empty state)" if ns.wipe else ""))
            return 0
    elif ns.tenant:
        tenants = [ns.tenant]
    else:
        p.error("one of --tenant, --all-tenants, or --list-tenants is required")

    if ns.wipe:
        from gold.pipeline.wipe import wipe_gold_data

        canonical_db = os.environ.get("GOLD_CANONICAL_DB", "gold_canonical")
        wipe_rc = wipe_gold_data(
            mongo_uri=mongo_uri,
            tenants=tenants,
            all_tenants=ns.all_tenants,
            feature_filter=ns.feature,
            bpe_db=ns.bpe_db,
            eden_db=ns.eden_db,
            qa_recommendation_db=ns.qa_recommendation_db,
            canonical_db=canonical_db,
            dry_run=ns.dry_run,
            assume_yes=ns.yes,
            source_env=ns.source_env,
        )
        if wipe_rc != 0:
            return wipe_rc
        if ns.dry_run:
            # --wipe --dry-run only previews the wipe scope; it does not also
            # run the loaders (which are already no-op under --dry-run).
            return 0

    # --isolate-features: spawn one subprocess per feature
    if ns.isolate_features:
        from gold.pipeline.step_registry import FEATURE_REGISTRY, is_feature_enabled

        # Build the argv shared across all feature subprocesses (omit --isolate-features
        # and --feature so each child process sets its own --feature). Also omit
        # --wipe/--yes: the wipe already ran once in THIS (parent) process above,
        # before any subprocess is spawned. Without stripping them, every child
        # re-enters run_pipeline.main() and re-runs the wipe logic a second time
        # (including its own confirmation input() call) -- under non-interactive
        # automation without --yes this raises EOFError in the child, reporting
        # every feature FAILED for a reason unrelated to loader correctness; even
        # with --yes, each child needlessly re-wipes, doubling Cosmos delete_many
        # calls.
        base_argv = [a for a in (argv or sys.argv[1:])
                     if a not in ("--isolate-features", "--wipe", "--yes")
                     and not a.startswith("--feature")]

        enabled_features = [
            name for name in sorted(FEATURE_REGISTRY)
            if (ns.feature is None or ns.feature == name) and is_feature_enabled(name)
        ]
        any_failed = False
        for feature in enabled_features:
            child_argv = [sys.executable, "-m", "gold.pipeline.feature_runner",
                          "--feature", feature] + base_argv
            log.info("spawning subprocess for feature=%s", feature)
            result = subprocess.run(child_argv)
            if result.returncode != 0:
                log.error("feature=%s subprocess exited with code %d", feature, result.returncode)
                any_failed = True
            else:
                log.info("feature=%s subprocess completed successfully", feature)
        return 1 if any_failed else 0

    # Run pipeline
    from gold.pipeline.pipeline_runner import run_pipeline

    return run_pipeline(
        tenants=tenants,
        mongo_uri=mongo_uri,
        pg_dsn=pg_dsn,
        bpe_db=ns.bpe_db,
        batch_size=ns.batch_size,
        dry_run=ns.dry_run,
        skip_setup=ns.skip_setup,
        tenant_timeout_s=ns.tenant_timeout,
        max_docs_per_tenant=ns.max_docs,
        eden_db=ns.eden_db,
        skip_eden=ns.skip_eden,
        qa_recommendation_db=ns.qa_recommendation_db,
        feature_filter=ns.feature,
        force_batch=ns.rebuild,
    )


if __name__ == "__main__":
    raise SystemExit(main())
