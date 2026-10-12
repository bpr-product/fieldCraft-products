"""
MissingTenantCorrector — recovers a tenant id from the payload itself.

The Silver normalizer (universal_silver_merge._normalise) checks a small set of
flat field names: tenant_id, tenantId, source_db. This corrector is more
thorough: it walks the payload's nested `source` block (where Debezium emits
database/tenant context) and other commonly-used aliases before giving up.

If a value is found, the corrector mutates the envelope's raw_full payload
to inject `tenantid=<found>` and writes the patched payload back, so the
watcher's replay step publishes the corrected event. (raw_full, not
raw_truncated — raw_truncated is a 500-char preview only; patching it would
either operate on already-clipped JSON or produce a "corrected" payload the
watcher must not republish. See shared.dlq.errors for the raw_full/
raw_truncated split.)

If no derivable tenant exists, REFUSE — guessing a tenant would violate the
per-tenant isolation guarantee. The event parks for human review.
"""
from __future__ import annotations

import json
import logging

from dlq_watcher.correctors.base import CorrectionOutcome, CorrectionResult

log = logging.getLogger(__name__)


# Search order. First non-empty value wins.
# Flat field names checked at the top level.
_FLAT_KEYS = ("tenantid", "tenant_id", "tenantId", "source_db")

# Nested paths — dotted, evaluated against the parsed JSON.
_NESTED_PATHS = (
    ("source", "db"),
    ("source", "tenant_id"),
    ("source", "tenantId"),
    ("source", "tenantid"),
    ("source", "name"),    # Debezium often uses "name" as a logical DB identifier
    ("payload", "source", "db"),
    ("payload", "source", "tenant_id"),
)


def _lookup_nested(obj: dict, path: tuple[str, ...]):
    cursor = obj
    for segment in path:
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(segment)
        if cursor is None:
            return None
    return cursor


def find_tenant_in_payload(payload: dict) -> tuple[str | None, str | None]:
    """
    Search `payload` for a tenant identifier. Returns (value, source_path) where
    source_path is a human-readable indicator of where the value was found.
    None / None when nothing usable was found.
    """
    for k in _FLAT_KEYS:
        v = payload.get(k)
        if v and str(v).strip():
            return str(v).strip(), k
    for path in _NESTED_PATHS:
        v = _lookup_nested(payload, path)
        if v and str(v).strip():
            return str(v).strip(), ".".join(path)
    return None, None


class MissingTenantCorrector:
    corrector_id = "missing_tenant.v1"

    def correct(self, envelope: dict) -> CorrectionResult:
        # raw_full is the complete, untruncated payload — raw_truncated is a
        # 500-char preview and must never be parsed/republished as if it were
        # the whole record (a truncated JSON string may not even parse, and
        # if it does, it silently drops trailing fields on replay).
        raw = envelope.get("raw_full") or ""
        if not raw:
            return CorrectionResult(
                outcome=CorrectionOutcome.REFUSED,
                reason="envelope has no raw_full to inspect",
            )

        try:
            payload = json.loads(raw)
        except Exception as exc:
            return CorrectionResult(
                outcome=CorrectionOutcome.REFUSED,
                reason=f"raw_full is not parseable JSON: {exc}",
            )

        if not isinstance(payload, dict):
            return CorrectionResult(
                outcome=CorrectionOutcome.REFUSED,
                reason="payload is not a JSON object",
            )

        tenant, source_path = find_tenant_in_payload(payload)
        if not tenant:
            return CorrectionResult(
                outcome=CorrectionOutcome.REFUSED,
                reason="no tenant id found in any known payload location",
                original_value={"checked": list(_FLAT_KEYS) + [".".join(p) for p in _NESTED_PATHS]},
            )

        # Patch tenantid into the payload and write back. We mutate the
        # envelope dict in place so the watcher's _handle_correct picks up
        # the corrected raw_full when it replays.
        patched = dict(payload)
        patched["tenantid"] = tenant
        envelope["raw_full"] = json.dumps(patched, default=str)
        envelope["tenantid"] = tenant  # also patch the envelope field for audit

        log.info("missing_tenant: recovered '%s' from %s", tenant, source_path)
        return CorrectionResult(
            outcome=CorrectionOutcome.APPLIED,
            reason=f"recovered tenant from {source_path}",
            original_value={"tenantid": None, "source_searched": source_path},
            corrected_value={"tenantid": tenant},
            actions_taken=[f"patched payload.tenantid from {source_path}"],
        )
