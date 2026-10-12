"""Gold document contract validator.

Validates Gold documents against their JSON Schema contracts before writing
to Cosmos DB. Catches schema violations at write time rather than silently
accepting invalid data.

Usage in loaders:
    from gold.src.monitoring.contract_validator import ContractValidator

    validator = ContractValidator("bpe_estimate")

    # In flush():
    for doc in pending:
        violation = validator.check(doc)
        if violation:
            log.warning("contract violation | collection=%s: %s", COLLECTION, violation)

Env vars:
    GOLD_CONTRACT_VALIDATION   "warn" (default) | "error" | "off"
      warn  — log violations but continue writing
      error — raise on first violation, abort batch
      off   — skip validation entirely (production perf mode)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("gold.contract_validator")

_MODE = os.environ.get("GOLD_CONTRACT_VALIDATION", "warn").lower()
_CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts" / "gold"

# Required fields every Gold document must have.
# _etlUpdatedAt was dropped 2026-07-12 (consolidated into goldUpdatedAt — see
# gold/pipeline/lineage.py's "Consolidation" note); featureName was never enforced
# here and was dropped from the lineage block in the same pass.
_REQUIRED_FIELDS = {"tenantId", "_grain", "_schemaVersion", "goldUpdatedAt"}


def _load_contract(contract_name: str) -> dict | None:
    """Load a JSON Schema contract by name from contracts/gold/.

    Contracts live in per-feature subdirectories (contracts/gold/eden/<name>.json,
    contracts/gold/bpe/<name>.json, ...), so a flat ``_CONTRACTS_DIR / name.json``
    lookup never found them — full validation silently degraded to the 4
    platform-field check. Search recursively so subdir contracts are found.

    Read with ``utf-8-sig``, not ``utf-8``: 18 of the 19 files under
    contracts/gold/ were saved with a UTF-8 BOM (only canonical_user_permissions.json
    lacks one), which ``json.loads`` on a plain ``utf-8``-decoded string rejects as
    "Unexpected UTF-8 BOM". That exception was being caught by the blanket
    ``except (json.JSONDecodeError, OSError): return None`` below and silently
    treated as "no contract found" — so contract validation had been silently
    degraded to the 4-platform-field check for nearly every Gold collection, not
    just the ones affected by the path bug. ``utf-8-sig`` strips a leading BOM if
    present and is otherwise identical to ``utf-8``, so it is safe for both BOM
    and non-BOM files.
    """
    # Fast path: top-level file (kept for any flat contracts / tests).
    path = _CONTRACTS_DIR / f"{contract_name}.json"
    if not path.exists():
        # Descend into per-feature subdirs (eden/, bpe/, qa_recommendation/).
        matches = sorted(_CONTRACTS_DIR.rglob(f"{contract_name}.json"))
        if not matches:
            return None
        path = matches[0]
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return None


class ContractValidator:
    """Validates Gold documents against their JSON Schema contract.

    Args:
        contract_name: filename without extension (e.g. "bpe_estimate")
        mode: "warn" | "error" | "off" — overrides GOLD_CONTRACT_VALIDATION env var
    """

    def __init__(self, contract_name: str, mode: str | None = None) -> None:
        self.contract_name = contract_name
        self.mode = (mode or _MODE).lower()
        self._contract = _load_contract(contract_name)

    def check(self, doc: dict[str, Any]) -> str | None:
        """Validate document. Returns error message string or None if valid.

        Two levels, both pure dict ops (no jsonschema dependency):
          1. The 4 platform fields every Gold doc must carry.
          2. If the contract loaded, its own ``required`` business fields plus a
             conservative null guard (a field present-but-null where the schema
             type does not permit null). Type enforcement is deliberately NOT
             done: pre-write docs hold native ``datetime`` objects for fields the
             contract types as ``string`` (date-time), so a strict type check
             would false-fail. Missing-required-field is the high-value,
             zero-false-positive signal — a dropped field is exactly the
             "wrong shape" regression that previously slipped through.
        """
        if self.mode == "off":
            return None

        # Always check required platform fields, even with no contract on disk.
        missing = _REQUIRED_FIELDS - set(doc.keys())
        if missing:
            return f"missing required platform fields: {sorted(missing)}"

        if not self._contract:
            return None

        required = self._contract.get("required", [])
        missing_contract = set(required) - set(doc.keys())
        if missing_contract:
            return f"missing required contract fields: {sorted(missing_contract)}"

        # Conservative null guard: a present field that is None where the
        # contract's declared JSON-Schema `type` does not include "null".
        for field, spec in self._contract.get("properties", {}).items():
            if not isinstance(spec, dict) or field not in doc or doc[field] is not None:
                continue
            types = spec.get("type")
            if types is None:
                continue
            if isinstance(types, str):
                types = [types]
            if "null" not in types:
                return f"field '{field}' is null but contract disallows null"

        return None

    def validate(self, doc: dict[str, Any], collection: str = "") -> None:
        """Validate document and act based on mode.

        Args:
            doc: Gold document to validate
            collection: collection name for logging context

        Raises:
            ValueError: if mode="error" and document is invalid
        """
        violation = self.check(doc)
        if violation is None:
            return

        label = collection or self.contract_name
        if self.mode == "error":
            raise ValueError(
                f"Gold contract violation [{label}] — {violation}"
            )
        else:  # warn
            log.warning(
                "Gold contract violation [%s] — %s | grain=%s tenant=%s",
                label,
                violation,
                doc.get("_grain", "?"),
                doc.get("tenantId", "?"),
            )
