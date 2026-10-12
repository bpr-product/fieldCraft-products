import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from source.quickbooks.QuickBooksBillImportService import QuickBooksBillImportService
from source.quickbooks.qbo_bill_utils import description_fingerprint
from source.Utils import constants as const


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_service():
    svc = QuickBooksBillImportService()
    svc.qbo_repo = MagicMock()
    svc.qbo_service = MagicMock()
    svc.receipt_repo = MagicMock()
    svc.receipt_map_repo = MagicMock()
    svc.vendor_map_repo = MagicMock()
    svc.coa_repo = MagicMock()
    svc.scheduled_job_repo = MagicMock()
    svc.project_repo = MagicMock()
    svc.project_repo.get_current_mapping_by_external_project_id.return_value = {
        "id": 30,
        "project_id": 300,
        "external_project_id": "QPROJ-1",
        "sync_status": "SYNCED",
    }
    svc.project_repo.get_mapping_sync_target.return_value = const.PROJECT_SYNC_TARGET_SUB_CUSTOMER
    svc.project_repo.get_active_project_by_id.return_value = {"id": 300, "description": "Project A"}
    # status constants
    svc.receipt_repo.STATUS_PENDING_PUSH = "PENDING_PUSH"
    svc.receipt_repo.STATUS_SYNCED = "SYNCED"
    svc.receipt_repo.STATUS_FAILED = "FAILED"
    svc.receipt_repo.STATUS_IMPORT_FAILED = "IMPORT_FAILED"
    svc.receipt_repo.STATUS_SYNC_FAILED = "SYNC_FAILED"
    svc.receipt_repo.STATUS_SYNCED_INCOMPLETE = "SYNCED_INCOMPLETE"
    # default: no pending retries
    svc.receipt_map_repo.get_open_import_failed_external_receipt_ids.return_value = []
    # default: no last sync
    svc.scheduled_job_repo.get_last_successful_module_sync_start.return_value = None
    return svc


def _parse_additional_info(raw):
    if raw is None:
        return {}
    data = raw if isinstance(raw, (dict, list)) else json.loads(str(raw))
    if isinstance(data, list):
        return data[0] if data else {}
    return data if isinstance(data, dict) else {}


def _fieldcraft_receipt(items, total=545.13):
    return {
        "id": 211,
        "created_by": "8aef1564-cd04-4b85-9633-49ad6f4f3231",
        "receipt_additional_info": json.dumps([{
            "total": str(total),
            "billNo": "BM-8852",
            "receiptInfo": json.dumps(items),
        }]),
    }


def _managed_mapping(description, *, amount=545.13, item_count=2, line_id="1", line_baseline=None):
    managed = {
        "amount": amount,
        "descriptionFingerprint": description_fingerprint(description),
        "itemCount": item_count,
    }
    if line_baseline is not None:
        managed["lineBaseline"] = line_baseline
    if line_id is not None:
        managed["id"] = line_id
    return {
        "id": 90,
        "receipt_id": 211,
        "sync_status": "SYNCED",
        "reason": json.dumps({
            "message": "Bill created in QuickBooks successfully.",
            "fieldcraftManagedBillLine": managed,
        }),
    }


def _qbo_payload(lines, total):
    return {
        "receipt_additional_info": json.dumps([{
            "total": str(total),
            "receiptInfo": json.dumps(lines),
        }]),
    }


def connected_info(status="connected"):
    return {
        const.KEY_REFRESH_TOKEN: "RT",
        const.KEY_REALM_ID: "123456",
        const.KEY_ENV: "sandbox",
        const.KEY_STATUS: status,
    }


def config_ok():
    return {const.KEY_CLIENT_ID: "CID", const.KEY_CLIENT_SECRET: "SEC"}


def sample_qbo_bill(bill_id="B1", doc_number="INV-001", vendor_id="QV1", account_id="QACC1", total=100.0, sync_token="0"):
    return {
        const.QBO_KEY_ID: bill_id,
        const.QBO_KEY_SYNC_TOKEN: sync_token,
        "DocNumber": doc_number,
        "TxnDate": "2024-01-15",
        "TotalAmt": total,
        "VendorRef": {"value": vendor_id},
        "Line": [
            {
                "Amount": total,
                "DetailType": "AccountBasedExpenseLineDetail",
                "AccountBasedExpenseLineDetail": {
                    "AccountRef": {"value": account_id},
                    "CustomerRef": {"value": "QPROJ-1", "name": "Customer:Project A"},
                },
            }
        ],
        "MetaData": {"LastUpdatedTime": "2024-01-15T10:00:00+00:00"},
    }



def sample_qbo_stock_bill(bill_id="BSTOCK", doc_number="STOCK-001", vendor_id="QV1", account_id="QACC1", total=100.0, sync_token="0"):
    bill = sample_qbo_bill(
        bill_id=bill_id,
        doc_number=doc_number,
        vendor_id=vendor_id,
        account_id=account_id,
        total=total,
        sync_token=sync_token,
    )
    # Stock bills are exported from FIELDCRAFT as account-only bill lines. QBO returns
    # them without CustomerRef/ProjectRef, and that must not downgrade the sync
    # status to SYNCED_INCOMPLETE.
    bill["Line"][0].pop("ProjectRef", None)
    bill["Line"][0]["AccountBasedExpenseLineDetail"].pop("CustomerRef", None)
    return bill

def _setup_full_import(svc, qbo_bills):
    """Configure svc so import_bills runs in FULL mode with the given bills."""
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()
    svc._refresh_access_token = MagicMock(return_value="TOKEN")
    svc._get_qbo_api_base = MagicMock(return_value="https://sandbox-qbo")
    # no CDC watermark → FULL mode
    svc._resolve_changed_since = MagicMock(return_value=(None, None))
    # FULL query returns bills then empty page
    svc._query_qbo_bills = MagicMock(side_effect=[
        (qbo_bills, "TID-1"),
        ([], None),
    ])


# ---------------------------------------------------------------------------
# import_bills – connection / config guard tests
# ---------------------------------------------------------------------------

def test_import_bills_missing_connection():
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {}
    result = svc.import_bills(tenant_id="t1", service_id="6")
    assert result[const.KEY_OK] is False
    assert "not connected" in result[const.KEY_MESSAGE].lower()


def test_import_bills_disconnected_status():
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info(status="disconnected")
    result = svc.import_bills(tenant_id="t1", service_id="6")
    assert result[const.KEY_OK] is False
    assert "not connected" in result[const.KEY_MESSAGE].lower()


def test_import_bills_missing_client_credentials():
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = {}
    result = svc.import_bills(tenant_id="t1", service_id="6")
    assert result[const.KEY_OK] is False
    assert "client credentials" in result[const.KEY_MESSAGE].lower()


# ---------------------------------------------------------------------------
# import_bills – FULL mode: skip deleted bill
# ---------------------------------------------------------------------------

def test_import_bills_full_skips_deleted_bill():
    svc = make_service()
    deleted_bill = {**sample_qbo_bill(), "status": "deleted"}
    _setup_full_import(svc, [deleted_bill])

    svc.receipt_map_repo.get_by_external_id.return_value = None
    svc.receipt_repo.build_receipt_payload_from_qbo = MagicMock(return_value={})

    result = svc.import_bills(tenant_id="t1", service_id="6", force_full=True)

    assert result["skippedDeleted"] == 1
    assert result["processed"] == 1
    svc.receipt_repo.create_receipt_for_tenant.assert_not_called()


# ---------------------------------------------------------------------------
# import_bills – FULL mode: create new receipt
# ---------------------------------------------------------------------------

def test_import_bills_full_creates_new_receipt():
    svc = make_service()
    bill = sample_qbo_bill(bill_id="B99")
    _setup_full_import(svc, [bill])

    svc.receipt_map_repo.get_by_external_id.return_value = None
    svc.vendor_map_repo.get_by_external_id.return_value = {"vendor_id": 10}
    svc.coa_repo.get_by_external_id.return_value = {"id": 20}
    payload = {"invoice_number": "INV-001", "invoice_date": "2024-01-15", "vendor_id": 10, "chart_of_account_id": 20, "receipt_additional_info": "{}"}
    svc.receipt_repo.build_receipt_payload_from_qbo = MagicMock(return_value=payload)
    svc.receipt_repo.get_receipt_id_by_doc_number.return_value = None
    svc.receipt_repo.parse_additional_info = MagicMock(return_value={"total_amount": 100.0})
    svc.receipt_repo._pick_total_amount = MagicMock(return_value=100.0)
    svc.receipt_repo.create_receipt_for_tenant = MagicMock(return_value=55)

    result = svc.import_bills(tenant_id="t1", service_id="6", force_full=True)

    assert result["created in FIELDCRAFT"] == 1
    svc.receipt_repo.create_receipt_for_tenant.assert_called_once()
    svc.receipt_map_repo.rotate_mapping_for_receipt.assert_called_once()


# ---------------------------------------------------------------------------
# import_bills – FULL mode: skip already current
# ---------------------------------------------------------------------------

def test_import_bills_full_skips_already_current():
    svc = make_service()
    bill = sample_qbo_bill(bill_id="B10")
    _setup_full_import(svc, [bill])

    existing_map = {"id": 1, "receipt_id": 5, "sync_status": "SYNCED"}
    svc.receipt_map_repo.get_by_external_id.return_value = existing_map
    existing_receipt = {"id": 5}
    svc.receipt_repo.get_current_receipt_by_id.return_value = existing_receipt
    svc.receipt_repo.get_receipt_latest_local_timestamp.return_value = None
    payload = {"invoice_number": "INV-001", "invoice_date": "2024-01-15"}
    svc.receipt_repo.build_receipt_payload_from_qbo = MagicMock(return_value=payload)
    # no change detected
    svc.receipt_repo.sync_projection_changed.return_value = False

    result = svc.import_bills(tenant_id="t1", service_id="6", force_full=True)

    assert result["skippedAlreadyCurrent"] == 1
    svc.receipt_repo.update_receipt_from_qbo.assert_not_called()


# ---------------------------------------------------------------------------
# import_bills – FULL mode: update existing receipt
# ---------------------------------------------------------------------------

def test_import_bills_full_updates_existing_receipt():
    svc = make_service()
    bill = sample_qbo_bill(bill_id="B11")
    _setup_full_import(svc, [bill])

    existing_map = {"id": 1, "receipt_id": 7, "sync_status": "SYNCED"}
    svc.receipt_map_repo.get_by_external_id.return_value = existing_map
    existing_receipt = {"id": 7, "updated_at": "2024-01-01T00:00:00+00:00"}
    svc.receipt_repo.get_current_receipt_by_id.return_value = existing_receipt
    local_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    qbo_ts = datetime(2024, 1, 16, tzinfo=timezone.utc)
    svc.receipt_repo.get_receipt_latest_local_timestamp.return_value = local_ts
    payload = {"invoice_number": "INV-001", "invoice_date": "2024-01-15"}
    svc.receipt_repo.build_receipt_payload_from_qbo = MagicMock(return_value=payload)
    svc.receipt_repo.sync_projection_changed.return_value = True
    svc.receipt_repo.update_receipt_from_qbo.return_value = {"id": 7}

    # Patch _get_qbo_last_updated_at to return newer QBO timestamp
    svc._get_qbo_last_updated_at = MagicMock(return_value=qbo_ts)

    result = svc.import_bills(tenant_id="t1", service_id="6", force_full=True)

    assert result["updatedInFIELDCRAFT"] == 1
    svc.receipt_repo.update_receipt_from_qbo.assert_called_once()


# ---------------------------------------------------------------------------
# import_bills – FULL mode: link existing receipt by doc number
# ---------------------------------------------------------------------------

def test_import_bills_full_links_existing_by_doc_number():
    svc = make_service()
    bill = sample_qbo_bill(bill_id="B12", doc_number="INV-888")
    _setup_full_import(svc, [bill])

    svc.receipt_map_repo.get_by_external_id.return_value = None
    svc.vendor_map_repo.get_by_external_id.return_value = {"vendor_id": 10}
    svc.coa_repo.get_by_external_id.return_value = {"id": 20}
    payload = {"invoice_number": "INV-888", "invoice_date": "2024-01-15", "receipt_additional_info": "{}"}
    svc.receipt_repo.build_receipt_payload_from_qbo = MagicMock(return_value=payload)
    svc.receipt_repo.get_receipt_id_by_doc_number.return_value = 42
    svc.receipt_repo.get_current_receipt_by_id.return_value = {"id": 42}
    svc.receipt_repo.get_receipt_latest_local_timestamp.return_value = None
    svc.receipt_repo.sync_projection_changed.return_value = False

    result = svc.import_bills(tenant_id="t1", service_id="6", force_full=True)

    assert result["linkedExistingInFieldCraft"] == 1
    svc.receipt_map_repo.rotate_mapping_for_receipt.assert_called_once()


# ---------------------------------------------------------------------------
# import_bills – FULL mode: missing required fields → IMPORT_FAILED
# ---------------------------------------------------------------------------

def test_import_bills_full_import_failed_missing_fields():
    svc = make_service()
    bill = sample_qbo_bill(bill_id="B13")
    _setup_full_import(svc, [bill])

    svc.receipt_map_repo.get_by_external_id.return_value = None
    # No vendor mapping, no coa mapping
    svc.vendor_map_repo.get_by_external_id.return_value = None
    svc.coa_repo.get_by_external_id.return_value = None
    payload = {"invoice_number": "INV-001", "invoice_date": None, "receipt_additional_info": "{}"}
    svc.receipt_repo.build_receipt_payload_from_qbo = MagicMock(return_value=payload)
    svc.receipt_repo.get_receipt_id_by_doc_number.return_value = None
    svc.receipt_repo.parse_additional_info = MagicMock(return_value={})
    svc.receipt_repo._pick_txn_date = MagicMock(return_value=None)
    svc.receipt_repo._pick_total_amount = MagicMock(return_value=None)

    result = svc.import_bills(tenant_id="t1", service_id="6", force_full=True)

    assert result["failed"] == 1
    svc.receipt_map_repo.upsert_import_failed_mapping.assert_called_once()


# ---------------------------------------------------------------------------
# import_bills – FULL mode: dry run skips all writes
# ---------------------------------------------------------------------------

def test_import_bills_full_dry_run_skips_create():
    svc = make_service()
    bill = sample_qbo_bill(bill_id="B14")
    _setup_full_import(svc, [bill])

    svc.receipt_map_repo.get_by_external_id.return_value = None
    svc.vendor_map_repo.get_by_external_id.return_value = {"vendor_id": 10}
    svc.coa_repo.get_by_external_id.return_value = {"id": 20}
    payload = {"invoice_number": "INV-001", "invoice_date": "2024-01-15", "receipt_additional_info": "{}"}
    svc.receipt_repo.build_receipt_payload_from_qbo = MagicMock(return_value=payload)
    svc.receipt_repo.get_receipt_id_by_doc_number.return_value = None
    svc.receipt_repo.parse_additional_info = MagicMock(return_value={"total_amount": 100.0})
    svc.receipt_repo._pick_total_amount = MagicMock(return_value=100.0)

    result = svc.import_bills(tenant_id="t1", service_id="6", force_full=True, dry_run=True)

    # dry run: nothing created
    svc.receipt_repo.create_receipt_for_tenant.assert_not_called()
    result_actions = [r[const.KEY_ACTION] for r in result[const.KEY_RESULTS]]
    assert "dryRun" in result_actions


# ---------------------------------------------------------------------------
# import_bills – CDC mode
# ---------------------------------------------------------------------------

def test_import_bills_uses_cdc_when_watermark_available():
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()
    svc._refresh_access_token = MagicMock(return_value="TOKEN")
    svc._get_qbo_api_base = MagicMock(return_value="https://sandbox-qbo")

    changed_since = datetime.now(timezone.utc) - timedelta(hours=1)
    svc._resolve_changed_since = MagicMock(return_value=(changed_since, "module"))
    svc._is_cdc_too_old = MagicMock(return_value=False)

    bill = sample_qbo_bill(bill_id="BCDC")
    svc._fetch_qbo_bills_cdc = MagicMock(return_value=([bill], "TID-CDC"))

    svc.receipt_map_repo.get_by_external_id.return_value = None
    svc.vendor_map_repo.get_by_external_id.return_value = {"vendor_id": 10}
    svc.coa_repo.get_by_external_id.return_value = {"id": 20}
    payload = {"invoice_number": "INV-001", "invoice_date": "2024-01-15", "receipt_additional_info": "{}"}
    svc.receipt_repo.build_receipt_payload_from_qbo = MagicMock(return_value=payload)
    svc.receipt_repo.get_receipt_id_by_doc_number.return_value = None
    svc.receipt_repo.parse_additional_info = MagicMock(return_value={"total_amount": 100.0})
    svc.receipt_repo._pick_total_amount = MagicMock(return_value=100.0)
    svc.receipt_repo.create_receipt_for_tenant = MagicMock(return_value=77)

    result = svc.import_bills(tenant_id="t1", service_id="6")

    assert result["mode"] == "CDC"
    assert result["processed"] == 1
    svc._fetch_qbo_bills_cdc.assert_called_once()


# ---------------------------------------------------------------------------
# Helper / utility unit tests
# ---------------------------------------------------------------------------

def test_is_deleted_qbo_bill_true():
    svc = make_service()
    assert svc._is_deleted_qbo_bill({"status": "deleted"}) is True
    assert svc._is_deleted_qbo_bill({"Status": "Deleted"}) is True


def test_is_deleted_qbo_bill_false():
    svc = make_service()
    assert svc._is_deleted_qbo_bill({"status": "active"}) is False
    assert svc._is_deleted_qbo_bill({}) is False


def test_should_skip_import_update_local_newer():
    svc = make_service()
    qbo_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    local_ts = datetime(2024, 1, 2, tzinfo=timezone.utc)
    assert svc._should_skip_import_update(qbo_ts, local_ts) is True


def test_should_skip_import_update_qbo_newer():
    svc = make_service()
    qbo_ts = datetime(2024, 1, 10, tzinfo=timezone.utc)
    local_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert svc._should_skip_import_update(qbo_ts, local_ts) is False


def test_should_skip_import_update_missing_timestamps():
    svc = make_service()
    assert svc._should_skip_import_update(None, None) is False
    assert svc._should_skip_import_update(datetime.now(timezone.utc), None) is False


def test_extract_qbo_account_id_from_line():
    svc = make_service()
    bill = sample_qbo_bill(account_id="ACC-42")
    assert svc._extract_qbo_account_id(bill) == "ACC-42"


def test_extract_qbo_account_id_fallback_ap_ref():
    svc = make_service()
    bill = {"APAccountRef": {"value": "AP-99"}, "Line": []}
    assert svc._extract_qbo_account_id(bill) == "AP-99"


def test_extract_qbo_account_id_empty():
    svc = make_service()
    assert svc._extract_qbo_account_id({}) == ""


def test_is_cdc_too_old_true():
    svc = make_service()
    old_ts = datetime.now(timezone.utc) - timedelta(days=30)
    assert svc._is_cdc_too_old(old_ts) is True


def test_is_cdc_too_old_false():
    svc = make_service()
    recent_ts = datetime.now(timezone.utc) - timedelta(days=5)
    assert svc._is_cdc_too_old(recent_ts) is False


def test_get_missing_required_fields_all_present():
    svc = make_service()
    svc.receipt_repo.parse_additional_info = MagicMock(return_value={"billDate": "2024-01-01"})
    svc.receipt_repo._pick_txn_date = MagicMock(return_value="2024-01-01")
    svc.receipt_repo._pick_total_amount = MagicMock(return_value=150.0)
    payload = {"invoice_date": None, "order_date": None, "receipt_additional_info": "{}"}
    missing = svc._get_missing_required_fields(payload=payload, local_vendor_id=5, local_account_id=10)
    assert missing == []


def test_get_missing_required_fields_all_missing():
    svc = make_service()
    svc.receipt_repo.parse_additional_info = MagicMock(return_value={})
    svc.receipt_repo._pick_txn_date = MagicMock(return_value=None)
    svc.receipt_repo._pick_total_amount = MagicMock(return_value=None)
    payload = {"invoice_date": None, "order_date": None, "receipt_additional_info": "{}"}
    missing = svc._get_missing_required_fields(payload=payload, local_vendor_id=None, local_account_id=None)
    assert "vendor" in missing
    assert "account" in missing
    assert "date" in missing
    assert "amount" in missing


def test_build_reason_without_tid():
    import json
    result = QuickBooksBillImportService._build_reason("test message")
    data = json.loads(result)
    assert data["message"] == "test message"
    assert "intuit_tid" not in data


def test_build_reason_with_tid():
    import json
    result = QuickBooksBillImportService._build_reason("err", intuit_tid="TID-123")
    data = json.loads(result)
    assert data["intuit_tid"] == "TID-123"


def test_import_bills_resolves_project_by_external_project_map():
    svc = make_service()
    bill = sample_qbo_bill(bill_id="BPROJECT")
    resolution = svc._resolve_local_project_for_bill(
        tenant_id="t1",
        service_id="6",
        realm_id="123456",
        qbo_bill=bill,
        bill_label="INV-001",
    )

    assert resolution["project_id"] == 300
    assert resolution["qbo_project_id"] == "QPROJ-1"
    svc.project_repo.get_current_mapping_by_external_project_id.assert_called_once_with(
        tenant_id="t1",
        service_id="6",
        external_project_id="QPROJ-1",
    )


def test_import_bills_fails_when_qbo_project_is_not_mapped():
    svc = make_service()
    svc.project_repo.get_current_mapping_by_external_project_id.return_value = None
    bill = sample_qbo_bill(bill_id="BPROJECT")

    resolution = svc._resolve_local_project_for_bill(
        tenant_id="t1",
        service_id="6",
        realm_id="123456",
        qbo_bill=bill,
        bill_label="INV-001",
    )

    assert resolution["project_id"] is None
    assert resolution["qbo_project_id"] == "QPROJ-1"
    assert "not mapped" in resolution["error"]
    assert "Run project sync" in resolution["error"]




def test_import_bills_prefers_project_ref_over_customer_ref():
    svc = make_service()
    svc.project_repo.get_current_mapping_by_external_project_id.return_value = {
        "id": 31,
        "project_id": 301,
        "external_project_id": "QBO-PROJECT-1",
        "sync_status": "SYNCED",
    }
    svc.project_repo.get_mapping_sync_target.return_value = const.PROJECT_SYNC_TARGET_QBO_PROJECT
    svc.project_repo.get_active_project_by_id.return_value = {"id": 301, "description": "Roof Project"}
    bill = sample_qbo_bill(bill_id="BPROJECT")
    bill["Line"][0]["ProjectRef"] = {"value": "QBO-PROJECT-1", "name": "Roof Project"}
    bill["Line"][0]["AccountBasedExpenseLineDetail"]["CustomerRef"] = {"value": "PARENT-CUST-1", "name": "Parent Customer"}

    resolution = svc._resolve_local_project_for_bill(
        tenant_id="t1",
        service_id="6",
        realm_id="123456",
        qbo_bill=bill,
        bill_label="INV-001",
    )

    assert resolution["project_id"] == 301
    assert resolution["qbo_project_id"] == "QBO-PROJECT-1"
    assert resolution["project_sync_target"] == const.PROJECT_SYNC_TARGET_QBO_PROJECT
    svc.project_repo.get_current_mapping_by_external_project_id.assert_called_once_with(
        tenant_id="t1",
        service_id="6",
        external_project_id="QBO-PROJECT-1",
    )


def test_import_bills_rejects_project_mapping_from_other_realm():
    svc = make_service()
    svc.project_repo.get_current_mapping_by_external_project_id.return_value = {
        "id": 31,
        "project_id": 301,
        "external_project_id": "QPROJ-1",
        "external_account_id": "other-realm",
        "sync_status": "SYNCED",
    }
    bill = sample_qbo_bill(bill_id="BPROJECT")

    with pytest.raises(ValueError, match="different QuickBooks company"):
        svc._resolve_local_project_for_bill(
            tenant_id="t1",
            service_id="6",
            realm_id="123456",
            qbo_bill=bill,
            bill_label="INV-001",
        )


def test_import_bills_rejects_mapping_target_mismatch():
    svc = make_service()
    svc.project_repo.get_current_mapping_by_external_project_id.return_value = {
        "id": 31,
        "project_id": 301,
        "external_project_id": "QBO-PROJECT-1",
        "sync_status": "SYNCED",
    }
    svc.project_repo.get_mapping_sync_target.return_value = const.PROJECT_SYNC_TARGET_SUB_CUSTOMER
    bill = sample_qbo_bill(bill_id="BPROJECT")
    bill["Line"][0]["ProjectRef"] = {"value": "QBO-PROJECT-1", "name": "Roof Project"}

    resolution = svc._resolve_local_project_for_bill(
        tenant_id="t1",
        service_id="6",
        realm_id="123456",
        qbo_bill=bill,
        bill_label="INV-001",
    )

    assert resolution["project_id"] is None
    assert resolution["project_sync_target"] == const.PROJECT_SYNC_TARGET_QBO_PROJECT
    assert "not mapped" in resolution["error"]


def test_persist_failed_mapping_uses_sync_failed_for_existing_mapped_bill():
    svc = make_service()
    current_mapping = {"id": 10, "receipt_id": 123, "external_receipt_id": "B1", "sync_status": "SYNCED"}

    svc._persist_import_failed_mapping(
        tenant_id="t1",
        service_id="6",
        realm_id="RID",
        qbo_bill_id="B1",
        sync_token="2",
        payload={"invoice_number": "INV-001", "vendor_id": 10},
        current_mapping=current_mapping,
        reason="boom",
    )

    svc.receipt_map_repo.upsert_import_failed_mapping.assert_called_once()
    call = svc.receipt_map_repo.upsert_import_failed_mapping.call_args.kwargs
    assert call["receipt_id"] == 123
    assert call["sync_status"] == "SYNC_FAILED"


def test_persist_failed_mapping_uses_import_failed_for_qbo_only_bill():
    svc = make_service()
    svc.receipt_repo.get_receipt_id_by_doc_number.return_value = None

    svc._persist_import_failed_mapping(
        tenant_id="t1",
        service_id="6",
        realm_id="RID",
        qbo_bill_id="B2",
        sync_token="1",
        payload={"invoice_number": "QBO-ONLY", "vendor_id": 10},
        current_mapping=None,
        reason="missing vendor",
    )

    svc.receipt_map_repo.upsert_import_failed_mapping.assert_called_once()
    call = svc.receipt_map_repo.upsert_import_failed_mapping.call_args.kwargs
    assert call["receipt_id"] is None
    assert call["sync_status"] == "IMPORT_FAILED"


def test_import_bills_keeps_fieldcraft_stock_bill_synced_when_qbo_has_no_project_link():
    svc = make_service()
    bill = sample_qbo_stock_bill(bill_id="BSTOCK-1", doc_number="STOCK-001")
    _setup_full_import(svc, [bill])

    existing_map = {"id": 1, "receipt_id": 5, "sync_status": "SYNCED"}
    stock_receipt = {"id": 5, "project_id": None, "stock_reference": "PO-STOCK-1"}
    svc.receipt_map_repo.get_by_external_id.return_value = existing_map
    svc.vendor_map_repo.get_by_external_id.return_value = {"vendor_id": 10}
    svc.coa_repo.get_by_external_id.return_value = {"id": 20}
    svc.receipt_repo.get_current_receipt_by_id.return_value = stock_receipt
    svc.receipt_repo.get_receipt_latest_local_timestamp.return_value = None
    payload = {
        "invoice_number": "STOCK-001",
        "invoice_date": "2024-01-15",
        "project_id": None,
        "vendor_id": 10,
        "chart_of_account_id": 20,
    }
    svc.receipt_repo.build_receipt_payload_from_qbo = MagicMock(return_value=payload)
    svc.receipt_repo.sync_projection_changed.return_value = False

    result = svc.import_bills(tenant_id="t1", service_id="6", force_full=True)

    assert result["skippedAlreadyCurrent"] == 1
    svc.receipt_map_repo.mark_mapping_synced.assert_called_once()
    call = svc.receipt_map_repo.mark_mapping_synced.call_args.kwargs
    assert call.get("sync_status") is None
    assert "project" not in call["reason"].lower()


def test_import_bills_marks_project_bill_incomplete_when_qbo_has_no_project_link():
    svc = make_service()
    bill = sample_qbo_stock_bill(bill_id="BPROJECT-NOLINK", doc_number="PROJ-001")
    _setup_full_import(svc, [bill])

    existing_map = {"id": 1, "receipt_id": 7, "sync_status": "SYNCED"}
    project_receipt = {"id": 7, "project_id": 300}
    svc.receipt_map_repo.get_by_external_id.return_value = existing_map
    svc.vendor_map_repo.get_by_external_id.return_value = {"vendor_id": 10}
    svc.coa_repo.get_by_external_id.return_value = {"id": 20}
    svc.receipt_repo.get_current_receipt_by_id.return_value = project_receipt
    svc.receipt_repo.get_receipt_latest_local_timestamp.return_value = None
    payload = {
        "invoice_number": "PROJ-001",
        "invoice_date": "2024-01-15",
        "project_id": None,
        "vendor_id": 10,
        "chart_of_account_id": 20,
    }
    svc.receipt_repo.build_receipt_payload_from_qbo = MagicMock(return_value=payload)
    svc.receipt_repo.sync_projection_changed.return_value = False

    result = svc.import_bills(tenant_id="t1", service_id="6", force_full=True)

    assert result["skippedAlreadyCurrent"] == 1
    svc.receipt_map_repo.mark_mapping_synced.assert_called_once()
    call = svc.receipt_map_repo.mark_mapping_synced.call_args.kwargs
    assert call["sync_status"] == "SYNCED_INCOMPLETE"
    assert "not linked to a project" in call["reason"]


def test_import_bills_blocks_qbo_project_link_for_fieldcraft_stock_bill():
    svc = make_service()
    bill = sample_qbo_bill(bill_id="BSTOCK-LINKED", doc_number="STOCK-LINKED-001")
    _setup_full_import(svc, [bill])

    existing_map = {"id": 1, "receipt_id": 5, "sync_status": "SYNCED"}
    stock_receipt = {"id": 5, "project_id": None, "stock_reference": "PO-STOCK-2"}
    svc.receipt_map_repo.get_by_external_id.return_value = existing_map
    svc.vendor_map_repo.get_by_external_id.return_value = {"vendor_id": 10}
    svc.coa_repo.get_by_external_id.return_value = {"id": 20}
    svc.receipt_repo.get_current_receipt_by_id.return_value = stock_receipt
    svc.receipt_repo.get_receipt_latest_local_timestamp.return_value = None
    payload = {
        "invoice_number": "STOCK-LINKED-001",
        "invoice_date": "2024-01-15",
        "project_id": 300,
        "vendor_id": 10,
        "chart_of_account_id": 20,
    }
    svc.receipt_repo.build_receipt_payload_from_qbo = MagicMock(return_value=payload)
    svc.receipt_repo.sync_projection_changed.return_value = False

    result = svc.import_bills(tenant_id="t1", service_id="6", force_full=True)

    assert result["skippedAlreadyCurrent"] == 1
    svc.receipt_repo.update_receipt_from_qbo.assert_not_called()
    assert payload["project_id"] is None

    svc.receipt_map_repo.mark_mapping_synced.assert_called_once()
    call = svc.receipt_map_repo.mark_mapping_synced.call_args.kwargs
    assert call["sync_status"] == "SYNCED_INCOMPLETE"
    reason = call["reason"].lower()
    assert "stock bill" in reason
    assert "cannot be linked to a project" in reason
    assert "quickbooks" in reason


def test_import_bills_blocks_qbo_project_link_for_doc_number_matched_stock_bill():
    svc = make_service()
    bill = sample_qbo_bill(bill_id="BSTOCK-DOC-LINKED", doc_number="STOCK-DOC-001")
    _setup_full_import(svc, [bill])

    stock_receipt = {"id": 5, "project_id": None, "stock_reference": "PO-STOCK-DOC"}
    svc.receipt_map_repo.get_by_external_id.return_value = None
    svc.vendor_map_repo.get_by_external_id.return_value = {"vendor_id": 10}
    svc.coa_repo.get_by_external_id.return_value = {"id": 20}
    svc.receipt_repo.get_receipt_id_by_doc_number.return_value = 5
    svc.receipt_repo.get_current_receipt_by_id.return_value = stock_receipt
    svc.receipt_repo.get_receipt_latest_local_timestamp.return_value = None
    payload = {
        "invoice_number": "STOCK-DOC-001",
        "invoice_date": "2024-01-15",
        "project_id": 300,
        "vendor_id": 10,
        "chart_of_account_id": 20,
    }
    svc.receipt_repo.build_receipt_payload_from_qbo = MagicMock(return_value=payload)
    svc.receipt_repo.sync_projection_changed.return_value = True
    svc.receipt_repo.update_receipt_from_qbo.return_value = {"id": 5}

    result = svc.import_bills(tenant_id="t1", service_id="6", force_full=True)

    assert result["linkedExistingInFieldCraft"] == 1
    svc.receipt_repo.update_receipt_from_qbo.assert_called_once()
    update_payload = svc.receipt_repo.update_receipt_from_qbo.call_args.kwargs["payload"]
    assert update_payload["project_id"] is None

    svc.receipt_map_repo.rotate_mapping_for_receipt.assert_called_once()
    map_call = svc.receipt_map_repo.rotate_mapping_for_receipt.call_args.kwargs
    assert map_call["sync_status"] == "SYNCED_INCOMPLETE"
    reason = map_call["reason"].lower()
    assert "stock bill" in reason
    assert "cannot be linked to a project" in reason


# ---------------------------------------------------------------------------
# FIELDCRAFT summarized-line two-way merge
# ---------------------------------------------------------------------------


def test_managed_summary_validation_allows_unchanged_summary_with_new_qbo_line():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    items = [
        {"description": "Material A", "quantity": 2, "price": 100, "totalPrice": 200},
        {"description": "Material B", "quantity": 3, "price": 100, "totalPrice": 300},
    ]
    summary_description = "Material A x 2; Material B x 3"
    current = _fieldcraft_receipt(items, total=500)
    mapping = _managed_mapping(summary_description, amount=500, item_count=2)
    qbo_bill = {
        "Line": [
            {"Id": "1", "Amount": 500, "Description": summary_description, "DetailType": "AccountBasedExpenseLineDetail"},
            {"Id": "2", "Amount": 50, "Description": "Delivery", "DetailType": "AccountBasedExpenseLineDetail"},
        ],
        "TotalAmt": 550,
    }

    metadata, changes = svc._validate_managed_summary_line(
        current_receipt=current,
        current_mapping=mapping,
        qbo_bill=qbo_bill,
    )

    assert metadata is not None
    assert changes == []


@pytest.mark.parametrize(
    ("line", "expected_change"),
    [
        ({"Id": "1", "Amount": 500, "Description": "Changed summary", "DetailType": "AccountBasedExpenseLineDetail"}, "description"),
        ({"Id": "1", "Amount": 550, "Description": "Material A x 2; Material B x 3", "DetailType": "AccountBasedExpenseLineDetail"}, "amount"),
    ],
)
def test_managed_summary_validation_blocks_description_or_amount_edits(line, expected_change):
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    items = [
        {"description": "Material A", "quantity": 2, "price": 100, "totalPrice": 200},
        {"description": "Material B", "quantity": 3, "price": 100, "totalPrice": 300},
    ]
    summary_description = "Material A x 2; Material B x 3"

    _, changes = svc._validate_managed_summary_line(
        current_receipt=_fieldcraft_receipt(items, total=500),
        current_mapping=_managed_mapping(summary_description, amount=500, item_count=2),
        qbo_bill={"Line": [line], "TotalAmt": line["Amount"]},
    )

    assert expected_change in changes


def test_managed_summary_validation_blocks_deleted_summary_line():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    items = [
        {"description": "Material A", "quantity": 2, "price": 100, "totalPrice": 200},
        {"description": "Material B", "quantity": 3, "price": 100, "totalPrice": 300},
    ]
    summary_description = "Material A x 2; Material B x 3"

    _, changes = svc._validate_managed_summary_line(
        current_receipt=_fieldcraft_receipt(items, total=500),
        current_mapping=_managed_mapping(summary_description, amount=500, item_count=2, line_id="1"),
        qbo_bill={
            "Line": [{"Id": "9", "Amount": 400, "Description": "Replacement", "DetailType": "AccountBasedExpenseLineDetail"}],
            "TotalAmt": 400,
        },
    )

    assert changes == ["line removed"]


def test_managed_summary_validation_blocks_exported_account_change():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    items = [{"description": "Material A", "quantity": 2, "price": 100, "totalPrice": 200}]
    summary_description = "Material A x 2"
    mapping = _managed_mapping(
        summary_description,
        amount=200,
        item_count=1,
        line_baseline={
            "detailType": "AccountBasedExpenseLineDetail",
            "accountRef": "A-1",
        },
    )
    qbo_line = {
        "Id": "1",
        "Amount": 200,
        "Description": summary_description,
        "DetailType": "AccountBasedExpenseLineDetail",
        "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "A-2"}},
    }

    _, changes = svc._validate_managed_summary_line(
        current_receipt=_fieldcraft_receipt(items, total=200),
        current_mapping=mapping,
        qbo_bill={"Line": [qbo_line], "TotalAmt": 200},
    )

    assert "account" in changes


def test_import_blocks_entire_fieldcraft_bill_update_when_managed_summary_is_modified():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    summary_description = "Material A x 2; Material B x 3"
    items = [
        {"description": "Material A", "quantity": 2, "price": 100, "totalPrice": 200},
        {"description": "Material B", "quantity": 3, "price": 100, "totalPrice": 300},
    ]
    current = _fieldcraft_receipt(items, total=500)
    mapping = _managed_mapping(summary_description, amount=500, item_count=2)
    bill = sample_qbo_bill(bill_id="B-BLOCKED", doc_number="BM-8852", total=500)
    bill["Line"][0]["Id"] = "1"
    bill["Line"][0]["Description"] = "Changed summarized line"
    _setup_full_import(svc, [bill])
    svc.receipt_map_repo.get_by_external_id.return_value = mapping
    svc.receipt_repo.get_current_receipt_by_id.return_value = current

    result = svc.import_bills(tenant_id="t1", service_id="6", force_full=True)

    assert result["blockedManagedSummary"] == 1
    assert result["failed"] == 1
    assert result["updatedInFIELDCRAFT"] == 0
    svc.receipt_repo.update_receipt_from_qbo.assert_not_called()
    svc.receipt_map_repo.rotate_mapping_for_receipt.assert_not_called()
    svc.receipt_map_repo.mark_mapping_synced.assert_called_once()
    call = svc.receipt_map_repo.mark_mapping_synced.call_args.kwargs
    assert call["sync_status"] == "SYNC_FAILED"
    assert "summarized line was modified" in call["reason"]
    assert "description" in call["reason"]


def test_blocked_managed_summary_does_not_clear_existing_pending_push():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    summary_description = "Material A x 2"
    current = _fieldcraft_receipt([
        {"description": "Material A", "quantity": 2, "price": 100, "totalPrice": 200},
    ], total=200)
    mapping = _managed_mapping(summary_description, amount=200, item_count=1)
    mapping["sync_status"] = "PENDING_PUSH"
    bill = sample_qbo_bill(bill_id="B-PENDING", doc_number="B-PENDING", total=250)
    bill["Line"][0]["Id"] = "1"
    bill["Line"][0]["Description"] = summary_description
    _setup_full_import(svc, [bill])
    svc.receipt_map_repo.get_by_external_id.return_value = mapping
    svc.receipt_repo.get_current_receipt_by_id.return_value = current
    # Avoid the defensive refresh changing the test fixture.
    svc._refresh_managed_fieldcraft_bill_if_needed = MagicMock(return_value=bill)

    result = svc.import_bills(tenant_id="t1", service_id="6", force_full=True)

    assert result["blockedManagedSummary"] == 1
    call = svc.receipt_map_repo.mark_mapping_synced.call_args.kwargs
    assert call["sync_status"] == "PENDING_PUSH"
    svc.receipt_repo.update_receipt_from_qbo.assert_not_called()


def test_unchanged_managed_summary_still_imports_new_separate_qbo_line():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    summary_description = "Material A x 2; Material B x 3"
    items = [
        {"description": "Material A", "quantity": 2, "price": 100, "totalPrice": 200},
        {"description": "Material B", "quantity": 3, "price": 100, "totalPrice": 300},
    ]
    current = _fieldcraft_receipt(items, total=500)
    mapping = _managed_mapping(summary_description, amount=500, item_count=2)
    bill = sample_qbo_bill(bill_id="B-NEW-LINE", doc_number="BM-8852", total=550)
    bill["Line"][0]["Id"] = "1"
    bill["Line"][0]["Amount"] = 500
    bill["Line"][0]["Description"] = summary_description
    bill["Line"].append({
        "Id": "2",
        "Amount": 50,
        "Description": "Delivery",
        "DetailType": "AccountBasedExpenseLineDetail",
        "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "QACC1"}},
    })
    _setup_full_import(svc, [bill])
    svc.receipt_map_repo.get_by_external_id.return_value = mapping
    svc.receipt_repo.get_current_receipt_by_id.return_value = current
    svc.vendor_map_repo.get_by_external_id.return_value = {"vendor_id": 10}
    svc.coa_repo.get_by_external_id.return_value = {"id": 20}
    payload = _qbo_payload(bill["Line"], 550)
    payload.update({
        "invoice_number": "BM-8852",
        "invoice_date": "2024-01-15",
        "project_id": 300,
        "vendor_id": 10,
        "chart_of_account_id": 20,
    })
    svc.receipt_repo.build_receipt_payload_from_qbo.return_value = payload
    svc.receipt_repo.get_receipt_latest_local_timestamp.return_value = None
    svc.receipt_repo.sync_projection_changed.return_value = True
    svc.receipt_repo.update_receipt_from_qbo.return_value = {"id": 211}

    result = svc.import_bills(tenant_id="t1", service_id="6", force_full=True)

    assert result["blockedManagedSummary"] == 0
    assert result["updatedInFIELDCRAFT"] == 1
    update_payload = svc.receipt_repo.update_receipt_from_qbo.call_args.kwargs["payload"]
    merged_items = json.loads(_parse_additional_info(update_payload["receipt_additional_info"])["receiptInfo"])
    assert merged_items[:2] == items
    assert merged_items[2]["description"] == "Delivery"
    assert merged_items[2]["totalPrice"] == 50.0


def test_fieldcraft_managed_summary_line_does_not_replace_original_receipt_items():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    items = [
        {"description": "Mid-America Mount Master Block Square", "quantity": 12, "price": 16.99, "totalPrice": 203.88},
        {"description": "Norandex Matte Inside Corner Post", "quantity": 15, "price": 22.75, "totalPrice": 341.25},
    ]
    summary_description = "Mid-America Mount Master Block Square x 12; Norandex Matte Inside Corner Post x 15"
    current = _fieldcraft_receipt(items)
    mapping = _managed_mapping(summary_description)
    qbo_lines = [{
        "Id": "1",
        "Amount": 545.13,
        "Description": summary_description,
        "DetailType": "AccountBasedExpenseLineDetail",
    }]

    payload, managed = svc._prepare_mapped_fieldcraft_payload(
        current_receipt=current,
        current_mapping=mapping,
        qbo_bill={"Line": qbo_lines, "TotalAmt": 545.13},
        payload=_qbo_payload(qbo_lines, 545.13),
    )

    merged = _parse_additional_info(payload["receipt_additional_info"])
    assert json.loads(merged["receiptInfo"]) == items
    assert managed["id"] == "1"


def test_fieldcraft_managed_summary_imports_new_qbo_line_without_flattening_fieldcraft_items():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    items = [
        {"description": "Material A", "quantity": 2, "price": 100, "totalPrice": 200},
        {"description": "Material B", "quantity": 3, "price": 100, "totalPrice": 300},
    ]
    summary_description = "Material A x 2; Material B x 3"
    current = _fieldcraft_receipt(items, total=500)
    mapping = _managed_mapping(summary_description, amount=500, item_count=2)
    qbo_lines = [
        {"Id": "1", "Amount": 500, "Description": summary_description, "DetailType": "AccountBasedExpenseLineDetail"},
        {"Id": "2", "Amount": 75, "Description": "Permit fee", "DetailType": "AccountBasedExpenseLineDetail"},
    ]

    payload, _ = svc._prepare_mapped_fieldcraft_payload(
        current_receipt=current,
        current_mapping=mapping,
        qbo_bill={"Line": qbo_lines, "TotalAmt": 575},
        payload=_qbo_payload(qbo_lines, 575),
    )

    merged_items = json.loads(_parse_additional_info(payload["receipt_additional_info"])["receiptInfo"])
    assert merged_items[:2] == items
    assert len(merged_items) == 3
    assert merged_items[2]["description"] == "Permit fee"
    assert merged_items[2]["totalPrice"] == 75.0
    assert merged_items[2]["_quickbooksImported"] is True
    assert merged_items[2]["_quickbooksLineId"] == "2"


def test_fieldcraft_managed_summary_imports_user_reported_account_line_and_keeps_total_consistent():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    items = [{
        "quantity": 32,
        "totalPrice": 1054.4,
        "price": "32.95",
        "description": 'Tool Metal Bender 3-\n1/2" Blade #85028',
    }]
    summary_description = 'Tool Metal Bender 3-\n1/2" Blade #85028 x 32'
    current = _fieldcraft_receipt(items, total=1054.4)
    mapping = _managed_mapping(summary_description, amount=1054.4, item_count=1)
    qbo_lines = [
        {
            "Id": "1",
            "Amount": 1054.4,
            "Description": summary_description,
            "DetailType": "AccountBasedExpenseLineDetail",
        },
        {
            "Id": "2",
            "Amount": 602.0,
            "Description": "materials now",
            "DetailType": "AccountBasedExpenseLineDetail",
        },
    ]

    payload, _ = svc._prepare_mapped_fieldcraft_payload(
        current_receipt=current,
        current_mapping=mapping,
        qbo_bill={"Id": "19", "Line": qbo_lines, "TotalAmt": 1656.4},
        payload=_qbo_payload(qbo_lines, 1656.4),
    )

    info = _parse_additional_info(payload["receipt_additional_info"])
    merged_items = json.loads(info["receiptInfo"])
    assert info["total"] == "1656.4"
    assert merged_items[0] == items[0]
    assert merged_items[1]["description"] == "materials now"
    assert merged_items[1]["totalPrice"] == 602.0
    assert merged_items[1]["_quickbooksImported"] is True
    assert merged_items[1]["_quickbooksLineId"] == "2"
    assert sum(float(item["totalPrice"]) for item in merged_items) == pytest.approx(1656.4)


def test_managed_fieldcraft_bill_refreshes_when_total_changed_but_received_lines_are_incomplete():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    items = [{
        "quantity": 32,
        "totalPrice": 1054.4,
        "price": "32.95",
        "description": 'Tool Metal Bender 3-\n1/2" Blade #85028',
    }]
    summary_description = 'Tool Metal Bender 3-\n1/2" Blade #85028 x 32'
    current = _fieldcraft_receipt(items, total=1054.4)
    mapping = _managed_mapping(summary_description, amount=1054.4, item_count=1)
    svc.receipt_repo.get_current_receipt_by_id.return_value = current

    received = {
        "Id": "19",
        "TotalAmt": 1656.4,
        "Line": [{
            "Id": "1",
            "Amount": 1054.4,
            "Description": summary_description,
            "DetailType": "AccountBasedExpenseLineDetail",
        }],
    }
    refreshed = {
        "Id": "19",
        "TotalAmt": 1656.4,
        "Line": [
            received["Line"][0],
            {
                "Id": "2",
                "Amount": 602.0,
                "Description": "materials now",
                "DetailType": "AccountBasedExpenseLineDetail",
            },
        ],
    }
    svc._fetch_qbo_bill_by_id = MagicMock(return_value=refreshed)

    result = svc._refresh_managed_fieldcraft_bill_if_needed(
        current_mapping=mapping,
        qbo_bill=received,
        tenant_id="t1",
        access_token="token",
        base_url="https://qbo.example",
        realm_id="realm",
    )

    assert result == refreshed
    svc._fetch_qbo_bill_by_id.assert_called_once_with(
        access_token="token",
        base_url="https://qbo.example",
        realm_id="realm",
        bill_id="19",
    )


def test_fieldcraft_managed_summary_rebuilds_qbo_added_lines_without_duplicates_on_edit():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    base_items = [
        {"description": "Material A", "quantity": 2, "price": 100, "totalPrice": 200},
        {"description": "Material B", "quantity": 3, "price": 100, "totalPrice": 300},
    ]
    previously_imported = {
        "description": "Permit fee",
        "totalPrice": 75.0,
        "_quickbooksImported": True,
        "_quickbooksLineId": "2",
    }
    current = _fieldcraft_receipt(base_items + [previously_imported], total=575)
    summary_description = "Material A x 2; Material B x 3"
    mapping = _managed_mapping(summary_description, amount=500, item_count=2)
    qbo_lines = [
        {"Id": "1", "Amount": 500, "Description": summary_description, "DetailType": "AccountBasedExpenseLineDetail"},
        {"Id": "2", "Amount": 90, "Description": "Permit fee updated", "DetailType": "AccountBasedExpenseLineDetail"},
    ]

    payload, _ = svc._prepare_mapped_fieldcraft_payload(
        current_receipt=current,
        current_mapping=mapping,
        qbo_bill={"Line": qbo_lines, "TotalAmt": 590},
        payload=_qbo_payload(qbo_lines, 590),
    )

    merged_items = json.loads(_parse_additional_info(payload["receipt_additional_info"])["receiptInfo"])
    assert len(merged_items) == 3
    assert merged_items[:2] == base_items
    assert merged_items[2]["description"] == "Permit fee updated"
    assert merged_items[2]["totalPrice"] == 90.0


def test_fieldcraft_managed_summary_removes_qbo_added_line_when_deleted_in_qbo():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    base_items = [
        {"description": "Material A", "quantity": 2, "price": 100, "totalPrice": 200},
        {"description": "Material B", "quantity": 3, "price": 100, "totalPrice": 300},
    ]
    current = _fieldcraft_receipt(base_items + [{
        "description": "Permit fee",
        "totalPrice": 75.0,
        "_quickbooksImported": True,
        "_quickbooksLineId": "2",
    }], total=575)
    summary_description = "Material A x 2; Material B x 3"
    mapping = _managed_mapping(summary_description, amount=500, item_count=2)
    qbo_lines = [{
        "Id": "1",
        "Amount": 500,
        "Description": summary_description,
        "DetailType": "AccountBasedExpenseLineDetail",
    }]

    payload, _ = svc._prepare_mapped_fieldcraft_payload(
        current_receipt=current,
        current_mapping=mapping,
        qbo_bill={"Line": qbo_lines, "TotalAmt": 500},
        payload=_qbo_payload(qbo_lines, 500),
    )

    merged_items = json.loads(_parse_additional_info(payload["receipt_additional_info"])["receiptInfo"])
    assert merged_items == base_items


def test_prepare_managed_payload_does_not_create_synthetic_row_for_summary_edit():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    base_items = [
        {"description": "Material A", "quantity": 2, "price": 100, "totalPrice": 200},
        {"description": "Material B", "quantity": 3, "price": 100, "totalPrice": 300},
    ]
    summary_description = "Material A x 2; Material B x 3"
    current = _fieldcraft_receipt(base_items, total=500)
    mapping = _managed_mapping(summary_description, amount=500, item_count=2)
    qbo_lines = [{
        "Id": "1",
        "Amount": 550,
        "Description": "Edited transport description",
        "DetailType": "AccountBasedExpenseLineDetail",
    }]

    payload, managed = svc._prepare_mapped_fieldcraft_payload(
        current_receipt=current,
        current_mapping=mapping,
        qbo_bill={"Line": qbo_lines, "TotalAmt": 550},
        payload=_qbo_payload(qbo_lines, 550),
    )

    merged_items = json.loads(_parse_additional_info(payload["receipt_additional_info"])["receiptInfo"])
    assert merged_items == base_items
    assert not any(item.get("_quickbooksSynthetic") for item in merged_items)
    assert "qboCurrent" not in managed


def test_fieldcraft_managed_summary_deleted_in_qbo_preserves_fieldcraft_rows_and_imports_replacement_lines():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    base_items = [
        {"description": "Material A", "quantity": 2, "price": 100, "totalPrice": 200},
        {"description": "Material B", "quantity": 3, "price": 100, "totalPrice": 300},
    ]
    summary_description = "Material A x 2; Material B x 3"
    current = _fieldcraft_receipt(base_items, total=500)
    mapping = _managed_mapping(summary_description, amount=500, item_count=2, line_id="1")
    qbo_lines = [{
        "Id": "9",
        "Amount": 400,
        "Description": "Replacement line from QuickBooks",
        "DetailType": "AccountBasedExpenseLineDetail",
    }]

    payload, managed = svc._prepare_mapped_fieldcraft_payload(
        current_receipt=current,
        current_mapping=mapping,
        qbo_bill={"Line": qbo_lines, "TotalAmt": 400},
        payload=_qbo_payload(qbo_lines, 400),
    )

    merged_items = json.loads(_parse_additional_info(payload["receipt_additional_info"])["receiptInfo"])
    assert merged_items[:2] == base_items
    assert merged_items[2]["description"] == "Replacement line from QuickBooks"
    assert merged_items[2]["totalPrice"] == 400.0
    assert len(merged_items) == 3
    assert "qboCurrent" not in managed


def test_user_reported_managed_line_edit_has_no_fake_adjustment_row():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    base_items = [{
        "description": 'Tool Metal Bender 3-1/2" Blade #85028',
        "quantity": 11,
        "price": 32.95,
        "totalPrice": 362.45,
    }]
    summary_description = 'Tool Metal Bender 3-1/2" Blade #85028 x 11'
    current = _fieldcraft_receipt(base_items, total=362.45)
    mapping = _managed_mapping(summary_description, amount=362.45, item_count=1)
    qbo_lines = [
        {
            "Id": "1",
            "Amount": 420.0,
            "Description": summary_description + " changed",
            "DetailType": "AccountBasedExpenseLineDetail",
        },
        {
            "Id": "2",
            "Amount": 5555.0,
            "Description": "hi materials! x 2",
            "DetailType": "AccountBasedExpenseLineDetail",
        },
        {
            "Id": "3",
            "Amount": 33.0,
            "Description": "COGS Supplies and Materials",
            "DetailType": "AccountBasedExpenseLineDetail",
        },
    ]

    payload, managed = svc._prepare_mapped_fieldcraft_payload(
        current_receipt=current,
        current_mapping=mapping,
        qbo_bill={"Id": "25", "Line": qbo_lines, "TotalAmt": 6008.0},
        payload=_qbo_payload(qbo_lines, 6008.0),
    )

    info = _parse_additional_info(payload["receipt_additional_info"])
    merged_items = json.loads(info["receiptInfo"])
    assert info["total"] == "6008.0"
    assert [item["description"] for item in merged_items] == [
        'Tool Metal Bender 3-1/2" Blade #85028',
        "hi materials! x 2",
        "COGS Supplies and Materials",
    ]
    assert not any(item.get("_quickbooksSynthetic") for item in merged_items)
    assert "qboCurrent" not in managed


def test_managed_item_count_preserves_previously_imported_rows_after_next_export():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    current_items = [
        {"description": "Material A", "quantity": 2, "price": 100, "totalPrice": 200},
        {
            "description": "Permit fee",
            "totalPrice": 75.0,
            "_quickbooksImported": True,
            "_quickbooksLineId": "2",
        },
    ]
    summary_description = "Material A x 2; Permit fee"
    current = _fieldcraft_receipt(current_items, total=275)
    mapping = _managed_mapping(summary_description, amount=275, item_count=2, line_id="1")
    qbo_lines = [{
        "Id": "1",
        "Amount": 275,
        "Description": summary_description,
        "DetailType": "AccountBasedExpenseLineDetail",
    }]

    payload, _ = svc._prepare_mapped_fieldcraft_payload(
        current_receipt=current,
        current_mapping=mapping,
        qbo_bill={"Line": qbo_lines, "TotalAmt": 275},
        payload=_qbo_payload(qbo_lines, 275),
    )

    merged_items = json.loads(_parse_additional_info(payload["receipt_additional_info"])["receiptInfo"])
    assert merged_items == current_items


def test_legacy_fieldcraft_summary_line_is_inferred_without_ai_or_mapping_metadata():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    items = [
        {"description": "Material A", "quantity": 2, "price": 100, "totalPrice": 200},
        {"description": "Material B", "quantity": 3, "price": 100, "totalPrice": 300},
    ]
    current = _fieldcraft_receipt(items, total=500)
    summary_description = "Material A x 2; Material B x 3"
    qbo_lines = [{
        "Id": "7",
        "Amount": 500,
        "Description": summary_description,
        "DetailType": "AccountBasedExpenseLineDetail",
    }]

    payload, managed = svc._prepare_mapped_fieldcraft_payload(
        current_receipt=current,
        current_mapping={"reason": None},
        qbo_bill={"Line": qbo_lines, "TotalAmt": 500},
        payload=_qbo_payload(qbo_lines, 500),
    )

    merged_items = json.loads(_parse_additional_info(payload["receipt_additional_info"])["receiptInfo"])
    assert merged_items == items
    assert managed["id"] == "7"
    assert managed["itemCount"] == 2


def test_quickbooks_origin_bill_keeps_existing_full_import_behavior():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    current = {
        "id": 77,
        "created_by": const.SERVICE_NAME_QUICKBOOKS,
        "receipt_additional_info": json.dumps([{"total": "10", "receiptInfo": "[]"}]),
    }
    qbo_lines = [{
        "Id": "4",
        "Amount": 25,
        "Description": "QBO line",
        "DetailType": "AccountBasedExpenseLineDetail",
    }]
    original_payload = _qbo_payload(qbo_lines, 25)

    payload, managed = svc._prepare_mapped_fieldcraft_payload(
        current_receipt=current,
        current_mapping=_managed_mapping("anything", amount=10, item_count=0),
        qbo_bill={"Line": qbo_lines, "TotalAmt": 25},
        payload=original_payload,
    )

    assert payload == original_payload
    assert managed is None


def test_paid_fieldcraft_bill_is_not_downgraded_by_unpaid_qbo_bill_payload():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    current = {
        "id": 226,
        "created_by": "8aef1564-cd04-4b85-9633-49ad6f4f3231",
        "status": "Approve",
        "paid_date": "2026-08-12T06:04:23.961087+00:00",
        "receipt_additional_info": json.dumps([{
            "total": "20",
            "billNo": "BILL-Paid",
            "paid": True,
            "paymentMethod": "Credit Card",
            "paymentType": "Company Card",
            "receiptInfo": json.dumps([{
                "description": "Prepay Fuel",
                "quantity": 15,
                "price": 1,
                "totalPrice": 15,
            }]),
        }]),
    }
    incoming = {
        "status": "Approve",
        "paid_date": None,
        "receipt_additional_info": json.dumps([{
            "total": "20.0",
            "billNo": "BILL-Paid",
            "paid": False,
            "paymentMethod": "",
            "paymentType": "",
            "receiptInfo": "[]",
        }]),
    }

    payload = svc._preserve_paid_fieldcraft_state(
        current_receipt=current,
        payload=incoming,
    )

    info = _parse_additional_info(payload["receipt_additional_info"])
    assert info["paid"] is True
    assert info["paymentMethod"] == "Credit Card"
    assert info["paymentType"] == "Company Card"
    assert payload["status"] == "Approve"
    assert payload["paid_date"] == "2026-08-12T06:04:23.961087+00:00"


def test_paid_fieldcraft_bill_preserves_payment_state_while_importing_new_qbo_lines():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    base_items = [{
        "description": "Prepay Fuel",
        "quantity": 15,
        "price": 1,
        "totalPrice": 15,
    }]
    summary_description = "Prepay Fuel x 15"
    current = {
        **_fieldcraft_receipt(base_items, total=20),
        "status": "Approve",
        "paid_date": "2026-08-12T06:04:23.961087+00:00",
        "receipt_additional_info": json.dumps([{
            "total": "20",
            "billNo": "BILL-Paid",
            "paid": True,
            "paymentMethod": "Credit Card",
            "paymentType": "Company Card",
            "receiptInfo": json.dumps(base_items),
        }]),
    }
    mapping = _managed_mapping(summary_description, amount=20, item_count=1)
    qbo_lines = [
        {
            "Id": "1",
            "Amount": 20,
            "Description": summary_description,
            "DetailType": "AccountBasedExpenseLineDetail",
        },
        {
            "Id": "2",
            "Amount": 5,
            "Description": "QuickBooks added line",
            "DetailType": "AccountBasedExpenseLineDetail",
        },
    ]
    incoming = {
        "status": "Approve",
        "paid_date": None,
        "receipt_additional_info": json.dumps([{
            "total": "25",
            "paid": False,
            "paymentMethod": "",
            "paymentType": "",
            "receiptInfo": json.dumps(qbo_lines),
        }]),
    }

    payload, _ = svc._prepare_mapped_fieldcraft_payload(
        current_receipt=current,
        current_mapping=mapping,
        qbo_bill={"Line": qbo_lines, "TotalAmt": 25},
        payload=incoming,
    )

    info = _parse_additional_info(payload["receipt_additional_info"])
    items = json.loads(info["receiptInfo"])
    assert info["paid"] is True
    assert info["paymentMethod"] == "Credit Card"
    assert info["paymentType"] == "Company Card"
    assert items[0] == base_items[0]
    assert items[1]["description"] == "QuickBooks added line"
    assert items[1]["_quickbooksImported"] is True


def test_unpaid_fieldcraft_bill_can_still_become_paid_from_qbo():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    current = {
        "id": 227,
        "created_by": "8aef1564-cd04-4b85-9633-49ad6f4f3231",
        "status": "Approve",
        "paid_date": None,
        "receipt_additional_info": json.dumps([{
            "total": "20",
            "paid": False,
            "receiptInfo": "[]",
        }]),
    }
    incoming = {
        "status": "paid",
        "paid_date": "2026-08-12T08:00:00+00:00",
        "receipt_additional_info": json.dumps([{
            "total": "20",
            "paid": True,
            "receiptInfo": "[]",
        }]),
    }

    payload = svc._preserve_paid_fieldcraft_state(
        current_receipt=current,
        payload=incoming,
    )

    assert payload == incoming


def test_quickbooks_origin_bill_keeps_qbo_payment_state_updates():
    svc = make_service()
    svc.receipt_repo.parse_additional_info.side_effect = _parse_additional_info
    current = {
        "id": 228,
        "created_by": const.SERVICE_NAME_QUICKBOOKS,
        "status": "paid",
        "paid_date": "2026-08-12T06:00:00+00:00",
        "receipt_additional_info": json.dumps([{
            "total": "20",
            "paid": True,
            "receiptInfo": "[]",
        }]),
    }
    incoming = {
        "status": "Approve",
        "paid_date": None,
        "receipt_additional_info": json.dumps([{
            "total": "20",
            "paid": False,
            "receiptInfo": "[]",
        }]),
    }

    payload = svc._preserve_paid_fieldcraft_state(
        current_receipt=current,
        payload=incoming,
    )

    assert payload == incoming
