from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from source.Utils import constants as const
from source.Utils.webhook import _process_qbo_webhook
from source.quickbooks.QuickBooksBillCreateService import QuickBooksBillCreateService


def _make_service():
    svc = QuickBooksBillCreateService()
    svc.qbo_repo = MagicMock()
    svc.qbo_service = MagicMock()
    svc.receipt_repo = MagicMock()
    svc.receipt_map_repo = MagicMock()
    svc.vendor_map_repo = MagicMock()
    svc.bill_exporter = MagicMock()
    return svc


def test_create_bill_validation_tenant_id_required():
    svc = _make_service()

    out = svc.create_bill(
        tenant_id="",
        service_id="6",
        receipt_external_map_id=101,
        vendor_external_map_id=202,
    )

    assert out["ok"] is False
    assert "tenant_id is required" in out["message"]


def test_create_bill_receipt_mapping_not_found():
    svc = _make_service()
    svc.receipt_map_repo.get_by_mapping_id.return_value = None

    out = svc.create_bill(
        tenant_id="tn1",
        service_id="6",
        receipt_external_map_id=101,
        vendor_external_map_id=202,
    )

    assert out["ok"] is False
    assert "Receipt external map not found" in out["message"]


def test_create_bill_vendor_mapping_mismatch():
    svc = _make_service()
    svc.receipt_map_repo.get_by_mapping_id.return_value = {
        "id": 101,
        "receipt_id": 501,
        "service_id": 6,
        "sync_status": "PENDING_PUSH",
    }
    svc.receipt_repo.get_current_receipt_by_id.return_value = {
        "id": 501,
        "vendor_id": 20,
    }
    svc.vendor_map_repo.get_by_mapping_id.return_value = {
        "id": 202,
        "service_id": 6,
        "vendor_id": 99,
    }

    out = svc.create_bill(
        tenant_id="tn1",
        service_id="6",
        receipt_external_map_id=101,
        vendor_external_map_id=202,
    )

    assert out["ok"] is False
    assert "does not belong to this bill's vendor" in out["message"]


def test_create_bill_delegates_to_single_bill_export():
    svc = _make_service()
    svc.qbo_repo.getServiceIdByName.return_value = "6"
    svc.receipt_map_repo.get_by_mapping_id.return_value = {
        "id": 101,
        "receipt_id": 501,
        "service_id": 6,
        "sync_status": "PENDING_PUSH",
    }
    svc.receipt_repo.get_current_receipt_by_id.return_value = {
        "id": 501,
        "vendor_id": 20,
    }
    svc.vendor_map_repo.get_by_mapping_id.return_value = {
        "id": 202,
        "service_id": 6,
        "vendor_id": 20,
        "external_vendor_id": "V-1",
        "sync_status": "SYNCED",
    }
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_STATUS: "connected",
        const.KEY_REALM_ID: "R1",
        const.KEY_ENV: "sandbox",
        const.KEY_REFRESH_TOKEN: "RT",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SECRET",
    }
    svc.bill_exporter.refresh_access_token.return_value = "AT"
    svc.bill_exporter.get_qbo_api_base.return_value = "https://sandbox-quickbooks.api.intuit.com"
    svc._export_single_bill = MagicMock(return_value={"ok": True, "createdInQbo": 1})

    out = svc.create_bill(
        tenant_id="tn1",
        service_id=None,
        receipt_external_map_id=101,
        vendor_external_map_id=202,
    )

    assert out["ok"] is True
    svc._export_single_bill.assert_called_once_with(
        tenant_id="tn1",
        service_id="6",
        realm_id="R1",
        env="sandbox",
        qbo_service_master_id="6",
        access_token="AT",
        base_url="https://sandbox-quickbooks.api.intuit.com",
        receipt={"id": 501, "vendor_id": 20},
        mapping={
            "id": 101,
            "receipt_id": 501,
            "service_id": 6,
            "sync_status": "PENDING_PUSH",
        },
    )


@pytest.mark.parametrize(
    ("receipt_name", "stock_credit"),
    [("StockDebitBill", False), ("StockCreditBill", True)],
)
def test_create_bill_routes_stock_adjustments_to_realtime_journal_export(receipt_name, stock_credit):
    svc = _make_service()
    svc.qbo_repo.getServiceIdByName.return_value = "6"
    svc.receipt_map_repo.get_by_mapping_id.return_value = {
        "id": 101,
        "receipt_id": 501,
        "service_id": 6,
        "sync_status": "PENDING_PUSH",
    }
    receipt = {
        "id": 501,
        "name": receipt_name,
        "project_id": 77,
        "chart_of_account_id": 14,
    }
    svc.receipt_repo.get_current_receipt_by_id.return_value = receipt
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_STATUS: "connected",
        const.KEY_REALM_ID: "R1",
        const.KEY_ENV: "sandbox",
        const.KEY_REFRESH_TOKEN: "RT",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SECRET",
    }
    svc.bill_exporter.refresh_access_token.return_value = "AT"
    svc.bill_exporter.get_qbo_api_base.return_value = "https://sandbox-quickbooks.api.intuit.com"
    svc._export_single_stock_journal_entry = MagicMock(
        return_value={"ok": True, "createdJournalEntriesInQbo": 1}
    )

    out = svc.create_bill(
        tenant_id="tn1",
        service_id="6",
        receipt_external_map_id=101,
    )

    assert out["ok"] is True
    svc.vendor_map_repo.get_by_mapping_id.assert_not_called()
    svc._export_single_stock_journal_entry.assert_called_once_with(
        stock_credit=stock_credit,
        tenant_id="tn1",
        service_id="6",
        realm_id="R1",
        env="sandbox",
        qbo_service_master_id="6",
        access_token="AT",
        base_url="https://sandbox-quickbooks.api.intuit.com",
        receipt=receipt,
        mapping={
            "id": 101,
            "receipt_id": 501,
            "service_id": 6,
            "sync_status": "PENDING_PUSH",
        },
    )


def test_stock_credit_realtime_journal_export_passes_reverse_direction_flag():
    svc = _make_service()
    svc.bill_exporter._clean.side_effect = lambda value: str(value or "").strip()

    out = svc._export_single_stock_journal_entry(
        stock_credit=True,
        tenant_id="tn1",
        service_id="6",
        realm_id="R1",
        env="sandbox",
        qbo_service_master_id="6",
        access_token="AT",
        base_url="https://sandbox-quickbooks.api.intuit.com",
        receipt={"id": 501, "name": "StockCreditBill", "project_id": 77},
        mapping={"id": 101, "sync_status": "PENDING_PUSH"},
    )

    assert out["ok"] is True
    kwargs = svc.bill_exporter._export_stock_debit_bill_as_journal_entry.call_args.kwargs
    assert kwargs["stock_credit"] is True
    assert kwargs["receipt"]["name"] == "StockCreditBill"


def test_create_bill_allows_sync_failed_mapping_for_retry():
    svc = _make_service()
    svc.qbo_repo.getServiceIdByName.return_value = "6"
    svc.receipt_map_repo.get_by_mapping_id.return_value = {
        "id": 101,
        "receipt_id": 501,
        "service_id": 6,
        "sync_status": "SYNC_FAILED",
    }
    receipt = {"id": 501, "vendor_id": 20}
    svc.receipt_repo.get_current_receipt_by_id.return_value = receipt
    svc.qbo_repo.getAdditionalInfo.return_value = {
        const.KEY_STATUS: "connected",
        const.KEY_REALM_ID: "R1",
        const.KEY_ENV: "sandbox",
        const.KEY_REFRESH_TOKEN: "RT",
    }
    svc.qbo_service.get_quickbooks_config.return_value = {
        const.KEY_CLIENT_ID: "CID",
        const.KEY_CLIENT_SECRET: "SECRET",
    }
    svc.bill_exporter.refresh_access_token.return_value = "AT"
    svc.bill_exporter.get_qbo_api_base.return_value = "https://sandbox-quickbooks.api.intuit.com"
    svc._export_single_bill = MagicMock(return_value={"ok": True, "updatedInQbo": 1})

    out = svc.create_bill(
        tenant_id="tn1",
        service_id="6",
        receipt_external_map_id=101,
    )

    assert out["ok"] is True
    svc._export_single_bill.assert_called_once()


def _configure_single_bill_export(svc):
    svc.receipt_repo.STATUS_PENDING_PUSH = "PENDING_PUSH"
    svc.receipt_repo.STATUS_FAILED = "FAILED"
    svc.bill_exporter._clean.side_effect = lambda value: str(value or "").strip()
    svc.bill_exporter._is_qbo_deleted_ref_error.return_value = False


def test_single_bill_payload_validation_failure_marks_mapping_failed():
    svc = _make_service()
    _configure_single_bill_export(svc)
    svc.bill_exporter._build_qbo_bill_payload.side_effect = ValueError(
        "Vendor has no QuickBooks mapping. Run vendor sync, then retry this bill."
    )

    out = svc._export_single_bill(
        tenant_id="tn1",
        service_id="6",
        realm_id="R1",
        env="sandbox",
        qbo_service_master_id="6",
        access_token="AT",
        base_url="https://sandbox-quickbooks.api.intuit.com",
        receipt={"id": 501, "vendor_id": 20},
        mapping={"id": 101, "sync_status": "PENDING_PUSH"},
    )

    assert out["ok"] is False
    assert out["failed"] == 1
    assert "Vendor has no QuickBooks mapping" in out[const.KEY_RESULTS][0][const.KEY_ERROR]
    svc.receipt_map_repo.mark_mapping_failed.assert_called_once()
    svc.bill_exporter.create_bill_in_qbo.assert_not_called()


def test_single_bill_rejects_mapping_from_different_quickbooks_company():
    svc = _make_service()
    _configure_single_bill_export(svc)
    svc.bill_exporter._assert_mapping_realm.side_effect = ValueError(
        "The mapped bill belongs to a different QuickBooks company. Re-sync it and retry."
    )

    out = svc._export_single_bill(
        tenant_id="tn1",
        service_id="6",
        realm_id="R1",
        env="sandbox",
        qbo_service_master_id="6",
        access_token="AT",
        base_url="https://sandbox-quickbooks.api.intuit.com",
        receipt={"id": 501, "vendor_id": 20},
        mapping={
            "id": 101,
            "sync_status": "PENDING_PUSH",
            "external_receipt_id": "B-OLD",
            "external_account_id": "OTHER-REALM",
        },
    )

    assert out["ok"] is False
    assert "different QuickBooks company" in out[const.KEY_RESULTS][0][const.KEY_ERROR]
    svc.receipt_map_repo.mark_mapping_failed.assert_called_once()
    svc.bill_exporter._build_qbo_bill_payload.assert_not_called()
    svc.bill_exporter.get_bill_in_qbo.assert_not_called()


def test_single_bill_create_persists_managed_line_metadata():
    svc = _make_service()
    _configure_single_bill_export(svc)
    receipt = {"id": 501, "vendor_id": 20, "document_id": 77}
    mapping = {"id": 101, "sync_status": "PENDING_PUSH"}
    payload = {"DocNumber": "B-1", "Line": [{"Amount": 100.0}]}
    created = {
        const.QBO_KEY_ID: "QB-1",
        const.QBO_KEY_SYNC_TOKEN: "0",
        "Line": [{"Id": "17", "Amount": 100.0}],
    }
    managed_line = {"id": "17", "amount": 100.0}
    svc.bill_exporter._build_qbo_bill_payload.return_value = payload
    svc.bill_exporter.find_bill_candidates_in_qbo.return_value = []
    svc.bill_exporter.create_bill_in_qbo.return_value = created
    svc.bill_exporter._build_managed_line_metadata.return_value = managed_line

    out = svc._export_single_bill(
        tenant_id="tn1",
        service_id="6",
        realm_id="R1",
        env="sandbox",
        qbo_service_master_id="6",
        access_token="AT",
        base_url="https://sandbox-quickbooks.api.intuit.com",
        receipt=receipt,
        mapping=mapping,
    )

    assert out["ok"] is True
    assert out["createdInQbo"] == 1
    svc.bill_exporter._build_managed_line_metadata.assert_called_once_with(
        qbo_bill=created,
        payload=payload,
        receipt=receipt,
        allow_single_line_fallback=True,
    )
    svc.bill_exporter._build_reason.assert_any_call(
        "Bill created in QuickBooks successfully.",
        managed_line=managed_line,
    )
    synced_kwargs = svc.receipt_map_repo.mark_mapping_synced.call_args.kwargs
    assert synced_kwargs["external_receipt_id"] == "QB-1"
    assert synced_kwargs["sync_token"] == "0"


def test_single_bill_qbo_newer_preserves_existing_managed_line_metadata():
    svc = _make_service()
    _configure_single_bill_export(svc)
    receipt = {"id": 501, "vendor_id": 20}
    mapping = {
        "id": 101,
        "sync_status": "PENDING_PUSH",
        "external_receipt_id": "QB-1",
        "external_account_id": "R1",
        "sync_token": "1",
    }
    managed_line = {"id": "17", "amount": 100.0}
    svc.bill_exporter._build_qbo_bill_payload.return_value = {"DocNumber": "B-1"}
    svc.bill_exporter.get_bill_in_qbo.return_value = {
        const.QBO_KEY_ID: "QB-1",
        const.QBO_KEY_SYNC_TOKEN: "2",
    }
    svc.bill_exporter._get_qbo_last_updated_at.return_value = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    svc.bill_exporter._get_local_receipt_updated_at.return_value = datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc)
    svc.bill_exporter._get_managed_line_metadata.return_value = managed_line

    out = svc._export_single_bill(
        tenant_id="tn1",
        service_id="6",
        realm_id="R1",
        env="sandbox",
        qbo_service_master_id="6",
        access_token="AT",
        base_url="https://sandbox-quickbooks.api.intuit.com",
        receipt=receipt,
        mapping=mapping,
    )

    assert out["ok"] is True
    assert out["skippedQboNewer"] == 1
    svc.bill_exporter._get_managed_line_metadata.assert_called_once_with(mapping)
    svc.bill_exporter._build_reason.assert_any_call(
        "QuickBooks has a newer version of this bill. The next import will apply those changes here.",
        managed_line=managed_line,
    )
    svc.bill_exporter.update_bill_in_qbo.assert_not_called()


def test_webhook_process_imports_bills_by_ids():
    changes = [
        {"name": "Bill", "id": "9001", "operation": "Create"},
        {"name": "Customer", "id": "C1", "operation": "Create"},
    ]

    with patch("source.quickbooks.QuickBooksBillImportService.QuickBooksBillImportService") as bill_cls, \
         patch("source.quickbooks.QuickBooksCustomerImportService.QuickBooksCustomerImportService") as customer_cls:
        bill_importer = bill_cls.return_value
        customer_importer = customer_cls.return_value

        _process_qbo_webhook("tn1", "6", changes)

        bill_importer.import_bills_by_ids.assert_called_once_with(
            tenant_id="tn1",
            service_id="6",
            bill_ids=["9001"],
        )
        customer_importer.import_customers_by_ids.assert_called_once_with(
            tenant_id="tn1",
            service_id="6",
            customer_ids=["C1"],
        )


def test_webhook_process_imports_bill_updates_by_ids():
    changes = [
        {"name": "Bill", "id": "9002", "operation": "Update"},
    ]

    with patch("source.quickbooks.QuickBooksBillImportService.QuickBooksBillImportService") as bill_cls:
        bill_importer = bill_cls.return_value

        _process_qbo_webhook("tn1", "6", changes)

        bill_importer.import_bills_by_ids.assert_called_once_with(
            tenant_id="tn1",
            service_id="6",
            bill_ids=["9002"],
        )
