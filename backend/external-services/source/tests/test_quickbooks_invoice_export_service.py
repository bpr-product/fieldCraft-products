from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from source.Utils import constants as const
from source.dal.InvoiceRepository import InvoiceRepository
from source.quickbooks.QuickBooksInvoiceExportService import QuickBooksInvoiceExportService


def _install_mock_export_lock(repo):
    def claim_export_mapping(*, tenant_id, service_id, mapping_id, source_fingerprint):
        del tenant_id, service_id, source_fingerprint
        for candidate in repo.get_export_candidates.return_value or []:
            mapping = candidate.get("mapping") or {}
            if int(mapping.get("id") or 0) == int(mapping_id):
                return {"claimed": True, "reason": "claimed", "mapping": dict(mapping)}
        raise ValueError(f"Invoice external mapping {mapping_id} was not found")

    repo.claim_export_mapping.side_effect = claim_export_mapping


def _invoice(**overrides):
    data = {
        "id": 101,
        "invoice_no": "INV-1001",
        "customer_uid": "customer-uuid-1",
        "project_id": 77,
        "created_at": datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc),
        "issue_at": datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc),
        "due_date": datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc),
        "status": "Un Paid",
        "invoice_message": "Thank you",
        "invoiced_amount": Decimal("110.00"),
        "total_amount": Decimal("100.00"),
        "tax_amount": Decimal("10.00"),
        "tax_info": [
            {"name": "Sales Tax", "percentage": "10", "status": "Active"}
        ],
        "term": "Progress Payment",
        "document_id": None,
        "items": [
            {
                "quantity": 2,
                "rate": "50.00",
                "subTotal": "100.00",
                "lineItemDescription": "Roofing labor",
                "status": "Active",
            }
        ],
    }
    data.update(overrides)
    return data


def _service():
    svc = QuickBooksInvoiceExportService()
    svc.customer_repo = MagicMock()
    svc.project_repo = MagicMock()
    svc.project_repo.get_active_project_by_id.return_value = {"customer_uid": "customer-uuid-1"}
    svc.role_repo = MagicMock()
    svc.role_repo.STATUS_SYNCED = "SYNCED"
    svc.role_repo.get_active_role_by_name.return_value = {
        "id": 5,
        "name": "labour",
        "active": True,
    }
    svc.role_repo.get_current_mapping_by_role_id.return_value = {
        "id": 83,
        "role_id": 5,
        "external_service_id": "6",
        "external_account_id": "REALM-1",
        "external_display_name": "labour",
        "sync_status": "SYNCED",
    }
    svc.invoice_repo = InvoiceRepository()
    svc.attachment_service = MagicMock()
    svc.attachment_service.attach_invoice_document.return_value = {
        "status": "uploaded",
        "documentId": 301,
        "attachableId": "ATT-1",
        "fileName": "INV-1001.pdf",
    }
    return svc


def test_invoice_item_mapping_resolves_labour_role_to_qbo_service_item():
    svc = _service()

    result = svc._resolve_invoice_item_mapping(tenant_id="tn1", service_id="6")

    assert result["role"]["id"] == 5
    assert result["mapping"]["id"] == 83
    assert result["item_id"] == "6"
    svc.role_repo.get_active_role_by_name.assert_called_once_with(tenant_id="tn1", name="labour")
    svc.role_repo.get_current_mapping_by_role_id.assert_called_once_with(
        tenant_id="tn1", service_id="6", role_id=5
    )


def test_invoice_item_mapping_requires_active_labour_role():
    svc = _service()
    svc.role_repo.get_active_role_by_name.return_value = None

    with pytest.raises(ValueError, match="must be set up and synced"):
        svc._resolve_invoice_item_mapping(tenant_id="tn1", service_id="6")


def test_invoice_item_mapping_requires_synced_role_mapping():
    svc = _service()
    svc.role_repo.get_current_mapping_by_role_id.return_value = {
        "id": 83,
        "role_id": 5,
        "external_service_id": "6",
        "external_account_id": "REALM-1",
        "sync_status": "PENDING_PUSH",
    }

    with pytest.raises(ValueError, match="must be synced to QuickBooks"):
        svc._resolve_invoice_item_mapping(tenant_id="tn1", service_id="6")


def test_invoice_item_mapping_requires_external_service_id():
    svc = _service()
    svc.role_repo.get_current_mapping_by_role_id.return_value = {
        "id": 83,
        "role_id": 5,
        "external_service_id": None,
        "external_account_id": "REALM-1",
        "sync_status": "SYNCED",
    }

    with pytest.raises(ValueError, match="needs to be synced again"):
        svc._resolve_invoice_item_mapping(tenant_id="tn1", service_id="6")


def test_validate_invoice_item_requires_qbo_service_item(monkeypatch):
    svc = _service()

    class Response:
        status_code = 200
        headers = {}

        def json(self):
            return {"Item": {"Id": "6", "Name": "labour", "Type": "Inventory", "Active": True}}

    import source.quickbooks.QuickBooksInvoiceExportService as invoice_export_module
    monkeypatch.setattr(invoice_export_module.qbo_err, "qbo_get", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(invoice_export_module.qbo_err, "raise_for_qbo_response", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(invoice_export_module.qbo_err, "safe_json", lambda response: response.json())

    with pytest.raises(ValueError, match="not a Service item"):
        svc._validate_invoice_item(
            access_token="AT",
            base_url="https://qbo.example",
            realm_id="REALM-1",
            item_id="6",
        )


def test_build_lines_uses_one_summary_line_for_entire_fieldcraft_invoice_total():
    svc = _service()

    lines = svc._build_lines(invoice=_invoice(), item_id="4")

    assert len(lines) == 1
    assert lines[0]["Amount"] == 110.0
    assert lines[0]["SalesItemLineDetail"] == {"ItemRef": {"value": "4"}}
    assert "Qty" not in lines[0]["SalesItemLineDetail"]
    assert "UnitPrice" not in lines[0]["SalesItemLineDetail"]
    assert lines[0]["Description"] == (
        "FIELDCRAFT Invoice INV-1001 | Term: Progress Payment | "
        "Sales Tax 10%: $10.00 | Total: $110.00"
    )


def test_build_lines_does_not_split_or_reconcile_fieldcraft_invoice_items():
    svc = _service()
    invoice = _invoice(
        invoiced_amount=Decimal("90.00"),
        items=[
            {"quantity": -1, "rate": "999.00", "subTotal": "999.00", "description": "Ignored"},
            {"subTotal": "-20.00", "description": "Also ignored"},
        ],
    )

    lines = svc._build_lines(invoice=invoice, item_id="4")

    assert len(lines) == 1
    assert lines[0]["Amount"] == 90.0


def test_build_lines_requires_positive_authoritative_invoice_total():
    svc = _service()

    with pytest.raises(ValueError, match="greater than zero"):
        svc._build_lines(invoice=_invoice(invoiced_amount=Decimal("0.00")), item_id="4")


def test_build_lines_tax_description_uses_tax_amount_even_without_tax_info():
    svc = _service()

    line = svc._build_lines(
        invoice=_invoice(tax_info=[], tax_amount=Decimal("8.25")),
        item_id="4",
    )[0]

    assert "Tax: $8.25" in line["Description"]


def test_build_payload_uses_parent_customer_and_project_ref_for_real_qbo_project():
    svc = _service()

    payload = svc.build_invoice_payload(
        invoice=_invoice(),
        item_id="4",
        references={
            "customer_ref": "QBO-CUSTOMER-1",
            "project_ref": "QBO-PROJECT-1",
        },
    )

    assert payload["CustomerRef"] == {"value": "QBO-CUSTOMER-1"}
    assert payload["ProjectRef"] == {"value": "QBO-PROJECT-1"}
    assert payload["DocNumber"] == "INV-1001"
    assert payload["TxnDate"] == "2026-08-13"
    assert payload["DueDate"] == "2026-09-12"


def test_build_payload_omits_project_ref_for_legacy_sub_customer_project():
    svc = _service()

    payload = svc.build_invoice_payload(
        invoice=_invoice(),
        item_id="4",
        references={
            "customer_ref": "QBO-SUBCUSTOMER-1",
            "project_ref": None,
        },
    )

    assert payload["CustomerRef"] == {"value": "QBO-SUBCUSTOMER-1"}
    assert "ProjectRef" not in payload



def test_resolve_references_rejects_invoice_customer_project_mismatch():
    svc = _service()
    svc.project_repo.get_active_project_by_id.return_value = {"customer_uid": "different-customer"}

    with pytest.raises(ValueError, match="does not match the customer assigned"):
        svc._resolve_references(
            tenant_id="tn1",
            service_id="6",
            realm_id="REALM-1",
            invoice=_invoice(),
        )

def test_resolve_references_uses_real_qbo_project_mapping():
    svc = _service()
    svc.customer_repo.get_latest_qbo_linked_mapping_for_customer_uuid.return_value = {
        "id": 11,
        "external_customer_id": "QBO-CUSTOMER-1",
        "external_account_id": "REALM-1",
    }
    project_mapping = {
        "id": 22,
        "external_project_id": "QBO-PROJECT-1",
        "external_account_id": "REALM-1",
    }
    svc.project_repo.get_current_mapping_by_project_id.return_value = project_mapping
    svc.project_repo.get_mapping_sync_target.return_value = const.PROJECT_SYNC_TARGET_QBO_PROJECT

    refs = svc._resolve_references(
        tenant_id="tn1",
        service_id="6",
        realm_id="REALM-1",
        invoice=_invoice(),
    )

    assert refs["customer_ref"] == "QBO-CUSTOMER-1"
    assert refs["project_ref"] == "QBO-PROJECT-1"
    assert refs["project_sync_target"] == const.PROJECT_SYNC_TARGET_QBO_PROJECT


def test_resolve_references_uses_project_as_customer_for_sub_customer_mapping():
    svc = _service()
    svc.customer_repo.get_latest_qbo_linked_mapping_for_customer_uuid.return_value = {
        "id": 11,
        "external_customer_id": "QBO-CUSTOMER-1",
        "external_account_id": "REALM-1",
    }
    project_mapping = {
        "id": 22,
        "external_project_id": "QBO-SUBCUSTOMER-1",
        "external_account_id": "REALM-1",
    }
    svc.project_repo.get_current_mapping_by_project_id.return_value = project_mapping
    svc.project_repo.get_mapping_sync_target.return_value = const.PROJECT_SYNC_TARGET_SUB_CUSTOMER

    refs = svc._resolve_references(
        tenant_id="tn1",
        service_id="6",
        realm_id="REALM-1",
        invoice=_invoice(),
    )

    assert refs["customer_ref"] == "QBO-SUBCUSTOMER-1"
    assert refs["project_ref"] is None
    assert refs["project_sync_target"] == const.PROJECT_SYNC_TARGET_SUB_CUSTOMER


def test_resolve_references_rejects_cross_company_project_mapping():
    svc = _service()
    svc.customer_repo.get_latest_qbo_linked_mapping_for_customer_uuid.return_value = {
        "external_customer_id": "QBO-CUSTOMER-1",
        "external_account_id": "REALM-1",
    }
    svc.project_repo.get_current_mapping_by_project_id.return_value = {
        "external_project_id": "QBO-PROJECT-1",
        "external_account_id": "OTHER-REALM",
    }

    with pytest.raises(ValueError, match="different QuickBooks company"):
        svc._resolve_references(
            tenant_id="tn1",
            service_id="6",
            realm_id="REALM-1",
            invoice=_invoice(),
        )


def test_upsert_updates_mapped_invoice_instead_of_creating_duplicate():
    svc = _service()
    svc.update_invoice_in_qbo = MagicMock(return_value={"Id": "QBO-INV-1", "SyncToken": "2"})
    svc.find_invoice_candidates_in_qbo = MagicMock()
    svc.create_invoice_in_qbo = MagicMock()

    result, action = svc._upsert_invoice(
        access_token="AT",
        base_url="https://qbo.example",
        realm_id="REALM-1",
        invoice=_invoice(),
        mapping={
            "external_invoice_id": "QBO-INV-1",
            "external_account_id": "REALM-1",
            "sync_token": "1",
        },
        payload={"CustomerRef": {"value": "QBO-CUSTOMER-1"}},
        references={"customer_ref": "QBO-CUSTOMER-1", "project_ref": "QBO-PROJECT-1"},
    )

    assert result["Id"] == "QBO-INV-1"
    assert action == "updated in QBO"
    svc.find_invoice_candidates_in_qbo.assert_not_called()
    svc.create_invoice_in_qbo.assert_not_called()


def test_source_fingerprint_ignores_payment_status_and_invoice_item_detail_changes():
    repo = InvoiceRepository()
    first = _invoice(
        status="Un Paid",
        balance=Decimal("110.00"),
        updated_at=datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc),
    )
    second = _invoice(
        status="Partially Paid",
        balance=Decimal("55.00"),
        updated_at=datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc),
    )
    second["items"] = [{"description": "Completely different", "subTotal": "9999.00"}]

    assert repo.source_fingerprint(first) == repo.source_fingerprint(second)


def test_source_fingerprint_changes_when_tax_or_attachment_changes():
    repo = InvoiceRepository()

    base = _invoice()
    assert repo.source_fingerprint(base) != repo.source_fingerprint(
        _invoice(tax_amount=Decimal("11.00"))
    )
    assert repo.source_fingerprint(base) != repo.source_fingerprint(
        _invoice(document_id=301)
    )


def test_source_fingerprint_changes_when_exported_amount_changes():
    repo = InvoiceRepository()

    assert repo.source_fingerprint(_invoice()) != repo.source_fingerprint(
        _invoice(invoiced_amount=Decimal("111.00"))
    )


def test_export_with_no_candidates_does_not_resolve_invoice_item(monkeypatch):
    import source.quickbooks.QuickBooksInvoiceExportService as invoice_export_module

    svc = _service()
    svc.invoice_repo = MagicMock()
    _install_mock_export_lock(svc.invoice_repo)
    svc.invoice_repo.get_export_candidates.return_value = []
    svc._open_qbo_session = MagicMock()
    monkeypatch.setattr(invoice_export_module, "safe_audit_log", MagicMock())

    result = svc.export_invoices(tenant_id="tn1", service_id="6")

    assert result[const.KEY_OK] is True
    assert result["requested"] == 0
    svc.role_repo.get_active_role_by_name.assert_not_called()
    svc._open_qbo_session.assert_not_called()


def test_export_marks_invoice_failed_when_labour_role_mapping_is_missing(monkeypatch):
    import source.quickbooks.QuickBooksInvoiceExportService as invoice_export_module

    svc = _service()
    svc.invoice_repo = MagicMock()
    svc.invoice_repo.STATUS_SYNC_FAILED = "SYNC_FAILED"
    _install_mock_export_lock(svc.invoice_repo)
    svc.invoice_repo.get_export_candidates.return_value = [
        {
            "invoice": _invoice(),
            "mapping": {"id": 7, "sync_status": "PENDING_PUSH"},
        }
    ]
    svc.role_repo.get_current_mapping_by_role_id.return_value = None
    svc._open_qbo_session = MagicMock()
    monkeypatch.setattr(invoice_export_module, "safe_audit_log", MagicMock())

    result = svc.export_invoices(tenant_id="tn1", service_id="6")

    assert result[const.KEY_OK] is True
    assert result["syncFailed"] == 1
    assert result[const.KEY_RESULTS][0][const.KEY_STATUS] == "failed"
    assert "Service Role 'labour' must be synced to QuickBooks" in result[const.KEY_RESULTS][0][const.KEY_ERROR]
    svc.invoice_repo.mark_mapping_failed.assert_called_once()
    assert svc.invoice_repo.mark_mapping_failed.call_args.kwargs["status"] == "SYNC_FAILED"
    svc._open_qbo_session.assert_not_called()


def test_export_marks_invoice_failed_when_labour_role_mapping_is_pending(monkeypatch):
    import source.quickbooks.QuickBooksInvoiceExportService as invoice_export_module

    svc = _service()
    svc.invoice_repo = MagicMock()
    svc.invoice_repo.STATUS_SYNC_FAILED = "SYNC_FAILED"
    _install_mock_export_lock(svc.invoice_repo)
    svc.invoice_repo.get_export_candidates.return_value = [
        {
            "invoice": _invoice(),
            "mapping": {"id": 7, "sync_status": "PENDING_PUSH"},
        }
    ]
    svc.role_repo.get_current_mapping_by_role_id.return_value["sync_status"] = "PENDING_PUSH"
    svc._open_qbo_session = MagicMock()
    monkeypatch.setattr(invoice_export_module, "safe_audit_log", MagicMock())

    result = svc.export_invoices(tenant_id="tn1", service_id="6")

    assert result[const.KEY_OK] is True
    assert result["syncFailed"] == 1
    failure = result[const.KEY_RESULTS][0]
    assert failure[const.KEY_STATUS] == "failed"
    assert "PENDING_PUSH" not in failure[const.KEY_ERROR]
    assert failure[const.KEY_ERROR] == (
        "Service Role 'labour' must be synced to QuickBooks before this invoice can be synced. "
        "Sync Service Roles, then try again."
    )
    svc.invoice_repo.mark_mapping_failed.assert_called_once()
    assert svc.invoice_repo.mark_mapping_failed.call_args.kwargs["status"] == "SYNC_FAILED"
    svc._open_qbo_session.assert_not_called()


def test_export_isolates_missing_project_mapping_as_record_failure(monkeypatch):
    import source.quickbooks.QuickBooksInvoiceExportService as invoice_export_module

    svc = _service()
    invoice_repo = MagicMock()
    invoice_repo.STATUS_SYNC_FAILED = "SYNC_FAILED"
    invoice_repo.is_exportable_invoice.return_value = True
    invoice_repo.get_export_candidates.return_value = [
        {
            "invoice": _invoice(),
            "mapping": {"id": 7, "sync_status": "PENDING_PUSH"},
        }
    ]
    svc.invoice_repo = invoice_repo
    _install_mock_export_lock(invoice_repo)
    svc.customer_repo.get_latest_qbo_linked_mapping_for_customer_uuid.return_value = {
        "id": 11,
        "external_customer_id": "QBO-CUSTOMER-1",
        "external_account_id": "REALM-1",
    }
    svc.project_repo.get_current_mapping_by_project_id.return_value = None
    svc._open_qbo_session = MagicMock(
        return_value={
            "access_token": "AT",
            "realm_id": "REALM-1",
            "env": "sandbox",
            "base_url": "https://qbo.example",
        }
    )
    svc._validate_invoice_item = MagicMock(return_value={"Id": "6", "Name": "labour", "Type": "Service"})
    monkeypatch.setattr(invoice_export_module, "safe_audit_log", MagicMock())

    result = svc.export_invoices(tenant_id="tn1", service_id="6")

    assert result[const.KEY_OK] is True
    assert result["syncFailed"] == 1
    assert "project is not mapped" in result[const.KEY_FAILURES][0][const.KEY_ERROR].lower()
    invoice_repo.mark_mapping_failed.assert_called_once()
    assert invoice_repo.mark_mapping_failed.call_args.kwargs["status"] == "SYNC_FAILED"


def test_export_propagates_reconnect_required_for_api_connection_handling(monkeypatch):
    import source.quickbooks.QuickBooksInvoiceExportService as invoice_export_module
    from source.quickbooks.QuickBooksAuthentication import QuickBooksReconnectRequiredError

    svc = _service()
    svc.invoice_repo = MagicMock()
    _install_mock_export_lock(svc.invoice_repo)
    svc.invoice_repo.get_export_candidates.return_value = [
        {"invoice": _invoice(), "mapping": {"id": 7, "sync_status": "PENDING_PUSH"}}
    ]
    svc._open_qbo_session = MagicMock(
        side_effect=QuickBooksReconnectRequiredError(
            "Reconnect QuickBooks",
            error_code="invalid_grant",
        )
    )
    monkeypatch.setattr(invoice_export_module, "safe_audit_log", MagicMock())

    with pytest.raises(QuickBooksReconnectRequiredError):
        svc.export_invoices(tenant_id="tn1", service_id="6")


def test_export_uses_labour_role_mapping_item_id_and_persists_mapping_metadata(monkeypatch):
    import source.quickbooks.QuickBooksInvoiceExportService as invoice_export_module

    svc = _service()
    invoice_repo = MagicMock()
    invoice_repo.is_exportable_invoice.return_value = True
    invoice_repo.get_export_candidates.return_value = [
        {
            "invoice": _invoice(document_id=301),
            "mapping": {"id": 7, "sync_status": "PENDING_PUSH"},
        }
    ]
    invoice_repo.source_fingerprint.return_value = "fingerprint-1"
    svc.invoice_repo = invoice_repo
    _install_mock_export_lock(invoice_repo)
    svc.customer_repo.get_latest_qbo_linked_mapping_for_customer_uuid.return_value = {
        "id": 11,
        "external_customer_id": "QBO-CUSTOMER-1",
        "external_account_id": "REALM-1",
    }
    svc.project_repo.get_current_mapping_by_project_id.return_value = {
        "id": 22,
        "external_project_id": "QBO-PROJECT-1",
        "external_account_id": "REALM-1",
    }
    svc.project_repo.get_mapping_sync_target.return_value = const.PROJECT_SYNC_TARGET_QBO_PROJECT
    svc._open_qbo_session = MagicMock(
        return_value={
            "access_token": "AT",
            "realm_id": "REALM-1",
            "env": "sandbox",
            "base_url": "https://qbo.example",
        }
    )
    svc._validate_invoice_item = MagicMock(
        return_value={"Id": "6", "Name": "labour", "Type": "Service", "Active": True}
    )
    svc._upsert_invoice = MagicMock(
        return_value=({"Id": "QBO-INV-1", "SyncToken": "0"}, "created in QBO")
    )
    monkeypatch.setattr(invoice_export_module, "safe_audit_log", MagicMock())

    result = svc.export_invoices(tenant_id="tn1", service_id="6")

    assert result[const.KEY_OK] is True
    assert result["createdInQbo"] == 1
    assert result["invoiceItem"] == {
        "id": "6",
        "name": "labour",
        "type": "Service",
        "sourceRoleId": 5,
        "sourceRoleName": "labour",
        "roleMappingId": 83,
    }
    payload = svc._upsert_invoice.call_args.kwargs["payload"]
    assert len(payload["Line"]) == 1
    assert payload["Line"][0]["Amount"] == 110.0
    assert payload["Line"][0]["SalesItemLineDetail"]["ItemRef"] == {"value": "6"}
    svc.attachment_service.attach_invoice_document.assert_called_once_with(
        tenant_id="tn1",
        access_token="AT",
        base_url="https://qbo.example",
        realm_id="REALM-1",
        invoice_id="QBO-INV-1",
        document_id=301,
        invoice_project_id=77,
    )
    assert invoice_repo.mark_mapping_synced.call_count == 2
    synced = invoice_repo.mark_mapping_synced.call_args.kwargs
    assert synced["additional_info"]["invoiceItemId"] == "6"
    assert synced["additional_info"]["invoiceItemRole"] == "labour"
    assert synced["additional_info"]["invoiceItemRoleId"] == 5
    assert synced["additional_info"]["invoiceItemRoleMappingId"] == 83
    assert synced["additional_info"]["attachment"]["status"] == "uploaded"
    assert synced["additional_info"]["attachment"]["documentId"] == 301


def test_export_marks_invoice_failed_when_labour_role_mapping_is_for_different_company(monkeypatch):
    import source.quickbooks.QuickBooksInvoiceExportService as invoice_export_module

    svc = _service()
    svc.invoice_repo = MagicMock()
    svc.invoice_repo.STATUS_SYNC_FAILED = "SYNC_FAILED"
    _install_mock_export_lock(svc.invoice_repo)
    svc.invoice_repo.get_export_candidates.return_value = [
        {"invoice": _invoice(), "mapping": {"id": 7, "sync_status": "PENDING_PUSH"}}
    ]
    svc.role_repo.get_current_mapping_by_role_id.return_value["external_account_id"] = "OTHER-REALM"
    svc._open_qbo_session = MagicMock(
        return_value={
            "access_token": "AT",
            "realm_id": "REALM-1",
            "env": "sandbox",
            "base_url": "https://qbo.example",
        }
    )
    svc._validate_invoice_item = MagicMock()
    monkeypatch.setattr(invoice_export_module, "safe_audit_log", MagicMock())

    result = svc.export_invoices(tenant_id="tn1", service_id="6")

    assert result[const.KEY_OK] is True
    assert result["syncFailed"] == 1
    assert "different QuickBooks company" in result[const.KEY_RESULTS][0][const.KEY_ERROR]
    svc.invoice_repo.mark_mapping_failed.assert_called_once()
    svc._validate_invoice_item.assert_not_called()


def test_export_keeps_invoice_synced_when_required_document_attachment_fails(monkeypatch):
    import source.quickbooks.QuickBooksInvoiceExportService as invoice_export_module

    svc = _service()
    invoice_repo = MagicMock()
    invoice_repo.STATUS_SYNC_FAILED = "SYNC_FAILED"
    invoice_repo.is_exportable_invoice.return_value = True
    invoice_repo.get_export_candidates.return_value = [
        {
            "invoice": _invoice(document_id=301),
            "mapping": {"id": 7, "sync_status": "PENDING_PUSH"},
        }
    ]
    svc.invoice_repo = invoice_repo
    _install_mock_export_lock(invoice_repo)
    svc.customer_repo.get_latest_qbo_linked_mapping_for_customer_uuid.return_value = {
        "id": 11,
        "external_customer_id": "QBO-CUSTOMER-1",
        "external_account_id": "REALM-1",
    }
    svc.project_repo.get_current_mapping_by_project_id.return_value = {
        "id": 22,
        "external_project_id": "QBO-PROJECT-1",
        "external_account_id": "REALM-1",
    }
    svc.project_repo.get_mapping_sync_target.return_value = const.PROJECT_SYNC_TARGET_QBO_PROJECT
    svc._open_qbo_session = MagicMock(
        return_value={
            "access_token": "AT",
            "realm_id": "REALM-1",
            "env": "sandbox",
            "base_url": "https://qbo.example",
        }
    )
    svc._validate_invoice_item = MagicMock(
        return_value={"Id": "6", "Name": "labour", "Type": "Service", "Active": True}
    )
    svc._upsert_invoice = MagicMock(
        return_value=({"Id": "QBO-INV-1", "SyncToken": "0"}, "created in QBO")
    )
    def fail_attachment(**_kwargs):
        assert invoice_repo.mark_mapping_synced.call_count == 1
        raise RuntimeError("tenant blob download failed")

    svc.attachment_service.attach_invoice_document.side_effect = fail_attachment
    monkeypatch.setattr(invoice_export_module, "safe_audit_log", MagicMock())

    result = svc.export_invoices(tenant_id="tn1", service_id="6")

    assert result[const.KEY_OK] is True
    assert result["createdInQbo"] == 1
    assert result["syncFailed"] == 0
    assert result["attachmentFailed"] == 1
    assert result[const.KEY_FAILURES] == []
    assert result[const.KEY_RESULTS][0][const.KEY_STATUS] == "success"
    assert result[const.KEY_RESULTS][0]["attachment"]["status"] == "failed"
    assert "tenant blob download failed" in result[const.KEY_RESULTS][0]["attachment"]["error"]
    assert invoice_repo.mark_mapping_synced.call_count == 2
    first_sync = invoice_repo.mark_mapping_synced.call_args_list[0].kwargs
    final_sync = invoice_repo.mark_mapping_synced.call_args_list[1].kwargs
    assert first_sync["external_invoice_id"] == "QBO-INV-1"
    assert first_sync["additional_info"]["attachment"]["status"] == "pending"
    assert final_sync["additional_info"]["attachment"]["status"] == "failed"
    assert "tenant blob download failed" in final_sync["additional_info"]["attachment"]["error"]
    invoice_repo.mark_mapping_failed.assert_not_called()


def test_export_reloads_mapping_inside_lock_before_invoice_upsert(monkeypatch):
    import source.quickbooks.QuickBooksInvoiceExportService as invoice_export_module

    svc = _service()
    invoice_repo = MagicMock()
    invoice_repo.is_exportable_invoice.return_value = True
    invoice_repo.get_export_candidates.return_value = [
        {
            "invoice": _invoice(),
            "mapping": {
                "id": 7,
                "external_invoice_id": None,
                "external_account_id": None,
                "sync_token": None,
                "sync_status": "PENDING_PUSH",
            },
        }
    ]
    invoice_repo.source_fingerprint.return_value = "fingerprint-1"
    svc.invoice_repo = invoice_repo
    svc.customer_repo.get_latest_qbo_linked_mapping_for_customer_uuid.return_value = {
        "id": 11,
        "external_customer_id": "QBO-CUSTOMER-1",
        "external_account_id": "REALM-1",
    }
    svc.project_repo.get_current_mapping_by_project_id.return_value = {
        "id": 22,
        "external_project_id": "QBO-PROJECT-1",
        "external_account_id": "REALM-1",
    }
    svc.project_repo.get_mapping_sync_target.return_value = const.PROJECT_SYNC_TARGET_QBO_PROJECT
    svc._open_qbo_session = MagicMock(
        return_value={
            "access_token": "AT",
            "realm_id": "REALM-1",
            "env": "sandbox",
            "base_url": "https://qbo.example",
        }
    )
    svc._validate_invoice_item = MagicMock(
        return_value={"Id": "6", "Name": "labour", "Type": "Service", "Active": True}
    )
    monkeypatch.setattr(invoice_export_module, "safe_audit_log", MagicMock())

    events = []

    def claimed_mapping(**_kwargs):
        events.append("mapping_claimed")
        return {
            "claimed": True,
            "reason": "claimed",
            "mapping": {
                "id": 7,
                "external_invoice_id": "QBO-INV-EXISTING",
                "external_account_id": "REALM-1",
                "sync_token": "4",
                "sync_status": "SYNCED",
            },
        }

    invoice_repo.claim_export_mapping.side_effect = claimed_mapping

    def tracked_upsert(**kwargs):
        events.append("upsert")
        assert kwargs["mapping"]["external_invoice_id"] == "QBO-INV-EXISTING"
        return {"Id": "QBO-INV-EXISTING", "SyncToken": "5"}, "updated in QBO"

    svc._upsert_invoice = MagicMock(side_effect=tracked_upsert)

    result = svc.export_invoices(tenant_id="tn1", service_id="6")

    assert result[const.KEY_OK] is True
    assert result["createdInQbo"] == 0
    assert result["updatedInQbo"] == 1
    assert result["syncFailed"] == 0
    assert events[:2] == ["mapping_claimed", "upsert"]
    assert invoice_repo.mark_mapping_synced.call_count == 2


def test_export_claim_finishes_before_qbo_and_attachment_network_calls(monkeypatch):
    """The short DB claim must finish before any invoice or attachment QBO I/O."""
    import source.quickbooks.QuickBooksInvoiceExportService as invoice_export_module

    svc = _service()
    invoice_repo = MagicMock()
    invoice_repo.STATUS_SYNC_FAILED = "SYNC_FAILED"
    invoice_repo.is_exportable_invoice.return_value = True
    invoice_repo.get_export_candidates.return_value = [
        {
            "invoice": _invoice(document_id=301),
            "mapping": {"id": 7, "sync_status": "PENDING_PUSH"},
        }
    ]
    invoice_repo.source_fingerprint.return_value = "fingerprint-1"
    svc.invoice_repo = invoice_repo
    svc.customer_repo.get_latest_qbo_linked_mapping_for_customer_uuid.return_value = {
        "id": 11,
        "external_customer_id": "QBO-CUSTOMER-1",
        "external_account_id": "REALM-1",
    }
    svc.project_repo.get_current_mapping_by_project_id.return_value = {
        "id": 22,
        "external_project_id": "QBO-PROJECT-1",
        "external_account_id": "REALM-1",
    }
    svc.project_repo.get_mapping_sync_target.return_value = const.PROJECT_SYNC_TARGET_QBO_PROJECT
    svc._open_qbo_session = MagicMock(
        return_value={
            "access_token": "AT",
            "realm_id": "REALM-1",
            "env": "sandbox",
            "base_url": "https://qbo.example",
        }
    )
    svc._validate_invoice_item = MagicMock(
        return_value={"Id": "6", "Name": "labour", "Type": "Service", "Active": True}
    )
    monkeypatch.setattr(invoice_export_module, "safe_audit_log", MagicMock())

    events = []

    def claimed_mapping(**_kwargs):
        events.append("mapping_claimed")
        return {
            "claimed": True,
            "reason": "claimed",
            "mapping": {"id": 7, "sync_status": "PENDING_PUSH"},
        }

    invoice_repo.claim_export_mapping.side_effect = claimed_mapping
    invoice_repo.mark_mapping_synced.side_effect = lambda **_kwargs: events.append("mark_synced")

    def tracked_upsert(**_kwargs):
        events.append("upsert")
        return {"Id": "QBO-INV-1", "SyncToken": "0"}, "created in QBO"

    svc._upsert_invoice = MagicMock(side_effect=tracked_upsert)

    def tracked_attachment(**_kwargs):
        events.append("attachment")
        return {"status": "uploaded", "documentId": 301, "attachableId": "ATT-1"}

    svc.attachment_service.attach_invoice_document.side_effect = tracked_attachment

    result = svc.export_invoices(tenant_id="tn1", service_id="6")

    assert result[const.KEY_OK] is True
    assert result["createdInQbo"] == 1
    assert result["syncFailed"] == 0
    assert result["attachmentFailed"] == 0
    assert result[const.KEY_RESULTS][0]["attachment"]["status"] == "uploaded"
    # The DB claim has already committed before the QBO invoice request starts.
    assert events == [
        "mapping_claimed",
        "upsert",
        "mark_synced",
        "attachment",
        "mark_synced",
    ]
