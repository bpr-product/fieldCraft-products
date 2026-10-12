"""Silver → Gold loader: user_permissions (Canonical Gold).

Produces one document per (tenant, user) with the resolved, effective RBAC
state — the same result rbac_resolver.py-style consumers previously computed
at query time against source PostgreSQL. Consumers (starting with Eden) read
this collection instead of joining Silver themselves.

Data model (verified against live Silver, see
docs/canonical/user_access_management_model.md):
  silver.user_account   — identity: id, object_id (external IdP id, JWT `oid`),
                           email, first/middle/lastname, active, deleted.
                           NOTE: `status` was removed from this loader's SELECT
                           — on this schema version it no longer exists as an
                           inline column (normalized to status_id -> a separate
                           silver.user_status table); it was unused downstream
                           in shape_doc() regardless, so dropped rather than
                           joined. Revisit if status is ever needed here.
  silver.roles          — NOTE: the source table is `roles`, not `role`.
                           id, name, description, builtin, active.
  silver.role_permission — id, roles_id -> roles.id, permission (free-text
                           'RESOURCE.ACTION' code), builtin. No permission
                           master table exists source-side.
  silver.user_role_map  — M:N user<->role with temporal validity:
                           user_account_id -> user_account.id,
                           roles_id -> roles.id, assign_start_date,
                           assign_end_date (NULL = open-ended).
  silver.resource       — workforce projection of a user (only a subset of
                           users have one): id, user_id -> user_account.id.
  silver.project_member — project access, keyed by resource, NOT by user
                           directly: project_id, resource_id -> resource.id,
                           status.

  Effective user -> project access path: user_account -> resource ->
  project_member -> project. Users with no resource row cannot appear in
  project_ids even if they hold roles/permissions — documented gap, see
  docs/canonical/user_access_management_model.md section 2.

  All joins are tenant-scoped (`_s_tenantid` equality) because Silver source
  PKs repeat across tenants (composite PK); a join without the tenant
  predicate fans out across tenants.

  "Effective" role assignment = user_role_map row with
  now BETWEEN assign_start_date AND COALESCE(assign_end_date, 'infinity'),
  joined to an active role (roles.active = TRUE) on an active, non-deleted
  user. Permissions are the DISTINCT role_permission.permission codes across
  those effective roles. EXCEPTIONS (see the role/permission LATERAL join
  below for the full reasoning on each):
    - assign_end_date < assign_start_date is treated as corrupted data, not
      a real expiry (data-driven, no tenant/role hardcoded).
    - an admin/superAdmin assignment ignores its own assign_end_date when it
      is the LAST currently-effective admin/superAdmin for that tenant —
      i.e. only when expiring it would leave the tenant with zero admins
      (business rule originating 2026-07-30; generalized 2026-09-10 from a
      hardcoded single-tenant exception to this dynamic, data-driven
      condition — no tenant id is hardcoded anywhere in this file).

Grain key: (tenantId, user_uid) where user_uid = user_account.object_id (the
JWT `oid`) — rows with a NULL object_id cannot be keyed and are skipped.

Upsert key: (tenantId, user_uid)

Exit codes: 0 success · 2 config error · 3 load failure
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse, urlunparse

import psycopg2
import psycopg2.extras
from pymongo import MongoClient, UpdateOne
from pymongo.collection import Collection
from pymongo.errors import (
    AutoReconnect, BulkWriteError, ConnectionFailure,
    NetworkTimeout, OperationFailure, ServerSelectionTimeoutError,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHARED_SRC = str(_REPO_ROOT / "shared")
_DATA_PIPELINE = str(_REPO_ROOT)
for _p in (_SHARED_SRC, _DATA_PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from secret_resolver import get_mongo_uri, get_postgres_dsn  # noqa: E402
from gold.src.monitoring.circuit_breaker import CircuitBreaker  # noqa: E402
from gold.src.monitoring.contract_validator import ContractValidator  # noqa: E402
from gold.pipeline.cursor_store import read_cursor, write_cursor, mark_error, EPOCH  # noqa: E402
from gold.pipeline.lineage import lineage_fields, parse_ts, set_on_insert  # noqa: E402

COLLECTION = "user_permissions"
FEATURE_NAME = "canonical"
_STEP_NAME = "load_user_permissions"
_contract_validator = ContractValidator("canonical_user_permissions")
TRANSIENT_ERRORS = (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError)
log = logging.getLogger("canonical.user_permissions_loader")


@dataclass(frozen=True)
class LoaderConfig:
    mongo_uri: str
    pg_dsn: str
    db_name: str
    tenantid: str
    batch_size: int
    max_retries: int
    retry_backoff_s: float
    dry_run: bool
    max_docs: int = 0
    rebuild: bool = False

    @staticmethod
    def from_args_and_env(argv: list[str] | None = None) -> "LoaderConfig":
        p = argparse.ArgumentParser(description="Silver → Gold loader: user_permissions")
        p.add_argument("--mongo-uri",     default=None)
        p.add_argument("--pg-dsn",        default=None)
        p.add_argument("--db",            dest="db_name",
                       default=os.environ.get("GOLD_CANONICAL_DB", "gold_canonical"))
        p.add_argument("--tenant", required=not bool(os.environ.get("TENANT_ID")),
                       default=os.environ.get("TENANT_ID"))
        p.add_argument("--batch-size",    type=int,
                       default=int(os.environ.get("BATCH_SIZE", "200")))
        p.add_argument("--max-retries",   type=int,
                       default=int(os.environ.get("MAX_RETRIES", "5")))
        p.add_argument("--retry-backoff", type=float,
                       default=float(os.environ.get("RETRY_BACKOFF_S", "1.5")))
        p.add_argument("--dry-run",       action="store_true")
        p.add_argument("--max-docs",      type=int,
                       default=int(os.environ.get("MAX_DOCS", "0")), metavar="N")
        p.add_argument("--rebuild",       action="store_true",
                       help="Force full re-aggregate from current Silver (ignore the "
                            "newness check) and do NOT advance the cursor.")
        ns = p.parse_args(argv)
        return LoaderConfig(
            mongo_uri=ns.mongo_uri or get_mongo_uri(),
            pg_dsn=ns.pg_dsn or get_postgres_dsn(),
            db_name=ns.db_name,
            tenantid=ns.tenant,
            batch_size=ns.batch_size,
            max_retries=ns.max_retries,
            retry_backoff_s=ns.retry_backoff,
            dry_run=ns.dry_run,
            max_docs=ns.max_docs,
            rebuild=ns.rebuild,
        )


def _mask_uri(uri: str) -> str:
    try:
        u = urlparse(uri)
        if u.password:
            netloc = f"{u.username}:***@{u.hostname}"
            if u.port:
                netloc += f":{u.port}"
            return urlunparse(u._replace(netloc=netloc))
        return uri
    except Exception:
        return re.sub(r"://[^@]+@", "://***@", uri)


# One row per user. Effective roles/permissions resolved via user_role_map with
# temporal validity; project_ids resolved via the resource bridge (NOT a direct
# user->project link — see docs/canonical/user_access_management_model.md).
_SILVER_QUERY = """
SELECT
    ua._s_tenantid                                          AS tenantid,
    ua._s_env                                                AS source_env,
    ua._s_is_deleted                                        AS is_deleted,
    ua._s_deleted_at                                        AS deleted_at,
    ua._s_lsn                                               AS silver_lsn,
    ua._s_silver_ts                                         AS ua_silver_ts,
    ua.id                                                   AS user_account_id,
    ua.object_id                                            AS user_uid,
    ua.email,
    ua.firstname,
    ua.middlename,
    ua.lastname,
    ua.active,
    ua.deleted,

    COALESCE(rp.roles, ARRAY[]::text[])                     AS roles,
    COALESCE(rp.permissions, ARRAY[]::text[])               AS permissions,
    rp.role_silver_ts,
    rp.perm_silver_ts,

    COALESCE(pm.project_ids, ARRAY[]::text[])               AS project_ids,
    pm.resource_silver_ts,
    pm.member_silver_ts

FROM silver.user_account ua

-- Effective role -> permission resolution (temporal validity + active role).
LEFT JOIN LATERAL (
    SELECT
        ARRAY_AGG(DISTINCT r.name)                                     AS roles,
        ARRAY_AGG(DISTINCT rperm.permission)
            FILTER (WHERE rperm.permission IS NOT NULL)                AS permissions,
        MAX(r._s_silver_ts)                                            AS role_silver_ts,
        MAX(rperm._s_silver_ts)                                        AS perm_silver_ts
    FROM silver.user_role_map urm
    JOIN silver.roles r
      ON r.id            = urm.roles_id
     AND r._s_tenantid    = urm._s_tenantid
     AND r._s_env         = urm._s_env
    LEFT JOIN silver.role_permission rperm
      ON rperm.roles_id     = r.id
     AND rperm._s_tenantid  = r._s_tenantid
     AND rperm._s_env       = r._s_env
     AND rperm._s_is_deleted = FALSE
    WHERE urm._s_tenantid       = ua._s_tenantid
      AND urm._s_env            = ua._s_env
      AND urm.user_account_id   = ua.id
      AND urm._s_is_deleted     = FALSE
      AND r._s_is_deleted       = FALSE
      AND r.active               = TRUE
      AND urm.assign_start_date <= NOW()
      -- admin/superAdmin assignments are treated as still-effective past
      -- their assign_end_date (business rule change 2026-07-30 — tn24047750
      -- had a genuine admin grant with a 1-day assign_end_date window that
      -- expired months ago, silently dropping the tenant's only admin from
      -- this collection and gold_eden.project_health.assignees; other roles
      -- keep the normal temporal expiry check).
      --
      -- SECURITY (finding 29, PR #7999): this bypass was originally written
      -- as a blanket `LOWER(r.name) IN ('admin','superadmin')` match with no
      -- tenant scoping — that made the expiry bypass apply to ANY tenant
      -- with a role display-named "admin"/"superAdmin" (case-insensitively),
      -- not just the tn24047750 incident that motivated it. Narrowed to the
      -- originating tenant only; re-scope to a role id instead of a name
      -- match if/when one becomes available for that tenant's admin role.
      --
      -- Generic data-corruption bypass added 2026-09-10 (prod incident, see
      -- OOM_AND_CDC_FAILURE_RCA_2026-09-09.md / PROD_FIXES_2026-09-09.md at
      -- repo root), REPLACING a tenant+roles_id-hardcoded clause
      -- (`ua._s_tenantid = 'tn88351775' AND urm.roles_id = 7`) that only
      -- covered the ONE tenant hit by this bug at the time it was written.
      -- The incident's own diagnosis already showed the real defect isn't
      -- tenant-specific: "12 expired assignments total, 5 with
      -- assign_end_date < assign_start_date" ACROSS MULTIPLE tenants
      -- (tn22473627 and tn59695547 were confirmed still broken by the same
      -- bug after the hardcoded tn88351775-only patch shipped). An
      -- assign_end_date strictly before its own assign_start_date is
      -- provably corrupted data, not a real expiry, for ANY tenant/role --
      -- so detecting that condition directly, instead of hardcoding which
      -- specific (tenant, role) pairs have been individually observed to
      -- hit it so far, is both more correct (covers tn22473627/tn59695547
      -- without another code change) and needs no per-incident patching
      -- going forward. THE ROOT CAUSE IS STILL UNFIXED: something in the
      -- source app writes assign_end_date earlier than assign_start_date --
      -- an ASE/Java application defect outside this repo. Once that's
      -- fixed and the affected assignments are corrected at the source
      -- (`UPDATE user_role_map SET assign_end_date = NULL WHERE
      -- assign_end_date < assign_start_date`), this clause becomes a no-op
      -- (no row will ever match it) and can be safely removed.
      --
      -- MONITORING (rbac-reviewer, 2026-09-10): this clause is intentionally
      -- tenant/role-agnostic, which is what makes it correct -- but it also
      -- means it's now a standing, platform-wide expiry-bypass condition
      -- rather than a one-tenant patch, and nothing here alerts if new
      -- corrupted rows keep appearing under it. Track
      -- `COUNT(*) FROM silver.user_role_map WHERE assign_end_date <
      -- assign_start_date` (per-tenant) as a pipeline health metric -- it
      -- should trend to zero as the source defect gets fixed and existing
      -- rows corrected, never grow. Add to daily_digest.py's checks and open
      -- an ADO item to remove this clause once that count is durably zero.
      --
      -- tn24047750 hardcode REMOVED 2026-09-10. Verified against live prod
      -- Silver: tn24047750 has ZERO rows in user_account/roles/user_role_map
      -- today (only tn22473627/tn59695547/tn88351775 currently have data) --
      -- the hardcoded clause was matching nothing. Rather than leave a
      -- dead, identity-hardcoded literal in place, replaced it with the
      -- actual CONDITION the 2026-07-30 business rule was protecting
      -- against ("don't silently drop a tenant's only admin"), expressed
      -- from data instead of a tenant id: bypass expiry for an admin/
      -- superAdmin assignment only when it is the LAST currently-effective
      -- one for that tenant -- i.e. only when NOT bypassing it would leave
      -- the tenant with zero admins. This is strictly NARROWER than both
      -- the finding-29 blanket role-name match (which fired for every
      -- admin-named role in every tenant) AND the tn24047750-only hardcode
      -- (which fired for exactly one, no-longer-existing tenant regardless
      -- of whether other admins existed) -- it only ever activates in the
      -- specific "last admin" edge case, for any tenant, without naming one.
      -- Verified against live prod data (all 3 current tenants) before
      -- shipping: every existing admin/superAdmin row either already
      -- matches the corruption clause above or has a far-future
      -- assign_end_date, so this replacement matches zero rows today --
      -- same behavior as the dead tenant-hardcoded clause it replaces, but
      -- with no identity hardcoded and ready to correctly fire if a real
      -- last-admin expiry recurs for any tenant.
      AND (
            urm.assign_end_date IS NULL
         OR urm.assign_end_date > NOW()
         OR urm.assign_end_date < urm.assign_start_date
         OR (
              LOWER(r.name) IN ('admin', 'superadmin')
              AND NOT EXISTS (
                    SELECT 1
                    FROM silver.user_role_map urm2
                    JOIN silver.roles r2
                      ON r2.id           = urm2.roles_id
                     AND r2._s_tenantid  = urm2._s_tenantid
                     AND r2._s_env       = urm2._s_env
                    WHERE urm2._s_tenantid    = urm._s_tenantid
                      AND urm2._s_env         = urm._s_env
                      AND urm2._s_is_deleted  = FALSE
                      AND r2._s_is_deleted    = FALSE
                      AND r2.active            = TRUE
                      AND LOWER(r2.name) IN ('admin', 'superadmin')
                      AND urm2.assign_start_date <= NOW()
                      AND (urm2.assign_end_date IS NULL OR urm2.assign_end_date > NOW())
                      AND urm2.id <> urm.id
                  )
            )
      )
) rp ON TRUE

-- Project membership via the resource bridge (project_member has no direct
-- user_id — access is user_account -> resource -> project_member -> project).
LEFT JOIN LATERAL (
    SELECT
        ARRAY_AGG(DISTINCT pm2.project_id::text)  AS project_ids,
        MAX(res._s_silver_ts)                     AS resource_silver_ts,
        MAX(pm2._s_silver_ts)                     AS member_silver_ts
    FROM silver.resource res
    JOIN silver.project_member pm2
      ON pm2.resource_id   = res.id
     AND pm2._s_tenantid   = res._s_tenantid
     AND pm2._s_env        = res._s_env
    WHERE res._s_tenantid   = ua._s_tenantid
      AND res._s_env        = ua._s_env
      AND res.user_id       = ua.id
      AND res._s_is_deleted = FALSE
      AND pm2._s_is_deleted = FALSE
      AND UPPER(pm2.status) = 'ACTIVE'
) pm ON TRUE

WHERE ua._s_tenantid  = %(tenantid)s
  AND ua.object_id    IS NOT NULL
  AND ua.deleted      = FALSE
ORDER BY ua.id
"""


def _scoped_query(user_account_id: str | None) -> str:
    """Return the Silver query, optionally scoped to a single user.

    Used by a future real-time streaming job the same way the other Eden
    loaders scope by project_id — kept symmetrical for that follow-up.
    """
    if user_account_id is None:
        return _SILVER_QUERY
    return _SILVER_QUERY.replace(
        "WHERE ua._s_tenantid  = %(tenantid)s",
        "WHERE ua._s_tenantid  = %(tenantid)s\n  AND ua.id = %(user_account_id)s",
        1,
    )


def iter_silver_rows(
    pg_dsn: str, tenantid: str, max_docs: int = 0, user_account_id: str | None = None
) -> Iterator[dict]:
    conn = psycopg2.connect(pg_dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            params: dict = {"tenantid": tenantid}
            if user_account_id is not None:
                params["user_account_id"] = user_account_id
            cur.execute(_scoped_query(user_account_id), params)
            rows = cur if max_docs <= 0 else (r for _, r in zip(range(max_docs), cur))
            for row in rows:
                yield dict(row)
    finally:
        conn.close()


def build_documents(
    pg_dsn: str,
    tenantid: str,
    gold_ts: datetime,
    user_account_id: str | None = None,
    max_docs: int = 0,
) -> list[dict]:
    """Build shaped Gold documents for a tenant (or a single user)."""
    return [
        shape_doc(row, gold_ts)
        for row in iter_silver_rows(pg_dsn, tenantid, max_docs, user_account_id)
    ]


def _has_new_silver_rows(pg_dsn: str, tenantid: str, since: datetime) -> bool:
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM silver.user_account"
            "  WHERE _s_tenantid = %s AND _s_silver_ts > %s"
            "  UNION ALL"
            "  SELECT 1 FROM silver.user_role_map"
            "  WHERE _s_tenantid = %s AND _s_silver_ts > %s"
            "  UNION ALL"
            "  SELECT 1 FROM silver.project_member"
            "  WHERE _s_tenantid = %s AND _s_silver_ts > %s"
            ")",
            (tenantid, since, tenantid, since, tenantid, since),
        )
        return bool(cur.fetchone()[0])


def _get_max_silver_ts(pg_dsn: str, tenantid: str) -> datetime:
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT GREATEST("
            "  COALESCE((SELECT MAX(_s_silver_ts) FROM silver.user_account   WHERE _s_tenantid = %s), '-infinity'::timestamptz),"
            "  COALESCE((SELECT MAX(_s_silver_ts) FROM silver.user_role_map  WHERE _s_tenantid = %s), '-infinity'::timestamptz),"
            "  COALESCE((SELECT MAX(_s_silver_ts) FROM silver.project_member WHERE _s_tenantid = %s), '-infinity'::timestamptz)"
            ")",
            (tenantid, tenantid, tenantid),
        )
        ts = cur.fetchone()[0]
    if ts is None:
        return EPOCH
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _to_ts(v) -> "datetime | None":
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo is not None else v.replace(tzinfo=timezone.utc)
    return None


def _display_name(row: dict) -> str | None:
    parts = [row.get("firstname"), row.get("middlename"), row.get("lastname")]
    name = " ".join(p.strip() for p in parts if p and p.strip())
    return name or None


def shape_doc(row: dict, gold_ts: datetime) -> dict:
    roles = sorted(set(row.get("roles") or []))
    permissions = sorted(set(row.get("permissions") or []))
    project_ids = sorted(set(row.get("project_ids") or []))

    watermarks = {
        "user_account":   _to_ts(row.get("ua_silver_ts")),
        "user_role_map":  _to_ts(row.get("role_silver_ts")),
        "role_permission": _to_ts(row.get("perm_silver_ts")),
        "project_member": _to_ts(row.get("member_silver_ts")),
    }
    valid_wm = [ts for ts in watermarks.values() if ts is not None]
    lag_ms = int((gold_ts - min(valid_wm)).total_seconds() * 1000) if valid_wm else 0

    doc = {
        "tenantId":        row["tenantid"],
        "user_uid":        row["user_uid"],
        "user_account_id": int(row["user_account_id"]),
        "displayName":     _display_name(row),
        "email":           row.get("email"),
        "isActive":        bool(row.get("active")) and not bool(row.get("deleted")),
        "roles":           roles,
        "permissions":     permissions,
        "project_ids":     project_ids,
        "_grain":          "user_permissions",
        "_schemaVersion":  1,
        "_silverLsn":      int(row.get("silver_lsn") or 0),
        "isDeleted":       bool(row.get("is_deleted") or False),
        "deletedAt":       parse_ts(row.get("deleted_at")),
        "dataAsOf":        gold_ts,
        "_silverWatermarks": watermarks,
        "lafieldcraft":           lag_ms,
    }
    doc.update(lineage_fields(FEATURE_NAME, gold_ts, iso=False,
                              source_updated_at=watermarks.get("user_account"),
                              source_env=row.get("source_env")))
    return doc


@dataclass
class WriteStats:
    upserted: int = 0
    modified: int = 0
    matched:  int = 0
    failed:   int = 0
    batches:  int = 0


_cb: CircuitBreaker | None = None


def bulk_write_with_retry(coll: Collection, ops: list[UpdateOne],
                          cfg: LoaderConfig, stats: WriteStats) -> None:
    delay = cfg.retry_backoff_s
    for attempt in range(1, cfg.max_retries + 1):
        try:
            global _cb
            if _cb is None:
                _cb = CircuitBreaker(name=coll.full_name)
            res = _cb.call(lambda: coll.bulk_write(ops, ordered=False))
            stats.upserted += res.upserted_count or 0
            stats.modified += res.modified_count or 0
            stats.matched  += res.matched_count  or 0
            stats.batches  += 1
            return
        except BulkWriteError as exc:
            errors = exc.details.get("writeErrors", []) if exc.details else []
            stats.failed += len(errors)
            log.error("bulk write partial failure: %d errors", len(errors))
            stats.batches += 1
            return
        except TRANSIENT_ERRORS as exc:
            if attempt == cfg.max_retries:
                raise
            log.warning("transient error attempt %d/%d: %s — retry %.1fs",
                        attempt, cfg.max_retries, exc, delay)
            time.sleep(delay)
            delay *= 2
        except OperationFailure as exc:
            log.error("operation failure (non-retryable): %s", exc)
            raise


_shutdown = False


def _install_signal_handlers() -> None:
    def _handler(signum, _frame):
        global _shutdown
        log.warning("signal %s — finishing current batch then stopping", signum)
        _shutdown = True
    signal.signal(signal.SIGINT, _handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handler)


def run(cfg: LoaderConfig) -> int:
    log.info("loader start | db=%s.%s tenant=%s dry_run=%s mongo=%s",
             cfg.db_name, COLLECTION, cfg.tenantid, cfg.dry_run, _mask_uri(cfg.mongo_uri))
    last_ts = read_cursor(cfg.pg_dsn, _STEP_NAME, cfg.tenantid)
    if cfg.rebuild:
        log.info("rebuild mode | full re-aggregate from current Silver; cursor will NOT be advanced")
    elif not _has_new_silver_rows(cfg.pg_dsn, cfg.tenantid, last_ts):
        log.info("no new Silver rows since %s — skipping full re-aggregate", last_ts.isoformat())
        return 0
    gold_ts = datetime.now(timezone.utc)
    stats = WriteStats()
    client: MongoClient | None = None
    coll: Collection | None = None
    if not cfg.dry_run:
        client = MongoClient(cfg.mongo_uri, serverSelectionTimeoutMS=15_000,
                             connectTimeoutMS=15_000, socketTimeoutMS=60_000,
                             retryWrites=False, appname="canonical-user-permissions")
        coll = client[cfg.db_name][COLLECTION]

    pending: list[dict] = []
    seen = 0

    def flush() -> None:
        if not pending:
            return
        for d in pending:
            _contract_validator.validate(d, COLLECTION)
        ops = [
            UpdateOne(
                {"tenantId": d["tenantId"], "user_uid": d["user_uid"],
                 "sourceEnv": d.get("sourceEnv"),
                 "$or": [{"_silverLsn": {"$exists": False}},
                         {"_silverLsn": {"$lte": d.get("_silverLsn", 0)}}]},
                {"$set": d, "$setOnInsert": set_on_insert(gold_ts, iso=False)},
                upsert=True,
            )
            for d in pending
        ]
        if ops and not cfg.dry_run and coll is not None:
            bulk_write_with_retry(coll, ops, cfg, stats)
        pending.clear()

    try:
        for row in iter_silver_rows(cfg.pg_dsn, cfg.tenantid, cfg.max_docs):
            if _shutdown:
                break
            seen += 1
            pending.append(shape_doc(row, gold_ts))
            if len(pending) >= cfg.batch_size:
                flush()
                log.info("progress | seen=%d upserted=%d", seen, stats.upserted)
        flush()
    except TRANSIENT_ERRORS as exc:
        mark_error(cfg.pg_dsn, _STEP_NAME, cfg.tenantid, exc)
        log.error("aborting after exhausted retries: %s", exc)
        return 3
    finally:
        if client:
            client.close()

    if cfg.dry_run:
        log.info("dry-run: cursor NOT advanced")
    elif cfg.rebuild:
        log.info("rebuild: cursor intentionally NOT advanced (incremental watermark preserved)")
    elif stats.failed > 0:
        mark_error(cfg.pg_dsn, _STEP_NAME, cfg.tenantid, f"{stats.failed} bulk write failures")
    else:
        max_ts = _get_max_silver_ts(cfg.pg_dsn, cfg.tenantid)
        if max_ts > last_ts:
            write_cursor(cfg.pg_dsn, _STEP_NAME, cfg.tenantid, max_ts)

    log.info("loader done | seen=%d upserted=%d modified=%d failed=%d batches=%d",
             seen, stats.upserted, stats.modified, stats.failed, stats.batches)
    return 0 if stats.failed == 0 else 3


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    _install_signal_handlers()
    try:
        cfg = LoaderConfig.from_args_and_env(argv)
    except RuntimeError as exc:
        log.error("config error: %s", exc)
        return 2
    return run(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
