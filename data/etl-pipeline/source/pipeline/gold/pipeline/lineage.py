"""Standardised run-lineage / audit stamp for every Gold document.

The Gold pipeline already mints a pipeline ``run_id`` (``gold/pipeline/audit.py::open_run``)
and threads it through the audit trail (``gold_audit.pipeline_run`` / ``gold_event`` /
``job_state``).  Historically that id — and gold-side timestamps — never reached the
Gold *business* documents; lineage could only be reconstructed from the audit collections.

This module is the single source of truth for the additive, camelCase lineage block
that every loader stamps into its document.  Keeping it here (rather than copy-pasting
into ten loaders) guarantees identical field names and semantics across all features.

Propagation: ``tenant_runner`` exports the active ``run_id`` into the
``GOLD_PIPELINE_RUN_ID`` environment variable before invoking each step closure (and
the subprocess-isolation path inherits it for free), so loaders read it via
``current_run_id()`` with no signature changes across the ``build_steps`` boundary.

Consolidation (2026-07-12): dropped ``featureName`` and ``processingTimestamp`` from
this block. ``featureName`` had no functional reader anywhere (query filter, RBAC,
aggregation) — the owning feature is already unambiguous from which Cosmos DB/collection
the document lives in. ``processingTimestamp`` was always set to the exact same value as
``goldUpdatedAt`` — a pure duplicate. Loaders were also independently hardcoding legacy
per-document stamps (``_etlUpdatedAt``, ``processedAt``, ``updatedAt``) outside this
module; those were removed at the call sites (see each ``load_*.py``) in the same pass —
``goldUpdatedAt`` (below) is now the single gold-side write-time field.

Field semantics
---------------
``pipelineRunId``        run that last wrote the doc (``None`` on ad-hoc / dry runs with no open_run)
``goldUpdatedAt``        gold-side write time — advances on every (re)write; sole gold-side timestamp
``sourceUpdatedAt``      last update time from the source (Silver ``_s_silver_ts``); ``None`` when the
                         loader does not pass it through
``dataOrigin``           provenance class — ``SOURCE`` (real source data, the default) / ``SYNTHETIC`` /
                         ``MASKED``. Per-row when masking/synthesis is applied; ``SOURCE`` otherwise.
``goldCreatedAt``        immutable first-seen time — written via ``$setOnInsert`` only (see ``set_on_insert``)
``sourceEnv``            logical *source* environment (Silver ``_s_env`` — e.g. ``si_dev``, ``datamart``,
                         ``qa``, ``prod``). Silver tables are partitioned by ``_s_env`` and a ``tenantid``
                         is only unique *within* an env, not across envs — this field (plus its inclusion
                         in each loader's Mongo upsert key) prevents two envs that happen to share a
                         tenantid from silently overwriting each other's Gold document. ``None`` when the
                         source row predates multi-env stamping.
                         NOT the same field as ``_env`` below — different axis (which source system a
                         tenant's data came from, vs which pipeline deployment wrote the doc).
``_env``                 deployment tier that wrote this document (``PIPELINE_ENV`` — one of the values
                         in each contract's ``_env`` enum, e.g. ``dev``/``qa``/``stg``/``sbx``/``prd``;
                         defaults to ``"dev"`` when unset). Every Gold contract under ``contracts/gold/``
                         (except ``canonical_user_permissions``) requires ``_env``. The streaming path
                         (``gold/src/flink/sinks/mongo_sink.py``) has always stamped this after
                         ``shape_doc()`` runs; the batch loaders never did, because the contract files
                         had a UTF-8 BOM that silently broke JSON parsing and made ``required`` checks a
                         no-op — so this gap was never actually enforced until the BOM was fixed (see
                         ``gold/src/monitoring/contract_validator.py``). Stamping it here closes that gap
                         so both write paths satisfy the same contract requirement.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timezone
from typing import Any

log = logging.getLogger("gold.pipeline.lineage")

# Feature keys — aligned with FEATURE_REGISTRY in gold/pipeline/step_registry.py.
FEATURE_NAMES = frozenset({"bpe", "eden", "reference_project", "canonical"})

RUN_ID_ENV = "GOLD_PIPELINE_RUN_ID"

# Default provenance class for rows sourced directly from the platform (Silver).
# Loaders override per-row only when a record is synthesised or masked.
DATA_ORIGIN_SOURCE = "SOURCE"

# Deployment-tier env var — same one gold/src/flink/sinks/mongo_sink.py reads for
# the streaming path's "_env" stamp. Kept as one constant so both write paths
# agree on the variable name and default.
PIPELINE_ENV_VAR = "PIPELINE_ENV"
_PIPELINE_ENV_DEFAULT = "dev"


def pipeline_env() -> str:
    """Return the deployment-tier stamp (``PIPELINE_ENV``, default ``"dev"``).

    Same env var and default the streaming sink (``MongoUpsertSink``) already
    uses for its ``_env`` field — kept in one place so batch and streaming
    docs agree.
    """
    return os.environ.get(PIPELINE_ENV_VAR, _PIPELINE_ENV_DEFAULT)


# Lineage keys that automation tests assert on (excludes goldCreatedAt — set-on-insert).
LINEAGE_SET_KEYS = (
    "pipelineRunId", "goldUpdatedAt",
    "sourceUpdatedAt", "dataOrigin", "sourceEnv", "_env",
)


_PG_TEXT_OFFSET_RE = re.compile(r"^(.*[+-]\d{2})(:\d{2})?$")


def parse_ts(v: Any) -> datetime | None:
    """Coerce a Silver date/timestamp value — native or ``::text``-cast — into a
    timezone-aware ``datetime`` for storage as a native BSON date.

    Every Gold document field that represents a date/timestamp must be a native
    BSON date, never a string (2026-07-20 sweep). Loaders get values three ways:
    already-native ``datetime``/``date`` (psycopg2, column with no ``::text``
    cast), or a Python ``str`` — either because the SQL casts to ``::text``
    directly, or because the value passed through a ``JSONB_BUILD_OBJECT`` in
    SQL first (JSON has no native date type, so Postgres always serializes it as
    a string on the way out, cast or not). This function is the single place
    that turns either shape back into a real ``datetime``, so no loader
    hand-rolls its own string parsing.

    Postgres's default text rendering of a ``timestamptz`` uses a bare 2-digit
    UTC offset (``2026-07-01 10:23:45+00``) rather than the
    ``+00:00``/``Z``-style offset ``datetime.fromisoformat`` requires before
    Python 3.11 — that offset is padded before parsing so this works on the
    3.10 runtime this pipeline ships on.
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo is not None else v.replace(tzinfo=timezone.utc)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s:
        return None
    m = _PG_TEXT_OFFSET_RE.match(s)
    if m and not m.group(2):
        s = s + ":00"
    try:
        ts = datetime.fromisoformat(s)
    except ValueError:
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def current_run_id() -> str | None:
    """Return the active pipeline run id from the environment, or ``None``.

    ``tenant_runner`` sets ``GOLD_PIPELINE_RUN_ID`` around each step; outside a
    pipeline run (manual loader invocation, dry run with no ``open_run``) the
    variable is unset and lineage records ``pipelineRunId=None``.
    """
    return os.environ.get(RUN_ID_ENV) or None


def lineage_fields(
    feature_name: str,
    gold_ts: datetime,
    *,
    run_id: str | None = None,
    iso: bool = False,
    source_updated_at: Any = None,
    data_origin: str = DATA_ORIGIN_SOURCE,
    source_env: str | None = None,
) -> dict[str, Any]:
    """Build the additive lineage block merged into a Gold document's ``$set``.

    ``feature_name`` is accepted for call-site compatibility (every loader already
    passes its feature key here) but is no longer stamped into the document — see
    the "Consolidation" note in the module docstring for why.

    ``gold_ts`` is the loader's single run timestamp — the sole gold-side
    write-time stamp (``goldUpdatedAt``).

    ``source_updated_at`` is the source watermark (Silver ``_s_silver_ts``) of the
    record; pass it through so ``sourceUpdatedAt`` is populated. It is formatted
    with the same ``iso`` style as the gold timestamps. Loaders that do not yet
    thread it leave it ``None``.

    ``data_origin`` defaults to ``SOURCE``; pass ``SYNTHETIC`` / ``MASKED`` per-row
    only when a record is generated or masked.

    ``source_env`` is the Silver ``_s_env`` value for this row (pass
    ``row.get("source_env")`` from the loader's Silver query). Callers should also
    fold ``sourceEnv`` into their Mongo upsert filter key alongside ``tenantId`` —
    this field alone only makes the source environment visible, it does not by
    itself prevent a same-tenantid collision across envs from overwriting a
    document.

    Set ``iso=True`` for loaders that emit ISO strings rather than ``datetime``
    objects (qa_recommendation, knowledge_chunk) so the stamp matches their style.
    """
    ts: Any = gold_ts.isoformat() if iso else gold_ts
    src: Any = source_updated_at
    if src is not None and iso and hasattr(src, "isoformat"):
        src = src.isoformat()
    del feature_name  # kept for call-site compatibility only — see docstring
    return {
        "pipelineRunId":       run_id if run_id is not None else current_run_id(),
        "goldUpdatedAt":       ts,
        "sourceUpdatedAt":     src,
        "dataOrigin":          data_origin,
        "sourceEnv":           source_env,
        "_env":                pipeline_env(),
    }


def set_on_insert(gold_ts: datetime, *, iso: bool = False) -> dict[str, Any]:
    """Build the ``$setOnInsert`` block (immutable ``goldCreatedAt``).

    Pass the result as the loader's ``UpdateOne(..., {"$set": doc, "$setOnInsert": ...})``
    so ``goldCreatedAt`` is stamped only on a true insert and preserved across updates.
    """
    return {"goldCreatedAt": gold_ts.isoformat() if iso else gold_ts}


def _is_older_project_id(candidate: Any, current: Any) -> bool:
    """True iff ``candidate`` is a strictly smaller Silver-internal project id
    than ``current``. Both are compared as ints (Silver's ``id`` is a real
    auto-increment integer, just carried as a string in Gold docs) — never as
    raw strings, since e.g. "999" > "1000" lexicographically even though 999
    < 1000 numerically once id length crosses a digit boundary. Returns False
    (never treat as older / never delete) if either value doesn't parse
    cleanly as an int — an uncertain comparison must not delete data."""
    try:
        return int(candidate) < int(current)
    except (TypeError, ValueError):
        return False


def cleanup_stale_order_no_siblings(
    coll: Any, tenant_id: Any, order_no: Any, current_project_id: Any,
    source_env: Any = None,
) -> int:
    """Real bug, found live 2026-08-26: the source system creates a NEW
    internal project id (not an update to the existing row) when a draft
    project is converted/submitted — confirmed on two real projects (order
    977660: ids 1232 draft -> 1233 current; order 482999: ids 1242 draft ->
    1243 current), no reference_project_id/parent_project_id link between
    them. Every Gold collection keyed by that raw internal id (project_health,
    canonical.project, ...) ends up with TWO documents for one real-world
    project — one frozen forever at the abandoned draft id's last state, one
    live-updating under the new id.

    Called from BOTH write paths that key a collection by ``projectId``:
    the streaming sink (``flink/sinks/mongo_sink.py``, one call per CDC-
    triggered upsert) and the batch loaders' own ``flush()`` (one call per
    upserted batch, so ``run_pipeline.py --rebuild`` also converges existing
    duplicates immediately instead of waiting for the next real CDC event).
    Both call this only after their own bulk upsert has confirmed success,
    since the argument is "we just proved current_project_id is the live
    row" — calling it speculatively without that would risk deleting a
    sibling that turns out to still be correct.

    ``source_env`` scopes the match/delete to the SAME identity grain every
    upsert filter in this pipeline already uses (``{tenantId, projectId,
    sourceEnv}`` — see the LSN-guarded ``UpdateOne`` filter at each call
    site). PR 8243 review (thread 71329) caught this omission live: without
    it, a tenant with the same orderNo appearing in two different Silver
    source environments (a real, supported case — see this module's
    ``sourceEnv`` field-semantics note above) could have a legitimate
    OTHER-environment document deleted, mistaking it for the same-environment
    abandoned-draft duplicate this function exists to clean up. Pass
    ``None`` only when the caller's own documents never populate sourceEnv
    (matches it against other ``sourceEnv: None`` docs, same as a normal
    Mongo equality filter) — never omit it to mean "match any environment".

    Deliberately one-directional (only deletes candidates OLDER than
    current_project_id, never newer): if a stale/delayed CDC event for an
    already-abandoned draft row somehow arrives after its successor has
    already been cleaned up, this call (for the OLD id) finds no sibling
    older than itself and deletes nothing — it still re-upserts the old doc
    itself (this function doesn't prevent that write), leaving a duplicate
    until the current row's next real change fires again and cleans up.
    That residual race is accepted as a rare, self-healing edge case, not a
    correctness gap worth blocking this fix on: the alternative (a
    pre-write existence/comparison guard on every single upsert) adds a
    synchronous round-trip to the hot path for a case that, per the two
    real occurrences observed so far, doesn't actually recur once a
    project leaves draft.

    Returns the number of sibling documents deleted (0 if none were stale,
    or orderNo/current_project_id were falsy)."""
    if not order_no or current_project_id is None:
        return 0
    try:
        siblings = coll.find(
            {"tenantId": tenant_id, "orderNo": order_no, "sourceEnv": source_env,
             "projectId": {"$ne": current_project_id}},
            {"projectId": 1},
        )
        stale_ids = [
            s["projectId"] for s in siblings
            if _is_older_project_id(s.get("projectId"), current_project_id)
        ]
        if not stale_ids:
            return 0
        result = coll.delete_many({
            "tenantId": tenant_id, "orderNo": order_no, "sourceEnv": source_env,
            "projectId": {"$in": stale_ids},
        })
        return result.deleted_count or 0
    except Exception as exc:  # noqa: BLE001 — cleanup must never break the caller
        log.warning(
            "cleanup_stale_order_no_siblings failed | tenant=%s orderNo=%s "
            "sourceEnv=%s currentProjectId=%s err=%s",
            tenant_id, order_no, source_env, current_project_id, exc,
        )
        return 0
