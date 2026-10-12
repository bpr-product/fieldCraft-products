#!/usr/bin/env python3
"""Cross-service consistency check (PR review, Raja): EDEN's
PRESIDIO_PLACEHOLDER_RE (src/eden/source/utils/llm_client.py) hand-duplicates
guardrails' DEFAULT_PRESIDIO_ENTITIES (src/guardrails/app/config.py) -- EDEN
can't import that module at runtime (separate deployable service, separate
Docker build context), so nothing enforces the two staying in sync except
this script.

Filed under src/eden/scripts/ (EDEN owns/maintains this check), but it still
reads BOTH src/eden/ and src/guardrails/ -- it must be run from a full
monorepo checkout, never from either service's own deployed container/build
context (each only has its own service directory, not the sibling it
compares against):

    python3 src/eden/scripts/check_presidio_entities_sync.py

Exits non-zero and prints the mismatch if the two lists ever drift apart.
"""
import re
import sys
from pathlib import Path

# Four levels up from this file (scripts/ -> eden/ -> src/ -> repo root) --
# NOT parent.parent, which was correct only when this script lived directly
# under the repo-root scripts/ folder (one level shallower).
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _guardrails_default_entities() -> set[str]:
    config_path = REPO_ROOT / "src/guardrails/app/config.py"
    text = config_path.read_text()
    match = re.search(r'DEFAULT_PRESIDIO_ENTITIES\s*=\s*"([^"]+)"', text)
    if not match:
        raise RuntimeError(f"Could not find DEFAULT_PRESIDIO_ENTITIES in {config_path}")
    return {e.strip() for e in match.group(1).split(",") if e.strip()}


def _eden_placeholder_entities() -> set[str]:
    llm_client_path = REPO_ROOT / "src/eden/source/utils/llm_client.py"
    text = llm_client_path.read_text()
    match = re.search(
        r"PRESIDIO_PLACEHOLDER_RE\s*=\s*re\.compile\(\s*r[\"']<\(([^)]+)\)>[\"']\s*\)",
        text,
    )
    if not match:
        raise RuntimeError(f"Could not find PRESIDIO_PLACEHOLDER_RE in {llm_client_path}")
    return {e.strip() for e in match.group(1).split("|") if e.strip()}


def main() -> int:
    guardrails_entities = _guardrails_default_entities()
    eden_entities = _eden_placeholder_entities()
    if guardrails_entities == eden_entities:
        print(f"OK -- both lists match: {sorted(guardrails_entities)}")
        return 0

    print("MISMATCH between guardrails' DEFAULT_PRESIDIO_ENTITIES and "
          "EDEN's PRESIDIO_PLACEHOLDER_RE:")
    only_guardrails = guardrails_entities - eden_entities
    only_eden = eden_entities - guardrails_entities
    if only_guardrails:
        print(f"  in guardrails only: {sorted(only_guardrails)}")
    if only_eden:
        print(f"  in EDEN only:       {sorted(only_eden)}")
    print("Update src/eden/source/utils/llm_client.py's PRESIDIO_PLACEHOLDER_RE "
          "to match src/guardrails/app/config.py's DEFAULT_PRESIDIO_ENTITIES.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
