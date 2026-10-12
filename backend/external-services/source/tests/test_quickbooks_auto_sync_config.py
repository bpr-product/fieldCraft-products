from unittest.mock import MagicMock, patch

from source.Utils import constants as const
from source.api.ImportExportSyncAPI import _auto_sync_skip_response
from source.dal.QuickBooksRepository import QuickBooksRepository


def test_auto_sync_defaults_to_enabled_when_setting_is_missing():
    repo = QuickBooksRepository()
    repo.getAdditionalInfo = MagicMock(return_value={"status": "connected"})

    config = repo.getAutoSyncConfig("tn1", "6")

    assert config == {const.KEY_AUTO_SYNC_ENABLED: True}
    assert repo.isAutoSyncEnabled("tn1", "6") is True


def test_auto_sync_reads_saved_disabled_setting():
    repo = QuickBooksRepository()
    repo.getAdditionalInfo = MagicMock(
        return_value={
            const.KEY_AUTO_SYNC: {const.KEY_AUTO_SYNC_ENABLED: False},
        }
    )

    assert repo.isAutoSyncEnabled("tn1", "6") is False


def test_save_auto_sync_preserves_existing_additional_info():
    repo = QuickBooksRepository()
    repo.getAdditionalInfo = MagicMock(return_value={"realmId": "realm-1", "other": {"x": 1}})
    repo.updateAdditionalInfo = MagicMock()

    repo.saveAutoSyncConfig("tn1", "6", False)

    saved = repo.updateAdditionalInfo.call_args.args[2]
    assert saved["realmId"] == "realm-1"
    assert saved["other"] == {"x": 1}
    assert saved[const.KEY_AUTO_SYNC] == {const.KEY_AUTO_SYNC_ENABLED: False}
    assert saved.get("updatedAt")


def test_auto_sync_gate_allows_enabled_tenant():
    with patch("source.api.ImportExportSyncAPI.QuickBooksRepository") as repo_type:
        repo_type.return_value.isAutoSyncEnabled.return_value = True

        result = _auto_sync_skip_response(
            tenant_id="tn1",
            service_id="6",
            module="invoices",
        )

    assert result is None


def test_auto_sync_gate_returns_successful_skip_when_disabled():
    with patch("source.api.ImportExportSyncAPI.QuickBooksRepository") as repo_type:
        repo_type.return_value.isAutoSyncEnabled.return_value = False

        result = _auto_sync_skip_response(
            tenant_id="tn1",
            service_id="6",
            module="bills",
        )

    assert result == {
        const.KEY_OK: True,
        "skipped": True,
        const.KEY_STATUS: "auto_sync_disabled",
        const.KEY_MESSAGE: "QuickBooks auto sync is disabled for this tenant.",
        "tenantId": "tn1",
        "serviceId": "6",
    }
