"""ContractValidator must find contracts in per-feature subdirectories
(contracts/gold/eden/<name>.json). Before the path fix it only looked at
contracts/gold/<name>.json, so full validation silently degraded to the 4
platform-field check and shape regressions slipped through.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from gold.src.monitoring.contract_validator import ContractValidator, _load_contract  # noqa: E402

pytestmark = pytest.mark.monitoring


def _bid_context_doc():
    return {
        "tenantId": "T-001",
        "projectId": "P-1",
        "_grain": "eden_bid_context",
        "_schemaVersion": 1,
        "_silverLsn": 42,
        "goldUpdatedAt": "2026-06-15T00:00:00+00:00",
        # required by the contract's "_env" enum (deployment-tier stamp — see
        # gold/pipeline/lineage.py::pipeline_env()); a real loader gets this
        # from lineage_fields(), this fixture sets it directly.
        "_env": "dev",
    }


def test_subdir_contract_is_found():
    assert _load_contract("eden_bid_context") is not None, \
        "subdir contract must be discovered via recursive lookup"


def _all_contract_names() -> list[str]:
    # NOTE: _ROOT above (parents[3]) resolves to data-pipeline/gold, not the
    # repo root -- correct for the sys.path insert this module already did,
    # but contracts/ lives one level further up.
    repo_root = _ROOT.parent
    contracts_dir = repo_root / "contracts" / "gold"
    return sorted(p.stem for p in contracts_dir.rglob("*.json"))


@pytest.mark.parametrize("contract_name", _all_contract_names())
def test_every_contract_file_is_loadable(contract_name):
    # Regression guard for the BOM bug this module's docstring describes: 18
    # of the (then) 19 contract files carried a UTF-8 BOM that json.loads()
    # rejected, silently swallowed by a blanket except and degrading full
    # validation to the 4-platform-field check for nearly every collection.
    # test_subdir_contract_is_found only proved ONE file (eden_bid_context)
    # loads; this iterates every contract under contracts/gold/ so a future
    # BOM-swallowing regression on ANY single file is caught, not just that one.
    contract = _load_contract(contract_name)
    assert contract is not None, (
        f"contract '{contract_name}' failed to load — check for a BOM or "
        f"malformed JSON (see this module's docstring)"
    )


def test_full_doc_passes():
    cv = ContractValidator("eden_bid_context", mode="warn")
    assert cv.check(_bid_context_doc()) is None


def test_missing_business_required_field_is_caught():
    # projectId is in the contract's `required` but not in the 4 platform
    # fields — only the subdir-loaded contract catches its absence.
    cv = ContractValidator("eden_bid_context", mode="warn")
    doc = _bid_context_doc()
    del doc["projectId"]
    result = cv.check(doc)
    assert result is not None and "projectId" in result


def test_null_where_disallowed_is_caught():
    cv = ContractValidator("eden_bid_context", mode="warn")
    doc = _bid_context_doc()
    doc["tenantId"] = None  # contract: {"type": "string"} — null not allowed
    result = cv.check(doc)
    assert result is not None and "tenantId" in result


def test_nullable_field_allows_none():
    cv = ContractValidator("eden_bid_context", mode="warn")
    doc = _bid_context_doc()
    doc["status"] = None  # contract: {"type": ["string", "null"]}
    assert cv.check(doc) is None
