from unittest.mock import MagicMock

from source.Utils import constants as const
from source.quickbooks.QuickBooksCapabilityService import (
    QuickBooksCapabilityService,
    _FEATURE_UNAVAILABLE_MESSAGES,
    _parse_preferences,
)


def test_for_purchase_false_does_not_disable_bill_sync():
    caps = _parse_preferences({
        "ProductAndServicesPrefs": {
            "ForPurchase": False,
        }
    })

    assert caps[const.CAPABILITY_BILLS] is True
    assert caps["detectionVersion"] == 3


def test_legacy_capability_cache_is_refreshed():
    service = QuickBooksCapabilityService()
    service.qbo_repo = MagicMock()
    service.qbo_repo.getCapabilities.return_value = {
        const.CAPABILITY_BILLS: False,
        "fetchedAt": "2026-07-30T00:00:00+00:00",
    }
    refreshed = {
        const.CAPABILITY_BILLS: True,
        "detectionVersion": 3,
        "fetchedAt": "2026-07-30T01:00:00+00:00",
    }
    service.fetch_and_save = MagicMock(return_value=refreshed)

    assert service.get_capabilities("tenant-1", "6") == refreshed
    service.fetch_and_save.assert_called_once_with("tenant-1", "6")


def test_bill_requirement_allows_valid_capability():
    service = QuickBooksCapabilityService()
    service.get_capabilities = MagicMock(return_value={
        const.CAPABILITY_BILLS: True,
        "detectionVersion": 3,
    })

    service.require("tenant-1", "6", const.CAPABILITY_BILLS)


def test_bill_capability_has_no_unreachable_plan_message():
    assert const.CAPABILITY_BILLS not in _FEATURE_UNAVAILABLE_MESSAGES
