import logging
import os
import re

from source.model.navigation_models import ProjectMatch
from source.utils.mongo_client import get_collection

logger = logging.getLogger(__name__)

# Real gold_eden schema (per datateam.md / live-verified): grain key is
# (tenantId, projectId, sourceEnv), name field is projectName. project_health
# itself has no usable "status" (always null, verified live) -- ProjectMatch's
# status defaults to "unknown" the same way it always has.
PROJECT_HEALTH_COLLECTION = "project_health"

# Semantic fallback (ENTITY_RESOLUTION_BACKEND=semantic): same in-process
# all-MiniLM-L6-v2 embeddings navigation_agent.py uses for route matching,
# applied here to catch what regex substring matching structurally can't --
# typos, nicknames, partial/reordered names ("river side job" vs "Riverside
# Ave Retrofit"). Only runs when the regex pass finds zero matches, so it
# never changes behavior for names that already resolve today. Default stays
# "regex" until verified against the regression suite -- same discipline as
# RETRIEVAL_BACKEND; falls back to regex-only on any init/embed failure
# rather than blocking resolution.
ENTITY_RESOLUTION_SEMANTIC_THRESHOLD = 0.55
ENTITY_RESOLUTION_SEMANTIC_POOL_LIMIT = 200

# Real bug, fixed: _resolve_by_individual_words retried EVERY word over 2
# characters as a candidate project name with no filtering at all -- for a
# phrase that named no project ("what is the cost"), that meant "what",
# "cost", and "the" each got a FULL resolve_project() attempt (regex +
# normalized + semantic), and the semantic tier alone is a real Qdrant
# collection delete+reindex+query round-trip (qdrant backend re-indexes on
# every call, see _semantic_resolve's docstring) -- never cached. Confirmed
# live: a single "what is the cost" request from a customer scoped to 12
# real projects triggered this fallback 24 times (~26 seconds) purely from
# retrying obviously-non-name filler words that were never going to match
# anything. Not an exhaustive stop-word list (that's ENTITY_STOP_WORDS'
# job, in navigation_agent.py, for a different purpose) -- just enough
# common English filler to stop the worst, most common offenders from ever
# reaching the expensive tiers.
# Cap on name-search results in resolve_project -- kept in sync with
# insight_agent.MAX_PROJECT_OPTIONS (not imported directly to avoid a
# circular import, insight_agent already imports FROM this module) so a
# name-based disambiguation offers exactly as many candidates as the
# no-name-given "list projects" path does.
NAME_MATCH_LIMIT = 10

# See _dedupe_latest_per_order_no's module comment -- list_projects overfetches
# by this factor before deduping so a project's own duplicate-orderNo sibling
# can't crowd a distinct project out of the page.
LIST_OVERFETCH_FACTOR = 3

_NON_NAME_WORDS = {
    "what", "when", "where", "which", "who", "why", "how",
    "the", "this", "that", "these", "those", "for", "with", "and",
    "cost", "cost's", "price", "cur", "current", "total", "much",
    "are", "was", "were", "been", "being", "does", "did", "doing",
    "can", "could", "would", "should", "will", "shall", "may", "might",
    "you", "your", "please", "tell", "show", "give", "want", "need",
}

# Whitespace/separator-insensitive substring match -- real gap found via
# live testing: "the oak wood job" (a natural, correctly-phrased reference)
# failed to resolve at all against "DEMO-Oakwood Bathroom Renovation".
# Regex requires the exact substring "oak wood" (with the space) to appear
# verbatim; it doesn't. Semantic embeddings scored the split-word phrase
# BELOW threshold against the one-word "Oakwood" -- a real, observed
# embedding-similarity miss, not a hypothetical. This tier sits between the
# two: collapse whitespace/hyphens/apostrophes on both sides and compare
# directly, which is exact-in-spirit (not a similarity guess) and catches
# exactly this class of miss. Runs unconditionally (cheap, in-memory, no
# embeddings) regardless of ENTITY_RESOLUTION_BACKEND -- unlike the semantic
# tier, this isn't a feature-flagged behavior change, it's closing a regex
# gap with more regex-like precision.
# Matching more than this many tenant projects via the normalized tier means
# the query was too generic (a shared prefix like "demo", not a real
# reference) -- reject it rather than presenting it as a disambiguation.
# Same cap as MAX_PROJECT_OPTIONS (insight_agent.py) -- a user picking
# between more than this stops being a quick choice anyway.
MAX_MEANINGFUL_NORMALIZED_MATCHES = 5

# Real bug, found live 2026-08-26: the source system creates a NEW internal
# project id (not an update to the existing row) when a draft project is
# converted/submitted -- confirmed on two real projects (order 977660: ids
# 1232 draft -> 1233 current; order 482999: ids 1242 draft -> 1243 current),
# no reference_project_id/parent_project_id link between them. Gold keys
# every collection by that raw internal id, so ONE real-world project can
# end up as TWO separate project_health documents sharing the same orderNo
# -- one frozen forever at whatever status the abandoned draft id last had,
# one live-updating under the new id. Every lookup below that can return
# more than one document for the same orderNo must dedupe on it, keeping
# only the one Gold most recently touched (`goldUpdatedAt` descending) --
# that's the row still receiving CDC-triggered rebuilds, i.e. the current
# one. A missing/null orderNo can never collide with anything (see
# `_dedupe_latest_per_order_no`'s grouping key), so this is a no-op for the
# (presumably rare) documents that lack one.
def _dedupe_latest_per_order_no(docs: list[dict]) -> list[dict]:
    """Given project_health documents possibly containing duplicates that
    share an orderNo, keep only the one with the greatest goldUpdatedAt per
    orderNo. Docs missing orderNo/goldUpdatedAt pass through unmerged (each
    keyed by its own identity, never grouped with another). Input order is
    not assumed sorted; this sorts internally. Output order is stable by
    orderNo, ascending (falls back to insertion order for docs without one)."""
    best: dict[object, dict] = {}
    order: list[object] = []
    for doc in docs:
        order_no = doc.get("orderNo")
        key = order_no if order_no is not None else id(doc)
        current = best.get(key)
        if current is None:
            best[key] = doc
            order.append(key)
            continue
        # goldUpdatedAt may be a real datetime (pymongo deserializes BSON
        # dates that way) or a plain string depending on caller/projection --
        # comparing across those types directly raises TypeError, so compare
        # via str() (fixed-width/zero-padded, still sorts correctly for
        # values from the same collection) -- but only once both sides are
        # confirmed non-None: a doc WITH a real timestamp must always beat
        # one with none, and str(None) == "None" sorts arbitrarily against a
        # real date string (e.g. "2026-..." < "None" lexicographically,
        # which would wrongly keep the undated doc).
        new_ts = doc.get("goldUpdatedAt")
        cur_ts = current.get("goldUpdatedAt")
        if new_ts is not None and (cur_ts is None or str(new_ts) > str(cur_ts)):
            best[key] = doc
    return [best[key] for key in order]


_ENTITY_NAME_SEPARATOR_RE = re.compile(r"[\s\-_'.]+")
# Strips a leading "order"/"order#"/"#" before an exact orderNo lookup --
# same filler-stripping discipline as _normalize_for_match, applied to
# order-number references instead of names.
_ORDER_NO_PREFIX_RE = re.compile(r"^(order\s*#?\s*|#\s*)", re.IGNORECASE)
# Trailing filler EXTRACT_PROJECT_SYSTEM_PROMPT is supposed to strip before
# this ever gets called ("the ... job/project") -- kept here too as a
# cheap safety net for whatever slips through un-stripped.
_ENTITY_NAME_FILLER_SUFFIXES = (" job", " project")


def _normalize_for_match(name: str) -> str:
    normalized = name.strip().lower()
    if normalized.startswith("the "):
        normalized = normalized[4:]
    for suffix in _ENTITY_NAME_FILLER_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
    return _ENTITY_NAME_SEPARATOR_RE.sub("", normalized)


class EntityResolver:
    def __init__(self):
        self.resolution_backend = os.getenv("ENTITY_RESOLUTION_BACKEND", "regex").strip().lower()
        self._embeddings_client = None
        self._vector_store = None
        if self.resolution_backend in ("semantic", "qdrant"):
            try:
                from source.utils.embeddings_client import EmbeddingsClient

                client = EmbeddingsClient()
                if not client.is_reachable():
                    raise RuntimeError("embeddings service not reachable")
                self._embeddings_client = client

                if self.resolution_backend == "qdrant":
                    from source.utils.qdrant_client import QdrantVectorStore

                    store = QdrantVectorStore()
                    if not store.is_reachable():
                        raise RuntimeError("qdrant not reachable")
                    self._vector_store = store
                    logger.info(
                        "Entity resolution backend: qdrant (all-MiniLM-L6-v2, persisted "
                        "to eden_project_names -- view at http://localhost:6333/dashboard)"
                    )
                else:
                    logger.info("Entity resolution backend: semantic (all-MiniLM-L6-v2, in-memory)")
            except Exception:
                logger.exception(
                    f"{self.resolution_backend}-backed entity resolution init failed — falling back to regex"
                )
                self.resolution_backend = "regex"
                self._embeddings_client = None
                self._vector_store = None

    def resolve_by_id(
        self, project_id: str, tenant_id: str, allowed_project_ids: list[str] | None = None
    ) -> list[ProjectMatch]:
        """Exact lookup by projectId -- used for page context ("the user is
        currently viewing this project"), where the frontend already knows
        the exact project id from the page's router state. Strictly better
        than name-based resolution for this case: exact, unambiguous, and
        immune to the duplicate-name problem in real data (where one name
        like 'vijay cus' maps to hundreds of projects). Returns a 0- or
        1-element list, mirroring resolve_project's shape so callers handle
        both the same way. `allowed_project_ids` scopes it the same way as
        the other methods (a non-empty list that excludes this id yields no
        match -- the user can't see a project outside their scope even by
        being on its page)."""
        if not project_id or not str(project_id).strip():
            return []
        try:
            query = {
                "tenantId": tenant_id, "projectId": str(project_id).strip(),
                "isDeleted": {"$ne": True},
            }
            if allowed_project_ids:
                query["projectId"] = {"$in": [pid for pid in allowed_project_ids if pid == str(project_id).strip()]}
                if not query["projectId"]["$in"]:
                    return []
            collection = get_collection(PROJECT_HEALTH_COLLECTION)
            doc = collection.find_one(
                query, {"projectId": 1, "projectName": 1, "status": 1, "orderNo": 1, "_id": 0}
            )
            if not doc:
                return []
            return [ProjectMatch(
                project_uid=str(doc["projectId"]),
                project_name=doc["projectName"],
                status=doc.get("status") or "unknown",
                order_no=doc.get("orderNo"),
            )]
        except Exception as e:
            logger.error(f"Entity resolver failed for projectId={project_id!r}: {e}")
            return []

    def list_projects(
        self, tenant_id: str, limit: int = 5, allowed_project_ids: list[str] | None = None
    ) -> list[ProjectMatch]:
        """All projects for a tenant, unfiltered by name -- used when the user
        hasn't named a project at all.

        `allowed_project_ids`: pass a user's Gold `user_permissions.project_ids`
        here to enforce it -- a NON-EMPTY list filters results to only those
        projects. An empty list or None is treated as "no scoping data
        available" (every tenant project), NOT "user has zero projects" --
        real Gold data has project_ids empty for effectively all users today
        (a known, separately-tracked gap), and there is no way to distinguish
        "not populated yet" from "genuinely assigned nothing" from an empty
        list alone. See UserRoleResolver.get_project_ids for the full
        reasoning. Callers that never pass this get today's existing,
        unscoped behavior unchanged."""
        try:
            query = {"tenantId": tenant_id, "isDeleted": {"$ne": True}}
            if allowed_project_ids:
                query["projectId"] = {"$in": allowed_project_ids}
            collection = get_collection(PROJECT_HEALTH_COLLECTION)
            # Overfetch before deduping (see _dedupe_latest_per_order_no):
            # applying `limit` at the query level would risk filling the
            # whole page with duplicate-orderNo siblings of the SAME real
            # project, silently shrinking how many distinct projects a
            # caller actually sees. LIST_OVERFETCH_FACTOR trades a larger
            # query for correctness -- observed real duplication is only
            # ~2x (one abandoned draft doc per current doc), 3x leaves
            # headroom without being unbounded.
            cursor = collection.find(
                query,
                {"projectId": 1, "projectName": 1, "status": 1, "orderNo": 1,
                 "goldUpdatedAt": 1, "_id": 0}
            ).sort("orderNo", 1).limit(limit * LIST_OVERFETCH_FACTOR)

            # Real bug, fixed: limit=0 is Mongo's own "no limit" convention
            # (used by mobile's _project_list_limit, see insight_agent.py) --
            # the .limit(0) call above already honors it correctly, but a
            # bare Python slice doesn't: [:0] means "take zero", the
            # opposite of unbounded. Without this, mobile's "list every
            # project" fetch silently returned an empty list.
            all_deduped = _dedupe_latest_per_order_no(list(cursor))
            deduped = all_deduped if limit == 0 else all_deduped[:limit]
            return [
                ProjectMatch(
                    project_uid=str(doc["projectId"]),
                    project_name=doc["projectName"],
                    status=doc.get("status") or "unknown",
                    order_no=doc.get("orderNo"),
                )
                for doc in deduped
            ]
        except Exception as e:
            logger.error(f"Entity resolver failed to list projects for tenant='{tenant_id}': {e}")
            return []

    def count_projects(
        self, tenant_id: str, allowed_project_ids: list[str] | None = None
    ) -> int:
        """True total matching list_projects' own filter, UNCAPPED by
        `limit` -- lets a caller tell the user honestly "showing 5 of 12"
        instead of silently truncating with no indication more exist (real
        gap, fixed: list_projects' own `limit` means projects beyond the cap
        were never even fetched, not just hidden from display -- a customer
        whose intended project wasn't in the first N returned had no way to
        reach it via the disambiguation list at all). Returns 0 on failure
        -- callers must treat that as "don't know," not "there are none"
        (list_projects' own result is still the source of truth for what to
        actually show)."""
        try:
            query = {"tenantId": tenant_id, "isDeleted": {"$ne": True}}
            if allowed_project_ids:
                query["projectId"] = {"$in": allowed_project_ids}
            collection = get_collection(PROJECT_HEALTH_COLLECTION)
            # count_documents would double-count a project that has a
            # duplicate-orderNo sibling doc (see _dedupe_latest_per_order_no) --
            # count distinct orderNo values instead so this matches what
            # list_projects' own deduped output would show. distinct()
            # collapses missing/null orderNo into one bucket too, so those
            # are counted separately (each such doc is its own project, same
            # as _dedupe_latest_per_order_no treats them).
            has_order_no = {**query, "orderNo": {"$ne": None}}
            no_order_no = {**query, "orderNo": None}
            distinct_count = len(collection.distinct("orderNo", has_order_no))
            null_count = collection.count_documents(no_order_no)
            return distinct_count + null_count
        except Exception as e:
            logger.error(f"Entity resolver failed to count projects for tenant='{tenant_id}': {e}")
            return 0

    def project_ids_for_customer(self, tenant_id: str, customer_uid: str) -> list[str]:
        """Real, populated alternative to user_permissions.project_ids for the
        customer role -- that field is empty for effectively every user in
        real data today (see list_projects' docstring), but project_health
        independently carries the SAME customer identity on every project
        it belongs to, at `project.customer.customerUuid` (confirmed live:
        matches the customer's own JWT-derived user_uid exactly). Querying
        by this field lets a customer resolve to their own real project(s)
        today, without waiting on Data Engineering to populate project_ids.

        Returns an empty list on no match or any failure -- callers must
        still treat that as "show nothing" for the customer role (fail
        closed), same as an unpopulated project_ids would."""
        try:
            collection = get_collection(PROJECT_HEALTH_COLLECTION)
            cursor = collection.find(
                {
                    "tenantId": tenant_id, "project.customer.customerUuid": customer_uid,
                    "isDeleted": {"$ne": True},
                },
                {"projectId": 1, "_id": 0},
            )
            return [str(doc["projectId"]) for doc in cursor if doc.get("projectId")]
        except Exception as e:
            logger.error(
                f"project_ids_for_customer lookup failed for "
                f"tenant='{tenant_id}' customer_uid='{customer_uid}': {e}"
            )
            return []

    def project_ids_for_assignee(self, tenant_id: str, user_id: str) -> list[str]:
        """Real, populated scoping source for internal roles (admin/PM) --
        each project_health doc now carries an `assignees` array (confirmed
        live: [{"name": ..., "userUid": ..., "roles": [...]}]), the same
        role/project link `assignees.userUid` matches the JWT-derived
        user_uid exactly, mirroring project_ids_for_customer's own
        customerUuid link. Querying by this lets an internal user resolve
        to the projects they're actually assigned to instead of the whole
        tenant, once real assignment data exists.

        Returns an empty list on no match or any failure. Callers must NOT
        treat empty the same way project_ids_for_customer's callers do --
        an internal account with no assignee entries anywhere is far more
        likely incomplete data-rollout than an intentional "sees nothing"
        boundary (unlike the customer case, this doesn't fail closed --
        see AgentOrchestrator._scoped_project_ids for how it falls back)."""
        try:
            collection = get_collection(PROJECT_HEALTH_COLLECTION)
            cursor = collection.find(
                {
                    "tenantId": tenant_id, "assignees.userUid": user_id,
                    "isDeleted": {"$ne": True},
                },
                {"projectId": 1, "_id": 0},
            )
            return [str(doc["projectId"]) for doc in cursor if doc.get("projectId")]
        except Exception as e:
            logger.error(
                f"project_ids_for_assignee lookup failed for "
                f"tenant='{tenant_id}' user_id='{user_id}': {e}"
            )
            return []

    def find_named_project_in_text(
        self, text: str, tenant_id: str, allowed_project_ids: list[str] | None = None
    ) -> str | None:
        """Data-grounded safety net for LLM name-extraction misses: does the
        raw utterance LITERALLY contain a real project name from this
        tenant's data? Returns that canonical name if exactly one distinct
        name matches, else None.

        Real bug this exists for: extract_project_name() (an LLM call)
        occasionally drops a genuine name it should have extracted -- most
        reproducibly "Peter Parker" (a real customer here whose name also
        happens to be Spider-Man's, which biases the model toward treating
        it as fictional and returning null). Prompt tuning could not fully
        fix that bias. This check overrides the model with the ground truth:
        the name is real if and only if it appears in project_health, full
        stop -- no stop-word guessing, no similarity threshold, just "is
        this string actually a project's name in the database." Immune to
        the model's world-knowledge bias, and structurally cannot
        false-positive on a question that names no project (nothing named
        'cost'/'status'/'address' exists to match). Only consulted AFTER the
        LLM returns no name, so it never overrides a successful extraction.

        Normalized (whitespace/hyphen/case-insensitive) substring match,
        same discipline as _normalize_for_match -- so 'peter parker' in the
        utterance matches the stored 'Peter Parker'. A distinct name shorter
        than 3 normalized chars is skipped to avoid trivial substring hits."""
        try:
            query = {"tenantId": tenant_id, "isDeleted": {"$ne": True}}
            if allowed_project_ids:
                query["projectId"] = {"$in": allowed_project_ids}
            collection = get_collection(PROJECT_HEALTH_COLLECTION)
            names = collection.distinct("projectName", query)
        except Exception as e:
            logger.error(
                f"find_named_project_in_text lookup failed for tenant='{tenant_id}': {e}"
            )
            return None

        haystack = _normalize_for_match(text)
        matched = [
            name for name in names
            if isinstance(name, str)
            and len(_normalize_for_match(name)) >= 3
            and _normalize_for_match(name) in haystack
        ]
        if len(set(matched)) == 1:
            logger.info(
                "Data-grounded name recovery: utterance %r contains real project name %r "
                "(LLM extraction had returned no name)", text, matched[0],
            )
            return matched[0]
        return None

    def find_ordered_project_in_text(
        self, text: str, tenant_id: str, allowed_project_ids: list[str] | None = None
    ) -> str | None:
        """Same data-grounded safety net as find_named_project_in_text, for
        the ORDER NUMBER half of extract_project_name's job instead of the
        name half.

        Real bug this exists for: confirmed live -- "what is the project
        cost for 682053" (a bare 6-digit order number, no "order"/"job
        number"/"#" prefix) got a null extraction from the LLM despite
        EXTRACT_PROJECT_SYSTEM_PROMPT explicitly instructing it to treat "a
        bare number in a context clearly about a specific project" as an
        order number -- confirmed the real project resolves instantly by a
        direct query against project_health.orderNo, so this is purely an
        instruction-following miss on a small local model, not a data or
        query-logic problem. Same fix shape as the name-recovery method:
        don't trust the model's negative alone when the ground truth is a
        single Mongo lookup away, checked only AFTER the LLM returns no
        name/number so a successful extraction is never second-guessed.

        Extracts every standalone digit run of 4+ characters from the raw
        utterance (order numbers observed live are 6 digits; 4+ leaves
        margin without matching short numbers like a day-of-month) and
        checks each against this tenant's real orderNo values in one query
        -- returns the order number if exactly one candidate is real,
        mirroring find_named_project_in_text's "ambiguous means don't
        guess" rule for more than one hit."""
        candidates = re.findall(r"\b\d{4,}\b", text)
        if not candidates:
            return None
        try:
            query = {
                "tenantId": tenant_id, "orderNo": {"$in": candidates},
                "isDeleted": {"$ne": True},
            }
            if allowed_project_ids:
                query["projectId"] = {"$in": allowed_project_ids}
            collection = get_collection(PROJECT_HEALTH_COLLECTION)
            matched = collection.distinct("orderNo", query)
        except Exception as e:
            logger.error(
                f"find_ordered_project_in_text lookup failed for tenant='{tenant_id}': {e}"
            )
            return None
        if len(matched) == 1:
            logger.info(
                "Data-grounded order-number recovery: utterance %r contains real orderNo %r "
                "(LLM extraction had returned no name/number)", text, matched[0],
            )
            return matched[0]
        return None

    def resolve_project(
        self, name: str, tenant_id: str, allowed_project_ids: list[str] | None = None
    ) -> list[ProjectMatch]:
        """See list_projects for what `allowed_project_ids` does and why an
        empty list doesn't mean "show nothing".

        Real gap, fixed: `orderNo` (a real, unambiguous identifier --
        `project_health.orderNo`) was never checked anywhere -- only
        `projectName` was ever searched. A reference like "order 900001" or
        a bare order number resolved to nothing. Checked FIRST, as an exact
        match (never fuzzy/regex -- an order number is either right or it
        isn't, unlike a name), before the name-based chain below."""
        if not name or not name.strip():
            return []
        stripped = name.strip()

        order_match = self._resolve_by_order_no(stripped, tenant_id, allowed_project_ids)
        if order_match:
            return order_match

        try:
            query = {
                "tenantId": tenant_id,
                "projectName": {"$regex": re.compile(re.escape(stripped), re.IGNORECASE)},
                "isDeleted": {"$ne": True},
            }
            if allowed_project_ids:
                query["projectId"] = {"$in": allowed_project_ids}
            collection = get_collection(PROJECT_HEALTH_COLLECTION)
            # Real bug, fixed: confirmed live -- this used to cap at 3 with NO
            # sort at all. Real Gold data has severe projectName duplication
            # (a dozen+ projects can share one name), and Mongo's default
            # document order with no explicit sort isn't meaningful or
            # stable -- a specific project (e.g. order 272000) could simply
            # never appear among an arbitrary first 3, with no way to reach
            # it by name at all (only by already knowing its order number).
            # Raised to match MAX_PROJECT_OPTIONS (insight_agent.py) for
            # consistency with the other "list candidate projects" path, and
            # sorted by orderNo so results are deterministic across calls
            # instead of whatever order Mongo's internal storage happens to
            # return.
            # No limit at the query level: a name match must be deduped by
            # orderNo (see _dedupe_latest_per_order_no) BEFORE NAME_MATCH_LIMIT
            # is applied, or a legitimate distinct project could be pushed out
            # of the cap by its own duplicate-orderNo sibling.
            cursor = collection.find(
                query,
                {"projectId": 1, "projectName": 1, "status": 1, "orderNo": 1,
                 "goldUpdatedAt": 1, "_id": 0}
            ).sort("orderNo", 1)

            deduped = _dedupe_latest_per_order_no(list(cursor))[:NAME_MATCH_LIMIT]
            results = [
                ProjectMatch(
                    project_uid=str(doc["projectId"]),
                    project_name=doc["projectName"],
                    status=doc.get("status") or "unknown",
                    order_no=doc.get("orderNo"),
                )
                for doc in deduped
            ]
        except Exception as e:
            logger.error(f"Entity resolver failed for name='{name}': {e}")
            return []

        if results:
            return results

        normalized_matches = self._normalized_resolve(name, tenant_id, allowed_project_ids)
        if normalized_matches:
            return normalized_matches

        if self.resolution_backend in ("semantic", "qdrant") and self._embeddings_client is not None:
            semantic_matches = self._semantic_resolve(name, tenant_id, allowed_project_ids)
            if semantic_matches:
                return semantic_matches

        # Last resort: the full phrase matched nothing, but it may still
        # contain a real project name plus extra conversational text a
        # stop-word list didn't fully strip -- real bug, found live: a
        # goal-continuation reply like "peter invoice details" (answering
        # "which project?") has "invoice" left over after filler-stripping
        # ("details" is filler, "invoice" isn't project-name filler either),
        # so the whole two-word phrase is searched verbatim and matches
        # nothing, even though "Peter Parker" is a real, obviously-intended
        # project. Only tried when the phrase has more than one word --
        # single words already went through every tier above.
        if len(stripped.split()) > 1:
            return self._resolve_by_individual_words(stripped, tenant_id, allowed_project_ids)
        return []

    def _resolve_by_individual_words(
        self, name: str, tenant_id: str, allowed_project_ids: list[str] | None = None
    ) -> list[ProjectMatch]:
        """Retries each individual word of a multi-word phrase that failed to
        resolve as a whole, longest word first (a distinctive word like
        "peter" is more likely the actual name than a short common one).
        Stops at the first word that resolves to something. Each retry is a
        single word, so it can never re-enter this same fallback (guarded by
        the `len(stripped.split()) > 1` check in resolve_project) --
        recursion terminates in one level."""
        words = [
            w for w in name.split()
            if len(w) > 2 and w.lower() not in _NON_NAME_WORDS
        ]
        for word in sorted(set(words), key=len, reverse=True):
            matches = self.resolve_project(word, tenant_id, allowed_project_ids=allowed_project_ids)
            if matches:
                return matches
        return []

    def _resolve_by_order_no(
        self, name: str, tenant_id: str, allowed_project_ids: list[str] | None = None
    ) -> list[ProjectMatch]:
        """Exact match against project_health.orderNo -- a real, unambiguous
        identifier, never fuzzy-matched (unlike projectName). Strips a
        leading "order"/"order#"/"#" the same way name-matching strips "the
        .../job/project" filler, as a safety net for whatever the LLM
        extraction didn't already clean up. Harmless when the candidate
        isn't actually an order number -- just no document matches."""
        candidate = _ORDER_NO_PREFIX_RE.sub("", name).strip()
        if not candidate:
            return []
        try:
            query = {"tenantId": tenant_id, "orderNo": candidate, "isDeleted": {"$ne": True}}
            if allowed_project_ids:
                query["projectId"] = {"$in": allowed_project_ids}
            collection = get_collection(PROJECT_HEALTH_COLLECTION)
            # find_one (no sort) used to just take whichever doc Mongo
            # happened to store first -- when this orderNo has a duplicate
            # (see _dedupe_latest_per_order_no's module comment), that could
            # arbitrarily be the abandoned draft-id doc instead of the live
            # one. Sort by goldUpdatedAt descending and take the first
            # result so this always resolves to the currently-updating doc.
            cursor = collection.find(
                query, {"projectId": 1, "projectName": 1, "status": 1, "orderNo": 1,
                        "goldUpdatedAt": 1, "_id": 0}
            ).sort("goldUpdatedAt", -1).limit(1)
            doc = next(iter(cursor), None)
            if not doc:
                return []
            logger.info(
                "Order-number match: name=%r -> orderNo=%r project=%r",
                name, candidate, doc["projectName"],
            )
            return [ProjectMatch(
                project_uid=str(doc["projectId"]),
                project_name=doc["projectName"],
                status=doc.get("status") or "unknown",
                order_no=doc.get("orderNo"),
            )]
        except Exception as e:
            logger.error(f"Order-number lookup failed for name={name!r}: {e}")
            return []

    def _normalized_resolve(
        self, name: str, tenant_id: str, allowed_project_ids: list[str] | None = None
    ) -> list[ProjectMatch]:
        """Fallback only: regex found nothing. See the module-level comment
        above _normalize_for_match for why this exists and what it fixes.

        Real bug, fixed: an extracted name like "demo project" strips to
        just "demo" after removing filler -- and since every project in a
        synthetic/demo tenant shares a "DEMO-" prefix, that trivial fragment
        matched ALL of them, presented as if it were a genuine disambiguation
        rather than what it actually is: a query too generic to mean
        anything. Matching a large fraction of the tenant's own project list
        isn't a real signal -- reject it and let semantic (real similarity
        scoring) or "no match" handle it instead."""
        query_norm = _normalize_for_match(name)
        if not query_norm:
            return []
        candidates = self.list_projects(
            tenant_id, limit=ENTITY_RESOLUTION_SEMANTIC_POOL_LIMIT,
            allowed_project_ids=allowed_project_ids,
        )
        matches = [
            c for c in candidates
            if query_norm in _normalize_for_match(c.project_name)
            or _normalize_for_match(c.project_name) in query_norm
        ]
        if not matches:
            return []
        if len(matches) > MAX_MEANINGFUL_NORMALIZED_MATCHES:
            logger.info(
                "Normalized match rejected as too generic: name=%r matched "
                "%d/%d tenant projects -- not a real disambiguation signal",
                name, len(matches), len(candidates),
            )
            return []
        logger.info(
            "Normalized (separator-insensitive) match: name=%r -> %s",
            name, [m.project_name for m in matches],
        )
        return matches

    def _semantic_resolve(
        self, name: str, tenant_id: str, allowed_project_ids: list[str] | None = None
    ) -> list[ProjectMatch]:
        """Fallback only: regex found nothing. Embeds the given name against
        every tenant project's name and returns whichever clear cosine
        matches above threshold, best first, so callers get the same
        exactly-one/ambiguous/none-shape they already handle for regex
        results (no separate disambiguation path needed upstream). With the
        qdrant backend, project vectors are (re)indexed into the persisted
        eden_project_names collection on every call rather than cached --
        simplicity over micro-optimizing a fallback path that's already
        rare (only reached when regex misses), and it guarantees the
        dashboard always reflects this tenant's current project set."""
        candidates = self.list_projects(
            tenant_id, limit=ENTITY_RESOLUTION_SEMANTIC_POOL_LIMIT,
            allowed_project_ids=allowed_project_ids,
        )
        if not candidates:
            return []
        try:
            project_names = [p.project_name for p in candidates]
            query_vec = self._embeddings_client.embed([name])[0]
            project_vecs = self._embeddings_client.embed(project_names)

            if self.resolution_backend == "qdrant" and self._vector_store is not None:
                self._vector_store.index_projects(
                    tenant_id,
                    [p.project_uid for p in candidates],
                    project_names,
                    project_vecs,
                )
                hits = self._vector_store.search_projects(tenant_id, query_vec, k=3)
                by_id = {p.project_uid: p for p in candidates}
                matches = [
                    by_id[project_id] for project_id, _, score in hits
                    if score >= ENTITY_RESOLUTION_SEMANTIC_THRESHOLD and project_id in by_id
                ]
            else:
                from sklearn.metrics.pairwise import cosine_similarity

                scores = cosine_similarity([query_vec], project_vecs)[0]
                ranked = sorted(zip(candidates, scores), key=lambda pair: pair[1], reverse=True)
                matches = [p for p, score in ranked if score >= ENTITY_RESOLUTION_SEMANTIC_THRESHOLD][:3]

            if matches:
                logger.info(
                    "Semantic entity resolution (%s) matched name=%r -> %s",
                    self.resolution_backend, name, [p.project_name for p in matches],
                )
            return matches
        except Exception:
            logger.exception(f"Semantic entity resolution failed for name='{name}'")
            return []
