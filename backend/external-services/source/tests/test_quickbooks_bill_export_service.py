import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from source.quickbooks.QuickBooksBillExportService import QuickBooksBillExportService
from source.Utils import constants as const


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_service():
    svc = QuickBooksBillExportService()
    svc.qbo_repo = MagicMock()
    svc.qbo_service = MagicMock()
    svc.receipt_repo = MagicMock()
    svc.receipt_map_repo = MagicMock()
    svc.vendor_map_repo = MagicMock()
    svc.vendor_repo = MagicMock()
    svc.coa_repo = MagicMock()
    svc.customer_repo = MagicMock()
    svc.project_repo = MagicMock()
    svc.project_repo.normalize_project_sync_target.side_effect = (
        lambda target, default=None: target if target in const.PROJECT_SYNC_TARGETS_ALLOWED else default
    )
    svc.capability_service = MagicMock()
    svc.capability_service.projects_available.return_value = True
    # status constants used by service
    svc.receipt_repo.STATUS_PENDING_PUSH = "PENDING_PUSH"
    svc.receipt_repo.STATUS_SYNCED = "SYNCED"
    svc.receipt_repo.STATUS_FAILED = "FAILED"
    svc.receipt_repo.STATUS_IMPORT_FAILED = "IMPORT_FAILED"
    # default: service_master found
    svc.qbo_repo.getServiceIdByName.return_value = "6"
    svc.project_repo.get_mapping_sync_target.return_value = const.PROJECT_SYNC_TARGET_SUB_CUSTOMER
    return svc


def connected_info(status="connected"):
    return {
        const.KEY_REFRESH_TOKEN: "RT",
        const.KEY_REALM_ID: "123456",
        const.KEY_ENV: "sandbox",
        const.KEY_STATUS: status,
    }


def config_ok():
    return {const.KEY_CLIENT_ID: "CID", const.KEY_CLIENT_SECRET: "SEC"}


def sample_receipt(receipt_id=1, vendor_id=10, coa_id=20, project_id=30, doc_number="INV-001", txn_date="2024-01-15", amount=100.0):
    additional_info = json.dumps([{
        "total": str(amount),
        "billDate": txn_date,
        "billNo": doc_number,
        "receiptInfo": "[]",
        "taxInfos": "[]",
        "paid": False,
        "category": "Test Category",
    }])
    return {
        "id": receipt_id,
        "vendor_id": vendor_id,
        "chart_of_account_id": coa_id,
        "project_id": project_id,
        "invoice_number": doc_number,
        "invoice_date": f"{txn_date}T00:00:00+00:00",
        "order_date": None,
        "description": "Test bill",
        "file_name": f"Bill {doc_number}.json",
        "name": "VendorBill",
        "status": "Approve",
        "updated_at": "2024-01-15T08:00:00+00:00",
        "receipt_additional_info": additional_info,
        "doc_number": doc_number,
        "txn_date": txn_date,
        "amount": amount,
    }


def sample_mapping(mapping_id=1, external_receipt_id=None, sync_status="PENDING_PUSH", sync_token=None):
    return {
        "id": mapping_id,
        "receipt_id": 1,
        "external_receipt_id": external_receipt_id,
        "external_account_id": "123456",
        "sync_status": sync_status,
        "sync_token": sync_token,
    }


def sample_candidate(receipt=None, mapping=None):
    return {
        "receipt": receipt or sample_receipt(),
        "mapping": mapping or sample_mapping(),
    }


def sample_qbo_bill(bill_id="B1", sync_token="0", last_updated="2024-01-10T10:00:00+00:00"):
    return {
        const.QBO_KEY_ID: bill_id,
        const.QBO_KEY_SYNC_TOKEN: sync_token,
        "DocNumber": "INV-001",
        "TxnDate": "2024-01-15",
        "MetaData": {"LastUpdatedTime": last_updated},
    }


def _setup_export(svc, candidates):
    """Wire up the happy-path connection dependencies."""
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()
    svc.refresh_access_token = MagicMock(return_value="TOKEN")
    svc.get_qbo_api_base = MagicMock(return_value="https://sandbox-qbo")
    svc.receipt_repo.get_export_candidates.return_value = candidates
    # default receipt_repo helpers
    svc.receipt_repo.parse_additional_info.return_value = {
        "total": "100.0",
        "billDate": "2024-01-15",
        "billNo": "INV-001",
        "receiptInfo": "[]",
    }
    svc.receipt_repo._pick_doc_number.return_value = "INV-001"
    svc.receipt_repo._pick_txn_date.return_value = "2024-01-15"
    svc.receipt_repo._pick_due_date.return_value = None
    svc.receipt_repo._pick_total_amount.return_value = 100.0
    # default: vendor mapped
    svc.vendor_map_repo.get_by_vendor_id.return_value = {
        "id": 1,
        "external_vendor_id": "QV-1",
        "sync_status": "SYNCED",
    }
    # default: COA mapped
    svc.coa_repo.get_external_id_for_account_id.return_value = {
        "id": 20,
        "external_id": "QACC-1",
    }
    svc.project_repo.get_current_mapping_by_project_id.return_value = {
        "id": 30,
        "project_id": 30,
        "external_project_id": "QPROJ-1",
        "sync_status": "SYNCED",
    }
    svc.project_repo.get_project_by_id.return_value = {"id": 30, "description": "Project A"}
    svc.project_repo.get_mapping_sync_target.return_value = const.PROJECT_SYNC_TARGET_SUB_CUSTOMER


# ---------------------------------------------------------------------------
# export_bills – connection / config guard tests
# ---------------------------------------------------------------------------

def test_export_bills_missing_connection():
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = {}
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result[const.KEY_OK] is False
    assert "not connected" in result[const.KEY_MESSAGE].lower()


def test_export_bills_disconnected_status():
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info(status="disconnected")
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result[const.KEY_OK] is False
    assert "not connected" in result[const.KEY_MESSAGE].lower()


def test_export_bills_missing_client_credentials():
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = {}
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result[const.KEY_OK] is False
    assert "client credentials" in result[const.KEY_MESSAGE].lower()


def test_export_bills_qbo_service_master_not_found():
    svc = make_service()
    svc.qbo_repo.getAdditionalInfo.return_value = connected_info()
    svc.qbo_service.get_quickbooks_config.return_value = config_ok()
    svc.refresh_access_token = MagicMock(return_value="TOKEN")
    svc.get_qbo_api_base = MagicMock(return_value="https://sandbox-qbo")
    svc.qbo_repo.getServiceIdByName.return_value = None
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result[const.KEY_OK] is False
    assert "service_master" in result[const.KEY_MESSAGE].lower()


# ---------------------------------------------------------------------------
# export_bills – no candidates
# ---------------------------------------------------------------------------

def test_export_bills_no_candidates():
    svc = make_service()
    _setup_export(svc, [])
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result[const.KEY_OK] is True
    assert result["requested"] == 0
    assert "no pending" in result[const.KEY_MESSAGE].lower()
    svc.receipt_map_repo.mark_mapping_synced.assert_not_called()


# ---------------------------------------------------------------------------
# export_bills – skip already mapped
# ---------------------------------------------------------------------------

def test_export_bills_skips_already_mapped():
    svc = make_service()
    mapping = sample_mapping(external_receipt_id="B99", sync_status="SYNCED")
    _setup_export(svc, [sample_candidate(mapping=mapping)])
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result["skippedAlreadyMapped"] == 1
    svc.receipt_map_repo.mark_mapping_synced.assert_not_called()
    svc.create_bill_in_qbo = MagicMock()
    svc.create_bill_in_qbo.assert_not_called()


# ---------------------------------------------------------------------------
# export_bills – failed payload: no vendor mapping
# ---------------------------------------------------------------------------

def test_export_bills_fails_when_no_vendor_mapping():
    svc = make_service()
    _setup_export(svc, [sample_candidate(receipt=sample_receipt(vendor_id=99))])
    svc.vendor_map_repo.get_by_vendor_id.return_value = None
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result["failed"] == 1
    assert "no quickbooks mapping" in result[const.KEY_RESULTS][0][const.KEY_ERROR].lower() or \
           "re-sync" in result[const.KEY_RESULTS][0][const.KEY_ERROR].lower()
    svc.receipt_map_repo.mark_mapping_failed.assert_called_once()


def test_export_bills_fails_when_vendor_sync_status_failed():
    svc = make_service()
    _setup_export(svc, [sample_candidate()])
    svc.vendor_map_repo.get_by_vendor_id.return_value = {
        "id": 1,
        "external_vendor_id": "QV-1",
        "sync_status": "FAILED",
    }
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result["failed"] == 1
    error_msg = result[const.KEY_RESULTS][0][const.KEY_ERROR].lower()
    assert "failed" in error_msg or "re-sync" in error_msg


def test_export_bills_fails_when_no_chart_of_account_id():
    svc = make_service()
    receipt = sample_receipt(coa_id=None)
    _setup_export(svc, [sample_candidate(receipt=receipt)])
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result["failed"] == 1
    assert "chart of accounts" in result[const.KEY_RESULTS][0][const.KEY_ERROR].lower()


def test_export_bills_fails_when_coa_not_mapped_in_qbo():
    svc = make_service()
    _setup_export(svc, [sample_candidate()])
    svc.coa_repo.get_external_id_for_account_id.return_value = None
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result["failed"] == 1
    assert "external id" in result[const.KEY_RESULTS][0][const.KEY_ERROR].lower() or \
           "chart of account" in result[const.KEY_RESULTS][0][const.KEY_ERROR].lower()


def test_export_bills_fails_when_no_txn_date():
    svc = make_service()
    receipt = sample_receipt()
    receipt["txn_date"] = None
    receipt["invoice_date"] = None
    receipt["order_date"] = None
    receipt["receipt_additional_info"] = json.dumps([{"total": "100.0", "billDate": None, "billNo": "INV-001"}])
    _setup_export(svc, [sample_candidate(receipt=receipt)])
    svc.receipt_repo._pick_txn_date.return_value = None
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result["failed"] == 1
    assert "bill date" in result[const.KEY_RESULTS][0][const.KEY_ERROR].lower() or \
           "date" in result[const.KEY_RESULTS][0][const.KEY_ERROR].lower()


def test_export_bills_fails_when_amount_zero():
    svc = make_service()
    _setup_export(svc, [sample_candidate(receipt=sample_receipt(amount=0.0))])
    svc.receipt_repo._pick_total_amount.return_value = 0.0
    svc.receipt_repo.parse_additional_info.return_value = {"total": "0.0", "billDate": "2024-01-15", "billNo": "INV-001"}
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result["failed"] == 1
    assert "amount" in result[const.KEY_RESULTS][0][const.KEY_ERROR].lower()


def test_export_bills_fails_when_amount_missing():
    svc = make_service()
    _setup_export(svc, [sample_candidate()])
    svc.receipt_repo._pick_total_amount.return_value = None
    svc.receipt_repo.parse_additional_info.return_value = {"billDate": "2024-01-15", "billNo": "INV-001"}
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result["failed"] == 1
    assert "amount" in result[const.KEY_RESULTS][0][const.KEY_ERROR].lower()


# ---------------------------------------------------------------------------
# export_bills – create new bill (no prior QBO id)
# ---------------------------------------------------------------------------

def test_export_bills_creates_new_bill_in_qbo():
    svc = make_service()
    _setup_export(svc, [sample_candidate()])
    svc.find_bill_candidates_in_qbo = MagicMock(return_value=[])
    svc.create_bill_in_qbo = MagicMock(return_value={const.QBO_KEY_ID: "NEWB-1", const.QBO_KEY_SYNC_TOKEN: "0"})
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result["createdInQbo"] == 1
    assert result[const.KEY_RESULTS][0][const.KEY_STATUS] == "success"
    assert result[const.KEY_RESULTS][0][const.KEY_ACTION] == "created in QBO"
    assert result[const.KEY_RESULTS][0][const.KEY_QBO_BILL_ID] == "NEWB-1"
    svc.receipt_map_repo.mark_mapping_synced.assert_called_once()
    call_kwargs = svc.receipt_map_repo.mark_mapping_synced.call_args.kwargs
    assert call_kwargs["external_receipt_id"] == "NEWB-1"
    assert call_kwargs["sync_token"] == "0"


def test_export_bills_create_maps_when_doc_number_match():
    svc = make_service()
    _setup_export(svc, [sample_candidate()])
    existing_qbo = {const.QBO_KEY_ID: "EXISTB-5", const.QBO_KEY_SYNC_TOKEN: "2"}
    svc.find_bill_candidates_in_qbo = MagicMock(return_value=[existing_qbo])
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result["mapped from QBO"] == 1
    assert result[const.KEY_RESULTS][0][const.KEY_ACTION] == "mapped from QBO"
    assert result[const.KEY_RESULTS][0][const.KEY_QBO_BILL_ID] == "EXISTB-5"
    svc.receipt_map_repo.mark_mapping_synced.assert_called_once()


def test_export_bills_fails_on_multiple_doc_number_matches():
    svc = make_service()
    _setup_export(svc, [sample_candidate()])
    svc.find_bill_candidates_in_qbo = MagicMock(return_value=[
        {const.QBO_KEY_ID: "B1"}, {const.QBO_KEY_ID: "B2"}
    ])
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result["failed"] == 1
    assert "multiple" in result[const.KEY_RESULTS][0][const.KEY_ERROR].lower()
    svc.receipt_map_repo.mark_mapping_failed.assert_called_once()


# ---------------------------------------------------------------------------
# export_bills – update existing bill
# ---------------------------------------------------------------------------

def test_export_bills_updates_existing_bill_in_qbo():
    svc = make_service()
    mapping = sample_mapping(external_receipt_id="B10", sync_status="PENDING_PUSH", sync_token="3")
    _setup_export(svc, [sample_candidate(mapping=mapping)])
    qbo_bill = sample_qbo_bill(bill_id="B10", sync_token="3", last_updated="2024-01-01T00:00:00+00:00")
    svc.get_bill_in_qbo = MagicMock(return_value=qbo_bill)
    svc.update_bill_in_qbo = MagicMock(return_value={const.QBO_KEY_ID: "B10", const.QBO_KEY_SYNC_TOKEN: "4"})
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result["updatedInQbo"] == 1
    assert result[const.KEY_RESULTS][0][const.KEY_ACTION] == "updated in QBO"
    assert result[const.KEY_RESULTS][0][const.KEY_QBO_BILL_ID] == "B10"
    svc.update_bill_in_qbo.assert_called_once()
    svc.receipt_map_repo.mark_mapping_synced.assert_called_once()


def test_export_bills_update_failed_mapping_retries():
    """FAILED mappings with an external_receipt_id should still try to update."""
    svc = make_service()
    mapping = sample_mapping(external_receipt_id="B20", sync_status="FAILED", sync_token="1")
    _setup_export(svc, [sample_candidate(mapping=mapping)])
    qbo_bill = sample_qbo_bill(bill_id="B20", sync_token="1", last_updated="2024-01-01T00:00:00+00:00")
    svc.get_bill_in_qbo = MagicMock(return_value=qbo_bill)
    svc.update_bill_in_qbo = MagicMock(return_value={const.QBO_KEY_ID: "B20", const.QBO_KEY_SYNC_TOKEN: "2"})
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result["updatedInQbo"] == 1
    svc.update_bill_in_qbo.assert_called_once()


# ---------------------------------------------------------------------------
# export_bills – conflict detection (QBO newer → skip)
# ---------------------------------------------------------------------------

def test_export_bills_skips_when_qbo_newer_by_sync_token():
    svc = make_service()
    mapping = sample_mapping(external_receipt_id="B30", sync_status="PENDING_PUSH", sync_token="1")
    receipt = sample_receipt()
    receipt["updated_at"] = "2024-01-01T00:00:00+00:00"
    _setup_export(svc, [sample_candidate(receipt=receipt, mapping=mapping)])
    # QBO has a different (newer) sync token and a later LastUpdatedTime
    qbo_bill = sample_qbo_bill(bill_id="B30", sync_token="5", last_updated="2024-01-20T00:00:00+00:00")
    svc.get_bill_in_qbo = MagicMock(return_value=qbo_bill)
    svc.update_bill_in_qbo = MagicMock()
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result["skippedQboNewer"] == 1
    assert result[const.KEY_RESULTS][0][const.KEY_ACTION] == "skippedQboNewer"
    svc.receipt_map_repo.mark_mapping_synced.assert_called_once()
    # must not call update
    svc.update_bill_in_qbo.assert_not_called()


def test_export_bills_exports_when_fieldcraft_newer_despite_token_mismatch():
    svc = make_service()
    mapping = sample_mapping(external_receipt_id="B31", sync_status="PENDING_PUSH", sync_token="1")
    receipt = sample_receipt()
    receipt["updated_at"] = "2024-01-20T00:00:00+00:00"  # FIELDCRAFT is newer
    _setup_export(svc, [sample_candidate(receipt=receipt, mapping=mapping)])
    # QBO token differs but FIELDCRAFT timestamp is later → export should proceed
    qbo_bill = sample_qbo_bill(bill_id="B31", sync_token="2", last_updated="2024-01-10T00:00:00+00:00")
    svc.get_bill_in_qbo = MagicMock(return_value=qbo_bill)
    svc.update_bill_in_qbo = MagicMock(return_value={const.QBO_KEY_ID: "B31", const.QBO_KEY_SYNC_TOKEN: "3"})
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result["updatedInQbo"] == 1
    svc.update_bill_in_qbo.assert_called_once()


# ---------------------------------------------------------------------------
# export_bills – stale reference fallthrough to create
# ---------------------------------------------------------------------------

def test_export_bills_stale_reference_falls_through_to_create():
    svc = make_service()
    mapping = sample_mapping(external_receipt_id="STALE-1", sync_status="PENDING_PUSH", sync_token="0")
    _setup_export(svc, [sample_candidate(mapping=mapping)])
    svc.get_bill_in_qbo = MagicMock(side_effect=Exception("object not found [610]"))
    svc.find_bill_candidates_in_qbo = MagicMock(return_value=[])
    svc.create_bill_in_qbo = MagicMock(return_value={const.QBO_KEY_ID: "NEW-2", const.QBO_KEY_SYNC_TOKEN: "0"})
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result["createdInQbo"] == 1
    svc.create_bill_in_qbo.assert_called_once()


# ---------------------------------------------------------------------------
# export_bills – deleted vendor ref → marks vendor mapping FAILED
# ---------------------------------------------------------------------------

def test_export_bills_marks_vendor_mapping_failed_on_deleted_ref():
    svc = make_service()
    _setup_export(svc, [sample_candidate()])
    svc.find_bill_candidates_in_qbo = MagicMock(return_value=[])
    svc.create_bill_in_qbo = MagicMock(side_effect=Exception("Vendor has been deleted"))
    svc.vendor_map_repo.get_by_vendor_id.return_value = {
        "id": 5,
        "external_vendor_id": "QV-1",
        "sync_status": "SYNCED",
    }
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result["failed"] == 1
    # vendor mapping should have been marked FAILED
    svc.vendor_map_repo.mark_mapping_failed.assert_called_once()
    call_kwargs = svc.vendor_map_repo.mark_mapping_failed.call_args.kwargs
    assert call_kwargs["mapping_id"] == 5


# ---------------------------------------------------------------------------
# export_bills – summary counters are accurate
# ---------------------------------------------------------------------------

def test_export_bills_summary_structure():
    svc = make_service()
    _setup_export(svc, [sample_candidate()])
    svc.find_bill_candidates_in_qbo = MagicMock(return_value=[])
    svc.create_bill_in_qbo = MagicMock(return_value={const.QBO_KEY_ID: "X1", const.QBO_KEY_SYNC_TOKEN: "0"})
    result = svc.export_bills(tenant_id="t1", service_id="6")
    assert result[const.KEY_OK] is True
    assert result["requested"] == 1
    assert result["createdInQbo"] == 1
    assert result["failed"] == 0
    assert len(result[const.KEY_RESULTS]) == 1
    assert len(result[const.KEY_FAILURES]) == 0


# ---------------------------------------------------------------------------
# _build_qbo_bill_payload unit tests
# ---------------------------------------------------------------------------

def test_build_payload_structure():
    svc = make_service()
    svc._tenant_id = "t1"
    svc._service_id = "6"
    receipt = sample_receipt(doc_number="DOC-999", txn_date="2024-03-01", amount=250.0)
    svc.receipt_repo.parse_additional_info.return_value = {"total": "250.0", "billDate": "2024-03-01", "billNo": "DOC-999"}
    svc.receipt_repo._pick_doc_number.return_value = "DOC-999"
    svc.receipt_repo._pick_txn_date.return_value = "2024-03-01"
    svc.receipt_repo._pick_due_date.return_value = None
    svc.receipt_repo._pick_total_amount.return_value = 250.0
    svc.vendor_map_repo.get_by_vendor_id.return_value = {"id": 1, "external_vendor_id": "V-7", "sync_status": "SYNCED"}
    svc.coa_repo.get_external_id_for_account_id.return_value = {"id": 20, "external_id": "A-5"}
    svc.project_repo.get_current_mapping_by_project_id.return_value = {"id": 30, "external_project_id": "QPROJ-1", "sync_status": "SYNCED"}

    payload = svc._build_qbo_bill_payload(
        tenant_id="t1", service_id="6", realm_id="RID", qbo_service_master_id="6", receipt=receipt
    )

    assert payload["VendorRef"] == {"value": "V-7"}
    assert payload["TxnDate"] == "2024-03-01"
    assert "DueDate" not in payload
    assert payload["DocNumber"] == "DOC-999"
    assert payload["Line"][0]["Amount"] == 250.0
    assert payload["Line"][0]["DetailType"] == "AccountBasedExpenseLineDetail"
    line_detail = payload["Line"][0]["AccountBasedExpenseLineDetail"]
    assert line_detail["AccountRef"]["value"] == "A-5"
    assert line_detail["CustomerRef"] == {"value": "QPROJ-1"}
    assert line_detail["BillableStatus"] == "Billable"


def test_build_payload_includes_due_date_when_present():
    svc = make_service()
    svc._tenant_id = "t1"
    svc._service_id = "6"
    receipt = sample_receipt(doc_number="DOC-999", txn_date="2024-03-01", amount=250.0)
    svc.receipt_repo.parse_additional_info.return_value = {"total": "250.0", "billDate": "2024-03-01", "dueDate": "2024-03-31", "billNo": "DOC-999"}
    svc.receipt_repo._pick_doc_number.return_value = "DOC-999"
    svc.receipt_repo._pick_txn_date.return_value = "2024-03-01"
    svc.receipt_repo._pick_due_date.return_value = "2024-03-31"
    svc.receipt_repo._pick_total_amount.return_value = 250.0
    svc.vendor_map_repo.get_by_vendor_id.return_value = {"id": 1, "external_vendor_id": "V-7", "sync_status": "SYNCED"}
    svc.coa_repo.get_external_id_for_account_id.return_value = {"id": 20, "external_id": "A-5"}
    svc.project_repo.get_current_mapping_by_project_id.return_value = {"id": 30, "external_project_id": "QPROJ-1", "sync_status": "SYNCED"}

    payload = svc._build_qbo_bill_payload(
        tenant_id="t1", service_id="6", realm_id="RID", qbo_service_master_id="6", receipt=receipt
    )

    assert payload["DueDate"] == "2024-03-31"

def test_build_payload_omits_doc_number_when_none():
    svc = make_service()
    svc._tenant_id = "t1"
    svc._service_id = "6"
    receipt = sample_receipt()
    receipt["doc_number"] = None
    receipt["invoice_number"] = None
    svc.receipt_repo.parse_additional_info.return_value = {"total": "100.0", "billDate": "2024-01-15", "billNo": None}
    svc.receipt_repo._pick_doc_number.return_value = None
    svc.receipt_repo._pick_txn_date.return_value = "2024-01-15"
    svc.receipt_repo._pick_due_date.return_value = None
    svc.receipt_repo._pick_total_amount.return_value = 100.0
    svc.vendor_map_repo.get_by_vendor_id.return_value = {"id": 1, "external_vendor_id": "V-7", "sync_status": "SYNCED"}
    svc.coa_repo.get_external_id_for_account_id.return_value = {"id": 20, "external_id": "A-5"}
    svc.project_repo.get_current_mapping_by_project_id.return_value = {"id": 30, "external_project_id": "QPROJ-1", "sync_status": "SYNCED"}

    payload = svc._build_qbo_bill_payload(
        tenant_id="t1", service_id="6", realm_id="RID", qbo_service_master_id="6", receipt=receipt
    )

    assert "DocNumber" not in payload


def test_build_payload_truncates_doc_number_to_21_chars():
    svc = make_service()
    svc._tenant_id = "t1"
    svc._service_id = "6"
    long_doc = "A" * 30
    receipt = sample_receipt(doc_number=long_doc)
    svc.receipt_repo.parse_additional_info.return_value = {"total": "100.0", "billDate": "2024-01-15", "billNo": long_doc}
    svc.receipt_repo._pick_doc_number.return_value = long_doc
    svc.receipt_repo._pick_txn_date.return_value = "2024-01-15"
    svc.receipt_repo._pick_due_date.return_value = None
    svc.receipt_repo._pick_total_amount.return_value = 100.0
    svc.vendor_map_repo.get_by_vendor_id.return_value = {"id": 1, "external_vendor_id": "V-7", "sync_status": "SYNCED"}
    svc.coa_repo.get_external_id_for_account_id.return_value = {"id": 20, "external_id": "A-5"}
    svc.project_repo.get_current_mapping_by_project_id.return_value = {"id": 30, "external_project_id": "QPROJ-1", "sync_status": "SYNCED"}

    payload = svc._build_qbo_bill_payload(
        tenant_id="t1", service_id="6", realm_id="RID", qbo_service_master_id="6", receipt=receipt
    )

    assert len(payload["DocNumber"]) == 21


def test_build_payload_raises_when_vendor_id_none():
    svc = make_service()
    svc._tenant_id = "t1"
    svc._service_id = "6"
    receipt = sample_receipt(vendor_id=None)
    svc.receipt_repo.parse_additional_info.return_value = {}
    with pytest.raises(Exception, match="(?i)vendor id"):
        svc._build_qbo_bill_payload(
            tenant_id="t1", service_id="6", realm_id="RID", qbo_service_master_id="6", receipt=receipt
        )


def test_build_payload_raises_when_no_qbo_vendor_id():
    svc = make_service()
    svc._tenant_id = "t1"
    svc._service_id = "6"
    receipt = sample_receipt()
    svc.receipt_repo.parse_additional_info.return_value = {}
    svc.vendor_map_repo.get_by_vendor_id.return_value = {"id": 1, "external_vendor_id": None, "sync_status": "SYNCED"}
    with pytest.raises(Exception, match="(?i)no quickbooks mapping"):
        svc._build_qbo_bill_payload(
            tenant_id="t1", service_id="6", realm_id="RID", qbo_service_master_id="6", receipt=receipt
        )


def test_build_payload_raises_when_chart_of_account_id_none():
    svc = make_service()
    svc._tenant_id = "t1"
    svc._service_id = "6"
    receipt = sample_receipt(coa_id=None)
    svc.receipt_repo.parse_additional_info.return_value = {}
    svc.vendor_map_repo.get_by_vendor_id.return_value = {"id": 1, "external_vendor_id": "V-1", "sync_status": "SYNCED"}
    with pytest.raises(Exception, match="chart of accounts"):
        svc._build_qbo_bill_payload(
            tenant_id="t1", service_id="6", realm_id="RID", qbo_service_master_id="6", receipt=receipt
        )


# ---------------------------------------------------------------------------
# find_bill_candidates_in_qbo
# ---------------------------------------------------------------------------

def test_find_bill_candidates_returns_empty_when_no_doc_number():
    svc = make_service()
    receipt = sample_receipt(doc_number=None)
    receipt["invoice_number"] = None
    receipt["doc_number"] = None
    svc.receipt_repo.parse_additional_info.return_value = {}
    svc.receipt_repo._pick_doc_number.return_value = None
    result = svc.find_bill_candidates_in_qbo(
        access_token="TOKEN", base_url="https://sandbox-qbo", realm_id="RID", receipt=receipt
    )
    assert result == []


# ---------------------------------------------------------------------------
# Helper / error classifier unit tests
# ---------------------------------------------------------------------------

def test_is_qbo_stale_reference_error():
    svc = make_service()
    assert svc._is_qbo_stale_reference_error("object not found [610]") is True
    assert svc._is_qbo_stale_reference_error("Object Made Inactive") is True
    assert svc._is_qbo_stale_reference_error("[610] something") is True
    assert svc._is_qbo_stale_reference_error("some other error") is False


def test_is_qbo_deleted_ref_error():
    svc = make_service()
    assert svc._is_qbo_deleted_ref_error("Vendor has been deleted") is True
    assert svc._is_qbo_deleted_ref_error("Invalid reference id") is True
    assert svc._is_qbo_deleted_ref_error("duplicate name") is False


def test_is_qbo_sync_token_error():
    svc = make_service()
    assert svc._is_qbo_sync_token_error("SyncToken mismatch [5010]") is True
    assert svc._is_qbo_sync_token_error("Stale Object error") is True
    assert svc._is_qbo_sync_token_error("[5010] error") is True
    assert svc._is_qbo_sync_token_error("unrelated error") is False


def test_build_reason_without_tid():
    result = QuickBooksBillExportService._build_reason("test message")
    data = json.loads(result)
    assert data["message"] == "test message"
    assert "intuit_tid" not in data


def test_build_reason_with_tid():
    result = QuickBooksBillExportService._build_reason("err", intuit_tid="TID-X")
    data = json.loads(result)
    assert data["intuit_tid"] == "TID-X"


def test_clean_strips_whitespace():
    svc = make_service()
    assert svc._clean("  hello  ") == "hello"
    assert svc._clean(None) == ""
    assert svc._clean(123) == "123"


def test_parse_timestamp_returns_utc_datetime():
    svc = make_service()
    dt = svc._parse_timestamp("2024-01-15T10:00:00+00:00")
    assert isinstance(dt, datetime)
    assert dt.tzinfo is not None


def test_parse_timestamp_returns_none_for_empty():
    svc = make_service()
    assert svc._parse_timestamp(None) is None
    assert svc._parse_timestamp("") is None


def test_get_vendor_label_uses_display_name():
    svc = make_service()
    svc.vendor_repo.get_current_vendor_by_id.return_value = {
        "display_name": "Test Vendor",
        "company_name": "Test Co",
    }
    label = svc._get_vendor_label(tenant_id="t1", vendor_id=5)
    assert label == "Test Vendor"


def test_get_vendor_label_falls_back_to_id():
    svc = make_service()
    svc.vendor_repo.get_current_vendor_by_id.return_value = {}
    label = svc._get_vendor_label(tenant_id="t1", vendor_id=42)
    assert "42" in label


def test_escape_qbo_literal():
    svc = make_service()
    assert svc._escape_qbo_literal("O'Brien") == "O\\'Brien"
    assert svc._escape_qbo_literal("C:\\path") == "C:\\\\path"
    assert svc._escape_qbo_literal("normal") == "normal"


def test_build_payload_links_bill_to_synced_project_sub_customer():
    svc = make_service()
    svc._tenant_id = "t1"
    svc._service_id = "6"
    receipt = sample_receipt(project_id=30)
    svc.receipt_repo.parse_additional_info.return_value = {"total": "100.0", "billDate": "2024-01-15", "billNo": "INV-001"}
    svc.receipt_repo._pick_doc_number.return_value = "INV-001"
    svc.receipt_repo._pick_txn_date.return_value = "2024-01-15"
    svc.receipt_repo._pick_due_date.return_value = None
    svc.receipt_repo._pick_total_amount.return_value = 100.0
    svc.vendor_map_repo.get_by_vendor_id.return_value = {"id": 1, "external_vendor_id": "V-7", "sync_status": "SYNCED"}
    svc.coa_repo.get_external_id_for_account_id.return_value = {"id": 20, "external_id": "A-5"}
    svc.project_repo.get_current_mapping_by_project_id.return_value = {"id": 30, "external_project_id": "QPROJ-1", "sync_status": "SYNCED"}

    payload = svc._build_qbo_bill_payload(
        tenant_id="t1", service_id="6", realm_id="RID", qbo_service_master_id="6", receipt=receipt
    )

    line_detail = payload["Line"][0]["AccountBasedExpenseLineDetail"]
    assert line_detail["CustomerRef"] == {"value": "QPROJ-1"}
    assert line_detail["BillableStatus"] == "Billable"




def test_build_payload_uses_project_ref_for_real_qbo_project():
    svc = make_service()
    svc._tenant_id = "t1"
    svc._service_id = "6"
    receipt = sample_receipt(project_id=30)
    svc.receipt_repo.parse_additional_info.return_value = {"total": "100.0", "billDate": "2024-01-15", "billNo": "INV-001"}
    svc.receipt_repo._pick_doc_number.return_value = "INV-001"
    svc.receipt_repo._pick_txn_date.return_value = "2024-01-15"
    svc.receipt_repo._pick_due_date.return_value = None
    svc.receipt_repo._pick_total_amount.return_value = 100.0
    svc.vendor_map_repo.get_by_vendor_id.return_value = {"id": 1, "external_vendor_id": "V-7", "sync_status": "SYNCED"}
    svc.coa_repo.get_external_id_for_account_id.return_value = {"id": 20, "external_id": "A-5"}
    svc.project_repo.get_current_mapping_by_project_id.return_value = {"id": 30, "external_project_id": "QBO-PROJECT-1", "sync_status": "SYNCED"}
    svc.project_repo.get_mapping_sync_target.return_value = const.PROJECT_SYNC_TARGET_QBO_PROJECT

    payload = svc._build_qbo_bill_payload(
        tenant_id="t1", service_id="6", realm_id="RID", qbo_service_master_id="6", receipt=receipt
    )

    line_detail = payload["Line"][0]["AccountBasedExpenseLineDetail"]
    assert payload["Line"][0]["ProjectRef"] == {"value": "QBO-PROJECT-1"}
    assert "CustomerRef" not in line_detail
    assert line_detail["BillableStatus"] == "Billable"
    svc.capability_service.projects_available.assert_called_once_with("t1", "6", refresh=True)



def test_build_payload_caches_projects_available_for_qbo_project_batch():
    svc = make_service()
    receipt_one = sample_receipt(receipt_id=1, project_id=30)
    receipt_two = sample_receipt(receipt_id=2, project_id=31)
    svc.receipt_repo.parse_additional_info.return_value = {"total": "100.0", "billDate": "2024-01-15", "billNo": "INV-001"}
    svc.receipt_repo._pick_doc_number.return_value = "INV-001"
    svc.receipt_repo._pick_txn_date.return_value = "2024-01-15"
    svc.receipt_repo._pick_due_date.return_value = None
    svc.receipt_repo._pick_total_amount.return_value = 100.0
    svc.vendor_map_repo.get_by_vendor_id.return_value = {"id": 1, "external_vendor_id": "V-7", "sync_status": "SYNCED"}
    svc.coa_repo.get_external_id_for_account_id.return_value = {"id": 20, "external_id": "A-5"}
    svc.project_repo.get_current_mapping_by_project_id.side_effect = [
        {"id": 30, "external_project_id": "QBO-PROJECT-1", "sync_status": "SYNCED"},
        {"id": 31, "external_project_id": "QBO-PROJECT-2", "sync_status": "SYNCED"},
    ]
    svc.project_repo.get_mapping_sync_target.return_value = const.PROJECT_SYNC_TARGET_QBO_PROJECT

    svc._build_qbo_bill_payload(
        tenant_id="t1", service_id="6", realm_id="RID", qbo_service_master_id="6", receipt=receipt_one
    )
    svc._build_qbo_bill_payload(
        tenant_id="t1", service_id="6", realm_id="RID", qbo_service_master_id="6", receipt=receipt_two
    )

    svc.capability_service.projects_available.assert_called_once_with("t1", "6", refresh=True)


def test_build_payload_blocks_qbo_project_bill_when_projects_disabled():
    svc = make_service()
    receipt = sample_receipt(project_id=30)
    svc.receipt_repo.parse_additional_info.return_value = {"total": "100.0", "billDate": "2024-01-15", "billNo": "INV-001"}
    svc.receipt_repo._pick_doc_number.return_value = "INV-001"
    svc.receipt_repo._pick_txn_date.return_value = "2024-01-15"
    svc.receipt_repo._pick_due_date.return_value = None
    svc.receipt_repo._pick_total_amount.return_value = 100.0
    svc.vendor_map_repo.get_by_vendor_id.return_value = {"id": 1, "external_vendor_id": "V-7", "sync_status": "SYNCED"}
    svc.coa_repo.get_external_id_for_account_id.return_value = {"id": 20, "external_id": "A-5"}
    svc.project_repo.get_current_mapping_by_project_id.return_value = {"id": 30, "external_project_id": "QBO-PROJECT-1", "sync_status": "SYNCED"}
    svc.project_repo.get_mapping_sync_target.return_value = const.PROJECT_SYNC_TARGET_QBO_PROJECT
    svc.capability_service.projects_available.return_value = False

    with pytest.raises(Exception, match="Projects are not enabled"):
        svc._build_qbo_bill_payload(
            tenant_id="t1", service_id="6", realm_id="RID", qbo_service_master_id="6", receipt=receipt
        )


def test_build_payload_fails_when_project_not_synced():
    svc = make_service()
    svc._tenant_id = "t1"
    svc._service_id = "6"
    receipt = sample_receipt(project_id=30)
    svc.receipt_repo.parse_additional_info.return_value = {"total": "100.0", "billDate": "2024-01-15", "billNo": "INV-001"}
    svc.vendor_map_repo.get_by_vendor_id.return_value = {"id": 1, "external_vendor_id": "V-7", "sync_status": "SYNCED"}
    svc.coa_repo.get_external_id_for_account_id.return_value = {"id": 20, "external_id": "A-5"}
    svc.project_repo.get_current_mapping_by_project_id.return_value = None
    svc.project_repo.get_project_by_id.return_value = {"id": 30, "description": "Project A"}
    svc.project_repo.get_mapping_sync_target.return_value = const.PROJECT_SYNC_TARGET_SUB_CUSTOMER

    with pytest.raises(Exception, match="project sync"):
        svc._build_qbo_bill_payload(
            tenant_id="t1", service_id="6", realm_id="RID", qbo_service_master_id="6", receipt=receipt
        )


def test_build_payload_allows_project_sync_failed_when_external_project_id_exists():
    svc = make_service()
    svc._tenant_id = "t1"
    svc._service_id = "6"
    receipt = sample_receipt(project_id=30)
    svc.receipt_repo.parse_additional_info.return_value = {"total": "100.0", "billDate": "2024-01-15", "billNo": "INV-001"}
    svc.receipt_repo._pick_doc_number.return_value = "INV-001"
    svc.receipt_repo._pick_txn_date.return_value = "2024-01-15"
    svc.receipt_repo._pick_due_date.return_value = None
    svc.receipt_repo._pick_total_amount.return_value = 100.0
    svc.vendor_map_repo.get_by_vendor_id.return_value = {"id": 1, "external_vendor_id": "V-7", "sync_status": "SYNCED"}
    svc.coa_repo.get_external_id_for_account_id.return_value = {"id": 20, "external_id": "A-5"}
    svc.project_repo.get_current_mapping_by_project_id.return_value = {
        "id": 30,
        "external_project_id": "QPROJ-1",
        "sync_status": "SYNC_FAILED",
    }

    payload = svc._build_qbo_bill_payload(
        tenant_id="t1", service_id="6", realm_id="RID", qbo_service_master_id="6", receipt=receipt
    )

    line_detail = payload["Line"][0]["AccountBasedExpenseLineDetail"]
    assert line_detail["CustomerRef"] == {"value": "QPROJ-1"}
    assert line_detail["BillableStatus"] == "Billable"


def test_build_payload_for_stock_bill_posts_to_inventory_asset_without_customer_or_project():
    svc = make_service()
    receipt = sample_receipt(project_id=None, doc_number="STOCK-001", txn_date="2026-07-05", amount=250.0)
    receipt["stock_reference"] = "PO-STOCK-001"
    # Simulate an old/wrong local COA assignment. Stock purchases must ignore this
    # and use the configured QuickBooks Inventory Asset account instead.
    receipt["chart_of_account_id"] = 835
    svc.receipt_repo.parse_additional_info.return_value = {
        "total": "250.0",
        "billDate": "2026-07-05",
        "billNo": "STOCK-001",
        "lineItems": [{"description": "Inventory shingle", "sku": "SH-01", "quantity": 5, "unitCost": 50}],
    }
    svc.receipt_repo._pick_doc_number.return_value = "STOCK-001"
    svc.receipt_repo._pick_txn_date.return_value = "2026-07-05"
    svc.receipt_repo._pick_due_date.return_value = None
    svc.receipt_repo._pick_total_amount.return_value = 250.0
    svc.vendor_map_repo.get_by_vendor_id.return_value = {"id": 1, "external_vendor_id": "V-7", "sync_status": "SYNCED"}
    svc.coa_repo.get_external_id_for_account_name.return_value = {"id": 923, "external_id": "81"}

    payload = svc._build_qbo_bill_payload(
        tenant_id="t1", service_id="6", realm_id="RID", qbo_service_master_id="6", receipt=receipt
    )

    assert payload["Line"][0]["Amount"] == 250.0
    assert "ProjectRef" not in payload["Line"][0]
    line_detail = payload["Line"][0]["AccountBasedExpenseLineDetail"]
    assert line_detail["AccountRef"] == {"value": "81"}
    assert "CustomerRef" not in line_detail
    assert "BillableStatus" not in line_detail
    svc.coa_repo.get_external_id_for_account_name.assert_called_once_with(
        tenant_id="t1",
        name="Inventory Asset",
        service_id="6",
        external_acc_id="RID",
        allowed_account_types=const.QBO_INVENTORY_ASSET_ACCOUNT_TYPES,
        allowed_detail_types=const.QBO_INVENTORY_ASSET_DETAIL_TYPES,
    )
    svc.coa_repo.get_external_id_for_account_id.assert_not_called()
    svc.project_repo.get_current_mapping_by_project_id.assert_not_called()


def test_build_payload_rejects_unclassified_bill_without_project_instead_of_treating_it_as_stock():
    svc = make_service()
    receipt = sample_receipt(project_id=None, doc_number="OVERHEAD-001", amount=85.0)
    receipt["name"] = "VendorBill"
    receipt["stock_reference"] = None
    svc.receipt_repo.parse_additional_info.return_value = {
        "total": "85.0",
        "billDate": "2024-01-15",
        "billNo": "OVERHEAD-001",
    }
    svc.receipt_repo._pick_total_amount.return_value = 85.0
    svc.receipt_repo._pick_txn_date.return_value = "2024-01-15"
    svc.receipt_repo._pick_due_date.return_value = None
    svc.receipt_repo._pick_doc_number.return_value = "OVERHEAD-001"
    svc.vendor_map_repo.get_by_vendor_id.return_value = {
        "id": 1,
        "external_vendor_id": "V-7",
        "sync_status": "SYNCED",
    }
    svc.coa_repo.get_external_id_for_account_id.return_value = {"id": 20, "external_id": "EXP-1"}

    with pytest.raises(ValueError, match="not marked as a stock bill"):
        svc._build_qbo_bill_payload(
            tenant_id="t1",
            service_id="6",
            realm_id="RID",
            qbo_service_master_id="6",
            receipt=receipt,
        )

    svc.coa_repo.get_external_id_for_account_name.assert_not_called()
    svc.project_repo.get_current_mapping_by_project_id.assert_not_called()


def test_extract_receipt_items_supports_new_line_items_shape():
    svc = make_service()
    info = {
        "lineItems": [
            {"description": "Stock board", "sku": "BD-01", "quantity": 2, "unitCost": 15.5},
        ]
    }

    items = svc._extract_receipt_items(receipt={}, info=info)

    assert len(items) == 1
    assert items[0]["description"] == "Stock board"


def test_build_receipt_item_description_includes_material_description_and_quantity():
    svc = make_service()

    description = svc._build_receipt_item_description({
        "materialName": "Roof Shingle",
        "description": "Black architectural bundle",
        "quantity": "3.0",
    })

    assert description == "Roof Shingle - Black architectural bundle x 3"


def test_build_payload_summarizes_materials_into_one_qbo_line():
    svc = make_service()
    receipt = sample_receipt(project_id=30, doc_number="BILL-1001", amount=275.0)
    svc.receipt_repo.parse_additional_info.return_value = {
        "total": "275.0",
        "billDate": "2026-08-10",
        "billNo": "BILL-1001",
        "lineItems": [
            {"materialName": "Roof Shingle", "description": "Black bundle", "quantity": 2, "unitCost": 75, "amount": 150},
            {"materialName": "Underlayment", "quantity": 5, "unitCost": 25, "amount": 125},
        ],
    }
    svc.receipt_repo._pick_doc_number.return_value = "BILL-1001"
    svc.receipt_repo._pick_txn_date.return_value = "2026-08-10"
    svc.receipt_repo._pick_due_date.return_value = None
    svc.receipt_repo._pick_total_amount.return_value = 275.0
    svc.vendor_map_repo.get_by_vendor_id.return_value = {"id": 1, "external_vendor_id": "V-7", "sync_status": "SYNCED"}
    svc.coa_repo.get_external_id_for_account_id.return_value = {"id": 20, "external_id": "A-5"}
    svc.project_repo.get_current_mapping_by_project_id.return_value = {
        "id": 30,
        "external_project_id": "QPROJ-1",
        "sync_status": "SYNCED",
    }

    payload = svc._build_qbo_bill_payload(
        tenant_id="t1", service_id="6", realm_id="RID", qbo_service_master_id="6", receipt=receipt
    )

    assert len(payload["Line"]) == 1
    line = payload["Line"][0]
    assert line["Amount"] == 275.0
    assert line["Description"] == "Roof Shingle - Black bundle x 2; Underlayment x 5"
    assert line["AccountBasedExpenseLineDetail"]["AccountRef"] == {"value": "A-5"}
    assert line["AccountBasedExpenseLineDetail"]["CustomerRef"] == {"value": "QPROJ-1"}


def test_build_receipt_items_summary_includes_all_materials():
    svc = make_service()
    info = {
        "lineItems": [
            {"materialName": f"Material {index}", "quantity": index}
            for index in range(1, 11)
        ]
    }

    description = svc._build_receipt_items_summary_description(receipt={}, info=info)

    assert "Material 1 x 1" in description
    assert "Material 10 x 10" in description
    assert "..." not in description


def test_build_payload_truncates_combined_material_description_to_qbo_limit():
    svc = make_service()
    receipt = sample_receipt(project_id=30, doc_number="BILL-LONG", amount=500.0)
    line_items = [
        {
            "materialName": f"Material {index:03d} " + ("X" * 40),
            "description": "Long material description",
            "quantity": index,
        }
        for index in range(1, 101)
    ]
    info = {
        "total": "500.0",
        "billDate": "2026-08-10",
        "billNo": "BILL-LONG",
        "lineItems": line_items,
    }
    svc.receipt_repo.parse_additional_info.return_value = info
    svc.receipt_repo._pick_doc_number.return_value = "BILL-LONG"
    svc.receipt_repo._pick_txn_date.return_value = "2026-08-10"
    svc.receipt_repo._pick_due_date.return_value = None
    svc.receipt_repo._pick_total_amount.return_value = 500.0
    svc.vendor_map_repo.get_by_vendor_id.return_value = {
        "id": 1,
        "external_vendor_id": "V-7",
        "sync_status": "SYNCED",
    }
    svc.coa_repo.get_external_id_for_account_id.return_value = {
        "id": 20,
        "external_id": "A-5",
    }
    svc.project_repo.get_current_mapping_by_project_id.return_value = {
        "id": 30,
        "external_project_id": "QPROJ-1",
        "sync_status": "SYNCED",
    }

    material_summary = svc._build_receipt_items_summary_description(receipt=receipt, info=info)
    assert len(material_summary) > 4000

    payload = svc._build_qbo_bill_payload(
        tenant_id="t1",
        service_id="6",
        realm_id="RID",
        qbo_service_master_id="6",
        receipt=receipt,
    )

    description = payload["Line"][0]["Description"]
    assert len(description) == 4000
    assert description == material_summary[:4000]


def test_build_payload_fallback_line_uses_material_description_when_item_amounts_missing():
    svc = make_service()
    receipt = sample_receipt(project_id=None, doc_number="STOCK-002", txn_date="2026-07-05", amount=120.0)
    receipt["stock_reference"] = "PO-STOCK-002"
    svc.receipt_repo.parse_additional_info.return_value = {
        "total": "120.0",
        "billDate": "2026-07-05",
        "billNo": "STOCK-002",
        "lineItems": [
            {"materialName": "Cement Bags", "description": "50 kg", "quantity": "2.0"},
            {"materialName": "Steel Rebars", "description": "10mm x 12mm", "quantity": "4"},
        ],
    }
    svc.receipt_repo._pick_doc_number.return_value = "STOCK-002"
    svc.receipt_repo._pick_txn_date.return_value = "2026-07-05"
    svc.receipt_repo._pick_due_date.return_value = None
    svc.receipt_repo._pick_total_amount.return_value = 120.0
    svc.vendor_map_repo.get_by_vendor_id.return_value = {"id": 1, "external_vendor_id": "V-7", "sync_status": "SYNCED"}
    svc.coa_repo.get_external_id_for_account_name.return_value = {"id": 923, "external_id": "81"}

    payload = svc._build_qbo_bill_payload(
        tenant_id="t1", service_id="6", realm_id="RID", qbo_service_master_id="6", receipt=receipt
    )

    assert payload["Line"][0]["Amount"] == 120.0
    assert payload["Line"][0]["Description"] == "Cement Bags - 50 kg x 2; Steel Rebars - 10mm x 12mm x 4"


def test_stock_debit_amount_uses_buying_total_instead_of_selling_total():
    svc = make_service()
    info = {
        "total": "2308.50",
        "buyingTotal": "5103.80",
        "receiptInfo": [
            {"description": "Ridge Cap Shingles", "amount": 4000, "sellAmount": 12.5},
            {"description": "Pipe", "amount": 37.8, "sellAmount": 1230},
            {"description": "RM TOTE 270 GALLON", "amount": 1066, "sellAmount": 1066},
        ],
    }

    assert svc._resolve_stock_debit_amount(receipt={}, info=info) == 5103.80


def test_stock_debit_amount_falls_back_to_sum_of_item_buying_amounts():
    svc = make_service()
    info = {
        "total": "2308.50",
        "receiptInfo": [
            {"description": "Ridge Cap Shingles", "amount": 4000, "sellAmount": 12.5},
            {"description": "Pipe", "amount": 37.8, "sellAmount": 1230},
            {"description": "RM TOTE 270 GALLON", "amount": 1066, "sellAmount": 1066},
        ],
    }

    assert svc._resolve_stock_debit_amount(receipt={}, info=info) == 5103.80


def test_stock_debit_amount_does_not_use_partial_item_cost_when_amount_is_missing():
    svc = make_service()
    info = {
        "total": "2308.50",
        "receiptInfo": [
            {"description": "Ridge Cap Shingles", "amount": 4000, "sellAmount": 12.5},
            {"description": "Pipe", "sellAmount": 1230, "unitCost": 3.78, "quantity": 10},
        ],
    }

    assert svc._resolve_stock_debit_amount(receipt={}, info=info) is None


def test_stock_credit_amount_uses_buying_total_instead_of_selling_total():
    svc = make_service()
    info = {
        "total": "246.0",
        "buyingTotal": "190.70",
        "receiptInfo": [
            {"description": "Pipe", "amount": 190.7, "sellAmount": 246},
        ],
    }

    assert svc._resolve_stock_credit_amount(receipt={}, info=info) == 190.70


def test_stock_credit_amount_falls_back_to_sum_of_item_buying_amounts():
    svc = make_service()
    info = {
        "total": "246.0",
        "receiptInfo": [
            {"description": "Pipe", "amount": 190.7, "sellAmount": 246},
            {"description": "Fitting", "amount": 55.3, "sellAmount": 80},
        ],
    }

    assert svc._resolve_stock_credit_amount(receipt={}, info=info) == 246.0


def test_stock_credit_amount_does_not_use_partial_item_cost_when_amount_is_missing():
    svc = make_service()
    info = {
        "total": "326.0",
        "receiptInfo": [
            {"description": "Pipe", "amount": 190.7, "sellAmount": 246},
            {"description": "Fitting", "sellAmount": 80, "unitCost": 27.65, "quantity": 2},
        ],
    }

    assert svc._resolve_stock_credit_amount(receipt={}, info=info) is None


def test_stock_credit_invalid_cost_error_names_stock_credit_bill():
    svc = make_service()
    receipt = sample_receipt(
        receipt_id=145,
        vendor_id=None,
        coa_id=3,
        project_id=30,
        doc_number="SCB-003",
        txn_date="2026-09-16",
        amount=0.0,
    )
    receipt["name"] = "StockCreditBill"
    svc.receipt_repo.parse_additional_info.return_value = {
        "billDate": "2026-09-16T00:00Z",
        "billNo": "SCB-003",
        "receiptInfo": [{
            "description": "Pipe",
            "quantity": 2,
            "unitCost": 95.35,
        }],
    }
    svc.receipt_repo._pick_doc_number.return_value = "SCB-003"
    svc.receipt_repo._pick_txn_date.return_value = "2026-09-16"
    svc.coa_repo.get_external_id_for_account_id.return_value = {
        "id": 3,
        "external_id": "COGS-QBO-1",
    }
    svc.coa_repo.get_external_id_for_account_name.return_value = {
        "id": 642,
        "external_id": "INV-QBO-1",
    }
    svc.project_repo.get_current_mapping_by_project_id.return_value = {
        "id": 30,
        "external_project_id": "QPROJ-1",
        "sync_status": "SYNCED",
    }

    with pytest.raises(Exception, match="StockCreditBill has no valid cost amount"):
        svc._build_stock_credit_journal_entry_payload(
            tenant_id="t1",
            service_id="6",
            realm_id="RID",
            qbo_service_master_id="6",
            receipt=receipt,
        )


def test_build_stock_debit_journal_entry_payload_uses_debit_coa_and_inventory_credit():
    svc = make_service()
    receipt = sample_receipt(
        receipt_id=141,
        vendor_id=None,
        coa_id=3,
        project_id=30,
        doc_number="SDB-001",
        txn_date="2026-07-09",
        amount=1725.0,
    )
    receipt["name"] = "StockDebitBill"
    receipt["receipt_additional_info"] = json.dumps([{
        "total": "1725.0",
        "billDate": "2026-07-09T00:00Z",
        "billNo": "SDB-001",
        "receiptInfo": [{
            "code": "RMAXX270GAL",
            "unit": "Gallon",
            "amount": 1725,
            "quantity": 5,
            "unitCost": 345,
            "description": "RM TOTE 270 GALLON",
        }],
    }])
    svc.receipt_repo.parse_additional_info.return_value = {
        "total": "1725.0",
        "billDate": "2026-07-09T00:00Z",
        "billNo": "SDB-001",
        "receiptInfo": [{
            "code": "RMAXX270GAL",
            "unit": "Gallon",
            "amount": 1725,
            "quantity": 5,
            "unitCost": 345,
            "description": "RM TOTE 270 GALLON",
        }],
    }
    svc.receipt_repo._pick_doc_number.return_value = "SDB-001"
    svc.receipt_repo._pick_txn_date.return_value = "2026-07-09"
    svc.receipt_repo._pick_total_amount.return_value = 1725.0
    svc.coa_repo.get_external_id_for_account_id.return_value = {"id": 3, "external_id": "COGS-QBO-1"}
    svc.coa_repo.get_external_id_for_account_name.return_value = {"id": 642, "external_id": "INV-QBO-1"}
    svc.project_repo.get_current_mapping_by_project_id.return_value = {
        "id": 30,
        "external_project_id": "QPROJ-1",
        "sync_status": "SYNCED",
    }

    payload = svc._build_stock_debit_journal_entry_payload(
        tenant_id="t1",
        service_id="6",
        realm_id="RID",
        qbo_service_master_id="6",
        receipt=receipt,
    )

    assert "VendorRef" not in payload
    assert payload["TxnDate"] == "2026-07-09"
    assert payload["DocNumber"] == "SDB-001"
    assert len(payload["Line"]) == 2

    debit_line = payload["Line"][0]
    debit_detail = debit_line["JournalEntryLineDetail"]
    assert debit_line["Amount"] == 1725.0
    assert debit_detail["PostingType"] == "Debit"
    assert debit_detail["AccountRef"] == {"value": "COGS-QBO-1"}
    assert debit_detail["Entity"] == {"Type": "Customer", "EntityRef": {"value": "QPROJ-1"}}

    credit_line = payload["Line"][1]
    credit_detail = credit_line["JournalEntryLineDetail"]
    assert credit_line["Amount"] == 1725.0
    assert credit_detail["PostingType"] == "Credit"
    assert credit_detail["AccountRef"] == {"value": "INV-QBO-1"}
    assert "Entity" not in credit_detail

    svc.coa_repo.get_external_id_for_account_name.assert_called_once_with(
        tenant_id="t1",
        name="Inventory Asset",
        service_id="6",
        external_acc_id="RID",
        allowed_account_types=const.QBO_INVENTORY_ASSET_ACCOUNT_TYPES,
        allowed_detail_types=const.QBO_INVENTORY_ASSET_DETAIL_TYPES,
    )


def test_build_stock_debit_journal_entry_payload_handles_real_qbo_project():
    svc = make_service()
    receipt = sample_receipt(
        receipt_id=142,
        vendor_id=None,
        coa_id=3,
        project_id=30,
        doc_number="SDB-002",
        txn_date="2026-07-09",
        amount=1725.0,
    )
    receipt["name"] = "StockDebitBill"
    svc.receipt_repo.parse_additional_info.return_value = {
        "total": "1725.0",
        "billDate": "2026-07-09T00:00Z",
        "billNo": "SDB-002",
        "receiptInfo": [{"description": "RM TOTE 270 GALLON", "quantity": 5, "unitCost": 345, "amount": 1725}],
    }
    svc.receipt_repo._pick_doc_number.return_value = "SDB-002"
    svc.receipt_repo._pick_txn_date.return_value = "2026-07-09"
    svc.receipt_repo._pick_total_amount.return_value = 1725.0
    svc.coa_repo.get_external_id_for_account_id.return_value = {"id": 3, "external_id": "COGS-QBO-1"}
    svc.coa_repo.get_external_id_for_account_name.return_value = {"id": 642, "external_id": "INV-QBO-1"}
    svc.project_repo.get_current_mapping_by_project_id.return_value = {
        "id": 30,
        "external_project_id": "QBO-PROJECT-56177",
        "sync_status": "SYNCED",
        const.KEY_PROJECT_SYNC_TARGET: const.PROJECT_SYNC_TARGET_QBO_PROJECT,
    }
    svc.project_repo.get_mapping_sync_target.return_value = const.PROJECT_SYNC_TARGET_QBO_PROJECT
    svc.project_repo.get_project_by_id.return_value = {"id": 30, "customer_uid": "customer-uid-1"}
    svc.customer_repo.get_latest_qbo_linked_mapping_for_customer_uuid.return_value = {
        "external_customer_id": "QBO-CUSTOMER-125",
        "sync_status": "SYNCED",
    }

    payload = svc._build_stock_debit_journal_entry_payload(
        tenant_id="t1",
        service_id="6",
        realm_id="RID",
        qbo_service_master_id="6",
        receipt=receipt,
    )

    debit_line = payload["Line"][0]
    debit_detail = debit_line["JournalEntryLineDetail"]
    assert debit_detail["Entity"] == {"Type": "Customer", "EntityRef": {"value": "QBO-CUSTOMER-125"}}
    assert debit_detail["BillableStatus"] == "NotBillable"
    assert debit_line["ProjectRef"] == {"value": "QBO-PROJECT-56177"}
    assert "ProjectRef" not in payload["Line"][1]
    assert "Entity" not in payload["Line"][1]["JournalEntryLineDetail"]


def test_export_bills_routes_stock_debit_bill_to_journal_entry_create():
    svc = make_service()
    receipt = sample_receipt(
        receipt_id=141,
        vendor_id=None,
        coa_id=3,
        project_id=30,
        doc_number="SDB-001",
        txn_date="2026-07-09",
        amount=1725.0,
    )
    receipt["name"] = "StockDebitBill"
    mapping = sample_mapping(mapping_id=77, external_receipt_id=None, sync_status="PENDING_PUSH")
    _setup_export(svc, [sample_candidate(receipt=receipt, mapping=mapping)])
    svc.receipt_repo.parse_additional_info.return_value = {
        "total": "1725.0",
        "billDate": "2026-07-09T00:00Z",
        "billNo": "SDB-001",
        "receiptInfo": [{"description": "RM TOTE 270 GALLON", "quantity": 5, "unitCost": 345, "amount": 1725}],
    }
    svc.receipt_repo._pick_doc_number.return_value = "SDB-001"
    svc.receipt_repo._pick_txn_date.return_value = "2026-07-09"
    svc.receipt_repo._pick_total_amount.return_value = 1725.0
    svc.coa_repo.get_external_id_for_account_id.return_value = {"id": 3, "external_id": "COGS-QBO-1"}
    svc.coa_repo.get_external_id_for_account_name.return_value = {"id": 642, "external_id": "INV-QBO-1"}
    svc.project_repo.get_current_mapping_by_project_id.return_value = {
        "id": 30,
        "external_project_id": "QPROJ-1",
        "sync_status": "SYNCED",
    }
    svc.find_journal_entry_candidates_in_qbo = MagicMock(return_value=[])
    svc.create_journal_entry_in_qbo = MagicMock(return_value={const.QBO_KEY_ID: "JE-1", const.QBO_KEY_SYNC_TOKEN: "0"})
    svc.find_bill_candidates_in_qbo = MagicMock(return_value=[])
    svc.create_bill_in_qbo = MagicMock()

    result = svc.export_bills(tenant_id="t1", service_id="6")

    assert result[const.KEY_OK] is True
    assert result["createdInQbo"] == 1
    assert result["createdJournalEntriesInQbo"] == 1
    assert result["failed"] == 0
    svc.create_journal_entry_in_qbo.assert_called_once()
    svc.create_bill_in_qbo.assert_not_called()
    svc.find_bill_candidates_in_qbo.assert_not_called()
    svc.receipt_map_repo.mark_mapping_synced.assert_called_once()
    synced_kwargs = svc.receipt_map_repo.mark_mapping_synced.call_args.kwargs
    assert synced_kwargs["mapping_id"] == 77
    assert synced_kwargs["external_receipt_id"] == "JE-1"


def test_build_stock_debit_journal_entry_payload_requires_inventory_asset_mapping():
    svc = make_service()
    receipt = sample_receipt(vendor_id=None, coa_id=3, project_id=30, doc_number="SDB-002", amount=100.0)
    receipt["name"] = "StockDebitBill"
    svc.receipt_repo.parse_additional_info.return_value = {"total": "100.0", "billDate": "2026-07-09", "billNo": "SDB-002"}
    svc.receipt_repo._pick_total_amount.return_value = 100.0
    svc.receipt_repo._pick_txn_date.return_value = "2026-07-09"
    svc.receipt_repo._pick_doc_number.return_value = "SDB-002"
    svc.coa_repo.get_external_id_for_account_id.return_value = {"id": 3, "external_id": "COGS-QBO-1"}
    svc.coa_repo.get_external_id_for_account_name.return_value = None

    with pytest.raises(Exception, match="Inventory Asset"):
        svc._build_stock_debit_journal_entry_payload(
            tenant_id="t1",
            service_id="6",
            realm_id="RID",
            qbo_service_master_id="6",
            receipt=receipt,
        )


def test_empty_reviewed_receipt_info_does_not_fall_back_to_stale_ai_items():
    svc = make_service()
    receipt = {
        "ai_result": json.dumps({
            "items": [
                {"description": "Old AI material", "quantity": 100, "price": 25, "totalPrice": 2500}
            ]
        })
    }
    info = {"receiptInfo": "[]"}

    assert svc._extract_receipt_items(receipt=receipt, info=info) == []
    assert svc._extract_receipt_item_candidates(receipt=receipt, info=info) == []


def test_managed_line_metadata_tracks_qbo_line_and_fieldcraft_item_count():
    svc = make_service()
    receipt = sample_receipt(project_id=30, doc_number="BILL-TRACK", amount=500.0)
    receipt["receipt_additional_info"] = json.dumps([{
        "total": "500",
        "receiptInfo": json.dumps([
            {"description": "Material A", "quantity": 2, "price": 100, "totalPrice": 200},
            {"description": "Material B", "quantity": 3, "price": 100, "totalPrice": 300},
        ]),
    }])
    svc.receipt_repo.parse_additional_info.side_effect = lambda raw: json.loads(raw)[0]
    payload = {
        "Line": [{
            "Amount": 500,
            "Description": "Material A x 2; Material B x 3",
            "DetailType": "AccountBasedExpenseLineDetail",
            "AccountBasedExpenseLineDetail": {
                "AccountRef": {"value": "A-5"},
                "CustomerRef": {"value": "QPROJ-1"},
                "BillableStatus": "Billable",
            },
        }]
    }
    qbo_bill = {
        "Line": [{
            "Id": "17",
            "Amount": 500,
            "Description": "Material A x 2; Material B x 3",
            "DetailType": "AccountBasedExpenseLineDetail",
            "AccountBasedExpenseLineDetail": {
                "AccountRef": {"value": "A-5"},
                "CustomerRef": {"value": "QPROJ-1"},
                "BillableStatus": "Billable",
            },
        }]
    }

    metadata = svc._build_managed_line_metadata(qbo_bill=qbo_bill, payload=payload, receipt=receipt)

    assert metadata["id"] == "17"
    assert metadata["amount"] == 500.0
    assert metadata["itemCount"] == 2
    assert metadata["descriptionFingerprint"]
    assert metadata["lineBaseline"] == {
        "detailType": "AccountBasedExpenseLineDetail",
        "accountRef": "A-5",
        "customerRef": "QPROJ-1",
        "billableStatus": "Billable",
    }


def test_build_stock_credit_journal_entry_payload_reverses_stock_debit_entry():
    svc = make_service()
    receipt = sample_receipt(
        receipt_id=143,
        vendor_id=None,
        coa_id=3,
        project_id=30,
        doc_number="SCB-001",
        txn_date="2026-09-16",
        amount=690.0,
    )
    receipt["name"] = "StockCreditBill"
    svc.receipt_repo.parse_additional_info.return_value = {
        "total": "690.0",
        "buyingTotal": "500.0",
        "billDate": "2026-09-16T00:00Z",
        "billNo": "SCB-001",
        "receiptInfo": [{
            "description": "RM TOTE 270 GALLON",
            "quantity": 2,
            "unitCost": 345,
            "amount": 690,
        }],
    }
    svc.receipt_repo._pick_doc_number.return_value = "SCB-001"
    svc.receipt_repo._pick_txn_date.return_value = "2026-09-16"
    svc.receipt_repo._pick_total_amount.return_value = 690.0
    svc.coa_repo.get_external_id_for_account_id.return_value = {
        "id": 3,
        "external_id": "COGS-QBO-1",
    }
    svc.coa_repo.get_external_id_for_account_name.return_value = {
        "id": 642,
        "external_id": "INV-QBO-1",
    }
    svc.project_repo.get_current_mapping_by_project_id.return_value = {
        "id": 30,
        "external_project_id": "QPROJ-1",
        "sync_status": "SYNCED",
    }

    payload = svc._build_stock_credit_journal_entry_payload(
        tenant_id="t1",
        service_id="6",
        realm_id="RID",
        qbo_service_master_id="6",
        receipt=receipt,
    )

    inventory_line = payload["Line"][0]
    inventory_detail = inventory_line["JournalEntryLineDetail"]
    assert inventory_line["Amount"] == 500.0
    assert inventory_detail["PostingType"] == "Debit"
    assert inventory_detail["AccountRef"] == {"value": "INV-QBO-1"}
    assert "Entity" not in inventory_detail

    account_line = payload["Line"][1]
    account_detail = account_line["JournalEntryLineDetail"]
    assert account_line["Amount"] == 500.0
    assert account_detail["PostingType"] == "Credit"
    assert account_detail["AccountRef"] == {"value": "COGS-QBO-1"}
    assert account_detail["Entity"] == {
        "Type": "Customer",
        "EntityRef": {"value": "QPROJ-1"},
    }
    assert payload["DocNumber"].startswith("SCB-")


def test_build_stock_credit_journal_entry_payload_uses_credit_bill_fallback_description():
    svc = make_service()
    receipt = sample_receipt(
        receipt_id=145,
        vendor_id=None,
        coa_id=3,
        project_id=30,
        doc_number=None,
        txn_date="2026-09-16",
        amount=200.0,
    )
    receipt["name"] = "StockCreditBill"
    receipt["description"] = None
    svc.receipt_repo.parse_additional_info.return_value = {
        "buyingTotal": "200.0",
        "billDate": "2026-09-16T00:00Z",
        "receiptInfo": [],
    }
    svc.receipt_repo._pick_doc_number.return_value = None
    svc.receipt_repo._pick_txn_date.return_value = "2026-09-16"
    svc.coa_repo.get_external_id_for_account_id.return_value = {
        "id": 3,
        "external_id": "COGS-QBO-1",
    }
    svc.coa_repo.get_external_id_for_account_name.return_value = {
        "id": 642,
        "external_id": "INV-QBO-1",
    }
    svc.project_repo.get_current_mapping_by_project_id.return_value = {
        "id": 30,
        "external_project_id": "QPROJ-1",
        "sync_status": "SYNCED",
    }

    payload = svc._build_stock_credit_journal_entry_payload(
        tenant_id="t1",
        service_id="6",
        realm_id="RID",
        qbo_service_master_id="6",
        receipt=receipt,
    )

    account_line = payload["Line"][1]
    assert account_line["Description"] == "StockCreditBill"
    assert "StockDebitBill" not in account_line["Description"]
    assert payload["DocNumber"] == "SCB-145"


def test_export_bills_routes_stock_credit_bill_to_journal_entry():
    svc = make_service()
    receipt = sample_receipt(
        receipt_id=144,
        vendor_id=None,
        coa_id=3,
        project_id=30,
        doc_number="SCB-002",
        txn_date="2026-09-16",
        amount=690.0,
    )
    receipt["name"] = "StockCreditBill"
    mapping = sample_mapping(
        mapping_id=78,
        external_receipt_id=None,
        sync_status="PENDING_PUSH",
    )
    _setup_export(svc, [sample_candidate(receipt=receipt, mapping=mapping)])
    svc.receipt_repo.parse_additional_info.return_value = {
        "total": "690.0",
        "billDate": "2026-09-16T00:00Z",
        "billNo": "SCB-002",
        "receiptInfo": [{
            "description": "RM TOTE 270 GALLON",
            "quantity": 2,
            "unitCost": 345,
            "amount": 690,
        }],
    }
    svc.receipt_repo._pick_doc_number.return_value = "SCB-002"
    svc.receipt_repo._pick_txn_date.return_value = "2026-09-16"
    svc.receipt_repo._pick_total_amount.return_value = 690.0
    svc.coa_repo.get_external_id_for_account_id.return_value = {
        "id": 3,
        "external_id": "COGS-QBO-1",
    }
    svc.coa_repo.get_external_id_for_account_name.return_value = {
        "id": 642,
        "external_id": "INV-QBO-1",
    }
    svc.project_repo.get_current_mapping_by_project_id.return_value = {
        "id": 30,
        "external_project_id": "QPROJ-1",
        "sync_status": "SYNCED",
    }
    svc.find_journal_entry_candidates_in_qbo = MagicMock(return_value=[])
    svc.create_journal_entry_in_qbo = MagicMock(
        return_value={const.QBO_KEY_ID: "JE-CREDIT-1", const.QBO_KEY_SYNC_TOKEN: "0"}
    )
    svc.find_bill_candidates_in_qbo = MagicMock(return_value=[])
    svc.create_bill_in_qbo = MagicMock()

    result = svc.export_bills(tenant_id="t1", service_id="6")

    assert result[const.KEY_OK] is True
    assert result["createdJournalEntriesInQbo"] == 1
    assert result["failed"] == 0
    svc.create_journal_entry_in_qbo.assert_called_once()
    svc.create_bill_in_qbo.assert_not_called()
    svc.find_bill_candidates_in_qbo.assert_not_called()
    synced = svc.receipt_map_repo.mark_mapping_synced.call_args.kwargs
    assert synced["mapping_id"] == 78
    assert synced["external_receipt_id"] == "JE-CREDIT-1"
