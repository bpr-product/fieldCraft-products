"""Step registry — builds the ordered list of loader steps per tenant.

Each step is a (name, callable) tuple. The callable takes no arguments
and returns an exit code (0 = success, non-zero = failure).

Adding a new loader:
  1. Add its profile to JOB_PROFILES below
  2. Define a _load_<name>() closure in build_steps()
  3. Append _step("load_<name>", _load_<name>) to the steps list
  No changes needed in run_pipeline.py or any other file.

Job classification rules:
  heavy  → joins 3+ tables OR calls external API per record
           → gets longer timeout, monitored for slow runs
  medium → joins 2-3 tables, no external API, medium record count
  light  → single aggregation or simple query, no external API

  batch   → freshness SLA >= 5 min  → runs via cron every 15 min
  realtime → freshness SLA < 5 min  → runs via Flink streaming (always on)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypedDict

log = logging.getLogger("gold.pipeline.step_registry")

# Canonical DBs whose indexes/shard-key/validator have been ensured at least
# once in this process. setup_gold_canonical.main() is documented as
# idempotent (safe to re-run), so this is a cheap process-lifetime cache, not
# a correctness gate on its own — it exists so a --skip-setup run still gets
# the unique indexes created before the FIRST write to a brand-new canonical
# deployment, closing the duplicate-write race that existed when index
# creation was reachable only via the --skip-setup-gated explicit step below.
_canonical_setup_done: set[str] = set()


def _ensure_canonical_setup_once(db_name: str) -> None:
    """Lazily run setup_gold_canonical.main() once per (process, db_name).

    Called unconditionally from every canonical loader closure — including
    when --skip-setup is passed — so the unique indexes the loaders' upserts
    rely on for idempotency are guaranteed to exist before any canonical
    write, regardless of whether the explicit "setup_gold_canonical" step ran
    earlier in this same build_steps() call.
    """
    if db_name in _canonical_setup_done:
        return
    from gold.src.canonical.setup_gold_canonical import main as _setup_main
    orig = os.environ.get("GOLD_CANONICAL_DB")
    os.environ["GOLD_CANONICAL_DB"] = db_name
    try:
        _setup_main()
    finally:
        if orig is None:
            os.environ.pop("GOLD_CANONICAL_DB", None)
        else:
            os.environ["GOLD_CANONICAL_DB"] = orig
    _canonical_setup_done.add(db_name)


# ── feature registry — extensibility anchor ──────────────────────────────────
#
# Adding a new feature:
#   1. Add a FeatureDescriptor entry to FEATURE_REGISTRY below.
#   2. Add its step closures to build_steps() — no other file needs touching.
#   3. Add an entry in infra/config/gold_config.yml under the feature key.
#
# The registry is used by:
#   • --feature / --list-features CLI args in run_pipeline.py
#   • build_steps(feature_filter=...) to slice the step list
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FeatureDescriptor:
    """Static metadata for a Gold feature.  Added to FEATURE_REGISTRY at definition time."""
    description: str
    step_names: frozenset[str]


FEATURE_REGISTRY: dict[str, FeatureDescriptor] = {
    "bpe": FeatureDescriptor(
        description="Ball Park Estimate — project cost estimation with vector similarity",
        step_names=frozenset({
            "setup_gold_feat_bpe",
            "load_estimate_snapshot",
        }),
    ),
    "eden": FeatureDescriptor(
        description="Eden AI Assistant — project health, invoice/task intelligence, tenant dashboard, RAG knowledge base",
        step_names=frozenset({
            "setup_gold_eden",
            "load_project_health",
            "load_invoice_summary",
            "load_task_intelligence",
            "load_workforce_status",
            "load_procurement_status",
            "load_bom_stock_gap",
            "load_estimate_price_evidence",
            "load_schedule_move_evidence",
            "load_weather_schedule_cross",
            "load_bid_context",
            "load_tenant_dashboard",
            "load_knowledge_chunk",
            "load_task_timeline",
        }),
    ),
    "qa_recommendation": FeatureDescriptor(
        description="Q&A Recommendation — answer history with transformed Q&A data per project",
        step_names=frozenset({
            "load_qa_recommendation",
        }),
    ),
    "canonical": FeatureDescriptor(
        description="Canonical Gold — cross-feature reference data owned by the platform, not one feature. "
                     "user_permissions: resolved effective roles/permissions/project membership per user. "
                     "tenant/customer/project/asset: lean reference records (added 2026-07-11) so eden/bpe/"
                     "qa_recommendation stop re-deriving the same tenant/customer/project fields independently. "
                     "question/answer (added 2026-07-16): global provider-owned Q&A template catalog + lean "
                     "answer passthrough with a precomputed templateId FK, so Eden/qa_recommendation/future "
                     "features stop re-parsing the ODM template and re-joining trade_oid+version independently. "
                     "lead/communication_log: CRM data (docs/crm/eden_crm_gold_requirements.md G2/G5) — "
                     "pre-conversion lead records and communication metadata, realtime-only from the start. "
                     "lead.serviceName is resolved via a LEFT JOIN directly inside load_lead.py's own Silver "
                     "query (G3a) — no separate service_lookup collection.",
        step_names=frozenset({
            "setup_gold_canonical",
            "load_user_permissions",
            "load_tenant",
            "load_customer",
            "load_project",
            "load_asset",
            "load_question",
            "load_answer",
            "load_lead",
            "load_communication_log",
        }),
    ),
}

# Single unified config — override via GOLD_CONFIG_PATH env var
_DEFAULT_CONFIG = (
    Path(__file__).resolve().parents[2]
    / "infra" / "config" / "gold_config.yml"
)


class JobProfile(TypedDict):
    type: str           # "batch" | "realtime"
    weight: str         # "heavy" | "medium" | "light"
    table_joins: int    # number of Silver tables joined
    external_api: bool  # True if calls external API per record
    freshness_sla: int  # seconds — max acceptable data age
    reason: str         # human-readable classification justification
    depends_on: list[str]  # step names that must succeed before this step runs


def _load_gold_config() -> dict:
    """Load unified Gold config from gold_config.yml.

    Override path via GOLD_CONFIG_PATH env var.
    Falls back to empty dict — all features run by default.
    """
    config_path = Path(os.environ.get("GOLD_CONFIG_PATH", str(_DEFAULT_CONFIG)))
    if not config_path.exists():
        log.warning("gold_config.yml not found at %s — all features enabled, steps unclassified", config_path)
        return {}
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        log.debug("loaded gold_config.yml from %s", config_path)
        return data
    except Exception as exc:
        log.warning("failed to load gold_config.yml: %s — using defaults", exc)
        return {}


# Loaded once at import time
_GOLD_CONFIG: dict = _load_gold_config()


def gold_config() -> dict:
    """Return the loaded gold_config.yml as a dict (empty if absent).

    Public accessor so other modules (e.g. the SLA resolver) read the same
    single in-memory config without re-parsing the file.
    """
    return _GOLD_CONFIG


# Loader-level profiles (under gold_config.yml "loaders:" key)
JOB_PROFILES: dict[str, JobProfile] = _GOLD_CONFIG.get("loaders", {})


def get_profile(step_name: str) -> JobProfile | None:
    """Return the loader profile for a step."""
    profile = JOB_PROFILES.get(step_name)
    if profile is not None:
        profile.setdefault("depends_on", [])  # type: ignore[attr-defined]
    return profile


def is_feature_enabled(feature: str) -> bool:
    """Check if a Gold feature is enabled in gold_config.yml.

    Falls back to True if config missing or feature not listed —
    so all features run by default without any config file.
    """
    if not _GOLD_CONFIG:
        return True
    cfg = _GOLD_CONFIG.get(feature, {})
    enabled = cfg.get("enabled", True)
    if not enabled:
        log.info("feature '%s' disabled in gold_config.yml — skipping", feature)
    return bool(enabled)


def is_heavy(step_name: str) -> bool:
    """True if the step is classified as heavy."""
    p = get_profile(step_name)
    return p is not None and p.get("weight") == "heavy"


def is_realtime(feature: str) -> bool:
    """True if the feature should run as a real-time Flink job."""
    cfg = _GOLD_CONFIG.get(feature, {})
    return cfg.get("type") == "realtime"


def enabled_collections(feature: str) -> set[str] | None:
    """Return the set of collections enabled for a feature, or None if unrestricted.

    Reads the optional `enabled_collections` allowlist under a feature's
    gold_config.yml entry (currently only used by `eden` for the Phase 1
    release — see infra/config/gold_config.yml). None means "no allowlist
    configured" — every collection for the feature runs, preserving the
    pre-Phase-1 default when the key is absent.
    """
    cfg = _GOLD_CONFIG.get(feature, {})
    allowlist = cfg.get("enabled_collections")
    if allowlist is None:
        return None
    return set(allowlist)


def is_collection_enabled(feature: str, collection: str) -> bool:
    """True if `collection` should be set up/populated for `feature`.

    Falls back to True when the feature has no `enabled_collections`
    allowlist configured.
    """
    allowlist = enabled_collections(feature)
    return allowlist is None or collection in allowlist


def _step(name: str, fn: Callable[[], int]) -> tuple[str, Callable[[], int]]:
    return name, fn


def build_steps(
    tenant: str,
    mongo_uri: str | None,
    pg_dsn: str | None,
    bpe_db: str,
    batch_size: int,
    dry_run: bool,
    skip_setup: bool,
    max_docs: int = 0,
    eden_db: str = "gold_eden",
    skip_eden: bool = False,
    qa_recommendation_db: str = "gold_qa_recommendation",
    feature_filter: str | None = None,
    force_batch: bool = False,
    canonical_db: str | None = None,
) -> list[tuple[str, Callable[[], int]]]:
    """Build ordered loader steps for one tenant.

    Args:
        feature_filter: when set, only steps belonging to that feature are
                        returned.  Must be a key in FEATURE_REGISTRY.
                        None (default) returns all enabled steps.
        force_batch: on-demand rebuild (R1). For a realtime feature the batch
                     loaders are normally skipped (the streaming job is the sole
                     writer). When True, re-run the loaders that rebuild the
                     STREAMING-owned collections from current Silver — used to
                     recover a gap the stream missed (no batch reconciliation
                     backs realtime). For BPE this re-runs feature_store
                     (load_estimate_snapshot). Idempotent (LSN-guarded upsert),
                     so safe to run while the stream is live.
                     EDEN IS THE EXCEPTION: its initial population is Debezium-
                     based, not direct-Silver-SQL (fieldcraft-silver-cdc-connector's
                     snapshot.mode=initial feeds gold_eden_stream.py the same
                     way ordinary CDC does), so force_batch is a logged no-op
                     for eden — see the eden branch below for the full
                     rationale and how to actually recover a streaming gap.
    """

    base = ["--tenant", tenant, "--batch-size", str(batch_size)]
    if mongo_uri:
        base += ["--mongo-uri", mongo_uri]
    if pg_dsn:
        base += ["--pg-dsn", pg_dsn]
    if dry_run:
        base += ["--dry-run"]
    if max_docs > 0:
        base += ["--max-docs", str(max_docs)]

    # --rebuild forces the loaders to read ALL current Silver (ignore the
    # incremental cursor) and not advance it — so a rebuild truly reconstructs
    # Gold rather than running an incremental no-op.
    _rebuild_flag = ["--rebuild"] if force_batch else []
    bpe_argv      = base + ["--db", bpe_db] + _rebuild_flag

    steps: list[tuple[str, Callable[[], int]]] = []

    # ------------------------------------------------------------------
    # BPE pipeline
    # ------------------------------------------------------------------
    if not is_feature_enabled("bpe"):
        log.info("BPE disabled in gold_config.yml — skipping all BPE steps")
    else:
        if not skip_setup:
            def _setup_bpe() -> int:
                from gold.src.bpe.setup_gold_feat_bpe import main as _main
                orig = os.environ.get("GOLD_DB_NAME")
                os.environ["GOLD_DB_NAME"] = bpe_db
                try:
                    return _main()
                finally:
                    if orig is None:
                        os.environ.pop("GOLD_DB_NAME", None)
                    else:
                        os.environ["GOLD_DB_NAME"] = orig
            steps.append(_step("setup_gold_feat_bpe", _setup_bpe))

        def _load_estimate_snapshot() -> int:
            from gold.src.bpe.load_estimate_snapshot import main as _main
            return _main(bpe_argv)

        if not is_realtime("bpe"):
            # Batch mode: run the BPE loader.
            steps.append(_step("load_estimate_snapshot", _load_estimate_snapshot))
        elif force_batch:
            # Rebuild (R1): re-run feature_store (the streaming-owned collection)
            # from current Silver.
            log.info("BPE rebuild — re-running feature_store from current Silver (force_batch)")
            steps.append(_step("load_estimate_snapshot", _load_estimate_snapshot))
        else:
            # Realtime steady-state: streaming job is the sole writer of
            # gold_bpe.feature_store; setup above still ensures collection/indexes.
            log.info("BPE is realtime — skipping batch loader steps (streaming job is sole writer)")

    # ------------------------------------------------------------------
    # Eden AI Assistant pipeline
    # ------------------------------------------------------------------
    if not is_feature_enabled("eden"):
        log.info("Eden disabled in gold_config.yml — skipping all Eden steps")
    elif not skip_eden:
        if not skip_setup:
            def _setup_eden() -> int:
                from gold.src.eden.setup_gold_eden import main as _main
                return _main()
            steps.append(_step("setup_gold_eden", _setup_eden))

        eden_argv = base + ["--db", eden_db] + _rebuild_flag

        def _load_project_health() -> int:
            from gold.src.eden.load_project_health import main as _main
            return _main(eden_argv)

        def _load_invoice_summary() -> int:
            from gold.src.eden.load_invoice_summary import main as _main
            return _main(eden_argv)

        def _load_task_intelligence() -> int:
            from gold.src.eden.load_task_intelligence import main as _main
            return _main(eden_argv)

        def _load_workforce_status() -> int:
            from gold.src.eden.load_workforce_status import main as _main
            return _main(eden_argv)

        def _load_procurement_status() -> int:
            from gold.src.eden.load_procurement_status import main as _main
            return _main(eden_argv)

        def _load_bom_stock_gap() -> int:
            from gold.src.eden.load_bom_stock_gap import main as _main
            return _main(eden_argv)

        def _load_weather_schedule_cross() -> int:
            from gold.src.eden.load_weather_schedule_cross import main as _main
            return _main(eden_argv)

        def _load_bid_context() -> int:
            from gold.src.eden.load_bid_context import main as _main
            return _main(eden_argv)

        def _load_tenant_dashboard() -> int:
            from gold.src.eden.load_tenant_dashboard import main as _main
            return _main(eden_argv)

        # Each loader is paired with the Cosmos collection it writes, so the
        # `enabled_collections` allowlist (Phase 1 release — see
        # infra/config/gold_config.yml) can drop individual loaders without
        # touching the eden feature flag itself.
        _eden_step_collections = [
            ("load_project_health",     "project_health",     _load_project_health),
            ("load_invoice_summary",    "invoice_summary",    _load_invoice_summary),
            ("load_task_intelligence",  "task_intelligence",  _load_task_intelligence),
            ("load_workforce_status",   "workforce_status",   _load_workforce_status),
            ("load_procurement_status", "procurement_status", _load_procurement_status),
            ("load_bom_stock_gap",      "bom_stock_gap",      _load_bom_stock_gap),
            ("load_weather_schedule_cross", "weather_schedule_cross", _load_weather_schedule_cross),
            ("load_bid_context",        "bid_context",        _load_bid_context),
            ("load_tenant_dashboard",   "tenant_dashboard",   _load_tenant_dashboard),
        ]
        _disabled_eden_collections = [
            name for name, coll, _ in _eden_step_collections
            if not is_collection_enabled("eden", coll)
        ]
        if _disabled_eden_collections:
            log.info(
                "eden collections disabled via enabled_collections allowlist — "
                "skipping steps: %s", _disabled_eden_collections,
            )
        eden_steps = [
            _step(name, fn) for name, coll, fn in _eden_step_collections
            if is_collection_enabled("eden", coll)
        ]

        if not is_realtime("eden"):
            steps += eden_steps                      # batch mode: full set
        elif force_batch:
            # Eden's initial population (R1/"rebuild") is now Debezium-based,
            # not a direct-Silver-SQL batch run: fieldcraft-silver-cdc-connector.
            # template.json's snapshot.mode is "initial", so a fresh
            # connector/replication-slot registration snapshots every current
            # Silver row and publishes it to {ETL_ENV}.silver.cdc.events —
            # gold_eden_stream.py (the sole writer once eden.type is
            # "realtime") consumes that snapshot exactly like ordinary CDC and
            # populates the 6 collections without this process running any
            # loader SQL. --rebuild is therefore a no-op for Eden: there is no
            # on-demand trigger to re-run here (Debezium only performs an
            # "initial" snapshot for a connector/slot it has no prior offset
            # for — see deploy_silver_cdc_connector.py's SNAPSHOT MODE note for
            # what re-triggering one on an already-running connector actually
            # requires). If gold_eden_stream.py's Silver watermark ever falls
            # behind (a genuine gap, not a first-time backfill), recover it by
            # replaying the Kafka topic from an earlier offset into that job,
            # not by adding a direct-SQL path back here.
            log.info(
                "Eden rebuild requested (force_batch) — no-op: Eden's snapshot "
                "is Debezium-based (fieldcraft-silver-cdc-connector snapshot.mode="
                "initial), not a direct-Silver-SQL batch run. See this branch's "
                "comment for how to recover a genuine streaming gap instead."
            )
        else:
            # Realtime steady-state: streaming job is the sole writer.
            log.info("Eden is realtime — skipping batch loader steps (streaming job is sole writer)")

        # knowledge_chunk — AI Assistant RAG knowledge base (migrated from
        # ai_assistant).  This is a file-driven BATCH loader (reads a JSON
        # source via SOURCE_PATH), independent of Eden's realtime/batch
        # classification, so it runs in the batch path whenever a source file
        # is configured.  Without a source it exits 2, so it is only wired
        # when SOURCE_PATH (or KNOWLEDGE_CHUNK_SOURCE) is set.
        _kc_source = os.environ.get("KNOWLEDGE_CHUNK_SOURCE") or os.environ.get("SOURCE_PATH")
        if not is_collection_enabled("eden", "knowledge_chunk"):
            log.info("knowledge_chunk disabled via enabled_collections allowlist — skipping loader")
        elif _kc_source:
            kc_argv = base + ["--db", eden_db, "--source", _kc_source]

            def _load_knowledge_chunk() -> int:
                from gold.src.eden.load_knowledge_chunk import main as _main
                return _main(kc_argv)

            steps.append(_step("load_knowledge_chunk", _load_knowledge_chunk))
        else:
            log.info("knowledge_chunk source not set (KNOWLEDGE_CHUNK_SOURCE/SOURCE_PATH) — skipping loader")

        # task_timeline — deep per-version task history (versioned/historical
        # data for the Task domain). Batch-only by design, with NO streaming
        # job: silver.task can retain hundreds of version rows per task (one
        # project measured at 194k task rows live) — recomputing this on every
        # Silver CDC event would amplify write volume far beyond what the
        # other streaming Eden collections tolerate. See "Cost / performance
        # risks" in docs/gold-layer/historical_data_strategy.md. Each version,
        # once written, is immutable, so this loader only needs to append new
        # versions (cheap incremental read) — it always runs regardless of
        # is_realtime("eden"), which only governs the point-read collections
        # in eden_steps above.
        def _load_task_timeline() -> int:
            from gold.src.eden.load_task_timeline import main as _main
            return _main(eden_argv)

        if is_collection_enabled("eden", "task_timeline"):
            steps.append(_step("load_task_timeline", _load_task_timeline))
        else:
            log.info("task_timeline disabled via enabled_collections allowlist — skipping loader")

        # estimate_price_evidence — Q1 reasoning evidence (why did the estimate
        # price change). Batch-only, same reasoning as task_timeline: it is NOT
        # wired into gold_eden_stream.py's CDC trigger table yet (project_estimate/
        # bom changes don't recompute it in realtime), so it always runs here
        # regardless of is_realtime("eden") — otherwise, with eden classified
        # realtime, it would only ever populate via an explicit --rebuild and
        # never on the normal 15-min batch cadence.
        def _load_estimate_price_evidence() -> int:
            from gold.src.eden.load_estimate_price_evidence import main as _main
            return _main(eden_argv)

        if is_collection_enabled("eden", "estimate_price_evidence"):
            steps.append(_step("load_estimate_price_evidence", _load_estimate_price_evidence))
        else:
            log.info("estimate_price_evidence disabled via enabled_collections allowlist — skipping loader")

        # schedule_move_evidence — Q2 reasoning evidence (why did the schedule
        # move). Same reasoning as estimate_price_evidence/task_timeline: not
        # wired into gold_eden_stream.py's CDC trigger table yet, so it always
        # runs here regardless of is_realtime("eden").
        def _load_schedule_move_evidence() -> int:
            from gold.src.eden.load_schedule_move_evidence import main as _main
            return _main(eden_argv)

        if is_collection_enabled("eden", "schedule_move_evidence"):
            steps.append(_step("load_schedule_move_evidence", _load_schedule_move_evidence))
        else:
            log.info("schedule_move_evidence disabled via enabled_collections allowlist — skipping loader")

    # ------------------------------------------------------------------
    # Q&A Recommendation — Silver answers → gold_qa_recommendation
    # ------------------------------------------------------------------
    if not is_feature_enabled("qa_recommendation"):
        log.info("qa_recommendation disabled in gold_config.yml — skipping")
    else:
        if not skip_setup:
            def _setup_qa_recommendation() -> int:
                from gold.src.reference_project.setup_gold_feat_refproject import main as _main
                return _main()
            steps.append(_step("setup_gold_feat_refproject", _setup_qa_recommendation))

        qa_argv = base + ["--db", qa_recommendation_db] + _rebuild_flag

        def _load_qa_recommendation() -> int:
            from gold.src.reference_project.load_qa_recommendation import main as _main
            return _main(qa_argv)

        if not is_realtime("qa_recommendation"):
            steps.append(_step("load_qa_recommendation", _load_qa_recommendation))
        elif force_batch:
            # Rebuild (R1): re-run qa_answer from current Silver.
            log.info("qa_recommendation rebuild — re-running qa_answer from current Silver (force_batch)")
            steps.append(_step("load_qa_recommendation", _load_qa_recommendation))
        else:
            # Realtime steady-state: streaming job is the sole writer of
            # gold_qa_recommendation.qa_answer.
            log.info("qa_recommendation is realtime — skipping batch loader step (streaming job is sole writer)")

    # ------------------------------------------------------------------
    # Canonical Gold — cross-feature reference data (not owned by one feature)
    # ------------------------------------------------------------------
    if not is_feature_enabled("canonical"):
        log.info("Canonical disabled in gold_config.yml — skipping all canonical steps")
    else:
        _canonical_db = canonical_db or os.environ.get("GOLD_CANONICAL_DB", "gold_canonical")

        if not skip_setup:
            def _setup_canonical() -> int:
                _ensure_canonical_setup_once(_canonical_db)
                return 0
            steps.append(_step("setup_gold_canonical", _setup_canonical))

        canonical_argv = base + ["--db", _canonical_db] + _rebuild_flag

        def _load_user_permissions() -> int:
            _ensure_canonical_setup_once(_canonical_db)
            from gold.src.canonical.load_user_permissions import main as _main
            return _main(canonical_argv)

        # user_permissions/tenant/project/customer/asset are realtime — the
        # Flink job (gold_user_permissions_stream.py, job name
        # gold-canonical-stream) is the sole steady-state writer for all five
        # once canonical.type is "realtime" in gold_config.yml. question/
        # answer stay batch-only (see below) — global/rarely-changing
        # reference data where the 15-min cadence is sufficient.
        def _load_tenant() -> int:
            _ensure_canonical_setup_once(_canonical_db)
            from gold.src.canonical.load_tenant import main as _main
            return _main(canonical_argv)

        def _load_project() -> int:
            _ensure_canonical_setup_once(_canonical_db)
            from gold.src.canonical.load_project import main as _main
            return _main(canonical_argv)

        def _load_customer() -> int:
            _ensure_canonical_setup_once(_canonical_db)
            from gold.src.canonical.load_customer import main as _main
            return _main(canonical_argv)

        def _load_asset() -> int:
            _ensure_canonical_setup_once(_canonical_db)
            from gold.src.canonical.load_asset import main as _main
            return _main(canonical_argv)

        def _load_lead() -> int:
            _ensure_canonical_setup_once(_canonical_db)
            from gold.src.canonical.load_lead import main as _main
            return _main(canonical_argv)

        def _load_communication_log() -> int:
            _ensure_canonical_setup_once(_canonical_db)
            from gold.src.canonical.load_communication_log import main as _main
            return _main(canonical_argv)

        # Gated by the same enabled_collections allowlist mechanism used for
        # eden (is_collection_enabled) — orthogonal to the realtime/batch gate
        # below — so a future release can phase in a subset of these seven
        # collections instead of all-or-nothing via is_feature_enabled
        # ("canonical") alone. Absent allowlist == all run (unchanged default).
        _realtime_steps = [
            (n, fn) for n, fn in (
                ("load_user_permissions", _load_user_permissions),
                ("load_tenant", _load_tenant),
                ("load_project", _load_project),
                ("load_customer", _load_customer),
                ("load_asset", _load_asset),
                ("load_lead", _load_lead),
                ("load_communication_log", _load_communication_log),
            )
            if is_collection_enabled("canonical", n.removeprefix("load_"))
        ]
        _realtime_names = [n for n, _ in _realtime_steps]
        if not is_realtime("canonical"):
            steps += [_step(n, fn) for n, fn in _realtime_steps]
        elif force_batch:
            log.info(
                "canonical rebuild — re-running %s from current Silver (force_batch)",
                _realtime_names,
            )
            steps += [_step(n, fn) for n, fn in _realtime_steps]
        else:
            log.info(
                "canonical is realtime — skipping batch loader steps %s "
                "(streaming job is sole writer)", _realtime_names,
            )

        # question/answer — global Q&A template catalog + lean per-tenant answer
        # passthrough. load_question is global reference data (see its module
        # docstring): it ignores --tenant for its Silver query and shares one
        # cursor across every tenant iteration, so running it once per tenant
        # here is a cheap no-op after the first tenant in a given pipeline run.
        def _load_question() -> int:
            _ensure_canonical_setup_once(_canonical_db)
            from gold.src.canonical.load_question import main as _main
            return _main(canonical_argv)

        def _load_answer() -> int:
            _ensure_canonical_setup_once(_canonical_db)
            from gold.src.canonical.load_answer import main as _main
            return _main(canonical_argv)

        if is_collection_enabled("canonical", "question"):
            steps.append(_step("load_question", _load_question))
        if is_collection_enabled("canonical", "answer"):
            steps.append(_step("load_answer", _load_answer))

    # ------------------------------------------------------------------
    # Feature filter — slice to a single feature when requested
    # ------------------------------------------------------------------
    if feature_filter is not None:
        descriptor = FEATURE_REGISTRY.get(feature_filter)
        if descriptor is None:
            known = ", ".join(sorted(FEATURE_REGISTRY))
            log.error(
                "unknown feature_filter=%r — no steps will run.  Known: %s",
                feature_filter, known,
            )
            return []
        steps = [(n, fn) for n, fn in steps if n in descriptor.step_names]
        if not steps:
            log.error(
                "feature_filter=%r yielded 0 steps — feature may be disabled in gold_config.yml",
                feature_filter,
            )
        log.debug(
            "feature_filter=%r → %d step(s): %s",
            feature_filter, len(steps), [n for n, _ in steps],
        )

    return steps
