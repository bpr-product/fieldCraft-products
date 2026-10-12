from unittest.mock import MagicMock

from source.Utils import constants as const
from source.quickbooks.QuickBooksInvoiceCreateService import QuickBooksInvoiceCreateService


def _service():
    svc = QuickBooksInvoiceCreateService()
    svc.qbo_repo = MagicMock()
    svc.invoice_repo = MagicMock()
    svc.invoice_repo.STATUS_PENDING_PUSH = "PENDING_PUSH"
    svc.invoice_repo.STATUS_FAILED = "FAILED"
    svc.invoice_repo.STATUS_SYNC_FAILED = "SYNC_FAILED"
    svc.invoice_repo.STATUS_SYNCED = "SYNCED"
    svc.invoice_exporter = MagicMock()
    return svc


def _mapping(**overrides):
    data = {
        "id": 901,
        "invoice_id": 101,
        "tenant_id": "tn1",
        "service_id": 6,
        "sync_status": "PENDING_PUSH",
        "external_invoice_id": None,
    }
    data.update(overrides)
    return data


def _invoice(**overrides):
    data = {
        "id": 101,
        "invoice_no": "INV-1001",
        "status": "Un Paid",
        "invoiced_amount": 125.50,
    }
    data.update(overrides)
    return data


def test_create_invoice_requires_tenant_id():
    svc = _service()

    out = svc.create_invoice(
        tenant_id="",
        service_id="6",
        invoice_external_map_id=901,
    )

    assert out[const.KEY_OK] is False
    assert "tenant_id is required" in out[const.KEY_MESSAGE]


def test_create_invoice_mapping_not_found():
    svc = _service()
    svc.invoice_repo.get_mapping_by_id.return_value = None

    out = svc.create_invoice(
        tenant_id="tn1",
        service_id="6",
        invoice_external_map_id=901,
    )

    assert out[const.KEY_OK] is False
    assert "not found" in out[const.KEY_MESSAGE]
    svc.invoice_exporter.export_invoices.assert_not_called()


def test_create_invoice_returns_success_when_duplicate_trigger_finds_mapping_already_synced():
    svc = _service()
    svc.invoice_repo.get_mapping_by_id.return_value = _mapping(
        sync_status="SYNCED",
        external_invoice_id="QBO-INV-1",
    )

    out = svc.create_invoice(
        tenant_id="tn1",
        service_id="6",
        invoice_external_map_id=901,
    )

    assert out[const.KEY_OK] is True
    assert out[const.KEY_STATUS] == "already_synced"
    assert out["qboInvoiceId"] == "QBO-INV-1"
    svc.invoice_exporter.export_invoices.assert_not_called()


def test_create_invoice_rejects_invoice_that_is_not_accounting_exportable():
    svc = _service()
    svc.invoice_repo.get_mapping_by_id.return_value = _mapping()
    svc.invoice_repo.get_current_invoice_by_id.return_value = _invoice(status="Draft")
    svc.invoice_repo.is_exportable_invoice.return_value = False

    out = svc.create_invoice(
        tenant_id="tn1",
        service_id="6",
        invoice_external_map_id=901,
    )

    assert out[const.KEY_OK] is False
    assert "not ready" in out[const.KEY_MESSAGE]
    svc.invoice_exporter.export_invoices.assert_not_called()


def test_create_invoice_delegates_exact_mapping_to_shared_invoice_exporter():
    svc = _service()
    svc.invoice_repo.get_mapping_by_id.return_value = _mapping(sync_status="SYNC_FAILED")
    svc.invoice_repo.get_current_invoice_by_id.return_value = _invoice()
    svc.invoice_repo.is_exportable_invoice.return_value = True
    svc.invoice_exporter.export_invoices.return_value = {
        const.KEY_OK: True,
        "requested": 1,
        "createdInQbo": 1,
        "updatedInQbo": 0,
        "mappedFromQbo": 0,
        "failed": 0,
        "syncFailed": 0,
        const.KEY_RESULTS: [{"mappingId": 901, "qboInvoiceId": "QBO-INV-1"}],
    }

    out = svc.create_invoice(
        tenant_id="tn1",
        service_id="6",
        invoice_external_map_id=901,
    )

    assert out[const.KEY_OK] is True
    assert out["invoiceId"] == 101
    assert out["invoiceExternalMapId"] == 901
    svc.invoice_exporter.export_invoices.assert_called_once_with(
        tenant_id="tn1",
        service_id="6",
        limit=1,
        offset=0,
        object_id=None,
        email=None,
        user_role=None,
        include_mapping_ids=[901],
    )


def test_create_invoice_turns_invoice_sync_failure_into_realtime_failure_result():
    svc = _service()
    svc.invoice_repo.get_mapping_by_id.return_value = _mapping()
    svc.invoice_repo.get_current_invoice_by_id.return_value = _invoice()
    svc.invoice_repo.is_exportable_invoice.return_value = True
    svc.invoice_exporter.export_invoices.return_value = {
        const.KEY_OK: True,
        "requested": 1,
        "failed": 0,
        "syncFailed": 1,
        const.KEY_MESSAGE: "Invoice sync completed with 1 invoice(s) that need attention.",
    }

    out = svc.create_invoice(
        tenant_id="tn1",
        service_id="6",
        invoice_external_map_id=901,
    )

    assert out[const.KEY_OK] is False
    assert out["syncFailed"] == 1
