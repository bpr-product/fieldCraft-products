from unittest.mock import MagicMock

from source.Utils import constants as const
from source.dal.TimelogRepository import TimelogRepository
from source.quickbooks.QuickBooksTimelogCreateService import QuickBooksTimelogCreateService


def _make_service():
    svc = QuickBooksTimelogCreateService()
    svc.qbo_repo = MagicMock()
    svc.timelog_repo = MagicMock()
    svc.timelog_exporter = MagicMock()
    return svc


def _connected_info(status="connected"):
    return {
        const.KEY_REFRESH_TOKEN: "RT",
        const.KEY_REALM_ID: "R1",
        const.KEY_ENV: "sandbox",
        const.KEY_STATUS: status,
    }


def _config_ok():
    return {const.KEY_CLIENT_ID: "CID", const.KEY_CLIENT_SECRET: "SECRET"}


def test_create_timelog_requires_tenant_id():
    svc = _make_service()

    result = svc.create_timelog(
        tenant_id="",
        service_id="6",
        timelog_external_map_id=10,
    )

    assert result[const.KEY_OK] is False
    assert "tenant_id is required" in result[const.KEY_MESSAGE]


def test_create_timelog_requires_external_map_id():
    svc = _make_service()

    result = svc.create_timelog(
        tenant_id="tn1",
        service_id="6",
        timelog_external_map_id=0,
    )

    assert result[const.KEY_OK] is False
    assert "timelog_external_map_id is required" in result[const.KEY_MESSAGE]


def test_create_timelog_returns_not_found_when_mapping_missing():
    svc = _make_service()
    svc.timelog_repo.get_mapping_by_id.return_value = None

    result = svc.create_timelog(
        tenant_id="tn1",
        service_id="6",
        timelog_external_map_id=10,
    )

    assert result[const.KEY_OK] is False
    assert "external map" in result[const.KEY_MESSAGE].lower()
    svc.timelog_repo.get_mapping_by_id.assert_called_once_with(
        tenant_id="tn1",
        service_id="6",
        mapping_id=10,
    )
    svc.timelog_repo.ensure_pending_mapping_for_timelog.assert_not_called()


def test_create_timelog_gets_timelog_id_from_mapping_and_returns_already_synced_without_qbo_call():
    svc = _make_service()
    svc.timelog_repo.get_mapping_by_id.return_value = {
        "id": 10,
        "timelog_id": 101,
        "sync_status": TimelogRepository.STATUS_SYNCED,
        "external_time_activity_id": "TA-1",
    }

    result = svc.create_timelog(
        tenant_id="tn1",
        service_id="6",
        timelog_external_map_id=10,
    )

    assert result[const.KEY_OK] is True
    assert result[const.KEY_STATUS] == "already_synced"
    assert result["timelogId"] == 101
    svc.timelog_exporter._process_candidate.assert_not_called()


def test_create_timelog_exports_single_candidate_from_external_map_id():
    svc = _make_service()
    svc.timelog_repo.get_mapping_by_id.return_value = {
        "id": 10,
        "timelog_id": 101,
        "sync_status": TimelogRepository.STATUS_PENDING_PUSH,
    }
    svc.timelog_repo.get_export_candidate_by_mapping_id.return_value = {
        "map_id": 10,
        "timelog_id": 101,
    }
    svc.qbo_repo.getAdditionalInfo.return_value = _connected_info()
    svc.timelog_exporter.accounting_context.return_value = {
        "access_token": "AT",
        "base_url": "https://sandbox-qbo",
    }
    svc.timelog_exporter._summary.return_value = {
        const.KEY_OK: True,
        "createdInQbo": 0,
        "updatedInQbo": 0,
        "failed": 0,
        "syncFailed": 0,
        const.KEY_RESULTS: [],
        const.KEY_FAILURES: [],
    }

    def mark_success(**kwargs):
        summary = kwargs["summary"]
        summary["createdInQbo"] = 1
        summary[const.KEY_RESULTS].append({
            const.KEY_STATUS: "success",
            const.KEY_ACTION: "created",
            "qboTimeActivityId": "TA-1",
        })

    svc.timelog_exporter._process_candidate.side_effect = mark_success

    result = svc.create_timelog(
        tenant_id="tn1",
        service_id="6",
        timelog_external_map_id=10,
    )

    assert result[const.KEY_OK] is True
    assert result["timelogId"] == 101
    assert result["timelogExternalMapId"] == 10
    assert result["createdInQbo"] == 1
    assert result[const.KEY_RESULTS][0]["qboTimeActivityId"] == "TA-1"
    svc.timelog_repo.ensure_pending_mapping_for_timelog.assert_not_called()
    svc.timelog_exporter._process_candidate.assert_called_once()
